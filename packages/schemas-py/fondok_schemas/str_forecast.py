"""STR forward-forecast schema (Wave 3 W3.3).

Sam's June 2026 ask: "We ingested 24 months of STR Trend data — now
project the NEXT 24 months across THREE scenarios (downside, base,
upside) so analysts can flex assumptions on each branch and see how
the subject's RevPAR moves vs the comp set."

Today the STR Trend extraction already lands the trailing 24 months
of monthly Occupancy / ADR / RevPAR for both the subject hotel AND
the comp set. This module models the **forward** 24-month forecast
on top of that history:

* Three default scenarios (downside / base / upside) — each with a
  RevPAR CAGR knob, a subject-vs-compset index target, and occupancy
  + ADR floors that prevent the math from drifting below institutional
  thresholds.
* Linear interpolation of the subject's RevPAR Index from "today" to
  the scenario's month-24 target — so a comp-set-catch-up scenario
  (index 0.92 → 0.95) walks up smoothly rather than snapping.
* Comp-set RevPAR projected forward at the scenario's CAGR off the
  trailing-12 average comp RevPAR (smoothes one-month outliers).
* Subject RevPAR decomposed back into occupancy × ADR using the
  trailing-12 occ:adr mix, then clipped to per-scenario floors.

The output is a single ``STRForecastResult`` consumed both by the
``GET /deals/{id}/str-forecast`` endpoint and by the optional
revenue-engine seeding hook (when the analyst opts in,
``RevenueEngineInput.starting_occupancy`` and ``starting_adr`` get
seeded from the BASE scenario's Month-12 values so the rooms-revenue
projection inherits the STR forecast's bottom-up math rather than the
T-12 / Kimpton-seed defaults).

All models are Pydantic v2, asyncpg-safe (JSON-serializable through
the analysis API), and round-trip cleanly to the TypeScript schema
package that the web app consumes.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


# Type alias for the three canonical scenario names. Centralised here
# so the engine + API + tests all refer to the same Literal. Named
# ``STRForecastScenarioName`` so it does not collide with the existing
# ``ScenarioName`` *class* in ``underwriting`` (different concern — the
# underwriting one names downside/base/upside variants of the IRR/EM
# bundle, not the STR forecast branches).
STRForecastScenarioName = Literal["downside", "base", "upside"]


class STRMonth(BaseModel):
    """One row in the STR history-or-forecast monthly time series.

    The same model is used for HISTORICAL months (ingested from the
    STR Trend extraction) and for FORECAST months (projected by the
    engine). ``is_historical`` is the discriminator the UI uses to
    draw the historical curve solid and the forecast curves dashed.
    """

    model_config = ConfigDict(extra="forbid")

    period: Annotated[str, Field(min_length=7, max_length=7, description="YYYY-MM")]
    occupancy: Annotated[float, Field(ge=0.0, le=1.0)]
    adr: Annotated[float, Field(ge=0.0)]
    revpar: Annotated[float, Field(ge=0.0)]
    comp_set_revpar: Annotated[float, Field(ge=0.0)]
    revpar_index: Annotated[float, Field(ge=0.0)]
    is_historical: bool


class STRForecastScenario(BaseModel):
    """Knobs that shape one forecast branch (downside / base / upside).

    ``revpar_cagr_pct`` is the annualized growth rate applied to the
    comp-set RevPAR baseline (NOT directly to the subject — the
    subject's RevPAR is derived from comp × index so its growth is the
    compound of CAGR + index migration).

    ``revpar_index_target`` is where the subject's penetration index
    lands at MONTH 24. 1.00 = parity with the comp set; 0.92 = subject
    runs 8% below comp (downside); 1.06 = subject pulls ahead. The
    engine linearly interpolates from today's index to the target over
    24 months.

    ``occupancy_floor`` and ``adr_floor`` are guardrails: if the
    decomposition math would drop occupancy or ADR below these levels
    in any forecast month, the floor wins. The ADR floor is
    interpreted as a multiplier against the trailing-12 average ADR
    (so 0.88 = 88% of trailing-12 ADR — a level the asset is unlikely
    to drop below without an operational shock).
    """

    model_config = ConfigDict(extra="forbid")

    name: STRForecastScenarioName
    revpar_cagr_pct: Annotated[float, Field(ge=-0.30, le=0.30)]
    revpar_index_target: Annotated[float, Field(ge=0.50, le=1.50)]
    occupancy_floor: Annotated[float, Field(ge=0.0, le=1.0)]
    adr_floor: Annotated[float, Field(ge=0.0, le=2.0)]
    notes: list[str] = Field(default_factory=list)


CoverageQuality = Literal["high", "medium", "low"]


class STRForecastResult(BaseModel):
    """Engine output: 24 months back + 24 months forward × 3 scenarios.

    ``historical_months`` is the most recent 24 months of subject +
    comp-set monthly data ingested from the STR Trend report. When the
    deal has fewer than 24 months on file, the field carries whatever
    is available and ``coverage_quality`` downgrades accordingly:

    * ``high`` — 24+ historical months (the default STR Trend window).
    * ``medium`` — 12-23 months (partial window; forecast still runs
      but trailing-12 averages have less smoothing).
    * ``low`` — < 12 months (forecast disabled; the engine returns
      empty ``forecast_months`` lists and the UI should render a
      "Awaiting more history" banner).

    ``forecast_months`` is keyed by scenario name (``downside`` /
    ``base`` / ``upside``) → 24 forward STRMonth rows.

    ``scenario_settings`` records the EXACT scenario inputs the engine
    ran with (after any analyst overrides), so the UI can hydrate the
    scenario cards without round-tripping back through defaults.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    historical_months: list[STRMonth]
    forecast_months: dict[str, list[STRMonth]]
    scenario_settings: list[STRForecastScenario]
    coverage_quality: CoverageQuality


__all__ = [
    "STRForecastScenarioName",
    "CoverageQuality",
    "STRMonth",
    "STRForecastScenario",
    "STRForecastResult",
]
