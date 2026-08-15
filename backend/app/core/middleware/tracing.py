"""Tracing 中间件 — 注入 trace_id 到请求上下文

每个请求自动生成或读取 X-Trace-ID, 注入到:
1. structlog contextvars (日志自动包含)
2. 响应头 X-Trace-ID (客户端可追踪)
3. OpenTelemetry span (如果启用)
"""

from __future__ import annotations

import uuid
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import bind_request_context, clear_request_context, _HAS_STRUCTLOG


class TracingMiddleware(BaseHTTPMiddleware):
    """请求追踪中间件

    流程:
    1. 读取或生成 trace_id
    2. 绑定到 structlog contextvars
    3. 执行请求
    4. 响应头写入 X-Trace-ID
    5. 清除上下文
    """

    async def dispatch(self, request: Request, call_next):
        # 跳过健康检查和指标端点
        if request.url.path in ("/health", "/health/ping", "/metrics"):
            return await call_next(request)

        # 1. 获取或生成 trace_id
        trace_id = request.headers.get("X-Trace-ID") or uuid.uuid4().hex[:16]

        # 2. 绑定请求上下文
        bind_request_context(
            trace_id=trace_id,
            method=request.method,
            path=request.url.path,
        )

        # 3. OpenTelemetry span (如果启用)
        if _HAS_OTEL:
            tracer = _get_tracer()
            with tracer.start_as_current_span("http_request") as span:
                span.set_attribute("http.method", request.method)
                span.set_attribute("http.path", request.url.path)
                span.set_attribute("trace_id", trace_id)

                response = await call_next(request)
                span.set_attribute("http.status_code", response.status_code)
        else:
            response = await call_next(request)

        # 4. 响应头写入 trace_id
        response.headers["X-Trace-ID"] = trace_id

        # 5. 清除上下文
        clear_request_context()

        return response


# ============================================================
#  OpenTelemetry 可选支持
# ============================================================

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    _HAS_OTEL = True
except ImportError:
    _HAS_OTEL = False


def setup_tracing(otlp_endpoint: str = "http://localhost:4317"):
    """初始化 OpenTelemetry 链路追踪

    Args:
        otlp_endpoint: OTLP gRPC 端点 (Jaeger/Tempo)
    """
    if not _HAS_OTEL:
        return

    provider = TracerProvider()
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    except ImportError:
        # 没有 OTLP exporter, 使用默认 ConsoleExporter
        pass

    trace.set_tracer_provider(provider)


def _get_tracer():
    """获取 tracer 实例"""
    if _HAS_OTEL:
        return trace.get_tracer("rag_system")
    return None
