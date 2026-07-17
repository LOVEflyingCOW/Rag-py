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

    # ===== 大语言模型 (LLM) 配置 =====
    # DeepSeek (DeepSeek Chat / Reasoner)
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # OpenAI 兼容 (GPT-3.5/4)
    OPENAI_API_KEY: str = ""
    OPENAI_API_URL: str = "https://api.openai.com/v1"
    OPENAI_MODEL: str = "gpt-3.5-turbo"

    # 当前使用的 provider: "mock" | "deepseek" | "openai" | "custom"
    LLM_PROVIDER: str = "mock"
    # 如果 LLM_PROVIDER=custom, 使用以下 API
    LLM_CUSTOM_API_URL: str = ""
    LLM_CUSTOM_API_KEY: str = ""
    LLM_CUSTOM_MODEL: str = "custom-model"

    # 生成参数
    LLM_TEMPERATURE: float = 0.3
    LLM_MAX_TOKENS: int = 1024
    LLM_TOP_P: float = 0.9
    LLM_TIMEOUT: int = 60
    LLM_MAX_RETRIES: int = 3
    LLM_RETRY_BACKOFF: float = 1.5

    # ===== Embedding (向量) 配置 =====
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
    RAG_REQUIRE_SOURCE: bool = True  # 要求 LLM 回答必须基于检索内容

    # 文件上传
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