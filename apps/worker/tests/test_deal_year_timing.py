"""FON-121 review I1 — with an acquisition close date, deal years govern every
engine, Returns timing included.

FON-67's ``acquisition_month_offset`` aligns the Returns monthly cash flows to
CALENDAR years with a stub (a 9/30 close = 9). On deal years (twelve months
from the close) that offset is 0 by construction: a stored non-zero offset on
a deal WITH a close date would sell every exit row nine months away from the
deal year its label names. So the offset is ignored for that deal and a
visible note says so (the reason and the stored value); on a deal with no
close date it still applies, unchanged.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

import pytest

from app.services.engine_runner import (
    DEAL_YEAR_TIMING_NOTE_KEY,
    _apply_deal_year_timing,
)


def test_a_close_date_ignores_the_month_offset_and_says_why() -> None:
    base = {"acquisition_close_date": "2025-09-30", "acquisition_month_offset": 9, "hold_years": 5}
    reasons: dict = {}
    _apply_deal_year_timing(base, reasons)
    assert "acquisition_month_offset" not in base
    r = reasons["acquisition_month_offset"]
    assert r["code"] == "not_applicable"
    assert "9" in r["detail"] and "2025-09-30" in r["detail"]
    assert "Oct 2025–Sep 2026" in r["detail"]
    note = base[DEAL_YEAR_TIMING_NOTE_KEY]
    assert note == r["detail"]


@pytest.mark.parametrize("offset", [0, None, "", "0"])
def test_a_zero_or_absent_offset_needs_no_note(offset) -> None:
    base = {"acquisition_close_date": "2025-09-30", "acquisition_month_offset": offset}
    reasons: dict = {}
    _apply_deal_year_timing(base, reasons)
    assert "acquisition_month_offset" not in reasons
    assert DEAL_YEAR_TIMING_NOTE_KEY not in base


def test_without_a_close_date_the_offset_still_applies() -> None:
    base = {"acquisition_month_offset": 9}
    reasons: dict = {}
    _apply_deal_year_timing(base, reasons)
    assert base["acquisition_month_offset"] == 9
    assert reasons == {}


@pytest.mark.asyncio
async def test_exit_rows_sell_at_their_labelled_deal_year() -> None:
    from app.database import get_session_factory
    from app.engines.revenue import projection_deal_years
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import ENGINE_REGISTRY, _build_input_for, _load_engine_inputs
    from tests.fixtures.kimpton_seed import KIMPTON_FIXTURE_DEAL_ID, kimpton_seed

    await run_startup_migrations()
    seed = {**kimpton_seed(), "acquisition_close_date": "2025-09-30", "acquisition_month_offset": 9}
    async with get_session_factory()() as s:
        base = await _load_engine_inputs(s, KIMPTON_FIXTURE_DEAL_ID, tenant_id="t", assumptions=seed)
    assert base["__reasons__"]["acquisition_month_offset"]["code"] == "not_applicable"
    acc: dict = {}
    for name in ("revenue", "fb", "expense", "capital", "debt", "returns"):
        acc[name] = ENGINE_REGISTRY[name]().run(_build_input_for(name, str(KIMPTON_FIXTURE_DEAL_ID), base, acc))
    ret = acc["returns"]
    assert ret.timing_note is not None and "Acquisition month offset 9 not applied" in ret.timing_note
    spans = projection_deal_years("2025-09-30", base["hold_years"])
    for row in ret.exit_schedule:
        if row.exit_year is None:
            continue
        # A year-end row sells at the END of its deal year: month 12·Y from close.
        assert row.exit_month == 12 * row.exit_year, (row.exit_year, row.exit_month)
        assert spans[row.exit_year - 1].end.startswith(str(2025 + row.exit_year))
