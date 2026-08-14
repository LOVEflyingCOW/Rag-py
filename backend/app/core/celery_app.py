"""Celery 应用配置 — 后台任务异步处理

Broker: Redis (db 1)
Result Backend: Redis (db 2)

任务模块 (自动发现):
  - app.tasks.audit_tasks: 审计日志异步写入 DB
  - app.tasks.document_tasks: 文档向量化异步处理

启动 Worker (Windows):
  celery -A app.core.celery_app worker --loglevel=info --pool=solo

启动 Worker (Linux/Mac):
  celery -A app.core.celery_app worker --loglevel=info
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings
from app.core.logging import logger


# 创建 Celery 应用
celery_app = Celery(
    "rag_system",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.audit_tasks",
        "app.tasks.document_tasks",
    ],
)

# Celery 配置
celery_app.conf.update(
    # 序列化
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # 时区
    timezone="Asia/Shanghai",
    enable_utc=True,

    # 任务超时
    task_time_limit=settings.CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=settings.CELERY_TASK_SOFT_TIME_LIMIT,

    # Worker 配置
    worker_prefetch_multiplier=settings.CELERY_WORKER_PREFETCH_MULTIPLIER,
    worker_max_tasks_per_child=settings.CELERY_WORKER_MAX_TASKS_PER_CHILD,

    # 任务路由
    task_routes={
        "app.tasks.audit_tasks.write_audit_log": {"queue": "audit"},
        "app.tasks.document_tasks.process_document": {"queue": "document"},
    },

    # 默认队列
    task_default_queue="default",

    # 结果过期时间 (1 小时)
    result_expires=3600,

    # 任务失败重试
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# 定时任务 (Beat) — 可选, 用于定期清理
celery_app.conf.beat_schedule = {
    # 每天凌晨 3 点清理过期的任务结果
    "cleanup-expired-results": {
        "task": "app.tasks.audit_tasks.cleanup_old_audit_logs",
        "schedule": crontab(hour=3, minute=0),
    },
}


@celery_app.task(bind=True)
def debug_task(self):
    """Celery 连通性测试任务"""
    logger.info("Celery debug task called: %s", self.request.id)
    return {"status": "ok", "message": "Celery is working"}


# 初始化日志
logger.info(
    "Celery configured: broker=%s, backend=%s",
    settings.CELERY_BROKER_URL,
    settings.CELERY_RESULT_BACKEND,
)
