"""Embedding API - 文本向量化接口"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from app.core.config import settings
from app.core.logging import logger
from app.models.response import ApiResponse
from app.models.schemas import (
    EncodeRequest,
    EncodeSingleRequest,
    EncodingInfo,
    EncodeResponse,
    SimilarityRequest,
    SimilarityResponse,
    EmbeddingStatus,
)
from app.processors.embedding.embedding_service import (
    CachingEmbeddingProvider,
    EmbeddingService,
    cosine_similarity,
)


router = APIRouter(prefix="/embeddings", tags=["Embeddings"])


# ---------- 单例（实际生产推荐用依赖注入池） ----------
_service_singleton: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    """获取 EmbeddingService 单例（带缓存）"""
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = EmbeddingService.from_settings(settings)
        logger.info(
            "EmbeddingService initialized: provider=%s, dim=%d",
            _service_singleton.provider_name,
            _service_singleton.dim,
        )
    return _service_singleton


def _extract_cache_stats(
    service: EmbeddingService,
) -> Optional[Dict[str, Any]]:
    """如果外层是 Caching Provider，返回统计信息"""
    provider = getattr(service, "_provider", None)
    if isinstance(provider, CachingEmbeddingProvider):
        return provider.stats()
    return None


def _interpret_similarity(score: float) -> str:
    """根据余弦相似度返回文字描述"""
    if score >= 0.85:
        return "语义高度相似（近乎复述）"
    if score >= 0.6:
        return "语义较相似（同一主题）"
    if score >= 0.35:
        return "存在部分语义关联"
    if score >= 0.15:
        return "语义较弱关联"
    return "语义几乎无关"


# ---------- API ----------

@router.get("/status", response_model=ApiResponse[EmbeddingStatus])
def get_status(
    service: EmbeddingService = Depends(get_embedding_service),
):
    """获取当前 Embedding 服务的状态与质量速览"""
    report = service.quality_report()
    return ApiResponse[EmbeddingStatus](
        data=EmbeddingStatus(
            provider=report["provider"],
            dim=report["dim"],
            caching_enabled=isinstance(
                getattr(service, "_provider", None),
                CachingEmbeddingProvider,
            ),
            sample_similarity_matrix=report["cosine_similarity_matrix"],
            sample_texts=[
                "RAG (Retrieval-Augmented Generation) 结合了检索与语言模型生成。",
                "向量数据库存储高维向量用于相似度搜索。",
                "大语言模型如 GPT 和 Llama 用于文本生成任务。",
                "文档分块是将长文本切分为较小片段以便向量化。",
                "Python 是一种解释型高级编程语言。",
            ],
        )
    )


@router.post("/encode", response_model=ApiResponse[EncodeResponse])
def encode_texts(
    payload: EncodeRequest,
    service: EmbeddingService = Depends(get_embedding_service),
):
    """对一组文本进行向量化

    - 返回向量维度、L2 范数、前若干维预览
    - 不返回完整向量（避免响应过大）；完整向量在后台使用
    """
    texts = payload.texts
    vectors = service.encode(texts)
    dim = service.dim
    preview_n = min(5, dim)

    items: List[EncodingInfo] = []
    for vec in vectors:
        norm = math.sqrt(sum(x * x for x in vec))
        items.append(
            EncodingInfo(
                dim=dim,
                norm=round(norm, 4),
                sample_preview=[round(x, 4) for x in vec[:preview_n]],
            )
        )

    return ApiResponse[EncodeResponse](
        data=EncodeResponse(
            provider=service.provider_name,
            dim=dim,
            count=len(items),
            items=items,
            cache_stats=_extract_cache_stats(service),
        )
    )


@router.post("/encode-single", response_model=ApiResponse[EncodeResponse])
def encode_single_text(
    payload: EncodeSingleRequest,
    service: EmbeddingService = Depends(get_embedding_service),
):
    """单文本向量化"""
    vec = service.encode_single(payload.text)
    dim = service.dim
    preview_n = min(5, dim)
    norm = math.sqrt(sum(x * x for x in vec))

    return ApiResponse[EncodeResponse](
        data=EncodeResponse(
            provider=service.provider_name,
            dim=dim,
            count=1,
            items=[
                EncodingInfo(
                    dim=dim,
                    norm=round(norm, 4),
                    sample_preview=[round(x, 4) for x in vec[:preview_n]],
                )
            ],
            cache_stats=_extract_cache_stats(service),
        )
    )


@router.post("/similarity", response_model=ApiResponse[SimilarityResponse])
def compute_similarity(
    payload: SimilarityRequest,
    service: EmbeddingService = Depends(get_embedding_service),
):
    """计算两段文本的语义相似度（基于余弦相似度）"""
    vec_a = service.encode_single(payload.text_a)
    vec_b = service.encode_single(payload.text_b)
    score = cosine_similarity(vec_a, vec_b)

    return ApiResponse[SimilarityResponse](
        data=SimilarityResponse(
            provider=service.provider_name,
            score=round(float(score), 4),
            interpretation=_interpret_similarity(float(score)),
        )
    )


__all__ = ["router", "get_embedding_service"]