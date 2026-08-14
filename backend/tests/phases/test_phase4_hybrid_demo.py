"""
Phase 4 混合检索实际效果测试

直接操作数据库插入测试文档，构建向量索引，运行混合检索，展示实际效果。

运行方式:
  cd backend
  ..\\venv\\Scripts\\python.exe tests/phases/test_phase4_hybrid_demo.py
"""

import sys
import os
import asyncio
import json
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 初始化 Redis
from app.core.redis import init_redis
asyncio.run(init_redis())

from sqlalchemy import select, delete, func
from app.models.database import AsyncSessionLocal, async_engine, Base, _is_sqlite
from app.models.entities.user import User
from app.models.entities.knowledge_base import KnowledgeBase
from app.models.entities.document import Document, DocumentChunk
from app.services.retrieval_service import RetrievalService, RetrievedHit
from app.core.security import hash_password as get_password_hash


# ===================== 测试文档数据 =====================

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

# 测试查询
TEST_QUERIES = [
    {
        "query": "什么是RAG系统",
        "expected_keywords": ["RAG", "检索增强生成", "知识库"],
        "description": "中文语义查询 - 应该命中 rag_introduction.txt",
    },
    {
        "query": "vector database comparison FAISS pgvector Milvus",
        "expected_keywords": ["FAISS", "pgvector", "Milvus", "向量数据库"],
        "description": "英文关键词查询 - 应该命中 vector_database_comparison.txt",
    },
    {
        "query": "如何选择嵌入模型",
        "expected_keywords": ["嵌入模型", "Embedding", "向量维度"],
        "description": "中文语义查询 - 应该命中 embedding_models.txt",
    },
    {
        "query": "深度学习和神经网络有什么关系",
        "expected_keywords": ["深度学习", "神经网络", "机器学习"],
        "description": "中文语义查询 - 应该命中 machine_learning_basics.txt",
    },
    {
        "query": "中文分词工具和NLP处理",
        "expected_keywords": ["中文", "分词", "NLP", "jieba"],
        "description": "中文关键词+语义查询 - 应该命中 chinese_nlp_techniques.txt",
    },
    {
        "query": "hybrid search ranking BM25 TF-IDF reranking",
        "expected_keywords": ["混合检索", "BM25", "排序", "hybrid"],
        "description": "英文混合查询 - 应该命中 search_relevance_ranking.txt",
    },
]


def print_separator(title=""):
    print(f"\n{'='*70}")
    if title:
        print(f"  {title}")
        print(f"{'='*70}")


async def setup_test_data():
    """创建测试数据：用户、知识库、文档、分块"""
    print_separator("Step 1: 创建测试数据")
    
    async with AsyncSessionLocal() as db:
        # 0. 自动迁移: 确保 chunks 表有 embedding 和 search_vector 列
        from sqlalchemy import text as sql_text
        migration_needed = False
        try:
            # 检查 chunks 表结构
            result = await db.execute(sql_text("PRAGMA table_info(chunks)"))
            columns = [row[1] for row in result.fetchall()]
            if "embedding" not in columns or "search_vector" not in columns:
                migration_needed = True
        except Exception:
            migration_needed = True
        
        if migration_needed:
            print("  [!] 检测到 chunks 表缺少 Phase 4 新列，正在自动迁移...")
            try:
                if "embedding" not in columns:
                    await db.execute(sql_text("ALTER TABLE chunks ADD COLUMN embedding JSON"))
                    print("  [+] 已添加 embedding 列 (JSON 类型)")
                if "search_vector" not in columns:
                    await db.execute(sql_text("ALTER TABLE chunks ADD COLUMN search_vector TEXT"))
                    print("  [+] 已添加 search_vector 列 (Text 类型)")
                await db.commit()
                print("  [OK] 迁移完成")
            except Exception as e:
                print(f"  [!] 迁移跳过: {e}")
                await db.rollback()

        # 1. 创建测试用户
        result = await db.execute(select(User).where(User.username == "hybrid_test_user"))
        user = result.scalars().first()
        if user is None:
            user = User(
                username="hybrid_test_user",
                email="hybrid_test@example.com",
                password_hash=get_password_hash("TestPass123!"),
                is_active=True,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            print(f"  [+] 创建测试用户: {user.username} (id={user.id})")
        else:
            print(f"  [=] 测试用户已存在: {user.username} (id={user.id})")

        # 2. 创建知识库
        result = await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.name == "混合检索测试知识库")
        )
        kb = result.scalars().first()
        if kb is None:
            kb = KnowledgeBase(
                name="混合检索测试知识库",
                description="Phase 4 混合检索功能验证 - 包含RAG、向量数据库、嵌入模型等文档",
                user_id=user.id,
                is_public=True,
                status="active",
            )
            db.add(kb)
            await db.commit()
            await db.refresh(kb)
            print(f"  [+] 创建知识库: {kb.name} (id={kb.id})")
        else:
            # 清除旧数据，重新插入
            await db.execute(
                delete(DocumentChunk).where(DocumentChunk.knowledge_base_id == kb.id)
            )
            await db.execute(
                delete(Document).where(Document.knowledge_base_id == kb.id)
            )
            await db.commit()
            print(f"  [=] 知识库已存在，已清除旧文档: {kb.name} (id={kb.id})")

        # 3. 创建文档和分块
        total_chunks = 0
        for doc_data in TEST_DOCUMENTS:
            # 创建文档
            doc = Document(
                knowledge_base_id=kb.id,
                filename=doc_data["filename"],
                file_path=f"/test/{doc_data['filename']}",
                file_type="txt",
                mime_type="text/plain",
                file_size=len(doc_data["content"]),
                size_bytes=len(doc_data["content"].encode('utf-8')),
                content_text=doc_data["content"],
                status="completed",
                total_chunks=1,
            )
            db.add(doc)
            await db.commit()
            await db.refresh(doc)

            # 创建分块 (每篇文档作为一个 chunk)
            chunk = DocumentChunk(
                document_id=doc.id,
                knowledge_base_id=kb.id,
                content=doc_data["content"],
                chunk_index=0,
                metadata_=json.dumps({"source": doc_data["filename"]}, ensure_ascii=False),
                vector_index=-1,
            )
            db.add(chunk)
            await db.commit()
            await db.refresh(chunk)
            
            total_chunks += 1
            print(f"  [+] 文档: {doc_data['filename']} -> chunk_id={chunk.id}")

        # 更新知识库统计
        kb.total_documents = len(TEST_DOCUMENTS)
        kb.total_chunks = total_chunks
        await db.commit()

        print(f"\n  总计: {len(TEST_DOCUMENTS)} 篇文档, {total_chunks} 个分块")
        print(f"  数据库类型: {'SQLite' if _is_sqlite else 'PostgreSQL'}")

        return kb.id


async def build_vector_index(kb_id: int):
    """构建向量索引"""
    print_separator("Step 2: 构建向量索引")
    
    async with AsyncSessionLocal() as db:
        from app.services.document_service import DocumentService
        ds = DocumentService(db)
        
        # 删除旧的向量存储文件（如果存在）
        if ds.vector_manager.has_store(kb_id):
            ds.vector_manager.delete(kb_id)
            print(f"  已清除旧的向量存储")
        
        print(f"  正在为知识库 (id={kb_id}) 构建向量索引...")
        await ds._rebuild_vector_index(kb_id)
        
        # 检查向量存储状态
        store = ds.vector_manager.get_store(kb_id, dim=ds.embedding.dim)
        stats = store.stats()
        print(f"  向量维度: {stats['dim']}")
        print(f"  向量数量: {stats['total_vectors']}")
        
        # 检查一致性
        ok, issues = store.check_consistency()
        if ok:
            print(f"  一致性检查: [OK] 通过")
        else:
            print(f"  一致性检查: [FAIL] 存在问题")
            for issue in issues:
                print(f"    - {issue}")
        
        return stats


async def run_hybrid_search(kb_id: int):
    """运行混合检索测试"""
    print_separator("Step 3: 混合检索测试")
    
    async with AsyncSessionLocal() as db:
        service = RetrievalService(db)
        
        # 确认检索模式
        mode = "PostgreSQL 原生检索" if service._use_postgres_search else "FAISS + BM25 (降级模式)"
        print(f"  检索模式: {mode}")
        print(f"  权重配置: Vector={service.VECTOR_WEIGHT}, BM25={service.BM25_WEIGHT}, Keyword={service.KEYWORD_WEIGHT}")
        
        results_summary = []
        
        for i, test_case in enumerate(TEST_QUERIES, 1):
            print(f"\n  --- 查询 {i}/{len(TEST_QUERIES)} ---")
            print(f"  查询: \"{test_case['query']}\"")
            print(f"  预期: {test_case['description']}")
            
            try:
                hits = await service.search(
                    kb_id=kb_id,
                    query_text=test_case["query"],
                    user_id=None,  # 公开知识库
                    top_k=3,
                    min_score=0.0,
                    enable_rerank=True,
                    enable_merge=False,
                )
                
                if not hits:
                    print(f"  结果: 未找到匹配文档")
                    results_summary.append({
                        "query": test_case["query"],
                        "hit_count": 0,
                        "top_hit": None,
                    })
                    continue
                
                print(f"  结果: 找到 {len(hits)} 条匹配")
                print(f"  {'Rank':<5} {'Score':<8} {'Vector':<8} {'BM25':<8} {'Keyword':<8} {'Filename':<35} {'Content Preview'}")
                print(f"  {'-'*5} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*35} {'-'*40}")
                
                for hit in hits:
                    # 获取文件名
                    filename = hit.document_filename or "unknown"
                    content_preview = hit.content[:50].replace('\n', ' ').strip() + "..."
                    
                    print(f"  {hit.rank:<5} {hit.final_score:<8.4f} {hit.vector_score:<8.4f} {hit.bm25_score:<8.4f} {hit.keyword_score:<8.4f} {filename:<35} {content_preview}")
                
                # 分析检索质量
                top_hit = hits[0]
                top_filename = top_hit.document_filename or ""
                
                # 检查是否命中预期文档
                expected_file = ""
                for keyword in test_case["expected_keywords"]:
                    if keyword.lower() in top_filename.lower():
                        expected_file = top_filename
                        break
                
                # 显示完整的 top-1 内容
                print(f"\n  [Top-1 完整内容]")
                print(f"  文件: {top_filename}")
                print(f"  分数: vector={top_hit.vector_score:.4f}, bm25={top_hit.bm25_score:.4f}, keyword={top_hit.keyword_score:.4f}, final={top_hit.final_score:.4f}")
                print(f"  内容:\n    {top_hit.content[:200]}...")
                
                results_summary.append({
                    "query": test_case["query"],
                    "hit_count": len(hits),
                    "top_hit": top_filename,
                    "top_score": top_hit.final_score,
                })
                
            except Exception as e:
                print(f"  [ERROR] 检索失败: {e}")
                import traceback
                traceback.print_exc()
                results_summary.append({
                    "query": test_case["query"],
                    "hit_count": 0,
                    "top_hit": None,
                    "error": str(e),
                })
        
        return results_summary


async def run_search_mode_comparison(kb_id: int):
    """对比不同检索模式的效果"""
    print_separator("Step 4: 检索模式对比")
    
    async with AsyncSessionLocal() as db:
        service = RetrievalService(db)
        
        test_query = "RAG系统如何使用向量数据库进行检索"
        
        print(f"  查询: \"{test_query}\"")
        print()
        
        # 1. 向量+BM25+关键词 混合检索 (默认)
        print(f"  [模式 A] 混合检索 (Vector + BM25 + Keyword)")
        hits_hybrid = await service.search(
            kb_id=kb_id,
            query_text=test_query,
            top_k=3,
            enable_rerank=True,
        )
        _print_hits(hits_hybrid, "A")
        
        # 2. 仅向量检索
        print(f"\n  [模式 B] 仅向量检索 (无重排)")
        hits_vector = await service.search(
            kb_id=kb_id,
            query_text=test_query,
            top_k=3,
            enable_rerank=False,  # 禁用 BM25 和关键词重排
        )
        _print_hits(hits_vector, "B")
        
        # 3. BM25 分数分析
        print(f"\n  [模式 C] BM25 文本匹配分析")
        bm25_idx = await service._get_or_build_bm25(kb_id)
        if bm25_idx and bm25_idx._n_docs > 0:
            scores = bm25_idx.score_normalized(test_query)
            sorted_scores = sorted(scores.items(), key=lambda x: -x[1])[:3]
            
            # 获取 chunk 内容
            for rank, (chunk_id, score) in enumerate(sorted_scores, 1):
                result = await db.execute(select(DocumentChunk).where(DocumentChunk.id == chunk_id))
                chunk = result.scalars().first()
                if chunk:
                    result = await db.execute(select(Document).where(Document.id == chunk.document_id))
                    doc = result.scalars().first()
                    filename = doc.filename if doc else "unknown"
                    print(f"  Rank {rank}: BM25={score:.4f} | {filename}")
                    print(f"    内容: {chunk.content[:60]}...")
        else:
            print(f"  BM25 索引不可用")


def _print_hits(hits: list, mode_label: str):
    """打印检索结果"""
    if not hits:
        print(f"  [模式 {mode_label}] 未找到结果")
        return
    
    for hit in hits:
        filename = hit.document_filename or "unknown"
        print(f"  Rank {hit.rank}: score={hit.final_score:.4f} (vec={hit.vector_score:.4f}, bm25={hit.bm25_score:.4f}, kw={hit.keyword_score:.4f}) | {filename}")


async def show_statistics(kb_id: int):
    """显示知识库统计信息"""
    print_separator("Step 5: 知识库统计")
    
    async with AsyncSessionLocal() as db:
        service = RetrievalService(db)
        stats = await service.get_kb_stats(kb_id, user_id=None)
        
        if stats:
            print(f"  知识库名称: {stats.get('kb_name', 'N/A')}")
            print(f"  文档总数: {stats.get('total_documents', 0)}")
            print(f"  分块总数: {stats.get('total_chunks', 0)}")
            print(f"  搜索模式: {stats.get('search_mode', 'unknown')}")
            print(f"  嵌入维度: {stats.get('embedding_dim', 'N/A')}")
            
            vs = stats.get('vector_store', {})
            if vs:
                print(f"  向量存储:")
                print(f"    - 类型: {vs.get('store_type', 'N/A')}")
                print(f"    - 总向量: {vs.get('total_vectors', 0)}")
                print(f"    - 维度: {vs.get('dim', 'N/A')}")
            
            pg_stats = stats.get('postgres_vector_store')
            if pg_stats:
                print(f"  PostgreSQL 向量存储:")
                print(f"    - 总分块: {pg_stats.get('total_chunks', 0)}")
                print(f"    - 有向量分块: {pg_stats.get('chunks_with_vectors', 0)}")


async def cleanup(kb_id: int):
    """清理测试数据"""
    print_separator("Step 6: 清理选项")
    print(f"  测试数据已保留在数据库中，便于再次测试")
    print(f"  知识库 ID: {kb_id}")
    print(f"  如需清理，可手动删除知识库和相关文档")


async def main():
    print("=" * 70)
    print("  Phase 4 混合检索实际效果测试")
    print("=" * 70)
    
    print(f"\n  数据库类型: {'SQLite' if _is_sqlite else 'PostgreSQL'}")
    print(f"  测试文档数: {len(TEST_DOCUMENTS)}")
    print(f"  测试查询数: {len(TEST_QUERIES)}")
    
    try:
        # Step 1: 创建测试数据
        kb_id = await setup_test_data()
        
        # Step 2: 构建向量索引
        await build_vector_index(kb_id)
        
        # Step 3: 混合检索测试
        results = await run_hybrid_search(kb_id)
        
        # Step 4: 模式对比
        await run_search_mode_comparison(kb_id)
        
        # Step 5: 统计信息
        await show_statistics(kb_id)
        
        # 总结
        print_separator("总结: 混合检索效果分析")
        
        successful = sum(1 for r in results if r["hit_count"] > 0)
        print(f"  查询成功: {successful}/{len(results)}")
        print()
        
        print(f"  {'查询':<45} {'命中数':<8} {'Top-1 文档':<35} {'分数'}")
        print(f"  {'-'*45} {'-'*8} {'-'*35} {'-'*8}")
        
        for r in results:
            query = r["query"][:42] + "..." if len(r["query"]) > 42 else r["query"]
            hit_count = r["hit_count"]
            top_hit = (r["top_hit"] or "N/A")[:33]
            top_score = f"{r.get('top_score', 0):.4f}" if r.get("top_score") else "N/A"
            print(f"  {query:<45} {hit_count:<8} {top_hit:<35} {top_score}")
        
        await cleanup(kb_id)
        
        print(f"\n{'='*70}")
        print(f"  混合检索测试完成!")
        print(f"{'='*70}")
        
    except Exception as e:
        print(f"\n  [FATAL] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
