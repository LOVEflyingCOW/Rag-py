from __future__ import annotations

"""对话服务 —— 持久化用户的多轮对话历史

说明:
- Conversation: 一次"对话"容器（类似会话）
- ChatMessageRecord: 单条消息（user / assistant / system）
- 为 RAG 提供历史上下文 + 后续检索优化依据
"""

from typing import List, Optional, Dict, Any
from datetime import datetime
import json

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.models.entities.conversation import Conversation, ChatMessageRecord as Message
from app.processors import ChatMessage as LlmChatMessage


class ConversationService:
    """对话历史服务"""

    MAX_MESSAGES_PER_CONVERSATION = 200
    MAX_CONVERSATIONS_PER_USER = 50

    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------- 会话: 创建 / 查询 ----------
    async def create_conversation(
        self,
        user_id: int,
        title: str = "新对话",
        knowledge_base_id: Optional[int] = None,
    ) -> Conversation:
        """创建一个新的对话会话"""
        # 超限清理：删除最旧的
        result = await self.db.execute(
            select(func.count()).select_from(Conversation).where(Conversation.user_id == user_id)
        )
        total = result.scalar()
        if total >= self.MAX_CONVERSATIONS_PER_USER:
            result = await self.db.execute(
                select(Conversation)
                .where(Conversation.user_id == user_id)
                .order_by(Conversation.updated_at.asc())
            )
            oldest = result.scalars().first()
            if oldest:
                await self.db.execute(
                    delete(Message).where(Message.conversation_id == oldest.id)
                )
                await self.db.delete(oldest)
                await self.db.commit()
                logger.info("ConversationService: 清理用户 %d 最旧的会话 %d", user_id, oldest.id)

        conv = Conversation(
            user_id=user_id,
            title=title[:80] or "新对话",
            knowledge_base_id=knowledge_base_id,
        )
        self.db.add(conv)
        await self.db.commit()
        await self.db.refresh(conv)
        return conv

    async def list_conversations(self, user_id: int) -> List[Dict[str, Any]]:
        """列出用户的所有会话（按更新时间倒序）"""
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
        convs = result.scalars().all()
        out: List[Dict[str, Any]] = []
        for c in convs:
            cnt_result = await self.db.execute(
                select(func.count()).select_from(Message).where(Message.conversation_id == c.id)
            )
            msg_count = cnt_result.scalar()
            out.append({
                "id": c.id,
                "title": c.title,
                "knowledge_base_id": c.knowledge_base_id,
                "created_at": c.created_at,
                "updated_at": c.updated_at,
                "message_count": msg_count,
            })
        return out

    async def get_conversation(self, conv_id: int, user_id: Optional[int]) -> Optional[Conversation]:
        """获取单个会话（带所属校验；user_id=None 表示已在外部校验过）"""
        result = await self.db.execute(
            select(Conversation).where(Conversation.id == conv_id)
        )
        conv = result.scalars().first()
        if conv is None:
            return None
        if user_id is not None and conv.user_id != user_id:
            return None
        return conv

    async def rename_conversation(self, conv_id: int, user_id: int, new_title: str) -> Optional[Conversation]:
        conv = await self.get_conversation(conv_id, user_id)
        if conv is None:
            return None
        conv.title = (new_title or "")[:80] or "新对话"
        conv.updated_at = datetime.utcnow()
        await self.db.commit()
        await self.db.refresh(conv)
        return conv

    async def delete_conversation(self, conv_id: int, user_id: int) -> bool:
        conv = await self.get_conversation(conv_id, user_id)
        if conv is None:
            return False
        await self.db.execute(delete(Message).where(Message.conversation_id == conv.id))
        await self.db.delete(conv)
        await self.db.commit()
        return True

    # ---------- 消息: 追加 / 查询 ----------
    async def append_user_message(self, conv_id: int, content: str, user_id: int,
                          extra: Optional[Dict[str, Any]] = None) -> Optional[Message]:
        """追加一条 user 消息"""
        conv = await self.get_conversation(conv_id, user_id)
        if conv is None:
            return None
        msg = await self._append_message(
            conv_id=conv.id, role="user", content=content, extra=extra)
        conv.updated_at = datetime.utcnow()
        cnt_result = await self.db.execute(
            select(func.count()).select_from(Message).where(Message.conversation_id == conv.id)
        )
        msg_count = cnt_result.scalar()
        if msg_count <= 2 and (conv.title in ("新对话", "New Conversation", None, "")):
            conv.title = content[:30]
        await self.db.commit()
        return msg

    async def append_assistant_message(
        self,
        conv_id: int,
        content: str,
        user_id: int,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Optional[Message]:
        """追加一条 assistant 消息"""
        conv = await self.get_conversation(conv_id, user_id)
        if conv is None:
            return None
        msg = await self._append_message(conv_id=conv.id, role="assistant",
                                   content=content, extra=extra)
        conv.updated_at = datetime.utcnow()
        await self.db.commit()
        return msg

    async def _append_message(self, conv_id: int, role: str, content: str,
                        extra: Optional[Dict[str, Any]] = None) -> Message:
        msg = Message(
            conversation_id=conv_id,
            role=role,
            content=content,
            retrieved_contexts=json.dumps(extra or {}, ensure_ascii=False) if extra else None,
        )
        self.db.add(msg)

        # 超限：删除最早的超出部分
        cnt_result = await self.db.execute(
            select(func.count()).select_from(Message).where(Message.conversation_id == conv_id)
        )
        total = cnt_result.scalar()
        if total > self.MAX_MESSAGES_PER_CONVERSATION:
            result = await self.db.execute(
                select(Message)
                .where(Message.conversation_id == conv_id)
                .order_by(Message.id.asc())
                .limit(total - self.MAX_MESSAGES_PER_CONVERSATION)
            )
            to_remove = result.scalars().all()
            for m in to_remove:
                await self.db.delete(m)

        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(msg)
        return msg

    async def get_messages(self, conv_id: int, user_id: int,
                    limit: int = 50) -> List[Dict[str, Any]]:
        """获取一个会话的消息列表（最新的在末尾）"""
        conv = await self.get_conversation(conv_id, user_id)
        if conv is None:
            return []
        result = await self.db.execute(
            select(Message)
            .where(Message.conversation_id == conv.id)
            .order_by(Message.id.asc())
            .limit(limit)
        )
        msgs = result.scalars().all()
        out: List[Dict[str, Any]] = []
        for m in msgs:
            try:
                meta = json.loads(m.retrieved_contexts) if m.retrieved_contexts else {}
            except Exception:
                meta = {}
            out.append({
                "id": m.id,
                "conversation_id": m.conversation_id,
                "role": m.role,
                "content": m.content,
                "metadata": meta,
                "created_at": m.created_at,
            })
        return out

    async def get_llm_context(
        self,
        conv_id: int,
        user_id: int,
        max_turns: int = 6,
    ) -> List[LlmChatMessage]:
        """获取给 LLM 作为上下文的最近若干轮对话"""
        raw = await self.get_messages(conv_id, user_id, limit=max_turns * 2)
        ctx: List[LlmChatMessage] = []
        for m in raw:
            if m["role"] in ("user", "assistant", "system"):
                ctx.append(LlmChatMessage(role=m["role"], content=m["content"]))
        return ctx[-max_turns * 2:]


__all__ = ["ConversationService"]
