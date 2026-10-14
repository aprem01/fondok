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
                   Clerk org went live so pre-Clerk seed rows stay put)
* --tables      — comma-separated list; default is all tenant-scoped
                  tables discovered at import time

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


async def _count(engine, table: str, tenant: UUID, since: str | None, until: str | None) -> int:
    where = ["tenant_id = :tenant"]
    params: dict = {"tenant": str(tenant)}
    if since:
        where.append("created_at >= :since")
        params["since"] = since
    if until:
        where.append("created_at < :until")
        params["until"] = until
    sql = f"SELECT COUNT(*) FROM {table} WHERE {' AND '.join(where)}"
    async with engine.begin() as conn:
        try:
            row = (await conn.execute(text(sql), params)).scalar_one()
        except Exception as exc:  # noqa: BLE001
            print(f"  {table:30s}  SKIPPED — {type(exc).__name__}: {exc}")
            return -1
    return int(row or 0)


async def _reassign(engine, table: str, from_tenant: UUID, to_tenant: UUID, since: str | None, until: str | None) -> int:
    where = ["tenant_id = :from_tenant"]
    params: dict = {"from_tenant": str(from_tenant), "to_tenant": str(to_tenant)}
    if since:
        where.append("created_at >= :since")
        params["since"] = since
    if until:
        where.append("created_at < :until")
        params["until"] = until
    sql = f"UPDATE {table} SET tenant_id = :to_tenant WHERE {' AND '.join(where)}"
    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        return int(result.rowcount or 0)


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

    total_default = 0
    total_would_move = 0
    print(f"\n{'table':30s}  {'source_tenant':>15s}  {'target_tenant':>15s}")
    for table in tables:
        n_default = await _count(engine, table, source_uuid, args.created_after, args.created_before)
        n_target = await _count(engine, table, target_uuid, args.created_after, args.created_before)
        if n_default < 0:
            continue
        total_default += n_default
        total_would_move += n_default
        print(f"  {table:30s}  {n_default:>15,}  {n_target:>15,}")

    print(f"\nRows on source tenant within window  : {total_default:,}")
    print(f"Would move to {target_uuid} : {total_would_move:,}")

    if not args.commit:
        print("\nDry-run only. Re-run with --commit to apply.")
        await engine.dispose()
        return 0

    print("\n=== APPLYING ===")
    moved = 0
    for table in tables:
        try:
            n = await _reassign(engine, table, source_uuid, target_uuid, args.created_after, args.created_before)
        except Exception as exc:  # noqa: BLE001
            print(f"  {table:30s}  ERROR — {type(exc).__name__}: {exc}")
            continue
        print(f"  {table:30s}  moved {n:,}")
        moved += n
    print(f"\nTotal rows moved: {moved:,}")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
