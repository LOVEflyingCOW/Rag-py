"""Vector Store - 向量存储与检索

实现:
1. FAISSVectorStore - 使用 Facebook FAISS 索引做向量相似度搜索
2. FallbackVectorStore - 不依赖 FAISS 的纯 Python 实现（基于 numpy/手工计算），
   确保在 FAISS 安装失败时也能工作

核心接口:
    store.add(vectors: List[List[float]], metadata: List[dict]) -> List[int]
    store.search(query_vector: List[float], top_k: int = 5) -> List[(index, score)]
    store.save(path: str)
    store.load(path: str)
"""
from __future__ import annotations

import json
import math
import os
import pickle
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

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


class BaseVectorStore:
    """向量存储基类"""

    def __init__(self, dim: int):
        self.dim = dim
        self._next_index = 0
        # 原始向量（用于保存/调试）
        self._vectors: List[List[float]] = []
        # 元数据: {vector_index: {"chunk_id": int, "document_id": int, ...}}
        self._metadata: Dict[int, Dict[str, Any]] = {}

    def add(self, vectors: List[List[float]], metadata: Optional[List[Dict[str, Any]]] = None) -> List[int]:
        """添加向量，返回每个向量的 index 列表"""
        if not vectors:
            return []

        indices = []
        for i, vec in enumerate(vectors):
            if len(vec) != self.dim:
                raise ValueError(
                    "向量维度不匹配: 期望 %d，实际 %d" % (self.dim, len(vec))
                )

            idx = self._next_index
            self._vectors.append(list(vec))
            if metadata and i < len(metadata):
                self._metadata[idx] = metadata[i]
            else:
                self._metadata[idx] = {}

            indices.append(idx)
            self._next_index += 1

        self._on_vectors_added(indices)
        return indices

    def _on_vectors_added(self, indices: List[int]) -> None:
        """子类可以在此处更新索引结构"""
        pass

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Tuple[int, float]]:
        """搜索最相似的向量，返回 [(vector_index, score), ...]

        score 为余弦相似度，范围 [0, 1]，越大越相似。
        """
        raise NotImplementedError

    def get_metadata(self, vector_index: int) -> Optional[Dict[str, Any]]:
        return self._metadata.get(vector_index)

    def total(self) -> int:
        return len(self._vectors)

    # ============================================================
    # 保存 / 加载
    # ============================================================
    def save(self, path: str) -> None:
        Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
        data = {
            "dim": self.dim,
            "next_index": self._next_index,
            "vectors": self._vectors,
            "metadata": self._metadata,
        }
        with open(path, "wb") as f:
            pickle.dump(data, f)

    def load(self, path: str) -> None:
        if not os.path.isfile(path):
            raise FileNotFoundError("向量存储文件不存在: %s" % path)
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.dim = data.get("dim", self.dim)
        self._next_index = data.get("next_index", 0)
        self._vectors = data.get("vectors", [])
        self._metadata = data.get("metadata", {})
        self._on_vectors_added(list(range(len(self._vectors))))


class FAISSVectorStore(BaseVectorStore):
    """FAISS 向量存储 - 使用 IndexFlatL2 + 归一化余弦相似度"""

    def __init__(self, dim: int):
        super().__init__(dim)
        if not _HAS_FAISS:
            raise RuntimeError("faiss-cpu 未安装。请运行: pip install faiss-cpu")
        if not _HAS_NUMPY:
            raise RuntimeError("numpy 未安装。请运行: pip install numpy")

        self._index = faiss.IndexFlatL2(dim)
        # 因为我们使用 L2 距离 + 预归一化向量，所以 L2 距离可以转换为余弦相似度

    def _on_vectors_added(self, indices: List[int]) -> None:
        # 重建 FAISS 索引（简单实现）
        if self._vectors:
            vec_np = np.array(self._vectors, dtype="float32")
            # 归一化
            norms = np.linalg.norm(vec_np, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            vec_np = vec_np / norms

            self._index = faiss.IndexFlatL2(self.dim)
            self._index.add(vec_np)

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Tuple[int, float]]:
        if not self._vectors:
            return []

        top_k = min(top_k, len(self._vectors))
        q = np.array([query_vector], dtype="float32")
        # 归一化查询向量
        norm = np.linalg.norm(q)
        if norm > 0:
            q = q / norm

        distances, indices = self._index.search(q, top_k)

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0 or idx >= len(self._vectors):
                continue
            # L2 距离 d, 归一化后向量的余弦相似度 = 1 - d^2 / 2
            cos_sim = 1.0 - float(dist) * float(dist) / 2.0
            # 映射到 [0, 1] 作为 score
            score = max(0.0, min(1.0, (cos_sim + 1.0) / 2.0))
            results.append((int(idx), score))

        return results

    def save(self, path: str) -> None:
        # 保存 FAISS 索引 + 元数据
        Path(os.path.dirname(path)).mkdir(parents=True, exist_ok=True)
        faiss_path = path + ".faiss"
        faiss.write_index(self._index, faiss_path)
        data = {
            "dim": self.dim,
            "next_index": self._next_index,
            "vectors": self._vectors,
            "metadata": self._metadata,
        }
        with open(path, "wb") as f:
            pickle.dump(data, f)

    def load(self, path: str) -> None:
        if not os.path.isfile(path):
            raise FileNotFoundError("向量存储文件不存在: %s" % path)
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.dim = data.get("dim", self.dim)
        self._next_index = data.get("next_index", 0)
        self._vectors = data.get("vectors", [])
        self._metadata = data.get("metadata", {})

        faiss_path = path + ".faiss"
        if os.path.isfile(faiss_path):
            self._index = faiss.read_index(faiss_path)
        else:
            self._on_vectors_added(list(range(len(self._vectors))))


class PurePythonVectorStore(BaseVectorStore):
    """纯 Python 实现（FAISS 不可用时的回退方案）

    基于 numpy 或手工计算余弦相似度。
    适合小规模知识库 (< 10,000 个 chunk)。
    """

    def __init__(self, dim: int):
        super().__init__(dim)
        self._normalized_vectors: List[List[float]] = []

    def _norm(self, vec: List[float]) -> float:
        if _HAS_NUMPY:
            import numpy as np
            return float(np.linalg.norm(vec))
        s = 0.0
        for v in vec:
            s += v * v
        return math.sqrt(s)

    def _on_vectors_added(self, indices: List[int]) -> None:
        # 预计算归一化向量
        self._normalized_vectors = []
        for vec in self._vectors:
            norm = self._norm(vec)
            if norm > 0:
                self._normalized_vectors.append([v / norm for v in vec])
            else:
                self._normalized_vectors.append([0.0] * self.dim)

    def search(self, query_vector: List[float], top_k: int = 5) -> List[Tuple[int, float]]:
        if not self._vectors:
            return []

        top_k = min(top_k, len(self._vectors))

        # 归一化查询向量
        q_norm = self._norm(query_vector)
        if q_norm > 0:
            q = [v / q_norm for v in query_vector]
        else:
            q = [0.0] * self.dim

        # 计算所有余弦相似度
        if _HAS_NUMPY:
            import numpy as np
            stored_np = np.array(self._normalized_vectors, dtype="float32")
            q_np = np.array(q, dtype="float32")
            scores = stored_np @ q_np  # 点积 = 余弦相似度（因为已归一化）
            scored_pairs = [(i, float(s)) for i, s in enumerate(scores)]
        else:
            scored_pairs = []
            for i, nv in enumerate(self._normalized_vectors):
                cos_sim = 0.0
                for j in range(self.dim):
                    cos_sim += nv[j] * q[j]
                scored_pairs.append((i, cos_sim))

        # 排序取 top_k
        scored_pairs.sort(key=lambda x: x[1], reverse=True)
        top = scored_pairs[:top_k]

        # 映射余弦相似度 [-1, 1] 到 [0, 1]
        results = [(idx, max(0.0, min(1.0, (s + 1.0) / 2.0))) for idx, s in top]
        return results


class VectorStoreManager:
    """向量存储管理器 - 按知识库管理多个 VectorStore"""

    def __init__(self, base_dir: str, default_dim: int = 64, prefer_faiss: bool = True):
        self.base_dir = base_dir
        self.default_dim = default_dim
        self.prefer_faiss = prefer_faiss and _HAS_FAISS
        self._stores: Dict[int, BaseVectorStore] = {}
        Path(base_dir).mkdir(parents=True, exist_ok=True)

    def _get_store_path(self, knowledge_base_id: int) -> str:
        return os.path.join(self.base_dir, "kb_%d.vecstore" % knowledge_base_id)

    def get_store(self, knowledge_base_id: int, dim: Optional[int] = None) -> BaseVectorStore:
        """获取知识库的向量存储（若不存在则新建/加载）"""
        if knowledge_base_id in self._stores:
            return self._stores[knowledge_base_id]

        path = self._get_store_path(knowledge_base_id)
        store_dim = dim or self.default_dim

        if os.path.isfile(path):
            # 优先尝试 FAISS
            if self.prefer_faiss and _HAS_FAISS:
                store: BaseVectorStore = FAISSVectorStore(store_dim)
            else:
                store = PurePythonVectorStore(store_dim)
            store.load(path)
        else:
            if self.prefer_faiss and _HAS_FAISS:
                store = FAISSVectorStore(store_dim)
            else:
                store = PurePythonVectorStore(store_dim)

        self._stores[knowledge_base_id] = store
        return store

    def save(self, knowledge_base_id: int) -> None:
        """保存知识库的向量存储到磁盘"""
        if knowledge_base_id in self._stores:
            path = self._get_store_path(knowledge_base_id)
            self._stores[knowledge_base_id].save(path)

    def delete(self, knowledge_base_id: int) -> None:
        """删除知识库的向量存储"""
        if knowledge_base_id in self._stores:
            del self._stores[knowledge_base_id]
        path = self._get_store_path(knowledge_base_id)
        if os.path.isfile(path):
            os.remove(path)
        faiss_path = path + ".faiss"
        if os.path.isfile(faiss_path):
            os.remove(faiss_path)

    def has_store(self, knowledge_base_id: int) -> bool:
        return knowledge_base_id in self._stores or os.path.isfile(
            self._get_store_path(knowledge_base_id)
        )


__all__ = [
    "BaseVectorStore",
    "FAISSVectorStore",
    "PurePythonVectorStore",
    "VectorStoreManager",
    "_HAS_FAISS",
]