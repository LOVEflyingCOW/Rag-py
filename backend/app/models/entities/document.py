from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.database import Base, _is_sqlite

# 根据数据库类型动态选择列类型
if _is_sqlite:
    # SQLite: 使用 JSON 存储向量 (降级方案)
    VectorType = JSON
    TSVectorType = Text
else:
    # PostgreSQL: 使用 pgvector 和原生 TSVector
    try:
        from pgvector.sqlalchemy import Vector
        VectorType = Vector(384)  # 与 embedding 维度一致
    except ImportError:
        VectorType = JSON
    
    from sqlalchemy.dialects.postgresql import TSVector
    TSVectorType = TSVector()


class Document(Base):
    """文档表"""

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    filename = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=True)
    file_type = Column(String(50))
    mime_type = Column(String(100))
    file_size = Column(Integer, default=0)
    size_bytes = Column(Integer, default=0)
    description = Column(Text, nullable=True)
    content_text = Column(Text, nullable=True)
    status = Column(String(50), default="pending")
    total_chunks = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", lazy="dynamic")

    def __repr__(self):
        return "<Document(id=%d, filename='%s')>" % (self.id, self.filename)


class DocumentChunk(Base):
    """文档分块表"""

    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    chunk_index = Column(Integer, default=0)
    metadata_ = Column("metadata", Text, nullable=True)
    vector_index = Column(Integer, default=-1)
    
    # Phase 4: PostgreSQL 原生检索支持
    embedding = Column("embedding", VectorType, nullable=True)
    search_vector = Column("search_vector", TSVectorType, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    document = relationship("Document", back_populates="chunks")
    
    __table_args__ = (
        # 为 pgvector 向量列添加 IVF 索引 (仅 PostgreSQL)
        Index("ix_chunks_embedding_cosine", "embedding", postgresql_using="ivfflat"),
        # 为全文检索列添加 GIN 索引
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
    ) if not _is_sqlite else ()

    def __repr__(self):
        return "<DocumentChunk(id=%d, document_id=%d, index=%d)>" % (self.id, self.document_id, self.chunk_index)