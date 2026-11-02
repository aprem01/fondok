"""Review I-4 (W5 topline fix) — where the model years sit on the calendar.

(a) The year placement follows the deal's REAL close date. Operating Year 1 is
    the calendar year holding the first operating month (close + 1 month,
    ``revenue.projection_start_year``, FON-41): Sam's deal closes 2025-09-30,
    so Year 1 = 2025 — the TTM benchmark's own year, used as reported — and
    Year 2 = 2026 is the first year grown by a forecast.
(b) A deal with NO close date still gets its CoStar / CBRE forecast: the
    growth is placed on a calendar assumed from the same anchor order the
    comp lookback uses — the underwriting as-of date, else the comp report's
    as-of date, else the run date — and the status says which anchor was used.
    It never says "no market forecast" while a forecast is in the report.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-topline-calendar.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from tests.fixtures.kimpton_docs_fixture import seed_kimpton_docs_deal  # noqa: E402


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.tenants import reset_tenant_cache

    await run_startup_migrations()
    reset_tenant_cache()
    async with get_session_factory()() as session:
        for tbl in ("engine_outputs", "extraction_results", "documents", "deals", "tenants"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


async def _docs_deal(**patch) -> tuple[str, str]:
    from app.database import get_session_factory

    async with get_session_factory()() as s:
        deal_id, tenant_id = await seed_kimpton_docs_deal(s)
        row = (await s.execute(text("SELECT field_overrides FROM deals WHERE id = :id"), {"id": deal_id})).first()
        ov = json.loads(row[0])
        # The defaults path: no analyst topline input, only the documents.
        for k in ("occupancy_growth_by_year", "adr_growth_by_year", "index_mpi_target", "index_ari_target",
                  "index_mpi_target_by_year", "index_ari_target_by_year"):
            ov.pop(k, None)
        for k, v in patch.items():
            if v is None:
                ov.pop(k, None)
            else:
                ov[k] = v
        await s.execute(text("UPDATE deals SET field_overrides = :ov WHERE id = :id"), {"ov": json.dumps(ov), "id": deal_id})
        await s.commit()
    return deal_id, tenant_id


async def _status(deal_id: str, tenant_id: str) -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    async with get_session_factory()() as s:
        base = await _load_engine_inputs(s, deal_id, tenant_id=tenant_id)
    return base["__topline_status__"]


# ─────────────────────────── the anchor order ───────────────────────────


def test_calendar_anchor_order_matches_the_comp_lookback() -> None:
    from app.services.topline import calendar_anchor

    today = date(2026, 11, 2)
    a = calendar_anchor(date(2025, 9, 30), underwriting_as_of=date(2025, 6, 30), comp_report_dates=[], today=today)
    assert (a.source, a.date, a.assumed) == ("acquisition_close", date(2025, 9, 30), False)
    a = calendar_anchor(None, underwriting_as_of=date(2025, 6, 30), comp_report_dates=[date(2025, 1, 1)], today=today)
    assert (a.source, a.date, a.assumed) == ("underwriting_as_of", date(2025, 6, 30), True)
    a = calendar_anchor(None, underwriting_as_of=None, comp_report_dates=[date(2024, 3, 1), date(2025, 2, 1)], today=today)
    assert (a.source, a.date) == ("comp_report_as_of", date(2025, 2, 1))
    a = calendar_anchor(None, underwriting_as_of=None, comp_report_dates=[], today=today)
    assert (a.source, a.date) == ("run_date", today)
    assert a.first_model_year == 2026  # first month after 2026-11-02 is in 2026
    assert calendar_anchor(None, underwriting_as_of=date(2025, 12, 15), comp_report_dates=[], today=today).first_model_year == 2026


# ─────────────────────────── (b) no close date ───────────────────────────


@pytest.mark.asyncio
async def test_no_close_date_places_the_costar_forecast_on_the_underwriting_as_of() -> None:
    deal_id, tenant_id = await _docs_deal(acquisition_close_date=None, underwriting_as_of="2025-12-31")
    st = await _status(deal_id, tenant_id)
    anchor = st["calendar_anchor"]
    assert anchor["source"] == "underwriting_as_of" and anchor["assumed"] is True
    assert anchor["date"] == "2025-12-31"
    assert "Set the acquisition close date" in anchor["label"]
    assert st["calendar_known"] is True
    assert st["growth_state"] == "sourced"
    years = [r["calendar_year"] for r in st["growth_by_year"]]
    assert years == [2026, 2027, 2028, 2029, 2030]
    assert st["growth_by_year"][0]["adr"]["source"] == "costar_submarket_forecast"
    labels = json.dumps(st)
    assert "cannot be placed on the model calendar" not in labels
    assert "No market" not in labels
    assert anchor["label"] in st["notes"]
    assert st["suggestions"] == []


@pytest.mark.asyncio
async def test_no_close_and_no_as_of_falls_back_to_the_run_date_and_says_so() -> None:
    from app.engines.revenue import projection_start_year

    deal_id, tenant_id = await _docs_deal(acquisition_close_date=None)
    st = await _status(deal_id, tenant_id)
    assert st["calendar_anchor"]["source"] == "run_date"
    assert st["calendar_anchor"]["date"] == date.today().isoformat()
    assert st["growth_by_year"][0]["calendar_year"] == projection_start_year(date.today().isoformat())
    assert st["growth_state"] != "unavailable"


@pytest.mark.asyncio
async def test_no_close_date_and_no_forecast_keeps_the_calendar_unknown() -> None:
    """Nothing to place: no assumed calendar is invented for its own sake."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    from tests.fixtures.kimpton_seed import KIMPTON_FIXTURE_DEAL_ID, kimpton_seed

    async with get_session_factory()() as s:
        base = await _load_engine_inputs(s, KIMPTON_FIXTURE_DEAL_ID, tenant_id="t", assumptions=kimpton_seed())
    st = base["__topline_status__"]
    assert st["calendar_known"] is False
    assert st["calendar_anchor"]["source"] is None


# ─────────────────────────── (a) the real close date ───────────────────────────


@pytest.mark.asyncio
async def test_real_close_date_puts_year_one_on_the_benchmarks_own_year() -> None:
    deal_id, tenant_id = await _docs_deal(acquisition_close_date="2025-09-30")
    st = await _status(deal_id, tenant_id)
    assert st["calendar_anchor"] == {
        "source": "acquisition_close", "date": "2025-09-30", "assumed": False,
        "first_model_year": 2025, "label": st["calendar_anchor"]["label"],
    }
    assert [r["calendar_year"] for r in st["growth_by_year"]] == [2025, 2026, 2027, 2028, 2029]
    # Year 1 = 2025 = the STR TTM's own year: used as reported, no growth applied.
    assert st["growth_by_year"][0]["applied"] is False
    b1 = st["benchmark_by_year"][0]
    assert b1["calendar_year"] == 2025
    assert b1["occupancy"] == pytest.approx(0.754959, abs=1e-6)
    assert b1["adr"] == pytest.approx(310.7753, abs=1e-3)
    # Year 2 = 2026 is grown by the CoStar L&UU 2026 forecast.
    b2 = st["benchmark_by_year"][1]
    assert b2["adr"] == pytest.approx(310.7753 * 1.018, rel=1e-6)
