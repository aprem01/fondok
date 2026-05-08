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
from ..extraction import ParseError, parse_pdf
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


def _guess_doc_type(filename: str) -> str:
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
    """
    name = (filename or "").lower()
    base = name.rsplit(".", 1)[0]
    tokens = set(base.replace("-", "_").split("_"))

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
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    files: list[UploadFile] = File(...),
) -> list[DocumentRecord]:
    """Persist one-or-more PDFs against ``deal_id`` and kick off
    parse + extract in the background.

    Each file is hashed and written to the raw store synchronously
    (cheap), but the actual PDF parse + LLM extraction runs as a
    background task so dense OMs don't blow through the proxy's HTTP
    timeout (Sam QA re-test #2). The route returns 201 immediately
    with each row at status ``PARSING``; the background pipeline
    drives the row through ``PARSING → UPLOADED → CLASSIFYING →
    EXTRACTING → EXTRACTED``. The web app just polls /documents.
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
                tenant_id=tenant_id_str,
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

    # Schedule parse + auto-extract for every freshly inserted doc. The
    # background task transitions PARSING → UPLOADED on parse success,
    # then chains directly into the existing extraction pipeline so the
    # web app sees a single linear status timeline (no second API call
    # required from the client). Each file gets its own task — they
    # run in parallel inside FastAPI's BackgroundTasks runner.
    for doc_id, deal_id_str, tenant_id_str_, body_bytes in pending_parse:
        background_tasks.add_task(
            _run_parse_and_extract,
            doc_id=doc_id,
            deal_id=deal_id_str,
            tenant_id=tenant_id_str_,
            body=body_bytes,
            filename=next(
                r.filename for r in records if str(r.id) == doc_id
            ),
        )

    logger.info(
        "documents.upload: deal=%s tenant=%s files=%d (parse async)",
        deal_id,
        tenant_id_str,
        len(records),
    )
    return records


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
        parsed = await parse_pdf(body, filename)
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
        async with factory() as s:
            try:
                await s.execute(
                    text("UPDATE documents SET status = :s WHERE id = :id"),
                    {"s": DOC_STATUS_PARSE_FAILED, "id": doc_id},
                )
                await s.commit()
            except Exception:  # noqa: BLE001
                logger.exception("parse_async: failed to record PARSE_FAILED")
        return
    except Exception as exc:  # noqa: BLE001 — never crash a background task
        logger.exception(
            "parse_async: unexpected error for doc=%s — %s", doc_id, exc
        )
        async with factory() as s:
            try:
                await s.execute(
                    text("UPDATE documents SET status = :s WHERE id = :id"),
                    {"s": DOC_STATUS_PARSE_FAILED, "id": doc_id},
                )
                await s.commit()
            except Exception:  # noqa: BLE001
                logger.exception("parse_async: failed to record PARSE_FAILED")
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
               AND UPPER(COALESCE(d.doc_type, '')) IN (
                   'STR_TREND', 'CBRE_HORIZONS', 'PNL_BENCHMARK'
               )
             ORDER BY er.created_at DESC
            """
        ),
        {"deal": str(deal_id)},
    )
    materialized = [dict(r._mapping) for r in rows.fetchall()]
    return _aggregate_market_data(materialized, deal_id)


# ─────────────────────────── download ───────────────────────────


@router.get("/{deal_id}/documents/{doc_id}/download")
async def download_document(
    deal_id: UUID,
    doc_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
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
                 WHERE id = :id AND deal_id = :deal
                """
            ),
            {"id": str(doc_id), "deal": str(deal_id)},
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
            # Persist the Router agent's classification back to the
            # documents row when it differs from the filename-heuristic
            # ``doc_type`` set at upload. The downstream
            # ``_load_critic_inputs`` filters on ``doc_type IN ('T12','PNL')``
            # — without this update an OM that was filename-classified as
            # T12 would have its broker_proforma fields silently bucketed
            # as actuals (Sam QA #10 root cause).
            if classified_doc_type:
                await session.execute(
                    text(
                        "UPDATE documents SET status = :s, doc_type = :dt WHERE id = :id"
                    ),
                    {
                        "s": DOC_STATUS_EXTRACTED,
                        "dt": classified_doc_type,
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
        content = ""
        source_pages: list[int] = []
    else:
        content = "\n\n".join(
            f"[Page {p.get('page_num', i+1)}]\n{p.get('text', '')}".strip()
            for i, p in enumerate(pages)
        )
        source_pages = [int(p.get("page_num", i + 1)) for i, p in enumerate(pages)]

    # Cheap filename-based doc-type hint passes to Router; agent confirms.
    hint = _guess_doc_type(filename)

    router_input = RouterInput(
        tenant_id=tenant_id,
        deal_id=deal_id,
        filename=filename,
        content_sample=content[:2000],
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

    extractor_doc = ExtractorDocument(
        document_id=doc_id,
        filename=filename,
        doc_type=doc_type,
        content=content or f"(empty document: {filename})",
        source_pages=source_pages,
    )
    extractor_input = ExtractorInput(
        tenant_id=tenant_id,
        deal_id=deal_id,
        documents=[extractor_doc],
    )
    extractor_out = await run_extractor(extractor_input)

    fields: list[dict[str, Any]] = []
    confidence: dict[str, Any] = {
        "overall": 0.0,
        "by_field": {},
        "low_confidence_fields": [],
        "requires_human_review": True,
    }
    for doc in extractor_out.extracted_documents or []:
        as_dict = doc.model_dump() if hasattr(doc, "model_dump") else dict(doc)
        for f in as_dict.get("fields", []) or []:
            fields.append(dict(f) if not isinstance(f, dict) else f)
        cr = as_dict.get("confidence")
        if cr:
            confidence = dict(cr) if not isinstance(cr, dict) else cr

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
    except Exception as exc:  # noqa: BLE001 - never block extraction
        logger.warning(
            "verification: failed to persist report for doc=%s: %s",
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
