"""FON-41 / FON-59 #3 — ONE stabilized year, and it moves no return.

Four incompatible "stabilized" definitions used to ship side by side. The
expense engine now publishes ONE block (``expense.stabilization``). FON-59
R-057 (Sam's decision 1): the year defaults to Year 3 after acquisition close
— the same index the debt engine reads for its stabilized DSCR and debt yield
— clamped to the hold, and is overridable by the analyst via the persisted
``stabilization_year`` assumption. The old occupancy / NOI-plateau signal is
published only as the ``detected_year`` hint.

The load-bearing guard is ``test_stabilization_year_does_not_move_returns``:
the block is DISPLAY-ONLY, so setting or changing it must leave the FON-67
reconciliation (gross sale, both IRRs, MOIC, terminal NOI) bit-identical.
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

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-stabilization-year.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

KIMPTON = "kimpton-angler-2026"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        try:
            await session.execute(text("DELETE FROM engine_outputs"))
            await session.commit()
        except Exception:
            pass
    yield


async def _deal_with_overrides(session, overrides: dict) -> tuple[str, str]:
    deal_id, tenant_id = str(uuid4()), str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO deals (id, tenant_id, name, status, field_overrides,
                               created_at, updated_at)
            VALUES (:id, :tenant, :name, 'Draft', :ov, :now, :now)
            """
        ),
        {
            "id": deal_id,
            "tenant": tenant_id,
            "name": "Stabilization Year Hotel",
            "ov": json.dumps(overrides),
            "now": datetime.now(UTC),
        },
    )
    await session.commit()
    return deal_id, tenant_id


def _comparable(value):
    """Engine outputs minus the per-run identifiers, for equality.

    ``deal_id`` differs between two runs because each is its own deal row — it
    is an identifier, not an engine number, and it appears at several depths
    (the debt stack carries its own). Everything else must match byte-for-byte.
    """
    if isinstance(value, dict):
        return {
            k: _comparable(v) for k, v in value.items() if k != "deal_id"
        }
    if isinstance(value, list):
        return [_comparable(v) for v in value]
    return value


async def _run(overrides: dict | None = None) -> dict:
    """Run the full chain — on the Kimpton demo deal, or on a deal carrying
    ``overrides`` in its ``field_overrides``."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    factory = get_session_factory()
    async with factory() as session:
        if overrides is None:
            deal_id, tenant_id = KIMPTON, str(uuid4())
        else:
            deal_id, tenant_id = await _deal_with_overrides(session, overrides)
        return await run_all_engines(
            session, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4())
        )


# ── the default: Year 3 after close ──────────────────────────────────


@pytest.mark.asyncio
async def test_default_stabilization_year_is_year_3_and_matches_debt() -> None:
    """No analyst year → Year 3 after close, the same index debt reads."""
    from app.engines.debt import _resolve_stabilized_year_index
    from app.engines.stabilization import (
        DEFAULT_STABILIZATION_ANCHOR,
        DEFAULT_STABILIZATION_YEAR,
    )

    results = await _run()
    expense = results["expense"]["outputs"]
    revenue = results["revenue"]["outputs"]

    stab = expense["stabilization"]
    assert stab is not None
    assert DEFAULT_STABILIZATION_YEAR == 3
    assert stab["stabilized_year"] == 3
    assert stab["stabilized_year_index"] == 2
    assert stab["source"] == "default_year_3"
    assert stab["anchor"] == DEFAULT_STABILIZATION_ANCHOR == "acquisition_close"
    assert stab["clamped"] is False

    debt_index = _resolve_stabilized_year_index(
        occupancy_by_year=[y["occupancy"] for y in revenue["years"]],
        stabilized_occupancy=0.762,
        noi_by_year=[y["noi"] for y in expense["years"]],
    )
    assert debt_index == stab["stabilized_year_index"]
    # The model-detected signal is still reported — as a hint only.
    assert stab["detected_year"] is not None
    assert stab["detected_signal"] in ("occupancy", "noi_plateau")


@pytest.mark.asyncio
async def test_the_block_reads_every_figure_off_the_same_year_index() -> None:
    """Sam: "All metrics must reconcile to the same projection year.\""""
    results = await _run()
    expense = results["expense"]["outputs"]
    revenue = results["revenue"]["outputs"]
    stab = expense["stabilization"]
    i = stab["stabilized_year_index"]

    assert stab["stabilized_occupancy"] == pytest.approx(
        revenue["years"][i]["occupancy"]
    )
    assert stab["stabilized_adr"] == pytest.approx(revenue["years"][i]["adr"])
    assert stab["stabilized_revenue"] == pytest.approx(
        expense["years"][i]["total_revenue"]
    )
    assert stab["stabilized_noi_before_reserve"] == pytest.approx(
        expense["years"][i]["noi_institutional"]
    )
    assert stab["stabilized_cash_noi"] == pytest.approx(expense["years"][i]["noi"])
    # The margin is the two figures above, from the SAME index — not a ratio
    # of one year's NOI to another year's revenue.
    assert stab["stabilized_noi_margin"] == pytest.approx(
        stab["stabilized_noi_before_reserve"] / stab["stabilized_revenue"]
    )


@pytest.mark.asyncio
async def test_stabilized_noi_is_not_the_exit_reversion() -> None:
    """The old Overview read ``returns.terminal_noi`` — the year hold+1
    reversion — and called it "Stabilized NOI"."""
    results = await _run()
    stab = results["expense"]["outputs"]["stabilization"]
    terminal = results["returns"]["outputs"]["terminal_noi"]
    assert stab["stabilized_noi_before_reserve"] != pytest.approx(terminal)


# ── the analyst override ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_analyst_override_wins() -> None:
    """A persisted ``stabilization_year`` selects that year and says so."""
    seeded = await _run()
    detected = seeded["expense"]["outputs"]["stabilization"]["detected_year"]
    other_year = 4

    results = await _run({"stabilization_year": {"value": other_year, "note": "IC"}})
    expense = results["expense"]["outputs"]
    stab = expense["stabilization"]

    assert stab["stabilized_year"] == other_year
    assert stab["stabilized_year_index"] == other_year - 1
    assert stab["source"] == "analyst_override"
    # The detected hint is still published alongside it.
    assert stab["detected_year"] == detected
    assert stab["stabilized_revenue"] == pytest.approx(
        expense["years"][other_year - 1]["total_revenue"]
    )


@pytest.mark.asyncio
async def test_a_re_saved_default_year_stays_default() -> None:
    """FON-65 — re-confirming the default unchanged is not an override."""
    results = await _run({"stabilization_year": {"value": 3, "note": "ok"}})
    stab = results["expense"]["outputs"]["stabilization"]

    assert stab["stabilized_year"] == 3
    assert stab["source"] == "default_year_3"


@pytest.mark.asyncio
async def test_an_out_of_range_year_falls_back_to_the_default() -> None:
    """A year past the hold is not a year this projection has."""
    results = await _run({"stabilization_year": 99})
    stab = results["expense"]["outputs"]["stabilization"]
    assert stab["stabilized_year"] == 3
    assert stab["source"] == "default_year_3"


# ── THE guard: the block moves nothing ────────────────────────────────


@pytest.mark.asyncio
async def test_stabilization_year_does_not_move_returns() -> None:
    """Setting or changing the Stabilization Year leaves the reconciliation
    bit-identical. This is what protects FON-67."""
    baseline = await _run({})
    with_year_2 = await _run({"stabilization_year": 2})
    with_year_4 = await _run({"stabilization_year": 4})

    guarded = ("gross_sale_price", "levered_irr", "equity_multiple", "terminal_noi")
    base_returns = baseline["returns"]["outputs"]
    for run in (with_year_2, with_year_4):
        for key in guarded:
            assert run["returns"]["outputs"][key] == base_returns[key], key

    # And nothing else on any engine moved either — the ONLY delta anywhere is
    # the stabilization block itself. (``deal_id`` differs because each run is
    # its own deal row; it is not an engine number.)
    for engine in ("revenue", "fb", "expense", "capital", "debt", "returns"):
        before = _comparable(baseline[engine]["outputs"])
        after = _comparable(with_year_4[engine]["outputs"])
        before.pop("stabilization", None)
        after.pop("stabilization", None)
        assert before == after, engine

    # The block itself DID move — otherwise this test proves nothing.
    assert (
        with_year_4["expense"]["outputs"]["stabilization"]["stabilized_year"] == 4
    )
    assert (
        with_year_2["expense"]["outputs"]["stabilization"]["stabilized_year"] == 2
    )


@pytest.mark.asyncio
async def test_debt_stabilized_metrics_are_untouched_by_the_analyst_year() -> None:
    """The analyst's Stabilization Year is a PROJECTION reporting assumption.
    Debt's stabilized DSCR / debt yield read the Year-3 default (the debt
    engine is not passed the analyst year) — the override moves no covenant
    number."""
    baseline = await _run({})
    overridden = await _run({"stabilization_year": 4})
    for key in ("stabilized_dscr", "stabilized_debt_yield"):
        assert (
            overridden["debt"]["outputs"][key] == baseline["debt"]["outputs"][key]
        ), key


# ── no resolvable year ────────────────────────────────────────────────


def test_no_resolvable_year_publishes_nothing_rather_than_zero() -> None:
    """With no projection there is no stabilized year — and no $0 row."""
    from app.engines.stabilization import build_stabilized_year

    assert (
        build_stabilized_year(
            total_revenue_by_year=[],
            noi_before_reserve_by_year=[],
            cash_noi_by_year=[],
        )
        is None
    )
    # An occupancy path that never reaches the assumption HANDS OFF to the NOI
    # plateau rather than refusing (changed 2026-09-12). The block still says
    # WHICH signal answered the detected hint, so an unmet occupancy
    # assumption stays visible rather than being silently papered over.
    block = build_stabilized_year(
        total_revenue_by_year=[10.0, 11.0],
        noi_before_reserve_by_year=[4.0, 5.0],
        cash_noi_by_year=[3.0, 4.0],
        occupancy_by_year=[0.40, 0.45],
        stabilized_occupancy=0.80,
    )
    assert block is not None
    assert block.detected_signal == "noi_plateau"


def test_a_pre_upgrade_projection_reports_no_before_reserve_noi() -> None:
    """``noi_institutional`` absent → the block says so rather than passing the
    after-reserve number off as a before-reserve one."""
    from app.engines.stabilization import build_stabilized_year

    block = build_stabilized_year(
        total_revenue_by_year=[10_000_000.0, 11_000_000.0],
        noi_before_reserve_by_year=[None, None],
        cash_noi_by_year=[3_000_000.0, 3_300_000.0],
        occupancy_by_year=[0.76, 0.77],
        stabilized_occupancy=0.76,
    )
    assert block is not None
    assert block.stabilized_noi_before_reserve is None
    assert block.stabilized_noi_margin is None
    # Two-year projection → the Year-3 default clamps to Year 2.
    assert block.stabilized_cash_noi == pytest.approx(3_300_000.0)


# ── Step F — worksheet_layout is never engine input ───────────────────


@pytest.mark.asyncio
async def test_worksheet_layout_never_reaches_engine_input() -> None:
    """The Grounded Worksheet's layout is persisted on ``field_overrides``
    (it moved off device-local storage), but it is presentation state. A deal
    carrying it must produce byte-identical engine output."""
    without = await _run({})
    with_layout = await _run(
        {
            "worksheet_layout": {
                "rows": [
                    {"id": "rooms_revenue", "label": "Rooms (renamed)", "order": 3},
                    {"id": "fb_revenue", "memo": "split per Sam"},
                ],
                "version": 2,
            }
        }
    )

    for engine in ("revenue", "fb", "expense", "capital", "debt", "returns",
                   "partnership", "cash_flow"):
        assert _comparable(with_layout[engine]["outputs"]) == _comparable(
            without[engine]["outputs"]
        ), engine


@pytest.mark.asyncio
async def test_worksheet_layout_is_skipped_by_name_not_by_shape() -> None:
    """Even a SCALAR ``worksheet_layout`` never lands on the assumptions.

    The scalar guard in the override loop used to be the only thing keeping the
    layout out of engine input — an incidental type-check, not a contract."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    factory = get_session_factory()
    async with factory() as session:
        deal_id, tenant_id = await _deal_with_overrides(
            session, {"worksheet_layout": "compact"}
        )
        base = await _load_engine_inputs(session, deal_id, tenant_id=tenant_id)

    assert "worksheet_layout" not in base
    assert "worksheet_layout" not in base.get("__sources__", {})


# ── Latent defect, 2026-09-12 ────────────────────────────────────────────
# When a deal's stabilized-occupancy assumption sits above the ceiling of its
# own projected ramp, the occupancy signal used to return (None, None) rather
# than handing off, blanking every Stabilization row on Overview, the IC memo
# and Scenario Analysis. The NOI plateau exists to answer exactly this case.
#
# Found while chasing a blank block on Sam MVP Test 2. That deal turned out
# NOT to hit this path (its target is reached in Year 1; the blank there was a
# stale engine run), so these cases are the defect's real reproduction rather
# than a transcript of what was on screen.
def test_unreached_occupancy_target_falls_through_to_the_noi_plateau() -> None:
    from app.engines.stabilization import resolve_stabilized_year

    index, signal = resolve_stabilized_year(
        occupancy_by_year=[0.716, 0.722, 0.728, 0.733, 0.739],
        stabilized_occupancy=0.762,  # never reached by the ramp above
        noi_by_year=[2_000_000.0, 2_400_000.0, 2_500_000.0, 2_550_000.0, 2_600_000.0],
    )
    assert index is not None, "an unmet occupancy target must not blank the block"
    assert signal == "noi_plateau"


def test_a_reached_occupancy_target_still_wins_over_the_plateau() -> None:
    """The fall-through must not demote the primary signal."""
    from app.engines.stabilization import resolve_stabilized_year

    index, signal = resolve_stabilized_year(
        occupancy_by_year=[0.70, 0.76, 0.78],
        stabilized_occupancy=0.76,
        noi_by_year=[1_000_000.0, 1_500_000.0, 1_600_000.0],
    )
    assert (index, signal) == (1, "occupancy")


def test_no_signal_at_all_still_refuses() -> None:
    """With no occupancy series AND no NOI series there is nothing to derive."""
    from app.engines.stabilization import resolve_stabilized_year

    assert resolve_stabilized_year(
        occupancy_by_year=None, stabilized_occupancy=None, noi_by_year=[]
    ) == (None, None)


# ── FON-59 R-057 — Year 3 after close: default / clamp / override ─────────

_FIVE = dict(
    total_revenue_by_year=[10.0, 11.0, 12.0, 12.4, 12.8],
    noi_before_reserve_by_year=[3.0, 3.6, 4.2, 4.3, 4.4],
    cash_noi_by_year=[2.6, 3.1, 3.7, 3.8, 3.9],
    # Un-displaced: occupancy is at its stabilized assumption from Year 1 —
    # the shape that used to tell testers the deal "stabilizes" in Year 1.
    occupancy_by_year=[0.76, 0.765, 0.77, 0.77, 0.77],
    adr_by_year=[300.0, 309.0, 318.0, 327.0, 337.0],
    stabilized_occupancy=0.76,
)


def test_default_is_index_2_on_a_five_year_hold() -> None:
    from app.engines.stabilization import (
        build_stabilized_year,
        resolve_stabilized_year_index,
    )

    block = build_stabilized_year(**_FIVE)
    assert block is not None
    assert block.stabilized_year_index == 2
    assert block.stabilized_year == 3
    assert block.source == "default_year_3"
    assert block.clamped is False
    assert block.stabilized_cash_noi == pytest.approx(3.7)
    assert block.stabilized_noi_before_reserve == pytest.approx(4.2)
    assert (
        resolve_stabilized_year_index(
            occupancy_by_year=_FIVE["occupancy_by_year"],
            stabilized_occupancy=0.76,
            noi_by_year=_FIVE["cash_noi_by_year"],
        )
        == 2
    )


def test_detected_year_is_still_reported_as_a_hint() -> None:
    from app.engines.stabilization import build_stabilized_year

    block = build_stabilized_year(**_FIVE)
    assert block is not None
    assert block.detected_year == 1
    assert block.detected_signal == "occupancy"
    # …and it does not select the year.
    assert block.stabilized_year == 3


def test_default_clamps_to_the_last_year_on_a_two_year_hold() -> None:
    from app.engines.stabilization import (
        build_stabilized_year,
        resolve_stabilized_year_index,
    )

    block = build_stabilized_year(
        total_revenue_by_year=[10.0, 11.0],
        noi_before_reserve_by_year=[3.0, 3.6],
        cash_noi_by_year=[2.6, 3.1],
    )
    assert block is not None
    assert block.stabilized_year_index == 1
    assert block.stabilized_year == 2
    assert block.source == "default_year_3"
    assert block.clamped is True
    assert block.default_year == 3
    assert (
        resolve_stabilized_year_index(
            occupancy_by_year=None, stabilized_occupancy=None, noi_by_year=[2.6, 3.1]
        )
        == 1
    )
    assert (
        resolve_stabilized_year_index(
            occupancy_by_year=None, stabilized_occupancy=None, noi_by_year=[]
        )
        is None
    )


def test_analyst_override_wins_over_the_default() -> None:
    from app.engines.stabilization import (
        build_stabilized_year,
        resolve_stabilized_year_index,
    )

    block = build_stabilized_year(**_FIVE, stabilization_year=5)
    assert block is not None
    assert block.stabilized_year == 5
    assert block.source == "analyst_override"
    assert block.clamped is False
    assert block.stabilized_cash_noi == pytest.approx(3.9)
    # Detected hint unchanged by the override.
    assert block.detected_year == 1
    assert (
        resolve_stabilized_year_index(
            occupancy_by_year=None,
            stabilized_occupancy=None,
            noi_by_year=_FIVE["cash_noi_by_year"],
            stabilization_year=5,
        )
        == 4
    )
    # Out of range → the default, never a guessed year.
    assert (
        resolve_stabilized_year_index(
            occupancy_by_year=None,
            stabilized_occupancy=None,
            noi_by_year=_FIVE["cash_noi_by_year"],
            stabilization_year=9,
        )
        == 2
    )


def test_debt_stabilized_dscr_reads_year_3_by_default() -> None:
    """Through the debt engine: stabilized DSCR / debt yield are Year 3's —
    even on a deal whose occupancy is already stabilized in Year 1."""
    from app.engines.debt import DebtEngine, DebtEngineInputExt

    noi = [2_000_000.0, 2_300_000.0, 2_600_000.0, 2_700_000.0, 2_800_000.0]
    out = DebtEngine().run(
        DebtEngineInputExt(
            deal_id=uuid4(),
            loan_amount=25_000_000.0,
            ltv=0.65,
            interest_rate=0.068,
            term_years=5,
            amortization_years=30,
            interest_only_years=0,
            noi_by_year=noi,
            occupancy_by_year=[0.76, 0.76, 0.77, 0.77, 0.77],
            stabilized_occupancy=0.76,
        )
    )
    assert out.stabilized_dscr == pytest.approx(
        noi[2] / out.schedule[2].debt_service
    )
    assert out.stabilized_debt_yield == pytest.approx(noi[2] / out.loan_amount)
    assert out.stabilized_dscr != pytest.approx(out.entry_dscr)
