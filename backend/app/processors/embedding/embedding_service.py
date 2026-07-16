"""Embedding Provider - 文本向量化服务

设计为可插拔的接口，支持多种实现:
1. MockProvider        - 本地伪随机向量，仅用于开发测试
2. LocalNumpyProvider  - 基于 numpy + TF-IDF 风格的本地轻量实现
3. RemoteAPIProvider   - 调用远程 HTTP Embedding API (OpenAI/DeepSeek/SiliconFlow 兼容)
4. CachingProvider     - 装饰器：为任意 Provider 增加 LRU 缓存

核心接口统一:
    provider.encode(texts: List[str]) -> List[List[float]]
    provider.encode_single(text: str) -> List[float]
    provider.dim -> int
    provider.name -> str

质量保证:
    - 向量维度必须一致
    - 向量必须为有限浮点数 (无 NaN / Inf)
    - 可选：L2 归一化到单位长度
    - 可选：批量切分 + 失败重试
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import numpy as np  # type: ignore
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

from app.core.exceptions import InternalError


# ============================================================
#  公共工具函数
# ============================================================

def normalize_vec(v: Sequence[float]) -> List[float]:
    """L2 归一化（纯 Python 实现，不依赖 numpy）"""
    if not v:
        return []
    sq_sum = sum(x * x for x in v)
    if sq_sum <= 0.0:
        # 零向量：返回一个简单的单位向量（第 0 维 = 1）
        result = [0.0] * len(v)
        result[0] = 1.0
        return result
    norm = math.sqrt(sq_sum)
    return [x / norm for x in v]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """计算两个向量的余弦相似度（纯 Python）"""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, dot / (norm_a * norm_b)))


def validate_vectors(vectors: List[List[float]], expected_dim: int) -> None:
    """验证向量质量，不通过则抛异常"""
    if len(vectors) == 0:
        return
    for idx, v in enumerate(vectors):
        if len(v) != expected_dim:
            raise InternalError(
                "向量维度不一致: 期望 %d，第 %d 个向量实际 %d 维"
                % (expected_dim, idx, len(v))
            )
        for j, x in enumerate(v):
            if not math.isfinite(x):
                raise InternalError(
                    "向量包含非法值: 第 %d 个向量第 %d 维 = %s"
                    % (idx, j, repr(x))
                )


# ============================================================
#  Base
# ============================================================

class BaseEmbeddingProvider:
    """Embedding Provider 基类"""

    name: str = "base"
    dim: int = 384

    def encode(self, texts: List[str]) -> List[List[float]]:
        raise NotImplementedError

    def encode_single(self, text: str) -> List[float]:
        result = self.encode([text])
        return result[0] if result else []

    # ---- 质量检查辅助方法 ----
    def _post_process(self, vectors: List[List[float]]) -> List[List[float]]:
        """统一做维度校验和归一化"""
        validate_vectors(vectors, self.dim)
        return [normalize_vec(v) for v in vectors]


# ============================================================
#  1. Mock Provider（开发测试用）
# ============================================================

class MockEmbeddingProvider(BaseEmbeddingProvider):
    """基于 Hash 的确定性伪随机向量生成器

    相同输入 -> 相同向量（可复现）
    字符重复也会在向量中留下痕迹（让同主题文本稍微相似）

    算法:
        - 对 hash(seed, i, text) 得到的 4 字节整数做 [-1, 1] 映射，得到 v0
        - 按字符做加权扰动（同一字符总出现在同一维度）
        - L2 归一化
    """

    name = "mock"

    def __init__(self, dim: int = 384, seed: int = 42):
        if dim <= 0:
            raise ValueError("dim 必须为正整数")
        self.dim = dim
        self.seed = seed

    # ---------- 公开 API ----------
    def encode(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        vectors: List[List[float]] = []
        for text in texts:
            vectors.append(self._text_to_vector(text))
        return self._post_process(vectors)

    # ---------- 内部 ----------
    def _text_to_vector(self, text: str) -> List[float]:
        # 1. 基础 hash 向量：每维独立 hash
        vec = [0.0] * self.dim
        text_bytes = (text or "").encode("utf-8")
        for i in range(self.dim):
            h = hashlib.sha256(
                b"%d:%d:%s" % (self.seed, i, text_bytes)
            ).digest()
            val = int.from_bytes(h[:4], "big", signed=False)
            vec[i] = (val / 0xFFFFFFFF) * 2.0 - 1.0  # [-1, 1]

        # 2. 字符层面的扰动：让相似文本更相似
        if text:
            for ch in text:
                ch_code = ord(ch)
                target_dim = ch_code % self.dim
                weight = (ch_code % 100) / 100.0
                vec[target_dim] += weight

        return vec


# ============================================================
#  2. Local Numpy Provider — 轻量本地实现
# ============================================================

class LocalNumpyEmbeddingProvider(BaseEmbeddingProvider):
    """基于字符/词 n-gram + 随机投影 的本地轻量向量生成器

    不需要任何外部模型文件，可以离线工作。
    虽然语义质量不及 bge-m3 / text-embedding-3，但在：
        - 无网络环境
        - 原型快速迭代
        - 冷启动 / 性能敏感场景
    下很有用。

    算法要点:
        1. 对输入文本做简单清洗（转小写、去多余空白）
        2. 提取字符 3-gram + 若干 token（中文 2-char window）
        3. 用 hash(ngram) -> 维度映射，每个 ngram 在一个随机维度累加
        4. L2 归一化 + 可选平滑
    """

    name = "local_numpy"

    _PUNCT_RE = re.compile(r"[\s,./?!;:\"'()\[\]\{\}_\-=+<>@#$%^&*`~]+")

    def __init__(self, dim: int = 384, ngram_size: int = 3, seed: int = 42):
        if dim <= 0:
            raise ValueError("dim 必须为正整数")
        self.dim = dim
        self.ngram_size = max(1, ngram_size)
        self.seed = seed

    # ---------- 公开 API ----------
    def encode(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if _HAS_NUMPY:
            return self._encode_fast(texts)
        return self._encode_slow(texts)

    # ---------- numpy 路径 ----------
    def _encode_fast(self, texts: List[str]) -> List[List[float]]:
        dim = self.dim
        ngram_size = self.ngram_size
        seed_bytes = ("%d" % self.seed).encode("utf-8")

        matrix = np.zeros((len(texts), dim), dtype="float32")

        for row_idx, text in enumerate(texts):
            clean = self._clean_text(text)
            n = len(clean)
            if n == 0:
                continue
            # 滑动窗口取 n-gram
            for i in range(max(1, n - ngram_size + 1)):
                gram = clean[i:i + ngram_size]
                h = hashlib.sha256(seed_bytes + gram.encode("utf-8")).digest()
                dim_idx = int.from_bytes(h[:4], "big", signed=False) % dim
                sign = 1.0 if (h[4] & 1) == 0 else -1.0
                matrix[row_idx, dim_idx] += sign

        # 归一化：按行 L2
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix = matrix / norms

        vectors: List[List[float]] = matrix.tolist()
        # 后处理（此时已归一化，但仍走一遍维度/有限值校验）
        validate_vectors(vectors, dim)
        return vectors

    # ---------- 纯 Python 路径 ----------
    def _encode_slow(self, texts: List[str]) -> List[List[float]]:
        dim = self.dim
        ngram_size = self.ngram_size
        seed_bytes = ("%d" % self.seed).encode("utf-8")

        vectors: List[List[float]] = []
        for text in texts:
            clean = self._clean_text(text)
            n = len(clean)
            v = [0.0] * dim
            if n > 0:
                for i in range(max(1, n - ngram_size + 1)):
                    gram = clean[i:i + ngram_size]
                    h = hashlib.sha256(seed_bytes + gram.encode("utf-8")).digest()
                    dim_idx = int.from_bytes(h[:4], "big", signed=False) % dim
                    sign = 1.0 if (h[4] & 1) == 0 else -1.0
                    v[dim_idx] += sign
            vectors.append(v)
        return self._post_process(vectors)

    # ---------- 文本清洗 ----------
    @classmethod
    def _clean_text(cls, text: str) -> str:
        if not text:
            return ""
        text = text.lower()
        text = cls._PUNCT_RE.sub(" ", text)
        return text.strip()


# ============================================================
#  3. Remote API Provider — 调用 OpenAI 兼容 Embedding API
# ============================================================

class RemoteAPIEmbeddingProvider(BaseEmbeddingProvider):
    """远程 HTTP Embedding API 调用者

    兼容:
        - OpenAI:  https://api.openai.com/v1
        - DeepSeek: https://api.deepseek.com/v1
        - SiliconFlow / Dify / Text Embedding 兼容服务

    请求格式:
        POST {api_url}/embeddings
        {
          "model": "bge-m3",
          "input": ["text1", "text2", ...]
        }

    响应格式:
        {
          "data": [
            {"embedding": [0.1, 0.2, ...]},
            ...
          ]
        }

    支持:
        - 自动批量切分 (batch_size)
        - 自动重试 (max_retries)
        - 请求超时
        - 自动维度检测（首次调用时）
    """

    name = "remote_api"

    def __init__(
        self,
        api_url: str,
        api_key: str = "",
        model: str = "bge-m3",
        dim: Optional[int] = None,
        timeout: int = 60,
        batch_size: int = 16,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
    ):
        if not api_url:
            raise ValueError("api_url 不能为空")
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._dim = dim or 0
        self.timeout = timeout
        self.batch_size = max(1, batch_size)
        self.max_retries = max(1, max_retries)
        self.retry_backoff = max(0.0, retry_backoff)

    # ---------- 属性 ----------
    @property
    def dim(self) -> int:
        return self._dim or 1024

    # ---------- 公开 API ----------
    def encode(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        try:
            import httpx  # type: ignore
        except ImportError:
            raise InternalError(
                "httpx 未安装，无法使用远程 Embedding API。"
                " 请运行: pip install httpx"
            )

        all_vectors: List[List[float]] = []
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer %s" % self.api_key

        # 按 batch_size 切分批次
        for batch_start in range(0, len(texts), self.batch_size):
            batch = texts[batch_start:batch_start + self.batch_size]
            batch_vectors = self._fetch_batch(httpx, headers, batch)
            all_vectors.extend(batch_vectors)

        # 检查数量
        if len(all_vectors) != len(texts):
            raise InternalError(
                "Embedding 数量不匹配: 请求 %d 个，得到 %d 个"
                % (len(texts), len(all_vectors))
            )

        # 自动检测维度
        if self._dim <= 0 and all_vectors:
            self._dim = len(all_vectors[0])

        # 维度/归一化校验
        validate_vectors(all_vectors, self.dim)
        return [normalize_vec(v) for v in all_vectors]

    # ---------- 内部 ----------
    def _fetch_batch(
        self,
        httpx_mod: Any,
        headers: Dict[str, str],
        batch: List[str],
    ) -> List[List[float]]:
        payload = {"model": self.model, "input": batch}

        last_exc: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with httpx_mod.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        "%s/embeddings" % self.api_url,
                        headers=headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                batch_vectors: List[List[float]] = []
                if "data" in data and isinstance(data["data"], list):
                    for item in data["data"]:
                        emb = item.get("embedding")
                        if emb is None:
                            continue
                        batch_vectors.append(list(float(x) for x in emb))
                if batch_vectors:
                    return batch_vectors
                raise InternalError("API 响应中未找到 embedding 数据")
            except Exception as e:
                last_exc = e
                if attempt < self.max_retries:
                    wait = self.retry_backoff * (2 ** (attempt - 1))
                    time.sleep(wait)
                    continue
                break

        raise InternalError(
            "远程 Embedding API 调用失败 (已重试 %d 次): %s"
            % (self.max_retries, str(last_exc))
        )


# ============================================================
#  4. Caching Provider — LRU 缓存装饰器
# ============================================================

class CachingEmbeddingProvider(BaseEmbeddingProvider):
    """为任意 Provider 增加 LRU 缓存层

    相同文本不会重复向量化，可以显著降低 API 调用次数和耗时。
    """

    name = "caching"

    def __init__(self, inner: BaseEmbeddingProvider, max_size: int = 1000):
        if inner is None:
            raise ValueError("inner provider 不能为空")
        self._inner = inner
        self.dim = inner.dim
        self.max_size = max(0, max_size)
        self._cache: "OrderedDict[str, List[float]]" = OrderedDict()
        self._hits = 0
        self._misses = 0

    # ---------- 统计 ----------
    def stats(self) -> Dict[str, int]:
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "hits": self._hits,
            "misses": self._misses,
        }

    def reset_stats(self) -> None:
        self._hits = 0
        self._misses = 0

    def clear(self) -> None:
        self._cache.clear()
        self.reset_stats()

    # ---------- 公开 API ----------
    def encode(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        if self.max_size == 0:
            return self._inner.encode(texts)

        result: List[Optional[List[float]]] = [None] * len(texts)
        miss_indices: List[int] = []
        miss_texts: List[str] = []

        for i, text in enumerate(texts):
            key = self._make_key(text)
            cached = self._cache.get(key)
            if cached is not None:
                result[i] = cached
                self._cache.move_to_end(key)
                self._hits += 1
            else:
                miss_indices.append(i)
                miss_texts.append(text)

        if miss_texts:
            self._misses += len(miss_texts)
            new_vectors = self._inner.encode(miss_texts)
            for j, vec in zip(miss_indices, new_vectors):
                result[j] = vec
                key = self._make_key(texts[j])
                self._cache[key] = list(vec)
                self._cache.move_to_end(key)
                # 逐出
                while len(self._cache) > self.max_size:
                    self._cache.popitem(last=False)

        # 此时 result 中所有项都不为 None
        return [list(v) for v in result]  # type: ignore[arg-type]

    # ---------- 工具 ----------
    @staticmethod
    def _make_key(text: str) -> str:
        # 用 hash 避免 cache key 过大
        raw = (text or "").encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


# ============================================================
#  5. EmbeddingService — 统一门面
# ============================================================

class EmbeddingService:
    """统一的 Embedding 服务门面

    负责:
        - 根据配置自动选择具体 Provider
        - 统一的 encode / encode_single 接口
        - 可选地叠加缓存层
    """

    def __init__(
        self,
        provider: Optional[BaseEmbeddingProvider] = None,
        use_cache: bool = True,
        cache_size: int = 1000,
    ):
        self._base_provider = provider or MockEmbeddingProvider()
        if use_cache and cache_size > 0:
            self._provider: BaseEmbeddingProvider = CachingEmbeddingProvider(
                self._base_provider, max_size=cache_size
            )
        else:
            self._provider = self._base_provider
        self.dim = self._provider.dim

    @classmethod
    def from_settings(cls, settings: Any) -> "EmbeddingService":
        """根据全局配置构造 EmbeddingService"""
        api_url = getattr(settings, "EMBEDDING_API_URL", "") or ""
        api_key = getattr(settings, "EMBEDDING_API_KEY", "") or ""
        model = getattr(settings, "EMBEDDING_MODEL", "bge-m3") or "bge-m3"
        batch_size = int(getattr(settings, "EMBEDDING_BATCH_SIZE", 16) or 16)
        timeout = int(getattr(settings, "EMBEDDING_TIMEOUT", 60) or 60)
        max_retries = int(getattr(settings, "EMBEDDING_MAX_RETRIES", 3) or 3)
        cache_size = int(getattr(settings, "EMBEDDING_CACHE_SIZE", 1000) or 1000)
        default_dim = int(getattr(settings, "EMBEDDING_DEFAULT_DIM", 384) or 384)

        if api_url:
            base = RemoteAPIEmbeddingProvider(
                api_url=api_url,
                api_key=api_key,
                model=model,
                batch_size=batch_size,
                timeout=timeout,
                max_retries=max_retries,
                dim=default_dim,
            )
        else:
            base = LocalNumpyEmbeddingProvider(dim=default_dim)

        return cls(provider=base, use_cache=(cache_size > 0), cache_size=cache_size)

    # ---------- 属性 ----------
    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def underlying(self) -> BaseEmbeddingProvider:
        return self._base_provider

    # ---------- 公开 API ----------
    def encode(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return self._provider.encode(texts)

    def encode_single(self, text: str) -> List[float]:
        vectors = self._provider.encode([text])
        return vectors[0] if vectors else []

    def encode_to_numpy(self, texts: List[str]):
        vectors = self.encode(texts)
        if not vectors:
            return None
        if _HAS_NUMPY:
            import numpy as np  # type: ignore
            return np.array(vectors, dtype="float32")
        return vectors

    # ---------- 质量评估辅助 ----------
    def quality_report(self, sample_texts: Optional[List[str]] = None) -> Dict[str, Any]:
        """生成当前 Embedding 服务的快速质量报告"""
        samples = sample_texts or [
            "RAG (Retrieval-Augmented Generation) 结合了检索与语言模型生成。",
            "向量数据库存储高维向量用于相似度搜索。",
            "大语言模型如 GPT 和 Llama 用于文本生成任务。",
            "文档分块是将长文本切分为较小片段以便向量化。",
            "Python 是一种解释型高级编程语言。",
        ]
        vectors = self.encode(samples)
        dim = self.dim

        # 余弦相似度矩阵（样本之间）
        n = len(vectors)
        sim_matrix: List[List[float]] = []
        for i in range(n):
            row: List[float] = []
            for j in range(n):
                row.append(round(cosine_similarity(vectors[i], vectors[j]), 4))
            sim_matrix.append(row)

        # 向量范数
        norms = [round(math.sqrt(sum(x * x for x in v)), 4) for v in vectors]

        # 维度检查
        dim_ok = all(len(v) == dim for v in vectors)
        finite_ok = all(
            math.isfinite(x) for v in vectors for x in v
        )

        return {
            "provider": self.provider_name,
            "dim": dim,
            "sample_count": n,
            "dim_ok": dim_ok,
            "finite_ok": finite_ok,
            "norms": norms,
            "cosine_similarity_matrix": sim_matrix,
        }


# ============================================================
#  公开导出
# ============================================================

__all__ = [
    "BaseEmbeddingProvider",
    "MockEmbeddingProvider",
    "LocalNumpyEmbeddingProvider",
    "RemoteAPIEmbeddingProvider",
    "CachingEmbeddingProvider",
    "EmbeddingService",
    "normalize_vec",
    "cosine_similarity",
    "validate_vectors",
]