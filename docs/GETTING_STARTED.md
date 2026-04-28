# Getting Started — Fondok

> Goal: a fresh laptop running the full stack in **five minutes**.

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Node | 20.x  | `brew install node@20` |
| Python | 3.12 | `brew install python@3.12` |
| uv     | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker | 25+  | https://docs.docker.com/desktop/ |
| gh CLI | latest | `brew install gh && gh auth login` |

## Five-minute setup

```bash
git clone https://github.com/aprem01/fondok.git
cd fondok
make install        # installs npm + uv deps
make db-up          # starts Postgres + Redis in Docker
make dev            # web on :3000, worker on :8001 (parallel)
```

Open two tabs:

- http://localhost:3000 — the Next.js web app
- http://localhost:8001/health — should return `{"status": "ok"}`

That's it. `Ctrl-C` in the `make dev` terminal stops both processes.

---

## What's running where

| Process | Port | Started by |
|---|---|---|
| Next.js web   | 3000 | `make dev` (or `make web`) |
| FastAPI worker | 8001 | `make dev` (or `make worker`) |
| Postgres      | 5432 | `make db-up` |
| Redis         | 6379 | `make db-up` |

If you don't have Docker available (e.g. on a corporate laptop), use:

```bash
make worker-dev   # worker on SQLite — no Postgres needed
```

…and skip `make db-up`. Some integration tests will be skipped, but the demo flow works end-to-end.

---

## Running tests

```bash
make worker-test    # pytest, with the slow agent/cache-hit suites skipped
make typecheck      # tsc + mypy
make lint           # next lint + ruff
make evals          # golden-set regression — deterministic, no LLM tokens
```

---

## The demo persona

The mock data that backs an unauthenticated landing visit lives at:

```
apps/web/src/lib/mockData.ts
```

The persona is **Eshan Mehta — Senior Analyst at Brookfield Real Estate (Pro Plan)**. The demo deal is **Kimpton Angler — Miami**. Edits to `mockData.ts` show up on every page that reads it; no DB seed required.

---

## Adding a new agent

1. Use `apps/worker/app/agents/router.py` as the template — it shows the canonical signature, state shape, and how to register with the LangGraph state machine.
2. Add the new node to `apps/worker/app/graph.py` (both the `add_node` call and any conditional edges).
3. Add a unit test under `apps/worker/tests/` that exercises the new node in isolation; keep LLM calls behind the `EVALS_MOCK` env so CI stays deterministic.
4. If the agent emits a new field, update both `packages/schemas-py/` and `packages/schemas-ts/` in the same PR (lockstep — see `CONTRIBUTING.md`).

## Adding a new engine

1. Use `apps/worker/app/engines/returns.py` as the template — it shows the engine base class, the deterministic compute pattern, and the citation/audit hooks.
2. Engines are pure functions over the extracted state — no LLM calls. If you need LLM reasoning, write an *agent* instead.
3. Add a golden-set fixture to `evals/` so the engine's output is regression-tested on every PR that touches engines.

---

## Common gotchas

- **`make dev` complains about port 3000** — kill the orphaned process: `lsof -ti:3000 | xargs kill -9`.
- **Worker can't reach Postgres** — Docker may be paused. `docker ps` should show `fondok-postgres` as `healthy`.
- **`uv sync` fails on macOS** — install Xcode CLT: `xcode-select --install`.
- **Web shows "demo data"** even though the worker is up — `NEXT_PUBLIC_WORKER_URL` isn't exported. The `make` targets handle this; if you're running `npx next dev` by hand, set it explicitly.
