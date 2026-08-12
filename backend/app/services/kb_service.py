from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from app.models.entities.knowledge_base import KnowledgeBase
from app.models.schemas import KnowledgeBaseCreate, KnowledgeBaseUpdate


class KnowledgeBaseService:
    """知识库服务 - 封装所有知识库的业务逻辑"""

    def __init__(self, db: Session):
        self.db = db

    def create(self, payload: KnowledgeBaseCreate, user_id: Optional[int]) -> KnowledgeBase:
        """创建知识库"""
        kb = KnowledgeBase(
            name=payload.name,
            description=payload.description,
            user_id=user_id,
            embedding_model=payload.embedding_model or "default",
            chunk_size=payload.chunk_size or 500,
            chunk_overlap=payload.chunk_overlap or 50,
            is_public=payload.is_public or False,
            status="active",
            total_documents=0,
            total_chunks=0,
        )
        self.db.add(kb)
        self.db.commit()
        self.db.refresh(kb)
        return kb

    def get_by_id(self, kb_id: int, user_id: Optional[int] = None) -> Optional[KnowledgeBase]:
        """通过 ID 获取知识库（用户仅能访问自己的或公开的）"""
        query = self.db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id)
        if user_id is not None:
            query = query.filter(
                or_(
                    KnowledgeBase.user_id == user_id,
                    KnowledgeBase.is_public == True,
                )
            )
        return query.first()

    def list(self, user_id: Optional[int] = None,
             page: int = 1, page_size: int = 20,
             keyword: Optional[str] = None) -> Tuple[List[KnowledgeBase], int]:
        """获取知识库列表

        Returns:
            (知识库列表, 总条数)
        """
        query = self.db.query(KnowledgeBase)

        if user_id is not None:
            query = query.filter(
                or_(
                    KnowledgeBase.user_id == user_id,
                    KnowledgeBase.is_public == True,
                )
            )

        if keyword:
            like_pattern = "%" + keyword + "%"
            query = query.filter(
                or_(
                    KnowledgeBase.name.like(like_pattern),
                    KnowledgeBase.description.like(like_pattern),
                )
            )

        query = query.order_by(KnowledgeBase.updated_at.desc())

        total = query.count()
        items = query.offset((page - 1) * page_size).limit(page_size).all()

        return items, total

    def update(self, kb_id: int, payload: KnowledgeBaseUpdate, user_id: int) -> Optional[KnowledgeBase]:
        """更新知识库（仅限所有者）"""
        kb = self.db.query(KnowledgeBase).filter(
            and_(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.user_id == user_id,
            )
        ).first()

        if kb is None:
            return None

        update_data = payload.dict(exclude_unset=True)
        for field, value in update_data.items():
            if value is not None:
                setattr(kb, field, value)

        self.db.commit()
        self.db.refresh(kb)
        return kb

    def delete(self, kb_id: int, user_id: int) -> bool:
        """删除知识库（仅限所有者）

        级联删除：文档分块 → 文档 → 会话消息 → 会话 → 知识库
        """
        kb = self.db.query(KnowledgeBase).filter(
            and_(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.user_id == user_id,
            )
        ).first()

        if kb is None:
            return False

        from app.models.entities.document import Document, DocumentChunk
        from app.models.entities.conversation import Conversation, ChatMessageRecord

        # 1) 删除所有分块（通过 knowledge_base_id 直接关联）
        self.db.query(DocumentChunk).filter(
            DocumentChunk.knowledge_base_id == kb_id
        ).delete(synchronize_session=False)

        # 2) 删除所有文档
        self.db.query(Document).filter(
            Document.knowledge_base_id == kb_id
        ).delete(synchronize_session=False)

        # 3) 删除会话消息（先查会话 ID，再删消息）
        conv_ids = [c.id for c in self.db.query(Conversation).filter(
            Conversation.knowledge_base_id == kb_id
        ).all()]
        if conv_ids:
            self.db.query(ChatMessageRecord).filter(
                ChatMessageRecord.conversation_id.in_(conv_ids)
            ).delete(synchronize_session=False)

        # 4) 删除会话
        self.db.query(Conversation).filter(
            Conversation.knowledge_base_id == kb_id
        ).delete(synchronize_session=False)

        # 5) 删除知识库本身
        self.db.delete(kb)
        self.db.commit()
        return True

    def increment_documents(self, kb_id: int, delta: int = 1) -> None:
        """更新文档计数"""
        kb = self.db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if kb:
            kb.total_documents = max(0, kb.total_documents + delta)
            self.db.commit()

    def increment_chunks(self, kb_id: int, delta: int = 1) -> None:
        """更新分块计数"""
        kb = self.db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if kb:
            kb.total_chunks = max(0, kb.total_chunks + delta)
            self.db.commit()