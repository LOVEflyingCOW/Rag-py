from __future__ import annotations

"""聊天服务

核心分层：
  1) RAGPipeline     - 纯逻辑层，不依赖数据库 (可测试)
                       query → embedding → vector search → 组装上下文 → LLM
  2) ChatService     - 业务层，基于 RAGPipeline + DB，管理会话与消息持久化

主要特性：
  - 幻觉抑制：未检索到足够上下文时，明确告知"无相关信息"而非编造
  - 上下文引用：要求 LLM 回答必须标记 chunk 引用
  - 多轮上下文：保留最近 N 轮对话历史
  - Provider 切换：运行时可动态切换 LLM provider
"""

import json
import time
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging import logger as _logger
from app.processors import get_llm_service, ChatMessage, ChatResult
from app.processors.embedding.embedding_service import EmbeddingService
from app.processors.retrieval.vector_store import VectorStoreManager


# ============ 数据结构 ============

@dataclass
class RetrievedChunk:
    """检索到的一条 chunk"""
    chunk_id: int
    content: str
    score: float
    document_id: Optional[int] = None
    document_filename: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "score": float(self.score),
            "document_id": self.document_id,
            "document_filename": self.document_filename,
            "content": self.content[:500] + "..." if len(self.content) > 500 else self.content,
        }


@dataclass
class RAGPipelineResult:
    """一次 RAG 调用的完整结果"""
    query: str
    retrieved_chunks: List[RetrievedChunk] = field(default_factory=list)
    llm_answer: str = ""
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None
    system_prompt: Optional[str] = None  # 调试用

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "answer": self.llm_answer,
            "model": self.model,
            "provider": self.provider,
            "success": self.success,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 1),
            "retrieved_chunks": [c.to_dict() for c in self.retrieved_chunks],
        }


# ============ RAG Pipeline (纯逻辑，无 DB 依赖) ============

# --- 幻觉抑制用的 System Prompt 模板 ---
SYSTEM_PROMPT_TEMPLATE = """你是一个基于知识库的智能问答助手。你的任务是：

1. 只能根据下面提供的【知识库片段】回答问题，不要编造或推测知识库以外的内容。
2. 如果知识库中没有相关信息，或信息不足以回答，必须明确说：
   "很抱歉，在知识库中未能检索到足够的相关信息来回答您的问题。"
3. 在回答中用 [来源 #N] 格式标注引用的片段编号（N=1,2,3...）。
4. 回答要简洁、准确、中文优先。

---
【知识库片段】
{context_text}
---

现在请根据以上知识库片段回答用户问题。"""


class RAGPipeline:
    """RAG 流水线：query → 向量搜索 → 上下文组装 → LLM 生成"""

    def __init__(
        self,
        embedding: Optional[EmbeddingService] = None,
        vector_manager: Optional[VectorStoreManager] = None,
    ):
        self.embedding = embedding or EmbeddingService()
        self.vectors = vector_manager or VectorStoreManager(settings.VECTOR_STORE_DIR)
        self.llm = get_llm_service()

    # ---------- 1) 检索 ----------
    def search(
        self,
        knowledge_base_id: int,
        query_text: str,
        *,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
    ) -> List[RetrievedChunk]:
        """向量搜索 —— 返回按分数排序的 chunks"""
        top_k = top_k or settings.RAG_TOP_K
        min_score = min_score if min_score is not None else settings.RAG_MIN_SCORE

        store = self.vectors.get_store(knowledge_base_id, dim=self.embedding.dim)
        if store.total() == 0:
            return []

        query_vec = self.embedding.encode_single(query_text)
        raw_results = store.search(query_vec, top_k=top_k * 2)  # 多取一些再过滤

        chunks = []
        for item in raw_results:
            if item["score"] < min_score:
                continue
            meta = item.get("metadata", {}) or {}
            chunk = RetrievedChunk(
                chunk_id=int(meta.get("chunk_id", item.get("index", 0))),
                content=meta.get("content", item.get("content", "")),
                score=float(item["score"]),
                document_id=meta.get("document_id"),
                document_filename=meta.get("document_filename"),
            )
            if chunk.content.strip():
                chunks.append(chunk)

        return chunks[:top_k]

    # ---------- 2) 上下文组装 ----------
    def build_context(self, chunks: List[RetrievedChunk], max_chars: Optional[int] = None) -> str:
        """把 chunks 拼成一段 context 文本（带编号，供 LLM 引用）"""
        max_chars = max_chars or settings.RAG_MAX_CONTEXT_CHARS
        if not chunks:
            return "(知识库中无相关内容)"

        lines = []
        total = 0
        for i, c in enumerate(chunks, start=1):
            seg = "[#%d] (来源: %s, 相似度: %.3f)\n%s" % (
                i, c.document_filename or ("文档 #%s" % (c.document_id or "?")), c.score, c.content
            )
            if total + len(seg) > max_chars and lines:
                break
            lines.append(seg)
            total += len(seg)
        return "\n\n".join(lines)

    # ---------- 3) 组装发给 LLM 的 messages ----------
    def build_messages(
        self,
        query: str,
        context_text: str,
        *,
        history: Optional[List[ChatMessage]] = None,
    ) -> List[ChatMessage]:
        """组装 messages 列表：[system, history..., user]

        幻觉抑制关键点：当无 context 时，明确要求 LLM 不回答。
        """
        has_context = bool(context_text and context_text != "(知识库中无相关内容)")

        if has_context:
            system_content = SYSTEM_PROMPT_TEMPLATE.format(context_text=context_text)
        else:
            system_content = (
                "你是一个基于知识库的智能问答助手。"
                "本次查询在知识库中未能检索到任何相关片段。"
                "你必须直接回复用户："
                '"很抱歉，在知识库中未能检索到足够的相关信息来回答您的问题。"'
                "绝不能编造任何回答。"
            )

        messages: List[ChatMessage] = [ChatMessage(role="system", content=system_content)]

        if history:
            for h in history:
                messages.append(ChatMessage(role=h.role, content=h.content))

        messages.append(ChatMessage(role="user", content=query))
        return messages

    # ---------- 4) 端到端调用 ----------
    def answer(
        self,
        knowledge_base_id: int,
        query_text: str,
        *,
        history: Optional[List[ChatMessage]] = None,
        top_k: Optional[int] = None,
        min_score: Optional[float] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        debug_include_system_prompt: bool = False,
    ) -> RAGPipelineResult:
        """RAG 端到端回答

        幻觉抑制分层策略：
          L1. 知识库为空 / 无任何 chunks → 直接拒绝
          L2. 最高 chunks 分数 < 拒绝阈值 → 不相关，拒绝
          L3. LLM 层面（system prompt + provider 自身规则）
        """
        start = time.perf_counter()
        result = RAGPipelineResult(query=query_text, provider=self.llm.provider_name())

        # 拒绝回答的分数阈值（低于此值视为"不够相关"）
        REJECT_SCORE_THRESHOLD = 0.42  # 独立于 min_score，用于"有 chunks 但不相关"判断

        try:
            # 1) 检索
            chunks = self.search(knowledge_base_id, query_text, top_k=top_k, min_score=min_score)
            result.retrieved_chunks = chunks

            max_score = max((c.score for c in chunks), default=0.0)

            # --- L1 + L2 幻觉抑制：无 chunks 或最高分数过低 → 直接拒绝 ---
            if not chunks or max_score < REJECT_SCORE_THRESHOLD:
                result.llm_answer = "很抱歉，在知识库中未能检索到足够的相关信息来回答您的问题。"
                result.model = "mock-hallucination-filter"
                result.success = True
                result.latency_ms = (time.perf_counter() - start) * 1000
                # 即使拒绝，也保留检索到的 chunks 信息（供调试）
                return result

            # 2) 上下文
            ctx = self.build_context(chunks)

            # 3) messages
            messages = self.build_messages(query_text, ctx, history=history)
            if debug_include_system_prompt:
                result.system_prompt = messages[0].content

            # 4) LLM 生成
            llm_result: ChatResult = self.llm.chat(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

            result.llm_answer = llm_result.content
            result.model = llm_result.model
            result.success = llm_result.success
            result.error = llm_result.error
            result.latency_ms = (time.perf_counter() - start) * 1000

            # --- L3 幻觉抑制：有 chunks 但 LLM 返回为空或无意义 ---
            if not result.llm_answer or result.llm_answer.strip() == "":
                result.llm_answer = "很抱歉，在知识库中未能检索到足够的相关信息来回答您的问题。"

        except Exception as exc:
            result.success = False
            result.error = "RAG pipeline 异常 (%s): %s" % (type(exc).__name__, str(exc))
            result.latency_ms = (time.perf_counter() - start) * 1000
            _logger.exception("RAG pipeline 异常: %s", exc)

        return result