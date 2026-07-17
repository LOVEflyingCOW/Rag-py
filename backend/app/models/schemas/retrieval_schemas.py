"""Retrieval API schemas - 向量索引管理 / 搜索 / 统计"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class VectorSearchQuery(BaseModel):
    """基于文本的向量搜索请求"""
    query_text: str = Field(..., min_length=1, max_length=2048)
    top_k: int = Field(default=5, ge=1, le=100)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)


class VectorSearchItem(BaseModel):
    """单个搜索结果项"""
    vector_index: int
    score: float
    chunk_id: Optional[int] = None
    document_id: Optional[int] = None
    content_preview: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class VectorSearchResponse(BaseModel):
    """搜索结果响应"""
    knowledge_base_id: int
    query_text: str
    hits: int
    items: List[VectorSearchItem]
    backend: str


class IndexStatusResponse(BaseModel):
    """向量索引状态"""
    knowledge_base_id: int
    exists: bool
    loaded: bool
    consistent: Optional[bool] = None
    backend: Optional[str] = None
    dim: Optional[int] = None
    total_vectors: Optional[int] = None
    next_index: Optional[int] = None
    metadata_count: Optional[int] = None
    nlist: Optional[int] = None
    nprobe: Optional[int] = None
    is_trained: Optional[bool] = None
    ntotal: Optional[int] = None
    issues: Optional[List[str]] = None
    path: Optional[str] = None


class IndexOperationResponse(BaseModel):
    """索引操作结果"""
    success: bool
    knowledge_base_id: int
    message: str
    details: Optional[Dict[str, Any]] = None


class GlobalIndexStatusResponse(BaseModel):
    """全局索引状态"""
    base_dir: str
    total_kbs_on_disk: int
    stored_kbs: List[int]
    faiss_available: bool
    numpy_available: bool
    default_dim: int


__all__ = [
    "VectorSearchQuery",
    "VectorSearchItem",
    "VectorSearchResponse",
    "IndexStatusResponse",
    "IndexOperationResponse",
    "GlobalIndexStatusResponse",
]