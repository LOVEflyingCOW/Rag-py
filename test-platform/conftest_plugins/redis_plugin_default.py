"""默认 Redis 插件：内存 FakeRedis，覆盖 sync + async API"""
from __future__ import annotations

import time
from typing import Any, Dict


class FakeRedis:
    """最小 Redis in-memory 假实现（set/get/delete/expire/exists + TTL + sync/async）"""

    def __init__(self) -> None:
        self._store: Dict[str, bytes] = {}
        self._ttl: Dict[str, float] = {}

    def _is_expired(self, key: str) -> bool:
        ttl = self._ttl.get(key)
        if ttl is None:
            return False
        if time.time() > ttl:
            self._store.pop(key, None)
            self._ttl.pop(key, None)
            return True
        return False

    # ---- sync ----
    def set(self, key: str, value, ex=None, px=None, nx=False, xx=False):  # type: ignore[override]
        if nx and key in self._store:
            return False
        if xx and key not in self._store:
            return False
        self._store[key] = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        ttl_s = ex or (px / 1000 if px else None)
        if ttl_s:
            self._ttl[key] = time.time() + ttl_s
        return True

    def get(self, key: str):
        if self._is_expired(key):
            return None
        return self._store.get(key)

    def delete(self, *keys: str):
        for k in keys:
            self._store.pop(k, None)
            self._ttl.pop(k, None)
        return True

    def expire(self, key: str, seconds: int):
        if key in self._store:
            self._ttl[key] = time.time() + seconds
        return True

    def exists(self, key: str):
        return 1 if self.get(key) is not None else 0

    # ---- async ----
    async def aget(self, key: str):
        return self.get(key)

    async def aset(self, key: str, value, ex=None, px=None, nx=False, xx=False):  # type: ignore[override]
        return self.set(key, value, ex=ex, px=px, nx=nx, xx=xx)

    async def adelete(self, *keys: str):
        return self.delete(*keys)

    async def aexists(self, key: str):
        return self.exists(key)


def register_fixtures(registry, cfg: dict) -> None:
    """fake_redis + mock_redis_module 两个 fixture"""
    import pytest

    # ---- fake_redis (per-test 独立实例) ----
    def fake_redis() -> FakeRedis:
        return FakeRedis()

    # ---- mock_redis_module (monkeypatch 到 app.core.redis / app.core.security) ----
    def mock_redis_module(fake_redis: FakeRedis, monkeypatch):
        class _Pool:
            def get_connection(self, *a, **kw):
                return fake_redis

            async def get(self, key):
                return await fake_redis.aget(key)

            async def set(self, key, value, ex=None, px=None, nx=False, xx=False):
                return fake_redis.set(key, value, ex=ex, px=px, nx=nx, xx=xx)

            async def delete(self, *keys):
                return fake_redis.delete(*keys)

            async def exists(self, key):
                return fake_redis.exists(key)

        pool = _Pool()
        # 尝试 patch app.core.redis，找不到就静默跳过（新项目可能叫别的模块）
        for mod_name in ("app.core.redis",):
            try:
                m = __import__(mod_name, fromlist=["*"])
                monkeypatch.setattr(m, "redis_pool", pool, raising=False)
                monkeypatch.setattr(m, "get_redis_pool", lambda *a, **kw: pool, raising=False)
            except Exception:
                pass
        # security 模块: revoke_sync/async 用到的单例
        for mod_name in ("app.core.security",):
            try:
                m = __import__(mod_name, fromlist=["*"])
                monkeypatch.setattr(m, "_redis_sync", fake_redis, raising=False)
                monkeypatch.setattr(m, "_redis_async_pool", pool, raising=False)
            except Exception:
                pass
        return pool

    registry.register("fake_redis", fake_redis, scope="function")
    registry.register("mock_redis_module", mock_redis_module, scope="function")

    # 把 FakeRedis 类也暴露出去，方便极个别测试直接 import
    registry._g["FakeRedis"] = FakeRedis
