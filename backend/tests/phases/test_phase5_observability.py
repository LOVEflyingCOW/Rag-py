"""Phase 5 测试: 可观测性 + 安全增强

测试覆盖:
  A. structlog JSON 日志 — 日志初始化、trace_id 上下文绑定
  B. TracingMiddleware — trace_id 注入、响应头
  C. Prometheus 指标 — 指标记录、/metrics 端点
  D. 健康探针 — liveness/readiness 分离
  E. OAuth2 — provider 配置、状态查询、未配置时 400
"""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch


# ============================================================
#  A. structlog JSON 日志测试
# ============================================================

class TestStructlogLogging:
    """结构化日志测试"""

    def test_module_import(self):
        """模块可正常导入"""
        from app.core.logging import setup_logging, get_logger
        assert callable(setup_logging)
        assert callable(get_logger)

    def test_get_logger_returns_logger(self):
        """get_logger 返回 logger 实例"""
        from app.core.logging import get_logger
        log = get_logger("test")
        # structlog BoundLogger 或标准 logging.Logger
        assert log is not None
        assert hasattr(log, "info")
        assert hasattr(log, "error")

    def test_has_structlog_flag(self):
        """_HAS_STRUCTLOG 标志存在"""
        from app.core.logging import _HAS_STRUCTLOG
        assert isinstance(_HAS_STRUCTLOG, bool)

    def test_bind_request_context(self):
        """bind_request_context 返回 trace_id"""
        from app.core.logging import bind_request_context, clear_request_context
        trace_id = bind_request_context(
            method="GET",
            path="/api/v1/test",
        )
        assert trace_id is not None
        assert len(trace_id) > 0
        clear_request_context()

    def test_bind_request_context_with_custom_trace_id(self):
        """自定义 trace_id 被正确返回"""
        from app.core.logging import bind_request_context, clear_request_context
        trace_id = bind_request_context(trace_id="custom-trace-123")
        assert trace_id == "custom-trace-123"
        clear_request_context()

    def test_bind_user_context(self):
        """bind_user_context 不抛异常"""
        from app.core.logging import bind_user_context, clear_request_context
        bind_user_context(user_id=42, username="testuser")
        # 清理
        clear_request_context()

    def test_clear_request_context(self):
        """clear_request_context 不抛异常"""
        from app.core.logging import clear_request_context
        clear_request_context()  # 不应抛异常


# ============================================================
#  B. TracingMiddleware 测试
# ============================================================

class TestTracingMiddleware:
    """Tracing 中间件测试"""

    def test_module_import(self):
        """模块可导入"""
        from app.core.middleware.tracing import TracingMiddleware
        assert TracingMiddleware is not None

    @pytest.mark.asyncio
    async def test_trace_id_generated(self):
        """请求未带 X-Trace-ID 时自动生成"""
        from app.core.middleware.tracing import TracingMiddleware

        middleware = TracingMiddleware(app=MagicMock())

        # Mock request 和 call_next
        request = MagicMock()
        request.headers = {}
        request.url.path = "/api/v1/test"
        request.method = "GET"

        response = MagicMock()
        response.headers = {}

        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)

        # 响应头应有 X-Trace-ID
        assert "X-Trace-ID" in result.headers
        assert len(result.headers["X-Trace-ID"]) > 0

    @pytest.mark.asyncio
    async def test_trace_id_preserved(self):
        """请求带 X-Trace-ID 时保留"""
        from app.core.middleware.tracing import TracingMiddleware

        middleware = TracingMiddleware(app=MagicMock())

        request = MagicMock()
        request.headers = {"X-Trace-ID": "my-trace-id"}
        request.url.path = "/api/v1/test"
        request.method = "POST"

        response = MagicMock()
        response.headers = {}

        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)

        assert result.headers["X-Trace-ID"] == "my-trace-id"

    @pytest.mark.asyncio
    async def test_health_endpoints_skipped(self):
        """健康检查端点跳过 trace 注入"""
        from app.core.middleware.tracing import TracingMiddleware

        middleware = TracingMiddleware(app=MagicMock())

        for path in ["/health", "/health/ping", "/metrics"]:
            request = MagicMock()
            request.headers = {}
            request.url.path = path
            request.method = "GET"

            response = MagicMock()
            response.headers = {}

            call_next = AsyncMock(return_value=response)
            result = await middleware.dispatch(request, call_next)

            # 健康端点不添加 X-Trace-ID
            assert "X-Trace-ID" not in result.headers

    def test_has_otel_flag(self):
        """_HAS_OTEL 标志存在"""
        from app.core.middleware.tracing import _HAS_OTEL
        assert isinstance(_HAS_OTEL, bool)


# ============================================================
#  C. Prometheus 指标测试
# ============================================================

class TestPrometheusMetrics:
    """Prometheus 指标测试"""

    def test_module_import(self):
        """模块可导入"""
        from app.core.metrics import (
            record_http_request,
            record_rag_query,
            get_metrics,
            is_metrics_enabled,
        )
        assert callable(record_http_request)
        assert callable(record_rag_query)
        assert callable(get_metrics)
        assert callable(is_metrics_enabled)

    def test_is_metrics_enabled(self):
        """is_metrics_enabled 返回 bool"""
        from app.core.metrics import is_metrics_enabled
        assert isinstance(is_metrics_enabled(), bool)

    def test_record_http_request_no_error(self):
        """record_http_request 不抛异常 (无 prometheus 时 no-op)"""
        from app.core.metrics import record_http_request
        record_http_request("GET", "/api/v1/test", 200, 0.05)
        record_http_request("POST", "/api/v1/auth/register", 201, 0.15)
        record_http_request("GET", "/api/v1/error", 500, 1.5)

    def test_record_rag_query_no_error(self):
        """record_rag_query 不抛异常"""
        from app.core.metrics import record_rag_query
        record_rag_query(kb_id=1, provider="deepseek", duration=0.8)
        record_rag_query(kb_id=2, provider="openai", duration=1.2)

    def test_record_retrieval_no_error(self):
        """record_retrieval 不抛异常"""
        from app.core.metrics import record_retrieval
        record_retrieval(kb_id=1, duration=0.05)

    def test_record_llm_no_error(self):
        """record_llm 不抛异常"""
        from app.core.metrics import record_llm
        record_llm(provider="deepseek", duration=0.5)

    def test_set_active_users(self):
        """set_active_users 不抛异常"""
        from app.core.metrics import set_active_users
        set_active_users(42)

    def test_set_db_pool_metrics(self):
        """set_db_pool_metrics 不抛异常"""
        from app.core.metrics import set_db_pool_metrics
        set_db_pool_metrics(in_use=5, pool_size=20)

    def test_set_cache_hit_rate(self):
        """set_cache_hit_rate 不抛异常"""
        from app.core.metrics import set_cache_hit_rate
        set_cache_hit_rate("kb", 0.85)

    def test_get_metrics_returns_data(self):
        """get_metrics 返回元组 (data, content_type)"""
        from app.core.metrics import get_metrics
        data, content_type = get_metrics()
        assert isinstance(data, (bytes, str))
        assert isinstance(content_type, str)

    @pytest.mark.asyncio
    async def test_metrics_endpoint(self):
        """/metrics 端点返回指标数据"""
        from app.api.health import metrics
        response = await metrics()
        assert response.status_code == 200
        assert "text" in response.media_type


# ============================================================
#  D. 健康探针测试
# ============================================================

class TestHealthProbes:
    """健康探针分离测试"""

    @pytest.mark.asyncio
    async def test_liveness(self):
        """存活探针返回 200"""
        from app.api.health import liveness
        result = await liveness()
        assert result["status"] == "alive"
        assert "timestamp" in result

    def test_liveness_route_exists(self):
        """/health/liveness 路由存在"""
        from app.api.health import router
        routes = [r.path for r in router.routes]
        assert "/health/liveness" in routes

    def test_readiness_route_exists(self):
        """/health/readiness 路由存在"""
        from app.api.health import router
        routes = [r.path for r in router.routes]
        assert "/health/readiness" in routes

    def test_metrics_route_exists(self):
        """/health/metrics 路由存在"""
        from app.api.health import router
        routes = [r.path for r in router.routes]
        assert "/health/metrics" in routes

    def test_ping_route_exists(self):
        """/health/ping 路由存在"""
        from app.api.health import router
        routes = [r.path for r in router.routes]
        assert "/health/ping" in routes

    def test_version_updated(self):
        """版本号已更新"""
        from app.api.health import __version__
        # v3.0.0 — Phase 5 版本
        assert __version__ == "3.0.0"


# ============================================================
#  E. OAuth2 测试
# ============================================================

class TestOAuth2:
    """OAuth2 三方登录测试"""

    def test_module_import(self):
        """模块可导入"""
        from app.api.v1.oauth import router, PROVIDERS
        assert router is not None
        assert "github" in PROVIDERS
        assert "google" in PROVIDERS

    def test_oauth_routes_exist(self):
        """OAuth 路由存在"""
        from app.api.v1.oauth import router
        routes = [r.path for r in router.routes]
        assert any("login" in r for r in routes)
        assert any("callback" in r for r in routes)
        assert any("status" in r for r in routes)

    @pytest.mark.asyncio
    async def test_oauth_status(self):
        """OAuth 状态查询"""
        from app.api.v1.oauth import oauth_status
        result = await oauth_status()
        assert "available_providers" in result
        assert "configured" in result
        assert isinstance(result["available_providers"], list)

    @pytest.mark.asyncio
    async def test_oauth_login_unsupported_provider(self):
        """不支持的 provider 返回 400"""
        from app.api.v1.oauth import oauth_login
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await oauth_login("twitter")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_oauth_login_not_configured(self):
        """未配置 Client ID 时返回 400"""
        from app.api.v1.oauth import oauth_login, PROVIDERS
        from fastapi import HTTPException

        # 确保 github 未配置 (默认无 client_id)
        original_id = PROVIDERS["github"]["client_id"]
        PROVIDERS["github"]["client_id"] = ""

        try:
            with pytest.raises(HTTPException) as exc_info:
                await oauth_login("github")
            assert exc_info.value.status_code == 400
            assert "not configured" in exc_info.value.detail.lower()
        finally:
            PROVIDERS["github"]["client_id"] = original_id

    @pytest.mark.asyncio
    async def test_oauth_callback_invalid_state(self):
        """无效 state 返回 400"""
        from app.api.v1.oauth import oauth_callback, PROVIDERS
        from fastapi import HTTPException

        # 临时设置 client_id 以跳过 "not configured" 检查
        original_id = PROVIDERS["github"]["client_id"]
        PROVIDERS["github"]["client_id"] = "fake_client_id"

        # Mock dependencies
        mock_db = MagicMock()

        try:
            with patch("app.api.v1.oauth.is_redis_available", return_value=True):
                with patch("app.api.v1.oauth.get_redis_client") as mock_redis:
                    mock_client = MagicMock()
                    mock_client.get = AsyncMock(return_value=None)
                    mock_redis.return_value = mock_client

                    with pytest.raises(HTTPException) as exc_info:
                        await oauth_callback(
                            provider="github",
                            code="fake_code",
                            state="invalid_state",
                            db=mock_db,
                        )
                    assert exc_info.value.status_code == 400
                    assert "state" in exc_info.value.detail.lower()
        finally:
            PROVIDERS["github"]["client_id"] = original_id

    @pytest.mark.asyncio
    async def test_oauth_callback_unsupported_provider(self):
        """不支持的 provider 回调返回 400"""
        from app.api.v1.oauth import oauth_callback
        from fastapi import HTTPException

        mock_db = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            await oauth_callback("twitter", "code", "state", mock_db)
        assert exc_info.value.status_code == 400


# ============================================================
#  F. 中间件注册测试
# ============================================================

class TestMiddlewareRegistration:
    """中间件注册完整性测试"""

    def test_all_middleware_exported(self):
        """所有中间件从 __init__ 导出"""
        from app.core.middleware import (
            RateLimitMiddleware,
            AuditLogMiddleware,
            SecurityHeadersMiddleware,
            IdempotencyMiddleware,
            TracingMiddleware,
            MetricsMiddleware,
        )
        # 确保都是类
        for mw in [
            RateLimitMiddleware,
            AuditLogMiddleware,
            SecurityHeadersMiddleware,
            IdempotencyMiddleware,
            TracingMiddleware,
            MetricsMiddleware,
        ]:
            assert isinstance(mw, type)

    def test_middleware_count(self):
        """中间件模块导出 6 个中间件"""
        from app.core.middleware import __all__
        middleware_names = [name for name in __all__ if name.endswith("Middleware")]
        assert len(middleware_names) == 6
