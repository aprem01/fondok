"""Tests for the engine orchestration service.

The Run Model button in the web app fans out to ``run_all_engines`` /
``run_single_engine``; these tests pin the contract:

* full chain on the Kimpton fixture completes with NOI/IRR in tolerance
* a single-engine call (returns) walks its dependencies and persists
* a synthetic failure in one engine doesn't sink independent engines
* every successful run lands one row in ``engine_outputs``
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-engine-runner.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Recreate the schema before each test."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        try:
            await session.execute(text("DELETE FROM engine_outputs"))
            await session.commit()
        except Exception:  # noqa: BLE001
            pass
    yield


@pytest.mark.asyncio
async def test_run_all_engines_kimpton() -> None:
    """Full chain on the Kimpton fixture — every engine completes and
    headline numbers land within tolerance of the seeded UI."""
    from app.database import get_session_factory
    from app.services.engine_runner import ENGINE_NAMES, run_all_engines

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        results = await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
        )

    assert set(results) == set(ENGINE_NAMES)
    for name, payload in results.items():
        assert payload["status"] == "complete", (
            f"engine {name} did not complete: {payload}"
        )
        assert payload["runtime_ms"] >= 0

    # Y1 NOI from the expense engine should be in the right neighbourhood.
    y1_noi = results["expense"]["outputs"]["years"][0]["noi"]
    assert 3_500_000 <= y1_noi <= 6_500_000, f"Y1 NOI out of band: {y1_noi}"

    # Levered IRR from the returns engine on Kimpton lands ~20-30%.
    irr = results["returns"]["outputs"]["levered_irr"]
    assert 0.10 <= irr <= 0.40, f"levered IRR out of band: {irr}"

    # Equity multiple sanity.
    em = results["returns"]["outputs"]["equity_multiple"]
    assert 1.5 <= em <= 3.5, f"equity multiple out of band: {em}"

    # Sensitivity grid is 5x5.
    cells = results["sensitivity"]["outputs"]["cells"]
    assert len(cells) == 25

    # Partnership engine emits both GP/LP IRR.
    lp_irr = results["partnership"]["outputs"]["lp"]["irr"]
    gp_irr = results["partnership"]["outputs"]["gp"]["irr"]
    assert lp_irr > 0
    assert gp_irr > 0


@pytest.mark.asyncio
async def test_run_single_engine_returns() -> None:
    """``run_single_engine('returns', ...)`` walks its deps and lands
    a complete output row."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_single_engine

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        result = await run_single_engine(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            engine_name="returns",
        )

    assert result["status"] == "complete"
    out = result["outputs"]
    for key in (
        "levered_irr", "unlevered_irr", "equity_multiple",
        "year_one_coc", "gross_sale_price", "selling_costs",
        "net_proceeds", "hold_years", "cash_flows",
    ):
        assert key in out, f"missing key {key!r} in returns output"
    assert out["hold_years"] == 5
    assert "IRR" in result["summary"]


@pytest.mark.asyncio
async def test_engine_failure_doesnt_block_independent_engines() -> None:
    """A bad ``ltv`` override blows up ``capital`` (which validates >=0
    <=1). Independent engines (revenue, fb, expense) should still run.

    revenue/fb/expense don't depend on capital — they should land
    'complete'. debt/returns/sensitivity/partnership all depend on
    capital (directly or transitively) and should land 'failed' with a
    skipped-upstream error.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        results = await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
            overrides={"ltv": 1.5},  # invalid: ltv must be <= 1.0
        )

    assert results["capital"]["status"] == "failed"
    # Independent engines unaffected.
    assert results["revenue"]["status"] == "complete"
    assert results["fb"]["status"] == "complete"
    assert results["expense"]["status"] == "complete"
    # Downstream of capital — every one should be skipped/failed.
    for downstream in ("debt", "returns", "sensitivity", "partnership"):
        assert results[downstream]["status"] == "failed", (
            f"expected {downstream} to be failed when capital fails, "
            f"got {results[downstream]['status']}"
        )


@pytest.mark.asyncio
async def test_run_persists_to_db() -> None:
    """run_all → engine_outputs has 8 rows, all 'complete', for the run."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        ENGINE_NAMES,
        _coerce_uuid,
        get_run_status,
        run_all_engines,
    )

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
        )

        rows = await get_run_status(
            session, deal_id=deal_id, run_id=run_id
        )

        # Direct count to confirm one row per engine, no dupes.
        # Producer coerces deal_id via _coerce_uuid before persisting,
        # so the raw query parameter must be coerced the same way.
        deal_id_db = str(_coerce_uuid(deal_id))
        count_row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) AS n FROM engine_outputs "
                    "WHERE deal_id = :deal AND run_id = :run"
                ),
                {"deal": deal_id_db, "run": run_id},
            )
        ).first()
        assert count_row is not None
        assert int(count_row._mapping["n"]) == len(ENGINE_NAMES)

    assert len(rows) == len(ENGINE_NAMES)
    seen_engines = {r["engine"] for r in rows}
    assert seen_engines == set(ENGINE_NAMES)
    for r in rows:
        assert r["status"] == "complete", f"row not complete: {r}"
        assert r["outputs"] is not None
        assert r["completed_at"] is not None


@pytest.mark.asyncio
async def test_get_latest_outputs_returns_per_engine_map() -> None:
    """``GET /deals/{id}/engines`` shape — one entry per engine name."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        ENGINE_NAMES,
        get_latest_outputs,
        run_all_engines,
    )

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())

    factory = get_session_factory()
    async with factory() as session:
        await run_all_engines(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            run_id=run_id,
        )
        outputs = await get_latest_outputs(session, deal_id=deal_id)

    assert set(outputs) == set(ENGINE_NAMES)
    for name, row in outputs.items():
        assert row["status"] == "complete"
        assert row["engine"] == name


@pytest.mark.asyncio
async def test_unknown_engine_raises() -> None:
    """``run_single_engine`` rejects unknown engine names cleanly."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_single_engine

    factory = get_session_factory()
    async with factory() as session:
        with pytest.raises(ValueError, match="unknown engine"):
            await run_single_engine(
                session,
                deal_id="kimpton-angler-2026",
                tenant_id=str(uuid4()),
                engine_name="nope",
            )
