"""Anthropic Message Batches lane for the Analyst agent — Task V (2026-07).

The IC memo lane is asynchronous by nature: an analyst kicks off the
draft on Fondok, walks away, and reviews the memo later. Anthropic
charges 50% of the standard input+output rate on the Message Batches
API in exchange for up to 24 hours of turnaround, which is a strict
subset of the memo lane's real SLA (Sam accepts overnight drafts on
weekend deals). This module wires the memo lane to that API without
disturbing the interactive extraction pipeline.

Design contract
---------------
* **Output parity.** The batch path constructs byte-identical messages
  as the sync single-shot path (:func:`app.agents.analyst._run_analyst_single`)
  — same system prompt blocks, same user prompt, same schema envelope,
  same model. The only variable is the transport (batch endpoint vs
  live ``messages.create``). This keeps the batch lane feature-flag-
  reversible: flip ``ANALYST_BATCH_API_ENABLED=false`` and the sync path
  keeps working exactly as before.
* **Additive.** Nothing here mutates the sync path. The wrapper caller
  in :mod:`app.api.deals` still schedules the streaming path when the
  batch flag is off (default).
* **Dark by default.** ``ANALYST_BATCH_API_ENABLED`` defaults to False
  so the code ships behind a flag and rolls out per-tenant only after
  the polling worker is verified in staging.

Runtime shape
-------------
1. ``run_analyst_batch(payload)`` submits a single-request batch to
   Anthropic's ``POST /v1/messages/batches`` endpoint (SDK:
   ``client.messages.batches.create``), persists a
   ``pending_batches`` row keyed by ``(batch_id, deal_id, tenant_id)``,
   and returns ``AnalystBatchSubmitResult(status='queued', batch_id=...)``
   immediately.
2. A periodic poller — invoked from a scheduler tick or a follow-up
   HTTP GET — calls :func:`poll_pending_batches` which iterates the
   ``queued``/``in_progress`` rows and asks
   ``client.messages.batches.retrieve(batch_id)`` for each. When the
   batch turns ``ended``, results are fetched via
   ``client.messages.batches.results(batch_id)`` (JSONL) and the
   ``_InvestmentMemoEnvelope`` is parsed, projected into an
   ``InvestmentMemo`` with the same helpers the sync path uses, and
   written to the memo cache + persisted for the cost dashboard at
   50%-rate pricing.

Cost accounting
---------------
Anthropic bills 50% of standard rates on batch traffic. The
``persist_model_calls_standalone`` helper doesn't know about the
discount, so we adjust ``cost_usd`` on the ``ModelCall`` row before
persisting. Downstream views (``/admin/cost``) treat the row as a
plain cost record — no batch-specific special-casing needed.

Failure semantics
-----------------
* Submit failure → row is not persisted; caller receives ``status='error'``
  with the exception message. Sync fallback is the natural recovery
  (flip the flag off, retry).
* Batch ends with a ``result.type == 'errored'`` per-request payload →
  row moves to ``status='failed'`` with the error message, memo cache
  is marked failed. No retry (the analyst can hit the sync endpoint).
* Batch expires (24h TTL) → poller moves the row to ``status='expired'``.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fondok_schemas import InvestmentMemo, MemoSection, ModelCall
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session_factory
from .analyst import (
    SYSTEM_PROMPT,
    AnalystInput,
    AnalystOutput,
    _build_user_prompt,
    _ensure_required_sections,
    _InvestmentMemoEnvelope,
    _make_confidence,
    _project_section,
    _safe_evaluate,
    _to_uuid,
)

logger = logging.getLogger(__name__)


# ─────────────────────── I/O contracts ───────────────────────


class AnalystBatchSubmitResult(BaseModel):
    """Return shape of :func:`run_analyst_batch`.

    The batch path is fire-and-forget; the caller gets back the
    ``batch_id`` (so it can be surfaced in the UI as "job queued") and
    the initial ``status``. ``error`` is populated only when the submit
    itself failed — the persisted row is not created in that case.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    batch_id: str | None = None
    status: str = "queued"
    error: str | None = None


class PendingBatchRow(BaseModel):
    """Typed view of a ``pending_batches`` row."""

    model_config = ConfigDict(extra="forbid")

    id: str
    batch_id: str
    deal_id: str
    tenant_id: str
    agent_name: str
    status: str
    submitted_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


# ─────────────────────── payload construction ───────────────────────


def _build_analyst_messages(payload: AnalystInput) -> tuple[SystemMessage, HumanMessage]:
    """Assemble the (system, user) message pair the batch request submits.

    This calls exactly the same builders as
    :func:`app.agents.analyst._run_analyst_single`. Output parity with
    the sync path is a hard contract of this module — if the sync path
    is updated to add or reorder cache breakpoints, this helper picks
    up the change for free.
    """
    # Imported lazily so importing this module during migrations
    # (before the langchain stack is available) does not fail.
    from ..llm import build_agent_system_blocks, cached_system_message_blocks
    from ..usali_rules import rules_as_prompt_block

    system_blocks = build_agent_system_blocks(
        role="analyst",
        agent_instructions=SYSTEM_PROMPT,
    )
    rules_as_prompt_block()  # warm the shared catalog cache
    system_message = cached_system_message_blocks(system_blocks, role="analyst")
    user_message = HumanMessage(content=_build_user_prompt(payload))
    return system_message, user_message


def _system_message_to_batch_blocks(system: SystemMessage) -> list[dict[str, Any]]:
    """Coerce the cached SystemMessage content into batch-API-shaped blocks.

    ``cached_system_message_blocks`` returns either a plain string (for
    non-Anthropic providers) or a list of ``{type: text, text, cache_control?}``
    dicts (for Anthropic). The batch API expects the ``system`` field
    to be a list of text blocks with optional ``cache_control``, matching
    the ``messages.create`` shape. This helper normalizes both cases.
    """
    content = system.content
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [dict(block) for block in content]
    return [{"type": "text", "text": str(content)}]


def _build_batch_request(
    payload: AnalystInput, *, custom_id: str, tool_schema: dict[str, Any]
) -> dict[str, Any]:
    """Return the ``requests[0]`` payload for the batch submission.

    Note: keep this consistent with the sync path's structured-output
    binding (``build_structured_llm(role='analyst', schema=_InvestmentMemoEnvelope)``).
    We pass the schema as a single Anthropic tool + ``tool_choice`` so
    the model is forced to return one ``_InvestmentMemoEnvelope`` — the
    same shape the sync ``.with_structured_output`` runnable receives.
    """
    settings = get_settings()
    system, user = _build_analyst_messages(payload)
    system_blocks = _system_message_to_batch_blocks(system)
    user_content = user.content
    if isinstance(user_content, list):
        user_blocks = [dict(b) for b in user_content]
    else:
        user_blocks = [{"type": "text", "text": str(user_content)}]

    body: dict[str, Any] = {
        "model": settings.ANTHROPIC_ANALYST_MODEL,
        "max_tokens": 16_384,
        "system": system_blocks,
        "messages": [{"role": "user", "content": user_blocks}],
        "tools": [
            {
                "name": "emit_investment_memo",
                "description": (
                    "Emit exactly one InvestmentMemoEnvelope containing all "
                    "six required sections."
                ),
                "input_schema": tool_schema,
            }
        ],
        "tool_choice": {"type": "tool", "name": "emit_investment_memo"},
    }
    return {"custom_id": custom_id, "params": body}


def _envelope_tool_schema() -> dict[str, Any]:
    """JSON schema for ``_InvestmentMemoEnvelope`` used as the batch tool schema."""
    # ``model_json_schema`` returns the pydantic v2 JSON schema; the
    # Anthropic tool API accepts it as-is (it is JSON Schema draft 2020-12
    # compatible for the fields the model produces).
    schema = _InvestmentMemoEnvelope.model_json_schema()
    # Anthropic's tool schema wants ``type: object`` at the top level;
    # pydantic always supplies it. Strip pydantic-only ``$defs`` refs if
    # unused so the schema is stable — but keep them when referenced by
    # ``$ref`` (which _InvestmentMemoEnvelope needs for _MemoSectionEnvelope).
    return schema


# ─────────────────────── DB helpers ───────────────────────


async def _insert_pending_batch(
    session: AsyncSession,
    *,
    batch_id: str,
    deal_id: str,
    tenant_id: str,
    agent_name: str = "analyst",
) -> PendingBatchRow:
    row_id = str(uuid4())
    now = datetime.now(UTC)
    await session.execute(
        text(
            """
            INSERT INTO pending_batches
                (id, batch_id, deal_id, tenant_id, agent_name,
                 status, submitted_at)
            VALUES
                (:id, :batch_id, :deal_id, :tenant_id, :agent_name,
                 :status, :submitted_at)
            """
        ),
        {
            "id": row_id,
            "batch_id": batch_id,
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "agent_name": agent_name,
            "status": "queued",
            "submitted_at": now.isoformat(),
        },
    )
    await session.commit()
    return PendingBatchRow(
        id=row_id,
        batch_id=batch_id,
        deal_id=deal_id,
        tenant_id=tenant_id,
        agent_name=agent_name,
        status="queued",
        submitted_at=now,
    )


async def _update_pending_status(
    session: AsyncSession,
    *,
    batch_id: str,
    tenant_id: str,
    status: str,
    error: str | None = None,
    completed: bool = False,
) -> None:
    params: dict[str, Any] = {
        "batch_id": batch_id,
        "tenant": str(tenant_id),
        "status": status,
        "error": error,
    }
    if completed:
        params["completed_at"] = datetime.now(UTC).isoformat()
        # tenant_id predicate keeps tenant_middleware / Sentry quiet — see
        # apps/worker/app/tenant_middleware.py.
        sql = """
            UPDATE pending_batches
               SET status = :status,
                   error = :error,
                   completed_at = :completed_at
             WHERE batch_id = :batch_id
               AND tenant_id = :tenant
        """
    else:
        # tenant_id predicate keeps tenant_middleware / Sentry quiet — see
        # apps/worker/app/tenant_middleware.py.
        sql = """
            UPDATE pending_batches
               SET status = :status,
                   error = :error
             WHERE batch_id = :batch_id
               AND tenant_id = :tenant
        """
    await session.execute(text(sql), params)
    await session.commit()


async def _list_open_batches(session: AsyncSession) -> list[PendingBatchRow]:
    result = await session.execute(
        text(
            """
            SELECT id, batch_id, deal_id, tenant_id, agent_name, status,
                   submitted_at, completed_at, error
              FROM pending_batches
             WHERE status IN ('queued', 'in_progress')
             ORDER BY submitted_at ASC
            """
        )
    )
    rows: list[PendingBatchRow] = []
    for r in result.fetchall():
        rows.append(
            PendingBatchRow(
                id=r[0],
                batch_id=r[1],
                deal_id=r[2],
                tenant_id=r[3],
                agent_name=r[4],
                status=r[5],
                submitted_at=_coerce_dt(r[6]),
                completed_at=_coerce_dt(r[7]) if r[7] else None,
                error=r[8],
            )
        )
    return rows


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(UTC)


# ─────────────────────── Anthropic client wrapper ───────────────────────


class BatchClient:
    """Thin wrapper around ``anthropic.Anthropic().messages.batches``.

    Exists as its own class so tests can drop in a fake without patching
    the SDK singleton. In production callers construct this once per
    submission or poll cycle — the underlying HTTP client is cheap
    enough not to warrant global caching.
    """

    def __init__(self, api_key: str | None = None) -> None:
        # Imported lazily so importing this module during migrations
        # (before anthropic is installed) does not fail.
        from anthropic import Anthropic

        settings = get_settings()
        key = api_key or (
            settings.ANTHROPIC_API_KEY.get_secret_value()
            if settings.ANTHROPIC_API_KEY
            else None
        )
        if not key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set — cannot use the batch client"
            )
        self._client = Anthropic(api_key=key)

    def submit(self, requests: list[dict[str, Any]]) -> Any:
        return self._client.messages.batches.create(requests=requests)

    def retrieve(self, batch_id: str) -> Any:
        return self._client.messages.batches.retrieve(batch_id)

    def results(self, batch_id: str) -> Any:
        return self._client.messages.batches.results(batch_id)


# ─────────────────────── submit ───────────────────────


async def run_analyst_batch(
    payload: AnalystInput,
    *,
    client: BatchClient | None = None,
    session: AsyncSession | None = None,
) -> AnalystBatchSubmitResult:
    """Submit the memo draft to the Anthropic Message Batches API.

    Returns ``status='queued'`` on success; the memo is drafted later
    by the poller. The polling loop is the one that eventually writes
    the ``InvestmentMemo`` into the memo cache and persists the
    ``ModelCall`` row at the 50%-rate discount.
    """
    settings = get_settings()
    if not settings.ANALYST_BATCH_API_ENABLED:
        return AnalystBatchSubmitResult(
            deal_id=payload.deal_id,
            status="disabled",
            error="ANALYST_BATCH_API_ENABLED is false",
        )

    try:
        custom_id = f"analyst:{payload.deal_id}:{uuid4().hex[:8]}"
        req = _build_batch_request(
            payload,
            custom_id=custom_id,
            tool_schema=_envelope_tool_schema(),
        )
    except Exception as exc:  # noqa: BLE001 - error path
        logger.exception(
            "analyst-batch: failed to build batch request for deal=%s",
            payload.deal_id,
        )
        return AnalystBatchSubmitResult(
            deal_id=payload.deal_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )

    try:
        batch_client = client or BatchClient()
        response = batch_client.submit([req])
        batch_id = _extract_batch_id(response)
    except Exception as exc:  # noqa: BLE001 - error path
        logger.exception(
            "analyst-batch: submit failed for deal=%s", payload.deal_id
        )
        return AnalystBatchSubmitResult(
            deal_id=payload.deal_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        )

    # Persist the pending row so the poller can find it. Use the caller's
    # session if provided (tests), otherwise open a fresh one.
    owns_session = session is None
    if session is None:
        session_factory = get_session_factory()
        session = session_factory()
    try:
        await _insert_pending_batch(
            session,
            batch_id=batch_id,
            deal_id=payload.deal_id,
            tenant_id=payload.tenant_id,
        )
    finally:
        if owns_session:
            await session.close()

    logger.info(
        "analyst-batch: submitted deal=%s batch_id=%s", payload.deal_id, batch_id
    )
    return AnalystBatchSubmitResult(
        deal_id=payload.deal_id,
        batch_id=batch_id,
        status="queued",
    )


def _extract_batch_id(response: Any) -> str:
    """Pull the batch id off an Anthropic ``MessageBatch`` object.

    The SDK returns a pydantic-like object with an ``id`` attribute; a
    test double may return a plain dict. Both are supported.
    """
    if isinstance(response, dict):
        return str(response.get("id") or response.get("batch_id") or "")
    return str(getattr(response, "id", "") or getattr(response, "batch_id", ""))


# ─────────────────────── poll + ingest ───────────────────────


async def poll_pending_batches(
    *,
    client: BatchClient | None = None,
    session: AsyncSession | None = None,
) -> dict[str, int]:
    """Iterate open batches; ingest results for the ones that ended.

    Returns a small tally so the caller can log or emit a metric.
    Safe to call from a scheduler tick every ~5 minutes. Batches that
    are still ``in_progress`` are left alone; the row's status stays
    ``queued`` (initial) or is bumped to ``in_progress`` when observed.
    """
    owns_session = session is None
    if session is None:
        session_factory = get_session_factory()
        session = session_factory()

    tally = {"checked": 0, "completed": 0, "failed": 0, "expired": 0, "pending": 0}
    try:
        open_rows = await _list_open_batches(session)
        if not open_rows:
            return tally

        batch_client = client or BatchClient()
        for row in open_rows:
            tally["checked"] += 1
            try:
                status_response = batch_client.retrieve(row.batch_id)
            except Exception as exc:  # noqa: BLE001 - error path
                logger.warning(
                    "analyst-batch: retrieve failed batch=%s (%s)",
                    row.batch_id,
                    exc,
                )
                continue

            processing_status = _extract_processing_status(status_response)
            if processing_status in ("in_progress", "canceling"):
                if row.status != "in_progress":
                    await _update_pending_status(
                        session,
                        batch_id=row.batch_id,
                        tenant_id=row.tenant_id,
                        status="in_progress",
                    )
                tally["pending"] += 1
                continue

            if processing_status == "expired":
                await _update_pending_status(
                    session,
                    batch_id=row.batch_id,
                    tenant_id=row.tenant_id,
                    status="expired",
                    error="batch expired without completion",
                    completed=True,
                )
                tally["expired"] += 1
                continue

            if processing_status != "ended":
                logger.warning(
                    "analyst-batch: unexpected processing_status=%s batch=%s",
                    processing_status,
                    row.batch_id,
                )
                tally["pending"] += 1
                continue

            # Ended → fetch results.
            try:
                raw_results = batch_client.results(row.batch_id)
            except Exception as exc:  # noqa: BLE001 - error path
                logger.exception(
                    "analyst-batch: results fetch failed batch=%s", row.batch_id
                )
                await _update_pending_status(
                    session,
                    batch_id=row.batch_id,
                    tenant_id=row.tenant_id,
                    status="failed",
                    error=f"results fetch failed: {exc}",
                    completed=True,
                )
                tally["failed"] += 1
                continue

            ingested = await _ingest_batch_results(
                row, raw_results, session=session
            )
            if ingested:
                await _update_pending_status(
                    session,
                    batch_id=row.batch_id,
                    tenant_id=row.tenant_id,
                    status="complete",
                    completed=True,
                )
                tally["completed"] += 1
            else:
                await _update_pending_status(
                    session,
                    batch_id=row.batch_id,
                    tenant_id=row.tenant_id,
                    status="failed",
                    error="no memo parsed from batch results",
                    completed=True,
                )
                tally["failed"] += 1
    finally:
        if owns_session:
            await session.close()

    return tally


def _extract_processing_status(response: Any) -> str:
    """Normalize the batch status field across dict/SDK object shapes."""
    if isinstance(response, dict):
        return str(response.get("processing_status") or response.get("status") or "")
    return str(
        getattr(response, "processing_status", None)
        or getattr(response, "status", "")
        or ""
    )


async def _ingest_batch_results(
    row: PendingBatchRow, raw_results: Any, *, session: AsyncSession
) -> bool:
    """Parse the batch results iterable, project a memo, write it out.

    Returns True when a memo was successfully persisted.
    """
    entries = _iter_batch_entries(raw_results)
    for entry in entries:
        # ``entry`` shape (per Anthropic docs):
        # {"custom_id": ..., "result": {"type": "succeeded" | "errored"
        #   | "canceled" | "expired", "message": {...}}}
        result = entry.get("result") if isinstance(entry, dict) else None
        if not result:
            continue
        result_type = result.get("type") if isinstance(result, dict) else None
        if result_type != "succeeded":
            error_detail = result.get("error") if isinstance(result, dict) else None
            logger.warning(
                "analyst-batch: request errored batch=%s type=%s error=%s",
                row.batch_id,
                result_type,
                error_detail,
            )
            return False

        message = result.get("message") if isinstance(result, dict) else None
        envelope = _parse_envelope_from_message(message)
        if envelope is None:
            return False

        memo = _project_memo(envelope, row=row)
        if memo is None:
            return False

        usage = _extract_usage(message)
        await _persist_memo_and_cost(memo, row=row, usage=usage)
        return True
    return False


def _iter_batch_entries(raw_results: Any) -> list[dict[str, Any]]:
    """Normalize batch results to a list of dict entries.

    Anthropic streams results as JSONL. The SDK's ``.results()`` returns
    an iterator of pydantic objects; a test double may return a list of
    dicts (or a JSONL string). All three shapes are supported.
    """
    if raw_results is None:
        return []
    if isinstance(raw_results, str):
        lines = [ln for ln in raw_results.splitlines() if ln.strip()]
        return [json.loads(ln) for ln in lines]
    if isinstance(raw_results, list):
        return [
            e if isinstance(e, dict) else _to_plain_dict(e)
            for e in raw_results
        ]
    # Iterator / generator.
    out: list[dict[str, Any]] = []
    for e in raw_results:
        if isinstance(e, dict):
            out.append(e)
        else:
            out.append(_to_plain_dict(e))
    return out


def _to_plain_dict(obj: Any) -> dict[str, Any]:
    """Coerce a pydantic-like SDK object to a plain dict."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return dict(obj)  # last resort


def _parse_envelope_from_message(
    message: dict[str, Any] | Any,
) -> _InvestmentMemoEnvelope | None:
    """Pull the tool_use block out of an Anthropic message and validate it.

    The batch tool call binds the model to a single ``emit_investment_memo``
    tool, so we expect one ``tool_use`` content block in the response.
    """
    if message is None:
        return None
    if not isinstance(message, dict):
        message = _to_plain_dict(message)
    content = message.get("content", [])
    if isinstance(content, str):
        # Non-tool text response — try JSON.
        try:
            return _InvestmentMemoEnvelope.model_validate_json(content)
        except (ValidationError, ValueError):
            return None
    for block in content:
        if not isinstance(block, dict):
            block = _to_plain_dict(block)
        if block.get("type") != "tool_use":
            continue
        raw = block.get("input", {})
        try:
            return _InvestmentMemoEnvelope.model_validate(raw)
        except ValidationError as exc:
            logger.warning(
                "analyst-batch: envelope validation failed (%s)", exc
            )
            return None
    return None


def _project_memo(
    envelope: _InvestmentMemoEnvelope, *, row: PendingBatchRow
) -> InvestmentMemo | None:
    """Convert the LLM envelope into an ``InvestmentMemo`` — mirrors the sync path."""
    # We don't have doc_ids available here (payload isn't persisted) —
    # pass an empty set which disables strict citation-target validation
    # in _project_citations. This matches the sync path's behavior on a
    # payload with no source documents.
    doc_ids: set[str] = set()
    sections: list[MemoSection] = []
    for s_env in envelope.sections:
        proj = _project_section(s_env, doc_ids=doc_ids)
        if proj is not None:
            sections.append(proj)
    sections = _ensure_required_sections(sections)
    if not sections:
        logger.warning(
            "analyst-batch: no valid required sections in batch=%s", row.batch_id
        )
        return None
    confidence = _make_confidence(sections, envelope.overall_confidence)
    return InvestmentMemo(
        deal_id=_to_uuid(row.deal_id),
        sections=sections,
        generated_at=datetime.now(UTC),
        confidence=confidence,
        version=1,
    )


def _extract_usage(message: dict[str, Any] | Any | None) -> dict[str, int]:
    """Pull token usage counters off an Anthropic message payload."""
    if message is None:
        return {"input_tokens": 0, "output_tokens": 0}
    if not isinstance(message, dict):
        message = _to_plain_dict(message)
    usage = message.get("usage") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_creation_input_tokens": int(
            usage.get("cache_creation_input_tokens") or 0
        ),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
    }


async def _persist_memo_and_cost(
    memo: InvestmentMemo, *, row: PendingBatchRow, usage: dict[str, int]
) -> None:
    """Write the drafted memo into the memo cache + persist a ``ModelCall`` row.

    Cost is booked at 50% of the standard rate — the batch API's core
    trade-off. We adjust the persisted ``cost_usd`` here rather than
    teach ``persist_model_calls_standalone`` about batches; the cost
    dashboard treats the row as any other spend.
    """
    settings = get_settings()

    # Write to memo cache so GET /memo picks it up.
    try:
        from ..streaming.broadcast import get_memo_cache

        memo_cache = get_memo_cache()
        for section in memo.sections:
            await memo_cache.record_section(
                row.deal_id, section.model_dump(mode="json")
            )
        await memo_cache.mark_done(
            row.deal_id, generated_at=datetime.now(UTC).isoformat()
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("analyst-batch: memo cache write failed (%s)", exc)

    # Build the ModelCall row + 50%-rate pricing adjustment.
    model_call = ModelCall(
        model=settings.ANTHROPIC_ANALYST_MODEL,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
        cost_usd=0.0,
        trace_id=row.deal_id,
        started_at=row.submitted_at,
        completed_at=datetime.now(UTC),
        cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
        cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        agent_name="analyst",
    )
    # Pre-compute at full rate, then halve.
    try:
        from ..cost_persistence import _compute_cost_usd

        full_cost = _compute_cost_usd(model_call)
        discounted = round(full_cost * 0.5, 6)
        model_call = model_call.model_copy(update={"cost_usd": discounted})
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("analyst-batch: cost adjust failed (%s)", exc)

    try:
        from ..cost_persistence import persist_model_calls_standalone

        await persist_model_calls_standalone(
            deal_id=row.deal_id,
            tenant_id=row.tenant_id,
            calls=[model_call],
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("analyst-batch: persist_model_calls failed (%s)", exc)

    # Optionally: safe eval (mirrors sync path). We swallow any errors.
    try:
        _safe_evaluate(memo)
    except Exception:  # pragma: no cover - defensive
        pass


__all__ = [
    "AnalystBatchSubmitResult",
    "BatchClient",
    "PendingBatchRow",
    "poll_pending_batches",
    "run_analyst_batch",
]
