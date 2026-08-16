"""B3 · API 契约测试 — OAuth2 Provider 配置状态

实际路由 (oauth.py router prefix="/oauth"):
    GET /api/v1/oauth/{provider}/login   — 重定向到三方授权页
    GET /api/v1/oauth/{provider}/callback— 三方回调
    GET /api/v1/oauth/status             — 已配置的 provider 列表 (启用/未启用)

重点不是让真外部登录, 而是断言两条稳定的 HTTP 契约:
  1) GET /oauth/status → 永远返回 200
  2) GET /oauth/{provider}/login 当 Client ID 未配置时, 返回 400/404/200+success=False, **不能 500**
"""
from __future__ import annotations

import pytest

OAUTH_PREFIX = "/api/v1/oauth"


class TestOAuthProviders:
    async def test_status_endpoint_200(self, api_client):
        # 即使没配 OAuth, status 端点也应该 200
        r = await api_client.get(f"{OAUTH_PREFIX}/status")
        assert r.status_code == 200, r.text

    async def test_github_login_without_config_soft_fail(self, api_client, monkeypatch):
        """github Client ID 为空, 访问 login 重定向端点不应抛 500"""
        from app.core.config import settings
        monkeypatch.setattr(settings, "OAUTH_GITHUB_CLIENT_ID", "")
        monkeypatch.setattr(settings, "OAUTH_GITHUB_CLIENT_SECRET", "")

        r = await api_client.get(f"{OAUTH_PREFIX}/github/login", follow_redirects=False)
        # 允许的契约形态 (任何一个都 OK, 关键是不能 500):
        #   404  路由未注册 / 该 provider 未被显式处理
        #   400  参数缺失
        #   200  success=False (业务拒绝)
        #   302/307 重定向 (即使 client_id 空也可能重定向到错误页)
        assert r.status_code in (400, 200, 404, 405, 302, 307), r.text
        # 500 绝对不允许
        assert r.status_code != 500
