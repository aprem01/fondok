"""Index Analysis methodology — which benchmark the subject is indexed against,
and the market-growth / penetration assumptions that ride on it (FON-61 / FON-41
E-028).

Three methods, chosen by the analyst and persisted as the ``index_methodology``
field override:

* ``str_comp_set`` (default — today's behaviour): the STR comp-set blend,
  recovered from the subject's STR trailing-twelve-month figures and the
  published penetration indices (comp = subject ÷ MPI / ARI). Read through
  ``market_comp_set.derive_ttm_blend`` — the SAME derivation Market Overview
  shows, so the two never disagree.
* ``market_benchmark``: the broader market / chain-scale segment figures the
  deal's CoStar / STR market reports already carry —
  ``ttm_performance.segment.<scale>.(occupancy|adr)*`` and
  ``pnl_benchmark.market.(occupancy|adr)*`` (plus the ``ttm_performance.market``
  / ``market_study.market`` spellings of the same thing).
* ``costar_comp_set``: a CoStar Property Analytics comp set —
  ``costar_comp_set.(occupancy|adr)*`` rows. When no such extraction exists the
  method is DISABLED and says why; it is never filled from another source.

Four editable assumptions (field overrides, engine input):

* ``index_market_occupancy_growth`` / ``index_market_adr_growth`` — the market's
  annual occupancy / ADR growth (fractions, 0.03 = 3%).
* ``index_mpi_target`` / ``index_ari_target`` — the subject's occupancy / ADR
  penetration against the selected benchmark (index points, 100 = parity).

They reach the revenue engine ONLY through the existing STR-basis path: when
the analyst has turned on "Use STR rates in the model"
(``revenue_seed_from_str_forecast``) AND that seed actually landed. Then

    starting_occupancy = benchmark occupancy x MPI target / 100
    starting_adr       = benchmark ADR       x ARI target / 100
    occupancy_growth   = market occupancy growth
    adr_growth         = market ADR growth

each only when the analyst set that override, and never over an explicit
analyst override of the target engine key. With the toggle off (the default,
and every golden deal) nothing here touches engine input.

Defaults shown next to an un-overridden assumption come from the documents
(field + document + page) or are computed from two document figures (inputs
listed). An assumption with no document source is ``None`` with a reason —
never a placeholder number.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .market_comp_set import build_str_inputs, derive_ttm_blend
from .market_fields import (
    FieldRef,
    FieldRow,
    coerce_float,
    coerce_int,
    extraction_lane,
    parse_extraction_records,
)

METHOD_STR_COMP_SET = "str_comp_set"
METHOD_MARKET_BENCHMARK = "market_benchmark"
METHOD_COSTAR_COMP_SET = "costar_comp_set"
METHODS: tuple[str, ...] = (METHOD_STR_COMP_SET, METHOD_MARKET_BENCHMARK, METHOD_COSTAR_COMP_SET)
DEFAULT_METHOD = METHOD_STR_COMP_SET

METHOD_LABELS: dict[str, str] = {
    METHOD_STR_COMP_SET: "STR competitive set",
    METHOD_MARKET_BENCHMARK: "Market / chain-scale benchmark",
    METHOD_COSTAR_COMP_SET: "CoStar Property Analytics comp set",
}

#: The persisted method choice (a METHOD choice, not a number — note-exempt).
OVERRIDE_METHOD = "index_methodology"
#: Which chain-scale segment the market benchmark reads (note-exempt choice).
OVERRIDE_SEGMENT = "index_market_segment"
KEY_OCC_GROWTH = "index_market_occupancy_growth"
KEY_ADR_GROWTH = "index_market_adr_growth"
KEY_MPI_TARGET = "index_mpi_target"
KEY_ARI_TARGET = "index_ari_target"
ASSUMPTION_KEYS: tuple[str, ...] = (KEY_OCC_GROWTH, KEY_ADR_GROWTH, KEY_MPI_TARGET, KEY_ARI_TARGET)
#: FON-95 part 2 — penetration targets BY MODEL YEAR (JSON lists; index y-1 =
#: model year y; null = not set for that year). Where set they beat the scalar
#: ``index_mpi_target`` / ``index_ari_target``, which remain the all-years
#: shorthand. RGI is derived (MPI × ARI ÷ 100), never an input.
KEY_MPI_TARGET_BY_YEAR = "index_mpi_target_by_year"
KEY_ARI_TARGET_BY_YEAR = "index_ari_target_by_year"
BY_YEAR_TARGET_KEYS: tuple[str, ...] = (KEY_MPI_TARGET_BY_YEAR, KEY_ARI_TARGET_BY_YEAR)
#: The model-year cap the by-year lists are read to (``hold_years`` ≤ 20).
MAX_MODEL_YEARS = 20

#: CBRE Horizons rows the market benchmark's forecast years are read from:
#: ``cbre_horizons.segment_<scope>.<YYYY>.(occupancy_pct|adr_usd|period)`` and
#: the legacy ``cbre_horizons.year_<n>.(occupancy_pct|adr_usd|calendar_year)``.
_CBRE_SEGMENT_YEAR_RE = re.compile(
    r"^cbre_horizons\.segment_(?P<scope>[a-z0-9_]+)\.(?P<year>(?:19|20)\d{2})\."
    r"(?P<attr>occupancy_pct|adr_usd|period)$"
)
_CBRE_LEGACY_YEAR_RE = re.compile(
    r"^cbre_horizons\.year_(?P<n>\d{1,2})\.(?P<attr>occupancy_pct|adr_usd|calendar_year)$"
)

#: The source id stamped on an engine key one of these assumptions moved.
SOURCE_INDEX_ASSUMPTION = "index_assumption"

#: ``ignored_override`` — the analyst stored a value that is NOT applied
#: (a legacy out-of-range MPI / ARI target); ``detail`` says why. It is
#: never an active override and never a document default.
FigureSource = Literal["document", "computed", "override", "ignored_override"]

# Market-level and segment-level occupancy / ADR rows. ``period`` is ``ttm``,
# a 4-digit year (optionally ``_annual``) or absent (undated). Forecast rows
# never match: a benchmark LEVEL is an actual.
_METRIC = r"(?P<metric>occupancy|occ|adr)(?:_(?:pct|usd))?(?:_(?P<period>ttm|\d{4})(?:_annual)?)?"
_MARKET_RE = re.compile(
    r"^(?:pnl_benchmark\.market|ttm_performance\.market|market_study\.market)\." + _METRIC + r"$"
)
_SEGMENT_RE = re.compile(r"^ttm_performance\.segment\.(?P<segment>[a-z0-9_]+)\." + _METRIC + r"$")
_COSTAR_RE = re.compile(r"^costar_comp_set\." + _METRIC + r"$")
COSTAR_PREFIX = "costar_comp_set."

# Growth defaults — the market's forward occupancy / ADR change.
_GROWTH_MARKET_RE = re.compile(
    r"^pnl_benchmark\.market\.(?P<metric>occupancy|adr)_change_(?P<year>\d{4})_forecast$"
)
_GROWTH_CBRE_RE = re.compile(
    r"^cbre_horizons\.segment_all\.(?P<year>\d{4})\.(?P<metric>occupancy|adr)_change_pct$"
)
_GROWTH_CBRE_LONG_RUN_RE = re.compile(r"^cbre_horizons\.long_run_avg\.(?P<metric>occupancy|adr)_change_pct$")


# ───────────────────────────── value objects ─────────────────────────────

BenchmarkBasis = Literal["actual", "forecast", "assumption"]

#: Sam #6 — the comp-set out-years are the reported level grown at the market
#: growth assumption (STR publishes no comp-set forecast).
ASSUMPTION_GROWN_LABEL = "Assumption · grown at market growth"


@dataclass(frozen=True)
class Figure:
    """One displayed number and where it came from.

    ``source`` is ``document`` (one extraction row), ``computed`` (a formula of
    the ``inputs`` rows), ``override`` (the analyst's own value; ``detail`` is
    their note), ``ignored_override`` (a stored analyst value the model does
    NOT apply — no value; ``detail`` says why) or ``None`` (no source —
    ``detail`` says why).
    """

    value: float | None
    source: FigureSource | None
    inputs: list[FieldRef] = field(default_factory=list)
    detail: str | None = None
    period_label: str | None = None
    #: Sam #6 (2026-10-15) — WHAT KIND of number a benchmark figure is, so an
    #: analyst never reads a projected benchmark as history: ``actual`` (a
    #: reported trailing-twelve-month / calendar-year figure), ``forecast`` (a
    #: CBRE Horizons projection) or ``assumption`` (a reported level grown at
    #: the market growth assumption). ``basis_label`` is the one-line label the
    #: web prints beside the value ("Actual · STR TTM May 2025 · <doc>",
    #: "Forecast · CBRE Horizons 2027 · <doc> p.4", "Assumption · grown at
    #: market growth"). Display only — never read by an engine.
    basis: BenchmarkBasis | None = None
    basis_label: str | None = None
    #: The reporting period end of an ``actual`` (ISO ``YYYY-MM`` / date / year).
    period_end: str | None = None
    #: The publication / report date of the document a ``forecast`` came from.
    report_date: str | None = None

    @classmethod
    def none(cls, detail: str) -> Figure:
        return cls(value=None, source=None, detail=detail)


@dataclass(frozen=True)
class MethodReading:
    method: str
    label: str
    available: bool
    disabled_reason: str | None
    occupancy: Figure  # fraction 0..1
    adr: Figure  # USD
    documents: list[str] = field(default_factory=list)
    #: market_benchmark only — the segment read and the segments on offer.
    segment: str | None = None
    segments_available: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IndexMethodologyReading:
    selected: str
    selected_source: Literal["override", "default"]
    methods: list[MethodReading]
    subject_occupancy: Figure  # STR subject TTM, fraction
    subject_adr: Figure
    subject_period_label: str | None
    assumptions: dict[str, Figure]
    toggle_on: bool
    #: Sam #6 — the STR TTM reporting period end (ISO) and the CBRE Horizons
    #: report date the by-year benchmark labels name. None = not reported.
    subject_period_end: str | None = None
    cbre_report_date: str | None = None

    def method(self, name: str) -> MethodReading | None:
        return next((m for m in self.methods if m.method == name), None)


# ───────────────────────────── small helpers ─────────────────────────────


def _occ_fraction(v: float | None) -> float | None:
    if v is None or v <= 0:
        return None
    return v / 100.0 if v > 1.5 else v


def _valid_occupancy(v: float) -> float | None:
    """A Year-1 occupancy the engine can run on — refused (None) outside (0, 1)."""
    return v if 0 < v < 1 else None


def _valid_adr(v: float) -> float | None:
    return v if v > 0 else None


#: Accepted penetration-target ranges, after reading the analyst's number as
#: either index points (100 = parity) or a ratio (1.0 = parity). The two
#: bands do not overlap, so a value tells us its own unit; anything between
#: them ("5", "12") or beyond is ambiguous or implausible and is REFUSED —
#: never read as 5 index points and applied to the model.
INDEX_POINTS_RANGE: tuple[float, float] = (25.0, 250.0)
INDEX_RATIO_RANGE: tuple[float, float] = (0.25, 2.5)


def validate_index_target(raw: Any) -> tuple[float | None, str | None]:
    """One penetration target as typed → ``(index points, None)``, or
    ``(None, reason)`` when it cannot be applied.

    Shared by the Excel apply (``api.pl_roundtrip``), the PATCH override
    gate (``api.deals.update_deal``) and ``coerce_target_list`` so every
    path refuses the same values for the same reason.
    """
    f = coerce_float(raw)
    if f is None:
        return None, "not a number"
    lo_r, hi_r = INDEX_RATIO_RANGE
    lo_p, hi_p = INDEX_POINTS_RANGE
    if lo_r <= f <= hi_r:
        return f * 100.0, None
    if lo_p <= f <= hi_p:
        return f, None
    return None, (
        f"{f:g} is outside the accepted range — index points "
        f"{lo_p:g}–{hi_p:g} (100 = parity with the benchmark) or a ratio "
        f"{lo_r:g}–{hi_r:g} (1.0 = parity)"
    )


def _index_points(v: float | None) -> float | None:
    """STR indices arrive as a ratio (1.032) or index points (103.2);
    out-of-range values read as unset (see ``validate_index_target``)."""
    return validate_index_target(v)[0]


#: The display labels a stored-target problem names.
_TARGET_LABELS: dict[str, str] = {
    "index_mpi_target": "all-years MPI target",
    "index_ari_target": "all-years ARI target",
    "index_mpi_target_by_year": "MPI target",
    "index_ari_target_by_year": "ARI target",
}


def stored_target_problem(raw: Any, key: str, *, year: int | None = None) -> str | None:
    """Why a STORED penetration target is ignored at read time, or ``None``
    when it is valid (or unset).

    The range check gates new writes (PATCH / Excel apply), but a legacy row
    written before it can still carry ``5`` or ``1000``. Every read that feeds
    the engine or the by-year grid goes through ``validate_index_target``; an
    out-of-range value is IGNORED — never applied — and this line is surfaced
    so the analyst knows to re-enter it.
    """
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if validate_index_target(raw)[0] is not None:
        return None
    lo_p, hi_p = INDEX_POINTS_RANGE
    lo_r, hi_r = INDEX_RATIO_RANGE
    f = coerce_float(raw)
    shown = f"{f:g}" if f is not None else repr(raw)
    label = _TARGET_LABELS.get(key, "target")
    where = f"Year-{year} " if year is not None else ""
    return (
        f"Stored {where}{label} {shown} is outside {lo_p:g}–{hi_p:g} index points "
        f"(or {lo_r:g}–{hi_r:g} as a ratio) — re-enter it. It is ignored, not applied."
    )


def _growth_fraction(v: Any) -> float | None:
    f = coerce_float(v)
    if f is None:
        return None
    frac = f if abs(f) <= 1.0 else f / 100.0
    return frac if abs(frac) <= 1.5 else None


def override_scalar(overrides: Mapping[str, Any] | None, key: str) -> Any:
    """The scalar behind a ``field_overrides`` entry (``{value, note}`` or bare)."""
    if not overrides or key not in overrides:
        return None
    raw = overrides[key]
    return raw.get("value") if isinstance(raw, dict) else raw


def override_note(overrides: Mapping[str, Any] | None, key: str) -> str | None:
    raw = (overrides or {}).get(key)
    if isinstance(raw, dict) and isinstance(raw.get("note"), str) and raw["note"].strip():
        return raw["note"].strip()
    return None


def toggle_is_on(overrides: Mapping[str, Any] | None) -> bool:
    return override_scalar(overrides, "revenue_seed_from_str_forecast") is True


def selected_method(overrides: Mapping[str, Any] | None) -> tuple[str, Literal["override", "default"]]:
    raw = override_scalar(overrides, OVERRIDE_METHOD)
    if isinstance(raw, str) and raw.strip() in METHODS:
        return raw.strip(), "override"
    return DEFAULT_METHOD, "default"


def _slug(text_value: Any) -> str | None:
    if not isinstance(text_value, str) or not text_value.strip():
        return None
    return re.sub(r"[^a-z0-9]+", "_", text_value.strip().lower()).strip("_") or None


def _period_rank(period: str | None) -> tuple[int, int]:
    """TTM first, then undated, then the latest year."""
    if period == "ttm":
        return (0, 0)
    if period is None:
        return (1, 0)
    return (2, -int(period))


def _period_label(period: str | None) -> str | None:
    if period == "ttm":
        return "TTM"
    return period


_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def period_end_text(iso: str | None) -> str | None:
    """``2025-05`` / ``2025-05-31`` → ``May 2025``; ``2025`` → ``2025``; else None."""
    if not iso:
        return None
    m = re.match(r"^((?:19|20)\d{2})(?:-(\d{2}))?", str(iso).strip())
    if not m:
        return None
    if m.group(2):
        month = int(m.group(2))
        if 1 <= month <= 12:
            return f"{_MONTH_NAMES[month - 1]} {m.group(1)}"
    return m.group(1)


def _family(doc_type: str | None) -> str:
    t = (doc_type or "").upper()
    if t in {"STR", "STR_TREND"}:
        return "STR"
    if t == "COSTAR":
        return "CoStar"
    if t == "CBRE_HORIZONS":
        return "CBRE Horizons"
    if t == "MARKET_STUDY":
        return "Market study"
    return "Market report"


def actual_label(source: str, period: str | None, doc: str | None) -> str:
    """``Actual · STR TTM May 2025 · <doc>`` — the Year-1 benchmark label."""
    head = f"Actual · {source}{f' {period}' if period else ''}"
    return f"{head} · {doc}" if doc else head


def forecast_label(calendar_year: int, doc: str | None, page: int | None, *, kind: str = "forecast") -> str:
    """``Forecast · CBRE Horizons 2027 · <doc> p.4`` — a CBRE year's label
    (``Actual · …`` when the report tags that year as an actual)."""
    head = f"{'Actual' if kind == 'actual' else 'Forecast'} · CBRE Horizons {calendar_year}"
    if not doc:
        return head
    return f"{head} · {doc}{f' p.{page}' if page else ''}"


def read_cbre_report_date(rows: Sequence[FieldRow]) -> str | None:
    """The newest CBRE Horizons report's publication date as the extraction
    states it (``cbre_horizons.publication_date``), else that document's
    ``report_as_of``. None when no CBRE report dates itself."""
    for r in rows:
        if r.lname == "cbre_horizons.publication_date" and isinstance(r.value, str) and r.value.strip():
            return r.value.strip()
    for r in rows:
        if r.lname.startswith("cbre_horizons.") and r.report_as_of:
            return r.report_as_of
    return None


def _pick_metric(
    rows: Iterable[tuple[FieldRow, re.Match[str]]], metric: str
) -> tuple[FieldRow, float, str | None] | None:
    """Best row for ``occupancy`` / ``adr`` — TTM, then undated, then latest year.
    Within a rank, the first row (newest extraction) wins."""
    best: tuple[tuple[int, int], FieldRow, float, str | None] | None = None
    for row, m in rows:
        mm = m.group("metric")
        kind = "occupancy" if mm in ("occupancy", "occ") else "adr"
        if kind != metric:
            continue
        if row.period and row.period.startswith("forecast"):
            continue
        v = coerce_float(row.value)
        if metric == "occupancy":
            v = _occ_fraction(v)
        elif v is not None and v <= 0:
            v = None
        if v is None:
            continue
        rank = _period_rank(m.group("period"))
        if best is None or rank < best[0]:
            best = (rank, row, v, m.group("period"))
    return None if best is None else (best[1], best[2], best[3])


def _figure_from(pick: tuple[FieldRow, float, str | None] | None, missing: str) -> Figure:
    if pick is None:
        return Figure.none(missing)
    row, v, period = pick
    label = _period_label(period)
    return Figure(
        value=v, source="document", inputs=[FieldRef.of(row)], period_label=label,
        basis="actual", basis_label=actual_label(_family(row.doc_type), label, row.doc_name),
        period_end=period if period and period != "ttm" else None,
    )


def _docs(*figures: Figure) -> list[str]:
    out: list[str] = []
    for f in figures:
        for ref in f.inputs:
            if ref.doc_name and ref.doc_name not in out:
                out.append(ref.doc_name)
    return out


# ───────────────────────────── the three methods ─────────────────────────────


def _str_rows(rows: Sequence[FieldRow]) -> list[FieldRow]:
    return [r for r in rows if (r.doc_type or "").upper() in {"STR", "STR_TREND"}]


def ttm_period_end(blend: Any) -> str | None:
    """The STR TTM window's period end: the last month of the subject's
    monthly series, else the report period the STR reader resolved for that
    report (extracted period-end field, document date, filename token or
    report year — ``market_comp_set.resolve_report_period_end``)."""
    return getattr(blend, "period_end", None) or getattr(blend, "period_end_used", None)


def _ttm_basis(blend: Any) -> tuple[str | None, str]:
    end = ttm_period_end(blend)
    doc = getattr(blend, "source_doc_name", None) or next(iter(getattr(blend, "documents", None) or []), None)
    return end, actual_label("STR TTM", period_end_text(end), doc)


def read_subject_ttm(rows: Sequence[FieldRow]) -> tuple[Figure, Figure, str | None]:
    """The subject's STR trailing-twelve-month occupancy / ADR and its period."""
    str_rows = _str_rows(rows)
    blend = derive_ttm_blend(build_str_inputs(list(str_rows))) if str_rows else None
    if blend is None:
        miss = "No STR Trend report with the subject's trailing-twelve-month figures on this deal."
        return Figure.none(miss), Figure.none(miss), None
    by_name = {ref.field_name.lower(): ref for ref in blend.inputs}
    period = (
        f"TTM to {blend.period_end}" if blend.period_end
        else f"STR {blend.report_year}" if blend.report_year
        else "STR TTM"
    )
    ttm_end, basis_label = _ttm_basis(blend)
    occ_ref = by_name.get("ttm_performance.subject.occupancy_pct")
    adr_ref = by_name.get("ttm_performance.subject.adr_usd")
    occ = (
        Figure(
            value=_occ_fraction(blend.subject_occupancy_pct), source="document", inputs=[occ_ref], period_label=period,
            basis="actual", basis_label=basis_label, period_end=ttm_end,
        )
        if occ_ref is not None and blend.subject_occupancy_pct is not None
        else Figure.none("The STR report carries no subject TTM occupancy.")
    )
    adr = (
        Figure(
            value=blend.subject_adr_usd, source="document", inputs=[adr_ref], period_label=period,
            basis="actual", basis_label=basis_label, period_end=ttm_end,
        )
        if adr_ref is not None and blend.subject_adr_usd is not None
        else Figure.none("The STR report carries no subject TTM ADR.")
    )
    return occ, adr, period


def read_str_comp_set(rows: Sequence[FieldRow]) -> tuple[MethodReading, Figure, Figure]:
    """The STR comp-set blend (comp = subject ÷ index) + the published MPI / ARI."""
    str_rows = _str_rows(rows)
    blend = derive_ttm_blend(build_str_inputs(list(str_rows))) if str_rows else None
    label = METHOD_LABELS[METHOD_STR_COMP_SET]
    if blend is None:
        reason = "No STR Trend report with the subject TTM and MPI / ARI indices on this deal."
        return (
            MethodReading(
                method=METHOD_STR_COMP_SET, label=label, available=False, disabled_reason=reason,
                occupancy=Figure.none(reason), adr=Figure.none(reason),
            ),
            Figure.none(reason),
            Figure.none(reason),
        )
    by_name = {ref.field_name.lower(): ref for ref in blend.inputs}
    period = f"TTM to {blend.period_end}" if blend.period_end else None
    ttm_end, basis_label = _ttm_basis(blend)

    def refs(*names: str) -> list[FieldRef]:
        return [by_name[n] for n in names if n in by_name]

    occ = (
        Figure(
            value=_occ_fraction(blend.occupancy_pct), source="computed",
            inputs=refs("ttm_performance.subject.occupancy_pct", "ttm_performance.indices.mpi_occupancy_index"),
            detail="subject TTM occupancy ÷ MPI", period_label=period,
            basis="actual", basis_label=basis_label, period_end=ttm_end,
        )
        if blend.occupancy_pct is not None
        else Figure.none("The STR report lacks the subject TTM occupancy or the MPI index.")
    )
    adr = (
        Figure(
            value=blend.adr_usd, source="computed",
            inputs=refs("ttm_performance.subject.adr_usd", "ttm_performance.indices.ari_adr_index"),
            detail="subject TTM ADR ÷ ARI", period_label=period,
            basis="actual", basis_label=basis_label, period_end=ttm_end,
        )
        if blend.adr_usd is not None
        else Figure.none("The STR report lacks the subject TTM ADR or the ARI index.")
    )
    mpi_ref = by_name.get("ttm_performance.indices.mpi_occupancy_index")
    ari_ref = by_name.get("ttm_performance.indices.ari_adr_index")
    mpi = (
        Figure(value=_index_points(blend.mpi), source="document", inputs=[mpi_ref], period_label=period)
        if blend.mpi is not None and mpi_ref is not None
        else Figure.none("The STR report carries no MPI index.")
    )
    ari = (
        Figure(value=_index_points(blend.ari), source="document", inputs=[ari_ref], period_label=period)
        if blend.ari is not None and ari_ref is not None
        else Figure.none("The STR report carries no ARI index.")
    )
    available = occ.value is not None and adr.value is not None
    return (
        MethodReading(
            method=METHOD_STR_COMP_SET, label=label, available=available,
            disabled_reason=None if available else "The STR report does not carry both the occupancy and ADR penetration inputs.",
            occupancy=occ, adr=adr, documents=_docs(occ, adr),
        ),
        mpi,
        ari,
    )


def read_market_benchmark(
    rows: Sequence[FieldRow], *, segment: str | None = None, service_hint: str | None = None
) -> MethodReading:
    """Market-level or chain-scale segment occupancy / ADR from the market reports.

    Segment choice: the analyst's ``index_market_segment`` → the segment named
    like the deal's service level → the market-wide figure → the only segment.
    Several segments and nothing to choose by = unavailable, naming them.
    """
    label = METHOD_LABELS[METHOD_MARKET_BENCHMARK]
    market_rows: list[tuple[FieldRow, re.Match[str]]] = []
    seg_rows: dict[str, list[tuple[FieldRow, re.Match[str]]]] = {}
    for r in rows:
        name = r.lname
        m = _MARKET_RE.match(name)
        if m:
            market_rows.append((r, m))
            continue
        m = _SEGMENT_RE.match(name)
        if m:
            seg_rows.setdefault(m.group("segment"), []).append((r, m))

    def usable(group: list[tuple[FieldRow, re.Match[str]]]) -> bool:
        return _pick_metric(group, "occupancy") is not None or _pick_metric(group, "adr") is not None

    segments = sorted(s for s, g in seg_rows.items() if usable(g))
    chosen_rows: list[tuple[FieldRow, re.Match[str]]] | None = None
    chosen_segment: str | None = None
    wanted = _slug(segment)
    hint = _slug(service_hint)
    if wanted and wanted in segments:
        chosen_rows, chosen_segment = seg_rows[wanted], wanted
    elif wanted == "market" and usable(market_rows):
        chosen_rows = market_rows
    elif hint and hint in segments:
        chosen_rows, chosen_segment = seg_rows[hint], hint
    elif usable(market_rows):
        chosen_rows = market_rows
    elif len(segments) == 1:
        chosen_rows, chosen_segment = seg_rows[segments[0]], segments[0]

    if chosen_rows is None:
        if segments:
            reason = (
                "The market report carries chain-scale segments ("
                + ", ".join(s.replace("_", " ") for s in segments)
                + ") but no market-wide figure — choose the segment to benchmark against."
            )
        else:
            reason = (
                "No market or chain-scale occupancy / ADR in the uploaded CoStar / STR market reports "
                "(ttm_performance.segment.* or pnl_benchmark.market.*)."
            )
        return MethodReading(
            method=METHOD_MARKET_BENCHMARK, label=label, available=False, disabled_reason=reason,
            occupancy=Figure.none(reason), adr=Figure.none(reason), segments_available=segments,
        )
    where = f"the {chosen_segment.replace('_', ' ')} segment" if chosen_segment else "the market"
    occ = _figure_from(_pick_metric(chosen_rows, "occupancy"), f"No occupancy for {where} in the market report.")
    adr = _figure_from(_pick_metric(chosen_rows, "adr"), f"No ADR for {where} in the market report.")
    available = occ.value is not None and adr.value is not None
    return MethodReading(
        method=METHOD_MARKET_BENCHMARK, label=label, available=available,
        disabled_reason=None if available else f"The market report has only part of {where}'s occupancy / ADR.",
        occupancy=occ, adr=adr, documents=_docs(occ, adr),
        segment=chosen_segment, segments_available=segments,
    )


def read_costar_comp_set(rows: Sequence[FieldRow]) -> MethodReading:
    label = METHOD_LABELS[METHOD_COSTAR_COMP_SET]
    costar = [r for r in rows if r.lname.startswith(COSTAR_PREFIX)]
    if not costar:
        reason = (
            "No CoStar Property Analytics comp-set extraction on this deal. Upload a CoStar "
            "Property Analytics comp-set export to use this method."
        )
        return MethodReading(
            method=METHOD_COSTAR_COMP_SET, label=label, available=False, disabled_reason=reason,
            occupancy=Figure.none(reason), adr=Figure.none(reason),
        )
    matched = [(r, m) for r in costar if (m := _COSTAR_RE.match(r.lname))]
    occ = _figure_from(_pick_metric(matched, "occupancy"), "The CoStar comp-set extraction carries no occupancy.")
    adr = _figure_from(_pick_metric(matched, "adr"), "The CoStar comp-set extraction carries no ADR.")
    available = occ.value is not None and adr.value is not None
    docs = sorted({r.doc_name for r in costar if r.doc_name})
    return MethodReading(
        method=METHOD_COSTAR_COMP_SET, label=label, available=available,
        disabled_reason=None if available else (
            f"A CoStar comp-set extraction exists ({', '.join(docs) or 'unnamed document'}) "
            "but it does not carry both occupancy and ADR."
        ),
        occupancy=occ, adr=adr, documents=_docs(occ, adr) or docs,
    )


# ───────────────────────────── assumptions ─────────────────────────────


def read_growth_default(rows: Sequence[FieldRow], metric: Literal["occupancy", "adr"]) -> Figure:
    """The market's forward growth for ``metric``: the earliest forecast year of
    the market report, then CBRE Horizons' earliest forecast year, then its
    long-run average. ``None`` with a reason when no report states one."""
    best: tuple[tuple[int, int], FieldRow, float, str] | None = None
    for r in rows:
        name = r.lname
        for tier, rx in ((0, _GROWTH_MARKET_RE), (1, _GROWTH_CBRE_RE), (2, _GROWTH_CBRE_LONG_RUN_RE)):
            m = rx.match(name)
            if not m or m.group("metric") != metric:
                continue
            if tier == 1 and (r.period or "") != "forecast":
                continue
            v = _growth_fraction(r.value)
            if v is None:
                continue
            year = int(m.group("year")) if "year" in m.groupdict() and m.group("year") else 9999
            label = f"{year} forecast" if year != 9999 else "long-run average"
            rank = (tier, year)
            if best is None or rank < best[0]:
                best = (rank, r, v, label)
    if best is None:
        noun = "occupancy" if metric == "occupancy" else "ADR"
        return Figure.none(f"No market {noun} growth forecast in the uploaded reports — enter your own.")
    _, row, v, label = best
    return Figure(value=v, source="document", inputs=[FieldRef.of(row)], period_label=label)


def _penetration_default(
    method: MethodReading, published: Figure, subject: Figure, benchmark: Figure, *, points_label: str
) -> Figure:
    if method.method == METHOD_STR_COMP_SET:
        return published
    if subject.value is not None and benchmark.value is not None and benchmark.value > 0:
        return Figure(
            value=round(subject.value / benchmark.value * 100.0, 2),
            source="computed",
            inputs=[*subject.inputs, *benchmark.inputs],
            detail=f"subject STR TTM ÷ {method.label.lower()} x 100 ({points_label})",
        )
    return Figure.none(
        f"Needs both the subject's STR TTM and the {method.label.lower()} figure to compute a current {points_label}."
    )


def resolve_index_methodology(
    rows: Sequence[FieldRow],
    overrides: Mapping[str, Any] | None,
    *,
    service_hint: str | None = None,
) -> IndexMethodologyReading:
    """Everything the Index Analysis methodology panel shows. Pure."""
    selected, selected_source = selected_method(overrides)
    str_method, mpi_pub, ari_pub = read_str_comp_set(rows)
    segment = override_scalar(overrides, OVERRIDE_SEGMENT)
    market = read_market_benchmark(
        rows, segment=segment if isinstance(segment, str) else None, service_hint=service_hint
    )
    costar = read_costar_comp_set(rows)
    methods = [str_method, market, costar]
    subj_occ, subj_adr, subj_period = read_subject_ttm(rows)
    subj_end = subj_occ.period_end or subj_adr.period_end
    chosen = next(m for m in methods if m.method == selected)

    assumptions: dict[str, Figure] = {
        KEY_OCC_GROWTH: read_growth_default(rows, "occupancy"),
        KEY_ADR_GROWTH: read_growth_default(rows, "adr"),
        KEY_MPI_TARGET: _penetration_default(chosen, mpi_pub, subj_occ, chosen.occupancy, points_label="MPI"),
        KEY_ARI_TARGET: _penetration_default(chosen, ari_pub, subj_adr, chosen.adr, points_label="ARI"),
    }
    for key in ASSUMPTION_KEYS:
        v = coerce_float(override_scalar(overrides, key))
        if v is None:
            continue
        if key in (KEY_MPI_TARGET, KEY_ARI_TARGET):
            # The panel must print the SAME number the by-year grid and the
            # model run on: a target typed as a ratio (1.10) is 110 index
            # points everywhere (``read_targets_by_year`` / ``validate_index_target``).
            # A legacy out-of-range value (stored before the range gate) is
            # IGNORED everywhere it is read — the model never runs on it — so
            # the row shows no value and says why, never a number nothing applies.
            # Its source is ``ignored_override`` — distinct from an active
            # override AND from "no source", so the panel can say a stored
            # value exists but is not applied.
            pts = _index_points(v)
            if pts is None:
                assumptions[key] = Figure(
                    value=None,
                    source="ignored_override",
                    detail=stored_target_problem(override_scalar(overrides, key), key)
                    or "Stored target is outside the accepted range — re-enter it. It is ignored, not applied.",
                )
                continue
            v = pts
        assumptions[key] = Figure(value=v, source="override", detail=override_note(overrides, key))
    return IndexMethodologyReading(
        selected=selected,
        selected_source=selected_source,
        methods=methods,
        subject_occupancy=subj_occ,
        subject_adr=subj_adr,
        subject_period_label=subj_period,
        assumptions=assumptions,
        toggle_on=toggle_is_on(overrides),
        subject_period_end=subj_end,
        cbre_report_date=read_cbre_report_date(rows),
    )


# ───────────────────────────── engine seam ─────────────────────────────


def wants_benchmark(base: Mapping[str, Any], analyst_override_paths: set[str]) -> bool:
    """Does applying the analyst's assumptions need the benchmark rows read?"""
    return any(
        k in analyst_override_paths and _index_points(coerce_float(base.get(k))) is not None
        for k in (KEY_MPI_TARGET, KEY_ARI_TARGET)
    ) or any(
        k in analyst_override_paths and any(v is not None for v in coerce_target_list(base.get(k)))
        for k in BY_YEAR_TARGET_KEYS
    )


def apply_index_assumptions(
    base: dict[str, Any],
    sources: dict[str, str],
    analyst_override_paths: set[str],
    *,
    rows: Sequence[FieldRow] | None,
    str_basis_sources: frozenset[str],
    derived_sources: frozenset[str] = frozenset(),
) -> list[str]:
    """Feed the analyst's Index Analysis assumptions into the revenue inputs.

    Called from the engine runner's STR-basis block ONLY (toggle on). Applies
    an assumption only when the analyst overrode it, and never over an
    explicit analyst override of the engine key it moves. The penetration
    targets apply only while the Year-1 rates sit on an STR basis — i.e. the
    seed actually landed. Returns the engine keys it changed.
    """
    changed: list[str] = []

    def analyst_set(key: str) -> float | None:
        return coerce_float(base.get(key)) if key in analyst_override_paths else None

    for assumption, engine_key in ((KEY_OCC_GROWTH, "occupancy_growth"), (KEY_ADR_GROWTH, "adr_growth")):
        v = analyst_set(assumption)
        if v is None or engine_key in analyst_override_paths:
            continue
        if sources.get(engine_key) in derived_sources:
            continue
        base[engine_key] = v
        sources[engine_key] = SOURCE_INDEX_ASSUMPTION
        changed.append(engine_key)

    # Read through the same range check the writes use: a legacy stored target
    # outside 25–250 points / 0.25–2.5 ratio is ignored, never applied
    # (``stored_target_problem`` names it on the panel and the by-year grid).
    mpi_t = _index_points(analyst_set(KEY_MPI_TARGET))
    ari_t = _index_points(analyst_set(KEY_ARI_TARGET))
    if (mpi_t is None and ari_t is None) or rows is None:
        return changed
    method, _ = selected_method({OVERRIDE_METHOD: base.get(OVERRIDE_METHOD)})
    segment = base.get(OVERRIDE_SEGMENT)
    if method == METHOD_STR_COMP_SET:
        reading = read_str_comp_set(rows)[0]
    elif method == METHOD_MARKET_BENCHMARK:
        reading = read_market_benchmark(rows, segment=segment if isinstance(segment, str) else None)
    else:
        reading = read_costar_comp_set(rows)
    for target, bench, engine_key, normalize in (
        (mpi_t, reading.occupancy.value, "starting_occupancy", _valid_occupancy),
        (ari_t, reading.adr.value, "starting_adr", _valid_adr),
    ):
        if target is None or bench is None or target <= 0:
            continue
        if sources.get(engine_key) not in str_basis_sources:
            continue  # an explicit analyst value, or the seed did not land
        value = normalize(bench * target / 100.0)  # target is index points
        if value is None:
            continue
        base[engine_key] = value
        sources[engine_key] = SOURCE_INDEX_ASSUMPTION
        changed.append(engine_key)
    return changed


# ───────────────────────────── by-year targets (FON-95 part 2) ─────────────────────────────


def _target_list_entries(raw: Any) -> list[Any] | None:
    """The raw entries of an ``index_*_target_by_year`` value (list or
    JSON-encoded list), capped at the model horizon; ``None`` when the value
    is not a list at all."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    if not isinstance(raw, (list, tuple)):
        return None
    return list(raw)[:MAX_MODEL_YEARS]


def coerce_target_list(raw: Any) -> list[float | None]:
    """``index_*_target_by_year`` → index points per model year (``None`` = unset).

    Accepts a list or a JSON-encoded list. Each entry arrives as index points
    (110) or a ratio (1.10); blank / zero / negative / unparseable / outside
    the accepted range (``validate_index_target``) = not set — an out-of-range
    entry is never read as points and driven into the model.
    """
    entries = _target_list_entries(raw)
    if entries is None:
        return []
    return [validate_index_target(v)[0] for v in entries]


def target_list_problems(raw: Any) -> list[str]:
    """Why an ``index_*_target_by_year`` value cannot be stored as sent —
    one line per offending year, empty when every set entry is valid.
    ``None`` / blank entries are "unset" and never a problem."""
    entries = _target_list_entries(raw)
    if entries is None:
        return [] if raw is None else ["must be a list with one entry per model year (null = unset)"]
    problems: list[str] = []
    for idx, v in enumerate(entries, start=1):
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        pts, why = validate_index_target(v)
        if pts is None:
            problems.append(f"Year {idx}: {why}")
    return problems


TargetSource = Literal["by_year", "scalar"]
#: ``scalar_year1_only`` — Year 2+ with only the all-years (scalar) target and
#: no analyst growth override for that metric: the target drives Year 1 only
#: and the model's own growth carries the year (the pre-FON-95-part-2 shape).
DriverStatus = Literal["driven", "no_target", "benchmark_unavailable", "scalar_year1_only"]


@dataclass(frozen=True)
class TargetReading:
    """One year's effective penetration target and where it came from."""

    value: float | None
    source: TargetSource | None
    note: str | None = None
    #: A stored target for this year that was IGNORED (out of range), and why.
    problem: str | None = None


@dataclass(frozen=True)
class IndexYearReading:
    year: int
    calendar_year: int | None
    benchmark_occupancy: Figure  # fraction
    benchmark_adr: Figure  # USD
    mpi_target: TargetReading
    ari_target: TargetReading
    #: MPI × ARI ÷ 100 — derived, never an input.
    rgi_derived: float | None
    #: benchmark × target ÷ 100 where both exist (before Year-1 displacement).
    occupancy: float | None
    adr: float | None
    occupancy_status: DriverStatus
    adr_status: DriverStatus
    reason: str | None


@dataclass(frozen=True)
class IndexByYearReading:
    method: str
    method_label: str
    hold_years: int
    calendar_known: bool
    years: list[IndexYearReading]
    #: The STR competitive set by model year — the SAME ``_grown_series`` the
    #: ``str_comp_set`` method indexes against (TTM level in Year 1, grown at
    #: the market growth assumption after it; ``None`` with the reason where
    #: no growth value exists). Computed whichever method is selected, so the
    #: Index Analysis comp-set table can fill its forecast columns.
    comp_set_occupancy: list[Figure] = field(default_factory=list)
    comp_set_adr: list[Figure] = field(default_factory=list)

    @property
    def any_target(self) -> bool:
        return any(y.mpi_target.value is not None or y.ari_target.value is not None for y in self.years)


def read_targets_by_year(overrides: Mapping[str, Any] | None, hold_years: int) -> tuple[list[TargetReading], list[TargetReading]]:
    """Effective MPI / ARI target per model year: the by-year entry where set,
    else the scalar all-years target, else none. Only ANALYST-SET values count —
    a document default is shown beside the assumption, never applied."""
    mpi_raw = override_scalar(overrides, KEY_MPI_TARGET_BY_YEAR)
    ari_raw = override_scalar(overrides, KEY_ARI_TARGET_BY_YEAR)
    mpi_list = coerce_target_list(mpi_raw)
    ari_list = coerce_target_list(ari_raw)
    entries = {
        KEY_MPI_TARGET_BY_YEAR: _target_list_entries(mpi_raw) or [],
        KEY_ARI_TARGET_BY_YEAR: _target_list_entries(ari_raw) or [],
    }
    mpi_scalar_raw = override_scalar(overrides, KEY_MPI_TARGET)
    ari_scalar_raw = override_scalar(overrides, KEY_ARI_TARGET)
    mpi_scalar = _index_points(coerce_float(mpi_scalar_raw))
    ari_scalar = _index_points(coerce_float(ari_scalar_raw))
    scalar_problem = {
        KEY_MPI_TARGET: stored_target_problem(mpi_scalar_raw, KEY_MPI_TARGET),
        KEY_ARI_TARGET: stored_target_problem(ari_scalar_raw, KEY_ARI_TARGET),
    }
    notes = {k: override_note(overrides, k) for k in (*BY_YEAR_TARGET_KEYS, KEY_MPI_TARGET, KEY_ARI_TARGET)}

    def pick(lst: list[float | None], scalar: float | None, list_key: str, scalar_key: str, y: int) -> TargetReading:
        v = lst[y - 1] if y - 1 < len(lst) else None
        if v is not None:
            return TargetReading(value=v, source="by_year", note=notes[list_key])
        # An out-of-range stored entry for this year, or an out-of-range
        # all-years target, is ignored (never applied) and named.
        raw_entries = entries[list_key]
        problems = [
            p
            for p in (
                stored_target_problem(raw_entries[y - 1], list_key, year=y) if y - 1 < len(raw_entries) else None,
                scalar_problem[scalar_key],
            )
            if p
        ]
        problem = " ".join(problems) or None
        if scalar is not None:
            return TargetReading(value=scalar, source="scalar", note=notes[scalar_key], problem=problem)
        return TargetReading(value=None, source=None, problem=problem)

    years = range(1, max(1, hold_years) + 1)
    return (
        [pick(mpi_list, mpi_scalar, KEY_MPI_TARGET_BY_YEAR, KEY_MPI_TARGET, y) for y in years],
        [pick(ari_list, ari_scalar, KEY_ARI_TARGET_BY_YEAR, KEY_ARI_TARGET, y) for y in years],
    )


def _grown_series(
    level: Figure, growth: Figure, hold_years: int, *, noun: str, method_label: str
) -> list[Figure]:
    """A comp-set benchmark by model year: the reported level in Year 1, grown
    at the market growth assumption after it. No growth assumption → the
    out-years are ``None`` with the reason (never the model's own growth)."""
    out: list[Figure] = []
    period = level.period_label or "TTM"
    for y in range(1, hold_years + 1):
        if level.value is None:
            out.append(Figure.none(level.detail or f"No {method_label} {noun}."))
            continue
        if y == 1:
            out.append(
                Figure(
                    value=level.value, source=level.source, inputs=list(level.inputs),
                    detail=f"{method_label} {period} {noun}" + (f" ({level.detail})" if level.detail else ""),
                    period_label=period,
                    basis=level.basis, basis_label=level.basis_label, period_end=level.period_end,
                )
            )
            continue
        if growth.value is None:
            out.append(
                Figure.none(
                    f"No market {noun} growth assumption to carry the {method_label.lower()} past Year 1"
                    + (f" — {growth.detail}" if growth.detail else "")
                    + "."
                )
            )
            continue
        v = level.value * (1.0 + growth.value) ** (y - 1)
        if noun == "occupancy" and v >= 1.0:
            out.append(Figure.none(f"Growing the {method_label.lower()} occupancy {y - 1} years at {growth.value:+.2%} passes 100%."))
            continue
        growth_basis = (
            "your override" if growth.source == "override" else (growth.period_label or "document")
        )
        out.append(
            Figure(
                value=v,
                source="computed",
                inputs=[*level.inputs, *growth.inputs],
                detail=(
                    f"{method_label} {period} {noun} grown {y - 1} year{'s' if y - 1 != 1 else ''} "
                    f"at {growth.value:+.2%} market {noun} growth ({growth_basis})"
                ),
                period_label=f"Year {y}",
                basis="assumption",
                basis_label=ASSUMPTION_GROWN_LABEL,
            )
        )
    return out


def _cbre_series(
    rows: Sequence[FieldRow],
    *,
    segment: str | None,
    calendar_years: Sequence[int],
    hold_years: int,
    level_occ: Figure,
    level_adr: Figure,
    method_label: str,
) -> tuple[list[Figure], list[Figure]]:
    """The market benchmark by model year: Year 1 = the reported level (as the
    scalar path uses), Year 2+ = the CBRE Horizons forecast for that model
    year's CALENDAR year — the segment table named like the chosen segment
    when the report carries one, else All Hotels. A year the report does not
    carry (or that cannot be placed without a close date) is ``None`` with the
    reason."""
    tables: dict[str, dict[int, dict[str, FieldRow]]] = {}
    legacy: dict[int, dict[str, FieldRow]] = {}
    for r in rows:
        m = _CBRE_SEGMENT_YEAR_RE.match(r.lname)
        if m:
            slot = tables.setdefault(m.group("scope"), {}).setdefault(int(m.group("year")), {})
            slot.setdefault(m.group("attr"), r)  # newest extraction first
            continue
        m = _CBRE_LEGACY_YEAR_RE.match(r.lname)
        if m:
            legacy.setdefault(int(m.group("n")), {}).setdefault(m.group("attr"), r)
    wanted = _slug(segment)
    scope = wanted if wanted and wanted in tables else "all"
    table = tables.get(scope) or {}
    if not table and legacy:
        for slot in legacy.values():
            cal = coerce_int(slot["calendar_year"].value) if "calendar_year" in slot else None
            if cal is not None:
                table.setdefault(cal, {}).update({k: v for k, v in slot.items() if k != "calendar_year"})
    scope_label = "All Hotels" if scope == "all" else scope.replace("_", " ")
    have_cbre = bool(tables or legacy)
    # The publication date each CBRE extraction states (newest first).
    report_dates: dict[str | None, str] = {}
    for r in rows:
        if r.lname == "cbre_horizons.publication_date" and isinstance(r.value, str) and r.value.strip():
            report_dates.setdefault(r.extraction_id, r.value.strip())

    def year_figure(y: int, attr: str, level: Figure, noun: str) -> Figure:
        if y == 1:
            if level.value is None:
                return Figure.none(level.detail or f"No {method_label} {noun}.")
            return Figure(
                value=level.value, source=level.source, inputs=list(level.inputs),
                detail=f"{method_label} {level.period_label or 'reported'} {noun}",
                period_label=level.period_label,
                basis=level.basis, basis_label=level.basis_label, period_end=level.period_end,
            )
        cal = calendar_years[y - 1] if y - 1 < len(calendar_years) else None
        if cal is None:
            return Figure.none(
                "No acquisition close date — the CBRE Horizons forecast cannot be placed on the "
                "model calendar (set the acquisition date on CAPEX)."
            )
        if not have_cbre:
            return Figure.none(
                "No CBRE Hotel Horizons report on this deal — upload one for the market benchmark's forecast years."
            )
        row = (table.get(cal) or {}).get(attr)
        v = coerce_float(row.value) if row is not None else None
        if noun == "occupancy":
            v = _occ_fraction(v)
        elif v is not None and v <= 0:
            v = None
        if row is None or v is None:
            return Figure.none(f"No CBRE Horizons {scope_label} {noun} for {cal} on this deal.")
        period_row = (table.get(cal) or {}).get("period")
        period = period_row.value if period_row is not None and isinstance(period_row.value, str) else None
        kind = "forecast" if (period or "forecast").strip().lower().startswith("forecast") else "actual"
        return Figure(
            value=v, source="document", inputs=[FieldRef.of(row)],
            detail=f"CBRE Horizons {scope_label} {cal} {kind} {noun}", period_label=str(cal),
            basis="forecast" if kind == "forecast" else "actual",
            basis_label=forecast_label(cal, row.doc_name, row.page, kind=kind),
            period_end=str(cal) if kind == "actual" else None,
            report_date=report_dates.get(row.extraction_id) or row.report_as_of,
        )

    occ = [year_figure(y, "occupancy_pct", level_occ, "occupancy") for y in range(1, hold_years + 1)]
    adr = [year_figure(y, "adr_usd", level_adr, "ADR") for y in range(1, hold_years + 1)]
    return occ, adr


def resolve_index_by_year(
    rows: Sequence[FieldRow],
    overrides: Mapping[str, Any] | None,
    *,
    hold_years: int,
    calendar_years: Sequence[int] | None = None,
    service_hint: str | None = None,
) -> IndexByYearReading:
    """Everything the by-year grid shows — and exactly what the runner drives
    the revenue engine with when the STR basis is on. Pure.

    Per model year: the selected benchmark's occupancy / ADR for that year
    (STR / CoStar comp set grown at the market growth assumptions; market
    benchmark = CBRE Horizons by calendar year), the effective MPI / ARI
    target (by-year beats scalar), the derived RGI, and the resulting
    occupancy = benchmark × MPI ÷ 100 / ADR = benchmark × ARI ÷ 100. A year
    with a target but no benchmark is ``benchmark_unavailable``; a year with no
    target is ``no_target``. Nothing is invented.
    """
    hold = max(1, min(int(hold_years or 1), MAX_MODEL_YEARS))
    cal = [int(c) for c in (calendar_years or [])]
    reading = resolve_index_methodology(rows, overrides, service_hint=service_hint)
    chosen = reading.method(reading.selected)
    assert chosen is not None
    occ_growth = reading.assumptions[KEY_OCC_GROWTH]
    adr_growth = reading.assumptions[KEY_ADR_GROWTH]
    if not chosen.available:
        reason = chosen.disabled_reason or f"{chosen.label} is unavailable on this deal."
        bench_occ = [Figure.none(reason) for _ in range(hold)]
        bench_adr = [Figure.none(reason) for _ in range(hold)]
    elif chosen.method == METHOD_MARKET_BENCHMARK:
        bench_occ, bench_adr = _cbre_series(
            rows, segment=chosen.segment, calendar_years=cal, hold_years=hold,
            level_occ=chosen.occupancy, level_adr=chosen.adr, method_label=chosen.label,
        )
    else:
        bench_occ = _grown_series(chosen.occupancy, occ_growth, hold, noun="occupancy", method_label=chosen.label)
        bench_adr = _grown_series(chosen.adr, adr_growth, hold, noun="ADR", method_label=chosen.label)
    mpi_t, ari_t = read_targets_by_year(overrides, hold)
    # Out-year gate: the all-years (scalar) target drives Year 1 only unless
    # the analyst asked for the out-years — a by-year target for that year, or
    # a market growth OVERRIDE for that metric (the document default is shown
    # beside the assumption, never applied). Without the gate a deal that had
    # a scalar target and the STR toggle on before by-year targets shipped
    # would have been re-based in Years 2+ with no analyst action.
    occ_growth_overridden = occ_growth.source == "override"
    adr_growth_overridden = adr_growth.source == "override"
    grown_method = chosen.method != METHOD_MARKET_BENCHMARK

    years: list[IndexYearReading] = []
    for y in range(1, hold + 1):
        bo, ba = bench_occ[y - 1], bench_adr[y - 1]
        mt, at = mpi_t[y - 1], ari_t[y - 1]
        reasons: list[str] = []

        def resolve(
            target: TargetReading, bench: Figure, normalize, label: str, *, growth_overridden: bool, noun: str
        ) -> tuple[float | None, DriverStatus]:
            if target.problem:
                reasons.append(f"{label}: {target.problem}")
            if target.value is None:
                return None, "no_target"
            if y > 1 and target.source == "scalar" and not growth_overridden:
                reasons.append(scalar_year1_only_reason(y, label, noun, grown_method=grown_method))
                return None, "scalar_year1_only"
            if bench.value is None:
                reasons.append(f"{label}: {bench.detail or 'no benchmark value'}")
                return None, "benchmark_unavailable"
            v = normalize(bench.value * target.value / 100.0)
            if v is None:
                reasons.append(f"{label}: benchmark × target gives an impossible figure.")
                return None, "benchmark_unavailable"
            return v, "driven"

        occ_v, occ_s = resolve(
            mt, bo, _valid_occupancy, "Occupancy", growth_overridden=occ_growth_overridden, noun="occupancy"
        )
        adr_v, adr_s = resolve(at, ba, _valid_adr, "ADR", growth_overridden=adr_growth_overridden, noun="ADR")
        rgi = (mt.value * at.value / 100.0) if mt.value is not None and at.value is not None else None
        years.append(
            IndexYearReading(
                year=y,
                calendar_year=cal[y - 1] if y - 1 < len(cal) else None,
                benchmark_occupancy=bo,
                benchmark_adr=ba,
                mpi_target=mt,
                ari_target=at,
                rgi_derived=round(rgi, 2) if rgi is not None else None,
                occupancy=occ_v,
                adr=adr_v,
                occupancy_status=occ_s,
                adr_status=adr_s,
                reason="; ".join(reasons) or None,
            )
        )
    str_method = reading.method(METHOD_STR_COMP_SET)
    if chosen.method == METHOD_STR_COMP_SET:
        comp_occ, comp_adr = bench_occ, bench_adr
    elif str_method is not None and str_method.available:
        comp_occ = _grown_series(str_method.occupancy, occ_growth, hold, noun="occupancy", method_label=str_method.label)
        comp_adr = _grown_series(str_method.adr, adr_growth, hold, noun="ADR", method_label=str_method.label)
    else:
        why = (str_method.disabled_reason if str_method is not None else None) or "No STR competitive set on this deal."
        comp_occ = [Figure.none(why) for _ in range(hold)]
        comp_adr = [Figure.none(why) for _ in range(hold)]
    return IndexByYearReading(
        method=chosen.method, method_label=chosen.label, hold_years=hold,
        calendar_known=bool(cal), years=years,
        comp_set_occupancy=comp_occ, comp_set_adr=comp_adr,
    )


def scalar_year1_only_reason(year: int, label: str, noun: str, *, grown_method: bool) -> str:
    """Why an out-year with only the all-years target is not driven."""
    how = f"set a Year-{year} {label.upper() if label == 'ADR' else label.lower()} target"
    if grown_method:
        how += f" or a market {noun} growth override"
    return (
        f"{label}: the all-years target drives Year 1 only; Year {year} follows the model's own "
        f"{noun} growth — {how} to index this year."
    )


def _doc_of(fig: Figure) -> tuple[str | None, int | None]:
    for ref in fig.inputs:
        if ref.doc_name:
            return ref.doc_name, ref.page
    return None, None


def driver_record(y: IndexYearReading, method: str) -> dict[str, Any]:
    """One ``IndexDriverYear`` payload (the schema the revenue engine echoes)."""
    doc, page = _doc_of(y.benchmark_occupancy)
    if doc is None:
        doc, page = _doc_of(y.benchmark_adr)
    return {
        "year": y.year,
        "calendar_year": y.calendar_year,
        "method": method,
        "benchmark_occupancy": y.benchmark_occupancy.value,
        "benchmark_adr": y.benchmark_adr.value,
        "benchmark_occupancy_source": y.benchmark_occupancy.detail,
        "benchmark_adr_source": y.benchmark_adr.detail,
        "benchmark_doc": doc,
        "benchmark_page": page,
        "mpi_target": y.mpi_target.value,
        "ari_target": y.ari_target.value,
        "mpi_target_source": y.mpi_target.source,
        "ari_target_source": y.ari_target.source,
        "rgi_derived": y.rgi_derived,
        "occupancy": y.occupancy,
        "adr": y.adr,
        "occupancy_status": y.occupancy_status,
        "adr_status": y.adr_status,
        "reason": y.reason,
    }


#: ``base`` keys the runner parks the drivers on for ``_build_input_for('revenue')``.
BASE_OCC_BY_YEAR = "index_occupancy_by_year"
BASE_ADR_BY_YEAR = "index_adr_by_year"
BASE_DRIVERS_BY_YEAR = "index_drivers_by_year"


def build_index_drivers(
    base: dict[str, Any],
    sources: dict[str, str],
    analyst_override_paths: set[str],
    *,
    rows: Sequence[FieldRow] | None,
    str_basis_sources: frozenset[str],
    hold_years: int,
    calendar_years: Sequence[int] | None,
) -> list[str]:
    """Turn the analyst's penetration targets into per-year revenue drivers.

    Called from the runner's STR-basis block ONLY (toggle on), after
    ``apply_index_assumptions``. Does nothing — and leaves ``base`` untouched —
    unless the analyst set a target (by-year or scalar) AND the Year-1 rate for
    that metric sits on an STR basis (the seed landed; an explicit analyst
    ``starting_*`` override is never overwritten). Parks the per-year lists and
    the driver records on ``base`` and re-points Year 1 (by-year beats scalar).
    Returns the engine keys it changed.
    """
    overrides: dict[str, Any] = {}
    for k in (OVERRIDE_METHOD, OVERRIDE_SEGMENT):
        if base.get(k) is not None:
            overrides[k] = base[k]
    for k in (*ASSUMPTION_KEYS, *BY_YEAR_TARGET_KEYS):
        if k in analyst_override_paths and base.get(k) is not None:
            overrides[k] = base[k]
    if not any(k in overrides for k in (KEY_MPI_TARGET, KEY_ARI_TARGET, *BY_YEAR_TARGET_KEYS)):
        return []
    if rows is None:
        return []
    drivable = str_basis_sources | {SOURCE_INDEX_ASSUMPTION}
    occ_ok = sources.get("starting_occupancy") in drivable
    adr_ok = sources.get("starting_adr") in drivable
    reading = resolve_index_by_year(rows, overrides, hold_years=hold_years, calendar_years=calendar_years)
    if not reading.any_target or not (occ_ok or adr_ok):
        return []
    occ_list: list[float | None] = [None] * reading.hold_years
    adr_list: list[float | None] = [None] * reading.hold_years
    records: list[dict[str, Any]] = []
    changed: list[str] = []
    for y in reading.years:
        rec = driver_record(y, reading.method)
        # Join, never overwrite: ``rec["reason"]`` already carries resolve()'s
        # per-metric explanation for the OTHER metric (e.g. the ADR out-year
        # gate), which the grid tooltip would otherwise lose.
        extra: list[str] = []
        if y.occupancy is not None and occ_ok:
            occ_list[y.year - 1] = y.occupancy
        elif y.occupancy is not None:
            rec["occupancy"] = None
            rec["occupancy_status"] = "no_target"
            extra.append("Occupancy: Year-1 occupancy is an explicit analyst value, not the STR basis — the target is not applied.")
        if y.adr is not None and adr_ok:
            adr_list[y.year - 1] = y.adr
        elif y.adr is not None:
            rec["adr"] = None
            rec["adr_status"] = "no_target"
            extra.append("ADR: Year-1 ADR is an explicit analyst value, not the STR basis — the target is not applied.")
        if extra:
            rec["reason"] = "; ".join([*extra, *([rec["reason"]] if rec.get("reason") else [])])
        records.append(rec)
    if not any(v is not None for v in occ_list) and not any(v is not None for v in adr_list):
        return []
    if occ_list[0] is not None:
        base["starting_occupancy"] = occ_list[0]
        sources["starting_occupancy"] = SOURCE_INDEX_ASSUMPTION
        changed.append("starting_occupancy")
    if adr_list[0] is not None:
        base["starting_adr"] = adr_list[0]
        sources["starting_adr"] = SOURCE_INDEX_ASSUMPTION
        changed.append("starting_adr")
    base[BASE_OCC_BY_YEAR] = occ_list
    base[BASE_ADR_BY_YEAR] = adr_list
    base[BASE_DRIVERS_BY_YEAR] = records
    return changed


# ───────────────────────────── DB read ─────────────────────────────

_INDEX_DOC_TYPES = ("STR", "STR_TREND", "MARKET_STUDY", "CBRE_HORIZONS", "COSTAR")

_ROWS_SQL = """
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


def rows_from_records(records: Iterable[Mapping[str, Any]]) -> list[FieldRow]:
    """Flatten extraction records, tagging each row with the LANE it was
    extracted in (an STR_TREND-tagged CoStar market study reads as
    MARKET_STUDY, never as the STR comp-set blend)."""
    out: list[FieldRow] = []
    for rec in records:
        rows = parse_extraction_records([rec])
        lane = extraction_lane(
            doc_type=rec.get("doc_type"),
            agent_version=rec.get("agent_version"),
            ai_proposed_doc_type=rec.get("ai_proposed_doc_type"),
            field_names=(r.field_name for r in rows),
        )
        if lane == "MARKET_STUDY":
            out.extend(_with_doc_type(rows, "MARKET_STUDY"))
        else:
            out.extend(rows)
    return out


def _with_doc_type(rows: list[FieldRow], doc_type: str) -> list[FieldRow]:
    return [replace(r, doc_type=doc_type) for r in rows]


async def load_index_rows(
    session: AsyncSession, *, deal_id: UUID | str, tenant_id: UUID | str
) -> list[FieldRow]:
    types_sql = ", ".join(f"'{t}'" for t in _INDEX_DOC_TYPES)  # fixed literals
    result = await session.execute(
        text(_ROWS_SQL.format(types=types_sql)), {"deal": str(deal_id), "tenant": str(tenant_id)}
    )
    return rows_from_records(dict(r._mapping) for r in result.fetchall())


__all__ = [
    "ASSUMPTION_KEYS",
    "BASE_ADR_BY_YEAR",
    "BASE_DRIVERS_BY_YEAR",
    "BASE_OCC_BY_YEAR",
    "BY_YEAR_TARGET_KEYS",
    "DEFAULT_METHOD",
    "INDEX_POINTS_RANGE",
    "INDEX_RATIO_RANGE",
    "IndexByYearReading",
    "IndexYearReading",
    "KEY_ADR_GROWTH",
    "KEY_ARI_TARGET",
    "KEY_ARI_TARGET_BY_YEAR",
    "KEY_MPI_TARGET",
    "KEY_MPI_TARGET_BY_YEAR",
    "KEY_OCC_GROWTH",
    "MAX_MODEL_YEARS",
    "TargetReading",
    "build_index_drivers",
    "coerce_target_list",
    "driver_record",
    "read_targets_by_year",
    "resolve_index_by_year",
    "target_list_problems",
    "validate_index_target",
    "METHODS",
    "METHOD_COSTAR_COMP_SET",
    "METHOD_MARKET_BENCHMARK",
    "METHOD_STR_COMP_SET",
    "OVERRIDE_METHOD",
    "OVERRIDE_SEGMENT",
    "SOURCE_INDEX_ASSUMPTION",
    "Figure",
    "IndexMethodologyReading",
    "MethodReading",
    "apply_index_assumptions",
    "load_index_rows",
    "read_costar_comp_set",
    "read_growth_default",
    "read_market_benchmark",
    "read_str_comp_set",
    "read_subject_ttm",
    "resolve_index_methodology",
    "rows_from_records",
    "wants_benchmark",
]
