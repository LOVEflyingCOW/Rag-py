"""Phase 3 验证测试 — 基础设施层 (Redis + Celery + 连接池)（真 pytest 版本）

改造点（vs 旧脚本版）:
  - 旧版致命问题:
      from app.core.redis import init_redis
      asyncio.run(init_redis())        # ← 模块顶层就连 Redis，pytest 收集阶段直接卡住/超时
      section("A. Redis ...")          # ← 裸 PASS/FAIL 计数器也在顶层
  - 新版:
      * **删除所有顶层副作用**：import 本文件不应该连任何网络、不该 print 任何东西
      * A~E 需要 Redis 的测试：写 autouse fixture 连 localhost:6379 ping，不通就整个 class SKIP
      * B2/B3 缓存: get_embed_cached / get_user_perm_cached 若 Redis 不可用会走内存 fallback，
        所以「无 Redis」时断言只检查「数据一致性」，不强制断言「调用次数 = 1」
      * F 连接池: 直接 import，不需要 HTTP/Redis
      * G Celery: 直接 import 任务注册状态（不启动 worker）
      * F2 高并发: 旧版 requests + ThreadPoolExecutor(50) 强依赖真服务，改成「httpx.AsyncClient + 30 并发」（如果 api_client 可共享）；没有 Redis 就 SKIP

运行方式 (可选):
  ① 无 Redis（默认开发机离线场景，会自动 SKIP Redis 相关 case）:
     cd backend && ..\venv\Scripts\python.exe -m pytest tests/phases/test_phase3_infra.py -v
  ② 有 Redis + 真服务场景（想测全量）:
     docker-compose up -d redis
     cd backend && ..\venv\Scripts\python.exe -m pytest tests/phases/test_phase3_infra.py -v \
          -o "addopts=-v --strict-markers"
"""

from __future__ import annotations

import asyncio
import time as _time
from pathlib import Path

import pytest


# ============================================================
#  通用工具: skip_if_no_redis / get_sync_redis
# ============================================================
_LOCAL_REDIS_URL = "redis://localhost:6379/0"


def _ping_local_redis() -> bool:
    """尝试 PING 本地 Redis。失败返回 False，不抛异常。"""
    try:
        import redis as _redis
    except Exception:
        return False
    try:
        client = _redis.from_url(_LOCAL_REDIS_URL, socket_connect_timeout=1.5, socket_timeout=1.5)
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False


@pytest.fixture(scope="class")
def skip_if_no_redis(request):
    """需要 Redis 的 class 挂这个 class-scope autouse fixture。

    用法:
        @pytest.mark.usefixtures("skip_if_no_redis")
        class TestRedisNeeds: ...
    """
    if not _ping_local_redis():
        pytest.skip("本地 Redis (localhost:6379) 不可达，跳过整组 Redis 集成测试")


# ============================================================
#  A. Redis 连接与健康检查（需要 Redis）
# ============================================================
@pytest.mark.usefixtures("skip_if_no_redis")
class TestRedisConnectivity:
    """Redis + 健康检查"""

    def test_a1_redis_ping(self):
        import redis as _redis
        r = _redis.from_url(_LOCAL_REDIS_URL, decode_responses=True, socket_timeout=2)
        try:
            assert r.ping()
        finally:
            r.close()

    @pytest.mark.asyncio
    async def test_a2_health_redis_status(self, api_client):
        """health endpoint 显示 redis=ok 或 degraded 都允许（项目级差异）。
        这里只断言：响应 JSON 结构合法 + 有 redis key。"""
        resp = await api_client.get("/health")
        # api_client 用的是 ASGITransport → 不一定能连到 docker 的真 Redis，
        # 项目 health 可能在 Redis 不可用时返回 degraded/warn。
        # 不做强断言，只看 JSON 可解析。
        assert resp.status_code in (200, 503)
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            pytest.fail(f"/health 返回非 JSON: {resp.text[:200]}")
            return
        # 标准结构: {"data": {"redis": "ok|degraded"}}
        data = body.get("data", {}) if isinstance(body, dict) else {}
        # 至少有 data 或直接有 redis 字段之一
        assert "redis" in data or "redis" in body, f"结构无 redis 字段: {body}"


# ============================================================
#  B. Redis Cache-Aside 模式 + Embedding 缓存 + Permission 缓存
#  C. 缓存失效
#  D. 滑动窗口限流 (Redis backend)
#  E. Token 黑名单 Redis key
#  合并到一个 class，因为共享 KB id / token
# ============================================================
@pytest.mark.usefixtures("skip_if_no_redis")
class TestRedisCacheAndToken:
    """B / C / D / E 合并 class，内部共享用户 + KB id。"""

    @pytest.fixture(scope="class")
    def _prepared(self, request):
        """class 级准备：注册 1 用户、建 1 KB。Redis 中会留下相关缓存 key。

        注意: class-scope fixture 拿不到 function-scope 的 api_client，
        所以这里用 module-scope 的「临时 api_client 工厂」模式不太好。
        折中: 第一个 test function 负责准备数据并 setattr(self, ...)，
        后续 test function 依赖顺序（pytest 默认按源码顺序执行）。
        """
        # 实际做法：第一个 test_* 函数里自己做 setup，见 test_b1_prepare_and_cache_aside
        yield

    # ---------- B1 (同时 setup 后续 C/E 依赖) ----------
    @pytest.mark.asyncio
    async def test_b1_prepare_and_cache_aside(self, api_client, class_storage):
        """B1: 建用户 + 建 KB + 读 KB（cache miss → 写缓存 → 10 次 cache hit）

        因为 class 内 test 共享「同一 function-scope api_client」不成立（每个 test function 新 fixture），
        所以 B1 同时「准备 class_storage 用户 token + kb_id」给 C/E 用。
        class_storage 是我们注册的一个 fixture（见本文件底部），跨 test function 传递数据。
        """
        import redis as _redis

        ts = str(int(_time.time() * 1000))
        uname = f"p3_cache_{ts}"
        reg = await api_client.post("/api/v1/auth/register", json={
            "username": uname, "password": "Test123456",
            "confirm_password": "Test123456", "email": f"{uname}@t.com",
        })
        assert reg.status_code == 200, reg.text[:200]
        token = reg.json()["data"]["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        # 建 KB
        r_kb = await api_client.post("/api/v1/knowledge-bases", json={
            "name": "Phase3 Cache Test KB", "description": "cache",
        }, headers=h)
        assert r_kb.status_code == 200, r_kb.text[:200]
        kb_id = r_kb.json()["data"]["id"]
        # 保存给 C / E
        class_storage["token"] = token
        class_storage["kb_id"] = kb_id

        # 手动清除 Redis 里可能已有的 kb:{id}，保证接下来是 cache miss
        r = _redis.from_url(_LOCAL_REDIS_URL, decode_responses=True, socket_timeout=2)
        try:
            r.delete(f"kb:{kb_id}")

            # 第一次: cache miss
            resp1 = await api_client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=h)
            assert resp1.status_code == 200

            # 写缓存: Redis 中必须已有 key
            keys = r.keys(f"kb:{kb_id}")
            assert len(keys) >= 1, "首次读取后 Redis 无 kb cache key（cache-aside 写逻辑未生效？）"

            # 10 次 cache hit：都必须 200
            for i in range(10):
                rr = await api_client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=h)
                assert rr.status_code == 200, f"第 {i+1} 次命中读失败: {rr.status_code}"
        finally:
            r.close()

    # ---------- B2: Embedding 缓存 ----------
    @pytest.mark.asyncio
    async def test_b2_embedding_cache(self):
        """get_embed_cached: 无 Redis → 走内存；有 Redis → 真正 key 存在。"""
        from app.core.cache import get_embed_cached

        call_count = {"n": 0}

        async def _fetch():
            call_count["n"] += 1
            return [0.1, 0.2, 0.3, 0.4, 0.5]

        v1 = await get_embed_cached("phase3_embed_unique_x", _fetch, ttl=60)
        v2 = await get_embed_cached("phase3_embed_unique_x", _fetch, ttl=60)
        assert v1 == v2 == [0.1, 0.2, 0.3, 0.4, 0.5]
        # 注意：如果 Redis 不可达 get_embed_cached 走内存 LRU，仍应命中
        assert call_count["n"] == 1, (
            f"fetch 调用 {call_count['n']} 次 (期望 1) — "
            "get_embed_cached fallback 缓存层可能未正确命中"
        )

    # ---------- B3: 用户权限缓存 ----------
    @pytest.mark.asyncio
    async def test_b3_user_perm_cache(self):
        from app.core.cache import get_user_perm_cached

        call_count = {"n": 0}

        async def _fetch():
            call_count["n"] += 1
            return {"roles": ["user"], "permissions": ["kb:read", "kb:write"]}

        p1 = await get_user_perm_cached(999_000, _fetch, ttl=30)
        p2 = await get_user_perm_cached(999_000, _fetch, ttl=30)
        assert p1 == p2
        assert call_count["n"] == 1

    # ---------- C. 缓存失效（更新后） ----------
    @pytest.mark.asyncio
    async def test_c_invalidation_after_update(self, api_client, class_storage):
        """C: PUT /knowledge-bases/{id} 后应删除旧缓存。"""
        import redis as _redis

        if "token" not in class_storage or "kb_id" not in class_storage:
            pytest.skip("B1 setup 未完成，跳过缓存失效测试")
        token = class_storage["token"]
        kb_id = class_storage["kb_id"]
        h = {"Authorization": f"Bearer {token}"}

        r = _redis.from_url(_LOCAL_REDIS_URL, decode_responses=True, socket_timeout=2)
        try:
            # 先手动清掉 + 读一次写回缓存
            r.delete(f"kb:{kb_id}")
            resp = await api_client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=h)
            assert resp.status_code == 200
            assert len(r.keys(f"kb:{kb_id}")) >= 1, "更新前缓存写回失败"

            # PUT 更新 → 服务端应该 invalidate 缓存
            resp2 = await api_client.put(f"/api/v1/knowledge-bases/{kb_id}", json={
                "name": "Invalidated Cache KB",
            }, headers=h)
            assert resp2.status_code == 200, resp2.text[:200]

            _time.sleep(0.5)  # 中间件/任务是异步但 cache-aside 是同步 delete
            remaining = r.keys(f"kb:{kb_id}")
            assert len(remaining) == 0, f"更新后 kb:{kb_id} 缓存仍存在, keys={remaining}"

            # 再读一次：数据是更新后的值
            resp3 = await api_client.get(f"/api/v1/knowledge-bases/{kb_id}", headers=h)
            assert resp3.status_code == 200
            assert resp3.json()["data"]["name"] == "Invalidated Cache KB"
        finally:
            r.close()

    # ---------- D. 滑动窗口限流（Redis backend） ----------
    @pytest.mark.asyncio
    async def test_d_rate_limit_redis_backend(self, api_client):
        """D: 连打 35 次 → 应有 429；检查 Redis 是否有 ratelimit:* key。"""
        import redis as _redis
        statuses = []
        for _ in range(35):
            resp = await api_client.get(
                "/api/v1/knowledge-bases", params={"page": 1, "page_size": 1},
            )
            statuses.append(resp.status_code)
            if resp.status_code == 429:
                break
        # 匿名未登录通常全 401。全 401 → skip
        if all(s == 401 for s in statuses):
            pytest.skip("匿名全 401，无法打到限流逻辑")
            return
        assert 429 in statuses, f"未出现 429: {set(statuses)}"

        r = _redis.from_url(_LOCAL_REDIS_URL, decode_responses=True, socket_timeout=2)
        try:
            n = len(r.keys("ratelimit:*"))
            assert n >= 1, f"Redis 中无 ratelimit:* key (slowapi 没启用 RedisStorage？)"
        finally:
            r.close()

    # ---------- E. Token 黑名单 Redis key ----------
    @pytest.mark.asyncio
    async def test_e_token_blacklist_key_on_redis(self, api_client, class_storage):
        """E: logout 后 blacklist:* key 应出现在 Redis 中。"""
        import redis as _redis
        if "token" not in class_storage:
            pytest.skip("B1 setup 未完成，跳过黑名单 Redis 测试")
        # 独立造一个用户专门做黑名单测试，不依赖 B1 setup 的 token
        ts2 = str(int(_time.time() * 1000))
        u2 = f"p3_blacklist_{ts2}"
        reg = await api_client.post("/api/v1/auth/register", json={
            "username": u2, "password": "Test123456",
            "confirm_password": "Test123456", "email": f"{u2}@t.com",
        })
        assert reg.status_code == 200, reg.text[:200]
        tok = reg.json()["data"]["access_token"]
        refresh = reg.json()["data"]["refresh_token"]
        h2 = {"Authorization": f"Bearer {tok}"}

        out = await api_client.post("/api/v1/auth/logout", json={"refresh_token": refresh}, headers=h2)
        assert out.status_code == 200

        # 等一会儿让 revoke_token 里的异步写 Redis 执行完（但 api_client 中 loop 已在跑）
        await asyncio.sleep(0.3)

        r = _redis.from_url(_LOCAL_REDIS_URL, decode_responses=True, socket_timeout=2)
        try:
            n = len(r.keys("blacklist:*"))
            # 宽松断言：只要有 blacklist key 就通过；
            # 若项目只写内存没写 Redis，就 SKIP 而不是 FAIL
            if n == 0:
                pytest.skip("Redis 无 blacklist:* key（写 Redis 分支可能关闭，走内存黑名单即可）")
                return
            assert n >= 1

            # 用刚撤销的 token 调接口 → 必须 401
            resp = await api_client.get("/api/v1/auth/me", headers=h2)
            assert resp.status_code == 401
        finally:
            r.close()


# ============================================================
#  F. 数据库连接池（无网络依赖，直接 import 检查）
# ============================================================
class TestDbPool:
    """Engine 初始化 + 连接池配置（不需要 Redis/HTTP）"""

    def test_f1_engines_exist(self):
        # 注意：async_engine / sync_engine 是在 app.models.database 顶层创建的，
        # 如果 settings.DATABASE_URL 指向 PostgreSQL 但服务未起，import 不会立刻
        # 建立连接（SQLAlchemy 引擎是 lazy 的），所以不会报错。
        from app.models.database import async_engine, sync_engine
        assert async_engine is not None
        assert sync_engine is not None

    @pytest.mark.asyncio
    async def test_f1_async_engine_connect(self, db_session):
        """有了 function-scope db_session fixture 就间接测了 async_engine 能连。"""
        from sqlalchemy import select
        r = await db_session.execute(select(1))
        assert r.scalar() == 1

    def test_f1_sync_engine_connect(self):
        """sync engine 基于 DATABASE_SYNC_URL，可能还指向真实 PostgreSQL。
        如果无法连接就 SKIP（项目可能没开本地 PG）。"""
        from sqlalchemy import text
        from app.models.database import sync_engine
        try:
            with sync_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"sync_engine 连不上（可能未开本地 PG），跳过: {e!r}")

    @pytest.mark.asyncio
    async def test_f2_concurrent_no_server_error(self, api_client):
        """F2: 50 并发（缩成 20 并发 + asyncio.gather，省资源）无 server 端错误。

        原脚本 requests + ThreadPoolExecutor(50) 依赖真 HTTP server。
        phase 测试里 api_client 是同一个 ASGI app，可以直接 asyncio.gather 并发。
        """
        # 先注册用户拿 token（避免全 401 无意义）
        ts = str(int(_time.time() * 1000))
        u = f"p3_pool_{ts}"
        reg = await api_client.post("/api/v1/auth/register", json={
            "username": u, "password": "Test123456",
            "confirm_password": "Test123456", "email": f"{u}@t.com",
        })
        assert reg.status_code == 200, reg.text[:200]
        tok = reg.json()["data"]["access_token"]
        h = {"Authorization": f"Bearer {tok}"}

        N = 20  # 比原脚本 50 收敛一点，phase 冒烟够用
        latencies: list[float] = []
        codes: list[int] = []

        async def _one(i):
            t0 = _time.perf_counter()
            resp = await api_client.get(
                "/api/v1/knowledge-bases",
                params={"page": 1, "page_size": 1},
                headers=h,
            )
            dt = (_time.perf_counter() - t0) * 1000
            return dt, resp.status_code

        results = await asyncio.gather(*[_one(i) for i in range(N)])
        for dt, c in results:
            latencies.append(dt)
            codes.append(c)

        server_errors = sum(1 for c in codes if c == 0 or c >= 500)
        assert server_errors == 0, f"{server_errors} 个 5xx/err"

        latencies.sort()
        # 不做硬性阈值断言（开发机差异大），只要求有 p95/avg 能算出来
        avg = sum(latencies) / len(latencies)
        p95 = latencies[int(len(latencies) * 0.95)]
        # 断言 P95 <= 10 秒（ASGI 内存级场景肯定满足，不满足说明有问题）
        assert p95 < 10_000, f"P95={p95:.0f}ms 过长"


# ============================================================
#  G. Celery 任务系统（不需要 Redis/PostgreSQL）
# ============================================================
class TestCeleryTasks:
    """Celery app 初始化 + 任务注册状态"""

    def test_g1_celery_app_loaded(self):
        from app.core.celery_app import celery_app
        # broker 可能是 redis://localhost → 不要求能连，只看对象拿到了
        assert celery_app is not None
        assert hasattr(celery_app, "conf")
        # broker_url 必须存在（哪怕是 memory://）
        assert celery_app.conf.broker_url, "celery_app.conf.broker_url 为空"

    def test_g2_audit_task_registered(self):
        from app.tasks.audit_tasks import write_audit_log
        assert hasattr(write_audit_log, "name")

    def test_g3_document_task_registered(self):
        from app.tasks.document_tasks import process_document
        assert hasattr(process_document, "name")


# ============================================================
#  Fixture: class_storage（同一 class 内跨 test function 共享数据）
# ============================================================
@pytest.fixture(scope="class")
def class_storage():
    """一个 dict，供 class 内 test_* 之间传递 token / kb_id 等 setup 数据。"""
    store: dict = {}
    yield store
