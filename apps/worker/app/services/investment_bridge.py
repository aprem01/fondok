"""Investment Bridge (tester round R-073) — equity invested → equity returned.

A waterfall that attributes the movement from the equity the sponsor puts in
at close to the total cash the equity gets back, across five legs:

    Equity invested
      + Acquisition   -(every Sources & Uses line except Renovation and the
                        senior loan origination fee)
      + Renovation    -(the "Renovation" use line, contingency included)
      + Operations    +Σ returns.noi_by_year           (NOI over the hold)
      + Financing     +loan proceeds - loan fee - Σ debt service
                      + refinance cash-out - loan payoff at exit
      + Exit          +gross sale - selling costs - transfer tax
    = Equity returned  Σ returns.cash_flows[1:]

READ-ONLY and DERIVED: every leg is read off the persisted engine envelopes of
ONE run (``capital.outputs`` · ``returns.outputs`` · ``returns.inputs`` — the
exact debt-service series, exit loan balance and refinance cash-out the
returns engine consumed). No engine math is re-implemented beyond summing
those series, and nothing is persisted.

Why it foots: the capital engine sets ``equity = total uses - debt`` and the
returns engine builds ``cash_flows = [-equity, NOI - DS (+ refi) …, + (gross -
selling - transfer - payoff)]``. Substituting one into the other gives
``equity + acquisition + renovation + operations + financing + exit ==
Σ cash_flows[1:]`` exactly. ``residual`` reports any difference (a run whose
pieces disagree), and ``reconciles`` is True only when every leg is available
and the residual is under a dollar.

A leg whose inputs are missing is ``status="unavailable"`` with ``value=None``
and a ``reason`` — it is never reported as zero. A leg the deal genuinely does
not have (no renovation line) is ``status="none"``: also ``value=None`` with a
reason, and it contributes nothing to the sum.
"""

from __future__ import annotations

from typing import Any

# Sources & Uses labels the bridge assigns to legs other than Acquisition.
RENOVATION_LABEL = "Renovation"
LOAN_FEE_LABEL = "Senior Loan Origination Fee"
TOTAL_USES_LABEL = "Total Uses"

# Tolerance for the foot check (USD).
FOOT_TOLERANCE_USD = 1.0


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if f == f else None  # NaN guard
    return None


def _envelope(rows: dict[str, Any], engine: str) -> tuple[dict[str, Any], dict[str, Any]]:
    row = rows.get(engine) or {}
    if not isinstance(row, dict):
        return {}, {}
    if row.get("status") not in (None, "complete"):
        return {}, {}
    outputs = row.get("outputs") if isinstance(row.get("outputs"), dict) else {}
    inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
    return outputs or {}, inputs or {}


def _component(label: str, value: float, source: str) -> dict[str, Any]:
    return {"label": label, "value": value, "source": source}


def _leg(
    key: str,
    label: str,
    *,
    value: float | None,
    status: str,
    formula: str,
    components: list[dict[str, Any]] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "status": status,
        "formula": formula,
        "components": components or [],
        "reason": reason,
    }


def _unavailable(key: str, label: str, formula: str, reason: str) -> dict[str, Any]:
    return _leg(key, label, value=None, status="unavailable", formula=formula, reason=reason)


def build_investment_bridge(rows: dict[str, Any]) -> dict[str, Any]:
    """Compute the bridge from one run's engine envelopes.

    ``rows`` is the ``{engine_name: envelope}`` map ``get_run_scoped_outputs``
    returns (each envelope carries ``status`` / ``inputs`` / ``outputs``).
    """
    ret_out, ret_in = _envelope(rows, "returns")
    cap_out, _cap_in = _envelope(rows, "capital")

    flows_raw = ret_out.get("cash_flows")
    flows = (
        [f for f in (_num(x) for x in flows_raw) if f is not None]
        if isinstance(flows_raw, list)
        else []
    )
    have_flows = isinstance(flows_raw, list) and len(flows) == len(flows_raw) and len(flows) >= 2
    equity_invested = -flows[0] if have_flows else None
    equity_returned = sum(flows[1:]) if have_flows else None
    hold = int(_num(ret_out.get("hold_years")) or 0) or None

    legs: list[dict[str, Any]] = []

    # ── Acquisition + Renovation — the capital engine's Sources & Uses ──────
    uses = cap_out.get("uses") if isinstance(cap_out.get("uses"), list) else None
    use_lines: list[tuple[str, float]] = []
    if uses is not None:
        for u in uses:
            if not isinstance(u, dict) or u.get("is_total") or u.get("label") == TOTAL_USES_LABEL:
                continue
            amt = _num(u.get("amount"))
            if amt is None:
                continue
            use_lines.append((str(u.get("label") or ""), amt))

    acq_formula = "-(Σ Sources & Uses lines, excluding Renovation and the senior loan fee)"
    reno_formula = "-(Sources & Uses → Renovation)"
    if uses is None:
        reason = "Sources & Uses unavailable — the capital engine has not run for this deal"
        legs.append(_unavailable("acquisition", "Acquisition", acq_formula, reason))
        legs.append(_unavailable("renovation", "Renovation / PIP", reno_formula, reason))
    else:
        acq_lines = [(lbl, a) for lbl, a in use_lines if lbl not in (RENOVATION_LABEL, LOAN_FEE_LABEL)]
        legs.append(
            _leg(
                "acquisition",
                "Acquisition",
                value=-sum(a for _, a in acq_lines),
                status="ok",
                formula=acq_formula,
                components=[_component(lbl, -a, f"capital.uses[{lbl}]") for lbl, a in acq_lines],
            )
        )
        reno = [(lbl, a) for lbl, a in use_lines if lbl == RENOVATION_LABEL]
        if reno:
            legs.append(
                _leg(
                    "renovation",
                    "Renovation / PIP",
                    value=-sum(a for _, a in reno),
                    status="ok",
                    formula=reno_formula,
                    components=[_component(lbl, -a, f"capital.uses[{lbl}]") for lbl, a in reno],
                )
            )
        else:
            legs.append(
                _leg(
                    "renovation",
                    "Renovation / PIP",
                    value=None,
                    status="none",
                    formula=reno_formula,
                    reason="No renovation budget in Sources & Uses — nothing to attribute",
                )
            )

    # ── Operations — Σ NOI the returns engine ran on ─────────────────────────
    noi_raw = ret_out.get("noi_by_year")
    noi = [_num(x) for x in noi_raw] if isinstance(noi_raw, list) else []
    ops_formula = "Σ returns.noi_by_year (NOI over the hold, before debt service)"
    if noi and all(n is not None for n in noi):
        legs.append(
            _leg(
                "operations",
                "Operations",
                value=sum(n for n in noi if n is not None),
                status="ok",
                formula=ops_formula,
                components=[
                    _component(f"Year {i + 1} NOI", float(n), f"returns.noi_by_year[{i}]")
                    for i, n in enumerate(noi)
                    if n is not None
                ],
            )
        )
    else:
        legs.append(
            _unavailable(
                "operations", "Operations", ops_formula,
                "The returns run carries no NOI series (noi_by_year) — re-run the model",
            )
        )

    # ── Financing — loan in, fee, debt service, refi cash-out, payoff ───────
    fin_formula = (
        "loan proceeds - loan fee - Σ debt service + refinance cash-out - loan payoff at exit"
    )
    loan = _num(ret_in.get("loan_amount"))
    ds_raw = ret_in.get("debt_service_by_year")
    annual_ds = _num(ret_in.get("annual_debt_service"))
    payoff_in = ret_in.get("loan_balance_at_exit")
    payoff = _num(payoff_in) if payoff_in is not None else loan
    refi_cash = _num(ret_in.get("refi_cash_out")) or 0.0
    refi_year = _num(ret_in.get("refi_year"))
    if not ret_in or loan is None or hold is None or annual_ds is None or payoff is None:
        legs.append(
            _unavailable(
                "financing", "Financing", fin_formula,
                "The returns run did not record its debt inputs (loan amount, debt service, "
                "exit balance) — re-run the model",
            )
        )
    else:
        # Mirror the returns engine's DS series exactly: the phased series,
        # truncated to the hold, padded with the scalar annual DS.
        if isinstance(ds_raw, list) and ds_raw:
            ds = [float(_num(x) or 0.0) for x in ds_raw[:hold]]
            while len(ds) < hold:
                ds.append(annual_ds)
        else:
            ds = [annual_ds] * hold
        refi_active = refi_cash > 0 and refi_year is not None and 1 <= int(refi_year) <= hold
        fee = sum(a for lbl, a in use_lines if lbl == LOAN_FEE_LABEL)
        comps = [_component("Loan proceeds", loan, "returns.inputs.loan_amount (= capital.debt_amount)")]
        if fee:
            comps.append(_component("Loan origination fee", -fee, f"capital.uses[{LOAN_FEE_LABEL}]"))
        comps.append(
            _component(
                "Debt service over the hold",
                -sum(ds),
                "returns.inputs.debt_service_by_year (debt engine)",
            )
        )
        if refi_active:
            comps.append(
                _component(
                    f"Refinance cash-out (Year {int(refi_year)})",  # type: ignore[arg-type]
                    refi_cash,
                    "returns.inputs.refi_cash_out (debt engine)",
                )
            )
        comps.append(
            _component(
                "Loan payoff at exit",
                -payoff,
                "returns.inputs.loan_balance_at_exit (debt engine)",
            )
        )
        legs.append(
            _leg(
                "financing",
                "Financing",
                value=sum(c["value"] for c in comps),
                status="ok",
                formula=fin_formula,
                components=comps,
            )
        )

    # ── Exit — net sale proceeds before the loan payoff ─────────────────────
    exit_formula = "gross sale price - selling costs - transfer tax"
    gross = _num(ret_out.get("gross_sale_price"))
    selling = _num(ret_out.get("selling_costs"))
    assumptions = ret_in.get("assumptions") if isinstance(ret_in.get("assumptions"), dict) else {}
    tt_pct = _num((assumptions or {}).get("transfer_tax_pct"))
    if gross is None or selling is None:
        legs.append(
            _unavailable(
                "exit", "Exit", exit_formula,
                "The returns run did not emit a gross sale price / selling costs",
            )
        )
    else:
        comps = [
            _component("Gross sale price", gross, "returns.gross_sale_price"),
            _component("Selling costs", -selling, "returns.selling_costs"),
        ]
        transfer = gross * tt_pct if tt_pct else 0.0
        if transfer:
            comps.append(
                _component(
                    "Transfer tax",
                    -transfer,
                    "returns.gross_sale_price x returns.inputs.assumptions.transfer_tax_pct",
                )
            )
        legs.append(
            _leg(
                "exit",
                "Exit",
                value=sum(c["value"] for c in comps),
                status="ok",
                formula=exit_formula,
                components=comps,
            )
        )

    unavailable = [leg["key"] for leg in legs if leg["status"] == "unavailable"]
    computed = (
        equity_invested + sum(leg["value"] for leg in legs if leg["value"] is not None)
        if equity_invested is not None and not unavailable
        else None
    )
    residual = (
        computed - equity_returned
        if computed is not None and equity_returned is not None
        else None
    )
    reconciles = residual is not None and abs(residual) < FOOT_TOLERANCE_USD

    return {
        "equity_invested": equity_invested,
        "equity_invested_source": "-returns.cash_flows[0] (= capital.equity_amount)",
        "equity_returned": equity_returned,
        "equity_returned_source": "Σ returns.cash_flows[1:]",
        "equity_profit": (
            equity_returned - equity_invested
            if equity_returned is not None and equity_invested is not None
            else None
        ),
        "hold_years": hold,
        "legs": legs,
        "unavailable": unavailable,
        "computed_equity_returned": computed,
        "residual": residual,
        "reconciles": reconciles,
        "available": have_flows,
        "reason": None if have_flows else "The returns engine has not produced a cash-flow series for this deal",
    }


__all__ = ["FOOT_TOLERANCE_USD", "build_investment_bridge"]
