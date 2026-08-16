"""多租户表 (Tenant)

支持 SaaS 化多租户隔离：
- 每个 User 属于 0 或 1 个 Tenant (nullable=True 兼容单租户历史数据)
- Tenant 有 plan 等级 (free/pro/enterprise) 用于限流和配额
- 通过 tenant_id 在 KB/Document 层做逻辑隔离
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.database import Base


class Tenant(Base):
    """租户表"""

    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    plan = Column(String(50), default="free", index=True)  # free / pro / enterprise
    is_active = Column(Boolean, default=True, index=True)

    # 配额 (0 或 NULL 表示不限)
    max_knowledge_bases = Column(Integer, nullable=True, default=None)
    max_documents_per_kb = Column(Integer, nullable=True, default=None)
    max_storage_mb = Column(Integer, nullable=True, default=None)
    max_requests_per_minute = Column(Integer, nullable=True, default=None)

    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now(), index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 反向关系：通过 User.tenant_id 反查
    users = relationship("User", back_populates="tenant", lazy="dynamic")

    def __repr__(self):
        return "<Tenant(id=%d, name='%s', plan='%s')>" % (self.id, self.name, self.plan)
