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
   series the previous year was read from: ADR grows at the report's
   PUBLISHED forecast average when it states one (FON-121 decision 2 —
   ``market_study.performance.forecast_avg_adr_change_pct``, CoStar's
   overview "Forecast Average", the submarket overall: "CoStar forecast
   average (as published) +2.5% … p.4", naming the class the yearly rows
   are when they are a bucket), else at the ARITHMETIC AVERAGE of that
   series' forecast-tagged ADR growth years (decision 1a, labelled
   "computed" with its years — "re-extract" when the extraction predates
   v11). CBRE Hotel Horizons publishes no forecast average in the
   extraction contract, so a CBRE series uses the computed one. OCCUPANCY
   HOLDS at its last level (growth 0) — a
   forecast's recovery-year occupancy gain cannot repeat every year (review
   I-2; the 95 % cap still applies). Both are assumptions, labelled with the
   report they extend. A series with no forecast-tagged ADR year has no
   average: the year is ``unavailable``.
5. ``unavailable`` — nothing at all: the figure is ``None`` and the engine
   holds the metric flat (``flat_no_growth``). Growth 0 is the absence of
   growth, never a seeded rate.

FON-121 — the model years are DEAL years (twelve months from the close,
``engines.deal_years``). The document chain above is resolved per CALENDAR
year (forecasts are published that way); a deal year's figure is the
month-weighted blend of the calendar years it spans —
g(Oct 2026–Sep 2027) = 3/12 × g(2026) + 9/12 × g(2027) — every part must
have a figure (else the year is unavailable / withheld, never a partial
figure), and a part past the forecast makes the year an assumption. The
label lists every part; ``chip`` shows the pages ("CoStar p.26, p.4").
The exit-NOI year (hold + 1) is resolved the same way (``forward``).

Occupancy is FLAT after stabilization (domain lead 2026-11-03, decision 1b):
with ``stabilization_year`` = S, the occupancy growth INTO every model year
after S is 0 with source ``held_at_stabilization`` — whatever a document, a
carry-forward or the analyst's all-years figure says — so occupancy holds at
its Year-S level. Only the analyst's PER-YEAR occupancy figure for that year
still applies (labelled as theirs). ADR is untouched. The held years are not
counted as a sourced figure in the growth state.

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
from dataclasses import dataclass, field, replace
from typing import Literal

from fondok_schemas.reasons import ReasonCode
from fondok_schemas.underwriting import GrowthSource, GrowthYear

from ..engines.deal_years import DealYear, deal_year_after
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
    "held_at_stabilization": "occupancy_held_at_stabilization",
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

#: Review round 2 (R2-I1) — a market forecast is on file but the deal has no
#: close date and no underwriting as-of date: the forecast is not placed on a
#: guessed calendar (never the run date); every document year says this.
CALENDAR_UNKNOWN_DETAIL = "Set the acquisition close date to place the market forecast"


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
    #: Decision 1a — the arithmetic average of the series' forecast-tagged
    #: growth years for this metric, and the first / last such year.
    forecast_average: float | None = None
    forecast_span: tuple[int, int] | None = None
    #: "CoStar" / "CBRE" — the vendor of a document-derived figure.
    vendor: str | None = None
    #: FON-121 — the deal year this figure is the growth INTO (None for a
    #: calendar-year figure), and its calendar parts ``(year, months, figure)``.
    deal_year: DealYear | None = None
    parts: tuple[tuple[int, int, MetricGrowth], ...] = ()
    #: Decision 2 — a post-forecast figure's basis: ``published_average``
    #: (the report's own) or ``computed_average`` (its forecast years).
    average_basis: str | None = None
    #: Decision 3 — the visible source chip ("CoStar p.26").
    chip: str | None = None
    #: The calendar year's part of a blended label ("2027 forecast p.26").
    short: str | None = None
    #: An analyst override typed for THIS model year (not the all-years one).
    per_year_override: bool = False

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
            "chip": self.chip,
            "average_basis": self.average_basis,
            "deal_year": self.deal_year.as_dict() if self.deal_year is not None else None,
            "calendar_parts": [
                {
                    "calendar_year": cy,
                    "months": m,
                    "value": g.value,
                    "source": g.source,
                    "page": g.page,
                    "label": g.label,
                }
                for cy, m, g in self.parts
            ],
        }


@dataclass
class GrowthResolution:
    """Per model (deal) year (index ``y − 1``), the exit-NOI year after the
    hold, and per calendar year, per metric."""

    hold_years: int
    #: FON-121 — the deal years the model years are (``[]`` = no calendar).
    deal_years: list[DealYear]
    by_year: dict[str, list[MetricGrowth]]
    #: The exit-NOI year (hold + 1), per metric — empty without a calendar.
    forward: dict[str, MetricGrowth] = field(default_factory=dict)
    #: ``(metric, calendar year)`` → the calendar year's figure (document,
    #: carry-forward, or the analyst's ALL-years figure — never a per-year
    #: one), for every calendar year the deal years (and the benchmark's
    #: first step) touch.
    by_calendar: dict[tuple[str, int], MetricGrowth] = field(default_factory=dict)
    class_bucket: str = "all"
    bucket_label: str = CLASS_BUCKET_LABELS["all"]
    #: Why the requested bucket is not the one read (overall series served).
    bucket_note: str | None = None
    #: CoStar submarket reports extracted before the class series existed.
    reextract_needed: list[str] = field(default_factory=list)
    #: R2-I1 — a document forecast is on file but no calendar places it.
    calendar_unknown: bool = False

    @property
    def calendar_known(self) -> bool:
        return bool(self.deal_years)

    def year(self, y: int, metric: str) -> MetricGrowth:
        return self.by_year[metric][y - 1]

    def deal_year(self, y: int) -> DealYear | None:
        return self.deal_years[y - 1] if 1 <= y <= len(self.deal_years) else None

    def calendar(self, cal: int, metric: str) -> MetricGrowth | None:
        return self.by_calendar.get((metric, cal))

    def state(self, years: Sequence[int]) -> GrowthState:
        """Overall growth state over the model ``years`` that are APPLIED."""
        picks = [
            p
            for p in (self.year(y, m) for y in years for m in METRICS if 1 <= y <= self.hold_years)
            # Decision 1b — a year held flat after stabilization is a rule,
            # not a market figure the deal has (or lacks).
            if p.source != "held_at_stabilization"
        ]
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


_DOC_CHANGE_RE = re.compile(
    r"^(?:cbre_horizons\.segment_all|market_study\.class\.[a-z_]+|market_study\.trend)"
    r"\.(?:19|20)\d{2}\.(?:occupancy|adr)_change_pct$"
)


def document_growth_on_file(rows: Sequence[FieldRow]) -> bool:
    """Does any row carry a per-calendar-year occupancy / ADR change (CBRE
    Horizons All Hotels, a CoStar class or overall series)? Review I-4: such
    a forecast is placed even without a close date (on an assumed calendar),
    never reported as "no market forecast"."""
    return any(_DOC_CHANGE_RE.match(r.lname) for r in rows)


def _is_actual(period: str | None) -> bool:
    return (period or "").startswith(("actual", "hist"))


def _fmt(v: float) -> str:
    return f"{v * 100:+.1f}%"


# ─────────────────────────── the resolver ───────────────────────────


def _pages_chip(vendor: str | None, pages: Sequence[int | None]) -> str | None:
    """Decision 3 — the visible source chip: "CoStar p.26", "CoStar p.26, p.4"."""
    if not vendor:
        return None
    seen: list[int] = []
    for p in pages:
        if p is not None and p not in seen:
            seen.append(p)
    return vendor + ("" if not seen else " " + ", ".join(f"p.{p}" for p in seen))


def _blend_short(g: MetricGrowth) -> str:
    """One calendar year's part of a blended deal-year label."""
    what = g.short or g.label
    return f"{_fmt(g.value)} {what}" if g.value is not None else what


def resolve_growth_by_year(
    rows: Sequence[FieldRow],
    overrides: GrowthOverrides,
    *,
    deal_years: Sequence[DealYear],
    hold_years: int,
    class_bucket: str,
    policy: TenantDataPolicies,
    benchmark_base_year: int | None = None,
    gated_doc_types: Sequence[str] = (),
    stabilization_year: int | None = None,
) -> GrowthResolution:
    """Resolve the growth INTO every model (deal) year ``1..hold_years`` and
    the exit-NOI year after it (FON-121: deal years run twelve months from
    the close — ``engines.deal_years``), plus every calendar year from the
    benchmark's first growth step (``benchmark_base_year`` + 1) up to the
    last calendar year the exit-NOI year touches. See the module docstring."""
    hold = max(1, int(hold_years))
    s_year = int(stabilization_year) if stabilization_year is not None and int(stabilization_year) >= 1 else None
    dys = list(deal_years)[:hold]
    if len(dys) < hold:
        dys = []  # a partial calendar is no calendar
    fwd_dy = deal_year_after(dys)
    gated = {t.upper() for t in gated_doc_types}
    market_gated = bool(gated & {"CBRE_HORIZONS", "MARKET_STUDY", "COSTAR"})
    #: R2-I1 — a forecast on file that no calendar can place.
    calendar_unknown = not dys and document_growth_on_file(rows)

    # ── document series ──
    cbre, cbre_date = _cbre_series(rows)
    costar_reading = read_class_forecast(rows)
    series = costar_reading.series(class_bucket)
    costar_withhold = bool(series.years) and costar_withheld(policy)
    submarket = costar_reading.submarket or "submarket"
    costar_report = f"CoStar {submarket} submarket"
    bucket_label = series.label
    bucket_note: str | None = None
    #: Review I-3 — the report predates the class series and the deal's
    #: bucket was never read: every CoStar-only year says "re-extract".
    stale_extraction = costar_reading.predates_class_series and not series.years
    if class_bucket == "all" and series.years:
        bucket_note = "All classes — no positioning tier set on the deal"
    elif series.fallback_reason and (series.years or stale_extraction):
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
    def _forecast_average(series_by_year: dict[int, _DocYear]) -> tuple[float | None, tuple[int, int] | None]:
        """Decision 1a — mean of the forecast-tagged years (``period`` not
        actual / historical; an untagged year reads as forecast, as its
        label does) — used when the report publishes no average."""
        fc = sorted(y for y, d in series_by_year.items() if not _is_actual(d.period))
        if not fc:
            return None, None
        return sum(series_by_year[y].value for y in fc) / len(fc), (fc[0], fc[-1])

    averages: dict[tuple[str, str], tuple[float | None, tuple[int, int] | None]] = {
        (src, metric): _forecast_average(series_by_year)
        for src, by_metric in (("cbre", cbre), ("costar", costar))
        for metric, series_by_year in by_metric.items()
    }
    #: FON-121 decision 2 — the CoStar report's PUBLISHED forecast average
    #: (its Average Trend table: the submarket overall). CBRE Hotel Horizons
    #: publishes no forecast average in the extraction contract (its
    #: long-run average is the exit convention, ``LongRunGrowth``).
    published_costar_adr = costar_reading.published_average("adr") if series.years else None

    reextract: list[str] = []
    if stale_extraction:
        reextract.append(series.fallback_reason or costar_reading.detail or "")
    elif costar_reading.reason == "no_source" and costar_reading.detail:
        reextract.append(costar_reading.detail)

    def _doc(metric: str, c: int) -> MetricGrowth | None:
        hit = cbre[metric].get(c)
        if hit is not None:
            kind = "Actual" if _is_actual(hit.period) else "Forecast"
            label = f"{kind} · CBRE Hotel Horizons {c} · All Hotels"
            if hit.doc_name:
                label += f" · {hit.doc_name}" + (f" p.{hit.page}" if hit.page else "")
            avg, span = averages[("cbre", metric)]
            return MetricGrowth(
                value=hit.value, source="cbre_horizons", label=label, doc_name=hit.doc_name,
                page=hit.page, report_date=hit.report_date or cbre_date,
                series_last_year=max(cbre[metric]), report="CBRE Hotel Horizons", calendar_year=c,
                forecast_average=avg, forecast_span=span, vendor="CBRE",
                short=f"CBRE {c} {kind.lower()}" + (f" p.{hit.page}" if hit.page else ""),
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
            avg, span = averages[("costar", metric)]
            return MetricGrowth(
                value=hit.value, source="costar_submarket_forecast", label=label,
                doc_name=hit.doc_name, page=hit.page, report_date=hit.report_date,
                bucket=series.bucket, series_last_year=max(costar[metric]),
                report=f"{costar_report} ({bucket_label})", calendar_year=c,
                forecast_average=avg, forecast_span=span, vendor="CoStar",
                short=f"CoStar {c} {kind.lower()}" + (f" p.{hit.page}" if hit.page else ""),
            )
        return None

    def _unavailable(metric: str, c: int | None, detail: str | None = None) -> MetricGrowth:
        noun = METRIC_NOUN[metric]
        if detail is None and stale_extraction and not market_gated:
            return MetricGrowth(
                value=None, source="unavailable",
                label=f"{c}: " + (series.fallback_reason or costar_reading.detail or "") if c else (
                    series.fallback_reason or costar_reading.detail or ""
                ),
                reason=ReasonCode.REEXTRACT_NEEDED,
                calendar_year=c,
            )
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

    def _past_series(metric: str, c: int, prev: MetricGrowth) -> MetricGrowth:
        """A calendar year past the end of the series ``prev`` was read from."""
        last = prev.series_last_year
        if metric == "occupancy":
            return MetricGrowth(
                value=0.0, source="carried_forward",
                label=f"Assumption · occupancy held at its {last} level — the {prev.report} forecast ends {last}",
                doc_name=prev.doc_name, page=prev.page, report_date=prev.report_date,
                bucket=prev.bucket, series_last_year=last, report=prev.report, calendar_year=c,
                forecast_average=prev.forecast_average, forecast_span=prev.forecast_span,
                vendor=prev.vendor, short=f"{c} occupancy held (forecast ends {last})",
            )
        if prev.vendor == "CoStar" and published_costar_adr is not None:
            # Decision 2 — the report's own published forecast average.
            pub = published_costar_adr
            overall_note = (
                f" — the report publishes it for the submarket overall; the per-year rows are {bucket_label}"
                if series.bucket != "all" else ""
            )
            label = (
                f"Assumption · CoStar forecast average (as published) {_fmt(pub.value)} · {submarket} "
                f"submarket{overall_note}"
                + (f" · {pub.doc_name}" if pub.doc_name else "")
                + (f" p.{pub.page}" if pub.page else "")
                + f" — the forecast ends {last}"
            )
            return MetricGrowth(
                value=pub.value, source="carried_forward", label=label,
                doc_name=pub.doc_name or prev.doc_name, page=pub.page, report_date=prev.report_date,
                bucket=prev.bucket, series_last_year=last, report=prev.report, calendar_year=c,
                forecast_average=prev.forecast_average, forecast_span=prev.forecast_span,
                vendor=prev.vendor, average_basis="published_average",
                short=f"{c} CoStar forecast average (as published" + (f", p.{pub.page})" if pub.page else ")"),
            )
        if prev.forecast_average is not None and prev.forecast_span is not None:
            # Decision 1a — no published average: the series' own forecast
            # years averaged, labelled as computed (never the last year's
            # figure carried on).
            value = prev.forecast_average
            first, fl = prev.forecast_span
            span = f"{first}–{fl}" if first != fl else f"{first}"
            hint = (
                "; re-extract the report to read its published average"
                if prev.vendor == "CoStar" and costar_reading.predates_published_average
                else "; the report publishes no average"
            )
            return MetricGrowth(
                value=value, source="carried_forward",
                label=(
                    f"Assumption · {prev.report} {span} forecast average, computed ({_fmt(value)}) — "
                    f"the forecast ends {last}{hint}"
                ),
                doc_name=prev.doc_name, page=prev.page, report_date=prev.report_date,
                bucket=prev.bucket, series_last_year=last, report=prev.report, calendar_year=c,
                forecast_average=prev.forecast_average, forecast_span=prev.forecast_span,
                vendor=prev.vendor, average_basis="computed_average",
                short=f"{c} {span} forecast average, computed" + (f" (p.{prev.page})" if prev.page else ""),
            )
        return MetricGrowth(
            value=None, source="unavailable",
            label=(
                f"No ADR growth for {c}: the {prev.report} series ends "
                f"{last} and has no forecast years to average — set growth manually"
            ),
            reason=ReasonCode.NO_MARKET_FORECAST, calendar_year=c,
        )

    # ── the document chain, calendar year by calendar year (carry-forward
    # needs the previous year whether or not the analyst overrode it) ──
    doc_by_cal: dict[tuple[str, int], MetricGrowth] = {}
    cal_range: list[int] = []
    if dys and fwd_dy is not None:
        first_cy = dys[0].calendar_months[0][0]
        if benchmark_base_year is not None:
            first_cy = min(first_cy, benchmark_base_year + 1)
        # The chain runs through the exit-NOI year (hold + 1): the exit
        # growth convention reads its own figure.
        cal_range = list(range(first_cy, fwd_dy.calendar_months[-1][0] + 1))
        for metric in METRICS:
            prev: MetricGrowth | None = None
            for c in cal_range:
                hit = _doc(metric, c)
                if hit is None and prev is not None and prev.value is not None and (
                    prev.series_last_year is not None and c > prev.series_last_year
                ):
                    hit = _past_series(metric, c, prev)
                if hit is None:
                    hit = _unavailable(metric, c)
                doc_by_cal[(metric, c)] = hit
                prev = hit

    def _blend(metric: str, dy: DealYear) -> MetricGrowth:
        """FON-121 — a deal year's document growth: the month-weighted blend
        of the calendar years it spans (3 months of 2026 + 9 months of 2027
        for Oct 2026–Sep 2027). Every part must have a figure: a missing
        part makes the year unavailable (never half a figure)."""
        parts = [(cy, m, doc_by_cal.get((metric, cy)) or _unavailable(metric, cy)) for cy, m in dy.calendar_months]
        if len(parts) == 1:
            g = parts[0][2]
            return replace(g, deal_year=dy, parts=tuple(parts), chip=_pages_chip(g.vendor, [g.page]))
        missing = [(cy, m, g) for cy, m, g in parts if g.value is None]
        first_doc = next((g for _cy, _m, g in parts if g.doc_name), None)
        if missing:
            withheld = any(g.source == "withheld_by_policy" for _cy, _m, g in missing)
            return MetricGrowth(
                value=None, source="withheld_by_policy" if withheld else "unavailable",
                label=f"{dy.label}: " + "; ".join(f"{m} mo of {cy} — {g.label}" for cy, m, g in missing),
                doc_name=first_doc.doc_name if first_doc else None,
                page=first_doc.page if first_doc else None,
                reason=missing[0][2].reason, deal_year=dy, parts=tuple(parts),
            )
        value = sum(m / 12.0 * float(g.value) for _cy, m, g in parts)  # type: ignore[arg-type]
        sources = [g.source for _cy, _m, g in parts]
        if len(set(sources)) == 1:
            source = sources[0]
        elif "carried_forward" in sources:
            source = "carried_forward"
        else:
            source = max(parts, key=lambda p: p[1])[2].source
        kinds = {("Assumption" if g.source == "carried_forward" else g.label.split(" · ", 1)[0]) for _c, _m, g in parts}
        kind = "Assumption" if "Assumption" in kinds else ("Forecast" if "Forecast" in kinds else "Actual")
        last = parts[-1][2]
        tail = last.report or ""
        if first_doc is not None and first_doc.doc_name:
            tail += f" · {first_doc.doc_name}"
        label = (
            f"{kind} · {dy.label} = "
            + " + ".join(f"{m} mo × {_blend_short(g)}" for _cy, m, g in parts)
            + f" = {_fmt(value)}"
            + (f" · {tail}" if tail else "")
        )
        vendor = next((g.vendor for _c, _m, g in parts if g.vendor), None)
        return MetricGrowth(
            value=value, source=source, label=label,  # type: ignore[arg-type]
            doc_name=first_doc.doc_name if first_doc else None,
            page=next((g.page for _c, _m, g in parts if g.page), None),
            report_date=last.report_date, bucket=last.bucket, series_last_year=last.series_last_year,
            report=last.report, forecast_average=last.forecast_average, forecast_span=last.forecast_span,
            vendor=vendor, deal_year=dy, parts=tuple(parts),
            average_basis=next((g.average_basis for _c, _m, g in parts if g.average_basis), None),
            chip=_pages_chip(vendor, [g.page for _c, _m, g in parts]),
        )

    # ── analyst overrides on top ──
    def _scalar(metric: str, occ_value: float | None) -> MetricGrowth | None:
        if metric == "occupancy" and overrides.occupancy_all_years is not None:
            v = overrides.occupancy_all_years
            key = overrides.occupancy_all_years_key
            return MetricGrowth(
                value=v, source="analyst_override",
                label=f"Analyst override · {_fmt(v)} occupancy growth, all years"
                + ("" if key == "occupancy_growth" else f" (legacy key {key}, read as all-years growth)"),
            )
        if metric == "adr" and overrides.adr_all_years is not None:
            v = overrides.adr_all_years
            key = overrides.adr_all_years_key
            return MetricGrowth(
                value=v, source="analyst_override",
                label=f"Analyst override · {_fmt(v)} ADR growth, all years"
                + ("" if key == "adr_growth" else f" (legacy key {key}, read as all-years growth)"),
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
                )
        return None

    def _resolve(metric: str, y: int | None, dy: DealYear | None, occ_value: float | None) -> MetricGrowth:
        lst = overrides.occupancy_by_year if metric == "occupancy" else overrides.adr_by_year
        if y is not None and 0 <= y - 1 < len(lst) and lst[y - 1] is not None:
            v = float(lst[y - 1])  # type: ignore[arg-type]
            return MetricGrowth(
                value=v, source="analyst_override",
                label=f"Analyst override · Year {y} {METRIC_NOUN[metric]} growth {_fmt(v)}",
                deal_year=dy, per_year_override=True,
            )
        if metric == "occupancy" and y is not None and s_year is not None and y > s_year:
            # Decision 1b — occupancy is flat after stabilization.
            where = f"Year {s_year} ({dys[s_year - 1].label})" if dys and s_year <= len(dys) else f"Year {s_year}"
            return MetricGrowth(
                value=0.0, source="held_at_stabilization",
                label=(
                    f"Assumption · occupancy held at its {where} stabilized level — occupancy is "
                    "flat after stabilization; ADR keeps growing"
                ),
                deal_year=dy,
            )
        hit = _scalar(metric, occ_value)
        if hit is not None:
            return replace(hit, deal_year=dy)
        if dy is None:
            # Review I-3 — a stale extraction is the more useful reason: the
            # report on file was never read for its forecast at all.
            if stale_extraction:
                return _unavailable(metric, None)
            if calendar_unknown and not stale_extraction:
                return MetricGrowth(
                    value=None, source="unavailable", label=CALENDAR_UNKNOWN_DETAIL,
                    reason=ReasonCode.CALENDAR_UNKNOWN,
                )
            return _unavailable(metric, None, NO_CLOSE_DATE_DETAIL)
        return _blend(metric, dy)

    by_year: dict[str, list[MetricGrowth]] = {"occupancy": [], "adr": []}
    for y in range(1, hold + 1):
        dy = dys[y - 1] if dys else None
        occ = _resolve("occupancy", y, dy, None)
        by_year["occupancy"].append(occ)
        by_year["adr"].append(_resolve("adr", y, dy, occ.value))
    forward: dict[str, MetricGrowth] = {}
    if fwd_dy is not None:
        # The exit-NOI year (hold + 1) — read by the exit growth convention.
        forward["occupancy"] = _resolve("occupancy", None, fwd_dy, None)
        forward["adr"] = _resolve("adr", None, fwd_dy, forward["occupancy"].value)
    by_cal: dict[tuple[str, int], MetricGrowth] = {}
    for c in cal_range:
        # Calendar-year figures (the benchmark's first step from its own
        # year): the all-years overrides apply, never a per-(deal-)year one.
        occ_c = _scalar("occupancy", None) or doc_by_cal[("occupancy", c)]
        by_cal[("occupancy", c)] = replace(occ_c, calendar_year=c)
        adr_c = _scalar("adr", occ_c.value) or doc_by_cal[("adr", c)]
        by_cal[("adr", c)] = replace(adr_c, calendar_year=c)

    return GrowthResolution(
        hold_years=hold,
        deal_years=dys,
        by_year=by_year,
        forward=forward,
        by_calendar=by_cal,
        class_bucket=series.bucket if series.years else class_bucket,
        bucket_label=bucket_label if series.years else CLASS_BUCKET_LABELS.get(class_bucket, class_bucket),
        bucket_note=bucket_note,
        reextract_needed=reextract,
        calendar_unknown=calendar_unknown and not stale_extraction,
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
        deal_year=res.deal_years[y - 1].span() if res.calendar_known else None,
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
        chip=merge_chips(adr.chip, occ.chip),
        reason=occ.reason or adr.reason,
    )


def merge_chips(*chips: str | None) -> str | None:
    """"CoStar p.26" + "CoStar p.4" → "CoStar p.26, p.4" (same vendor);
    different vendors are listed side by side."""
    by_vendor: dict[str, list[str]] = {}
    for chip in chips:
        if not chip:
            continue
        vendor, _, pages = chip.partition(" ")
        lst = by_vendor.setdefault(vendor, [])
        for p in (x.strip() for x in pages.split(",") if x.strip()):
            if p not in lst:
                lst.append(p)
    if not by_vendor:
        return None
    return " · ".join(v + (" " + ", ".join(ps) if ps else "") for v, ps in by_vendor.items())


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
    "document_growth_on_file",
    "growth_year",
    "merge_chips",
    "resolve_growth_by_year",
    "vendor_of",
]
