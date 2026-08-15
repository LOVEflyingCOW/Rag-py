from __future__ import annotations

import os
from typing import List, Optional
from pathlib import Path

try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseSettings


class Settings(BaseSettings):
    """全局配置管理"""

    APP_NAME: str = "RAG Knowledge Base System"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # ===== 数据库 (PostgreSQL) =====
    # 异步驱动 URL (asyncpg) — 用于 API 层
    DATABASE_URL: str = "postgresql+asyncpg://rag_user:rag_dev_password@localhost:5432/rag_system"
    # 同步驱动 URL (psycopg2) — 用于 Alembic 迁移
    DATABASE_SYNC_URL: str = "postgresql+psycopg2://rag_user:rag_dev_password@localhost:5432/rag_system"

    # 连接池
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 3600

    # ===== Redis =====
    REDIS_URL: str = "redis://localhost:6379/0"

    # ===== Celery (Phase 3) =====
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    CELERY_TASK_TIME_LIMIT: int = 600          # 单任务最大执行时间 (秒)
    CELERY_TASK_SOFT_TIME_LIMIT: int = 540    # 软超时 (提前 60s 通知)
    CELERY_WORKER_PREFETCH_MULTIPLIER: int = 1  # 每次只取 1 个任务 (公平调度)
    CELERY_WORKER_MAX_TASKS_PER_CHILD: int = 100  # 子进程执行 100 个任务后回收 (防内存泄漏)

    # ===== 鉴权 =====
    SECRET_KEY: str = "change-this-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_EXPIRE_DAYS: int = 7
    JWT_EXPIRE_MINUTES: int = 1440  # 兼容旧配置

    # ===== 大语言模型 (LLM) 配置 =====
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    OPENAI_API_KEY: str = ""
    OPENAI_API_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-3.5-turbo"

    LLM_PROVIDER: str = "mock"
    LLM_CUSTOM_API_URL: str = ""
    LLM_CUSTOM_API_KEY: str = ""
    LLM_CUSTOM_MODEL: str = "custom-model"

    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 1024
    LLM_TOP_P: float = 0.9
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRIES: int = 3
    LLM_RETRY_BACKOFF: float = 1.5

    # ===== Embedding 配置 =====
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_API_URL: str = ""
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_BATCH_SIZE: int = 16
    EMBEDDING_TIMEOUT: int = 60
    EMBEDDING_CACHE_SIZE: int = 1000
    EMBEDDING_MAX_RETRIES: int = 3
    EMBEDDING_DEFAULT_DIM: int = 384

    # ===== RAG 检索配置 =====
    RAG_TOP_K: int = 5
    RAG_MIN_SCORE: float = 0.35
    RAG_MAX_CONTEXT_CHARS: int = 3000
    RAG_REQUIRE_SOURCE: bool = True

    # ===== 文件存储 =====
    UPLOAD_DIR: str = "./data/uploads"
    VECTOR_STORE_DIR: str = "./data/vector_stores"

    # ===== CORS =====
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # ===== OAuth2 三方登录 (Phase 5) =====
    OAUTH_GITHUB_CLIENT_ID: str = ""
    OAUTH_GITHUB_CLIENT_SECRET: str = ""
    OAUTH_GOOGLE_CLIENT_ID: str = ""
    OAUTH_GOOGLE_CLIENT_SECRET: str = ""
    OAUTH_REDIRECT_BASE: str = "http://localhost:8000/api/v1/oauth"

    class Config:
        env_file = ".env"
        case_sensitive = True

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return "sqlite" in self.DATABASE_URL

    @property
    def database_url_async(self) -> str:
        """返回异步驱动 URL"""
        url = self.DATABASE_URL
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def database_url_sync(self) -> str:
        """返回同步驱动 URL (Alembic 用)"""
        url = self.DATABASE_SYNC_URL or self.DATABASE_URL
        if url.startswith("postgresql+asyncpg://"):
            return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
        return url

    @property
    def embedding_provider_name(self) -> str:
        if self.EMBEDDING_API_URL:
            return "remote"
        return "mock"

    @property
    def active_llm_name(self) -> str:
        """当前激活的 LLM provider 名称"""
        provider = (self.LLM_PROVIDER or "mock").strip().lower()
        mapping = {
            "mock": "MockLLM (无 API Key 时的开发回退)",
            "deepseek": "DeepSeek (%s)" % (self.DEEPSEEK_MODEL or "deepseek-chat"),
            "openai": "OpenAI (%s)" % (self.OPENAI_MODEL or "gpt-3.5-turbo"),
            "custom": "Custom (%s)" % (self.LLM_CUSTOM_MODEL or "custom-model"),
        }
        return mapping.get(provider, mapping["mock"])

    def ensure_dirs(self) -> None:
        dirs = [self.UPLOAD_DIR, self.VECTOR_STORE_DIR]
        for d in dirs:
            Path(d).mkdir(parents=True, exist_ok=True)


settings = Settings()
