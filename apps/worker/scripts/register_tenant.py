"""Register a Clerk organization as a Fondok tenant — FON-93.

The ``tenants`` registry is what turns a Clerk org into a workspace once
``REQUIRE_REGISTERED_TENANT`` is on. ``POST /admin/tenants`` does the same
thing over HTTP, but only for a caller whose own tenant is already
``internal`` — so the FIRST internal tenant ("Fondok Internal") has to be
written from a trusted shell with this script. After that, use the API.

Usage (from ``apps/worker``, against the DB in ``DATABASE_URL``):

    # Bootstrap Fondok's own workspace
    uv run python scripts/register_tenant.py \\
        --clerk-org org_XXXXXXXXXXXXXXXXXXXXXXXX \\
        --name "Fondok Internal" --kind internal

    # Register a customer institution
    uv run python scripts/register_tenant.py \\
        --clerk-org org_YYYYYYYYYYYYYYYYYYYYYYYY \\
        --name "Harbor Point Capital" --kind external

    # Approve an org the Clerk webhook parked as pending_review
    uv run python scripts/register_tenant.py \\
        --clerk-org org_YYYY... --name "Harbor Point Capital" \\
        --kind external --status active

    # See what is registered
    uv run python scripts/register_tenant.py --list

Re-running for an existing org updates name / kind / status (upsert).
The tenant UUID printed is the UUIDv5 every tenant-scoped table stores
in ``tenant_id`` — the same value ``_coerce_tenant_id`` produces at
request time, so rows written before registration are already attached.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

# Reuse the runtime coercer so this can never drift from the request path.
from app.api.deals import coerce_org_tenant_id
from app.config import get_settings
from app.migrations import run_startup_migrations
from app.tenants import KINDS, STATUSES, list_tenants, upsert_tenant


async def _list() -> int:
    rows = await list_tenants()
    if not rows:
        print("no tenants registered")
        return 0
    print(f"{'tenant_id':38s} {'kind':9s} {'status':15s} {'clerk_org_id':34s} name")
    for t in rows:
        print(f"{t.id!s:38s} {t.kind:9s} {t.status:15s} {t.clerk_org_id:34s} {t.name}")
    return 0


async def _register(clerk_org: str, name: str, kind: str, status: str) -> int:
    tenant_id = coerce_org_tenant_id(clerk_org)
    if tenant_id is None:
        print(f"error: {clerk_org!r} is not a Clerk organization id (org_…)", file=sys.stderr)
        return 2
    stored = await upsert_tenant(
        tenant_id=tenant_id,
        clerk_org_id=clerk_org,
        name=name,
        kind=kind,
        status=status,
    )
    print("registered:")
    for k, v in stored.as_dict().items():
        print(f"  {k:14s} {v}")
    return 0


async def _main(args: argparse.Namespace) -> int:
    settings = get_settings()
    print(f"database: {settings.async_database_url.split('@')[-1]}")
    # Idempotent — makes sure the ``tenants`` table exists on a DB that
    # predates the migration (every entry is CREATE … IF NOT EXISTS).
    await run_startup_migrations()
    if args.list:
        return await _list()
    if not (args.clerk_org and args.name):
        print("error: --clerk-org and --name are required (or use --list)", file=sys.stderr)
        return 2
    return await _register(args.clerk_org, args.name, args.kind, args.status)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clerk-org", help="Clerk organization id (org_…)")
    parser.add_argument("--name", help="Institution name shown in /admin/tenants")
    parser.add_argument("--kind", choices=sorted(KINDS), default="external")
    parser.add_argument("--status", choices=sorted(STATUSES), default="active")
    parser.add_argument("--list", action="store_true", help="Print the registry and exit")
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
