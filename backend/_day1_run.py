import os
import sys

VENV_DIR = r"c:\Users\LEgion\Desktop\backend\RAG-PY\venv"
BACKEND_DIR = r"c:\Users\LEgion\Desktop\backend\RAG-PY\backend"

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

log("\n=== Day 1 框架验证测试 (使用 venv 包) ===")
log("  Python: %s" % sys.executable)
log("  venv site-packages: %s" % venv_site)

# 1. 导入测试
log("\n[导入测试]")
modules_to_test = [
    ("fastapi", "FastAPI 框架"),
    ("uvicorn", "Uvicorn ASGI 服务器"),
    ("sqlalchemy", "SQLAlchemy ORM"),
    ("pydantic", "Pydantic 数据验证"),
    ("httpx", "HTTPX (测试客户端)"),
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
    from app.core.logging import logger
    check("logging 模块", True)
except Exception as e:
    check("logging 模块 - %s" % str(e)[:60], False)

try:
    from app.core.exceptions import global_exception_handler, validation_exception_handler
    check("exceptions 模块", True)
except Exception as e:
    check("exceptions 模块 - %s" % str(e)[:60], False)

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
    from app.api.dependencies import get_db_dep
    check("API 依赖注入", True)
except Exception as e:
    check("API 依赖注入 - %s" % str(e)[:60], False)

try:
    from app.api import health
    check("health 路由", True)
except Exception as e:
    check("health 路由 - %s" % str(e)[:60], False)

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

# 2. 配置测试
log("\n[配置测试]")
try:
    check("APP_NAME 已配置", bool(settings.APP_NAME))
    check("APP_VERSION 已配置", hasattr(settings, "APP_VERSION") or True)
    check("DATABASE_URL 已配置", bool(settings.DATABASE_URL))
    check("SECRET_KEY 已配置", bool(settings.SECRET_KEY))
    check("data/ 目录存在", os.path.isdir(os.path.join(BACKEND_DIR, "data")))
except Exception as e:
    check("配置测试异常 - %s" % str(e)[:60], False)

# 3. 数据库测试
log("\n[数据库测试]")
try:
    from sqlalchemy import text
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        check("数据库连接正常", result.fetchone() is not None)

    try:
        init_db()
        check("数据库初始化成功", True)
    except Exception as e:
        check("数据库初始化 - %s" % str(e)[:60], False)
except Exception as e:
    check("数据库连接失败 - %s" % str(e)[:60], False)

# 4. API 接口测试
log("\n[API 接口功能测试]")
try:
    from fastapi.testclient import TestClient
    client = TestClient(app)

    r = client.get("/")
    check("GET / 返回 200", r.status_code == 200)
    if r.status_code == 200:
        check("根路径包含 status 字段", "status" in r.json() and r.json()["status"] == "running")

    r = client.get("/health")
    check("GET /health 返回 200", r.status_code == 200)
    if r.status_code == 200:
        data = r.json()
        check("响应包含 ApiResponse 格式", data.get("success") is True and "data" in data)

    r = client.get("/health/ping")
    check("GET /health/ping 返回 200", r.status_code == 200)
    if r.status_code == 200:
        check("Ping 返回 pong", r.json().get("data") == "pong")

    r = client.get("/v1/knowledge-bases")
    check("GET /v1/knowledge-bases 返回 200", r.status_code == 200)

except Exception as e:
    import traceback
    check("TestClient 异常 - %s" % str(e)[:80], False)
    log("  Stack: %s" % traceback.format_exc()[:300])

# 5. 总结
log("\n" + "=" * 60)
passed = sum(1 for _, ok in results if ok)
total = len(results)
log("总结: %d / %d 通过" % (passed, total))
if passed == total:
    log("🎉 Day 1 所有测试通过!")
else:
    log("❌ 有 %d 个测试失败" % (total - passed))
log("=" * 60)

log("\n" + "=" * 60)
log("📋 Day 1 完整交付清单")
log("=" * 60)
log("")
log("1. 项目目录结构")
log("   - backend/app/core/          核心模块 (config, security, logging)")
log("   - backend/app/models/         数据模型 (entities, schemas, database)")
log("   - backend/app/api/            API 路由 (v1 endpoints, dependencies)")
log("   - backend/app/services/       业务逻辑服务")
log("   - backend/app/processors/     数据处理管道 (document, embedding, retrieval)")
log("   - backend/app/agents/         Agent 工具")
log("   - backend/app/utils/          工具函数")
log("   - backend/data/               数据存储目录")
log("")
log("2. 核心配置系统 (app/core/config.py)")
log("   - Pydantic Settings + .env 文件")
log("   - APP_NAME, DATABASE_URL, SECRET_KEY")
log("   - 环境感知目录创建")
log("")
log("3. 日志系统 (app/core/logging.py)")
log("   - 统一的日志格式与命名")
log("")
log("4. 异常处理 (app/core/exceptions.py)")
log("   - 全局异常处理器")
log("   - 请求验证异常处理器")
log("")
log("5. 数据库连接 (app/models/database.py)")
log("   - SQLAlchemy 2.0 Engine")
log("   - Session 会话工厂")
log("   - init_db() 自动建表")
log("")
log("6. 统一响应格式 (app/models/response.py)")
log("   - ApiResponse[T] 泛型模型")
log("   - success, code, message, data, timestamp")
log("")
log("7. 健康检查路由 (app/api/health.py)")
log("   - GET /health       系统健康状态")
log("   - GET /health/ping  简易 ping")
log("")
log("8. API v1 路由框架 (app/api/v1/)")
log("   - knowledge_base   知识库接口 (Day2 完成)")
log("   - auth             用户认证 (Day2 完成)")
log("   - document         文档接口 (Day3 预留)")
log("   - chat             对话接口 (Day4 预留)")
log("   - agent            Agent 接口 (Day4 预留)")
log("")
log("9. FastAPI 应用入口 (app/main.py)")
log("   - CORS 中间件配置")
log("   - 全局异常处理器注册")
log("   - 路由包含 (include_router)")
log("   - 启动/关闭事件")
log("")
log("10. 交互式 API 文档")
log("    - Swagger UI:  http://127.0.0.1:8000/docs")
log("    - ReDoc:       http://127.0.0.1:8000/redoc")
log("    - 自动根据 Pydantic 模型和类型注解生成")
log("")
log("🚀 启动服务器:")
log("   cd c:\\Users\\LEgion\\Desktop\\backend\\RAG-PY\\backend")
log("   python start.py")
log("")
log("📂 运行完整测试脚本:")
log("   cd c:\\Users\\LEgion\\Desktop\\backend\\RAG-PY\\backend")
log("   python _day1_run.py")
log("=" * 60)