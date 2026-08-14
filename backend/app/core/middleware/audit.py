"""审计日志中间件 — 记录所有 API 调用, 异步写入 DB

Phase 3: 通过 Celery 异步写入 audit_logs 表, 不阻塞请求.

流程:
  请求 → 中间件收集信息 → 执行请求 → 计算 duration → dispatch Celery 任务 → 返回响应
  Celery Worker → write_audit_log → 写入 DB
"""

from __future__ import annotations

import time
import json
import hashlib
from datetime import datetime

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.logging import logger


# 不记录审计日志的路径
EXEMPT_PATHS = {"/", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}

# 请求体最大记录长度
MAX_BODY_LOG = 500

# 敏感字段 (脱敏)
SENSITIVE_FIELDS = {"password", "confirm_password", "token", "secret", "api_key"}

# 超过此大小 (bytes) 的请求不记录 body
MAX_BODY_SIZE = 10000


def _sanitize_body(body: str) -> str:
    """脱敏请求体中的敏感字段"""
    if not body:
        return ""
    try:
        data = json.loads(body)
        _sanitize_dict(data)
        return json.dumps(data, ensure_ascii=False)[:MAX_BODY_LOG]
    except (json.JSONDecodeError, TypeError):
        return body[:MAX_BODY_LOG] if body else ""


def _sanitize_dict(d: dict) -> None:
    """递归脱敏字典中的敏感字段"""
    for key in list(d.keys()):
        if key.lower() in SENSITIVE_FIELDS:
            d[key] = "***"
        elif isinstance(d[key], dict):
            _sanitize_dict(d[key])
        elif isinstance(d[key], list):
            for item in d[key]:
                if isinstance(item, dict):
                    _sanitize_dict(item)


def _extract_user_id_from_token(auth_header: str) -> tuple:
    """从 Authorization 头快速提取 user_id 和 username (仅解析 JWT payload, 不验签)

    Returns: (user_id, username) 或 (None, "anonymous")
    """
    if not auth_header.startswith("Bearer "):
        return None, "anonymous"

    token = auth_header[7:]
    if len(token) < 20:
        return None, "anonymous"

    try:
        import base64
        # JWT 格式: header.payload.signature
        parts = token.split(".")
        if len(parts) != 3:
            return None, "anonymous"

        # 解码 payload (第二部分)
        payload_bytes = parts[1]
        # 补全 base64 padding
        padding = 4 - len(payload_bytes) % 4
        if padding != 4:
            payload_bytes += "=" * padding

        payload_json = base64.urlsafe_b64decode(payload_bytes)
        payload = json.loads(payload_json)

        user_id = int(payload.get("sub", 0)) if payload.get("sub") else None
        username = payload.get("username", "authenticated")

        return user_id, username

    except Exception:
        return None, "authenticated"


class AuditLogMiddleware(BaseHTTPMiddleware):
    """审计日志中间件 — Celery 异步写入 DB"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in EXEMPT_PATHS or path.startswith("/docs"):
            return await call_next(request)

        start_time = time.time()
        method = request.method
        client_ip = request.client.host if request.client else "unknown"
        user_agent = request.headers.get("User-Agent", "")[:500]

        # 执行请求
        response = await call_next(request)

        # 计算响应时间
        duration_ms = int((time.time() - start_time) * 1000)

        # 从 Authorization 头提取用户信息 (仅解析 JWT payload, 不查 DB)
        auth_header = request.headers.get("Authorization", "")
        user_id, username = _extract_user_id_from_token(auth_header)

        # Dispatch Celery 任务 (非阻塞)
        try:
            from app.tasks.audit_tasks import write_audit_log

            write_audit_log.delay(
                user_id=user_id,
                username=username,
                method=method,
                path=path,
                status_code=response.status_code,
                ip_address=client_ip,
                user_agent=user_agent,
                request_body=None,  # 不读取 body (避免 BaseHTTPMiddleware 消费问题)
                response_time_ms=duration_ms,
            )
        except Exception as e:
            # Celery 不可用时降级到日志
            logger.warning("Celery audit dispatch failed, logging only: %s", str(e))
            logger.info(
                "AUDIT | %s %s | status=%d | %dms | user=%s | ip=%s",
                method, path, response.status_code, duration_ms,
                username, client_ip,
            )

        return response
