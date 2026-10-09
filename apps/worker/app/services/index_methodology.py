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

#: The source id stamped on an engine key one of these assumptions moved.
SOURCE_INDEX_ASSUMPTION = "index_assumption"

FigureSource = Literal["document", "computed", "override"]

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


@dataclass(frozen=True)
class Figure:
    """One displayed number and where it came from.

    ``source`` is ``document`` (one extraction row), ``computed`` (a formula of
    the ``inputs`` rows), ``override`` (the analyst's own value; ``detail`` is
    their note) or ``None`` (no source — ``detail`` says why).
    """

    value: float | None
    source: FigureSource | None
    inputs: list[FieldRef] = field(default_factory=list)
    detail: str | None = None
    period_label: str | None = None

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


def _index_points(v: float | None) -> float | None:
    """STR indices arrive as a ratio (1.032) or index points (103.2)."""
    if v is None or v <= 0:
        return None
    return v if v > 3 else v * 100.0


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
    return Figure(value=v, source="document", inputs=[FieldRef.of(row)], period_label=_period_label(period))


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
    occ_ref = by_name.get("ttm_performance.subject.occupancy_pct")
    adr_ref = by_name.get("ttm_performance.subject.adr_usd")
    occ = (
        Figure(value=_occ_fraction(blend.subject_occupancy_pct), source="document", inputs=[occ_ref], period_label=period)
        if occ_ref is not None and blend.subject_occupancy_pct is not None
        else Figure.none("The STR report carries no subject TTM occupancy.")
    )
    adr = (
        Figure(value=blend.subject_adr_usd, source="document", inputs=[adr_ref], period_label=period)
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

    def refs(*names: str) -> list[FieldRef]:
        return [by_name[n] for n in names if n in by_name]

    occ = (
        Figure(
            value=_occ_fraction(blend.occupancy_pct), source="computed",
            inputs=refs("ttm_performance.subject.occupancy_pct", "ttm_performance.indices.mpi_occupancy_index"),
            detail="subject TTM occupancy ÷ MPI", period_label=period,
        )
        if blend.occupancy_pct is not None
        else Figure.none("The STR report lacks the subject TTM occupancy or the MPI index.")
    )
    adr = (
        Figure(
            value=blend.adr_usd, source="computed",
            inputs=refs("ttm_performance.subject.adr_usd", "ttm_performance.indices.ari_adr_index"),
            detail="subject TTM ADR ÷ ARI", period_label=period,
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
    chosen = next(m for m in methods if m.method == selected)

    assumptions: dict[str, Figure] = {
        KEY_OCC_GROWTH: read_growth_default(rows, "occupancy"),
        KEY_ADR_GROWTH: read_growth_default(rows, "adr"),
        KEY_MPI_TARGET: _penetration_default(chosen, mpi_pub, subj_occ, chosen.occupancy, points_label="MPI"),
        KEY_ARI_TARGET: _penetration_default(chosen, ari_pub, subj_adr, chosen.adr, points_label="ARI"),
    }
    for key in ASSUMPTION_KEYS:
        v = coerce_float(override_scalar(overrides, key))
        if v is not None:
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
    )


# ───────────────────────────── engine seam ─────────────────────────────


def wants_benchmark(base: Mapping[str, Any], analyst_override_paths: set[str]) -> bool:
    """Does applying the analyst's assumptions need the benchmark rows read?"""
    return any(
        k in analyst_override_paths and coerce_float(base.get(k)) is not None
        for k in (KEY_MPI_TARGET, KEY_ARI_TARGET)
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

    mpi_t = analyst_set(KEY_MPI_TARGET)
    ari_t = analyst_set(KEY_ARI_TARGET)
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
        ratio = target / 100.0 if target > 3 else target
        value = normalize(bench * ratio)
        if value is None:
            continue
        base[engine_key] = value
        sources[engine_key] = SOURCE_INDEX_ASSUMPTION
        changed.append(engine_key)
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
    "DEFAULT_METHOD",
    "KEY_ADR_GROWTH",
    "KEY_ARI_TARGET",
    "KEY_MPI_TARGET",
    "KEY_OCC_GROWTH",
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
