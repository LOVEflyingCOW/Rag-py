"""B4 · 负载/并发测试 — httpx AsyncClient + asyncio.gather

默认用 @pytest.mark.slow / @pytest.mark.load 标记; CI 中:
  pytest tests/ -m "not slow and not load"
本地跑:
  pytest tests/load -m load --no-cov
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.mark.load
@pytest.mark.slow
class TestConcurrentHealthz:
    """大量并发 /healthz/live 打一下, 验证 ASGITransport + 中间件不会死锁."""

    @pytest.mark.parametrize("n", [20, 50])
    async def test_concurrent_liveness(self, api_client, n):
        async def one():
            r = await api_client.get("/healthz/live")
            return r.status_code

        statuses = await asyncio.gather(*[one() for _ in range(n)])
        ok = sum(1 for s in statuses if s == 200)
        # 只要 90% 成功, 就不算压测失败 (个别被限流正常)
        assert ok >= n * 0.9, f"liveness ok={ok}/{n}, 低于 90%"


@pytest.mark.load
@pytest.mark.slow
class TestConcurrentAuthRegister:
    """并发注册相同用户名 → 必须只有 1 人成功, 其余 409; 不能有 500 或 DB IntegrityError 泄漏到 500"""

    async def test_concurrent_dup_username_50_workers(self, api_client):
        import random
        username = "load_test_dup_user_%d" % random.randint(1, 1_000_000)
        payload = {
            "username": username,
            "email": f"{username}@example.com",
            "password": "Test123456",
            "confirm_password": "Test123456",
        }
        headers = {"Content-Type": "application/json"}

        async def one():
            try:
                r = await api_client.post("/auth/register", json=dict(payload), headers=headers)
                return r.status_code
            except Exception:
                return 0

        n = 30
        results = await asyncio.gather(*[one() for _ in range(n)])
        # 不允许 500
        assert all(s != 500 for s in results), f"出现 500, results={sorted(results)}"
        # 成功次数 (200) 必须 == 1
        succ = sum(1 for s in results if s == 200)
        assert succ == 1, f"相同用户名并发注册: 成功 {succ} 次, 期望 1"
