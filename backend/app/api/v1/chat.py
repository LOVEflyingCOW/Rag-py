from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/chat", tags=["Chat"])


@router.get("/")
async def chat_root():
    """对话接口根路径 - Day 6-7 实现"""
    return {"message": "Chat API - coming in Day 6-7"}