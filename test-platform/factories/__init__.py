"""测试数据工厂包

设计原则（与 P7 通用化骨架一致）：
  - 🟦 factories/base_factory.py = 通用（任何项目直接用，不 import app.*）
  - 🟨 factories/ragpy_factories.py = RAG-PY 项目适配层（实体 + 密码 hash + 级联）

新项目接入 SOP：
  1. 复制 ragpy_factories.py → your_project_factories.py
  2. 把 `from app.models.entities.xxx import Yyy` 改成你项目的实体 import
  3. 如果你的项目有「密码 hash」「自动生成 token」这类字段处理，对应改写 _create 方法
  4. 在 project_config.yaml 里改 factories_module 指向你的文件即可
"""

from __future__ import annotations

from typing import Any


def import_all_factories(factories_module: str, registry: Any = None) -> dict[str, Any]:
    """加载 factories 模块，返回 {factory_name: factory_class} 字典。

    通用化：插件系统调用此函数拿到 factories，再注册成 fixtures。
    不用 registry 时，返回 dict 给手动调用场景。
    """
    import importlib
    mod = importlib.import_module(factories_module)
    result: dict[str, Any] = {}
    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        # 只收集「名字以 Factory 结尾」的类（忽略 base class）
        if isinstance(attr, type) and attr_name.endswith("Factory") and attr_name != "BaseAsyncFactory":
            # 例如 UserFactory → user_factory，作为 fixture 名
            fixture_name = "".join(["_" + c.lower() if c.isupper() else c for c in attr_name]).lstrip("_")
            result[fixture_name] = attr
    return result
