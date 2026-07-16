# -*- coding: utf-8 -*-
"""Day 4 - Embedding 抽象层强化 全面测试

涵盖单元测试 + 集成测试 + 质量评估 + API 测试
运行: python _day4_test.py
"""
from __future__ import annotations

import math
import os
import sys
import time

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


results = []  # (description, bool)


def log(msg: str, indent: int = 0) -> None:
    prefix = "  " * indent
    try:
        print(prefix + msg)
    except Exception:
        print((prefix + msg).encode("ascii", errors="replace").decode("ascii"))
    sys.stdout.flush()


def check(desc: str, condition: bool, indent: int = 1) -> None:
    results.append((desc, bool(condition)))
    tag = "[OK] " if condition else "[FAIL]"
    log("%s %s" % (tag, desc), indent=indent)


# ============================================================
#  导入测试
# ============================================================
log("=" * 64)
log("Day 4 - Embedding 抽象层强化 综合测试")
log("=" * 64)

log("\n[1] 模块导入")
try:
    from app.processors.embedding.embedding_service import (
        BaseEmbeddingProvider,
        MockEmbeddingProvider,
        LocalNumpyEmbeddingProvider,
        CachingEmbeddingProvider,
        EmbeddingService,
        normalize_vec,
        cosine_similarity,
        validate_vectors,
    )
    check("EmbeddingService & Providers 导入", True)
except Exception as e:
    check("EmbeddingService & Providers 导入: %s" % str(e)[:80], False)

try:
    from app.core.config import settings
    check("config 导入", True)
except Exception as e:
    check("config 导入: %s" % str(e)[:80], False)

try:
    from fastapi.testclient import TestClient
    from app.main import app
    check("FastAPI TestClient 导入", True)
except Exception as e:
    check("FastAPI TestClient: %s" % str(e)[:80], False)


# ============================================================
#  [2] Mock Embedding Provider 单元测试
# ============================================================
log("\n[2] MockEmbeddingProvider 单元测试")
try:
    mock = MockEmbeddingProvider(dim=128, seed=42)
    check("构造 (dim=128, seed=42)", True)

    texts = [
        "RAG 结合检索与语言模型生成。",
        "向量数据库用于相似度搜索。",
        "Python 是解释型高级编程语言。",
    ]
    vectors = mock.encode(texts)
    check("encode 产生 %d 个向量" % len(texts), len(vectors) == len(texts))
    check("每个向量 128 维", all(len(v) == 128 for v in vectors))

    # 相同文本 -> 相同向量（确定性）
    v1 = mock.encode_single("hello world")
    v2 = mock.encode_single("hello world")
    check("相同文本 -> 相同向量 (deterministic)", v1 == v2)

    # 向量是有限浮点数
    all_finite = all(math.isfinite(x) for v in vectors for x in v)
    check("所有向量元素都是有限浮点数", all_finite)

    # L2 范数接近 1（已归一化）
    norms = [math.sqrt(sum(x * x for x in v)) for v in vectors]
    check("所有向量都已 L2 归一化 (范数 ~= 1.0)",
          all(0.99 < n < 1.01 for n in norms))

    # 非空输入不是零向量
    check("非空文本产生非零向量", all(n > 0.1 for n in norms))

    # 空输入: encode([]) 返回 []
    empty = mock.encode([])
    check("encode([]) 返回空列表", empty == [])
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:400])


# ============================================================
#  [3] LocalNumpyEmbeddingProvider 单元测试
# ============================================================
log("\n[3] LocalNumpyEmbeddingProvider 单元测试")
try:
    local = LocalNumpyEmbeddingProvider(dim=128, ngram_size=3)
    check("构造 (dim=128, ngram=3)", True)

    # 相似语义应该比不相关语义更相似
    rag1 = "RAG 使用检索增强生成模型，将外部知识库与大语言模型结合。"
    rag2 = "检索增强生成（RAG）是一种结合向量搜索与 LLM 的问答技术。"
    cook = "今天的晚餐需要准备鸡肉、土豆和洋葱，然后炖煮 40 分钟。"

    v_rag1 = local.encode_single(rag1)
    v_rag2 = local.encode_single(rag2)
    v_cook = local.encode_single(cook)

    sim_rag = cosine_similarity(v_rag1, v_rag2)
    sim_mix1 = cosine_similarity(v_rag1, v_cook)
    sim_mix2 = cosine_similarity(v_rag2, v_cook)

    log("  相似度: RAG<->RAG = %.4f  |  RAG<->cooking = %.4f / %.4f"
        % (sim_rag, sim_mix1, sim_mix2))

    # 同类文本的相似度应当高于异类
    check("同类文本相似度 > 异类文本相似度 (sim_rag > sim_mix1)",
          sim_rag > sim_mix1)
    check("同类文本相似度 > 异类文本相似度 (sim_rag > sim_mix2)",
          sim_rag > sim_mix2)

    # 向量质量
    check("向量维度正确", all(len(v) == 128 for v in [v_rag1, v_rag2, v_cook]))
    norms = [math.sqrt(sum(x * x for x in v)) for v in [v_rag1, v_rag2, v_cook]]
    check("向量已归一化 (范数 ~= 1.0)",
          all(0.99 < n < 1.01 for n in norms))

    # 相同文本 -> 相同向量
    check("相同文本 -> 相同向量", local.encode_single("测试") == local.encode_single("测试"))

    # 批量编码
    batch = ["文本 %d" % i for i in range(10)]
    vecs = local.encode(batch)
    check("批量 encode 10 个文本 -> 10 个向量", len(vecs) == 10)
    check("批量向量维度正确", all(len(v) == 128 for v in vecs))
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:400])


# ============================================================
#  [4] CachingEmbeddingProvider 缓存测试
# ============================================================
log("\n[4] CachingEmbeddingProvider 缓存测试")
try:
    inner = MockEmbeddingProvider(dim=64)
    cached = CachingEmbeddingProvider(inner, max_size=5)
    check("构造 CachingEmbeddingProvider (cache_size=5)", True)

    # 首次 encode - 应当 miss
    texts = ["apple", "banana", "cherry", "date", "elderberry"]
    v1 = cached.encode(texts)
    stats1 = cached.stats()
    check("首次 encode: %d 个 miss" % len(texts), stats1["misses"] == len(texts))
    check("首次 encode: hits == 0", stats1["hits"] == 0)
    check("cache size == %d" % len(texts), stats1["size"] == len(texts))

    # 再次 encode 相同文本 - 应当全 hit
    v2 = cached.encode(texts)
    stats2 = cached.stats()
    check("再次 encode 相同文本: hits = %d" % len(texts), stats2["hits"] == len(texts))
    check("两次结果完全一致", v1 == v2)

    # 超过 cache size 应触发逐出
    new_texts = ["fig", "grape", "kiwi", "lemon", "mango", "orange"]
    cached.encode(new_texts)
    stats3 = cached.stats()
    check("新增 %d 条后 cache 不超过 size=5" % len(new_texts),
          stats3["size"] <= 5)

    # 清空
    cached.clear()
    check("clear 后 cache size=0", cached.stats()["size"] == 0)
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:400])


# ============================================================
#  [5] normalize_vec / cosine_similarity / validate_vectors 工具函数
# ============================================================
log("\n[5] 工具函数测试 (normalize_vec / cosine_similarity / validate_vectors)")
try:
    import math as _m

    v = [3.0, 4.0, 0.0]
    n = normalize_vec(v)
    n_norm = _m.sqrt(sum(x * x for x in n))
    check("normalize_vec([3,4,0]) 产生单位向量 (|v|~=1)", abs(n_norm - 1.0) < 1e-6)

    # 自身相似度 = 1
    a = normalize_vec([1.0, 2.0, 3.0])
    check("cosine_similarity(a, a) == 1.0",
          abs(cosine_similarity(a, a) - 1.0) < 1e-6)

    # 正交向量相似度 ≈ 0
    check("cosine_similarity([1,0], [0,1]) == 0.0",
          abs(cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-6)

    # 相反向量相似度 = -1
    check("cosine_similarity([1,0], [-1,0]) == -1.0",
          abs(cosine_similarity([1.0, 0.0], [-1.0, 0.0]) + 1.0) < 1e-6)

    # 零向量 -> 不 crash
    zero = [0.0, 0.0, 0.0]
    nz = normalize_vec(zero)
    check("normalize_vec(零向量) 返回合法值（长度>0）", len(nz) > 0)

    # validate_vectors 检测坏向量
    try:
        validate_vectors([[1.0, float("nan")]], expected_dim=2)
        check("validate_vectors 检测 NaN", False)
    except Exception:
        check("validate_vectors 检测 NaN 抛异常", True)

    try:
        validate_vectors([[1.0, 2.0, 3.0]], expected_dim=2)
        check("validate_vectors 检测维度不符", False)
    except Exception:
        check("validate_vectors 检测维度不符抛异常", True)
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:400])


# ============================================================
#  [6] EmbeddingService 门面层
# ============================================================
log("\n[6] EmbeddingService 门面层")
try:
    # 默认设置（无远程 API）
    service = EmbeddingService.from_settings(settings)
    check("from_settings 构造成功", True)
    check("provider name 存在", isinstance(service.provider_name, str) and service.provider_name != "")
    check("dim 为正整数", isinstance(service.dim, int) and service.dim > 0)

    # quality_report
    report = service.quality_report()
    check("quality_report 包含 'provider'", "provider" in report)
    check("quality_report 包含 'cosine_similarity_matrix'",
          "cosine_similarity_matrix" in report)
    check("quality_report 相似度矩阵为方阵",
          isinstance(report["cosine_similarity_matrix"], list) and
          all(len(row) == len(report["cosine_similarity_matrix"])
              for row in report["cosine_similarity_matrix"]))

    log("  provider=%s, dim=%d" % (report["provider"], report["dim"]))
    log("  相似度矩阵:")
    for row in report["cosine_similarity_matrix"]:
        log("    " + " ".join("%.3f" % s for s in row))
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:400])


# ============================================================
#  [7] 性能测试
# ============================================================
log("\n[7] 性能测试")
try:
    local = LocalNumpyEmbeddingProvider(dim=128, ngram_size=3)
    long_text = "这是一段用于性能测试的中文文本。" * 20
    batch = [long_text + " v%d" % i for i in range(50)]

    t0 = time.perf_counter()
    vectors = local.encode(batch)
    elapsed = time.perf_counter() - t0

    check("50 个长文本编码成功", len(vectors) == 50)
    log("  50 个长文本编码耗时: %.3fs (avg %.1f ms/text)"
        % (elapsed, 1000 * elapsed / len(batch)))

    # 加上缓存后的二次性能
    cached = CachingEmbeddingProvider(local, max_size=500)
    cached.encode(batch)  # 首次：warm up
    t1 = time.perf_counter()
    cached.encode(batch)  # 二次：应全命中
    elapsed_cached = time.perf_counter() - t1
    check("二次 encode（全命中缓存）应显著更快", elapsed_cached < elapsed)
    log("  全缓存命中 50 个文本耗时: %.3fs (avg %.1f ms/text)"
        % (elapsed_cached, 1000 * elapsed_cached / len(batch)))
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:400])


# ============================================================
#  [8] API 测试
# ============================================================
log("\n[8] API 集成测试 (FastAPI)")
try:
    client = TestClient(app)

    # 8a. /v1/embeddings/status
    r = client.get("/v1/embeddings/status")
    check("GET /v1/embeddings/status -> 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        check("status 返回 success=True", body.get("success") is True)
        data = body.get("data", {})
        check("status 包含 provider", "provider" in data)
        check("status 包含 dim", isinstance(data.get("dim"), int))
        check("status 包含 sample_similarity_matrix",
              isinstance(data.get("sample_similarity_matrix"), list))
        log("  provider=%s, dim=%d" % (data.get("provider"), data.get("dim")))

    # 8b. POST /v1/embeddings/encode
    r = client.post(
        "/v1/embeddings/encode",
        json={"texts": [
            "RAG 系统结合向量检索与语言模型。",
            "Python 是一门流行的编程语言。",
            "天气晴朗，适合散步。"
        ]}
    )
    check("POST /v1/embeddings/encode -> 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        check("encode 返回 success=True", body.get("success") is True)
        data = body.get("data", {})
        check("encode count == 3", data.get("count") == 3)
        check("每个 item 都有 dim/norm/sample_preview",
              all("dim" in it and "norm" in it and "sample_preview" in it
                  for it in data.get("items", [])))

    # 8c. POST /v1/embeddings/encode-single
    r = client.post(
        "/v1/embeddings/encode-single",
        json={"text": "单次向量化测试文本。"}
    )
    check("POST /v1/embeddings/encode-single -> 200", r.status_code == 200)

    # 8d. POST /v1/embeddings/similarity
    r = client.post(
        "/v1/embeddings/similarity",
        json={
            "text_a": "RAG 利用向量数据库检索上下文。",
            "text_b": "检索增强生成依赖向量相似度搜索。"
        }
    )
    check("POST /v1/embeddings/similarity -> 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        data = body.get("data", {})
        score = data.get("score", -1)
        check("similarity 分数在 [-1, 1] 之间", -1.0 <= float(score) <= 1.0)
        check("similarity 有文字解读",
              isinstance(data.get("interpretation"), str) and len(data["interpretation"]) > 0)
        log("  同类文本相似度分数 = %.4f (%s)" % (score, data.get("interpretation")))

    # 8e. 异类文本相似度
    r = client.post(
        "/v1/embeddings/similarity",
        json={
            "text_a": "RAG 利用向量数据库检索上下文。",
            "text_b": "鸡肉炖土豆需要中火慢煮 40 分钟。"
        }
    )
    if r.status_code == 200:
        data = r.json().get("data", {})
        log("  异类文本相似度分数 = %.4f (%s)" %
            (data.get("score"), data.get("interpretation")))

    # 8f. 错误请求: 空 texts 列表
    r = client.post("/v1/embeddings/encode", json={"texts": []})
    check("空文本列表 -> 422 (schema 校验)", r.status_code in (400, 422))

    # 8g. 错误请求: 缺少必填字段
    r = client.post("/v1/embeddings/encode-single", json={"x": "missing 'text'"})
    check("缺少 'text' 字段 -> 422", r.status_code == 422)
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:400])


# ============================================================
#  与 Day 3 上游模块的兼容性测试
# ============================================================
log("\n[9] 与 Day 3 DocumentService 兼容性（向量检索）")
try:
    from app.services.document_service import DocumentService
    from app.models.database import Base, engine, SessionLocal

    # 简单 smoke test：保证 DocumentService 能够被构造
    session = SessionLocal()
    try:
        ds = DocumentService(db=session)
        check("DocumentService 可构造", True)
        check("DocumentService.embedding.dim > 0", ds.embedding.dim > 0)

        # 测试向量搜索: 搜索测试（如果有 chunk）
        from sqlalchemy import text as sqltext
        count_row = session.execute(
            sqltext("SELECT COUNT(*) FROM chunks")
        ).fetchone()
        chunk_count = int(count_row[0]) if count_row else 0
        log("  现有 chunk 数量: %d" % chunk_count)
    finally:
        session.close()
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:400])


# ============================================================
#  汇总
# ============================================================
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
total = len(results)

log("\n" + "=" * 64)
log("测试结果汇总: %d / %d 通过，%d 失败" % (passed, total, failed))
log("=" * 64)

if failed > 0:
    log("\n失败项:")
    for desc, ok in results:
        if not ok:
            log("  [FAIL] " + desc, indent=0)

log("")
log("Day 4 交付内容小结:")
log("  1) MockEmbeddingProvider - 基于 hash 的确定性伪向量 (开发测试用)")
log("  2) LocalNumpyEmbeddingProvider - ngram+hash, numpy 加速, 离线可用")
log("  3) RemoteAPIEmbeddingProvider - OpenAI/DeepSeek 兼容 API, 支持重试/批量")
log("  4) CachingEmbeddingProvider - LRU 缓存装饰器, 提供 hit/miss 统计")
log("  5) EmbeddingService - 统一门面层, 根据 settings 自动选择 provider")
log("  6) 工具函数: normalize_vec / cosine_similarity / validate_vectors")
log("  7) Schemas: EncodeRequest / SimilarityRequest / EmbeddingStatus 等")
log("  8) API: GET /v1/embeddings/status")
log("         POST /v1/embeddings/encode")
log("         POST /v1/embeddings/encode-single")
log("         POST /v1/embeddings/similarity")
log("")
log("下一个里程碑: Day 5 - 向量存储与检索 (FAISS 深度集成 + 索引管理)")