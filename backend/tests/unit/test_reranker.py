"""B1 · 单元测试 — 重排器: BaseReranker / KeywordReranker / CrossEncoder 回退

对应模块: app.processors.retrieval.reranker
纯逻辑无外部依赖。CrossEncoderReranker 在 sentence-transformers 未装或模型未加载时的降级行为也在这里测。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.processors.retrieval.reranker import (  # noqa: E402
    KeywordReranker,
    CrossEncoderReranker,
    BaseReranker,
)


SAMPLE = [
    {"chunk_id": 1, "content": "PostgreSQL 的 pgvector 支持向量检索和 HNSW 索引."},
    {"chunk_id": 2, "content": "MySQL 是关系型数据库, 不擅长向量检索."},
    {"chunk_id": 3, "content": "Redis 常用于缓存和限流, 不是向量数据库."},
    {"chunk_id": 4, "content": "pgvector 的 ivfflat 索引在向量数较大时比 HNSW 省内存."},
    {"chunk_id": 5, "content": "FastAPI 是现代 Python 异步 web 框架."},
]


class TestBaseReranker:
    def test_subclass_requires_rerank_method(self):
        # BaseReranker 没加 ABCMeta, 因此直接实例化不抛 TypeError — 这是项目的既定 API 契约.
        # 我们改为断言基类的 rerank() 抛 NotImplementedError (或返回 NotImplemented 合理替代)
        b = BaseReranker()
        with pytest.raises(NotImplementedError):
            # 直接调用基类 rerank — 应该抛 NotImplementedError (因为基类 rerank 默认没有真正实现)
            import asyncio
            asyncio.get_event_loop().run_until_complete(b.rerank("q", []))


class TestKeywordReranker:
    @pytest.fixture
    def rr(self):
        return KeywordReranker()

    @pytest.mark.asyncio
    async def test_empty_documents(self, rr):
        assert await rr.rerank("x", [], top_k=5) == []

    @pytest.mark.asyncio
    async def test_pgvector_keywords(self, rr):
        results = await rr.rerank("PostgreSQL pgvector HNSW 向量检索", SAMPLE, top_k=3)
        assert len(results) == 3
        # 含 "pgvector" 且有 "向量检索" 的 1/4 应该靠前
        top_ids = [r["chunk_id"] for r in results]
        assert top_ids[0] in (1, 4)

    @pytest.mark.asyncio
    async def test_sequential_rank(self, rr):
        results = await rr.rerank("Redis 缓存 限流", SAMPLE, top_k=5)
        for i, r in enumerate(results, 1):
            assert r["rerank_rank"] == i
            assert r.get("rerank_score") is not None

    @pytest.mark.asyncio
    async def test_top_k_exceeds_size(self, rr):
        r = await rr.rerank("anything", SAMPLE, top_k=999)
        assert len(r) == len(SAMPLE)


class TestCrossEncoderReranker:
    def test_create_without_model_should_not_import_error(self):
        # CrossEncoderReranker 懒加载模型, 只需要 3 个合法参数: model_name / max_length / device
        ce = CrossEncoderReranker(
            model_name="unit-test-fake-name",
            max_length=128,
            device="cpu",
        )
        assert ce is not None
        assert ce.model_name == "unit-test-fake-name"

    @pytest.mark.asyncio
    async def test_fallback_when_model_not_loaded(self):
        """首次 rerank 会尝试加载 sentence-transformers，失败时的行为 (抛 ImportError 或 RuntimeError 都算符合契约)。"""
        ce = CrossEncoderReranker(
            model_name="no-such-unit-model",
            max_length=128,
            device="cpu",
        )
        # _ensure_model 内部可能因为没装 sentence-transformers、模型名不存在两种情况之一报错;
        # 但不管怎样, rerank() 都不应该出现 TypeError / AttributeError / ValueError 这类代码 bug.
        try:
            results = await ce.rerank("PostgreSQL pgvector", SAMPLE, top_k=2)
        except ImportError:
            # 没装 sentence-transformers: 合法契约
            return
        except RuntimeError:
            # 模型加载失败: 合法契约
            return
        # 如果环境真有 sentence-transformers 并加载成功了, 则断言返回 2 条
        assert len(results) == 2
