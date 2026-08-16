# CI/CD 操作手册（CI Playbook）

> 对应流水线文件：[.github/workflows/ci.yml](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/.github/workflows/ci.yml)
> 配置快照：[../configs/ci.yml.snapshot](../configs/ci.yml.snapshot)

---

## 1. 触发时机

| 事件 | 是否触发 | 说明 |
|---|---|---|
| push → `main` / `dev` | ✅ | 全量 lint + test + docker-build |
| 任何 PR (含 fork PR) | ✅ | lint + test；**fork PR 的 docker-build 自动跳过** (防泄露 secrets) |
| 只改 `docs/**` / `*.md` | ❌ | paths 过滤，省时长 |

同分支快速连 push 2 个 commit → **自动 cancel 前一个 workflow run**（`concurrency.group = ci-${{ github.ref }}`）。

---

## 2. 三 Job 详解

### Job 1 · lint（ubuntu-24.04, Python 3.11）
| Step | 命令 | 失败策略 |
|---|---|---|
| Checkout | `actions/checkout@v4` | — |
| Setup Python | `actions/setup-python@v5` | — |
| Install deps | `pip install -r backend/requirements.txt` + `pip install ruff mypy` | — |
| ruff check | `ruff check backend/app backend/tests` | continue-on-error ✅ （先基线再收紧，P0 O-0.1 会关掉） |
| ruff format | `ruff format --check backend/app backend/tests` | continue-on-error ✅ |
| mypy | `mypy backend/app` | continue-on-error ✅ |

### Job 2 · test（needs: lint）
Service 容器：
- `pgvector/pgvector:pg16`（⚠️ **不是**纯 postgres，否则 `CREATE EXTENSION vector` 会挂）
- `redis:7-alpine`

Step 顺序：
```
pg_isready → CREATE EXTENSION vector → alembic upgrade head
  → pip install
  → pytest tests/unit tests/integration tests/api tests/phases
       -m "not load and not slow"
       --maxfail=5
       --cov 生成 coverage.xml + test-results.xml
  → upload-artifact: reports/ + htmlcov/ (保留 14 天)
```

### Job 3 · docker-build（needs: test）
- `if: github.event_name == 'push' || github.event.pull_request.head.repo.full_name == github.repository`
- 使用 `docker/build-push-action@v6` 带 GHA cache（`mode=max`）
- `push=false`，只验证镜像能构建，不推送到镜像仓库
- 打 tag：`rag-py-backend:ci-${{ github.sha }}`

---

## 3. 如何本地复现 CI 失败

```bat
:: 1. lint 失败
ruff check backend/app --fix          :: 能自动修的先修
ruff format backend/app                :: 自动格式化

:: 2. test 失败（先用 load/slow 排除，和 CI 同条件）
cd backend
pytest tests/unit tests/integration tests/api tests/phases -m "not load and not slow" --maxfail=5 --tb=short
```

如果"本地过 CI 挂" → 重点查：
1. **路由前缀**：本地测试是不是写成了 `/api/xxx`，真实是 `/api/v1/xxx`（见 conftest api_client）
2. **Celery audit 连接 Redis**：测试环境不能有真 `delay()`，conftest 的 api_client fixture 已经打了 monkeypatch
3. **SQLite vs Postgres**：unit 层 SQLite 过 → Postgres JSONB / vector 语法不兼容

---

## 4. Artifact 下载与查看

每个 workflow run → Summary 页 → Artifacts 区可下载：
- `test-reports` → 解压：
  - `reports/test-results.xml` — JUnit，丢进 Jenkins / Allure 能渲染
  - `reports/coverage.xml` — Coverage，给 Codecov / SonarQube
  - `htmlcov/index.html` — 浏览器打开看行级覆盖

---

## 5. 接入 Codecov Bot（路线图 O-0.2）

```yaml
# 在 test Job 最后追加：
- name: Upload to Codecov
  uses: codecov/codecov-action@v4
  with:
    files: backend/reports/coverage.xml
    fail_ci_if_error: false
    token: ${{ secrets.CODECOV_TOKEN }}   # 到 codecov.io 申请
```

效果：PR 里会有 Bot 评论 `Coverage +1.2% (39% → 40.2%)` ✅。

---

## 6. 常见故障排查

| 现象 | 根因 | 修复 |
|---|---|---|
| test Job `alembic upgrade head` 挂 | pgvector 镜像用错，extension 不存在 | service image 改成 `pgvector/pgvector:pg16` |
| pytest 跑 120s 超时 | audit_tasks.delay() 连真 Redis 阻塞 | 确保 api_client fixture monkeypatch 了 `write_audit_log.delay` |
| test_kb_api 返回 404 | 路由前缀少了 v1 | 所有 HTTP 调用必须是 `/api/v1/knowledge-bases` |
| 覆盖率突然掉 5%+ | 新代码没写对应 test_ | PR 评论里 Codecov Bot 会高亮哪些行没覆盖 |
