from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar, Optional

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一的 API 响应格式"""
    success: bool = True
    code: int = 200
    message: str = "OK"
    data: Optional[T] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True