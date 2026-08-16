"""B2 · 集成测试 — RAG 检索 + LLM 生成链路

为什么不走真实向量文件?
  - EmbeddingService 依赖 sentence-transformers (重模型)
  - VectorStoreManager 会写 VECTOR_STORE_DIR, 在 CI 上不具备可重复性
所以 RAGPipeline 测试的重点是 **build_context / build_messages** + **幻觉抑制 L1/L2** (纯逻辑)。
而端到端 chat_message 路由在 B3 api/ 层测试。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.chat_service import RAGPipeline, RAGPipelineResult  # noqa: E402
from app.services.kb_service import KnowledgeBaseService  # noqa: E402
from app.models.schemas.kb_schemas import KnowledgeBaseCreate  # noqa: E402


class TestKnowledgeBaseCRUD:
    """纯 DB 跨模块：验证业务服务 + 数据实体 (SQLite 内存)"""

    async def test_create_and_list(self, db_session, test_user):
        svc = KnowledgeBaseService(db_session)
        payload = KnowledgeBaseCreate(name="IT KB 1", description="来自集成测试",
                                      chunk_size=200, chunk_overlap=20)
        kb = await svc.create(payload, user_id=test_user.id)
        assert kb.id and kb.user_id == test_user.id

        items, total = await svc.list(user_id=test_user.id, page=1, page_size=10)
        assert total >= 1
        assert any(x.id == kb.id for x in items)

    async def test_delete_owner_only(self, db_session, test_user, admin_user):
        """另一个用户 (admin) 不能删除 test_user 的 KB"""
        svc = KnowledgeBaseService(db_session)
        kb = await svc.create(KnowledgeBaseCreate(name="del-o"), user_id=test_user.id)
        deleted_by_admin = await svc.delete(kb.id, user_id=admin_user.id)
        assert deleted_by_admin is False

        deleted_by_owner = await svc.delete(kb.id, user_id=test_user.id)
        assert deleted_by_owner is True


class TestRAGPipelineLogic:
    """只测 RAGPipeline 的纯函数部分: build_context / build_messages / hallucination-suppression"""

    @pytest.fixture
    def pipeline(self, monkeypatch):
        """实例化 pipeline 但把真 Embedding 换成 mock (不用真 sentence-transformers)"""
        # RAGPipeline.__init__ 会 new EmbeddingService() 和 VectorStoreManager(), 这里只 patch 它们的副作用
        from app.processors.llm.llm_service import LLMService, MockLLMProvider
        svc = RAGPipeline.__new__(RAGPipeline)
        svc.llm = type(
            "_FakeLLM", (),
            {"provider_name": lambda *a, **kw: "mock"}
        )()
        return svc

    def test_build_context_empty_chunks(self, pipeline):
        from app.services.chat_service import RetrievedChunk  # noqa: F401 实际不用, 仅说明 import 位置
        ctx = pipeline.build_context([])
        assert "知识库中无相关内容" in ctx

    def test_build_context_joins_chunks(self, pipeline):
        from app.services.chat_service import RetrievedChunk
        chunks = [
            RetrievedChunk(chunk_id=1, content="内容 A 片段 1", score=0.92,
                           document_id=10, document_filename="doc-a.md"),
            RetrievedChunk(chunk_id=2, content="内容 B 片段 2", score=0.81,
                           document_id=11, document_filename="doc-b.md"),
        ]
        ctx = pipeline.build_context(chunks, max_chars=99999)
        assert "doc-a.md" in ctx
        assert "doc-b.md" in ctx
        assert "[#1]" in ctx and "[#2]" in ctx

    def test_build_messages_includes_system_and_query(self, pipeline):
        msgs = pipeline.build_messages("用户问题", "上下文长文本")
        # 至少 1 system + 1 user
        assert len(msgs) >= 2
        assert msgs[0].role == "system"
        assert msgs[-1].role == "user"
        assert "用户问题" in msgs[-1].content
        assert "上下文长文本" in msgs[0].content or any("上下文长文本" in m.content for m in msgs)

    def test_hallucination_suppression_layer1_empty_chunks(self):
        """当知识库完全空时，RAGPipeline.answer() 直接拒绝回答 (不调用 LLM)

        实现原理: chat_service RAGPipeline.search() 当 store.total() == 0 时返回 [];
        chat() 发现无 chunks → 直接写"检索不到足够相关信息"。
        这里我们只断言 RAGPipeline.build_messages 不会在无 context 时
        给 LLM 喂一些容易幻觉的空白内容。
        """
        from app.services.chat_service import RAGPipeline
        from unittest.mock import MagicMock

        p = RAGPipeline.__new__(RAGPipeline)
        # 空 chunks 时 build_context → 明确的拒答模板
        ctx = p.build_context([], max_chars=99999)
        assert "未检索到相关内容" in ctx or "无相关内容" in ctx
