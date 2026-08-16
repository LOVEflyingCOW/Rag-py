"""
Phase 4 混合检索 DEMO / pytest 用例（双模式）

⚠️ 两种运行方式：
-----------------------------------------------------------------
A) pytest 用例模式（PHASE7 新增 —— 标记 @pytest.mark.slow @pytest.mark.demo）
   pytest -m demo                                            # 只跑 DEMO 套件（默认跳过）
   pytest tests/phases/test_phase4_hybrid_demo.py -m demo    # 指定文件跑
   ✨ 特点：用 pytest 断言代替 print，失败时能明确看哪个 query 命中错误

B) 手动脚本模式（旧版兼容，不依赖 pytest runner）
   cd backend
   ..\venv\Scripts\python.exe tests/phases/test_phase4_hybrid_demo.py
   ✨ 特点：人眼可读的漂亮表格输出，演示用

为什么默认用 @pytest.mark.slow @pytest.mark.demo 标记（默认跳过）：
  - 依赖真 Embedding 模型（要对 6 个文档 + 6 个查询做 embedding）
  - 本地无 MODEL_CACHE 时可能触发下载（200MB+，首次 30s+）
  - 单条用例 20-60s，日常 CI 不适合跑；想验证时显式加 -m demo
"""

import sys
import os
import asyncio
import json
from pathlib import Path
from datetime import datetime
from typing import Tuple  # Python 3.7 兼容：不能用 tuple[A,B] 内置泛型

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ===================== 测试文档数据（模块级，pytest 导入时无副作用）=====================

TEST_DOCUMENTS = [
    {
        "filename": "rag_introduction.txt",
        "content": """RAG（检索增强生成）是一种结合了信息检索与大语言模型的技术框架。
RAG系统的工作流程包括：首先从知识库中检索与用户问题相关的文档片段，
然后将这些检索到的内容作为上下文提供给大语言模型，最后由大语言模型生成准确、可靠的回答。
RAG的核心优势在于能够利用外部知识库，减少大模型的幻觉问题，同时保持知识的时效性。
常见的RAG应用场景包括企业知识库问答、技术文档助手、法律条文检索等。""",
    },
    {
        "filename": "vector_database_comparison.txt",
        "content": """向量数据库是RAG系统的核心组件，负责存储和检索文档的向量表示。
主流的向量数据库包括：FAISS是Facebook开源的向量检索库，适合单机场景；
pgvector是PostgreSQL的向量扩展，适合需要事务一致性的场景；
Milvus是开源的分布式向量数据库，支持十亿级向量；
Pinecone是商业化的云原生向量数据库，提供托管服务。
选择向量数据库时需要考虑：数据规模、查询延迟、一致性要求、成本预算等因素。""",
    },
    {
        "filename": "embedding_models.txt",
        "content": """嵌入模型（Embedding Model）将文本转换为高维向量，是RAG系统的基础。
常用的嵌入模型包括：OpenAI的text-embedding-ada-002，维度为1536；
Sentence-Transformers的all-MiniLM-L6-v2，维度为384，适合资源受限的场景；
BGE模型在中文场景表现优秀，支持多语言。
选择嵌入模型时需要权衡：向量维度、推理速度、语言支持、模型大小等因素。
嵌入模型的质量直接影响检索的准确率，因此需要根据具体任务选择合适的模型。""",
    },
    {
        "filename": "machine_learning_basics.txt",
        "content": """机器学习是人工智能的核心分支，通过数据驱动的方式让计算机自动学习规律。
监督学习需要标注数据，包括分类和回归任务；
无监督学习不需要标注数据，包括聚类和降维；
强化学习通过与环境交互学习最优策略。
深度学习是机器学习的子集，基于多层神经网络，在图像识别、自然语言处理等领域取得了突破性进展。
常见的深度学习框架包括TensorFlow、PyTorch和JAX。""",
    },
    {
        "filename": "chinese_nlp_techniques.txt",
        "content": """中文自然语言处理（NLP）面临独特的挑战，包括分词、词义消歧、命名实体识别等。
中文分词是NLP的基础步骤，常用工具包括jieba、HanLP、LTP等。
在大语言模型时代，中文NLP能力得到了显著提升，GPT-4、文心一言、通义千问等模型在中文理解方面表现出色。
RAG系统在处理中文文档时，需要特别注意分词质量和语义理解的准确性。
对于专业领域的中文文档，通常需要使用领域特定的嵌入模型来提高检索效果。""",
    },
    {
        "filename": "search_relevance_ranking.txt",
        "content": """搜索相关性排序是信息检索的核心问题。传统的排序方法主要基于关键词匹配，
如TF-IDF和BM25，它们通过词频和逆文档频率来衡量文档与查询的相关性。
现代的排序方法结合了语义理解，使用向量相似度来捕捉深层语义关系。
混合检索（Hybrid Search）将关键词匹配与语义搜索结合，通常能取得比单一方法更好的效果。
常见的融合策略包括线性加权融合和倒数排名融合（RRF）。
重排序（Reranking）是在初步检索后使用更精细的模型对结果进行二次排序，进一步提高准确率。""",
    },
]

# 测试查询（每个查询对应预期命中的文档关键词 —— pytest 断言会检查 top-1 是否命中预期文档）
TEST_QUERIES = [
    {
        "query": "什么是RAG系统",
        "expected_keywords": ["rag_introduction", "RAG", "检索增强", "知识库"],
        "description": "中文语义-RAG介绍",
    },
    {
        "query": "vector database comparison FAISS pgvector Milvus",
        "expected_keywords": ["vector_database", "FAISS", "pgvector", "Milvus"],
        "description": "英文关键词-向量数据库对比",
    },
    {
        "query": "如何选择嵌入模型",
        "expected_keywords": ["embedding_models", "嵌入模型", "Embedding", "向量维度"],
        "description": "中文语义-嵌入模型选择",
    },
    {
        "query": "深度学习和神经网络有什么关系",
        "expected_keywords": ["machine_learning", "深度学习", "神经网络", "机器学习"],
        "description": "中文语义-机器学习基础",
    },
    {
        "query": "中文分词工具和NLP处理",
        "expected_keywords": ["chinese_nlp", "中文", "分词", "jieba", "NLP"],
        "description": "中文关键词-中文NLP",
    },
    {
        "query": "hybrid search ranking BM25 TF-IDF reranking",
        "expected_keywords": ["search_relevance", "混合检索", "BM25", "排序", "hybrid"],
        "description": "英文混合-相关性排序",
    },
]


def print_separator(title=""):
    """脚本模式下用的漂亮分隔线（pytest 模式不用）"""
    print(f"\n{'='*70}")
    if title:
        print(f"  {title}")
        print(f"{'='*70}")


# ============================================================
#  公共逻辑：setup/build/search 步骤
#  - 脚本模式和 pytest 模式共用，避免重复实现
#  - 区别：pytest 模式通过 fixture 传 db_session；脚本模式直接用 AsyncSessionLocal
# ============================================================


def _match_expected(filename: str, expected_keywords: list) -> bool:
    """top-1 文件名里是否有任意 expected keyword"""
    name_l = filename.lower()
    return any(kw.lower() in name_l for kw in expected_keywords)


async def _setup_kb_data(db_session, user_factory=None):
    """创建测试数据（用 pytest fixtures 更优雅）。

    两种 DB accessor：
      pytest 模式: user_factory 可用（造 user/kb cascade），db_session 是回滚安全的 function scope
      脚本模式: user_factory 为 None，直接 insert User/KnowledgeBase/Document/Chunk
    """
    from sqlalchemy import select, delete, func
    from app.models.entities.user import User
    from app.models.entities.knowledge_base import KnowledgeBase
    from app.models.entities.document import Document, DocumentChunk
    from app.core.security import hash_password as get_password_hash
    from app.models.database import _is_sqlite

    # 0. 迁移保险（SQLite 内存库第一次跑时 chunks 表可能缺 embedding/search_vector 列）
    from sqlalchemy import text as sql_text

    migration_needed = False
    columns: list = []
    try:
        result = await db_session.execute(sql_text("PRAGMA table_info(chunks)"))
        columns = [row[1] for row in result.fetchall()]
        if columns and ("embedding" not in columns or "search_vector" not in columns):
            migration_needed = True
    except Exception:
        migration_needed = True

    if migration_needed:
        print("  [!] 检测到 chunks 表缺少 Phase 4 新列，正在自动迁移...")
        try:
            if "embedding" not in columns:
                await db_session.execute(sql_text("ALTER TABLE chunks ADD COLUMN embedding JSON"))
                print("  [+] 已添加 embedding 列")
            if "search_vector" not in columns:
                await db_session.execute(sql_text("ALTER TABLE chunks ADD COLUMN search_vector TEXT"))
                print("  [+] 已添加 search_vector 列")
            await db_session.commit()
        except Exception:
            await db_session.rollback()

    # 1. 创建测试用户（有 user_factory 用 factory，无则直接 insert）
    if user_factory is not None:
        tenant = await user_factory.tenant_factory.acreate(
            db_session, name="hybrid_demo_tenant"
        )
        user = await user_factory.acreate(
            db_session,
            username=f"hybrid_test_user_{os.getpid()}",
            tenant_id=tenant.id if tenant else None,
        )
        await db_session.flush()
    else:
        result = await db_session.execute(
            select(User).where(User.username == "hybrid_test_user")
        )
        user = result.scalars().first()
        if user is None:
            user = User(
                username="hybrid_test_user",
                email="hybrid_test@example.com",
                password_hash=get_password_hash("TestPass123!"),
                is_active=True,
            )
            db_session.add(user)
            await db_session.commit()
            await db_session.refresh(user)

    # 2. 创建知识库（固定名，脚本模式下幂等）
    if user_factory is not None:
        # pytest 模式：每个 session 唯一 name，避免冲突（SQLite 内存库其实隔离，为保险）
        kb = KnowledgeBase(
            name=f"混合检索测试知识库_{os.getpid()}",
            description="Phase 4 混合检索 pytest DEMO",
            user_id=user.id,
            tenant_id=user.tenant_id,
            is_public=True,
            status="active",
        )
        db_session.add(kb)
        await db_session.flush()
    else:
        result = await db_session.execute(
            select(KnowledgeBase).where(KnowledgeBase.name == "混合检索测试知识库")
        )
        kb = result.scalars().first()
        if kb is None:
            kb = KnowledgeBase(
                name="混合检索测试知识库",
                description="Phase 4 混合检索功能验证",
                user_id=user.id,
                is_public=True,
                status="active",
            )
            db_session.add(kb)
            await db_session.commit()
            await db_session.refresh(kb)
        else:
            # 幂等：清除旧文档，重新插入
            await db_session.execute(
                delete(DocumentChunk).where(DocumentChunk.knowledge_base_id == kb.id)
            )
            await db_session.execute(
                delete(Document).where(Document.knowledge_base_id == kb.id)
            )
            await db_session.commit()

    # 3. 创建文档 + 分块（一个文档 = 1 chunk，方便断言 top-1）
    total_chunks = 0
    for doc_data in TEST_DOCUMENTS:
        doc = Document(
            knowledge_base_id=kb.id,
            filename=doc_data["filename"],
            file_path=f"/test/{doc_data['filename']}",
            file_type="txt",
            mime_type="text/plain",
            file_size=len(doc_data["content"]),
            size_bytes=len(doc_data["content"].encode("utf-8")),
            content_text=doc_data["content"],
            status="completed",
            total_chunks=1,
        )
        db_session.add(doc)
        await db_session.flush()

        chunk = DocumentChunk(
            document_id=doc.id,
            knowledge_base_id=kb.id,
            content=doc_data["content"],
            chunk_index=0,
            metadata_=json.dumps({"source": doc_data["filename"]}, ensure_ascii=False),
            vector_index=-1,
        )
        db_session.add(chunk)
        await db_session.flush()
        total_chunks += 1

    # 更新 kb 统计 + commit（脚本模式要持久化；pytest 模式 commit 也 OK，fixture teardown 回滚）
    kb.total_documents = len(TEST_DOCUMENTS)
    kb.total_chunks = total_chunks
    await db_session.commit()

    return kb.id


async def _build_vector_index(kb_id: int):
    """对某个 kb build 向量索引（返回 stats dict）"""
    from app.models.database import AsyncSessionLocal
    from app.services.document_service import DocumentService

    async with AsyncSessionLocal() as db:
        ds = DocumentService(db)
        if ds.vector_manager.has_store(kb_id):
            ds.vector_manager.delete(kb_id)
        await ds._rebuild_vector_index(kb_id)
        store = ds.vector_manager.get_store(kb_id, dim=ds.embedding.dim)
        return store.stats()


async def _run_search(db_session, kb_id: int, query: str, enable_rerank: bool = True):
    """封装：调用 RetrievalService.search，返回 list[RetrievedHit]"""
    from app.services.retrieval_service import RetrievalService

    service = RetrievalService(db_session)
    return await service.search(
        kb_id=kb_id,
        query_text=query,
        user_id=None,  # 公开知识库
        top_k=3,
        min_score=0.0,
        enable_rerank=enable_rerank,
        enable_merge=False,
    )


def _check_embedding_model_available() -> Tuple[bool, str]:
    """检测 Embedding 模型文件是否可用；没有则建议 skip。

    返回: (ok, reason)
    """
    try:
        from app.core.config import settings
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception as e:
        return False, f"import Embedding 依赖失败: {e}"

    model_dir = getattr(settings, "MODEL_CACHE_DIR", None) or Path(
        PROJECT_ROOT / "backend" / "data" / "model_cache"
    )
    model_path = Path(model_dir) / "embedding"
    # 条件：
    #   a) embedding 目录下有实际模型文件（.bin / .safetensors / config.json 任一）
    #   b) 或者 SentenceTransformer 能直接从 HF hub load（联网场景）
    model_file_exists = (
        model_path.exists()
        and any(
            p.suffix.lower() in {".bin", ".safetensors", ".json", ".pt"}
            for p in model_path.rglob("*")
        )
    )
    if model_file_exists:
        return True, "ok (local model cache)"
    # 不直接尝试联网下载，避免卡死；给出提示信息
    return (
        False,
        "Embedding 模型未缓存（建议手动跑一次 build_vector_index 让它自动下载）",
    )


# ============================================================
#  PART A · pytest 模式（PHASE7 新增）
# ============================================================
# 整个 module 加 pytestmark = [slow, demo]，默认 pytest.ini -m "not ..." 跳过
import pytest  # noqa: E402

pytestmark = [
    pytest.mark.slow,   # 单条 > 10s
    pytest.mark.demo,   # 演示类：依赖真 Embedding 模型
]


@pytest.fixture(scope="module")
async def hybrid_demo_env():
    """module scope fixture：一次性建 kb → build vector index → cleanup。

    实现说明（为什么不用 db_session/user_factory fixtures）：
      - db_session 是 function scope，module scope fixture 不能依赖它（pytest scope 层级限制）
      - 所以直接用真实 AsyncSessionLocal 连持久化库（通常是 SQLite 文件 / PostgreSQL）
      - 好处：整个 module 9 个 test function 共用同一个 KB，embedding build 只做 1 次（15-30s）
      - teardown：手动删 KB 级联文档/chunks + 删 vector store 文件，避免污染下次测试
    """
    import pytest as _pt
    from sqlalchemy import select, delete
    from app.models.database import AsyncSessionLocal
    from app.models.entities.knowledge_base import KnowledgeBase
    from app.models.entities.document import Document, DocumentChunk
    from app.services.document_service import DocumentService

    # ---- 前置条件 1：Embedding 模型可用 ----
    ok, reason = _check_embedding_model_available()
    if not ok:
        _pt.skip(
            f"[hybrid_demo_env] Embedding 模型不可用，跳过 hybrid_demo。原因: {reason}"
        )

    # ---- 前置条件 2：真 DB session 可用 ----
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(select(1))
    except Exception as e:
        _pt.skip(f"[hybrid_demo_env] AsyncSessionLocal 不可用: {e}")

    kb_id = None  # type: int | None
    kb_obj = None

    # ---- 1) 造测试数据（脚本模式同一套逻辑，持久化）----
    async with AsyncSessionLocal() as db:
        kb_id = await _setup_kb_data(db, user_factory=None)
        # 拿到 kb 对象（后面 teardown 用）
        kb_obj = (await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))).scalars().first()

    # ---- 2) build 向量索引（关键慢步骤：真 embedding）----
    try:
        await _build_vector_index(kb_id)
    except Exception as e:
        # build 失败（没模型 / 联网失败）→ skip，不是 FAIL
        _pt.skip(f"[hybrid_demo_env] Build 向量索引失败，跳过 demo: {e}")

    yield {"kb_id": kb_id}  # 共享给 test function

    # ---- teardown：KB 级联删除 + vector store 文件清理 ----
    try:
        async with AsyncSessionLocal() as db:
            # 删 chunks → docs → kb（级联：有些外键没设 CASCADE，手动按顺序删保险）
            await db.execute(delete(DocumentChunk).where(DocumentChunk.knowledge_base_id == kb_id))
            await db.execute(delete(Document).where(Document.knowledge_base_id == kb_id))
            if kb_obj is not None:
                await db.delete(kb_obj)
            await db.commit()
    except Exception:
        pass  # 清理失败不影响 test 结果（顶多残留一些 test 数据，下次 setup 会幂等清除）

    try:
        async with AsyncSessionLocal() as db:
            ds = DocumentService(db)
            if ds.vector_manager.has_store(kb_id):
                ds.vector_manager.delete(kb_id)
    except Exception:
        pass


# ============================================================
#  pytest 模式专用 helper：给每个 test function 一个 AsyncSession
#  不用 db_session fixture 是为了避免「function scope vs module scope」冲突
# ============================================================
async def _pytest_run_search(kb_id: int, query: str, enable_rerank: bool = True):
    """pytest 模式下的 search：直接开真实 AsyncSessionLocal，确保能看到 fixture 写入的数据。"""
    from app.models.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        return await _run_search(db, kb_id, query, enable_rerank=enable_rerank)


# ⭐ 主断言：6 个查询 × top-1 是否命中预期文档
@pytest.mark.parametrize(
    "test_case",
    TEST_QUERIES,
    ids=[q["description"] for q in TEST_QUERIES],  # pytest 显示用例名时用这个（可读性）
)
@pytest.mark.asyncio
async def test_hybrid_top1_matches_expected(hybrid_demo_env, test_case):
    """每个查询 top-1 文档的文件名必须包含至少 1 个 expected 关键词。

    这是混合检索「效果底线」断言 —— 6 个文档语义区分度足够，top-1 错了基本说明检索有 bug。
    """
    kb_id = hybrid_demo_env["kb_id"]

    hits = await _pytest_run_search(kb_id, test_case["query"], enable_rerank=True)

    # 1) 至少有 1 条结果
    assert hits, f"查询 [{test_case['query']}] 返回空结果（期望 ≥1 条）"

    # 2) top-1 文件名是否命中预期
    top_hit = hits[0]
    top_filename = top_hit.document_filename or ""
    assert _match_expected(top_filename, test_case["expected_keywords"]), (
        f"查询 [{test_case['query']}] Top-1 命中错误文档。\n"
        f"  实际命中: {top_filename} (score={top_hit.final_score:.4f})\n"
        f"  预期包含关键词: {test_case['expected_keywords']}"
    )


@pytest.mark.asyncio
async def test_hybrid_has_hits_for_all_queries(hybrid_demo_env):
    """批量断言：6 个查询每条都至少有 1 条 hit（快速 smoke 类断言）。"""
    kb_id = hybrid_demo_env["kb_id"]
    results = []
    for tc in TEST_QUERIES:
        hits = await _pytest_run_search(kb_id, tc["query"])
        results.append((tc["query"], len(hits)))

    bad = [r for r in results if r[1] == 0]
    assert not bad, f"以下查询返回 0 条 hits: {bad}"


@pytest.mark.asyncio
async def test_hybrid_hybrid_mode_beats_vector_only(hybrid_demo_env):
    """定性断言：Hybrid（混合检索）模式 hit_count ≥ 纯向量模式 hit_count。

    一般 Hybrid 会召回更多（因为结合了 BM25 / keyword），这里只做 ≥ 断言。
    """
    kb_id = hybrid_demo_env["kb_id"]
    q = "RAG系统如何使用向量数据库进行检索"

    from app.models.database import AsyncSessionLocal
    from app.services.retrieval_service import RetrievalService

    async with AsyncSessionLocal() as db:
        svc = RetrievalService(db)

        hits_hybrid = await svc.search(
            kb_id=kb_id, query_text=q, top_k=5, enable_rerank=True
        )
        hits_vector = await svc.search(
            kb_id=kb_id, query_text=q, top_k=5, enable_rerank=False
        )

    # 定性：混合召回 ≥ 纯向量召回（不是绝对，但对 DEMO 文档集应该成立）
    assert len(hits_hybrid) >= len(hits_vector), (
        f"Hybrid 召回 {len(hits_hybrid)} 条，纯向量召回 {len(hits_vector)} 条，"
        "Hybrid 不应少于纯向量"
    )


@pytest.mark.asyncio
async def test_hybrid_kb_stats(hybrid_demo_env):
    """get_kb_stats 返回文档/分块数量和 setup 一致。"""
    kb_id = hybrid_demo_env["kb_id"]
    from app.models.database import AsyncSessionLocal
    from app.services.retrieval_service import RetrievalService

    async with AsyncSessionLocal() as db:
        svc = RetrievalService(db)
        stats = await svc.get_kb_stats(kb_id, user_id=None)

    assert stats is not None, "get_kb_stats 返回 None"
    assert stats.get("total_documents") == len(TEST_DOCUMENTS), (
        f"文档数 {stats.get('total_documents')} != 预期 {len(TEST_DOCUMENTS)}"
    )
    assert stats.get("total_chunks") == len(TEST_DOCUMENTS), (
        f"分块数 {stats.get('total_chunks')} != 预期 {len(TEST_DOCUMENTS)} (1 doc = 1 chunk)"
    )


# ============================================================
#  PART B · 手动脚本模式（旧版兼容）
# ============================================================


def _print_hits(hits: list, mode_label: str = ""):
    """脚本模式下漂亮打印检索结果"""
    if not hits:
        print(f"  [模式 {mode_label}] 未找到结果")
        return

    for hit in hits:
        filename = hit.document_filename or "unknown"
        print(
            f"  Rank {hit.rank}: score={hit.final_score:.4f} "
            f"(vec={hit.vector_score:.4f}, bm25={hit.bm25_score:.4f}, "
            f"kw={hit.keyword_score:.4f}) | {filename}"
        )


async def setup_test_data():
    """脚本模式：使用真实 AsyncSessionLocal 造数据（持久化，支持多轮重复演示）"""
    from app.models.database import AsyncSessionLocal

    print_separator("Step 1: 创建测试数据")
    async with AsyncSessionLocal() as db:
        kb_id = await _setup_kb_data(db, user_factory=None)
        await db.commit()
        print(f"  [OK] 知识库 ID={kb_id}，文档 {len(TEST_DOCUMENTS)} 篇")
        return kb_id


async def build_vector_index(kb_id: int):
    print_separator("Step 2: 构建向量索引")
    stats = await _build_vector_index(kb_id)
    print(f"  向量维度: {stats['dim']}  向量数量: {stats['total_vectors']}")
    return stats


async def run_hybrid_search(kb_id: int):
    """脚本模式：循环 6 个查询 + 打印详细表格"""
    from app.models.database import AsyncSessionLocal

    print_separator("Step 3: 混合检索测试")
    async with AsyncSessionLocal() as db:
        service = __import__("app.services.retrieval_service", fromlist=["RetrievedHit"]).RetrievalService(db)
        mode = "PostgreSQL 原生检索" if service._use_postgres_search else "FAISS + BM25 (降级模式)"
        print(f"  检索模式: {mode}")

        summary = []
        for i, tc in enumerate(TEST_QUERIES, 1):
            print(f"\n  --- 查询 {i}/{len(TEST_QUERIES)} ---")
            print(f"  查询: \"{tc['query']}\"   预期: {tc['description']}")
            try:
                hits = await _run_search(db, kb_id, tc["query"])
                print(f"  命中 {len(hits)} 条")
                if hits:
                    for h in hits[:3]:
                        fn = h.document_filename or "?"
                        preview = h.content[:40].replace("\n", " ").strip() + "..."
                        print(
                            f"    #{h.rank} score={h.final_score:.4f} "
                            f"(vec={h.vector_score:.2f} bm25={h.bm25_score:.2f} kw={h.keyword_score:.2f}) "
                            f"| {fn:<30} {preview}"
                        )
                    top_fn = hits[0].document_filename or ""
                    ok = _match_expected(top_fn, tc["expected_keywords"])
                    print(f"  Top-1 命中预期文档: {'YES' if ok else 'NO'}")
                    summary.append(
                        {
                            "query": tc["query"],
                            "hit_count": len(hits),
                            "top_hit": top_fn,
                            "top_score": hits[0].final_score,
                            "matched": ok,
                        }
                    )
                else:
                    summary.append(
                        {"query": tc["query"], "hit_count": 0, "top_hit": None, "matched": False}
                    )
            except Exception as e:
                print(f"  [ERROR] {e}")
                summary.append({"query": tc["query"], "hit_count": 0, "error": str(e)})

        return summary


async def run_search_mode_comparison(kb_id: int):
    print_separator("Step 4: 检索模式对比")
    from app.models.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.entities.document import Document, DocumentChunk

    async with AsyncSessionLocal() as db:
        from app.services.retrieval_service import RetrievalService

        service = RetrievalService(db)
        q = "RAG系统如何使用向量数据库进行检索"
        print(f"  查询: \"{q}\"\n")

        print("  [模式 A] 混合检索 (Vector + BM25 + Keyword, rerank=True)")
        hits = await service.search(kb_id=kb_id, query_text=q, top_k=3, enable_rerank=True)
        _print_hits(hits, "A")

        print("\n  [模式 B] 纯向量 (无 BM25/Keyword, rerank=False)")
        hits = await service.search(kb_id=kb_id, query_text=q, top_k=3, enable_rerank=False)
        _print_hits(hits, "B")

        print("\n  [模式 C] BM25 文本匹配 Top-3 (原始分数)")
        idx = await service._get_or_build_bm25(kb_id)
        if idx and idx._n_docs > 0:
            scores = sorted(idx.score_normalized(q).items(), key=lambda x: -x[1])[:3]
            for r, (cid, s) in enumerate(scores, 1):
                row = (await db.execute(select(DocumentChunk).where(DocumentChunk.id == cid))).scalars().first()
                doc = (
                    (await db.execute(select(Document).where(Document.id == row.document_id)))
                    .scalars()
                    .first()
                    if row
                    else None
                )
                fn = doc.filename if doc else "?"
                content = (row.content[:55] + "...") if row else "?"
                print(f"    Rank {r}: BM25={s:.4f} | {fn:<30} {content}")
        else:
            print("    BM25 索引不可用")


async def show_statistics(kb_id: int):
    print_separator("Step 5: 知识库统计")
    from app.models.database import AsyncSessionLocal
    from app.services.retrieval_service import RetrievalService

    async with AsyncSessionLocal() as db:
        svc = RetrievalService(db)
        stats = await svc.get_kb_stats(kb_id, user_id=None)
        if not stats:
            print("  (get_kb_stats 返回 None)")
            return
        print(f"  知识库名称: {stats.get('kb_name')}")
        print(f"  文档总数: {stats.get('total_documents')}   分块总数: {stats.get('total_chunks')}")
        print(f"  搜索模式: {stats.get('search_mode')}   嵌入维度: {stats.get('embedding_dim')}")


async def cleanup(kb_id: int):
    print_separator("Step 6: 清理")
    print(f"  测试数据保留（便于再次测试），知识库 ID={kb_id}")
    print(f"  手动清理: 删除知识库和相关文档即可")


async def main():
    try:
        from app.core.redis import init_redis

        await init_redis()
    except Exception:
        pass

    print("=" * 70)
    print("  Phase 4 混合检索 DEMO（脚本模式）")
    print("=" * 70)

    try:
        kb_id = await setup_test_data()
        await build_vector_index(kb_id)
        results = await run_hybrid_search(kb_id)
        await run_search_mode_comparison(kb_id)
        await show_statistics(kb_id)

        # 总结
        print_separator("总结")
        matched = sum(1 for r in results if r.get("matched"))
        total = len(results)
        print(f"  Top-1 命中预期文档: {matched}/{total}")
        print()
        print(f"  {'查询':<36} {'命中':<6} {'Top-1 文档':<30} {'分数':<8} 预期")
        print(f"  {'-'*36} {'-'*6} {'-'*30} {'-'*8}")
        for tc, r in zip(TEST_QUERIES, results):
            q = (tc["query"][:33] + "...") if len(tc["query"]) > 33 else tc["query"]
            top_hit = (r.get("top_hit") or "N/A")[:28]
            score = f"{r.get('top_score', 0):.4f}" if r.get("top_score") else "N/A"
            ok = "Y" if r.get("matched") else "N"
            print(f"  {q:<36} {r['hit_count']:<6} {top_hit:<30} {score:<8} {ok}")

        await cleanup(kb_id)
        print(f"\n{'='*70}\n  DEMO 完成!\n{'='*70}")
    except Exception as e:
        print(f"\n  [FATAL] {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
