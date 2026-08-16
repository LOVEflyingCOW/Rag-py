"""默认 DB 插件：SQLite 内存库 + aiosqlite，每次 test 事务回滚

通用化说明：
  - 项目特有的「entities 子模块列表」可在 project_config.yaml 的
    project.entity_submodules 字段里覆盖（list[str]）。
  - 插件默认会尝试 tenant/user/knowledge_base/document/conversation/auth
    这 6 个「典型 RAG 项目命名」的子模块，缺哪个会静默跳过。

### 坑点记录（2026-03-18 PHASE7 phase1 pytest 调试出的真实 bug）——【为什么要 reload entities】
  Python 的 `importlib.reload(X)` **只 reload X 自身**，不会递归 reload X 引用
  过的模块，也不会 reload 「引用了 X 的模块」。

  在我们的结构里：
    app.models.database      → 定义了 Base = DeclarativeBase()
    app.models.entities.user → `from app.models.database import Base`
                               class User(Base): __tablename__ = "users"

  测试时我们想「换一张干净的内存 SQLite」，需要让 database 模块的 settings 生效，
  所以要 reload(app.models.database)。如果只 reload database，会发生：
    1) database 重新初始化 → 新的 Base 对象诞生，Base.metadata = {}（空）
    2) entities.user 这个模块还在 sys.modules 里缓存着
    3) User 类还是「继承旧 Base」的那个类，没有注册到新 Base 的 metadata 上
    4) test_engine 里 `Base.metadata.create_all(engine)` 建了 0 张表
    5) API 注册用户时报：`sqlalchemy.exc.OperationalError: no such table: users`

  修复方法：**reload database 之后，立刻 reload 所有 entities 子模块 + entities 包入口**。
  让实体类重新 import 一次新的 Base 并注册到新 Base.metadata。
"""
from __future__ import annotations

import importlib
from typing import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from conftest_plugins import import_attr


TEST_SQLITE_URL = "sqlite+aiosqlite:///:memory:"


def _get(cfg: dict, key: str, default: str) -> str:
    return (((cfg or {}).get("project") or {}).get(key)) or default


def register_fixtures(registry, cfg: dict) -> None:
    # ---- 常量 / 路径（从 project_config.yaml 取） ----
    settings_path = _get(cfg, "settings_path", "app.core.config:settings")
    base_meta_path = _get(cfg, "base_metadata_path", "app.models.database:Base")
    entities_module = _get(cfg, "entities_module", "app.models.entities")
    get_db_dep_path = _get(cfg, "get_db_dep_path", "app.api.dependencies:get_db_dep")

    # base_meta_path = "app.models.database:Base" → database 模块路径
    db_module_dotted = base_meta_path.split(":")[0]
    _db = importlib.import_module(db_module_dotted)

    # 项目级「entities 子模块列表」：entities/__init__.py 里显式 from .xxx import 的那些
    project_section = (cfg or {}).get("project") or {}
    _entity_submodules: list[str]
    if project_section.get("entity_submodules"):
        _entity_submodules = list(project_section["entity_submodules"])
    else:
        # 默认 6 个「典型 RAG 项目」实体。通用化：新项目实体结构不同可在 yaml 覆盖
        _entity_submodules = [
            f"{entities_module}.tenant",
            f"{entities_module}.user",
            f"{entities_module}.knowledge_base",
            f"{entities_module}.document",
            f"{entities_module}.conversation",
            f"{entities_module}.auth",
        ]

    # ============================================================
    #  预加载 (Critical!): 提前 import 完整 entities 包，避免「部分 import」
    #  导致 SQLAlchemy class_registry 里缺少 Tenant 等类，然后
    #  mapper.configure() 报 "expression 'Tenant' failed to locate a name"。
    #
    #  触发场景：全量 pytest 跑 phase + unit + api + integration 时，
    #    phase4 reranker_chunker / fulltext_search 等模块顶层只做
    #    `from app.models.entities.document import DocumentChunk`，
    #    此时 SQLAlchemy 如果自动 configure mappers，会看到 User 的
    #    relationship("Tenant") 但 registry 里还没有 Tenant，
    #    之后就算 reload 了全部 entities，configure mappers 也不会
    #    重新触发（已经配置过且状态脏了）。
    # ============================================================
    try:
        importlib.import_module(entities_module)
    except Exception:
        pass
    for _m in _entity_submodules:
        try:
            importlib.import_module(_m)
        except Exception:
            # 通用化：项目不一定有全部子模块，静默 skip
            pass
    try:
        from sqlalchemy.orm import configure_mappers
        configure_mappers()  # 预触发一次，提前暴露关系问题（而不是 test 中间炸）
    except Exception:
        pass

    # ============================================================
    #  工具函数：reload database + entities
    # ============================================================
    def _reload_all_entities() -> None:
        """按「子模块 → 包入口」顺序 reload，保证实体重新绑定最新 Base。

        顺序重要原因：
          entities/__init__.py 里是 `from .user import User`，
          只有先让 user.py 重新执行（重新定义 User，继承新 Base），
          __init__.py 再重新 import 才能拿到新 User。
        """
        for mod_dotted in _entity_submodules:
            try:
                mod = importlib.import_module(mod_dotted)
                importlib.reload(mod)
            except Exception:
                # 通用化：项目不一定有全部子模块，跳过即可
                continue
        try:
            pkg = importlib.import_module(entities_module)
            importlib.reload(pkg)
        except Exception:
            pass

    def _reinit_database_module(db_url: str) -> None:
        """切换 database 模块里读写的 DB URL，并连带 reload database + entities。"""
        settings = import_attr(settings_path)
        settings.DATABASE_URL = db_url
        sync_url = db_url
        if sync_url.startswith("sqlite+aiosqlite:///"):
            sync_url = sync_url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
        settings.DATABASE_SYNC_URL = sync_url
        importlib.reload(_db)
        # 关键：reload database 后必须立刻 reload entities，否则实体仍绑定旧 Base
        _reload_all_entities()

    # ============================================================
    #  Fixtures
    # ============================================================
    async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
        engine = create_async_engine(
            TEST_SQLITE_URL, echo=False,
            connect_args={"check_same_thread": False},
        )
        # 先把 DB 模块「洗」成干净状态 + 确保 entities 注册到当前 Base
        _reinit_database_module(TEST_SQLITE_URL)
        Base = import_attr(base_meta_path)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield engine
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
        _reinit_database_module(TEST_SQLITE_URL)
        TestingSession = async_sessionmaker(
            test_engine, expire_on_commit=False, class_=AsyncSession
        )
        async with TestingSession() as session:
            yield session
            try:
                await session.rollback()
            except Exception:
                pass

    def override_get_db(db_session: AsyncSession):
        get_db_dep = import_attr(get_db_dep_path)

        async def _gen() -> AsyncGenerator[AsyncSession, None]:
            yield db_session

        return get_db_dep, _gen

    registry.register("test_engine", test_engine, scope="function")
    registry.register("db_session", db_session, scope="function")
    registry.register("override_get_db", override_get_db, scope="function")

    # 同时把 _reinit_database_module 暴露出去，给极个别特殊 fixture 用
    #（不是 fixture，是注册到 registry._g 里的工具函数）
    registry._g["_reinit_database_module__plugin"] = _reinit_database_module
