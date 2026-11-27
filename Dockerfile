# ===== 多阶段构建：multi-agent-code-system =====
# builder 阶段：安装依赖，编译 wheel
# runtime 阶段：精简镜像，只携带运行时必需文件

# ------------------------------------------------------------------ #
#  Builder
# ------------------------------------------------------------------ #
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# 先拷贝依赖描述文件，利用 Docker 层缓存
COPY pyproject.toml requirements.txt ./

# 安装构建工具，把项目依赖装到 /install（便于后续拷贝到 runtime 镜像）
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --upgrade pip \
    && pip install --prefix=/install -r requirements.txt

# ------------------------------------------------------------------ #
#  Runtime
# ------------------------------------------------------------------ #
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=prod \
    APP_PORT=8001 \
    DB_PATH=/app/data/app.db \
    CHROMA_PERSIST_DIR=/app/data/chroma \
    HOME=/home/appuser

WORKDIR /app

# 从 builder 拷贝已安装的依赖
COPY --from=builder /install /usr/local

# 拷贝应用代码（app/ 服务本体，web/ dashboard 与 landing page，scripts/ 运维入口，
# src/ golden 用例夹具）
COPY app/ ./app/
COPY web/ ./web/
COPY scripts/ ./scripts/
COPY src/ ./src/

# 数据持久化目录（SQLite + ChromaDB）+ 非 root 运行用户
RUN mkdir -p /app/data \
    && useradd --create-home --uid 10001 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8001

# 健康检查：命中 FastAPI 的 /api/v1/health
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8001/api/v1/health', timeout=3).status == 200 else sys.exit(1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
