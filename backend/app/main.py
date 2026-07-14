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
        version="0.1.0",
        description="RAG Knowledge Base System - Built from scratch",
        docs_url="/docs",
        redoc_url="/redoc",
        debug=settings.APP_DEBUG
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    app.include_router(health.router)
    app.include_router(api_router)

    @app.on_event("startup")
    async def startup_event():
        from app.models.database import init_db
        logger.info("Application starting up...")
        try:
            init_db()
            logger.info("Database initialized")
        except Exception as e:
            logger.error("Failed to initialize database: %s", str(e))
        logger.info("Application started successfully")

    @app.on_event("shutdown")
    async def shutdown_event():
        logger.info("Application shutting down...")

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "name": settings.APP_NAME,
            "version": "0.1.0",
            "status": "running",
            "docs": "/docs",
            "health": "/health"
        }

    return app


app = create_app()