"""conftest_plugins 包 — 测试平台通用插件系统

设计:
    - registry = FixtureRegistry(globals_of_conftest)
    - plugin.register_fixtures(registry)
    - registry.register("fixture_name", factory_fn, scope="function")
    -> 动态把 factory_fn 以 pytest.fixture 的形式注入到 conftest 的 globals，
       后续 test_*.py 里直接写 `async def test_xxx(fixture_name): ...` 即可

为什么不直接用 pytest_plugins 列表?
    pytest_plugins 是静态 import，重写一个 fixture 会报 "multiple fixtures named xx"
    冲突；动态注册允许 "新 plugin 覆盖同名 fixture"，这对项目适配层很重要。
"""
from __future__ import annotations

import sys
import importlib
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest


# 项目配置 yaml 名（放在仓库根/test-platform/下，或仓库根下）
CONFIG_CANDIDATE_NAMES = [
    "project_config.yaml",
    "test-platform/project_config.yaml",
]


class FixtureRegistry:
    """把 plugin 提供的工厂函数转成 pytest.fixture 注入 conftest globals"""

    def __init__(self, conftest_globals: Dict[str, Any]) -> None:
        self._g = conftest_globals

    def register(self, name: str, factory: Callable[..., Any], scope: str = "function") -> None:
        # 允许后注册的覆盖先注册的（项目适配层优先级 > 默认实现）
        self._g[name] = pytest.fixture(scope=scope)(factory)

    def register_many(self, mapping: Dict[str, Callable[..., Any]], scope: str = "function") -> None:
        for n, fn in mapping.items():
            self.register(n, fn, scope)


def _find_repo_root(start: Path) -> Path:
    """从 conftest 路径向上找 ".git" 或 "pyproject.toml"，判定仓库根"""
    p = start.resolve()
    for _ in range(10):
        if (p / ".git").exists() or (p / "pyproject.toml").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return start.resolve().parent.parent  # fallback: 上两层（backend/tests/conftest → repo_root）


def _load_yaml(path: Path) -> dict:
    """PyYAML 没装就降级：读最小实现（项目配置只用到 string/list/dict，够了）"""
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        # 极简易 yaml 解析：仅支持 "key: value"、"- item"、列表嵌套(2空格缩进)；
        # 够解析我们的 project_config.yaml.example 格式；复杂格式请装 PyYAML
        return _mini_parse_yaml(path)


def _mini_parse_yaml(path: Path) -> dict:
    """极简 YAML，仅解析本仓库 project_config 的形态"""
    from collections import defaultdict
    root: Dict[str, Any] = {}
    stack: list = [(0, root)]  # (indent, container)

    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if not line.strip() or line.strip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            content = line.strip()

            # pop stack to correct parent
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if not stack:
                stack.append((0, root))
            _, parent = stack[-1]

            if content.startswith("- "):
                item = content[2:].strip().strip('"').strip("'")
                if not isinstance(parent, list):
                    # 异常：列表项的父不是 list → 忽略
                    continue
                parent.append(item)
                continue

            if ":" in content:
                k, v = content.split(":", 1)
                k = k.strip()
                v = v.strip()
                # 判断下一行是不是更深的 child（简化：只看 v 是否为空）
                if v == "":
                    # 是 dict 还是 list？看首行 child: "- "开头就是list
                    new_container: Any = {}
                    parent[k] = new_container
                    stack.append((indent + 1, new_container))
                else:
                    # 有值，基础类型 / list inline
                    v = v.strip('"').strip("'")
                    # inline list: "[a, b, c]"
                    if v.startswith("[") and v.endswith("]"):
                        items = [x.strip().strip('"').strip("'") for x in v[1:-1].split(",") if x.strip()]
                        parent[k] = items
                    elif v.lower() in ("true", "false"):
                        parent[k] = (v.lower() == "true")
                    else:
                        parent[k] = v
    return root


def load_project_config(conftest_file: Path) -> dict:
    """按 CONFIG_CANDIDATE_NAMES 顺序找 yaml，找不到返回空 dict（走 RAG-PY 默认）"""
    repo_root = _find_repo_root(conftest_file)
    for name in CONFIG_CANDIDATE_NAMES:
        p = repo_root / name
        if p.exists():
            cfg = _load_yaml(p)
            cfg["_repo_root"] = repo_root
            cfg["_config_path"] = p
            return cfg
    # fallback: 空配置，插件会再判断要不要用内置默认（RAG-PY 路径）
    return {"_repo_root": repo_root, "_config_path": None}


def _add_testplatform_to_syspath(repo_root: Path) -> None:
    tp = str(repo_root / "test-platform")
    if tp not in sys.path:
        sys.path.insert(0, tp)


def import_attr(dotted: str):
    """'app.api.dependencies:get_db_dep' / 'app.api.dependencies.get_db_dep' -> obj"""
    if ":" in dotted:
        mod_path, attr = dotted.split(":", 1)
    else:
        mod_path, _, attr = dotted.rpartition(".")
    mod = importlib.import_module(mod_path)
    obj = getattr(mod, attr)
    return obj


def bootstrap_plugins(conftest_file: str | Path, conftest_globals: Dict[str, Any]) -> dict:
    """conftest.py 启动入口：读 yaml → 加 sys.path → import plugin → 注册 fixture

    Args:
        conftest_file: __file__ of conftest.py
        conftest_globals: globals() of conftest.py

    Returns:
        解析后的 project_config dict（供 conftest 内额外逻辑用）
    """
    conftest_path = Path(conftest_file).resolve()
    cfg = load_project_config(conftest_path)
    repo_root = cfg["_repo_root"]

    # 1. 加 backend_root（让 import app.* 能成功）
    project_section = cfg.get("project", {})
    backend_rel = project_section.get("backend_root", "backend")
    backend_root = (repo_root / backend_rel).resolve()
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    # 2. 加 test-platform 到 sys.path（让 import conftest_plugins 能成功）
    _add_testplatform_to_syspath(repo_root)

    # 3. 逐个 plugin import + register
    registry = FixtureRegistry(conftest_globals)
    plugins_section = cfg.get("plugins", {}) or {}
    if not plugins_section:
        # 没配置时 fallback 到 6 个默认插件（保证当前 RAG-PY 零配置继续跑）
        # 顺序重要：db_plugin → factory_plugin（factory 依赖 db_session fixture）
        plugins_section = {
            "db_plugin":         "conftest_plugins.db_plugin_default",
            "redis_plugin":      "conftest_plugins.redis_plugin_default",
            "auth_plugin":       "conftest_plugins.auth_plugin_default",
            "data_plugin":       "conftest_plugins.data_plugin_default",
            "factory_plugin":    "conftest_plugins.factory_plugin_default",
            "api_client_plugin": "conftest_plugins.api_client_plugin_default",
        }

    for plugin_key, module_path in plugins_section.items():
        mod = importlib.import_module(module_path)
        if not hasattr(mod, "register_fixtures"):
            raise RuntimeError(
                f"Plugin {plugin_key}={module_path} 缺少 def register_fixtures(registry) 函数"
            )
        # 把 cfg 注入进去（闭包）
        def _make_register(m):
            def _register(reg):
                m.register_fixtures(reg, cfg)
            return _register
        _make_register(mod)(registry)

    # 4. 把 cfg 本身作为一个 fixture 暴露出来（极少用，备着）
    def _project_config():
        return cfg
    conftest_globals["project_config"] = pytest.fixture(scope="session")(_project_config)

    return cfg
