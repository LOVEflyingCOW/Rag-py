"""FastAPI 依赖注入 — 鉴权 / 当前用户获取

设计原则 (高并发优化):
1. 默认 get_current_user 只解析 JWT, 不查数据库, 典型 <1ms
2. 仅在需要实时 RBAC / 账户状态的场景使用 get_current_user_from_db
3. 避免"每请求必查用户表"造成的连接池压力
4. Token 黑名单检查走 Redis (异步, 精确)
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_db_dep
from app.models.entities.user import User
from app.core.security import (
    extract_user_from_token,
    extract_user_from_token_async,
    is_token_revoked,
    is_token_revoked_async,
)


security = HTTPBearer(auto_error=False)


class CurrentUser:
    """轻量级当前用户对象 (完全由 JWT 构造, 零 DB 查询)"""

    __slots__ = ("user_id", "username", "roles", "jti", "is_admin", "is_active")

    def __init__(self, user_id: int, username: str, roles: list, jti: str = ""):
        self.user_id = user_id
        self.username = username
        self.roles = roles or []
        self.jti = jti
        # JWT-only 模式下无法获知实时状态, 默认 True / 根据 roles 推断
        self.is_admin = "admin" in self.roles
        self.is_active = True


async def _extract_from_credentials(
    credentials: Optional[HTTPAuthorizationCredentials],
) -> CurrentUser:
    """从 Bearer Token 提取用户信息 (异步, 精确检查 Redis 黑名单)"""
    if credentials is None or not credentials.scheme.lower() == "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供有效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # 异步检查 Redis 黑名单 (精确)
    if await is_token_revoked_async(token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证令牌已被撤销",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 异步提取用户信息 (内部也会检查黑名单, 但上面已检查过, 这里主要做 JWT 解码)
    user_info = await extract_user_from_token_async(token)
    if user_info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return CurrentUser(
        user_id=user_info["user_id"],
        username=user_info["username"],
        roles=user_info["roles"],
        jti=user_info.get("jti", ""),
    )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> CurrentUser:
    """必须登录 — JWT-only 模式 (不查数据库, 高并发友好)

    绝大多数业务接口应使用此依赖, 避免每请求查询 users 表.
    """
    return await _extract_from_credentials(credentials)


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> Optional[CurrentUser]:
    """可选登录 — JWT-only 模式, 未登录时返回 None"""
    if credentials is None or not credentials.scheme.lower() == "bearer":
        return None
    try:
        return await _extract_from_credentials(credentials)
    except HTTPException:
        return None


async def get_current_user_from_db(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db_dep),
) -> User:
    """需要实时数据库状态的场景 (管理员校验/账户冻结检查等)

    注意: 此依赖会查询 users 表, 仅在必须时使用.
    """
    token_user = await _extract_from_credentials(credentials)

    result = await db.execute(select(User).where(User.id == token_user.user_id))
    user = result.scalars().first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户账户已停用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_admin(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    """当前用户必须是管理员 (JWT 角色判定, 零 DB)"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user


# ============================================================
#  RBAC 细粒度权限依赖 (Require Permission)
# ============================================================
# 设计原则:
# 1. Admin 无条件通过
# 2. 用户显式拥有 resource:action 权限 → 通过
# 3. 用户在 user_roles 表中没有任何角色行 (Legacy 用户) → 通过,
#    继续走 Service 层所有权检查 (向后兼容历史数据)
# 4. 有角色但缺少对应权限 → 403
# 注意: 第 3 点仅在注册流程尚未走默认角色分配时触发；新用户注册时
#       会由 _assign_default_role() 自动分配 editor 角色。
# ============================================================

def require_permission(resource: str, action: str):
    """细粒度权限校验工厂。使用方法:

        @router.post("")
        async def create_kb(
            user: CurrentUser = Depends(require_permission("kb", "create")),
            ...
        ):
            ...
    """
    async def checker(
        current_user: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db_dep),
    ) -> CurrentUser:
        # 1. Admin 兜底 (JWT 角色判定)
        if current_user.is_admin:
            return current_user

        # 2. Legacy 用户 (user_roles 中无任何记录) — 向后兼容
        from sqlalchemy import text
        has_any_role = (await db.execute(
            text("SELECT 1 FROM user_roles WHERE user_id = :uid LIMIT 1"),
            {"uid": current_user.user_id}
        )).fetchone()
        if not has_any_role:
            # 没有角色分配的老用户：交由 Service 层所有权校验
            return current_user

        # 3. 显式权限检查 (通过 roles → role_permissions → permissions)
        role_names = [r for r in (current_user.roles or [])]
        if role_names:
            placeholders = ",".join([f":r{i}" for i in range(len(role_names))])
            params = {f"r{i}": rn for i, rn in enumerate(role_names)}
            params["res"] = resource
            params["act"] = action
            sql = (
                "SELECT 1 FROM permissions p "
                "JOIN role_permissions rp ON rp.permission_id = p.id "
                "JOIN roles r ON r.id = rp.role_id "
                f"WHERE r.name IN ({placeholders}) "
                "  AND p.resource = :res AND p.action = :act LIMIT 1"
            )
            has = (await db.execute(text(sql), params)).fetchone()
            if has:
                return current_user

        # 4. 不满足 — 403
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="缺少 %s:%s 权限，请联系管理员" % (resource, action),
        )

    return checker


def require_any_role(*roles: str):
    """要求用户拥有任一指定角色。

    例: require_any_role("admin", "editor") → 管理员或编辑者可访问
    """
    async def checker(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> CurrentUser:
        if current_user.is_admin and "admin" in roles:
            return current_user
        user_roles_set = set(current_user.roles or [])
        for r in roles:
            if r in user_roles_set:
                return current_user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要以下角色之一: %s" % ", ".join(roles),
        )

    return checker
