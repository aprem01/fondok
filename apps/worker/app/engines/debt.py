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

from typing import Annotated, Any
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
from .tranche_stack import DebtStackResult, LoanTranche, compute_debt_stack


class DebtEngineInputExt(DebtEngineInput):
    """Debt input plus the NOI series used for DSCR and debt-yield reporting."""

    model_config = ConfigDict(extra="forbid")

    noi_by_year: list[Annotated[float, Field(ge=0)]] = Field(default_factory=list)
    # FON-63 — deal basis for the multi-tranche stack's LTV / LTC. Optional so
    # the legacy single-loan callers don't have to supply them.
    purchase_price_usd: Annotated[float, Field(ge=0)] | None = None
    total_capital_usd: Annotated[float, Field(ge=0)] | None = None
    # FON-63 — analyst per-tranche edits from the Debt tab, shaped
    # ``{"tranches": {0: {field: value}, 1: {...}}, <stack-level keys>}``.
    # The senior (index 0) is seeded from the deal; index 1 is the PACE
    # placeholder an analyst activates by entering a principal. Optional so
    # legacy callers are byte-for-byte unaffected.
    debt_stack_overrides: dict[str, Any] | None = None


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
    # FON-59 — echo the loan terms so the Overview Financing tile can render
    # Interest Rate / Term / Amortization without re-fetching the debt inputs.
    interest_rate: Annotated[float, Field(ge=0)] | None = None
    term_years: Annotated[int, Field(ge=0)] | None = None
    amortization_years: Annotated[int, Field(ge=0)] | None = None
    # FON-63 — the institutional multi-tranche view. Seeded from the deal's own
    # extracted senior loan; a user adds PACE / mezz + floating terms in the
    # Debt tab. Optional so legacy consumers ignore it.
    debt_stack: DebtStackResult | None = None
    # FON-67 — two-phase senior→refinance financing. Populated only when the
    # analyst sets a refi year; empty/zero keeps single-phase deals unchanged.
    # ``debt_service_by_year`` is the phased DS (senior then refi) the Returns
    # engine consumes; ``refi_cash_out`` is the net proceeds returned to equity
    # at ``refi_year``; ``balance_at_exit`` is the loan balance at the sale.
    debt_service_by_year: list[Annotated[float, Field(ge=0)]] = Field(
        default_factory=list
    )
    refi_cash_out: Annotated[float, Field(ge=0)] = 0.0
    refi_year: Annotated[int, Field(ge=1)] | None = None
    balance_at_exit: Annotated[float, Field(ge=0)] | None = None


def pmt(rate: float, nper: int, pv: float) -> float:
    """Standard mortgage PMT — periodic payment to fully amortize ``pv``."""
    if nper <= 0:
        return 0.0
    if rate == 0:
        return pv / nper
    factor = (1.0 + rate) ** nper
    return pv * (rate * factor) / (factor - 1.0)


# FON-63 — a forward-SOFR assumption for any floating tranche the analyst
# configures. Fixed tranches (the default senior) ignore it.
_SOFR_DEFAULT = 0.043

# FON-63 — the editable per-tranche fields, keyed by the override path
# ``debt_stack.tranches.<idx>.<field>``. Values arrive as fractions (rates,
# fees) or raw units (USD, months), matching the Debt tab inputs.
_TRANCHE_OVERRIDE_FIELDS = (
    "principal_usd",
    "rate_pct",
    "amortization_months",
    "io_period_months",
    "upfront_fee_pct",
    "exit_fee_pct",
)


def _build_default_tranches(payload: DebtEngineInputExt) -> list[LoanTranche]:
    """Deal-agnostic institutional default stack: the deal's own senior loan
    (index 0) plus a PACE placeholder (index 1). PACE starts at $0 / pending so
    the default economics equal the legacy single-senior model until an analyst
    activates it in the Debt tab."""
    senior_is_io = (
        payload.amortization_years == 0
        or payload.interest_only_years >= (payload.term_years or 0)
    )
    senior = LoanTranche(
        kind="senior",
        label="Senior Loan",
        loan_amount=payload.loan_amount,
        rate_type="fixed",
        fixed_rate=payload.interest_rate,
        interest_only=senior_is_io,
        amortization_years=payload.amortization_years or None,
        term_years=payload.term_years or None,
    )
    pace = LoanTranche(
        kind="pace",
        label="PACE Loan",
        loan_amount=0.0,
        terms_pending=True,
    )
    return [senior, pace]


def _apply_tranche_overrides(
    tranches: list[LoanTranche], overrides: dict[str, Any] | None
) -> list[LoanTranche]:
    """Layer analyst per-tranche edits onto the seed tranches. Each override
    maps a Debt-tab field onto the ``LoanTranche`` shape; supplying a rate on a
    pending tranche activates it. JSONB round-trips can key the tranche index as
    an int or a string, so both are accepted."""
    if not overrides:
        return tranches
    tranche_ovs = overrides.get("tranches") or {}
    if not tranche_ovs:
        return tranches
    out: list[LoanTranche] = []
    for idx, t in enumerate(tranches):
        ov = tranche_ovs.get(idx)
        if ov is None:
            ov = tranche_ovs.get(str(idx))
        if not ov:
            out.append(t)
            continue
        data = t.model_dump()
        if "principal_usd" in ov:
            data["loan_amount"] = max(0.0, float(ov["principal_usd"]))
        if "rate_pct" in ov:
            data["fixed_rate"] = float(ov["rate_pct"])
            data["rate_type"] = "fixed"
            data["terms_pending"] = False
        if "amortization_months" in ov:
            # FON-63 follow-up: a single Amort control covers both cases —
            # >0 amortizes over that term, 0 means interest-only.
            months = float(ov["amortization_months"])
            if months > 0:
                data["amortization_years"] = int(round(months / 12))
                data["interest_only"] = False
            else:
                data["amortization_years"] = None
                data["interest_only"] = True
        if "io_period_months" in ov:
            io_months = float(ov["io_period_months"])
            data["interest_only"] = io_months > 0
            if io_months > 0:
                data["amortization_years"] = None
        if "upfront_fee_pct" in ov:
            data["origination_fee_pct"] = float(ov["upfront_fee_pct"])
        if "exit_fee_pct" in ov:
            data["exit_fee_pct"] = float(ov["exit_fee_pct"])
        # A tranche with a principal but still no resolvable rate stays pending
        # (compute_debt_stack will exclude it from debt service, not invent one).
        if (
            data.get("fixed_rate") is None
            and data.get("spread") is None
            and data["loan_amount"] > 0
        ):
            data["terms_pending"] = True
        out.append(LoanTranche(**data))
    return out


def _refi_params(overrides: dict[str, Any] | None) -> dict[str, float] | None:
    """Pull the mid-hold refinance assumptions from the stack-level overrides
    (``debt_stack.refi_*``). Returns None when no refi year is set — i.e. the
    deal stays single-phase and nothing downstream changes."""
    if not overrides:
        return None
    year = overrides.get("refi_test_year")
    if year is None:
        return None
    try:
        year_i = int(round(float(year)))
    except (TypeError, ValueError):
        return None
    if year_i < 1:
        return None

    def _f(key: str, default: float) -> float:
        v = overrides.get(key)
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    return {
        "year": float(year_i),
        # FON-67 — LTV-based sizing is the Kimpton source-of-truth method:
        # refi proceeds = refi LTV × stabilized value (value = explicit input,
        # or stabilized NOI ÷ exit cap). When an LTV is provided it takes
        # precedence; the debt-yield / DSCR limits then act only as covenant
        # tests, not as the sizing constraint. With no LTV set we fall back to
        # the prior min(debt-yield, DSCR) sizing so existing deals are unchanged.
        "ltv": _f("refi_market_ltv_pct", 0.0),
        "stabilized_value": _f("refi_stabilized_value", 0.0),
        "stabilized_noi": _f("refi_stabilized_noi", 0.0),
        "exit_cap": _f("refi_exit_cap_rate", 0.0),
        "fee_pct": _f("refi_fee_pct", 0.0),
        "debt_yield": _f("refi_market_debt_yield_pct", 0.10),
        "dscr_min": _f("refi_market_dscr_min", 1.25),
        "rate": _f("refi_market_rate_pct", 0.068),
    }


def _compute_refi(
    refi: dict[str, float],
    schedule: list[DebtServiceYear],
    noi_by_year: list[float],
    horizon: int,
    senior_ds_by_year: list[float],
) -> tuple[list[float], float, float, int] | None:
    """Model a mid-hold refinance: size the new loan off the refi-year NOI
    (min of the debt-yield and DSCR limits), retire the senior balance, and
    return the phased debt service, the net cash-out to equity, the (interest-
    only) refi balance at exit, and the refi year. None when out of range."""
    k = int(refi["year"])
    if k < 1 or k >= horizon:  # refi must land strictly before the exit year
        return None
    noi_k = (
        noi_by_year[k - 1]
        if k - 1 < len(noi_by_year)
        else (noi_by_year[-1] if noi_by_year else 0.0)
    )
    rate = refi["rate"]
    # FON-67 — LTV sizing takes precedence when configured: refi proceeds =
    # LTV × stabilized value (value = explicit input, else stabilized NOI ÷ exit
    # cap). Debt-yield / DSCR then act as covenant tests only. Falls back to the
    # min(debt-yield, DSCR) sizing when no LTV is set (single-phase deals).
    stabilized_value = refi.get("stabilized_value", 0.0)
    if stabilized_value <= 0 and refi.get("stabilized_noi", 0.0) > 0 and refi.get("exit_cap", 0.0) > 0:
        stabilized_value = refi["stabilized_noi"] / refi["exit_cap"]
    if refi.get("ltv", 0.0) > 0 and stabilized_value > 0:
        refi_proceeds = refi["ltv"] * stabilized_value
    else:
        by_dy = noi_k / refi["debt_yield"] if refi["debt_yield"] > 0 else 0.0
        by_dscr = (
            noi_k / (refi["dscr_min"] * rate)
            if (refi["dscr_min"] > 0 and rate > 0)
            else 0.0
        )
        sizers = [x for x in (by_dy, by_dscr) if x > 0]
        refi_proceeds = min(sizers) if sizers else 0.0
    senior_balance_at_k = (
        schedule[k - 1].ending_balance if k - 1 < len(schedule) else 0.0
    )
    # Net the refi loan fee out of the cash-out to equity (source: 1.00% fee).
    refi_fee = refi_proceeds * refi.get("fee_pct", 0.0)
    refi_cash_out = max(0.0, refi_proceeds - senior_balance_at_k - refi_fee)
    refi_ds = refi_proceeds * rate  # interest-only refinance
    ds_by_year: list[float] = []
    for i in range(1, horizon + 1):
        if i <= k:
            ds_by_year.append(
                senior_ds_by_year[i - 1] if i - 1 < len(senior_ds_by_year) else 0.0
            )
        else:
            ds_by_year.append(refi_ds)
    return ds_by_year, refi_cash_out, refi_proceeds, k


class DebtEngine(BaseEngine[DebtEngineInputExt, DebtEngineOutputExt]):
    """Build the debt service schedule and DSCR / debt-yield headline metrics."""

    name = "debt"

    def run(self, payload: DebtEngineInputExt) -> DebtEngineOutputExt:
        # FON-63 — resolve the tranche stack up front. The senior (index 0)
        # drives the amortization schedule below; with no overrides the
        # resolved senior equals the deal's seed, so the schedule is
        # byte-for-byte identical to the legacy single-loan path.
        resolved_tranches = _apply_tranche_overrides(
            _build_default_tranches(payload), payload.debt_stack_overrides
        )
        senior = resolved_tranches[0]

        loan = senior.loan_amount
        annual_rate = senior.effective_rate(_SOFR_DEFAULT) or senior.fixed_rate or 0.0
        monthly_rate = annual_rate / 12.0
        if senior.interest_only:
            amort_months = 0
            io_months = (payload.term_years or 0) * 12
        else:
            amort_months = (senior.amortization_years or 0) * 12
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

        senior_annual_ds = schedule[0].debt_service if schedule else 0.0

        # FON-63 — the institutional multi-tranche view. Only tranches with a
        # positive balance (or a resolvable rate) enter the stack, so the default
        # PACE placeholder ($0 / pending) is excluded until an analyst activates
        # it — keeping every existing deal's leverage and coverage unchanged.
        active_tranches = [t for t in resolved_tranches if t.loan_amount > 0]
        debt_stack = compute_debt_stack(
            active_tranches,
            year_one_noi=payload.noi_by_year[0] if payload.noi_by_year else None,
            property_value=payload.purchase_price_usd,
            total_cost=payload.total_capital_usd,
            default_index=_SOFR_DEFAULT,
            deal_id=payload.deal_id,
        )

        # Reconcile the senior tranche's debt service to the month-by-month
        # schedule (compute_debt_stack uses an annual level payment; the schedule
        # is monthly-compounded) so the Capital Stack view and the Debt Summary
        # DSCR agree and amortizing deals see no drift.
        if debt_stack.tranches and debt_stack.tranches[0].kind == "senior":
            sr = debt_stack.tranches[0]
            if sr.annual_debt_service is not None:
                delta = senior_annual_ds - sr.annual_debt_service
                sr.annual_debt_service = senior_annual_ds
                debt_stack.total_annual_debt_service += delta
                if payload.noi_by_year and debt_stack.total_annual_debt_service > 0:
                    debt_stack.year_one_dscr = (
                        payload.noi_by_year[0]
                        / debt_stack.total_annual_debt_service
                    )

        # Headline metrics reflect the whole stack: senior debt service from the
        # accurate schedule plus any priced junior tranche (activated PACE/mezz).
        extra_ds = sum(
            t.annual_debt_service or 0.0
            for t in debt_stack.tranches
            if t.kind != "senior" and t.annual_debt_service is not None
        )
        annual_ds = senior_annual_ds + extra_ds
        total_debt = debt_stack.total_debt or loan
        year1_dscr = (
            (payload.noi_by_year[0] / annual_ds)
            if payload.noi_by_year and annual_ds > 0
            else None
        )
        year1_dy = (
            (payload.noi_by_year[0] / total_debt)
            if payload.noi_by_year and total_debt > 0
            else None
        )
        dscrs = [yr.dscr for yr in schedule if yr.dscr is not None]
        avg_dscr = sum(dscrs) / len(dscrs) if dscrs else None

        # FON-67 — model a mid-hold refinance when the analyst sets a refi year.
        # It only produces the returns-facing phased DS series, exit balance and
        # cash-out; every headline metric above (Year-1 DSCR, debt yield, the
        # senior schedule) is unchanged, and a deal with no refi year is
        # byte-for-byte the single-phase model.
        refi = _refi_params(payload.debt_stack_overrides)
        horizon = len(payload.noi_by_year)
        debt_service_by_year: list[float] = []
        refi_cash_out = 0.0
        refi_year_out: int | None = None
        balance_at_exit_out: float | None = (
            schedule[-1].ending_balance if schedule else None
        )
        if refi is not None and horizon > 0:
            senior_ds_by_year = [
                schedule[i].debt_service if i < len(schedule) else senior_annual_ds
                for i in range(horizon)
            ]
            computed = _compute_refi(
                refi, schedule, payload.noi_by_year, horizon, senior_ds_by_year
            )
            if computed is not None:
                (
                    debt_service_by_year,
                    refi_cash_out,
                    balance_at_exit_out,
                    refi_year_out,
                ) = computed

        return DebtEngineOutputExt(
            deal_id=payload.deal_id,
            annual_debt_service=annual_ds,
            schedule=schedule,
            avg_dscr=avg_dscr,
            loan_amount=total_debt,
            monthly_schedule=monthly_schedule,
            year_one_dscr=year1_dscr,
            year_one_debt_yield=year1_dy,
            interest_rate=payload.interest_rate,
            term_years=payload.term_years,
            amortization_years=payload.amortization_years,
            debt_stack=debt_stack,
            debt_service_by_year=debt_service_by_year,
            refi_cash_out=refi_cash_out,
            refi_year=refi_year_out,
            balance_at_exit=balance_at_exit_out,
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
