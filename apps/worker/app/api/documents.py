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
    Form,
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
from ..engines.historical_baseline import (
    HistoricalYear,
    YoYDelta,
    build_historical_baseline,
    walk_yoy,
)
from ..extraction import ParseError, parse_document
from ..services.comp_set_drift import (
    CompSetDriftReportOut,
    compute_comp_set_drift,
    drift_report_to_pydantic,
)
from ..storage import StorageError, get_raw_store
from .deals import _assert_deal_belongs_to_tenant, get_tenant_id

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


# ─────────────────── extraction-cache versioning ────────────────────
#
# Sam cost-opt (2026-07): the Router → Extractor → Normalizer chain is
# the single biggest LLM spend on a deal — $6-10 in tokens even when
# the exact same file bytes were extracted last week for a different
# deal or in a QA reprocess. The content-hash cache short-circuits
# every re-upload of previously-extracted content at zero LLM cost.
#
# ``EXTRACTION_PIPELINE_VERSION`` is a bump-me-when-the-agents-change
# tag baked into every persisted ``extraction_results.agent_version``
# so a change to the schema addendum, USALI rules, or extractor
# prompt-template auto-invalidates the cache without a manual purge.
# The lookup requires an EXACT match on both ``content_hash`` and
# this pipeline version, so:
#
#   * Same content, same version → HIT (clone the prior row; no LLM).
#   * Same content, older version → MISS (run the extractor fresh).
#   * Different content, any version → MISS (obviously).
#
# When bumping this constant, bump the trailing integer. Old cache
# rows stay in place (they're still real extractions) but they no
# longer satisfy the ``LIKE '%;pv=vN'`` filter and every doc runs the
# full pipeline once against the new agents.
EXTRACTION_PIPELINE_VERSION = "v1"


# Per-tenant cache-hit counter surfaced via /health so ops can eyeball
# cache efficiency (Sam: "I want to know how much we're saving each
# week"). Process-local — sums are reset on worker restart, which is
# fine for a "current running total" gauge; long-term aggregate lives
# in the DB via the extraction_results rows themselves (``cached_from``
# column would be a natural next step if we start reporting weekly $
# saved). Keyed by tenant_id string.
_EXTRACTION_CACHE_HITS: dict[str, int] = {}
_EXTRACTION_CACHE_MISSES: dict[str, int] = {}


def _record_cache_hit(tenant_id: str) -> None:
    """Bump the per-tenant HIT counter surfaced by ``GET /health``."""
    _EXTRACTION_CACHE_HITS[tenant_id] = _EXTRACTION_CACHE_HITS.get(tenant_id, 0) + 1


def _record_cache_miss(tenant_id: str) -> None:
    """Bump the per-tenant MISS counter surfaced by ``GET /health``."""
    _EXTRACTION_CACHE_MISSES[tenant_id] = (
        _EXTRACTION_CACHE_MISSES.get(tenant_id, 0) + 1
    )


def get_extraction_cache_metrics() -> dict[str, Any]:
    """Snapshot of the per-tenant cache-hit counters for ``/health``.

    Returns a mapping ``{tenant_id: {"hits": int, "misses": int,
    "hit_rate": float}}`` plus a rolled-up ``total`` block. Read-only —
    safe to expose over an unauthenticated liveness probe (numbers only,
    no doc IDs or tenant PII).
    """
    all_tenants = set(_EXTRACTION_CACHE_HITS) | set(_EXTRACTION_CACHE_MISSES)
    per_tenant: dict[str, dict[str, Any]] = {}
    total_hits = 0
    total_misses = 0
    for tid in sorted(all_tenants):
        h = _EXTRACTION_CACHE_HITS.get(tid, 0)
        m = _EXTRACTION_CACHE_MISSES.get(tid, 0)
        total_hits += h
        total_misses += m
        denom = h + m
        per_tenant[tid] = {
            "hits": h,
            "misses": m,
            "hit_rate": (h / denom) if denom else 0.0,
        }
    denom_total = total_hits + total_misses
    return {
        "pipeline_version": EXTRACTION_PIPELINE_VERSION,
        "total": {
            "hits": total_hits,
            "misses": total_misses,
            "hit_rate": (total_hits / denom_total) if denom_total else 0.0,
        },
        "per_tenant": per_tenant,
    }


def _reset_extraction_cache_metrics() -> None:
    """Zero out the counters — for test isolation."""
    _EXTRACTION_CACHE_HITS.clear()
    _EXTRACTION_CACHE_MISSES.clear()


def _parse_route_from_agent_version(agent_version: str | None) -> str | None:
    """Pull the Router's route out of an ``agent_version`` string.

    Real extractor rows use the format ``router:{route};extractor;pv=vN``
    (see the tail of ``_run_graph_extraction``); mock rows use
    ``mock-evals;pv=vN`` and carry no route. Returns ``None`` when the
    string doesn't carry a ``router:`` segment — the cache-hit branch
    then falls through to the same "no classified type" behavior the
    mock path uses.
    """
    if not agent_version:
        return None
    for segment in agent_version.split(";"):
        segment = segment.strip()
        if segment.startswith("router:"):
            route = segment[len("router:"):].strip()
            return route or None
    return None


def _tag_agent_version(base: str) -> str:
    """Suffix an agent_version string with the current pipeline version.

    The cache lookup filters on this suffix so a code change that bumps
    ``EXTRACTION_PIPELINE_VERSION`` invalidates every prior row without
    a manual purge. Idempotent — re-applying the suffix is a no-op.
    """
    suffix = f";pv={EXTRACTION_PIPELINE_VERSION}"
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


async def _lookup_extraction_cache(
    session: AsyncSession,
    *,
    tenant_id: str,
    content_hash: str,
) -> dict[str, Any] | None:
    """Look for a prior extraction on the same content_hash + tenant.

    Returns ``None`` on cache miss (no prior row, or every prior row
    was written by a different pipeline version). Returns a dict with
    ``id``, ``fields``, ``confidence_report``, ``agent_version`` on
    hit — enough for the caller to clone into a new extraction_results
    row for the current doc.

    Safety gates enforced here:
      * ``tenant_id`` is a hard filter (cross-tenant lookups impossible).
      * ``content_hash`` must match exactly.
      * ``agent_version`` must end with the current pipeline version
        suffix, so a redeploy with an updated extractor doesn't serve
        stale results.
      * ``documents.status = 'EXTRACTED'`` — never cache-hit off a
        FAILED / PARSE_FAILED row.
    """
    if not content_hash or not tenant_id:
        return None
    suffix = f"%;pv={EXTRACTION_PIPELINE_VERSION}"
    row = (
        await session.execute(
            text(
                """
                SELECT er.id, er.fields, er.confidence_report, er.agent_version
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.tenant_id = :tenant
                   AND d.content_hash = :h
                   AND d.status = :status
                   AND er.agent_version LIKE :suffix
                 ORDER BY er.created_at DESC
                 LIMIT 1
                """
            ),
            {
                "tenant": tenant_id,
                "h": content_hash,
                "status": DOC_STATUS_EXTRACTED,
                "suffix": suffix,
            },
        )
    ).first()
    if row is None:
        return None
    return dict(row._mapping)


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
    # Guided-onboarding wizard signals (ROADMAP #1).
    #   * ``user_provided_doc_type`` — the analyst's tag at upload time
    #     (e.g. "T12", "PNL_MONTHLY", "STR_TREND"). Stays sticky even
    #     after the Router agent runs — we never silently overwrite
    #     analyst intent.
    #   * ``fiscal_year`` — optional year the file represents (2025,
    #     2024, …). Only set when the user pinned a year in the wizard
    #     "Financials by year" step.
    #   * ``misclassified`` — flipped to True when the Router disagrees
    #     with ``user_provided_doc_type``. The UI surfaces a warn-tone
    #     banner with "Use Fondok's classification" / "Keep mine"
    #     choices; the worker honors ``user_provided_doc_type`` until
    #     the user accepts the AI classification.
    user_provided_doc_type: str | None = None
    fiscal_year: int | None = None
    misclassified: bool = False
    # Sam QA Bug #2 v2 (June 2026) — the Router-or-refined doc_type the
    # AI proposed at extraction time. Kept SEPARATE from ``doc_type``
    # (which stays equal to the analyst tag when ``misclassified=True``)
    # so the banner can render BOTH sides distinctly. Without this the
    # banner displayed ``doc_type`` for both ``userLabel`` and
    # ``aiLabel`` and showed "T-12 vs T-12" because they resolved to
    # the same persisted value.
    # NULL when ``misclassified=False`` (no conflict to display).
    ai_proposed_doc_type: str | None = None
    # Wave 1 — year-mismatch banner (June 2026).
    #   * ``year_mismatch`` — True when the analyst pinned a fiscal_year
    #     in the wizard AND the Extractor pulled a ``period_ending``
    #     whose year disagrees. Cleared by ``POST .../accept_year``.
    #   * ``extracted_period_year`` — the year inferred from
    #     ``p_and_l_usali.period_ending`` (or any ``*.period_ending``).
    #     Surfaced so the banner can render Fondok's read without
    #     re-walking the extraction_results JSON.
    year_mismatch: bool = False
    extracted_period_year: int | None = None
    # Wave 4 USALI v4 — structural P&L recognizer confidence (0.0-1.0).
    # Written during extraction by the recognizer in
    # ``app.services.structural_recognizer.classify_structure``. Used by
    # the misclassification override (v4 trusts the user's tag when this
    # is high) and surfaced to the UI for analysts who want to see why
    # the recognizer made its call. NULL on docs extracted before v4 OR
    # on non-P&L docs the recognizer ignored.
    structural_pnl_score: float | None = None


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
    # Sam QA 2026-07-02: the /extraction read path was 500-ing on any
    # doc whose confidence blob carried the chunk_errors payload the
    # extractor added in June (structural_contradiction + rate-limit
    # error surfacing). Loosen to extra="ignore" so a write-side
    # schema addition can never brick the read path again.
    model_config = ConfigDict(extra="ignore")

    overall: float = 0.0
    by_field: dict[str, float] = Field(default_factory=dict)
    low_confidence_fields: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    # Explicitly modeled so the field survives into the JSON response.
    # Present when the extractor hit per-chunk failures (auth, rate
    # limit, structural_contradiction) so the UI can surface a real
    # error message instead of a bare "0 fields" empty envelope.
    chunk_errors: list[str] = Field(default_factory=list)


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


# ─────────────────────────── upload validation ───────────────────────────
#
# Wave 1 hardening (June 2026). Reject obvious garbage at the
# request boundary so a stray 500 MB MOV / 200 MB PDF doesn't drag
# the worker into a 60-second LlamaParse spiral that's guaranteed
# to FAIL. Numbers are deliberately loose — the largest legit OM
# Sam has shipped was 38 MB, biggest workbook 18 MB.
#
# Cap is env-overridable via ``MAX_UPLOAD_MB`` (default 50). Read
# lazily so a test that bumps the setting via env or monkeypatch
# takes effect without reloading the module.


def _max_upload_bytes() -> int:
    return get_settings().MAX_UPLOAD_MB * 1024 * 1024


# Module-level alias preserved for tests + readability at the call
# site. Computed at import — bumping the env after worker boot
# requires a restart, which matches every other Settings field.
_MAX_UPLOAD_BYTES = _max_upload_bytes()

# Lower-cased file extensions Fondok accepts. PDF for OMs / reports,
# Excel for P&Ls / room mixes, CSV for raw exports, Word for the
# rare narrative spec sheet.
_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".csv",
    ".doc",
    ".docx",
}

# Lower-cased Content-Type prefixes. We match by prefix so the
# vendor-specific ``application/vnd.openxmlformats-officedocument…``
# Office MIME types pass without a full enumeration. Browsers and
# Excel exports also sometimes send ``application/octet-stream`` for
# .xlsm — we don't allowlist that, but the extension check above
# catches it.
_ALLOWED_MIME_PREFIXES = (
    "application/pdf",
    "application/vnd.openxmlformats",  # .xlsx / .docx / .pptx family
    "application/vnd.ms-excel",  # .xls
    "application/vnd.ms-excel.sheet.macroenabled",  # .xlsm
    "application/msword",  # .doc
    "text/csv",
    # Some browsers / OSes send no explicit MIME for legitimate
    # .csv uploads ("application/octet-stream" or ""); the extension
    # check covers those — they don't need to appear here.
)


def _content_matches_extension(filename: str, body: bytes) -> bool:
    """Magic-byte sniff: does the leading byte signature match what
    the filename's extension implies?

    Sam QA: a ``.exe`` renamed to ``.pdf`` slipped through the
    extension/MIME allowlist (the MIME side is intentionally permissive
    so broker-stripped uploads aren't blocked). This second pass reads
    the first few bytes and rejects anything whose magic doesn't match.

    Permissive on .csv and .doc — CSVs have no magic and legacy .doc
    has weak heuristics; we accept whatever the allowlist passed for
    those. Everything else (PDF / xlsx / xlsm / xls) has a well-known
    magic and we enforce it.
    """
    if not body or len(body) < 4:
        return False
    name = (filename or "").lower()
    dot = name.rfind(".")
    ext = name[dot:] if dot >= 0 else ""
    head = body[:8]
    if ext == ".pdf":
        # PDF spec: every conforming file starts with ``%PDF-`` in the
        # first 1024 bytes. We require it in the first 8 — every PDF
        # I've ever seen lands it at offset 0.
        return head.startswith(b"%PDF-")
    if ext in (".xlsx", ".xlsm", ".docx"):
        # ZIP archive (Office Open XML) — starts with ``PK\x03\x04``.
        return head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06")
    if ext == ".xls":
        # Legacy OLE Compound Document — starts with the well-known
        # ``D0 CF 11 E0 A1 B1 1A E1`` signature.
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    # .csv (no magic, just text) and .doc (weak magic, varied) — trust
    # the extension/MIME pass.
    return True


def _is_allowed_upload(filename: str, content_type: str | None) -> bool:
    """Return True when ``filename`` extension OR ``content_type``
    matches the Wave 1 allowlist. Reject only when BOTH fail.

    Why permissive: real-world broker uploads occasionally arrive
    with stripped or generic content-types (e.g. ``application/octet-stream``)
    even though the file is a legit PDF. Insisting on both checks would
    block uploads that obviously belong here. A spreadsheet with a
    ``.pdf`` extension is similarly rare — we trade a sliver of false-
    accept risk for far fewer false-rejects.
    """
    name = (filename or "").lower()
    dot = name.rfind(".")
    ext = name[dot:] if dot >= 0 else ""
    ext_ok = ext in _ALLOWED_EXTENSIONS
    ct = (content_type or "").lower().strip()
    ct_ok = any(ct.startswith(prefix) for prefix in _ALLOWED_MIME_PREFIXES)
    return ext_ok or ct_ok


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

    # Wave 1 — 11-category guided onboarding additions. Each filename
    # hint maps onto a recommended-for-IC category so a "coi_2025.pdf"
    # or "phase1_environmental.pdf" gets bucketed correctly even when
    # the Router LLM is offline. Order matters — more-specific tokens
    # before generic fall-throughs.
    if (
        "insurance" in tokens
        or "coi" in tokens
        or "policy" in tokens and "ins" in name
        or ("certificate" in tokens and ("insurance" in name or "liability" in name))
        or "loss_run" in name
        or "lossrun" in name
    ):
        return "INSURANCE"
    if (
        ("property" in tokens and ("tax" in tokens or "taxes" in tokens))
        or "property_tax" in name
        or "propertytax" in name
        or "tax_bill" in name
        or "taxbill" in name
        or "assessor" in tokens
        or "assessment" in tokens
    ):
        return "PROPERTY_TAX"
    if (
        "capex" in tokens
        or "capital_expenditure" in name
        or "capitalexpenditure" in name
        or ("pip" in tokens and ("scope" in tokens or "budget" in tokens))
        or "ffe_reserve" in name
        or "ffereserve" in name
        or "renovation_budget" in name
    ):
        return "CAPEX"
    if (
        "floorplan" in tokens
        or "floor_plan" in name
        or "site_plan" in name
        or "siteplan" in name
        or "franchise_agreement" in name
        or "brand_standards" in name
        or "property_info" in name
        or "propertyinfo" in name
    ):
        return "PROPERTY_INFO"
    if (
        "lease" in tokens
        or "leases" in tokens
        or "ground_lease" in name
        or "groundlease" in name
        or "management_agreement" in name
        or "operator_agreement" in name
    ):
        return "LEASES"
    if (
        "alta" in tokens
        or "phase1" in tokens
        or "phase_1" in name
        or "phase2" in tokens
        or "phase_2" in name
        or "environmental" in tokens
        or "pca" in tokens
        or "engineering_report" in name
        or "structural_report" in name
        or "survey" in tokens
        or "surveys" in tokens
    ):
        return "SURVEYS"
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


def _canonical_doc_type(value: str | None) -> str:
    """Collapse a doc-type token to its canonical comparison form.

    Two stores feed the misclassification check:
      * ``user_provided_doc_type`` — wizard-supplied canonical enum
        ``T12``, but legacy uploads / external clients sometimes
        emit ``"T-12"``, ``"t 12"``, or ``"PNL_MONTHLY"`` with mixed
        case. The Router-or-refined ``doc_type`` is always canonical
        enum form.
      * The Router-or-refined ``doc_type`` is canonical enum form.

    Sam QA (Bug #2, June 2026): a banner fired on a correctly
    categorized T-12 because the user-supplied string and the AI
    label compared as plain strings: ``"T-12" != "T12"``. This helper
    canonicalizes both sides — uppercases, strips, and drops
    hyphens / spaces / underscores so the three common surface forms
    (``T-12`` / ``T 12`` / ``T_12`` / ``T12``) collapse to ``T12``.
    Returns ``""`` for empty input so the misclassified check can
    short-circuit on missing tags via truthiness.
    """
    if value is None:
        return ""
    return value.upper().replace("-", "").replace(" ", "").replace("_", "").strip()


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

    # Wizard signals. ``misclassified`` is BOOLEAN on Postgres and
    # INTEGER (0/1) on SQLite — coerce both into a Python bool so the
    # Pydantic response is consistent across dialects.
    raw_misc = row.get("misclassified")
    if raw_misc is None:
        misclassified = False
    elif isinstance(raw_misc, bool):
        misclassified = raw_misc
    else:
        try:
            misclassified = bool(int(raw_misc))
        except (TypeError, ValueError):
            misclassified = bool(raw_misc)

    fiscal_year_raw = row.get("fiscal_year")
    try:
        fiscal_year: int | None = (
            int(fiscal_year_raw) if fiscal_year_raw is not None else None
        )
    except (TypeError, ValueError):
        fiscal_year = None

    user_provided_doc_type = row.get("user_provided_doc_type")
    if isinstance(user_provided_doc_type, str):
        user_provided_doc_type = user_provided_doc_type.strip() or None

    # Sam QA Bug #2 v2 — Router's proposal at extraction time. Stays
    # NULL when no conflict surfaced.
    ai_proposed_doc_type = row.get("ai_proposed_doc_type")
    if isinstance(ai_proposed_doc_type, str):
        ai_proposed_doc_type = ai_proposed_doc_type.strip() or None
    elif ai_proposed_doc_type is not None:
        # Defensive — non-string non-null shouldn't happen but log
        # silently so we don't crash on a bad cache.
        ai_proposed_doc_type = None

    # Year-mismatch flag (Wave 1). Same BOOLEAN/INTEGER dialect dance as
    # ``misclassified``.
    raw_ym = row.get("year_mismatch")
    if raw_ym is None:
        year_mismatch = False
    elif isinstance(raw_ym, bool):
        year_mismatch = raw_ym
    else:
        try:
            year_mismatch = bool(int(raw_ym))
        except (TypeError, ValueError):
            year_mismatch = bool(raw_ym)
    epy_raw = row.get("extracted_period_year")
    try:
        extracted_period_year: int | None = (
            int(epy_raw) if epy_raw is not None else None
        )
    except (TypeError, ValueError):
        extracted_period_year = None
    sps_raw = row.get("structural_pnl_score")
    try:
        structural_pnl_score: float | None = (
            float(sps_raw) if sps_raw is not None else None
        )
    except (TypeError, ValueError):
        structural_pnl_score = None

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
        user_provided_doc_type=user_provided_doc_type,
        fiscal_year=fiscal_year,
        misclassified=misclassified,
        ai_proposed_doc_type=ai_proposed_doc_type,
        year_mismatch=year_mismatch,
        extracted_period_year=extracted_period_year,
        structural_pnl_score=structural_pnl_score,
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
        except Exception:
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
                           extraction_data, usali_score, usali_deviations,
                           user_provided_doc_type, fiscal_year,
                           misclassified, ai_proposed_doc_type,
                           year_mismatch, extracted_period_year,
                           structural_pnl_score
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
    user_doc_types: list[str] | None = Form(None),
    # Accept ``list[str]`` instead of ``list[int]`` so the wizard can
    # send positionally-aligned empty strings for files without a year
    # (e.g. the OM in a mixed batch) without tripping Pydantic's int
    # parser. Each entry is coerced to int inside the loop with a
    # plausibility window (1900-2100).
    fiscal_years: list[str] | None = Form(None),
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

    Wizard metadata (ROADMAP #1 — guided per-category onboarding)
    --------------------------------------------------------------
    The guided onboarding wizard pre-categorizes each file ("this is a
    2024 detailed P&L"). When provided, ``user_doc_types`` and
    ``fiscal_years`` are positionally aligned with ``files`` —
    ``user_doc_types[i]`` belongs to ``files[i]``. Empty strings or
    out-of-range entries are treated as "not provided" (the legacy
    bulk-upload zone on the Data Room calls this endpoint without
    either array, which must still work).

    ``user_provided_doc_type`` is stored verbatim. Once extraction runs,
    if the Router agent's classification disagrees with the analyst's
    tag, ``misclassified`` is flipped to ``True`` so the UI can prompt
    the user to accept or reject the AI classification — we never
    silently overwrite analyst intent.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one file is required",
        )

    # Normalize the wizard metadata arrays so we can index them by file
    # position without a per-iteration length guard. An entry shorter
    # than ``files`` is padded with ``None``; over-long entries are
    # truncated (defensive — never expected from the wizard).
    def _pad(seq: list[Any] | None, n: int) -> list[Any]:
        if not seq:
            return [None] * n
        out: list[Any] = list(seq)[:n]
        if len(out) < n:
            out += [None] * (n - len(out))
        return out

    user_doc_types_padded = _pad(user_doc_types, len(files))
    fiscal_years_padded = _pad(fiscal_years, len(files))

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
    for idx, upload in enumerate(files):
        filename = upload.filename or "upload.pdf"
        # Pull the wizard-provided category + year for this file. Empty
        # strings (the wizard sends them when the user picked "Not
        # sure") collapse to None so we don't store noise.
        raw_user_type = user_doc_types_padded[idx]
        user_provided_type: str | None = (
            raw_user_type.strip().upper()
            if isinstance(raw_user_type, str) and raw_user_type.strip()
            else None
        )
        raw_year = fiscal_years_padded[idx]
        fiscal_year: int | None
        try:
            # Reject implausible years (e.g. 0, 99) so a sloppy form
            # post doesn't pollute the column. Hotel acquisitions cover
            # roughly 1900-now+1; anything outside that is bad data.
            fiscal_year_candidate = (
                int(raw_year) if raw_year not in (None, "") else None
            )
            fiscal_year = (
                fiscal_year_candidate
                if fiscal_year_candidate is not None
                and 1900 <= fiscal_year_candidate <= 2100
                else None
            )
        except (TypeError, ValueError):
            fiscal_year = None
        try:
            body = await upload.read()
        except Exception as exc:
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

        # Wave 1 size guard (B1). Reject oversize uploads at the boundary
        # so we don't burn LlamaParse credits + Sonnet tokens on a file
        # that can never realistically be a hotel doc. Cap is
        # env-overridable via ``MAX_UPLOAD_MB`` (default 50, set in
        # ``app/config.py``).
        if len(body) > _MAX_UPLOAD_BYTES:
            mb = len(body) / 1024 / 1024
            cap_mb = _MAX_UPLOAD_BYTES // (1024 * 1024)
            records.append(
                _failed_upload_record(
                    deal_id=deal_id,
                    tenant_id=tenant_id,
                    filename=filename,
                    error_kind="too_large",
                    error_message=(
                        f"File is {mb:.1f} MB — Fondok accepts files up to "
                        f"{cap_mb} MB. Compress the PDF or split the workbook "
                        "and re-upload."
                    ),
                )
            )
            continue

        # Wave 1 MIME / extension allowlist (B2). Rejects any file
        # whose extension AND content_type are both outside the hotel-
        # doc envelope (PDF / Excel / CSV / Word). Either-or so a
        # broker-stripped MIME ("application/octet-stream") on a real
        # PDF still passes.
        if not _is_allowed_upload(filename, upload.content_type):
            records.append(
                _failed_upload_record(
                    deal_id=deal_id,
                    tenant_id=tenant_id,
                    filename=filename,
                    error_kind="unsupported_type",
                    error_message=(
                        "Fondok accepts PDF, Excel, CSV, and Word "
                        "documents only. Re-export this file as one of "
                        "those formats and try again."
                    ),
                )
            )
            continue

        # Magic-byte sniff (Sam QA): a renamed .exe → .pdf passes the
        # extension/MIME allowlist above (browser sends content_type
        # ``application/octet-stream`` or worse, ``application/x-msdownload``,
        # but the extension says ``.pdf``). The allowlist trusts that —
        # by design, to tolerate broker-stripped MIME types — so we
        # cross-check the actual leading bytes against the declared
        # type before handing the file to LlamaParse / openpyxl.
        if not _content_matches_extension(filename, body):
            records.append(
                _failed_upload_record(
                    deal_id=deal_id,
                    tenant_id=tenant_id,
                    filename=filename,
                    error_kind="unsupported_type",
                    error_message=(
                        "This file's contents don't match its extension. "
                        "Re-export the original document as a PDF, Excel, "
                        "CSV, or Word file and re-upload."
                    ),
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
        except Exception as exc:
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
        # User-provided doc type wins over the filename heuristic for the
        # initial ``doc_type`` so the downstream pipeline reads the
        # analyst's intent. The filename hint stays available via
        # ``_guess_doc_type`` for the Router fallback path.
        guessed_doc_type = _guess_doc_type(filename, size_bytes=len(body))
        doc_type = user_provided_type or guessed_doc_type
        uploaded_at = _now()

        try:
            await session.execute(
                text(
                    """
                    INSERT INTO documents (
                        id, deal_id, tenant_id, filename, doc_type, status,
                        uploaded_at, content_hash, storage_key, size_bytes,
                        page_count, parser, extraction_data,
                        user_provided_doc_type, fiscal_year, misclassified,
                        ai_proposed_doc_type,
                        year_mismatch, extracted_period_year
                    ) VALUES (
                        :id, :deal_id, :tenant_id, :filename, :doc_type, :status,
                        :uploaded_at, :content_hash, :storage_key, :size_bytes,
                        :page_count, :parser, :extraction_data,
                        :user_provided_doc_type, :fiscal_year, :misclassified,
                        :ai_proposed_doc_type,
                        :year_mismatch, :extracted_period_year
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
                    "user_provided_doc_type": user_provided_type,
                    "fiscal_year": fiscal_year,
                    # Postgres (asyncpg) strictly rejects int for a
                    # BOOLEAN column — every prod upload silently
                    # failed with ``db_insert_failed`` until this was
                    # changed from 0 to False. SQLite's bool→int
                    # coercion is one-way; False round-trips fine on
                    # both dialects.
                    "misclassified": False,
                    # Sam QA Bug #2 v2 — Router has not run at INSERT
                    # time; this column is filled by the extraction
                    # completion block only when a conflict is detected.
                    "ai_proposed_doc_type": None,
                    "year_mismatch": False,
                    "extracted_period_year": None,
                },
            )
            await session.commit()
        except Exception as exc:
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
                user_provided_doc_type=user_provided_type,
                fiscal_year=fiscal_year,
                misclassified=False,
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

    # If EVERY document failed (db_insert, read_failed, too_large,
    # unsupported_type, empty, etc.) the previous behaviour was a
    # cheerful 201 with a list of FAILED rows — the UI rendered "2 docs
    # uploaded" while the truth was zero docs survived. Distinguish the
    # whole-batch failure case so the client can surface the actual
    # outcome. Mixed outcomes (some FAILED + some PARSING) still return
    # 201; the UI shows per-row error chips in that case.
    failed_kinds = {
        "db_insert_failed",
        "read_failed",
        "too_large",
        "unsupported_type",
        "empty",
        "storage_failed",
    }
    all_failed = bool(records) and all(
        (rec.error_kind or "") in failed_kinds for rec in records
    )
    if all_failed:
        # Per-row payload still useful for the client error banner.
        from fastapi.responses import JSONResponse

        try:
            from ..alerting import report_alert

            report_alert(
                severity="error",
                title="Batch upload — all documents failed validation",
                deal_id=deal_id,
                stage="upload.batch",
                extra={
                    "doc_count": len(records),
                    "error_kinds": sorted({(rec.error_kind or "") for rec in records}),
                    "filenames": [rec.filename for rec in records][:10],
                },
            )
        except Exception:
            pass

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=[rec.model_dump(mode="json") for rec in records],
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
            except Exception:
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
    except Exception as exc:
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
        except Exception:
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
    except Exception:
        logger.exception(
            "parse_async: context_store indexing failed for doc=%s "
            "(non-fatal — extraction continues)",
            doc_id,
        )

    # Auto-chain extraction so the user sees a single status timeline
    # without having to call a second endpoint. Failures inside
    # ``_run_extraction_pipeline`` are already caught and recorded as
    # ``FAILED`` on the row. The process-level
    # ``acquire_extractor_slot()`` semaphore lives INSIDE
    # ``_run_extraction_pipeline`` so the cap also applies to the
    # standalone ``POST .../extract`` endpoint that bypasses this
    # function (Wave 4 reliability fix — Bug #2).
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
        except Exception as exc:
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
    """List documents on a deal in upload-recent order.

    Tenant-scoped: cross-tenant access returns 404 via the deal-belongs
    gate, plus the SQL filters on ``tenant_id`` for belt-and-suspenders.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    rows = await session.execute(
        text(
            """
            SELECT id, deal_id, tenant_id, filename, doc_type, status,
                   uploaded_at, content_hash, storage_key, size_bytes,
                   page_count, parser, extraction_data,
                   usali_score, usali_deviations,
                   user_provided_doc_type, fiscal_year, misclassified,
                   ai_proposed_doc_type,
                   year_mismatch, extracted_period_year,
                   structural_pnl_score
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

        # Bucket the legacy bare 'STR' label alongside STR_TREND so a
        # row classified before the router canonicalization fix still
        # contributes its comp-set + per-property keys to the STR
        # block (Sam QA 2026-06-30 — same file emitted STR on one
        # deal and STR_TREND on another).
        if doc_type == "STR":
            doc_type = "STR_TREND"
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
    compset_entries: list[CompSetEntry] = [
        CompSetEntry(
            name=str(compset[i].get("name")) if compset[i].get("name") is not None else None,
            keys=_coerce_int(compset[i].get("keys")),
            occupancy_pct=_coerce_float(compset[i].get("occupancy_pct")),
            adr_usd=_coerce_float(compset[i].get("adr_usd")),
            revpar_usd=_coerce_float(compset[i].get("revpar_usd")),
        )
        for i in sorted(compset.keys())
    ]

    # `comp_set.total_keys` is the sum of room counts across every
    # property in the comp set. STR's "Response" tab carries it as the
    # last row of the property roster; many older extractions skip the
    # explicit rollup but DO emit the indexed compset rows. Fall back
    # to summing the per-property `keys` so the frontend Index-Analysis
    # table can compute Available Rooms = days × total_keys instead of
    # rendering a row of zeros. The subject keys live on the deal
    # record so we leave them out of this comp-set rollup.
    extracted_total_keys = _coerce_int(flat.get("comp_set.total_keys"))
    if extracted_total_keys is None or extracted_total_keys <= 0:
        keys_sum = sum(e.keys for e in compset_entries if e.keys and e.keys > 0)
        derived_total_keys: int | None = keys_sum if keys_sum > 0 else None
    else:
        derived_total_keys = extracted_total_keys

    # comp_set_size: prefer the extracted value; otherwise count rows
    # that have *any* identifying detail (name or keys). An empty
    # roster correctly stays None.
    extracted_size = _coerce_int(flat.get("comp_set.comp_set_size"))
    if extracted_size is None or extracted_size <= 0:
        derived_size: int | None = (
            sum(1 for e in compset_entries if e.name or (e.keys and e.keys > 0))
            or None
        )
    else:
        derived_size = extracted_size

    block = StrTrendBlock(
        subject_occupancy_pct=_coerce_float(flat.get("ttm_performance.subject.occupancy_pct")),
        subject_adr_usd=_coerce_float(flat.get("ttm_performance.subject.adr_usd")),
        subject_revpar_usd=_coerce_float(flat.get("ttm_performance.subject.revpar_usd")),
        rgi_revpar_index=_coerce_float(flat.get("ttm_performance.indices.rgi_revpar_index")),
        ari_adr_index=_coerce_float(flat.get("ttm_performance.indices.ari_adr_index")),
        mpi_occupancy_index=_coerce_float(flat.get("ttm_performance.indices.mpi_occupancy_index")),
        comp_set_size=derived_size,
        total_keys=derived_total_keys,
        compset=compset_entries,
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

    Tenant-scoped: cross-tenant access returns 404 before any join
    runs, plus the join itself filters on ``tenant_id``.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
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
                   'STR', 'STR_TREND', 'CBRE_HORIZONS', 'PNL_BENCHMARK'
               )
             ORDER BY er.created_at DESC
            """
        ),
        {"deal": str(deal_id), "tenant": str(tenant_id)},
    )
    materialized = [dict(r._mapping) for r in rows.fetchall()]
    return _aggregate_market_data(materialized, deal_id)


# ─────────────────────────── comp-set drift ───────────────────────────


@router.get("/{deal_id}/comp_set_drift", response_model=CompSetDriftReportOut)
async def get_comp_set_drift(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> CompSetDriftReportOut:
    """STR comp-set drift across years — Wave 1 roadmap item #8.

    Walks every STR_TREND extraction for the deal, sorts by
    ``str_trend.report_year`` (the schema field added alongside this
    feature), and emits one diff per consecutive year pair. The
    response surfaces ``added`` / ``removed`` / ``unchanged`` plus
    ``uncertain_matches`` — fuzzy-name pairs above the 80% threshold
    that an analyst should sanity-check before the side-note ships in
    the memo.

    Eshan's framing on the June 25 2026 call: "In 2024 you had Hilton
    South Beach in your comp set; in 2025 it was replaced with W South
    Beach. Fondok could make those notes on the side." This endpoint
    backs that side-note.

    Tenant-scoped; the underlying SQL filters on both the extraction
    row's ``tenant_id`` and the document's ``tenant_id`` so a
    cross-tenant deal_id guess produces an empty report (drifts=[])
    rather than data leakage. The deal-belongs gate runs first so the
    response is a clean 404 rather than an empty 200 envelope.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    report = await compute_comp_set_drift(
        session,
        deal_id=str(deal_id),
        tenant_id=str(tenant_id),
    )
    return drift_report_to_pydantic(report)


# ─────────────────────────── document-coverage audit ───────────────────────────


class CoverageGapOut(BaseModel):
    """One gap entry — serialized shape of ``CoverageGap`` for the API.

    Mirrors the dataclass in ``services.coverage_audit`` but lives here
    so the OpenAPI schema renders without dragging the service layer
    into the router import path.
    """

    model_config = ConfigDict(extra="forbid")

    gap_type: str
    year: int
    message: str
    severity: str
    months_missing: list[int] | None = None
    dismissible: bool = False


class CoverageDocOut(BaseModel):
    """One contributing document inside a year's coverage entry."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str | None = None
    doc_type: str | None = None
    period_type: str | None = None
    period_ending: str | None = None


class DocumentCoverageResponse(BaseModel):
    """Full coverage rollup returned by GET /deals/{deal_id}/document_coverage."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    year_coverage: dict[int, list[CoverageDocOut]] = Field(default_factory=dict)
    gaps: list[CoverageGapOut] = Field(default_factory=list)
    lookback_years: int


@router.get(
    "/{deal_id}/document_coverage",
    response_model=DocumentCoverageResponse,
)
async def get_document_coverage(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    lookback_years: int = 5,
) -> DocumentCoverageResponse:
    """Sequential + detail-level gap detection on the deal's P&L uploads.

    Roadmap item #7 — Sam's June 25 framing: "If I have financials from
    2019 to 2025 but I'm missing detailed for 2024 to 2025, only summary
    — that's a gap I'd want Fondok to flag." This endpoint backs the gap
    chips strip on the Onboarding / Data Room view.

    Query params
    ------------
    lookback_years
        How far back (from the current year) to demand contiguous
        coverage. Wave 1 default is 5 — analysts can pass a custom value
        per deal when the property has a longer / shorter history.

    Tenant scoping
    --------------
    Every query is tenant-scoped via ``X-Tenant-Id`` (see commit
    ``2a8ed64`` for the P0 fix that hardened the previously-leaky
    endpoints). A tenant can only see its own documents — cross-tenant
    rows are filtered out at the SQL layer. The deal-belongs gate
    short-circuits any cross-tenant request to 404.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    from ..services.coverage_audit import audit_document_coverage

    coverage = await audit_document_coverage(
        session,
        deal_id=str(deal_id),
        tenant_id=str(tenant_id),
        lookback_years=lookback_years,
    )

    return DocumentCoverageResponse(
        deal_id=deal_id,
        year_coverage={
            year: [CoverageDocOut(**entry) for entry in entries]
            for year, entries in sorted(coverage.year_coverage.items())
        },
        gaps=[
            CoverageGapOut(
                gap_type=g.gap_type,
                year=g.year,
                message=g.message,
                severity=g.severity,
                months_missing=g.months_missing,
                dismissible=g.dismissible,
            )
            for g in coverage.gaps
        ],
        lookback_years=coverage.lookback_years,
    )


# ─────────────────────────── historical baseline (Wave 2 P2.6) ───────────────────────────


class HistoricalYearOut(BaseModel):
    """One historical fiscal year's P&L roll-up — wire shape.

    Mirrors the ``HistoricalYear`` dataclass in
    ``engines.historical_baseline`` but lives here so the OpenAPI
    schema renders without dragging the engine import into the router
    docs. Numeric fields are ``float | None`` (None = extractor didn't
    ship that line; UI renders an em-dash).
    """

    model_config = ConfigDict(extra="forbid")

    fiscal_year: int
    occupancy: float | None = None
    adr: float | None = None
    revpar: float | None = None
    rooms_revenue: float | None = None
    fnb_revenue: float | None = None
    other_revenue: float | None = None
    total_revenue: float | None = None
    rooms_dept_expense: float | None = None
    fnb_dept_expense: float | None = None
    other_dept_expense: float | None = None
    undistributed: float | None = None
    gop: float | None = None
    fixed_expenses: float | None = None
    noi: float | None = None
    source_document_ids: list[str] = Field(default_factory=list)


class YoYDeltaOut(BaseModel):
    """One above-noise YoY swing on a single line + year — wire shape.

    ``yoy_pct=None`` rows are first-year-of-series entries (no prior
    year to compare against) or zero-prior-year divisions. The walk
    is sorted by ``abs(yoy_pct) DESC`` with None entries last.
    """

    model_config = ConfigDict(extra="forbid")

    line: str
    year: int
    value: float
    yoy_abs: float | None = None
    yoy_pct: float | None = None


class HistoricalBaselineResponse(BaseModel):
    """Full envelope returned by GET /deals/{deal_id}/historical-baseline.

    Sam's June 2026 ask (Wave 2 P2.6): "Institutional IC analysts will
    not approve a deal without seeing the multi-year trend." The
    ``years`` list backs the side-by-side table; ``walk`` carries the
    top YoY swings as broker-question candidates; ``coverage_pct`` +
    ``gaps`` drive the "Coverage 3/5 yrs — Missing 2020-2021" chip.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    years: list[HistoricalYearOut] = Field(default_factory=list)
    gaps: list[int] = Field(default_factory=list)
    look_back_years: int = 5
    coverage_pct: float = 0.0
    walk: list[YoYDeltaOut] = Field(default_factory=list)


@router.get(
    "/{deal_id}/historical-baseline",
    response_model=HistoricalBaselineResponse,
)
async def get_historical_baseline(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    lookback_years: int = 5,
) -> HistoricalBaselineResponse:
    """3-5 year historical baseline of the subject property — Wave 2 P2.6.

    Aggregates the property's OWN historical P&L extractions (every
    T12/PNL/PNL_MONTHLY/PNL_YTD doc with a ``documents.fiscal_year``
    set) into a year-by-year roll-up. Reuses the USALI scorer's alias
    map + ``_derive_usali_rollups`` so synthesized totals
    (``total_revenue`` / ``gop`` / ``noi``) land alongside directly-
    extracted line items.

    ``walk`` is the YoY-delta projection sorted by ``abs(yoy_pct)
    DESC`` with a 0.5% noise floor — feeds the UI's "biggest swings"
    chips that each click-to-create a Broker Question (Wave 1 #4).

    Tenant-scoped: the underlying SQL filters on both the extraction
    row's ``tenant_id`` and the deal-belongs gate runs first. A
    cross-tenant deal_id returns 404 rather than leaking data.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    # Clamp the lookback window to a sane range. Anything < 2 yrs is
    # not a "walk" (you need at least 2 years for a YoY); > 10 is more
    # history than any institutional UW model bothers with.
    lookback = max(2, min(10, lookback_years))
    baseline = await build_historical_baseline(
        session,
        deal_id=str(deal_id),
        tenant_id=str(tenant_id),
        lookback_years=lookback,
    )
    walk = walk_yoy(baseline)
    return HistoricalBaselineResponse(
        deal_id=deal_id,
        years=[HistoricalYearOut(**_year_dict(y)) for y in baseline.years],
        gaps=list(baseline.gaps),
        look_back_years=baseline.look_back_years,
        coverage_pct=baseline.coverage_pct,
        walk=[YoYDeltaOut(**_delta_dict(d)) for d in walk],
    )


# ──────────────────── STR forward forecast (Wave 3 W3.3) ────────────────────


class _STRMonthOut(BaseModel):
    """Wire-format row for one historical-or-forecast STR month.

    Mirrors ``fondok_schemas.str_forecast.STRMonth`` 1:1; defined here
    so the OpenAPI spec is self-contained (the engine schema lives in
    the cross-package ``fondok_schemas`` module). Field semantics:

    * ``period`` — YYYY-MM
    * ``occupancy`` — 0..1 (NOT a percent)
    * ``adr`` — USD
    * ``revpar`` — USD (== occupancy × adr at write-time)
    * ``comp_set_revpar`` — USD comp-set RevPAR for the same period
    * ``revpar_index`` — subject revpar / comp_set_revpar
    * ``is_historical`` — True for ingested rows, False for forecast
    """

    model_config = ConfigDict(extra="forbid")

    period: str
    occupancy: float
    adr: float
    revpar: float
    comp_set_revpar: float
    revpar_index: float
    is_historical: bool


class _STRForecastScenarioOut(BaseModel):
    """Wire-format scenario settings (defaults OR analyst overrides)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    revpar_cagr_pct: float
    revpar_index_target: float
    occupancy_floor: float
    adr_floor: float
    notes: list[str] = Field(default_factory=list)


class _STRForecastResultOut(BaseModel):
    """GET /deals/{id}/str-forecast response envelope."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    historical_months: list[_STRMonthOut] = Field(default_factory=list)
    forecast_months: dict[str, list[_STRMonthOut]] = Field(default_factory=dict)
    scenario_settings: list[_STRForecastScenarioOut] = Field(default_factory=list)
    coverage_quality: str = "low"


class _STRForecastScenarioOverride(BaseModel):
    """Partial scenario override accepted on POST.

    Every field but ``name`` is optional. Omitted fields fall back to
    the default for that scenario, so callers can flex just one
    knob (e.g. lift the base scenario's RevPAR CAGR) without re-
    declaring the full scenario.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    revpar_cagr_pct: float | None = None
    revpar_index_target: float | None = None
    occupancy_floor: float | None = None
    adr_floor: float | None = None
    notes: list[str] | None = None


class _STRForecastScenariosRequest(BaseModel):
    """POST /deals/{id}/str-forecast/scenarios body."""

    model_config = ConfigDict(extra="forbid")

    scenarios: list[_STRForecastScenarioOverride] = Field(default_factory=list)


def _str_month_out(m: Any) -> _STRMonthOut:
    return _STRMonthOut(
        period=m.period,
        occupancy=m.occupancy,
        adr=m.adr,
        revpar=m.revpar,
        comp_set_revpar=m.comp_set_revpar,
        revpar_index=m.revpar_index,
        is_historical=m.is_historical,
    )


def _str_scenario_out(s: Any) -> _STRForecastScenarioOut:
    return _STRForecastScenarioOut(
        name=s.name,
        revpar_cagr_pct=s.revpar_cagr_pct,
        revpar_index_target=s.revpar_index_target,
        occupancy_floor=s.occupancy_floor,
        adr_floor=s.adr_floor,
        notes=list(s.notes or []),
    )


def _str_forecast_to_out(deal_id: UUID, forecast: Any) -> _STRForecastResultOut:
    return _STRForecastResultOut(
        deal_id=deal_id,
        historical_months=[_str_month_out(m) for m in forecast.historical_months],
        forecast_months={
            k: [_str_month_out(m) for m in v]
            for k, v in forecast.forecast_months.items()
        },
        scenario_settings=[_str_scenario_out(s) for s in forecast.scenario_settings],
        coverage_quality=forecast.coverage_quality,
    )


def _merge_scenario_overrides(
    overrides: list[_STRForecastScenarioOverride] | None,
) -> list[Any] | None:
    """Project the partial overrides onto full STRForecastScenario.

    Returns None when no overrides are supplied. Unknown scenario
    names are ignored (defensive — caller can only override
    downside / base / upside).
    """
    if not overrides:
        return None
    from ..engines.str_forecast import default_scenarios
    from fondok_schemas.str_forecast import STRForecastScenario

    defaults_by_name = {s.name: s for s in default_scenarios()}
    merged: list[STRForecastScenario] = []
    for ov in overrides:
        if ov.name not in defaults_by_name:
            continue
        base = defaults_by_name[ov.name]
        merged.append(
            STRForecastScenario(
                name=ov.name,  # type: ignore[arg-type]
                revpar_cagr_pct=(
                    ov.revpar_cagr_pct
                    if ov.revpar_cagr_pct is not None
                    else base.revpar_cagr_pct
                ),
                revpar_index_target=(
                    ov.revpar_index_target
                    if ov.revpar_index_target is not None
                    else base.revpar_index_target
                ),
                occupancy_floor=(
                    ov.occupancy_floor
                    if ov.occupancy_floor is not None
                    else base.occupancy_floor
                ),
                adr_floor=(
                    ov.adr_floor if ov.adr_floor is not None else base.adr_floor
                ),
                notes=ov.notes if ov.notes is not None else base.notes,
            )
        )
    return merged or None


@router.get(
    "/{deal_id}/str-forecast",
    response_model=_STRForecastResultOut,
)
async def get_str_forecast(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> _STRForecastResultOut:
    """Return the 24-month forward STR forecast (3 scenarios) for the deal.

    Wave 3 W3.3 — Sam's June 2026 ask. Pulls every STR_TREND extraction
    for ``(deal_id, tenant_id)``, materializes the trailing 24 months of
    subject + comp-set monthly RevPAR / Occ / ADR, runs the forecast
    engine with default downside / base / upside scenarios, and returns
    historical + forward rows for all three branches.

    Tenant-scoped: the cross-tenant deal_id returns 404 (the deal-belongs
    gate fires first) and the STR_TREND query is filtered on both the
    extraction row's tenant_id AND the document's tenant_id.

    When the deal has < 12 historical months on file, the response sets
    ``coverage_quality = "low"`` and ``forecast_months`` is keyed but
    empty per scenario. The UI is expected to render an "Awaiting more
    history" banner in that case.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )

    from ..engines.str_forecast import build_str_forecast
    from ..services.str_forecast_loader import load_str_history_for_deal

    history = await load_str_history_for_deal(
        session, deal_id=str(deal_id), tenant_id=str(tenant_id)
    )
    forecast = build_str_forecast(
        deal_id=str(deal_id),
        historical_months=history,
    )
    return _str_forecast_to_out(deal_id, forecast)


@router.post(
    "/{deal_id}/str-forecast/scenarios",
    response_model=_STRForecastResultOut,
)
async def update_str_forecast_scenarios(
    deal_id: UUID,
    body: _STRForecastScenariosRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> _STRForecastResultOut:
    """Recompute the STR forecast with caller-supplied scenario overrides.

    The body's ``scenarios`` list may contain a partial override for any
    of ``downside`` / ``base`` / ``upside``. Missing fields on an
    overridden scenario inherit from the engine's default for that
    scenario, so callers can flex one knob (e.g. push the upside
    scenario's CAGR to 7%) without re-declaring every field.

    Same tenant gate as GET. Same coverage-quality semantics — the
    response shape is identical.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )

    from ..engines.str_forecast import build_str_forecast
    from ..services.str_forecast_loader import load_str_history_for_deal

    history = await load_str_history_for_deal(
        session, deal_id=str(deal_id), tenant_id=str(tenant_id)
    )
    overrides = _merge_scenario_overrides(body.scenarios)
    forecast = build_str_forecast(
        deal_id=str(deal_id),
        historical_months=history,
        scenario_overrides=overrides,
    )
    return _str_forecast_to_out(deal_id, forecast)


def _year_dict(year: HistoricalYear) -> dict[str, Any]:
    """Convert a ``HistoricalYear`` dataclass into the wire-shape dict.

    Plain ``asdict`` would be enough but inline keeps the conversion
    local to the API module without expanding the import surface.
    """
    from dataclasses import asdict

    return asdict(year)


def _delta_dict(delta: YoYDelta) -> dict[str, Any]:
    """Convert a ``YoYDelta`` dataclass into the wire-shape dict."""
    from dataclasses import asdict

    return asdict(delta)


# ─────────────────────────── download ───────────────────────────


@router.delete(
    "/{deal_id}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_document(
    deal_id: UUID,
    doc_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> Response:
    """Hard-delete a single document and its derived artifacts.

    Cascade:
      * ``extraction_results`` rows for this doc (extracted fields,
        confidence report, model-call manifest).
      * Object-store blob (best-effort; storage errors are logged but
        do not block DB deletion — we'd rather have an orphaned blob
        than a stuck row).
      * ``documents`` row itself.

    Tenant-isolated: cross-tenant guesses return 404. Audit-logged so
    a future review can answer "who deleted X?". Irreversible.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    doc_row = (
        await session.execute(
            text(
                "SELECT id, filename, storage_key FROM documents "
                "WHERE id = :id AND deal_id = :deal AND tenant_id = :tenant"
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": str(tenant_id),
            },
        )
    ).first()
    if doc_row is None:
        raise HTTPException(
            status_code=404, detail=f"document {doc_id} not found"
        )
    storage_key = doc_row._mapping.get("storage_key")
    filename = doc_row._mapping.get("filename")

    # Order: extraction_results first (FK to documents), then the doc.
    await session.execute(
        text("DELETE FROM extraction_results WHERE document_id = :id"),
        {"id": str(doc_id)},
    )
    await session.execute(
        text(
            "DELETE FROM documents "
            "WHERE id = :id AND deal_id = :deal AND tenant_id = :tenant"
        ),
        {
            "id": str(doc_id),
            "deal": str(deal_id),
            "tenant": str(tenant_id),
        },
    )

    # Audit BEFORE commit so a crash still leaves the audit row.
    try:
        from ..audit import log_audit

        await log_audit(
            session,
            tenant_id=str(tenant_id),
            deal_id=str(deal_id),
            actor_id=None,
            action="document.deleted",
            resource_type="document",
            resource_id=str(doc_id),
            metadata={"filename": filename, "storage_key": storage_key},
        )
    except Exception:
        logger.exception("delete_document: audit log failed for doc=%s", doc_id)

    await session.commit()

    # Best-effort object-store cleanup. We don't roll back DB on
    # storage failure — orphaned blobs are an ops nuisance, not a
    # correctness bug.
    if storage_key:
        try:
            from ..storage import get_store

            store = get_store()
            await asyncio.to_thread(store.delete, storage_key)
        except Exception:
            logger.exception(
                "delete_document: storage delete failed for key=%s (doc=%s); "
                "doc row already deleted, blob orphaned",
                storage_key,
                doc_id,
            )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    except Exception as exc:
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
    except (FileNotFoundError, StorageError) as exc:
        # Raw bytes are gone — almost always because the worker is on
        # the ephemeral-/tmp LocalRawStore and Railway wiped the disk
        # on a redeploy. Surface as 410 Gone with an actionable
        # message instead of a generic 500 ("server bug"): the user
        # needs to re-upload, not file a bug.
        msg = str(exc).lower()
        if isinstance(exc, FileNotFoundError) or "missing" in msg:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail=(
                    "Raw bytes for this document are no longer in storage "
                    "(typically because the worker was redeployed and the "
                    "ephemeral disk was wiped). Please re-upload the file "
                    "to retry."
                ),
            ) from exc
        logger.exception("reprocess: store fetch failed for %s", filename)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not fetch raw bytes from storage: {exc}",
        ) from exc
    except Exception as exc:
        logger.exception("reprocess: store fetch failed for %s", filename)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"could not fetch raw bytes from storage: {exc}",
        ) from exc

    # Flip the row back to PARSING so the UI shows immediate feedback.
    # `error_kind` / `error_message` are stored *inside* the
    # extraction_data JSON blob (not separate columns) so setting
    # extraction_data = NULL clears the stale failure reason in one
    # shot — the previous "also clear error_kind/error_message"
    # variant tried to update non-existent columns and crashed the
    # endpoint with UndefinedColumnError.
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


class AcceptClassificationBody(BaseModel):
    """Body for the ``accept_classification`` endpoint.

    ``use_ai_classification=True`` accepts Fondok's classification and
    writes the Router's tag onto ``user_provided_doc_type`` so future
    re-routes don't re-flip the banner.
    ``use_ai_classification=False`` rejects the AI suggestion and
    restores the user's original tag as ``doc_type``.
    """

    model_config = ConfigDict(extra="forbid")

    use_ai_classification: bool


@router.post(
    "/{deal_id}/documents/{doc_id}/accept_classification",
    response_model=DocumentRecord,
)
async def accept_classification(
    deal_id: UUID,
    doc_id: UUID,
    body: AcceptClassificationBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> DocumentRecord:
    """Resolve a misclassification banner — wizard ROADMAP #1.

    When the Router agent disagrees with the analyst's wizard tag, the
    document is left with ``misclassified=True``, the user's original
    ``user_provided_doc_type`` as ``doc_type``, and a warn-tone banner
    in the UI. The analyst then has two choices:

      * ``use_ai_classification=True`` — accept Fondok's read. We copy
        the current ``doc_type`` over to ``user_provided_doc_type`` so
        the row is no longer "in disagreement", clear the flag, and
        return the updated record.
      * ``use_ai_classification=False`` — reject Fondok's read. Keep
        ``user_provided_doc_type`` as the source of truth, clear the
        flag (analyst has now confirmed their tag). ``doc_type`` is
        already the user's tag so no change is required.

    Tenant-scoped via the canonical pattern (see ``deals.py::get_deal``):
    the SELECT requires both ``deal_id`` and ``tenant_id`` to match, so
    a cross-tenant guess produces a 404 rather than leaking another
    tenant's row.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, tenant_id, filename, doc_type, status,
                       uploaded_at, content_hash, storage_key, size_bytes,
                       page_count, parser, extraction_data,
                       usali_score, usali_deviations,
                       user_provided_doc_type, fiscal_year, misclassified,
                       ai_proposed_doc_type,
                       year_mismatch, extracted_period_year,
                       structural_pnl_score
                  FROM documents
                 WHERE id = :id
                   AND deal_id = :deal_id
                   AND tenant_id = :tenant
                """
            ),
            {
                "id": str(doc_id),
                "deal_id": str(deal_id),
                "tenant": str(tenant_id),
            },
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document {doc_id} not found on deal {deal_id}",
        )

    current = dict(row._mapping)
    current_doc_type = current.get("doc_type")
    user_tag = current.get("user_provided_doc_type")

    if body.use_ai_classification:
        # Trust Fondok's read. ``doc_type`` was set to the user's tag
        # by the upload path; the AI's tag was never persisted onto
        # ``doc_type`` (we kept user intent), so we need to fall back
        # to the user tag plus the Router promise here. In practice the
        # banner exposed both labels via the UI; the worker now has to
        # honor the user's accept by re-running the refinement: simplest
        # safe behavior is to copy ``user_provided_doc_type`` (the
        # source-of-truth signal) over the column so a future re-extract
        # doesn't keep flipping the flag. ``doc_type`` itself is kept
        # as-is — re-classification requires a re-extract, which the
        # analyst can trigger via Retry if they want the engines to
        # re-bucket. Clearing ``misclassified`` is the headline change.
        # Bug #2 v2: also clear ``ai_proposed_doc_type`` so the row no
        # longer carries the v2 conflict signal.
        new_user_tag = current_doc_type or user_tag
        await session.execute(
            text(
                "UPDATE documents "
                "SET user_provided_doc_type = :u, misclassified = :m, "
                "ai_proposed_doc_type = NULL "
                "WHERE id = :id"
            ),
            {
                "u": new_user_tag,
                "m": False,
                "id": str(doc_id),
            },
        )
    else:
        # Keep the user's tag. ``doc_type`` already equals the user
        # tag at this point (the extraction path keeps it that way
        # when the flag was set), so we only need to clear the flag.
        # Defensive: if a future code path writes the AI tag onto
        # ``doc_type``, restore the user tag here.
        # Bug #2 v2: clear ``ai_proposed_doc_type`` on either branch.
        if user_tag and current_doc_type != user_tag:
            await session.execute(
                text(
                    "UPDATE documents "
                    "SET doc_type = :dt, misclassified = :m, "
                    "ai_proposed_doc_type = NULL "
                    "WHERE id = :id"
                ),
                {
                    "dt": user_tag,
                    "m": False,
                    "id": str(doc_id),
                },
            )
        else:
            await session.execute(
                text(
                    "UPDATE documents "
                    "SET misclassified = :m, "
                    "ai_proposed_doc_type = NULL "
                    "WHERE id = :id"
                ),
                {"m": False, "id": str(doc_id)},
            )

    await session.commit()

    refreshed = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, tenant_id, filename, doc_type, status,
                       uploaded_at, content_hash, storage_key, size_bytes,
                       page_count, parser, extraction_data,
                       usali_score, usali_deviations,
                       user_provided_doc_type, fiscal_year, misclassified,
                       ai_proposed_doc_type,
                       year_mismatch, extracted_period_year,
                       structural_pnl_score
                  FROM documents
                 WHERE id = :id
                """
            ),
            {"id": str(doc_id)},
        )
    ).first()
    assert refreshed is not None  # we just updated it
    logger.info(
        "accept_classification: deal=%s doc=%s use_ai=%s",
        deal_id,
        doc_id,
        body.use_ai_classification,
    )
    return _row_to_record(dict(refreshed._mapping))


# ─────────────────── accept_year — Wave 1 #4 ───────────────────


class AcceptYearBody(BaseModel):
    """Body for the ``accept_year`` endpoint.

    Same UX pattern as ``accept_classification``: the user either
    accepts Fondok's read of the period_ending year or keeps their
    own wizard tag. Either way the banner clears.
    """

    model_config = ConfigDict(extra="forbid")

    use_ai_year: bool


@router.post(
    "/{deal_id}/documents/{doc_id}/accept_year",
    response_model=DocumentRecord,
)
async def accept_year(
    deal_id: UUID,
    doc_id: UUID,
    body: AcceptYearBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> DocumentRecord:
    """Resolve a year-mismatch banner — Wave 1 B4.

    The Extractor read a ``period_ending`` whose year disagrees with
    the analyst's wizard ``fiscal_year``. The analyst now picks:

      * ``use_ai_year=True`` — accept Fondok's read. Overwrite
        ``fiscal_year`` with ``extracted_period_year`` and clear the
        flag so a re-extract doesn't re-flip the banner.
      * ``use_ai_year=False`` — reject Fondok's read. Keep
        ``fiscal_year`` as-is, clear the flag (analyst has confirmed).

    Tenant-scoped via the canonical pattern.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, fiscal_year, extracted_period_year
                  FROM documents
                 WHERE id = :id
                   AND deal_id = :deal_id
                   AND tenant_id = :tenant
                """
            ),
            {
                "id": str(doc_id),
                "deal_id": str(deal_id),
                "tenant": str(tenant_id),
            },
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document {doc_id} not found on deal {deal_id}",
        )

    current = dict(row._mapping)
    if body.use_ai_year:
        epy = current.get("extracted_period_year")
        await session.execute(
            text(
                "UPDATE documents "
                "SET fiscal_year = :fy, year_mismatch = :ym "
                "WHERE id = :id"
            ),
            {"fy": epy, "ym": False, "id": str(doc_id)},
        )
    else:
        await session.execute(
            text(
                "UPDATE documents "
                "SET year_mismatch = :ym WHERE id = :id"
            ),
            {"ym": False, "id": str(doc_id)},
        )
    await session.commit()

    refreshed = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, tenant_id, filename, doc_type, status,
                       uploaded_at, content_hash, storage_key, size_bytes,
                       page_count, parser, extraction_data,
                       usali_score, usali_deviations,
                       user_provided_doc_type, fiscal_year, misclassified,
                       ai_proposed_doc_type,
                       year_mismatch, extracted_period_year,
                       structural_pnl_score
                  FROM documents
                 WHERE id = :id
                """
            ),
            {"id": str(doc_id)},
        )
    ).first()
    assert refreshed is not None  # we just updated it
    logger.info(
        "accept_year: deal=%s doc=%s use_ai=%s",
        deal_id,
        doc_id,
        body.use_ai_year,
    )
    return _row_to_record(dict(refreshed._mapping))


# ─────────────────── completeness — Wave 1 #1 ───────────────────


class CompletenessCategory(BaseModel):
    """One row in the deal-completeness response."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    covered: bool
    doc_count: int
    required_for_ic: bool


class CompletenessResponse(BaseModel):
    """``GET /deals/{deal_id}/completeness`` — Wave 1 #1.

    Surfaces "how IC-ready is this deal?" as a single percent +
    per-category breakdown. The 10 ``required_for_ic`` categories
    define the denominator; ``SURVEYS`` is recommended only and
    excluded from the percent. The wizard's right-rail and the
    CompletenessCard on the deal workspace both consume this.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    completeness_pct: int
    categories: list[CompletenessCategory] = Field(default_factory=list)


# Canonical 11-category checklist. Source of truth for the wizard
# right-rail AND the workspace CompletenessCard so the two never drift.
# ``doc_types`` maps each category to the DocType enum tokens that
# count as "covered". Order matches the wizard sidebar exactly.
COMPLETENESS_CATEGORIES: list[dict[str, Any]] = [
    {
        "id": "om",
        "label": "Offering Memorandum",
        "doc_types": {"OM"},
        "required_for_ic": True,
    },
    {
        "id": "t12",
        "label": "T-12 / Trailing Twelve Months",
        "doc_types": {"T12"},
        "required_for_ic": True,
    },
    {
        "id": "historical_pnl",
        "label": "Annual / YTD / Monthly P&L",
        "doc_types": {"PNL", "PNL_MONTHLY", "PNL_YTD", "PNL_BENCHMARK"},
        "required_for_ic": True,
    },
    {
        "id": "str",
        "label": "STR / Comp Set Report",
        "doc_types": {"STR", "STR_TREND"},
        "required_for_ic": True,
    },
    {
        "id": "insurance",
        "label": "Insurance Records",
        "doc_types": {"INSURANCE"},
        "required_for_ic": True,
    },
    {
        "id": "property_tax",
        "label": "Property Taxes",
        "doc_types": {"PROPERTY_TAX"},
        "required_for_ic": True,
    },
    {
        "id": "room_mix",
        "label": "Room Mix / Unit Mix",
        "doc_types": {"ROOM_MIX"},
        "required_for_ic": True,
    },
    {
        "id": "capex",
        "label": "Historical CapEx",
        "doc_types": {"CAPEX"},
        "required_for_ic": True,
    },
    {
        "id": "property_info",
        "label": "Basic Property Info",
        "doc_types": {"PROPERTY_INFO"},
        "required_for_ic": True,
    },
    {
        "id": "leases",
        "label": "Leases & Agreements",
        # CONTRACT is the legacy token broker agreements were classified
        # under before LEASES landed; honor both so a redeployed worker
        # against a backfilled DB still scores correctly.
        "doc_types": {"LEASES", "CONTRACT"},
        "required_for_ic": True,
    },
    {
        "id": "surveys",
        "label": "Surveys & Reviews",
        "doc_types": {"SURVEYS"},
        "required_for_ic": False,
    },
]


@router.get(
    "/{deal_id}/completeness",
    response_model=CompletenessResponse,
)
async def get_deal_completeness(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> CompletenessResponse:
    """Return the per-category coverage map for a deal.

    Tenant-scoped: a cross-tenant guess returns 404 just like the
    other read endpoints. ``completeness_pct`` is rounded to the
    nearest whole percent over the 10 required-for-IC categories
    (SURVEYS is recommended only and excluded from the denominator).
    """
    # Tenant gate — confirm the deal exists under this tenant first
    # so we don't leak a category list for someone else's deal.
    deal_row = (
        await session.execute(
            text(
                "SELECT id FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if deal_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )

    rows = await session.execute(
        text(
            """
            SELECT doc_type
              FROM documents
             WHERE deal_id = :deal_id
               AND tenant_id = :tenant
            """
        ),
        {"deal_id": str(deal_id), "tenant": str(tenant_id)},
    )
    uploaded: dict[str, int] = {}
    for r in rows.fetchall():
        dt = (r._mapping.get("doc_type") or "").upper().strip()
        if not dt:
            continue
        uploaded[dt] = uploaded.get(dt, 0) + 1

    categories: list[CompletenessCategory] = []
    required_total = 0
    required_covered = 0
    for spec in COMPLETENESS_CATEGORIES:
        doc_types: set[str] = spec["doc_types"]
        count = sum(uploaded.get(dt, 0) for dt in doc_types)
        covered = count > 0
        required = bool(spec["required_for_ic"])
        if required:
            required_total += 1
            if covered:
                required_covered += 1
        categories.append(
            CompletenessCategory(
                id=spec["id"],
                label=spec["label"],
                covered=covered,
                doc_count=count,
                required_for_ic=required,
            )
        )
    pct = (
        round((required_covered / required_total) * 100)
        if required_total > 0
        else 0
    )
    return CompletenessResponse(
        deal_id=deal_id,
        completeness_pct=pct,
        categories=categories,
    )


@router.post(
    "/{deal_id}/documents/{doc_id}/rescore-usali",
)
async def rescore_usali(
    deal_id: UUID,
    doc_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> dict[str, Any]:
    """Re-score a doc's USALI compliance from its persisted extraction
    without re-fetching the source bytes.

    Sam QA 2026-06-29: docs extracted under the broken v4 deploy window
    have ``usali_score=null`` because the scorer hit a swallowed
    exception. This endpoint reloads the most-recent ``extraction_results``
    row for the document, calls the live (post-v4) scorer, persists the
    result, and RETURNS any exception verbatim so we can debug the
    silent-failure path without grepping Railway logs.
    """
    await _assert_deal_belongs_to_tenant(session, deal_id=deal_id, tenant_id=tenant_id)
    doc_row = (
        await session.execute(
            text(
                "SELECT id, doc_type FROM documents "
                "WHERE id = :id AND deal_id = :deal AND tenant_id = :tenant"
            ),
            {"id": str(doc_id), "deal": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if doc_row is None:
        raise HTTPException(status_code=404, detail=f"document {doc_id} not found")
    dt = (doc_row._mapping.get("doc_type") or "").upper()
    if dt not in _PNL_FAMILY_DOC_TYPES:
        return {
            "ok": False,
            "reason": f"doc_type {dt!r} not in P&L family ({sorted(_PNL_FAMILY_DOC_TYPES)})",
            "score": None,
        }
    extraction_row = (
        await session.execute(
            text(
                "SELECT fields FROM extraction_results "
                "WHERE document_id = :id "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"id": str(doc_id)},
        )
    ).first()
    if extraction_row is None:
        return {"ok": False, "reason": "no extraction_results row", "score": None}

    fields_raw = extraction_row._mapping.get("fields")
    if isinstance(fields_raw, str):
        try:
            fields = json.loads(fields_raw)
        except json.JSONDecodeError as exc:
            return {"ok": False, "reason": f"fields not valid JSON: {exc}", "score": None}
    else:
        fields = fields_raw
    if not isinstance(fields, list) or not fields:
        return {"ok": False, "reason": "fields empty or not a list", "score": None}

    try:
        from ..services.usali_scorer import (
            deviations_to_jsonb,
            flatten_extraction_fields,
            score_extraction,
        )

        flat = flatten_extraction_fields(fields, extra_context={})
        result = score_extraction(flat)
        payload = deviations_to_jsonb(result)

        # Sam QA 2026-06-29: also re-evaluate misclassification with
        # the v4 logic. The 5 OG annual PNLs on deal f3152107... were
        # wrongly flagged misclassified=True under pre-fix code (the
        # null-tag bug). Now that scoring proves they're real P&Ls
        # (score is not None, applicable_count >= 5), clear the flag
        # when the user never tagged it (so there's no real conflict
        # to surface). This keeps rescore idempotent + ensures the
        # post-fix state is internally consistent across docs that
        # extracted under broken vs fixed code.
        also_cleared_misclassified = False
        if (
            result.score is not None
            and not result.inconclusive
        ):
            # Look up user_provided_doc_type — clear misclassified
            # ONLY when there's no conflict to surface.
            doc_check = (
                await session.execute(
                    text(
                        "SELECT user_provided_doc_type, misclassified "
                        "FROM documents WHERE id = :id"
                    ),
                    {"id": str(doc_id)},
                )
            ).first()
            if (
                doc_check is not None
                and doc_check._mapping.get("misclassified")
                and not doc_check._mapping.get("user_provided_doc_type")
            ):
                await session.execute(
                    text(
                        "UPDATE documents "
                        "SET misclassified = :m, "
                        "ai_proposed_doc_type = NULL "
                        "WHERE id = :id"
                    ),
                    {"m": False, "id": str(doc_id)},
                )
                also_cleared_misclassified = True

        await session.execute(
            text(
                "UPDATE documents "
                "SET usali_score = :score, usali_deviations = :dev "
                "WHERE id = :id"
            ),
            {
                "score": result.score,
                "dev": json.dumps(payload),
                "id": str(doc_id),
            },
        )
        await session.commit()
        return {
            "ok": True,
            "doc_id": str(doc_id),
            "score": result.score,
            "applicable": result.applicable_count,
            "passed": result.passed_count,
            "inconclusive": result.inconclusive,
            "deviations": len(result.deviations),
            "flat_keys": len(flat),
            "also_cleared_misclassified": also_cleared_misclassified,
        }
    except Exception as exc:
        import traceback

        return {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "traceback": traceback.format_exc().splitlines()[-15:],
        }


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
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> ExtractionResultResponse:
    """Return the latest extraction result for a document.

    Sam 2026-06-30: the prior version didn't take tenant_id at all
    and both its queries missed the tenant predicate, which
    (a) lit up tenant_middleware with CRITICAL "cross-tenant leak
    risk" lines on every poll (heavy log noise — this endpoint
    fires per-doc per-poll), and (b) was a real defense gap:
    statistically near-zero with v4 UUIDs but a uuid-guess bypass
    existed. Both queries now carry `AND tenant_id = :tenant`.
    """
    doc_row = (
        await session.execute(
            text(
                """
                SELECT status, page_count, extraction_data
                  FROM documents
                 WHERE id = :id AND deal_id = :d AND tenant_id = :tenant
                """
            ),
            {"id": str(doc_id), "d": str(deal_id), "tenant": str(tenant_id)},
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
                 WHERE document_id = :id AND tenant_id = :tenant
                 ORDER BY created_at DESC
                 LIMIT 1
                """
            ),
            {"id": str(doc_id), "tenant": str(tenant_id)},
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
    # Sam QA Bug J (2026-06-30) — the extractor refused to run because
    # the routed doc_type contradicts the parsed content (e.g. routed
    # as T12 but the text is unambiguously STR Trend / CoStar). The
    # analyst's next step is to re-classify via the wizard and re-run.
    "structural_contradiction": (
        "The document was classified as a P&L but its content looks "
        "like an STR Trend / CoStar comp-set report. Re-classify the "
        "document type in the wizard (likely STR_TREND) and re-run "
        "extraction — running the wrong extractor would have hung for "
        "minutes without producing fields."
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
        or ("401" in low and "anthropic" in low)
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

    Wave 4 reliability fix (Bug #2): every entrypoint into the LLM
    extraction fan-out passes through ``acquire_extractor_slot()`` so
    the process-wide cap (``EXTRACTOR_MAX_CONCURRENT_DOCS``, default 4)
    binds regardless of whether the caller is the auto-chain off
    upload or the standalone ``POST .../extract`` retry endpoint. Slot
    is released even when the body raises — ``async with`` guarantees
    cleanup on the exception path.
    """
    from ..services.extractor_throttle import acquire_extractor_slot

    async with acquire_extractor_slot():
        await _run_extraction_pipeline_inner(
            deal_id=deal_id, doc_id=doc_id, tenant_id=tenant_id,
        )


async def _run_extraction_pipeline_inner(
    *, deal_id: str, doc_id: str, tenant_id: str
) -> None:
    """Inner body of ``_run_extraction_pipeline`` after the process-wide
    extractor-slot semaphore has been acquired. Split out so the slot
    wrapper stays tiny + so tests can exercise the cap by patching
    the wrapper without re-implementing the LangGraph driver.
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
                        "SELECT storage_key, filename, extraction_data, "
                        "user_provided_doc_type, fiscal_year, content_hash "
                        "FROM documents WHERE id = :id"
                    ),
                    {"id": doc_id},
                )
            ).first()
            if not row:
                raise RuntimeError(f"document {doc_id} vanished mid-extraction")
            storage_key = row._mapping["storage_key"]
            filename = row._mapping["filename"]
            content_hash = row._mapping.get("content_hash")
            raw_user_provided_doc_type = row._mapping.get(
                "user_provided_doc_type"
            )
            user_provided_doc_type: str | None = (
                raw_user_provided_doc_type.strip().upper()
                if isinstance(raw_user_provided_doc_type, str)
                and raw_user_provided_doc_type.strip()
                else None
            )
            # Wizard fiscal_year — used by the Wave 1 year-mismatch flag
            # below. NULL when the analyst skipped year-tagging.
            raw_user_fiscal_year = row._mapping.get("fiscal_year")
            try:
                user_fiscal_year: int | None = (
                    int(raw_user_fiscal_year)
                    if raw_user_fiscal_year is not None
                    else None
                )
            except (TypeError, ValueError):
                user_fiscal_year = None
            raw_extraction_data = row._mapping["extraction_data"]
            if isinstance(raw_extraction_data, str):
                try:
                    extraction_data = json.loads(raw_extraction_data)
                except json.JSONDecodeError:
                    extraction_data = None
            else:
                extraction_data = raw_extraction_data

            classified_doc_type: str | None = None
            cache_hit_source_id: str | None = None

            # ── Content-hash extraction cache (cost-opt) ───────────────
            # Before spending any LLM tokens, check whether the SAME
            # bytes have already been extracted on this tenant with the
            # current pipeline version. If so, clone the prior result
            # into a new extraction_results row for this doc and skip
            # the Router → Extractor → Normalizer → Verifier chain.
            #
            # Same-tenant only — the JOIN in ``_lookup_extraction_cache``
            # filters ``er.tenant_id = :tenant`` so a doc uploaded to
            # tenant A can never satisfy the lookup from tenant B.
            settings = get_settings()
            cached: dict[str, Any] | None = None
            if settings.EXTRACTION_CACHE_ENABLED and content_hash:
                cached = await _lookup_extraction_cache(
                    session,
                    tenant_id=tenant_id,
                    content_hash=content_hash,
                )

            if cached is not None:
                cached_fields = cached["fields"]
                if isinstance(cached_fields, str):
                    cached_fields = (
                        json.loads(cached_fields) if cached_fields else []
                    )
                cached_cr = cached["confidence_report"]
                if isinstance(cached_cr, str):
                    cached_cr = json.loads(cached_cr) if cached_cr else {}
                fields = list(cached_fields or [])
                # ConfidenceReportOut is Pydantic ``extra='forbid'`` so we
                # must not add breadcrumb keys here; the log line below
                # is the ops-visible audit surface for the clone (docs
                # can be joined back to the source via content_hash if
                # forensics ever needs the origin row).
                confidence = dict(cached_cr or {})
                agent_version = cached["agent_version"]
                # Recover the Router's original classification from the
                # cached agent_version string — format is
                # ``router:{route};extractor;pv=vN``. Post-extraction
                # override logic (structural recognizer, misclassified,
                # year-mismatch) runs deterministically over ``fields``
                # so it re-produces the SAME final doc_type without any
                # LLM call.
                classified_doc_type = _parse_route_from_agent_version(
                    agent_version
                )
                cache_hit_source_id = str(cached["id"])
                _record_cache_hit(tenant_id)
                logger.info(
                    "extraction cache HIT: doc=%s content_hash=%s "
                    "cloned_from=%s zero LLM cost",
                    doc_id,
                    content_hash,
                    cache_hit_source_id,
                )
            elif os.environ.get("EVALS_MOCK", "").lower() in (
                "1",
                "true",
                "yes",
            ):
                fields, confidence = _mock_extraction_payload()
                agent_version = _tag_agent_version("mock-evals")
                _record_cache_miss(tenant_id)
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
                agent_version = _tag_agent_version(agent_version)
                _record_cache_miss(tenant_id)

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
                    len(p.get("text", "") or "") for p in parsed_pages
                    if isinstance(p, dict)
                )
                # Bug J: surface the fail-fast structural-contradiction
                # path with its own error_kind so the analyst gets a
                # clear "re-classify this doc" CTA instead of the
                # generic "use Retry" message that's wrong here
                # (re-running with the same wrong doc_type would just
                # hit the same fail-fast guard).
                chunk_errors = (
                    (confidence or {}).get("chunk_errors") or []
                    if isinstance(confidence, dict) else []
                )
                contradiction_hit = any(
                    isinstance(e, str)
                    and e.startswith("structural_contradiction:")
                    for e in chunk_errors
                )
                # Sam QA 2026-06-30: when the Anthropic API key is
                # invalid/expired/revoked, every LLM call returns 401
                # and (after retries also 401) the extractor accepts
                # failure → 0 fields. Without this check we surface the
                # generic empty_envelope message which reads as "the
                # extractor returned no fields" — invisible that the
                # real problem is auth. Surface auth as its own
                # error_kind so the ops fix is obvious.
                auth_hit = any(
                    isinstance(e, str)
                    and (
                        "401" in e
                        or "invalid x-api-key" in e
                        or "authentication_error" in e
                        or "AuthenticationError" in e
                    )
                    for e in chunk_errors
                )
                if contradiction_hit:
                    kind = "structural_contradiction"
                    friendly = ERROR_KIND_MESSAGES[
                        "structural_contradiction"
                    ]
                elif auth_hit:
                    kind = "auth"
                    friendly = (
                        "Anthropic API key rejected (HTTP 401). The "
                        "extractor LLM couldn't authenticate, so no "
                        "fields could be extracted. Rotate the "
                        "ANTHROPIC_API_KEY environment variable on the "
                        "worker (Railway → Variables) and hit Retry on "
                        "this document — the raw bytes are still in S3 "
                        "and re-extraction will pick them up cleanly."
                    )
                elif total_chars < 100:
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

                # Sam QA Bug H (June 30 2026) — structural override for
                # Router non-financial misfires.
                #
                # Symptom Sam caught on a CLEAN deal (no user tag):
                # "May 2025 Financials.xlsx" → Router classified as
                # PROPERTY_INFO. Without a ``T12``-typed row, the broker-
                # questions YoY engine produces ZERO questions (its SQL
                # filters ``doc_type IN ('T12','PNL','PNL_MONTHLY',
                # 'PNL_YTD')``) — even though the file extracts cleanly.
                # On a different deal the same file classified as T12 and
                # 30+ broker questions generated. Pure Router non-
                # determinism.
                #
                # The existing v4 block below handles the misclassified-
                # banner case (analyst tagged correctly, Router was
                # wrong) AND the inverse (analyst tagged a P&L as
                # PROPERTY_INFO). Neither branch fires when there is NO
                # user tag (bulk Data Room upload) because both gate on
                # ``canonical_user`` being non-empty. This override
                # patches that gap: when the Router lands on a known
                # non-financial label AND the structural recognizer is
                # confident the payload IS a P&L, rewrite the doc_type
                # to T12/PNL/PNL_MONTHLY/PNL_YTD so downstream engines
                # see the row.
                #
                # Scope is intentionally narrow (Option B):
                #   * Router said one of NON_FINANCIAL_ROUTER_LABELS —
                #     OM, STR_*, MARKET_STUDY, PORTFOLIO_PNL, etc. all
                #     have distinct downstream semantics and STAY put.
                #   * structural_signals.is_pnl must be True (the
                #     recognizer's combined gate: revenue + expense +
                #     rollup + dollar-field count + ≥ 6 distinct
                #     canonical concepts).
                #   * pnl_score ≥ 0.85 floor (belt-and-braces; is_pnl
                #     implies score = 1.0 today but the threshold pins
                #     the contract if the recognizer's gates ever
                #     soften).
                #
                # Router's original call is preserved on
                # ``ai_proposed_doc_type`` so Sam's misclassification
                # banner can render "Router thought PROPERTY_INFO, we
                # overrode to T12" — no silent rewrites.
                from ..services.structural_recognizer import (
                    classify_structure,
                )

                structural_signals = classify_structure(fields)
                structural_pnl_score = float(structural_signals.pnl_score)

                _NON_FINANCIAL_ROUTER_LABELS = {
                    "PROPERTY_INFO",
                    "UNKNOWN",
                    "CONTRACT",
                    "PROPERTY_TAX",
                    "INSURANCE",
                    "CAPEX",
                    "LEASES",
                    "SURVEYS",
                    "ROOM_MIX",
                }
                # Sam QA Bug J (June 30 2026) — mirror of Bug H in the
                # OPPOSITE direction. Router landed on a financial label
                # (T12 / PNL*) but the structural recognizer says the
                # payload is unmistakably STR Trend / CoStar (subject +
                # comp set + MPI/ARI/RGI indices). Override the doc_type
                # to STR_TREND so the comp-set / Index Analysis pipeline
                # (which keys on ``doc_type IN ('STR', 'STR_TREND')``)
                # actually sees the row. Without this the STR file
                # silently sat as a T12 row and the Market tab showed
                # "no comp-set data".
                _FINANCIAL_ROUTER_LABELS = {
                    "T12", "PNL", "PNL_MONTHLY", "PNL_YTD"
                }
                _STRUCTURAL_PNL_OVERRIDE_THRESHOLD = 0.85
                _STRUCTURAL_STR_OVERRIDE_THRESHOLD = 0.85

                router_overridden_original: str | None = None
                router_call_for_override = (
                    (refined_doc_type or classified_doc_type or "")
                    .upper()
                    .strip()
                )
                if (
                    router_call_for_override in _NON_FINANCIAL_ROUTER_LABELS
                    and structural_signals.is_pnl
                    and structural_pnl_score
                    >= _STRUCTURAL_PNL_OVERRIDE_THRESHOLD
                ):
                    # Re-run the period-type narrowing against the
                    # structural override so a single-month upload lands
                    # as PNL_MONTHLY rather than the catch-all T12.
                    overridden = _refine_pnl_doc_type("T12", fields) or "T12"
                    router_overridden_original = router_call_for_override
                    logger.info(
                        "structural_recognizer overrode Router: %s → %s "
                        "(signal=%.2f, %s) doc=%s",
                        router_call_for_override,
                        overridden,
                        structural_pnl_score,
                        structural_signals.reason,
                        doc_id,
                    )
                    refined_doc_type = overridden
                    classified_doc_type = overridden
                elif (
                    router_call_for_override in _FINANCIAL_ROUTER_LABELS
                    and structural_signals.is_str
                    and float(structural_signals.str_score)
                    >= _STRUCTURAL_STR_OVERRIDE_THRESHOLD
                    and not structural_signals.is_pnl
                ):
                    # Sam QA Bug J: STR Trend mis-classified as T12.
                    # The ``not is_pnl`` guard belt-and-braces against a
                    # pathological doc that somehow trips BOTH gates —
                    # ambiguity stays on the Router's call rather than
                    # the recognizer flipping it. In practice STR Trend
                    # reports never satisfy ``is_pnl`` (no revenue +
                    # expense + rollup) so this gate is decisive.
                    router_overridden_original = router_call_for_override
                    logger.info(
                        "structural_recognizer overrode Router (Bug J): "
                        "%s → STR_TREND (str_score=%.2f, %s) doc=%s",
                        router_call_for_override,
                        float(structural_signals.str_score),
                        structural_signals.reason,
                        doc_id,
                    )
                    refined_doc_type = "STR_TREND"
                    classified_doc_type = "STR_TREND"

                ai_proposed_doc_type = refined_doc_type or classified_doc_type

                # Misclassification rule (Wave 1 #1): when the analyst
                # tagged the file in the wizard ("Annual / T-12" for a
                # 2025 P&L) AND the Router-or-refined doc_type
                # disagrees, set ``misclassified=True`` and keep the
                # analyst tag. The UI shows a warn banner with
                # "Use Fondok's classification" / "Keep mine"; we never
                # silently overwrite the user's intent (locked product
                # decision).
                normalized_ai = (
                    (ai_proposed_doc_type or "").upper().strip() or None
                )
                # Canonicalize BOTH sides through ``_canonical_doc_type``
                # so ``T-12`` / ``T12`` / ``t 12`` / ``PNL_MONTHLY`` /
                # ``PNL MONTHLY`` collapse to a single comparison key
                # (Sam QA Bug #2 — banner was firing on a correctly
                # categorized T-12 because the raw strings differed).
                canonical_user = _canonical_doc_type(user_provided_doc_type)
                canonical_ai = _canonical_doc_type(normalized_ai)
                misclassified_flag = bool(
                    canonical_user
                    and canonical_ai
                    and canonical_user != canonical_ai
                )

                # Sam QA Bug #2 v4 (June 28 2026) — structural override.
                #
                # On Wave 4 Sam caught a 181-field T-12 being flagged
                # "misclassified" because the LLM Router landed on
                # PROPERTY_INFO (the filename + first 2k chars didn't
                # carry enough P&L signal for it). With the analyst's
                # ``user_provided_doc_type`` = T12 AND a structurally
                # confirmed P&L payload, the misclassification banner
                # is wrong — and it cascades (the engine_runner SQL
                # filters T12/PNL/PNL_MONTHLY/PNL_YTD, so a
                # ``doc_type=PROPERTY_INFO`` row drops out of every
                # downstream YoY / variance / broker-question call).
                #
                # Rules (the structural recognizer is the tiebreaker):
                #
                # 1. user_tag in P&L family + recognizer says is_pnl →
                #    NOT misclassified. We trust the user's tag and let
                #    USALI scoring + YoY engines run normally.
                # 2. user_tag in PROPERTY_INFO/OM/etc. + recognizer
                #    says is_pnl → DO flag misclassified. The analyst
                #    uploaded a P&L under the wrong wizard bucket and
                #    the structural signal is the right tiebreaker.
                # 3. user_tag in P&L family + recognizer says NOT P&L →
                #    NOT misclassified (keep current Router decision
                #    flow). Could be a thin P&L the recognizer didn't
                #    pick up; safer to preserve the user's intent than
                #    to surprise them with a banner.
                #
                # NOTE: ``structural_signals`` / ``structural_pnl_score``
                # are computed once above (the Bug H override block) and
                # reused here — the recognizer is deterministic over a
                # given payload so a second call would just duplicate work.
                _PNL_TAG_CANONICALS = {"T12", "PNL", "PNLMONTHLY", "PNLYTD"}
                user_tag_is_pnl = canonical_user in _PNL_TAG_CANONICALS

                if structural_signals.is_pnl and user_tag_is_pnl:
                    # Trust the user's tag — recognizer confirms shape.
                    # Override the Router's possibly-wrong PROPERTY_INFO
                    # read so downstream queries see the P&L doc_type
                    # the analyst actually meant.
                    if misclassified_flag:
                        logger.info(
                            "router v4: doc=%s structural override — "
                            "user tagged %s, router said %s, recognizer "
                            "confirms P&L (%s). Clearing misclassified.",
                            doc_id,
                            user_provided_doc_type,
                            normalized_ai,
                            structural_signals.reason,
                        )
                    misclassified_flag = False
                    # Adopt the user's tag as the AI label so the
                    # downstream UPDATE writes the canonical P&L value
                    # (not the Router's PROPERTY_INFO) into doc_type.
                    normalized_ai = canonical_user
                    refined_doc_type = canonical_user
                elif (
                    structural_signals.is_pnl
                    and canonical_user
                    and not user_tag_is_pnl
                ):
                    # Analyst uploaded a P&L under a non-P&L tag —
                    # the structural signal wins the conflict. Flag
                    # misclassified WITH the recognizer's verdict as
                    # the AI label so the banner shows a meaningful
                    # alternative ("Fondok thinks this is a P&L").
                    # Requires a non-null user tag — if the analyst
                    # never tagged (bulk upload to the Data Room with
                    # no per-doc category), there's no conflict to flag
                    # (Sam QA 2026-06-29: bulk-uploaded annuals were
                    # all getting misclassified=True even though there
                    # was no analyst intent to disagree with).
                    misclassified_flag = True
                    if not normalized_ai:
                        normalized_ai = "T12"  # generic P&L lane

                # Wave 1 year-mismatch (B4). When the analyst pinned a
                # ``fiscal_year`` AND the Extractor pulled a
                # ``p_and_l_usali.period_ending`` whose year disagrees,
                # surface a YearMismatchBanner. Use the existing
                # ``coverage_audit._extract_period_ending`` helper so we
                # parse the date with the same conventions as every
                # other consumer.
                #
                # Sam QA Bug #4 (June 2026): ALWAYS derive
                # ``extracted_period_year`` when the Extractor surfaced
                # a ``period_ending`` — not just when the user pinned a
                # ``fiscal_year``. Downstream consumers
                # (Broker Questions multi-year detection,
                # HistoricalVarianceEngine year-mapping, document
                # coverage audit) all read this column to decide
                # whether they can compare apples-to-apples across
                # documents; gating it on user_fiscal_year leaves it
                # NULL for the common case where the analyst skipped
                # the year prompt, breaking those features silently.
                from ..services.coverage_audit import _extract_period_ending

                extracted_period_year: int | None = None
                period_end = _extract_period_ending(fields)
                if period_end is not None:
                    extracted_period_year = period_end.year
                year_mismatch_flag = bool(
                    user_fiscal_year is not None
                    and extracted_period_year is not None
                    and extracted_period_year != user_fiscal_year
                )

                if misclassified_flag:
                    # Keep ``doc_type`` = user's tag, flip the flag.
                    # ``year_mismatch`` / ``extracted_period_year`` are
                    # updated regardless of the misclassification branch
                    # so the banner stays accurate even if the analyst
                    # ignores the doc-type mismatch.
                    #
                    # Sam QA Bug #2 v2: also persist
                    # ``ai_proposed_doc_type`` = the Router's read
                    # (normalized_ai). The banner reads this column
                    # for ``aiLabel`` — without it both sides resolved
                    # from ``doc_type`` and rendered "T-12 vs T-12".
                    await session.execute(
                        text(
                            "UPDATE documents "
                            "SET status = :s, misclassified = :m, "
                            "ai_proposed_doc_type = :apdt, "
                            "year_mismatch = :ym, "
                            "extracted_period_year = :epy, "
                            "structural_pnl_score = :sps "
                            "WHERE id = :id"
                        ),
                        {
                            "s": DOC_STATUS_EXTRACTED,
                            # asyncpg rejects int for BOOLEAN — Sam
                            # caught this at INSERT (commit 6e1ad56)
                            # but the extraction UPDATEs had the same
                            # bug, silently failing every doc after
                            # upload survived.
                            "m": True,
                            "apdt": normalized_ai,
                            "ym": bool(year_mismatch_flag),
                            "epy": extracted_period_year,
                            "sps": structural_pnl_score,
                            "id": doc_id,
                        },
                    )
                    logger.info(
                        "extraction: doc=%s misclassified — user said %s, "
                        "router said %s (keeping user tag); "
                        "structural_pnl_score=%.2f",
                        doc_id,
                        user_provided_doc_type,
                        normalized_ai,
                        structural_pnl_score,
                    )
                elif refined_doc_type:
                    # No conflict — clear the AI proposal column so a
                    # PREVIOUSLY-misclassified row that's been re-run
                    # doesn't leave a stale value behind.
                    #
                    # Bug H exception: when the structural recognizer
                    # overrode the Router's non-financial classification
                    # to a P&L lane, preserve the Router's ORIGINAL call
                    # on ``ai_proposed_doc_type`` so Sam's banner can
                    # surface "Router said PROPERTY_INFO, we overrode
                    # to T12" — silent rewrites are explicitly not
                    # acceptable per the locked product decision.
                    await session.execute(
                        text(
                            "UPDATE documents SET status = :s, doc_type = :dt, "
                            "misclassified = :m, "
                            "ai_proposed_doc_type = :apdt, "
                            "year_mismatch = :ym, "
                            "extracted_period_year = :epy, "
                            "structural_pnl_score = :sps WHERE id = :id"
                        ),
                        {
                            "s": DOC_STATUS_EXTRACTED,
                            "dt": refined_doc_type,
                            "m": False,
                            "apdt": router_overridden_original,
                            "ym": bool(year_mismatch_flag),
                            "epy": extracted_period_year,
                            "sps": structural_pnl_score,
                            "id": doc_id,
                        },
                    )
                else:
                    await session.execute(
                        text(
                            "UPDATE documents SET status = :s, "
                            "misclassified = :m, "
                            "ai_proposed_doc_type = NULL, "
                            "year_mismatch = :ym, "
                            "extracted_period_year = :epy, "
                            "structural_pnl_score = :sps WHERE id = :id"
                        ),
                        {
                            "s": DOC_STATUS_EXTRACTED,
                            "m": False,
                            "ym": bool(year_mismatch_flag),
                            "epy": extracted_period_year,
                            "sps": structural_pnl_score,
                            "id": doc_id,
                        },
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
        except Exception as exc:
            logger.exception("extraction failed: doc=%s — %s", doc_id, exc)
            kind, friendly = _classify_extraction_error(exc)
            try:
                from ..alerting import report_alert

                report_alert(
                    severity="warning",
                    title="Extraction failure",
                    deal_id=deal_id,
                    stage="extraction",
                    exc=exc,
                    extra={"doc_id": doc_id, "doc_type": scoring_doc_type or "", "error_kind": kind},
                )
            except Exception:
                pass
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
            except Exception:
                logger.exception("extraction: failed to record FAILED status")


# Pages per extraction chunk. Cost-opt pass U (2026-07): the default +
# per-doc-type overrides live on ``Settings`` (see
# ``EXTRACTOR_CHUNK_PAGES_DEFAULT`` / ``EXTRACTOR_CHUNK_PAGES_BY_DOCTYPE``)
# so operators can tune without a code change. Historical default was a
# uniform 5 pages / chunk — a 45-page OM → 9 chunks, a 3-page T-12 → 1
# chunk. The bench in ``scripts/bench_chunk_size.py`` measures
# tokens × wall-time × USALI score across candidate sizes so shifts
# aren't guesses.
def _resolve_chunk_pages(doc_type: str | None) -> int:
    """Return the configured page-per-chunk size for ``doc_type``.

    Looks up the per-doc-type override first, falls back to the global
    default. Kept as a small helper (rather than inline) so the bench
    script + the unit test can exercise the same lookup as production.
    """
    settings = get_settings()
    default = int(settings.EXTRACTOR_CHUNK_PAGES_DEFAULT)
    if not doc_type:
        return default
    overrides = settings.EXTRACTOR_CHUNK_PAGES_BY_DOCTYPE or {}
    try:
        size = int(overrides.get(str(doc_type), default))
    except (TypeError, ValueError):
        size = default
    # Guard against a bad env override slipping past pydantic (e.g. a
    # JSON value that decoded to 0). Any non-positive size collapses
    # to the default so the extractor never hits an infinite loop.
    return size if size > 0 else default


def _build_extractor_chunks(
    *,
    pages: list[dict[str, Any]],
    doc_id: str,
    filename: str,
    doc_type: str,
    make_doc: Any,
    chunk_pages: int | None = None,
) -> list[Any]:
    """Split a parsed document's pages into per-doc-type-sized chunks.

    All chunks share the same ``document_id`` / ``filename`` /
    ``doc_type`` — they're the same source document, just sliced so the
    extractor can fan out concurrently. ``make_doc`` is the
    ``ExtractorDocument`` constructor passed in to avoid a module-level
    import cycle. ``chunk_pages`` lets a caller (e.g. the benchmark
    harness) force a size; when ``None`` (production path) the size is
    resolved from settings via ``_resolve_chunk_pages(doc_type)``.
    Returns at least one chunk even for an empty document so the
    extractor still runs (and reports 0 fields honestly).
    """
    if chunk_pages is None:
        chunk_pages = _resolve_chunk_pages(doc_type)

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
    for start in range(0, len(pages), chunk_pages):
        batch = pages[start : start + chunk_pages]
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


# ─────────────────────── Structural pre-filter (cost-opt pass S) ─────────
#
# The extractor is charged per-chunk. On a 26-sheet STR Trend xlsx, 20+
# sheets are Cover / Help / Glossary / Notes / SetUp / Instructions —
# none of them carry a P&L line, an STR index, or a dollar figure.
# The extractor confidently returns empty_envelope on each at ~$0.10-
# $0.30 apiece. This filter drops those chunks BEFORE the Sonnet call.
#
# Design constraints:
#   1. Never drop the last chunk. Zero-chunk extractions crash downstream
#      (the merger assumes ≥1 doc). Fall back to keeping the highest-
#      scoring chunk when every chunk fails the signal gate.
#   2. Prose-heavy docs (OM / MARKET_STUDY / SURVEYS) OPT OUT entirely.
#      A 45-page OM has legitimately low-signal pages (broker headshot,
#      table of contents, disclosure boilerplate) that STILL matter for
#      context — e.g. sponsor / brand / location surface only on those
#      pages. Filtering there = data loss.
#   3. Doc types where the extractor's schema is narrow (T12, PNL*,
#      STR*, CBRE_HORIZONS, PNL_BENCHMARK, PORTFOLIO_PNL) use the strict
#      gate — no dollar/P&L/STR signal = drop.
#   4. Light gate (PROPERTY_INFO / ROOM_MIX): keep any chunk with tabular
#      signals (looks like a grid) even if no dollar values — those doc
#      types carry things like room counts / floor plates that aren't
#      currency-shaped.


# Doc types where the filter is disabled entirely. Prose-heavy — every
# chunk potentially matters (broker narrative, market context, etc.).
_PREFILTER_SKIP_DOC_TYPES: frozenset[str] = frozenset({
    "OM",
    "MARKET_STUDY",
    "SURVEYS",
    "LEASES",
    "CONTRACT",
    "INSURANCE",
    "PROPERTY_TAX",
    "CAPEX",
})

# Doc types where the strict gate applies — tabular, narrow schema, low-
# signal sheets are safe to drop.
_PREFILTER_STRICT_DOC_TYPES: frozenset[str] = frozenset({
    "T12",
    "PNL",
    "PNL_MONTHLY",
    "PNL_YTD",
    "PNL_BENCHMARK",
    "PORTFOLIO_PNL",
    "STR",
    "STR_TREND",
    "STR_SEGMENTATION",
    "CBRE_HORIZONS",
    "RENT_ROLL",
})

# Doc types where a lighter filter applies — keep any chunk with tabular
# (grid-shaped) content even if no dollar / P&L vocab appears.
_PREFILTER_LIGHT_DOC_TYPES: frozenset[str] = frozenset({
    "PROPERTY_INFO",
    "ROOM_MIX",
})

# Any chunk whose content is shorter than this after stripping the
# ``[Page N]`` / whitespace / tab-delimiter noise is a candidate for
# dropping (subject to signal gates). Anything above this floor is
# probably at least a partial data table and worth keeping.
_MIN_MEANINGFUL_CHARS = 200

# Currency / dollar detector — catches "$1,234,567", "1,234,567.89 USD",
# "$4.2M", etc. Used by both the strict and light gates.
_CURRENCY_RE = __import__("re").compile(
    r"(\$\s*[\d,]+(?:\.\d+)?(?:\s*[KMB])?"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"
    r"|\b\d+(?:\.\d+)?\s*(?:USD|usd|dollars?)\b)",
)

# Boilerplate-y content that shows up on Cover / Help / Notes / SetUp
# tabs and adds char-count without adding signal. We strip it out
# before comparing against ``_MIN_MEANINGFUL_CHARS`` so a "Help" tab
# with 400 chars of instruction text still trips the drop threshold.
_BOILERPLATE_RE = __import__("re").compile(
    r"(?im)^\s*(?:"
    r"cover(?:\s*sheet)?|"
    r"table\s+of\s+contents|"
    r"instructions?|"
    r"how\s+to\s+use|"
    r"read\s+me|"
    r"notes?|"
    r"disclaimer|"
    r"about\s+this\s+(?:report|workbook|template)|"
    r"help|"
    r"glossary|"
    r"legend|"
    r"definitions?|"
    r"assumptions?|"
    r"set[\s_-]?up|"
    r"index"
    r")\b.*$",
)


def _extract_sheet_names(pages: list[dict[str, Any]]) -> list[str]:
    """Pull ``metadata.sheet_name`` out of each parsed page.

    Returns an empty string in position ``i`` when the source is a PDF /
    docx / other non-workbook format that doesn't carry sheet names.
    """
    out: list[str] = []
    for p in pages:
        meta = p.get("metadata") if isinstance(p, dict) else None
        name = ""
        if isinstance(meta, dict):
            sn = meta.get("sheet_name")
            if isinstance(sn, str):
                name = sn
        out.append(name)
    return out


def _chunk_signal_score(
    *,
    content: str,
    doc_type: str,
) -> tuple[int, str]:
    """Rate a chunk's signal density on a small integer scale.

    Returns ``(score, reason)`` where ``score`` is 0..N (higher =
    stronger signal) and ``reason`` is a compact human-readable
    breakdown for the drop-log. The scoring rubric is intentionally
    simple / conservative:

    * +3 for any P&L text-marker hit
    * +3 for any STR text-marker hit
    * +2 for a currency / dollar figure
    * +2 for tabular-shape signal (≥ 3 tab-delimited rows) — this is the
      "meaningful signal" gate for the light doc types (ROOM_MIX,
      PROPERTY_INFO), which don't carry dollars but do carry grids.
    * +1 for meaningful (non-boilerplate) content ≥ 200 chars — weakest
      signal; alone this does NOT rescue a chunk under the light gate,
      because a Help tab's instructional prose passes the char floor
      while carrying no data.

    A chunk with score 0 in strict mode is a drop candidate. The light
    gate (used for ROOM_MIX / PROPERTY_INFO) requires score >= 2, so
    tabular structure or currency alone survives but instructional
    boilerplate does not.
    """
    # Lazy import to avoid a module-load-order cycle.
    from ..services.structural_recognizer import (
        _PNL_TEXT_MARKERS,
        _STR_TEXT_MARKERS,
    )

    if not isinstance(content, str):
        return 0, "non-string content"

    score = 0
    hits: list[str] = []

    # P&L vocabulary — rooms revenue, F&B, GOP, NOI, EBITDA, etc.
    pnl_hits = sum(1 for rx in _PNL_TEXT_MARKERS if rx.search(content))
    if pnl_hits:
        score += 3
        hits.append(f"pnl={pnl_hits}")

    # STR / CoStar vocabulary — MPI, ARI, RGI, comp set, penetration index.
    str_hits = sum(1 for rx in _STR_TEXT_MARKERS if rx.search(content))
    if str_hits:
        score += 3
        hits.append(f"str={str_hits}")

    # Currency / dollar figures.
    currency_hits = len(_CURRENCY_RE.findall(content))
    if currency_hits >= 1:
        score += 2
        hits.append(f"$={currency_hits}")

    # Tabular-shape signal — parser emits sheet rows as tab-separated,
    # PDF tables also normalize to whitespace-delimited grids. Count
    # lines that look grid-shaped (≥ 3 whitespace-separated columns).
    tabular_lines = 0
    for line in content.splitlines():
        if "\t" in line:
            if line.count("\t") >= 2:
                tabular_lines += 1
        elif len(line.split()) >= 4 and any(c.isdigit() for c in line):
            tabular_lines += 1
        if tabular_lines >= 3:
            break
    if tabular_lines >= 3:
        score += 2
        hits.append(f"grid={tabular_lines}+")

    # Non-boilerplate character count. Strip common instructional
    # patterns first so a "Help" tab with 400 chars of instructions
    # doesn't accidentally score as "meaningful".
    stripped = _BOILERPLATE_RE.sub("", content)
    meaningful_chars = len(stripped.strip())
    if meaningful_chars >= _MIN_MEANINGFUL_CHARS:
        score += 1
        hits.append(f"chars={meaningful_chars}")

    reason = ",".join(hits) if hits else "none"
    return score, reason


def _filter_chunks_by_signal(
    *,
    chunks: list[Any],
    pages: list[dict[str, Any]],
    doc_type: str,
    doc_id: str,
    filename: str,
) -> list[Any]:
    """Drop low-signal chunks before we spend Sonnet tokens on them.

    Doc-type aware:

    * Prose-heavy types (OM / MARKET_STUDY / …): filter DISABLED — every
      chunk is kept regardless of signal density. Prose docs have low-
      signal pages (broker narrative, sponsor bio, TOC) that still
      carry entities the Extractor needs (property name, keys, sponsor).
    * Tabular types (T12 / PNL* / STR* / CBRE_HORIZONS / …): strict
      gate — a chunk with zero P&L / STR / currency hits AND fewer than
      ``_MIN_MEANINGFUL_CHARS`` of non-boilerplate content is dropped.
    * Light types (PROPERTY_INFO / ROOM_MIX): a chunk needs either
      tabular shape OR meaningful non-boilerplate content to survive.

    Safety: at least one chunk is always returned. If EVERY chunk fails
    the gate (pathological case — full workbook is boilerplate), the
    highest-scoring chunk is kept anyway so the extractor still runs
    and honestly reports 0 fields.

    Every drop is logged with the chunk's sheet name(s) + page range +
    reason so an operator can audit the filter after the fact.
    """
    settings = get_settings()
    if not settings.STRUCTURAL_PREFILTER_ENABLED:
        return chunks
    if not chunks:
        return chunks
    if doc_type in _PREFILTER_SKIP_DOC_TYPES:
        # Prose-heavy — filter disabled by policy. Log once so the
        # operator can see we intentionally left chunks in place.
        logger.info(
            "structural pre-filter: doc=%s doc_type=%s — SKIPPED (prose-heavy policy) "
            "chunks_kept=%d",
            doc_id, doc_type, len(chunks),
        )
        return chunks

    # Map page_num → sheet_name (empty for PDFs) so we can name drops
    # with what an operator will recognize.
    sheet_by_page: dict[int, str] = {}
    for i, p in enumerate(pages):
        if not isinstance(p, dict):
            continue
        pn = int(p.get("page_num", i + 1))
        meta = p.get("metadata")
        if isinstance(meta, dict):
            sn = meta.get("sheet_name")
            if isinstance(sn, str) and sn:
                sheet_by_page[pn] = sn

    kept: list[Any] = []
    dropped_names: list[str] = []
    strict = doc_type in _PREFILTER_STRICT_DOC_TYPES
    light = doc_type in _PREFILTER_LIGHT_DOC_TYPES
    # Default = strict when caller passed an unfamiliar doc_type. Better
    # to over-drop and let the fallback safety keep the best chunk.
    if not strict and not light:
        strict = True

    scores: list[tuple[int, Any, str, str]] = []  # score, chunk, label, reason
    for chunk in chunks:
        content = getattr(chunk, "content", "") or ""
        source_pages = list(getattr(chunk, "source_pages", []) or [])
        # Label combines sheet names (unique) + page range for the log.
        sheets = [sheet_by_page.get(pn, "") for pn in source_pages]
        uniq_sheets = [s for s in dict.fromkeys(sheets) if s]
        page_range = (
            f"pages={source_pages[0]}-{source_pages[-1]}"
            if source_pages else "pages=?"
        )
        sheet_label = (
            f"sheets={'/'.join(uniq_sheets)}"
            if uniq_sheets else page_range
        )

        score, reason = _chunk_signal_score(content=content, doc_type=doc_type)

        # Gate:
        #  strict — keep iff score >= 2 (currency alone, or grid, or any
        #  P&L/STR marker); light — keep iff score >= 2 as well, but the
        #  scoring rubric gives grid +2 (light-type docs like ROOM_MIX
        #  earn survival from tabular structure alone). Meaningful char
        #  count is only worth +1, so a Help tab that only has prose
        #  fails both gates. A score of 0-1 drops.
        threshold = 2
        if score >= threshold:
            kept.append(chunk)
        else:
            dropped_names.append(f"{sheet_label} [score={score} {reason}]")

        scores.append((score, chunk, sheet_label, reason))

    # Safety: never send zero chunks. Keep the top-scoring chunk when
    # every one failed the gate — the extractor will report 0 fields
    # honestly, which is better than crashing the pipeline.
    if not kept:
        scores.sort(key=lambda t: t[0], reverse=True)
        top = scores[0]
        logger.warning(
            "structural pre-filter: doc=%s doc_type=%s — ALL %d chunks failed "
            "signal gate; keeping top-scoring chunk (score=%d %s %s) as safety fallback",
            doc_id, doc_type, len(chunks), top[0], top[2], top[3],
        )
        kept = [top[1]]
        # Recompute dropped_names to exclude the safety-kept chunk.
        dropped_names = [
            f"{lbl} [score={sc} {rsn}]"
            for sc, ch, lbl, rsn in scores if ch is not top[1]
        ]

    if dropped_names:
        # Log names truncated at 8 to keep the line readable when a
        # 26-sheet workbook drops 22 of them.
        preview = "; ".join(dropped_names[:8])
        suffix = f" (+{len(dropped_names) - 8} more)" if len(dropped_names) > 8 else ""
        logger.info(
            "structural pre-filter: doc=%s doc_type=%s chunks_before=%d "
            "chunks_after=%d chunks_dropped=%d dropped=[%s%s]",
            doc_id, doc_type, len(chunks), len(kept), len(dropped_names),
            preview, suffix,
        )
    else:
        logger.info(
            "structural pre-filter: doc=%s doc_type=%s — no chunks dropped "
            "(chunks_kept=%d)",
            doc_id, doc_type, len(kept),
        )

    return kept


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

    Sam QA Bug J (2026-06-30): per-chunk errors from the Extractor are
    now surfaced on ``confidence["chunk_errors"]`` so the 0-fields
    branch can distinguish the new ``structural_contradiction`` failure
    (typed prefix in the chunk error message) from the generic
    ``empty_envelope`` shape. Without this surface the contradiction
    would be reported as a generic empty envelope and the analyst
    would have no signal that the doc just needs re-classification.
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

    # Sam QA Bug J round 2 (2026-06-30): the post-extraction structural
    # override at ~line 4192 runs `classify_structure(fields)` on the
    # extracted field set. For STR Trend xlsx files misclassified as
    # T12, the T12 extractor returns ZERO fields (no P&L to find),
    # which leaves `classify_structure` with nothing to identify
    # is_str from — so the override never fires. Meanwhile the
    # in-extractor contradiction guard runs per-chunk on ~5 pages of
    # content; STR markers (MPI/ARI/RGI/comp_set) typically appear on
    # later worksheets / pages, not the first 5.
    #
    # Fix: pre-compute structural text signals on the FULL parsed
    # text (every page, all 253K+ chars for a multi-sheet xlsx)
    # BEFORE the Router runs. If the full document is unambiguously
    # STR-shaped (4+ markers, outweighs P&L vocab 2x — same gates as
    # the per-chunk guard), bypass the Router entirely and pin
    # doc_type to STR_TREND. The Router's prospective call gets logged
    # for telemetry but the LLM call is skipped, saving the round-trip
    # and the 6-minute retry burn an STR-as-T12 misextraction costs.
    from ..services.structural_recognizer import detect_text_signals as _detect_text_signals

    full_text_for_recognizer = "\n\n".join(
        (p.get("text", "") or "") for p in pages
    )
    pre_router_text_signals = _detect_text_signals(full_text_for_recognizer)
    pre_router_str_override: str | None = None
    if pre_router_text_signals.looks_str:
        pre_router_str_override = "STR_TREND"
        logger.info(
            "pre-router structural override: doc=%s str_markers=%d pnl_markers=%d "
            "matched=%s — pinning doc_type=STR_TREND, skipping Router LLM call",
            doc_id,
            pre_router_text_signals.str_marker_hits,
            pre_router_text_signals.pnl_marker_hits,
            ", ".join(pre_router_text_signals.str_markers_matched[:5]),
        )

    if pre_router_str_override is not None:
        doc_type = pre_router_str_override
        route = "extract-pre-router-structural-override"
        router_out = None
        # UX: also persist the override to the documents row NOW so the
        # data-room shows the correct doc_type during the ~minutes-long
        # extraction window. Without this the column would keep
        # displaying whatever filename-hint label was written at upload
        # time (typically the misclassified one) until extraction
        # completes and the success-state UPDATE rewrites doc_type.
        # Sam QA 2026-06-30: he saw the STR row "stuck as T12 EXTRACTING"
        # and concluded J failed — actually J had pinned STR_TREND in
        # memory but the column wouldn't reflect that until extraction
        # finished ~3 min later (verified via "extraction complete:
        # doc=X fields=730 doc_type=STR_TREND" log).
        try:
            from sqlalchemy import text as _sa_text
            from ..database import get_session_factory

            _Session = get_session_factory()
            async with _Session() as _early_sess:
                await _early_sess.execute(
                    _sa_text(
                        "UPDATE documents "
                        "   SET doc_type = :dt, "
                        "       ai_proposed_doc_type = COALESCE(ai_proposed_doc_type, doc_type) "
                        " WHERE id = :id AND tenant_id = :tenant"
                    ),
                    {
                        "dt": pre_router_str_override,
                        "id": doc_id,
                        "tenant": tenant_id,
                    },
                )
                await _early_sess.commit()
        except Exception as exc:
            logger.warning(
                "pre-router override: failed to persist early doc_type "
                "update for doc=%s (%s); column will still update at "
                "success-state UPDATE",
                doc_id,
                exc,
            )
    else:
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
        except Exception as exc:
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

    # Chunked extraction (Sam QA 2026-05-14, tuned pass U 2026-07):
    # split the document into per-doc-type-sized batches and build one
    # ExtractorDocument per chunk. The extractor agent fans these out
    # in parallel (capped concurrency), so a 45-page OM that used to be
    # a single 3-minute Sonnet call becomes ~6-9 concurrent ~30s calls.
    # Smaller per-call context also lifts per-field confidence — the
    # model isn't juggling 45 pages at once. Small docs (≤ chunk size)
    # produce a single chunk and behave exactly as before. Sizes come
    # from ``EXTRACTOR_CHUNK_PAGES_BY_DOCTYPE`` (see config.py) so
    # operators can tune without redeploying.
    extractor_docs = _build_extractor_chunks(
        pages=pages,
        doc_id=doc_id,
        filename=filename,
        doc_type=doc_type,
        make_doc=ExtractorDocument,
    )
    # Cost-opt pass S: drop chunks that carry no financial / STR / dollar
    # signal before we spend Sonnet tokens on them. Doc-type-aware:
    # aggressive on tabular reports (T12 / PNL* / STR* / CBRE_HORIZONS),
    # light on PROPERTY_INFO / ROOM_MIX, disabled on prose (OM /
    # MARKET_STUDY / SURVEYS). Safety-clamped: never returns 0 chunks.
    extractor_docs = _filter_chunks_by_signal(
        chunks=extractor_docs,
        pages=pages,
        doc_type=doc_type,
        doc_id=doc_id,
        filename=filename,
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
    # Bug J: collect per-chunk errors so the 0-fields branch downstream
    # can identify ``structural_contradiction`` failures (the fail-fast
    # guard's typed error string) and surface the right error_kind on
    # the documents row.
    chunk_errors: list[str] = []
    for doc in extractor_out.extracted_documents or []:
        as_dict = doc.model_dump() if hasattr(doc, "model_dump") else dict(doc)
        if not as_dict.get("success", True):
            err = as_dict.get("error")
            if err:
                chunk_errors.append(str(err))
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
        # Bug J: surfaced so the 0-fields branch can pattern-match the
        # typed ``structural_contradiction:`` prefix and pick a
        # specific error_kind. Empty list when every chunk succeeded.
        "chunk_errors": chunk_errors,
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
    except Exception:
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
    except Exception:
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
    except Exception:
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
        except Exception as exc:
            logger.warning(
                "verification: confidence-promote failed for doc=%s: %s",
                doc_id,
                exc,
            )
    except Exception as exc:
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
        except Exception:
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
    except Exception as exc:
        # Sam QA 2026-06-29 — replace silent WARN with loud surfacing:
        # 1) Full traceback in logs (not just one-line WARN)
        # 2) Persist error_kind + error_message on the doc row so the
        #    UI can show "USALI scoring failed — retry available" and
        #    operators can find the doc via `WHERE error_kind LIKE 'usali_%'`
        # 3) report_alert at severity=error so Sentry/Slack catches it
        # The OVERALL extraction stays EXTRACTED (this is a post-extraction
        # scoring pass, not the extraction itself) — we just annotate
        # the doc with the additional failure signal.
        logger.exception(
            "usali_score: scoring crashed for doc=%s deal=%s",
            doc_id,
            deal_id,
        )
        try:
            await session.execute(
                text(
                    "UPDATE documents SET "
                    "error_kind = COALESCE(error_kind, 'usali_scorer_crash'), "
                    "error_message = COALESCE(error_message, :msg) "
                    "WHERE id = :id"
                ),
                {
                    "msg": f"USALI scoring failed ({type(exc).__name__}): "
                           f"{str(exc)[:300]}. "
                           "Retry via POST /deals/<id>/documents/<doc_id>/rescore-usali",
                    "id": doc_id,
                },
            )
            await session.commit()
        except Exception:
            logger.exception("usali_score: also failed to persist error annotation")
        try:
            from ..alerting import report_alert

            report_alert(
                severity="error",
                title=f"USALI scoring crashed: {type(exc).__name__}",
                deal_id=str(deal_id),
                stage="usali_scoring",
                exc=exc,
                extra={"doc_id": str(doc_id), "doc_type": doc_type},
            )
        except Exception:
            pass


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

        run_narrative = os.environ.get("EVALS_MOCK", "").lower() not in ("1", "true", "yes")
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
    except Exception as exc:
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
            UndistributedExpenses,
            USALIFinancials,
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
        except Exception as exc:
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
