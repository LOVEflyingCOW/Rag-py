from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_dep
from app.core.config import settings
from app.core.logging import logger
from app.core.redis import health_check as redis_health
from app.models.database import async_engine
from app.models.response import ApiResponse
from app.models.schemas import HealthInfo

router = APIRouter(prefix="/health", tags=["Health"])

__version__ = "2.1.0"


@router.get("", response_model=ApiResponse[HealthInfo])
async def health_check():
    """系统健康检查接口"""
    db_status = "ok"
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Database health check failed: %s", str(e))
        db_status = "error"

    redis_status = "ok"
    try:
        redis_info = await redis_health()
        redis_status = redis_info.get("status", "unknown")
        if redis_status == "degraded":
            redis_status = "degraded"
    except Exception as e:
        logger.error("Redis health check failed: %s", str(e))
        redis_status = "error"

    all_ok = db_status == "ok" and redis_status != "error"
    info = HealthInfo(
        status="ok" if all_ok else "degraded",
        app_name=settings.APP_NAME,
        version=__version__,
        database=db_status,
        redis=redis_status,
        timestamp=datetime.utcnow()
    )
    return ApiResponse[HealthInfo](data=info)


@router.get("/ping", response_model=ApiResponse[str])
async def ping():
    """简易 Ping 接口"""
    return ApiResponse[str](data="pong")
