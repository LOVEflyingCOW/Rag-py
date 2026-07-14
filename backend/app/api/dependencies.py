from __future__ import annotations

from typing import Any, Generator, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.models.entities.user import User
from app.core.security import extract_user_from_token


security = HTTPBearer(auto_error=False)


def get_db_dep() -> Generator[Session, Any, None]:
    """数据库会话依赖注入"""
    return next(get_db())


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dep)
) -> Optional[User]:
    """可选的当前用户 - 未登录时返回 None

    与 get_current_user 的区别是这个不抛出 401 异常
    """
    if credentials is None or not credentials.scheme.lower() == "bearer":
        return None

    token = credentials.credentials
    user_info = extract_user_from_token(token)
    if user_info is None:
        return None

    user = db.query(User).filter(User.id == user_info["user_id"]).first()
    if user is None or not user.is_active:
        return None

    return user


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db_dep)
) -> User:
    """必须登录的当前用户依赖 - 未登录时抛出 401"""
    if credentials is None or not credentials.scheme.lower() == "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供有效的认证令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    user_info = extract_user_from_token(token)
    if user_info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="认证令牌无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.id == user_info["user_id"]).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户账户已停用",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_current_admin(current_user: User = Depends(get_current_user)) -> User:
    """当前用户必须是管理员"""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return current_user