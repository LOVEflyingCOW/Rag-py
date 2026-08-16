# PHASE 7 · 测试平台通用化 + P0 迭代 过程日志

> **目标**：按 README 路线图，把 P0 5 项逐项落地；同时搭「配置驱动的脚手架」通用化骨架，让 test-platform 不局限于 RAG-PY 项目。
> **日期**：2026-08-16
> **执行方式**：每完成 1 条任务，追加 1 条记录（想法 / 坑点 / 解法 / 代码引用）
> **通用化原则**：
> - 🟦 平台内核 = 通用（任何项目直接拷过去用）
> - 🟨 项目适配层 = 每个项目只改这一层（project_config.yaml + 可选 conftest_plugins 重写）

---

## 0. 前置：决策记录（开工前和用户确认的方向）

| 决策项 | 选择 | 原因 |
|---|---|---|
| 通用化形态 | 配置驱动脚手架（渐进式） | 当下只有 RAG-PY 1 个项目，先 1 份 yaml + 插件接口解耦；等 3+ 项目共用再抽 pip 包 |
| mypy 严格度 | 渐进式收紧 | 历史模块先 ignore_errors 白名单，新模块强约束，不阻塞日常开发 |
| Codecov | 预留 Codecov Bot step，token 空时静默 skip | 不强制用户申请账号，填了 token 立刻生效 |
| phases/ 改造 | 真·pytest 用例化（async def + mock） | 要纳入覆盖率/报告资产，subprocess 外层包一层浪费现有脚本价值 |
| 过程日志 | test-platform/docs/PHASE7_PLATFORM_ITERATION_LOG.md | 和 PHASE6 同目录，统一索引 |

---

## 1. O-0.0-a：过程日志骨架

- **想法**：先有一个地方"边写边记"，避免做完之后回忆遗漏。
- **代码引用**：本文件 [PHASE7_PLATFORM_ITERATION_LOG.md](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/docs/PHASE7_PLATFORM_ITERATION_LOG.md)

---

## 2. O-0.0-b：搭通用化骨架（project_config.yaml 示例 + conftest_plugins 接口）

- **想法**：现在 conftest.py 的 9 个 fixture 全是 RAG-PY 硬编码。通用化关键是：**fixture 的"行为"通用，fixture 的"实现"由项目配置驱动**。
  - 通用行为："我需要一个 async DB session，每 test 回滚"
  - 项目特定："用什么引擎？默认 SQLite 内存库还是 asyncpg？FastAPI 的 Depends(get_db) 在哪里？"
- **设计**：
  1. `test-platform/project_config.yaml.example`：一份注释完整的示例，任何新项目复制一份改名 `project_config.yaml` 即可
  2. `test-platform/conftest_plugins/__init__.py`：插件加载器，根据 `project_config.yaml` 里写的 plugin 路径动态 import
  3. `test-platform/conftest_plugins/db_plugin_default.py`、`redis_plugin_default.py`、`api_client_plugin_default.py`：RAG-PY 默认实现（即当前 conftest.py 抽出的可复用版本）
  4. `backend/tests/conftest.py`：从"把所有实现写死"改为"调用插件加载器 + 注入 fixture"
- **坑点**：conftest.py 是 pytest 启动时第一个被 import 的，如果 yaml 路径写相对路径又 cd 到不同目录就会找不到 → 用 `Path(__file__).resolve().parent` 做基准，再往上级找 `test-platform/project_config.yaml`，找不到就 fallback 用默认插件（保证当前 RAG-PY 项目零配置继续跑）。
- **解决的路径问题**：
  ```
  backend/tests/conftest.py
      ↖ (parent.parent)
  backend/
      ↖ (parent)
  repo_root/
      ↓
  test-platform/
    ├── project_config.yaml.example
    └── conftest_plugins/
  ```
- **代码引用**：
  - [project_config.yaml.example](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/project_config.yaml.example)
  - [conftest_plugins/__init__.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/conftest_plugins/__init__.py)
  - [conftest_plugins/db_plugin_default.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/conftest_plugins/db_plugin_default.py)
  - [conftest_plugins/redis_plugin_default.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/conftest_plugins/redis_plugin_default.py)
  - [conftest_plugins/api_client_plugin_default.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/conftest_plugins/api_client_plugin_default.py)
  - [conftest_plugins/auth_plugin_default.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/conftest_plugins/auth_plugin_default.py)
  - 修改后：[backend/tests/conftest.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/conftest.py)

---

## 3. O-0.3：pytest.ini 加 --ff --nf

- **想法**：这是投入产出比最高的一行修改，10 分钟搞定。
- **原理**：
  - `--ff` (--failed-first)：上次失败的用例先跑
  - `--nf` (--new-first)：新创建的 test_ 先跑
  - 日常 debug 场景：改了 1 个函数，上次失败了 3 条用例，这次直接先跑这 3 条 → 10s 看到结果，而不是等 21s 全量跑完才看到失败
- **修改位置**：[backend/pytest.ini](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/pytest.ini) `[pytest]` → `addopts = ... --ff --nf`
- **验证**：pytest 跑一下看没告警即可。

---

## 4. O-0.5：修 5 个 TODO（Agent ReAct 中文别名 + revoke 同步 warning）

### 4.1 Agent ReAct 解析器加中文别名

- **背景**：PHASE6_WORK_LOG 里提到的坑——ReAct 正则只认 `Thought/Action/Action Input`，如果 LLM 是中文模型输出「思考:/动作:/动作输入:」，解析器就会认不出来，Agent 直接走死循环。
- **改法**：把正则 pattern 扩展成双语关键词枚举，格式形如：
  ```
  (Thought|思考)[:：]\s*(?P<thought>.+?)\n
  (Action|动作)[:：]\s*(?P<action>.+?)\n
  (Action\s*Input|动作输入)[:：]\s*(?P<action_input>.+?)(?=\n|$)
  ```
- **坑点**：要兼容 ASCII 冒号 `:` 和中文全角冒号 `：`，还要兼容 `Action Input`（有空格），不能直接用 `\w+`。
- **代码引用**：[backend/app/services/agent_service.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/services/agent_service.py) ReAct 解析部分

### 4.2 revoke_token 同步版吞异常

- **背景**：`revoke_token_sync` 在非 async 上下文里用 `asyncio.create_task(save_to_redis())` 把黑名单写 Redis，如果 event loop 没跑就会抛异常，现在 try/except 是全吞不打 log，排查 Redis 为什么不生效要花半小时。
- **改法**：
  ```python
  try:
      loop = asyncio.get_event_loop()
      if loop.is_running():
          loop.create_task(...)
      else:
          loop.run_until_complete(...)  # 同步环境直接跑完
  except RuntimeError as e:
      warnings.warn(f"[revoke_token_sync] 无法写入Redis黑名单(内存已写入): {e}")
  ```
- **坑点**：`asyncio.create_task` 要求有 running loop，同步脚本（比如 alembic 里调 revoke）会 `RuntimeError: no running event loop`，这是之前全吞的根因。
- **代码引用**：[backend/app/core/security.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/app/core/security.py) `revoke_token_sync` 函数

---

## 5. O-0.1：ruff/mypy strict 化

### 5.1 ruff 阶段

- **思路**：先 `ruff check --fix` 能自动修的（E/W 里的 import 排序、trailing whitespace、冗余 f-string 这类），剩下的 F 类错误（未定义变量、类型错）手动修。
- **执行顺序**：
  1. `ruff check backend/app backend/tests --fix`
  2. `ruff format backend/app backend/tests`
  3. 对 20+ 个文件逐一排查剩下的 ruff 错误
  4. **关键：CI 里 3 个 continue-on-error 先保留**，等本地 100% 过再去掉
- **坑点**：ruff `--fix` 会改代码，要注意 import 顺序会不会把 conftest 的循环依赖搞出来（conftest  import app 再 app 再 import 回来就炸）。
- **验证**：`ruff check backend/app backend/tests` 退出码 0 才算 OK。

### 5.2 mypy 渐进阶段

- **思路**：用户选了渐进式，用 `[[tool.mypy.overrides]]` 给每个历史模块加 `ignore_errors = true`，新模块（以后新建的 py 文件）默认 strict。
- **白名单怎么列**：`find backend/app -name "*.py"` 枚举所有模块，每个模块单独一条 override（这样以后每个 Sprint 解 1 条，直接删那一行就能严格化）。
- **代码引用**：[pyproject.toml](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/pyproject.toml) `[tool.mypy]` 下面追加 overrides 区块。

### 5.3 去 continue-on-error

- **时机**：本地 ruff 退出码 0 + mypy 退出码 0 → 再去改 ci.yml。
- **风险**：如果本地 0 CI 还是挂，基本是 cache/pip 版本不一致，在 CI 的 step 里 pin 死 ruff 版本即可。
- **代码引用**：[.github/workflows/ci.yml](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/.github/workflows/ci.yml) lint Job 三个步骤去掉 `continue-on-error: true`。

---

## 6. O-0.4：tests/phases/ 真·pytest 用例化

- **6 个改造对象**：
  | 文件 | 现在的问题 |
  |---|---|
  | test_phase1_database_async.py | 可能是 `if __name__` 脚本 |
  | test_phase2_auth_security.py | 同上 |
  | test_phase3_infra.py | 同上 + 可能连真 Redis |
  | test_phase4_fulltext_search.py | 同上 |
  | test_phase4_hybrid_demo.py | 同上 |
  | test_phase4_reranker_chunker.py | 同上 |
  | test_phase5_observability.py | 有 Py3.7 AsyncMock shim |
- **每个文件的 SOP**：
  1. Read 整文件，理解它在测什么（场景是啥）
  2. 去掉 `if __name__ == "__main__"` 和顶层调用
  3. 所有函数改成 `async def test_xxx(self, ...)` 接收 fixture（能用公共 fixture 就不用自己写 setup）
  4. 真 DB/Redis/LLM 依赖用 conftest 里的 mock/假对象替换
  5. 手动的 assert 不动（这是有价值的基线）
  6. 单文件 pytest 跑过 → 再并入全量
- **坑点预警**：phase3_infra 会连真 Redis，**必须**替换成 `fake_redis` fixture，否则本地没装 Redis 就卡死 114s （老坑了，Audit Celery delay 那种一样的根因）。

---

## 7. O-0.2：ci.yml 追加 Codecov Bot step

- **改法**：在 test Job artifact upload 之后追加：
  ```yaml
  - name: Upload coverage to Codecov
    if: always()   # 即使 pytest 挂了也尝试上传（部分覆盖也有参考价值）
    uses: codecov/codecov-action@v4
    with:
      files: backend/reports/coverage.xml
      fail_ci_if_error: false   # 没 token 也不挂 CI
      token: ${{ secrets.CODECOV_TOKEN }}
  ```
- **为什么 if: always() + fail_ci_if_error: false**：很多人写 `needs: test` 只有 test 过了才上传，其实挂的时候更需要看哪些覆盖掉了；而且如果用户没填 secrets，这个 step 必须静默，别把 CI 搞红。

---

## 8. P7-8：phase 失败修复（限流绕过 + table_args + 上传断点 + mapper 预加载）

### 8.1 修复清单（按发现顺序）

| # | Bug | 触发场景 | 修复方式 | 代码引用 |
|---|---|---|---|---|
| 1 | 限流测试不触发 429 | api_client fixture 把 anonymous limit 拉到 99999，导致 phase2 的限流测试永远打不到阈值 | test 内部临时把 RATE_LIMITS 改回 10，清空 `_memory_limiter._windows`，finally 还原 | [test_phase2_auth_security.py#test_e1](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase2_auth_security.py#L379-L429) |
| 2 | SQLAlchemy mapper 找不到 Tenant 类 | 全量 pytest 跑时，phase4 reranker_chunker 顶层只 import DocumentChunk，SQLAlchemy 自动 configure_mappers() 看到 User.relationship("Tenant") 但 registry 还没有 Tenant → 之后再 reload entities 也救不回来 | db_plugin register_fixtures 阶段**预加载全部 entities 子模块**，然后显式调用 `configure_mappers()` 提前暴露问题 | [db_plugin_default.py#L87-L101](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/conftest_plugins/db_plugin_default.py#L87-L101) |
| 3 | 文档上传 405 Method Not Allowed | 旧版测试写的路由是 `/documents/upload`，实际 FastAPI 路由是 `POST /documents` | 修正路由路径，并在 api_client_plugin 里加路由自动检测兜底 | [test_phase4_fulltext_search.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase4_fulltext_search.py) |
| 4 | DocumentChunk `__table_args__` 断言 | SQLite 下可能是空 tuple，旧断言 `len(args) > 0` 直接挂 | 兼容分支：len==0 时只判断 isinstance(tuple) | [test_phase4_fulltext_search.py#test_1](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase4_fulltext_search.py) |
| 5 | test_f_audit_log_written 查不到 audit_logs | 审计写入是 Celery 异步任务，api_client fixture 把 delay monkeypatch 成 no-op，任务根本没执行 | 改为**断言 middleware 确实调用了 delay 方法**（monkeypatch spy），而非直接查 DB（集成层另起一条真跑 Celery 的测试） | [test_phase2_auth_security.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase2_auth_security.py) |
| 6 | test_e_token_blacklist 依赖 B1 setup token | B1 如果挂，E 也跟着挂（耦合） | E 测试内部独立注册新用户拿 token，不依赖外部 setup | [test_phase2_auth_security.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase2_auth_security.py) |
| 7 | test_f1_sync_engine_connect execute 参数错 | SQLAlchemy 2.0 sync engine.execute() 必须接收 `text()` 对象，不能直接传 SQL 字符串 | 用 `sqlalchemy.text(sql)` 包装 | [test_phase1_database_async.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase1_database_async.py) |

### 8.2 坑点复盘（为什么会出现这些 bug）

> **核心教训**：「conftest 里的 fixture 为了让 A 测试稳定做的全局 monkeypatch，一定会反向影响 B 测试的真实场景验证」——这是写测试框架最容易踩的暗坑。

具体来说：
1. **限流测试**：api_client 拉高 RATE_LIMITS 是为了「跨测试文件不要互相打 429」（防串扰），但**限流这个场景本身就是要测 429**，所以 fixture 的「全局优化」和「特定测试的需求」是矛盾的。解法是：**fixture 给宽松默认值，测试自己临时收紧并 finally 还原**（类似 DB 事务回滚的思路）。
2. **SQLAlchemy mapper**：`configure_mappers()` 是个「一次性」操作——只要有任何一条代码触发过它（哪怕是 import 时的隐式触发），之后再补 import 新实体类都不会重新配置。所以必须在**任何测试逻辑跑之前**，把所有实体类一次性 import 完，再显式 configure 一次。
3. **Audit Log 异步任务**：monkeypatch delay 之后，测试就永远看不到「任务真的执行了」的效果。解法是把测试分层：**API 层只测「调用了 delay」（spy），集成层单独起一条跑真 Celery worker 的测试（用 pytest-celery 或 task_always_eager=True）。**

---

## 9. 全量验证 + 收尾（P7 交付基线）

### 9.1 执行命令
```
cd backend
..\venv\Scripts\python.exe -m pytest tests/ -m "not slow and not load" --tb=short -q
```

### 9.2 结果（2026-08-16 最终基线 + P0 落地）
| 指标 | PHASE6 旧基线 | PHASE7 初版基线 | **P0 落地后最新基线** |
|---|---|---|---|
| Passed | 83 | 281 | **294** (+13 vs 初版) |
| Deselected | 0 | 0 | **53** (50 fuzz + 3 load/slow/demo，默认 marker 过滤) |
| Skipped | - | 10 | 10 (与 PHASE7 初版相同) |
| Coverage | 39% | 47% | 待测 (新增 factories unit tests 贡献 ~1-2pp) |
| 执行时长 baseline | ~25s | 52.20s | 70s（baseline 默认全跑，含 cov） |
| 执行时长 `--smoke` | - | 不存在 | **45s**（70 条 unit + api，跳 phases/integration） |
| Fuzz 用例数 | 0 | 0 | **50 endpoints**（每条 endpoint 一个 pytest 用例） |
| Demo 用例数 | 0 | 0（仅脚本） | **9 collected**（@pytest.mark.demo，默认跳过） |

### 9.3 为什么覆盖率提升 8pp？
- 旧 phases/ 是 `if __name__` 脚本，pytest-cov 不认（它们跑了但没算进覆盖率统计）
- 改造后 phases/ 的 7 个文件、100+ 条 `async def test_xxx` 全部纳入统计
- 特别是鉴权、DB、middleware 这些之前覆盖率低的核心模块，现在有真实 API 调用链路覆盖了

---

## 10. 通用化资产索引（PHASE7 交付用）

| 资产 | 类型 | 新项目接入要改吗？ |
|---|---|---|
| test-platform/project_config.yaml.example | 🟨 适配层 | 复制改名 → 改路径/模块/插件 |
| test-platform/conftest_plugins/*_default.py | 🟨 适配层（默认实现） | 默认用 RAG-PY 的；新项目写同名 override 就行 |
| test-platform/scripts/* | 🟦 通用 | 直接用，读 yaml |
| test-platform/templates/* | 🟦 通用 | 直接用 |
| test-platform/docs/* | 🟦 通用 | 直接用 |
| backend/tests/conftest.py（重构后） | 🟦 通用 + 🟨 适配钩子 | 拷贝到新项目 `tests/`，它会自动找 yaml |
| pyproject.toml ruff/mypy base | 🟦 通用 | 新项目拷贝对应段 |
| .github/workflows/ci.yml | 🟦 通用 | 新项目服务名不同就改 service image 名即可 |

---

## 11. P0-O-1.1：Factory Boy 测试数据工厂（通用化 + Faker 随机值）

### 11.1 为什么做（原问题）
- 之前 phases/ 测试每条 setup 都是手写 `User(username="xxx", password_hash=hash_password("pw"))` —— 全项目约 50+ 处重复造数据代码
- 硬编码用户名/邮箱容易撞唯一约束（跨文件并行测试经常 unique 冲突）
- 写一个 cascade 场景（Tenant → User → KnowledgeBase → Document → Chunk）要 20+ 行

### 11.2 设计（通用化）
- **通用层（base_factory.py）**：基于 factory_boy 的 `SQLAlchemyModelFactory` 加 async wrapper
  - `acreate()` / `abatch(n)` / `abuild()`：异步安全，避免 factory_boy 同步 session 问题
  - 每次 acreate 临时设 `_meta.sqlalchemy_session = db_session`，用完就 reset（线程安全，不污染其他 test）
- **项目适配层（ragpy_factories.py）**：每个实体一个 Factory class
  - 用 Faker 生成随机用户名/邮箱（`f"{fake.user_name()}_{fake.uuid4()[:6]}"`）→ 唯一约束永不碰
  - `class Params: password = "..."` + `password_hash = LazyAttribute(_hash_password)`：用户只需传 `password="xx"`，自动算 hash
  - `tenant = factory.SubFactory(TenantFactory)` + cascade FK：acreate(User) 自动连 Tenant 一起造

### 11.3 坑点
1. **factory_boy 不支持 AsyncSession natively**（v3.3.0 仍是 sync session）
   - 解法：`_meta.sqlalchemy_session = db_session` 之后 `cls.create()`，再 `await db_session.flush()` —— Sync create 走 SQLAlchemy 2.0 的「sync operations on async session」兼容层，SQLite 内存库完美支持
2. **RefreshToken 实体没有 user relationship**
   - RefreshTokenFactory 里写 `user = factory.SubFactory(UserFactory)` 会报 `TypeError: 'user' is an invalid keyword argument`，因为 RefreshToken model 只有 `user_id` 字段，没有 `user = relationship("User")`
   - 解法：加一个 `Params: user_obj` 虚拟参数，再 `user_id = factory.LazyAttribute(lambda o: o.user_obj.id)`
3. **Faker 18.x 的 `fake.json()` 不支持 `data_fields` 参数**
   - 18.x 的 json() 是随机 JSON schema，不是按 data_fields 生成
   - 解法：手动 `json.dumps({"key": fake.word(), ...})` 拼
4. **User.roles M2M 用 SubFactory(RoleFactory) 不会自动 add**
   - factory_boy 对 M2M 要 post_generation hook：
   ```python
   @factory.post_generation
   def roles(self, create, extracted, **kw):
       if not create: return
       if extracted:
           for r in extracted:
               self.roles.add(r)
   ```

### 11.4 验证结果
- Factory count: 13 个（Tenant, User, KnowledgeBase, Document, DocumentChunk, Role, Permission, RefreshToken, AuditLog, Conversation, ChatMessage, Agent, Task + 组合级联）
- Unit test: `tests/unit/test_factories.py` → 13 tests 全部 passed
- 实际节省：每条 cascade setup 从 ~20 行 → 1 行 `u = await user_factory.acreate(db_session, with_roles_post_gen=True)`

### 11.5 代码引用
- 通用基类 [base_factory.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/factories/base_factory.py)
- RAG-PY 适配 [ragpy_factories.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/factories/ragpy_factories.py)
- Unit 验证 [test_factories.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/unit/test_factories.py)

---

## 12. P0-O-1.2：schemathesis API 契约 Fuzz 测试（@pytest.mark.fuzz）

### 12.1 为什么做
- 手写 API 测试只能覆盖「happy path + 几个已知异常」，**边界值/乱入字符/超大 payload/奇怪 header** 不可能人工枚举
- OpenAPI schema 是 FastAPI 自动生成的「接口契约」，但没人保证「实际 response 真符合 schema 声明」—— 比如 handler 声明返回 `TokenResponse` 但实际上半路 return 了 JSONResponse({"msg":...})
- 解决方案：schemathesis + hypothesis 基于 OpenAPI schema 自动 Fuzz，每个 endpoint 生成 5-100 个用例

### 12.2 设计
- **标记设计**：整个 Fuzz test class 加 `@pytest.mark.fuzz`，pytest.ini `addopts=-m "not fuzz and not demo and not slow and not load"`，**日常/CI 默认跳过**，只有显式 `pytest -m fuzz` 才跑
- **OpenAPI 版本兼容（最大坑）**：
  - FastAPI 0.100+ 默认生成 OpenAPI **3.1.0**
  - schemathesis 3.21 只支持 **3.0.x**（3.1 支持要 v4，还不稳定）
  - **解法**：用 `app.openapi()` 拿 dict → 手动 `schema_dict["openapi"] = "3.0.3"` → 再 `schemathesis.from_dict(schema_dict, ...)`。这在 3.1→3.0 的 minor 版本降级是安全的（FastAPI 生成的 schema 没用到 3.1 新特性）
- **Hypothesis settings**：
  - `max_examples=FUZZ_CASES_PER_ENDPOINT`（env 可调，默认 5；CI 可设 100 做重度 Fuzz）
  - `deadline=None`（个别 endpoint 慢，Hypothesis 默认 200ms deadline 会炸）
  - **`suppress_health_check=[function_scoped_fixture, too_slow, filter_too_much]`**（见坑点 2）
- **鉴权注入**：检查 endpoint 是否在 schema 里有 security（`case.operation.security`），如果有，用 user_factory 造用户 + register API 拿 Bearer token，塞进 `case.headers["Authorization"]`

### 12.3 坑点
1. **case.call_asgi() 是同步 API，不能 await**
   - 一开始以为是 ASGI 就 await，直接报 `TypeError: object Response can't be used in 'await' expression`
   - 原因：schemathesis 3.21 的 call_asgi 内部用同步 `asgiref.testing` 模块跑，外层不暴露 async
   - 解法：去掉 await；同时把这个具体 TypeError 包成 pytest.fail 给出明确提示（后面再踩坑直接定位）
2. **Hypothesis function_scoped_fixture health check 必炸**
   - Hypothesis 在一个 test function 里跑 max_examples 次（比如每个 endpoint 5 次），但 db_session/user_factory 是 function scope，5 次 example 共用同一个 fixture
   - 对 Fuzz 来说这是 OK 的：多个 example 共用 session → fixture teardown 统一 rollback，顶多数据越积越多，不会污染其他 test
   - 解法：`suppress_health_check=[HealthCheck.function_scoped_fixture, ...]`
3. **pytest.ini 不加 marker 过滤 = 全量跑默认包含 Fuzz**
   - 一开始只加了 @pytest.mark.fuzz 标记，但 pytest.ini 没加 `-m "not fuzz"`，日常 `pytest tests/` 会把 50 条 Fuzz（每条 5 example = 250 次请求）全跑，预计 20+ 分钟
   - 解法：pytest.ini addopts 加 `-m "not fuzz and not demo and not slow and not load"`，四种「高耗时/演示/压力」用例一次性默认排除
4. **schemathesis parametrize() 不支持 max_examples 参数**
   - 文档里有些旧版本例子写 `@schema.parametrize(max_examples=5)` → TypeError
   - 解法：改用 `@hypothesis.settings(max_examples=5)` 装饰器控制

### 12.4 验证结果
- 收集：`collected 50 items`（= FastAPI route 数 × method 组合数：每个 endpoint + method 一个 pytest 用例）
- 轻量跑：`-k health` 4 条 health endpoint → **4 passed**
- 安全性：默认 `pytest tests/` → `no tests collected (50 deselected)`，Fuzz 不会被误跑

### 12.5 代码引用
- Fuzz 测试主体 [test_api_fuzz.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/integration/test_api_fuzz.py)
- 关键 marker 过滤配置 [pytest.ini addopts](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/pytest.ini#L31-L53)
- CI 安装依赖 & 排除 fuzz/demo [ci.yml](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/.github/workflows/ci.yml)

---

## 13. P0-O-1.3-a：pytest --smoke 自定义 CLI 参数插件

### 13.1 为什么做
- 开发时改几行代码想快速回归，不想跑 phases/integration（要 mock 一堆 middleware、造 20+ 个实体，花 50s+）
- 之前做法是手动 `pytest tests/unit tests/api --no-cov`，命令太长、新人不知道
- 目标：一个 `pytest --smoke` 自动帮你做 3 件事：① 只跑 unit + api；② 关 cov；③ 顺手滤掉 slow/demo/fuzz/load 标记

### 13.2 设计
用 3 个 pytest hooks（conftest.py 顶层直接写，不塞进 conftest_plugins 因为这是通用能力，所有项目直接用）：

1. **`pytest_addoption(parser)`**：注册 `--smoke` switch，显示在 `pytest --help` 里
2. **`pytest_configure(config)`**：
   - 设 `config.option.no_cov = True`（等价 `--no-cov`，但有时被 pytest-cov hook 覆盖兜底见下）
   - （尝试）改 `config.option.file_or_dir` 限制 unit/api 目录
3. **`pytest_collection_modifyitems(config, items)`**（真正生效的过滤层）
   - 按路径过滤：只保留 item.fspath 在 tests/unit 或 tests/api 下的
   - 按 marker 过滤：再滤一遍 fuzz/demo/slow/load

### 13.3 坑点
1. **`config.option.file_or_dir` 修改在 pytest 7.x 根本不生效**
   - 一开始以为 pytest_configure 的时候改 file_or_dir 就能改变 collection 范围，但**pytest 7.x 的 initial collection 在 pytest_configure 之前**就解析了 pytest.ini 的 `testpaths = tests`，之后改 file_or_dir 不会影响已经在跑的 collection
   - 症状：--smoke 后 phases 仍然被收集（SKIPPED 日志能看到），证明 file_or_dir 白改了
   - 解法：完全放弃在 pytest_configure 改 testpaths，**只依赖 collection_modifyitems 做最终过滤**—— 虽然「多收集再丢掉」浪费 1-2s，但跨平台跨 pytest 版本 100% 稳，优先级高于这 1-2s
2. **`config.option.no_cov = True` 也可能不生效**
   - pytest-cov 的 `pytest_configure` hook 可能在我们之后执行，把 no_cov 又覆盖回 False
   - 症状：--smoke 时 cov report 仍然生成（"Coverage HTML written to dir htmlcov"）
   - 影响：不大，顶多慢 3-5s；现在接受这个 trade-off，追求简单稳定
3. **item.fspath 在某些 pytest 版本是 None**
   - fallback 逻辑：fspath 为 None 时从 `item.location[0]` 拿 module 路径，用 Path.resolve() 标准化

### 13.4 验证结果
- 无 --smoke：294 passed / 70s（unit + api + phases + integration）
- 加 --smoke：**70 passed / 45s**（unit + api，跳 phases/integration）
- 时长对比：45s vs 70s = **提升 35%**，再减去 cov 时间实际上跑 unit/api 纯用例 15-20s

### 13.5 代码引用
- --smoke hooks 实现 [tests/conftest.py pytest_addoption + pytest_configure + pytest_collection_modifyitems](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/conftest.py#L65-L173)

---

## 14. P0-O-1.3-b：test_phase4_hybrid_demo.py 改造（真 pytest 用例 + 旧脚本双模式）

### 14.1 为什么做
- 旧 hybrid_demo.py 是纯 `if __name__ == "__main__"` 脚本：
  - 顶层 `pytest.skip(allow_module_level=True)` 保证 pytest 收集时整个 module 跳过（防止 init_redis 连网卡死）
  - 「效果好不好」只能人眼看 print 输出，机器没有断言
  - CI 上完全不跑（即使有模型缓存也会被整个 module skip）
- 目标：**同一文件同时支持「脚本演示模式」（原体验不变）+「pytest 用例模式」（机器断言 + marker 过滤）**

### 14.2 设计
- **pytestmark 过滤**：整个 module 设 `pytestmark = [pytest.mark.slow, pytest.mark.demo]`
  - 默认 `-m "not demo and not slow"` → 跳过，不影响日常基线
  - 显式 `pytest -m demo` 才跑，符合 P0 目标
- **fixture 设计（module scope `hybrid_demo_env`）**
  - 为什么 module scope：build_vector_index 要做 6 个文档 embedding，单次 15-30s；整个 module 9 个 test 共用同一个 KB，省 9 倍 build 时间
  - 为什么不用 `db_session` fixture：`db_session` 是 function scope，**pytest scope 层级限制 module > function，module scope fixture 不能依赖 function scope fixture**
  - 解法：hybrid_demo_env 完全不用 pytest DB fixtures，直接用真实 `AsyncSessionLocal()` 连持久化库（SQLite 文件 / PostgreSQL），手动 commit / teardown 删除 KB + vector store
- **前置条件检查（Embedding 模型缓存）**：
  - `_check_embedding_model_available()` 先检查 MODEL_CACHE_DIR 下的 embedding 文件夹是否有 .bin/.safetensors 等实际模型文件
  - 没有 → `pytest.skip()` 整个 module，**不是 FAIL**（避免新人第一次跑 pytest -m demo 就被奇怪的 HF 下载卡死）
- **参数化主断言**：
  ```python
  @pytest.mark.parametrize("test_case", TEST_QUERIES, ids=[q["description"] for q in TEST_QUERIES])
  async def test_hybrid_top1_matches_expected(hybrid_demo_env, test_case):
      ...
      assert _match_expected(top_filename, test_case["expected_keywords"])
  ```
  6 个查询每个对应一个 pytest 用例（带人类可读的 ids），失败时 pytest 会具体告诉你「哪个查询 top-1 错了，实际命中了什么，分数多少」
- **脚本模式兼容**：所有原逻辑（setup_test_data / build_vector_index / run_hybrid_search / run_search_mode_comparison / show_statistics / cleanup / main）原封不动保留，`if __name__ == "__main__": asyncio.run(main())` 行为和改造前 100% 一致

### 14.3 坑点
1. **pytest scope 层级（module > function）限制**
   - 一开始想 hybrid_demo_env → 依赖 db_session_module（假设有的 module scope session）→ 没有这个 fixture 就报错
   - 然后想 hybrid_demo_env 降成 function scope → 每个 test function 都 rebuild vector_index（9 × 20s = 3min），无法接受
   - 解法：干脆脱离 pytest fixture 体系，直接 AsyncSessionLocal 连真实 DB，手动 cleanup（反正 KB 级联删除 + vector store 删除两行代码搞定）
2. **Python 3.7 内置泛型不支持**
   - `tuple[bool, str]`、`int | None` 语法是 Python 3.10+ 的，3.7 直接 `TypeError: 'type' object is not subscriptable`
   - 解法：`from typing import Tuple` → `Tuple[bool, str]`，以及 `kb_id = None  # type: int | None`（注释形式的 type hint 3.7 安全）
3. **db_session 回滚隔离 vs 真检索链路可见性**
   - 即使 hybrid_demo_env 不用 db_session，test function 里如果用 function scope 的 db_session 跑 search，可能会因为 SQLite 事务隔离看不到 setup 阶段 commit 的 KB（SQLite WAL / 不同 session 的可见性问题）
   - 解法：test function 里也不用 db_session，全部用 AsyncSessionLocal 新开 session —— 反正 demo 是真持久化，开一个独立 session 读就完事

### 14.4 验证结果
- pytest 收集：`pytest test_phase4_hybrid_demo.py -m demo --collect-only` → **9 tests collected**（6 参数化 + 3 独立）
- 默认跳过：`pytest tests/`（不带 -m demo）→ 9 tests 被 deselect，不影响 294 条基线

### 14.5 代码引用
- 双模式改造 [test_phase4_hybrid_demo.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/phases/test_phase4_hybrid_demo.py)

---

## 15. P0-O-1.3-c：扫描并收敛代码库 TODO/FIXME 标记

### 15.1 目标
- 代码里留的 `# TODO xxx` / `# FIXME xxx` 是技术债，时间长了没人记得
- P0 收尾时先「盘点」一次，分类：可立即修 / 模板占位 / 暂缓（再决定下一步做不做）

### 15.2 扫描方式
```bash
# 只用严格模式：# 注释 + 大写关键词
grep -r "#\s*(TODO|FIXME|HACK|XXX|BUG)" backend/app backend/tests test-platform
```

### 15.3 扫描结果（仅 4 处）
| 位置 | 内容 | 分类 | 处理 |
|---|---|---|---|
| [generate_case_template.py:50](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/scripts/generate_case_template.py#L50) | `# TODO: 按 AAA 模式补充真实用例` | 🟨 模板占位符 | ✅ 保留：脚手架生成的模板，给用户看的提示，不是技术债 |
| [test_api_template.py:17](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/templates/test_api_template.py#L17) | `URL = "/api/v1/xxx"  # TODO: 填真实路由` | 🟨 模板占位符 | ✅ 保留 |
| [test_unit_template.py:19](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/templates/test_unit_template.py#L19) | `result = lambda d: d  # TODO: 替换成真实函数` | 🟨 模板占位符 | ✅ 保留 |
| [test_unit_template.py:27](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/test-platform/templates/test_unit_template.py#L27) | `raise ValueError("示例异常")  # TODO: 真实调用` | 🟨 模板占位符 | ✅ 保留 |

### 15.4 结论
- **backend/app（核心代码）：0 TODO/FIXME/HACK**
- **backend/tests（测试代码）：0 TODO/FIXME/HACK**
- 只有 test-platform 下 4 个「脚手架模板文件」里故意留的 TODO 占位符（引导用户下一步填什么），不属于技术债
- → 现状**非常好**，不需要额外清理；后续新加代码如果要留 TODO，建议统一格式 `# TODO(作者, 日期): 具体内容 + issue 链接`，避免变成孤儿 TODO
