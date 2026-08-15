# ============================================================
#  Dockerfile — 多阶段构建
#
# 设计:
#   Builder 阶段: 安装依赖到 /root/.local (不污染系统 Python)
#   Runtime 阶段: 仅复制安装好的依赖 + 应用代码
#
# 优势:
#   - 镜像体积小 (~200MB vs 全量 ~500MB)
#   - 构建缓存: requirements.txt 不变时跳过 pip install
#   - 安全: runtime 阶段无编译工具
# ============================================================

# ---- Builder 阶段 ----
FROM python:3.11-slim AS builder

WORKDIR /build

# 安装编译依赖 (argon2-cffi, asyncpg 等需要)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# ---- Runtime 阶段 ----
FROM python:3.11-slim

WORKDIR /app

# 安装运行时依赖 (libpq5 for asyncpg, libffi7 for argon2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libffi7 curl \
    && rm -rf /var/lib/apt/lists/*

# 从 builder 复制安装好的 Python 包
COPY --from=builder /root/.local /root/.local

# 复制应用代码
COPY . .

# 环境变量
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 暴露端口
EXPOSE 8000

# 健康检查 (Phase 5 liveness 探针)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD curl -f http://localhost:8000/health/liveness || exit 1

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
