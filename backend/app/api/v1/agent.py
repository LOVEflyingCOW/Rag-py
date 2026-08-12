from __future__ import annotations

from typing import List, Optional, Dict, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import get_db_dep, get_current_user_optional
from app.models.entities.user import User
from sqlalchemy.orm import Session

from app.services.agent_service import AgentService

router = APIRouter(prefix="/agent", tags=["Agent"])


# ---------- 请求/响应模型 ----------

class AgentQueryRequest(BaseModel):
    """Agent 查询请求"""
    query: str
    knowledge_base_id: Optional[int] = None
    max_turns: int = 3
    include_raw_steps: bool = False
    history: Optional[List[Dict[str, Any]]] = None


class AgentStepItem(BaseModel):
    """单步推理"""
    thought: str
    action: str
    action_input: str
    observation: str
    latency_ms: float


class AgentResponse(BaseModel):
    """Agent 响应"""
    success: bool
    answer: str
    steps: List[AgentStepItem] = []
    latency_ms: float = 0.0
    error: Optional[str] = None


# ---------- 1) 主接口：Agent 推理 ----------

@router.post("/run", response_model=AgentResponse)
def agent_run(
    payload: AgentQueryRequest,
    db: Session = Depends(get_db_dep),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """执行 Agent 推理循环 —— 通过 ReAct 风格查询知识库并回答用户问题

    - query: 用户问题（中文 / 英文均可）
    - knowledge_base_id: 目标知识库 ID（必须）
    - max_turns: 最大工具调用次数（默认 3）
    - history: 可选对话历史，格式: [{\"role\": \"user\"|\"assistant\", \"content\": \"...\"}]
    """
    if not payload.knowledge_base_id:
        raise HTTPException(status_code=400, detail="knowledge_base_id 不能为空")

    agent = AgentService(db)
    result = agent.run(
        query=payload.query,
        kb_id=payload.knowledge_base_id,
        user_id=user.id if user else None,
        max_turns=payload.max_turns,
        history=payload.history,
        include_raw_steps=payload.include_raw_steps,
    )

    return AgentResponse(
        success=result.success,
        answer=result.answer,
        steps=[
            AgentStepItem(
                thought=s.thought,
                action=s.action,
                action_input=s.action_input,
                observation=s.observation,
                latency_ms=round(s.latency_ms, 1),
            )
            for s in result.steps
        ],
        latency_ms=round(result.latency_ms, 1),
        error=result.error,
    )


# ---------- 2) 工具列表（供前端调试/展示） ----------

@router.get("/tools")
def agent_tools(db: Session = Depends(get_db_dep)):
    """返回当前 Agent 可用的工具列表"""
    agent = AgentService(db)
    return {
        "tools": [
            {
                "name": t.name,
                "description": t.description,
            }
            for t in agent.tools.values()
        ]
    }


# ---------- 3) 根路径 ----------

@router.get("/")
async def agent_root():
    """Agent API 根路径"""
    return {
        "service": "agent",
        "description": "基于 ReAct 推理的智能客服 Agent，可调用知识库搜索、文档查询等工具。",
        "endpoints": {
            "POST /agent/run": "执行一次 Agent 推理对话",
            "GET  /agent/tools": "查看可用工具",
        },
    }