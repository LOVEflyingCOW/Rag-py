# -*- coding: utf-8 -*-
"""Day 5 - 向量存储与检索（FAISS 深度集成）综合测试

运行: python _day5_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import random

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

results = []  # (description, bool)

def log(msg: str, indent: int = 0) -> None:
    prefix = "  " * indent
    print(prefix + msg)
    sys.stdout.flush()

def check(desc: str, cond: bool, indent: int = 1) -> None:
    results.append((desc, bool(cond)))
    log(("[OK]  " if cond else "[FAIL]") + desc, indent=indent)


# ---------- 导入 ----------
log("=" * 64)
log("Day 5 - 向量存储与检索 综合测试")
log("=" * 64)

log("\n[1] 模块导入")
try:
    import numpy as _np
    _has_numpy = True
    check("numpy 已安装", True)
except Exception:
    _has_numpy = False
    check("numpy 已安装", False)

try:
    import faiss as _faiss
    _has_faiss = True
    check("faiss 已安装", True)
except Exception:
    _has_faiss = False
    check("faiss 未安装 (将回退到 PurePythonVectorStore)", True)

try:
    from app.processors.retrieval.vector_store import (
        BaseVectorStore,
        FAISSVectorStore,
        IVFVectorStore,
        PurePythonVectorStore,
        VectorStoreManager,
    )
    check("VectorStore 核心类导入", True)
except Exception as e:
    check("VectorStore 核心类导入: %s" % str(e)[:80], False)

try:
    from app.processors.embedding.embedding_service import EmbeddingService
    from app.core.config import settings
    check("EmbeddingService / settings 导入", True)
except Exception as e:
    check("EmbeddingService / settings 导入: %s" % str(e)[:80], False)

try:
    from fastapi.testclient import TestClient
    from app.main import app
    check("FastAPI TestClient 导入", True)
except Exception as e:
    check("FastAPI TestClient: %s" % str(e)[:80], False)


# ---------- 随机种子（可重复） ----------
random.seed(42)
if _has_numpy:
    _np.random.seed(42)


# ============================================================
#  [2] PurePythonVectorStore - 核心功能单元测试
# ============================================================
log("\n[2] PurePythonVectorStore - 核心功能测试")
try:
    store = PurePythonVectorStore(dim=128)
    check("构造 (dim=128)", True)

    # 添加 20 个向量
    vectors = []
    metas = []
    for i in range(20):
        # 构造有语义的向量组：前 10 个围绕锚点 A，后 10 个围绕锚点 B
        if _has_numpy:
            if i < 10:
                v = _np.random.randn(128).astype(float)
                v[:64] += 5.0  # 偏向锚点 A
            else:
                v = _np.random.randn(128).astype(float)
                v[64:] += 5.0  # 偏向锚点 B
            vectors.append(v.tolist())
        else:
            v = [random.gauss(0, 1) for _ in range(128)]
            if i < 10:
                v[:64] = [x + 5 for x in v[:64]]
            else:
                v[64:] = [x + 5 for x in v[64:]]
            vectors.append(v)
        metas.append({"chunk_id": i + 1, "document_id": (i // 5) + 1, "group": "A" if i < 10 else "B"})

    indices = store.add(vectors, metas)
    check("add 20 个向量 -> 20 个 indices", len(indices) == 20)
    check("索引连续 0..19", indices == list(range(20)))

    # total() 正确性
    check("total() == 20", store.total() == 20)

    # 搜索 A 组 - query 是 A 组的一个样本
    if _has_numpy:
        query_v = _np.random.randn(128).astype(float)
        query_v[:64] += 5.0
        query = query_v.tolist()
    else:
        query = [random.gauss(0, 1) for _ in range(128)]
        query[:64] = [x + 5 for x in query[:64]]

    results_search = store.search(query, top_k=5)
    check("search 返回 5 个结果", len(results_search) == 5)

    # 前 5 个结果应该主要来自 A 组 (chunk_id 1-10)
    a_group_hits = sum(
        1 for (vidx, score) in results_search
        if (store.get_metadata(vidx) or {}).get("group") == "A"
    )
    check("A 组 query 的 top-5 中 >= 3 个来自 A 组 (相似度有区分度)", a_group_hits >= 3)
    log("  A 组 query top-5 命中 A 组: %d/5" % a_group_hits)

    # score 范围 [0, 1]
    check("所有 score 都在 [0, 1]", all(0.0 <= s <= 1.0 for _, s in results_search))

    # 分数递减
    check("search 结果按分数从高到低返回",
          all(results_search[i][1] >= results_search[i + 1][1]
              for i in range(len(results_search) - 1)))

    # 维度不匹配的 query 抛异常
    try:
        store.search([1.0] * 64, top_k=3)
        check("维度不匹配的 query 应抛异常", False)
    except Exception:
        check("维度不匹配的 query 抛异常 (预期行为)", True)

    # 元数据读取
    m = store.get_metadata(5)
    check("get_metadata(5) 包含 chunk_id=6", m and m.get("chunk_id") == 6)

    # stats()
    stats = store.stats()
    check("stats() 包含 total_vectors=20", stats.get("total_vectors") == 20)
    check("stats() 包含 backend='pure-python'", stats.get("backend") == "pure-python")

    # consistency check
    consistent, issues = store.check_consistency()
    check("check_consistency() 返回 True", consistent is True)
    check("无 issues 列表", len(issues) == 0)
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:500])


# ============================================================
#  [3] FAISSVectorStore - FAISS 精确索引测试 (有 FAISS 才跑)
# ============================================================
if _has_faiss and _has_numpy:
    log("\n[3] FAISSVectorStore - IndexFlatIP 精确搜索")
    try:
        store = FAISSVectorStore(dim=128)
        check("构造 FAISSVectorStore", True)

        # 增量添加: 分批 add，验证 total 递增
        groups = []
        for batch_i in range(3):
            batch_vecs = []
            batch_metas = []
            for j in range(15):
                v = _np.random.randn(128).astype(float)
                # 第 batch_i 组加偏移，形成聚类
                v[batch_i * 40: (batch_i + 1) * 40] += 10.0
                batch_vecs.append(v.tolist())
                batch_metas.append({"chunk_id": batch_i * 15 + j, "cluster": batch_i})
            idx = store.add(batch_vecs, batch_metas)
            groups.append(idx)

        check("3 批 × 15 = 45 个向量", store.total() == 45)
        check("stats() backend = faiss-IndexFlatIP",
              store.stats().get("backend") == "faiss-IndexFlatIP")

        # 用 cluster 0 的一个向量做 query
        q_vec = _np.random.randn(128).astype(float)
        q_vec[:40] += 10.0
        res = store.search(q_vec.tolist(), top_k=10)
        check("search cluster-0 query 返回 10 个结果", len(res) == 10)

        # 检查 top 结果主要来自 cluster 0
        cluster_0_hits = sum(
            1 for (vidx, s) in res
            if (store.get_metadata(vidx) or {}).get("cluster") == 0
        )
        check("cluster-0 query 的 top-10 中 >= 6 个来自 cluster-0", cluster_0_hits >= 6)
        log("  cluster-0 查询: %d/10 命中 cluster-0" % cluster_0_hits)

        # score 都在 [0, 1]
        check("所有 score ∈ [0, 1]", all(0.0 <= s <= 1.0 for _, s in res))
    except Exception as e:
        log("  [EXCEPTION] %s" % str(e))
        import traceback
        log("  %s" % traceback.format_exc()[:500])
else:
    log("\n[3] FAISSVectorStore - 跳过 (未安装 faiss)")


# ============================================================
#  [4] IVFVectorStore - 大规模近似搜索测试 (有 FAISS 才跑)
# ============================================================
if _has_faiss and _has_numpy:
    log("\n[4] IVFVectorStore - 倒排索引大规模搜索")
    try:
        dim = 64
        n_clusters = 5
        n_per_cluster = 200
        store = IVFVectorStore(dim=dim, nlist=n_clusters, nprobe=3)

        # 构造 1000 个有聚类结构的向量
        all_vecs = []
        all_metas = []
        for c in range(n_clusters):
            for i in range(n_per_cluster):
                v = _np.random.randn(dim).astype(float) * 0.5
                # 每个聚类一个锚点偏移
                anchor = _np.zeros(dim, dtype=float)
                anchor[c * (dim // n_clusters): (c + 1) * (dim // n_clusters)] = 5.0
                v += anchor
                all_vecs.append(v.tolist())
                all_metas.append({"chunk_id": len(all_vecs), "cluster": c})

        idx = store.add(all_vecs, all_metas)
        check("添加 %d 个向量 成功" % len(all_vecs), len(idx) == len(all_vecs))
        check("total() == %d" % len(all_vecs), store.total() == len(all_vecs))

        # 搜索 cluster 2 的向量
        q = _np.zeros(dim, dtype=float)
        q[2 * (dim // n_clusters): 3 * (dim // n_clusters)] = 5.0
        res = store.search(q.tolist(), top_k=20)
        check("IVF search 返回 20 个结果", len(res) == 20)

        cluster_2_hits = sum(
            1 for (vidx, s) in res
            if (store.get_metadata(vidx) or {}).get("cluster") == 2
        )
        check("cluster-2 查询 top-20 中 >= 10 个来自 cluster-2", cluster_2_hits >= 10)
        log("  cluster-2 查询: %d/20 命中 cluster-2" % cluster_2_hits)
    except Exception as e:
        log("  [EXCEPTION] %s" % str(e))
        import traceback
        log("  %s" % traceback.format_exc()[:500])
else:
    log("\n[4] IVFVectorStore - 跳过 (未安装 faiss)")


# ============================================================
#  [5] VectorStoreManager - 多知识库管理 + 持久化测试
# ============================================================
log("\n[5] VectorStoreManager - 管理 + 持久化")
try:
    with tempfile.TemporaryDirectory(dir=BACKEND_DIR) as tmpdir:
        # 建 3 个知识库索引
        mgr = VectorStoreManager(base_dir=tmpdir, default_dim=64)
        check("构造 VectorStoreManager", True)

        dim = 64
        # kb 1: 50 个向量
        store1 = mgr.get_store(1, dim=dim)
        for i in range(50):
            v = _np.random.randn(dim).astype(float).tolist() if _has_numpy else [random.gauss(0, 1) for _ in range(dim)]
            store1.add([v], [{"chunk_id": i}])
        check("kb=1: add 50 个向量 ok", store1.total() == 50)

        # kb 2: 30 个向量
        store2 = mgr.get_store(2, dim=dim)
        for i in range(30):
            v = _np.random.randn(dim).astype(float).tolist() if _has_numpy else [random.gauss(0, 1) for _ in range(dim)]
            store2.add([v], [{"chunk_id": i}])
        check("kb=2: add 30 个向量 ok", store2.total() == 30)

        # kb 3: 120 个向量
        store3 = mgr.get_store(3, dim=dim)
        for i in range(120):
            v = _np.random.randn(dim).astype(float).tolist() if _has_numpy else [random.gauss(0, 1) for _ in range(dim)]
            store3.add([v], [{"chunk_id": i}])
        check("kb=3: add 120 个向量 ok", store3.total() == 120)

        # 持久化
        ok1 = mgr.save(1)
        ok2 = mgr.save(2)
        ok3 = mgr.save(3)
        check("save_all -> 3 个都保存成功", all([ok1, ok2, ok3]))

        # 磁盘上有 3 个 kb 文件
        on_disk = mgr.list_stored_kbs()
        check("磁盘上有 3 个知识库索引", sorted(on_disk) == [1, 2, 3])

        # 清内存，重新加载
        mgr.clear_memory()
        check("clear_memory 后内部字典为空", len(getattr(mgr, "_stores", {})) == 0)

        # 重新加载 kb=2，验证内容
        store2_reload = mgr.get_store(2, dim=dim)
        check("重新加载 kb=2 后 total() == 30", store2_reload.total() == 30)

        # get_status 测试
        st = mgr.get_status(2)
        check("get_status 包含 'total_vectors' key", "total_vectors" in st)
        check("get_status: consistent = True", st.get("consistent") is True)

        # 删除 kb=1
        mgr.delete(1)
        check("delete(1) 后 has_store(1) = False", mgr.has_store(1) is False)
        check("磁盘上剩下 2 个", len(mgr.list_stored_kbs()) == 2)

        # get_status 不存在的 kb
        st_miss = mgr.get_status(999)
        check("get_status(999) 返回 exists=False", st_miss.get("exists") is False)

        # bulk_add / bulk_search 测试
        items = []
        for i in range(25):
            v = _np.random.randn(dim).astype(float).tolist() if _has_numpy else [random.gauss(0, 1) for _ in range(dim)]
            items.append((v, {"chunk_id": 1000 + i}))
        idxs = mgr.bulk_add(2, items)
        check("bulk_add 25 个 -> 25 indices", len(idxs) == 25)
        check("kb=2 total 现在 = 55", mgr.get_store(2).total() == 55)

        # bulk_search: 3 个 query
        queries = []
        for _ in range(3):
            q = _np.random.randn(dim).astype(float).tolist() if _has_numpy else [random.gauss(0, 1) for _ in range(dim)]
            queries.append(q)
        bulk_res = mgr.bulk_search(2, queries, top_k=5)
        check("bulk_search 返回 3 组结果", len(bulk_res) == 3)
        check("每组 5 个结果", all(len(r) == 5 for r in bulk_res))
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:500])


# ============================================================
#  [6] 性能基准测试
# ============================================================
log("\n[6] 性能基准测试")
try:
    dim = 128
    n = 2000 if _has_numpy else 500

    # 构造 n 个向量
    if _has_numpy:
        vs = [_np.random.randn(dim).astype(float).tolist() for _ in range(n)]
    else:
        vs = [[random.gauss(0, 1) for _ in range(dim)] for _ in range(n)]

    metas = [{"chunk_id": i} for i in range(n)]

    # Pure Python 性能
    pp_store = PurePythonVectorStore(dim=dim)
    t0 = time.perf_counter()
    pp_store.add(vs, metas)
    t_add = time.perf_counter() - t0

    q = vs[0]
    t0 = time.perf_counter()
    for _ in range(10):
        pp_store.search(q, top_k=10)
    t_search = time.perf_counter() - t0

    log("  PurePythonVectorStore:")
    log("    add %d 个向量: %.2fs (%.1f us/vec)" % (n, t_add, 1e6 * t_add / n))
    log("    search × 10: %.2fs (%.1f ms/search)" % (t_search, t_search / 10 * 1000))

    # FAISS 性能 (如可用)
    if _has_faiss and _has_numpy:
        fa_store = FAISSVectorStore(dim=dim)
        t0 = time.perf_counter()
        fa_store.add(vs, metas)
        t_add_fa = time.perf_counter() - t0

        t0 = time.perf_counter()
        for _ in range(10):
            fa_store.search(q, top_k=10)
        t_search_fa = time.perf_counter() - t0

        log("  FAISSVectorStore (IndexFlatIP):")
        log("    add %d 个向量: %.2fs (%.1f us/vec)" % (n, t_add_fa, 1e6 * t_add_fa / n))
        log("    search × 10: %.2fs (%.1f ms/search)" % (t_search_fa, t_search_fa / 10 * 1000))
        check("FAISS add 不比 Pure Python 慢 10x 以上 (宽容)", t_add_fa < t_add * 10)
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:500])


# ============================================================
#  [7] EmbeddingService + VectorStore 端到端搜索验证
# ============================================================
log("\n[7] Embedding + VectorStore 端到端搜索")
try:
    with tempfile.TemporaryDirectory(dir=BACKEND_DIR) as tmpdir:
        embed = EmbeddingService.from_settings(settings)
        mgr = VectorStoreManager(base_dir=tmpdir, default_dim=embed.dim)
        store = mgr.get_store(42, dim=embed.dim)

        # 向量化一组中文文本
        texts = [
            "RAG 结合了检索与大语言模型生成，能够引用外部知识库。",
            "向量数据库用于存储和检索高维度的向量表示。",
            "检索增强生成 (Retrieval-Augmented Generation) 通过搜索获取上下文。",
            "FAISS 是 Facebook 开源的高效相似性搜索库。",
            "DeepSeek 提供大语言模型 API，可用于生成式问答。",
            "今天天气晴朗，气温约 23 摄氏度，适合散步。",
            "鸡肉炖土豆需要中火慢煮 40 分钟，加入洋葱和盐调味。",
            "Python 是一种解释型高级编程语言，广泛用于数据科学。",
            "机器学习是人工智能的一个分支，从数据中学习规律。",
            "推荐系统帮助用户从海量内容中发现感兴趣的信息。",
        ]
        vectors = embed.encode(texts)
        metas = [{"chunk_id": i, "content": t, "category": ("rag" if i < 5 else "misc")}
                 for i, t in enumerate(texts)]

        idxs = store.add(vectors, metas)
        check("向量化 10 段文本并加入索引", len(idxs) == 10)

        # RAG 相关 query
        q_rag = "RAG 如何结合向量检索和大语言模型？"
        q_vec = embed.encode_single(q_rag)
        res = store.search(q_vec, top_k=5)
        check("RAG query -> top-5 结果", len(res) == 5)

        # top-5 中 rag 类数量
        rag_hits = sum(
            1 for (vidx, s) in res
            if (store.get_metadata(vidx) or {}).get("category") == "rag"
        )
        check("RAG query 的 top-5 中 >= 3 个来自 RAG 相关文档", rag_hits >= 3)
        log("  RAG query top-5: %d 命中 RAG 相关, 分数: %s" %
            (rag_hits, ", ".join("%.3f" % s for _, s in res)))

        # 烹饪 query
        q_cook = "如何烹饪鸡肉土豆？"
        qc_vec = embed.encode_single(q_cook)
        res2 = store.search(qc_vec, top_k=3)
        check("烹饪 query -> top-3 结果", len(res2) == 3)
        log("  烹饪 query top-3:")
        for vidx, s in res2:
            meta = store.get_metadata(vidx) or {}
            log("    [%.3f] %s..." % (s, str(meta.get("content", ""))[:40]))

        # 持久化 + reload
        mgr.save(42)
        mgr.clear_memory()
        store_re = mgr.get_store(42, dim=embed.dim)
        check("reload 后 total() == 10", store_re.total() == 10)

        # reload 后再搜索
        res3 = store_re.search(q_vec, top_k=3)
        check("reload 后仍可搜索 (3 个结果)", len(res3) == 3)
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:500])


# ============================================================
#  [8] API 测试
# ============================================================
log("\n[8] API 集成测试 (FastAPI)")
try:
    client = TestClient(app)

    # 先准备一个有数据的索引：使用知识库 id=9999
    embed = EmbeddingService.from_settings(settings)
    tmp_mgr = VectorStoreManager(
        base_dir=settings.VECTOR_STORE_DIR,
        default_dim=embed.dim,
    )
    tmp_store = tmp_mgr.get_store(9999, dim=embed.dim)
    sample_texts = [
        "RAG 是检索增强生成技术。",
        "向量数据库存储高维向量做相似搜索。",
        "大语言模型擅长文本生成。",
        "Python 是解释型编程语言。",
        "FAISS 是 Facebook 开源的向量搜索库。",
    ]
    vs = embed.encode(sample_texts)
    tmp_store.add(vs, [{"chunk_id": i, "content": t} for i, t in enumerate(sample_texts)])
    tmp_mgr.save(9999)

    # 8a. 全局状态
    r = client.get("/v1/retrieval/status")
    check("GET /v1/retrieval/status -> 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        check("status.success = True", body.get("success") is True)
        data = body.get("data", {})
        check("stored_kbs 包含 id=9999", 9999 in data.get("stored_kbs", []))

    # 8b. 知识库索引状态
    r = client.get("/v1/retrieval/index/9999")
    check("GET /v1/retrieval/index/9999 -> 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        data = body.get("data", {})
        check("kb=9999 exists=True", data.get("exists") is True)
        check("kb=9999 total_vectors = 5", data.get("total_vectors") == 5)

    # 8c. 不存在的 kb 状态
    r = client.get("/v1/retrieval/index/88888")
    check("GET /v1/retrieval/index/88888 -> 200 (exists=False)", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        check("不存在的 kb: exists=False", body.get("data", {}).get("exists") is False)

    # 8d. 搜索
    r = client.post(
        "/v1/retrieval/search/9999",
        json={"query_text": "向量数据库如何工作？", "top_k": 3},
    )
    check("POST /v1/retrieval/search/9999 -> 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        data = body.get("data", {})
        check("search hits >= 1", data.get("hits", 0) >= 1)
        check("items 长度 <= 3", len(data.get("items", [])) <= 3)

    # 8e. flush
    r = client.post("/v1/retrieval/index/9999/flush")
    check("POST /v1/retrieval/index/9999/flush -> 200", r.status_code == 200)

    # 8f. delete 索引
    r = client.delete("/v1/retrieval/index/9999")
    check("DELETE /v1/retrieval/index/9999 -> 200", r.status_code == 200)
    if r.status_code == 200:
        body = r.json()
        check("delete 返回 success=True", body.get("data", {}).get("success") is True)

    # 8g. 搜索不存在 kb 的索引
    r = client.post(
        "/v1/retrieval/search/12345",
        json={"query_text": "hello", "top_k": 3},
    )
    check("POST /v1/retrieval/search/12345 (空索引) -> 200, hits=0",
          r.status_code == 200 and r.json().get("data", {}).get("hits", -1) == 0)

    # 8h. 错误请求: 缺 query_text
    r = client.post("/v1/retrieval/search/123", json={"top_k": 5})
    check("缺少 query_text -> 422 (schema 校验)", r.status_code == 422)

    # 8i. 错误请求: top_k 过大
    r = client.post("/v1/retrieval/search/123", json={"query_text": "x", "top_k": 10000})
    check("top_k > 100 -> 422", r.status_code == 422)

    # 8j. clear memory
    r = client.post("/v1/retrieval/index/clear-memory")
    check("POST /v1/retrieval/index/clear-memory -> 200", r.status_code == 200)

    # 清理: 确保测试留下的 kb_9999.vecstore 被删除
    try:
        path = os.path.join(settings.VECTOR_STORE_DIR, "kb_9999.vecstore")
        if os.path.isfile(path):
            os.remove(path)
        for s in (".faiss", "_index.faiss", ".state.json"):
            p = path + s
            if os.path.isfile(p):
                os.remove(p)
    except Exception:
        pass
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:500])


# ============================================================
#  [9] 与现有 DocumentService / API 兼容性
# ============================================================
log("\n[9] 与现有 DocumentService / documents API 兼容性")
try:
    from app.services.document_service import DocumentService
    from app.models.database import SessionLocal

    session = SessionLocal()
    try:
        ds = DocumentService(db=session)
        check("DocumentService 可构造", True)
        check("vector_manager 是 VectorStoreManager 实例",
              isinstance(ds.vector_manager, VectorStoreManager))

        # 测试 documents search API (不登录，公开知识库)
        client = TestClient(app)
        # 列公开知识库
        r = client.get("/v1/knowledge-bases?page=1&page_size=10")
        check("GET /v1/knowledge-bases -> 200 (公开)", r.status_code == 200)
    finally:
        session.close()
except Exception as e:
    log("  [EXCEPTION] %s" % str(e))
    import traceback
    log("  %s" % traceback.format_exc()[:500])


# ============================================================
# 总结
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
            log("  [FAIL] " + desc)

log("\nDay 5 交付内容小结:")
log("  1) FAISSVectorStore - IndexFlatIP 精确搜索，增量 add")
log("  2) IVFVectorStore - 倒排索引近似搜索（适合 >1w chunk）")
log("  3) PurePythonVectorStore - 纯 numpy/Python 回退实现")
log("  4) VectorStoreManager - 多 KB 管理，持久化，批量，统计")
log("  5) 一致性检查 (check_consistency) - 向量/元数据完整性自检")
log("  6) 线程安全 (RLock)")
log("  7) API 路由:")
log("       GET  /v1/retrieval/status                 (全局状态)")
log("       GET  /v1/retrieval/index/{kb_id}          (KB 索引状态)")
log("       POST /v1/retrieval/index/{kb_id}/flush    (落盘)")
log("       DELETE /v1/retrieval/index/{kb_id}        (删除索引)")
log("       POST /v1/retrieval/index/clear-memory     (释放内存)")
log("       POST /v1/retrieval/search/{kb_id}         (向量搜索)")
log("")
log("下一里程碑: Day 6 - LLM Chat 生成集成 (DeepSeek API) + RAG 上下文拼接")