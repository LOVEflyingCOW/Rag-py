"""Reranker — Cross-Encoder 二次精排

设计:
1. 在初步检索 (向量+BM25 混合) 后, 对 top-K 结果使用 Cross-Encoder 精排
2. Cross-Encoder 同时编码 query 和 document, 输出相关性分数
3. 支持 BAAI/bge-reranker-large 等模型 (sentence-transformers)
4. 模型懒加载: 首次调用时加载, 避免启动慢
5. 降级策略: sentence-transformers 不可用时, 使用关键词重排

优势:
- Cross-Encoder 比双塔模型 (Embedding) 更准确
- 可以修正向量检索的语义偏差
- 适合对 top-20 结果做精排

性能:
- bge-reranker-large: ~200ms/pair (CPU)
- bge-reranker-base: ~50ms/pair (CPU)
- 默认 top-K 控制在 20 以内, 总延迟 <2s

使用:
    reranker = Reranker(model_name="BAAI/bge-reranker-base")
    reranked = await reranker.rerank(query, documents, top_k=5)
"""

from __future__ import annotations

import asyncio
import time
from typing import List, Dict, Any, Optional

from app.core.logging import logger


# 依赖检测
try:
    from sentence_transformers import CrossEncoder
    _HAS_CROSS_ENCODER = True
except ImportError:
    _HAS_CROSS_ENCODER = False
    logger.info("sentence-transformers not available, reranker will use keyword fallback")


class BaseReranker:
    """Reranker 基类"""

    async def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """对文档列表进行重排序

        Args:
            query: 查询文本
            documents: 文档列表 (每个文档至少包含 "content" 字段)
            top_k: 返回前 K 个结果

        Returns:
            排序后的文档列表, 每个文档增加 "rerank_score" 字段
        """
        raise NotImplementedError


class CrossEncoderReranker(BaseReranker):
    """Cross-Encoder 重排序器

    使用 sentence-transformers 的 CrossEncoder 模型。
    推荐:
    - BAAI/bge-reranker-base: 轻量, 适合 CPU 环境
    - BAAI/bge-reranker-large: 精度更高, 需要 GPU
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        max_length: int = 512,
        device: str = "cpu",
    ):
        self.model_name = model_name
        self.max_length = max_length
        self.device = device
        self._model: Optional[CrossEncoder] = None  # 懒加载

    def _ensure_model(self):
        """懒加载模型 (首次调用时加载)"""
        if self._model is None:
            if not _HAS_CROSS_ENCODER:
                raise ImportError(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                )
            logger.info("Loading CrossEncoder model: %s", self.model_name)
            start = time.time()
            self._model = CrossEncoder(
                self.model_name,
                max_length=self.max_length,
                device=self.device,
            )
            elapsed = time.time() - start
            logger.info("CrossEncoder loaded in %.2fs", elapsed)

    async def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """Cross-Encoder 精排

        Args:
            query: 查询文本
            documents: 文档列表
            top_k: 返回数量

        Returns:
            精排后的文档列表
        """
        if not documents:
            return []

        if not _HAS_CROSS_ENCODER:
            # 降级到关键词重排
            return await KeywordReranker().rerank(query, documents, top_k)

        # 在线程池中执行 (模型推理是 CPU 密集型)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._rerank_sync,
            query,
            documents,
            top_k,
        )

    def _rerank_sync(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """同步精排 (在线程池中执行)"""
        self._ensure_model()

        # 构建 (query, document) pairs
        pairs = []
        for doc in documents:
            content = doc.get("content", "")
            # 截断过长的内容
            if len(content) > 2000:
                content = content[:2000]
            pairs.append((query, content))

        # 批量预测相关性分数
        start = time.time()
        scores = self._model.predict(pairs, show_progress_bar=False)
        elapsed = time.time() - start
        logger.debug("CrossEncoder predicted %d pairs in %.3fs", len(pairs), elapsed)

        # 组合分数并排序
        scored_docs = []
        for doc, score in zip(documents, scores):
            # CrossEncoder 输出的是 logit, 通过 sigmoid 转为 0-1
            import math
            rerank_score = 1.0 / (1.0 + math.exp(-float(score)))
            doc_with_score = {**doc, "rerank_score": rerank_score}
            scored_docs.append(doc_with_score)

        # 按 rerank_score 降序排序
        scored_docs.sort(key=lambda x: x["rerank_score"], reverse=True)

        # 重新编号 rank
        for i, doc in enumerate(scored_docs[:top_k], 1):
            doc["rerank_rank"] = i

        return scored_docs[:top_k]


class KeywordReranker(BaseReranker):
    """关键词重排器 (降级方案)

    当 sentence-transformers 不可用时使用。
    基于关键词覆盖率和位置加权进行重排。
    """

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """提取关键词 (中英文混合)"""
        import re
        keywords = []
        # 英文单词
        for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text):
            keywords.append(w.lower())
        # 中文双字组合
        for seg in re.findall(r"[\u4e00-\u9fa5]+", text):
            for i in range(len(seg) - 1):
                keywords.append(seg[i:i + 2])
        return list(set(keywords))

    async def rerank(
        self,
        query: str,
        documents: List[Dict[str, Any]],
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """关键词覆盖率重排"""
        if not documents:
            return []

        query_keywords = self._extract_keywords(query)
        if not query_keywords:
            return documents[:top_k]

        scored_docs = []
        for doc in documents:
            content = doc.get("content", "").lower()
            hits = 0
            position_bonus = 0.0

            for kw in query_keywords:
                if kw in content:
                    hits += 1
                    # 出现位置越靠前, 分数越高
                    pos = content.find(kw)
                    if pos >= 0:
                        position_bonus += max(0, 1.0 - pos / 1000.0)

            coverage = hits / len(query_keywords)
            # 最终分数 = 覆盖率 * 0.7 + 位置加权 * 0.3
            rerank_score = coverage * 0.7 + min(position_bonus / len(query_keywords), 1.0) * 0.3

            doc_with_score = {**doc, "rerank_score": rerank_score}
            scored_docs.append(doc_with_score)

        scored_docs.sort(key=lambda x: x["rerank_score"], reverse=True)

        for i, doc in enumerate(scored_docs[:top_k], 1):
            doc["rerank_rank"] = i

        return scored_docs[:top_k]


class RerankerFactory:
    """Reranker 工厂"""

    @staticmethod
    def create(
        model_name: str = "BAAI/bge-reranker-base",
        device: str = "cpu",
    ) -> BaseReranker:
        """创建 Reranker 实例

        Args:
            model_name: Cross-Encoder 模型名
            device: 运行设备 (cpu/cuda)

        Returns:
            Reranker 实例 (CrossEncoder 或降级方案)
        """
        if _HAS_CROSS_ENCODER:
            return CrossEncoderReranker(model_name=model_name, device=device)
        else:
            logger.warning(
                "sentence-transformers not available, using keyword reranker"
            )
            return KeywordReranker()


# 全局单例
_reranker: Optional[BaseReranker] = None


def get_reranker(
    model_name: str = "BAAI/bge-reranker-base",
    device: str = "cpu",
) -> BaseReranker:
    """获取全局 Reranker 单例"""
    global _reranker
    if _reranker is None:
        _reranker = RerankerFactory.create(model_name=model_name, device=device)
    return _reranker


__all__ = [
    "BaseReranker",
    "CrossEncoderReranker",
    "KeywordReranker",
    "RerankerFactory",
    "get_reranker",
]
