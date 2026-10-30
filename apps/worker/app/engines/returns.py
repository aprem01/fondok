"""Returns engine — unlevered/levered IRR, equity multiple, cash-on-cash.

Computes the standard private-equity return triplet on an annual cash flow
series. IRR is solved with Newton's method (no SciPy dependency); a
bisection fallback handles cash-flow series where Newton fails to converge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from fondok_schemas.financial import ModelAssumptions
from fondok_schemas.provenance import ValueInput, ValueTrace, apply_states
from fondok_schemas.reasons import ReasonCode
from fondok_schemas.underwriting import (
    CashFlowEngineOutput,
    CashFlowYear,
    ExitYearReturn,
    ReturnsEngineInput,
    ReturnsEngineOutput,
)
from pydantic import BaseModel, ConfigDict, Field

from .base import BaseEngine
from .monthly_cashflow import (
    MonthlyCashFlowInput,
    MonthlyCashFlowResult,
    build_monthly_cashflow,
)
from .stabilization import resolve_stabilized_year_index

# FON-44 (R-059) + FON-44 addition (2026-10-30): the analyst-selectable bases.
# ``override`` is not selectable — it is what ``terminal_noi_override`` reports.
ExitNoiBasis = Literal["forward_12m", "stabilized", "sale_year"]


class _TerminalNoiCheck(BaseModel):
    """FON-63 — the one REAL stop on NOI. Operating years may be negative and
    still flow through, but the reversion is ``terminal_noi ÷ exit_cap_rate``:
    a non-positive exit NOI has no sale value. Validated as its own model so
    the runner's error names ``terminal_noi`` and attaches its hint."""

    model_config = ConfigDict(extra="forbid")

    terminal_noi: Annotated[float, Field(gt=0)]


def _resolve_terminal_noi(
    *,
    noi_series: list[float],
    hold: int,
    growth: float,
    basis: str,
    stabilized_year: int | None,
    override: float | None,
    next_year_noi: float | None = None,
) -> tuple[float, str, str, int | None, str]:
    """FON-44 (R-059) — the NOI the reversion caps, and how it was chosen.

    Returns ``(terminal_noi, basis, period_label, source_index, formula)``.
    ``source_index`` is the 0-based NOI year the figure was read from (None
    for the override). Precedence: ``terminal_noi_override`` → the selected
    basis. ``forward_12m`` (default) is the exit year's NOI grown one more year
    — byte-identical to the pre-FON-44 reversion. ``stabilized`` is the
    stabilized year's NOI grown to the exit year at the same growth rate; when
    stabilization lands after the exit, the exit year's NOI is used and the
    label says so. ``sale_year`` (FON-44 addition) is the exit year's own Cash
    NOI, not grown.

    ``next_year_noi`` (FON-118) is the PROJECTED NOI of the year after the
    sale, passed only for an interim exit-schedule row — the projection has
    that year, so the forward 12 months are read, not grown. The headline
    never passes it (there is no projected year after the hold), so its
    arithmetic is untouched.
    """
    if override is not None:
        return (
            override,
            "override",
            "Reconciliation override",
            None,
            "terminal_noi = terminal_noi_override",
        )
    if basis == "stabilized":
        s = stabilized_year
        if s is None:
            idx = resolve_stabilized_year_index(
                occupancy_by_year=None,
                stabilized_occupancy=None,
                noi_by_year=noi_series,
            )
            s = idx + 1 if idx is not None else None
        if s is not None and s >= 1:
            if s > hold:
                return (
                    noi_series[-1],
                    "stabilized",
                    (
                        f"Exit-year NOI (Year {hold}; stabilization in Year {s} "
                        "falls after the exit)"
                    ),
                    hold - 1,
                    "terminal_noi = exit_year_noi",
                )
            label = (
                f"Stabilized NOI (Year {s}, grown to Year {hold})"
                if s < hold
                else f"Stabilized NOI (Year {s}, the exit year)"
            )
            return (
                noi_series[s - 1] * ((1.0 + growth) ** (hold - s)),
                "stabilized",
                label,
                s - 1,
                "terminal_noi = stabilized_year_noi × (1 + revpar_growth)"
                " ^ (exit_year − stabilized_year)",
            )
    if basis == "sale_year":
        return (
            noi_series[-1],
            "sale_year",
            f"Sale-year NOI (Year {hold})",
            hold - 1,
            "terminal_noi = exit_year_noi",
        )
    if next_year_noi is not None:
        return (
            next_year_noi,
            "forward_12m",
            f"Forward 12-month NOI (Year {hold + 1}, projected)",
            hold,
            "terminal_noi = projected_noi[exit_year + 1]",
        )
    return (
        noi_series[-1] * (1.0 + growth),
        "forward_12m",
        f"Forward 12-month NOI (Year {hold + 1})",
        hold - 1,
        "terminal_noi = exit_year_noi × (1 + revpar_growth)",
    )


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
    terminal_noi_ref: str | None = None,
    basis: str = "forward_12m",
    basis_label: str | None = None,
) -> dict[str, ValueTrace]:
    """Shared exit-value trace map for both returns construction paths.

    Traces the exit-value chain analysts most want to interrogate:
    gross_sale_price → net_proceeds → equity_multiple.

    ``terminal_noi_ref`` is the traced value the reversion NOI was grown from
    (``"expense.years[N].noi"``), when the engine can honestly name one — i.e.
    the NOI path really is the expense engine's and the analyst has not pinned
    a ``terminal_noi_override``. ``None`` where it cannot, and the note says so.
    """
    return {
        "gross_sale_price": ValueTrace(
            value=gross_sale,
            formula="gross_sale_price = terminal_noi ÷ exit_cap_rate",
            inputs=[
                ValueInput(
                    name="terminal_noi",
                    value=terminal_noi,
                    traces_to=terminal_noi_ref,
                ),
                ValueInput(
                    name="exit_cap_rate",
                    value=exit_cap_rate,
                    assumption_key="exit_cap_rate",
                ),
            ],
            note=(
                "Direct-cap terminal value at the end of the hold."
                + (
                    (
                        " terminal_noi is the final projected year's NOI grown one"
                        " more year at revpar_growth."
                        if basis == "forward_12m"
                        else f" terminal_noi basis: {basis_label}."
                    )
                    if terminal_noi_ref
                    else " terminal_noi is pinned or projected outside the"
                    " expense engine's schedule, so no upstream value is asserted."
                    + (f" Basis: {basis_label}." if basis_label else "")
                )
            ),
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
                ValueInput(
                    name="selling_costs_pct",
                    value=selling_costs_pct,
                    assumption_key="selling_costs_pct",
                ),
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
                # total_distributions is a sum over every year's cash flow plus
                # the exit — several upstream values, not one, so it carries no
                # single pointer (the lineage bridge still fans it out).
                ValueInput(name="total_distributions", value=total_distributions),
                ValueInput(
                    name="equity", value=equity, traces_to="capital.equity_amount"
                ),
            ],
            note=(
                "total_distributions = Σ annual cash-flow-after-debt "
                "+ net_proceeds at exit."
            ),
        ),
    }


def _terminal_noi_trace(
    *,
    terminal_noi: float,
    basis: str,
    label: str,
    formula: str,
    source_noi: float | None,
    source_ref: str | None,
    growth: float,
) -> ValueTrace:
    """FON-44 (R-059) — the reversion NOI, naming the basis it was read on."""
    inputs: list[ValueInput] = []
    if basis == "override":
        inputs.append(
            ValueInput(
                name="terminal_noi_override",
                value=terminal_noi,
                assumption_key="terminal_noi_override",
            )
        )
    else:
        if source_noi is not None:
            inputs.append(
                ValueInput(
                    name="source_year_noi", value=source_noi, traces_to=source_ref
                )
            )
        if "revpar_growth" in formula:
            inputs.append(
                ValueInput(
                    name="revpar_growth",
                    value=growth,
                    assumption_key="revpar_growth",
                )
            )
    return ValueTrace(
        value=terminal_noi,
        formula=formula,
        inputs=inputs,
        note=f"Exit NOI basis: {label}.",
    )


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


def _coc_provenance(
    *,
    year_one_coc: float,
    avg_coc: float,
    year_one_cfad: float,
    total_cfad: float,
    n_years: int,
    equity: float,
) -> dict[str, ValueTrace]:
    """Provenance for the cash-on-cash yields the Returns tab renders.

    Both are the levered cash-flow-after-debt over invested equity — Year 1 for
    ``year_one_coc``, the hold average for ``avg_coc`` — so an analyst can see
    the badge is a genuine calculation, not a placeholder (fills the FON-65 gap
    where the CoC KPIs shipped without a trace).
    """
    return {
        "year_one_coc": ValueTrace(
            value=year_one_coc,
            formula="year_one_coc = year_1_cash_flow_after_debt ÷ equity",
            inputs=[
                # NOI less debt service — two upstream values, so no single
                # pointer; the lineage bridge fans it out to both.
                ValueInput(name="year_1_cash_flow_after_debt", value=year_one_cfad),
                ValueInput(
                    name="equity", value=equity, traces_to="capital.equity_amount"
                ),
            ],
            note="Year-1 levered cash yield on invested equity.",
        ),
        "avg_coc": ValueTrace(
            value=avg_coc,
            formula="avg_coc = (Σ cash_flow_after_debt ÷ years) ÷ equity",
            inputs=[
                ValueInput(name="total_cash_flow_after_debt", value=total_cfad),
                ValueInput(name="years", value=float(n_years)),
                ValueInput(
                    name="equity", value=equity, traces_to="capital.equity_amount"
                ),
            ],
            note="Average annual levered cash yield across the hold.",
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


# ─────────────── Returns engine inputs / outputs ───────────────


class ReturnsEngineInputExt(BaseModel):
    """Self-contained input — does not require a pre-built CashFlowEngineOutput.

    The engine will derive the NOI series from ``year_one_noi`` and grow it
    at ``revpar_growth`` (close enough for top-line projection — actual
    composition can be passed in via :attr:`noi_by_year`).
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    assumptions: ModelAssumptions
    # FON-63 — NOI may be negative (a ramp / PIP year): the cash flows carry it
    # as-is. Only the exit-year NOI must be positive (see _TerminalNoiCheck).
    year_one_noi: float
    annual_debt_service: Annotated[float, Field(ge=0)] = 0.0
    loan_amount: Annotated[float, Field(ge=0)] = 0.0
    loan_balance_at_exit: Annotated[float, Field(ge=0)] | None = None
    equity: Annotated[float, Field(gt=0)]
    noi_by_year: list[float] = Field(default_factory=list)
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
    # FON-67 — off-cycle disposition month (1-based). None → clean hold_years×12
    # exit; set it for a hold that doesn't land on a whole-year boundary (e.g.
    # a 52-month / 4.333-year hold). The monthly model exits here.
    exit_month_override: Annotated[int, Field(ge=1)] | None = None
    # FON-67 — explicit per-month equity draw schedule (index 0 = close). When
    # set it drives the levered equity outflows exactly, for reconciling to a
    # source model's actual renovation draw timing. Empty → equity at close +
    # the deferred-capital spread.
    equity_draw_schedule: list[float] = Field(default_factory=list)
    terminal_noi_override: Annotated[float, Field(gt=0)] | None = Field(
        default=None,
        description=(
            "Override the engine-projected Y(N+1) NOI used for the exit-cap "
            "calculation. Useful when the underwriter applies a stress "
            "scenario or a normalized terminal NOI."
        ),
    )
    # FON-44 (R-059) — which NOI the reversion caps. ``forward_12m`` (default,
    # byte-identical) = the exit year's NOI grown one more year; ``stabilized``
    # = the stabilized year's NOI grown to the exit year. A set
    # ``terminal_noi_override`` beats both.
    exit_noi_basis: ExitNoiBasis = "forward_12m"
    # 1-based stabilized year for the ``stabilized`` basis (the runner passes
    # the analyst's ``stabilization_year``, else the resolved signal). None →
    # the engine resolves it from the NOI plateau.
    stabilized_year: Annotated[int, Field(ge=1)] | None = None
    # PROVENANCE ONLY — never read by any calculation. True (the default) means
    # ``noi_by_year[i]`` is the expense engine's ``years[i].noi``, so the exit
    # trace can ASSERT where the reversion NOI came from instead of leaving the
    # lineage service to infer it. The runner sets it False when it substitutes
    # the analyst's ``noi_override_by_year`` pin (FON-67).
    noi_from_expense_engine: bool = True
    # FON-118 — returns by exit year. The inputs below feed ONLY the interim
    # rows of ``exit_schedule``; the selected exit (the headline) reads the
    # scalar ``loan_balance_at_exit`` exactly as before, so every existing
    # output is byte-identical whatever is passed here.
    #
    # Senior ending balance per month from close (index 0 = month 1), from the
    # debt engine's monthly_schedule. Empty → the scalar ``loan_balance_at_exit``
    # is the payoff at every exit (legacy single-balance behaviour).
    loan_balance_by_month: list[Annotated[float, Field(ge=0)]] = Field(
        default_factory=list
    )
    # The post-refinance balance (interest-only → constant) once the refi month
    # has passed. None → the scalar ``loan_balance_at_exit`` stands in.
    refi_balance: Annotated[float, Field(ge=0)] | None = None
    # Compute the per-year table. Grid callers (sensitivity, pricing, max-price,
    # price solver, asking-vs-bid) set False: they read one metric per run.
    exit_schedule: bool = True
    # Development-like deal (the runner reads ``deals.deal_type``) — used ONLY to
    # label rows inside the analyst-stated construction window; never a number.
    development_like: bool = False
    # Analyst-stated construction window's last month, months from close
    # (timeline override ``renovation_start_offset_months +
    # renovation_duration_months``); None = not stated, so no row is dashed
    # for construction.
    construction_end_month: Annotated[int, Field(ge=0)] | None = None


class ReturnsEngineOutputExt(ReturnsEngineOutput):
    model_config = ConfigDict(extra="forbid")

    cash_flows: list[float] = Field(default_factory=list)
    cash_flows_unlevered: list[float] = Field(default_factory=list)
    # The exact operating NOI series (per year 1..hold) this engine used to
    # build ``cash_flows_unlevered`` — i.e. the override-aware ``noi_series``
    # (``noi_override_by_year`` when set, else the projected/expense series).
    # The cash_flow view composes its NOI row from THIS so it always ties out
    # to the returns engine, even on deals whose expense NOI differs from the
    # override the returns engine ran on. Empty → cash_flow falls back to the
    # expense NOI (byte-identical to the pre-FON behavior for non-override deals).
    noi_by_year: list[float] = Field(default_factory=list)
    # FON-59 — surface the reversion assumptions so the Overview's Reversion
    # tile can show Exit Cap Rate + Terminal NOI instead of "—".
    exit_cap_rate: float | None = None
    terminal_noi: float | None = None
    # FON-67 — the deepest cumulative equity outflow over the hold (close +
    # deferred draws net of interim distributions), from the monthly model.
    # None when the monthly path didn't run. Distinct from initial equity.
    peak_equity: float | None = None
    # FON-66/67 — the monthly levered cash-flow series (month 0 = close;
    # negative = equity draw, positive = distribution). Feeds the monthly
    # partnership waterfall. Empty when the monthly path didn't run.
    levered_monthly: list[float] = Field(default_factory=list)


def _project_noi_series(
    year_one_noi: float, growth: float, hold_years: int
) -> list[float]:
    return [year_one_noi * ((1.0 + growth) ** (y - 1)) for y in range(1, hold_years + 1)]


# ─────────────── Exit schedule (FON-118) ───────────────

#: ``monthly_cashflow.xirr`` answers a one-sign stream with these rather than
#: 0 so the price solver's return-vs-price surface stays monotone. They are
#: sentinels, not rates — a table never prints them as an IRR.
_IRR_SENTINEL_HI = 10.0
_IRR_SENTINEL_LO = -0.9999

_EXIT_ROW_TRACED_FIELDS: tuple[str, ...] = (
    "levered_irr",
    "unlevered_irr",
    "equity_multiple",
    "terminal_noi",
    "gross_sale_price",
    "net_proceeds",
)


def _irr_is_sentinel(value: float) -> bool:
    return value >= _IRR_SENTINEL_HI or value <= _IRR_SENTINEL_LO


@dataclass
class _ExitContext:
    """Everything that is FIXED across every sale date of one run (§2.3):
    the projection, the debt service, the draws and the refinance. A row
    moves only the sale date and the payoff."""

    hold: int
    full_term: int
    noi_series: list[float]
    op_cfad: list[float]
    refi_active: bool
    refi_month: int | None
    monthly_ds: list[float]
    total_capital: float
    # The stabilized year every interim row reads on the ``stabilized`` basis,
    # resolved ONCE on the full projection (a row's truncated series must not
    # re-resolve it).
    row_stabilized_year: int | None


@dataclass
class _ExitCase:
    """The deal sold at ``exit_month`` — one evaluation of :meth:`_exit_case`."""

    exit_month: int
    exit_year: int | None
    selected: bool
    status: Literal["computed", "not_available"] = "computed"
    reason: ReasonCode | None = None
    reason_detail: str | None = None
    basis: str | None = None
    label: str = ""
    formula: str | None = None
    source_idx: int | None = None
    terminal_noi: float | None = None
    gross_sale: float | None = None
    selling_costs: float | None = None
    transfer_tax: float | None = None
    payoff: float | None = None
    payoff_source: str | None = None
    refi_included: bool = False
    net_proceeds: float | None = None
    # Row-facing IRRs: None when the monthly solver returned a sentinel.
    levered_irr: float | None = None
    unlevered_irr: float | None = None
    equity_multiple: float | None = None
    total_distributions: float | None = None
    # The monthly model's raw answers (sentinels included) — the headline
    # keeps these exactly as before.
    raw_levered_irr: float | None = None
    raw_unlevered_irr: float | None = None
    monthly: MonthlyCashFlowResult | None = None
    cfad_series: list[float] | None = None


def _capital_deploying_through(
    payload: ReturnsEngineInputExt, ctx: _ExitContext
) -> int | None:
    """The last month (from close) in which equity is still going IN, or None.

    Two sources: an explicit per-month draw schedule (index 0 = close; a draw
    at a later index is capital still deploying) and the deferred-capital
    window the monthly model spreads ``deferred_capital`` across (the same
    offset-aware months :func:`build_monthly_cashflow` receives).
    """
    last: int | None = None
    draws = payload.equity_draw_schedule
    for i in range(len(draws) - 1, 0, -1):
        if draws[i] > 0:
            last = i
            break
    dy = payload.deferred_capital_year
    if payload.deferred_capital > 0 and dy is not None:
        end = min(ctx.full_term, (dy + 1) * 12 - payload.acquisition_month_offset)
        last = end if last is None else max(last, end)
    return last


def _payoff_at(
    payload: ReturnsEngineInputExt, exit_month: int, refi_included: bool
) -> tuple[float, str]:
    """Loan payoff for an INTERIM sale at ``exit_month`` and where it was read.

    After the refinance the post-refi balance is owed (interest-only, so
    constant); before it the senior's monthly ending balance at that month;
    past loan maturity the last balance stands (today's ``schedule[-1]``
    convention). With no monthly schedule the scalar ``loan_balance_at_exit``
    is the payoff at every exit — the legacy single-balance behaviour.
    """
    scalar = (
        payload.loan_balance_at_exit
        if payload.loan_balance_at_exit is not None
        else payload.loan_amount
    )
    if refi_included:
        if payload.refi_balance is not None:
            return payload.refi_balance, "refi_balance"
        return scalar, "loan_balance_at_exit"
    if payload.loan_balance_by_month:
        idx = min(exit_month, len(payload.loan_balance_by_month)) - 1
        return payload.loan_balance_by_month[idx], f"loan_balance_by_month[{idx}]"
    return scalar, "loan_balance_at_exit"


def _exit_row(case: _ExitCase, *, is_peak: bool) -> ExitYearReturn:
    return ExitYearReturn(
        exit_year=case.exit_year,
        exit_month=case.exit_month,
        is_selected=case.selected,
        is_peak_levered=is_peak,
        status=case.status,
        reason=case.reason,
        reason_detail=case.reason_detail,
        exit_noi_basis=case.basis,  # type: ignore[arg-type]
        exit_noi_period_label=case.label,
        terminal_noi=case.terminal_noi,
        gross_sale_price=case.gross_sale,
        selling_costs=case.selling_costs,
        transfer_tax=case.transfer_tax,
        loan_payoff=case.payoff,
        refi_included=case.refi_included,
        net_proceeds=case.net_proceeds,
        levered_irr=case.levered_irr,
        unlevered_irr=case.unlevered_irr,
        equity_multiple=case.equity_multiple,
    )


def _exit_row_provenance(
    idx: int,
    case: _ExitCase,
    *,
    payload: ReturnsEngineInputExt,
    ctx: _ExitContext,
) -> dict[str, ValueTrace]:
    """Traces for one schedule row under ``exit_schedule[i].<field>`` keys.

    A field the row has no value for carries ``reason`` and no formula (the
    web's ``Traced`` renders nothing for it); a field the row did compute
    carries the same shape the headline's trace does, compactly.
    """
    key = f"exit_schedule[{idx}]"
    a = payload.assumptions
    m = case.exit_month
    out: dict[str, ValueTrace] = {}

    def _refused(field: str) -> ValueTrace:
        # No value — the reason is the trace (``Traced`` renders nothing).
        return ValueTrace(value=None, reason=case.reason, note=case.reason_detail)

    # terminal_noi
    if case.terminal_noi is None:
        out[f"{key}.terminal_noi"] = _refused("terminal_noi")
    else:
        inputs: list[ValueInput] = []
        if case.source_idx is not None and case.source_idx < len(ctx.noi_series):
            inputs.append(
                ValueInput(
                    name="source_year_noi",
                    value=ctx.noi_series[case.source_idx],
                )
            )
        if case.formula and "revpar_growth" in case.formula:
            inputs.append(
                ValueInput(
                    name="revpar_growth",
                    value=a.revpar_growth,
                    assumption_key="revpar_growth",
                )
            )
        if case.basis == "override":
            inputs.append(
                ValueInput(
                    name="terminal_noi_override",
                    value=case.terminal_noi,
                    assumption_key="terminal_noi_override",
                )
            )
        note = f"Exit NOI basis: {case.label}."
        if payload.terminal_noi_override is not None and not case.selected:
            note += (
                " The deal's terminal_noi_override applies to the selected exit"
                " only; this row uses the deal's basis."
            )
        out[f"{key}.terminal_noi"] = ValueTrace(
            value=case.terminal_noi, formula=case.formula, inputs=inputs, note=note
        )

    # gross_sale_price
    if case.gross_sale is None:
        out[f"{key}.gross_sale_price"] = _refused("gross_sale_price")
    else:
        out[f"{key}.gross_sale_price"] = ValueTrace(
            value=case.gross_sale,
            formula="gross_sale_price = terminal_noi ÷ exit_cap_rate",
            inputs=[
                ValueInput(
                    name="terminal_noi",
                    value=case.terminal_noi,
                    traces_to=f"{key}.terminal_noi",
                ),
                ValueInput(
                    name="exit_cap_rate",
                    value=a.exit_cap_rate,
                    assumption_key="exit_cap_rate",
                ),
            ],
            note=f"Direct-cap value for a sale at month {m}.",
        )

    # net_proceeds
    if case.net_proceeds is None:
        out[f"{key}.net_proceeds"] = _refused("net_proceeds")
    else:
        out[f"{key}.net_proceeds"] = ValueTrace(
            value=case.net_proceeds,
            formula=(
                "net_proceeds = gross_sale_price − selling_costs − transfer_tax"
                " − loan_payoff"
            ),
            inputs=[
                ValueInput(
                    name="gross_sale_price",
                    value=case.gross_sale,
                    traces_to=f"{key}.gross_sale_price",
                ),
                ValueInput(name="selling_costs", value=case.selling_costs),
                ValueInput(name="transfer_tax", value=case.transfer_tax),
                ValueInput(name="loan_payoff", value=case.payoff),
            ],
            note=(
                f"Loan payoff read from {case.payoff_source}; the refinance is "
                + ("included" if case.refi_included else "not yet drawn")
                + f" at month {m}."
            ),
        )

    # IRRs
    for field, raw, what in (
        ("levered_irr", case.levered_irr, "equity"),
        ("unlevered_irr", case.unlevered_irr, "asset"),
    ):
        if raw is None:
            out[f"{key}.{field}"] = _refused(field)
            continue
        out[f"{key}.{field}"] = ValueTrace(
            value=raw,
            formula=f"{field} = rate r where Σ CFₜ ÷ (1 + r)ᵗ = 0",
            inputs=[
                ValueInput(name="exit_month", value=float(m)),
                ValueInput(
                    name="net_proceeds",
                    value=case.net_proceeds,
                    traces_to=f"{key}.net_proceeds",
                ),
            ],
            note=(
                f"Monthly XIRR of the {what} cash flows of the SAME projection "
                f"sold at month {m}: operating cash, draws, debt service and "
                "refinance timing are held fixed; only the sale date and the "
                "payoff move."
            ),
        )

    # equity_multiple
    if case.equity_multiple is None:
        out[f"{key}.equity_multiple"] = _refused("equity_multiple")
    else:
        convention = (
            "monthly model (distributions through the exit month ÷ equity drawn)"
            if case.selected and payload.exit_month_override is not None
            else (
                "Σ annual cash-flow-after-debt through the exit year + "
                "net_proceeds, ÷ equity"
            )
        )
        out[f"{key}.equity_multiple"] = ValueTrace(
            value=case.equity_multiple,
            formula="equity_multiple = total_distributions ÷ equity",
            inputs=[
                ValueInput(
                    name="total_distributions",
                    value=(
                        case.total_distributions
                        if case.total_distributions is not None
                        else case.equity_multiple * payload.equity
                    ),
                ),
                ValueInput(
                    name="equity",
                    value=payload.equity,
                    traces_to="capital.equity_amount",
                ),
            ],
            note=f"Convention: {convention}.",
        )
    return out


# ─────────────── Returns engine ───────────────


class ReturnsEngine(BaseEngine[ReturnsEngineInputExt, ReturnsEngineOutputExt]):
    """Compute levered/unlevered IRR, equity multiple and cash-on-cash.

    FON-118 — ONE mechanism: :meth:`_exit_case` prices "the deal sold at
    month m" on the canonical projection. The headline is that function at
    the deal's own exit (``exit_month_override`` or ``hold × 12``); every
    projection year-end is the same function at its month, and together they
    are ``exit_schedule``. The headline's arithmetic is the pre-FON-118 code
    statement for statement, so the Kimpton golden is byte-identical.
    """

    name = "returns"

    def _exit_case(
        self,
        payload: ReturnsEngineInputExt,
        ctx: _ExitContext,
        *,
        exit_month: int,
        exit_year: int | None,
        selected: bool,
    ) -> _ExitCase:
        """Price the deal sold at ``exit_month`` (§2.3).

        ``selected`` is the deal's own exit — the headline. Its branches are
        the legacy expressions verbatim: terminal NOI on the full series with
        the override honoured, payoff = the scalar ``loan_balance_at_exit``,
        equity multiple on the full annual stream (monthly for an off-cycle
        month). An interim row reads the projected forward year, the
        schedule's balance at that month, and the annual stream truncated at
        its year; and it REFUSES (``not_available`` + reason) where the model
        does not price a sale — it never raises and never prints 0.
        """
        assumptions = payload.assumptions
        hold = ctx.hold
        noi_series = ctx.noi_series
        m = exit_month
        case = _ExitCase(exit_month=m, exit_year=exit_year, selected=selected)

        if selected:
            # Terminal NOI for the exit-cap calc: the override, else the selected
            # basis (default: NOI in year hold + 1). FON-44 / R-059.
            (
                terminal_noi,
                exit_noi_basis,
                exit_noi_period_label,
                terminal_src_idx,
                terminal_formula,
            ) = _resolve_terminal_noi(
                noi_series=noi_series,
                hold=hold,
                growth=assumptions.revpar_growth,
                basis=payload.exit_noi_basis,
                stabilized_year=payload.stabilized_year,
                override=payload.terminal_noi_override,
            )
            # FON-63 — negative operating NOI flows through, but a non-positive
            # exit NOI has no sale value: stop here, naming ``terminal_noi``.
            _TerminalNoiCheck(terminal_noi=terminal_noi)
        else:
            assert exit_year is not None
            k = exit_year
            (
                terminal_noi,
                exit_noi_basis,
                exit_noi_period_label,
                terminal_src_idx,
                terminal_formula,
            ) = _resolve_terminal_noi(
                noi_series=noi_series[:k],
                hold=k,
                growth=assumptions.revpar_growth,
                basis=payload.exit_noi_basis,
                stabilized_year=ctx.row_stabilized_year,
                # Open decision 3 — the pin applies to the selected exit only.
                override=None,
                # Open decision 1 — the projected Year k+1 Cash NOI where the
                # projection has it; the final year is grown, as the headline.
                next_year_noi=noi_series[k] if k < len(noi_series) else None,
            )
        case.basis = exit_noi_basis
        case.label = exit_noi_period_label
        case.formula = terminal_formula
        case.source_idx = terminal_src_idx
        case.terminal_noi = terminal_noi

        if not selected:
            # Refusals — ONE row, never the headline (which hard-stops above).
            if terminal_noi <= 0:
                case.status = "not_available"
                case.reason = ReasonCode.NO_SALE_VALUE
                case.reason_detail = (
                    f"exit NOI for a sale at month {m} is not positive "
                    f"({terminal_noi:,.0f}) — no sale value"
                )
                return case
            if (
                payload.development_like
                and payload.construction_end_month is not None
                and m <= payload.construction_end_month
            ):
                case.status = "not_available"
                case.reason = ReasonCode.UNDER_CONSTRUCTION
                case.reason_detail = (
                    f"month {m} is inside the stated construction window "
                    f"(through month {payload.construction_end_month}); a sale "
                    "before completion is not modelled"
                )
                case.terminal_noi = None
                return case
            deploying_through = _capital_deploying_through(payload, ctx)
            if deploying_through is not None and deploying_through > m:
                case.status = "not_available"
                case.reason = ReasonCode.CAPITAL_DEPLOYING
                case.reason_detail = (
                    f"capital still deploying through month {deploying_through}; "
                    "a sale before completion is not modelled"
                )
                case.terminal_noi = None
                return case

        gross_sale = terminal_noi / assumptions.exit_cap_rate
        selling_costs = gross_sale * assumptions.selling_costs_pct
        # FON-67 — transfer/recordation tax on the gross sale, deducted from the
        # reversion alongside brokerage selling costs (0 unless set).
        transfer_tax = gross_sale * assumptions.transfer_tax_pct
        refi_included = (
            ctx.refi_active and ctx.refi_month is not None and ctx.refi_month <= m
        )
        if selected:
            loan_balance_at_exit = (
                payload.loan_balance_at_exit
                if payload.loan_balance_at_exit is not None
                else payload.loan_amount  # IO assumption — full balance still outstanding
            )
            payoff_source = "loan_balance_at_exit"
        else:
            loan_balance_at_exit, payoff_source = _payoff_at(payload, m, refi_included)
        net_proceeds_to_equity = gross_sale - selling_costs - transfer_tax - loan_balance_at_exit

        # FON-67 — a mid-hold refi returns capital to equity. For the DISPLAY
        # stream (Cash Flow tab + equity multiple) fold the cash-out into its
        # year; it's levered-only, so the unlevered case is untouched. An
        # interim row keeps the years through its exit year only.
        if selected:
            cfad_series = list(ctx.op_cfad)
            if ctx.refi_active:
                cfad_series[payload.refi_year - 1] += payload.refi_cash_out
        else:
            # The cash-out enters an interim row's multiple on the SAME rule
            # its payoff and IRR use — the refinance month precedes the sale
            # (``refi_included``) — never on the debt engine's year index,
            # which would credit a month-30 refi to a month-27 sale.
            k = exit_year or hold
            cfad_series = list(ctx.op_cfad[:k])
            if refi_included and payload.refi_year is not None:
                cfad_series[min(payload.refi_year, k) - 1] += payload.refi_cash_out

        # FON-67 — compute the returns on a MONTHLY calendar (the accurate,
        # institutional method): operating cash accrues through the year, the
        # refi and sale land on their real months, and IRR is XIRR over the full
        # monthly series. See app/engines/monthly_cashflow.py.
        dy = payload.deferred_capital_year
        monthly = build_monthly_cashflow(
            MonthlyCashFlowInput(
                hold_years=hold,
                noi_by_year=list(noi_series),
                acquisition_month_offset=payload.acquisition_month_offset,
                exit_month=m,
                equity_draws=list(payload.equity_draw_schedule),
                monthly_debt_service=ctx.monthly_ds,
                equity_at_close=max(0.0, payload.equity - payload.deferred_capital),
                total_capital_at_close=max(
                    0.0, ctx.total_capital - payload.deferred_capital
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
                refi_month=ctx.refi_month,
                refi_net_cash_out=payload.refi_cash_out if ctx.refi_active else 0.0,
                exit_net_proceeds_levered=net_proceeds_to_equity,
                exit_net_proceeds_unlevered=gross_sale - selling_costs - transfer_tax,
            )
        )

        # Equity multiple. For an off-cycle exit the annual cash-flow series
        # (built over whole hold-years) overstates the distributions, so the
        # selected exit uses the monthly model's multiple, which stops at the
        # real exit month; year-end rows (and a clean hold) use the annual
        # convention — the pre-existing headline convention, preserved.
        total_distributions = sum(cfad_series) + net_proceeds_to_equity
        if selected and payload.exit_month_override is not None:
            equity_multiple = monthly.equity_multiple
        else:
            equity_multiple = (
                total_distributions / payload.equity if payload.equity else 0.0
            )

        case.gross_sale = gross_sale
        case.selling_costs = selling_costs
        case.transfer_tax = transfer_tax
        case.payoff = loan_balance_at_exit
        case.payoff_source = payoff_source
        case.refi_included = refi_included
        case.net_proceeds = net_proceeds_to_equity
        case.cfad_series = cfad_series
        case.total_distributions = total_distributions
        case.equity_multiple = equity_multiple
        case.monthly = monthly
        case.raw_levered_irr = monthly.levered_irr
        case.raw_unlevered_irr = monthly.unlevered_irr
        # A one-sign stream has no IRR: the row says so instead of printing
        # the solver's sentinel. The headline scalars keep the raw answer.
        case.levered_irr = (
            None if _irr_is_sentinel(monthly.levered_irr) else monthly.levered_irr
        )
        case.unlevered_irr = (
            None if _irr_is_sentinel(monthly.unlevered_irr) else monthly.unlevered_irr
        )
        if case.levered_irr is None or case.unlevered_irr is None:
            case.status = "not_available"
            case.reason = ReasonCode.INCONCLUSIVE
            case.reason_detail = (
                f"cash flows for a sale at month {m} have one sign; no IRR"
            )
        return case

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

        # Debt service per year. When the caller supplies a full debt-stack DS
        # series (Wave 4 W4.4) we honor it year-by-year; otherwise we fall back
        # to the scalar ``annual_debt_service`` (the legacy single-loan path —
        # preserved byte-identically).
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
        # Unlevered basis: Year 0 = TOTAL invested capital (equity + loan =
        # purchase + renovation + closing + working capital), NOT just the
        # purchase price — an all-cash buyer funds the whole project.
        total_capital = payload.equity + payload.loan_amount

        # FON-67 — the refi month and the monthly debt service. The refi funds
        # mid-hold on a much larger balance, so its interest-only payment (the
        # post-refi annual amount) applies from the actual refi month, not the
        # following year-end — otherwise the months between the refi and
        # year-end under-count debt service. Senior months keep the senior
        # schedule. Fixed across every sale date.
        refi_month: int | None = None
        if refi_active and payload.refi_year is not None:
            _rt = (
                payload.refi_time_years
                if payload.refi_time_years is not None
                else float(payload.refi_year)
            )
            refi_month = max(1, min(hold * 12, round(_rt * 12)))
        _monthly_ds = [ds_series[y] / 12.0 for y in range(hold) for _ in range(12)]
        if refi_active and refi_month is not None and ds_series:
            _refi_monthly = ds_series[-1] / 12.0
            for _m in range(refi_month, len(_monthly_ds)):
                _monthly_ds[_m] = _refi_monthly

        # The stabilized year the interim rows read on the ``stabilized`` basis
        # — resolved once on the full projection (the headline resolves its
        # own inside ``_resolve_terminal_noi``, exactly as before).
        row_stabilized_year = payload.stabilized_year
        if payload.exit_noi_basis == "stabilized" and row_stabilized_year is None:
            _idx = resolve_stabilized_year_index(
                occupancy_by_year=None,
                stabilized_occupancy=None,
                noi_by_year=noi_series,
            )
            row_stabilized_year = _idx + 1 if _idx is not None else None

        full_term = hold * 12
        ctx = _ExitContext(
            hold=hold,
            full_term=full_term,
            noi_series=noi_series,
            op_cfad=op_cfad,
            refi_active=refi_active,
            refi_month=refi_month,
            monthly_ds=_monthly_ds,
            total_capital=total_capital,
            row_stabilized_year=row_stabilized_year,
        )

        # FON-118 — the deal's own exit. ``exit_month_override`` (FON-67) is
        # clamped to the hold exactly as the monthly model clamps it; None is
        # the clean hold × 12 exit. Year-end rows sit at ``12k − offset`` — the
        # same month → calendar-year mapping ``monthly_cashflow.noi_m`` uses —
        # so the selected exit is a year row when it lands on one.
        offset = payload.acquisition_month_offset
        selected_month = (
            max(1, min(full_term, payload.exit_month_override))
            if payload.exit_month_override is not None
            else full_term
        )
        year_end_months: dict[int, int] = {12 * k - offset: k for k in range(1, hold + 1)}
        selected_year = year_end_months.get(selected_month)

        selected = self._exit_case(
            payload, ctx, exit_month=selected_month, exit_year=selected_year, selected=True
        )
        if (
            payload.exit_month_override is not None
            and payload.exit_month_override != selected_month
        ):
            selected.reason_detail = (
                f"exit_month_override {payload.exit_month_override} is past the "
                f"hold; the sale is modelled at month {selected_month}"
            )
        assert selected.terminal_noi is not None  # the hard stop raised otherwise
        assert selected.gross_sale is not None
        assert selected.selling_costs is not None
        assert selected.transfer_tax is not None
        assert selected.payoff is not None
        assert selected.net_proceeds is not None
        assert selected.monthly is not None
        assert selected.cfad_series is not None
        assert selected.raw_levered_irr is not None
        assert selected.raw_unlevered_irr is not None
        assert selected.equity_multiple is not None
        assert selected.total_distributions is not None
        terminal_noi = selected.terminal_noi
        exit_noi_basis = selected.basis or "forward_12m"
        exit_noi_period_label = selected.label
        terminal_src_idx = selected.source_idx
        terminal_formula = selected.formula or ""
        gross_sale = selected.gross_sale
        selling_costs = selected.selling_costs
        transfer_tax = selected.transfer_tax
        loan_balance_at_exit = selected.payoff
        net_proceeds_to_equity = selected.net_proceeds
        cfad_series = selected.cfad_series
        levered_irr = selected.raw_levered_irr
        unlevered_irr = selected.raw_unlevered_irr
        peak_equity_out: float | None = selected.monthly.peak_equity
        levered_monthly_out: list[float] = list(selected.monthly.levered_monthly)
        total_distributions = selected.total_distributions
        equity_multiple = selected.equity_multiple

        # PROVENANCE ONLY — the traced value the reversion NOI was grown from,
        # named only when it honestly IS one of the expense engine's projected
        # years: not pinned by an override, sourced from the expense engine, and
        # inside the series the runner handed over (a hold longer than the P&L
        # is extrapolated here, and no upstream value backs that).
        terminal_noi_ref = (
            f"expense.years[{terminal_src_idx}].noi"
            if (
                terminal_src_idx is not None
                and payload.noi_from_expense_engine
                and hold >= 1
                and len(payload.noi_by_year) >= hold
            )
            else None
        )

        # Levered cash flow stream (Year 0 = -equity) — the annual DISPLAY
        # stream the Cash Flow tab reads (``cash_flows``).
        levered_flows = [-payload.equity] + cfad_series[:-1] + [
            cfad_series[-1] + net_proceeds_to_equity
        ]
        # Unlevered cash flow stream over the total invested capital.
        unlevered_flows = [-total_capital] + noi_series[:-1] + [
            noi_series[-1] + gross_sale - selling_costs - transfer_tax
        ]
        # FON-67 — phased capital deployment: shift a chunk of capital (e.g. the
        # renovation) out of the t=0 close and into its deployment year, in both
        # annual streams. Index 0 is t=0; index d is year d in every stream here.
        dc = payload.deferred_capital
        dy = payload.deferred_capital_year
        if dc > 0 and dy is not None and 1 <= dy <= hold:
            unlevered_flows[0] += dc
            unlevered_flows[dy] -= dc
            levered_flows[0] += dc
            levered_flows[dy] -= dc

        year_one_coc = (
            cfad_series[0] / payload.equity if payload.equity else 0.0
        )
        avg_coc = (
            (sum(cfad_series) / len(cfad_series)) / payload.equity
            if payload.equity and cfad_series
            else 0.0
        )

        # FON-118 — returns by exit year: every projection year-end, in month
        # order, with the selected exit either AS its year row or inserted as
        # its own off-cycle row. Grid callers pass ``exit_schedule=False``.
        cases: list[_ExitCase] = []
        if payload.exit_schedule:
            for m_k, k in sorted(year_end_months.items()):
                if m_k == selected_month:
                    cases.append(selected)
                else:
                    cases.append(
                        self._exit_case(
                            payload, ctx, exit_month=m_k, exit_year=k, selected=False
                        )
                    )
            if selected_year is None:
                pos = next(
                    (i for i, c in enumerate(cases) if c.exit_month > selected_month),
                    len(cases),
                )
                cases.insert(pos, selected)
        peak_year: int | None = None
        peak_irr: float | None = None
        for c in cases:
            if c.exit_year is None or c.status != "computed" or c.levered_irr is None:
                continue
            if peak_irr is None or c.levered_irr > peak_irr:
                peak_irr = c.levered_irr
                peak_year = c.exit_year
        exit_schedule = [
            _exit_row(c, is_peak=(c.exit_year is not None and c.exit_year == peak_year))
            for c in cases
        ]
        row_provenance: dict[str, ValueTrace] = {}
        for i, c in enumerate(cases):
            row_provenance.update(_exit_row_provenance(i, c, payload=payload, ctx=ctx))

        return ReturnsEngineOutputExt(
            deal_id=payload.deal_id,
            levered_irr=levered_irr,
            unlevered_irr=unlevered_irr,
            equity_multiple=equity_multiple,
            peak_equity=peak_equity_out,
            levered_monthly=levered_monthly_out,
            year_one_coc=year_one_coc,
            avg_coc=avg_coc,
            gross_sale_price=gross_sale,
            selling_costs=selling_costs,
            net_proceeds=net_proceeds_to_equity,
            hold_years=hold,
            exit_cap_rate=assumptions.exit_cap_rate,
            terminal_noi=terminal_noi,
            exit_noi_basis=exit_noi_basis,  # type: ignore[arg-type]
            exit_noi_period_label=exit_noi_period_label,
            exit_schedule=exit_schedule,
            selected_exit_month=selected_month,
            selected_exit_year=selected_year,
            peak_levered_exit_year=peak_year,
            cash_flows=levered_flows,
            cash_flows_unlevered=unlevered_flows,
            noi_by_year=list(noi_series),
            provenance=apply_states({
                "terminal_noi": _terminal_noi_trace(
                    terminal_noi=terminal_noi,
                    basis=exit_noi_basis,
                    label=exit_noi_period_label,
                    formula=terminal_formula,
                    source_noi=(
                        noi_series[terminal_src_idx]
                        if terminal_src_idx is not None
                        else None
                    ),
                    source_ref=terminal_noi_ref,
                    growth=assumptions.revpar_growth,
                ),
                **_exit_value_provenance(
                    terminal_noi=terminal_noi,
                    basis_label=exit_noi_period_label,
                    basis=exit_noi_basis,
                    exit_cap_rate=assumptions.exit_cap_rate,
                    gross_sale=gross_sale,
                    selling_costs_pct=assumptions.selling_costs_pct,
                    selling_costs=selling_costs,
                    loan_balance_at_exit=loan_balance_at_exit,
                    net_proceeds=net_proceeds_to_equity,
                    total_distributions=total_distributions,
                    equity=payload.equity,
                    equity_multiple=equity_multiple,
                    terminal_noi_ref=terminal_noi_ref,
                ),
                **_irr_provenance(
                    levered_irr=levered_irr,
                    levered_flows=levered_flows,
                    unlevered_irr=unlevered_irr,
                    unlevered_flows=unlevered_flows,
                ),
                **_coc_provenance(
                    year_one_coc=year_one_coc,
                    avg_coc=avg_coc,
                    year_one_cfad=cfad_series[0] if cfad_series else 0.0,
                    total_cfad=sum(cfad_series),
                    n_years=len(cfad_series),
                    equity=payload.equity,
                ),
                **row_provenance,
            }),
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
        noi_by_year=list(noi_series),
        provenance=apply_states({
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
            **_coc_provenance(
                year_one_coc=cfad[0] / equity if equity and cfad else 0.0,
                avg_coc=(sum(cfad) / len(cfad)) / equity if equity and cfad else 0.0,
                year_one_cfad=cfad[0] if cfad else 0.0,
                total_cfad=sum(cfad),
                n_years=len(cfad),
                equity=equity,
            ),
        }),
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
