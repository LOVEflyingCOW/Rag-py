from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel

__all__ = ["HealthInfo"]


class HealthInfo(BaseModel):
    """健康检查信息"""
    status: str
    app_name: str
    version: str
    database: str
    timestamp: datetime