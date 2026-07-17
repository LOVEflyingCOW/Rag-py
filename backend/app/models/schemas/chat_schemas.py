from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


# ============ Chat 请求 ============

class ChatMessageItem(BaseModel):
    """单条对话消息"""
    role: str = Field(..., description="system | user | assistant")
    content: str = Field(..., min_length=1, max_length=8000, description="消息内容")


class ChatRequest(BaseModel):
    """对话请求 —— 单轮 / 多轮通用

    knowledge_base_id : 指定查询的知识库
    message           : 本轮用户提问
    history           : 可选，最近若干轮对话（用于多轮上下文）
    top_k             : 可选，覆盖全局 RAG_TOP_K
    min_score         : 可选，覆盖全局 RAG_MIN_SCORE
    temperature       : 可选，L2 温度
    max_tokens        : 可选，最大生成长度
    include_raw       : 是否在响应中包含原始检索/系统 prompt（调试用）
    """
    knowledge_base_id: int = Field(..., ge=1)
    message: str = Field(..., min_length=1, max_length=2000)
    history: Optional[List[ChatMessageItem]] = None
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    min_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1, le=4096)
    include_raw: bool = False


class RetrievedChunkItem(BaseModel):
    """检索结果片段（在响应中返回）"""
    chunk_id: int
    score: float
    document_id: Optional[int] = None
    document_filename: Optional[str] = None
    content: Optional[str] = None  # 可选返回片段内容


class ChatResponse(BaseModel):
    """对话响应

    主要字段：
      - answer  : LLM 最终回答
      - model / provider : 实际使用的模型
      - retrieved_chunks : 本次检索到的证据片段
      - latency_ms : 总耗时（ms）
      - system_prompt : 调试模式下返回的 prompt
    """
    query: str
    answer: str
    model: str
    provider: str
    success: bool = True
    error: Optional[str] = None
    latency_ms: float = 0.0
    retrieved_chunks: List[RetrievedChunkItem] = Field(default_factory=list)
    system_prompt: Optional[str] = None  # 仅调试模式返回


class LLMProviderInfo(BaseModel):
    """当前 LLM provider 信息"""
    provider: str
    model: str
    has_api_key: bool
    supported_providers: List[str]