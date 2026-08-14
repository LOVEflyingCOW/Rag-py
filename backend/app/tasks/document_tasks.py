"""文档向量化 Celery 任务 — 异步处理文档上传

设计:
1. API 接收文件上传, 保存到磁盘, 创建 Document 记录 (status="processing")
2. API 调用 process_document.delay(doc_id) — 非阻塞返回
3. Celery Worker 执行: 提取文本 → 分块 → 向量化 → 写入 FAISS → 更新 DB
4. 处理完成: Document.status = "processed"
5. 处理失败: Document.status = "error", 错误信息存入 content_text

任务使用同步 DB Session (Celery 是同步进程, 避免事件循环冲突).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from app.core.celery_app import celery_app
from app.core.config import settings
from app.core.logging import logger


@celery_app.task(
    bind=True,
    name="app.tasks.document_tasks.process_document",
    queue="document",
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def process_document(
    self,
    doc_id: int,
    kb_id: int,
    file_path: str,
    filename: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> dict:
    """异步处理文档: 提取文本 → 分块 → 向量化 → 建立 FAISS 索引

    Args:
        doc_id: Document 记录 ID
        kb_id: 知识库 ID
        file_path: 文件在磁盘上的路径
        filename: 原始文件名
        chunk_size: 分块大小
        chunk_overlap: 分块重叠

    Returns:
        dict: {"success": bool, "doc_id": int, "total_chunks": int, "error": str}
    """
    try:
        from app.models.database import SessionLocal
        from app.models.entities.document import Document, DocumentChunk as Chunk
        from app.models.entities.knowledge_base import KnowledgeBase
        from app.processors import (
            DocumentProcessor,
            EmbeddingService,
            VectorStoreManager,
        )
        from sqlalchemy import select

        logger.info("CELERY_PROCESS_DOC doc_id=%d kb_id=%d file=%s", doc_id, kb_id, filename)

        db = SessionLocal()
        try:
            # 1. 读取 Document 和 KnowledgeBase
            doc = db.execute(
                select(Document).where(Document.id == doc_id)
            ).scalars().first()

            if doc is None:
                logger.error("Document %d not found", doc_id)
                return {"success": False, "doc_id": doc_id, "error": "Document not found"}

            kb = db.execute(
                select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
            ).scalars().first()

            if kb is None:
                doc.status = "error"
                doc.content_text = "Knowledge base not found"
                db.commit()
                return {"success": False, "doc_id": doc_id, "error": "KB not found"}

            # 2. 初始化处理器
            processor = DocumentProcessor(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
            embedding = EmbeddingService.from_settings(settings)
            vector_manager = VectorStoreManager(
                base_dir=settings.VECTOR_STORE_DIR,
                default_dim=embedding.dim,
            )

            # 3. 提取文本
            file_type = processor.detect_file_type(file_path)
            text = processor.extract_text(file_path, file_type)

            if not text or len(text.strip()) < 10:
                doc.status = "skipped"
                doc.total_chunks = 0
                db.commit()
                logger.info("CELERY_DOC_SKIPPED doc_id=%d (empty text)", doc_id)
                return {"success": True, "doc_id": doc_id, "total_chunks": 0, "error": None}

            doc.content_text = text[:500000]  # 限制大小

            # 4. 智能分块
            chunks_data = processor.split_chunks(
                text,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

            if not chunks_data:
                doc.status = "processed"
                doc.total_chunks = 0
                db.commit()
                return {"success": True, "doc_id": doc_id, "total_chunks": 0, "error": None}

            # 5. 向量化
            chunk_texts = [c["content"] for c in chunks_data]
            vectors = embedding.encode(chunk_texts)

            # 6. 写入 Chunk 表
            store = vector_manager.get_store(kb_id, dim=embedding.dim)
            metadata_list = []
            chunk_objs = []

            for i, (chunk_info, vec) in enumerate(zip(chunks_data, vectors)):
                chunk_obj = Chunk(
                    document_id=doc.id,
                    knowledge_base_id=kb_id,
                    content=chunk_info["content"],
                    chunk_index=chunk_info["index"],
                    vector_index=-1,
                    metadata_="",
                )
                db.add(chunk_obj)
                chunk_objs.append(chunk_obj)

                metadata_list.append({
                    "chunk_id_placeholder": i,
                    "document_id": doc.id,
                    "knowledge_base_id": kb_id,
                    "document_filename": filename,
                    "content": chunk_info["content"],
                })

            db.flush()  # 让 chunk_objs 获得真实的 id

            # 更新 metadata 中的 chunk_id
            for i, chunk_obj in enumerate(chunk_objs):
                metadata_list[i]["chunk_id"] = chunk_obj.id

            # 7. 添加到向量索引
            vector_indices = store.add(vectors, metadata_list)

            # 更新 Chunk 的 vector_index
            for chunk_obj, v_idx in zip(chunk_objs, vector_indices):
                chunk_obj.vector_index = v_idx

            # 保存向量索引到磁盘
            vector_manager.save(kb_id)

            # 8. 更新 Document 状态
            doc.status = "processed"
            doc.total_chunks = len(chunk_objs)

            # 更新知识库统计
            kb.total_documents = (kb.total_documents or 0) + 1
            kb.total_chunks = (kb.total_chunks or 0) + len(chunk_objs)

            db.commit()

            logger.info(
                "CELERY_DOC_PROCESSED doc_id=%d chunks=%d kb_id=%d",
                doc_id, len(chunk_objs), kb_id,
            )

            return {
                "success": True,
                "doc_id": doc_id,
                "total_chunks": len(chunk_objs),
                "error": None,
            }

        finally:
            db.close()

    except Exception as e:
        logger.error("CELERY_DOC_ERROR doc_id=%d error=%s", doc_id, str(e))

        # 标记文档为错误状态
        try:
            from app.models.database import SessionLocal
            from app.models.entities.document import Document
            from sqlalchemy import select

            db = SessionLocal()
            try:
                doc = db.execute(
                    select(Document).where(Document.id == doc_id)
                ).scalars().first()
                if doc:
                    doc.status = "error"
                    doc.content_text = str(e)[:2000]
                    db.commit()
            finally:
                db.close()
        except Exception:
            pass

        # 重试 (不是所有错误都值得重试)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))

        return {
            "success": False,
            "doc_id": doc_id,
            "total_chunks": 0,
            "error": str(e),
        }
