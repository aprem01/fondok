# Contributing to Fondok

Short version of what makes a PR easy to merge.

## Before you open a PR

- Branch off `main`. One topic per branch.
- Run `make lint typecheck worker-test` locally — CI will run the same.
- If you touched anything under `apps/worker/app/engines/`, `app/agents/`, `app/graph.py`, `packages/schemas-py/`, or `evals/`, also run `make evals` locally. CI gates merges on it.

## Commits

Conventional-style prefixes are encouraged but not enforced:

```
feat(worker): add F&B revenue engine
fix(web): variance tab crash on empty deal
chore(ci): bump setup-uv to v3
docs(runbook): add Vercel rollback steps
```

Subject ≤ 72 chars. Body explains the *why*, not the *what* (the diff already shows the what).

## Pull requests

Use the repo's PR template if one exists; otherwise:

```
## Summary
<1–3 bullets on the why>

## Test plan
- [ ] make worker-test
- [ ] make typecheck
- [ ] (if engines/agents touched) make evals
- [ ] Manual: <what you clicked through>
```

Keep PRs small. If a PR exceeds ~400 lines of changed code that aren't generated, split it.

## Code style

- **Python** — `ruff check .` and `mypy app` must pass. Type hints on every public function. Async-first; no sync HTTP/DB calls inside request handlers.
- **TypeScript** — `next lint` and `tsc --noEmit` must pass. Prettier handles formatting (no debate). Prefer functional React components and server actions over client-fetched data where possible.

## Schemas — lockstep enforcement

Cross-boundary types live in two mirrored packages:

- `packages/schemas-py/` — source of truth.
- `packages/schemas-ts/` — hand-maintained mirror.

**Any PR that touches one MUST touch the other.** Reviewers will block PRs that don't update both. Drift is caught by the golden-set evals (which round-trip through both schemas), but visual diff at review time is cheaper than a red CI run.

## Tests are part of the PR

- New engine → golden-set fixture under `evals/cases/` covering at least one regression case.
- New agent → unit test under `apps/worker/tests/` that exercises the node with mocked LLM calls (`EVALS_MOCK=true`).
- New API route → at least one integration test hitting it through FastAPI's TestClient.
- New web component with non-trivial logic → unit test or Playwright test (when we add the harness).

## What not to commit

- `.env` files. Use `.env.example` as the canonical reference; secrets live in Railway / Vercel.
- Local SQLite databases (`*.db`). The repo's `.gitignore` should already cover them.
- Anything generated (`.next/`, `node_modules/`, `__pycache__/`, `.venv/`).

## Questions

Open a Draft PR and ping in Slack — feedback is faster than a long-form spec.
