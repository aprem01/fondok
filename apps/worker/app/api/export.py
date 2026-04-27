"""Export endpoints — Excel model, PDF memo, PPTX IC deck."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)
router = APIRouter()


class ExportJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    format: str
    status: str = "queued"
    download_url: str | None = None


@router.post("/{deal_id}/export/xlsx", response_model=ExportJob)
async def export_xlsx(deal_id: UUID) -> ExportJob:
    """Stub: enqueues the Excel model export."""
    return ExportJob(deal_id=deal_id, format="xlsx")


@router.post("/{deal_id}/export/pdf", response_model=ExportJob)
async def export_pdf(deal_id: UUID) -> ExportJob:
    """Stub: enqueues the PDF memo export."""
    return ExportJob(deal_id=deal_id, format="pdf")


@router.post("/{deal_id}/export/pptx", response_model=ExportJob)
async def export_pptx(deal_id: UUID) -> ExportJob:
    """Stub: enqueues the PPTX IC deck export."""
    return ExportJob(deal_id=deal_id, format="pptx")
