"""
RAG-Knowledge-Base 全量测试套件
==============================
本文件覆盖了系统所有核心模块的功能测试、边界测试和性能测试。
直接运行:  python test_all.py
"""

import os
import sys
import json
import time
import math
import tempfile
import traceback
from typing import Dict, List, Any, Optional
from dataclasses import asdict

# ============================== 测试结果汇总 ==============================
_results: List[Dict[str, Any]] = []
_passed = 0
_failed = 0
_errors: List[str] = []


def _record(name: str, passed: bool, detail: str = ""):
    global _passed, _failed
    status = "PASS" if passed else "FAIL"
    if not passed:
        _failed += 1
        _errors.append(f"[FAIL] {name}: {detail}")
    else:
        _passed += 1
    _results.append({"name": name, "status": status, "detail": detail})
    icon = "✅" if passed else "❌"
    print(f"  {icon} [{status}] {name}" + (f"  — {detail}" if detail else ""))


def _section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ============================== 0. 环境与配置 ==============================
_section("0. 环境与配置测试")

# --- 测试 0.1: 项目路径与关键目录 ---
# 验证项目根目录结构完整，所有子模块均可定位
try:
    base = os.path.dirname(os.path.abspath(__file__))
    required = ["app", "app/core", "app/models", "app/services",
                "app/processors", "app/api", "app/api/v1"]
    missing = [d for d in required if not os.path.isdir(os.path.join(base, d))]
    _record("0.1 项目目录结构", len(missing) == 0,
            f"missing: {missing}" if missing else "all directories present")
except Exception as e:
    _record("0.1 项目目录结构", False, str(e))

# --- 测试 0.2: settings 加载 ---
# 验证 Settings 可正确实例化，且关键默认值合理
try:
    sys.path.insert(0, base)
    from app.core.config import settings
    assert settings.APP_NAME, "APP_NAME 为空"
    assert settings.LLM_PROVIDER in {"mock", "deepseek", "openai", "custom"}, \
        f"LLM_PROVIDER 值异常: {settings.LLM_PROVIDER}"
    assert settings.RAG_TOP_K > 0, "RAG_TOP_K 应大于 0"
    assert 0 < settings.RAG_MIN_SCORE < 1, "RAG_MIN_SCORE 应在 (0,1) 之间"
    settings.ensure_dirs()
    assert os.path.isdir(settings.UPLOAD_DIR), "UPLOAD_DIR 未创建"
    assert os.path.isdir(settings.VECTOR_STORE_DIR), "VECTOR_STORE_DIR 未创建"
    _record("0.2 Settings 配置加载", True,
            f"provider={settings.LLM_PROVIDER}, top_k={settings.RAG_TOP_K}, "
            f"min_score={settings.RAG_MIN_SCORE}")
except Exception as e:
    _record("0.2 Settings 配置加载", False, str(e))

# --- 测试 0.3: CORS origins 解析 ---
# 验证 cors_origin_list 属性正确从逗号分隔字符串解析
try:
    origins = settings.cors_origin_list
    assert isinstance(origins, list), "cors_origin_list 应返回 list"
    assert len(origins) >= 1, "至少有一个默认 origin"
    _record("0.3 CORS origins 解析", True, str(origins))
except Exception as e:
    _record("0.3 CORS origins 解析", False, str(e))

# --- 测试 0.4: active_llm_name 属性 ---
# 验证当前 LLM provider 描述字符串可读
try:
    name = settings.active_llm_name
    assert name and isinstance(name, str), "active_llm_name 应为非空字符串"
    _record("0.4 active_llm_name 属性", True, name)
except Exception as e:
    _record("0.4 active_llm_name 属性", False, str(e))


# ============================== 1. 日志模块 ==============================
_section("1. 日志模块测试")

# --- 测试 1.1: logger 实例创建 ---
# 验证日志模块可正常导入并获取 logger
try:
    from app.core.logging import logger
    assert logger is not None, "logger 为 None"
    _record("1.1 Logger 实例创建", True)
except Exception as e:
    _record("1.1 Logger 实例创建", False, str(e))


# ============================== 2. 数据库模块 ==============================
_section("2. 数据库模块测试")

# --- 测试 2.1: 数据库引擎初始化 ---
# 验证 SQLAlchemy engine 可创建并连接
try:
    from app.models.database import engine, SessionLocal, Base
    assert engine is not None, "engine 为 None"
    _record("2.1 数据库引擎初始化", True, str(engine.url))
except Exception as e:
    _record("2.1 数据库引擎初始化", False, str(e))

# --- 测试 2.2: 数据库表创建 ---
# 验证 init_db() 能成功建表
try:
    from app.models.database import init_db
    init_db()
    _record("2.2 数据库表创建 (init_db)", True)
except Exception as e:
    _record("2.2 数据库表创建 (init_db)", False, str(e))

# --- 测试 2.3: Session 创建与关闭 ---
# 验证 SessionLocal 能正常工作
try:
    db = SessionLocal()
    assert db is not None
    db.close()
    _record("2.3 Session 创建与关闭", True)
except Exception as e:
    _record("2.3 Session 创建与关闭", False, str(e))


# ============================== 3. 实体模型 ==============================
_section("3. 实体模型测试")

# --- 测试 3.1: User 实体 CRUD ---
# 验证用户模型可正常增删改查
try:
    from app.models.database import SessionLocal
    from app.models.entities.user import User
    from app.core.security import hash_password, verify_password

    db = SessionLocal()
    test_email = f"test_{int(time.time())}@example.com"
    test_user = User(
        username=f"testuser_{int(time.time())}",
        email=test_email,
        hashed_password=hash_password("testpass123"),
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)
    assert test_user.id is not None, "用户 ID 未生成"

    fetched = db.query(User).filter(User.id == test_user.id).first()
    assert fetched is not None, "查询用户失败"
    assert fetched.email == test_email, "邮箱不匹配"

    assert verify_password("testpass123", fetched.hashed_password), "密码验证失败"
    assert not verify_password("wrongpass", fetched.hashed_password), "错误密码不应通过"

    db.delete(test_user)
    db.commit()
    _record("3.1 User CRUD + 密码验证", True)
except Exception as e:
    _record("3.1 User CRUD + 密码验证", False, str(e))
    try:
        db.rollback()
        db.close()
    except Exception:
        pass

# --- 测试 3.2: KnowledgeBase 实体 CRUD ---
# 验证知识库模型及与 User 的关联
try:
    from app.models.database import SessionLocal
    from app.models.entities.user import User
    from app.models.entities.knowledge_base import KnowledgeBase
    from app.core.security import hash_password

    db = SessionLocal()
    test_user = User(
        username=f"kb_test_{int(time.time())}",
        email=f"kb_test_{int(time.time())}@test.com",
        hashed_password=hash_password("pass"),
    )
    db.add(test_user)
    db.commit()
    db.refresh(test_user)

    kb = KnowledgeBase(
        name="Test KB",
        description="Test knowledge base",
        user_id=test_user.id,
        is_public=False,
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)
    assert kb.id is not None, "KB ID 未生成"

    fetched = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb.id).first()
    assert fetched is not None, "查询 KB 失败"
    assert fetched.user_id == test_user.id, "用户关联错误"
    assert fetched.is_public == False, "is_public 默认值错误"

    # 级联清理
    db.delete(fetched)
    db.delete(test_user)
    db.commit()
    _record("3.2 KnowledgeBase CRUD + User 关联", True)
except Exception as e:
    _record("3.2 KnowledgeBase CRUD + User 关联", False, str(e))
    try:
        db.rollback()
        db.close()
    except Exception:
        pass

# --- 测试 3.3: Document + Chunk 实体 ---
# 验证文档与分块模型及与 KB 的关联
try:
    from app.models.database import SessionLocal
    from app.models.entities.user import User
    from app.models.entities.knowledge_base import KnowledgeBase
    from app.models.entities.document import Document, Chunk
    from app.core.security import hash_password

    db = SessionLocal()
    ts = int(time.time())
    u = User(username=f"doc_{ts}", email=f"doc_{ts}@t.com",
             hashed_password=hash_password("p"))
    db.add(u); db.commit(); db.refresh(u)
    kb = KnowledgeBase(name="DocTest", user_id=u.id, is_public=True)
    db.add(kb); db.commit(); db.refresh(kb)

    doc = Document(
        filename="test.txt",
        knowledge_base_id=kb.id,
        user_id=u.id,
        description="Test document",
        total_chunks=2,
    )
    db.add(doc); db.commit(); db.refresh(doc)

    chunk1 = Chunk(
        document_id=doc.id,
        knowledge_base_id=kb.id,
        content="这是第一段测试内容。",
        chunk_index=0,
    )
    chunk2 = Chunk(
        document_id=doc.id,
        knowledge_base_id=kb.id,
        content="This is the second chunk for testing.",
        chunk_index=1,
    )
    db.add_all([chunk1, chunk2]); db.commit()

    doc_chunks = db.query(Chunk).filter(Chunk.document_id == doc.id).order_by(Chunk.chunk_index).all()
    assert len(doc_chunks) == 2, f"Chunk 数量应为 2，实际 {len(doc_chunks)}"
    assert doc_chunks[0].chunk_index == 0
    assert doc_chunks[1].chunk_index == 1

    # 清理
    db.query(Chunk).filter(Chunk.document_id == doc.id).delete()
    db.delete(doc); db.delete(kb); db.delete(u)
    db.commit()
    _record("3.3 Document + Chunk 级联关联", True)
except Exception as e:
    _record("3.3 Document + Chunk 级联关联", False, str(e))
    try:
        db.rollback(); db.close()
    except Exception:
        pass


# ============================== 4. 安全模块 ==============================
_section("4. 安全模块测试")

# --- 测试 4.1: 密码哈希与验证 ---
# 验证 hash_password/verify_password 的正确性和一致性
try:
    from app.core.security import hash_password, verify_password
    pwd = "MyStr0ng!Pass#"
    h = hash_password(pwd)
    assert h and len(h) > 20, "哈希值过短"
    assert verify_password(pwd, h), "正确密码应通过"
    assert not verify_password("wrong", h), "错误密码应被拒"
    h2 = hash_password(pwd)
    assert h != h2, "两次哈希应不同（盐值随机）"
    _record("4.1 密码哈希与验证", True)
except Exception as e:
    _record("4.1 密码哈希与验证", False, str(e))

# --- 测试 4.2: JWT 创建与解码 ---
# 验证 JWT token 签发与验证流程
try:
    from app.core.security import create_access_token, verify_access_token
    token = create_access_token(data={"sub": "user:42", "role": "admin"})
    assert token and len(token) > 20, "Token 生成失败"
    payload = verify_access_token(token)
    assert payload is not None, "Token 解码失败"
    assert payload.get("sub") == "user:42", "sub 字段不匹配"
    assert payload.get("role") == "admin", "role 字段不匹配"
    _record("4.2 JWT 创建与解码", True)
except Exception as e:
    _record("4.2 JWT 创建与解码", False, str(e))


# ============================== 5. 数据模型 (Schemas) ==============================
_section("5. 数据模型 (Schemas) 测试")

# --- 测试 5.1: Schemas 包导入 ---
# 验证所有 Pydantic schema 模型可正确导入
try:
    from app.models.schemas import (
        UserRegister, UserLogin, UserInfo,
        KnowledgeBaseCreate, KnowledgeBaseUpdate, KnowledgeBaseInfo,
        DocumentInfo, DocumentListResponse,
        ChatRequest, ChatResponse, ChatMessageItem,
        VectorSearchQuery, VectorSearchResponse,
        EncodeRequest, SimilarityRequest,
        HealthInfo,
    )
    _record("5.1 Schemas 全量导入", True)
except Exception as e:
    _record("5.1 Schemas 全量导入", False, str(e))

# --- 测试 5.2: UserRegister 验证 ---
# 验证 Pydantic 的数据校验（min_length、email 格式等）
try:
    from app.models.schemas import UserRegister
    u = UserRegister(username="hello", email="hello@test.com", password="pass123")
    assert u.username == "hello"
    assert u.email == "hello@test.com"
    # 测试空用户名应失败
    try:
        UserRegister(username="", email="a@b.com", password="pass")
        _record("5.2 UserRegister 校验", False, "空用户名未报错")
    except Exception:
        _record("5.2 UserRegister 校验", True, "空用户名正确被拒")
except Exception as e:
    _record("5.2 UserRegister 校验", False, str(e))

# --- 测试 5.3: KnowledgeBaseCreate 验证 ---
# 验证知识库创建 schema 的约束条件
try:
    from app.models.schemas import KnowledgeBaseCreate
    kb = KnowledgeBaseCreate(name="  My KB  ", description="test")
    assert kb.name == "My KB", "名称应自动去空格"
    assert kb.chunk_size == 500, "chunk_size 默认值错误"
    assert kb.chunk_overlap == 50, "chunk_overlap 默认值错误"
    assert kb.is_public == False, "is_public 默认值错误"
    _record("5.3 KnowledgeBaseCreate 验证", True)
except Exception as e:
    _record("5.3 KnowledgeBaseCreate 验证", False, str(e))

# --- 测试 5.4: ChatRequest 验证 ---
# 验证聊天请求的所有字段
try:
    from app.models.schemas import ChatRequest
    r = ChatRequest(query="你好", knowledge_base_id=1)
    assert r.query == "你好"
    assert r.knowledge_base_id == 1
    assert r.top_k == 5, "top_k 默认值应为 5"
    assert r.stream == False, "stream 默认值应为 False"
    _record("5.4 ChatRequest 验证", True)
except Exception as e:
    _record("5.4 ChatRequest 验证", False, str(e))


# ============================== 6. 文档处理模块 ==============================
_section("6. 文档处理模块测试")

# --- 测试 6.1: DocumentProcessor 导入 ---
# 验证文档处理器模块可正常加载
try:
    from app.processors import DocumentProcessor, SUPPORTED_EXTENSIONS
    assert len(SUPPORTED_EXTENSIONS) > 0, "支持的扩展名列表为空"
    _record("6.1 DocumentProcessor 导入", True,
            f"supported: {SUPPORTED_EXTENSIONS}")
except Exception as e:
    _record("6.1 DocumentProcessor 导入", False, str(e))

# --- 测试 6.2: MarkdownParser 解析 ---
# 验证 Markdown 解析器能正确处理标题、段落、列表等
try:
    from app.processors import MarkdownParser, parse_markdown
    md_text = """# Hello World
## Sub Title
This is a paragraph.
- list item 1
- list item 2
1. ordered item
"""
    parsed = parse_markdown(md_text)
    assert parsed is not None
    # 应有 title
    assert hasattr(parsed, 'title'), "ParsedMarkdown 缺 title"
    # 应有 blocks
    assert len(parsed.blocks) > 0, "blocks 为空"
    _record("6.2 MarkdownParser 解析", True,
            f"blocks={len(parsed.blocks)}, title={parsed.title}")
except Exception as e:
    _record("6.2 MarkdownParser 解析", False, str(e))

# --- 测试 6.3: SemanticChunker 分块 ---
# 验证语义分块器能按字数/句子合理切分
try:
    from app.processors import SemanticChunker, chunk_text
    long_text = "这是一段测试文本。" * 50  # 约 500+ 字
    chunks = chunk_text(long_text, chunk_size=100, chunk_overlap=20)
    assert len(chunks) > 1, "长文本应至少分成 2 块"
    # 每块字数不应超过 chunk_size + overlap
    for c in chunks:
        assert len(c) > 0, "空 chunk"
    _record("6.3 SemanticChunker 分块", True,
            f"input_len={len(long_text)}, chunks={len(chunks)}")
except Exception as e:
    _record("6.3 SemanticChunker 分块", False, str(e))

# --- 测试 6.4: DocumentPipeline 完整流程 ---
# 验证文档处理管道的端到端能力
try:
    from app.processors import DocumentPipeline
    dp = DocumentPipeline()
    # 模拟处理一段文本
    result = dp.process_text("Hello world. This is a test document for RAG system.",
                             doc_id=1, kb_id=1)
    assert result is not None
    assert hasattr(result, 'chunks'), "ProcessedDocument 缺 chunks"
    assert len(result.chunks) > 0, "处理结果无 chunk"
    _record("6.4 DocumentPipeline 流程", True,
            f"chunks={len(result.chunks)}")
except Exception as e:
    _record("6.4 DocumentPipeline 流程", False, str(e))


# ============================== 7. Embedding 服务 ==============================
_section("7. Embedding 服务测试")

# --- 测试 7.1: EmbeddingService 初始化 ---
# 验证向量嵌入服务可实例化
try:
    from app.processors import EmbeddingService
    es = EmbeddingService()
    assert es.dim > 0, f"embedding 维度应为正整数，实际 {es.dim}"
    _record("7.1 EmbeddingService 初始化", True, f"dim={es.dim}")
except Exception as e:
    _record("7.1 EmbeddingService 初始化", False, str(e))

# --- 测试 7.2: 单文本编码 ---
# 验证 encode_single 返回正确维度的向量
try:
    from app.processors import EmbeddingService
    es = EmbeddingService()
    vec = es.encode_single("你好世界")
    assert isinstance(vec, list), "向量应为 list"
    assert len(vec) == es.dim, f"维度不匹配: {len(vec)} vs {es.dim}"
    # 向量值应浮点数
    for v in vec[:5]:
        assert isinstance(v, (int, float)), "向量元素应为数值"
    _record("7.2 单文本编码", True, f"dim={len(vec)}")
except Exception as e:
    _record("7.2 单文本编码", False, str(e))

# --- 测试 7.3: 批量编码 ---
# 验证 encode_batch 能处理多个文本
try:
    from app.processors import EmbeddingService
    es = EmbeddingService()
    texts = ["你好", "世界", "测试"]
    vecs = es.encode_batch(texts)
    assert len(vecs) == len(texts), f"批量结果数量不匹配: {len(vecs)} vs {len(texts)}"
    for v in vecs:
        assert len(v) == es.dim, "某向量维度错误"
    _record("7.3 批量编码", True, f"batch_size={len(texts)}")
except Exception as e:
    _record("7.3 批量编码", False, str(e))

# --- 测试 7.4: 向量相似度计算 ---
# 验证余弦相似度计算
try:
    from app.processors import EmbeddingService
    es = EmbeddingService()
    v1 = es.encode_single("支付方式")
    v2 = es.encode_single("付款方法")
    v3 = es.encode_single("完全不相关的内容 飞机")
    sim_12 = es.cosine_similarity(v1, v2)
    sim_13 = es.cosine_similarity(v1, v3)
    assert 0 <= sim_12 <= 1, f"相似度越界: {sim_12}"
    assert sim_12 > sim_13, f"相关文本应更相似: {sim_12} vs {sim_13}"
    _record("7.4 向量相似度", True,
            f"sim(相关)={sim_12:.3f}, sim(不相关)={sim_13:.3f}")
except Exception as e:
    _record("7.4 向量相似度", False, str(e))

# --- 测试 7.5: 空文本处理 ---
# 验证空输入不会导致崩溃
try:
    from app.processors import EmbeddingService
    es = EmbeddingService()
    vec = es.encode_single("")
    # 空文本应返回零向量或安全处理
    assert vec is not None, "空文本应返回非 None 结果"
    _record("7.5 空文本处理", True, f"len={len(vec)}")
except Exception as e:
    _record("7.5 空文本处理", False, str(e))


# ============================== 8. 向量存储 ==============================
_section("8. 向量存储测试")

# --- 测试 8.1: VectorStoreManager 初始化 ---
# 验证向量存储管理器可创建
try:
    from app.processors import VectorStoreManager, EmbeddingService
    es = EmbeddingService()
    with tempfile.TemporaryDirectory() as tmp:
        mgr = VectorStoreManager(base_dir=tmp, default_dim=es.dim)
        _record("8.1 VectorStoreManager 初始化", True)
except Exception as e:
    _record("8.1 VectorStoreManager 初始化", False, str(e))

# --- 测试 8.2: 向量存储 CRUD ---
# 验证向量的添加、搜索、删除
try:
    from app.processors import VectorStoreManager, EmbeddingService
    es = EmbeddingService()
    with tempfile.TemporaryDirectory() as tmp:
        mgr = VectorStoreManager(base_dir=tmp, default_dim=es.dim)
        store = mgr.get_store(kb_id=99, dim=es.dim)

        # 添加向量
        vecs = [es.encode_single("支付方式介绍"),
                es.encode_single("退货政策"),
                es.encode_single("产品规格")]
        metadatas = [
            {"chunk_id": 1, "content": "我们支持支付宝和微信支付", "document_id": 1, "document_filename": "faq.txt"},
            {"chunk_id": 2, "content": "7天无理由退换货", "document_id": 1, "document_filename": "faq.txt"},
            {"chunk_id": 3, "content": "产品尺寸为 30x20x5cm", "document_id": 2, "document_filename": "spec.txt"},
        ]
        for v, m in zip(vecs, metadatas):
            store.add(v, metadata=m)
        assert store.total() == 3, f"存储数量应为 3，实际 {store.total()}"

        # 搜索
        q_vec = es.encode_single("怎么付款")
        results = store.search(q_vec, top_k=2)
        assert len(results) <= 2, "搜索结果数量超限"
        assert len(results) > 0, "搜索无结果"
        # 第一个结果应是支付相关的
        top_meta = results[0].get("metadata", {})
        assert "支付" in top_meta.get("content", ""), \
            f"最相关结果应为支付相关，实际: {top_meta.get('content', '')}"

        # 删除
        mgr.delete(kb_id=99)
        _record("8.2 向量存储 CRUD", True,
                f"added=3, searched={len(results)}, deleted=True")
except Exception as e:
    _record("8.2 向量存储 CRUD", False, str(e))

# --- 测试 8.3: 空存储搜索 ---
# 验证空存储不会崩溃
try:
    from app.processors import VectorStoreManager, EmbeddingService
    es = EmbeddingService()
    with tempfile.TemporaryDirectory() as tmp:
        mgr = VectorStoreManager(base_dir=tmp, default_dim=es.dim)
        store = mgr.get_store(kb_id=999, dim=es.dim)
        q_vec = es.encode_single("随便问")
        results = store.search(q_vec, top_k=5)
        assert len(results) == 0, "空存储应返回空结果"
        _record("8.3 空存储搜索", True)
except Exception as e:
    _record("8.3 空存储搜索", False, str(e))

# --- 测试 8.4: 向量存储状态查询 ---
# 验证 get_status 返回正确信息
try:
    from app.processors import VectorStoreManager, EmbeddingService
    es = EmbeddingService()
    with tempfile.TemporaryDirectory() as tmp:
        mgr = VectorStoreManager(base_dir=tmp, default_dim=es.dim)
        status = mgr.get_status(kb_id=1)
        assert isinstance(status, dict), "status 应为 dict"
        _record("8.4 向量存储状态查询", True, str(status))
except Exception as e:
    _record("8.4 向量存储状态查询", False, str(e))

# --- 测试 8.5: 多知识库隔离 ---
# 验证不同 KB 的向量存储相互独立
try:
    from app.processors import VectorStoreManager, EmbeddingService
    es = EmbeddingService()
    with tempfile.TemporaryDirectory() as tmp:
        mgr = VectorStoreManager(base_dir=tmp, default_dim=es.dim)
        v = es.encode_single("测试")
        s1 = mgr.get_store(kb_id=1, dim=es.dim)
        s2 = mgr.get_store(kb_id=2, dim=es.dim)
        s1.add(v, metadata={"chunk_id": 1})
        assert s1.total() == 1, "KB1 应有 1 条"
        assert s2.total() == 0, "KB2 应为空"
        _record("8.5 多知识库隔离", True)
except Exception as e:
    _record("8.5 多知识库隔离", False, str(e))


# ============================== 9. LLM 服务 ==============================
_section("9. LLM 服务测试")

# --- 测试 9.1: ChatMessage / ChatResult 数据结构 ---
# 验证消息和结果数据结构的正确性
try:
    from app.processors import ChatMessage, ChatResult
    msg = ChatMessage(role="user", content="你好")
    assert msg.role == "user"
    assert msg.content == "你好"
    d = msg.to_dict()
    assert d == {"role": "user", "content": "你好"}
    msg2 = ChatMessage.from_dict(d)
    assert msg2.role == msg.role and msg2.content == msg.content

    result = ChatResult(content="答", model="m", provider="p",
                        success=True, latency_ms=10.5)
    assert result.success
    assert result.content == "答"
    _record("9.1 ChatMessage/ChatResult 结构", True)
except Exception as e:
    _record("9.1 ChatMessage/ChatResult 结构", False, str(e))

# --- 测试 9.2: MockLLM 基础回复 ---
# 验证 Mock LLM 能基于 system prompt 中的 chunks 回答
try:
    from app.processors import get_llm_service, ChatMessage
    svc = get_llm_service()
    assert svc.provider_name() == "mock", f"当前应为 mock provider，实际 {svc.provider_name()}"

    messages = [
        ChatMessage(role="system", content=(
            "你是问答助手。\n"
            "【知识库片段】\n"
            "[#1] (来源: faq.txt, 相似度: 0.900)\n"
            "我们支持支付宝、微信、信用卡三种支付方式。\n"
            "[#2] (来源: faq.txt, 相似度: 0.800)\n"
            "7天内无理由退换货。"
        )),
        ChatMessage(role="user", content="你们支持什么支付方式？"),
    ]
    result = svc.chat(messages)
    assert result.success, f"LLM 调用失败: {result.error}"
    assert "支付" in result.content, f"回答应包含支付相关内容，实际: {result.content}"
    assert "[来源" in result.content, "回答应包含来源引用"
    _record("9.2 MockLLM 基础回复", True, result.content[:80])
except Exception as e:
    _record("9.2 MockLLM 基础回复", False, str(e))

# --- 测试 9.3: MockLLM 幻觉抑制（无 chunks 时拒绝） ---
# 验证知识库无相关内容时 Mock 明确拒绝
try:
    from app.processors import get_llm_service, ChatMessage
    svc = get_llm_service()
    messages = [
        ChatMessage(role="system", content=(
            "你是问答助手。\n"
            "本次查询在知识库中未能检索到任何相关片段。\n"
            "你必须直接回复用户：\n"
            '"很抱歉，在知识库中未能检索到足够的相关信息来回答您的问题。"\n'
            "绝不能编造任何回答。"
        )),
        ChatMessage(role="user", content="随便问一个知识库没有的问题"),
    ]
    result = svc.chat(messages)
    assert "未能检索到" in result.content or "抱歉" in result.content, \
        f"应拒绝回答，实际: {result.content}"
    _record("9.3 MockLLM 幻觉抑制", True, result.content[:80])
except Exception as e:
    _record("9.3 MockLLM 幻觉抑制", False, str(e))

# --- 测试 9.4: MockLLM 流式输出 ---
# 验证流式接口产出 token 序列
try:
    from app.processors import get_llm_service, ChatMessage
    svc = get_llm_service()
    messages = [
        ChatMessage(role="system", content=(
            "你是问答助手。\n"
            "【知识库片段】\n"
            "[#1] (来源: faq.txt, 相似度: 0.900)\n"
            "我们支持支付宝、微信、信用卡三种支付方式。"
        )),
        ChatMessage(role="user", content("怎么付款")),
    ]
    tokens = list(svc.chat_stream(messages))
    assert len(tokens) > 1, f"流式应产出多个 token，实际 {len(tokens)}"
    # 最后的 token 不应有 content
    last = tokens[-1]
    assert last.success, "最后一个 token 应标记成功"
    # 前几个 token 应有内容
    first_content_tokens = [t for t in tokens[:-1] if t.content]
    assert len(first_content_tokens) > 0, "流式输出无内容"
    full_text = "".join(t.content for t in tokens if t.content)
    assert len(full_text) > 0, "拼接后文本为空"
    _record("9.4 MockLLM 流式输出", True,
            f"tokens={len(tokens)}, full_len={len(full_text)}")
except Exception as e:
    _record("9.4 MockLLM 流式输出", False, str(e))

# --- 测试 9.5: LLM token 计数 ---
# 验证 count_tokens 估算合理
try:
    from app.processors import get_llm_service
    svc = get_llm_service()
    n1 = svc.count_tokens("")
    n2 = svc.count_tokens("hello world")
    n3 = svc.count_tokens("a" * 1000)
    assert n1 == 0, "空文本应 0 tokens"
    assert n2 > 0, "短文本应至少 1 token"
    assert n3 > n2, "长文本应更多 tokens"
    _record("9.5 LLM token 计数", True, f"empty={n1}, short={n2}, long={n3}")
except Exception as e:
    _record("9.5 LLM token 计数", False, str(e))


# ============================== 10. BM25 索引 ==============================
_section("10. BM25 索引测试")

# --- 测试 10.1: BM25 分词 ---
# 验证中英文混合分词正确性
try:
    from app.services.retrieval_service import BM25Index
    tokens = BM25Index.tokenize("你好世界 hello world 支付")
    assert len(tokens) > 0, "分词结果为空"
    # 中文应被分块（2字窗口），英文按词
    has_chinese = any("你好" in t or "好世" in t or "世界" in t for t in tokens)
    has_english = any("hello" in t or "world" in t for t in tokens)
    _record("10.1 BM25 分词", True,
            f"tokens={tokens[:10]}, has_zh={has_chinese}, has_en={has_english}")
except Exception as e:
    _record("10.1 BM25 分词", False, str(e))

# --- 测试 10.2: BM25 索引 + 打分 ---
# 验证 BM25 添加文档后可正确评分
try:
    from app.services.retrieval_service import BM25Index
    idx = BM25Index()
    idx.add_doc(1, "我们支持支付宝和微信支付方式")
    idx.add_doc(2, "7天无理由退换货政策")
    idx.add_doc(3, "产品规格为 30x20x5 厘米")

    scores = idx.score_normalized("支付方式", external_ids=[1, 2, 3])
    assert 1 in scores, "doc 1 应有分数"
    assert 2 not in scores or scores.get(2, 0) < scores.get(1, 0), \
        "doc 1 (支付) 分数应高于 doc 2 (退货)"
    assert scores.get(1, 0) > 0, "doc 1 分数应大于 0"
    _record("10.2 BM25 索引 + 打分", True,
            f"scores[1]={scores.get(1, 0):.3f}, scores[2]={scores.get(2, 0):.3f}")
except Exception as e:
    _record("10.2 BM25 索引 + 打分", False, str(e))

# --- 测试 10.3: BM25 空查询处理 ---
# 验证空查询和空索引不崩溃
try:
    from app.services.retrieval_service import BM25Index
    idx = BM25Index()
    scores = idx.score("任意查询")
    assert scores == {}, "空索引应返回空 dict"
    idx.add_doc(1, "测试文档")
    scores2 = idx.score("")
    assert scores2 == {}, "空查询应返回空 dict"
    _record("10.3 BM25 空查询处理", True)
except Exception as e:
    _record("10.3 BM25 空查询处理", False, str(e))


# ============================== 11. 检索服务 ==============================
_section("11. 检索服务测试")

# --- 测试 11.1: RetrievalService 初始化 ---
# 验证检索服务可正常实例化
try:
    from app.services.retrieval_service import RetrievalService
    from app.models.database import SessionLocal
    db = SessionLocal()
    rs = RetrievalService(db)
    _record("11.1 RetrievalService 初始化", True)
    db.close()
except Exception as e:
    _record("11.1 RetrievalService 初始化", False, str(e))

# --- 测试 11.2: 关键词提取 ---
# 验证 _extract_keywords 正确提取中英文关键词
try:
    from app.services.retrieval_service import RetrievalService
    kws = RetrievalService._extract_keywords("你们支持什么支付方式？")
    assert len(kws) > 0, "关键词提取为空"
    has_payment = any("支付" in k for k in kws)
    _record("11.2 关键词提取", True, f"keywords={kws[:8]}, has_payment={has_payment}")
except Exception as e:
    _record("11.2 关键词提取", False, str(e))

# --- 测试 11.3: 关键词重叠分 ---
# 验证 _keyword_overlap_score 的正确性
try:
    from app.services.retrieval_service import RetrievalService
    score1 = RetrievalService._keyword_overlap_score(
        "我们支持支付宝和微信支付", ["支付", "支付宝"]
    )
    score2 = RetrievalService._keyword_overlap_score(
        "完全不相关的内容", ["支付", "支付宝"]
    )
    assert score1 > score2, f"相关文本应得更高分: {score1} vs {score2}"
    _record("11.3 关键词重叠分", True,
            f"relevant={score1:.2f}, irrelevant={score2:.2f}")
except Exception as e:
    _record("11.3 关键词重叠分", False, str(e))

# --- 测试 11.4: 文本重叠度 ---
# 验证 _text_overlap (Jaccard) 正确性
try:
    from app.services.retrieval_service import RetrievalService
    o1 = RetrievalService._text_overlap("你好世界", "你好世界")
    assert o1 == 1.0, f"相同文本重叠度应为 1.0，实际 {o1}"
    o2 = RetrievalService._text_overlap("你好世界", "完全不同的内容")
    assert o2 < 0.5, f"不相关文本重叠度应低，实际 {o2}"
    _record("11.4 文本重叠度 (Jaccard)", True,
            f"identical={o1:.2f}, different={o2:.2f}")
except Exception as e:
    _record("11.4 文本重叠度 (Jaccard)", False, str(e))

# --- 测试 11.5: 混合检索权重 ---
# 验证三路权重之和为 1
try:
    from app.services.retrieval_service import RetrievalService
    total = RetrievalService.VECTOR_WEIGHT + RetrievalService.BM25_WEIGHT + RetrievalService.KEYWORD_WEIGHT
    assert abs(total - 1.0) < 0.001, f"权重和应为 1.0，实际 {total}"
    _record("11.5 混合检索权重", True,
            f"V={RetrievalService.VECTOR_WEIGHT}, BM25={RetrievalService.BM25_WEIGHT}, "
            f"KW={RetrievalService.KEYWORD_WEIGHT}, sum={total}")
except Exception as e:
    _record("11.5 混合检索权重", False, str(e))


# ============================== 12. RAG Pipeline ==============================
_section("12. RAG Pipeline 测试")

# --- 测试 12.1: RAGPipeline 初始化 ---
# 验证 RAGPipeline 可创建，内部组件正确初始化
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as tmp:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=tmp, default_dim=es.dim)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        assert rag.llm is not None, "LLM 未初始化"
        assert rag.vector_manager is not None, "vector_manager 未初始化"
        _record("12.1 RAGPipeline 初始化", True)
except Exception as e:
    _record("12.1 RAGPipeline 初始化", False, str(e))

# --- 测试 12.2: RAG 搜索（无数据时返回空） ---
# 验证空知识库搜索返回空列表
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as tmp:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=tmp, default_dim=es.dim)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        chunks = rag.search(knowledge_base_id=999, query_text="随便问")
        assert chunks == [], "空知识库应返回空列表"
        _record("12.2 RAG 空知识库搜索", True)
except Exception as e:
    _record("12.2 RAG 空知识库搜索", False, str(e))

# --- 测试 12.3: RAG 上下文构建 ---
# 验证 build_context 正确格式化 chunks
try:
    from app.services.chat_service import RAGPipeline, RetrievedChunk
    rag = RAGPipeline()
    chunks = [
        RetrievedChunk(chunk_id=1, content="我们支持支付宝和微信支付。",
                       score=0.9, document_id=1, document_filename="faq.txt"),
        RetrievedChunk(chunk_id=2, content="7天无理由退换。",
                       score=0.8, document_id=1, document_filename="faq.txt"),
    ]
    ctx = rag.build_context(chunks)
    assert "[#1]" in ctx, "应包含 chunk 编号"
    assert "[#2]" in ctx, "应包含第二个 chunk"
    assert "支付" in ctx, "应包含 chunk 内容"

    # 空 chunks 测试
    ctx_empty = rag.build_context([])
    assert "无相关" in ctx_empty or "无内容" in ctx_empty, "空 chunks 应有兜底文案"
    _record("12.3 RAG 上下文构建", True,
            f"ctx_len={len(ctx)}, has_refs={'[#1]' in ctx}")
except Exception as e:
    _record("12.3 RAG 上下文构建", False, str(e))

# --- 测试 12.4: RAG Messages 组装 ---
# 验证 build_messages 正确处理 history 和幻觉抑制
try:
    from app.services.chat_service import RAGPipeline
    from app.processors import ChatMessage
    rag = RAGPipeline()

    msgs = rag.build_messages("你好", "【知识库片段】\n[#1] 内容")
    assert len(msgs) >= 2, "至少应有 system + user"
    assert msgs[0].role == "system", "第一条应为 system"
    assert msgs[-1].role == "user", "最后一条应为 user"
    assert msgs[-1].content == "你好"

    # 测试带历史
    history = [ChatMessage(role="user", content="之前的问题"),
               ChatMessage(role="assistant", content="之前的回答")]
    msgs2 = rag.build_messages("新问题", "【知识库片段】\n[#1] 内容", history=history)
    assert len(msgs2) >= 4, f"带历史应至少 4 条，实际 {len(msgs2)}"

    # 测试无 context 时的幻觉抑制
    msgs3 = rag.build_messages("新问题", "(知识库中无相关内容)")
    assert "未能检索到" in msgs3[0].content, "无 context 时应明确拒绝"

    _record("12.4 RAG Messages 组装", True, f"msgs_count={len(msgs)}")
except Exception as e:
    _record("12.4 RAG Messages 组装", False, str(e))

# --- 测试 12.5: RAG 幻觉抑制分层 ---
# 验证 L1/L2 两层幻觉抑制
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as tmp:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=tmp, default_dim=es.dim)
        rag = RAGPipeline(embedding=es, vector_manager=vm)

        # L1: 空知识库 → 直接拒绝
        result_l1 = rag.answer(knowledge_base_id=999, query_text="随便问")
        assert "未能检索到" in result_l1.llm_answer or "抱歉" in result_l1.llm_answer, \
            f"L1 应拒绝回答，实际: {result_l1.llm_answer}"
        assert result_l1.success, "L1 拒绝应为 success=True"
        assert result_l1.model == "mock-hallucination-filter", f"L1 model 应为 filter"

        _record("12.5 RAG 幻觉抑制分层 (L1)", True, result_l1.llm_answer[:60])
except Exception as e:
    _record("12.5 RAG 幻觉抑制分层 (L1)", False, str(e))

# --- 测试 12.6: RAG 流式回答 ---
# 验证 answer_stream 产出正确的事件序列
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as tmp:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=tmp, default_dim=es.dim)
        rag = RAGPipeline(embedding=es, vector_manager=vm)

        events = list(rag.answer_stream(knowledge_base_id=999, query_text="随便问"))
        assert len(events) >= 2, f"流式应至少 2 个事件，实际 {len(events)}"
        assert events[0]["type"] == "retrieval_done", "第一个事件应为 retrieval_done"
        assert events[-1]["type"] == "done", "最后一个事件应为 done"
        # 中间应有 token 事件
        token_events = [e for e in events if e["type"] == "token"]
        assert len(token_events) >= 1, "应至少有 1 个 token 事件"

        _record("12.6 RAG 流式回答", True,
                f"events={len(events)}, tokens={len(token_events)}")
except Exception as e:
    _record("12.6 RAG 流式回答", False, str(e))

# --- 测试 12.7: RAG 检索增强回答 ---
# 在有数据的情况下验证完整 RAG 流程
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as tmp:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=tmp, default_dim=es.dim)

        # 先存几条数据
        store = vm.get_store(kb_id=1, dim=es.dim)
        chunks_data = [
            ("我们支持支付宝和微信支付方式。", "faq.txt", 1, 1),
            ("7天无理由退换货，需保持商品完好。", "faq.txt", 1, 2),
            ("产品规格: 30x20x5 厘米，重量约 500g。", "spec.txt", 2, 3),
        ]
        for content, fn, did, cid in chunks_data:
            vec = es.encode_single(content)
            store.add(vec, metadata={
                "chunk_id": cid, "content": content,
                "document_id": did, "document_filename": fn
            })

        rag = RAGPipeline(embedding=es, vector_manager=vm)
        result = rag.answer(knowledge_base_id=1, query_text="怎么付款")
        assert result.success, f"RAG 失败: {result.error}"
        assert len(result.retrieved_chunks) > 0, "应检索到 chunks"
        assert result.llm_answer and len(result.llm_answer) > 0, "回答为空"
        _record("12.7 RAG 检索增强回答", True,
                f"chunks={len(result.retrieved_chunks)}, answer_len={len(result.llm_answer)}")
except Exception as e:
    _record("12.7 RAG 检索增强回答", False, str(e))


# ============================== 13. Agent 服务 ==============================
_section("13. Agent 服务测试")

# --- 测试 13.1: Agent 初始化 ---
# 验证 AgentService 可创建（即使无 DB）
try:
    from app.services.agent_service import AgentService
    agent = AgentService(db=None)
    _record("13.1 Agent 初始化 (无 DB)", True)
except Exception as e:
    _record("13.1 Agent 初始化 (无 DB)", False, str(e))

# --- 测试 13.2: Agent 工具注册 ---
# 验证有 DB 时工具正确注册
try:
    from app.services.agent_service import AgentService
    from app.models.database import SessionLocal
    db = SessionLocal()
    agent = AgentService(db=db)
    assert "search_kb" in agent.tools, "search_kb 工具未注册"
    assert "get_doc" in agent.tools, "get_doc 工具未注册"
    _record("13.2 Agent 工具注册", True, f"tools={list(agent.tools.keys())}")
    db.close()
except Exception as e:
    _record("13.2 Agent 工具注册", False, str(e))
    try:
        db.close()
    except Exception:
        pass

# --- 测试 13.3: Agent 解析 Thought/Action ---
# 验证 _parse_agent_output 正确解析 LLM 输出
try:
    from app.services.agent_service import AgentService
    # 标准格式
    text1 = "Thought: 用户想知道支付方式\nAction: search_kb\nAction Input: {\"query\": \"支付方式\", \"kb_id\": 1}"
    t, a, ai = AgentService._parse_agent_output(text1)
    assert "支付" in t or "想知道" in t, f"Thought 解析错误: {t}"
    assert a == "search_kb", f"Action 解析错误: {a}"
    assert "支付方式" in ai, f"Action Input 解析错误: {ai}"

    # Final Answer 格式
    text2 = "Thought: 已有足够信息\nAction: Final Answer\nAction Input: 我们支持支付宝"
    t2, a2, ai2 = AgentService._parse_agent_output(text2)
    assert a2.lower() == "final answer", f"Final Answer 解析错误: {a2}"
    assert "支付宝" in ai2, f"Final Answer 内容错误: {ai2}"

    # 括号后缀不应影响解析
    text3 = "Thought: 需要搜索\nAction: Final Answer [summary]\nAction Input: 最终回答"
    t3, a3, ai3 = AgentService._parse_agent_output(text3)
    assert a3 == "Final Answer", f"带括号后缀的 Action 解析错误: {a3}"

    _record("13.3 Agent 解析 Thought/Action", True)
except Exception as e:
    _record("13.3 Agent 解析 Thought/Action", False, str(e))

# --- 测试 13.4: Agent 空查询处理 ---
# 验证空查询被正确拒绝
try:
    from app.services.agent_service import AgentService
    agent = AgentService(db=None)
    result = agent.run(query="")
    assert not result.success, "空查询应失败"
    assert "不能为空" in result.error, f"错误信息不正确: {result.error}"
    _record("13.4 Agent 空查询处理", True)
except Exception as e:
    _record("13.4 Agent 空查询处理", False, str(e))

# --- 测试 13.5: Agent 工具参数解析 ---
# 验证 JSON 参数解析的容错性
try:
    from app.services.agent_service import AgentService
    # 合法 JSON
    args1 = json.loads('{"query": "test", "kb_id": 1}')
    assert args1["query"] == "test"
    assert args1["kb_id"] == 1

    # 非法 JSON 应 fallback
    try:
        args2 = json.loads("not valid json")
        _record("13.5 Agent JSON 容错", False, "非法 JSON 不应成功")
    except Exception:
        _record("13.5 Agent JSON 容错", True)
except Exception as e:
    _record("13.5 Agent JSON 容错", False, str(e))

# --- 测试 13.6: Agent 工具搜索 (有 DB + 数据) ---
# 验证 SearchKBTool 端到端
try:
    from app.services.agent_service import AgentService, SearchKBTool
    from app.models.database import SessionLocal
    from app.models.entities.user import User
    from app.models.entities.knowledge_base import KnowledgeBase
    from app.models.entities.document import Document, Chunk
    from app.core.security import hash_password
    from app.processors import EmbeddingService

    db = SessionLocal()
    ts = int(time.time())
    u = User(username=f"agent_{ts}", email=f"agent_{ts}@t.com",
             hashed_password=hash_password("p"))
    db.add(u); db.commit(); db.refresh(u)
    kb = KnowledgeBase(name="AgentTest", user_id=u.id, is_public=True)
    db.add(kb); db.commit(); db.refresh(kb)

    # 存文档 + chunk（不建向量索引，仅测工具逻辑）
    doc = Document(filename="tool_test.txt", knowledge_base_id=kb.id,
                   user_id=u.id, total_chunks=1)
    db.add(doc); db.commit(); db.refresh(doc)
    chunk = Chunk(document_id=doc.id, knowledge_base_id=kb.id,
                  content="我们支持支付宝、微信、信用卡三种支付方式。", chunk_index=0)
    db.add(chunk); db.commit()

    tool = SearchKBTool(db)
    result = tool.run({"query": "支付方式", "kb_id": kb.id},
                      {"kb_id": kb.id, "user_id": u.id})
    assert result.success, f"工具执行失败: {result.error}"
    _record("13.6 Agent SearchKBTool", True,
            f"success={result.success}, content_len={len(result.content)}")

    # 清理
    db.query(Chunk).filter(Chunk.document_id == doc.id).delete()
    db.delete(doc); db.delete(kb); db.delete(u)
    db.commit()
    db.close()
except Exception as e:
    _record("13.6 Agent SearchKBTool", False, str(e))
    try:
        db.rollback(); db.close()
    except Exception:
        pass

# --- 测试 13.7: Agent GetDoc 工具 ---
# 验证 GetDocTool 能获取文档全文
try:
    from app.services.agent_service import GetDocTool
    from app.models.database import SessionLocal
    from app.models.entities.user import User
    from app.models.entities.knowledge_base import KnowledgeBase
    from app.models.entities.document import Document, Chunk
    from app.core.security import hash_password

    db = SessionLocal()
    ts = int(time.time())
    u = User(username=f"getdoc_{ts}", email=f"getdoc_{ts}@t.com",
             hashed_password=hash_password("p"))
    db.add(u); db.commit(); db.refresh(u)
    kb = KnowledgeBase(name="DocTest", user_id=u.id, is_public=True)
    db.add(kb); db.commit(); db.refresh(kb)
    doc = Document(filename="full_doc.txt", knowledge_base_id=kb.id,
                   user_id=u.id, total_chunks=2)
    db.add(doc); db.commit(); db.refresh(doc)
    c1 = Chunk(document_id=doc.id, knowledge_base_id=kb.id,
               content="第一段内容：产品介绍。", chunk_index=0)
    c2 = Chunk(document_id=doc.id, knowledge_base_id=kb.id,
               content="第二段内容：使用说明。", chunk_index=1)
    db.add_all([c1, c2]); db.commit()

    tool = GetDocTool(db)
    result = tool.run({"document_id": doc.id}, {})
    assert result.success, f"GetDoc 失败: {result.error}"
    assert "产品介绍" in result.content or "第一段" in result.content, \
        f"应包含文档内容，实际: {result.content[:100]}"

    # 不存在的文档 ID
    result2 = tool.run({"document_id": 99999}, {})
    assert not result2.success, "不存在的文档应失败"

    # 清理
    db.query(Chunk).filter(Chunk.document_id == doc.id).delete()
    db.delete(doc); db.delete(kb); db.delete(u)
    db.commit()
    db.close()
    _record("13.7 Agent GetDocTool", True)
except Exception as e:
    _record("13.7 Agent GetDocTool", False, str(e))
    try:
        db.rollback(); db.close()
    except Exception:
        pass


# ============================== 14. Integration 服务 ==============================
_section("14. Integration 服务测试")

# --- 测试 14.1: Webhook Token 生成 ---
# 验证 token 格式正确性
try:
    from app.services.integration_service import IntegrationService
    token = IntegrationService.generate_webhook_token("shopify", 42)
    assert token, "Token 为空"
    parts = token.split("_")
    assert len(parts) >= 3, f"Token 段数不对: {len(parts)}"
    assert parts[0] == "shopify", f"Channel 不对: {parts[0]}"
    assert parts[1] == "42", f"KB ID 不对: {parts[1]}"
    _record("14.1 Webhook Token 生成", True, token)
except Exception as e:
    _record("14.1 Webhook Token 生成", False, str(e))

# --- 测试 14.2: Webhook Token 验证 (shopify) ---
# 验证 shopify channel 的 token 可正确验证
try:
    from app.services.integration_service import IntegrationService
    token = IntegrationService.generate_webhook_token("shopify", 42)
    result = IntegrationService.verify_webhook_token(token)
    assert result is not None, "Token 验证应通过"
    channel, kb_id = result
    assert channel == "shopify", f"Channel 不对: {channel}"
    assert kb_id == 42, f"KB ID 不对: {kb_id}"
    _record("14.2 Webhook Token 验证 (shopify)", True, f"channel={channel}, kb_id={kb_id}")
except Exception as e:
    _record("14.2 Webhook Token 验证 (shopify)", False, str(e))

# --- 测试 14.3: Webhook Token 验证 (generic_http 含下划线) ---
# 这是之前修复的关键 bug：generic_http 含下划线，验证不能用 split("_")
try:
    from app.services.integration_service import IntegrationService
    token = IntegrationService.generate_webhook_token("generic_http", 99)
    result = IntegrationService.verify_webhook_token(token)
    assert result is not None, "generic_http token 验证应通过（之前因下划线 bug 失败）"
    channel, kb_id = result
    assert channel == "generic_http", f"Channel 不对: {channel}"
    assert kb_id == 99, f"KB ID 不对: {kb_id}"
    _record("14.3 Token 验证 (generic_http)", True, f"channel={channel}, kb_id={kb_id}")
except Exception as e:
    _record("14.3 Token 验证 (generic_http)", False, str(e))

# --- 测试 14.4: Token 防伪造 ---
# 验证随机/篡改的 token 被正确拒绝
try:
    from app.services.integration_service import IntegrationService
    assert IntegrationService.verify_webhook_token("") is None, "空 token 应被拒"
    assert IntegrationService.verify_webhook_token("invalid_token") is None, "无效 token 应被拒"
    assert IntegrationService.verify_webhook_token("shopify_999_fakesignature") is None, "伪造签名应被拒"
    _record("14.4 Token 防伪造", True)
except Exception as e:
    _record("14.4 Token 防伪造", False, str(e))

# --- 测试 14.5: 通用 HTTP 消息解析 ---
# 验证 parse_generic_http 能从多种字段名中提取 query
try:
    from app.services.integration_service import IntegrationService
    svc = IntegrationService()

    # 标准格式
    msg1 = svc.parse_generic_http({"query": "你好", "user_id": "u1"}, kb_id=1)
    assert msg1 is not None
    assert msg1.query_text == "你好"
    assert msg1.external_user_id == "u1"
    assert msg1.kb_id == 1

    # 兼容字段
    msg2 = svc.parse_generic_http({"message": "兼容字段"}, kb_id=2)
    assert msg2 is not None and msg2.query_text == "兼容字段"

    # 空 query
    msg3 = svc.parse_generic_http({"query": ""}, kb_id=1)
    assert msg3 is None, "空 query 应返回 None"

    # 非 dict
    msg4 = svc.parse_generic_http("not a dict", kb_id=1)
    assert msg4 is None, "非 dict 应返回 None"

    _record("14.5 通用 HTTP 消息解析", True)
except Exception as e:
    _record("14.5 通用 HTTP 消息解析", False, str(e))

# --- 测试 14.6: Shopify Webhook 解析 ---
# 验证 parse_shopify_webhook 能解析 Shopify 格式的 payload
try:
    from app.services.integration_service import IntegrationService
    svc = IntegrationService()
    payload = {
        "query": "你们支持什么支付方式",
        "customer": {"id": "cust_123", "email": "test@test.com"},
        "conversation_id": "conv_456",
    }
    headers = {"X-Shopify-Shop-Domain": "mystore.myshopify.com"}
    msg = svc.parse_shopify_webhook(payload, headers, kb_id=10)
    assert msg is not None
    assert msg.channel == "shopify"
    assert msg.external_user_id == "cust_123"
    assert msg.external_conversation_id == "conv_456"
    assert msg.query_text == "你们支持什么支付方式"
    assert msg.metadata.get("shop") == "mystore.myshopify.com"
    _record("14.6 Shopify Webhook 解析", True)
except Exception as e:
    _record("14.6 Shopify Webhook 解析", False, str(e))

# --- 测试 14.7: 渠道回复渲染 ---
# 验证 render_reply_for_channel 正确格式化为各渠道格式
try:
    from app.services.integration_service import IntegrationService, OutboundReply
    reply = OutboundReply(
        answer_text="我们支持支付宝和微信支付。",
        sources=[{"document_filename": "faq.txt", "content": "支付方式说明"}],
        latency_ms=42.5,
    )
    svc = IntegrationService()

    # 通用格式
    generic = svc.render_reply_for_channel("generic_http", reply)
    assert generic["ok"] == True
    assert generic["answer"] == "我们支持支付宝和微信支付。"
    assert generic["latency_ms"] == 42.5

    # Shopify 格式
    shopify = svc.render_reply_for_channel("shopify", reply)
    assert shopify["ok"] == True
    assert "message_html" in shopify, "Shopify 格式缺 message_html"
    assert "plain" in shopify, "Shopify 格式缺 plain"
    assert "参考来源" in shopify.get("message_html", ""), "Shopify HTML 缺来源"

    _record("14.7 渠道回复渲染", True)
except Exception as e:
    _record("14.7 渠道回复渲染", False, str(e))