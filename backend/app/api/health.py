from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_dep
from app.core.config import settings
from app.core.logging import logger
from app.models.database import engine
from app.models.response import ApiResponse
from app.models.schemas import HealthInfo

router = APIRouter(prefix="/health", tags=["Health"])

__version__ = "0.1.0"


@router.get("", response_model=ApiResponse[HealthInfo])
async def health_check():
    """系统健康检查接口"""
    db_status = "ok"
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Database health check failed: %s", str(e))
        db_status = "error"

    info = HealthInfo(
        status="ok" if db_status == "ok" else "degraded",
        app_name=settings.APP_NAME,
        version=__version__,
        database=db_status,
        timestamp=datetime.utcnow()
    )
    return ApiResponse[HealthInfo](data=info)


@router.get("/ping", response_model=ApiResponse[str])
async def ping():
    """简易 Ping 接口"""
    return ApiResponse[str](data="pong")