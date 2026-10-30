"""FON-118 — returns by exit year: ONE mechanism, the exit schedule.

``ReturnsEngine._exit_case`` prices "the deal sold at month m" on the canonical
projection. The headline is that function at the deal's own exit; every
projection year-end is the same function at its month. These tests pin:

* the hold-year row IS the headline (exact ``==`` on every figure);
* interim rows read the projected forward year, the schedule's balance at
  that month and the annual stream truncated at their year;
* a row the model does not price is ``not_available`` with a reason — never
  a 0, never a sentinel IRR, never an engine error — while the selected exit
  keeps its one hard stop (``terminal_noi`` ≤ 0);
* the FON-67 52-month Kimpton reconciliation economics (offset 9, refi month
  30, renovation months 4-15, exit 52) through the engine.

Engine-level only (no DB). The runner-level Kimpton golden assertions live
alongside in ``test_exit_schedule_runner.py``.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fondok_schemas.financial import ModelAssumptions
from fondok_schemas.reasons import ReasonCode
from pydantic import ValidationError

from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt

# A ramping deal: stabilizes in Year 3, flat-ish growth after (the same
# economics tests/test_exit_noi_basis.py uses).
NOI = [1_000_000.0, 2_000_000.0, 3_000_000.0, 3_090_000.0, 3_182_700.0]
G = 0.03
CAP = 0.075
LOAN = 24_000_000.0
EQUITY = 17_000_000.0


def _input(**kw: object) -> ReturnsEngineInputExt:
    base: dict[str, object] = dict(
        deal_id=uuid4(),
        assumptions=ModelAssumptions(
            purchase_price=40_000_000.0,
            ltv=0.6,
            interest_rate=0.065,
            amortization_years=30,
            loan_term_years=5,
            hold_years=5,
            exit_cap_rate=CAP,
            revpar_growth=G,
            expense_growth=0.03,
            selling_costs_pct=0.02,
            closing_costs_pct=0.02,
        ),
        year_one_noi=NOI[0],
        noi_by_year=list(NOI),
        annual_debt_service=1_560_000.0,
        loan_amount=LOAN,
        equity=EQUITY,
    )
    base.update(kw)
    return ReturnsEngineInputExt(**base)  # type: ignore[arg-type]


def _run(**kw: object):
    return ReturnsEngine().run(_input(**kw))


# ─────────────── the headline is one row of the schedule ───────────────


def test_hold_year_row_is_the_headline_bit_for_bit() -> None:
    out = _run()
    rows = out.exit_schedule
    assert len(rows) == 5
    assert [r.exit_month for r in rows] == [12, 24, 36, 48, 60]
    assert [r.exit_year for r in rows] == [1, 2, 3, 4, 5]
    sel = rows[4]
    assert sel.is_selected is True
    assert sum(r.is_selected for r in rows) == 1
    assert sel.status == "computed" and sel.reason is None
    # Exact equality — the same arithmetic in the same order, not approx.
    assert sel.levered_irr == out.levered_irr
    assert sel.unlevered_irr == out.unlevered_irr
    assert sel.equity_multiple == out.equity_multiple
    assert sel.terminal_noi == out.terminal_noi
    assert sel.gross_sale_price == out.gross_sale_price
    assert sel.selling_costs == out.selling_costs
    assert sel.net_proceeds == out.net_proceeds
    assert sel.loan_payoff == LOAN  # the scalar loan_balance_at_exit → loan_amount
    assert sel.exit_noi_basis == out.exit_noi_basis == "forward_12m"
    assert sel.exit_noi_period_label == out.exit_noi_period_label == "Forward 12-month NOI (Year 6)"
    assert out.selected_exit_month == 60
    assert out.selected_exit_year == 5


def test_interim_rows_read_the_projected_forward_year() -> None:
    out = _run()
    for k, row in enumerate(out.exit_schedule[:4], start=1):
        assert row.status == "computed", row
        assert row.exit_noi_basis == "forward_12m"
        assert row.exit_noi_period_label == f"Forward 12-month NOI (Year {k + 1}, projected)"
        assert row.terminal_noi == NOI[k]  # the projection HAS year k+1: read, not grown
        assert row.gross_sale_price == NOI[k] / CAP
        assert row.levered_irr is not None and row.unlevered_irr is not None
        assert row.equity_multiple is not None
        assert row.refi_included is False
    trace = out.provenance["exit_schedule[0].terminal_noi"]
    assert trace.formula == "terminal_noi = projected_noi[exit_year + 1]"
    assert {i.name for i in trace.inputs} == {"source_year_noi"}


def test_peak_levered_is_one_year_row_and_matches_the_scalar() -> None:
    out = _run()
    peaks = [r for r in out.exit_schedule if r.is_peak_levered]
    assert len(peaks) == 1
    assert peaks[0].exit_year == out.peak_levered_exit_year
    computed = [r for r in out.exit_schedule if r.status == "computed" and r.exit_year]
    assert peaks[0].levered_irr == max(r.levered_irr for r in computed)  # type: ignore[type-var]


def test_exit_schedule_false_yields_no_rows_and_an_identical_headline() -> None:
    full = _run()
    bare = ReturnsEngine().run(_input(exit_schedule=False).model_copy(update={"deal_id": full.deal_id}))
    assert bare.exit_schedule == []
    assert bare.selected_exit_month == 60 and bare.selected_exit_year == 5
    assert bare.peak_levered_exit_year is None
    assert bare.model_dump(exclude={"exit_schedule", "provenance", "peak_levered_exit_year"}) == full.model_dump(
        exclude={"exit_schedule", "provenance", "peak_levered_exit_year"}
    )
    head_keys = {k for k in full.provenance if not k.startswith("exit_schedule[")}
    assert set(bare.provenance) == head_keys
    for key in head_keys:
        assert bare.provenance[key] == full.provenance[key]


def test_every_row_carries_the_six_traced_keys() -> None:
    out = _run()
    for i, row in enumerate(out.exit_schedule):
        for field in ("levered_irr", "unlevered_irr", "equity_multiple", "terminal_noi",
                      "gross_sale_price", "net_proceeds"):
            trace = out.provenance[f"exit_schedule[{i}].{field}"]
            assert trace.reason is None
            assert trace.value == getattr(row, field)
            assert trace.formula


# ─────────────── refusals — one row, never the headline ───────────────


def test_sentinel_irr_is_inconclusive_never_ten_or_minus_one() -> None:
    # Debt service above NOI every month and a payoff far above the sale price:
    # every levered flow is an outflow, so the monthly solver returns its
    # -0.9999 sentinel. The table says "no IRR"; the headline keeps the raw
    # sentinel it always carried.
    out = _run(annual_debt_service=5_000_000.0, loan_balance_at_exit=200_000_000.0)
    assert out.levered_irr == -0.9999
    for row in out.exit_schedule:
        assert row.status == "not_available"
        assert row.reason == ReasonCode.INCONCLUSIVE
        assert row.levered_irr is None
        assert row.unlevered_irr is not None  # the asset side has a real IRR
        assert row.equity_multiple is not None
        assert "one sign" in (row.reason_detail or "")
    for i in range(len(out.exit_schedule)):
        t = out.provenance[f"exit_schedule[{i}].levered_irr"]
        assert t.reason == ReasonCode.INCONCLUSIVE and t.formula is None


def test_non_positive_exit_noi_dashes_the_row_and_still_hard_stops_the_headline() -> None:
    noi = [1_000_000.0, -1_000_000.0, 2_000_000.0, 3_000_000.0, 4_000_000.0]
    out = _run(noi_by_year=noi)
    y1 = out.exit_schedule[0]
    assert y1.status == "not_available" and y1.reason == ReasonCode.NO_SALE_VALUE
    assert y1.terminal_noi == -1_000_000.0  # the figure that explains the dash
    assert y1.gross_sale_price is None and y1.net_proceeds is None and y1.levered_irr is None
    # Year 2 flows the negative operating year through (FON-63) and prices fine.
    y2 = out.exit_schedule[1]
    assert y2.status == "computed" and y2.terminal_noi == 2_000_000.0
    assert out.exit_schedule[4].is_selected and out.exit_schedule[4].status == "computed"
    # The selected exit keeps the ONE hard stop.
    with pytest.raises(ValidationError):
        _run(noi_by_year=[1_000_000.0, 2_000_000.0, 3_000_000.0, 3_000_000.0, -5.0])


def test_development_construction_window_dashes_only_the_rows_inside_it() -> None:
    acq = _run()
    dev = _run(development_like=True, construction_end_month=24)
    for row in dev.exit_schedule[:2]:
        assert row.status == "not_available"
        assert row.reason == ReasonCode.UNDER_CONSTRUCTION
        assert "through month 24" in (row.reason_detail or "")
        assert row.levered_irr is None and row.gross_sale_price is None
    for a, d in zip(acq.exit_schedule[2:], dev.exit_schedule[2:], strict=True):
        assert d.status == "computed"
        assert d.model_dump() == a.model_dump()  # numbers untouched
    assert dev.levered_irr == acq.levered_irr
    # A development deal with NO stated window has no construction in the model.
    open_dev = _run(development_like=True)
    assert all(r.status == "computed" for r in open_dev.exit_schedule)
    # The flag alone never changes a number.
    assert open_dev.model_dump(exclude={"deal_id"}) == acq.model_dump(exclude={"deal_id"})
    # And the window without the flag (an acquisition's renovation) dashes nothing.
    reno = _run(construction_end_month=24)
    assert all(r.status == "computed" for r in reno.exit_schedule)


def test_capital_still_deploying_refuses_a_sale_before_completion() -> None:
    # Deferred capital deploys across calendar year 1 → months 13-24 (offset 0).
    out = _run(deferred_capital=2_000_000.0, deferred_capital_year=1)
    y1, y2 = out.exit_schedule[0], out.exit_schedule[1]
    assert y1.status == "not_available" and y1.reason == ReasonCode.CAPITAL_DEPLOYING
    assert "through month 24" in (y1.reason_detail or "")
    assert y2.status == "computed"
    # An explicit draw schedule: a draw at month 18 dashes Year 1 only.
    draws = [EQUITY - 1_000_000.0] + [0.0] * 17 + [1_000_000.0]
    out2 = _run(equity_draw_schedule=draws)
    assert out2.exit_schedule[0].reason == ReasonCode.CAPITAL_DEPLOYING
    assert out2.exit_schedule[1].status == "computed"
    assert out2.exit_schedule[4].is_selected and out2.exit_schedule[4].status == "computed"


# ─────────────── basis rules per row ───────────────


def test_stabilized_basis_rows_grow_their_stabilized_year_to_the_exit() -> None:
    out = _run(exit_noi_basis="stabilized", stabilized_year=3)
    rows = out.exit_schedule
    assert rows[1].exit_noi_period_label == (
        "Exit-year NOI (Year 2; stabilization in Year 3 falls after the exit)"
    )
    assert rows[1].terminal_noi == NOI[1]
    assert rows[2].exit_noi_period_label == "Stabilized NOI (Year 3, the exit year)"
    assert rows[2].terminal_noi == NOI[2]
    assert rows[3].exit_noi_period_label == "Stabilized NOI (Year 3, grown to Year 4)"
    assert rows[3].terminal_noi == pytest.approx(NOI[2] * (1 + G) ** 1)
    assert rows[4].terminal_noi == out.terminal_noi == pytest.approx(NOI[2] * (1 + G) ** 2)


def test_stabilized_without_a_year_resolves_once_for_every_row() -> None:
    out = _run(exit_noi_basis="stabilized")
    labels = [r.exit_noi_period_label for r in out.exit_schedule]
    # One stabilized year appears across the rows (grown / the exit year / after).
    years = {int(lbl.split("Year ")[1].split(",")[0].split(")")[0].split(";")[0])
             for lbl in labels if lbl.startswith("Stabilized NOI")}
    assert len(years) == 1


def test_terminal_noi_override_applies_to_the_selected_exit_only() -> None:
    out = _run(terminal_noi_override=2_500_000.0)
    sel = out.exit_schedule[4]
    assert sel.exit_noi_basis == "override" and sel.terminal_noi == 2_500_000.0
    assert out.terminal_noi == 2_500_000.0
    for k, row in enumerate(out.exit_schedule[:4], start=1):
        assert row.exit_noi_basis == "forward_12m"
        assert row.terminal_noi == NOI[k]
        note = out.provenance[f"exit_schedule[{k - 1}].terminal_noi"].note or ""
        assert "selected exit only" in note


# ─────────────── exit month geometry ───────────────


def test_off_cycle_selected_exit_is_its_own_row_in_month_order() -> None:
    out = _run(exit_month_override=30)
    months = [r.exit_month for r in out.exit_schedule]
    assert months == [12, 24, 30, 36, 48, 60]
    sel = out.exit_schedule[2]
    assert sel.is_selected and sel.exit_year is None
    assert out.selected_exit_month == 30 and out.selected_exit_year is None
    assert sel.levered_irr == out.levered_irr
    assert sel.equity_multiple == out.equity_multiple  # the monthly convention
    assert not sel.is_peak_levered  # peak is a YEAR row
    assert sum(r.is_selected for r in out.exit_schedule) == 1


def test_mid_year_close_without_override_sells_after_the_last_year_row() -> None:
    out = _run(acquisition_month_offset=9)
    months = [r.exit_month for r in out.exit_schedule]
    assert months == [3, 15, 27, 39, 51, 60]
    assert out.exit_schedule[-1].is_selected and out.exit_schedule[-1].exit_year is None
    assert out.selected_exit_month == 60 and out.selected_exit_year is None


def test_override_on_a_year_end_month_makes_that_row_the_selected_one() -> None:
    out = _run(exit_month_override=36)
    assert [r.exit_month for r in out.exit_schedule] == [12, 24, 36, 48, 60]
    sel = out.exit_schedule[2]
    assert sel.is_selected and sel.exit_year == 3
    assert out.selected_exit_year == 3
    assert sel.levered_irr == out.levered_irr
    # Rows AFTER the selected exit are still shown (years within the hold).
    assert out.exit_schedule[4].status == "computed"


def test_clamped_override_is_reported_on_the_selected_row() -> None:
    out = _run(exit_month_override=999)
    assert out.selected_exit_month == 60 and out.selected_exit_year == 5
    sel = out.exit_schedule[4]
    assert sel.is_selected and sel.status == "computed"
    assert "999" in (sel.reason_detail or "") and "month 60" in (sel.reason_detail or "")
    assert sel.levered_irr == out.levered_irr


def test_interim_payoff_reads_the_monthly_schedule_and_the_selected_keeps_the_scalar() -> None:
    by_month = [LOAN - 10_000.0 * m for m in range(1, 61)]
    out = _run(loan_balance_by_month=by_month, loan_balance_at_exit=23_000_000.0)
    for k, row in enumerate(out.exit_schedule[:4], start=1):
        assert row.loan_payoff == by_month[12 * k - 1]
        assert "loan_balance_by_month" in (out.provenance[f"exit_schedule[{k - 1}].net_proceeds"].note or "")
    sel = out.exit_schedule[4]
    assert sel.loan_payoff == 23_000_000.0  # exactly the headline's figure
    assert sel.net_proceeds == out.net_proceeds


def test_legacy_single_balance_when_no_monthly_schedule_is_given() -> None:
    out = _run(loan_balance_at_exit=23_000_000.0)
    assert all(r.loan_payoff == 23_000_000.0 for r in out.exit_schedule)


# ─────────────── FON-67 — the 52-month Kimpton reconciliation ───────────────


def _fon67_input() -> ReturnsEngineInputExt:
    """``tests/test_monthly_cashflow.py::_kimpton_input`` economics, expressed
    as the returns engine receives them from the runner: 9/30 close (offset
    9), renovation drawn across calendar 2026 (months 4-15), refinance at
    month 30, sale at month 52 on a pinned terminal NOI."""
    senior, refi = 23_187_000.0, 51_240_548.0
    r_senior = (3_995_804 - 2_447_736) / senior
    r_refi = (4_594_429 - 651_996) / refi
    total_capital = 36_436_802 + 4_943_400 + 284_041 + 299_057 + 728_736
    reno = 4_943_400.0
    senior_ds = senior * r_senior
    refi_ds = refi * r_refi
    return ReturnsEngineInputExt(
        deal_id=uuid4(),
        assumptions=ModelAssumptions(
            purchase_price=36_436_802.0,
            ltv=senior / 36_436_802.0,
            interest_rate=r_senior,
            amortization_years=30,
            loan_term_years=5,
            hold_years=5,
            exit_cap_rate=0.065,
            revpar_growth=0.03,
            expense_growth=0.03,
            selling_costs_pct=0.015,
            transfer_tax_pct=0.0105,
            closing_costs_pct=0.02,
        ),
        year_one_noi=1_013_046.0,
        noi_by_year=[1_013_046.0, 3_025_953.0, 3_995_804.0, 4_253_258.0, 4_594_429.0],
        # Months 1-30 senior, 31-60 refi (the engine re-times from refi_month).
        debt_service_by_year=[senior_ds, senior_ds, senior_ds, refi_ds, refi_ds],
        annual_debt_service=senior_ds,
        loan_amount=senior,
        loan_balance_at_exit=refi,  # the runner forwards debt.balance_at_exit (= refi balance)
        loan_balance_by_month=[senior] * 60,  # interest-only senior
        refi_balance=refi,
        refi_cash_out=refi - senior - refi * 0.01,
        refi_year=3,
        refi_time_years=2.5,  # month 30
        deferred_capital=reno,
        deferred_capital_year=1,  # calendar 2026 → months 4-15 at offset 9
        acquisition_month_offset=9,
        exit_month_override=52,
        terminal_noi_override=4_440_841.0,
        equity=total_capital - senior,
    )


def test_fon67_52_month_exit_reconciles_and_the_schedule_reads_each_date() -> None:
    out = ReturnsEngine().run(_fon67_input())
    rows = out.exit_schedule
    assert [r.exit_month for r in rows] == [3, 15, 27, 39, 51, 52]
    sel = rows[-1]
    assert sel.is_selected and sel.exit_month == 52 and sel.exit_year is None
    assert out.selected_exit_month == 52 and out.selected_exit_year is None
    assert sel.levered_irr == out.levered_irr
    assert sel.equity_multiple == out.equity_multiple
    assert sel.refi_included is True
    assert sel.loan_payoff == 51_240_548.0  # the headline's scalar: the refi balance
    assert sel.exit_noi_basis == "override" and sel.terminal_noi == 4_440_841.0
    # Within 2 pts of the source's 39.59%, as the monthly test already pins.
    assert abs(out.levered_irr - 0.3959) < 0.02, out.levered_irr
    # Year 1 (month 3): the renovation draws run to month 15 — not priced.
    y1 = rows[0]
    assert y1.exit_year == 1 and y1.status == "not_available"
    assert y1.reason == ReasonCode.CAPITAL_DEPLOYING
    assert "through month 15" in (y1.reason_detail or "")
    # Year 2 (month 15): capital fully in, refi not yet drawn → senior payoff.
    y2 = rows[1]
    assert y2.exit_year == 2 and y2.status == "computed"
    assert y2.refi_included is False
    assert y2.loan_payoff == 23_187_000.0  # loan_balance_by_month[14]
    assert y2.exit_noi_basis == "forward_12m"  # the pin is the selected exit's
    assert y2.terminal_noi == 3_995_804.0  # projected Year 3
    # Year 3 (month 27): still before the month-30 refinance.
    y3 = rows[2]
    assert y3.exit_year == 3 and y3.refi_included is False and y3.status == "computed"
    # Year 4 (month 39): the refinance has happened — payoff is the refi balance.
    y4 = rows[3]
    assert y4.exit_year == 4 and y4.refi_included is True
    assert y4.loan_payoff == 51_240_548.0
    assert y4.status == "computed"
    # Year 5 (month 51): the hold year, the final year grown as the headline would.
    y5 = rows[4]
    assert y5.exit_year == 5 and y5.exit_noi_period_label == "Forward 12-month NOI (Year 6)"
    assert y5.status == "computed"
    assert out.peak_levered_exit_year in {2, 3, 4, 5}
