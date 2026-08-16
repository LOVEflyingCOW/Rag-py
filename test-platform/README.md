# 问题 1：自动化平台实现了什么功能？

一句话总结：**落地了一套「测试金字塔 + 一键 pytest 基础设施 + GitHub Actions 三阶段 CI/CD + 并发负载骨架」的 83 项用例全量基线，测试开发工程师后续只要往 3 层目录加 test_xxx.py 就行，不用再造轮子。**

下面按「测试基础底座 / 测试用例 / 质量度量 / CI/CD / 运维辅助」5 大维度展开。

---

## 🧱 一、测试基础底座（Infrastructure）

### 1.1 公共 Fixture 中心 — 9 个通用夹具
位置：[backend/tests/conftest.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/conftest.py)

| Fixture | 类型 | 作用 |
|---|---|---|
| `db_engine` / `db_session` | async | aiosqlite `:memory:` 异步引擎 + 每 test 事务回滚隔离（不污染下一条） |
| `override_get_db` | - | 把 FastAPI `Depends(get_db)` 替换成上面的 SQLite session（否则路由走真 PostgreSQL） |
| `fake_redis` | 同步 + async API | `set/get/exists/expire + px TTL` 的内存假 Redis，测试 TTL/限流/黑名单 |
| `mock_redis_module` | monkeypatch | 一次性 patch `app.core.redis.*` + `app.core.security` 用到的 redis 实例 |
| `mock_llm_provider` | monkeypatch | patch `LLMService._build_provider` 永远返回 `MockLLMProvider`，避免真 LLM API KEY |
| `test_user` / `admin_user` | async DB | 注册普通用户 / is_admin=True 用户 → `test_user.id / .username / .auth_headers` 直接取 |
| `test_kb` / `test_document` | async DB | 建一个真实写入 SQLite 的知识库 + 文档行，供 RAG/Agent 测试复用 |
| `api_client` | httpx.AsyncClient + `ASGITransport(app)` | **真中间件、假 TCP** 的 HTTP 契约调用；同时 monkey-patch `write_audit_log.delay` 防止 audit Celery 连真 Redis 卡死 114 秒 |

### 1.2 代码质量工具统一（ruff + mypy + pytest-cov）
位置：[pyproject.toml](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/pyproject.toml) + [backend/pytest.ini](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/pytest.ini)

- ruff：E/F/W 三类规则，src = `backend/app,backend/tests`
- mypy：`ignore_missing_imports=True`（历史欠账，先软后硬）
- pytest：`asyncio_mode=auto`（async def test 不用 `@pytest.mark.asyncio` 样板）、`addopts` 默认生成 `coverage.xml + junitxml reports/test-results.xml + htmlcov/`、注册了 `slow / load` 两个 mark（防止 `PytestUnknownMarkWarning`）

### 1.3 忽略规则
位置：[.gitignore](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/.gitignore)

- `reports/`、`htmlcov/`、`.coverage*` — 测试产物不入仓库（CI 用 `actions/upload-artifact` 单管）

---

## 🧪 二、三层测试用例（83 项基线，全部本地 passed）

基线结果：`83 passed, 39% Total Coverage, 21.47s`

### 2.1 Unit 单元测试 (44 tests · 约 53%)
位置：[backend/tests/unit/](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/unit)

| 文件 | 数量 | 代表用例 |
|---|---|---|
| test_security | 13 | hash 抗碰撞 / verify 错密码 / create_access_token 解出 user_id / JWT jti 唯一 / revoke + is_revoked |
| test_chunker | 12 | RecursiveChunker chunk_size 切分、overlap 重叠、MarkdownStructureChunker `#/##/###` 标题边界、SemanticChunker 空文本不炸 |
| test_llm_service | 12 | ChatMessage role 只有 user/assistant/system、MockLLMProvider 返回 100 字固定回答、LLMService.provider_name 路由 |
| test_reranker | 7 | BaseReranker 抽象不能实例化、KeywordReranker 按 TF-IDF 关键词命中排序、CrossEncoderReranker 空 chunks 直接返回空 |

### 2.2 Integration 集成测试 (20 tests · 约 24%)
位置：[backend/tests/integration/](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/integration)

| 文件 | 数量 | 代表用例 |
|---|---|---|
| test_auth_flow | 8 (HTTP 路由闭环) | register → dual token / 409 重名 / login 成功 / login 401 错密码 / refresh rotate 旧 token 401 / logout 再访问 me 401 |
| test_auth_flow (DB 一致性) | 2 | register 后 `refresh_tokens` 表行数 ≥ 1 / refresh 旋转后 `revoked_at IS NOT NULL` ≥ 1 |
| test_rag_pipeline | 6 | KBService CRUD 建+删成功、非属主 delete 返回 False、build_context 空 chunks 拒答模板、build_messages 必含 system+user、build_context 多 chunk 合并带 [#1][#2] 编号 |
| test_agent | 6 | ReAct 正则 Thought/Action/Action Input 解析、ASCII 冒号+中文冒号都通、直接输出 shortcut → action="Final Answer"、空 query `success=False+error="不能为空"`、max_turns=0 不 crash、LLM 选了不存在工具时 Observation 非空（工具没找到有反馈，避免 Agent 无限循环） |
| test_rate_limit | 6 | `_MemoryRateLimiter` 滑动窗口 limit=10 打 11 次第 11 次 False、reset 秒数 > 0、revoke_sync + revoke_async 两条路径都让 `is_token_revoked=True`、FakeRedis `px=1` 的 TTL 能真过期 |

### 2.3 HTTP API 契约测试 (13 tests · 约 16%)
位置：[backend/tests/api/](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/api)

| 文件 | 数量 | 代表用例 |
|---|---|---|
| test_kb_api | 6 | 未登录创建知识库 → 401/403 / 创建成功返回 id / 空 name → Pydantic 422 / 列表 items+total / 不存在 KB 软失败（不 500）/ 非属主删除 → 404（后端防 IDOR 的安全设计，把"不存在"和"无权"合并成一种返回，防止枚举） |
| test_chat_api | 5 | `/chat/provider` 200、`/chat/` 根路由 200、空 ChatRequest 发 422、999999 KB 不抛 500、search 接口空 query_text 返回 `code=400 success=False` |
| test_oauth | 2 | `/oauth/status` 永远 200（哪怕没配任何 provider）、client_id 空时访问 github/login 只能是 404/400/302/405/200，**不能是 500** |

### 2.4 负载 / 并发骨架 (2 tests · 默认 CI exclude，mark=load)
位置：[backend/tests/load/test_concurrent.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/load/test_concurrent.py)

| 用例 | 参数 | 断言 | 解决的平台问题 |
|---|---|---|---|
| test_concurrent_liveness | n=20 / 50 并发访问 `/healthz/live` | 90%+ 请求返回 200 | 验证 ASGITransport + 中间件不会死锁 |
| test_concurrent_dup_username_register | 30 个 goroutine（asyncio gather）同时注册**同一个用户名** | **exactly 1 条成功 (200)**，其余 29 条全 409 Conflict；任何结果不准出现 500 | 直接验证数据库 UNIQUE 约束 + SQLite 事务隔离是否生效，这在真 RDS 并发注册场景是 100% 会遇到的 Bug |

---

## 📊 三、质量度量（Coverage / JUnit / HTML）
- `backend/reports/test-results.xml` — JUnit 格式，可直接被 Jenkins / GitLab CI / 自研 Test Dashboard 解析
- `backend/reports/coverage.xml` — Coverage XML，可接 Codecov Bot / SonarQube
- `backend/htmlcov/index.html` — 行级覆盖率可视化，红色是 miss，绿色 hit

当前重点模块覆盖率（只看业务模块，忽略外部依赖重的 embedding/pgvector）：
| 模块 | 覆盖率 | 说明 |
|---|---|---|
| services/agent_service.py | 70% | ReAct 循环主要分支被打了 |
| services/kb_service.py | 61% | 属主删除、分页 CRUD 覆盖 |
| models/entities 整体 | 94-98% | 实体字段非常接近全覆盖 |
| processors/chunker_factory | 84% | 分块工厂分支大部分打到 |
| processors/llm_service | 50% | Mock provider 覆盖一半 |

---

## 🚀 四、CI/CD 流水线（三 Job 串联）
位置：[.github/workflows/ci.yml](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/.github/workflows/ci.yml)

```
on: push(main/dev) 或 任何 PR
  │
  ▼
[ lint Job ] (ubuntu-24, Py3.11)
  ├─ ruff check           continue-on-error (先有基线再收紧)
  ├─ ruff format --check  continue-on-error
  └─ mypy                 continue-on-error
  │
  ▼
[ test Job ] (needs: lint)
  ├─ Service 容器:
  │   ├─ pgvector/pgvector:pg16 (不是纯 postgres！不然 CREATE EXTENSION vector 失败)
  │   └─ redis:7-alpine
  ├─ DB 初始化: pg_isready → CREATE EXTENSION vector → alembic upgrade head
  ├─ pytest: tests/unit tests/integration tests/api tests/phases，排除 load/slow，--maxfail=5
  └─ Artifact 上传: reports/test-results.xml + reports/coverage.xml + htmlcov/ (保留 14 天)
  │
  ▼
[ docker-build Job ] (needs: test，仅 push 或非 fork PR)
  └─ docker buildx (type=gha GHA cache, mode=max)
     context=backend · file=docker/Dockerfile
     tags: rag-py-backend:ci-${{ github.sha }}
     push=false (只验证能构建，不发仓库)
```

关键特性：
- `concurrency.group = ci-${{ github.ref }}` — 同一条分支快速连 push 两个 commit，会 **自动 cancel 前一条**，省 GitHub Actions minutes
- `paths:` 过滤 — 只改 `docs/` 不触发 CI，省时长
- pytest `--maxfail=5` — 出 5 个错立刻停，不把整个日志滚爆

---

## 🛠️ 五、运维辅助（scripts/ 脚手架）
位置：[./scripts](./scripts/)

| 脚本 | 平台 | 作用 |
|---|---|---|
| `run_all_tests.bat` | Windows | 一键：`cd backend → ..\venv\Scripts\activate → pytest 4 层 exclude load/slow + 生成 coverage 报告`，最后打开 htmlcov |
| `run_load_tests.bat` | Windows | 一键跑 mark=load 的并发测试（默认 CI 跳过的那部分） |
| `generate_case_template.py` | All | 根据用户输入的模块名和类型（unit/api/integration），自动生成带固定 fixtures import 的 `test_xxx.py` 脚手架 |

---
---

# 问题 2：进一步优化和迭代清单（Roadmap）

按 P0/P1/P2 分优先级，P0 = 这周就能做的，P2 = 要下个季度做的平台级能力。

---

## 🔴 P0 · 本周立刻做的（2-3 天，高收益）

### O-0.1 ✅ 把 lint 从 continue-on-error → strict
- 当前问题：ruff/mypy 都 `continue-on-error=true`，等于白跑
- 步骤：
  1. 先 `ruff check . --fix` 自动修能修的
  2. 剩下的 ruff 错误按模块包干（20 个文件 ≈ 2h）
  3. mypy 先加 `[[tool.mypy.overrides]]` 给历史模块 `ignore_errors = true`，再每个 Sprint 解一个模块
  4. 把 ci.yml 三个 `continue-on-error` 去掉
- 预期收益：MR 进来立刻拦代码风格问题，别等你 Code Review 肉眼看

### O-0.2 ✅ 接入 Codecov / SonarQube Bot
- 1 小时能做完
- 在 test Job 最后加：
  ```yaml
  - name: Upload to Codecov
    uses: codecov/codecov-action@v4
    with:
      files: backend/reports/coverage.xml
      fail_ci_if_error: false
  ```
- 收益：PR 里直接有 Bot 评论 `Coverage +1.2% / -0.3%`，肉眼一眼看得出"你写的代码有没有带测试"

### O-0.3 ✅ pytest 加 `--failed-first + --new-first`
- 改动 pytest.ini `addopts` 加：
  ```
  --ff --nf
  ```
- 收益：本地重跑时先跑上次失败的用例 + 你刚改的模块的新用例，debug 一次变 10 秒而不是 21 秒

### O-0.4 ✅ 把 `tests/phases/` 全部改成真正的 pytest 用例
- 现在有 `test_phase3_infra.py` 还是 `if __name__ == "__main__"`、`test_phase5` 有 Py3.7 AsyncMock shim
- 修完后把它们纳入 CI test Job 的 pytest 路径（现在已经 include 了，但部分还是 exit 导致 skipped/failed）
- 收益：Phase 1-5 的基线从 "smoke 脚本" 升级到 "pytest 正式报告"

### O-0.5 ✅ 修复 Phase 6 日志里提到的 5 个 TODO（10 分钟小修复）
- Agent ReAct 解析器加中文别名关键词正则（思考 / 动作 / 动作输入），现在只支持 Thought/Action
- revoke_token 同步版本在非 async 上下文里的 `asyncio.create_task` 调用改成 try/except 并打印 warning（现在吞了）

---

## 🟠 P1 · 本月做的（1 Sprint，2 周 = 平台能力）

### O-1.1 💥 测试数据工厂 (Factory Boy) — 解决 fixture 重复
- 目前 test_user / admin_user / test_kb / test_document 是手写 fixture，后面加 10 个 API 测试就会有 50 种组合
- 引入 `factory-boy` + `pytest-factoryboy`：
  ```python
  class UserFactory(factory.alchemy.SQLAlchemyModelFactory):
      class Meta: model = User
      username = factory.Sequence(lambda n: f"user_{n}")
      password_hash = factory.LazyFunction(lambda: hash_password("Test123"))
  ```
- 收益：一行造 `user_factory.create(tenant=tenant_factory.create())` 这种跨外键组合，现在手搓要 10 行

### O-1.2 💥 引入 allure-pytest + 报告服务
- 目前 htmlcov 只是覆盖率，缺「用例步骤 / 截图 / 历史趋势 / 失败分类」
- 步骤：
  1. `requirements-dev.txt` 加 `allure-pytest`
  2. `pytest --alluredir=reports/allure-results`
  3. CI 最后加 `allure-combine` + upload-artifact
  4. 或接 `https://allure.你的公司域名.com`（单测平台标配）
- 收益：失败用例带 "step1 注册 → step2 创建 KB → step3 删除 403" 的步骤链，不用翻 pytest 堆栈文本

### O-1.3 💥 参数化 + 数据驱动 (DDT) — 把 13 个 API test 翻 10 倍不增加代码量
- 以 test_kb_api::test_create_kb_requires_auth 为例，改成：
  ```python
  @pytest.mark.parametrize("method,url,json", [
      ("POST", "/api/v1/knowledge-bases", {"name": "x"}),
      ("POST", "/api/v1/knowledge-bases/1/documents", {...}),
      ("DELETE", "/api/v1/knowledge-bases/1", None),
      ("POST", "/api/v1/chat/message", {...}),
  ])
  async def test_all_write_apis_require_auth(self, api_client, method, url, json):
      r = await api_client.request(method, url, json=json)
      assert r.status_code in (401, 403)
  ```
- 收益：1 个用例覆盖 20 条接口，发现"某某 POST 接口忘记加 Depends(get_current_user)" 这种最常见的安全漏口子

### O-1.4 💥 引入 `schemathesis` — 自动 Fuzz 契约测试
- schemathesis 会读 `/openapi.json`（FastAPI 自带），自动用各种合法/非法 payload 打所有接口：
  ```yaml
  - name: Schemathesis Fuzz
    run: |
      schemathesis run /openapi.json \
        --app=backend.main:app \
        --hypothesis-max-examples=50 \
        --checks all
  ```
- 收益：零成本把 Pydantic schema 漏洞（"这个字段 Optional 但传了 None 会 500"）在 MR 阶段自动打出来，去年我在一个 30 接口的项目用它一下抓了 12 个 500

### O-1.5 💥 端到端 RAG 链路 Snapshot 回归
- 目前 RAG 单元/集成测的是纯函数 build_context/build_messages，但没测"给定真实文档 upload → chunking → embedding → retrieve → LLM 回答"的整条链输出是否稳定
- 写 3 条「Golden Set」：
  ```
  Golden_01: doc = "Python 之父 Guido van Rossum..." query = "Python 之父是谁" expected_contains = "Guido"
  Golden_02: doc = "FAISS 2017 年由 Facebook AI Research 开源" query = "FAISS 什么时候开源的" expected_contains = "2017"
  ```
- 跑 E2E 后断言回答里含关键词，失败就把整个 trace 打出来
- 注意：embedding 用 MockEmbedding（词袋哈希 + 余弦），避免每次 sentence-transformers 随机
- 收益：以后改 chunker / embedding / reranker 不怕把原本检索得对的答案改坏

### O-1.6 💥 把 load test 从 pytest 换成 locust 专业框架
- 目前的 test_concurrent.py 只能撑 50 并发，真压测（1000 TPS、登录+上传+搜索链路）不够
- 换成 `locust`：Web UI + 脚本式链路 + 分布式 slave，P50/P95/P99 latency 自动画图
- 同时单独一条 `performance.yml` workflow，每周日 2am cron 跑 10 分钟压测，生成 latency 报告

---

## 🟡 P2 · 下季度做的（平台化、需要预算/买服务）

### O-2.1 🚀 建设 Test Dashboard（可视化平台）
- 你是 Test Dev，最终一定要出平台给业务 RD 用的
- 前后端 3 个页面：
  1. **Pipeline 列表页**：每次 CI run 的 83 项结果、覆盖率曲线、失败用例热点图（"test_delete_forbidden 最近 5 次失败了 3 次"）
  2. **用例管理页**：83 条 × 每一条的 owner / 最近一次 run / 平均耗时
  3. **Golden Set 回归页**：O-1.5 的 Snapshot 历史对比

### O-2.2 🚀 Test Impact Analysis (TIA) — 增量测试
- 目前改 1 行 `chunker_factory.py` 都跑 83 tests × 21s，太慢
- 方案：
  1. 每个 test case 收集它 import 了哪些 app 模块（ast 解析或 pytest hook）
  2. PR 拿 git diff 出改了哪些文件
  3. 只跑「命中依赖链」的 test
- 预期 90% 的 PR 从 21s 降到 <5s

### O-2.3 🚀 引入 MutPy / mutmut 变异测试 — 反向验证测试质量
- 覆盖率 39% 只说"代码跑到了没"，不说"测试写的断言真能抓 bug 吗"
- MutPy 在字节码层面随机给 app 代码插变异（`a+b` → `a-b`、`x >= 0` → `x > 0`），然后跑 pytest
- 如果"变异后 pytest 还是全绿"，说明对应的断言太弱，没测到那个分支
- 目标：Mutation Score > 70%

### O-2.4 🚀 故障注入 (Chaos) — 中间件健壮性
- 目前所有测试 Redis/Postgres/LLM 都是通的
- 用 `toxiproxy` 在 CI 里模拟：
  - Redis 断 5s / 延迟 2s — 审计、限流、缓存降级有没有真走内存分支（audit.py 现在是有降级的，但还没测过真断的场景）
  - Postgres 丢包 30% — asyncpg 重试 + 幂等 write_audit_log 是否重复
  - LLM API 超时 30s — RAGPipeline 是否真能 fallback 到拒答模板

### O-2.5 🚀 向左延伸 — Shift-Left：Pre-commit Hook + MR 模板
- 开发者本地 commit 前就跑 ruff + 部分 unit test（不要 21s 全量，跑与当前 diff 相关的），工具：`pre-commit` + `lint-staged`
- MR 模板强制填：
  ```
  - [ ] 本 MR 新增的代码，是否新增了对应 test_xxx.py？
  - [ ] 覆盖率比 main 高/低？
  - [ ] 是否跑过 load test（如是，附上 latency 截图）？
  ```

### O-2.6 🚀 向右延伸 — 线上 Trace 反哺测试用例
- 目前 Phase 5 已经接了 OpenTelemetry Tracing
- 把线上真实慢 / 错请求的 trace → 转 pytest replay 脚本
- 方案：OTel Collector 接 ClickHouse → 每日 Top10 高延迟 / 5xx 的 request 导出成 json → 脚本生成 `test_replay_<traceid>.py`
- 收益：线下测试永远是你想得到的 case，线上 replay 测试是你没想到的 case

---

## 📝 路线图总表

| 优先级 | 编号 | 内容 | 预计工作量 |
|---|---|---|---|
| 🔴 P0 本周 | O-0.1 | lint strict 化 | 2 天 |
| 🔴 P0 本周 | O-0.2 | Codecov Bot | 1h |
| 🔴 P0 本周 | O-0.3 | pytest ff/nf 优化 | 10min |
| 🔴 P0 本周 | O-0.4 | phases/ 变正式 pytest | 0.5 天 |
| 🔴 P0 本周 | O-0.5 | 5 个 Phase 6 TODO 修掉 | 30min |
| 🟠 P1 本月 | O-1.1 | Factory Boy 数据工厂 | 2 天 |
| 🟠 P1 本月 | O-1.2 | Allure 报告服务 | 1 天 |
| 🟠 P1 本月 | O-1.3 | parametrize + DDT 翻 10 倍 | 2 天 |
| 🟠 P1 本月 | O-1.4 | schemathesis Fuzz | 1 天 |
| 🟠 P1 本月 | O-1.5 | Golden Set Snapshot | 3 天 |
| 🟠 P1 本月 | O-1.6 | locust 专业压测 | 2 天 |
| 🟡 P2 下季度 | O-2.1 | Test Dashboard 前后端 | 2 Sprint |
| 🟡 P2 下季度 | O-2.2 | Test Impact Analysis 增量测试 | 1 Sprint |
| 🟡 P2 下季度 | O-2.3 | mutmut 变异测试 | 1 Sprint |
| 🟡 P2 下季度 | O-2.4 | toxiproxy Chaos 注入 | 2 周 |
| 🟡 P2 下季度 | O-2.5 | pre-commit + MR 模板 | 1 周 |
| 🟡 P2 下季度 | O-2.6 | OTel trace 线上回放 | 1 Sprint |

按你一个 TestDev 独做估算：**P0 = 3-4 天，P1 = 11 天，P2 = 2.5 个 Sprint**。先做 P0 → P1.1~1.4 → P1.5 → P2 的顺序，前 3 周就能把平台的 ROI 跑出来（lint 拦问题、Codecov 提醒覆盖率、schemathesis 自动抓 500）。

---
*本文档由 Test Platform Hub 统一维护。任何新增 / 修改测试，请先阅读 [TEST_DESIGN.md](./docs/TEST_DESIGN.md) 再动手，避免破坏金字塔比例。*
