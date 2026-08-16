"""tests/unit/__init__.py — 单元测试包 (Phase 6 新增)

单元测试原则:
  1) 纯逻辑, 不依赖 DB / Redis / 网络
  2) 不 import conftest 里的 DB/API fixtures — 用纯 pytest 原生 fixture
  3) 快：1 组测试 < 200ms，上千条也能秒级跑完
"""
