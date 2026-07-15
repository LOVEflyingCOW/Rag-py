"""Processors - 数据处理管道

document  - 文档文本提取与智能分块
embedding - 文本向量化服务
retrieval - 向量存储与相似度搜索
"""
from .document.document_processor import DocumentProcessor, SUPPORTED_EXTENSIONS
from .embedding.embedding_service import (
    BaseEmbeddingProvider,
    MockEmbeddingProvider,
    RemoteAPIEmbeddingProvider,
    EmbeddingService,
)
from .retrieval.vector_store import (
    BaseVectorStore,
    FAISSVectorStore,
    PurePythonVectorStore,
    VectorStoreManager,
    _HAS_FAISS,
)

__all__ = [
    "DocumentProcessor",
    "SUPPORTED_EXTENSIONS",
    "BaseEmbeddingProvider",
    "MockEmbeddingProvider",
    "RemoteAPIEmbeddingProvider",
    "EmbeddingService",
    "BaseVectorStore",
    "FAISSVectorStore",
    "PurePythonVectorStore",
    "VectorStoreManager",
    "_HAS_FAISS",
]