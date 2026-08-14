"""Redis 连接管理 — 异步客户端 + 优雅降级

设计:
1. 全局单例 async Redis 客户端, 应用启动时连接, 关闭时释放
2. Redis 不可用时自动降级到内存模式 (开发环境友好)
3. 提供 get_redis() 依赖注入 + health_check()

使用:
    from app.core.redis import get_redis_client, init_redis, close_redis

    # 启动时
    await init_redis()
    # 关闭时
    await close_redis()
    # 业务中
    redis = get_redis_client()
    await redis.set("key", "value")
"""

from __future__ import annotations

from typing import Optional

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.core.config import settings
from app.core.logging import logger


# 全局 Redis 客户端单例
_redis_client: Optional[Redis] = None

# Redis 是否可用 (降级标志)
_redis_available: bool = False


def get_redis_client() -> Optional[Redis]:
    """获取全局 Redis 客户端单例

    Returns:
        Redis 客户端实例, 如果未初始化或不可用则返回 None
    """
    return _redis_client


def is_redis_available() -> bool:
    """Redis 是否可用"""
    return _redis_available


async def init_redis() -> bool:
    """初始化 Redis 连接

    在应用 startup 事件中调用. 连接失败时自动降级到内存模式.

    Returns:
        True 表示连接成功, False 表示降级到内存模式
    """
    global _redis_client, _redis_available

    try:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
            retry_on_timeout=True,
            socket_connect_timeout=3,
            socket_timeout=5,
            health_check_interval=30,
        )

        # 测试连接
        await _redis_client.ping()
        _redis_available = True
        logger.info("Redis connected: %s", settings.REDIS_URL)
        return True

    except Exception as e:
        _redis_available = False
        _redis_client = None
        logger.warning(
            "Redis unavailable, falling back to in-memory mode: %s", str(e)
        )
        return False


async def close_redis() -> None:
    """关闭 Redis 连接池

    在应用 shutdown 事件中调用.
    """
    global _redis_client, _redis_available

    if _redis_client is not None:
        try:
            await _redis_client.aclose()
            logger.info("Redis connection closed")
        except Exception as e:
            logger.warning("Error closing Redis: %s", str(e))
        finally:
            _redis_client = None
            _redis_available = False


async def health_check() -> dict:
    """Redis 健康检查 (供 /health 端点调用)"""
    if not _redis_available or _redis_client is None:
        return {"status": "degraded", "mode": "in-memory"}

    try:
        pong = await _redis_client.ping()
        info = await _redis_client.info("server")
        return {
            "status": "ok" if pong else "error",
            "mode": "redis",
            "version": info.get("redis_version", "unknown"),
            "connected_clients": info.get("connected_clients", "unknown"),
        }
    except Exception as e:
        return {"status": "error", "mode": "redis", "error": str(e)}
