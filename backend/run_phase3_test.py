"""Phase 3 压力测试 — Redis 缓存 + 连接池 + 限流

测试项:
  T1: 并发注册/登录 (30 用户)
  T2: KB 缓存命中测试 (连续 50 次同 KB 请求, 验证 Redis 缓存)
  T3: 高并发 JWT-only 请求 (200 请求, 验证连接池 + 缓存)
  T4: 限流验证 (匿名 35 次, 验证 30/min 限制)
  T5: 缓存失效验证 (更新 KB 后, 缓存自动失效)
"""
import time
import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = "http://127.0.0.1:8000"
redis_client = None

def get_redis():
    global redis_client
    if redis_client is None:
        import redis
        redis_client = redis.from_url("redis://localhost:6379/0", decode_responses=True)
    return redis_client

def check_redis_key(pattern):
    r = get_redis()
    keys = r.keys(pattern)
    return keys

# ============ T1: 并发注册 ============
print("=" * 60)
print("  T1: 并发注册 30 用户")
print("=" * 60)

def register_one(i):
    username = f"p3test_{i}_{int(time.time())}"
    email = f"p3test_{i}_{int(time.time())}@test.com"
    r = requests.post(f"{BASE}/api/v1/auth/register", json={
        "username": username, "password": "TestPass123",
        "confirm_password": "TestPass123", "email": email,
    }, timeout=30)
    return r.status_code, r.json()

t0 = time.time()
tokens = []
with ThreadPoolExecutor(max_workers=15) as ex:
    futs = [ex.submit(register_one, i) for i in range(30)]
    for f in as_completed(futs):
        code, data = f.result()
        if code == 200:
            tokens.append(data["data"]["access_token"])

elapsed = time.time() - t0
print(f"  成功: {len(tokens)}/30, 耗时: {elapsed:.1f}s, 平均: {elapsed/30*1000:.0f}ms")
print(f"  平均 QPS: {30/elapsed:.1f}")

if not tokens:
    print("  [ERROR] 无 token, 终止测试")
    exit(1)

token = tokens[0]

# ============ T2: KB 缓存命中 ============
print("\n" + "=" * 60)
print("  T2: KB 缓存命中测试 (50 次连续同 KB 请求)")
print("=" * 60)

# 先创建一个 KB
kb_resp = requests.post(f"{BASE}/api/v1/knowledge-bases", json={
    "name": "Cache Test KB", "description": "test"
}, headers={"Authorization": f"Bearer {token}"})
kb_id = kb_resp.json()["data"]["id"]
print(f"  创建 KB: id={kb_id}")

# 清空可能存在的旧缓存
r = get_redis()
r.delete(f"kb:{kb_id}")

# 第一次请求 (cache miss → DB)
t_first = time.time()
resp = requests.get(f"{BASE}/api/v1/knowledge-bases/{kb_id}",
                     headers={"Authorization": f"Bearer {token}"})
first_time = (time.time() - t_first) * 1000
print(f"  第 1 次 (cache miss): {first_time:.0f}ms → status={resp.status_code}")

# 验证缓存已写入
keys = r.keys(f"kb:{kb_id}")
print(f"  Redis 缓存已写入: {'是' if keys else '否'} (key={keys[:1] if keys else 'N/A'})")

# 连续 49 次请求 (cache hit)
hit_times = []
for _ in range(49):
    t = time.time()
    resp = requests.get(f"{BASE}/api/v1/knowledge-bases/{kb_id}",
                         headers={"Authorization": f"Bearer {token}"})
    hit_times.append((time.time() - t) * 1000)

avg_hit = sum(hit_times) / len(hit_times)
print(f"  后 49 次 (cache hit): 平均 {avg_hit:.0f}ms, P50={sorted(hit_times)[24]:.0f}ms")
print(f"  缓存加速比: {first_time/avg_hit:.1f}x" if avg_hit > 0 else "  缓存加速比: N/A")

# ============ T3: 高并发 JWT-only ============
print("\n" + "=" * 60)
print("  T3: 高并发 JWT-only (200 请求, 验证连接池)")
print("=" * 60)

latencies = []
status_counts = {}

def hit_kb(_):
    t = time.time()
    try:
        resp = requests.get(f"{BASE}/api/v1/knowledge-bases",
                             headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return (time.time() - t) * 1000, resp.status_code
    except Exception:
        return (time.time() - t) * 1000, 0

with ThreadPoolExecutor(max_workers=30) as ex:
    futs = [ex.submit(hit_kb, i) for i in range(200)]
    for f in as_completed(futs):
        lat, code = f.result()
        latencies.append(lat)
        status_counts[code] = status_counts.get(code, 0) + 1

latencies.sort()
print(f"  状态码: {status_counts}")
ok_count = status_counts.get(200, 0)
limited_count = status_counts.get(429, 0)
print(f"  200 OK: {ok_count}, 429 限流: {limited_count}")
if latencies:
    ok_lats = [l for l in latencies if l < 5000]  # 排除超时
    if ok_lats:
        print(f"  延迟 (ms, 仅成功请求): min={ok_lats[0]:.0f} P50={sorted(ok_lats)[len(ok_lats)//2]:.0f} "
              f"P95={sorted(ok_lats)[int(len(ok_lats)*0.95)]:.0f} max={ok_lats[-1]:.0f}")
    # 关键指标: 连接池是否耗尽
    print(f"  连接池分析: 200 占比 {ok_count/2:.0f}% — {'正常' if ok_count > 50 else '限流生效, 需扩容或提高限流阈值'}")

# ============ T4: 限流验证 ============
print("\n" + "=" * 60)
print("  T4: 限流验证 (匿名 IP, 35 次)")
print("=" * 60)

blocked = 0
ok = 0
for i in range(35):
    resp = requests.get(f"{BASE}/api/v1/knowledge-bases", timeout=5)
    if resp.status_code == 429:
        blocked += 1
    elif resp.status_code == 200:
        ok += 1

print(f"  200 OK: {ok}, 429 限流: {blocked}")
print(f"  Redis 限流 key 数: {len(check_redis_key('ratelimit:*'))}")

# ============ T5: 缓存失效 ============
print("\n" + "=" * 60)
print("  T5: 缓存失效验证 (更新 KB 后)")
print("=" * 60)

# 等待限流窗口过期 (60s 滑动窗口)
print("  等待限流窗口过期 (65s)...")
time.sleep(65)

# 等待 TTL 过期或手动删除
r.delete(f"kb:{kb_id}")

# 读取 → 写 → 读
resp1 = requests.get(f"{BASE}/api/v1/knowledge-bases/{kb_id}",
                      headers={"Authorization": f"Bearer {token}"})
if resp1.status_code == 200:
    name_before = resp1.json()["data"]["name"]
    print(f"  更新前名称: {name_before}")
else:
    print(f"  读取 KB 失败: {resp1.status_code}")
    name_before = "unknown"

# 更新 KB
resp2 = requests.put(f"{BASE}/api/v1/knowledge-bases/{kb_id}", json={
    "name": "Updated Cache Test KB"
}, headers={"Authorization": f"Bearer {token}"})
print(f"  更新状态: {resp2.status_code}")

# 缓存应已失效
keys_after = r.keys(f"kb:{kb_id}")
print(f"  缓存是否已失效: {'是' if not keys_after else '否 (仍有 key)'}")

# 重新读取 → 应获取新名称
resp3 = requests.get(f"{BASE}/api/v1/knowledge-bases/{kb_id}",
                      headers={"Authorization": f"Bearer {token}"})
if resp3.status_code == 200:
    name_after = resp3.json()["data"]["name"]
    print(f"  更新后名称: {name_after}")
    print(f"  数据一致性: {'通过' if name_after == 'Updated Cache Test KB' else '失败!'}")
else:
    print(f"  重新读取失败: {resp3.status_code}")
    name_after = "error"

# ============ 总结 ============
print("\n" + "=" * 60)
print("  Phase 3 压力测试总结")
print("=" * 60)
t1_pass = len(tokens) >= 28
t2_pass = len(keys) > 0 and avg_hit <= first_time  # 缓存已写入且命中不比首次慢
t3_pass = ok_count > 50
t4_pass = blocked >= 5
t5_pass = name_after == 'Updated Cache Test KB'
print(f"  T1 并发注册: {'通过' if t1_pass else '异常'}")
print(f"  T2 缓存命中: {'通过' if t2_pass else '需优化'}")
print(f"  T3 高并发:   {'通过' if t3_pass else '异常'}")
print(f"  T4 限流:     {'通过' if t4_pass else '未触发'}")
print(f"  T5 缓存失效: {'通过' if t5_pass else '异常'}")
print(f"  Redis keys:  限流={len(check_redis_key('ratelimit:*'))}, 黑名单={len(check_redis_key('blacklist:*'))}, 缓存={len(check_redis_key('kb:*'))}")
all_pass = all([t1_pass, t2_pass, t3_pass, t4_pass, t5_pass])
print(f"\n  总体结果: {'🎉 全部通过!' if all_pass else '⚠️ 部分未通过, 请检查'}")
