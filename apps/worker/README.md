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

## Exports

Three real export endpoints stream institutional-grade artifacts:

- `GET /deals/{deal_id}/export/excel` → 10-tab `.xlsx` acquisition model (openpyxl)
- `GET /deals/{deal_id}/export/memo.pdf` → 2-3 page IC memo PDF (WeasyPrint)
- `GET /deals/{deal_id}/export/presentation.pptx` → 8-slide IC deck (python-pptx)

Until the agent runtime persists EngineOutputs to the DB, every `deal_id`
resolves to the Kimpton Angler demo fixture (`app/export/fixtures.py`).

### WeasyPrint native dependencies

`weasyprint` requires system libraries (cairo, pango, gdk-pixbuf, libffi):

```bash
# macOS
brew install cairo pango gdk-pixbuf libffi
# Debian / Ubuntu
apt-get install -y libcairo2 libpango-1.0-0 libpangoft2-1.0-0 libgdk-pixbuf-2.0-0
```

## Status

Phase 2/3 scaffold — most routes return placeholder Pydantic responses. Real agent and engine logic land in subsequent passes.
