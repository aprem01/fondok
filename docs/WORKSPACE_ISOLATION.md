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

The header path (`ALLOW_TENANT_HEADER_WITHOUT_JWT`) is unchanged: off by
default, on only in `tests/conftest.py` and a developer's local `.env`.

## 3. Staged behind settings (default off — production unchanged until flipped)

| Setting (Railway → `fondok-worker`) | Default | What it turns on |
| --- | --- | --- |
| `REQUIRE_REGISTERED_TENANT` | `False` | A JWT whose org is not an `active` row in `tenants` gets `403 tenant_not_registered`. |
| `DB_SET_TENANT_GUC` | `False` | Every request transaction runs `set_config('app.tenant_id', <caller>, true)` so Postgres RLS policies can see the caller. No-op on SQLite. |
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
* `POST /webhooks/clerk` — on `organization.created`, inserts
  `kind=external, status=pending_review`. Never downgrades an existing row.
  Svix signature verified with `CLERK_WEBHOOK_SECRET`; 5-minute replay
  window (`CLERK_WEBHOOK_TOLERANCE_S`).

The registry row's `id` **is** the tenant UUID already stored on every
existing row for that org, so registering an org that has been using the
product attaches its history with no data movement.

### Lookups

`app/tenants.py` caches rows for 30 s in-process. Writes clear the cache.
On a DB that predates the migration the lookup degrades to "not
registered" and logs — the worker never 500s; it only refuses once
`REQUIRE_REGISTERED_TENANT` is on, which is the operator's explicit choice.

## 5. Rollout order (Prem, by hand)

Do these in order; each step is independently reversible.

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
   `https://<worker>/webhooks/clerk` subscribed to `organization.created`
   (optionally `organization.updated`). Copy its signing secret
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
# 2. dry run: counts per table that would move (default is dry-run)
uv run python scripts/reassign_default_tenant_rows.py \
    --from-tenant <old uuid> --clerk-org org_<FondokInternal>
# 3. apply
uv run python scripts/reassign_default_tenant_rows.py \
    --from-tenant <old uuid> --clerk-org org_<FondokInternal> --commit
```

`--from-tenant` and the complete table list (22 tables — it used to miss
nine) were added to the script in FON-93. Each table is moved in its own
transaction; a failure on one table prints and continues, so re-run until
every row reads `moved 0`.

`audit_log` has an append-only trigger (`audit_log_no_update_delete`), so
its `UPDATE` will print an ERROR and be skipped. Either leave history
under the old tenant (the Activity Feed for those deals starts fresh in
Fondok Internal) or, as the owner role inside one transaction,
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
  `USING (tenant_id = current_setting('app.tenant_id', true)::uuid)` with
  the same `WITH CHECK`. Idempotent; **not** run at startup.
* `app/database.py::get_session` — when `DB_SET_TENANT_GUC` is on and the
  DSN is Postgres, attaches an `after_begin` hook that runs
  `set_config('app.tenant_id', <tenant>, true)` at the start of **every**
  transaction. The hook (not a one-shot statement) is deliberate: a route
  that commits mid-request autobegins a new transaction, and the GUC must
  be re-published or RLS would return zero rows for the rest of the request.
  The tenant comes from the `current_tenant_id` ContextVar set by
  `get_current_auth` on every path.

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

The digest scheduler (`services/digest_scheduler.py`) and the Anthropic
batch poller (`agents/analyst_batch.py`) scan **all** tenants with no
request context, so under RLS as `fondok_app` they would see nothing. Two
options, pick one before step 7.4: (a) run them under the owner DSN, or
(b) have them `set_config` per tenant as they iterate (they already carry
`tenant_id` on every row they touch).

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

## 8. Not covered here

* Clerk-side membership (who is in which org) — Clerk dashboard.
* Per-tenant storage prefixes in S3 — paths already embed `tenant_id`;
  bucket-policy separation is a separate ticket.
* The premium instance-per-customer tier — `SECURITY_ARCHITECTURE.md` §2.
