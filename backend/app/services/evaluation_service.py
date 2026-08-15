"""RAG 评估模块 — RAGEvaluator

设计:
1. 评估 RAG 检索质量: 召回率、准确率、MRR、延迟
2. 评估生成质量: 关键词命中率、答案相关性
3. 支持批量评估, 输出结构化报告
4. 与 RetrievalService 和 LLMService 集成

评估指标:
  - Recall@K:    前 K 个结果中包含正确文档的比例
  - Precision@K: 前 K 个结果中正确文档的占比
  - MRR:         平均倒数排名 (Mean Reciprocal Rank)
  - Latency:     检索/生成延迟 (ms)
  - Keyword Hit: LLM 答案中包含预期关键词的比例

使用:
    evaluator = RAGEvaluator(db)
    report = await evaluator.evaluate(
        kb_id=1,
        test_cases=[
            {"query": "什么是RAG", "expected_keywords": ["检索", "生成"], "expected_doc_ids": [1]},
        ],
    )
"""

from __future__ import annotations

import time
import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from app.core.logging import logger
from app.services.retrieval_service import RetrievalService


@dataclass
class TestCase:
    """评估测试用例"""
    query: str
    expected_keywords: List[str] = field(default_factory=list)
    expected_doc_ids: List[int] = field(default_factory=list)
    expected_source: str = ""  # 预期文件名
    description: str = ""


@dataclass
class QueryResult:
    """单条查询评估结果"""
    query: str
    retrieved_chunks: List[Dict[str, Any]] = field(default_factory=list)
    retrieved_doc_ids: List[int] = field(default_factory=list)
    llm_answer: str = ""
    keyword_hits: List[str] = field(default_factory=list)
    keyword_hit_rate: float = 0.0
    recall_at_k: float = 0.0
    precision_at_k: float = 0.0
    mrr: float = 0.0  # Mean Reciprocal Rank
    retrieval_latency_ms: int = 0
    llm_latency_ms: int = 0
    total_latency_ms: int = 0


@dataclass
class EvaluationReport:
    """评估报告"""
    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0

    # 检索质量
    avg_recall_at_k: float = 0.0
    avg_precision_at_k: float = 0.0
    avg_mrr: float = 0.0
    avg_retrieval_latency_ms: float = 0.0

    # 生成质量
    avg_keyword_hit_rate: float = 0.0
    avg_llm_latency_ms: float = 0.0

    # 总延迟
    avg_total_latency_ms: float = 0.0

    # 详情
    details: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_queries": self.total_queries,
            "successful_queries": self.successful_queries,
            "failed_queries": self.failed_queries,
            "avg_recall_at_k": round(self.avg_recall_at_k, 4),
            "avg_precision_at_k": round(self.avg_precision_at_k, 4),
            "avg_mrr": round(self.avg_mrr, 4),
            "avg_retrieval_latency_ms": round(self.avg_retrieval_latency_ms, 1),
            "avg_keyword_hit_rate": round(self.avg_keyword_hit_rate, 4),
            "avg_llm_latency_ms": round(self.avg_llm_latency_ms, 1),
            "avg_total_latency_ms": round(self.avg_total_latency_ms, 1),
            "details": self.details,
        }

    def summary(self) -> str:
        """生成可读的摘要"""
        lines = [
            f"RAG Evaluation Report",
            f"  Queries: {self.successful_queries}/{self.total_queries} succeeded",
            f"  --- Retrieval Quality ---",
            f"  Recall@K:    {self.avg_recall_at_k:.4f}",
            f"  Precision@K:  {self.avg_precision_at_k:.4f}",
            f"  MRR:          {self.avg_mrr:.4f}",
            f"  Retrieval Latency: {self.avg_retrieval_latency_ms:.1f}ms",
            f"  --- Generation Quality ---",
            f"  Keyword Hit Rate: {self.avg_keyword_hit_rate:.4f}",
            f"  LLM Latency:       {self.avg_llm_latency_ms:.1f}ms",
            f"  Total Latency:     {self.avg_total_latency_ms:.1f}ms",
        ]
        return "\n".join(lines)


class RAGEvaluator:
    """RAG 质量评估器

    评估流程:
    1. 对每个测试用例执行检索
    2. 计算检索质量指标 (Recall, Precision, MRR)
    3. (可选) 调用 LLM 生成回答, 评估关键词命中率
    4. 汇总输出评估报告
    """

    def __init__(self, db, use_llm: bool = False):
        """
        Args:
            db: 异步数据库会话
            use_llm: 是否调用 LLM 生成回答 (默认 False, 仅评估检索)
        """
        self.db = db
        self.use_llm = use_llm
        self.retrieval_service = RetrievalService(db)

    async def evaluate(
        self,
        kb_id: int,
        test_cases: List[TestCase],
        top_k: int = 5,
        user_id: Optional[int] = None,
    ) -> EvaluationReport:
        """执行批量评估

        Args:
            kb_id: 知识库 ID
            test_cases: 测试用例列表
            top_k: 检索返回数量
            user_id: 用户 ID (权限检查)

        Returns:
            评估报告
        """
        report = EvaluationReport(total_queries=len(test_cases))
        all_results: List[QueryResult] = []

        for i, tc in enumerate(test_cases, 1):
            logger.info("Evaluating query %d/%d: %s", i, len(test_cases), tc.query[:50])

            result = await self._evaluate_single(
                kb_id=kb_id,
                test_case=tc,
                top_k=top_k,
                user_id=user_id,
            )
            all_results.append(result)

            if result.retrieved_chunks:
                report.successful_queries += 1
            else:
                report.failed_queries += 1

        # 汇总指标
        successful = [r for r in all_results if r.retrieved_chunks]
        if successful:
            report.avg_recall_at_k = sum(r.recall_at_k for r in successful) / len(successful)
            report.avg_precision_at_k = sum(r.precision_at_k for r in successful) / len(successful)
            report.avg_mrr = sum(r.mrr for r in successful) / len(successful)
            report.avg_retrieval_latency_ms = sum(r.retrieval_latency_ms for r in successful) / len(successful)
            report.avg_keyword_hit_rate = sum(r.keyword_hit_rate for r in successful) / len(successful)
            report.avg_llm_latency_ms = sum(r.llm_latency_ms for r in successful) / len(successful)
            report.avg_total_latency_ms = sum(r.total_latency_ms for r in successful) / len(successful)

        # 详情
        for r in all_results:
            report.details.append({
                "query": r.query,
                "retrieved_count": len(r.retrieved_chunks),
                "retrieved_doc_ids": r.retrieved_doc_ids,
                "keyword_hits": r.keyword_hits,
                "keyword_hit_rate": round(r.keyword_hit_rate, 4),
                "recall_at_k": round(r.recall_at_k, 4),
                "precision_at_k": round(r.precision_at_k, 4),
                "mrr": round(r.mrr, 4),
                "retrieval_latency_ms": r.retrieval_latency_ms,
                "llm_latency_ms": r.llm_latency_ms,
                "total_latency_ms": r.total_latency_ms,
            })

        return report

    async def _evaluate_single(
        self,
        kb_id: int,
        test_case: TestCase,
        top_k: int,
        user_id: Optional[int],
    ) -> QueryResult:
        """评估单个查询"""
        result = QueryResult(query=test_case.query)
        total_start = time.time()

        # 1. 检索
        retrieval_start = time.time()
        try:
            hits = await self.retrieval_service.search(
                kb_id=kb_id,
                query_text=test_case.query,
                user_id=user_id,
                top_k=top_k,
                enable_rerank=True,
            )
        except Exception as e:
            logger.error("Retrieval failed for query '%s': %s", test_case.query[:50], str(e))
            hits = []

        result.retrieval_latency_ms = int((time.time() - retrieval_start) * 1000)

        # 转换结果
        for hit in hits:
            chunk_data = {
                "chunk_id": hit.chunk_id,
                "document_id": hit.document_id,
                "content": hit.content[:200],
                "final_score": hit.final_score,
                "document_filename": hit.document_filename,
            }
            result.retrieved_chunks.append(chunk_data)
            if hit.document_id not in result.retrieved_doc_ids:
                result.retrieved_doc_ids.append(hit.document_id)

        # 2. 计算检索质量指标
        if test_case.expected_doc_ids:
            expected_set = set(test_case.expected_doc_ids)
            retrieved_set = set(result.retrieved_doc_ids)

            # Recall@K: 正确文档被检索到的比例
            if expected_set:
                hits_count = len(expected_set & retrieved_set)
                result.recall_at_k = hits_count / len(expected_set)

            # Precision@K: 检索结果中正确文档的占比
            if retrieved_set:
                result.precision_at_k = len(expected_set & retrieved_set) / len(retrieved_set)

            # MRR: 第一个正确文档的倒数排名
            for rank, doc_id in enumerate(result.retrieved_doc_ids, 1):
                if doc_id in expected_set:
                    result.mrr = 1.0 / rank
                    break

        # 也检查文件名匹配
        if test_case.expected_source:
            for rank, hit in enumerate(hits, 1):
                if test_case.expected_source.lower() in (hit.document_filename or "").lower():
                    if result.mrr == 0:
                        result.mrr = 1.0 / rank
                    result.recall_at_k = max(result.recall_at_k, 1.0)
                    break

        # 3. (可选) LLM 生成 + 关键词命中率
        if self.use_llm and hits:
            llm_start = time.time()
            try:
                from app.processors import LLMService
                from app.processors.llm.llm_service import ChatMessage

                llm = LLMService()
                context = "\n\n".join([hit.content[:500] for hit in hits[:3]])

                messages = [
                    ChatMessage(role="system", content="你是一个知识库问答助手。根据以下检索到的上下文回答用户问题。"),
                    ChatMessage(role="user", content=f"上下文:\n{context}\n\n问题: {test_case.query}"),
                ]

                chat_result = await asyncio.to_thread(llm.chat, messages)
                result.llm_answer = chat_result.content
                result.llm_latency_ms = int((time.time() - llm_start) * 1000)

                # 关键词命中率
                if test_case.expected_keywords:
                    answer_lower = result.llm_answer.lower()
                    hits_kw = [
                        kw for kw in test_case.expected_keywords
                        if kw.lower() in answer_lower
                    ]
                    result.keyword_hits = hits_kw
                    result.keyword_hit_rate = len(hits_kw) / len(test_case.expected_keywords)

            except Exception as e:
                logger.warning("LLM evaluation failed: %s", str(e))
                result.llm_latency_ms = int((time.time() - llm_start) * 1000)

        # 如果不使用 LLM, 仍然评估关键词在检索内容中的命中率
        elif test_case.expected_keywords and hits:
            all_content = " ".join([hit.content for hit in hits]).lower()
            hits_kw = [
                kw for kw in test_case.expected_keywords
                if kw.lower() in all_content
            ]
            result.keyword_hits = hits_kw
            result.keyword_hit_rate = len(hits_kw) / len(test_case.expected_keywords)

        result.total_latency_ms = int((time.time() - total_start) * 1000)

        return result


__all__ = [
    "TestCase",
    "QueryResult",
    "EvaluationReport",
    "RAGEvaluator",
]
