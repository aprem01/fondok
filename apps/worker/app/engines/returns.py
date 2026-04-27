"""Returns engine — unlevered/levered IRR, equity multiple, cash-on-cash.

Computes the standard private-equity return triplet on an annual cash flow
series. IRR is solved with Newton's method (no SciPy dependency); a
bisection fallback handles cash-flow series where Newton fails to converge.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fondok_schemas.financial import ModelAssumptions
from fondok_schemas.underwriting import (
    CashFlowEngineOutput,
    CashFlowYear,
    ReturnsEngineInput,
    ReturnsEngineOutput,
)

from .base import BaseEngine


# ─────────────── IRR helpers ───────────────


def npv(rate: float, flows: list[float]) -> float:
    """Net present value of ``flows`` at periodic ``rate``."""
    total = 0.0
    for i, cf in enumerate(flows):
        total += cf / ((1.0 + rate) ** i)
    return total


def npv_derivative(rate: float, flows: list[float]) -> float:
    total = 0.0
    for i, cf in enumerate(flows):
        if i == 0:
            continue
        total -= i * cf / ((1.0 + rate) ** (i + 1))
    return total


def irr(
    flows: list[float],
    guess: float = 0.10,
    tol: float = 1e-7,
    max_iter: int = 200,
) -> float:
    """Internal rate of return via Newton's method, bisection fallback.

    Returns 0.0 if the series cannot produce a valid IRR (e.g. all-positive
    or all-negative cash flows).
    """
    if not flows or all(cf >= 0 for cf in flows) or all(cf <= 0 for cf in flows):
        return 0.0

    rate = guess
    for _ in range(max_iter):
        f = npv(rate, flows)
        df = npv_derivative(rate, flows)
        if abs(df) < 1e-12:
            break
        new_rate = rate - f / df
        if new_rate <= -0.999:
            new_rate = (rate - 0.999) / 2.0
        if abs(new_rate - rate) < tol:
            return new_rate
        rate = new_rate

    # Bisection fallback over a wide bracket.
    lo, hi = -0.999, 10.0
    f_lo = npv(lo, flows)
    f_hi = npv(hi, flows)
    if f_lo * f_hi > 0:
        return rate  # best Newton guess
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = npv(mid, flows)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
            f_hi = f_mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


# ─────────────── Returns engine ───────────────


class ReturnsEngineInputExt(BaseModel):
    """Self-contained input — does not require a pre-built CashFlowEngineOutput.

    The engine will derive the NOI series from ``year_one_noi`` and grow it
    at ``revpar_growth`` (close enough for top-line projection — actual
    composition can be passed in via :attr:`noi_by_year`).
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    assumptions: ModelAssumptions
    year_one_noi: Annotated[float, Field(gt=0)]
    annual_debt_service: Annotated[float, Field(ge=0)] = 0.0
    loan_amount: Annotated[float, Field(ge=0)] = 0.0
    loan_balance_at_exit: Annotated[float, Field(ge=0)] | None = None
    equity: Annotated[float, Field(gt=0)]
    noi_by_year: list[Annotated[float, Field(ge=0)]] = Field(default_factory=list)
    terminal_noi_override: Annotated[float, Field(gt=0)] | None = Field(
        default=None,
        description=(
            "Override the engine-projected Y(N+1) NOI used for the exit-cap "
            "calculation. Useful when the underwriter applies a stress "
            "scenario or a normalized terminal NOI."
        ),
    )


class ReturnsEngineOutputExt(ReturnsEngineOutput):
    model_config = ConfigDict(extra="forbid")

    cash_flows: list[float] = Field(default_factory=list)
    cash_flows_unlevered: list[float] = Field(default_factory=list)


def _project_noi_series(
    year_one_noi: float, growth: float, hold_years: int
) -> list[float]:
    return [year_one_noi * ((1.0 + growth) ** (y - 1)) for y in range(1, hold_years + 1)]


class ReturnsEngine(BaseEngine[ReturnsEngineInputExt, ReturnsEngineOutputExt]):
    """Compute levered/unlevered IRR, equity multiple and cash-on-cash."""

    name = "returns"

    def run(self, payload: ReturnsEngineInputExt) -> ReturnsEngineOutputExt:
        assumptions = payload.assumptions
        hold = assumptions.hold_years

        if payload.noi_by_year:
            noi_series = list(payload.noi_by_year[:hold])
            while len(noi_series) < hold:
                noi_series.append(
                    noi_series[-1] * (1.0 + assumptions.revpar_growth)
                )
        else:
            noi_series = _project_noi_series(
                payload.year_one_noi, assumptions.revpar_growth, hold
            )

        # Terminal NOI = NOI in year (hold + 1), used for exit cap calc.
        terminal_noi = (
            payload.terminal_noi_override
            if payload.terminal_noi_override is not None
            else noi_series[-1] * (1.0 + assumptions.revpar_growth)
        )
        gross_sale = terminal_noi / assumptions.exit_cap_rate
        selling_costs = gross_sale * assumptions.selling_costs_pct
        loan_balance_at_exit = (
            payload.loan_balance_at_exit
            if payload.loan_balance_at_exit is not None
            else payload.loan_amount  # IO assumption — full balance still outstanding
        )
        net_proceeds_to_equity = gross_sale - selling_costs - loan_balance_at_exit

        # Levered cash flow stream (Year 0 = -equity).
        cfad_series = [n - payload.annual_debt_service for n in noi_series]
        levered_flows = [-payload.equity] + cfad_series[:-1] + [
            cfad_series[-1] + net_proceeds_to_equity
        ]

        # Unlevered cash flow stream (Year 0 = -purchase price).
        purchase = assumptions.purchase_price
        unlevered_flows = [-purchase] + noi_series[:-1] + [
            noi_series[-1] + gross_sale - selling_costs
        ]

        levered_irr = irr(levered_flows)
        unlevered_irr = irr(unlevered_flows)

        total_distributions = sum(cfad_series) + net_proceeds_to_equity
        equity_multiple = total_distributions / payload.equity if payload.equity else 0.0

        year_one_coc = (
            cfad_series[0] / payload.equity if payload.equity else 0.0
        )
        avg_coc = (
            (sum(cfad_series) / len(cfad_series)) / payload.equity
            if payload.equity and cfad_series
            else 0.0
        )

        return ReturnsEngineOutputExt(
            deal_id=payload.deal_id,
            levered_irr=levered_irr,
            unlevered_irr=unlevered_irr,
            equity_multiple=equity_multiple,
            year_one_coc=year_one_coc,
            avg_coc=avg_coc,
            gross_sale_price=gross_sale,
            selling_costs=selling_costs,
            net_proceeds=net_proceeds_to_equity,
            hold_years=hold,
            cash_flows=levered_flows,
            cash_flows_unlevered=unlevered_flows,
        )


def returns_from_cash_flow(
    payload: ReturnsEngineInput, equity: float, loan_balance_at_exit: float
) -> ReturnsEngineOutputExt:
    """Adapter that takes the canonical :class:`ReturnsEngineInput` schema.

    This bridges between the engine pipeline (which composes
    CashFlowEngineOutput) and the lighter-weight ``ReturnsEngineInputExt``
    used for direct invocation.
    """
    assumptions = payload.assumptions
    hold = assumptions.hold_years
    cfad = [yr.cash_flow_after_debt for yr in payload.cash_flow.years[:hold]]
    noi_series = [yr.noi for yr in payload.cash_flow.years[:hold]]
    debt_service = noi_series[0] - cfad[0] if cfad else 0.0

    gross_sale = payload.terminal_noi / assumptions.exit_cap_rate
    selling_costs = gross_sale * assumptions.selling_costs_pct
    net_proceeds = gross_sale - selling_costs - loan_balance_at_exit

    levered_flows = [-equity] + cfad[:-1] + [cfad[-1] + net_proceeds]
    purchase = assumptions.purchase_price
    unlevered_flows = [-purchase] + noi_series[:-1] + [
        noi_series[-1] + gross_sale - selling_costs
    ]

    total_distributions = sum(cfad) + net_proceeds
    return ReturnsEngineOutputExt(
        deal_id=payload.deal_id,
        levered_irr=irr(levered_flows),
        unlevered_irr=irr(unlevered_flows),
        equity_multiple=total_distributions / equity if equity else 0.0,
        year_one_coc=cfad[0] / equity if equity and cfad else 0.0,
        avg_coc=(sum(cfad) / len(cfad)) / equity if equity and cfad else 0.0,
        gross_sale_price=gross_sale,
        selling_costs=selling_costs,
        net_proceeds=net_proceeds,
        hold_years=hold,
        cash_flows=levered_flows,
        cash_flows_unlevered=unlevered_flows,
    )


__all__ = [
    "ReturnsEngine",
    "ReturnsEngineInputExt",
    "ReturnsEngineOutputExt",
    "irr",
    "npv",
    "returns_from_cash_flow",
]


# Re-export to satisfy unused-import linter.
_ = (CashFlowEngineOutput, CashFlowYear)
