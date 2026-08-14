"""审计日志 Celery 任务 — 异步写入 DB

设计:
1. 审计中间件调用 write_audit_log.delay() — 非阻塞, <1ms
2. Celery Worker 消费消息, 用同步 DB Session 写入 audit_logs 表
3. 写入失败自动重试 (最多 3 次, 指数退避)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from celery import shared_task

from app.core.celery_app import celery_app
from app.core.logging import logger


@celery_app.task(
    bind=True,
    name="app.tasks.audit_tasks.write_audit_log",
    queue="audit",
    max_retries=3,
    default_retry_delay=5,  # 重试间隔 5 秒
    acks_late=True,
)
def write_audit_log(
    self,
    user_id: Optional[int] = None,
    username: str = "anonymous",
    method: str = "",
    path: str = "",
    status_code: int = 200,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_body: Optional[str] = None,
    response_time_ms: Optional[int] = None,
) -> dict:
    """异步写入审计日志到 DB

    Args:
        user_id: 用户 ID (匿名为 None)
        username: 用户名或 "anonymous"
        method: HTTP 方法 (GET/POST/PUT/DELETE)
        path: 请求路径
        status_code: HTTP 状态码
        ip_address: 客户端 IP
        user_agent: User-Agent
        request_body: 脱敏后的请求体
        response_time_ms: 响应耗时 (毫秒)

    Returns:
        dict: {"success": bool, "audit_id": int}
    """
    try:
        from app.models.database import SessionLocal
        from app.models.entities.auth import AuditLog

        db = SessionLocal()
        try:
            audit = AuditLog(
                user_id=user_id,
                username=username,
                method=method,
                path=path[:500],
                status_code=status_code,
                ip_address=ip_address,
                user_agent=user_agent[:500] if user_agent else None,
                request_body=request_body[:5000] if request_body else None,
                response_time_ms=response_time_ms,
            )
            db.add(audit)
            db.commit()
            db.refresh(audit)

            logger.debug(
                "AUDIT_LOG_WRITTEN id=%s method=%s path=%s status=%d",
                audit.id, method, path, status_code,
            )
            return {"success": True, "audit_id": audit.id}

        finally:
            db.close()

    except Exception as e:
        logger.error("Failed to write audit log: %s", str(e))
        # 重试 (指数退避)
        raise self.retry(exc=e, countdown=5 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    name="app.tasks.audit_tasks.cleanup_old_audit_logs",
    queue="audit",
)
def cleanup_old_audit_logs(self, days: int = 90) -> dict:
    """定时清理过期的审计日志 (默认保留 90 天)

    由 Celery Beat 每天凌晨 3 点自动触发.
    """
    try:
        from app.models.database import SessionLocal
        from app.models.entities.auth import AuditLog
        from sqlalchemy import delete, func

        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            result = db.execute(
                delete(AuditLog).where(AuditLog.created_at < cutoff)
            )
            db.commit()
            deleted = result.rowcount

            logger.info("Cleaned up %d audit logs older than %d days", deleted, days)
            return {"success": True, "deleted": deleted}

        finally:
            db.close()

    except Exception as e:
        logger.error("Failed to cleanup audit logs: %s", str(e))
        raise self.retry(exc=e, countdown=60)
