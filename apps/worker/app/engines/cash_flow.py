"""Cash Flow Statement engine (Move 2) — a *composed view*, not new math.

This engine relocates what the Cash Flow tab used to assemble ad hoc in the
browser (``CashFlowTab.buildCashFlowFromWorker`` + the Distributions section)
into one reconciled server output. It composes the levered/unlevered
statement and the distribution waterfall out of the canonical engine outputs:

    capital   — sources & uses funded at close (acquisition uses, debt proceeds)
    expense   — NOI + FF&E reserve by year
    debt      — interest / principal / refinance / exit payoff
    returns   — gross sale, selling costs, and the CANONICAL cash-flow series
    partnership — LP / GP distributions + promote

The one rule that makes this safe: **every bottom line ties out to the returns
engine to the cent.** ``returns.cash_flows`` / ``cash_flows_unlevered`` are the
single source of truth for the levered / unlevered series; this engine only
decomposes them into legible line items. The reconciliation guard in
:meth:`CashFlowStatementEngine.run` asserts the composed component rows sum
back to those canonical series — if they ever drift, the engine fails loudly
rather than serving a second, inconsistent set of numbers.

Value indexing: ``unlevered`` / ``levered`` line ``values`` are indexed 0..hold
(index 0 = at close / Year 0); ``distributions`` values are indexed by
operating period (year 1..hold) as sourced from the partnership waterfall.
"""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fondok_schemas.provenance import ValueInput, ValueTrace
from fondok_schemas.underwriting import (
    CashFlowStatementLine,
    CashFlowStatementOutput,
)

from .base import BaseEngine
from .capital import CapitalEngineOutput
from .debt import DebtEngineOutputExt
from .expense import ExpenseEngineOutput
from .partnership import PartnershipOutputExt
from .returns import ReturnsEngineOutputExt

# Reconciliation tolerance — the composed statement must tie out to the
# returns engine within one cent (float roll-up noise only).
_CENT = 0.01


class CashFlowStatementInput(BaseModel):
    """Self-contained input — the upstream engine outputs this view composes.

    Populated by ``engine_runner._build_input_for('cash_flow', …)`` from the
    run's ``accumulated`` outputs, plus a few reconciliation scalars that live
    on the deal assumptions rather than on any single engine output.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    capital: CapitalEngineOutput
    expense: ExpenseEngineOutput
    debt: DebtEngineOutputExt
    returns: ReturnsEngineOutputExt
    partnership: PartnershipOutputExt
    # Transfer / recordation tax rate applied to the gross sale on exit — the
    # returns engine deducts it alongside brokerage selling costs, so the
    # unlevered disposition line must too (0 unless the analyst set it).
    transfer_tax_pct: Annotated[float, Field(ge=0.0, le=0.20)] = 0.0
    # FON-67 phased capital deployment — the returns engine shifts this outflow
    # from close (Year 0) to ``deferred_capital_year``; mirror it so the
    # unlevered stream ties out. 0 keeps the single-close view.
    deferred_capital: Annotated[float, Field(ge=0)] = 0.0
    deferred_capital_year: Annotated[int, Field(ge=0)] | None = None
    # Brand key money funded at close (a levered source). The returns engine
    # does not model it, so it stays 0 here to preserve reconciliation.
    key_money: Annotated[float, Field(ge=0)] = 0.0


def _slug(label: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", label.lower())).strip("_")


class CashFlowStatementEngine(
    BaseEngine[CashFlowStatementInput, CashFlowStatementOutput]
):
    """Compose the reconciled levered/unlevered statement + distributions."""

    name = "cash_flow"

    def run(self, payload: CashFlowStatementInput) -> CashFlowStatementOutput:
        cap = payload.capital
        exp = payload.expense
        debt = payload.debt
        ret = payload.returns
        part = payload.partnership

        unlev_canon = [float(x) for x in ret.cash_flows_unlevered]
        lev_canon = [float(x) for x in ret.cash_flows]
        if len(unlev_canon) < 2 or len(lev_canon) < 2:
            raise ValueError(
                "cash_flow view requires the returns engine's cash-flow series "
                "(cash_flows / cash_flows_unlevered); none present"
            )
        hold = len(lev_canon) - 1

        n = hold + 1  # period count incl. close (index 0)

        def _blank() -> list[float | None]:
            return [None] * n

        # ── upstream reads, aligned to operating years 1..hold ──
        ret_noi = list(getattr(ret, "noi_by_year", None) or [])

        def _noi_before_reserve(y: int) -> float:
            """NOI *before* the FF&E reserve (institutional / cap-rate basis).

            The returns engine's unlevered series carries NOI net of the FF&E
            reserve, so we add it back here and subtract it on its own line —
            the pair nets to the canonical NOI, and the reserve is visible.

            Source the net-of-reserve NOI from the returns engine's own
            ``noi_by_year`` when it published it (the override-aware series it
            actually built ``cash_flows_unlevered`` from). On a deal carrying
            ``noi_override_by_year`` the expense engine's NOI differs from that
            override, so composing from expense NOI would fail the
            reconciliation guard. Falling back to ``exp.years[y-1].noi`` keeps
            non-override deals — and any deal where returns didn't populate the
            series — byte-identical to the prior behavior.
            """
            yr = exp.years[y - 1]
            if y - 1 < len(ret_noi):
                return float(ret_noi[y - 1]) + float(yr.ffe_reserve)
            return float(yr.noi) + float(yr.ffe_reserve)

        def _ffe(y: int) -> float:
            return float(exp.years[y - 1].ffe_reserve)

        have_expense = len(exp.years) >= hold

        # Debt service the returns engine actually used, per operating year
        # (phased senior→refi when present, else the scalar annual figure) —
        # so the levered stream ties out even for multi-tranche / refi deals.
        dsby = list(debt.debt_service_by_year or [])

        def _ds_used(y: int) -> float:
            if dsby:
                return float(dsby[y - 1]) if y - 1 < len(dsby) else float(debt.annual_debt_service)
            return float(debt.annual_debt_service)

        def _sched(y: int):
            return debt.schedule[y - 1] if 0 <= y - 1 < len(debt.schedule) else None

        dc = float(payload.deferred_capital or 0.0)
        dy = payload.deferred_capital_year
        transfer_tax = float(ret.gross_sale_price) * float(payload.transfer_tax_pct)

        # ─────────────────────────── Unlevered ────────────────────────────
        unlev_lines: list[CashFlowStatementLine] = []

        acq = _blank()
        acq[0] = -float(cap.total_capital)
        unlev_lines.append(
            CashFlowStatementLine(
                label="Acquisition Uses at Close",
                values=acq,
                kind="linked",
                note="Investment → Sources & Uses (total uses funded at close).",
            )
        )
        if dc > 0:
            held = _blank()
            held[0] = dc
            unlev_lines.append(
                CashFlowStatementLine(
                    label="Deferred Capital Held at Close",
                    values=held,
                    kind="linked",
                    note="Capital drawn later in the hold, not funded at close.",
                )
            )

        noi_row = _blank()
        ffe_row = _blank()
        for y in range(1, hold + 1):
            if have_expense:
                noi_row[y] = _noi_before_reserve(y)
                ffe_row[y] = -_ffe(y)
        unlev_lines.append(
            CashFlowStatementLine(
                label="Net Operating Income",
                values=noi_row,
                kind="linked",
                note="Financials → Projections (NOI before FF&E reserve).",
            )
        )
        unlev_lines.append(
            CashFlowStatementLine(
                label="FF&E Reserve",
                values=ffe_row,
                kind="linked",
                note="Investment → recurring FF&E reserve (a real cash outflow).",
            )
        )

        gross = _blank()
        gross[hold] = float(ret.gross_sale_price)
        disp = _blank()
        disp[hold] = -(float(ret.selling_costs) + transfer_tax)
        unlev_lines.append(
            CashFlowStatementLine(
                label="Gross Sale Proceeds", values=gross, kind="linked",
                note="Investment → Exit (forward NOI ÷ exit cap).",
            )
        )
        unlev_lines.append(
            CashFlowStatementLine(
                label="Selling & Disposition Costs", values=disp, kind="linked",
                note="Investment → brokerage selling costs + transfer tax.",
            )
        )

        if dc > 0 and dy is not None and 1 <= dy <= hold:
            dep = _blank()
            dep[dy] = -dc
            unlev_lines.append(
                CashFlowStatementLine(
                    label="Deferred Capital Deployed", values=dep, kind="linked",
                    note=f"Deferred capital funded in year {dy}.",
                )
            )

        # Composed unlevered bottom line = Σ component rows.
        composed_unlev = self._sum_rows(unlev_lines, n)
        unlev_lines.append(
            CashFlowStatementLine(
                label="Unlevered Cash Flow",
                values=[float(v) for v in unlev_canon],
                kind="calc",
                note="Asset-level cash flow before financing.",
            )
        )

        # ──────────────────────────── Levered ─────────────────────────────
        lev_lines: list[CashFlowStatementLine] = []
        lev_lines.append(
            CashFlowStatementLine(
                label="Unlevered Cash Flow",
                values=[float(v) for v in unlev_canon],
                kind="linked",
                note="From the unlevered statement above.",
            )
        )

        proceeds = _blank()
        proceeds[0] = float(cap.debt_amount)
        lev_lines.append(
            CashFlowStatementLine(
                label="Debt Proceeds", values=proceeds, kind="linked",
                note="Debt → senior loan funded at close.",
            )
        )
        if payload.key_money > 0:
            km = _blank()
            km[0] = float(payload.key_money)
            lev_lines.append(
                CashFlowStatementLine(
                    label="Key Money", values=km, kind="linked",
                    note="Partnership → brand agreement key money.",
                )
            )

        interest = _blank()
        principal = _blank()
        junior = _blank()
        for y in range(1, hold + 1):
            sr = _sched(y)
            senior_ds = float(sr.debt_service) if sr is not None else 0.0
            if sr is not None:
                interest[y] = -float(sr.interest)
                principal[y] = -float(sr.principal)
            # Fold any refi/junior-tranche debt-service delta (the portion the
            # returns engine serviced beyond the senior amortization schedule)
            # onto its own line so the stream ties out for phased / multi-tranche
            # deals; zero for a plain single-senior loan (line omitted below).
            delta = _ds_used(y) - senior_ds
            if abs(delta) > _CENT:
                junior[y] = -delta
        lev_lines.append(
            CashFlowStatementLine(
                label="Interest Expense", values=interest, kind="linked",
                note="Debt → amortization schedule.",
            )
        )
        lev_lines.append(
            CashFlowStatementLine(
                label="Principal Amortization", values=principal, kind="linked",
                note="Debt → amortization schedule.",
            )
        )
        if any(v is not None for v in junior):
            lev_lines.append(
                CashFlowStatementLine(
                    label="Refinance / Junior Debt Service", values=junior,
                    kind="linked",
                    note="Debt → phased refinance / junior-tranche debt service.",
                )
            )

        if debt.refi_cash_out and debt.refi_cash_out > 0 and debt.refi_year:
            ry = int(debt.refi_year)
            if 1 <= ry <= hold:
                # Canonical 4-row refinance split (design/canonical/Cash Flow
                # Tab.dc.html): Refinance proceeds (+) · Existing debt payoff (−)
                # · Refinance fees (−) · Net refinance cash-out (=). The three
                # components are linked reads off the debt engine's already-
                # computed refi detail; the net is a calc subtotal (NOT summed —
                # ``_sum_rows`` skips calc rows) so the levered stream still ties
                # out to exactly ``refi_cash_out``. When any component is missing
                # (a run predating the debt-engine detail) or the three don't
                # foot to the net, fall back to the single net line so the
                # reconciliation guard is never at risk and older runs are
                # byte-identical.
                net_cash_out = float(debt.refi_cash_out)
                proceeds_v = debt.refi_new_loan_proceeds
                payoff_v = debt.refi_existing_balance_repaid
                fees_v = debt.refi_financing_costs
                components_present = (
                    proceeds_v is not None
                    and payoff_v is not None
                    and fees_v is not None
                )
                foots = components_present and (
                    abs(
                        (float(proceeds_v) - float(payoff_v) - float(fees_v))
                        - net_cash_out
                    )
                    <= _CENT
                )
                if foots:
                    proceeds_row = _blank()
                    proceeds_row[ry] = float(proceeds_v)
                    payoff_row = _blank()
                    payoff_row[ry] = -float(payoff_v)
                    fees_row = _blank()
                    fees_row[ry] = -float(fees_v)
                    net_row = _blank()
                    net_row[ry] = net_cash_out
                    lev_lines.append(
                        CashFlowStatementLine(
                            label="Refinance Proceeds", values=proceeds_row,
                            kind="linked",
                            note="Debt → refinance new loan proceeds.",
                        )
                    )
                    lev_lines.append(
                        CashFlowStatementLine(
                            label="Existing Debt Payoff", values=payoff_row,
                            kind="linked",
                            note="Debt → existing senior balance repaid at refinance.",
                        )
                    )
                    lev_lines.append(
                        CashFlowStatementLine(
                            label="Refinance Fees", values=fees_row,
                            kind="linked",
                            note="Debt → refinance financing costs.",
                        )
                    )
                    lev_lines.append(
                        CashFlowStatementLine(
                            label="Net Refinance Cash-Out", values=net_row,
                            kind="calc",
                            note=(
                                "Refinance proceeds − existing payoff − fees = "
                                "net cash-out to equity."
                            ),
                        )
                    )
                else:
                    refi = _blank()
                    refi[ry] = net_cash_out
                    lev_lines.append(
                        CashFlowStatementLine(
                            label="Net Refinance Cash-Out", values=refi,
                            kind="linked",
                            note="Debt → refinance proceeds returned to equity.",
                        )
                    )

        payoff = _blank()
        payoff[hold] = -float(
            debt.balance_at_exit
            if debt.balance_at_exit is not None
            else (debt.schedule[-1].ending_balance if debt.schedule else cap.debt_amount)
        )
        lev_lines.append(
            CashFlowStatementLine(
                label="Exit Debt Payoff", values=payoff, kind="linked",
                note="Debt → loan balance repaid at disposition.",
            )
        )

        composed_lev = self._sum_rows(lev_lines, n)
        lev_lines.append(
            CashFlowStatementLine(
                label="Net Cash Flow to Equity",
                values=[float(v) for v in lev_canon],
                kind="calc",
                note="Levered cash flow to equity, after debt service.",
            )
        )

        # ─────────────────────── Reconciliation guard ─────────────────────
        # A view over canonical numbers — never a second source of truth.
        self._reconcile(composed_unlev, unlev_canon, "unlevered")
        self._reconcile(composed_lev, lev_canon, "levered")

        # ────────────────────────── Distributions ─────────────────────────
        # Sourced from the partnership waterfall — LP pref, GP promote, exit —
        # NOT a hardcoded 10% pref-target proxy (the old CashFlowTab default).
        lp_flows = [float(x) for x in (part.lp_cash_flows or [])]
        gp_flows = [float(x) for x in (part.gp_cash_flows or [])]
        dist_lines: list[CashFlowStatementLine] = []
        if lp_flows:
            dist_lines.append(
                CashFlowStatementLine(
                    label="LP Distributions", values=list(lp_flows), kind="linked",
                    note="Partnership → LP cash flows (pref + residual).",
                )
            )
        if gp_flows:
            dist_lines.append(
                CashFlowStatementLine(
                    label="GP Distributions", values=list(gp_flows), kind="linked",
                    note=(
                        "Partnership → GP cash flows incl. promote "
                        f"(total promote ${part.promote_amount:,.0f})."
                    ),
                )
            )
        if lp_flows and gp_flows and len(lp_flows) == len(gp_flows):
            total = [lp_flows[i] + gp_flows[i] for i in range(len(lp_flows))]
            dist_lines.append(
                CashFlowStatementLine(
                    label="Total Distributions", values=total, kind="calc",
                    note="LP + GP distributions per period.",
                )
            )

        provenance = self._build_provenance(unlev_lines, lev_lines, dist_lines)

        return CashFlowStatementOutput(
            deal_id=payload.deal_id,
            hold_years=hold,
            unlevered=unlev_lines,
            levered=lev_lines,
            distributions=dist_lines,
            unlevered_cash_flow=[float(v) for v in unlev_canon],
            levered_cash_flow=[float(v) for v in lev_canon],
            provenance=provenance,
        )

    # ─────────────────────────────── helpers ──────────────────────────────

    @staticmethod
    def _sum_rows(lines: list[CashFlowStatementLine], n: int) -> list[float]:
        # Only ``linked`` component rows contribute to the composed bottom line.
        # ``calc`` rows are subtotals this view derives (e.g. the refinance
        # "Net Refinance Cash-Out" that sits between its own component rows), so
        # summing them would double-count. This mirrors the frontend's
        # ``footLinked`` and the reconciliation test's ``_composed`` helper,
        # both of which already skip non-linked rows. No existing behavior
        # changes: no ``calc`` row is present in ``lines`` at the point this is
        # called for a no-refi deal, so the composed totals stay byte-identical.
        totals = [0.0] * n
        for line in lines:
            if line.kind != "linked":
                continue
            for i, v in enumerate(line.values):
                if v is not None and i < n:
                    totals[i] += float(v)
        return totals

    @staticmethod
    def _reconcile(composed: list[float], canonical: list[float], label: str) -> None:
        if len(composed) != len(canonical):
            raise ValueError(
                f"cash_flow {label} reconciliation: length mismatch "
                f"{len(composed)} != {len(canonical)}"
            )
        for i, (c, k) in enumerate(zip(composed, canonical)):
            if abs(c - k) > _CENT:
                raise ValueError(
                    f"cash_flow {label} reconciliation failed at period {i}: "
                    f"composed {c:.4f} vs returns {k:.4f} (Δ {c - k:.4f}); "
                    "the statement must tie out to the returns engine to the cent"
                )

    @staticmethod
    def _build_provenance(
        unlev: list[CashFlowStatementLine],
        lev: list[CashFlowStatementLine],
        dist: list[CashFlowStatementLine],
    ) -> dict[str, ValueTrace]:
        """One trace per row, with ``state`` emitted directly from the row kind.

        ``linked`` rows are values pulled straight from another engine;
        ``calc`` rows are composed (summed) by this view. The trace's aggregate
        ``value`` is the row total, and a single cross-engine input records the
        provenance link so the UI can navigate back to the source.
        """
        # Which canonical engine each labelled row is linked from.
        source_ref = {
            "Acquisition Uses at Close": "capital.total_capital",
            "Deferred Capital Held at Close": "returns.cash_flows_unlevered",
            "Net Operating Income": "expense.years",
            "FF&E Reserve": "expense.years",
            "Gross Sale Proceeds": "returns.gross_sale_price",
            "Selling & Disposition Costs": "returns.selling_costs",
            "Deferred Capital Deployed": "returns.cash_flows_unlevered",
            "Unlevered Cash Flow": "returns.cash_flows_unlevered",
            "Debt Proceeds": "capital.debt_amount",
            "Key Money": "partnership.key_money",
            "Interest Expense": "debt.schedule",
            "Principal Amortization": "debt.schedule",
            "Refinance / Junior Debt Service": "debt.debt_service_by_year",
            "Refinance Proceeds": "debt.refi_new_loan_proceeds",
            "Existing Debt Payoff": "debt.refi_existing_balance_repaid",
            "Refinance Fees": "debt.refi_financing_costs",
            "Net Refinance Cash-Out": "debt.refi_cash_out",
            "Exit Debt Payoff": "debt.balance_at_exit",
            "Net Cash Flow to Equity": "returns.cash_flows",
            "LP Distributions": "partnership.lp_cash_flows",
            "GP Distributions": "partnership.gp_cash_flows",
            "Total Distributions": "partnership.lp_cash_flows",
        }
        prov: dict[str, ValueTrace] = {}
        for section, lines in (("unlevered", unlev), ("levered", lev), ("distributions", dist)):
            for line in lines:
                total = sum(float(v) for v in line.values if v is not None)
                state = "linked" if line.kind == "linked" else "calculated"
                ref = source_ref.get(line.label)
                inputs: list[ValueInput] = []
                if line.kind == "linked" and ref:
                    inputs = [ValueInput(name=line.label, value=total, traces_to=ref)]
                trace = ValueTrace(
                    value=total,
                    formula=(None if line.kind == "linked" else f"{line.label} = Σ component rows"),
                    inputs=inputs,
                    note=line.note,
                    state=state,
                )
                prov[f"{section}.{_slug(line.label)}"] = trace
        return prov


__all__ = ["CashFlowStatementEngine", "CashFlowStatementInput"]
