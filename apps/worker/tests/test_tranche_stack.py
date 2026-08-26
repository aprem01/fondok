"""FON-63 — multi-tranche debt stack, verified against Sam's QA debt template."""
from app.engines.tranche_stack import (
    LoanTranche, compute_debt_stack, kimpton_reference_stack,
)


def test_senior_floating_rate_resolves_with_floor_and_cap():
    t = LoanTranche(kind="senior", loan_amount=35e6, rate_type="floating",
                    spread=0.05, rate_floor=0.03, rate_cap=0.06, index_assumption=0.03)
    assert abs(t.effective_rate(0.0) - 0.08) < 1e-9          # 5% + floor 3%
    # Index above the cap gets clamped: 5% + 6% = 11%.
    t2 = t.model_copy(update={"index_assumption": 0.075})
    assert abs(t2.effective_rate(0.0) - 0.11) < 1e-9
    # Index below the floor gets floored: 5% + 3% = 8%.
    t3 = t.model_copy(update={"index_assumption": 0.01})
    assert abs(t3.effective_rate(0.0) - 0.08) < 1e-9


def test_pending_tranche_has_no_rate_or_debt_service():
    p = LoanTranche(kind="pace", loan_amount=30e6, terms_pending=True)
    assert p.effective_rate(0.03) is None


def test_amortizing_pmt_path():
    # $10M, 6% fixed, amortizing over 30y, not IO.
    t = LoanTranche(kind="senior", loan_amount=10e6, rate_type="fixed",
                    fixed_rate=0.06, interest_only=False, amortization_years=30)
    r = compute_debt_stack([t], year_one_noi=1e6, property_value=15e6,
                           total_cost=15e6, default_index=0.0)
    ds = r.tranches[0].annual_debt_service
    # Level annual P&I on $10M @6%/30y ≈ $726,489.
    assert 720_000 < ds < 733_000, ds


def test_kimpton_reference_stack():
    tranches, cov = kimpton_reference_stack()
    r = compute_debt_stack(
        tranches, year_one_noi=5_133_454, property_value=36_400_000,
        total_cost=44_318_900, default_index=0.03, covenants=cov,
    )
    # Senior: floating 8% IO on $35M → $2.8M debt service.
    senior = next(t for t in r.tranches if t.kind == "senior")
    assert abs(senior.all_in_rate - 0.08) < 1e-9
    assert abs(senior.annual_debt_service - 2_800_000) < 1.0
    # PACE: pending → no rate, no debt service, but counts toward total debt.
    pace = next(t for t in r.tranches if t.kind == "pace")
    assert pace.all_in_rate is None and pace.annual_debt_service is None
    assert pace.terms_pending

    assert abs(r.total_debt - 65_000_000) < 1.0
    assert abs(r.priced_debt - 35_000_000) < 1.0
    assert abs(r.total_annual_debt_service - 2_800_000) < 1.0
    assert abs(r.year_one_dscr - (5_133_454 / 2_800_000)) < 1e-6   # ≈1.83x
    assert abs(r.ltv - (65_000_000 / 36_400_000)) < 1e-6           # ≈178%
    assert abs(r.debt_yield - (5_133_454 / 65_000_000)) < 1e-6     # ≈7.9%
    assert abs(r.ltc - (65_000_000 / 44_318_900)) < 1e-6          # ≈147%
    # Honest warnings: PACE pending + LTC>100% + LTV breaches 65% covenant.
    joined = " ".join(r.warnings).lower()
    assert "terms not specified" in joined
    assert "ltc" in joined
    assert "covenant" in joined
    print("\nKimpton stack:",
          f"total_debt=${r.total_debt/1e6:.0f}M dscr={r.year_one_dscr:.2f}x "
          f"ltv={r.ltv*100:.0f}% debt_yield={r.debt_yield*100:.1f}% ltc={r.ltc*100:.0f}%")
    for w in r.warnings:
        print("  warn:", w)
