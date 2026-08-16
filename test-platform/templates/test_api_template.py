"""
HTTP API 契约测试模板
命名约定: test_<资源>_api.py，放在 tests/api/ 下
- 通过 api_client (httpx.AsyncClient + ASGITransport) 发请求
- 路由前缀固定 /api/v1/xxx
- 必测 4 个维度：鉴权 / 合法2xx / 非法422 / 非属主403 or 404(防IDOR)
"""
import pytest


pytestmark = pytest.mark.asyncio


class TestXxxApi:
    """API 套件: /api/v1/xxx"""

    URL = "/api/v1/xxx"  # TODO: 填真实路由

    async def test_xxx_create_requires_auth(self, api_client):
        """未登录写接口 → 401/403"""
        r = await api_client.post(self.URL, json={"name": "x"})
        assert r.status_code in (401, 403)

    async def test_xxx_create_ok(self, api_client, test_user):
        """登录 → 201/200 + 返回 id"""
        r = await api_client.post(
            self.URL, json={"name": "合法名称"}, headers=test_user.auth_headers
        )
        assert r.status_code in (200, 201)
        assert "id" in r.json()

    async def test_xxx_create_invalid_payload_422(self, api_client, test_user):
        """空字段 → Pydantic 422（不能是 500）"""
        r = await api_client.post(
            self.URL, json={"name": ""}, headers=test_user.auth_headers
        )
        assert r.status_code == 422

    async def test_xxx_delete_not_owner_returns_404(self, api_client, test_user, admin_user):
        """非属主删除 → 404（防枚举 IDOR）"""
        # admin 建一个 KB 再让 test_user 删
        create_resp = await api_client.post(
            self.URL, json={"name": "owner-is-admin"}, headers=admin_user.auth_headers
        )
        obj_id = create_resp.json()["id"]
        r = await api_client.delete(f"{self.URL}/{obj_id}", headers=test_user.auth_headers)
        assert r.status_code == 404
