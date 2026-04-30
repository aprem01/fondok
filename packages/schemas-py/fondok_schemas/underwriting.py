"""Engine inputs/outputs — one boundary per underwriting engine.

Engines: Investment, Revenue, P&L, Debt, Cash Flow, Returns.
Partnership lives in `partnership.py` because it composes returns + waterfall.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .financial import ModelAssumptions, USALIFinancials


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
