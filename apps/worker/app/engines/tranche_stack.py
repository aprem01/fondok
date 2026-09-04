"""Institutional multi-tranche debt stack (FON-63).

The Wave 4 ``DebtStack`` schema was fixed-rate, senior/mezz/pref only, and
required every term up front. Institutional hotel debt (the Kimpton Angler
reference) needs more: floating rate priced off an index with a floor and cap,
PACE and other tranche kinds, interest-only structures, and tranches whose terms
are deliberately "not specified" yet (the PACE piece), which must still count
toward leverage without inventing a debt service.

This module is the deterministic heart of the redesign — pure Python, no I/O.
The Debt tab and engine_runner wiring build on top of it. Every figure here is
computed from the tranche inputs; nothing is inferred for a tranche whose terms
are pending.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

TrancheKind = Literal["senior", "pace", "mezzanine", "other"]
RateType = Literal["fixed", "floating"]


class LoanTranche(BaseModel):
    """One slice of the capital stack. Terms may be partially specified — a
    tranche with ``terms_pending`` (or no resolvable rate) contributes to total
    debt / LTV / debt yield but is excluded from debt-service math until the
    user fills its terms in the Debt tab."""

    model_config = ConfigDict(extra="forbid")

    kind: TrancheKind = "senior"
    label: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    loan_amount: Annotated[float, Field(ge=0)]

    rate_type: RateType = "fixed"
    fixed_rate: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    # Floating: all-in = spread + clamp(index, floor, cap).
    index_name: str | None = None  # e.g. "SOFR"
    index_assumption: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    spread: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    rate_floor: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    rate_cap: Annotated[float, Field(ge=0.0, le=1.0)] | None = None

    interest_only: bool = True
    amortization_years: Annotated[int, Field(ge=0, le=40)] | None = None
    term_years: Annotated[int, Field(ge=0, le=40)] | None = None

    origination_fee_pct: Annotated[float, Field(ge=0.0, le=10.0)] = 0.0
    exit_fee_pct: Annotated[float, Field(ge=0.0, le=10.0)] = 0.0

    # Set when the tranche's economics are intentionally unresolved (the PACE
    # piece in the Kimpton template). Also inferred when no rate can be derived.
    terms_pending: bool = False

    def effective_rate(self, default_index: float) -> float | None:
        """Resolve the all-in annual rate, or None when it can't be computed."""
        if self.terms_pending:
            return None
        if self.rate_type == "fixed":
            return self.fixed_rate
        # Floating.
        if self.spread is None:
            return None
        idx = self.index_assumption if self.index_assumption is not None else default_index
        if self.rate_floor is not None:
            idx = max(idx, self.rate_floor)
        if self.rate_cap is not None:
            idx = min(idx, self.rate_cap)
        return self.spread + idx


class DebtCovenants(BaseModel):
    """Covenant package carried alongside the stack. Stored + displayed now;
    live testing (DSCR/debt-yield tests by date, cash-trap trigger, extension
    gating) is the Phase-2 follow-on."""

    model_config = ConfigDict(extra="forbid")

    max_ltv: float | None = None
    min_debt_yield: float | None = None
    min_dscr: float | None = None
    combined_min_dscr: float | None = None  # e.g. Senior + PACE 1.25x
    cash_trap: bool | None = None
    notes: list[str] = Field(default_factory=list)


class TrancheResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: TrancheKind
    label: str
    loan_amount: float
    all_in_rate: float | None
    rate_type: RateType
    annual_debt_service: float | None
    interest_only: bool
    terms_pending: bool
    # FON-63 follow-up — surface the amortization so the Debt tab can render
    # and edit it (None / interest-only shows as "IO").
    amortization_years: int | None = None
    # FON-72 follow-up — the floating-rate build-up echoed straight from the
    # INPUT tranche (index name, index rate assumption, spread) so the Debt tab's
    # Loan Terms card can render Benchmark → Spread → All-In. A fixed-rate tranche
    # carries none of these (all None) and the tab renders those rows "—". These
    # are pure pass-throughs — nothing in the rate/DSCR math reads them.
    benchmark_name: str | None = None
    benchmark_rate: float | None = None
    spread: float | None = None


class DebtStackResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID | None = None
    tranches: list[TrancheResult] = Field(default_factory=list)
    total_debt: float = 0.0
    priced_debt: float = 0.0  # debt with a computable rate
    total_annual_debt_service: float = 0.0
    weighted_avg_rate: float | None = None
    year_one_dscr: float | None = None
    ltv: float | None = None
    debt_yield: float | None = None
    ltc: float | None = None
    covenants: DebtCovenants | None = None
    warnings: list[str] = Field(default_factory=list)


def _annual_debt_service(tranche: LoanTranche, rate: float) -> float:
    """IO tranches pay interest only; amortizing tranches use a level P&I PMT."""
    if tranche.interest_only or not tranche.amortization_years:
        return tranche.loan_amount * rate
    n = tranche.amortization_years
    if rate <= 0:
        return tranche.loan_amount / n
    # Annual level payment (amortization in years).
    factor = (1 + rate) ** n
    return tranche.loan_amount * rate * factor / (factor - 1)


def compute_debt_stack(
    tranches: list[LoanTranche],
    *,
    year_one_noi: float | None,
    property_value: float | None,
    total_cost: float | None,
    default_index: float = 0.0,
    covenants: DebtCovenants | None = None,
    deal_id: UUID | None = None,
) -> DebtStackResult:
    """Roll a list of tranches into consolidated leverage + coverage metrics.

    Pending-terms tranches count toward total debt (and therefore LTV / debt
    yield / LTC) but are left out of debt service and DSCR — with a warning —
    rather than fabricating a rate.
    """
    results: list[TrancheResult] = []
    warnings: list[str] = []
    total_debt = 0.0
    priced_debt = 0.0
    total_ds = 0.0
    rate_weight = 0.0  # Σ (rate × amount) over priced tranches, for the blended rate.

    for t in tranches:
        rate = t.effective_rate(default_index)
        label = t.label or t.kind.replace("_", " ").title()
        total_debt += t.loan_amount
        if rate is None:
            warnings.append(f"{label}: terms not specified — excluded from debt service.")
            results.append(TrancheResult(
                kind=t.kind, label=label, loan_amount=t.loan_amount, all_in_rate=None,
                rate_type=t.rate_type, annual_debt_service=None,
                interest_only=t.interest_only, terms_pending=True,
                amortization_years=t.amortization_years,
                benchmark_name=t.index_name, benchmark_rate=t.index_assumption,
                spread=t.spread,
            ))
            continue
        ds = _annual_debt_service(t, rate)
        priced_debt += t.loan_amount
        total_ds += ds
        rate_weight += rate * t.loan_amount
        results.append(TrancheResult(
            kind=t.kind, label=label, loan_amount=t.loan_amount, all_in_rate=rate,
            rate_type=t.rate_type, annual_debt_service=ds,
            interest_only=t.interest_only, terms_pending=False,
            amortization_years=t.amortization_years,
            benchmark_name=t.index_name, benchmark_rate=t.index_assumption,
            spread=t.spread,
        ))

    weighted_avg_rate = rate_weight / priced_debt if priced_debt > 0 else None
    dscr = (year_one_noi / total_ds) if (year_one_noi and total_ds > 0) else None
    ltv = (total_debt / property_value) if (property_value and property_value > 0) else None
    debt_yield = (year_one_noi / total_debt) if (year_one_noi and total_debt > 0) else None
    ltc = (total_debt / total_cost) if (total_cost and total_cost > 0) else None

    if ltc is not None and ltc > 1.0:
        warnings.append(
            f"Total debt (${total_debt/1e6:.1f}M) exceeds total cost — LTC is "
            f"{ltc*100:.0f}%. Check the tranche amounts against the deal basis."
        )
    if covenants and covenants.max_ltv is not None and ltv is not None and ltv > covenants.max_ltv:
        warnings.append(
            f"LTV {ltv*100:.0f}% breaches the {covenants.max_ltv*100:.0f}% covenant."
        )

    return DebtStackResult(
        deal_id=deal_id,
        tranches=results,
        total_debt=total_debt,
        priced_debt=priced_debt,
        total_annual_debt_service=total_ds,
        weighted_avg_rate=weighted_avg_rate,
        year_one_dscr=dscr,
        ltv=ltv,
        debt_yield=debt_yield,
        ltc=ltc,
        covenants=covenants,
        warnings=warnings,
    )


def kimpton_reference_stack() -> tuple[list[LoanTranche], DebtCovenants]:
    """The Senior + PACE stack from Sam's QA debt template
    (Kimpton_Fondok_Debt_Upload_Template.xlsx). PACE terms are intentionally
    left pending — the template says Fondok must not invent them."""
    senior = LoanTranche(
        kind="senior", label="Senior Loan", loan_amount=35_000_000,
        rate_type="floating", index_name="SOFR", spread=0.05,
        rate_floor=0.03, rate_cap=0.06, index_assumption=0.03,  # SOFR at the floor
        interest_only=True, term_years=3,
    )
    pace = LoanTranche(
        kind="pace", label="PACE Loan", loan_amount=30_000_000,
        terms_pending=True,
    )
    covenants = DebtCovenants(
        max_ltv=0.65, min_debt_yield=0.10, min_dscr=1.0, combined_min_dscr=1.25,
        cash_trap=True,
        notes=[
            "Debt yield step-up 10% → 11% by 12/31/29",
            "DSCR step-up 1.0x → 1.2x by 12/31/29",
            "Senior + PACE combined DSCR 1.25x",
            "2 x 1-year extension options",
        ],
    )
    return [senior, pace], covenants


__all__ = [
    "LoanTranche",
    "DebtCovenants",
    "TrancheResult",
    "DebtStackResult",
    "compute_debt_stack",
    "kimpton_reference_stack",
]
