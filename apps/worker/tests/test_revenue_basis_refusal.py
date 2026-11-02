"""Review I-5 (W5 topline fix) — no invented Year-1 occupancy / ADR.

Until this change the loader's labelled defaults carried a Year-1 occupancy
of 76.2% and an ADR of $385 (the Kimpton demo's rates). A deal with no T-12,
no STR / market benchmark and no analyst value projected a Kimpton-priced
hotel; a T-12 with occupancy but no ADR had the gap filled the same way.
Under the no-invented-data rule there is no defensible platform figure for a
hotel's own rate, so the revenue engine — and every engine that consumes its
output — refuses with the typed ``no_revenue_basis`` failure, exactly as a
deal with no key count refuses with ``missing_identity_inputs``.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-revenue-basis.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

GUIDANCE = "Upload a T-12 or STR report, or enter Year-1 occupancy and ADR"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    async with get_session_factory()() as session:
        for tbl in ("engine_outputs", "scenarios", "deals"):
            await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


async def _insert_deal(session, *, tenant_id: str, field_overrides: dict | None = None) -> str:
    deal_id = str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO deals (id, tenant_id, name, status, keys, purchase_price,
                               field_overrides, created_at, updated_at)
            VALUES (:id, :tenant, 'Basis Hotel', 'Draft', 150, 40000000,
                    :fo, :now, :now)
            """
        ),
        {
            "id": deal_id,
            "tenant": tenant_id,
            "fo": json.dumps(field_overrides or {}),
            "now": datetime.now(UTC),
        },
    )
    await session.commit()
    return deal_id


def _no_seed_rates_anywhere(blob: object) -> None:
    s = json.dumps(blob, default=str)
    assert "0.762" not in s, s[:400]
    assert "385.0" not in s, s[:400]


def test_the_seed_year1_rates_are_not_production_defaults() -> None:
    from app.services.engine_runner import REVENUE_BASIS_KEYS, _default_assumptions

    assert REVENUE_BASIS_KEYS == ("starting_occupancy", "starting_adr")
    for key in REVENUE_BASIS_KEYS:
        assert key not in _default_assumptions()


def test_revenue_basis_needs_are_transitive_over_engine_deps() -> None:
    from app.services.engine_runner import ENGINE_NAMES, revenue_basis_needed

    needs = {name for name in ENGINE_NAMES if revenue_basis_needed(name)}
    assert needs == {
        "revenue", "fb", "expense", "debt", "returns", "sensitivity", "partnership", "cash_flow",
    }
    assert not revenue_basis_needed("capital")


@pytest.mark.asyncio
async def test_no_basis_refuses_revenue_and_everything_downstream() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import get_run_status, run_all_engines

    tenant_id, run_id = str(uuid4()), str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(session, tenant_id=tenant_id)
        results = await run_all_engines(session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)
        rows = await get_run_status(session, deal_id=deal_id, run_id=run_id, tenant_id=tenant_id)

    for name in ("revenue", "fb", "expense", "debt", "returns", "sensitivity", "partnership", "cash_flow"):
        r = results[name]
        assert r["status"] == "failed", (name, r)
        assert r.get("outputs") is None
        assert r["failure"]["reason"] == "no_revenue_basis", (name, r)
        assert r["failure"]["missing"] == ["starting_adr", "starting_occupancy"]
        assert GUIDANCE in r["failure"]["message"]
    # The capital stack does not read the topline: it still runs.
    assert results["capital"]["status"] == "complete", results["capital"]
    _no_seed_rates_anywhere(results)
    persisted = {r["engine"]: r for r in rows}
    assert persisted["revenue"]["failure"]["reason"] == "no_revenue_basis"
    assert persisted["revenue"]["error"] == persisted["revenue"]["failure"]["message"]


@pytest.mark.asyncio
async def test_loader_reports_the_missing_basis_as_a_reason_and_on_topline_status() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import TOPLINE_STATUS_KEY, _load_engine_inputs

    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(session, tenant_id=tenant_id)
        base = await _load_engine_inputs(session, deal_id, tenant_id=tenant_id)

    for key in ("starting_occupancy", "starting_adr"):
        assert key not in base
        assert key not in base["__sources__"]
        assert base["__reasons__"][key]["code"] == "no_revenue_basis"
        assert GUIDANCE in base["__reasons__"][key]["detail"]
    status = base[TOPLINE_STATUS_KEY]
    assert status["revenue_basis"] == {
        "state": "missing",
        "missing": ["starting_adr", "starting_occupancy"],
        "message": status["revenue_basis"]["message"],
    }
    assert GUIDANCE in status["revenue_basis"]["message"]


@pytest.mark.asyncio
async def test_one_missing_metric_refuses_and_names_only_that_metric() -> None:
    """Occupancy entered, ADR not: the seed used to fill the ADR silently."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(
            session, tenant_id=tenant_id,
            field_overrides={"starting_occupancy": {"value": 0.71, "note": "broker"}},
        )
        results = await run_all_engines(session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4()))
    assert results["revenue"]["failure"]["reason"] == "no_revenue_basis"
    assert results["revenue"]["failure"]["missing"] == ["starting_adr"]


@pytest.mark.asyncio
async def test_analyst_year1_rates_run_the_model() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import TOPLINE_STATUS_KEY, _load_engine_inputs, run_all_engines

    tenant_id = str(uuid4())
    overrides = {
        "starting_occupancy": {"value": 0.71, "note": "broker"},
        "starting_adr": {"value": 240.0, "note": "broker"},
    }
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(session, tenant_id=tenant_id, field_overrides=overrides)
        base = await _load_engine_inputs(session, deal_id, tenant_id=tenant_id)
        results = await run_all_engines(session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4()))
    assert base[TOPLINE_STATUS_KEY]["revenue_basis"]["state"] == "present"
    assert results["revenue"]["status"] == "complete", results["revenue"]
    y1 = results["revenue"]["outputs"]["years"][0]
    assert y1["occupancy"] == pytest.approx(0.71)
    assert y1["adr"] == pytest.approx(240.0)


def test_build_input_for_raises_the_typed_basis_error() -> None:
    from app.services.engine_runner import (
        MissingIdentityInputsError,
        MissingRevenueBasisError,
        _build_input_for,
        _default_assumptions,
    )
    from fondok_schemas.reasons import ReasonCode

    base = {**_default_assumptions(), "keys": 120, "purchase_price": 1.0e7}
    with pytest.raises(MissingRevenueBasisError) as info:
        _build_input_for("revenue", str(uuid4()), base, {})
    assert isinstance(info.value, MissingIdentityInputsError)  # same 422 handler
    assert info.value.reason is ReasonCode.NO_REVENUE_BASIS
    assert info.value.missing == ["starting_adr", "starting_occupancy"]
    assert GUIDANCE in str(info.value)


@pytest.mark.asyncio
async def test_returns_preview_answers_422_with_the_basis_reason() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app

    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        deal_id = await _insert_deal(session, tenant_id=tenant_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.post(
            f"/deals/{deal_id}/engines/returns/preview",
            json={}, headers={"X-Tenant-Id": tenant_id},
        )
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["reason"] == "no_revenue_basis"
    assert detail["missing"] == ["starting_adr", "starting_occupancy"]
    assert GUIDANCE in detail["message"]
