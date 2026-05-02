"""Researcher agent — grounded Q&A over a deal's Context Data Product.

Takes a ``DealDossier`` + a free-form question, returns a single
grounded answer with citations back to the source documents. Built
to support institutional reviewers asking deal-specific questions
("what's the broker's stabilized NOI vs my T-12 actuals?", "which
expense lines did the engine ratio-synthesize vs read from T-12?").

Sits next to the Analyst — same Opus 4.7 backbone, same citation
shape, but optimized for one-question-at-a-time interactive use
instead of full memo drafting.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid5

from fondok_schemas import ModelCall
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..budget import check_budget
from ..dossier import DealDossier
from ..telemetry import trace_agent
from ..usali_rules import rules_as_prompt_block

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are Fondok's Researcher agent — a senior hotel
acquisitions analyst answering deal-specific questions for an
institutional underwriter.

You are given (a) the deal's full Context Data Product as a structured
dossier (deal metadata, document inventory with per-page excerpts,
extracted financial fields, latest engine outputs, variance flags,
confidence rollups), and (b) one specific question from the underwriter.

Your job: produce ONE grounded, institutional-tone answer in plain
English (3-12 sentences typically) with at least one citation back
to a source document + page when the answer rests on extracted data.

Rules:

* Use only values present in the dossier — never invent numbers. If
  the dossier doesn't carry the data needed to answer, say so
  explicitly and point to what document type would close the gap.
* Citations are ``(document_id, page, field?, excerpt?)`` tuples
  pointing back to one of the dossier's documents. Use the
  ``document_id`` verbatim from the dossier; never invent one.
* Acknowledge variance when the question touches a flagged field —
  e.g. if the underwriter asks "what's the broker NOI" and the
  dossier carries a variance flag on NOI, surface the broker number,
  the T-12 actual, and the delta together.
* Tone: institutional. No marketing language, no hedging adjectives
  unless a number is right next to them.
* When the question is ambiguous (e.g. "what's NOI" — Year 1?
  stabilized? T-12 actual?), pick the most useful interpretation
  and state which one you answered.
* ``confidence`` (0..1) reflects how grounded your answer is in
  the dossier. 0.9+ = directly cited from extracted fields; 0.7 =
  derived from extracted data; 0.5 = inferred from partial data;
  <0.5 = guessing because the dossier is sparse — say so plainly.

Output: one structured ``ResearcherAnswer``. No prose outside the
schema.
"""


# ─────────────────────── envelopes ───────────────────────


class _CitationEnv(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: Annotated[str, Field(min_length=1, max_length=80)]
    page: Annotated[int, Field(ge=1)] | None = None
    field: Annotated[str, Field(max_length=200)] | None = None
    excerpt: Annotated[str, Field(max_length=1000)] | None = None


class _ResearcherAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: Annotated[str, Field(min_length=1, max_length=4000)]
    citations: list[_CitationEnv] = Field(default_factory=list)
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.85
    note: Annotated[str, Field(max_length=500)] | None = None


class ResearcherCitation(BaseModel):
    """Public citation shape returned to the API layer."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    page: int | None = None
    field: str | None = None
    excerpt: str | None = None


class ResearcherInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    dossier: DealDossier


class ResearcherOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    answer: str
    citations: list[ResearcherCitation] = Field(default_factory=list)
    confidence: float = 0.0
    note: str | None = None
    success: bool = True
    error: str | None = None
    model_calls: list[ModelCall] = Field(default_factory=list)


# ─────────────────────── prompt formatting ───────────────────────


def _format_dossier_for_prompt(d: DealDossier) -> str:
    """Render the dossier as a compact text block the LLM can scan.

    Trims per-page excerpts to keep the prompt manageable; full text
    is still in the dossier object if the agent needs deeper context.
    """
    parts: list[str] = ["=== DEAL CONTEXT DATA PRODUCT ==="]

    if d.deal:
        parts.append("--- Property Metadata ---")
        for k in (
            "name",
            "city",
            "brand",
            "service",
            "keys",
            "deal_stage",
            "return_profile",
            "purchase_price",
            "status",
        ):
            if k in d.deal and d.deal[k] not in (None, ""):
                parts.append(f"  {k}: {d.deal[k]}")

    if d.documents:
        parts.append("\n--- Document Inventory ---")
        for doc in d.documents:
            line = (
                f"  doc_id={doc.document_id} type={doc.doc_type or '—'} "
                f"filename={doc.filename!r} status={doc.status} "
                f"pages={doc.page_count or '—'} fields_extracted={doc.field_count}"
            )
            if doc.overall_confidence is not None:
                line += f" overall_confidence={doc.overall_confidence:.2f}"
            parts.append(line)

    if d.spread_actuals:
        parts.append("\n--- T-12 Actuals (USALI normalized) ---")
        parts.append(_format_spread(d.spread_actuals))
    if d.spread_broker:
        parts.append("\n--- Broker Pro Forma (USALI normalized) ---")
        parts.append(_format_spread(d.spread_broker))

    if d.engines:
        parts.append("\n--- Engine Outputs ---")
        for e in d.engines:
            parts.append(f"  {e.name} ({e.status}): {e.summary}")
            if e.outputs:
                for k, v in list(e.outputs.items())[:8]:
                    parts.append(f"    {k}: {v}")

    if d.variance:
        parts.append("\n--- Broker vs T-12 Variance Flags ---")
        for v in d.variance:
            parts.append(
                f"  [{v.severity}] {v.field} actual={v.actual} broker={v.broker} "
                f"delta_pct={v.delta_pct} rule={v.rule_id or '—'}"
            )

    parts.append("\n--- Confidence Rollup ---")
    parts.append(
        f"  avg_field_confidence={d.confidence.avg_field_confidence:.2f} "
        f"extracted_field_count={d.confidence.extracted_field_count} "
        f"docs_extracted={d.confidence.docs_extracted}/{d.confidence.docs_total} "
        f"variance_critical={d.confidence.variance_critical_count} "
        f"warn={d.confidence.variance_warn_count}"
    )

    if d.documents:
        parts.append("\n--- Per-Page Document Excerpts ---")
        for doc in d.documents:
            for page, text in doc.excerpts_by_page.items():
                snippet = text if len(text) <= 800 else text[:800] + "…[truncated]"
                parts.append(
                    f"  [doc={doc.document_id} page={page}]\n  {snippet}\n"
                )

    return "\n".join(parts)


def _format_spread(spread: dict[str, Any]) -> str:
    keys = (
        "period_label",
        "rooms_revenue",
        "fb_revenue",
        "resort_fees",
        "other_revenue",
        "total_revenue",
        "gop",
        "noi",
        "opex_ratio",
        "occupancy",
        "adr",
        "revpar",
    )
    lines = []
    for k in keys:
        v = spread.get(k)
        if v is None:
            continue
        if isinstance(v, float):
            lines.append(f"  {k}: {v:,.2f}" if k in ("opex_ratio", "occupancy") else f"  {k}: {v:,.0f}")
        else:
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _build_user_prompt(payload: ResearcherInput) -> str:
    return (
        _format_dossier_for_prompt(payload.dossier)
        + "\n\n=== QUESTION ===\n"
        + payload.question.strip()
        + "\n\nAnswer the question now using only the dossier above. "
        "Return one ResearcherAnswer."
    )


# ─────────────────────── agent runner ───────────────────────


def _to_uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return uuid5(UUID("00000000-0000-0000-0000-000000000000"), value)


def _project_citations(
    cites: list[_CitationEnv], doc_ids: set[str]
) -> list[ResearcherCitation]:
    out: list[ResearcherCitation] = []
    for c in cites:
        # Drop citations that point at unknown documents.
        if doc_ids and c.document_id not in doc_ids:
            logger.debug(
                "researcher: dropping citation to unknown document_id=%s",
                c.document_id,
            )
            continue
        try:
            out.append(
                ResearcherCitation(
                    document_id=c.document_id,
                    page=c.page,
                    field=c.field,
                    excerpt=c.excerpt,
                )
            )
        except (ValidationError, ValueError) as exc:
            logger.debug("researcher: dropping malformed citation (%s)", exc)
    return out


@trace_agent("Researcher")
async def run_researcher(payload: ResearcherInput) -> ResearcherOutput:
    started = datetime.now(UTC)
    t0 = time.monotonic()

    try:
        check_budget(
            {"deal_id": payload.deal_id, "model_calls": []},
            stage="researcher",
        )
    except Exception as exc:
        logger.warning("researcher: budget check raised: %s", exc)
        return ResearcherOutput(
            deal_id=payload.deal_id,
            answer="",
            citations=[],
            confidence=0.0,
            note=f"budget exceeded: {exc}",
            success=False,
            error=str(exc),
        )

    from ..llm import (
        build_agent_system_blocks,
        build_structured_llm,
        cached_system_message_blocks,
    )
    from ..usage import UsageCapture

    system_blocks = build_agent_system_blocks(
        role="analyst",
        agent_instructions=SYSTEM_PROMPT,
    )
    rules_as_prompt_block()  # warm the catalog cache

    llm = build_structured_llm(
        role="analyst",
        schema=_ResearcherAnswer,
        max_tokens=2048,
        timeout=120,
        temperature=None,
    )

    messages = [
        cached_system_message_blocks(system_blocks, role="analyst"),
        HumanMessage(content=_build_user_prompt(payload)),
    ]

    usage = UsageCapture()
    try:
        raw = await llm.ainvoke(messages, config={"callbacks": [usage]})
    except Exception as exc:  # noqa: BLE001
        # Surface the actual error body for debugging — Anthropic's
        # BadRequestError carries a structured ``message`` that explains
        # exactly why the request was rejected (token limit, schema
        # mismatch, malformed content block, etc).
        detail = getattr(exc, "message", None) or str(exc)
        body_attr = getattr(exc, "body", None)
        if body_attr is not None:
            detail = f"{detail} | body={body_attr}"
        logger.exception(
            "researcher: LLM call failed for deal=%s detail=%s",
            payload.deal_id,
            detail,
        )
        return ResearcherOutput(
            deal_id=payload.deal_id,
            answer="",
            citations=[],
            confidence=0.0,
            note=f"LLM call failed: {type(exc).__name__}: {detail}"[:500],
            success=False,
            error=str(exc),
        )

    if isinstance(raw, _ResearcherAnswer):
        envelope = raw
    elif isinstance(raw, BaseModel):
        envelope = _ResearcherAnswer.model_validate(raw.model_dump())
    elif isinstance(raw, dict):
        envelope = _ResearcherAnswer.model_validate(raw)
    else:
        return ResearcherOutput(
            deal_id=payload.deal_id,
            answer="",
            citations=[],
            confidence=0.0,
            note=f"unexpected LLM return type: {type(raw).__name__}",
            success=False,
            error="bad-llm-shape",
        )

    doc_ids = {d.document_id for d in payload.dossier.documents}
    citations = _project_citations(envelope.citations, doc_ids)
    runtime_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "researcher: deal=%s answered in %dms (chars=%d cites=%d conf=%.2f)",
        payload.deal_id,
        runtime_ms,
        len(envelope.answer),
        len(citations),
        envelope.confidence,
    )

    model_calls = list(usage.model_calls) if hasattr(usage, "model_calls") else []
    _ = (started, json)  # keep linter happy on unused imports if usage trims
    return ResearcherOutput(
        deal_id=payload.deal_id,
        answer=envelope.answer,
        citations=citations,
        confidence=envelope.confidence,
        note=envelope.note,
        success=True,
        model_calls=model_calls,
    )


__all__ = [
    "ResearcherCitation",
    "ResearcherInput",
    "ResearcherOutput",
    "run_researcher",
    "SYSTEM_PROMPT",
]
