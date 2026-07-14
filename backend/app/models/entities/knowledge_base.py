from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.database import Base


class KnowledgeBase(Base):
    """知识库表"""

    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    embedding_model = Column(String(100), default="default")
    chunk_size = Column(Integer, default=500)
    chunk_overlap = Column(Integer, default=50)
    is_public = Column(Boolean, default=False, index=True)
    status = Column(String(50), default="active")
    total_documents = Column(Integer, default=0)
    total_chunks = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="knowledge_bases")
    documents = relationship("Document", back_populates="knowledge_base", lazy="dynamic")
    conversations = relationship("Conversation", back_populates="knowledge_base", lazy="dynamic")

    def __repr__(self):
        return "<KnowledgeBase(id=%d, name='%s')>" % (self.id, self.name)