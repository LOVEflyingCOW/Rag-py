"""默认 Auth 插件：用户模型 + JWT fixture（test_user / admin_user / rbac_user）"""
from __future__ import annotations

import asyncio
from typing import Any, List

import pytest
from sqlalchemy import text

from conftest_plugins import import_attr


def _get_proj(cfg: dict, key: str, default: Any) -> Any:
    return (((cfg or {}).get("project") or {}).get(key)) or default


def _field_mapping(cfg: dict) -> dict:
    return _get_proj(cfg, "user_fields", {
        "username": "username",
        "email": "email",
        "password_hash": "password_hash",
        "is_active": "is_active",
        "is_admin": "is_admin",
    })


class TestUser:
    """通用用户包装：.id/.username/.auth_headers 直接取"""

    def __init__(self, user: Any, access_token: str, refresh_token: str, roles: List[str] | None = None):
        self.user = user
        self.id = user.id
        self.username = getattr(user, "username", None)
        self.roles = roles or []
        self.access_token = access_token
        self.refresh_token = refresh_token

    @property
    def auth_headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}


def register_fixtures(registry, cfg: dict) -> None:
    import asyncio  # noqa: F811 (shadow top level intentional? no, reuse)
    from sqlalchemy.ext.asyncio import AsyncSession

    # ---- 读取配置 ----
    user_model_path = _get_proj(cfg, "user_model_path", "app.models.entities:User")
    security_mod = _get_proj(cfg, "security_module", "app.core.security")
    fn_hash_name = _get_proj(cfg, "fn_hash_password", "hash_password")
    fn_acc_name = _get_proj(cfg, "fn_create_access_token", "create_access_token")
    fn_ref_name = _get_proj(cfg, "fn_create_refresh_token", "create_refresh_token")
    default_pw = _get_proj(cfg, "user_default_password", "Test123456")
    fields = _field_mapping(cfg)

    sec = __import__(security_mod, fromlist=["*"])
    hash_password = getattr(sec, fn_hash_name)
    create_access_token = getattr(sec, fn_acc_name)
    create_refresh_token = getattr(sec, fn_ref_name)
    UserModel = import_attr(user_model_path)

    # ---- ts 工具: 唯一化用户名 ----
    def _ts() -> str:
        return str(int(asyncio.get_event_loop().time() * 1000) % 10000000)

    def _make_testuser(user_obj: Any, roles: List[str] | None = None) -> TestUser:
        acc = create_access_token(user_id=user_obj.id,
                                  username=getattr(user_obj, fields["username"], None),
                                  roles=roles or [])
        ref = create_refresh_token(user_id=user_obj.id)
        return TestUser(user_obj, access_token=acc, refresh_token=ref, roles=roles)

    # ---- fixtures ----
    async def test_user(db_session: AsyncSession, mock_redis_module) -> TestUser:
        ts = _ts()
        u = UserModel(
            **{
                fields["username"]: f"pytest_user_{ts}",
                fields["email"]: f"pytest_{ts}@test.com",
                fields["password_hash"]: hash_password(default_pw),
                fields["is_active"]: True,
                fields["is_admin"]: False,
            }
        )
        db_session.add(u)
        await db_session.commit()
        await db_session.refresh(u)
        return _make_testuser(u, roles=[])

    async def admin_user(db_session: AsyncSession, mock_redis_module) -> TestUser:
        ts = _ts()
        u = UserModel(
            **{
                fields["username"]: f"pytest_admin_{ts}",
                fields["email"]: f"admin_{ts}@test.com",
                fields["password_hash"]: hash_password(default_pw),
                fields["is_active"]: True,
                fields["is_admin"]: True,
            }
        )
        db_session.add(u)
        await db_session.commit()
        await db_session.refresh(u)
        return _make_testuser(u, roles=["admin"])

    async def rbac_user(db_session: AsyncSession, mock_redis_module) -> TestUser:
        """带 role_user 行的用户，走 require_permission 真正查表分支"""
        ts = _ts()
        u = UserModel(
            **{
                fields["username"]: f"pytest_rbac_{ts}",
                fields["email"]: f"rbac_{ts}@test.com",
                fields["password_hash"]: hash_password(default_pw),
                fields["is_active"]: True,
                fields["is_admin"]: False,
            }
        )
        db_session.add(u)
        await db_session.flush()

        try:
            RoleModel = import_attr("app.models.entities:Role")
        except Exception:
            RoleModel = None

        r = (await db_session.execute(
            text("SELECT id, name FROM roles WHERE name = :n LIMIT 1"), {"n": "role_user"}
        )).fetchone()
        if r is None:
            if RoleModel is None:
                # 没 Role 模型就不建 roles 表，退化成普通用户
                role_id = 0
            else:
                role = RoleModel(name="role_user", description="测试内建普通用户角色")
                db_session.add(role)
                await db_session.flush()
                role_id = role.id
        else:
            role_id = r.id

        try:
            await db_session.execute(
                text("INSERT INTO user_roles (user_id, role_id) VALUES (:uid, :rid)"),
                {"uid": u.id, "rid": role_id},
            )
        except Exception:
            pass  # 没有 user_roles 表也能跑，角色分支走空返回

        await db_session.commit()
        await db_session.refresh(u)
        return _make_testuser(u, roles=["role_user"])

    registry.register("test_user", test_user, scope="function")
    registry.register("admin_user", admin_user, scope="function")
    registry.register("rbac_user", rbac_user, scope="function")
    registry._g["TestUser"] = TestUser
