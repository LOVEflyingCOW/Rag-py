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

    logger.info("Database initialized successfully")


def init_db_sync() -> None:
    """同步初始化数据库"""
    logger.info("Initializing database (sync): %s", _sync_url)

    import app.models.entities  # noqa: F401

    Base.metadata.create_all(bind=sync_engine)

    logger.info("Database initialized successfully")
