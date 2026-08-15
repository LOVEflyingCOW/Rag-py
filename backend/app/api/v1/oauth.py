"""OAuth2 三方登录 — GitHub / Google

设计:
1. /oauth/{provider}/login — 重定向到三方授权页
2. /oauth/{provider}/callback — 回调换取 token, 查找或创建本地用户, 返回双 Token
3. state 参数防 CSRF (Redis 存 5min)
4. 未配置 Client ID 的 provider 返回 400

流程:
  客户端 → GET /oauth/github/login
  → 重定向到 GitHub 授权页 (带 state)
  → 用户授权
  → GitHub 回调 /oauth/github/callback?code=xxx&state=yyy
  → 用 code 换 access_token
  → 用 access_token 获取用户信息
  → 查找或创建本地用户
  → 返回 access_token + refresh_token
"""

from __future__ import annotations

import uuid
import httpx
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_dep
from app.core.config import settings
from app.core.logging import logger
from app.core.redis import get_redis_client, is_redis_available
from app.core.security import (
    hash_password,
    create_access_token,
    create_refresh_token,
)
from app.models.entities.user import User
from app.models.response import ApiResponse

router = APIRouter(prefix="/oauth", tags=["OAuth2"])

# ============================================================
#  Provider 配置
# ============================================================

PROVIDERS = {
    "github": {
        "auth_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "user_url": "https://api.github.com/user",
        "client_id": settings.OAUTH_GITHUB_CLIENT_ID,
        "client_secret": settings.OAUTH_GITHUB_CLIENT_SECRET,
        "scopes": "read:user user:email",
    },
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "user_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "client_id": settings.OAUTH_GOOGLE_CLIENT_ID,
        "client_secret": settings.OAUTH_GOOGLE_CLIENT_SECRET,
        "scopes": "openid email profile",
    },
}


# ============================================================
#  登录入口
# ============================================================

@router.get("/{provider}/login")
async def oauth_login(provider: str):
    """重定向到三方授权页

    Args:
        provider: "github" 或 "google"
    """
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    config = PROVIDERS[provider]
    if not config["client_id"]:
        raise HTTPException(
            status_code=400,
            detail=f"OAuth2 {provider} not configured. Set OAUTH_{provider.upper()}_CLIENT_ID."
        )

    # 生成 state 防 CSRF
    state = uuid.uuid4().hex[:16]

    # 存入 Redis (5min 过期)
    if is_redis_available():
        redis = get_redis_client()
        if redis:
            await redis.setex(f"oauth:state:{state}", 300, provider)

    # 构造授权 URL
    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE}/{provider}/callback"
    auth_url = (
        f"{config['auth_url']}"
        f"?client_id={config['client_id']}"
        f"&redirect_uri={redirect_uri}"
        f"&scope={config['scopes']}"
        f"&state={state}"
        f"&response_type=code"
    )

    logger.info("OAuth2 login redirect: provider=%s, state=%s", provider, state)
    return RedirectResponse(url=auth_url)


# ============================================================
#  回调
# ============================================================

@router.get("/{provider}/callback")
async def oauth_callback(
    provider: str,
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db_dep),
):
    """三方登录回调

    Args:
        provider: "github" 或 "google"
        code: 授权码
        state: CSRF 防护 state
    """
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    config = PROVIDERS[provider]
    if not config["client_id"]:
        raise HTTPException(status_code=400, detail=f"OAuth2 {provider} not configured")

    # 1. 校验 state
    if is_redis_available():
        redis = get_redis_client()
        if redis:
            saved_provider = await redis.get(f"oauth:state:{state}")
            if not saved_provider:
                raise HTTPException(status_code=400, detail="Invalid or expired state")
            await redis.delete(f"oauth:state:{state}")

    # 2. 换取 access_token
    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE}/{provider}/callback"
    token_data = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }

    async with httpx.AsyncClient() as client:
        # GitHub 返回 JSON, Google 返回 JSON
        token_resp = await client.post(
            config["token_url"],
            data=token_data,
            headers={"Accept": "application/json"},
        )

        if token_resp.status_code != 200:
            logger.error("OAuth token exchange failed: %s", token_resp.text)
            raise HTTPException(status_code=400, detail="Token exchange failed")

        token_json = token_resp.json()
        oauth_access_token = token_json.get("access_token")
        if not oauth_access_token:
            raise HTTPException(status_code=400, detail="No access_token in response")

        # 3. 获取用户信息
        user_resp = await client.get(
            config["user_url"],
            headers={"Authorization": f"Bearer {oauth_access_token}"},
        )

        if user_resp.status_code != 200:
            logger.error("OAuth user info failed: %s", user_resp.text)
            raise HTTPException(status_code=400, detail="Failed to get user info")

        user_info = user_resp.json()

    # 4. 提取用户信息 (GitHub / Google 格式不同)
    if provider == "github":
        oauth_id = str(user_info.get("id", ""))
        username = user_info.get("login", "")
        email = user_info.get("email") or f"{username}@github.oauth"
        full_name = user_info.get("name") or username
    else:  # google
        oauth_id = user_info.get("id", "")
        email = user_info.get("email", "")
        username = email.split("@")[0] if email else f"google_{oauth_id}"
        full_name = user_info.get("name", "")

    if not oauth_id:
        raise HTTPException(status_code=400, detail="Failed to extract user ID")

    # 5. 查找或创建本地用户
    oauth_user_id = f"{provider}_{oauth_id}"

    # 先查 username (provider_oauthid)
    result = await db.execute(
        select(User).where(User.username == oauth_user_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        # 创建新用户
        user = User(
            username=oauth_user_id,
            email=email,
            password_hash=hash_password(uuid.uuid4().hex),  # 随机密码 (OAuth 用户不使用密码登录)
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info("OAuth2 user created: provider=%s, user_id=%d", provider, user.id)
    else:
        logger.info("OAuth2 user login: provider=%s, user_id=%d", provider, user.id)

    # 6. 生成双 Token
    access_token = create_access_token(
        data={"sub": str(user.id), "username": user.username, "roles": []}
    )
    refresh_token = await create_refresh_token(
        db=db,
        user_id=user.id,
    )

    # 7. 返回 Token (重定向到前端, 通过 query param 传递)
    frontend_url = settings.cors_origin_list[0] if settings.cors_origin_list else "http://localhost:3000"
    redirect_url = (
        f"{frontend_url}/oauth/callback"
        f"?access_token={access_token}"
        f"&refresh_token={refresh_token}"
        f"&token_type=bearer"
    )

    return RedirectResponse(url=redirect_url)


# ============================================================
#  查询 OAuth 配置状态
# ============================================================

@router.get("/status")
async def oauth_status():
    """查询已配置的 OAuth provider"""
    available = []
    for provider, config in PROVIDERS.items():
        if config["client_id"]:
            available.append(provider)
    return {
        "available_providers": available,
        "configured": len(available) > 0,
    }
