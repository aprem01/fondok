# syntax=docker/dockerfile:1.7

# stage 1: build with uv
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

# The schemas package is a path dep; copy it before resolving.
COPY packages/schemas-py /workspace/packages/schemas-py

WORKDIR /workspace/apps/worker

COPY apps/worker/pyproject.toml apps/worker/uv.lock* ./

RUN uv sync --frozen --no-install-project --no-dev || \
    uv sync --no-install-project --no-dev

COPY apps/worker/app ./app

RUN uv sync --no-dev

# stage 2: minimal runtime
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/workspace/apps/worker/.venv/bin:$PATH"

RUN useradd -m -u 1000 fondok

COPY --from=builder /workspace /workspace
WORKDIR /workspace/apps/worker

USER fondok
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info"]
