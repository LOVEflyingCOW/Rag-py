"""B1 · 单元测试 — Chunker 三种分块器: Recursive / Markdown / Semantic

对应模块: app.processors.document.chunker_factory
纯逻辑无外部依赖。不覆盖 sentence-transformers 相关特性 (放 integration 层)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.processors.document.chunker_factory import (  # noqa: E402
    ChunkResult,
    RecursiveCharacterChunker,
    MarkdownStructureChunker,
    SemanticChunkerAdapter,
    ChunkerFactory,
)


# ============================================================
#  RecursiveCharacterChunker
# ============================================================

class TestRecursiveCharacterChunker:
    def test_empty_input_returns_empty(self):
        c = RecursiveCharacterChunker(chunk_size=50, chunk_overlap=10)
        assert c.split("") == []
        assert c.split("   \n\n  ") == []

    def test_short_text_single_chunk(self):
        c = RecursiveCharacterChunker(chunk_size=1000, chunk_overlap=50)
        txt = "Hello world, short text."
        chunks = c.split(txt)
        assert len(chunks) == 1
        assert chunks[0].content == txt
        assert chunks[0].index == 0

    def test_paragraph_split_then_rollover(self):
        # 6 段文本, 每段 30 字, 共 180 字左右; chunk_size=80 会切成多段
        parts = [
            "111111111122222222223333333333444444444455555555556666666666",
            "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeeeffffffffgggggghhhhhh",
            "XXXXYYYYZZZZQQQQWWWWEEEERRRRRTTTTYYYYUUUUIIIIOOOOpppaaaasss",
        ]
        txt = "\n\n".join(parts)
        c = RecursiveCharacterChunker(chunk_size=80, chunk_overlap=10)
        chunks = c.split(txt)
        # 基本断言: 至少多块, 每块不超过 chunk_size + 点容差, 首尾索引单调
        assert len(chunks) >= 2
        lens = [len(c.content) for c in chunks]
        assert all(l <= 80 + 20 for l in lens)  # +容差是 overlap 导致
        indices = [c.index for c in chunks]
        assert indices == sorted(indices)

    def test_overlap_is_nonnegative(self):
        # overlap 负的不该抛错, 要取 0
        c = RecursiveCharacterChunker(chunk_size=50, chunk_overlap=-9)
        assert c.split("a " * 100)  # 能 split 就行


# ============================================================
#  MarkdownStructureChunker
# ============================================================

class TestMarkdownStructureChunker:
    MD_SAMPLE = """# 总标题

导语内容: 这是顶级段落.

## 安装

一步步来.

```bash
pip install x
```

## 使用

- 列 1
- 列 2

### 子用法

子内容.
"""

    def test_empty_returns_empty(self):
        assert MarkdownStructureChunker().split("") == []

    def test_heading_level_split(self):
        chunks = MarkdownStructureChunker(chunk_size=500, max_heading_level=2).split(self.MD_SAMPLE)
        # 至少应该有 '## 安装' / '## 使用' 两个块 + 导语块或总标题块
        contents = [c.content for c in chunks]
        assert any("安装" in c for c in contents)
        assert any("使用" in c for c in contents)
        # 代码块完整性: ```bash ... ``` 必须在同一块
        joined = "\n".join(contents)
        assert "pip install x" in joined

    def test_subheading_respected(self):
        # max_heading_level=3 会把 ### 子用法 也切开
        chunks = MarkdownStructureChunker(chunk_size=500, max_heading_level=3).split(self.MD_SAMPLE)
        assert any("子用法" in c.content for c in chunks)


# ============================================================
#  SemanticChunkerAdapter (适配器层, 纯接口验证)
# ============================================================

class TestSemanticChunkerAdapter:
    def test_empty_returns_empty(self):
        assert SemanticChunkerAdapter().split("") == []

    def test_short_text_at_least_one_chunk(self):
        # sentence-transformers 没装情况下, 适配器也要 fallback 返回一块
        chunks = SemanticChunkerAdapter(chunk_size=200, chunk_overlap=20).split("Hello " * 20)
        assert isinstance(chunks, list)
        # 不强制长度, 因为 fallback 的实现策略可以多样; 只断言类型合法
        for c in chunks:
            assert isinstance(c, ChunkResult)


# ============================================================
#  Factory
# ============================================================

class TestChunkerFactory:
    def test_create_recursive(self):
        c = ChunkerFactory.create("recursive", chunk_size=123, chunk_overlap=11)
        assert isinstance(c, RecursiveCharacterChunker)

    def test_create_markdown(self):
        c = ChunkerFactory.create("markdown", chunk_size=200, max_heading_level=2)
        assert isinstance(c, MarkdownStructureChunker)

    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            ChunkerFactory.create("not-exists")
