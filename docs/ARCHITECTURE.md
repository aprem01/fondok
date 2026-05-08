# Fondok Architecture

One-page overview. For the full pixel-level UI spec, see `docs/fondok-ai-walkthrough.md`. For ops, see `docs/RUNBOOK.md`.

## High level

```
┌────────────────────────┐         ┌────────────────────────────────────────┐
│ Next.js 14 web app     │  HTTPS  │ FastAPI worker (Railway)               │
│ apps/web (Vercel)      │◄───────►│ apps/worker                            │
│  - Landing + dashboard │         │  - /deals  /documents  /memo/stream    │
│  - Live IRR engine     │         │  - LangGraph multi-agent orchestration │
│  - SSE memo viewer     │         │  - Anthropic SDK (cached prompts)      │
│  - Clerk auth          │         │  - Postgres + Redis                    │
└────────────────────────┘         └──────────────┬─────────────────────────┘
                                                  │
                                ┌─────────────────┼─────────────────┐
                                ▼                 ▼                 ▼
                         ┌────────────┐    ┌────────────┐    ┌────────────┐
                         │ Claude     │    │ Claude     │    │ Claude     │
                         │ Haiku 4.5  │    │ Sonnet 4.6 │    │ Opus 4.7   │
                         │ classifier │    │ extractor  │    │ memo synth │
                         └────────────┘    └────────────┘    └────────────┘
```

The router picks the cheapest model that meets each task's quality bar. Prompt caching cuts repeated-context cost on extraction reruns and memo regeneration.

---

## Code map

| Concern | Path |
|---|---|
| LangGraph state machine            | `apps/worker/app/graph.py` |
| Engines (deterministic compute, 8) | `apps/worker/app/engines/` (revenue, fb, expense, capital, debt, returns, sensitivity, partnership) |
| Agents (LLM-backed reasoning)      | `apps/worker/app/agents/` (router, extractor, normalizer, critic, due_diligence, analyst) |
| Extraction pipeline                | `apps/worker/app/extraction/` (PDF + .xls/.xlsx) |
| Streaming / SSE memo synth         | `apps/worker/app/streaming/` |
| Excel / PDF / PPTX export          | `apps/worker/app/export/` |
| HTTP API surface                   | `apps/worker/app/api/` |
| Variance / Due-Diligence / Dossier | `apps/worker/app/api/{analysis,due_diligence,dossier}.py` |
| Market data (CBRE / STR / PNL)     | `apps/worker/app/api/documents.py` (`/deals/{id}/market-data`) |
| Persistence (SQLAlchemy async)     | `apps/worker/app/database.py`, `app/storage/` |
| LLM client + budgets               | `apps/worker/app/llm.py`, `app/budget.py`, `app/costs.py` |
| Audit / telemetry                  | `apps/worker/app/audit.py`, `app/telemetry.py` |
| Schemas (Python — source of truth) | `packages/schemas-py/` |
| Schemas (TypeScript — must mirror) | `packages/schemas-ts/` |
| Frontend tab system                | `apps/web/src/app/projects/[id]/page.tsx` |
| Demo / mock fixtures               | `apps/web/src/lib/mockData.ts` |
| Golden-set regression              | `evals/run.py`, `evals/golden-set/` |
| External-report parse coverage     | `evals/external-reports/` |

---

## Request flow (happy path)

1. Analyst drops a folder into the **Data Room** (web). Accepts `.pdf` (OMs, T-12s, CBRE Horizons, P&L Benchmarker) and `.xls` / `.xlsx` (STR CoStar Trend exports).
2. Web POSTs each file to `/deals/{id}/documents/upload` on the worker. Worker writes bytes to the raw store synchronously; parsing + extraction run as a background task (`PARSING → UPLOADED → CLASSIFYING → EXTRACTING → EXTRACTED`).
3. Parser branches on extension: PDF → LlamaParse (when configured) or PyMuPDF + pdfplumber. Excel → `xlrd` (.xls) or `openpyxl` (.xlsx); each sheet becomes one `ParsedPage`.
4. **Router agent** classifies (Haiku, cheap) and **Extractor agent** pulls structured fields with Sonnet, citing source pages.
5. **Engines** (revenue / fb / expense / capital / debt / returns / sensitivity / partnership) compute deterministically over extracted state. No LLM calls in the math path.
6. **`/analysis/{id}/variance`** is a deterministic endpoint (not an agent) — flags `BROKER_VS_T12_*` and `BROKER_VS_CBRE_*` divergences.
7. **Due-Diligence agent** generates a broker-question packet from the variance report + extracted fields (Sonnet, structured output).
8. **`/deals/{id}/dossier`** + **`/deals/{id}/ask`** ground analyst Q&A in the deal corpus.
9. On "Generate IC Memo" the **Analyst agent** (Opus) synthesizes a 6-section memo and streams it back via SSE; citations deep-link to source-PDF pages.
10. Excel / PDF / PPTX export endpoints stream files directly from the worker.

---

## Schema lockstep

`packages/schemas-py/` is the source of truth for every cross-boundary type. `packages/schemas-ts/` is a hand-maintained mirror. **Both must change in the same PR** — CI catches drift via the golden-set evals (which exercise the round-trip), but visual diff during review is cheaper than a red CI run.

---

## What lives outside the repo

- **Deployments** — Railway (worker) + Vercel (web). See `RUNBOOK.md`.
- **Auth** — Clerk. The web app is the only Clerk-aware surface; the worker trusts an opaque user ID in the request body.
- **LLM provider** — Anthropic. No fallback provider yet.
- **Traces** — LangSmith.
