from __future__ import annotations

from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field, EmailStr, validator

from app.models.response import ApiResponse


class UserRegister(BaseModel):
    """用户注册请求"""
    username: str = Field(..., min_length=3, max_length=100, description="用户名 (3-100 字符)")
    email: Optional[str] = Field(None, max_length=255, description="邮箱")
    password: str = Field(..., min_length=6, max_length=128, description="密码 (6-128 字符)")
    confirm_password: str = Field(..., description="确认密码")

    @validator("confirm_password")
    def passwords_match(cls, v, values):
        if "password" in values and v != values["password"]:
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

    class Config:
        from_attributes = True
        orm_mode = True


class TokenData(BaseModel):
    """Token 响应数据"""
    access_token: str
    token_type: str = "bearer"
    user: UserInfo


class UserCreateResponse(BaseModel):
    """注册成功响应"""
    user: UserInfo