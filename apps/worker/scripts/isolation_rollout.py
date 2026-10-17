"""Sequence the FON-93 Phase-3 runbook (docs/WORKSPACE_ISOLATION.md §5, §6, §10).

Dry run by default: prints what each step WOULD do against the database
in ``DATABASE_URL`` and writes nothing. ``--execute`` performs the
**database** steps only — registering the internal tenant and folding
the listed deals' tenants into it, through the same code paths as
``scripts/register_tenant.py`` and ``scripts/reassign_default_tenant_rows.py``.
Clerk dashboard changes and Railway variable flips are never performed:
they are printed as instructions, in order, with the ``/health`` fields
that must read true afterwards.

Inputs
------
* ``--internal-org org_…``  the Clerk org that becomes ``kind=internal``
  ("Fondok Internal"). Required.
* ``--name``                its display name (default "Fondok Internal").
* ``--deal-id UUID``        repeatable — deals that must end up inside the
  internal tenant (Sam / Eshan / Rani's deals). Each is located under a
  known tenant with a tenant-predicated lookup; a deal found under no
  known tenant is reported, never guessed.
* ``--fold-org org_…`` / ``--fold-tenant UUID``  repeatable — the old
  single-member orgs (or their tenant UUIDs) whose rows are candidates to
  fold. The all-zeros catch-all and every registry row are always
  candidates.
* ``--created-after`` / ``--created-before``  forwarded to the reassign
  plan (per-table timestamp columns, see that script).

For each source tenant that owns at least one listed deal the plan shows
the per-table row counts the reassign script would move. The reassign
script moves a WHOLE tenant, so when a source tenant also owns deals that
are NOT in the list the plan says so and ``--execute`` refuses that
tenant unless ``--move-whole-tenant`` is given (or a window excludes the
other rows). Nothing is moved while any table cannot be counted (the
reassign script's own fail-closed rule).

Usage (from ``apps/worker``, against ``DATABASE_URL``)::

    # plan only (default)
    uv run python scripts/isolation_rollout.py \\
        --internal-org org_FondokInternal \\
        --fold-org org_SamOld --fold-org org_EshanOld \\
        --deal-id 7c1c… --deal-id 9ab0…

    # perform the DB steps; Clerk + Railway steps are printed, not done
    uv run python scripts/isolation_rollout.py … --execute

Exit codes: 0 plan printed / executed; 2 bad arguments; 3 a table could
not be counted (nothing moved); 1 a write failed (rows it names stayed).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.api.deals import coerce_org_tenant_id
from app.config import get_settings
from app.database import get_engine
from app.tenants import Tenant, get_tenant, list_tenants, upsert_tenant

_HERE = Path(__file__).resolve().parent
DEFAULT_TENANT = UUID("00000000-0000-0000-0000-000000000000")

# Clerk-side variables the worker reads (apps/worker/app/config.py). Values
# are never printed — presence only.
CLERK_ENV_VARS: tuple[tuple[str, str], ...] = (
    ("CLERK_JWKS_URL", "JWT verification (JWKS) — required for any JWT traffic"),
    ("CLERK_SECRET_KEY", "optional; the worker verifies with JWKS, not the secret"),
    ("CLERK_JWT_ISSUER", "optional issuer pin"),
    ("CLERK_JWT_AUDIENCE", "optional audience pin"),
    ("CLERK_WEBHOOK_SECRET", "enables POST /webhooks/clerk (step 6)"),
)

# Flag → value it must have on the deployed worker after Phase 3.
TARGET_FLAGS: tuple[tuple[str, str, str], ...] = (
    ("ALLOW_TENANT_HEADER_WITHOUT_JWT", "0 / unset", "/health.auth.header_tenant_path_enabled == false, jwt_required == true"),
    ("REQUIRE_REGISTERED_TENANT", "1", "/health.auth.registered_tenant_required == true"),
    ("CLERK_WEBHOOK_SECRET", "whsec_… (set)", "POST /webhooks/clerk test event → 200 {\"ok\": true}"),
    ("DB_SET_TENANT_GUC", "1 — LATER, with RLS (§7)", "/health.auth.rls_tenant_guc == true"),
    ("DATABASE_URL_MIGRATIONS", "owner DSN — LATER, with RLS (§7), set BEFORE switching DATABASE_URL to fondok_app", "/health.db_migrations_dsn == \"owner\""),
)


def _load_reassign_module():
    """The reassign script, loaded from disk so its plan/move code is reused
    verbatim (it has no package)."""
    path = _HERE / "reassign_default_tenant_rows.py"
    spec = importlib.util.spec_from_file_location("reassign_default_tenant_rows", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("reassign_default_tenant_rows", mod)
    spec.loader.exec_module(mod)
    return mod


@dataclass
class DealRow:
    id: str
    name: str
    tenant_id: str
    status: str | None


@dataclass
class SourcePlan:
    tenant_id: UUID
    label: str
    listed_deals: list[DealRow]
    other_deal_count: int
    table_counts: list[tuple[str, int, int]]
    errors: list[tuple[str, str]]

    @property
    def rows_to_move(self) -> int:
        return sum(n for _t, n, _m in self.table_counts)


# ─────────────────────────── lookups (tenant-predicated) ───────────────────────────


async def deals_under_tenant(engine, tenant_id: UUID, deal_ids: list[str]) -> list[DealRow]:
    """The listed deals that live under ``tenant_id``. The predicate is the
    tenant — an operator never reads a deal row without naming its tenant."""
    if not deal_ids:
        return []
    placeholders = ", ".join(f":d{i}" for i in range(len(deal_ids)))
    params: dict[str, Any] = {"tenant": str(tenant_id)}
    params.update({f"d{i}": d for i, d in enumerate(deal_ids)})
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    f"SELECT id, name, tenant_id, status FROM deals "
                    f"WHERE tenant_id = :tenant AND id IN ({placeholders})"
                ),
                params,
            )
        ).fetchall()
    return [DealRow(str(r[0]), str(r[1] or ""), str(r[2]), r[3]) for r in rows]


async def existing_tables(engine) -> set[str]:
    """Table names present in this database. The canonical tenant-scoped list
    includes ``document_chunks`` (Postgres-only, pgvector); a table that does
    not exist has no rows to move, so it is reported as absent and left out
    of the plan rather than failing the count pass. (The reassign script's
    fail-closed rule is for a table name the OPERATOR typed; here the list is
    the script's own.)"""
    from sqlalchemy import inspect

    async with engine.connect() as conn:
        names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
    return set(names)


async def count_other_deals(engine, tenant_id: UUID, listed: list[str]) -> int:
    params: dict[str, Any] = {"tenant": str(tenant_id)}
    sql = "SELECT COUNT(*) FROM deals WHERE tenant_id = :tenant"
    if listed:
        placeholders = ", ".join(f":d{i}" for i in range(len(listed)))
        params.update({f"d{i}": d for i, d in enumerate(listed)})
        sql += f" AND id NOT IN ({placeholders})"
    async with engine.connect() as conn:
        return int((await conn.execute(text(sql), params)).scalar_one() or 0)


# ─────────────────────────── plan ───────────────────────────


def _present(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def print_environment(settings, out) -> None:
    print("=== 0. Environment (presence only — values never printed) ===", file=out)
    db = settings.async_database_url
    print(f"  database        : {db.split('@')[-1] if '@' in db else db}", file=out)
    for name, why in CLERK_ENV_VARS:
        print(f"  {name:28s} {'present' if _present(name) else 'ABSENT ':8s} — {why}", file=out)
    print("  current flags   :", file=out)
    print(f"    ALLOW_TENANT_HEADER_WITHOUT_JWT = {settings.ALLOW_TENANT_HEADER_WITHOUT_JWT}", file=out)
    print(f"    REQUIRE_REGISTERED_TENANT       = {settings.REQUIRE_REGISTERED_TENANT}", file=out)
    print(f"    DB_SET_TENANT_GUC               = {settings.DB_SET_TENANT_GUC}", file=out)
    if settings.ALLOW_TENANT_HEADER_WITHOUT_JWT:
        print("    WARNING: the header tenant path is OPEN in this environment — fine locally, never on Railway.", file=out)


def print_registry(rows: list[Tenant], out) -> None:
    print("\n=== 1. Registry (tenants table) ===", file=out)
    if not rows:
        print("  (empty — nobody is registered yet)", file=out)
        return
    print(f"  {'tenant_id':38s} {'kind':9s} {'status':15s} {'clerk_org_id':34s} name", file=out)
    for t in rows:
        print(f"  {t.id!s:38s} {t.kind:9s} {t.status:15s} {t.clerk_org_id:34s} {t.name}", file=out)


def print_register_step(existing: Tenant | None, org: str, tenant_id: UUID, name: str, out) -> None:
    print("\n=== 2. register_tenant.py — Fondok Internal ===", file=out)
    print(f"  clerk org  : {org}", file=out)
    print(f"  tenant_id  : {tenant_id}  (UUIDv5 — already stamped on every row this org has written)", file=out)
    if existing is None:
        print(f"  WOULD INSERT tenants(id={tenant_id}, clerk_org_id={org}, name={name!r}, kind=internal, status=active)", file=out)
    else:
        changes = [
            f"{k}: {getattr(existing, k)!r} → {v!r}"
            for k, v in (("name", name), ("kind", "internal"), ("status", "active"))
            if getattr(existing, k) != v
        ]
        if changes:
            print(f"  WOULD UPDATE existing row: {', '.join(changes)}", file=out)
        else:
            print("  already registered as kind=internal status=active — no change", file=out)
    print(f"  equivalent : uv run python scripts/register_tenant.py --clerk-org {org} --name {name!r} --kind internal", file=out)


def print_source_plans(
    plans: list[SourcePlan],
    unresolved: list[str],
    target: UUID,
    windowed: bool,
    reassign,
    out,
    *,
    absent: list[str] | None = None,
) -> None:
    print("\n=== 3. reassign_default_tenant_rows.py — fold deals into Fondok Internal ===", file=out)
    print(f"  target tenant: {target}", file=out)
    if absent:
        print(
            f"  tables not in this database (no rows to move, left out of the plan): {', '.join(absent)}",
            file=out,
        )
    if unresolved:
        print(f"  NOT FOUND under any known tenant ({len(unresolved)}): {', '.join(unresolved)}", file=out)
        print("    → pass --fold-org <org_…> for the org that owns each, or check the id. Nothing is guessed.", file=out)
    if not plans:
        print("  no source tenant owns a listed deal — nothing to fold", file=out)
        return
    for p in plans:
        print(f"\n  source tenant {p.tenant_id}  ({p.label})", file=out)
        for d in p.listed_deals:
            print(f"    listed deal  {d.id}  {d.name!r}  status={d.status}", file=out)
        if p.other_deal_count:
            print(
                f"    WARNING: this tenant also owns {p.other_deal_count} deal(s) NOT in your list. "
                "The reassign script moves the WHOLE tenant — add --created-after/--created-before, "
                "list those deals too, or pass --move-whole-tenant to accept.",
                file=out,
            )
        print(f"    {'table':30s}  {'window col':>12s}  {'rows on source':>14s}  {'rows on target':>14s}", file=out)
        for table, n_src, n_tgt in p.table_counts:
            col = reassign.TABLE_TIMESTAMP_COLUMNS.get(table, "?") if windowed else "-"
            print(f"    {table:30s}  {col:>12s}  {n_src:>14,}  {n_tgt:>14,}", file=out)
        for table, err in p.errors:
            print(f"    {table:30s}  ERROR — {err}", file=out)
        print(f"    rows that would move: {p.rows_to_move:,}", file=out)
        print(
            f"    equivalent : uv run python scripts/reassign_default_tenant_rows.py "
            f"--from-tenant {p.tenant_id} --clerk-org <internal org> [--commit]",
            file=out,
        )
    print(
        "\n  note: audit_log has an append-only trigger on Postgres — its UPDATE counts here but fails at "
        "write time unless the trigger is disabled for the move (docs/WORKSPACE_ISOLATION.md §6).",
        file=out,
    )


def print_hand_steps(org: str, out) -> None:
    print("\n=== 4. Clerk dashboard — BY HAND, never performed by this script ===", file=out)
    print("  a. Organizations settings → turn OFF \"Allow users to create organizations\".", file=out)
    print(f"  b. Confirm the org {org} exists and Sam, Eshan, Rani and Prem are members.", file=out)
    print("  c. Webhooks → add https://<worker>/webhooks/clerk for organization.created / .updated / .deleted;", file=out)
    print("     copy its signing secret (whsec_…) into Railway as CLERK_WEBHOOK_SECRET; redeploy; send the test event.", file=out)
    print("\n=== 5. Railway → fondok-worker variables — BY HAND, in this order ===", file=out)
    for flag, value, health in TARGET_FLAGS:
        print(f"  {flag:32s} = {value:26s} → verify {health}", file=out)
    print("\n=== 6. Verify ===", file=out)
    print("  curl -s https://<worker>/health | python -m json.tool  # the .auth block above", file=out)
    print("  FIRM_A_JWT=… FIRM_B_JWT=… uv run python scripts/isolation_check.py --base-url https://<worker>", file=out)
    print("  a Fondok Internal session lists its deals; a throwaway Clerk org gets 403 tenant_not_registered", file=out)


# ─────────────────────────── main ───────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--internal-org", required=True, help="Clerk org id (org_…) that becomes kind=internal")
    p.add_argument("--name", default="Fondok Internal")
    p.add_argument("--deal-id", action="append", default=[], help="deal that must end up in the internal tenant (repeatable)")
    p.add_argument("--fold-org", action="append", default=[], help="old Clerk org whose tenant is a fold candidate (repeatable)")
    p.add_argument("--fold-tenant", action="append", default=[], help="old tenant UUID that is a fold candidate (repeatable)")
    p.add_argument("--created-after", default=None)
    p.add_argument("--created-before", default=None)
    p.add_argument("--move-whole-tenant", action="store_true",
                   help="with --execute: fold a source tenant even when it owns deals not in --deal-id")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", default=True, help="(default) print the plan, write nothing")
    g.add_argument("--execute", action="store_true", help="perform the DB steps (register + fold); print the rest")
    return p


async def run(args: argparse.Namespace, out=sys.stdout) -> int:
    execute = bool(args.execute)
    settings = get_settings()
    reassign = _load_reassign_module()

    target = coerce_org_tenant_id(args.internal_org)
    if target is None:
        print(f"error: {args.internal_org!r} is not a Clerk organization id (org_…)", file=sys.stderr)
        return 2
    for d in args.deal_id:
        try:
            UUID(d)
        except ValueError:
            print(f"error: --deal-id {d!r} is not a UUID", file=sys.stderr)
            return 2
    for raw in (args.created_after, args.created_before):
        if raw is not None:
            try:
                reassign.parse_window_bound(raw)
            except ValueError:
                print(f"error: window bound {raw!r} is not an ISO date/datetime", file=sys.stderr)
                return 2

    print("=" * 78, file=out)
    print(f"Fondok workspace isolation — Phase 3 rollout  [{'EXECUTE (DB steps)' if execute else 'DRY RUN — nothing is written'}]", file=out)
    print("=" * 78, file=out)

    print_environment(settings, out)

    # Never run migrations from an operator's laptop: the deployed worker
    # applies them at boot (2026-10-17: the dry run used to call
    # run_startup_migrations() against production, which is a write path).
    # Require the schema to be current instead, and in dry-run mode make
    # every connection read-only so a bug here cannot write anything.
    engine = get_engine()
    if not execute:
        from sqlalchemy import event

        def _read_only(dbapi_conn, _record):  # pragma: no cover - driver glue
            raw = getattr(dbapi_conn, "_connection", None)
            if raw is not None and hasattr(dbapi_conn, "await_"):
                dbapi_conn.await_(raw.execute("SET default_transaction_read_only = on"))
            else:  # sqlite: no server setting; the plan issues SELECTs only
                return None

        event.listen(engine.sync_engine, "connect", _read_only)
    tables = await existing_tables(engine)
    if "tenants" not in tables or "deals" not in tables:
        print(
            "ERROR: this database has no `tenants`/`deals` table. The deployed worker "
            "creates them at boot — deploy (or run the worker once against this DATABASE_URL) "
            "and retry. This script does not run migrations.",
            file=out,
        )
        raise SystemExit(2)

    registry = await list_tenants()
    print_registry(registry, out)

    existing = await get_tenant(target, use_cache=False)
    print_register_step(existing, args.internal_org, target, args.name, out)

    # Fold candidates: registry rows + catch-all + explicit --fold-*.
    candidates: dict[UUID, str] = {t.id: f"registry: {t.clerk_org_id} {t.name!r}" for t in registry}
    candidates.setdefault(DEFAULT_TENANT, "all-zeros catch-all (pre-Clerk traffic)")
    for org in args.fold_org:
        tid = coerce_org_tenant_id(org)
        if tid is None:
            print(f"error: --fold-org {org!r} is not a Clerk organization id", file=sys.stderr)
            return 2
        candidates.setdefault(tid, f"--fold-org {org}")
    for raw in args.fold_tenant:
        try:
            candidates.setdefault(UUID(raw), f"--fold-tenant {raw}")
        except ValueError:
            print(f"error: --fold-tenant {raw!r} is not a UUID", file=sys.stderr)
            return 2
    candidates.pop(target, None)  # rows already home are not moved

    present = await existing_tables(engine)
    tables = [t for t in reassign.TENANT_SCOPED_TABLES if t in present]
    absent = [t for t in reassign.TENANT_SCOPED_TABLES if t not in present]

    plans: list[SourcePlan] = []
    found: set[str] = set()
    for tid, label in candidates.items():
        listed = await deals_under_tenant(engine, tid, args.deal_id)
        if not listed:
            continue
        found.update(d.id for d in listed)
        others = await count_other_deals(engine, tid, [d.id for d in listed])
        counts, errors = await reassign.plan_counts(
            engine, tables, source=tid, target=target,
            since=args.created_after, until=args.created_before,
        )
        plans.append(SourcePlan(tid, label, listed, others, counts, errors))
    unresolved = [d for d in args.deal_id if d not in found]
    windowed = bool(args.created_after or args.created_before)
    print_source_plans(plans, unresolved, target, windowed, reassign, out, absent=absent)

    print_hand_steps(args.internal_org, out)

    if not execute:
        print("\nDRY RUN — nothing was written. Re-run with --execute to perform steps 2 and 3 (DB only).", file=out)
        return 0

    # ── execute: DB steps only ──
    if any(p.errors for p in plans):
        print("\nERROR: a table could not be counted — refusing to move anything (fail-closed).", file=sys.stderr)
        return 3
    if unresolved:
        print(f"\nERROR: {len(unresolved)} listed deal(s) were not found under any known tenant — refusing to execute.", file=sys.stderr)
        return 2
    blocked = [p for p in plans if p.other_deal_count and not args.move_whole_tenant]
    if blocked:
        print(
            "\nERROR: these source tenants own deals not in your list; pass --move-whole-tenant to fold them anyway "
            "or narrow with a window: " + ", ".join(str(p.tenant_id) for p in blocked),
            file=sys.stderr,
        )
        return 2

    print("\n=== EXECUTING (database only) ===", file=out)
    stored = await upsert_tenant(
        tenant_id=target, clerk_org_id=args.internal_org, name=args.name, kind="internal", status="active"
    )
    print(f"  registered {stored.clerk_org_id} → {stored.id} kind={stored.kind} status={stored.status}", file=out)

    failed: list[str] = []
    for p in plans:
        print(f"  folding tenant {p.tenant_id} → {target}", file=out)
        for table, _n_src, _n_tgt in p.table_counts:
            try:
                n = await reassign._reassign(engine, table, p.tenant_id, target, args.created_after, args.created_before)
            except Exception as exc:  # audit_log's append-only trigger, etc.
                print(f"    {table:30s}  ERROR — {type(exc).__name__}: {exc}", file=out)
                failed.append(f"{p.tenant_id}:{table}")
                continue
            print(f"    {table:30s}  moved {n:,}", file=out)
    if failed:
        print(f"\nWARNING: {len(failed)} table move(s) failed; their rows are still on the source tenant: {', '.join(failed)}", file=sys.stderr)
        return 1
    print("\nDB steps done. Now do §4 (Clerk) and §5 (Railway) above by hand, then §6 to verify.", file=out)
    return 0


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
