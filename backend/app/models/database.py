"""数据库连接管理

生产级配置 (Phase 3 优化):
1. 异步引擎 (asyncpg): pool_size + max_overflow, pool_pre_ping 断线重连
2. 同步引擎 (psycopg2): Celery Worker / Alembic 使用
3. SQLite 模式: 开发/测试环境, 无连接池
4. 引擎事件监听: 连接池指标暴露
5. 优雅关闭: dispose() 在 shutdown 时释放所有连接
"""

from __future__ import annotations

import time
from typing import Any, AsyncGenerator, Generator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

from app.core.config import settings
from app.core.logging import logger


# ============================================================
#  辅助函数
# ============================================================

def _build_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite:///") and "aiosqlite" not in url:
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url


def _build_sync_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if url.startswith("sqlite+aiosqlite:///"):
        return url.replace("sqlite+aiosqlite:///", "sqlite:///", 1)
    return url


# ============================================================
#  异步引擎 (API 层使用)
# ============================================================

_async_url = _build_async_url(settings.DATABASE_URL)
_is_sqlite = "sqlite" in _async_url

if _is_sqlite:
    async_engine: AsyncEngine = create_async_engine(
        _async_url,
        echo=settings.APP_DEBUG,
        connect_args={"check_same_thread": False},
    )
else:
    async_engine: AsyncEngine = create_async_engine(
        _async_url,
        echo=settings.APP_DEBUG,
        # 基础连接数 (常驻)
        pool_size=settings.DB_POOL_SIZE,
        # 突发连接数 (可临时超出)
        max_overflow=settings.DB_MAX_OVERFLOW,
        # 从连接池获取连接的超时时间 (秒)
        pool_timeout=settings.DB_POOL_TIMEOUT,
        # 连接回收时间 (秒), 防止长时间空闲连接被 PG 断开
        pool_recycle=settings.DB_POOL_RECYCLE,
        # 连接前执行 SELECT 1 检测存活
        pool_pre_ping=True,
        # asyncpg 专属参数
        connect_args={
            "server_settings": {
                # 为每条连接设置应用名 (便于 PG 监控)
                "application_name": "rag_api",
            },
            # 连接超时
            "timeout": 10.0,
            # TCP keepalive
            "tcp_keepalives_idle": 60,
            "tcp_keepalives_interval": 10,
            "tcp_keepalives_count": 3,
        },
    )

# 异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ============================================================
#  同步引擎 (Celery Worker / Alembic 使用)
# ============================================================

_sync_url = _build_sync_url(settings.DATABASE_SYNC_URL or settings.DATABASE_URL)

if "sqlite" in _sync_url:
    sync_engine = create_engine(
        _sync_url,
        echo=settings.APP_DEBUG,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
else:
    sync_engine = create_engine(
        _sync_url,
        echo=settings.APP_DEBUG,
        # Celery Worker 同步引擎: 每个 Worker 进程独立, 小连接池即可
        pool_size=max(5, settings.DB_POOL_SIZE // 4),
        max_overflow=5,
        pool_timeout=15,
        pool_recycle=3600,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 10,
        },
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=sync_engine)


# ============================================================
#  Base + 事件监听
# ============================================================

Base = declarative_base()

# 连接池指标
_pool_metrics = {
    "async_checkout": 0,
    "async_checkin": 0,
}

# 仅 PostgreSQL (非 SQLite) 模式下注册事件监听
if not _is_sqlite:
    _sync_engine_for_events = async_engine.sync_engine

    @event.listens_for(_sync_engine_for_events, "checkout")
    def _on_async_checkout(dbapi_connection, connection_record, connection_proxy):
        _pool_metrics["async_checkout"] += 1

    @event.listens_for(_sync_engine_for_events, "checkin")
    def _on_async_checkin(dbapi_connection, connection_record):
        _pool_metrics["async_checkin"] += 1


async def get_pool_metrics() -> dict:
    """获取异步连接池指标 (供 /health 扩展)"""
    try:
        from sqlalchemy.pool import QueuePool
        pool = async_engine.pool
        if isinstance(pool, QueuePool):
            return {
                "size": pool.size(),
                "checked_in": pool.checkedin(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
                "status": "ok",
            }
    except Exception:
        pass
    return {"status": "unknown"}


# ============================================================
#  依赖注入
# ============================================================

async def get_db_dep() -> AsyncGenerator[AsyncSession, Any]:
    """异步数据库会话依赖注入"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def get_db_sync() -> Generator[Session, Any, None]:
    """同步数据库会话 (Celery / 脚本用)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ============================================================
#  引擎生命周期
# ============================================================

async def dispose_async_engine() -> None:
    """释放异步引擎的所有连接"""
    try:
        await async_engine.dispose()
        logger.info("Async engine disposed (all connections released)")
    except Exception as e:
        logger.warning("Error disposing async engine: %s", str(e))


def dispose_sync_engine() -> None:
    """释放同步引擎的所有连接"""
    try:
        sync_engine.dispose()
        logger.info("Sync engine disposed (all connections released)")
    except Exception as e:
        logger.warning("Error disposing sync engine: %s", str(e))


# ============================================================
#  初始化
# ============================================================

async def init_db() -> None:
    """异步初始化数据库"""
    logger.info("Initializing database (async): %s", _async_url)

    import app.models.entities  # noqa: F401

    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    if not _is_sqlite:
        async with async_engine.begin() as conn:
            await conn.execute(__import__("sqlalchemy").text("CREATE EXTENSION IF NOT EXISTS vector"))

    # 种子数据 — 默认角色 + 权限（兼容 PG/SQLite，已存在则跳过）
    await _seed_rbac()

    logger.info("Database initialized successfully")


def init_db_sync() -> None:
    """同步初始化数据库"""
    logger.info("Initializing database (sync): %s", _sync_url)

    import app.models.entities  # noqa: F401

    Base.metadata.create_all(bind=sync_engine)

    _seed_rbac_sync()

    logger.info("Database initialized successfully")


# ============================================================
#  RBAC 种子数据（与 alembic/versions/f1a2c3d4e5f6_*.py 保持一致）
# ============================================================

_DEFAULT_ROLES = ["admin", "editor", "viewer"]
_DEFAULT_PERMISSIONS = [
    ("kb", "create", "创建知识库"),
    ("kb", "read", "查看知识库"),
    ("kb", "update", "更新知识库"),
    ("kb", "delete", "删除知识库"),
    ("document", "create", "上传文档"),
    ("document", "read", "查看文档"),
    ("document", "update", "更新文档"),
    ("document", "delete", "删除文档"),
    ("conversation", "create", "创建会话"),
    ("conversation", "read", "查看会话"),
    ("conversation", "delete", "删除会话"),
    ("chat", "use", "使用聊天问答"),
    ("retrieval", "use", "使用检索接口"),
    ("user", "manage", "管理用户"),
    ("system", "admin", "系统管理员权限"),
]
_ROLE_PERMISSIONS = {
    # admin: 所有 15 个权限
    "admin": [
        ("kb", "create"), ("kb", "read"), ("kb", "update"), ("kb", "delete"),
        ("document", "create"), ("document", "read"), ("document", "update"), ("document", "delete"),
        ("conversation", "create"), ("conversation", "read"), ("conversation", "delete"),
        ("chat", "use"), ("retrieval", "use"),
        ("user", "manage"), ("system", "admin"),
    ],
    # editor: 除 user.manage / system.admin 外的业务权限
    # 设计意图：RBAC 控制"是否允许访问该端点"，Service 层所有权再限制操作对象
    "editor": [
        ("kb", "create"), ("kb", "read"), ("kb", "update"), ("kb", "delete"),
        ("document", "create"), ("document", "read"), ("document", "update"), ("document", "delete"),
        ("conversation", "create"), ("conversation", "read"), ("conversation", "delete"),
        ("chat", "use"), ("retrieval", "use"),
    ],
    # viewer: 只读 + 问答/检索
    "viewer": [
        ("kb", "read"),
        ("document", "read"),
        ("conversation", "read"),
        ("chat", "use"), ("retrieval", "use"),
    ],
}
_ROLE_DESCS = {
    "admin": "系统管理员 — 拥有全部权限",
    "editor": "编辑者 — 可创建/修改知识库和文档",
    "viewer": "观察者 — 只读权限，可提问",
}


async def _seed_rbac() -> None:
    """异步方式写入默认角色/权限（幂等）"""
    from sqlalchemy import text
    from app.models.entities import Role, Permission

    async with AsyncSessionLocal() as session:
        # 1. 角色
        for rn in _DEFAULT_ROLES:
            exists = (await session.execute(
                text("SELECT 1 FROM roles WHERE name = :n"), {"n": rn}
            )).fetchone()
            if not exists:
                await session.execute(
                    text("INSERT INTO roles (name, description) VALUES (:n, :d)"),
                    {"n": rn, "d": _ROLE_DESCS.get(rn, "")}
                )
        # 2. 权限
        for (res, act, desc) in _DEFAULT_PERMISSIONS:
            exists = (await session.execute(
                text("SELECT 1 FROM permissions WHERE resource = :r AND action = :a"),
                {"r": res, "a": act}
            )).fetchone()
            if not exists:
                await session.execute(
                    text("INSERT INTO permissions (resource, action, description) VALUES (:r, :a, :d)"),
                    {"r": res, "a": act, "d": desc}
                )
        await session.commit()

        # 3. role_permissions 关联
        for rn, perms in _ROLE_PERMISSIONS.items():
            role_row = (await session.execute(
                text("SELECT id FROM roles WHERE name = :n"), {"n": rn}
            )).fetchone()
            if not role_row:
                continue
            rid = role_row[0]
            for (res, act) in perms:
                perm_row = (await session.execute(
                    text("SELECT id FROM permissions WHERE resource = :r AND action = :a"),
                    {"r": res, "a": act}
                )).fetchone()
                if not perm_row:
                    continue
                pid = perm_row[0]
                exists = (await session.execute(
                    text("SELECT 1 FROM role_permissions WHERE role_id = :rid AND permission_id = :pid"),
                    {"rid": rid, "pid": pid}
                )).fetchone()
                if not exists:
                    await session.execute(
                        text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)"),
                        {"rid": rid, "pid": pid}
                    )
        await session.commit()


def _seed_rbac_sync() -> None:
    """同步方式写入默认角色/权限（Celery Worker 等场景）"""
    from sqlalchemy import text

    with SessionLocal() as session:
        for rn in _DEFAULT_ROLES:
            if not session.execute(text("SELECT 1 FROM roles WHERE name = :n"), {"n": rn}).fetchone():
                session.execute(
                    text("INSERT INTO roles (name, description) VALUES (:n, :d)"),
                    {"n": rn, "d": _ROLE_DESCS.get(rn, "")}
                )
        for (res, act, desc) in _DEFAULT_PERMISSIONS:
            if not session.execute(
                text("SELECT 1 FROM permissions WHERE resource = :r AND action = :a"),
                {"r": res, "a": act}
            ).fetchone():
                session.execute(
                    text("INSERT INTO permissions (resource, action, description) VALUES (:r, :a, :d)"),
                    {"r": res, "a": act, "d": desc}
                )
        session.commit()
        for rn, perms in _ROLE_PERMISSIONS.items():
            role_row = session.execute(text("SELECT id FROM roles WHERE name = :n"), {"n": rn}).fetchone()
            if not role_row:
                continue
            rid = role_row[0]
            for (res, act) in perms:
                perm_row = session.execute(
                    text("SELECT id FROM permissions WHERE resource = :r AND action = :a"),
                    {"r": res, "a": act}
                ).fetchone()
                if not perm_row:
                    continue
                pid = perm_row[0]
                if not session.execute(
                    text("SELECT 1 FROM role_permissions WHERE role_id = :rid AND permission_id = :pid"),
                    {"rid": rid, "pid": pid}
                ).fetchone():
                    session.execute(
                        text("INSERT INTO role_permissions (role_id, permission_id) VALUES (:rid, :pid)"),
                        {"rid": rid, "pid": pid}
                    )
        session.commit()
