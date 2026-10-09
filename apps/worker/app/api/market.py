"""Market overview + comp-set endpoints.

These routes are intentionally thin until a real STR/CoStar feed is
wired in — the full market-research pass is future work. To stay
useful in the meantime, ``GET /market/{deal_id}/overview`` now reads
the deal row and surfaces what we *do* know (city, keys, brand,
service) so the web app's market header can render with real data
instead of nulls.

The transaction-comps endpoint reads ``transaction_comps.<n>.*`` rows
out of the deal's extracted documents (OMs typically include comp
sales tables). Sam called these "critical for anchoring exit cap rate"
in his May 7 call summary — even when the OM only carries 3-5 comps
the exit cap conversation has anchors instead of feel.

The proper STR/CoStar integration will populate ``occupancy_index``,
``adr_index``, ``revpar_index``, and a comp-set list. Until then those
fields stay null and the comps endpoint returns an empty list so the
UI can render an "awaiting market data" empty state.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..services.market_comp_set import (
    CompSetDerivation,
    StrReportOrder,
    TtmBlend,
    build_str_inputs,
    derive_comp_set_from_inputs,
    derive_ttm_blend,
)
from ..services.market_fields import (
    FieldRef,
    FieldRow,
    extraction_lane,
    parse_extraction_records,
)
from ..services.market_study_reader import (
    GrowthReading,
    PipelineFilter,
    PipelineHotel,
    SupplyReading,
    market_terms_from_rows,
    read_demand_growth,
    read_supply_growth,
)
from .deals import _assert_deal_belongs_to_tenant, _coerce_overrides, get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── Market tab blocks (FON-61) ───────────────────────────
#
# E-009 / E-007 / E-008 — the comp-set roster, the TTM comp-set blend's
# definition, and demand / supply growth from MARKET_STUDY extractions. All
# additive and nullable: an older web bundle ignores them, and any read
# failure leaves the block None rather than failing the overview.


class FieldRefOut(BaseModel):
    """One extraction row a Market figure was read from."""

    model_config = ConfigDict(extra="forbid")

    field_name: str
    value: float | int | str | bool | None = None
    doc_name: str | None = None
    doc_id: str | None = None
    page: int | None = None

    @classmethod
    def of(cls, ref: FieldRef) -> FieldRefOut:
        v = ref.value
        if not isinstance(v, (int, float, str, bool)) and v is not None:
            v = str(v)
        return cls(field_name=ref.field_name, value=v, doc_name=ref.doc_name, doc_id=ref.doc_id, page=ref.page)


class CompSetHotelOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    name: str
    name_as_reported: str
    keys: int | None = None
    status: Literal["active", "closed"]
    status_source: Literal["extracted_status_field", "str_closed_label"] | None = None
    # STR's property id — the key the roster is unioned on across reports.
    str_id: str | None = None
    # The document that carried the closed marker.
    status_doc_name: str | None = None
    status_doc_id: str | None = None
    status_page: int | None = None
    # The (newest) document the key count was read from.
    keys_doc_name: str | None = None
    # Every STR roster document that lists this hotel, newest first.
    reports: list[str] = Field(default_factory=list)
    # Other roster names this hotel was merged under (by STR id or the
    # alias rule) — "The Betsy Hotel" for "The Betsy South Beach".
    merged_names: list[str] = Field(default_factory=list)


PeriodEndBasisOut = Literal[
    "extracted_period_end",
    "subject_monthly_series",
    "period_start_plus_months",
    "document_report_as_of",
    "filename_token",
    "report_year",
    "created_at",
]


class StrReportOrderOut(BaseModel):
    """One STR extraction's place in the report-period ordering the Market
    blocks read in — most recent report period first, never upload time.
    Mirrors ``services.market_comp_set.StrReportOrder``."""

    model_config = ConfigDict(extra="forbid")

    extraction_id: str | None = None
    doc_name: str | None = None
    doc_id: str | None = None
    # ISO period end at the precision the source stated: YYYY-MM-DD / YYYY-MM / YYYY.
    period_end: str | None = None
    period_end_basis: PeriodEndBasisOut = "created_at"
    # The field path / document attribute / filename the period end came from.
    period_end_source: str | None = None
    created_at: str | None = None

    @classmethod
    def of(cls, o: StrReportOrder) -> StrReportOrderOut:
        return cls(
            extraction_id=o.extraction_id, doc_name=o.doc_name, doc_id=o.doc_id,
            period_end=o.period_end, period_end_basis=o.period_end_basis,
            period_end_source=o.period_end_source, created_at=o.created_at,
        )


class MarketCompSetBlock(BaseModel):
    """ONE derivation feeds the hotel count and the keys (active hotels)."""

    model_config = ConfigDict(extra="forbid")

    hotels: list[CompSetHotelOut] = Field(default_factory=list)
    active_count: int | None = None
    active_keys: int | None = None
    closed_count: int = 0
    closed_names: list[str] = Field(default_factory=list)
    count_basis: Literal["active_roster", "reported_rollup", "none"] = "none"
    keys_basis: Literal["active_roster", "reported_rollup", "none"] = "none"
    status_available: bool = False
    reported_comp_set_size: int | None = None
    reported_total_keys: int | None = None
    source_doc_name: str | None = None
    source_doc_id: str | None = None
    source_page: int | None = None
    note: str = ""
    # Every STR roster document unioned, most recent report period first.
    documents: list[str] = Field(default_factory=list)
    # Every STR extraction in the order the union read them, with the period
    # end each resolved to and how — the audit trail for "most recent".
    ordering: list[StrReportOrderOut] = Field(default_factory=list)

    @classmethod
    def of(cls, d: CompSetDerivation) -> MarketCompSetBlock:
        return cls(
            hotels=[
                CompSetHotelOut(
                    index=h.index, name=h.name, name_as_reported=h.name_as_reported,
                    keys=h.keys, status=h.status, status_source=h.status_source,
                    str_id=h.str_id, status_doc_name=h.status_doc_name,
                    status_doc_id=h.status_doc_id, status_page=h.status_page,
                    keys_doc_name=h.keys_doc_name, reports=list(h.reports),
                    merged_names=list(h.merged_names),
                )
                for h in d.hotels
            ],
            documents=list(d.documents),
            ordering=[StrReportOrderOut.of(o) for o in d.ordering],
            active_count=d.active_count,
            active_keys=d.active_keys,
            closed_count=d.closed_count,
            closed_names=list(d.closed_names),
            count_basis=d.count_basis,
            keys_basis=d.keys_basis,
            status_available=d.status_available,
            reported_comp_set_size=d.reported_comp_set_size,
            reported_total_keys=d.reported_total_keys,
            source_doc_name=d.source_doc_name,
            source_doc_id=d.source_doc_id,
            source_page=d.source_page,
            note=d.note,
        )


class MarketTtmBlendBlock(BaseModel):
    """The "TTM · comp-set blend" with the rows and period that define it."""

    model_config = ConfigDict(extra="forbid")

    occupancy_pct: float | None = None
    adr_usd: float | None = None
    revpar_usd: float | None = None
    subject_occupancy_pct: float | None = None
    subject_adr_usd: float | None = None
    subject_revpar_usd: float | None = None
    mpi: float | None = None
    ari: float | None = None
    rgi: float | None = None
    period_start: str | None = None
    period_end: str | None = None
    months: int | None = None
    period_basis: Literal["subject_monthly_series", "report_year", "none"] = "none"
    # ``str_trend.report_year`` of the report that supplied the inputs.
    report_year: int | None = None
    inputs: list[FieldRefOut] = Field(default_factory=list)
    # The document that supplied the inputs — one report, never a per-field mix.
    documents: list[str] = Field(default_factory=list)
    method: str = ""
    # Which STR report supplied the subject TTM and the indices, the period
    # end it resolved to (``period_end_used``) and how (``ordering_basis``);
    # ``ordering`` lists every STR extraction in the order the reader used.
    source_doc_name: str | None = None
    source_doc_id: str | None = None
    source_extraction_id: str | None = None
    period_end_used: str | None = None
    ordering_basis: PeriodEndBasisOut | None = None
    ordering: list[StrReportOrderOut] = Field(default_factory=list)

    @classmethod
    def of(cls, b: TtmBlend) -> MarketTtmBlendBlock:
        return cls(
            occupancy_pct=b.occupancy_pct, adr_usd=b.adr_usd, revpar_usd=b.revpar_usd,
            subject_occupancy_pct=b.subject_occupancy_pct, subject_adr_usd=b.subject_adr_usd,
            subject_revpar_usd=b.subject_revpar_usd, mpi=b.mpi, ari=b.ari, rgi=b.rgi,
            period_start=b.period_start, period_end=b.period_end, months=b.months,
            period_basis=b.period_basis, report_year=b.report_year,
            inputs=[FieldRefOut.of(r) for r in b.inputs], documents=list(b.documents),
            method=b.method,
            source_doc_name=b.source_doc_name, source_doc_id=b.source_doc_id,
            source_extraction_id=b.source_extraction_id,
            period_end_used=b.period_end_used, ordering_basis=b.ordering_basis,
            ordering=[StrReportOrderOut.of(o) for o in b.ordering],
        )


class MarketGrowthBlock(BaseModel):
    """Demand growth — a reported figure or two years of the demand series."""

    model_config = ConfigDict(extra="forbid")

    value_pct: float | None = None
    period_label: str | None = None
    basis: Literal["reported", "derived_from_series"] | None = None
    inputs: list[FieldRefOut] = Field(default_factory=list)
    reason: str | None = None
    detail: str | None = None
    # The report's forecast for the same series — shown AS forecast, never
    # in place of an actual.
    forecast_pct: float | None = None
    forecast_label: str | None = None
    forecast_input: FieldRefOut | None = None
    # An absolute demand change the report states (room nights, as printed)
    # that the unit guard kept out of ``value_pct`` — a level, never a rate.
    demand_room_nights_change: FieldRefOut | None = None
    demand_room_nights_change_period: str | None = None

    @classmethod
    def of(cls, g: GrowthReading) -> MarketGrowthBlock:
        return cls(
            value_pct=g.value_pct, period_label=g.period_label, basis=g.basis,
            inputs=[FieldRefOut.of(r) for r in g.inputs], reason=g.reason, detail=g.detail,
            forecast_pct=g.forecast_pct, forecast_label=g.forecast_label,
            forecast_input=FieldRefOut.of(g.forecast_input) if g.forecast_input else None,
            demand_room_nights_change=(
                FieldRefOut.of(g.demand_room_nights_change) if g.demand_room_nights_change else None
            ),
            demand_room_nights_change_period=g.demand_room_nights_change_period,
        )


class PipelineHotelOut(BaseModel):
    """One pipeline project in the deal's market (export row, canonical row,
    or the submarket report's own list)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    keys: int | None = None
    status: str | None = None
    bucket: Literal["under_construction", "final_planning", "planned"] | None = None
    market: str | None = None
    submarket: str | None = None
    expected_open: str | None = None
    doc_name: str | None = None
    doc_id: str | None = None
    page: int | None = None

    @classmethod
    def of(cls, h: PipelineHotel) -> PipelineHotelOut:
        return cls(
            name=h.name, keys=h.keys, status=h.status, bucket=h.bucket, market=h.market,
            submarket=h.submarket, expected_open=h.expected_open, doc_name=h.doc_name,
            doc_id=h.doc_id, page=h.page,
        )


class PipelineFilterOut(BaseModel):
    """How a multi-market pipeline export was narrowed to the deal's market."""

    model_config = ConfigDict(extra="forbid")

    terms: list[str] = Field(default_factory=list)
    matched: int = 0
    total: int = 0
    doc_name: str | None = None
    note: str | None = None
    # Export rows with neither a market nor a submarket — counted neither
    # as matched nor as unmatched.
    market_unknown_rows: int = 0

    @classmethod
    def of(cls, f: PipelineFilter) -> PipelineFilterOut:
        return cls(
            terms=list(f.terms), matched=f.matched, total=f.total, doc_name=f.doc_name, note=f.note,
            market_unknown_rows=f.market_unknown_rows,
        )


class MarketSupplyGrowthBlock(BaseModel):
    """Pipeline rooms over existing inventory (under construction / final planning)."""

    model_config = ConfigDict(extra="forbid")

    existing_rooms: int | None = None
    existing_period_label: str | None = None
    under_construction_rooms: int | None = None
    final_planning_rooms: int | None = None
    planned_rooms: int | None = None
    under_construction_pct: float | None = None
    final_planning_pct: float | None = None
    reported_supply_change_pct: float | None = None
    reported_supply_change_period: str | None = None
    inputs: list[FieldRefOut] = Field(default_factory=list)
    reason: str | None = None
    detail: str | None = None
    # "reported" = the report's own "% of inventory" row; "computed" = rooms ÷
    # existing inventory. None when the share is unavailable.
    under_construction_pct_basis: Literal["reported", "computed"] | None = None
    final_planning_pct_basis: Literal["reported", "computed"] | None = None
    forecast_supply_change_pct: float | None = None
    forecast_supply_change_period: str | None = None
    pipeline_hotels: list[PipelineHotelOut] = Field(default_factory=list)
    pipeline_filter: PipelineFilterOut | None = None
    # An absolute supply change the report states (rooms, as printed) that
    # the unit guard kept out of ``reported_supply_change_pct``.
    supply_rooms_change: FieldRefOut | None = None
    supply_rooms_change_period: str | None = None

    @classmethod
    def of(cls, s: SupplyReading) -> MarketSupplyGrowthBlock:
        return cls(
            existing_rooms=s.existing_rooms, existing_period_label=s.existing_period_label,
            under_construction_rooms=s.under_construction_rooms,
            final_planning_rooms=s.final_planning_rooms, planned_rooms=s.planned_rooms,
            under_construction_pct=s.under_construction_pct, final_planning_pct=s.final_planning_pct,
            reported_supply_change_pct=s.reported_supply_change_pct,
            reported_supply_change_period=s.reported_supply_change_period,
            inputs=[FieldRefOut.of(r) for r in s.inputs], reason=s.reason, detail=s.detail,
            under_construction_pct_basis=s.under_construction_pct_basis,
            final_planning_pct_basis=s.final_planning_pct_basis,
            forecast_supply_change_pct=s.forecast_supply_change_pct,
            forecast_supply_change_period=s.forecast_supply_change_period,
            pipeline_hotels=[PipelineHotelOut.of(h) for h in (s.pipeline_hotels or [])],
            pipeline_filter=PipelineFilterOut.of(s.pipeline_filter) if s.pipeline_filter else None,
            supply_rooms_change=FieldRefOut.of(s.supply_rooms_change) if s.supply_rooms_change else None,
            supply_rooms_change_period=s.supply_rooms_change_period,
        )


_EXTRACTION_ROWS_SQL = """
    SELECT er.id AS extraction_id,
           er.fields,
           er.agent_version,
           er.document_id,
           er.created_at,
           d.filename,
           d.doc_type,
           d.ai_proposed_doc_type,
           d.report_as_of,
           d.report_as_of_precision
      FROM extraction_results er
      JOIN documents d ON d.id = er.document_id
     WHERE er.deal_id = :deal
       AND er.tenant_id = :tenant
       AND d.tenant_id = :tenant
       AND UPPER(COALESCE(d.doc_type, '')) IN ({types})
     ORDER BY er.created_at DESC, er.id DESC
"""

# Every document tag an STR report or a market study can arrive under. The
# lane each extraction actually ran in (``extraction_lane``) decides which
# reader gets it — the tag alone does not.
_MARKET_DOC_TYPES = ("STR", "STR_TREND", "MARKET_STUDY")


async def _extraction_records(
    session: AsyncSession, *, deal_id: UUID, tenant_id: UUID, doc_types: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Extraction rows (newest first) for the given doc types, tenant-scoped.

    ``created_at`` and the document's ``report_as_of`` ride along so the STR
    reader can order reports by REPORT PERIOD (``market_comp_set``) — the
    ``created_at DESC`` order here is only its final tiebreak.
    """
    types_sql = ", ".join(f"'{t}'" for t in doc_types)  # fixed literals, never user input
    rows = await session.execute(
        text(_EXTRACTION_ROWS_SQL.format(types=types_sql)),
        {"deal": str(deal_id), "tenant": str(tenant_id)},
    )
    return [dict(r._mapping) for r in rows.fetchall()]


def _partition_by_lane(records: list[dict[str, Any]]) -> dict[str, list[FieldRow]]:
    """Split extraction records into the STR and MARKET_STUDY readers' rows.

    FON-61 live fact: the tester's CoStar reports are tagged ``STR_TREND``
    on the document row while their extraction ran in the MARKET_STUDY
    lane (``agent_version`` ``dt:MARKET_STUDY``, ``market_study.*`` /
    ``pnl_benchmark.market.*`` fields). Selecting by the tag answered
    ``no_document`` for a deal that had both reports — and would have let
    a market report's "subject" occupancy pollute the STR comp-set blend.
    """
    lanes: dict[str, list[FieldRow]] = {"STR": [], "MARKET_STUDY": []}
    for rec in records:
        rows = parse_extraction_records([rec])
        lane = _lane_of(rec, rows)
        if lane == "MARKET_STUDY":
            # Tag the rows with the lane's doc type so downstream filters
            # (``market_study_rows``) see the lane, not the analyst's tag.
            lanes[lane].extend(replace(r, doc_type="MARKET_STUDY") for r in rows)
        elif lane == "STR":
            lanes[lane].extend(rows)
    return lanes


def _lane_of(rec: dict[str, Any], rows: list[FieldRow]) -> str | None:
    return extraction_lane(
        doc_type=rec.get("doc_type"),
        agent_version=rec.get("agent_version"),
        ai_proposed_doc_type=rec.get("ai_proposed_doc_type"),
        field_names=(r.field_name for r in rows),
    )


async def _market_blocks(
    session: AsyncSession, *, deal_id: UUID, tenant_id: UUID, city: str | None = None
) -> dict[str, Any]:
    """The four Market-tab blocks; each is None on any failure (never a 500).

    Extractions are routed to the STR reader (comp set, TTM blend) or the
    MARKET_STUDY reader (demand / supply growth) by the lane they were
    extracted in. ``city`` (the deal row) plus the submarket the market
    studies name for themselves are the terms a multi-market pipeline export
    is filtered by before anything is summed — never the national list.
    """
    out: dict[str, Any] = {
        "comp_set": None, "ttm_blend": None, "demand_growth": None, "supply_growth": None,
    }
    try:
        records = await _extraction_records(
            session, deal_id=deal_id, tenant_id=tenant_id, doc_types=_MARKET_DOC_TYPES
        )
    except Exception:  # overview must never fail on this read
        logger.exception("market_overview: extraction read failed")
        return out
    lanes = _partition_by_lane(records)
    try:
        str_rows = lanes["STR"]
        if str_rows:
            inputs = build_str_inputs(str_rows)
            out["comp_set"] = MarketCompSetBlock.of(derive_comp_set_from_inputs(inputs))
            blend = derive_ttm_blend(inputs)
            out["ttm_blend"] = MarketTtmBlendBlock.of(blend) if blend else None
    except Exception:
        logger.exception("market_overview: comp-set / TTM blend read failed")
    try:
        ms_rows = lanes["MARKET_STUDY"]
        # ``no_document`` only when NO extraction on the deal is in the lane
        # (an extraction with zero parsable fields still counts as present).
        has_docs = any(
            _lane_of(rec, parse_extraction_records([rec])) == "MARKET_STUDY" for rec in records
        )
        out["demand_growth"] = MarketGrowthBlock.of(
            read_demand_growth(ms_rows, has_documents=has_docs)
        )
        market_terms = [t for t in [city, *market_terms_from_rows(ms_rows)] if isinstance(t, str) and t.strip()]
        out["supply_growth"] = MarketSupplyGrowthBlock.of(
            read_supply_growth(ms_rows, has_documents=has_docs, market_terms=market_terms)
        )
    except Exception:
        logger.exception("market_overview: MARKET_STUDY growth read failed")
    return out


class PropertyNameOriginal(BaseModel):
    """The document-extracted property name + where it came from (FON-59)."""

    model_config = ConfigDict(extra="forbid")

    value: str
    doc_name: str | None = None
    page: int | None = None


class MarketOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    market: str | None = None
    keys: int | None = None
    brand: str | None = None
    service: str | None = None
    # The subject hotel's actual name, extracted from the documents (OM wins).
    # Distinct from the deal row's `name`, which is the user's project name —
    # this endpoint NEVER falls back to the project name (FON-59: the two are
    # stored independently and displayed distinctly).
    property_name: str | None = None
    # FON-59 — an analyst may override the extracted name via
    # ``field_overrides["property_overview.name"]`` (bare string or the
    # structured ``{value, note}`` record). When set it wins as
    # ``property_name`` and ``property_name_source`` says so; the extracted
    # value is preserved in ``property_name_original`` (+ the OM it came
    # from, when known) so the override can be restored. All null when
    # nothing applies — additive, older clients ignore them.
    property_name_original: PropertyNameOriginal | None = None
    property_name_source: Literal["analyst_override", "document"] | None = None
    # FON-70 — descriptive property metadata extracted from the OM
    # (property_overview.*). None when no document carried the field, so the UI
    # shows a blank rather than a fabricated value. Title / transfer tax are not
    # extracted (title = ownership type isn't in the OM; transfer tax needs a
    # jurisdiction lookup), so they stay off this response.
    year_built: int | None = None
    gba_sf: int | None = None
    labor_type: str | None = None
    # FON-59 R-054 — the OM's own classification of the asset
    # (``property_overview.property_type``, e.g. "Boutique Lifestyle
    # Full-Service"). Read-only from the extraction; None when no document
    # carried it so the Overview's Property Type row shows a dash rather than
    # the deal row's ``service`` column dressed up as a document value. There
    # is deliberately NO ``floors`` field: the extraction catalog has no
    # floors / stories concept, so the row stays a reasoned dash on the web.
    property_type: str | None = None
    # Trailing-12 average subject occupancy (0-1 fraction) and ADR (USD) — the
    # SAME trailing-12 the STR forward-forecast uses as its baseline (read via
    # ``str_forecast.trailing_12_occ_adr`` off the deal's STR_TREND
    # extractions). Canonical Overview v3 renders a single combined
    # "Trailing-12 Occupancy / ADR" row sourced from Financials → Historicals.
    # Both None when the deal has no STR history on file, so the UI shows "—".
    trailing_12_occupancy: float | None = None
    trailing_12_adr: float | None = None
    occupancy_index: float | None = None
    adr_index: float | None = None
    revpar_index: float | None = None
    # FON-61 Market tab blocks (E-009 / E-007 / E-008). Additive + nullable.
    # ``comp_set``: the ONE derivation behind "N hotels / N keys" (active
    # hotels of the STR roster; closed ones listed and excluded).
    # ``ttm_blend``: the rows + period that define "TTM · comp-set blend".
    # ``demand_growth`` / ``supply_growth``: read off MARKET_STUDY
    # extractions, with provenance, or a reason code when absent.
    comp_set: MarketCompSetBlock | None = None
    ttm_blend: MarketTtmBlendBlock | None = None
    demand_growth: MarketGrowthBlock | None = None
    supply_growth: MarketSupplyGrowthBlock | None = None


class Comp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    distance_miles: float | None = None
    keys: int | None = None
    chain_scale: str | None = None
    revpar: float | None = None


class CompsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    comps: list[Comp] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


async def _extracted_property_meta(
    session: AsyncSession, *, deal_id: UUID, tenant_id: UUID
) -> dict[str, Any]:
    """Descriptive property metadata extracted from the documents (OM wins).

    Returns a dict with any of ``name`` / ``year_built`` / ``gba_sf`` /
    ``labor_type`` / ``property_type`` that a document carried; absent keys mean nothing extracted
    it (so the UI shows a blank rather than a fabricated value). Name is
    ``property_overview.name`` (or the STR subject name); the rest are
    ``property_overview.*`` from the OM (FON-59 / FON-70). Rows are OM-first, so
    the first hit per field is the OM's value. When a name is found,
    ``name_doc_name`` / ``name_page`` carry the source document's filename and
    1-indexed page (when the extractor recorded one) so the Overview can cite
    "Original: <name> · <doc> p.<n>" next to an analyst override. Title
    (ownership type) and transfer tax are deliberately not surfaced — the OM
    doesn't carry a title type and transfer tax needs a jurisdiction lookup,
    so they stay blank."""
    out: dict[str, Any] = {}
    rows = await session.execute(
        text(
            """
            SELECT er.fields, d.doc_type, d.filename
              FROM extraction_results er
              JOIN documents d ON d.id = er.document_id
             WHERE er.deal_id = :deal
               AND er.tenant_id = :tenant
               AND d.tenant_id = :tenant
             ORDER BY CASE UPPER(COALESCE(d.doc_type, ''))
                        WHEN 'OM' THEN 0
                        WHEN 'STR_TREND' THEN 1
                        WHEN 'STR' THEN 1
                        ELSE 2 END,
                      er.created_at DESC
            """
        ),
        {"deal": str(deal_id), "tenant": str(tenant_id)},
    )
    for r in rows.fetchall():
        raw = r._mapping.get("fields")
        doc_name = r._mapping.get("filename")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if not isinstance(raw, list):
            continue
        for f in raw:
            if not isinstance(f, dict):
                continue
            fn = (f.get("field_name") or "").strip().lower()
            val = f.get("value")
            if fn in ("property_overview.name", "ttm_performance.subject.name"):
                if "name" not in out and isinstance(val, str) and val.strip():
                    out["name"] = val.strip()
                    if isinstance(doc_name, str) and doc_name.strip():
                        out["name_doc_name"] = doc_name.strip()
                    page = _coerce_int(f.get("source_page") or f.get("page_number"))
                    if page is not None and page >= 1:
                        out["name_page"] = page
            elif fn == "property_overview.year_built":
                if "year_built" not in out:
                    yb = _coerce_int(val)
                    if yb is not None and 1700 < yb < 2100:
                        out["year_built"] = yb
            elif fn == "property_overview.gba_sf":
                if "gba_sf" not in out:
                    g = _coerce_int(val)
                    if g is not None and g > 0:
                        out["gba_sf"] = g
            elif fn == "property_overview.labor_type":
                if "labor_type" not in out and isinstance(val, str) and val.strip():
                    out["labor_type"] = val.strip()
            elif fn == "property_overview.property_type":
                if "property_type" not in out and isinstance(val, str) and val.strip():
                    out["property_type"] = val.strip()
    return out


# FON-59 — the field_overrides key the Overview writes for a Property Name
# override. Mirrors the extractor path so the override and the extracted
# value are addressed by the same name.
PROPERTY_NAME_OVERRIDE_KEY = "property_overview.name"


def _property_name_override(overrides: dict[str, Any]) -> str | None:
    """The analyst's Property Name override, if one is set.

    Accepts both shapes the app writes: a bare string (legacy) and the
    structured ``{value, note}`` record every other override uses. A blank /
    non-string value counts as "no override" so a stray empty save can never
    blank out the extracted name.
    """
    raw = overrides.get(PROPERTY_NAME_OVERRIDE_KEY)
    if isinstance(raw, dict):
        raw = raw.get("value")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


@router.get("/{deal_id}/overview", response_model=MarketOverview)
async def market_overview(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> MarketOverview:
    """Pull the deal row and surface its market-relevant fields.

    Real STR-driven RevPAR/ADR/Occupancy indices are still future work
    (TODO(str-integration)); the indices stay null until the feed lands.
    The web app should render the city/keys/brand block from this
    response and treat null indices as "awaiting market data".
    """
    row = (
        await session.execute(
            text(
                """
                SELECT city, keys, brand, service, field_overrides
                  FROM deals
                 WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )
    m = row._mapping
    overrides = _coerce_overrides(m.get("field_overrides"))
    keys: int | None = None
    if m.get("keys") is not None:
        try:
            keys = int(m["keys"])
        except (TypeError, ValueError):
            keys = None
    meta = await _extracted_property_meta(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    # Trailing-12 subject occupancy / ADR from the deal's STR_TREND history —
    # the same trailing-12 the STR forward-forecast baselines off. Best-effort:
    # a deal with no STR history (or any read failure) leaves both None so the
    # Overview row renders "—" rather than the endpoint 500-ing.
    trailing_12_occupancy: float | None = None
    trailing_12_adr: float | None = None
    try:
        from ..engines.str_forecast import trailing_12_occ_adr
        from ..services.str_forecast_loader import load_str_history_for_deal

        history = await load_str_history_for_deal(
            session, deal_id=str(deal_id), tenant_id=str(tenant_id)
        )
        trailing = trailing_12_occ_adr(history)
        if trailing is not None:
            trailing_12_occupancy, trailing_12_adr = trailing
    except Exception:  # noqa: BLE001 — overview must never fail on the STR read
        logger.exception("market_overview: trailing-12 STR read failed")
    blocks = await _market_blocks(
        session, deal_id=deal_id, tenant_id=tenant_id, city=m.get("city")
    )
    # FON-59 — Property Name resolution: analyst override > extracted (OM
    # first) > null. The deal row's ``name`` (the confidential project name)
    # is deliberately NOT a fallback here.
    extracted_name = meta.get("name")
    name_override = _property_name_override(overrides)
    property_name_original = (
        PropertyNameOriginal(
            value=extracted_name,
            doc_name=meta.get("name_doc_name"),
            page=meta.get("name_page"),
        )
        if extracted_name
        else None
    )
    property_name_source: Literal["analyst_override", "document"] | None = (
        "analyst_override" if name_override
        else "document" if extracted_name
        else None
    )
    return MarketOverview(
        deal_id=deal_id,
        market=m.get("city"),
        keys=keys,
        brand=m.get("brand"),
        service=m.get("service"),
        property_name=name_override or extracted_name,
        property_name_original=property_name_original,
        property_name_source=property_name_source,
        year_built=meta.get("year_built"),
        gba_sf=meta.get("gba_sf"),
        labor_type=meta.get("labor_type"),
        property_type=meta.get("property_type"),
        trailing_12_occupancy=trailing_12_occupancy,
        trailing_12_adr=trailing_12_adr,
        comp_set=blocks["comp_set"],
        ttm_blend=blocks["ttm_blend"],
        demand_growth=blocks["demand_growth"],
        supply_growth=blocks["supply_growth"],
    )


@router.get("/{deal_id}/comps", response_model=CompsResponse)
async def market_comps(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> CompsResponse:
    """Comp-set endpoint.

    TODO(str-integration): pull comp set from the STR/CoStar feed
    keyed off the deal's city. Until then we return an empty list +
    a metadata flag so the UI renders an "awaiting market data" panel
    rather than a blank page.

    Tenant-scoped: even though the stub returns no data, a cross-tenant
    ``deal_id`` returns 404 so the surface stays uniform with the
    future STR-integrated implementation.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    return CompsResponse(
        deal_id=deal_id,
        comps=[],
        metadata={"source": "stub", "awaiting_integration": "str-costar"},
    )


# ─────────────────────────── transaction comps ───────────────────────────


class TransactionCompEntry(BaseModel):
    """One comparable hotel sale parsed out of an OM's comp table."""

    model_config = ConfigDict(extra="forbid")

    name: str
    market: str | None = None
    sale_date: str | None = None  # ISO date string when known; free-form otherwise
    keys: int | None = None
    sale_price_usd: float | None = None
    price_per_key_usd: float | None = None
    cap_rate_pct: float | None = None
    buyer_name: str | None = None
    buyer_type: str | None = None
    # FON-72 Market Tab — the design's comps table carries both a BUYER and a
    # SELLER column. Emitted as null (renders "—") when the OM row doesn't
    # disclose a seller rather than omitting the field.
    seller: str | None = None
    source_document_id: str | None = None
    source_page: int | None = None


class TransactionCompsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    comps: list[TransactionCompEntry] = Field(default_factory=list)
    median_price_per_key: float | None = Field(
        default=None,
        description=(
            "Median $/key across the returned comps — the headline anchor "
            "for the exit-cap conversation. Null when fewer than 1 comp."
        ),
    )
    median_cap_rate_pct: float | None = None
    note: str | None = None


# Map extractor field paths to the canonical column on a comp entry.
# The extractor emits ``transaction_comps.<n>.<field>`` rows; ``<n>`` is
# 1-indexed in the order the comps appear in the source table. Field
# names are loose-matched (snake_case + alternate aliases).
_TXN_FIELD_ALIASES: dict[str, str] = {
    "name": "name",
    "hotel_name": "name",
    "property_name": "name",
    "market": "market",
    "city": "market",
    "submarket": "market",
    "sale_date": "sale_date",
    "date_of_sale": "sale_date",
    "transaction_date": "sale_date",
    "date": "sale_date",
    "keys": "keys",
    "rooms": "keys",
    "key_count": "keys",
    "sale_price": "sale_price_usd",
    "sale_price_usd": "sale_price_usd",
    "price": "sale_price_usd",
    "transaction_price": "sale_price_usd",
    "price_per_key": "price_per_key_usd",
    "price_per_key_usd": "price_per_key_usd",
    "ppk": "price_per_key_usd",
    "cap_rate": "cap_rate_pct",
    "cap_rate_pct": "cap_rate_pct",
    "going_in_cap": "cap_rate_pct",
    "buyer": "buyer_name",
    "buyer_name": "buyer_name",
    "purchaser": "buyer_name",
    "buyer_type": "buyer_type",
    "buyer_class": "buyer_type",
    "seller": "seller",
    "seller_name": "seller",
    "vendor": "seller",
    "disposition_by": "seller",
}


def _coerce_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "").strip().rstrip("%").rstrip("$")))
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        cleaned = (
            str(v).replace(",", "").replace("$", "").replace("%", "").strip()
        )
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _normalize_cap_rate(v: float | None) -> float | None:
    """Cap rates ship as both ``8.5`` and ``0.085``. Normalize to 0..30."""
    if v is None:
        return None
    if v <= 1.0:
        return v * 100.0
    return v


@router.get("/{deal_id}/transaction-comps", response_model=TransactionCompsResponse)
async def transaction_comps(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> TransactionCompsResponse:
    """Return comparable hotel sales the extractor pulled off this deal's docs.

    OMs almost always include a "Comparable Sales" table with hotel
    name, sale date, keys, sale price, $/key, and cap rate. The extractor
    emits each row as ``transaction_comps.<n>.<field>``. We aggregate
    them, derive median $/key + median cap rate, and return.

    Tenant-scoped: ``deal_id`` is gated by the deal-belongs check, and
    the underlying join is filtered by ``d.tenant_id`` for belt-and-
    suspenders coverage.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    try:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT er.fields, er.document_id, d.tenant_id, d.doc_type
                      FROM extraction_results er
                      JOIN documents d ON d.id = er.document_id
                     WHERE er.deal_id = :deal
                       AND d.tenant_id = :tenant
                     ORDER BY er.created_at DESC
                    """
                ),
                {"deal": str(deal_id), "tenant": str(tenant_id)},
            )
        ).fetchall()
    except Exception:  # noqa: BLE001
        return TransactionCompsResponse(deal_id=deal_id, comps=[], note="db-error")

    # Aggregate rows from all docs into a per-index bucket. The first
    # extracted value for any given (n, field) wins so a later doc
    # doesn't clobber an OM's cleaner field unless the OM was empty.
    buckets: dict[int, dict[str, Any]] = {}
    sources: dict[int, tuple[str | None, int | None]] = {}

    for r in rows:
        raw = r._mapping["fields"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw else None
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(raw, list):
            continue
        doc_id = str(r._mapping["document_id"]) if r._mapping["document_id"] else None
        for f in raw:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            if not name.startswith("transaction_comps."):
                continue
            try:
                _, idx_part, *rest = name.split(".")
                idx = int(idx_part)
                tail = ".".join(rest).lower()
            except (ValueError, IndexError):
                continue
            canonical = _TXN_FIELD_ALIASES.get(tail)
            if canonical is None:
                continue
            value = f.get("value")
            if value in (None, ""):
                continue
            entry = buckets.setdefault(idx, {})
            entry.setdefault(canonical, value)
            if idx not in sources:
                page = f.get("source_page")
                sources[idx] = (
                    doc_id,
                    int(page) if isinstance(page, (int, float)) else None,
                )

    # Materialize TransactionCompEntry rows.
    comps: list[TransactionCompEntry] = []
    for idx in sorted(buckets):
        b = buckets[idx]
        name = b.get("name")
        if not name or not isinstance(name, str):
            continue
        keys_int = _coerce_int(b.get("keys"))
        sale_price = _coerce_float(b.get("sale_price_usd"))
        ppk = _coerce_float(b.get("price_per_key_usd"))
        if ppk is None and sale_price is not None and keys_int and keys_int > 0:
            ppk = round(sale_price / keys_int, 2)
        cap = _normalize_cap_rate(_coerce_float(b.get("cap_rate_pct")))
        sale_date = b.get("sale_date")
        if sale_date is not None and not isinstance(sale_date, str):
            try:
                sale_date = str(sale_date)
            except Exception:  # noqa: BLE001
                sale_date = None
        market = b.get("market")
        if market is not None and not isinstance(market, str):
            market = str(market)
        buyer_name = b.get("buyer_name")
        if buyer_name is not None and not isinstance(buyer_name, str):
            buyer_name = str(buyer_name)
        buyer_type = b.get("buyer_type")
        if buyer_type is not None and not isinstance(buyer_type, str):
            buyer_type = str(buyer_type)
        seller = b.get("seller")
        if seller is not None and not isinstance(seller, str):
            seller = str(seller)

        doc_id, page = sources.get(idx, (None, None))
        comps.append(
            TransactionCompEntry(
                name=str(name).strip(),
                market=market,
                sale_date=sale_date,
                keys=keys_int,
                sale_price_usd=sale_price,
                price_per_key_usd=ppk,
                cap_rate_pct=cap,
                buyer_name=buyer_name,
                buyer_type=buyer_type,
                seller=seller,
                source_document_id=doc_id,
                source_page=page,
            )
        )

    # Headline anchors — median $/key + median cap rate.
    def _median(xs: list[float]) -> float | None:
        if not xs:
            return None
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    median_ppk = _median([c.price_per_key_usd for c in comps if c.price_per_key_usd])
    median_cap = _median([c.cap_rate_pct for c in comps if c.cap_rate_pct])

    note: str | None = None
    if not comps:
        note = (
            "No transaction comps extracted yet. Upload an OM with a "
            "'Comparable Sales' table to populate this view."
        )

    return TransactionCompsResponse(
        deal_id=deal_id,
        comps=comps,
        median_price_per_key=median_ppk,
        median_cap_rate_pct=median_cap,
        note=note,
    )



# ─────────────────────────── Index Analysis methodology (E-028) ───────────────────────────


class IndexFigureOut(BaseModel):
    """One Index Analysis figure: its value and where it came from."""

    model_config = ConfigDict(extra="forbid")

    value: float | None = None
    #: ``document`` / ``computed`` / ``override``; None = no source (see ``detail``).
    source: Literal["document", "computed", "override"] | None = None
    inputs: list[FieldRefOut] = Field(default_factory=list)
    detail: str | None = None
    period_label: str | None = None

    @classmethod
    def of(cls, f: Any) -> IndexFigureOut:
        return cls(
            value=f.value, source=f.source, inputs=[FieldRefOut.of(r) for r in f.inputs],
            detail=f.detail, period_label=f.period_label,
        )


class IndexMethodOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: str
    label: str
    available: bool
    disabled_reason: str | None = None
    occupancy: IndexFigureOut
    adr: IndexFigureOut
    documents: list[str] = Field(default_factory=list)
    segment: str | None = None
    segments_available: list[str] = Field(default_factory=list)


class IndexMethodologyResponse(BaseModel):
    """``GET /market/{deal_id}/index-methodology`` — the method the analyst
    picked (``index_methodology`` override; default ``str_comp_set``), what each
    method reads, and the four editable assumptions with their sources."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    selected: str
    selected_source: Literal["override", "default"]
    methods: list[IndexMethodOut]
    subject_occupancy: IndexFigureOut
    subject_adr: IndexFigureOut
    subject_period_label: str | None = None
    assumptions: dict[str, IndexFigureOut]
    #: "Use STR rates in the model" — the assumptions feed revenue only when on.
    toggle_on: bool


def index_methodology_response(deal_id: UUID, reading: Any) -> IndexMethodologyResponse:
    """Serialize a ``resolve_index_methodology`` reading (pure; unit-tested)."""
    return IndexMethodologyResponse(
        deal_id=deal_id,
        selected=reading.selected,
        selected_source=reading.selected_source,
        methods=[
            IndexMethodOut(
                method=x.method, label=x.label, available=x.available, disabled_reason=x.disabled_reason,
                occupancy=IndexFigureOut.of(x.occupancy), adr=IndexFigureOut.of(x.adr),
                documents=list(x.documents), segment=x.segment, segments_available=list(x.segments_available),
            )
            for x in reading.methods
        ],
        subject_occupancy=IndexFigureOut.of(reading.subject_occupancy),
        subject_adr=IndexFigureOut.of(reading.subject_adr),
        subject_period_label=reading.subject_period_label,
        assumptions={k: IndexFigureOut.of(v) for k, v in reading.assumptions.items()},
        toggle_on=reading.toggle_on,
    )


@router.get("/{deal_id}/index-methodology", response_model=IndexMethodologyResponse)
async def index_methodology(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> IndexMethodologyResponse:
    from ..services.index_methodology import load_index_rows, resolve_index_methodology

    row = (
        await session.execute(
            text("SELECT service, field_overrides FROM deals WHERE id = :id AND tenant_id = :tenant"),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"deal {deal_id} not found")
    m = row._mapping
    overrides = _coerce_overrides(m.get("field_overrides"))
    try:
        rows = await load_index_rows(session, deal_id=deal_id, tenant_id=tenant_id)
    except Exception:  # the panel must render (all methods disabled) rather than 500
        logger.exception("index_methodology: extraction read failed")
        rows = []
    reading = resolve_index_methodology(rows, overrides, service_hint=m.get("service"))
    return index_methodology_response(deal_id, reading)
