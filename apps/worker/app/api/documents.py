"""Document upload + extraction endpoints.

Endpoints
---------
POST   /deals/{deal_id}/documents/upload                — multi-file PDF upload
POST   /deals/{deal_id}/documents/{doc_id}/extract       — kick off extraction
GET    /deals/{deal_id}/documents/{doc_id}/extraction    — latest extraction result
GET    /deals/{deal_id}/documents                        — list documents on deal

The upload route hashes each file, persists to the configured raw
store (local FS or S3), parses the PDF (LlamaParse or PyMuPDF), and
writes a ``documents`` row with status ``UPLOADED``. The extract
route kicks the LangGraph runtime via ``BackgroundTasks`` and
transitions the row through ``CLASSIFYING → EXTRACTING → EXTRACTED``
(or ``FAILED``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session, get_session_factory
from ..extraction import ParseError, parse_pdf
from ..storage import get_raw_store

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── status enum ───────────────────────────


# Lifecycle: UPLOADED → CLASSIFYING → EXTRACTING → EXTRACTED (or FAILED)
DOC_STATUS_UPLOADED = "UPLOADED"
DOC_STATUS_CLASSIFYING = "CLASSIFYING"
DOC_STATUS_EXTRACTING = "EXTRACTING"
DOC_STATUS_EXTRACTED = "EXTRACTED"
DOC_STATUS_FAILED = "FAILED"


# ─────────────────────────── response shapes ───────────────────────────


class DocumentRecord(BaseModel):
    """Row-level view of a deal document."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    tenant_id: UUID
    filename: str
    doc_type: str | None = None
    status: str = DOC_STATUS_UPLOADED
    uploaded_at: datetime
    content_hash: str | None = None
    storage_key: str | None = None
    size_bytes: int | None = None
    page_count: int | None = None
    parser: str | None = None


class ExtractionStartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    job_id: str
    status: str = "extraction_started"


class ExtractionFieldOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str
    value: Any | None = None
    unit: str | None = None
    source_page: int | None = None
    confidence: float | None = None
    raw_text: str | None = None


class ConfidenceReportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall: float = 0.0
    by_field: dict[str, float] = Field(default_factory=dict)
    low_confidence_fields: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class ExtractionResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    status: str
    fields: list[ExtractionFieldOut] = Field(default_factory=list)
    confidence_report: ConfidenceReportOut | None = None
    agent_version: str | None = None
    created_at: datetime | None = None


# ─────────────────────────── helpers ───────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _guess_doc_type(filename: str) -> str:
    """Cheap filename-driven hint. The Router agent overrides on extract."""
    name = (filename or "").lower()
    if "t12" in name or "t-12" in name:
        return "T12"
    if "om" == name.split(".")[0] or "offering" in name:
        return "OM"
    if "str" in name:
        return "STR"
    if "rent" in name and "roll" in name:
        return "RENT_ROLL"
    if "market" in name:
        return "MARKET_STUDY"
    if "p&l" in name or "pnl" in name or "p_l" in name:
        return "PNL"
    if "contract" in name:
        return "CONTRACT"
    return "T12"


def _row_to_record(row: dict[str, Any]) -> DocumentRecord:
    return DocumentRecord(
        id=UUID(str(row["id"])),
        deal_id=UUID(str(row["deal_id"])),
        tenant_id=UUID(str(row["tenant_id"])),
        filename=row["filename"],
        doc_type=row.get("doc_type"),
        status=row["status"],
        uploaded_at=_coerce_dt(row["uploaded_at"]),
        content_hash=row.get("content_hash"),
        storage_key=row.get("storage_key"),
        size_bytes=row.get("size_bytes"),
        page_count=row.get("page_count"),
        parser=row.get("parser"),
    )


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str):
        # SQLite hands us "YYYY-MM-DD HH:MM:SS" (no TZ).
        try:
            return datetime.fromisoformat(value).replace(tzinfo=UTC)
        except ValueError:
            pass
    return _now()


# ─────────────────────────── upload ───────────────────────────


@router.post(
    "/{deal_id}/documents/upload",
    response_model=list[DocumentRecord],
    status_code=status.HTTP_201_CREATED,
)
async def upload_documents(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    files: list[UploadFile] = File(...),
) -> list[DocumentRecord]:
    """Persist and parse one-or-more PDFs against ``deal_id``.

    Each file is hashed, written to the raw store, parsed (LlamaParse
    or PyMuPDF), and recorded as a ``documents`` row. The parse output
    is cached on the row's ``extraction_data`` so the Extractor agent
    can read raw page text without re-parsing the PDF.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one file is required",
        )

    settings = get_settings()
    tenant_id = settings.DEFAULT_TENANT_ID  # multi-tenant auth lands later
    store = get_raw_store(settings)
    records: list[DocumentRecord] = []

    for upload in files:
        body = await upload.read()
        if not body:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"file {upload.filename!r} is empty",
            )
        filename = upload.filename or "upload.pdf"
        content_hash = hashlib.sha256(body).hexdigest()

        try:
            storage_key = await store.put(
                tenant_id=str(tenant_id),
                deal_id=str(deal_id),
                content_hash=content_hash,
                filename=filename,
                bytes_=body,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("upload: store put failed for %s", filename)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"raw store write failed: {exc}",
            ) from exc

        page_count: int | None = None
        parser_label: str | None = None
        extraction_data: dict[str, Any] | None = None
        try:
            parsed = await parse_pdf(body, filename)
            page_count = parsed.total_pages
            parser_label = parsed.parser
            extraction_data = {
                "parser": parsed.parser,
                "total_pages": parsed.total_pages,
                "content_hash": parsed.content_hash,
                "parsed_at": parsed.parsed_at.isoformat(),
                "pages": [
                    {
                        "page_num": p.page_num,
                        "text": p.text,
                        "tables": p.tables,
                        "metadata": p.metadata,
                    }
                    for p in parsed.pages
                ],
            }
        except ParseError as exc:
            logger.warning(
                "upload: parse failed for %s — recording UPLOADED w/o parse: %s",
                filename,
                exc,
            )

        doc_id = uuid4()
        doc_type = _guess_doc_type(filename)
        uploaded_at = _now()

        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, content_hash, storage_key, size_bytes,
                    page_count, parser, extraction_data
                ) VALUES (
                    :id, :deal_id, :tenant_id, :filename, :doc_type, :status,
                    :uploaded_at, :content_hash, :storage_key, :size_bytes,
                    :page_count, :parser, :extraction_data
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal_id": str(deal_id),
                "tenant_id": str(tenant_id),
                "filename": filename,
                "doc_type": doc_type,
                "status": DOC_STATUS_UPLOADED,
                "uploaded_at": uploaded_at,
                "content_hash": content_hash,
                "storage_key": storage_key,
                "size_bytes": len(body),
                "page_count": page_count,
                "parser": parser_label,
                "extraction_data": (
                    json.dumps(extraction_data) if extraction_data else None
                ),
            },
        )
        await session.commit()

        records.append(
            DocumentRecord(
                id=doc_id,
                deal_id=deal_id,
                tenant_id=UUID(tenant_id),
                filename=filename,
                doc_type=doc_type,
                status=DOC_STATUS_UPLOADED,
                uploaded_at=uploaded_at,
                content_hash=content_hash,
                storage_key=storage_key,
                size_bytes=len(body),
                page_count=page_count,
                parser=parser_label,
            )
        )

    logger.info(
        "documents.upload: deal=%s tenant=%s files=%d",
        deal_id,
        tenant_id,
        len(records),
    )
    return records


# ─────────────────────────── list ───────────────────────────


@router.get("/{deal_id}/documents", response_model=list[DocumentRecord])
async def list_documents(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[DocumentRecord]:
    """List documents on a deal in upload-recent order."""
    rows = await session.execute(
        text(
            """
            SELECT id, deal_id, tenant_id, filename, doc_type, status,
                   uploaded_at, content_hash, storage_key, size_bytes,
                   page_count, parser
              FROM documents
             WHERE deal_id = :deal_id
             ORDER BY uploaded_at DESC
            """
        ),
        {"deal_id": str(deal_id)},
    )
    return [_row_to_record(dict(r._mapping)) for r in rows.fetchall()]


# ─────────────────────────── extract ───────────────────────────


@router.post(
    "/{deal_id}/documents/{doc_id}/extract",
    response_model=ExtractionStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def extract_document(
    deal_id: UUID,
    doc_id: UUID,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ExtractionStartResponse:
    """Kick off the extraction pipeline for a single document.

    The route returns immediately with a job id. The actual work runs
    in a FastAPI ``BackgroundTask`` that drives the LangGraph runtime
    up to (but not through) the first HITL gate.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, tenant_id, status
                  FROM documents
                 WHERE id = :id AND deal_id = :deal_id
                """
            ),
            {"id": str(doc_id), "deal_id": str(deal_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document {doc_id} not found on deal {deal_id}",
        )

    tenant_id = str(row._mapping["tenant_id"])

    # Mark the row as classifying immediately so the UI can pick up
    # the transition before the background task lands.
    await session.execute(
        text(
            "UPDATE documents SET status = :s WHERE id = :id"
        ),
        {"s": DOC_STATUS_CLASSIFYING, "id": str(doc_id)},
    )
    await session.commit()

    job_id = uuid4()
    background_tasks.add_task(
        _run_extraction_pipeline,
        deal_id=str(deal_id),
        doc_id=str(doc_id),
        tenant_id=tenant_id,
    )
    return ExtractionStartResponse(
        document_id=doc_id,
        job_id=str(job_id),
        status="extraction_started",
    )


# ─────────────────────────── extraction read ───────────────────────────


@router.get(
    "/{deal_id}/documents/{doc_id}/extraction",
    response_model=ExtractionResultResponse,
)
async def get_extraction(
    deal_id: UUID,
    doc_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ExtractionResultResponse:
    """Return the latest extraction result for a document."""
    doc_row = (
        await session.execute(
            text(
                "SELECT status FROM documents WHERE id = :id AND deal_id = :d"
            ),
            {"id": str(doc_id), "d": str(deal_id)},
        )
    ).first()
    if doc_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document {doc_id} not found",
        )
    doc_status = doc_row._mapping["status"]

    extraction_row = (
        await session.execute(
            text(
                """
                SELECT id, fields, confidence_report, agent_version, created_at
                  FROM extraction_results
                 WHERE document_id = :id
                 ORDER BY created_at DESC
                 LIMIT 1
                """
            ),
            {"id": str(doc_id)},
        )
    ).first()

    if extraction_row is None:
        return ExtractionResultResponse(
            document_id=doc_id,
            status=doc_status,
        )

    mapping = extraction_row._mapping
    fields_blob = mapping["fields"]
    if isinstance(fields_blob, str):
        fields_blob = json.loads(fields_blob) if fields_blob else []
    fields = [ExtractionFieldOut.model_validate(f) for f in (fields_blob or [])]

    cr_blob = mapping["confidence_report"]
    if isinstance(cr_blob, str):
        cr_blob = json.loads(cr_blob) if cr_blob else None
    confidence_report = (
        ConfidenceReportOut.model_validate(cr_blob) if cr_blob else None
    )

    return ExtractionResultResponse(
        document_id=doc_id,
        status=doc_status,
        fields=fields,
        confidence_report=confidence_report,
        agent_version=mapping["agent_version"],
        created_at=_coerce_dt(mapping["created_at"]),
    )


# ─────────────────────────── background runner ───────────────────────────


async def _run_extraction_pipeline(
    *, deal_id: str, doc_id: str, tenant_id: str
) -> None:
    """Drive the LangGraph runtime end-to-end up to the first HITL gate.

    On any failure the document row is marked ``FAILED`` and the error
    is logged. ``EVALS_MOCK=true`` short-circuits the agents so CI can
    exercise the wiring without spending tokens.
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            await session.execute(
                text("UPDATE documents SET status = :s WHERE id = :id"),
                {"s": DOC_STATUS_EXTRACTING, "id": doc_id},
            )
            await session.commit()

            row = (
                await session.execute(
                    text(
                        "SELECT storage_key, extraction_data FROM documents WHERE id = :id"
                    ),
                    {"id": doc_id},
                )
            ).first()
            storage_key = row._mapping["storage_key"] if row else None

            if os.environ.get("EVALS_MOCK", "").lower() in ("1", "true", "yes"):
                fields, confidence = _mock_extraction_payload()
                agent_version = "mock-evals"
            else:
                fields, confidence, agent_version = await _run_graph_extraction(
                    deal_id=deal_id,
                    tenant_id=tenant_id,
                    storage_key=storage_key,
                )

            ext_id = uuid4()
            await session.execute(
                text(
                    """
                    INSERT INTO extraction_results (
                        id, document_id, deal_id, tenant_id,
                        fields, confidence_report, agent_version, created_at
                    ) VALUES (
                        :id, :doc, :deal, :tenant,
                        :fields, :cr, :ver, :created
                    )
                    """
                ),
                {
                    "id": str(ext_id),
                    "doc": doc_id,
                    "deal": deal_id,
                    "tenant": tenant_id,
                    "fields": json.dumps(fields),
                    "cr": json.dumps(confidence),
                    "ver": agent_version,
                    "created": _now(),
                },
            )
            await session.execute(
                text("UPDATE documents SET status = :s WHERE id = :id"),
                {"s": DOC_STATUS_EXTRACTED, "id": doc_id},
            )
            await session.commit()
            logger.info(
                "extraction complete: doc=%s deal=%s fields=%d",
                doc_id,
                deal_id,
                len(fields),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("extraction failed: doc=%s — %s", doc_id, exc)
            try:
                await session.execute(
                    text("UPDATE documents SET status = :s WHERE id = :id"),
                    {"s": DOC_STATUS_FAILED, "id": doc_id},
                )
                await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("extraction: failed to record FAILED status")


async def _run_graph_extraction(
    *,
    deal_id: str,
    tenant_id: str,
    storage_key: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
    """Drive the LangGraph runtime through classify → extract → normalize.

    For the upload pipeline we don't need to run the full deal flow —
    we just need the agents that turn raw pages into structured
    fields + confidence. We invoke those agents directly and let the
    end-to-end graph run land at the deal level later.
    """
    from ..agents.extractor import ExtractorInput, run_extractor
    from ..agents.router import RouterInput, run_router

    router_input = RouterInput(tenant_id=tenant_id, deal_id=deal_id)
    router_out = await run_router(router_input)

    extractor_input = ExtractorInput(
        tenant_id=tenant_id,
        deal_id=deal_id,
        document_uris=[storage_key] if storage_key else [],
    )
    extractor_out = await run_extractor(extractor_input)

    # The real Extractor returns ``extracted_documents`` per the
    # current agent envelope; we project it down to a plain field
    # list + confidence dict the API can serialize.
    fields: list[dict[str, Any]] = []
    confidence: dict[str, Any] = {
        "overall": 0.0,
        "by_field": {},
        "low_confidence_fields": [],
        "requires_human_review": True,
    }
    for doc in extractor_out.extracted_documents or []:
        # The real extractor envelope (TBD) will expose .fields and
        # .confidence; until then we tolerate either a Pydantic model
        # or a plain dict.
        as_dict = doc.model_dump() if hasattr(doc, "model_dump") else dict(doc)
        for f in as_dict.get("fields", []) or []:
            fields.append(dict(f))
        cr = as_dict.get("confidence")
        if cr:
            confidence = dict(cr)

    return fields, confidence, f"router:{router_out.route};extractor"


def _mock_extraction_payload() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Canned fields + confidence for ``EVALS_MOCK=true`` runs."""
    fields = [
        {
            "field_name": "noi_year_1",
            "value": 1234567.0,
            "unit": "USD",
            "source_page": 1,
            "confidence": 0.92,
            "raw_text": "Net Operating Income: $1,234,567",
        },
        {
            "field_name": "occupancy_year_1",
            "value": 0.74,
            "unit": "ratio",
            "source_page": 1,
            "confidence": 0.88,
            "raw_text": "Occupancy: 74%",
        },
    ]
    confidence = {
        "overall": 0.9,
        "by_field": {"noi_year_1": 0.92, "occupancy_year_1": 0.88},
        "low_confidence_fields": [],
        "requires_human_review": False,
    }
    return fields, confidence


# Public symbols for tests.
__all__ = [
    "DOC_STATUS_CLASSIFYING",
    "DOC_STATUS_EXTRACTED",
    "DOC_STATUS_EXTRACTING",
    "DOC_STATUS_FAILED",
    "DOC_STATUS_UPLOADED",
    "DocumentRecord",
    "ExtractionResultResponse",
    "ExtractionStartResponse",
    "router",
]
