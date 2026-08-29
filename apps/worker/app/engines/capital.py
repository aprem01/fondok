"""Capital engine — sources & uses, PIP, capex schedule.

Builds the deal-level capital stack from the underwriter's inputs.

By default debt is sized at LTV against purchase price only (the typical
hotel acquisition convention). Set ``debt_basis = "cost"`` to size against
purchase + closing + renovation (LTC convention).

    Uses    = Purchase + Closing + Renovation + Working Capital
              + Soft Costs + Contingency + Loan Costs
    Debt    = LTV * basis
    Equity  = Total Uses - Debt
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

    # Renovation cost split (FON-71). The renovation budget is one line in
    # the sources & uses, but the Investment tab breaks it into hard costs,
    # soft costs, and professional fees. These are configurable percentages
    # (industry-standard 75/20/5 default), applied uniformly — not a per-deal
    # fixture. A future doc-driven breakdown can override these when a
    # renovation budget with itemized costs is extracted.
    renovation_hard_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.75
    renovation_soft_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.20
    renovation_fees_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.05


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


class CapitalEngine(BaseEngine[CapitalEngineInput, CapitalEngineOutput]):
    """Build sources & uses; size senior debt at LTV x cost basis."""

    name = "capital"

    def run(self, payload: CapitalEngineInput) -> CapitalEngineOutput:
        # Closing costs may have been provided absolutely; otherwise apply pct.
        closing_costs = payload.closing_costs or payload.purchase_price * payload.closing_costs_pct

        cost_basis = payload.purchase_price + closing_costs + payload.renovation_budget
        basis = payload.purchase_price if payload.debt_basis == "purchase" else cost_basis
        debt = basis * payload.ltv
        loan_costs = debt * payload.loan_costs_pct

        uses_lines = [
            SourceUseLine(label="Purchase Price", amount=payload.purchase_price),
            SourceUseLine(label="Closing Costs", amount=closing_costs),
            SourceUseLine(label="Renovation", amount=payload.renovation_budget),
            SourceUseLine(label="Working Capital", amount=payload.working_capital),
            SourceUseLine(label="Soft Costs", amount=payload.soft_costs),
            SourceUseLine(label="Contingency", amount=payload.contingency),
            SourceUseLine(label="Loan Costs", amount=loan_costs),
        ]
        uses_lines = [u for u in uses_lines if u.amount > 0]

        total_uses = sum(u.amount for u in uses_lines)
        equity = total_uses - debt

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
        )


__all__ = [
    "CapitalEngine",
    "CapitalEngineInput",
    "CapitalEngineOutput",
    "RenovationBreakdown",
]
