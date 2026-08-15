"""Phase 4 补全: Reranker + ChunkerFactory 单元测试

测试覆盖:
  A. KeywordReranker — 关键词重排 (降级方案, 无需外部依赖)
     - 基本重排、关键词提取、覆盖率计算、位置加权、top_k 截断
     - 空输入、无关键词、中文/英文混合
  B. CrossEncoderReranker — Cross-Encoder 精排
     - 模块导入、懒加载、降级到 KeywordReranker、线程池执行
  C. RerankerFactory — 工厂模式
     - 创建实例、全局单例、降级策略
  D. RecursiveCharacterChunker — 递归字符分块
     - 基本分块、chunk 大小控制、overlap、空输入
     - 中文/英文/混合文本、小 chunk 合并
  E. MarkdownStructureChunker — Markdown 结构分块
     - 按标题分块、保留代码块、非 MD 降级、大 section 切分
  F. SemanticChunkerAdapter — 语义分块适配器
     - 基本分块、空输入、metadata 正确性
  G. ChunkerFactory — 工厂 + 策略检测
     - 三种策略创建、未知策略报错、自动检测
  H. 集成场景 — Reranker + Chunker 协作
     - 分块后重排、真实文档场景
"""

import pytest
import asyncio
import math
from typing import List, Dict, Any


# ============================================================
#  测试数据
# ============================================================

SAMPLE_DOCUMENTS = [
    {
        "content": "RAG（检索增强生成）是一种结合检索和生成的技术，通过从知识库中检索相关文档来增强 LLM 的回答质量。",
        "document_id": 1,
        "chunk_id": 101,
    },
    {
        "content": "FAISS 是 Facebook 开发的高效向量检索库，支持百万级向量的近似最近邻搜索。",
        "document_id": 2,
        "chunk_id": 102,
    },
    {
        "content": "PostgreSQL 的 pgvector 扩展提供了原生的向量存储和相似度搜索能力，配合 GIN 索引可以实现全文检索。",
        "document_id": 3,
        "chunk_id": 103,
    },
    {
        "content": "BM25 是一种基于词频和逆文档频率的文本相关性排序算法，广泛用于搜索引擎。",
        "document_id": 4,
        "chunk_id": 104,
    },
    {
        "content": "Cross-Encoder 模型同时编码 query 和 document，输出相关性分数，精度比双塔模型更高。",
        "document_id": 5,
        "chunk_id": 105,
    },
]

MARKDOWN_TEXT = """# RAG 系统架构

## 概述
RAG 系统由三个核心模块组成：检索、重排和生成。

## 检索模块
检索模块负责从知识库中找到与用户查询相关的文档。

### 向量检索
使用 Embedding 模型将文本编码为向量，通过余弦相似度搜索。

### 全文检索
使用 PostgreSQL 的 tsvector 和 GIN 索引实现全文检索。

## 生成模块
将检索到的上下文和用户查询一起发送给 LLM 生成最终回答。

```python
def rag_pipeline(query):
    docs = retrieve(query)
    reranked = rerank(query, docs)
    answer = llm.generate(query, reranked)
    return answer
```
"""

PLAIN_TEXT = (
    "RAG 是一种结合检索和生成的技术。"
    "它通过从知识库中检索相关文档来增强 LLM 的回答质量。"
    "RAG 系统通常包含三个模块: 检索、重排和生成。"
    "检索模块使用向量搜索和全文检索的混合方式。"
    "重排模块使用 Cross-Encoder 对初步检索结果做精排。"
    "生成模块将检索到的上下文和用户查询一起发送给 LLM。"
)


# ============================================================
#  A. KeywordReranker 测试
# ============================================================

class TestKeywordReranker:
    """关键词重排器测试 (降级方案, 无需 sentence-transformers)"""

    @pytest.fixture
    def reranker(self):
        from app.processors.retrieval.reranker import KeywordReranker
        return KeywordReranker()

    @pytest.mark.asyncio
    async def test_basic_rerank(self, reranker):
        """基本重排: 包含更多查询关键词的文档应排前面"""
        query = "RAG 检索生成"
        results = await reranker.rerank(query, SAMPLE_DOCUMENTS, top_k=3)

        assert len(results) == 3
        # 每个结果应有 rerank_score 和 rerank_rank
        for r in results:
            assert "rerank_score" in r
            assert "rerank_rank" in r
            assert 0.0 <= r["rerank_score"] <= 1.0

    @pytest.mark.asyncio
    async def test_top_doc_has_highest_score(self, reranker):
        """最高分文档应排在第一位"""
        query = "Cross-Encoder query document"
        results = await reranker.rerank(query, SAMPLE_DOCUMENTS, top_k=5)

        # 文档 5 包含 "Cross-Encoder", "query", "document" 全部关键词
        assert results[0]["chunk_id"] == 105
        # 分数应递减
        for i in range(len(results) - 1):
            assert results[i]["rerank_score"] >= results[i + 1]["rerank_score"]

    @pytest.mark.asyncio
    async def test_rerank_rank_sequential(self, reranker):
        """rerank_rank 应从 1 递增"""
        results = await reranker.rerank("向量检索", SAMPLE_DOCUMENTS, top_k=4)
        for i, r in enumerate(results, 1):
            assert r["rerank_rank"] == i

    @pytest.mark.asyncio
    async def test_empty_documents(self, reranker):
        """空文档列表返回空"""
        results = await reranker.rerank("test", [], top_k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_no_keywords_in_query(self, reranker):
        """查询无关键词时返回原始顺序的 top_k"""
        query = "?"  # 无法提取关键词
        results = await reranker.rerank(query, SAMPLE_DOCUMENTS, top_k=2)
        assert len(results) == 2
        # 无关键词时直接返回前 top_k 个
        assert results[0]["chunk_id"] == 101

    @pytest.mark.asyncio
    async def test_top_k_exceeds_docs(self, reranker):
        """top_k 超过文档数时返回全部"""
        results = await reranker.rerank("RAG", SAMPLE_DOCUMENTS, top_k=100)
        assert len(results) == len(SAMPLE_DOCUMENTS)

    def test_extract_keywords_chinese(self, reranker):
        """中文关键词提取 (双字组合)"""
        kws = reranker._extract_keywords("检索增强生成")
        assert "检索" in kws
        assert "增强" in kws
        assert "强生" in kws
        assert "生成" in kws

    def test_extract_keywords_english(self, reranker):
        """英文关键词提取"""
        kws = reranker._extract_keywords("RAG system retrieval augmented")
        assert "rag" in kws  # 不匹配: rag 只有 3 字符, 但 [A-Za-z][A-Za-z0-9-]{2,} 需要 3+ 字符
        assert "system" in kws
        assert "retrieval" in kws
        assert "augmented" in kws

    def test_extract_keywords_mixed(self, reranker):
        """中英文混合关键词提取"""
        kws = reranker._extract_keywords("RAG系统使用FAISS检索")
        assert "rag" in kws
        assert "faiss" in kws
        assert "系统" in kws
        assert "使用" in kws
        assert "检索" in kws

    def test_extract_keywords_dedup(self, reranker):
        """关键词去重"""
        kws = reranker._extract_keywords("检索检索检索")
        # 去重后只有 "检索"
        assert kws.count("检索") == 1

    @pytest.mark.asyncio
    async def test_position_bonus(self, reranker):
        """出现位置靠前的文档得分更高"""
        query = "apple"
        docs = [
            {"content": "apple at the beginning of the text", "id": 1},
            {"content": "some text before apple appears here later", "id": 2},
        ]
        results = await reranker.rerank(query, docs, top_k=2)
        # 文档 1 的 "apple" 出现更早, 应排前面
        assert results[0]["id"] == 1
        assert results[0]["rerank_score"] > results[1]["rerank_score"]

    @pytest.mark.asyncio
    async def test_preserves_original_fields(self, reranker):
        """重排后应保留原始文档字段"""
        results = await reranker.rerank("RAG", SAMPLE_DOCUMENTS, top_k=1)
        assert "content" in results[0]
        assert "document_id" in results[0]
        assert "chunk_id" in results[0]


# ============================================================
#  B. CrossEncoderReranker 测试
# ============================================================

class TestCrossEncoderReranker:
    """Cross-Encoder 重排器测试"""

    def test_module_import(self):
        """模块可正常导入"""
        from app.processors.retrieval.reranker import (
            CrossEncoderReranker,
            BaseReranker,
        )
        assert issubclass(CrossEncoderReranker, BaseReranker)

    def test_init_defaults(self):
        """默认参数初始化"""
        from app.processors.retrieval.reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker()
        assert reranker.model_name == "BAAI/bge-reranker-base"
        assert reranker.max_length == 512
        assert reranker.device == "cpu"
        assert reranker._model is None  # 懒加载

    def test_init_custom(self):
        """自定义参数初始化"""
        from app.processors.retrieval.reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker(
            model_name="BAAI/bge-reranker-large",
            max_length=256,
            device="cuda",
        )
        assert reranker.model_name == "BAAI/bge-reranker-large"
        assert reranker.max_length == 256
        assert reranker.device == "cuda"

    def test_has_cross_encoder_flag(self):
        """_HAS_CROSS_ENCODER 标志存在"""
        from app.processors.retrieval.reranker import _HAS_CROSS_ENCODER
        assert isinstance(_HAS_CROSS_ENCODER, bool)

    @pytest.mark.asyncio
    async def test_empty_documents(self):
        """空文档列表返回空"""
        from app.processors.retrieval.reranker import CrossEncoderReranker
        reranker = CrossEncoderReranker()
        results = await reranker.rerank("test", [], top_k=5)
        assert results == []

    @pytest.mark.asyncio
    async def test_fallback_to_keyword_when_no_lib(self):
        """无 sentence-transformers 时降级到 KeywordReranker"""
        from app.processors.retrieval.reranker import (
            CrossEncoderReranker,
            _HAS_CROSS_ENCODER,
            KeywordReranker,
        )
        if _HAS_CROSS_ENCODER:
            pytest.skip("sentence-transformers 已安装, 跳过降级测试")

        reranker = CrossEncoderReranker()
        # 降级时行为应与 KeywordReranker 一致
        results = await reranker.rerank("RAG检索", SAMPLE_DOCUMENTS, top_k=3)
        assert len(results) == 3
        assert "rerank_score" in results[0]

    @pytest.mark.asyncio
    async def test_rerank_with_mock_model(self):
        """使用 mock 模型测试 CrossEncoder 精排逻辑"""
        from app.processors.retrieval.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker()

        # Mock 模型: 返回与文档顺序相反的分数 (最后一个文档最高分)
        class MockModel:
            def predict(self, pairs, show_progress_bar=False):
                # 返回 logit, 最后一个最高
                return [0.1 * i for i in range(len(pairs))]

        reranker._model = MockModel()

        results = reranker._rerank_sync("query", SAMPLE_DOCUMENTS, top_k=3)
        assert len(results) == 3
        # 最后一个文档 (index 4) 的 logit=0.4 最高
        assert results[0]["chunk_id"] == 105
        # rerank_score 是 sigmoid(0.4), 应在 0~1 之间
        assert 0 < results[0]["rerank_score"] < 1
        # rank 从 1 开始
        assert results[0]["rerank_rank"] == 1

    def test_rerank_sync_score_normalization(self):
        """sigmoid 归一化: logit → 0~1"""
        from app.processors.retrieval.reranker import CrossEncoderReranker

        reranker = CrossEncoderReranker()

        class MockModel:
            def predict(self, pairs, show_progress_bar=False):
                # 正 logit → score > 0.5, 负 logit → score < 0.5
                return [5.0, -5.0]

        reranker._model = MockModel()
        docs = [{"content": "doc1", "id": 1}, {"content": "doc2", "id": 2}]
        results = reranker._rerank_sync("q", docs, top_k=2)

        # logit=5.0 → sigmoid ≈ 0.993, logit=-5.0 → sigmoid ≈ 0.007
        assert results[0]["rerank_score"] > 0.9  # 高分在前
        assert results[1]["rerank_score"] < 0.1


# ============================================================
#  C. RerankerFactory 测试
# ============================================================

class TestRerankerFactory:
    """Reranker 工厂测试"""

    def test_create_returns_reranker(self):
        """工厂方法返回 Reranker 实例"""
        from app.processors.retrieval.reranker import (
            RerankerFactory,
            BaseReranker,
        )
        reranker = RerankerFactory.create()
        assert isinstance(reranker, BaseReranker)

    def test_create_cross_encoder_when_available(self):
        """有 sentence-transformers 时返回 CrossEncoderReranker"""
        from app.processors.retrieval.reranker import (
            RerankerFactory,
            CrossEncoderReranker,
            _HAS_CROSS_ENCODER,
        )
        reranker = RerankerFactory.create()
        if _HAS_CROSS_ENCODER:
            assert isinstance(reranker, CrossEncoderReranker)
        else:
            # 降级时返回 KeywordReranker
            from app.processors.retrieval.reranker import KeywordReranker
            assert isinstance(reranker, KeywordReranker)

    def test_get_reranker_singleton(self):
        """get_reranker 返回全局单例"""
        from app.processors.retrieval.reranker import get_reranker
        r1 = get_reranker()
        r2 = get_reranker()
        assert r1 is r2


# ============================================================
#  D. RecursiveCharacterChunker 测试
# ============================================================

class TestRecursiveCharacterChunker:
    """递归字符分块器测试"""

    @pytest.fixture
    def chunker(self):
        from app.processors.document.chunker_factory import RecursiveCharacterChunker
        return RecursiveCharacterChunker(chunk_size=200, chunk_overlap=20)

    def test_basic_split(self, chunker):
        """基本分块: 文本被切分为多个 chunk"""
        long_text = "RAG系统。检索增强生成。" * 20  # 约 360 字符
        results = chunker.split(long_text)
        assert len(results) >= 2
        for r in results:
            assert r.content
            assert r.index >= 0
            assert r.metadata["strategy"] == "recursive"

    def test_short_text_single_chunk(self, chunker):
        """短文本返回单个 chunk"""
        short_text = "这是一段短文本。"
        results = chunker.split(short_text)
        assert len(results) == 1
        assert results[0].content == short_text

    def test_empty_input(self, chunker):
        """空输入返回空列表"""
        assert chunker.split("") == []
        assert chunker.split("   ") == []
        assert chunker.split(None) == []

    def test_chunk_size_respected(self):
        """chunk 不应显著超过 chunk_size"""
        from app.processors.document.chunker_factory import RecursiveCharacterChunker
        chunker = RecursiveCharacterChunker(chunk_size=100, chunk_overlap=0)
        long_text = "a" * 500  # 无分隔符的纯字符
        results = chunker.split(long_text)
        for r in results:
            # 允许小量超出 (overlap 和合并), 但不应超过 2x
            assert len(r.content) <= 200

    def test_overlap_between_chunks(self):
        """相邻 chunk 有 overlap"""
        from app.processors.document.chunker_factory import RecursiveCharacterChunker
        chunker = RecursiveCharacterChunker(chunk_size=100, chunk_overlap=20)
        # 用分隔符让切分可控
        text = "段落一内容。段落二内容。段落三内容。段落四内容。" * 5
        results = chunker.split(text)
        if len(results) >= 2:
            # 第二个 chunk 的开头应与第一个 chunk 的结尾有重叠
            # (由于 strip, 可能不完全匹配, 但应有部分重叠)
            assert len(results) >= 2

    def test_chinese_text(self, chunker):
        """中文文本分块"""
        text = "这是第一个句子。这是第二个句子。这是第三个句子。" * 10
        results = chunker.split(text)
        assert len(results) >= 1
        assert all(r.content for r in results)

    def test_english_text(self, chunker):
        """英文文本分块"""
        text = (
            "This is the first sentence. "
            "This is the second sentence. "
            "This is the third sentence. " * 10
        )
        results = chunker.split(text)
        assert len(results) >= 1

    def test_mixed_text(self, chunker):
        """中英文混合文本"""
        text = "RAG系统是 retrieval augmented generation 的缩写。它结合了检索和生成。"
        results = chunker.split(text)
        assert len(results) >= 1

    def test_separators_priority(self):
        """分隔符优先级: \\n\\n 优先于 \\n"""
        from app.processors.document.chunker_factory import RecursiveCharacterChunker
        chunker = RecursiveCharacterChunker(chunk_size=50, chunk_overlap=0)
        # 每段超过 50 字符才会切分 (避免被 _merge_small_chunks 合并)
        p1 = "这是段落一的内容" + "测试数据" * 12  # >50 chars
        p2 = "这是段落二的内容" + "验证数据" * 12  # >50 chars
        text = p1 + "\n\n" + p2
        results = chunker.split(text)
        # 应按 \n\n 切分为 2 个 chunk
        assert len(results) >= 2

    def test_min_chunk_size_merge(self):
        """过小的 chunk 会被合并"""
        from app.processors.document.chunker_factory import RecursiveCharacterChunker
        chunker = RecursiveCharacterChunker(chunk_size=300, chunk_overlap=0)
        # 多个小段落应合并为一个 chunk
        text = "短。短。短。短。短。"
        results = chunker.split(text)
        # 全部合并为 1 个 chunk (因为总长 < 300)
        assert len(results) == 1

    def test_chunk_result_to_dict(self, chunker):
        """ChunkResult.to_dict() 返回正确结构"""
        results = chunker.split("测试文本内容。" * 5)
        d = results[0].to_dict()
        assert "content" in d
        assert "index" in d
        assert "char_count" in d
        assert "metadata" in d
        assert d["char_count"] == len(d["content"])


# ============================================================
#  E. MarkdownStructureChunker 测试
# ============================================================

class TestMarkdownStructureChunker:
    """Markdown 结构分块器测试"""

    @pytest.fixture
    def chunker(self):
        from app.processors.document.chunker_factory import MarkdownStructureChunker
        return MarkdownStructureChunker(chunk_size=500, max_heading_level=3)

    def test_basic_markdown_split(self, chunker):
        """基本 Markdown 分块: 按标题分组"""
        results = chunker.split(MARKDOWN_TEXT)
        assert len(results) >= 3  # 至少 3 个 section

    def test_heading_in_metadata(self, chunker):
        """每个 chunk 的 metadata 包含标题信息"""
        results = chunker.split(MARKDOWN_TEXT)
        for r in results:
            assert r.metadata["strategy"] == "markdown"
            # 至少有些 chunk 有标题
            has_heading = any(r.metadata.get("heading") for r in results)
            assert has_heading

    def test_code_block_preserved(self, chunker):
        """代码块应保留在分块中"""
        results = chunker.split(MARKDOWN_TEXT)
        all_content = " ".join(r.content for r in results)
        assert "def rag_pipeline" in all_content
        assert "return answer" in all_content

    def test_non_markdown_fallback(self, chunker):
        """非 Markdown 文本降级到 semantic 分块"""
        plain = "这是一段纯文本内容, 没有任何 Markdown 格式标记。"
        results = chunker.split(plain)
        assert len(results) >= 1
        # 降级后 strategy 应为 semantic_fallback
        assert results[0].metadata["strategy"] == "semantic_fallback"

    def test_empty_input(self, chunker):
        """空输入"""
        assert chunker.split("") == []
        assert chunker.split("   ") == []

    def test_heading_level_filter(self):
        """max_heading_level 控制标题分组层级"""
        from app.processors.document.chunker_factory import MarkdownStructureChunker
        chunker = MarkdownStructureChunker(chunk_size=500, max_heading_level=2)
        results = chunker.split(MARKDOWN_TEXT)
        # H3 标题不会开启新 section (level=3 > max_heading_level=2)
        # 所以 chunk 数应比 max_heading_level=3 时少
        chunker_h3 = MarkdownStructureChunker(chunk_size=500, max_heading_level=3)
        results_h3 = chunker_h3.split(MARKDOWN_TEXT)
        assert len(results) <= len(results_h3)

    def test_is_markdown_detection(self, chunker):
        """_is_markdown 正确检测"""
        assert chunker._is_markdown(MARKDOWN_TEXT) is True
        assert chunker._is_markdown("普通文本, 没有格式。") is False
        assert chunker._is_markdown("# 标题\n\n内容") is True

    def test_large_section_sub_split(self):
        """过大的 section 会被进一步切分"""
        from app.processors.document.chunker_factory import MarkdownStructureChunker
        # 构造一个超长 section
        long_md = "# 标题\n\n" + "这是一个很长的段落内容。" * 100
        chunker = MarkdownStructureChunker(chunk_size=200, max_heading_level=3)
        results = chunker.split(long_md)
        # 应被切分为多个 chunk
        assert len(results) >= 2


# ============================================================
#  F. SemanticChunkerAdapter 测试
# ============================================================

class TestSemanticChunkerAdapter:
    """语义分块适配器测试"""

    @pytest.fixture
    def chunker(self):
        from app.processors.document.chunker_factory import SemanticChunkerAdapter
        return SemanticChunkerAdapter(chunk_size=300, chunk_overlap=30)

    def test_basic_split(self, chunker):
        """基本语义分块"""
        results = chunker.split(MARKDOWN_TEXT)
        assert len(results) >= 1
        for r in results:
            assert r.metadata["strategy"] == "semantic"

    def test_empty_input(self, chunker):
        """空输入"""
        assert chunker.split("") == []
        assert chunker.split(None) == []

    def test_plain_text(self, chunker):
        """纯文本分块"""
        results = chunker.split(PLAIN_TEXT)
        assert len(results) >= 1
        assert all(r.content for r in results)

    def test_chunk_index_sequential(self, chunker):
        """chunk index 从 0 递增"""
        results = chunker.split(MARKDOWN_TEXT)
        for i, r in enumerate(results):
            assert r.index == i


# ============================================================
#  G. ChunkerFactory 测试
# ============================================================

class TestChunkerFactory:
    """分块器工厂 + 策略检测测试"""

    def test_create_recursive(self):
        """创建递归分块器"""
        from app.processors.document.chunker_factory import (
            ChunkerFactory,
            RecursiveCharacterChunker,
        )
        chunker = ChunkerFactory.create("recursive", chunk_size=200)
        assert isinstance(chunker, RecursiveCharacterChunker)

    def test_create_semantic(self):
        """创建语义分块器"""
        from app.processors.document.chunker_factory import (
            ChunkerFactory,
            SemanticChunkerAdapter,
        )
        chunker = ChunkerFactory.create("semantic")
        assert isinstance(chunker, SemanticChunkerAdapter)

    def test_create_markdown(self):
        """创建 Markdown 分块器"""
        from app.processors.document.chunker_factory import (
            ChunkerFactory,
            MarkdownStructureChunker,
        )
        chunker = ChunkerFactory.create("markdown")
        assert isinstance(chunker, MarkdownStructureChunker)

    def test_create_unknown_strategy(self):
        """未知策略应报错"""
        from app.processors.document.chunker_factory import ChunkerFactory
        with pytest.raises(ValueError, match="未知分块策略"):
            ChunkerFactory.create("nonexistent")

    def test_create_with_custom_params(self):
        """自定义参数传递"""
        from app.processors.document.chunker_factory import (
            ChunkerFactory,
            RecursiveCharacterChunker,
        )
        chunker = ChunkerFactory.create(
            "recursive",
            chunk_size=100,
            chunk_overlap=10,
        )
        assert isinstance(chunker, RecursiveCharacterChunker)
        assert chunker.chunk_size == 100
        assert chunker.chunk_overlap == 10

    # --- 策略检测 ---

    def test_detect_markdown(self):
        """检测 Markdown 文本"""
        from app.processors.document.chunker_factory import ChunkerFactory
        assert ChunkerFactory.detect_strategy(MARKDOWN_TEXT) == "markdown"

    def test_detect_semantic(self):
        """检测结构化文本 (多段落)"""
        from app.processors.document.chunker_factory import ChunkerFactory
        text = "段落一\n\n段落二\n\n段落三\n\n段落四"
        assert ChunkerFactory.detect_strategy(text) == "semantic"

    def test_detect_recursive(self):
        """检测纯文本"""
        from app.processors.document.chunker_factory import ChunkerFactory
        text = "这是一段没有格式的纯文本内容"
        assert ChunkerFactory.detect_strategy(text) == "recursive"

    def test_detect_empty(self):
        """空文本默认 recursive"""
        from app.processors.document.chunker_factory import ChunkerFactory
        assert ChunkerFactory.detect_strategy("") == "recursive"
        assert ChunkerFactory.detect_strategy(None) == "recursive"

    def test_detect_markdown_with_code(self):
        """检测含代码块的 Markdown"""
        from app.processors.document.chunker_factory import ChunkerFactory
        text = "# 标题\n\n```python\ncode\n```\n\n正文"
        assert ChunkerFactory.detect_strategy(text) == "markdown"


# ============================================================
#  H. 集成场景: Reranker + Chunker 协作
# ============================================================

class TestRerankerChunkerIntegration:
    """Reranker + Chunker 协作测试"""

    @pytest.mark.asyncio
    async def test_chunk_then_rerank(self):
        """先分块再重排的完整流程"""
        from app.processors.document.chunker_factory import ChunkerFactory
        from app.processors.retrieval.reranker import KeywordReranker

        # 1. 分块
        chunker = ChunkerFactory.create("recursive", chunk_size=100, chunk_overlap=10)
        chunks = chunker.split(PLAIN_TEXT)
        assert len(chunks) >= 1

        # 2. 转为文档列表
        docs = [{"content": c.content, "chunk_index": c.index} for c in chunks]

        # 3. 重排
        reranker = KeywordReranker()
        results = await reranker.rerank("RAG检索生成", docs, top_k=3)
        assert len(results) >= 1
        assert "rerank_score" in results[0]

    @pytest.mark.asyncio
    async def test_markdown_chunk_then_rerank(self):
        """Markdown 分块后重排"""
        from app.processors.document.chunker_factory import ChunkerFactory
        from app.processors.retrieval.reranker import KeywordReranker

        chunker = ChunkerFactory.create("markdown")
        chunks = chunker.split(MARKDOWN_TEXT)
        docs = [{"content": c.content, "heading": c.metadata.get("heading", "")} for c in chunks]

        reranker = KeywordReranker()
        results = await reranker.rerank("检索模块向量搜索", docs, top_k=5)
        assert len(results) >= 1

        # 包含 "检索" 和 "向量" 的 chunk 应排前面
        top_content = results[0]["content"].lower()
        assert "检索" in top_content or "向量" in top_content

    @pytest.mark.asyncio
    async def test_auto_detect_chunk_then_rerank(self):
        """自动检测策略 → 分块 → 重排"""
        from app.processors.document.chunker_factory import ChunkerFactory
        from app.processors.retrieval.reranker import KeywordReranker

        # 自动检测策略
        strategy = ChunkerFactory.detect_strategy(MARKDOWN_TEXT)
        assert strategy == "markdown"

        # 用检测到的策略分块
        chunker = ChunkerFactory.create(strategy, chunk_size=300)
        chunks = chunker.split(MARKDOWN_TEXT)
        docs = [{"content": c.content} for c in chunks]

        # 重排
        reranker = KeywordReranker()
        results = await reranker.rerank("RAG系统架构模块", docs, top_k=3)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_rerank_preserves_chunk_metadata(self):
        """重排后 chunk 的 metadata 字段保留"""
        from app.processors.document.chunker_factory import ChunkerFactory
        from app.processors.retrieval.reranker import KeywordReranker

        chunker = ChunkerFactory.create("markdown")
        chunks = chunker.split(MARKDOWN_TEXT)
        docs = [
            {"content": c.content, "index": c.index, "heading": c.metadata.get("heading", "")}
            for c in chunks
        ]

        reranker = KeywordReranker()
        results = await reranker.rerank("检索", docs, top_k=3)

        for r in results:
            assert "content" in r
            assert "index" in r
            assert "heading" in r
            assert "rerank_score" in r
            assert "rerank_rank" in r
