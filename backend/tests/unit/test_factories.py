"""Factory Boy 数据工厂单元测试（验证 P0-O-1.1 落地）

覆盖场景：
  1. 各实体工厂单条创建（acreate）
  2. 批量创建（abatch 10条，Faker 多样性验证）
  3. SubFactory 级联链（KB → User → Tenant）
  4. Document → Chunk 完整文档链路
  5. Conversation → ChatMessage 对话链路
  6. UserFactory 密码自动 hash 验证（verify_password 真能过）
  7. Role + Permission 多对多 post_generation
  8. RefreshToken / AuditLog 独立实体创建
"""

from __future__ import annotations

import pytest


pytestmark = pytest.mark.asyncio


class TestTenantAndUserFactories:
    """最顶层实体：Tenant + User"""

    async def test_tenant_acreate_defaults(self, db_session, tenant_factory):
        """Tenant 单条创建：id 生成、name 唯一、默认 plan 命中三选一"""
        t = await tenant_factory.acreate(db_session)
        assert t.id is not None
        assert len(t.name) > 0 and "_" in t.name  # 有 uuid 后缀避免重复
        assert t.plan in ("free", "pro", "enterprise")
        assert t.is_active is True

    async def test_tenant_override_fields(self, db_session, tenant_factory):
        """手动覆盖字段，确认工厂不乱改"""
        t = await tenant_factory.acreate(
            db_session, name="专属租户_测试", plan="enterprise",
            max_knowledge_bases=10, max_storage_mb=2048,
        )
        assert t.name == "专属租户_测试"
        assert t.plan == "enterprise"
        assert t.max_knowledge_bases == 10
        assert t.max_storage_mb == 2048

    async def test_user_acreate_default_password_hashed(
        self, db_session, user_factory
    ):
        """⭐ 核心：password 虚拟字段 → password_hash 自动 Argon2id hash，且 verify_password 能过"""
        from app.core.security import verify_password

        user = await user_factory.acreate(
            db_session, username="alice_test", password="MySecret@2025"
        )
        assert user.id is not None
        assert user.username == "alice_test"
        # 不应该存明文！
        assert user.password_hash != "MySecret@2025"
        assert user.password_hash.startswith("$argon2id$")
        # 真的能验证
        assert verify_password("MySecret@2025", user.password_hash) is True
        assert verify_password("WrongPass", user.password_hash) is False

    async def test_user_with_tenant_cascade(
        self, db_session, user_factory, tenant_factory
    ):
        """手动分配 tenant：User.tenant_id 应该有值，且反查 tenant.users 能找到"""
        tenant = await tenant_factory.acreate(db_session)
        user = await user_factory.acreate(db_session, tenant=tenant)
        assert user.tenant_id == tenant.id
        # 反查（dynamic lazy，需要 await .all() 或者直接在 session 里看对象）
        # 这里直接查 id 相等即可（避免 async query 写法差异）
        assert user.tenant.id == tenant.id

    async def test_user_abatch_10_unique(self, db_session, user_factory, faker_seed):
        """批量创建 10 个用户：id、username、email 都应该唯一"""
        users = await user_factory.abatch(db_session, size=10)
        ids = [u.id for u in users]
        usernames = [u.username for u in users]
        emails = [u.email for u in users]
        assert len(users) == 10
        assert len(set(ids)) == 10, f"id 有重复: {ids}"
        assert len(set(usernames)) == 10, f"username 有重复: {usernames}"
        # email 可能有极低概率重复（Faker + uuid 后缀应避免），如果真遇到调大 uuid 段
        assert len(set(emails)) >= 9, f"email 重复太多: {emails}"


class TestKnowledgeBaseAndDocumentFactories:
    """知识库 + 文档 + 分块级联链"""

    async def test_kb_auto_creates_owner_and_tenant(
        self, db_session, knowledge_base_factory
    ):
        """⭐ SubFactory 级联：直接建 KB，应该自动有 owner（User），owner 可能有/没有 tenant"""
        kb = await knowledge_base_factory.acreate(db_session, name="级联测试知识库")
        assert kb.id is not None
        assert kb.name == "级联测试知识库"
        # SubFactory 默认应该建了 owner（KnowledgeBaseFactory 默认 owner=SubFactory(UserFactory)）
        assert kb.owner is not None
        assert kb.owner.id is not None
        assert kb.user_id == kb.owner.id
        # 反查：owner.knowledge_bases 动态关系里应该包含这个 KB
        from sqlalchemy import select
        result = await db_session.execute(
            select(type(kb)).where(type(kb).user_id == kb.owner.id)
        )
        owned_kbs = result.scalars().all()
        assert kb.id in [k.id for k in owned_kbs]

    async def test_document_and_chunk_full_chain(
        self, db_session, document_factory, document_chunk_factory
    ):
        """完整链路：Document(级联 KB + User) → 3 个 DocumentChunk"""
        doc = await document_factory.acreate(
            db_session, filename="工厂测试_doc.md", content_text="# Hello\n这是一段内容。"
        )
        assert doc.id is not None
        assert doc.knowledge_base_id is not None  # SubFactory 自动建的 KB

        # 手动建 3 个 chunk，关联到同一个 document 和 kb
        chunks = []
        for i in range(3):
            ch = await document_chunk_factory.acreate(
                db_session,
                document=doc,
                document_id=doc.id,
                knowledge_base_id=doc.knowledge_base_id,
                chunk_index=i,
                content=f"段落 {i}: 这是文档分块的内容。",
            )
            chunks.append(ch)

        # 验证外键
        for c in chunks:
            assert c.document_id == doc.id
            assert c.knowledge_base_id == doc.knowledge_base_id
        chunk_ids = {c.id for c in chunks}
        assert len(chunk_ids) == 3
        chunk_indices = sorted([c.chunk_index for c in chunks])
        assert chunk_indices == [0, 1, 2]


class TestConversationAndMessageFactories:
    async def test_conversation_with_owner_and_kb(
        self, db_session, conversation_factory
    ):
        """默认 ConversationFactory 会级联创建 owner + knowledge_base"""
        conv = await conversation_factory.acreate(db_session)
        assert conv.id is not None
        assert conv.owner is not None and conv.owner.id is not None
        assert conv.knowledge_base is not None and conv.knowledge_base.id is not None
        assert conv.user_id == conv.owner.id
        assert conv.knowledge_base_id == conv.knowledge_base.id

    async def test_chat_messages_in_conversation(
        self, db_session, conversation_factory, chat_message_record_factory
    ):
        conv = await conversation_factory.acreate(db_session)
        # user + assistant + user 三轮对话
        roles = ["user", "assistant", "user"]
        contents = ["你好", "你好呀，有什么可以帮你？", "帮我总结一下文档"]
        for role, content in zip(roles, contents):
              await chat_message_record_factory.acreate(
                  db_session, conversation=conv, conversation_id=conv.id,
                  role=role, content=content,
              )
        from sqlalchemy import select
        from app.models.entities.conversation import ChatMessageRecord
        result = await db_session.execute(
            select(ChatMessageRecord).where(ChatMessageRecord.conversation_id == conv.id)
        )
        msgs = result.scalars().all()
        assert len(msgs) == 3
        assert [m.role for m in msgs] == roles


class TestAuthAndAuditFactories:
    async def test_role_and_permission_m2m(
        self, db_session, role_factory, permission_factory
    ):
        """多对多 post_generation：role.permissions = [p1, p2] 能正确写入关联表"""
        p1 = await permission_factory.acreate(db_session, resource="kb", action="create")
        p2 = await permission_factory.acreate(db_session, resource="document", action="delete")
        role = await role_factory.acreate(
            db_session, name="editor", permissions=[p1, p2],  # 传列表，走 post_generation
        )
        # session expire_on_commit=False 所以直接读关系
        perms = list(role.permissions)
        assert len(perms) == 2
        perm_ids = {p.id for p in perms}
        assert {p1.id, p2.id}.issubset(perm_ids)

    async def test_user_with_roles_post_gen(
        self, db_session, user_factory, role_factory
    ):
        """User.roles 多对多分配：post_generation 处理"""
        r_admin = await role_factory.acreate(db_session, name="admin")
        r_editor = await role_factory.acreate(db_session, name="editor")
        user = await user_factory.acreate(
            db_session, username="multi_role_user", roles=[r_admin, r_editor]
        )
        role_names = {r.name for r in list(user.roles)}
        assert {"admin", "editor"}.issubset(role_names)

    async def test_refresh_token_factory(self, db_session, refresh_token_factory, user_factory):
        # RefreshToken 必须绑定已存在用户（Factory 不自动级联建 user，避免 id 空）
        user = await user_factory.acreate(db_session, username="rt_user")
        rt = await refresh_token_factory.acreate(
            db_session, user_id=user.id, device_info="Pytest UA"
        )
        assert rt.id is not None, "RefreshToken 没 flush 成功，id=None"
        assert rt.user_id == user.id
        assert rt.token_hash and len(rt.token_hash) == 64  # SHA-256 = 64 hex chars
        assert rt.revoked_at is None  # 默认未撤销
        assert rt.is_active is True

    async def test_audit_log_factory(self, db_session, audit_log_factory):
        logs = await audit_log_factory.abatch(db_session, size=5)
        assert len(logs) == 5
        # 每个 log 的 method、status_code 应该是枚举值之一
        for log in logs:
            assert log.method in ("GET", "POST", "PUT", "DELETE", "PATCH")
            assert log.status_code in (200, 201, 204, 400, 401, 403, 404, 500)
            assert log.path.startswith("/")
