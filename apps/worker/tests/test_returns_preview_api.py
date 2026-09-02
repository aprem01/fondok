"""Tests for the non-persisting Returns preview endpoint (FON-68 step 3).

``POST /deals/{deal_id}/engines/returns/preview`` is the ephemeral
"sensitivity sandbox" behind the Returns tab's Live Assumptions card. It
runs the engine chain in memory under sandbox overrides and returns the
resulting IRR / equity multiple / year-one CoC / exit value / DSCR — and it
must NEVER write an ``engine_outputs`` row (dragging a slider cannot mutate
the deal or pollute the canonical run).

These tests mount a temporary FastAPI app through ASGI so the tenant-scope
check + ``Depends(get_tenant_id)`` wiring is exercised without standing up the
full worker.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

# Per-test SQLite database BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-returns-preview.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


async def _count_engine_outputs(deal_id: str) -> int:
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM engine_outputs WHERE deal_id = :deal"
                ),
                {"deal": deal_id},
            )
        ).first()
    return int(row[0]) if row else 0


async def _seed_deal(tenant_id: str, deal_id: str) -> None:
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, city, keys, purchase_price, "
                "service, status, deal_stage, risk, ai_confidence, created_at, updated_at) "
                "VALUES (:id, :tenant, 'Test', 'NYC', 200, 40000000, "
                "'Full Service', 'Draft', 'Teaser', 'Medium', 0.8, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": deal_id, "tenant": tenant_id},
        )
        await session.commit()


@pytest.mark.asyncio
async def test_preview_returns_values_and_persists_nothing() -> None:
    """Preview returns real headline numbers AND writes no engine_outputs row."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.migrations import run_startup_migrations

    await run_startup_migrations()

    tenant = str(uuid4())
    deal_id = str(uuid4())
    await _seed_deal(tenant, deal_id)

    assert await _count_engine_outputs(deal_id) == 0

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/deals/{deal_id}/engines/returns/preview",
            json={"overrides": {"exit_cap_rate": 0.075, "hold_years": 5}},
            headers={"X-Tenant-Id": tenant},
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Every headline KPI resolves to a real number.
    for field in (
        "levered_irr",
        "equity_multiple",
        "year_one_coc",
        "exit_value",
        "dscr_y1",
    ):
        assert data[field] is not None, f"{field} was null: {data}"
        assert isinstance(data[field], (int, float))
    assert data["hold_years"] == 5
    assert data["exit_cap_rate"] == pytest.approx(0.075)
    # No sensitivity grid unless requested.
    assert data.get("sensitivity") is None

    # Non-persistence proof: still zero engine_outputs rows for this deal.
    assert await _count_engine_outputs(deal_id) == 0


@pytest.mark.asyncio
async def test_preview_override_changes_result() -> None:
    """Flexing the sandbox exit cap moves IRR — proves overrides are applied."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.migrations import run_startup_migrations

    await run_startup_migrations()

    tenant = str(uuid4())
    deal_id = str(uuid4())
    await _seed_deal(tenant, deal_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        low = await client.post(
            f"/deals/{deal_id}/engines/returns/preview",
            json={"overrides": {"exit_cap_rate": 0.06}},
            headers={"X-Tenant-Id": tenant},
        )
        high = await client.post(
            f"/deals/{deal_id}/engines/returns/preview",
            json={"overrides": {"exit_cap_rate": 0.09}},
            headers={"X-Tenant-Id": tenant},
        )

    assert low.status_code == 200 and high.status_code == 200
    # A higher exit cap → lower exit value → lower levered IRR.
    assert high.json()["levered_irr"] < low.json()["levered_irr"]
    assert high.json()["exit_value"] < low.json()["exit_value"]

    # Still nothing persisted after several preview calls.
    assert await _count_engine_outputs(deal_id) == 0


@pytest.mark.asyncio
async def test_preview_includes_sensitivity_grid_when_requested() -> None:
    """include_sensitivity returns the serialized sensitivity engine grid."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.migrations import run_startup_migrations

    await run_startup_migrations()

    tenant = str(uuid4())
    deal_id = str(uuid4())
    await _seed_deal(tenant, deal_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/deals/{deal_id}/engines/returns/preview",
            json={"include_sensitivity": True},
            headers={"X-Tenant-Id": tenant},
        )

    assert resp.status_code == 200, resp.text
    grid = resp.json().get("sensitivity")
    assert grid is not None
    # Shape the web matrixFromWorker helper consumes.
    assert isinstance(grid.get("rows"), list) and grid["rows"]
    assert isinstance(grid.get("cols"), list) and grid["cols"]
    assert isinstance(grid.get("cells"), list) and grid["cells"]
    assert "row_variable" in grid and "col_variable" in grid

    assert await _count_engine_outputs(deal_id) == 0


@pytest.mark.asyncio
async def test_preview_tenant_scoped() -> None:
    """Cross-tenant deal id 404s before any compute."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.migrations import run_startup_migrations

    await run_startup_migrations()

    tenant_a = str(uuid4())
    tenant_b = str(uuid4())
    deal_id = str(uuid4())
    await _seed_deal(tenant_a, deal_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r_cross = await client.post(
            f"/deals/{deal_id}/engines/returns/preview",
            json={"overrides": {"exit_cap_rate": 0.07}},
            headers={"X-Tenant-Id": tenant_b},
        )
        assert r_cross.status_code == 404, (
            f"expected 404 for cross-tenant request, got {r_cross.status_code} "
            f"body={r_cross.text}"
        )
        r_same = await client.post(
            f"/deals/{deal_id}/engines/returns/preview",
            json={"overrides": {"exit_cap_rate": 0.07}},
            headers={"X-Tenant-Id": tenant_a},
        )
        assert r_same.status_code != 404, (
            f"endpoint must not 404 on the owning tenant: status="
            f"{r_same.status_code} body={r_same.text[:300]}"
        )
