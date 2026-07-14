from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/documents", tags=["Document"])


@router.get("/")
async def list_documents():
    """获取文档列表 - Day 3 实现"""
    return {"message": "Document API - coming in Day 3"}