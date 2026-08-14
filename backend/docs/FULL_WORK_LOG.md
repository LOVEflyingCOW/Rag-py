# RAG 知识库系统 — Phase 1~4 完整工作记录

> 日期: 2026-08-14
> 项目: RAG-PY (FastAPI + SQLAlchemy + FAISS + pgvector + Redis + Celery)

---

## 目录

- [Phase 1: 数据库迁移 + 全链路异步化](#phase-1-数据库迁移--全链路异步化)
- [Phase 2: 鉴权安全体系](#phase-2-鉴权安全体系)
- [Phase 3: 基础设施层 (Redis + Celery + 连接池)](#phase-3-基础设施层-redis--celery--连接池)
- [Phase 4: 全文检索功能 (pgvector + FTS)](#phase-4-全文检索功能-pgvector--fts)
- [问题与解决方案汇总](#问题与解决方案汇总)
- [测试统计](#测试统计)
- [架构总览](#架构总览)

---

## Phase 1: 数据库迁移 + 全链路异步化

### 目标
将项目从同步框架升级为工业级异步架构，支持 PostgreSQL + SQLite 双模式，配置 Alembic 迁移系统。

### 实现内容

#### 1.1 异步数据库引擎

**文件**: [app/models/database.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/database.py)

- **异步引擎 (asyncpg)**: API 层使用，配置连接池参数
  - `pool_size=10` — 常驻连接数
  - `max_overflow=20` — 突发连接数
  - `pool_timeout=30` — 获取连接超时
  - `pool_recycle=3600` — 连接回收时间
  - `pool_pre_ping=True` — 断线重连检测
  - `tcp_keepalives` — TCP 保活参数

- **同步引擎 (psycopg2)**: Celery Worker / Alembic 使用
  - 独立小连接池 `pool_size=max(5, DB_POOL_SIZE//4)`

- **SQLite 模式**: 开发环境自动降级，无连接池，`check_same_thread=False`

- **双模式 URL 转换**:
  ```python
  # 异步: postgresql:// → postgresql+asyncpg://, sqlite:/// → sqlite+aiosqlite:///
  # 同步: postgresql+asyncpg:// → postgresql+psycopg2://
  ```

#### 1.2 数据库会话管理

- **AsyncSessionLocal**: 异步会话工厂，`expire_on_commit=False`, `autoflush=False`
- **SessionLocal**: 同步会话工厂 (Celery 用)
- **get_db_dep()**: 异步依赖注入 (FastAPI 路由用)
- **get_db_sync()**: 同步依赖注入 (Celery 任务用)

#### 1.3 引擎生命周期

- **init_db()**: 异步初始化，创建所有表 + pgvector 扩展
- **dispose_async_engine()**: 优雅释放所有异步连接
- **dispose_sync_engine()**: 优雅释放所有同步连接
- **连接池指标**: checkout/checkin 事件监听，暴露连接池状态

#### 1.4 数据模型

**文件目录**: [app/models/entities/](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities)

| 模型 | 文件 | 说明 |
|------|------|------|
| User | [user.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/user.py) | 用户表 (id, username, email, password_hash, is_active) |
| KnowledgeBase | [knowledge_base.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/knowledge_base.py) | 知识库表 (name, description, user_id, is_public, status) |
| Document | [document.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/document.py) | 文档表 (filename, file_path, file_type, status, content_text) |
| DocumentChunk | [document.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/document.py) | 文档分块表 (content, chunk_index, vector_index) |
| Conversation | [conversation.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/conversation.py) | 对话会话表 |
| RefreshToken | [auth.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/auth.py) | Refresh Token 存储 (token_hash, expires_at, revoked_at) |
| Role | [auth.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/auth.py) | 角色表 (name, description) |
| Permission | [auth.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/auth.py) | 权限表 (resource, action) |
| AuditLog | [auth.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/auth.py) | 审计日志表 (method, path, status_code, ip_address, response_time_ms) |

#### 1.5 全链路异步化

所有 API 路由、Service 层方法均改为 `async def`，使用 `AsyncSession` 进行数据库操作。

**API 路由**: [app/api/v1/](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/v1)
- [auth.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/v1/auth.py) — 认证 (注册/登录/刷新/登出)
- [knowledge_base.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/v1/knowledge_base.py) — 知识库 CRUD
- [document.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/v1/document.py) — 文档上传/管理
- [chat.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/v1/chat.py) — 聊天问答
- [conversation.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/v1/conversation.py) — 对话管理

**服务层**: [app/services/](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services)
- [kb_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/kb_service.py) — 知识库服务
- [document_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/document_service.py) — 文档服务
- [retrieval_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/retrieval_service.py) — 检索服务

**处理器层**: [app/processors/](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/processors)
- [embedding/embedding_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/processors/embedding/embedding_service.py) — 嵌入服务
- [retrieval/vector_store.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/processors/retrieval/vector_store.py) — FAISS 向量存储
- [document/document_pipeline.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/processors/document/document_pipeline.py) — 文档处理流水线
- [llm/llm_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/processors/llm/llm_service.py) — LLM 服务

#### 1.6 Alembic 迁移系统

- 创建 `alembic/` 目录和配置
- 支持 PostgreSQL 和 SQLite 双模式迁移
- 初始化迁移脚本

#### 1.7 Docker Compose

**文件**: [docker-compose.yml](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/docker-compose.yml)

服务定义:
- `postgres` — PostgreSQL + pgvector (端口 5432)
- `redis` — Redis 7 (端口 6379)

### Phase 1 测试

**文件**: [tests/phases/test_phase1_database_async.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase1_database_async.py)

测试覆盖:
- A. 配置层字段就绪
- B. 异步引擎 + AsyncSession 正常工作
- C. Alembic 迁移系统初始化
- D. 全链路 async API 路由
- E. Service 层 async 方法
- F. 无残留同步引用
- G. 端到端 API 流程 (注册→登录→创建KB→列表→删除)
- H. Docker Compose 配置

---

## Phase 2: 鉴权安全体系

### 目标
构建工业级认证安全体系：Argon2id 密码哈希、双 Token 机制、RBAC 角色权限、限流中间件、审计日志。

### 实现内容

#### 2.1 Argon2id 密码哈希

**文件**: [app/core/security.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/security.py)

```python
_pwd_hasher = PasswordHasher(
    time_cost=2,        # 迭代次数
    memory_cost=16384,  # 16MB 内存 (开发环境，生产环境 64MB+)
    parallelism=2,      # 并行线程
    type=Type.ID,       # Argon2id (推荐)
)
```

- `hash_password(password)` — Argon2id 哈希
- `verify_password(password, hashed)` — 密码验证

#### 2.2 双 Token 机制 (Access + Refresh)

- **Access Token** (短时, 15 分钟):
  - Payload: `sub`(user_id), `username`, `roles`, `type`="access", `jti`(唯一ID)
  - 用于 API 请求认证

- **Refresh Token** (长时, 7 天):
  - Payload: `sub`(user_id), `type`="refresh", `jti`(唯一ID)
  - 支持轮换和撤销
  - Hash 后存入 `refresh_tokens` 表

#### 2.3 Token 黑名单 (Redis + 内存降级)

```python
# Redis key: blacklist:{sha256(token)}
# TTL: 与 Token 剩余有效期一致 (自动过期)
```

- `revoke_token(token)` — 同步撤销 (写入内存 + 异步 Redis)
- `revoke_token_async(token)` — 异步撤销 (先写内存，再写 Redis)
- `is_token_revoked(token)` — 同步检查 (仅内存)
- `is_token_revoked_async(token)` — 异步检查 (内存 + Redis)

**关键修复**: `revoke_token_async` 先写入内存 `_blacklist`，再写 Redis，确保撤销立即可见。

#### 2.4 RBAC 角色权限表

**文件**: [app/models/entities/auth.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/auth.py)

- **Role** 表: admin / editor / viewer
- **Permission** 表: resource (kb/document/conversation/user) + action (create/read/update/delete)
- **role_permissions** 关联表: 角色-权限多对多
- **user_roles** 关联表: 用户-角色多对多

#### 2.5 审计日志表

**AuditLog** 表:
- `user_id`, `username` — 用户信息
- `method`, `path` — HTTP 方法和路径
- `status_code` — HTTP 状态码
- `ip_address` — 客户端 IP
- `request_body` — 脱敏后的请求体
- `response_time_ms` — 响应耗时
- `created_at` — 时间戳 (索引)

#### 2.6 限流中间件

**文件**: [app/core/middleware/rate_limit.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/rate_limit.py)

- **算法**: 滑动窗口 (Redis ZSET + Lua 原子操作)
- **策略**:
  - 匿名用户: 30 req/min
  - 已认证用户: 120 req/min
  - 管理员: 600 req/min
- **Redis Lua 脚本**: 原子操作 ZREMRANGEBYSCORE + ZCARD + ZADD
- **降级**: Redis 不可用时使用内存滑动窗口 (线程安全)

#### 2.7 审计日志中间件

**文件**: [app/core/middleware/audit.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/audit.py)

- **流程**: 请求 → 中间件收集信息 → 执行请求 → 计算 duration → dispatch Celery 任务 → 返回
- **敏感字段脱敏**: password, token, secret, api_key 等
- **JWT 解析**: 从 Authorization 头快速提取 user_id (仅解析 payload，不验签)
- **不读取 request.body()**: 避免 BaseHTTPMiddleware 消费 body 导致路由处理器获取不到数据

#### 2.8 安全响应头

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `X-XSS-Protection: 1; mode=block`
- 请求体大小限制 (超大返回 413)

### Phase 2 测试

**文件**: [tests/phases/test_phase2_auth_security.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase2_auth_security.py)

测试覆盖:
- A. 安全核心: Argon2id 密码 + 双 Token + 黑名单
- B. 实体模型: RefreshToken / Role / Permission / AuditLog 表
- C. API 端点: 注册(双Token) / 登录 / 刷新 / 登出
- D. RBAC 角色权限表结构
- E. 限流中间件: 超限返回 429
- F. 审计日志: API 调用记录
- G. 安全响应头
- H. 请求体大小限制: 超大返回 413
- I. 负面测试: 无效/过期/已撤销 Token

---

## Phase 3: 基础设施层 (Redis + Celery + 连接池)

### 目标
引入 Redis 分布式缓存和限流、Celery 后台异步任务、数据库连接池优化。

### 实现内容

#### 3.1 Redis 连接管理

**文件**: [app/core/redis.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/redis.py)

- **全局单例**: 异步 Redis 客户端，应用启动时连接
- **连接配置**:
  - `max_connections=20` — 最大连接数
  - `socket_connect_timeout=3` — 连接超时
  - `socket_timeout=5` — 操作超时
  - `health_check_interval=30` — 健康检查
  - `retry_on_timeout=True` — 超时重试
- **优雅降级**: Redis 不可用时自动切换内存模式，不影响业务
- **健康检查**: `health_check()` 返回 Redis 版本、连接数等状态

#### 3.2 Redis 缓存层 (Cache-Aside)

**文件**: [app/core/cache.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/cache.py)

- **Cache-Aside 模式**: 先查缓存 → miss 查 DB → 回填缓存
- **热点数据缓存**:

| 缓存对象 | Redis Key | TTL | 说明 |
|---------|-----------|-----|------|
| 知识库元数据 | `kb:{id}` | 60s | 读多写少 |
| 用户权限 | `user_perm:{user_id}` | 30s | 读多写少 |
| Embedding 向量 | `embed:{sha256(text)[:16]}` | 3600s | 计算昂贵 |

- **主动失效**: `invalidate_cache(key)` 写操作后主动删除
- **降级**: Redis 不可用时直查 DB

**集成到 kb_service.py**:
- `get_by_id()` 方法使用 Redis 缓存 (TTL=60s)
- 缓存数据包含 `created_at` 和 `updated_at` 字段 (供 schema 序列化)
- `update()` / `delete()` 后自动失效缓存

#### 3.3 Celery 应用配置

**文件**: [app/core/celery_app.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/celery_app.py)

- **Broker**: Redis db 1 (`redis://localhost:6379/1`)
- **Backend**: Redis db 2 (`redis://localhost:6379/2`)
- **序列化**: JSON
- **时区**: Asia/Shanghai
- **任务路由**:

| 任务 | 队列 | 说明 |
|------|------|------|
| `app.tasks.audit_tasks.write_audit_log` | audit | 审计日志写入 |
| `app.tasks.document_tasks.process_document` | document | 文档处理 |

- **定时任务 (Beat)**: 每天凌晨 3 点清理过期审计日志 (保留 90 天)
- **可靠性**: `task_acks_late=True`, `task_reject_on_worker_lost=True`

#### 3.4 审计日志 Celery 任务

**文件**: [app/tasks/audit_tasks.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/tasks/audit_tasks.py)

- **write_audit_log**: 异步写入审计日志到 DB
  - 使用同步 Session (Celery 是同步进程)
  - 失败自动重试 (最多 3 次，指数退避)
- **cleanup_old_audit_logs**: 定时清理过期日志

#### 3.5 文档处理 Celery 任务

**文件**: [app/tasks/document_tasks.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/tasks/document_tasks.py)

- **process_document**: 异步文档向量化
  - 流程: 提取文本 → 分块 → 向量化 → 写入 FAISS → 更新 DB
  - 大文件 (>500KB) 自动走 Celery 异步处理
  - 状态管理: processing → processed / error

#### 3.6 连接池优化

**文件**: [app/models/database.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/database.py) (L60-L90)

- **asyncpg 连接参数**:
  - `server_settings.application_name = "rag_api"` — 便于 PG 监控
  - `timeout = 10.0` — 连接超时
  - `tcp_keepalives_idle = 60` — TCP 保活
  - `tcp_keepalives_interval = 10`
  - `tcp_keepalives_count = 3`
- **连接池指标**: checkout/checkin 事件监听
- **pool_pre_ping**: 连接前 SELECT 1 检测存活

#### 3.7 限流中间件升级

**文件**: [app/core/middleware/rate_limit.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/rate_limit.py)

Phase 3 升级: 从内存限流升级为 Redis 分布式限流
- Redis ZSET + Lua 原子滑动窗口
- 多实例共享限流计数
- Redis 不可用时降级到内存模式

### Phase 3 测试

**文件**: [tests/phases/test_phase3_infra.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase3_infra.py)

测试覆盖:
- Redis 连接和健康检查
- Cache-Aside 缓存命中/失效
- Celery 任务调度
- 连接池指标
- 限流中间件 (Redis 分布式)

---

## Phase 4: 全文检索功能 (pgvector + FTS)

### 目标
用 PostgreSQL 原生能力 (pgvector + FTS) 替代 FAISS 内存索引 + 纯 Python BM25，实现工业级混合检索。

### 实现内容

#### 4.1 数据模型扩展

**文件**: [app/models/entities/document.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/models/entities/document.py)

DocumentChunk 表新增两列:

| 列名 | PostgreSQL 类型 | SQLite 降级类型 | 说明 |
|------|----------------|----------------|------|
| `embedding` | `Vector(384)` (pgvector) | `JSON` | 文档向量嵌入 |
| `search_vector` | `TSVector` | `Text` | 全文检索向量 |

**动态列类型选择**:
```python
if _is_sqlite:
    VectorType = JSON
    TSVectorType = Text
else:
    from pgvector.sqlalchemy import Vector
    VectorType = Vector(384)
    from sqlalchemy.dialects.postgresql import TSVector
    TSVectorType = TSVector()
```

**索引配置** (仅 PostgreSQL):
- `ix_chunks_embedding_cosine` — IVFFLAT 索引 (向量余弦相似度)
- `ix_chunks_search_vector` — GIN 索引 (全文检索)

#### 4.2 PgVectorStore — 向量存储服务

**文件**: [app/services/pg_vector_store.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/pg_vector_store.py) (新建, 384 行)

替代 FAISSVectorStore，将向量直接存储在 PostgreSQL 中。

**核心方法**:

| 方法 | 说明 |
|------|------|
| `add_vector(chunk_id, embedding)` | 为文档块添加向量 |
| `add_vectors(chunks, embeddings)` | 批量添加向量 |
| `search(kb_id, query_vector, top_k)` | 余弦距离向量搜索 |
| `search_by_l2_distance(...)` | L2 距离向量搜索 |
| `search_by_inner_product(...)` | 内积向量搜索 |
| `remove_vector(chunk_id)` | 删除向量 |
| `remove_vectors_by_document(doc_id)` | 删除文档所有向量 |
| `count_vectors(kb_id)` | 统计向量数量 (带缓存) |
| `create_index(index_type, lists)` | 创建 IVF/HNSW 索引 |
| `rebuild_indexes()` | 重建所有向量索引 |
| `stats(kb_id)` | 获取统计信息 |

**距离度量**:
- 余弦距离 `<=>`: `score = 1 - distance/2` (范围 0~1)
- L2 距离 `<->`: `score = 1/(1+distance)`
- 内积 `<#>`: `score = (ip+1)/2`

#### 4.3 PgFullTextSearch — 全文检索服务

**文件**: [app/services/postgres_search.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/postgres_search.py) (新建, 350 行)

替代内存 BM25Index，使用 PostgreSQL 原生 FTS。

**核心方法**:

| 方法 | 说明 |
|------|------|
| `index_document(chunk_id, content)` | 为文档创建 tsvector |
| `batch_index_documents(documents)` | 批量创建全文索引 |
| `search(kb_id, query_text, top_k)` | 全文检索，返回排名结果 |
| `_preprocess_query(query)` | 查询预处理 (中文/英文分词) |
| `_extract_snippet(content, query)` | 提取匹配片段 |

**中文分词策略**:
PostgreSQL 默认 FTS 不支持中文，采用自定义预处理:
- 提取中文字符串和英文单词
- 中文段: 每个字符用 `|` (OR) 连接
- 各段之间用 `&` (AND) 连接

示例: `"如何使用RAG系统"` → `"如 | 何 | 使 | 用 & RAG & 系 | 统"`

**SQL 查询**:
```sql
SELECT c.id, c.content,
       ts_rank(c.search_vector, to_tsquery('simple', :query)) AS rank
FROM chunks c
WHERE c.knowledge_base_id = :kb_id
  AND c.search_vector @@ to_tsquery('simple', :query)
ORDER BY rank DESC
LIMIT :limit
```

#### 4.4 PostgreSQLHybridSearch — 混合检索

**文件**: [app/services/postgres_search.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/postgres_search.py) (同文件)

融合向量搜索和全文检索。

**两种融合策略**:

1. **线性加权融合 (Linear Weighted)**
   ```
   final_score = vector_weight * vector_score + fts_weight * fts_score
   ```
   默认: vector=0.6, fts=0.4

2. **倒数排名融合 (RRF)**
   ```
   score = 1/(k + rank_vector) + 1/(k + rank_fts)
   ```
   默认 k=60

#### 4.5 RetrievalService 重构

**文件**: [app/services/retrieval_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/retrieval_service.py) (重写)

**双模式自动切换**:
```
PostgreSQL 模式:
  查询 → _has_postgres_vectors() → 有向量 → _search_postgres_native()
                                  → 无向量 → _search_fallback() (降级)

SQLite 模式:
  查询 → _search_fallback() (FAISS + BM25)
```

**新增方法**:

| 方法 | 说明 |
|------|------|
| `search(..., force_postgres=False)` | 统一搜索入口，自动选择策略 |
| `_has_postgres_vectors(kb_id)` | 检查是否有 pgvector 数据 |
| `_search_postgres_native(...)` | PostgreSQL 原生混合检索 |
| `_search_fallback(...)` | 降级搜索 (FAISS + BM25 + Keyword) |
| `index_document_for_search(...)` | 为文档块创建双重索引 |
| `batch_index_documents(...)` | 批量创建索引 |
| `initialize_postgres_search(db)` | 初始化 pgvector 扩展 |

**降级模式权重**: Vector=0.50, BM25=0.35, Keyword=0.15

**RetrievedHit** 新增 `search_type` 字段: `"postgres_native"` / `"faiss_bm25"`

**容错**: PostgreSQL 原生检索失败时自动降级到 FAISS + BM25。

#### 4.6 混合检索实际效果

在 SQLite 降级模式下用 6 篇文档测试:

| 查询 | Top-1 文档 | 分数 |
|------|-----------|------|
| 什么是RAG系统 | rag_introduction.txt | 0.6604 |
| vector database comparison FAISS pgvector Milvus | vector_database_comparison.txt | 0.6868 |
| 如何选择嵌入模型 | embedding_models.txt | 0.7314 |
| 深度学习和神经网络有什么关系 | machine_learning_basics.txt | 0.6748 |
| 中文分词工具和NLP处理 | chinese_nlp_techniques.txt | 0.6990 |
| hybrid search ranking BM25 TF-IDF reranking | search_relevance_ranking.txt | 0.7462 |

**6/6 全部正确命中预期文档**

### Phase 4 测试

**文件**:
- [tests/phases/test_phase4_fulltext_search.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase4_fulltext_search.py) — 集成测试 (30 项)
- [tests/phases/test_phase4_hybrid_demo.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase4_hybrid_demo.py) — 效果演示测试 (6/6 命中)

---

## 问题与解决方案汇总

### Phase 1

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 1 | `async_sessionmaker` 报错 `unexpected keyword argument 'execution_options'` | 向 `async_sessionmaker` 传递了 `execution_options` 参数 | 移除该参数 |

### Phase 2

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 2 | 注册接口超时 | Argon2id 内存成本 64MB + 审计中间件 `await request.body()` 阻塞 | 内存成本降至 16MB + 审计日志改为非阻塞 |
| 3 | 审计中间件导致路由获取不到 body | `await request.body()` 消费了请求体 | 移除中间件中的 body 读取 |
| 4 | Token 黑名单测试失败 | 测试用 GET (optional auth)，revoked token 被视为匿名 | 改用 POST (mandatory auth)，正确返回 401 |
| 5 | Token 撤销后仍可用 | `revoke_token_async` 仅写 Redis，内存黑名单延迟 | 先写内存 `_blacklist`，再写 Redis |

### Phase 3

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 6 | KB GET 500 错误 | 缓存数据缺少 `created_at`/`updated_at` 字段 | 在缓存数据中添加这两个字段 |
| 7 | security.py 中 debug print | 调试代码未清理 | 移除 print 语句 |

### Phase 4

| # | 问题 | 原因 | 解决 |
|---|------|------|------|
| 8 | PostgreSQL 容器启动失败 | 服务名为 "postgres" 而非 "pgvector" | 使用正确的服务名 |
| 9 | SQLAlchemy inspect 不兼容 | Mapper 对象 API 版本差异 | 直接访问 `__table__.columns` |
| 10 | SQLite 缺少新列 | 模型更新但数据库未迁移 | 测试脚本自动 ALTER TABLE |
| 11 | User 模型字段名不匹配 | 字段名是 `password_hash` 而非 `hashed_password` | 使用正确字段名 |
| 12 | 密码哈希函数名不匹配 | 函数名是 `hash_password` 而非 `get_password_hash` | 正确导入 |
| 13 | Unicode 字符 GBK 编码错误 | Windows 终端默认 GBK | 替换为 ASCII 字符 |

---

## 测试统计

| Phase | 测试文件 | 通过 | 失败 | 跳过 |
|-------|---------|------|------|------|
| Phase 1 | test_phase1_database_async.py | 全部通过 | 0 | 0 |
| Phase 2 | test_phase2_auth_security.py | 全部通过 | 0 | 0 |
| Phase 3 | test_phase3_infra.py | 30/30 | 0 | 0 |
| Phase 4 | test_phase4_fulltext_search.py | 30 | 0 | 1 (API 需服务) |
| Phase 4 | test_phase4_hybrid_demo.py | 6/6 命中 | 0 | 0 |

---

## 架构总览

### 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI | 异步 API 框架 |
| ORM | SQLAlchemy 2.0 | 异步 ORM |
| 数据库 | PostgreSQL + pgvector | 生产环境 |
| 数据库 | SQLite + aiosqlite | 开发环境 |
| 缓存 | Redis 7 | 分布式缓存 + 限流 |
| 任务队列 | Celery | 后台异步任务 |
| 向量检索 | pgvector / FAISS | 双模式向量搜索 |
| 全文检索 | PostgreSQL FTS / BM25 | 双模式文本搜索 |
| 密码哈希 | Argon2id | 抗 GPU/ASIC |
| Token | PyJWT | 双 Token 机制 |

### 分层架构

```
┌─────────────────────────────────────────────────┐
│                   API 层 (FastAPI)                │
│  auth.py | knowledge_base.py | document.py | ...  │
├─────────────────────────────────────────────────┤
│                  中间件层                         │
│  RateLimitMiddleware | AuditLogMiddleware | ...   │
├─────────────────────────────────────────────────┤
│                  Service 层                       │
│  KnowledgeBaseService | DocumentService |        │
│  RetrievalService (双模式自动切换)                │
├─────────────────────────────────────────────────┤
│                  处理器层                         │
│  EmbeddingService | VectorStoreManager |          │
│  PgVectorStore | PgFullTextSearch |              │
│  PostgreSQLHybridSearch                           │
├─────────────────────────────────────────────────┤
│                  基础设施层                        │
│  Redis (缓存+限流) | Celery (异步任务) |           │
│  Security (Argon2id+JWT) | Cache (Cache-Aside)   │
├─────────────────────────────────────────────────┤
│                  数据层                           │
│  PostgreSQL + pgvector | SQLite (降级) |           │
│  Redis (3个db) | FAISS (文件存储)                │
└─────────────────────────────────────────────────┘
```

### Redis 使用规划

| DB | 用途 |
|----|------|
| 0 | 缓存 (kb/user_perm/embed) + 限流 (ZSET) + 黑名单 |
| 1 | Celery Broker (任务队列) |
| 2 | Celery Backend (任务结果) |

### 检索策略

```
PostgreSQL 模式 (生产):
  查询 → pgvector 向量搜索 + PostgreSQL FTS 全文检索 → 混合融合

SQLite 模式 (开发):
  查询 → FAISS 向量搜索 + BM25 文本搜索 + 关键词匹配 → 三路融合
  权重: Vector=0.50, BM25=0.35, Keyword=0.15
```

---

## 文件变更清单

### 新建文件 (Phase 3-4)

| 文件 | Phase | 说明 |
|------|-------|------|
| app/core/redis.py | 3 | Redis 连接管理 + 优雅降级 |
| app/core/cache.py | 3 | Cache-Aside 缓存层 |
| app/core/celery_app.py | 3 | Celery 应用配置 |
| app/tasks/audit_tasks.py | 3 | 审计日志 Celery 任务 |
| app/tasks/document_tasks.py | 3 | 文档处理 Celery 任务 |
| app/services/pg_vector_store.py | 4 | PgVectorStore 向量存储 |
| app/services/postgres_search.py | 4 | FTS + 混合检索 |
| tests/phases/test_phase1_database_async.py | 1 | Phase 1 测试 |
| tests/phases/test_phase2_auth_security.py | 2 | Phase 2 测试 |
| tests/phases/test_phase3_infra.py | 3 | Phase 3 测试 |
| tests/phases/test_phase4_fulltext_search.py | 4 | Phase 4 测试 |
| tests/phases/test_phase4_hybrid_demo.py | 4 | 混合检索效果演示 |

### 修改文件 (Phase 1-4)

| 文件 | Phase | 改动 |
|------|-------|------|
| app/models/database.py | 1+3 | 异步引擎 + 连接池优化 + pgvector 扩展 |
| app/models/entities/document.py | 1+4 | DocumentChunk 模型 + embedding/search_vector 列 |
| app/models/entities/auth.py | 2 | RefreshToken/Role/Permission/AuditLog |
| app/core/security.py | 2+3 | Argon2id + 双Token + Redis 黑名单 |
| app/core/middleware/rate_limit.py | 2+3 | 限流中间件 → Redis 分布式 |
| app/core/middleware/audit.py | 2+3 | 审计中间件 → Celery 异步 |
| app/services/kb_service.py | 3 | Redis 缓存集成 |
| app/services/retrieval_service.py | 4 | 双模式检索重构 |
| app/api/v1/document.py | 3 | 大文件 Celery 异步处理 |
