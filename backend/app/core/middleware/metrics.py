"""Prometheus 指标中间件 — 自动采集 HTTP 请求指标

每个请求自动记录:
- 请求计数 (method, path, status)
- 请求延迟 (method, path)
"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import logger
from app.core.metrics import record_http_request, is_metrics_enabled


class MetricsMiddleware(BaseHTTPMiddleware):
    """Prometheus 指标采集中间件"""

    async def dispatch(self, request, call_next):
        if not is_metrics_enabled():
            return await call_next(request)

        # 跳过 /metrics 自身
        if request.url.path == "/metrics":
            return await call_next(request)

        start_time = time.time()
        method = request.method
        path = request.url.path

        try:
            response = await call_next(request)
            status = response.status_code
        except Exception as e:
            # 请求异常, 记录 500
            duration = time.time() - start_time
            record_http_request(method, path, 500, duration)
            raise

        # 记录指标
        duration = time.time() - start_time
        record_http_request(method, path, status, duration)

        return response
