"""Markdown 结构化解析器

将 Markdown 文本解析为结构化的"语义块"，例如:
  - HeadingBlock  (标题)
  - ParagraphBlock  (普通段落)
  - CodeBlock     (代码块)
  - ListBlock     (列表)
  - TableBlock    (表格)
  - QuoteBlock    (引用)

这样在后续分块时，可以尽量避免在语义边界（如标题和正文之间、代码块中）
进行强制切分，从而提升检索时的语义连贯性。

**设计原则：**
  1. 容错 —— 任何不规范的 Markdown 都不会崩溃，只会退化为纯文本段落
  2. 纯函数 —— parse(text) 仅依赖输入，无副作用
  3. 信息保留 —— 每个语义块保留原始文本 + 规范化文本
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional


# ============================================================
# 数据结构
# ============================================================

BLOCK_TYPES = (
    "heading",      # 标题: #, ##, ###
    "paragraph",    # 普通段落: 纯文本
    "code",         # 代码块: ``` ... ```
    "list",         # 列表: -, *, 1. 2.
    "table",        # 表格: | col1 | col2 |
    "quote",        # 引用: > ...
    "thematic",     # 分隔线: ---
)


@dataclass
class MarkdownBlock:
    """一个结构化的 Markdown 语义块"""
    block_type: str                    # BLOCK_TYPES 之一
    content: str                       # 规范化后的文本内容（用于后续分块/向量化）
    raw: str                           # 原始文本（保留 Markdown 语法）
    level: int = 0                     # 标题层级（1-6），其他类型为 0
    meta: dict = field(default_factory=dict)  # 额外信息（代码块的语言、列表项数等）

    @property
    def char_count(self) -> int:
        return len(self.content)


@dataclass
class ParsedMarkdown:
    """解析结果 —— 结构化 Markdown"""
    blocks: List[MarkdownBlock]
    original_text: str

    @property
    def total_chars(self) -> int:
        return sum(b.char_count for b in self.blocks)

    @property
    def total_blocks(self) -> int:
        return len(self.blocks)

    def summary(self) -> str:
        """简要统计信息（调试用）"""
        counts = {}
        for b in self.blocks:
            counts[b.block_type] = counts.get(b.block_type, 0) + 1
        parts = ["%s=%d" % (k, v) for k, v in counts.items()]
        return "blocks=%d (%s), total_chars=%d" % (
            len(self.blocks), ", ".join(parts), self.total_chars
        )


# ============================================================
# Markdown 解析器
# ============================================================

class MarkdownParser:
    '''Markdown 结构化解析器

    用法示例:
        parser = MarkdownParser()
        parsed = parser.parse("# 标题\\n\\n这里是一段正文。\\n\\n- 列表项 1\\n- 列表项 2")
        for block in parsed.blocks:
            print("[%s L%d] %s" % (block.block_type, block.level, block.content[:40]))
    '''

    # --- 正则 ---
    RE_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
    RE_CODE_FENCE = re.compile(r"^(`{3,}|~{3,})\s*([\w+-]*)\s*$")
    RE_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.+)$")
    RE_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
    RE_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
    RE_QUOTE = re.compile(r"^>\s?(.*)$")
    RE_THEMATIC = re.compile(r"^\s*([-*_])\s*\1\s*\1[\s\S]*$")

    def parse(self, text: str) -> ParsedMarkdown:
        """解析 Markdown 文本为结构化块"""
        if not text:
            return ParsedMarkdown(blocks=[], original_text="")

        lines = text.replace("\r\n", "\n").split("\n")
        blocks: List[MarkdownBlock] = []
        i = 0
        n = len(lines)

        while i < n:
            line = lines[i].rstrip()

            # --- 空行跳过 ---
            if not line.strip():
                i += 1
                continue

            # --- 分隔线 ---
            if self.RE_THEMATIC.match(line) and len(line.strip()) <= 50:
                blocks.append(MarkdownBlock(
                    block_type="thematic", content="", raw=line, level=0
                ))
                i += 1
                continue

            # --- 标题: ### Title ---
            m = self.RE_HEADING.match(line)
            if m:
                level = len(m.group(1))
                content = m.group(2).strip()
                blocks.append(MarkdownBlock(
                    block_type="heading",
                    content=content,
                    raw=line,
                    level=level,
                    meta={"level": level},
                ))
                i += 1
                continue

            # --- 代码块: ```python ... ``` ---
            m_fence = self.RE_CODE_FENCE.match(line)
            if m_fence:
                fence_chars = m_fence.group(1)
                lang = m_fence.group(2) or ""
                code_lines: List[str] = []
                j = i + 1
                while j < n:
                    cl = lines[j]
                    if cl.strip().startswith(fence_chars[0] * 3) and self.RE_CODE_FENCE.match(cl):
                        j += 1
                        break
                    code_lines.append(cl)
                    j += 1
                code_text = "\n".join(code_lines).strip()
                raw_text = "\n".join(lines[i:j])
                blocks.append(MarkdownBlock(
                    block_type="code",
                    content=code_text,
                    raw=raw_text,
                    level=0,
                    meta={"language": lang, "line_count": len(code_lines)},
                ))
                i = j
                continue

            # --- 引用: > text ---
            if self.RE_QUOTE.match(line):
                quote_lines = []
                j = i
                while j < n:
                    m_q = self.RE_QUOTE.match(lines[j])
                    if m_q:
                        quote_lines.append(m_q.group(1).strip())
                        j += 1
                    else:
                        break
                content = "\n".join(quote_lines).strip()
                raw = "\n".join(lines[i:j])
                blocks.append(MarkdownBlock(
                    block_type="quote", content=content, raw=raw, level=0,
                    meta={"line_count": len(quote_lines)},
                ))
                i = j
                continue

            # --- 表格 ---
            if self.RE_TABLE_ROW.match(line) and i + 1 < n and self.RE_TABLE_SEP.match(lines[i + 1]):
                table_lines = [line]
                j = i + 1
                while j < n and self.RE_TABLE_ROW.match(lines[j]):
                    table_lines.append(lines[j])
                    j += 1
                header_cells = self._split_cells(table_lines[0])
                data_rows = [self._split_cells(row) for row in table_lines[2:]]
                content_rows = []
                for r in data_rows:
                    parts = []
                    for h, v in zip(header_cells, r):
                        parts.append("%s=%s" % (h.strip(), v.strip()))
                    content_rows.append("; ".join(parts))
                content = " | ".join(content_rows).strip()
                if not content:
                    content = "表头: " + ", ".join(h.strip() for h in header_cells)
                raw = "\n".join(lines[i:j])
                blocks.append(MarkdownBlock(
                    block_type="table", content=content, raw=raw, level=0,
                    meta={"header": header_cells, "row_count": len(data_rows)},
                ))
                i = j
                continue

            # --- 列表 ---
            if self.RE_LIST_ITEM.match(line):
                list_items: List[str] = []
                j = i
                while j < n:
                    m_li = self.RE_LIST_ITEM.match(lines[j])
                    if m_li:
                        list_items.append(m_li.group(3).strip())
                        j += 1
                    else:
                        # 继续：列表项可能有多行（下一行缩进）
                        if j > i and lines[j].startswith(" ") and lines[j].strip():
                            list_items[-1] = (list_items[-1] + " " + lines[j].strip())
                            j += 1
                        else:
                            break
                content = "\n".join("- " + item for item in list_items if item)
                raw = "\n".join(lines[i:j])
                blocks.append(MarkdownBlock(
                    block_type="list", content=content, raw=raw, level=0,
                    meta={"item_count": len(list_items)},
                ))
                i = j
                continue

            # --- 普通段落（收集连续非空行） ---
            para_lines: List[str] = []
            j = i
            while j < n:
                cur_line = lines[j].rstrip()
                if not cur_line.strip():
                    break
                # 避免吞掉后续特殊块
                if self.RE_HEADING.match(cur_line) or self.RE_CODE_FENCE.match(cur_line):
                    break
                para_lines.append(cur_line.strip())
                j += 1
            if para_lines:
                content = " ".join(para_lines).strip()
                raw = "\n".join(lines[i:j])
                blocks.append(MarkdownBlock(
                    block_type="paragraph", content=content, raw=raw, level=0,
                ))
            i = j if j > i else i + 1

        # 收尾：如果最后有未处理的空列表块，忽略
        return ParsedMarkdown(blocks=blocks, original_text=text)

    @staticmethod
    def _split_cells(row: str) -> List[str]:
        """将表格行切分为单元格"""
        parts = row.strip()
        if parts.startswith("|"):
            parts = parts[1:]
        if parts.endswith("|"):
            parts = parts[:-1]
        cells = [c.strip() for c in parts.split("|")]
        return cells

    # Markdown 行内格式（粗体/斜体/删除线/行内代码）
    _RE_INLINE_BOLD = re.compile(r"(\*\*|__)(?P<body>.*?)\1")
    _RE_INLINE_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(?P<body>[^*\n]+)\*(?!\*)")
    _RE_INLINE_STRIPE = re.compile(r"(~~)(?P<body>.*?)\1")
    _RE_INLINE_CODE = re.compile(r"`([^`]+)`")
    _RE_LINK = re.compile(r"\[(?P<text>[^\]]+)\]\([^)]+\)")
    _RE_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]+\)")

    @classmethod
    def _strip_inline_format(cls, text: str) -> str:
        """剥离 Markdown 行内格式符号，返回纯文本"""
        if not text:
            return ""
        s = cls._RE_IMAGE.sub("", text)
        s = cls._RE_LINK.sub(lambda m: m.group("text"), s)
        s = cls._RE_INLINE_CODE.sub(r"\1", s)
        s = cls._RE_INLINE_BOLD.sub(r"\g<body>", s)
        s = cls._RE_INLINE_ITALIC.sub(r"\g<body>", s)
        s = cls._RE_INLINE_STRIPE.sub(r"\g<body>", s)
        return s

    def to_plain_text(self, text: str) -> str:
        """便捷方法：解析 Markdown 并返回纯文本（拼接所有块的 content，并剥离行内格式）"""
        parsed = self.parse(text or "")
        parts: List[str] = []
        for b in parsed.blocks:
            if b.content:
                parts.append(self._strip_inline_format(b.content))
        return "\n".join(parts)


# ============================================================
# 便捷函数
# ============================================================

def parse_markdown(text: str) -> ParsedMarkdown:
    """解析 Markdown（便捷函数）"""
    return MarkdownParser().parse(text)


__all__ = [
    "MarkdownBlock",
    "ParsedMarkdown",
    "MarkdownParser",
    "parse_markdown",
    "BLOCK_TYPES",
]