"""Debt engine — loan amortization, debt service coverage, refinance test.

No external deps: PMT and amortization schedule are computed in pure Python.
Supports an interest-only stub period followed by P&I amortization.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
    """Debt output enriched with DSCR, debt-yield and a monthly schedule."""

    model_config = ConfigDict(extra="forbid")

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
            monthly_schedule=monthly_schedule,
            year_one_dscr=year1_dscr,
            year_one_debt_yield=year1_dy,
        )


__all__ = [
    "DebtEngine",
    "DebtEngineInputExt",
    "DebtEngineOutputExt",
    "DebtMonth",
    "pmt",
]
