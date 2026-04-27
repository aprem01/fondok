"""Deal lifecycle endpoints — CRUD, status, HITL gates, memo streaming.

Phase-2 stubs: every route returns a typed Pydantic response so the
web client can wire against a stable contract while the agent + engine
implementations land underneath.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings
from ..costs import build_cost_report

try:
    from fondok_schemas import DealCostReport
except ImportError:  # pragma: no cover
    DealCostReport = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── request bodies ───────────────────────────


class CreateDealBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    city: str | None = None
    keys: int | None = Field(default=None, ge=1)
    service: str | None = None


class Gate1Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(approve|reject|edit)$")
    notes: str | None = None


class Gate2Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation: str = Field(pattern=r"^(go|no-go|conditional)$")
    notes: str | None = None


# ─────────────────────────── response shapes ───────────────────────────


class DealSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    name: str
    city: str | None = None
    keys: int | None = None
    service: str | None = None
    status: str = "Draft"
    deal_stage: str | None = None
    risk: str | None = None
    ai_confidence: float | None = None
    created_at: datetime
    updated_at: datetime


class DealStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: str
    deal_stage: str | None = None
    last_event: str | None = None


class GateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    gate: str
    accepted: bool = True
    next_state: str | None = None


class MemoEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    sections: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


# ─────────────────────────── routes ───────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _stub_summary(name: str = "Stub Deal", deal_id: UUID | None = None) -> DealSummary:
    settings = get_settings()
    return DealSummary(
        id=deal_id or uuid4(),
        tenant_id=UUID(settings.DEFAULT_TENANT_ID),
        name=name,
        status="Draft",
        created_at=_now(),
        updated_at=_now(),
    )


@router.get("", response_model=list[DealSummary])
async def list_deals() -> list[DealSummary]:
    """Stub: returns an empty list until the DB-backed query lands."""
    return []


@router.post("", response_model=DealSummary, status_code=status.HTTP_201_CREATED)
async def create_deal(body: CreateDealBody) -> DealSummary:
    """Stub: echoes the request body as a freshly minted deal."""
    return _stub_summary(name=body.name)


@router.get("/{deal_id}", response_model=DealSummary)
async def get_deal(deal_id: UUID) -> DealSummary:
    return _stub_summary(deal_id=deal_id)


@router.get("/{deal_id}/status", response_model=DealStatusResponse)
async def get_deal_status(deal_id: UUID) -> DealStatusResponse:
    return DealStatusResponse(id=deal_id, status="Draft", last_event="created")


@router.post("/{deal_id}/gate1", response_model=GateResponse)
async def gate1(deal_id: UUID, body: Gate1Body) -> GateResponse:
    """HITL Gate 1: accept / reject / edit the normalized spread."""
    logger.info("gate1(stub): deal=%s decision=%s", deal_id, body.decision)
    return GateResponse(
        id=deal_id,
        gate="gate1",
        accepted=body.decision == "approve",
        next_state="run_engines",
    )


@router.post("/{deal_id}/gate2", response_model=GateResponse)
async def gate2(deal_id: UUID, body: Gate2Body) -> GateResponse:
    """HITL Gate 2: final recommendation on the IC memo."""
    logger.info("gate2(stub): deal=%s recommendation=%s", deal_id, body.recommendation)
    return GateResponse(
        id=deal_id, gate="gate2", accepted=True, next_state="finalize"
    )


@router.get("/{deal_id}/memo", response_model=MemoEnvelope)
async def get_memo(deal_id: UUID) -> MemoEnvelope:
    """Final IC memo (JSON envelope). Stub returns empty sections."""
    return MemoEnvelope(deal_id=deal_id)


@router.get("/{deal_id}/costs", response_model=DealCostReport)
async def get_deal_costs(deal_id: UUID) -> Any:
    """Aggregated LLM cost dashboard for ``deal_id``.

    Reads ``ModelCall`` rows from the ``model_calls`` table (when
    populated) and rolls them up by agent and model bucket. Returns a
    well-formed zeroed report when there's no activity yet so the UI
    can render the empty state without a separate code path.
    """
    return await build_cost_report(str(deal_id))


async def _load_deal_payload(deal_id: str) -> Any:
    """Build an ``AnalystInput`` from the persisted deal + engine state.

    Until the live deal flow is fully wired through the database we fall
    back to the Kimpton Angler fixture so the streaming endpoint demos
    end-to-end. Once ``apps/worker/app/storage`` lands a deal-fetch helper
    this function should prefer real DB rows when available.
    """
    from ..agents.analyst import AnalystInput, AnalystSourceDocument
    from ..export.fixtures import kimpton_deal, kimpton_memo, kimpton_model

    settings = get_settings()
    deal = kimpton_deal()
    deal["id"] = deal_id
    model = kimpton_model()
    memo = kimpton_memo()

    # Build a lightweight set of source documents from the memo's
    # appendix so the Analyst has something to cite. Each filename
    # maps to a deterministic synthetic document_id; the Analyst
    # tolerates citations that reference any of these ids.
    docs: list[AnalystSourceDocument] = []
    for idx, fname in enumerate(memo.get("appendix", {}).get("documents_reviewed", []), start=1):
        docs.append(
            AnalystSourceDocument(
                document_id=f"doc-{idx:02d}",
                filename=fname,
                doc_type="reference",
                page_count=1,
                excerpts_by_page={1: f"Reference excerpt for {fname}."},
            )
        )

    return AnalystInput(
        tenant_id=settings.DEFAULT_TENANT_ID,
        deal_id=deal_id,
        deal_data=deal,
        normalized_spread=None,
        engine_results=model,
        variance_report=None,
        source_documents=docs,
    )


@router.post("/{deal_id}/memo/generate")
async def trigger_memo_generation(
    deal_id: str, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Kick off the streaming Opus memo draft. Returns immediately.

    The Analyst publishes one section at a time to the in-process
    ``MemoBroadcast`` keyed by ``memo:{deal_id}``; clients should
    immediately open ``GET /deals/{deal_id}/memo/stream`` to receive
    the sections via SSE.
    """
    from ..agents.analyst import run_analyst_streaming

    payload = await _load_deal_payload(deal_id)
    background_tasks.add_task(run_analyst_streaming, payload)
    logger.info("memo/generate: scheduled streaming draft for deal=%s", deal_id)
    return {"status": "started", "deal_id": deal_id}


@router.get("/{deal_id}/memo/stream")
async def stream_memo(deal_id: str) -> StreamingResponse:
    """SSE stream of memo sections as the Analyst writes them.

    Subscribes to the in-process ``MemoBroadcast`` channel
    ``memo:{deal_id}``. Each completed section is emitted as a
    ``section`` SSE event; a final ``done`` event closes the stream.
    """
    from ..streaming import DONE_SENTINEL, get_broadcast

    broadcast = get_broadcast()
    channel = f"memo:{deal_id}"

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            async for event in broadcast.subscribe(channel):
                event_name = event.get("event", "section")
                if event_name == DONE_SENTINEL:
                    payload = json.dumps(
                        {
                            "data": event.get("data", {}),
                            "metadata": event.get("metadata", {}),
                        }
                    )
                    yield f"event: done\ndata: {payload}\n\n".encode()
                    break
                payload = json.dumps(
                    {
                        "data": event.get("data", {}),
                        "metadata": event.get("metadata", {}),
                    }
                )
                yield f"event: section\ndata: {payload}\n\n".encode()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("memo/stream: subscriber loop failed (%s)", exc)
            err = json.dumps({"error": str(exc)})
            yield f"event: error\ndata: {err}\n\n".encode()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
