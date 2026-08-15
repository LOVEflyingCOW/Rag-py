from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Response
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_dep
from app.core.config import settings
from app.core.logging import logger
from app.core.redis import health_check as redis_health
from app.core.metrics import get_metrics, is_metrics_enabled
from app.models.database import async_engine
from app.models.response import ApiResponse
from app.models.schemas import HealthInfo

router = APIRouter(prefix="/health", tags=["Health"])

__version__ = "3.0.0"


@router.get("", response_model=ApiResponse[HealthInfo])
async def health_check():
    """系统健康检查接口 (完整)"""
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


# ============================================================
#  健康探针分离 (Phase 5)
# ============================================================

@router.get("/liveness")
async def liveness():
    """存活探针 — 进程是否在运行

    Kubernetes liveness probe:
    - 200: 进程存活
    - 503: 进程异常 (需要重启)
    不检查依赖, 只检查进程本身。
    """
    return {"status": "alive", "timestamp": datetime.utcnow().isoformat()}


@router.get("/readiness")
async def readiness(db: AsyncSession = Depends(get_db_dep)):
    """就绪探针 — 是否可以接收流量

    Kubernetes readiness probe:
    - 200: 所有依赖正常, 可以接收流量
    - 503: 依赖异常, 不接收新流量 (但不重启)

    检查项:
    - Database (SELECT 1)
    - Redis (PING)
    - Celery (可选)
    """
    checks = {}

    # 1. Database
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"fail: {str(e)[:100]}"

    # 2. Redis
    try:
        redis_info = await redis_health()
        redis_status = redis_info.get("status", "unknown")
        checks["redis"] = redis_status
    except Exception as e:
        checks["redis"] = f"fail: {str(e)[:100]}"

    # 3. Celery (非阻塞检查)
    try:
        from app.core.celery_app import celery_app
        inspect = celery_app.control.inspect(timeout=1)
        active = inspect.stats()
        checks["celery"] = "ok" if active else "no_workers"
    except Exception:
        checks["celery"] = "unknown"

    # 4. Prometheus (可选)
    checks["metrics"] = "enabled" if is_metrics_enabled() else "disabled"

    # 汇总
    critical_ok = checks.get("database") == "ok"
    all_ok = critical_ok and checks.get("redis") != "error"

    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "ready" if all_ok else "not_ready",
            "checks": checks,
            "version": __version__,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )


# ============================================================
#  Prometheus 指标端点
# ============================================================

@router.get("/metrics", include_in_schema=False)
async def metrics():
    """Prometheus 指标端点

    供 Prometheus 抓取, 输出文本格式指标数据。
    """
    data, content_type = get_metrics()
    return PlainTextResponse(content=data, media_type=content_type)
