"""Vector Store - 向量存储与检索（Day 5 深度增强版）

核心改进:
1. FAISSVectorStore - 使用 IndexFlatIP（内积），增量添加不重建索引
2. IVFVectorStore - 大规模知识库用倒排索引（可选）
3. PurePythonVectorStore - 纯 numpy 回退
4. VectorStoreManager - 增强的索引管理（状态查询/重建/统计/并发安全）
5. 一致性检查: dim 校验、向量有限性检查、序列化完整性

核心接口:
    store.add(vectors, metadata) -> List[int]
    store.search(query_vector, top_k=5) -> List[(index, score)]
    store.stats() -> dict
    store.save(path) / store.load(path)
"""
from __future__ import annotations

import json
import math
import os
import pickle
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------- 依赖检测 ----------
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import faiss
    _HAS_FAISS = True
except ImportError:
    _HAS_FAISS = False


# ---------- 小工具 ----------
def _is_valid_vector(vec: List[float], dim: int) -> bool:
    """检查向量是否合法"""
    if len(vec) != dim:
        return False
    for v in vec:
        if isinstance(v, (int, float)) and math.isfinite(v):
            continue
        return False
    return True


def _normalize_vectors(vectors: List[List[float]]) -> List[List[float]]:
    """批量归一化（返回副本）"""
    if not vectors:
        return []
    dim = len(vectors[0])
    if _HAS_NUMPY:
        import numpy as np2
        arr = np2.array(vectors, dtype="float32")
        norms = np2.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms
        return arr.tolist()
    # 纯 Python
    result = []
    for vec in vectors:
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            result.append([v / norm for v in vec])
        else:
            result.append([0.0] * dim)
    return result


# ============================================================
#  BaseVectorStore
# ============================================================
class BaseVectorStore:
    """向量存储基类"""

    def __init__(self, dim: int):
        self.dim = int(dim)
        if self.dim <= 0:
            raise ValueError("dim 必须为正整数，收到: %s" % dim)
        self._next_index = 0
        self._vectors: List[List[float]] = []
        self._metadata: Dict[int, Dict[str, Any]] = {}
        self._lock = threading.RLock()

    # ---------- 添加 ----------
    def add(
        self,
        vectors: List[List[float]],
        metadata: Optional[List[Dict[str, Any]]] = None,
    ) -> List[int]:
        """添加向量，返回分配的 index 列表"""
        if not vectors:
            return []

        with self._lock:
            indices = []
            for i, vec in enumerate(vectors):
                if not _is_valid_vector(vec, self.dim):
                    raise ValueError(
                        "第 %d 个向量维度非法: 期望 %d，实际 %d"
                        % (i, self.dim, len(vec) if isinstance(vec, list) else -1)
                    )
                idx = self._next_index
                self._vectors.append(list(vec))
                self._metadata[idx] = metadata[i] if metadata and i < len(metadata) else {}
                indices.append(idx)
                self._next_index += 1

            if indices:
                self._on_vectors_added(indices)

            return indices

    def _on_vectors_added(self, indices: List[int]) -> None:
        """子类更新内部索引结构"""
        pass

    # ---------- 搜索 ----------
    def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """搜索最相似的向量，返回 [{index, score, metadata}]
        score ∈ [0, 1]，越大越相似
        """
        raise NotImplementedError

    # ---------- 元数据/查询 ----------
    def get_metadata(self, vector_index: int) -> Optional[Dict[str, Any]]:
        return self._metadata.get(vector_index)

    def get_vector(self, vector_index: int) -> Optional[List[float]]:
        if 0 <= vector_index < len(self._vectors):
            return list(self._vectors[vector_index])
        return None

    def total(self) -> int:
        return len(self._vectors)

    def stats(self) -> Dict[str, Any]:
        """返回统计信息"""
        return {
            "dim": self.dim,
            "total_vectors": len(self._vectors),
            "next_index": self._next_index,
            "metadata_count": len(self._metadata),
        }

    def check_consistency(self) -> Tuple[bool, List[str]]:
        """一致性检查"""
        issues: List[str] = []
        if len(self._vectors) != self._next_index:
            issues.append(
                "向量数量 (%d) 与 next_index (%d) 不一致"
                % (len(self._vectors), self._next_index)
            )
        for i, v in enumerate(self._vectors):
            if len(v) != self.dim:
                issues.append("vec[%d] 维度 = %d (期望 %d)" % (i, len(v), self.dim))
                break
        for i in range(len(self._vectors)):
            if i not in self._metadata:
                issues.append("vec[%d] 缺少 metadata" % i)
                break
        return (len(issues) == 0, issues)

    # ---------- 保存/加载 ----------
    def _save_meta(self, path: str) -> None:
        """保存基础元数据（子类可调用）"""
        Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
        data = {
            "dim": self.dim,
            "next_index": self._next_index,
            "vectors": self._vectors,
            "metadata": self._metadata,
        }
        with open(path, "wb") as f:
            pickle.dump(data, f)

    def _load_meta(self, path: str) -> None:
        """加载基础元数据"""
        if not os.path.isfile(path):
            raise FileNotFoundError("向量存储文件不存在: %s" % path)
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.dim = int(data.get("dim", self.dim))
        self._next_index = int(data.get("next_index", 0))
        self._vectors = data.get("vectors", [])
        self._metadata = data.get("metadata", {})

    def save(self, path: str) -> None:
        with self._lock:
            self._save_meta(path)

    def load(self, path: str) -> None:
        with self._lock:
            self._load_meta(path)
            if self._vectors:
                self._on_vectors_added(list(range(len(self._vectors))))


# ============================================================
#  FAISSVectorStore (IndexFlatIP - 增量添加)
# ============================================================
class FAISSVectorStore(BaseVectorStore):
    """FAISS 向量存储 - 使用 IndexFlatIP（内积）做精确余弦相似度搜索

    核心改进:
    - IndexFlatIP 比 IndexFlatL2 更适合归一化向量的余弦相似度场景
    - 增量 add: 每次只 add 新向量，不重建整个索引
    - 规范化 score ∈ [0, 1]
    """

    def __init__(self, dim: int):
        if not _HAS_FAISS:
            raise RuntimeError("faiss-cpu 未安装。请运行: pip install faiss-cpu")
        if not _HAS_NUMPY:
            raise RuntimeError("numpy 未安装。请运行: pip install numpy")
        super().__init__(dim)
        self._index = faiss.IndexFlatIP(dim)

    def _on_vectors_added(self, indices: List[int]) -> None:
        """仅将新向量加入 FAISS 索引（增量）"""
        if not indices:
            return
        # 注意: 因为 _vectors 是按 index 顺序追加的，新向量就是 indices 那段
        # 但为鲁棒性，我们直接把 indices 对应的向量取出
        new_vecs = [self._vectors[i] for i in indices]
        arr = np.array(new_vecs, dtype="float32")
        # 归一化
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms
        self._index.add(arr)

    def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        if not self._vectors:
            return []
        if len(query_vector) != self.dim:
            raise ValueError(
                "查询向量维度不匹配: 期望 %d，实际 %d" % (self.dim, len(query_vector))
            )

        top_k = max(1, min(int(top_k), len(self._vectors)))

        q = np.array([query_vector], dtype="float32")
        norm = np.linalg.norm(q)
        if norm > 0:
            q = q / norm

        # IndexFlatIP 返回内积，对归一化向量来说 = 余弦相似度 ∈ [-1, 1]
        scores_arr, indices_arr = self._index.search(q, top_k)

        results: List[Dict[str, Any]] = []
        for s, idx in zip(scores_arr[0], indices_arr[0]):
            if idx < 0 or idx >= len(self._vectors):
                continue
            cos_sim = float(s)
            score = max(0.0, min(1.0, (cos_sim + 1.0) / 2.0))
            results.append({
                "index": int(idx),
                "score": score,
                "metadata": self._metadata.get(int(idx), {}),
            })
        return results

    def stats(self) -> Dict[str, Any]:
        info = super().stats()
        info["backend"] = "faiss-IndexFlatIP"
        info["is_trained"] = getattr(self._index, "is_trained", True)
        info["ntotal"] = getattr(self._index, "ntotal", 0)
        return info

    # ---------- 保存/加载（FAISS 索引 + 元数据） ----------
    def save(self, path: str) -> None:
        with self._lock:
            Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
            faiss_path = path + ".faiss"
            try:
                faiss.write_index(self._index, faiss_path)
            except Exception:
                # 某些 FAISS 版本对某些索引类型需要不同方法
                faiss_path_internal = path + "_index.faiss"
                faiss.write_index(self._index, faiss_path_internal)
            self._save_meta(path)

    def load(self, path: str) -> None:
        with self._lock:
            self._load_meta(path)
            faiss_path = path + ".faiss"
            if os.path.isfile(faiss_path):
                self._index = faiss.read_index(faiss_path)
            else:
                # 重建
                self._index = faiss.IndexFlatIP(self.dim)
                if self._vectors:
                    arr = np.array(self._vectors, dtype="float32")
                    norms = np.linalg.norm(arr, axis=1, keepdims=True)
                    norms[norms == 0] = 1.0
                    arr = arr / norms
                    self._index.add(arr)


# ============================================================
#  IVFVectorStore (倒排索引 - 大规模知识库)
# ============================================================
class IVFVectorStore(BaseVectorStore):
    """基于 FAISS IndexIVFFlat 的近似搜索

    适合大规模知识库 (> 10,000 个 chunk)。
    搜索复杂度从 O(N) 降低到 O(log N) 级别。

    注意: 需要先调用 train() 或在 add 时自动训练。
    """

    def __init__(self, dim: int, nlist: int = 100, nprobe: int = 10):
        if not _HAS_FAISS:
            raise RuntimeError("faiss-cpu 未安装")
        if not _HAS_NUMPY:
            raise RuntimeError("numpy 未安装")
        super().__init__(dim)
        self.nlist = max(1, int(nlist))
        self.nprobe = max(1, int(nprobe))
        self._index: Optional[Any] = None
        self._trained = False
        # 用一个子索引做训练，正式索引在训练后重建
        self._index = faiss.IndexFlatIP(dim)
        self._train_buffer: List[List[float]] = []

    def _train(self, sample_vectors: List[List[float]]) -> None:
        """训练倒排索引。样本向量数必须 >= nlist"""
        if not sample_vectors:
            return
        n = len(sample_vectors)
        effective_nlist = min(self.nlist, max(1, n // 5))  # 每个簇至少 5 个
        if effective_nlist < 1:
            effective_nlist = 1

        arr = np.array(sample_vectors, dtype="float32")
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        arr = arr / norms

        quantizer = faiss.IndexFlatIP(self.dim)
        index = faiss.IndexIVFFlat(quantizer, self.dim, effective_nlist, faiss.METRIC_INNER_PRODUCT)
        index.nprobe = self.nprobe
        index.train(arr)

        # 将已有向量加入
        index.add(arr)
        self._index = index
        self.nlist = effective_nlist
        self._trained = True

    def _on_vectors_added(self, indices: List[int]) -> None:
        if not indices:
            return
        new_vecs = [self._vectors[i] for i in indices]

        if not self._trained:
            # 未训练 -> 累计到 buffer，直到足够样本
            self._train_buffer.extend(new_vecs)
            if len(self._train_buffer) >= max(self.nlist * 5, 50):
                self._train(self._train_buffer)
                self._train_buffer = []
            else:
                # 训练前先用 Flat 存储临时
                # 这里简化: 不立刻加入索引，等训练后批量加入
                return
        else:
            # 已训练 -> 直接 add
            arr = np.array(new_vecs, dtype="float32")
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms
            self._index.add(arr)

    def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        if not self._vectors:
            return []
        if len(query_vector) != self.dim:
            raise ValueError("查询向量维度不匹配")

        top_k = max(1, min(int(top_k), len(self._vectors)))

        q = np.array([query_vector], dtype="float32")
        norm = np.linalg.norm(q)
        if norm > 0:
            q = q / norm

        # 未训练时回退到线性扫描
        if not self._trained:
            arr = np.array(self._vectors, dtype="float32")
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms
            scores = (arr @ q[0])
            top_idx = np.argsort(-scores)[:top_k]
            return [
                {
                    "index": int(i),
                    "score": max(0.0, min(1.0, (float(scores[i]) + 1.0) / 2.0)),
                    "metadata": self._metadata.get(int(i), {}),
                }
                for i in top_idx
            ]

        scores_arr, indices_arr = self._index.search(q, top_k)
        results: List[Dict[str, Any]] = []
        for s, idx in zip(scores_arr[0], indices_arr[0]):
            if idx < 0 or idx >= len(self._vectors):
                continue
            cos_sim = float(s)
            score = max(0.0, min(1.0, (cos_sim + 1.0) / 2.0))
            results.append({
                "index": int(idx),
                "score": score,
                "metadata": self._metadata.get(int(idx), {}),
            })
        return results

    def stats(self) -> Dict[str, Any]:
        info = super().stats()
        info["backend"] = "faiss-IndexIVFFlat"
        info["nlist"] = self.nlist
        info["nprobe"] = self.nprobe
        info["is_trained"] = self._trained
        info["ntotal"] = getattr(self._index, "ntotal", 0) if self._index else 0
        return info

    # ---------- 保存/加载 ----------
    def save(self, path: str) -> None:
        with self._lock:
            Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
            faiss_path = path + ".faiss"
            try:
                faiss.write_index(self._index, faiss_path)
            except Exception:
                pass
            self._save_meta(path)
            # 额外保存训练状态
            state = {
                "trained": self._trained,
                "nlist": self.nlist,
                "nprobe": self.nprobe,
                "train_buffer": self._train_buffer,
            }
            with open(path + ".state.json", "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in state.items() if k != "train_buffer"}, f)

    def load(self, path: str) -> None:
        with self._lock:
            self._load_meta(path)
            faiss_path = path + ".faiss"
            state_path = path + ".state.json"
            if os.path.isfile(state_path):
                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        st = json.load(f)
                    self.nlist = int(st.get("nlist", self.nlist))
                    self.nprobe = int(st.get("nprobe", self.nprobe))
                    self._trained = bool(st.get("trained", False))
                except Exception:
                    self._trained = False
            if os.path.isfile(faiss_path):
                try:
                    self._index = faiss.read_index(faiss_path)
                    if hasattr(self._index, "nprobe"):
                        self._index.nprobe = self.nprobe
                except Exception:
                    self._index = faiss.IndexFlatIP(self.dim)
                    self._trained = False
                    if self._vectors:
                        arr = np.array(self._vectors, dtype="float32")
                        norms = np.linalg.norm(arr, axis=1, keepdims=True)
                        norms[norms == 0] = 1.0
                        arr = arr / norms
                        self._index.add(arr)
            else:
                self._index = faiss.IndexFlatIP(self.dim)
                self._trained = False


# ============================================================
#  PurePythonVectorStore (numpy 回退)
# ============================================================
class PurePythonVectorStore(BaseVectorStore):
    """纯 Python + numpy 回退实现

    小规模知识库 (< 5,000 chunk) 下性能可用。
    精确搜索，无近似。
    """

    def __init__(self, dim: int):
        super().__init__(dim)
        self._normalized: List[List[float]] = []

    def _on_vectors_added(self, indices: List[int]) -> None:
        # 仅对新向量做归一化（增量）
        for i in indices:
            if _HAS_NUMPY:
                import numpy as np2
                v = np2.array(self._vectors[i], dtype="float32")
                n = float(np2.linalg.norm(v))
                if n > 0:
                    v = v / n
                else:
                    v = np2.zeros(self.dim, dtype="float32")
                self._normalized.append(v.tolist())
            else:
                vec = self._vectors[i]
                norm = math.sqrt(sum(v * v for v in vec))
                if norm > 0:
                    self._normalized.append([v / norm for v in vec])
                else:
                    self._normalized.append([0.0] * self.dim)

    def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """搜索最相似的向量，返回 [{index, score, metadata}]
        score ∈ [0, 1]，越大越相似
        """
        if not self._vectors:
            return []
        if len(query_vector) != self.dim:
            raise ValueError("查询向量维度不匹配")
        top_k = max(1, min(int(top_k), len(self._vectors)))

        # 归一化查询
        if _HAS_NUMPY:
            import numpy as np2
            q = np2.array(query_vector, dtype="float32")
            n = float(np2.linalg.norm(q))
            if n > 0:
                q = q / n
            stored = np2.array(self._normalized, dtype="float32")
            scores = stored @ q
            top_idx = np2.argsort(-scores)[:top_k]
            return [
                {
                    "index": int(i),
                    "score": max(0.0, min(1.0, (float(scores[i]) + 1.0) / 2.0)),
                    "metadata": self._metadata.get(int(i), {}),
                }
                for i in top_idx
            ]

        # 纯 Python
        q_norm = math.sqrt(sum(v * v for v in query_vector))
        if q_norm > 0:
            q = [v / q_norm for v in query_vector]
        else:
            q = [0.0] * self.dim
        scored = []
        for i, nv in enumerate(self._normalized):
            s = 0.0
            for j in range(self.dim):
                s += nv[j] * q[j]
            scored.append((i, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            {
                "index": int(idx),
                "score": max(0.0, min(1.0, (s + 1.0) / 2.0)),
                "metadata": self._metadata.get(int(idx), {}),
            }
            for idx, s in scored[:top_k]
        ]

    def stats(self) -> Dict[str, Any]:
        info = super().stats()
        info["backend"] = "pure-python"
        info["numpy"] = _HAS_NUMPY
        return info


# ============================================================
#  VectorStoreManager - 增强版
# ============================================================
class VectorStoreManager:
    """向量存储管理器 - 按知识库管理多个 VectorStore

    新增特性:
    - 自动选择后端（FAISS / PurePython）
    - 支持 'ivf' 模式用于大规模知识库
    - get_status / rebuild / list_all 等管理功能
    - 线程安全
    """

    BACKEND_FLAT = "flat"
    BACKEND_IVF = "ivf"
    BACKEND_PURE = "pure"

    def __init__(
        self,
        base_dir: str,
        default_dim: int = 384,
        prefer_faiss: bool = True,
        large_kb_threshold: int = 10000,
    ):
        self.base_dir = base_dir
        self.default_dim = int(default_dim)
        self.prefer_faiss = bool(prefer_faiss)
        self.large_kb_threshold = int(large_kb_threshold)
        self._stores: Dict[int, BaseVectorStore] = {}
        self._lock = threading.RLock()
        Path(base_dir).mkdir(parents=True, exist_ok=True)

    # ---------- 路径 ----------
    def _get_store_path(self, knowledge_base_id: int) -> str:
        return os.path.join(self.base_dir, "kb_%d.vecstore" % knowledge_base_id)

    def _pick_backend(self, knowledge_base_id: int, hint: Optional[str] = None) -> str:
        """选择后端"""
        if hint and hint in (self.BACKEND_FLAT, self.BACKEND_IVF, self.BACKEND_PURE):
            return hint
        if not self.prefer_faiss or not _HAS_FAISS:
            return self.BACKEND_PURE
        return self.BACKEND_FLAT

    def _create_store(self, backend: str, dim: int) -> BaseVectorStore:
        if backend == self.BACKEND_IVF and _HAS_FAISS:
            return IVFVectorStore(dim, nlist=min(256, max(16, dim // 4)), nprobe=20)
        if backend == self.BACKEND_FLAT and _HAS_FAISS:
            return FAISSVectorStore(dim)
        return PurePythonVectorStore(dim)

    # ---------- CRUD ----------
    def get_store(
        self,
        knowledge_base_id: int,
        dim: Optional[int] = None,
        backend: Optional[str] = None,
    ) -> BaseVectorStore:
        """获取/加载知识库的向量存储"""
        with self._lock:
            if knowledge_base_id in self._stores:
                return self._stores[knowledge_base_id]

            path = self._get_store_path(knowledge_base_id)
            store_dim = int(dim) if dim else self.default_dim
            chosen = self._pick_backend(knowledge_base_id, backend)

            store = self._create_store(chosen, store_dim)
            if os.path.isfile(path):
                try:
                    store.load(path)
                except Exception:
                    # 加载失败，用新 store
                    store = self._create_store(chosen, store_dim)
            self._stores[knowledge_base_id] = store
            return store

    def save(self, knowledge_base_id: int) -> bool:
        with self._lock:
            if knowledge_base_id not in self._stores:
                return False
            try:
                self._stores[knowledge_base_id].save(self._get_store_path(knowledge_base_id))
                return True
            except Exception:
                return False

    def save_all(self) -> None:
        """保存所有已加载 store"""
        with self._lock:
            for kb_id in list(self._stores.keys()):
                try:
                    self._stores[kb_id].save(self._get_store_path(kb_id))
                except Exception:
                    pass

    def delete(self, knowledge_base_id: int) -> bool:
        with self._lock:
            if knowledge_base_id in self._stores:
                del self._stores[knowledge_base_id]
            path = self._get_store_path(knowledge_base_id)
            ok = False
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    ok = True
                except Exception:
                    pass
            for suffix in (".faiss", "_index.faiss", ".state.json"):
                p = path + suffix
                if os.path.isfile(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            return ok

    def has_store(self, knowledge_base_id: int) -> bool:
        return knowledge_base_id in self._stores or os.path.isfile(
            self._get_store_path(knowledge_base_id)
        )

    def clear_memory(self, knowledge_base_id: Optional[int] = None) -> None:
        """从内存中释放（不删磁盘）"""
        with self._lock:
            if knowledge_base_id is None:
                self._stores.clear()
            elif knowledge_base_id in self._stores:
                del self._stores[knowledge_base_id]

    def list_stored_kbs(self) -> List[int]:
        """磁盘上存在的所有知识库 id"""
        ids = []
        try:
            for fn in os.listdir(self.base_dir):
                if fn.startswith("kb_") and fn.endswith(".vecstore"):
                    try:
                        ids.append(int(fn[3:-9]))
                    except Exception:
                        pass
        except Exception:
            pass
        return sorted(ids)

    def get_status(self, knowledge_base_id: int) -> Dict[str, Any]:
        """查询知识库索引状态"""
        if knowledge_base_id not in self._stores and not os.path.isfile(
            self._get_store_path(knowledge_base_id)
        ):
            return {
                "knowledge_base_id": knowledge_base_id,
                "exists": False,
                "loaded": False,
                "path": self._get_store_path(knowledge_base_id),
            }
        store = self.get_store(knowledge_base_id)
        consistent, issues = store.check_consistency()
        return {
            "knowledge_base_id": knowledge_base_id,
            "exists": True,
            "loaded": True,
            "path": self._get_store_path(knowledge_base_id),
            "consistent": consistent,
            "issues": issues,
            **store.stats(),
        }

    # ---------- 高级: 批量 ----------
    def bulk_add(
        self,
        knowledge_base_id: int,
        items: List[Tuple[List[float], Dict[str, Any]]],
    ) -> List[int]:
        """批量添加: items = [(vector, metadata), ...]"""
        store = self.get_store(knowledge_base_id)
        vectors = [it[0] for it in items]
        metas = [it[1] for it in items]
        return store.add(vectors, metas)

    def bulk_search(
        self,
        knowledge_base_id: int,
        queries: List[List[float]],
        top_k: int = 5,
    ) -> List[List[Tuple[int, float]]]:
        """批量搜索"""
        store = self.get_store(knowledge_base_id)
        return [store.search(q, top_k) for q in queries]


__all__ = [
    "BaseVectorStore",
    "FAISSVectorStore",
    "IVFVectorStore",
    "PurePythonVectorStore",
    "VectorStoreManager",
    "_HAS_FAISS",
    "_HAS_NUMPY",
]