#!/usr/bin/env python3
"""
Test Platform - 测试用例脚手架生成器
用法:
    python generate_case_template.py --module kb_service --type unit
    python generate_case_template.py --module chat_api   --type api
    python generate_case_template.py --module rag_flow   --type integration
生成的文件会写到 backend/tests/<type>/test_<module>.py
"""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATES = ROOT / "test-platform" / "templates"

HEADER = '''\
"""
{type_title}测试: {module}
生成时间: {date}
设计原则: 先 AAA(Arrange-Act-Assert)，再 Given-When-Then
"""
import pytest
'''

UNIT_IMPORTS = '''\
# 如需 DB 夹具：from tests.conftest import db_session  # noqa: F401 (pytest 自动注入)
'''

API_IMPORTS = '''\
# api_client 是 httpx.AsyncClient(ASGITransport)，真中间件 + 假 TCP
# auth headers: test_user.auth_headers = {"Authorization": "Bearer <token>"}
'''

INTEGRATION_IMPORTS = '''\
# 集成测试：夹具可组合 db_session + api_client + test_kb + test_document
'''

BODY = '''\

pytestmark = pytest.mark.asyncio  # 也可在 pytest.ini 开 asyncio_mode=auto 省略本行


class Test{module_camel}:
    """测试套件: {module}"""

    async def test_{module}_sanity_check_always_pass(self):
        """sanity：确保这个文件能被 pytest 收集和执行"""
        assert 1 + 1 == 2

    # TODO: 按 AAA 模式补充真实用例
    # Arrange  -> 准备数据 / Mock 依赖
    # Act      -> 调用被测函数 / API
    # Assert   -> 断言返回值 + 副作用（DB行、Redis key、日志）
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--module", required=True, help="模块名，如 kb_service / chat_api")
    ap.add_argument("--type", required=True, choices=["unit", "api", "integration"])
    args = ap.parse_args()

    from datetime import datetime
    type_title = {"unit": "单元", "api": "HTTP API 契约", "integration": "集成"}[args.type]
    module_camel = args.module.replace("_", " ").title().replace(" ", "")

    content = HEADER.format(type_title=type_title, module=args.module,
                            date=datetime.now().strftime("%Y-%m-%d"))
    content += {"unit": UNIT_IMPORTS, "api": API_IMPORTS,
                "integration": INTEGRATION_IMPORTS}[args.type]
    content += BODY.format(module_camel=module_camel, module=args.module)

    out = ROOT / "backend" / "tests" / args.type / f"test_{args.module}.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        print(f"[SKIP] 已存在: {out.relative_to(ROOT)}")
        return
    out.write_text(content, encoding="utf-8")
    print(f"[OK]   生成: {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
