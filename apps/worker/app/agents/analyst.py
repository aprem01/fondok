"""Analyst agent — drafts the IC memo from the locked spread + engine outputs.

Calls Claude Opus 4.7 (1M-context tier) with structured output to draft
an ``InvestmentMemo`` containing six required sections:

  1. INVESTMENT_THESIS    — why this deal, why now
  2. MARKET_ANALYSIS      — submarket dynamics, comp set, demand
  3. DEAL_OVERVIEW        — property profile (the "Property" section)
  4. FINANCIAL_ANALYSIS   — T-12, broker proforma reconciliation,
                            engine outputs (IRR/multiple/DSCR)
  5. RISK_FACTORS         — variance flags, capex, market risk
  6. RECOMMENDATION       — Approve / Conditional / Decline + terms

Every section ships at least one ``Citation`` pointing back to a
source document + page (or to a USALI rule_id for normative claims).

Streaming
---------
When ``MEMO_STREAMING_ENABLED=true`` the agent drafts each section in
its own LLM call and publishes the completed section to the in-process
``MemoBroadcast`` so the UI can render the memo as it builds. The total
wall-clock is similar to the single-shot path; perceived latency drops
substantially. Final return shape is identical to the single-shot path.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID, uuid5

from fondok_schemas import (
    Citation,
    ConfidenceReport,
    InvestmentMemo,
    MemoSection,
    MemoSectionId,
    ModelCall,
    USALIFinancials,
    VarianceReport,
)
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..budget import check_budget
from ..config import get_settings
from ..telemetry import trace_agent
from ..usali_rules import rules_as_prompt_block

logger = logging.getLogger(__name__)


# ─────────────────────── prompts ───────────────────────


SYSTEM_PROMPT = """You are Fondok's Analyst agent — a senior hotel
acquisitions analyst drafting an investment-committee memo for a
managing director's signature.

Your output is a structured ``InvestmentMemo`` with six required
sections, each grounded in cited source material:

  1. ``investment_thesis``   — Investment Thesis. Why this asset, why
     this market, why this hold. 200-400 words.
  2. ``market_analysis``     — Market Context. Submarket RevPAR,
     supply/demand, comp set, demand generators. 200-400 words.
  3. ``deal_overview``       — Property. Keys, brand, year built,
     PIP scope, in-place debt, fee/management structure. 150-300 words.
  4. ``financial_analysis``  — Financial Summary. Reconcile T-12 vs
     broker proforma. Cite engine outputs (IRR / equity multiple /
     DSCR / debt yield). 250-500 words.
  5. ``risk_factors``        — Risk Assessment. Surface every CRITICAL
     and WARN variance flag with the underwriter's view. 200-400 words.
  6. ``recommendation``      — Recommendation. Approve / Approve with
     Conditions / Decline / Refer Up + the headline conditions or
     reasons. 100-200 words.

Rules:

* Every section body MUST cite at least one source. A citation is a
  ``(document_id, page, field?, excerpt?)`` tuple pointing back to
  one of the input documents the orchestrator surfaces below. Use
  the document_id verbatim — never invent one.
* Never assert a number that isn't in the locked spread, the engine
  results, or the variance report. The spread is FROZEN.
* Tone: institutional. No marketing language. No hedging adjectives
  ("strong", "exciting", "robust") unless a number is right next to
  them.
* For each variance flag with severity CRITICAL or WARN, you MUST
  acknowledge it in either ``financial_analysis`` or ``risk_factors``
  by name. Do not paper over a 20% NOI variance.
* Output one structured ``InvestmentMemoEnvelope``. No prose outside
  the schema.
"""


# ─────────────────────── structured-output envelope ───────────────────────


_REQUIRED_SECTION_IDS: list[str] = [
    MemoSectionId.INVESTMENT_THESIS.value,
    MemoSectionId.MARKET_ANALYSIS.value,
    MemoSectionId.DEAL_OVERVIEW.value,
    MemoSectionId.FINANCIAL_ANALYSIS.value,
    MemoSectionId.RISK_FACTORS.value,
    MemoSectionId.RECOMMENDATION.value,
]


_VALID_SECTION_IDS: set[str] = {s.value for s in MemoSectionId}


class _CitationEnv(BaseModel):
    """Mirror of ``Citation`` — flat strings so the LLM tool schema
    stays simple, then projected via ``UUID(...)`` at validation."""

    model_config = ConfigDict(extra="forbid")

    document_id: Annotated[str, Field(min_length=1, max_length=80)]
    page: Annotated[int, Field(ge=1)]
    field: Annotated[str, Field(max_length=200)] | None = None
    excerpt: Annotated[str, Field(max_length=1000)] | None = None


class _MemoSectionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: Annotated[
        str,
        Field(
            description=(
                "One of: investment_thesis, market_analysis, deal_overview, "
                "financial_analysis, risk_factors, recommendation"
            )
        ),
    ]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    body: Annotated[str, Field(min_length=1)]
    citations: list[_CitationEnv] = Field(min_length=1)


class _InvestmentMemoEnvelope(BaseModel):
    """LLM-facing envelope mirroring ``InvestmentMemo``."""

    model_config = ConfigDict(extra="forbid")

    sections: list[_MemoSectionEnvelope] = Field(
        min_length=1,
        description="At least the six required sections.",
    )
    overall_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.85
    low_confidence_fields: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


# ─────────────────────── I/O contracts ───────────────────────


class AnalystSourceDocument(BaseModel):
    """A document made available to the Analyst as a citable source."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    doc_type: str | None = None
    page_count: Annotated[int, Field(ge=1)] = 1
    excerpts_by_page: dict[int, str] = Field(default_factory=dict)


class AnalystInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    deal_data: dict[str, Any] = Field(default_factory=dict)
    normalized_spread: USALIFinancials | None = None
    engine_results: dict[str, Any] = Field(default_factory=dict)
    variance_report: VarianceReport | None = None
    source_documents: list[AnalystSourceDocument] = Field(default_factory=list)


class AnalystOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    memo: InvestmentMemo | None = None
    success: bool = True
    error: str | None = None
    model_calls: list[ModelCall] = Field(default_factory=list)


# ─────────────────────── helpers ───────────────────────


def _to_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return uuid5(UUID("00000000-0000-0000-0000-000000000000"), value)


def _format_spread(spread: USALIFinancials) -> str:
    parts = [
        "=== LOCKED USALI SPREAD ===",
        f"Period: {spread.period_label}",
        f"Total Revenue: ${spread.total_revenue:,.0f}",
        f"  Rooms:         ${spread.rooms_revenue:,.0f}",
        f"  F&B:           ${spread.fb_revenue:,.0f}",
        f"  Other:         ${spread.other_revenue:,.0f}",
        f"Departmental:    ${spread.dept_expenses.total:,.0f}",
        f"Undistributed:   ${spread.undistributed.total:,.0f}",
        f"Mgmt Fee:        ${spread.mgmt_fee:,.0f}",
        f"FF&E Reserve:    ${spread.ffe_reserve:,.0f}",
        f"Fixed Charges:   ${spread.fixed_charges.total:,.0f}",
        f"GOP:             ${spread.gop:,.0f}",
        f"NOI:             ${spread.noi:,.0f}",
        f"OpEx Ratio:      {spread.opex_ratio:.2%}",
    ]
    if spread.occupancy is not None:
        parts.append(f"Occupancy:       {spread.occupancy:.1%}")
    if spread.adr is not None:
        parts.append(f"ADR:             ${spread.adr:,.2f}")
    if spread.revpar is not None:
        parts.append(f"RevPAR:          ${spread.revpar:,.2f}")
    return "\n".join(parts)


def _format_engines(engine_results: dict[str, Any]) -> str:
    if not engine_results:
        return "=== ENGINE RESULTS ===\n(no engine outputs supplied)"
    lines = ["=== ENGINE RESULTS ==="]
    for k, v in engine_results.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


def _format_variance(report: VarianceReport | None) -> str:
    if report is None or not report.flags:
        return "=== VARIANCE REPORT ===\n(no variance flags)"
    lines = ["=== VARIANCE REPORT ==="]
    for f in report.flags:
        note = (f.note or "").strip().split("\n", 1)[0]
        lines.append(
            f"- [{f.severity.value}] {f.field} actual={f.actual:,.2f} "
            f"broker={f.broker:,.2f} delta_pct={f.delta_pct} "
            f"rule={f.rule_id} — {note}"
        )
    return "\n".join(lines)


def _format_sources(docs: list[AnalystSourceDocument]) -> str:
    if not docs:
        return "=== SOURCE DOCUMENTS ===\n(no source documents — citations may be empty)"
    parts = ["=== SOURCE DOCUMENTS (cite by document_id + page) ==="]
    for d in docs:
        parts.append(
            f"\n--- document_id={d.document_id} ({d.doc_type or 'unspecified'}) ---"
        )
        parts.append(f"filename: {d.filename} ({d.page_count} pages)")
        for page in sorted(d.excerpts_by_page):
            text = d.excerpts_by_page[page]
            snippet = text if len(text) <= 1500 else text[:1500] + "…[truncated]"
            parts.append(f"\n[page {page}]\n{snippet}")
    return "\n".join(parts)


def _build_user_prompt(payload: AnalystInput) -> str:
    parts: list[str] = [
        f"tenant: {payload.tenant_id}",
        f"deal_id: {payload.deal_id}",
    ]
    if payload.deal_data:
        parts.append(f"deal_data: {payload.deal_data}")
    if payload.normalized_spread is not None:
        parts.append(_format_spread(payload.normalized_spread))
    parts.append(_format_engines(payload.engine_results))
    parts.append(_format_variance(payload.variance_report))
    parts.append(_format_sources(payload.source_documents))
    parts.append(
        "\nDraft the full investment memo now. Each of the six required "
        "sections must include at least one citation pointing back to one "
        "of the source documents above. Return one InvestmentMemoEnvelope."
    )
    return "\n\n".join(parts)


def _project_citations(
    cites: list[_CitationEnv], doc_ids: set[str]
) -> list[Citation]:
    out: list[Citation] = []
    for c in cites:
        # Drop citations that point at unknown documents.
        if doc_ids and c.document_id not in doc_ids:
            logger.debug(
                "analyst: dropping citation to unknown document_id=%s", c.document_id
            )
            continue
        try:
            out.append(
                Citation(
                    document_id=_to_uuid(c.document_id),
                    page=int(c.page),
                    field=c.field,
                    excerpt=c.excerpt,
                )
            )
        except (ValidationError, ValueError) as exc:
            logger.debug("analyst: dropping malformed citation (%s)", exc)
    return out


def _project_section(
    env: _MemoSectionEnvelope, *, doc_ids: set[str]
) -> MemoSection | None:
    if env.section_id not in _VALID_SECTION_IDS:
        # Best-effort coercion: allow the model to use a synonym for
        # the six required ids but never accept an unknown section.
        return None
    cites = _project_citations(env.citations, doc_ids)
    try:
        return MemoSection(
            section_id=MemoSectionId(env.section_id),
            title=env.title,
            body=env.body,
            citations=cites,
        )
    except ValidationError as exc:
        logger.warning("analyst: section %s failed validation: %s", env.section_id, exc)
        return None


def _ensure_required_sections(
    sections: list[MemoSection],
) -> list[MemoSection]:
    by_id: dict[str, MemoSection] = {s.section_id.value: s for s in sections}
    out: list[MemoSection] = []
    for sid in _REQUIRED_SECTION_IDS:
        if sid in by_id:
            out.append(by_id[sid])
    return out


# ─────────────────────── LLM client ───────────────────────


def _build_llm() -> Any:
    """Opus 4.7 with structured output bound to the full memo envelope."""
    from ..llm import build_structured_llm

    return build_structured_llm(
        role="analyst",
        schema=_InvestmentMemoEnvelope,
        max_tokens=16_384,
        timeout=300,
        temperature=None,  # Opus 4.7 rejects temperature; let llm.py drop it.
    )


def _build_section_llm() -> Any:
    """Per-section client — smaller max_tokens, one section at a time."""
    from ..llm import build_structured_llm

    return build_structured_llm(
        role="analyst",
        schema=_MemoSectionEnvelope,
        max_tokens=4096,
        timeout=180,
        temperature=None,
    )


async def _invoke_llm(
    llm: Any, messages: list[Any], usage: Any | None = None
) -> _InvestmentMemoEnvelope:
    config = {"callbacks": [usage]} if usage is not None else None
    raw = await llm.ainvoke(messages, config=config)
    if isinstance(raw, _InvestmentMemoEnvelope):
        return raw
    if isinstance(raw, BaseModel):
        return _InvestmentMemoEnvelope.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return _InvestmentMemoEnvelope.model_validate(raw)
    raise ValueError(f"Unexpected Analyst LLM return: {type(raw).__name__}")


async def _invoke_section_llm(
    llm: Any, messages: list[Any], usage: Any | None = None
) -> _MemoSectionEnvelope:
    config = {"callbacks": [usage]} if usage is not None else None
    raw = await llm.ainvoke(messages, config=config)
    if isinstance(raw, _MemoSectionEnvelope):
        return raw
    if isinstance(raw, BaseModel):
        return _MemoSectionEnvelope.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return _MemoSectionEnvelope.model_validate(raw)
    raise ValueError(f"Unexpected section LLM return: {type(raw).__name__}")


# ─────────────────────── memo builders ───────────────────────


def _make_confidence(
    sections: list[MemoSection], envelope_overall: float | None = None
) -> ConfidenceReport:
    cites_per_section = [len(s.citations) for s in sections]
    if cites_per_section:
        # Lightly penalize sections with weaker grounding.
        density = sum(min(c, 3) for c in cites_per_section) / (3 * len(sections))
    else:
        density = 0.0
    overall = (
        envelope_overall
        if envelope_overall is not None
        else max(0.5, min(1.0, 0.7 + 0.3 * density))
    )
    low_fields = [
        s.section_id.value for s, c in zip(sections, cites_per_section) if c == 0
    ]
    return ConfidenceReport(
        overall=overall,
        low_confidence_fields=low_fields,
        requires_human_review=overall < 0.85 or bool(low_fields),
    )


# ─────────────────────── single-shot path ───────────────────────


async def _run_analyst_single(payload: AnalystInput) -> AnalystOutput:
    started = datetime.now(UTC)
    t0 = time.monotonic()

    from ..llm import build_agent_system_blocks, cached_system_message_blocks
    from ..usage import UsageCapture

    # 4-block system prompt: agent instructions (uncached) + USALI rules
    # + brand catalog + schema addendum (all cached).
    system_blocks = build_agent_system_blocks(
        role="analyst",
        agent_instructions=SYSTEM_PROMPT,
    )
    rules_as_prompt_block()  # warm the catalog cache
    messages = [
        cached_system_message_blocks(system_blocks, role="analyst"),
        HumanMessage(content=_build_user_prompt(payload)),
    ]
    usage = UsageCapture()

    try:
        llm = _build_llm()
        envelope = await _invoke_llm(llm, messages, usage=usage)
    except (ValidationError, Exception) as exc:  # noqa: BLE001 - error path
        logger.warning("analyst: LLM call failed (%s)", exc)
        return AnalystOutput(
            deal_id=payload.deal_id,
            memo=None,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    doc_ids = {d.document_id for d in payload.source_documents}
    sections: list[MemoSection] = []
    for s_env in envelope.sections:
        proj = _project_section(s_env, doc_ids=doc_ids)
        if proj is not None:
            sections.append(proj)

    sections = _ensure_required_sections(sections)
    if not sections:
        return AnalystOutput(
            deal_id=payload.deal_id,
            memo=None,
            success=False,
            error="analyst: model emitted no valid required sections",
        )

    confidence = _make_confidence(sections, envelope.overall_confidence)
    memo = InvestmentMemo(
        deal_id=_to_uuid(payload.deal_id),
        sections=sections,
        generated_at=datetime.now(UTC),
        confidence=confidence,
        version=1,
    )

    completed = datetime.now(UTC)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    settings = get_settings()
    model_call = ModelCall(
        model=usage.model or settings.ANTHROPIC_ANALYST_MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=0.0,
        trace_id=payload.deal_id,
        started_at=started,
        completed_at=completed,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        agent_name="analyst",
    )
    logger.info(
        "analyst OK deal=%s sections=%d in %dms",
        payload.deal_id,
        len(sections),
        elapsed_ms,
    )
    return AnalystOutput(
        deal_id=payload.deal_id,
        memo=memo,
        success=True,
        model_calls=[model_call],
    )


# ─────────────────────── streaming path ───────────────────────


async def _draft_one_section(
    section_id: str,
    *,
    llm: Any,
    system_blocks: list[Any],
    context_block: str,
    usage: Any,
) -> _MemoSectionEnvelope | None:
    """Draft one section. Returns None on persistent failure."""
    from ..llm import cached_system_message_blocks

    base_messages: list[Any] = [
        cached_system_message_blocks(system_blocks, role="analyst"),
        HumanMessage(
            content=(
                f"{context_block}\n\n"
                f"Now draft section: **{section_id}**.\n\n"
                "Output exactly one MemoSection envelope with "
                f"section_id={section_id!r}. The body must include at "
                "least one citation pointing at a real source document."
            )
        ),
    ]
    messages = list(base_messages)
    for attempt in (1, 2):
        try:
            section = await _invoke_section_llm(llm, messages, usage=usage)
            if section.section_id != section_id:
                section = section.model_copy(update={"section_id": section_id})
            return section
        except (ValidationError, Exception) as exc:  # noqa: BLE001
            logger.warning(
                "analyst: section %s attempt %d failed (%s)", section_id, attempt, exc
            )
            if attempt == 2:
                return None
            messages.append(
                HumanMessage(
                    content=(
                        f"Your previous response failed validation: {exc}. "
                        f"Re-emit one MemoSection envelope for section "
                        f"{section_id!r} with at least one citation."
                    )
                )
            )
    return None  # unreachable


async def _run_analyst_streaming(payload: AnalystInput) -> AnalystOutput:
    """Per-section streaming variant. Same return shape as the single-shot path."""
    started = datetime.now(UTC)
    t0 = time.monotonic()

    from ..llm import build_agent_system_blocks
    from ..streaming.broadcast import DONE_SENTINEL, get_broadcast
    from ..usage import UsageCapture

    system_blocks = build_agent_system_blocks(
        role="analyst",
        agent_instructions=SYSTEM_PROMPT,
    )
    rules_as_prompt_block()  # warm the catalog cache
    context_block = _build_user_prompt(payload)
    broadcast = get_broadcast()
    section_llm = _build_section_llm()
    usage = UsageCapture()
    doc_ids = {d.document_id for d in payload.source_documents}

    drafted_sections: list[MemoSection] = []
    for section_id in _REQUIRED_SECTION_IDS:
        envelope = await _draft_one_section(
            section_id,
            llm=section_llm,
            system_blocks=system_blocks,
            context_block=context_block,
            usage=usage,
        )
        if envelope is None:
            continue
        proj = _project_section(envelope, doc_ids=doc_ids)
        if proj is None:
            continue
        drafted_sections.append(proj)
        try:
            await broadcast.publish(
                f"memo:{payload.deal_id}",
                {
                    "event": "section",
                    "data": proj.model_dump(mode="json"),
                    "metadata": {
                        "input_tokens": usage.input_tokens,
                        "output_tokens": usage.output_tokens,
                        "model": usage.model,
                        "section_index": len(drafted_sections),
                        "section_total": len(_REQUIRED_SECTION_IDS),
                    },
                },
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("analyst: broadcast publish failed (%s)", exc)

    if not drafted_sections:
        return AnalystOutput(
            deal_id=payload.deal_id,
            memo=None,
            success=False,
            error="analyst: streaming path produced no sections",
        )

    confidence = _make_confidence(drafted_sections)
    memo = InvestmentMemo(
        deal_id=_to_uuid(payload.deal_id),
        sections=_ensure_required_sections(drafted_sections),
        generated_at=datetime.now(UTC),
        confidence=confidence,
        version=1,
    )

    try:
        await broadcast.publish(
            f"memo:{payload.deal_id}",
            {
                "event": DONE_SENTINEL,
                "data": {"sections": len(memo.sections)},
                "metadata": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "model": usage.model,
                },
            },
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("analyst: final broadcast failed (%s)", exc)

    completed = datetime.now(UTC)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    settings = get_settings()
    model_call = ModelCall(
        model=usage.model or settings.ANTHROPIC_ANALYST_MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=0.0,
        trace_id=payload.deal_id,
        started_at=started,
        completed_at=completed,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        agent_name="analyst",
    )
    logger.info(
        "analyst (streaming) OK deal=%s sections=%d in %dms",
        payload.deal_id,
        len(memo.sections),
        elapsed_ms,
    )
    return AnalystOutput(
        deal_id=payload.deal_id,
        memo=memo,
        success=True,
        model_calls=[model_call],
    )


# ─────────────────────── public entry point ───────────────────────


@trace_agent("Analyst")
async def run_analyst(payload: AnalystInput) -> AnalystOutput:
    """Draft an InvestmentMemo. Picks streaming vs single-shot per settings."""
    # Backwards-compatible no-op path for the graph stub when nothing
    # has been computed yet.
    if payload.normalized_spread is None and not payload.source_documents:
        logger.info(
            "analyst: insufficient context (deal=%s) — empty memo", payload.deal_id
        )
        return AnalystOutput(
            deal_id=payload.deal_id,
            memo=None,
            success=True,
            model_calls=[],
        )

    try:
        check_budget(
            {"deal_id": payload.deal_id, "model_calls": []}, stage="analyst"
        )
    except Exception as exc:
        logger.warning("analyst: budget check raised (%s)", exc)
        return AnalystOutput(
            deal_id=payload.deal_id,
            memo=None,
            success=False,
            error=str(exc),
        )

    settings = get_settings()
    if settings.MEMO_STREAMING_ENABLED:
        return await _run_analyst_streaming(payload)
    return await _run_analyst_single(payload)


# Convenience helper for tests / future API endpoints that want to
# force a particular path independently of the env flag.
async def run_analyst_streaming(payload: AnalystInput) -> AnalystOutput:
    """Force the per-section streaming variant regardless of the env flag."""
    return await _run_analyst_streaming(payload)


# Helper used by the IC builder when no real deal_uuid is supplied.
def memo_today() -> date:
    """Today, in UTC. Indirected so tests can monkeypatch."""
    return datetime.now(UTC).date()


__all__ = [
    "AnalystInput",
    "AnalystOutput",
    "AnalystSourceDocument",
    "memo_today",
    "run_analyst",
    "run_analyst_streaming",
]
