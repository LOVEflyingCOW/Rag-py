from __future__ import annotations

"""Pydantic schemas - API 请求/响应数据结构"""

from datetime import datetime
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field


# ============ User / Auth ============

class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[str] = None
    password: str = Field(..., min_length=6)

class UserLogin(BaseModel):
    username: str
    password: str

class UserInfo(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    is_active: bool = True
    is_admin: bool = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class TokenData(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int = 900  # Access Token 有效期 (秒)
    user: UserInfo


class RefreshTokenRequest(BaseModel):
    """Refresh Token 请求"""
    refresh_token: str = Field(..., description="Refresh Token")


class LogoutRequest(BaseModel):
    """登出请求"""
    refresh_token: Optional[str] = Field(None, description="Refresh Token (可选, 同时撤销)")


# ============ Health ============

class HealthInfo(BaseModel):
    status: str
    app_name: str
    version: str
    database: str
    redis: Optional[str] = None
    timestamp: datetime


# ============ Knowledge Base ============

class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    embedding_model: Optional[str] = "default"
    chunk_size: Optional[int] = 500
    chunk_overlap: Optional[int] = 50
    is_public: Optional[bool] = False

class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    is_public: Optional[bool] = None
    status: Optional[str] = None

class KnowledgeBaseInfo(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    user_id: Optional[int] = None
    embedding_model: str = "default"
    chunk_size: int = 500
    chunk_overlap: int = 50
    is_public: bool = False
    status: str = "active"
    total_documents: int = 0
    total_chunks: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class KnowledgeBaseListResponse(BaseModel):
    items: List[KnowledgeBaseInfo] = []
    total: int = 0
    page: int = 1
    page_size: int = 20


# ============ Document ============

class DocumentInfo(BaseModel):
    id: int
    knowledge_base_id: int
    filename: str
    file_type: Optional[str] = None
    file_size: int = 0
    status: str = "pending"
    total_chunks: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class DocumentListResponse(BaseModel):
    items: List[DocumentInfo] = []
    total: int = 0
    page: int = 1
    page_size: int = 20

class DocumentUploadResponse(BaseModel):
    document: DocumentInfo
    message: str = ""

class ChunkInfo(BaseModel):
    id: int
    document_id: int
    content: str
    chunk_index: int = 0
    vector_index: int = -1

    class Config:
        from_attributes = True

class SearchResponse(BaseModel):
    query: str
    results: List[Dict[str, Any]] = []
    total: int = 0
    search_time_ms: float = 0.0


# ============ Chat ============

class ChatMessageItem(BaseModel):
    role: str = "user"
    content: str

class ChatRequest(BaseModel):
    knowledge_base_id: int
    message: str
    history: Optional[List[ChatMessageItem]] = None
    top_k: Optional[int] = None
    min_score: Optional[float] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    include_raw: bool = False

class RetrievedChunkItem(BaseModel):
    chunk_id: int
    score: float
    document_id: Optional[int] = None
    document_filename: Optional[str] = None
    content: str

class ChatResponse(BaseModel):
    query: str
    answer: str
    model: Optional[str] = None
    provider: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    latency_ms: float = 0.0
    retrieved_chunks: List[RetrievedChunkItem] = []
    system_prompt: Optional[str] = None

class LLMProviderInfo(BaseModel):
    provider: str
    model: str
    has_api_key: bool = False
    supported_providers: List[str] = []


# ============ Embedding ============

class EncodeRequest(BaseModel):
    texts: List[str]

class EncodeSingleRequest(BaseModel):
    text: str

class EncodingInfo(BaseModel):
    dim: int
    norm: float
    sample_preview: List[float] = []

class EncodeResponse(BaseModel):
    provider: str
    dim: int
    count: int
    items: List[EncodingInfo] = []
    cache_stats: Optional[Dict[str, Any]] = None

class SimilarityRequest(BaseModel):
    text_a: str
    text_b: str

class SimilarityResponse(BaseModel):
    provider: str
    score: float
    interpretation: str

class EmbeddingStatus(BaseModel):
    provider: str
    dim: int
    caching_enabled: bool
    sample_similarity_matrix: Optional[List[List[float]]] = None
    sample_texts: List[str] = []


# ============ Retrieval / Vector Store ============

class VectorSearchQuery(BaseModel):
    query_text: str
    top_k: int = 5
    min_score: float = 0.0

class VectorSearchItem(BaseModel):
    vector_index: int
    score: float
    chunk_id: Optional[int] = None
    document_id: Optional[int] = None
    content_preview: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class VectorSearchResponse(BaseModel):
    knowledge_base_id: int
    query_text: str
    hits: int
    items: List[VectorSearchItem] = []
    backend: str = "unknown"

class IndexStatusResponse(BaseModel):
    knowledge_base_id: int
    exists: bool = False
    loaded: bool = False
    consistent: Optional[bool] = None
    backend: Optional[str] = None
    dim: Optional[int] = None
    total_vectors: int = 0
    next_index: int = 0
    metadata_count: int = 0
    nlist: int = 0
    nprobe: int = 0
    is_trained: bool = False
    ntotal: int = 0
    issues: Optional[List[str]] = None
    path: Optional[str] = None

class IndexOperationResponse(BaseModel):
    success: bool
    knowledge_base_id: int
    message: str

class GlobalIndexStatusResponse(BaseModel):
    base_dir: str
    total_kbs_on_disk: int = 0
    stored_kbs: List[Dict[str, Any]] = []
    faiss_available: bool = False
    numpy_available: bool = False
    default_dim: int = 384