"""Router agent — classifies an incoming document into a hotel-deal lane.

Calls Claude Haiku 4.5 (cheap, fast classification) to map a filename +
content sample onto one of the canonical ``DocType`` values plus a
confidence score and a one-line rationale.

Backwards compatibility
-----------------------
The earlier stub accepted only ``tenant_id`` / ``deal_id`` and returned
a hard-coded ``route="extractor"``. The graph still calls it that way
during the warm-up boot path; when no ``filename``/``content_sample`` is
supplied we skip the LLM call and return a deterministic default. As
soon as the Extractor produces a payload the Router runs the real
classifier and emits a typed ``DocType`` decision.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fondok_schemas import DocType, ModelCall
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..budget import check_budget
from ..config import get_settings
from ..telemetry import trace_agent

logger = logging.getLogger(__name__)


# ─────────────────────── prompt ───────────────────────


SYSTEM_PROMPT = """You are Fondok's Router agent — a hotel real-estate
underwriter who classifies an incoming document into one lane of the
deal pipeline.

Available document types (use exactly one of these tokens):

- OM             — Offering Memorandum / sales teaser. Broker proforma,
                   asking price, market overview, comparable sales.
- T12            — Trailing-twelve-month profit-and-loss statement.
                   Monthly revenue, departmental expenses, NOI lines.
- STR            — STR/STAR market report. Subject vs. comp set
                   indices (MPI/ARI/RGI), occupancy, ADR, RevPAR.
- RENT_ROLL      — Multifamily/extended-stay tenant roster (rare for
                   hotels; surfaces with mixed-use deals).
- PNL            — Generic profit-and-loss statement that is NOT a
                   12-month rollup (annual budget, monthly forecast).
- MARKET_STUDY   — Third-party market/feasibility study; demand
                   generators, supply pipeline, ADR/RevPAR forecasts.
- CONTRACT       — Purchase-and-sale, franchise, management, or other
                   binding agreement.
- UNKNOWN        — Cannot confidently classify; downstream HITL gate.

Rules:
1. Return exactly one ``doc_type`` from the list above.
2. ``confidence`` is your self-assessed certainty in [0, 1]. If the
   filename and the content sample disagree, prefer the content and
   lower confidence. Set ``confidence < 0.7`` whenever you fall back
   to ``UNKNOWN``.
3. ``reasoning`` is one short sentence — the underwriter scanning a
   queue should know in five seconds why you chose this lane.
4. Never invent a ``doc_type`` that isn't in the list. If nothing
   fits, emit ``UNKNOWN``.

Output format: one structured RouterDecision envelope. No prose
outside the schema.
"""


# ─────────────────────── structured-output envelope ───────────────────────


_VALID_DOC_TYPES = {dt.value for dt in DocType} | {"UNKNOWN"}


class _RouterDecision(BaseModel):
    """LLM-facing envelope mirroring the Router's classification."""

    model_config = ConfigDict(extra="forbid")

    doc_type: Annotated[
        str,
        Field(
            description=(
                "One of: OM, T12, STR, RENT_ROLL, PNL, MARKET_STUDY, "
                "CONTRACT, UNKNOWN"
            )
        ),
    ]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    reasoning: Annotated[str, Field(min_length=1, max_length=400)]


# ─────────────────────── I/O contracts ───────────────────────


class RouterInput(BaseModel):
    """Inputs to the Router.

    ``filename`` and ``content_sample`` are optional so the existing
    graph path (which only knows ``tenant_id`` / ``deal_id``) keeps
    working; supplying them flips on the real LLM classifier.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    document_id: str | None = None
    filename: str | None = Field(default=None, max_length=500)
    content_sample: str | None = Field(
        default=None,
        description="First ~2000 chars of the document; truncated if larger.",
    )
    hint: str | None = Field(
        default=None, description="Optional caller hint (e.g. 'STR', 'P&L')."
    )


class RouterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    document_id: str | None = None
    doc_type: str | None = Field(
        default=None,
        description="Canonical DocType value or 'UNKNOWN'.",
    )
    confidence: float = 0.0
    rationale: str = ""
    # Legacy field kept for backwards compatibility with the graph node.
    route: str = Field(default="extractor")
    success: bool = True
    error: str | None = None
    model_calls: list[ModelCall] = Field(default_factory=list)


# ─────────────────────── helpers ───────────────────────


def _coerce_doc_type(raw: str) -> str:
    """Normalize the LLM's free-form answer onto our enum."""
    s = (raw or "").strip().upper()
    s = s.replace("-", "_").replace(" ", "_")
    # Common synonyms the model occasionally emits.
    aliases = {
        "OFFERING_MEMORANDUM": "OM",
        "TEASER": "OM",
        "TRAILING_TWELVE": "T12",
        "T_12": "T12",
        "T-12": "T12",
        "STR_REPORT": "STR",
        "STAR_REPORT": "STR",
        "P_AND_L": "PNL",
        "P_L": "PNL",
        "INCOME_STATEMENT": "PNL",
        "MARKET_REPORT": "MARKET_STUDY",
        "FEASIBILITY_STUDY": "MARKET_STUDY",
    }
    s = aliases.get(s, s)
    if s in _VALID_DOC_TYPES:
        return s
    return "UNKNOWN"


def _build_user_prompt(payload: RouterInput) -> str:
    parts: list[str] = [f"deal_id: {payload.deal_id}"]
    if payload.document_id:
        parts.append(f"document_id: {payload.document_id}")
    parts.append(f"filename: {payload.filename or '<none provided>'}")
    if payload.hint:
        parts.append(f"caller hint: {payload.hint}")
    sample = (payload.content_sample or "").strip()
    if sample:
        if len(sample) > 2000:
            sample = sample[:2000] + "\n…[truncated]"
        parts.extend(["", "=== CONTENT SAMPLE ===", sample])
    parts.append("\nClassify this document. Return one RouterDecision envelope.")
    return "\n".join(parts)


# ─────────────────────── LLM client (overridable for tests) ───────────────────────


def _build_llm() -> Any:
    """Construct the Router's structured-output client (Haiku)."""
    from ..llm import build_structured_llm

    return build_structured_llm(
        role="router",
        schema=_RouterDecision,
        max_tokens=512,
        timeout=30,
        temperature=0.0,
    )


async def _invoke_llm(
    llm: Any, messages: list[Any], usage: Any | None = None
) -> _RouterDecision:
    config = {"callbacks": [usage]} if usage is not None else None
    raw = await llm.ainvoke(messages, config=config)
    if isinstance(raw, _RouterDecision):
        return raw
    if isinstance(raw, BaseModel):
        return _RouterDecision.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return _RouterDecision.model_validate(raw)
    raise ValueError(f"Unexpected Router LLM return: {type(raw).__name__}")


# ─────────────────────── public agent entry point ───────────────────────


@trace_agent("Router")
async def run_router(payload: RouterInput) -> RouterOutput:
    """Classify a document into one of the canonical hotel-deal lanes."""
    started = datetime.now(UTC)
    t0 = time.monotonic()

    # Backwards-compatible fast path: when no document context is
    # provided we skip the LLM call and return a deterministic default
    # so the existing graph boot still works.
    if not (payload.filename or payload.content_sample):
        logger.info(
            "router: no document context provided (deal=%s) — default route",
            payload.deal_id,
        )
        return RouterOutput(
            deal_id=payload.deal_id,
            document_id=payload.document_id,
            doc_type=None,
            confidence=0.0,
            rationale="no document context provided; default route",
            route="extractor",
            success=True,
            model_calls=[],
        )

    # Budget pre-flight — pricing is cheap (Haiku) but we still check.
    try:
        check_budget({"deal_id": payload.deal_id, "model_calls": []}, stage="router")
    except Exception as exc:
        logger.warning("router: budget check raised: %s", exc)
        return RouterOutput(
            deal_id=payload.deal_id,
            document_id=payload.document_id,
            doc_type=None,
            confidence=0.0,
            rationale="budget exceeded before router",
            route="extractor",
            success=False,
            error=str(exc),
        )

    from ..llm import cached_system_message_blocks
    from ..usage import UsageCapture

    messages = [
        cached_system_message_blocks([SYSTEM_PROMPT], role="router"),
        HumanMessage(content=_build_user_prompt(payload)),
    ]
    usage = UsageCapture()

    try:
        llm = _build_llm()
        decision = await _invoke_llm(llm, messages, usage=usage)
    except (ValidationError, Exception) as exc:  # noqa: BLE001 - error path
        logger.warning("router: LLM call failed (%s)", exc)
        return RouterOutput(
            deal_id=payload.deal_id,
            document_id=payload.document_id,
            doc_type="UNKNOWN",
            confidence=0.0,
            rationale=f"router LLM error: {type(exc).__name__}",
            route="extractor",
            success=False,
            error=str(exc),
        )

    doc_type = _coerce_doc_type(decision.doc_type)
    completed = datetime.now(UTC)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "router OK deal=%s doc_type=%s confidence=%.2f in %dms",
        payload.deal_id,
        doc_type,
        decision.confidence,
        elapsed_ms,
    )

    settings = get_settings()
    model_call = ModelCall(
        model=usage.model or settings.ANTHROPIC_ROUTER_MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=0.0,
        trace_id=payload.deal_id,
        started_at=started,
        completed_at=completed,
    )

    return RouterOutput(
        deal_id=payload.deal_id,
        document_id=payload.document_id,
        doc_type=doc_type,
        confidence=decision.confidence,
        rationale=decision.reasoning,
        route="extractor",
        success=True,
        model_calls=[model_call],
    )


__all__ = ["RouterInput", "RouterOutput", "run_router"]
