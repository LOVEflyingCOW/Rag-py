from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_dep, get_current_user, get_current_user_from_db, CurrentUser
from app.models.entities.user import User
from app.models.entities.auth import RefreshToken
from app.models.schemas import UserRegister, UserLogin, UserInfo, TokenData, RefreshTokenRequest, LogoutRequest
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token,
    extract_user_from_token, extract_user_from_refresh_token,
    extract_user_from_refresh_token_async,
    revoke_token, revoke_token_async,
    hash_token,
)
from app.core.config import settings
from app.models.response import ApiResponse


router = APIRouter(prefix="/auth", tags=["认证"])


# ============================================================
#  辅助函数
# ============================================================

async def _save_refresh_token(
    db: AsyncSession, user_id: int, token: str, request: Request
) -> None:
    """将 Refresh Token 哈希后存入数据库"""
    token_h = hash_token(token)
    rt = RefreshToken(
        user_id=user_id,
        token_hash=token_h,
        device_info=request.headers.get("User-Agent", "")[:500] if request else None,
        ip_address=request.client.host if request and request.client else None,
        expires_at=datetime.utcnow() + timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS),
    )
    db.add(rt)
    await db.commit()


async def _revoke_refresh_token(db: AsyncSession, token: str) -> bool:
    """撤销 Refresh Token"""
    token_h = hash_token(token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_h)
    )
    rt = result.scalars().first()
    if rt and rt.revoked_at is None:
        rt.revoked_at = datetime.utcnow()
        await db.commit()
        return True
    return False


async def _is_refresh_token_valid(db: AsyncSession, token: str) -> bool:
    """检查 Refresh Token 是否在数据库中且未撤销"""
    token_h = hash_token(token)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_h)
    )
    rt = result.scalars().first()
    if rt is None:
        return False
    if rt.revoked_at is not None:
        return False
    if rt.expires_at <= datetime.utcnow():
        return False
    return True


def _get_user_roles(user: User) -> list:
    """获取用户角色名列表"""
    if user.is_admin:
        return ["admin"]
    return ["viewer"]


# ============================================================
#  注册
# ============================================================

@router.post("/register", response_model=ApiResponse[TokenData])
async def register(payload: UserRegister, request: Request, db: AsyncSession = Depends(get_db_dep)):
    """用户注册 — 注册成功后直接返回双 Token (自动登录)"""
    result = await db.execute(select(User).where(User.username == payload.username))
    existing_user = result.scalars().first()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已被占用",
        )

    if payload.email:
        result = await db.execute(select(User).where(User.email == payload.email))
        existing_email = result.scalars().first()
        if existing_email is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该邮箱已被注册",
            )

    hashed = hash_password(payload.password)
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hashed,
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    roles = _get_user_roles(user)
    access_token = create_access_token(user.id, user.username, roles)
    refresh_token = create_refresh_token(user.id)

    await _save_refresh_token(db, user.id, refresh_token, request)

    return ApiResponse[TokenData](
        data=TokenData(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
            user=UserInfo.from_orm(user),
        )
    )


# ============================================================
#  登录
# ============================================================

@router.post("/login", response_model=ApiResponse[TokenData])
async def login(payload: UserLogin, request: Request, db: AsyncSession = Depends(get_db_dep)):
    """用户登录 — 返回双 Token (Access + Refresh)"""
    result = await db.execute(select(User).where(User.username == payload.username))
    user = result.scalars().first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账户已停用，请联系管理员",
        )

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    roles = _get_user_roles(user)
    access_token = create_access_token(user.id, user.username, roles)
    refresh_token = create_refresh_token(user.id)

    await _save_refresh_token(db, user.id, refresh_token, request)

    return ApiResponse[TokenData](
        data=TokenData(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
            user=UserInfo.from_orm(user),
        )
    )


# ============================================================
#  刷新 Token
# ============================================================

@router.post("/refresh", response_model=ApiResponse[TokenData])
async def refresh_token_endpoint(payload: RefreshTokenRequest, request: Request, db: AsyncSession = Depends(get_db_dep)):
    """用 Refresh Token 换取新的双 Token (轮换: 旧的 Refresh Token 失效)"""
    rt_info = await extract_user_from_refresh_token_async(payload.refresh_token)
    if rt_info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh Token 无效或已过期",
        )

    # 检查是否在数据库中且未撤销
    is_valid = await _is_refresh_token_valid(db, payload.refresh_token)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh Token 已被撤销",
        )

    # 查找用户
    result = await db.execute(select(User).where(User.id == rt_info["user_id"]))
    user = result.scalars().first()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已停用",
        )

    # 轮换: 撤销旧的 Refresh Token (DB + Redis 黑名单)
    await _revoke_refresh_token(db, payload.refresh_token)
    await revoke_token_async(payload.refresh_token, ttl=settings.JWT_REFRESH_EXPIRE_DAYS * 86400)

    # 签发新的双 Token
    roles = _get_user_roles(user)
    new_access = create_access_token(user.id, user.username, roles)
    new_refresh = create_refresh_token(user.id)

    await _save_refresh_token(db, user.id, new_refresh, request)

    return ApiResponse[TokenData](
        data=TokenData(
            access_token=new_access,
            refresh_token=new_refresh,
            expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
            user=UserInfo.from_orm(user),
        )
    )


# ============================================================
#  登出
# ============================================================

@router.post("/logout", response_model=ApiResponse)
async def logout(
    payload: LogoutRequest,
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_dep),
):
    """登出 — 撤销 Access Token + Refresh Token (Redis 黑名单)"""
    # 从请求头获取 Access Token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        access_token = auth_header[7:]
        await revoke_token_async(access_token, ttl=settings.JWT_ACCESS_EXPIRE_MINUTES * 60)

    # 撤销 Refresh Token (DB + Redis 黑名单)
    if payload.refresh_token:
        await _revoke_refresh_token(db, payload.refresh_token)
        await revoke_token_async(payload.refresh_token, ttl=settings.JWT_REFRESH_EXPIRE_DAYS * 86400)

    return ApiResponse(message="登出成功")


# ============================================================
#  获取当前用户
# ============================================================

@router.get("/me", response_model=ApiResponse[UserInfo])
async def get_me(current_user: User = Depends(get_current_user_from_db)):
    """获取当前登录用户信息 — 需要从数据库读取完整用户对象"""
    return ApiResponse[UserInfo](data=UserInfo.from_orm(current_user))
