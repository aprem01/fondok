"""Dossier + Q&A endpoints — the Context Data Product surface.

``GET  /deals/{deal_id}/dossier``  → typed snapshot of a deal.
``POST /deals/{deal_id}/ask``      → grounded Q&A over the dossier.

The dossier endpoint is read-only and pure-composition. The ask
endpoint composes the dossier and hands it to the Researcher agent
(Opus 4.7 with prompt caching) which returns an answer + citations.
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..dossier import DealDossier, build_dossier
from .deals import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── /dossier ───────────────────────────


@router.get(
    "/{deal_id}/dossier",
    response_model=DealDossier,
)
async def get_dossier(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    include_page_excerpts: bool = True,
) -> DealDossier:
    """Return the deal's full Context Data Product.

    ``include_page_excerpts=false`` trims the per-page text snapshots
    on each document (useful when the caller only needs structured
    fields, not raw narrative).
    """
    return await build_dossier(
        session,
        deal_id=deal_id,
        tenant_id=str(tenant_id),
        include_page_excerpts=include_page_excerpts,
    )


# ─────────────────────────── /ask ───────────────────────────


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[str, Field(min_length=1, max_length=4000)]


class AskCitation(BaseModel):
    """Citation in the Researcher's answer — same shape as memo
    citations so the web side can reuse the existing pin/side-pane
    component."""

    model_config = ConfigDict(extra="forbid")

    document_id: str | None = None
    page: int | None = None
    field: str | None = None
    excerpt: str | None = None


class AskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    question: str
    answer: str
    citations: list[AskCitation] = Field(default_factory=list)
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    note: str | None = None


@router.post(
    "/{deal_id}/ask",
    response_model=AskResponse,
)
async def ask_deal(
    deal_id: str,
    body: AskRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> AskResponse:
    """Answer ``body.question`` grounded in the deal's dossier.

    Composes the dossier, hands it to the Researcher agent (Opus 4.7),
    and returns a single grounded answer with citations. Empty-state
    response carries a structured ``note`` when the dossier doesn't
    have enough material yet (e.g. no extracted documents).
    """
    dossier = await build_dossier(
        session,
        deal_id=deal_id,
        tenant_id=str(tenant_id),
        include_page_excerpts=True,
    )

    if dossier.confidence.docs_extracted == 0:
        return AskResponse(
            deal_id=deal_id,
            question=body.question,
            answer="",
            citations=[],
            confidence=0.0,
            note=(
                "no extracted documents on this deal — upload an OM or "
                "T-12 first so the researcher has source material."
            ),
        )

    from ..agents.researcher import ResearcherInput, run_researcher

    payload = ResearcherInput(
        tenant_id=str(tenant_id),
        deal_id=deal_id,
        question=body.question,
        dossier=dossier,
    )
    try:
        out = await run_researcher(payload)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("ask: researcher run failed for deal=%s", deal_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"researcher failed: {type(exc).__name__}: {exc}",
        ) from exc

    return AskResponse(
        deal_id=deal_id,
        question=body.question,
        answer=out.answer,
        citations=[
            AskCitation(
                document_id=c.document_id,
                page=c.page,
                field=c.field,
                excerpt=c.excerpt,
            )
            for c in out.citations
        ],
        confidence=out.confidence,
        note=out.note,
    )


__all__ = ["router"]
