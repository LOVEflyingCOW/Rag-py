from __future__ import annotations

from typing import List

from fastapi import APIRouter

from app.core.config import settings
from app.models.response import ApiResponse
from app.models.schemas import (
    ChatRequest,
    ChatMessageItem,
    ChatResponse,
    RetrievedChunkItem,
    LLMProviderInfo,
)
from app.processors import ChatMessage
from app.processors.embedding.embedding_service import EmbeddingService
from app.processors.retrieval.vector_store import VectorStoreManager
from app.services.chat_service import RAGPipeline

router = APIRouter(prefix="/chat", tags=["Chat"])


# ---------- 工具：懒加载 RAG Pipeline ----------
_cache = {}


def _get_pipeline() -> RAGPipeline:
    if "pipeline" not in _cache:
        _cache["pipeline"] = RAGPipeline(
            embedding=EmbeddingService(),
            vector_manager=VectorStoreManager(settings.VECTOR_STORE_DIR),
        )
    return _cache["pipeline"]


# ---------- 1) 主对话接口 ----------
@router.post("/message", response_model=ApiResponse[ChatResponse])
def chat_message(payload: ChatRequest):
    """RAG 对话 —— 单轮/多轮对话

    流程：
      1. 用户 query 向量化
      2. 从指定知识库向量搜索 top-k chunks
      3. 组装 system prompt（含幻觉抑制规则 + 上下文）
      4. 可选加入 history 对话
      5. 调 LLM 生成回答
    """
    pipeline = _get_pipeline()

    # 把请求中的 history 转为 ChatMessage 列表
    history: List[ChatMessage] = []
    if payload.history:
        for h in payload.history[-8:]:  # 最多保留最近 8 轮
            role = "assistant" if h.role == "assistant" else "user"
            history.append(ChatMessage(role=role, content=h.content))

    rag_result = pipeline.answer(
        knowledge_base_id=payload.knowledge_base_id,
        query_text=payload.message,
        history=history,
        top_k=payload.top_k,
        min_score=payload.min_score,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        debug_include_system_prompt=payload.include_raw,
    )

    retrieved_items = [
        RetrievedChunkItem(
            chunk_id=c.chunk_id,
            score=c.score,
            document_id=c.document_id,
            document_filename=c.document_filename,
            content=c.content if payload.include_raw else c.content[:200],
        )
        for c in rag_result.retrieved_chunks
    ]

    return ApiResponse[ChatResponse](
        data=ChatResponse(
            query=rag_result.query,
            answer=rag_result.llm_answer,
            model=rag_result.model,
            provider=rag_result.provider,
            success=rag_result.success,
            error=rag_result.error,
            latency_ms=round(rag_result.latency_ms, 1),
            retrieved_chunks=retrieved_items,
            system_prompt=rag_result.system_prompt if payload.include_raw else None,
        ),
    )


# ---------- 2) 仅搜索（不调用 LLM） ----------
@router.post("/search/{kb_id}", response_model=ApiResponse[dict])
def chat_search_only(kb_id: int, payload: dict):
    """仅做向量搜索 —— 返回 top-k chunks，供前端预览/调试"""
    query_text = (payload or {}).get("query_text", "")
    if not query_text:
        return ApiResponse[dict](success=False, code=400, message="query_text 不能为空")
    top_k = int((payload or {}).get("top_k", settings.RAG_TOP_K))
    min_score = float((payload or {}).get("min_score", settings.RAG_MIN_SCORE))

    pipeline = _get_pipeline()
    chunks = pipeline.search(kb_id, query_text, top_k=top_k, min_score=min_score)
    return ApiResponse[dict](data={
        "query": query_text,
        "hit_count": len(chunks),
        "items": [c.to_dict() for c in chunks],
    })


# ---------- 3) Provider 信息 ----------
@router.get("/provider", response_model=ApiResponse[LLMProviderInfo])
def get_provider():
    """查看当前 LLM provider / 模型信息"""
    from app.processors import get_llm_service
    llm = get_llm_service()
    provider_name = llm.provider_name()

    has_key = False
    if provider_name == "deepseek":
        has_key = bool(settings.DEEPSEEK_API_KEY)
    elif provider_name == "openai":
        has_key = bool(settings.OPENAI_API_KEY)
    elif provider_name == "custom":
        has_key = bool(settings.LLM_CUSTOM_API_KEY)
    else:
        has_key = False

    return ApiResponse[LLMProviderInfo](data=LLMProviderInfo(
        provider=provider_name,
        model=settings.DEEPSEEK_MODEL if provider_name == "deepseek" else (settings.OPENAI_MODEL if provider_name == "openai" else (settings.LLM_CUSTOM_MODEL if provider_name == "custom" else "mock-llm")),
        has_api_key=has_key,
        supported_providers=["mock", "deepseek", "openai", "custom"],
    ))


# ---------- 4) 快速健康检查 ----------
@router.get("/")
async def chat_root():
    """Chat API 根路径 —— 返回基本信息"""
    return {
        "service": "chat",
        "provider": settings.active_llm_name,
        "rag_top_k": settings.RAG_TOP_K,
        "rag_min_score": settings.RAG_MIN_SCORE,
        "endpoints": {
            "POST /chat/message": "RAG 对话",
            "POST /chat/search/{kb_id}": "仅向量搜索",
            "GET  /chat/provider": "当前 LLM provider 信息",
        },
    }