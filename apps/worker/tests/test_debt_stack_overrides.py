"""FON-63 — multi-tranche debt stack + editable-tranche override wiring.

The debt engine now seeds a deal-agnostic institutional stack (the deal's own
senior loan + a PACE placeholder) and lets an analyst edit tranches from the
Debt tab via indexed overrides (``debt_stack.tranches.<idx>.<field>``). These
tests pin the two invariants that matter most:

  * a deal with NO debt overrides is byte-for-byte the legacy single-senior
    model (PACE placeholder excluded), so nothing regresses; and
  * edits + an activated PACE tranche flow through to the headline metrics the
    Returns engine consumes.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.debt import (
    DebtEngine,
    DebtEngineInputExt,
    _apply_tranche_overrides,
    _build_default_tranches,
)


def _input(**overrides) -> DebtEngineInputExt:
    base = dict(
        deal_id=uuid4(),
        loan_amount=23_700_000,
        ltv=0.65,
        interest_rate=0.068,
        term_years=5,
        amortization_years=30,
        interest_only_years=0,
        noi_by_year=[1_300_000, 1_400_000, 1_500_000, 1_600_000, 1_700_000],
        purchase_price_usd=36_400_000,
        total_capital_usd=44_300_000,
    )
    base.update(overrides)
    return DebtEngineInputExt(**base)


def test_default_stack_is_senior_only_and_matches_legacy() -> None:
    out = DebtEngine().run(_input())
    # PACE placeholder ($0 / pending) is excluded — leverage unchanged.
    assert len(out.debt_stack.tranches) == 1
    assert out.debt_stack.tranches[0].kind == "senior"
    assert out.loan_amount == pytest.approx(23_700_000)
    assert out.debt_stack.total_debt == pytest.approx(23_700_000)
    # Headline DS comes from the monthly senior schedule (no drift).
    assert out.annual_debt_service == pytest.approx(out.schedule[0].debt_service)


def test_default_seed_has_senior_and_pending_pace() -> None:
    tranches = _build_default_tranches(_input())
    assert [t.kind for t in tranches] == ["senior", "pace"]
    assert tranches[1].terms_pending is True
    assert tranches[1].loan_amount == 0.0


def test_senior_rate_override_raises_debt_service() -> None:
    base = DebtEngine().run(_input())
    hi = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {0: {"rate_pct": 0.075}}})
    )
    assert hi.annual_debt_service > base.annual_debt_service
    assert hi.year_one_dscr < base.year_one_dscr


def test_activated_pace_adds_debt_and_service() -> None:
    base = DebtEngine().run(_input())
    p = DebtEngine().run(
        _input(
            debt_stack_overrides={
                "tranches": {1: {"principal_usd": 5_000_000, "rate_pct": 0.06}}
            }
        )
    )
    assert len(p.debt_stack.tranches) == 2
    assert p.debt_stack.total_debt == pytest.approx(28_700_000)
    # Senior DS unchanged + PACE interest ($5M x 6% = $300k).
    assert p.annual_debt_service == pytest.approx(
        base.annual_debt_service + 300_000, rel=1e-3
    )


def test_unpriced_pace_stays_pending_but_counts_in_leverage() -> None:
    base = DebtEngine().run(_input())
    p = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {1: {"principal_usd": 5_000_000}}})
    )
    pace = next(t for t in p.debt_stack.tranches if t.kind == "pace")
    # No invented rate — excluded from debt service...
    assert pace.terms_pending is True
    assert pace.all_in_rate is None
    assert p.annual_debt_service == pytest.approx(base.annual_debt_service)
    # ...but still counts toward total debt / LTV / debt yield.
    assert p.debt_stack.total_debt == pytest.approx(28_700_000)


def test_string_keyed_override_indexes_apply() -> None:
    # JSONB round-trips can key the tranche index as a string.
    p = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {"0": {"rate_pct": 0.09}}})
    )
    assert p.debt_stack.tranches[0].all_in_rate == pytest.approx(0.09)


def test_apply_overrides_is_noop_without_overrides() -> None:
    seed = _build_default_tranches(_input())
    assert _apply_tranche_overrides(seed, None) is seed
    assert _apply_tranche_overrides(seed, {}) is seed
    assert _apply_tranche_overrides(seed, {"tranches": {}}) is seed


def test_amortization_override_switches_off_interest_only() -> None:
    resolved = _apply_tranche_overrides(
        _build_default_tranches(_input(amortization_years=0)),  # IO senior
        {"tranches": {0: {"amortization_months": 300}}},
    )
    assert resolved[0].interest_only is False
    assert resolved[0].amortization_years == 25


def test_zero_amortization_override_means_interest_only() -> None:
    # A single Amort control: 0 years -> interest-only.
    resolved = _apply_tranche_overrides(
        _build_default_tranches(_input(amortization_years=30)),  # amortizing senior
        {"tranches": {0: {"amortization_months": 0}}},
    )
    assert resolved[0].interest_only is True
    assert resolved[0].amortization_years is None


def test_zero_amortization_lowers_debt_service() -> None:
    # Switching the senior to IO reduces its debt service (no principal).
    amort = DebtEngine().run(_input())  # default = amortizing 30yr
    io = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {0: {"amortization_months": 0}}})
    )
    assert io.annual_debt_service < amort.annual_debt_service


# ─────────────── FON-63 Wave 2 — Loan Terms assumptions workspace ───────────


def test_io_stub_override_delays_principal_on_amortizing_senior() -> None:
    """``io_period_months`` is an interest-only STUB before principal starts
    (not a switch to full IO): months 1..N pay interest only, month N+1 starts
    amortizing, and Year-1 debt service drops accordingly."""
    base = DebtEngine().run(_input())
    stub = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {0: {"io_period_months": 24}}})
    )
    assert all(m.principal == 0.0 for m in stub.monthly_schedule[:24])
    assert stub.monthly_schedule[24].principal > 0.0
    assert stub.schedule[0].debt_service < base.schedule[0].debt_service
    assert stub.interest_only_months == 24
    assert stub.amortization_years == 30  # still amortizing after the stub
    # Year 3 onwards is level P&I again — the stub never made the loan IO.
    assert stub.schedule[2].principal > 0.0
    assert stub.debt_stack.tranches[0].io_months == 24


def test_floating_senior_prices_off_spread_plus_index_with_floor_and_cap() -> None:
    """A Floating pick + spread prices the senior at index + spread; the index
    is the analyst's assumption when entered, clamped to floor / cap."""
    ov = {"tranches": {0: {"rate_type": "floating", "spread_pct": 0.03, "index_rate_pct": 0.04}}}
    out = DebtEngine().run(_input(debt_stack_overrides=ov))
    sr = out.debt_stack.tranches[0]
    assert sr.rate_type == "floating"
    assert sr.all_in_rate == pytest.approx(0.07)
    assert out.interest_rate == pytest.approx(0.07)  # the schedule ran on it
    assert sr.benchmark_rate == pytest.approx(0.04)
    assert sr.benchmark_is_default is False
    assert sr.spread == pytest.approx(0.03)
    assert sr.terms_pending is False
    # Monthly interest in month 1 reflects the floating all-in rate.
    assert out.monthly_schedule[0].interest == pytest.approx(23_700_000 * 0.07 / 12)

    ov["tranches"][0]["rate_floor_pct"] = 0.05
    floored = DebtEngine().run(_input(debt_stack_overrides=ov))
    assert floored.debt_stack.tranches[0].all_in_rate == pytest.approx(0.08)
    assert floored.debt_stack.tranches[0].rate_floor == pytest.approx(0.05)

    ov["tranches"][0]["rate_floor_pct"] = 0
    ov["tranches"][0]["rate_cap_pct"] = 0.035
    capped = DebtEngine().run(_input(debt_stack_overrides=ov))
    assert capped.debt_stack.tranches[0].all_in_rate == pytest.approx(0.065)
    assert capped.debt_stack.tranches[0].rate_floor is None


def test_floating_without_index_flags_the_default_benchmark() -> None:
    """No index assumption entered → the engine's flat default index is used
    and the tranche SAYS so (``benchmark_is_default``) so the tab can present
    it as an input to provide rather than an entered term."""
    from app.engines.debt import _SOFR_DEFAULT

    out = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {0: {"rate_type": "floating", "spread_pct": 0.03}}})
    )
    sr = out.debt_stack.tranches[0]
    assert sr.benchmark_is_default is True
    assert sr.benchmark_rate == pytest.approx(_SOFR_DEFAULT)
    assert sr.all_in_rate == pytest.approx(_SOFR_DEFAULT + 0.03)


def test_explicit_rate_type_wins_over_stale_fixed_rate_override() -> None:
    """A Floating pick with a leftover ``rate_pct`` override still floats."""
    out = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {0: {
            "rate_pct": 0.09, "rate_type": "floating", "spread_pct": 0.02, "index_rate_pct": 0.04,
        }}})
    )
    assert out.debt_stack.tranches[0].rate_type == "floating"
    assert out.debt_stack.tranches[0].all_in_rate == pytest.approx(0.06)
    fixed = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {0: {"rate_pct": 0.09, "rate_type": "fixed", "spread_pct": 0.02}}})
    )
    assert fixed.debt_stack.tranches[0].rate_type == "fixed"
    assert fixed.debt_stack.tranches[0].all_in_rate == pytest.approx(0.09)
    assert fixed.debt_stack.tranches[0].spread is None  # not a floating build-up


def test_floating_senior_without_spread_is_pending_and_schedule_falls_back() -> None:
    """Floating with no spread has no resolvable rate: the stack marks the
    senior pending (no invented spread) and the schedule keeps pricing at the
    fixed rate on file — which the output echoes so the tab can say so."""
    out = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {0: {"rate_type": "floating"}}})
    )
    sr = out.debt_stack.tranches[0]
    assert sr.terms_pending is True and sr.all_in_rate is None
    assert out.interest_rate == pytest.approx(0.068)
    assert any("terms not specified" in w.lower() for w in out.debt_stack.warnings)


def test_pace_funded_with_rate_and_amortization_terms_echo() -> None:
    """A priced PACE tranche echoes every term the Debt tab renders/edits."""
    out = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {1: {
            "principal_usd": 6_000_000, "rate_pct": 0.065,
            "amortization_months": 300, "io_period_months": 12,
        }}})
    )
    pace = next(t for t in out.debt_stack.tranches if t.kind == "pace")
    assert pace.terms_pending is False
    assert pace.amortization_years == 25
    assert pace.io_months == 12
    # A 12-month IO stub → Year-1 debt service is interest only.
    assert pace.annual_debt_service == pytest.approx(6_000_000 * 0.065)
    assert out.loan_amount == pytest.approx(23_700_000 + 6_000_000)
    assert out.annual_debt_service == pytest.approx(
        out.schedule[0].debt_service + 6_000_000 * 0.065
    )


def test_runner_allowlists_loan_terms_and_covenant_keys() -> None:
    """The runner routes every Debt-tab key the engine consumes — the Loan
    Terms build-up per tranche and the stack-level covenant thresholds — and
    the parser lands covenant keys as stack-level scalars where
    ``_covenant_thresholds`` reads them."""
    from app.engines.debt import _TRANCHE_OVERRIDE_FIELDS
    from app.services.engine_runner import (
        _DEBT_STACK_TRANCHE_FIELDS,
        _OVERRIDE_DEBT_KEYS,
        _parse_debt_stack_override_path,
    )

    assert set(_DEBT_STACK_TRANCHE_FIELDS) == set(_TRANCHE_OVERRIDE_FIELDS)
    for idx in (0, 1):
        for field in ("rate_type", "spread_pct", "index_rate_pct", "rate_floor_pct",
                      "rate_cap_pct", "io_period_months", "amortization_months",
                      "principal_usd", "rate_pct"):
            assert f"debt_stack.tranches.{idx}.{field}" in _OVERRIDE_DEBT_KEYS
    for key in ("covenant_max_ltv", "covenant_max_ltc", "covenant_min_dscr", "covenant_min_debt_yield"):
        assert f"debt_stack.{key}" in _OVERRIDE_DEBT_KEYS
        assert _parse_debt_stack_override_path(f"debt_stack.{key}") == ("stack", None, key)
    assert _parse_debt_stack_override_path("debt_stack.tranches.0.rate_type") == ("tranche", 0, "rate_type")
    assert _parse_debt_stack_override_path("debt_stack.tranches.0.bogus") is None
