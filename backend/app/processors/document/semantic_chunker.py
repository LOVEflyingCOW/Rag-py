"""语义感知文本分块器

核心特性:
  1. **结构感知**: 识别并保持 Markdown 结构（标题/代码块/表格/列表/引用）
  2. **句子边界**: 长段落按句子切分，避免断句中间位置
  3. **Overlap**: 相邻 chunk 共享尾部若干字符，缓解跨 chunk 语义断裂
  4. **最小长度合并**: 避免字符数显著低于 min_chars 的 chunk（与相邻 chunk 合并）
  5. **零依赖**: 仅使用 Python 标准库 re

用法:
    chunker = SemanticChunker(max_chars=500, min_chars=100, overlap=50)
    chunks = chunker.split_text(markdown_or_plain_text)
    for c in chunks:
        print("[chunk %d] %d chars: %s" % (c["index"], c["char_count"], c["content"][:50]))
"""
from __future__ import annotations

import re
from typing import List, Dict, Any, Optional

from .markdown_parser import MarkdownParser, ParsedMarkdown, parse_markdown


# ============================================================
# 分块器
# ============================================================

class SemanticChunker:
    """语义感知分块器

    Args:
        max_chars:    单个 chunk 的最大字符数（软上限，结构块可能超出）
        min_chars:    单个 chunk 的最小期望字符数（低于此值会与相邻合并）
        overlap:      相邻 chunk 的重叠字符数（缓解跨 chunk 语义断裂）
        respect_code_blocks: 是否把代码块作为整体保留（不切分）
        respect_tables:      是否把表格作为整体保留（不切分）
    """

    # 句子分割标志（中英文标点 + 换行）
    _SENT_SPLIT = re.compile(r"([。！？.!?;；\n])")

    def __init__(
        self,
        max_chars: int = 500,
        min_chars: int = 100,
        overlap: int = 50,
        respect_code_blocks: bool = True,
        respect_tables: bool = True,
    ):
        self.max_chars = max(max_chars, 50)
        self.min_chars = max(min_chars, 20)
        self.overlap = max(overlap, 0)
        if self.overlap >= self.max_chars // 2:
            self.overlap = self.max_chars // 4  # 避免 overlap 过大
        self.respect_code_blocks = respect_code_blocks
        self.respect_tables = respect_tables
        self._md_parser = MarkdownParser()

    # -------- 主入口 --------

    def split_text(self, text: str) -> List[Dict[str, Any]]:
        """分块入口 —— 自动判断是否为 Markdown"""
        if text is None:
            return []
        text = str(text)
        if not text.strip():
            return []

        parsed = self._md_parser.parse(text)
        # 结构化分块条件（任一即可）:
        #   1. blocks 数量 ≥ 3（明显有多段结构）
        #   2. 含有特殊结构块（code / table / heading / quote）
        #   3. blocks 数量多于段落行数的 1/8（文档有明显结构化）
        has_special = any(
            b.block_type in ("code", "table", "heading", "quote")
            for b in parsed.blocks
        )
        line_count = max(1, len(text.split("\n")))
        if has_special or parsed.total_blocks >= 3 or parsed.total_blocks > line_count / 8:
            return self.split_parsed(parsed)

        return self._split_plain_text(text)

    # -------- 结构化分块 --------

    def split_parsed(self, parsed: ParsedMarkdown) -> List[Dict[str, Any]]:
        """基于结构化 Markdown 分块

        策略:
          - 标题 H1/H2 通常开启新 chunk
          - 代码块/表格整体保留（如果过长则单独成块）
          - 段落/列表/引用按句子边界进行合理切分
          - 过短 chunk 与相邻合并
        """
        if not parsed.blocks:
            return []

        chunks: List[Dict[str, Any]] = []
        current_segments: List[str] = []  # 当前 chunk 的各段文本
        current_chars = 0
        chunk_index = 0
        pending_heading: Optional[str] = None  # 上一个标题，作为下一个 chunk 的前缀

        def _flush_current():
            """把 current_segments 作为一个新 chunk 加入 chunks"""
            nonlocal chunk_index
            nonlocal current_segments
            nonlocal current_chars
            if not current_segments:
                return
            combined = "\n\n".join(s for s in current_segments if s)
            if combined.strip():
                chunks.append(self._make_chunk(chunk_index, [combined]))
                chunk_index += 1
            current_segments = []
            current_chars = 0

        def _merge_to_next(block_text: str):
            """把文本追加到 current_segments，必要时按句子拆分"""
            nonlocal current_chars
            nonlocal chunk_index
            # 如果加进去不会显著超过 max_chars → 直接加
            if current_chars + len(block_text) <= self.max_chars:
                current_segments.append(block_text)
                current_chars += len(block_text)
                return
            # 如果 current_segments 足够大 → 先 flush 再追加
            if current_chars >= self.min_chars:
                _flush_current()
            # 再按句子追加 block_text
            self._append_with_boundary(
                block_text, current_segments, chunks, chunk_index,
            )
            if len(chunks) and len(current_segments) == 0:
                chunk_index = len(chunks)
            current_chars = sum(len(s) for s in current_segments)

        for block in parsed.blocks:
            # --- 标题: 开启新 chunk ---
            if block.block_type == "heading":
                # flush 上一段
                if current_segments:
                    _flush_current()
                title = ("#" * block.level) + " " + block.content
                pending_heading = title
                current_segments.append(title)
                current_chars += len(title)
                continue

            # --- 代码块: 整体保留（不切分） ---
            if block.block_type == "code" and self.respect_code_blocks:
                code_text = "```%s\n%s\n```" % (
                    block.meta.get("language", ""), block.content
                )
                if current_chars + len(code_text) > self.max_chars * 1.2:
                    if current_segments:
                        _flush_current()
                    # 代码块单独作为一个 chunk
                    chunks.append(self._make_chunk(
                        chunk_index, [code_text],
                        extra_meta={"is_code": True, "language": block.meta.get("language")}
                    ))
                    chunk_index += 1
                else:
                    current_segments.append(code_text)
                    current_chars += len(code_text)
                continue

            # --- 表格: 整体保留 ---
            if block.block_type == "table" and self.respect_tables:
                table_text = "表格: " + block.content
                _merge_to_next(table_text)
                continue

            # --- 引用 / 列表 / 段落: 按句子切分后合并 ---
            if block.block_type in ("quote", "list", "paragraph"):
                text = block.content
                # 如果当前只有 pending heading 且字符数很少 → 不 flush，而是合并进来
                _merge_to_next(text)
                continue

            # --- 其他: 直接追加 ---
            if block.content:
                if current_chars + len(block.content) > self.max_chars:
                    if current_segments:
                        _flush_current()
                current_segments.append(block.content)
                current_chars += len(block.content)

        # --- 最后 flush ---
        if current_segments:
            _flush_current()

        # --- 短 chunk 合并 + overlap ---
        chunks = self._merge_short_chunks(chunks)
        return self._apply_overlap(chunks)

    # -------- 纯文本分块（无 Markdown 结构） --------

    def _split_plain_text(self, text: str) -> List[Dict[str, Any]]:
        """对纯文本进行分块（按段落 → 句子边界）"""
        paragraphs = re.split(r"\n\s*\n", text.strip())
        paragraphs = [p.strip() for p in paragraphs if p.strip()]

        if not paragraphs:
            return []

        chunks: List[Dict[str, Any]] = []
        current_segments: List[str] = []
        current_chars = 0
        chunk_index = 0

        for para in paragraphs:
            if current_chars + len(para) <= self.max_chars:
                # 整段可以放进去
                current_segments.append(para)
                current_chars += len(para)
            else:
                # 需要按句子切分
                if current_chars >= self.min_chars:
                    chunks.append(self._make_chunk(chunk_index, current_segments))
                    chunk_index += 1
                    current_segments = []
                    current_chars = 0
                self._append_with_boundary(para, current_segments, chunks, chunk_index)
                if len(chunks) and len(current_segments) == 0:
                    chunk_index = len(chunks)
                current_chars = sum(len(s) for s in current_segments)

        if current_segments:
            chunks.append(self._make_chunk(chunk_index, current_segments))

        chunks = self._merge_short_chunks(chunks)
        return self._apply_overlap(chunks)

    # -------- 内部: 按句子边界追加文本 --------

    def _append_with_boundary(
        self,
        text: str,
        current_segments: List[str],
        chunks: List[Dict[str, Any]],
        chunk_index: int,
    ) -> None:
        """以句子为单位追加文本到 current_segments

        - 如果整段能放下 → 直接追加
        - 如果放不下 → 按句子切分装满当前 chunk 后 flush
        - 剩余句子留在 current_segments 里
        """
        if not text:
            return

        # 先按句子切分（保留标点）
        parts = self._SENT_SPLIT.split(text)
        sentences: List[str] = []
        buf = ""
        for p in parts:
            if self._SENT_SPLIT.fullmatch(p):
                buf += p
                if buf.strip():
                    sentences.append(buf)
                buf = ""
            else:
                buf += p
        if buf.strip():
            sentences.append(buf)

        if not sentences:
            # 没有明显句子标记 → 当作一整个处理
            sentences = [text]

        for sent in sentences:
            current_len = sum(len(s) for s in current_segments)
            if current_len + len(sent) <= self.max_chars:
                current_segments.append(sent)
            else:
                # 先 flush 当前 chunk（如果有内容）
                if current_segments:
                    chunks.append(self._make_chunk(len(chunks), current_segments))
                    current_segments = []
                # 句子本身太长 → 直接放进去（允许单句 chunk 超出 max_chars）
                if len(sent) > self.max_chars:
                    chunks.append(self._make_chunk(len(chunks), [sent]))
                else:
                    current_segments.append(sent)

    # -------- 内部: 短 chunk 合并 --------

    def _merge_short_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """合并字符数显著低于 min_chars 的 chunk

        策略:
          - 从左到右扫描
          - 如果 chunk 字符数 < min_chars / 2 → 与下一个 chunk 合并
          - 如果是最后一个 chunk → 与前一个合并
        """
        if len(chunks) <= 1:
            return chunks

        merged: List[Dict[str, Any]] = []
        threshold = max(self.min_chars // 2, 40)

        i = 0
        while i < len(chunks):
            cur = chunks[i]
            cur_chars = cur["char_count"]

            # 如果足够长 → 直接保留
            if cur_chars >= threshold:
                merged.append(cur)
                i += 1
                continue

            # 尝试和下一个合并
            if i + 1 < len(chunks):
                nxt = chunks[i + 1]
                combined_content = (cur["content"] + "\n\n" + nxt["content"]).strip()
                combined = {
                    "index": cur["index"],
                    "content": combined_content,
                    "char_count": len(combined_content),
                }
                # 如果合并后不过长 → 合并；否则直接保留
                if len(combined_content) <= self.max_chars * 1.5:
                    merged.append(combined)
                    i += 2
                    continue

            # 没有下一个可合并 → 与前一个合并
            if merged:
                prev = merged[-1]
                combined_content = (prev["content"] + "\n\n" + cur["content"]).strip()
                prev["content"] = combined_content
                prev["char_count"] = len(combined_content)
            else:
                merged.append(cur)
            i += 1

        return merged

    # -------- 内部: 构造 chunk 对象 --------

    def _make_chunk(
        self,
        index: int,
        segments: List[str],
        extra_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        content = "\n".join(s.strip() for s in segments if s and s.strip())
        result = {
            "index": index,
            "content": content,
            "char_count": len(content),
        }
        if extra_meta:
            result.update(extra_meta)
        return result

    # -------- 内部: 给 chunks 加上 overlap 后缀 --------

    def _apply_overlap(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """相邻 chunk 之间保留尾部 overlap 字符"""
        if self.overlap <= 0 or len(chunks) <= 1:
            return chunks

        for i in range(1, len(chunks)):
            prev = chunks[i - 1]["content"]
            overlap_text = prev[-self.overlap:] if len(prev) > self.overlap else prev
            if overlap_text:
                chunks[i]["content"] = (overlap_text + "\n" + chunks[i]["content"])
                chunks[i]["char_count"] = len(chunks[i]["content"])

        return chunks


# ============================================================
# 便捷函数
# ============================================================

def chunk_text(
    text: str,
    max_chars: int = 500,
    min_chars: int = 100,
    overlap: int = 50,
) -> List[Dict[str, Any]]:
    """单行分块（便捷函数）

    Returns:
        [{"index": 0, "content": "...", "char_count": N}, ...]
    """
    chunker = SemanticChunker(
        max_chars=max_chars, min_chars=min_chars, overlap=overlap
    )
    return chunker.split_text(text)


__all__ = ["SemanticChunker", "chunk_text"]