"""默认 API Client 插件：httpx.AsyncClient + ASGITransport，真中间件假 TCP"""
from __future__ import annotations

from typing import AsyncGenerator

from conftest_plugins import import_attr


def _get_proj(cfg: dict, key: str, default):
    return (((cfg or {}).get("project") or {}).get(key)) or default


def register_fixtures(registry, cfg: dict) -> None:
    from httpx import AsyncClient, ASGITransport
    from sqlalchemy.ext.asyncio import AsyncSession

    settings_path = _get_proj(cfg, "settings_path", "app.core.config:settings")
    app_module_dotted = _get_proj(cfg, "app_module", "app") + ".main"
    main_app_attr = _get_proj(cfg, "main_app_attr", "app")
    audit_tasks_module = _get_proj(cfg, "audit_tasks_module", "app.tasks.audit_tasks")
    audit_task_name = _get_proj(cfg, "audit_task_name", "write_audit_log")

    async def api_client(
        db_session: AsyncSession,
        override_get_db,
        mock_redis_module,
        monkeypatch,
    ) -> AsyncGenerator[AsyncClient, None]:
        settings = import_attr(settings_path)

        # 1) 环境变量 / settings monkeypatch
        for k, v in {
            "APP_DEBUG": False,
            "REDIS_URL": "redis://fake:6379/0",
            "CELERY_BROKER_URL": "memory://",
            "CELERY_RESULT_BACKEND": "cache+memory://",
        }.items():
            if hasattr(settings, k):
                monkeypatch.setattr(settings, k, v)

        # 1b) Mock Celery 审计任务 no-op，避免 delay() 连真 Redis hang 1~2min
        try:
            at_mod = __import__(audit_tasks_module, fromlist=["*"])
            task_obj = getattr(at_mod, audit_task_name, None)
            if task_obj is not None:
                class _NoOp:
                    @staticmethod
                    def delay(*a, **kw):
                        return None
                    @staticmethod
                    def apply_async(*a, **kw):
                        return None
                monkeypatch.setattr(task_obj, "delay", _NoOp.delay, raising=False)
                monkeypatch.setattr(task_obj, "apply_async", _NoOp.apply_async, raising=False)
        except Exception:
            pass

        # 1c) 测试环境：拉高限流配额（MemoryStorage 跨用例共享，phase2 会吃满 30 次导致 429）
        #     通用化设计：尝试 import rate_limit 模块，失败则跳过（非 RAG 项目未必有）
        try:
            import importlib as _il
            rl_mod = _il.import_module("app.core.middleware.rate_limit")
            if hasattr(rl_mod, "RATE_LIMITS"):
                # 深拷贝一份再改，避免污染原模块
                new_limits = {k: dict(v) for k, v in rl_mod.RATE_LIMITS.items()}
                new_limits["anonymous"] = {"limit": 99999, "window": 60}
                new_limits["authenticated"] = {"limit": 99999, "window": 60}
                new_limits["admin"] = {"limit": 99999, "window": 60}
                monkeypatch.setattr(rl_mod, "RATE_LIMITS", new_limits)
        except Exception:
            pass

        # 2) 懒加载 FastAPI app（不能模块级 import，否则提前初始化真 DB/Redis）
        m = __import__(app_module_dotted, fromlist=["*"])
        app = getattr(m, main_app_attr)

        # 3) override DB 依赖
        get_db_dep, _gen = override_get_db
        app.dependency_overrides[get_db_dep] = _gen

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            yield c

        # 4) 清理 override
        app.dependency_overrides.pop(get_db_dep, None)

    registry.register("api_client", api_client, scope="function")
