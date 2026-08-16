# 测试设计规范（Test Design Guideline）

> 适用于本仓库 backend/tests 下 unit / integration / api / load 四层。
> 先读本文件再新增 / 修改测试，避免破坏金字塔比例和复用规则。

---

## 1. 金字塔比例（目标）

| 层 | 占比 | 单条耗时 | 定位问题 |
|---|---|---|---|
| unit | ~60% | <100ms | 函数级 Bug |
| integration | ~25% | 100ms~1s | 模块协作 Bug |
| api (contract) | ~12% | 1s~3s | 路由/鉴权/契约 Bug |
| load / e2e | ~3% | 10s+ | 性能、死锁、并发 Bug |

**新增用例前先问自己：这个用例能不能下沉到 unit 层写？能下沉就下沉。**

---

## 2. 夹具复用规则（禁止手写重复 DB setup）

所有公共夹具统一在 [backend/tests/conftest.py](file:///c:/Users/LEgion/Desktop/backend/RAG-PY/backend/tests/conftest.py)，**禁止在单个 test_xxx.py 里再写 engine/session/fake_redis**。

| 你需要什么 | 直接用这个 fixture |
|---|---|
| 异步 SQLite 内存库 session | `db_session` |
| 内存假 Redis（支持 TTL） | `fake_redis` + `mock_redis_module` |
| 假 LLM（不耗 Token） | `mock_llm_provider` |
| 普通登录用户（含 .auth_headers） | `test_user` |
| 管理员用户 | `admin_user` |
| 已建好的 1 条 KB 行 | `test_kb` |
| 已建好的 1 条 Document 行 | `test_document` |
| HTTP 客户端（真中间件 / 假 TCP） | `api_client` |

需要跨外键的复杂组合数据？→ P1 迭代接 `factory-boy`（见 README 路线图 O-1.1）。

---

## 3. AAA / Given-When-Then 写法

### 3.1 Unit → AAA
```python
def test_hash_password_抗碰撞():
    # Arrange
    pw1, pw2 = "Test123!", "Test123!!"
    # Act
    h1 = hash_password(pw1)
    h2 = hash_password(pw2)
    # Assert
    assert h1 != h2
    assert verify_password(pw1, h1)
```

### 3.2 Integration / API → Given-When-Then
```python
async def test_refresh_token_旋转后旧token失效(self, api_client, test_user):
    # Given  已经拿到 dual token
    login_resp = await api_client.post("/api/v1/auth/login", json={...})
    old_refresh = login_resp.json()["refresh_token"]

    # When   用 refresh 换一对新 token
    new_resp = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})

    # Then   新 token 能访问 me；旧 refresh 再用 → 401
    me = await api_client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {new_resp.json()['access_token']}"})
    assert me.status_code == 200
    reuse_old = await api_client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert reuse_old.status_code == 401
```

---

## 4. 断言的"三条腿原则"

每个业务函数 / 每个写接口，必须至少覆盖：
1. ✅ 正向 happy path → 2xx / 返回预期数据
2. ❌ 异常输入 / 异常依赖 → 4xx / raise / fallback
3. 🧱 边界值（0、空串、超长、并发重复）→ 不能 500

---

## 5. 标记（Mark）约定

| mark | 含义 | 默认 CI 是否执行 |
|---|---|---|
| `@pytest.mark.slow` | 单条 > 5s（例如真 embedding 初始化） | ❌ 排除 |
| `@pytest.mark.load` | 并发 / 压测（`tests/load/` 下的） | ❌ 排除，手动跑 run_load_tests.bat |
| （默认无 mark） | 普通 unit/integration/api | ✅ |

---

## 6. 避免"假阳性"的陷阱清单

| 陷阱 | 解决 |
|---|---|
| 用**可选认证**的端点（如 `/healthz`）测 token 吊销 → 永远 200 | 必须用 POST `/knowledge-bases` 这种**强制认证**的端点 |
| token 黑名单只写 Redis → Redis 挂了会被绕 | 黑名单**先写内存 dict 再异步写 Redis**，详见 `revoke_token` 实现 |
| 测另一个用户删除 → 断言 403 → 后端合并成 404 防 IDOR | 断言用 `in (403, 404)` 或直接 `== 404` |
| Celery audit 任务 delay() → 测试环境没 Redis 卡死 114s | conftest 的 api_client fixture 已经 monkeypatch `.delay`，不要自己再定义 api_client |

---

## 7. 新增用例流程（SOP）

```
1. 确定被测对象属于哪一层 → unit/api/integration/load
2. 打开对应 templates/test_xxx_template.py 复制骨架
3. 填 fixture（能复用的绝不手写）
4. 三条腿各至少 1 条断言
5. 本地 pytest 单文件跑通 → 再 run_all_tests.bat 全量
6. pytest -v --cov=app.services.xxx tests/unit/test_xxx.py 看单模块覆盖率
7. 覆盖率没比基线掉，提交
```
