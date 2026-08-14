"""Conversation API - 对话会话历史管理"""
from __future__ import annotations

from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.models.response import ApiResponse
from app.api.dependencies import get_current_user_optional, get_db_dep, CurrentUser

router = APIRouter(prefix="/conversation", tags=["Conversation"])


# --- Pydantic Schemas ---

class ConversationCreate(BaseModel):
    """创建会话"""
    title: Optional[str] = "新对话"
    knowledge_base_id: Optional[int] = None


class ConversationItem(BaseModel):
    """会话列表项"""
    id: int
    title: str
    knowledge_base_id: Optional[int]
    message_count: int = 0
    updated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class MessageItem(BaseModel):
    """单条消息"""
    id: int
    role: str
    content: str
    metadata: Optional[dict] = None
    created_at: Optional[datetime] = None


@router.get("", response_model=ApiResponse[List[ConversationItem]])
async def list_conversations(
    user: Optional[CurrentUser] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db_dep),
):
    """列出当前用户的所有会话"""
    if user is None:
        return ApiResponse[List[ConversationItem]](success=False, code=401, message="请先登录")

    from app.services.conversation_service import ConversationService
    service = ConversationService(db)
    raw = await service.list_conversations(user.user_id)
    items = [
        ConversationItem(
            id=r["id"],
            title=r["title"],
            knowledge_base_id=r["knowledge_base_id"],
            message_count=r.get("message_count", 0),
            created_at=r.get("created_at"),
            updated_at=r.get("updated_at"),
        )
        for r in raw
    ]
    return ApiResponse[List[ConversationItem]](data=items)


@router.post("", response_model=ApiResponse[ConversationItem])
async def create_conversation(
    payload: ConversationCreate,
    user: Optional[CurrentUser] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db_dep),
):
    """创建一个新会话"""
    if user is None:
        return ApiResponse[ConversationItem](success=False, code=401, message="请先登录")

    from app.services.conversation_service import ConversationService
    service = ConversationService(db)
    conv = await service.create_conversation(
        user_id=user.user_id,
        title=payload.title or "新对话",
        knowledge_base_id=payload.knowledge_base_id,
    )
    return ApiResponse[ConversationItem](
        data=ConversationItem(
            id=conv.id,
            title=conv.title,
            knowledge_base_id=conv.knowledge_base_id,
            message_count=0,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )
    )


@router.get("/{conv_id}", response_model=ApiResponse[List[MessageItem]])
async def get_messages(
    conv_id: int,
    user: Optional[CurrentUser] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db_dep),
):
    """获取一个会话的完整消息历史"""
    if user is None:
        return ApiResponse[List[MessageItem]](success=False, code=401, message="请先登录")

    from app.services.conversation_service import ConversationService
    service = ConversationService(db)

    # 先校验归属
    conv = await service.get_conversation(conv_id, user.user_id)
    if conv is None:
        return ApiResponse[List[MessageItem]](success=False, code=404, message="会话不存在或无权访问")

    raw = await service.get_messages(conv_id, user.user_id)
    items = [
        MessageItem(
            id=m["id"],
            role=m["role"],
            content=m["content"],
            metadata=m.get("metadata"),
            created_at=m.get("created_at"),
        )
        for m in raw
    ]
    return ApiResponse[List[MessageItem]](data=items)


@router.delete("/{conv_id}", response_model=ApiResponse[bool])
async def delete_conversation(
    conv_id: int,
    user: Optional[CurrentUser] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db_dep),
):
    """删除一个会话及其所有消息"""
    if user is None:
        return ApiResponse[bool](success=False, code=401, message="请先登录")

    from app.services.conversation_service import ConversationService
    service = ConversationService(db)
    ok = await service.delete_conversation(conv_id, user.user_id)
    return ApiResponse[bool](
        data=ok,
        success=ok,
        code=200 if ok else 404,
        message="已删除" if ok else "会话不存在或无权删除",
    )


__all__ = ["router"]
