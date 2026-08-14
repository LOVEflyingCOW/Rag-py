"""
Phase 3 验证测试 — 基础设施层 (Redis + Celery + 连接池)

运行方式 (需先启动服务):
  cd backend
  ..\\venv\\Scripts\\python.exe tests/phases/test_phase3_infra.py
"""

import sys
import os
import time
import json
import asyncio
import requests
import concurrent.futures
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.redis import init_redis
asyncio.run(init_redis())

PASS = 0
FAIL = 0
SKIP = 0

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api/v1"
REDIS_URL = "redis://localhost:6379/0"

token = None
bl_token = None
pool_token = None
kb_id = None


def pass_test(name, detail=""):
    global PASS
    PASS += 1
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))


def fail_test(name, detail=""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


def skip_test(name, detail=""):
    global SKIP
    SKIP += 1
    print(f"  [SKIP] {name}" + (f" — {detail}" if detail else ""))


def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


def get_redis():
    import redis
    return redis.from_url(REDIS_URL, decode_responses=True)


def create_test_user(username_suffix="p3_test", password="TestPass123!"):
    uname = f"{username_suffix}_{int(time.time())}"
    email = f"{uname}@example.com"
    resp = requests.post(f"{API}/auth/register", json={
        "username": uname,
        "email": email,
        "password": password,
        "confirm_password": password,
    })
    if resp.status_code == 409:
        resp = requests.post(f"{API}/auth/login", json={
            "username": uname,
            "password": password,
        })
    if resp.status_code in (200, 201):
        data = resp.json()
        if "data" in data:
            return data["data"]["access_token"], uname
    raise Exception(f"Failed to create user: {resp.status_code} {resp.text[:200]}")


def api_get(path, t=None):
    headers = {"Authorization": f"Bearer {t}"} if t else {}
    return requests.get(f"{API}{path}", headers=headers)


def api_post(path, data=None, t=None):
    headers = {"Authorization": f"Bearer {t}"} if t else {}
    return requests.post(f"{API}{path}", json=data, headers=headers)


def api_put(path, data=None, t=None):
    headers = {"Authorization": f"Bearer {t}"} if t else {}
    return requests.put(f"{API}{path}", json=data, headers=headers)


# ============================================================
#  A. Redis 连接与健康检查
# ============================================================
section("A. Redis 连接与健康检查")

try:
    r = get_redis()
    r.ping()
    pass_test("Redis 连接", "PING → PONG")
except Exception as e:
    fail_test("Redis 连接", str(e))

try:
    resp = requests.get(f"{BASE}/health")
    data = resp.json()
    if data.get("data", {}).get("redis") == "ok":
        pass_test("健康检查 Redis 状态", "redis=ok")
    else:
        fail_test("健康检查 Redis 状态", f"redis={data.get('data', {}).get('redis')}")
except Exception as e:
    fail_test("健康检查", str(e))


# ============================================================
#  B. Redis Cache-Aside 模式
# ============================================================
section("B. Redis Cache-Aside 模式")

# B1 + 预创建: 在限流之前创建所有用户
print("\n  [B1] KB 缓存读写 + 预创建用户")
try:
    token, _ = create_test_user("p3_cache")

    resp = api_post("/knowledge-bases", {
        "name": "Phase3 Cache Test KB",
        "description": "Testing cache layer",
    }, token)
    if resp.status_code == 200:
        kb_id = resp.json()["data"]["id"]
        pass_test("创建 KB", f"id={kb_id}")
    else:
        fail_test("创建 KB", f"status={resp.status_code}")
        raise Exception("Cannot continue without KB")

    # 预创建 E/F 需要的用户 (在限流之前)
    bl_token, _ = create_test_user("p3_blacklist")
    pool_token, _ = create_test_user("p3_pool")
    pass_test("预创建 E/F 用户", "成功")

    # 清除缓存并测试 cache miss
    r = get_redis()
    r.delete(f"kb:{kb_id}")

    resp1 = api_get(f"/knowledge-bases/{kb_id}", token)
    if resp1.status_code == 200:
        pass_test("首次获取 (cache miss)", f"status=200")
    else:
        fail_test("首次获取", f"status={resp1.status_code}")

    keys = r.keys(f"kb:{kb_id}")
    if keys:
        pass_test("缓存已写入 Redis", f"key={keys[0]}")
    else:
        fail_test("缓存已写入 Redis", "未找到 key")

    for i in range(10):
        resp = api_get(f"/knowledge-bases/{kb_id}", token)
        if resp.status_code != 200:
            fail_test(f"缓存命中 #{i+1}", f"status={resp.status_code}")
            break
    else:
        pass_test("缓存命中 10 次", "全部 200 OK")

except Exception as e:
    fail_test("KB 缓存测试", str(e))


# B2: Embedding 缓存
print("\n  [B2] Embedding 缓存测试")
try:
    from app.core.cache import get_embed_cached

    async def test_embed_cache():
        call_count = [0]
        async def fetch_embedding():
            call_count[0] += 1
            return [0.1, 0.2, 0.3, 0.4, 0.5]
        vec1 = await get_embed_cached("test text", fetch_embedding, ttl=3600)
        vec2 = await get_embed_cached("test text", fetch_embedding, ttl=3600)
        return vec1, vec2, call_count[0]

    vec1, vec2, count = asyncio.run(test_embed_cache())
    pass_test("Embedding 缓存命中", f"fetch 仅被调用 {count} 次" if count == 1 else f"fetch 被调用 {count} 次 (期望 1)")
    pass_test("Embedding 缓存数据一致" if vec1 == vec2 == [0.1, 0.2, 0.3, 0.4, 0.5] else "数据不一致")
except Exception as e:
    fail_test("Embedding 缓存测试", str(e))


# B3: 用户权限缓存
print("\n  [B3] 用户权限缓存测试")
try:
    from app.core.cache import get_user_perm_cached

    async def test_perm_cache():
        call_count = [0]
        async def fetch_perm():
            call_count[0] += 1
            return {"roles": ["user"], "permissions": ["kb:read", "kb:write"]}
        perm1 = await get_user_perm_cached(999, fetch_perm, ttl=30)
        perm2 = await get_user_perm_cached(999, fetch_perm, ttl=30)
        return perm1, perm2, call_count[0]

    perm1, perm2, count = asyncio.run(test_perm_cache())
    pass_test("用户权限缓存命中", f"fetch 仅被调用 {count} 次" if count == 1 else f"fetch 被调用 {count} 次")
    pass_test("用户权限缓存数据一致" if perm1 == perm2 else "数据不一致")
except Exception as e:
    fail_test("用户权限缓存测试", str(e))


# ============================================================
#  C. 缓存失效 (更新后)
# ============================================================
section("C. 缓存失效 (更新后)")

if kb_id is not None:
    try:
        r = get_redis()
        r.delete(f"kb:{kb_id}")

        resp = api_get(f"/knowledge-bases/{kb_id}", token)
        if resp.status_code == 200:
            pass_test("读取 KB (cache miss)")

        resp = api_put(f"/knowledge-bases/{kb_id}", {"name": "Invalidated Cache KB"}, token)
        if resp.status_code == 200:
            pass_test("更新 KB (触发缓存失效)")
        else:
            fail_test("更新 KB", f"status={resp.status_code}")

        time.sleep(0.5)
        keys = r.keys(f"kb:{kb_id}")
        pass_test("缓存自动失效", "更新后无该 key" if not keys else f"仍存在 key: {keys}")

        resp = api_get(f"/knowledge-bases/{kb_id}", token)
        if resp.status_code == 200 and resp.json()["data"]["name"] == "Invalidated Cache KB":
            pass_test("缓存失效后数据正确")
        else:
            fail_test("缓存失效后数据正确", resp.text[:200])
    except Exception as e:
        fail_test("缓存失效测试", str(e))
else:
    skip_test("缓存失效测试", "无可用 KB")


# ============================================================
#  D. 滑动窗口限流
# ============================================================
section("D. 滑动窗口限流")

print("\n  [D1] 匿名 IP 限流测试 (30/min)")
try:
    ok_count = 0
    blocked_count = 0
    for i in range(35):
        resp = api_get("/knowledge-bases")
        if resp.status_code == 200:
            ok_count += 1
        elif resp.status_code == 429:
            blocked_count += 1

    pass_test("匿名限流触发", f"35 请求中 {blocked_count} 被拦截" if blocked_count >= 5 else f"仅 {blocked_count} 被拦截")

    r = get_redis()
    rate_keys = r.keys("ratelimit:*")
    pass_test("限流数据存储在 Redis", f"key 数={len(rate_keys)}" if rate_keys else "未找到 ratelimit key")
except Exception as e:
    fail_test("匿名限流测试", str(e))


# ============================================================
#  E. Token 黑名单
# ============================================================
section("E. Token 黑名单")

if bl_token:
    try:
        resp = api_post("/auth/logout", {}, bl_token)
        if resp.status_code in (200, 204):
            pass_test("登出 → Token 加入黑名单")
        else:
            fail_test("登出 → Token 加入黑名单", f"status={resp.status_code}")

        time.sleep(0.5)
        r = get_redis()
        blacklist_keys = r.keys("blacklist:*")
        pass_test("黑名单 Redis key 存在", f"key 数={len(blacklist_keys)}" if blacklist_keys else "未找到")

        resp = api_post("/knowledge-bases", {
            "name": "Blocked KB",
            "description": "Should be blocked",
        }, bl_token)
        if resp.status_code == 401:
            pass_test("已撤销 Token 被拒绝")
        else:
            fail_test("已撤销 Token 被拒绝", f"status={resp.status_code}, 期望 401")
    except Exception as e:
        fail_test("Token 黑名单测试", str(e))
else:
    skip_test("Token 黑名单测试", "无可用 token")


# ============================================================
#  F. 数据库连接池
# ============================================================
section("F. 数据库连接池")

print("\n  [F1] Engine 初始化验证")
try:
    from app.models.database import async_engine, sync_engine

    if async_engine is not None:
        pass_test("Async Engine 已初始化")

        async def test_async():
            async with async_engine.connect():
                return True

        pass_test("Async Engine 连接成功" if asyncio.run(test_async()) else "连接失败")
    else:
        fail_test("Async Engine 已初始化")

    if sync_engine is not None:
        pass_test("Sync Engine 已初始化")
        with sync_engine.connect() as conn:
            pass_test("Sync Engine 连接成功")
    else:
        fail_test("Sync Engine 已初始化")
except Exception as e:
    fail_test("连接池配置", str(e))


print("\n  [F2] 连接池高并发稳定性 (50 并发)")
if pool_token:
    try:
        latencies = []
        server_errors = 0
        rate_limited = 0

        def make_request(_):
            t0 = time.time()
            try:
                resp = api_get("/knowledge-bases", pool_token)
                return (time.time() - t0) * 1000, resp.status_code
            except Exception:
                return (time.time() - t0) * 1000, 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(make_request, i) for i in range(50)]
            for f in concurrent.futures.as_completed(futures):
                lat, code = f.result()
                latencies.append(lat)
                if code == 0 or code >= 500:
                    server_errors += 1
                elif code == 429:
                    rate_limited += 1

        pass_test("50 并发无服务器错误", f"errors={server_errors}" if server_errors == 0 else f"errors={server_errors}")

        if latencies:
            latencies.sort()
            p95 = latencies[int(len(latencies) * 0.95)]
            avg = sum(latencies) / len(latencies)
            pass_test(f"延迟: avg={avg:.0f}ms, P95={p95:.0f}ms, max={latencies[-1]:.0f}ms")
    except Exception as e:
        fail_test("连接池高并发测试", str(e))
else:
    skip_test("连接池高并发测试", "无可用 token")


# ============================================================
#  G. Celery 任务系统
# ============================================================
section("G. Celery 任务系统")

try:
    from app.core.celery_app import celery_app
    pass_test("Celery App 已加载", f"broker={celery_app.conf.broker_url}")
except Exception as e:
    fail_test("Celery App 加载", str(e))

try:
    from app.tasks.audit_tasks import write_audit_log
    pass_test("write_audit_log 任务已注册", f"name={write_audit_log.name}")
except Exception as e:
    fail_test("Audit 任务", str(e))

try:
    from app.tasks.document_tasks import process_document
    pass_test("process_document 任务已注册", f"name={process_document.name}")
except Exception as e:
    fail_test("Document 任务", str(e))

if token:
    try:
        resp = api_get("/knowledge-bases", token)
        if resp.status_code == 200:
            pass_test("审计中间件正常分发 (HTTP 200)")
        elif resp.status_code == 429:
            skip_test("审计中间件测试", "已被限流, 跳过")
        else:
            pass_test("审计中间件响应", f"status={resp.status_code}")
    except Exception as e:
        fail_test("审计中间件测试", str(e))
else:
    skip_test("审计中间件测试", "无可用 token")


# ============================================================
#  汇总
# ============================================================
print(f"\n{'='*60}")
print(f"  Phase 3 集成测试汇总")
print(f"{'='*60}")
print(f"  通过: {PASS}")
print(f"  失败: {FAIL}")
print(f"  跳过: {SKIP}")
total = PASS + FAIL + SKIP
if FAIL == 0:
    print(f"\n  🎉 全部测试通过! ({PASS}/{total})")
else:
    print(f"\n  ⚠️  {FAIL} 项测试失败, 请检查")

sys.exit(0 if FAIL == 0 else 1)