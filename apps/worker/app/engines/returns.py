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
from fondok_schemas.provenance import ValueInput, ValueTrace
from fondok_schemas.underwriting import (
    CashFlowEngineOutput,
    CashFlowYear,
    ReturnsEngineInput,
    ReturnsEngineOutput,
)

from .base import BaseEngine
from .monthly_cashflow import MonthlyCashFlowInput, build_monthly_cashflow


def _exit_value_provenance(
    *,
    terminal_noi: float,
    exit_cap_rate: float,
    gross_sale: float,
    selling_costs_pct: float,
    selling_costs: float,
    loan_balance_at_exit: float,
    net_proceeds: float,
    total_distributions: float,
    equity: float,
    equity_multiple: float,
) -> dict[str, ValueTrace]:
    """Shared exit-value trace map for both returns construction paths.

    Traces the exit-value chain analysts most want to interrogate:
    gross_sale_price → net_proceeds → equity_multiple.
    """
    return {
        "gross_sale_price": ValueTrace(
            value=gross_sale,
            formula="gross_sale_price = terminal_noi ÷ exit_cap_rate",
            inputs=[
                ValueInput(name="terminal_noi", value=terminal_noi),
                ValueInput(name="exit_cap_rate", value=exit_cap_rate),
            ],
            note="Direct-cap terminal value at the end of the hold.",
        ),
        "selling_costs": ValueTrace(
            value=selling_costs,
            formula="selling_costs = gross_sale_price × selling_costs_pct",
            inputs=[
                ValueInput(
                    name="gross_sale_price",
                    value=gross_sale,
                    traces_to="gross_sale_price",
                ),
                ValueInput(name="selling_costs_pct", value=selling_costs_pct),
            ],
        ),
        "net_proceeds": ValueTrace(
            value=net_proceeds,
            formula="net_proceeds = gross_sale_price − selling_costs − loan_balance_at_exit",
            inputs=[
                ValueInput(
                    name="gross_sale_price",
                    value=gross_sale,
                    traces_to="gross_sale_price",
                ),
                ValueInput(
                    name="selling_costs",
                    value=selling_costs,
                    traces_to="selling_costs",
                ),
                ValueInput(name="loan_balance_at_exit", value=loan_balance_at_exit),
            ],
            note="Equity proceeds at sale, after debt payoff.",
        ),
        "equity_multiple": ValueTrace(
            value=equity_multiple,
            formula="equity_multiple = total_distributions ÷ equity",
            inputs=[
                ValueInput(name="total_distributions", value=total_distributions),
                ValueInput(name="equity", value=equity),
            ],
            note=(
                "total_distributions = Σ annual cash-flow-after-debt "
                "+ net_proceeds at exit."
            ),
        ),
    }


def _irr_provenance(
    *,
    levered_irr: float,
    levered_flows: list[float],
    unlevered_irr: float,
    unlevered_flows: list[float],
) -> dict[str, ValueTrace]:
    """Provenance for the two IRRs.

    IRR has no closed form: it's the discount rate that sets the NPV of the
    cash-flow series to zero, solved iteratively (Newton's method, bisection
    fallback — see :func:`irr`). We express that *definition* as the formula
    and list the exact cash-flow stream the solver ran over, so an analyst
    can see it's a genuine calculation and which flows drove the rate.
    """

    def _flow_inputs(flows: list[float]) -> list[ValueInput]:
        return [
            ValueInput(name=f"cash_flow_year_{i}", value=float(cf))
            for i, cf in enumerate(flows)
        ]

    return {
        "levered_irr": ValueTrace(
            value=levered_irr,
            formula="levered_irr = rate r where Σ CFₜ ÷ (1 + r)ᵗ = 0",
            inputs=_flow_inputs(levered_flows),
            note=(
                "Calculated, not read from a document. Solved iteratively "
                "(Newton's method — no closed form) as the rate that zeroes the "
                "NPV of the equity cash flows: Year 0 = −equity invested, "
                "Years 1…N = cash flow after debt service, plus net sale "
                "proceeds at exit."
            ),
        ),
        "unlevered_irr": ValueTrace(
            value=unlevered_irr,
            formula="unlevered_irr = rate r where Σ CFₜ ÷ (1 + r)ᵗ = 0",
            inputs=_flow_inputs(unlevered_flows),
            note=(
                "Calculated asset-level IRR before debt — same iterative solve. "
                "Year 0 = −purchase price, Years 1…N = NOI, plus net sale at exit."
            ),
        ),
    }


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


def xnpv(rate: float, times: list[float], flows: list[float]) -> float:
    """NPV of cash flows at arbitrary (fractional-year) time offsets."""
    return sum(cf / ((1.0 + rate) ** t) for t, cf in zip(times, flows))


def xirr(
    times: list[float],
    flows: list[float],
    tol: float = 1e-7,
    max_iter: int = 200,
) -> float:
    """Date-aware IRR (Excel XIRR semantics) — solves for the rate r where
    Σ CFₜ ÷ (1+r)^tₜ = 0 with ``times`` given in years from t=0. Used so a
    mid-hold event (e.g. a month-30 cash-out refinance) is discounted at its
    actual time rather than folded into a year-end. Integer, evenly-spaced
    ``times`` reduce exactly to the annual-period :func:`irr`. Bisection over a
    wide bracket; falls back to the annual IRR when there is no sign change.
    """
    if not flows or all(cf >= 0 for cf in flows) or all(cf <= 0 for cf in flows):
        return 0.0
    lo, hi = -0.999, 10.0
    f_lo = xnpv(lo, times, flows)
    f_hi = xnpv(hi, times, flows)
    if f_lo * f_hi > 0:
        return irr(flows)
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = xnpv(mid, times, flows)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
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
    # Wave 4 W4.4 — full debt-stack DS series. When set, the engine
    # uses these year-by-year debt service totals (senior + mezz + pref
    # equity aggregate) instead of the scalar ``annual_debt_service``.
    # Empty list preserves the legacy single-DS path byte-identically.
    debt_service_by_year: list[Annotated[float, Field(ge=0)]] = Field(
        default_factory=list
    )
    # FON-67 — mid-hold refinance. When a refi occurs in ``refi_year`` the net
    # cash-out (refi proceeds − senior payoff − costs) is a positive equity
    # distribution in that year; ``debt_service_by_year`` already carries the
    # phased senior→refi debt service and ``loan_balance_at_exit`` the refi
    # balance. Zero / None keeps the single-phase behavior byte-identical.
    refi_cash_out: Annotated[float, Field(ge=0)] = 0.0
    refi_year: Annotated[int, Field(ge=1)] | None = None
    # FON-67 — actual time (years from acquisition) of the refi cash-out, for
    # date-based XIRR. A month-30 refi is 2.5. None places the cash-out at
    # refi_year's year-end, so the levered IRR equals the annual-period IRR and
    # deals without a refi timing are unchanged.
    refi_time_years: Annotated[float, Field(gt=0)] | None = None
    # FON-67 — phased capital deployment. When capital (e.g. the renovation) is
    # spent mid-hold rather than all at close, model it as an outflow deferred
    # from t=0 to ``deferred_capital_year``. This defers equity, lifting the IRR
    # toward a source model that draws capital over the first years. 0 keeps the
    # single-close behavior (all capital at t=0), so existing deals are unchanged.
    deferred_capital: Annotated[float, Field(ge=0)] = 0.0
    # Calendar years after the acquisition year in which the deferred capital
    # deploys (0 = acquisition year, 1 = the next calendar year). Combined with
    # acquisition_month_offset this lands the outflow in the right months even
    # for a mid-year close (Kimpton renovation in 2026 → 1).
    deferred_capital_year: Annotated[int, Field(ge=0)] | None = None
    # FON-67 — months elapsed in the acquisition calendar year at close (0-11),
    # for the monthly model's stub-year handling (a 9/30 close = 9).
    acquisition_month_offset: Annotated[int, Field(ge=0, le=11)] = 0
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
    # FON-59 — surface the reversion assumptions so the Overview's Reversion
    # tile can show Exit Cap Rate + Terminal NOI instead of "—".
    exit_cap_rate: float | None = None
    terminal_noi: float | None = None


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
        # FON-67 — transfer/recordation tax on the gross sale, deducted from the
        # reversion alongside brokerage selling costs (0 unless set).
        transfer_tax = gross_sale * assumptions.transfer_tax_pct
        loan_balance_at_exit = (
            payload.loan_balance_at_exit
            if payload.loan_balance_at_exit is not None
            else payload.loan_amount  # IO assumption — full balance still outstanding
        )
        net_proceeds_to_equity = gross_sale - selling_costs - transfer_tax - loan_balance_at_exit

        # Levered cash flow stream (Year 0 = -equity). When the
        # caller supplies a full debt-stack DS series (Wave 4 W4.4)
        # we honor it year-by-year; otherwise we fall back to the
        # scalar ``annual_debt_service`` (the legacy single-loan
        # path — preserved byte-identically).
        ds_series: list[float]
        if payload.debt_service_by_year:
            ds_series = list(payload.debt_service_by_year[:hold])
            while len(ds_series) < hold:
                ds_series.append(payload.annual_debt_service)
        else:
            ds_series = [payload.annual_debt_service] * hold
        # Operating cash flow after debt service, per year (the refi cash-out is
        # held out here so it can be timed correctly for the date-based IRR).
        op_cfad = [n - ds for n, ds in zip(noi_series, ds_series)]
        refi_active = (
            payload.refi_cash_out > 0
            and payload.refi_year is not None
            and 1 <= payload.refi_year <= len(op_cfad)
        )
        # FON-67 — a mid-hold refi returns capital to equity. For the DISPLAY
        # stream (Cash Flow tab + equity multiple) fold the cash-out into its
        # year; it's levered-only, so the unlevered case is untouched.
        cfad_series = list(op_cfad)
        if refi_active:
            cfad_series[payload.refi_year - 1] += payload.refi_cash_out
        levered_flows = [-payload.equity] + cfad_series[:-1] + [
            cfad_series[-1] + net_proceeds_to_equity
        ]

        # Unlevered cash flow stream. Year 0 = TOTAL invested capital
        # (equity + loan = purchase + renovation + closing + working
        # capital), NOT just the purchase price — an all-cash buyer funds
        # the whole project. Using purchase_price understated the basis and
        # inflated unlevered IRR, which made leverage look negative even
        # when the asset yield exceeded the debt cost.
        total_capital = payload.equity + payload.loan_amount
        unlevered_flows = [-total_capital] + noi_series[:-1] + [
            noi_series[-1] + gross_sale - selling_costs - transfer_tax
        ]

        # FON-67 — date-based levered IRR. Equity at t=0, each operating CF at
        # its year-end, the exit at t=hold, and the refi cash-out at its ACTUAL
        # time (refi_time_years; e.g. 2.5 for a month-30 refi). With no refi
        # timing set the cash-out sits at its year-end, so xirr reduces exactly
        # to the annual-period IRR — existing deals are byte-for-byte unchanged.
        lev_times: list[float] = [0.0]
        lev_amounts: list[float] = [-payload.equity]
        for y in range(1, hold + 1):
            lev_times.append(float(y))
            lev_amounts.append(
                op_cfad[y - 1] + (net_proceeds_to_equity if y == hold else 0.0)
            )
        if refi_active:
            assert payload.refi_year is not None
            lev_times.append(
                payload.refi_time_years
                if payload.refi_time_years is not None
                else float(payload.refi_year)
            )
            lev_amounts.append(payload.refi_cash_out)

        # FON-67 — phased capital deployment: shift a chunk of capital (e.g. the
        # renovation) out of the t=0 close and into its deployment year, in both
        # the levered and unlevered streams. Deferring the outflow lifts the IRR
        # toward a source model that draws capital over the first years. Index 0
        # is t=0; index d is year d in every stream here.
        dc = payload.deferred_capital
        dy = payload.deferred_capital_year
        if dc > 0 and dy is not None and 1 <= dy <= hold:
            unlevered_flows[0] += dc
            unlevered_flows[dy] -= dc
            levered_flows[0] += dc
            levered_flows[dy] -= dc
            lev_amounts[0] += dc
            lev_amounts[dy] -= dc

        levered_irr = xirr(lev_times, lev_amounts)
        unlevered_irr = irr(unlevered_flows)

        # FON-67 — compute the returns on a MONTHLY calendar (the accurate,
        # institutional method): operating cash accrues through the year, the
        # refi and sale land on their real months, and IRR is XIRR over the full
        # monthly series. This closes the several IRR points an annual grid loses
        # to intra-year timing + partial stub years. The annual figures above are
        # the fallback. See app/engines/monthly_cashflow.py.
        if hold > 0 and noi_series:
            refi_month: int | None = None
            if refi_active and payload.refi_year is not None:
                _rt = (
                    payload.refi_time_years
                    if payload.refi_time_years is not None
                    else float(payload.refi_year)
                )
                refi_month = max(1, min(hold * 12, round(_rt * 12)))
            dy = payload.deferred_capital_year
            monthly = build_monthly_cashflow(
                MonthlyCashFlowInput(
                    hold_years=hold,
                    noi_by_year=list(noi_series),
                    acquisition_month_offset=payload.acquisition_month_offset,
                    monthly_debt_service=[
                        ds_series[y] / 12.0 for y in range(hold) for _ in range(12)
                    ],
                    equity_at_close=max(0.0, payload.equity - payload.deferred_capital),
                    total_capital_at_close=max(
                        0.0, total_capital - payload.deferred_capital
                    ),
                    deferred_capital=payload.deferred_capital,
                    # Offset-aware deployment window: calendar year ``dy`` after
                    # acquisition maps to its actual months given the close date.
                    deferred_capital_start_month=(
                        max(1, dy * 12 - payload.acquisition_month_offset + 1)
                        if dy is not None
                        else 1
                    ),
                    deferred_capital_end_month=(
                        min(hold * 12, (dy + 1) * 12 - payload.acquisition_month_offset)
                        if dy is not None
                        else 12
                    ),
                    refi_month=refi_month,
                    refi_net_cash_out=payload.refi_cash_out if refi_active else 0.0,
                    exit_net_proceeds_levered=net_proceeds_to_equity,
                    exit_net_proceeds_unlevered=gross_sale - selling_costs - transfer_tax,
                )
            )
            levered_irr = monthly.levered_irr
            unlevered_irr = monthly.unlevered_irr

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
            exit_cap_rate=assumptions.exit_cap_rate,
            terminal_noi=terminal_noi,
            cash_flows=levered_flows,
            cash_flows_unlevered=unlevered_flows,
            provenance={
                **_exit_value_provenance(
                    terminal_noi=terminal_noi,
                    exit_cap_rate=assumptions.exit_cap_rate,
                    gross_sale=gross_sale,
                    selling_costs_pct=assumptions.selling_costs_pct,
                    selling_costs=selling_costs,
                    loan_balance_at_exit=loan_balance_at_exit,
                    net_proceeds=net_proceeds_to_equity,
                    total_distributions=total_distributions,
                    equity=payload.equity,
                    equity_multiple=equity_multiple,
                ),
                **_irr_provenance(
                    levered_irr=levered_irr,
                    levered_flows=levered_flows,
                    unlevered_irr=unlevered_irr,
                    unlevered_flows=unlevered_flows,
                ),
            },
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
    transfer_tax = gross_sale * assumptions.transfer_tax_pct
    net_proceeds = gross_sale - selling_costs - transfer_tax - loan_balance_at_exit

    levered_flows = [-equity] + cfad[:-1] + [cfad[-1] + net_proceeds]
    # Unlevered basis = total invested capital an all-cash buyer funds
    # (purchase + closing + renovation + soft + contingency + working
    # capital), NOT just purchase_price — see the ReturnsEngine.run note.
    # This adapter lacks the loan amount, so derive from the assumptions'
    # capital components (loan costs excluded — an all-cash buyer has none).
    a = assumptions
    total_capital = (
        a.purchase_price * (1.0 + a.closing_costs_pct)
        + a.renovation_budget + a.soft_costs + a.contingency + a.working_capital
    )
    unlevered_flows = [-total_capital] + noi_series[:-1] + [
        noi_series[-1] + gross_sale - selling_costs - transfer_tax
    ]

    total_distributions = sum(cfad) + net_proceeds
    equity_multiple = total_distributions / equity if equity else 0.0
    lev_irr = irr(levered_flows)
    unl_irr = irr(unlevered_flows)
    return ReturnsEngineOutputExt(
        deal_id=payload.deal_id,
        levered_irr=lev_irr,
        unlevered_irr=unl_irr,
        equity_multiple=equity_multiple,
        year_one_coc=cfad[0] / equity if equity and cfad else 0.0,
        avg_coc=(sum(cfad) / len(cfad)) / equity if equity and cfad else 0.0,
        gross_sale_price=gross_sale,
        selling_costs=selling_costs,
        net_proceeds=net_proceeds,
        hold_years=hold,
        exit_cap_rate=assumptions.exit_cap_rate,
        terminal_noi=payload.terminal_noi,
        cash_flows=levered_flows,
        cash_flows_unlevered=unlevered_flows,
        provenance={
            **_exit_value_provenance(
                terminal_noi=payload.terminal_noi,
                exit_cap_rate=assumptions.exit_cap_rate,
                gross_sale=gross_sale,
                selling_costs_pct=assumptions.selling_costs_pct,
                selling_costs=selling_costs,
                loan_balance_at_exit=loan_balance_at_exit,
                net_proceeds=net_proceeds,
                total_distributions=total_distributions,
                equity=equity,
                equity_multiple=equity_multiple,
            ),
            **_irr_provenance(
                levered_irr=lev_irr,
                levered_flows=levered_flows,
                unlevered_irr=unl_irr,
                unlevered_flows=unlevered_flows,
            ),
        },
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
