"""Engine inputs/outputs — one boundary per underwriting engine.

Engines: Investment, Revenue, P&L, Debt, Cash Flow, Returns.
Partnership lives in `partnership.py` because it composes returns + waterfall.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .financial import ModelAssumptions, USALIFinancials


# ─────────────── Revenue Segmentation (Wave 2 P2.1) ───────────────
#
# Institutional revenue model splits rooms revenue into five demand
# segments — transient (BAR + OTA), corporate, group, contract — each
# with its own ADR, mix share, and channel-cost percentage (commissions
# / OTA fees / TMC fees / attrition). Aggregating gross_revenue - channel
# cost yields the canonical `rooms_revenue` the downstream P&L / Returns
# engines consume.
#
# When ``RevenueEngineInput.segments`` is empty the engine runs the
# legacy single-line path (occupied × ADR) unchanged. Every existing
# test continues to pass — segmentation is purely additive.

# Allowed segment ids — keep in lockstep with the engine defaults
# tabled in `apps/worker/app/services/engine_runner.py`.
ALLOWED_SEGMENT_NAMES: frozenset[str] = frozenset({
    "transient_bar",
    "transient_ota",
    "corporate",
    "group",
    "contract",
})


class RevenueSegment(BaseModel):
    """One demand segment in the institutional revenue model.

    ``mix_pct`` is the share of OCCUPIED rooms in this segment (not of
    available rooms). ``channel_cost_pct`` captures OTA commissions /
    TMC fees / group attrition as a percentage of gross segment revenue;
    the engine deducts it before reporting `rooms_revenue` so the canonical
    revenue line is net of distribution cost.

    ``adr_growth`` overrides the engine-level `adr_growth` for this
    segment only — useful when, for example, OTA pricing is expected
    to grow more slowly than corporate due to channel discipline.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=40)]
    mix_pct: Annotated[float, Field(ge=0.0, le=1.0)]
    adr: Annotated[float, Field(ge=0.0)]
    channel_cost_pct: Annotated[float, Field(ge=0.0, le=0.50)] = 0.0
    adr_growth: Annotated[float, Field(ge=-0.50, le=0.50)] | None = None

    @model_validator(mode="after")
    def _check_name_in_allowed(self) -> "RevenueSegment":
        if self.name not in ALLOWED_SEGMENT_NAMES:
            raise ValueError(
                f"segment name {self.name!r} not in allowed set: "
                f"{sorted(ALLOWED_SEGMENT_NAMES)}"
            )
        return self


class SegmentYear(BaseModel):
    """Per-segment per-year output emitted alongside the projection."""

    model_config = ConfigDict(extra="forbid")

    name: str
    mix_pct: Annotated[float, Field(ge=0.0, le=1.0)]
    occupied_rooms: Annotated[float, Field(ge=0.0)]
    # ``adr`` here is the segment's effective ADR AFTER growth and Y1
    # displacement — i.e. the per-year ADR the gross revenue was
    # computed at. Audit-grade: lets a reviewer back-compute every
    # segment's gross_revenue without re-running the engine.
    adr: Annotated[float, Field(ge=0.0)]
    channel_cost_pct: Annotated[float, Field(ge=0.0, le=0.50)]
    gross_revenue: Annotated[float, Field(ge=0.0)]
    net_revenue: Annotated[float, Field(ge=0.0)]


# ─────────────── PIP Displacement v2 (Wave 2 P2.4) ───────────────
#
# Hospitality IC analysts don't model renovation displacement as a single
# flat Y1 percentage. They model it as: a closure strategy (rolling /
# full / wing-by-wing), a month-by-month inventory-offline schedule, a
# brand-specific recovery curve, and a post-PIP RevPAR uplift. The flat
# ``y1_occupancy_displacement_pct`` / ``y1_adr_displacement_pct`` fields
# stay on ``RevenueEngineInput`` as the legacy single-pct path; the new
# ``pip_displacement`` object wins whenever it's set.

PIPClosureStrategy = Literal["rolling", "full_closure", "wing_by_wing", "none"]


class PIPDisplacement(BaseModel):
    """Structured PIP / renovation displacement spec.

    Closure strategy semantics:

    * ``rolling`` — rooms taken offline in batches; remaining inventory
      operates at normal ADR. ``pct_rooms_offline_by_month`` carries the
      fraction of inventory offline each month (0.0 .. 1.0).
    * ``full_closure`` — the hotel is shut. Every month in the reno
      window must be 1.0 (= 100% offline); off-window months 0.0.
    * ``wing_by_wing`` — one wing at a time, capped at 50% offline; a
      5% ADR drag on operating rooms reflects construction nuisance.
    * ``none`` — no PIP. Equivalent to leaving ``pip_displacement`` as
      ``None`` on the engine input.

    ``brand`` — when set, picks brand-specific multipliers on
    ``occupancy_recovery_months`` and ``revpar_index_post_reno`` from
    ``_BRAND_DISPLACEMENT_MULTIPLIERS`` inside the revenue engine.
    Marriott / Hilton are the industry baseline (×1.0 / ×1.0); IHG
    recovers slightly slower; Hyatt slightly faster; Independent /
    soft-brand recovers fastest. These are STR / CBRE Horizons rules
    of thumb — not hard data — analysts can override per-deal.

    ``revpar_index_post_reno`` — Y2+ ADR multiplier (default 1.05 =
    +5%). The whole point of a PIP is to charge more after.

    ``occupancy_recovery_months`` — how many months Y2+ occupancy
    takes to ramp linearly from Y1 ending occupancy back to the
    stabilized baseline. Capped at 12 inside Y2.
    """

    model_config = ConfigDict(extra="forbid")

    closure_strategy: PIPClosureStrategy = "none"
    pct_rooms_offline_by_month: Annotated[
        list[float], Field(default_factory=list, max_length=36)
    ]
    brand: str | None = None
    revpar_index_post_reno: Annotated[float, Field(ge=0.5, le=2.0)] = 1.05
    occupancy_recovery_months: Annotated[int, Field(ge=0, le=36)] = 12

    @field_validator("pct_rooms_offline_by_month")
    @classmethod
    def _check_each_pct_in_unit_interval(cls, v: list[float]) -> list[float]:
        for i, pct in enumerate(v):
            if pct < 0.0 or pct > 1.0:
                raise ValueError(
                    f"pct_rooms_offline_by_month[{i}] = {pct} must be in [0.0, 1.0]"
                )
        return v

    @model_validator(mode="after")
    def _check_strategy_consistency(self) -> "PIPDisplacement":
        if self.closure_strategy == "full_closure":
            # Every month in the schedule must be a clean 1.0 (full
            # closure) or 0.0 (off-window). A partial closure under
            # the full_closure label is an analyst error — flag it
            # rather than silently degrade to a rolling schedule.
            for i, pct in enumerate(self.pct_rooms_offline_by_month):
                if pct not in (0.0, 1.0):
                    raise ValueError(
                        "full_closure strategy requires each "
                        f"pct_rooms_offline_by_month entry to be 0.0 or 1.0; "
                        f"got {pct} at index {i}"
                    )
        if self.closure_strategy == "none":
            for i, pct in enumerate(self.pct_rooms_offline_by_month):
                if pct != 0.0:
                    raise ValueError(
                        "closure_strategy='none' requires every "
                        f"pct_rooms_offline_by_month entry to be 0.0; "
                        f"got {pct} at index {i}"
                    )
        return self


# ─────────────── Capex Three-Bucket Plan (Wave 2 P2.5) ───────────────
#
# Institutional hotel underwriting splits capex into three buckets:
#
#   1. PIP (Property Improvement Plan) - brand-mandated, hard timeline
#      (typically Y1-Y2), non-discretionary, hits balance sheet at
#      closing for IRR / equity-multiple purposes and Y1 NOI via
#      displacement (already modeled on RevenueEngineInput).
#
#   2. Non-PIP / FF&E Reserve - ongoing 3-4% of revenue reserve that
#      smooths over the hold period. Already deducted ABOVE the cap-
#      rate line by the Expense engine via ``ffe_reserve_pct`` - the
#      new ``NonPIPCapex`` model just adds the per-key floor.
#
#   3. ROI capex - discretionary investments (energy retrofits, F&B
#      build-outs, conference centers) that drive incremental NOI.
#      Modeled with their own annual lift x linear ramp.
#
# When ``CapexPlan`` is left at defaults AND the legacy
# ``ffe_reserve_pct`` field is present, the engine renders BYTE-IDENTICAL
# numbers to pre-P2.5 code - Non-PIP collapses into the existing FF&E
# reserve, PIP into the existing ``renovation_budget`` on the capital
# engine, and ROI projects are absent.


class PIPCapex(BaseModel):
    """Property Improvement Plan capex - brand-mandated, time-bound."""

    model_config = ConfigDict(extra="forbid")

    total_usd: Annotated[float, Field(ge=0)]
    per_key_usd: Annotated[float, Field(ge=0)] | None = None
    timing_pct_by_year: list[Annotated[float, Field(ge=0.0, le=1.0)]] = Field(
        default_factory=lambda: [1.0]
    )
    completion_quarter: Annotated[int, Field(ge=1, le=8)] | None = None
    source: Annotated[str, Field(min_length=1, max_length=40)] = "analyst_override"

    @model_validator(mode="after")
    def _check_timing_sums_to_one(self) -> "PIPCapex":
        if not self.timing_pct_by_year:
            raise ValueError("timing_pct_by_year must have at least one entry")
        total = sum(self.timing_pct_by_year)
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"timing_pct_by_year must sum to 1.0 (+/- 0.001); "
                f"got {total:.6f}"
            )
        return self


class NonPIPCapex(BaseModel):
    """Ongoing FF&E reserve - % of revenue with a per-key per-year floor."""

    model_config = ConfigDict(extra="forbid")

    annual_pct_of_revenue: Annotated[float, Field(ge=0.0, le=0.10)] = 0.04
    minimum_per_key_per_year: Annotated[float, Field(ge=0)] = 1500.0
    source: Annotated[str, Field(min_length=1, max_length=40)] = "industry_default"


class ROICapex(BaseModel):
    """Discretionary capex with its own NOI lift curve."""

    model_config = ConfigDict(extra="forbid")

    project_name: Annotated[str, Field(min_length=1, max_length=80)]
    initial_investment_usd: Annotated[float, Field(ge=0)]
    investment_year: Annotated[int, Field(ge=1)]
    annual_noi_lift_usd: Annotated[float, Field(ge=0)] = 0.0
    ramp_months: Annotated[int, Field(ge=1, le=36)] = 12
    source: Annotated[str, Field(min_length=1, max_length=40)] = "analyst_override"


class CapexPlan(BaseModel):
    """Three-bucket capex plan - PIP, ongoing FF&E reserve, ROI projects."""

    model_config = ConfigDict(extra="forbid")

    pip: PIPCapex | None = None
    non_pip: NonPIPCapex = Field(default_factory=NonPIPCapex)
    roi_projects: list[ROICapex] = Field(default_factory=list)


class CapexScheduleYear(BaseModel):
    """One year of the materialized capex schedule."""

    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=1)]
    pip_usd: Annotated[float, Field(ge=0)] = 0.0
    non_pip_usd: Annotated[float, Field(ge=0)] = 0.0
    roi_investment_usd: Annotated[float, Field(ge=0)] = 0.0
    roi_noi_lift_usd: Annotated[float, Field(ge=0)] = 0.0
    total_capex_usd: Annotated[float, Field(ge=0)] = 0.0


# ─────────────── Investment Engine ───────────────


class SourceUseLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(min_length=1, max_length=120)]
    amount: Annotated[float, Field(ge=0)]
    pct: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    is_total: bool = False


class InvestmentEngineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    purchase_price: Annotated[float, Field(gt=0)]
    keys: Annotated[int, Field(gt=0)]
    closing_costs: Annotated[float, Field(ge=0)] = 0.0
    working_capital: Annotated[float, Field(ge=0)] = 0.0
    renovation_budget: Annotated[float, Field(ge=0)] = 0.0
    hard_costs_per_key: Annotated[float, Field(ge=0)] = 0.0
    soft_costs: Annotated[float, Field(ge=0)] = 0.0
    contingency: Annotated[float, Field(ge=0)] = 0.0


class InvestmentEngineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    total_capital: Annotated[float, Field(gt=0)]
    price_per_key: Annotated[float, Field(gt=0)]
    sources: list[SourceUseLine]
    uses: list[SourceUseLine]


# ─────────────── Revenue Engine ───────────────


class RevenueEngineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    keys: Annotated[int, Field(gt=0)]
    starting_occupancy: Annotated[float, Field(ge=0.0, le=1.0)]
    starting_adr: Annotated[float, Field(gt=0)]
    occupancy_growth: Annotated[float, Field(ge=-0.50, le=0.50)] = 0.0
    adr_growth: Annotated[float, Field(ge=-0.50, le=0.50)] = 0.03
    fb_revenue_per_occupied_room: Annotated[float, Field(ge=0)] = 0.0
    other_revenue_pct_of_rooms: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    # Resort Fees Year-1 anchor (USD). Carried as a fixed dollar
    # amount rather than a ratio because resort fees are typically
    # billed per occupied room night and the OM publishes them as a
    # full-year dollar figure. Out-years grow at ``resort_fees_growth``.
    # 0.0 → engine emits 0 resort_fees on every year (default behavior
    # when no T-12 anchor is present).
    starting_resort_fees: Annotated[float, Field(ge=0)] = 0.0
    resort_fees_growth: Annotated[float, Field(ge=-0.50, le=0.50)] = 0.03
    hold_years: Annotated[int, Field(ge=1, le=20)]
    # Year-1 renovation/PIP displacement (Eshan v2 QA). When the deal
    # carries a renovation budget the engine treats Year-1 as a
    # disrupted year — rooms out of service during construction depress
    # occupancy, and disruption / soft-launch rates depress ADR. Year 2+
    # compound forward from the UN-displaced stabilized baseline
    # (``starting_*``), not from the depressed Y1. Pass 0.0 to disable.
    y1_occupancy_displacement_pct: Annotated[float, Field(ge=0.0, le=0.50)] = 0.0
    y1_adr_displacement_pct: Annotated[float, Field(ge=0.0, le=0.50)] = 0.0

    # Wave 2 P2.1 — institutional revenue segmentation. Empty list (the
    # default) preserves the legacy single-line path: occupied × ADR with
    # no channel cost. Populated, the engine runs the five-segment model
    # — see ``RevenueSegment`` and ``apps/worker/app/engines/revenue.py``.
    segments: list[RevenueSegment] = Field(default_factory=list)

    # Wave 2 P2.4 — structured PIP displacement (closure strategy +
    # % rooms offline + brand recovery curve). When ``None`` (or set to
    # the ``none`` strategy), the engine falls back to the flat-pct
    # ``y1_*_displacement_pct`` math above byte-identically. When set
    # with a real strategy, this object overrides the flat-pct path
    # entirely. See ``PIPDisplacement`` and
    # ``apps/worker/app/engines/revenue.py``.
    pip_displacement: PIPDisplacement | None = None

    @model_validator(mode="after")
    def _check_segment_mix_sums_to_one(self) -> "RevenueEngineInput":
        if not self.segments:
            return self
        total = sum(seg.mix_pct for seg in self.segments)
        # 0.1% tolerance covers analyst rounding (e.g. 0.45 + 0.30 +
        # 0.15 + 0.10 = 1.00 exactly; 0.451 + 0.30 + 0.149 + 0.10 also
        # passes). Tighter than that and we'd reject defensible analyst
        # inputs; looser and we'd accept a silently broken mix that
        # mis-scales rooms revenue by several percent.
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"segment mix_pct must sum to 1.0 (±0.001); got {total:.6f}"
            )
        return self


class RevenueProjectionYear(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=1)]
    occupancy: Annotated[float, Field(ge=0.0, le=1.0)]
    adr: Annotated[float, Field(gt=0)]
    revpar: Annotated[float, Field(ge=0)]
    rooms_revenue: Annotated[float, Field(ge=0)]
    fb_revenue: Annotated[float, Field(ge=0)] = 0.0
    # Resort Fees — separate USALI revenue line (Sam QA #11).
    resort_fees: Annotated[float, Field(ge=0)] = 0.0
    other_revenue: Annotated[float, Field(ge=0)] = 0.0
    total_revenue: Annotated[float, Field(ge=0)]
    # Wave 2 P2.1 — per-segment breakdown for this year. Empty on legacy
    # single-line deals so the UI hides the segmentation sub-section.
    segment_breakdown: list[SegmentYear] = Field(default_factory=list)
    # GROSS rooms revenue (before channel cost). When `segment_breakdown`
    # is empty, equals `rooms_revenue`; when populated, equals the sum of
    # per-segment gross_revenue. The IC memo cites both numbers so OTA
    # commission drag is visible side-by-side.
    gross_rooms_revenue: Annotated[float, Field(ge=0)] = 0.0
    # Aggregate channel cost = gross_rooms_revenue - rooms_revenue.
    channel_cost_total: Annotated[float, Field(ge=0)] = 0.0


class RevenueEngineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    years: list[RevenueProjectionYear]
    total_revenue_cagr: float


# ─────────────── P&L Engine ───────────────


class PLEngineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    historical_periods: list[USALIFinancials] = Field(default_factory=list)
    revenue_projection: RevenueEngineOutput | None = None
    expense_growth: Annotated[float, Field(ge=-0.50, le=0.50)] = 0.03
    mgmt_fee_pct: Annotated[float, Field(ge=0.0, le=0.10)] = 0.03
    ffe_reserve_pct: Annotated[float, Field(ge=0.0, le=0.10)] = 0.04


class PLEngineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    projected_periods: list[USALIFinancials]
    noi_cagr: float


# ─────────────── Debt Engine ───────────────


class DebtEngineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    loan_amount: Annotated[float, Field(gt=0)]
    ltv: Annotated[float, Field(ge=0.0, le=1.0)]
    interest_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    term_years: Annotated[int, Field(ge=1, le=40)]
    amortization_years: Annotated[int, Field(ge=0, le=40)] = 30
    interest_only_years: Annotated[int, Field(ge=0, le=10)] = 0


class DebtServiceYear(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=1)]
    interest: Annotated[float, Field(ge=0)]
    principal: Annotated[float, Field(ge=0)]
    debt_service: Annotated[float, Field(ge=0)]
    ending_balance: Annotated[float, Field(ge=0)]
    dscr: Annotated[float, Field(ge=0)] | None = None


class DebtEngineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    annual_debt_service: Annotated[float, Field(ge=0)]
    schedule: list[DebtServiceYear]
    avg_dscr: Annotated[float, Field(ge=0)] | None = None


# ─────────────── Cash Flow Engine ───────────────


class CashFlowEngineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    pl: PLEngineOutput
    debt: DebtEngineOutput


class CashFlowYear(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=1)]
    noi: float
    debt_service: float
    cash_flow_after_debt: float
    capex: Annotated[float, Field(ge=0)] = 0.0


class CashFlowEngineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    years: list[CashFlowYear]


# ─────────────── Returns Engine ───────────────


class ReturnsEngineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    assumptions: ModelAssumptions
    cash_flow: CashFlowEngineOutput
    terminal_noi: Annotated[float, Field(gt=0)]


class ReturnsEngineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    levered_irr: float
    unlevered_irr: float
    equity_multiple: Annotated[float, Field(ge=0)]
    year_one_coc: float
    avg_coc: float
    gross_sale_price: Annotated[float, Field(ge=0)]
    selling_costs: Annotated[float, Field(ge=0)]
    net_proceeds: float
    hold_years: Annotated[int, Field(ge=1, le=20)]


# ─────────────── Scenario Bundles ───────────────


class ScenarioName(BaseModel):
    """Single scenario row — used for downside/base/upside comparisons."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=80)]
    probability: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    irr: float
    unlevered_irr: float | None = None
    equity_multiple: Annotated[float, Field(ge=0)]
    avg_coc: float
    exit_value: Annotated[float, Field(ge=0)] | None = None
    is_base: bool = False
