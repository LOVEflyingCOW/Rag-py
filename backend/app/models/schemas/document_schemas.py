from __future__ import annotations

from datetime import datetime
from typing import Optional, List

# Pydantic v1/v2 兼容: 项目固定 Pydantic v1.10+
from pydantic import BaseModel, Field
try:
    from pydantic import ConfigDict  # v2+
except ImportError:  # pragma: no cover - v1 fallback
    def ConfigDict(**kwargs):  # type: ignore
        return kwargs

__all__ = [
    "DocumentInfo",
    "DocumentListResponse",
    "ChunkInfo",
    "DocumentUploadResponse",
    "DocumentUploadRequest",
    "SearchQuery",
    "SearchResult",
    "SearchResponse",
]


class DocumentUploadRequest(BaseModel):
    """文档上传请求（multipart/form-data 的附加参数）"""
    knowledge_base_id: int
    filename: Optional[str] = None
    description: Optional[str] = None


class DocumentInfo(BaseModel):
    """文档信息响应"""
    id: int
    knowledge_base_id: int
    filename: str
    file_type: Optional[str] = None
    file_size: int = 0
    status: str = "pending"
    total_chunks: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        orm_mode = True

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    """文档列表响应"""
    items: List[DocumentInfo]
    total: int
    page: int
    page_size: int


class ChunkInfo(BaseModel):
    """分块信息"""
    id: int
    document_id: int
    content: str
    chunk_index: int
    vector_index: int = -1

    class Config:
        orm_mode = True

    model_config = ConfigDict(from_attributes=True)


class DocumentUploadResponse(BaseModel):
    """文档上传响应"""
    document: DocumentInfo
    message: str = "上传成功，正在处理中"


class SearchQuery(BaseModel):
    """搜索查询"""
    query: str = Field(..., min_length=1, max_length=500, description="查询文本")
    top_k: int = Field(5, ge=1, le=50, description="返回最相关的结果数量")
    min_score: float = Field(0.0, ge=0.0, le=1.0, description="最小相似度分数")


class SearchResult(BaseModel):
    """单个搜索结果"""
    chunk_id: int
    document_id: int
    knowledge_base_id: int
    content: str
    score: float
    document_filename: Optional[str] = None


class SearchResponse(BaseModel):
    """搜索响应"""
    query: str
    results: List[SearchResult]
    total: int
    search_time_ms: float