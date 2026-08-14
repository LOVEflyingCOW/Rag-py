from __future__ import annotations

"""外部渠道集成 API —— Shopify / 通用 Webhook 接入

核心端点:
  POST /integration/generic/{kb_id}/chat       通用 HTTP 客服（无需 token，适合自家前端）
  POST /integration/webhook/{token}            带签名 token 的 Webhook（适合 Shopify Webhook 等第三方）
  GET  /integration/generate-token/{kb_id}     生成指定渠道的 Webhook token（需要登录）

典型 Shopify 接入方式:
  方式 1 (简单): 商家用 App Proxy 把 /tools/chat-proxy 请求转发到
                 POST /integration/generic/{kb_id}/chat，响应里 message_html 可直接嵌入 Liquid
  方式 2 (标准): 在 Shopify 后台配置 Webhook 到 POST /integration/webhook/{token}
"""

from typing import Optional, Dict, Any
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Header
from pydantic import BaseModel, Field
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_dep, get_current_user_optional, CurrentUser
from app.processors.llm.llm_service import ChatMessage
from app.services.integration_service import (
    IntegrationService,
    InboundMessage,
    OutboundReply,
    CHANNEL_GENERIC,
    CHANNEL_SHOPIFY,
    VALID_CHANNELS,
)
from app.services.chat_service import RAGPipeline
from app.services.agent_service import AgentService
from app.processors import EmbeddingService, VectorStoreManager

router = APIRouter(prefix="/integration", tags=["Integration"])


# ---------- 请求/响应模型 ----------

class GenericChatRequest(BaseModel):
    """通用 Chat 请求（前端/Shopify App Proxy 可用）"""
    query: str
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    use_agent: bool = False           # True = 使用 Agent(多步推理), False = 普通 RAG
    max_turns: int = 2
    history: Optional[list] = None    # 备用：直接传历史对话
    metadata: Optional[Dict[str, Any]] = None


class GenerateTokenRequest(BaseModel):
    """生成 Webhook Token 请求"""
    channel: str = "generic_http"
    kb_id: int


# ---------- 1) 通用 HTTP Chat 入口（最简接入方式） ----------

@router.post("/generic/{kb_id}/chat")
async def generic_chat(
    kb_id: int,
    payload: GenericChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_dep),
    x_forwarded_for: Optional[str] = Header(default=None),
):
    """通用 HTTP 客服聊天接口 —— 推荐给 Shopify App Proxy 或自家前端直接调用

    响应包含:
      - plain: 纯文本回答
      - message_html: 可直接嵌入 Shopify 主题的 HTML（带来源列表）
      - sources: 引用的知识库 chunks
    """
    start = time.perf_counter()

    if not payload.query or not payload.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")

    # A) 校验 kb_id 是否存在
    from app.models.entities.knowledge_base import KnowledgeBase
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalars().first()
    if kb is None:
        raise HTTPException(status_code=404, detail=f"知识库 #{kb_id} 不存在")

    # B) 调用 RAG Pipeline 或 Agent
    try:
        if payload.use_agent:
            # Agent 模式（多步推理 + 工具调用）
            agent = AgentService(db)
            agent_result = await agent.run(
                query=payload.query,
                kb_id=kb_id,
                max_turns=payload.max_turns,
                history=payload.history,
            )
            if not agent_result.success:
                raise RuntimeError(agent_result.error or "Agent 执行失败")
            answer_text = agent_result.answer
            steps_for_output = [
                {
                    "thought": s.thought,
                    "action": s.action,
                    "action_input": s.action_input,
                    "observation": s.observation[:500],
                    "latency_ms": round(s.latency_ms, 1),
                }
                for s in agent_result.steps
            ]
            sources: list = []
        else:
            # 普通 RAG Pipeline（单次检索+生成）
            import tempfile as _tf
            _tmp_dir = _tf.mkdtemp(prefix="rag_integration_")
            rag = RAGPipeline(
                embedding=EmbeddingService(),
                vector_manager=VectorStoreManager(_tmp_dir),
            )
            history_msgs = [
                ChatMessage(role=h.get("role", "user"), content=h.get("content", ""))
                for h in (payload.history or [])
                if isinstance(h, dict) and h.get("role") in {"user", "assistant", "system"}
            ]
            rag_result = await rag.answer(
                knowledge_base_id=kb_id,
                query_text=payload.query,
                history=history_msgs,
                top_k=5,
                min_score=0.2,
            )
            if not rag_result.success:
                raise RuntimeError(rag_result.error or "RAG 回答失败")
            answer_text = rag_result.llm_answer
            steps_for_output = []
            sources = [c.to_dict() for c in (rag_result.retrieved_chunks or [])]
    except HTTPException:
        raise
    except Exception as exc:
        from app.core.logging import logger as _log
        _log.error("Integration generic_chat exception: %s", exc, exc_info=True)
        # 友好降级：返回一条默认提示，而不是 500
        answer_text = "抱歉，系统处理您的问题时出现了临时故障，请稍后重试。"
        sources = []
        steps_for_output = []

    latency = (time.perf_counter() - start) * 1000

    # C) 按渠道格式输出（这里默认 Shopify 风格，带 message_html 方便嵌入）
    reply = OutboundReply(
        answer_text=answer_text,
        sources=sources,
        agent_steps=steps_for_output,
        latency_ms=latency,
        raw_context={
            "kb_id": kb_id,
            "kb_title": kb.name if kb else "",
            "channel": CHANNEL_GENERIC,
            "remote_ip": x_forwarded_for or (request.client.host if request.client else ""),
        },
    )
    integration = IntegrationService(db)
    return integration.render_reply_for_channel(CHANNEL_SHOPIFY, reply)


# ---------- 2) 签名 Webhook 入口（适合 Shopify 后台 Webhook 配置） ----------

@router.post("/webhook/{token}")
async def webhook_entry(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db_dep),
    content_type: Optional[str] = Header(default=None),
    x_shopify_topic: Optional[str] = Header(default=None),
    x_shopify_hmac_sha256: Optional[str] = Header(default=None),
):
    """带签名 Token 的 Webhook 接入点

    用法:
      1. 先调用 GET /integration/generate-token/{kb_id}?channel=shopify 获得 token
      2. 在 Shopify 后台 → Settings → Notifications → Webhooks
         填入:  POST https://<你的域名>/api/v1/integration/webhook/{token}
      3. Shopify 一有消息就 POST 到这里，我们返回 JSON 回答
    """
    # A) 校验 token
    parsed = IntegrationService.verify_webhook_token(token)
    if not parsed:
        raise HTTPException(status_code=401, detail="无效或过期的 webhook token")
    channel_from_token, kb_id = parsed
    headers_dict = {
        "content-type": content_type or "",
        "x-shopify-topic": x_shopify_topic or "",
        "x-shopify-hmac-sha256": x_shopify_hmac_sha256 or "",
    }

    # B) 解析请求体（JSON / form）
    try:
        raw_body = await request.body()
        if raw_body:
            try:
                payload = json.loads(raw_body.decode("utf-8", errors="ignore"))
            except Exception:
                payload = {"raw": raw_body.decode("utf-8", errors="ignore")[:2000]}
        else:
            payload = {}
    except Exception:
        payload = {}

    # C) 根据渠道选择解析器
    integration = IntegrationService(db)
    if channel_from_token == CHANNEL_SHOPIFY or x_shopify_topic:
        msg = integration.parse_shopify_webhook(payload, headers_dict, kb_id)
        channel_used = CHANNEL_SHOPIFY
    else:
        msg = integration.parse_generic_http(payload, kb_id)
        channel_used = CHANNEL_GENERIC

    if msg is None:
        return {"ok": False, "answer": "", "_debug": "无法从请求体中提取客户消息"}

    # D) 调用 RAG（走 RAGPipeline）
    start = time.perf_counter()
    try:
        import tempfile as _tf2
        _tmp_dir = _tf2.mkdtemp(prefix="rag_webhook_")
        rag = RAGPipeline(
            embedding=EmbeddingService(),
            vector_manager=VectorStoreManager(_tmp_dir),
        )
        result = await rag.answer(
            knowledge_base_id=kb_id,
            query_text=msg.query_text,
            history=[],
            top_k=5,
            min_score=0.2,
        )
        answer_text = result.llm_answer if result.success else "抱歉，我暂时无法回答这个问题。"
        sources = [c.to_dict() for c in (result.retrieved_chunks or [])]
    except Exception as exc:
        _get_logger().error("Webhook RAG 异常: %s", exc, exc_info=True)
        answer_text = "系统处理中出现临时问题，请稍后再试。"
        sources = []

    latency = (time.perf_counter() - start) * 1000
    reply = OutboundReply(
        answer_text=answer_text,
        sources=sources,
        latency_ms=latency,
        raw_context={
            "kb_id": kb_id,
            "external_user": msg.external_user_id,
            "external_conv": msg.external_conversation_id,
        },
    )
    return integration.render_reply_for_channel(channel_used, reply)


# ---------- 3) 生成 Webhook Token ----------

@router.get("/generate-token/{kb_id}")
async def generate_webhook_token_endpoint(
    kb_id: int,
    channel: str = "generic_http",
    db: AsyncSession = Depends(get_db_dep),
    user: Optional[CurrentUser] = Depends(get_current_user_optional),
):
    """生成 Webhook 访问 Token（可选校验用户是否有该知识库权限）"""
    if channel not in VALID_CHANNELS:
        raise HTTPException(status_code=400, detail=f"channel 必须是 {sorted(VALID_CHANNELS)} 之一")
    from app.models.entities.knowledge_base import KnowledgeBase
    result = await db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
    kb = result.scalars().first()
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    # 可选的权限校验（如果 user 存在且非管理员，检查是否 owner）
    if user and not user.is_admin:
        if kb.user_id != user.user_id:
            raise HTTPException(status_code=403, detail="无权为该知识库生成 token")
    token = IntegrationService.generate_webhook_token(channel, kb_id)
    return {
        "ok": True,
        "channel": channel,
        "kb_id": kb_id,
        "webhook_url": f"/api/v1/integration/webhook/{token}",
        "token": token,
        "shopify_instructions": {
            "步骤1": "Shopify 后台 → Settings → Notifications → Webhooks → Create webhook",
            "步骤2": "Event 选: Order creation / 其他需要的事件（或使用 App Proxy 模式）",
            "步骤3": "URL 填入: https://<你的域名>/api/v1/integration/webhook/" + token,
            "步骤4": "保存，Shopify 会发 test webhook，应返回 ok=True 的 JSON",
            "说明": "推荐同时使用 通用 Chat 接口（generic/{kb_id}/chat），配合 App Proxy 嵌入到店铺页面实时聊天。",
        },
    }


# ---------- 4) 根路径 ----------

@router.get("/")
async def integration_root():
    return {
        "service": "integration",
        "description": "外部渠道集成（Shopify / 通用 HTTP Webhook / 微信等）",
        "endpoints": {
            "POST /integration/generic/{kb_id}/chat": "通用 HTTP 聊天（推荐 Shopify App Proxy + 自家前端）",
            "POST /integration/webhook/{token}": "带签名的 Webhook 入口（Shopify 后台配置）",
            "GET  /integration/generate-token/{kb_id}?channel=shopify|generic_http": "生成 webhook token",
        },
        "channels": sorted(VALID_CHANNELS),
    }


# ---------- 工具 ----------
def _get_logger():
    from app.core.logging import logger
    return logger
