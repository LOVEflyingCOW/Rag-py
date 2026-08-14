from __future__ import annotations

"""鉴权相关实体模型

包含:
- RefreshToken: Refresh Token 存储 (支持轮换和撤销)
- Role: 角色表
- Permission: 权限表
- AuditLog: 审计日志表
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, ForeignKey, Table, func,
)
from sqlalchemy.orm import relationship

from app.models.database import Base


# ============================================================
#  关联表
# ============================================================

role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
    Column("permission_id", Integer, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True),
)

user_roles = Table(
    "user_roles",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("role_id", Integer, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True),
)


# ============================================================
#  RefreshToken
# ============================================================

class RefreshToken(Base):
    """Refresh Token — 支持轮换和撤销"""
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(128), unique=True, nullable=False, index=True)  # SHA-256 摘要
    device_info = Column(String(500), nullable=True)
    ip_address = Column(String(45), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)  # 非空 = 已撤销
    created_at = Column(DateTime, server_default=func.now())

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > datetime.utcnow()


# ============================================================
#  Role
# ============================================================

class Role(Base):
    """角色表"""
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), unique=True, nullable=False)  # admin / editor / viewer
    description = Column(String(200), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    permissions = relationship("Permission", secondary=role_permissions, lazy="selectin")


# ============================================================
#  Permission
# ============================================================

class Permission(Base):
    """权限表 — 资源 + 操作的细粒度权限"""
    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    resource = Column(String(50), nullable=False)  # kb / document / conversation / user
    action = Column(String(50), nullable=False)    # create / read / update / delete
    description = Column(String(200), nullable=True)

    class Config:
        unique_together = ("resource", "action")


# ============================================================
#  AuditLog
# ============================================================

class AuditLog(Base):
    """审计日志表 — 记录所有 API 调用"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(100), nullable=True)
    method = Column(String(10), nullable=False)   # GET/POST/PUT/DELETE
    path = Column(String(500), nullable=False, index=True)
    status_code = Column(Integer, nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    request_body = Column(Text, nullable=True)     # 脱敏后的请求体摘要
    response_time_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
