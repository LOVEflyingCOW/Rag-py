"""
Phase 1 验证测试 — 数据库迁移 + 全链路异步化

测试覆盖:
  A. 配置层: config.py 新增字段是否就绪
  B. 异步引擎: async engine + AsyncSession 是否正常工作
  C. Alembic: 迁移系统是否初始化成功
  D. 全链路 async: 所有 API 路由是否为 async def
  E. Service 层 async: 所有 service 方法是否为 async def
  F. 无残留同步引用: 是否还有 from sqlalchemy.orm import Session
  G. 端到端 API 流程: 注册 → 登录 → 创建KB → 列表 → 删除
  H. Docker Compose: 配置文件是否包含 PG + Redis + RabbitMQ

运行方式 (需先启动服务):
  cd backend
  ..\venv\Scripts\python.exe -m pytest tests/phases/test_phase1_database_async.py -v

或直接运行:
  ..\venv\Scripts\python.exe tests/phases/test_phase1_database_async.py
"""

import sys
import os
import inspect
import asyncio
import importlib
import time
from pathlib import Path

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PASS = 0
FAIL = 0
SKIP = 0


def pass_test(name: str, detail: str = ""):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))


def fail_test(name: str, detail: str = ""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def skip_test(name: str, detail: str = ""):
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name}" + (f" — {detail}" if detail else ""))


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ============================================================
#  A. 配置层
# ============================================================

def test_config():
    section("A. 配置层 (config.py)")

    from app.core.config import settings

    # A.1 DATABASE_URL 存在
    if hasattr(settings, "DATABASE_URL") and settings.DATABASE_URL:
        pass_test("A.1 DATABASE_URL 配置存在", settings.DATABASE_URL[:50])
    else:
        fail_test("A.1 DATABASE_URL 配置存在")

    # A.2 DATABASE_SYNC_URL 存在
    if hasattr(settings, "DATABASE_SYNC_URL") and settings.DATABASE_SYNC_URL:
        pass_test("A.2 DATABASE_SYNC_URL 配置存在")
    else:
        fail_test("A.2 DATABASE_SYNC_URL 配置存在")

    # A.3 连接池参数
    pool_params = ["DB_POOL_SIZE", "DB_MAX_OVERFLOW", "DB_POOL_TIMEOUT", "DB_POOL_RECYCLE"]
    all_pool = all(hasattr(settings, p) for p in pool_params)
    if all_pool:
        pass_test("A.3 连接池参数全部存在", f"pool={settings.DB_POOL_SIZE}, overflow={settings.DB_MAX_OVERFLOW}")
    else:
        fail_test("A.3 连接池参数全部存在")

    # A.4 Redis URL
    if hasattr(settings, "REDIS_URL") and settings.REDIS_URL:
        pass_test("A.4 REDIS_URL 配置存在", settings.REDIS_URL)
    else:
        fail_test("A.4 REDIS_URL 配置存在")

    # A.5 is_sqlite 属性
    if hasattr(settings, "is_sqlite"):
        pass_test("A.5 is_sqlite 属性存在", f"is_sqlite={settings.is_sqlite}")
    else:
        fail_test("A.5 is_sqlite 属性不存在")

    # A.6 database_url_async 属性
    if hasattr(settings, "database_url_async"):
        pass_test("A.6 database_url_async 属性存在")
    else:
        fail_test("A.6 database_url_async 属性不存在")

    # A.7 database_url_sync 属性
    if hasattr(settings, "database_url_sync"):
        pass_test("A.7 database_url_sync 属性存在")
    else:
        fail_test("A.7 database_url_sync 属性不存在")


# ============================================================
#  B. 异步引擎
# ============================================================

def test_async_engine():
    section("B. 异步引擎 (database.py)")

    from app.models.database import (
        async_engine, AsyncSessionLocal, get_db_dep,
        sync_engine, SessionLocal, get_db_sync,
        Base, init_db, init_db_sync,
    )
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    # B.1 async_engine 类型
    if isinstance(async_engine, AsyncEngine):
        pass_test("B.1 async_engine 是 AsyncEngine 实例")
    else:
        fail_test("B.1 async_engine 类型错误", str(type(async_engine)))

    # B.2 AsyncSessionLocal 可创建 AsyncSession
    async def _check_session():
        async with AsyncSessionLocal() as session:
            return isinstance(session, AsyncSession)

    result = asyncio.run(_check_session())
    if result:
        pass_test("B.2 AsyncSessionLocal 创建 AsyncSession")
    else:
        fail_test("B.2 AsyncSessionLocal 创建失败")

    # B.3 get_db_dep 是 async generator function
    if inspect.isasyncgenfunction(get_db_dep):
        pass_test("B.3 get_db_dep 是 async generator function")
    else:
        fail_test("B.3 get_db_dep 不是 async generator function", str(type(get_db_dep)))

    # B.4 sync_engine 存在 (Alembic 用)
    from sqlalchemy.engine import Engine
    if isinstance(sync_engine, Engine):
        pass_test("B.4 sync_engine 存在 (Alembic 用)")
    else:
        fail_test("B.4 sync_engine 不存在")

    # B.5 init_db 是 async function
    if inspect.iscoroutinefunction(init_db):
        pass_test("B.5 init_db 是 async function")
    else:
        fail_test("B.5 init_db 不是 async function")

    # B.6 Base.metadata 包含所有表 (需先导入 entities)
    import app.models.entities  # noqa: F401
    tables = Base.metadata.tables
    expected_tables = {"users", "knowledge_bases", "documents", "chunks", "conversations", "messages"}
    found_tables = set(tables.keys())
    missing = expected_tables - found_tables
    if not missing:
        pass_test("B.6 Base.metadata 包含所有 6 张表", f"tables={sorted(found_tables)}")
    else:
        fail_test("B.6 缺少表", str(missing))


# ============================================================
#  C. Alembic
# ============================================================

def test_alembic():
    section("C. Alembic 迁移系统")

    alembic_dir = PROJECT_ROOT / "alembic"
    alembic_ini = PROJECT_ROOT / "alembic.ini"
    versions_dir = alembic_dir / "versions"

    # C.1 alembic.ini 存在
    if alembic_ini.exists():
        pass_test("C.1 alembic.ini 存在")
    else:
        fail_test("C.1 alembic.ini 不存在")

    # C.2 alembic/ 目录存在
    if alembic_dir.exists():
        pass_test("C.2 alembic/ 目录存在")
    else:
        fail_test("C.2 alembic/ 目录不存在")

    # C.3 alembic/env.py 存在且导入 Base
    env_path = alembic_dir / "env.py"
    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        if "from app.models.database import Base" in content:
            pass_test("C.3 env.py 正确导入 Base")
        else:
            fail_test("C.3 env.py 未导入 Base")
    else:
        fail_test("C.3 env.py 不存在")

    # C.4 versions/ 目录存在
    if versions_dir.exists():
        pass_test("C.4 versions/ 目录存在")
    else:
        fail_test("C.4 versions/ 目录不存在")

    # C.5 至少一个迁移文件
    migration_files = list(versions_dir.glob("*.py"))
    if migration_files:
        pass_test("C.5 初始迁移文件存在", migration_files[0].name)
    else:
        fail_test("C.5 无迁移文件")

    # C.6 alembic_version 表存在 (stamp head 后)
    from app.models.database import sync_engine
    from sqlalchemy import text
    try:
        with sync_engine.connect() as conn:
            result = conn.execute(text("SELECT version_num FROM alembic_version"))
            version = result.scalar()
            if version:
                pass_test("C.6 alembic_version 表已标记", f"version={version}")
            else:
                fail_test("C.6 alembic_version 表为空")
    except Exception as e:
        fail_test("C.6 alembic_version 查询失败", str(e)[:100])


# ============================================================
#  D. API 路由 async 检查
# ============================================================

def test_api_async():
    section("D. API 路由 async 检查")

    route_modules = [
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

    for mod_path in route_modules:
        try:
            mod = importlib.import_module(mod_path)
            router = getattr(mod, "router", None)
            if router is None:
                fail_test(f"D.{mod_path}", "无 router 对象")
                continue

            # 检查所有路由函数是否为 async
            all_async = True
            sync_routes = []
            for route in router.routes:
                if hasattr(route, "endpoint"):
                    func = route.endpoint
                    if not inspect.iscoroutinefunction(func):
                        all_async = False
                        sync_routes.append(route.path)

            if all_async:
                pass_test(f"D. {mod_path}", "所有路由均为 async")
            else:
                fail_test(f"D. {mod_path}", f"同步路由: {sync_routes}")
        except Exception as e:
            fail_test(f"D. {mod_path}", str(e)[:80])


# ============================================================
#  E. Service 层 async 检查
# ============================================================

def test_service_async():
    section("E. Service 层 async 检查")

    service_checks = [
        ("app.services.kb_service", "KnowledgeBaseService", ["create", "get_by_id", "list", "update", "delete"]),
        ("app.services.document_service", "DocumentService", ["process_upload", "list_documents", "get_document", "delete_document", "search_in_kb"]),
        ("app.services.conversation_service", "ConversationService", ["create_conversation", "list_conversations", "get_messages", "delete_conversation"]),
        ("app.services.retrieval_service", "RetrievalService", ["search", "get_kb_stats", "rebuild_index"]),
        ("app.services.agent_service", "AgentService", ["run"]),
    ]

    for mod_path, class_name, methods in service_checks:
        try:
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, class_name, None)
            if cls is None:
                fail_test(f"E. {class_name}", "类不存在")
                continue

            all_async = True
            sync_methods = []
            for method_name in methods:
                method = getattr(cls, method_name, None)
                if method is None:
                    continue
                if not inspect.iscoroutinefunction(method):
                    all_async = False
                    sync_methods.append(method_name)

            if all_async:
                pass_test(f"E. {class_name}", "所有关键方法均为 async")
            else:
                fail_test(f"E. {class_name}", f"同步方法: {sync_methods}")
        except Exception as e:
            fail_test(f"E. {class_name}", str(e)[:80])


# ============================================================
#  F. 无残留同步引用
# ============================================================

def test_no_sync_residuals():
    section("F. 无残留同步引用")

    import subprocess
    app_dir = PROJECT_ROOT / "app"

    # F.1 搜索 from sqlalchemy.orm import Session (排除 database.py, 它合法使用 Session for Alembic)
    import re
    sync_count = 0
    sync_files = []
    for py_file in app_dir.rglob("*.py"):
        if py_file.name == "database.py":
            continue  # database.py 合法使用 Session (Alembic sync engine)
        try:
            content = py_file.read_text(encoding="utf-8")
            # 排除注释行
            for line_num, line in enumerate(content.split("\n"), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "from sqlalchemy.orm import" in line and "Session" in line:
                    # 排除 AsyncSession
                    if "AsyncSession" in line and "Session" in line.replace("AsyncSession", ""):
                        # 有可能同时 import Session 和 AsyncSession, 检查是否只有 AsyncSession
                        # 简单处理: 如果行包含 "Session" 但不是 "AsyncSession"
                        parts = line.split("import")
                        if len(parts) == 2:
                            imports = parts[1].strip()
                            names = [n.strip() for n in imports.split(",")]
                            if any(n == "Session" for n in names):
                                sync_count += 1
                                sync_files.append(f"{py_file.name}:{line_num}")
                    elif "AsyncSession" not in line:
                        sync_count += 1
                        sync_files.append(f"{py_file.name}:{line_num}")
        except Exception:
            pass

    if sync_count == 0:
        pass_test("F.1 无 'from sqlalchemy.orm import Session' 残留")
    else:
        fail_test("F.1 发现同步 Session 引用", str(sync_files))

    # F.2 搜索 db.query( (旧式同步查询)
    query_count = 0
    query_files = []
    for py_file in app_dir.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8")
            for line_num, line in enumerate(content.split("\n"), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "db.query(" in line or "self.db.query(" in line:
                    query_count += 1
                    query_files.append(f"{py_file.name}:{line_num}")
        except Exception:
            pass

    if query_count == 0:
        pass_test("F.2 无 db.query() 同步查询残留")
    else:
        fail_test("F.2 发现 db.query() 残留", str(query_files))

    # F.3 dependencies.py 中 get_current_user 是 async
    from app.api.dependencies import get_current_user, get_current_user_optional, get_current_admin
    if inspect.iscoroutinefunction(get_current_user):
        pass_test("F.3 get_current_user 是 async")
    else:
        fail_test("F.3 get_current_user 不是 async")

    if inspect.iscoroutinefunction(get_current_user_optional):
        pass_test("F.4 get_current_user_optional 是 async")
    else:
        fail_test("F.4 get_current_user_optional 不是 async")

    if inspect.iscoroutinefunction(get_current_admin):
        pass_test("F.5 get_current_admin 是 async")
    else:
        fail_test("F.5 get_current_admin 不是 async")


# ============================================================
#  G. 端到端 API 流程
# ============================================================

def test_e2e_api():
    section("G. 端到端 API 流程")

    import requests
    BASE = "http://127.0.0.1:8000"
    API = f"{BASE}/api/v1"

    try:
        # G.1 健康检查
        resp = requests.get(f"{BASE}/health", timeout=5)
        if resp.status_code == 200 and resp.json().get("data", {}).get("database") == "ok":
            pass_test("G.1 健康检查通过")
        else:
            fail_test("G.1 健康检查失败", f"status={resp.status_code}")

    except requests.ConnectionError:
        skip_test("G.* 端到端测试", "服务未启动, 跳过")
        return

    ts = str(int(time.time()))
    headers = {"Content-Type": "application/json"}

    # G.2 注册
    try:
        resp = requests.post(f"{API}/auth/register", json={
            "username": f"phase1test_{ts}",
            "password": "Test123456",
            "confirm_password": "Test123456",
            "email": f"phase1_{ts}@test.com",
        }, timeout=5)
        if resp.status_code == 200 and resp.json().get("success"):
            pass_test("G.2 用户注册成功")
        else:
            fail_test("G.2 用户注册失败", f"status={resp.status_code}, body={resp.text[:100]}")
    except Exception as e:
        fail_test("G.2 用户注册异常", str(e)[:80])

    # G.3 登录
    token = None
    try:
        resp = requests.post(f"{API}/auth/login", json={
            "username": f"phase1test_{ts}",
            "password": "Test123456",
        }, timeout=5)
        if resp.status_code == 200:
            token = resp.json()["data"]["access_token"]
            pass_test("G.3 用户登录成功")
        else:
            fail_test("G.3 用户登录失败", f"status={resp.status_code}")
    except Exception as e:
        fail_test("G.3 用户登录异常", str(e)[:80])

    if not token:
        skip_test("G.4-G.8", "无 Token, 跳过后续测试")
        return

    auth_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

    # G.4 获取当前用户
    try:
        resp = requests.get(f"{API}/auth/me", headers=auth_headers, timeout=5)
        if resp.status_code == 200 and resp.json()["data"]["username"] == f"phase1test_{ts}":
            pass_test("G.4 获取当前用户成功")
        else:
            fail_test("G.4 获取当前用户失败", f"status={resp.status_code}")
    except Exception as e:
        fail_test("G.4 获取当前用户异常", str(e)[:80])

    # G.5 创建知识库
    kb_id = None
    try:
        resp = requests.post(f"{API}/knowledge-bases", json={
            "name": f"Phase1 Test KB {ts}",
            "description": "Phase 1 验证测试",
        }, headers=auth_headers, timeout=5)
        if resp.status_code == 200:
            kb_id = resp.json()["data"]["id"]
            pass_test("G.5 创建知识库成功", f"kb_id={kb_id}")
        else:
            fail_test("G.5 创建知识库失败", f"status={resp.status_code}, body={resp.text[:100]}")
    except Exception as e:
        fail_test("G.5 创建知识库异常", str(e)[:80])

    # G.6 知识库列表
    try:
        resp = requests.get(f"{API}/knowledge-bases?page=1&page_size=10", headers=auth_headers, timeout=5)
        if resp.status_code == 200:
            total = resp.json()["data"]["total"]
            pass_test("G.6 知识库列表成功", f"total={total}")
        else:
            fail_test("G.6 知识库列表失败", f"status={resp.status_code}")
    except Exception as e:
        fail_test("G.6 知识库列表异常", str(e)[:80])

    # G.7 知识库详情
    if kb_id:
        try:
            resp = requests.get(f"{API}/knowledge-bases/{kb_id}", headers=auth_headers, timeout=5)
            if resp.status_code == 200:
                pass_test("G.7 知识库详情成功")
            else:
                fail_test("G.7 知识库详情失败", f"status={resp.status_code}")
        except Exception as e:
            fail_test("G.7 知识库详情异常", str(e)[:80])

    # G.8 删除知识库
    if kb_id:
        try:
            resp = requests.delete(f"{API}/knowledge-bases/{kb_id}", headers=auth_headers, timeout=5)
            if resp.status_code == 200:
                pass_test("G.8 删除知识库成功")
            else:
                fail_test("G.8 删除知识库失败", f"status={resp.status_code}")
        except Exception as e:
            fail_test("G.8 删除知识库异常", str(e)[:80])

    # G.9 无效 Token 被拒绝
    try:
        resp = requests.get(f"{API}/auth/me", headers={"Authorization": "Bearer invalid_token_xxx"}, timeout=5)
        if resp.status_code == 401:
            pass_test("G.9 无效 Token 被正确拒绝 (401)")
        else:
            fail_test("G.9 无效 Token 未被拒绝", f"status={resp.status_code}")
    except Exception as e:
        fail_test("G.9 无效 Token 测试异常", str(e)[:80])


# ============================================================
#  H. Docker Compose 配置
# ============================================================

def test_docker_compose():
    section("H. Docker Compose 配置")

    compose_path = PROJECT_ROOT.parent / "docker-compose.yml"
    dockerfile_path = PROJECT_ROOT.parent / "docker" / "Dockerfile"

    # H.1 docker-compose.yml 存在
    if compose_path.exists():
        pass_test("H.1 docker-compose.yml 存在")
    else:
        fail_test("H.1 docker-compose.yml 不存在", str(compose_path))
        return

    content = compose_path.read_text(encoding="utf-8")

    # H.2 PostgreSQL 服务
    if "postgres" in content and "pgvector" in content:
        pass_test("H.2 PostgreSQL + pgvector 服务配置存在")
    else:
        fail_test("H.2 PostgreSQL 服务配置缺失")

    # H.3 Redis 服务
    if "redis" in content:
        pass_test("H.3 Redis 服务配置存在")
    else:
        fail_test("H.3 Redis 服务配置缺失")

    # H.4 RabbitMQ 服务
    if "rabbitmq" in content:
        pass_test("H.4 RabbitMQ 服务配置存在")
    else:
        fail_test("H.4 RabbitMQ 服务配置缺失")

    # H.5 健康检查
    if "healthcheck" in content:
        pass_test("H.5 健康检查配置存在")
    else:
        fail_test("H.5 健康检查配置缺失")

    # H.6 Dockerfile 存在
    if dockerfile_path.exists():
        pass_test("H.6 Dockerfile 存在")
    else:
        fail_test("H.6 Dockerfile 不存在")


# ============================================================
#  主入口
# ============================================================

def main():
    print("\n" + "=" * 60)
    print("  Phase 1 验证测试 — 数据库迁移 + 全链路异步化")
    print("=" * 60)

    test_config()
    test_async_engine()
    test_alembic()
    test_api_async()
    test_service_async()
    test_no_sync_residuals()
    test_e2e_api()
    test_docker_compose()

    print(f"\n{'='*60}")
    print(f"  总计: {PASS + FAIL + SKIP}")
    print(f"  通过: {PASS}")
    print(f"  失败: {FAIL}")
    print(f"  跳过: {SKIP}")
    print(f"  通过率: {PASS/(PASS+FAIL)*100:.1f}%" if (PASS + FAIL) > 0 else "  无可测试项")
    print(f"{'='*60}\n")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
