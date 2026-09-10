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

from fondok_schemas.provenance import ValueInput, ValueTrace, apply_states
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
    # FON-71 follow-up — renovation contingency as a percent of the renovation
    # HARD costs (the construction-risk base). When > 0 the contingency dollars
    # fold into the renovation total (base budget + contingency), so the
    # "Renovation" use line — and therefore total cost / LTC / equity / returns
    # — reflect it. Default 0.0 keeps every existing number byte-identical.
    renovation_contingency_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0


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
    # FON-71 follow-up — renovation contingency fold-in. ``renovation_base_usd``
    # is the analyst's renovation budget; ``renovation_contingency_usd`` =
    # renovation_contingency_pct × hard costs; ``renovation_total_usd`` = base +
    # contingency (== the "Renovation" use line). With no contingency the total
    # equals the base and the pct/usd are 0 → byte-identical to before.
    renovation_base_usd: Annotated[float, Field(ge=0)] = 0.0
    renovation_contingency_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    renovation_contingency_usd: Annotated[float, Field(ge=0)] = 0.0
    renovation_total_usd: Annotated[float, Field(ge=0)] = 0.0
    # FON-67 — the property/capital uses (Total Uses minus financing costs)
    # and the senior loan fee, surfaced separately so the source-model
    # convention (property uses kept apart from financing) is legible.
    property_uses_usd: Annotated[float, Field(ge=0)] = 0.0
    senior_loan_fee_usd: Annotated[float, Field(ge=0)] = 0.0
    # FON-25/65 — per-value provenance sidecar (see provenance.py). Keyed by
    # dotted output path (e.g. "debt_amount", "uses[0].amount") → ValueTrace
    # with a derived ``state`` (calculated for the sized/derived figures,
    # assumption for analyst-provided inputs). Empty by default.
    provenance: dict[str, ValueTrace] = Field(default_factory=dict)


# Which Sources & Uses labels are raw analyst inputs, and the canonical
# ``__sources__`` key each one IS. Asserting the key on the trace is what lets
# a Sources & Uses line be walked back to its assumption badge (and on to the
# deal record / override / document behind it) instead of dead-ending on a
# display label no consumer can match against the assumption vocabulary — the
# line reads "Renovation", the assumption is ``renovation_budget``.
_USE_LABEL_ASSUMPTIONS: dict[str, str] = {
    "Purchase Price": "purchase_price",
    "Renovation": "renovation_budget",
    "Working Capital": "working_capital",
    "Insurance Reserve": "insurance_reserve",
    "Soft Costs": "soft_costs",
    "Contingency": "contingency",
}


def _property_line_input(line: SourceUseLine, reno_contingency: float) -> ValueInput:
    """One ``property_uses`` summand, linked to what it actually came from.

    A Renovation line that already folded in a contingency is no longer the
    ``renovation_budget`` assumption — it is this engine's own
    ``renovation_total_usd``, so it points there instead of claiming a key it
    is not. Closing Costs is likewise a calculation, not an assumption, and
    gets neither.
    """
    if line.label == "Renovation" and reno_contingency > 0:
        return ValueInput(
            name=line.label, value=line.amount, traces_to="renovation_total_usd"
        )
    return ValueInput(
        name=line.label,
        value=line.amount,
        assumption_key=_USE_LABEL_ASSUMPTIONS.get(line.label),
    )


class CapitalEngine(BaseEngine[CapitalEngineInput, CapitalEngineOutput]):
    """Build sources & uses; size senior debt at LTV x cost basis."""

    name = "capital"

    def run(self, payload: CapitalEngineInput) -> CapitalEngineOutput:
        # Closing costs may have been provided absolutely; otherwise apply pct.
        closing_costs = payload.closing_costs or payload.purchase_price * payload.closing_costs_pct

        # FON-71 follow-up — renovation contingency (percent of hard costs)
        # folds into the renovation total. Contingency 0 → total == base ==
        # renovation_budget, so the Renovation use line (and everything
        # downstream) is byte-identical to before.
        reno_base = payload.renovation_budget
        reno_hard_costs = reno_base * payload.renovation_hard_pct
        reno_contingency = payload.renovation_contingency_pct * reno_hard_costs
        reno_total = reno_base + reno_contingency

        cost_basis = payload.purchase_price + closing_costs + reno_total
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
            # Renovation appears once, as the TOTAL PIP budget incl. contingency.
            SourceUseLine(label="Renovation", amount=reno_total),
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

        price_per_key = payload.purchase_price / payload.keys

        # ─── FON-25/65 provenance sidecar for the Investment / Sources & Uses
        # tab. Analyst-input capital lines read as ``assumption``; the sized and
        # derived figures (debt, equity, LTC, totals, fee, renovation split)
        # read as ``calculated``. Metadata only — no computed number changes. ───
        prov: dict[str, ValueTrace] = {}
        closing_provided = payload.closing_costs > 0
        senior_explicit = (
            payload.senior_loan_amount is not None and payload.senior_loan_amount > 0
        )
        _input_use_labels = set(_USE_LABEL_ASSUMPTIONS)

        # Sources & Uses table rows — one trace per rendered line.
        for i, line in enumerate(uses_lines):
            key = f"uses[{i}].amount"
            if line.label == "Closing Costs" and not closing_provided:
                prov[key] = ValueTrace(
                    value=line.amount,
                    formula="closing_costs = purchase_price × closing_costs_pct",
                    inputs=[
                        ValueInput(
                            name="purchase_price",
                            value=payload.purchase_price,
                            assumption_key="purchase_price",
                        ),
                        ValueInput(
                            name="closing_costs_pct",
                            value=payload.closing_costs_pct,
                            assumption_key="closing_costs_pct",
                        ),
                    ],
                )
            elif line.label == "Senior Loan Fee":
                prov[key] = ValueTrace(
                    value=line.amount,
                    formula="senior_loan_fee = senior_debt × loan_costs_pct",
                    inputs=[
                        ValueInput(name="senior_debt", value=debt, traces_to="debt_amount"),
                        ValueInput(
                            name="loan_costs_pct",
                            value=payload.loan_costs_pct,
                            assumption_key="loan_costs_pct",
                        ),
                    ],
                    note="Financing cost funded at close, kept apart from property uses.",
                )
            elif line.label == "Total Uses":
                prov[key] = ValueTrace(
                    value=line.amount,
                    formula="total_uses = property_uses + senior_loan_fee",
                    inputs=[
                        ValueInput(name="property_uses", value=property_uses, traces_to="property_uses_usd"),
                        ValueInput(name="senior_loan_fee", value=senior_loan_fee, traces_to="senior_loan_fee_usd"),
                    ],
                )
            elif line.label == "Renovation" and reno_contingency > 0:
                prov[key] = ValueTrace(
                    value=line.amount,
                    formula="renovation_total = renovation_budget + renovation_contingency",
                    inputs=[
                        ValueInput(
                            name="renovation_budget",
                            value=reno_base,
                            assumption_key="renovation_budget",
                        ),
                        ValueInput(
                            name="renovation_contingency",
                            value=reno_contingency,
                            traces_to="renovation_contingency_usd",
                        ),
                    ],
                    note="Base PIP budget plus contingency (pct × renovation hard costs).",
                )
            elif line.label in _input_use_labels:
                # A raw analyst input line — the value IS the assumption, so the
                # key lands on the trace (not on a self-referential input) and
                # the FON-65 badge stays ``assumption``.
                prov[key] = ValueTrace(
                    value=line.amount,
                    assumption_key=_USE_LABEL_ASSUMPTIONS[line.label],
                    note="Analyst-provided capital use.",
                )
            else:  # Closing Costs provided absolutely, or any other input line
                prov[key] = ValueTrace(
                    value=line.amount,
                    note="Analyst-provided capital use.",
                )

        # Sources rows.
        prov["sources[0].amount"] = (
            ValueTrace(
                value=debt,
                assumption_key="senior_loan_amount",
                note="Analyst-confirmed senior loan amount (source-model reconciliation).",
            )
            if senior_explicit
            else ValueTrace(
                value=debt,
                formula=f"senior_debt = {payload.debt_basis}_basis × ltv",
                inputs=[
                    ValueInput(
                        name=f"{payload.debt_basis}_basis",
                        value=basis,
                        # ``purchase`` basis IS the purchase-price assumption;
                        # a ``cost`` basis is purchase + closing + renovation,
                        # which no single assumption owns.
                        assumption_key=(
                            "purchase_price"
                            if payload.debt_basis == "purchase"
                            else None
                        ),
                    ),
                    ValueInput(name="ltv", value=payload.ltv, assumption_key="ltv"),
                ],
            )
        )
        prov["sources[1].amount"] = ValueTrace(
            value=equity,
            formula="equity = total_uses − senior_debt",
            inputs=[
                ValueInput(name="total_uses", value=total_uses, traces_to="uses[{}].amount".format(len(uses_lines) - 1)),
                ValueInput(name="senior_debt", value=debt, traces_to="sources[0].amount"),
            ],
        )
        prov["sources[2].amount"] = ValueTrace(
            value=total_uses,
            formula="total_sources = senior_debt + equity",
            inputs=[
                ValueInput(name="senior_debt", value=debt, traces_to="sources[0].amount"),
                ValueInput(name="equity", value=equity, traces_to="sources[1].amount"),
            ],
        )

        # Headline scalar figures the Investment tab surfaces above the table.
        prov["purchase_price"] = ValueTrace(
            value=payload.purchase_price,
            assumption_key="purchase_price",
            note="Analyst / OM purchase price — the deal's capital anchor.",
        )
        prov["price_per_key"] = ValueTrace(
            value=price_per_key,
            formula="price_per_key = purchase_price ÷ keys",
            inputs=[
                ValueInput(
                    name="purchase_price",
                    value=payload.purchase_price,
                    assumption_key="purchase_price",
                ),
                ValueInput(
                    name="keys", value=float(payload.keys), assumption_key="keys"
                ),
            ],
        )
        prov["debt_amount"] = (
            ValueTrace(
                value=debt,
                assumption_key="senior_loan_amount",
                note="Analyst-confirmed senior loan amount (source-model reconciliation).",
            )
            if senior_explicit
            else ValueTrace(
                value=debt,
                formula=f"debt_amount = {payload.debt_basis}_basis × ltv",
                inputs=[
                    ValueInput(
                        name=f"{payload.debt_basis}_basis",
                        value=basis,
                        # ``purchase`` basis IS the purchase-price assumption;
                        # a ``cost`` basis is purchase + closing + renovation,
                        # which no single assumption owns.
                        assumption_key=(
                            "purchase_price"
                            if payload.debt_basis == "purchase"
                            else None
                        ),
                    ),
                    ValueInput(name="ltv", value=payload.ltv, assumption_key="ltv"),
                ],
            )
        )
        prov["equity_amount"] = ValueTrace(
            value=equity,
            formula="equity_amount = total_uses − debt_amount",
            inputs=[
                ValueInput(name="total_uses", value=total_uses, traces_to="total_capital"),
                ValueInput(name="debt_amount", value=debt, traces_to="debt_amount"),
            ],
        )
        prov["ltc"] = ValueTrace(
            value=debt / total_uses if total_uses else 0.0,
            formula="ltc = debt_amount ÷ total_uses",
            inputs=[
                ValueInput(name="debt_amount", value=debt, traces_to="debt_amount"),
                ValueInput(name="total_uses", value=total_uses, traces_to="total_capital"),
            ],
            note="Loan-to-cost on the fully-loaded capitalization.",
        )
        prov["total_capital"] = ValueTrace(
            value=total_uses,
            formula="total_capital = property_uses + senior_loan_fee",
            inputs=[
                ValueInput(name="property_uses", value=property_uses, traces_to="property_uses_usd"),
                ValueInput(name="senior_loan_fee", value=senior_loan_fee, traces_to="senior_loan_fee_usd"),
            ],
        )
        prov["property_uses_usd"] = ValueTrace(
            value=property_uses,
            formula="property_uses = Σ property capital lines",
            # Each summed line names the assumption it IS. Without this the
            # chain died here: a lineage walk cannot match a display label
            # ("Working Capital") against the assumption vocabulary, so
            # Total Uses → Equity → Levered IRR had no path to the deal's
            # purchase price, renovation budget or reserves at all.
            inputs=[_property_line_input(line, reno_contingency) for line in property_lines],
            note=(
                "Purchase + closing + renovation + reserves, excl. financing fee. "
                "Closing Costs is itself a calculation (purchase_price × "
                "closing_costs_pct), so it names no single assumption here."
            ),
        )
        prov["senior_loan_fee_usd"] = ValueTrace(
            value=senior_loan_fee,
            formula="senior_loan_fee = debt_amount × loan_costs_pct",
            inputs=[
                ValueInput(name="debt_amount", value=debt, traces_to="debt_amount"),
                ValueInput(
                    name="loan_costs_pct",
                    value=payload.loan_costs_pct,
                    assumption_key="loan_costs_pct",
                ),
            ],
        )
        if reno_breakdown is not None:
            for part, pct in (
                ("hard", payload.renovation_hard_pct),
                ("soft", payload.renovation_soft_pct),
                ("fees", payload.renovation_fees_pct),
            ):
                prov[f"renovation_breakdown.{part}"] = ValueTrace(
                    value=getattr(reno_breakdown, part),
                    formula=f"renovation_{part} = renovation_budget × renovation_{part}_pct",
                    inputs=[
                        ValueInput(
                            name="renovation_budget",
                            value=payload.renovation_budget,
                            assumption_key="renovation_budget",
                        ),
                        ValueInput(
                            name=f"renovation_{part}_pct",
                            value=pct,
                            assumption_key=f"renovation_{part}_pct",
                        ),
                    ],
                )
        # FON-71 follow-up — contingency provenance (only when set, so a deal
        # with no contingency keeps a byte-identical provenance map).
        if reno_contingency > 0:
            prov["renovation_contingency_usd"] = ValueTrace(
                value=reno_contingency,
                formula="renovation_contingency = renovation_contingency_pct × renovation_hard_costs",
                inputs=[
                    ValueInput(
                        name="renovation_contingency_pct",
                        value=payload.renovation_contingency_pct,
                        assumption_key="renovation_contingency_pct",
                    ),
                    # hard costs = renovation_budget × renovation_hard_pct —
                    # two assumptions, so neither is claimed here.
                    ValueInput(name="renovation_hard_costs", value=reno_hard_costs),
                ],
                note=(
                    "Contingency on the renovation hard costs. renovation_hard_costs "
                    "is itself renovation_budget × renovation_hard_pct, so it names "
                    "no single assumption."
                ),
            )
            prov["renovation_total_usd"] = ValueTrace(
                value=reno_total,
                formula="renovation_total = renovation_budget + renovation_contingency",
                inputs=[
                    ValueInput(
                        name="renovation_budget",
                        value=reno_base,
                        assumption_key="renovation_budget",
                    ),
                    ValueInput(name="renovation_contingency", value=reno_contingency, traces_to="renovation_contingency_usd"),
                ],
            )

        return CapitalEngineOutput(
            deal_id=payload.deal_id,
            total_capital=total_uses,
            price_per_key=price_per_key,
            sources=sources_lines,
            uses=uses_lines,
            debt_amount=debt,
            equity_amount=equity,
            ltc=debt / total_uses if total_uses else 0.0,
            renovation_breakdown=reno_breakdown,
            renovation_base_usd=reno_base,
            renovation_contingency_pct=payload.renovation_contingency_pct,
            renovation_contingency_usd=reno_contingency,
            renovation_total_usd=reno_total,
            property_uses_usd=property_uses,
            senior_loan_fee_usd=senior_loan_fee,
            provenance=apply_states(prov),
        )


__all__ = [
    "CapitalEngine",
    "CapitalEngineInput",
    "CapitalEngineOutput",
    "RenovationBreakdown",
]
