from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.v1 import api_router
from app.core.config import settings
from app.core.exceptions import (
    global_exception_handler,
    validation_exception_handler
)
from app.core.logging import logger


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    settings.ensure_dirs()

    app = FastAPI(
        title=settings.APP_NAME,
        version="2.0.0",
        description="RAG Knowledge Base System - Industrial Grade",
        docs_url="/docs",
        redoc_url="/redoc",
        debug=settings.APP_DEBUG
    )

    # 中间件 (注册顺序: 后注册的先执行)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from app.core.middleware import (
        RateLimitMiddleware,
        AuditLogMiddleware,
        SecurityHeadersMiddleware,
    )
    app.add_middleware(AuditLogMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    app.include_router(health.router)
    app.include_router(api_router, prefix="/api")

    @app.on_event("startup")
    async def startup_event():
        from app.models.database import init_db
        from app.core.redis import init_redis
        from app.core.middleware.rate_limit import _load_lua_script
        logger.info("Application starting up...")

        # 初始化数据库
        try:
            await init_db()
            logger.info("Database initialized")
        except Exception as e:
            logger.error("Failed to initialize database: %s", str(e))

        # 初始化 Redis
        redis_ok = await init_redis()
        if redis_ok:
            # 预加载限流 Lua 脚本
            await _load_lua_script()
            logger.info("Redis initialized (rate limiter + token blacklist active)")
        else:
            logger.warning("Redis unavailable, using in-memory fallback for rate limiting and blacklist")

        logger.info("Application started successfully")

    @app.on_event("shutdown")
    async def shutdown_event():
        from app.core.redis import close_redis
        from app.models.database import dispose_async_engine, dispose_sync_engine
        logger.info("Application shutting down...")
        await close_redis()
        await dispose_async_engine()
        dispose_sync_engine()
        logger.info("Application shut down")

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "name": settings.APP_NAME,
            "version": "2.0.0",
            "status": "running",
            "docs": "/docs",
            "health": "/health"
        }

    return app


app = create_app()