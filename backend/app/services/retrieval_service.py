"""检索服务 —— 统一的向量搜索 + 全文检索 + 混合重排能力

Phase 4 增强版:
- 支持 PostgreSQL 原生检索 (pgvector + FTS)
- 支持 SQLite/FAISS 降级模式
- 自动检测数据库类型，选择最优检索策略
- 混合重排 (Vector + FTS + Lexical 三路融合)

核心接口:
    search(kb_id, query_text, ...) -> List[RetrievedHit]
"""

from __future__ import annotations

import math
import re
import time
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
from collections import Counter as _Counter

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.models.entities.document import Document, DocumentChunk
from app.models.entities.knowledge_base import KnowledgeBase
from app.models.database import _is_sqlite
from app.processors import EmbeddingService, VectorStoreManager

# PostgreSQL 原生检索服务 (Phase 4)
try:
    from app.services.postgres_search import (
        PgVectorSearch,
        PgFullTextSearch,
        PostgreSQLHybridSearch,
    )
    from app.services.pg_vector_store import PgVectorStore
    _HAS_POSTGRES_SEARCH = True
except ImportError:
    _HAS_POSTGRES_SEARCH = False
    logger.warning("PostgreSQL 检索服务不可用，将使用降级模式")

Chunk = DocumentChunk


@dataclass
class RetrievedHit:
    """检索命中结果"""
    chunk_id: int
    document_id: int
    knowledge_base_id: int
    content: str
    document_filename: str
    vector_score: float
    bm25_score: float
    keyword_score: float
    final_score: float
    rank: int
    search_type: str = "vector"  # vector, bm25, hybrid, postgres


# ============ BM25 文本索引（纯 Python，零依赖） ============

class BM25Index:
    """Okapi BM25 轻量级实现 (降级模式)"""
    K1 = 1.5
    B = 0.75
    EPS = 1e-6

    def __init__(self):
        self._docs: List[List[str]] = []
        self._doc_len: List[int] = []
        self._avg_len: float = 0.0
        self._n_docs: int = 0
        self._df: Dict[str, int] = {}
        self._idf: Dict[str, float] = {}
        self._external_ids: Dict[int, int] = {}

    @staticmethod
    def tokenize(text: str) -> List[str]:
        if not text:
            return []
        tokens: List[str] = []
        for m in re.finditer(r"[A-Za-z][A-Za-z0-9\-']{1,}|[0-9]+", text):
            tokens.append(m.group(0).lower())
        for seg in re.findall(r"[\u4e00-\u9fa5]+", text):
            if len(seg) == 1:
                tokens.append(seg)
            else:
                for i in range(len(seg) - 1):
                    tokens.append(seg[i:i + 2])
                if len(seg) <= 4:
                    for ch in seg:
                        tokens.append(ch)
        filtered: List[str] = []
        for t in tokens:
            if len(t) == 1 and re.match(r"[A-Za-z]", t):
                continue
            if t in {"的", "了", "是", "在", "和", "有", "也", "就", "这", "那", "吗", "呢", "吧", "and", "the", "a", "an", "is", "are", "of", "to", "for"}:
                continue
            filtered.append(t)
        return filtered

    def add_doc(self, external_id: int, content: str) -> None:
        tokens = self.tokenize(content)
        internal_id = self._n_docs
        self._external_ids[external_id] = internal_id
        self._docs.append(tokens)
        self._doc_len.append(len(tokens))
        self._n_docs += 1
        self._avg_len = sum(self._doc_len) / self._n_docs
        for t in set(tokens):
            self._df[t] = self._df.get(t, 0) + 1
        for t, dfv in self._df.items():
            self._idf[t] = math.log((self._n_docs - dfv + 0.5) / (dfv + 0.5) + 1.0)

    def score(self, query_text: str, external_ids: Optional[List[int]] = None) -> Dict[int, float]:
        q_tokens = self.tokenize(query_text)
        if not q_tokens or self._n_docs == 0:
            return {}
        avg_len = self._avg_len
        results: Dict[int, float] = {}
        if external_ids is not None:
            id_set = set(external_ids)
            iter_ids = [(eid, self._external_ids[eid]) for eid in id_set if eid in self._external_ids]
        else:
            iter_ids = [(eid, iid) for eid, iid in self._external_ids.items()]

        for ext_id, iid in iter_ids:
            doc_tokens = self._docs[iid]
            if not doc_tokens:
                results[ext_id] = 0.0
                continue
            dl = len(doc_tokens)
            tf = _Counter(doc_tokens)
            score = 0.0
            for qt in q_tokens:
                if qt not in tf:
                    continue
                idf = self._idf.get(qt, 0.0)
                tf_ = tf[qt]
                bm25_tf = (tf_ * (self.K1 + 1)) / (tf_ + self.K1 * (1 - self.B + self.B * dl / (avg_len or 1)))
                score += idf * bm25_tf
            results[ext_id] = score
        return results

    def score_normalized(self, query_text: str, external_ids: Optional[List[int]] = None) -> Dict[int, float]:
        raw = self.score(query_text, external_ids)
        if not raw:
            return {}
        max_v = max(raw.values())
        if max_v <= self.EPS:
            return {k: 0.0 for k in raw}
        return {k: float(v) / max_v for k, v in raw.items()}


class RetrievalService:
    """检索服务（Phase 4 增强版）

    根据数据库类型自动选择检索策略:
    - PostgreSQL: 使用 pgvector + FTS 原生检索 (高性能)
    - SQLite: 使用 FAISS + BM25 内存检索 (降级模式)
    """

    VECTOR_WEIGHT = 0.50
    BM25_WEIGHT = 0.35
    KEYWORD_WEIGHT = 0.15

    def __init__(self, db: AsyncSession):
        self.db = db
        self._embedding: Optional[EmbeddingService] = None
        self._vector_manager: Optional[VectorStoreManager] = None
        self._bm25_cache: Dict[int, BM25Index] = {}
        self._bm25_last_rebuild: Dict[int, float] = {}
        
        # Phase 4: PostgreSQL 原生检索组件
        self._pg_vector_store: Optional[PgVectorStore] = None
        self._pg_fts: Optional[PgFullTextSearch] = None
        self._pg_hybrid: Optional[PostgreSQLHybridSearch] = None
        self._use_postgres_search = _HAS_POSTGRES_SEARCH and not _is_sqlite

        logger.info(
            "检索服务初始化: 模式=%s",
            "PostgreSQL原生检索" if self._use_postgres_search else "降级模式(SQLite/FAISS)"
        )

    @property
    def embedding(self) -> EmbeddingService:
        if self._embedding is None:
            self._embedding = EmbeddingService()
        return self._embedding

    @property
    def vector_manager(self) -> VectorStoreManager:
        if self._vector_manager is None:
            self._vector_manager = VectorStoreManager(
                base_dir=settings.VECTOR_STORE_DIR,
                default_dim=self.embedding.dim,
            )
        return self._vector_manager

    @property
    def pg_vector_store(self) -> Optional[PgVectorStore]:
        if not self._use_postgres_search:
            return None
        if self._pg_vector_store is None:
            self._pg_vector_store = PgVectorStore(self.db, self.embedding.dim)
        return self._pg_vector_store

    @property
    def pg_fts(self) -> Optional[PgFullTextSearch]:
        if not self._use_postgres_search:
            return None
        if self._pg_fts is None:
            self._pg_fts = PgFullTextSearch(self.db)
        return self._pg_fts

    @property
    def pg_hybrid(self) -> Optional[PostgreSQLHybridSearch]:
        if not self._use_postgres_search:
            return None
        if self._pg_hybrid is None:
            self._pg_hybrid = PostgreSQLHybridSearch(self.db)
        return self._pg_hybrid

    # ---------- 权限检查 ----------
    async def _can_access_kb(self, kb_id: int, user_id: Optional[int]) -> Optional[KnowledgeBase]:
        result = await self.db.execute(select(KnowledgeBase).where(KnowledgeBase.id == kb_id))
        kb = result.scalars().first()
        if kb is None:
            return None
        if kb.is_public:
            return kb
        if user_id is not None and kb.user_id == user_id:
            return kb
        return None

    # ---------- 核心: 搜索 (自动选择策略) ----------
    async def search(
        self,
        kb_id: int,
        query_text: str,
        user_id: Optional[int] = None,
        top_k: int = 5,
        min_score: float = 0.0,
        enable_rerank: bool = True,
        enable_merge: bool = False,
        force_postgres: bool = False,
    ) -> List[RetrievedHit]:
        """
        搜索接口 (自动选择检索策略)

        Args:
            kb_id: 知识库 ID
            query_text: 查询文本
            user_id: 用户 ID (权限检查)
            top_k: 返回结果数量
            min_score: 最低分数阈值
            enable_rerank: 是否启用重排序
            enable_merge: 是否合并重叠内容
            force_postgres: 强制使用 PostgreSQL 检索 (即使是 SQLite)

        Returns:
            List of RetrievedHit
        """
        if not query_text or not query_text.strip():
            return []

        kb = await self._can_access_kb(kb_id, user_id)
        if kb is None:
            return []

        # Phase 4: 尝试使用 PostgreSQL 原生检索
        if self._use_postgres_search or force_postgres:
            try:
                if await self._has_postgres_vectors(kb_id):
                    return await self._search_postgres_native(
                        kb_id, query_text, top_k, min_score, enable_merge
                    )
            except Exception as e:
                logger.warning(f"PostgreSQL 原生检索失败，降级到 FAISS: {e}")

        # 降级: 使用 FAISS + BM25
        return await self._search_fallback(
            kb_id, query_text, user_id, top_k, min_score, enable_rerank, enable_merge
        )

    # ---------- PostgreSQL 原生检索 ----------
    async def _has_postgres_vectors(self, kb_id: int) -> bool:
        """检查知识库是否有 pgvector 数据"""
        if self.pg_vector_store is None:
            return False
        return await self.pg_vector_store.has_vectors(kb_id)

    async def _search_postgres_native(
        self,
        kb_id: int,
        query_text: str,
        top_k: int,
        min_score: float,
        enable_merge: bool,
    ) -> List[RetrievedHit]:
        """
        使用 PostgreSQL 原生检索 (pgvector + FTS)

        优势:
        - 向量搜索在数据库层面完成
        - 全文检索使用 GIN 索引
        - 混合重排融合两路结果
        """
        if self.pg_hybrid is None:
            return []

        # 执行混合检索
        fused_results = await self.pg_hybrid.search(
            kb_id=kb_id,
            query_text=query_text,
            top_k=top_k * 3,
            min_score=min_score * 0.5,  # 放宽阈值，后续再过滤
            vector_weight=0.6,
            fts_weight=0.4,
        )

        if not fused_results:
            return []

        # 转换为 RetrievedHit
        hits = []
        for rank, result in enumerate(fused_results[:top_k], start=1):
            hits.append(RetrievedHit(
                chunk_id=result["chunk_id"],
                document_id=result["document_id"],
                knowledge_base_id=kb_id,
                content=result["content"],
                document_filename=result.get("document_filename", ""),
                vector_score=result.get("vector_score", 0.0),
                bm25_score=result.get("fts_score", 0.0),
                keyword_score=0.0,
                final_score=result["final_score"],
                rank=rank,
                search_type="postgres_native",
            ))

        # 合并重叠内容
        if enable_merge and len(hits) > 1:
            hits = self._merge_hits(hits)

        return hits

    # ---------- 降级搜索 (FAISS + BM25) ----------
    async def _search_fallback(
        self,
        kb_id: int,
        query_text: str,
        user_id: Optional[int],
        top_k: int,
        min_score: float,
        enable_rerank: bool,
        enable_merge: bool,
    ) -> List[RetrievedHit]:
        """降级搜索: FAISS 向量 + BM25 文本"""
        store = self.vector_manager.get_store(kb_id, dim=self.embedding.dim)
        if store.total() == 0:
            await self._rebuild_if_needed(kb_id)
            store = self.vector_manager.get_store(kb_id, dim=self.embedding.dim)
            if store.total() == 0:
                return []

        query_vec = self.embedding.encode_single(query_text)
        if not query_vec:
            return []

        candidate_count = max(top_k * 5, 20)
        raw_vec = store.search(query_vec, top_k=candidate_count)

        by_chunk_id: Dict[int, Dict[str, Any]] = {}
        for r in raw_vec:
            meta = r.get("metadata") or {}
            chunk_id = meta.get("chunk_id")
            if chunk_id is None:
                continue
            vec_score = float(r.get("score", 0.0))
            content = meta.get("content", "")
            doc_id = meta.get("document_id")
            doc_filename = meta.get("document_filename", "")
            if not content:
                result = await self.db.execute(select(Chunk).where(Chunk.id == int(chunk_id)))
                chunk_from_db = result.scalars().first()
                if chunk_from_db is None:
                    continue
                content = chunk_from_db.content
            by_chunk_id[int(chunk_id)] = {
                "chunk_id": int(chunk_id),
                "document_id": int(doc_id) if doc_id is not None else 0,
                "knowledge_base_id": int(kb_id),
                "content": content,
                "document_filename": doc_filename or "",
                "vector_score": vec_score,
                "bm25_score": 0.0,
                "keyword_score": 0.0,
            }

        if enable_rerank:
            bm25 = await self._get_or_build_bm25(kb_id)
            if bm25 is not None and bm25._n_docs > 0:
                vector_candidates = list(by_chunk_id.keys())
                bm25_norm = bm25.score_normalized(query_text, vector_candidates)
                for cid, s in bm25_norm.items():
                    if cid in by_chunk_id:
                        by_chunk_id[cid]["bm25_score"] = float(s)

                if len(by_chunk_id) < candidate_count:
                    bm25_all = bm25.score_normalized(query_text)
                    for ext_id, bm_s in sorted(bm25_all.items(), key=lambda x: -x[1])[:candidate_count]:
                        if ext_id in by_chunk_id:
                            continue
                        if bm_s <= 0.15:
                            continue
                        result = await self.db.execute(select(Chunk).where(Chunk.id == int(ext_id)))
                        chunk_db = result.scalars().first()
                        if chunk_db is None:
                            continue
                        result = await self.db.execute(select(Document).where(Document.id == chunk_db.document_id))
                        doc = result.scalars().first()
                        by_chunk_id[ext_id] = {
                            "chunk_id": ext_id,
                            "document_id": chunk_db.document_id or 0,
                            "knowledge_base_id": int(kb_id),
                            "content": chunk_db.content,
                            "document_filename": doc.filename if doc else "",
                            "vector_score": 0.0,
                            "bm25_score": float(bm_s),
                            "keyword_score": 0.0,
                        }

        if enable_rerank:
            query_tokens = self._extract_keywords(query_text)
            for hit in by_chunk_id.values():
                hit["keyword_score"] = self._keyword_overlap_score(hit["content"], query_tokens)

        for hit in by_chunk_id.values():
            hit["final_score"] = (
                self.VECTOR_WEIGHT * hit["vector_score"]
                + self.BM25_WEIGHT * hit["bm25_score"]
                + self.KEYWORD_WEIGHT * hit["keyword_score"]
            )

        hits_data = list(by_chunk_id.values())
        hits_data.sort(key=lambda x: x["final_score"], reverse=True)
        hits_data = [h for h in hits_data if h["final_score"] >= min_score]

        if enable_merge and len(hits_data) > 1:
            hits_data = self._merge_overlapping(hits_data)

        results: List[RetrievedHit] = []
        for rank, hit in enumerate(hits_data[:top_k], start=1):
            results.append(RetrievedHit(
                chunk_id=hit["chunk_id"],
                document_id=hit["document_id"],
                knowledge_base_id=hit["knowledge_base_id"],
                content=hit["content"],
                document_filename=hit["document_filename"],
                vector_score=float(hit["vector_score"]),
                bm25_score=float(hit.get("bm25_score", 0.0)),
                keyword_score=float(hit["keyword_score"]),
                final_score=float(hit["final_score"]),
                rank=rank,
                search_type="faiss_bm25",
            ))

        return results

    # ---------- BM25 索引构建 ----------
    async def _get_or_build_bm25(self, kb_id: int, rebuild_interval_sec: float = 600.0) -> Optional[BM25Index]:
        now = time.time()
        last = self._bm25_last_rebuild.get(kb_id, 0.0)
        if kb_id in self._bm25_cache and (now - last) < rebuild_interval_sec:
            return self._bm25_cache[kb_id]

        try:
            result = await self.db.execute(select(Chunk).where(Chunk.knowledge_base_id == kb_id))
            chunks = result.scalars().all()
            if not chunks:
                return None
            idx = BM25Index()
            for c in chunks:
                if c.content:
                    idx.add_doc(int(c.id), c.content)
            self._bm25_cache[kb_id] = idx
            self._bm25_last_rebuild[kb_id] = now
            return idx
        except Exception as exc:
            logger.warning("构建 BM25 索引失败: %s", exc)
            return None

    # ---------- Phase 4: 索引管理 ----------
    async def index_document_for_search(
        self,
        chunk_id: int,
        content: str,
        embedding: Optional[List[float]] = None,
    ) -> bool:
        """
        为文档块创建索引 (Phase 4)

        包括:
        1. 向量索引 (pgvector)
        2. 全文索引 (FTS)

        Args:
            chunk_id: 文档块 ID
            content: 文档内容
            embedding: 向量 (可选)

        Returns:
            是否成功
        """
        if not self._use_postgres_search:
            logger.info("当前为降级模式，跳过 PostgreSQL 索引创建")
            return False

        success = True

        # 1. 创建向量索引
        if embedding and self.pg_vector_store:
            try:
                await self.pg_vector_store.add_vector(chunk_id, embedding)
            except Exception as e:
                logger.error(f"创建向量索引失败 chunk_id={chunk_id}: {e}")
                success = False

        # 2. 创建全文索引
        if self.pg_fts:
            try:
                await self.pg_fts.index_document(chunk_id, content)
            except Exception as e:
                logger.error(f"创建全文索引失败 chunk_id={chunk_id}: {e}")
                success = False

        return success

    async def batch_index_documents(
        self,
        documents: List[Tuple[int, str, Optional[List[float]]]],
    ) -> Dict[str, Any]:
        """
        批量为文档块创建索引 (Phase 4)

        Args:
            documents: [(chunk_id, content, embedding), ...]

        Returns:
            操作结果统计
        """
        if not self._use_postgres_search:
            return {"success": False, "reason": "降级模式"}

        success_count = 0
        fail_count = 0

        for chunk_id, content, embedding in documents:
            try:
                await self.index_document_for_search(chunk_id, content, embedding)
                success_count += 1
            except Exception as e:
                logger.error(f"索引文档失败 chunk_id={chunk_id}: {e}")
                fail_count += 1

        return {
            "success": fail_count == 0,
            "total": len(documents),
            "success_count": success_count,
            "fail_count": fail_count,
        }

    # ---------- 辅助: 关键词提取 ----------
    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        if not text:
            return []
        tokens = []
        for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text):
            tokens.append(w.lower())
        for seg in re.findall(r"[\u4e00-\u9fa5]+", text):
            for i in range(len(seg) - 1):
                tokens.append(seg[i:i + 2])
            if len(seg) >= 3:
                for i in range(len(seg) - 2):
                    tokens.append(seg[i:i + 3])
        seen = set()
        unique = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique[:50]

    @staticmethod
    def _keyword_overlap_score(chunk_content: str, query_tokens: List[str]) -> float:
        if not query_tokens or not chunk_content:
            return 0.0
        lower = chunk_content.lower()
        hits = sum(1 for t in query_tokens if t in lower)
        return float(hits) / float(len(query_tokens))

    # ---------- 合并逻辑 ----------
    @staticmethod
    def _merge_overlapping(hits: List[Dict[str, Any]], threshold: float = 0.7) -> List[Dict[str, Any]]:
        if not hits:
            return hits
        kept: List[Dict[str, Any]] = []
        for hit in hits:
            merged = False
            for k in kept:
                if RetrievalService._text_overlap(hit["content"], k["content"]) >= threshold:
                    if len(hit["content"]) > len(k["content"]):
                        k["content"] = hit["content"]
                    k["vector_score"] = max(k["vector_score"], hit["vector_score"])
                    k["keyword_score"] = max(k["keyword_score"], hit["keyword_score"])
                    k["final_score"] = max(k["final_score"], hit["final_score"])
                    merged = True
                    break
            if not merged:
                kept.append(hit)
        return kept

    @staticmethod
    def _merge_hits(hits: List[RetrievedHit], threshold: float = 0.7) -> List[RetrievedHit]:
        """合并 RetrievedHit 列表中的重叠内容"""
        if not hits:
            return hits
        kept: List[RetrievedHit] = []
        for hit in hits:
            merged = False
            for k in kept:
                if RetrievalService._text_overlap(hit.content, k.content) >= threshold:
                    if len(hit.content) > len(k.content):
                        k.content = hit.content
                    k.vector_score = max(k.vector_score, hit.vector_score)
                    k.bm25_score = max(k.bm25_score, hit.bm25_score)
                    k.final_score = max(k.final_score, hit.final_score)
                    merged = True
                    break
            if not merged:
                kept.append(hit)
        # 重新排序
        kept.sort(key=lambda x: x.final_score, reverse=True)
        for i, hit in enumerate(kept, 1):
            hit.rank = i
        return kept

    @staticmethod
    def _text_overlap(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        sa, sb = set(a), set(b)
        inter = len(sa & sb)
        union = len(sa | sb)
        return float(inter) / float(union) if union > 0 else 0.0

    # ---------- 辅助: 按需重建向量索引 ----------
    async def _rebuild_if_needed(self, kb_id: int) -> None:
        from app.services.document_service import DocumentService

        result = await self.db.execute(select(func.count()).select_from(Chunk).where(Chunk.knowledge_base_id == kb_id))
        total_chunks = result.scalar()
        if total_chunks == 0:
            return

        ds = DocumentService(self.db)
        await ds._rebuild_vector_index(kb_id)
        logger.info("检索服务: 为 KB=%d 重建了 %d 个 chunk 的向量索引", kb_id, total_chunks)

    # ---------- 管理接口：状态查询 ----------
    async def get_kb_stats(self, kb_id: int, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        kb = await self._can_access_kb(kb_id, user_id)
        if kb is None:
            return None

        result = await self.db.execute(select(func.count()).select_from(Chunk).where(Chunk.knowledge_base_id == kb_id))
        total_chunks = result.scalar()
        result = await self.db.execute(select(func.count()).select_from(Document).where(Document.knowledge_base_id == kb_id))
        total_docs = result.scalar()

        store_status = self.vector_manager.get_status(kb_id)
        
        # Phase 4: PostgreSQL 统计
        postgres_stats = None
        if self._use_postgres_search and self.pg_vector_store:
            try:
                postgres_stats = await self.pg_vector_store.stats(kb_id)
            except Exception:
                pass

        return {
            "kb_id": kb_id,
            "kb_name": kb.name,
            "is_public": bool(kb.is_public),
            "total_documents": int(total_docs),
            "total_chunks": int(total_chunks),
            "vector_store": store_status,
            "postgres_vector_store": postgres_stats,
            "search_mode": "postgresql_native" if self._use_postgres_search else "fallback_faiss",
            "embedding_dim": self.embedding.dim,
        }

    # ---------- 管理接口：重建索引 ----------
    async def rebuild_index(self, kb_id: int, user_id: int) -> bool:
        result = await self.db.execute(select(KnowledgeBase).where(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.user_id == user_id,
        ))
        kb = result.scalars().first()
        if kb is None:
            return False

        if self.vector_manager.has_store(kb_id):
            self.vector_manager.delete(kb_id)
        from app.services.document_service import DocumentService
        ds = DocumentService(self.db)
        await ds._rebuild_vector_index(kb_id)
        return True

    # ---------- Phase 4: 数据库初始化 ----------
    @staticmethod
    async def initialize_postgres_search(db: AsyncSession) -> None:
        """
        初始化 PostgreSQL 检索环境

        需要在应用启动时调用一次:
        1. 创建 pgvector 扩展
        2. 创建必要的索引
        """
        if _is_sqlite:
            logger.info("SQLite 模式，跳过 PostgreSQL 初始化")
            return

        try:
            from app.services.pg_vector_store import init_pgvector_extension
            await init_pgvector_extension(db)
            logger.info("PostgreSQL 检索环境初始化成功")
        except Exception as e:
            logger.error(f"PostgreSQL 检索环境初始化失败: {e}")


__all__ = ["RetrievedHit", "RetrievalService", "BM25Index"]
