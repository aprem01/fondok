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
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | required (`pk_test_` today, `pk_live_` after the cutover below) | same | same |
| `CLERK_SECRET_KEY` | required alongside the publishable key | same | same |

Both Clerk keys are effectively required on every deployed environment: a
web bundle without them boots in its local demo persona, but the worker
refuses every request that carries no Clerk JWT (see "Enabling Clerk auth"),
so such a deployment shows no data.

## Enabling Clerk auth

**The contract (FON-93, in force since 2026-10-06): the tenant of every
worker request is the `org_id` of a verified Clerk session JWT, and nothing
else.** There is no header fallback and no default tenant on a deployed
worker.

How a request is scoped:

1. The web app attaches `Authorization: Bearer <Clerk session JWT>` to every
   worker call. The worker verifies the signature against `CLERK_JWKS_URL`
   (RS256) and, when set, the `CLERK_JWT_ISSUER` / `CLERK_JWT_AUDIENCE`
   claims. A token that fails verification is a **401** (the browser
   refreshes its session and retries); a request with no token is a **401**.
2. The JWT's organization id (`org_id`, or `o.id` in v2 claims) is turned
   into the tenant UUID (`coerce_org_tenant_id`, a UUIDv5 of the org id).
   Every tenant-scoped table stores that UUID; every query carries it.
3. With `REQUIRE_REGISTERED_TENANT=1` the org must also be a row in the
   `tenants` registry with status `active`; otherwise the request is a
   **403 `tenant_not_registered`**. This is what makes a Clerk organization a
   Fondok workspace — creating an org in Clerk is not enough.
4. The legacy `X-Tenant-Id` header path is **closed**:
   `ALLOW_TENANT_HEADER_WITHOUT_JWT` defaults to off and must stay unset on
   Railway. It exists for the test suite and a developer's laptop only.
   `DEFAULT_TENANT_ID` is reachable only through that closed path.

Verify the posture on the deployed worker (internal admin JWT):

```bash
curl -s -H "Authorization: Bearer <internal admin token>" \
  https://fondok-worker-production.up.railway.app/admin/health \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["auth"])'
# {'header_tenant_path_enabled': False, 'jwt_required': True,
#  'registered_tenant_required': True, 'rls_tenant_guc': True}
```

### Registering a tenant

`POST /admin/tenants` registers an org over HTTP, but only for a caller whose
own tenant is already `internal` — so the first internal tenant is written
from a trusted shell, and new institutions can be too:

```bash
cd apps/worker
# Bootstrap Fondok's own workspace (once per Clerk instance)
uv run python scripts/register_tenant.py \
    --clerk-org org_XXXXXXXXXXXXXXXXXXXXXXXX --name "Fondok Internal" --kind internal
# Register a customer institution
uv run python scripts/register_tenant.py \
    --clerk-org org_YYYYYYYYYYYYYYYYYYYYYYYY --name "Brookfield" --kind external
# See what is registered
uv run python scripts/register_tenant.py --list
```

`scripts/isolation_rollout.py` sequences the whole Phase-3 runbook
(`docs/WORKSPACE_ISOLATION.md` §5, §6, §10): dry run by default, `--execute`
performs the database steps (register the internal tenant, fold the listed
deals' old single-member tenants into it) and prints the Clerk / Railway
steps as instructions with the `/admin/health` fields that must read true
afterwards. The Clerk webhook (`CLERK_WEBHOOK_SECRET`, Svix) parks
`organization.created` orgs as `pending_review`; `register_tenant.py
--status active` or the admin API approves them.

Worker variables that drive all of this (`apps/worker/app/config.py`):

| Key | Production value |
| --- | --- |
| `CLERK_JWKS_URL` | the instance's `https://<frontend-api>/.well-known/jwks.json` |
| `CLERK_JWT_ISSUER` | the instance's issuer, e.g. `https://clerk.<host>` (optional, recommended) |
| `CLERK_JWT_AUDIENCE` | optional |
| `CLERK_WEBHOOK_SECRET` | the instance's Svix endpoint secret (`whsec_…`) |
| `REQUIRE_REGISTERED_TENANT` | `1` |
| `ALLOW_TENANT_HEADER_WITHOUT_JWT` | **unset** |

Values must carry no trailing whitespace. Since 2026-10-30 the worker
strips every string setting at boot and logs
`config: stripped whitespace from <NAME>` (the name only) when it had to —
grep the deploy log for that line after changing a variable.

### Web keys

```bash
printf '%s' "$VALUE" | vercel env add NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY production
printf '%s' "$VALUE" | vercel env add CLERK_SECRET_KEY production
```

`printf '%s'` (no trailing newline) is the point. A publishable key ending in
`_dummy` is treated as unset by the web app (preview deployments that should
not sign in); the worker behind it still requires a JWT.

## Clerk production cutover

The worker and the web app run against a Clerk **development** instance
(`pk_test_` / `sk_test_`). Go-live moves both to a production instance.
Facts that shape the order below (verify against Clerk's docs when
executing): a production instance is bound to a domain you control and
needs DNS CNAME records (`clerk.<domain>`, `accounts.<domain>`, mail
records); `*.vercel.app` cannot carry them. **A Fondok-owned domain (e.g.
`app.fondok.<tld>`) is therefore a prerequisite**, and that one fact fans
out into CORS, the e2e host guard and the alias workflow.

Steps, in order. "Verify" lines are read-only.

1. **Domain (Prem).** Add the production hostname to the Vercel project
   (`aprem01s-projects/fondok`); keep `fondok-app.vercel.app` as a secondary
   alias during the transition.
   Verify: `curl -sI https://<host>/ | head -1` → 200, or 307 to sign-in.
2. **CORS (code/config).** `CORS_ORIGINS=https://<host>` on Railway — the
   default regex only knows `*.vercel.app` hosts.
   Verify: `curl -s -o /dev/null -w '%{http_code}' -H "Origin: https://<host>" -X OPTIONS -H "Access-Control-Request-Method: GET" https://fondok-worker-production.up.railway.app/health`
   and inspect `access-control-allow-origin` with `-D -`.
3. **Clerk (Prem).** Create the production instance with the same settings
   as dev: organizations on, org creation off — repeat the dev-instance step
   from `docs/WORKSPACE_ISOLATION.md` §10.2:
   `clerk api /instance/organization_settings -X PATCH -d '{"organization_creation_defaults":{"enabled":false}}' --instance prod`.
   Set the domain, add the DNS records, wait for Clerk to show them
   verified. Create the "Fondok Internal" and "Brookfield" organizations on
   the production instance — **their org ids are new** (step 7).
   Verify: `clerk api /instance --instance prod` and
   `curl -s https://clerk.<host>/.well-known/jwks.json | python -m json.tool | head`.
4. **Vercel env, production scope only (Prem).**
   `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_…`, `CLERK_SECRET_KEY=sk_live_…`;
   keep `NEXT_PUBLIC_WORKER_URL`; leave `NEXT_PUBLIC_SHOW_DIAG` and
   `NEXT_PUBLIC_SHOW_DANGER_ZONE` unset. Use
   `printf '%s' "$VALUE" | vercel env add NAME production`.
   Verify: `vercel env ls production` lists the names; after the deploy the
   sign-in page no longer shows Clerk's "Development mode" badge.
5. **Railway env (Prem).**
   `CLERK_JWKS_URL=https://clerk.<host>/.well-known/jwks.json`,
   `CLERK_JWT_ISSUER=https://clerk.<host>`, and rotate `CLERK_WEBHOOK_SECRET`
   to the production instance's Svix endpoint secret (webhook URL unchanged:
   `https://fondok-worker-production.up.railway.app/webhooks/clerk`, events
   `organization.created/updated/deleted`). Set each through stdin:
   `printf '%s' "$VALUE" | railway variable set --service fondok-worker --stdin KEY`.
   Verify no whitespace slipped in:
   `railway variables --service fondok-worker --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print({k:(v!=v.strip()) for k,v in d.items()})'`
   → every value `False`. Then confirm the redeploy landed (next section's
   `build_sha` one-liner).
6. **JWT verification check (code, after the worker redeploys).** Take a
   production-instance session token (DevTools → any worker call →
   Authorization) and decode it without verifying:
   `python3 -c 'import jwt,sys; print(jwt.decode(sys.argv[1], options={"verify_signature":False}))' <token>`
   → `iss` equals `CLERK_JWT_ISSUER`, `o.id` / `org_id` present. Then
   `curl -H "Authorization: Bearer <token>" https://fondok-worker-production.up.railway.app/deals`
   → 200, or 403 `tenant_not_registered` until step 7 — that sequence is
   expected.
7. **Tenant registry re-seed (Prem).** Tenant UUIDs derive from the Clerk
   org id, so **production-instance orgs are new tenants**; every deal Sam,
   Eshan and Rani rely on lives under dev-instance org UUIDs. Dry run, then
   execute:
   `uv run python scripts/isolation_rollout.py --internal-org org_<prodFondokInternal> --fold-org org_<devFondokInternal> --deal-id …`
   (add `--execute` once the plan reads right) to register the new internal
   tenant and fold the dev-era rows into it; then
   `uv run python scripts/register_tenant.py --clerk-org org_<prodBrookfield> --name Brookfield --kind external`.
   Verify: `register_tenant.py --list` shows both rows `active`; Sam signs
   in and sees the folded deals (`docs/WORKSPACE_ISOLATION.md` §10.7).
8. **Enforcement (Prem).** `REQUIRE_REGISTERED_TENANT=1` if not already on.
   Verify: `/admin/health.auth.registered_tenant_required == true` as a
   Fondok Internal admin; a throwaway org gets 403.
9. **CI smoke secrets (code — decision needed).** The post-deploy smoke
   signs in through the deployed app, but `apps/web/e2e/prod/global-setup.ts`
   refuses live keys. Either (a) relax the guard to "refuse `sk_live_` unless
   `FONDOK_SMOKE_ALLOW_LIVE_INSTANCE=1`" and provision a dedicated smoke user
   on the production instance (Clerk test emails work on production
   instances only with test mode enabled — verify), or (b) keep a
   dev-instance preview deployment for the smoke and point
   `PLAYWRIGHT_BASE_URL` there. (a) is recommended. Either way update the
   `apps/web/e2e/prod/guard.ts` host allow-list and the hard-coded
   `https://fondok-app.vercel.app` in `post-deploy-smoke.yml` to the new
   domain.
10. **Docs.** This file's "Enabling Clerk auth" and the RUNBOOK's live
    surfaces must name the new host.

**Rollback:** switch the four Clerk variables (`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`,
`CLERK_SECRET_KEY` on Vercel; `CLERK_JWKS_URL`, `CLERK_JWT_ISSUER` on
Railway — and `CLERK_WEBHOOK_SECRET` if rotated) back to the dev-instance
values and redeploy both sides; confirm with the `build_sha` one-liner and
`/admin/health.auth`. The registry rows for the production orgs are
harmless to leave in place.

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

A push to `main` also redeploys (Railway builds `apps/worker/Dockerfile` on
push). Either way, **confirm the new build is serving before judging it** —
on 2026-10-28 Railway lagged 3.5 hours behind `main` and nothing showed it:

```bash
curl -s https://fondok-worker-production.up.railway.app/health \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["build_sha"], d["version"], d["status"], d["degraded_reasons"])'
git rev-parse origin/main   # must equal build_sha
```

`build_sha` is `RAILWAY_GIT_COMMIT_SHA` (Railway sets it on the container),
else the Dockerfile's `ARG GIT_SHA`, else `null` — a `null` means the
container has neither, not that nothing is deployed. The post-deploy smoke
(`.github/workflows/post-deploy-smoke.yml`, job `worker-deployed`) runs this
same check for up to 20 minutes after every alias promotion and fails with an
annotation when the worker never catches up; the web side is
`https://fondok-app.vercel.app/api/version` (`{sha, env}`), required to match
only when the deploy touched `apps/web/`.

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
curl -s https://fondok-worker-production.up.railway.app/health | python3 -m json.tool
# status, version, build_sha, build_time, db, db_migrations_dsn, usali_rules,
# structural_recognizer, ontology_version, degraded_reasons
```

The one-liner that answers "is the commit I pushed the one serving?":

```bash
curl -s https://fondok-worker-production.up.railway.app/health \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["build_sha"], d["version"], d["status"], d["degraded_reasons"])'
```

Operator detail — storage, auth posture, embeddings, cache counters, the
last LLM provider refusal (`llm_provider.last_error` / `last_error_at`),
`extraction_failures_last_hour` / `engine_failures_last_hour` and the
`data_policies` posture (`tenants_total`, `tenants_with_withhold`,
`invalid_rows`, or `error` on a registry outage) — is on `GET /admin/health`
behind an internal admin JWT.

First deploy carrying FON-116 (tenant data policies): confirm the worker's
first boot log shows `migration applied: tenants.add_data_policies` before
trusting `/admin/health` — every registry read now selects that column, so a
boot whose migration failed refuses tenant lookups rather than serving them.

### Update the Vercel web app

```bash
cd /Users/prem/fondok
vercel --prod --yes
```

Since 2026-10-30 `apps/web/.eslintrc.json` is in place and `next build` runs
ESLint, so **a lint error fails the Vercel production build** (warnings do
not fail `next build`; CI's separate lint step runs `--max-warnings=0`, so a
warning fails CI instead); run `pnpm exec next lint --max-warnings=0` in
`apps/web` before pushing. CI's own build step passes `--no-lint` because its
dedicated lint step already ran.

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
