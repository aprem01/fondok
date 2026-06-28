"""Debt Stack v2 — Wave 4 W4.4.

Sixteen tests covering:

 1. Byte-identical backward compatibility with the legacy single-loan
    engine (single senior tranche → same monthly schedule).
 2-3. Two- and three-tranche aggregation correctness.
 4-5. IO stub vs P&I amortization mechanics.
 6. Debt yield uses EOP outstanding balance.
 7. Blended DSCR across the full stack.
 8-10. Refi test pass / fail / cash-to-close branches.
 11. Senior tranche must hold priority_rank=1.
 12-13. Upfront fee + exit fee accounting.
 14. Empty tranche list rejected at the schema layer.
 15. Returns engine consumes the stacked DS series.
 16. DSCR sensitivity grid (re-uses pricing_sensitivity helpers).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from fondok_schemas.debt_stack import (
    DebtStackInput,
    DebtTranche,
)
from fondok_schemas.financial import ModelAssumptions

from app.engines.debt import (
    DebtEngine,
    DebtEngineInputExt,
    build_amort_schedule,
    build_stack_schedule,
    run_refi_test,
)
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt
from app.engines.pricing_sensitivity import (
    _flex_returns_input,
    run_sensitivity_grid,
)


# ────────────────────────── Helpers ──────────────────────────


def _noi_series(start: float = 3_500_000.0, growth: float = 0.06, n: int = 6) -> list[float]:
    """Standard 5-year NOI series (Y1..Y6) used across most tests."""
    return [start * ((1.0 + growth) ** i) for i in range(n)]


def _senior(
    principal: float = 24_000_000.0,
    rate: float = 0.06,
    io_months: int = 0,
    amort_months: int = 360,
    upfront: float = 0.0,
    exit_fee: float = 0.0,
    label: str | None = None,
) -> DebtTranche:
    return DebtTranche(
        name="senior",
        label=label,
        principal_usd=principal,
        rate_pct=rate,
        io_period_months=io_months,
        amortization_months=amort_months,
        upfront_fee_pct=upfront,
        exit_fee_pct=exit_fee,
        is_senior=True,
        priority_rank=1,
    )


def _mezz(
    principal: float = 8_000_000.0,
    rate: float = 0.11,
    io_months: int = 60,
    amort_months: int = 60,
    label: str | None = None,
) -> DebtTranche:
    return DebtTranche(
        name="mezz",
        label=label,
        principal_usd=principal,
        rate_pct=rate,
        io_period_months=io_months,
        amortization_months=amort_months,
        is_senior=False,
        priority_rank=2,
    )


def _pref_equity(
    principal: float = 4_000_000.0,
    rate: float = 0.14,
    io_months: int = 60,
    amort_months: int = 60,
    label: str | None = None,
) -> DebtTranche:
    return DebtTranche(
        name="pref_equity",
        label=label,
        principal_usd=principal,
        rate_pct=rate,
        io_period_months=io_months,
        amortization_months=amort_months,
        is_senior=False,
        priority_rank=3,
    )


# ────────────────────────── Tests 1-3: aggregation ──────────────────────────


def test_single_senior_tranche_matches_legacy_single_loan() -> None:
    """Single senior tranche must reproduce the legacy engine's schedule byte-identically."""
    deal = uuid4()
    loan = 24_000_000.0
    rate = 0.06
    term_years = 5
    amort_years = 30
    noi = _noi_series()[:term_years]

    legacy = DebtEngine().run(
        DebtEngineInputExt(
            deal_id=deal,
            loan_amount=loan,
            ltv=0.65,
            interest_rate=rate,
            term_years=term_years,
            amortization_years=amort_years,
            interest_only_years=0,
            noi_by_year=noi,
        )
    )

    stack = build_stack_schedule(
        DebtStackInput(
            deal_id=deal,
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[_senior(principal=loan, rate=rate, amort_months=amort_years * 12)],
            noi_by_year=noi,
            term_years=term_years,
            refi_test_year=None,
        )
    )

    # Compare year-by-year debt service and ending balance.
    for y in range(term_years):
        legacy_year = legacy.schedule[y]
        stack_year = stack.schedules[0].years[y]
        assert legacy_year.debt_service == pytest.approx(
            stack_year.debt_service_usd, rel=1e-9
        )
        assert legacy_year.ending_balance == pytest.approx(
            stack_year.ending_balance_usd, rel=1e-9
        )
        # Total stack DS for that year matches the legacy single-loan DS.
        assert stack.total_ds_by_year[y] == pytest.approx(
            legacy_year.debt_service, rel=1e-9
        )


def test_two_tranches_senior_plus_mezz_aggregated_ds_correct() -> None:
    """Stack DS Y1 equals senior DS Y1 + mezz DS Y1 exactly."""
    noi = _noi_series()[:5]
    deal = uuid4()

    senior_tranche = _senior(principal=24_000_000.0, rate=0.06)
    mezz_tranche = _mezz(principal=8_000_000.0, rate=0.11)

    senior_only = build_stack_schedule(
        DebtStackInput(
            deal_id=deal,
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[senior_tranche],
            noi_by_year=noi,
            term_years=5,
            refi_test_year=None,
        )
    )
    mezz_only = build_stack_schedule(
        DebtStackInput(
            deal_id=deal,
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[_senior(principal=8_000_000.0, rate=0.11)],  # senior shape for stand-alone amort
            noi_by_year=noi,
            term_years=5,
            refi_test_year=None,
        )
    )
    combined = build_stack_schedule(
        DebtStackInput(
            deal_id=deal,
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[senior_tranche, mezz_tranche],
            noi_by_year=noi,
            term_years=5,
            refi_test_year=None,
        )
    )

    # Y1 stack DS = senior Y1 DS + mezz Y1 DS (each computed independently).
    # We use the same per-tranche amort math, so the sum lands exactly.
    expected_y1 = (
        combined.debt_service_per_tranche_by_year["senior"][0]
        + combined.debt_service_per_tranche_by_year["mezz"][0]
    )
    assert combined.total_ds_by_year[0] == pytest.approx(expected_y1, rel=1e-9)
    # Senior-only Y1 DS must equal the stack senior-Y1 DS.
    assert (
        combined.debt_service_per_tranche_by_year["senior"][0]
        == pytest.approx(senior_only.total_ds_by_year[0], rel=1e-9)
    )


def test_three_tranches_senior_mezz_pref_aggregated() -> None:
    """Three-tranche stack: total LTC + total DS + weighted rate sanity."""
    noi = _noi_series()[:5]
    out = build_stack_schedule(
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[_senior(), _mezz(), _pref_equity()],
            noi_by_year=noi,
            term_years=5,
            refi_test_year=None,
        )
    )
    # Total debt = 24 + 8 + 4 = 36M; LTC = 36/40 = 0.90.
    assert out.total_debt_usd == pytest.approx(36_000_000.0)
    assert out.cumulative_ltc == pytest.approx(0.90, rel=1e-6)
    # Weighted-average rate = (24*6 + 8*11 + 4*14) / 36 = 8.0%.
    expected_w = (24 * 6 + 8 * 11 + 4 * 14) / 36 / 100.0
    assert out.weighted_avg_rate_pct == pytest.approx(expected_w, rel=1e-6)
    # Total DS Y1 = sum across all three tranches.
    expected_total_y1 = sum(
        out.debt_service_per_tranche_by_year[name][0]
        for name in ("senior", "mezz", "pref_equity")
    )
    assert out.total_ds_by_year[0] == pytest.approx(expected_total_y1, rel=1e-9)


# ────────────────────────── Tests 4-5: IO vs P&I ──────────────────────────


def test_io_stub_period_no_principal_payment() -> None:
    """During the IO stub period principal must be zero every month."""
    tranche = _senior(principal=20_000_000.0, rate=0.06, io_months=24, amort_months=360)
    monthly = build_amort_schedule(tranche, term_years=5)
    # First 24 months are IO — principal exactly 0.
    for m in monthly[:24]:
        assert m.principal == 0.0
        # Interest equals balance × monthly rate, payment equals interest.
        assert m.payment == pytest.approx(m.interest, rel=1e-9)
    # Balance unchanged through IO stub.
    assert monthly[23].ending_balance == pytest.approx(
        tranche.principal_usd, rel=1e-9
    )


def test_pi_amortization_after_io_stub() -> None:
    """After the IO stub the schedule starts amortizing principal."""
    tranche = _senior(
        principal=20_000_000.0, rate=0.06, io_months=12, amort_months=360
    )
    monthly = build_amort_schedule(tranche, term_years=5)
    # Month 13 is the first amortizing month.
    m13 = monthly[12]
    assert m13.principal > 0.0
    # Ending balance after month 13 must be lower than principal.
    assert m13.ending_balance < tranche.principal_usd
    # Total month-13 payment = interest + principal.
    assert m13.payment == pytest.approx(m13.interest + m13.principal, rel=1e-6)


# ────────────────────────── Test 6: debt yield uses EOP ──────────────────────────


def test_debt_yield_by_year_uses_eop_balance() -> None:
    """Debt yield year-Y = NOI(Y) / sum of EOP balances across tranches at year-Y."""
    noi = _noi_series()[:5]
    out = build_stack_schedule(
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[_senior(principal=24_000_000.0, rate=0.06)],
            noi_by_year=noi,
            term_years=5,
            refi_test_year=None,
        )
    )
    # EOP balance Y1 from the schedule.
    eob_y1 = out.schedules[0].years[0].ending_balance_usd
    expected = noi[0] / eob_y1
    assert out.debt_yield_by_year[0] == pytest.approx(expected, rel=1e-9)


# ────────────────────────── Test 7: blended DSCR ──────────────────────────


def test_dscr_blended_when_two_tranches() -> None:
    """Blended DSCR = NOI / (senior DS + mezz DS)."""
    noi = _noi_series()[:5]
    senior = _senior(principal=24_000_000.0, rate=0.06)
    mezz = _mezz(principal=8_000_000.0, rate=0.11)
    out = build_stack_schedule(
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[senior, mezz],
            noi_by_year=noi,
            term_years=5,
            refi_test_year=None,
        )
    )
    senior_ds_y1 = out.debt_service_per_tranche_by_year["senior"][0]
    mezz_ds_y1 = out.debt_service_per_tranche_by_year["mezz"][0]
    expected = noi[0] / (senior_ds_y1 + mezz_ds_y1)
    assert out.dscr_blended_by_year[0] == pytest.approx(expected, rel=1e-9)
    # Per-tranche cumulative DSCR: senior alone vs. senior+mezz.
    assert out.dscr_by_year_per_tranche["senior"][0] == pytest.approx(
        noi[0] / senior_ds_y1, rel=1e-9
    )
    assert out.dscr_by_year_per_tranche["mezz"][0] == pytest.approx(expected, rel=1e-9)


# ────────────────────────── Tests 8-10: refi test ──────────────────────────


def test_refi_test_passes_when_dy_above_threshold() -> None:
    """Healthy NOI growth + modest senior debt → refi clears the floor."""
    # 6-year NOI: refi at year 5 reads NOI[5] (year 6) for the sizing.
    noi = _noi_series(start=3_500_000.0, growth=0.06, n=6)
    out = build_stack_schedule(
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[_senior(principal=20_000_000.0, rate=0.06)],
            noi_by_year=noi,
            term_years=5,
            refi_test_year=5,
            refi_market_debt_yield_pct=0.09,
            refi_market_dscr_min=1.30,
            refi_market_rate_pct=0.075,
            exit_cap_rate=0.07,
        )
    )
    assert out.refi_test is not None
    assert out.refi_test.can_refi is True
    assert out.refi_test.cash_to_close_equity == 0.0
    assert out.refi_test.refi_dscr is not None
    assert out.refi_test.refi_dscr >= 1.30


def test_refi_test_fails_when_dy_below_threshold() -> None:
    """Heavy senior debt + flat NOI → max refi debt < outstanding."""
    noi = [2_500_000.0] * 7  # weak, flat NOI
    out = build_stack_schedule(
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=60_000_000.0,
            keys=200,
            # Big senior loan + IO so balance stays high through year 5.
            tranches=[
                _senior(
                    principal=45_000_000.0,
                    rate=0.06,
                    io_months=60,
                    amort_months=360,
                )
            ],
            noi_by_year=noi,
            term_years=5,
            refi_test_year=5,
            refi_market_debt_yield_pct=0.09,
            refi_market_dscr_min=1.30,
            refi_market_rate_pct=0.075,
            exit_cap_rate=0.07,
        )
    )
    assert out.refi_test is not None
    assert out.refi_test.can_refi is False
    # Either DY or DSCR pinned the failure.
    assert (
        out.refi_test.cash_to_close_equity > 0.0
        or (out.refi_test.refi_dscr is not None and out.refi_test.refi_dscr < 1.30)
    )


def test_refi_test_cash_to_close_equity_computed() -> None:
    """When outstanding > max refi debt, cash_to_close_equity = the shortfall."""
    noi = [3_000_000.0] * 7
    out = build_stack_schedule(
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=50_000_000.0,
            keys=200,
            tranches=[
                _senior(
                    principal=40_000_000.0,
                    rate=0.06,
                    io_months=60,
                    amort_months=360,
                )
            ],
            noi_by_year=noi,
            term_years=5,
            refi_test_year=5,
            refi_market_debt_yield_pct=0.09,
            refi_market_dscr_min=1.30,
            refi_market_rate_pct=0.075,
            exit_cap_rate=0.07,
        )
    )
    rt = out.refi_test
    assert rt is not None
    # Binding constraint is the tighter of (NOI/DY) and (NOI/(DSCR_min*rate)).
    # 3M/0.09 = 33.33M; 3M/(1.30*0.075) = 30.77M → DSCR is binding.
    max_dy = 3_000_000.0 / 0.09
    max_dscr = 3_000_000.0 / (1.30 * 0.075)
    expected_max = min(max_dy, max_dscr)
    assert rt.max_refi_debt_usd == pytest.approx(expected_max, rel=1e-6)
    assert rt.outstanding_balance_usd > 35_000_000.0
    # Shortfall = outstanding - max.
    expected_shortfall = rt.outstanding_balance_usd - rt.max_refi_debt_usd
    assert rt.cash_to_close_equity == pytest.approx(expected_shortfall, rel=1e-6)


# ────────────────────────── Test 11: rank invariant ──────────────────────────


def test_priority_rank_enforced() -> None:
    """is_senior=True must align with priority_rank=1, and vice versa."""
    # 11a. senior tranche with rank=2 — rejected at the tranche level.
    with pytest.raises(Exception):
        DebtTranche(
            name="senior",
            principal_usd=20_000_000.0,
            rate_pct=0.06,
            is_senior=True,
            priority_rank=2,
        )
    # 11b. mezz tranche claiming rank=1 — rejected.
    with pytest.raises(Exception):
        DebtTranche(
            name="mezz",
            principal_usd=8_000_000.0,
            rate_pct=0.11,
            is_senior=False,
            priority_rank=1,
        )
    # 11c. Stack with no senior — rejected by stack-level validator.
    with pytest.raises(Exception):
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[_mezz(), _pref_equity()],
            noi_by_year=_noi_series()[:5],
            term_years=5,
        )


# ────────────────────────── Tests 12-13: fees ──────────────────────────


def test_upfront_fee_added_to_basis_at_funding() -> None:
    """Upfront fees aggregate into total_upfront_fees_usd at the stack level."""
    out = build_stack_schedule(
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[
                _senior(principal=24_000_000.0, upfront=1.0),  # 1.0% = 240,000
                _mezz(principal=8_000_000.0),
            ],
            noi_by_year=_noi_series()[:5],
            term_years=5,
            refi_test_year=None,
        )
    )
    assert out.total_upfront_fees_usd == pytest.approx(240_000.0, rel=1e-6)
    # Per-tranche schedule echo.
    assert out.schedules[0].upfront_fee_usd == pytest.approx(240_000.0, rel=1e-6)
    assert out.schedules[1].upfront_fee_usd == 0.0


def test_exit_fee_added_to_ds_at_maturity() -> None:
    """An exit fee on a tranche shows up in the final-year DS bucket."""
    no_fee = build_stack_schedule(
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[_senior(principal=24_000_000.0)],
            noi_by_year=_noi_series()[:5],
            term_years=5,
            refi_test_year=None,
        )
    )
    with_fee = build_stack_schedule(
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[_senior(principal=24_000_000.0, exit_fee=2.0)],  # 2.0% = 480,000
            noi_by_year=_noi_series()[:5],
            term_years=5,
            refi_test_year=None,
        )
    )
    final_year = 4  # Y5 = index 4
    diff = (
        with_fee.schedules[0].years[final_year].debt_service_usd
        - no_fee.schedules[0].years[final_year].debt_service_usd
    )
    assert diff == pytest.approx(480_000.0, rel=1e-6)
    # Earlier years unaffected.
    assert (
        with_fee.schedules[0].years[0].debt_service_usd
        == pytest.approx(no_fee.schedules[0].years[0].debt_service_usd, rel=1e-9)
    )


# ────────────────────────── Test 14: empty tranches rejected ──────────────────────────


def test_tranches_empty_input_rejected() -> None:
    """Empty tranche list must be rejected at the schema layer."""
    with pytest.raises(Exception):
        DebtStackInput(
            deal_id=uuid4(),
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[],
            noi_by_year=_noi_series()[:5],
            term_years=5,
        )


# ────────────────────────── Test 15: returns uses stacked DS ──────────────────────────


def test_returns_engine_uses_total_ds_with_stacked_input() -> None:
    """Returns engine consumes debt_service_by_year and produces a lower
    Y1 cash-on-cash than the scalar-only single-loan path because mezz
    + pref equity drag CFAD down further."""
    noi = _noi_series()[:5]
    deal = uuid4()
    stack = build_stack_schedule(
        DebtStackInput(
            deal_id=deal,
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[_senior(), _mezz(), _pref_equity()],
            noi_by_year=noi,
            term_years=5,
            refi_test_year=None,
        )
    )
    assumptions = ModelAssumptions(
        purchase_price=40_000_000.0,
        ltv=0.65,
        interest_rate=0.06,
        amortization_years=30,
        loan_term_years=5,
        hold_years=5,
        exit_cap_rate=0.07,
        revpar_growth=0.04,
        expense_growth=0.035,
        selling_costs_pct=0.02,
        closing_costs_pct=0.02,
    )
    # Stacked returns input — uses full DS series.
    stacked_in = ReturnsEngineInputExt(
        deal_id=deal,
        assumptions=assumptions,
        year_one_noi=noi[0],
        noi_by_year=noi,
        annual_debt_service=stack.total_ds_by_year[0],
        debt_service_by_year=stack.total_ds_by_year,
        loan_amount=stack.total_debt_usd,
        loan_balance_at_exit=sum(
            s.years[-1].ending_balance_usd for s in stack.schedules
        ),
        equity=4_000_000.0,  # GP/LP equity portion below the stack
    )
    senior_only_stack = build_stack_schedule(
        DebtStackInput(
            deal_id=deal,
            purchase_price_usd=40_000_000.0,
            keys=200,
            tranches=[_senior()],
            noi_by_year=noi,
            term_years=5,
            refi_test_year=None,
        )
    )
    senior_only_in = ReturnsEngineInputExt(
        deal_id=deal,
        assumptions=assumptions,
        year_one_noi=noi[0],
        noi_by_year=noi,
        annual_debt_service=senior_only_stack.total_ds_by_year[0],
        debt_service_by_year=senior_only_stack.total_ds_by_year,
        loan_amount=senior_only_stack.total_debt_usd,
        loan_balance_at_exit=senior_only_stack.schedules[0].years[-1].ending_balance_usd,
        equity=4_000_000.0,
    )
    stacked = ReturnsEngine().run(stacked_in)
    senior_only = ReturnsEngine().run(senior_only_in)
    # Stacked deal pays more DS → lower Y1 CoC → lower CFAD.
    assert stacked.year_one_coc < senior_only.year_one_coc
    # The CFAD difference Y1 = mezz DS Y1 + pref DS Y1.
    expected_diff = (
        stack.debt_service_per_tranche_by_year["mezz"][0]
        + stack.debt_service_per_tranche_by_year["pref_equity"][0]
    )
    actual_diff = stacked_in.equity * (senior_only.year_one_coc - stacked.year_one_coc)
    assert actual_diff == pytest.approx(expected_diff, rel=1e-6)


# ────────────────────────── Test 16: DSCR sensitivity grid ──────────────────────────


def test_dscr_sensitivity_grid() -> None:
    """A 5×5 grid of (senior_rate ±100bp, mezz_rate ±200bp) → DSCR Y1.

    Re-uses pricing_sensitivity's ``_flex_returns_input`` helper to wire
    a returns input across the rate axes and assert monotonicity (higher
    rates → lower DSCR Y1).
    """
    noi = _noi_series()[:5]
    deal = uuid4()
    senior_rate_axis = [0.05, 0.055, 0.06, 0.065, 0.07]
    mezz_rate_axis = [0.09, 0.10, 0.11, 0.12, 0.13]

    grid: list[list[float]] = []
    for sr in senior_rate_axis:
        row: list[float] = []
        for mr in mezz_rate_axis:
            out = build_stack_schedule(
                DebtStackInput(
                    deal_id=deal,
                    purchase_price_usd=40_000_000.0,
                    keys=200,
                    tranches=[
                        _senior(principal=24_000_000.0, rate=sr),
                        _mezz(principal=8_000_000.0, rate=mr),
                    ],
                    noi_by_year=noi,
                    term_years=5,
                    refi_test_year=None,
                )
            )
            row.append(out.dscr_blended_by_year[0])
        grid.append(row)

    # Confirm shape.
    assert len(grid) == 5
    assert all(len(row) == 5 for row in grid)
    # Monotonicity: along a row, increasing mezz rate → lower DSCR Y1.
    for row in grid:
        assert all(row[i] >= row[i + 1] for i in range(4))
    # Monotonicity: down a column, increasing senior rate → lower DSCR Y1.
    for col in range(5):
        col_values = [grid[row][col] for row in range(5)]
        assert all(col_values[i] >= col_values[i + 1] for i in range(4))
    # Center cell — sanity: blended DSCR for the worked example.
    # Senior 6% + mezz 11% on $24M + $8M against $3.5M NOI clears the
    # IC stress floor (1.30x) but stays under 1.5x by design.
    center = grid[2][2]
    assert center > 1.30  # passes IC stress floor at 1.30x
    assert center < 2.0  # sanity ceiling

    # Bonus: pricing_sensitivity helper still functions on the stacked
    # returns input (regression guard against the helper's API).
    assumptions = ModelAssumptions(
        purchase_price=40_000_000.0,
        ltv=0.65,
        interest_rate=0.06,
        amortization_years=30,
        loan_term_years=5,
        hold_years=5,
        exit_cap_rate=0.07,
        revpar_growth=0.04,
        expense_growth=0.035,
        selling_costs_pct=0.02,
        closing_costs_pct=0.02,
    )
    base_in = ReturnsEngineInputExt(
        deal_id=deal,
        assumptions=assumptions,
        year_one_noi=noi[0],
        noi_by_year=noi,
        annual_debt_service=2_000_000.0,
        debt_service_by_year=[2_000_000.0] * 5,
        equity=4_000_000.0,
    )
    flexed = _flex_returns_input(base_in, exit_cap_pct=0.075, noi_multiplier=0.9)
    assert flexed.assumptions.exit_cap_rate == pytest.approx(0.075)
    assert flexed.year_one_noi == pytest.approx(noi[0] * 0.9)
    # And run_sensitivity_grid happily consumes the stacked-DS base input.
    sgrid = run_sensitivity_grid(base_in, target_irr=0.15)
    assert len(sgrid.cells) == 25
