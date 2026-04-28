"""Critic agent — deterministic + grounding-validator tests.

These tests are intentionally LLM-free. The Critic's deterministic
cross-field checks fire from pure-Python invariants over the typed
financials, and the grounding validator is a set membership test —
neither needs to call Anthropic.

The single LLM-style test runs the agent end-to-end with
``run_narrative_pass=False`` so CI can exercise the wiring without an
API key.
"""

from __future__ import annotations

import os

import pytest

# Force the SQLite dev DSN before app modules import — same pattern as
# test_smoke.py — so settings don't bleed in from the developer shell.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")


# ─────────────────────── helpers ───────────────────────


def _financials(
    *,
    period_label: str,
    rooms_revenue: float,
    fb_revenue: float = 0.0,
    other_revenue: float = 0.0,
    insurance: float = 0.0,
    property_taxes: float = 0.0,
    fb_dept: float = 0.0,
    rooms_dept: float = 0.0,
    a_and_g: float = 0.0,
    mgmt_fee: float = 0.0,
    ffe_reserve: float = 0.0,
    occupancy: float | None = None,
    adr: float | None = None,
    revpar: float | None = None,
    noi: float | None = None,
):
    from fondok_schemas import (
        DepartmentalExpenses,
        FixedCharges,
        USALIFinancials,
        UndistributedExpenses,
    )

    total_revenue = rooms_revenue + fb_revenue + other_revenue
    dept_total = rooms_dept + fb_dept
    undist_total = a_and_g
    fixed_total = insurance + property_taxes
    gop = total_revenue - dept_total - undist_total
    if noi is None:
        noi = gop - mgmt_fee - ffe_reserve - fixed_total
    opex_ratio = (
        (total_revenue - noi) / total_revenue if total_revenue > 0 else 0.0
    )
    opex_ratio = max(0.0, min(2.0, opex_ratio))
    return USALIFinancials(
        period_label=period_label,
        rooms_revenue=rooms_revenue,
        fb_revenue=fb_revenue,
        other_revenue=other_revenue,
        total_revenue=total_revenue,
        dept_expenses=DepartmentalExpenses(
            rooms=rooms_dept,
            food_beverage=fb_dept,
            other_operated=0.0,
            total=dept_total,
        ),
        undistributed=UndistributedExpenses(
            administrative_general=a_and_g,
            total=undist_total,
        ),
        mgmt_fee=mgmt_fee,
        ffe_reserve=ffe_reserve,
        fixed_charges=FixedCharges(
            property_taxes=property_taxes,
            insurance=insurance,
            total=fixed_total,
        ),
        gop=gop,
        noi=noi,
        opex_ratio=opex_ratio,
        occupancy=occupancy,
        adr=adr,
        revpar=revpar,
    )


# ─────────────────────── deterministic checks ───────────────────────


@pytest.mark.asyncio
async def test_deterministic_cross_field_revpar_inconsistency() -> None:
    """ADR x Occupancy != RevPAR within 1% → MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY."""
    from app.agents.critic import CriticInput, run_critic
    from fondok_schemas import Severity

    # ADR $300 x Occ 0.75 = $225. Broker reports RevPAR $260 — a 13.5% gap.
    broker = _financials(
        period_label="Broker Proforma Year 1",
        rooms_revenue=10_000_000,
        adr=300.0,
        occupancy=0.75,
        revpar=260.0,
    )
    out = await run_critic(
        CriticInput(
            tenant_id="t1",
            deal_id="11111111-1111-1111-1111-111111111111",
            broker_proforma=broker,
        ),
        run_narrative_pass=False,
    )
    assert out.success
    assert out.report is not None
    rule_ids = [f.rule_id for f in out.report.findings]
    assert "MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY" in rule_ids
    flag = next(
        f
        for f in out.report.findings
        if f.rule_id == "MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY"
    )
    assert flag.severity is Severity.CRITICAL
    assert "revpar" in flag.cited_fields
    assert "adr" in flag.cited_fields
    assert "occupancy" in flag.cited_fields


@pytest.mark.asyncio
async def test_deterministic_florida_insurance_held_flat() -> None:
    """Miami coastal property w/ insurance flat YoY → MULTI_FIELD_INSURANCE_COASTAL_RISK."""
    from app.agents.critic import CriticInput, run_critic
    from fondok_schemas import Severity

    # T-12 actual insurance: $500K. Broker proforma: $510K (+2%, far below 30%).
    actuals = _financials(
        period_label="T-12 Actual",
        rooms_revenue=15_000_000,
        insurance=500_000,
        occupancy=0.78,
        adr=350.0,
        revpar=273.0,
    )
    broker = _financials(
        period_label="Broker Proforma Year 1",
        rooms_revenue=15_500_000,
        insurance=510_000,
        occupancy=0.78,
        adr=350.0,
        revpar=273.0,
    )
    out = await run_critic(
        CriticInput(
            tenant_id="t1",
            deal_id="22222222-2222-2222-2222-222222222222",
            t12_actual=actuals,
            broker_proforma=broker,
            market_context={
                "city": "Miami Beach, FL",
                "location": "Miami Beach, FL",
                "service": "Lifestyle",
            },
            keys=132,
        ),
        run_narrative_pass=False,
    )
    assert out.success
    assert out.report is not None
    rule_ids = [f.rule_id for f in out.report.findings]
    assert "MULTI_FIELD_INSURANCE_COASTAL_RISK" in rule_ids
    flag = next(
        f
        for f in out.report.findings
        if f.rule_id == "MULTI_FIELD_INSURANCE_COASTAL_RISK"
    )
    assert flag.severity is Severity.CRITICAL
    assert "insurance" in flag.cited_fields


@pytest.mark.asyncio
async def test_grounding_validator_rejects_unknown_rule_ids() -> None:
    """validate_grounding drops findings citing rule_ids not in the catalog."""
    from uuid import UUID, uuid4

    from app.agents.critic import validate_grounding
    from fondok_schemas import CriticFinding, Severity

    deal_id = UUID("33333333-3333-3333-3333-333333333333")
    findings = [
        CriticFinding(
            id=uuid4(),
            deal_id=deal_id,
            rule_id="MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY",
            title="Real rule",
            narrative="Body 1",
            severity=Severity.CRITICAL,
        ),
        CriticFinding(
            id=uuid4(),
            deal_id=deal_id,
            rule_id="MADE_UP",
            title="Hallucinated rule",
            narrative="Body 2",
            severity=Severity.WARN,
        ),
    ]
    known = {"MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY", "INSURANCE_PER_KEY"}
    grounded, rejected = validate_grounding(findings, known)
    assert len(grounded) == 1
    assert grounded[0].rule_id == "MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY"
    assert rejected == 1


@pytest.mark.asyncio
async def test_critic_with_evals_mock() -> None:
    """Full pipeline w/ ``EVALS_MOCK=true`` should still emit deterministic findings."""
    os.environ["EVALS_MOCK"] = "true"
    try:
        from app.agents.critic import CriticInput, run_critic

        # Combine TWO triggers so we know the deterministic pass really
        # runs even when the LLM pass is suppressed.
        actuals = _financials(
            period_label="T-12 Actual",
            rooms_revenue=12_000_000,
            insurance=400_000,
            adr=340.0,
            occupancy=0.76,
            revpar=258.4,
            noi=3_500_000,
        )
        broker = _financials(
            period_label="Broker Proforma Year 1",
            rooms_revenue=12_500_000,
            insurance=410_000,  # only +2.5% in a coastal market
            adr=340.0,
            occupancy=0.76,
            # Intentionally inconsistent RevPAR to fire the math rule.
            revpar=300.0,
            noi=3_600_000,
        )
        out = await run_critic(
            CriticInput(
                tenant_id="t1",
                deal_id="44444444-4444-4444-4444-444444444444",
                t12_actual=actuals,
                broker_proforma=broker,
                market_context={
                    "city": "Tampa, FL",
                    "location": "Tampa, FL coastal",
                    "service": "Select Service",
                },
                keys=200,
            ),
            run_narrative_pass=False,
        )
        assert out.success
        assert out.report is not None
        assert len(out.report.findings) >= 1
        # All findings must ground in a known rule_id (validator already
        # ran inside run_critic, so this is belt-and-suspenders).
        rule_ids = {f.rule_id for f in out.report.findings}
        assert rule_ids.issubset(
            {
                "MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY",
                "MULTI_FIELD_NOI_GROWTH_WITHOUT_OPEX_PRESSURE",
                "MULTI_FIELD_INSURANCE_COASTAL_RISK",
                "MULTI_FIELD_LABOR_INFLATION_MISSING",
                "MULTI_FIELD_SEASONAL_PATTERN_MISSING",
                "MULTI_FIELD_FNB_MARGIN_AGGRESSIVE",
                "MULTI_FIELD_PIP_TIMING_INCONSISTENT",
                "MULTI_FIELD_DEBT_YIELD_VS_DSCR_DIVERGENCE",
                "MULTI_FIELD_REVENUE_GROWTH_WITHOUT_DEMAND_DRIVER",
            }
        )
    finally:
        os.environ.pop("EVALS_MOCK", None)


@pytest.mark.asyncio
async def test_critic_empty_inputs_returns_empty_report() -> None:
    """No financials at all → success with an empty report."""
    from app.agents.critic import CriticInput, run_critic

    out = await run_critic(
        CriticInput(
            tenant_id="t1",
            deal_id="55555555-5555-5555-5555-555555555555",
        ),
        run_narrative_pass=False,
    )
    assert out.success
    assert out.report is not None
    assert out.report.findings == []


@pytest.mark.asyncio
async def test_deterministic_fnb_margin_aggressive_select_service() -> None:
    """Select-service hotel w/ F&B margin > 25% → MULTI_FIELD_FNB_MARGIN_AGGRESSIVE."""
    from app.agents.critic import CriticInput, run_critic

    # F&B revenue $400K, F&B departmental cost $200K → 50% margin.
    broker = _financials(
        period_label="Broker Proforma Year 1",
        rooms_revenue=8_000_000,
        fb_revenue=400_000,
        fb_dept=200_000,
        adr=180.0,
        occupancy=0.72,
        revpar=129.6,
    )
    out = await run_critic(
        CriticInput(
            tenant_id="t1",
            deal_id="66666666-6666-6666-6666-666666666666",
            broker_proforma=broker,
            market_context={"service": "Select Service", "city": "Plano, TX"},
        ),
        run_narrative_pass=False,
    )
    assert out.success
    assert out.report is not None
    rule_ids = [f.rule_id for f in out.report.findings]
    assert "MULTI_FIELD_FNB_MARGIN_AGGRESSIVE" in rule_ids
