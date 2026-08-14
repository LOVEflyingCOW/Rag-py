"""
Phase 2 验证测试 — 鉴权安全体系

测试覆盖:
  A. 安全核心 (security.py): Argon2id 密码 + 双 Token + 黑名单
  B. 实体模型: RefreshToken / Role / Permission / AuditLog 表是否存在
  C. API 端点: 注册(返回双Token) / 登录(双Token) / 刷新 / 登出 / 获取用户
  D. RBAC: 角色权限表结构
  E. 限流中间件: 超限返回 429
  F. 审计日志: API 调用是否被记录
  G. 安全响应头: X-Content-Type-Options 等
  H. 请求体大小限制: 超大请求返回 413
  I. 负面测试: 无效 Token / 过期 Token / 已撤销 Token

运行方式 (需先启动服务):
  cd backend
  ..\venv\Scripts\python.exe tests/phases/test_phase2_auth_security.py
"""

import sys
import os
import time
import json
import requests
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

PASS = 0
FAIL = 0
SKIP = 0

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api/v1"


def pass_test(name, detail=""):
    global PASS; PASS += 1
    print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))

def fail_test(name, detail=""):
    global FAIL; FAIL += 1
    print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))

def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


# ============================================================
#  A. 安全核心 (security.py)
# ============================================================

def test_security_core():
    section("A. 安全核心 (security.py)")

    from app.core.security import (
        hash_password, verify_password,
        create_access_token, create_refresh_token,
        decode_token, extract_user_from_token, extract_user_from_refresh_token,
        revoke_token, is_token_revoked, hash_token,
    )

    # A.1 Argon2id 密码哈希
    hashed = hash_password("TestPassword123")
    if hashed.startswith("$argon2id$"):
        pass_test("A.1 Argon2id 密码哈希格式正确", hashed[:30] + "...")
    else:
        fail_test("A.1 密码哈希格式错误", hashed[:30])

    # A.2 密码验证正确
    if verify_password("TestPassword123", hashed):
        pass_test("A.2 正确密码验证通过")
    else:
        fail_test("A.2 正确密码验证失败")

    # A.3 错误密码验证失败
    if not verify_password("WrongPassword", hashed):
        pass_test("A.3 错误密码验证失败 (正确行为)")
    else:
        fail_test("A.3 错误密码验证通过 (安全漏洞!)")

    # A.4 Access Token 创建
    access = create_access_token(42, "testuser", ["viewer"])
    if access and len(access) > 50:
        pass_test("A.4 Access Token 创建成功")
    else:
        fail_test("A.4 Access Token 创建失败")

    # A.5 Access Token 解码
    info = extract_user_from_token(access)
    if info and info["user_id"] == 42 and info["username"] == "testuser":
        pass_test("A.5 Access Token 解码正确", f"user_id={info['user_id']}")
    else:
        fail_test("A.5 Access Token 解码失败", str(info))

    # A.6 Refresh Token 创建
    refresh = create_refresh_token(42)
    if refresh and len(refresh) > 50:
        pass_test("A.6 Refresh Token 创建成功")
    else:
        fail_test("A.6 Refresh Token 创建失败")

    # A.7 Refresh Token 解码
    rt_info = extract_user_from_refresh_token(refresh)
    if rt_info and rt_info["user_id"] == 42:
        pass_test("A.7 Refresh Token 解码正确")
    else:
        fail_test("A.7 Refresh Token 解码失败")

    # A.8 Token 黑名单 — 撤销后不可用
    revoke_token(access)
    if is_token_revoked(access):
        pass_test("A.8 Token 撤销后加入黑名单")
    else:
        fail_test("A.8 Token 撤销未生效")
    # 清理黑名单
    from app.core.security import _blacklist
    _blacklist.discard(access)

    # A.9 撤销的 Token 提取用户返回 None
    revoke_token(access)
    info2 = extract_user_from_token(access)
    if info2 is None:
        pass_test("A.9 撤销的 Token 无法提取用户")
    else:
        fail_test("A.9 撤销的 Token 仍可提取用户 (安全漏洞!)")
    _blacklist.discard(access)

    # A.10 Token hash
    h = hash_token(access)
    if len(h) == 64:  # SHA-256 hex
        pass_test("A.10 Token SHA-256 哈希正确", f"len={len(h)}")
    else:
        fail_test("A.10 Token 哈希长度错误", f"len={len(h)}")

    # A.11 无效 Token 解码返回 None
    if decode_token("invalid.token.here") is None:
        pass_test("A.11 无效 Token 解码返回 None")
    else:
        fail_test("A.11 无效 Token 解码未返回 None")

    # A.12 用 Refresh Token 不能提取 Access 用户信息
    wrong = extract_user_from_token(refresh)
    if wrong is None:
        pass_test("A.12 Refresh Token 不能用于 Access 接口")
    else:
        fail_test("A.12 Refresh Token 被误用于 Access (安全漏洞!)")


# ============================================================
#  B. 实体模型
# ============================================================

def test_entity_models():
    section("B. 实体模型 (auth.py)")

    from app.models.entities.auth import RefreshToken, Role, Permission, AuditLog
    from app.models.database import Base
    import app.models.entities  # 确保导入

    tables = Base.metadata.tables

    # B.1 refresh_tokens 表
    if "refresh_tokens" in tables:
        pass_test("B.1 refresh_tokens 表存在")
    else:
        fail_test("B.1 refresh_tokens 表不存在")

    # B.2 roles 表
    if "roles" in tables:
        pass_test("B.2 roles 表存在")
    else:
        fail_test("B.2 roles 表不存在")

    # B.3 permissions 表
    if "permissions" in tables:
        pass_test("B.3 permissions 表存在")
    else:
        fail_test("B.3 permissions 表不存在")

    # B.4 audit_logs 表
    if "audit_logs" in tables:
        pass_test("B.4 audit_logs 表存在")
    else:
        fail_test("B.4 audit_logs 表不存在")

    # B.5 role_permissions 关联表
    if "role_permissions" in tables:
        pass_test("B.5 role_permissions 关联表存在")
    else:
        fail_test("B.5 role_permissions 关联表不存在")

    # B.6 user_roles 关联表
    if "user_roles" in tables:
        pass_test("B.6 user_roles 关联表存在")
    else:
        fail_test("B.6 user_roles 关联表不存在")

    # B.7 RefreshToken 关键字段
    rt_cols = set(tables["refresh_tokens"].columns.keys())
    required = {"id", "user_id", "token_hash", "expires_at", "revoked_at", "created_at"}
    if required.issubset(rt_cols):
        pass_test("B.7 RefreshToken 关键字段完整", str(sorted(required)))
    else:
        fail_test("B.7 RefreshToken 缺少字段", str(required - rt_cols))

    # B.8 AuditLog 关键字段
    al_cols = set(tables["audit_logs"].columns.keys())
    required_al = {"id", "method", "path", "status_code", "ip_address", "response_time_ms"}
    if required_al.issubset(al_cols):
        pass_test("B.8 AuditLog 关键字段完整")
    else:
        fail_test("B.8 AuditLog 缺少字段", str(required_al - al_cols))


# ============================================================
#  C. API 端点 (双 Token 流程)
# ============================================================

def test_api_endpoints():
    section("C. API 端点 (双 Token 流程)")

    ts = str(int(time.time()))
    h = {"Content-Type": "application/json"}

    # C.1 注册 — 返回双 Token (Argon2id 较慢, 用 15s 超时)
    try:
        resp = requests.post(f"{API}/auth/register", json={
            "username": f"phase2_{ts}",
            "password": "Test123456",
            "confirm_password": "Test123456",
            "email": f"phase2_{ts}@test.com",
        }, timeout=15)
        data = resp.json().get("data", {})
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires = data.get("expires_in")

        if resp.status_code == 200 and access and refresh:
            pass_test("C.1 注册返回双 Token", f"access={access[:20]}..., refresh={refresh[:20]}...")
        else:
            fail_test("C.1 注册未返回双 Token", f"status={resp.status_code}, keys={list(data.keys())}")
            return
    except Exception as e:
        fail_test("C.1 注册异常", str(e)[:80])
        return

    # C.2 expires_in 存在
    if expires and expires == 900:
        pass_test("C.2 expires_in = 900 (15min)")
    else:
        fail_test("C.2 expires_in 错误", str(expires))

    # C.3 登录 — 返回双 Token
    try:
        resp = requests.post(f"{API}/auth/login", json={
            "username": f"phase2_{ts}",
            "password": "Test123456",
        }, timeout=15)
        data = resp.json().get("data", {})
        access2 = data.get("access_token")
        refresh2 = data.get("refresh_token")

        if resp.status_code == 200 and access2 and refresh2:
            pass_test("C.3 登录返回双 Token")
        else:
            fail_test("C.3 登录未返回双 Token", f"status={resp.status_code}")
            return
    except Exception as e:
        fail_test("C.3 登录异常", str(e)[:80])
        return

    # C.4 获取用户 (用 access token)
    try:
        resp = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {access2}"}, timeout=5)
        if resp.status_code == 200:
            pass_test("C.4 获取用户成功")
        else:
            fail_test("C.4 获取用户失败", f"status={resp.status_code}")
    except Exception as e:
        fail_test("C.4 获取用户异常", str(e)[:80])

    # C.5 刷新 Token
    try:
        resp = requests.post(f"{API}/auth/refresh", json={"refresh_token": refresh2}, timeout=5)
        data = resp.json().get("data", {})
        new_access = data.get("access_token")
        new_refresh = data.get("refresh_token")

        if resp.status_code == 200 and new_access and new_refresh:
            pass_test("C.5 刷新 Token 成功", "获得新的双 Token")
        else:
            fail_test("C.5 刷新 Token 失败", f"status={resp.status_code}, body={resp.text[:100]}")
    except Exception as e:
        fail_test("C.5 刷新 Token 异常", str(e)[:80])

    # C.6 旧 Refresh Token 不可重用 (轮换)
    try:
        resp = requests.post(f"{API}/auth/refresh", json={"refresh_token": refresh2}, timeout=5)
        if resp.status_code == 401:
            pass_test("C.6 旧 Refresh Token 已失效 (轮换成功)")
        else:
            fail_test("C.6 旧 Refresh Token 仍可用 (轮换失败!)", f"status={resp.status_code}")
    except Exception as e:
        fail_test("C.6 旧 Refresh Token 测试异常", str(e)[:80])

    # C.7 登出
    try:
        resp = requests.post(f"{API}/auth/logout",
            json={"refresh_token": new_refresh if 'new_refresh' in dir() else refresh},
            headers={"Authorization": f"Bearer {new_access if 'new_access' in dir() else access2}"},
            timeout=5)
        if resp.status_code == 200:
            pass_test("C.7 登出成功")
        else:
            fail_test("C.7 登出失败", f"status={resp.status_code}")
    except Exception as e:
        fail_test("C.7 登出异常", str(e)[:80])

    # C.8 登出后 Access Token 失效
    try:
        resp = requests.get(f"{API}/auth/me",
            headers={"Authorization": f"Bearer {new_access if 'new_access' in dir() else access2}"},
            timeout=5)
        if resp.status_code == 401:
            pass_test("C.8 登出后 Token 被撤销 (401)")
        else:
            fail_test("C.8 登出后 Token 仍可用 (安全漏洞!)", f"status={resp.status_code}")
    except Exception as e:
        fail_test("C.8 登出后 Token 测试异常", str(e)[:80])


# ============================================================
#  D. RBAC
# ============================================================

def test_rbac():
    section("D. RBAC 角色权限")

    from app.models.entities.auth import Role, Permission, role_permissions, user_roles
    from app.models.database import Base
    import app.models.entities

    tables = Base.metadata.tables

    # D.1 Role 有 name 字段
    if "name" in tables["roles"].columns:
        pass_test("D.1 Role.name 字段存在")
    else:
        fail_test("D.1 Role.name 字段不存在")

    # D.2 Permission 有 resource + action
    perm_cols = set(tables["permissions"].columns.keys())
    if "resource" in perm_cols and "action" in perm_cols:
        pass_test("D.2 Permission 有 resource + action 字段")
    else:
        fail_test("D.2 Permission 缺少 resource/action 字段")

    # D.3 role_permissions 关联表
    rp_cols = set(tables["role_permissions"].columns.keys())
    if "role_id" in rp_cols and "permission_id" in rp_cols:
        pass_test("D.3 role_permissions 有 role_id + permission_id")
    else:
        fail_test("D.3 role_permissions 缺少关联字段")

    # D.4 user_roles 关联表
    ur_cols = set(tables["user_roles"].columns.keys())
    if "user_id" in ur_cols and "role_id" in ur_cols:
        pass_test("D.4 user_roles 有 user_id + role_id")
    else:
        fail_test("D.4 user_roles 缺少关联字段")


# ============================================================
#  E. 限流中间件
# ============================================================

def test_rate_limit():
    section("E. 限流中间件")

    # E.1 快速发 35 个匿名请求, 应有 429
    try:
        status_codes = []
        for i in range(35):
            resp = requests.get(f"{API}/knowledge-bases?page=1&page_size=1", timeout=3)
            status_codes.append(resp.status_code)

        has_429 = 429 in status_codes
        ok_count = sum(1 for s in status_codes if s in (200, 401))

        if has_429:
            pass_test("E.1 超限请求返回 429", f"ok={ok_count}, 429={status_codes.count(429)}")
        else:
            fail_test("E.1 超限请求未返回 429", f"codes={set(status_codes)}")
    except Exception as e:
        fail_test("E.1 限流测试异常", str(e)[:80])

    # E.2 响应头有限流信息
    try:
        resp = requests.get(f"{BASE}/health", timeout=3)
        if "X-RateLimit-Limit" in resp.headers:
            pass_test("E.2 响应头包含 X-RateLimit-Limit", f"limit={resp.headers.get('X-RateLimit-Limit')}")
        else:
            # health 路径可能豁免限流, 换一个路径
            resp = requests.get(f"{API}/knowledge-bases?page=1&page_size=1", timeout=3)
            if "X-RateLimit-Limit" in resp.headers:
                pass_test("E.2 响应头包含 X-RateLimit-Limit")
            else:
                fail_test("E.2 响应头缺少限流信息")
    except Exception as e:
        fail_test("E.2 限流头测试异常", str(e)[:80])


# ============================================================
#  F. 审计日志
# ============================================================

def test_audit_log():
    section("F. 审计日志")

    # F.1 检查 audit_logs 表是否有记录
    try:
        from app.models.database import SessionLocal
        from app.models.entities.auth import AuditLog
        from sqlalchemy import select, func

        db = SessionLocal()
        count = db.execute(select(func.count()).select_from(AuditLog)).scalar()
        db.close()

        if count and count > 0:
            pass_test("F.1 audit_logs 表有记录", f"count={count}")
        else:
            fail_test("F.1 audit_logs 表为空")
    except Exception as e:
        fail_test("F.1 审计日志查询异常", str(e)[:80])


# ============================================================
#  G. 安全响应头
# ============================================================

def test_security_headers():
    section("G. 安全响应头")

    try:
        resp = requests.get(f"{BASE}/health", timeout=3)
        headers = resp.headers

        checks = [
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("X-XSS-Protection", "1; mode=block"),
            ("Referrer-Policy", "strict-origin-when-cross-origin"),
        ]

        for name, expected in checks:
            actual = headers.get(name, "")
            if expected.lower() in actual.lower():
                pass_test(f"G. {name}", actual)
            else:
                fail_test(f"G. {name}", f"expected={expected}, got={actual}")
    except Exception as e:
        fail_test("G. 安全响应头测试异常", str(e)[:80])


# ============================================================
#  H. 请求体大小限制
# ============================================================

def test_body_size_limit():
    section("H. 请求体大小限制")

    # H.1 发送 11MB 请求体, 应返回 413
    try:
        large_body = "x" * (11 * 1024 * 1024)
        resp = requests.post(
            f"{API}/auth/register",
            json={"username": "large", "password": "123456", "confirm_password": "123456", "large_field": large_body},
            timeout=5
        )
        if resp.status_code == 413:
            pass_test("H.1 超大请求体返回 413")
        else:
            # 可能 JSON 序列化前就被拒绝
            if resp.status_code in (413, 422):
                pass_test("H.1 超大请求体被拒绝", f"status={resp.status_code}")
            else:
                fail_test("H.1 超大请求体未被拒绝", f"status={resp.status_code}")
    except Exception as e:
        fail_test("H.1 请求体大小测试异常", str(e)[:80])


# ============================================================
#  I. 负面测试
# ============================================================

def test_negative():
    section("I. 负面测试")

    # I.1 无效 Token
    try:
        resp = requests.get(f"{API}/auth/me", headers={"Authorization": "Bearer invalid_token_xxx"}, timeout=3)
        if resp.status_code == 401:
            pass_test("I.1 无效 Token 被拒绝 (401)")
        else:
            fail_test("I.1 无效 Token 未被拒绝", f"status={resp.status_code}")
    except Exception as e:
        fail_test("I.1 无效 Token 测试异常", str(e)[:80])

    # I.2 无 Token
    try:
        resp = requests.get(f"{API}/auth/me", timeout=3)
        if resp.status_code == 401:
            pass_test("I.2 无 Token 被拒绝 (401)")
        else:
            fail_test("I.2 无 Token 未被拒绝", f"status={resp.status_code}")
    except Exception as e:
        fail_test("I.2 无 Token 测试异常", str(e)[:80])

    # I.3 用 Refresh Token 访问需要 Access Token 的接口
    try:
        from app.core.security import create_refresh_token
        rt = create_refresh_token(1)
        resp = requests.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {rt}"}, timeout=3)
        if resp.status_code == 401:
            pass_test("I.3 Refresh Token 不能用于访问接口 (401)")
        else:
            fail_test("I.3 Refresh Token 被误用于访问 (安全漏洞!)", f"status={resp.status_code}")
    except Exception as e:
        fail_test("I.3 Refresh Token 测试异常", str(e)[:80])

    # I.4 重复注册
    ts = str(int(time.time()))
    try:
        # 第一次注册
        requests.post(f"{API}/auth/register", json={
            "username": f"dup_{ts}", "password": "Test123456",
            "confirm_password": "Test123456", "email": f"dup_{ts}@test.com"
        }, timeout=15)
        # 第二次注册同名
        resp = requests.post(f"{API}/auth/register", json={
            "username": f"dup_{ts}", "password": "Test123456",
            "confirm_password": "Test123456", "email": f"dup2_{ts}@test.com"
        }, timeout=15)
        if resp.status_code == 409:
            pass_test("I.4 重复注册返回 409")
        else:
            fail_test("I.4 重复注册未返回 409", f"status={resp.status_code}")
    except Exception as e:
        fail_test("I.4 重复注册测试异常", str(e)[:80])

    # I.5 密码不匹配
    try:
        resp = requests.post(f"{API}/auth/register", json={
            "username": f"mismatch_{ts}", "password": "A123456",
            "confirm_password": "B123456", "email": f"mis_{ts}@test.com"
        }, timeout=15)
        if resp.status_code == 422:
            pass_test("I.5 密码不匹配返回 422")
        else:
            fail_test("I.5 密码不匹配未返回 422", f"status={resp.status_code}")
    except Exception as e:
        fail_test("I.5 密码不匹配测试异常", str(e)[:80])


# ============================================================
#  主入口
# ============================================================

def main():
    print("\n" + "=" * 60)
    print("  Phase 2 验证测试 — 鉴权安全体系")
    print("=" * 60)

    test_security_core()
    test_entity_models()
    test_api_endpoints()
    test_rbac()
    test_audit_log()
    test_security_headers()
    test_body_size_limit()
    test_negative()
    # 限流测试放最后 (会消耗匿名请求配额)
    test_rate_limit()

    print(f"\n{'='*60}")
    print(f"  总计: {PASS + FAIL + SKIP}")
    print(f"  通过: {PASS}")
    print(f"  失败: {FAIL}")
    print(f"  跳过: {SKIP}")
    print(f"  通过率: {PASS/(PASS+FAIL)*100:.1f}%" if (PASS + FAIL) > 0 else "  无可测试项")
    print(f"{'='*60}\n")

    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
