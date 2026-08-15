"""请求幂等性中间件 — 基于 Idempotency-Key 头

设计:
1. 客户端在 POST/PUT/PATCH 请求中携带 Idempotency-Key 头
2. 中间件检查 Redis 是否已存在该 key:
   - 存在: 直接返回缓存的响应 (不执行请求)
   - 不存在: 执行请求, 缓存响应 (TTL=1h)
3. 无 Idempotency-Key 头的请求直接放行, 不影响性能

适用场景:
  - 文档上传 (防止重复上传)
  - 知识库创建 (防止重复创建)
  - 支付/订单类操作 (防止重复提交)

Redis key: idem:{key}
Value: JSON 序列化的响应 (status_code, headers, body)
TTL: 3600 秒 (1 小时)

降级:
  - Redis 不可用时直接放行 (不保证幂等性)
  - 响应体过大 (>100KB) 不缓存 (防止内存溢出)
"""

from __future__ import annotations

import json
import time
import asyncio
from typing import Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.background import BackgroundTask

from app.core.logging import logger
from app.core.redis import get_redis_client, is_redis_available


# 配置
_IDEM_PREFIX = "idem:"
_IDEM_TTL = 3600  # 1 小时
_MAX_BODY_CACHE = 100 * 1024  # 100KB: 超过不缓存
_METHODS = {"POST", "PUT", "PATCH"}


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """请求幂等性中间件

    通过 Idempotency-Key 头实现 POST/PUT/PATCH 请求的幂等性。
    """

    async def dispatch(self, request: Request, call_next):
        # 仅对写方法生效
        if request.method not in _METHODS:
            return await call_next(request)

        # 检查是否有 Idempotency-Key 头
        idem_key = request.headers.get("Idempotency-Key")
        if not idem_key:
            # 无 key, 直接放行
            return await call_next(request)

        # Redis 不可用时直接放行
        if not is_redis_available():
            return await call_next(request)

        redis = get_redis_client()
        if redis is None:
            return await call_next(request)

        redis_key = f"{_IDEM_PREFIX}{idem_key}"

        # 1. 检查是否已处理
        try:
            cached = await redis.get(redis_key)
            if cached is not None:
                # 已处理过, 返回缓存的响应
                try:
                    cached_data = json.loads(cached)
                    logger.info(
                        "IDEMPOTENCY_HIT key=%s path=%s status=%d",
                        idem_key[:16], request.url.path, cached_data.get("status_code", 0)
                    )
                    return JSONResponse(
                        status_code=cached_data.get("status_code", 200),
                        content=cached_data.get("body"),
                        headers=cached_data.get("headers", {}),
                    )
                except (json.JSONDecodeError, TypeError):
                    # 缓存损坏, 继续执行请求
                    pass
        except Exception as e:
            logger.debug("Idempotency check failed: %s", str(e))

        # 2. 执行请求
        response = await call_next(request)

        # 3. 缓存响应 (仅缓存成功的响应, 4xx/5xx 不缓存)
        if 200 <= response.status_code < 300:
            try:
                # 读取响应体
                response_body = b""
                async for chunk in response.body_iterator:
                    response_body += chunk

                # 检查响应体大小
                if len(response_body) > _MAX_BODY_CACHE:
                    logger.debug(
                        "IDEMPOTENCY_SKIP key=%s body too large (%d bytes)",
                        idem_key[:16], len(response_body)
                    )
                    # 重新构造响应 (不缓存)
                    return Response(
                        content=response_body,
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        media_type=response.media_type,
                    )

                # 解析响应体
                try:
                    body_json = json.loads(response_body)
                except (json.JSONDecodeError, TypeError):
                    body_json = None

                # 构造缓存数据
                cache_data = {
                    "status_code": response.status_code,
                    "headers": {
                        k: v for k, v in response.headers.items()
                        if k.lower() not in ("content-length", "transfer-encoding")
                    },
                    "body": body_json if body_json is not None else response_body.decode("utf-8", errors="replace"),
                    "method": request.method,
                    "path": request.url.path,
                    "cached_at": time.time(),
                }

                # 写入 Redis
                await redis.set(
                    redis_key,
                    json.dumps(cache_data, ensure_ascii=False),
                    ex=_IDEM_TTL,
                )

                logger.info(
                    "IDEMPOTENCY_STORE key=%s path=%s status=%d",
                    idem_key[:16], request.url.path, response.status_code
                )

                # 重新构造响应
                return Response(
                    content=response_body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )

            except Exception as e:
                logger.warning("Idempotency cache failed: %s", str(e))
                # 缓存失败不影响请求, 返回原始响应

        return response


async def invalidate_idempotency_key(idem_key: str) -> bool:
    """手动清除幂等性缓存

    Args:
        idem_key: 幂等性 key

    Returns:
        是否成功删除
    """
    if not is_redis_available():
        return False
    redis = get_redis_client()
    if redis is None:
        return False
    try:
        redis_key = f"{_IDEM_PREFIX}{idem_key}"
        deleted = await redis.delete(redis_key)
        return deleted > 0
    except Exception:
        return False


async def get_idempotency_status(idem_key: str) -> Optional[dict]:
    """查询幂等性缓存的执行状态

    Args:
        idem_key: 幂等性 key

    Returns:
        缓存的响应数据或 None
    """
    if not is_redis_available():
        return None
    redis = get_redis_client()
    if redis is None:
        return None
    try:
        redis_key = f"{_IDEM_PREFIX}{idem_key}"
        cached = await redis.get(redis_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass
    return None
