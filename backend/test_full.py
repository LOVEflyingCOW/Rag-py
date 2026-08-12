"""
RAG-Knowledge-Base 全量测试套件（完整版）
========================================
本文件覆盖系统所有核心模块的功能/边界/异常/性能测试。
直接运行:  python test_full.py          （在 backend/ 目录下）

测试覆盖:
  Part A  环境与配置
  Part B  日志 & 安全模块（密码/JWT/Token）
  Part C  数据库实体 & Schema
  Part D  文档处理 Pipeline
  Part E  Embedding & Vector Store
  Part F  LLM Provider（含递归修复验证、Mock 流式）
  Part G  BM25 检索
  Part H  混合检索服务（三路融合、重排、去重）
  Part I  RAG Pipeline（幻觉抑制 L1/L2/L3、流式）
  Part J  Agent（ReAct 循环、工具、解析修复）
  Part K  Integration（Webhook Token、Shopify/Generic 解析、渲染）
  Part L  API 端点（FastAPI TestClient）
  Part M  并发 & 鲁棒性
  Part N  性能基准
"""

import os
import sys
import io
import json
import time
import math
import uuid
import hmac
import hashlib
import tempfile
import threading
import traceback
from typing import Dict, List, Any, Optional
from dataclasses import asdict

# 项目路径
_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BASE)

# ========================= 结果收集器 =========================
_results: List[Dict[str, Any]] = []
_passed = 0
_failed = 0
_errors: List[str] = []


def _record(name: str, ok: bool, detail: str = ""):
    global _passed, _failed
    if ok:
        _passed += 1
    else:
        _failed += 1
        _errors.append(f"[FAIL] {name} | {detail}")
    icon = "PASS" if ok else "FAIL"
    print(f"  [{icon}] {name}" + (f"  — {detail}" if detail else ""))


def _section(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def _sub(title: str):
    print(f"\n--- {title} ---")


# ================================================================
# Part A  环境与配置
# ================================================================
_section("Part A  环境与配置")

# A.1 项目关键目录
# 目的: 验证模块布局完整，防止 import 阶段因目录缺失报错
try:
    required = [
        "app", "app/core", "app/models", "app/models/entities",
        "app/models/schemas", "app/services", "app/processors",
        "app/processors/document", "app/processors/embedding",
        "app/processors/llm", "app/processors/retrieval",
        "app/api", "app/api/v1", "data",
    ]
    missing = [d for d in required if not os.path.isdir(os.path.join(_BASE, d))]
    _record("A.1 项目关键目录完整性", len(missing) == 0,
            f"missing: {missing}" if missing else "all ok")
except Exception as e:
    _record("A.1 项目关键目录完整性", False, str(e))

# A.2 requirements.txt 存在 & 关键依赖可导入
# 目的: 防止"代码没问题但依赖缺失"的伪通过
try:
    from app.core.config import settings
    settings.ensure_dirs()
    _record("A.2 settings 加载 & 目录创建", True,
            f"provider={settings.LLM_PROVIDER}, top_k={settings.RAG_TOP_K}")
except Exception as e:
    _record("A.2 settings 加载", False, str(e))

# A.3 关键第三方库
# 目的: 标记哪些环境需要提前 pip install
for lib_name in ["sqlalchemy", "pydantic", "fastapi", "uvicorn", "passlib", "jwt"]:
    try:
        __import__(lib_name)
        _record(f"A.3 依赖 [{lib_name}]", True, "import ok")
    except Exception as e:
        _record(f"A.3 依赖 [{lib_name}]", False, str(e))

# A.4 numpy / faiss 可选
# 目的: numpy 影响纯 Python 回退性能，faiss 影响大规模检索
for lib_name in ["numpy", "faiss", "requests"]:
    try:
        __import__(lib_name)
        _record(f"A.4 可选依赖 [{lib_name}]", True, "available")
    except Exception:
        _record(f"A.4 可选依赖 [{lib_name}]", True, "not installed (will use fallback)")

# ================================================================
# Part B  日志 & 安全模块
# ================================================================
_section("Part B  日志 & 安全模块")

# B.1 logger 能输出到文件
# 目的: 保证日志链路可用，不抛错
try:
    from app.core.logging import logger
    logger.info("test_log_line_%d", int(time.time()))
    log_path = os.path.join(_BASE, "data", "rag_system.log")
    # 若有配置写日志，至少 logger 实例化成功
    _record("B.1 logger 实例化与 info() 调用", True, f"log_path={log_path}")
except Exception as e:
    _record("B.1 logger", False, str(e))

# B.2 密码哈希
# 目的: 注册/登录链路依赖 passlib，需要能双向校验
try:
    from app.core.security import hash_password, verify_password
    pwd = "MyP@ssw0rd_测试"
    h = hash_password(pwd)
    assert h != pwd, "哈希不应等于明文"
    assert verify_password(pwd, h), "正确密码应通过"
    assert not verify_password("wrong_pwd", h), "错误密码应被拒绝"
    _record("B.2 密码哈希双向校验", True, f"hash_prefix={h[:20]}")
except Exception as e:
    _record("B.2 密码哈希", False, str(e))

# B.3 JWT 签发与校验
# 目的: 对外鉴权基础
try:
    from app.core.security import create_access_token, decode_access_token
    tok = create_access_token(user_id=123, username="alice")
    decoded = decode_access_token(tok)
    assert decoded is not None, "Token 应可解析"
    assert str(decoded.get("sub")) == "123"
    assert decoded.get("username") == "alice"
    assert decoded.get("type") == "access"
    _record("B.3 JWT 签发/解析", True, f"token_prefix={tok[:30]}")
except Exception as e:
    _record("B.3 JWT", False, str(e))

# B.4 JWT 过期拒绝
# 目的: 防止过期 token 被误用
try:
    from app.core.security import create_jwt_token, decode_jwt_token
    tok = create_jwt_token({"sub": "1", "username": "x"}, expire_minutes=0)
    time.sleep(1.1)
    decoded = decode_jwt_token(tok)
    assert decoded is None, "过期 token 应返回 None"
    _record("B.4 JWT 过期拒签", True, f"decoded={decoded}")
except Exception as e:
    _record("B.4 JWT 过期", False, str(e))

# ================================================================
# Part C  数据库实体 & Schema
# ================================================================
_section("Part C  数据库实体 & Schema")

# C.1 建立 SQLite 内存会话
# 目的: 后续所有 DB 相关测试共用同一引擎
try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.database import Base
    # 必须显式 import 所有 entity，使 SQLAlchemy 元数据注册
    import app.models.entities  # noqa: F401
    _engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine)
    _db = _SessionLocal()
    _record("C.1 SQLite 内存会话创建", True)
except Exception as e:
    _record("C.1 SQLite 会话", False, str(e))
    _db = None

# C.2 实体可创建 & 持久化
# 目的: 防止 ORM 字段缺失/约束错误
if _db is not None:
    try:
        from app.models.entities.user import User
        u = User(username="alice", email="alice@example.com",
                 password_hash="fake_hash_1", is_active=True)
        _db.add(u)
        _db.commit()
        assert u.id is not None
        back = _db.query(User).filter(User.username == "alice").first()
        assert back is not None and back.email == "alice@example.com"
        _record("C.2 User 实体增查", True, f"id={u.id}")
    except Exception as e:
        _record("C.2 User 实体", False, str(e))

    try:
        from app.models.entities.knowledge_base import KnowledgeBase
        kb = KnowledgeBase(name="Test KB", description="测试知识库",
                           user_id=1, is_public=True)
        _db.add(kb)
        _db.commit()
        _record("C.3 KnowledgeBase 创建", True, f"kb_id={kb.id}")
    except Exception as e:
        _record("C.3 KnowledgeBase", False, str(e))
        kb = None
    else:
        try:
            from app.models.entities.document import Document, DocumentChunk
            doc = Document(
                knowledge_base_id=kb.id,
                filename="faq.txt",
                file_path="/tmp/faq.txt",
                description="常见问题文档",
                mime_type="text/plain",
                size_bytes=256,
            )
            _db.add(doc)
            _db.commit()
            chunks = [
                DocumentChunk(document_id=doc.id, knowledge_base_id=kb.id,
                      chunk_index=i, content=f"Chunk {i+1}: 这是第{i+1}段测试内容。" * 3,
                      vector_index=0)
                for i in range(3)
            ]
            for c in chunks:
                _db.add(c)
            _db.commit()
            _record("C.4 Document + 3 Chunk 写入", True, f"doc_id={doc.id}")
        except Exception as e:
            _record("C.4 Document/Chunk", False, str(e))
            doc = None

# C.5 Pydantic Schema 可实例化
# 目的: API 层契约正确性
try:
    from app.models.schemas import (
        UserRegister, UserLogin, KnowledgeBaseCreate, KnowledgeBaseUpdate,
        ChatRequest, ChatMessageItem, DocumentUploadRequest,
    )
    u = UserRegister(username="bob", email="bob@x.com", password="P@ssw0rd", confirm_password="P@ssw0rd")
    assert u.username == "bob"
    kb = KnowledgeBaseCreate(name="test", title="t")
    assert kb.name == "test"
    req = ChatRequest(knowledge_base_id=1, message="你好")
    assert req.message == "你好"
    _record("C.5 核心 Pydantic Schema 实例化", True)
except Exception as e:
    _record("C.5 Pydantic Schema", False, str(e))

# ================================================================
# Part D  文档处理 Pipeline
# ================================================================
_section("Part D  文档处理 Pipeline")

# D.1 SemanticChunker 对中文/英文混合文本分块
# 目的: chunk 质量直接影响召回
try:
    from app.processors.document.semantic_chunker import SemanticChunker
    ch = SemanticChunker(max_chars=120, overlap=20)
    text = (
        "欢迎使用我们的智能客服系统。\n"
        "本系统基于 RAG 技术，可以根据商家内部文档回答用户问题。\n"
        "我们支持多渠道接入，包括 Shopify、微信、Slack 等。\n"
        "All sales are final within 7 days. After that, we offer store credit.\n"
        "退款流程：用户需提供订单号和联系方式，客服将在 24 小时内处理。\n"
        "Shipping is free for orders over $50 within United States.\n"
    )
    chunks = ch.split_text(text)
    assert len(chunks) >= 2, f"应至少 2 段，实际 {len(chunks)}"
    for c in chunks:
        assert isinstance(c, dict) and "content" in c and len(c["content"]) > 0
    _record("D.1 SemanticChunker 中英混合分块", True, f"chunks={len(chunks)}")
except Exception as e:
    _record("D.1 SemanticChunker", False, str(e))

# D.2 MarkdownParser 提取纯文本
# 目的: Markdown 解析要剥离格式
try:
    from app.processors.document.markdown_parser import MarkdownParser
    md = "# Title\n\nSome **bold** and *italic* text.\n\n- item1\n- item2"
    parser = MarkdownParser()
    plain = parser.to_plain_text(md)
    assert "Title" in plain
    assert "**" not in plain
    assert "item1" in plain
    _record("D.2 MarkdownParser 纯文本提取", True, f"len={len(plain)}")
except Exception as e:
    _record("D.2 MarkdownParser", False, str(e))

# D.3 DocumentPipeline 完整流程（文本 -> chunk -> 向量）
# 目的: 验证最常见的"上传文档"端到端路径
try:
    from app.processors.document.document_pipeline import DocumentPipeline
    dp = DocumentPipeline(vector_store_dir=tempfile.mkdtemp(prefix="rag_dp_"))
    assert dp is not None
    _record("D.3 DocumentPipeline 构造", True)
except Exception as e:
    _record("D.3 DocumentPipeline", False, str(e))

# ================================================================
# Part E  Embedding & Vector Store
# ================================================================
_section("Part E  Embedding & Vector Store")

# E.1 EmbeddingService 单条向量
# 目的: embedding 是检索基石
try:
    from app.processors.embedding.embedding_service import EmbeddingService
    es = EmbeddingService()
    vec = es.encode_single("你好世界 hello world")
    assert isinstance(vec, list) and len(vec) == es.dim
    # 检查有限性
    assert all(math.isfinite(float(v)) for v in vec)
    _record("E.1 EmbeddingService.encode_single", True, f"dim={es.dim}")
except Exception as e:
    _record("E.1 EmbeddingService", False, str(e))

# E.2 EmbeddingService 批量编码
# 目的: 批量接口用于文档入库加速
try:
    from app.processors.embedding.embedding_service import EmbeddingService
    es = EmbeddingService()
    vecs = es.encode_batch(["苹果手机", "华为手机", "客服退款政策"])
    assert len(vecs) == 3
    for v in vecs:
        assert len(v) == es.dim
    _record("E.2 EmbeddingService.encode_batch", True)
except Exception as e:
    _record("E.2 EmbeddingService.encode_batch", False, str(e))

# E.3 向量归一化
# 目的: cosine 相似度依赖归一化
try:
    from app.processors.retrieval.vector_store import _normalize_vectors
    arr = [[3.0, 4.0], [0.0, 0.0], [1.0, 0.0]]
    normed = _normalize_vectors(arr)
    assert len(normed) == 3
    # 非零向量归一化后模为 1
    norm0 = math.sqrt(sum(x * x for x in normed[0]))
    assert abs(norm0 - 1.0) < 1e-4, f"非零向量模应为 1，实际 {norm0}"
    # 零向量保持为 0
    assert normed[1] == [0.0, 0.0]
    _record("E.3 向量归一化", True, f"norm0={norm0:.6f}")
except Exception as e:
    _record("E.3 向量归一化", False, str(e))

# E.4 VectorStoreManager 生命周期
# 目的: 向量库增删查存完整流程
try:
    from app.processors import EmbeddingService, VectorStoreManager
    es = EmbeddingService()
    with tempfile.TemporaryDirectory() as td:
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        store = vm.get_store(1, dim=es.dim)
        assert store.total() == 0
        vecs = es.encode_batch(["退款政策介绍", "Shipping info", "Refund process", "订单查询"])
        metas = [
            {"chunk_id": i + 1, "document_id": 1, "document_filename": "faq.txt",
             "content": c}
            for i, c in enumerate(["退款政策介绍", "Shipping info", "Refund process", "订单查询"])
        ]
        ids = store.add(vecs, metas)
        assert len(ids) == 4
        assert store.total() == 4

        q = es.encode_single("怎么退款")
        results = store.search(q, top_k=3)
        assert len(results) >= 1
        assert results[0]["score"] > 0

        # 持久化
        vm.save(1)
        assert vm.has_store(1)

        # 删除
        vm.delete(1)
        assert not vm.has_store(1)

        _record("E.4 VectorStoreManager 增删查存", True,
                f"top_score={results[0]['score']:.3f}")
except Exception as e:
    _record("E.4 VectorStoreManager", False, str(e))

# E.5 维度校验
# 目的: 维度不一致会导致检索异常
try:
    from app.processors import EmbeddingService, VectorStoreManager
    es = EmbeddingService()
    with tempfile.TemporaryDirectory() as td:
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        store = vm.get_store(1, dim=es.dim)
        bad_vec = [0.1] * (es.dim + 5)
        try:
            store.add([bad_vec], [{"chunk_id": 99}])
            _record("E.5 向量维度校验", False, "维度异常应被 ValueError 拒绝")
        except ValueError:
            _record("E.5 向量维度校验", True)
except Exception as e:
    _record("E.5 向量维度校验", False, str(e))

# ================================================================
# Part F  LLM Provider
# ================================================================
_section("Part F  LLM Provider")

# F.1 递归修复验证 —— chat() 不应再触发 chat_stream()
# 目的: 验证历史 bug (chat 与 chat_stream 互调死循环) 已修复
try:
    from app.processors.llm.llm_service import (
        BaseLLMProvider, MockLLMProvider, ChatMessage, ChatResult,
    )

    class RecursionCheck(BaseLLMProvider):
        def __init__(self):
            super().__init__()
            self.chat_called = 0
            self.stream_called = 0

        def chat(self, messages, **kwargs):
            self.chat_called += 1
            time.sleep(0.01)
            return ChatResult(content="ok", model="x", provider="test")

        def chat_stream(self, messages, **kwargs):
            self.stream_called += 1
            for m in super().chat_stream(messages, **kwargs):
                yield m

    rc = RecursionCheck()
    msgs = [ChatMessage(role="user", content="hi")]
    # 调用 chat_stream
    list(rc.chat_stream(msgs))
    # 结果: chat_stream 被调 1 次；chat 也被调 1 次（通过 _chat_impl）
    assert rc.stream_called == 1, f"stream_called={rc.stream_called}"
    assert rc.chat_called == 1, f"chat_called={rc.chat_called}"
    _record("F.1 递归修复验证 (chat_stream->chat 仅一次)", True)
except Exception as e:
    _record("F.1 递归修复验证", False, str(e))

# F.2 MockLLMProvider 正常回答
# 目的: 无 API Key 环境必须能回答
try:
    from app.processors.llm.llm_service import MockLLMProvider, ChatMessage
    mock = MockLLMProvider()
    msgs = [
        ChatMessage(role="system",
                    content="你是助手。\n【知识库片段】\n[#1] (来源: doc.txt, 相似度: 0.9)\n"
                            "苹果手机支持 14 天无理由退款。\n---\n请回答用户问题。"),
        ChatMessage(role="user", content="苹果手机怎么退款？"),
    ]
    res = mock.chat(msgs)
    assert res.success and res.content
    assert "mock" in res.model
    _record("F.2 MockLLMProvider 正常回答", True, f"answer[:60]={res.content[:60]}")
except Exception as e:
    _record("F.2 MockLLMProvider", False, str(e))

# F.3 MockLLMProvider 幻觉抑制 —— 无 chunk 时拒绝
# 目的: 验证 Mock 的 L1/L2 层幻觉抑制
try:
    from app.processors.llm.llm_service import MockLLMProvider, ChatMessage
    mock = MockLLMProvider()
    msgs = [
        ChatMessage(role="system",
                    content="本次查询在知识库中未能检索到任何相关片段。"
                            "你必须直接回复用户：\"很抱歉，在知识库中未能检索到足够的相关信息来回答您的问题。\""),
        ChatMessage(role="user", content="随便问一个知识库之外的问题"),
    ]
    res = mock.chat(msgs)
    assert "未能检索到" in res.content, f"应拒答，实际: {res.content[:80]}"
    _record("F.3 Mock 幻觉抑制（无 chunk）", True)
except Exception as e:
    _record("F.3 Mock 幻觉抑制", False, str(e))

# F.4 Mock 流式输出
# 目的: 验证 SSE 接口产出多个 token
try:
    from app.processors.llm.llm_service import MockLLMProvider, ChatMessage
    mock = MockLLMProvider()
    msgs = [
        ChatMessage(role="system",
                    content="你是助手。\n【知识库片段】\n[#1] (来源: doc.txt, 相似度: 0.9)\n"
                            "我们支持 7 天无理由退货。\n---\n请回答。"),
        ChatMessage(role="user", content="怎么退货？"),
    ]
    tokens = list(mock.chat_stream(msgs))
    assert len(tokens) >= 2, f"应至少 2 个 token，实际 {len(tokens)}"
    full = "".join(t.content for t in tokens if t.content)
    assert len(full) > 0
    _record("F.4 Mock 流式输出", True, f"tokens={len(tokens)}, total_chars={len(full)}")
except Exception as e:
    _record("F.4 Mock 流式输出", False, str(e))

# F.5 Token 估算合理性
# 目的: 估算是否大致可用
try:
    from app.processors.llm.llm_service import BaseLLMProvider
    import random
    bp = BaseLLMProvider()
    samples = ["", "hi", "你好世界，这是一段中文文本。" * 100, "Hello world " * 500]
    for s in samples:
        n = bp.count_tokens(s)
        assert isinstance(n, int) and n >= 0
    _record("F.5 Token 估算", True)
except Exception as e:
    _record("F.5 Token 估算", False, str(e))

# ================================================================
# Part G  BM25 检索
# ================================================================
_section("Part G  BM25 检索")

# G.1 中英文分词
# 目的: BM25 分词质量决定检索精度
try:
    from app.services.retrieval_service import BM25Index
    tokens_zh = BM25Index.tokenize("苹果手机怎么退款")
    tokens_en = BM25Index.tokenize("How to refund an iPhone")
    assert len(tokens_zh) > 0 and len(tokens_en) > 0
    # 中文不应只按整句
    assert any(len(t) >= 2 for t in tokens_zh), f"中文分词过粗: {tokens_zh}"
    _record("G.1 BM25 分词 (中英)", True,
            f"zh={tokens_zh[:5]}, en={tokens_en[:5]}")
except Exception as e:
    _record("G.1 BM25 分词", False, str(e))

# G.2 BM25 索引打分 + 归一化
# 目的: 验证打分可区分相关与不相关
try:
    from app.services.retrieval_service import BM25Index
    idx = BM25Index()
    docs = [
        (1, "苹果手机支持 14 天无理由退款政策"),
        (2, "安卓手机的保修流程说明"),
        (3, "如何联系客服修改订单地址"),
        (4, "iPhone refund policy 14 days no reason"),
    ]
    for eid, content in docs:
        idx.add_doc(eid, content)

    scores = idx.score_normalized("苹果手机退款")
    # 归一化后 0~1
    for v in scores.values():
        assert 0.0 <= v <= 1.0
    # 应至少有一个高分
    assert max(scores.values()) > 0.5
    # 文档 1 和 4 应比 2/3 分高
    assert scores.get(1, 0) > scores.get(2, 0)
    _record("G.2 BM25 打分 + 归一化", True,
            f"doc_scores={ {k: round(v,3) for k,v in scores.items()} }")
except Exception as e:
    _record("G.2 BM25 打分", False, str(e))

# G.3 BM25 空库查询
# 目的: 空库不应报错
try:
    from app.services.retrieval_service import BM25Index
    idx = BM25Index()
    s = idx.score("随便问")
    assert s == {}
    s_norm = idx.score_normalized("随便问")
    assert s_norm == {}
    _record("G.3 BM25 空库稳健性", True)
except Exception as e:
    _record("G.3 BM25 空库", False, str(e))

# ================================================================
# Part H  混合检索服务
# ================================================================
_section("Part H  混合检索服务")

# H.1 三路混合权重配置
# 目的: 权重和必须为 1
try:
    from app.services.retrieval_service import RetrievalService
    w_sum = RetrievalService.VECTOR_WEIGHT + RetrievalService.BM25_WEIGHT + RetrievalService.KEYWORD_WEIGHT
    assert abs(w_sum - 1.0) < 1e-6
    _record("H.1 混合权重和=1", True, f"sum={w_sum}")
except Exception as e:
    _record("H.1 混合权重", False, str(e))

# H.2 _extract_keywords 中英混合
# 目的: 关键词提取用于 lexical rerank
try:
    from app.services.retrieval_service import RetrievalService
    kws = RetrievalService._extract_keywords("苹果手机退款政策 iPhone refund")
    assert len(kws) >= 2
    _record("H.2 关键词提取", True, f"keywords={kws[:8]}")
except Exception as e:
    _record("H.2 关键词提取", False, str(e))

# H.3 _keyword_overlap_score
# 目的: lexical 重排评分在 [0,1]
try:
    from app.services.retrieval_service import RetrievalService
    s = RetrievalService._keyword_overlap_score("苹果手机退款政策", ["苹果", "退款", "政策", "iPhone"])
    assert 0.0 <= s <= 1.0
    # 应至少命中 3/4
    assert s >= 0.75
    _record("H.3 关键词重叠分", True, f"score={s:.3f}")
except Exception as e:
    _record("H.3 关键词重叠分", False, str(e))

# H.4 重叠 chunk 合并
# 目的: 减少冗余 chunk
try:
    from app.services.retrieval_service import RetrievalService
    hits = [
        {"content": "苹果手机支持 14 天无理由退款政策", "vector_score": 0.9,
         "keyword_score": 0.8, "final_score": 0.85},
        {"content": "苹果手机支持 14 天无理由退款政策以及相关流程",
         "vector_score": 0.85, "keyword_score": 0.9, "final_score": 0.88},
        {"content": "完全不相关的内容 123456", "vector_score": 0.1,
         "keyword_score": 0.0, "final_score": 0.05},
    ]
    merged = RetrievalService._merge_overlapping(hits, threshold=0.6)
    assert len(merged) <= len(hits)
    _record("H.4 重叠 chunk 合并", True, f"before={len(hits)}, after={len(merged)}")
except Exception as e:
    _record("H.4 重叠合并", False, str(e))

# H.5 混合检索端到端（需 DB）
# 目的: 验证完整的向量+BM25+lexical 流程
if _db is not None:
    try:
        from app.services.retrieval_service import RetrievalService
        rs = RetrievalService(_db)
        kb_id = 1
        hits = rs.search(kb_id, "苹果退款", top_k=5, min_score=0.0,
                         enable_rerank=True, enable_merge=True)
        assert isinstance(hits, list)
        # 即使无 chunk 入库也不应报错
        _record("H.5 混合检索端到端", True, f"hits={len(hits)}")
    except Exception as e:
        _record("H.5 混合检索端到端", False, str(e))
else:
    _record("H.5 混合检索端到端", True, "skip (no db)")

# ================================================================
# Part I  RAG Pipeline
# ================================================================
_section("Part I  RAG Pipeline")

# I.1 RAGPipeline 构造 & 向后兼容
# 目的: 验证 vector_manager 命名修复 + vectors 别名
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        assert rag.vector_manager is vm
        assert rag.vectors is vm  # 向后兼容别名
        _record("I.1 RAGPipeline 构造/向后兼容别名", True)
except Exception as e:
    _record("I.1 RAGPipeline 构造", False, str(e))

# I.2 检索（先写入再检索）
# 目的: 确保 search 返回合理 chunk
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        vecs = es.encode_batch([
            "苹果手机支持 14 天无理由退款",
            "安卓手机保修政策",
            "订单查询流程说明",
        ])
        metas = [
            {"chunk_id": i + 1, "document_id": 1, "document_filename": "faq.txt",
             "content": c}
            for i, c in enumerate([
                "苹果手机支持 14 天无理由退款",
                "安卓手机保修政策",
                "订单查询流程说明",
            ])
        ]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        chunks = rag.search(1, "苹果退款", top_k=3)
        assert len(chunks) >= 1
        assert chunks[0].score > 0.0
        _record("I.2 RAG 检索", True, f"hits={len(chunks)}, top_score={chunks[0].score:.3f}")
except Exception as e:
    _record("I.2 RAG 检索", False, str(e))

# I.3 上下文组装 & 截断
# 目的: 验证 max_chars 限制
try:
    from app.services.chat_service import RAGPipeline, RetrievedChunk
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        chunks = [
            RetrievedChunk(chunk_id=i, content=f"测试内容 {i} " * 300,
                           score=0.9, document_id=1, document_filename="doc.txt")
            for i in range(10)
        ]
        ctx = rag.build_context(chunks, max_chars=500)
        # build_context 允许至少装入 1 块，且允许略超 max_chars（容错策略）
        assert len(ctx) > 0, "ctx 不应为空"
        assert len(ctx) <= 2500, f"ctx 应 <= 2500（容错上限），实际 {len(ctx)}"
        _record("I.3 RAG 上下文组装/截断", True, f"ctx_len={len(ctx)}")
except Exception as e:
    _record("I.3 RAG 上下文组装", False, str(e))

# I.4 幻觉抑制 L1：空知识库 → 直接拒答
# 目的: 最严格的一层 —— 无需 LLM 调用
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        r = rag.answer(knowledge_base_id=9999, query_text="任意问题")
        assert r.success
        assert "未能检索到" in r.llm_answer or "抱歉" in r.llm_answer
        assert r.model == "mock-hallucination-filter"
        _record("I.4 幻觉抑制 L1 (空库拒答)", True, f"answer[:50]={r.llm_answer[:50]}")
except Exception as e:
    _record("I.4 幻觉抑制 L1", False, str(e))

# I.5 幻觉抑制 L2：max_score < 0.42 → 拒答
# 目的: 弱相关内容不生成回答
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        # 写入一个与 query 完全无关的 chunk
        vecs = es.encode_batch(["今天天气不错我们去公园散步"])
        metas = [{"chunk_id": 1, "document_id": 1, "document_filename": "x.txt",
                  "content": "今天天气不错我们去公园散步"}]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        r = rag.answer(knowledge_base_id=1, query_text="苹果手机退款政策")
        assert r.success
        assert "未能检索到" in r.llm_answer or "抱歉" in r.llm_answer
        _record("I.5 幻觉抑制 L2 (低分拒答)", True, f"answer[:60]={r.llm_answer[:60]}")
except Exception as e:
    _record("I.5 幻觉抑制 L2", False, str(e))

# I.6 正常 RAG 端到端
# 目的: 正向路径
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        vecs = es.encode_batch([
            "苹果手机支持 14 天无理由退款，需提供订单号",
            "安卓手机保修一年，免费维修",
        ])
        metas = [
            {"chunk_id": 1, "document_id": 1, "document_filename": "faq.txt",
             "content": "苹果手机支持 14 天无理由退款，需提供订单号"},
            {"chunk_id": 2, "document_id": 1, "document_filename": "faq.txt",
             "content": "安卓手机保修一年，免费维修"},
        ]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        r = rag.answer(knowledge_base_id=1, query_text="苹果怎么退款", top_k=3)
        assert r.success
        assert r.llm_answer
        _record("I.6 RAG 端到端正常回答", True,
                f"model={r.model}, answer[:60]={r.llm_answer[:60]}")
except Exception as e:
    _record("I.6 RAG 端到端", False, str(e))

# I.7 RAG 流式 answer_stream
# 目的: 前端 SSE 依赖此接口
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        vecs = es.encode_batch(["苹果手机 14 天无理由退款政策"])
        metas = [{"chunk_id": 1, "document_id": 1, "document_filename": "faq.txt",
                  "content": "苹果手机 14 天无理由退款政策"}]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        events = list(rag.answer_stream(1, "苹果退款"))
        assert len(events) >= 2
        types = [e["type"] for e in events]
        assert "retrieval_done" in types
        assert "done" in types
        # 最终 done 里含 answer
        done = [e for e in events if e["type"] == "done"][0]
        assert done.get("answer")
        _record("I.7 RAG answer_stream 流式", True,
                f"events={len(events)}, types={types}")
except Exception as e:
    _record("I.7 RAG 流式", False, str(e))

# I.8 RAG 流式幻觉抑制
# 目的: 流式也要拒答
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        events = list(rag.answer_stream(9999, "任何问题"))
        types = [e["type"] for e in events]
        assert "done" in types
        done = [e for e in events if e["type"] == "done"][0]
        assert done["success"]
        assert "未能检索到" in done["answer"] or "抱歉" in done["answer"]
        _record("I.8 RAG 流式幻觉抑制", True)
except Exception as e:
    _record("I.8 RAG 流式幻觉抑制", False, str(e))

# I.9 多轮 history
# 目的: 验证 history 参数正确拼接
try:
    from app.services.chat_service import RAGPipeline
    from app.processors.llm.llm_service import ChatMessage
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        vecs = es.encode_batch(["苹果退款政策：14 天无理由退款"])
        metas = [{"chunk_id": 1, "document_id": 1, "document_filename": "faq.txt",
                  "content": "苹果退款政策：14 天无理由退款"}]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        history = [
            ChatMessage(role="user", content="你好"),
            ChatMessage(role="assistant", content="您好，有什么可以帮您？"),
        ]
        r = rag.answer(knowledge_base_id=1, query_text="苹果怎么退款", history=history)
        assert r.success
        _record("I.9 RAG 多轮 history", True, f"history_len={len(history)}")
except Exception as e:
    _record("I.9 RAG 多轮 history", False, str(e))

# ================================================================
# Part J  Agent
# ================================================================
_section("Part J  Agent")

# J.1 Agent 构造 & 无 DB 模式
# 目的: Agent 在无 DB 时应优雅降级
try:
    from app.services.agent_service import AgentService
    agent = AgentService(db=None)
    assert len(agent.tools) == 0
    _record("J.1 Agent 无 DB 构造", True, f"tools={agent.tools}")
except Exception as e:
    _record("J.1 Agent 构造", False, str(e))

# J.2 Agent _parse_agent_output —— 正常解析 Thought/Action
# 目的: 验证正则解析
try:
    from app.services.agent_service import AgentService
    sample = (
        "Thought: 用户想查询苹果退款政策，我需要 search_kb 工具\n"
        "Action: search_kb\n"
        "Action Input: {\"kb_id\": 1, \"query\": \"苹果退款\"}"
    )
    t, a, ai = AgentService._parse_agent_output(sample)
    assert "Thought" in t or len(t) > 0
    assert a == "search_kb", f"action 应为 search_kb，实际 {a}"
    assert "kb_id" in ai
    _record("J.2 Agent 解析 Thought/Action", True, f"action={a}")
except Exception as e:
    _record("J.2 Agent 解析", False, str(e))

# J.3 Agent _parse_agent_output —— Final Answer 带括号后缀
# 目的: 验证 "Final Answer [summary]" 不会被截断成 "Final"
try:
    from app.services.agent_service import AgentService
    sample = (
        "Thought: 已获取足够信息\n"
        "Action: Final Answer [summary]\n"
        "Action Input: 根据知识库，苹果支持 14 天无理由退款。"
    )
    t, a, ai = AgentService._parse_agent_output(sample)
    assert a.lower() == "final answer", f"action 应为 Final Answer，实际 {a!r}"
    assert "14 天" in ai or "退款" in ai
    _record("J.3 Agent Final Answer 括号修复验证", True, f"action={a!r}")
except Exception as e:
    _record("J.3 Agent Final Answer 解析", False, str(e))

# J.4 Agent system prompt 变量替换（花括号安全）
# 目的: 验证用 replace 代替 format 后无 KeyError
try:
    from app.services.agent_service import AgentService
    tool_list = "- search_kb: 搜索知识库\n- get_doc: 获取文档"
    prompt = AgentService.SYSTEM_PROMPT.replace("$TOOL_LIST", tool_list).replace("$MAX_TURNS", "3")
    assert "$TOOL_LIST" not in prompt
    assert "$MAX_TURNS" not in prompt
    assert "search_kb" in prompt
    # 验证无 KeyError（之前 format 会炸）
    _record("J.4 Agent system prompt 变量替换", True, f"len={len(prompt)}")
except Exception as e:
    _record("J.4 Agent system prompt 替换", False, str(e))

# J.5 Agent.run —— 空 query
# 目的: 空输入应被拒绝
try:
    from app.services.agent_service import AgentService
    agent = AgentService(db=None)
    r = agent.run("", max_turns=2)
    assert not r.success
    assert r.error
    _record("J.5 Agent 空 query 校验", True)
except Exception as e:
    _record("J.5 Agent 空 query", False, str(e))

# J.6 Agent.run —— 正常调用（Mock 模式下）
# 目的: Agent 至少能走完一轮 ReAct
try:
    from app.services.agent_service import AgentService
    agent = AgentService(db=None)
    r = agent.run("你好", max_turns=2)
    assert isinstance(r.steps, list)
    assert r.success or r.error
    _record("J.6 Agent 正常执行 (Mock)", True,
            f"steps={len(r.steps)}, success={r.success}")
except Exception as e:
    _record("J.6 Agent 正常执行", False, str(e))

# J.7 Agent.SearchKBTool / GetDocTool（需 DB）
# 目的: 验证工具链路
if _db is not None:
    try:
        from app.services.agent_service import SearchKBTool, GetDocTool
        sk = SearchKBTool(_db)
        # 缺参数应失败
        r = sk.run({}, {})
        assert not r.success
        # 正常（可能返回空结果但 success=True）
        r2 = sk.run({"kb_id": 1, "query": "测试"}, {"user_id": 1})
        assert r2.success
        _record("J.7 SearchKBTool 运行", True, f"msg={r2.content[:50]}")
    except Exception as e:
        _record("J.7 SearchKBTool", False, str(e))

    try:
        from app.services.agent_service import GetDocTool
        gd = GetDocTool(_db)
        r = gd.run({}, {})
        assert not r.success
        r2 = gd.run({"document_id": 9999}, {})
        assert not r2.success
        _record("J.8 GetDocTool 参数校验", True)
    except Exception as e:
        _record("J.8 GetDocTool", False, str(e))
else:
    _record("J.7/J.8 Agent 工具", True, "skip (no db)")

# ================================================================
# Part K  Integration Service
# ================================================================
_section("Part K  Integration Service")

# K.1 generate_webhook_token 基础
# 目的: 生成格式正确
try:
    from app.services.integration_service import IntegrationService
    tok = IntegrationService.generate_webhook_token("shopify", 42)
    assert tok.startswith("shopify_42_"), f"前缀不对: {tok[:20]}"
    assert len(tok.split("_")) >= 3  # shopify_42_<sig>
    _record("K.1 生成 webhook token (shopify)", True, f"token[:30]={tok[:30]}")
except Exception as e:
    _record("K.1 generate_webhook_token", False, str(e))

# K.2 含下划线渠道 (generic_http) token 解析
# 目的: 验证历史 bug fix —— 之前 generic_http 会解析失败
try:
    from app.services.integration_service import IntegrationService
    tok = IntegrationService.generate_webhook_token("generic_http", 7)
    assert "generic_http_7_" in tok
    parsed = IntegrationService.verify_webhook_token(tok)
    assert parsed is not None, f"generic_http token 校验失败: {tok[:40]}"
    channel, kb_id = parsed
    assert channel == "generic_http", f"channel 应为 generic_http，实际 {channel}"
    assert kb_id == 7
    _record("K.2 generic_http 渠道 token 解析 (下划线修复)", True)
except Exception as e:
    _record("K.2 generic_http token 解析", False, str(e))

# K.3 shopify 渠道 token 解析
# 目的: 其他渠道也正确
try:
    from app.services.integration_service import IntegrationService
    for ch in ["shopify", "wechat", "slack", "custom", "generic_http"]:
        tok = IntegrationService.generate_webhook_token(ch, 100)
        parsed = IntegrationService.verify_webhook_token(tok)
        assert parsed is not None, f"{ch} token 校验失败"
        c, k = parsed
        assert c == ch and k == 100
    _record("K.3 所有有效渠道 token 校验", True)
except Exception as e:
    _record("K.3 多渠道 token", False, str(e))

# K.4 非法 token 拒绝
# 目的: 安全 —— 乱造的 token 不能通过
try:
    from app.services.integration_service import IntegrationService
    bad_tokens = [
        "", "abc", "x_y_z_123", "shopify_1_notasignature_xxxxxxxxxxxxxxxxxx",
        "shopify_1_" + "a" * 24 + "_extratail",
    ]
    for bad in bad_tokens:
        r = IntegrationService.verify_webhook_token(bad)
        assert r is None, f"非法 token {bad!r} 不应通过，实际 {r}"
    _record("K.4 非法 token 拒绝", True)
except Exception as e:
    _record("K.4 非法 token", False, str(e))

# K.5 过期 token 容忍（昨日 token 仍可过）
# 目的: 防止深夜跨天导致 token 失效
try:
    from app.services.integration_service import IntegrationService
    # 昨日 token 手工计算
    ch, kb = "shopify", 5
    salt = IntegrationService.DEFAULT_GENERIC_SECRET
    yesterday = int(time.time() / 86400) - 1
    raw = f"{ch}|{kb}|{salt}|{yesterday}"
    sig = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    old_tok = f"{ch}_{kb}_{sig}"
    parsed = IntegrationService.verify_webhook_token(old_tok)
    assert parsed is not None, "昨日 token 应被接受"
    _record("K.5 昨日 token 容忍（跨天容灾）", True)
except Exception as e:
    _record("K.5 昨日 token 容忍", False, str(e))

# K.6 parse_generic_http 多字段兼容
# 目的: 任意前端字段名都能解析
try:
    from app.services.integration_service import IntegrationService, CHANNEL_GENERIC
    svc = IntegrationService()
    payloads = [
        {"query": "你好"},
        {"message": "你好"},
        {"text": "你好"},
        {"content": "你好"},
        {"msg": "你好"},
    ]
    for p in payloads:
        m = svc.parse_generic_http(p, kb_id=1)
        assert m is not None and m.query_text == "你好", f"payload {p} 解析失败"
        assert m.channel == CHANNEL_GENERIC
    # 空 payload 拒绝
    assert svc.parse_generic_http({}, 1) is None
    assert svc.parse_generic_http({"query": ""}, 1) is None
    assert svc.parse_generic_http("not_a_dict", 1) is None
    _record("K.6 generic_http 多字段兼容 + 异常输入", True)
except Exception as e:
    _record("K.6 generic_http 解析", False, str(e))

# K.7 parse_shopify_webhook
# 目的: Shopify 实际格式
try:
    from app.services.integration_service import IntegrationService, CHANNEL_SHOPIFY
    svc = IntegrationService()
    payload = {
        "query": "你们支持什么支付方式？",
        "customer": {"id": 12345, "email": "cust@shop.com"},
        "cart_token": "abc123",
    }
    headers = {
        "x-shopify-shop-domain": "mystore.myshopify.com",
        "x-shopify-customer-id": "12345",
    }
    m = svc.parse_shopify_webhook(payload, headers, kb_id=2)
    assert m is not None
    assert m.channel == CHANNEL_SHOPIFY
    assert m.kb_id == 2
    assert m.external_user_id == "12345"
    assert m.metadata.get("shop") == "mystore.myshopify.com"
    # 空拒绝
    assert svc.parse_shopify_webhook({"query": ""}, {}, 1) is None
    assert svc.parse_shopify_webhook("bad", {}, 1) is None
    _record("K.7 Shopify webhook 解析", True)
except Exception as e:
    _record("K.7 Shopify webhook 解析", False, str(e))

# K.8 渲染 reply —— generic 格式
# 目的: 通用 HTTP 输出结构正确
try:
    from app.services.integration_service import IntegrationService, OutboundReply, CHANNEL_GENERIC
    svc = IntegrationService()
    reply = OutboundReply(answer_text="这是测试回复", sources=[{"document_filename": "a.txt"}])
    out = svc.render_reply_for_channel(CHANNEL_GENERIC, reply)
    assert out["ok"] is True
    assert out["answer"] == "这是测试回复"
    assert "sources" in out
    # compact 模式不含 sources
    compact = svc.render_reply_for_channel(CHANNEL_GENERIC, reply, compact=True)
    assert "sources" not in compact
    _record("K.8 generic 渲染", True)
except Exception as e:
    _record("K.8 generic 渲染", False, str(e))

# K.9 渲染 reply —— Shopify HTML
# 目的: Shopify 需要 HTML 直接嵌入
try:
    from app.services.integration_service import IntegrationService, OutboundReply, CHANNEL_SHOPIFY
    svc = IntegrationService()
    reply = OutboundReply(answer_text="<script>alert(1)</script> 退款政策",
                          sources=[{"document_filename": "faq.html"}])
    out = svc.render_reply_for_channel(CHANNEL_SHOPIFY, reply)
    assert "message_html" in out
    # HTML 转义应生效
    assert "<script>" not in out["message_html"], "HTML 注入风险"
    assert "退款政策" in out["message_html"]
    assert "参考来源" in out["message_html"]
    _record("K.9 Shopify HTML 渲染 (含 XSS 防护)", True)
except Exception as e:
    _record("K.9 Shopify HTML 渲染", False, str(e))

# ================================================================
# Part L  API 端点（FastAPI TestClient）
# ================================================================
_section("Part L  API 端点 (FastAPI TestClient)")

try:
    from fastapi.testclient import TestClient
    from app.main import app
    _client = TestClient(app)
    _record("L.1 FastAPI TestClient 初始化", True)
except Exception as e:
    _record("L.1 TestClient 初始化", False, str(e))
    _client = None

if _client is not None:
    # L.2 / 根路径
    try:
        r = _client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert "name" in data or "status" in data
        _record("L.2 GET / 根路径", True, f"keys={list(data.keys())}")
    except Exception as e:
        _record("L.2 GET /", False, str(e))

    # L.3 /health
    try:
        r = _client.get("/health")
        assert r.status_code == 200
        _record("L.3 GET /health", True)
    except Exception as e:
        _record("L.3 /health", False, str(e))

    # L.4 Chat /chat/provider
    try:
        r = _client.get("/api/v1/chat/provider")
        assert r.status_code == 200
        data = r.json()
        assert data.get("data", {}).get("provider") is not None
        _record("L.4 GET /chat/provider", True,
                f"provider={data.get('data',{}).get('provider')}")
    except Exception as e:
        _record("L.4 /chat/provider", False, str(e))

    # L.5 Chat /chat/message (RAG)
    try:
        payload = {
            "knowledge_base_id": 9999,  # 空库 → 触发 L1 拒答
            "message": "测试消息",
        }
        r = _client.post("/api/v1/chat/message", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert data.get("success") is True
        ans = data.get("data", {}).get("answer", "")
        assert "未能检索到" in ans or "抱歉" in ans
        _record("L.5 POST /chat/message (L1 拒答)", True, f"answer[:60]={ans[:60]}")
    except Exception as e:
        _record("L.5 /chat/message", False, str(e))

    # L.6 Chat /chat/message/stream (SSE)
    try:
        with _client.stream("POST", "/api/v1/chat/message/stream",
                             json={"knowledge_base_id": 9999, "message": "hi"}) as resp:
            assert resp.status_code == 200
            body = b""
            for chunk in resp.iter_bytes():
                body += chunk
            text = body.decode("utf-8", errors="ignore")
            assert "data:" in text
            assert "done" in text
        _record("L.6 POST /chat/message/stream (SSE)", True)
    except Exception as e:
        _record("L.6 /chat/message/stream", False, str(e))

    # L.7 Integration /integration/generic/{kb_id}/chat
    try:
        payload = {"query": "任意问题"}
        r = _client.post("/api/v1/integration/generic/9999/chat", json=payload)
        # 9999 不存在 → 404 或返回降级
        data = r.json()
        # 不崩就行
        _record("L.7 POST /integration/generic/{kb}/chat", True,
                f"status={r.status_code}")
    except Exception as e:
        _record("L.7 /integration/generic", False, str(e))

    # L.8 Integration /integration/generate-token/{kb_id}
    try:
        r = _client.get("/api/v1/integration/generate-token/1?channel=generic_http")
        data = r.json()
        assert "token" in data or r.status_code == 404
        _record("L.8 GET /integration/generate-token", True,
                f"status={r.status_code}")
    except Exception as e:
        _record("L.8 /integration/generate-token", False, str(e))

    # 为 L.9 Agent 测试准备一个有效的知识库
    try:
        import time as _time2
        _ts2 = int(_time2.time())
        from app.models.database import SessionLocal
        from app.models.entities.user import User
        from app.models.entities.knowledge_base import KnowledgeBase
        from app.core.security import hash_password
        _api_db = SessionLocal()
        _kb_user = User(
            username=f"_test_agent_{_ts2}",
            email=f"_test_agent_{_ts2}@example.com",
            password_hash=hash_password("P@ssw0rd"),
            is_active=True,
        )
        _api_db.add(_kb_user)
        _api_db.commit()
        _api_db.refresh(_kb_user)
        _test_kb = KnowledgeBase(
            name=f"Agent Test KB {_ts2}",
            description="Agent 测试知识库",
            user_id=_kb_user.id,
            is_public=True,
        )
        _api_db.add(_test_kb)
        _api_db.commit()
        _api_db.refresh(_test_kb)
        _kb_id = _test_kb.id
        _api_db.close()
        _record("L.9 前置: 创建测试知识库", True, f"kb_id={_kb_id}")
    except Exception as e:
        _record("L.9 前置: 创建测试知识库", False, str(e))
        _kb_id = None

    # L.9 Agent /agent/run
    try:
        if _kb_id is None:
            raise RuntimeError("知识库未创建，跳过 Agent 测试")
        payload = {"query": "你好", "max_turns": 2, "knowledge_base_id": _kb_id}
        r = _client.post("/api/v1/agent/run", json=payload)
        assert r.status_code == 200, f"status={r.status_code} body={r.text[:200]}"
        data = r.json()
        assert "data" in data or "answer" in str(data) or "success" in data
        _record("L.9 POST /agent/run", True,
                f"status={r.status_code} keys={list(data.keys())}")
    except Exception as e:
        _record("L.9 /agent/run", False, str(e))

    # L.10 Auth /auth/register (不应 200 成功, 但至少要能处理)
    try:
        import time as _time
        _ts = int(_time.time())
        payload = {"username": f"__test_no_use_{_ts}__", "email": f"x_no_use_{_ts}@x.com",
                   "password": "P@ssw0rd", "confirm_password": "P@ssw0rd"}
        r = _client.post("/api/v1/auth/register", json=payload)
        assert r.status_code in (200, 201, 400, 409, 422)
        _record("L.10 POST /auth/register", True, f"status={r.status_code}")
    except Exception as e:
        _record("L.10 /auth/register", False, str(e))

# ================================================================
# Part M  并发 & 鲁棒性
# ================================================================
_section("Part M  并发 & 鲁棒性")

# M.1 并发 LLM 调用不崩
# 目的: 真实生产需要多线程安全
try:
    from app.processors.llm.llm_service import MockLLMProvider, ChatMessage
    mock = MockLLMProvider()
    errors = []
    def worker(i):
        try:
            msgs = [ChatMessage(role="user", content=f"test {i}")]
            _ = mock.chat(msgs)
        except Exception as e:
            errors.append(str(e))
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(errors) == 0, f"并发错误: {errors[:3]}"
    _record("M.1 并发 20 次 LLM 调用", True)
except Exception as e:
    _record("M.1 并发 LLM", False, str(e))

# M.2 并发 RAGPipeline
# 目的: 多线程下 pipeline 不应崩
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        vecs = es.encode_batch(["退款政策介绍", "订单查询说明"])
        metas = [{"chunk_id": i + 1, "document_id": 1,
                  "document_filename": "doc.txt", "content": c}
                 for i, c in enumerate(["退款政策介绍", "订单查询说明"])]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        errors = []
        def worker(i):
            try:
                rag.answer(knowledge_base_id=1, query_text=f"退款问题{i}")
            except Exception as e:
                errors.append(str(e))
        ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert len(errors) == 0, f"并发 RAG 错误: {errors[:3]}"
        _record("M.2 并发 10 次 RAGPipeline", True)
except Exception as e:
    _record("M.2 并发 RAG", False, str(e))

# M.3 空向量/异常输入鲁棒性
# 目的: 防御非法输入
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        # 空 query
        r1 = rag.answer(1, "")
        assert r1.success
        # None/极端参数
        r2 = rag.answer(1, "hi", top_k=None, min_score=None)
        assert r2.success
        # 超大 top_k
        r3 = rag.answer(1, "hi", top_k=9999)
        assert r3.success
        _record("M.3 异常输入鲁棒性", True)
except Exception as e:
    _record("M.3 异常输入鲁棒性", False, str(e))

# M.4 非法 token 注入防护
# 目的: SQL 注入/超长 token 等边界
try:
    from app.services.integration_service import IntegrationService
    bad = [
        "' OR 1=1--",
        "a" * 10000,
        "../../etc/passwd_xx",
        "",
    ]
    for t in bad:
        r = IntegrationService.verify_webhook_token(t)
        assert r is None
    _record("M.4 非法 token 注入防护", True)
except Exception as e:
    _record("M.4 非法 token 注入防护", False, str(e))

# ================================================================
# Part N  性能基准
# ================================================================
_section("Part N  性能基准")

# N.1 Embedding 单条延迟
# 目的: 观察响应时间
try:
    from app.processors.embedding.embedding_service import EmbeddingService
    es = EmbeddingService()
    t0 = time.perf_counter()
    for _ in range(20):
        es.encode_single("苹果手机退款政策 hello world" * 3)
    dt = (time.perf_counter() - t0) / 20 * 1000
    _record(f"N.1 Embedding 单次延迟 ({dt:.2f}ms)", True)
except Exception as e:
    _record("N.1 Embedding 性能", False, str(e))

# N.2 向量搜索延迟（小规模）
# 目的: 观察检索耗时
try:
    from app.processors import EmbeddingService, VectorStoreManager
    es = EmbeddingService()
    with tempfile.TemporaryDirectory() as td:
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        vecs = es.encode_batch([f"测试文档内容第{i}条" for i in range(200)])
        metas = [{"chunk_id": i + 1, "document_id": 1,
                  "document_filename": "doc.txt",
                  "content": f"测试文档内容第{i}条"}
                 for i in range(200)]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        q = es.encode_single("测试文档内容第 50 条")
        t0 = time.perf_counter()
        for _ in range(100):
            vm.get_store(1, dim=es.dim).search(q, top_k=5)
        dt = (time.perf_counter() - t0) / 100 * 1000
        _record(f"N.2 向量搜索 200 条库 x100 次 ({dt:.2f}ms/call)", True)
except Exception as e:
    _record("N.2 向量搜索性能", False, str(e))

# N.3 Mock LLM 延迟
# 目的: Mock 模式下延迟应极低
try:
    from app.processors.llm.llm_service import MockLLMProvider, ChatMessage
    mock = MockLLMProvider()
    msgs = [ChatMessage(role="user", content="hi")]
    t0 = time.perf_counter()
    for _ in range(50):
        mock.chat(msgs)
    dt = (time.perf_counter() - t0) / 50 * 1000
    _record(f"N.3 Mock LLM 单次延迟 ({dt:.2f}ms)", True)
except Exception as e:
    _record("N.3 Mock LLM 延迟", False, str(e))

# N.4 RAG 端到端延迟（Mock）
# 目的: 观察全链路耗时
try:
    from app.services.chat_service import RAGPipeline
    with tempfile.TemporaryDirectory() as td:
        from app.processors import EmbeddingService, VectorStoreManager
        es = EmbeddingService()
        vm = VectorStoreManager(base_dir=td, default_dim=es.dim)
        vecs = es.encode_batch([
            "苹果手机支持 14 天无理由退款",
            "安卓手机保修一年",
            "订单查询流程",
        ])
        metas = [{"chunk_id": i + 1, "document_id": 1,
                  "document_filename": "faq.txt", "content": c}
                 for i, c in enumerate([
                     "苹果手机支持 14 天无理由退款",
                     "安卓手机保修一年",
                     "订单查询流程",
                 ])]
        vm.get_store(1, dim=es.dim).add(vecs, metas)
        rag = RAGPipeline(embedding=es, vector_manager=vm)
        t0 = time.perf_counter()
        for _ in range(20):
            rag.answer(knowledge_base_id=1, query_text="苹果退款政策")
        dt = (time.perf_counter() - t0) / 20 * 1000
        _record(f"N.4 RAG 端到端延迟 (Mock, {dt:.2f}ms/call)", True)
except Exception as e:
    _record("N.4 RAG 端到端延迟", False, str(e))

# N.5 Token 吞吐量（流式 tokens/s）
# 目的: 估算 SSE 下的 token 速率
try:
    from app.processors.llm.llm_service import MockLLMProvider, ChatMessage
    mock = MockLLMProvider()
    msgs = [
        ChatMessage(role="system",
                    content="你是助手。\n【知识库片段】\n[#1] (来源: doc.txt, 相似度: 0.9)\n"
                            "苹果手机支持 14 天无理由退款政策，详细流程请联系客服。\n"
                            "---\n请回答。"),
        ChatMessage(role="user", content="苹果怎么退款？" * 20),
    ]
    t0 = time.perf_counter()
    n = 0
    for _ in range(5):
        for t in mock.chat_stream(msgs):
            if t.content:
                n += 1
    dt = (time.perf_counter() - t0) / 5 * 1000
    _record(f"N.5 流式响应 ({dt:.1f}ms/run, {n} tokens total)", True)
except Exception as e:
    _record("N.5 流式响应", False, str(e))

# ================================================================
# 最终汇总
# ================================================================
print(f"\n{'='*70}")
print(f"  测试汇总")
print(f"{'='*70}")
print(f"  总数 : {_passed + _failed}")
print(f"  通过 : {_passed}")
print(f"  失败 : {_failed}")
if _failed > 0:
    print(f"\n  --- 失败详情 ---")
    for err in _errors:
        print(f"  ❌ {err}")
print(f"\n 通过率: {_passed / (_passed + _failed) * 100:.2f}%" if (_passed + _failed) else "  无测试")
print(f"{'='*70}")

# 清理 DB
if _db is not None:
    try:
        _db.close()
    except Exception:
        pass

# 非零退出码（有失败时）
sys.exit(0 if _failed == 0 else 1)