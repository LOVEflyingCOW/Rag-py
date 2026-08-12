from __future__ import annotations

"""文档处理模块

- document_processor : 基础文本提取（TXT/Markdown/PDF/HTML/CSV 等）
- markdown_parser    : Markdown 结构化解析（标题/代码/表格/列表/引用）
- semantic_chunker   : 语义感知分块（尊重 Markdown 结构 + 句子边界）
- document_pipeline  : 纯逻辑层 pipeline（文件 → 文本 → chunks → 向量存储）
"""

from .document_processor import DocumentProcessor, SUPPORTED_EXTENSIONS
from .markdown_parser import (
    MarkdownBlock,
    ParsedMarkdown,
    MarkdownParser,
    parse_markdown,
)
from .semantic_chunker import SemanticChunker, chunk_text
from .document_pipeline import (
    DocumentChunk,
    ProcessedDocument,
    DocumentPipeline,
)

__all__ = [
    "DocumentProcessor",
    "SUPPORTED_EXTENSIONS",
    "MarkdownBlock",
    "ParsedMarkdown",
    "MarkdownParser",
    "parse_markdown",
    "SemanticChunker",
    "chunk_text",
    "DocumentChunk",
    "ProcessedDocument",
    "DocumentPipeline",
]