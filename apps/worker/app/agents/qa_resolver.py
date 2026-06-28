"""QA Resolver agent — the brains behind Wave 1 #5's seller Q&A re-ingestion loop.

Input: the analyst's broker question (snapshot at the time it was sent),
the broker's pasted email reply, and surrounding deal context (the
supporting variance numbers + the deal's current engine assumptions so
the agent doesn't propose overrides that don't make sense).

Output (structured via Anthropic tool-use):

  * ``verdict`` — one of ``resolved`` / ``partially_resolved`` /
    ``still_concerning``
  * ``summary`` — 1-2 plain-English sentences wrapping the broker's reply
  * ``proposed_overrides`` — list of ``ProposedOverride`` rows the agent
    thinks should land in the engine inputs; the analyst confirms each
    one before it lands in ``deals.field_overrides``. Allow-listed to the
    canonical paths in ``ALLOWED_OVERRIDE_PATHS`` so the agent can't
    smuggle a field path the engines don't understand.
  * ``audit_note`` — IC memo footnote text, surfaced from
    ``run_analyst`` so the underwriting section cites the broker reply

Cost guard: this agent's calls flow through the per-deal $20 budget
guard (``apps.worker.app.budget``). When the deal is over budget the
caller surfaces a 402 (Payment Required) so the analyst sees a clear
"raise the budget" message instead of a silent failure.

Pattern mirrors ``variance.py``: build_structured_llm with Sonnet 4.6,
deterministic temperature, 4-block cached system prompt, UsageCapture
for cost persistence.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fondok_schemas import ModelCall
from fondok_schemas.broker_qa import (
    ProposedOverride,
    ProposedOverrideConfidence,
    ResolverVerdict,
)
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..budget import BudgetExceededError, check_budget
from ..config import get_settings
from ..telemetry import trace_agent

logger = logging.getLogger(__name__)


# ─────────────────────── allow-listed override paths ───────────────────
#
# The ~30 canonical engine-input paths the resolver agent is allowed to
# propose overrides on. Anything else gets dropped at validation time.
#
# Mirrors the most-important assumptions the engines consume off
# ``_load_engine_inputs`` in ``services/engine_runner.py``. Adding a new
# path requires:
#
#   1. Add it here (so the resolver agent can name it).
#   2. Make sure the canonical key is in either
#      ``OM_CAPITAL_FIELD_ALIASES`` or ``OM_DEBT_FIELD_ALIASES`` in
#      ``extraction/field_catalog.yaml`` so the apply-flow knows how to
#      route the override into the engine inputs.
#
# Keep the docstring next to each path — the agent's prompt references
# them so the LLM picks the right one.
ALLOWED_OVERRIDE_PATHS: dict[str, str] = {
    # ── Capital structure
    "asking_price.headline_price_usd": "Purchase price (USD)",
    "asking_price.price_per_key_usd": "Price per key (USD)",
    "broker_proforma.renovation_budget_usd": "Renovation / PIP budget (USD)",
    "broker_proforma.entry_cap_rate": "Entry cap rate (0..1 fraction)",
    "property_overview.year_built": "Year built",
    # ── In-place debt
    "in_place_debt.loan_balance_usd": "Existing loan balance (USD)",
    "in_place_debt.interest_rate_pct": "Existing loan interest rate (0..1 or 0..100)",
    "in_place_debt.amortization_years": "Existing loan amortization (years)",
    "in_place_debt.term_years": "Existing loan term (years)",
    "in_place_debt.ltv_pct": "Existing LTV (0..1 or 0..100)",
    # ── Revenue drivers (T-12 / forward)
    "p_and_l_usali.operational_kpis.occupancy_pct": "Occupancy (0..1)",
    "p_and_l_usali.operational_kpis.adr_usd": "ADR (USD)",
    "p_and_l_usali.operational_kpis.revpar_usd": "RevPAR (USD)",
    "p_and_l_usali.operating_revenue.rooms_revenue": "Rooms revenue (USD)",
    "p_and_l_usali.operating_revenue.fb_revenue": "F&B revenue (USD)",
    "p_and_l_usali.operating_revenue.other_revenue": "Other revenue (USD)",
    "p_and_l_usali.operating_revenue.resort_fees": "Resort fees (USD)",
    # ── Departmental expenses
    "p_and_l_usali.departmental_expenses.rooms": "Rooms departmental expense (USD)",
    "p_and_l_usali.departmental_expenses.food_beverage": "F&B departmental expense (USD)",
    "p_and_l_usali.departmental_expenses.other_operated": "Other operated expense (USD)",
    # ── Undistributed expenses
    "p_and_l_usali.undistributed.administrative_general": "A&G (USD)",
    "p_and_l_usali.undistributed.information_telecom": "IT / Telecom (USD)",
    "p_and_l_usali.undistributed.sales_marketing": "Sales & Marketing (USD)",
    "p_and_l_usali.undistributed.property_operations": "Property Operations / Repairs (USD)",
    "p_and_l_usali.undistributed.utilities": "Utilities (USD)",
    # ── Fees + reserves + fixed charges
    "p_and_l_usali.fees_and_reserves.mgmt_fee": "Management fee (USD)",
    "p_and_l_usali.fees_and_reserves.ffe_reserve": "FF&E reserve (USD)",
    "p_and_l_usali.fixed_charges.property_taxes": "Property taxes (USD)",
    "p_and_l_usali.fixed_charges.insurance": "Insurance (USD)",
}


# ─────────────────────── prompt ───────────────────────


SYSTEM_PROMPT = """You are Fondok's QA Resolver — the agent that reads a
broker's emailed reply to a Fondok-generated YoY variance question and
decides (a) whether the reply resolves the analyst's concern and (b)
whether any underwriting assumption should change as a result.

You are NOT an underwriter. You do not invent numbers. You do not chase
risk that the broker's reply does not actually create. If the reply
plainly explains the variance and the explanation is consistent with
the supporting data the analyst gave you, the verdict is ``resolved``
and ``proposed_overrides`` may be empty.

Verdict ladder
--------------
  * ``resolved``            — broker's reply addresses the variance AND
                              is internally consistent. Propose at most
                              the overrides that the reply directly
                              implies (e.g. broker reset the F&B operator
                              contract — restate the FB margin to the
                              pre-closure baseline the reply names).
  * ``partially_resolved``  — broker addressed part of it but left a
                              material question open. Surface the open
                              concern in ``summary`` so the analyst can
                              follow up.
  * ``still_concerning``    — broker either dodged the question or the
                              reply makes the variance LARGER (e.g.
                              broker confirmed a permanent revenue loss
                              the proforma hadn't reflected).

Proposed overrides — strict rules
---------------------------------
  1. ``field_path`` MUST be one of the canonical paths in the
     ALLOWED OVERRIDE PATHS block below. Off-catalog paths are dropped
     at validation; if you can't map the broker's number to one of these
     paths, DO NOT propose the override — say so in ``audit_note`` instead.
  2. ``value`` is the exact override value. For percentage-like paths
     (occupancy / LTV / cap rate / interest rate) emit a 0..1 fraction —
     not a percentage. For dollar fields emit the raw USD number.
  3. ``rationale`` is a short hotel-underwriting sentence the analyst
     will see next to the checkbox AND that gets written into the
     ``FieldOverrideRecord`` note. Lead with the broker's reasoning,
     not the math. Example: "Broker reset F&B contract Nov-24; reverting
     FB margin to the pre-closure 18% baseline named in their reply."
  4. ``confidence``: ``high`` only when the broker reply states the
     replacement value explicitly (or the supporting data computes
     unambiguously). ``low`` when you're interpreting an imprecise reply.
     Default to ``medium`` when unsure.

Audit note — what the IC memo will cite
---------------------------------------
``audit_note`` is one sentence the Analyst memo will surface in a
footnote in the underwriting section. Format:

  "Per broker reply (YYYY-MM-DD): <reason>. <impact on underwriting in
  ≤20 words>."

If you don't know the date (rarely needed — the date is for context;
the analyst's pasted reply may not include one), omit the leading
date — leave only the reason + impact. Keep the note ≤220 chars.

Output: one ``ResolverEnvelope`` (structured). Empty proposed_overrides
is valid and common.
"""


# ─────────────────────── structured-output envelope ───────────────────────


class _ProposedOverrideEnvelope(BaseModel):
    """LLM-side mirror of ``ProposedOverride``.

    Kept locally so the LLM tool schema doesn't inherit the strict
    ``extra='forbid'`` of the canonical schema; we re-validate to the
    canonical type post-parse. Allow-list enforcement happens in
    ``_filter_overrides`` so off-catalog paths get logged + dropped,
    not 422'd back to the caller.
    """

    model_config = ConfigDict(extra="forbid")

    field_path: Annotated[str, Field(min_length=1, max_length=240)]
    value: float | str
    rationale: Annotated[str, Field(min_length=1, max_length=1000)]
    confidence: Literal["high", "medium", "low"]


class _ResolverEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["resolved", "partially_resolved", "still_concerning"]
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    proposed_overrides: list[_ProposedOverrideEnvelope] = Field(default_factory=list)
    audit_note: Annotated[str, Field(min_length=1, max_length=2000)]


# ─────────────────────── I/O contracts ───────────────────────


@dataclass
class QAResolverInput:
    """All the context the agent needs to read + decide.

    ``supporting_data`` is the variance snapshot the analyst sent
    alongside the question — line_item, period_key, variance_pct,
    actual_prior, actual_current. Mostly used so the resolver can sanity-
    check the broker's reply ("they said F&B dropped because of a
    closure — does the closure window match the variance period?").

    ``current_assumptions`` is the deal's CURRENT engine inputs as a
    flat ``{path: number}`` dict (rendered into the prompt so the agent
    doesn't propose an override that's already in place).
    """

    deal_id: str
    tenant_id: str
    broker_question_id: str
    analyst_question: str
    broker_response: str
    supporting_data: dict[str, Any]
    current_assumptions: dict[str, float]


@dataclass
class QAResolverOutput:
    """What the agent emits + cost-bookkeeping for persistence."""

    deal_id: str
    verdict: ResolverVerdict | None
    summary: str
    proposed_overrides: list[ProposedOverride]
    audit_note: str
    success: bool
    error: str | None
    model_calls: list[ModelCall]


# ─────────────────────── prompt rendering ───────────────────────


def _render_allowed_paths_block() -> str:
    """Render the allow-listed paths so the prompt names every canonical key.

    Kept tabular so the LLM has a hard reference for naming the path
    correctly — Sonnet otherwise occasionally invents a similar-looking
    but invalid path on a fuzzy match.
    """
    lines = ["=== ALLOWED OVERRIDE PATHS (use these exact strings) ==="]
    for path, label in ALLOWED_OVERRIDE_PATHS.items():
        lines.append(f"  {path}    — {label}")
    return "\n".join(lines)


def _render_supporting_data(data: dict[str, Any]) -> str:
    if not data:
        return "(no supporting variance snapshot provided)"
    lines = ["=== SUPPORTING DATA (the variance that triggered the question) ==="]
    for k in (
        "line_item",
        "period_key",
        "variance_pct",
        "actual_prior",
        "actual_current",
        "threshold_pct",
    ):
        if k in data and data[k] is not None:
            lines.append(f"  {k}: {data[k]}")
    return "\n".join(lines)


def _render_current_assumptions(assumptions: dict[str, float]) -> str:
    """Render the small subset of current assumptions the resolver needs.

    Only the keys that are also in ALLOWED_OVERRIDE_PATHS — there's no
    value in the prompt being able to see assumptions the agent can't
    touch, and the cache breakpoint stays stable across runs.
    """
    if not assumptions:
        return "=== CURRENT ASSUMPTIONS ===\n(none extracted — engine is on seed defaults)"
    lines = ["=== CURRENT ASSUMPTIONS (so you don't restate what's already in place) ==="]
    canonical_keys = set(ALLOWED_OVERRIDE_PATHS.keys())
    # Match either the full path or the canonical short key the engine uses.
    short_to_full = {p.rsplit(".", 1)[-1]: p for p in canonical_keys}
    for k, v in sorted(assumptions.items()):
        path = k if k in canonical_keys else short_to_full.get(k)
        if not path:
            continue
        try:
            lines.append(f"  {path}: {float(v):.6g}")
        except (TypeError, ValueError):
            continue
    if len(lines) == 1:
        return "=== CURRENT ASSUMPTIONS ===\n(none of the allow-listed paths are set yet)"
    return "\n".join(lines)


def _build_user_prompt(payload: QAResolverInput) -> str:
    parts: list[str] = [
        _render_allowed_paths_block(),
        "",
        f"DEAL: {payload.deal_id}",
        "",
        "=== ANALYST'S BROKER QUESTION ===",
        payload.analyst_question.strip(),
        "",
        "=== BROKER'S REPLY (raw paste from analyst) ===",
        payload.broker_response.strip(),
        "",
        _render_supporting_data(payload.supporting_data),
        "",
        _render_current_assumptions(payload.current_assumptions),
        "",
        (
            "Now produce one ResolverEnvelope:\n"
            "  1. verdict (resolved / partially_resolved / still_concerning)\n"
            "  2. summary (1-2 sentences)\n"
            "  3. proposed_overrides (only paths from the allow-list above; "
            "empty list is fine)\n"
            "  4. audit_note (memo footnote, ≤220 chars)"
        ),
    ]
    return "\n".join(parts)


# ─────────────────────── LLM client ───────────────────────


def _build_llm() -> Any:
    """Sonnet 4.6 with structured output bound to ``_ResolverEnvelope``."""
    from ..llm import build_structured_llm

    return build_structured_llm(
        role="variance",  # shares the variance/analyst pricing track
        schema=_ResolverEnvelope,
        max_tokens=2048,
        timeout=120,
        temperature=0.1,
    )


async def _invoke_llm(
    llm: Any, messages: list[Any], usage: Any | None = None
) -> _ResolverEnvelope:
    config = {"callbacks": [usage]} if usage is not None else None
    raw = await llm.ainvoke(messages, config=config)
    if isinstance(raw, _ResolverEnvelope):
        return raw
    if isinstance(raw, BaseModel):
        return _ResolverEnvelope.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return _ResolverEnvelope.model_validate(raw)
    raise ValueError(f"Unexpected QAResolver LLM return: {type(raw).__name__}")


# ─────────────────────── validation + filtering ───────────────────────


_PERCENTAGE_LIKE_PATHS: frozenset[str] = frozenset({
    "broker_proforma.entry_cap_rate",
    "in_place_debt.interest_rate_pct",
    "in_place_debt.ltv_pct",
    "p_and_l_usali.operational_kpis.occupancy_pct",
})


def _normalize_value(path: str, value: float | str) -> float | str:
    """Coerce a percentage-style value emitted as 0..100 back to 0..1.

    Defensive — the prompt tells the agent to emit 0..1 but Sonnet
    occasionally returns the value the broker named (often 0..100).
    The engine normalizer would catch this on the back end, but the UI
    renders the raw proposed value next to the checkbox so we'd rather
    show a coherent 0.78 than 78 when the analyst is deciding.
    """
    if not isinstance(value, (int, float)):
        return value
    if path in _PERCENTAGE_LIKE_PATHS and float(value) > 1.0:
        return float(value) / 100.0
    return float(value)


def _filter_overrides(
    raw: list[_ProposedOverrideEnvelope],
) -> list[ProposedOverride]:
    """Drop off-catalog paths, normalize values, log what got dropped.

    The agent is instructed to stay on-catalog but a Sonnet hallucination
    of a similar-looking path shouldn't break the resolver flow — we
    just drop the row and continue.
    """
    out: list[ProposedOverride] = []
    for o in raw:
        if o.field_path not in ALLOWED_OVERRIDE_PATHS:
            logger.info(
                "qa_resolver: dropping off-catalog override field_path=%s",
                o.field_path,
            )
            continue
        try:
            out.append(
                ProposedOverride(
                    field_path=o.field_path,
                    value=_normalize_value(o.field_path, o.value),
                    rationale=o.rationale,
                    confidence=o.confidence,
                )
            )
        except ValidationError as exc:
            logger.warning(
                "qa_resolver: dropping malformed override field_path=%s (%s)",
                o.field_path,
                exc,
            )
    return out


# ─────────────────────── public entry point ───────────────────────


@trace_agent("QAResolver")
async def run_qa_resolver(payload: QAResolverInput) -> QAResolverOutput:
    """Read the broker's reply, decide verdict, propose overrides.

    Budget guard runs FIRST — if the deal is over the per-deal cap the
    caller wraps the BudgetExceededError into a 402 response (no LLM
    call ever happens).
    """
    started = datetime.now(UTC)
    t0 = time.monotonic()

    if not payload.analyst_question.strip() or not payload.broker_response.strip():
        return QAResolverOutput(
            deal_id=payload.deal_id,
            verdict=None,
            summary="",
            proposed_overrides=[],
            audit_note="",
            success=False,
            error="analyst_question and broker_response are both required",
            model_calls=[],
        )

    # Per-deal $20 cap — let the caller catch BudgetExceededError so the
    # API surface can map it to a 402. Inside this function we treat it
    # the same way the variance agent does: short-circuit with an error.
    try:
        check_budget(
            {"deal_id": payload.deal_id, "model_calls": []}, stage="qa_resolver"
        )
    except BudgetExceededError:
        raise

    from ..llm import build_agent_system_blocks, cached_system_message_blocks
    from ..usage import UsageCapture

    # 4-block system prompt mirrors the variance agent — agent
    # instructions (uncached, ~2KB) + USALI rules + brand catalog +
    # schema addendum. The allow-listed paths go in the user prompt so
    # they're easy to reorder when we add new paths without busting the
    # system-prompt cache.
    system_blocks = build_agent_system_blocks(
        role="variance",
        agent_instructions=SYSTEM_PROMPT,
    )
    messages = [
        cached_system_message_blocks(system_blocks, role="variance"),
        HumanMessage(content=_build_user_prompt(payload)),
    ]
    usage = UsageCapture()

    envelope: _ResolverEnvelope | None = None
    llm_error: str | None = None
    try:
        llm = _build_llm()
        envelope = await _invoke_llm(llm, messages, usage=usage)
    except (ValidationError, Exception) as exc:  # noqa: BLE001 - error path
        logger.warning("qa_resolver: LLM call failed (%s)", exc)
        llm_error = f"{type(exc).__name__}: {exc}"

    completed = datetime.now(UTC)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    if envelope is None:
        return QAResolverOutput(
            deal_id=payload.deal_id,
            verdict=None,
            summary="",
            proposed_overrides=[],
            audit_note="",
            success=False,
            error=llm_error or "no envelope returned",
            model_calls=[],
        )

    proposed = _filter_overrides(envelope.proposed_overrides)

    settings = get_settings()
    fallback_model = (
        getattr(settings, "ANTHROPIC_VARIANCE_MODEL", None)
        or settings.ANTHROPIC_ANALYST_MODEL
    )
    model_calls: list[ModelCall] = [
        ModelCall(
            model=usage.model or fallback_model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=0.0,
            trace_id=payload.deal_id,
            started_at=started,
            completed_at=completed,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
            agent_name="qa_resolver",
        )
    ]

    # Persist for the cost dashboard. Best-effort.
    try:
        from ..cost_persistence import persist_model_calls_standalone

        await persist_model_calls_standalone(
            deal_id=payload.deal_id,
            tenant_id=payload.tenant_id,
            calls=model_calls,
        )
    except Exception:  # noqa: BLE001 - best-effort persistence
        logger.debug("qa_resolver: cost persistence failed (non-fatal)", exc_info=True)

    logger.info(
        "qa_resolver OK deal=%s verdict=%s proposed=%d in %dms",
        payload.deal_id,
        envelope.verdict,
        len(proposed),
        elapsed_ms,
    )

    return QAResolverOutput(
        deal_id=payload.deal_id,
        verdict=envelope.verdict,
        summary=envelope.summary,
        proposed_overrides=proposed,
        audit_note=envelope.audit_note,
        success=True,
        error=None,
        model_calls=model_calls,
    )


__all__ = [
    "ALLOWED_OVERRIDE_PATHS",
    "QAResolverInput",
    "QAResolverOutput",
    "run_qa_resolver",
]
