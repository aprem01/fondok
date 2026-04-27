# Fondok Worker

FastAPI + LangGraph runtime for the Fondok hotel-underwriting agent platform.

## Quickstart

```bash
cd apps/worker
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Health check: `GET http://localhost:8000/health`

## Layout

- `app/main.py` — FastAPI factory, lifespan (telemetry + migrations), CORS, routers.
- `app/config.py` — `Settings` loaded from env / `.env`.
- `app/database.py` — async SQLAlchemy engine + session factory.
- `app/migrations.py` — idempotent in-process SQL run on startup.
- `app/state.py` — `DealState` TypedDict shared across LangGraph nodes.
- `app/graph.py` — LangGraph `StateGraph` with HITL `interrupt_before` gates.
- `app/llm.py` — provider dispatch (`build_llm`, `build_structured_llm`) with per-role overrides.
- `app/budget.py` — per-deal budget guard + pricing table for Haiku / Sonnet / Opus.
- `app/telemetry.py` — OTel setup + `@trace_agent` decorator.
- `app/agents/` — agent stubs (router, extractor, normalizer, analyst, variance).
- `app/engines/` — deterministic engine stubs (revenue, F&B, expense, capital, debt, returns, sensitivity, partnership).
- `app/api/` — HTTP routers (deals, documents, model, market, analysis, export, data_library, settings, health).

## Status

Phase 2/3 scaffold — most routes return placeholder Pydantic responses. Real agent and engine logic land in subsequent passes.
