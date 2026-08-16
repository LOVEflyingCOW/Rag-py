"""B2 · 集成测试 — Rate Limit 滑动窗口 / Token 撤销

主要测两部分：
  1) SlidingWindow 类的 check() 逻辑 (在 rate_limit 模块内)
  2) revoke_token 与 is_token_revoked 的联动 (基于 FakeRedis)
"""
from __future__ import annotations

import time
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


class TestSlidingWindowInProcess:
    """纯内存版: _MemoryRateLimiter.check() 不需要 Redis"""

    def test_below_limit_passes(self):
        from app.core.middleware.rate_limit import _MemoryRateLimiter
        sw = _MemoryRateLimiter()
        for _ in range(5):
            ok, remain, reset = sw.check("user-x", limit=10, window=60)
            assert ok is True

    def test_above_limit_blocked(self):
        from app.core.middleware.rate_limit import _MemoryRateLimiter
        sw = _MemoryRateLimiter()
        # 连续打 11 次, 第 11 次应该被限
        ok = True
        for i in range(11):
            ok, *_ = sw.check("user-y", limit=10, window=60)
        assert ok is False, "超过 limit 必须返回 False (限流生效)"

    def test_reset_is_positive(self):
        from app.core.middleware.rate_limit import _MemoryRateLimiter
        sw = _MemoryRateLimiter()
        ok, remain, reset = sw.check("user-z", limit=5, window=30)
        assert reset > 0  # reset 为秒数, 必须 > 0


class TestTokenRevokeIntegration:
    """revoke_token + is_token_revoked 组合 — 实际底层是内存 _blacklist"""

    def test_revoke_then_check_revoked(self):
        from app.core.security import revoke_token, is_token_revoked, create_access_token
        tok = create_access_token(user_id=7, username="rev")
        assert is_token_revoked(tok) is False
        revoke_token(tok, ttl=300)
        assert is_token_revoked(tok) is True

    async def test_revoke_async_then_check_async(self):
        from app.core.security import (
            revoke_token_async,
            is_token_revoked_async,
            create_access_token,
        )
        tok = create_access_token(user_id=8, username="async-rev")
        assert (await is_token_revoked_async(tok)) is False
        await revoke_token_async(tok, ttl=300)
        assert (await is_token_revoked_async(tok)) is True

    def test_fake_redis_ttl_expires_eventually(self):
        """只验证 FakeRedis 的过期逻辑 (不涉及 security)"""
        from tests.conftest import FakeRedis
        r = FakeRedis()
        r.set("short", "x", px=1)  # 1ms 过期
        time.sleep(0.05)  # 50ms >> 1ms
        assert r.get("short") is None, "FakeRedis px TTL 应该生效"
