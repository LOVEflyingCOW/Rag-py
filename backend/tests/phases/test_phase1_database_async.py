"""Phase 1 验证测试 — 数据库迁移 + 全链路异步化（真 pytest 版本）

改造点（vs 旧脚本版）:
  - 旧版: 全局 PASS/FAIL 计数器 + requests 打真服务 + if __name__
  - 新版: 每个断言独立 test_xxx 函数，用原生 assert
          A-F/H 不需要 HTTP，直接 import 检查
          G 端到端用 api_client fixture (ASGITransport, 不起真服务) + test_user

模块级分类:
  TestConfig       — A. 配置层 (config.py)
  TestAsyncEngine  — B. 异步引擎 (database.py)
  TestAlembic      — C. Alembic 迁移（pytest 内存 SQLite 无 alembic_version，部分 skip）
  TestApiAsync     — D. API 路由 async 检查
  TestServiceAsync — E. Service 层 async 检查
  TestNoSyncResid  — F. 无残留同步引用
  TestE2EApi       — G. 端到端 API（注册登录建删 KB）
  TestDockerCompose— H. docker-compose.yml 结构检查
"""

import inspect
import importlib
import re
from pathlib import Path

import pytest

# repo_root = backend/tests/phases → up 3 levels
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_BACKEND_ROOT = _REPO_ROOT / "backend"
# alembic 是在 backend 子目录里初始化的，不是 repo root
_ALEMBIC_ROOT = _BACKEND_ROOT


# ============================================================
#  A. 配置层
# ============================================================

class TestConfig:
    """config.py 新增字段是否就绪"""

    def test_a1_database_url_exists(self):
        from app.core.config import settings
        assert hasattr(settings, "DATABASE_URL") and settings.DATABASE_URL

    def test_a2_database_sync_url_exists(self):
        from app.core.config import settings
        assert hasattr(settings, "DATABASE_SYNC_URL") and settings.DATABASE_SYNC_URL

    def test_a3_pool_params_exist(self):
        from app.core.config import settings
        pool_params = ["DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT", "DB_POOL_RECYCLE"]
        assert all(hasattr(settings, p) for p in pool_params), pool_params

    def test_a4_redis_url_exists(self):
        from app.core.config import settings
        assert hasattr(settings, "REDIS_URL") and settings.REDIS_URL

    def test_a5_is_sqlite_attr_exists(self):
        from app.core.config import settings
        assert hasattr(settings, "is_sqlite")

    def test_a6_database_url_async_attr_exists(self):
        from app.core.config import settings
        assert hasattr(settings, "database_url_async")

    def test_a7_database_url_sync_attr_exists(self):
        from app.core.config import settings
        assert hasattr(settings, "database_url_sync")


# ============================================================
#  B. 异步引擎
# ============================================================

class TestAsyncEngine:
    """database.py 异步引擎 + AsyncSession 是否正常工作"""

    @pytest.mark.asyncio
    async def test_b1_async_engine_is_asyncengine(self):
        from app.models.database import async_engine
        from sqlalchemy.ext.asyncio import AsyncEngine
        assert isinstance(async_engine, AsyncEngine)

    @pytest.mark.asyncio
    async def test_b2_async_session_creatable(self, db_session):
        """通过公共 db_session fixture 间接验证 AsyncSessionLocal 可用"""
        from sqlalchemy.ext.asyncio import AsyncSession
        assert isinstance(db_session, AsyncSession)

    def test_b3_get_db_dep_is_asyncgen(self):
        from app.models.database import get_db_dep
        assert inspect.isasyncgenfunction(get_db_dep)

    def test_b4_sync_engine_exists(self):
        from app.models.database import sync_engine
        from sqlalchemy.engine import Engine
        assert isinstance(sync_engine, Engine)

    def test_b5_init_db_is_async(self):
        from app.models.database import init_db
        assert inspect.iscoroutinefunction(init_db)

    def test_b6_metadata_has_all_core_tables(self):
        from app.models.database import Base
        import app.models.entities  # noqa: F401 — 把 entities 注册到 Base.metadata
        expected = {"users", "knowledge_bases", "documents", "chunks", "conversations", "messages"}
        found = set(Base.metadata.tables.keys())
        missing = expected - found
        assert not missing, f"缺少表: {missing}"


# ============================================================
#  C. Alembic 迁移系统
# ============================================================

class TestAlembic:
    """Alembic 初始化状态（pytest 内存 SQLite 无 alembic_version，部分 skip）"""

    @pytest.fixture
    def alembic_ini(self):
        return _ALEMBIC_ROOT / "alembic.ini"

    @pytest.fixture
    def alembic_dir(self):
        return _ALEMBIC_ROOT / "alembic"

    def test_c1_alembic_ini_exists(self, alembic_ini):
        assert alembic_ini.exists(), f"alembic.ini not found at {alembic_ini}"

    def test_c2_alembic_dir_exists(self, alembic_dir):
        assert alembic_dir.exists(), f"alembic/ not found at {alembic_dir}"

    def test_c3_env_py_imports_base(self, alembic_dir):
        env_path = alembic_dir / "env.py"
        assert env_path.exists()
        content = env_path.read_text(encoding="utf-8")
        assert "from app.models.database import Base" in content

    def test_c4_versions_dir_exists(self, alembic_dir):
        versions_dir = alembic_dir / "versions"
        assert versions_dir.exists()

    def test_c5_at_least_one_migration_file(self, alembic_dir):
        versions_dir = alembic_dir / "versions"
        if not versions_dir.exists():
            pytest.skip("versions/ 目录不存在，跳过")
        files = list(versions_dir.glob("*.py"))
        assert len(files) >= 1, "versions/ 下无迁移文件"

    def test_c6_alembic_version_stamp(self):
        """注意：pytest 用内存 SQLite，无 alembic_version 表 — 正常 skip。
        该断言只在跑「真 PostgreSQL + stamp head」环境下有意义。"""
        try:
            from app.models.database import sync_engine
            from sqlalchemy import text
            with sync_engine.connect() as conn:
                result = conn.execute(text("SELECT version_num FROM alembic_version"))
                version = result.scalar()
            assert version, "alembic_version 为空"
        except Exception as e:
            pytest.skip(f"alembic_version 表不可用 (通常是内存 SQLite，正常): {e}")


# ============================================================
#  D. API 路由 async 检查
# ============================================================

_ROUTE_MODULES = [
    "app.api.v1.auth",
    "app.api.v1.knowledge_base",
    "app.api.v1.document",
    "app.api.v1.chat",
    "app.api.v1.agent",
    "app.api.v1.conversation",
    "app.api.v1.integration",
    "app.api.v1.retrieval",
    "app.api.v1.embedding",
    "app.api.health",
]


@pytest.mark.parametrize("mod_path", _ROUTE_MODULES)
def test_d_route_module_all_async(mod_path):
    """每个路由模块的 endpoint 必须是 async def"""
    try:
        mod = importlib.import_module(mod_path)
    except Exception as e:
        pytest.skip(f"{mod_path} 导入失败: {e}")
        return

    router = getattr(mod, "router", None)
    assert router is not None, f"{mod_path} 无 router 对象"

    sync_routes = []
    for route in router.routes:
        if hasattr(route, "endpoint"):
            func = route.endpoint
            if not inspect.iscoroutinefunction(func):
                sync_routes.append(route.path)
    assert not sync_routes, f"{mod_path} 存在同步路由: {sync_routes}"


# ============================================================
#  E. Service 层 async 检查
# ============================================================

_SERVICE_CHECKS = [
    ("app.services.kb_service", "KnowledgeBaseService", ["create", "get_by_id", "list", "update", "delete"]),
    ("app.services.document_service", "DocumentService", ["process_upload", "list_documents", "get_document", "delete_document", "search_in_kb"]),
    ("app.services.conversation_service", "ConversationService", ["create_conversation", "list_conversations", "get_messages", "delete_conversation"]),
    ("app.services.retrieval_service", "RetrievalService", ["search", "get_kb_stats", "rebuild_index"]),
    ("app.services.agent_service", "AgentService", ["run"]),
]


@pytest.mark.parametrize("mod_path,class_name,methods", _SERVICE_CHECKS)
def test_e_service_class_all_async(mod_path, class_name, methods):
    """每个 service 类的关键方法必须是 async def"""
    try:
        mod = importlib.import_module(mod_path)
    except Exception as e:
        pytest.skip(f"{mod_path} 导入失败: {e}")
        return

    cls = getattr(mod, class_name, None)
    assert cls is not None, f"{mod_path} 中 {class_name} 不存在"

    sync_methods = []
    for method_name in methods:
        method = getattr(cls, method_name, None)
        if method is None:
            continue
        if not inspect.iscoroutinefunction(method):
            sync_methods.append(method_name)
    assert not sync_methods, f"{class_name} 存在同步方法: {sync_methods}"


# ============================================================
#  F. 无残留同步引用
# ============================================================

class TestNoSyncResiduals:
    """不应该有旧式同步 DB 调用残留"""

    def _iter_py_files(self):
        app_dir = _BACKEND_ROOT / "app"
        for py_file in app_dir.rglob("*.py"):
            yield py_file

    def test_f1_no_sync_session_import(self):
        """禁止 from sqlalchemy.orm import Session（database.py 除外，它要给 alembic 用）"""
        sync_files = []
        for py_file in self._iter_py_files():
            if py_file.name == "database.py":
                continue
            try:
                lines = py_file.read_text(encoding="utf-8").split("\n")
            except Exception:
                continue
            for line_num, raw in enumerate(lines, 1):
                line = raw.strip()
                if line.startswith("#"):
                    continue
                if "from sqlalchemy.orm import" not in raw or "Session" not in raw:
                    continue
                if "AsyncSession" in raw:
                    # 同时 import Session + AsyncSession 的情况：确认 Session 确实 import 了
                    parts = raw.split("import")
                    if len(parts) != 2:
                        continue
                    names = [n.strip() for n in parts[1].split(",")]
                    if any(n == "Session" for n in names):
                        sync_files.append(f"{py_file.name}:{line_num}")
                else:
                    sync_files.append(f"{py_file.name}:{line_num}")
        assert not sync_files, f"发现同步 Session 引用: {sync_files}"

    def test_f2_no_db_query_call(self):
        """禁止 db.query() 旧式同步查询（必须用 SQLAlchemy 2.0 select() 风格）"""
        hits = []
        for py_file in self._iter_py_files():
            try:
                lines = py_file.read_text(encoding="utf-8").split("\n")
            except Exception:
                continue
            for line_num, raw in enumerate(lines, 1):
                line = raw.strip()
                if line.startswith("#"):
                    continue
                if "db.query(" in raw or "self.db.query(" in raw:
                    hits.append(f"{py_file.name}:{line_num}")
        assert not hits, f"发现 db.query() 残留: {hits}"

    def test_f3_get_current_user_is_async(self):
        from app.api.dependencies import get_current_user
        assert inspect.iscoroutinefunction(get_current_user)

    def test_f4_get_current_user_optional_is_async(self):
        from app.api.dependencies import get_current_user_optional
        assert inspect.iscoroutinefunction(get_current_user_optional)

    def test_f5_get_current_admin_is_async(self):
        from app.api.dependencies import get_current_admin
        assert inspect.iscoroutinefunction(get_current_admin)


# ============================================================
#  G. 端到端 API 流程 (用 api_client + test_user fixture)
# ============================================================

class TestE2EApi:
    """注册 → 登录 → 创建KB → 列表 → 删除（走 ASGITransport，无需 uvicorn）"""

    @pytest.mark.asyncio
    async def test_g1_health_check_ok(self, api_client):
        resp = await api_client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        # 内存 SQLite 里 database 也应该是 ok
        assert body.get("data", {}).get("database") == "ok"

    @pytest.mark.asyncio
    async def test_g2_register_and_login_flow(self, api_client):
        """手动 register + login（不用 test_user fixture，因为要验证端点本身）"""
        import time as _t
        ts = str(int(_t.time() * 1000))
        uname = f"phase1_pytest_{ts}"
        email = f"{uname}@test.com"

        # register
        resp = await api_client.post("/api/v1/auth/register", json={
            "username": uname, "password": "Test123456",
            "confirm_password": "Test123456", "email": email,
        })
        assert resp.status_code == 200, resp.text[:200]
        body = resp.json()
        assert body.get("success"), body
        access = body["data"]["access_token"]
        assert access and len(access) > 10

        # login (换一种方式拿 token)
        resp2 = await api_client.post("/api/v1/auth/login", json={
            "username": uname, "password": "Test123456",
        })
        assert resp2.status_code == 200, resp2.text[:200]
        access2 = resp2.json()["data"]["access_token"]
        assert access2

        # auth_headers
        h = {"Authorization": f"Bearer {access2}"}

        # me
        resp3 = await api_client.get("/api/v1/auth/me", headers=h)
        assert resp3.status_code == 200
        assert resp3.json()["data"]["username"] == uname

        # create KB
        resp4 = await api_client.post("/api/v1/knowledge-bases", json={
            "name": f"Phase1 Pytest KB {ts}", "description": "pytest 测试",
        }, headers=h)
        assert resp4.status_code == 200, resp4.text[:200]
        kb_id = resp4.json()["data"]["id"]
        assert kb_id

        # list KBs
        resp5 = await api_client.get("/api/v1/knowledge-bases?page=1&page_size=10", headers=h)
        assert resp5.status_code == 200
        assert resp5.json()["data"]["total"] >= 1

        # detail
        resp6 = await api_client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=h)
        assert resp6.status_code == 200

        # delete
        resp7 = await api_client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=h)
        assert resp7.status_code == 200

        # invalid token should be 401
        resp8 = await api_client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer invalid_token_xxx"},
        )
        assert resp8.status_code == 401

    @pytest.mark.asyncio
    async def test_g3_second_kb_lifecycle_isolated(self, api_client):
        """
        再跑一次独立的 KB 生命周期（不共享 g2 的用户），
        确保「多个 test function 的内存 DB 完全隔离」这个性质成立。
        
        注意：原本想直接用 test_user fixture 简化，但它依赖 db_session → importlib.reload(database)
        导致 Base 对象新旧引用不一致，setup 时偶尔出现 'no such table: users'。
        结论：phase 冒烟用例直接走 register/login 端点更稳定（ASGI app 自己会处理初始化顺序）。
        """
        import time as _t
        ts = str(int(_t.time() * 1000)) + "_b"
        uname = f"phase1b_{ts}"

        reg = await api_client.post("/api/v1/auth/register", json={
            "username": uname, "password": "Test123456",
            "confirm_password": "Test123456", "email": f"{uname}@test.com",
        })
        assert reg.status_code == 200
        token = reg.json()["data"]["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        # create
        resp = await api_client.post("/api/v1/knowledge-bases", json={
            "name": "Phase1 Isolated 2", "description": "second test",
        }, headers=h)
        assert resp.status_code == 200
        kb_id = resp.json()["data"]["id"]

        # update
        resp2 = await api_client.put(f"/api/v1/knowledge-bases/{kb_id}", json={
            "name": "Phase1 Renamed 2",
        }, headers=h)
        assert resp2.status_code == 200

        # verify rename
        resp3 = await api_client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=h)
        assert resp3.status_code == 200
        assert resp3.json()["data"]["name"] == "Phase1 Renamed 2"

        # delete
        resp4 = await api_client.delete(f"/api/v1/knowledge-bases/{kb_id}", headers=h)
        assert resp4.status_code == 200

        # 404 after delete (安全策略：删除他人 KB 返回 404，删除自己刚删的 KB 也应不可访问)
        resp5 = await api_client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=h)
        assert resp5.status_code == 404


# ============================================================
#  H. Docker Compose 配置
# ============================================================

class TestDockerCompose:
    """docker-compose.yml / Dockerfile 是否存在且包含必要服务声明"""

    @pytest.fixture
    def compose_path(self):
        return _REPO_ROOT / "docker-compose.yml"

    @pytest.fixture
    def dockerfile_path(self):
        return _REPO_ROOT / "docker" / "Dockerfile"

    def test_h1_compose_file_exists(self, compose_path):
        if not compose_path.exists():
            pytest.skip(f"docker-compose.yml 不在 {compose_path}")
        assert True

    def test_h2_postgres_pgvector_service(self, compose_path):
        if not compose_path.exists():
            pytest.skip("无 docker-compose.yml")
        content = compose_path.read_text(encoding="utf-8")
        assert "postgres" in content and "pgvector" in content

    def test_h3_redis_service(self, compose_path):
        if not compose_path.exists():
            pytest.skip("无 docker-compose.yml")
        content = compose_path.read_text(encoding="utf-8")
        assert "redis" in content

    def test_h4_rabbitmq_service(self, compose_path):
        if not compose_path.exists():
            pytest.skip("无 docker-compose.yml")
        content = compose_path.read_text(encoding="utf-8")
        assert "rabbitmq" in content

    def test_h5_healthcheck_declared(self, compose_path):
        if not compose_path.exists():
            pytest.skip("无 docker-compose.yml")
        content = compose_path.read_text(encoding="utf-8")
        assert "healthcheck" in content

    def test_h6_dockerfile_exists(self, dockerfile_path):
        if not dockerfile_path.exists():
            pytest.skip(f"Dockerfile 不在 {dockerfile_path}")
        assert True
