"""B2 · 集成测试 — 鉴权完整流程: 注册 → 登录 → 刷新 → 登出 → 撤销

依赖 conftest: db_session, api_client (httpx AsyncClient)
注意: 注册/登录/刷新 是真实路由, 但 TestClient 不需要真 uvicorn 进程。
"""
from __future__ import annotations

import pytest


# ============================================================
#  Auth 流程 (走真实 HTTP 路由 + SQLite 内存库会话)
# ============================================================

class TestAuthFlow:
    HEADERS = {"Content-Type": "application/json"}
    PREFIX = "/api/v1/auth"

    async def _unique_user(self, suffix=""):
        import asyncio
        ts = int(asyncio.get_event_loop().time() * 1000) % 10000000
        return {
            "username": f"it_flow_{ts}{suffix}",
            "email": f"it_flow_{ts}{suffix}@example.com",
            "password": "Test123456",
            "confirm_password": "Test123456",
        }

    async def test_register_ok_returns_dual_tokens(self, api_client):
        payload = await self._unique_user("a")
        r = await api_client.post(f"{self.PREFIX}/register", json=payload, headers=self.HEADERS)
        assert r.status_code == 200, r.text
        data = r.json().get("data") or {}
        assert data.get("access_token") and data.get("refresh_token")
        assert data.get("token_type") == "bearer"
        assert data.get("user", {}).get("username") == payload["username"]

    async def test_register_duplicate_username_409(self, api_client):
        """相同用户名二次注册返回 409 Conflict"""
        payload = await self._unique_user("dup")
        r1 = await api_client.post(f"{self.PREFIX}/register", json=payload, headers=self.HEADERS)
        assert r1.status_code == 200, r1.text
        r2 = await api_client.post(f"{self.PREFIX}/register", json=payload, headers=self.HEADERS)
        assert r2.status_code == 409

    async def test_login_ok(self, api_client):
        payload = await self._unique_user("b")
        await api_client.post(f"{self.PREFIX}/register", json=payload, headers=self.HEADERS)
        r = await api_client.post(f"{self.PREFIX}/login", json={
            "username": payload["username"],
            "password": payload["password"],
        }, headers=self.HEADERS)
        assert r.status_code == 200
        data = r.json().get("data") or {}
        assert data.get("access_token") and data.get("refresh_token")

    async def test_login_wrong_password_401(self, api_client):
        payload = await self._unique_user("c")
        await api_client.post(f"{self.PREFIX}/register", json=payload, headers=self.HEADERS)
        r = await api_client.post(f"{self.PREFIX}/login", json={
            "username": payload["username"],
            "password": "WrongPassword!@",
        }, headers=self.HEADERS)
        assert r.status_code == 401

    async def test_refresh_token_rotation(self, api_client):
        """刷新 token 后旧 refresh 立即失效"""
        payload = await self._unique_user("rot")
        reg = await api_client.post(f"{self.PREFIX}/register", json=payload, headers=self.HEADERS)
        refresh_old = (reg.json().get("data") or {}).get("refresh_token")
        assert refresh_old

        # 第一次刷新 → 200 给新 token
        r2 = await api_client.post(f"{self.PREFIX}/refresh", json={"refresh_token": refresh_old},
                                   headers=self.HEADERS)
        assert r2.status_code == 200, r2.text
        new_refresh = (r2.json().get("data") or {}).get("refresh_token")
        assert new_refresh and new_refresh != refresh_old

        # 旧 refresh 重用 → 401 (轮换生效)
        r3 = await api_client.post(f"{self.PREFIX}/refresh", json={"refresh_token": refresh_old},
                                   headers=self.HEADERS)
        assert r3.status_code == 401

    async def test_logout_then_me_401(self, api_client):
        """登出后 access token 被 Redis 黑名单吊销 → GET /auth/me 返回 401"""
        payload = await self._unique_user("logout")
        reg = await api_client.post(f"{self.PREFIX}/register", json=payload, headers=self.HEADERS)
        data = (reg.json().get("data") or {})
        access, refresh = data.get("access_token"), data.get("refresh_token")
        bearer = {"Authorization": f"Bearer {access}"}

        # 登出之前 /auth/me 应该 200
        me = await api_client.get(f"{self.PREFIX}/me", headers=bearer)
        assert me.status_code == 200, me.text

        lo = await api_client.post(f"{self.PREFIX}/logout",
                                   json={"refresh_token": refresh},
                                   headers={**bearer, **self.HEADERS})
        assert lo.status_code == 200, lo.text

        # 登出之后再访问 /auth/me → 401 (token 已撤销)
        me2 = await api_client.get(f"{self.PREFIX}/me", headers=bearer)
        assert me2.status_code == 401


# ============================================================
#  Auth 数据一致性: 直接访问 DB 验证 refresh_tokens 表
# ============================================================

class TestAuthDataConsistency:
    PREFIX = "/api/v1/auth"

    async def test_register_persists_refresh_token_row(self, db_session, api_client):
        """注册后 refresh_tokens 表里应该有 1 行"""
        payload = {
            "username": "dbcons_user",
            "email": "dbcons_user@example.com",
            "password": "Test123456",
            "confirm_password": "Test123456",
        }
        r = await api_client.post(f"{self.PREFIX}/register", json=payload,
                                  headers={"Content-Type": "application/json"})
        assert r.status_code == 200
        from sqlalchemy import text
        rows = (await db_session.execute(text("SELECT COUNT(*) FROM refresh_tokens"))).scalar()
        assert rows >= 1, "注册后 refresh_tokens 至少有 1 行"

    async def test_refresh_token_revoked_after_rotation(self, db_session, api_client):
        """刷新后旧 token 的 revoked_at 应已更新"""
        from sqlalchemy import text
        payload = {
            "username": "revoke_check_user",
            "email": "revoke_user@example.com",
            "password": "Test123456",
            "confirm_password": "Test123456",
        }
        reg = await api_client.post(f"{self.PREFIX}/register", json=payload,
                                    headers={"Content-Type": "application/json"})
        refresh_old = (reg.json().get("data") or {}).get("refresh_token")
        await api_client.post(f"{self.PREFIX}/refresh",
                              json={"refresh_token": refresh_old},
                              headers={"Content-Type": "application/json"})

        # revoked_at 字段 IS NOT NULL 计数 ≥1
        row = (await db_session.execute(text(
            "SELECT COUNT(*) FROM refresh_tokens WHERE revoked_at IS NOT NULL"
        ))).scalar()
        assert row >= 1, "refresh token 未被标记 revoked"
