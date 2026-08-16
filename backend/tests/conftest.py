"""公共 fixture 入口（通用化后版本）

变化（vs 旧版）:
  - 旧版: 9 个 fixture 硬编码，和 RAG-PY 强耦合
  - 新版: 调用 test-platform/conftest_plugins.bootstrap_plugins，
          根据 project_config.yaml 动态注册 fixture;
          新项目只改 yaml，不需要重写 conftest.py

fixture 来源（按注册表优先级）:
  1. conftest_plugins.xxx_plugin_default（默认实现）
  2. project_config.yaml 指向的 override plugin（可覆盖同名）
  3. 本文件手动追加的 RAG 特有 fixture（mock_llm_provider）
"""
from __future__ import annotations

import sys
from pathlib import Path

# 1) 把 backend 加到 sys.path（保险：bootstrap_plugins 内部也会加，双保险不伤人）
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# 2) 把 test-platform 加到 sys.path（保证 import conftest_plugins 成功）
REPO_ROOT = BACKEND_ROOT.parent
TP_ROOT = REPO_ROOT / "test-platform"
if str(TP_ROOT) not in sys.path:
    sys.path.insert(0, str(TP_ROOT))

# 3) 启动通用插件系统：读 yaml → import plugins → 注入 fixtures
from conftest_plugins import bootstrap_plugins  # noqa: E402

_CFG = bootstrap_plugins(__file__, globals())

# 4) 平台级 fixture 已经全部注册到 globals() 里，pytest 会自动识别；
#    这里再补 RAG 项目特有的 1 个 fixture（其它项目不一定有 LLMService）:
import pytest  # noqa: E402


@pytest.fixture(scope="function")
def mock_llm_provider(monkeypatch):
    """RAG 项目专用：把 LLMService._build_provider 换成 MockLLMProvider，免 API KEY。

    如果你的项目不是 RAG，这个 fixture 不会被引用，pytest 自动忽略；
    也可以在自定义 plugin 里覆盖同名 fixture 替换成你自己的 mock。
    """
    try:
        from app.processors.llm.llm_service import LLMService, MockLLMProvider  # type: ignore
    except Exception:
        # 非 RAG 项目：直接 yield 一个空 mock，保证 conftest 不炸
        from unittest.mock import MagicMock
        yield MagicMock(name="MockLLMProviderFallback")
        return

    mock = MockLLMProvider("mock-test")
    original = LLMService._build_provider

    def _fake_build(self, *a, **kw):
        return mock

    monkeypatch.setattr(LLMService, "_build_provider", _fake_build)
    yield mock


# ============================================================
#  PHASE7 · pytest CLI 自定义扩展（通用化能力：所有项目都能用）
#  - --smoke: 快速冒烟套件，只跑 unit + api，跳过 cov，≈30s 出结果
# ============================================================
# pytest hook: 注册 --smoke 参数
def pytest_addoption(parser: pytest.Parser) -> None:
    """注册 --smoke 自定义 CLI 参数。

    用法::

        pytest --smoke        # 冒烟（unit + api，无 cov，无 phases/integration/load）
        pytest --smoke -q     # 配合简洁输出
    """
    parser.addoption(
        "--smoke",
        action="store_true",
        default=False,
        help="Run SMOKE suite only: tests/unit + tests/api, disable coverage, skip phases/integration/load (fast, ~30s).",
    )


# pytest hook: 如果 --smoke，提前改配置（跳过 cov / 限制 testpaths）
def pytest_configure(config: pytest.Config) -> None:
    """--smoke 早期配置调整（在 test collection 前执行）。

    调整点:
      1) 关闭覆盖率（cov 本身 import 重 + 每个文件插桩，至少慢 2-3 倍）
      2) 若用户没手动传测试路径（即默认跑 tests/），只收集 unit / api 目录
    """
    if not config.getoption("--smoke"):
        return

    # ---- 1) 强制 --no-cov（把 pytest.ini 里的 --cov=xxx 给覆盖掉）----
    # pytest-cov 提供 no_cov 这个 Config.option 属性，直接写 True 等价于传 --no-cov
    try:
        config.option.no_cov = True
    except AttributeError:
        # 老版本 pytest-cov 可能没 no_cov 属性，兜底：把 cov_source 清空
        if hasattr(config.option, "cov_source"):
            config.option.cov_source = []
        if hasattr(config.option, "cov_report"):
            config.option.cov_report = []

    # ---- 2) 把 testpaths 限制到 unit + api（--smoke 的核心定义）----
    # 说明:
    #   为什么不「只在默认 tests/ 下才替换」？
    #   因为 pytest.ini testpaths = tests 是相对路径，file_or_dir 初始值是 ['tests']
    #   难以和绝对路径 BACKEND_ROOT/tests 精准匹配（跨 Windows 盘符 / slash 方向都可能错）。
    #   所以只要传了 --smoke，就强制 unit/api：用户真要「自定义路径 + marker 过滤」
    #   可以用 `pytest path/ -m "not slow and not fuzz..."`，不需要 --smoke。
    tests_dir = BACKEND_ROOT / "tests"
    config.option.file_or_dir = [
        str(tests_dir / "unit"),
        str(tests_dir / "api"),
    ]


# pytest hook: --smoke 过滤核心（collection 完成后按路径 + marker 双条件过滤）
def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """--smoke 最终过滤：保留 tests/unit + tests/api 目录 且 非 fuzz/demo/slow/load 标记。

    为什么在这一层（而不是 pytest_configure 改 file_or_dir）：
      pytest 7.x 的 initial collection 在「pytest_configure 之前」就定了根路径，
      改 config.option.file_or_dir 根本无法影响已经解析好的 `testpaths: tests`。
      在 collection_modifyitems 过滤虽然「多收集再丢掉」有点浪费，但：
        1. 行为 100% 可控，跨平台跨 pytest 版本稳；
        2. 额外 collection 一次 phases 也就 1-2s（对比跑 smoke 全流程 20-30s 可接受）。
    """
    if not config.getoption("--smoke"):
        return

    # ---- 1) 路径白名单：只保留 tests/unit 或 tests/api ----
    tests_dir = BACKEND_ROOT / "tests"
    unit_prefix = str((tests_dir / "unit").resolve())
    api_prefix = str((tests_dir / "api").resolve())

    path_filtered: list = []
    for item in items:
        # Item 的真实文件路径（兼容 pytest 7.x，从 fspath / location 拿都可以）
        fspath = getattr(item, "fspath", None)
        if fspath is None:
            # fallback: 从 location[0] 拿（module file path）
            loc = getattr(item, "location", None)
            if loc:
                mod_path = (BACKEND_ROOT / loc[0]).resolve()
            else:
                # 不知道路径的 item，跳过（不保留，宁杀错不放过）
                continue
        else:
            mod_path = Path(str(fspath)).resolve()

        mod_str = str(mod_path)
        if mod_str.startswith(unit_prefix) or mod_str.startswith(api_prefix):
            path_filtered.append(item)
        # else: phases / integration / load 等目录直接丢掉

    # ---- 2) marker 黑名单（fuzz/demo/slow/load）再滤一次 ----
    skip_markers = {"fuzz", "demo", "slow", "load"}
    kept: list = []
    for item in path_filtered:
        bad = any(item.get_closest_marker(m) for m in skip_markers)
        if not bad:
            kept.append(item)

    # ---- 3) 应用过滤结果（通知 pytest 哪些被 deselected，然后原地替换 items 列表）----
    dropped = [it for it in items if it not in kept]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept  # 原地替换，pytest 用的是这个 list 的引用
