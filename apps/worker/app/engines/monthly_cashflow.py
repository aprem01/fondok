"""Monthly cash-flow model — the period-accurate foundation for returns.

Institutional underwriting runs on a monthly calendar, not annual buckets:
operating cash accrues through the year, capital deploys on real dates, the
refinance and the sale land on specific months, and the return is a **date-based
XIRR** over the whole monthly series. The annual returns view is a *summary* of
this. Modeling monthly is what lets Fondok reconcile to a source Excel model to
within a rounding error instead of the several IRR points an annual grid loses
to intra-year timing and partial stub years (FON-67).

This module is deliberately pure and self-contained: it takes annual assumptions
plus the event months, expands them to a month-by-month levered and unlevered
cash-flow series, and solves each for its XIRR. The returns engine composes it.
"""

from __future__ import annotations

from dataclasses import dataclass


def xnpv(rate: float, flows: list[tuple[float, float]]) -> float:
    """NPV of ``(t_years, amount)`` flows at ``rate``."""
    return sum(cf / ((1.0 + rate) ** t) for t, cf in flows)


def xirr(flows: list[tuple[float, float]], tol: float = 1e-9, max_iter: int = 400) -> float:
    """Date-based IRR over ``(t_years, amount)`` flows via bisection.

    Returns 0.0 when the series can't bracket a root (all one sign)."""
    if not flows or all(cf >= 0 for _, cf in flows) or all(cf <= 0 for _, cf in flows):
        return 0.0
    lo, hi = -0.9999, 10.0
    f_lo = xnpv(lo, flows)
    if f_lo * xnpv(hi, flows) > 0:
        return 0.0
    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        f_mid = xnpv(mid, flows)
        if abs(f_mid) < tol:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo = mid
            f_lo = f_mid
    return (lo + hi) / 2.0


@dataclass
class MonthlyCashFlowInput:
    """Assumptions for the monthly model. Amounts are whole dollars; months are
    integer offsets from acquisition (month 0)."""

    hold_years: int
    # Operating NOI per calendar year from the acquisition year (index 0 = the
    # acquisition calendar year). Distributed evenly across that year's months.
    noi_by_year: list[float]
    # Monthly debt service, one entry per hold month (index 0 = month 1). The
    # caller builds this so the senior→refi transition lands on the actual refi
    # month and IO vs. amortization is handled at source. A short/empty list is
    # padded with 0.0.
    monthly_debt_service: list[float]
    # Capital funded at acquisition (month 0): equity portion (levered basis) and
    # the full project cost net of deferred capital (unlevered basis).
    equity_at_close: float
    total_capital_at_close: float
    # Months already elapsed in the acquisition calendar year at close (0-11).
    # 0 = acquired at the start of a calendar year, so hold-years are clean
    # 12-month blocks (the general case). >0 models a mid-year acquisition: a
    # 9/30 close (offset 9) makes the first calendar year a 3-month stub, so the
    # following year's higher NOI begins at month 4 — which lifts the IRR and is
    # required to reconcile a calendar-year source model.
    acquisition_month_offset: int = 0
    # Capital deferred out of the close and deployed later — e.g. renovation.
    # Spread evenly across [deploy_start_month, deploy_end_month] inclusive.
    deferred_capital: float = 0.0
    deferred_capital_start_month: int = 1
    deferred_capital_end_month: int = 12
    # Mid-hold refinance. ``refi_net_cash_out`` = proceeds − senior payoff − fee,
    # a levered-only inflow at ``refi_month``.
    refi_month: int | None = None
    refi_net_cash_out: float = 0.0
    # Reversion at exit (final month): net of selling costs, transfer tax and the
    # loan payoff (levered) / gross of debt (unlevered).
    exit_net_proceeds_levered: float = 0.0
    exit_net_proceeds_unlevered: float = 0.0


@dataclass
class MonthlyCashFlowResult:
    levered_irr: float
    unlevered_irr: float
    equity_multiple: float
    peak_equity: float
    levered_monthly: list[float]
    unlevered_monthly: list[float]


def build_monthly_cashflow(inp: MonthlyCashFlowInput) -> MonthlyCashFlowResult:
    exit_month = inp.hold_years * 12
    n_years = len(inp.noi_by_year)

    def noi_m(month: int) -> float:
        # Calendar-year index of this month given the acquisition offset. With
        # offset 0 this is the clean hold-year (months 1-12 → year 0); with an
        # offset it aligns to calendar years so a mid-year close gets a stub.
        y = (inp.acquisition_month_offset + month - 1) // 12
        if not inp.noi_by_year:
            return 0.0
        y = min(y, n_years - 1)  # pad the trailing stub year with the last NOI
        return inp.noi_by_year[y] / 12.0

    def ds_m(month: int) -> float:
        idx = month - 1
        return inp.monthly_debt_service[idx] if 0 <= idx < len(inp.monthly_debt_service) else 0.0

    # Deferred capital (e.g. renovation) spread over its deployment window.
    lo, hi = inp.deferred_capital_start_month, inp.deferred_capital_end_month
    window = max(1, hi - lo + 1)
    per_month_defer = inp.deferred_capital / window if inp.deferred_capital > 0 else 0.0

    def defer_m(month: int) -> float:
        return per_month_defer if lo <= month <= hi else 0.0

    lev: list[tuple[float, float]] = [(0.0, -inp.equity_at_close)]
    unlev: list[tuple[float, float]] = [(0.0, -inp.total_capital_at_close)]
    lev_series = [-inp.equity_at_close]
    unlev_series = [-inp.total_capital_at_close]

    # Peak equity: the deepest cumulative equity outflow (close + deferred draws
    # net of any interim distributions) before capital starts coming back.
    cum_equity, peak_equity = inp.equity_at_close, inp.equity_at_close
    distributions = 0.0

    for m in range(1, exit_month + 1):
        t = m / 12.0
        cap = defer_m(m)
        refi = inp.refi_net_cash_out if (inp.refi_month is not None and m == inp.refi_month) else 0.0
        exit_lev = inp.exit_net_proceeds_levered if m == exit_month else 0.0
        exit_unlev = inp.exit_net_proceeds_unlevered if m == exit_month else 0.0

        lev_cf = noi_m(m) - ds_m(m) - cap + refi + exit_lev
        unlev_cf = noi_m(m) - cap + exit_unlev
        lev.append((t, lev_cf))
        unlev.append((t, unlev_cf))
        lev_series.append(lev_cf)
        unlev_series.append(unlev_cf)

        # Track peak equity from the levered draws (negative levered CF = further
        # equity in); positive CF returns capital.
        cum_equity += -lev_cf if lev_cf < 0 else 0.0
        peak_equity = max(peak_equity, cum_equity)
        if lev_cf > 0:
            distributions += lev_cf

    equity_multiple = (
        (inp.equity_at_close + max(0.0, distributions - 0.0)) / peak_equity
        if peak_equity > 0
        else 0.0
    )
    # Equity multiple = total distributions ÷ total equity invested.
    total_equity_in = inp.equity_at_close + inp.deferred_capital
    equity_multiple = (distributions / total_equity_in) if total_equity_in > 0 else 0.0

    return MonthlyCashFlowResult(
        levered_irr=xirr(lev),
        unlevered_irr=xirr(unlev),
        equity_multiple=equity_multiple,
        peak_equity=peak_equity,
        levered_monthly=lev_series,
        unlevered_monthly=unlev_series,
    )
