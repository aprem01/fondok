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
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..database import get_session, get_session_factory
from ..extraction import ParseError, parse_document
from ..storage import StorageError, get_raw_store
from .deals import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── status enum ───────────────────────────


# Lifecycle:
#   PARSING (upload accepted, LlamaParse / PyMuPDF in flight) →
#   UPLOADED (parse done, ready for extraction)              →
#   CLASSIFYING                                              →
#   EXTRACTING                                               →
#   EXTRACTED  (terminal success)
#   PARSE_FAILED / FAILED (terminal failure)
#
# PARSING is new (Sam QA re-test #2): LlamaParse can take 30-60s on
# dense PDFs, which exceeded the inline upload-request budget on
# Vercel/Railway proxies — large OMs were timing out before parse
# completed. Upload now returns 201 immediately at PARSING, and a
# background task drives the row through PARSING → UPLOADED →
# CLASSIFYING → EXTRACTING → EXTRACTED end-to-end. The frontend just
# polls /documents until it sees EXTRACTED (or one of the failure
# states) — no separate /extract trigger required.
DOC_STATUS_PARSING = "PARSING"
DOC_STATUS_UPLOADED = "UPLOADED"
DOC_STATUS_CLASSIFYING = "CLASSIFYING"
DOC_STATUS_EXTRACTING = "EXTRACTING"
DOC_STATUS_EXTRACTED = "EXTRACTED"
DOC_STATUS_PARSE_FAILED = "PARSE_FAILED"
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
    # Typed failure signal — populated on FAILED rows so the web UI can
    # surface a meaningful banner instead of a vague "extraction failed"
    # toast. Kind is one of: ``billing`` (Anthropic credit balance
    # exhausted), ``auth`` (API key rejected), ``rate_limit``
    # (Anthropic 429), ``parse`` (PDF/XLS parser couldn't read the
    # file), ``other`` (anything else).
    error_kind: str | None = None
    error_message: str | None = None
    # USALI compliance scoring (ROADMAP #3). ``usali_score`` is a 0-100
    # percentage written after a successful P&L-family extraction (T12,
    # PNL_MONTHLY, PNL_YTD). ``None`` means "inconclusive" — fewer than
    # 5 USALI rules were applicable; the UI shows that as a label
    # instead of a misleading percent. ``usali_deviations`` carries the
    # full JSONB shape: ``{inconclusive, applicable_count, passed_count,
    # deviations: [...]}``.
    usali_score: float | None = None
    usali_deviations: list[dict] | dict | None = None


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
    # Per-page text from the parser cache, keyed by page number as
    # string. Lets the citation side-pane show the cited page's
    # contents without re-parsing the PDF. Empty when extraction
    # hasn't run yet or the parser produced no usable pages.
    parsed_pages: dict[str, str] = Field(default_factory=dict)
    page_count: int | None = None


# ─────────────────────────── market-data envelope ───────────────────────────


class CompSetEntry(BaseModel):
    """One competitor row inside a STR_TREND extraction."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    keys: int | None = None
    occupancy_pct: float | None = None
    adr_usd: float | None = None
    revpar_usd: float | None = None


class StrTrendBlock(BaseModel):
    """Subject + comp set + indices, from a STR_TREND extraction."""

    model_config = ConfigDict(extra="forbid")

    subject_occupancy_pct: float | None = None
    subject_adr_usd: float | None = None
    subject_revpar_usd: float | None = None
    rgi_revpar_index: float | None = None
    ari_adr_index: float | None = None
    mpi_occupancy_index: float | None = None
    comp_set_size: int | None = None
    total_keys: int | None = None
    compset: list[CompSetEntry] = Field(default_factory=list)


class CbreYearProjection(BaseModel):
    """One forecast year inside a CBRE Horizons extraction."""

    model_config = ConfigDict(extra="forbid")

    year_index: int
    occupancy_pct: float | None = None
    adr_usd: float | None = None
    revpar_usd: float | None = None
    revpar_growth_pct: float | None = None


class CbreHorizonsBlock(BaseModel):
    """Five-year forward projection from a CBRE_HORIZONS extraction."""

    model_config = ConfigDict(extra="forbid")

    submarket: str | None = None
    chain_scale: str | None = None
    publication_date: str | None = None
    years: list[CbreYearProjection] = Field(default_factory=list)


class PnlBenchmarkBlock(BaseModel):
    """Peer-set ratios + PAR/POR figures from a PNL_BENCHMARK extraction."""

    model_config = ConfigDict(extra="forbid")

    peer_set_size: int | None = None
    rooms_dept_pct: float | None = None
    fb_dept_margin: float | None = None
    gop_margin: float | None = None
    a_and_g_pct: float | None = None
    sales_marketing_pct: float | None = None
    utilities_pct: float | None = None
    property_taxes_pct: float | None = None
    insurance_pct: float | None = None
    rooms_revenue_par: float | None = None
    total_revenue_par: float | None = None
    noi_par: float | None = None
    rooms_revenue_por: float | None = None
    fb_revenue_por: float | None = None


class MarketDataResponse(BaseModel):
    """Aggregated external-report envelope for the Market tab.

    The web app's Market tab + the forward-projection engine read this
    single endpoint instead of hitting three separate extraction
    queries. Every block is optional — empty when the deal has no
    extracted document of that type yet — so the frontend can render
    "awaiting <doc>" cards without needing a separate 404 path.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    str_trend: StrTrendBlock | None = None
    cbre_horizons: CbreHorizonsBlock | None = None
    pnl_benchmark: PnlBenchmarkBlock | None = None
    sources: dict[str, list[UUID]] = Field(
        default_factory=dict,
        description=(
            "Map from doc_type ('STR_TREND' / 'CBRE_HORIZONS' / "
            "'PNL_BENCHMARK') to the document_ids whose extractions "
            "fed each block. Lets the UI deep-link from a block back "
            "to the underlying source PDF."
        ),
    )


# ─────────────────────────── helpers ───────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


# A .xlsx that's tiny is almost never a full-year P&L — real T-12s
# carry 12 monthly columns × dozens of departmental lines and weigh
# in around 80-300KB at the smallest. Anything materially below
# that threshold is more likely a room-types lookup, a rate sheet,
# or a one-tab summary. Falling through to the T12 default for
# those wastes an extractor LLM call on the wrong USALI schema and
# returns 0 fields (Sam QA 2026-05-30: ROOM TYPES COUNT LOCATION.xlsx
# at 11KB classified as T12). Threshold tuned to admit thin annual
# P&L exports from small properties while excluding lookup tables.
_TINY_XLSX_BYTES = 30_000


def _guess_doc_type(filename: str, size_bytes: int | None = None) -> str:
    """Cheap filename-driven hint. The Router agent overrides on extract.

    Live smoke test caught a regression: ``sample_OM.pdf`` was being
    classified as T12 because the OM check required the ENTIRE base
    name to equal "om". The looser ``"_om" in name`` / token check
    correctly catches "sample_OM.pdf" and "Coral_Bay_OM_v2.pdf"
    without false-matching benign substrings (the previous
    ``"offering" in name`` check is preserved).

    Order matters: more-specific external-report types
    (``STR_TREND``, ``PNL_BENCHMARK``, ``CBRE_HORIZONS``) are checked
    BEFORE the broader ``STR`` / ``PNL`` so a "STR_trend.pdf" doesn't
    get mis-routed as a plain STR benchmark and lose its comp-set
    fan-out.

    When ``size_bytes`` is provided AND the file is a tiny .xlsx
    (< 30KB), the function refuses to fall through to the T12 default
    and returns ``UNKNOWN`` instead — small Excel files are nearly
    always lookups / rate sheets, not full P&Ls, and the Extractor
    will burn an LLM call returning 0 fields if pointed at the
    wrong schema.
    """
    name = (filename or "").lower()
    base = name.rsplit(".", 1)[0]
    # Tokenize on underscore, hyphen, AND whitespace — uploads from
    # broker pipelines sometimes carry literal-space filenames
    # ("ROOM TYPES COUNT LOCATION.xlsx") that the old underscore-only
    # split treated as one giant token, defeating every heuristic
    # below (Sam QA 2026-05-30).
    tokens = set(
        base.replace("-", "_").replace(" ", "_").split("_")
    )

    # Room mix / unit mix lookup tables — usually thin Excel files.
    # Check before the size guard so a "room_types_breakdown.xlsx"
    # gets the correct ROOM_MIX label even when tiny.
    if (
        ("room" in tokens and ("types" in tokens or "type" in tokens))
        or ("rooms" in tokens and ("types" in tokens or "type" in tokens))
        or ("room" in tokens and "mix" in tokens)
        or ("unit" in tokens and "mix" in tokens)
        or "roommix" in name
        or "unitmix" in name
        or "room_count" in name
        or "roomcount" in name
        or "key_count" in name
        or ("room" in tokens and "count" in tokens)
    ):
        return "ROOM_MIX"

    # External market reports (May 7 scope) — check first so the more
    # specific patterns win over the generic STR / PNL fallbacks.
    if (
        ("str" in tokens and "trend" in tokens)
        or ("trend" in tokens and ("comp" in tokens or "compset" in tokens))
        or "comp_set" in name
        or "compset" in name
        or "competitive_set" in name
        or "competitiveset" in name
        or ("str" in tokens and ("comp" in tokens or "competitive" in tokens))
    ):
        return "STR_TREND"
    if "cbre" in tokens or "horizons" in tokens or "forecast" in tokens:
        return "CBRE_HORIZONS"
    if (
        "benchmark" in tokens
        or "hotstats" in tokens
        or "por_par" in name
        or "porpar" in name
        or "industry_avg" in name
        or "industryavg" in name
        or ("industry" in tokens and "avg" in tokens)
    ):
        return "PNL_BENCHMARK"

    if "t12" in name or "t-12" in name:
        return "T12"
    if "om" in tokens or base == "om" or "offering" in name or "memorandum" in name:
        return "OM"
    if "str" in tokens or "smith" in name:
        return "STR"
    if "rent" in name and "roll" in name:
        return "RENT_ROLL"
    if "market" in name:
        return "MARKET_STUDY"
    if "p&l" in name or "pnl" in name or "p_l" in name:
        return "PNL"
    if "contract" in name:
        return "CONTRACT"

    # Tiny-xlsx guard — don't fall through to the T12 default for
    # spreadsheets too small to plausibly be a P&L.
    if (
        size_bytes is not None
        and (name.endswith(".xlsx") or name.endswith(".xlsm"))
        and size_bytes < _TINY_XLSX_BYTES
    ):
        return "UNKNOWN"

    return "T12"


# Lower-cased period_type tokens the Extractor emits on P&L documents
# under `p_and_l_usali.period_type`. Maps to the narrower DocType.
_PERIOD_TYPE_TO_PNL_DOC_TYPE: dict[str, str] = {
    "annual": "T12",
    "fiscal_year": "T12",
    "full_year": "T12",
    "trailing_twelve": "T12",
    "ttm": "T12",
    "t12": "T12",
    "rolling_twelve": "T12",
    "ytd": "PNL_YTD",
    "year_to_date": "PNL_YTD",
    "monthly": "PNL_MONTHLY",
    "month": "PNL_MONTHLY",
    "single_month": "PNL_MONTHLY",
    # Quarterly P&Ls are uncommon enough to leave broadly classified.
    # They'll still pass the engine_runner SQL filter as PNL.
}


def _refine_pnl_doc_type(
    classified: str | None, fields: list[dict[str, Any]]
) -> str | None:
    """Refine a P&L-ish doc_type using the extracted period_type.

    The Router agent classifies docs from filename + first ~2k chars and
    can't reliably distinguish a single-month P&L from a true trailing-
    twelve. The Extractor pulls the period span off the table itself
    under ``p_and_l_usali.period_type``. We use that to narrow the
    persisted ``doc_type`` so downstream engines rank annual T-12s
    above YTD/monthly rolls (Rani's QA: a May 2024 monthly was being
    treated as a T-12).

    Returns ``None`` to mean "no change" — the caller still falls back
    to updating just the status. Non-P&L doc types (OM, STR, etc.)
    pass through unchanged.
    """
    base = (classified or "").upper().strip() or None
    # Only refine docs the Router landed in the P&L family.
    if base not in {"T12", "PNL", "PNL_MONTHLY", "PNL_YTD"}:
        return base
    if not isinstance(fields, list):
        return base
    period_type = ""
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = (f.get("field_name") or "").strip().lower()
        if not name.endswith("period_type"):
            continue
        v = f.get("value")
        if isinstance(v, str) and v.strip():
            period_type = v.strip().lower()
            break
    if not period_type:
        return base
    refined = _PERIOD_TYPE_TO_PNL_DOC_TYPE.get(period_type)
    if refined is None:
        return base
    return refined


def _row_to_record(row: dict[str, Any]) -> DocumentRecord:
    # Surface typed error info to the UI when present. The
    # extraction_data JSON blob carries `error_kind` and
    # `error_message` for FAILED rows (set on PARSE_FAILED or
    # extraction-time 0-field empties). Web reads these to show an
    # actionable banner instead of just "Pending" / "Failed".
    error_kind: str | None = None
    error_message: str | None = None
    raw_ed = row.get("extraction_data")
    if isinstance(raw_ed, str) and raw_ed:
        try:
            raw_ed = json.loads(raw_ed)
        except (json.JSONDecodeError, TypeError):
            raw_ed = None
    if isinstance(raw_ed, dict):
        ek = raw_ed.get("error_kind")
        em = raw_ed.get("error_message")
        if isinstance(ek, str):
            error_kind = ek
        if isinstance(em, str):
            error_message = em

    # USALI compliance — score is nullable (inconclusive ⇒ NULL).
    # Postgres stores deviations as JSONB (driver returns dict / list);
    # SQLite stores it as TEXT (we json.dumps on write and parse here).
    usali_score_raw = row.get("usali_score")
    try:
        usali_score: float | None = (
            float(usali_score_raw) if usali_score_raw is not None else None
        )
    except (TypeError, ValueError):
        usali_score = None
    usali_deviations: list[dict] | dict | None = None
    raw_dev = row.get("usali_deviations")
    if isinstance(raw_dev, (list, dict)):
        usali_deviations = raw_dev
    elif isinstance(raw_dev, str) and raw_dev:
        try:
            parsed = json.loads(raw_dev)
            if isinstance(parsed, (list, dict)):
                usali_deviations = parsed
        except (json.JSONDecodeError, TypeError):
            usali_deviations = None

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
        error_kind=error_kind,
        error_message=error_message,
        usali_score=usali_score,
        usali_deviations=usali_deviations,
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


async def _persist_parse_failure(
    factory: Any,
    *,
    doc_id: str,
    error_kind: str,
    error_message: str,
) -> None:
    """Merge a typed parse-failure into the documents row.

    Previously the parse failure path just flipped status to
    PARSE_FAILED with no payload; the UI showed a vague "Failed" pill
    and the user had no path to retry. Now we merge error_kind /
    error_message into extraction_data so the web app can surface a
    real reason + a working Retry button.
    """
    async with factory() as s:
        try:
            existing = (
                await s.execute(
                    text(
                        "SELECT extraction_data FROM documents WHERE id = :id"
                    ),
                    {"id": doc_id},
                )
            ).first()
            existing_data: dict[str, Any] = {}
            if existing is not None:
                raw = existing._mapping.get("extraction_data")
                if isinstance(raw, dict):
                    existing_data = dict(raw)
                elif isinstance(raw, str) and raw:
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, dict):
                            existing_data = dict(parsed)
                    except (json.JSONDecodeError, TypeError):
                        pass
            existing_data["error_kind"] = error_kind
            existing_data["error_message"] = error_message
            await s.execute(
                text(
                    "UPDATE documents SET status = :s, "
                    "extraction_data = :d WHERE id = :id"
                ),
                {
                    "s": DOC_STATUS_PARSE_FAILED,
                    "d": json.dumps(existing_data),
                    "id": doc_id,
                },
            )
            await s.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "parse_async: failed to record PARSE_FAILED for %s", doc_id
            )


async def _find_duplicate_document(
    session: AsyncSession,
    *,
    deal_id: str,
    content_hash: str,
) -> dict[str, Any] | None:
    """Return the existing documents row matching (deal_id, content_hash),
    or ``None`` if this is a fresh upload. Lets the batch upload loop
    skip a re-upload without re-running parse/extract.
    """
    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT id, deal_id, tenant_id, filename, doc_type,
                           status, uploaded_at, content_hash,
                           storage_key, size_bytes, page_count, parser,
                           extraction_data, usali_score, usali_deviations
                      FROM documents
                     WHERE deal_id = :deal AND content_hash = :h
                     ORDER BY uploaded_at DESC
                     LIMIT 1
                    """
                ),
                {"deal": deal_id, "h": content_hash},
            )
        ).first()
    except Exception:
        return None
    if row is None:
        return None
    return dict(row._mapping)


def _failed_upload_record(
    *,
    deal_id: UUID,
    tenant_id: UUID,
    filename: str,
    error_kind: str,
    error_message: str,
) -> DocumentRecord:
    """Build a synthetic FAILED DocumentRecord for an upload that never
    made it into the documents table (empty body, storage failure,
    DB insert failure). The web app reads ``error_kind`` / ``error_message``
    and can surface a per-file row with a Retry button.
    """
    now = _now()
    return DocumentRecord(
        id=uuid4(),
        deal_id=deal_id,
        tenant_id=tenant_id,
        filename=filename,
        doc_type=_guess_doc_type(filename),
        status=DOC_STATUS_FAILED,
        uploaded_at=now,
        content_hash=None,
        storage_key=None,
        size_bytes=0,
        page_count=None,
        parser=None,
        error_kind=error_kind,
        error_message=error_message,
    )


# ─────────────────────────── upload ───────────────────────────


@router.post(
    "/{deal_id}/documents/upload",
    response_model=list[DocumentRecord],
    status_code=status.HTTP_201_CREATED,
)
async def upload_documents(
    deal_id: UUID,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    files: list[UploadFile] = File(...),
) -> list[DocumentRecord]:
    """Persist one-or-more documents against ``deal_id`` and kick off
    parse + extract in the background.

    Accepts ``.pdf`` (OMs / T-12s / CBRE / benchmark reports) and
    ``.xls`` / ``.xlsx`` (STR CoStar Trend exports). Each file is
    hashed and written to the raw store synchronously (cheap), but the
    actual parse + LLM extraction runs as a background task so dense
    OMs don't blow through the proxy's HTTP timeout (Sam QA re-test
    #2). The route returns 201 immediately with each row at status
    ``PARSING``; the background pipeline drives the row through
    ``PARSING → UPLOADED → CLASSIFYING → EXTRACTING → EXTRACTED``. The
    web app just polls /documents.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one file is required",
        )

    settings = get_settings()
    tenant_id_str = str(tenant_id)
    store = get_raw_store(settings)
    records: list[DocumentRecord] = []
    pending_parse: list[tuple[str, str, str, bytes]] = []

    # Per-file try/except — Rani's QA: uploading a batch that contained
    # a previously-uploaded file would HTTPException mid-loop and leave
    # the surviving files unprocessed. Now each file gets its own
    # outcome (created / duplicate / empty / storage_failed) and the
    # batch never aborts as a whole.
    for upload in files:
        filename = upload.filename or "upload.pdf"
        try:
            body = await upload.read()
        except Exception as exc:  # noqa: BLE001
            logger.exception("upload: read failed for %s", filename)
            records.append(
                _failed_upload_record(
                    deal_id=deal_id,
                    tenant_id=tenant_id,
                    filename=filename,
                    error_kind="read_failed",
                    error_message=str(exc) or "Failed to read uploaded file body.",
                )
            )
            continue
        if not body:
            records.append(
                _failed_upload_record(
                    deal_id=deal_id,
                    tenant_id=tenant_id,
                    filename=filename,
                    error_kind="empty",
                    error_message="Uploaded file is empty — re-export and try again.",
                )
            )
            continue

        content_hash = hashlib.sha256(body).hexdigest()

        # Dedup by (deal_id, content_hash). Same bytes already uploaded
        # for this deal → return the existing row with a "duplicate"
        # marker so the UI can surface it without re-running extraction.
        existing = await _find_duplicate_document(
            session,
            deal_id=str(deal_id),
            content_hash=content_hash,
        )
        if existing is not None:
            existing_record = _row_to_record(existing)
            existing_record_dict = existing_record.model_dump()
            # Don't disturb the existing row's status; just flag it so
            # the UI can show "Already uploaded" without re-processing.
            existing_record = DocumentRecord(**{
                **existing_record_dict,
                "error_kind": existing_record.error_kind or "duplicate",
                "error_message": (
                    existing_record.error_message
                    or f"This file was already uploaded on "
                    f"{existing.get('uploaded_at', 'a prior session')}. "
                    "Skipped to avoid re-processing."
                ),
            })
            records.append(existing_record)
            continue

        try:
            storage_key = await store.put(
                tenant_id=tenant_id_str,
                deal_id=str(deal_id),
                content_hash=content_hash,
                filename=filename,
                bytes_=body,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("upload: store put failed for %s", filename)
            records.append(
                _failed_upload_record(
                    deal_id=deal_id,
                    tenant_id=tenant_id,
                    filename=filename,
                    error_kind="storage_failed",
                    error_message=str(exc) or "Raw storage write failed.",
                )
            )
            continue

        doc_id = uuid4()
        doc_type = _guess_doc_type(filename, size_bytes=len(body))
        uploaded_at = _now()

        try:
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
                    "tenant_id": tenant_id_str,
                    "filename": filename,
                    "doc_type": doc_type,
                    "status": DOC_STATUS_PARSING,
                    "uploaded_at": uploaded_at,
                    "content_hash": content_hash,
                    "storage_key": storage_key,
                    "size_bytes": len(body),
                    "page_count": None,
                    "parser": None,
                    "extraction_data": None,
                },
            )
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.exception("upload: INSERT failed for %s", filename)
            records.append(
                _failed_upload_record(
                    deal_id=deal_id,
                    tenant_id=tenant_id,
                    filename=filename,
                    error_kind="db_insert_failed",
                    error_message=str(exc) or "Database insert failed.",
                )
            )
            continue

        records.append(
            DocumentRecord(
                id=doc_id,
                deal_id=deal_id,
                tenant_id=tenant_id,
                filename=filename,
                doc_type=doc_type,
                status=DOC_STATUS_PARSING,
                uploaded_at=uploaded_at,
                content_hash=content_hash,
                storage_key=storage_key,
                size_bytes=len(body),
                page_count=None,
                parser=None,
            )
        )
        pending_parse.append((str(doc_id), str(deal_id), tenant_id_str, body))

    # Schedule parse + auto-extract for every freshly inserted doc.
    #
    # IMPORTANT: FastAPI's BackgroundTasks runs added tasks
    # SEQUENTIALLY, not concurrently — N add_task() calls means doc 2
    # waits for doc 1 to fully parse+extract before it even starts
    # (Sam QA 2026-05-14: a 2-doc upload took 339s wall time because
    # the OM and the P&L serialized). We schedule ONE batch task that
    # fans out across all docs with asyncio.gather, so a 2-doc upload
    # runs in max(doc1, doc2) wall time instead of the sum.
    batch = [
        {
            "doc_id": doc_id,
            "deal_id": deal_id_str,
            "tenant_id": tenant_id_str_,
            "body": body_bytes,
            "filename": next(
                r.filename for r in records if str(r.id) == doc_id
            ),
        }
        for doc_id, deal_id_str, tenant_id_str_, body_bytes in pending_parse
    ]
    background_tasks.add_task(_run_parse_and_extract_batch, batch=batch)

    logger.info(
        "documents.upload: deal=%s tenant=%s files=%d (parse async)",
        deal_id,
        tenant_id_str,
        len(records),
    )
    return records


# Max documents parsed+extracted concurrently within one upload
# batch. Each doc internally fans out its own chunked extraction
# (4-wide), so 2 docs × 4 chunks = up to 8 concurrent Sonnet calls —
# comfortably under Anthropic's rate limit while still cutting a
# multi-doc upload's wall time to ~max(doc) instead of sum(docs).
_PARSE_BATCH_CONCURRENCY = 2


async def _run_parse_and_extract_batch(*, batch: list[dict[str, Any]]) -> None:
    """Parse + extract every doc in an upload batch concurrently.

    FastAPI BackgroundTasks runs scheduled tasks sequentially, so the
    upload endpoint schedules exactly ONE of these per request and we
    fan out internally with asyncio.gather + a concurrency cap. Each
    doc is fully independent — a failure on one never blocks another
    (``_run_parse_and_extract`` already swallows + records its own
    errors).
    """
    import asyncio

    sem = asyncio.Semaphore(_PARSE_BATCH_CONCURRENCY)

    async def _one(item: dict[str, Any]) -> None:
        async with sem:
            try:
                await _run_parse_and_extract(
                    doc_id=item["doc_id"],
                    deal_id=item["deal_id"],
                    tenant_id=item["tenant_id"],
                    body=item["body"],
                    filename=item["filename"],
                )
            except Exception:  # noqa: BLE001 - never let one doc kill the batch
                logger.exception(
                    "parse_batch: unhandled error for doc=%s",
                    item.get("doc_id"),
                )

    await asyncio.gather(*(_one(item) for item in batch))


async def _run_parse_and_extract(
    *,
    doc_id: str,
    deal_id: str,
    tenant_id: str,
    body: bytes,
    filename: str,
) -> None:
    """Background pipeline: parse → write extraction_data → run extraction.

    Runs entirely off the request loop so a slow LlamaParse on a dense
    OM never trips the proxy's HTTP timeout (Sam QA re-test #2). On
    parse failure we mark the row ``PARSE_FAILED`` and stop — the
    document is still queryable, just unusable for extraction until
    re-uploaded or re-parsed manually.
    """
    factory = get_session_factory()
    extraction_data_to_persist: dict[str, Any] | None = None
    page_count_to_persist: int | None = None
    parser_label_to_persist: str | None = None

    try:
        parsed = await parse_document(body, filename)
        page_count_to_persist = parsed.total_pages
        parser_label_to_persist = parsed.parser
        extraction_data_to_persist = {
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
            "parse_async: ParseError for doc=%s (%s) — marking PARSE_FAILED",
            doc_id,
            exc,
        )
        await _persist_parse_failure(
            factory,
            doc_id=doc_id,
            error_kind="parse",
            error_message=(
                str(exc)
                or "The parser could not read this file. It may be image-"
                "only / scanned, password-protected, or corrupt. Re-export "
                "the file (or rotate to a text-based PDF) and click Retry."
            ),
        )
        return
    except Exception as exc:  # noqa: BLE001 — never crash a background task
        logger.exception(
            "parse_async: unexpected error for doc=%s — %s", doc_id, exc
        )
        await _persist_parse_failure(
            factory,
            doc_id=doc_id,
            error_kind="parse_unexpected",
            error_message=(
                str(exc)
                or "An unexpected error occurred while parsing this file. "
                "Check the worker logs for stack trace details, then click Retry."
            ),
        )
        return

    # Persist parse result and flip status to UPLOADED.
    async with factory() as s:
        try:
            await s.execute(
                text(
                    """
                    UPDATE documents
                       SET status = :s,
                           page_count = :pc,
                           parser = :parser,
                           extraction_data = :data
                     WHERE id = :id
                    """
                ),
                {
                    "s": DOC_STATUS_UPLOADED,
                    "pc": page_count_to_persist,
                    "parser": parser_label_to_persist,
                    "data": json.dumps(extraction_data_to_persist),
                    "id": doc_id,
                },
            )
            await s.commit()
        except Exception:  # noqa: BLE001
            logger.exception(
                "parse_async: failed to persist parsed data for doc=%s", doc_id
            )
            return

    # Index the parsed pages into the context store (chunks + optional
    # Voyage embeddings). Best-effort — failures are logged but don't
    # block the rest of the extract pipeline; chunks can be backfilled
    # by re-uploading or by a future repair job.
    try:
        from ..extraction.context_store import index_parsed_document
        async with factory() as s:
            await index_parsed_document(
                s,
                deal_id=deal_id,
                tenant_id=tenant_id,
                document_id=doc_id,
                parsed=parsed,
            )
    except Exception:  # noqa: BLE001 — best-effort
        logger.exception(
            "parse_async: context_store indexing failed for doc=%s "
            "(non-fatal — extraction continues)",
            doc_id,
        )

    # Auto-chain extraction so the user sees a single status timeline
    # without having to call a second endpoint. Failures inside
    # ``_run_extraction_pipeline`` are already caught and recorded as
    # ``FAILED`` on the row.
    await _run_extraction_pipeline(
        deal_id=deal_id,
        doc_id=doc_id,
        tenant_id=tenant_id,
    )


# ─────────────────────────── list ───────────────────────────


class SearchHit(BaseModel):
    """One result from the deal's context-store search.

    Returned by GET /deals/{deal_id}/search. Each hit points at a
    specific chunk of a specific document so the UI can deep-link to
    the source page for citation.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    document_id: str
    chunk_index: int
    chunk_text: str
    source_page: int | None = None
    score: float


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    query: str
    hits: list[SearchHit] = Field(default_factory=list)
    # True when Voyage embeddings ran for this query (hybrid ranking).
    # False when only FTS contributed — search still works but recall
    # is lower for semantic-similarity queries.
    embeddings_used: bool = False


@router.get("/{deal_id}/search", response_model=SearchResponse)
async def search_deal_chunks(
    deal_id: UUID,
    q: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    k: int = 10,
) -> SearchResponse:
    """Search across every chunked document on a deal.

    Phase 3 of the dynamic-extensibility refactor. Backs the data-room
    search box + any future agent tool that needs to ground answers in
    arbitrary document text.

    Ranking:
        * Always: Postgres FTS via ``plainto_tsquery('english', q)``.
        * When VOYAGE_API_KEY is set + chunks have been embedded for
          this deal: hybrid score = 0.6 × cosine + 0.4 × FTS rank.
    """
    from ..extraction import embeddings
    from ..extraction.context_store import search_chunks

    if not q or not q.strip():
        return SearchResponse(deal_id=deal_id, query=q or "", hits=[])

    k = max(1, min(k, 50))
    hits = await search_chunks(
        session,
        deal_id=str(deal_id),
        query=q.strip(),
        k=k,
    )
    return SearchResponse(
        deal_id=deal_id,
        query=q.strip(),
        hits=[SearchHit(**h) for h in hits],
        embeddings_used=embeddings.is_enabled(),
    )


class BackfillEmbeddingsResponse(BaseModel):
    """Response shape for the embeddings backfill endpoint."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID | None = None
    chunks_total: int = 0
    chunks_missing: int = 0
    chunks_embedded: int = 0
    chunks_failed: int = 0
    skipped_reason: str | None = None


@router.post(
    "/{deal_id}/backfill_embeddings",
    response_model=BackfillEmbeddingsResponse,
)
async def backfill_embeddings(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    batch_size: int = 64,
) -> BackfillEmbeddingsResponse:
    """Embed any chunks on this deal that still have NULL embedding.

    Use case: chunks written before VOYAGE_API_KEY was set, or written
    when a Voyage call failed (the chunking pipeline persists chunks
    with embedding=NULL on failure so search still works via FTS).
    This endpoint walks the deal's chunks in batches and writes the
    missing embeddings back.

    Idempotent — chunks whose embedding is already populated are
    skipped silently.
    """
    from ..extraction import embeddings
    from ..extraction.context_store import _table_exists, _to_vector_literal

    if not await _table_exists(session):
        return BackfillEmbeddingsResponse(
            deal_id=deal_id,
            skipped_reason="document_chunks table not present (SQLite or pgvector unavailable)",
        )

    if not embeddings.is_enabled():
        return BackfillEmbeddingsResponse(
            deal_id=deal_id,
            skipped_reason="VOYAGE_API_KEY not set on the worker",
        )

    # Verify deal exists + tenant authorization.
    row = (
        await session.execute(
            text(
                "SELECT id FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )

    # Total + missing counts (for the response).
    counts = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN embedding IS NULL THEN 1 ELSE 0 END) AS missing
                  FROM document_chunks
                 WHERE deal_id = :deal
                """
            ),
            {"deal": str(deal_id)},
        )
    ).first()
    total = int(counts._mapping["total"] or 0) if counts else 0
    missing = int(counts._mapping["missing"] or 0) if counts else 0

    embedded = 0
    failed = 0
    batch_size = max(1, min(batch_size, embeddings.VOYAGE_MAX_BATCH))

    while True:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT id, chunk_text
                      FROM document_chunks
                     WHERE deal_id = :deal AND embedding IS NULL
                     ORDER BY document_id, chunk_index
                     LIMIT :n
                    """
                ),
                {"deal": str(deal_id), "n": batch_size},
            )
        ).fetchall()
        if not rows:
            break

        ids = [str(r._mapping["id"]) for r in rows]
        texts = [r._mapping["chunk_text"] for r in rows]

        try:
            vectors = await embeddings.embed_batch(texts, input_type="document")
        except Exception as exc:  # noqa: BLE001 — batch-level failure
            logger.warning(
                "backfill_embeddings: Voyage batch failed for deal=%s "
                "(%s); skipping these %d chunks",
                deal_id,
                exc,
                len(rows),
            )
            failed += len(rows)
            # Mark these specific chunks as "tried" by inserting an
            # empty-vector-equivalent? No — just break so we don't
            # loop infinitely. Caller can retry.
            break

        for cid, vec in zip(ids, vectors, strict=True):
            await session.execute(
                text(
                    "UPDATE document_chunks SET embedding = CAST(:vec AS vector) "
                    "WHERE id = :id"
                ),
                {"id": cid, "vec": _to_vector_literal(vec)},
            )
        await session.commit()
        embedded += len(rows)
        logger.info(
            "backfill_embeddings: deal=%s embedded=%d (this batch=%d)",
            deal_id,
            embedded,
            len(rows),
        )

    return BackfillEmbeddingsResponse(
        deal_id=deal_id,
        chunks_total=total,
        chunks_missing=missing,
        chunks_embedded=embedded,
        chunks_failed=failed,
    )


@router.get("/{deal_id}/documents", response_model=list[DocumentRecord])
async def list_documents(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> list[DocumentRecord]:
    """List documents on a deal in upload-recent order."""
    rows = await session.execute(
        text(
            """
            SELECT id, deal_id, tenant_id, filename, doc_type, status,
                   uploaded_at, content_hash, storage_key, size_bytes,
                   page_count, parser, extraction_data,
                   usali_score, usali_deviations
              FROM documents
             WHERE deal_id = :deal_id
               AND tenant_id = :tenant
             ORDER BY uploaded_at DESC
            """
        ),
        {"deal_id": str(deal_id), "tenant": str(tenant_id)},
    )
    return [_row_to_record(dict(r._mapping)) for r in rows.fetchall()]


# ─────────────────────────── market data aggregator ───────────────────────────


def _coerce_float(v: Any) -> float | None:
    """Best-effort numeric coerce for extraction values.

    The Extractor agent normally emits numeric scalars, but we tolerate
    string forms ("1.05", "75.4%", "$312") so a slightly off-spec
    upstream agent doesn't blank out the Market tab.
    """
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "")
        pct = s.endswith("%")
        if pct:
            s = s[:-1]
        try:
            f = float(s)
        except ValueError:
            return None
        return f / 100.0 if pct else f
    return None


def _coerce_int(v: Any) -> int | None:
    f = _coerce_float(v)
    if f is None:
        return None
    try:
        return int(round(f))
    except (TypeError, ValueError):
        return None


def _aggregate_market_data(
    rows: list[dict[str, Any]], deal_id: UUID
) -> MarketDataResponse:
    """Walk extraction_results × documents rows and bucket fields by
    doc_type into the three external-report blocks.

    Each input row is ``{"fields": <list>, "doc_type": <str>,
    "document_id": <uuid>}``. Most-recent extraction for each doc_type
    wins because the caller orders by ``created_at DESC`` and we only
    set a slot the first time we see it.
    """
    str_trend_fields: dict[str, Any] = {}
    cbre_fields: dict[str, Any] = {}
    pnl_fields: dict[str, Any] = {}
    # Comp-set rows are keyed by their numeric index (1..7).
    compset_rows: dict[int, dict[str, Any]] = {}
    sources: dict[str, list[UUID]] = {}

    for row in rows:
        doc_type = (row.get("doc_type") or "").upper()
        doc_id_raw = row.get("document_id")
        try:
            doc_id_uuid = (
                doc_id_raw if isinstance(doc_id_raw, UUID)
                else UUID(str(doc_id_raw))
            )
        except (TypeError, ValueError):
            doc_id_uuid = None

        raw_fields = row.get("fields")
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields)
            except json.JSONDecodeError:
                continue
        if not isinstance(raw_fields, list):
            continue

        if doc_type not in {"STR_TREND", "CBRE_HORIZONS", "PNL_BENCHMARK"}:
            continue
        if doc_id_uuid is not None:
            sources.setdefault(doc_type, [])
            if doc_id_uuid not in sources[doc_type]:
                sources[doc_type].append(doc_id_uuid)

        for f in raw_fields:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip()
            if not name:
                continue
            value = f.get("value")
            if doc_type == "STR_TREND":
                _bucket_str_trend(name, value, str_trend_fields, compset_rows)
            elif doc_type == "CBRE_HORIZONS":
                _bucket_cbre(name, value, cbre_fields)
            elif doc_type == "PNL_BENCHMARK":
                _bucket_pnl(name, value, pnl_fields)

    str_trend = _build_str_trend_block(str_trend_fields, compset_rows)
    cbre = _build_cbre_block(cbre_fields)
    pnl = _build_pnl_block(pnl_fields)

    return MarketDataResponse(
        deal_id=deal_id,
        str_trend=str_trend,
        cbre_horizons=cbre,
        pnl_benchmark=pnl,
        sources=sources,
    )


def _bucket_str_trend(
    name: str,
    value: Any,
    flat: dict[str, Any],
    compset: dict[int, dict[str, Any]],
) -> None:
    """Sort STR_TREND field paths into the flat block or one of the
    indexed compset rows."""
    lname = name.lower()
    # Compset rows look like ``ttm_performance.compset.<n>.<attr>``.
    if lname.startswith("ttm_performance.compset."):
        rest = lname[len("ttm_performance.compset.") :]
        try:
            idx_str, attr = rest.split(".", 1)
            idx = int(idx_str)
        except (ValueError, IndexError):
            return
        compset.setdefault(idx, {})[attr] = value
        return
    # Subject + indices + rollups land in the flat dict so the most
    # recent extraction's value wins (don't overwrite if already set
    # because rows arrive newest-first).
    flat.setdefault(lname, value)


def _bucket_cbre(name: str, value: Any, flat: dict[str, Any]) -> None:
    flat.setdefault(name.lower(), value)


def _bucket_pnl(name: str, value: Any, flat: dict[str, Any]) -> None:
    flat.setdefault(name.lower(), value)


def _build_str_trend_block(
    flat: dict[str, Any],
    compset: dict[int, dict[str, Any]],
) -> StrTrendBlock | None:
    if not flat and not compset:
        return None
    block = StrTrendBlock(
        subject_occupancy_pct=_coerce_float(flat.get("ttm_performance.subject.occupancy_pct")),
        subject_adr_usd=_coerce_float(flat.get("ttm_performance.subject.adr_usd")),
        subject_revpar_usd=_coerce_float(flat.get("ttm_performance.subject.revpar_usd")),
        rgi_revpar_index=_coerce_float(flat.get("ttm_performance.indices.rgi_revpar_index")),
        ari_adr_index=_coerce_float(flat.get("ttm_performance.indices.ari_adr_index")),
        mpi_occupancy_index=_coerce_float(flat.get("ttm_performance.indices.mpi_occupancy_index")),
        comp_set_size=_coerce_int(flat.get("comp_set.comp_set_size")),
        total_keys=_coerce_int(flat.get("comp_set.total_keys")),
        compset=[
            CompSetEntry(
                name=str(compset[i].get("name")) if compset[i].get("name") is not None else None,
                keys=_coerce_int(compset[i].get("keys")),
                occupancy_pct=_coerce_float(compset[i].get("occupancy_pct")),
                adr_usd=_coerce_float(compset[i].get("adr_usd")),
                revpar_usd=_coerce_float(compset[i].get("revpar_usd")),
            )
            for i in sorted(compset.keys())
        ],
    )
    return block


def _build_cbre_block(flat: dict[str, Any]) -> CbreHorizonsBlock | None:
    if not flat:
        return None
    years: list[CbreYearProjection] = []
    for n in (1, 2, 3, 4, 5):
        prefix = f"cbre_horizons.year_{n}."
        occ = _coerce_float(flat.get(prefix + "occupancy_pct"))
        adr = _coerce_float(flat.get(prefix + "adr_usd"))
        revpar = _coerce_float(flat.get(prefix + "revpar_usd"))
        growth = _coerce_float(flat.get(prefix + "revpar_growth_pct"))
        if any(v is not None for v in (occ, adr, revpar, growth)):
            years.append(
                CbreYearProjection(
                    year_index=n,
                    occupancy_pct=occ,
                    adr_usd=adr,
                    revpar_usd=revpar,
                    revpar_growth_pct=growth,
                )
            )
    submarket = flat.get("cbre_horizons.submarket")
    chain_scale = flat.get("cbre_horizons.chain_scale")
    pub_date = flat.get("cbre_horizons.publication_date")
    return CbreHorizonsBlock(
        submarket=str(submarket) if submarket is not None else None,
        chain_scale=str(chain_scale) if chain_scale is not None else None,
        publication_date=str(pub_date) if pub_date is not None else None,
        years=years,
    )


def _build_pnl_block(flat: dict[str, Any]) -> PnlBenchmarkBlock | None:
    if not flat:
        return None
    return PnlBenchmarkBlock(
        peer_set_size=_coerce_int(flat.get("pnl_benchmark.peer_set_size")),
        rooms_dept_pct=_coerce_float(flat.get("pnl_benchmark.rooms_dept_pct")),
        fb_dept_margin=_coerce_float(flat.get("pnl_benchmark.fb_dept_margin")),
        gop_margin=_coerce_float(flat.get("pnl_benchmark.gop_margin")),
        a_and_g_pct=_coerce_float(flat.get("pnl_benchmark.a_and_g_pct")),
        sales_marketing_pct=_coerce_float(flat.get("pnl_benchmark.sales_marketing_pct")),
        utilities_pct=_coerce_float(flat.get("pnl_benchmark.utilities_pct")),
        property_taxes_pct=_coerce_float(flat.get("pnl_benchmark.property_taxes_pct")),
        insurance_pct=_coerce_float(flat.get("pnl_benchmark.insurance_pct")),
        rooms_revenue_par=_coerce_float(flat.get("pnl_benchmark.rooms_revenue_par")),
        total_revenue_par=_coerce_float(flat.get("pnl_benchmark.total_revenue_par")),
        noi_par=_coerce_float(flat.get("pnl_benchmark.noi_par")),
        rooms_revenue_por=_coerce_float(flat.get("pnl_benchmark.rooms_revenue_por")),
        fb_revenue_por=_coerce_float(flat.get("pnl_benchmark.fb_revenue_por")),
    )


@router.get("/{deal_id}/market-data", response_model=MarketDataResponse)
async def get_market_data(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> MarketDataResponse:
    """Aggregate the deal's external-report extractions into one envelope.

    Reads ``extraction_results`` joined to ``documents`` filtered to
    ``doc_type IN ('STR_TREND', 'CBRE_HORIZONS', 'PNL_BENCHMARK')``
    and folds the field rows into three typed blocks plus a sources
    map for citation deep-links. Every block is optional — when the
    deal has no extracted document of a given type the block is
    ``null`` so the Market tab can render "awaiting <doc>" cards
    without a 404 path.
    """
    rows = await session.execute(
        text(
            """
            SELECT er.fields,
                   er.document_id,
                   d.doc_type
              FROM extraction_results er
              JOIN documents d ON d.id = er.document_id
             WHERE er.deal_id = :deal
               AND er.tenant_id = :tenant
               AND d.tenant_id = :tenant
               AND UPPER(COALESCE(d.doc_type, '')) IN (
                   'STR_TREND', 'CBRE_HORIZONS', 'PNL_BENCHMARK'
               )
             ORDER BY er.created_at DESC
            """
        ),
        {"deal": str(deal_id), "tenant": str(tenant_id)},
    )
    materialized = [dict(r._mapping) for r in rows.fetchall()]
    return _aggregate_market_data(materialized, deal_id)


# ─────────────────────────── download ───────────────────────────


@router.get("/{deal_id}/documents/{doc_id}/download")
async def download_document(
    deal_id: UUID,
    doc_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> Response:
    """Stream the raw uploaded file bytes back to the caller.

    Used by the citation side-pane's "See PDF" deep-link so reviewers
    can open the source document at the cited page (browsers honor
    ``#page=N`` anchors on application/pdf URLs). Inline disposition
    keeps the file in the viewer rather than forcing a download.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT filename, storage_key
                  FROM documents
                 WHERE id = :id
                   AND deal_id = :deal
                   AND tenant_id = :tenant
                """
            ),
            {"id": str(doc_id), "deal": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document {doc_id} not found on deal {deal_id}",
        )
    storage_key = row._mapping["storage_key"]
    filename = row._mapping["filename"] or "document.pdf"
    if not storage_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document {doc_id} has no stored bytes",
        )

    settings = get_settings()
    store = get_raw_store(settings)
    try:
        body = await store.get(storage_key)
    except FileNotFoundError as exc:
        # LocalRawStore raises StorageError, not FileNotFoundError, but
        # leaving this branch in case a future backend uses it directly.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"stored bytes missing for document {doc_id}",
        ) from exc
    except StorageError as exc:
        # Local backend raises this on missing-key. Treat as 404 so the
        # citation pane shows a clean "not found" instead of crashing.
        if "missing" in str(exc).lower():
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"stored bytes missing for document {doc_id}",
            ) from exc
        logger.exception("download: store.get failed for %s", storage_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"raw store read failed: {exc}",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("download: store.get failed for %s", storage_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"raw store read failed: {exc}",
        ) from exc

    media_type = "application/pdf" if filename.lower().endswith(".pdf") else "application/octet-stream"
    return Response(
        content=body,
        media_type=media_type,
        headers={
            # Inline so the browser's PDF viewer renders it (and honors
            # the ``#page=N`` anchor) instead of forcing a save dialog.
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "private, max-age=300",
        },
    )


# ─────────────────────────── extract ───────────────────────────


@router.post(
    "/{deal_id}/documents/{doc_id}/reprocess",
    response_model=ExtractionStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def reprocess_document(
    deal_id: UUID,
    doc_id: UUID,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ExtractionStartResponse:
    """Re-run parse + extract for a doc that previously hit PARSE_FAILED
    or FAILED. Pulls the body back from raw storage by content_hash and
    re-fires `_run_parse_and_extract`. Returns 404 if the row or the
    raw bytes are missing.

    Sam's QA reported that documents stuck in PARSE_FAILED have no
    user-driven retry path. This endpoint backs the Retry button in
    the Data Room KebabMenu.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, tenant_id, filename, content_hash,
                       storage_key, status
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
    m = row._mapping
    filename = m["filename"] or "upload.pdf"
    content_hash = m["content_hash"]
    tenant_id = str(m["tenant_id"])
    storage_key = m["storage_key"]

    if not content_hash or not storage_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "document missing storage metadata — re-upload the file "
                "instead of retrying."
            ),
        )

    settings = get_settings()
    store = get_raw_store(settings)
    try:
        body = await store.get(storage_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception("reprocess: store fetch failed for %s", filename)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not fetch raw bytes from storage: {exc}",
        ) from exc

    # Flip the row back to PARSING so the UI shows immediate feedback.
    # Clear any prior error_kind / error_message so a successful retry
    # doesn't display the stale failure reason.
    await session.execute(
        text(
            "UPDATE documents SET status = :s, extraction_data = NULL "
            "WHERE id = :id"
        ),
        {"s": DOC_STATUS_PARSING, "id": str(doc_id)},
    )
    await session.commit()

    job_id = uuid4()
    background_tasks.add_task(
        _run_parse_and_extract,
        doc_id=str(doc_id),
        deal_id=str(deal_id),
        tenant_id=tenant_id,
        body=body,
        filename=filename,
    )
    return ExtractionStartResponse(
        document_id=doc_id,
        job_id=str(job_id),
        status="reprocess_started",
    )


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
                """
                SELECT status, page_count, extraction_data
                  FROM documents
                 WHERE id = :id AND deal_id = :d
                """
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
    page_count_raw = doc_row._mapping.get("page_count")
    try:
        page_count_value: int | None = (
            int(page_count_raw) if page_count_raw is not None else None
        )
    except (TypeError, ValueError):
        page_count_value = None

    # Page text comes from the parser cache stashed at upload time —
    # citations need to deep-link to a page even before the LLM
    # Extractor has run, so we surface it whenever it exists.
    raw_extraction_data = doc_row._mapping.get("extraction_data")
    if isinstance(raw_extraction_data, str):
        try:
            extraction_data = json.loads(raw_extraction_data)
        except json.JSONDecodeError:
            extraction_data = None
    else:
        extraction_data = raw_extraction_data

    parsed_pages: dict[str, str] = {}
    for p in (extraction_data or {}).get("pages") or []:
        try:
            num = int(p.get("page_num", 0))
        except (TypeError, ValueError):
            continue
        if num < 1:
            continue
        page_text = p.get("text")
        if not isinstance(page_text, str) or not page_text.strip():
            continue
        parsed_pages[str(num)] = page_text

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
            parsed_pages=parsed_pages,
            page_count=page_count_value,
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
        parsed_pages=parsed_pages,
        page_count=page_count_value,
    )


# ─────────────────────── extraction error classifier ───────────────────


# User-facing copy for each error_kind. The web reads error_kind to pick
# a banner tone and CTA; this dict is the canonical source for the body
# text so we don't drift between server and client.
ERROR_KIND_MESSAGES: dict[str, str] = {
    "billing": (
        "Anthropic API credit balance is exhausted. Extractions are "
        "queued but cannot complete until the API key is topped up at "
        "console.anthropic.com → Billing."
    ),
    "auth": (
        "Anthropic API key is invalid or revoked. Update "
        "ANTHROPIC_API_KEY on the worker environment to resume "
        "extractions."
    ),
    "rate_limit": (
        "Anthropic rate-limit hit — extractions will retry "
        "automatically once the limit resets."
    ),
    "parse": (
        "The file couldn't be parsed (corrupt PDF, password-protected, "
        "or unsupported format). Re-upload or check the original file."
    ),
    "other": "Extraction failed. Check the worker logs for details.",
}


def _classify_extraction_error(exc: BaseException) -> tuple[str, str]:
    """Map an exception bubbling out of the extraction pipeline to a
    typed (error_kind, error_message) tuple the web UI can act on.

    The classifier walks both the exception itself and its ``__cause__``
    chain so an Anthropic API error wrapped by a higher-level
    BudgetExceeded / ValidationError still surfaces with the right
    kind. Falls back to ``other`` when no pattern matches.
    """
    cur: BaseException | None = exc
    text_blob = ""
    while cur is not None:
        text_blob += " " + repr(cur) + " " + str(cur)
        cur = cur.__cause__ or cur.__context__

    low = text_blob.lower()
    # Anthropic surfaces credit-balance as a 400 with a specific message.
    if (
        "credit balance is too low" in low
        or "credit balance too low" in low
        or "insufficient credit" in low
        or "budgetexceeded" in low
    ):
        kind = "billing"
    elif (
        "authentication" in low
        or "invalid x-api-key" in low
        or "invalid api key" in low
        or "401" in low and "anthropic" in low
    ):
        kind = "auth"
    elif (
        "rate_limit" in low
        or "rate limit" in low
        or "429" in low
    ):
        kind = "rate_limit"
    elif (
        "parseerror" in low
        or "could not parse" in low
        or "unsupported file extension" in low
    ):
        kind = "parse"
    else:
        kind = "other"

    return kind, ERROR_KIND_MESSAGES[kind]


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
                        "SELECT storage_key, filename, extraction_data FROM documents WHERE id = :id"
                    ),
                    {"id": doc_id},
                )
            ).first()
            if not row:
                raise RuntimeError(f"document {doc_id} vanished mid-extraction")
            storage_key = row._mapping["storage_key"]
            filename = row._mapping["filename"]
            raw_extraction_data = row._mapping["extraction_data"]
            if isinstance(raw_extraction_data, str):
                try:
                    extraction_data = json.loads(raw_extraction_data)
                except json.JSONDecodeError:
                    extraction_data = None
            else:
                extraction_data = raw_extraction_data

            classified_doc_type: str | None = None
            if os.environ.get("EVALS_MOCK", "").lower() in ("1", "true", "yes"):
                fields, confidence = _mock_extraction_payload()
                agent_version = "mock-evals"
            else:
                (
                    fields,
                    confidence,
                    agent_version,
                    classified_doc_type,
                ) = await _run_graph_extraction(
                    deal_id=deal_id,
                    tenant_id=tenant_id,
                    storage_key=storage_key,
                    doc_id=doc_id,
                    filename=filename,
                    extraction_data=extraction_data,
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
            # Sam QA 2026-05-13: a 0-field extraction is not a success.
            # Previously we marked the row EXTRACTED regardless of
            # field count; the UI then showed a green "Extracted" pill
            # and the right-panel fell back to mock KPIs ($385 ADR
            # etc.), making it look like extraction worked when it
            # hadn't. Now: zero fields → FAILED row with a typed
            # error_kind ("empty_envelope") merged into extraction_data
            # so the UI can surface the real reason.
            if not fields:
                # Differentiate "the parser couldn't get text" from "the
                # extractor ran on real text but found nothing matching
                # the schema." Sam QA 2026-05-30 surfaced both under the
                # same generic empty_envelope message which masked the
                # actionable distinction (a scanned PDF needs OCR
                # software; a misclassified xlsx needs reclassification).
                parsed_pages = (
                    extraction_data.get("pages", [])
                    if isinstance(extraction_data, dict) else []
                )
                total_chars = sum(
                    len((p.get("text", "") or "")) for p in parsed_pages
                    if isinstance(p, dict)
                )
                if total_chars < 100:
                    kind = "no_text"
                    friendly = (
                        "The parser couldn't extract any text from this "
                        "document — usually a scanned or image-only PDF "
                        "without an OCR layer. Run the file through an "
                        "OCR tool (Adobe Acrobat, ABBYY, or Tesseract) "
                        "and re-upload, or upload the source spreadsheet "
                        "instead of a screenshot."
                    )
                else:
                    kind = "empty_envelope"
                    friendly = (
                        "The extractor ran on real text but emitted no "
                        "grounded fields. The document may be classified "
                        "as the wrong type (e.g. a room-types lookup "
                        "table classified as T12), or the LLM hit a "
                        "structured-output edge case. Use Retry to "
                        "re-run, or check the assigned doc type matches "
                        "the content."
                    )
                logger.warning(
                    "extraction: doc=%s deal=%s produced 0 fields "
                    "(parsed_chars=%d) — marking FAILED with error_kind=%s",
                    doc_id,
                    deal_id,
                    total_chars,
                    kind,
                )
                # Merge error info into existing extraction_data so the
                # parse-stage payload (parser/total_pages/pages) stays
                # intact for forensics.
                existing_data: dict[str, Any] = {}
                if isinstance(extraction_data, dict):
                    existing_data = dict(extraction_data)
                existing_data["error_kind"] = kind
                existing_data["error_message"] = friendly
                await session.execute(
                    text(
                        "UPDATE documents SET status = :s, "
                        "extraction_data = :d WHERE id = :id"
                    ),
                    {
                        "s": DOC_STATUS_FAILED,
                        "d": json.dumps(existing_data),
                        "id": doc_id,
                    },
                )
                await session.commit()
            else:
                # Persist the Router agent's classification back to the
                # documents row when it differs from the filename-heuristic
                # ``doc_type`` set at upload. The downstream
                # ``_load_critic_inputs`` filters on
                # ``doc_type IN ('T12','PNL','PNL_MONTHLY','PNL_YTD')``
                # — without this update an OM that was filename-classified as
                # T12 would have its broker_proforma fields silently bucketed
                # as actuals (Sam QA #10 root cause).
                #
                # Then: narrow PNL/T12 → PNL_MONTHLY / PNL_YTD / T12
                # based on `p_and_l_usali.period_type` from the
                # extraction. The Router only sees filename + ~2k chars,
                # so it can't reliably distinguish a single month from
                # a full T-12 — but the Extractor pulls the period_type
                # off the table itself. Rani's QA flagged a May 2024
                # monthly P&L being treated as a T-12.
                refined_doc_type = _refine_pnl_doc_type(
                    classified_doc_type, fields
                )
                if refined_doc_type:
                    await session.execute(
                        text(
                            "UPDATE documents SET status = :s, doc_type = :dt WHERE id = :id"
                        ),
                        {
                            "s": DOC_STATUS_EXTRACTED,
                            "dt": refined_doc_type,
                            "id": doc_id,
                        },
                    )
                else:
                    await session.execute(
                        text("UPDATE documents SET status = :s WHERE id = :id"),
                        {"s": DOC_STATUS_EXTRACTED, "id": doc_id},
                    )
                await session.commit()
            logger.info(
                "extraction complete: doc=%s deal=%s fields=%d doc_type=%s",
                doc_id,
                deal_id,
                len(fields),
                classified_doc_type,
            )

            # Source-of-truth hierarchy (May 7 scope): documents >
            # wizard. When the OM / T-12 surfaces a property metadata
            # value (keys, brand, year_built, address) that contradicts
            # the deals row, prefer the document and write the change
            # to audit_log so a reviewer can see what shifted. The
            # wizard input is treated as a stale guess once a real
            # document arrives.
            await _sync_deal_metadata_from_extraction(
                session,
                deal_id=deal_id,
                tenant_id=tenant_id,
                fields=fields,
            )

            # Chain-of-verification — re-read each cited number against the
            # parser cache. Best-effort; never blocks completion.
            await _persist_verification_report(
                session,
                deal_id=deal_id,
                tenant_id=tenant_id,
                doc_id=doc_id,
                fields=fields,
                extraction_data=extraction_data,
            )

            # Cross-field critic pass — looks for narrative issues spanning
            # multiple fields (coastal insurance, NOI vs OpEx divergence,
            # etc.). Best-effort; never blocks completion.
            await _persist_critic_report(
                session,
                deal_id=deal_id,
                tenant_id=tenant_id,
            )

            # USALI compliance scoring (ROADMAP #3) — run the 66-rule
            # catalog against this document's extracted fields and
            # persist the score + deviations back onto the documents
            # row. Only P&L-family uploads get scored; OMs/STRs/etc.
            # don't carry the canonical P&L fields the rules check.
            # Best-effort: a failure here never blocks completion.
            scoring_doc_type = (
                refined_doc_type if "refined_doc_type" in locals()
                else (classified_doc_type or "")
            )
            await _persist_usali_score(
                session,
                deal_id=deal_id,
                doc_id=doc_id,
                doc_type=scoring_doc_type or "",
                fields=fields,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("extraction failed: doc=%s — %s", doc_id, exc)
            kind, friendly = _classify_extraction_error(exc)
            try:
                # Merge the error info into existing extraction_data so
                # the parse-stage payload (parser/total_pages/pages)
                # stays intact for forensics.
                existing_row = (
                    await session.execute(
                        text("SELECT extraction_data FROM documents WHERE id = :id"),
                        {"id": doc_id},
                    )
                ).first()
                existing_data: dict[str, Any] = {}
                if existing_row is not None:
                    raw_ed = existing_row._mapping.get("extraction_data")
                    if isinstance(raw_ed, str) and raw_ed:
                        try:
                            existing_data = json.loads(raw_ed)
                        except (json.JSONDecodeError, TypeError):
                            existing_data = {}
                    elif isinstance(raw_ed, dict):
                        existing_data = raw_ed
                existing_data["error_kind"] = kind
                existing_data["error_message"] = friendly
                existing_data["error_raw"] = str(exc)[:500]
                await session.execute(
                    text(
                        "UPDATE documents "
                        "SET status = :s, extraction_data = :d "
                        "WHERE id = :id"
                    ),
                    {
                        "s": DOC_STATUS_FAILED,
                        "d": json.dumps(existing_data),
                        "id": doc_id,
                    },
                )
                await session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("extraction: failed to record FAILED status")


# Pages per extraction chunk. A 45-page OM → 9 chunks; a 3-page T-12
# → 1 chunk (unchanged behavior). Tuned so each chunk's content stays
# comfortably under the extractor prompt's content budget and the LLM
# isn't reasoning over too many pages at once.
_EXTRACTOR_CHUNK_PAGES = 5


def _build_extractor_chunks(
    *,
    pages: list[dict[str, Any]],
    doc_id: str,
    filename: str,
    doc_type: str,
    make_doc: Any,
) -> list[Any]:
    """Split a parsed document's pages into ~5-page ExtractorDocuments.

    All chunks share the same ``document_id`` / ``filename`` /
    ``doc_type`` — they're the same source document, just sliced so the
    extractor can fan out concurrently. ``make_doc`` is the
    ``ExtractorDocument`` constructor passed in to avoid a module-level
    import cycle. Returns at least one chunk even for an empty document
    so the extractor still runs (and reports 0 fields honestly).
    """
    if not pages:
        return [
            make_doc(
                document_id=doc_id,
                filename=filename,
                doc_type=doc_type,
                content=f"(empty document: {filename})",
                source_pages=[],
            )
        ]

    chunks: list[Any] = []
    for start in range(0, len(pages), _EXTRACTOR_CHUNK_PAGES):
        batch = pages[start : start + _EXTRACTOR_CHUNK_PAGES]
        content = "\n\n".join(
            f"[Page {p.get('page_num', start + i + 1)}]\n{p.get('text', '')}".strip()
            for i, p in enumerate(batch)
        )
        source_pages = [
            int(p.get("page_num", start + i + 1)) for i, p in enumerate(batch)
        ]
        chunks.append(
            make_doc(
                document_id=doc_id,
                filename=filename,
                doc_type=doc_type,
                content=content or f"(empty page range: {filename})",
                source_pages=source_pages,
            )
        )
    return chunks


async def _run_graph_extraction(
    *,
    deal_id: str,
    tenant_id: str,
    storage_key: str | None,
    doc_id: str,
    filename: str,
    extraction_data: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any], str, str]:
    """Drive Router → Extractor against a single uploaded document.

    Reads the parsed page text cached on the document row's
    ``extraction_data`` JSONB and feeds it to the Extractor as actual
    content (NOT a URI — the agent has no fetcher). Returns the
    flattened field list + rolled-up confidence + agent version string
    + the Router's classified doc_type (so the caller can update the
    documents row when the LLM disagrees with the filename heuristic).
    """
    from fondok_schemas import DocType

    from ..agents.extractor import (
        ExtractorDocument,
        ExtractorInput,
        run_extractor,
    )
    from ..agents.router import RouterInput, run_router

    # Reconstruct page text from parser cache.
    pages = (extraction_data or {}).get("pages") or []
    if not pages:
        logger.warning(
            "extraction: no parsed pages cached for doc=%s — extractor will see empty content",
            doc_id,
        )

    # Small content sample for the Router classification — first ~2
    # pages is plenty to recognize doc type without paying to
    # serialize the whole document.
    router_sample = "\n\n".join(
        (p.get("text", "") or "") for p in pages[:2]
    )[:2000]

    # Cheap filename-based doc-type hint passes to Router; agent confirms.
    hint = _guess_doc_type(filename)

    router_input = RouterInput(
        tenant_id=tenant_id,
        deal_id=deal_id,
        filename=filename,
        content_sample=router_sample,
    ) if "filename" in RouterInput.model_fields else RouterInput(
        tenant_id=tenant_id, deal_id=deal_id,
    )
    try:
        router_out = await run_router(router_input)
        doc_type = getattr(router_out, "doc_type", None) or hint
        route = getattr(router_out, "route", "extract")
    except Exception as exc:  # noqa: BLE001
        logger.warning("router failed for doc=%s — falling back to %s: %s", doc_id, hint, exc)
        doc_type = hint
        route = "extract-fallback"

    # The router returns 'UNKNOWN' as a sentinel when the LLM call
    # rate-limits, the credit balance is exhausted, or the model emits
    # an off-list value. 'UNKNOWN' is not a valid DocType enum member,
    # so passing it through to ExtractorDocument crashes Pydantic
    # validation and the upload row lands FAILED. Fall back to the
    # cheap filename hint instead so extraction still proceeds — the
    # downstream verifier and the user-facing doc_type column on the
    # documents row will reflect the hint, which is correct for the
    # 90% case where the filename actually carries the type.
    valid_types = {dt.value for dt in DocType}
    if doc_type not in valid_types:
        logger.warning(
            "router returned invalid doc_type=%r for doc=%s — falling back to filename hint=%s",
            doc_type,
            doc_id,
            hint,
        )
        doc_type = hint
        route = "extract-hint-fallback"

    # Chunked extraction (Sam QA 2026-05-14): split the document into
    # ~5-page batches and build one ExtractorDocument per chunk. The
    # extractor agent fans these out in parallel (capped concurrency),
    # so a 45-page OM that used to be a single 3-minute Sonnet call
    # becomes ~9 concurrent ~30s calls. Smaller per-call context also
    # lifts per-field confidence — the model isn't juggling 45 pages
    # at once. Small docs (≤ chunk size) produce a single chunk and
    # behave exactly as before.
    extractor_docs = _build_extractor_chunks(
        pages=pages,
        doc_id=doc_id,
        filename=filename,
        doc_type=doc_type,
        make_doc=ExtractorDocument,
    )
    extractor_input = ExtractorInput(
        tenant_id=tenant_id,
        deal_id=deal_id,
        documents=extractor_docs,
    )
    extractor_out = await run_extractor(extractor_input)

    # Merge chunk results. Each chunk emits its own field list +
    # confidence report; concatenating naively would double-count any
    # field two adjacent chunks both saw (e.g. a property name in a
    # header repeated across pages). Dedup by field_name, keeping the
    # highest-confidence instance.
    by_name: dict[str, dict[str, Any]] = {}
    for doc in extractor_out.extracted_documents or []:
        as_dict = doc.model_dump() if hasattr(doc, "model_dump") else dict(doc)
        for f in as_dict.get("fields", []) or []:
            fd = dict(f) if not isinstance(f, dict) else f
            name = fd.get("field_name")
            if not name:
                continue
            existing = by_name.get(name)
            if existing is None or (
                float(fd.get("confidence", 0) or 0)
                > float(existing.get("confidence", 0) or 0)
            ):
                by_name[name] = fd
    fields: list[dict[str, Any]] = list(by_name.values())

    # Recompute the rolled-up confidence over the deduped field set so
    # the documents-row confidence reflects the merged result, not a
    # single chunk's report.
    by_field_conf = {
        f["field_name"]: float(f.get("confidence", 0) or 0)
        for f in fields
        if f.get("field_name")
    }
    overall_conf = (
        sum(by_field_conf.values()) / len(by_field_conf)
        if by_field_conf
        else 0.0
    )
    low_conf = [n for n, c in by_field_conf.items() if c < 0.85]
    confidence: dict[str, Any] = {
        "overall": overall_conf,
        "by_field": by_field_conf,
        "low_confidence_fields": low_conf,
        "requires_human_review": overall_conf < 0.85 or not fields,
    }

    return fields, confidence, f"router:{route};extractor", doc_type


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


# ─────────────────────────── doc → deal metadata sync ───────────────────────────


# Map property-metadata field names emitted by the Extractor onto the
# canonical column on the ``deals`` table. Only fields that have a
# 1:1 column mapping are listed; ad-hoc property attributes (year
# built, GBA) live on the extraction row rather than the deals row
# until they justify a column.
_PROPERTY_METADATA_FIELD_TO_COL: dict[str, str] = {
    "property_overview.keys": "keys",
    "property_overview.brand": "brand",
    "property_overview.address": "city",
    "property_overview.submarket": "city",
    "property_overview.location": "city",
}


async def _sync_deal_metadata_from_extraction(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    fields: list[dict[str, Any]],
) -> None:
    """When an extracted document carries property-metadata values that
    differ from the deals-table row, prefer the document. Best-effort.

    Implements the May 7 scope rule: docs > wizard. The wizard input
    is a stale guess; the OM / T-12 carries the property's actual
    keys / brand / address. We update the deals row in place and
    write an ``audit_log`` entry per change so a reviewer can see
    what shifted. UUID guard, exception-tolerant — never blocks
    extraction completion.
    """
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return

    # Read the current deals row so we only UPDATE columns whose
    # extracted value actually contradicts what's there.
    try:
        row = (
            await session.execute(
                text("SELECT keys, brand, city FROM deals WHERE id = :id"),
                {"id": deal_id},
            )
        ).first()
    except Exception:  # noqa: BLE001 - best-effort
        return
    if row is None:
        return
    current = row._mapping
    proposed: dict[str, Any] = {}

    for f in fields:
        if not isinstance(f, dict):
            continue
        name = (f.get("field_name") or "").strip().lower()
        col = _PROPERTY_METADATA_FIELD_TO_COL.get(name)
        if col is None:
            continue
        value = f.get("value")
        if value in (None, "", 0):
            continue
        # Coerce to the column's expected type. Keys is int, brand /
        # city are text. Skip anything we can't cleanly cast.
        if col == "keys":
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
        else:
            value = str(value).strip()
            if not value:
                continue

        existing = current.get(col)
        if col == "keys":
            try:
                existing_int = int(existing) if existing is not None else None
            except (TypeError, ValueError):
                existing_int = None
            if existing_int == value:
                continue
        else:
            if existing and str(existing).strip().lower() == value.lower():
                continue

        # First write wins per column — if the OM and T-12 disagree
        # the OM's value lands first (loop order = SELECT order).
        proposed.setdefault(col, value)

    if not proposed:
        return

    # Build a single UPDATE so the change is atomic.
    set_clauses = ", ".join(f"{col} = :{col}" for col in proposed)
    params = {"id": deal_id, **proposed}
    try:
        await session.execute(
            text(f"UPDATE deals SET {set_clauses}, updated_at = NOW() WHERE id = :id")
            if not str(get_settings().async_database_url).startswith("sqlite")
            else text(
                f"UPDATE deals SET {set_clauses}, updated_at = CURRENT_TIMESTAMP WHERE id = :id"
            ),
            params,
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "deal_metadata_sync: failed to UPDATE deals for deal=%s changes=%s",
            deal_id,
            list(proposed.keys()),
        )
        return

    # Audit log — one entry per column change so reviewers see
    # provenance ("we changed keys from 200 → 132 because the OM
    # said so").
    try:
        from ..audit import log_audit

        for col, new_val in proposed.items():
            old_val = current.get(col)
            await log_audit(
                session,
                tenant_id=tenant_id,
                action="deal.metadata_synced_from_extraction",
                resource_type="deal",
                resource_id=deal_id,
                input_payload={
                    "column": col,
                    "old_value": (str(old_val) if old_val is not None else None),
                    "new_value": (str(new_val) if new_val is not None else None),
                    "rule": "docs > wizard (May 7 scope)",
                },
            )
        await session.commit()
    except Exception:  # noqa: BLE001
        # Audit failure shouldn't roll back the metadata fix.
        logger.warning(
            "deal_metadata_sync: audit_log write failed for deal=%s — change still applied",
            deal_id,
        )

    logger.info(
        "deal_metadata_sync: deal=%s applied=%s",
        deal_id,
        list(proposed.keys()),
    )


# ─────────────────────────── verification ───────────────────────────


async def _persist_verification_report(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    doc_id: str,
    fields: list[dict[str, Any]],
    extraction_data: dict[str, Any] | None,
) -> None:
    """Run the deterministic citation verifier and persist the report.

    Best-effort: any failure logs and returns silently — verification is
    a defense layer, never a hard gate. The persisted ``pass_rate`` lets
    the UI surface a single grounding-quality score next to the memo.
    """
    if extraction_data is None:
        return
    try:
        from datetime import datetime as _dt
        from fondok_schemas import ExtractionField
        from ..extraction.models import ParsedDocument, ParsedPage
        from ..verification import verify_citations

        # Reconstruct ParsedDocument from the cached extraction_data.
        pages = []
        for p in extraction_data.get("pages") or []:
            pages.append(
                ParsedPage(
                    page_num=int(p.get("page_num", 1)),
                    text=p.get("text", "") or "",
                    tables=p.get("tables") or [],
                    metadata=p.get("metadata") or {},
                )
            )
        if not pages:
            return
        parsed_at_raw = extraction_data.get("parsed_at")
        try:
            parsed_at = (
                _dt.fromisoformat(parsed_at_raw) if parsed_at_raw else _now()
            )
        except (TypeError, ValueError):
            parsed_at = _now()
        parsed_doc = ParsedDocument(
            filename=extraction_data.get("filename", "uploaded.pdf"),
            total_pages=int(extraction_data.get("total_pages", len(pages))),
            pages=pages,
            content_hash=extraction_data.get("content_hash", "0" * 64),
            parsed_at=parsed_at,
            parser=extraction_data.get("parser", "pymupdf"),
        )

        # Coerce raw field dicts → ExtractionField. Skip non-numeric or
        # malformed entries silently.
        ef_list: list[ExtractionField] = []
        field_doc_ids: dict[str, str] = {}
        for f in fields:
            try:
                ef = ExtractionField.model_validate(f)
            except Exception:
                continue
            ef_list.append(ef)
            field_doc_ids[ef.field_name] = doc_id

        report = verify_citations(
            ef_list,
            {doc_id: parsed_doc},
            deal_id=deal_id,
            field_doc_ids=field_doc_ids,
        )

        report_id = uuid4()
        await session.execute(
            text(
                """
                INSERT INTO verification_reports (
                    id, deal_id, tenant_id, pass_rate, report_json, created_at
                ) VALUES (
                    :id, :deal, :tenant, :pass_rate, :report, :created
                )
                """
            ),
            {
                "id": str(report_id),
                "deal": deal_id,
                "tenant": tenant_id,
                "pass_rate": float(report.pass_rate),
                "report": report.model_dump_json(),
                "created": _now(),
            },
        )
        await session.commit()
        logger.info(
            "verification: deal=%s doc=%s pass_rate=%.3f checks=%d",
            deal_id,
            doc_id,
            report.pass_rate,
            len(report.checks),
        )

        # Critic-promote (Sam QA 2026-05-14): the verifier just
        # re-read every cited number against its source page. A field
        # whose number was confirmed verbatim (MATCH) is as certain as
        # extraction gets — promote its confidence. CLOSE = within
        # tolerance, modest promote. MISMATCH = the cited number isn't
        # on the page, demote hard so the UI flags it for review.
        # UNVERIFIABLE (no parseable number on the page) is left
        # untouched. Best-effort: a failure here never blocks
        # extraction completion.
        try:
            await _apply_verification_to_confidence(
                session,
                deal_id=deal_id,
                doc_id=doc_id,
                fields=fields,
                checks=report.checks,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "verification: confidence-promote failed for doc=%s: %s",
                doc_id,
                exc,
            )
    except Exception as exc:  # noqa: BLE001 - never block extraction
        logger.warning(
            "verification: failed to persist report for doc=%s: %s",
            doc_id,
            exc,
        )


# Confidence floors/ceilings applied after the citation verifier
# re-reads each cited number against its source page.
_VERIFY_PROMOTE_MATCH = 0.98   # number confirmed verbatim on the page
_VERIFY_PROMOTE_CLOSE = 0.92   # confirmed within tolerance
_VERIFY_DEMOTE_MISMATCH = 0.50  # cited number absent from the page


async def _apply_verification_to_confidence(
    session: AsyncSession,
    *,
    deal_id: str,
    doc_id: str,
    fields: list[dict[str, Any]],
    checks: list[Any],
) -> None:
    """Re-write the extraction_results row with verifier-adjusted confidence.

    For every field the citation verifier re-read:
      * MATCH   → confidence floored to 0.98 (verbatim-confirmed)
      * CLOSE   → confidence floored to 0.92 (within tolerance)
      * MISMATCH→ confidence capped at 0.50 (cited number not on page)
      * UNVERIFIABLE / not-checked → untouched

    The row's ``confidence_report`` is recomputed over the adjusted
    field set so the documents-list confidence badge reflects the
    grounded score, not the raw LLM self-assessment.
    """
    from fondok_schemas import CitationStatus

    # field_name → verifier status. A field can appear once per doc.
    status_by_field: dict[str, Any] = {}
    for c in checks:
        fn = getattr(c, "field_name", None)
        st = getattr(c, "status", None)
        if fn and st is not None:
            status_by_field[fn] = st

    if not status_by_field:
        return

    adjusted = 0
    for f in fields:
        name = f.get("field_name")
        st = status_by_field.get(name)
        if st is None:
            continue
        cur = float(f.get("confidence", 0) or 0)
        if st == CitationStatus.MATCH:
            new = max(cur, _VERIFY_PROMOTE_MATCH)
        elif st == CitationStatus.CLOSE:
            new = max(cur, _VERIFY_PROMOTE_CLOSE)
        elif st == CitationStatus.MISMATCH:
            new = min(cur, _VERIFY_DEMOTE_MISMATCH)
        else:  # UNVERIFIABLE — leave as-is
            continue
        if abs(new - cur) > 1e-9:
            f["confidence"] = new
            adjusted += 1

    if adjusted == 0:
        return

    # Recompute the rolled-up confidence report over the adjusted set.
    by_field_conf = {
        f["field_name"]: float(f.get("confidence", 0) or 0)
        for f in fields
        if f.get("field_name")
    }
    overall = (
        sum(by_field_conf.values()) / len(by_field_conf)
        if by_field_conf
        else 0.0
    )
    confidence_report = {
        "overall": overall,
        "by_field": by_field_conf,
        "low_confidence_fields": [
            n for n, c in by_field_conf.items() if c < 0.85
        ],
        "requires_human_review": overall < 0.85 or not by_field_conf,
    }

    # Update the most-recent extraction_results row for this document.
    await session.execute(
        text(
            """
            UPDATE extraction_results
               SET fields = :fields,
                   confidence_report = :cr
             WHERE document_id = :doc
               AND deal_id = :deal
            """
        ),
        {
            "fields": json.dumps(fields),
            "cr": json.dumps(confidence_report),
            "doc": doc_id,
            "deal": deal_id,
        },
    )
    await session.commit()
    logger.info(
        "verification: promoted/demoted %d field confidences for doc=%s "
        "(new overall=%.3f)",
        adjusted,
        doc_id,
        overall,
    )


# ─────────────────────────── usali scoring ───────────────────────────


# Doc types that carry the canonical P&L fields the USALI catalog
# expects. Non-P&L docs (OMs, STR reports, market studies, …) lack the
# revenue / expense lines the rules check, so we don't score them —
# every applicable count would be 0 and the deviations list useless.
_PNL_FAMILY_DOC_TYPES: frozenset[str] = frozenset(
    {"T12", "PNL", "PNL_MONTHLY", "PNL_YTD"}
)


async def _persist_usali_score(
    session: AsyncSession,
    *,
    deal_id: str,
    doc_id: str,
    doc_type: str,
    fields: list[dict[str, Any]],
) -> None:
    """Score a P&L extraction against the USALI catalog and persist the
    score + deviations back to the documents row.

    Best-effort: any failure logs and returns silently — USALI scoring
    is additive intelligence on top of extraction, never a gate.

    The score persistence policy honors the Wave 1 product decision:

    * Document has <5 applicable rules → store NULL score with the
      ``inconclusive=True`` flag on the JSONB so the UI shows
      "Inconclusive (N rules)" instead of a misleading percent.
    * Document has ≥5 applicable rules → store the 0-100 percent.
    * Deviations are persisted in both cases (a 3-rule doc can still
      have a CRITICAL identity violation worth surfacing).
    * Rules requiring market context the deal lacks (e.g. coastal
      insurance benchmark) are excluded from the applicable count and
      surfaced with ``requires_market_context=True``.
    """
    dt = (doc_type or "").upper()
    if dt not in _PNL_FAMILY_DOC_TYPES:
        return
    if not fields:
        return
    try:
        # Best-effort: pull a few deal-level context fields off the
        # deals row so the scorer can resolve rules that probe property
        # metadata (keys, purchase_price, coastal/seasonal flags). The
        # extracted fields take precedence — context is only the fallback.
        extra_context: dict[str, Any] = {}
        try:
            deal_row = (
                await session.execute(
                    text("SELECT keys, purchase_price FROM deals WHERE id = :id"),
                    {"id": deal_id},
                )
            ).first()
            if deal_row is not None:
                m = deal_row._mapping
                if m.get("keys") is not None:
                    extra_context["keys"] = m["keys"]
                if m.get("purchase_price") is not None:
                    extra_context["purchase_price"] = m["purchase_price"]
        except Exception:  # noqa: BLE001 - best-effort
            pass

        from ..services.usali_scorer import (
            deviations_to_jsonb,
            flatten_extraction_fields,
            score_extraction,
        )

        flat = flatten_extraction_fields(fields, extra_context=extra_context)
        result = score_extraction(flat)
        payload = deviations_to_jsonb(result)

        # SQLite stores JSONB as TEXT; we serialize once and let the
        # driver write either way. ``usali_score`` is NULL on
        # inconclusive (Wave 1 decision).
        await session.execute(
            text(
                "UPDATE documents "
                "SET usali_score = :score, usali_deviations = :dev "
                "WHERE id = :id"
            ),
            {
                "score": result.score,
                "dev": json.dumps(payload),
                "id": doc_id,
            },
        )
        await session.commit()
        logger.info(
            "usali_score: deal=%s doc=%s score=%s applicable=%d "
            "passed=%d deviations=%d inconclusive=%s",
            deal_id,
            doc_id,
            (
                f"{result.score:.2f}"
                if result.score is not None
                else "INCONCLUSIVE"
            ),
            result.applicable_count,
            result.passed_count,
            len(result.deviations),
            result.inconclusive,
        )
    except Exception as exc:  # noqa: BLE001 - never block extraction
        logger.warning(
            "usali_score: failed to persist score for doc=%s: %s",
            doc_id,
            exc,
        )


# ─────────────────────────── critic ───────────────────────────


async def _persist_critic_report(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> None:
    """Run the Critic agent over the deal and persist findings.

    Best-effort: any failure logs and returns silently — the Critic is
    additive; never block the extraction pipeline on it. We re-build
    minimal financial inputs from the latest extraction results on the
    deal so the Critic has something to read.

    When ``EVALS_MOCK=true`` we skip the LLM narrative pass — the
    deterministic checks still run so CI exercises the wiring without
    spending tokens.
    """
    try:
        from ..agents.critic import CriticInput, run_critic

        # The first cut is intentionally narrow: we read whatever
        # broker / T-12 financials we can synthesize from the latest
        # extraction blob on the deal. When richer context is wired
        # through the LangGraph state, this helper can pick that up
        # without the API surface changing.
        broker, actuals, market_context, keys = await _load_critic_inputs(
            session, deal_id=deal_id
        )

        # If we have neither side of the comparison there's nothing to
        # critique — the agent's empty-input contract makes this safe
        # to invoke, but we skip the DB write so the UI doesn't show a
        # spurious "no findings yet" entry.
        if broker is None and actuals is None:
            return

        run_narrative = not (
            os.environ.get("EVALS_MOCK", "").lower() in ("1", "true", "yes")
        )
        critic_input = CriticInput(
            tenant_id=tenant_id,
            deal_id=deal_id,
            t12_actual=actuals,
            broker_proforma=broker,
            initial_variance=None,
            market_context=market_context,
            keys=keys,
        )
        out = await run_critic(critic_input, run_narrative_pass=run_narrative)
        if out.report is None:
            return

        report = out.report
        report_id = uuid4()
        await session.execute(
            text(
                """
                INSERT INTO critic_reports (
                    id, deal_id, tenant_id, summary, report_json, created_at
                ) VALUES (
                    :id, :deal, :tenant, :summary, :report, :created
                )
                """
            ),
            {
                "id": str(report_id),
                "deal": deal_id,
                "tenant": tenant_id,
                "summary": report.summary,
                "report": report.model_dump_json(),
                "created": _now(),
            },
        )
        for f in report.findings:
            await session.execute(
                text(
                    """
                    INSERT INTO critic_findings (
                        id, deal_id, tenant_id, rule_id, title, narrative,
                        severity, cited_fields, cited_pages,
                        impact_estimate_usd, created_at
                    ) VALUES (
                        :id, :deal, :tenant, :rule_id, :title, :narrative,
                        :severity, :cited_fields, :cited_pages,
                        :impact_estimate_usd, :created
                    )
                    """
                ),
                {
                    "id": str(f.id),
                    "deal": deal_id,
                    "tenant": tenant_id,
                    "rule_id": f.rule_id,
                    "title": f.title,
                    "narrative": f.narrative,
                    "severity": f.severity.value,
                    "cited_fields": json.dumps(f.cited_fields or []),
                    "cited_pages": json.dumps(f.cited_pages or []),
                    "impact_estimate_usd": f.impact_estimate_usd,
                    "created": _now(),
                },
            )
        await session.commit()
        logger.info(
            "critic: deal=%s findings=%d (CRIT=%d WARN=%d INFO=%d)",
            deal_id,
            len(report.findings),
            report.critical_count,
            report.warn_count,
            report.info_count,
        )
    except Exception as exc:  # noqa: BLE001 - never block extraction
        logger.warning(
            "critic: failed to persist report for deal=%s: %s", deal_id, exc
        )


async def _load_critic_inputs(
    session: AsyncSession,
    *,
    deal_id: str,
) -> tuple[Any | None, Any | None, dict[str, Any], int | None]:
    """Pull whatever broker proforma + T-12 + market context we can
    reconstruct from the latest extraction results on the deal.

    Returns ``(broker_proforma, t12_actual, market_context, keys)``.
    Both financial sides may be ``None`` when only one document type
    has been extracted yet.
    """
    try:
        from fondok_schemas import (
            DepartmentalExpenses,
            FixedCharges,
            USALIFinancials,
            UndistributedExpenses,
        )
    except ImportError:
        return None, None, {}, None

    # Pull the deal row for market context (city, brand, service, keys).
    deal_row = (
        await session.execute(
            text(
                """
                SELECT city, brand, service, keys
                  FROM deals
                 WHERE id = :id
                """
            ),
            {"id": deal_id},
        )
    ).first()
    market_context: dict[str, Any] = {}
    keys: int | None = None
    if deal_row is not None:
        m = deal_row._mapping
        if m.get("city"):
            market_context["city"] = m["city"]
            market_context["location"] = m["city"]
        if m.get("brand"):
            market_context["brand"] = m["brand"]
        if m.get("service"):
            market_context["service"] = m["service"]
        if m.get("keys"):
            try:
                keys = int(m["keys"])
                market_context["keys"] = keys
            except (TypeError, ValueError):
                pass

    # Walk every extraction result on the deal, bucketing fields by
    # T-12 (USALI P&L paths) vs broker proforma (broker_proforma.*).
    rows = await session.execute(
        text(
            """
            SELECT er.fields, d.doc_type
              FROM extraction_results er
              JOIN documents d ON d.id = er.document_id
             WHERE er.deal_id = :deal
             ORDER BY er.created_at DESC
            """
        ),
        {"deal": deal_id},
    )

    broker_fields: dict[str, float] = {}
    actual_fields: dict[str, float] = {}
    for r in rows.fetchall():
        m = r._mapping
        raw_fields = m["fields"]
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields)
            except json.JSONDecodeError:
                continue
        if not isinstance(raw_fields, list):
            continue
        doc_type = (m.get("doc_type") or "").upper()
        for f in raw_fields:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip()
            value = f.get("value")
            if not name or not isinstance(value, (int, float)):
                continue
            value_f = float(value)
            lname = name.lower()
            # broker_proforma.* always feeds broker side regardless of doc_type.
            if "broker_proforma." in lname or "broker." in lname:
                broker_fields.setdefault(lname.rsplit(".", 1)[-1], value_f)
            elif doc_type in ("T12", "PNL"):
                actual_fields.setdefault(lname.rsplit(".", 1)[-1], value_f)
            elif doc_type == "OM":
                # OM headlines feed the broker side.
                broker_fields.setdefault(lname.rsplit(".", 1)[-1], value_f)

    def _build(values: dict[str, float], label: str) -> Any | None:
        if not values:
            return None
        # Prefer explicit total_revenue; otherwise sum the legs we know.
        total_rev = values.get("total_revenue") or values.get("total_revenue_usd")
        rooms = values.get("rooms_revenue") or values.get("rooms_revenue_usd") or 0.0
        fb = values.get("fb_revenue") or values.get("fb_revenue_usd") or 0.0
        # Resort Fees — distinct USALI 11th-edition revenue line. Sam
        # QA #11. Sum into total_revenue alongside rooms / fb / other.
        resort_fees = (
            values.get("resort_fees")
            or values.get("resort_fees_usd")
            or 0.0
        )
        other = values.get("other_revenue") or values.get("other_revenue_usd") or 0.0
        if total_rev is None:
            total_rev = rooms + fb + resort_fees + other
        if total_rev <= 0:
            return None
        noi = (
            values.get("noi") or values.get("noi_usd") or 0.0
        )
        gop = values.get("gop") or values.get("gop_usd") or noi
        opex_ratio = (
            (total_rev - noi) / total_rev if total_rev > 0 else 0.0
        )
        opex_ratio = max(0.0, min(2.0, opex_ratio))
        try:
            return USALIFinancials(
                period_label=label,
                rooms_revenue=max(0.0, rooms),
                fb_revenue=max(0.0, fb),
                resort_fees=max(0.0, resort_fees),
                other_revenue=max(0.0, other),
                total_revenue=max(0.0, total_rev),
                dept_expenses=DepartmentalExpenses(
                    rooms=max(0.0, values.get("departmental_rooms", 0.0)),
                    food_beverage=max(0.0, values.get("departmental_fb", 0.0)),
                    other_operated=0.0,
                    total=max(0.0, values.get("departmental_expenses", 0.0)),
                ),
                undistributed=UndistributedExpenses(),
                mgmt_fee=max(0.0, values.get("mgmt_fee", 0.0)),
                ffe_reserve=max(0.0, values.get("ffe_reserve", 0.0)),
                fixed_charges=FixedCharges(
                    insurance=max(0.0, values.get("insurance", 0.0)),
                    property_taxes=max(0.0, values.get("property_taxes", 0.0)),
                    total=max(0.0, values.get("fixed_charges", 0.0)),
                ),
                gop=gop,
                noi=noi,
                opex_ratio=opex_ratio,
                occupancy=values.get("occupancy") or values.get("occupancy_pct"),
                adr=values.get("adr") or values.get("adr_usd"),
                revpar=values.get("revpar") or values.get("revpar_usd"),
            )
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            logger.debug("critic: %s build failed (%s)", label, exc)
            return None

    broker = _build(broker_fields, "Broker Proforma Year 1")
    actuals = _build(actual_fields, "T-12 Actual")
    return broker, actuals, market_context, keys


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
