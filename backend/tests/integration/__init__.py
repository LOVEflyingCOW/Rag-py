"""tests/integration/__init__.py — 集成测试包

集成测试特点：
  1) 依赖 conftest.db_session（SQLite 内存）/ test_user / test_kb / test_document
  2) 直接 import 业务 service 做跨模块链路验证
  3) 比 unit 慢，但比端到端快（不启动真实 uvicorn，不依赖网络/Redis/LLM API）
"""
