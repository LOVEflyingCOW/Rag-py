import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

sys.stderr.write("Starting framework verification...\n")
sys.stderr.flush()

try:
    from app.core.config import settings
    sys.stderr.write("OK: Config loaded - " + str(settings.APP_NAME) + "\n")
    sys.stderr.flush()
except Exception as e:
    sys.stderr.write("FAIL: Config - " + str(e) + "\n")
    sys.exit(1)

try:
    from app.core.exceptions import RAGBaseException, NotFoundError
    sys.stderr.write("OK: Exceptions module loaded\n")
    sys.stderr.flush()
except Exception as e:
    sys.stderr.write("FAIL: Exceptions - " + str(e) + "\n")
    sys.exit(1)

try:
    from app.models.database import engine, Base
    sys.stderr.write("OK: Database engine created - " + str(engine.url) + "\n")
    sys.stderr.flush()
except Exception as e:
    sys.stderr.write("FAIL: Database - " + str(e) + "\n")
    sys.exit(1)

try:
    from app.main import app
    sys.stderr.write("OK: FastAPI app created - " + str(len(app.routes)) + " routes\n")
    sys.stderr.flush()
except Exception as e:
    sys.stderr.write("FAIL: FastAPI - " + str(e) + "\n")
    import traceback
    sys.stderr.write(traceback.format_exc())
    sys.exit(1)

try:
    from fastapi.testclient import TestClient
    client = TestClient(app)

    routes_to_test = [
        "/",
        "/health",
        "/health/ping",
        "/v1/knowledge-bases/",
        "/v1/documents/",
        "/v1/chat/",
        "/v1/agent/",
    ]

    for route in routes_to_test:
        r = client.get(route)
        status = "OK" if r.status_code == 200 else "FAIL"
        sys.stderr.write(status + ": GET " + route + " -> " + str(r.status_code) + "\n")
        sys.stderr.flush()

except Exception as e:
    sys.stderr.write("FAIL: Route tests - " + str(e) + "\n")
    import traceback
    sys.stderr.write(traceback.format_exc())
    sys.exit(1)

sys.stderr.write("\n")
sys.stderr.write("=" * 50 + "\n")
sys.stderr.write("DAY 1 FRAMEWORK VERIFIED SUCCESSFULLY!\n")
sys.stderr.write("=" * 50 + "\n")
sys.stderr.flush()