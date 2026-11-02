"""FON-116 / FON-117 — the per-year market growth resolver (topline v2).

ONE mechanism decides the occupancy / ADR growth applied INTO each model year
(``topline-growth-plan.md`` §2.3). Per metric and per year, first hit wins:

1. ``analyst_override`` — the analyst's per-year list (``occupancy_growth_by_year``
   / ``adr_growth_by_year``, index ``y − 1`` = growth INTO model year ``y``),
   else the all-years scalar (``occupancy_growth`` / ``adr_growth``; a legacy
   ``index_market_*_growth`` value is read as that scalar and says so), else —
   ADR only — the analyst's ``revpar_growth`` (FON-69: ADR growth is derived
   per year so RevPAR grows at the analyst's rate over the resolved occupancy).
2. ``cbre_horizons`` — ``cbre_horizons.segment_all.<YYYY>.<metric>_change_pct``
   placed on the model calendar ("All Hotels"; the price tiers are not mapped
   to a class — plan decision 8.4).
3. ``costar_submarket_forecast`` — the deal's CoStar submarket report, the
   class bucket that matches the deal's positioning
   (``market_study.class.<bucket>.<YYYY>.<metric>_change_pct``), else the
   overall series, labelled. A bucket series that lacks a year inside its own
   range is NOT patched from another bucket.
4. ``carried_forward`` — a calendar year past the last year of the document
   series the previous year was read from reuses the previous year's growth
   (an assumption, labelled with the report and year it carries).
5. ``unavailable`` — nothing at all: the figure is ``None`` and the engine
   holds the metric flat (``flat_no_growth``). Growth 0 is the absence of
   growth, never a seeded rate.

A CoStar-derived candidate under a ``withhold`` data policy is
``withheld_by_policy`` (reason ``license_withheld``) — never applied, never
replaced by a seed. Document growth needs the model calendar
(``acquisition_close_date``); without it only the analyst's own figures apply.

Pure: no DB, no I/O. The loader filters rows dated after the underwriting
as-of date before calling and passes the gated document types so an
``unavailable`` year can say why.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from fondok_schemas.reasons import ReasonCode
from fondok_schemas.underwriting import GrowthSource, GrowthYear

from ..tenants import TenantDataPolicies
from .market_fields import FieldRow, coerce_float
from .market_study_reader import (
    CLASS_BUCKET_LABELS,
    change_fraction,
    read_class_forecast,
)

Metric = Literal["occupancy", "adr"]
METRICS: tuple[Metric, Metric] = ("occupancy", "adr")
METRIC_NOUN: dict[str, str] = {"occupancy": "occupancy", "adr": "ADR"}
GrowthState = Literal["sourced", "partially_sourced", "override", "unavailable", "withheld"]

#: The ontology source id each growth source is badged with
#: (``apps/worker/app/ontology/concepts.yaml`` ``sources:``).
GROWTH_SOURCE_IDS: dict[str, str] = {
    "analyst_override": "analyst_override",
    "cbre_horizons": "cbre_horizons",
    "costar_submarket_forecast": "costar_submarket_forecast",
    "carried_forward": "growth_carried_forward",
    "withheld_by_policy": "costar_withheld_by_policy",
    "unavailable": "market_forecast_unavailable",
}

#: The engine's accepted growth band (``GrowthFraction``).
_GROWTH_MIN, _GROWTH_MAX = -0.50, 0.50

_CBRE_CHANGE_RE = re.compile(
    r"^cbre_horizons\.segment_all\.(?P<year>(?:19|20)\d{2})\.(?P<metric>occupancy|adr)_change_pct$"
)
_CBRE_PERIOD_RE = re.compile(r"^cbre_horizons\.segment_all\.(?P<year>(?:19|20)\d{2})\.period$")

NO_CLOSE_DATE_DETAIL = (
    "No acquisition close date — a market forecast cannot be placed on the model "
    "calendar. Set the acquisition close date, or set growth manually."
)


# ─────────────────────────── positioning → class bucket ───────────────────────────

_BUCKET_BY_SCALE: dict[str, str] = {
    "luxury": "luxury_upper_upscale",
    "upper upscale": "luxury_upper_upscale",
    "upscale": "upscale_upper_midscale",
    "upper midscale": "upscale_upper_midscale",
    "midscale": "midscale_economy",
    "economy": "midscale_economy",
}


def class_bucket_from_positioning(value: object) -> str:
    """The CoStar class bucket for ``deals.positioning`` (web tier ids such as
    ``upper-upscale`` or STR labels such as ``Upper Upscale``). Anything else
    — blank, ``default``, ``lifestyle`` — is ``all`` (the overall series,
    labelled "All classes — no positioning tier set")."""
    if not isinstance(value, str):
        return "all"
    s = re.sub(r"[\s_\-]+", " ", value.strip().lower())
    s = s.removesuffix(" class").strip()
    return _BUCKET_BY_SCALE.get(s, "all")


def vendor_of(row: FieldRow) -> Literal["str", "costar", "cbre", "other"]:
    """The data vendor behind a row (``topline-growth-plan.md`` §3.4).

    ``COSTAR`` documents and the MARKET_STUDY lane are CoStar: the class-bucket
    performance appendix the submarket reader parses is CoStar's layout, and
    the lane is where CoStar hospitality reports land. Treating every market
    study as CoStar is the conservative reading for the licensing policy (a
    ``withhold`` tenant never gets a default from one)."""
    t = (row.doc_type or "").upper()
    if t in {"STR", "STR_TREND"}:
        return "str"
    if t in {"COSTAR", "MARKET_STUDY"}:
        return "costar"
    if t == "CBRE_HORIZONS":
        return "cbre"
    return "other"


def costar_withheld(policy: TenantDataPolicies, *, origin: str = "own_upload") -> bool:
    """Does the tenant's policy withhold a CoStar-derived default of ``origin``?

    ``own_upload`` — a CoStar report the client uploaded to its own deal
    (``costar_derived_defaults``, allow by default); ``licensed`` — CoStar data
    Fondok licenses and loads itself (``costar_licensed_defaults``, withhold by
    default until the licence covers the tenant). Every row the resolvers read
    today comes off the deal's own uploads (tenant- and deal-scoped)."""
    if origin == "licensed":
        return policy.withholds_licensed_costar
    return policy.withholds_costar


# ─────────────────────────── inputs / outputs ───────────────────────────


@dataclass(frozen=True)
class GrowthOverrides:
    """What the analyst set (only analyst-applied keys; a seed is not here)."""

    occupancy_by_year: tuple[float | None, ...] = ()
    adr_by_year: tuple[float | None, ...] = ()
    occupancy_all_years: float | None = None
    adr_all_years: float | None = None
    revpar_all_years: float | None = None
    #: Which key supplied the all-years figure (``occupancy_growth`` or the
    #: legacy ``index_market_occupancy_growth``), for the label.
    occupancy_all_years_key: str = "occupancy_growth"
    adr_all_years_key: str = "adr_growth"


@dataclass(frozen=True)
class MetricGrowth:
    """One metric's growth INTO one year, with its source."""

    value: float | None
    source: GrowthSource
    label: str
    doc_name: str | None = None
    page: int | None = None
    report_date: str | None = None
    reason: ReasonCode | None = None
    #: CoStar bucket the figure was read from (``all`` = overall series).
    bucket: str | None = None
    #: Last year of the document series that produced it (carry-forward end).
    series_last_year: int | None = None
    #: "CBRE Horizons" / "CoStar <submarket> submarket" — for a carried label.
    report: str | None = None
    calendar_year: int | None = None

    @property
    def source_id(self) -> str:
        return GROWTH_SOURCE_IDS[self.source]

    def as_dict(self) -> dict[str, object]:
        return {
            "value": self.value,
            "source": self.source,
            "source_id": self.source_id,
            "label": self.label,
            "doc_name": self.doc_name,
            "page": self.page,
            "report_date": self.report_date,
            "bucket": self.bucket,
            "bucket_label": CLASS_BUCKET_LABELS.get(self.bucket) if self.bucket else None,
            "reason": self.reason.value if self.reason is not None else None,
        }


@dataclass
class GrowthResolution:
    """Per model year (index ``y − 1``) and per calendar year, per metric."""

    hold_years: int
    calendar_years: list[int]
    by_year: dict[str, list[MetricGrowth]]
    #: ``(metric, calendar year)`` → resolution, for every calendar year the
    #: resolver was asked about (model years AND the benchmark's gap years
    #: before Year 1). Gap years carry the all-years overrides, never a
    #: per-year (model-year) override.
    by_calendar: dict[tuple[str, int], MetricGrowth] = field(default_factory=dict)
    class_bucket: str = "all"
    bucket_label: str = CLASS_BUCKET_LABELS["all"]
    #: Why the requested bucket is not the one read (overall series served).
    bucket_note: str | None = None
    #: CoStar submarket reports extracted before the class series existed.
    reextract_needed: list[str] = field(default_factory=list)

    @property
    def calendar_known(self) -> bool:
        return bool(self.calendar_years)

    def year(self, y: int, metric: str) -> MetricGrowth:
        return self.by_year[metric][y - 1]

    def calendar(self, cal: int, metric: str) -> MetricGrowth | None:
        return self.by_calendar.get((metric, cal))

    def state(self, years: Sequence[int]) -> GrowthState:
        """Overall growth state over the model ``years`` that are APPLIED."""
        picks = [self.year(y, m) for y in years for m in METRICS if 1 <= y <= self.hold_years]
        if not picks:
            return "unavailable"
        have = [p for p in picks if p.value is not None]
        if not have:
            return "withheld" if any(p.source == "withheld_by_policy" for p in picks) else "unavailable"
        if len(have) < len(picks):
            return "partially_sourced"
        if all(p.source == "analyst_override" for p in have):
            return "override"
        return "sourced"


# ─────────────────────────── document series ───────────────────────────


@dataclass(frozen=True)
class _DocYear:
    value: float
    period: str | None
    doc_name: str | None
    page: int | None
    report_date: str | None


def _cbre_series(rows: Sequence[FieldRow]) -> tuple[dict[str, dict[int, _DocYear]], str | None]:
    """``{metric: {calendar year: change}}`` off the newest CBRE extraction
    that carries the All Hotels change rows, plus its publication date."""
    by_ext: dict[str | None, list[FieldRow]] = {}
    for r in rows:
        if r.lname.startswith("cbre_horizons."):
            by_ext.setdefault(r.extraction_id, []).append(r)
    for ext_rows in by_ext.values():  # rows arrive newest extraction first
        periods: dict[int, str] = {}
        for r in ext_rows:
            m = _CBRE_PERIOD_RE.match(r.lname)
            if m and isinstance(r.value, str):
                periods.setdefault(int(m.group("year")), r.value.strip().lower())
        report_date = next(
            (
                r.value.strip()
                for r in ext_rows
                if r.lname == "cbre_horizons.publication_date" and isinstance(r.value, str) and r.value.strip()
            ),
            None,
        ) or next((r.report_as_of for r in ext_rows if r.report_as_of), None)
        out: dict[str, dict[int, _DocYear]] = {"occupancy": {}, "adr": {}}
        for r in ext_rows:
            m = _CBRE_CHANGE_RE.match(r.lname)
            if not m:
                continue
            v = change_fraction(r)
            if v is None:
                continue
            year = int(m.group("year"))
            period = (r.period or periods.get(year) or "").strip().lower() or None
            out[m.group("metric")].setdefault(
                year, _DocYear(value=v, period=period, doc_name=r.doc_name, page=r.page, report_date=report_date)
            )
        if out["occupancy"] or out["adr"]:
            return out, report_date
    return {"occupancy": {}, "adr": {}}, None


def _is_actual(period: str | None) -> bool:
    return (period or "").startswith(("actual", "hist"))


def _fmt(v: float) -> str:
    return f"{v * 100:+.1f}%"


# ─────────────────────────── the resolver ───────────────────────────


def resolve_growth_by_year(
    rows: Sequence[FieldRow],
    overrides: GrowthOverrides,
    *,
    calendar_years: Sequence[int],
    hold_years: int,
    class_bucket: str,
    policy: TenantDataPolicies,
    first_calendar_year: int | None = None,
    gated_doc_types: Sequence[str] = (),
) -> GrowthResolution:
    """Resolve the growth INTO every model year ``1..hold_years`` (and every
    calendar year from ``first_calendar_year`` — the benchmark's first growth
    step — up to the last model year). See the module docstring."""
    hold = max(1, int(hold_years))
    cal = [int(c) for c in calendar_years][:hold]
    if len(cal) < hold:
        cal = []  # a partial calendar is no calendar
    gated = {t.upper() for t in gated_doc_types}
    market_gated = bool(gated & {"CBRE_HORIZONS", "MARKET_STUDY", "COSTAR"})

    # ── document series ──
    cbre, cbre_date = _cbre_series(rows)
    costar_reading = read_class_forecast(rows)
    series = costar_reading.series(class_bucket)
    costar_withhold = bool(series.years) and costar_withheld(policy)
    submarket = costar_reading.submarket or "submarket"
    costar_report = f"CoStar {submarket} submarket"
    bucket_label = series.label
    bucket_note: str | None = None
    if class_bucket == "all" and series.years:
        bucket_note = "All classes — no positioning tier set on the deal"
    elif series.fallback_reason and series.years:
        bucket_note = series.fallback_reason
    costar: dict[str, dict[int, _DocYear]] = {"occupancy": {}, "adr": {}}
    for cy in series.years:
        for metric, attr in (("occupancy", "occupancy_change_pct"), ("adr", "adr_change_pct")):
            v = getattr(cy, attr)
            if v is not None:
                costar[metric][cy.year] = _DocYear(
                    value=v, period=cy.period, doc_name=cy.doc_name, page=cy.page,
                    report_date=costar_reading.report_date,
                )
    reextract: list[str] = []
    if costar_reading.reason == "no_source" and costar_reading.detail:
        reextract.append(costar_reading.detail)

    def _doc(metric: str, c: int) -> MetricGrowth | None:
        hit = cbre[metric].get(c)
        if hit is not None:
            kind = "Actual" if _is_actual(hit.period) else "Forecast"
            label = f"{kind} · CBRE Hotel Horizons {c} · All Hotels"
            if hit.doc_name:
                label += f" · {hit.doc_name}" + (f" p.{hit.page}" if hit.page else "")
            return MetricGrowth(
                value=hit.value, source="cbre_horizons", label=label, doc_name=hit.doc_name,
                page=hit.page, report_date=hit.report_date or cbre_date,
                series_last_year=max(cbre[metric]), report="CBRE Hotel Horizons", calendar_year=c,
            )
        hit = costar[metric].get(c)
        if hit is not None:
            if costar_withhold:
                return MetricGrowth(
                    value=None, source="withheld_by_policy",
                    label=(
                        f"{costar_report} forecast {c} on file but not applied under this "
                        "workspace's data policy — set growth manually"
                    ),
                    doc_name=hit.doc_name, page=hit.page, reason=ReasonCode.LICENSE_WITHHELD,
                    bucket=series.bucket, calendar_year=c,
                )
            kind = "Actual" if _is_actual(hit.period) else "Forecast"
            label = f"{kind} · {costar_report} {c} · {bucket_label}"
            if hit.doc_name:
                label += f" · {hit.doc_name}" + (f" p.{hit.page}" if hit.page else "")
            return MetricGrowth(
                value=hit.value, source="costar_submarket_forecast", label=label,
                doc_name=hit.doc_name, page=hit.page, report_date=hit.report_date,
                bucket=series.bucket, series_last_year=max(costar[metric]),
                report=f"{costar_report} ({bucket_label})", calendar_year=c,
            )
        return None

    def _unavailable(metric: str, c: int | None, detail: str | None = None) -> MetricGrowth:
        noun = METRIC_NOUN[metric]
        if detail is None:
            if market_gated:
                detail = (
                    f"No market {noun} forecast knowable at the underwriting as-of date"
                    + (f" for {c}" if c else "")
                    + " — set growth manually"
                )
            else:
                detail = f"No market {noun} forecast" + (f" for {c}" if c else "") + " — set growth manually"
        return MetricGrowth(
            value=None, source="unavailable", label=detail,
            reason=ReasonCode.NOT_KNOWABLE_AS_OF if market_gated else ReasonCode.NO_MARKET_FORECAST,
            calendar_year=c,
        )

    # ── the document chain, calendar year by calendar year (carry-forward
    # needs the previous year whether or not the analyst overrode it) ──
    doc_by_cal: dict[tuple[str, int], MetricGrowth] = {}
    if cal:
        start = min(cal[0], first_calendar_year) if first_calendar_year is not None else cal[0]
        for metric in METRICS:
            prev: MetricGrowth | None = None
            for c in range(start, cal[-1] + 1):
                hit = _doc(metric, c)
                if hit is None and prev is not None and prev.value is not None and (
                    prev.series_last_year is not None and c > prev.series_last_year
                ):
                    hit = MetricGrowth(
                        value=prev.value, source="carried_forward",
                        label=(
                            f"Assumption · carried forward from {prev.report} {prev.series_last_year}"
                            f" ({_fmt(prev.value)})"
                        ),
                        doc_name=prev.doc_name, page=prev.page, report_date=prev.report_date,
                        bucket=prev.bucket, series_last_year=prev.series_last_year,
                        report=prev.report, calendar_year=c,
                    )
                if hit is None:
                    hit = _unavailable(metric, c)
                doc_by_cal[(metric, c)] = hit
                prev = hit

    # ── analyst overrides on top ──
    def _scalar(metric: str, c: int | None, occ_value: float | None) -> MetricGrowth | None:
        if metric == "occupancy" and overrides.occupancy_all_years is not None:
            v = overrides.occupancy_all_years
            key = overrides.occupancy_all_years_key
            return MetricGrowth(
                value=v, source="analyst_override",
                label=f"Analyst override · {_fmt(v)} occupancy growth, all years"
                + ("" if key == "occupancy_growth" else f" (legacy key {key}, read as all-years growth)"),
                calendar_year=c,
            )
        if metric == "adr" and overrides.adr_all_years is not None:
            v = overrides.adr_all_years
            key = overrides.adr_all_years_key
            return MetricGrowth(
                value=v, source="analyst_override",
                label=f"Analyst override · {_fmt(v)} ADR growth, all years"
                + ("" if key == "adr_growth" else f" (legacy key {key}, read as all-years growth)"),
                calendar_year=c,
            )
        if metric == "adr" and overrides.revpar_all_years is not None:
            occ = occ_value if occ_value is not None else 0.0
            derived = (1.0 + overrides.revpar_all_years) / (1.0 + occ) - 1.0
            if _GROWTH_MIN <= derived <= _GROWTH_MAX:
                return MetricGrowth(
                    value=derived, source="analyst_override",
                    label=(
                        f"Derived from your {_fmt(overrides.revpar_all_years)} RevPAR growth override "
                        f"over {_fmt(occ)} occupancy growth"
                    ),
                    calendar_year=c,
                )
        return None

    def _resolve(metric: str, y: int | None, c: int | None, occ_value: float | None) -> MetricGrowth:
        lst = overrides.occupancy_by_year if metric == "occupancy" else overrides.adr_by_year
        if y is not None and 0 <= y - 1 < len(lst) and lst[y - 1] is not None:
            v = float(lst[y - 1])  # type: ignore[arg-type]
            return MetricGrowth(
                value=v, source="analyst_override",
                label=f"Analyst override · Year {y} {METRIC_NOUN[metric]} growth {_fmt(v)}",
                calendar_year=c,
            )
        hit = _scalar(metric, c, occ_value)
        if hit is not None:
            return hit
        if c is None:
            return _unavailable(metric, None, NO_CLOSE_DATE_DETAIL)
        return doc_by_cal.get((metric, c)) or _unavailable(metric, c)

    by_year: dict[str, list[MetricGrowth]] = {"occupancy": [], "adr": []}
    by_cal: dict[tuple[str, int], MetricGrowth] = {}
    for y in range(1, hold + 1):
        c = cal[y - 1] if cal else None
        occ = _resolve("occupancy", y, c, None)
        adr = _resolve("adr", y, c, occ.value)
        by_year["occupancy"].append(occ)
        by_year["adr"].append(adr)
        if c is not None:
            by_cal[("occupancy", c)] = occ
            by_cal[("adr", c)] = adr
    if cal and first_calendar_year is not None:
        for c in range(first_calendar_year, cal[0]):
            occ = _resolve("occupancy", None, c, None)
            by_cal[("occupancy", c)] = occ
            by_cal[("adr", c)] = _resolve("adr", None, c, occ.value)

    return GrowthResolution(
        hold_years=hold,
        calendar_years=cal,
        by_year=by_year,
        by_calendar=by_cal,
        class_bucket=series.bucket if series.years else class_bucket,
        bucket_label=bucket_label if series.years else CLASS_BUCKET_LABELS.get(class_bucket, class_bucket),
        bucket_note=bucket_note,
        reextract_needed=reextract,
    )


def growth_year(res: GrowthResolution, y: int) -> GrowthYear:
    """The engine's per-year provenance record for model year ``y``."""
    occ, adr = res.year(y, "occupancy"), res.year(y, "adr")
    if occ.label == adr.label:
        label = occ.label
    else:
        label = f"Occupancy: {occ.label}; ADR: {adr.label}"
    doc = next((g for g in (adr, occ) if g.doc_name), None)
    bucket = next((g.bucket for g in (adr, occ) if g.bucket), None)
    return GrowthYear(
        year=y,
        calendar_year=res.calendar_years[y - 1] if res.calendar_known else None,
        occupancy_growth=occ.value,
        adr_growth=adr.value,
        occupancy_source=occ.source,
        adr_source=adr.source,
        class_bucket=bucket,  # type: ignore[arg-type]
        bucket_label=CLASS_BUCKET_LABELS.get(bucket) if bucket else None,
        doc_name=doc.doc_name if doc else None,
        page=doc.page if doc else None,
        report_date=doc.report_date if doc else None,
        label=label,
        reason=occ.reason or adr.reason,
    )


def coerce_growth_list(raw: object, *, max_years: int = 20) -> tuple[list[float | None], list[str]]:
    """``*_growth_by_year`` (list or JSON text) → fractions per model year and
    the problems that refuse a write. Entries: a fraction (``0.03``) or a
    whole percent (``3`` → 0.03); ``None`` / blank = not set. Outside
    −50 %..+50 % is a problem, never clamped."""
    import json

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return [], ["must be a list with one entry per model year (null = unset)"]
    if raw is None:
        return [], []
    if not isinstance(raw, (list, tuple)):
        return [], ["must be a list with one entry per model year (null = unset)"]
    out: list[float | None] = []
    problems: list[str] = []
    for idx, v in enumerate(list(raw)[:max_years], start=1):
        if v is None or (isinstance(v, str) and not v.strip()):
            out.append(None)
            continue
        f = coerce_float(v)
        if f is None:
            out.append(None)
            problems.append(f"Year {idx}: not a number")
            continue
        frac = f if abs(f) <= 1.0 else f / 100.0
        if not (_GROWTH_MIN <= frac <= _GROWTH_MAX):
            out.append(None)
            problems.append(f"Year {idx}: {f:g} is outside −50%..+50% growth")
            continue
        out.append(frac)
    return out, problems


__all__ = [
    "GROWTH_SOURCE_IDS",
    "GrowthOverrides",
    "GrowthResolution",
    "MetricGrowth",
    "class_bucket_from_positioning",
    "coerce_growth_list",
    "costar_withheld",
    "growth_year",
    "resolve_growth_by_year",
    "vendor_of",
]
