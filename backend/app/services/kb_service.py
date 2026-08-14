from __future__ import annotations

from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, and_, select, delete, func

from app.models.entities.knowledge_base import KnowledgeBase
from app.models.schemas import KnowledgeBaseCreate, KnowledgeBaseUpdate


class KnowledgeBaseService:
    """知识库服务 - 封装所有知识库的业务逻辑"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, payload: KnowledgeBaseCreate, user_id: Optional[int]) -> KnowledgeBase:
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
        await self.db.commit()
        await self.db.refresh(kb)
        return kb

    async def get_by_id(self, kb_id: int, user_id: Optional[int] = None) -> Optional[KnowledgeBase]:
        """通过 ID 获取知识库（用户仅能访问自己的或公开的）

        读多写少场景, 使用 Redis Cache-Aside 缓存 (TTL=60s).
        用户权限变更时 (update/delete) 主动失效.
        """
        # 公开知识库或用户自己的知识库 → 缓存
        if user_id is not None:
            from app.core.cache import get_kb_cached

            async def _fetch():
                stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
                stmt = stmt.where(
                    or_(
                        KnowledgeBase.user_id == user_id,
                        KnowledgeBase.is_public == True,
                    )
                )
                result = await self.db.execute(stmt)
                kb = result.scalars().first()
                if kb:
                    return {
                        "id": kb.id,
                        "name": kb.name,
                        "description": kb.description,
                        "user_id": kb.user_id,
                        "is_public": kb.is_public,
                        "status": kb.status,
                        "total_documents": kb.total_documents,
                        "total_chunks": kb.total_chunks,
                        "embedding_model": kb.embedding_model,
                        "chunk_size": kb.chunk_size,
                        "chunk_overlap": kb.chunk_overlap,
                        "created_at": kb.created_at.isoformat() if kb.created_at else None,
                        "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
                    }
                return None

            cached_data = await get_kb_cached(kb_id, _fetch, ttl=60)
            if cached_data is None:
                return None

            # 从缓存数据重建 ORM 对象 (轻量级, 仅用于下游使用)
            kb = KnowledgeBase()
            for key, value in cached_data.items():
                setattr(kb, key, value)
            return kb

        # 无用户 ID → 直接查 DB (公开知识库无需缓存)
        stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def list(self, user_id: Optional[int] = None,
                   page: int = 1, page_size: int = 20,
                   keyword: Optional[str] = None) -> Tuple[List[KnowledgeBase], int]:
        """获取知识库列表

        Returns:
            (知识库列表, 总条数)
        """
        conditions = []

        if user_id is not None:
            conditions.append(
                or_(
                    KnowledgeBase.user_id == user_id,
                    KnowledgeBase.is_public == True,
                )
            )

        if keyword:
            like_pattern = "%" + keyword + "%"
            conditions.append(
                or_(
                    KnowledgeBase.name.like(like_pattern),
                    KnowledgeBase.description.like(like_pattern),
                )
            )

        # 总条数
        count_stmt = select(func.count()).select_from(KnowledgeBase)
        for cond in conditions:
            count_stmt = count_stmt.where(cond)
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar()

        # 分页列表
        items_stmt = select(KnowledgeBase)
        for cond in conditions:
            items_stmt = items_stmt.where(cond)
        items_stmt = items_stmt.order_by(KnowledgeBase.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
        items_result = await self.db.execute(items_stmt)
        items = items_result.scalars().all()

        return items, total

    async def update(self, kb_id: int, payload: KnowledgeBaseUpdate, user_id: int) -> Optional[KnowledgeBase]:
        """更新知识库（仅限所有者）"""
        stmt = select(KnowledgeBase).where(
            and_(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.user_id == user_id,
            )
        )
        result = await self.db.execute(stmt)
        kb = result.scalars().first()

        if kb is None:
            return None

        update_data = payload.dict(exclude_unset=True)
        for field, value in update_data.items():
            if value is not None:
                setattr(kb, field, value)

        await self.db.commit()
        await self.db.refresh(kb)

        # 失效缓存
        from app.core.cache import invalidate_kb_cache
        await invalidate_kb_cache(kb_id)

        return kb

    async def delete(self, kb_id: int, user_id: int) -> bool:
        """删除知识库（仅限所有者）

        级联删除：文档分块 → 文档 → 会话消息 → 会话 → 知识库
        """
        stmt = select(KnowledgeBase).where(
            and_(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.user_id == user_id,
            )
        )
        result = await self.db.execute(stmt)
        kb = result.scalars().first()

        if kb is None:
            return False

        from app.models.entities.document import Document, DocumentChunk
        from app.models.entities.conversation import Conversation, ChatMessageRecord

        # 1) 删除所有分块（通过 knowledge_base_id 直接关联）
        await self.db.execute(
            delete(DocumentChunk).where(DocumentChunk.knowledge_base_id == kb_id)
        )

        # 2) 删除所有文档
        await self.db.execute(
            delete(Document).where(Document.knowledge_base_id == kb_id)
        )

        # 3) 删除会话消息（先查会话 ID，再删消息）
        conv_stmt = select(Conversation).where(Conversation.knowledge_base_id == kb_id)
        conv_result = await self.db.execute(conv_stmt)
        conv_ids = [c.id for c in conv_result.scalars().all()]
        if conv_ids:
            await self.db.execute(
                delete(ChatMessageRecord).where(ChatMessageRecord.conversation_id.in_(conv_ids))
            )

        # 4) 删除会话
        await self.db.execute(
            delete(Conversation).where(Conversation.knowledge_base_id == kb_id)
        )

        # 5) 删除知识库本身
        await self.db.delete(kb)
        await self.db.commit()

        # 失效缓存
        from app.core.cache import invalidate_kb_cache
        await invalidate_kb_cache(kb_id)

        return True

    async def increment_documents(self, kb_id: int, delta: int = 1) -> None:
        """更新文档计数"""
        result = await self.db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb = result.scalars().first()
        if kb:
            kb.total_documents = max(0, kb.total_documents + delta)
            await self.db.commit()

    async def increment_chunks(self, kb_id: int, delta: int = 1) -> None:
        """更新分块计数"""
        result = await self.db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb = result.scalars().first()
        if kb:
            kb.total_chunks = max(0, kb.total_chunks + delta)
            await self.db.commit()
