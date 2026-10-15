"""Reassign rows tagged with the default catch-all tenant to a real
Clerk org's UUIDv5.

Backstory — Sam QA 2026-07-02: before commit 796df73 the worker's
`get_tenant_id` rejected Clerk-style `org_...` X-Tenant-Id headers as
malformed UUIDs and fell back to `DEFAULT_TENANT_ID`
(``00000000-0000-0000-0000-000000000000``). So months of real user
traffic ended up tagged with the default tenant. Now that new traffic
maps to each Clerk org's deterministic UUIDv5, this script one-shots
the backfill so the historical rows show up under the real tenant
too.

Safety knobs:
* --dry-run     — prints counts, changes nothing (default: on)
* --clerk-org   — the Clerk org id whose data to migrate (required)
* --from-tenant — the SOURCE tenant UUID (default: the all-zeros catch-all).
                  FON-93: pass an old single-member org's UUIDv5 to fold
                  its rows into a shared institution org (e.g. Sam/Eshan/
                  Rani → "Fondok Internal"); see docs/WORKSPACE_ISOLATION.md §6
* --created-after / --created-before — restrict by row timestamp
                  (recommended: set --created-after to the date your
                   Clerk org went live so pre-Clerk seed rows stay put).
                  The timestamp column differs per table — ``documents``
                  has ``uploaded_at``, ``engine_outputs`` ``started_at``,
                  ``pending_batches`` ``submitted_at`` — see
                  ``TABLE_TIMESTAMP_COLUMNS``. Before 2026-10-14 the script
                  filtered every table on ``created_at``, so with a window
                  set the ``documents`` count errored and the table was
                  SKIPPED while deals / extractions / audit rows moved: the
                  folded deals opened with an empty Data Room.
* --tables      — comma-separated list; default is all tenant-scoped
                  tables discovered at import time

Fail-closed: the count pass runs first and prints a per-table plan. If ANY
table errors (unknown table, missing column, no timestamp mapping) the run
stops with exit code 3 and ``--commit`` is refused — fix the list, or pass
``--tables`` without the offending table on purpose. A partial move is
exactly the bug this guards against.

Usage:
    # dry run first — always
    uv run python scripts/reassign_default_tenant_rows.py \
        --clerk-org org_3Cy8foYI4xm9PK2VNrApwzGmZk2 \
        --created-after 2026-06-01

    # once counts look right, --commit to apply
    uv run python scripts/reassign_default_tenant_rows.py \
        --clerk-org org_3Cy8foYI4xm9PK2VNrApwzGmZk2 \
        --created-after 2026-06-01 \
        --commit
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Reuse the coerce function so we can't drift from the runtime mapping.
from app.api.deals import _CLERK_ORG_UUID_NAMESPACE, _coerce_tenant_id
from app.config import get_settings

DEFAULT_TENANT = UUID("00000000-0000-0000-0000-000000000000")

# Every tenant-scoped table the schema knows about. If you add a
# tenant_id column somewhere new, add it here too — the script will
# refuse to migrate silently. (FON-93 completed the list: it used to miss
# nine tables, so a "full" migration left memo edits, critic reports,
# lineage and comments behind on the old tenant.)
TENANT_SCOPED_TABLES = [
    "deals",
    "documents",
    "template_mappings",
    "extraction_results",
    "document_chunks",
    "audit_log",
    "memo_edits",
    "verification_reports",
    "critic_findings",
    "critic_reports",
    "engine_outputs",
    "due_diligence_questions",
    "broker_questions",
    "broker_qa_pairs",
    "scenarios",
    "portfolio_library",
    "saved_pipeline_views",
    "pipeline_digest_schedules",
    "pending_batches",
    "lineage_records",
    "cell_comments",
    "model_calls",
]

# The row-timestamp column ``--created-after`` / ``--created-before`` filter
# on, per table. Most tables carry ``created_at``; the exceptions are listed
# explicitly. ``tests/test_reassign_tenant_rows_script.py`` checks every
# entry against the migrations (SQLite schema + Postgres DDL) so this map
# cannot drift from the real columns.
TABLE_TIMESTAMP_COLUMNS: dict[str, str] = {
    **{t: "created_at" for t in TENANT_SCOPED_TABLES},
    "documents": "uploaded_at",
    "engine_outputs": "started_at",
    "pending_batches": "submitted_at",
}


def timestamp_column(table: str) -> str:
    """The timestamp column the window filters ``table`` on.

    Raises ``KeyError`` for a table the map does not know: a table passed
    via ``--tables`` that is not in ``TENANT_SCOPED_TABLES`` must be added
    to ``TABLE_TIMESTAMP_COLUMNS`` (with its real column) before it can be
    windowed. The count pass turns that into a refused run.
    """
    return TABLE_TIMESTAMP_COLUMNS[table]


def _window_sql(table: str, since: str | None, until: str | None, params: dict) -> list[str]:
    """``WHERE`` fragments for the timestamp window, binding into ``params``."""
    where: list[str] = []
    if since or until:
        col = timestamp_column(table)
        if since:
            where.append(f"{col} >= :since")
            params["since"] = since
        if until:
            where.append(f"{col} < :until")
            params["until"] = until
    return where


async def _count(engine, table: str, tenant: UUID, since: str | None, until: str | None) -> int:
    """Rows on ``tenant`` within the window. Raises on any DB / map error —
    the caller turns that into a refused run, never a skipped table."""
    params: dict = {"tenant": str(tenant)}
    where = ["tenant_id = :tenant", *_window_sql(table, since, until, params)]
    sql = f"SELECT COUNT(*) FROM {table} WHERE {' AND '.join(where)}"
    async with engine.begin() as conn:
        row = (await conn.execute(text(sql), params)).scalar_one()
    return int(row or 0)


async def _reassign(engine, table: str, from_tenant: UUID, to_tenant: UUID, since: str | None, until: str | None) -> int:
    params: dict = {"from_tenant": str(from_tenant), "to_tenant": str(to_tenant)}
    where = ["tenant_id = :from_tenant", *_window_sql(table, since, until, params)]
    sql = f"UPDATE {table} SET tenant_id = :to_tenant WHERE {' AND '.join(where)}"
    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        return int(result.rowcount or 0)


async def plan_counts(
    engine,
    tables: list[str],
    *,
    source: UUID,
    target: UUID,
    since: str | None,
    until: str | None,
) -> tuple[list[tuple[str, int, int]], list[tuple[str, str]]]:
    """The count pass: ``(table, rows_on_source, rows_on_target)`` per table
    that counted, plus ``(table, error)`` for every table that could not be
    counted. Any entry in the second list means the run must not commit."""
    plan: list[tuple[str, int, int]] = []
    errors: list[tuple[str, str]] = []
    for table in tables:
        try:
            n_source = await _count(engine, table, source, since, until)
            n_target = await _count(engine, table, target, since, until)
        except Exception as exc:  # noqa: BLE001
            errors.append((table, f"{type(exc).__name__}: {exc}"))
            continue
        plan.append((table, n_source, n_target))
    return plan, errors


async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--clerk-org", required=True, help="Clerk org id like 'org_3Cy8foYI...'")
    p.add_argument(
        "--from-tenant",
        default=str(DEFAULT_TENANT),
        help="Source tenant UUID to move rows FROM (default: the all-zeros catch-all tenant)",
    )
    p.add_argument("--created-after", default=None, help="ISO date, e.g. 2026-06-01")
    p.add_argument("--created-before", default=None, help="ISO date, e.g. 2026-07-01")
    p.add_argument("--tables", default=",".join(TENANT_SCOPED_TABLES), help="Comma-separated table list")
    p.add_argument("--commit", action="store_true", help="Actually apply the update (default is dry-run)")
    args = p.parse_args()

    target_uuid = _coerce_tenant_id(args.clerk_org)
    if target_uuid is None:
        print(f"ERROR: could not coerce {args.clerk_org!r} to UUID. Is it the right Clerk id?", file=sys.stderr)
        return 2

    try:
        source_uuid = UUID(args.from_tenant)
    except ValueError:
        print(f"ERROR: --from-tenant {args.from_tenant!r} is not a UUID", file=sys.stderr)
        return 2
    if source_uuid == target_uuid:
        print("ERROR: source and target tenant are the same — nothing to do", file=sys.stderr)
        return 2

    tables = [t.strip() for t in args.tables.split(",") if t.strip()]

    print("=" * 70)
    print("Fondok default-tenant reassignment")
    print("=" * 70)
    print(f"  Clerk org      : {args.clerk_org}")
    print(f"  Target tenant  : {target_uuid}  (UUIDv5 of clerk_org)")
    print(
        f"  Source tenant  : {source_uuid}  "
        f"({'catch-all default' if source_uuid == DEFAULT_TENANT else 'explicit --from-tenant'})"
    )
    print(f"  Created >=     : {args.created_after or '(no lower bound)'}")
    print(f"  Created <      : {args.created_before or '(no upper bound)'}")
    print(f"  Mode           : {'COMMIT' if args.commit else 'DRY-RUN (no changes)'}")
    print("=" * 70)

    settings = get_settings()
    # Same DSN normalization the worker uses at runtime — the raw
    # DATABASE_URL from Railway is `postgresql://...` which SQLAlchemy
    # would route to psycopg2 (not installed in the worker venv).
    # ``settings.async_database_url`` rewrites the scheme to
    # ``postgresql+asyncpg://...`` so create_async_engine finds the
    # driver we actually ship.
    engine = create_async_engine(settings.async_database_url, echo=False)

    windowed = bool(args.created_after or args.created_before)
    plan, errors = await plan_counts(
        engine,
        tables,
        source=source_uuid,
        target=target_uuid,
        since=args.created_after,
        until=args.created_before,
    )

    print(f"\n=== PLAN ({'window applied per table' if windowed else 'no window'}) ===")
    print(f"{'table':30s}  {'window column':>14s}  {'source_tenant':>15s}  {'target_tenant':>15s}")
    total_would_move = 0
    for table, n_source, n_target in plan:
        col = TABLE_TIMESTAMP_COLUMNS.get(table, "?") if windowed else "-"
        print(f"  {table:30s}  {col:>14s}  {n_source:>15,}  {n_target:>15,}")
        total_would_move += n_source
    for table, err in errors:
        print(f"  {table:30s}  ERROR — {err}")

    print(f"\nRows on source tenant within window  : {total_would_move:,}")
    print(f"Would move to {target_uuid} : {total_would_move:,}")

    if errors:
        print(
            f"\nERROR: {len(errors)} table(s) could not be counted — refusing to "
            f"{'commit' if args.commit else 'plan'}. A partial move leaves a deal "
            "without its documents / extractions. Fix the table list (or pass "
            "--tables without the failing table(s) on purpose) and re-run.",
            file=sys.stderr,
        )
        await engine.dispose()
        return 3

    if not args.commit:
        print("\nDry-run only. Re-run with --commit to apply.")
        await engine.dispose()
        return 0

    print("\n=== APPLYING ===")
    moved = 0
    failed: list[str] = []
    for table, _n_source, _n_target in plan:
        try:
            n = await _reassign(engine, table, source_uuid, target_uuid, args.created_after, args.created_before)
        except Exception as exc:  # noqa: BLE001
            # Each table is its own transaction. The count pass passed, so
            # this is a write-time refusal (audit_log's append-only trigger).
            print(f"  {table:30s}  ERROR — {type(exc).__name__}: {exc}")
            failed.append(table)
            continue
        print(f"  {table:30s}  moved {n:,}")
        moved += n
    print(f"\nTotal rows moved: {moved:,}")
    if failed:
        print(
            f"\nWARNING: {len(failed)} table(s) failed at write time: {', '.join(failed)}. "
            "Their rows are still on the source tenant — see docs/WORKSPACE_ISOLATION.md §6.",
            file=sys.stderr,
        )

    await engine.dispose()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
