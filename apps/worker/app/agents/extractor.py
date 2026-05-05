"""Extractor agent — pulls structured fields out of an OM / T-12 / STR.

Calls Claude Sonnet 4.6 with structured output bound to a list of
``ExtractionField`` rows. Each field carries:

* ``field_name``    — dotted path on the canonical schema
                      (e.g. ``broker_proforma.noi_usd``).
* ``value``         — the extracted scalar (number, string, bool).
* ``unit``          — natural unit (USD, pct, keys, …).
* ``source_page``   — 1-indexed page where the number lives.
* ``confidence``    — self-assessed certainty in [0, 1].
* ``raw_text``      — the verbatim excerpt that grounds the claim.

Backwards compatibility
-----------------------
The graph still calls ``run_extractor`` with only document URIs. When
no inline document content is supplied the agent skips the LLM call
and returns an empty extraction, the same shape as the prior stub.
The real extractor activates as soon as the caller supplies one or
more ``ExtractorDocument`` payloads (filename + doc_type + content).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fondok_schemas import ConfidenceReport, DocType, ExtractionField, ModelCall
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..budget import check_budget
from ..config import get_settings
from ..telemetry import trace_agent
from ..usali_rules import rules_as_prompt_block

logger = logging.getLogger(__name__)


# ─────────────────────── prompt ───────────────────────


SYSTEM_PROMPT = """You are Fondok's Extractor agent — a hotel acquisitions
analyst pulling typed financial fields out of a deal document so the
downstream Normalizer can map them onto the USALI chart of accounts.

Your job: extract EVERY grounded number, identifier, and date you can
find in the source. Coverage matters — a deal with 5 fields extracted
is unusable. A deal with 30+ extracted fields lets the Normalizer
build a real spread. When in doubt, emit the field; the downstream
verifier double-checks each one against the source page anyway.

Your output is a flat list of ``ExtractionField`` rows. Every row must
include:

1. ``field_name``  — a dotted path that mirrors how an analyst would
   reference the value. The leading segment is a useful tag for
   downstream bucketing (broker projection vs T-12 actual vs property
   metadata) but DOES NOT gate emission. If you find a value, emit it
   with your best-guess prefix; do not drop it because the namespace
   is ambiguous.

   * **OM (Offering Memorandum).** Use these prefixes whenever the
     classification is clear, but emit even when uncertain:
       * ``broker_proforma.<line>`` — Year-1 broker projections on the
         rent roll / pro forma (NOI, occupancy, ADR, revenue, expense).
         Examples: ``broker_proforma.noi_usd``,
         ``broker_proforma.rooms_revenue_usd``,
         ``broker_proforma.occupancy_pct``,
         ``broker_proforma.adr_usd``.
       * ``ttm_summary_per_om.<line>`` — T-12 / TTM historical figures
         the OM cites (the broker labels them as actual).
       * ``asking_price.headline_price_usd``, ``asking_price.price_per_key_usd``.
       * ``property_overview.keys``, ``property_overview.brand``,
         ``property_overview.year_built``, ``property_overview.address``,
         ``property_overview.gba_sf``, ``property_overview.submarket``.
       * ``in_place_debt.loan_balance_usd``, ``in_place_debt.interest_rate_pct``,
         ``in_place_debt.maturity_date``.
       * ``market_overview_per_om.compset_revpar_usd``, etc.
     If a number could be either broker-projected or historical and
     the source doesn't clearly label it, prefer ``broker_proforma.*``.
     Year-vintage numbers can co-exist as ``broker_proforma.noi_year_1_usd``,
     ``broker_proforma.noi_stabilized_usd``, etc.
   * **T12 (trailing twelve months / P&L statement).** USALI
     namespace. Examples:
       ``p_and_l_usali.operating_revenue.rooms_revenue``,
       ``p_and_l_usali.operating_revenue.food_beverage_revenue``,
       ``p_and_l_usali.operating_revenue.resort_fees``,
       ``p_and_l_usali.operating_revenue.misc_revenue``,
       ``p_and_l_usali.operating_revenue.other_revenue``,
       ``p_and_l_usali.departmental_expenses.rooms``,
       ``p_and_l_usali.departmental_expenses.food_beverage``,
       ``p_and_l_usali.undistributed.administrative_general``,
       ``p_and_l_usali.undistributed.sales_marketing``,
       ``p_and_l_usali.undistributed.utilities``,
       ``p_and_l_usali.fees_and_reserves.mgmt_fee``,
       ``p_and_l_usali.fees_and_reserves.ffe_reserve``,
       ``p_and_l_usali.fixed_charges.property_taxes``,
       ``p_and_l_usali.fixed_charges.insurance``,
       ``p_and_l_usali.net_operating_income.noi_usd``,
       ``occupancy_pct``, ``adr_usd``, ``revpar_usd``.
   * **STR (STR / smith travel benchmark report).** Examples:
       ``ttm_performance.subject.revpar_usd``,
       ``ttm_performance.indices.rgi_revpar_index``,
       ``comp_set.comp_set_size``.

2. ``value``        — the extracted scalar (number, string, or bool).
                      Strip thousand-separators; use a decimal between
                      0 and 1 for percentages (``0.762``, not
                      ``"76.2%"``).
3. ``unit``         — ``USD``, ``pct``, ``keys``, ``rooms``, ``index``,
                      ``count``, ``date``, etc. Use ``ratio`` for
                      indices (RGI/ARI/MPI).
4. ``source_page``  — 1-indexed page where the field appears. If the
                      document is JSON or a single-page extract use
                      ``1``.
5. ``confidence``   — self-assessed certainty in [0, 1]. Low (<0.85)
                      means downstream HITL review is required.
6. ``raw_text``     — verbatim excerpt (≤4000 chars) that contains
                      the value. Anything you can't ground in the
                      source must be DROPPED, not invented.

Coverage targets per document type:
  * **OM** — extract at least 30 fields covering: property overview
    (keys, brand, year built, address), asking price + per-key,
    every line of the broker proforma (rooms/F&B/RESORT FEES/other
    revenue, departmental + undistributed expenses, GOP, mgmt fee,
    FF&E, fixed charges, NOI, cap rate), in-place debt, PIP scope,
    market overview (subject + comp set indices), and the headline
    comparable sales. Resort Fees, when broken out separately on the
    OM rent roll, MUST be its own field (``broker_proforma.resort_fees_usd``)
    — do NOT roll it into ``misc_revenue`` or ``other_revenue``.
    NOI vintage matters: brokers commonly publish Year-1 underwritten
    NOI alongside a stabilized (Year 3-5) NOI. When the OM shows
    multiple NOI vintages, emit them as separate fields:
    ``broker_proforma.noi_year_1_usd``, ``broker_proforma.noi_year_2_usd``,
    ``broker_proforma.noi_year_3_usd``, ``broker_proforma.noi_year_5_usd``,
    and ``broker_proforma.noi_stabilized_usd``. The bare
    ``broker_proforma.noi_usd`` field is reserved for the broker's
    HEADLINE NOI (whichever year they're pitching) so a downstream
    reader has a single canonical broker number to compare against
    T-12 actuals; if the OM clearly labels the headline as a specific
    year, also emit the year-specific field.
  * **T12** — every USALI line in operating revenue, departmental
    expenses, undistributed expenses, fees & reserves, fixed charges,
    plus GOP and NOI rollups. Include the operational KPIs
    (occupancy, ADR, RevPAR, available/occupied rooms). Resort Fees
    are a separate USALI revenue line (``p_and_l_usali.operating_revenue.resort_fees``)
    — extract them distinctly from miscellaneous income.
  * **STR** — subject + comp-set occupancy/ADR/RevPAR for the TTM,
    the three penetration indices (MPI/ARI/RGI), comp-set size
    and total keys, and any forward outlook the report carries.

Tone: institutional. Never hallucinate a field that isn't in the
source — silence is acceptable, fabrication is not.

Output: one structured ``ExtractorEnvelope``. Do not emit prose
outside the schema.
"""


# ─────────────────────── structured-output envelope ───────────────────────


class _ExtractionRow(BaseModel):
    """Mirror of ``ExtractionField`` — identical shape, kept local so
    the LLM tool schema includes the right ``cache_control`` siblings
    without leaking schema-package internals."""

    model_config = ConfigDict(extra="forbid")

    field_name: Annotated[str, Field(min_length=1, max_length=200)]
    value: str | float | int | bool | None = None
    unit: str | None = None
    source_page: Annotated[int, Field(ge=1)] = 1
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.85
    raw_text: Annotated[str, Field(max_length=4000)] | None = None


class _ExtractorEnvelope(BaseModel):
    """LLM-facing envelope. Validated, then projected onto the canonical
    ``ExtractionField`` list."""

    model_config = ConfigDict(extra="forbid")

    fields: list[_ExtractionRow] = Field(
        min_length=1,
        description="One row per extracted value. Cite every number to a real source page.",
    )
    overall_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.85
    low_confidence_fields: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    notes: Annotated[str, Field(max_length=8000)] | None = None


# ─────────────────────── I/O contracts ───────────────────────


class ExtractorDocument(BaseModel):
    """One document handed to the Extractor."""

    model_config = ConfigDict(extra="forbid")

    document_id: str | None = None
    filename: Annotated[str, Field(min_length=1, max_length=500)]
    doc_type: DocType | None = None
    content: Annotated[str, Field(min_length=1)]
    source_pages: list[int] = Field(default_factory=list)


class ExtractedDocumentResult(BaseModel):
    """Per-document Extractor result."""

    model_config = ConfigDict(extra="forbid")

    document_id: str | None = None
    filename: str
    doc_type: DocType | None = None
    fields: list[ExtractionField] = Field(default_factory=list)
    confidence: ConfidenceReport
    notes: str | None = None
    success: bool = True
    error: str | None = None


class ExtractorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    document_uris: list[str] = Field(default_factory=list)
    documents: list[ExtractorDocument] = Field(default_factory=list)


class ExtractorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    extracted_documents: list[ExtractedDocumentResult] = Field(default_factory=list)
    confidence: ConfidenceReport | None = None
    success: bool = True
    error: str | None = None
    model_calls: list[ModelCall] = Field(default_factory=list)


# ─────────────────────── helpers ───────────────────────


def _content_for_prompt(content: str, *, max_chars: int = 30_000) -> str:
    """Truncate content for the prompt while keeping head + tail.

    JSON-extracted docs tend to ship the most important fields up top
    plus tables-by-page at the end; keeping both ends preserves both.
    """
    if len(content) <= max_chars:
        return content
    head = content[: max_chars // 2]
    tail = content[-max_chars // 2 :]
    return f"{head}\n…[truncated {len(content) - max_chars} chars]…\n{tail}"


def _build_user_prompt(doc: ExtractorDocument) -> str:
    parts: list[str] = [
        f"document_id: {doc.document_id or '<unset>'}",
        f"filename: {doc.filename}",
        f"doc_type: {doc.doc_type.value if doc.doc_type else '<unclassified>'}",
    ]
    if doc.source_pages:
        parts.append(f"source pages available: {doc.source_pages}")
    parts.extend(
        [
            "",
            "=== CONTENT ===",
            _content_for_prompt(doc.content),
            "",
            (
                "Extract every grounded field per the system instructions. "
                "Return one ExtractorEnvelope. Drop anything you cannot "
                "verify against the content above."
            ),
        ]
    )
    return "\n".join(parts)


def _to_canonical_fields(rows: list[_ExtractionRow]) -> list[ExtractionField]:
    out: list[ExtractionField] = []
    for r in rows:
        try:
            out.append(
                ExtractionField(
                    field_name=r.field_name,
                    value=r.value,
                    unit=r.unit,
                    source_page=max(1, int(r.source_page)),
                    confidence=float(r.confidence),
                    raw_text=r.raw_text,
                )
            )
        except (ValidationError, ValueError) as exc:
            logger.warning("extractor: dropping malformed row %r (%s)", r.field_name, exc)
    return out


# ─────────────────────── LLM client ───────────────────────


def _build_llm() -> Any:
    """Sonnet 4.6 with structured output bound to ``_ExtractorEnvelope``."""
    from ..llm import build_structured_llm

    return build_structured_llm(
        role="extractor",
        schema=_ExtractorEnvelope,
        # Sonnet 4.6 supports up to 64k output tokens. ≥30 ExtractionField
        # rows × ~150 tokens each + envelope overhead easily blows past
        # 8k; budget generously and let the cost ledger catch overspend.
        max_tokens=16_384,
        timeout=240,
        temperature=0.0,
    )


async def _invoke_llm(
    llm: Any, messages: list[Any], usage: Any | None = None
) -> _ExtractorEnvelope:
    config = {"callbacks": [usage]} if usage is not None else None
    raw = await llm.ainvoke(messages, config=config)
    if isinstance(raw, _ExtractorEnvelope):
        return raw
    if isinstance(raw, BaseModel):
        return _ExtractorEnvelope.model_validate(raw.model_dump())
    if isinstance(raw, dict):
        return _ExtractorEnvelope.model_validate(raw)
    raise ValueError(f"Unexpected Extractor LLM return: {type(raw).__name__}")


# ─────────────────────── per-document runner ───────────────────────


async def _extract_one(
    doc: ExtractorDocument,
    *,
    deal_id: str,
    system_blocks: list[Any],
) -> tuple[ExtractedDocumentResult, ModelCall | None]:
    """Run one LLM call for one document. Errors return success=False."""
    started = datetime.now(UTC)

    from ..llm import cached_system_message_blocks
    from ..usage import UsageCapture

    usage = UsageCapture()
    messages = [
        cached_system_message_blocks(system_blocks, role="extractor"),
        HumanMessage(content=_build_user_prompt(doc)),
    ]

    try:
        llm = _build_llm()
        envelope = await _invoke_llm(llm, messages, usage=usage)
    except (ValidationError, Exception) as exc:  # noqa: BLE001 - error path
        logger.warning(
            "extractor: LLM call failed for %s (%s)", doc.filename, exc
        )
        result = ExtractedDocumentResult(
            document_id=doc.document_id,
            filename=doc.filename,
            doc_type=doc.doc_type,
            fields=[],
            confidence=ConfidenceReport(
                overall=0.0,
                low_confidence_fields=[],
                requires_human_review=True,
            ),
            notes=None,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        return result, None

    fields = _to_canonical_fields(envelope.fields)
    by_field = {f.field_name: f.confidence for f in fields}
    avg_conf = (sum(by_field.values()) / len(by_field)) if by_field else 0.0
    overall = max(0.0, min(1.0, envelope.overall_confidence or avg_conf))
    confidence = ConfidenceReport(
        overall=overall,
        by_field=by_field,
        low_confidence_fields=list(envelope.low_confidence_fields),
        requires_human_review=envelope.requires_human_review or overall < 0.85,
    )

    result = ExtractedDocumentResult(
        document_id=doc.document_id,
        filename=doc.filename,
        doc_type=doc.doc_type,
        fields=fields,
        confidence=confidence,
        notes=envelope.notes,
        success=True,
    )

    completed = datetime.now(UTC)
    settings = get_settings()
    model_call = ModelCall(
        model=usage.model or settings.ANTHROPIC_EXTRACTOR_MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=0.0,
        trace_id=deal_id,
        started_at=started,
        completed_at=completed,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        agent_name="extractor",
    )
    return result, model_call


# ─────────────────────── public entry point ───────────────────────


@trace_agent("Extractor")
async def run_extractor(payload: ExtractorInput) -> ExtractorOutput:
    """Extract structured fields from each document on the deal."""
    t0 = time.monotonic()

    # Backwards-compatible no-op path for the graph stub.
    if not payload.documents:
        logger.info(
            "extractor: no inline documents (deal=%s, uris=%d) — empty result",
            payload.deal_id,
            len(payload.document_uris),
        )
        return ExtractorOutput(
            deal_id=payload.deal_id,
            extracted_documents=[],
            success=True,
            model_calls=[],
        )

    try:
        check_budget(
            {"deal_id": payload.deal_id, "model_calls": []}, stage="extractor"
        )
    except Exception as exc:
        logger.warning("extractor: budget check raised: %s", exc)
        return ExtractorOutput(
            deal_id=payload.deal_id,
            extracted_documents=[],
            success=False,
            error=str(exc),
        )

    # 4-block system prompt: agent instructions (uncached) +
    # USALI rules + brand catalog + extractor schema addendum (cached).
    # The agent instructions block changes per agent; the trailing
    # blocks are stable across tenants and live in the cache prefix
    # so the second call inside the 5-min TTL hits cache.
    from ..llm import build_agent_system_blocks

    system_blocks = build_agent_system_blocks(
        role="extractor",
        agent_instructions=SYSTEM_PROMPT,
    )
    # Pre-cache the catalog block so the lru_cache is warm before the
    # first parallel doc fan-out — keeps the very first call from
    # paying both the build cost and the cache miss cost.
    rules_as_prompt_block()

    results: list[ExtractedDocumentResult] = []
    model_calls: list[ModelCall] = []
    for doc in payload.documents:
        result, call = await _extract_one(
            doc,
            deal_id=payload.deal_id,
            system_blocks=system_blocks,
        )
        results.append(result)
        if call is not None:
            model_calls.append(call)

    # Cross-document confidence rollup.
    confidences = [r.confidence.overall for r in results if r.success]
    overall = (sum(confidences) / len(confidences)) if confidences else 0.0
    confidence = ConfidenceReport(
        overall=overall,
        low_confidence_fields=[
            f
            for r in results
            for f in r.confidence.low_confidence_fields
        ],
        requires_human_review=any(
            r.confidence.requires_human_review for r in results
        )
        or any(not r.success for r in results),
    )

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "extractor OK deal=%s docs=%d fields=%d in %dms",
        payload.deal_id,
        len(results),
        sum(len(r.fields) for r in results),
        elapsed_ms,
    )

    # Persist all per-document calls for the cost dashboard. Best-effort.
    if model_calls:
        from ..cost_persistence import persist_model_calls_standalone

        await persist_model_calls_standalone(
            deal_id=payload.deal_id,
            tenant_id=payload.tenant_id,
            calls=model_calls,
        )

    return ExtractorOutput(
        deal_id=payload.deal_id,
        extracted_documents=results,
        confidence=confidence,
        success=all(r.success for r in results),
        model_calls=model_calls,
    )


def serialize_json_doc(obj: Any) -> str:
    """Helper: render an extracted-JSON dict as deterministic content
    text for the LLM. Used by tests that load the golden-set fixtures."""
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False)


__all__ = [
    "ExtractedDocumentResult",
    "ExtractorDocument",
    "ExtractorInput",
    "ExtractorOutput",
    "run_extractor",
    "serialize_json_doc",
]
