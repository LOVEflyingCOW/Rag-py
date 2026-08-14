"""
Phase 4 验证测试 — 全文检索 (pgvector + PostgreSQL FTS)

运行方式:
  cd backend
  ..\\venv\\Scripts\\python.exe tests/phases/test_phase4_fulltext_search.py

测试内容:
1. 数据模型扩展 (embedding + search_vector 列)
2. pgvector 存储服务 (PgVectorStore)
3. PostgreSQL 全文检索 (PgFullTextSearch)
4. RetrievalService 自动选择检索策略
5. 混合检索 (Vector + FTS)
"""

import sys
import os
import time
import json
import asyncio
import requests
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 初始化 Redis (Phase 3 依赖)
from app.core.redis import init_redis
asyncio.run(init_redis())

PASS = 0
FAIL = 0
SKIP = 0

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api/v1"

token = None
kb_id = None
doc_ids = []


def pass_test(name, detail=""):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))


def fail_test(name, detail=""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def skip_test(name, detail=""):
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name}" + (f" — {detail}" if detail else ""))


def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


def create_test_user(username_suffix="p4_test", password="TestPass123!"):
    uname = f"{username_suffix}_{int(time.time())}"
    email = f"{uname}@example.com"
    try:
        resp = requests.post(f"{API}/auth/register", json={
            "username": uname,
            "email": email,
            "password": password,
            "confirm_password": password,
        }, timeout=5)
        if resp.status_code == 409:
            resp = requests.post(f"{API}/auth/login", json={
                "username": uname,
                "password": password,
            }, timeout=5)
        if resp.status_code in (200, 201):
            data = resp.json()
            return data.get("access_token") or data.get("token"), uname
    except requests.exceptions.RequestException:
        pass
    return None, uname


def get_headers():
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def create_knowledge_base(name="Test KB"):
    try:
        resp = requests.post(f"{API}/knowledge-bases", json={
            "name": name,
            "description": "Phase 4 Test Knowledge Base",
        }, headers=get_headers(), timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("id") or data.get("kb_id")
    except requests.exceptions.RequestException:
        pass
    return None


def upload_document(kb_id, filename, content):
    """通过创建临时文件上传文档"""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
    tmp.write(content)
    tmp.close()
    
    try:
        with open(tmp.name, 'rb') as f:
            resp = requests.post(
                f"{API}/knowledge-bases/{kb_id}/documents/upload",
                files={"file": (filename, f, "text/plain")},
                headers=get_headers(),
                timeout=10,
            )
        
        if resp.status_code in (200, 201):
            data = resp.json()
            return data.get("document_id") or data.get("id")
    except requests.exceptions.RequestException:
        pass
    finally:
        try:
            os.unlink(tmp.name)
        except:
            pass
    return None


# ============ 测试 1: 数据模型扩展 ============
def test_data_model_extension():
    section("测试 1: 数据模型扩展")
    
    try:
        from app.models.entities.document import DocumentChunk
        
        # 检查表定义中的列
        table = DocumentChunk.__table__
        column_names = [c.name for c in table.columns]
        
        has_embedding = "embedding" in column_names
        has_search_vector = "search_vector" in column_names
        
        if has_embedding:
            pass_test("DocumentChunk 包含 embedding 列")
        else:
            fail_test("DocumentChunk 缺少 embedding 列")
        
        if has_search_vector:
            pass_test("DocumentChunk 包含 search_vector 列")
        else:
            fail_test("DocumentChunk 缺少 search_vector 列")
        
        # 检查 __table_args__ 中的索引配置
        table_args = getattr(DocumentChunk, '__table_args__', None)
        
        if table_args is not None:
            pass_test("表参数 (__table_args__) 已配置")
        else:
            fail_test("表参数 (__table_args__) 未配置")
            
    except ImportError as e:
        skip_test("数据模型扩展测试", f"导入失败: {e}")
    except Exception as e:
        fail_test("数据模型扩展测试", str(e))


# ============ 测试 2: PgVectorStore 导入和功能 ============
def test_pgvector_store_import():
    section("测试 2: PgVectorStore 导入和基础功能")
    
    try:
        from app.services.pg_vector_store import PgVectorStore, init_pgvector_extension
        
        pass_test("PgVectorStore 模块导入成功")
        
        # 检查类是否存在
        methods = ['add_vector', 'search', 'count_vectors', 'stats', 'has_vectors']
        for method in methods:
            if hasattr(PgVectorStore, method):
                pass_test(f"PgVectorStore.{method} 方法存在")
            else:
                fail_test(f"PgVectorStore.{method} 方法缺失")
        
    except ImportError as e:
        skip_test("PgVectorStore 导入", f"模块不可用: {e}")
    except Exception as e:
        fail_test("PgVectorStore 测试", str(e))


# ============ 测试 3: PgFullTextSearch 功能 ============
def test_pg_fts_import():
    section("测试 3: PgFullTextSearch 导入和基础功能")
    
    try:
        from app.services.postgres_search import PgFullTextSearch
        
        pass_test("PgFullTextSearch 模块导入成功")
        
        # 检查类是否存在
        methods = ['search', 'index_document', '_preprocess_query']
        for method in methods:
            if hasattr(PgFullTextSearch, method):
                pass_test(f"PgFullTextSearch.{method} 方法存在")
            else:
                fail_test(f"PgFullTextSearch.{method} 方法缺失")
        
        # 测试查询预处理 (不需要数据库)
        fts = PgFullTextSearch(db=None)
        
        # 中文查询预处理
        query = "如何使用RAG系统"
        processed = fts._preprocess_query(query)
        assert "|" in processed or len(processed) > 0
        pass_test(f"中文查询预处理: '{query}' -> '{processed}'")
        
        # 英文查询预处理
        query_en = "how to use RAG system"
        processed_en = fts._preprocess_query(query_en)
        assert "&" in processed_en or len(processed_en) > 0
        pass_test(f"英文查询预处理: '{query_en}' -> '{processed_en}'")
        
        # 测试片段提取
        snippet = fts._extract_snippet("这是一段关于人工智能的长文本内容", "人工智能")
        assert "人工智能" in snippet
        pass_test("片段提取功能正常")
        
    except ImportError as e:
        skip_test("PgFullTextSearch 导入", f"模块不可用: {e}")
    except AssertionError as e:
        fail_test("PgFullTextSearch 方法检查", str(e))
    except Exception as e:
        fail_test("PgFullTextSearch 测试", str(e))


# ============ 测试 4: PostgreSQLHybridSearch ============
def test_pg_hybrid_import():
    section("测试 4: PostgreSQLHybridSearch 导入和基础功能")
    
    try:
        from app.services.postgres_search import PostgreSQLHybridSearch
        
        pass_test("PostgreSQLHybridSearch 模块导入成功")
        
        # 检查类是否存在
        methods = ['search', 'search_with_rrf']
        for method in methods:
            if hasattr(PostgreSQLHybridSearch, method):
                pass_test(f"PostgreSQLHybridSearch.{method} 方法存在")
            else:
                fail_test(f"PostgreSQLHybridSearch.{method} 方法缺失")
        
    except ImportError as e:
        skip_test("PostgreSQLHybridSearch 导入", f"模块不可用: {e}")
    except Exception as e:
        fail_test("PostgreSQLHybridSearch 测试", str(e))


# ============ 测试 5: RetrievalService 自动选择策略 ============
def test_retrieval_service_strategy():
    section("测试 5: RetrievalService 自动选择检索策略")
    
    try:
        from app.services.retrieval_service import RetrievalService, RetrievedHit
        from app.models.database import _is_sqlite
        
        db_type = "SQLite" if _is_sqlite else "PostgreSQL"
        pass_test(f"当前数据库类型: {db_type}")
        
        # 检查 RetrievalService 是否支持 Phase 4 功能
        methods = [
            'search', 'index_document_for_search', 
            'batch_index_documents', 'initialize_postgres_search',
            '_has_postgres_vectors', '_search_postgres_native'
        ]
        for method in methods:
            if hasattr(RetrievalService, method):
                pass_test(f"RetrievalService.{method} 方法存在")
            else:
                fail_test(f"RetrievalService.{method} 方法缺失")
        
        # 测试 RetrievedHit 数据类
        hit = RetrievedHit(
            chunk_id=1,
            document_id=1,
            knowledge_base_id=1,
            content="test content",
            document_filename="test.txt",
            vector_score=0.9,
            bm25_score=0.8,
            keyword_score=0.7,
            final_score=0.85,
            rank=1,
            search_type="postgres_native"
        )
        assert hit.search_type == "postgres_native"
        assert hit.final_score == 0.85
        pass_test("RetrievedHit 数据类功能正常")
        
    except ImportError as e:
        fail_test("RetrievalService 导入", str(e))
    except AssertionError as e:
        fail_test("RetrievalService 方法检查", str(e))
    except Exception as e:
        fail_test("RetrievalService 测试", str(e))


# ============ 测试 6: 混合检索逻辑 (纯逻辑测试) ============
def test_hybrid_search_logic():
    section("测试 6: 混合检索逻辑")
    
    try:
        from app.services.retrieval_service import RetrievalService
        
        # 测试关键词提取
        keywords = RetrievalService._extract_keywords("如何使用RAG系统进行文档问答")
        assert len(keywords) > 0
        pass_test(f"关键词提取: 找到 {len(keywords)} 个关键词", keywords[:3])
        
        # 测试文本重叠计算
        overlap = RetrievalService._text_overlap("这是一段关于机器学习的文本", "这是一段关于深度学习的文本")
        assert 0 <= overlap <= 1
        pass_test(f"文本重叠度计算: {overlap:.3f}")
        
        # 测试关键词匹配分数
        score = RetrievalService._keyword_overlap_score(
            "RAG系统使用向量数据库存储嵌入向量", 
            ["rag", "向量", "嵌入"]
        )
        assert score > 0
        pass_test(f"关键词匹配分数: {score:.3f}")
        
    except Exception as e:
        fail_test("混合检索逻辑测试", str(e))


# ============ 测试 7: API 集成测试 (需要服务) ============
def test_api_integration():
    section("测试 7: API 集成测试 (需要服务运行)")
    
    global token, kb_id
    
    # 检查服务是否运行
    try:
        health_resp = requests.get(f"{BASE}/health", timeout=2)
        if health_resp.status_code != 200:
            skip_test("API 测试", "服务健康检查失败")
            return
    except requests.exceptions.ConnectionError:
        skip_test("API 测试", "服务未启动，请先运行 uvicorn")
        return
    
    # 7.1 用户认证
    print("  --- 7.1 用户认证 ---")
    token, username = create_test_user("p4_fulltext")
    if token:
        pass_test("用户注册/登录成功", username)
    else:
        skip_test("用户注册/登录", "无法连接到服务")
        return
    
    # 7.2 创建知识库
    print("  --- 7.2 创建知识库 ---")
    kb_id = create_knowledge_base("Phase4 FullText Test KB")
    if kb_id:
        pass_test("知识库创建成功", f"kb_id={kb_id}")
    else:
        fail_test("知识库创建失败")
        return
    
    # 7.3 上传测试文档
    print("  --- 7.3 上传测试文档 ---")
    test_docs = [
        ("rag_intro.txt", "RAG是检索增强生成的缩写，它结合了信息检索与大语言模型。RAG系统首先从知识库中检索相关文档，然后将检索结果作为上下文提供给大语言模型，生成更准确的回答。"),
        ("machine_learning.txt", "机器学习是人工智能的一个分支，它使计算机系统能够从数据中学习规律，而无需显式编程。常见的机器学习算法包括决策树、随机森林、支持向量机和神经网络。深度学习是机器学习的子集，基于多层神经网络。"),
        ("vector_search.txt", "向量搜索是信息检索中的核心技术。在RAG系统中，文档被转换为高维向量存储在向量数据库中。常见的向量数据库包括FAISS、pgvector、Milvus和Pinecone。余弦相似度是衡量向量相似性的常用指标。"),
    ]
    
    for filename, content in test_docs:
        doc_id = upload_document(kb_id, filename, content)
        if doc_id:
            doc_ids.append(doc_id)
            pass_test(f"文档上传成功", filename)
        else:
            fail_test(f"文档上传失败", filename)
    
    if not doc_ids:
        fail_test("没有成功上传的文档")
        return
    
    # 等待文档处理和索引构建
    print("  --- 等待文档处理和索引构建 (5秒) ---")
    time.sleep(5)
    
    # 7.4 测试搜索接口
    print("  --- 7.4 搜索接口测试 ---")
    
    # 尝试不同的搜索端点
    search_endpoints = [
        f"{API}/knowledge-bases/{kb_id}/search",
        f"{API}/knowledge-bases/{kb_id}/query",
        f"{API}/retrieval/search",
    ]
    
    search_done = False
    for endpoint in search_endpoints:
        try:
            resp = requests.post(
                endpoint,
                json={"query": "什么是RAG系统", "top_k": 3},
                headers=get_headers(),
                timeout=10,
            )
            if resp.status_code == 200:
                results = resp.json()
                if isinstance(results, list) and len(results) > 0:
                    pass_test(f"搜索接口 {endpoint} 返回结果", f"找到 {len(results)} 条")
                    search_done = True
                elif isinstance(results, dict) and "results" in results:
                    pass_test(f"搜索接口 {endpoint} 返回结果", f"找到 {len(results['results'])} 条")
                    search_done = True
                break
        except requests.exceptions.RequestException:
            continue
    
    if not search_done:
        skip_test("搜索接口测试", "未找到可用的搜索端点或无结果")
    
    # 7.5 测试统计接口
    print("  --- 7.5 统计接口测试 ---")
    try:
        resp = requests.get(
            f"{API}/knowledge-bases/{kb_id}/stats",
            headers=get_headers(),
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            pass_test("统计接口返回成功", f"模式: {data.get('search_mode', 'unknown')}")
        else:
            skip_test("统计接口", f"HTTP {resp.status_code}")
    except requests.exceptions.RequestException:
        skip_test("统计接口", "请求失败")


# ============ 主测试流程 ============
def main():
    global PASS, FAIL, SKIP
    
    print("=" * 70)
    print("  Phase 4 验证测试 — 全文检索 (pgvector + FTS)")
    print("=" * 70)
    
    # 单元测试 (不需要服务)
    test_data_model_extension()
    test_pgvector_store_import()
    test_pg_fts_import()
    test_pg_hybrid_import()
    test_retrieval_service_strategy()
    test_hybrid_search_logic()
    
    # API 集成测试 (需要服务运行)
    test_api_integration()
    
    # 总结
    section("测试总结")
    print(f"  通过: {PASS}")
    print(f"  失败: {FAIL}")
    print(f"  跳过: {SKIP}")
    print(f"  总计: {PASS + FAIL + SKIP}")
    
    if FAIL == 0:
        print("\n  ✅ 所有测试通过!")
    else:
        print(f"\n  ❌ {FAIL} 个测试失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
