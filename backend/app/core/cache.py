"""多级缓存层 — L1 (本地 LRU) + L2 (Redis) Cache-Aside 模式

设计:
1. 两级缓存: L1 本地 LRU (毫秒级, 进程内) + L2 Redis (毫秒级, 跨实例共享)
2. 应用层管理缓存 (Cache-Aside): 先查 L1 → miss 查 L2 → miss 查 DB → 回填 L1+L2
3. 支持 TTL 过期、主动失效 (双级同步失效)、穿透保护
4. Redis 不可用时自动降级: L1 仍然可用, L2 直查 DB
5. 缓存击穿防护: Redis SET NX 互斥锁, 防止并发回源

热点数据:
  - 知识库元数据 (kb:{id}): 读多写少, TTL=60s
  - 用户权限 (user_perm:{user_id}): 读多写少, TTL=30s
  - Embedding 向量缓存 (embed:{hash}): 计算昂贵, TTL=3600s

L1 缓存说明:
  - 使用 OrderedDict 实现 LRU 淘汰
  - 最大容量 1000 条 (可通过 L1_MAX_SIZE 配置)
  - 本地缓存不跨实例, 适用于单机热点
  - 线程安全: asyncio.Lock 保护并发读写
"""

from __future__ import annotations

import json
import hashlib
import time
import asyncio
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, TypeVar

from app.core.logging import logger
from app.core.redis import get_redis_client, is_redis_available


T = TypeVar("T")

# Redis key 前缀
_KB_PREFIX = "kb:"
_USER_PERM_PREFIX = "user_perm:"
_EMBED_PREFIX = "embed:"

# L1 本地缓存配置
L1_MAX_SIZE = 1000  # 本地缓存最大条目数
_L1_TTL_DEFAULT = 60  # 本地缓存默认 TTL (秒), 比 L2 短以保持一致性


# ============================================================
#  L1 本地 LRU 缓存
# ============================================================

class _L1Cache:
    """线程安全的 L1 本地 LRU 缓存

    使用 OrderedDict 实现 LRU 淘汰策略。
    每条缓存记录包含 value 和 expire_at, 过期自动清理。
    """

    def __init__(self, max_size: int = L1_MAX_SIZE):
        self._store: OrderedDict[str, tuple] = OrderedDict()
        # tuple = (value_str, expire_at_timestamp)
        self._lock = asyncio.Lock()
        self._max_size = max_size

    async def get(self, key: str) -> Optional[str]:
        """获取缓存值, 未命中或过期返回 None"""
        async with self._lock:
            if key not in self._store:
                return None
            value_str, expire_at = self._store[key]
            if time.time() > expire_at:
                # 过期, 删除
                del self._store[key]
                return None
            # LRU: 移到末尾 (最近使用)
            self._store.move_to_end(key)
            return value_str

    async def set(self, key: str, value_str: str, ttl: int) -> None:
        """写入缓存"""
        async with self._lock:
            expire_at = time.time() + ttl
            self._store[key] = (value_str, expire_at)
            self._store.move_to_end(key)
            # LRU 淘汰: 超过容量时删除最旧的
            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    async def delete(self, key: str) -> None:
        """删除缓存"""
        async with self._lock:
            self._store.pop(key, None)

    async def delete_pattern(self, pattern: str) -> int:
        """删除匹配前缀的所有缓存"""
        async with self._lock:
            keys_to_delete = [k for k in self._store if k.startswith(pattern)]
            for k in keys_to_delete:
                del self._store[k]
            return len(keys_to_delete)

    async def clear(self) -> None:
        """清空所有缓存"""
        async with self._lock:
            self._store.clear()

    def size(self) -> int:
        """当前缓存条目数"""
        return len(self._store)

    async def cleanup_expired(self) -> int:
        """清理所有过期条目"""
        now = time.time()
        async with self._lock:
            expired_keys = [
                k for k, (_, expire_at) in self._store.items()
                if now > expire_at
            ]
            for k in expired_keys:
                del self._store[k]
            return len(expired_keys)


# 全局 L1 缓存实例
_l1_cache = _L1Cache()


# ============================================================
#  L2 Redis 缓存
# ============================================================

async def _get_l2(key: str) -> Optional[str]:
    """从 Redis (L2) 获取缓存值"""
    if not is_redis_available():
        return None
    redis = get_redis_client()
    if redis is None:
        return None
    try:
        return await redis.get(key)
    except Exception:
        return None


async def _set_l2(key: str, value: str, ttl: int) -> None:
    """写入 Redis (L2) 缓存"""
    if not is_redis_available():
        return
    redis = get_redis_client()
    if redis is None:
        return
    try:
        await redis.set(key, value, ex=ttl)
    except Exception as e:
        logger.debug("L2 cache set failed for %s: %s", key, str(e))


async def _delete_l2(key: str) -> None:
    """删除 Redis (L2) 缓存"""
    if not is_redis_available():
        return
    redis = get_redis_client()
    if redis is None:
        return
    try:
        await redis.delete(key)
    except Exception:
        pass


# ============================================================
#  L1 + L2 多级缓存: 读取 / 写入 / 失效
# ============================================================

async def _get_cache(key: str) -> Optional[str]:
    """从多级缓存获取: L1 → L2

    L1 命中: 直接返回 (最快)
    L1 miss, L2 命中: 回填 L1 后返回
    L1+L2 miss: 返回 None (由调用方查 DB)
    """
    # 1. 查 L1 本地缓存
    l1_value = await _l1_cache.get(key)
    if l1_value is not None:
        return l1_value

    # 2. L1 miss → 查 L2 Redis
    l2_value = await _get_l2(key)
    if l2_value is not None:
        # 回填 L1 (使用较短的 TTL 保持一致性)
        await _l1_cache.set(key, l2_value, ttl=min(_L1_TTL_DEFAULT, 60))
        return l2_value

    return None


async def _set_cache(key: str, value: str, ttl: int) -> None:
    """写入多级缓存: L1 + L2"""
    # 写入 L1
    await _l1_cache.set(key, value, ttl=min(ttl, _L1_TTL_DEFAULT))
    # 写入 L2
    await _set_l2(key, value, ttl)


async def _delete_cache(key: str) -> None:
    """删除多级缓存: L1 + L2 (双级同步失效)"""
    await _l1_cache.delete(key)
    await _delete_l2(key)


# ============================================================
#  缓存击穿防护 (互斥锁)
# ============================================================

async def _get_or_acquire_lock(key: str, lock_ttl: int = 10) -> bool:
    """尝试获取互斥锁 (Redis SET NX), 防止缓存击穿

    Returns:
        True: 获取锁成功 (可以回源查 DB)
        False: 其他请求正在回源 (等待重试)
    """
    if not is_redis_available():
        return True  # Redis 不可用时直接放行

    redis = get_redis_client()
    if redis is None:
        return True

    lock_key = f"lock:{key}"
    try:
        acquired = await redis.set(lock_key, "1", nx=True, ex=lock_ttl)
        return acquired is not None
    except Exception:
        return True  # 异常时放行


async def _release_lock(key: str) -> None:
    """释放互斥锁"""
    if not is_redis_available():
        return
    redis = get_redis_client()
    if redis is None:
        return
    lock_key = f"lock:{key}"
    try:
        await redis.delete(lock_key)
    except Exception:
        pass


# ============================================================
#  通用缓存装饰器 (多级缓存 + 击穿防护)
# ============================================================

async def cached_get(
    cache_key: str,
    fetch_fn: Callable[[], Any],
    ttl: int = 60,
    use_lock: bool = True,
) -> Any:
    """多级 Cache-Aside 缓存获取

    流程: L1 → L2 → (互斥锁) → DB → 回填 L1+L2

    Args:
        cache_key: 缓存 key
        fetch_fn: 缓存 miss 时的数据获取函数 (返回可 JSON 序列化的对象)
        ttl: L2 Redis 缓存过期时间 (秒), L1 使用 min(ttl, 60)
        use_lock: 是否使用互斥锁防击穿 (默认 True)

    Returns:
        数据 (dict/list/str/int) 或 None
    """
    # 1. 查多级缓存 (L1 → L2)
    cached = await _get_cache(cache_key)
    if cached is not None:
        try:
            return json.loads(cached)
        except (json.JSONDecodeError, TypeError):
            pass  # 缓存数据损坏, 继续查 DB

    # 2. 缓存 miss → 互斥锁防击穿
    if use_lock and is_redis_available():
        acquired = await _get_or_acquire_lock(cache_key)
        if not acquired:
            # 其他请求正在回源, 短暂等待后重试缓存
            await asyncio.sleep(0.1)
            cached = await _get_cache(cache_key)
            if cached is not None:
                try:
                    return json.loads(cached)
                except (json.JSONDecodeError, TypeError):
                    pass

    # 3. 查数据源
    try:
        data = await fetch_fn()
    except Exception as e:
        logger.warning("Cache fetch failed for %s: %s", cache_key, str(e))
        if use_lock and is_redis_available():
            await _release_lock(cache_key)
        raise

    # 4. 回填多级缓存 (L1 + L2)
    if data is not None:
        try:
            serialized = json.dumps(data, ensure_ascii=False, default=str)
            await _set_cache(cache_key, serialized, ttl)
        except Exception as e:
            logger.debug("Cache backfill failed for %s: %s", cache_key, str(e))

    # 5. 释放互斥锁
    if use_lock and is_redis_available():
        await _release_lock(cache_key)

    return data


async def invalidate_cache(cache_key: str) -> None:
    """主动失效多级缓存 (写操作后调用)

    同时删除 L1 本地和 L2 Redis 中的缓存。
    """
    await _delete_cache(cache_key)


# ============================================================
#  知识库缓存 (热点)
# ============================================================

async def get_kb_cached(
    kb_id: int,
    fetch_fn: Callable[[], Any],
    ttl: int = 60,
) -> Any:
    """获取知识库 (带多级缓存)"""
    key = f"{_KB_PREFIX}{kb_id}"
    return await cached_get(key, fetch_fn, ttl=ttl)


async def invalidate_kb_cache(kb_id: int) -> None:
    """失效知识库缓存 (更新/删除后调用)"""
    key = f"{_KB_PREFIX}{kb_id}"
    await invalidate_cache(key)


# ============================================================
#  用户权限缓存
# ============================================================

async def get_user_perm_cached(
    user_id: int,
    fetch_fn: Callable[[], Any],
    ttl: int = 30,
) -> Any:
    """获取用户权限信息 (带多级缓存)"""
    key = f"{_USER_PERM_PREFIX}{user_id}"
    return await cached_get(key, fetch_fn, ttl=ttl)


async def invalidate_user_perm_cache(user_id: int) -> None:
    """失效用户权限缓存 (角色变更后调用)"""
    await invalidate_cache(f"{_USER_PERM_PREFIX}{user_id}")


# ============================================================
#  Embedding 向量缓存 (避免重复 API 调用)
# ============================================================

async def get_embed_cached(
    text: str,
    fetch_fn: Callable[[], list],
    ttl: int = 3600,
) -> Optional[list]:
    """获取文本的 Embedding 向量 (带多级缓存)

    通过 text 的 SHA-256 哈希作为缓存 key.
    Embedding 计算昂贵, L1 缓存 TTL 仍使用 60s (比 L2 短).
    """
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    key = f"{_EMBED_PREFIX}{text_hash}"

    # 查多级缓存
    cached = await _get_cache(key)
    if cached is not None:
        try:
            return json.loads(cached)
        except (json.JSONDecodeError, TypeError):
            pass

    # 互斥锁防击穿
    acquired = await _get_or_acquire_lock(key)
    if not acquired:
        await asyncio.sleep(0.1)
        cached = await _get_cache(key)
        if cached is not None:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass

    try:
        vector = await fetch_fn()
    except Exception as e:
        logger.warning("Embed fetch failed for hash %s: %s", text_hash, str(e))
        await _release_lock(key)
        raise

    if vector is not None:
        try:
            serialized = json.dumps(vector)
            await _set_cache(key, serialized, ttl)
        except Exception:
            pass

    await _release_lock(key)
    return vector


async def clear_embed_cache() -> None:
    """清空所有 Embedding 缓存 (模型切换后调用)

    清空 L1 本地 + L2 Redis 中的所有 embed: 前缀缓存。
    """
    # 清空 L1
    count_l1 = await _l1_cache.delete_pattern(_EMBED_PREFIX)

    # 清空 L2
    count_l2 = 0
    if is_redis_available():
        redis = get_redis_client()
        if redis is not None:
            try:
                keys = await redis.keys(f"{_EMBED_PREFIX}*")
                if keys:
                    await redis.delete(*keys)
                    count_l2 = len(keys)
            except Exception as e:
                logger.debug("Clear L2 embed cache failed: %s", str(e))

    logger.info("Cleared embed cache: L1=%d, L2=%d", count_l1, count_l2)


# ============================================================
#  L1 缓存管理接口
# ============================================================

def get_l1_stats() -> dict:
    """获取 L1 本地缓存统计信息"""
    return {
        "size": _l1_cache.size(),
        "max_size": L1_MAX_SIZE,
        "type": "LRU (OrderedDict)",
    }


async def cleanup_l1_expired() -> int:
    """清理 L1 中所有过期条目 (可由定时任务调用)"""
    return await _l1_cache.cleanup_expired()


async def clear_l1_cache() -> None:
    """清空 L1 本地缓存"""
    await _l1_cache.clear()
    logger.info("L1 cache cleared")
