"""Document Service - 文档业务服务

整合 DocumentProcessor + EmbeddingService + VectorStoreManager
处理上传、分块、向量化、索引、搜索的完整业务流程。
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.entities.document import Document, DocumentChunk as Chunk
from app.models.entities.knowledge_base import KnowledgeBase
from app.processors import (
    DocumentProcessor,
    EmbeddingService,
    VectorStoreManager,
    SUPPORTED_EXTENSIONS,
)


class DocumentService:
    """文档服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self._processor: Optional[DocumentProcessor] = None
        self._embedding: Optional[EmbeddingService] = None
        self._vector_manager: Optional[VectorStoreManager] = None

    # ============================================================
    # 依赖的处理器（懒加载）
    # ============================================================
    @property
    def processor(self) -> DocumentProcessor:
        if self._processor is None:
            self._processor = DocumentProcessor(chunk_size=500, chunk_overlap=50)
        return self._processor

    @property
    def embedding(self) -> EmbeddingService:
        if self._embedding is None:
            self._embedding = EmbeddingService.from_settings(settings)
        return self._embedding

    @property
    def vector_manager(self) -> VectorStoreManager:
        if self._vector_manager is None:
            self._vector_manager = VectorStoreManager(
                base_dir=settings.VECTOR_STORE_DIR,
                default_dim=self.embedding.dim,
            )
        return self._vector_manager

    # ============================================================
    # 权限检查
    # ============================================================
    async def _check_kb_owner(self, kb_id: int, user_id: int) -> Optional[KnowledgeBase]:
        """检查用户是否为知识库所有者"""
        result = await self.db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id,
                KnowledgeBase.user_id == user_id,
            )
        )
        kb = result.scalars().first()
        return kb

    async def _check_document_owner(self, doc_id: int, user_id: int) -> Optional[Document]:
        result = await self.db.execute(
            select(Document).where(Document.id == doc_id)
        )
        doc = result.scalars().first()
        if doc is None:
            return None
        # 通过知识库检查权限
        result = await self.db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == doc.knowledge_base_id,
                KnowledgeBase.user_id == user_id,
            )
        )
        kb = result.scalars().first()
        return doc if kb else None

    # ============================================================
    # 文件上传
    # ============================================================

    # 异步处理阈值: 文件大于此值时走 Celery 异步处理 (bytes)
    ASYNC_THRESHOLD = 500 * 1024  # 500KB

    async def prepare_upload(
        self,
        kb_id: int,
        user_id: int,
        filename: str,
        file_content: bytes,
    ) -> Optional[Tuple[Document, str]]:
        """准备文件上传 — 保存文件 + 创建 Document 记录 (status=processing)

        不做文本提取/分块/向量化. 用于 Celery 异步处理的前置步骤.

        Returns: (Document, file_path) 或 None (权限不足)
        """
        # 1. 权限检查
        kb = await self._check_kb_owner(kb_id, user_id)
        if kb is None:
            return None

        # 2. 检查文件类型
        suffix = Path(filename).suffix.lower()
        if not suffix:
            suffix = ".txt"

        # 3. 保存文件
        kb_upload_dir = os.path.join(settings.UPLOAD_DIR, "kb_%d" % kb_id)
        Path(kb_upload_dir).mkdir(parents=True, exist_ok=True)
        safe_filename = "%s_%s" % (uuid.uuid4().hex[:8], Path(filename).name)
        file_path = os.path.join(kb_upload_dir, safe_filename)

        with open(file_path, "wb") as f:
            f.write(file_content)

        file_size = len(file_content)

        # 4. 创建 Document 记录 (status=processing)
        file_type = self.processor.detect_file_type(file_path)
        doc = Document(
            knowledge_base_id=kb_id,
            filename=filename,
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            status="processing",
            total_chunks=0,
        )
        self.db.add(doc)
        await self.db.commit()
        await self.db.refresh(doc)

        return doc, file_path

    async def process_upload(
        self,
        kb_id: int,
        user_id: int,
        filename: str,
        file_content: bytes,
        chunk_size: Optional[int] = None,
        chunk_overlap: Optional[int] = None,
    ) -> Optional[Document]:
        """处理文件上传

        流程:
        1. 检查权限（必须是知识库所有者）
        2. 保存文件到磁盘
        3. 在数据库中创建 Document 记录（状态 pending）
        4. 提取文本 + 分块 + 向量化 + 建立向量索引
        5. 更新 Document 状态为 processed
        """
        # 1. 权限检查
        kb = await self._check_kb_owner(kb_id, user_id)
        if kb is None:
            return None

        # 2. 检查文件类型
        suffix = Path(filename).suffix.lower()
        if not suffix:
            suffix = ".txt"

        # 3. 保存文件
        kb_upload_dir = os.path.join(settings.UPLOAD_DIR, "kb_%d" % kb_id)
        Path(kb_upload_dir).mkdir(parents=True, exist_ok=True)
        safe_filename = "%s_%s" % (uuid.uuid4().hex[:8], Path(filename).name)
        file_path = os.path.join(kb_upload_dir, safe_filename)

        with open(file_path, "wb") as f:
            f.write(file_content)

        file_size = len(file_content)

        # 4. 创建 Document 记录
        file_type = self.processor.detect_file_type(file_path)
        doc = Document(
            knowledge_base_id=kb_id,
            filename=filename,
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            status="processing",
            total_chunks=0,
        )
        self.db.add(doc)
        await self.db.flush()

        try:
            # 5. 提取文本
            text = self.processor.extract_text(file_path, file_type)
            doc.content_text = text if len(text) < 500000 else text[:500000]  # 限制大小

            if not text or len(text.strip()) < 10:
                doc.status = "skipped"
                doc.total_chunks = 0
                await self.db.commit()
                await self.db.refresh(doc)
                return doc

            # 6. 智能分块
            use_chunk_size = chunk_size if chunk_size else kb.chunk_size
            use_chunk_overlap = chunk_overlap if chunk_overlap else kb.chunk_overlap
            chunks_data = self.processor.split_chunks(
                text,
                chunk_size=use_chunk_size,
                chunk_overlap=use_chunk_overlap,
            )

            if not chunks_data:
                doc.status = "processed"
                doc.total_chunks = 0
                await self.db.commit()
                await self.db.refresh(doc)
                return doc

            # 7. 向量化
            chunk_texts = [c["content"] for c in chunks_data]
            vectors = self.embedding.encode(chunk_texts)

            # 8. 写入 Chunk 表 + 向量存储
            store = self.vector_manager.get_store(kb_id, dim=self.embedding.dim)
            metadata_list = []
            chunk_objs = []

            for i, (chunk_info, vec) in enumerate(zip(chunks_data, vectors)):
                chunk_obj = Chunk(
                    document_id=doc.id,
                    knowledge_base_id=kb_id,
                    content=chunk_info["content"],
                    chunk_index=chunk_info["index"],
                    vector_index=-1,  # 先占位
                    metadata_="",
                )
                self.db.add(chunk_obj)
                chunk_objs.append(chunk_obj)

                metadata_list.append({
                    "chunk_id_placeholder": i,
                    "document_id": doc.id,
                    "knowledge_base_id": kb_id,
                    "document_filename": filename,
                    "content": chunk_info["content"],
                })

            await self.db.flush()  # 让 chunk_objs 获得真实的 id

            # 更新 metadata 中的 chunk_id
            for i, chunk_obj in enumerate(chunk_objs):
                metadata_list[i]["chunk_id"] = chunk_obj.id

            # 添加到向量索引
            vector_indices = store.add(vectors, metadata_list)

            # 更新 Chunk 的 vector_index
            for chunk_obj, v_idx in zip(chunk_objs, vector_indices):
                chunk_obj.vector_index = v_idx

            # 保存向量索引到磁盘
            self.vector_manager.save(kb_id)

            doc.status = "processed"
            doc.total_chunks = len(chunk_objs)

            # 更新知识库统计
            kb.total_documents = (kb.total_documents or 0) + 1
            kb.total_chunks = (kb.total_chunks or 0) + len(chunk_objs)

            await self.db.commit()
            await self.db.refresh(doc)
            return doc

        except Exception as e:
            doc.status = "error"
            doc.content_text = str(e)[:2000]
            await self.db.commit()
            await self.db.refresh(doc)
            raise

    # ============================================================
    # 查询
    # ============================================================
    async def list_documents(
        self,
        kb_id: int,
        user_id: Optional[int],
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Document], int]:
        """获取知识库的文档列表"""
        # 权限: 可以访问自己的知识库 + 公开知识库
        result = await self.db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        )
        kb = result.scalars().first()
        if kb is None:
            return [], 0

        can_access = (user_id is not None and kb.user_id == user_id) or kb.is_public
        if not can_access:
            return [], 0

        total_result = await self.db.execute(
            select(func.count()).select_from(Document).where(
                Document.knowledge_base_id == kb_id
            )
        )
        total = total_result.scalar()

        docs_result = await self.db.execute(
            select(Document)
            .where(Document.knowledge_base_id == kb_id)
            .order_by(Document.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        docs = docs_result.scalars().all()
        return docs, total

    async def get_document(
        self,
        doc_id: int,
        user_id: Optional[int],
    ) -> Optional[Document]:
        """获取单个文档"""
        result = await self.db.execute(
            select(Document).where(Document.id == doc_id)
        )
        doc = result.scalars().first()
        if doc is None:
            return None

        result = await self.db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == doc.knowledge_base_id
            )
        )
        kb = result.scalars().first()
        if kb is None:
            return None

        can_access = (user_id is not None and kb.user_id == user_id) or kb.is_public
        return doc if can_access else None

    async def get_document_chunks(
        self,
        doc_id: int,
        user_id: Optional[int],
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Chunk], int]:
        """获取文档的分块列表"""
        doc = await self.get_document(doc_id, user_id)
        if doc is None:
            return [], 0

        total_result = await self.db.execute(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == doc_id)
        )
        total = total_result.scalar()

        chunks_result = await self.db.execute(
            select(Chunk)
            .where(Chunk.document_id == doc_id)
            .order_by(Chunk.chunk_index.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        chunks = chunks_result.scalars().all()
        return chunks, total

    # ============================================================
    # 删除
    # ============================================================
    async def delete_document(self, doc_id: int, user_id: int) -> bool:
        """删除文档（仅知识库所有者）"""
        doc = await self._check_document_owner(doc_id, user_id)
        if doc is None:
            return False

        kb_id = doc.knowledge_base_id
        result = await self.db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        )
        kb = result.scalars().first()

        # 删除磁盘文件
        try:
            if os.path.isfile(doc.file_path):
                os.remove(doc.file_path)
        except Exception:
            pass

        # 删除 chunks
        chunks_result = await self.db.execute(
            select(Chunk).where(Chunk.document_id == doc_id)
        )
        chunks = chunks_result.scalars().all()
        deleted_chunk_count = len(chunks)
        for chunk in chunks:
            await self.db.delete(chunk)

        # 删除文档
        await self.db.delete(doc)

        # 更新知识库统计
        if kb:
            kb.total_documents = max(0, (kb.total_documents or 0) - 1)
            kb.total_chunks = max(0, (kb.total_chunks or 0) - deleted_chunk_count)

        # 重建该知识库的向量索引（简单实现: 删除后下次使用时重新构建）
        # 因为 chunks 的 vector_index 是全局递增的，所以需要重建
        if self.vector_manager.has_store(kb_id):
            self.vector_manager.delete(kb_id)
            # 重新索引剩余文档的 chunk
            remaining_result = await self.db.execute(
                select(Chunk)
                .where(Chunk.knowledge_base_id == kb_id)
                .order_by(Chunk.id.asc())
            )
            remaining_chunks = remaining_result.scalars().all()
            if remaining_chunks:
                # 重新向量化（这里简化处理：只重建向量索引结构）
                store = self.vector_manager.get_store(kb_id, dim=self.embedding.dim)
                texts = [c.content for c in remaining_chunks]
                vectors = self.embedding.encode(texts)
                # 根据 document_id 反查 filename
                doc_map = {}
                docs_result = await self.db.execute(
                    select(Document).where(
                        Document.knowledge_base_id == kb_id
                    )
                )
                for d in docs_result.scalars().all():
                    doc_map[d.id] = d.filename
                metas = [
                    {
                        "chunk_id": c.id,
                        "document_id": c.document_id,
                        "knowledge_base_id": kb_id,
                        "document_filename": doc_map.get(c.document_id, ""),
                        "content": c.content,
                    }
                    for c in remaining_chunks
                ]
                v_indices = store.add(vectors, metas)
                for c, vi in zip(remaining_chunks, v_indices):
                    c.vector_index = vi
                self.vector_manager.save(kb_id)

        await self.db.commit()
        return True

    async def delete_all_for_kb(self, kb_id: int, user_id: int) -> int:
        """删除知识库下的所有文档"""
        kb = await self._check_kb_owner(kb_id, user_id)
        if kb is None:
            return 0

        docs_result = await self.db.execute(
            select(Document).where(Document.knowledge_base_id == kb_id)
        )
        docs = docs_result.scalars().all()
        count = 0
        for doc in docs:
            if await self.delete_document(doc.id, user_id):
                count += 1

        # 删除向量存储
        if self.vector_manager.has_store(kb_id):
            self.vector_manager.delete(kb_id)

        return count

    # ============================================================
    # 向量搜索
    # ============================================================
    async def search_in_kb(
        self,
        kb_id: int,
        user_id: Optional[int],
        query_text: str,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """在知识库中进行语义搜索"""
        # 权限检查
        result = await self.db.execute(
            select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
        )
        kb = result.scalars().first()
        if kb is None:
            return []
        can_access = (user_id is not None and kb.user_id == user_id) or kb.is_public
        if not can_access:
            return []

        # 检查知识库是否有分块
        total_chunks_result = await self.db.execute(
            select(func.count()).select_from(Chunk).where(
                Chunk.knowledge_base_id == kb_id
            )
        )
        total_chunks = total_chunks_result.scalar()
        if total_chunks == 0:
            return []

        # 获取/重建向量索引
        store = self.vector_manager.get_store(kb_id, dim=self.embedding.dim)

        # 如果向量索引还没有数据（可能是从磁盘加载或首次使用），需要确保已有 chunk 被索引
        if store.total() == 0:
            await self._rebuild_vector_index(kb_id)
            store = self.vector_manager.get_store(kb_id, dim=self.embedding.dim)
            if store.total() == 0:
                return []

        # 编码查询
        query_vec = self.embedding.encode_single(query_text)
        if not query_vec:
            return []

        # 搜索
        results = store.search(query_vec, top_k=max(top_k, 1))

        # 组装结果
        final_results = []
        for r in results:
            # store.search 返回 List[Dict]: {"index": int, "score": float, "metadata": Dict}
            vec_idx = r.get("index")
            score = r.get("score", 0.0)
            meta = r.get("metadata") or {}

            if vec_idx is None:
                continue
            if score < min_score:
                continue

            chunk_id = meta.get("chunk_id")
            doc_id = meta.get("document_id")

            if chunk_id is None:
                continue

            chunk_result = await self.db.execute(
                select(Chunk).where(Chunk.id == chunk_id)
            )
            chunk = chunk_result.scalars().first()
            if chunk is None:
                continue
            doc_result = await self.db.execute(
                select(Document).where(Document.id == chunk.document_id)
            )
            doc = doc_result.scalars().first()

            final_results.append({
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "knowledge_base_id": kb_id,
                "content": chunk.content,
                "score": float(score),
                "document_filename": doc.filename if doc else None,
            })

        return final_results

    async def _rebuild_vector_index(self, kb_id: int) -> None:
        """重建某个知识库的向量索引"""
        chunks_result = await self.db.execute(
            select(Chunk)
            .where(Chunk.knowledge_base_id == kb_id)
            .order_by(Chunk.id.asc())
        )
        chunks = chunks_result.scalars().all()
        if not chunks:
            return

        store = self.vector_manager.get_store(kb_id, dim=self.embedding.dim)

        texts = [c.content for c in chunks]
        vectors = self.embedding.encode(texts)

        # 根据 document_id 反查 filename
        doc_ids = set(c.document_id for c in chunks)
        doc_map = {}
        docs_result = await self.db.execute(
            select(Document).where(Document.id.in_(doc_ids))
        )
        for d in docs_result.scalars().all():
            doc_map[d.id] = d.filename

        metas = [
            {
                "chunk_id": c.id,
                "document_id": c.document_id,
                "knowledge_base_id": kb_id,
                "document_filename": doc_map.get(c.document_id, ""),
                "content": c.content,
            }
            for c in chunks
        ]
        v_indices = store.add(vectors, metas)
        for c, vi in zip(chunks, v_indices):
            c.vector_index = vi

        await self.db.commit()
        self.vector_manager.save(kb_id)


__all__ = ["DocumentService"]
