"""FON-116 / FON-117 — the topline builder (methodology v2).

Joins the two resolvers into the revenue engine's ``ToplineDrivers`` and the
``topline_status`` block the assumptions API serves
(``topline-growth-plan.md`` §2.4, §2.5, §4.1):

* :func:`read_topline_inputs` — what the analyst set (only keys the override
  loop APPLIED; a seed is never an input), the legacy keys translated with a
  visible reason, the stabilization year.
* :func:`build_topline` — the benchmark grown into every model year by the
  resolved growth, penetration (100 default) × ramp (85 / 95 default for a
  Year-3 stabilization), the engine's per-year growth lists, the status.

The benchmark is GROWN INTO Year 1: a level describes its own period (an STR
TTM to May 2025 describes 2025), so Year 1 on calendar 2026 is that level ×
(1 + the 2026 growth) — exactly how the reference Index Analysis tab builds
its competitive-set row. A level whose period already is Year 1's calendar
year is used as reported. The Year-1 growth entry is published (``GrowthYear``
year 1) only when it is applied. With no model calendar (no close date) the
level is Year 1 and the out-years grow by the analyst's per-year figures.

A growth step with no figure holds the benchmark flat for that step (growth 0
is the absence of growth, never a seeded rate); the year's growth source says
``unavailable`` and the status names it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fondok_schemas.reasons import ReasonCode
from fondok_schemas.underwriting import BenchmarkYear, ToplineDrivers, ToplineReference

from ..engines.revenue import (
    DEFAULT_PENETRATION_INDEX,
    OCCUPANCY_CAP,
    TOPLINE_METHODOLOGY_VERSION,
    _ramp_for_year,
    default_ramp_by_year,
)
from ..engines.stabilization import (
    DEFAULT_STABILIZATION_YEAR,
    first_full_operating_year_after_reopening,
)
from ..engines.timeline import parse_iso_date
from .index_methodology import (
    KEY_ARI_TARGET,
    KEY_ARI_TARGET_BY_YEAR,
    KEY_MPI_TARGET,
    KEY_MPI_TARGET_BY_YEAR,
    MAX_MODEL_YEARS,
    OVERRIDE_METHOD,
    coerce_target_list,
    validate_index_target,
)
from .market_fields import coerce_float
from .topline_benchmark import (
    FORCED_LEVEL_KEY,
    FORCED_LEVEL_VALUES,
    LEGACY_METHOD_TO_LEVEL,
    BenchmarkResolution,
)
from .topline_growth import (
    METRIC_NOUN,
    METRICS,
    GrowthOverrides,
    GrowthResolution,
    MetricGrowth,
    coerce_growth_list,
    growth_year,
)

#: New ``field_overrides`` keys (JSON lists, index ``y − 1`` = model year ``y``).
KEY_OCC_GROWTH_BY_YEAR = "occupancy_growth_by_year"
KEY_ADR_GROWTH_BY_YEAR = "adr_growth_by_year"
KEY_RAMP_BY_YEAR = "penetration_ramp_by_year"
TOPLINE_LIST_KEYS: frozenset[str] = frozenset({KEY_OCC_GROWTH_BY_YEAR, KEY_ADR_GROWTH_BY_YEAR, KEY_RAMP_BY_YEAR})
#: Retired keys read once with a visible reason.
LEGACY_OCC_GROWTH = "index_market_occupancy_growth"
LEGACY_ADR_GROWTH = "index_market_adr_growth"
LEGACY_STR_SEED_FLAG = "revenue_seed_from_str_forecast"

#: The platform suggestion (plan decision 8.1 (a)): offered as a labelled chip
#: when growth is not sourced, NEVER applied by the loader.
PLATFORM_REVPAR_SUGGESTION = 0.045
SUGGESTION_NOTE = "Platform suggestion 4.5% — no market forecast on file"

TOPLINE_STATUS_KEY = "__topline_status__"

#: FON-116 / FON-117 staged rollout (TEMPORARY — remove with the setting once
#: FON-117 is on in production; DEPLOY.md "Topline rollout switch").
#:   ``growth_only`` — FON-116: Year 1 from the subject's own basis, growth per
#:                     year from the resolver (``TOPLINE_BENCHMARK_ENABLED`` off,
#:                     the default).
#:   ``benchmark``   — FON-117: benchmark × penetration × ramp (switch on).
TOPLINE_MODES: tuple[str, str] = ("growth_only", "benchmark")


def topline_mode() -> str:
    """The topline rollout mode — the ONE reader of
    ``Settings.TOPLINE_BENCHMARK_ENABLED`` (read per call, so a restart with
    a new environment is the only way it changes)."""
    from ..config import get_settings

    return "benchmark" if bool(get_settings().TOPLINE_BENCHMARK_ENABLED) else "growth_only"

_RAMP_MAX = 1.5

#: Review I-1 — WHICH deals the default 85 / 95 ramp applies to. The ONE
#: policy point: the domain lead's decision is a change of this line.
#:   ``all_deals``          — every deal (Sam, 2026-11-01; current).
#:   ``repositioning_only`` — only a deal with a reopening date or an explicit
#:                            renovation displacement (Year-1 displacement %,
#:                            PIP closure schedule or displacement schedule):
#:                            the ramp is a lease-up concept; a stabilized
#:                            operating asset runs at the stabilized
#:                            penetration from Year 1.
#:   ``never``              — no default ramp on any deal.
#: An analyst's ``penetration_ramp_by_year`` always wins over the policy.
RAMP_POLICIES: tuple[str, ...] = ("all_deals", "repositioning_only", "never")
DEFAULT_RAMP_POLICY = "all_deals"

_DISPLACEMENT_KEYS = (
    "y1_occupancy_displacement_pct",
    "y1_adr_displacement_pct",
    "pip_displacement_overrides",
    "renovation_displacement_schedule",
)


def default_ramp_applies(base: Mapping[str, Any], policy: str = DEFAULT_RAMP_POLICY) -> bool:
    """Does the default ramp apply to the deal described by ``base`` under
    ``policy``? See :data:`DEFAULT_RAMP_POLICY`."""
    if policy == "all_deals":
        return True
    if policy == "never":
        return False
    if base.get("reopening_date") not in (None, ""):
        return True
    for key in _DISPLACEMENT_KEYS:
        v = base.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if v > 0:
                return True
        elif v:
            return True
    return False


def _ramp_policy_note(policy: str) -> str:
    return (
        f"the default ramp does not apply to this deal (ramp policy: {policy}) — the stabilized "
        "penetration applies from Year 1. Enter a ramp to phase it in."
    )


@dataclass(frozen=True)
class ToplineInputs:
    growth: GrowthOverrides
    penetration_occ: float | None = None
    penetration_adr: float | None = None
    penetration_occ_by_year: tuple[float | None, ...] = ()
    penetration_adr_by_year: tuple[float | None, ...] = ()
    ramp: tuple[float | None, ...] | None = None
    stabilization_year: int = DEFAULT_STABILIZATION_YEAR
    stabilization_source: str = "default_year_3"
    forced_level: str | None = None
    explicit_metrics: tuple[str, ...] = ()
    legacy_flags: tuple[dict[str, Any], ...] = ()
    problems: tuple[str, ...] = ()
    #: Review I-1 — the ramp policy in force, and whether it switched the
    #: default ramp off for this deal (``ramp`` is then ``()``).
    ramp_policy: str = DEFAULT_RAMP_POLICY
    ramp_off_by_policy: bool = False


def coerce_ramp_list(raw: Any) -> tuple[list[float | None], list[str]]:
    """``penetration_ramp_by_year`` → fractions (``0 < r ≤ 1.5``) per year
    ``1..S−1``; a whole percent (``85``) reads as 0.85. ``None`` / blank =
    no ramp for that year (100 %)."""
    import json

    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return [], ["must be a list with one entry per pre-stabilization year (null = unset)"]
    if raw is None:
        return [], []
    if not isinstance(raw, (list, tuple)):
        return [], ["must be a list with one entry per pre-stabilization year (null = unset)"]
    out: list[float | None] = []
    problems: list[str] = []
    for idx, v in enumerate(list(raw)[:MAX_MODEL_YEARS], start=1):
        if v is None or (isinstance(v, str) and not v.strip()):
            out.append(None)
            continue
        f = coerce_float(v)
        if f is not None and f > _RAMP_MAX and f <= _RAMP_MAX * 100:
            f = f / 100.0
        if f is None or not (0.0 < f <= _RAMP_MAX):
            out.append(None)
            problems.append(f"Year {idx}: ramp must be above 0 and at most 150% of the stabilized penetration")
            continue
        out.append(f)
    return out, problems


def _scalar_growth(v: Any) -> float | None:
    f = coerce_float(v)
    if f is None or not math.isfinite(f):
        return None
    f = f if abs(f) <= 1.0 else f / 100.0
    return f if -0.5 <= f <= 0.5 else None


def default_stabilization_year(base: Mapping[str, Any]) -> tuple[int, str]:
    """``(S, source)`` with no analyst value: Year 3 after the close, or Year 3
    after the first full operating year after a reopening date that places on
    the projection (FON-98) — NOT clamped to the hold (the engine says so)."""
    from ..engines.revenue import projection_start_year

    reopening = parse_iso_date(base.get("reopening_date"))
    close = base.get("acquisition_close_date")
    start = projection_start_year(
        close.isoformat() if isinstance(close, date) else (str(close) if close not in (None, "") else None)
    )
    if reopening is not None and start is not None:
        cal = first_full_operating_year_after_reopening(reopening) + DEFAULT_STABILIZATION_YEAR - 1
        s = cal - start + 1
        if s >= 1:
            return s, "default_year_3_reopening"
    return DEFAULT_STABILIZATION_YEAR, "default_year_3"


def read_topline_inputs(
    base: Mapping[str, Any],
    sources: Mapping[str, str],
    analyst_override_paths: set[str],
    *,
    str_noted_keys: frozenset[str] | set[str] = frozenset(),
    derived_adr_source: str = "derived_from_revpar_growth",
    ramp_policy: str = DEFAULT_RAMP_POLICY,
) -> ToplineInputs:
    """Everything the topline reads off the resolved assumptions."""
    applied = analyst_override_paths
    legacy: list[dict[str, Any]] = []
    problems: list[str] = []

    def analyst(key: str) -> Any:
        return base.get(key) if key in applied else None

    occ_list, p1 = coerce_growth_list(analyst(KEY_OCC_GROWTH_BY_YEAR))
    adr_list, p2 = coerce_growth_list(analyst(KEY_ADR_GROWTH_BY_YEAR))
    problems += [f"Occupancy growth by year — {p} (ignored)" for p in p1]
    problems += [f"ADR growth by year — {p} (ignored)" for p in p2]

    occ_all = _scalar_growth(analyst("occupancy_growth"))
    occ_key = "occupancy_growth"
    adr_all = _scalar_growth(analyst("adr_growth")) if sources.get("adr_growth") != derived_adr_source else None
    adr_key = "adr_growth"
    for legacy_key, metric in ((LEGACY_OCC_GROWTH, "occupancy"), (LEGACY_ADR_GROWTH, "adr")):
        v = _scalar_growth(analyst(legacy_key))
        if v is None:
            continue
        used = (metric == "occupancy" and occ_all is None) or (metric == "adr" and adr_all is None)
        if used:
            if metric == "occupancy":
                occ_all, occ_key = v, legacy_key
            else:
                adr_all, adr_key = v, legacy_key
        legacy.append(
            {
                "key": legacy_key,
                "value": v,
                "treatment": "read_as_all_years_growth" if used else "ignored_newer_key_set",
                "reason": ReasonCode.LEGACY_KEY_TRANSLATED.value,
                "detail": (
                    f"The retired Index Analysis market {METRIC_NOUN[metric]} growth is read as your "
                    f"all-years {METRIC_NOUN[metric]} growth."
                    if used
                    else f"The retired key is ignored: an all-years {METRIC_NOUN[metric]} growth is set."
                ),
            }
        )
    revpar_all = _scalar_growth(analyst("revpar_growth"))

    def pen(key: str) -> float | None:
        raw = analyst(key)
        if raw is None:
            return None
        pts, why = validate_index_target(raw)
        if pts is None:
            problems.append(f"{key} {raw!r}: {why} (ignored)")
        return pts

    ramp_raw = analyst(KEY_RAMP_BY_YEAR)
    ramp: tuple[float | None, ...] | None = None
    if ramp_raw is not None:
        lst, rp = coerce_ramp_list(ramp_raw)
        problems += [f"Ramp — {p} (ignored)" for p in rp]
        ramp = tuple(lst)
    ramp_off = False
    if ramp is None and not default_ramp_applies(base, ramp_policy):
        ramp, ramp_off = (), True

    from .engine_runner import STABILIZATION_YEAR_KEY, _coerce_stabilization_year

    s_raw = _coerce_stabilization_year(analyst(STABILIZATION_YEAR_KEY))
    if s_raw is not None:
        s, s_source = s_raw, "analyst_override"
    else:
        s, s_source = default_stabilization_year(base)

    forced: str | None = None
    raw_level = analyst(FORCED_LEVEL_KEY)
    if isinstance(raw_level, str) and raw_level.strip() in FORCED_LEVEL_VALUES:
        forced = None if raw_level.strip() == "auto" else raw_level.strip()
    else:
        method = base.get(OVERRIDE_METHOD)
        if isinstance(method, str) and method.strip() in LEGACY_METHOD_TO_LEVEL:
            mapped = LEGACY_METHOD_TO_LEVEL[method.strip()]
            legacy.append(
                {
                    "key": OVERRIDE_METHOD,
                    "value": method.strip(),
                    "treatment": f"read_as_{mapped}_level",
                    "reason": ReasonCode.LEGACY_KEY_TRANSLATED.value,
                    "detail": (
                        f"The retired Index Analysis method choice is read as the {mapped.replace('_', ' ')} "
                        "benchmark level."
                    ),
                }
            )
            forced = mapped

    if base.get(LEGACY_STR_SEED_FLAG) is not None:
        legacy.append(
            {
                "key": LEGACY_STR_SEED_FLAG,
                "value": base.get(LEGACY_STR_SEED_FLAG),
                "treatment": "ignored",
                "reason": ReasonCode.LEGACY_KEY_TRANSLATED.value,
                "detail": (
                    "The retired \"Use STR rates\" switch is ignored — the comp set is the "
                    "model's benchmark by default."
                ),
            }
        )
    explicit: list[str] = []
    for key, metric in (("starting_occupancy", "occupancy"), ("starting_adr", "adr")):
        if key not in applied:
            continue
        if key in str_noted_keys:
            legacy.append(
                {
                    "key": key,
                    "value": base.get(key),
                    "treatment": "treated_as_legacy_seed",
                    "reason": ReasonCode.LEGACY_KEY_TRANSLATED.value,
                    "detail": (
                        "This Year-1 rate was written by the retired \"Use STR rates\" button; it is "
                        "not your explicit value, so the benchmark × penetration × ramp sets the year."
                    ),
                }
            )
            continue
        explicit.append(metric)

    return ToplineInputs(
        growth=GrowthOverrides(
            occupancy_by_year=tuple(occ_list),
            adr_by_year=tuple(adr_list),
            occupancy_all_years=occ_all,
            adr_all_years=adr_all,
            revpar_all_years=revpar_all,
            occupancy_all_years_key=occ_key,
            adr_all_years_key=adr_key,
        ),
        penetration_occ=pen(KEY_MPI_TARGET),
        penetration_adr=pen(KEY_ARI_TARGET),
        penetration_occ_by_year=tuple(coerce_target_list(analyst(KEY_MPI_TARGET_BY_YEAR))),
        penetration_adr_by_year=tuple(coerce_target_list(analyst(KEY_ARI_TARGET_BY_YEAR))),
        ramp=ramp,
        stabilization_year=s,
        stabilization_source=s_source,
        forced_level=forced,
        explicit_metrics=tuple(explicit),
        legacy_flags=tuple(legacy),
        problems=tuple(problems),
        ramp_policy=ramp_policy,
        ramp_off_by_policy=ramp_off,
    )


#: Review I-4 / R2-I1 — where the model calendar comes from: the deal's own
#: close date, else its underwriting as-of date. Nothing else places it.
CALENDAR_ANCHOR_CLOSE = "acquisition_close"
CALENDAR_ANCHOR_LABELS: dict[str, str] = {
    CALENDAR_ANCHOR_CLOSE: "the acquisition close",
    "underwriting_as_of": "the underwriting as-of date",
}


@dataclass(frozen=True)
class CalendarAnchor:
    """The date the model years are placed from, and why.

    Operating Year 1 is the calendar year of the first operating month (the
    anchor + 1 month — ``revenue.projection_start_year``, FON-41): a
    2025-09-30 close puts Year 1 on 2025, a 2025-12-31 close on 2026. With no
    close date the anchor is ASSUMED from the underwriting as-of date and used
    only to place a document forecast; the statement's calendar stays blank.
    With neither there is no anchor (R2-I1)."""

    source: str | None
    date: date | None
    assumed: bool

    @property
    def first_model_year(self) -> int | None:
        from ..engines.revenue import projection_start_year

        return projection_start_year(self.date.isoformat()) if self.date is not None else None

    @property
    def label(self) -> str:
        if self.source is None or self.date is None:
            return "No acquisition close date and no underwriting as-of date — the model years carry no calendar."
        where = CALENDAR_ANCHOR_LABELS.get(self.source, self.source)
        if not self.assumed:
            return (
                f"Model calendar from {where} {self.date.isoformat()}: Year 1 = {self.first_model_year} "
                "(the calendar year of the first operating month)."
            )
        return (
            f"No acquisition close date — the market forecast is placed on a calendar assumed from {where} "
            f"{self.date.isoformat()} (Year 1 = {self.first_model_year}). Set the acquisition close date to "
            "place it exactly."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "date": self.date.isoformat() if self.date is not None else None,
            "assumed": self.assumed,
            "first_model_year": self.first_model_year,
            "label": self.label,
        }


NO_CALENDAR_ANCHOR = CalendarAnchor(source=None, date=None, assumed=False)


def calendar_anchor(
    close: date | None,
    *,
    underwriting_as_of: date | None,
) -> CalendarAnchor:
    """The model-calendar anchor: the acquisition close; else (assumed) the
    underwriting as-of date; else none. Review round 2 (R2-I1): no comp-report
    date and never the run date — a deal's forecast must not move with the
    clock. With neither date a document forecast stays unplaced
    (``calendar_unknown``)."""
    if close is not None:
        return CalendarAnchor(source=CALENDAR_ANCHOR_CLOSE, date=close, assumed=False)
    if underwriting_as_of is not None:
        return CalendarAnchor(source="underwriting_as_of", date=underwriting_as_of, assumed=True)
    return NO_CALENDAR_ANCHOR


@dataclass(frozen=True)
class LongRunGrowth:
    """A source's own long-run growth figure (CBRE Hotel Horizons "Long Run
    Average" RevPAR change, else its ADR change) — the terminal growth
    convention when one is on file (review I-2)."""

    value: float
    source_id: str
    label: str


@dataclass
class ToplineBuild:
    drivers: ToplineDrivers
    occupancy_growth_by_year: list[float | None]
    adr_growth_by_year: list[float | None]
    status: dict[str, Any]
    #: The un-displaced Year-1 rate the topline sets per metric (benchmark ×
    #: penetration × ramp, occupancy under the cap); ``None`` = not set by it.
    year1_occupancy: float | None = None
    year1_adr: float | None = None
    #: Returns' terminal growth when the analyst set none (review I-2): the
    #: source's long-run average, else the last projected year's ADR growth
    #: (occupancy held at its stabilized level), else 0; and its source id.
    terminal_revpar_growth: float = 0.0
    terminal_revpar_source: str = "market_forecast_unavailable"
    applied_years: list[int] = field(default_factory=list)


def _step_summary(steps: Sequence[MetricGrowth]) -> str:
    parts: list[str] = []
    for g in steps:
        c = g.calendar_year
        if g.value is None:
            parts.append(f"{c}: held flat — no figure")
        else:
            kind = {
                "analyst_override": "your override",
                "cbre_horizons": "CBRE forecast",
                "costar_submarket_forecast": "CoStar forecast",
                "carried_forward": "past the forecast",
                "held_at_stabilization": "held after stabilization",
            }.get(g.source, g.source)
            parts.append(f"{c}: {g.value * 100:+.1f}% ({kind})")
    return "; ".join(parts)


def build_topline(
    bench: BenchmarkResolution,
    growth: GrowthResolution,
    inputs: ToplineInputs,
    *,
    hold_years: int,
    t12: ToplineReference | None,
    long_run: LongRunGrowth | None = None,
) -> ToplineBuild:
    hold = max(1, int(hold_years))
    cal = growth.calendar_years
    base_year = bench.base_calendar_year
    levels = {"occupancy": bench.occupancy, "adr": bench.adr}

    bench_years: list[BenchmarkYear] = []
    year1_growth_applied = False
    notes: list[str] = []
    for y in range(1, hold + 1):
        c = cal[y - 1] if cal else None
        values: dict[str, float | None] = {}
        all_steps: list[MetricGrowth] = []
        for metric in METRICS:
            level = levels[metric]
            if bench.level == "none" or level is None or metric in inputs.explicit_metrics:
                values[metric] = None
                continue
            if c is None or base_year is None:
                steps = [growth.year(k, metric) for k in range(2, y + 1)]
            elif c < base_year:
                values[metric] = None
                if metric == "occupancy":
                    notes.append(
                        f"Year {y} ({c}) is before the benchmark's own period ({bench.period_label}); "
                        "no benchmark is shown for it."
                    )
                continue
            else:
                steps = []
                for k in range(base_year + 1, c + 1):
                    g = growth.calendar(k, metric)
                    if g is not None:
                        steps.append(g)
                if y == 1 and steps:
                    year1_growth_applied = True
            v = float(level)
            for g in steps:
                v *= 1.0 + (g.value if g.value is not None else 0.0)
            if metric == "occupancy" and not (0.0 < v <= 1.0):
                values[metric] = None
                notes.append(f"Year {y}: the benchmark occupancy grown to {v:.1%} is not a rate; not used.")
                continue
            values[metric] = v
            all_steps.extend(steps)
        if values.get("occupancy") is None and values.get("adr") is None:
            continue
        if not all_steps:
            basis = bench.basis or "actual"
            label = f"{'Forecast' if basis == 'forecast' else 'Actual'} · {bench.label}"
        else:
            doc_only = all(g.source in ("cbre_horizons", "costar_submarket_forecast") for g in all_steps)
            basis = "forecast" if doc_only else "assumption"
            label = (
                f"{'Forecast' if doc_only else 'Assumption'} · {bench.label}, grown to {c} — "
                + _step_summary([g for g in all_steps])
            )
        occ_v, adr_v = values.get("occupancy"), values.get("adr")
        bench_years.append(
            BenchmarkYear(
                year=y,
                calendar_year=c,
                occupancy=occ_v,
                adr=adr_v,
                revpar=(occ_v * adr_v) if occ_v is not None and adr_v is not None else None,
                basis=basis,  # type: ignore[arg-type]
                level=bench.level,
                label=label,
                doc_name=bench.doc_name,
                page=bench.page,
            )
        )

    applied_years = ([1] if year1_growth_applied else []) + list(range(2, hold + 1))
    growth_years = [growth_year(growth, y) for y in applied_years]
    occ_list: list[float | None] = [None] * hold
    adr_list: list[float | None] = [None] * hold
    for gy in growth_years:
        occ_list[gy.year - 1] = gy.occupancy_growth
        adr_list[gy.year - 1] = gy.adr_growth

    drivers = ToplineDrivers(
        methodology_version=TOPLINE_METHODOLOGY_VERSION,
        benchmark_level=bench.level,
        benchmark_label=bench.label,
        benchmark_fallback_reason=bench.fallback_reason,
        benchmark_by_year=bench_years,
        growth_by_year=growth_years,
        stabilization_year=inputs.stabilization_year,
        penetration_occ=inputs.penetration_occ if inputs.penetration_occ is not None else DEFAULT_PENETRATION_INDEX,
        penetration_adr=inputs.penetration_adr if inputs.penetration_adr is not None else DEFAULT_PENETRATION_INDEX,
        penetration_occ_source="analyst_override" if inputs.penetration_occ is not None else "default",
        penetration_adr_source="analyst_override" if inputs.penetration_adr is not None else "default",
        ramp_by_year=list(inputs.ramp) if inputs.ramp is not None else None,
        ramp_policy_note=_ramp_policy_note(inputs.ramp_policy) if inputs.ramp_off_by_policy else None,
        penetration_occ_by_year=list(inputs.penetration_occ_by_year)[:hold],
        penetration_adr_by_year=list(inputs.penetration_adr_by_year)[:hold],
        explicit_year1=list(inputs.explicit_metrics),
        t12_reference=t12,
    )

    # Year-1 rate the topline sets (same arithmetic as the engine's plan).
    year1: dict[str, float | None] = {"occupancy": None, "adr": None}
    b1 = next((b for b in bench_years if b.year == 1), None)
    ramp1, _state, _note = _ramp_for_year(drivers, 1, hold)
    for metric, p, by_year in (
        ("occupancy", drivers.penetration_occ, drivers.penetration_occ_by_year),
        ("adr", drivers.penetration_adr, drivers.penetration_adr_by_year),
    ):
        bv = getattr(b1, metric) if b1 is not None else None
        if bv is None:
            continue
        v_override = by_year[0] if by_year else None
        pen = v_override if v_override is not None else (p * ramp1 if ramp1 is not None else p)
        v = bv * pen / 100.0
        year1[metric] = min(OCCUPANCY_CAP, v) if metric == "occupancy" else v

    # Returns' terminal growth (when the analyst set no revpar_growth) —
    # review I-2: never a single (recovery) year's occupancy gain carried in
    # perpetuity. The source's long-run average when one is on file, else the
    # last projected year's ADR growth with occupancy held at its stabilized
    # level, else 0 (no figure).
    terminal = _terminal_growth(growth.year(hold, "adr"), hold, long_run)
    term, term_source = terminal["value"], terminal["source_id"]

    state = growth.state(applied_years)
    status = _status(
        bench, growth, inputs, drivers,
        hold=hold, applied_years=applied_years, state=state, notes=notes, t12=t12,
        terminal=terminal,
    )
    return ToplineBuild(
        drivers=drivers,
        occupancy_growth_by_year=occ_list,
        adr_growth_by_year=adr_list,
        status=status,
        year1_occupancy=year1["occupancy"],
        year1_adr=year1["adr"],
        terminal_revpar_growth=term,
        terminal_revpar_source=term_source,
        applied_years=applied_years,
    )


def _legacy_flags_for(flags: Sequence[dict[str, Any]], bench: BenchmarkResolution) -> list[dict[str, Any]]:
    """Review M-2 — a Year-1 rate the retired "Use STR rates" button wrote is
    set aside for the benchmark × penetration × ramp ONLY when a benchmark is
    in use; with none (as-of gated, forced level unavailable) it stays the
    Year-1 rate, and the flag must say so."""
    out: list[dict[str, Any]] = []
    for flag in flags:
        if flag.get("treatment") == "treated_as_legacy_seed" and bench.level == "none":
            flag = {
                **flag,
                "treatment": "kept_no_benchmark",
                "detail": (
                    "This Year-1 rate was written by the retired \"Use STR rates\" button. No benchmark "
                    "is in use, so it stays the Year-1 rate (badged STR comp set) — enter your own value "
                    "or clear it."
                ),
            }
        out.append(dict(flag))
    return out


def _terminal_growth(last_adr: MetricGrowth, hold: int, long_run: LongRunGrowth | None) -> dict[str, Any]:
    """``{value, source_id, basis, label}`` of the exit-year growth convention."""
    if long_run is not None:
        return {
            "value": long_run.value,
            "source_id": long_run.source_id,
            "basis": "long_run_average",
            "label": f"{long_run.label} (the source's long-run average)",
        }
    if last_adr.value is not None:
        return {
            "value": last_adr.value,
            "source_id": last_adr.source_id,
            "basis": "last_year_adr_growth",
            "label": (
                f"Year {hold} ADR growth {last_adr.value * 100:+.1f}% — occupancy held at its "
                f"stabilized level ({last_adr.label})"
            ),
        }
    return {
        "value": 0.0,
        "source_id": "market_forecast_unavailable",
        "basis": "unavailable",
        "label": f"No ADR growth for Year {hold} and no long-run average on file — the exit NOI is not grown",
    }


def _status(
    bench: BenchmarkResolution,
    growth: GrowthResolution,
    inputs: ToplineInputs,
    drivers: ToplineDrivers,
    *,
    hold: int,
    applied_years: list[int],
    state: str,
    notes: list[str],
    t12: ToplineReference | None,
    terminal: dict[str, Any],
) -> dict[str, Any]:
    """The ``topline_status`` block (API contract — see the topline report)."""
    s = drivers.stabilization_year
    if inputs.ramp_off_by_policy:
        ramp = []
        ramp_source = "not_applied_by_policy"
        ramp_state = "defined"
    elif drivers.ramp_by_year is not None:
        ramp = list(drivers.ramp_by_year)
        ramp_source = "analyst_override"
        ramp_state = "defined"
    else:
        ramp = default_ramp_by_year(s)
        ramp_source = "default"
        ramp_state = "undefined_for_stabilization_year" if s >= 4 else "defined"
    if s > hold:
        ramp_state = "stabilization_after_hold"

    def current_index(metric: str) -> dict[str, Any]:
        published = bench.published_mpi if metric == "occupancy" else bench.published_ari
        if published is not None:
            return {
                "value": published,
                "label": f"Current {'MPI' if metric == 'occupancy' else 'ARI'} {published:.1f} "
                f"(STR {bench.index_period_label or 'TTM'})",
                "basis": "str_published",
            }
        level = bench.occupancy if metric == "occupancy" else bench.adr
        subject = (t12.occupancy if metric == "occupancy" else t12.adr) if t12 is not None else None
        if level and subject:
            v = subject / level * 100.0
            return {
                "value": round(v, 2),
                "label": f"Subject T-12 ÷ benchmark = {v:.1f}",
                "basis": "t12_over_benchmark",
            }
        return {"value": None, "label": "No current index — needs the STR report or a T-12", "basis": None}

    growth_rows: list[dict[str, Any]] = []
    for y in range(1, hold + 1):
        occ, adr = growth.year(y, "occupancy"), growth.year(y, "adr")
        growth_rows.append(
            {
                "year": y,
                "calendar_year": growth.calendar_years[y - 1] if growth.calendar_known else None,
                "applied": y in applied_years,
                "occupancy": occ.as_dict(),
                "adr": adr.as_dict(),
            }
        )
    suggestions: list[dict[str, Any]] = []
    if state in ("unavailable", "withheld", "partially_sourced") and inputs.growth.revpar_all_years is None:
        suggestions.append(
            {
                "key": "revpar_growth",
                "value": PLATFORM_REVPAR_SUGGESTION,
                "label": "Apply platform suggestion 4.5% RevPAR growth (labelled; not a market forecast)",
                "note_prefill": SUGGESTION_NOTE,
            }
        )
    return {
        "methodology_version": TOPLINE_METHODOLOGY_VERSION,
        "benchmark": bench.as_dict(),
        "benchmark_level": bench.level,
        "benchmark_label": bench.label,
        "benchmark_fallback_reason": bench.fallback_reason,
        "benchmark_by_year": [b.model_dump(mode="json") for b in drivers.benchmark_by_year],
        "class_bucket": growth.class_bucket,
        "bucket_label": growth.bucket_label,
        "bucket_note": growth.bucket_note,
        "calendar_known": growth.calendar_known,
        "calendar_unknown": growth.calendar_unknown,
        "growth_state": state,
        "growth_by_year": growth_rows,
        "stabilization_year": s,
        "stabilization_source": inputs.stabilization_source,
        "penetration_occ": drivers.penetration_occ,
        "penetration_adr": drivers.penetration_adr,
        "penetration_occ_source": drivers.penetration_occ_source,
        "penetration_adr_source": drivers.penetration_adr_source,
        "penetration_occ_by_year": drivers.penetration_occ_by_year,
        "penetration_adr_by_year": drivers.penetration_adr_by_year,
        "current_mpi": current_index("occupancy"),
        "current_ari": current_index("adr"),
        "ramp_by_year": ramp,
        "ramp_source": ramp_source,
        "ramp_policy": inputs.ramp_policy,
        "ramp_state": ramp_state,
        "explicit_metrics": list(inputs.explicit_metrics),
        "t12_reference": t12.model_dump(mode="json") if t12 is not None else None,
        "terminal_revpar_growth": dict(terminal),
        "suggestions": suggestions,
        "legacy_flags": _legacy_flags_for(inputs.legacy_flags, bench),
        "problems": list(inputs.problems),
        "notes": notes,
        "reextract_needed": list(growth.reextract_needed),
    }


__all__ = [
    "KEY_ADR_GROWTH_BY_YEAR",
    "KEY_OCC_GROWTH_BY_YEAR",
    "KEY_RAMP_BY_YEAR",
    "CalendarAnchor",
    "DEFAULT_RAMP_POLICY",
    "RAMP_POLICIES",
    "default_ramp_applies",
    "LongRunGrowth",
    "NO_CALENDAR_ANCHOR",
    "calendar_anchor",
    "LEGACY_ADR_GROWTH",
    "LEGACY_OCC_GROWTH",
    "LEGACY_STR_SEED_FLAG",
    "PLATFORM_REVPAR_SUGGESTION",
    "TOPLINE_LIST_KEYS",
    "TOPLINE_MODES",
    "TOPLINE_STATUS_KEY",
    "ToplineBuild",
    "ToplineInputs",
    "build_topline",
    "coerce_ramp_list",
    "default_stabilization_year",
    "read_topline_inputs",
    "topline_mode",
]
