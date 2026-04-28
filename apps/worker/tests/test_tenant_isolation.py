"""Tenant isolation tests for the X-Tenant-Id header.

Asserts that:
- POST /deals with `X-Tenant-Id: A` and `B` produce two independent rows
- GET /deals with `X-Tenant-Id: A` returns only A's deals
- GET /deals with `X-Tenant-Id: B` returns only B's deals
- GET /deals without the header falls back to DEFAULT_TENANT_ID and only
  returns deals created by the demo persona

These tests pin the contract between `apps/web/src/lib/api.ts` (which
mirrors the active Clerk Organization id into `X-Tenant-Id`) and the
worker's `get_tenant_id` dependency in `apps/worker/app/api/deals.py`.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-tenant-isolation.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-tenant-isolation-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


# Two synthetic tenant UUIDs we'll map to "Org A" and "Org B" — distinct
# from the worker's DEFAULT_TENANT_ID so the demo-mode fallback test
# produces a distinct list.
TENANT_A = "11111111-1111-1111-1111-11111111aaaa"
TENANT_B = "22222222-2222-2222-2222-22222222bbbb"


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Truncate state between tests so each starts deterministic."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001 — table may not exist yet
                pass
        await session.commit()
    yield


# ─────────────────────────── tests ───────────────────────────


@pytest.mark.asyncio
async def test_create_and_list_scoped_by_tenant_header() -> None:
    """Each X-Tenant-Id sees only its own deals across POST/GET."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Create one deal under each tenant.
        ra = await client.post(
            "/deals",
            json={"name": "Hotel Alpha (TenantA)", "city": "Austin, TX"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert ra.status_code == 201, ra.text
        a_deal = ra.json()
        assert a_deal["tenant_id"] == TENANT_A

        rb = await client.post(
            "/deals",
            json={"name": "Hotel Beta (TenantB)", "city": "Miami, FL"},
            headers={"X-Tenant-Id": TENANT_B},
        )
        assert rb.status_code == 201, rb.text
        b_deal = rb.json()
        assert b_deal["tenant_id"] == TENANT_B

        # Tenant A list returns only Hotel Alpha.
        ra_list = await client.get(
            "/deals", headers={"X-Tenant-Id": TENANT_A}
        )
        assert ra_list.status_code == 200
        a_names = [d["name"] for d in ra_list.json()]
        assert a_names == ["Hotel Alpha (TenantA)"]

        # Tenant B list returns only Hotel Beta.
        rb_list = await client.get(
            "/deals", headers={"X-Tenant-Id": TENANT_B}
        )
        assert rb_list.status_code == 200
        b_names = [d["name"] for d in rb_list.json()]
        assert b_names == ["Hotel Beta (TenantB)"]


@pytest.mark.asyncio
async def test_missing_header_falls_back_to_default_tenant() -> None:
    """Without X-Tenant-Id, the worker uses DEFAULT_TENANT_ID (demo mode)."""
    from httpx import ASGITransport, AsyncClient

    from app.config import get_settings
    from app.main import app

    default_tenant = get_settings().DEFAULT_TENANT_ID

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Seed one deal per tenant: A, B, and the unauthenticated default.
        await client.post(
            "/deals",
            json={"name": "Tenant A Deal"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        await client.post(
            "/deals",
            json={"name": "Tenant B Deal"},
            headers={"X-Tenant-Id": TENANT_B},
        )
        # No header → falls back to DEFAULT_TENANT_ID.
        rd = await client.post("/deals", json={"name": "Demo Deal"})
        assert rd.status_code == 201
        assert rd.json()["tenant_id"] == default_tenant

        # GET without header should only return the demo deal.
        rl = await client.get("/deals")
        assert rl.status_code == 200
        names = [d["name"] for d in rl.json()]
        assert names == ["Demo Deal"]


@pytest.mark.asyncio
async def test_get_deal_404_across_tenants() -> None:
    """A deal owned by tenant A is invisible (404) to tenant B's GET."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        ra = await client.post(
            "/deals",
            json={"name": "Cross-Tenant Probe"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        deal_id = ra.json()["id"]

        # Tenant A reads its own deal: 200.
        ok = await client.get(
            f"/deals/{deal_id}", headers={"X-Tenant-Id": TENANT_A}
        )
        assert ok.status_code == 200

        # Tenant B should see a 404 — never know the deal exists.
        miss = await client.get(
            f"/deals/{deal_id}", headers={"X-Tenant-Id": TENANT_B}
        )
        assert miss.status_code == 404


@pytest.mark.asyncio
async def test_malformed_header_falls_back_silently() -> None:
    """A bogus X-Tenant-Id should resolve to DEFAULT_TENANT_ID, not 4xx."""
    from httpx import ASGITransport, AsyncClient

    from app.config import get_settings
    from app.main import app

    default_tenant = get_settings().DEFAULT_TENANT_ID

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Seed a deal under the default tenant via no-header.
        await client.post("/deals", json={"name": "Default Tenant Deal"})

        # Send a malformed UUID — worker should fall back, not 400.
        rl = await client.get(
            "/deals", headers={"X-Tenant-Id": "not-a-uuid"}
        )
        assert rl.status_code == 200
        names = [d["name"] for d in rl.json()]
        assert names == ["Default Tenant Deal"]
        # Sanity: the seeded deal really was on the default tenant.
        assert default_tenant  # smoke; ensures settings loaded
