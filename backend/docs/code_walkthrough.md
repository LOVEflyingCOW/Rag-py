# RAG 知识库系统 代码走读文档（code_walkthrough）

> 本文档基于项目根目录 `c:\Users\LEgion\Desktop\backend\RAG-PY\backend` 下的真实源代码逐文件、逐段解读，涵盖入口与配置、数据模型、API 路由、业务服务、处理管道五大模块，并补充模块调用链、依赖关系、可维护性与改进点分析。
>
> 文档结构：
> 1. 项目总览与目录分组
> 2. 入口与配置模块
> 3. 数据库与模型模块
> 4. API 层模块
> 5. Service 层模块
> 6. Processor 层模块
> 7. 文件分类说明（配置/脚本/入口/核心业务/工具）
> 8. 项目运行入口与初始化流程
> 9. 关键公共方法、类、函数用途详解
> 10. 重要依赖与被依赖关系分析
> 11. 模块调用链
> 12. 代码风格、可维护性与潜在改进点

---

## 1. 项目总览与目录分组

本系统是一个从零搭建的 RAG（Retrieval-Augmented Generation）知识库系统，采用 FastAPI + SQLAlchemy + FAISS/NumPy 的技术栈，分层清晰：

```
backend/
├── start.py                      # 启动脚本（修复 sys.path 后拉起 uvicorn）
├── requirements.txt              # Python 依赖清单
├── .env.example                  # 环境变量示例
├── pytest.ini                    # pytest 配置
└── app/
    ├── __init__.py               # 空包标记
    ├── main.py                   # FastAPI 应用工厂 create_app()
    ├── core/                     # 核心基础设施
    │   ├── __init__.py           # 空包标记
    │   ├── config.py             # 全局配置（pydantic BaseSettings）
    │   ├── security.py           # 密码哈希 + 手写 JWT
    │   ├── logging.py            # 统一日志（控制台 + 滚动文件）
    │   └── exceptions.py         # 自定义异常 + 全局异常处理器
    ├── models/                   # 数据库与数据结构
    │   ├── __init__.py           # 空包标记
    │   ├── database.py           # SQLAlchemy engine/session/Base
    │   ├── response.py           # 统一 ApiResponse 包装
    │   ├── schemas.py            # Pydantic 请求/响应模型
    │   └── entities/             # ORM 实体
    │       ├── __init__.py       # 显式导入所有实体（触发 relationship 解析）
    │       ├── user.py
    │       ├── knowledge_base.py
    │       ├── document.py
    │       └── conversation.py
    ├── api/                      # API 路由层
    │   ├── __init__.py           # 空包标记
    │   ├── health.py             # 健康检查 /health
    │   ├── dependencies.py       # 依赖注入（DB 会话、当前用户）
    │   └── v1/                   # v1 版本路由聚合
    │       ├── __init__.py       # 聚合 api_router
    │       ├── auth.py
    │       ├── knowledge_base.py
    │       ├── document.py
    │       ├── chat.py
    │       ├── agent.py
    │       ├── retrieval.py
    │       ├── embedding.py
    │       ├── conversation.py
    │       └── integration.py
    ├── services/                 # 业务服务层
    │   ├── __init__.py           # 空包标记
    │   ├── kb_service.py
    │   ├── document_service.py
    │   ├── chat_service.py
    │   ├── conversation_service.py
    │   ├── retrieval_service.py
    │   ├── agent_service.py
    │   └── integration_service.py
    └── processors/               # 数据处理管道（与 DB 解耦的纯逻辑）
        ├── __init__.py           # processors 包总出口，统一 re-export
        ├── document/
        │   ├── __init__.py
        │   ├── document_processor.py
        │   ├── markdown_parser.py
        │   ├── semantic_chunker.py
        │   └── document_pipeline.py
        ├── embedding/
        │   ├── __init__.py
        │   └── embedding_service.py
        ├── llm/
        │   ├── __init__.py
        │   └── llm_service.py
        └── retrieval/
            ├── __init__.py
            └── vector_store.py
```

整体分层关系（自上而下依赖）：

```
API 层 (api/) ──依赖──▶ Service 层 (services/) ──依赖──▶ Processor 层 (processors/)
   │                       │                              │
   └──依赖──▶ core/         └──依赖──▶ models/entities/     └──内部: document/embedding/llm/retrieval
```

---

## 2. 入口与配置模块

### 2.1 `start.py` —— 启动脚本

```python
import os
import sys

VENV_DIR = r"c:\Users\LEgion\Desktop\backend\RAG-PY\venv"
BACKEND_DIR = r"c:\Users\LEgion\Desktop\backend\RAG-PY\backend"

venv_site = os.path.join(VENV_DIR, "Lib", "site-packages")
if os.path.isdir(venv_site) and venv_site not in sys.path:
    sys.path.insert(0, venv_site)

os.chdir(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.main import app
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
        reload=False
    )
```

**逐段说明：**

- **第 1-2 行**：导入 `os`、`sys`，用于操作路径与解释器搜索路径。
- **第 4-5 行**：硬编码虚拟环境目录 `VENV_DIR` 与后端目录 `BACKEND_DIR`。这是 Windows 环境下为确保解释器能找到 venv 中安装的第三方库（如 fastapi、sqlalchemy）而做的"补丁"。
- **第 7-9 行**：把 venv 的 `Lib/site-packages` 插入 `sys.path` 首位，保证优先从虚拟环境加载依赖。
- **第 11-13 行**：切换工作目录到 `BACKEND_DIR`，并把 `BACKEND_DIR` 加入 `sys.path`，使 `from app.main import app` 能正确解析 `app` 包。
- **第 15-16 行**：从 `app.main` 导入 `app` 对象（FastAPI 实例），并导入 `uvicorn` 作为 ASGI 服务器。
- **第 18-25 行**：`__main__` 入口，以 `127.0.0.1:8000`、`info` 日志级别、非热重载方式启动 uvicorn。

**设计意图：** 该脚本是面向"开发者本地直接双击/命令行运行"的便捷入口，绕开 IDE 配置，显式修正 `sys.path`。生产部署推荐直接 `uvicorn app.main:app`。

**关联关系：** 依赖 `app/main.py` 暴露的 `app` 全局变量。

---

### 2.2 `app/main.py` —— FastAPI 应用工厂

```python
from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.api.v1 import api_router
from app.core.config import settings
from app.core.exceptions import (
    global_exception_handler,
    validation_exception_handler
)
from app.core.logging import logger


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    settings.ensure_dirs()

    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        description="RAG Knowledge Base System - Built from scratch",
        docs_url="/docs",
        redoc_url="/redoc",
        debug=settings.APP_DEBUG
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(Exception, global_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    app.include_router(health.router)
    app.include_router(api_router, prefix="/api")

    @app.on_event("startup")
    async def startup_event():
        from app.models.database import init_db
        logger.info("Application starting up...")
        try:
            init_db()
            logger.info("Database initialized")
        except Exception as e:
            logger.error("Failed to initialize database: %s", str(e))
        logger.info("Application started successfully")

    @app.on_event("shutdown")
    async def shutdown_event():
        logger.info("Application shutting down...")

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "name": settings.APP_NAME,
            "version": "0.1.0",
            "status": "running",
            "docs": "/docs",
            "health": "/health"
        }

    return app


app = create_app()
```

**逐段说明：**

- **导入区**：从 `app.api` 引入健康检查路由 `health`；从 `app.api.v1` 引入聚合路由 `api_router`；从 `app.core` 引入配置 `settings`、异常处理器、日志 `logger`。
- **`create_app()`**：应用工厂函数。
  - `settings.ensure_dirs()`：先确保上传目录与向量存储目录存在。
  - `FastAPI(...)`：构造应用实例，绑定标题、版本、Swagger/ReDoc 文档地址、debug 开关。
  - `add_middleware(CORSMiddleware, ...)`：注册跨域中间件，允许来源来自 `settings.cors_origin_list`（解析自 `CORS_ORIGINS` 配置项），允许携带凭证、所有方法与头。
  - `add_exception_handler`：注册两类全局异常处理器——通用 `Exception` 走 `global_exception_handler`，FastAPI 参数校验异常 `RequestValidationError` 走 `validation_exception_handler`。
  - `include_router`：挂载 `health.router`（前缀 `/health`，已在 router 内声明）和 `api_router`（统一前缀 `/api`，其内部又带 `/v1`）。
  - `@app.on_event("startup")`：启动事件中延迟导入并调用 `init_db()` 建表；失败仅记录日志不中断启动（保证应用即使 DB 异常也能起来便于排查）。
  - `@app.on_event("shutdown")`：关闭事件记录日志。
  - `@app.get("/")`：根路径返回服务名、版本、状态及文档链接。
- **`app = create_app()`**：模块级执行，产出全局 `app` 供 `start.py` / uvicorn 使用。

**关联关系：** 该文件是整个应用的装配中心，向下依赖 `core`、`api`、`models` 三大模块。最终路由前缀形如 `/api/v1/...` 与 `/health`。

---

### 2.3 `app/__init__.py` 与 `app/core/__init__.py`

二者均为空文件，仅作为 Python 包标记存在，无逻辑。`app/__init__.py` 为空使得 `app` 可被当作包导入；`app/core/__init__.py` 同理。

---

### 2.4 `app/core/config.py` —— 全局配置

```python
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
```

**逐段说明：**

- **`Settings(BaseSettings)`**：继承 pydantic v1 的 `BaseSettings`，所有字段均可由 `.env` 文件或环境变量覆盖；`case_sensitive = True` 表示大小写敏感。
- **应用基础配置**（`APP_*`）：名称、环境、调试、监听地址端口。
- **`DATABASE_URL`**：默认 SQLite，存于 `./data/rag_system.db`；`.env.example` 注释中也给出 PostgreSQL 切换示例。
- **安全配置**：`SECRET_KEY` 用于 JWT 签名，`JWT_ALGORITHM` 固定 HS256，`JWT_EXPIRE_MINUTES` 默认 24 小时。
- **LLM 配置**：支持 mock/deepseek/openai/custom 四种 provider，分别有独立的 API URL/Key/Model；`LLM_PROVIDER` 决定激活哪一个。生成参数含 temperature、max_tokens、top_p、超时、重试次数与退避系数。
- **Embedding 配置**：默认维度 384，模型 `bge-m3`，支持远程 API（当 `EMBEDDING_API_URL` 非空时启用），含批量大小、超时、缓存大小、重试次数。
- **RAG 检索配置**：`RAG_TOP_K` 召回数量、`RAG_MIN_SCORE` 最低相似度、`RAG_MAX_CONTEXT_CHARS` 喂给 LLM 的上下文字符上限、`RAG_REQUIRE_SOURCE` 是否强制基于检索内容。
- **文件存储**：上传目录与向量存储目录。
- **`CORS_ORIGINS`**：逗号分隔的允许来源字符串。
- **`cors_origin_list`**：属性，把字符串拆成列表供中间件使用。
- **`embedding_provider_name`**：根据是否配置远程 URL 判断返回 `"remote"` 或 `"mock"`。
- **`active_llm_name`**：返回人类可读的当前 LLM 描述（用于 `/chat/` 根路径展示）。
- **`ensure_dirs()`**：启动时确保上传与向量目录存在。
- **`settings = Settings()`**：模块级单例，全项目共享。

**依赖关系：** 被 `main.py`、`security.py`、`database.py`、各 service、各 processor 广泛依赖，是全局配置中枢。

---

### 2.5 `app/core/security.py` —— 密码哈希与手写 JWT

```python
from __future__ import annotations

import hashlib
import hmac
import json
import os
import base64
import time
from typing import Dict, Optional, Any

from app.core.config import settings

_HASH_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 100000
_SALT_LENGTH = 16


def generate_salt() -> str:
    """生成随机盐值 (hex)"""
    return os.urandom(_SALT_LENGTH).hex()


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """使用 PBKDF2-HMAC 哈希密码

    返回格式: pbkdf2_sha256$iterations$salt$hash_hex
    """
    if salt is None:
        salt = generate_salt()

    password_bytes = password.encode("utf-8")
    salt_bytes = salt.encode("utf-8")

    key = hashlib.pbkdf2_hmac(
        _HASH_ALGORITHM,
        password_bytes,
        salt_bytes,
        _PBKDF2_ITERATIONS,
        dklen=32
    )

    return "pbkdf2_sha256$%d$%s$%s" % (_PBKDF2_ITERATIONS, salt, key.hex())


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    try:
        parts = hashed_password.split("$")
        if len(parts) != 4:
            return False

        algorithm, iterations_str, salt, stored_hash = parts
        if algorithm != "pbkdf2_sha256":
            return False

        iterations = int(iterations_str)
        password_bytes = plain_password.encode("utf-8")
        salt_bytes = salt.encode("utf-8")

        computed = hashlib.pbkdf2_hmac(
            _HASH_ALGORITHM,
            password_bytes,
            salt_bytes,
            iterations,
            dklen=32
        )

        return computed.hex() == stored_hash
    except Exception:
        return False
```

**逐段说明（密码部分）：**

- **常量**：算法名 sha256、PBKDF2 迭代 10 万次、盐长 16 字节。
- **`generate_salt()`**：用 `os.urandom` 生成密码学安全随机盐，返回 hex 字符串。
- **`hash_password(password, salt)`**：PBKDF2-HMAC-SHA256 派生密钥，输出格式 `pbkdf2_sha256$iterations$salt$hash_hex`，把算法、迭代次数、盐、哈希都编码进字符串，便于后续无状态验证。
- **`verify_password(plain, hashed)`**：拆分存储字符串，按相同参数重新计算哈希，与存储值比较；任何异常返回 `False`（防侧信道）。

```python
def _base64url_encode(data: bytes) -> str:
    """Base64 URL 安全编码"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _base64url_decode(data: str) -> bytes:
    """Base64 URL 安全解码"""
    padding = "=" * (4 - len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_jwt_token(payload: Dict[str, Any], secret: Optional[str] = None,
                     algorithm: str = "HS256",
                     expire_minutes: Optional[int] = None) -> str:
    """创建 JWT Token

    手写实现（不依赖 PyJWT），保证 Python 3.7 兼容性
    """
    if secret is None:
        secret = settings.SECRET_KEY

    if expire_minutes is None:
        expire_minutes = settings.JWT_EXPIRE_MINUTES

    now = int(time.time())
    header = {"typ": "JWT", "alg": algorithm}

    payload = dict(payload)
    payload["iat"] = now
    payload["exp"] = now + expire_minutes * 60

    header_b64 = _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    signing_input = (header_b64 + "." + payload_b64).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_b64 = _base64url_encode(signature)

    return header_b64 + "." + payload_b64 + "." + signature_b64


def decode_jwt_token(token: str, secret: Optional[str] = None,
                     algorithms: Optional[list] = None) -> Optional[Dict[str, Any]]:
    """验证并解码 JWT Token

    失败返回 None
    """
    if secret is None:
        secret = settings.SECRET_KEY
    if algorithms is None:
        algorithms = ["HS256"]

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header_b64, payload_b64, signature_b64 = parts

        signing_input = (header_b64 + "." + payload_b64).encode("utf-8")
        expected_signature = hmac.new(
            secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        provided_signature = _base64url_decode(signature_b64)

        if not hmac.compare_digest(expected_signature, provided_signature):
            return None

        payload = json.loads(_base64url_decode(payload_b64).decode("utf-8"))

        now = int(time.time())
        if "exp" in payload and payload["exp"] < now:
            return None

        return payload
    except Exception:
        return None


def create_access_token(user_id: int, username: str) -> str:
    """为用户创建访问令牌"""
    payload = {
        "sub": str(user_id),
        "username": username,
        "type": "access"
    }
    return create_jwt_token(payload)


def decode_access_token(token: str, secret: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """验证并解析 access token（校验签名 + exp）"""
    return decode_jwt_token(token, secret=secret)


def extract_user_from_token(token: str) -> Optional[Dict[str, Any]]:
    """从 Token 中提取用户信息"""
    payload = decode_jwt_token(token)
    if payload is None:
        return None
    try:
        return {
            "user_id": int(payload["sub"]),
            "username": payload.get("username", ""),
        }
    except (KeyError, ValueError, TypeError):
        return None
```

**逐段说明（JWT 部分）：**

- **`_base64url_encode/decode`**：JWT 要求的 URL 安全 Base64，编码时去除 `=` 填充，解码时补齐。
- **`create_jwt_token(payload, secret, algorithm, expire_minutes)`**：手写 JWT 生成——构造 header/payload，自动注入 `iat`（签发时间）与 `exp`（过期时间），用 HMAC-SHA256 签名，拼成 `header.payload.signature`。注释说明不依赖 PyJWT 是为兼容老版本 Python。
- **`decode_jwt_token(token, ...)`**：拆分三段，重新计算期望签名，用 `hmac.compare_digest` 做常量时间比较防时序攻击；校验 `exp` 过期；任何异常返回 `None`。
- **`create_access_token(user_id, username)`**：业务封装，payload 含 `sub`（用户 id 字符串）、`username`、`type=access`。
- **`decode_access_token`**：`decode_jwt_token` 的别名封装。
- **`extract_user_from_token(token)`**：解码后提取 `user_id`/`username`，供依赖注入使用。

**依赖关系：** 被 `api/dependencies.py`、`api/v1/auth.py` 依赖。自身依赖 `config.settings` 的 `SECRET_KEY`、`JWT_EXPIRE_MINUTES`。

---

### 2.6 `app/core/logging.py` —— 统一日志

```python
from __future__ import annotations

import logging
import sys
from typing import Optional
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logger(name: str = "rag_system", log_file: Optional[str] = "./data/rag_system.log",
                 level: int = logging.INFO) -> logging.Logger:
    """配置统一的日志系统

    同时输出到控制台和文件
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


logger = setup_logger()
```

**逐段说明：**

- **`setup_logger(name, log_file, level)`**：创建名为 `rag_system` 的 logger。
  - 若已有 handler 直接返回，避免重复添加（幂等）。
  - 格式：`时间 - logger名 - 级别 - 模块:行号 - 消息`。
  - 控制台 handler 输出到 stdout。
  - 文件 handler 使用 `RotatingFileHandler`，单文件 10MB、保留 5 个备份、UTF-8 编码；会自动创建父目录。
  - `propagate = False` 阻止日志向 root logger 冒泡，避免重复输出。
- **`logger = setup_logger()`**：模块级单例，全项目共享。

**依赖关系：** 被 `main.py`、`exceptions.py`、`health.py`、`database.py`、`chat_service.py`、`conversation_service.py`、`retrieval_service.py`、`agent_service.py`、`integration_service.py`、`llm_service.py`、`embedding.py`(api) 等广泛使用。

---

### 2.7 `app/core/exceptions.py` —— 自定义异常与全局处理器

```python
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.logging import logger


class RAGBaseException(Exception):
    """系统自定义异常基类"""

    def __init__(self, message: str, code: int = 400, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)


class NotFoundError(RAGBaseException):
    """资源未找到异常"""

    def __init__(self, message: str = "Resource not found", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=status.HTTP_404_NOT_FOUND, details=details)


class ValidationError(RAGBaseException):
    """参数验证异常"""

    def __init__(self, message: str = "Validation failed", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=status.HTTP_400_BAD_REQUEST, details=details)


class UnauthorizedError(RAGBaseException):
    """未授权异常"""

    def __init__(self, message: str = "Unauthorized", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=status.HTTP_401_UNAUTHORIZED, details=details)


class InternalError(RAGBaseException):
    """内部错误异常"""

    def __init__(self, message: str = "Internal server error", details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=status.HTTP_500_INTERNAL_SERVER_ERROR, details=details)


class ErrorResponse(BaseModel):
    success: bool = False
    code: int
    message: str
    details: Dict[str, Any] = {}


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """全局异常处理器"""

    if isinstance(exc, RAGBaseException):
        logger.warning("Business error: %s - %s", exc.code, exc.message)
        return JSONResponse(
            status_code=exc.code,
            content=ErrorResponse(
                code=exc.code,
                message=exc.message,
                details=exc.details
            ).dict()
        )

    logger.exception("Unhandled exception: %s", str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse(
            code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            message="Internal server error",
            details={"error": str(exc)} if False else {}
        ).dict()
    )


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI 参数验证异常处理器"""
    message = "Request validation failed"
    details = {}
    try:
        details = {"errors": exc.errors()}
    except Exception:
        pass
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            message=message,
            details=details
        ).dict()
    )
```

**逐段说明：**

- **异常类层级**：`RAGBaseException` 为基类，携带 `message`/`code`/`details`；子类 `NotFoundError`(404)、`ValidationError`(400)、`UnauthorizedError`(401)、`InternalError`(500) 预设常见状态码。
- **`ErrorResponse`**：pydantic 模型，统一错误响应体 `{success, code, message, details}`。
- **`global_exception_handler`**：注册到 `Exception` 的兜底处理器。若是业务异常则按其 code 返回；否则记录完整堆栈并返回 500。注意 `details={"error": str(exc)} if False else {}`——`if False` 表示生产环境不回传内部错误细节给客户端（安全考量）。
- **`validation_exception_handler`**：处理 FastAPI 的 `RequestValidationError`，返回 422 并附带字段级错误列表。

**依赖关系：** 被 `main.py` 注册；`InternalError` 被 `embedding_service.py` 用于向量质量校验失败时抛出。

---

### 2.8 `requirements.txt` —— 依赖清单

```text
fastapi>=0.100,<0.104
uvicorn>=0.20,<0.23
sqlalchemy>=2.0,<2.1
pydantic>=1.10,<2.0
python-dotenv>=0.20,<2.0
python-multipart>=0.0.5,<0.0.10
aiofiles>=22.0,<23.0
httpx>=0.24,<0.26
numpy>=1.21,<1.27
faiss-cpu>=1.7,<1.9
pytest>=7.0,<8.0
pytest-asyncio>=0.21,<0.22
```

**说明：**

- `fastapi` + `uvicorn`：Web 框架与 ASGI 服务器。
- `sqlalchemy 2.0`：ORM（注意使用的是 `declarative_base` 旧式 API）。
- `pydantic <2.0`：使用 v1 的 `BaseSettings`，与 `config.py` 一致。
- `python-multipart`：FastAPI 文件上传所需。
- `aiofiles`：异步文件 IO（虽项目多为同步）。
- `httpx`：`RemoteAPIEmbeddingProvider` 远程 Embedding 调用。
- `numpy` / `faiss-cpu`：向量计算与近似最近邻搜索。
- `pytest` / `pytest-asyncio`：测试框架。

**注意：** `llm_service.py` 的 `HTTPLLMProvider` 使用了 `requests`，但 `requirements.txt` 未列出 `requests`，是一个潜在依赖缺失（详见改进点）。

---

### 2.9 `.env.example` —— 环境变量示例

```text
# ====== 应用基础配置 ======
APP_NAME=RAG Knowledge Base System
APP_ENV=development
APP_DEBUG=true
APP_HOST=0.0.0.0
APP_PORT=8000

# ====== 数据库配置（Day 1 先用 SQLite，后续切换 PostgreSQL） ======
DATABASE_URL=sqlite:///./data/rag_system.db

# ====== PostgreSQL 配置（后期使用） ======
# DATABASE_URL=postgresql+asyncpg://rag_user:rag_password@localhost:5432/rag_db

# ====== 安全配置 ======
SECRET_KEY=change-this-in-production-to-a-long-random-secret-key
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# ====== 外部服务 API Key（后期填入） ======
# DeepSeek API
DEEPSEEK_API_KEY=
DEEPSEEK_API_URL=https://api.deepseek.com/v1

# 备用：OpenAI 兼容接口
OPENAI_API_KEY=
OPENAI_API_URL=https://api.openai.com/v1

# Embedding API（可选：远程Embedding）
EMBEDDING_API_KEY=
EMBEDDING_API_URL=
EMBEDDING_MODEL=bge-m3

# ====== 文件存储路径 ======
UPLOAD_DIR=./data/uploads
VECTOR_STORE_DIR=./data/vector_stores

# ====== CORS 配置 ======
CORS_ORIGINS=http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173
```

**说明：** 与 `config.py` 字段一一对应，提供部署时的环境变量模板。注释中给出 PostgreSQL 切换示例。CORS 默认放行 3000（React 默认）与 5173（Vite 默认）端口。

---

### 2.10 `pytest.ini` —— 测试配置

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
asyncio_mode = auto
addopts = -v
```

**说明：**

- 测试目录 `tests`，文件名 `test_*.py`，类名 `Test*`，函数名 `test_*`。
- `asyncio_mode = auto`：自动识别异步测试函数，无需显式 `@pytest.mark.asyncio`。
- `addopts = -v`：默认详细输出。

---

## 3. 数据库与模型模块

### 3.1 `app/models/database.py` —— SQLAlchemy 引擎与会话

```python
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

    import app.models.entities  # noqa: F401 — 触发显式 import，使 relationship 字符串引用可解析

    Base.metadata.create_all(bind=engine)
    logger.info("Database initialized successfully")
```

**逐段说明：**

- **`engine`**：根据 `DATABASE_URL` 创建引擎。SQLite 时禁用 `check_same_thread`（FastAPI 多线程场景必需）；`echo=APP_DEBUG` 调试时打印 SQL；`pool_pre_ping=True` 连接前 ping 防止使用失效连接。
- **`SessionLocal`**：会话工厂，关闭自动提交与自动 flush，绑定 engine。
- **`Base`**：所有 ORM 实体的 declarative 基类。
- **`get_db()`**：生成器风格的依赖注入函数，yield 一个会话，finally 关闭。注意此处定义了 `get_db`，被 `dependencies.py` 通过 `from app.models.database import get_db` 再次导出。
- **`init_db()`**：建表入口。关键点：显式 `import app.models.entities` 触发所有实体类被加载，使 SQLAlchemy 能解析 `relationship("KnowledgeBase")` 这类字符串引用；然后 `create_all` 创建所有表。

**关联关系：** 被 `main.py` 启动事件调用 `init_db()`；被 `entities/*` 通过 `Base` 继承；被 `dependencies.py` 通过 `SessionLocal`/`get_db` 使用；被 `health.py` 通过 `engine` 做健康检查。

---

### 3.2 `app/models/response.py` —— 统一响应包装

```python
from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar, Optional

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一的 API 响应格式"""
    success: bool = True
    code: int = 200
    message: str = "OK"
    data: Optional[T] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        from_attributes = True
```

**逐段说明：**

- **`ApiResponse[T]`**：泛型响应模型，`T` 为数据负载类型。字段：`success`、`code`、`message`、`data`、`timestamp`。
- **`Config.from_attributes = True`**（pydantic v1 写法，等价于 v2 的 `from_attributes`）：允许从 ORM 对象属性构造。
- 全项目 API 路由统一返回 `ApiResponse[SomeModel](data=...)`，保证响应结构一致。

**依赖关系：** 被几乎所有 `api/v1/*` 与 `api/health.py` 使用。

---

### 3.3 `app/models/schemas.py` —— Pydantic 请求/响应模型

该文件集中定义了所有 API 请求体与响应体的 pydantic 模型，按业务域分组：

```python
from __future__ import annotations

"""Pydantic schemas - API 请求/响应数据结构"""

from datetime import datetime
from typing import List, Optional, Dict, Any

from pydantic import BaseModel, Field


# ============ User / Auth ============

class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[str] = None
    password: str = Field(..., min_length=6)

class UserLogin(BaseModel):
    username: str
    password: str

class UserInfo(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    is_active: bool = True
    is_admin: bool = False
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class TokenData(BaseModel):
    access_token: str
    user: UserInfo
```

- **User 域**：`UserRegister`（注册，用户名 3-50 字符、密码≥6）、`UserLogin`、`UserInfo`（含 `from_attributes` 支持 ORM 转换）、`TokenData`（登录返回 token+用户）。

```python
# ============ Health ============

class HealthInfo(BaseModel):
    status: str
    app_name: str
    version: str
    database: str
    timestamp: datetime
```

- **Health**：健康检查返回结构。

```python
# ============ Knowledge Base ============

class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None
    embedding_model: Optional[str] = "default"
    chunk_size: Optional[int] = 500
    chunk_overlap: Optional[int] = 50
    is_public: Optional[bool] = False

class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    chunk_size: Optional[int] = None
    chunk_overlap: Optional[int] = None
    is_public: Optional[bool] = None
    status: Optional[str] = None

class KnowledgeBaseInfo(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    user_id: Optional[int] = None
    embedding_model: str = "default"
    chunk_size: int = 500
    chunk_overlap: int = 50
    is_public: bool = False
    status: str = "active"
    total_documents: int = 0
    total_chunks: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class KnowledgeBaseListResponse(BaseModel):
    items: List[KnowledgeBaseInfo] = []
    total: int = 0
    page: int = 1
    page_size: int = 20
```

- **知识库域**：创建/更新/详情/列表分页模型。`KnowledgeBaseInfo` 含统计字段 `total_documents`/`total_chunks`。

```python
# ============ Document ============

class DocumentInfo(BaseModel):
    id: int
    knowledge_base_id: int
    filename: str
    file_type: Optional[str] = None
    file_size: int = 0
    status: str = "pending"
    total_chunks: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class DocumentListResponse(BaseModel):
    items: List[DocumentInfo] = []
    total: int = 0
    page: int = 1
    page_size: int = 20

class DocumentUploadResponse(BaseModel):
    document: DocumentInfo
    message: str = ""

class ChunkInfo(BaseModel):
    id: int
    document_id: int
    content: str
    chunk_index: int = 0
    vector_index: int = -1

    class Config:
        from_attributes = True

class SearchResponse(BaseModel):
    query: str
    results: List[Dict[str, Any]] = []
    total: int = 0
    search_time_ms: float = 0.0
```

- **文档域**：`DocumentInfo`、列表、上传响应、分块 `ChunkInfo`、搜索响应 `SearchResponse`（含耗时）。

```python
# ============ Chat ============

class ChatMessageItem(BaseModel):
    role: str = "user"
    content: str

class ChatRequest(BaseModel):
    knowledge_base_id: int
    message: str
    history: Optional[List[ChatMessageItem]] = None
    top_k: Optional[int] = None
    min_score: Optional[float] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    include_raw: bool = False

class RetrievedChunkItem(BaseModel):
    chunk_id: int
    score: float
    document_id: Optional[int] = None
    document_filename: Optional[str] = None
    content: str

class ChatResponse(BaseModel):
    query: str
    answer: str
    model: Optional[str] = None
    provider: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    latency_ms: float = 0.0
    retrieved_chunks: List[RetrievedChunkItem] = []
    system_prompt: Optional[str] = None

class LLMProviderInfo(BaseModel):
    provider: str
    model: str
    has_api_key: bool = False
    supported_providers: List[str] = []
```

- **对话域**：`ChatRequest` 携带知识库 id、消息、历史、检索与生成参数、`include_raw` 调试开关；`ChatResponse` 含 answer、模型、provider、耗时、检索片段、可选 system_prompt；`LLMProviderInfo` 用于 provider 信息查询。

```python
# ============ Embedding ============

class EncodeRequest(BaseModel):
    texts: List[str]

class EncodeSingleRequest(BaseModel):
    text: str

class EncodingInfo(BaseModel):
    dim: int
    norm: float
    sample_preview: List[float] = []

class EncodeResponse(BaseModel):
    provider: str
    dim: int
    count: int
    items: List[EncodingInfo] = []
    cache_stats: Optional[Dict[str, Any]] = None

class SimilarityRequest(BaseModel):
    text_a: str
    text_b: str

class SimilarityResponse(BaseModel):
    provider: str
    score: float
    interpretation: str

class EmbeddingStatus(BaseModel):
    provider: str
    dim: int
    caching_enabled: bool
    sample_similarity_matrix: Optional[List[List[float]]] = None
    sample_texts: List[str] = []
```

- **Embedding 域**：批量/单条编码请求与响应（含维度、L2 范数、前几维预览、缓存统计）、相似度计算请求响应（含文字解释）、服务状态。

```python
# ============ Retrieval / Vector Store ============

class VectorSearchQuery(BaseModel):
    query_text: str
    top_k: int = 5
    min_score: float = 0.0

class VectorSearchItem(BaseModel):
    vector_index: int
    score: float
    chunk_id: Optional[int] = None
    document_id: Optional[int] = None
    content_preview: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class VectorSearchResponse(BaseModel):
    knowledge_base_id: int
    query_text: str
    hits: int
    items: List[VectorSearchItem] = []
    backend: str = "unknown"

class IndexStatusResponse(BaseModel):
    knowledge_base_id: int
    exists: bool = False
    loaded: bool = False
    consistent: Optional[bool] = None
    backend: Optional[str] = None
    dim: Optional[int] = None
    total_vectors: int = 0
    next_index: int = 0
    metadata_count: int = 0
    nlist: int = 0
    nprobe: int = 0
    is_trained: bool = False
    ntotal: int = 0
    issues: Optional[List[str]] = None
    path: Optional[str] = None

class IndexOperationResponse(BaseModel):
    success: bool
    knowledge_base_id: int
    message: str

class GlobalIndexStatusResponse(BaseModel):
    base_dir: str
    total_kbs_on_disk: int = 0
    stored_kbs: List[Dict[str, Any]] = []
    faiss_available: bool = False
    numpy_available: bool = False
    default_dim: int = 384
```

- **向量检索域**：搜索查询/结果项/响应（含后端标识）、单库索引状态 `IndexStatusResponse`（含一致性、训练状态等）、索引操作响应、全局索引状态。

**依赖关系：** 被 `api/v1/*` 与 `api/health.py` 广泛使用作为请求/响应模型；部分 service 也引用（如 `kb_service.py` 引用 `KnowledgeBaseCreate/Update`）。

---

### 3.4 `app/models/__init__.py`

空文件，包标记。

### 3.5 `app/models/entities/__init__.py` —— 实体聚合

```python
"""ORM 实体模块包。

必须显式 import 各子模块，以便 SQLAlchemy 正确扫描 relationship 中
以字符串引用的类名（如 relationship("KnowledgeBase")）。
"""
from .user import User
from .knowledge_base import KnowledgeBase
from .document import Document, DocumentChunk
from .conversation import Conversation, ChatMessageRecord

__all__ = [
    "User",
    "KnowledgeBase",
    "Document",
    "DocumentChunk",
    "Conversation",
    "ChatMessageRecord",
]
```

**说明：** 关键的"触发器"包。`database.init_db()` 显式 import 它，使所有实体类被注册到 `Base.metadata`，从而 `relationship("KnowledgeBase")` 这类字符串引用可被解析。导出 `User/KnowledgeBase/Document/DocumentChunk/Conversation/ChatMessageRecord`。

> **注意（潜在问题）：** `conversation_service.py` 中 `from app.models.entities.conversation import Conversation, Message` 引用了 `Message`，但实体文件实际定义的类名是 `ChatMessageRecord`，并不存在 `Message`。这会导致该 service 在运行时 `ImportError`。详见第 12 节改进点。

---

### 3.6 `app/models/entities/user.py` —— 用户实体

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.database import Base


class User(Base):
    """用户表"""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=True)
    password_hash = Column(String(512), nullable=False)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    knowledge_bases = relationship("KnowledgeBase", back_populates="owner", lazy="dynamic")
    conversations = relationship("Conversation", back_populates="owner", lazy="dynamic")

    def __repr__(self):
        return "<User(id=%d, username='%s')>" % (self.id, self.username)
```

**逐段说明：**

- 表名 `users`，字段：自增主键 `id`、唯一用户名、可空唯一邮箱、密码哈希、激活/管理员标志、创建/更新时间。
- `created_at` 同时设置 Python 端 `default` 与 DB 端 `server_default=func.now()`。
- `updated_at` 带 `onupdate=datetime.utcnow`，更新时自动刷新。
- `relationship`：一对多关联 `KnowledgeBase`（`back_populates="owner"`）与 `Conversation`，`lazy="dynamic"` 返回 query 对象而非直接加载（适合过滤/计数）。
- `__repr__` 便于调试。

**依赖关系：** 被 `dependencies.py`、`auth.py`、各 service 通过 `User` 类查询。

---

### 3.7 `app/models/entities/knowledge_base.py` —— 知识库实体

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.database import Base


class KnowledgeBase(Base):
    """知识库表"""

    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String(200), nullable=False, index=True)
    description = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    embedding_model = Column(String(100), default="default")
    chunk_size = Column(Integer, default=500)
    chunk_overlap = Column(Integer, default=50)
    is_public = Column(Boolean, default=False, index=True)
    status = Column(String(50), default="active")
    total_documents = Column(Integer, default=0)
    total_chunks = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="knowledge_bases")
    documents = relationship("Document", back_populates="knowledge_base", lazy="dynamic")
    conversations = relationship("Conversation", back_populates="knowledge_base", lazy="dynamic")

    def __repr__(self):
        return "<KnowledgeBase(id=%d, name='%s')>" % (self.id, self.name)
```

**逐段说明：**

- 表名 `knowledge_bases`，字段含名称、描述、所有者外键 `user_id`、embedding 模型名、分块大小/重叠、是否公开、状态、文档/分块计数、时间戳。
- `user_id` 可空（允许匿名创建的公开知识库场景）。
- `is_public`、`user_id`、`name` 建索引以加速列表查询与权限过滤。
- relationship：反向关联 `User`(owner)、一对多 `Document`、一对多 `Conversation`，均 `lazy="dynamic"`。

**依赖关系：** 被 `kb_service`、`document_service`、`retrieval_service`、`integration` API 等广泛查询。

---

### 3.8 `app/models/entities/document.py` —— 文档与分块实体

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.database import Base


class Document(Base):
    """文档表"""

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    filename = Column(String(500), nullable=False)
    file_path = Column(String(1000), nullable=True)
    file_type = Column(String(50))
    mime_type = Column(String(100))
    file_size = Column(Integer, default=0)
    size_bytes = Column(Integer, default=0)
    description = Column(Text, nullable=True)
    content_text = Column(Text, nullable=True)
    status = Column(String(50), default="pending")
    total_chunks = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", lazy="dynamic")

    def __repr__(self):
        return "<Document(id=%d, filename='%s')>" % (self.id, self.filename)


class DocumentChunk(Base):
    """文档分块表"""

    __tablename__ = "chunks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=False, index=True)
    content = Column(Text, nullable=False)
    chunk_index = Column(Integer, default=0)
    metadata_ = Column("metadata", Text, nullable=True)
    vector_index = Column(Integer, default=-1)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    document = relationship("Document", back_populates="chunks")

    def __repr__(self):
        return "<DocumentChunk(id=%d, document_id=%d, index=%d)>" % (self.id, self.document_id, self.chunk_index)
```

**逐段说明：**

- **`Document`**：表 `documents`。关键字段：`knowledge_base_id` 外键、`filename`、`file_path`（磁盘路径）、`file_type`/`mime_type`、`file_size`/`size_bytes`（冗余双字段）、`content_text`（提取的全文，限长存储）、`status`（pending/processing/processed/skipped/error）、`total_chunks`。relationship 反向关联知识库与分块。
- **`DocumentChunk`**：表 `chunks`。字段：`document_id` 与 `knowledge_base_id` 双外键（便于按库直接查 chunk）、`content`、`chunk_index`、`metadata_`（注意 Python 属性名 `metadata_` 映射到 DB 列名 `metadata`，避免与 SQLAlchemy 内置 `metadata` 冲突）、`vector_index`（在向量存储中的索引，初始 -1）。

> 注意：`DocumentChunk` 在 `retrieval_service.py` 中被别名 `Chunk = DocumentChunk` 引用；在 `document_service.py` 中被别名 `Chunk` 引用。

---

### 3.9 `app/models/entities/conversation.py` —— 对话与消息实体

```python
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.database import Base


class Conversation(Base):
    """对话会话表"""

    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), nullable=True, index=True)
    title = Column(String(500), default="New Conversation")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    owner = relationship("User", back_populates="conversations")
    knowledge_base = relationship("KnowledgeBase", back_populates="conversations")
    messages = relationship("ChatMessageRecord", back_populates="conversation", lazy="dynamic")

    def __repr__(self):
        return "<Conversation(id=%d, title='%s')>" % (self.id, self.title)


class ChatMessageRecord(Base):
    """对话消息表"""

    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    retrieved_contexts = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, server_default=func.now())

    conversation = relationship("Conversation", back_populates="messages")

    def __repr__(self):
        return "<ChatMessageRecord(id=%d, role='%s')>" % (self.id, self.role)
```

**逐段说明：**

- **`Conversation`**：表 `conversations`。关联用户与知识库（均可空），`title`、`is_active`、时间戳。relationship 反向关联 owner、knowledge_base、messages。
- **`ChatMessageRecord`**：表 `messages`。字段 `conversation_id` 外键、`role`（user/assistant/system）、`content`、`retrieved_contexts`（JSON 文本，存检索到的上下文，供回溯）、时间戳。

> **关键不一致：** 实体类名为 `ChatMessageRecord`，但 `conversation_service.py` 与 `api/v1/conversation.py` 试图导入 `Message`。这是一个真实的命名不一致 bug，会导致运行时 `ImportError`（详见第 12 节）。

---

## 4. API 层模块

### 4.1 `app/api/__init__.py` 与 `app/api/v1/__init__.py`

`app/api/__init__.py` 为空。`app/api/v1/__init__.py` 聚合所有 v1 路由：

```python
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, knowledge_base, document, chat, agent, embedding, retrieval, conversation, integration

api_router = APIRouter(prefix="/v1")

api_router.include_router(auth.router)
api_router.include_router(knowledge_base.router)
api_router.include_router(document.router)
api_router.include_router(embedding.router)
api_router.include_router(retrieval.router)
api_router.include_router(chat.router)
api_router.include_router(agent.router)
api_router.include_router(conversation.router)
api_router.include_router(integration.router)
```

**说明：** 创建带 `/v1` 前缀的聚合 router，挂载 9 个子路由模块。最终由 `main.py` 以 `/api` 前缀挂载，形成 `/api/v1/<子路由前缀>/...` 的完整路径。

---

### 4.2 `app/api/health.py` —— 健康检查

```python
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_dep
from app.core.config import settings
from app.core.logging import logger
from app.models.database import engine
from app.models.response import ApiResponse
from app.models.schemas import HealthInfo

router = APIRouter(prefix="/health", tags=["Health"])

__version__ = "0.1.0"


@router.get("", response_model=ApiResponse[HealthInfo])
async def health_check():
    """系统健康检查接口"""
    db_status = "ok"
    try:
        from sqlalchemy import text
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("Database health check failed: %s", str(e))
        db_status = "error"

    info = HealthInfo(
        status="ok" if db_status == "ok" else "degraded",
        app_name=settings.APP_NAME,
        version=__version__,
        database=db_status,
        timestamp=datetime.utcnow()
    )
    return ApiResponse[HealthInfo](data=info)


@router.get("/ping", response_model=ApiResponse[str])
async def ping():
    """简易 Ping 接口"""
    return ApiResponse[str](data="pong")
```

**逐段说明：**

- `router` 前缀 `/health`，tag `Health`。
- **`GET /health`**：用 `engine.connect()` 执行 `SELECT 1` 探活数据库；DB 异常则状态降级为 `degraded`，否则 `ok`。返回应用名、版本、DB 状态、时间戳。
- **`GET /health/ping`**：极简探活，返回 `"pong"`，不查 DB。

> 注意：`get_db_dep` 被导入但未实际使用（可移除）。

---

### 4.3 `app/api/dependencies.py` —— 依赖注入

```python
from __future__ import annotations

from typing import Any, Generator, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.models.database import get_db, SessionLocal
from app.models.entities.user import User
from app.core.security import extract_user_from_token


security = HTTPBearer(auto_error=False)


def get_db_dep() -> Generator[Session, Any, None]:
    """数据库会话依赖注入"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dep)
) -> Optional[User]:
    """可选的当前用户 - 未登录时返回 None

    与 get_current_user 的区别是这个不抛出 401 异常
    """
    if credentials is None or not credentials.scheme.lower() == "bearer":
        return None

    token = credentials.credentials
    user_info = extract_user_from_token(token)
    if user_info is None:
        return None

    user = db.query(User).filter(User.id == user_info["user_id"]).first()
    if user is None or not user.is_active:
        return None

    return user


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dep)
) -> User:
    """必须登录的当前用户依赖 - 未登录时抛出 401"""
    if credentials is None or not credentials.scheme.lower() == "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供有效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    user_info = extract_user_from_token(token)
    if user_info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_info["user_id"]).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户账户已停用",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """当前用户必须是管理员"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user
```

**逐段说明：**

- **`security = HTTPBearer(auto_error=False)`**：声明 Bearer token 安全方案，`auto_error=False` 表示无 token 时不自动报错，交由依赖函数自行处理（支持"可选登录"）。
- **`get_db_dep()`**：每次请求创建独立 DB 会话，请求结束关闭。
- **`get_current_user_optional()`**：可选认证。无 token/无效 token/用户不存在或停用 → 返回 `None`，不抛异常。用于公开+私有混合接口（如知识库列表）。
- **`get_current_user()`**：强制认证。各失败分支抛 401 并带 `WWW-Authenticate: Bearer` 头。
- **`get_current_admin()`**：在 `get_current_user` 基础上校验 `is_admin`，否则 403。

**依赖关系：** 该模块再导出了 `get_db`（来自 `database.py`），因此 `agent.py`/`conversation.py`/`integration.py` 中 `from app.api.dependencies import get_db` 能正常工作。被几乎所有 v1 路由依赖。

---

### 4.4 `app/api/v1/auth.py` —— 认证路由

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_dep, get_current_user
from app.models.entities.user import User
from app.models.schemas import UserRegister, UserLogin, UserInfo, TokenData
from app.core.security import hash_password, verify_password, create_access_token
from app.models.response import ApiResponse


router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=ApiResponse[TokenData])
def register(payload: UserRegister, db: Session = Depends(get_db_dep)):
    """用户注册

    - 检查用户名是否已存在
    - PBKDF2 哈希密码后存储
    - 注册成功后直接返回 Token（自动登录）
    """
    # 检查用户名是否存在
    existing_user = db.query(User).filter(User.username == payload.username).first()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已被占用",
        )

    # 检查邮箱是否存在
    if payload.email:
        existing_email = db.query(User).filter(User.email == payload.email).first()
        if existing_email is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该邮箱已被注册",
            )

    # 创建用户
    hashed = hash_password(payload.password)
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hashed,
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 生成 Token
    token = create_access_token(user.id, user.username)

    return ApiResponse[TokenData](
        data=TokenData(access_token=token, user=UserInfo.from_orm(user))
    )


@router.post("/login", response_model=ApiResponse[TokenData])
def login(payload: UserLogin, db: Session = Depends(get_db_dep)):
    """用户登录

    - 验证用户名和密码
    - 返回 JWT Token
    """
    user = db.query(User).filter(User.username == payload.username).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已停用，请联系管理员",
        )

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token(user.id, user.username)

    return ApiResponse[TokenData](
        data=TokenData(access_token=token, user=UserInfo.from_orm(user))
    )


@router.get("/me", response_model=ApiResponse[UserInfo])
def get_me(current_user: User = Depends(get_current_user)):
    """获取当前登录用户信息

    需要在请求头中携带: `Authorization: Bearer <token>`
    """
    return ApiResponse[UserInfo](data=UserInfo.from_orm(current_user))
```

**逐段说明：**

- `router` 前缀 `/auth`。
- **`POST /auth/register`**：用户名/邮箱查重（409 冲突），`hash_password` 哈希后建用户，`create_access_token` 直接签发 token（注册即登录），返回 `TokenData`。
- **`POST /auth/login`**：按用户名查用户（不存在 401），校验激活状态（停用 403），`verify_password` 验证密码（失败 401），签发 token。
- **`GET /auth/me`**：依赖 `get_current_user`，返回当前用户信息。

**关联关系：** 依赖 `dependencies`、`security`、`entities.user`、`schemas`、`response`。

---

### 4.5 `app/api/v1/knowledge_base.py` —— 知识库 CRUD

```python
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_dep, get_current_user, get_current_user_optional
from app.models.entities.user import User
from app.models.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    KnowledgeBaseInfo,
    KnowledgeBaseListResponse,
)
from app.services.kb_service import KnowledgeBaseService
from app.models.response import ApiResponse


router = APIRouter(prefix="/knowledge-bases", tags=["知识库"])


@router.post("", response_model=ApiResponse[KnowledgeBaseInfo])
def create_kb(
    payload: KnowledgeBaseCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
):
    """创建知识库（需要登录）"""
    service = KnowledgeBaseService(db)
    kb = service.create(payload, user_id=current_user.id)
    return ApiResponse[KnowledgeBaseInfo](data=KnowledgeBaseInfo.from_orm(kb))


@router.get("", response_model=ApiResponse[KnowledgeBaseListResponse])
def list_kbs(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    keyword: Optional[str] = Query(None, description="搜索关键词"),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db_dep),
):
    """获取知识库列表

    - 未登录：仅返回公开的知识库
    - 已登录：返回自己的 + 公开的知识库
    """
    user_id = current_user.id if current_user else None
    service = KnowledgeBaseService(db)
    items, total = service.list(user_id=user_id, page=page, page_size=page_size, keyword=keyword)

    data = KnowledgeBaseListResponse(
        items=[KnowledgeBaseInfo.from_orm(kb) for kb in items],
        total=total,
        page=page,
        page_size=page_size,
    )
    return ApiResponse[KnowledgeBaseListResponse](data=data)


@router.get("/{kb_id}", response_model=ApiResponse[KnowledgeBaseInfo])
def get_kb(
    kb_id: int,
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: Session = Depends(get_db_dep),
):
    """获取单个知识库详情"""
    user_id = current_user.id if current_user else None
    service = KnowledgeBaseService(db)
    kb = service.get_by_id(kb_id, user_id=user_id)

    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在或无权访问",
        )

    return ApiResponse[KnowledgeBaseInfo](data=KnowledgeBaseInfo.from_orm(kb))


@router.put("/{kb_id}", response_model=ApiResponse[KnowledgeBaseInfo])
def update_kb(
    kb_id: int,
    payload: KnowledgeBaseUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
):
    """更新知识库（仅限所有者）"""
    service = KnowledgeBaseService(db)
    kb = service.update(kb_id, payload, user_id=current_user.id)

    if kb is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在或无权修改",
        )

    return ApiResponse[KnowledgeBaseInfo](data=KnowledgeBaseInfo.from_orm(kb))


@router.delete("/{kb_id}", response_model=ApiResponse[dict])
def delete_kb(
    kb_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db_dep),
):
    """删除知识库（仅限所有者）"""
    service = KnowledgeBaseService(db)
    success = service.delete(kb_id, user_id=current_user.id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在或无权删除",
        )

    return ApiResponse[dict](data={"message": "删除成功", "kb_id": kb_id})
```

**逐段说明：**

- `router` 前缀 `/knowledge-bases`。所有方法把业务委托给 `KnowledgeBaseService(db)`，路由层只做参数校验、权限依赖注入、HTTP 状态码映射。
- **`POST /`**：需登录，创建。
- **`GET /`**：可选登录，分页+关键词搜索；未登录只见公开库，登录见自己+公开库。
- **`GET /{kb_id}`**：可选登录，详情；无权访问返回 None → 404。
- **`PUT /{kb_id}`**：需登录，更新（仅所有者）。
- **`DELETE /{kb_id}`**：需登录，删除（仅所有者）。

**关联关系：** 依赖 `kb_service.KnowledgeBaseService`。

---

### 4.6 `app/api/v1/document.py` —— 文档上传/列表/搜索

该文件较大，提供文档上传、列表、详情、分块列表、删除、向量搜索 6 个接口，路径前缀 `/knowledge-bases/{kb_id}/documents`。核心片段：

```python
router = APIRouter(prefix="/knowledge-bases/{kb_id}/documents", tags=["Documents"])


@router.post("", response_model=ApiResponse[DocumentUploadResponse])
def upload_document(
    kb_id: int,
    file: UploadFile = File(..., description="要上传的文件 (.txt, .md, .pdf, .doc, .docx 等)"),
    chunk_size: Optional[int] = Form(None, ge=100, le=2000, description="分块大小（字符），默认使用知识库配置"),
    chunk_overlap: Optional[int] = Form(None, ge=0, le=500, description="分块重叠（字符），默认使用知识库配置"),
    current_user: User = Depends(get_current_user),
    db=Depends(get_db_dep),
):
    """上传文档到知识库（仅知识库所有者）

    - 自动提取文本
    - 自动按配置的 chunk_size/chunk_overlap 分块
    - 自动向量化并建立 FAISS 索引
    - 上传后可以立即使用 /search 接口进行语义搜索
    """
    content = file.file.read()
    filename = file.filename or "unnamed.txt"

    service = DocumentService(db)
    try:
        doc = service.process_upload(
            kb_id=kb_id,
            user_id=current_user.id,
            filename=filename,
            file_content=content,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="文档处理失败: %s" % str(e),
        )

    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="知识库不存在或无权访问",
        )

    return ApiResponse[DocumentUploadResponse](...)
```

**逐段说明（核心要点）：**

- **上传**：读取文件字节，委托 `DocumentService.process_upload`，该 service 内部完成权限校验→保存磁盘→建 Document 记录→提取文本→分块→向量化→建索引→更新统计。处理异常返回 500，无权限返回 404。
- **列表**：分页查询，无结果时二次校验知识库是否存在/有权限，区分 404 与 403。
- **详情/分块列表**：可选登录，按权限过滤。
- **删除**：需登录（所有者），`DocumentService.delete_document` 会删磁盘文件、chunks、重建向量索引、更新统计。
- **向量搜索**：`POST /search`，记录耗时，无结果时二次校验权限区分 404/403。

**关联关系：** 依赖 `DocumentService`。

---

### 4.7 `app/api/v1/chat.py` —— RAG 对话

提供主对话、仅搜索、provider 信息、根信息、SSE 流式对话 5 个端点：

```python
_cache = {}


def _get_pipeline() -> RAGPipeline:
    if "pipeline" not in _cache:
        _cache["pipeline"] = RAGPipeline(
            embedding=EmbeddingService(),
            vector_manager=VectorStoreManager(settings.VECTOR_STORE_DIR),
        )
    return _cache["pipeline"]
```

- **`_get_pipeline()`**：模块级单例缓存 `RAGPipeline`，避免每次请求重建 embedding/vector_manager。

```python
@router.post("/message", response_model=ApiResponse[ChatResponse])
def chat_message(payload: ChatRequest):
    """RAG 对话 —— 单轮/多轮对话"""
    pipeline = _get_pipeline()
    history: List[ChatMessage] = []
    if payload.history:
        for h in payload.history[-8:]:  # 最多保留最近 8 轮
            role = "assistant" if h.role == "assistant" else "user"
            history.append(ChatMessage(role=role, content=h.content))

    rag_result = pipeline.answer(
        knowledge_base_id=payload.knowledge_base_id,
        query_text=payload.message,
        history=history,
        top_k=payload.top_k,
        min_score=payload.min_score,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        debug_include_system_prompt=payload.include_raw,
    )
    ...
```

- **`POST /chat/message`**：把 history 转 `ChatMessage` 列表（保留最近 8 轮），调用 `pipeline.answer`，返回 `ChatResponse`。`include_raw` 控制是否返回 system_prompt 与完整 chunk 内容。

```python
@router.post("/message/stream")
def chat_message_stream(payload: ChatRequest):
    """RAG 流式对话 —— 使用 Server-Sent Events (SSE)"""
    ...
    def sse_generator():
        try:
            for event in pipeline.answer_stream(...):
                yield "data: %s\n\n" % _json.dumps(event, ensure_ascii=False)
        except Exception as exc:
            yield "data: %s\n\n" % _json.dumps(
                {"type": "error", "message": str(exc)}, ensure_ascii=False
            )

    return _StreamingResponse(sse_generator(), media_type="text/event-stream")
```

- **`POST /chat/message/stream`**：SSE 流式，每个事件以 `data: {...}\n\n` 格式输出，事件类型包括 `retrieval_done`/`token`/`done`/`error`。

```python
@router.post("/search/{kb_id}", response_model=ApiResponse[dict])
def chat_search_only(kb_id: int, payload: dict):
    """仅做向量搜索 —— 返回 top-k chunks，供前端预览/调试"""
    ...

@router.get("/provider", response_model=ApiResponse[LLMProviderInfo])
def get_provider():
    """查看当前 LLM provider / 模型信息"""
    ...
```

- **`POST /chat/search/{kb_id}`**：仅检索不调 LLM，供前端预览。
- **`GET /chat/provider`**：返回当前 LLM provider/模型/是否有 API key/支持的 provider 列表。

**关联关系：** 依赖 `RAGPipeline`、`EmbeddingService`、`VectorStoreManager`、`get_llm_service`。

---

### 4.8 `app/api/v1/agent.py` —— ReAct Agent

```python
class AgentQueryRequest(BaseModel):
    query: str
    knowledge_base_id: Optional[int] = None
    max_turns: int = 3
    include_raw_steps: bool = False
    history: Optional[List[Dict[str, Any]]] = None

class AgentStepItem(BaseModel):
    thought: str
    action: str
    action_input: str
    observation: str
    latency_ms: float

class AgentResponse(BaseModel):
    success: bool
    answer: str
    steps: List[AgentStepItem] = []
    latency_ms: float = 0.0
    error: Optional[str] = None


@router.post("/run", response_model=AgentResponse)
def agent_run(
    payload: AgentQueryRequest,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_current_user_optional),
):
    """执行 Agent 推理循环 —— 通过 ReAct 风格查询知识库并回答用户问题"""
    if not payload.knowledge_base_id:
        raise HTTPException(status_code=400, detail="knowledge_base_id 不能为空")

    agent = AgentService(db)
    result = agent.run(
        query=payload.query,
        kb_id=payload.knowledge_base_id,
        user_id=user.id if user else None,
        max_turns=payload.max_turns,
        history=payload.history,
        include_raw_steps=payload.include_raw_steps,
    )
    return AgentResponse(...)


@router.get("/tools")
def agent_tools(db: Session = Depends(get_db)):
    """返回当前 Agent 可用的工具列表"""
    agent = AgentService(db)
    return {"tools": [{"name": t.name, "description": t.description} for t in agent.tools.values()]}
```

**逐段说明：**

- 内联定义请求/响应模型 `AgentQueryRequest`/`AgentStepItem`/`AgentResponse`。
- **`POST /agent/run`**：执行 ReAct 推理循环，`max_turns` 限制工具调用次数，`include_raw_steps` 控制是否返回每步详情。
- **`GET /agent/tools`**：列出可用工具（search_kb/get_doc）。

**关联关系：** 依赖 `AgentService`。

---

### 4.9 `app/api/v1/retrieval.py` —— 向量索引管理

```python
_manager_singleton: Optional[VectorStoreManager] = None
_embedding_singleton: Optional[EmbeddingService] = None


def _get_manager() -> VectorStoreManager:
    global _manager_singleton
    if _manager_singleton is None:
        _manager_singleton = VectorStoreManager(
            base_dir=settings.VECTOR_STORE_DIR,
            default_dim=settings.EMBEDDING_DEFAULT_DIM,
            prefer_faiss=True,
        )
    return _manager_singleton


def _get_embedding() -> EmbeddingService:
    global _embedding_singleton
    if _embedding_singleton is None:
        _embedding_singleton = EmbeddingService.from_settings(settings)
    return _embedding_singleton
```

提供 6 个端点：

- **`GET /retrieval/status`**：全局索引状态，列出磁盘所有知识库索引，报告 FAISS/numpy 可用性。
- **`GET /retrieval/index/{kb_id}`**：单库索引状态（exists/loaded/consistent/backend/dim/total_vectors/nlist/nprobe/is_trained/ntotal/issues/path）。
- **`POST /retrieval/index/{kb_id}/flush`**：强制落盘。
- **`DELETE /retrieval/index/{kb_id}`**：删除索引（内存+磁盘）。
- **`POST /retrieval/index/clear-memory`**：释放所有内存索引（保留磁盘）。
- **`POST /retrieval/search/{kb_id}`**：文本→向量→top_k 搜索，过滤 min_score，返回带 metadata 的结果。

**关联关系：** 直接使用 `VectorStoreManager` 与 `EmbeddingService`（绕过 service 层），并通过 `app.processors` 导入 `_HAS_FAISS`/`_HAS_NUMPY` 探测标志。

---

### 4.10 `app/api/v1/embedding.py` —— 文本向量化接口

```python
_service_singleton: Optional[EmbeddingService] = None

def get_embedding_service() -> EmbeddingService:
    """获取 EmbeddingService 单例（带缓存）"""
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = EmbeddingService.from_settings(settings)
        ...
    return _service_singleton
```

提供 4 个端点：

- **`GET /embeddings/status`**：服务状态 + 质量速览（含样本相似度矩阵）。
- **`POST /embeddings/encode`**：批量编码，返回维度/L2 范数/前 5 维预览（不返回完整向量避免响应过大）+ 缓存统计。
- **`POST /embeddings/encode-single`**：单条编码。
- **`POST /embeddings/similarity`**：两文本余弦相似度 + 文字解释（`_interpret_similarity` 按分数段返回"高度相似/较相似/部分关联/较弱/几乎无关"）。

**关联关系：** 依赖 `EmbeddingService`、`CachingEmbeddingProvider`、`cosine_similarity`。

---

### 4.11 `app/api/v1/conversation.py` —— 对话历史管理

```python
class ConversationCreate(BaseModel):
    title: Optional[str] = "新对话"
    knowledge_base_id: Optional[int] = None

class ConversationItem(BaseModel):
    id: int
    title: str
    knowledge_base_id: Optional[int]
    message_count: int = 0
    updated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

class MessageItem(BaseModel):
    id: int
    role: str
    content: str
    metadata: Optional[dict] = None
    created_at: Optional[datetime] = None
```

端点：

- **`GET /conversation`**：列出当前用户所有会话（未登录返回 401 业务码）。
- **`POST /conversation`**：创建会话。
- **`GET /conversation/{conv_id}`**：获取会话完整消息历史。
- **`DELETE /conversation/{conv_id}`**：删除会话及其消息。

**说明：** 采用"业务码返回"而非抛 HTTPException 的风格（未登录返回 `success=False, code=401`）。延迟导入 `ConversationService` 避免循环依赖。

> **注意：** 该路由依赖的 `ConversationService` 内部存在 `Message` 导入 bug（见第 12 节），会导致此模块运行时失败。

---

### 4.12 `app/api/v1/integration.py` —— 外部渠道集成

面向 Shopify / 通用 Webhook 接入：

```python
class GenericChatRequest(BaseModel):
    query: str
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None
    use_agent: bool = False
    max_turns: int = 2
    history: Optional[list] = None
    metadata: Optional[Dict[str, Any]] = None
```

端点：

- **`POST /integration/generic/{kb_id}/chat`**：通用 HTTP 客服聊天。校验 kb 存在；根据 `use_agent` 选择 `AgentService`（多步推理）或 `RAGPipeline`（单次检索+生成）；异常时友好降级返回默认提示；最终通过 `IntegrationService.render_reply_for_channel(CHANNEL_SHOPIFY, reply)` 输出含 `message_html` 的 Shopify 友好格式。
- **`POST /integration/webhook/{token}`**：带签名 token 的 Webhook。`IntegrationService.verify_webhook_token` 校验 token 并解析出 (channel, kb_id)；按渠道选择 `parse_shopify_webhook` 或 `parse_generic_http` 解析入站消息；调用 RAG 生成回复；按渠道渲染输出。
- **`GET /integration/generate-token/{kb_id}`**：生成 Webhook token（可选校验用户对知识库的权限），返回带 Shopify 配置步骤说明。
- **`GET /integration/`**：根路径说明。

**关联关系：** 依赖 `IntegrationService`、`RAGPipeline`、`AgentService`、`EmbeddingService`、`VectorStoreManager`。注意它在 generic_chat 与 webhook 中每次都用 `tempfile.mkdtemp` 新建临时目录构造 `VectorStoreManager`，这是为隔离每次请求的向量状态，但实际 `VectorStoreManager.get_store` 会从全局 `VECTOR_STORE_DIR` 加载磁盘索引，因此临时目录仅影响"内存 store 缓存"位置（详见改进点）。

---

## 5. Service 层模块

### 5.1 `app/services/__init__.py`

空文件，包标记。

---

### 5.2 `app/services/kb_service.py` —— 知识库服务

```python
class KnowledgeBaseService:
    """知识库服务 - 封装所有知识库的业务逻辑"""

    def __init__(self, db: Session):
        self.db = db

    def create(self, payload: KnowledgeBaseCreate, user_id: Optional[int]) -> KnowledgeBase: ...
    def get_by_id(self, kb_id: int, user_id: Optional[int] = None) -> Optional[KnowledgeBase]: ...
    def list(self, user_id, page, page_size, keyword) -> Tuple[List[KnowledgeBase], int]: ...
    def update(self, kb_id, payload, user_id) -> Optional[KnowledgeBase]: ...
    def delete(self, kb_id, user_id) -> bool: ...
    def increment_documents(self, kb_id, delta=1) -> None: ...
    def increment_chunks(self, kb_id, delta=1) -> None: ...
```

**逐段说明：**

- **`create`**：构造 `KnowledgeBase` 对象，提交并刷新返回。
- **`get_by_id`**：按 id 查询；若传 `user_id` 则附加权限过滤（`user_id == 当前` OR `is_public`）。
- **`list`**：分页+关键词模糊搜索（name/description），权限过滤，按 `updated_at desc` 排序，返回 (items, total)。
- **`update`**：仅所有者可改，`payload.dict(exclude_unset=True)` 只更新传入字段。
- **`delete`**：仅所有者可删。
- **`increment_documents/increment_chunks`**：更新文档/分块计数，`max(0, ...)` 防负数。

**关联关系：** 被 `knowledge_base.py`(api) 依赖；`increment_*` 方法定义了但 `document_service` 实际直接操作 `kb.total_documents` 字段而非调用此方法（存在职责重复，见改进点）。

---

### 5.3 `app/services/document_service.py` —— 文档服务

这是最复杂的 service，整合 `DocumentProcessor + EmbeddingService + VectorStoreManager`。核心方法：

```python
class DocumentService:
    def __init__(self, db: Session):
        self.db = db
        self._processor = None
        self._embedding = None
        self._vector_manager = None

    @property
    def processor(self) -> DocumentProcessor:
        if self._processor is None:
            self._processor = DocumentProcessor(chunk_size=500, chunk_overlap=50)
        return self._processor

    @property
    def embedding(self) -> EmbeddingService:
        if self._embedding is None:
            self._embedding = EmbeddingService.from_settings(settings)
        return self._embedding

    @property
    def vector_manager(self) -> VectorStoreManager:
        if self._vector_manager is None:
            self._vector_manager = VectorStoreManager(
                base_dir=settings.VECTOR_STORE_DIR,
                default_dim=self.embedding.dim,
            )
        return self._vector_manager
```

- **懒加载属性**：processor/embedding/vector_manager 按需创建，避免构造时即触发外部依赖。

**`process_upload` 流程（核心）：**

1. `_check_kb_owner` 权限校验（必须是所有者）。
2. 检查扩展名，保存文件到 `UPLOAD_DIR/kb_{id}/` 下（uuid 前缀防冲突）。
3. `detect_file_type` + 建 Document 记录（status=processing），`flush` 获取 id。
4. `extract_text` 提取文本，超 50 万字符截断；文本过短标记 `skipped`。
5. 智能分块（用知识库配置或请求参数的 chunk_size/overlap）。
6. `embedding.encode` 批量向量化。
7. 建 Chunk 记录（vector_index 占位 -1）+ 组装 metadata → `store.add` 入向量索引 → 回填 `vector_index` → `vector_manager.save` 落盘。
8. 更新 Document status=processed、total_chunks；更新 KB 统计。
9. 异常时 status=error，记录错误信息到 content_text，重抛。

**`delete_document`**：删磁盘文件→删 chunks→删文档→更新 KB 统计→若该库有向量存储则删除并按剩余 chunks 重建索引（因 vector_index 全局递增，删除后需重建保证一致性）。

**`search_in_kb`**：权限校验→检查 chunk 数→获取/重建向量索引→编码查询→`store.search`→过滤 min_score→从 DB 反查 chunk 与 document 组装结果。

**`_rebuild_vector_index`**：从 DB 取该库所有 chunks，重新向量化并入索引，回填 vector_index，落盘。

**关联关系：** 被 `document.py`(api) 与 `retrieval_service._rebuild_if_needed`（通过延迟 import 避免循环）依赖。自身依赖 `DocumentProcessor`/`EmbeddingService`/`VectorStoreManager`。

---

### 5.4 `app/services/chat_service.py` —— RAG 流水线

定义数据结构与 `RAGPipeline`：

```python
@dataclass
class RetrievedChunk:
    chunk_id: int
    content: str
    score: float
    document_id: Optional[int] = None
    document_filename: Optional[str] = None
    def to_dict(self) -> Dict[str, Any]: ...

@dataclass
class RAGPipelineResult:
    query: str
    retrieved_chunks: List[RetrievedChunk] = field(default_factory=list)
    llm_answer: str = ""
    model: str = ""
    provider: str = ""
    latency_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None
    system_prompt: Optional[str] = None
```

**`SYSTEM_PROMPT_TEMPLATE`**：幻觉抑制 system prompt，要求 LLM 只依据【知识库片段】回答、无信息时明确说"未能检索到足够的相关信息"、用 `[来源 #N]` 标注引用。

**`RAGPipeline` 类：**

- **`search`**：向量搜索，多取 `top_k*2` 再按 min_score 过滤，返回 `RetrievedChunk` 列表。
- **`build_context`**：把 chunks 拼成带编号的 context 文本，受 `RAG_MAX_CONTEXT_CHARS` 限制。
- **`build_messages`**：组装 `[system, history..., user]`；无 context 时换用强拒绝 system prompt。
- **`answer`**：端到端 RAG。三层幻觉抑制：
  - L1：无 chunks 或最高分 < `REJECT_SCORE_THRESHOLD`(0.42) → 直接拒绝，不调 LLM。
  - L2：正常调 LLM 生成。
  - L3：LLM 返回空 → 替换为拒绝话术。
- **`answer_stream`**：生成器版流式，yield `retrieval_done`/`token`/`done`/`error` 事件，供 SSE 使用。

**关联关系：** 被 `chat.py`(api)、`integration.py`(api) 依赖。自身依赖 `get_llm_service`、`EmbeddingService`、`VectorStoreManager`。

---

### 5.5 `app/services/conversation_service.py` —— 对话历史服务

```python
class ConversationService:
    MAX_MESSAGES_PER_CONVERSATION = 200
    MAX_CONVERSATIONS_PER_USER = 50
```

- **容量限制**：每会话最多 200 条消息（超出删最早的）、每用户最多 50 个会话（超出删最旧的）。
- **`create_conversation`**：超限清理 + 建会话，title 截断 80 字符。
- **`list_conversations`**：列表，每项含 message_count（子查询计数）。
- **`append_user_message`/`append_assistant_message`**：追加消息；user 消息在会话前 2 条且 title 为默认时自动用内容前 30 字更新 title。
- **`get_messages`**：按 id 升序取消息，解析 `retrieved_contexts` JSON 为 metadata。
- **`get_llm_context`**：取最近 `max_turns*2` 条作为 LLM 上下文 `ChatMessage` 列表。

> **关键 bug：** 文件顶部 `from app.models.entities.conversation import Conversation, Message` —— 实体模块只导出 `Conversation` 和 `ChatMessageRecord`，不存在 `Message`。这会导致 `ImportError`，使整个 `conversation_service` 不可用，进而 `api/v1/conversation.py` 也会失败。

**关联关系（设计意图）：** 被 `api/v1/conversation.py` 依赖；设计上希望被 `chat_service` 用作历史持久化，但当前 chat 路由直接用请求中的 history，未集成此服务。

---

### 5.6 `app/services/retrieval_service.py` —— 混合检索服务

提供向量+BM25+关键词三路混合重排：

```python
@dataclass
class RetrievedHit:
    chunk_id: int
    document_id: int
    knowledge_base_id: int
    content: str
    document_filename: str
    vector_score: float
    bm25_score: float
    keyword_score: float
    final_score: float
    rank: int
```

**`BM25Index`**：纯 Python Okapi BM25 实现。

- `tokenize`：英文按词、中文按 2-char 窗口（短文本加单字），去停用词。
- `add_doc`：增量添加，维护 df/idf，重算 idf。
- `score`：对候选集打原始 BM25 分。
- `score_normalized`：归一化到 [0,1]。

**`RetrievalService`：**

- 权重：`VECTOR_WEIGHT=0.50`、`BM25_WEIGHT=0.35`、`KEYWORD_WEIGHT=0.15`。
- **`search`** 主流程：
  1. 权限校验。
  2. 获取/重建向量索引。
  3. 查询向量化。
  4. 向量粗搜索取 `top_k*5` 候选。
  5. BM25 对候选重打分 + 补充 DB 中其他高 BM25 分 chunk（阈值 0.15）。
  6. 关键词重叠分（Jaccard 风格）。
  7. 三路加权得 final_score。
  8. 排序 + min_score 过滤。
  9. 可选合并高重叠 chunk（阈值 0.7）。
  10. 组装 `RetrievedHit`。
- **`_get_or_build_bm25`**：按 kb 懒加载 BM25 索引，10 分钟重建周期。
- **`_extract_keywords`/`_keyword_overlap_score`**：极简中英文关键词提取与重叠打分。
- **`_merge_overlapping`/`_text_overlap`**：基于字符集合 Jaccard 合并冗余 chunk。
- **`_rebuild_if_needed`**：向量索引空但 DB 有 chunk 时，延迟 import `DocumentService` 重建。
- **`get_kb_stats`/`rebuild_index`**：管理接口。

**关联关系：** 被 `agent_service.SearchKBTool` 依赖。自身依赖 `EmbeddingService`、`VectorStoreManager`，并通过延迟 import 依赖 `DocumentService`（避免循环）。

---

### 5.7 `app/services/agent_service.py` —— ReAct Agent

定义工具体系与 ReAct 循环：

```python
@dataclass
class ToolResult:
    success: bool
    content: str
    error: Optional[str] = None
    raw: Optional[Any] = None

class BaseTool:
    name: str = "base_tool"
    description: str = "基类，请勿直接使用"
    def run(self, args, context) -> ToolResult: raise NotImplementedError

class SearchKBTool(BaseTool):
    name = "search_kb"
    description = "在指定知识库中进行语义搜索..."
    def __init__(self, db): self.db = db; self.retrieval = RetrievalService(db)
    def run(self, args, context) -> ToolResult: ...

class GetDocTool(BaseTool):
    name = "get_doc"
    description = "获取指定文档的完整内容..."
    def run(self, args, context) -> ToolResult: ...
```

- **`SearchKBTool`**：调 `RetrievalService.search`，格式化结果（含 vec/bm25 分数）。
- **`GetDocTool`**：按 document_id 查全文（拼接 chunks，截断 2000 字）。

**`AgentService`：**

- **`SYSTEM_PROMPT`**：ReAct 风格，解释 Thought/Action/Observation 循环，含 `$TOOL_LIST`/`$MAX_TURNS` 占位符（用 `replace` 替换避免与 JSON 花括号冲突）。
- **`run`** 主循环：
  1. 构造工具描述、系统 prompt、历史（最多 10 条）、首轮 user prompt。
  2. 循环 `max_turns` 次：调 LLM → 解析 Thought/Action/Action Input → 若 `Final Answer` 则结束 → 否则执行工具，把 Observation 作为 user message 追加。
  3. 循环结束仍无答案 → 追加 summary 消息让 LLM 总结。
- **`_parse_agent_output`**：正则解析 `Thought:`/`Action:`/`Action Input:`，兼容中英文冒号；完全无 Action 且文本短则视为直接回答。

**关联关系：** 被 `agent.py`(api)、`integration.py`(api) 依赖。自身依赖 `RetrievalService`、`get_llm_service`、`ChatMessage`。

---

### 5.8 `app/services/integration_service.py` —— 外部渠道集成服务

```python
CHANNEL_SHOPIFY = "shopify"
CHANNEL_GENERIC = "generic_http"
CHANNEL_WECHAT = "wechat"
CHANNEL_SLACK = "slack"
CHANNEL_CUSTOM = "custom"
VALID_CHANNELS = {CHANNEL_SHOPIFY, CHANNEL_GENERIC, CHANNEL_WECHAT, CHANNEL_SLACK, CHANNEL_CUSTOM}

@dataclass
class InboundMessage: ...   # 统一入站消息
@dataclass
class OutboundReply: ...     # 统一回复
```

**`IntegrationService`：**

- **`generate_webhook_token(channel, kb_id, salt)`**：生成 `{channel}_{kb_id}_{sig24}` 格式 token，sig 基于 `channel|kb_id|salt|day_key` 的 sha256 前 24 位，day_key 按天变化。
- **`verify_webhook_token(token)`**：从右拆分解析（因 channel 可能含下划线），校验 channel 合法性，容灾支持多个 salt + today/yesterday 两个 day_key（防深夜跨天）。
- **`parse_generic_http`**：宽容解析通用 webhook（兼容 query/message/text/content/msg 多字段）。
- **`parse_shopify_webhook`**：解析 Shopify 消息（兼容 customer 字段、多种 header），HMAC 仅记录不强制校验。
- **`render_reply_for_channel`**：按渠道渲染。`_render_shopify` 生成可直接嵌入 Liquid 的 `message_html`（含 HTML 转义 + 来源列表），`_render_generic` 返回纯 JSON。

**关联关系：** 被 `integration.py`(api) 依赖。

---

## 6. Processor 层模块

### 6.1 `app/processors/__init__.py` —— 总出口

集中 re-export 所有 processor 子模块的公共 API：

```python
from .document.document_processor import DocumentProcessor, SUPPORTED_EXTENSIONS
from .document.markdown_parser import (MarkdownBlock, ParsedMarkdown, MarkdownParser, parse_markdown)
from .document.semantic_chunker import SemanticChunker, chunk_text
from .document.document_pipeline import (DocumentChunk, ProcessedDocument, DocumentPipeline)
from .embedding.embedding_service import (BaseEmbeddingProvider, MockEmbeddingProvider, RemoteAPIEmbeddingProvider, EmbeddingService)
from .retrieval.vector_store import (BaseVectorStore, FAISSVectorStore, IVFVectorStore, PurePythonVectorStore, VectorStoreManager, _HAS_FAISS, _HAS_NUMPY)
from .llm.llm_service import (BaseLLMProvider, MockLLMProvider, HTTPLLMProvider, LLMService, ChatMessage, ChatResult, get_llm_service)
```

**说明：** 这是 processor 包的"门面"，外部模块统一 `from app.processors import ...` 即可。注意它未导出 `LocalNumpyEmbeddingProvider` 与 `CachingEmbeddingProvider`，但 `embedding.py`(api) 直接从子模块路径导入这两个类。

---

### 6.2 `app/processors/document/__init__.py`

re-export document 子模块的公共 API，与总出口的 document 部分一致。

---

### 6.3 `app/processors/document/document_processor.py` —— 文本提取与分块

```python
SUPPORTED_EXTENSIONS = {".txt", ".text", ".md", ".markdown", ".pdf", ".doc", ".docx", ".rtf", ".html", ".htm", ".json", ".csv", ".log"}

class DocumentProcessor:
    def __init__(self, chunk_size=500, chunk_overlap=50): ...
    @staticmethod
    def detect_file_type(filepath) -> str: ...
    @staticmethod
    def is_supported(filepath) -> bool: ...
    def extract_text(self, filepath, file_type=None) -> str: ...
    def split_chunks(self, text, chunk_size=None, chunk_overlap=None) -> List[Dict[str, Any]]: ...
```

**逐段说明：**

- **`detect_file_type`**：扩展名→类型映射（text/markdown/pdf/word/rich_text/html/json/csv/log）。
- **`extract_text`**：按类型分发：
  - `_extract_text_plain`：UTF-8 优先，失败回退 GBK。
  - `_extract_text_pdf`：先试 `pypdf`，再试 `pdfplumber`，都失败则正则去 PDF 二进制标记（基础回退）。
  - `_extract_text_word`：试 `python-docx`（含表格），失败提示转换。
  - `_extract_text_html`：试 `BeautifulSoup`，失败正则去标签。
  - `_extract_text_json`：格式化 JSON 文本。
  - `_extract_text_csv`：行用 `|` 拼接。
  - `_clean_text`：统一换行、去控制字符、合并空行/空格。
- **`split_chunks`**：段落→句子分段，短段合并、长段切句，保留 overlap，返回 `[{index, content, char_count}]`。
- 辅助：`_split_paragraphs`、`_split_sentences`（中英文标点切分）、`_split_long_text`（暴力切分）。

**设计意图：** 不强依赖大型库（pdfplumber/docx/bs4 可选），缺库时优雅降级。

**关联关系：** 被 `DocumentService` 与 `DocumentPipeline` 依赖。

---

### 6.4 `app/processors/document/markdown_parser.py` —— Markdown 结构化解析

```python
BLOCK_TYPES = ("heading", "paragraph", "code", "list", "table", "quote", "thematic")

@dataclass
class MarkdownBlock:
    block_type: str
    content: str
    raw: str
    level: int = 0
    meta: dict = field(default_factory=dict)
    @property
    def char_count(self) -> int: return len(self.content)

@dataclass
class ParsedMarkdown:
    blocks: List[MarkdownBlock]
    original_text: str
    @property
    def total_chars(self) -> int: ...
    @property
    def total_blocks(self) -> int: ...
    def summary(self) -> str: ...
```

**`MarkdownParser`：**

- 一组正则：`RE_HEADING`/`RE_CODE_FENCE`/`RE_LIST_ITEM`/`RE_TABLE_ROW`/`RE_TABLE_SEP`/`RE_QUOTE`/`RE_THEMATIC`。
- **`parse(text)`**：逐行状态机解析。优先级：空行→分隔线→标题→代码块→引用→表格→列表→普通段落。每类块保留 `raw`（原始 Markdown）与 `content`（规范化文本）+ meta（标题层级/代码语言/列表项数/表头等）。
- **`_split_cells`**：表格行切单元格。
- **`_strip_inline_format`**：剥离行内粗体/斜体/删除线/行内代码/链接/图片。
- **`to_plain_text`**：解析后拼接纯文本。

**`parse_markdown(text)`**：便捷函数。

**设计原则**（注释）：容错（不规范 Markdown 退化为段落）、纯函数（无副作用）、信息保留（保留 raw + content）。

**关联关系：** 被 `SemanticChunker` 依赖。

---

### 6.5 `app/processors/document/semantic_chunker.py` —— 语义感知分块

```python
class SemanticChunker:
    _SENT_SPLIT = re.compile(r"([。！？.!?;；\n])")

    def __init__(self, max_chars=500, min_chars=100, overlap=50, respect_code_blocks=True, respect_tables=True):
        ...
        self._md_parser = MarkdownParser()

    def split_text(self, text) -> List[Dict[str, Any]]: ...
    def split_parsed(self, parsed) -> List[Dict[str, Any]]: ...
    def _split_plain_text(self, text) -> List[Dict[str, Any]]: ...
```

**逐段说明：**

- 构造时 clamp 参数，overlap 过大时自动调整为 `max_chars//4`。
- **`split_text`**：先用 `MarkdownParser` 解析，判断是否有特殊结构块或足够多的 block → 走 `split_parsed`；否则走 `_split_plain_text`。
- **`split_parsed`**：
  - 标题 H1/H2 开启新 chunk（flush 上一段）。
  - 代码块/表格整体保留（过长则单独成块）。
  - 引用/列表/段落按 `_append_with_boundary` 句子边界追加。
  - 最后 flush + `_merge_short_chunks` + `_apply_overlap`。
- **`_append_with_boundary`**：按句子切分（保留标点），装满当前 chunk 后 flush，超长单句允许超出 max_chars。
- **`_merge_short_chunks`**：合并 `< min_chars/2` 的 chunk 到相邻。
- **`_apply_overlap`**：相邻 chunk 头部加前一个 chunk 尾部 overlap 字符。

**`chunk_text(text, ...)`**：便捷函数。

**关联关系：** 被 `DocumentPipeline` 依赖。

---

### 6.6 `app/processors/document/document_pipeline.py` —— 纯逻辑文档管道

```python
@dataclass
class DocumentChunk:
    index: int
    content: str
    char_count: int
    vector_index: int = -1
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ProcessedDocument:
    document_id: str
    filename: str
    file_type: str
    file_size: int
    total_chars: int
    total_chunks: int
    chunks: List[DocumentChunk] = field(default_factory=list)
    processing_time_ms: float = 0.0
    status: str = "processed"
    error_message: str = ""
    @property
    def success(self) -> bool: return self.status == "processed"
```

**`DocumentPipeline`：**

- 构造时可注入 processor/chunker/embedding/vector_store_dir，默认自建。
- **`process(filename, text, kb_id)`**：最简便捷入口，转调 `process_text`。
- **`process_file(kb_id, file_path, file_type, skip_vectorize)`**：从磁盘读文件。
- **`process_text(kb_id, text, filename, file_type, skip_vectorize)`**：处理内存文本。
- **`process_bytes(kb_id, content, filename, ...)`**：写字节到临时文件再处理，finally 删临时文件（API 上传场景）。
- **`_process_text_core`**：核心逻辑——空文本 skip → `chunker.split_text`（失败回退 `_fallback_split`）→ 过滤太短 chunk → 向量化+入库（`store.add` + `vector_manager.save`）→ 回填 vector_index → 构造 `ProcessedDocument`。
- **`_fallback_split`**：字符级切分，尽量在标点对齐。
- **`process_batch`**：批量处理。

**设计意图：** 与 DB 解耦的纯逻辑层，便于单测与轻量部署。

**关联关系：** 依赖 `DocumentProcessor`、`SemanticChunker`、`EmbeddingService`、`VectorStoreManager`。注意 `DocumentService`（业务层）并未直接使用 `DocumentPipeline`，而是自行组装 processor/embedding/vector_manager——存在职责重复（见改进点）。

---

### 6.7 `app/processors/embedding/__init__.py`

```python
from __future__ import annotations

"""Embedding处理 - Day 4 实现"""
```

仅注释，无实际导出（embedding 子模块通过总出口 `app.processors` 导出）。

---

### 6.8 `app/processors/embedding/embedding_service.py` —— 向量化服务

公共工具：

```python
def normalize_vec(v) -> List[float]: ...      # L2 归一化（纯 Python）
def cosine_similarity(a, b) -> float: ...      # 余弦相似度（纯 Python）
def validate_vectors(vectors, expected_dim) -> None: ...  # 维度+有限值校验，失败抛 InternalError
```

**Provider 层级：**

- **`BaseEmbeddingProvider`**：基类，定义 `encode`/`encode_single`/`dim`/`name`，`_post_process` 统一校验+归一化。
- **`MockEmbeddingProvider`**：基于 hash+ngram 累加的确定性伪随机向量。提取 char-2gram/3gram/token，每个 ngram hash 映射到固定维度累加权重，再加少量 hash 噪声防退化，L2 归一化。相似文本因共享 ngram 会在同维度累加→相似向量。
- **`LocalNumpyEmbeddingProvider`**：字符 n-gram + 随机投影。有 numpy 走 `_encode_fast`（矩阵运算），无 numpy 走 `_encode_slow`（纯 Python）。
- **`RemoteAPIEmbeddingProvider`**：OpenAI 兼容 Embedding API。按 `batch_size` 切分，`_fetch_batch` 带重试（指数退避），自动维度检测，结果归一化校验。依赖 `httpx`。
- **`CachingEmbeddingProvider`**：LRU 缓存装饰器，`OrderedDict` 实现，sha256 文本作 key，命中 move_to_end，超限 popitem。提供 `stats`/`reset_stats`/`clear`。

**`EmbeddingService`**（门面）：

- 构造时可选叠加 `CachingEmbeddingProvider`。
- **`from_settings(settings)`**：根据配置选 provider——有 `EMBEDDING_API_URL` 用 `RemoteAPIEmbeddingProvider`，否则用 `LocalNumpyEmbeddingProvider`。
- `encode`/`encode_single`/`encode_batch`/`encode_to_numpy`。
- **`quality_report`**：用样本文本生成质量报告（余弦相似度矩阵、范数、维度检查、有限值检查）。

**关联关系：** 被 `DocumentService`、`RetrievalService`、`RAGPipeline`、`retrieval.py`(api)、`embedding.py`(api)、`integration.py`(api) 依赖。

---

### 6.9 `app/processors/llm/__init__.py`

re-export `llm_service` 的公共 API。

---

### 6.10 `app/processors/llm/llm_service.py` —— LLM 服务

数据结构：

```python
@dataclass
class ChatMessage:
    role: str
    content: str
    def to_dict(self) -> Dict[str, Any]: ...
    @classmethod
    def from_dict(cls, d) -> "ChatMessage": ...

@dataclass
class ChatResult:
    content: str
    model: str
    provider: str
    success: bool = True
    error: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    latency_ms: float = 0.0
```

**`BaseLLMProvider`**：基类，`threading.RLock` 保护；`chat` 必须重写，`chat_stream` 默认调非流式一次性产出；`count_tokens` 粗略估算（≈4 字符/token）。

**`MockLLMProvider`**：不联网的规则引擎。

- `_parse_chunks`：从 system prompt 中解析 `[#N]` 标记的 chunks（支持带来源元信息）。
- `_extract_keywords`：中英文关键词提取，去停用词。
- `_relevance_score`：chunk 与 query 关键词命中率。
- `chat`：三层幻觉抑制——system 说无内容→拒绝；无 chunks→拒绝；最高相关性 0→拒绝；否则取最相关 1-2 条 chunk 前 80 字摘要 + `[来源 #N]` 拼成回答。
- `chat_stream`：调 `chat` 后按词拆分模拟流式（每 2-4 token 一段，`time.sleep(0.02)` 控节奏）。

**`HTTPLLMProvider`**：OpenAI 兼容协议，DeepSeek/OpenAI/Custom 共用。

- `__init__`：建 `requests.Session`（缺 requests 时 session=None）。
- `chat`：`_call_with_retry` 带重试，仅 429/5xx 重试，payload 含 model/messages/temperature/max_tokens/top_p/stream=False。
- `chat_stream`：真 SSE，`stream=True` + `iter_lines` 解析 `data: {...}`，逐 token yield `ChatResult`，遇 `[DONE]` 结束。

**`LLMService`**：工厂。`_build_provider` 按 `LLM_PROVIDER` 配置+对应 API Key 选择 `HTTPLLMProvider`，否则回退 `MockLLMProvider`。

**`get_llm_service()`**：全局单例，双重检查锁。

**关联关系：** 被 `RAGPipeline`、`AgentService`、`chat.py`(api) 依赖。注意 `HTTPLLMProvider` 用 `requests` 但未在 `requirements.txt` 声明（改进点）。

---

### 6.11 `app/processors/retrieval/__init__.py`

```python
from __future__ import annotations

"""检索处理 - Day 5 实现"""
```

仅注释。

---

### 6.12 `app/processors/retrieval/vector_store.py` —— 向量存储

依赖探测：

```python
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

try:
    import faiss
    _HAS_FAISS = True
except ImportError:
    _HAS_FAISS = False
```

工具：`_is_valid_vector`、`_normalize_vectors`（numpy 或纯 Python）。

**`BaseVectorStore`**：基类。

- `add(vectors, metadata)`：校验维度，追加，分配递增 index，调 `_on_vectors_added` 让子类更新索引结构。
- `search(query_vector, top_k)`：抽象。
- `get_metadata`/`get_vector`/`total`/`stats`/`check_consistency`。
- `_save_meta`/`_load_meta`：pickle 序列化 dim/next_index/vectors/metadata。
- `save`/`load`：加锁。

**`FAISSVectorStore`**：`IndexFlatIP`（内积）精确搜索。

- `_on_vectors_added`：增量 add 新向量（归一化后）。
- `search`：查询归一化→`index.search`→内积映射到 [0,1]（`(cos+1)/2`）。
- `save`/`load`：FAISS 索引单独存 `.faiss`，元数据 pickle；加载时若 `.faiss` 缺失则从 vectors 重建。

**`IVFVectorStore`**：`IndexIVFFlat` 倒排索引，大规模库用。

- `_train`：样本数≥nlist*5 时训练，每簇至少 5 个。
- `_on_vectors_added`：未训练累计到 buffer，足够后训练；已训练直接 add。
- `search`：未训练回退线性扫描，训练后用 IVF 搜索。
- `save`/`load`：额外存 `.state.json`（trained/nlist/nprobe）。

**`PurePythonVectorStore`**：numpy/纯 Python 回退。

- `_on_vectors_added`：增量归一化缓存。
- `search`：numpy 矩阵乘或纯 Python 循环，argsort 取 top_k。

**`VectorStoreManager`**：按 kb 管理多 store。

- `_get_store_path`：`kb_{id}.vecstore`。
- `_pick_backend`：优先 faiss flat，无 faiss 用 pure。
- `get_store`：内存缓存→磁盘加载→新建。
- `save`/`save_all`/`delete`/`has_store`/`clear_memory`/`list_stored_kbs`/`get_status`。
- `bulk_add`/`bulk_search`：批量。

**关联关系：** 被 `DocumentService`、`RetrievalService`、`RAGPipeline`、`retrieval.py`(api)、`integration.py`(api) 依赖。是整个检索能力的底座。

---

## 7. 文件分类说明

### 7.1 配置文件
- `requirements.txt`：Python 依赖清单。
- `.env.example`：环境变量模板。
- `pytest.ini`：pytest 配置。
- `app/core/config.py`：运行时配置类（pydantic BaseSettings）。

### 7.2 脚本文件
- `start.py`：启动脚本，修正 sys.path 后拉起 uvicorn。

### 7.3 入口文件
- `app/main.py`：FastAPI 应用工厂 `create_app()`，装配中间件、异常处理器、路由、启动/关闭事件。
- `start.py`：进程入口。

### 7.4 核心业务文件
- `app/services/document_service.py`：文档上传→分块→向量化→索引全流程。
- `app/services/chat_service.py`：RAG 流水线（检索→上下文→LLM→幻觉抑制）。
- `app/services/retrieval_service.py`：向量+BM25+关键词三路混合检索。
- `app/services/agent_service.py`：ReAct Agent 推理循环。
- `app/services/integration_service.py`：外部渠道接入与适配。
- `app/services/kb_service.py`：知识库 CRUD。
- `app/services/conversation_service.py`：对话历史持久化（注：存在导入 bug）。

### 7.5 工具文件
- `app/core/security.py`：密码哈希 + JWT。
- `app/core/logging.py`：统一日志。
- `app/core/exceptions.py`：异常体系 + 全局处理器。
- `app/processors/document/document_processor.py`：文本提取与分块。
- `app/processors/document/markdown_parser.py`：Markdown 解析。
- `app/processors/document/semantic_chunker.py`：语义分块。
- `app/processors/document/document_pipeline.py`：纯逻辑文档管道。
- `app/processors/embedding/embedding_service.py`：向量化 provider 体系。
- `app/processors/llm/llm_service.py`：LLM provider 体系。
- `app/processors/retrieval/vector_store.py`：向量存储体系。
- `app/models/response.py`：统一响应包装。
- `app/models/schemas.py`：Pydantic 模型集合。

---

## 8. 项目运行入口与初始化流程

完整启动链路：

1. **`start.py`** 执行：
   - 把 venv `site-packages` 与 `BACKEND_DIR` 插入 `sys.path`。
   - `os.chdir(BACKEND_DIR)`。
   - `from app.main import app` 触发 `app/main.py` 模块加载。

2. **`app/main.py`** 模块加载：
   - 执行 `app = create_app()`。
   - `create_app()` 内：
     - `settings.ensure_dirs()` 创建 `UPLOAD_DIR`/`VECTOR_STORE_DIR`。
     - 构造 `FastAPI` 实例。
     - 注册 CORS 中间件、全局异常处理器。
     - `include_router(health.router)` 挂载 `/health`。
     - `include_router(api_router, prefix="/api")` 挂载 `/api/v1/...`。
     - 注册 startup/shutdown 事件。
     - 定义根路由 `GET /`。
   - 返回 `app`。

3. **`uvicorn.run(app, ...)`** 启动 ASGI 服务。

4. **首个请求前触发 startup 事件**：
   - `from app.models.database import init_db`。
   - `init_db()`：
     - `import app.models.entities` 触发所有实体类加载，注册到 `Base.metadata`。
     - `Base.metadata.create_all(bind=engine)` 建表（若不存在）。
   - 失败仅记日志不中断。

5. **请求处理**：
   - 经 CORS 中间件 → 路由匹配 → 依赖注入（`get_db_dep` 建会话、`get_current_user*` 解 token）→ 路由函数 → 调 Service → 调 Processor → 返回 `ApiResponse`。
   - 异常被全局处理器捕获，统一返回 `ErrorResponse`。

6. **关闭**：触发 shutdown 事件，记录日志。

最终路由前缀映射：
- `GET /` → root
- `GET /health`、`GET /health/ping`
- `/api/v1/auth/*`、`/api/v1/knowledge-bases/*`、`/api/v1/knowledge-bases/{kb_id}/documents/*`、`/api/v1/chat/*`、`/api/v1/agent/*`、`/api/v1/retrieval/*`、`/api/v1/embeddings/*`、`/api/v1/conversation/*`、`/api/v1/integration/*`

---

## 9. 关键公共方法、类、函数用途详解

### 9.1 配置与安全
| 名称 | 位置 | 用途 |
|---|---|---|
| `Settings` | `core/config.py` | 全局配置，pydantic BaseSettings，支持 .env 覆盖 |
| `settings` | `core/config.py` | 全局单例 |
| `hash_password`/`verify_password` | `core/security.py` | PBKDF2 密码哈希与验证 |
| `create_jwt_token`/`decode_jwt_token` | `core/security.py` | 手写 JWT 签发与校验 |
| `create_access_token`/`extract_user_from_token` | `core/security.py` | 业务层 token 封装 |
| `setup_logger`/`logger` | `core/logging.py` | 统一日志（控制台+滚动文件） |
| `RAGBaseException` 及子类 | `core/exceptions.py` | 业务异常体系 |
| `global_exception_handler`/`validation_exception_handler` | `core/exceptions.py` | 全局异常处理器 |

### 9.2 数据库与模型
| 名称 | 位置 | 用途 |
|---|---|---|
| `engine`/`SessionLocal`/`Base` | `models/database.py` | SQLAlchemy 引擎/会话工厂/基类 |
| `get_db`/`get_db_dep` | `models/database.py`/`dependencies.py` | DB 会话依赖注入 |
| `init_db` | `models/database.py` | 建表 |
| `ApiResponse[T]` | `models/response.py` | 统一响应包装 |
| `User`/`KnowledgeBase`/`Document`/`DocumentChunk`/`Conversation`/`ChatMessageRecord` | `models/entities/*` | ORM 实体 |

### 9.3 依赖注入
| 名称 | 位置 | 用途 |
|---|---|---|
| `get_current_user` | `api/dependencies.py` | 强制认证依赖 |
| `get_current_user_optional` | `api/dependencies.py` | 可选认证依赖 |
| `get_current_admin` | `api/dependencies.py` | 管理员依赖 |

### 9.4 Service 层
| 名称 | 位置 | 用途 |
|---|---|---|
| `KnowledgeBaseService` | `services/kb_service.py` | 知识库 CRUD + 计数 |
| `DocumentService.process_upload` | `services/document_service.py` | 文档上传全流程 |
| `DocumentService.search_in_kb` | `services/document_service.py` | 知识库语义搜索 |
| `RAGPipeline.answer`/`answer_stream` | `services/chat_service.py` | RAG 端到端（含幻觉抑制） |
| `RetrievalService.search` | `services/retrieval_service.py` | 三路混合检索 |
| `BM25Index` | `services/retrieval_service.py` | 纯 Python BM25 |
| `AgentService.run` | `services/agent_service.py` | ReAct 推理循环 |
| `IntegrationService.generate_webhook_token`/`verify_webhook_token` | `services/integration_service.py` | Webhook token 签发与校验 |
| `ConversationService` | `services/conversation_service.py` | 对话历史持久化（注：有 bug） |

### 9.5 Processor 层
| 名称 | 位置 | 用途 |
|---|---|---|
| `DocumentProcessor` | `processors/document/document_processor.py` | 文本提取+分块 |
| `MarkdownParser`/`parse_markdown` | `processors/document/markdown_parser.py` | Markdown 结构化解析 |
| `SemanticChunker`/`chunk_text` | `processors/document/semantic_chunker.py` | 语义感知分块 |
| `DocumentPipeline` | `processors/document/document_pipeline.py` | 纯逻辑文档处理管道 |
| `EmbeddingService` | `processors/embedding/embedding_service.py` | 向量化门面 |
| `MockEmbeddingProvider`/`LocalNumpyEmbeddingProvider`/`RemoteAPIEmbeddingProvider`/`CachingEmbeddingProvider` | `processors/embedding/embedding_service.py` | 向量化 provider |
| `normalize_vec`/`cosine_similarity`/`validate_vectors` | `processors/embedding/embedding_service.py` | 向量工具 |
| `LLMService`/`get_llm_service` | `processors/llm/llm_service.py` | LLM 门面与单例 |
| `MockLLMProvider`/`HTTPLLMProvider` | `processors/llm/llm_service.py` | LLM provider |
| `ChatMessage`/`ChatResult` | `processors/llm/llm_service.py` | LLM 消息与结果 |
| `FAISSVectorStore`/`IVFVectorStore`/`PurePythonVectorStore` | `processors/retrieval/vector_store.py` | 向量存储实现 |
| `VectorStoreManager` | `processors/retrieval/vector_store.py` | 多库向量存储管理 |

---

## 10. 重要依赖与被依赖关系分析

### 10.1 模块依赖图（自顶向下）

```
start.py
  └─ app/main.py
       ├─ app/core/config.py (settings)
       ├─ app/core/logging.py (logger)
       ├─ app/core/exceptions.py (handlers)
       ├─ app/api/health.py
       │    └─ app/models/database.py (engine)
       └─ app/api/v1/__init__.py (api_router)
            ├─ auth.py ─→ dependencies, security, entities.user, schemas, response
            ├─ knowledge_base.py ─→ kb_service
            ├─ document.py ─→ document_service
            ├─ chat.py ─→ chat_service.RAGPipeline, embedding, vector_store
            ├─ agent.py ─→ agent_service
            ├─ retrieval.py ─→ vector_store, embedding_service (直接用 processor)
            ├─ embedding.py ─→ embedding_service
            ├─ conversation.py ─→ conversation_service (注:有 bug)
            └─ integration.py ─→ integration_service, chat_service, agent_service, processors
```

### 10.2 关键依赖链

- **文档上传链**：`document.py` → `DocumentService` → `DocumentProcessor` + `EmbeddingService` + `VectorStoreManager` → `models/entities`。
- **RAG 对话链**：`chat.py` → `RAGPipeline` → `EmbeddingService` + `VectorStoreManager` + `get_llm_service`。
- **Agent 链**：`agent.py` → `AgentService` → `RetrievalService` + `get_llm_service` → `EmbeddingService` + `VectorStoreManager` + `DocumentService`(延迟 import 重建索引)。
- **集成链**：`integration.py` → `IntegrationService` + `RAGPipeline`/`AgentService` + `EmbeddingService` + `VectorStoreManager`。

### 10.3 被依赖最多的组件（高扇入）

1. `settings` (`core/config.py`)：几乎被所有模块依赖。
2. `logger` (`core/logging.py`)：被大部分 service/api/processor 依赖。
3. `ApiResponse` (`models/response.py`)：被所有 api 路由依赖。
4. `EmbeddingService`：被 document/chat/retrieval/integration API 与多个 service 依赖。
5. `VectorStoreManager`：同上。
6. `get_db_dep`/`get_current_user*`：被所有需认证/DB 的路由依赖。

### 10.4 对外依赖（外部库）

| 库 | 用途 | 使用方 |
|---|---|---|
| `fastapi`/`uvicorn` | Web 框架/ASGI | main.py, api/* |
| `sqlalchemy` | ORM | models/*, services/* |
| `pydantic`(v1) | 数据校验 | config, schemas, response, exceptions |
| `python-multipart` | 文件上传 | document.py |
| `httpx` | 远程 Embedding API | RemoteAPIEmbeddingProvider |
| `numpy` | 向量计算 | embedding_service, vector_store |
| `faiss-cpu` | ANN 搜索 | FAISSVectorStore, IVFVectorStore |
| `requests` | LLM HTTP 调用 | HTTPLLMProvider（**未声明在 requirements**） |
| `pypdf`/`pdfplumber` | PDF 提取 | DocumentProcessor（可选） |
| `python-docx` | Word 提取 | DocumentProcessor（可选） |
| `beautifulsoup4` | HTML 提取 | DocumentProcessor（可选） |

---

## 11. 模块调用链

### 11.1 用户注册/登录调用链

```
客户端 POST /api/v1/auth/register
  → auth.register(payload, db)
    → db.query(User) 查重
    → hash_password(payload.password)
    → db.add(User) + commit + refresh
    → create_access_token(user.id, user.username)
      → create_jwt_token(payload)  # 手写 JWT
    → return ApiResponse[TokenData]
```

### 11.2 文档上传与向量化调用链

```
客户端 POST /api/v1/knowledge-bases/{kb_id}/documents (带 Bearer token)
  → document.upload_document
    → get_current_user → extract_user_from_token → db.query(User)
    → DocumentService.process_upload
      → _check_kb_owner (权限)
      → 保存文件到 UPLOAD_DIR/kb_{id}/
      → DocumentProcessor.detect_file_type + extract_text
      → DocumentProcessor.split_chunks
      → EmbeddingService.encode (向量化)
      → VectorStoreManager.get_store → store.add(vectors, metadata)
      → VectorStoreManager.save (落盘)
      → db: 建 DocumentChunk + 更新 Document + 更新 KnowledgeBase 统计
    → return DocumentUploadResponse
```

### 11.3 RAG 对话调用链

```
客户端 POST /api/v1/chat/message
  → chat.chat_message(payload)
    → _get_pipeline() (单例 RAGPipeline)
    → RAGPipeline.answer(kb_id, query, history, ...)
      → search: EmbeddingService.encode_single + VectorStoreManager.get_store + store.search
      → 幻觉抑制 L1: 无 chunks 或 max_score<0.42 → 拒绝
      → build_context (拼带编号的 context)
      → build_messages ([system, history..., user])
      → get_llm_service().chat (MockLLMProvider 或 HTTPLLMProvider)
      → 幻觉抑制 L3: 空回答 → 拒绝
    → return ChatResponse
```

### 11.4 Agent 推理调用链

```
客户端 POST /api/v1/agent/run
  → agent.agent_run
    → AgentService(db).run(query, kb_id, max_turns, history)
      → 构造 SYSTEM_PROMPT (含 $TOOL_LIST)
      → 循环 max_turns:
          → llm.chat(messages)
          → _parse_agent_output (Thought/Action/Action Input)
          → if Final Answer: 结束
          → tools[action].run(args, context)
              SearchKBTool → RetrievalService.search (向量+BM25+关键词三路混合)
                → EmbeddingService.encode_single (查询向量化)
                → VectorStoreManager.get_store → store.search (向量粗召回)
                → BM25Index.score_normalized (BM25 重排)
                → _keyword_overlap_score (关键词重叠分)
                → 三路加权 → 排序 + min_score 过滤
              GetDocTool → db.query(Document + DocumentChunk) 取全文
          → 把 Observation 作为 user message 追加到 messages
          → 若解析出 Final Answer → 结束循环
      → 循环结束仍无 Final Answer → 追加 summary 消息让 LLM 总结
    → 组装 AgentResponse (含 steps/latency_ms)
    → return ApiResponse[AgentResponse]
```

**关键点：** ReAct 循环通过"Thought → Action → Observation"的迭代让 LLM 自主决定是否调用工具以及调用哪个工具。每轮工具结果（Observation）作为新的 user 消息回灌给 LLM，使其基于检索到的事实继续推理。`max_turns` 防止无限循环；`_parse_agent_output` 用正则兼容中英文冒号解析结构化输出。

---

### 11.5 外部渠道 / Webhook 调用链

```
外部系统 POST /api/v1/integration/webhook/{token}
  → integration.webhook(token, body, request)
    → IntegrationService.verify_webhook_token(token)
        解析 {channel}_{kb_id}_{sig24}
        校验 channel ∈ VALID_CHANNELS
        按 channel+kb_id+salt+day_key 重算 sha256 比对 sig
        容灾: today/yesterday 两个 day_key + 多 salt
    → 按 channel 选择解析器:
        shopify → parse_shopify_webhook(body, headers) → InboundMessage
        其他    → parse_generic_http(body)             → InboundMessage
    → 选执行引擎:
        use_agent=True → AgentService(db).run(...)   (多步推理)
        else           → RAGPipeline.answer(...)      (单次检索+生成)
    → IntegrationService.render_reply_for_channel(channel, reply)
        shopify → _render_shopify: 生成 message_html (HTML 转义 + 来源列表)
        generic → _render_generic:  纯 JSON
    → return OutboundReply
```

**通用聊天调用链：**

```
客户端 POST /api/v1/integration/generic/{kb_id}/chat
  → integration.generic_chat(kb_id, payload)
    → db.query(KnowledgeBase) 校验 kb 存在
    → use_agent? AgentService.run : RAGPipeline.answer
    → IntegrationService.render_reply_for_channel(CHANNEL_SHOPIFY, reply)
    → 异常时友好降级返回默认提示
```

**关键点：** Webhook token 采用"渠道_知识库_签名"三段式，签名按天变化（day_key）实现轻量级时效控制；verify 支持昨日 day_key 容灾，避免跨天失效。Shopify 渠道输出 `message_html` 可直接嵌入 Liquid 模板。

---

### 11.6 向量索引管理调用链

```
客户端 GET /api/v1/retrieval/status
  → retrieval.global_status
    → VectorStoreManager.list_stored_kbs() (扫描磁盘 .vecstore 文件)
    → 报告 _HAS_FAISS / _HAS_NUMPY 可用性

客户端 POST /api/v1/retrieval/search/{kb_id}
  → retrieval.search(kb_id, payload)
    → _get_embedding().encode_single(query_text)
    → _get_manager().get_store(kb_id) (内存缓存→磁盘加载→新建)
    → store.search(query_vector, top_k)
    → 过滤 min_score → 反查 metadata → 组装 VectorSearchItem
    → return VectorSearchResponse

客户端 DELETE /api/v1/retrieval/index/{kb_id}
  → _get_manager().delete(kb_id) (删内存 + 删磁盘 .vecstore/.faiss/.state.json)
```

**关键点：** `retrieval.py` 直接使用 `VectorStoreManager` 与 `EmbeddingService`，绕过了 service 层。这是为管理/调试目的设计的"低层级"接口，与 `DocumentService.search_in_kb` / `RetrievalService.search` 形成"高层级"业务接口的双轨。

---

## 12. 代码风格、可维护性与潜在改进点

### 12.1 代码风格总结

**整体风格：** 项目代码风格统一、注释详尽，整体质量较高。主要特征：

- **中文注释 + 英文标识符**：模块/类/方法 docstring 与行内注释均用中文，变量名/函数名用英文，符合国内团队习惯。
- **模块级 docstring**：每个 processor 文件顶部都有详细的"设计意图 + 核心接口"说明（如 `vector_store.py`、`embedding_service.py`、`llm_service.py`），便于快速理解模块职责。
- **`from __future__ import annotations`**：几乎每个文件首行都加，统一使用字符串化注解，兼容性好。
- **格式化**：使用 4 空格缩进、双引号字符串为主、`%` 格式化（而非 f-string），风格一致但略偏老派（`%` 格式化在复杂场景可读性不如 f-string）。
- **类型注解**：processor 层类型注解完整（`List`/`Dict`/`Optional`/`Tuple`），API/service 层较完整。
- **线程安全**：`BaseLLMProvider`/`BaseVectorStore`/`CachingEmbeddingProvider` 都用 `threading.RLock` 保护内部状态，体现并发意识。
- **依赖降级**：`numpy`/`faiss`/`pypdf`/`pdfplumber`/`python-docx`/`bs4` 均为可选依赖，通过 `try/except ImportError` 探测后优雅降级，保证核心功能在最小依赖下可用。
- **分段注释分隔符**：processor 文件大量使用 `# ============` 与 `# ----------` 划分逻辑段，结构清晰。

**风格不足：**

- `%` 格式化遍地使用（如 `"第 %d 个向量维度非法: 期望 %d，实际 %d" % (i, ...)`），建议统一改为 f-string 提升可读性。
- 部分文件混用 `from typing import` 与内建泛型（Python 3.9+ 可用 `list[int]`），但因兼容性考虑保留 `typing` 是合理的。
- `chat.py`(api) 顶部 `_cache = {}` 用模块级全局变量做单例，不如用函数级闭包或 `functools.lru_cache` 规范。

---

### 12.2 已发现的 Bug 与缺陷

#### Bug 1：`conversation_service.py` 导入不存在的 `Message`（严重）

**位置：** `app/services/conversation_service.py` 顶部

**问题：** 
```python
from app.models.entities.conversation import Conversation, Message
```
但 `app/models/entities/conversation.py` 实际定义的类是 `ChatMessageRecord`，并不存在 `Message`。

**影响：** `ImportError` 导致整个 `conversation_service` 模块不可导入，进而 `api/v1/conversation.py` 所有端点（会话列表/创建/历史/删除）在运行时 500 错误。

**修复方案：** 将 `Message` 改为 `ChatMessageRecord`，或在 `entities/conversation.py` 中增加别名 `Message = ChatMessageRecord`。推荐前者以保持命名一致。

#### Bug 2：`requests` 未声明在 `requirements.txt`（中等）

**位置：** `app/processors/llm/llm_service.py` 的 `HTTPLLMProvider`

**问题：** `HTTPLLMProvider.__init__` 中 `import requests` 并创建 `requests.Session`，但 `requirements.txt` 未列出 `requests`。

**影响：** 当 `LLM_PROVIDER` 配置为 deepseek/openai/custom 且有 API Key 时，`HTTPLLMProvider` 初始化会 `ImportError`。虽然代码做了 `try/except` 把 session 置 None，但后续 `_call_with_retry` 调用仍会失败。

**修复方案：** 在 `requirements.txt` 增加 `requests>=2.28,<3.0`，或改用已声明的 `httpx`（与 `RemoteAPIEmbeddingProvider` 统一技术栈）。

#### Bug 3：`details={"error": str(exc)} if False else {}`（轻微/可疑）

**位置：** `app/core/exceptions.py` 的 `global_exception_handler`

**问题：** `if False` 是死代码，表达式永远走 `else` 分支返回空 dict。看起来像调试残留——开发者可能想用环境变量控制是否回传错误细节，但忘了把 `False` 换成 `settings.APP_DEBUG`。

**修复方案：** 改为 `details={"error": str(exc)} if settings.APP_DEBUG else {}`。

#### Bug 4：`start.py` 硬编码绝对路径（部署隐患）

**位置：** `start.py`

**问题：** `VENV_DIR` 与 `BACKEND_DIR` 硬编码为 `c:\Users\LEgion\Desktop\...` 绝对路径，换机器/换用户即失效。

**修复方案：** 改用 `os.path.dirname(os.path.abspath(__file__))` 推导 `BACKEND_DIR`，`VENV_DIR` 从环境变量或相对路径推导。

---

### 12.3 架构与设计改进点

#### 12.3.1 `DocumentService` 与 `DocumentPipeline` 职责重复

`DocumentService`（业务层）自行组装 `DocumentProcessor` + `EmbeddingService` + `VectorStoreManager` 实现了完整的"上传→分块→向量化→入库"流程；而 `DocumentPipeline`（processor 层）也实现了几乎相同的逻辑。

**问题：** 两套代码并行存在，维护成本翻倍，行为可能不一致。

**改进：** 让 `DocumentService` 委托 `DocumentPipeline` 处理纯逻辑（分块+向量化+索引），自身只负责 DB 事务与权限校验。这样 processor 层可独立单测，service 层聚焦业务编排。

#### 12.3.2 `kb_service.increment_*` 与 `document_service` 直接改字段重复

`KnowledgeBaseService` 定义了 `increment_documents`/`increment_chunks`，但 `DocumentService` 实际直接操作 `kb.total_documents += delta` 字段，未调用前者。

**改进：** 统一通过 `KnowledgeBaseService.increment_*` 更新计数，保证事务边界一致。

#### 12.3.3 `retrieval.py` 绕过 service 层

`api/v1/retrieval.py` 直接使用 `VectorStoreManager` 与 `EmbeddingService`，而 `api/v1/document.py` 的搜索走 `DocumentService.search_in_kb`，`agent.py` 走 `RetrievalService.search`。三条路径各自实现"查询向量化→搜索→过滤→组装"。

**改进：** 统一收敛到 `RetrievalService` 作为唯一检索入口，管理类接口（status/flush/delete）可保留直接访问 manager，但搜索应统一。

#### 12.3.4 `integration.py` 每次请求 `tempfile.mkdtemp` 新建 `VectorStoreManager`

`generic_chat` 与 `webhook` 中每次请求都用 `tempfile.mkdtemp()` 新建临时目录构造 `VectorStoreManager`。虽然 `get_store` 会从全局 `VECTOR_STORE_DIR` 加载磁盘索引，但临时目录仅影响"内存 store 缓存"位置，且 `mkdtemp` 产生的目录不会自动清理，长期运行会泄漏临时文件。

**改进：** 复用全局单例 `VectorStoreManager`（`get_store` 已有内存缓存且线程安全），移除 `mkdtemp` 逻辑。

#### 12.3.5 单例分散且不统一

项目中有多种单例模式：
- `chat.py`：模块级 `_cache = {}` 字典缓存。
- `retrieval.py`：模块级 `_manager_singleton`/`_embedding_singleton` 全局变量 + 双重检查。
- `embedding.py`：模块级 `_service_singleton`。
- `llm_service.py`：`get_llm_service()` 双重检查锁。
- `DocumentService`：实例级懒加载属性。

**改进：** 统一用 `functools.lru_cache` 或显式单例类，减少模式不一致带来的认知负担。

#### 12.3.6 `chat_service` 未集成 `conversation_service` 持久化

`RAGPipeline.answer` 只做一次性检索+生成，不持久化对话历史。`api/v1/chat.py` 用请求中的 `history` 字段传多轮上下文。而 `ConversationService` 已实现完整的历史持久化能力（但因导入 bug 不可用）。

**改进：** 修复 `conversation_service` 后，让 `chat.py` 集成 `ConversationService`，支持按 conversation_id 自动加载/追加历史，前端无需自行维护 history。

---

### 12.4 性能改进点

#### 12.4.1 SQLite 同步阻塞 ASGI 事件循环

数据库操作全程同步（`sqlalchemy.create_engine` + 同步 Session），但在 FastAPI 异步路由中直接调用。SQLite 的 `check_same_thread=False` 允许多线程，但同步 IO 仍会阻塞事件循环线程。

**改进：** 将重 DB 操作用 `run_in_threadpool` 包装，或迁移到 `sqlalchemy[asyncio]` + `aiosqlite`/`asyncpg`。`DATABASE_URL` 已预留 PostgreSQL 切换路径。

#### 12.4.2 `delete_document` 后全量重建索引

删除单个文档时，`DocumentService.delete_document` 会删除该库全部向量索引并按剩余 chunks 重新向量化重建。对于大知识库（数千 chunk）开销显著。

**改进：** 
- 短期：标记删除（软删除），搜索时按 document_id 过滤。
- 长期：引入支持删除的向量索引（如 FAISS `IndexFlatIP` 支持 `remove_ids`，或改用 Milvus/Qdrant）。

#### 12.4.3 `MockEmbeddingProvider` 与 `LocalNumpyEmbeddingProvider` 重复 ngram 逻辑

两者都基于 hash + ngram 映射维度，算法高度相似但实现各自一套。

**改进：** 抽取公共 ngram 提取与 hash 投影逻辑到基类或工具函数，减少重复。

#### 12.4.4 BM25 索引按 kb 全量内存构建

`RetrievalService._get_or_build_bm25` 把整个 kb 的所有 chunk 内容加载到内存建 BM25 索引，10 分钟重建。大库内存压力大。

**改进：** BM25 索引落盘（如用 `whoosh`/`rank_bm25` 持久化），或迁移到 SQLite FTS5 做全文检索。

#### 12.4.5 向量搜索多取 `top_k*2`/`top_k*5` 候选再过滤

`RAGPipeline.search` 取 `top_k*2`，`RetrievalService.search` 取 `top_k*5`，策略不一致且可能召回过多。

**改进：** 统一候选倍率参数化，或基于 min_score 动态调整。

---

### 12.5 安全改进点

#### 12.5.1 `SECRET_KEY` 默认值不安全

`config.py` 中 `SECRET_KEY: str = "change-this-in-production"`，若部署时未改 `.env`，JWT 签名密钥为公开默认值，攻击者可伪造 token。

**改进：** 启动时校验 `SECRET_KEY` 是否为默认值，若 `APP_ENV=production` 则拒绝启动或强制告警。

#### 12.5.2 Webhook HMAC 仅记录不校验

`IntegrationService.parse_shopify_webhook` 注释说明"HMAC 仅记录不强制校验"，意味着任何人都能伪造 Shopify webhook。

**改进：** 配置 Shopify webhook secret 后强制校验 `X-Shopify-Hmac-SHA256` 头。

#### 12.5.3 全局异常处理器不回传错误细节

`global_exception_handler` 中 `details={"error": str(exc)} if False else {}`——虽然 `if False` 是 bug，但"不回传内部错误"的安全意图是正确的。

**改进：** 修复为 `if settings.APP_DEBUG`，生产环境保持不回传。

#### 12.5.4 JWT 使用 HS256 对称签名

手写 JWT 用 HS256，密钥同时用于签发与验证。若服务有多实例或需前端验签，对称方案不灵活。

**改进：** 多实例场景可改 RS256（非对称），但当前单后端 HS256 足够，优先级低。

#### 12.5.5 文件上传未限制大小与类型白名单强校验

`document.py` 的 `upload_document` 直接 `file.file.read()` 读取全部内容到内存，无大小限制；扩展名校验在 `DocumentService` 中但未做 magic bytes 校验。

**改进：** 在路由层用 FastAPI 的 `File(..., max_size=...)` 或中间件限制上传大小；对敏感类型（可执行文件）做 magic bytes 黑名单。

---

### 12.6 可维护性改进点

#### 12.6.1 `schemas.py` 与 `schemas/` 目录并存

项目同时存在 `app/models/schemas.py`（单文件版）与 `app/models/schemas/`（按域拆分的多文件版，含 `user_schemas.py`/`kb_schemas.py`/`document_schemas.py`/`chat_schemas.py`/`embedding_schemas.py`/`retrieval_schemas.py`/`common_schemas.py`）。

**问题：** `schemas/` 目录是 `schemas.py` 的重构版，按业务域拆分更清晰、字段约束更严格（如 `chat_schemas.py` 的 `ChatRequest` 加了 `ge`/`le`/`max_length` 校验），但两者并存导致"到底用哪个"的歧义。当前 API 路由实际 `from app.models.schemas import ...`（单文件版）。

**改进：** 完成迁移后删除 `schemas.py`，统一用 `schemas/` 目录版本。`schemas/` 版本的字段约束更完善，是更好的演进方向。

#### 12.6.2 `agents/` 目录为空壳

`app/agents/__init__.py` 与 `app/agents/tools/__init__.py` 仅含 `"""Agent模块 - Day 10+ 实现"""` 注释，无实际代码。当前 Agent 实现实际在 `app/services/agent_service.py`。

**改进：** 若计划将 Agent 工具体系迁移到 `agents/` 目录，应明确迁移计划；否则删除空壳目录避免误导。

#### 12.6.3 测试缺失

`pytest.ini` 配置了 `testpaths = tests`，但项目根目录下无 `tests/` 目录，仅有 `test_all.py`/`test_full.py`/`debug_agent.py` 等散落的脚本。`requirements.txt` 声明了 pytest 但无实际测试套件。

**改进：** 建立 `tests/` 目录，按模块补充分层测试（processor 层单测优先，因其与 DB 解耦最易测）。

#### 12.6.4 `debug_agent.py`/`test_all.py`/`test_full.py` 散落根目录

这些脚本直接放在 `backend/` 根目录，混入正式代码。

**改进：** 移入 `tests/` 或 `scripts/` 目录，或清理不再使用的脚本。

#### 12.6.5 `data/` 目录纳入版本管理

`data/rag_system.db`、`data/uploads/`、`data/vector_stores/` 是运行时产物，不应纳入版本管理。

**改进：** `.gitignore` 中排除 `data/`。

#### 12.6.6 循环依赖用延迟 import 规避

`retrieval_service.py` 通过函数内 `from app.services.document_service import DocumentService` 规避与 `document_service` 的循环依赖。

**改进：** 重构为依赖注入（`RetrievalService` 构造时接收一个"索引重建回调"），或将公共逻辑下沉到更底层模块。

#### 12.6.7 `health.py` 导入未使用的 `get_db_dep`

`app/api/health.py` 顶部 `from app.api.dependencies import get_db_dep` 但未使用。

**改进：** 删除未使用导入。

---

### 12.7 改进优先级建议

| 优先级 | 改进项 | 理由 |
|---|---|---|
| P0（阻断） | 修复 `conversation_service.py` 的 `Message` 导入 bug | 导致整个对话历史功能不可用 |
| P0（阻断） | `requirements.txt` 补充 `requests` | 导致真实 LLM provider 不可用 |
| P1（重要） | `start.py` 去硬编码路径 | 阻碍部署与协作 |
| P1（重要） | `SECRET_KEY` 生产环境强制校验 | 安全风险 |
| P1（重要） | `integration.py` 移除 `mkdtemp` 复用单例 | 临时文件泄漏 |
| P2（优化） | 统一检索入口（`RetrievalService`） | 降低维护成本 |
| P2（优化） | `DocumentService` 委托 `DocumentPipeline` | 消除重复代码 |
| P2（优化） | 迁移到 `schemas/` 目录版本 | 字段校验更完善 |
| P2（优化） | 补充 `tests/` 测试套件 | 保证重构安全性 |
| P3（增强） | 异步 DB（asyncpg/aiosqlite） | 提升并发性能 |
| P3（增强） | 删除文档改为软删除 | 避免全量重建索引 |
| P3（增强） | BM25 持久化（FTS5/whoosh） | 降低内存压力 |

---

### 12.8 总结

本项目作为从零搭建的 RAG 知识库系统，整体架构分层清晰（API → Service → Processor → Models），模块职责划分合理，代码注释详尽，依赖降级机制完善。核心亮点包括：

1. **三路混合检索**（向量 + BM25 + 关键词）——`RetrievalService` 的加权重排策略提升了召回质量。
2. **ReAct Agent**——`AgentService` 实现了完整的 Thought/Action/Observation 推理循环。
3. **多层幻觉抑制**——`RAGPipeline` 与 `MockLLMProvider` 都实现了"无检索结果→拒绝回答"的防幻觉策略。
4. **可插拔 Provider 体系**——Embedding/LLM 均支持多 provider + 缓存装饰器，Mock 实现保证零配置可运行。
5. **向量存储多后端**——FAISS(Flat/IVF)/PurePython 三套实现，按可用性自动选择。
6. **手写 JWT**——不依赖 PyJWT，HMAC 常量时间比较防时序攻击。

主要待改进方向集中在：修复阻断性 bug（`Message` 导入 / `requests` 依赖）、消除职责重复（`DocumentService` vs `DocumentPipeline`、三套检索路径）、统一单例模式、补充分层测试、以及生产化加固（密钥校验、上传限制、异步 DB）。

---

> **文档完。** 本文档基于 `c:\Users\LEgion\Desktop\backend\RAG-PY\backend` 下的真实源代码编写，涵盖入口/配置、数据库/模型、API、Service、Processor 五大模块的逐文件、逐段解读，以及模块调用链、依赖关系、可维护性与改进点分析。