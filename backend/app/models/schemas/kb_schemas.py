from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class KnowledgeBaseCreate(BaseModel):
    """创建知识库请求"""
    name: str = Field(..., min_length=1, max_length=200, description="知识库名称")
    description: Optional[str] = Field(None, description="知识库描述")
    embedding_model: Optional[str] = Field("default", description="向量模型名称")
    chunk_size: Optional[int] = Field(500, ge=100, le=2000, description="分块大小")
    chunk_overlap: Optional[int] = Field(50, ge=0, le=500, description="分块重叠")
    is_public: Optional[bool] = Field(False, description="是否公开")


class KnowledgeBaseUpdate(BaseModel):
    """更新知识库请求"""
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None)
    embedding_model: Optional[str] = Field(None)
    chunk_size: Optional[int] = Field(None, ge=100, le=2000)
    chunk_overlap: Optional[int] = Field(None, ge=0, le=500)
    is_public: Optional[bool] = Field(None)
    status: Optional[str] = Field(None)


class KnowledgeBaseInfo(BaseModel):
    """知识库信息响应"""
    id: int
    name: str
    description: Optional[str] = None
    user_id: Optional[int] = None
    embedding_model: str
    chunk_size: int
    chunk_overlap: int
    is_public: bool
    status: str
    total_documents: int
    total_chunks: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        orm_mode = True


class KnowledgeBaseListResponse(BaseModel):
    """知识库列表响应"""
    items: List[KnowledgeBaseInfo]
    total: int
    page: int
    page_size: int