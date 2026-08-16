"""B3 · API 契约测试 — KnowledgeBase CRUD + 权限

实际路由 (由 knowledge_base.py 的 APIRouter(prefix="/knowledge-bases")):
    POST   /api/v1/knowledge-bases           创建
    GET    /api/v1/knowledge-bases           列表 (分页)
    GET    /api/v1/knowledge-bases/{kb_id}   详情
    PUT    /api/v1/knowledge-bases/{kb_id}   更新
    DELETE /api/v1/knowledge-bases/{kb_id}   删除
"""
from __future__ import annotations

import pytest

KB_PREFIX = "/api/v1/knowledge-bases"


class TestKBCreate:
    async def test_create_kb_requires_auth(self, api_client):
        """未登录 → 401"""
        r = await api_client.post(KB_PREFIX, json={"name": "x"})
        assert r.status_code in (401, 403), r.text

    async def test_create_kb_success(self, api_client, test_user):
        r = await api_client.post(
            KB_PREFIX,
            json={"name": "it-api-kb-1", "description": "d"},
            headers=test_user.auth_headers,
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload.get("success") is True
        data = payload.get("data") or {}
        assert data.get("id") and data.get("name") == "it-api-kb-1"

    async def test_create_kb_empty_name_422(self, api_client, test_user):
        """name 长度 0 → Pydantic 校验 422"""
        r = await api_client.post(KB_PREFIX, json={"name": ""},
                                  headers=test_user.auth_headers)
        # FastAPI Pydantic 校验失败是 422
        assert r.status_code == 422, r.status_code


class TestKBListAndDetail:
    async def test_list_my_kbs(self, api_client, test_user):
        # 先建一个
        await api_client.post(KB_PREFIX, json={"name": "api-list-1"},
                              headers=test_user.auth_headers)
        r = await api_client.get(KB_PREFIX, headers=test_user.auth_headers)
        assert r.status_code == 200
        d = r.json().get("data") or {}
        # 分页接口有两种: dict(items+total) 或 list — 都接受
        assert isinstance(d, (dict, list))
        if isinstance(d, dict):
            assert "items" in d or "list" in d

    async def test_get_kb_not_exists_soft_fail(self, api_client, test_user):
        # 不存在的 kb — 只要不抛 500 即可
        r = await api_client.get(f"{KB_PREFIX}/99999999", headers=test_user.auth_headers)
        assert r.status_code in (200, 404, 400), r.status_code


class TestKBDelete:
    async def test_delete_other_owner_forbidden(self, api_client, test_user, admin_user):
        """admin 创建的 kb, test_user 删不掉 (权限 403 或业务层返回 success=False)"""
        r = await api_client.post(KB_PREFIX,
                                  json={"name": "cross-delete"},
                                  headers=admin_user.auth_headers)
        kb_id = (r.json().get("data") or {}).get("id")
        assert kb_id, f"创建失败 {r.text}"

        d = await api_client.delete(f"{KB_PREFIX}/{kb_id}",
                                    headers=test_user.auth_headers)
        # 合法的三种契约:
        #   403 Forbidden / 404 Not Found (不暴露存在性) / 200+success=False
        assert d.status_code in (403, 200, 404), d.text
        if d.status_code == 200:
            body = d.json()
            assert (body.get("success") is False) or (
                body.get("message") and ("权限" in body.get("message", "")
                                         or "属主" in body.get("message", "")
                                         or "无权" in body.get("message", ""))
            )
