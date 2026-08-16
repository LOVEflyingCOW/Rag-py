"""默认数据插件：test_kb + test_document fixture"""
from __future__ import annotations

import asyncio
from typing import Any

from conftest_plugins import import_attr


def _get_proj(cfg: dict, key: str, default: Any) -> Any:
    return (((cfg or {}).get("project") or {}).get(key)) or default


def register_fixtures(registry, cfg: dict) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    # 缺省用 RAG-PY 的实体路径（零配置 fallback）；新项目如不通用可自行覆盖 plugin
    kb_path = _get_proj(cfg, "kb_model_path", "app.models.entities:KnowledgeBase")
    doc_path = _get_proj(cfg, "document_model_path", "app.models.entities:Document")

    # 通用项目可能没有 KB/Document 模型 → import 失败就跳过，不炸整个 conftest
    try:
        KB = import_attr(kb_path)
        Doc = import_attr(doc_path)
    except (ImportError, AttributeError, ModuleNotFoundError):
        return

    def _ts() -> str:
        return str(int(asyncio.get_event_loop().time() * 1000) % 10000000)

    async def test_kb(db_session: AsyncSession, test_user):
        ts = _ts()
        # 尝试用通用字段：name / user_id，不存在的字段从 kwargs 剔除
        kb_kwargs = {
            "name": f"pytest_kb_{ts}",
            "description": "conftest 生成",
            "user_id": test_user.id,
        }
        for extra_k, extra_v in {
            "embedding_model": "default",
            "chunk_size": 500,
            "chunk_overlap": 50,
            "is_public": False,
            "status": "active",
            "total_documents": 0,
            "total_chunks": 0,
        }.items():
            if hasattr(KB, extra_k):
                kb_kwargs[extra_k] = extra_v
        kb = KB(**kb_kwargs)
        db_session.add(kb)
        await db_session.commit()
        await db_session.refresh(kb)
        return kb

    async def test_document(db_session: AsyncSession, test_user, test_kb):
        doc_kwargs = {
            "filename": "lorem.md",
            "file_type": "markdown",
            "file_size": 0,
        }
        for extra_k, extra_v in {
            "status": "ready",
            "total_chunks": 1,
        }.items():
            if hasattr(Doc, extra_k):
                doc_kwargs[extra_k] = extra_v
        # 关联 KB 的外键字段名可能叫 knowledge_base_id / kb_id，检测下
        kb_fk = "knowledge_base_id" if hasattr(Doc, "knowledge_base_id") else "kb_id"
        doc_kwargs[kb_fk] = test_kb.id
        d = Doc(**doc_kwargs)
        db_session.add(d)
        await db_session.commit()
        await db_session.refresh(d)
        return d

    registry.register("test_kb", test_kb, scope="function")
    registry.register("test_document", test_document, scope="function")
