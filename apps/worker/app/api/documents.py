"""Document upload + extraction endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, File, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class DocumentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    filename: str
    doc_type: str | None = None
    status: str = "uploaded"
    uploaded_at: datetime


class ExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    status: str = "queued"
    extracted_fields: dict[str, str] = Field(default_factory=dict)


@router.post(
    "/{deal_id}/upload",
    response_model=DocumentRecord,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    deal_id: UUID, file: UploadFile = File(...)
) -> DocumentRecord:
    """Stub: accepts a multipart upload and returns a placeholder record."""
    logger.info(
        "documents(stub): upload deal=%s filename=%s", deal_id, file.filename
    )
    return DocumentRecord(
        id=uuid4(),
        deal_id=deal_id,
        filename=file.filename or "unknown",
        uploaded_at=datetime.now(UTC),
    )


@router.post("/{document_id}/extract", response_model=ExtractionResponse)
async def extract_document(document_id: UUID) -> ExtractionResponse:
    """Stub: enqueues an Extractor run."""
    return ExtractionResponse(document_id=document_id, status="queued")
