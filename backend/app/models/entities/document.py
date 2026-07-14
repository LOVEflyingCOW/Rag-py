from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.database import Base


class Document(Base):
    """文档表"""

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    filename = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=False)
    file_type = Column(String(50))
    file_size = Column(Integer, default=0)
    content_text = Column(Text, nullable=True)
    status = Column(String(50), default="pending")
    total_chunks = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("Chunk", back_populates="document", lazy="dynamic")

    def __repr__(self):
        return "<Document(id=%d, filename='%s')>" % (self.id, self.filename)


class Chunk(Base):
    """文档分块表"""

    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    chunk_index = Column(Integer, default=0)
    metadata_ = Column("metadata", Text, nullable=True)
    vector_index = Column(Integer, default=-1)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    document = relationship("Document", back_populates="chunks")

    def __repr__(self):
        return "<Chunk(id=%d, document_id=%d, index=%d)>" % (self.id, self.document_id, self.chunk_index)