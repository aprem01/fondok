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
| LangGraph state machine        | `apps/worker/app/graph.py` |
| Engines (deterministic compute) | `apps/worker/app/engines/` |
| Agents (LLM-backed reasoning)   | `apps/worker/app/agents/` |
| Extraction pipeline             | `apps/worker/app/extraction/` |
| Streaming / SSE                 | `apps/worker/app/streaming/` |
| Excel / PDF / PPTX export       | `apps/worker/app/export/` |
| HTTP API surface                | `apps/worker/app/api/` |
| Persistence (SQLAlchemy async)  | `apps/worker/app/database.py`, `app/storage/` |
| LLM client + budgets            | `apps/worker/app/llm.py`, `app/budget.py`, `app/costs.py` |
| Audit / telemetry               | `apps/worker/app/audit.py`, `app/telemetry.py` |
| Schemas (Python — source of truth) | `packages/schemas-py/` |
| Schemas (TypeScript — must mirror) | `packages/schemas-ts/` |
| Frontend tab system             | `apps/web/src/app/projects/[id]/page.tsx` |
| Demo / mock fixtures            | `apps/web/src/lib/mockData.ts` |
| Golden-set regression           | `evals/run.py`, `evals/cases/` |

---

## Request flow (happy path)

1. Analyst drops a folder into the **Data Room** (web).
2. Web POSTs each file to `/documents` on the worker; worker stores the bytes and enqueues a classifier run.
3. **Router agent** classifies (T-12, OM, STR, term sheet…) using Haiku — cheap.
4. **Extractor agent** pulls structured fields with Sonnet, returning citations to source pages.
5. **Engines** (returns, variance, debt, capital, expense, partnership, sensitivity, F&B revenue) compute deterministically over extracted state. No LLM calls.
6. **Variance agent** highlights divergences between the broker case and the underwritten case.
7. On "Generate IC Memo", the **analyst agent** (Opus) synthesizes a 6-section memo and streams it back to the web via SSE.
8. Excel / PDF / PPTX export endpoints stream files directly from the worker.

---

## Schema lockstep

`packages/schemas-py/` is the source of truth for every cross-boundary type. `packages/schemas-ts/` is a hand-maintained mirror. **Both must change in the same PR** — CI catches drift via the golden-set evals (which exercise the round-trip), but visual diff during review is cheaper than a red CI run.

---

## What lives outside the repo

- **Deployments** — Railway (worker) + Vercel (web). See `RUNBOOK.md`.
- **Auth** — Clerk. The web app is the only Clerk-aware surface; the worker trusts an opaque user ID in the request body.
- **LLM provider** — Anthropic. No fallback provider yet.
- **Traces** — LangSmith.
