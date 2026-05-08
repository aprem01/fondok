"""Due Diligence agent — generates broker questions from the deal's
extracted state.

Reads the deal's extraction results, engine outputs, and variance flags;
produces a prioritized list of questions an institutional underwriter
would want answered by the broker before touching the model. Each
question carries a source citation (which document / extraction the
gap was detected in), a category (Revenue / Expenses / Operations /
Market / CapEx), a priority (high / medium / low), and a supporting
metric (key + value) so the analyst can see exactly what the agent
keyed off when raising the question.

The output is the new ``Due Diligence`` sub-tab on the P&L page —
KPI counters, filterable list, batch send actions. The agent runs on
demand (POST /deals/{id}/due-diligence/generate) rather than on every
extraction completion, since LLM cost makes that prohibitive.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..budget import check_budget
from ..config import get_settings
from ..telemetry import trace_agent

try:
    from fondok_schemas import ModelCall
except ImportError:  # pragma: no cover - schemas always available
    ModelCall = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)


# ─────────────────────── prompts ───────────────────────


SYSTEM_PROMPT = """You are Fondok's Due Diligence agent — a senior
hotel acquisitions analyst building the broker-question packet
institutional buyers (Brookfield, KSL, Apollo) actually send before
LOI.

Read the deal's extracted state (T-12 actuals, OM broker proforma,
STR comp set, CBRE forecast, P&L benchmark, variance flags, engine
outputs) and identify the gaps, risks, and inconsistencies an
underwriter would want the broker to clarify. For each, write one
crisp question — institutional in tone, not adversarial — plus a
short narrative explaining WHY the question matters and what
specific extracted metric raised it.

Output: a flat list of ``DueDiligenceQuestion`` rows. Target 8-15
questions. Coverage matters — a packet with 3 questions is
unactionable; a packet with 30 is noise.

Per question, you must populate:

1. ``question`` — single sentence, ends with a question mark.
   Plain-language; assume an institutional reader. No internal
   shorthand. Examples:
     "Can you provide the trailing 12-month STR report with
     day-of-week segmentation?"
     "What is the current group vs transient mix, and are there
     any long-term group contracts expiring within the hold period?"
     "Confirm whether the F&B outlet leases are pass-through or
     included in the management fee structure."

2. ``narrative`` — 1-2 sentences explaining WHY. Reference the
   specific signal that raised the flag (variance, market gap,
   missing extraction, broker overstatement). Examples:
     "AI detected potential weekend/weekday performance variance.
     STR data shows 118.3 RGI but day-of-week breakdown needed to
     validate ADR assumptions."
     "Revenue projections assume stable segmentation. Understanding
     contract expirations is critical for forecasting accuracy."

3. ``priority`` — one of ``high`` / ``medium`` / ``low``:
   * ``high`` — material to underwriting; the deal can't go to IC
     without an answer (NOI variance, missing comp set, broker vs
     T-12 divergence ≥10%, debt-coverage gaps).
   * ``medium`` — the answer changes the model but isn't blocking
     (segmentation breakdowns, fee structure clarification,
     CapEx scope).
   * ``low`` — background context (operating partner history,
     property tax abatement status, ongoing litigation).

4. ``category`` — one of ``revenue`` / ``expenses`` / ``operations`` /
   ``market`` / ``capex``. Pick the closest fit:
   * Revenue: ADR / occupancy / segmentation / RGI / RevPAR / rate
     strategy / group bookings
   * Expenses: any P&L line below GOP, mgmt fee, FF&E reserve,
     property tax, insurance
   * Operations: brand affiliation, mgmt company, labor, GM
     tenure, F&B contracts
   * Market: comp set, supply pipeline, demand drivers, submarket
     trends
   * CapEx: PIP scope, deferred maintenance, ADA compliance,
     historical capex spend

5. ``source`` — short label of where the agent keyed off the
   question. Examples: ``"Competitive Set Analysis"``,
   ``"Revenue Engine"``, ``"T-12 Extraction"``, ``"OM Broker Proforma"``,
   ``"CBRE Horizons"``, ``"Variance Engine"``.

6. ``supporting_metric_key`` and ``supporting_metric_value`` — the
   specific number that triggered the question, rendered as a
   key/value pair the UI surfaces under the question. Examples:
     key="RevPAR Index", value="118.3"
     key="ADR (Broker)", value="$490"
     key="NOI Variance", value="-19.6%"
   Both fields optional; leave null when there's no single
   triggering number.

Rules:
* Don't invent numbers. Every supporting_metric must trace to a
  number in the deal state surfaced below.
* Don't ask questions whose answer is already in the extraction.
  ("What's the year built?" is silly if property_overview.year_built
  is populated.)
* Tone: institutional. No marketing language. No hedging
  adjectives ("strong", "exciting", "robust"). Direct, specific.
* Output one structured ``DueDiligenceEnvelope``. No prose
  outside the schema.
"""


# ─────────────────── structured-output envelope ───────────────────


class _QuestionEnv(BaseModel):
    """LLM-facing envelope for a single question. Validated then
    projected onto the canonical ``DueDiligenceQuestion``."""

    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(min_length=10, max_length=400)]
    narrative: Annotated[str, Field(min_length=10, max_length=600)]
    priority: Literal["high", "medium", "low"]
    category: Literal["revenue", "expenses", "operations", "market", "capex"]
    source: Annotated[str, Field(min_length=2, max_length=120)]
    supporting_metric_key: Annotated[str, Field(max_length=80)] | None = None
    supporting_metric_value: Annotated[str, Field(max_length=80)] | None = None


class _DueDiligenceEnvelope(BaseModel):
    """LLM-facing envelope returning a list of questions."""

    model_config = ConfigDict(extra="forbid")

    questions: list[_QuestionEnv] = Field(min_length=1, max_length=30)


# ─────────────────────── I/O contracts ───────────────────────


class DueDiligenceQuestion(BaseModel):
    """Canonical shape persisted to the DB and returned via API."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    question: str
    narrative: str
    priority: Literal["high", "medium", "low"]
    category: Literal["revenue", "expenses", "operations", "market", "capex"]
    source: str
    supporting_metric_key: str | None = None
    supporting_metric_value: str | None = None
    status: Literal["pending", "sent", "answered"] = "pending"
    created_at: datetime
    sent_at: datetime | None = None


class DueDiligenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    deal_data: dict[str, Any] = Field(default_factory=dict)
    extracted_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-doc-type field summaries the agent keys off.",
    )
    engine_outputs: dict[str, Any] = Field(default_factory=dict)
    variance_flags: list[dict[str, Any]] = Field(default_factory=list)
    market_data: dict[str, Any] = Field(default_factory=dict)


class DueDiligenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    questions: list[DueDiligenceQuestion] = Field(default_factory=list)
    success: bool = True
    error: str | None = None
    model_calls: list[Any] = Field(default_factory=list)


# ─────────────────────── helpers ───────────────────────


def _build_user_prompt(payload: DueDiligenceInput) -> str:
    """Pack the deal state into a single prompt block.

    The agent gets a structured snapshot of every layer that might
    raise a question: deal metadata, T-12 vs broker spread summary,
    engine headlines, variance flags by severity, market data
    indices. The LLM scans this and identifies gaps.
    """
    parts: list[str] = [
        f"deal_id: {payload.deal_id}",
        f"tenant: {payload.tenant_id}",
    ]

    if payload.deal_data:
        parts.append("=== DEAL METADATA ===")
        for k, v in payload.deal_data.items():
            parts.append(f"  {k}: {v}")

    if payload.extracted_summary:
        parts.append("=== EXTRACTED FIELD SUMMARY ===")
        for doc_type, fields in payload.extracted_summary.items():
            parts.append(f"--- {doc_type} ---")
            if isinstance(fields, dict):
                for k, v in fields.items():
                    parts.append(f"  {k}: {v}")

    if payload.engine_outputs:
        parts.append("=== ENGINE HEADLINES ===")
        for engine, headlines in payload.engine_outputs.items():
            parts.append(f"--- {engine} ---")
            if isinstance(headlines, dict):
                for k, v in headlines.items():
                    parts.append(f"  {k}: {v}")

    if payload.variance_flags:
        parts.append("=== VARIANCE FLAGS ===")
        for flag in payload.variance_flags:
            parts.append(
                f"  [{flag.get('severity', '?')}] {flag.get('field', '?')} "
                f"actual={flag.get('actual')} broker={flag.get('broker')} "
                f"delta_pct={flag.get('delta_pct')} rule={flag.get('rule_id')}"
            )

    if payload.market_data:
        parts.append("=== MARKET DATA ===")
        if isinstance(payload.market_data, dict):
            for k, v in payload.market_data.items():
                parts.append(f"  {k}: {v}")

    parts.append(
        "\nGenerate the broker question packet now. Target 8-15 "
        "questions. Return one DueDiligenceEnvelope. No prose."
    )
    return "\n".join(parts)


def _project_questions(
    rows: list[_QuestionEnv], deal_id: str
) -> list[DueDiligenceQuestion]:
    """Project LLM rows onto canonical shape with fresh UUIDs."""
    try:
        deal_uuid = UUID(deal_id)
    except (TypeError, ValueError):
        from uuid import uuid5

        deal_uuid = uuid5(UUID("00000000-0000-0000-0000-000000000000"), deal_id)

    now = datetime.now(UTC)
    out: list[DueDiligenceQuestion] = []
    for r in rows:
        try:
            out.append(
                DueDiligenceQuestion(
                    id=uuid4(),
                    deal_id=deal_uuid,
                    question=r.question,
                    narrative=r.narrative,
                    priority=r.priority,
                    category=r.category,
                    source=r.source,
                    supporting_metric_key=r.supporting_metric_key,
                    supporting_metric_value=r.supporting_metric_value,
                    status="pending",
                    created_at=now,
                    sent_at=None,
                )
            )
        except (ValidationError, ValueError) as exc:
            logger.warning(
                "due_diligence: dropped malformed question (%s): %s",
                exc,
                r.question[:80] if r.question else "<empty>",
            )
    return out


# ─────────────────────── LLM client ───────────────────────


def _build_llm() -> Any:
    """Sonnet 4.6 with structured output bound to ``_DueDiligenceEnvelope``."""
    from ..llm import build_structured_llm

    return build_structured_llm(
        role="due_diligence",
        schema=_DueDiligenceEnvelope,
        max_tokens=8_192,
        timeout=180,
        temperature=0.1,
    )


# ─────────────────────── public entrypoint ───────────────────────


@trace_agent("DueDiligence")
async def run_due_diligence(payload: DueDiligenceInput) -> DueDiligenceOutput:
    """Generate the broker due-diligence packet for ``deal_id``.

    On any LLM / structured-output failure we return a populated
    output with ``success=False`` rather than raising — the route
    surfaces the error to the UI without losing the partial state.
    """
    started = datetime.now(UTC)
    t0 = time.monotonic()

    try:
        check_budget({"deal_id": payload.deal_id, "model_calls": []}, stage="due_diligence")
    except Exception as exc:  # noqa: BLE001
        logger.warning("due_diligence: budget check raised: %s", exc)
        return DueDiligenceOutput(
            deal_id=payload.deal_id,
            questions=[],
            success=False,
            error=str(exc),
        )

    from ..llm import build_agent_system_blocks, cached_system_message_blocks
    from ..usage import UsageCapture

    system_blocks = build_agent_system_blocks(
        role="due_diligence",
        agent_instructions=SYSTEM_PROMPT,
    )
    messages = [
        cached_system_message_blocks(system_blocks, role="due_diligence"),
        HumanMessage(content=_build_user_prompt(payload)),
    ]
    usage = UsageCapture()

    try:
        llm = _build_llm()
        envelope = await llm.with_config(
            {"callbacks": [usage]}
        ).ainvoke(messages)
    except Exception as exc:  # noqa: BLE001
        logger.exception("due_diligence: LLM call failed for deal=%s", payload.deal_id)
        return DueDiligenceOutput(
            deal_id=payload.deal_id,
            questions=[],
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    if not isinstance(envelope, _DueDiligenceEnvelope):
        # Some LLM clients return a dict; coerce.
        try:
            envelope = _DueDiligenceEnvelope.model_validate(envelope)
        except ValidationError as exc:
            logger.warning("due_diligence: envelope validation failed: %s", exc)
            return DueDiligenceOutput(
                deal_id=payload.deal_id,
                questions=[],
                success=False,
                error=f"envelope validation: {exc}",
            )

    questions = _project_questions(envelope.questions, payload.deal_id)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "due_diligence: deal=%s n_questions=%d elapsed_ms=%d",
        payload.deal_id,
        len(questions),
        elapsed_ms,
    )
    _ = started  # currently informational

    return DueDiligenceOutput(
        deal_id=payload.deal_id,
        questions=questions,
        success=True,
        model_calls=[],  # detailed call records are persisted by UsageCapture
    )


__all__ = [
    "DueDiligenceInput",
    "DueDiligenceOutput",
    "DueDiligenceQuestion",
    "run_due_diligence",
]
