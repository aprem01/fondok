"""USALI-aligned hotel financials.

USALI = Uniform System of Accounts for the Lodging Industry. The structure
mirrors the standard P&L layout an analyst would see in a hotel T-12.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class FoodBeverageDetail(BaseModel):
    """USALI 11th-edition F&B revenue / cost breakdown.

    The CBRE Benchmarker P&L splits F&B four ways: in-restaurant
    (Venues), Room Service, Mini-Bar, and Banquet. When the source
    document carries the split, populate this block; otherwise leave
    ``None`` and rely on the rolled-up ``DepartmentalExpenses.food_beverage``.
    """

    model_config = ConfigDict(extra="forbid")

    venues: Annotated[float, Field(ge=0)] = 0.0
    room_service: Annotated[float, Field(ge=0)] = 0.0
    mini_bar: Annotated[float, Field(ge=0)] = 0.0
    banquet: Annotated[float, Field(ge=0)] = 0.0


class UtilitiesDetail(BaseModel):
    """USALI 11th-edition Utilities five-way split.

    Real CBRE Benchmarker reports break Utilities into Electricity,
    Water/Sewer, Steam, Gas/Fuel, and Other. Optional — falls back to
    the rolled-up ``UndistributedExpenses.utilities`` when not extracted.
    """

    model_config = ConfigDict(extra="forbid")

    electricity: Annotated[float, Field(ge=0)] = 0.0
    water_sewer: Annotated[float, Field(ge=0)] = 0.0
    steam: Annotated[float, Field(ge=0)] = 0.0
    gas_fuel: Annotated[float, Field(ge=0)] = 0.0
    other: Annotated[float, Field(ge=0)] = 0.0


class LaborByDepartment(BaseModel):
    """USALI 11th-edition labor breakdown for a single department.

    Mirrors the CBRE Benchmarker columns: Salaries-Mgmt,
    Salaries-NonMgmt, Service Charge Distribution, Contract/Leased,
    Bonuses, Unassigned, plus the Payroll-Related line that sits on
    top. Each department (rooms / fb / a_and_g / it / sales_marketing /
    maintenance) carries one of these blocks when the source supports
    the split.
    """

    model_config = ConfigDict(extra="forbid")

    salaries_management: Annotated[float, Field(ge=0)] = 0.0
    salaries_non_management: Annotated[float, Field(ge=0)] = 0.0
    service_charge_distribution: Annotated[float, Field(ge=0)] = 0.0
    contract_labor: Annotated[float, Field(ge=0)] = 0.0
    bonuses_incentives: Annotated[float, Field(ge=0)] = 0.0
    unassigned_salaries: Annotated[float, Field(ge=0)] = 0.0
    payroll_related: Annotated[float, Field(ge=0)] = 0.0


class DepartmentalExpenses(BaseModel):
    """Direct costs by revenue department."""

    model_config = ConfigDict(extra="forbid")

    rooms: Annotated[float, Field(ge=0)] = 0.0
    food_beverage: Annotated[float, Field(ge=0)] = 0.0
    other_operated: Annotated[float, Field(ge=0)] = 0.0
    total: Annotated[float, Field(ge=0)] = 0.0
    # Optional richer F&B split when the source document supports it
    # (CBRE Benchmarker, full USALI 11th P&Ls). When absent the
    # rolled-up ``food_beverage`` line carries the full F&B cost.
    fb_detail: FoodBeverageDetail | None = None


class UndistributedExpenses(BaseModel):
    """Operating costs not allocated to a single revenue department."""

    model_config = ConfigDict(extra="forbid")

    administrative_general: Annotated[float, Field(ge=0)] = 0.0
    information_telecom: Annotated[float, Field(ge=0)] = 0.0
    sales_marketing: Annotated[float, Field(ge=0)] = 0.0
    property_operations: Annotated[float, Field(ge=0)] = 0.0
    utilities: Annotated[float, Field(ge=0)] = 0.0
    # USALI 11th edition added Service Charge Distribution as a
    # standalone line — required for proper labor accounting on
    # service-charge hotels (resorts, all-inclusives). Defaults to 0.0
    # so existing T-12 payloads stay valid.
    service_charge_distribution: Annotated[float, Field(ge=0)] = 0.0
    total: Annotated[float, Field(ge=0)] = 0.0
    # Optional five-way utilities split when the source carries it
    # (CBRE Benchmarker electricity / water-sewer / steam / gas-fuel /
    # other). Falls back to the ``utilities`` rollup when ``None``.
    utilities_detail: UtilitiesDetail | None = None


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

    # Optional per-department labor breakdown when the source carries
    # USALI 11th-edition labor accounting. Keys are department slugs:
    # ``rooms``, ``fb``, ``a_and_g``, ``it``, ``sales_marketing``,
    # ``maintenance``. Empty dict when the source only carries
    # rolled-up labor numbers.
    labor_by_dept: dict[str, LaborByDepartment] = Field(default_factory=dict)


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
