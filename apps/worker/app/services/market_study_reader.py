"""Demand growth + supply growth from MARKET_STUDY extractions (FON-61 E-008).

Two CoStar reports uploaded under "STR / Comp Set" were routed to the
MARKET_STUDY lane and extracted by the GENERIC extractor (no market_study
schema existed), so the LLM put the data under the closest canonical
prefixes it knew. The live paths on the tester's deal are:

* Submarket report ("Miami Beach-Hospitality-Submarket-2025-12-10"):
  - trend: ``pnl_benchmark.market.(demand|supply)_change_<YYYY>_(annual|q[1-4]|forecast)``
    (fractions: 0.25 = +25%),
  - pipeline headline: ``property_overview.rooms_under_construction_{count,total}``,
    ``property_overview.under_construction_pct_of_(existing_)inventory``,
    ``property_overview.final_planning_rooms(_total)``,
    ``ttm_performance.subject.rooms_under_construction``,
  - pipeline list: ``property_overview.pipeline_hotel_<n>_{name,keys,expected_open,tier,developer}``,
  - TTM levels ``ttm_performance.subject.(demand|supply)_room_nights`` (not growth).
* Pipeline export ("Miami Beach Supply 12.10.25.xlsx", MULTI-market):
  ``market_study.pipeline.<slug>.{name,market,submarket,keys,status,...}`` with
  ``status`` in {"Under Construction", "Final Planning", ...} and rows for
  Boston / Tampa / San Diego alongside Miami Beach — it MUST be filtered to
  the deal's market before anything is summed.

The canonical ``market_study.*`` / ``under_construction.*`` paths
(``extraction_schemas/market_study.md``) and the CBRE-style
``cbre_horizons.*`` rows are read first; the live names above are read next.
Every figure names its document and field (``FieldRef``).

Precedence
----------
Demand growth: (1) a REPORTED figure — TTM actual, then the latest actual
annual year, then an undated figure, then the latest quarterly; a
forecast-tagged row never ranks as actual and the earliest forecast is
surfaced separately (``forecast_pct`` / ``forecast_label``); (2) derived
from two years of a demand room-night series (``latest ÷ prior - 1``);
(3) ``no_source``.

Supply growth (pipeline over inventory, under construction and final
planning kept separate):
* under-construction ROOMS: canonical total → ``property_overview`` total /
  count → ``ttm_performance.subject.rooms_under_construction`` → pipeline
  rows summed by status (canonical rows + the market-filtered export);
* existing ROOMS: canonical → ``property_overview.*{inventory|existing}*``
  → latest actual year of the supply series;
* under-construction SHARE: the report's own ``…under_construction_pct_of_
  inventory`` (``basis="reported"``) → rooms ÷ existing (``"computed"``);
  final planning likewise (``final_planning_rooms`` / a reported share);
* reported supply change: same ranking as demand, forecast surfaced apart.
A missing piece yields ``no_source`` with a "not in the uploaded reports"
detail; when the export has rows but none match the deal's market the
detail says "no pipeline rows for <market> in <file>". ``no_document`` when
the deal has no MARKET_STUDY at all.

Export filter rules (live export: 30 rows, 3 with a ``market`` /
``submarket``, 27 with neither):
* a row matches when the FULL normalised term equals its market or
  submarket, or appears in one as a whole phrase — never on a shared token
  ("Miami" / "Miami Airport" rows do not match a "Miami Beach" deal);
* a row with neither a market nor a submarket is ``market_unknown_rows``
  in the filter record and is not counted either way;
* a report's own ``submarket`` is a filter term only when the report is the
  subject's submarket report — a multi-market export (rows carrying ≥ 2
  distinct ``market`` values) mis-reads its own header ("Miami Airport"),
  so its self-stated submarket is never a term.

Unit guard on growth (``*_change*``) candidates: a value is a rate only when
it is a fraction (|v| ≤ 1.5) or a plausible percent (|v| ≤ 150 with a
``%`` / ``pct`` hint on the unit, in the path or in the raw text). Anything
larger is an absolute delta (live: ``cbre_horizons.overall_supply.2025_ytd.
demand_change = -35997`` room nights) — surfaced as
``demand_room_nights_change`` / ``supply_rooms_change`` with provenance,
never as the rate.

Pure functions — no DB, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any, Literal

from .market_fields import FieldRef, FieldRow, coerce_float, coerce_int, pct_points

REASON_NO_DOCUMENT = "no_document"
REASON_NO_SOURCE = "no_source"

# Namespaces read INSIDE a MARKET_STUDY document for growth figures.
GROWTH_PREFIXES = ("market_study.", "cbre_horizons.", "pnl_benchmark.market.", "property_overview.")
# Kept for callers that import the older name.
MARKET_STUDY_PREFIXES = (*GROWTH_PREFIXES, "under_construction.")
UNDER_CONSTRUCTION_PREFIX = "under_construction."
PIPELINE_EXPORT_PREFIX = "market_study.pipeline."
PIPELINE_HOTEL_RE = re.compile(r"^property_overview\.pipeline_hotel_(\d+)_(.+)$", re.IGNORECASE)
# Namespace prefixes stripped before a path is tokenised, so the words of
# the namespace ("property" in ``property_overview``, "subject" in
# ``ttm_performance.subject``, "market" in ``pnl_benchmark.market``) never
# collide with the exclusion vocabulary below.
_NAMESPACE_PREFIXES = (
    "pnl_benchmark.market.", "ttm_performance.subject.", "ttm_performance.segment.", "property_overview.",
    "market_study.", "cbre_horizons.", "under_construction.",
)

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_QUARTER_RE = re.compile(r"^q([1-4])$")
_SPLIT_RE = re.compile(r"[._\-\s/]+")

GROWTH_TOKENS = frozenset({"growth", "change", "chg", "yoy", "delta", "var", "variance"})
FORECAST_TOKENS = frozenset(
    {"forecast", "forecasted", "forecasts", "projected", "projection", "projections", "proj", "outlook", "f"}
)
TTM_TOKENS = frozenset({"ttm", "t12", "trailing", "trailing12", "12mo", "12m", "l12m", "last12", "current"})
# Year-to-date path tokens (``2025_ytd``) — a partial year, labelled "2025 YTD".
YTD_TOKENS = frozenset({"ytd", "yeartodate"})
PIPELINE_TOKENS = frozenset(
    {
        "under", "construction", "uc", "planning", "planned", "proposed", "pipeline",
        "delivered", "deliveries", "delivery", "openings", "opening", "prospective",
        "deferred", "abandoned", "unentitled", "entitled",
    }
)
ROOMS_TOKENS = frozenset({"rooms", "keys", "units", "supply", "inventory"})
EXISTING_TOKENS = frozenset({"existing", "inventory", "total", "current"})
SHARE_TOKENS = frozenset({"pct", "percent", "share", "ratio"})
NOT_SUBMARKET_TOKENS = frozenset(
    {
        "subject", "compset", "comp", "competitive", "property", "properties", "hotels",
        "projects", "share", "pct", "percent",
    }
)
FINAL_PLANNING_TOKENS = frozenset({"final"})
PLANNED_TOKENS = frozenset({"planned", "proposed", "unentitled", "prospective", "early", "deferred"})
NOT_PIPELINE_STATUS_TOKENS = frozenset(
    {"abandoned", "canceled", "cancelled", "completed", "delivered", "open", "opened", "existing", "operating"}
)

Bucket = Literal["under_construction", "final_planning", "planned"]
ShareBasis = Literal["reported", "computed"]

# Unit guard for ``*_change*`` growth candidates. The live CBRE Horizons
# rows ``cbre_horizons.overall_supply.2025_ytd.demand_change = -35997`` and
# ``…supply_change = 48998`` are room-night / room DELTAS, not rates — and,
# ranked as the latest annual actual, they were shown as "-35,997%" demand
# growth. A candidate is a rate only when it is a fraction (|v| ≤ 1.5) or a
# plausible percent (|v| ≤ 150 with a % / pct hint on the unit, in the path
# or in the raw text); anything larger is an absolute change, surfaced as a
# level with provenance, never as a rate.
RATE_FRACTION_MAX = 1.5
RATE_PERCENT_MAX = 150.0
_PCT_HINT_TOKENS = frozenset({"pct", "percent", "percentage"})
_PCT_UNITS = frozenset({"pct", "percent", "%"})
_SUBJECTS = frozenset({"demand", "supply"})


def _tokens(name: str) -> list[str]:
    return [t for t in _SPLIT_RE.split(name.lower()) if t]


def _path_tokens(field_name: str) -> list[str]:
    """Tokens of a field path with its namespace prefix stripped."""
    lname = field_name.strip().lower()
    for prefix in _NAMESPACE_PREFIXES:
        if lname.startswith(prefix):
            lname = lname[len(prefix):]
            break
    return _tokens(lname)


def _year_of(tokens: Iterable[str]) -> int | None:
    for t in tokens:
        if _YEAR_RE.match(t):
            return int(t)
    return None


def _quarter_of(tokens: Iterable[str]) -> int | None:
    for t in tokens:
        m = _QUARTER_RE.match(t)
        if m:
            return int(m.group(1))
    return None


def _period_tags(rows: Sequence[FieldRow]) -> dict[str, str]:
    """``<parent path>`` → the ``.period`` sibling's value (CBRE convention)."""
    out: dict[str, str] = {}
    for r in rows:
        lname = r.lname
        if lname.endswith(".period") and isinstance(r.value, str):
            out.setdefault(lname[: -len(".period")], r.value.strip().lower())
    return out


def _is_forecast(row: FieldRow, tokens: list[str], tags: dict[str, str], as_of_year: int) -> bool:
    if set(tokens) & FORECAST_TOKENS:
        return True
    if row.period and "forecast" in row.period:
        return True
    parent = row.lname.rsplit(".", 1)[0]
    if "forecast" in tags.get(parent, ""):
        return True
    year = _year_of(tokens)
    return year is not None and year > as_of_year


# ───────────────────────────── readings ─────────────────────────────


@dataclass(frozen=True)
class GrowthReading:
    value_pct: float | None
    period_label: str | None
    basis: Literal["reported", "derived_from_series"] | None
    inputs: list[FieldRef]
    reason: str | None
    detail: str | None
    #: The report's forecast for the same series, shown AS forecast — never
    #: in place of an actual.
    forecast_pct: float | None = None
    forecast_label: str | None = None
    forecast_input: FieldRef | None = None
    #: An absolute demand change the report states (room nights, as
    #: printed) that the unit guard kept OUT of the rate — a level with
    #: provenance, never shown as a percentage.
    demand_room_nights_change: FieldRef | None = None
    demand_room_nights_change_period: str | None = None


@dataclass(frozen=True)
class PipelineHotel:
    """One pipeline project row (canonical, submarket-report list or export)."""

    name: str | None
    keys: int | None
    #: The status as printed ("Under Construction", "Final Planning", …).
    status: str | None
    bucket: Bucket | None
    market: str | None
    submarket: str | None
    expected_open: str | None
    doc_name: str | None
    doc_id: str | None
    page: int | None
    source_prefix: str
    keys_ref: FieldRef | None = None


@dataclass(frozen=True)
class PipelineFilter:
    """How the multi-market export was narrowed to the deal's market."""

    terms: list[str]
    matched: int
    total: int
    doc_name: str | None
    note: str | None
    #: Export rows carrying neither a ``market`` nor a ``submarket`` — not
    #: counted as matched or unmatched (live: 27 of 30).
    market_unknown_rows: int = 0


@dataclass(frozen=True)
class SupplyReading:
    existing_rooms: int | None
    existing_period_label: str | None
    under_construction_rooms: int | None
    final_planning_rooms: int | None
    planned_rooms: int | None
    #: under-construction share of inventory, percent points.
    under_construction_pct: float | None
    #: final-planning share of inventory, percent points.
    final_planning_pct: float | None
    reported_supply_change_pct: float | None
    reported_supply_change_period: str | None
    inputs: list[FieldRef]
    reason: str | None
    detail: str | None
    under_construction_pct_basis: ShareBasis | None = None
    final_planning_pct_basis: ShareBasis | None = None
    forecast_supply_change_pct: float | None = None
    forecast_supply_change_period: str | None = None
    #: Pipeline projects IN the deal's market (export rows that passed the
    #: filter, canonical rows, and the submarket report's own list).
    pipeline_hotels: list[PipelineHotel] | None = None
    pipeline_filter: PipelineFilter | None = None
    #: An absolute supply change the report states (rooms, as printed) that
    #: the unit guard kept OUT of ``reported_supply_change_pct``.
    supply_rooms_change: FieldRef | None = None
    supply_rooms_change_period: str | None = None


# ───────────────────────────── growth candidates ─────────────────────────────


@dataclass(frozen=True)
class _GrowthCandidate:
    rank: tuple[int, int, int]
    order: int
    value: float
    label: str
    ref: FieldRef
    is_forecast: bool


def _rank(is_ttm: bool, year: int | None, quarter: int | None, is_forecast: bool) -> tuple[int, int, int]:
    """Lower sorts first: TTM actual, latest annual actual, undated, latest
    quarterly, earliest forecast."""
    if is_forecast:
        return (4, year or 9999, 0)
    if is_ttm:
        return (0, 0, 0)
    if year is not None and quarter is not None:
        return (3, -year, -quarter)
    if year is not None:
        return (1, -year, 0)
    return (2, 0, 0)


def _label(
    is_ttm: bool, year: int | None, quarter: int | None, is_forecast: bool, is_ytd: bool = False
) -> str:
    """``TTM`` / ``2025 Q1`` / ``2025 YTD`` / ``2025`` (+ `` forecast``) / ``as reported``.

    A year-to-date row (``cbre_horizons.overall_supply.2025_ytd.demand_pct_change``)
    is a partial year and must never be labelled as the full year.
    """
    if is_ttm:
        return "TTM"
    suffix = " forecast" if is_forecast else ""
    if year is not None and quarter is not None:
        return f"{year} Q{quarter}{suffix}"
    if year is not None and is_ytd:
        return f"{year} YTD{suffix}"
    if year is not None:
        return f"{year}{suffix}"
    if is_ytd:
        return f"YTD{suffix}"
    return "forecast" if is_forecast else "as reported"


def _rate_points(row: FieldRow, tokset: set[str]) -> tuple[float | None, float | None]:
    """``(percent points, raw value)`` for a ``*_change*`` candidate.

    Points are None when the unit guard says the value is an absolute
    delta (|v| > 1.5 without a % / pct hint, or |v| > 150 regardless);
    the raw value is None when the cell isn't numeric at all.
    """
    raw = coerce_float(row.value)
    if raw is None:
        return None, None
    if abs(raw) <= RATE_FRACTION_MAX:
        return pct_points(row.value), raw
    hinted = (
        row.unit in _PCT_UNITS
        or bool(tokset & _PCT_HINT_TOKENS)
        or (isinstance(row.value, str) and "%" in row.value)
    )
    if abs(raw) <= RATE_PERCENT_MAX and hinted:
        return raw, raw
    return None, raw


def _growth_candidates(
    rows: Sequence[FieldRow], subject: str, *, as_of_year: int
) -> tuple[list[_GrowthCandidate], list[_GrowthCandidate]]:
    """``(rates, absolute changes)`` for ``demand`` / ``supply``, each
    sorted best-first. Absolute changes carry the raw value as printed."""
    tags = _period_tags(rows)
    rates: list[_GrowthCandidate] = []
    deltas: list[_GrowthCandidate] = []
    for order, r in enumerate(rows):
        if not r.lname.startswith(GROWTH_PREFIXES) or r.lname.startswith(PIPELINE_EXPORT_PREFIX):
            continue
        toks = _path_tokens(r.field_name)
        tokset = set(toks)
        if subject not in tokset or not (tokset & GROWTH_TOKENS):
            continue
        # When the leaf names a subject it must be THIS one: the CBRE row
        # ``overall_supply.2025_ytd.demand_change`` is a demand figure even
        # though "supply" sits in its parent path.
        leaf_subjects = set(_tokens(r.lname.rsplit(".", 1)[-1])) & _SUBJECTS
        if leaf_subjects and subject not in leaf_subjects:
            continue
        if tokset & (NOT_SUBMARKET_TOKENS - SHARE_TOKENS):
            continue
        points, raw = _rate_points(r, tokset)
        if raw is None:
            continue
        is_ttm = bool(tokset & TTM_TOKENS)
        is_ytd = bool(tokset & YTD_TOKENS)
        year = _year_of(toks)
        quarter = _quarter_of(toks)
        fc = _is_forecast(r, toks, tags, as_of_year)
        cand = _GrowthCandidate(
            rank=_rank(is_ttm, year, quarter, fc), order=order,
            value=points if points is not None else raw,
            label=_label(is_ttm, year, quarter, fc, is_ytd), ref=FieldRef.of(r), is_forecast=fc,
        )
        (rates if points is not None else deltas).append(cand)
    key = lambda c: (c.rank, c.order)  # noqa: E731
    return sorted(rates, key=key), sorted(deltas, key=key)


def _pick_reported_growth(
    rows: Sequence[FieldRow], subject: str, *, as_of_year: int
) -> tuple[_GrowthCandidate | None, _GrowthCandidate | None, _GrowthCandidate | None]:
    """(best actual rate, earliest forecast rate, best actual absolute
    change) for ``demand`` / ``supply``."""
    rates, deltas = _growth_candidates(rows, subject, as_of_year=as_of_year)
    actual = next((c for c in rates if not c.is_forecast), None)
    forecast = next((c for c in rates if c.is_forecast), None)
    level = next((c for c in deltas if not c.is_forecast), None)
    return actual, forecast, level


def _pick_year_series(
    rows: Sequence[FieldRow], subject: str, *, as_of_year: int, forbid: frozenset[str]
) -> dict[int, tuple[float, FieldRef]]:
    """``{year: (value, ref)}`` of a level (not growth) series — actual years only."""
    tags = _period_tags(rows)
    out: dict[int, tuple[float, FieldRef]] = {}
    for r in rows:
        if not r.lname.startswith(GROWTH_PREFIXES) or r.lname.startswith(PIPELINE_EXPORT_PREFIX):
            continue
        toks = _path_tokens(r.field_name)
        tokset = set(toks)
        if subject not in tokset or (tokset & GROWTH_TOKENS) or (tokset & forbid):
            continue
        year = _year_of(toks)
        if year is None or _quarter_of(toks) is not None or _is_forecast(r, toks, tags, as_of_year):
            continue
        if r.unit in {"pct", "percent", "%", "ratio"}:
            continue
        v = coerce_float(r.value)
        if v is None or v <= 0:
            continue
        out.setdefault(year, (v, FieldRef.of(r)))
    return out


def read_demand_growth(
    rows: Sequence[FieldRow], *, has_documents: bool, as_of_year: int | None = None
) -> GrowthReading:
    as_of = as_of_year or date.today().year
    if not has_documents:
        return GrowthReading(
            value_pct=None, period_label=None, basis=None, inputs=[],
            reason=REASON_NO_DOCUMENT,
            detail="No market study / CoStar submarket report is on the deal.",
        )
    actual, forecast, level = _pick_reported_growth(rows, "demand", as_of_year=as_of)
    fc_kwargs: dict[str, Any] = (
        {"forecast_pct": round(forecast.value, 4), "forecast_label": forecast.label, "forecast_input": forecast.ref}
        if forecast
        else {}
    )
    if level is not None:
        fc_kwargs["demand_room_nights_change"] = level.ref
        fc_kwargs["demand_room_nights_change_period"] = level.label
    if actual is not None:
        return GrowthReading(
            value_pct=round(actual.value, 4), period_label=actual.label, basis="reported",
            inputs=[actual.ref], reason=None, detail=None, **fc_kwargs,
        )
    series = _pick_year_series(
        rows, "demand", as_of_year=as_of, forbid=NOT_SUBMARKET_TOKENS | PIPELINE_TOKENS
    )
    if len(series) >= 2:
        years = sorted(series)
        latest, prior = years[-1], years[-2]
        v_latest, ref_latest = series[latest]
        v_prior, ref_prior = series[prior]
        growth = (v_latest / v_prior - 1.0) * 100.0
        return GrowthReading(
            value_pct=round(growth, 4),
            period_label=f"{prior}→{latest}",
            basis="derived_from_series",
            inputs=[ref_prior, ref_latest],
            reason=None,
            detail=f"Derived from the demand series: {latest} ÷ {prior} - 1.",
            **fc_kwargs,
        )
    return GrowthReading(
        value_pct=None, period_label=None, basis=None, inputs=[],
        reason=REASON_NO_SOURCE,
        detail="Demand growth (or a two-year demand series) is not in the uploaded reports.",
        **fc_kwargs,
    )


# ───────────────────────────── rooms + shares ─────────────────────────────


def _pick_rooms(
    rows: Sequence[FieldRow],
    *,
    prefixes: tuple[str, ...],
    require_all: frozenset[str] = frozenset(),
    require_any: frozenset[str] = ROOMS_TOKENS,
    forbid: frozenset[str] = frozenset(),
    year_mode: Literal["undated", "dated"] = "undated",
    as_of_year: int,
) -> tuple[int, str | None, FieldRef] | None:
    """A room count whose path tokens satisfy the rule.

    ``year_mode="undated"`` → the first (newest) figure with no year in its
    path; ``"dated"`` → the latest ACTUAL year of a year-keyed series (the
    period label is that year). A non-integral value (a 0.057 share the
    LLM filed under a rooms-ish name) never counts as rooms.
    """
    tags = _period_tags(rows)
    dated: dict[int, tuple[int, FieldRef]] = {}
    for r in rows:
        if not r.lname.startswith(prefixes) or r.lname.startswith(PIPELINE_EXPORT_PREFIX):
            continue
        if PIPELINE_HOTEL_RE.match(r.lname):
            continue
        toks = _path_tokens(r.field_name)
        tokset = set(toks)
        if require_all and not require_all <= tokset:
            continue
        if require_any and not (tokset & require_any):
            continue
        if tokset & (forbid | GROWTH_TOKENS):
            continue
        if r.unit in {"pct", "percent", "%", "ratio", "usd"}:
            continue
        year = _year_of(toks)
        if (year is None) != (year_mode == "undated"):
            continue
        f = coerce_float(r.value)
        if f is None or f < 0 or abs(f - round(f)) > 1e-9:
            continue
        v = round(f)
        if year is None:
            return (v, None, FieldRef.of(r))
        if not _is_forecast(r, toks, tags, as_of_year):
            dated.setdefault(year, (v, FieldRef.of(r)))
    if dated:
        latest = max(dated)
        v, ref = dated[latest]
        return (v, str(latest), ref)
    return None


def _pick_existing_rooms(
    rows: Sequence[FieldRow], *, as_of_year: int
) -> tuple[int, str | None, FieldRef] | None:
    """The submarket's existing inventory.

    1. An undated ``market_study.*`` figure whose path says existing /
       inventory / total / current rooms (``market_study.supply.existing_rooms``).
    2. An undated ``property_overview.*`` figure that says inventory / existing
       rooms (the generic extractor's home for the report header).
    3. Otherwise the latest actual year of the supply series.
    Pipeline, subject-hotel and comp-set room counts never qualify.
    """
    forbid = PIPELINE_TOKENS | NOT_SUBMARKET_TOKENS
    remaining = list(rows)
    while True:
        undated = _pick_rooms(
            remaining, prefixes=("market_study.",), require_any=ROOMS_TOKENS, forbid=forbid,
            year_mode="undated", as_of_year=as_of_year,
        )
        if undated is None:
            break
        if set(_path_tokens(undated[2].field_name)) & EXISTING_TOKENS:
            return undated
        # A bare undated "rooms" figure that doesn't say what it is — skip
        # it and look for the next explicit one.
        remaining = [
            r for r in remaining
            if not (r.field_name == undated[2].field_name and r.doc_id == undated[2].doc_id)
        ]
    header = _pick_rooms(
        rows, prefixes=("property_overview.",), require_all=frozenset(), require_any=ROOMS_TOKENS,
        forbid=forbid, year_mode="undated", as_of_year=as_of_year,
    )
    while header is not None and not (set(_path_tokens(header[2].field_name)) & frozenset({"inventory", "existing"})):
        rows = [
            r for r in rows
            if not (r.field_name == header[2].field_name and r.doc_id == header[2].doc_id)
        ]
        header = _pick_rooms(
            rows, prefixes=("property_overview.",), require_any=ROOMS_TOKENS, forbid=forbid,
            year_mode="undated", as_of_year=as_of_year,
        )
    if header is not None:
        return header
    return _pick_rooms(
        rows, prefixes=("market_study.",), require_any=ROOMS_TOKENS, forbid=forbid,
        year_mode="dated", as_of_year=as_of_year,
    )


def _pick_reported_share(
    rows: Sequence[FieldRow], require_all: frozenset[str]
) -> tuple[float, FieldRef] | None:
    """The report's own "<stage> as % of inventory" figure, percent points."""
    for r in rows:
        if not r.lname.startswith(("market_study.", "property_overview.")) or r.lname.startswith(PIPELINE_EXPORT_PREFIX):
            continue
        tokset = set(_path_tokens(r.field_name))
        if not require_all <= tokset or not (tokset & SHARE_TOKENS):
            continue
        if tokset & GROWTH_TOKENS or tokset & {"hotels", "projects", "subject"}:
            continue
        v = pct_points(r.value)
        if v is None or v < 0 or v > 100:
            continue
        return round(v, 4), FieldRef.of(r)
    return None


# ───────────────────────────── pipeline rows ─────────────────────────────


def _bucket_for(status: str | None, *, default: Bucket | None) -> Bucket | None:
    if not status or not status.strip():
        return default
    s = set(_tokens(status))
    if ("under" in s and "construction" in s) or "uc" in s:
        return "under_construction"
    if s & FINAL_PLANNING_TOKENS or "planning" in s:
        return "final_planning"
    if s & PLANNED_TOKENS:
        return "planned"
    if s & NOT_PIPELINE_STATUS_TOKENS:
        return None
    return default


_KEYS_ATTRS = frozenset({"rooms", "keys", "units", "room_count", "key_count"})
_STATUS_ATTRS = frozenset({"status", "stage", "phase", "pipeline_status"})
_OPEN_ATTRS = frozenset(
    {"expected_open", "expected_opening", "opening", "open_date", "delivery", "expected_delivery", "completion", "year_built"}
)


def _norm_term(s: str) -> str:
    """Lower-cased, commas and slashes read as separators ("Boston, MA" →
    "boston ma", "Miami Beach/South Beach" → "miami beach south beach")."""
    return " ".join(s.lower().replace(",", " ").replace("/", " ").split())


def _geo_matches(term: str, geo: str) -> bool:
    """The FULL normalised term equals the geo or appears in it as a whole
    phrase (token-aligned) — never a shared token: "miami beach" does not
    match "miami airport" or "miami"."""
    if term == geo:
        return True
    t, g = term.split(), geo.split()
    return bool(t) and any(g[i:i + len(t)] == t for i in range(len(g) - len(t) + 1))


def _matches_market(hotel_market: str | None, hotel_submarket: str | None, terms: Sequence[str]) -> bool:
    geo = [_norm_term(g) for g in (hotel_submarket, hotel_market) if isinstance(g, str) and g.strip()]
    return any(_geo_matches(t, g) for t in terms if t for g in geo)


def _collect_pipeline_hotels(
    rows: Sequence[FieldRow], market_terms: Sequence[str]
) -> tuple[list[PipelineHotel], PipelineFilter | None]:
    """Every pipeline project row, with the export narrowed to the market.

    Returns the hotels that count for this deal (canonical rows and the
    submarket report's list are single-market; export rows must match the
    deal's market / submarket, or the export must carry no market column at
    all) plus the filter record for the export.
    """
    terms = list(dict.fromkeys(_norm_term(t) for t in market_terms if isinstance(t, str) and t.strip()))
    grouped: dict[tuple[str, str | None], dict[str, object]] = {}
    for r in rows:
        lname = r.lname
        key: tuple[str, str | None] | None = None
        attr: str | None = None
        kind: str | None = None
        if lname.startswith(UNDER_CONSTRUCTION_PREFIX):
            rest = lname[len(UNDER_CONSTRUCTION_PREFIX):]
            parts = rest.split(".")
            if len(parts) < 2 or parts[0] in {"total", "totals", "summary"}:
                continue
            key, attr, kind = (f"{UNDER_CONSTRUCTION_PREFIX}{'.'.join(parts[:-1])}", r.doc_id), parts[-1], "canonical"
        elif lname.startswith(PIPELINE_EXPORT_PREFIX):
            rest = lname[len(PIPELINE_EXPORT_PREFIX):]
            parts = rest.split(".")
            if len(parts) < 2:
                continue
            key, attr, kind = (f"{PIPELINE_EXPORT_PREFIX}{'.'.join(parts[:-1])}", r.doc_id), parts[-1], "export"
        else:
            m = PIPELINE_HOTEL_RE.match(lname)
            if m:
                key, attr, kind = (f"property_overview.pipeline_hotel_{m.group(1)}", r.doc_id), m.group(2), "report_list"
        if key is None or attr is None:
            continue
        g = grouped.setdefault(key, {"kind": kind, "doc_name": r.doc_name, "doc_id": r.doc_id, "page": r.page})
        if attr == "name" and isinstance(r.value, str):
            g.setdefault("name", r.value.strip())
        elif attr in _KEYS_ATTRS:
            v = coerce_int(r.value)
            if v is not None and v > 0 and "keys" not in g:
                g["keys"] = v
                g["keys_ref"] = FieldRef.of(r)
        elif attr in _STATUS_ATTRS and isinstance(r.value, str):
            g.setdefault("status", r.value.strip())
        elif attr == "market" and isinstance(r.value, str):
            g.setdefault("market", r.value.strip())
        elif attr == "submarket" and isinstance(r.value, str):
            g.setdefault("submarket", r.value.strip())
        elif attr in _OPEN_ATTRS and r.value not in (None, ""):
            g.setdefault("expected_open", str(r.value).strip())

    hotels: list[PipelineHotel] = []
    export_total = 0
    export_matched = 0
    export_unknown = 0
    export_docs: list[str] = []
    export_has_geo = any(
        g["kind"] == "export" and (g.get("market") or g.get("submarket")) for g in grouped.values()
    )
    for (prefix, _doc), g in grouped.items():
        kind = str(g["kind"])
        default: Bucket | None = "under_construction" if kind == "canonical" else None
        hotel = PipelineHotel(
            name=g.get("name"),  # type: ignore[arg-type]
            keys=g.get("keys"),  # type: ignore[arg-type]
            status=g.get("status"),  # type: ignore[arg-type]
            bucket=_bucket_for(g.get("status"), default=default),  # type: ignore[arg-type]
            market=g.get("market"),  # type: ignore[arg-type]
            submarket=g.get("submarket"),  # type: ignore[arg-type]
            expected_open=g.get("expected_open"),  # type: ignore[arg-type]
            doc_name=g.get("doc_name"),  # type: ignore[arg-type]
            doc_id=g.get("doc_id"),  # type: ignore[arg-type]
            page=g.get("page"),  # type: ignore[arg-type]
            source_prefix=prefix,
            keys_ref=g.get("keys_ref"),  # type: ignore[arg-type]
        )
        if kind != "export":
            hotels.append(hotel)
            continue
        export_total += 1
        if hotel.doc_name and hotel.doc_name not in export_docs:
            export_docs.append(hotel.doc_name)
        if export_has_geo and not (hotel.market or hotel.submarket):
            # The export has a market column but this row left it blank —
            # unknown market, counted neither as matched nor as unmatched.
            export_unknown += 1
            continue
        matched = (not export_has_geo) or _matches_market(hotel.market, hotel.submarket, terms)
        if matched:
            export_matched += 1
            hotels.append(hotel)

    pfilter: PipelineFilter | None = None
    if export_total:
        doc_label = ", ".join(export_docs) if export_docs else "the pipeline export"
        unknown_note = (
            f"{export_unknown} of {export_total} rows carry no market or submarket and were not counted"
            if export_unknown
            else None
        )
        if not export_has_geo:
            note = f"{doc_label} carries no market column; all {export_total} rows were taken as the deal's market."
        elif not terms:
            note = f"No deal market to filter {doc_label} by — its {export_total} rows were not summed."
        elif export_matched == 0:
            note = f"no pipeline rows for {market_terms[0]} in {doc_label}"
            if unknown_note:
                note += f" ({unknown_note})"
        elif unknown_note:
            note = f"{unknown_note} in {doc_label}."
        else:
            note = None
        pfilter = PipelineFilter(
            terms=list(terms), matched=export_matched, total=export_total,
            doc_name=export_docs[0] if export_docs else None, note=note,
            market_unknown_rows=export_unknown,
        )
    return hotels, pfilter


def _sum_by_bucket(hotels: Sequence[PipelineHotel]) -> dict[str, tuple[int, list[FieldRef]]]:
    out: dict[str, tuple[int, list[FieldRef]]] = {}
    for h in hotels:
        if h.bucket is None or not h.keys:
            continue
        total, refs = out.get(h.bucket, (0, []))
        out[h.bucket] = (total + h.keys, [*refs, *( [h.keys_ref] if h.keys_ref else [] )])
    return out


def multi_market_export_docs(rows: Sequence[FieldRow]) -> set[str | None]:
    """Documents whose ``market_study.pipeline.<slug>.market`` rows carry
    ≥ 2 distinct values — multi-market pipeline exports."""
    markets_by_doc: dict[str | None, set[str]] = {}
    for r in rows:
        lname = r.lname
        if not lname.startswith(PIPELINE_EXPORT_PREFIX) or lname.rsplit(".", 1)[-1] != "market":
            continue
        if isinstance(r.value, str) and r.value.strip():
            markets_by_doc.setdefault(r.doc_id, set()).add(_norm_term(r.value))
    return {doc for doc, markets in markets_by_doc.items() if len(markets) >= 2}


def market_terms_from_rows(rows: Sequence[FieldRow]) -> list[str]:
    """Submarket names the MARKET_STUDY reports state about themselves
    (``property_overview.submarket``, ``market_study.submarket``, …).

    A report's own submarket is a term only when the report IS the
    subject's submarket report: a multi-market pipeline export (its rows
    carry ≥ 2 distinct ``market`` values) mis-reads its own header — the
    live Supply export filed ``property_overview.submarket = "Miami
    Airport"`` — so its self-stated submarket is never a term. The export's
    per-row ``market`` / ``submarket`` columns are never terms either.
    """
    skip_docs = multi_market_export_docs(rows)
    out: list[str] = []
    for r in rows:
        lname = r.lname
        if lname.startswith(PIPELINE_EXPORT_PREFIX) or not lname.startswith(GROWTH_PREFIXES):
            continue
        if lname.rsplit(".", 1)[-1] not in {"submarket", "submarket_name"}:
            continue
        if r.doc_id in skip_docs:
            continue
        if isinstance(r.value, str) and r.value.strip() and r.value.strip() not in out:
            out.append(r.value.strip())
    return out


def read_supply_growth(
    rows: Sequence[FieldRow],
    *,
    has_documents: bool,
    as_of_year: int | None = None,
    market_terms: Sequence[str] = (),
) -> SupplyReading:
    as_of = as_of_year or date.today().year
    empty = SupplyReading(
        existing_rooms=None, existing_period_label=None, under_construction_rooms=None,
        final_planning_rooms=None, planned_rooms=None, under_construction_pct=None,
        final_planning_pct=None, reported_supply_change_pct=None,
        reported_supply_change_period=None, inputs=[], reason=None, detail=None,
    )
    if not has_documents:
        return replace(
            empty,
            reason=REASON_NO_DOCUMENT,
            detail="No market study / CoStar submarket report is on the deal.",
        )
    inputs: list[FieldRef] = []

    hotels, pfilter = _collect_pipeline_hotels(rows, market_terms)
    sums = _sum_by_bucket(hotels)

    existing = _pick_existing_rooms(rows, as_of_year=as_of)
    existing_rooms = existing[0] if existing else None
    existing_label = existing[1] if existing else None
    if existing:
        inputs.append(existing[2])

    uc_forbid = NOT_SUBMARKET_TOKENS | FINAL_PLANNING_TOKENS | PLANNED_TOKENS
    uc = (
        _pick_rooms(
            rows, prefixes=(UNDER_CONSTRUCTION_PREFIX,), require_all=frozenset({"total"}), as_of_year=as_of,
        )
        or _pick_rooms(
            rows, prefixes=("market_study.", "property_overview."),
            require_all=frozenset({"under", "construction"}), forbid=uc_forbid, as_of_year=as_of,
        )
        or _pick_rooms(
            rows, prefixes=("ttm_performance.subject.",), require_all=frozenset({"under", "construction"}),
            forbid=(uc_forbid - {"subject"}) | frozenset({"segment"}), as_of_year=as_of,
        )
    )
    if uc is not None:
        uc_rooms: int | None = uc[0]
        inputs.append(uc[2])
    elif "under_construction" in sums:
        uc_rooms, refs = sums["under_construction"]
        inputs.extend(refs)
    else:
        uc_rooms = None

    fp = _pick_rooms(
        rows, prefixes=("market_study.", "property_overview.", UNDER_CONSTRUCTION_PREFIX),
        require_all=frozenset({"final", "planning"}), forbid=NOT_SUBMARKET_TOKENS, as_of_year=as_of,
    )
    if fp is not None:
        fp_rooms: int | None = fp[0]
        inputs.append(fp[2])
    elif "final_planning" in sums:
        fp_rooms, refs = sums["final_planning"]
        inputs.extend(refs)
    else:
        fp_rooms = None

    planned = _pick_rooms(
        [r for r in rows if set(_path_tokens(r.field_name)) & PLANNED_TOKENS],
        prefixes=("market_study.", "property_overview."), require_any=ROOMS_TOKENS,
        forbid=NOT_SUBMARKET_TOKENS | frozenset({"under", "construction", "final"}), as_of_year=as_of,
    )
    if planned is not None:
        planned_rooms: int | None = planned[0]
        inputs.append(planned[2])
    elif "planned" in sums:
        planned_rooms, refs = sums["planned"]
        inputs.extend(refs)
    else:
        planned_rooms = None

    def share(n: int | None) -> float | None:
        if n is None or not existing_rooms or existing_rooms <= 0:
            return None
        return round(n / existing_rooms * 100.0, 4)

    uc_share = _pick_reported_share(rows, frozenset({"under", "construction"}))
    if uc_share is not None:
        uc_pct: float | None = uc_share[0]
        uc_basis: ShareBasis | None = "reported"
        inputs.append(uc_share[1])
    else:
        uc_pct = share(uc_rooms)
        uc_basis = "computed" if uc_pct is not None else None

    fp_share = _pick_reported_share(rows, frozenset({"final", "planning"}))
    if fp_share is not None:
        fp_pct: float | None = fp_share[0]
        fp_basis: ShareBasis | None = "reported"
        inputs.append(fp_share[1])
    else:
        fp_pct = share(fp_rooms)
        fp_basis = "computed" if fp_pct is not None else None

    actual, forecast, level = _pick_reported_growth(rows, "supply", as_of_year=as_of)
    if actual:
        inputs.append(actual.ref)
    if forecast:
        inputs.append(forecast.ref)

    reason: str | None = None
    details: list[str] = []
    if uc_pct is None:
        reason = REASON_NO_SOURCE
        missing = []
        if uc_rooms is None:
            missing.append("under-construction rooms")
        if not existing_rooms:
            missing.append("existing submarket inventory")
        details.append(f"{' and '.join(missing) or 'Supply pipeline'} not in the uploaded reports.")
    if pfilter is not None and pfilter.note:
        details.append(pfilter.note)

    return SupplyReading(
        existing_rooms=existing_rooms,
        existing_period_label=existing_label,
        under_construction_rooms=uc_rooms,
        final_planning_rooms=fp_rooms,
        planned_rooms=planned_rooms,
        under_construction_pct=uc_pct,
        final_planning_pct=fp_pct,
        reported_supply_change_pct=round(actual.value, 4) if actual else None,
        reported_supply_change_period=actual.label if actual else None,
        inputs=inputs,
        reason=reason,
        detail=" ".join(details) if details else None,
        under_construction_pct_basis=uc_basis,
        final_planning_pct_basis=fp_basis,
        forecast_supply_change_pct=round(forecast.value, 4) if forecast else None,
        forecast_supply_change_period=forecast.label if forecast else None,
        pipeline_hotels=hotels,
        pipeline_filter=pfilter,
        supply_rooms_change=level.ref if level else None,
        supply_rooms_change_period=level.label if level else None,
    )


def market_study_rows(rows: Sequence[FieldRow]) -> list[FieldRow]:
    """Only the rows that belong to MARKET_STUDY documents."""
    return [r for r in rows if (r.doc_type or "") == "MARKET_STUDY"]


__all__ = [
    "GROWTH_PREFIXES",
    "MARKET_STUDY_PREFIXES",
    "PIPELINE_EXPORT_PREFIX",
    "RATE_FRACTION_MAX",
    "RATE_PERCENT_MAX",
    "REASON_NO_DOCUMENT",
    "REASON_NO_SOURCE",
    "GrowthReading",
    "PipelineFilter",
    "PipelineHotel",
    "SupplyReading",
    "market_study_rows",
    "market_terms_from_rows",
    "multi_market_export_docs",
    "read_demand_growth",
    "read_supply_growth",
]
