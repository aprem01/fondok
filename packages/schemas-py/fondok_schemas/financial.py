"""USALI-aligned hotel financials.

USALI = Uniform System of Accounts for the Lodging Industry. The structure
mirrors the standard P&L layout an analyst would see in a hotel T-12.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class DepartmentalExpenses(BaseModel):
    """Direct costs by revenue department."""

    model_config = ConfigDict(extra="forbid")

    rooms: Annotated[float, Field(ge=0)] = 0.0
    food_beverage: Annotated[float, Field(ge=0)] = 0.0
    other_operated: Annotated[float, Field(ge=0)] = 0.0
    total: Annotated[float, Field(ge=0)] = 0.0


class UndistributedExpenses(BaseModel):
    """Operating costs not allocated to a single revenue department."""

    model_config = ConfigDict(extra="forbid")

    administrative_general: Annotated[float, Field(ge=0)] = 0.0
    information_telecom: Annotated[float, Field(ge=0)] = 0.0
    sales_marketing: Annotated[float, Field(ge=0)] = 0.0
    property_operations: Annotated[float, Field(ge=0)] = 0.0
    utilities: Annotated[float, Field(ge=0)] = 0.0
    total: Annotated[float, Field(ge=0)] = 0.0


class FixedCharges(BaseModel):
    """Below-the-line fixed costs."""

    model_config = ConfigDict(extra="forbid")

    property_taxes: Annotated[float, Field(ge=0)] = 0.0
    insurance: Annotated[float, Field(ge=0)] = 0.0
    rent: Annotated[float, Field(ge=0)] = 0.0
    other_fixed: Annotated[float, Field(ge=0)] = 0.0
    total: Annotated[float, Field(ge=0)] = 0.0


class USALIFinancials(BaseModel):
    """Single-period USALI-aligned hotel P&L."""

    model_config = ConfigDict(extra="forbid")

    period_label: Annotated[str, Field(min_length=1, max_length=80)]

    # Revenue
    rooms_revenue: Annotated[float, Field(ge=0)]
    fb_revenue: Annotated[float, Field(ge=0)] = 0.0
    # Resort Fees — broken out as a distinct USALI 11th edition revenue
    # line. Sam QA #11: these were being aggregated into Misc / Other
    # Income, hiding ~$1M of revenue per year on a real deal. Defaults
    # to 0.0 so existing payloads stay valid; extractor + normalizer
    # populate when the source document carries a separate Resort Fees
    # line.
    resort_fees: Annotated[float, Field(ge=0)] = 0.0
    other_revenue: Annotated[float, Field(ge=0)] = 0.0
    total_revenue: Annotated[float, Field(ge=0)]

    # Costs
    dept_expenses: DepartmentalExpenses = Field(default_factory=DepartmentalExpenses)
    undistributed: UndistributedExpenses = Field(default_factory=UndistributedExpenses)
    mgmt_fee: Annotated[float, Field(ge=0)] = 0.0
    ffe_reserve: Annotated[float, Field(ge=0)] = 0.0
    fixed_charges: FixedCharges = Field(default_factory=FixedCharges)

    # Computed lines
    gop: float = Field(description="Gross Operating Profit = Total Revenue - Dept - Undistributed.")
    noi: float = Field(description="Net Operating Income, post mgmt fee, FF&E reserve, fixed charges.")
    opex_ratio: Annotated[float, Field(ge=0.0, le=2.0)] = Field(
        description="Total operating expenses / total revenue."
    )

    # Operational KPIs (room-level)
    occupancy: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    adr: Annotated[float, Field(ge=0)] | None = None
    revpar: Annotated[float, Field(ge=0)] | None = None


class ModelAssumptions(BaseModel):
    """Underwriting assumption set bound to a deal."""

    model_config = ConfigDict(extra="forbid")

    purchase_price: Annotated[float, Field(gt=0)]
    price_per_key: Annotated[float, Field(gt=0)] | None = None
    ltv: Annotated[float, Field(ge=0.0, le=1.0)]
    interest_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    amortization_years: Annotated[int, Field(ge=0, le=40)] = 30
    loan_term_years: Annotated[int, Field(ge=1, le=40)] = 5
    hold_years: Annotated[int, Field(ge=1, le=20)]
    exit_cap_rate: Annotated[float, Field(gt=0.0, le=0.30)]
    entry_cap_rate: Annotated[float, Field(gt=0.0, le=0.30)] | None = None
    revpar_growth: Annotated[float, Field(ge=-0.50, le=0.50)]
    expense_growth: Annotated[float, Field(ge=-0.50, le=0.50)] = 0.03
    selling_costs_pct: Annotated[float, Field(ge=0.0, le=0.10)] = 0.02
    closing_costs_pct: Annotated[float, Field(ge=0.0, le=0.10)] = 0.02
