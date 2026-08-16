"""tests/api/__init__.py — HTTP 契约测试包

API 层测试原则:
  1) 全部使用 conftest.api_client (httpx.AsyncClient + ASGITransport), 不需要真 uvicorn
  2) 验证: HTTP 状态码、响应 JSON 结构 (code / success / data)、权限 403
  3) 不做深层业务断言 (业务正确由 integration 测), 这层只关心「API 契约稳定」
"""
