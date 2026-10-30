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

# The USALI rules catalog lives at evals/golden-set/usali-rules.csv and
# is resolved at runtime via Path(__file__).resolve().parents[3] /
# "evals" / "golden-set" / "usali-rules.csv". Without it,
# load_usali_rules() returns [] silently and every doc scores
# applicable=0 / score=null (Sam QA 2026-06-29: structural recognizer
# fires correctly but USALI score was always null on prod because the
# image never carried the rules CSV).
COPY evals /workspace/evals

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

# Which build is serving (go-live audit D.2, 2026-10-30). ``GET /health``
# reports ``build_sha`` from RAILWAY_GIT_COMMIT_SHA (Railway injects it at
# runtime — the primary path, nothing to pass) and falls back to the value
# baked here for a build made anywhere else:
#   docker build --build-arg GIT_SHA=$(git rev-parse HEAD) \
#                --build-arg BUILD_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ) ...
# An ARG that was not passed bakes an empty ENV, which /health reads as null.
# BUILD_TIME is also written to a file at image build so a Railway build,
# which passes no args, still reports when the image was made.
ARG GIT_SHA=""
ARG BUILD_TIME=""
ENV FONDOK_BUILD_SHA=${GIT_SHA} \
    FONDOK_BUILD_TIME=${BUILD_TIME}
RUN date -u +%Y-%m-%dT%H:%M:%SZ > /workspace/BUILD_TIME

USER fondok
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info"]
