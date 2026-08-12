```markdown
# RAG 知识库系统 架构文档（architecture.md）

> 本文档基于对 `backend/` 项目源码的逐文件阅读编写，所有结论均来自项目实际代码。
> 无法由现有资料确认的内容，均明确标注"根据现有资料无法判断"。

---

## 目录

1. [项目概述](#1-项目概述)
2. [目录结构总览](#2-目录结构总览)
3. [系统架构图文字说明](#3-系统架构图文字说明)
4. [核心模块划分](#4-核心模块划分)
5. [业务流程与数据流](#5-业务流程与数据流)
6. [设计思想与关键实现策略](#6-设计思想与关键实现策略)
7. [技术栈选型原因](#7-技术栈选型原因)
8. [项目中的亮点与风险点](#8-项目中的亮点与风险点)
9. [基于现有资料可得出的结论](#9-基于现有资料可得出的结论)
10. [资料缺失项清单](#10-资料缺失项清单)

---

## 1. 项目概述

### 1.1 项目定位

本项目是一个 **RAG（Retrieval-Augmented Generation，检索增强生成）知识库系统**，名称为 `RAG Knowledge Base System`（见 `app/core/config.py` 中 `APP_NAME`）。其核心目标是：让用户上传文档到"知识库"，系统自动完成文本提取、语义分块、向量化与索引构建；用户提问时，系统先在知识库中做向量/关键词检索，再把检索到的片段作为上下文交给大语言模型（LLM）生成带有来源引用的回答，并尽可能抑制"幻觉"（编造知识库中不存在的内容）。

### 1.2 能力概览（来自源码）

从 `app/api/v1/__init__.py` 注册的路由与各 service 实现可确认，系统对外提供以下能力：

- **认证**：用户注册 / 登录 / 获取当前用户（`app/api/v1/auth.py`），基于手写 JWT。
- **知识库管理**：创建 / 列表 / 详情 / 更新 / 删除，支持公开/私有与所有者权限（`app/api/v1/knowledge_base.py`、`app/services/kb_service.py`）。
- **文档管理**：上传（自动提取文本→分块→向量化→建索引）、列表、详情、分块查看、删除、向量搜索（`app/api/v1/document.py`、`app/services/document_service.py`）。
- **RAG 对话**：单轮/多轮对话、仅检索、SSE 流式对话、Provider 信息查询（`app/api/v1/chat.py`、`app/services/chat_service.py` 中的 `RAGPipeline`）。
- **Agent 推理**：基于 ReAct 风格的多步工具调用（`app/api/v1/agent.py`、`app/services/agent_service.py`）。
- **对话历史**：会话与消息的持久化管理（`app/api/v1/conversation.py`、`app/services/conversation_service.py`）。
- **检索/索引管理**：全局索引状态、单库索引状态、强制落盘、删除索引、清内存、直接向量搜索（`app/api/v1/retrieval.py`、`app/services/retrieval_service.py`）。
- **Embedding 服务**：批量/单文本向量化、相似度计算、质量报告（`app/api/v1/embedding.py`、`app/processors/embedding/embedding_service.py`）。
- **外部渠道集成**：Shopify / 通用 HTTP Webhook 接入、Webhook Token 生成与校验（`app/api/v1/integration.py`、`app/services/integration_service.py`）。
- **健康检查**：`/health`、`/health/ping`（`app/api/health.py`）。

### 1.3 设计取向

从整体代码组织可以看出三个鲜明取向：

1. **"从零构建"**：`app/main.py` 的描述字段写着 `"Built from scratch"`；JWT、BM25、语义分块、向量存储等均自带实现，而非全部依赖第三方库。
2. **"可降级"**：几乎每个外部依赖（FAISS、numpy、requests、httpx、pypdf、python-docx、BeautifulSoup）都有 `try/except ImportError` 的回退路径，无 API Key 时退化为 Mock，保证开发环境零配置可运行。
3. **"纯逻辑与业务分层"**：`RAGPipeline`、`DocumentPipeline` 等被刻意设计为不依赖数据库的纯逻辑层，便于测试与独立部署；`*Service` 类则在其上叠加 DB 与权限。

---

## 2. 目录结构总览

项目根目录为 `backend/`。以下结构基于实际读取的文件路径整理（仅列出代码与配置相关条目；`__pycache__`、`data/` 运行时产物等不列入）。

```
backend/
├── app/
│   ├── main.py                      # FastAPI 应用入口（create_app 工厂）
│   │
│   ├── core/                        # 全局基础设施
│   │   ├── config.py                # Settings（Pydantic BaseSettings 全局配置）
│   │   ├── security.py              # PBKDF2 密码哈希 + 手写 JWT
│   │   ├── logging.py               # 统一日志（控制台 + RotatingFileHandler）
│   │   └── exceptions.py            # 自定义异常 + 全局异常处理器
│   │
│   ├── models/                      # 数据层
│   │   ├── database.py              # SQLAlchemy engine / SessionLocal / Base / init_db
│   │   ├── response.py              # 统一响应 ApiResponse[T]
│   │   ├── schemas.py               # Pydantic 请求/响应模型（全部 API 的 DTO）
│   │   └── entities/                # ORM 实体
│   │       ├── __init__.py          # 显式导出所有实体（触发 relationship 解析）
│   │       ├── user.py              # User
│   │       ├── knowledge_base.py    # KnowledgeBase
│   │       ├── document.py          # Document / DocumentChunk
│   │       └── conversation.py      # Conversation / ChatMessageRecord
│   │
│   ├── api/                         # HTTP 接口层
│   │   ├── dependencies.py          # get_db_dep / get_current_user / get_current_user_optional / get_current_admin
│   │   ├── health.py                # /health 健康检查
│   │   └── v1/                      # v1 版本路由
│   │       ├── __init__.py          # api_router 聚合（前缀 /v1）
│   │       ├── auth.py              # /auth 认证
│   │       ├── knowledge_base.py    # /knowledge-bases 知识库
│   │       ├── document.py          # /knowledge-bases/{kb_id}/documents 文档
│   │       ├── chat.py              # /chat RAG 对话（含 SSE 流式）
│   │       ├── agent.py             # /agent ReAct Agent
│   │       ├── retrieval.py         # /retrieval 索引管理与向量搜索
│   │       ├── embedding.py         # /embeddings 向量化
│   │       ├── conversation.py      # /conversation 对话历史
│   │       └── integration.py       # /integration Shopify/Webhook 集成
│   │
│   ├── services/                    # 业务服务层（依赖 DB + processors）
│   │   ├── kb_service.py            # KnowledgeBaseService
│   │   ├── document_service.py      # DocumentService（上传全链路）
│   │   ├── chat_service.py          # RAGPipeline（纯逻辑）+ 数据结构
│   │   ├── conversation_service.py  # ConversationService
│   │   ├── retrieval_service.py     # RetrievalService（三重混合检索）+ BM25Index
│   │   ├── agent_service.py         # AgentService（ReAct）+ 工具
│   │   └── integration_service.py   # IntegrationService（渠道适配/Webhook Token）
│   │
│   └── processors/                  # 纯逻辑处理层（无 DB 依赖）
│       ├── __init__.py              # 统一导出 document/embedding/retrieval/llm
│       ├── document/
│       │   ├── document_processor.py    # DocumentProcessor（文本提取+分块）
│       │   ├── markdown_parser.py       # MarkdownParser（结构化解析）
│       │   ├── semantic_chunker.py      # SemanticChunker（语义感知分块）
│       │   └── document_pipeline.py     # DocumentPipeline（文件→向量纯逻辑链）
│       ├── embedding/
│       │   └── embedding_service.py     # EmbeddingService + 多 Provider + 缓存
│       ├── retrieval/
│       │   └── vector_store.py          # FAISS/IVF/PurePython + VectorStoreManager
│       └── llm/
│           └── llm_service.py           # LLMService + Mock/HTTP Provider + 流式
│
├── start.py                         # 启动脚本（注入 venv 路径 + uvicorn.run）
├── requirements.txt                 # 依赖清单
└── .env.example                     # 环境变量示例
```

> 说明：`docs/architecture.md`（即本文件）为本次新增产物。文档目录在源码中原本是否存在其他内容，根据现有资料无法判断。

---

## 3. 系统架构图文字说明

由于本文档为纯文本 Markdown，下面以"分层 + 数据流"的文字方式描述系统架构。

### 3.1 分层视图（自上而下）

```
┌─────────────────────────────────────────────────────────────────────┐
│  客户端 / 外部渠道                                                    │
│  （前端 SPA、Shopify App Proxy、Shopify Webhook、通用 HTTP 调用方）       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP / SSE
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  API 层（app/api）                                                    │
│  ┌──────────┬──────────┬──────────┬──────────┬──────────┬────────┐  │
│  │ health   │ auth     │ knowledge│ document │ chat     │ agent  │  │
│  │          │          │ _base    │          │ (+SSE)   │        │  │
│  ├──────────┼──────────┼──────────┼──────────┴──────────┴────────┤  │
│  │ retrieval│embedding │conversation│ integration                 │  │
│  └──────────┴──────────┴──────────┴──────────────────────────────┘  │
│  公共：dependencies.py（DB 会话、当前用户、可选用户、管理员）            │
│  公共：main.py 注册全局异常处理器 + CORS + startup(init_db)             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 调用
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Service 业务层（app/services）—— 依赖 DB + processors                  │
│  KnowledgeBaseService / DocumentService / ConversationService        │
│  RetrievalService（三重混合）/ AgentService（ReAct）/ IntegrationService│
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 复用
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Processor 纯逻辑层（app/processors）—— 无 DB 依赖，可独立测试            │
│  ┌───────────────┬───────────────┬───────────────┬────────────────┐ │
│  │ document      │ embedding     │ retrieval     │ llm            │ │
│  │ DocumentProc- │ EmbeddingSvc  │ VectorStore   │ LLMService     │ │
│  │ essor/Seman-  │ (Mock/Local/  │ Manager       │ (Mock/HTTP/    │ │
│  │ ticChunker/   │  Remote+Cache)│ (FAISS/IVF/   │  DeepSeek/     │ │
│  │ Pipeline      │               │  PurePython)  │  OpenAI/Custom)│ │
│  └───────────────┴───────────────┴───────────────┴────────────────┘ │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ 持久化
                               ▼
┌──────────────────────┬───────────────────────┬──────────────────────┐
│ 关系数据库（SQLite）   │ 文件系统               │ 向量索引磁盘文件        │
│ users / knowledge_   │ UPLOAD_DIR            │ VECTOR_STORE_DIR     │
│ bases / documents /  │ ./data/uploads/kb_N/  │ kb_N.vecstore[.faiss]│
│ chunks / conversations│                      │ [.state.json]        │
│ / messages           │                       │                      │
└──────────────────────┴───────────────────────┴──────────────────────┘
                               │
                               ▼ （可选外部调用）
┌─────────────────────────────────────────────────────────────────────┐
│  外部服务：DeepSeek / OpenAI / 自定义 OpenAI 兼容 LLM；远程 Embedding API│
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 关键架构要点说明

- **API 层薄、Service 层厚**：`app/api/v1/*` 中的端点函数基本只做参数校验、权限透传、调用 Service、包装 `ApiResponse` 返回；真正的业务逻辑（权限判定、流程编排、异常处理）集中在 `app/services/*`。
- **Processor 层是"纯函数式"底座**：`RAGPipeline`（`app/services/chat_service.py`）与 `DocumentPipeline`（`app/processors/document/document_pipeline.py`）都被刻意设计为不持有 `Session`，仅依赖 `EmbeddingService`、`VectorStoreManager`、`LLMService` 等纯逻辑组件。这使得检索-生成链路可以脱离数据库单测。
- **存储三态**：关系数据（SQLAlchemy）、原始上传文件（磁盘）、向量索引（磁盘 pickle + FAISS 文件）三者并列，由 `DocumentService` 在上传流程中协同写入。
- **单例与懒加载**：`get_llm_service()`（`app/processors/llm/llm_service.py`）使用双检锁单例；`chat.py` 中 `_get_pipeline()`、`retrieval.py` 中 `_get_manager()`/`_get_embedding()`、`embedding.py` 中 `get_embedding_service()` 均为模块级缓存单例，避免重复初始化。

---

## 4. 核心模块划分

### 4.1 core 基础设施层

#### 4.1.1 `app/core/config.py` — 全局配置

- 定义 `Settings(BaseSettings)`，通过 `.env` 注入，`case_sensitive = True`。
- 涵盖：应用基础（`APP_NAME/APP_ENV/APP_DEBUG/APP_HOST/APP_PORT`）、数据库（`DATABASE_URL` 默认 SQLite）、安全（`SECRET_KEY/JWT_ALGORITHM/JWT_EXPIRE_MINUTES`，默认 1440 分钟=24 小时）、LLM（DeepSeek/OpenAI/Custom 四选一，`LLM_PROVIDER` 默认 `mock`，含 temperature/max_tokens/top_p/timeout/重试与退避）、Embedding（`EMBEDDING_MODEL` 默认 `bge-m3`，`EMBEDDING_DEFAULT_DIM=384`，缓存大小 1000）、RAG（`RAG_TOP_K=5`、`RAG_MIN_SCORE=0.35`、`RAG_MAX_CONTEXT_CHARS=3000`、`RAG_REQUIRE_SOURCE=True`）、文件路径（`UPLOAD_DIR`、`VECTOR_STORE_DIR`）、CORS。
- 提供派生属性：`cors_origin_list`、`embedding_provider_name`（有 `EMBEDDING_API_URL` 则 `remote` 否则 `mock`）、`active_llm_name`（人类可读的当前 LLM 名称）。
- `ensure_dirs()` 在应用启动时创建 `UPLOAD_DIR` 与 `VECTOR_STORE_DIR`。
- 模块底部实例化全局 `settings = Settings()`。

#### 4.1.2 `app/core/security.py` — 密码与 JWT

- **密码**：使用 `PBKDF2-HMAC-SHA256`，迭代 100000 次，盐长 16 字节。存储格式 `pbkdf2_sha256$iterations$salt$hash_hex`。`hash_password()` 与 `verify_password()` 均为纯 `hashlib` 实现，**不依赖 passlib/bcrypt**。
- **JWT**：完全手写（注释明确写"不依赖 PyJWT，保证 Python 3.7 兼容性"）。`create_jwt_token()` 手动构造 header/payload 的 base64url，用 `hmac.new(..., sha256)` 签名；`decode_jwt_token()` 用 `hmac.compare_digest` 做常量时间比较防时序攻击，并校验 `exp`。`create_access_token(user_id, username)` 生成包含 `sub/username/type=access` 的令牌。
- 设计意图：**最小化依赖、最大化可控**，代价是需要自行承担 JWT 协议细节（已在代码中处理了 base64url padding、签名比较安全性、过期校验）。

#### 4.1.3 `app/core/logging.py` — 日志

- `setup_logger()` 创建名为 `rag_system` 的 logger，同时挂载 `StreamHandler(sys.stdout)` 与 `RotatingFileHandler`（`maxBytes=10MB`，`backupCount=5`，UTF-8）。
- 格式：`%(asctime)s - %(name)s - %(levelname)s - %(module)s:%(lineno)d - %(message)s`。
- `propagate = False` 防止向 root logger 重复传播。
- 模块底部 `logger = setup_logger()` 提供全局单例。

#### 4.1.4 `app/core/exceptions.py` — 异常体系

- 自定义基类 `RAGBaseException(message, code, details)`，派生 `NotFoundError(404)`、`ValidationError(400)`、`UnauthorizedError(401)`、`InternalError(500)`。
- `ErrorResponse`（Pydantic）统一错误体：`{success:false, code, message, details}`。
- `global_exception_handler`：捕获 `RAGBaseException` 走业务日志（warning）并按 `exc.code` 返回；其余异常走 `logger.exception` 并返回 500。注意一处细节：`details={"error": str(exc)} if False else {}` 中 `if False` 恒为假，即**生产模式下不向外抛露原始异常字符串**（避免信息泄露）。
- `validation_exception_handler`：专门处理 FastAPI 的 `RequestValidationError`，返回 422 并附带 `exc.errors()`。

### 4.2 models 数据层

#### 4.2.1 `app/models/database.py`

- `create_engine(DATABASE_URL, echo=APP_DEBUG, pool_pre_ping=True)`，对 SQLite 额外加 `check_same_thread=False`（FastAPI 多线程访问 SQLite 必需）。
- `SessionLocal = sessionmaker(autocommit=False, autoflush=False)`。
- `Base = declarative_base()`。
- `get_db()` 生成器依赖（yield Session → close）。
- `init_db()`：先 `import app.models.entities` 触发所有实体注册（使字符串形式 `relationship("KnowledgeBase")` 可解析），再 `Base.metadata.create_all`。在 `main.py` 的 `startup` 事件中调用。

#### 4.2.2 `app/models/response.py`

- `ApiResponse[T]`（泛型，`Generic[T]`）：`{success:bool=True, code:int=200, message:str="OK", data:Optional[T], timestamp:datetime}`，`from_attributes=True`。这是全系统统一的成功响应外壳。

#### 4.2.3 `app/models/schemas.py`

集中定义所有 API 的 Pydantic 模型，按域分组：User/Auth、Health、KnowledgeBase、Document、Chat、Embedding、Retrieval/VectorStore。值得注意的细节：
- `ChatRequest` 含 `include_raw` 字段（控制是否回传 system_prompt 与完整 chunk 内容），用于调试。
- `ChatResponse` 含 `retrieved_chunks`、`system_prompt`、`latency_ms`、`provider`、`model`，便于前端展示来源与调试。
- `IndexStatusResponse` 字段非常细（`nlist/nprobe/is_trained/ntotal/consistent/issues/path`），对应 `VectorStoreManager.get_status()` 的丰富返回。

#### 4.2.4 `app/models/entities/*` — ORM 实体

五张表，关系如下：

| 实体 | 表名 | 关键字段 | 关系 |
|---|---|---|---|
| `User` | `users` | username(唯一)、email(唯一,可空)、password_hash、is_active、is_admin | → KnowledgeBase(owner)、→ Conversation(owner) |
| `KnowledgeBase` | `knowledge_bases` | name、description、user_id(FK)、embedding_model、chunk_size(默认500)、chunk_overlap(默认50)、is_public、status、total_documents、total_chunks | ← owner、→ documents、→ conversations |
| `Document` | `documents` | knowledge_base_id(FK)、filename、file_path、file_type、mime_type、file_size、size_bytes、content_text、status、total_chunks | ← knowledge_base、→ chunks |
| `DocumentChunk` | `chunks` | document_id(FK)、knowledge_base_id(FK)、content、chunk_index、metadata_、vector_index | ← document |
| `Conversation` | `conversations` | user_id(FK)、knowledge_base_id(FK)、title、is_active | ← owner、← knowledge_base、→ messages |
| `ChatMessageRecord` | `messages` | conversation_id(FK)、role、content、retrieved_contexts | ← conversation |

设计要点：
- `DocumentChunk.vector_index` 默认 `-1`，表示在向量存储中的下标，写入向量存储后回填。
- `DocumentChunk.metadata_` 用 `Column("metadata", ...)` 显式映射到数据库列名 `metadata`（避开 SQLAlchemy 保留属性名）。
- `KnowledgeBase.total_documents/total_chunks` 是**冗余计数字段**，由 `KnowledgeBaseService.increment_*` 与 `DocumentService` 维护，避免列表查询时 `count(*)`。
- `ChatMessageRecord.retrieved_contexts` 以 JSON 字符串存检索上下文，由 `ConversationService` 序列化。

> ⚠️ 风险点（详见第 8 章）：`conversation_service.py` 第 19 行写的是 `from app.models.entities.conversation import Conversation, Message`，但 `app/models/entities/conversation.py` 与 `__init__.py` 中类名是 `ChatMessageRecord`，**并无 `Message` 别名**。该 import 在运行时会触发 `ImportError`，属于代码不一致问题。

### 4.3 api 接口层

#### 4.3.1 `app/api/dependencies.py`

- `HTTPBearer(auto_error=False)` 作为统一鉴权入口。
- `get_db_dep()`：模块内**重复实现**了一份 `get_db()`（与 `database.get_db` 等价），各路由混用 `get_db_dep` 与 `get_db`（例如 `chat.py` 用 `get_db`，`auth.py` 用 `get_db_dep`），存在重复。
- `get_current_user_optional`：无 token 或无效 token 返回 `None`，不抛异常——用于"未登录可看公开库、登录可看私有库"的渐进权限。
- `get_current_user`：必须有效 token，否则 401，并校验 `is_active`。
- `get_current_admin`：在 `get_current_user` 基础上要求 `is_admin`，否则 403。

#### 4.3.2 路由聚合（`app/api/v1/__init__.py`）

- `api_router = APIRouter(prefix="/v1")`，按顺序 include：auth、knowledge_base、document、embedding、retrieval、chat、agent、conversation、integration。
- `main.py` 中 `app.include_router(api_router, prefix="/api")`，`app.include_router(health.router)`（health 无 `/api` 前缀）。
- 最终对外路径形如 `/api/v1/knowledge-bases`、`/health`、`/`（根路由）。

#### 4.3.3 各路由职责摘要

| 文件 | 前缀 | 主要端点 | 鉴权 |
|---|---|---|---|
| `auth.py` | `/auth` | POST /register、POST /login、GET /me | 注册/登录无需 token；/me 需登录 |
| `knowledge_base.py` | `/knowledge-bases` | CRUD + 列表（分页+关键词） | 创建/改/删需登录且为 owner；列表/详情支持可选登录 |
| `document.py` | `/knowledge-bases/{kb_id}/documents` | 上传/列表/详情/分块/删除/搜索 | 上传/删除需 owner；其余可选登录（公开库可匿名） |
| `chat.py` | `/chat` | POST /message、POST /message/stream(SSE)、POST /search/{kb_id}、GET /provider | 未强制鉴权（依赖知识库可见性） |
| `agent.py` | `/agent` | POST /run、GET /tools | 可选登录；需 `knowledge_base_id` |
| `retrieval.py` | `/retrieval` | GET /status、GET/DELETE /index/{kb_id}、POST /index/{kb_id}/flush、POST /index/clear-memory、POST /search/{kb_id} | 管理类无鉴权（潜在风险） |
| `embedding.py` | `/embeddings` | GET /status、POST /encode、POST /encode-single、POST /similarity | 无鉴权 |
| `conversation.py` | `/conversation` | GET /、POST /、GET /{id}、DELETE /{id} | 必须登录 |
| `integration.py` | `/integration` | POST /generic/{kb_id}/chat、POST /webhook/{token}、GET /generate-token/{kb_id} | generic 无 token；webhook 靠 token；generate-token 可选登录 |

> 注：`chat.py` 与 `embedding.py` 路由内声明了 `get_current_user_optional`/`get_db` 依赖但部分端点未真正使用，鉴权强度偏弱（详见第 8 章）。

### 4.4 services 业务层

#### 4.4.1 `KnowledgeBaseService`（`kb_service.py`）

- 封装知识库 CRUD，权限模型：`get_by_id/list` 用 `or_(user_id==self, is_public==True)` 实现"自己的 + 公开的"；`update/delete` 用 `and_(id==kb_id, user_id==user_id)` 限定 owner。
- `increment_documents/increment_chunks` 维护冗余计数。

#### 4.4.2 `DocumentService`（`document_service.py`）

系统的"上传全链路"核心。`process_upload()` 流程：
1. `_check_kb_owner` 校验所有者；
2. 落盘到 `UPLOAD_DIR/kb_{id}/{uuid8}_{filename}`；
3. 创建 `Document` 记录（status=processing）；
4. `DocumentProcessor.extract_text()` 提取文本（content_text 限 500000 字）；
5. `split_chunks()` 分块（用知识库的 chunk_size/overlap）；
6. `EmbeddingService.encode()` 批量向量化；
7. `db.flush()` 拿到 chunk 真实 id 后，构造 metadata（含 chunk_id/document_id/document_filename/content），`store.add()` 入向量索引；
8. 回填 `vector_index`，`vector_manager.save()` 落盘；
9. 更新 Document.status=processed、total_chunks，更新 KB 计数。

`delete_document()` 的特殊处理：因为 `vector_index` 是全局递增的，删除单个文档会破坏索引连续性，所以采用**删除整个知识库向量索引 + 用剩余 chunks 重建**的策略（代码注释明确说明"简单实现"）。`search_in_kb()` 在 `store.total()==0` 时触发 `_rebuild_vector_index()`，实现"懒重建"。

#### 4.4.3 `RAGPipeline`（`chat_service.py`）

纯逻辑层 RAG 流水线，不依赖 DB。核心方法：
- `search()`：向量搜索，多取 `top_k*2` 再按 `min_score` 过滤。
- `build_context()`：把 chunks 拼成带编号 `[#N] (来源: xxx, 相似度: x.xxx)` 的上下文，受 `RAG_MAX_CONTEXT_CHARS` 限制。
- `build_messages()`：组装 `[system, history..., user]`。**关键设计**——当无 context 时，system prompt 强制要求 LLM 回复固定拒答话术，这是幻觉抑制的核心。
- `answer()`：端到端，实现**三层幻觉抑制**（见第 6 章）。
- `answer_stream()`：生成器，按 `retrieval_done → token → done/error` 的事件流输出，供 `chat.py` 的 SSE 端点消费。

数据结构：`RetrievedChunk`、`RAGPipelineResult`（dataclass）。

#### 4.4.4 `ConversationService`（`conversation_service.py`）

- 会话/消息持久化，含**超限清理**：每用户最多 50 个会话（`MAX_CONVERSATIONS_PER_USER`），每会话最多 200 条消息（`MAX_MESSAGES_PER_CONVERSATION`），超出删最旧。
- `append_user_message` 在消息数 ≤2 且标题为默认时，自动用首条用户消息前 30 字作为会话标题。
- `get_llm_context()` 取最近 `max_turns*2` 条消息作为 LLM 上下文。
- `retrieved_contexts` 字段以 JSON 存额外元信息。

> ⚠️ 前述 `import Message` 不一致问题即在此文件第 19 行。

#### 4.4.5 `RetrievalService` + `BM25Index`（`retrieval_service.py`）

系统的"三重混合检索"核心。
- `BM25Index`：纯 Python Okapi BM25（K1=1.5, B=0.75），自实现中英文混合分词（英文按词、中文按 2-gram + 单字），自带停用词表，返回 0~1 归一化分数。
- `RetrievalService.search()` 流程：
  1. 权限校验；
  2. 向量索引为空则 `_rebuild_if_needed`；
  3. 查询向量化；
  4. 向量粗搜索取 `top_k*5`（至少 20）作为候选；
  5. BM25 对候选重打分，并从 DB 补充含关键词的 chunks（阈值 0.15）；
  6. 关键词 Jaccard 重叠分；
  7. 三路加权 `final = 0.5*vec + 0.35*bm25 + 0.15*keyword`；
  8. 排序 + `min_score` 过滤；
  9. 可选 `_merge_overlapping`（阈值 0.7）合并高重叠 chunk 去冗余；
  10. 组装 `RetrievedHit`（含各路分数 + rank）。
- BM25 索引按 kb 缓存，`rebuild_interval_sec=600`（10 分钟）自动刷新。

#### 4.4.6 `AgentService`（`agent_service.py`）

ReAct 风格 Agent。
- 工具：`SearchKBTool`（调用 `RetrievalService.search`，开 rerank+merge）、`GetDocTool`（按 document_id 取全文）。`web_search` 在文件头注释中提及但**未实现**。
- `SYSTEM_PROMPT` 约束 LLM 以 `Thought/Action/Action Input` 格式输出，最多 `max_turns` 轮。
- `_parse_agent_output()` 用正则解析三段；工具不存在时把错误回灌给 LLM 让其重选；参数非 JSON 时降级为 `{query, kb_id}`。
- 循环结束仍无 Final Answer 则追加一条 "请用 Final Answer 总结" 的 user 消息再调一次 LLM。
- 返回 `AgentResult`（含 `steps: List[AgentStep]`、`latency_ms`）。

#### 4.4.7 `IntegrationService`（`integration_service.py`）

外部渠道集成。
- 渠道枚举：`CHANNEL_SHOPIFY/GENERIC/WECHAT/SLACK/CUSTOM`。
- `InboundMessage`/`OutboundReply` dataclass 作为统一中间结构。
- **Webhook Token**：格式 `{channel}_{kb_id}_{sig24}`，`sig = sha256(f"{channel}|{kb_id}|{salt}|{day_key}").hexdigest()[:24]`，`day_key = int(time/86400)`（按天）。校验时容灾支持多 salt（`SECRET_KEY` + 默认 secret）与 today/yesterday 两天（防跨天）。
- `parse_generic_http`/`parse_shopify_webhook` 宽容解析多种字段名（query/message/text/content/msg）。
- `render_reply_for_channel`：generic 返回纯 JSON；shopify 额外输出 `message_html`（带 HTML 转义 + 来源列表 `<ul>`），可直接嵌入 Liquid 模板。
- 注意：Shopify HMAC 仅记日志，**未强制校验**（代码注释提示需配置 `SHOPIFY_WEBHOOK_SECRET`，但该配置项在 `config.py` 中并不存在）。

### 4.5 processors 纯逻辑层

#### 4.5.1 document 子模块

- `DocumentProcessor`（`document_processor.py`）：文本提取 + 字符级分块。提取支持 txt/md/pdf（pypdf→pdfplumber→原始字节正则清洗 三级回退）、word（python-docx→提示转换）、html（BeautifulSoup→正则去标签）、json/csv/log。`_clean_text` 统一清洗（控制字符、空行、行尾空格）。`split_chunks` 按段落→句子（中英文标点）→overlap 合并。
- `MarkdownParser`（`markdown_parser.py`）：将 Markdown 解析为 `MarkdownBlock`（heading/paragraph/code/list/table/quote/thematic），保留 raw + 规范化 content + meta。设计原则：容错、纯函数、信息保留。提供 `to_plain_text` 剥离行内格式。
- `SemanticChunker`（`semantic_chunker.py`）：语义感知分块。自动判断是否走结构化分块（含特殊块 或 blocks≥3 或 blocks>行数/8）。结构化策略：标题开新 chunk、代码块/表格整体保留、按句子边界切分、短 chunk 合并（`< min_chars/2`）、尾部 overlap。纯文本回退按段落→句子。零依赖（仅 `re`）。
- `DocumentPipeline`（`document_pipeline.py`）：纯逻辑全链路 `文件→文本→chunks→向量`，提供 `process_file/process_text/process_bytes/process_batch`，返回 `ProcessedDocument`。语义分块失败时 `_fallback_split` 字符级切分。便于单测与无 DB 部署。

#### 4.5.2 embedding 子模块（`embedding_service.py`）

可插拔 Provider 体系：
- `BaseEmbeddingProvider`：定义 `encode/encode_single/dim/name`，`_post_process` 统一做 `validate_vectors` + `normalize_vec`。
- `MockEmbeddingProvider`：基于 ngram 哈希累加 + 少量噪声的确定性伪向量，相似文本会在同维度累加从而产生相似向量（比纯 hash 更有语义感）。
- `LocalNumpyEmbeddingProvider`：字符 n-gram + 随机投影，numpy 路径与纯 Python 路径双实现，离线可用。
- `RemoteAPIEmbeddingProvider`：OpenAI 兼容 `/embeddings`，支持 batch 切分、`max_retries` 指数退避、自动维度检测，依赖 `httpx`。
- `CachingEmbeddingProvider`：LRU 装饰器，key 为文本 sha256，`OrderedDict` 实现，带 hits/misses 统计。
- `EmbeddingService`：门面，`from_settings()` 根据是否有 `EMBEDDING_API_URL` 选 Remote 或 LocalNumpy，可选叠加缓存。
- 公共函数：`normalize_vec`、`cosine_similarity`、`validate_vectors`（维度 + 有限值校验，不通过抛 `InternalError`）。

#### 4.5.3 retrieval 子模块（`vector_store.py`）

向量存储体系：
- `BaseVectorStore`：线程安全（`threading.RLock`），`add/search/get_metadata/get_vector/total/stats/check_consistency`，pickle 保存基础元数据。
- `FAISSVectorStore`：`IndexFlatIP`（内积，配合归一化=余弦），增量 add 不重建，score 从 `[-1,1]` 映射到 `[0,1]`。保存 `.faiss` + `.vecstore`。
- `IVFVectorStore`：`IndexIVFFlat` 倒排，适合 >10000 chunk，未训练时累计 buffer，训练阈值 `nlist*5` 至少 50，未训练回退线性扫描。
- `PurePythonVectorStore`：numpy/纯 Python 双路径回退，小规模可用。
- `VectorStoreManager`：按 kb_id 管理多 store，自动选后端（`prefer_faiss=True` 时优先 FAISS，否则 Pure），`get_store/save/save_all/delete/has_store/clear_memory/list_stored_kbs/get_status/bulk_add/bulk_search`。
- 依赖检测：`_HAS_FAISS`、`_HAS_NUMPY` 在模块顶层 `try/except ImportError`。

#### 4.5.4 llm 子模块（`llm_service.py`）

- `ChatMessage`/`ChatResult` dataclass。
- `BaseLLMProvider`：`chat`（子类必实现）、`chat_stream`（默认调 `chat` 一次性产出，子类可重写真流式）、`count_tokens`（粗略 `len//4 + words//2`）。
- `MockLLMProvider`：从 system prompt 解析 `[#N]` chunks，按关键词相关性选 top-2 摘要 + `[来源 #N]`，三层幻觉抑制（system 已说无内容/无 chunks/最高相关性=0 均 reject）。`chat_stream` 按"词/中文字"拆分模拟流式（`time.sleep(0.02)`）。
- `HTTPLLMProvider`：OpenAI 兼容 `/chat/completions`，`requests.Session` 复用，`threading.RLock` 保护，按 `LLM_MAX_RETRIES` 重试（仅 429/5xx），真 SSE 流式解析 `data: {...}` 与 `[DONE]`。
- `LLMService._build_provider()`：按 `LLM_PROVIDER` + 对应 API Key 选择 DeepSeek/OpenAI/Custom，否则 Mock。
- `get_llm_service()`：双检锁全局单例。

---

## 5. 业务流程与数据流

### 5.1 用户注册 → 登录 → 携带 token 访问

```
POST /api/v1/auth/register {username, email, password}
  → auth.register
  → 查重(username/email) → hash_password(PBKDF2) → 存 User
  → create_access_token(user_id, username)
  → 返回 ApiResponse[TokenData]{access_token, user}

POST /api/v1/auth/login {username, password}
  → 查 User → 校验 is_active → verify_password
  → create_access_token
  → 返回 TokenData

后续请求携带 Authorization: Bearer <token>
  → HTTPBearer → extract_user_from_token(校验签名+exp)
  → db.query(User).filter(id==user_id) → 校验 is_active
  → 注入 current_user
```

### 5.2 创建知识库 → 上传文档（核心数据流）

```
POST /api/v1/knowledge-bases {name, chunk_size, chunk_overlap, is_public}
  → KnowledgeBaseService.create (需登录)
  → 返回 KnowledgeBaseInfo

POST /api/v1/knowledge-bases/{kb_id}/documents (multipart file)
  → DocumentService.process_upload
    ├─ 权限: _check_kb_owner(kb_id, user_id) → 404 if not owner
    ├─ 落盘: UPLOAD_DIR/kb_{id}/{uuid8}_{filename}
    ├─ DB: Document(status=processing) → flush
    ├─ DocumentProcessor.extract_text (pdf/docx/html/... 三级回退)
    ├─ DocumentProcessor.split_chunks (段落→句子→overlap)
    ├─ EmbeddingService.encode(chunks)  ← 远程 API 或本地 Mock/Local
    ├─ VectorStoreManager.get_store(kb_id, dim) → store.add(vectors, metas)
    │   └─ metas = {chunk_id, document_id, knowledge_base_id, document_filename, content}
    ├─ 回填 DocumentChunk.vector_index
    ├─ VectorStoreManager.save(kb_id)  ← 落盘 kb_{id}.vecstore[.faiss]
    ├─ Document.status=processed, total_chunks=N
    └─ KB.total_documents += 1, total_chunks += N
  → 返回 DocumentUploadResponse
```

### 5.3 RAG 对话（单轮）

```
POST /api/v1/chat/message {knowledge_base_id, message, history, top_k, min_score, include_raw}
  → chat._get_pipeline() (单例 RAGPipeline)
  → history 截断保留最近 8 轮 → List[ChatMessage]
  → RAGPipeline.answer:
    ├─ search(kb_id, query):
    │   ├─ store = vector_manager.get_store(kb_id, dim)
    │   ├─ query_vec = embedding.encode_single(query)
    │   ├─ raw = store.search(query_vec, top_k*2)  ← FAISS/Pure
    │   └─ 过滤 score<min_score → List[RetrievedChunk]
    ├─ REJECT_SCORE_THRESHOLD=0.42 (硬编码)
    ├─ if 无 chunks 或 max_score<0.42:
    │     返回固定拒答话术 (model="mock-hallucination-filter")
    ├─ build_context(chunks)  ← [#N] 编号 + 来源 + 相似度
    ├─ build_messages(query, ctx, history) ← system(幻觉抑制规则+ctx) + history + user
    ├─ llm.chat(messages)  ← DeepSeek/OpenAI/Custom/Mock
    └─ if llm_answer 为空 → 拒答话术
  → 组装 ChatResponse{answer, retrieved_chunks, system_prompt?, latency_ms}
```

### 5.4 RAG 流式对话（SSE）

```
POST /api/v1/chat/message/stream
  → StreamingResponse(media_type="text/event-stream")
  → RAGPipeline.answer_stream 生成器:
      yield {"type":"retrieval_done","chunks":[...],"max_score":x}
      (若拒答) yield {"type":"token","content":拒答话术} → yield {"type":"done",...}
      (否则)
        for token in llm.chat_stream(messages):
          yield {"type":"token","content":token.content}
        yield {"type":"done","answer":全量,"model":...,"latency_ms":...}
      (异常) yield {"type":"error","message":...}
  → 每条 yield → "data: {json}\n\n"
```

### 5.5 三重混合检索（RetrievalService，注意：RAGPipeline 默认未调用它）

```
RetrievalService.search(kb_id, query, top_k, min_score, enable_rerank, enable_merge)
  ├─ 权限: _can_access_kb
  ├─ store.total()==0 → _rebuild_if_needed (借 DocumentService._rebuild_vector_index)
  ├─ query_vec = embedding.encode_single
  ├─ 向量粗搜: store.search(query_vec, top_k*5) → 候选集
  ├─ BM25 (enable_rerank):
  │   ├─ _get_or_build_bm25(kb_id)  ← 从 DB 加载全部 chunks 建 BM25Index (10分钟缓存)
  │   ├─ 对候选集 bm25.score_normalized
  │   └─ 从 DB 补充含关键词 chunks (bm25>0.15) 直至 candidate_count
  ├─ 关键词重叠分 (Jaccard-like)
  ├─ final = 0.5*vec + 0.35*bm25 + 0.15*keyword
  ├─ 排序 + min_score 过滤
  ├─ _merge_overlapping (enable_merge, 阈值0.7) ← 合并高重叠 chunk
  └─ 返回 List[RetrievedHit]{各路分数, rank}
```

> 注意：`RAGPipeline`（chat 主链路）直接使用 `VectorStoreManager.search`，**并未调用** `RetrievalService` 的三重混合检索。`RetrievalService` 主要被 `AgentService.SearchKBTool` 与 `retrieval.py` 的管理接口使用。即：普通 `/chat/message` 走纯向量检索，Agent 走三重混合检索。这是代码现状，是否符合设计意图根据现有资料无法判断。

### 5.6 Agent ReAct 推理

```
POST /api/v1/agent/run {query, knowledge_base_id, max_turns, history, include_raw_steps}
  → AgentService.run:
    ├─ 组装 system(ReAct 规则 + 工具列表) + history(最近10条) + user
    ├─ for turn in range(max_turns):
    │   ├─ llm.chat(messages, temperature=0.2, max_tokens=800)
    │   ├─ _parse_agent_output → (thought, action, action_input)
    │   ├─ if action == "final answer" or "": answer=action_input; break
    │   ├─ if action not in tools: 回灌错误 observation → continue
    │   ├─ 解析 action_input JSON (失败则降级 {query, kb_id})
    │   ├─ tools[action].run(args, context) → ToolResult
    │   └─ 把 observation 追加为 user 消息 → 下一轮
    ├─ 若循环结束无 answer → 再调一次 LLM 强制总结
    └─ 返回 AgentResult{answer, steps, latency_ms}
```

### 5.7 外部渠道接入（Shopify Webhook）

```
1. GET /api/v1/integration/generate-token/{kb_id}?channel=shopify
   → IntegrationService.generate_webhook_token → 返回 {token, webhook_url, shopify_instructions}

2. Shopify 后台配置 Webhook → POST https://域名/api/v1/integration/webhook/{token}
   → webhook_entry:
     ├─ verify_webhook_token(token) → (channel, kb_id) or 401
     ├─ 解析 body (JSON/form)
     ├─ parse_shopify_webhook 或 parse_generic_http → InboundMessage
     ├─ RAGPipeline.answer(kb_id, msg.query_text, top_k=5, min_score=0.2)
     ├─ OutboundReply{answer_text, sources, latency_ms, raw_context}
     └─ render_reply_for_channel(shopify) → {plain, message_html, sources}
```

---

## 6. 设计思想与关键实现策略

### 6.1 分层与"纯逻辑层"抽离

项目把"数据处理算法"与"业务编排"严格分离：
- `processors/` 是纯算法层，不 import `sqlalchemy`/`Session`，可被单测、可在无 DB 环境运行。
- `services/` 在 processors 之上叠加 DB、权限、事务。
- `RAGPipeline` 与 `DocumentPipeline` 是这一思想的典范：前者把检索-生成链路做成无状态纯逻辑，后者把文件-向量链路做成纯逻辑。

**为什么这样设计**：RAG 的核心价值在算法质量（分块策略、检索召回、幻觉抑制），把这些做成纯逻辑层可以脱离 Web/DB 快速迭代与测试，也方便后续替换存储后端。

### 6.2 全链路可降级

几乎每个外部依赖都有回退：
- LLM：无 API Key → `MockLLMProvider`（仍能基于 chunks 关键词给出带引用的简短回答）。
- Embedding：无 `EMBEDDING_API_URL` → `LocalNumpyEmbeddingProvider`（离线 n-gram 投影）。
- 向量存储：无 faiss → `PurePythonVectorStore`（numpy 或纯 Python）。
- PDF 提取：无 pypdf/pdfplumber → 原始字节正则清洗。
- Word 提取：无 python-docx → 提示转换。
- HTML 提取：无 bs4 → 正则去标签。

**为什么这样设计**：降低开发环境搭建成本，"开箱即跑"（`LLM_PROVIDER=mock` 时无需任何外部服务即可启动并验证全链路）。代价是 Mock 质量有限，仅适合功能验证。

### 6.3 三层幻觉抑制（RAGPipeline.answer）

这是系统的核心质量策略，明确写在代码注释中：
- **L1**：知识库为空 / 无任何 chunks → 直接拒答。
- **L2**：最高 chunk 分数 < `REJECT_SCORE_THRESHOLD`（硬编码 0.42，独立于 `min_score`）→ 视为"有 chunks 但不相关"，拒答。
- **L3**：LLM 层面——system prompt 强制要求"只能根据知识库片段回答，无相关信息必须说固定话术"；LLM 返回空也降级为拒答。

**为什么这样设计**：RAG 系统最大的失败模式是"检索不到也硬编"。通过在 pipeline 层（而非依赖 LLM 自觉）设置硬阈值，保证最坏情况下输出可控的拒答而非幻觉。

### 6.4 三重混合检索（RetrievalService）

向量检索对"语义相近但用词不同"强，但对"精确关键词/专名/编号"弱；BM25 补足关键词命中；lexical overlap 做轻量兜底。三者加权（0.5/0.35/0.15）后重排，并支持合并高重叠 chunk 去冗余。

**为什么这样设计**：纯向量在中文短查询、产品型号、政策编号等场景易漏召回，BM25 + 关键词能显著提升这类查询的命中。代码注释称之为"三路融合"。

### 6.5 语义感知分块

`SemanticChunker` 先用 `MarkdownParser` 把文档切成结构块（标题/代码/表格/列表/引用/段落），再按"标题开新 chunk、代码块/表格整体保留、按句子边界切分、短 chunk 合并、尾部 overlap"策略分块。非 Markdown 文本回退到段落+句子切分。

**为什么这样设计**：朴素字符分块会在代码块/表格中间切断，破坏语义连贯性，直接劣化检索质量。结构感知分块能保证每个 chunk 是一个相对完整的语义单元。

### 6.6 冗余计数与懒重建

- `KnowledgeBase.total_documents/total_chunks` 冗余字段，避免列表 `count(*)`。
- 向量索引在 `store.total()==0` 但 DB 有 chunks 时触发 `_rebuild_vector_index`，实现"按需重建"，无需显式迁移。
- 删除文档时因 `vector_index` 全局递增会破坏连续性，采用"删整库索引 + 用剩余 chunks 重建"的简单策略（代码注释自述"简单实现"）。

**为什么这样设计**：在原型期用最简单可靠的方式维持索引与 DB 一致，避免实现复杂的向量级删除。代价是删除操作开销大（O(剩余chunks) 重新向量化）。

### 6.7 渐进式权限（可选登录）

`get_current_user_optional` 让"未登录看公开库、登录看自己的库"在同一端点共存。`knowledge_base.py`/`document.py` 的列表/详情/搜索均用此模式。

**为什么这样设计**：支持公开知识库的匿名访问（如 Shopify 面向终端客户的客服场景），同时保留私有库的登录保护。

### 6.8 单例与懒加载

`get_llm_service()`（双检锁）、`chat._get_pipeline()`、`retrieval._get_manager()`、`embedding.get_embedding_service()`、`DocumentService` 的 `@property` 懒加载 processor/embedding/vector_manager。

**为什么这样设计**：避免每次请求重建昂贵的对象（HTTP Session、向量索引、模型客户端），同时避免全局初始化带来的启动延迟与循环依赖。

### 6.9 手写基础设施（JWT/BM25/分块）

JWT 不用 PyJWT，BM25 不用 rank_bm25，分块不用 langchain。均自实现。

**为什么这样设计**：注释明确提到"保证 Python 3.7 兼容性"（JWT），以及减少重型依赖。好处是可控、可裁剪；风险是自实现的安全性与正确性需自行保障（如 JWT 已正确处理常量时间比较与 exp，但缺少 `aud`/`iss` 等声明校验）。

---

## 7. 技术栈选型原因

> 选型"原因"在源码注释中有明确说明的，标注【源码注释】；其余为基于代码行为的合理推断，标注【推断】。

| 技术 | 版本约束（requirements.txt） | 选型原因 |
|---|---|---|
| **FastAPI** | `>=0.100,<0.104` | 现代 ASGI Web 框架，原生支持 Pydantic 校验、OpenAPI 文档（`/docs`/`/redoc`）、依赖注入、StreamingResponse(SSE)。【推断】适合 RAG 这种重 IO+流式场景 |
| **Uvicorn** | `>=0.20,<0.23` | ASGI 服务器，配合 FastAPI。`start.py` 中 `reload=False` 生产式启动 |
| **SQLAlchemy** | `>=2.0,<2.1` | ORM。`declarative_base` + `sessionmaker`。注意用的是同步 API（非 async），`pool_pre_ping=True` 防断连 |
| **Pydantic** | `>=1.10,<2.0` | **v1** 而非 v2。`BaseSettings`、`.dict()`、`.from_orm()`、`from_attributes=True` 均为 v1 风格。【推断】锁 v1 是为避免 v2 的破坏性变更（`.dict()`→`.model_dump()` 等） |
| **python-dotenv** | `>=0.20,<2.0` | 加载 `.env`，Pydantic v1 BaseSettings 内置支持 |
| **python-multipart** | `>=0.0.5,<0.0.10` | FastAPI 文件上传（`UploadFile`）所需 |
| **aiofiles** | `>=22.0,<23.0` | 异步文件 IO 支持 |
| **httpx** | `>=0.24,<0.26` | `RemoteAPIEmbeddingProvider` 的 HTTP 客户端（同步 `Client`） |
| **numpy** | `>=1.21,<1.27` | 向量计算底座，FAISS 与 LocalNumpy 都依赖 |
| **faiss-cpu** | `>=1.7,<1.9` | 向量检索引擎，`IndexFlatIP`/`IndexIVFFlat`。可选（运行时 `_HAS_FAISS` 检测） |
| **pytest** | `>=7.0,<8.0` | 测试框架 |
| **pytest-asyncio** | `>=0.21,<0.22` | 异步测试支持 |
| **requests** | （未在 requirements.txt） | `HTTPLLMProvider` 用 `requests.Session`。【风险】requirements.txt 未列 requests，但 LLM HTTP Provider 依赖它；`except ImportError` 时 `_session=None` 报错 |
| **pypdf/pdfplumber/python-docx/beautifulsoup4** | （未在 requirements.txt） | 文档提取的可选依赖，均有回退。未列入 requirements.txt，需手动安装 |

数据库选 SQLite 为默认（`sqlite:///./data/rag_system.db`），`.env.example` 注释明确"Day 1 先用 SQLite，后续切换 PostgreSQL"，并给出了 PostgreSQL 连接串示例（`postgresql+asyncpg://...`）。但当前 `database.py` 用的是同步 engine，切换 asyncpg 需重构为异步 Session，根据现有资料无法判断是否已规划异步改造。

---

## 8. 项目中的亮点与风险点

### 8.1 亮点

1. **纯逻辑层抽离彻底**：`RAGPipeline`/`DocumentPipeline` 不依赖 DB，可独立单测，架构清晰。
2. **三层幻觉抑制**：在 pipeline 层用硬阈值 + system prompt + 空回答降级，把"拒答"作为一等公民，而非依赖 LLM 自觉。
3. **三重混合检索**：Vector + BM25 + 关键词三路加权重排，附 chunk 合并去冗余，检索质量策略完整。BM25 纯 Python 自实现，零额外依赖。
4. **语义感知分块**：Markdown 结构化解析 + 代码块/表格整体保留 + 句子边界 + overlap + 短 chunk 合并，分块质量优于朴素字符切分。
5. **全链路可降级**：LLM/Embedding/向量存储/PDF/Word/HTML 全部有回退，开发环境零配置可跑。
6. **Provider 插件化**：Embedding 与 LLM 均为可插拔多 Provider + 缓存装饰器，切换成本低。
7. **SSE 流式对话**：`answer_stream` + `StreamingResponse` 实现真流式，事件分 `retrieval_done/token/done/error`，前端体验好。
8. **ReAct Agent**：工具调用 + 观察回灌 + 格式容错（非 JSON 降级）+ 兜底总结，工程化程度高。
9. **Shopify 集成实用**：Webhook Token 按天签名 + 跨天容灾 + 多 salt 兜底；Shopify 渲染输出 `message_html` 可直嵌 Liquid。
10. **安全细节**：PBKDF2 迭代 10 万次、JWT 常量时间比较、全局异常不泄露 `str(exc)`（`if False`）、密码不出现在日志。
11. **向量索引一致性检查**：`BaseVectorStore.check_consistency` 校验向量数/next_index/维度/metadata 完整性，`get_status` 暴露 issues。
12. **对话历史超限清理**：自动删最旧会话/消息，防止无界增长。

### 8.2 风险点

1. **🔴 `conversation_service.py` 的 `Message` 导入不一致**：第 19 行 `from app.models.entities.conversation import Conversation, Message`，但实体类名为 `ChatMessageRecord`，`__init__.py` 也未导出 `Message` 别名。一旦该模块被 import（调用任何 `/conversation` 端点）即 `ImportError`。这是当前最严重的代码缺陷。
2. **🔴 `requests` 未列入 requirements.txt**：`HTTPLLMProvider` 依赖 `requests`，但依赖清单无此包。生产环境若未额外安装，所有非 Mock LLM 调用都会失败（代码会进入 `_session=None` 分支返回错误）。
3. **🟠 SQLite 默认 + 同步 ORM**：`check_same_thread=False` 让 SQLite 在多线程下用，但 SQLite 写并发极弱，生产场景需切 PostgreSQL；且 `.env.example` 提到的 asyncpg 与当前同步 engine 不兼容，迁移成本不低。
4. **🟠 Pydantic v1 锁版本**：v1 已停止主流维护，生态库（含 FastAPI 新版）正向 v2 迁移，长期看是技术债。`.dict()`/`.from_orm()` 在 v2 下会报错。
5. **🟠 鉴权覆盖不完整**：
   - `retrieval.py` 的索引管理接口（flush/delete/clear-memory）**无鉴权**，任何人都可删除他人知识库的向量索引。
   - `embedding.py` 全部接口无鉴权，可被滥用消耗远程 Embedding API 配额。
   - `chat.py`/`integration.py` 的对话端点未强制登录，仅靠知识库可见性控制。
   - `integration.generate-token` 的权限校验注释写"可选"，非 admin 用户只要 `kb.user_id == user.id` 才被拦截，但 user 为 None（未登录）时**直接放行**生成 token——存在未授权生成 webhook token 的风险。
6. **🟠 Shopify HMAC 未强制校验**：`parse_shopify_webhook` 仅记录 HMAC 日志，不校验。`SHOPIFY_WEBHOOK_SECRET` 配置项在 `config.py` 中不存在，无法启用严格校验。Webhook 端点可能被伪造请求攻击。
7. **🟠 Webhook Token 安全性有限**：签名 salt 之一是 `DEFAULT_GENERIC_SECRET = "rag-demo-generic-secret-change-me"`（硬编码默认值），若 `SECRET_KEY` 未改且被泄露，token 可被伪造。Token 按天生效，有效期最长 2 天（today+yesterday 容灾）。
8. **🟠 删除文档重建索引成本高**：`delete_document` 删整个 kb 的向量索引 + 重新向量化全部剩余 chunks，大知识库下删除操作可能很慢且重复消耗 Embedding API。
9. **🟠 `get_db` vs `get_db_dep` 重复**：`database.get_db` 与 `dependencies.get_db_dep` 实现等价，路由混用，易混淆。
10. **🟠 `REJECT_SCORE_THRESHOLD=0.42` 硬编码**：在 `RAGPipeline.answer` 与 `answer_stream` 中硬编码，不在 `Settings` 中，无法按知识库调优。且该阈值与 `RAG_MIN_SCORE=0.35`（配置）关系微妙：0.42 > 0.35，意味着即使配置 min_score=0.35，低于 0.42 仍会被拒答。
11. **🟠 RAG 主链路未用三重混合检索**：`/chat/message` 走 `RAGPipeline`（纯向量），而三重混合的 `RetrievalService` 仅 Agent 用。普通对话检索质量可能弱于 Agent。是否符合设计意图根据现有资料无法判断。
12. **🟡 `@app.on_event("startup")` 已弃用**：FastAPI 新版推荐 `lifespan` 上下文，`on_event` 在未来版本可能移除。
13. **🟡 `Document.content_text` 限 500000 字**：超长文档被截断，可能丢失内容。
14. **🟡 BM25 索引内存占用**：每个 kb 的 BM25Index 缓存全部 chunks 的 token list 在内存，大知识库多时内存压力。
15. **🟡 Token 估算粗糙**：`count_tokens = len//4 + words//2`，与真实 BPE token 数差异较大，仅适合粗略用量统计。
16. **🟡 IVFVectorStore 未被默认使用**：`VectorStoreManager._pick_backend` 只在 `prefer_faiss=True` 时返回 `flat`，从不返回 `ivf`（除非显式传 `backend="ivf"`）。`large_kb_threshold` 字段定义了但未在选型逻辑中使用。大规模知识库的 IVF 优化路径实质未启用。
17. **🟡 日志文件路径硬编码**：`logging.py` 默认 `./data/rag_system.log`，不可配置。
18. **🟡 `start.py` 硬编码绝对路径**：`VENV_DIR`/`BACKEND_DIR` 写死 `c:\Users\LEgion\Desktop\backend\RAG-PY\venv`，换机器/换路径即失效。

---

## 9. 基于现有资料可得出的结论

1. **这是一个工程化程度较高的 RAG 后端原型**：分层清晰、纯逻辑层可测、全链路可降级、具备从认证到外部集成的完整闭环。`Built from scratch` 的定位与代码自实现程度一致。

2. **核心质量策略明确**：幻觉抑制（三层）、语义分块、三重混合检索三者构成 RAG 质量的主干，且均有代码落实，不是空话。

3. **目标场景偏向"客服/电商知识库"**：从 Shopify 集成、`message_html` 渲染、公开/私有知识库、Webhook Token 等设计可推断，系统面向"商家上传产品/政策文档，终端客户通过 Shopify 或前端聊天提问"的客服场景。

4. **当前处于"可运行但需加固"阶段**：存在 `Message` 导入不一致、`requests` 未入依赖、管理接口无鉴权等可立即触发的缺陷，不适合直接上生产。SQLite + Pydantic v1 + 同步 ORM 也表明尚未完成生产化改造。

5. **检索能力分层提供但未统一**：`RetrievalService`（三重混合）能力强于 `RAGPipeline`（纯向量），但主对话链路用的是后者。若要提升普通对话质量，应考虑让 `RAGPipeline` 复用 `RetrievalService`。

6. **可降级策略是双刃剑**：极大降低了开发门槛，但也意味着"看起来能跑"可能掩盖"Mock 模式下质量低下"的事实。生产部署必须配置真实 LLM API Key 与远程 Embedding API。

7. **自实现基础设施可控但有维护成本**：JWT、BM25、分块、向量存储均为自实现，好处是无重型依赖、可裁剪；风险是安全性（JWT 声明校验缺失）、性能（纯 Python BM25/向量搜索在大规模下吃力）需自行持续维护。

8. **文档/测试资料在本次阅读范围内缺失**：未见 `tests/` 目录内容（虽然 requirements 含 pytest）、未见 README、未见 API 文档（除 FastAPI 自动生成的 `/docs`）、未见部署脚本（除 `start.py`）。项目成熟度的完整评估受限。

---

## 10. 资料缺失项清单

以下信息**根据现有资料无法判断**，需进一步确认：

1. **测试覆盖**：`requirements.txt` 含 `pytest`/`pytest-asyncio`，但本次阅读范围内未见 `tests/` 目录的实际测试文件，无法判断测试覆盖率与质量。
2. **`web_search` 工具**：`agent_service.py` 文件头注释提到"网页搜索工具 (web_search, 可选)"，但代码中未实现该工具类，无法判断是否规划中或已移除。
3. **异步 DB 改造计划**：`.env.example` 提到 PostgreSQL + asyncpg，但 `database.py` 为同步 engine，无法判断异步化是否在路线图中。
4. **`SHOPIFY_WEBHOOK_SECRET` 配置**：`integration_service.py` 注释提示"若需严格校验请配置 SHOPIFY_WEBHOOK_SECRET"，但 `config.py` 中无此字段，无法判断是否为待实现项。
5. **IVFVectorStore 的实际启用条件**：`large_kb_threshold` 字段存在但未被 `_pick_backend` 使用，无法判断大规模知识库的 IVF 启用是否为手动操作或待自动化的 TODO。
6. **前端项目**：CORS 配置含 `localhost:3000`/`5173`，暗示有前端，但本次范围内未见前端代码，无法判断前端架构与对接状态。
7. **部署与运维**：除 `start.py`（本地启动）外，未见 Dockerfile、CI/CD、生产部署文档，无法判断部署方式。
8. **数据迁移**：未见 Alembic 或其他迁移工具，`init_db` 用 `create_all`，无法判断表结构变更如何处理。
9. **速率限制**：未见任何限流中间件，无法判断是否在前置网关层处理。
10. **`docs/` 目录原有内容**：本次任务是新建 `docs/architecture.md`，无法判断 `docs/` 目录此前是否存在其他文档。
11. **Agent 工具扩展机制**：`AgentService._build_tools` 硬编码注册 `SearchKBTool`/`GetDocTool`，未见动态加载或配置化机制，无法判断是否支持外部工具注入。
12. **多租户隔离**：知识库靠 `user_id` + `is_public` 做软隔离，未见 schema 级多租户（如 tenant_id），无法判断是否支持多组织。
13. **Token 黑名单/刷新机制**：JWT 仅校验 exp，无 refresh token、无黑名单，登出后 token 仍有效至过期，无法判断是否有意为之。
14. **向量维度一致性跨知识库**：`EMBEDDING_DEFAULT_DIM=384` 全局，但 `KnowledgeBase.embedding_model` 字段存在（默认 "default"），未在分块/向量化时实际使用该字段选择不同模型，无法判断是否为预留扩展点。
15. **RAGPipeline 与 RetrievalService 的统一计划**：两者检索能力不一致，无法判断是否为临时状态或有意区分对话 vs Agent 场景。

---

> 本文档结束。所有结论均基于 `backend/` 项目源码，未作无依据推测。标注"根据现有资料无法判断"的项请通过补充资料或询问项目维护者确认。
```
