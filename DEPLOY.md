# Fondok Deployment Runbook

Authoritative reference for the Fondok production deployment.

## Topology

| Tier | Host | URL |
| --- | --- | --- |
| Web (Next.js) | Vercel (`fondok` project) | https://fondok-app.vercel.app |
| Worker (FastAPI + LangGraph) | Railway (`fondok-worker` project) | https://fondok-worker-production.up.railway.app |
| Postgres 18 (managed) | Railway (`Postgres` service) | Internal: `postgres.railway.internal:5432` |
| Redis (managed) | Railway (`Redis` service) | Internal: `redis.railway.internal:6379` |

All Railway services live in the **production** environment of project
`fondok-worker` (project ID `4c62714e-3546-4779-8bdc-5600a086cac8`,
region `us-east4-eqdc4a`).

## Worker environment variables

Set on `fondok-worker` service. Use `railway variables --service fondok-worker` to view.

| Key | Source / value |
| --- | --- |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` reference (auto-rotates) |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` reference (auto-rotates) |
| `ANTHROPIC_API_KEY` | Stored encrypted on Railway. Set from `apps/worker/.env` via stdin. |
| `LLM_PROVIDER` | `anthropic` |
| `DEFAULT_TENANT_ID` | `00000000-0000-0000-0000-000000000000` |
| `DEFAULT_DEAL_BUDGET_USD` | `20.0` |
| `CORS_ORIGINS` | `https://fondok-app.vercel.app,https://*.vercel.app` |
| `ANTHROPIC_ROUTER_MODEL` | `claude-haiku-4-5-20251001` |
| `ANTHROPIC_EXTRACTOR_MODEL` | `claude-sonnet-4-6` |
| `ANTHROPIC_NORMALIZER_MODEL` | `claude-sonnet-4-6` |
| `ANTHROPIC_ANALYST_MODEL` | `claude-opus-4-7` |
| `ANTHROPIC_VARIANCE_MODEL` | `claude-sonnet-4-6` |
| `MEMO_STREAMING_ENABLED` | `true` (Phase 5: Analyst drafts the IC memo section-by-section and publishes each finished section to the in-process `MemoBroadcast`. The web app subscribes via SSE at `GET /deals/{id}/memo/stream`. Set to `false` to fall back to the single-shot Opus call.) |
| `PYTHONUNBUFFERED` | `1` |
| `LANGSMITH_API_KEY` | _optional_; enables LangSmith trace export when set |
| `LANGSMITH_PROJECT` | _optional_; defaults to `fondok-${DEPLOYMENT_ENVIRONMENT}` |
| `LANGSMITH_ENDPOINT` | _optional_; self-hosted / EU LangSmith endpoint |
| `ALLOW_TEST_INGEST` | `false` in production; defaults to `true` for dev |

`PORT` is injected by Railway at runtime; the Dockerfile binds to `${PORT:-8000}`.

## Vercel environment variables

| Key | Production | Development | Preview |
| --- | --- | --- | --- |
| `NEXT_PUBLIC_WORKER_URL` | `https://fondok-worker-production.up.railway.app` | same | _not yet set — see Known Issues_ |
| `ANTHROPIC_API_KEY` | encrypted | encrypted | – |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | _optional_; unset = demo mode | _optional_ | _optional_ |
| `CLERK_SECRET_KEY` | _optional_; required when publishable key is set | _optional_ | _optional_ |

## Enabling Clerk auth

Auth is feature-flagged via `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`. Without
it, the app boots in demo mode as **Eshan Mehta · Brookfield Real Estate**
(see `apps/web/src/lib/mockData.ts`) and every worker request resolves
to `DEFAULT_TENANT_ID`. Setting both keys flips the app into authenticated
multi-tenant mode without any code changes.

1. Sign up at [clerk.com](https://clerk.com) and create a new application
   (Standard plan suffices for an org-aware deployment).
2. From the Clerk dashboard → API Keys, copy the **Publishable key**
   (`pk_test_…` or `pk_live_…`).
3. Set `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` in Vercel:
   ```bash
   vercel env add NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY production --value 'pk_live_xxx'
   ```
4. Copy the **Secret key** and set `CLERK_SECRET_KEY` (encrypted):
   ```bash
   vercel env add CLERK_SECRET_KEY production --value 'sk_live_xxx'
   ```
5. Redeploy the web app — `vercel --prod --yes`. Sign-in / sign-up routes
   activate automatically and the sidebar swaps the demo persona for the
   live Clerk user + organization switcher.
6. (Optional, multi-tenant) In the Clerk dashboard → Organizations,
   enable organizations. Each organization becomes a Fondok tenant: the
   web app sends `X-Tenant-Id: <organization_id>` on every worker
   request, and the worker scopes all reads/writes to that tenant. When
   no org is selected, the worker falls back to `DEFAULT_TENANT_ID`.

To disable auth in a deployment without unsetting the env var (useful
for previews), set `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_dummy_dummy`
— the `_dummy` suffix is treated as unset and the app reverts to demo
mode.

## Build configuration

- `railway.toml` (repo root) — Railway monorepo config. Builds `apps/worker/Dockerfile`,
  health-checks `/health`, restart policy `ON_FAILURE` with 3 retries.
- `.railwayignore` (repo root) — Prevents `apps/web/`, `package-lock.json`,
  `pnpm-lock.yaml`, and dev artifacts from being uploaded. Required to bypass
  Railway's NPM vulnerability scanner picking up Next.js CVEs that don't
  apply to the Python worker.
- `.dockerignore` (repo root) — Excludes lockfiles, node_modules, local
  SQLite DBs, and `.env` files from the Docker build context.
- `apps/worker/Dockerfile` — Two-stage build: `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`
  builder, then `python:3.12-slim-bookworm` runtime with WeasyPrint native
  deps (`libcairo2`, `libpango-1.0-0`, `libpangocairo-1.0-0`, `libpangoft2-1.0-0`,
  `libgdk-pixbuf-2.0-0`, `libffi8`, `shared-mime-info`, `fonts-dejavu-core`).

## Observability

### LangSmith tracing

LangSmith captures every LLM call (router / extractor / normalizer /
variance / analyst) under a project named `fondok-{environment}`. To
enable, set `LANGSMITH_API_KEY` on the Railway service:

```bash
printf "$LANGSMITH_KEY" | railway variable set --service fondok-worker --stdin LANGSMITH_API_KEY
```

That single env var is enough — the worker auto-detects it on boot and
sets the standard `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, and
`LANGCHAIN_PROJECT` env vars internally so every `ChatAnthropic` client
ships traces upstream. To override the project name, also set
`LANGSMITH_PROJECT`. To target a self-hosted / EU instance, set
`LANGSMITH_ENDPOINT`.

When `LANGSMITH_API_KEY` is unset, tracing is silently disabled.

### Cache hit rate dashboard

The worker exposes two read-only endpoints:

```bash
# Last 100 model calls — overall + per-agent cache hit rate
curl https://fondok-worker-production.up.railway.app/observability/cache-stats

# Per-agent token spend + cache hit rate over the last 7 days
curl https://fondok-worker-production.up.railway.app/observability/agent-costs?days=7
```

The Vercel web app reads `/observability/cache-stats` from the
AnalysisTab badge (top-right "Cache hit: NN%"). When
`NEXT_PUBLIC_WORKER_URL` is unset the badge degrades to "—" silently.

`POST /observability/_test/model-call` is gated by `ALLOW_TEST_INGEST`
and exists only for the worker test suite — production sets it to
`false`.

### Sentry (web error reporting, optional)

Sentry is wired into the Next.js web app behind a single env-var feature
flag. When `NEXT_PUBLIC_SENTRY_DSN` is unset, every Sentry call is a
no-op — even if the optional `@sentry/nextjs` package is installed. To
enable:

1. Install the optional dep (already declared in `apps/web/package.json`
   under `optionalDependencies`):

   ```bash
   cd apps/web && npm install
   ```

2. Set the DSN on Vercel for whichever environments you want to capture:

   ```bash
   vercel env add NEXT_PUBLIC_SENTRY_DSN production --value 'https://<key>@o<org>.ingest.sentry.io/<project>' --yes
   vercel env add NEXT_PUBLIC_SENTRY_DSN preview    --value 'https://<key>@o<org>.ingest.sentry.io/<project>' --yes
   ```

3. (Optional) Wrap the export in `apps/web/next.config.js` with
   `withSentryConfig` from `@sentry/nextjs` if you want source-map
   upload. The two config files at `apps/web/sentry.client.config.ts`
   and `apps/web/sentry.server.config.ts` are already in place and do
   the actual `Sentry.init()` calls behind the DSN gate.

4. Redeploy the web app:

   ```bash
   vercel --prod --yes
   ```

To disable, simply unset `NEXT_PUBLIC_SENTRY_DSN`. The worker (Python)
ships with its own observability stack (LangSmith / cache stats) and is
intentionally not wired through Sentry.

## Common operations

### Redeploy the worker

```bash
cd /Users/prem/fondok
railway up --service fondok-worker --detach
```

Build takes about 3-5 minutes. Use `--detach` to background; omit to stream.

### View logs

```bash
# Stream live runtime logs
railway logs --service fondok-worker

# Build logs for a specific deployment
railway logs --build --service fondok-worker <deployment-id>

# Last N lines (no streaming)
railway logs --service fondok-worker --lines 200
```

### Roll back

List deployments and pick a known-good ID:

```bash
railway deployment list --service fondok-worker
railway deployment redeploy --service fondok-worker <deployment-id>
```

Or, to roll back the most recent deployment:

```bash
railway redeploy --service fondok-worker
```

### Update an env var

```bash
railway variable set --service fondok-worker KEY=VALUE
```

For secrets, pipe via stdin so the value never appears in shell history:

```bash
printf "$SECRET_VALUE" | railway variable set --service fondok-worker --stdin KEY
```

Setting a variable triggers a redeploy by default; add `--skip-deploys` to defer.

### Connect to Postgres

```bash
railway connect Postgres
```

### Verify health

```bash
curl https://fondok-worker-production.up.railway.app/health
# {"status":"ok","version":"0.1.0","db":"ok"}
```

### Update the Vercel web app

```bash
cd /Users/prem/fondok
vercel --prod --yes
```

Set `NEXT_PUBLIC_WORKER_URL` if the worker URL ever changes (e.g. custom domain):

```bash
vercel env rm NEXT_PUBLIC_WORKER_URL production --yes
vercel env add NEXT_PUBLIC_WORKER_URL production --value '<new-url>' --yes
vercel --prod --yes
```

## Known issues

### Preview environment variable not set

The Vercel CLI rejected `vercel env add NEXT_PUBLIC_WORKER_URL preview` with
both the no-branch and `main` forms (the production branch isn't valid for
preview, and the no-branch form errored with a contradictory hint). Preview
deploys will fall back to the in-app default until the var is set via the
Vercel dashboard or after the first preview branch deploy creates one.

### Railway security scanner trips on Next.js lockfile

Railway's snapshot scanner inspects every `package-lock.json` it finds in
the upload, even if the Dockerfile never copies it. The repo-root
`package-lock.json` reports HIGH-severity Next.js CVEs (CVE-2025-55184,
CVE-2025-67779) that only affect the web app. `.railwayignore` keeps it out
of the upload entirely — do not delete that file. If you need to deploy
without `.railwayignore`, upgrade `next` to `^14.2.35` first.

## Project IDs (for the Railway dashboard / API)

- Project: `4c62714e-3546-4779-8bdc-5600a086cac8`
- Environment (production): `1a59870d-da89-4b99-b9e6-42494e2b8d7d`
- Worker service: `1d2ace37-730f-498c-a976-9e5d618c26a3`
- Postgres service: `d71c127f-2535-43d6-943b-99e23e100d2b`
- Vercel project (`fondok`): `prj_d7ryhICUhcIQZBaFFWHxbQm4m7Ca`
- Vercel team (`aprem01s-projects`): `team_T1NIMmLVdB8quERrLdVNv2gF`
