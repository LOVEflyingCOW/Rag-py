"""Embedding Provider - 文本向量化服务

设计为可插拔的接口，支持多种实现:
- MockProvider: 本地伪随机向量，仅用于开发测试（不依赖任何 API）
- RemoteAPIProvider: 调用远程 HTTP Embedding API (OpenAI/DeepSeek/SiliconFlow 兼容格式)

接口统一:
    provider.encode(texts: List[str]) -> List[List[float]]
    provider.dim -> int  (向量维度)
    provider.name -> str
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from typing import List, Optional, Any, Dict

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


class BaseEmbeddingProvider:
    """Embedding 服务基类"""

    name: str = "base"
    dim: int = 384

    def encode(self, texts: List[str]) -> List[List[float]]:
        """将一组文本编码为向量列表"""
        raise NotImplementedError

    def encode_single(self, text: str) -> List[float]:
        """编码单个文本"""
        result = self.encode([text])
        return result[0] if result else []


class MockEmbeddingProvider(BaseEmbeddingProvider):
    """本地 Mock Embedding Provider

    使用文本 hash 生成稳定的伪随机向量。
    - 相同文本始终得到相同向量（可复现）
    - 语义接近的文本不会产生相似向量（这是 Mock 限制）
    - 仅用于开发测试，不应用于生产环境

    设计目标: 不依赖任何外部服务即可跑通完整 RAG 流程。
    """

    name = "mock"
    dim = 64  # 使用较小维度以便快速测试

    def __init__(self, dim: int = 64, seed: int = 42):
        self.dim = dim
        self.seed = seed

    def encode(self, texts: List[str]) -> List[List[float]]:
        vectors = []
        for text in texts:
            vec = self._text_to_vector(text)
            vectors.append(vec)
        return vectors

    def _text_to_vector(self, text: str) -> List[float]:
        """基于 hash 生成稳定的伪随机向量，并做 L2 归一化"""
        if not text:
            text = ""

        vec = [0.0] * self.dim
        # 使用多轮 hash 填充向量
        for i in range(self.dim):
            hash_input = "%d:%d:%s" % (self.seed, i, text)
            h_bytes = hashlib.sha256(hash_input.encode("utf-8")).digest()
            # 取前 4 字节作为整数，映射到 [-1, 1]
            val = int.from_bytes(h_bytes[:4], "big", signed=False)
            normalized = (val / 0xFFFFFFFF) * 2 - 1  # [-1, 1]
            vec[i] = normalized

        # 加上内容字符特征（让同一个字符在向量中留下痕迹）
        for idx, ch in enumerate(text):
            target_dim = idx % self.dim
            vec[target_dim] += (ord(ch) % 100) / 100.0 * 0.3

        # L2 归一化
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec


class RemoteAPIEmbeddingProvider(BaseEmbeddingProvider):
    """远程 API Embedding Provider

    兼容 OpenAI / DeepSeek / SiliconFlow / Dify 等 API 格式:
    POST {api_url}/embeddings
    {
      "model": "bge-m3",
      "input": ["text1", "text2", ...]
    }
    Response:
    {
      "data": [
        {"embedding": [0.1, 0.2, ...]},
        ...
      ]
    }
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
    ):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._dim = dim or 1024
        self.timeout = timeout
        self.batch_size = batch_size

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []

        try:
            import httpx
        except ImportError:
            raise RuntimeError("httpx 未安装，无法使用远程 Embedding API")

        all_vectors: List[List[float]] = []

        for batch_start in range(0, len(texts), self.batch_size):
            batch = texts[batch_start:batch_start + self.batch_size]

            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = "Bearer %s" % self.api_key

            payload = {
                "model": self.model,
                "input": batch,
            }

            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.post(
                        "%s/embeddings" % self.api_url,
                        headers=headers,
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    batch_vectors = []
                    if "data" in data and isinstance(data["data"], list):
                        for item in data["data"]:
                            if "embedding" in item:
                                batch_vectors.append(item["embedding"])

                    if batch_vectors:
                        all_vectors.extend(batch_vectors)
                    else:
                        raise ValueError("API 响应中未找到 embedding 数据")
            except Exception as e:
                raise RuntimeError("远程 Embedding API 调用失败: %s" % str(e))

        if len(all_vectors) != len(texts):
            raise RuntimeError(
                "Embedding 数量不匹配: 请求 %d 个，得到 %d 个" % (len(texts), len(all_vectors))
            )

        return all_vectors


class EmbeddingService:
    """统一的 Embedding 服务门面

    负责:
    - 选择并初始化具体的 Provider
    - 提供编码接口
    - 缓存最近的编码结果（可选）
    """

    def __init__(self, provider: Optional[BaseEmbeddingProvider] = None):
        self.provider = provider or MockEmbeddingProvider()

    @classmethod
    def from_settings(cls, settings: Any) -> "EmbeddingService":
        """根据应用配置自动选择 Provider"""
        api_url = getattr(settings, "EMBEDDING_API_URL", "")
        api_key = getattr(settings, "EMBEDDING_API_KEY", "")
        model = getattr(settings, "EMBEDDING_MODEL", "bge-m3")

        if api_url:
            provider = RemoteAPIEmbeddingProvider(
                api_url=api_url,
                api_key=api_key,
                model=model,
            )
        else:
            provider = MockEmbeddingProvider()

        return cls(provider=provider)

    @property
    def dim(self) -> int:
        return self.provider.dim

    @property
    def provider_name(self) -> str:
        return self.provider.name

    def encode(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        return self.provider.encode(texts)

    def encode_single(self, text: str) -> List[float]:
        return self.provider.encode_single(text)

    def encode_to_numpy(self, texts: List[str]):
        """编码为 numpy 数组（如果有 numpy）"""
        vectors = self.encode(texts)
        if not vectors:
            return None
        if _HAS_NUMPY:
            import numpy as np
            return np.array(vectors, dtype="float32")
        return vectors


__all__ = [
    "BaseEmbeddingProvider",
    "MockEmbeddingProvider",
    "RemoteAPIEmbeddingProvider",
    "EmbeddingService",
]