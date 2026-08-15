"""多策略分块器 — ChunkerFactory

设计:
1. 统一接口: 所有分块器实现 split(text) -> List[Chunk]
2. 三种分块策略:
   - recursive: 递归字符分块 (适合纯文本, 按 \\n\\n → \\n → 。→ . → 空格递归切分)
   - semantic:  语义感知分块 (复用 SemanticChunker, 保持 Markdown 结构)
   - markdown:  Markdown 结构分块 (按标题层级分块, 保留代码块/表格完整性)
3. 工厂模式: ChunkerFactory.create(strategy, **kwargs)
4. 自动检测: detect_strategy(text) 根据文本特征推荐分块策略

策略对比:
  | 策略 | 适合场景 | 优点 | 缺点 |
  |------|---------|------|------|
  | recursive | 纯文本/日志 | 简单高效 | 可能断句 |
  | semantic | 混合文档 | 保持结构 | 复杂度较高 |
  | markdown | MD 文档 | 完美保留结构 | 仅适合 MD |
"""

from __future__ import annotations

import re
from typing import List, Dict, Any, Optional, Protocol

from app.core.logging import logger
from .semantic_chunker import SemanticChunker
from .markdown_parser import MarkdownParser, parse_markdown


class ChunkResult:
    """分块结果"""

    def __init__(
        self,
        content: str,
        index: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.content = content
        self.index = index
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "index": self.index,
            "char_count": len(self.content),
            "metadata": self.metadata,
        }

    def __repr__(self):
        return f"ChunkResult(index={self.index}, chars={len(self.content)})"


class BaseChunker(Protocol):
    """分块器接口"""

    def split(self, text: str) -> List[ChunkResult]:
        """将文本分块

        Args:
            text: 输入文本

        Returns:
            分块结果列表
        """
        ...


# ============================================================
#  递归字符分块器
# ============================================================

class RecursiveCharacterChunker:
    """递归字符分块器

    按分隔符优先级递归切分:
    1. \\n\\n (段落)
    2. \\n (行)
    3. 。(中文句号)
    4. . (英文句号)
    5. ; (分号)
    6. 空格

    每次切分后检查 chunk 大小, 超过 max_size 则递归切分更小的单元。
    相邻 chunk 有 overlap 个字符的重叠。
    """

    DEFAULT_SEPARATORS = ["\n\n", "\n", "。", ".", "!", "?", ";", "；", " ", ""]

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        separators: Optional[List[str]] = None,
    ):
        self.chunk_size = max(chunk_size, 50)
        self.chunk_overlap = min(chunk_overlap, self.chunk_size // 4)
        self.separators = separators or self.DEFAULT_SEPARATORS

    def split(self, text: str) -> List[ChunkResult]:
        """递归字符分块"""
        if not text or not text.strip():
            return []

        chunks_raw = self._recursive_split(text, self.separators)

        # 合并过小的 chunk
        merged = self._merge_small_chunks(chunks_raw)

        # 添加 overlap
        if self.chunk_overlap > 0:
            merged = self._add_overlap(merged)

        # 转为 ChunkResult
        results = []
        for i, content in enumerate(merged):
            results.append(ChunkResult(
                content=content.strip(),
                index=i,
                metadata={"strategy": "recursive"},
            ))

        return results

    def _recursive_split(
        self,
        text: str,
        separators: List[str],
    ) -> List[str]:
        """递归切分文本"""
        if len(text) <= self.chunk_size:
            return [text]

        if not separators:
            # 没有分隔符了, 强制按长度切
            return [
                text[i:i + self.chunk_size]
                for i in range(0, len(text), self.chunk_size)
            ]

        sep = separators[0]
        remaining_seps = separators[1:]

        if sep:
            parts = text.split(sep)
        else:
            parts = [text]

        # 重新组合, 每个 part 不超过 chunk_size
        result = []
        current = ""
        for part in parts:
            if len(current) + len(sep) + len(part) <= self.chunk_size:
                current = current + sep + part if current else part
            else:
                if current:
                    result.append(current)
                if len(part) > self.chunk_size:
                    # 递归用更小的分隔符
                    sub_parts = self._recursive_split(part, remaining_seps)
                    result.extend(sub_parts)
                    current = ""
                else:
                    current = part
        if current:
            result.append(current)

        return result

    def _merge_small_chunks(
        self,
        chunks: List[str],
        min_size: int = 50,
    ) -> List[str]:
        """合并过小的 chunk"""
        if not chunks:
            return []

        merged = []
        current = ""

        for chunk in chunks:
            if len(current) + len(chunk) <= self.chunk_size:
                current = current + " " + chunk if current else chunk
            else:
                if current and len(current) < min_size and merged:
                    # 当前 chunk 太小, 合并到上一个
                    merged[-1] = merged[-1] + " " + current
                elif current:
                    merged.append(current)
                current = chunk

        if current:
            if len(current) < min_size and merged:
                merged[-1] = merged[-1] + " " + current
            else:
                merged.append(current)

        return merged

    def _add_overlap(self, chunks: List[str]) -> List[str]:
        """为相邻 chunk 添加重叠"""
        if len(chunks) <= 1:
            return chunks

        result = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tail = chunks[i - 1][-self.chunk_overlap:] if chunks[i - 1] else ""
            result.append(prev_tail + chunks[i])

        return result


# ============================================================
#  Markdown 结构分块器
# ============================================================

class MarkdownStructureChunker:
    """Markdown 结构分块器

    按 Markdown 标题层级分块, 保留代码块和表格完整性。
    每个分块对应一个标题及其下属内容。
    """

    def __init__(
        self,
        chunk_size: int = 500,
        max_heading_level: int = 3,
    ):
        self.chunk_size = max(chunk_size, 100)
        self.max_heading_level = max_heading_level
        self._md_parser = MarkdownParser()
        self._fallback = SemanticChunker(
            max_chars=chunk_size,
            min_chars=50,
            overlap=30,
        )

    def split(self, text: str) -> List[ChunkResult]:
        """Markdown 结构分块"""
        if not text or not text.strip():
            return []

        # 检测是否为 Markdown
        if not self._is_markdown(text):
            # 降级到语义分块
            logger.debug("Text is not Markdown, falling back to semantic chunker")
            return self._fallback_split(text)

        # 解析 Markdown 结构
        parsed = parse_markdown(text)

        if parsed.total_blocks == 0:
            return self._fallback_split(text)

        # 按标题分组
        sections = self._group_by_heading(parsed)

        # 转为 ChunkResult
        results = []
        for i, section in enumerate(sections):
            content = section["content"].strip()
            if not content:
                continue

            # 如果 section 太大, 进一步切分
            if len(content) > self.chunk_size * 2:
                sub_chunks = self._fallback.split_text(content)
                for j, sub in enumerate(sub_chunks):
                    results.append(ChunkResult(
                        content=sub["content"],
                        index=len(results),
                        metadata={
                            "strategy": "markdown",
                            "heading": section.get("heading", ""),
                            "heading_level": section.get("level", 0),
                            "sub_index": j,
                        },
                    ))
            else:
                results.append(ChunkResult(
                    content=content,
                    index=len(results),
                    metadata={
                        "strategy": "markdown",
                        "heading": section.get("heading", ""),
                        "heading_level": section.get("level", 0),
                    },
                ))

        return results if results else self._fallback_split(text)

    def _is_markdown(self, text: str) -> bool:
        """检测文本是否为 Markdown"""
        md_indicators = [
            r"^#{1,6}\s",           # 标题
            r"^```",                 # 代码块
            r"^\|.*\|.*\|",         # 表格
            r"^\s*[-*+]\s",         # 列表
            r"^\s*\d+\.\s",         # 有序列表
            r"^\s*>",                # 引用
            r"\[.*\]\(.*\)",        # 链接
            r"!\[.*\]\(.*\)",       # 图片
        ]
        for pattern in md_indicators:
            if re.search(pattern, text, re.MULTILINE):
                return True
        return False

    def _group_by_heading(self, parsed) -> List[Dict[str, Any]]:
        """按标题分组"""
        sections = []
        current_heading = ""
        current_level = 0
        current_content = ""

        for block in parsed.blocks:
            if block.block_type == "heading" and block.level <= self.max_heading_level:
                # 新标题: 保存当前 section
                if current_content.strip():
                    sections.append({
                        "heading": current_heading,
                        "level": current_level,
                        "content": current_content,
                    })
                current_heading = block.content
                current_level = block.level
                current_content = block.content + "\n\n"
            else:
                current_content += block.content + "\n\n"

        # 最后一个 section
        if current_content.strip():
            sections.append({
                "heading": current_heading,
                "level": current_level,
                "content": current_content,
            })

        return sections

    def _fallback_split(self, text: str) -> List[ChunkResult]:
        """降级到语义分块"""
        chunks = self._fallback.split_text(text)
        return [
            ChunkResult(
                content=c["content"],
                index=i,
                metadata={"strategy": "semantic_fallback"},
            )
            for i, c in enumerate(chunks)
        ]


# ============================================================
#  语义分块器适配器
# ============================================================

class SemanticChunkerAdapter:
    """语义分块器适配器

    包装现有的 SemanticChunker, 统一接口。
    """

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ):
        self._chunker = SemanticChunker(
            max_chars=chunk_size,
            min_chars=50,
            overlap=chunk_overlap,
        )

    def split(self, text: str) -> List[ChunkResult]:
        """语义分块"""
        if not text or not text.strip():
            return []

        chunks = self._chunker.split_text(text)
        return [
            ChunkResult(
                content=c["content"],
                index=i,
                metadata={"strategy": "semantic", **c.get("metadata", {})},
            )
            for i, c in enumerate(chunks)
        ]


# ============================================================
#  工厂 + 策略检测
# ============================================================

class ChunkerFactory:
    """分块器工厂"""

    @staticmethod
    def create(
        strategy: str = "recursive",
        chunk_size: int = 500,
        chunk_overlap: int = 50,
        **kwargs,
    ) -> BaseChunker:
        """创建分块器

        Args:
            strategy: 分块策略 ("recursive" / "semantic" / "markdown")
            chunk_size: 单个分块最大字符数
            chunk_overlap: 相邻分块重叠字符数
            **kwargs: 策略特定参数

        Returns:
            分块器实例
        """
        if strategy == "recursive":
            return RecursiveCharacterChunker(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                separators=kwargs.get("separators"),
            )
        elif strategy == "semantic":
            return SemanticChunkerAdapter(
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        elif strategy == "markdown":
            return MarkdownStructureChunker(
                chunk_size=chunk_size,
                max_heading_level=kwargs.get("max_heading_level", 3),
            )
        else:
            raise ValueError(
                f"未知分块策略: {strategy}. "
                f"可选: recursive / semantic / markdown"
            )

    @staticmethod
    def detect_strategy(text: str) -> str:
        """根据文本特征自动检测最佳分块策略

        Args:
            text: 输入文本

        Returns:
            推荐的分块策略名
        """
        if not text or not text.strip():
            return "recursive"

        # 检测 Markdown 特征
        md_patterns = [
            r"^#{1,6}\s",
            r"^```",
            r"^\|.*\|.*\|",
            r"^\s*[-*+]\s",
        ]
        md_count = sum(1 for p in md_patterns if re.search(p, text, re.MULTILINE))

        if md_count >= 2:
            return "markdown"

        # 检测结构化特征 (段落多)
        paragraphs = text.split("\n\n")
        if len(paragraphs) >= 3:
            return "semantic"

        # 默认递归分块
        return "recursive"


__all__ = [
    "ChunkResult",
    "BaseChunker",
    "RecursiveCharacterChunker",
    "MarkdownStructureChunker",
    "SemanticChunkerAdapter",
    "ChunkerFactory",
]
