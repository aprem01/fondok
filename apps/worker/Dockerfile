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

# WeasyPrint native deps — required for IC memo PDF rendering.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 fondok

COPY --from=builder /workspace /workspace
WORKDIR /workspace/apps/worker

USER fondok
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info"]
