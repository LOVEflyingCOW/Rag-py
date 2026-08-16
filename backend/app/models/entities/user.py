from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.database import Base


class User(Base):
    """用户表"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=True)
    password_hash = Column(String(512), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)

    # 多租户：nullable=True 保持向后兼容，旧用户不强制绑定
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="users")
    knowledge_bases = relationship("KnowledgeBase", back_populates="owner", lazy="dynamic")
    conversations = relationship("Conversation", back_populates="owner", lazy="dynamic")
    roles = relationship("Role", secondary="user_roles", lazy="selectin")

    def __repr__(self):
        return "<User(id=%d, username='%s')>" % (self.id, self.username)