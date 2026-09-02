"""Capital engine — sources & uses, PIP, capex schedule.

Builds the deal-level capital stack from the underwriter's inputs.

By default debt is sized at LTV against purchase price only (the typical
hotel acquisition convention). Set ``debt_basis = "cost"`` to size against
purchase + closing + renovation (LTC convention). An explicit
``senior_loan_amount`` (FON-67) overrides LTV sizing so the stack reconciles
to a source model's confirmed senior figure.

    Property uses = Purchase + Closing + Renovation + Working Capital
                    + Insurance Reserve + Soft Costs + Contingency
    Debt          = senior_loan_amount, else LTV * basis
    Senior fee    = Debt * loan_costs_pct   (financing cost at close)
    Total Uses    = Property uses + Senior fee
    Equity        = Total Uses - Debt

Financing costs (the senior fee here, plus the refi fee modeled in the debt
engine) are kept separate from the property uses per the source-model
convention, but still fund at close, so the levered IRR is unchanged versus
carrying the fee inside the property uses.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fondok_schemas.underwriting import (
    InvestmentEngineInput,
    InvestmentEngineOutput,
    SourceUseLine,
)

from .base import BaseEngine


class CapitalEngineInput(InvestmentEngineInput):
    """Investment input plus financing assumptions used to size sources."""

    model_config = ConfigDict(extra="forbid")

    ltv: Annotated[float, Field(ge=0.0, le=1.0)] = 0.65
    closing_costs_pct: Annotated[float, Field(ge=0.0, le=0.10)] = 0.02
    loan_costs_pct: Annotated[float, Field(ge=0.0, le=0.05)] = 0.015
    debt_basis: Literal["purchase", "cost"] = "purchase"
    # FON-67 — an explicit senior loan amount reconciles the stack to a source
    # model's confirmed senior figure. When set (> 0) it wins over LTV sizing;
    # None keeps the LTV-derived debt so existing deals are unchanged.
    senior_loan_amount: Annotated[float, Field(ge=0)] | None = None
    # FON-67 — an insurance / operating reserve funded at close, a property use
    # alongside working capital (0 unless the analyst provides it).
    insurance_reserve: Annotated[float, Field(ge=0)] = 0.0

    # Renovation cost split (FON-71). The renovation budget is one line in
    # the sources & uses, but the Investment tab breaks it into hard costs,
    # soft costs, and professional fees. These are configurable percentages
    # (industry-standard 75/20/5 default), applied uniformly — not a per-deal
    # fixture. A future doc-driven breakdown can override these when a
    # renovation budget with itemized costs is extracted.
    renovation_hard_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.75
    renovation_soft_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.15
    renovation_fees_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10


class RenovationBreakdown(BaseModel):
    """Hard / soft / professional-fee split of the renovation budget."""

    model_config = ConfigDict(extra="forbid")

    hard: Annotated[float, Field(ge=0)]
    soft: Annotated[float, Field(ge=0)]
    fees: Annotated[float, Field(ge=0)]


class CapitalEngineOutput(InvestmentEngineOutput):
    """Investment output enriched with debt/equity split."""

    model_config = ConfigDict(extra="forbid")

    debt_amount: Annotated[float, Field(ge=0)]
    equity_amount: Annotated[float, Field(ge=0)]
    ltc: Annotated[float, Field(ge=0.0, le=1.5)]
    # None when there is no renovation budget (so the UI shows '—' rather
    # than a fabricated $0 split).
    renovation_breakdown: RenovationBreakdown | None = None
    # FON-67 — the property/capital uses (Total Uses minus financing costs)
    # and the senior loan fee, surfaced separately so the source-model
    # convention (property uses kept apart from financing) is legible.
    property_uses_usd: Annotated[float, Field(ge=0)] = 0.0
    senior_loan_fee_usd: Annotated[float, Field(ge=0)] = 0.0


class CapitalEngine(BaseEngine[CapitalEngineInput, CapitalEngineOutput]):
    """Build sources & uses; size senior debt at LTV x cost basis."""

    name = "capital"

    def run(self, payload: CapitalEngineInput) -> CapitalEngineOutput:
        # Closing costs may have been provided absolutely; otherwise apply pct.
        closing_costs = payload.closing_costs or payload.purchase_price * payload.closing_costs_pct

        cost_basis = payload.purchase_price + closing_costs + payload.renovation_budget
        basis = payload.purchase_price if payload.debt_basis == "purchase" else cost_basis
        # FON-67 — explicit senior loan wins over LTV sizing when provided, so
        # the stack reconciles to a source model's confirmed senior amount.
        if payload.senior_loan_amount is not None and payload.senior_loan_amount > 0:
            debt = float(payload.senior_loan_amount)
        else:
            debt = basis * payload.ltv
        # Senior loan fee — a financing cost funded at close (levered outflow at
        # t=0), kept separate from the property uses per the source-model
        # convention. It still adds to equity, so the levered IRR is unchanged
        # vs. carrying it as a "Loan Costs" use.
        senior_loan_fee = debt * payload.loan_costs_pct

        property_lines = [
            SourceUseLine(label="Purchase Price", amount=payload.purchase_price),
            SourceUseLine(label="Closing Costs", amount=closing_costs),
            SourceUseLine(label="Renovation", amount=payload.renovation_budget),
            SourceUseLine(label="Working Capital", amount=payload.working_capital),
            SourceUseLine(label="Insurance Reserve", amount=payload.insurance_reserve),
            SourceUseLine(label="Soft Costs", amount=payload.soft_costs),
            SourceUseLine(label="Contingency", amount=payload.contingency),
        ]
        property_lines = [u for u in property_lines if u.amount > 0]
        property_uses = sum(u.amount for u in property_lines)

        # Total capitalization = property uses + financing fee (unchanged
        # meaning of ``total_capital``); equity funds the gap plus the fee.
        total_uses = property_uses + senior_loan_fee
        equity = total_uses - debt

        uses_lines = list(property_lines)
        if senior_loan_fee > 0:
            uses_lines.append(
                SourceUseLine(label="Senior Loan Fee", amount=senior_loan_fee)
            )

        # Break the renovation budget into hard / soft / professional fees.
        # Only when there's an actual budget — otherwise leave it None so the
        # UI renders '—' rather than a $0 split.
        reno_breakdown: RenovationBreakdown | None = None
        if payload.renovation_budget > 0:
            reno_breakdown = RenovationBreakdown(
                hard=payload.renovation_budget * payload.renovation_hard_pct,
                soft=payload.renovation_budget * payload.renovation_soft_pct,
                fees=payload.renovation_budget * payload.renovation_fees_pct,
            )

        # Stamp pct on each line and append totals.
        for line in uses_lines:
            line.pct = line.amount / total_uses if total_uses else None
        uses_lines.append(
            SourceUseLine(label="Total Uses", amount=total_uses, pct=1.0, is_total=True)
        )

        sources_lines = [
            SourceUseLine(
                label="Senior Debt",
                amount=debt,
                pct=debt / total_uses if total_uses else None,
            ),
            SourceUseLine(
                label="Equity",
                amount=equity,
                pct=equity / total_uses if total_uses else None,
            ),
            SourceUseLine(
                label="Total Sources", amount=total_uses, pct=1.0, is_total=True
            ),
        ]

        return CapitalEngineOutput(
            deal_id=payload.deal_id,
            total_capital=total_uses,
            price_per_key=payload.purchase_price / payload.keys,
            sources=sources_lines,
            uses=uses_lines,
            debt_amount=debt,
            equity_amount=equity,
            ltc=debt / total_uses if total_uses else 0.0,
            renovation_breakdown=reno_breakdown,
            property_uses_usd=property_uses,
            senior_loan_fee_usd=senior_loan_fee,
        )


__all__ = [
    "CapitalEngine",
    "CapitalEngineInput",
    "CapitalEngineOutput",
    "RenovationBreakdown",
]
