"""Normalizer agent — maps Extractor fields onto USALI financials.

Calls Claude Sonnet 4.6 with structured output to project a list of
``ExtractionField`` rows onto the canonical ``USALIFinancials`` shape.

The LLM does the synonym mapping ("Rm Rev" → ``rooms_revenue``,
"F&B Sales" → ``fb_revenue``, "Mgmt Fee — Base" → ``mgmt_fee``) and
emits exactly one period; we then validate identities deterministically
and append a sub-list of ``warnings`` when the LLM-provided rollup
diverges from the line-item sum.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fondok_schemas import (
    DepartmentalExpenses,
    ExtractionField,
    FixedCharges,
    ModelCall,
    USALIFinancials,
    UndistributedExpenses,
)
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..budget import check_budget
from ..config import get_settings
from ..telemetry import trace_agent
from ..usali_rules import rules_as_prompt_block

logger = logging.getLogger(__name__)


# ─────────────────────── prompt ───────────────────────


SYSTEM_PROMPT = """You are Fondok's Normalizer agent — a hotel
acquisitions analyst who projects raw extracted line items onto a
single USALI-aligned profit-and-loss period.

Your output is one structured ``USALINormalized`` envelope. Map the
extracted fields onto the canonical USALI buckets:

  Revenue
    rooms_revenue    ← "Rm Rev", "Rooms Revenue", "Guest Room Sales"
    fb_revenue       ← "F&B", "Food & Beverage", "Restaurant + Bar"
    other_revenue    ← spa, parking, telecom, miscellaneous income
    total_revenue    ← sum of the three; verify the input rollup

  Departmental Expenses
    rooms            ← Rooms Department Expense / Direct Cost
    food_beverage    ← F&B Department Expense
    other_operated   ← Other Operated Department Expense
    total            ← sum of the three

  Undistributed Expenses
    administrative_general
    information_telecom
    sales_marketing  ← include franchise marketing fees
    property_operations
    utilities
    total            ← sum

  Fees / Reserves (top-level, not undistributed)
    mgmt_fee         ← base + incentive management fees
    ffe_reserve      ← FF&E reserve

  Fixed Charges
    property_taxes
    insurance
    rent             ← ground rent (0.0 if fee simple)
    other_fixed
    total            ← sum

  Computed lines
    gop = total_revenue − dept_expenses.total − undistributed.total
    noi = gop − mgmt_fee − ffe_reserve − fixed_charges.total
    opex_ratio = (dept_expenses.total + undistributed.total
                  + mgmt_fee + ffe_reserve + fixed_charges.total)
                 / total_revenue

  Operational KPIs
    occupancy (0..1), adr (USD), revpar (USD)

Rules:
1. Every numeric value is in absolute USD (not thousands). Strip
   whatever scaling the source used.
2. ``period_label`` is required: use what the source describes
   ("TTM ended 2026-03-31", "Calendar 2027 Stabilized", etc.).
3. Verify identities deterministically before emitting:
     * GOP_IDENTITY:   gop ≈ total_revenue − dept − undistributed
     * NOI_IDENTITY:   noi ≈ gop − mgmt_fee − ffe_reserve − fixed_charges
     * REVPAR_CHECK:   revpar ≈ occupancy × adr (when occ + adr present)
   When the source's stated value disagrees with the computed value
   beyond 0.5%, prefer the COMPUTED value and append a warning to
   ``normalization_warnings`` describing the gap.
4. If the extracted fields are insufficient to populate a field
   (e.g. no FF&E reserve in the source), use 0.0 and add a warning.
5. Map only fields you can ground in the input. Never invent values.

Output: one structured ``USALINormalized`` envelope. No prose
outside the schema.
"""


# ─────────────────────── structured-output envelope ───────────────────────


class _DeptExpensesEnv(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rooms: Annotated[float, Field(ge=0)] = 0.0
    food_beverage: Annotated[float, Field(ge=0)] = 0.0
    other_operated: Annotated[float, Field(ge=0)] = 0.0
    total: Annotated[float, Field(ge=0)] = 0.0


class _UndistribEnv(BaseModel):
    model_config = ConfigDict(extra="forbid")
    administrative_general: Annotated[float, Field(ge=0)] = 0.0
    information_telecom: Annotated[float, Field(ge=0)] = 0.0
    sales_marketing: Annotated[float, Field(ge=0)] = 0.0
    property_operations: Annotated[float, Field(ge=0)] = 0.0
    utilities: Annotated[float, Field(ge=0)] = 0.0
    total: Annotated[float, Field(ge=0)] = 0.0


class _FixedEnv(BaseModel):
    model_config = ConfigDict(extra="forbid")
    property_taxes: Annotated[float, Field(ge=0)] = 0.0
    insurance: Annotated[float, Field(ge=0)] = 0.0
    rent: Annotated[float, Field(ge=0)] = 0.0
    other_fixed: Annotated[float, Field(ge=0)] = 0.0
    total: Annotated[float, Field(ge=0)] = 0.0


class _USALINormalized(BaseModel):
    """LLM-facing envelope mirroring ``USALIFinancials``."""

    model_config = ConfigDict(extra="forbid")

    period_label: Annotated[str, Field(min_length=1, max_length=80)]

    rooms_revenue: Annotated[float, Field(ge=0)]
    fb_revenue: Annotated[float, Field(ge=0)] = 0.0
    other_revenue: Annotated[float, Field(ge=0)] = 0.0
    total_revenue: Annotated[float, Field(ge=0)]

    dept_expenses: _DeptExpensesEnv = Field(default_factory=_DeptExpensesEnv)
    undistributed: _UndistribEnv = Field(default_factory=_UndistribEnv)
    mgmt_fee: Annotated[float, Field(ge=0)] = 0.0
    ffe_reserve: Annotated[float, Field(ge=0)] = 0.0
    fixed_charges: _FixedEnv = Field(default_factory=_FixedEnv)

    gop: float
    noi: float
    opex_ratio: Annotated[float, Field(ge=0.0, le=2.0)] = 0.0

    occupancy: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    adr: Annotated[float, Field(ge=0)] | None = None
    revpar: Annotated[float, Field(ge=0)] | None = None

    normalization_warnings: list[str] = Field(default_factory=list)


# ─────────────────────── I/O contracts ───────────────────────


class NormalizerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    extracted_documents: list[Any] = Field(default_factory=list)
    fields: list[ExtractionField] = Field(
        default_factory=list,
        description="Flat list of ExtractionField rows from the Extractor.",
    )
    period_hint: str | None = Field(
        default=None,
        description="Optional caller hint, e.g. 'TTM ended 2026-03-31'.",
    )


class NormalizerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    normalized_spread: USALIFinancials | None = None
    warnings: list[str] = Field(default_factory=list)
    success: bool = True
    error: str | None = None
    model_calls: list[ModelCall] = Field(default_factory=list)


# ─────────────────────── helpers ───────────────────────


_TOLERANCE = 0.005  # 0.5% — same threshold the USALI catalog uses.


def _within(a: float, b: float, *, tol: float = _TOLERANCE) -> bool:
    if b == 0:
        return abs(a) < 1.0
    return abs(a - b) / abs(b) <= tol


def _validate_and_recompute(
    env: _USALINormalized,
) -> tuple[USALIFinancials, list[str]]:
    """Project the LLM envelope onto the canonical schema, recomputing
    identities deterministically and accumulating warnings on drift."""
    warnings: list[str] = list(env.normalization_warnings)

    # Revenue rollup
    components = env.rooms_revenue + env.fb_revenue + env.other_revenue
    if env.total_revenue > 0 and not _within(env.total_revenue, components):
        warnings.append(
            f"REVENUE_SUM: total_revenue={env.total_revenue:,.0f} != "
            f"sum(rooms+fb+other)={components:,.0f}"
        )
    total_revenue = env.total_revenue or components

    # Departmental rollup
    dept_components = (
        env.dept_expenses.rooms
        + env.dept_expenses.food_beverage
        + env.dept_expenses.other_operated
    )
    if env.dept_expenses.total > 0 and not _within(
        env.dept_expenses.total, dept_components
    ):
        warnings.append(
            f"DEPT_EXPENSE_SUM: total={env.dept_expenses.total:,.0f} != "
            f"sum={dept_components:,.0f}"
        )
    dept_total = env.dept_expenses.total or dept_components

    # Undistributed rollup
    undist_components = (
        env.undistributed.administrative_general
        + env.undistributed.information_telecom
        + env.undistributed.sales_marketing
        + env.undistributed.property_operations
        + env.undistributed.utilities
    )
    if env.undistributed.total > 0 and not _within(
        env.undistributed.total, undist_components
    ):
        warnings.append(
            f"UNDISTRIBUTED_SUM: total={env.undistributed.total:,.0f} != "
            f"sum={undist_components:,.0f}"
        )
    undist_total = env.undistributed.total or undist_components

    # Fixed-charges rollup
    fixed_components = (
        env.fixed_charges.property_taxes
        + env.fixed_charges.insurance
        + env.fixed_charges.rent
        + env.fixed_charges.other_fixed
    )
    if env.fixed_charges.total > 0 and not _within(
        env.fixed_charges.total, fixed_components
    ):
        warnings.append(
            f"FIXED_CHARGES_SUM: total={env.fixed_charges.total:,.0f} != "
            f"sum={fixed_components:,.0f}"
        )
    fixed_total = env.fixed_charges.total or fixed_components

    # GOP identity
    gop_computed = total_revenue - dept_total - undist_total
    if env.gop and not _within(env.gop, gop_computed):
        warnings.append(
            f"GOP_IDENTITY: stated={env.gop:,.0f} computed={gop_computed:,.0f}"
        )
    gop_final = env.gop if abs(env.gop) > 1 else gop_computed

    # NOI identity
    noi_computed = gop_final - env.mgmt_fee - env.ffe_reserve - fixed_total
    if env.noi and not _within(env.noi, noi_computed):
        warnings.append(
            f"NOI_IDENTITY: stated={env.noi:,.0f} computed={noi_computed:,.0f}"
        )
    noi_final = env.noi if abs(env.noi) > 1 else noi_computed

    # OpEx ratio
    opex_total = dept_total + undist_total + env.mgmt_fee + env.ffe_reserve + fixed_total
    opex_ratio = opex_total / total_revenue if total_revenue > 0 else 0.0
    if env.opex_ratio and not _within(env.opex_ratio, opex_ratio, tol=0.02):
        warnings.append(
            f"OPEX_RATIO: stated={env.opex_ratio:.3f} computed={opex_ratio:.3f}"
        )

    # RevPAR identity
    if env.revpar is not None and env.occupancy is not None and env.adr is not None:
        rp_computed = env.occupancy * env.adr
        if env.revpar and not _within(env.revpar, rp_computed):
            warnings.append(
                f"REVPAR_CHECK: stated={env.revpar:.2f} computed={rp_computed:.2f}"
            )

    spread = USALIFinancials(
        period_label=env.period_label,
        rooms_revenue=env.rooms_revenue,
        fb_revenue=env.fb_revenue,
        other_revenue=env.other_revenue,
        total_revenue=total_revenue,
        dept_expenses=DepartmentalExpenses(
            rooms=env.dept_expenses.rooms,
            food_beverage=env.dept_expenses.food_beverage,
            other_operated=env.dept_expenses.other_operated,
            total=dept_total,
        ),
        undistributed=UndistributedExpenses(
            administrative_general=env.undistributed.administrative_general,
            information_telecom=env.undistributed.information_telecom,
            sales_marketing=env.undistributed.sales_marketing,
            property_operations=env.undistributed.property_operations,
            utilities=env.undistributed.utilities,
            total=undist_total,
        ),
        mgmt_fee=env.mgmt_fee,
        ffe_reserve=env.ffe_reserve,
        fixed_charges=FixedCharges(
            property_taxes=env.fixed_charges.property_taxes,
            insurance=env.fixed_charges.insurance,
            rent=env.fixed_charges.rent,
            other_fixed=env.fixed_charges.other_fixed,
            total=fixed_total,
        ),
        gop=gop_final,
        noi=noi_final,
        opex_ratio=min(2.0, max(0.0, opex_ratio)),
        occupancy=env.occupancy,
        adr=env.adr,
        revpar=env.revpar,
    )
    return spread, warnings


def _format_fields(fields: list[ExtractionField]) -> str:
    lines = ["=== EXTRACTED FIELDS (field_name | value | unit | page | conf) ==="]
    for f in fields:
        lines.append(
            f"- {f.field_name} | {f.value!r} | {f.unit or '-'} | "
            f"p{f.source_page} | conf={f.confidence:.2f}"
        )
    return "\n".join(lines)


def _build_user_prompt(payload: NormalizerInput) -> str:
    parts: list[str] = [
        f"deal_id: {payload.deal_id}",
        f"period hint: {payload.period_hint or '<none>'}",
        "",
        _format_fields(payload.fields),
        "",
        (
            "Project these fields onto a single USALI period. Recompute "
            "every identity (GOP, NOI, RevPAR) deterministically. Return "
            "one USALINormalized envelope."
        ),
    ]
    return "\n".join(parts)


# ─────────────────────── LLM client ───────────────────────


def _build_llm() -> Any:
    """Sonnet 4.6 with structured output bound to ``_USALINormalized``."""
    from ..llm import build_structured_llm

    return build_structured_llm(
        role="normalizer",
        schema=_USALINormalized,
        max_tokens=4096,
        timeout=120,
        temperature=0.0,
    )


async def _invoke_llm(
    llm: Any, messages: list[Any], usage: Any | None = None
) -> _USALINormalized:
    config = {"callbacks": [usage]} if usage is not None else None
    raw = await llm.ainvoke(messages, config=config)
    if isinstance(raw, _USALINormalized):
        return raw
    if isinstance(raw, BaseModel):
        return _USALINormalized.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return _USALINormalized.model_validate(raw)
    raise ValueError(f"Unexpected Normalizer LLM return: {type(raw).__name__}")


# ─────────────────────── public entry point ───────────────────────


@trace_agent("Normalizer")
async def run_normalizer(payload: NormalizerInput) -> NormalizerOutput:
    """Map an Extractor's field list onto a single USALI period."""
    started = datetime.now(UTC)
    t0 = time.monotonic()

    # Backwards-compatible no-op path for the graph stub.
    if not payload.fields:
        logger.info(
            "normalizer: no extracted fields (deal=%s) — empty result",
            payload.deal_id,
        )
        return NormalizerOutput(
            deal_id=payload.deal_id,
            normalized_spread=None,
            success=True,
            model_calls=[],
        )

    try:
        check_budget(
            {"deal_id": payload.deal_id, "model_calls": []}, stage="normalizer"
        )
    except Exception as exc:
        logger.warning("normalizer: budget check raised: %s", exc)
        return NormalizerOutput(
            deal_id=payload.deal_id,
            normalized_spread=None,
            success=False,
            error=str(exc),
        )

    from ..llm import cached_system_message_blocks
    from ..usage import UsageCapture

    system_blocks = [SYSTEM_PROMPT, rules_as_prompt_block()]
    messages = [
        cached_system_message_blocks(system_blocks, role="normalizer"),
        HumanMessage(content=_build_user_prompt(payload)),
    ]
    usage = UsageCapture()

    try:
        llm = _build_llm()
        envelope = await _invoke_llm(llm, messages, usage=usage)
    except (ValidationError, Exception) as exc:  # noqa: BLE001 - error path
        logger.warning("normalizer: LLM call failed (%s)", exc)
        return NormalizerOutput(
            deal_id=payload.deal_id,
            normalized_spread=None,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    try:
        spread, warnings = _validate_and_recompute(envelope)
    except (ValidationError, ValueError) as exc:
        logger.warning("normalizer: projection failed (%s)", exc)
        return NormalizerOutput(
            deal_id=payload.deal_id,
            normalized_spread=None,
            success=False,
            error=f"projection: {exc}",
        )

    completed = datetime.now(UTC)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    settings = get_settings()
    model_call = ModelCall(
        model=usage.model or settings.ANTHROPIC_NORMALIZER_MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=0.0,
        trace_id=payload.deal_id,
        started_at=started,
        completed_at=completed,
    )

    if warnings:
        for w in warnings:
            logger.warning("normalizer: %s", w)

    logger.info(
        "normalizer OK deal=%s period=%s noi=%.0f warnings=%d in %dms",
        payload.deal_id,
        spread.period_label,
        spread.noi,
        len(warnings),
        elapsed_ms,
    )

    return NormalizerOutput(
        deal_id=payload.deal_id,
        normalized_spread=spread,
        warnings=warnings,
        success=True,
        model_calls=[model_call],
    )


__all__ = ["NormalizerInput", "NormalizerOutput", "run_normalizer"]
