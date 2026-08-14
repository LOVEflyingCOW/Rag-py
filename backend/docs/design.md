# RAG 系统工业级升级架构设计文档

> **版本**: v2.0 | **日期**: 2026-08-14 | **作者**: 架构升级方案

---

## 目录

- [1. 现状分析与目标](#1-现状分析与目标)
- [2. 目标架构总览](#2-目标架构总览)
- [3. Phase 1 — 数据库迁移 + 全链路异步化](#3-phase-1--数据库迁移--全链路异步化)
- [4. Phase 2 — 鉴权安全体系](#4-phase-2--鉴权安全体系)
- [5. Phase 3 — 性能 + 缓存 + 消息队列](#5-phase-3--性能--缓存--消息队列)
- [6. Phase 4 — RAG 质量增强](#6-phase-4--rag-质量增强)
- [7. Phase 5 — 可观测性 + 安全增强](#7-phase-5--可观测性--安全增强)
- [8. Phase 6 — CI/CD + 测试平台](#8-phase-6--cicd--测试平台)
- [9. Docker Compose 编排设计](#9-docker-compose-编排设计)
- [10. 技术选型汇总](#10-技术选型汇总)

---

## 1. 现状分析与目标

### 1.1 当前架构短板

```
当前架构（MVP 级）
┌─────────────────────────────────────────┐
│            FastAPI (同步)                │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌────────┐  │
│  │ Auth │ │  KB  │ │ Chat │ │ Agent  │  │
│  └──┬───┘ └──┬───┘ └──┬───┘ └───┬────┘  │
│     │        │        │          │       │
│  ┌──▼────────▼────────▼──────────▼────┐  │
│  │     SQLAlchemy (同步, SQLite)      │  │
│  └────────────────────────────────────┘  │
│  ┌────────────────────────────────────┐ │
│  │  FAISS (内存索引, pickle 持久化)     │ │
│  └────────────────────────────────────┘ │
│  ┌─────────────┐  ┌──────────────────┐  │
│  │ Mock/HTTP   │  │ Mock/Remote      │  │
│  │ LLM Provider│  │ Embedding        │  │
│  └─────────────┘  └──────────────────┘  │
└─────────────────────────────────────────┘
```

**核心问题：**

| 层级 | 问题 | 影响 |
|------|------|------|
| 数据库 | SQLite 单文件, 不支持并发写 | 高并发写入锁死 |
| 数据库 | 无 Alembic 迁移 | schema 变更需删库重建 |
| 数据库 | 全同步 SQLAlchemy | 阻塞事件循环 |
| 鉴权 | 单 Token, 无 Refresh | 过期需重新登录 |
| 鉴权 | 无 Token 撤销机制 | 登出后 Token 仍有效 |
| 鉴权 | 仅 is_admin 布尔值 | 无法做细粒度权限 |
| 鉴权 | 无限流 | 易被 DDoS/爬虫 |
| 性能 | 无 Redis 缓存 | 重复计算, 响应慢 |
| 性能 | 无消息队列 | 文档处理阻塞请求 |
| 可观测 | 无结构化日志 | 无法聚合检索 |
| 可观测 | 无监控指标 | 无法发现性能瓶颈 |
| RAG | 无 Reranker | 检索精度有限 |
| RAG | 无混合检索 | 语义+关键词无法兼顾 |

### 1.2 目标

```
目标架构（工业级）
┌──────────────────────────────────────────────────┐
│              FastAPI (全异步 async/await)          │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌────────┐ │
│  │ Auth │ │  KB  │ │ Chat │ │Agent │ │Integra.│ │
│  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘ └───┬────┘ │
│     │        │        │        │         │      │
│  ┌──▼────────▼────────▼────────▼─────────▼────┐  │
│  │     中间件层 (限流/审计/链路追踪/脱敏)       │  │
│  └────────────────────────────────────────────┘  │
│     │        │        │        │         │      │
│  ┌──▼──┐ ┌──▼──┐ ┌──▼──┐ ┌──▼──────────▼────┐  │
│  │Redis│ │PG主 │ │Rabbit│ │ Prometheus/OTel  │  │
│  │Cache│ │pgvec│ │MQ    │ │ 监控/追踪         │  │
│  └─────┘ └─────┘ └──────┘ └──────────────────┘  │
└──────────────────────────────────────────────────┘
```

---

## 2. 目标架构总览

### 2.1 技术栈

| 层级 | 当前 | 目标 | 理由 |
|------|------|------|------|
| Web 框架 | FastAPI (同步) | FastAPI (全异步) | 原生 async 支持 |
| ORM | SQLAlchemy 2.0 同步 | SQLAlchemy 2.0 async | 非阻塞 IO |
| 数据库 | SQLite | PostgreSQL 16 + pgvector | 并发写, 网络访问, 向量扩展 |
| 迁移 | 无 | Alembic | schema 版本管理 |
| 缓存 | 内存 LRU | Redis 7 + 本地 LRU | 分布式缓存, 多实例共享 |
| 消息队列 | 无 | RabbitMQ + Celery | 异步任务, 重试, 死信 |
| 向量存储 | FAISS IndexFlatIP | pgvector HNSW | 事务一致性, 不需要额外服务 |
| 鉴权 | 手写 JWT + PBKDF2 | PyJWT + RS256 + Argon2id | 安全标准 |
| 限流 | 无 | Redis-Lua 滑动窗口 | 原子操作, 分布式 |
| 监控 | 无 | Prometheus + Grafana | 指标采集 + 可视化 |
| 追踪 | 无 | OpenTelemetry | 全链路追踪 |
| 日志 | RotatingFileHandler | structlog + JSON | 结构化, 可聚合 |
| 部署 | 手动 | Docker Compose | 单机多容器编排 |

### 2.2 项目目录结构变更

```
backend/
├── app/
│   ├── core/
│   │   ├── config.py          # → 扩展配置项
│   │   ├── security.py        # → 重写: Argon2id + PyJWT + RS256
│   │   ├── logging.py         # → structlog JSON 日志
│   │   ├── exceptions.py      # 保持, 增加异常分类
│   │   └── middleware/        # 新增: 中间件目录
│   │       ├── rate_limit.py  # 限流中间件
│   │       ├── audit.py       # 审计日志中间件
│   │       ├── tracing.py     # 链路追踪中间件
│   │       ├── desensitize.py # 数据脱敏中间件
│   │       └── security.py    # 安全头/CSRF/请求大小限制
│   ├── models/
│   │   ├── database.py        # → async engine + AsyncSession
│   │   ├── entities/          # → 增加 Role, Permission, AuditLog, Tenant
│   │   └── schemas/           # → 增加 Token 相关 schema
│   ├── api/
│   │   ├── v1/                # 保持, 逐步改 async
│   │   └── v2/                # 新增: v2 版本路由 (渐进迁移)
│   ├── services/              # → 改 async, 增加 cache_service
│   ├── processors/
│   │   ├── llm/               # → 改 httpx async
│   │   ├── embedding/         # → 增加 Reranker
│   │   ├── retrieval/         # → 改 pgvector 后端
│   │   └── document/          # → 增加多策略分块
│   └── tasks/                 # 新增: Celery 异步任务
│       ├── celery_app.py
│       ├── document_tasks.py  # 文档处理任务
│       └── embedding_tasks.py # 批量向量化任务
├── alembic/                   # 新增: 数据库迁移
│   ├── env.py
│   └── versions/
├── tests/                     # 新增: 规范化测试目录
│   ├── unit/
│   ├── integration/
│   └── conftest.py
├── docker/
│   ├── Dockerfile             # 多阶段构建
│   └── docker-compose.yml     # 编排文件
├── alembic.ini
├── requirements.txt           # → 更新依赖
└── pyproject.toml             # 新增: ruff/mypy 配置
```

---

## 3. Phase 1 — 数据库迁移 + 全链路异步化

### 3.1 PostgreSQL + pgvector

#### 3.1.1 Docker Compose 数据库服务

```yaml
# docker/docker-compose.yml (Phase 1 部分)
services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: rag-postgres
    environment:
      POSTGRES_DB: rag_system
      POSTGRES_USER: rag_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-rag_dev_password}
    ports:
      - "5432:5432"
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U rag_user -d rag_system"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  pg_data:
```

#### 3.1.2 数据库连接配置

```python
# app/core/config.py 变更
class Settings(BaseSettings):
    # PostgreSQL
    DATABASE_URL: str = "postgresql+asyncpg://rag_user:rag_dev_password@localhost:5432/rag_system"
    DATABASE_SYNC_URL: str = "postgresql+psycopg2://rag_user:rag_dev_password@localhost:5432/rag_system"  # Alembic 用

    # 连接池
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 3600
```

#### 3.1.3 async Engine + AsyncSession

```python
# app/models/database.py 重写
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.APP_DEBUG,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_db_dep() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

#### 3.1.4 Entity 改 async 兼容

```python
# app/models/entities/user.py — 仅增加 tenant_id
class User(Base):
    # ... 现有字段 ...
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True, index=True)

# app/models/entities/tenant.py — 新增
class Tenant(Base):
    """多租户表"""
    __tablename__ = "tenants"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False)
    plan = Column(String(50), default="free")  # free/pro/enterprise
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
```

#### 3.1.5 向量存储切换 pgvector

```python
# app/processors/retrieval/pgvector_store.py — 新增
from pgvector.sqlalchemy import Vector

class VectorChunk(Base):
    """向量分块表 — 替代 FAISS 的 pickle 文件"""
    __tablename__ = "vector_chunks"
    id = Column(Integer, primary_key=True)
    knowledge_base_id = Column(Integer, ForeignKey("knowledge_bases.id"), index=True)
    document_id = Column(Integer, ForeignKey("documents.id"), index=True)
    chunk_id = Column(Integer, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(384))  # pgvector 向量列
    metadata_ = Column("metadata", JSON, default=dict)

    __table_args__ = (
        Index("ivfflat_chunks_embedding", "embedding",
              postgresql_using="ivfflat",
              postgresql_with={"lists": 100},
              postgreslymal_ops="vector_cosine_ops"),
    )
```

```python
# 向量搜索 SQL
SELECT chunk_id, content, 1 - (embedding <=> :query_vec) AS score
FROM vector_chunks
WHERE knowledge_base_id = :kb_id
ORDER BY embedding <=> :query_vec
LIMIT :top_k;
```

### 3.2 Alembic 迁移

```bash
# 初始化
alembic init alembic

# 配置 alembic.ini
# sqlalchemy.url = postgresql+psycopg2://rag_user:rag_dev_password@localhost:5432/rag_system

# 首次迁移（从现有 model 生成）
alembic revision --autogenerate -m "initial schema with pgvector"

# 后续迁移
alembic upgrade head
```

### 3.3 全链路 async 改造

改造规则（逐文件）：

| 文件 | 当前 | 目标 |
|------|------|------|
| `app/api/v1/*.py` 所有路由 | `def` | `async def` |
| `app/api/dependencies.py` | `def get_db_dep` | `async def get_db_dep` |
| `app/services/*.py` | `def method()` | `async def method()` |
| `db.query(User).first()` | sync query | `await db.execute(select(User)); result.scalars().first()` |
| `db.commit()` | sync | `await db.commit()` |
| `requests.post()` (LLM) | sync | `httpx.AsyncClient().post()` |

**改造示例 — auth.py：**

```python
# 改造前
@router.post("/register")
def register(payload: UserRegister, db: Session = Depends(get_db_dep)):
    existing = db.query(User).filter(User.username == payload.username).first()
    ...

# 改造后
@router.post("/register")
async def register(payload: UserRegister, db: AsyncSession = Depends(get_db_dep)):
    result = await db.execute(select(User).where(User.username == payload.username))
    existing = result.scalars().first()
    ...
```

### 3.4 依赖更新

```
# requirements.txt 新增
asyncpg>=0.29
psycopg2-binary>=2.9
alembic>=1.13
pgvector>=0.3
sqlalchemy[asyncio]>=2.0
```

---

## 4. Phase 2 — 鉴权安全体系

### 4.1 双 Token 机制

```
登录流程:
  POST /auth/login
    → 返回 { access_token (15min), refresh_token (7d) }

请求流程:
  Authorization: Bearer <access_token>
    → 过期 → 401

自动刷新:
  POST /auth/refresh
  Body: { "refresh_token": "<refresh_token>" }
    → 返回新的 { access_token, refresh_token }  (Refresh 轮换, 旧的失效)

登出:
  POST /auth/logout
    → Access Token + Refresh Token 同时加入 Redis 黑名单
```

#### 数据模型变更

```python
# app/models/entities/auth.py — 新增

class RefreshToken(Base):
    """Refresh Token 表 — 支持轮换和撤销"""
    __tablename__ = "refresh_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(128), unique=True, nullable=False)  # SHA-256 摘要
    device_info = Column(String(500), nullable=True)  # User-Agent
    ip_address = Column(String(45), nullable=True)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)  # 非空 = 已撤销
    created_at = Column(DateTime, server_default=func.now())
```

```python
# app/core/security.py — 重写

import argon2
from argon2 import PasswordHasher
import jwt  # PyJWT

_ph = PasswordHasher(
    time_cost=3,       # 迭代次数
    memory_cost=65536, # 64MB
    parallelism=4,     # 并行线程
)

def hash_password(password: str) -> str:
    return _ph.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, password)
    except argon2.exceptions.VerifyMismatchError:
        return False

# JWT — RS256 非对称签名
def create_access_token(user_id: int, username: str, roles: list) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "roles": roles,
        "type": "access",
        "iat": int(time.time()),
        "exp": int(time.time()) + 900,  # 15min
    }
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")

def create_refresh_token(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": uuid.uuid4().hex,  # 唯一 ID, 用于撤销
        "iat": int(time.time()),
        "exp": int(time.time()) + 604800,  # 7d
    }
    return jwt.encode(payload, PRIVATE_KEY, algorithm="RS256")
```

### 4.2 RBAC 角色权限

```python
# app/models/entities/rbac.py — 新增

class Role(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)  # admin / editor / viewer
    description = Column(String(200))
    permissions = relationship("Permission", secondary="role_permissions")

class Permission(Base):
    __tablename__ = "permissions"
    id = Column(Integer, primary_key=True)
    resource = Column(String(50), nullable=False)  # kb / document / conversation
    action = Column(String(50), nullable=False)    # create / read / update / delete
    description = Column(String(200))

# 关联表
role_permissions = Table(
    "role_permissions", Base.metadata,
    Column("role_id", Integer, ForeignKey("roles.id")),
    Column("permission_id", Integer, ForeignKey("permissions.id")),
)

user_roles = Table(
    "user_roles", Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id")),
    Column("role_id", Integer, ForeignKey("roles.id")),
)

# User 表增加关系
class User(Base):
    # ... 现有字段 ...
    roles = relationship("Role", secondary=user_roles, lazy="selectin")
```

```python
# 权限校验依赖
def require_permission(resource: str, action: str):
    async def checker(
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db_dep),
    ) -> User:
        if current_user.is_admin:
            return current_user
        for role in current_user.roles:
            for perm in role.permissions:
                if perm.resource == resource and perm.action == action:
                    return current_user
        raise HTTPException(403, f"需要 {resource}:{action} 权限")
    return checker

# 使用
@router.delete("/{kb_id}")
async def delete_kb(
    kb_id: int,
    user: User = Depends(require_permission("kb", "delete")),
    db: AsyncSession = Depends(get_db_dep),
):
    ...
```

### 4.3 API 限流

```python
# app/core/middleware/rate_limit.py — 新增

import redis.asyncio as redis
from datetime import datetime

class RateLimiter:
    """Redis 滑动窗口限流"""
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def check(self, key: str, limit: int, window: int = 60) -> bool:
        """滑动窗口限流
        key: 限流维度 (ip:1.2.3.4 或 user:42)
        limit: 窗口内最大请求数
        window: 窗口大小(秒)
        """
        now = time.time()
        pipe = self.redis.pipeline()
        # Lua 脚本保证原子性
        script = """
        local key = KEYS[1]
        local now = tonumber(ARGV[1])
        local window = tonumber(ARGV[2])
        local limit = tonumber(ARGV[3])
        -- 移除窗口外的记录
        redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
        -- 当前计数
        local count = redis.call('ZCARD', key)
        if count < limit then
            redis.call('ZADD', key, now, now .. '-' .. math.random(1,1000000))
            redis.call('EXPIRE', key, window)
            return 1
        else
            return 0
        end
        """
        allowed = await self.redis.eval(script, 1, key, now, window, limit)
        return bool(allowed)

# 限流配置
RATE_LIMITS = {
    "anonymous": {"limit": 30, "window": 60},      # 匿名: 30/min
    "authenticated": {"limit": 120, "window": 60}, # 已认证: 120/min
    "premium": {"limit": 600, "window": 60},        # 付费: 600/min
}
```

### 4.4 审计日志

```python
# app/models/entities/audit.py — 新增

class AuditLog(Base):
    """审计日志表"""
    __tablename__ = "audit_logs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, nullable=True, index=True)
    username = Column(String(100), nullable=True)
    method = Column(String(10), nullable=False)   # GET/POST/PUT/DELETE
    path = Column(String(500), nullable=False, index=True)
    status_code = Column(Integer, nullable=False)
    ip_address = Column(String(45))
    user_agent = Column(String(500))
    request_body = Column(Text, nullable=True)    # 脱敏后的请求体
    response_time_ms = Column(Integer)
    created_at = Column(DateTime, server_default=func.now(), index=True)
```

### 4.5 请求防护中间件

```python
# app/core/middleware/security.py — 新增

async def security_middleware(request: Request, call_next):
    # 1. 请求体大小限制 (10MB)
    if request.headers.get("content-length"):
        if int(request.headers["content-length"]) > 10 * 1024 * 1024:
            return JSONResponse(413, {"detail": "请求体过大"})

    # 2. 安全响应头
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000"

    # 3. IP 黑名单检查
    client_ip = request.client.host
    if await is_ip_blacklisted(client_ip):
        return JSONResponse(403, {"detail": "IP 已被封禁"})

    return response
```

---

## 5. Phase 3 — 性能 + 缓存 + 消息队列

### 5.1 Redis 多级缓存

```python
# app/services/cache_service.py — 新增

class CacheService:
    """多级缓存: L1(本地 LRU) + L2(Redis)"""

    def __init__(self, redis_client, local_max=1000):
        self.redis = redis_client
        self.local = OrderedDict()  # L1 本地缓存
        self.local_max = local_max

    async def get(self, key: str):
        # L1
        if key in self.local:
            self.local.move_to_end(key)
            return self.local[key]
        # L2
        val = await self.redis.get(f"cache:{key}")
        if val:
            data = json.loads(val)
            self._set_local(key, data)
            return data
        return None

    async def set(self, key: str, value, ttl: int = 300):
        self._set_local(key, value)
        await self.redis.setex(f"cache:{key}", ttl, json.dumps(value))

    # 缓存击穿防护 — 互斥锁
    async def get_or_set(self, key: str, factory, ttl: int = 300):
        val = await self.get(key)
        if val is not None:
            return val
        # 获取互斥锁, 只让一个请求回源
        lock_key = f"lock:{key}"
        acquired = await self.redis.set(lock_key, "1", nx=True, ex=10)
        if acquired:
            try:
                val = await factory()
                await self.set(key, val, ttl)
                return val
            finally:
                await self.redis.delete(lock_key)
        else:
            # 等待其他请求填充缓存
            await asyncio.sleep(0.1)
            return await self.get_or_set(key, factory, ttl)
```

### 5.2 Celery + RabbitMQ 异步任务

```python
# app/tasks/celery_app.py — 新增

from celery import Celery

celery_app = Celery(
    "rag_system",
    broker="amqp://rag_user:rag_password@localhost:5672/rag_vhost",
    backend="redis://localhost:6379/1",
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_acks_late=True,           # 任务完成后才 ACK
    task_reject_on_worker_lost=True, # Worker 崩溃时拒绝任务
    task_default_retry_delay=60,    # 重试间隔
    task_max_retries=3,             # 最大重试
)

# 死信队列
celery_app.conf.task_routes = {
    "app.tasks.document_tasks.*": {"queue": "document"},
    "app.tasks.embedding_tasks.*": {"queue": "embedding"},
}
```

```python
# app/tasks/document_tasks.py — 新增

@celery_app.task(bind=True, name="process_document")
def process_document_task(self, doc_id: int, kb_id: int, file_path: str):
    """异步文档处理: 解析 → 分块 → 向量化 → 入库"""
    try:
        # 1. 文本提取
        # 2. 分块
        # 3. 向量化
        # 4. pgvector 入库
        # 5. 更新文档状态
        ...
    except Exception as exc:
        # 自动重试, 最多 3 次
        raise self.retry(exc=exc)
```

### 5.3 请求幂等性

```python
# app/core/middleware/idempotency.py — 新增

async def idempotency_middleware(request: Request, call_next):
    if request.method in ("POST", "PUT", "PATCH"):
        idem_key = request.headers.get("Idempotency-Key")
        if idem_key:
            # Redis 检查是否已处理
            cached = await redis.get(f"idem:{idem_key}")
            if cached:
                return JSONResponse(200, json.loads(cached))
            # 执行请求并缓存结果
            response = await call_next(request)
            body = await response.body()
            await redis.setex(f"idem:{idem_key}", 3600, body)
            return response
    return await call_next(request)
```

---

## 6. Phase 4 — RAG 质量增强

### 6.1 混合检索

```python
# app/processors/retrieval/hybrid_search.py — 新增

class HybridRetriever:
    """向量检索 + BM25 关键词检索, 加权融合"""

    def __init__(self, db: AsyncSession, embedding_service):
        self.db = db
        self.embedding = embedding_service
        self.weights = {"vector": 0.7, "bm25": 0.3}

    async def search(self, kb_id: int, query: str, top_k: int = 5):
        # 1. 向量检索 (pgvector)
        query_vec = await self.embedding.encode_single_async(query)
        vector_results = await self._vector_search(kb_id, query_vec, top_k * 2)

        # 2. BM25 检索 (PostgreSQL full-text search)
        bm25_results = await self._bm25_search(kb_id, query, top_k * 2)

        # 3. RRF (Reciprocal Rank Fusion) 融合
        fused = self._rrf_fusion(vector_results, bm25_results)
        return fused[:top_k]

    def _rrf_fusion(self, vec_results, bm25_results, k=60):
        """Reciprocal Rank Fusion 融合两路检索结果"""
        scores = {}
        for rank, item in enumerate(vec_results):
            cid = item["chunk_id"]
            scores[cid] = scores.get(cid, 0) + 1 / (k + rank + 1)
        for rank, item in enumerate(bm25_results):
            cid = item["chunk_id"]
            scores[cid] = scores.get(cid, 0) + 1 / (k + rank + 1)
        # 按融合分数排序
        return sorted(scores.items(), key=lambda x: -x[1])
```

### 6.2 Reranker

```python
# app/processors/retrieval/reranker.py — 新增

class CrossEncoderReranker:
    """Cross-encoder 重排序器
    
    使用 bge-reranker-large 模型对检索结果二次排序
    安装: pip install sentence-transformers
    """

    def __init__(self, model_name="BAAI/bge-reranker-large"):
        self.model = None  # 懒加载

    async def rerank(self, query: str, documents: list, top_k: int = 5):
        if not documents:
            return []

        # 懒加载模型
        if self.model is None:
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(model_name)

        # 构建 pairs
        pairs = [(query, doc["content"]) for doc in documents]
        scores = self.model.predict(pairs)

        # 按重排分数排序
        ranked = sorted(zip(documents, scores), key=lambda x: -x[1])
        return [doc for doc, score in ranked[:top_k]]
```

### 6.3 多策略分块

```python
# app/processors/document/chunker.py — 新增

class ChunkerFactory:
    """分块器工厂"""

    @staticmethod
    def create(strategy: str = "recursive", **kwargs):
        if strategy == "recursive":
            return RecursiveCharacterChunker(
                chunk_size=kwargs.get("chunk_size", 500),
                chunk_overlap=kwargs.get("chunk_overlap", 50),
                separators=["\n\n", "\n", "。", ".", " "],
            )
        elif strategy == "semantic":
            return SemanticChunker(
                embedding_service=kwargs["embedding_service"],
                threshold=0.5,
            )
        elif strategy == "markdown":
            return MarkdownStructureChunker(
                chunk_size=kwargs.get("chunk_size", 500),
            )
        else:
            raise ValueError(f"未知分块策略: {strategy}")
```

### 6.4 RAG 评估模块

```python
# app/services/evaluation_service.py — 新增

class RAGEvaluator:
    """RAG 质量评估"""

    async def evaluate(self, kb_id: int, test_cases: list) -> dict:
        """
        test_cases: [{"query": "...", "expected_keywords": [...], "expected_sources": [...]}]
        
        返回: {
            "recall_rate": 0.85,      # 召回率
            "precision": 0.92,         # 准确率
            "avg_latency_ms": 120,     # 平均延迟
            "hallucination_rate": 0.05, # 幻觉率
            "details": [...]
        }
        """
        results = []
        for tc in test_cases:
            # 执行 RAG 查询
            rag_result = await self.pipeline.answer(kb_id, tc["query"])
            # 评估关键词命中
            kw_hits = [kw for kw in tc["expected_keywords"]
                       if kw.lower() in rag_result.llm_answer.lower()]
            results.append({
                "query": tc["query"],
                "answer": rag_result.llm_answer,
                "keyword_hit_rate": len(kw_hits) / len(tc["expected_keywords"]),
                "latency_ms": rag_result.latency_ms,
                "retrieved_chunks": len(rag_result.retrieved_chunks),
            })
        # 汇总
        return {
            "recall_rate": sum(r["keyword_hit_rate"] for r in results) / len(results),
            "avg_latency_ms": sum(r["latency_ms"] for r in results) / len(results),
            "details": results,
        }
```

---

## 7. Phase 5 — 可观测性 + 安全增强

### 7.1 结构化日志

```python
# app/core/logging.py — 重写

import structlog

def setup_logging():
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,    # 合并 trace_id
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),        # JSON 输出
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
    )
    return structlog.get_logger()

# 每个请求自动注入 trace_id
async def tracing_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-ID") or uuid.uuid4().hex
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        trace_id=trace_id,
        user_id=None,
        method=request.method,
        path=request.url.path,
    )
    response = await call_next(request)
    response.headers["X-Trace-ID"] = trace_id
    return response
```

### 7.2 Prometheus 指标

```python
# app/core/metrics.py — 新增

from prometheus_client import Counter, Histogram, Gauge, generate_latest

# 指标定义
HTTP_REQUEST_COUNT = Counter(
    "rag_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"]
)
HTTP_REQUEST_LATENCY = Histogram(
    "rag_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"]
)
RAG_QUERY_COUNT = Counter(
    "rag_queries_total",
    "Total RAG queries",
    ["kb_id", "provider"]
)
RAG_QUERY_LATENCY = Histogram(
    "rag_query_duration_seconds",
    "RAG query duration",
    ["kb_id"]
)
ACTIVE_USERS = Gauge(
    "rag_active_users",
    "Active users in last 5 min"
)

# 暴露端点
@router.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type="text/plain")
```

### 7.3 OpenTelemetry 链路追踪

```python
# app/core/tracing.py — 新增

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

def setup_tracing():
    provider = TracerProvider()
    otlp_exporter = OTLPSpanExporter(
        endpoint="http://localhost:4317"  # Jaeger/Tempo
    )
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(provider)

# 自动注入 span
@router.post("/chat/message")
async def chat_message(payload: ChatRequest):
    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("rag_chat") as span:
        span.set_attribute("kb_id", payload.knowledge_base_id)
        span.set_attribute("query", payload.message[:100])
        # 检索 span
        with tracer.start_as_current_span("vector_search"):
            chunks = await pipeline.search(...)
        # 生成 span
        with tracer.start_as_current_span("llm_generate"):
            answer = await pipeline.generate(...)
```

### 7.4 OAuth2 三方登录

```python
# app/api/v1/oauth.py — 新增

@router.get("/oauth/{provider}/login")
async def oauth_login(provider: str, request: Request):
    """重定向到三方登录页"""
    states = {"github": GITHUB_AUTH_URL, "google": GOOGLE_AUTH_URL}
    state = generate_state()
    await redis.setex(f"oauth:state:{state}", 300, provider)
    return RedirectResponse(f"{states[provider]}?client_id=...&state={state}")

@router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str, code: str, state: str, db = Depends(get_db_dep)):
    """三方登录回调"""
    # 1. 校验 state
    # 2. 换取 access_token
    # 3. 获取用户信息
    # 4. 查找或创建本地用户
    # 5. 返回双 Token
    ...
```

### 7.5 健康探针分离

```python
# app/api/health.py — 增强

@router.get("/health/liveness")
async def liveness():
    """存活探针 — 进程是否在运行"""
    return {"status": "alive"}

@router.get("/health/readiness")
async def readiness(db = Depends(get_db_dep)):
    """就绪探针 — 是否可以接收流量"""
    checks = {}
    # DB
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "fail"
    # Redis
    try:
        await redis.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "fail"
    # RabbitMQ
    try:
        celery_app.connection().ensure_connection(max_retries=1)
        checks["rabbitmq"] = "ok"
    except Exception:
        checks["rabbitmq"] = "fail"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "not_ready", "checks": checks}
    )
```

---

## 8. Phase 6 — CI/CD + 测试平台

### 8.1 Docker 多阶段构建

```dockerfile
# docker/Dockerfile
FROM python:3.11-slim as builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

FROM python:3.11-slim as runtime
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health/liveness || exit 1
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 8.2 GitHub Actions

```yaml
# .github/workflows/ci.yml
name: CI
on: [push, pull_request]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install ruff mypy
      - run: ruff check app/
      - run: mypy app/ --ignore-missing-imports

  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env: { POSTGRES_PASSWORD: test, POSTGRES_DB: rag_test }
        ports: ["5432:5432"]
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: pytest tests/ -v --cov=app

  docker:
    needs: [lint, test]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: docker/build-push-action@v5
        with:
          context: .
          file: docker/Dockerfile
          push: false
          tags: rag-system:${{ github.sha }}
```

### 8.3 测试目录结构

```
tests/
├── conftest.py              # 公共 fixture
├── unit/                    # 单元测试
│   ├── test_security.py
│   ├── test_vector_store.py
│   ├── test_chunker.py
│   └── test_llm_service.py
├── integration/             # 集成测试
│   ├── test_auth_flow.py
│   ├── test_rag_pipeline.py
│   ├── test_agent.py
│   └── test_rate_limit.py
├── api/                     # API 契约测试
│   ├── test_kb_api.py
│   ├── test_chat_api.py
│   └── test_oauth.py
└── load/                    # 性能/负载测试
    └── test_concurrent.py
```

---

## 9. Docker Compose 编排设计

```yaml
# docker/docker-compose.yml — 完整版
version: "3.9"

services:
  # ---- API 服务 ----
  api:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    container_name: rag-api
    ports:
      - "8000:8000"
    environment:
      - DATABASE_URL=postgresql+asyncpg://rag_user:rag_dev_password@postgres:5432/rag_system
      - REDIS_URL=redis://redis:6379/0
      - CELERY_BROKER=amqp://rag_user:rag_password@rabbitmq:5672/rag_vhost
      - LLM_PROVIDER=${LLM_PROVIDER:-mock}
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      rabbitmq: { condition: service_healthy }
    volumes:
      - ../data:/app/data
    restart: unless-stopped

  # ---- PostgreSQL + pgvector ----
  postgres:
    image: pgvector/pgvector:pg16
    container_name: rag-postgres
    environment:
      POSTGRES_DB: rag_system
      POSTGRES_USER: rag_user
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-rag_dev_password}
    ports: ["5432:5432"]
    volumes:
      - pg_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U rag_user"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ---- Redis ----
  redis:
    image: redis:7-alpine
    container_name: rag-redis
    ports: ["6379:6379"]
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5

  # ---- RabbitMQ ----
  rabbitmq:
    image: rabbitmq:3-management-alpine
    container_name: rag-rabbitmq
    environment:
      RABBITMQ_DEFAULT_USER: rag_user
      RABBITMQ_DEFAULT_PASS: rag_password
      RABBITMQ_DEFAULT_VHOST: rag_vhost
    ports:
      - "5672:5672"
      - "15672:15672"  # 管理界面
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # ---- Celery Worker ----
  worker:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    container_name: rag-worker
    command: celery -A app.tasks.celery_app worker --loglevel=info --concurrency=4
    environment:
      - DATABASE_URL=postgresql+asyncpg://rag_user:rag_dev_password@postgres:5432/rag_system
      - REDIS_URL=redis://redis:6379/0
      - CELERY_BROKER=amqp://rag_user:rag_password@rabbitmq:5672/rag_vhost
    depends_on:
      rabbitmq: { condition: service_healthy }
      postgres: { condition: service_healthy }
    volumes:
      - ../data:/app/data
    restart: unless-stopped

  # ---- Prometheus (可选, Phase 5) ----
  prometheus:
    image: prom/prometheus:latest
    container_name: rag-prometheus
    ports: ["9090:9090"]
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml

  # ---- Grafana (可选, Phase 5) ----
  grafana:
    image: grafana/grafana:latest
    container_name: rag-grafana
    ports: ["3000:3000"]
    volumes:
      - grafana_data:/var/lib/grafana

volumes:
  pg_data:
  redis_data:
  rabbitmq_data:
  grafana_data:
```

---

## 10. 技术选型汇总

| 组件 | 选型 | 版本 | 备注 |
|------|------|------|------|
| Web 框架 | FastAPI | >=0.100 | 全异步 |
| ORM | SQLAlchemy async | >=2.0 | AsyncSession |
| DB 驱动 | asyncpg | >=0.29 | 异步 PG 驱动 |
| 数据库 | PostgreSQL | 16 | + pgvector 扩展 |
| 向量扩展 | pgvector | >=0.3 | HNSW 索引 |
| 迁移 | Alembic | >=1.13 | 自动生成 + 手动调整 |
| 缓存 | Redis | 7 | 多级缓存 L1+L2 |
| 消息队列 | RabbitMQ | 3 | 死信队列 + 幂等 |
| 任务队列 | Celery | >=5.3 | 异步文档处理 |
| 密码哈希 | argon2-cffi | >=23.1 | Argon2id |
| JWT | PyJWT | >=2.8 | RS256 非对称 |
| 限流 | Redis-Lua | - | 滑动窗口 |
| 监控 | Prometheus + Grafana | - | 指标 + 可视化 |
| 追踪 | OpenTelemetry | >=1.20 | 全链路 |
| 日志 | structlog | >=24.1 | JSON 结构化 |
| Reranker | sentence-transformers | >=2.7 | bge-reranker |
| 容器 | Docker Compose | - | 单机编排 |

### 依赖清单更新

```
# requirements.txt — 完整版
# --- Web ---
fastapi>=0.100,<0.105
uvicorn[standard]>=0.20
python-multipart>=0.0.5
aiofiles>=22.0

# --- Database ---
sqlalchemy[asyncio]>=2.0,<2.1
asyncpg>=0.29
psycopg2-binary>=2.9      # Alembic 用
alembic>=1.13
pgvector>=0.3

# --- Cache & Queue ---
redis[hiredis]>=5.0
celery>=5.3
kombu>=5.3

# --- Security ---
argon2-cffi>=23.1
PyJWT[crypto]>=2.8
cryptography>=42.0        # RS256 密钥
pydantic>=1.10,<2.0

# --- LLM & Embedding ---
httpx>=0.24
numpy>=1.21
faiss-cpu>=1.7            # 过渡期保留, Phase 1 后可移除
sentence-transformers>=2.7  # Reranker

# --- Observability ---
structlog>=24.1
prometheus-client>=0.19
opentelemetry-api>=1.20
opentelemetry-sdk>=1.20
opentelemetry-exporter-otlp>=1.20

# --- Dev ---
ruff>=0.4
mypy>=1.8
pytest>=7.0
pytest-asyncio>=0.23
pytest-cov>=4.0
```

---

## 实施建议

1. **Phase 1 优先**：数据库和异步化是地基，所有后续阶段都依赖它
2. **渐进式迁移**：保留 `app/api/v1/` 作为同步版本, 新增 `app/api/v2/` 作为异步版本, 逐步切换
3. **测试先行**：每个 Phase 实施前先写测试用例, 确保不回归
4. **配置驱动**：所有新功能通过 `.env` 控制开关, 未配置时自动降级到当前行为
```
