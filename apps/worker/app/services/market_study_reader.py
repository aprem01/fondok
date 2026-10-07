"""Demand growth + supply growth from MARKET_STUDY extractions (FON-61 E-008).

Two CoStar submarket reports uploaded under "STR / Comp Set" were routed to
the MARKET_STUDY lane and extracted fine (hundreds of fields in the
``market_study`` / ``under_construction`` / ``cbre_horizons`` groups), but
nothing read them: the Market tab's Demand Growth and Supply Growth tiles
only knew how to say "awaiting CoStar submarket report".

This reader turns those rows into the two tiles, with provenance that
names the document and the field for every input:

* **Demand growth** — the report's demand (occupied room nights) growth.
  Preferred: a reported growth figure (``market_study.trend.ttm.
  demand_change_pct``, ``market_study.trend.<YYYY>.demand_change_pct``,
  or — because the generic extraction prompt steers CoStar trend tables
  onto the CBRE paths — ``cbre_horizons.segment_all.<YYYY>.demand_change_pct``
  inside the MARKET_STUDY document). Fallback: two years of the demand
  room-night series → ``latest ÷ prior - 1``. TTM beats the latest actual
  year beats an undated figure; forecast-tagged years are used only when
  no actual exists, and are labelled as forecast.

* **Supply growth** — pipeline rooms over existing inventory, reported
  separately for under construction and final planning:
  ``under_construction_rooms ÷ existing_rooms`` and
  ``final_planning_rooms ÷ existing_rooms``. Existing inventory is the
  report's submarket room count (``market_study.supply.existing_rooms`` or
  the latest actual year of the supply series); under-construction rooms
  are the report's total (``under_construction.total_rooms``) or the sum of
  the listed ``under_construction.<n>.rooms`` projects not tagged as
  planning. A reported supply change (``…supply_change_pct``) is surfaced
  alongside, never substituted for the pipeline ratio.

Field-name matching is by canonical path first and then by the tokens of
the path (``demand`` + ``growth``/``change``…), because the LLM named
these groups itself before ``market_study.md`` existed. Every match is
returned as a ``FieldRef`` so the analyst can see exactly which row was
read. When a required series is absent the reading carries the reason
code the rest of the app uses — ``no_document`` (no MARKET_STUDY on the
deal) or ``no_source`` (reports present, series not in them) — and a
one-line detail.

Pure functions — no DB, no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

from .market_fields import FieldRef, FieldRow, coerce_float, coerce_int, pct_points

REASON_NO_DOCUMENT = "no_document"
REASON_NO_SOURCE = "no_source"

# Namespaces read INSIDE a MARKET_STUDY document. ``cbre_horizons.`` is here
# because the generic extraction prompt tells the LLM to use the closest
# canonical prefix, and CoStar's supply/demand trend table maps onto the
# CBRE ``segment_<scope>.<YYYY>.{supply,demand}_change_pct`` shape.
MARKET_STUDY_PREFIXES = ("market_study.", "under_construction.", "cbre_horizons.")
UNDER_CONSTRUCTION_PREFIX = "under_construction."

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_SPLIT_RE = re.compile(r"[._\-\s/]+")

GROWTH_TOKENS = frozenset({"growth", "change", "chg", "yoy", "delta", "var", "variance"})
FORECAST_TOKENS = frozenset(
    {"forecast", "forecasted", "forecasts", "projected", "projection", "projections", "proj", "outlook", "f"}
)
TTM_TOKENS = frozenset({"ttm", "t12", "trailing", "trailing12", "12mo", "12m", "l12m", "last12", "current"})
PIPELINE_TOKENS = frozenset(
    {
        "under", "construction", "uc", "planning", "planned", "proposed", "pipeline",
        "delivered", "deliveries", "delivery", "openings", "opening", "prospective",
        "deferred", "abandoned", "unentitled", "entitled",
    }
)
ROOMS_TOKENS = frozenset({"rooms", "keys", "units", "supply", "inventory"})
EXISTING_TOKENS = frozenset({"existing", "inventory", "total", "current"})
NOT_SUBMARKET_TOKENS = frozenset(
    {"subject", "compset", "comp", "competitive", "property", "properties", "hotels", "share", "pct", "percent"}
)
FINAL_PLANNING_TOKENS = frozenset({"final"})
PLANNED_TOKENS = frozenset({"planned", "proposed", "unentitled", "prospective", "early"})

Period = tuple[int, int | None]  # (rank, year)


def _tokens(name: str) -> list[str]:
    return [t for t in _SPLIT_RE.split(name.lower()) if t]


def _year_of(tokens: list[str]) -> int | None:
    for t in tokens:
        if _YEAR_RE.match(t):
            return int(t)
    return None


def _in_market_study_namespace(row: FieldRow) -> bool:
    return row.lname.startswith(MARKET_STUDY_PREFIXES)


def _period_tags(rows: list[FieldRow]) -> dict[str, str]:
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


@dataclass(frozen=True)
class GrowthReading:
    value_pct: float | None
    period_label: str | None
    basis: Literal["reported", "derived_from_series"] | None
    inputs: list[FieldRef]
    reason: str | None
    detail: str | None


@dataclass(frozen=True)
class SupplyReading:
    existing_rooms: int | None
    existing_period_label: str | None
    under_construction_rooms: int | None
    final_planning_rooms: int | None
    planned_rooms: int | None
    #: under_construction_rooms ÷ existing_rooms, percent points.
    under_construction_pct: float | None
    #: final_planning_rooms ÷ existing_rooms, percent points.
    final_planning_pct: float | None
    reported_supply_change_pct: float | None
    reported_supply_change_period: str | None
    inputs: list[FieldRef]
    reason: str | None
    detail: str | None


def _rank(is_ttm: bool, year: int | None, is_forecast: bool) -> tuple[int, int]:
    """Lower sorts first: TTM actual, latest actual year, undated, earliest forecast."""
    if is_forecast:
        return (3, year or 9999)
    if is_ttm:
        return (0, 0)
    if year is not None:
        return (1, -year)
    return (2, 0)


def _label(is_ttm: bool, year: int | None, is_forecast: bool) -> str:
    if is_ttm:
        return "TTM"
    if year is not None:
        return f"{year} forecast" if is_forecast else str(year)
    return "forecast" if is_forecast else "as reported"


def _pick_reported_growth(
    rows: list[FieldRow], subject: str, *, as_of_year: int
) -> tuple[float, str, FieldRef] | None:
    tags = _period_tags(rows)
    best: tuple[tuple[int, int], int, float, str, FieldRef] | None = None
    for order, r in enumerate(rows):
        if not _in_market_study_namespace(r):
            continue
        toks = _tokens(r.field_name)
        tokset = set(toks)
        if subject not in tokset or not (tokset & GROWTH_TOKENS):
            continue
        if tokset & NOT_SUBMARKET_TOKENS - {"pct", "percent"}:
            continue
        v = pct_points(r.value)
        if v is None:
            continue
        is_ttm = bool(tokset & TTM_TOKENS)
        year = _year_of(toks)
        fc = _is_forecast(r, toks, tags, as_of_year)
        key = (_rank(is_ttm, year, fc), order)
        if best is None or key < (best[0], best[1]):
            best = (key[0], order, v, _label(is_ttm, year, fc), FieldRef.of(r))
    if best is None:
        return None
    return best[2], best[3], best[4]


def _pick_year_series(
    rows: list[FieldRow], subject: str, *, as_of_year: int, forbid: frozenset[str]
) -> dict[int, tuple[float, FieldRef]]:
    """``{year: (value, ref)}`` of a level (not growth) series — actual years only."""
    tags = _period_tags(rows)
    out: dict[int, tuple[float, FieldRef]] = {}
    for r in rows:
        if not _in_market_study_namespace(r):
            continue
        toks = _tokens(r.field_name)
        tokset = set(toks)
        if subject not in tokset or (tokset & GROWTH_TOKENS) or (tokset & forbid):
            continue
        year = _year_of(toks)
        if year is None or _is_forecast(r, toks, tags, as_of_year):
            continue
        if r.unit in {"pct", "percent", "%", "ratio"}:
            continue
        v = coerce_float(r.value)
        if v is None or v <= 0:
            continue
        out.setdefault(year, (v, FieldRef.of(r)))
    return out


def read_demand_growth(
    rows: list[FieldRow], *, has_documents: bool, as_of_year: int | None = None
) -> GrowthReading:
    as_of = as_of_year or date.today().year
    if not has_documents:
        return GrowthReading(
            value_pct=None, period_label=None, basis=None, inputs=[],
            reason=REASON_NO_DOCUMENT,
            detail="No market study / CoStar submarket report is on the deal.",
        )
    reported = _pick_reported_growth(rows, "demand", as_of_year=as_of)
    if reported is not None:
        value, label, ref = reported
        return GrowthReading(
            value_pct=round(value, 4), period_label=label, basis="reported",
            inputs=[ref], reason=None, detail=None,
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
        )
    return GrowthReading(
        value_pct=None, period_label=None, basis=None, inputs=[],
        reason=REASON_NO_SOURCE,
        detail="Demand growth (or a two-year demand series) is not in the uploaded reports.",
    )


def _pick_rooms(
    rows: list[FieldRow],
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
    period label is that year).
    """
    tags = _period_tags(rows)
    dated: dict[int, tuple[int, FieldRef]] = {}
    for r in rows:
        if not r.lname.startswith(prefixes):
            continue
        toks = _tokens(r.field_name)
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
        v = coerce_int(r.value)
        if v is None or v < 0:
            continue
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
    rows: list[FieldRow], *, as_of_year: int
) -> tuple[int, str | None, FieldRef] | None:
    """The submarket's existing inventory.

    1. An undated figure whose path says existing / inventory / total /
       current rooms (``market_study.supply.existing_rooms``, …).
    2. Otherwise the latest actual year of the supply series
       (``market_study.trend.<YYYY>.supply_rooms``).
    Pipeline, subject-hotel and comp-set room counts never qualify.
    """
    forbid = PIPELINE_TOKENS | NOT_SUBMARKET_TOKENS
    undated = _pick_rooms(
        rows, prefixes=("market_study.",), require_any=ROOMS_TOKENS, forbid=forbid,
        year_mode="undated", as_of_year=as_of_year,
    )
    while undated is not None and not (set(_tokens(undated[2].field_name)) & EXISTING_TOKENS):
        # A bare undated "rooms" figure that doesn't say what it is — skip
        # it and look for the next explicit one.
        rows = [r for r in rows if r.field_name != undated[2].field_name or r.doc_id != undated[2].doc_id]
        undated = _pick_rooms(
            rows, prefixes=("market_study.",), require_any=ROOMS_TOKENS, forbid=forbid,
            year_mode="undated", as_of_year=as_of_year,
        )
    if undated is not None:
        return undated
    return _pick_rooms(
        rows, prefixes=("market_study.",), require_any=ROOMS_TOKENS, forbid=forbid,
        year_mode="dated", as_of_year=as_of_year,
    )


def _pipeline_rows_by_status(rows: list[FieldRow]) -> dict[str, tuple[int, list[FieldRef]]]:
    """Sum ``under_construction.<n>.rooms`` by the row's status sibling.

    Buckets: ``under_construction`` (no status, or a status that says so),
    ``final_planning`` (status mentions final / planning) and ``planned``
    (status mentions planned / proposed / prospective / early).
    """
    status_by_row: dict[str, str] = {}
    rooms_by_row: dict[str, tuple[int, FieldRef]] = {}
    for r in rows:
        if not r.lname.startswith(UNDER_CONSTRUCTION_PREFIX):
            continue
        rest = r.lname[len(UNDER_CONSTRUCTION_PREFIX):]
        parts = rest.split(".")
        if len(parts) < 2:
            continue
        row_id, attr = ".".join(parts[:-1]), parts[-1]
        if row_id in {"total", "totals", "summary"}:
            continue
        if attr in {"status", "stage", "phase", "pipeline_status"} and isinstance(r.value, str):
            status_by_row.setdefault(row_id, r.value.strip().lower())
        elif attr in {"rooms", "keys", "units", "room_count", "key_count"}:
            v = coerce_int(r.value)
            if v is not None and v > 0:
                rooms_by_row.setdefault(row_id, (v, FieldRef.of(r)))
    out: dict[str, tuple[int, list[FieldRef]]] = {}
    for row_id, (v, ref) in rooms_by_row.items():
        status = status_by_row.get(row_id, "")
        stoks = set(_tokens(status))
        if stoks & FINAL_PLANNING_TOKENS or ("planning" in stoks and "under" not in stoks):
            bucket = "final_planning"
        elif stoks & PLANNED_TOKENS:
            bucket = "planned"
        else:
            bucket = "under_construction"
        total, refs = out.get(bucket, (0, []))
        out[bucket] = (total + v, [*refs, ref])
    return out


def read_supply_growth(
    rows: list[FieldRow], *, has_documents: bool, as_of_year: int | None = None
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

    existing = _pick_existing_rooms(rows, as_of_year=as_of)
    existing_rooms = existing[0] if existing else None
    existing_label = existing[1] if existing else None
    if existing:
        inputs.append(existing[2])

    uc = _pick_rooms(
        rows, prefixes=(UNDER_CONSTRUCTION_PREFIX,), require_all=frozenset({"total"}),
        as_of_year=as_of,
    ) or _pick_rooms(
        rows, prefixes=("market_study.",), require_all=frozenset({"under", "construction"}),
        forbid=NOT_SUBMARKET_TOKENS | FINAL_PLANNING_TOKENS | PLANNED_TOKENS, as_of_year=as_of,
    )
    by_status = _pipeline_rows_by_status(rows)
    if uc is not None:
        uc_rooms: int | None = uc[0]
        inputs.append(uc[2])
    elif "under_construction" in by_status:
        uc_rooms, refs = by_status["under_construction"]
        inputs.extend(refs)
    else:
        uc_rooms = None

    fp = _pick_rooms(
        rows, prefixes=("market_study.", UNDER_CONSTRUCTION_PREFIX),
        require_all=frozenset({"final", "planning"}), forbid=NOT_SUBMARKET_TOKENS, as_of_year=as_of,
    )
    if fp is not None:
        fp_rooms: int | None = fp[0]
        inputs.append(fp[2])
    elif "final_planning" in by_status:
        fp_rooms, refs = by_status["final_planning"]
        inputs.extend(refs)
    else:
        fp_rooms = None

    planned = _pick_rooms(
        [r for r in rows if set(_tokens(r.field_name)) & PLANNED_TOKENS],
        prefixes=("market_study.",), require_any=ROOMS_TOKENS,
        forbid=NOT_SUBMARKET_TOKENS | frozenset({"under", "construction", "final"}), as_of_year=as_of,
    )
    if planned is not None:
        planned_rooms: int | None = planned[0]
        inputs.append(planned[2])
    elif "planned" in by_status:
        planned_rooms, refs = by_status["planned"]
        inputs.extend(refs)
    else:
        planned_rooms = None

    reported = _pick_reported_growth(rows, "supply", as_of_year=as_of)
    reported_pct = round(reported[0], 4) if reported else None
    reported_period = reported[1] if reported else None
    if reported:
        inputs.append(reported[2])

    def share(n: int | None) -> float | None:
        if n is None or not existing_rooms or existing_rooms <= 0:
            return None
        return round(n / existing_rooms * 100.0, 4)

    uc_pct = share(uc_rooms)
    fp_pct = share(fp_rooms)

    reason: str | None = None
    detail: str | None = None
    if uc_pct is None:
        reason = REASON_NO_SOURCE
        missing = []
        if uc_rooms is None:
            missing.append("under-construction rooms")
        if not existing_rooms:
            missing.append("existing submarket inventory")
        detail = f"{' and '.join(missing) or 'Supply pipeline'} not in the uploaded reports."

    return SupplyReading(
        existing_rooms=existing_rooms,
        existing_period_label=existing_label,
        under_construction_rooms=uc_rooms,
        final_planning_rooms=fp_rooms,
        planned_rooms=planned_rooms,
        under_construction_pct=uc_pct,
        final_planning_pct=fp_pct,
        reported_supply_change_pct=reported_pct,
        reported_supply_change_period=reported_period,
        inputs=inputs,
        reason=reason,
        detail=detail,
    )


def market_study_rows(rows: list[FieldRow]) -> list[FieldRow]:
    """Only the rows that belong to MARKET_STUDY documents."""
    return [r for r in rows if (r.doc_type or "") == "MARKET_STUDY"]


__all__ = [
    "MARKET_STUDY_PREFIXES",
    "REASON_NO_DOCUMENT",
    "REASON_NO_SOURCE",
    "GrowthReading",
    "SupplyReading",
    "market_study_rows",
    "read_demand_growth",
    "read_supply_growth",
]
