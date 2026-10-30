"""Revenue engine — rooms-revenue projection (occupancy x ADR x rooms-available).

Pure deterministic math. Given a starting occupancy and ADR, projects rooms,
F&B and ancillary revenue forward over the underwriting hold. Year 1 is
treated as the post-PIP stabilized year.

Wave 2 P2.1 — institutional revenue segmentation. When
``RevenueEngineInput.segments`` is non-empty, the engine projects rooms
revenue as Σ over five demand segments — transient_bar, transient_ota,
corporate, group, contract — each with its own ADR, mix share, and
channel-cost percentage. The canonical ``rooms_revenue`` line becomes NET
of channel cost so downstream P&L / Returns engines never double-count
distribution drag. When ``segments == []`` the engine runs the original
single-line path with byte-identical math; every legacy test still passes.

Wave 2 P2.4 — structured PIP displacement. When
``RevenueEngineInput.pip_displacement`` is populated, Y1 rooms revenue
scales by the monthly ``pct_rooms_offline_by_month`` schedule (with the
strategy-specific rules captured in ``_y1_revenue_factor_and_adr_drag``),
Y2 occupancy ramps linearly back to baseline over
``occupancy_recovery_months`` (after brand-multiplier adjustment), and Y2+
ADR is uplifted by ``revpar_index_post_reno``. When ``pip_displacement``
is ``None`` (or its strategy is ``"none"``) the engine falls back to the
legacy ``y1_*_displacement_pct`` math byte-for-byte.

Sam, 2026-10-15 — explicit renovation displacement SCHEDULE. When
``RevenueEngineInput.renovation_displacement_schedule`` is non-empty the
engine converts its periods (months from close × rooms offline × rate
discount) into one occupancy factor and one ADR factor per model year —
``renovation_displacement_factors`` holds the exact arithmetic — and applies
them to every year the schedule touches. The schedule is then the ONLY
displacement applied: the scalar ``y1_*_displacement_pct`` and the structured
``pip_displacement`` are ignored rather than stacked (never double-applied).
An empty schedule (the default) leaves every output byte-identical.

FON-116 / FON-117 — topline methodology v2 (``docs/plans/go-live-2026-10-30/
topline-growth-plan.md`` §2.4). When ``RevenueEngineInput.topline`` is set,
each year's un-displaced occupancy / ADR is ``benchmark × penetration ÷ 100``
where the penetration is the stabilized index ``P`` × the year's ramp ``r_y``
before stabilization (or a per-year override), occupancy held under
:data:`OCCUPANCY_CAP` and flagged ``capped_95``; a metric with no benchmark
runs on the subject's own starting level × growth with
``benchmark_unavailable`` and is NOT penetrated. Growth is one step per year
(``occupancy_growth_by_year`` / ``adr_growth_by_year``; ``None`` = held flat,
``flat_no_growth``); a constant list equal to the scalar assumption evaluates
to exactly the scalar expression, so with ``topline=None`` and the loader's
constant list every golden figure and trace is byte-identical. The output
carries one ``topline_by_year`` record per model year and the
``topline_methodology_version`` the run used.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from fondok_schemas.provenance import ValueInput, ValueTrace, apply_states
from fondok_schemas.underwriting import (
    IndexDriverYear,
    PIPDisplacement,
    RenovationDisplacementPeriod,
    RenovationDisplacementYear,
    RevenueEngineInput,
    RevenueEngineOutput,
    RevenueProjectionYear,
    SegmentYear,
    ToplineDrivers,
)

from .base import BaseEngine
from .timeline import parse_iso_date

DAYS_PER_YEAR = 365

# ──────── FON-116 / FON-117 — topline methodology (benchmark × penetration × ramp) ────────

#: The topline method this engine implements: the subject's occupancy / ADR are
#: benchmark × stabilized penetration × ramp per model year, with per-year
#: growth lists (``topline-growth-plan.md`` §2.4). Emitted on the output as
#: ``topline_methodology_version`` for a run ``RevenueEngineInput.topline``
#: drove; a run on the legacy scalar / Index Analysis driver path reports
#: :data:`LEGACY_TOPLINE_METHODOLOGY_VERSION`, so "0 headline changes" is never
#: claimed across methods (``scripts/compare_golden_baseline.py`` prints when
#: the two differ).
TOPLINE_METHODOLOGY_VERSION = 2
LEGACY_TOPLINE_METHODOLOGY_VERSION = 1

#: The occupancy ceiling every projected year is held under — the same 0.95
#: the compounding path has always applied; under the topline it is surfaced
#: on the year's record as ``capped_95`` instead of binding silently.
OCCUPANCY_CAP = 0.95

#: Stabilized penetration when the analyst set none: parity with the benchmark.
DEFAULT_PENETRATION_INDEX = 100.0

#: The default ramp (fraction of the stabilized penetration for years 1..S−1)
#: is DEFINED for stabilization in Year 3 ([0.85, 0.95]) and Year 2 ([0.85])
#: only. For S ≥ 4 Years 1–2 keep those two fractions and Years 3..S−1 are
#: undefined: they run at 100 % and the record says
#: ``undefined_for_stabilization_year`` — no interpolation is invented
#: (open decision 8.3 of the plan).
_DEFAULT_RAMP_Y1 = 0.85
_DEFAULT_RAMP_Y2 = 0.95
_TOPLINE_METHOD = "benchmark_penetration_ramp"


def default_ramp_by_year(stabilization_year: int) -> list[float | None]:
    """Default ramp fractions for years ``1..S−1`` (index ``y − 1``).

    ``[]`` for S ≤ 1, ``[0.85]`` for S = 2, ``[0.85, 0.95]`` for S = 3 and
    ``[0.85, 0.95, None, …]`` for S ≥ 4 — ``None`` = undefined for that year.
    """
    n = max(0, stabilization_year - 1)
    defined: list[float | None] = [_DEFAULT_RAMP_Y1, _DEFAULT_RAMP_Y2][:n]
    return defined + [None] * (n - len(defined))


def _compound(value: float, steps: Sequence[float | None]) -> float:
    """``value`` grown by one growth step per year, ``None`` = held flat.

    Consecutive equal steps are applied as one power — ``value × (1 + g) ^ n``
    — which is exactly the expression the scalar path has always evaluated, so
    a constant list reproduces the scalar projection to the last bit; a
    varying list is the product of those runs.
    """
    out = value
    i = 0
    n_steps = len(steps)
    while i < n_steps:
        g = steps[i]
        j = i + 1
        while j < n_steps and steps[j] == g:
            j += 1
        out = out * (1.0 + (g if g is not None else 0.0)) ** (j - i)
        i = j
    return out


def _growth_into(
    by_year: Sequence[float | None] | None, scalar: float, year: int
) -> tuple[float | None, bool]:
    """``(growth applied INTO model year ``year``, came-from-the-list)``.

    With no list the scalar carries every year (legacy). With a list, index
    ``year − 1`` is the figure; ``None`` — or a list that ends before the year
    — is "no growth figure": the metric is held flat, never seeded.
    """
    if by_year is None:
        return scalar, False
    idx = year - 1
    return (by_year[idx] if 0 <= idx < len(by_year) else None), True


@dataclass
class _ToplinePlan:
    """What the topline resolved for every model year, before displacement.

    ``occ_drivers`` / ``adr_drivers`` hold benchmark × penetration ÷ 100 per
    year (index ``y − 1``; ``None`` = that metric has no benchmark for the year
    and runs on the subject's own level × growth). ``records`` is the per-year
    provenance the engine fills with the figures that ran.
    """

    occ_drivers: list[float | None] = field(default_factory=list)
    adr_drivers: list[float | None] = field(default_factory=list)
    records: dict[int, IndexDriverYear] = field(default_factory=dict)


def _ramp_for_year(
    topline: ToplineDrivers, year: int, hold_years: int
) -> tuple[float | None, str, str | None]:
    """``(ramp fraction, ramp state, note)`` for one model year — §2.4.

    * ``y ≥ S`` → no ramp, ``stabilized``.
    * ``y < S`` with a fraction → ``P × r_y``, ``ramped``; when the hold ends
      before S the LAST projected year is ``stabilization_after_hold`` — it
      still runs at its ramp and the projection never reaches 100 %.
    * ``y < S`` without a fraction → 100 %: ``undefined_for_stabilization_year``
      when the default ramp is in use and undefined for the year (S ≥ 4, years
      3..S−1), ``no_ramp`` when the analyst's ramp has no entry for the year.
    """
    s = topline.stabilization_year  # as the analyst set it — NOT pre-clamped
    if year >= s:
        return None, "stabilized", None
    # The ONE place the hold clamps S for the math: only Years 1..hold are
    # ever evaluated (the loop runs ``payload.hold_years``), so an S past the
    # hold means the projection never reaches the stabilized penetration; the
    # last projected year carries that fact.
    after_hold = s > hold_years and year == hold_years
    after_hold_note = (
        f"Stabilization (Year {s}) is after the hold; Year {hold_years} runs at ramp "
        "{ramp} and the projection never reaches the stabilized penetration."
    )
    ramp = topline.ramp_by_year if topline.ramp_by_year is not None else default_ramp_by_year(s)
    r = ramp[year - 1] if 0 <= year - 1 < len(ramp) else None
    if r is None:
        if topline.ramp_by_year is None and s >= 4 and year >= 3:
            state, note = "undefined_for_stabilization_year", (
                f"Year {year}: the default ramp is defined for stabilization in Year 3 "
                f"(85 % / 95 %) only — with stabilization in Year {s} the ramp for "
                f"Years 3–{s - 1} is undefined, so the year runs at 100 % of the "
                f"stabilized penetration. Enter the ramp for Years 3–{s - 1}."
            )
        else:
            state, note = "no_ramp", (
                f"Year {year}: no ramp entered for the year — it runs at 100 % of the "
                "stabilized penetration."
            )
        if after_hold:
            note = f"{note} {after_hold_note.format(ramp='100 % (none defined)')}"
        return None, state, note
    if after_hold:
        return r, "stabilization_after_hold", after_hold_note.format(ramp=f"{r:g}")
    return r, "ramped", None


def _plan_topline(
    payload: RevenueEngineInput, calendar_years: Sequence[int]
) -> _ToplinePlan:
    """Resolve §2.4 for every model year: the per-year driver values the loop
    applies and the provenance record each year is echoed on.

    ``pen_y = V_y`` (per-year override) | ``P × r_y`` (y < S, ramp defined) |
    ``P``; ``occupancy = benchmark × pen ÷ 100`` (the loop applies the 0.95
    cap and flags it), ``adr = benchmark × pen ÷ 100``. A metric with no
    benchmark for the year is NOT penetrated: ``None`` here, and the loop runs
    the subject's own level × growth for it.
    """
    topline = payload.topline
    assert topline is not None
    plan = _ToplinePlan(
        occ_drivers=[None] * payload.hold_years,
        adr_drivers=[None] * payload.hold_years,
    )
    bench_by_year = {b.year: b for b in topline.benchmark_by_year}
    growth_by_year = {g.year: g for g in topline.growth_by_year}
    t12 = topline.t12_reference
    metric_label = {"occupancy": "occupancy", "adr": "ADR"}

    def _override(lst: Sequence[float | None], year: int) -> float | None:
        return lst[year - 1] if 0 <= year - 1 < len(lst) else None

    for y in range(1, payload.hold_years + 1):
        bench = bench_by_year.get(y)
        ramp, ramp_state, ramp_note = _ramp_for_year(topline, y, payload.hold_years)
        gy = growth_by_year.get(y)
        notes: list[str] = []
        rec = IndexDriverYear(
            year=y,
            calendar_year=(
                bench.calendar_year
                if bench is not None and bench.calendar_year is not None
                else (gy.calendar_year if gy is not None and gy.calendar_year is not None else None)
                or (calendar_years[y - 1] if y - 1 < len(calendar_years) else None)
            ),
            method=_TOPLINE_METHOD,
            benchmark_level=topline.benchmark_level,
            benchmark_doc=bench.doc_name if bench is not None else None,
            benchmark_page=bench.page if bench is not None else None,
            ramp_pct=ramp,
            ramp_state=ramp_state,
            t12_occupancy=t12.occupancy if t12 is not None else None,
            t12_adr=t12.adr if t12 is not None else None,
            growth_label=gy.label if gy is not None else None,
            occupancy_growth_source=gy.occupancy_source if gy is not None else None,
            adr_growth_source=gy.adr_source if gy is not None else None,
        )
        if topline.benchmark_level == "none":
            # What the engine knows: it ran the starting figures it was given.
            # WHICH source those are (T-12 actual, analyst override, …) is the
            # loader's ``__sources__["starting_*"]`` entry, not the engine's.
            notes.append(
                "No benchmark — projecting from the subject's own starting occupancy "
                "and ADR as supplied"
                + (f" ({topline.benchmark_fallback_reason})" if topline.benchmark_fallback_reason else "")
                + "."
            )
        for metric, target_attr, source_attr in (
            ("occupancy", "mpi_target", "mpi_target_source"),
            ("adr", "ari_target", "ari_target_source"),
        ):
            b_value = getattr(bench, metric) if bench is not None else None
            if b_value is None:
                setattr(rec, f"{metric}_status", "benchmark_unavailable")
                if topline.benchmark_level != "none":
                    notes.append(
                        f"Subject's own starting {metric} as supplied (no "
                        f"{topline.benchmark_level} benchmark for {metric_label[metric]}"
                        + ("" if bench is None else f" in Year {y}")
                        + "); penetration and ramp are not applied to it."
                    )
                continue
            p = topline.penetration_occ if metric == "occupancy" else topline.penetration_adr
            p_source = (
                topline.penetration_occ_source if metric == "occupancy" else topline.penetration_adr_source
            )
            by_year = (
                topline.penetration_occ_by_year if metric == "occupancy" else topline.penetration_adr_by_year
            )
            v = _override(by_year, y)
            if v is not None:
                pen = v
                setattr(rec, source_attr, "by_year")
            else:
                pen = p * ramp if ramp is not None else p
                setattr(rec, source_attr, "scalar")
                setattr(rec, f"penetration_{'occ' if metric == 'occupancy' else 'adr'}_source", p_source)
            driver = b_value * pen / 100.0
            setattr(rec, target_attr, pen)
            setattr(rec, f"benchmark_{metric}", b_value)
            setattr(rec, f"benchmark_{metric}_source", bench.label if bench is not None else None)
            setattr(rec, f"{metric}_status", "driven")
            if metric == "occupancy":
                plan.occ_drivers[y - 1] = driver
                if driver > OCCUPANCY_CAP:
                    rec.capped_95 = True
                    notes.append(
                        f"Occupancy capped at {OCCUPANCY_CAP:.0%}: benchmark × penetration "
                        f"= {driver:.4f}."
                    )
            else:
                plan.adr_drivers[y - 1] = driver
        if rec.mpi_target is not None and rec.ari_target is not None:
            rec.rgi_derived = rec.mpi_target * rec.ari_target / 100.0
        if ramp_note:
            notes.append(ramp_note)
        rec.reason = " ".join(notes) if notes else None
        plan.records[y] = rec
    return plan


def projection_start_year(close_date: str | None) -> int | None:
    """Calendar year of operating Year 1, or ``None`` with no close date.

    FON-41 #2. The projection's ``year`` field is an ordinal (1..hold_years);
    this is the ONLY place the ordinal is mapped onto the calendar, and it
    anchors on exactly the date the timeline engine anchors on
    (``acquisition_close_date``). Operating Year 1 is the calendar year
    containing the first operating month — close + 1 month — so a 2025-09-30
    close projects Year 1 in 2025 and a 2025-12-15 close projects it in 2026.

    An unparseable or absent date returns ``None``: the grid then renders the
    ordinal alone. It never falls back to the wall clock — a guessed year is a
    fabricated number.
    """
    d = parse_iso_date(close_date)
    if d is None:
        return None
    # First operating month = close + 1 month; its calendar year is Year 1's.
    return d.year + 1 if d.month == 12 else d.year


def projection_calendar_years(
    close_date: str | None, hold_years: int
) -> list[int]:
    """``[start, start+1, ...]`` for ``hold_years`` rows, or ``[]`` with no anchor."""
    start = projection_start_year(close_date)
    if start is None or hold_years <= 0:
        return []
    return [start + i for i in range(hold_years)]


# ──────── Renovation displacement schedule (Sam, 2026-10-15) ────────

MONTHS_PER_YEAR = 12


def renovation_displacement_factors(
    schedule: list[RenovationDisplacementPeriod],
    hold_years: int,
) -> dict[int, RenovationDisplacementYear]:
    """Per-model-year occupancy / ADR factors from an explicit renovation
    displacement schedule. ``{}`` when the schedule is empty.

    The arithmetic — the methodology page states the same in prose:

    * Model year ``y`` is months ``12·(y−1)+1 .. 12·y`` counted from the
      acquisition close (``start_month`` = 1 is the first operating month).
      Every month weighs 1/12 of its year; calendar day counts are ignored.
    * For each month ``m``: ``offline_m = min(1, Σ rooms_offline_pct)`` and
      ``discount_m = min(1, Σ rate_discount_pct)`` over the periods whose
      ``[start_month, end_month]`` contains ``m`` (overlapping periods add,
      capped at 100%). A month outside every period has both at 0.
      ``open_m = 1 − offline_m`` is the share of inventory still selling.
    * ``rooms_offline_share_y = Σ_m offline_m ÷ 12`` and
      ``occupancy_factor_y = 1 − rooms_offline_share_y = Σ_m open_m ÷ 12``.
      Occupancy is reported on the FULL inventory (keys × 365), so taking
      rooms offline shows up as lower occupancy — which is how a P&L reads
      a renovation year — and RevPAR = rooms revenue ÷ rooms available still
      reconciles.
    * ``adr_factor_y = Σ_m open_m·(1 − discount_m) ÷ Σ_m open_m`` — the rate
      discount applies to the rooms that remain open in the discounted
      months, so the year's ADR is the open-room-weighted average rate of
      what actually sold (1.0 when the hotel is closed all year: nothing
      sold, so there is no rate to discount; the occupancy factor is 0).
    * The year's rooms revenue is therefore
      ``rooms_available × occupancy × occupancy_factor_y × ADR × adr_factor_y``
      ``= rooms_available × occupancy × ADR × Σ_m open_m·(1 − discount_m) ÷ 12``,
      i.e. exactly the sum of twelve monthly ``open share × discounted rate``
      terms — one calculation, applied twice (occupancy and rate), not two.
    * Only the year the period falls in moves. Years the schedule does not
      touch keep factor 1.0 and are NOT listed in the result; out-years
      compound from the un-displaced baseline exactly as before.
    """
    if not schedule:
        return {}
    out: dict[int, RenovationDisplacementYear] = {}
    for y in range(1, hold_years + 1):
        first = MONTHS_PER_YEAR * (y - 1) + 1
        entries: list[int] = []
        open_sum = 0.0
        open_discounted_sum = 0.0
        months_in_period = 0
        for m in range(first, first + MONTHS_PER_YEAR):
            offline = 0.0
            discount = 0.0
            touched = False
            for i, period in enumerate(schedule):
                if period.start_month <= m <= period.end_month:
                    touched = True
                    offline += period.rooms_offline_pct
                    discount += period.rate_discount_pct
                    if i not in entries:
                        entries.append(i)
            if touched:
                months_in_period += 1
            offline = min(1.0, offline)
            discount = min(1.0, discount)
            open_share = 1.0 - offline
            open_sum += open_share
            open_discounted_sum += open_share * (1.0 - discount)
        if not entries:
            continue
        occupancy_factor = open_sum / MONTHS_PER_YEAR
        adr_factor = (open_discounted_sum / open_sum) if open_sum > 0.0 else 1.0
        out[y] = RenovationDisplacementYear(
            year=y,
            entries=entries,
            months_in_period=months_in_period,
            rooms_offline_share=min(1.0, max(0.0, 1.0 - occupancy_factor)),
            occupancy_factor=min(1.0, max(0.0, occupancy_factor)),
            adr_factor=min(1.0, max(0.0, adr_factor)),
        )
    return out


def schedule_period_label(period: RenovationDisplacementPeriod) -> str:
    """``"months 1–6: 25% rooms offline, 10% rate discount"`` — the sentence
    fragment the displacement status and the lineage notes print."""
    span = (
        f"month {period.start_month}"
        if period.start_month == period.end_month
        else f"months {period.start_month}–{period.end_month}"
    )
    return (
        f"{span}: {period.rooms_offline_pct:.0%} rooms offline, "
        f"{period.rate_discount_pct:.0%} rate discount"
    )


# ─────────────── Brand displacement multipliers (Wave 2 P2.4) ───────────────
#
# Industry rules of thumb for how brand affiliation shapes the PIP
# recovery curve. Sources: STR Hotels Insights "Brand Standard
# Renovation Impact Survey" (2023); CBRE Hotels Horizons brand-recovery
# commentary. These are NOT hard data — analysts can override per-deal.
#
# Each tuple is ``(recovery_months_multiplier, revpar_index_multiplier)``:
#
#   * Marriott / Hilton — the industry baseline (1.0 / 1.0). Strict
#     brand standards drive a textbook recovery curve.
#   * IHG — slightly slower recovery; programs are stricter on launch
#     QC than Marriott.
#   * Hyatt — faster recovery; strong direct booking channel pulls
#     loyalists back early. Modest extra RevPAR uplift.
#   * Independent / soft-brand — fastest recovery (less brand-standard
#     overhead) but smaller starting RevPAR uplift relative to the
#     national brands.
_BRAND_DISPLACEMENT_MULTIPLIERS: dict[str, tuple[float, float]] = {
    "Marriott": (1.0, 1.0),
    "Hilton": (1.0, 1.0),
    "IHG": (1.05, 1.0),
    "Hyatt": (0.95, 1.02),
    "Independent": (0.85, 1.03),
}


def _resolve_brand_multipliers(brand: str | None) -> tuple[float, float]:
    """Lookup ``(recovery_months_mult, revpar_index_mult)`` for a brand.

    Unknown / None brand defaults to the "Independent" curve (fastest
    recovery, modest uplift) — that's the safest assumption for a
    boutique deal with no published brand standards.
    """
    if brand is None:
        return _BRAND_DISPLACEMENT_MULTIPLIERS["Independent"]
    return _BRAND_DISPLACEMENT_MULTIPLIERS.get(
        brand, _BRAND_DISPLACEMENT_MULTIPLIERS["Independent"]
    )


def _y1_pct_schedule(pip: PIPDisplacement) -> list[float]:
    """Pad the analyst-supplied schedule to a full 12-month Y1 array.

    Months past the end of the supplied list contribute 0% offline
    (the reno wrapped). For ``wing_by_wing`` each pct is capped at 0.5
    inside the engine math, not here, so the audit trail keeps the raw
    analyst input intact.
    """
    sched = list(pip.pct_rooms_offline_by_month)[:12]
    while len(sched) < 12:
        sched.append(0.0)
    return sched


def _y1_revenue_factor_and_adr_drag(pip: PIPDisplacement) -> tuple[float, float]:
    """Return ``(rooms_revenue_factor_y1, y1_adr_drag)`` for the strategy.

    The factor is a single multiplier on what the un-displaced Y1
    rooms revenue would have been. The ADR drag is the multiplicative
    haircut applied to Y1 ADR (always 1.0 except wing_by_wing where
    we apply a 5% drag to reflect construction nuisance).

    Math:

    * ``rolling`` → factor = mean over 12 months of (1 - pct[m])
    * ``full_closure`` → same formula; months at 1.0 contribute 0
    * ``wing_by_wing`` → cap each pct at 0.5, then same formula;
      Y1 ADR drag = 0.95
    * ``none`` → factor = 1.0, drag = 1.0 (PIP object is effectively
      no-op; analyst should leave it None instead).
    """
    if pip.closure_strategy == "none":
        return 1.0, 1.0
    sched = _y1_pct_schedule(pip)
    if pip.closure_strategy == "wing_by_wing":
        sched = [min(0.5, p) for p in sched]
        adr_drag = 0.95
    else:
        adr_drag = 1.0
    factor = sum(1.0 - p for p in sched) / 12.0
    return factor, adr_drag


def _effective_recovery_months(
    pip: PIPDisplacement, brand_recovery_mult: float
) -> int:
    """Brand-adjusted recovery months, clamped to [0, 12] for Y2 accounting.

    Y3 is always full recovery in the engine, so capping at 12 matches
    the projection semantics. We round to the nearest integer month
    because the linear ramp inside ``_y2_occupancy_recovery_factor`` is
    month-indexed.
    """
    raw = int(round(pip.occupancy_recovery_months * brand_recovery_mult))
    return max(0, min(12, raw))


def _y2_occupancy_recovery_factor(
    pip: PIPDisplacement, brand_recovery_mult: float
) -> float:
    """Average Y2 occupancy factor relative to the stabilized baseline.

    Y1 ends with the hotel running at ``y1_revenue_factor`` of baseline.
    Y2 ramps linearly from that depressed start back to 1.0 over
    ``effective_recovery_months`` (capped at 12 within Y2 — Y3 is always
    full recovery). The average is what multiplies the un-displaced Y2
    occupancy.

    For ``closure_strategy='none'`` this returns 1.0 so the legacy code
    path is unchanged.
    """
    if pip.closure_strategy == "none":
        return 1.0
    y1_factor, _ = _y1_revenue_factor_and_adr_drag(pip)
    effective_recovery_months = _effective_recovery_months(
        pip, brand_recovery_mult
    )
    if effective_recovery_months == 0:
        return 1.0
    # Y2 starts at the Y1 ending factor. We model a linear ramp from
    # ``y1_factor`` at month 0 of Y2 to 1.0 at month
    # ``effective_recovery_months``. Months beyond that contribute 1.0.
    # The annual average over 12 months of Y2:
    ramp_total = 0.0
    for m in range(12):
        if m < effective_recovery_months:
            ramp_total += y1_factor + (1.0 - y1_factor) * (
                m / effective_recovery_months
            )
        else:
            ramp_total += 1.0
    return ramp_total / 12.0


# ─────────────── FON-87 — occupancy / ADR trace builder ───────────────

# Relative tolerance under which "benchmark × target ÷ 100" is accepted as
# the formula for an index-driven value. The driver arrives already computed
# (``services.index_methodology``) and may have been unit-normalized on the
# way; when the product does not reproduce it the trace names the driver
# value itself as the input instead of asserting arithmetic that is not there.
_DRIVER_RECONCILE_RTOL = 1e-9


def _index_driver_term(
    metric: str,
    *,
    year: int,
    driver_value: float,
    driver: IndexDriverYear | None,
) -> tuple[str, list[ValueInput], str]:
    """(formula term, inputs, note) for a year an Index Analysis driver set.

    The benchmark and the penetration target are named as inputs — the target
    with the assumption key the lineage walk links (``index_mpi_target`` /
    ``index_mpi_target_by_year``, likewise ARI) — and the note carries the
    benchmark's basis line plus its document and page when the driver record
    has them. With no driver record (an explicit per-year value and nothing
    else), the value itself is the input and the note says so.
    """
    if metric == "occupancy":
        bench = driver.benchmark_occupancy if driver is not None else None
        bench_source = driver.benchmark_occupancy_source if driver is not None else None
        target = driver.mpi_target if driver is not None else None
        target_source = driver.mpi_target_source if driver is not None else None
        bench_name, target_name, target_label = "benchmark_occupancy", "mpi_target", "MPI"
        key_scalar, key_by_year = "index_mpi_target", "index_mpi_target_by_year"
        bench_text = f"{bench:.4f}" if bench is not None else None
    else:
        bench = driver.benchmark_adr if driver is not None else None
        bench_source = driver.benchmark_adr_source if driver is not None else None
        target = driver.ari_target if driver is not None else None
        target_source = driver.ari_target_source if driver is not None else None
        bench_name, target_name, target_label = "benchmark_adr", "ari_target", "ARI"
        key_scalar, key_by_year = "index_ari_target", "index_ari_target_by_year"
        bench_text = f"{bench:.2f}" if bench is not None else None

    where = ""
    if driver is not None and (driver.benchmark_doc or driver.benchmark_page is not None):
        where = " — " + ", ".join(
            part
            for part in (
                driver.benchmark_doc,
                f"p.{driver.benchmark_page}" if driver.benchmark_page is not None else None,
            )
            if part
        )
    basis = f"; {bench_source}" if bench_source else ""

    reconciles = (
        bench is not None
        and target is not None
        and abs(bench * target / 100.0 - driver_value)
        <= _DRIVER_RECONCILE_RTOL * max(1.0, abs(driver_value))
    )
    if reconciles:
        inputs = [
            ValueInput(name=bench_name, value=bench),  # type: ignore[arg-type]
            ValueInput(
                name=target_name,
                value=target,  # type: ignore[arg-type]
                assumption_key=key_by_year if target_source == "by_year" else key_scalar,
            ),
        ]
        term = f"{bench_name} × {target_name} ÷ 100"
        note = (
            f"Year {year} {metric} is indexed (Index Analysis penetration target): "
            f"benchmark {bench_text}{basis}{where} × {target_label} target {target} ÷ 100."
        )
    else:
        inputs = [ValueInput(name=f"index_driver_{metric}", value=driver_value)]
        term = f"index_driver_{metric}"
        if driver is not None and bench is not None and target is not None:
            note = (
                f"Year {year} {metric} is indexed (Index Analysis penetration target): "
                f"the driver value {driver_value:.6g} was built from benchmark "
                f"{bench_text}{basis}{where} and {target_label} target {target}; the "
                "product does not reproduce it to the last digit (unit "
                "normalization upstream), so the driver value is the input named."
            )
        else:
            note = (
                f"Year {year} {metric} is set by an explicit per-year driver "
                f"({driver_value:.6g}); no Index Analysis benchmark record accompanies it."
            )
    return term, inputs, note


def _rate_trace(
    *,
    metric: str,
    year: int,
    value: float,
    starting_key: str,
    starting_value: float,
    driver_value: float | None,
    y1_driver_value: float | None,
    driver: IndexDriverYear | None,
    y1_driver: IndexDriverYear | None,
    anchor: tuple[float, int],
    growth_key: str,
    growth_scalar: float,
    growth_steps: Sequence[tuple[int, float | None]],
    growth_from_list: bool,
    cap: float | None,
    y1_cap: float | None,
    y1_displacement_key: str,
    y1_displacement: float,
    pip: PIPDisplacement | None,
    pip_y1_factor: tuple[str, float],
    pip_out_year_factor: tuple[str, float] | None,
    schedule_factor: tuple[str, float, RenovationDisplacementYear] | None = None,
    schedule: list[RenovationDisplacementPeriod] | None = None,
) -> ValueTrace:
    """The trace for one year's occupancy or ADR — the exact branch
    :meth:`RevenueEngine.run` took for it, named.

    * Any year under an explicit renovation displacement schedule
      (``schedule_factor`` set): the year's base × the schedule's factor for
      that year, naming the periods and months behind it.

    * Year 1: the starting assumption (or the Year-1 index driver) × the
      Year-1 displacement — the legacy ``(1 − y1_*_displacement_pct)`` or the
      structured PIP factor — where one applied.
    * Year 2+, driven: the index driver (occupancy capped at 0.95).
    * Year 2+, undriven: the anchor (the starting assumption / Year-1 driver
      when the anchor is Year 1, otherwise the last driven year's own trace)
      compounded at the growth assumption for the years since; occupancy
      capped at 0.95; the PIP recovery / uplift factor where one applied.
    """
    inputs: list[ValueInput] = []
    notes: list[str] = []

    def _year_one_base() -> str:
        if y1_driver_value is not None:
            term, drv_inputs, note = _index_driver_term(
                metric, year=1, driver_value=y1_driver_value, driver=y1_driver
            )
            inputs.extend(drv_inputs)
            notes.append(note)
            # FON-116 — under the topline Year 1 is held under the occupancy
            # ceiling like every other year (the legacy driver path never
            # capped Year 1 and keeps that expression).
            return f"min({y1_cap}, {term})" if y1_cap is not None else term
        inputs.append(
            ValueInput(name=starting_key, value=starting_value, assumption_key=starting_key)
        )
        return starting_key

    def _schedule_note(name: str, factor: float, sched_year: RenovationDisplacementYear) -> str:
        periods = ", ".join(
            schedule_period_label(schedule[i])
            for i in sched_year.entries
            if schedule is not None and 0 <= i < len(schedule)
        )
        return (
            f"Renovation displacement schedule, Year {sched_year.year}: "
            f"{sched_year.months_in_period} of 12 months inside a renovation period "
            f"({periods}); {name} = {factor:.6g}. The scalar Y1 displacement "
            "assumptions and any PIP closure schedule are not applied on top."
        )

    if year == 1:
        base = _year_one_base()
        if schedule_factor is not None:
            name, factor, sched_year = schedule_factor
            inputs.append(
                ValueInput(
                    name=name, value=factor,
                    assumption_key="renovation_displacement_schedule",
                )
            )
            formula = f"{metric} = {base} × {name}"
            notes.append(_schedule_note(name, factor, sched_year))
        elif pip is not None:
            name, factor = pip_y1_factor
            inputs.append(ValueInput(name=name, value=factor))
            formula = f"{metric} = {base} × {name}"
            notes.append(
                f"Year 1 structured PIP displacement (closure strategy "
                f"{pip.closure_strategy}{', brand ' + pip.brand if pip.brand else ''}): "
                f"{name} = {factor:.6g}."
            )
        elif y1_displacement:
            inputs.append(
                ValueInput(
                    name=y1_displacement_key,
                    value=y1_displacement,
                    assumption_key=y1_displacement_key,
                )
            )
            formula = f"{metric} = {base} × (1 − {y1_displacement_key})"
            notes.append(
                f"Year 1 renovation displacement of {y1_displacement:.2%} applied."
            )
        else:
            formula = f"{metric} = {base}"
            notes.append("No Year-1 displacement applied.")
    elif driver_value is not None:
        term, drv_inputs, note = _index_driver_term(
            metric, year=year, driver_value=driver_value, driver=driver
        )
        inputs.extend(drv_inputs)
        notes.append(note)
        formula = f"{metric} = min({cap}, {term})" if cap is not None else f"{metric} = {term}"
        notes.append(f"Year {year} re-anchors the undriven years that follow it.")
    else:
        anchor_value, anchor_year = anchor
        if anchor_year == 1:
            base = _year_one_base()
            notes.append(
                f"Year {year} compounds the un-displaced Year-1 baseline "
                f"({anchor_value:.6g}) forward — a Year-1 displacement never "
                "cascades into the out-years."
            )
        else:
            inputs.append(
                ValueInput(
                    name=f"anchor_{metric}",
                    value=anchor_value,
                    # The anchor IS the driven year's own figure on the
                    # legacy path; under a structured PIP the Year-2 figure
                    # carries a recovery / uplift factor the anchor does not,
                    # so the pointer is only asserted when the two are equal.
                    traces_to=(
                        f"years[{anchor_year - 1}].{metric}" if pip is None else None
                    ),
                )
            )
            base = f"anchor_{metric}"
            notes.append(
                f"Anchored on Year {anchor_year}, the last year an Index Analysis "
                f"driver set ({anchor_value:.6g})."
            )
        # FON-116 — growth is one step per year. With no per-year list, or a
        # list that restates the scalar assumption for every step, the trace
        # names that assumption and the power (the expression the engine
        # evaluated); a varying list names every year's step so the lineage
        # walk reaches ``*_growth_by_year``. A step with no figure is held
        # flat and said so — it is never a seeded rate.
        constant = (not growth_from_list) or all(g == growth_scalar for _, g in growth_steps)
        if constant:
            inputs.append(
                ValueInput(name=growth_key, value=growth_scalar, assumption_key=growth_key)
            )
            inputs.append(ValueInput(name="years_compounded", value=float(year - anchor_year)))
            grown = f"{base} × (1 + {growth_key}) ^ years_compounded"
        else:
            terms: list[str] = []
            flat_years: list[int] = []
            for step_year, g in growth_steps:
                name = f"{growth_key}_y{step_year}"
                # A year with no growth figure is an input with NO value —
                # never 0.0 (a zero in a trace reads as a rate of zero).
                inputs.append(
                    ValueInput(
                        name=name,
                        value=g,
                        assumption_key=f"{growth_key}_by_year",
                    )
                )
                terms.append(f"(1 + {name})")
                if g is None:
                    flat_years.append(step_year)
            grown = f"{base} × " + " × ".join(terms)
            notes.append(
                f"Per-year {growth_key} applied into each of Years "
                f"{growth_steps[0][0]}–{growth_steps[-1][0]}."
                + (
                    " Year(s) " + ", ".join(str(fy) for fy in flat_years)
                    + " held flat — no growth figure for that year (growth 0 is the "
                    "absence of growth, not a seeded rate)."
                    if flat_years
                    else ""
                )
            )
        formula = f"{metric} = min({cap}, {grown})" if cap is not None else f"{metric} = {grown}"
        if pip_out_year_factor is not None:
            name, factor = pip_out_year_factor
            inputs.append(ValueInput(name=name, value=factor))
            formula = f"{formula} × {name}"
            notes.append(
                f"Structured PIP ({pip.closure_strategy if pip is not None else 'n/a'}): "
                f"{name} = {factor:.6g} applied."
            )
    if pip_out_year_factor is not None and driver_value is not None and year >= 2:
        # A driven out-year under a structured PIP still carries the factor.
        name, factor = pip_out_year_factor
        inputs.append(ValueInput(name=name, value=factor))
        formula = f"{formula} × {name}"
        notes.append(f"Structured PIP: {name} = {factor:.6g} applied.")
    if schedule_factor is not None and year >= 2:
        # An out-year a renovation period falls into (e.g. months 13–18)
        # carries that year's schedule factor on top of the compounded /
        # driven base — the base itself is never displaced.
        name, factor, sched_year = schedule_factor
        inputs.append(
            ValueInput(
                name=name, value=factor,
                assumption_key="renovation_displacement_schedule",
            )
        )
        formula = f"{formula} × {name}"
        notes.append(_schedule_note(name, factor, sched_year))

    return ValueTrace(value=value, formula=formula, inputs=inputs, note=" ".join(notes))


class RevenueEngine(BaseEngine[RevenueEngineInput, RevenueEngineOutput]):
    """Project total revenue across rooms, F&B and other operated departments."""

    name = "revenue"

    def run(self, payload: RevenueEngineInput) -> RevenueEngineOutput:
        keys = payload.keys
        rooms_available = keys * DAYS_PER_YEAR
        years: list[RevenueProjectionYear] = []
        # FON-25 — per-value provenance sidecar, keyed by dotted output path.
        # Populated per year as the canonical revenue lines are computed so
        # an analyst can trace any projected number back to its formula and
        # inputs. Emitted alongside the typed output (never mutates it).
        prov: dict[str, ValueTrace] = {}

        # `starting_*` represents the STABILIZED baseline (typically
        # the T-12 actual or the CBRE Year-1 forecast). Year 1 may be
        # disrupted by renovation displacement; Year 2 onwards compound
        # forward from the un-displaced baseline so a heavy PIP doesn't
        # permanently depress the projection.
        baseline_occ = payload.starting_occupancy
        baseline_adr = payload.starting_adr
        # FON-95 part 2 — explicit per-year drivers (Index Analysis
        # penetration targets × benchmark). Index y-1 = model year y; None =
        # the year is not driven. A Year-1 driver replaces the stabilized
        # baseline; a later driven year becomes the anchor the following
        # undriven years compound from. Both lists empty → the legacy
        # expressions below run unchanged (byte-identical goldens).
        # FON-41 #2 — the projection calendar (read by the per-year records
        # below and echoed on the output; nothing in the math reads it).
        calendar_years = projection_calendar_years(
            payload.acquisition_close_date, payload.hold_years
        )
        # FON-116 / FON-117 — the topline (benchmark × penetration × ramp)
        # resolves every year's driver value up front; the legacy Index
        # Analysis drivers arrive as lists. The two are mutually exclusive
        # (input validator), so from here on one list per metric drives.
        topline_plan = (
            _plan_topline(payload, calendar_years) if payload.topline is not None else None
        )
        if topline_plan is not None:
            occ_drivers = topline_plan.occ_drivers
            adr_drivers = topline_plan.adr_drivers
        else:
            occ_drivers = list(payload.occupancy_by_year_override or [])
            adr_drivers = list(payload.adr_by_year_override or [])

        def _driver(lst: list[float | None], year: int) -> float | None:
            return lst[year - 1] if 0 <= year - 1 < len(lst) else None

        # Under the topline Year 1 is held under the occupancy ceiling like
        # every other year (and says ``capped_95``); the legacy driver path
        # never capped Year 1 and keeps that expression.
        y1_cap = OCCUPANCY_CAP if topline_plan is not None else None
        if _driver(occ_drivers, 1) is not None:
            baseline_occ = _driver(occ_drivers, 1)  # type: ignore[assignment]
            if y1_cap is not None:
                baseline_occ = min(y1_cap, baseline_occ)
        if _driver(adr_drivers, 1) is not None:
            baseline_adr = _driver(adr_drivers, 1)  # type: ignore[assignment]
        # (anchor value, anchor year) the undriven out-years compound from.
        occ_anchor: tuple[float, int] = (baseline_occ, 1)
        adr_anchor: tuple[float, int] = (baseline_adr, 1)
        # The drivers' provenance, echoed on the output with the resulting
        # figures filled in per year: the topline's records, or the legacy
        # Index Analysis records the runner built.
        legacy_drivers_by_year = {
            d.year: d.model_copy() for d in payload.index_drivers_by_year
        }
        drivers_by_year = (
            topline_plan.records if topline_plan is not None else legacy_drivers_by_year
        )
        # FON-116 — growth INTO each year per metric (scalar, or the per-year
        # list; ``None`` = held flat). Index ``y − 1`` = model year ``y``.
        occ_growth = [
            _growth_into(payload.occupancy_growth_by_year, payload.occupancy_growth, y)
            for y in range(1, payload.hold_years + 1)
        ]
        adr_growth = [
            _growth_into(payload.adr_growth_by_year, payload.adr_growth, y)
            for y in range(1, payload.hold_years + 1)
        ]

        def _steps(
            growth: list[tuple[float | None, bool]], from_year: int, to_year: int
        ) -> list[tuple[int, float | None]]:
            """``(year, growth into year)`` for the years after ``from_year``
            up to ``to_year`` — what ``to_year`` compounds by."""
            return [(k, growth[k - 1][0]) for k in range(from_year + 1, to_year + 1)]

        # FON-116 — one record per model year on the output
        # (``topline_by_year``): the driver record where one exists, else a
        # plain record the loop fills with the year's figures and statuses.
        topline_records: dict[int, IndexDriverYear] = {}
        resort_fees = payload.starting_resort_fees
        y1_occ_disp = payload.y1_occupancy_displacement_pct
        y1_adr_disp = payload.y1_adr_displacement_pct
        use_segments = bool(payload.segments)

        # Opt-in bottom-up resort-fee build-up. When ``resort_fee_per_night``
        # is set (> 0) resort-fee revenue is computed per year from
        # per-night × occupied-room-nights × capture[year] and REPLACES the
        # flat ``starting_resort_fees`` anchor. When it's None/0 the legacy
        # flat + ``resort_fees_growth`` path below runs byte-identically.
        resort_fee_per_night = payload.resort_fee_per_night
        use_resort_fee_buildup = (
            resort_fee_per_night is not None and resort_fee_per_night > 0
        )
        # Capture ramp with carry-forward defaults: a missing year inherits
        # the prior year's capture (y1 defaults to full capture, 1.0), so a
        # single supplied capture value carries across the whole hold.
        cap_y1 = (
            payload.resort_fee_capture_y1
            if payload.resort_fee_capture_y1 is not None
            else 1.0
        )
        cap_y2 = (
            payload.resort_fee_capture_y2
            if payload.resort_fee_capture_y2 is not None
            else cap_y1
        )
        cap_y3 = (
            payload.resort_fee_capture_y3
            if payload.resort_fee_capture_y3 is not None
            else cap_y2
        )

        def _resort_fee_capture(year: int) -> float:
            if year <= 1:
                return cap_y1
            if year == 2:
                return cap_y2
            return cap_y3  # y3 carries forward for every year past 3

        # Wave 2 P2.4 — structured PIP displacement (closure strategy +
        # % rooms offline + brand recovery curve). When set with a real
        # strategy, this object overrides the legacy flat-pct path. When
        # ``None`` (or strategy == 'none'), the legacy math below runs
        # byte-identically.
        pip_v2 = payload.pip_displacement if (
            payload.pip_displacement is not None
            and payload.pip_displacement.closure_strategy != "none"
        ) else None
        # Sam, 2026-10-15 — the explicit renovation displacement schedule.
        # Non-empty, it is the only displacement applied: the scalar Y1
        # percentages and the structured PIP are switched OFF here so nothing
        # is applied twice. Empty → ``{}`` and every branch below is verbatim.
        schedule = list(payload.renovation_displacement_schedule or [])
        sched_by_year = renovation_displacement_factors(schedule, payload.hold_years)
        if schedule:
            pip_v2 = None
            y1_occ_disp = 0.0
            y1_adr_disp = 0.0

        def _sched_factor(year: int, which: str) -> float:
            sy = sched_by_year.get(year)
            if sy is None:
                return 1.0
            return sy.occupancy_factor if which == "occupancy" else sy.adr_factor

        def _sched_trace(
            year: int, which: str
        ) -> tuple[str, float, RenovationDisplacementYear] | None:
            sy = sched_by_year.get(year)
            if sy is None:
                return None
            name = "schedule_occupancy_factor" if which == "occupancy" else "schedule_adr_factor"
            return (name, _sched_factor(year, which), sy)
        if pip_v2 is not None:
            brand_recovery_mult, brand_revpar_mult = _resolve_brand_multipliers(
                pip_v2.brand
            )
            y1_revenue_factor, y1_adr_drag = _y1_revenue_factor_and_adr_drag(pip_v2)
            y2_occ_recovery_factor = _y2_occupancy_recovery_factor(
                pip_v2, brand_recovery_mult
            )
            effective_revpar_index = (
                pip_v2.revpar_index_post_reno * brand_revpar_mult
            )
        else:
            y1_revenue_factor = 1.0
            y1_adr_drag = 1.0
            y2_occ_recovery_factor = 1.0
            effective_revpar_index = 1.0

        for y in range(1, payload.hold_years + 1):
            if y == 1:
                if pip_v2 is not None:
                    # Y1 effective occ + ADR. We REPORT the year's
                    # effective occupancy (baseline × y1_revenue_factor)
                    # so the projection year's revpar = rooms_revenue /
                    # rooms_available reconciles. ADR drag only applies
                    # under wing_by_wing.
                    occ = baseline_occ * y1_revenue_factor
                    adr = baseline_adr * y1_adr_drag
                elif 1 in sched_by_year:
                    occ = baseline_occ * _sched_factor(1, "occupancy")
                    adr = baseline_adr * _sched_factor(1, "adr")
                else:
                    occ = baseline_occ * (1.0 - y1_occ_disp)
                    adr = baseline_adr * (1.0 - y1_adr_disp)
            else:
                # Year 2+ compounds from the stabilized baseline,
                # not from the displaced Y1, so PIP year doesn't
                # cascade into permanently depressed out-years.
                # FON-95 part 2 — a per-year driver replaces the compounded
                # figure and re-anchors the years after it; with no drivers
                # the legacy expression runs verbatim.
                # FON-87 — the anchors this year compounds FROM, captured
                # before a driver re-anchors them, so the trace names them.
                occ_anchor_from = occ_anchor
                adr_anchor_from = adr_anchor
                # FON-116 — an undriven year compounds its anchor (the
                # un-displaced Year-1 baseline, or the last driven year) by
                # one growth step per year since; ``_compound`` evaluates a
                # constant run as the ``× (1 + g) ^ n`` the scalar path
                # always has, so the goldens are byte-identical.
                occ_d = _driver(occ_drivers, y)
                if occ_d is not None:
                    base_occ_y = min(OCCUPANCY_CAP, occ_d)
                    occ_anchor = (base_occ_y, y)
                else:
                    base_occ_y = min(
                        OCCUPANCY_CAP,
                        _compound(
                            occ_anchor[0],
                            [g for _, g in _steps(occ_growth, occ_anchor[1], y)],
                        ),
                    )
                adr_d = _driver(adr_drivers, y)
                if adr_d is not None:
                    base_adr_y = adr_d
                    adr_anchor = (base_adr_y, y)
                else:
                    base_adr_y = _compound(
                        adr_anchor[0],
                        [g for _, g in _steps(adr_growth, adr_anchor[1], y)],
                    )
                if pip_v2 is not None and y == 2:
                    occ = base_occ_y * y2_occ_recovery_factor
                    adr = base_adr_y * effective_revpar_index
                elif pip_v2 is not None:
                    # Y3+ — full recovery, full uplift.
                    occ = base_occ_y
                    adr = base_adr_y * effective_revpar_index
                elif y in sched_by_year:
                    # A renovation period falling into this year (months
                    # 12(y−1)+1 .. 12y) displaces it; the base it compounds
                    # from is untouched, so later years recover in full.
                    occ = base_occ_y * _sched_factor(y, "occupancy")
                    adr = base_adr_y * _sched_factor(y, "adr")
                else:
                    occ = base_occ_y
                    adr = base_adr_y
                if not use_resort_fee_buildup:
                    resort_fees = resort_fees * (1.0 + payload.resort_fees_growth)

            occupied = rooms_available * occ

            # Bottom-up resort-fee build-up overrides the flat anchor once
            # the year's occupied-room-nights (``occupied``) are known.
            if use_resort_fee_buildup:
                resort_fees = (
                    resort_fee_per_night * occupied * _resort_fee_capture(y)
                )

            # ─── Rooms revenue: segmented vs. single-line ───
            segment_breakdown: list[SegmentYear] = []
            if use_segments:
                # Per-segment yearly math. Each segment carries its own
                # ADR (post growth + Y1 displacement) and a channel-cost
                # percentage that captures OTA commissions, TMC fees,
                # group attrition, etc. Aggregate `rooms_revenue` is
                # NET of channel cost — this is the canonical line the
                # downstream P&L / Returns engines read.
                gross_total = 0.0
                net_total = 0.0
                for seg in payload.segments:
                    if y == 1:
                        if pip_v2 is not None:
                            # Y1 segment ADR uses wing_by_wing drag (if
                            # applicable) — segments share the same
                            # construction-nuisance haircut.
                            seg_adr_yn = seg.adr * y1_adr_drag
                        elif 1 in sched_by_year:
                            # The schedule's rate discount applies inside
                            # each segment (the open rooms of every segment
                            # sell at the discounted rate).
                            seg_adr_yn = seg.adr * _sched_factor(1, "adr")
                        else:
                            # Y1 displacement applies inside each segment.
                            # ``starting_adr`` baseline; segment ADR uses
                            # its own anchor with same displacement %.
                            seg_adr_yn = seg.adr * (1.0 - y1_adr_disp)
                    else:
                        # The per-segment ``adr_growth`` override wins; else
                        # the segment reads ADR growth from the per-year list
                        # (or the scalar) exactly as the single line does.
                        if seg.adr_growth is not None:
                            seg_adr_grown = seg.adr * (1.0 + seg.adr_growth) ** (y - 1)
                        else:
                            seg_adr_grown = _compound(
                                seg.adr, [g for _, g in _steps(adr_growth, 1, y)]
                            )
                        if pip_v2 is not None:
                            seg_adr_yn = seg_adr_grown * effective_revpar_index
                        elif y in sched_by_year:
                            seg_adr_yn = seg_adr_grown * _sched_factor(y, "adr")
                        else:
                            seg_adr_yn = seg_adr_grown
                    seg_occupied = occupied * seg.mix_pct
                    seg_gross = seg_occupied * seg_adr_yn
                    seg_channel = seg_gross * seg.channel_cost_pct
                    seg_net = seg_gross - seg_channel
                    gross_total += seg_gross
                    net_total += seg_net
                    segment_breakdown.append(
                        SegmentYear(
                            name=seg.name,
                            mix_pct=seg.mix_pct,
                            occupied_rooms=seg_occupied,
                            adr=seg_adr_yn,
                            channel_cost_pct=seg.channel_cost_pct,
                            gross_revenue=seg_gross,
                            net_revenue=seg_net,
                        )
                    )
                gross_rooms_revenue = gross_total
                rooms_revenue = net_total
                channel_cost_total = gross_total - net_total
            else:
                # Legacy single-line path — byte-identical to pre-Wave-2.
                rooms_revenue = occupied * adr
                gross_rooms_revenue = rooms_revenue
                channel_cost_total = 0.0

            revpar = rooms_revenue / rooms_available if rooms_available else 0.0

            fb_revenue = occupied * payload.fb_revenue_per_occupied_room
            other_revenue = rooms_revenue * payload.other_revenue_pct_of_rooms
            total_revenue = rooms_revenue + fb_revenue + resort_fees + other_revenue

            # ─── Provenance for this year's canonical revenue lines ───
            idx = y - 1
            if use_segments:
                rooms_trace = ValueTrace(
                    value=rooms_revenue,
                    formula="rooms_revenue = Σ segment (gross_revenue − channel_cost)",
                    inputs=[
                        ValueInput(name="gross_rooms_revenue", value=gross_rooms_revenue),
                        ValueInput(name="channel_cost_total", value=channel_cost_total),
                    ],
                    note=(
                        f"Net of channel cost across {len(segment_breakdown)} demand "
                        "segments; see segment_breakdown for the per-segment split. "
                        "No single assumption owns this value — each segment carries "
                        "its own mix, ADR and channel cost — so no assumption_key is "
                        "asserted on either input."
                    ),
                )
            else:
                rooms_inputs = [
                    # The engine ASSERTS which assumption each factor is
                    # anchored to, so the lineage walk never has to infer
                    # it from the input's name. Years 2+ compound the same
                    # baseline forward — the key names the anchor, not the
                    # only input (the note below says what else applies).
                    # FON-87 — each factor also points at its own trace
                    # (``years[i].occupied_rooms`` / ``years[i].adr``), so
                    # the lineage walk reaches the rate lines the Future
                    # P&L drawer asks for, not only the Year-1 assumption.
                    ValueInput(
                        name="occupied_rooms",
                        value=occupied,
                        assumption_key="starting_occupancy",
                        traces_to=f"years[{idx}].occupied_rooms",
                    ),
                    ValueInput(
                        name="adr",
                        value=adr,
                        assumption_key="starting_adr",
                        traces_to=f"years[{idx}].adr",
                    ),
                ]
                driver_note = ""
                drv = drivers_by_year.get(y)
                if drv is not None and (
                    _driver(occ_drivers, y) is not None
                    or _driver(adr_drivers, y) is not None
                ):
                    # FON-95 part 2 — the year was indexed: name the benchmark
                    # and the penetration target so the lineage walk reaches
                    # the Index Analysis assumption, not just the Year-1 seed.
                    parts: list[str] = []
                    if _driver(occ_drivers, y) is not None and drv.benchmark_occupancy is not None:
                        rooms_inputs.append(
                            ValueInput(name="benchmark_occupancy", value=drv.benchmark_occupancy)
                        )
                        if drv.mpi_target is not None:
                            rooms_inputs.append(
                                ValueInput(
                                    name="mpi_target",
                                    value=drv.mpi_target,
                                    assumption_key=(
                                        "index_mpi_target_by_year"
                                        if drv.mpi_target_source == "by_year"
                                        else "index_mpi_target"
                                    ),
                                )
                            )
                        parts.append(
                            f"occupancy = benchmark occupancy ({drv.benchmark_occupancy:.4f}"
                            f"{'; ' + drv.benchmark_occupancy_source if drv.benchmark_occupancy_source else ''}) "
                            f"× MPI target {drv.mpi_target} ÷ 100"
                        )
                    if _driver(adr_drivers, y) is not None and drv.benchmark_adr is not None:
                        rooms_inputs.append(
                            ValueInput(name="benchmark_adr", value=drv.benchmark_adr)
                        )
                        if drv.ari_target is not None:
                            rooms_inputs.append(
                                ValueInput(
                                    name="ari_target",
                                    value=drv.ari_target,
                                    assumption_key=(
                                        "index_ari_target_by_year"
                                        if drv.ari_target_source == "by_year"
                                        else "index_ari_target"
                                    ),
                                )
                            )
                        parts.append(
                            f"ADR = benchmark ADR ({drv.benchmark_adr:.2f}"
                            f"{'; ' + drv.benchmark_adr_source if drv.benchmark_adr_source else ''}) "
                            f"× ARI target {drv.ari_target} ÷ 100"
                        )
                    if parts:
                        driver_note = (
                            f" Year {y} is indexed (Index Analysis penetration target): "
                            + "; ".join(parts)
                            + "."
                        )
                rooms_trace = ValueTrace(
                    value=rooms_revenue,
                    formula="rooms_revenue = occupied_rooms × ADR",
                    inputs=rooms_inputs,
                    note=(
                        f"occupied_rooms = keys ({keys}) × {DAYS_PER_YEAR} days × "
                        f"occupancy ({occ:.4f}); occupancy and ADR chain back to the "
                        "starting_occupancy / starting_adr assumptions."
                        + (
                            ""
                            if y == 1
                            else (
                                f" Year {y} compounds that baseline by "
                                "occupancy_growth / adr_growth, so those two "
                                "assumptions also move this value."
                            )
                        )
                        + driver_note
                    ),
                )
            # FON-95 part 2 — echo the driver record with the figures that ran.
            # FON-116 — and build the year's ``topline_by_year`` record: the
            # driver record when one exists, else a plain record. An undriven
            # metric carries the growth step that moved it (``None`` = held
            # flat → ``flat_no_growth``; Year 1 has no step).
            drv_out = drivers_by_year.get(y)
            rec_out = drv_out if drv_out is not None else IndexDriverYear(
                year=y,
                calendar_year=calendar_years[y - 1] if y - 1 < len(calendar_years) else None,
            )
            rec_out.occupancy = occ
            rec_out.adr = adr
            if y >= 2:
                for metric, lst, growth in (
                    ("occupancy", occ_drivers, occ_growth),
                    ("adr", adr_drivers, adr_growth),
                ):
                    if _driver(lst, y) is not None:
                        continue
                    g, from_list = growth[y - 1]
                    setattr(rec_out, f"{metric}_growth_applied", g)
                    if from_list and g is None:
                        # No growth figure: the growth state is carried by the
                        # metric's growth source. The year's status becomes
                        # ``flat_no_growth`` unless the more consequential
                        # ``benchmark_unavailable`` already stands — both
                        # signals then survive on the record.
                        if getattr(rec_out, f"{metric}_growth_source") is None:
                            setattr(rec_out, f"{metric}_growth_source", "unavailable")
                        if getattr(rec_out, f"{metric}_status") != "benchmark_unavailable":
                            setattr(rec_out, f"{metric}_status", "flat_no_growth")
            topline_records[y] = rec_out
            prov[f"years[{idx}].rooms_revenue"] = rooms_trace
            if use_resort_fee_buildup:
                prov[f"years[{idx}].resort_fees"] = ValueTrace(
                    value=resort_fees,
                    formula=(
                        "resort_fees = resort_fee_per_night × occupied_rooms "
                        "× capture_pct"
                    ),
                    inputs=[
                        ValueInput(
                            name="resort_fee_per_night",
                            value=resort_fee_per_night,
                            assumption_key="resort_fee_per_night",
                        ),
                        ValueInput(
                            name="occupied_rooms",
                            value=occupied,
                            assumption_key="starting_occupancy",
                        ),
                        # capture_pct carries forward across three assumption
                        # keys (resort_fee_capture_y1/y2/y3) — a missing year
                        # inherits the prior one — so no single key owns it.
                        ValueInput(
                            name="capture_pct", value=_resort_fee_capture(y)
                        ),
                    ],
                    note=(
                        "Bottom-up resort-fee build-up; capture ramps "
                        f"y1={cap_y1:.4f} / y2={cap_y2:.4f} / y3+={cap_y3:.4f}. "
                        "capture_pct carries forward across the three "
                        "resort_fee_capture_y* assumptions, so no assumption_key "
                        "is asserted on it."
                    ),
                )
            else:
                # The flat anchor path. Traced too, so the F&B engine's
                # pass-through (``traces_to="revenue.years[i].resort_fees"``)
                # resolves on EVERY deal rather than only on the bottom-up ones
                # — an unresolvable pointer reads as a broken chain.
                prov[f"years[{idx}].resort_fees"] = ValueTrace(
                    value=resort_fees,
                    formula=(
                        "resort_fees = starting_resort_fees"
                        if y == 1
                        else "resort_fees = prior_year × (1 + resort_fees_growth)"
                    ),
                    inputs=[
                        ValueInput(
                            name="starting_resort_fees",
                            value=resort_fees,
                            assumption_key="starting_resort_fees",
                        )
                    ],
                    note=(
                        "Flat Year-1 resort-fee anchor"
                        + (
                            "."
                            if y == 1
                            else f" grown {y - 1} year(s) at resort_fees_growth."
                        )
                    ),
                )
            # F&B and other-operated revenue, each a direct function of one
            # assumption. Traced so the F&B engine's grounded pass-throughs
            # resolve — before this they pointed at keys the revenue engine
            # never emitted, and the walk dead-ended on every year.
            prov[f"years[{idx}].fb_revenue"] = ValueTrace(
                value=fb_revenue,
                formula="fb_revenue = occupied_rooms × fb_revenue_per_occupied_room",
                inputs=[
                    ValueInput(
                        name="occupied_rooms",
                        value=occupied,
                        assumption_key="starting_occupancy",
                    ),
                    ValueInput(
                        name="fb_revenue_per_occupied_room",
                        value=payload.fb_revenue_per_occupied_room,
                        assumption_key="fb_revenue_per_occupied_room",
                    ),
                ],
            )
            prov[f"years[{idx}].other_revenue"] = ValueTrace(
                value=other_revenue,
                formula="other_revenue = rooms_revenue × other_revenue_pct_of_rooms",
                inputs=[
                    ValueInput(
                        name="rooms_revenue",
                        value=rooms_revenue,
                        traces_to=f"years[{idx}].rooms_revenue",
                    ),
                    ValueInput(
                        name="other_revenue_pct_of_rooms",
                        value=payload.other_revenue_pct_of_rooms,
                        assumption_key="other_revenue_pct_of_rooms",
                    ),
                ],
            )
            prov[f"years[{idx}].total_revenue"] = ValueTrace(
                value=total_revenue,
                formula=(
                    "total_revenue = rooms_revenue + fb_revenue + resort_fees "
                    "+ other_revenue"
                ),
                inputs=[
                    ValueInput(
                        name="rooms_revenue",
                        value=rooms_revenue,
                        traces_to=f"years[{idx}].rooms_revenue",
                    ),
                    ValueInput(
                        name="fb_revenue",
                        value=fb_revenue,
                        traces_to=f"years[{idx}].fb_revenue",
                    ),
                    ValueInput(
                        name="resort_fees",
                        value=resort_fees,
                        traces_to=f"years[{idx}].resort_fees",
                    ),
                    ValueInput(
                        name="other_revenue",
                        value=other_revenue,
                        traces_to=f"years[{idx}].other_revenue",
                    ),
                ],
            )

            # ─── FON-87 — the rate lines behind rooms revenue ───
            # ``years[i].occupancy`` / ``.adr`` / ``.revpar`` are the paths the
            # Future P&L lineage drawer requests for its Occupancy / Average
            # Rate / RevPAR cells; with no trace it said "No trace available".
            # Each trace names the anchor the year was built from (the
            # starting assumption, the Index Analysis driver — benchmark ×
            # target, with the benchmark's document and page — or the prior
            # anchored year), the growth compounded onto it and the Year-1
            # displacement where one applied. Values are the figures above;
            # nothing here moves a number.
            if y == 1:
                occ_anchor_from = (baseline_occ, 1)
                adr_anchor_from = (baseline_adr, 1)
            prov[f"years[{idx}].occupancy"] = _rate_trace(
                metric="occupancy",
                year=y,
                value=occ,
                starting_key="starting_occupancy",
                starting_value=payload.starting_occupancy,
                driver_value=_driver(occ_drivers, y),
                y1_driver_value=_driver(occ_drivers, 1),
                driver=drivers_by_year.get(y),
                y1_driver=drivers_by_year.get(1),
                anchor=occ_anchor_from,
                growth_key="occupancy_growth",
                growth_scalar=payload.occupancy_growth,
                growth_steps=_steps(occ_growth, occ_anchor_from[1], y),
                growth_from_list=payload.occupancy_growth_by_year is not None,
                cap=OCCUPANCY_CAP,
                y1_cap=y1_cap,
                y1_displacement_key="y1_occupancy_displacement_pct",
                y1_displacement=y1_occ_disp,
                pip=pip_v2,
                pip_y1_factor=("y1_revenue_factor", y1_revenue_factor),
                pip_out_year_factor=(
                    ("y2_occupancy_recovery_factor", y2_occ_recovery_factor)
                    if pip_v2 is not None and y == 2
                    else None
                ),
                schedule_factor=_sched_trace(y, "occupancy"),
                schedule=schedule,
            )
            prov[f"years[{idx}].adr"] = _rate_trace(
                metric="adr",
                year=y,
                value=adr,
                starting_key="starting_adr",
                starting_value=payload.starting_adr,
                driver_value=_driver(adr_drivers, y),
                y1_driver_value=_driver(adr_drivers, 1),
                driver=drivers_by_year.get(y),
                y1_driver=drivers_by_year.get(1),
                anchor=adr_anchor_from,
                growth_key="adr_growth",
                growth_scalar=payload.adr_growth,
                growth_steps=_steps(adr_growth, adr_anchor_from[1], y),
                growth_from_list=payload.adr_growth_by_year is not None,
                cap=None,
                y1_cap=None,
                y1_displacement_key="y1_adr_displacement_pct",
                y1_displacement=y1_adr_disp,
                pip=pip_v2,
                pip_y1_factor=("y1_adr_drag", y1_adr_drag),
                pip_out_year_factor=(
                    ("revpar_index_post_reno", effective_revpar_index)
                    if pip_v2 is not None and y >= 2
                    else None
                ),
                schedule_factor=_sched_trace(y, "adr"),
                schedule=schedule,
            )
            prov[f"years[{idx}].occupied_rooms"] = ValueTrace(
                value=occupied,
                formula="occupied_rooms = keys × days × occupancy",
                inputs=[
                    ValueInput(name="keys", value=float(keys), assumption_key="keys"),
                    ValueInput(name="days", value=float(DAYS_PER_YEAR)),
                    ValueInput(
                        name="occupancy",
                        value=occ,
                        traces_to=f"years[{idx}].occupancy",
                    ),
                ],
                note=f"rooms_available = keys ({keys}) × {DAYS_PER_YEAR} days.",
            )
            prov[f"years[{idx}].revpar"] = ValueTrace(
                value=revpar,
                formula="revpar = rooms_revenue ÷ rooms_available",
                inputs=[
                    ValueInput(
                        name="rooms_revenue",
                        value=rooms_revenue,
                        traces_to=f"years[{idx}].rooms_revenue",
                    ),
                    ValueInput(
                        name="rooms_available",
                        value=float(rooms_available),
                        assumption_key="keys",
                    ),
                ],
                note=(
                    f"rooms_available = keys ({keys}) × {DAYS_PER_YEAR} days."
                    + (
                        ""
                        if use_segments
                        else " On the single-line path this equals occupancy × ADR."
                    )
                    + (
                        " Rooms revenue is net of channel cost across the demand "
                        "segments, so RevPAR is a NET figure here."
                        if use_segments
                        else ""
                    )
                ),
            )

            years.append(
                RevenueProjectionYear(
                    year=y,
                    occupancy=occ,
                    adr=adr,
                    revpar=revpar,
                    rooms_revenue=rooms_revenue,
                    fb_revenue=fb_revenue,
                    resort_fees=resort_fees,
                    other_revenue=other_revenue,
                    total_revenue=total_revenue,
                    segment_breakdown=segment_breakdown,
                    gross_rooms_revenue=gross_rooms_revenue,
                    channel_cost_total=channel_cost_total,
                )
            )

        if len(years) >= 2 and years[0].total_revenue > 0:
            n = len(years) - 1
            cagr = (years[-1].total_revenue / years[0].total_revenue) ** (1 / n) - 1
        else:
            cagr = 0.0

        # FON-41 #2 — the projection calendar rides alongside the ordinal rows.
        # Nothing in the math above reads it; it exists so the statement can
        # print "Year 1 — 2025" instead of printing the ordinal as a year.
        start_year = projection_start_year(payload.acquisition_close_date)

        return RevenueEngineOutput(
            deal_id=payload.deal_id,
            years=years,
            total_revenue_cagr=cagr,
            projection_start_year=start_year,
            projection_calendar_years=calendar_years,
            provenance=apply_states(prov),
            # The legacy Index Analysis echo — empty under the topline (nothing
            # legacy drove the run); the API and the Index Analysis grid read it.
            index_drivers_by_year=[
                legacy_drivers_by_year[k] for k in sorted(legacy_drivers_by_year)
            ],
            # FON-116 — one record per model year, and the method that ran.
            topline_by_year=[topline_records[k] for k in sorted(topline_records)],
            topline_methodology_version=(
                TOPLINE_METHODOLOGY_VERSION
                if topline_plan is not None
                else LEGACY_TOPLINE_METHODOLOGY_VERSION
            ),
            renovation_displacement_by_year=[
                sched_by_year[k] for k in sorted(sched_by_year)
            ],
        )


__all__ = [
    "LEGACY_TOPLINE_METHODOLOGY_VERSION",
    "OCCUPANCY_CAP",
    "TOPLINE_METHODOLOGY_VERSION",
    "RevenueEngine",
    "default_ramp_by_year",
    "projection_calendar_years",
    "projection_start_year",
    "renovation_displacement_factors",
    "schedule_period_label",
    "_BRAND_DISPLACEMENT_MULTIPLIERS",
    "_resolve_brand_multipliers",
]
