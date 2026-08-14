"""PostgreSQL 原生检索服务 (Phase 4)

替代内存 BM25 和 FAISS，使用 PostgreSQL 原生能力：
1. pgvector 进行向量相似度搜索
2. PostgreSQL FTS (Full-Text Search) 进行全文检索
3. 混合搜索 (Vector + FTS)

核心优势:
- 数据一致性: 向量和文本索引直接存储在数据库中
- 高性能: GIN 索引 + IVF 索引，支持百万级文档
- 实时性: 索引随数据更新自动更新，无需重建
- 可扩展: 支持分布式部署和读写分离
"""

from __future__ import annotations

import math
import re
from typing import List, Dict, Optional, Any, Tuple

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import expression

from app.core.config import settings
from app.core.logging import logger
from app.models.entities.document import DocumentChunk, Document
from app.models.entities.knowledge_base import KnowledgeBase
from app.processors import EmbeddingService


class PgVectorSearch:
    """pgvector 向量搜索服务

    使用 pgvector 扩展进行高效的向量相似度搜索:
    - 支持余弦距离 (cosine distance)
    - 支持 L2 距离 (euclidean distance)
    - 支持内积 (inner product)
    """

    COSINE_DISTANCE = "<=>"
    L2_DISTANCE = "<->"
    INNER_PRODUCT = "<#>"

    def __init__(self, db: AsyncSession, metric: str = COSINE_DISTANCE):
        self.db = db
        self.metric = metric

    async def search(
        self,
        kb_id: int,
        query_vector: List[float],
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        使用 pgvector 进行向量相似度搜索

        Args:
            kb_id: 知识库 ID
            query_vector: 查询向量
            top_k: 返回前 K 个结果
            min_score: 最低相似度阈值 (0-1)

        Returns:
            List of dicts with keys: chunk_id, document_id, content, distance, score
        """
        vector_str = f"[{','.join(str(v) for v in query_vector)}]"
        
        distance_expr = getattr(DocumentChunk.embedding, self.metric)(vector_str)
        
        query = (
            select(
                DocumentChunk.id.label("chunk_id"),
                DocumentChunk.document_id,
                DocumentChunk.content,
                DocumentChunk.metadata_,
                distance_expr.label("distance"),
            )
            .where(DocumentChunk.knowledge_base_id == kb_id)
            .where(DocumentChunk.embedding.isnot(None))
            .order_by(distance_expr)
            .limit(top_k * 5)  # 多取一些用于重排序
        )
        
        result = await self.db.execute(query)
        rows = result.fetchall()
        
        results = []
        for row in rows:
            # 计算相似度分数 (0-1, 越大越相似)
            distance = row.distance
            if self.metric == self.COSINE_DISTANCE:
                # 余弦距离: 0 = 完全相同, 2 = 完全相反
                # 转换为相似度: 1 - distance/2
                score = 1.0 - distance / 2.0
            elif self.metric == self.L2_DISTANCE:
                # L2 距离: 越小越相似
                score = 1.0 / (1.0 + distance)
            else:  # INNER_PRODUCT
                # 内积: 越大越相似
                # 归一化到 [0, 1] 范围
                score = (distance + 1.0) / 2.0
            
            if score < min_score:
                continue
                
            results.append({
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "content": row.content,
                "metadata": row.metadata_,
                "distance": distance,
                "score": max(0.0, min(1.0, score)),
            })
        
        return results

    async def batch_search(
        self,
        kb_id: int,
        query_vectors: List[List[float]],
        top_k: int = 5,
    ) -> List[List[Dict[str, Any]]]:
        """批量向量搜索"""
        return [
            await self.search(kb_id, qv, top_k)
            for qv in query_vectors
        ]


class PgFullTextSearch:
    """PostgreSQL 全文检索服务 (FTS)

    使用 PostgreSQL 原生的全文检索能力:
    - to_tsvector: 将文档内容转换为 tsvector
    - to_tsquery: 将查询转换为 tsquery
    - @@ 操作符: 进行全文匹配
    - GIN 索引: 高效索引

    支持:
    - 中文分词 (使用 simple 或 custom 分词器)
    - 英文分词 (使用 english 分词器)
    - 关键词匹配和相关性排名
    """

    def __init__(self, db: AsyncSession, language: str = "simple"):
        self.db = db
        self.language = language

    async def index_document(
        self,
        chunk_id: int,
        content: str,
    ) -> None:
        """
        为文档块创建全文索引

        Args:
            chunk_id: 文档块 ID
            content: 文档内容
        """
        query = text("""
            UPDATE chunks
            SET search_vector = to_tsvector(:language, :content)
            WHERE id = :chunk_id
        """)
        
        await self.db.execute(query, {
            "language": self.language,
            "content": content,
            "chunk_id": chunk_id,
        })
        await self.db.commit()

    async def batch_index_documents(
        self,
        documents: List[Tuple[int, str]],
    ) -> None:
        """批量创建全文索引"""
        for chunk_id, content in documents:
            await self.index_document(chunk_id, content)

    async def search(
        self,
        kb_id: int,
        query_text: str,
        top_k: int = 5,
        min_rank: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        全文检索

        Args:
            kb_id: 知识库 ID
            query_text: 查询文本
            top_k: 返回前 K 个结果
            min_rank: 最低相关性排名

        Returns:
            List of dicts with keys: chunk_id, document_id, content, rank, snippet
        """
        # 预处理查询文本，处理中文
        processed_query = self._preprocess_query(query_text)
        
        query = text("""
            SELECT
                c.id AS chunk_id,
                c.document_id,
                c.content,
                c.metadata,
                ts_rank(c.search_vector, to_tsquery(:language, :query)) AS rank
            FROM chunks c
            WHERE c.knowledge_base_id = :kb_id
                AND c.search_vector IS NOT NULL
                AND c.search_vector @@ to_tsquery(:language, :query)
            ORDER BY rank DESC
            LIMIT :limit
        """)
        
        result = await self.db.execute(query, {
            "language": self.language,
            "query": processed_query,
            "kb_id": kb_id,
            "limit": top_k * 5,
        })
        
        rows = result.fetchall()
        
        results = []
        for row in rows:
            rank = row.rank if row.rank else 0.0
            if rank < min_rank:
                continue
            
            # 提取匹配片段
            snippet = self._extract_snippet(row.content, query_text)
            
            results.append({
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "content": row.content,
                "metadata": row.metadata,
                "rank": rank,
                "snippet": snippet,
            })
        
        return results

    def _preprocess_query(self, query: str) -> str:
        """
        预处理查询文本，支持中文分词

        PostgreSQL FTS 默认分词器不支持中文，
        我们需要将中文查询拆分为字符并使用 '|' 连接 (OR 逻辑)
        或者使用 '&' 连接 (AND 逻辑)
        """
        if not query:
            return ""
        
        # 检测是否包含中文
        has_chinese = bool(re.search(r'[\u4e00-\u9fa5]', query))
        
        if has_chinese:
            # 中文处理：将每个字符用 '|' 连接 (OR 逻辑)
            # 或者将连续中文字符组合在一起
            tokens = []
            # 提取中文子串和英文单词
            segments = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+|\d+', query)
            
            for seg in segments:
                if re.search(r'[\u4e00-\u9fa5]', seg):
                    # 中文段：每个字符单独处理，用 | 连接
                    chars = [c for c in seg]
                    tokens.append(" | ".join(chars))
                else:
                    # 英文/数字段：直接使用
                    tokens.append(seg)
            
            return " & ".join(tokens)
        else:
            # 纯英文：使用默认处理
            # 将空格替换为 & (AND 逻辑)，或者保持空格让 PostgreSQL 处理
            return query.strip().replace(" ", " & ")

    def _extract_snippet(self, content: str, query: str, context: int = 100) -> str:
        """
        从内容中提取与查询匹配的片段

        Args:
            content: 文档内容
            query: 查询文本
            context: 上下文字符数

        Returns:
            包含匹配内容的片段
        """
        if not content or not query:
            return content[:200] if content else ""
        
        # 查找查询中的关键词在内容中的位置
        keywords = re.findall(r'[\u4e00-\u9fa5]+|[a-zA-Z]+|\d+', query)
        
        best_pos = -1
        for kw in keywords:
            pos = content.lower().find(kw.lower())
            if pos >= 0:
                best_pos = pos
                break
        
        if best_pos < 0:
            # 没有找到匹配，返回开头部分
            return content[:200] + "..." if len(content) > 200 else content
        
        # 计算片段范围
        start = max(0, best_pos - context)
        end = min(len(content), best_pos + len(query) + context)
        
        snippet = content[start:end]
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."
        
        return snippet


class PostgreSQLHybridSearch:
    """PostgreSQL 混合检索服务 (Vector + FTS)

    结合向量相似度搜索和全文检索的优势:
    1. 向量搜索: 捕捉语义相似性
    2. 全文检索: 捕捉关键词精确匹配
    3. 混合重排: 融合两路结果，提高检索质量

    支持的融合策略:
    - Linear Weighted: 线性加权融合
    - Reciprocal Rank Fusion (RRF): 倒数排名融合
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.embedding = EmbeddingService()
        self.vector_search = PgVectorSearch(db)
        self.fts_search = PgFullTextSearch(db)

    # ---------- 核心搜索 ----------
    async def search(
        self,
        kb_id: int,
        query_text: str,
        user_id: Optional[int] = None,
        top_k: int = 5,
        min_score: float = 0.0,
        vector_weight: float = 0.6,
        fts_weight: float = 0.4,
    ) -> List[Dict[str, Any]]:
        """
        混合检索

        Args:
            kb_id: 知识库 ID
            query_text: 查询文本
            user_id: 用户 ID (权限检查)
            top_k: 返回结果数量
            min_score: 最低分数阈值
            vector_weight: 向量搜索权重
            fts_weight: 全文检索权重

        Returns:
            List of dicts with keys: chunk_id, document_id, content, score, search_type
        """
        if not query_text or not query_text.strip():
            return []

        # 1. 权限检查
        kb = await self._check_permission(kb_id, user_id)
        if kb is None:
            return []

        # 2. 向量搜索
        query_vector = self.embedding.encode_single(query_text)
        vector_results = await self.vector_search.search(
            kb_id=kb_id,
            query_vector=query_vector,
            top_k=top_k * 3,
        )

        # 3. 全文检索
        fts_results = await self.fts_search.search(
            kb_id=kb_id,
            query_text=query_text,
            top_k=top_k * 3,
        )

        # 4. 混合融合
        fused_results = self._linear_weighted_fusion(
            vector_results, fts_results,
            vector_weight, fts_weight,
        )

        # 5. 过滤和排序
        filtered_results = [
            r for r in fused_results
            if r["final_score"] >= min_score
        ]
        filtered_results.sort(key=lambda x: x["final_score"], reverse=True)

        return filtered_results[:top_k]

    # ---------- RRF 融合 ----------
    async def search_with_rrf(
        self,
        kb_id: int,
        query_text: str,
        user_id: Optional[int] = None,
        top_k: int = 5,
        k: int = 60,
    ) -> List[Dict[str, Any]]:
        """
        使用 Reciprocal Rank Fusion (RRF) 进行混合检索

        RRF 公式: score = 1/(k + rank_vector) + 1/(k + rank_fts)

        Args:
            kb_id: 知识库 ID
            query_text: 查询文本
            user_id: 用户 ID
            top_k: 返回结果数量
            k: RRF 常数 (通常设为 60)

        Returns:
            List of dicts with fused scores
        """
        if not query_text or not query_text.strip():
            return []

        kb = await self._check_permission(kb_id, user_id)
        if kb is None:
            return []

        # 执行两路搜索
        query_vector = self.embedding.encode_single(query_text)
        vector_results = await self.vector_search.search(
            kb_id=kb_id,
            query_vector=query_vector,
            top_k=top_k * 3,
        )
        fts_results = await self.fts_search.search(
            kb_id=kb_id,
            query_text=query_text,
            top_k=top_k * 3,
        )

        # RRF 融合
        rrf_scores: Dict[int, float] = {}
        chunk_info: Dict[int, Dict[str, Any]] = {}

        # 向量搜索排名
        for rank, r in enumerate(vector_results, 1):
            cid = r["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (k + rank)
            if cid not in chunk_info:
                chunk_info[cid] = r

        # 全文检索排名
        for rank, r in enumerate(fts_results, 1):
            cid = r["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0) + 1.0 / (k + rank)
            if cid not in chunk_info:
                chunk_info[cid] = r

        # 排序
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        
        results = []
        for cid in sorted_ids[:top_k]:
            info = chunk_info[cid]
            results.append({
                "chunk_id": cid,
                "document_id": info.get("document_id", 0),
                "content": info.get("content", ""),
                "final_score": rrf_scores[cid],
                "vector_score": info.get("score", 0),
                "fts_score": info.get("rank", 0),
                "search_type": "hybrid_rrf",
            })

        return results

    # ---------- 辅助方法 ----------
    async def _check_permission(
        self, kb_id: int, user_id: Optional[int]
    ) -> Optional[KnowledgeBase]:
        """检查用户是否有权访问知识库"""
        result = await self.db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        )
        kb = result.scalars().first()
        if kb is None:
            return None
        if kb.is_public:
            return kb
        if user_id is not None and kb.user_id == user_id:
            return kb
        return None

    def _linear_weighted_fusion(
        self,
        vector_results: List[Dict[str, Any]],
        fts_results: List[Dict[str, Any]],
        vector_weight: float,
        fts_weight: float,
    ) -> List[Dict[str, Any]]:
        """
        线性加权融合

        final_score = vector_weight * vector_score + fts_weight * fts_score
        """
        # 归一化分数
        vector_scores = {
            r["chunk_id"]: r["score"]
            for r in vector_results
        }
        fts_scores = {
            r["chunk_id"]: r["rank"]
            for r in fts_results
        }

        # 收集所有 chunk ID
        all_chunk_ids = set(list(vector_scores.keys()) + list(fts_scores.keys()))

        # 获取 chunk 信息
        chunk_info: Dict[int, Dict[str, Any]] = {}
        for r in vector_results + fts_results:
            cid = r["chunk_id"]
            if cid not in chunk_info:
                chunk_info[cid] = {
                    "chunk_id": cid,
                    "document_id": r.get("document_id", 0),
                    "content": r.get("content", ""),
                    "metadata": r.get("metadata", {}),
                }

        # 融合分数
        results = []
        for cid in all_chunk_ids:
            v_score = vector_scores.get(cid, 0.0)
            f_score = fts_scores.get(cid, 0.0)
            
            # 归一化 fts_score (通常 ts_rank 在 0-10 之间)
            f_score_normalized = min(1.0, f_score / 10.0)
            
            final_score = vector_weight * v_score + fts_weight * f_score_normalized
            
            info = chunk_info.get(cid, {"chunk_id": cid})
            results.append({
                **info,
                "vector_score": v_score,
                "fts_score": f_score,
                "final_score": final_score,
                "search_type": "hybrid_linear",
            })

        # 按分数排序
        results.sort(key=lambda x: x["final_score"], reverse=True)
        return results


# 工厂函数
def create_hybrid_search(db: AsyncSession) -> PostgreSQLHybridSearch:
    """创建混合检索实例"""
    return PostgreSQLHybridSearch(db)
