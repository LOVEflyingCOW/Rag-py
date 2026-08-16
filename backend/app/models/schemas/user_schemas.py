from __future__ import annotations

from datetime import datetime
from typing import Optional, List

# Pydantic v1/v2 兼容: 项目固定 Pydantic v1.10+ (Py3.7 可用), 优先走 v1 语义
from pydantic import BaseModel, Field, EmailStr, validator  # noqa: F401

# v2 名称在 v1 中的别名映射
try:
    from pydantic import field_validator  # v2+
except ImportError:  # pragma: no cover - v1 fallback
    field_validator = validator  # type: ignore

try:
    from pydantic import ConfigDict  # v2+
except ImportError:  # pragma: no cover - v1 fallback
    # v1 直接在模型里用 class Config: 定义，这里只提供占位以支持导入
    def ConfigDict(**kwargs):  # type: ignore
        return kwargs

from app.models.response import ApiResponse


class UserRegister(BaseModel):
    """用户注册请求"""
    username: str = Field(..., min_length=3, max_length=100, description="用户名 (3-100 字符)")
    email: Optional[str] = Field(None, max_length=255, description="邮箱")
    password: str = Field(..., min_length=6, max_length=128, description="密码 (6-128 字符)")
    confirm_password: str = Field(..., description="确认密码")

    @field_validator("confirm_password")
    @classmethod
    def passwords_match(cls, v, values=None, **kwargs):
        # v1: values 是第二个参数 (dict); v2: values 在 info.data 下
        password = None
        if values is not None and isinstance(values, dict):
            password = values.get("password")  # v1 路径
        elif hasattr(kwargs.get("info", object()), "data"):  # v2 路径
            password = kwargs["info"].data.get("password")
        if password is not None and v != password:
            raise ValueError("两次输入的密码不一致")
        return v


class UserLogin(BaseModel):
    """用户登录请求"""
    username: str = Field(..., description="用户名")
    password: str = Field(..., description="密码")


class UserInfo(BaseModel):
    """用户信息响应"""
    id: int
    username: str
    email: Optional[str] = None
    is_active: bool
    is_admin: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    # v1/v2 双写法: Pydantic v1 识别 class Config; v2 识别 model_config = ConfigDict
    class Config:
        orm_mode = True

    # v2 占位 (v1 会忽略这个非 dunder 类属性)
    model_config = ConfigDict(from_attributes=True)


class TokenData(BaseModel):
    """Token 响应数据"""
    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: int = 900
    user: UserInfo


class UserCreateResponse(BaseModel):
    """注册成功响应"""
    user: UserInfo


class RefreshTokenRequest(BaseModel):
    """Refresh Token 请求"""
    refresh_token: str = Field(..., description="Refresh Token")


class LogoutRequest(BaseModel):
    """登出请求"""
    refresh_token: Optional[str] = Field(None, description="Refresh Token (可选, 同时撤销)")