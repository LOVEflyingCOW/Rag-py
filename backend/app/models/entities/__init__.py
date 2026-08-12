"""ORM 实体模块包。

必须显式 import 各子模块，以便 SQLAlchemy 正确扫描 relationship 中
以字符串引用的类名（如 relationship("KnowledgeBase")）。
"""
from .user import User
from .knowledge_base import KnowledgeBase
from .document import Document, DocumentChunk
from .conversation import Conversation, ChatMessageRecord

__all__ = [
    "User",
    "KnowledgeBase",
    "Document",
    "DocumentChunk",
    "Conversation",
    "ChatMessageRecord",
]