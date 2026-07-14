from __future__ import annotations

import hashlib
import hmac
import json
import os
import base64
import time
from typing import Dict, Optional, Any

from app.core.config import settings

_HASH_ALGORITHM = "sha256"
_PBKDF2_ITERATIONS = 100000
_SALT_LENGTH = 16


def generate_salt() -> str:
    """生成随机盐值 (hex)"""
    return os.urandom(_SALT_LENGTH).hex()


def hash_password(password: str, salt: Optional[str] = None) -> str:
    """使用 PBKDF2-HMAC 哈希密码

    返回格式: pbkdf2_sha256$iterations$salt$hash_hex
    """
    if salt is None:
        salt = generate_salt()

    password_bytes = password.encode("utf-8")
    salt_bytes = salt.encode("utf-8")

    key = hashlib.pbkdf2_hmac(
        _HASH_ALGORITHM,
        password_bytes,
        salt_bytes,
        _PBKDF2_ITERATIONS,
        dklen=32
    )

    return "pbkdf2_sha256$%d$%s$%s" % (_PBKDF2_ITERATIONS, salt, key.hex())


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    try:
        parts = hashed_password.split("$")
        if len(parts) != 4:
            return False

        algorithm, iterations_str, salt, stored_hash = parts
        if algorithm != "pbkdf2_sha256":
            return False

        iterations = int(iterations_str)
        password_bytes = plain_password.encode("utf-8")
        salt_bytes = salt.encode("utf-8")

        computed = hashlib.pbkdf2_hmac(
            _HASH_ALGORITHM,
            password_bytes,
            salt_bytes,
            iterations,
            dklen=32
        )

        return computed.hex() == stored_hash
    except Exception:
        return False


def _base64url_encode(data: bytes) -> str:
    """Base64 URL 安全编码"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _base64url_decode(data: str) -> bytes:
    """Base64 URL 安全解码"""
    padding = "=" * (4 - len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def create_jwt_token(payload: Dict[str, Any], secret: Optional[str] = None,
                     algorithm: str = "HS256",
                     expire_minutes: Optional[int] = None) -> str:
    """创建 JWT Token

    手写实现（不依赖 PyJWT），保证 Python 3.7 兼容性
    """
    if secret is None:
        secret = settings.SECRET_KEY

    if expire_minutes is None:
        expire_minutes = settings.JWT_EXPIRE_MINUTES

    now = int(time.time())
    header = {"typ": "JWT", "alg": algorithm}

    payload = dict(payload)
    payload["iat"] = now
    payload["exp"] = now + expire_minutes * 60

    header_b64 = _base64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _base64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

    signing_input = (header_b64 + "." + payload_b64).encode("utf-8")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    signature_b64 = _base64url_encode(signature)

    return header_b64 + "." + payload_b64 + "." + signature_b64


def decode_jwt_token(token: str, secret: Optional[str] = None,
                     algorithms: Optional[list] = None) -> Optional[Dict[str, Any]]:
    """验证并解码 JWT Token

    失败返回 None
    """
    if secret is None:
        secret = settings.SECRET_KEY
    if algorithms is None:
        algorithms = ["HS256"]

    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header_b64, payload_b64, signature_b64 = parts

        signing_input = (header_b64 + "." + payload_b64).encode("utf-8")
        expected_signature = hmac.new(
            secret.encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        provided_signature = _base64url_decode(signature_b64)

        if not hmac.compare_digest(expected_signature, provided_signature):
            return None

        payload = json.loads(_base64url_decode(payload_b64).decode("utf-8"))

        now = int(time.time())
        if "exp" in payload and payload["exp"] < now:
            return None

        return payload
    except Exception:
        return None


def create_access_token(user_id: int, username: str) -> str:
    """为用户创建访问令牌"""
    payload = {
        "sub": str(user_id),
        "username": username,
        "type": "access"
    }
    return create_jwt_token(payload)


def extract_user_from_token(token: str) -> Optional[Dict[str, Any]]:
    """从 Token 中提取用户信息"""
    payload = decode_jwt_token(token)
    if payload is None:
        return None
    try:
        return {
            "user_id": int(payload["sub"]),
            "username": payload.get("username", ""),
        }
    except (KeyError, ValueError, TypeError):
        return None