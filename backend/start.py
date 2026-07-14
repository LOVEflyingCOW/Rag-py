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

from app.main import app
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level="info",
        reload=False
    )