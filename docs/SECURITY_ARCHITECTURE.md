# Fondok Security Architecture

This document is the authoritative reference for Fondok's security
posture. It is written so a procurement / IT-security reviewer at
Brookfield, KSL, Blackstone, or Apollo can sign off on shared
multi-tenancy without a follow-up call. Everything here is shipped
code or a roadmap commitment with a date — nothing is aspirational
marketing.

## 1. Data classification

Fondok ingests and processes the following data classes per tenant:

| Class | Examples | Sensitivity |
| --- | --- | --- |
| **Deal metadata** | Property name, city, brand, keys, sourcing channel, purchase price | Confidential — competitively sensitive but not regulated |
| **Financial proformas** | OM / broker proforma extracts, T-12 actuals, HOST benchmark deltas | Confidential — deal-team-only by convention |
| **Broker correspondence** | Broker Q&A pairs, due-diligence questions, analyst notes | Confidential |
| **Engine outputs** | Deterministic revenue / expense / capital / debt / returns model results | Confidential |
| **LLM context** | Prompt-cached system + catalog payloads; never customer-specific PII in the cacheable prefix | Internal |

What we explicitly DO NOT hold:
* PCI data (no payment cards)
* PHI (no healthcare)
* SSN / driver-license / passport (no consumer KYC)
* Brokers' internal pricing models beyond what they share in OM PDFs

## 2. Tenancy model

Fondok runs as a **shared-database, tenant-scoped** SaaS today. Every
row in every business table carries a NOT NULL `tenant_id` column.
The application enforces isolation in three layers of defense:

1. **Endpoint layer** — Every `/deals/{deal_id}/...` route resolves
   the caller's tenant via the `Depends(get_tenant_id)` FastAPI
   dependency (reads `X-Tenant-Id` from the request, validated
   against the Clerk session by the web tier). The route then either
   filters the SQL by `tenant_id` directly or calls the
   `_assert_deal_belongs_to_tenant` helper, which returns **404
   (not 403)** for any cross-tenant access. 404 leaks the least
   information about whether the deal exists.

2. **Test layer** — `apps/worker/tests/test_tenant_isolation_comprehensive.py`
   exercises every deal-scoped endpoint against two fixture tenants
   and asserts that Tenant A cannot read, mutate, or trigger work on
   Tenant B's deal_id. The suite is data-driven so adding a new
   endpoint requires one new line in `ENDPOINT_CASES`.

3. **Database layer** — `apps/worker/app/tenant_middleware.py`
   attaches a SQLAlchemy `before_cursor_execute` listener that
   inspects every SQL statement. If a query touches one of the
   tenant-scoped tables (`deals`, `documents`, `extraction_results`,
   `audit_log`, `memo_edits`, `document_chunks`, `broker_questions`,
   `broker_qa_pairs`, `verification_reports`, `critic_findings`,
   `critic_reports`, `engine_outputs`, `due_diligence_questions`) and
   the WHERE clause does not reference `tenant_id`, the listener
   logs CRITICAL and (in `STRICT_TENANT_ENFORCEMENT=raise` mode) raises
   `MissingTenantFilterError`. In production we fail-open with
   telemetry; tests run in `raise` mode so a forgotten filter
   surfaces immediately in CI.

### Premium "instance-per-customer" tier (roadmap)

For enterprise contracts that require physical separation, Fondok
will offer a **dedicated-instance** tier: a separate database +
worker pod per tenant, behind a customer-specific subdomain. The
scoping logic and audit trail are identical to the shared tier;
the only difference is the network and storage perimeter. This is
gated on signed contract; not built into the codebase until needed.

## 3. Defense in depth — proof points

| Layer | Mechanism | Evidence |
| --- | --- | --- |
| 1. Routing | Web tier (`apps/web/src/lib/api.ts`) forwards `X-Tenant-Id` from the active Clerk Organization on every API call | `apps/web/src/lib/api.ts` |
| 2. FastAPI dep | `Depends(get_tenant_id)` resolves the header into a UUID; malformed values fall back to `DEFAULT_TENANT_ID` (demo persona) — never raise | `apps/worker/app/api/deals.py::get_tenant_id` |
| 3. Per-deal gate | `_assert_deal_belongs_to_tenant` returns 404 if `(deal_id, tenant_id)` is not in `deals` | `apps/worker/app/api/deals.py::_assert_deal_belongs_to_tenant` |
| 4. SQL predicate | Every business query filters on `tenant_id` (verified by the safety listener) | `apps/worker/app/api/{deals,documents,analysis,model,market,due_diligence,dossier}.py` |
| 5. Comprehensive tests | 50+ data-driven cross-tenant probes, plus listener self-tests | `apps/worker/tests/test_tenant_isolation_comprehensive.py` |
| 6. SQLAlchemy listener | Catches developer omission at the cursor layer | `apps/worker/app/tenant_middleware.py` |
| 7. Audit log scoping | Sole sanctioned reader is `list_audit_log` which rejects falsy `tenant_id` with `ValueError` | `apps/worker/app/audit.py::list_audit_log` |

## 4. Audit logging

Every state-changing operation writes one row to `audit_log` via
`log_audit`. The row carries:

* `tenant_id` + `actor_id` + `resource_type` + `resource_id`
* SHA-256 of the canonical-JSON input payload (`input_hash`)
* SHA-256 of the canonical-JSON output payload (`output_hash`)
* The full payload (JSONB on Postgres) for forensic replay
* `created_at` (UTC, `TIMESTAMPTZ`)

The Postgres table is enforced **append-only** by a trigger
(`audit_log_block_mutation`) that raises on `UPDATE` / `DELETE` —
even with full DB credentials, audit rows cannot be silently
rewritten. SQLite (dev only) keeps the same shape without the
trigger.

The defensive `list_audit_log` helper is the only sanctioned reader.
Its signature makes `tenant_id` keyword-only and required; a falsy
value raises `ValueError` rather than executing an unscoped query.
There is no UI for the audit log today (June 2026); when one ships
it MUST go through `list_audit_log`.

## 5. Access control

* **Authentication** — Clerk handles user identity. Sessions are
  cookie-based, refreshed automatically.
* **Tenancy** — Clerk Organizations map 1:1 to Fondok tenants. The
  active organization id is the source of truth for `X-Tenant-Id`.
* **Authorization** — Within a tenant, every authenticated user has
  full access (no per-deal RBAC yet). The internal Fondok team is
  single-tenant-per-user; cross-tenant RBAC is not a current
  requirement. When a tenant requests per-role permissions (e.g.
  analyst vs IC member), it will plug into Clerk's role API.
* **Demo mode** — When `X-Tenant-Id` is absent, the worker falls
  back to `DEFAULT_TENANT_ID` so the unauthenticated marketing demo
  keeps working. This tenant carries fixture data only and is
  isolated from any real customer tenant by the same boundary as
  every other tenant.

## 6. Data residency

* **Application + worker** — Railway (us-east-1, Virginia)
* **Web tier** — Vercel (us-east-1)
* **Database** — NeonDB Postgres (us-east-1)
* **Document storage** — Railway-attached volume; planned migration
  to a per-tenant S3 bucket gated on enterprise contract.
* **Embedding store** — Postgres `pgvector` extension, co-located.
* **LLM providers** — Anthropic (Claude Opus 4.7 + Sonnet),
  OpenAI (Whisper for audio), Voyage AI (embeddings). All
  invocations leave us-east-1 and return via TLS 1.3; no data is
  written outside us-east-1 by the worker.

## 7. Encryption

* **In transit** — TLS 1.3 enforced end-to-end by Vercel (web tier),
  Railway (worker), and NeonDB.
* **At rest** — NeonDB encrypts every volume with AES-256. Document
  storage volumes are encrypted at the Railway level.
* **Field-level encryption** — Not currently in scope. No data field
  in the current schema meets the threshold (no SSN, no payment
  data, no protected health information). When a customer requires
  field-level encryption for a specific category (e.g. broker
  contact info), the path is Postgres `pgcrypto`.

## 8. LLM data handling

* **No training on customer data** — Anthropic and Voyage AI
  workspace agreements explicitly exclude our traffic from any model
  training pipeline. Confirmed annually with the vendor.
* **Prompt caching** — Anthropic's prompt cache is used only for
  the system prompt + USALI catalog payload (both tenant-agnostic).
  Per-deal context is sent uncached on every call so no tenant data
  ever sits in a cached prefix.
* **Provider isolation** — All LLM calls go through
  `apps/worker/app/llm.py`, a single chokepoint that enforces the
  no-training flag at request time and tags the call with the
  tenant id for cost attribution + audit.

## 9. Incident response

* **Runtime errors** — Sentry captures every exception in worker
  and web tier. Tenant id is attached as a tag so per-tenant
  incident counts are queryable.
* **Forensic trail** — `audit_log` is append-only and tamper-evident
  via the SHA-256 hashes. Replay of any caller's actions is a single
  `list_audit_log` call.
* **Cross-tenant violation alerting** — The SQLAlchemy listener
  logs CRITICAL on every unscoped query. Sentry's `CRITICAL` filter
  routes those to PagerDuty in production. After 30 days of clean
  logs we flip the listener to `STRICT_TENANT_ENFORCEMENT=raise` in
  production, at which point a forgotten filter is a 5xx instead of
  a silent leak.
* **Customer notification** — Per the SLA in the master agreement,
  any confirmed cross-tenant access is disclosed to the affected
  tenants within 24 hours of detection along with the audit-log
  evidence trail.

## 10. Known gaps + roadmap

| Gap | Status | Target |
| --- | --- | --- |
| Premium instance-per-customer tier | Planned, contract-gated | When first enterprise contract signs |
| SOC 2 Type II | Planned | Q1 2027 (requires 6 months of runtime data) |
| Row-level security at the Postgres layer | Planned | Q4 2026, after 30 days of clean tenant-middleware logs in prod |
| Per-deal RBAC (analyst vs IC) | Planned | When a tenant requests it; plugs into Clerk roles |
| Field-level encryption (pgcrypto) | Conditional | When a customer requires it for a specific column |
| Per-tenant S3 storage isolation | Planned | Alongside instance-per-customer tier |
| Customer-managed keys (CMK) | Not planned | Available via the instance-per-customer tier (BYOK) |

## 11. Subdomain vs path-prefix tenant routing (pending decision)

Sam owns the routing-strategy decision (open question #9 in the
June 2026 product email). The shipped code supports both shapes —
the `X-Tenant-Id` header is the load-bearing contract, and the
Vercel routing layer can map either `tenant.fondok.app/deal/...` or
`fondok.app/{tenant}/deal/...` to the same backend. This document
will be updated with the chosen strategy once Sam answers.

This section explicitly does NOT block the rest of the security
posture — the routing layer rides on top of the tenant boundary,
not the other way around.

## 12. Out-of-scope for this document

* SOC 2 controls catalog (separate document, prepared with auditor)
* Penetration test reports (engaged annually, NDA-gated)
* Vendor security questionnaires (Anthropic, Clerk, Neon, Railway,
  Vercel — all available on request)
* Disaster recovery / business continuity plan (separate runbook
  in `docs/RUNBOOK.md`)

## References

* `apps/worker/app/tenant_middleware.py` — SQLAlchemy safety listener
* `apps/worker/app/audit.py` — Append-only audit log + `list_audit_log`
* `apps/worker/app/api/deals.py::_assert_deal_belongs_to_tenant` — Per-deal 404 gate
* `apps/worker/tests/test_tenant_isolation_comprehensive.py` — Cross-tenant test suite
* `apps/worker/app/migrations.py` — Schema definitions with `tenant_id` columns
* Commit `2a8ed64` — P0 fix that hardened eight pre-existing leak points
* Commit `<this branch>` — Wave 1 #9: tenant-isolation hardening
