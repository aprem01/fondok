"""Debt engine — loan amortization, debt service coverage, refinance test.

No external deps: PMT and amortization schedule are computed in pure Python.
Supports an interest-only stub period followed by P&I amortization.

Wave 4 W4.4 — extended to a TRANCHE STACK (senior + mezz + pref equity).
The legacy single-loan ``DebtEngine`` is preserved byte-for-byte; the new
stack helpers (``build_amort_schedule``, ``build_stack_schedule``,
``run_refi_test``) operate on the ``DebtStackInput`` / ``DebtStackOutput``
schemas. A single-tranche senior-only stack reproduces the legacy
schedule identically — see ``test_single_senior_tranche_matches_legacy_single_loan``.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fondok_schemas.debt_stack import (
    DebtStackInput,
    DebtStackOutput,
    DebtTranche,
    RefiTestResult,
    TrancheAmortYear,
    TrancheSchedule,
)
from fondok_schemas.provenance import ValueInput, ValueTrace
from fondok_schemas.underwriting import (
    DebtEngineInput,
    DebtEngineOutput,
    DebtServiceYear,
)

from .base import BaseEngine


class DebtEngineInputExt(DebtEngineInput):
    """Debt input plus the NOI series used for DSCR and debt-yield reporting."""

    model_config = ConfigDict(extra="forbid")

    noi_by_year: list[Annotated[float, Field(ge=0)]] = Field(default_factory=list)


class DebtMonth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: Annotated[int, Field(ge=1)]
    interest: Annotated[float, Field(ge=0)]
    principal: Annotated[float, Field(ge=0)]
    payment: Annotated[float, Field(ge=0)]
    ending_balance: Annotated[float, Field(ge=0)]


class DebtEngineOutputExt(DebtEngineOutput):
    """Debt output enriched with DSCR, debt-yield and a monthly schedule.

    ``loan_amount`` is echoed from the input so the web app can render
    the headline KPI strip (loan amount + DSCR + debt yield) without
    having to re-fetch the engine inputs separately. The Debt tab
    treats a missing ``loan_amount`` as "engine hasn't run yet" and
    short-circuits to the empty-state placeholder — Sam QA #4 was
    that path triggering even though DSCR was clearly present.
    """

    model_config = ConfigDict(extra="forbid")

    loan_amount: Annotated[float, Field(ge=0)] | None = None
    monthly_schedule: list[DebtMonth] = Field(default_factory=list)
    year_one_dscr: Annotated[float, Field(ge=0)] | None = None
    year_one_debt_yield: Annotated[float, Field(ge=0)] | None = None


def pmt(rate: float, nper: int, pv: float) -> float:
    """Standard mortgage PMT — periodic payment to fully amortize ``pv``."""
    if nper <= 0:
        return 0.0
    if rate == 0:
        return pv / nper
    factor = (1.0 + rate) ** nper
    return pv * (rate * factor) / (factor - 1.0)


class DebtEngine(BaseEngine[DebtEngineInputExt, DebtEngineOutputExt]):
    """Build the debt service schedule and DSCR / debt-yield headline metrics."""

    name = "debt"

    def run(self, payload: DebtEngineInputExt) -> DebtEngineOutputExt:
        loan = payload.loan_amount
        annual_rate = payload.interest_rate
        monthly_rate = annual_rate / 12.0
        amort_months = payload.amortization_years * 12
        io_months = payload.interest_only_years * 12

        # Monthly payment for the amortizing portion.
        amortizing_pmt = pmt(monthly_rate, amort_months, loan) if amort_months else 0.0

        balance = loan
        monthly_schedule: list[DebtMonth] = []
        # We track per-month for the full term and roll up to annual at the end.
        total_months = max(payload.term_years * 12, 12)

        for m in range(1, total_months + 1):
            interest = balance * monthly_rate
            if m <= io_months:
                principal = 0.0
                payment = interest
            else:
                payment = amortizing_pmt
                principal = max(0.0, payment - interest)
                if principal > balance:
                    principal = balance
                    payment = principal + interest
            balance = max(0.0, balance - principal)
            monthly_schedule.append(
                DebtMonth(
                    month=m,
                    interest=interest,
                    principal=principal,
                    payment=payment,
                    ending_balance=balance,
                )
            )

        # Roll up to annual schedule.
        schedule: list[DebtServiceYear] = []
        # FON-25 — per-value provenance sidecar for the debt schedule.
        prov: dict[str, ValueTrace] = {}
        for y in range(1, payload.term_years + 1):
            window = monthly_schedule[(y - 1) * 12 : y * 12]
            if not window:
                break
            interest_sum = sum(m.interest for m in window)
            principal_sum = sum(m.principal for m in window)
            ds = interest_sum + principal_sum
            ending = window[-1].ending_balance
            noi_y = (
                payload.noi_by_year[y - 1]
                if y - 1 < len(payload.noi_by_year)
                else None
            )
            dscr = (noi_y / ds) if (noi_y is not None and ds > 0) else None
            idx = len(schedule)
            prov[f"schedule[{idx}].debt_service"] = ValueTrace(
                value=ds,
                formula="debt_service = interest + principal",
                inputs=[
                    ValueInput(name="interest", value=interest_sum),
                    ValueInput(name="principal", value=principal_sum),
                ],
                note=(
                    "Annual roll-up of the monthly amortization schedule "
                    f"(12 months of year {y})."
                ),
            )
            if dscr is not None:
                prov[f"schedule[{idx}].dscr"] = ValueTrace(
                    value=dscr,
                    formula="dscr = noi ÷ debt_service",
                    inputs=[
                        ValueInput(name="noi", value=noi_y),
                        ValueInput(
                            name="debt_service",
                            value=ds,
                            traces_to=f"schedule[{idx}].debt_service",
                        ),
                    ],
                    note="Debt Service Coverage Ratio — lender's cushion test.",
                )
            schedule.append(
                DebtServiceYear(
                    year=y,
                    interest=interest_sum,
                    principal=principal_sum,
                    debt_service=ds,
                    ending_balance=ending,
                    dscr=dscr,
                )
            )

        annual_ds = schedule[0].debt_service if schedule else 0.0
        year1_dscr = schedule[0].dscr if schedule else None
        year1_dy = (
            (payload.noi_by_year[0] / loan)
            if payload.noi_by_year and loan > 0
            else None
        )
        dscrs = [yr.dscr for yr in schedule if yr.dscr is not None]
        avg_dscr = sum(dscrs) / len(dscrs) if dscrs else None

        return DebtEngineOutputExt(
            deal_id=payload.deal_id,
            annual_debt_service=annual_ds,
            schedule=schedule,
            avg_dscr=avg_dscr,
            loan_amount=payload.loan_amount,
            monthly_schedule=monthly_schedule,
            year_one_dscr=year1_dscr,
            year_one_debt_yield=year1_dy,
            provenance=prov,
        )


# ─────────────────────────── Debt Stack v2 ────────────────────────────
#
# Wave 4 W4.4. The stack engine is intentionally pure — no DB, no LLM,
# no I/O — so the tests can spin up a tranche list and assert on
# dollars-and-cents output.


def build_amort_schedule(tranche: DebtTranche, *, term_years: int) -> list[DebtMonth]:
    """Pure helper: per-tranche monthly amortization schedule.

    Replicates the legacy single-loan path (IO stub → amortizing PMT)
    on a per-tranche basis. The ``term_years`` argument is the OUTER
    schedule horizon — the same length we report for every tranche so
    the rollup math aligns year-on-year.

    Exit fee (if any) is appended to the final month's payment, not
    capitalized into a new principal balance.
    """
    monthly_rate = tranche.rate_pct / 12.0
    amort_months = tranche.amortization_months
    io_months = tranche.io_period_months

    amortizing_pmt = (
        pmt(monthly_rate, amort_months, tranche.principal_usd)
        if amort_months > 0
        else 0.0
    )

    balance = tranche.principal_usd
    schedule: list[DebtMonth] = []
    total_months = max(term_years * 12, 12)

    for m in range(1, total_months + 1):
        interest = balance * monthly_rate
        if m <= io_months or amort_months == 0:
            principal = 0.0
            payment = interest
        else:
            payment = amortizing_pmt
            principal = max(0.0, payment - interest)
            if principal > balance:
                principal = balance
                payment = principal + interest
        balance = max(0.0, balance - principal)
        # Exit fee lands on the final month of the outer term.
        if m == total_months and tranche.exit_fee_pct > 0:
            exit_fee = tranche.principal_usd * (tranche.exit_fee_pct / 100.0)
            payment += exit_fee
        schedule.append(
            DebtMonth(
                month=m,
                interest=interest,
                principal=principal,
                payment=payment,
                ending_balance=balance,
            )
        )
    return schedule


def _annualize(schedule: list[DebtMonth], term_years: int) -> list[TrancheAmortYear]:
    """Roll a monthly schedule up into per-year buckets."""
    out: list[TrancheAmortYear] = []
    for y in range(1, term_years + 1):
        window = schedule[(y - 1) * 12 : y * 12]
        if not window:
            break
        interest_sum = sum(m.interest for m in window)
        principal_sum = sum(m.principal for m in window)
        # Total cash debt service for the year — includes exit fee
        # tacked onto the final month payment by ``build_amort_schedule``.
        ds = sum(m.payment for m in window)
        ending = window[-1].ending_balance
        out.append(
            TrancheAmortYear(
                year=y,
                interest_usd=interest_sum,
                principal_usd=principal_sum,
                debt_service_usd=ds,
                ending_balance_usd=ending,
            )
        )
    return out


def build_stack_schedule(payload: DebtStackInput) -> DebtStackOutput:
    """Build the full debt-stack schedule.

    Aggregates per-tranche debt service into ``total_ds_by_year``,
    computes debt yield against EOP balances, and reports cumulative
    DSCR per tranche (rank 1 sees only senior DS; rank 2 sees senior +
    mezz; etc.) plus a blended DSCR using the entire stack.
    """
    term_years = payload.term_years

    schedules: list[TrancheSchedule] = []
    monthly_by_tranche: dict[str, list[DebtMonth]] = {}
    annual_by_tranche: dict[str, list[TrancheAmortYear]] = {}
    upfront_total = 0.0

    for tranche in payload.tranches:
        monthly = build_amort_schedule(tranche, term_years=term_years)
        annual = _annualize(monthly, term_years)
        upfront_fee = tranche.principal_usd * (tranche.upfront_fee_pct / 100.0)
        exit_fee = tranche.principal_usd * (tranche.exit_fee_pct / 100.0)
        upfront_total += upfront_fee
        schedules.append(
            TrancheSchedule(
                name=tranche.name,
                label=tranche.label,
                priority_rank=tranche.priority_rank,
                years=annual,
                upfront_fee_usd=upfront_fee,
                exit_fee_usd=exit_fee,
            )
        )
        monthly_by_tranche[tranche.name] = monthly
        annual_by_tranche[tranche.name] = annual

    # Year-over-year aggregates.
    total_ds_by_year: list[float] = []
    debt_service_per_tranche_by_year: dict[str, list[float]] = {
        t.name: [] for t in payload.tranches
    }
    ending_balance_per_tranche_by_year: dict[str, list[float]] = {
        t.name: [] for t in payload.tranches
    }

    for y in range(term_years):
        year_total = 0.0
        for tranche in payload.tranches:
            ds = (
                annual_by_tranche[tranche.name][y].debt_service_usd
                if y < len(annual_by_tranche[tranche.name])
                else 0.0
            )
            eob = (
                annual_by_tranche[tranche.name][y].ending_balance_usd
                if y < len(annual_by_tranche[tranche.name])
                else 0.0
            )
            debt_service_per_tranche_by_year[tranche.name].append(ds)
            ending_balance_per_tranche_by_year[tranche.name].append(eob)
            year_total += ds
        total_ds_by_year.append(year_total)

    # Debt yield (NOI / EOP total debt outstanding).
    debt_yield_by_year: list[float] = []
    for y in range(term_years):
        noi_y = (
            payload.noi_by_year[y] if y < len(payload.noi_by_year) else 0.0
        )
        eob_total = sum(
            ending_balance_per_tranche_by_year[t.name][y]
            for t in payload.tranches
            if y < len(ending_balance_per_tranche_by_year[t.name])
        )
        dy = (noi_y / eob_total) if eob_total > 0 else 0.0
        debt_yield_by_year.append(dy)

    # DSCR per tranche — cumulative through that tranche's priority
    # rank (rank 1 = senior DS only; rank 2 = senior + mezz; etc.).
    # Tranches share NOI in the numerator.
    tranches_by_rank = sorted(payload.tranches, key=lambda t: t.priority_rank)
    dscr_by_year_per_tranche: dict[str, list[float]] = {
        t.name: [] for t in payload.tranches
    }
    for y in range(term_years):
        noi_y = payload.noi_by_year[y] if y < len(payload.noi_by_year) else 0.0
        cum_ds = 0.0
        for t in tranches_by_rank:
            cum_ds += debt_service_per_tranche_by_year[t.name][y]
            dscr = (noi_y / cum_ds) if cum_ds > 0 else 0.0
            dscr_by_year_per_tranche[t.name].append(dscr)

    dscr_blended_by_year: list[float] = []
    for y in range(term_years):
        noi_y = payload.noi_by_year[y] if y < len(payload.noi_by_year) else 0.0
        ds_y = total_ds_by_year[y]
        dscr_blended_by_year.append((noi_y / ds_y) if ds_y > 0 else 0.0)

    total_debt = sum(t.principal_usd for t in payload.tranches)
    weighted_rate = (
        sum(t.principal_usd * t.rate_pct for t in payload.tranches) / total_debt
        if total_debt > 0
        else 0.0
    )

    cumulative_ltc = total_debt / payload.purchase_price_usd if payload.purchase_price_usd > 0 else 0.0
    cumulative_ltv = cumulative_ltc

    out = DebtStackOutput(
        deal_id=payload.deal_id,
        total_ds_by_year=total_ds_by_year,
        debt_service_per_tranche_by_year=debt_service_per_tranche_by_year,
        debt_yield_by_year=debt_yield_by_year,
        dscr_by_year_per_tranche=dscr_by_year_per_tranche,
        dscr_blended_by_year=dscr_blended_by_year,
        cumulative_ltc=cumulative_ltc,
        cumulative_ltv=cumulative_ltv,
        total_debt_usd=total_debt,
        weighted_avg_rate_pct=weighted_rate,
        schedules=schedules,
        refi_test=None,
        total_upfront_fees_usd=upfront_total,
    )
    if payload.refi_test_year is not None:
        out.refi_test = run_refi_test(payload, out)
    return out


def run_refi_test(
    payload: DebtStackInput,
    schedule: DebtStackOutput,
) -> RefiTestResult:
    """Run the Year-N refinance test.

    At ``refi_test_year``:

    * Property value = ``noi[year+1] / market_cap_rate``.
    * Max refi debt (debt-yield constraint) = ``noi[year+1] / market_debt_yield``.
    * DSCR at refi rate: assume IO-equivalent debt service =
      ``max_refi_debt × refi_rate_pct``; require DSCR ≥ floor.
    * Outstanding balance = sum of EOP balances across all tranches at
      ``refi_test_year``.

    Returns a :class:`RefiTestResult` with ``can_refi`` True only when
    BOTH constraints clear AND ``max_refi_debt >= outstanding_balance``.
    Otherwise ``cash_to_close_equity`` is the shortfall the sponsor
    has to bring to close out the refinance.
    """
    year = payload.refi_test_year or 5
    # Refi uses the FOLLOWING year's NOI (the lender underwrites on
    # the next 12 months' NOI, not on the trailing).
    refi_noi_idx = year  # year is 1-indexed, list is 0-indexed; next year's NOI = noi_by_year[year]
    if refi_noi_idx >= len(payload.noi_by_year):
        # Fall back to last available year so the result is still
        # meaningful — flagged via a note.
        refi_noi_idx = max(0, len(payload.noi_by_year) - 1)
        notes_fallback = [f"NOI for year {year + 1} unavailable; using last available NOI"]
    else:
        notes_fallback = []
    refi_noi = payload.noi_by_year[refi_noi_idx] if payload.noi_by_year else 0.0

    market_cap = payload.refi_market_cap_rate or payload.exit_cap_rate
    market_dy = payload.refi_market_debt_yield_pct
    dscr_floor = payload.refi_market_dscr_min
    # Refi rate proxy — explicit market rate if set, else the senior
    # tranche rate (refis are senior-only by default in our model).
    senior = next((t for t in payload.tranches if t.is_senior), payload.tranches[0])
    refi_rate = payload.refi_market_rate_pct or senior.rate_pct

    refi_property_value = (refi_noi / market_cap) if market_cap > 0 else 0.0
    # Two constraints — take the binding one.
    max_debt_dy = (refi_noi / market_dy) if market_dy > 0 else 0.0
    # DSCR-implied max debt (assuming IO refi for the test).
    max_debt_dscr = (refi_noi / (dscr_floor * refi_rate)) if refi_rate > 0 else float("inf")
    max_refi_debt = min(max_debt_dy, max_debt_dscr) if max_debt_dscr != float("inf") else max_debt_dy

    # Outstanding balance at the END of refi_test_year (sum across
    # tranches). For year=5, that's the EOP balance after Y5.
    eop_idx = year - 1  # year 5 → index 4 (Y5 EOP)
    outstanding = 0.0
    for sched in schedule.schedules:
        if eop_idx < len(sched.years):
            outstanding += sched.years[eop_idx].ending_balance_usd
    # DSCR @ refi rate using outstanding balance (or max if lower).
    refi_balance_for_check = min(outstanding, max_refi_debt) if max_refi_debt > 0 else outstanding
    refi_ds = refi_balance_for_check * refi_rate
    refi_dscr = (refi_noi / refi_ds) if refi_ds > 0 else None
    refi_dy = (refi_noi / outstanding) if outstanding > 0 else None

    notes = list(notes_fallback)
    can_refi_dy = max_debt_dy >= outstanding if outstanding > 0 else True
    # DSCR check: at the outstanding balance, can we still clear floor?
    dscr_at_outstanding = (
        (refi_noi / (outstanding * refi_rate))
        if outstanding > 0 and refi_rate > 0
        else float("inf")
    )
    can_refi_dscr = dscr_at_outstanding >= dscr_floor
    can_refi = can_refi_dy and can_refi_dscr

    cash_to_close = 0.0
    if not can_refi and outstanding > max_refi_debt:
        cash_to_close = outstanding - max_refi_debt
        notes.append(
            f"Refi shortfall: outstanding ${outstanding:,.0f} > max ${max_refi_debt:,.0f}"
        )
    if not can_refi_dscr:
        notes.append(
            f"DSCR @ refi rate ({dscr_at_outstanding:.2f}x) below floor ({dscr_floor:.2f}x)"
        )

    return RefiTestResult(
        triggered_year=year,
        can_refi=can_refi,
        max_refi_debt_usd=max_refi_debt,
        outstanding_balance_usd=outstanding,
        cash_to_close_equity=cash_to_close,
        refi_dscr=refi_dscr,
        refi_debt_yield=refi_dy,
        refi_property_value_usd=refi_property_value,
        refi_rate_used_pct=refi_rate,
        notes=notes,
    )


__all__ = [
    "DebtEngine",
    "DebtEngineInputExt",
    "DebtEngineOutputExt",
    "DebtMonth",
    "build_amort_schedule",
    "build_stack_schedule",
    "pmt",
    "run_refi_test",
]
