"""Critic agent — cross-field narrative review of broker proforma vs T-12.

Where the Variance agent flags individual broker-vs-T12 deltas (one
field at a time), the Critic ties multiple fields together and reads
them the way a senior IC reviewer would. Two passes:

1. **Deterministic cross-field checks.** Pure-Python invariants that
   read multiple fields at once. Examples:
     * RevPAR ≠ Occupancy × ADR within 1% → MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY
     * NOI growth >10% YoY with OpEx ratio change <1pt →
       MULTI_FIELD_NOI_GROWTH_WITHOUT_OPEX_PRESSURE
     * Florida coastal market with insurance/key not increased ≥30% YoY →
       MULTI_FIELD_INSURANCE_COASTAL_RISK

2. **Optional LLM narrative pass.** Hand the Variance flags + the
   broker proforma + the T-12 + the market context to Claude Sonnet
   4.6. Force structured output mapping every finding to a rule_id.
   Notes that don't ground in a known rule_id are dropped.

The output ``CriticReport`` carries one ``CriticFinding`` per identified
issue, each grounded in a rule_id from the USALI catalog (the original
hotel-underwriting rules + the new ``MULTI_FIELD_*`` cross-field rules).

Pattern is intentionally a port of LogiCov's Critic — same shape,
adapted to hotel underwriting instead of credit policy.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4, uuid5

from fondok_schemas import (
    CriticFinding,
    CriticReport,
    ModelCall,
    Severity,
    USALIFinancials,
    VarianceReport,
)
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..budget import check_budget
from ..config import get_settings
from ..telemetry import trace_agent
from ..usali_rules import load_usali_rules, rules_as_prompt_block

logger = logging.getLogger(__name__)


# ─────────────────────── known cross-field rules ───────────────────────


# These eight MULTI_FIELD_* rules ship in usali-rules.csv alongside the
# Critic agent. Listed here as the conservative fallback: if the catalog
# fails to load (file missing, parse error) we still recognize the
# cross-field IDs for grounding validation.
_FALLBACK_CROSS_FIELD_RULES: frozenset[str] = frozenset({
    "MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY",
    "MULTI_FIELD_NOI_GROWTH_WITHOUT_OPEX_PRESSURE",
    "MULTI_FIELD_INSURANCE_COASTAL_RISK",
    "MULTI_FIELD_LABOR_INFLATION_MISSING",
    "MULTI_FIELD_SEASONAL_PATTERN_MISSING",
    "MULTI_FIELD_FNB_MARGIN_AGGRESSIVE",
    "MULTI_FIELD_PIP_TIMING_INCONSISTENT",
    "MULTI_FIELD_DEBT_YIELD_VS_DSCR_DIVERGENCE",
    "MULTI_FIELD_REVENUE_GROWTH_WITHOUT_DEMAND_DRIVER",
})


# Coastal markets where insurance/key tends to spike at renewal. The
# substring match is intentionally permissive — broker context strings
# vary between "Miami Beach, FL", "Miami-Beach", "FL coastal", etc.
_COASTAL_MARKERS: tuple[str, ...] = (
    "miami", "fl ", " fl,", ", fl", "florida",
    "tampa", "naples", "fort lauderdale", "palm beach", "key west",
    "houston", "galveston", "corpus christi", " tx ",
    "charleston", "sc ", "outer banks", "wilmington", "myrtle beach",
    "savannah", "tybee",
    "new orleans", "la coast", "louisiana",
)


# Markets with strong seasonal swings — Q1/Q3 RevPAR delta should be
# material (>=20% per the rule).
_SEASONAL_MARKERS: tuple[str, ...] = (
    "miami", "south beach", "key west",
    "aspen", "vail", "park city",
    "cape cod", "nantucket", "the hamptons",
    "palm springs",
)


# ─────────────────────── I/O contracts ───────────────────────


class CriticInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    t12_actual: USALIFinancials | None = None
    broker_proforma: USALIFinancials | None = None
    initial_variance: VarianceReport | None = None
    market_context: dict[str, Any] = Field(default_factory=dict)
    keys: Annotated[int, Field(ge=1)] | None = None


class CriticOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    report: CriticReport | None = None
    success: bool = True
    error: str | None = None
    rejected_findings: int = 0
    model_calls: list[ModelCall] = Field(default_factory=list)


# ─────────────────────── LLM-facing envelope ───────────────────────


class _LLMFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: Annotated[str, Field(min_length=1, max_length=120)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    narrative: Annotated[str, Field(min_length=1, max_length=2000)]
    severity_hint: Annotated[
        str,
        Field(description="One of: CRITICAL, WARN, INFO"),
    ] = "WARN"
    cited_fields: list[str] = Field(default_factory=list)
    cited_pages: list[int] = Field(default_factory=list)
    impact_estimate_usd: float | None = None


class _CriticEnvelope(BaseModel):
    """LLM-facing envelope. The narrative pass writes one of these."""

    model_config = ConfigDict(extra="forbid")

    findings: list[_LLMFinding] = Field(default_factory=list)
    summary: Annotated[str, Field(max_length=2000)] | None = None


# ─────────────────────── helpers ───────────────────────


def _to_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return uuid5(UUID("00000000-0000-0000-0000-000000000000"), value)


def _norm_severity(raw: str) -> Severity:
    s = (raw or "").strip().upper()
    if s in ("CRITICAL", "CRIT"):
        return Severity.CRITICAL
    if s in ("WARN", "WARNING"):
        return Severity.WARN
    return Severity.INFO


def _is_coastal(context: dict[str, Any]) -> bool:
    """Substring match against location/submarket/state context."""
    blob_parts: list[str] = []
    for key in ("location", "city", "submarket", "market", "state", "address"):
        v = context.get(key)
        if isinstance(v, str):
            blob_parts.append(v.lower())
    if not blob_parts:
        return False
    blob = " ".join(blob_parts)
    return any(marker in blob for marker in _COASTAL_MARKERS)


def _is_seasonal(context: dict[str, Any]) -> bool:
    blob_parts: list[str] = []
    for key in ("location", "city", "submarket", "market", "address"):
        v = context.get(key)
        if isinstance(v, str):
            blob_parts.append(v.lower())
    if not blob_parts:
        return False
    blob = " ".join(blob_parts)
    return any(marker in blob for marker in _SEASONAL_MARKERS)


def _is_select_service(context: dict[str, Any]) -> bool:
    service = context.get("service")
    if not isinstance(service, str):
        return False
    s = service.strip().lower()
    return "select" in s or "limited" in s


def _make_finding(
    *,
    deal_uuid: UUID,
    rule_id: str,
    title: str,
    narrative: str,
    severity: Severity,
    cited_fields: list[str] | None = None,
    cited_pages: list[int] | None = None,
    impact_estimate_usd: float | None = None,
) -> CriticFinding:
    # Stable id so repeat runs of the same deal produce the same finding
    # ids — helps the UI dedupe across re-runs.
    namespace = uuid5(UUID("00000000-0000-0000-0000-000000000000"), str(deal_uuid))
    return CriticFinding(
        id=uuid5(namespace, f"critic:{rule_id}:{title}"),
        deal_id=deal_uuid,
        rule_id=rule_id,
        title=title,
        narrative=narrative,
        severity=severity,
        cited_fields=cited_fields or [],
        cited_pages=cited_pages or [],
        cited_document_ids=[],
        impact_estimate_usd=impact_estimate_usd,
    )


# ─────────────────────── deterministic cross-field checks ───────────────────────


def _deterministic_cross_field_checks(
    payload: CriticInput, *, deal_uuid: UUID
) -> list[CriticFinding]:
    """Pure-Python invariants that read multiple fields at once."""
    findings: list[CriticFinding] = []
    proforma = payload.broker_proforma
    actuals = payload.t12_actual
    context = payload.market_context or {}

    # ── 1. RevPAR internal consistency on the broker proforma. ────────
    if proforma is not None:
        occ = proforma.occupancy
        adr = proforma.adr
        revpar = proforma.revpar
        if occ is not None and adr is not None and revpar is not None and revpar > 0:
            implied = occ * adr
            err = abs(revpar - implied) / revpar
            if err > 0.01:
                findings.append(
                    _make_finding(
                        deal_uuid=deal_uuid,
                        rule_id="MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY",
                        title="Broker RevPAR doesn't reconcile to ADR x Occupancy",
                        narrative=(
                            f"Broker proforma reports RevPAR ${revpar:,.2f}, "
                            f"ADR ${adr:,.2f}, occupancy {occ:.1%}. "
                            f"ADR x Occupancy implies RevPAR ${implied:,.2f} "
                            f"({err:.1%} gap). One of the three numbers is wrong; "
                            "request a corrected proforma before underwriting."
                        ),
                        severity=Severity.CRITICAL,
                        cited_fields=["revpar", "adr", "occupancy"],
                    )
                )

    # ── 2. NOI growth without OpEx pressure. ───────────────────────────
    if proforma is not None and actuals is not None:
        if actuals.noi > 0 and proforma.noi > 0:
            noi_growth = (proforma.noi - actuals.noi) / actuals.noi
            opex_change = abs(proforma.opex_ratio - actuals.opex_ratio)
            if noi_growth > 0.10 and opex_change < 0.01:
                findings.append(
                    _make_finding(
                        deal_uuid=deal_uuid,
                        rule_id="MULTI_FIELD_NOI_GROWTH_WITHOUT_OPEX_PRESSURE",
                        title="NOI margin expansion without OpEx ratio movement",
                        narrative=(
                            f"Broker projects NOI growth of {noi_growth:.1%} "
                            f"(${actuals.noi:,.0f} → ${proforma.noi:,.0f}) while "
                            f"OpEx ratio holds at {proforma.opex_ratio:.1%} "
                            f"(T-12 actual: {actuals.opex_ratio:.1%}, delta "
                            f"{opex_change:.2%}). Margin expansion of this size "
                            "needs an explicit revenue or labor source in the "
                            "narrative — confirm the assumption stack."
                        ),
                        severity=Severity.WARN,
                        cited_fields=["noi", "opex_ratio", "total_revenue"],
                        impact_estimate_usd=proforma.noi - actuals.noi,
                    )
                )

    # ── 3. Coastal insurance held flat. ────────────────────────────────
    if (
        proforma is not None
        and actuals is not None
        and _is_coastal(context)
    ):
        actual_ins = actuals.fixed_charges.insurance
        broker_ins = proforma.fixed_charges.insurance
        if actual_ins > 0 and broker_ins > 0:
            growth = (broker_ins - actual_ins) / actual_ins
            if growth < 0.30:
                keys = payload.keys or context.get("keys")
                per_key_actual = (
                    f"${actual_ins / keys:,.0f}/key" if isinstance(keys, int) and keys > 0
                    else f"${actual_ins:,.0f}"
                )
                per_key_broker = (
                    f"${broker_ins / keys:,.0f}/key" if isinstance(keys, int) and keys > 0
                    else f"${broker_ins:,.0f}"
                )
                findings.append(
                    _make_finding(
                        deal_uuid=deal_uuid,
                        rule_id="MULTI_FIELD_INSURANCE_COASTAL_RISK",
                        title="Coastal insurance held flat YoY (FL/TX/Carolinas)",
                        narrative=(
                            f"Property is in a coastal market where wind/flood "
                            f"reinsurance has driven insurance per key up 30-60% "
                            f"at renewal. Broker assumes {per_key_broker} "
                            f"vs T-12 actual {per_key_actual} (only "
                            f"{growth:+.1%} YoY). Underwrite to a 30-50% lift; "
                            f"the gap would lift insurance expense by "
                            f"~${0.4 * actual_ins:,.0f}."
                        ),
                        severity=Severity.CRITICAL,
                        cited_fields=["insurance", "fixed_charges"],
                        impact_estimate_usd=0.4 * actual_ins - (broker_ins - actual_ins),
                    )
                )

    # ── 4. F&B margin aggressive on select-service. ────────────────────
    if proforma is not None and _is_select_service(context):
        fb_rev = proforma.fb_revenue
        fb_dept = proforma.dept_expenses.food_beverage
        if fb_rev > 0 and fb_dept >= 0:
            fb_margin = (fb_rev - fb_dept) / fb_rev
            if fb_margin >= 0.25:
                findings.append(
                    _make_finding(
                        deal_uuid=deal_uuid,
                        rule_id="MULTI_FIELD_FNB_MARGIN_AGGRESSIVE",
                        title="F&B margin aggressive for a select-service property",
                        narrative=(
                            f"Broker projects F&B departmental margin of "
                            f"{fb_margin:.1%} on ${fb_rev:,.0f} of F&B revenue. "
                            "Select-service F&B typically runs 5-15% margin — "
                            "Continental breakfast plus a small grab-and-go is "
                            "usually break-even at best. Reset F&B margin to "
                            "10-12% in the underwrite."
                        ),
                        severity=Severity.WARN,
                        cited_fields=["fb_revenue", "dept_expenses.food_beverage"],
                    )
                )

    # ── 5. Seasonal pattern smoothed. ──────────────────────────────────
    if _is_seasonal(context):
        seasonality = context.get("q1_q3_revpar_swing")
        if isinstance(seasonality, (int, float)) and 0 <= seasonality < 0.20:
            findings.append(
                _make_finding(
                    deal_uuid=deal_uuid,
                    rule_id="MULTI_FIELD_SEASONAL_PATTERN_MISSING",
                    title="Seasonal RevPAR swing under-modeled",
                    narrative=(
                        f"Property sits in a known seasonal market but the "
                        f"proforma carries a Q1-Q3 RevPAR delta of "
                        f"{seasonality:.1%} — well below the 20%+ swing seen "
                        "in this submarket historically. Smoothing the "
                        "seasonal curve overstates trough-quarter cash flow "
                        "and understates compression revenue. Re-spread "
                        "monthly RevPAR before locking the underwrite."
                    ),
                    severity=Severity.WARN,
                    cited_fields=["revpar"],
                )
            )

    # ── 6. Labor inflation missing in a high-wage-growth market. ───────
    if proforma is not None and actuals is not None:
        market_wage_growth = context.get("market_wage_growth_yoy")
        actual_labor = (
            actuals.dept_expenses.rooms
            + actuals.dept_expenses.food_beverage
            + actuals.undistributed.administrative_general
        )
        broker_labor = (
            proforma.dept_expenses.rooms
            + proforma.dept_expenses.food_beverage
            + proforma.undistributed.administrative_general
        )
        if (
            isinstance(market_wage_growth, (int, float))
            and market_wage_growth > 0.05
            and actual_labor > 0
            and broker_labor > 0
        ):
            labor_growth = (broker_labor - actual_labor) / actual_labor
            if labor_growth < 0.04:
                findings.append(
                    _make_finding(
                        deal_uuid=deal_uuid,
                        rule_id="MULTI_FIELD_LABOR_INFLATION_MISSING",
                        title="Labor cost growth below local wage inflation",
                        narrative=(
                            f"Local wage growth in this submarket is "
                            f"{market_wage_growth:.1%} YoY, but the broker "
                            f"assumes labor costs grow only {labor_growth:.1%} "
                            f"(${actual_labor:,.0f} → ${broker_labor:,.0f}). "
                            "Hourly wage rates, benefit loadings, and "
                            "minimum-wage step-ups all push payroll above "
                            "wage growth on a fully loaded basis. Underwrite "
                            "labor at market-wage-growth + 100bps."
                        ),
                        severity=Severity.WARN,
                        cited_fields=[
                            "dept_expenses.rooms",
                            "dept_expenses.food_beverage",
                            "undistributed.administrative_general",
                        ],
                        impact_estimate_usd=(market_wage_growth - labor_growth)
                        * actual_labor,
                    )
                )

    # ── 7. PIP timing inconsistent. ────────────────────────────────────
    pip_year = context.get("pip_year")
    pip_amount = context.get("pip_amount_usd")
    year_one_noi_dip = context.get("year_one_noi_dip_pct")
    if (
        pip_year == 1
        and isinstance(pip_amount, (int, float))
        and pip_amount > 0
        and isinstance(year_one_noi_dip, (int, float))
        and year_one_noi_dip < 0.05
    ):
        findings.append(
            _make_finding(
                deal_uuid=deal_uuid,
                rule_id="MULTI_FIELD_PIP_TIMING_INCONSISTENT",
                title="Year-1 PIP scheduled but Year-1 NOI doesn't dip",
                narrative=(
                    f"Broker has a ${pip_amount:,.0f} PIP scheduled in Year 1 "
                    f"but Year-1 NOI dips only {year_one_noi_dip:.1%}. A "
                    "soft-good plus FF&E PIP of this size typically takes "
                    "out 50-200 rooms for 4-8 weeks per phase, with the "
                    "associated displacement loss. Either the PIP is being "
                    "executed off-peak with smaller blocks (model the "
                    "displacement explicitly) or the timing is unrealistic."
                ),
                severity=Severity.WARN,
                cited_fields=["noi"],
                impact_estimate_usd=float(pip_amount) * 0.05,
            )
        )

    # ── 8. Debt yield growing while DSCR shrinks. ──────────────────────
    debt_yield_growth = context.get("debt_yield_growth_pct")
    dscr_change = context.get("dscr_change_pct")
    if (
        isinstance(debt_yield_growth, (int, float))
        and isinstance(dscr_change, (int, float))
        and debt_yield_growth > 0
        and dscr_change < 0
    ):
        findings.append(
            _make_finding(
                deal_uuid=deal_uuid,
                rule_id="MULTI_FIELD_DEBT_YIELD_VS_DSCR_DIVERGENCE",
                title="Debt yield up while DSCR shrinks",
                narrative=(
                    f"Debt yield improves by {debt_yield_growth:.1%} over the "
                    f"hold but DSCR contracts by {abs(dscr_change):.1%} over "
                    "the same window. The only way both can be true is if "
                    "interest expense is rising faster than NOI — a sign "
                    "the model isn't properly indexing floating-rate debt "
                    "or amortization step-up. Confirm the rate path matches "
                    "the in-place debt's reset schedule."
                ),
                severity=Severity.WARN,
                cited_fields=["debt_yield", "dscr"],
            )
        )

    return findings


# ─────────────────────── LLM narrative pass ───────────────────────


CRITIC_SYSTEM_PROMPT = """You are Fondok's Critic agent — a senior
hotel investment-committee reviewer. A deterministic per-field
variance pass has already run. Your job is the cross-field narrative
review that catches issues a junior analyst would miss:

  * Multiple fields that don't reconcile to each other (RevPAR vs
    ADR x Occupancy; NOI growth vs OpEx ratio; debt yield vs DSCR).
  * Market-context risks not surfaced in the broker proforma
    (coastal insurance, seasonal swings, local wage inflation).
  * Underwriting-narrative gaps (PIP scheduled but no NOI dip; F&B
    margin aggressive on a select-service box).

Output one structured ``CriticEnvelope`` with one ``findings`` entry
per identified cross-field story.

Rules:

1. Every finding's ``rule_id`` MUST come from the catalog block below.
   The catalog includes both the standard USALI rules AND the
   ``MULTI_FIELD_*`` cross-field rules. If you can't map an issue to
   a known rule_id, DON'T emit a finding.
2. ``narrative`` is plain hotel-underwriting English (<=400 words),
   reads like a senior IC reviewer typed it, no marketing language.
3. ``cited_fields`` enumerates the canonical USALI field names the
   finding spans (e.g. ``["noi", "opex_ratio"]``).
4. ``severity_hint`` is one of CRITICAL / WARN / INFO. Default to
   WARN; reserve CRITICAL for math-broken or material-dollar issues.
5. ``impact_estimate_usd`` is OPTIONAL — supply only when the impact
   is quantifiable from the inputs.
6. Don't restate the deterministic variance flags; layer cross-field
   stories ON TOP. If the same issue is already in the variance flag
   list, skip it.
"""


def _format_financials(label: str, fin: USALIFinancials | None) -> str:
    if fin is None:
        return f"=== {label} ===\n(not provided)"
    parts = [
        f"=== {label} ===",
        f"Period: {fin.period_label}",
        f"Total Revenue: ${fin.total_revenue:,.0f}",
        f"  Rooms:   ${fin.rooms_revenue:,.0f}",
        f"  F&B:     ${fin.fb_revenue:,.0f}",
        f"  Other:   ${fin.other_revenue:,.0f}",
        f"Departmental Total: ${fin.dept_expenses.total:,.0f}",
        f"  Rooms:   ${fin.dept_expenses.rooms:,.0f}",
        f"  F&B:     ${fin.dept_expenses.food_beverage:,.0f}",
        f"Undistributed Total: ${fin.undistributed.total:,.0f}",
        f"  A&G:     ${fin.undistributed.administrative_general:,.0f}",
        f"  Sales/Mkt: ${fin.undistributed.sales_marketing:,.0f}",
        f"Mgmt Fee:    ${fin.mgmt_fee:,.0f}",
        f"FF&E Reserve: ${fin.ffe_reserve:,.0f}",
        f"Fixed Charges: ${fin.fixed_charges.total:,.0f}",
        f"  Insurance: ${fin.fixed_charges.insurance:,.0f}",
        f"  Property Tax: ${fin.fixed_charges.property_taxes:,.0f}",
        f"GOP:    ${fin.gop:,.0f}",
        f"NOI:    ${fin.noi:,.0f}",
        f"OpEx Ratio: {fin.opex_ratio:.2%}",
    ]
    if fin.occupancy is not None:
        parts.append(f"Occupancy: {fin.occupancy:.1%}")
    if fin.adr is not None:
        parts.append(f"ADR: ${fin.adr:,.2f}")
    if fin.revpar is not None:
        parts.append(f"RevPAR: ${fin.revpar:,.2f}")
    return "\n".join(parts)


def _format_variance(report: VarianceReport | None) -> str:
    if report is None or not report.flags:
        return "=== EXISTING VARIANCE FLAGS ===\n(none)"
    lines = ["=== EXISTING VARIANCE FLAGS ==="]
    for f in report.flags:
        note = (f.note or "").strip().split("\n", 1)[0]
        lines.append(
            f"- [{f.severity.value}] {f.field} actual={f.actual:,.2f} "
            f"broker={f.broker:,.2f} delta_pct={f.delta_pct} "
            f"rule={f.rule_id} — {note}"
        )
    return "\n".join(lines)


def _format_market_context(ctx: dict[str, Any]) -> str:
    if not ctx:
        return "=== MARKET CONTEXT ===\n(no context provided)"
    lines = ["=== MARKET CONTEXT ==="]
    for k, v in ctx.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _build_user_prompt(payload: CriticInput) -> str:
    parts = [
        f"deal_id: {payload.deal_id}",
        f"tenant_id: {payload.tenant_id}",
        _format_market_context(payload.market_context),
        _format_financials("T-12 ACTUAL", payload.t12_actual),
        _format_financials("BROKER PROFORMA", payload.broker_proforma),
        _format_variance(payload.initial_variance),
        (
            "Identify cross-field issues. Return one CriticEnvelope. "
            "Every finding's rule_id MUST exist in the catalog above. "
            "Do not duplicate findings already covered in the existing "
            "variance flags."
        ),
    ]
    return "\n\n".join(parts)


def _build_llm() -> Any:
    """Sonnet 4.6 for the narrative pass; deterministic temperature."""
    from ..llm import build_structured_llm

    return build_structured_llm(
        role="critic",
        schema=_CriticEnvelope,
        max_tokens=4000,
        timeout=180,
        temperature=0.1,
    )


async def _invoke_llm(
    llm: Any, messages: list[Any], usage: Any | None = None
) -> _CriticEnvelope:
    config = {"callbacks": [usage]} if usage is not None else None
    raw = await llm.ainvoke(messages, config=config)
    if isinstance(raw, _CriticEnvelope):
        return raw
    if isinstance(raw, BaseModel):
        return _CriticEnvelope.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return _CriticEnvelope.model_validate(raw)
    raise ValueError(f"Unexpected Critic LLM return: {type(raw).__name__}")


async def _run_narrative_pass(
    payload: CriticInput,
    *,
    deal_uuid: UUID,
) -> tuple[list[CriticFinding], str | None, Any, datetime, datetime]:
    """Optional LLM pass — returns (findings, summary, usage, t0, t1).

    Findings are *not* yet grounded — the validate-grounding step above
    drops anything pointing at an unknown rule_id.
    """
    from ..llm import build_agent_system_blocks, cached_system_message_blocks
    from ..usage import UsageCapture

    started = datetime.now(UTC)
    usage = UsageCapture()

    system_blocks = build_agent_system_blocks(
        role="critic",
        agent_instructions=CRITIC_SYSTEM_PROMPT,
    )
    rules_as_prompt_block()  # warm catalog cache
    messages = [
        cached_system_message_blocks(system_blocks, role="critic"),
        HumanMessage(content=_build_user_prompt(payload)),
    ]
    try:
        llm = _build_llm()
        envelope = await _invoke_llm(llm, messages, usage=usage)
    except (ValidationError, Exception) as exc:  # noqa: BLE001
        logger.warning("critic: narrative LLM failed (%s)", exc)
        return [], None, usage, started, datetime.now(UTC)

    findings: list[CriticFinding] = []
    namespace = uuid5(UUID("00000000-0000-0000-0000-000000000000"), str(deal_uuid))
    for f in envelope.findings:
        try:
            findings.append(
                CriticFinding(
                    id=uuid5(namespace, f"critic-llm:{f.rule_id}:{f.title}"),
                    deal_id=deal_uuid,
                    rule_id=f.rule_id,
                    title=f.title,
                    narrative=f.narrative,
                    severity=_norm_severity(f.severity_hint),
                    cited_fields=list(f.cited_fields or []),
                    cited_pages=[int(p) for p in (f.cited_pages or []) if p >= 1],
                    cited_document_ids=[],
                    impact_estimate_usd=f.impact_estimate_usd,
                )
            )
        except ValidationError as exc:
            logger.debug("critic: dropped malformed LLM finding (%s)", exc)
    completed = datetime.now(UTC)
    return findings, envelope.summary, usage, started, completed


# ─────────────────────── grounding validator ───────────────────────


def validate_grounding(
    findings: list[CriticFinding], known_rule_ids: set[str]
) -> tuple[list[CriticFinding], int]:
    """Return ``(grounded, rejected_count)``. Anything citing an unknown
    rule_id is dropped (fail-closed)."""
    grounded: list[CriticFinding] = []
    rejected = 0
    for f in findings:
        if f.rule_id in known_rule_ids:
            grounded.append(f)
        else:
            rejected += 1
            logger.warning(
                "critic: rejected finding rule_id=%s title=%r — not in catalog",
                f.rule_id,
                f.title,
            )
    return grounded, rejected


def _summarize(findings: list[CriticFinding]) -> str:
    if not findings:
        return "Critic identified no cross-field issues."
    crit = sum(1 for f in findings if f.severity is Severity.CRITICAL)
    warn = sum(1 for f in findings if f.severity is Severity.WARN)
    info = sum(1 for f in findings if f.severity is Severity.INFO)
    parts = [
        f"Fondok identified {len(findings)} cross-field "
        f"issue{'s' if len(findings) != 1 else ''} across the broker proforma"
    ]
    counts: list[str] = []
    if crit:
        counts.append(f"{crit} CRITICAL")
    if warn:
        counts.append(f"{warn} WARN")
    if info:
        counts.append(f"{info} INFO")
    if counts:
        parts.append(f" ({', '.join(counts)})")
    parts.append(".")
    return "".join(parts)


# ─────────────────────── public entry point ───────────────────────


@trace_agent("Critic")
async def run_critic(
    payload: CriticInput, *, run_narrative_pass: bool = True
) -> CriticOutput:
    """Run the Critic over a deal. Two-pass: deterministic + optional LLM.

    The narrative LLM pass is on by default for live runs; tests using
    ``EVALS_MOCK=true`` should set ``run_narrative_pass=False`` to
    exercise just the deterministic checks without burning tokens.
    """
    started = datetime.now(UTC)
    t0 = time.monotonic()

    # Empty inputs → empty report (preserves the no-op contract used by
    # the orchestrator before financials are spread).
    if payload.t12_actual is None and payload.broker_proforma is None:
        logger.info(
            "critic: no financials supplied (deal=%s) — empty report",
            payload.deal_id,
        )
        deal_uuid = _to_uuid(payload.deal_id)
        return CriticOutput(
            deal_id=payload.deal_id,
            report=CriticReport(deal_id=deal_uuid, findings=[]),
            success=True,
        )

    try:
        check_budget(
            {"deal_id": payload.deal_id, "model_calls": []}, stage="critic"
        )
    except Exception as exc:
        logger.warning("critic: budget check raised: %s", exc)
        return CriticOutput(
            deal_id=payload.deal_id,
            report=None,
            success=False,
            error=str(exc),
        )

    deal_uuid = _to_uuid(payload.deal_id)

    # Build the universe of legal rule_ids from the live catalog (plus
    # the conservative cross-field fallback so we still ground when the
    # CSV fails to load).
    catalog_rules = load_usali_rules()
    known_rule_ids: set[str] = (
        {r.rule_id for r in catalog_rules} | _FALLBACK_CROSS_FIELD_RULES
    )

    # ── Step 1: deterministic checks ───────────────────────────────────
    deterministic = _deterministic_cross_field_checks(payload, deal_uuid=deal_uuid)
    logger.info(
        "critic: %d deterministic cross-field finding(s) for deal=%s",
        len(deterministic),
        payload.deal_id,
    )

    # ── Step 2: optional LLM narrative pass ────────────────────────────
    llm_findings: list[CriticFinding] = []
    llm_summary: str | None = None
    model_calls: list[ModelCall] = []
    if run_narrative_pass:
        (
            llm_findings,
            llm_summary,
            usage,
            llm_started,
            llm_completed,
        ) = await _run_narrative_pass(payload, deal_uuid=deal_uuid)
        if usage is not None and (usage.input_tokens or usage.output_tokens):
            settings = get_settings()
            model_calls.append(
                ModelCall(
                    model=usage.model or settings.ANTHROPIC_ANALYST_MODEL,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cost_usd=0.0,
                    trace_id=payload.deal_id,
                    started_at=llm_started,
                    completed_at=llm_completed,
                    cache_creation_input_tokens=usage.cache_creation_input_tokens,
                    cache_read_input_tokens=usage.cache_read_input_tokens,
                    agent_name="critic",
                )
            )

    # ── Step 3: merge + dedupe on (rule_id, title) ─────────────────────
    seen: set[tuple[str, str]] = set()
    merged: list[CriticFinding] = []
    for f in deterministic + llm_findings:
        key = (f.rule_id, f.title.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        merged.append(f)

    # ── Step 4: grounding validator (fail-closed) ──────────────────────
    grounded, rejected = validate_grounding(merged, known_rule_ids)

    # ── Step 5: summarize + sort by severity ───────────────────────────
    sev_rank = {Severity.CRITICAL: 0, Severity.WARN: 1, Severity.INFO: 2}
    grounded.sort(key=lambda f: (sev_rank[f.severity], f.title))
    summary = (llm_summary or "").strip() or _summarize(grounded)

    crit_n = sum(1 for f in grounded if f.severity is Severity.CRITICAL)
    warn_n = sum(1 for f in grounded if f.severity is Severity.WARN)
    info_n = sum(1 for f in grounded if f.severity is Severity.INFO)
    report = CriticReport(
        deal_id=deal_uuid,
        findings=grounded,
        summary=summary,
        critical_count=crit_n,
        warn_count=warn_n,
        info_count=info_n,
    )

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "Critic OK deal=%s findings=%d (CRIT=%d WARN=%d INFO=%d) rejected=%d in %dms",
        payload.deal_id,
        len(grounded),
        crit_n,
        warn_n,
        info_n,
        rejected,
        elapsed_ms,
    )

    return CriticOutput(
        deal_id=payload.deal_id,
        report=report,
        success=True,
        rejected_findings=rejected,
        model_calls=model_calls,
    )


__all__ = [
    "CriticInput",
    "CriticOutput",
    "run_critic",
    "validate_grounding",
]
