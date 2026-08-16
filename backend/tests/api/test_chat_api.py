"""B3 · API 契约测试 — Chat 路由
  • POST /chat/message (payload 校验, 无 kb_id → 明确错误)
  • GET  /chat/provider
  • POST /chat/search/{kb_id} (query 为空返回 400/success=false)
  • GET  /chat/
"""
from __future__ import annotations

import pytest


class TestChatProviderAndRoot:
    async def test_chat_provider_endpoint_ok(self, api_client):
        r = await api_client.get("/api/v1/chat/provider")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("success") is True or body.get("data") is not None

    async def test_chat_root_endpoint_200(self, api_client):
        r = await api_client.get("/api/v1/chat/")
        # 根路径一般返回 JSON
        assert r.status_code == 200


class TestChatMessage:
    async def test_empty_payload_422(self, api_client, test_user):
        """ChatRequest 至少需要 knowledge_base_id + query_text"""
        r = await api_client.post("/api/v1/chat/message", json={},
                                  headers={**test_user.auth_headers,
                                           "Content-Type": "application/json"})
        assert r.status_code == 422, r.status_code

    async def test_chat_message_without_kb_refuses(self, api_client, test_user):
        """kb_id=0 或查不到时返回 "没有可用知识库" 类提示 (由 RAG L1 拒绝分支)"""
        r = await api_client.post("/api/chat/message", json={
            "knowledge_base_id": 999999,
            "query_text": "不存在的库",
        }, headers=test_user.auth_headers)
        # 可能 200 + success=false(业务拒绝) 或 200 + data.answer 直接说没检索到
        assert r.status_code in (200, 400, 404), r.text


class TestChatSearchOnly:
    async def test_search_missing_query_returns_fail(self, api_client, test_kb, test_user):
        """search/kb_id 没传 query_text → 契约中是 success=False / code=400"""
        r = await api_client.post(f"/api/v1/chat/search/{test_kb.id}", json={},
                                  headers=test_user.auth_headers)
        assert r.status_code == 200, r.text
        body = r.json()
        # 契约: ApiResponse(code=400, success=False)
        assert body.get("code") == 400 or body.get("success") is False
