"""
集成测试模板
命名约定: test_<链路>.py，放在 tests/integration/ 下
- 跨 2+ 个模块，真实走 DB + Service + 中间件（Redis/LLM 依然 Mock）
- 按 Given-When-Then 描述业务场景
"""
import pytest


pytestmark = pytest.mark.asyncio


class TestXxxFlow:
    """链路: A → B → C"""

    async def test_xxx_full_flow(self, db_session, api_client, test_user, test_kb, test_document):
        # Given — 准备夹具（conftest 已经注入，直接用）
        headers = test_user.auth_headers

        # When — 执行链路
        r1 = await api_client.get(f"/api/v1/knowledge-bases/{test_kb.id}", headers=headers)
        # 可以继续调用下一个接口：
        # r2 = await api_client.post("/api/v1/chat/message", headers=headers, json={...})

        # Then — 断言返回 AND 副作用
        assert r1.status_code == 200
        body = r1.json()
        assert body["id"] == test_kb.id
        assert body["owner_id"] == test_user.id
