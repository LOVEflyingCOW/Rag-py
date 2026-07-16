"""Embedding API 请求/响应 Schema"""
from __future__ import annotations

from typing import Any, List, Optional, Dict

from pydantic import BaseModel, Field


class EncodeRequest(BaseModel):
    """文本 -> 向量 编码请求"""
    texts: List[str] = Field(..., min_items=1, max_items=256, description="待编码的文本列表")


class EncodeSingleRequest(BaseModel):
    """单文本编码请求"""
    text: str = Field(..., min_length=1, max_length=8192, description="待编码的文本")


class EncodingInfo(BaseModel):
    """单个编码结果（不含完整向量，避免响应过大）"""
    dim: int
    norm: float
    sample_preview: List[float]  # 前 N 维预览


class EncodeResponse(BaseModel):
    """编码响应"""
    provider: str
    dim: int
    count: int
    items: List[EncodingInfo]
    cache_stats: Optional[Dict[str, Any]] = None


class SimilarityRequest(BaseModel):
    """相似度计算请求"""
    text_a: str = Field(..., min_length=1, max_length=4096)
    text_b: str = Field(..., min_length=1, max_length=4096)


class SimilarityResponse(BaseModel):
    """相似度响应"""
    provider: str
    score: float
    interpretation: str


class EmbeddingStatus(BaseModel):
    """Embedding 服务状态"""
    provider: str
    dim: int
    caching_enabled: bool
    sample_similarity_matrix: List[List[float]]
    sample_texts: List[str]


__all__ = [
    "EncodeRequest",
    "EncodeSingleRequest",
    "EncodingInfo",
    "EncodeResponse",
    "SimilarityRequest",
    "SimilarityResponse",
    "EmbeddingStatus",
]