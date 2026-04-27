"""Expense engine — undistributed and fixed operating expenses by department.

Implements a simplified USALI flow:

    Total Revenue
      - Departmental Expenses (rooms, F&B, other)
      - Undistributed (admin&general, sales&marketing, utilities, R&M, IT)
      = GOP
      - Management Fee  (% of total revenue)
      - FF&E Reserve     (% of total revenue)
      - Fixed Charges    (taxes, insurance, ground rent)
      = NOI
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fondok_schemas.financial import DepartmentalExpenses, FixedCharges, UndistributedExpenses

from .base import BaseEngine
from .fb_revenue import FBRevenueOutput


# Industry margin defaults (expense as % of *its* revenue category for departmental,
# % of total revenue for undistributed, % of revenue for mgmt/FF&E).
HOTEL_TYPE_DEFAULTS: dict[str, dict[str, float]] = {
    "limited": {
        "rooms_dept_pct": 0.24,
        "fb_dept_pct": 0.50,
        "other_dept_pct": 0.40,
        "undistributed_pct_revenue": 0.18,
        "fixed_pct_revenue": 0.06,
    },
    "select": {
        "rooms_dept_pct": 0.27,
        "fb_dept_pct": 0.65,
        "other_dept_pct": 0.45,
        "undistributed_pct_revenue": 0.22,
        "fixed_pct_revenue": 0.06,
    },
    "full": {
        "rooms_dept_pct": 0.30,
        "fb_dept_pct": 0.75,
        "other_dept_pct": 0.50,
        "undistributed_pct_revenue": 0.26,
        "fixed_pct_revenue": 0.07,
    },
    "lifestyle": {
        "rooms_dept_pct": 0.30,
        "fb_dept_pct": 0.75,
        "other_dept_pct": 0.50,
        "undistributed_pct_revenue": 0.26,
        "fixed_pct_revenue": 0.07,
    },
    "luxury": {
        "rooms_dept_pct": 0.32,
        "fb_dept_pct": 0.78,
        "other_dept_pct": 0.55,
        "undistributed_pct_revenue": 0.30,
        "fixed_pct_revenue": 0.08,
    },
}


class ExpenseEngineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    revenue: FBRevenueOutput
    hotel_type: Literal["limited", "select", "full", "lifestyle", "luxury"] = "full"
    mgmt_fee_pct: Annotated[float, Field(ge=0.0, le=0.10)] = 0.03
    ffe_reserve_pct: Annotated[float, Field(ge=0.0, le=0.10)] = 0.04
    expense_growth: Annotated[float, Field(ge=-0.5, le=0.5)] = 0.035
    overrides: dict[str, float] = Field(default_factory=dict)
    grow_opex_independently: bool = Field(
        default=False,
        description=(
            "When True, departmental + undistributed + fixed Y1 amounts are "
            "computed from the ratios, then grown at ``expense_growth`` for "
            "Y2..Yn. Mgmt fee and FF&E reserve always remain proportional to "
            "revenue. Use this for the standard hotel underwriting convention "
            "where opex grows ~3-3.5% while top line grows ~5%."
        ),
    )


class ExpenseYear(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=1)]
    total_revenue: Annotated[float, Field(ge=0)]
    dept_expenses: DepartmentalExpenses
    undistributed: UndistributedExpenses
    mgmt_fee: Annotated[float, Field(ge=0)]
    ffe_reserve: Annotated[float, Field(ge=0)]
    fixed_charges: FixedCharges
    gop: float
    noi: float


class ExpenseEngineOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    years: list[ExpenseYear]
    noi_cagr: float


class ExpenseEngine(BaseEngine[ExpenseEngineInput, ExpenseEngineOutput]):
    """Compute departmental, undistributed, fixed expenses → GOP → NOI."""

    name = "expense"

    def run(self, payload: ExpenseEngineInput) -> ExpenseEngineOutput:
        defaults = {
            **HOTEL_TYPE_DEFAULTS.get(payload.hotel_type, HOTEL_TYPE_DEFAULTS["full"]),
            **payload.overrides,
        }

        years: list[ExpenseYear] = []
        # Year-1 anchors used when growing opex independently of revenue.
        y1_dept_rooms = y1_dept_fb = y1_dept_other = 0.0
        y1_undist = y1_fixed = 0.0

        for idx, rev_year in enumerate(payload.revenue.years):
            rooms = rev_year.rooms_revenue
            fb = rev_year.fb_revenue
            other = rev_year.other_revenue
            total = rev_year.total_revenue

            if payload.grow_opex_independently and idx > 0:
                growth = (1.0 + payload.expense_growth) ** idx
                dept_rooms = y1_dept_rooms * growth
                dept_fb = y1_dept_fb * growth
                dept_other = y1_dept_other * growth
                undist_total = y1_undist * growth
                fixed_total = y1_fixed * growth
            else:
                dept_rooms = rooms * defaults["rooms_dept_pct"]
                dept_fb = fb * defaults["fb_dept_pct"]
                dept_other = other * defaults["other_dept_pct"]
                undist_total = total * defaults["undistributed_pct_revenue"]
                fixed_total = total * defaults["fixed_pct_revenue"]
                if idx == 0:
                    y1_dept_rooms, y1_dept_fb, y1_dept_other = dept_rooms, dept_fb, dept_other
                    y1_undist, y1_fixed = undist_total, fixed_total

            dept_total = dept_rooms + dept_fb + dept_other
            # Spread undistributed across canonical USALI buckets.
            undist = UndistributedExpenses(
                administrative_general=undist_total * 0.30,
                information_telecom=undist_total * 0.06,
                sales_marketing=undist_total * 0.24,
                property_operations=undist_total * 0.16,
                utilities=undist_total * 0.24,
                total=undist_total,
            )

            gop = total - dept_total - undist_total
            mgmt_fee = total * payload.mgmt_fee_pct
            ffe = total * payload.ffe_reserve_pct
            fixed = FixedCharges(
                property_taxes=fixed_total * 0.45,
                insurance=fixed_total * 0.45,
                rent=0.0,
                other_fixed=fixed_total * 0.10,
                total=fixed_total,
            )
            noi = gop - mgmt_fee - ffe - fixed_total

            years.append(
                ExpenseYear(
                    year=rev_year.year,
                    total_revenue=total,
                    dept_expenses=DepartmentalExpenses(
                        rooms=dept_rooms,
                        food_beverage=dept_fb,
                        other_operated=dept_other,
                        total=dept_total,
                    ),
                    undistributed=undist,
                    mgmt_fee=mgmt_fee,
                    ffe_reserve=ffe,
                    fixed_charges=fixed,
                    gop=gop,
                    noi=noi,
                )
            )

        if len(years) >= 2 and years[0].noi > 0:
            n = len(years) - 1
            noi_cagr = (years[-1].noi / years[0].noi) ** (1 / n) - 1
        else:
            noi_cagr = 0.0

        return ExpenseEngineOutput(
            deal_id=payload.deal_id,
            years=years,
            noi_cagr=noi_cagr,
        )


__all__ = [
    "ExpenseEngine",
    "ExpenseEngineInput",
    "ExpenseEngineOutput",
    "ExpenseYear",
    "HOTEL_TYPE_DEFAULTS",
]
