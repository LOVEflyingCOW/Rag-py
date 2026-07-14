from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db_dep, get_current_user
from app.models.entities.user import User
from app.models.schemas import UserRegister, UserLogin, UserInfo, TokenData
from app.core.security import hash_password, verify_password, create_access_token
from app.models.response import ApiResponse


router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=ApiResponse[TokenData])
def register(payload: UserRegister, db: Session = Depends(get_db_dep)):
    """用户注册

    - 检查用户名是否已存在
    - PBKDF2 哈希密码后存储
    - 注册成功后直接返回 Token（自动登录）
    """
    # 检查用户名是否存在
    existing_user = db.query(User).filter(User.username == payload.username).first()
    if existing_user is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="用户名已被占用",
        )

    # 检查邮箱是否存在
    if payload.email:
        existing_email = db.query(User).filter(User.email == payload.email).first()
        if existing_email is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="该邮箱已被注册",
            )

    # 创建用户
    hashed = hash_password(payload.password)
    user = User(
        username=payload.username,
        email=payload.email,
        password_hash=hashed,
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 生成 Token
    token = create_access_token(user.id, user.username)

    return ApiResponse[TokenData](
        data=TokenData(access_token=token, user=UserInfo.from_orm(user))
    )


@router.post("/login", response_model=ApiResponse[TokenData])
def login(payload: UserLogin, db: Session = Depends(get_db_dep)):
    """用户登录

    - 验证用户名和密码
    - 返回 JWT Token
    """
    user = db.query(User).filter(User.username == payload.username).first()
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

    token = create_access_token(user.id, user.username)

    return ApiResponse[TokenData](
        data=TokenData(access_token=token, user=UserInfo.from_orm(user))
    )


@router.get("/me", response_model=ApiResponse[UserInfo])
def get_me(current_user: User = Depends(get_current_user)):
    """获取当前登录用户信息

    需要在请求头中携带: `Authorization: Bearer <token>`
    """
    return ApiResponse[UserInfo](data=UserInfo.from_orm(current_user))