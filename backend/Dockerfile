# syntax=docker/dockerfile:1
# rebuild backend — multi-stage build.
# Stage 1 installs deps with uv into a venv; stage 2 is a slim runtime with the
# system tools the integration layer shells out to (git, ssh).

# ── Stage 1: build deps ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

# uv: fast, reproducible installs from uv.lock
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
# Install dependencies first (cached layer) using only the lock + manifest.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# ── Stage 2: runtime ─────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# git + openssh-client: the Git / Remote integration shells out to these.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user.
RUN useradd --create-home --uid 10001 appuser

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    REBUILD_DATA_DIR=/app/.data \
    REBUILD_DATABASE_URL=sqlite:////app/.data/rebuild.db

WORKDIR /app
COPY . /app

# Data dir (sqlite db, execution sandbox, repos) lives on a mounted volume.
RUN mkdir -p /app/.data && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
