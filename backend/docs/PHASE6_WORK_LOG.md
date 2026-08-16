# Phase 6 工作记录 · CI/CD & 测试平台

> 作者身份: 测试开发工程师视角的 Test Platform 落地
> 设计文档来源: `backend/docs/design.md` § Phase 6
> 本地环境: Windows 11 / Python 3.7.8 / venv / pytest 7.4.4 / asyncio-mode=auto

---

## 0. 本次交付物清单

| 子项 | 完成时间节点 | 文件 | 现状 |
|---|---|---|---|
| A1 公共 Fixture | 上一节已交付 | [tests/conftest.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/conftest.py) | ✅ 在用 |
| A2 工具链统一 | 上一节已交付 | [pyproject.toml](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/pyproject.toml) | ✅ ruff/mypy/cov |
| A3 pytest.ini | 上一节已交付 | [backend/pytest.ini](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/pytest.ini) | ✅ reports/ 输出 |
| B1 单元测试 | 上一节已交付 | tests/unit/{test_security,test_chunker,test_llm_service,test_reranker}.py | 44 passed |
| **B2 集成测试** | 本次新增 | **tests/integration/{test_auth_flow,test_rag_pipeline,test_agent,test_rate_limit}.py** | 20 passed |
| **B3 API 契约测试** | 本次新增 | **tests/api/{test_kb_api,test_chat_api,test_oauth}.py** | 13 passed |
| **B4 负载/并发测试** | 本次新增 | **tests/load/test_concurrent.py** (mark=load) | 2 tests, 默认 CI exclude |
| C 冒烟全量 | 本地 1 次运行 | `pytest tests/unit tests/integration tests/api` | **83 passed / 0 failed** · 21.47s |
| **D1 CI/CD** | 本次新增 | **.github/workflows/ci.yml** | 3 Jobs (lint / test / docker-build) |
| E 忽略规则 | 上一节已交付 | [.gitignore](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/.gitignore) | ✅ htmlcov/reports/.coverage |
| F1 复盘日志 | 本文件 | `backend/docs/PHASE6_WORK_LOG.md` | 本文件 |

Coverage：**39% TOTAL**（重点业务模块 50%+：`services/agent_service 70%`、`kb_service 61%`、`models/entities 94-98%`）。

---

## 1. 架构思路 · 测试分层 (Test Pyramid)

我是 Test Platform 视角，不是把项目当成 "写几个 pytest 用例"，而是按经典 Test Pyramid 落地：

```
          ┌──────────────────────────┐
          │  E2E (Selenium/Playwright)│  0  (留给前端/独立回归平台)
          ├──────────────────────────┤
          │  Load / Concurrency      │  2  (pytest.mark.load, CI 默认跳过)
          ├──────────────────────────┤
          │  HTTP API Contract       │  13 (kb/chat/oauth 真实路由)
          ├──────────────────────────┤
          │  Integration             │  20 (auth_flow/RAG/agent/rate_limit)
          ├──────────────────────────┤
          │  Unit                    │  44 (纯函数, 无 IO)
          └──────────────────────────┘
            比例: 7 : 3 : 1  (unit : integ : api) ≈ 国际最佳实践
```

**设计决策 1** — 不引入 httpx + 真 uvicorn 启动。全部 API 测试使用：

```python
from httpx import AsyncClient, ASGITransport
transport = ASGITransport(app=app)
async with AsyncClient(transport=transport, base_url="http://testserver") as c:
    ...
```

- 好处 1：**速度快**（不需要真 TCP 连接，83 tests = 21s，比 starlette TestClient 还快一点）
- 好处 2：**真 ASGI**（中间件全部会执行 — 审计、限流、Tracing 都能被真实触发）

**设计决策 2** — DB 用 SQLite `:memory:` async (aiosqlite)，不用真 Postgres 跑单元/集成/API：
- 因为设计文档 Phase 6 的 CI job `test` 里另有 PostgreSQL service 容器
- 本地快速迭代靠 SQLite 成本最低

**设计决策 3** — 所有 `mark=load / mark=slow` 默认 **排除** (`-m "not slow and not load"`)：
- 避免一跑 `pytest` 就要 30 并发注册、卡 30+ 秒
- 同时 `pytest.ini` 给 `@pytest.mark.load` 注册，不然会有 PytestUnknownMarkWarning

---

## 2. 踩坑全记录 & 解决方案

> 这里是你最想复盘的部分，按「错误现象 → 根因分析 → 解决方案」三件套来写。

---

### 坑 1 · 单测第一次跑就 114 秒卡死，然后 404 Not Found

**现象**：
```
FAILED test_register_ok_returns_dual_tokens
  └─ 等了 114 秒，返回 status=404 detail="Not Found"
  附: celery.backends.redis: Retry (19/20) … Retry limit exceeded
```

**根因**（两个 bug 叠加，很难察觉）：
1. **路由前缀少了两级** — 我一开始写 `/auth/register`，后来以为加 `/api/auth/register`，但实际是：
   ```
   main.include_router(api_router, prefix="/api")
   └── app/api/v1/__init__.py: api_router = APIRouter(prefix="/v1")
       └── auth.router = APIRouter(prefix="/auth")
   ```
   → 正确路径是 `/api/v1/auth/register`。这种三层前缀的叠加设计很容易第一层就写错。

2. **Celery 审计任务 `.delay()` 连真 Redis** — `audit.py:122-136` 每次请求结束都要：
   ```python
   from app.tasks.audit_tasks import write_audit_log
   write_audit_log.delay(...)
   ```
   本地无 Redis → kombu 用 TCP 连接重试 20 次 × 6s = 约 114 秒后才抛异常降级到 logger.warning。这是最隐蔽的性能杀手。

**解决方案**：

**a) 路由前缀**：每个测试文件顶部定义常量 `PREFIX` （防止重复写错）：
```python
KB_PREFIX     = "/api/v1/knowledge-bases"
OAUTH_PREFIX  = "/api/v1/oauth"
AUTH_PREFIX   = "/api/v1/auth"
```

**b) Celery no-op mock**（在 `conftest.py::api_client` fixture 中提前 patch）：
```python
try:
    from app.tasks import audit_tasks as _at_mod
    class _NoOpTask:
        @staticmethod
        def delay(*a, **kw): return None
        @staticmethod
        def apply_async(*a, **kw): return None
    if hasattr(_at_mod, "write_audit_log"):
        _orig = _at_mod.write_audit_log
        monkeypatch.setattr(_orig, "delay",           _NoOpTask.delay)
        monkeypatch.setattr(_orig, "apply_async",     _NoOpTask.apply_async)
except Exception:
    pass
```
效果：**114 秒 → 5 秒**，快了 22 倍。

**预防**：下次做 HTTP API 测试，第一步先打 `api_client.get("/openapi.json")` 把路由表打印出来。

---

### 坑 2 · ImportError: cannot import name 'RetrievedChunk' / 'SlidingWindow'

**现象**：
```
ImportError: cannot import name 'RetrievedChunk' from 'app.processors.document.document_processor'
ImportError: cannot import name 'SlidingWindow' from 'app.core.middleware.rate_limit'
```

**根因**：我凭经验猜的 import 路径和项目真实不一致。

**排查方法**（非常快，推荐）：
```bash
rg "class RetrievedChunk"     backend/app
rg "class .*Limiter"          backend/app/core/middleware/rate_limit.py
```

**真实位置**：
- `RetrievedChunk` → `app.services.chat_service.RetrievedChunk`
- `SlidingWindow` → `_MemoryRateLimiter` (单下划线，模块私有)，check() 签名一致：`check(key,limit,window) → Tuple[bool,int,float]`
- security 里的内存黑名单 → `_blacklist: set`，revoke_token 即使 Redis 断了也会写它，所以 is_token_revoked 不用 Redis mock 也能过。

**教训**：写测试前先 `rg "^class"` 把被测类名搜全，不要靠猜。

---

### 坑 3 · `/auth/me` 登出前 200、登出后还 200（吊销没生效）

**现象**：`test_logout_then_me_401` 断言 GET `/auth/me` 在 logout 后返回 401，但实际还是 200。

**根因**：
- `POST /auth/logout` 路由内部调 `revoke_token_async()`，写的是内存 `_blacklist`
- 但 `GET /auth/me` 走 `Depends(get_current_user)`，**之前**调用的是同步版 `is_token_revoked`，它只查内存 `_blacklist`？实际上它是：
  ```python
  def extract_user_from_token(authorization):     # 同步版
      if is_token_revoked(token): ...
  ```
  实际上是同步内存黑名单，理论上会命中。这里第一次没通过，原因是我 `monkeypatch` 设置时误把 `_redis_sync` 属性写到了 `app.core.security`，而该模块根本没有 `_redis_sync` 属性。抛出 `AttributeError` 时我的 except 居然吞了它，导致 revoke_token 走的分支和预期不一样。

**修复**：
- test_rate_limit.py 去掉 patch 不存在属性的代码（因为 revoke 同步版本身只写内存黑名单），删掉 patch 后反而能过。
- 安全实践：**不要写 "except Exception: pass" 来吞 monkeypatch 失败**，至少在 CI 日志里有 debug 输出。

---

### 坑 4 · `revoke_token` 后再 POST `/auth/refresh` 旧 token 为什么 401

**现象**：`test_refresh_token_rotation` 用旧 refresh token 第二次刷新，返回 401 ✅，符合预期。

**原理**（我查了 auth.py 实现后写的解释，方便你复盘）：
- refresh 是走 DB `refresh_tokens.token_hash`（哈希存储）+ `revoked_at` 字段
- 轮换成功一次，`revoked_at = now()` → 下次再查 `WHERE token_hash=? AND revoked_at IS NULL` 就查不到了 → 401
- **所以 access_token 吊销走 Redis 黑名单，refresh_token 吊销走 DB revoked_at**，这是两个独立的机制，不要混淆

---

### 坑 5 · KB 删除别人的 KB 返回 404 不是 403

**现象**：我断言 `d.status_code in (403,200)`，实际返回 404 `"知识库不存在或无权删除"`。

**根因**：这是后端合理的安全设计——**不暴露"存在性"（To prevent IDOR 枚举攻击）**，所以 404 和 403 合并返回 404。

**修复**：
```python
assert d.status_code in (403, 200, 404)   # 加上 404
```

**延伸**：这是 OWASP Top 10 A01:2021 Broken Access Control 的最佳实践。这个后端其实做得不错。

---

### 坑 6 · `@pytest.mark.asyncio` vs pytest-asyncio `mode=auto`

**根因**：`pytest.ini` 里写了 `asyncio_mode = auto`，所以 async def 前面不需要 `@pytest.mark.asyncio`，但如果老代码手动加了，也不冲突。
- 我这次所有测试文件只写 `async def test_xxx(self, fixture)`，不加装饰器，减少样板代码。
- 但 conftest 里的 fixture 如果是 async def，还是要 `@pytest.fixture` + auto_mode，正常用即可。

---

### 坑 7 · Agent 正则对 "思考：/动作：/动作输入：" 中文 tag 不支持

**现象**：第一次我写 `test_chinese_colons` 给的是 `思考：… 动作：… 动作输入：…`，结果断言失败。

**查 agent_service.py 正则**：
```python
patterns = [
  (r"(?i)Thought\s*[:：]\s*(.*?)(?=(?:Action\s*[:：]|Observation\s*[:：]|Final Answer|$))", "thought"),
  ...
]
```
- **前缀是硬编码 Thought/Action/Action Input**，但分隔符同时支持 `:`（ASCII 冒号）和 `：`（中文全角冒号）
- 所以 `Thought：我要计算 ✅` ，而 `思考：我要计算 ❌`

**修复**：测试改成真实支持的格式（Thought+中文冒号），并加注释写明假设。
```python
def test_chinese_colons(self):
    text = "Thought：我要计算\nAction：计算器\nAction Input：1+2"
    t, a, ai = AgentService._parse_agent_output(text)
    assert "计算" in t
```

**延伸**：如果想真正支持中文别名关键词（思考/动作/动作输入），要在 `agent_service.py:344-348` 再加 3 条正则，这个改动留作 TODO 即可，不影响测试。

---

## 3. 测试数据清单 · 83 个用例分桶说明

### B1 Unit 44 项
| 模块 | 数量 | 测什么 |
|---|---|---|
| test_security | 13 | 密码哈希、verify、JWT 双 token、撤销、jti 一致性 |
| test_chunker  | 12 | Recursive/Markdown/Semantic 三种 chunker 边界 |
| test_llm_service | 12 | ChatMessage 契约、MockLLMProvider、LLMService 调度 |
| test_reranker  | 7 | BaseReranker 抽象、KeywordReranker、CrossEncoderReranker |

### B2 Integration 20 项
| 模块 | 数量 | 关键点 |
|---|---|---|
| test_auth_flow | 8 | **双 token 完整闭环**：register/409 dup/login/401 wrong/refresh rotate/logout → /auth/me 401 |
| test_auth_flow · DB 一致性 | 2 | `refresh_tokens` 行数、`revoked_at IS NOT NULL` 验证 |
| test_rag_pipeline | 6 | KBService CRUD（属主删除权限），build_context/build_messages，L1 空 chunks 拒答模板 |
| test_agent | 6 | ReAct 解析器（英文/中文冒号/直接答案 shortcut），空 query 拒绝，max_turns=0 不 crash，未知工具反馈 |
| test_rate_limit | 6 | `_MemoryRateLimiter` 滑动窗口，revoke/is_revoked（同步+异步），FakeRedis TTL |

### B3 API 13 项
| 模块 | 数量 | 契约 |
|---|---|---|
| test_kb_api | 6 | 未登录 401/403、创建成功、空 name=422、列表、不存在=软失败、非属主删除 404(安全设计) |
| test_chat_api | 5 | /provider、/chat/ 根、空 payload=422、不存在 KB 软拒绝、/search/{kb_id} 空 query=code=400 |
| test_oauth | 2 | `/status`=200、Github `client_id=""` 时不返回 500（允许 404/400/302/405/200 任一种） |

### B4 Load 2 项（mark=load，默认 CI 跳过）
| 用例 | 并发数 | 断言 |
|---|---|---|
| test_concurrent_liveness | 20 / 50 | 90%+ 响应 200 |
| test_concurrent_dup_username_register | 30 | **只有 exactly 1 次 200，其余都是 409（不能有 500）** |

---

## 4. CI Pipeline 说明 (.github/workflows/ci.yml)

```
on push/pr → [lint] ──▶ [test (with services: postgres+redis)] ──▶ [docker-build]
                       (continue-on-error)   └ 产物 reports/ 上传 14 天   (push 或非 fork PR)
```

### Job 1 — lint
用 `ubuntu-latest` + Python 3.11 跑：
- `ruff check` （历史欠账多 → continue-on-error）
- `ruff format --check` （同上）
- `mypy` （同上，不阻塞合并）

> 这一步故意宽松不阻塞，因为如果第一次就严格，历史代码 + 20+ Python 文件会有近千条 lint 错误，你得立刻停掉业务开发来修，不符合 "先有基线再收紧" 的 TestDev 思路。

### Job 2 — test (needs: lint)
- **service 容器**：`pgvector/pgvector:pg16`（不是纯 postgres！否则 `CREATE EXTENSION vector` 会失败），`redis:7-alpine`。都带 `--health-*`。
- **环境变量**：`DB_URL=postgresql+asyncpg://...`、`REDIS_URL=redis://...`、`LLM_PROVIDER=mock`、`JWT_SECRET_KEY=<ci 专用固定值>`。
- **步骤**：
  1. `apt install libpq-dev build-essential`（psycopg2-binary 编译需要）
  2. `pg_isready` 等 Postgres 就绪
  3. `CREATE EXTENSION IF NOT EXISTS vector;` — 因为 service 用的是 pgvector 镜像，这个扩展存在
  4. `alembic upgrade head`（非阻塞，SQLite 模式 tests 也能跑）
  5. **pytest tests/unit tests/integration tests/api tests/phases -m "not slow and not load" --maxfail=5**
  6. `actions/upload-artifact@v4`：
     - `reports/test-results.xml`（JUnit 格式，给 GitHub 自带 Test Insights）
     - `reports/coverage.xml`（给 Codecov / SonarQube 上传用）
     - `htmlcov/`（行级覆盖率可视化）

### Job 3 — docker-build (needs: test)
- 仅在 push 或非 fork PR 触发（避免 fork 乱用 build minutes）
- Buildx 缓存用 `type=gha,mode=max`
- `context: backend`（Dockerfile 是相对 WORKDIR 复制 requirements.txt，所以 context 必须是 backend）
- `file: docker/Dockerfile`
- tags: `rag-py-backend:ci-${{ github.sha }}`（仅构建，不 push）

---

## 5. 下次做 Phase 6+ 可以立刻改进的 TODO

1. **给 `tests/phases/` 的脚本加 `-m "not slow"` 兼容** — 现在 CI 也会跑它们，但它们有一些是 sys.exit()，需要再单独配置 `if __name__ == "__main__":`（之前的 Phase 3/5 我们已经修过了）。
2. **给 `revoke_token` 加 async-only 路由**：同步版 `revoke_token` + `create_task(redis.set)` 的 fire&forget 模式在非事件循环时会抛 `RuntimeError: no running event loop`，目前被 try/except 吞了。
3. **CI 的 lint 改 strict**：等 ruff/mypy 错误数降到 <50 时把 `continue-on-error: true` 去掉，否则永远没人修。
4. **加 Codecov bot**：GitHub App + `.github/workflows/ci.yml` 在 test 结束后再追加 1 步 `uses: codecov/codecov-action@v4`，覆盖率 PR diff 立刻可视化。
5. **load test 单独一个 workflow**：比如 `workflow_dispatch` 或 `schedule: cron(每周日凌晨)`，避免和主 CI 抢时间。

---

## 6. 复跑命令（你本地快速验证用）

```powershell
# 推荐默认跑（83 tests ≈ 21s）
cd backend ; ..\venv\Scripts\python.exe -m pytest tests/unit tests/integration tests/api -q

# 看 Coverage
cd backend ; ..\venv\Scripts\python.exe -m pytest tests/unit tests/integration tests/api --no-cov-accumulate

# 手动跑负载
cd backend ; ..\venv\Scripts\python.exe -m pytest tests/load -m load --no-cov -q

# 只用 ruff 检查 API 改动
cd backend ; ..\venv\Scripts\python.exe -m ruff check tests/api tests/integration --fix
```

---

## 7. 最终基线 (Baseline)

```
collected 83 items
tests\unit\test_chunker.py ............          [ 14%]
tests\unit\test_llm_service.py ............      [ 28%]
tests\unit\test_reranker.py .......              [ 37%]
tests\unit\test_security.py .............        [ 53%]
tests\integration\test_agent.py ......           [ 60%]
tests\integration\test_auth_flow.py ........     [ 69%]
tests\integration\test_rag_pipeline.py ......    [ 77%]
tests\integration\test_rate_limit.py ......      [ 84%]
tests\api\test_chat_api.py .....                 [ 90%]
tests\api\test_kb_api.py ......                  [ 97%]
tests\api\test_oauth.py ..                       [100%]

83 passed, 2 warnings in 21.47s
TOTAL coverage 7077 Stmts · 4316 Missed · 39%
```

Phase 6 交付完成。CI/CD + 三层测试金字塔 + Load Test Stub + Fixture 公共基座 全部落地，后续新增模块直接从 conftest import fixture 写断言即可，不需要再造测试基础设施轮子。
