"""Documents — uploaded artifacts and the structured fields extracted from them."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocType(str, Enum):
    OM = "OM"
    T12 = "T12"
    STR = "STR"
    # External market reports (May 7 scope alignment).
    # ``STR_TREND`` is a STR competitive-set / TREND report (subject +
    # 5-7 competitors with occupancy/ADR/RevPAR plus penetration
    # indices). Distinct from the simpler ``STR`` benchmark to keep
    # routing rules clean — the Market tab and forward-projection
    # engine read the trend variant.
    STR_TREND = "STR_TREND"
    # STR Segmentation report (Wave 2 P2.1) — per-segment demand share
    # (transient vs. group; sometimes channel-split into Direct / OTA /
    # Brand.com / Voice). Feeds the institutional revenue model so
    # rooms_revenue can be projected as Σ five segments × channel-cost
    # NET contribution margin. Distinct from STR / STR_TREND, which only
    # carry property-level Occ/ADR/RevPAR.
    STR_SEGMENTATION = "STR_SEGMENTATION"
    # CBRE Horizons 5-year ADR/RevPAR forecast by submarket and
    # chain scale; feeds the forward-projection engine.
    CBRE_HORIZONS = "CBRE_HORIZONS"
    # HotStats-equivalent line-item P&L benchmark (POR/PAR ratios for
    # departmental margins, expense ratios, GOP margin).
    PNL_BENCHMARK = "PNL_BENCHMARK"
    # Wave 2 P2.7 — analyst's in-house portfolio P&L benchmark
    # (Sam's June 2026 ask: "Wants op-ratios extracted from CBRE/in-house
    # portfolio P&Ls (not HOST defaults)."). Aggregated peer-set ratios
    # rolled up across the analyst firm's existing hotel investments —
    # the same chain scales / markets as the subject. Sits ABOVE the
    # generic ``PNL_BENCHMARK`` (HostStats default) and ``CBRE_HORIZONS``
    # in the op-ratio precedence chain — a firm's own portfolio is the
    # most credible peer set when it covers the subject chain scale. See
    # ``apps/worker/app/services/op_ratio_precedence.py``.
    PORTFOLIO_PNL = "PORTFOLIO_PNL"
    PNL = "PNL"
    # Finer P&L distinctions assigned by the post-extraction
    # reclassifier based on `p_and_l_usali.period_type`. The Router
    # still emits the broad PNL / T12 tokens up front (it only sees
    # filename + first ~2k chars). Once the Extractor runs and we
    # know whether the doc is a single month, a year-to-date roll,
    # or a true T-12, we narrow the doc_type in place. Rani's QA
    # flagged a May-2024 monthly P&L being treated as a T-12.
    PNL_MONTHLY = "PNL_MONTHLY"
    PNL_YTD = "PNL_YTD"
    RENT_ROLL = "RENT_ROLL"
    # Room mix / unit mix lookup table — typically a 1-2 tab .xlsx that
    # lists room count by category (king, double, suite) and floor.
    # Surfaces in OM annexes and brand-system handoffs. Distinct from a
    # P&L; classifying these as T12 ran the Extractor with the wrong
    # USALI schema and returned 0 fields (Sam QA 2026-05-30).
    ROOM_MIX = "ROOM_MIX"
    MARKET_STUDY = "MARKET_STUDY"
    CONTRACT = "CONTRACT"
    # Wave 1 — 11-category guided onboarding (June 2026). Each of the
    # additions below maps 1:1 to a wizard sub-stage and is "Recommended
    # for IC" rather than hard-required. The Router agent's
    # classification prompt was extended at the same time so it can
    # actually emit these tokens. See ``apps/worker/app/agents/router.py``.
    INSURANCE = "INSURANCE"
    PROPERTY_TAX = "PROPERTY_TAX"
    CAPEX = "CAPEX"
    PROPERTY_INFO = "PROPERTY_INFO"
    LEASES = "LEASES"
    SURVEYS = "SURVEYS"
    # NOTE: ``UNKNOWN`` is intentionally NOT in this enum — it's a
    # sentinel the Router agent emits when the LLM can't classify
    # confidently. Persisted doc_type values are always one of the
    # above; UNKNOWN gets resolved to the filename hint downstream.


class DocumentStatus(str, Enum):
    PENDING = "Pending"
    PROCESSING = "Processing"
    EXTRACTED = "Extracted"
    FAILED = "Failed"


class ExtractionField(BaseModel):
    """One field extracted from a source document with its grounding."""

    model_config = ConfigDict(extra="forbid")

    field_name: Annotated[str, Field(min_length=1, max_length=200)]
    value: str | float | int | bool | None = None
    unit: str | None = None
    source_page: Annotated[int, Field(ge=1)]
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    raw_text: Annotated[str, Field(max_length=4000)] | None = None


class Document(BaseModel):
    """An uploaded artifact attached to a deal."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    filename: Annotated[str, Field(min_length=1, max_length=300)]
    doc_type: DocType
    status: DocumentStatus
    size: Annotated[int, Field(ge=0, description="File size in bytes.")]
    uploaded_at: datetime
    fields_extracted: Annotated[int, Field(ge=0)] = 0
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    populates: list[str] = Field(
        default_factory=list,
        description="Engine identifiers this document feeds (e.g. 'Investment', 'P&L').",
    )
    fields: list[ExtractionField] = Field(default_factory=list)
