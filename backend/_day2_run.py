import os
import sys

VENV_DIR = r"c:\Users\LEgion\Desktop\backend\RAG-PY\venv"
BACKEND_DIR = r"c:\Users\LEgion\Desktop\backend\RAG-PY\backend"

# 关键：把 venv 的 site-packages 加到路径，这样即使是系统 Python 也能加载包
venv_site = os.path.join(VENV_DIR, "Lib", "site-packages")
if os.path.isdir(venv_site) and venv_site not in sys.path:
    sys.path.insert(0, venv_site)

os.chdir(BACKEND_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

results = []

def log(msg):
    print(msg)
    sys.stdout.flush()

def check(desc, condition):
    results.append((desc, condition))
    status = "✓" if condition else "✗"
    log("  %s %s" % (status, desc))

log("\n=== Day 2 完整功能验证 (使用 venv 包) ===")
log("  Python: %s" % sys.executable)
log("  venv site-packages: %s" % venv_site)

# 1. 导入测试
log("\n[导入测试]")
modules_to_test = [
    ("sqlalchemy", "SQLAlchemy ORM"),
    ("fastapi", "FastAPI"),
    ("pydantic", "Pydantic"),
    ("httpx", "HTTPX (测试客户端)"),
    ("aiofiles", "aiofiles (异步文件)"),
]

for module, desc in modules_to_test:
    try:
        __import__(module)
        check("%s (%s)" % (desc, module), True)
    except Exception as e:
        check("%s - %s" % (desc, str(e)[:40]), False)

try:
    from app.core.config import settings
    check("config 模块", True)
except Exception as e:
    check("config 模块 - %s" % str(e)[:60], False)

try:
    from app.core.security import hash_password, verify_password, create_jwt_token, decode_jwt_token
    check("security 模块", True)
except Exception as e:
    check("security 模块 - %s" % str(e)[:60], False)

try:
    from app.models.database import engine, Base, init_db
    check("database 模块", True)
except Exception as e:
    check("database 模块 - %s" % str(e)[:60], False)

try:
    from app.models.response import ApiResponse
    check("response 模块", True)
except Exception as e:
    check("response 模块 - %s" % str(e)[:60], False)

try:
    from app.models.entities.user import User
    check("User 实体", True)
except Exception as e:
    check("User 实体 - %s" % str(e)[:60], False)

try:
    from app.models.entities.knowledge_base import KnowledgeBase
    check("KnowledgeBase 实体", True)
except Exception as e:
    check("KnowledgeBase 实体 - %s" % str(e)[:60], False)

try:
    from app.models.entities.document import Document, Chunk
    check("Document & Chunk 实体", True)
except Exception as e:
    check("Document/Chunk 实体 - %s" % str(e)[:60], False)

try:
    from app.models.entities.conversation import Conversation, Message
    check("Conversation & Message 实体", True)
except Exception as e:
    check("Conversation/Message 实体 - %s" % str(e)[:60], False)

try:
    from app.models.schemas import UserRegister, UserLogin, UserInfo, TokenData
    check("用户 Schema", True)
except Exception as e:
    check("用户 Schema - %s" % str(e)[:60], False)

try:
    from app.models.schemas import KnowledgeBaseCreate, KnowledgeBaseUpdate, KnowledgeBaseInfo, KnowledgeBaseListResponse
    check("知识库 Schema", True)
except Exception as e:
    check("知识库 Schema - %s" % str(e)[:60], False)

try:
    from app.api.dependencies import get_current_user, get_db_dep
    check("API 依赖注入", True)
except Exception as e:
    check("API 依赖注入 - %s" % str(e)[:60], False)

try:
    from app.api.v1 import api_router
    check("API v1 路由", True)
except Exception as e:
    check("API v1 路由 - %s" % str(e)[:60], False)

try:
    from app.main import app
    check("FastAPI 应用", True)
except Exception as e:
    check("FastAPI 应用 - %s" % str(e)[:60], False)

# 2. 安全模块功能测试
log("\n[安全模块功能测试]")
try:
    hashed = hash_password("test_password_day2")
    check("密码哈希生成 (长度=%d)" % len(hashed), len(hashed) > 20)
    check("正确密码验证通过", verify_password("test_password_day2", hashed))
    check("错误密码验证失败", not verify_password("wrong", hashed))

    token = create_jwt_token({"sub": "42", "username": "tester"})
    check("JWT Token 生成", len(token) > 30 and token.count(".") == 2)
    decoded = decode_jwt_token(token)
    check("JWT Token 解码", decoded is not None and decoded["sub"] == "42")
    check("篡改 Token 被拒绝", decode_jwt_token(token + "garbage") is None)
    check("错误密钥被拒绝", decode_jwt_token(token, secret="wrong-secret") is None)
except Exception as e:
    import traceback
    check("安全模块异常 - %s" % str(e)[:80], False)
    log("  %s" % traceback.format_exc()[:200])

# 3. API 测试
log("\n[API 接口功能测试]")
try:
    from fastapi.testclient import TestClient
    check("TestClient 可用", True)

    # 初始化数据库（全新）
    try:
        DB_PATH = os.path.join(BACKEND_DIR, "data", "rag_system.db")
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)
        init_db()
        check("数据库初始化成功", True)
    except Exception as e:
        check("数据库初始化 - %s" % str(e)[:60], False)

    client = TestClient(app)

    r = client.get("/health")
    check("GET /health 返回 200", r.status_code == 200)

    # === 用户注册 ===
    r = client.post("/v1/auth/register", json={
        "username": "day2_tester",
        "password": "day2_pass_123",
        "confirm_password": "day2_pass_123",
        "email": "day2@test.com",
    })
    check("POST /v1/auth/register (注册)", r.status_code == 200)
    token_str = ""
    if r.status_code == 200:
        token_str = r.json()["data"]["access_token"]
        log("    Token: %s..." % token_str[:40])
    else:
        log("    Response: %s" % r.text[:300])

    r = client.post("/v1/auth/register", json={
        "username": "day2_tester",
        "password": "another",
        "confirm_password": "another",
    })
    check("重复注册返回 409", r.status_code == 409)

    # === 用户登录 ===
    r = client.post("/v1/auth/login", json={
        "username": "day2_tester",
        "password": "day2_pass_123",
    })
    check("POST /v1/auth/login (登录)", r.status_code == 200)

    r = client.post("/v1/auth/login", json={
        "username": "day2_tester",
        "password": "wrong_pass",
    })
    check("错误密码返回 401", r.status_code == 401)

    # === 当前用户 ===
    r = client.get("/v1/auth/me", headers={"Authorization": "Bearer " + token_str})
    check("GET /v1/auth/me (当前用户)", r.status_code == 200)

    r = client.get("/v1/auth/me")
    check("未登录访问 /me 返回 401", r.status_code == 401)

    # === 创建知识库 ===
    r = client.post("/v1/knowledge-bases", json={
        "name": "Day2 测试知识库",
        "description": "测试描述内容",
        "chunk_size": 500,
        "chunk_overlap": 50,
        "is_public": False,
    }, headers={"Authorization": "Bearer " + token_str})
    check("POST /v1/knowledge-bases (创建)", r.status_code == 200)
    kb_id = 0
    if r.status_code == 200:
        kb_id = r.json()["data"]["id"]
        log("    KB ID: %d" % kb_id)
    else:
        log("    Response: %s" % r.text[:300])

    r = client.post("/v1/knowledge-bases", json={
        "name": "Day2 公开知识库",
        "description": "公开测试",
        "chunk_size": 500,
        "chunk_overlap": 50,
        "is_public": True,
    }, headers={"Authorization": "Bearer " + token_str})
    check("创建第二个知识库 (公开)", r.status_code == 200)
    kb_id2 = r.json()["data"]["id"] if r.status_code == 200 else 0

    r = client.post("/v1/knowledge-bases", json={"name": "未登录"})
    check("未登录创建知识库返回 401", r.status_code == 401)

    # === 知识库列表 ===
    r = client.get("/v1/knowledge-bases", headers={"Authorization": "Bearer " + token_str})
    check("GET /v1/knowledge-bases (列表)", r.status_code == 200)
    if r.status_code == 200:
        check("列表包含 >= 2 个", r.json()["data"]["total"] >= 2)

    r = client.get("/v1/knowledge-bases")
    check("未登录获取列表返回 200", r.status_code == 200)
    if r.status_code == 200:
        items = r.json()["data"]["items"]
        all_public = all(it.get("is_public") is True for it in items)
        check("未登录仅能看到公开知识库", len(items) == 0 or all_public)
        log("    未登录可见: %d 个" % len(items))

    # === 知识库详情 ===
    if kb_id > 0:
        r = client.get("/v1/knowledge-bases/%d" % kb_id,
                       headers={"Authorization": "Bearer " + token_str})
        check("GET /v1/knowledge-bases/{id} (详情)", r.status_code == 200)

        # === 更新知识库 ===
        r = client.put("/v1/knowledge-bases/%d" % kb_id, json={
            "name": "Day2 更新后的知识库",
            "description": "新描述内容",
            "is_public": True,
        }, headers={"Authorization": "Bearer " + token_str})
        check("PUT /v1/knowledge-bases/{id} (更新)", r.status_code == 200)

        # === 搜索知识库 ===
        r = client.get("/v1/knowledge-bases?keyword=更新",
                       headers={"Authorization": "Bearer " + token_str})
        check("GET /v1/knowledge-bases?keyword= (搜索)", r.status_code == 200)

        # === 删除知识库 ===
        r = client.delete("/v1/knowledge-bases/%d" % kb_id,
                          headers={"Authorization": "Bearer " + token_str})
        check("DELETE /v1/knowledge-bases/{id} (删除)", r.status_code == 200)

        r = client.get("/v1/knowledge-bases/%d" % kb_id,
                       headers={"Authorization": "Bearer " + token_str})
        check("删除后返回 404", r.status_code == 404)

    # === 权限控制测试 ===
    r = client.post("/v1/auth/register", json={
        "username": "day2_bob",
        "password": "bob_pass_123",
        "confirm_password": "bob_pass_123",
        "email": "bob@test.com",
    })
    bob_token = r.json()["data"]["access_token"] if r.status_code == 200 else ""

    if bob_token and kb_id2 > 0:
        # Bob 尝试修改 Alice 的知识库
        r = client.put("/v1/knowledge-bases/%d" % kb_id2, json={
            "name": "Bob 恶意修改",
        }, headers={"Authorization": "Bearer " + bob_token})
        check("Bob 无法修改 Alice 的 KB (404)", r.status_code == 404)

        # Bob 尝试删除 Alice 的知识库
        r = client.delete("/v1/knowledge-bases/%d" % kb_id2,
                          headers={"Authorization": "Bearer " + bob_token})
        check("Bob 无法删除 Alice 的 KB (404)", r.status_code == 404)

    # === 边界测试 ===
    r = client.get("/v1/knowledge-bases/99999",
                   headers={"Authorization": "Bearer " + token_str})
    check("GET 不存在的知识库返回 404", r.status_code == 404)

    r = client.get("/v1/auth/me", headers={"Authorization": "Bearer invalid.token.string"})
    check("无效 Token 返回 401", r.status_code == 401)

except Exception as e:
    import traceback
    check("TestClient 异常 - %s" % str(e)[:80], False)
    log("  Stack: %s" % traceback.format_exc()[:500])

# 4. 总结
log("\n" + "=" * 60)
passed = sum(1 for _, ok in results if ok)
total = len(results)
log("总结: %d / %d 通过" % (passed, total))
if passed == total:
    log("🎉 Day 2 所有测试通过!")
else:
    log("❌ 有 %d 个测试失败" % (total - passed))
log("=" * 60)

log("\n" + "=" * 60)
log("📋 Day 2 完整交付清单")
log("=" * 60)
log("")
log("1. 密码哈希系统 (app/core/security.py)")
log("   - PBKDF2-HMAC, 使用 Python 标准库 hashlib")
log("   - 无需第三方依赖 (passlib/bcrypt)")
log("")
log("2. JWT Token 系统 (app/core/security.py)")
log("   - 自研实现: hmac + base64url")
log("   - 签名验证 + 过期检查")
log("   - 无需第三方依赖 (PyJWT)")
log("")
log("3. 用户数据库表 (app/models/entities/user.py)")
log("   - id, username, email, password_hash")
log("   - is_active, is_admin, created_at, updated_at")
log("")
log("4. 知识库数据库表 (app/models/entities/knowledge_base.py)")
log("   - id, name, description, user_id")
log("   - embedding_model, chunk_size, chunk_overlap")
log("   - is_public, status, total_documents, total_chunks")
log("")
log("5. 文档与对话表 (document.py, conversation.py)")
log("   - Document: 文件名、路径、内容、分块数")
log("   - Chunk: 文档分块、向量索引")
log("   - Conversation: 对话会话")
log("   - Message: 单条消息")
log("")
log("6. 用户 Schema (app/models/schemas/user_schemas.py)")
log("   - UserRegister: 用户名/邮箱/密码/确认密码")
log("   - UserLogin: 用户名/密码")
log("   - UserInfo: 返回用户信息")
log("   - TokenData: 返回 access_token + user")
log("")
log("7. 知识库 Schema (app/models/schemas/kb_schemas.py)")
log("   - KnowledgeBaseCreate: 创建请求体")
log("   - KnowledgeBaseUpdate: 更新请求体")
log("   - KnowledgeBaseInfo: 返回知识库信息")
log("   - KnowledgeBaseListResponse: 分页列表 + 总数")
log("")
log("8. 统一响应格式 (app/models/response.py)")
log("   - ApiResponse[T]: success, code, message, data, timestamp")
log("   - 所有 API 接口统一使用此格式")
log("")
log("9. API 依赖注入 (app/api/dependencies.py)")
log("   - get_db_dep: 数据库会话")
log("   - get_current_user: 当前登录用户 (必需)")
log("   - get_current_user_optional: 当前用户 (可选)")
log("   - HTTP Bearer Token 鉴权")
log("")
log("10. 认证路由 (app/api/v1/auth.py)")
log("    POST /v1/auth/register   - 用户注册")
log("    POST /v1/auth/login      - 用户登录")
log("    GET  /v1/auth/me         - 当前用户信息")
log("")
log("11. 知识库完整 CRUD (app/api/v1/knowledge_base.py)")
log("    POST   /v1/knowledge-bases         - 创建 (需登录)")
log("    GET    /v1/knowledge-bases         - 列表 (分页+搜索)")
log("    GET    /v1/knowledge-bases/{id}    - 详情")
log("    PUT    /v1/knowledge-bases/{id}    - 更新 (仅所有者)")
log("    DELETE /v1/knowledge-bases/{id}    - 删除 (仅所有者)")
log("")
log("12. 知识库业务服务 (app/services/kb_service.py)")
log("    - create, get_by_id, list, update, delete")
log("    - 权限检查封装在 service 层")
log("")
log("13. 权限控制")
log("    - 未登录: 仅可查看公开知识库")
log("    - 已登录: 自己的 + 公开的知识库")
log("    - 修改/删除: 仅所有者可以操作")
log("")
log("🚀 启动服务器:")
log("   cd c:\\Users\\LEgion\\Desktop\\backend\\RAG-PY\\backend")
log("   python start.py")
log("")
log("📖 访问交互式 API 文档:")
log("   http://127.0.0.1:8000/docs    - Swagger UI")
log("   http://127.0.0.1:8000/redoc   - ReDoc")
log("")
log("📂 运行完整测试脚本:")
log("   双击运行: backend\\run_day2.bat")
log("   或命令行: cd backend && python _day2_run.py")
log("=" * 60)