# Workspace isolation (FON-93)

How Fondok keeps one institution's deals, documents and memos invisible to
every other institution — what is enforced in code today, what is staged
behind settings, and the hand steps Prem must do to turn each stage on.

Companion to `SECURITY_ARCHITECTURE.md` §2 (tenancy model). This document
is the operational one: flags, roles, order of rollout, data migration.

## 1. Vocabulary

| Term | Meaning |
| --- | --- |
| **Tenant** | One institution's workspace. Its id is a UUIDv5 derived from the Clerk organization id (`coerce_org_tenant_id` in `apps/worker/app/api/deals.py`). Every business table stores it in `tenant_id`. |
| **Clerk org** | The identity-side object. A user's session JWT carries `org_id` for the organization they have made active. |
| **Registry** | The `tenants` table (`id`, `clerk_org_id`, `name`, `kind`, `status`, `created_at`). Turns tenancy into an explicit grant. |
| `kind` | `internal` (Fondok staff / testers — may administer the registry and read cross-tenant observability) or `external` (a customer). |
| `status` | `active` is served. `suspended` and `pending_review` (what the Clerk webhook writes for an org nobody registered) are refused once enforcement is on. |

## 2. What the worker enforces today (no flags needed)

Shipped 2026-10-14 on `main`; live as soon as the worker deploys.

1. **The JWT's `org_id` is the only tenant source.** A verified JWT with no
   `org_id` gets `409 no_active_organization` regardless of any
   `X-Tenant-Id` header. Before this change a personal-account token plus
   a header naming any tenant UUID was served that tenant's data.
   (`apps/worker/app/auth/__init__.py`, locked by
   `tests/test_rbac.py::test_personal_jwt_with_tenant_header_is_409_not_served`.)
2. **Only `org_…` ids become tenants on the JWT path.** `user_…`, `acc_…`
   and raw UUIDs in the `org_id` claim are refused (`coerce_org_tenant_id`).
3. **No default-tenant fallbacks in the data path.** `_load_engine_inputs`
   and `_load_deal_payload` raise if called with a DB session and no
   tenant; the memo `AnalystInput` is stamped with the caller's tenant;
   `GET /settings/tenant` returns the caller's tenant (plus its registry
   row) instead of `DEFAULT_TENANT_ID`.
4. **`/observability/cache-stats` and `/observability/agent-costs` require
   auth and are scoped to the caller's tenant.** `?all=1` is honoured only
   for a registered `internal` tenant (or the local header path).
5. **The SQL safety listener covers every `tenant_id` table.** Eight tables
   were missing (`scenarios`, `cell_comments`, `lineage_records`,
   `portfolio_library`, `saved_pipeline_views`, `pipeline_digest_schedules`,
   `pending_batches`, `template_mappings`). `tests/test_tenant_listener_strict.py`
   runs the whole deal-scoped endpoint surface with the listener in `raise`
   mode.
6. **Web:** the sidebar `OrganizationSwitcher` hides the personal account
   and the "Create organization" action, so an analyst cannot mint a
   workspace from the app. (Clerk also needs the dashboard toggle — §5.)
7. **Every deal-scoped route resolves a caller (2026-10-15).** A route
   audit found three that did not: `GET /deals/{id}/search` (full-text
   search over a deal's document text with no session at all) and
   `POST …/documents/{doc}/reprocess` + `POST …/documents/{doc}/extract`
   (re-fire a paid extraction on any document given its two UUIDs). All
   three now take `Depends(get_tenant_id)`, scope their lookup by the
   caller's tenant (cross-tenant → 404, no caller → 401) and the chunk
   search carries `tenant_id` in its SQL. The legacy `/model/{id}/…`
   stubs require a caller too. Locked by
   `tests/test_tenant_isolation_comprehensive.py` (the `/search` catalog
   row now sends `?q=` — without it FastAPI 422'd before the handler and
   the probe proved nothing) and
   `test_document_routes_404_across_tenants_and_401_without_a_caller`.
8. **Clerk session-token v2 claims.** The worker reads the org from
   top-level `org_id` / `org_role` (v1) **or** `o.id` / `o.rol` (v2), so a
   Clerk instance upgrade does not turn every session into
   `409 no_active_organization` (`tests/test_rbac.py::test_v2_session_token_org_claims_resolve_tenant_and_role`).
9. **Three more routes 404 a foreign deal (2026-10-17).** The first run of
   `scripts/isolation_check.py` (§9) over every mounted deal-scoped route
   found three that answered another tenant 2xx: the three `/export/*`
   routes built an *empty* payload for a foreign deal (`load_live_payload`
   never raises; every inner query is tenant-scoped, so no data crossed,
   but a stranger downloaded a blank workbook / PDF / deck instead of a
   404); `POST …/backfill_embeddings` returned its "skipped" envelope
   before the tenant check on a worker without the chunk table or without
   `VOYAGE_API_KEY`; and `POST …/memo/{section}/edits` had no ownership
   gate (the GET did), so a caller could append an edit + audit row
   against a foreign deal id. All three now 404. Locked by
   `tests/test_isolation_scripts.py::test_exports_backfill_and_memo_edit_404_a_foreign_deal`.
   One deliberate exception remains: `GET /deals/{id}/audit` returns the
   **empty** feed (`total: 0, entries: []`) for a foreign id rather than
   404 (documented in `api/audit.py`; both shapes leak the same
   information). The checker accepts that route only when the body is
   exactly that empty shape.

The header path (`ALLOW_TENANT_HEADER_WITHOUT_JWT`) is unchanged: off by
default, on only in `tests/conftest.py` and a developer's local `.env`.

## 3. Staged behind settings (default off — production unchanged until flipped)

| Setting (Railway → `fondok-worker`) | Default | What it turns on |
| --- | --- | --- |
| `REQUIRE_REGISTERED_TENANT` | `False` | A JWT whose org is not an `active` row in `tenants` gets `403 tenant_not_registered`. If the registry itself cannot be read (DB down, table missing) the request gets `503 tenant_registry_unavailable` + `Retry-After: 2` — an outage is never reported as "not registered", and is never cached. |
| `DB_SET_TENANT_GUC` | `False` | Every transaction of every worker session — request-scoped **and** background (extraction pipeline, Run Model task, cost persistence, batch poller, digest scheduler) — runs `set_config('app.tenant_id', <tenant>, true)` so Postgres RLS policies can see the caller. No-op on SQLite. |
| `CLERK_WEBHOOK_SECRET` | unset | Enables `POST /webhooks/clerk`. Unset → every delivery is refused with 503 (never accepted unsigned). |

`/health` → `auth` reports all of these:
`{"header_tenant_path_enabled", "jwt_required", "registered_tenant_required", "rls_tenant_guc"}`.

## 4. The registry

### Reading / writing it

* `GET /admin/tenants` (admin role) — an `internal` caller sees every row;
  anyone else sees only their own row (or `[]`).
* `POST /admin/tenants` (admin role, **internal callers only**) — upsert
  `{clerk_org_id, name, kind, status}`. Posting an org the webhook parked
  as `pending_review` with `status: "active"` is how a new institution is
  approved.
* `scripts/register_tenant.py` — the same upsert from a trusted shell.
  Needed exactly once: the first `internal` tenant cannot be created over
  HTTP because nobody is internal yet.
* `POST /webhooks/clerk` — Svix signature verified with
  `CLERK_WEBHOOK_SECRET`; 5-minute replay window
  (`CLERK_WEBHOOK_TOLERANCE_S`). Three events are acted on; everything
  else is acknowledged (200) and ignored:
  * `organization.created` → inserts `kind=external, status=pending_review`.
    Never downgrades an existing row (a replay for an approved org leaves
    it `active`; a replay for a deleted org leaves it `suspended`).
  * `organization.updated` → refreshes `name` only. `kind` and `status`
    are the registry's (an admin's) to set — a rename in Clerk never
    approves, suspends or re-activates. Unknown org → `{"ignored": …,
    "reason": "not_registered"}`, no row created.
  * `organization.deleted` → `status=suspended`, `kind` and `name` kept,
    row kept (the UUID still explains every historical `tenant_id`).
    Nothing in the webhook path can ever set `status=active`.

The registry row's `id` **is** the tenant UUID already stored on every
existing row for that org, so registering an org that has been using the
product attaches its history with no data movement.

### Lookups

`app/tenants.py` caches in-process: a **hit** for 10 s, a **miss** (no
row) for 2 s. Writes on the same replica clear the cache; a registration
or suspension written on another replica lands within those windows. A
lookup **failure** (connection error, `tenants` table missing) is never
cached:

* the informational callers (`GET /settings/tenant`, the admin list)
  degrade to "no registry row" and log;
* the enforcement path (`REQUIRE_REGISTERED_TENANT=1`) answers
  `503 tenant_registry_unavailable` with `Retry-After: 2`. Before
  2026-10-14 an outage read as `403 tenant_not_registered` and was cached
  for the hit TTL — a paying institution would have been told it had no
  workspace until the cache lapsed.

With enforcement off the worker never refuses on the registry's account;
turning it on is the operator's explicit choice, and from then on a
registry outage is a (retryable) outage for JWT traffic.

## 5. Rollout order (Prem, by hand)

Do these in order; each step is independently reversible. **§10 is the
checked version of this list** — it sequences the same steps through
`scripts/isolation_rollout.py` (dry run, then `--execute` for the database
steps) and ends with the two isolation proofs (§9). Use §10; this section
stays as the explanation of each step.

1. **Deploy the worker** (`git push origin main` → Railway). Nothing
   changes for users yet except the hole closes (§2.1) and
   `/observability/*` now needs a session.
2. **Clerk dashboard → Organizations settings:** turn **off** "Allow users
   to create organizations" (the web switcher hides the button; this makes
   the API refuse too). Decide who may create orgs (Prem only).
3. **Create the "Fondok Internal" org in Clerk** (if not already present)
   and add Sam, Eshan, Rani and Prem as members. Note its `org_…` id.
4. **Bootstrap the registry** from a shell with the production
   `DATABASE_URL` (Railway → `fondok-worker` → Variables; or `railway run`):
   ```bash
   cd apps/worker
   uv run python scripts/register_tenant.py \
       --clerk-org org_<FondokInternal> --name "Fondok Internal" --kind internal
   uv run python scripts/register_tenant.py --list
   ```
   Then register each customer org (`--kind external`) the same way, or
   via `POST /admin/tenants` from a Fondok Internal admin session.
5. **Migrate Sam / Eshan / Rani's existing deals into Fondok Internal**
   (§6) so nothing they rely on disappears when enforcement turns on.
6. **Clerk dashboard → Webhooks:** add an endpoint
   `https://<worker>/webhooks/clerk` subscribed to `organization.created`,
   `organization.updated` and `organization.deleted`. Copy its signing secret
   (`whsec_…`) into Railway as `CLERK_WEBHOOK_SECRET`. Redeploy. Send the
   dashboard's test event; expect `200 {"ok": true, ...}`.
7. **Flip `REQUIRE_REGISTERED_TENANT=1`** on Railway. Verify:
   `/health.auth.registered_tenant_required == true`; a Fondok Internal
   session lists deals; a throwaway Clerk org gets `403
   tenant_not_registered`.
8. **RLS (later, separate change window)** — §7.

## 6. Data migration: Sam / Eshan / Rani → "Fondok Internal"

Today their deals live under whichever Clerk org each of them had active
(their own single-member orgs, plus possibly the historical catch-all
`DEFAULT_TENANT_ID`). After step 5.3 they share one org, and rows must
follow. **Do not hand-write UPDATEs** — reuse
`scripts/reassign_default_tenant_rows.py`, which already knows the
tenant-scoped tables and moves rows between tenant UUIDs with a dry run.

For each old org (one per person):

```bash
cd apps/worker
# 1. find the old tenant UUID
uv run python -c "from app.api.deals import coerce_org_tenant_id as c; print(c('org_<OLD>'))"
# 2. dry run: the per-table PLAN (rows on source / target, window column) — default is dry-run
uv run python scripts/reassign_default_tenant_rows.py \
    --from-tenant <old uuid> --clerk-org org_<FondokInternal>
# 3. apply — the same plan is printed first, then each table is moved
uv run python scripts/reassign_default_tenant_rows.py \
    --from-tenant <old uuid> --clerk-org org_<FondokInternal> --commit
```

`--from-tenant` and the complete table list (22 tables — it used to miss
nine) were added to the script in FON-93.

**The run is fail-closed (2026-10-14).** The count pass runs first and
prints a plan for every table. If **any** table cannot be counted
(unknown table, missing column, no timestamp mapping) the script exits 3,
refuses `--commit`, and moves nothing. Before this the failing table was
printed as `SKIPPED` and the rest moved — with `--created-after` set,
`documents` (which has `uploaded_at`, not `created_at`) was the table
skipped, so the folded deals opened with an empty Data Room.

`--created-after` / `--created-before` filter each table on **its own**
timestamp column (`TABLE_TIMESTAMP_COLUMNS` in the script: `documents` →
`uploaded_at`, `engine_outputs` → `started_at`, `pending_batches` →
`submitted_at`, everything else `created_at`). On Postgres the bound is
bound as a tz-aware `datetime` (a date-only value is midnight UTC);
until 2026-10-15 it was bound as the raw string, which asyncpg refuses
against `TIMESTAMPTZ`, so every windowed count errored and the run
refused with exit 3 — the window had only ever worked on SQLite. A
malformed bound now exits 2 before the database is touched.
`tests/test_reassign_tenant_rows_script.py` checks the map against the
migrations' DDL, so adding a tenant-scoped table without mapping it fails
the suite. Each table is still moved in its own transaction; a write-time
failure (below) is reported with exit 1 and the rows it names stay on the
source tenant — re-run until every table reads `moved 0`.

`audit_log` has an append-only trigger (`audit_log_no_update_delete`), so
its `UPDATE` counts fine but prints an ERROR at write time. Either leave
history under the old tenant (the Activity Feed for those deals starts
fresh in Fondok Internal) or, as the owner role inside one transaction,
`ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete`, run the
script's `UPDATE audit_log …` for that tenant, re-enable the trigger — a
product call, not a technical one. Document whichever you choose in the
Linear ticket.

After the move, the old single-member orgs can be deleted in Clerk (or
left as `suspended` rows in the registry so the audit trail explains the
UUIDs).

## 7. Row-level security (ready, not applied)

Defense-in-depth below the application: even a query that forgets its
`WHERE tenant_id = …` returns only the caller's rows.

### Pieces already in the repo

* `apps/worker/migrations/rls/001_enable_rls.sql` — creates the
  `fondok_app` role (`LOGIN NOBYPASSRLS`), grants DML, then for every
  tenant-scoped table: `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL
  SECURITY`, and a policy
  `USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)`
  with the same `WITH CHECK`. The `NULLIF` matters: once any transaction on
  a pooled connection has published the GUC, a later transaction that
  publishes nothing reads `''` (not NULL) from `current_setting`, and
  `''::uuid` raises `invalid input syntax for type uuid` — every unscoped
  statement on that connection would 500 instead of seeing zero rows
  (verified on Postgres 16, 2026-10-15). The policy is dropped and
  recreated on each run, so re-applying the file upgrades an older
  policy. Idempotent; **not** run at startup.
* `app/database.py` — an `after_begin` listener registered **once, on the
  Session class** (`TenantSession`, the sync session behind every
  `AsyncSession` the factory builds). When `DB_SET_TENANT_GUC` is on and
  the DSN is Postgres it runs `set_config('app.tenant_id', <tenant>, true)`
  at the start of **every** transaction of **every** session — the
  request dependency `get_session` and every `get_session_factory()()`
  session alike. Until 2026-10-14 the hook was attached per session inside
  `get_session` only, so the background pipelines (document parse /
  extraction, the Run Model task, `persist_model_calls_standalone`, the
  batch poller, the digest scheduler) would have published nothing and read
  zero rows under RLS. The hook (not a one-shot statement) is deliberate: a
  route that commits mid-request autobegins a new transaction, and the GUC
  must be re-published or RLS would return zero rows for the rest of the
  request. The flag is read on every call, so flipping it needs no restart
  ordering. The tenant comes from the `current_tenant_id` ContextVar, set
  by `get_current_auth` on every request path and by each background
  entrypoint from its `tenant_id` argument.

### Why a non-owner role

`FORCE ROW LEVEL SECURITY` applies policies to the table owner, but a
superuser or any role with `BYPASSRLS` ignores them. Railway's default
Postgres user is the owner and typically superuser. So:

* keep the owner role for `run_startup_migrations()` (DDL) — or run
  migrations from a one-off job;
* point the worker's `DATABASE_URL` at `fondok_app` for serving traffic.

Simplest shape: two Railway variables, `DATABASE_URL` (fondok_app) and
`DATABASE_URL_MIGRATIONS` (owner). Teaching `run_startup_migrations` to
prefer the second when set is a small follow-up; until then apply
`001_enable_rls.sql` manually and run migrations manually when the schema
changes.

### Background jobs

Per-deal background work (document parse / extraction, the Run Model
task, cost persistence) now sets `current_tenant_id` from its `tenant_id`
argument before opening its session, so under RLS it reads and writes as
that tenant.

The digest scheduler (`services/digest_scheduler.py`) and the Anthropic
batch poller (`agents/analyst_batch.py`) set the tenant **per row inside
their loop** (and end the scan's transaction first, so each row's
statements start a fresh transaction under that row's tenant). Their
initial scan, however, is cross-tenant (`SELECT … FROM
pipeline_digest_schedules WHERE next_run_at <= now` / open
`pending_batches`) and under RLS as `fondok_app` would return nothing.
Before step 7.4 either (a) run the worker's background loops under the
owner DSN, or (b) replace the scan with a per-tenant iteration over the
registry (`list_tenants()`), publishing each tenant before its scan.

### Order

1. Apply `001_enable_rls.sql` as the owner, in a change window. Policies
   exist but the worker still connects as the owner → nothing changes.
2. Verify as `fondok_app` with psql (the file's footer has the queries):
   no GUC → 0 rows; GUC set → that tenant's rows.
3. Set `DB_SET_TENANT_GUC=1` on Railway (worker still as owner → still
   nothing changes functionally; `/health.auth.rls_tenant_guc` flips).
4. Switch the worker's `DATABASE_URL` to `fondok_app`. Smoke the product
   as Fondok Internal and as one external tenant. Watch for 0-row
   responses on background jobs (§ above).
5. Rollback at any step: switch the DSN back to the owner; optionally
   `DISABLE ROW LEVEL SECURITY` (footer of the SQL file).

## 9. Phase-3 tooling (no hand steps in the data path)

Three tools, added 2026-10-17, so that turning enforcement on is a
sequence of checked steps rather than a list of things to remember.

### `apps/worker/scripts/isolation_check.py` — the HTTP proof

Runs against a **live worker** and proves, route by route, that firm B
cannot read or touch firm A's deal. Routes are enumerated from
`app.main` (the same enumeration `tests/test_unauthenticated_routes_guard.py`
uses), so a route that ships tomorrow is probed tomorrow; `--routes openapi`
reads the deployed worker's `/openapi.json` instead when the checkout
might not match the deploy.

* **Credentials.** JWT mode: `FIRM_A_JWT` / `FIRM_B_JWT` (two Clerk session
  tokens, one member of each firm — DevTools → Network → any worker call
  → `Authorization`; they expire in about a minute). Header mode (local
  only): `--tenant-a` / `--tenant-b` UUIDs against a worker started with
  `ALLOW_TENANT_HEADER_WITHOUT_JWT=1`; the script reads
  `/health.auth.jwt_required` first and refuses header mode on a worker
  that requires JWTs.
* **What it asserts.** Both credentials resolve to distinct tenants; the
  two `GET /deals` lists are disjoint and every row carries its caller's
  tenant; for every deal-scoped route, firm B at firm A's real deal (and
  first document) gets 404/403 — a 2xx is a `LEAK`; an anonymous request
  gets 401 (JWT required) or 404 (header path open, served
  `DEFAULT_TENANT_ID`); the owner is also probed on GET routes so a 404 for
  everyone is reported as `INCONCLUSIVE`, never as a pass. `/settings/tenant`,
  `/admin/tenants` and `/observability/*?all=1` are checked against each
  caller's registry `kind` (internal: whole registry + cross-tenant view;
  external: own row only, 403 on `all=1`).
* **It writes nothing by default.** Only GET routes are probed against the
  real deal; mutating routes are listed as `SKIP`. `--allow-writes` creates
  one scratch deal per firm, probes every mutating route on firm A's
  *scratch* deal as firm B, and hard-deletes both afterwards (archives when
  the credential is not an admin, and says so).
* **Exit status.** 0 clean; 1 on any `LEAK` or `ERROR` (`--strict` also on
  `INCONCLUSIVE`); 2 on a discovery problem. `--json out.json` writes the
  rows.

```bash
cd apps/worker
FIRM_A_JWT=eyJ… FIRM_B_JWT=eyJ… uv run python scripts/isolation_check.py \
    --base-url https://<worker> --allow-writes
```

`tests/test_isolation_scripts.py` drives it over an ASGI transport against
the local app with two tenants (all 100 deal-scoped routes, writes on) and
proves it is not vacuous (a toy app that serves a foreign deal → `LEAK`,
exit 1).

### `apps/web/e2e/prod/02-two-firm-isolation.spec.ts` — the browser proof

Opt-in exactly like the live-deal smoke (`FONDOK_E2E_PROD=1` + the
production base URL + the five smoke secrets) **plus** `FONDOK_SMOKE_B_EMAIL`,
`FONDOK_SMOKE_B_PASSWORD`, `FONDOK_SMOKE_B_ORG` — a second Clerk test user
in a *different* organization. Signs in as firm A, opens the pinned deal
and reads its project list; signs in as firm B in a fresh browser context,
makes `FONDOK_SMOKE_B_ORG` the active org (refuses to run as any other),
visits firm A's deal URL and asserts the app's not-found state
(`data-testid="deal-load-error"`, "Could not load this deal" — the worker
404s), then asserts firm B's project list shares no deal id and no deal
name with firm A's. Read-only; without the firm-B secrets it skips itself
naming them and `global-setup.ts` warns that isolation was not checked.
See `apps/web/e2e/README.md`.

### `apps/worker/scripts/isolation_rollout.py` — the sequenced runbook

Dry run by default. Against `DATABASE_URL` it prints: (0) which Clerk
variables are present (names only) and the current flag values; (1) the
registry; (2) what `register_tenant.py` would do for `--internal-org`
(INSERT vs. UPDATE and which fields); (3) for each `--deal-id`, the tenant
it currently lives under — located with a tenant-predicated lookup across
the registry rows, the all-zeros catch-all and every `--fold-org` /
`--fold-tenant` you name (a deal under no known tenant is reported, never
guessed) — and, per source tenant, the per-table row counts
`reassign_default_tenant_rows.py` would move, with a warning when that
tenant also owns deals you did not list (the fold moves a whole tenant);
(4) the Clerk dashboard steps; (5) the Railway variables to flip and the
`/health.auth` fields that must read true afterwards; (6) how to verify.

`--execute` performs **only the database steps** — the internal-tenant
upsert and the per-table moves, through the same functions the two
scripts use — and refuses when a listed deal was not found, when a source
tenant owns unlisted deals (unless `--move-whole-tenant`), or when any
table cannot be counted. Clerk and Railway changes are never performed;
they are printed. `tests/test_isolation_scripts.py` checks that the dry run
writes nothing (row counts and tenant ids unchanged) and that `--execute`
registers and folds exactly the listed tenants.

```bash
cd apps/worker   # with the production DATABASE_URL in the environment
uv run python scripts/isolation_rollout.py \
    --internal-org org_<FondokInternal> \
    --fold-org org_<SamOld> --fold-org org_<EshanOld> --fold-org org_<RaniOld> \
    --deal-id <uuid> --deal-id <uuid> …          # dry run
uv run python scripts/isolation_rollout.py … --execute
```

## 10. Phase-3 checklist (Sam / Prem — follow in order)

Each step says who, what, and how you know it worked. Nothing before
step 8 changes what any user sees.

1. **Prem — deploy the worker** (`git push origin main`). Check
   `curl -s https://<worker>/health | python -m json.tool` → `auth.jwt_required: true`,
   `auth.header_tenant_path_enabled: false`.
2. **Prem — turn off organization creation.** Two layers, both settable
   from the Clerk CLI (done 2026-10-17 on the dev instance): the per-user
   flag for existing users (`PATCH /users/{id}` `create_organization_enabled=false`)
   and the default for NEW users
   (`clerk api /instance/organization_settings -X PATCH -d '{"organization_creation_defaults":{"enabled":false}}' --yes --app <app> --instance dev`).
   Verify with `clerk api /instance/organization_settings …` →
   `organization_creation_defaults.enabled: false`. The dashboard toggle is the
   same setting.
3. **Prem — Clerk dashboard:** create (or confirm) the "Fondok Internal"
   org; add Sam, Eshan, Rani, Prem. Copy its `org_…` id. For each person,
   note the `org_…` id of the single-member org they have been using (Clerk
   → Users → their memberships).
4. **Prem — list the deals that must follow into Fondok Internal** (the
   app's `/projects` as each person, or `GET /deals` with their session).
5. **Prem — dry run the rollout** with the production `DATABASE_URL`:
   `isolation_rollout.py --internal-org org_<FondokInternal> --fold-org …
   --deal-id …` (§9). Read the plan: every listed deal found; no "NOT FOUND";
   no "also owns N deal(s) NOT in your list" you did not expect; the
   `document_chunks` / `audit_log` notes understood (§6 on the audit trigger).
6. **Prem — execute the DB steps:** same command `--execute`. Expect
   `registered org_… → <uuid> kind=internal status=active` and `moved N`
   per table. Re-run the dry run: the source tenants now count 0.
7. **Sam — sign in with Fondok Internal active.** `/projects` lists the
   folded deals; each opens; the Data Room shows its documents. If a deal
   is missing, stop and tell Prem (nothing has been enforced yet).
8. **Prem — Clerk webhook.** The instance needs a Svix app first
   (`clerk api /webhooks/svix -X POST --yes …`, once); then
   `clerk api /webhooks/svix_url -X POST --yes …` returns a one-time Svix
   dashboard link. In it add an endpoint `https://<worker>/webhooks/clerk`
   for `organization.created/updated/deleted` and copy its `whsec_…` into
   `apps/worker/.env.prod` (never into chat); push it to Railway as
   `CLERK_WEBHOOK_SECRET` (`railway variables --set … --service fondok-worker`),
   redeploy, send the dashboard test event → `200 {"ok": true, …}`.
9. **Prem — flip `REQUIRE_REGISTERED_TENANT=1`** on Railway. Verify
   `/health.auth.registered_tenant_required: true`.
10. **Sam — re-check step 7.** Then, as a throwaway Clerk org (or a user
    with no org), confirm the app shows "No workspace assigned" / the
    worker answers `403 tenant_not_registered`.
11. **Prem — run the HTTP proof against production:** `isolation_check.py
    --base-url https://<worker> --allow-writes` with a Fondok Internal JWT
    as firm A and a second registered org's JWT as firm B. Expect
    `RESULT: PASS`, `LEAK=0 ERROR=0`. Attach the table (or `--json`) to the
    Linear ticket.
12. **Prem — provision firm B for the browser proof** (`FONDOK_SMOKE_B_*`
    repository secrets) and run `pnpm test:e2e:prod`; `02-two-firm-isolation`
    must pass, not skip.
13. **Later, separate change window — RLS** (§7): apply
    `001_enable_rls.sql`, verify as `fondok_app`, set `DB_SET_TENANT_GUC=1`,
    switch the DSN, re-run step 11.

Rollback at any point before step 13: unset `REQUIRE_REGISTERED_TENANT`
(step 9) — the registry and the folded rows are harmless with enforcement
off.

## 11. Not covered here

* Clerk-side membership (who is in which org) — Clerk dashboard.
* Per-tenant storage prefixes in S3 — paths already embed `tenant_id`;
  bucket-policy separation is a separate ticket.
* The premium instance-per-customer tier — `SECURITY_ARCHITECTURE.md` §2.
