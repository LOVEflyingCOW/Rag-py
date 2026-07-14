from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_dep, get_current_user, get_current_user_optional
from app.models.entities.user import User
from app.models.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeBaseInfo,
    KnowledgeBaseListResponse,
)
from app.services.kb_service import KnowledgeBaseService
from app.models.response import ApiResponse


router = APIRouter(prefix="/knowledge-bases", tags=["知识库"])


@router.post("", response_model=ApiResponse[KnowledgeBaseInfo])
def create_kb(
    payload: KnowledgeBaseCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
):
    """创建知识库（需要登录）"""
    service = KnowledgeBaseService(db)
    kb = service.create(payload, user_id=current_user.id)
    return ApiResponse[KnowledgeBaseInfo](data=KnowledgeBaseInfo.from_orm(kb))


@router.get("", response_model=ApiResponse[KnowledgeBaseListResponse])
def list_kbs(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db_dep),
):
    """获取知识库列表

    - 未登录：仅返回公开的知识库
    - 已登录：返回自己的 + 公开的知识库
    """
    user_id = current_user.id if current_user else None
    service = KnowledgeBaseService(db)
    items, total = service.list(user_id=user_id, page=page, page_size=page_size, keyword=keyword)

    data = KnowledgeBaseListResponse(
        items=[KnowledgeBaseInfo.from_orm(kb) for kb in items],
        total=total,
        page=page,
        page_size=page_size,
    )
    return ApiResponse[KnowledgeBaseListResponse](data=data)


@router.get("/{kb_id}", response_model=ApiResponse[KnowledgeBaseInfo])
def get_kb(
    kb_id: int,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db_dep),
):
    """获取单个知识库详情"""
    user_id = current_user.id if current_user else None
    service = KnowledgeBaseService(db)
    kb = service.get_by_id(kb_id, user_id=user_id)

    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在或无权访问",
        )

    return ApiResponse[KnowledgeBaseInfo](data=KnowledgeBaseInfo.from_orm(kb))


@router.put("/{kb_id}", response_model=ApiResponse[KnowledgeBaseInfo])
def update_kb(
    kb_id: int,
    payload: KnowledgeBaseUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
):
    """更新知识库（仅限所有者）"""
    service = KnowledgeBaseService(db)
    kb = service.update(kb_id, payload, user_id=current_user.id)

    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在或无权修改",
        )

    return ApiResponse[KnowledgeBaseInfo](data=KnowledgeBaseInfo.from_orm(kb))


@router.delete("/{kb_id}", response_model=ApiResponse[dict])
def delete_kb(
    kb_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
):
    """删除知识库（仅限所有者）"""
    service = KnowledgeBaseService(db)
    success = service.delete(kb_id, user_id=current_user.id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在或无权删除",
        )

    return ApiResponse[dict](data={"message": "删除成功", "kb_id": kb_id})