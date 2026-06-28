"""Engine inputs/outputs — one boundary per underwriting engine.

Engines: Investment, Revenue, P&L, Debt, Cash Flow, Returns.
Partnership lives in `partnership.py` because it composes returns + waterfall.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
