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
    # Year-1 actuals lifted off the deal's extracted T-12. When present we
    # use these as the Y1 anchor instead of synthesizing from USALI ratios
    # — the synthesized numbers were Sam's QA #1 complaint (Insurance
    # $457K vs actual $1.16M; Utilities $905K vs actual $288K). Keys are
    # canonical USALI line names; missing keys still fall back to ratio
    # synthesis so partial extraction degrades gracefully.
    t12_actuals: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Year-1 actual expense lines extracted from the T-12. "
            "Recognized keys: rooms_dept_expense, fb_dept_expense, "
            "other_dept_expense, administrative_general, "
            "information_telecom, sales_marketing, property_operations, "
            "utilities, insurance, property_taxes, mgmt_fee, ffe_reserve."
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
    # Lines whose Y1 came from the T-12 actuals rather than USALI ratios.
    # The web app surfaces this list as a footnote on the Operating
    # Statement so reviewers know which numbers are real vs synthesized.
    sourced_from_t12: list[str] = Field(default_factory=list)


class ExpenseEngine(BaseEngine[ExpenseEngineInput, ExpenseEngineOutput]):
    """Compute departmental, undistributed, fixed expenses → GOP → NOI."""

    name = "expense"

    def run(self, payload: ExpenseEngineInput) -> ExpenseEngineOutput:
        defaults = {
            **HOTEL_TYPE_DEFAULTS.get(payload.hotel_type, HOTEL_TYPE_DEFAULTS["full"]),
            **payload.overrides,
        }
        actuals = payload.t12_actuals or {}

        # Track which lines we sourced from the T-12 actuals so the UI
        # can show a per-line "from T-12" badge. We only mark Y1 — out-
        # years are always grown forward from the chosen Y1 anchor.
        sourced: list[str] = sorted(actuals.keys()) if actuals else []

        # Undistributed canonical USALI bucket weights. Sales & Marketing
        # and Utilities used to share the same 0.24 multiplier, which
        # produced identical $ figures (Sam QA #1: "S&M and Utilities
        # both exactly $905K — clearly a benchmark formula error"). The
        # corrected mix below sums to 1.0 with realistic industry shares.
        UNDIST_WEIGHTS = {
            "administrative_general": 0.28,
            "information_telecom": 0.07,
            "sales_marketing": 0.20,
            "property_operations": 0.20,
            "utilities": 0.25,
        }
        # Fixed charges canonical mix.
        FIXED_WEIGHTS = {
            "property_taxes": 0.55,
            "insurance": 0.40,
            "other_fixed": 0.05,
        }

        def _undist_from_actuals_or_share(
            line: str, undist_total: float, *, use_actual: bool
        ) -> float:
            if use_actual and line in actuals:
                return float(actuals[line])
            return undist_total * UNDIST_WEIGHTS[line]

        def _fixed_from_actuals_or_share(
            line: str, fixed_total: float, *, use_actual: bool
        ) -> float:
            if use_actual and line in actuals:
                return float(actuals[line])
            return fixed_total * FIXED_WEIGHTS[line]

        years: list[ExpenseYear] = []
        # Year-1 anchors used when growing opex independently of revenue.
        y1_dept_rooms = y1_dept_fb = y1_dept_other = 0.0
        y1_undist_lines: dict[str, float] = {}
        y1_fixed_lines: dict[str, float] = {}
        y1_undist_total = y1_fixed_total = 0.0

        for idx, rev_year in enumerate(payload.revenue.years):
            rooms = rev_year.rooms_revenue
            fb = rev_year.fb_revenue
            other = rev_year.other_revenue
            total = rev_year.total_revenue
            is_y1 = idx == 0
            use_actuals = is_y1 and bool(actuals)

            if payload.grow_opex_independently and idx > 0:
                growth = (1.0 + payload.expense_growth) ** idx
                dept_rooms = y1_dept_rooms * growth
                dept_fb = y1_dept_fb * growth
                dept_other = y1_dept_other * growth
                undist_lines = {k: v * growth for k, v in y1_undist_lines.items()}
                fixed_lines = {k: v * growth for k, v in y1_fixed_lines.items()}
                undist_total = y1_undist_total * growth
                fixed_total = y1_fixed_total * growth
            else:
                # Departmental — Y1 actuals if present, else ratio.
                dept_rooms = (
                    actuals["rooms_dept_expense"]
                    if use_actuals and "rooms_dept_expense" in actuals
                    else rooms * defaults["rooms_dept_pct"]
                )
                dept_fb = (
                    actuals["fb_dept_expense"]
                    if use_actuals and "fb_dept_expense" in actuals
                    else fb * defaults["fb_dept_pct"]
                )
                dept_other = (
                    actuals["other_dept_expense"]
                    if use_actuals and "other_dept_expense" in actuals
                    else other * defaults["other_dept_pct"]
                )

                # Undistributed: build per-line first so actual overrides
                # take effect; fall back to weighted share of the synth
                # total for missing lines. The total is then the sum.
                undist_pool_total = total * defaults["undistributed_pct_revenue"]
                undist_lines = {
                    line: _undist_from_actuals_or_share(
                        line, undist_pool_total, use_actual=use_actuals
                    )
                    for line in UNDIST_WEIGHTS
                }
                undist_total = sum(undist_lines.values())

                # Fixed charges: same pattern.
                fixed_pool_total = total * defaults["fixed_pct_revenue"]
                fixed_lines = {
                    line: _fixed_from_actuals_or_share(
                        line, fixed_pool_total, use_actual=use_actuals
                    )
                    for line in FIXED_WEIGHTS
                }
                fixed_total = sum(fixed_lines.values())

                if is_y1:
                    y1_dept_rooms, y1_dept_fb, y1_dept_other = dept_rooms, dept_fb, dept_other
                    y1_undist_lines = dict(undist_lines)
                    y1_undist_total = undist_total
                    y1_fixed_lines = dict(fixed_lines)
                    y1_fixed_total = fixed_total

            dept_total = dept_rooms + dept_fb + dept_other
            undist = UndistributedExpenses(
                administrative_general=undist_lines["administrative_general"],
                information_telecom=undist_lines["information_telecom"],
                sales_marketing=undist_lines["sales_marketing"],
                property_operations=undist_lines["property_operations"],
                utilities=undist_lines["utilities"],
                total=undist_total,
            )

            gop = total - dept_total - undist_total
            mgmt_fee = (
                actuals["mgmt_fee"]
                if use_actuals and "mgmt_fee" in actuals
                else total * payload.mgmt_fee_pct
            )
            ffe = (
                actuals["ffe_reserve"]
                if use_actuals and "ffe_reserve" in actuals
                else total * payload.ffe_reserve_pct
            )
            fixed = FixedCharges(
                property_taxes=fixed_lines["property_taxes"],
                insurance=fixed_lines["insurance"],
                rent=0.0,
                other_fixed=fixed_lines["other_fixed"],
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
            sourced_from_t12=sourced,
        )


__all__ = [
    "ExpenseEngine",
    "ExpenseEngineInput",
    "ExpenseEngineOutput",
    "ExpenseYear",
    "HOTEL_TYPE_DEFAULTS",
]
