from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.logging import logger


class RAGBaseException(Exception):
    """系统自定义异常基类"""

    def __init__(self, message: str, code: int = 400, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)


class NotFoundError(RAGBaseException):
    """资源未找到异常"""

    def __init__(self, message: str = "Resource not found", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=status.HTTP_404_NOT_FOUND, details=details)


class ValidationError(RAGBaseException):
    """参数验证异常"""

    def __init__(self, message: str = "Validation failed", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=status.HTTP_400_BAD_REQUEST, details=details)


class UnauthorizedError(RAGBaseException):
    """未授权异常"""

    def __init__(self, message: str = "Unauthorized", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=status.HTTP_401_UNAUTHORIZED, details=details)


class InternalError(RAGBaseException):
    """内部错误异常"""

    def __init__(self, message: str = "Internal server error", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=status.HTTP_500_INTERNAL_SERVER_ERROR, details=details)


class ErrorResponse(BaseModel):
    success: bool = False
    code: int
    message: str
    details: Dict[str, Any] = {}


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局异常处理器"""

    if isinstance(exc, RAGBaseException):
        logger.warning("Business error: %s - %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.code,
            content=ErrorResponse(
                code=exc.code,
                message=exc.message,
                details=exc.details
            ).dict()
        )

    logger.exception("Unhandled exception: %s", str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Internal server error",
            details={"error": str(exc)} if False else {}
        ).dict()
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI 参数验证异常处理器"""
    message = "Request validation failed"
    details = {}
    try:
        details = {"errors": exc.errors()}
    except Exception:
        pass
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=message,
            details=details
        ).dict()
    )