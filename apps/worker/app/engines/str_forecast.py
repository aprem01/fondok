"""STR forward-forecast engine — 24 months back + 24 months forward × 3 scenarios.

**Wave 3 W3.3** — Sam's June 2026 ask: institutional analysts won't approve
a deal without seeing a forward RevPAR forecast across multiple scenarios.
Today Fondok ingests STR Trends (24 months of subject + comp-set monthly
RevPAR / Occ / ADR) but does not project the NEXT 24 months. This engine
fills that gap.

How the math works
==================

The engine consumes ``list[STRMonth]`` of historical observations (most
recent first OR last — the engine sorts) and emits one
``STRForecastResult`` carrying:

1. The trailing 24 historical months (or whatever fraction of that
   window is available — coverage degrades gracefully).
2. THREE forward 24-month scenarios (downside / base / upside).
3. The exact scenario settings the run used (defaults OR analyst
   overrides, so the UI can hydrate scenario cards without round-
   tripping back through defaults).

Scenario defaults
-----------------

* **downside** — RevPAR CAGR -2.0%, subject index target 0.92 (subject
  falls below comp set), occupancy floor 0.55, ADR floor 0.80 (80% of
  trailing-12 ADR). Mirrors a recessionary print where the subject
  loses share to its comp set.
* **base** — RevPAR CAGR +2.5%, index target 1.00 (subject matches comp
  set), occupancy floor 0.60, ADR floor 0.88. Steady-state mid-cycle
  assumption tied to the long-run nominal lodging-RevPAR growth rate.
* **upside** — RevPAR CAGR +5.0%, index target 1.06 (subject pulls
  ahead by 6%), occupancy floor 0.65, ADR floor 0.92. Reflects either
  a post-PIP RevPAR uplift OR a market-cycle peak.

Per-month math
--------------

For each scenario and each forward month ``m`` (1..24):

1. **Comp-set RevPAR** — projected at the scenario's CAGR off the
   trailing-12 average comp RevPAR (smoothes one-month spikes). The
   monthly growth factor is ``(1 + CAGR) ** (m / 12)``.
2. **Subject RevPAR Index** — linearly interpolated from the trailing-
   12 subject index to the scenario's ``revpar_index_target`` over the
   24-month horizon. m=1 nudges off the starting index; m=24 sits
   exactly at the target.
3. **Subject RevPAR** — ``comp_revpar[m] * subject_index[m]``.
4. **Decompose subject RevPAR into Occ × ADR** — preserves the trailing-
   12 occ:adr ratio (``r = trailing12_occ / trailing12_adr``). With
   ``occ × adr = revpar`` and ``occ / adr = r`` we solve
   ``occ = sqrt(revpar × r)``, ``adr = sqrt(revpar / r)``.
5. **Clip to floors** — if the decomposed occupancy is below the
   scenario's ``occupancy_floor`` we hold occupancy at the floor and
   solve ADR back out from RevPAR. Same idea for the ADR floor (where
   the floor is interpreted as a multiplier on the trailing-12 ADR).
   When BOTH floors bite, occupancy and ADR both pin to the floors and
   the resulting RevPAR is the floor product (lower than the scenario's
   indicated RevPAR — a deliberate guardrail, not a math bug).

Coverage tiers
--------------

* 24+ historical months → ``high`` (full STR Trend window).
* 12-23 months → ``medium`` (partial window; forecast runs but
  trailing-12 averages are noisier).
* < 12 months → ``low`` (forecast disabled; empty
  ``forecast_months`` lists per scenario; the UI should render a
  "Awaiting more history" banner).

Determinism + Pydantic v2 contract
----------------------------------

Pure-function, no I/O, no time. Pydantic v2 inputs in / outputs out.
asyncpg-safe — the API layer JSON-serializes the result through the
standard FastAPI ``response_model`` path.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Iterable

from fondok_schemas.str_forecast import (
    CoverageQuality,
    STRForecastResult,
    STRForecastScenario,
    STRMonth,
)


# Default trailing-12 average — used when historical_months has fewer
# than 12 entries (we average what we have rather than synthesize).
_TRAILING_WINDOW = 12

# Horizon for both the historical window (most recent N) and the
# forecast (forward N).
_FORECAST_MONTHS = 24

# When the comp_revpar floor is needed (subject_revpar / index would
# divide-by-zero) we hold at a tiny non-zero number rather than NaN.
_FLOOR_EPSILON = 1e-9


def default_scenarios() -> list[STRForecastScenario]:
    """The three out-of-the-box forecast branches.

    These mirror institutional rules of thumb (mid-cycle RevPAR growth
    rates from CBRE Hotels Horizons + STR trailing-decade nominal
    lodging growth). Analysts can override per-scenario through the
    POST /str-forecast/scenarios endpoint and ``build_str_forecast``
    will accept the new ``STRForecastScenario`` list verbatim.
    """
    return [
        STRForecastScenario(
            name="downside",
            revpar_cagr_pct=-0.02,
            revpar_index_target=0.92,
            occupancy_floor=0.55,
            adr_floor=0.80,
            notes=[
                "Recessionary cycle; subject loses share to comp set.",
                "Comp-set RevPAR contracts 2% annualized over 24 months.",
            ],
        ),
        STRForecastScenario(
            name="base",
            revpar_cagr_pct=0.025,
            revpar_index_target=1.00,
            occupancy_floor=0.60,
            adr_floor=0.88,
            notes=[
                "Mid-cycle; subject converges to comp set parity.",
                "Long-run nominal lodging RevPAR growth ~2.5%.",
            ],
        ),
        STRForecastScenario(
            name="upside",
            revpar_cagr_pct=0.05,
            revpar_index_target=1.06,
            occupancy_floor=0.65,
            adr_floor=0.92,
            notes=[
                "Post-PIP RevPAR lift OR market-cycle peak.",
                "Subject pulls ahead of comp set by ~6% by month 24.",
            ],
        ),
    ]


def _coverage_quality(n: int) -> CoverageQuality:
    if n >= _FORECAST_MONTHS:
        return "high"
    if n >= _TRAILING_WINDOW:
        return "medium"
    return "low"


def _sorted_history(historical: Iterable[STRMonth]) -> list[STRMonth]:
    """Sort ASCENDING by ``period`` and keep only the trailing N months.

    Caller can pass DESC or ASC; we normalize. We also clip to the most
    recent ``_FORECAST_MONTHS`` so a longer history doesn't bloat the
    response. ``period`` is YYYY-MM so lexical sort == chronological.
    """
    rows = sorted(
        (m for m in historical),
        key=lambda m: m.period,
    )
    return rows[-_FORECAST_MONTHS:]


def _trailing_avg(history: list[STRMonth]) -> tuple[float, float, float, float, float]:
    """Trailing-12 averages: (occ, adr, revpar, comp_revpar, index).

    When fewer than 12 historical rows are available we average what
    we have (down to 1). The caller has already gated coverage_quality,
    so this stays defensive but never crashes.
    """
    window = history[-_TRAILING_WINDOW:] if len(history) >= _TRAILING_WINDOW else history
    n = max(1, len(window))
    occ_sum = sum(m.occupancy for m in window)
    adr_sum = sum(m.adr for m in window)
    revpar_sum = sum(m.revpar for m in window)
    comp_sum = sum(m.comp_set_revpar for m in window)
    idx_sum = sum(m.revpar_index for m in window)
    return (
        occ_sum / n,
        adr_sum / n,
        revpar_sum / n,
        comp_sum / n,
        idx_sum / n,
    )


def _add_months(yyyy_mm: str, delta: int) -> str:
    """Return YYYY-MM ``delta`` months after ``yyyy_mm``.

    No external dateutil dependency — the math is trivial enough to
    inline. ``delta=1`` advances by one month; ``delta=0`` returns
    the input unchanged.
    """
    year, month = yyyy_mm.split("-")
    y = int(year)
    m = int(month) + delta
    # Normalize month into 1..12 with year carryover.
    y += (m - 1) // 12
    m = ((m - 1) % 12) + 1
    return f"{y:04d}-{m:02d}"


def _decompose(
    revpar: float,
    occ_adr_ratio: float,
    occupancy_floor: float,
    adr_floor_usd: float,
) -> tuple[float, float]:
    """Decompose ``revpar`` into ``(occupancy, adr)`` honouring floors.

    Preserves the trailing-12 occ:adr ratio when neither floor binds.
    Hits the floors when the math would otherwise dip below them.
    """
    if revpar <= 0 or occ_adr_ratio <= 0:
        # Degenerate input — hold both at their floors, accept that
        # RevPAR will round to the floor product.
        occ = max(occupancy_floor, 0.0)
        adr = max(adr_floor_usd, 0.0)
        return occ, adr

    # Solve from the system { occ * adr = revpar, occ / adr = r }.
    occ = math.sqrt(revpar * occ_adr_ratio)
    adr = math.sqrt(revpar / occ_adr_ratio)

    # Clamp occupancy to [floor, 0.95]. The 0.95 ceiling matches the
    # revenue engine's institutional max occupancy (rooms_available is
    # capped so a comp-set spike can't push subject occ to 100%).
    if occ < occupancy_floor:
        occ = occupancy_floor
        adr = revpar / occ if occ > 0 else adr
    elif occ > 0.95:
        occ = 0.95
        adr = revpar / occ

    # ADR floor — interpreted in USD (caller has already multiplied the
    # multiplier × trailing-12 ADR).
    if adr < adr_floor_usd:
        adr = adr_floor_usd
        # When ADR pins to its floor we may need to lift occupancy back
        # above its own floor to satisfy revpar; but if occupancy is
        # ALREADY at the occ floor we leave both floors binding and
        # accept the lower RevPAR.
        if occ < 0.95:
            new_occ = revpar / adr if adr > 0 else occ
            occ = min(0.95, max(occupancy_floor, new_occ))

    return occ, adr


def _project_one_scenario(
    *,
    history: list[STRMonth],
    scenario: STRForecastScenario,
    trailing12_occ: float,
    trailing12_adr: float,
    trailing12_comp_revpar: float,
    trailing12_index: float,
) -> list[STRMonth]:
    """Forward-project 24 months for one scenario.

    Math walk:
    1. comp_revpar[m] = trailing12_comp_revpar * (1 + CAGR)^(m/12).
    2. subject_index[m] = trailing12_index + (target - trailing12_index) * (m / 24).
    3. subject_revpar[m] = comp_revpar[m] * subject_index[m].
    4. Decompose subject_revpar[m] back into (occ, adr) honouring floors.
    """
    last_period = history[-1].period
    # ADR floor: scenario.adr_floor is a multiplier (0.0..2.0) against
    # the trailing-12 average ADR. Convert to a USD floor here so the
    # decomposer can work in absolute dollars.
    adr_floor_usd = scenario.adr_floor * trailing12_adr
    occ_adr_ratio = (
        trailing12_occ / trailing12_adr if trailing12_adr > 0 else 0.0
    )

    out: list[STRMonth] = []
    for step in range(1, _FORECAST_MONTHS + 1):
        # 1. Comp-set RevPAR at scenario CAGR (annualized → monthly).
        comp_revpar = trailing12_comp_revpar * (
            (1.0 + scenario.revpar_cagr_pct) ** (step / 12.0)
        )
        comp_revpar = max(comp_revpar, _FLOOR_EPSILON)

        # 2. Linear interpolation of the subject's RevPAR Index.
        subject_index = trailing12_index + (
            (scenario.revpar_index_target - trailing12_index) * (step / _FORECAST_MONTHS)
        )
        subject_index = max(subject_index, _FLOOR_EPSILON)

        # 3. Subject RevPAR from comp × index.
        subject_revpar = comp_revpar * subject_index

        # 4. Decompose into occ × ADR with floors.
        occ, adr = _decompose(
            revpar=subject_revpar,
            occ_adr_ratio=occ_adr_ratio,
            occupancy_floor=scenario.occupancy_floor,
            adr_floor_usd=adr_floor_usd,
        )

        # If a floor bit and changed the (occ, adr) pair, recompute
        # subject_revpar so the row is internally consistent (occ × adr
        # == revpar). This is what the institutional model expects —
        # the scenario's INTENT is comp × index, but the GUARDRAIL is
        # the floors, and when they fight the floors win.
        final_revpar = occ * adr
        # And recompute index from the floored revpar so the chart
        # row labelled "index" matches the displayed (occ, adr).
        final_index = final_revpar / comp_revpar if comp_revpar > 0 else subject_index

        period = _add_months(last_period, step)
        out.append(
            STRMonth(
                period=period,
                occupancy=round(occ, 6),
                adr=round(adr, 4),
                revpar=round(final_revpar, 4),
                comp_set_revpar=round(comp_revpar, 4),
                revpar_index=round(final_index, 6),
                is_historical=False,
            )
        )

    return out


def build_str_forecast(
    *,
    deal_id: str,
    historical_months: list[STRMonth],
    scenario_overrides: list[STRForecastScenario] | None = None,
) -> STRForecastResult:
    """Build the 24-month forward forecast across 3 scenarios.

    Args:
        deal_id: the deal this forecast belongs to (carried through to
            the API response).
        historical_months: 24-ish months of subject + comp-set RevPAR
            history (most recent N kept; chronological order doesn't
            matter — the engine sorts ASC).
        scenario_overrides: optional list of analyst-edited scenario
            settings. When the list contains a scenario named
            ``"base"`` it REPLACES the default base scenario; same for
            ``"downside"`` and ``"upside"``. Scenarios not present in
            the override list fall back to defaults.

    Returns:
        A populated ``STRForecastResult``. When coverage_quality is
        ``"low"`` (<12 historical months) the ``forecast_months`` dict
        is keyed but empty per scenario — the UI is expected to render
        a banner rather than a chart.
    """
    history = _sorted_history(historical_months)
    history = [m.model_copy(update={"is_historical": True}) for m in history]
    coverage = _coverage_quality(len(history))

    # Resolve scenarios — apply analyst overrides on top of defaults.
    defaults_by_name = {s.name: s for s in default_scenarios()}
    if scenario_overrides:
        for ov in scenario_overrides:
            defaults_by_name[ov.name] = ov
    scenarios = [defaults_by_name[name] for name in ("downside", "base", "upside")]

    if coverage == "low":
        return STRForecastResult(
            deal_id=deal_id,
            historical_months=history,
            forecast_months={s.name: [] for s in scenarios},
            scenario_settings=scenarios,
            coverage_quality=coverage,
        )

    (
        trailing12_occ,
        trailing12_adr,
        _trailing12_revpar,
        trailing12_comp_revpar,
        trailing12_index,
    ) = _trailing_avg(history)

    forecast_by_name: dict[str, list[STRMonth]] = {}
    for scenario in scenarios:
        forecast_by_name[scenario.name] = _project_one_scenario(
            history=history,
            scenario=scenario,
            trailing12_occ=trailing12_occ,
            trailing12_adr=trailing12_adr,
            trailing12_comp_revpar=trailing12_comp_revpar,
            trailing12_index=trailing12_index,
        )

    return STRForecastResult(
        deal_id=deal_id,
        historical_months=history,
        forecast_months=forecast_by_name,
        scenario_settings=scenarios,
        coverage_quality=coverage,
    )


__all__ = [
    "build_str_forecast",
    "default_scenarios",
]
