"""纯逻辑层文档处理 Pipeline

不依赖数据库 / SQLAlchemy，将"文件 → 文本 → chunks → 向量存储"整合成一条纯逻辑链，
便于单元测试与独立部署。

**pipeline 流程:**
 1. FileInput    → extract_text() → 纯文本
 2. 纯文本 → split_chunks() → List[chunk_dict]
 3. List[chunk_dict] → encode_chunks() → List[vector]
 4. List[vector] → store.add() → 向量索引

**返回的 ProcessedDocument 对象:**
  - 包含原始文件名、文件类型、字符数、chunks 列表
  - chunks 中包含: index/content/char_count/vector_index 等信息
  - 可直接作为"知识库文档"的处理结果

**使用场景:**
  1. 单元测试: 直接调用 pipeline 验证"文件→向量"全链路
  2. 轻量部署: 不使用数据库的场景（纯文件系统 + 向量存储）
  3. 调试: 查看某个文件被如何分块、向量索引建立情况
"""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple, Union

from .document_processor import DocumentProcessor
from .semantic_chunker import SemanticChunker
from ..embedding.embedding_service import EmbeddingService
from ..retrieval.vector_store import VectorStoreManager


# ============================================================
# 数据结构
# ============================================================

@dataclass
class DocumentChunk:
    """单个分块的处理结果"""
    index: int
    content: str
    char_count: int
    vector_index: int = -1  # 在向量存储中的索引（add 成功后更新）
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "content": self.content,
            "char_count": self.char_count,
            "vector_index": self.vector_index,
            "meta": self.meta,
        }


@dataclass
class ProcessedDocument:
    """完整文档的处理结果"""
    document_id: str                    # 处理时生成的唯一 ID
    filename: str
    file_type: str
    file_size: int
    total_chars: int
    total_chunks: int
    chunks: List[DocumentChunk] = field(default_factory=list)
    processing_time_ms: float = 0.0
    status: str = "processed"          # processed / skipped / error
    error_message: str = ""

    @property
    def success(self) -> bool:
        return self.status == "processed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "filename": self.filename,
            "file_type": self.file_type,
            "file_size": self.file_size,
            "total_chars": self.total_chars,
            "total_chunks": self.total_chunks,
            "chunks": [c.to_dict() for c in self.chunks],
            "processing_time_ms": round(self.processing_time_ms, 2),
            "status": self.status,
            "success": self.success,
            "error_message": self.error_message,
        }


# ============================================================
# 主 Pipeline
# ============================================================

class DocumentPipeline:
    """纯逻辑层的文档处理 pipeline

    示例:
        from app.processors import DocumentPipeline, EmbeddingService, VectorStoreManager

        # 最简用法
        pipeline = DocumentPipeline(vector_store_dir="./data/vector_stores")
        result = pipeline.process_file(
            kb_id=1,
            file_path="./docs/readme.md",
        )
        print("处理完成: %d chunks, 耗时 %.1fms" % (
            result.total_chunks, result.processing_time_ms
        ))

        # 自定义参数
        pipeline = DocumentPipeline(
            vector_store_dir="./data/vector_stores",
            chunk_size=600,
            chunk_overlap=80,
            embedding=EmbeddingService(),
        )

        # 直接处理内存中的文本（不需要文件）
        result = pipeline.process_text(
            kb_id=1,
            text="..." ,
            filename="in-memory.txt",
        )
    """

    def __init__(
        self,
        vector_store_dir: Optional[str] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        min_chunk_chars: int = 100,
        embedding: Optional[EmbeddingService] = None,
        processor: Optional[DocumentProcessor] = None,
        chunker: Optional[SemanticChunker] = None,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_chars = min_chunk_chars

        self.processor = processor or DocumentProcessor(
            chunk_size=chunk_size, chunk_overlap=chunk_overlap
        )
        self.chunker = chunker or SemanticChunker(
            max_chars=chunk_size, min_chars=min_chunk_chars, overlap=chunk_overlap
        )
        self.embedding = embedding or EmbeddingService()
        if vector_store_dir is None:
            from app.core.config import settings
            vector_store_dir = settings.VECTOR_STORE_DIR
        self.vector_store_dir = vector_store_dir
        self.vector_manager = VectorStoreManager(vector_store_dir)

    # ---------- 便捷入口: process(filename, text) —— 最简化用法 ----------

    def process(self, filename: str, text: str, kb_id: int = 0) -> ProcessedDocument:
        """最简便捷入口：给定文件名和文本内容，处理后返回 ProcessedDocument。"""
        return self.process_text(
            kb_id=kb_id,
            text=text,
            filename=filename,
        )

    # ---------- 文件路径入口 ----------

    def process_file(
        self,
        kb_id: int,
        file_path: Union[str, Path],
        file_type: Optional[str] = None,
        *,
        skip_vectorize: bool = False,
    ) -> ProcessedDocument:
        """处理单个文件（从磁盘读取）

        Args:
            kb_id: 知识库 ID（用于向量存储的命名空间）
            file_path: 文件路径
            file_type: 可选，强制指定文件类型（如 "markdown"）
            skip_vectorize: 是否跳过向量化（仅做解析+分块，调试用）
        """
        t0 = time.perf_counter()

        fpath = str(file_path)
        filename = os.path.basename(fpath)

        if not os.path.isfile(fpath):
            return self._make_error(
                filename, "unknown", 0, "文件不存在: %s" % fpath, t0
            )

        try:
            file_size = os.path.getsize(fpath)
            ft = file_type or self.processor.detect_file_type(fpath)
            text = self.processor.extract_text(fpath, ft)
        except Exception as e:
            return self._make_error(
                filename, "unknown", 0, "文本提取失败: %s" % str(e), t0
            )

        return self._process_text_core(
            kb_id=kb_id,
            text=text,
            filename=filename,
            file_type=ft,
            file_size=file_size,
            skip_vectorize=skip_vectorize,
            start_time=t0,
        )

    # ---------- 内存文本入口 ----------

    def process_text(
        self,
        kb_id: int,
        text: str,
        filename: str = "in-memory.txt",
        file_type: str = "text",
        *,
        skip_vectorize: bool = False,
    ) -> ProcessedDocument:
        """处理内存中的文本（无需磁盘文件）"""
        return self._process_text_core(
            kb_id=kb_id,
            text=text,
            filename=filename,
            file_type=file_type,
            file_size=len(text.encode("utf-8", errors="ignore")),
            skip_vectorize=skip_vectorize,
            start_time=time.perf_counter(),
        )

    # ---------- 字节内容入口 ----------

    def process_bytes(
        self,
        kb_id: int,
        content: bytes,
        filename: str,
        file_type: Optional[str] = None,
        *,
        skip_vectorize: bool = False,
        tmp_dir: Optional[str] = None,
    ) -> ProcessedDocument:
        """处理文件字节内容（API 上传场景）

        会写入临时文件后交给 extract_text，再删除临时文件。
        """
        tmp_path = None
        try:
            tmp = tmp_dir or "./tmp_uploads"
            os.makedirs(tmp, exist_ok=True)
            tmp_path = os.path.join(tmp, "%s_%s" % (uuid.uuid4().hex[:8], filename))
            with open(tmp_path, "wb") as f:
                f.write(content)

            result = self.process_file(
                kb_id=kb_id,
                file_path=tmp_path,
                file_type=file_type,
                skip_vectorize=skip_vectorize,
            )
            # 修正文件名（去掉 uuid 前缀）
            result.filename = filename
            return result
        finally:
            if tmp_path and os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    # ---------- 核心处理逻辑 ----------

    def _process_text_core(
        self,
        kb_id: int,
        text: str,
        filename: str,
        file_type: str,
        file_size: int,
        skip_vectorize: bool,
        start_time: float,
    ) -> ProcessedDocument:
        """核心: 文本 → 分块 → 向量化 → 入库"""
        doc_id = "doc_%s" % uuid.uuid4().hex[:12]

        # 空文本 → 跳过
        if not text or len(text.strip()) < 10:
            return ProcessedDocument(
                document_id=doc_id,
                filename=filename,
                file_type=file_type,
                file_size=file_size,
                total_chars=len(text),
                total_chunks=0,
                chunks=[],
                processing_time_ms=(time.perf_counter() - start_time) * 1000,
                status="skipped",
                error_message="文本过短或为空",
            )

        # --- 分块（语义感知）---
        try:
            raw_chunks = self.chunker.split_text(text)
        except Exception as e:
            # 语义分块失败 → 回退到字符级切分
            raw_chunks = self._fallback_split(text)

        # 过滤太短的 chunk
        filtered_chunks = [
            c for c in raw_chunks
            if c.get("char_count", 0) >= self.min_chunk_chars
        ]
        if not filtered_chunks and raw_chunks:
            # 如果所有 chunk 都太小，但整体又有内容，直接取第一块
            filtered_chunks = raw_chunks[:1]

        chunks: List[DocumentChunk] = []
        for i, c in enumerate(filtered_chunks):
            dc = DocumentChunk(
                index=i,
                content=c["content"],
                char_count=c.get("char_count", len(c["content"])),
                meta={"language": c.get("language"), "is_code": c.get("is_code", False)},
            )
            chunks.append(dc)

        # --- 向量化 + 入库 ---
        if not skip_vectorize and chunks:
            try:
                store = self.vector_manager.get_store(kb_id, dim=self.embedding.dim)
                texts = [c.content for c in chunks]
                vectors = self.embedding.encode(texts)

                metas = [
                    {
                        "document_id": doc_id,
                        "chunk_index": c.index,
                        "filename": filename,
                        "file_type": file_type,
                    }
                    for c in chunks
                ]
                vector_indices = store.add(vectors, metas)
                for c, vi in zip(chunks, vector_indices):
                    c.vector_index = vi
                self.vector_manager.save(kb_id)
            except Exception as e:
                return ProcessedDocument(
                    document_id=doc_id,
                    filename=filename,
                    file_type=file_type,
                    file_size=file_size,
                    total_chars=len(text),
                    total_chunks=0,
                    chunks=chunks,
                    processing_time_ms=(time.perf_counter() - start_time) * 1000,
                    status="error",
                    error_message="向量化/入库失败: %s" % str(e),
                )

        return ProcessedDocument(
            document_id=doc_id,
            filename=filename,
            file_type=file_type,
            file_size=file_size,
            total_chars=len(text),
            total_chunks=len(chunks),
            chunks=chunks,
            processing_time_ms=(time.perf_counter() - start_time) * 1000,
            status="processed",
        )

    # ---------- 回退: 字符级切分 ----------

    def _fallback_split(self, text: str) -> List[Dict[str, Any]]:
        """字符级切分（SemanticChunker 失败时的回退）"""
        chunks: List[Dict[str, Any]] = []
        start = 0
        n = len(text)
        idx = 0
        while start < n:
            end = min(start + self.chunk_size, n)
            # 尝试在空格/换行处对齐
            if end < n and text[end] not in " \n。！？.!?":
                # 向前 50 字符内找一个标点
                align_end = min(n, end + 50)
                for pos in range(end, align_end):
                    if text[pos] in " \n。！？.!?;；":
                        end = pos + 1
                        break
            content = text[start:end].strip()
            if content:
                chunks.append({"index": idx, "content": content, "char_count": len(content)})
                idx += 1
            start = end - self.chunk_overlap
        return chunks

    # ---------- 错误构造 ----------

    @staticmethod
    def _make_error(
        filename: str, file_type: str, file_size: int, error: str, start_time: float,
    ) -> ProcessedDocument:
        return ProcessedDocument(
            document_id="doc_error_%s" % uuid.uuid4().hex[:8],
            filename=filename,
            file_type=file_type,
            file_size=file_size,
            total_chars=0,
            total_chunks=0,
            chunks=[],
            processing_time_ms=(time.perf_counter() - start_time) * 1000,
            status="error",
            error_message=error,
        )

    # ---------- 便捷: 批量处理 ----------

    def process_batch(
        self,
        kb_id: int,
        file_paths: List[Union[str, Path]],
    ) -> List[ProcessedDocument]:
        """批量处理多个文件"""
        return [self.process_file(kb_id, p) for p in file_paths]


# ============================================================
# 导出
# ============================================================

__all__ = [
    "DocumentChunk",
    "ProcessedDocument",
    "DocumentPipeline",
]