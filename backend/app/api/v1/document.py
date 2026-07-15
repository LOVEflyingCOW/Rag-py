"""Document API 路由

提供文档的上传、列表、详情、分块、删除，以及向量搜索接口。
"""
from __future__ import annotations

import time
from typing import Optional, List

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, Query, Form

from app.api.dependencies import get_db_dep, get_current_user, get_current_user_optional
from app.models.response import ApiResponse
from app.models.schemas import DocumentInfo, DocumentListResponse, DocumentUploadResponse, ChunkInfo, SearchResponse
from app.models.entities.user import User
from app.services.document_service import DocumentService

router = APIRouter(prefix="/knowledge-bases/{kb_id}/documents", tags=["Documents"])


# ============================================================
# 上传文档
# ============================================================
@router.post("", response_model=ApiResponse[DocumentUploadResponse])
def upload_document(
    kb_id: int,
    file: UploadFile = File(..., description="要上传的文件 (.txt, .md, .pdf, .doc, .docx 等)"),
    chunk_size: Optional[int] = Form(None, ge=100, le=2000, description="分块大小（字符），默认使用知识库配置"),
    chunk_overlap: Optional[int] = Form(None, ge=0, le=500, description="分块重叠（字符），默认使用知识库配置"),
    current_user: User = Depends(get_current_user),
    db=Depends(get_db_dep),
):
    """上传文档到知识库（仅知识库所有者）

    - 自动提取文本
    - 自动按配置的 chunk_size/chunk_overlap 分块
    - 自动向量化并建立 FAISS 索引
    - 上传后可以立即使用 /search 接口进行语义搜索
    """
    content = file.file.read()
    filename = file.filename or "unnamed.txt"

    service = DocumentService(db)
    try:
        doc = service.process_upload(
            kb_id=kb_id,
            user_id=current_user.id,
            filename=filename,
            file_content=content,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="文档处理失败: %s" % str(e),
        )

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在或无权访问",
        )

    return ApiResponse[DocumentUploadResponse](
        data=DocumentUploadResponse(
            document=DocumentInfo(
                id=doc.id,
                knowledge_base_id=doc.knowledge_base_id,
                filename=doc.filename,
                file_type=doc.file_type,
                file_size=doc.file_size,
                status=doc.status,
                total_chunks=doc.total_chunks,
                created_at=doc.created_at,
                updated_at=doc.updated_at,
            ),
            message="文档处理完成: %s, 共 %d 个分块" % (doc.status, doc.total_chunks),
        )
    )


# ============================================================
# 文档列表
# ============================================================
@router.get("", response_model=ApiResponse[DocumentListResponse])
def list_documents(
    kb_id: int,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db=Depends(get_db_dep),
):
    """获取知识库的文档列表

    - 未登录用户: 仅能查看公开知识库
    - 已登录用户: 可以查看自己的和公开知识库
    """
    user_id = current_user.id if current_user else None
    service = DocumentService(db)
    docs, total = service.list_documents(kb_id, user_id=user_id, page=page, page_size=page_size)

    if docs == [] and total == 0:
        # 检查是否有权限问题: 先看知识库是否存在且可访问
        from app.models.entities.knowledge_base import KnowledgeBase
        kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if kb is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="知识库不存在",
            )
        can_access = (user_id is not None and kb.user_id == user_id) or kb.is_public
        if not can_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权访问该知识库",
            )

    return ApiResponse[DocumentListResponse](
        data=DocumentListResponse(
            items=[
                DocumentInfo(
                    id=d.id,
                    knowledge_base_id=d.knowledge_base_id,
                    filename=d.filename,
                    file_type=d.file_type,
                    file_size=d.file_size,
                    status=d.status,
                    total_chunks=d.total_chunks,
                    created_at=d.created_at,
                    updated_at=d.updated_at,
                )
                for d in docs
            ],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


# ============================================================
# 文档详情
# ============================================================
@router.get("/{doc_id}", response_model=ApiResponse[DocumentInfo])
def get_document(
    kb_id: int,
    doc_id: int,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db=Depends(get_db_dep),
):
    """获取单个文档详情"""
    user_id = current_user.id if current_user else None
    service = DocumentService(db)
    doc = service.get_document(doc_id, user_id=user_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在或无权访问",
        )
    return ApiResponse[DocumentInfo](
        data=DocumentInfo(
            id=doc.id,
            knowledge_base_id=doc.knowledge_base_id,
            filename=doc.filename,
            file_type=doc.file_type,
            file_size=doc.file_size,
            status=doc.status,
            total_chunks=doc.total_chunks,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
    )


# ============================================================
# 文档分块列表
# ============================================================
@router.get("/{doc_id}/chunks", response_model=ApiResponse[dict])
def get_document_chunks(
    kb_id: int,
    doc_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db=Depends(get_db_dep),
):
    """获取文档的分块列表"""
    user_id = current_user.id if current_user else None
    service = DocumentService(db)
    chunks, total = service.get_document_chunks(doc_id, user_id=user_id, page=page, page_size=page_size)

    if chunks == [] and total == 0:
        doc = service.get_document(doc_id, user_id=user_id)
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="文档不存在或无权访问",
            )

    return ApiResponse[dict](
        data={
            "items": [
                ChunkInfo(
                    id=c.id,
                    document_id=c.document_id,
                    content=c.content,
                    chunk_index=c.chunk_index,
                    vector_index=c.vector_index,
                )
                for c in chunks
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


# ============================================================
# 删除文档
# ============================================================
@router.delete("/{doc_id}", response_model=ApiResponse[dict])
def delete_document(
    kb_id: int,
    doc_id: int,
    current_user: User = Depends(get_current_user),
    db=Depends(get_db_dep),
):
    """删除文档（仅知识库所有者）"""
    service = DocumentService(db)
    success = service.delete_document(doc_id, user_id=current_user.id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文档不存在或无权删除",
        )
    return ApiResponse[dict](
        data={"message": "删除成功", "doc_id": doc_id}
    )


# ============================================================
# 向量搜索
# ============================================================
@router.post("/search", response_model=ApiResponse[SearchResponse])
def search_knowledge_base(
    kb_id: int,
    query: str = Query(..., min_length=1, max_length=500, description="搜索查询文本"),
    top_k: int = Query(5, ge=1, le=50, description="返回最相关的结果数量"),
    min_score: float = Query(0.0, ge=0.0, le=1.0, description="最小相似度分数 (0-1)"),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db=Depends(get_db_dep),
):
    """在知识库中进行语义搜索

    - 使用向量相似度进行匹配
    - 返回最相关的分块（chunk）及分数
    - 未登录用户: 仅可搜索公开知识库
    - 已登录用户: 可以搜索自己的和公开知识库
    """
    user_id = current_user.id if current_user else None
    service = DocumentService(db)

    t0 = time.time()
    results = service.search_in_kb(
        kb_id=kb_id,
        user_id=user_id,
        query_text=query,
        top_k=top_k,
        min_score=min_score,
    )
    elapsed_ms = (time.time() - t0) * 1000

    # 如果没有结果，检查是否有权限问题
    if not results:
        from app.models.entities.knowledge_base import KnowledgeBase
        kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()
        if kb is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="知识库不存在",
            )
        can_access = (user_id is not None and kb.user_id == user_id) or kb.is_public
        if not can_access:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权访问该知识库",
            )

    return ApiResponse[SearchResponse](
        data=SearchResponse(
            query=query,
            results=[
                {
                    "chunk_id": r["chunk_id"],
                    "document_id": r["document_id"],
                    "knowledge_base_id": r["knowledge_base_id"],
                    "content": r["content"],
                    "score": r["score"],
                    "document_filename": r["document_filename"],
                }
                for r in results
            ],
            total=len(results),
            search_time_ms=elapsed_ms,
        )
    )