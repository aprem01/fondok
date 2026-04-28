# Fondok AI

> AI-powered hotel acquisition underwriting. From offering memorandum to investment committee memo in **17 minutes**.

Fondok is decision infrastructure for institutional hotel investors. It ingests broker materials (OMs, T-12s, STR reports, term sheets), runs them through a multi-agent extraction + reasoning pipeline, and produces a fully-cited IC memo with live IRR, variance detection against the broker case, and exportable Excel / PDF / PPTX deliverables.

**Live demo:** https://fondok-app.vercel.app/  
**Inspiration:** https://fondokai.lovable.app/  
**Worker API:** https://fondok-worker-production.up.railway.app/

---

## What it does

1. **Ingest.** Drop a folder of deal documents — Fondok classifies each (T-12, OM, STR, term sheet, …) and routes it to the right extractor.
2. **Extract.** Per-document field extraction with confidence scores and citations back to the source page.
3. **Underwrite.** Three engines (returns, variance, comp set) compute IRR / EM / CoC and flag where the broker case diverges from the underwritten case.
4. **Synthesize.** Streaming IC memo generation in 6 sections, every claim cited.
5. **Export.** Excel model, PDF memo, PowerPoint deck.

---

## Architecture

```
┌──────────────────────┐         ┌────────────────────────────────────────┐
│ Next.js 14 web app   │  HTTPS  │ FastAPI worker (Railway)               │
│ apps/web (Vercel)    │◄───────►│ apps/worker                            │
│  - Landing + dashboard│         │  - /deals  /documents  /memo/stream    │
│  - Live IRR engine    │         │  - LangGraph multi-agent orchestration │
│  - SSE memo viewer    │         │  - Anthropic SDK (cached prompts)      │
└──────────────────────┘         │  - Postgres + Redis                    │
                                 └──────────────┬─────────────────────────┘
                                                │
                              ┌─────────────────┼─────────────────┐
                              ▼                 ▼                 ▼
                       ┌────────────┐    ┌────────────┐    ┌────────────┐
                       │ Claude     │    │ Claude     │    │ Claude     │
                       │ Haiku 4.5  │    │ Sonnet 4.6 │    │ Opus 4.7   │
                       │ classifier │    │ extractor  │    │ memo synth │
                       └────────────┘    └────────────┘    └────────────┘
```

Routing logic chooses the cheapest model that meets the task's bar; prompt caching cuts repeated-context cost on extraction reruns and memo regeneration.

---

## Quick start

```bash
# install
npm install                       # workspace root
cd apps/worker && uv sync         # python deps

# run the web app
cd apps/web
NEXT_PUBLIC_WORKER_URL=https://fondok-worker-production.up.railway.app npm run dev
# unset NEXT_PUBLIC_WORKER_URL to render with mock data

# run the worker locally (see apps/worker/README.md for full setup)
cd apps/worker
uv run uvicorn app.main:app --reload --port 8000
```

---

## Tech

- **Web:** Next.js 14 (App Router), TypeScript, Tailwind CSS, Clerk auth (optional)
- **Worker:** FastAPI, LangGraph, Pydantic, Anthropic SDK, Postgres, Redis
- **Models:** Claude Haiku 4.5 (classification) · Sonnet 4.6 (extraction) · Opus 4.7 (memo)
- **Infra:** Vercel (web) · Railway (worker + Postgres + Redis)

---

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | Mock-data UI shell, dashboard, projects list | done |
| 2 | Worker API contracts, deal CRUD, doc upload | done |
| 3 | Live worker integration, classification, extraction | done |
| 4 | Memo SSE streaming, citations, exports | done |
| 5 | Variance engine, comp sets, scenarios | done |
| 6 | Landing page, favicon, demo polish | in flight |

---

## Documentation

- `docs/fondok-ai-walkthrough.md` — product walkthrough
- `apps/web/README.md` — web app smoke test + env vars
- `apps/worker/` — FastAPI source (see `app/api/` for endpoint contracts)
- `evals/` — golden-set evals + last run results
- `DEPLOY.md` — deployment runbook (Vercel + Railway)

---

## Diagnostics

A non-linked diagnostics page is exposed at [/diag](https://fondok-app.vercel.app/diag) showing worker URL, `/health`, deal count, recent agent costs, and an in-browser smoke test runner.
