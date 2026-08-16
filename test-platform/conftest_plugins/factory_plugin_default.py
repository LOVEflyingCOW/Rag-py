"""默认 Factory Boy 数据工厂插件

注册内容（fixture 名 → 对应类）：
  - tenant_factory        → TenantFactory
  - user_factory          → UserFactory        (额外含 password 虚拟字段自动 hash)
  - knowledge_base_factory→ KnowledgeBaseFactory
  - document_factory      → DocumentFactory
  - document_chunk_factory→ DocumentChunkFactory
  - conversation_factory  → ConversationFactory
  - chat_message_factory  → ChatMessageRecordFactory
  - refresh_token_factory → RefreshTokenFactory
  - role_factory          → RoleFactory
  - permission_factory    → PermissionFactory
  - audit_log_factory     → AuditLogFactory

用法示例：
  async def test_xxx(db_session, user_factory):
      # acreate 用法：await factory_class.acreate(db_session, 覆盖字段=值)
      alice = await user_factory.acreate(db_session, username="alice", password="Secret@1")
      # abatch 用法：一次造 N 条，字段随机
      users = await user_factory.abatch(db_session, size=10)
      # 级联：直接造 KB 时，factory 会自动建 User + Tenant(可选)
      kb = await knowledge_base_factory.acreate(db_session, name="AI 知识库")
      assert kb.owner is not None  # 自动建的 owner
      assert kb.owner.id is not None

### 通用化说明（新项目接入）
  1. 在 project_config.yaml 里配置 factories_module: "your_project_factories"
     （放在 test-platform/factories/ 下，或任意 sys.path 可达处）
  2. 没有配置时，默认 fallback 到 RAG-PY 的 ragpy_factories
  3. 如果你的项目 factories 命名规范不是 {EntityName}Factory，
     可以重写本插件的 _load_from_module 方法，或者在 register_fixtures 里手动注册。
"""

from __future__ import annotations

import sys
import importlib
from pathlib import Path
from typing import Any, Dict

import pytest


TESTED_MODULES_CACHE: Dict[str, bool] = {}


def _get(cfg: dict, key: str, default: str) -> str:
    section = (cfg or {}).get("project") or {}
    return section.get(key) or default


def _ensure_testplatform_syspath(cfg: dict) -> None:
    """factories/ 在 test-platform 下，确保 sys.path 包含 test-platform 根"""
    repo_root = cfg.get("_repo_root")
    if repo_root:
        tp = str(Path(repo_root) / "test-platform")
        if tp not in sys.path:
            sys.path.insert(0, tp)


def _load_from_module(module_dotted: str, registry, cfg: dict) -> None:
    """加载 factories 模块，自动扫描以 Factory 结尾的类并注册 fixtures。

    注册规则：UserFactory → fixture 名 "user_factory"（CamelCase → snake_case + _factory 后缀保留）
    """
    import re
    mod = importlib.import_module(module_dotted)

    def camel_to_snake(name: str) -> str:
        # "UserFactory" → "user_factory", "ChatMessageRecordFactory" → "chat_message_record_factory"
        s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
        return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        # 过滤条件：是类 → 名字以 Factory 结尾 → 不是抽象 BaseAsyncFactory
        if (
            isinstance(attr, type)
            and attr_name.endswith("Factory")
            and attr.__name__ != "BaseAsyncFactory"
        ):
            fixture_name = camel_to_snake(attr_name)
            # 注册为 function scope fixture：直接返回 factory 类本身
            # 测试里调用 await xxx_factory.acreate(db_session, ...)
            def _make_factory_fixture(factory_cls):
                async def _fixture():
                    return factory_cls
                return _fixture
            registry.register(fixture_name, _make_factory_fixture(attr), scope="function")


def register_fixtures(registry, cfg: dict) -> None:
    _ensure_testplatform_syspath(cfg)

    # 1) 读项目配置，没配置就用 RAG-PY 默认
    factories_module = _get(
        cfg, "factories_module", "factories.ragpy_factories"
    )

    # 2) 尝试加载；失败时（新项目还没写 factories）给一个清晰的 SKIP 提示，不要炸 conftest
    try:
        _load_from_module(factories_module, registry, cfg)
    except ModuleNotFoundError as e:
        import warnings
        warnings.warn(
            f"[factory_plugin] factories_module={factories_module} 加载失败({e})，"
            f"factory fixtures 将不可用。如果是新项目，请在 test-platform/factories/ 下写工厂"
            f"或在 project_config.yaml 里设置 factories_module 指向正确模块。",
            stacklevel=2,
        )
        return

    # 3) 额外注册一个 faker_seed fixture（pytest-randomly 没装时的 fallback，固定复现用）
    def _faker_seed():
        """默认固定 seed=42，跑失败了想复现就直接用这个 seed；
        装了 pytest-randomly 后它会 override 这个 fixture。"""
        import random
        from faker import Faker as _Faker
        seed = 42
        random.seed(seed)
        _fake = _Faker("zh_CN")
        _fake.seed_instance(seed)
        try:
            import numpy as np  # type: ignore
            np.random.seed(seed)
        except Exception:
            pass
        return seed

    registry.register("faker_seed", _faker_seed, scope="function")
