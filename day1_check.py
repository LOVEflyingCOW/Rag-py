import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

log_lines = []
log_lines.append("Day 1 Framework Verification")
log_lines.append("Python: " + sys.version.split("\n")[0])
log_lines.append("Backend path: " + BACKEND)
log_lines.append("")

try:
    from app.core.config import settings
    log_lines.append("[OK] Config loaded: " + str(settings.APP_NAME))
except Exception as e:
    log_lines.append("[FAIL] Config: " + str(e))

try:
    from app.core.exceptions import RAGBaseException, NotFoundError
    log_lines.append("[OK] Exceptions loaded")
except Exception as e:
    log_lines.append("[FAIL] Exceptions: " + str(e))

try:
    from app.models.database import engine, Base
    log_lines.append("[OK] Database: " + str(engine.url))
except Exception as e:
    log_lines.append("[FAIL] Database: " + str(e))

try:
    from app.main import app
    log_lines.append("[OK] FastAPI: " + str(len(app.routes)) + " routes")
except Exception as e:
    log_lines.append("[FAIL] FastAPI: " + str(e))
    import traceback
    log_lines.append(traceback.format_exc())

try:
    from fastapi.testclient import TestClient
    client = TestClient(app)
    for route in ["/", "/health", "/health/ping",
                  "/v1/knowledge-bases/", "/v1/documents/",
                  "/v1/chat/", "/v1/agent/"]:
        r = client.get(route)
        status = "[OK]" if r.status_code == 200 else "[FAIL]"
        log_lines.append("%s GET %s -> %d" % (status, route, r.status_code))
except Exception as e:
    log_lines.append("[FAIL] Routes: " + str(e))

log_lines.append("")
passed = sum(1 for l in log_lines if "[OK]" in l)
failed = sum(1 for l in log_lines if "[FAIL]" in l)
log_lines.append("Summary: %d passed, %d failed" % (passed, failed))
if failed == 0:
    log_lines.append("ALL TESTS PASSED - DAY 1 FRAMEWORK COMPLETE!")

with open(os.path.join(ROOT, "day1_summary.txt"), "w") as f:
    f.write("\n".join(log_lines))

# Also write to data directory
data_dir = os.path.join(BACKEND, "data")
os.makedirs(data_dir, exist_ok=True)
with open(os.path.join(data_dir, "day1_summary.txt"), "w") as f:
    f.write("\n".join(log_lines))

sys.exit(0)