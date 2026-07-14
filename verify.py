from __future__ import annotations

import os
import sys

BACKEND_DIR = r'c:\Users\LEgion\Desktop\backend\RAG-PY\backend'
OUTPUT_FILE = r'c:\Users\LEgion\Desktop\backend\RAG-PY\TEST_RESULTS.txt'

sys.path.insert(0, BACKEND_DIR)

results = []
results.append("=== Day 1 Framework Verification ===")
results.append("Python: " + sys.version.split("\n")[0])
results.append("Backend dir: " + BACKEND_DIR)
results.append("")

try:
    from app.core.config import settings
    results.append("[PASS] Config loaded - APP_NAME=" + str(settings.APP_NAME))
    results.append("       DATABASE_URL=" + str(settings.DATABASE_URL))
except Exception as e:
    results.append("[FAIL] Config: " + str(e))

try:
    from app.core.exceptions import RAGBaseException, NotFoundError
    results.append("[PASS] Exceptions module loaded")
except Exception as e:
    results.append("[FAIL] Exceptions: " + str(e))

try:
    from app.models.database import engine, Base
    results.append("[PASS] Database engine created: " + str(engine.url))
except Exception as e:
    results.append("[FAIL] Database: " + str(e))

try:
    from fastapi.testclient import TestClient
    from app.main import app
    results.append("[PASS] FastAPI app created with " + str(len(app.routes)) + " routes")
    client = TestClient(app)

    tests = [
        ("/", 200),
        ("/health", 200),
        ("/health/ping", 200),
        ("/v1/knowledge-bases/", 200),
        ("/v1/documents/", 200),
        ("/v1/chat/", 200),
        ("/v1/agent/", 200),
    ]

    for path, expected in tests:
        r = client.get(path)
        if r.status_code == expected:
            results.append("[PASS] GET " + path + " -> " + str(r.status_code))
        else:
            results.append("[FAIL] GET " + path + " -> " + str(r.status_code) + " (expected " + str(expected) + ")")

except Exception as e:
    results.append("[FAIL] FastAPI: " + str(e))
    import traceback
    results.append("       Traceback: " + traceback.format_exc())

results.append("")
results.append("=== Summary ===")
passed = sum(1 for r in results if "[PASS]" in r)
failed = sum(1 for r in results if "[FAIL]" in r)
results.append("Total: " + str(passed + failed) + " | Passed: " + str(passed) + " | Failed: " + str(failed))
if failed == 0:
    results.append("DAY 1 FRAMEWORK VERIFIED SUCCESSFULLY!")

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(results))

print("DONE. Results written to: " + OUTPUT_FILE)
print("\n".join(results[-5:]))