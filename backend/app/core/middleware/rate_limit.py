"""限流中间件 — 滑动窗口算法 (Redis ZSET + Lua 原子操作)

策略:
- 匿名用户: 30 req/min
- 已认证用户: 120 req/min
- 管理员: 600 req/min

Phase 3: Redis 分布式限流, 多实例共享计数.
Redis 不可用时自动降级到内存模式.
"""

from __future__ import annotations

import time
import uuid
import threading
import hashlib
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import logger
from app.core.redis import get_redis_client, is_redis_available


# 限流配置
RATE_LIMITS = {
    "anonymous": {"limit": 30, "window": 60},
    "authenticated": {"limit": 120, "window": 60},
    "admin": {"limit": 600, "window": 60},
}

# 不需要限流的路径
EXEMPT_PATHS = {"/", "/health", "/docs", "/openapi.json", "/redoc"}

# Redis key 前缀
_REDIS_PREFIX = "ratelimit:"


# ============================================================
#  Lua 脚本 — 原子滑动窗口限流
# ============================================================
#  KEYS[1] = 限流 key (ZSET)
#  ARGV[1] = now (当前时间戳, float)
#  ARGV[2] = window (窗口大小, 秒)
#  ARGV[3] = limit (最大请求数)
#  ARGV[4] = member (唯一成员标识)
#
#  返回: {allowed(1/0), remaining, reset_at}
# ============================================================
_LUA_SLIDING_WINDOW = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local window_start = now - window

-- 清理过期记录
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

-- 当前窗口内的请求数
local count = redis.call('ZCARD', key)

if count < limit then
    -- 允许: 添加当前请求
    redis.call('ZADD', key, now, member)
    -- 设置 key 过期时间 (窗口 + 1 秒缓冲)
    redis.call('EXPIRE', key, window + 1)
    return {1, limit - count - 1, now + window}
else
    -- 拒绝: 返回最早请求的过期时间
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local reset_at = now + window
    if oldest[2] then
        reset_at = tonumber(oldest[2]) + window
    end
    return {0, 0, reset_at}
end
"""


# ============================================================
#  内存降级限流器 (Redis 不可用时使用)
# ============================================================

class _MemoryRateLimiter:
    """内存滑动窗口限流器 (降级用)"""

    def __init__(self):
        self._windows: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: int = 60) -> Tuple[bool, int, float]:
        now = time.time()
        window_start = now - window

        with self._lock:
            entries = self._windows[key]
            if entries and entries[0] <= window_start:
                self._windows[key] = [t for t in entries if t > window_start]

            current_count = len(self._windows[key])
            if current_count < limit:
                self._windows[key].append(now)
                remaining = limit - current_count - 1
                return True, remaining, now + window
            else:
                return False, 0, self._windows[key][0] + window


# 全局内存限流器 (降级用)
_memory_limiter = _MemoryRateLimiter()

# 预加载的 Lua 脚本 SHA (启动时 SCRIPT LOAD)
_lua_sha: Optional[str] = None


async def _load_lua_script() -> Optional[str]:
    """预加载 Lua 脚本到 Redis, 返回 SHA 值"""
    global _lua_sha
    redis = get_redis_client()
    if redis is None:
        return None
    try:
        _lua_sha = await redis.script_load(_LUA_SLIDING_WINDOW)
        logger.info("Rate limit Lua script loaded: %s", _lua_sha[:12])
        return _lua_sha
    except Exception as e:
        logger.warning("Failed to load Lua script: %s", str(e))
        return None


async def _redis_check(key: str, limit: int, window: int) -> Tuple[bool, int, float]:
    """通过 Redis ZSET + Lua 脚本检查限流 (原子操作)"""
    global _lua_sha
    redis = get_redis_client()
    if redis is None:
        # 降级到内存
        return _memory_limiter.check(key, limit, window)

    redis_key = _REDIS_PREFIX + key
    now = time.time()
    member = "%s:%s" % (str(now), uuid.uuid4().hex[:8])

    try:
        # 使用 EVALSHA (如果已加载) 或 EVAL (回退)
        if _lua_sha:
            result = await redis.evalsha(_lua_sha, 1, redis_key, str(now), str(window), str(limit), member)
        else:
            result = await redis.eval(_LUA_SLIDING_WINDOW, 1, redis_key, str(now), str(window), str(limit), member)

        allowed = bool(result[0])
        remaining = int(result[1])
        reset_at = float(result[2])
        return allowed, remaining, reset_at

    except Exception as e:
        # Redis 异常 → 降级到内存
        logger.warning("Redis rate limit failed, falling back to memory: %s", str(e))
        return _memory_limiter.check(key, limit, window)


async def check_rate_limit(key: str, limit: int, window: int = 60) -> Tuple[bool, int, float]:
    """检查限流 — 自动选择 Redis 或内存模式

    Returns: (allowed: bool, remaining: int, reset_at: float)
    """
    if is_redis_available():
        return await _redis_check(key, limit, window)
    else:
        return _memory_limiter.check(key, limit, window)


def _classify_user(request: Request) -> Tuple[str, str]:
    """分类请求来源, 返回 (策略名, 限流 key)"""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if len(token) >= 20:
            fp = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
            return "authenticated", f"t:{fp}"

    client_ip = request.client.host if request.client else "unknown"
    return "anonymous", f"ip:{client_ip}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """API 限流中间件 (Redis 分布式 + 内存降级)"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 跳过不需要限流的路径
        if path in EXEMPT_PATHS or path.startswith("/docs") or path.startswith("/api/v1/embeddings"):
            return await call_next(request)

        # 分类用户 + 选择策略
        policy_name, limit_key = _classify_user(request)
        policy = RATE_LIMITS[policy_name]

        # 检查限流
        allowed, remaining, reset_at = await check_rate_limit(
            limit_key, policy["limit"], policy["window"]
        )

        if not allowed:
            logger.warning(
                "Rate limit exceeded for %s (%s): %d/%d in %ds",
                limit_key, policy_name, policy["limit"], policy["limit"], policy["window"],
            )
            retry_after = max(1, int(reset_at - time.time()))
            return JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "error": {"code": "RATE_LIMIT_EXCEEDED", "message": "请求过于频繁，请稍后重试"},
                    "detail": f"Rate limit: {policy['limit']} requests per {policy['window']}s",
                },
                headers={
                    "X-RateLimit-Limit": str(policy["limit"]),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(reset_at)),
                    "Retry-After": str(retry_after),
                },
            )

        response = await call_next(request)

        # 添加限流信息到响应头
        response.headers["X-RateLimit-Limit"] = str(policy["limit"])
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(reset_at))

        return response
