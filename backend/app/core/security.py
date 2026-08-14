from __future__ import annotations

"""
安全模块 — Argon2id 密码哈希 + PyJWT 双 Token 机制

功能:
1. Argon2id 密码哈希与验证
2. Access Token (短时, 15min)
3. Refresh Token (长时, 7d, 支持轮换和撤销)
4. Token 黑名单 (Redis SET + EXPIRE, 内存降级)
5. Token 校验与用户提取
"""

import asyncio
import hashlib
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Set

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from app.core.config import settings


# ============================================================
#  Argon2id 密码哈希
# ============================================================

_pwd_hasher = PasswordHasher(
    time_cost=2,        # 迭代次数 (开发: 2, 生产: 3+)
    memory_cost=16384,  # 16MB (开发: 16MB, 生产: 64MB+)
    parallelism=2,      # 并行线程
    type=Type.ID,       # Argon2id
)


def hash_password(password: str) -> str:
    """使用 Argon2id 哈希密码"""
    return _pwd_hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    """验证密码是否匹配哈希"""
    try:
        return _pwd_hasher.verify(hashed, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


# ============================================================
#  Token 黑名单 (Redis SET + EXPIRE, 内存降级)
# ============================================================
#  Redis key: blacklist:{sha256(token)}
#  Value: "1"
#  TTL: 与 Token 剩余有效期一致 (自动过期, 不占内存)
# ============================================================

# 内存降级黑名单 (Redis 不可用时使用)
_blacklist: Set[str] = set()

# Redis key 前缀
_BLACKLIST_PREFIX = "blacklist:"


def _token_hash(token: str) -> str:
    """对 Token 做 SHA-256 摘要 (作为 Redis key, 不存明文)"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def revoke_token(token: str, ttl: Optional[int] = None) -> None:
    """将 Token 加入黑名单

    Args:
        token: JWT Token 字符串
        ttl: 黑名单保留秒数 (默认 = Access Token 有效期 900s)
    """
    token_h = _token_hash(token)
    if ttl is None:
        ttl = settings.JWT_ACCESS_EXPIRE_MINUTES * 60

    # 尝试写入 Redis
    from app.core.redis import get_redis_client, is_redis_available
    if is_redis_available():
        redis = get_redis_client()
        if redis is not None:
            # 使用 asyncio.create_task 异步写入, 不阻塞当前请求
            # 如果在事件循环中, 用 ensure_future
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(
                    redis.set(_BLACKLIST_PREFIX + token_h, "1", ex=ttl)
                )
            except RuntimeError:
                # 不在事件循环中 (同步调用), 用同步 Redis 客户端降级
                _blacklist.add(token_h)
        else:
            _blacklist.add(token_h)
    else:
        _blacklist.add(token_h)


async def revoke_token_async(token: str, ttl: Optional[int] = None) -> None:
    """异步将 Token 加入黑名单 (推荐在 async 路由中使用)"""
    token_h = _token_hash(token)
    if ttl is None:
        ttl = settings.JWT_ACCESS_EXPIRE_MINUTES * 60

    _blacklist.add(token_h)

    from app.core.redis import get_redis_client, is_redis_available
    if is_redis_available():
        redis = get_redis_client()
        if redis is not None:
            try:
                await redis.set(_BLACKLIST_PREFIX + token_h, "1", ex=ttl)
            except Exception:
                pass


def is_token_revoked(token: str) -> bool:
    """检查 Token 是否在黑名单中 (同步, 可能存在 Redis 延迟)"""
    token_h = _token_hash(token)

    # 先检查内存 (覆盖 Redis 不可用 + 刚撤销尚未同步的情况)
    if token_h in _blacklist:
        return True

    # Redis 检查需要异步, 这里返回 False, 由异步路径 (extract_user_from_token_async) 精确检查
    return False


async def is_token_revoked_async(token: str) -> bool:
    """异步检查 Token 是否在黑名单中 (精确, 推荐)"""
    token_h = _token_hash(token)

    # 先检查内存
    if token_h in _blacklist:
        return True

    # 再检查 Redis
    from app.core.redis import get_redis_client, is_redis_available
    if is_redis_available():
        redis = get_redis_client()
        if redis is not None:
            try:
                exists = await redis.exists(_BLACKLIST_PREFIX + token_h)
                if exists:
                    _blacklist.add(token_h)
                    return True
            except Exception:
                pass

    return False


# ============================================================
#  Access Token (短时, 15min)
# ============================================================

def create_access_token(user_id: int, username: str, roles: Optional[list] = None) -> str:
    """创建 Access Token — 15 分钟有效期"""
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "username": username,
        "roles": roles or [],
        "type": "access",
        "iat": now,
        "exp": now + 900,  # 15 分钟
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


# ============================================================
#  Refresh Token (长时, 7d)
# ============================================================

def create_refresh_token(user_id: int) -> str:
    """创建 Refresh Token — 7 天有效期"""
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "iat": now,
        "exp": now + 604800,  # 7 天
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def hash_token(token: str) -> str:
    """对 Token 做 SHA-256 摘要 (存数据库用, 不存明文)"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ============================================================
#  Token 校验
# ============================================================

def decode_token(token: str) -> Optional[Dict[str, Any]]:
    """解码 JWT Token, 返回 payload 或 None"""
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def extract_user_from_token(token: str) -> Optional[Dict[str, Any]]:
    """从 Access Token 提取用户信息 (同步, 仅检查内存黑名单)"""
    if is_token_revoked(token):
        return None

    payload = decode_token(token)
    if payload is None:
        return None

    if payload.get("type") != "access":
        return None

    try:
        return {
            "user_id": int(payload["sub"]),
            "username": payload.get("username", ""),
            "roles": payload.get("roles", []),
            "jti": payload.get("jti", ""),
        }
    except (KeyError, ValueError):
        return None


async def extract_user_from_token_async(token: str) -> Optional[Dict[str, Any]]:
    """从 Access Token 提取用户信息 (异步, 精确检查 Redis 黑名单)

    推荐在 FastAPI 依赖注入中使用此函数.
    """
    if await is_token_revoked_async(token):
        return None

    payload = decode_token(token)
    if payload is None:
        return None

    if payload.get("type") != "access":
        return None

    try:
        return {
            "user_id": int(payload["sub"]),
            "username": payload.get("username", ""),
            "roles": payload.get("roles", []),
            "jti": payload.get("jti", ""),
        }
    except (KeyError, ValueError):
        return None


def extract_user_from_refresh_token(token: str) -> Optional[Dict[str, Any]]:
    """从 Refresh Token 提取用户信息 (同步, 仅检查内存黑名单)"""
    if is_token_revoked(token):
        return None

    payload = decode_token(token)
    if payload is None:
        return None

    if payload.get("type") != "refresh":
        return None

    try:
        return {
            "user_id": int(payload["sub"]),
            "jti": payload.get("jti", ""),
            "exp": payload.get("exp", 0),
        }
    except (KeyError, ValueError):
        return None


async def extract_user_from_refresh_token_async(token: str) -> Optional[Dict[str, Any]]:
    """从 Refresh Token 提取用户信息 (异步, 精确检查 Redis 黑名单)"""
    if await is_token_revoked_async(token):
        return None

    payload = decode_token(token)
    if payload is None:
        return None

    if payload.get("type") != "refresh":
        return None

    try:
        return {
            "user_id": int(payload["sub"]),
            "jti": payload.get("jti", ""),
            "exp": payload.get("exp", 0),
        }
    except (KeyError, ValueError):
        return None


# ============================================================
#  兼容: 旧版 create_token (Phase 1 测试用)
# ============================================================

def create_token(user_id: int, username: str) -> str:
    """旧版兼容接口 — 等同于 create_access_token"""
    return create_access_token(user_id, username)
