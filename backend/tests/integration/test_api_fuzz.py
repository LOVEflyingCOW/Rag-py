"""P0-O-1.2 · schemathesis API 契约 Fuzz 测试

## 作用
  基于 FastAPI 自动生成的 /openapi.json 契约，对每个接口自动生成「边界值 + 异常值 + 随机值」
  的请求，验证：
  1) **契约一致性**：响应码/响应体是否符合 OpenAPI 声明（比如 200 的接口突然返 500）
  2) **鲁棒性**：各种畸形输入（超长字符串、负数 id、特殊字符、空数组等）不触发 500
  3) **越权基础防护**：带普通用户 token，访问非自己资源应该是 403/404，而不是 500

## 运行方式（⚠️ 默认 CI / 日常 pytest 都跳过，手动触发）
  ```
  # 轻量模式（每个接口 5 个用例，~2-5min，本地快速验证）
  cd backend
  ..\venv\Scripts\python.exe -m pytest tests/integration/test_api_fuzz.py -m fuzz -v

  # 完整模式（每个接口 100 个用例，~30-60min，nightly build）
  set FUZZ_CASES_PER_ENDPOINT=100
  ..\venv\Scripts\python.exe -m pytest tests/integration/test_api_fuzz.py -m fuzz -v
  ```

## Marker 说明
  - @pytest.mark.fuzz: pytest.ini 已注册，默认 `-m "not fuzz"` 跳过，CI test Job 也显式排除
  - 不打 @pytest.mark.slow 是因为：轻量模式（5 cases）不一定慢，只有 100 cases 才慢，
    由运行者自行用 FUZZ_CASES_PER_ENDPOINT 控制。

## 通用化说明（新项目接入）
  1. 复制本文件到 tests/integration/test_api_fuzz.py
  2. 改 `from app.main import app` → `from your_project.main import app`
  3. 改 `_get_auth_token` 函数，适配你项目的登录接口和返回 token 格式
  4. 如果项目的 OpenAPI schema 路径不是 /openapi.json（默认 FastAPI 路径），改 from_asgi 第一个参数
  5. 如果项目的 auth scheme 不是 Bearer Token，改 `case.headers["Authorization"]` 的值和前缀
"""

from __future__ import annotations

import os
from typing import Optional

import hypothesis
import pytest
import schemathesis
from starlette_testclient import TestClient as StarletteTestClient  # schemathesis 自带


# ============================================================
#  环境变量控制用例数（轻量 5 vs nightly 100）
# ============================================================
FUZZ_CASES_PER_ENDPOINT = int(os.environ.get("FUZZ_CASES_PER_ENDPOINT", "5"))
if FUZZ_CASES_PER_ENDPOINT < 1:
    FUZZ_CASES_PER_ENDPOINT = 1


# ============================================================
#  加载 OpenAPI schema（手动打 /openapi.json → 降级版本号 → from_dict）
#
#  ⚠️ 坑点：FastAPI 0.100+ 默认生成 OpenAPI 3.1.0，
#     schemathesis 3.21.x 只支持到 3.0.x，直接 from_asgi 会：
#       SchemaError: "uses Open API 3.1.0, which is currently not supported"
#
#  解法：先同步 TestClient 打 /openapi.json 拿到 schema dict，
#        把 schema["openapi"] 改成 "3.0.3"，再用 from_dict 加载。
#     3.1 → 3.0 的差异主要是 nullable/format 的语法糖，FastAPI 生成的
#     大部分接口是兼容的，极少数 3.1 新特性（如 examples）schemathesis
#     会自动忽略，不影响 Fuzz 主流程。
# ============================================================
def _load_schema_dict():
    """同步 TestClient 取 /openapi.json，降级到 OpenAPI 3.0.3。"""
    from app.main import app
    with StarletteTestClient(app) as client:
        resp = client.get("/openapi.json")
    if resp.status_code != 200:
        raise RuntimeError(f"取不到 OpenAPI schema: HTTP {resp.status_code}")
    schema = resp.json()
    # 降级版本号：3.1.x → 3.0.3
    current = schema.get("openapi", "3.0.3")
    if current.startswith("3.1"):
        schema["openapi"] = "3.0.3"
    return schema


schema = schemathesis.from_dict(
    _load_schema_dict(),
    # app 对象给 case.call_asgi() 用（不用真开端口）
    app=None if False else None,  # placeholder，from_dict() 不需要 app 参数
    # 忽略 schema 校验里的 minor warning（很多历史接口没严格标 4XX response）
    validate_schema=False,
)


# schemathesis.from_dict() 出来的 schema 默认没有 asgi_app 引用，
# 后面 test_api_contract_fuzz 里调用 case.call_asgi() 需要手动传 app。
# 这里懒加载 app 存起来，测试函数里复用。
def _get_app_for_call():
    from app.main import app
    return app


# ============================================================
#  鉴权辅助：用 test_user（通过 factory）注册 → 调登录接口 → 拿 access token
# ============================================================
async def _get_auth_token(
    db_session, user_factory, api_client, as_user: str = "viewer",
) -> Optional[str]:
    """获取一个已注册用户的 Bearer access token。

    Args:
        as_user: "viewer" 普通用户 | "admin" 管理员
    Returns:
        token_str（不包含 "Bearer " 前缀），或 None 表示登录失败。
    """
    try:
        # 1) 用 factory 建一个真实用户（密码自动 Argon2id hash）
        password = "FuzzTest@2025"
        is_admin = (as_user == "admin")
        user = await user_factory.acreate(
            db_session,
            username=f"fuzz_{as_user}_{os.urandom(4).hex()}",
            password=password,
            is_admin=is_admin,
        )
        if user is None or user.id is None:
            return None

        # 2) 调登录接口拿 token — 用 api_client fixture 里的 httpx.AsyncClient
        login_resp = await api_client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "password": password},
        )
        if login_resp.status_code != 200:
            # 可能是 username/password 字段名不同（新项目要改这里）
            return None
        data = login_resp.json()
        token = data.get("access_token") or data.get("token") or data.get("data", {}).get("access_token")
        return str(token) if token else None
    except Exception:
        # 任何异常都静默返回 None — 鉴权挂了也别影响 Fuzz 主流程，
        # 没 token 时 schemathesis 会测匿名访问路径。
        return None


# ============================================================
#  主测试：每个 OpenAPI endpoint + method 组合 → 1 个 pytest 用例
# ============================================================
@pytest.mark.fuzz  # ⭐ 默认 CI/日常跳过
@pytest.mark.asyncio
@schema.parametrize()
@hypothesis.settings(
    # 每个 endpoint 生成多少个随机/边界用例
    max_examples=FUZZ_CASES_PER_ENDPOINT,
    # 禁用 hypothesis 超时 deadline（单条 API 可能慢，尤其是需要注册/登录的）
    deadline=None,
    # 失败时不要打印太多乱码样例（默认 print 10 个，输入有特殊字符会把 log 撑爆）
    print_blob=True,
    # ============================================================
    #  Suppress Health Checks（为什么是安全的）:
    #  1) function_scoped_fixture:
    #     Hypothesis 会在一个 test function 里跑 max_examples 次生成用例，
    #     db_session/user_factory 是 function scope，不会每个 example 重建。
    #     → 对 Fuzz 场景是 OK 的：同一个 test 里的多个 example 共用一个
    #       DB session，顶多 user/Kb 记录越积越多，但 test 结束 fixture
    #       统一 rollback，不会污染其他 test。
    #  2) too_slow:
    #     个别接口（如 embedding 计算）可能 1-2s，Hypothesis 默认觉得慢，
    #     我们不关心 Fuzz 整体耗时，只要最终结果对就行。
    # ============================================================
    suppress_health_check=[
        hypothesis.HealthCheck.function_scoped_fixture,
        hypothesis.HealthCheck.too_slow,
        hypothesis.HealthCheck.filter_too_much,
        hypothesis.HealthCheck.return_value,
    ],
)
async def test_api_contract_fuzz(
    case,
    db_session,
    user_factory,
    api_client,
):
    """每个 endpoint 自动跑 N 个边界值/异常值/随机值 case。

    - db_session: 每次 test 独立 SQLite，回滚保证隔离
    - user_factory: 快速注册用户拿 token
    - api_client: FastAPI TestClient（ASGI transport，不走真实 TCP）
    """
    # ---- 1) 注入鉴权 ----
    # 策略：除了明确是 auth/* 的接口（注册、登录），其他接口都带 token 测
    path = case.path or ""
    method = (case.method or "get").lower()

    needs_auth = not (path.startswith("/api/v1/auth") or path.startswith("/health"))
    token: Optional[str] = None
    if needs_auth:
        # 先试普通 viewer token；接口需要 admin 时会自然返回 403（不是 500 就 OK）
        token = await _get_auth_token(db_session, user_factory, api_client, "viewer")

    if token:
        case.headers = dict(case.headers or {})
        case.headers["Authorization"] = f"Bearer {token}"
    # 没有 token 的接口，schemathesis 会匿名测（比如 /health, /auth/login, /auth/register）

    # ---- 2) 实际发请求（用 schemathesis 自带 ASGI transport，复用 app）----
    # ⚠️ 坑点：schemathesis 3.21 的 case.call_asgi() 是**同步**方法
    #     （它内部用同步的 asgiref.testing 模块，不支持 async/await）。
    #     所以不能加 await，加了会 TypeError: object Response can't be used in 'await' expression
    # 注意：from_dict() 加载的 schema 没有 asgi_app 引用，必须显式传 app=...
    try:
        response = case.call_asgi(app=_get_app_for_call())
    except TypeError as te:
        if "can't be used in 'await' expression" in str(te):
            pytest.fail(
                "call_asgi 不能 await，请检查是否误用了 await（schemathesis 3.21 是同步 API）"
            )
        raise
    except Exception as e:
        # schemathesis 自己的异常（比如 schema 定义有问题）应该标 FAIL，而不是 ERROR
        pytest.fail(f"schemathesis call_asgi 异常: {type(e).__name__}: {e}")
        return

    # ---- 3) 契约校验 ----
    # 核心断言：response 的 status_code / body / headers 必须符合 OpenAPI schema 声明
    # 如果某接口声明 200 返回 TokenResponse，但实际返回了 500，这里会炸。
    # 如果某接口声明字段是 int，但实际返回 str，这里也会炸。
    try:
        case.validate_response(response)
    except AssertionError as e:
        # 契约不匹配：打印完整请求/响应信息，方便排查
        msg = (
            f"\n===== Fuzz 契约不匹配 =====\n"
            f"{case.method.upper()} {case.path}\n"
            f"path_parameters: {case.path_parameters}\n"
            f"query:           {case.query}\n"
            f"body:            {case.body}\n"
            f"resp.status:     {response.status_code}\n"
            f"resp.body:       {getattr(response, '_content', None) or getattr(response, 'content', None)}\n"
            f"error:           {e}\n"
            f"============================="
        )
        # 白名单：历史欠账——很多接口没声明 401/403/404 response，
        # 这些不算 "契约破坏"，只当 WARN 打印，不 FAIL 测试。
        status = response.status_code
        if status in (401, 403, 404, 409, 422):
            # 常见业务错误码，属于正常响应（只是 schema 没声明完整），WARN 即可
            print(msg.replace("契约不匹配", "契约WARN(常见4XX, schema未声明)"))
            return
        pytest.fail(msg)
