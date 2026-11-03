"""FON-116 / FON-117 — the topline benchmark resolver (topline v2).

ONE mechanism picks the level the subject is penetrated against
(``topline-growth-plan.md`` §2.2), first available wins unless the analyst
forced a level (``topline_benchmark_level``):

1. ``comp_set`` — the STR competitive set (subject TTM ÷ MPI / ARI from one
   STR report — the blend the Market tab shows), else a CoStar Property
   Analytics comp set. Available only when BOTH occupancy and ADR exist.
2. ``submarket`` — the CoStar submarket report's latest ACTUAL year for the
   deal's class bucket (else the overall series, labelled).
3. ``market`` — market-wide figures in the market reports
   (``pnl_benchmark.market.*`` / ``ttm_performance.market.*`` /
   ``market_study.market.*``), else CBRE Hotel Horizons All Hotels levels.
4. ``none`` — ``fallback_reason`` names what was missing at each level.

The resolver returns the LEVEL and the calendar year it describes
(``base_calendar_year``); ``services.topline`` grows it by the resolved
growth into every model year. A CoStar-derived level under a ``withhold``
data policy is skipped with that reason. Pure: no DB, no I/O (the loader
filters rows dated after the underwriting as-of date before calling).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from fondok_schemas.underwriting import BenchmarkLevel

from ..tenants import TenantDataPolicies
from .index_methodology import (
    METHOD_COSTAR_COMP_SET,
    METHOD_MARKET_BENCHMARK,
    METHOD_STR_COMP_SET,
    Figure,
    period_end_text,
    read_costar_comp_set,
    read_market_benchmark,
    read_str_comp_set,
)
from .market_fields import FieldRow, coerce_float
from .market_study_reader import read_class_forecast
from .topline_growth import costar_withheld, vendor_of

LEVELS: tuple[BenchmarkLevel, ...] = ("comp_set", "submarket", "market")
LEVEL_LABELS: dict[str, str] = {
    "comp_set": "Competitive set",
    "submarket": "Submarket",
    "market": "Market",
    "none": "No benchmark",
}
#: ``topline_benchmark_level`` values (a note-exempt METHOD choice).
FORCED_LEVEL_KEY = "topline_benchmark_level"
FORCED_LEVEL_VALUES: frozenset[str] = frozenset({"auto", *LEVELS})
#: The legacy ``index_methodology`` choice, translated (reported as a legacy flag).
LEGACY_METHOD_TO_LEVEL: dict[str, str] = {
    METHOD_STR_COMP_SET: "comp_set",
    METHOD_COSTAR_COMP_SET: "comp_set",
    METHOD_MARKET_BENCHMARK: "market",
}

_CBRE_LEVEL_RE = re.compile(
    r"^cbre_horizons\.segment_all\.(?P<year>(?:19|20)\d{2})\.(?P<attr>occupancy_pct|adr_usd|period)$"
)


@dataclass(frozen=True)
class BenchmarkResolution:
    level: BenchmarkLevel
    #: "STR comp set TTM May 2025 · ANG-20250500-USD-E.xlsx"
    label: str
    #: Ontology source id stamped on ``starting_*`` when this level drives them.
    source_id: str | None = None
    occupancy: float | None = None
    adr: float | None = None
    #: The calendar year the level describes (a TTM's end year); ``None`` when
    #: the report states no period — Year 1 is then the level as reported.
    base_calendar_year: int | None = None
    period_label: str | None = None
    basis: Literal["actual", "forecast"] | None = None
    doc_name: str | None = None
    page: int | None = None
    #: Why each higher level was skipped (in order), joined.
    fallback_reason: str | None = None
    skipped: tuple[tuple[str, str], ...] = ()
    forced_level: str | None = None
    #: The published STR penetration indices (index points) for the TTM the
    #: comp-set level was read from — printed beside the 100 default.
    published_mpi: float | None = None
    published_ari: float | None = None
    index_period_label: str | None = None
    bucket: str | None = None
    bucket_label: str | None = None
    inputs: list[dict[str, object]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "level": self.level,
            "level_label": LEVEL_LABELS[self.level],
            "label": self.label,
            "source_id": self.source_id,
            "occupancy": self.occupancy,
            "adr": self.adr,
            "revpar": (self.occupancy * self.adr) if self.occupancy is not None and self.adr is not None else None,
            "base_calendar_year": self.base_calendar_year,
            "period_label": self.period_label,
            "basis": self.basis,
            "doc_name": self.doc_name,
            "page": self.page,
            "fallback_reason": self.fallback_reason,
            "skipped": [{"level": lv, "reason": why} for lv, why in self.skipped],
            "forced_level": self.forced_level,
            "bucket": self.bucket,
            "bucket_label": self.bucket_label,
            "inputs": self.inputs,
        }


def _year_of(text: str | None) -> int | None:
    if not text:
        return None
    m = re.match(r"^((?:19|20)\d{2})", str(text).strip())
    return int(m.group(1)) if m else None


def _first_ref(fig: Figure) -> tuple[str | None, int | None]:
    for ref in fig.inputs:
        if ref.doc_name:
            return ref.doc_name, ref.page
    return None, None


def _refs(*figs: Figure) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for fig in figs:
        for ref in fig.inputs:
            out.append({"field_name": ref.field_name, "value": ref.value, "doc_name": ref.doc_name, "page": ref.page})
    return out


def _figure_year(fig: Figure, rows: Sequence[FieldRow]) -> int | None:
    """The calendar year a level figure describes: its period end, else its
    period label as a year, else (a TTM with no stated end) the document's
    own report date."""
    y = _year_of(fig.period_end) or _year_of(fig.period_label)
    if y is not None:
        return y
    names = {ref.field_name.lower() for ref in fig.inputs}
    for r in rows:
        if r.lname in names and r.report_as_of:
            return _year_of(r.report_as_of)
    return None


def _comp_set(rows: Sequence[FieldRow], policy: TenantDataPolicies) -> tuple[BenchmarkResolution | None, str]:
    method, mpi, ari = read_str_comp_set(rows)
    if method.available:
        occ, adr = method.occupancy, method.adr
        end = occ.period_end or adr.period_end
        doc, page = _first_ref(occ)
        period = f"TTM {period_end_text(end)}" if end else "TTM"
        return (
            BenchmarkResolution(
                level="comp_set",
                label=f"STR comp set {period}" + (f" · {doc}" if doc else ""),
                source_id="str_comp_set",
                occupancy=occ.value,
                adr=adr.value,
                base_calendar_year=_year_of(end) or _figure_year(occ, rows),
                period_label=period,
                basis="actual",
                doc_name=doc,
                page=page,
                published_mpi=mpi.value,
                published_ari=ari.value,
                index_period_label=period,
                inputs=_refs(occ, adr),
            ),
            "",
        )
    str_reason = method.disabled_reason or "No STR comp set on this deal."
    costar = read_costar_comp_set(rows)
    if costar.available:
        costar_rows = [r for r in rows if r.lname.startswith("costar_comp_set.")]
        if costar_rows and costar_withheld(policy):
            return None, (
                f"{str_reason} A CoStar comp set is on file but not applied under this "
                "workspace's data policy."
            )
        occ, adr = costar.occupancy, costar.adr
        doc, page = _first_ref(occ)
        period = occ.period_label or "reported"
        return (
            BenchmarkResolution(
                level="comp_set",
                label=f"CoStar comp set {period}" + (f" · {doc}" if doc else ""),
                source_id="costar_comp_set",
                occupancy=occ.value,
                adr=adr.value,
                base_calendar_year=_figure_year(occ, rows),
                period_label=period,
                basis="actual",
                doc_name=doc,
                page=page,
                inputs=_refs(occ, adr),
            ),
            "",
        )
    return None, f"{str_reason} {costar.disabled_reason or ''}".strip()


def _submarket(
    rows: Sequence[FieldRow], class_bucket: str, policy: TenantDataPolicies
) -> tuple[BenchmarkResolution | None, str]:
    reading = read_class_forecast(rows)
    series = reading.series(class_bucket)
    if not series.years:
        return None, series.fallback_reason or "No CoStar submarket performance series on this deal."
    if costar_withheld(policy):
        return None, (
            "A CoStar submarket report is on file but not applied under this workspace's data policy."
        )
    actual = [cy for cy in series.years if cy.period == "actual" and cy.occupancy_pct and cy.adr_usd]
    if not actual:
        return None, f"The {series.label} submarket series has no actual year with occupancy and ADR."
    cy = actual[-1]
    submarket = reading.submarket or "submarket"
    label = f"CoStar {submarket} submarket {series.label} {cy.year} actual"
    if series.fallback_reason or series.bucket == "all":
        label += " (overall series — " + (
            "bucket not in report" if series.fallback_reason else "no positioning tier set"
        ) + ")"
    if cy.doc_name:
        label += f" · {cy.doc_name}" + (f" p.{cy.page}" if cy.page else "")
    return (
        BenchmarkResolution(
            level="submarket",
            label=label,
            source_id="costar_submarket",
            occupancy=cy.occupancy_pct,
            adr=cy.adr_usd,
            base_calendar_year=cy.year,
            period_label=f"{cy.year} actual",
            basis="actual",
            doc_name=cy.doc_name,
            page=cy.page,
            bucket=series.bucket,
            bucket_label=series.label,
            inputs=[
                {"field_name": ref.field_name, "value": ref.value, "doc_name": ref.doc_name, "page": ref.page}
                for key, ref in cy.refs.items()
                if key in ("occupancy_pct", "adr_usd")
            ],
        ),
        "",
    )


def _market(rows: Sequence[FieldRow], policy: TenantDataPolicies) -> tuple[BenchmarkResolution | None, str]:
    reasons: list[str] = []
    eligible = list(rows)
    if costar_withheld(policy):
        withheld = [r for r in rows if vendor_of(r) == "costar"]
        if withheld:
            eligible = [r for r in rows if vendor_of(r) != "costar"]
            reasons.append("CoStar market figures are on file but not applied under this workspace's data policy.")
    reading = read_market_benchmark(eligible, segment="market")
    if reading.available:
        occ, adr = reading.occupancy, reading.adr
        doc, page = _first_ref(occ)
        names = {ref.field_name.lower() for ref in occ.inputs}
        vendor = next((vendor_of(r) for r in eligible if r.lname in names), "other")
        period = occ.period_label or "reported"
        return (
            BenchmarkResolution(
                level="market",
                label=f"Market {period}" + (f" · {doc}" if doc else ""),
                source_id="costar_market" if vendor == "costar" else "market_benchmark",
                occupancy=occ.value,
                adr=adr.value,
                base_calendar_year=_figure_year(occ, eligible),
                period_label=period,
                basis="actual",
                doc_name=doc,
                page=page,
                inputs=_refs(occ, adr),
            ),
            "",
        )
    reasons.append(reading.disabled_reason or "No market-wide occupancy / ADR in the market reports.")
    # CBRE Hotel Horizons All Hotels levels: the latest ACTUAL year with both
    # figures, else the first forecast year.
    by_year: dict[int, dict[str, FieldRow]] = {}
    for r in eligible:
        m = _CBRE_LEVEL_RE.match(r.lname)
        if m:
            by_year.setdefault(int(m.group("year")), {}).setdefault(m.group("attr"), r)
    complete: list[tuple[int, str, float, float, FieldRow]] = []
    for year, cells in sorted(by_year.items()):
        o = coerce_float(cells["occupancy_pct"].value) if "occupancy_pct" in cells else None
        a = coerce_float(cells["adr_usd"].value) if "adr_usd" in cells else None
        if o is None or a is None or o <= 0 or a <= 0:
            continue
        o = o / 100.0 if o > 1.5 else o
        period = (
            str(cells["period"].value).strip().lower()
            if "period" in cells and isinstance(cells["period"].value, str)
            else ""
        )
        kind = "actual" if period.startswith(("actual", "hist")) else "forecast"
        complete.append((year, kind, o, a, cells["occupancy_pct"]))
    if complete:
        actuals = [c for c in complete if c[1] == "actual"]
        year, kind, o, a, row = actuals[-1] if actuals else complete[0]
        label = f"CBRE Hotel Horizons All Hotels {year} {kind}"
        if row.doc_name:
            label += f" · {row.doc_name}" + (f" p.{row.page}" if row.page else "")
        return (
            BenchmarkResolution(
                level="market",
                label=label,
                source_id="cbre_horizons",
                occupancy=o,
                adr=a,
                base_calendar_year=year,
                period_label=f"{year} {kind}",
                basis="actual" if kind == "actual" else "forecast",
                doc_name=row.doc_name,
                page=row.page,
            ),
            "",
        )
    reasons.append("No CBRE Hotel Horizons All Hotels occupancy / ADR on this deal.")
    return None, " ".join(reasons)


def resolve_benchmark(
    rows: Sequence[FieldRow],
    *,
    class_bucket: str,
    forced_level: str | None,
    policy: TenantDataPolicies,
    gated_doc_types: Sequence[str] = (),
) -> BenchmarkResolution:
    """Pick the benchmark level (see the module docstring)."""
    readers = {
        "comp_set": lambda: _comp_set(rows, policy),
        "submarket": lambda: _submarket(rows, class_bucket, policy),
        "market": lambda: _market(rows, policy),
    }
    forced = forced_level if forced_level in LEVELS else None
    order = [forced] if forced else list(LEVELS)
    skipped: list[tuple[str, str]] = []
    for level in order:
        hit, why = readers[level]()  # type: ignore[index]
        if hit is not None:
            reason = " ".join(f"{LEVEL_LABELS[lv]}: {w}" for lv, w in skipped) or None
            return BenchmarkResolution(
                **{
                    **hit.__dict__,
                    "fallback_reason": reason,
                    "skipped": tuple(skipped),
                    "forced_level": forced,
                }
            )
        skipped.append((level, why))
    gated = sorted({t.upper() for t in gated_doc_types})
    if gated:
        skipped.append(("none", f"Reports dated after the underwriting as-of date were not read ({', '.join(gated)})."))
    reason = " ".join(f"{LEVEL_LABELS[lv]}: {w}" for lv, w in skipped)
    if forced:
        reason = f"You chose the {LEVEL_LABELS[forced].lower()} level, which is not available — {reason}"
    return BenchmarkResolution(
        level="none",
        label="No benchmark — projecting from the subject's own starting occupancy and ADR",
        fallback_reason=reason,
        skipped=tuple(skipped),
        forced_level=forced,
    )


__all__ = [
    "BenchmarkResolution",
    "FORCED_LEVEL_KEY",
    "FORCED_LEVEL_VALUES",
    "LEGACY_METHOD_TO_LEVEL",
    "LEVELS",
    "LEVEL_LABELS",
    "resolve_benchmark",
]
