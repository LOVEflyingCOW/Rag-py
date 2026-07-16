from __future__ import annotations

import os
from typing import List, Optional
from pathlib import Path

from pydantic import BaseSettings


class Settings(BaseSettings):
    """全局配置管理"""

    APP_NAME: str = "RAG Knowledge Base System"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    DATABASE_URL: str = "sqlite:///./data/rag_system.db"

    SECRET_KEY: str = "change-this-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_URL: str = "https://api.deepseek.com/v1"
    OPENAI_API_KEY: str = ""
    OPENAI_API_URL: str = "https://api.openai.com/v1"

    EMBEDDING_API_KEY: str = ""
    EMBEDDING_API_URL: str = ""
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_BATCH_SIZE: int = 16
    EMBEDDING_TIMEOUT: int = 60
    EMBEDDING_CACHE_SIZE: int = 1000
    EMBEDDING_MAX_RETRIES: int = 3
    EMBEDDING_DEFAULT_DIM: int = 384

    UPLOAD_DIR: str = "./data/uploads"
    VECTOR_STORE_DIR: str = "./data/vector_stores"

    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    class Config:
        env_file = ".env"
        case_sensitive = True

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def embedding_provider_name(self) -> str:
        """根据配置自动推断 Provider 名称"""
        if self.EMBEDDING_API_URL:
            return "remote"
        return "mock"

    def ensure_dirs(self) -> None:
        """确保所有必要目录存在"""
        dirs = [self.UPLOAD_DIR, self.VECTOR_STORE_DIR]
        for d in dirs:
            Path(d).mkdir(parents=True, exist_ok=True)


settings = Settings()