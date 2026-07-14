from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/agent", tags=["Agent"])


@router.get("/")
async def agent_root():
    """Agent接口根路径 - Day 10+ 实现"""
    return {"message": "Agent API - coming in Day 10+"}