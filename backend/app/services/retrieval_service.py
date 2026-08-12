from __future__ import annotations

"""检索服务 —— 提供统一的向量搜索 + BM25 文本搜索 + 混合重排能力

能力清单:
1. 向量相似度搜索 (FAISS / PurePython)
2. BM25 文本搜索 (基于倒排 + IDF 打分，纯 Python)
3. 规则级重排序 (lexical overlap / keyword match score)
4. 混合重排 (Vector + BM25 + lexical 三路融合)
5. 上下文合并 (合并相近 / 高度重叠的 chunk)
6. 知识库状态查询 / 向量索引重建
"""

import math
import re
import time
from typing import List, Dict, Optional, Any, Tuple
from dataclasses import dataclass
from collections import Counter as _Counter

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.models.entities.document import Document, DocumentChunk
from app.models.entities.knowledge_base import KnowledgeBase
from app.processors import EmbeddingService, VectorStoreManager

Chunk = DocumentChunk


@dataclass
class RetrievedHit:
    """检索命中结果"""
    chunk_id: int
    document_id: int
    knowledge_base_id: int
    content: str
    document_filename: str
    vector_score: float        # 向量相似度 ∈ [0, 1]
    bm25_score: float          # BM25 文本匹配分 ∈ [0, 1]
    keyword_score: float       # 关键词重叠分 ∈ [0, 1]
    final_score: float         # 综合分 ∈ [0, 1]
    rank: int                  # 重排序之后的排名


# ============ BM25 文本索引（纯 Python，零依赖） ============

class BM25Index:
    """Okapi BM25 轻量级实现
    - 支持中英文混合 token（英文按词，中文按字/ngram）
    - 支持增量 add / query / save/load
    - 返回 0~1 归一化分数
    """
    K1 = 1.5
    B = 0.75
    EPS = 1e-6

    def __init__(self):
        self._docs: List[List[str]] = []         # docs[doc_id] = token list
        self._doc_len: List[int] = []            # docs length
        self._avg_len: float = 0.0
        self._n_docs: int = 0
        self._df: Dict[str, int] = {}            # term -> doc frequency
        self._idf: Dict[str, float] = {}         # term -> idf
        self._external_ids: Dict[int, int] = {}  # external_chunk_id -> internal_doc_id

    # --- 分词 ---
    @staticmethod
    def tokenize(text: str) -> List[str]:
        if not text:
            return []
        tokens: List[str] = []
        # 英文词 & 数字
        for m in re.finditer(r"[A-Za-z][A-Za-z0-9\-']{1,}|[0-9]+", text):
            tokens.append(m.group(0).lower())
        # 中文：连续 1~2 字窗口，增加召回
        for seg in re.findall(r"[\u4e00-\u9fa5]+", text):
            if len(seg) == 1:
                tokens.append(seg)
            else:
                for i in range(len(seg) - 1):
                    tokens.append(seg[i:i + 2])
                # 加单字（当短文本时）
                if len(seg) <= 4:
                    for ch in seg:
                        tokens.append(ch)
        # 去停用词（粗略：1 英文字符 或 非中英数 的符号）
        filtered: List[str] = []
        for t in tokens:
            if len(t) == 1 and re.match(r"[A-Za-z]", t):
                continue
            if t in {"的", "了", "是", "在", "和", "有", "也", "就", "这", "那", "吗", "呢", "吧", "and", "the", "a", "an", "is", "are", "of", "to", "for"}:
                continue
            filtered.append(t)
        return filtered

    # --- add ---
    def add_doc(self, external_id: int, content: str) -> None:
        tokens = self.tokenize(content)
        internal_id = self._n_docs
        self._external_ids[external_id] = internal_id
        self._docs.append(tokens)
        self._doc_len.append(len(tokens))
        self._n_docs += 1
        self._avg_len = sum(self._doc_len) / self._n_docs
        # 更新 df
        for t in set(tokens):
            self._df[t] = self._df.get(t, 0) + 1
        # 重算 idf
        for t, dfv in self._df.items():
            self._idf[t] = math.log((self._n_docs - dfv + 0.5) / (dfv + 0.5) + 1.0)

    # --- score ---
    def score(self, query_text: str, external_ids: Optional[List[int]] = None) -> Dict[int, float]:
        """对给定查询计算每个文档的 BM25 分数。
        返回: {external_id: raw_score}
        """
        q_tokens = self.tokenize(query_text)
        if not q_tokens or self._n_docs == 0:
            return {}
        avg_len = self._avg_len
        results: Dict[int, float] = {}
        # 只在候选集合上打分
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
        """返回 0~1 归一化的分数"""
        raw = self.score(query_text, external_ids)
        if not raw:
            return {}
        max_v = max(raw.values())
        if max_v <= self.EPS:
            return {k: 0.0 for k in raw}
        return {k: float(v) / max_v for k, v in raw.items()}


class RetrievalService:
    """检索服务（含 BM25 + 向量 + lexical 三重混合重排）"""

    # 混合检索权重（vector + bm25 + keyword，三者和=1）
    VECTOR_WEIGHT = 0.50
    BM25_WEIGHT = 0.35
    KEYWORD_WEIGHT = 0.15

    def __init__(self, db: Session):
        self.db = db
        self._embedding: Optional[EmbeddingService] = None
        self._vector_manager: Optional[VectorStoreManager] = None
        self._bm25_cache: Dict[int, BM25Index] = {}  # kb_id -> BM25 索引
        self._bm25_last_rebuild: Dict[int, float] = {}

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

    # ---------- 权限检查 ----------
    def _can_access_kb(self, kb_id: int, user_id: Optional[int]) -> Optional[KnowledgeBase]:
        kb = self.db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if kb is None:
            return None
        if kb.is_public:
            return kb
        if user_id is not None and kb.user_id == user_id:
            return kb
        return None

    # ---------- 核心: 向量搜索 + 重排序 ----------
    def search(
        self,
        kb_id: int,
        query_text: str,
        user_id: Optional[int] = None,
        top_k: int = 5,
        min_score: float = 0.0,
        enable_rerank: bool = True,
        enable_merge: bool = False,
    ) -> List[RetrievedHit]:
        """完整检索流程

        Args:
            kb_id             : 目标知识库 ID
            query_text         : 查询文本
            user_id            : 当前用户（None 表示未登录）
            top_k              : 返回 top-k 结果
            min_score          : 最低分过滤
            enable_rerank      : 是否开启"向量 + 关键词"重排序
            enable_merge       : 是否合并高度重叠的相邻 chunk

        Returns:
            排序后的 RetrievedHit 列表
        """
        if not query_text or not query_text.strip():
            return []

        # 权限
        kb = self._can_access_kb(kb_id, user_id)
        if kb is None:
            return []

        # 获取/重建向量索引
        store = self.vector_manager.get_store(kb_id, dim=self.embedding.dim)
        if store.total() == 0:
            # 首次使用：尝试从 DB chunks 重建索引
            self._rebuild_if_needed(kb_id)
            store = self.vector_manager.get_store(kb_id, dim=self.embedding.dim)
            if store.total() == 0:
                return []

        # 1) 查询向量化
        query_vec = self.embedding.encode_single(query_text)
        if not query_vec:
            return []

        # 2) 向量粗搜索 —— 取 top-k*5 作为候选集（更多候选=重排收益更高）
        candidate_count = max(top_k * 5, 20)
        raw_vec = store.search(query_vec, top_k=candidate_count)

        # 3) 组装候选集（从 vector 结果 + metadata 中）
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
                chunk_from_db = self.db.query(Chunk).filter(Chunk.id == int(chunk_id)).first()
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

        # 4) BM25 检索：从 DB 中取全部 chunks（按 kb_id 过滤）做全文检索
        #    —— 取向量候选集的 top-k 做 BM25 打分（避免对所有 chunk 全文扫描）
        #    —— 同时：从 DB 中额外取 50 个包含 query 关键词的 chunks 作为补充候选
        if enable_rerank:
            bm25 = self._get_or_build_bm25(kb_id)
            if bm25 is not None and bm25._n_docs > 0:
                # (a) 对 vector 候选集做 BM25 重打分
                vector_candidates = list(by_chunk_id.keys())
                bm25_norm = bm25.score_normalized(query_text, vector_candidates)
                for cid, s in bm25_norm.items():
                    if cid in by_chunk_id:
                        by_chunk_id[cid]["bm25_score"] = float(s)

                # (b) 补充：对 DB 中其他包含关键词的 chunks 做 BM25 + 向量搜索
                # 先从 DB 中查询（keyword 快速过滤），然后 BM25 打分，
                # 只取那些分数高的补充进候选（不会超过 candidate_count 个新条目）
                if len(by_chunk_id) < candidate_count:
                    bm25_all = bm25.score_normalized(query_text)
                    for ext_id, bm_s in sorted(bm25_all.items(), key=lambda x: -x[1])[:candidate_count]:
                        if ext_id in by_chunk_id:
                            continue
                        if bm_s <= 0.15:  # 低于此阈值的不考虑
                            continue
                        # 从 DB 获取 chunk 内容
                        chunk_db = self.db.query(Chunk).filter(Chunk.id == int(ext_id)).first()
                        if chunk_db is None:
                            continue
                        # 做一次向量搜索补全分数（懒：如果此 chunk 在 store 中能找到就用其分数，否则 0）
                        doc = self.db.query(Document).filter(Document.id == chunk_db.document_id).first()
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

        # 5) 关键词重叠分（轻量 lexical rerank）
        if enable_rerank:
            query_tokens = self._extract_keywords(query_text)
            for hit in by_chunk_id.values():
                hit["keyword_score"] = self._keyword_overlap_score(hit["content"], query_tokens)

        # 6) 三路综合分
        for hit in by_chunk_id.values():
            hit["final_score"] = (
                self.VECTOR_WEIGHT * hit["vector_score"]
                + self.BM25_WEIGHT * hit["bm25_score"]
                + self.KEYWORD_WEIGHT * hit["keyword_score"]
            )

        # 7) 排序 + min_score 过滤
        hits_data = list(by_chunk_id.values())
        hits_data.sort(key=lambda x: x["final_score"], reverse=True)
        hits_data = [h for h in hits_data if h["final_score"] >= min_score]

        # 8) 可选: 合并高重叠的 chunk
        if enable_merge and len(hits_data) > 1:
            hits_data = self._merge_overlapping(hits_data)

        # 9) 组装 RetrievedHit
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
            ))

        return results

    # ---------- BM25 索引构建（懒加载 + 定时重建） ----------
    def _get_or_build_bm25(self, kb_id: int, rebuild_interval_sec: float = 600.0) -> Optional[BM25Index]:
        """按需构建/刷新某个知识库的 BM25 索引"""
        now = time.time()
        last = self._bm25_last_rebuild.get(kb_id, 0.0)
        if kb_id in self._bm25_cache and (now - last) < rebuild_interval_sec:
            return self._bm25_cache[kb_id]

        # 从 DB 加载所有 chunks
        try:
            chunks = self.db.query(Chunk).filter(Chunk.knowledge_base_id == kb_id).all()
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
            _logger = __import__("logging").getLogger("retrieval")
            _logger.warning("构建 BM25 索引失败: %s", exc)
            return None

    # ---------- 辅助: 关键词提取 ----------
    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """极简关键词提取（中英文混合）"""
        if not text:
            return []
        tokens = []
        # 英文词
        for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{2,}", text):
            tokens.append(w.lower())
        # 中文连续 2+ 字片段（简单切分，足够作 lexical overlap 用）
        for seg in re.findall(r"[\u4e00-\u9fa5]+", text):
            for i in range(len(seg) - 1):
                tokens.append(seg[i:i + 2])
            if len(seg) >= 3:
                for i in range(len(seg) - 2):
                    tokens.append(seg[i:i + 3])
        # 去重
        seen = set()
        unique = []
        for t in tokens:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique[:50]

    # ---------- 辅助: 关键词重叠分 ----------
    @staticmethod
    def _keyword_overlap_score(chunk_content: str, query_tokens: List[str]) -> float:
        """Jaccard-like 相似度 —— query 中的 token 在 chunk 中出现的比例"""
        if not query_tokens or not chunk_content:
            return 0.0
        lower = chunk_content.lower()
        hits = sum(1 for t in query_tokens if t in lower)
        return float(hits) / float(len(query_tokens))

    # ---------- 辅助: 合并重叠 chunk ----------
    @staticmethod
    def _merge_overlapping(hits: List[Dict[str, Any]], threshold: float = 0.7) -> List[Dict[str, Any]]:
        """合并高度文本重叠的相邻 chunk（去冗余）"""
        if not hits:
            return hits
        kept: List[Dict[str, Any]] = []
        for hit in hits:
            merged = False
            for k in kept:
                if RetrievalService._text_overlap(hit["content"], k["content"]) >= threshold:
                    # 合并：取更长的；保留最高分
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
    def _text_overlap(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        sa, sb = set(a), set(b)
        inter = len(sa & sb)
        union = len(sa | sb)
        return float(inter) / float(union) if union > 0 else 0.0

    # ---------- 辅助: 按需重建向量索引 ----------
    def _rebuild_if_needed(self, kb_id: int) -> None:
        """若向量索引为空但 DB 中有 chunks → 重建"""
        from app.services.document_service import DocumentService  # 避免循环 import

        total_chunks = self.db.query(Chunk).filter(Chunk.knowledge_base_id == kb_id).count()
        if total_chunks == 0:
            return

        ds = DocumentService(self.db)
        ds._rebuild_vector_index(kb_id)  # type: ignore[attr-defined]
        logger.info("检索服务: 为 KB=%d 重建了 %d 个 chunk 的向量索引", kb_id, total_chunks)

    # ---------- 管理接口：状态查询 ----------
    def get_kb_stats(self, kb_id: int, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """查询知识库的检索状态"""
        kb = self._can_access_kb(kb_id, user_id)
        if kb is None:
            return None

        total_chunks = self.db.query(Chunk).filter(Chunk.knowledge_base_id == kb_id).count()
        total_docs = self.db.query(Document).filter(Document.knowledge_base_id == kb_id).count()

        store_status = self.vector_manager.get_status(kb_id)

        return {
            "kb_id": kb_id,
            "kb_name": kb.name,
            "is_public": bool(kb.is_public),
            "total_documents": int(total_docs),
            "total_chunks": int(total_chunks),
            "vector_store": store_status,
            "embedding_dim": self.embedding.dim,
        }

    # ---------- 管理接口：重建索引 ----------
    def rebuild_index(self, kb_id: int, user_id: int) -> bool:
        """重建某个知识库的向量索引（仅所有者可操作）"""
        kb = self.db.query(KnowledgeBase).filter(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.user_id == user_id,
        ).first()
        if kb is None:
            return False

        # 清除现有 + 重建
        if self.vector_manager.has_store(kb_id):
            self.vector_manager.delete(kb_id)
        from app.services.document_service import DocumentService
        DocumentService(self.db)._rebuild_vector_index(kb_id)  # type: ignore[attr-defined]
        return True


__all__ = ["RetrievedHit", "RetrievalService"]