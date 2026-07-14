from __future__ import annotations

from typing import Any, AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings
from app.core.logging import logger

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.APP_DEBUG,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
    pool_pre_ping=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Generator[Session, Any, None]:
    """获取数据库会话（同步版本）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """初始化数据库 - 创建所有表"""
    logger.info("Initializing database: %s", settings.DATABASE_URL)

    from app.models.entities import user, knowledge_base, document, conversation

    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully")