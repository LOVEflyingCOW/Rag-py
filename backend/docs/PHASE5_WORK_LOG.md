# Phase 5 实施文档 — 可观测性 + 安全增强

> 日期: 2026-08-15
> 范围: design.md 第 7 节 (7.1~7.5)
> 测试: 38/38 通过

---

## 一、整体思路

对照 design.md 第 7 节，Phase 5 包含 5 个子任务：

| # | 设计项 | 状态 | 说明 |
|---|--------|------|------|
| 7.1 | structlog JSON 日志 | ✅ | 重写 logging.py, 支持 JSON 输出 + trace_id 注入 |
| 7.2 | Prometheus 指标 | ✅ | 新建 metrics.py + MetricsMiddleware + /metrics 端点 |
| 7.3 | OpenTelemetry 追踪 | ✅ | 集成到 TracingMiddleware, 可选依赖降级 |
| 7.4 | OAuth2 三方登录 | ✅ | GitHub/Google OAuth2 流程, state 防 CSRF |
| 7.5 | 健康探针分离 | ✅ | liveness (存活) + readiness (就绪) |

---

## 二、实现详情

### 2.1 structlog JSON 结构化日志

**文件**: [app/core/logging.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/logging.py) (重写)

#### 想法

1. **为什么用 structlog?** 标准 logging 输出的是非结构化文本，不便于 ELK/Loki 聚合。structlog 输出 JSON 格式，每个字段都可以索引。
2. **降级兼容**: structlog 是可选依赖。未安装时降级到标准 logging，保持原有行为。通过 `_HAS_STRUCTLOG` 标志控制。
3. **trace_id 注入**: 使用 `structlog.contextvars` 在请求中间件中绑定 trace_id，后续所有日志自动包含该字段。请求结束后清除上下文。
4. **DEBUG 模式优化**: DEBUG 级别用 `ConsoleRenderer` (彩色可读)，INFO 以上用 `JSONRenderer` (JSON 格式)。

#### 坑点

- **structlog 与标准 logging 共存**: structlog 底层使用标准 logging 的 Handler。必须先配置标准 logging 的 Handler (console + file)，再配置 structlog。否则 structlog 输出无处可去。
- **contextvars 跨请求泄漏**: 如果请求异常退出没有 `clear_contextvars()`，trace_id 会泄漏到下一个请求。必须在 TracingMiddleware 的 finally 块中清除上下文。

#### 关键接口

```python
bind_request_context(trace_id, method, path)  # 请求开始时绑定
bind_user_context(user_id, username)          # 认证后追加用户
clear_request_context()                      # 请求结束时清除
get_logger(name)                             # 获取 structlog logger
```

---

### 2.2 TracingMiddleware — trace_id 注入

**文件**: [app/core/middleware/tracing.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/tracing.py) (新建)

#### 设计

```
请求进入 → 读取/生成 X-Trace-ID → 绑定 structlog contextvars
→ (可选) OpenTelemetry span → 执行请求 → 响应头写入 X-Trace-ID → 清除上下文
```

#### 坑点

- **中间件执行顺序**: Starlette 中后注册的中间件先执行。TracingMiddleware 最后注册（最先执行），确保 trace_id 在所有其他中间件之前注入。
- **健康端点跳过**: `/health`、`/health/ping`、`/metrics` 不需要 trace_id，跳过以减少开销。

---

### 2.3 Prometheus 指标

**文件**:
- [app/core/metrics.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/metrics.py) (新建)
- [app/core/middleware/metrics.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/metrics.py) (新建)

#### 指标设计

| 指标 | 类型 | 标签 | 说明 |
|------|------|------|------|
| `rag_http_requests_total` | Counter | method, path, status | HTTP 请求总数 |
| `rag_http_request_duration_seconds` | Histogram | method, path | HTTP 请求延迟 |
| `rag_queries_total` | Counter | kb_id, provider | RAG 查询总数 |
| `rag_query_duration_seconds` | Histogram | kb_id | RAG 查询延迟 |
| `rag_retrieval_duration_seconds` | Histogram | kb_id | 检索延迟 |
| `rag_llm_duration_seconds` | Histogram | provider | LLM 生成延迟 |
| `rag_active_users` | Gauge | — | 活跃用户数 |
| `rag_db_pool_in_use` | Gauge | — | DB 连接池使用数 |
| `rag_cache_hit_rate` | Gauge | cache_type | 缓存命中率 |

#### 降级策略

`prometheus_client` 是可选依赖。未安装时所有 `record_*` 和 `set_*` 函数都是 no-op（空操作），不影响业务逻辑。

---

### 2.4 健康探针分离

**文件**: [app/api/health.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/health.py) (增强)

#### 设计

| 端点 | 用途 | 检查内容 | 失败行为 |
|------|------|---------|---------|
| `/health/liveness` | 存活探针 | 仅进程本身 | 200 (进程在就返回) |
| `/health/readiness` | 就绪探针 | DB + Redis + Celery | 503 (不接收新流量) |
| `/health/metrics` | Prometheus | 指标数据 | 200 |

#### 坑点

- **readiness 检查 Celery**: 使用 `celery_app.control.inspect(timeout=1)` 检查 worker 状态。这是同步调用，但 timeout=1s 不会长时间阻塞。如果 Celery 不可用返回 `"unknown"` 而非 `"fail"`（非关键依赖）。
- **liveness 不检查依赖**: liveness 只检查进程是否存活，不检查 DB/Redis。这样在依赖短暂不可用时不会导致 Pod 被重启。

---

### 2.5 OAuth2 三方登录

**文件**: [app/api/v1/oauth.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/v1/oauth.py) (新建)

#### 流程

```
1. 客户端 → GET /api/v1/oauth/github/login
2. 服务端生成 state, 存入 Redis (5min TTL), 重定向到 GitHub 授权页
3. 用户授权 → GitHub 回调 /api/v1/oauth/github/callback?code=xxx&state=yyy
4. 服务端校验 state → 用 code 换 access_token → 获取用户信息
5. 查找或创建本地用户 (username = "github_{oauth_id}")
6. 生成双 Token, 重定向到前端 (通过 query param 传递)
```

#### 坑点

- **state 校验顺序**: `oauth_callback` 先检查 provider 是否支持、再检查 client_id 是否配置、最后才校验 state。测试时需要 mock `client_id` 才能测到 state 校验逻辑。
- **GitHub 用户 email 为 null**: GitHub API 默认不返回 email（需要 `user:email` scope）。如果 email 为 null，用 `{username}@github.oauth` 作为占位。
- **httpx 异步客户端**: 使用 `httpx.AsyncClient` 发起 HTTP 请求（token 交换 + 用户信息获取），不阻塞事件循环。

---

## 三、文件变更清单

### 新建文件

| 文件 | 说明 |
|------|------|
| [app/core/metrics.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/metrics.py) | Prometheus 指标定义 + 记录接口 |
| [app/core/middleware/tracing.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/tracing.py) | TracingMiddleware + OpenTelemetry 集成 |
| [app/core/middleware/metrics.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/metrics.py) | MetricsMiddleware (HTTP 请求指标采集) |
| [app/api/v1/oauth.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/v1/oauth.py) | OAuth2 三方登录路由 |
| [tests/phases/test_phase5_observability.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase5_observability.py) | Phase 5 测试 (38 项) |

### 修改文件

| 文件 | 改动 |
|------|------|
| [app/core/logging.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/logging.py) | 重写为 structlog JSON + 降级兼容 |
| [app/api/health.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/health.py) | 新增 liveness/readiness/metrics 端点 |
| [app/core/middleware/__init__.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/middleware/__init__.py) | 导出 TracingMiddleware + MetricsMiddleware |
| [app/main.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/main.py) | 注册 TracingMiddleware + MetricsMiddleware |
| [app/api/v1/__init__.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/api/v1/__init__.py) | 注册 oauth 路由 |
| [app/core/config.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/config.py) | 新增 OAuth2 配置项 |
| [app/core/security.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/security.py) | 修复 argon2 InvalidHashError 导入兼容 |

---

## 四、中间件注册顺序

```
请求进入 → TracingMiddleware (最先, 注入 trace_id)
         → MetricsMiddleware (采集 HTTP 指标)
         → SecurityHeadersMiddleware (安全头)
         → IdempotencyMiddleware (幂等检查)
         → RateLimitMiddleware (限流)
         → AuditLogMiddleware (审计日志, 最后)
         → 路由处理器
```

---

## 五、测试统计

| 模块 | 测试数 | 覆盖内容 |
|------|-------|---------|
| structlog 日志 | 7 | 导入、logger 获取、_HAS_STRUCTLOG、context 绑定/清除 |
| TracingMiddleware | 5 | 导入、trace_id 生成/保留、健康端点跳过、_HAS_OTEL |
| Prometheus 指标 | 10 | 导入、enabled 标志、record_*/set_* no-op、get_metrics、/metrics 端点 |
| 健康探针 | 6 | liveness 返回、路由存在性 (4 个)、版本号 |
| OAuth2 | 7 | 导入、路由存在、status、不支持 provider、未配置、无效 state、不支持回调 |
| 中间件注册 | 2 | 6 个中间件导出、数量验证 |
| **合计** | **38** | **全部通过** |
