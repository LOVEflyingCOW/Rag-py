"""Retrieval API - 向量索引管理与搜索"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import settings
from app.models.response import ApiResponse
from app.models.schemas import (
    VectorSearchQuery,
    VectorSearchItem,
    VectorSearchResponse,
    IndexStatusResponse,
    IndexOperationResponse,
    GlobalIndexStatusResponse,
)
from app.processors import (
    EmbeddingService,
    VectorStoreManager,
    _HAS_FAISS,
    _HAS_NUMPY,
)

router = APIRouter(prefix="/retrieval", tags=["Retrieval"])

# 全局 VectorStoreManager 单例
_manager_singleton: Optional[VectorStoreManager] = None
_embedding_singleton: Optional[EmbeddingService] = None


def _get_manager() -> VectorStoreManager:
    global _manager_singleton
    if _manager_singleton is None:
        _manager_singleton = VectorStoreManager(
            base_dir=settings.VECTOR_STORE_DIR,
            default_dim=settings.EMBEDDING_DEFAULT_DIM,
            prefer_faiss=True,
        )
    return _manager_singleton


def _get_embedding() -> EmbeddingService:
    global _embedding_singleton
    if _embedding_singleton is None:
        _embedding_singleton = EmbeddingService.from_settings(settings)
    return _embedding_singleton


# ---------- 全局索引状态 ----------
@router.get("/status", response_model=ApiResponse[GlobalIndexStatusResponse])
def get_global_status(
    manager: VectorStoreManager = Depends(_get_manager),
):
    """全局索引状态 - 列出磁盘上所有知识库索引"""
    stored = manager.list_stored_kbs()
    return ApiResponse[GlobalIndexStatusResponse](
        data=GlobalIndexStatusResponse(
            base_dir=manager.base_dir,
            total_kbs_on_disk=len(stored),
            stored_kbs=stored,
            faiss_available=bool(_HAS_FAISS),
            numpy_available=bool(_HAS_NUMPY),
            default_dim=manager.default_dim,
        )
    )


# ---------- 知识库索引状态 ----------
@router.get("/index/{kb_id}", response_model=ApiResponse[IndexStatusResponse])
def get_kb_index_status(
    kb_id: int,
    manager: VectorStoreManager = Depends(_get_manager),
):
    """查询某个知识库的向量索引状态"""
    st = manager.get_status(kb_id)
    return ApiResponse[IndexStatusResponse](
        data=IndexStatusResponse(
            knowledge_base_id=kb_id,
            exists=st.get("exists", False),
            loaded=st.get("loaded", False),
            consistent=st.get("consistent"),
            backend=st.get("backend"),
            dim=st.get("dim"),
            total_vectors=st.get("total_vectors"),
            next_index=st.get("next_index"),
            metadata_count=st.get("metadata_count"),
            nlist=st.get("nlist"),
            nprobe=st.get("nprobe"),
            is_trained=st.get("is_trained"),
            ntotal=st.get("ntotal"),
            issues=st.get("issues"),
            path=st.get("path"),
        )
    )


# ---------- 强制落盘 ----------
@router.post("/index/{kb_id}/flush", response_model=ApiResponse[IndexOperationResponse])
def flush_kb_index(
    kb_id: int,
    manager: VectorStoreManager = Depends(_get_manager),
):
    """将内存中的向量索引强制写入磁盘"""
    ok = manager.save(kb_id)
    return ApiResponse[IndexOperationResponse](
        data=IndexOperationResponse(
            success=ok,
            knowledge_base_id=kb_id,
            message=("已写入磁盘" if ok else "该知识库未在内存中加载或写入失败"),
        )
    )


# ---------- 删除索引 ----------
@router.delete("/index/{kb_id}", response_model=ApiResponse[IndexOperationResponse])
def delete_kb_index(
    kb_id: int,
    manager: VectorStoreManager = Depends(_get_manager),
):
    """删除某个知识库的向量索引（同时清内存和磁盘）"""
    ok = manager.delete(kb_id)
    return ApiResponse[IndexOperationResponse](
        data=IndexOperationResponse(
            success=ok,
            knowledge_base_id=kb_id,
            message=("已删除索引" if ok else "没有可删除的索引"),
        )
    )


# ---------- 清理内存缓存 ----------
@router.post("/index/clear-memory", response_model=ApiResponse[IndexOperationResponse])
def clear_memory_cache(
    manager: VectorStoreManager = Depends(_get_manager),
):
    """释放内存中已加载的所有索引（磁盘文件保留）"""
    manager.clear_memory(None)
    return ApiResponse[IndexOperationResponse](
        data=IndexOperationResponse(
            success=True,
            knowledge_base_id=0,
            message="内存中的所有向量索引已释放",
        )
    )


# ---------- 直接向量搜索 (文本 -> 向量 -> top_k) ----------
@router.post("/search/{kb_id}", response_model=ApiResponse[VectorSearchResponse])
def vector_search(
    kb_id: int,
    payload: VectorSearchQuery,
    manager: VectorStoreManager = Depends(_get_manager),
    embedding: EmbeddingService = Depends(_get_embedding),
):
    """基于文本在指定知识库做向量相似度搜索

    - 自动编码查询文本
    - 检索 top_k 个相似 chunk
    - 过滤 score 低于 min_score 的结果
    """
    store = manager.get_store(kb_id, dim=embedding.dim)
    if store.total() == 0:
        return ApiResponse[VectorSearchResponse](
            data=VectorSearchResponse(
                knowledge_base_id=kb_id,
                query_text=payload.query_text,
                hits=0,
                items=[],
                backend=store.stats().get("backend", "unknown"),
            )
        )

    # 编码查询
    query_vec = embedding.encode_single(payload.query_text)

    # 搜索
    raw_results = store.search(query_vec, top_k=payload.top_k)

    items = []
    for vec_idx, score in raw_results:
        if score < payload.min_score:
            continue
        meta = store.get_metadata(vec_idx) or {}
        items.append(
            VectorSearchItem(
                vector_index=vec_idx,
                score=float(score),
                chunk_id=meta.get("chunk_id"),
                document_id=meta.get("document_id"),
                content_preview=(meta.get("content") or "")[:120] if meta.get("content") else None,
                metadata=meta,
            )
        )

    return ApiResponse[VectorSearchResponse](
        data=VectorSearchResponse(
            knowledge_base_id=kb_id,
            query_text=payload.query_text,
            hits=len(items),
            items=items,
            backend=store.stats().get("backend", "unknown"),
        )
    )


__all__ = ["router", "_get_manager", "_get_embedding"]