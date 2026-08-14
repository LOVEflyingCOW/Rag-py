"""Redis 缓存层 — Cache-Aside 模式

设计:
1. 应用层管理缓存 (Cache-Aside): 先查缓存, miss 时查 DB 并回填缓存
2. 支持 TTL 过期、主动失效、穿透保护
3. Redis 不可用时自动降级到直查 DB (不抛异常)

热点数据:
  - 知识库元数据 (kb:{id}): 读多写少, TTL=60s
  - 用户权限 (user_perm:{user_id}): 读多写少, TTL=30s
  - Embedding 向量缓存 (embed:{hash}): 计算昂贵, TTL=3600s
"""

from __future__ import annotations

import json
import hashlib
import time
from typing import Any, Callable, Dict, Optional, TypeVar

from app.core.logging import logger
from app.core.redis import get_redis_client, is_redis_available


T = TypeVar("T")

# Redis key 前缀
_KB_PREFIX = "kb:"
_USER_PERM_PREFIX = "user_perm:"
_EMBED_PREFIX = "embed:"


async def _get_cache(key: str) -> Optional[str]:
    """从 Redis 获取缓存值 (字符串)"""
    if not is_redis_available():
        return None
    redis = get_redis_client()
    if redis is None:
        return None
    try:
        return await redis.get(key)
    except Exception:
        return None


async def _set_cache(key: str, value: str, ttl: int) -> None:
    """写入 Redis 缓存"""
    if not is_redis_available():
        return
    redis = get_redis_client()
    if redis is None:
        return
    try:
        await redis.set(key, value, ex=ttl)
    except Exception as e:
        logger.debug("Cache set failed for %s: %s", key, str(e))


async def _delete_cache(key: str) -> None:
    """删除 Redis 缓存"""
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
#  通用缓存装饰器
# ============================================================

async def cached_get(
    cache_key: str,
    fetch_fn: Callable[[], Any],
    ttl: int = 60,
) -> Any:
    """Cache-Aside 缓存获取

    Args:
        cache_key: Redis 缓存 key
        fetch_fn: 缓存 miss 时的数据获取函数 (返回可 JSON 序列化的对象)
        ttl: 缓存过期时间 (秒)

    Returns:
        数据 (dict/list/str/int) 或 None
    """
    # 1. 查缓存
    cached = await _get_cache(cache_key)
    if cached is not None:
        try:
            return json.loads(cached)
        except (json.JSONDecodeError, TypeError):
            pass  # 缓存数据损坏, 继续查 DB

    # 2. 缓存 miss → 查数据源
    try:
        data = await fetch_fn()
    except Exception as e:
        logger.warning("Cache fetch failed for %s: %s", cache_key, str(e))
        raise

    # 3. 回填缓存
    if data is not None:
        try:
            serialized = json.dumps(data, ensure_ascii=False, default=str)
            await _set_cache(cache_key, serialized, ttl)
        except Exception as e:
            logger.debug("Cache backfill failed for %s: %s", cache_key, str(e))

    return data


async def invalidate_cache(cache_key: str) -> None:
    """主动失效缓存 (写操作后调用)"""
    await _delete_cache(cache_key)


# ============================================================
#  知识库缓存 (热点)
# ============================================================

async def get_kb_cached(
    kb_id: int,
    fetch_fn: Callable[[], Any],
    ttl: int = 60,
) -> Any:
    """获取知识库 (带缓存)"""
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
    """获取用户权限信息 (带缓存)"""
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
    """获取文本的 Embedding 向量 (带缓存)

    通过 text 的 SHA-256 哈希作为缓存 key.
    """
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    key = f"{_EMBED_PREFIX}{text_hash}"

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
        raise

    if vector is not None:
        try:
            serialized = json.dumps(vector)
            await _set_cache(key, serialized, ttl)
        except Exception:
            pass

    return vector


async def clear_embed_cache() -> None:
    """清空所有 Embedding 缓存 (模型切换后调用)"""
    if not is_redis_available():
        return
    redis = get_redis_client()
    if redis is None:
        return
    try:
        keys = await redis.keys(f"{_EMBED_PREFIX}*")
        if keys:
            await redis.delete(*keys)
            logger.info("Cleared %d embedding cache entries", len(keys))
    except Exception as e:
        logger.debug("Clear embed cache failed: %s", str(e))
