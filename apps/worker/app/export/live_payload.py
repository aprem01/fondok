"""Assemble the export payload for a *live* (real UUID) deal.

The export builders (:mod:`app.export.excel` / ``memo_pdf`` / ``presentation``)
consume a ``(deal, model, memo)`` triple shaped exactly like the hard-coded
Kimpton fixture in :mod:`app.export.fixtures`. This module produces that same
shape from a live deal instead — reading:

    * the deal row (name / city / keys / brand / service / stage / confidence),
    * the extracted subject-property name (OM-first; distinct from the project
      name — see ``reference_fondok_property_name``),
    * the deal's *canonical-run* engine snapshot
      (:func:`app.services.engine_runner.get_run_scoped_outputs`), and
    * the generated IC memo from the in-process ``MemoCache`` with the analyst's
      persisted ``memo_*`` overrides layered on
      (:func:`app.memo_overrides.apply_memo_overrides`), plus the real uploaded
      documents for the appendix.

No-fake-data contract (see ``feedback_no_fake_data``)
----------------------------------------------------
Every field is mapped from a genuine live source. Where the live deal lacks a
value the Kimpton fixture carried, the key is **omitted** so the builders render
``—`` / an empty section — the fixture value is NEVER carried into a real
deal's export. A handful of standard display ratios (entry cap on Y1 UW NOI,
Y1 yield-on-cost, per-key figures, annual cash-flow-after-debt) are derived
arithmetically from two live engine outputs; these are presentation-layer
aggregations, not new engine math.

The engine-output field names differ from the fixture's ``model`` keys (e.g.
returns emits ``net_proceeds`` / ``year_one_coc`` / ``exit_cap_rate``, debt emits
``year_one_dscr`` / ``year_one_debt_yield``, capital emits ``uses[]`` line items
rather than scalar ``*_usd`` costs). The mapping below is the single translation
seam between the two.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ─────────────────────────── small helpers ───────────────────────────


def _num(value: Any) -> float | None:
    """Coerce a JSON scalar to float, or None when absent / non-numeric."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _outputs(snapshot: dict[str, dict[str, Any]], engine: str) -> dict[str, Any]:
    """The serialized ``outputs`` dict for one engine, or ``{}``."""
    row = snapshot.get(engine) or {}
    out = row.get("outputs")
    return out if isinstance(out, dict) else {}


def _set(target: dict[str, Any], key: str, value: Any) -> None:
    """Assign ``key`` only when ``value`` is present — omit otherwise.

    Omitting (rather than writing 0 / None) is what makes the builders render
    ``—`` for a genuinely-missing live value instead of a fabricated zero.
    """
    if value is not None:
        target[key] = value


def _use_amount(uses: list[dict[str, Any]], *needles: str) -> float | None:
    """Amount of the first ``uses[]`` line whose label matches a needle.

    The capital engine emits acquisition costs as ``uses[]`` line items
    (``label`` / ``amount``), not scalar fields, and drops zero-value lines.
    Match is case-insensitive substring so ``"renovation"`` hits the
    ``"Renovation"`` line.
    """
    for row in uses or []:
        if not isinstance(row, dict):
            continue
        if row.get("is_total"):
            continue
        label = str(row.get("label", "")).lower()
        if any(n in label for n in needles):
            return _num(row.get("amount"))
    return None


# ─────────────────────────── model assembly ───────────────────────────


def _build_investment(
    capital: dict[str, Any], keys: int | None, noi_y1: float | None
) -> dict[str, Any]:
    """``investment_engine`` block from the capital engine + a derived cap/yield."""
    out: dict[str, Any] = {}
    uses = capital.get("uses") if isinstance(capital.get("uses"), list) else []

    purchase = _use_amount(uses, "purchase price")
    total_capital = _num(capital.get("total_capital"))
    renovation = _use_amount(uses, "renovation")
    loan_costs = _use_amount(uses, "senior loan fee", "loan fee")
    if loan_costs is None:
        loan_costs = _num(capital.get("senior_loan_fee_usd"))

    _set(out, "purchase_price_usd", purchase)
    _set(out, "price_per_key_usd", _num(capital.get("price_per_key")))
    _set(out, "closing_costs_usd", _use_amount(uses, "closing"))
    _set(out, "renovation_budget_usd", renovation)
    _set(out, "soft_costs_usd", _use_amount(uses, "soft cost"))
    _set(out, "contingency_usd", _use_amount(uses, "contingency"))
    _set(out, "working_capital_usd", _use_amount(uses, "working capital"))
    _set(out, "loan_costs_usd", loan_costs)
    _set(out, "total_capital_usd", total_capital)

    if keys and keys > 0:
        if total_capital is not None:
            out["total_capital_per_key_usd"] = round(total_capital / keys)
        if renovation is not None:
            out["renovation_per_key_usd"] = round(renovation / keys)

    # Derived display ratios (two real engine outputs each; not engine math):
    #   entry cap (Y1 UW) = Y1 underwritten NOI / purchase price
    #   Y1 yield on cost   = Y1 underwritten NOI / all-in total capital
    if noi_y1 is not None and purchase and purchase > 0:
        out["entry_cap_rate_year1_uw"] = noi_y1 / purchase
    if noi_y1 is not None and total_capital and total_capital > 0:
        out["year1_yield_on_cost"] = noi_y1 / total_capital
    return out


def _build_proforma_and_cf(
    revenue: dict[str, Any],
    expense: dict[str, Any],
    debt: dict[str, Any],
    horizon: int = 5,
) -> tuple[dict[str, Any], dict[str, Any], float | None]:
    """Build ``p_and_l_engine_proforma`` + ``cash_flow_engine`` from live years.

    Returns ``(proforma, cash_flow_engine, y1_noi_usd)``. Values in the proforma
    are in USD **thousands** (the fixture/builder scale); ``y1_noi_usd`` is the
    Year-1 NOI in whole dollars for the entry-cap derivation. Returns empty
    dicts when revenue+expense years aren't both present (barebones deal).
    """
    rev_years = revenue.get("years") if isinstance(revenue.get("years"), list) else []
    exp_years = expense.get("years") if isinstance(expense.get("years"), list) else []
    if not rev_years or not exp_years:
        return {}, {}, None

    rev_by = {int(y.get("year")): y for y in rev_years if y.get("year") is not None}
    exp_by = {int(y.get("year")): y for y in exp_years if y.get("year") is not None}

    ds_series = debt.get("debt_service_by_year")
    if not isinstance(ds_series, list) or not ds_series:
        sched = debt.get("schedule")
        if isinstance(sched, list) and sched:
            ds_series = [_num(r.get("debt_service")) or 0.0 for r in sched]
        else:
            ann = _num(debt.get("annual_debt_service"))
            ds_series = [ann or 0.0] * horizon

    def k(v: float | None) -> float:
        """USD → USD thousands, rounded (builder scale)."""
        return round((v or 0.0) / 1000.0)

    years = sorted(set(rev_by) & set(exp_by))[:horizon]
    if not years:
        return {}, {}, None

    room: dict[int, float] = {}
    fb: dict[int, float] = {}
    other: dict[int, float] = {}
    total_rev: dict[int, float] = {}
    opex: dict[int, float] = {}
    mgmt: dict[int, float] = {}
    ffe: dict[int, float] = {}
    noi: dict[int, float] = {}
    cfad: dict[int, float] = {}
    noi_y1_usd: float | None = None

    for n in years:
        r = rev_by[n]
        e = exp_by[n]
        dept = (e.get("dept_expenses") or {}).get("total")
        undist = (e.get("undistributed") or {}).get("total")
        fixed = (e.get("fixed_charges") or {}).get("total")
        noi_usd = _num(e.get("noi"))
        if n == 1:
            noi_y1_usd = _num(e.get("noi_institutional")) or noi_usd
        ds = ds_series[n - 1] if n - 1 < len(ds_series) else (ds_series[-1] if ds_series else 0.0)
        ds = _num(ds) or 0.0

        room[n] = k(_num(r.get("rooms_revenue")))
        fb[n] = k(_num(r.get("fb_revenue")))
        other[n] = k((_num(r.get("other_revenue")) or 0.0) + (_num(r.get("resort_fees")) or 0.0))
        total_rev[n] = k(_num(r.get("total_revenue")))
        opex[n] = k((_num(dept) or 0.0) + (_num(undist) or 0.0) + (_num(fixed) or 0.0))
        mgmt[n] = k(_num(e.get("mgmt_fee")))
        ffe[n] = k(_num(e.get("ffe_reserve")))
        noi[n] = k(noi_usd)
        cfad[n] = k((noi_usd or 0.0) - ds)

    def row(label: str, src: dict[int, float], **extra: Any) -> dict[str, Any]:
        d: dict[str, Any] = {"label": label}
        for n in range(1, horizon + 1):
            d[f"y{n}"] = src.get(n, 0)
        d.update(extra)
        return d

    ds_row: dict[int, float] = {}
    for n in years:
        idx = n - 1
        ds_val = ds_series[idx] if idx < len(ds_series) else (ds_series[-1] if ds_series else 0.0)
        ds_row[n] = k(_num(ds_val) or 0.0)

    lines = [
        row("Room Revenue", room),
        row("F&B Revenue", fb),
        row("Other Revenue", other),
        row("Total Revenue", total_rev, cagr=_num(revenue.get("total_revenue_cagr")), bold=True),
        row("Operating Expenses", opex),
        row("Management Fee", mgmt),
        row("FF&E Reserve", ffe),
        row("Net Operating Income", noi, cagr=_num(expense.get("noi_cagr")), bold=True),
        row("Debt Service", ds_row),
        row("Cash Flow After Debt", cfad, bold=True),
    ]
    proforma: dict[str, Any] = {"lines": lines}

    cash_flow_engine: dict[str, Any] = {}
    cumulative = 0.0
    for n in range(1, horizon + 1):
        val_k = cfad.get(n)
        if val_k is None:
            continue
        usd = val_k * 1000.0
        cash_flow_engine[f"year{n}_cf_after_debt_usd"] = usd
        cumulative += usd
    if cash_flow_engine:
        cash_flow_engine["cumulative_cf_5yr_usd"] = cumulative

    return proforma, cash_flow_engine, noi_y1_usd


def _build_debt(debt: dict[str, Any], capital: dict[str, Any]) -> dict[str, Any]:
    """``debt_engine`` block from the debt engine (+ capital LTC)."""
    out: dict[str, Any] = {}
    stack = debt.get("debt_stack") if isinstance(debt.get("debt_stack"), dict) else {}

    loan = _num(debt.get("loan_amount"))
    if loan is None:
        loan = _num(stack.get("total_debt"))
    _set(out, "loan_amount_usd", loan)

    ltv_value = _num(stack.get("ltv"))
    if ltv_value is None:
        for cov in debt.get("covenants") or []:
            if isinstance(cov, dict) and cov.get("name") == "ltv":
                ltv_value = _num(cov.get("current"))
                break
    _set(out, "ltv_value", ltv_value)

    ltc = _num(capital.get("ltc"))
    if ltc is None:
        ltc = _num(stack.get("ltc"))
    _set(out, "ltc", ltc)

    _set(out, "interest_rate_pct", _num(debt.get("interest_rate")))
    _set(out, "amortization_years", _num(debt.get("amortization_years")))
    _set(out, "term_years", _num(debt.get("term_years")))
    _set(out, "annual_debt_service_usd", _num(debt.get("annual_debt_service")))
    _set(out, "year1_dscr", _num(debt.get("year_one_dscr")))
    _set(out, "year1_debt_yield", _num(debt.get("year_one_debt_yield")))
    return out


def _build_returns(returns: dict[str, Any]) -> dict[str, Any]:
    """``returns_engine`` block — note the live field renames."""
    out: dict[str, Any] = {}
    _set(out, "hold_years", _num(returns.get("hold_years")))
    _set(out, "exit_cap_rate_pct", _num(returns.get("exit_cap_rate")))
    _set(out, "terminal_noi_usd", _num(returns.get("terminal_noi")))
    _set(out, "gross_sale_price_usd", _num(returns.get("gross_sale_price")))
    _set(out, "selling_costs_usd", _num(returns.get("selling_costs")))
    _set(out, "net_sale_proceeds_usd", _num(returns.get("net_proceeds")))
    _set(out, "levered_irr", _num(returns.get("levered_irr")))
    _set(out, "unlevered_irr", _num(returns.get("unlevered_irr")))
    _set(out, "equity_multiple", _num(returns.get("equity_multiple")))
    _set(out, "year1_cash_on_cash", _num(returns.get("year_one_coc")))
    _set(out, "avg_cash_on_cash", _num(returns.get("avg_coc")))
    return out


def _build_partnership(partnership: dict[str, Any]) -> dict[str, Any]:
    """``partnership_engine`` block (Excel-only) from the partnership engine."""
    lp = partnership.get("lp") if isinstance(partnership.get("lp"), dict) else {}
    gp = partnership.get("gp") if isinstance(partnership.get("gp"), dict) else {}
    if not lp and not gp:
        return {}
    out: dict[str, Any] = {"structure": "GP/LP waterfall"}
    lp_eq = _num(lp.get("contributed_equity"))
    gp_eq = _num(gp.get("contributed_equity"))
    _set(out, "lp_equity_usd", lp_eq)
    _set(out, "gp_equity_usd", gp_eq)
    if lp_eq is not None or gp_eq is not None:
        out["total_equity_usd"] = (lp_eq or 0.0) + (gp_eq or 0.0)
    _set(out, "lp_irr_after_promote", _num(lp.get("irr")))
    _set(out, "gp_irr_after_promote", _num(gp.get("irr")))
    _set(out, "lp_equity_multiple", _num(lp.get("equity_multiple")))
    _set(out, "gp_equity_multiple", _num(gp.get("equity_multiple")))
    return out


def _build_sources_uses(capital: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Non-total ``sources`` / ``uses`` rows as ``{label, amount}``.

    The builders recompute their own totals, so the engine's ``is_total`` rows
    are dropped (leaving them in would double-count).
    """
    def rows(items: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in items or []:
            if not isinstance(r, dict) or r.get("is_total"):
                continue
            amt = _num(r.get("amount"))
            if amt is None:
                continue
            out.append({"label": r.get("label", ""), "amount": amt})
        return out

    return rows(capital.get("sources")), rows(capital.get("uses"))


def _build_segments_by_year(revenue: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Revenue-mix source for the memo/Excel (per-year ``segment_breakdown``)."""
    years = revenue.get("years") if isinstance(revenue.get("years"), list) else []
    out: list[dict[str, Any]] = []
    for y in years:
        segs = y.get("segment_breakdown")
        if isinstance(segs, list) and segs:
            out.append({"year": y.get("year"), "segment_breakdown": segs})
    return out or None


# ─────────────────────────── memo assembly ───────────────────────────


_LIVE_TO_EXEC = "deal_overview"  # live "Property" section seeds the exec summary


def _build_memo(
    *,
    live_sections: list[dict[str, Any]],
    overrides: dict[str, Any],
    property_name: str | None,
    deal_name: str,
    location: str | None,
    keys: int | None,
    deal_stage: str | None,
    ai_confidence: float | None,
    engines_run: list[str],
    documents_reviewed: list[str],
) -> dict[str, Any]:
    """Translate the live memo (overrides applied) into the builder memo shape.

    The Analyst emits six prose sections (``investment_thesis``,
    ``market_analysis``, ``deal_overview``, ``financial_analysis``,
    ``risk_factors``, ``recommendation``); the builders read ``executive_summary``
    / ``investment_thesis`` / ``recommendation`` prose plus (when present)
    ``key_insights`` / ``risk_assessment`` / ``variance_disclosure`` structured
    sections. We pass every live section through (unknown ids are ignored) and
    additionally alias ``deal_overview`` → ``executive_summary`` so the exec
    block leads with real prose. The structured highlight/risk/variance sections
    have no reliable live source and are intentionally omitted (rendered ``—``)
    rather than fabricated.
    """
    from ..memo_overrides import apply_memo_overrides

    sections = list(apply_memo_overrides(live_sections, overrides))

    # Alias deal_overview → executive_summary (copy, not move) when the builder's
    # exec section isn't already present.
    have_ids = {s.get("section_id") for s in sections}
    if "executive_summary" not in have_ids:
        for s in sections:
            if s.get("section_id") == _LIVE_TO_EXEC and (s.get("body") or "").strip():
                alias = dict(s)
                alias["section_id"] = "executive_summary"
                alias["title"] = "Executive Summary"
                sections.append(alias)
                break

    # Verdict: the analyst's persisted IC verdict, else an honest neutral (never
    # the pptx builder's "PROCEED TO LOI" default).
    from ..memo_overrides import VALID_VERDICTS

    verdict = overrides.get("memo_recommendation_override")
    recommendation = verdict if verdict in VALID_VERDICTS else "In Review"

    header: dict[str, Any] = {
        "title": "Investment Committee Memorandum",
        "subject_property": property_name or deal_name or "—",
        "location": location or "",
        "recommendation": recommendation,
        "deal_stage": deal_stage or "",
    }

    appendix: dict[str, Any] = {
        "documents_reviewed": documents_reviewed,
        "engines_run": engines_run,
    }
    if ai_confidence is not None:
        appendix["ai_confidence"] = ai_confidence

    return {"header": header, "sections": sections, "appendix": appendix}


# ─────────────────────────── DB reads ───────────────────────────


async def _deal_row(session: AsyncSession, deal_id: str, tenant_id: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT name, city, keys, brand, service, status, deal_stage,
                       ai_confidence, field_overrides
                  FROM deals
                 WHERE id = :id AND tenant_id = :tenant
                 LIMIT 1
                """
            ),
            {"id": deal_id, "tenant": tenant_id},
        )
    ).first()
    return dict(row._mapping) if row is not None else None


async def _documents_reviewed(session: AsyncSession, deal_id: str, tenant_id: str) -> list[str]:
    """Real uploaded filenames for the appendix (empty on any read failure)."""
    try:
        rows = await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT filename
                  FROM documents
                 WHERE deal_id = :id AND tenant_id = :tenant
                 ORDER BY uploaded_at ASC
                """
            ),
            {"id": deal_id, "tenant": tenant_id},
        )
    except Exception:  # noqa: BLE001 - degrade gracefully
        return []
    return [r._mapping["filename"] for r in rows.fetchall()]


async def _market_kpis(
    session: AsyncSession, deal_id: str, tenant_id: str, *, city: str | None
) -> dict[str, Any]:
    """A small, real submarket-KPI dict for the deck.

    Always non-empty (at least ``Submarket``) so the pptx builder never falls
    back to its hard-coded Miami Beach sample. Trailing-12 occupancy / ADR are
    the same figures the Overview surfaces; both omitted when the deal has no
    STR history on file.
    """
    kpis: dict[str, Any] = {"Submarket": city or "—"}
    try:
        from ..engines.str_forecast import trailing_12_occ_adr
        from ..services.str_forecast_loader import load_str_history_for_deal

        history = await load_str_history_for_deal(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        trailing = trailing_12_occ_adr(history)
        if trailing is not None:
            occ, adr = trailing
            if occ is not None:
                kpis["Trailing-12 Occupancy"] = f"{occ * 100:.1f}%"
            if adr is not None:
                kpis["Trailing-12 ADR"] = f"${adr:,.0f}"
    except Exception:  # noqa: BLE001 — KPI panel must never fail the export
        logger.debug("live export: trailing-12 STR read failed", exc_info=True)
    return kpis


# ─────────────────── Wave 2/3 conditional sheets (FON-54 follow-up) ───────────────────
#
# Each helper below sources ONE conditional Excel sheet (or the PPTX scenario
# cards) from the live deal and returns ``None`` / ``[]`` when the deal has no
# data for it. The caller then omits the key, so the builder skips the sheet —
# never a fixture row, never a fabricated zero. Every helper is best-effort:
# a read failure logs at debug and omits that sheet rather than failing the
# whole export.
#
# Live source per sheet (same source the UI surface reads):
#   variance_flags      GET /analysis/{id}/variance  (Financials variance chips)
#   market_comps        GET /market/{id}/transaction-comps  (Market tab comps table)
#   comp_sales          GET /deals/{id}/comp-sales  (Comparable Sales engine)
#   named_scenarios /   scenarios table + each scenario's own run, Base pinned to
#   scenario_outputs      the canonical run (as ``compare_scenarios`` does)
#   historical_baseline GET /deals/{id}/historical-baseline  (P&L docs → baseline)
#   str_forecast        GET /deals/{id}/str-forecast  (STR_TREND history → 24-mo fcst)
#   capex_schedule      capital ``uses`` Renovation line + expense ``ffe_reserve``
#                         (+ persisted ``capex_plan.*`` overrides when set)
#   op_ratio_provenance ``_load_engine_inputs`` ``__sources__`` map (AssumptionBadge)
#   sensitivity_grid /  POST /analysis/{id}/pricing/{sensitivity,max-price,loi}
#   max_price / loi_draft  — the Pricing panel's read-only in-memory chain walk
#
# Deliberately NOT wired (no live source that avoids a fabricated value):
#   pip_displacement    the revenue engine carries the PIP spec on its INPUTS
#                       but emits no displacement-dollar output, and the
#                       Renovation Plan sheet headlines ``Y1 Displacement`` —
#                       it would print a fabricated $0. Omitted.


_SEVERITY_LABELS: dict[str, str] = {
    "critical": "CRITICAL",
    "warn": "WARN",
    "warning": "WARN",
    "info": "INFO",
}

_ACRONYMS: dict[str, str] = {
    "noi": "NOI", "adr": "ADR", "revpar": "RevPAR", "gop": "GOP",
    "fb": "F&B", "ag": "A&G", "ffe": "FF&E", "cbre": "CBRE", "t12": "T-12",
    "y1": "Y1", "usd": "USD", "pct": "%",
}


def _attr(obj: Any, key: str) -> Any:
    """Read ``key`` off a pydantic model / dataclass / dict alike."""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _humanize_field(field: str) -> str:
    """``broker_adr_growth_vs_market`` → ``Broker ADR Growth vs Market``."""
    words = str(field or "").replace(".", " ").replace("_", " ").split()
    out: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in _ACRONYMS:
            out.append(_ACRONYMS[lw])
        elif lw == "vs":
            out.append("vs")
        else:
            out.append(w.capitalize())
    return " ".join(out) or "—"


def _variance_flags_from_out(flags: list[Any]) -> list[dict[str, Any]]:
    """Map ``VarianceFlagOut`` rows onto the builder's ``variance_flags`` shape.

    Input is the ``/analysis/{id}/variance`` flag list (the same rows the
    Financials variance chips render): ``field`` / ``severity`` / ``broker`` /
    ``actual`` / ``delta_pct`` / ``note`` / ``rule_id``. Output keys match the
    fixture rows the Variance sheet reads (``flag_id`` / ``severity`` /
    ``metric`` / ``broker_value`` / ``t12_value`` / ``variance_pct`` /
    ``recommended_action``). Absent numbers are omitted, not zeroed.
    """
    out: list[dict[str, Any]] = []
    for i, f in enumerate(flags, start=1):
        sev_raw = str(_attr(f, "severity") or "info").lower()
        row: dict[str, Any] = {
            "flag_id": f"VF-{i:03d}",
            "severity": _SEVERITY_LABELS.get(sev_raw, "INFO"),
            "metric": _humanize_field(str(_attr(f, "field") or "")),
        }
        _set(row, "rule_id", _attr(f, "rule_id"))
        _set(row, "broker_value", _num(_attr(f, "broker")))
        _set(row, "t12_value", _num(_attr(f, "actual")))
        _set(row, "variance_pct", _num(_attr(f, "delta_pct")))
        _set(row, "recommended_action", _attr(f, "note"))
        out.append(row)
    return out


async def _variance_flags(
    session: AsyncSession, deal_id: str, tenant_id: str
) -> list[dict[str, Any]]:
    """Live variance flags — the deterministic broker-vs-T-12 (+ broker-vs-CBRE)
    rule pass behind ``GET /analysis/{id}/variance``. Empty until both an
    OM/broker proforma and a T-12 have been extracted on the deal."""
    try:
        from ..api.analysis import get_variance

        resp = await get_variance(
            deal_id=UUID(deal_id), session=session, tenant_id=UUID(tenant_id)
        )
    except Exception:  # noqa: BLE001 — sheet is best-effort
        logger.debug("live export: variance read failed", exc_info=True)
        return []
    return _variance_flags_from_out(list(resp.flags or []))


def _fmt_money_compact(value: float | None) -> str:
    """``245_000_000`` → ``$245M``; ``392_000`` → ``$392k`` (fixture style)."""
    if value is None:
        return ""
    v = float(value)
    a = abs(v)
    if a >= 1_000_000:
        s = f"{v / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return f"${s}M"
    if a >= 1_000:
        return f"${v / 1_000:.0f}k"
    return f"${v:,.0f}"


def _market_comps_from_entries(entries: list[Any]) -> list[dict[str, Any]]:
    """Map ``TransactionCompEntry`` rows onto the Market Comps sheet rows.

    The sheet's columns are display strings (``price`` / ``per_key`` / ``cap``
    carry no number format), so they're rendered fixture-style; the numeric
    originals ride along under their API names for any numeric consumer.
    ``keys`` is ``None`` (blank cell) when the OM row didn't carry it — never 0.
    """
    out: list[dict[str, Any]] = []
    for c in entries:
        name = _attr(c, "name")
        if not name:
            continue
        keys = _attr(c, "keys")
        price = _num(_attr(c, "sale_price_usd"))
        per_key = _num(_attr(c, "price_per_key_usd"))
        cap = _num(_attr(c, "cap_rate_pct"))
        row: dict[str, Any] = {
            "name": str(name),
            "keys": int(keys) if keys is not None else None,
            "date": _attr(c, "sale_date") or "",
            "price": _fmt_money_compact(price),
            "per_key": _fmt_money_compact(per_key),
            "cap": f"{cap:.1f}%" if cap is not None else "",
            "buyer": _attr(c, "buyer_name") or _attr(c, "buyer_type") or "",
        }
        _set(row, "sale_price_usd", price)
        _set(row, "price_per_key_usd", per_key)
        _set(row, "cap_rate_pct", cap)
        _set(row, "market", _attr(c, "market"))
        _set(row, "seller", _attr(c, "seller"))
        _set(row, "source_document_id", _attr(c, "source_document_id"))
        out.append(row)
    return out


async def _market_comps(
    session: AsyncSession, deal_id: str, tenant_id: str
) -> list[dict[str, Any]]:
    """Live transaction comps — the OM's ``transaction_comps.<n>.*`` extraction
    rows behind ``GET /market/{id}/transaction-comps`` (the Market tab table).
    The STR/CoStar comp-set endpoint (``/market/{id}/comps``) is still a stub
    that returns ``[]``, so transaction comps are the only live comp source."""
    try:
        from ..api.market import transaction_comps

        resp = await transaction_comps(
            deal_id=UUID(deal_id), session=session, tenant_id=UUID(tenant_id)
        )
    except Exception:  # noqa: BLE001
        logger.debug("live export: transaction comps read failed", exc_info=True)
        return []
    return _market_comps_from_entries(list(resp.comps or []))


async def _comp_sales(
    session: AsyncSession, deal_id: str, tenant_id: str
) -> dict[str, Any] | None:
    """Live Comparable Sales set (W3.1) — the same in-memory engine run behind
    ``GET /deals/{id}/comp-sales`` (analyst exclude-list applied). ``None``
    when the deal's OM carries no comp table."""
    try:
        from ..api.deals import _load_subject_market_and_chain
        from ..services.engine_runner import _build_comp_sales_set

        market, chain = await _load_subject_market_and_chain(
            session, deal_id=UUID(deal_id), tenant_id=UUID(tenant_id)
        )
        comp_set = await _build_comp_sales_set(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            subject_market=market,
            subject_chain_scale=chain,
        )
    except Exception:  # noqa: BLE001
        logger.debug("live export: comp sales read failed", exc_info=True)
        return None
    if not hasattr(comp_set, "model_dump"):
        return None
    data = comp_set.model_dump(mode="json")
    if not data.get("transactions"):
        return None
    return data


def _scenario_kpis(engines: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Named-Scenarios KPI row from one run's engine envelopes.

    Year-1 NOI is the first projected year's ``noi``; stabilized NOI is the
    hold-year's ``noi`` (last projected year when the hold exceeds the
    projection) — the same rows the Operating Proforma sheet prints.
    """
    returns = _outputs(engines, "returns")
    expense = _outputs(engines, "expense")
    debt = _outputs(engines, "debt")
    kpis: dict[str, Any] = {}
    _set(kpis, "levered_irr", _num(returns.get("levered_irr")))
    _set(kpis, "equity_multiple", _num(returns.get("equity_multiple")))
    years = expense.get("years") if isinstance(expense.get("years"), list) else []
    by_year: dict[int, dict[str, Any]] = {}
    for y in years:
        if isinstance(y, dict) and y.get("year") is not None:
            try:
                by_year[int(y["year"])] = y
            except (TypeError, ValueError):
                continue
    if by_year:
        _set(kpis, "year1_noi_usd", _num(by_year[min(by_year)].get("noi")))
        hold = int(_num(returns.get("hold_years")) or 0)
        stab_year = hold if hold in by_year else max(by_year)
        _set(kpis, "stabilized_noi_usd", _num(by_year[stab_year].get("noi")))
    _set(kpis, "exit_cap_pct", _num(returns.get("exit_cap_rate")))
    _set(kpis, "year1_dscr", _num(debt.get("year_one_dscr")))
    return kpis


def _scenario_output_row(
    name: str, engines: dict[str, dict[str, Any]], *, is_base: bool
) -> dict[str, Any]:
    """PPTX scenario card (``scenario_outputs`` row) from one run's returns."""
    returns = _outputs(engines, "returns")
    row: dict[str, Any] = {"name": name}
    _set(row, "irr", _num(returns.get("levered_irr")))
    _set(row, "unlevered_irr", _num(returns.get("unlevered_irr")))
    _set(row, "multiple", _num(returns.get("equity_multiple")))
    _set(row, "avg_coc", _num(returns.get("avg_coc")))
    _set(row, "exit_value_usd", _num(returns.get("gross_sale_price")))
    if is_base:
        row["base"] = True
    return row


async def _scenarios(
    session: AsyncSession,
    deal_id: str,
    tenant_id: str,
    canonical: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(named_scenarios, scenario_outputs)`` from the deal's saved scenarios.

    Read-only reuse of the ``compare_scenarios`` read path: each scenario's
    column is its OWN run (``scenarios.last_run_id``), and the Base column is
    pinned to the deal's canonical run (``get_canonical_run_id``) — never the
    base scenario's possibly-stale ``last_run_id``. Scenarios that have never
    been run are skipped (an export never triggers an engine run). When the
    deal predates base-scenario rows, the canonical snapshot itself is the
    Base Case.
    """
    named: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    try:
        from ..services.engine_runner import (
            ENGINE_NAMES,
            get_canonical_run_id,
            get_run_status,
        )
    except Exception:  # noqa: BLE001
        logger.debug("live export: engine_runner import failed", exc_info=True)
        return named, outputs

    try:
        rows = (
            await session.execute(
                text(
                    # tenant-scope predicate required by tenant_middleware
                    """
                    SELECT id, name, description, is_base, last_run_id
                      FROM scenarios
                     WHERE deal_id = :deal AND tenant_id = :tenant
                     ORDER BY is_base DESC, created_at ASC
                    """
                ),
                {"deal": deal_id, "tenant": tenant_id},
            )
        ).fetchall()
    except Exception:  # noqa: BLE001 — scenarios table absent on old DBs
        logger.debug("live export: scenarios read failed", exc_info=True)
        rows = []

    canonical_run: str | None = None
    try:
        canonical_run = await get_canonical_run_id(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
    except Exception:  # noqa: BLE001
        canonical_run = None

    seen_base = False
    for r in rows:
        m = r._mapping
        is_base = bool(m.get("is_base"))
        last_run = str(m["last_run_id"]) if m.get("last_run_id") else None
        run_id = (canonical_run or last_run) if is_base else last_run
        if run_id is None:
            continue
        try:
            run_rows = await get_run_status(
                session, deal_id=deal_id, run_id=run_id, tenant_id=tenant_id
            )
        except Exception:  # noqa: BLE001
            continue
        engines = {
            e["engine"]: e for e in run_rows if e.get("engine") in ENGINE_NAMES
        }
        kpis = _scenario_kpis(engines)
        if not kpis:
            continue
        entry: dict[str, Any] = {
            "name": str(m.get("name") or "—"),
            "is_base": is_base,
            "kpis": kpis,
        }
        _set(entry, "description", m.get("description"))
        named.append(entry)
        if is_base:
            seen_base = True
        out_row = _scenario_output_row(entry["name"], engines, is_base=is_base)
        if "irr" in out_row:
            outputs.append(out_row)

    if not seen_base and _outputs(canonical, "returns"):
        kpis = _scenario_kpis(canonical)
        if kpis:
            named.insert(0, {"name": "Base Case", "is_base": True, "kpis": kpis})
            out_row = _scenario_output_row("Base Case", canonical, is_base=True)
            if "irr" in out_row:
                outputs.insert(0, out_row)
    return named, outputs


async def _historical_baseline(
    session: AsyncSession, deal_id: str, tenant_id: str
) -> dict[str, Any] | None:
    """Live 5-year historical baseline + YoY walk — the engine behind
    ``GET /deals/{id}/historical-baseline`` (extracted T12/PNL docs with a
    fiscal year). ``None`` when coverage is zero. The walk keeps only rows with
    a computable YoY % (first-year / zero-prior rows carry none)."""
    try:
        from ..engines.historical_baseline import (
            baseline_to_dict,
            build_historical_baseline,
            walk_to_list,
            walk_yoy,
        )

        baseline = await build_historical_baseline(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
    except Exception:  # noqa: BLE001
        logger.debug("live export: historical baseline read failed", exc_info=True)
        return None
    if not baseline.years or float(baseline.coverage_pct or 0) <= 0:
        return None
    data = baseline_to_dict(baseline)
    data["walk"] = [
        d for d in walk_to_list(walk_yoy(baseline)) if d.get("yoy_pct") is not None
    ]
    return data


async def _str_forecast(
    session: AsyncSession, deal_id: str, tenant_id: str
) -> dict[str, Any] | None:
    """Live STR forward forecast (W3.3) — ``GET /deals/{id}/str-forecast``:
    the deal's STR_TREND history through the forecast engine with the default
    downside / base / upside scenarios. ``None`` with no STR history on file."""
    try:
        from ..engines.str_forecast import build_str_forecast
        from ..services.str_forecast_loader import load_str_history_for_deal

        history = await load_str_history_for_deal(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        if not history:
            return None
        forecast = build_str_forecast(deal_id=deal_id, historical_months=history)
    except Exception:  # noqa: BLE001
        logger.debug("live export: STR forecast read failed", exc_info=True)
        return None
    data = forecast.model_dump(mode="json")
    if not data.get("historical_months"):
        return None
    return data


async def _engine_base_inputs(
    session: AsyncSession, deal_id: str, tenant_id: str
) -> dict[str, Any]:
    """The resolved assumption set (+ ``__sources__`` provenance map and the
    routed ``capex_plan_overrides``) — ``engine_runner._load_engine_inputs``,
    the same read the ``assumption_sources`` endpoint does. ``{}`` on failure."""
    try:
        from ..services.engine_runner import _load_engine_inputs

        base = await _load_engine_inputs(session, deal_id, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        logger.debug("live export: engine inputs read failed", exc_info=True)
        return {}
    return base if isinstance(base, dict) else {}


def _build_capex_schedule(
    *,
    capital: dict[str, Any],
    expense: dict[str, Any],
    revenue: dict[str, Any],
    returns: dict[str, Any],
    keys: int | None,
    capex_overrides: Any,
) -> list[dict[str, Any]] | None:
    """Capital Plan (3-bucket) rows from LIVE outputs — no default plan.

    * **PIP** — the capital engine's Renovation ``uses`` line. The model books
      it as a closing-day cost, so it lands in Year 1 unless a persisted
      ``capex_plan.pip.timing_pct_by_year`` phases it; an explicit
      ``capex_plan.pip.total_usd`` override wins over the line.
    * **Non-PIP FF&E** — the expense engine's per-year ``ffe_reserve`` (what
      the Operating Proforma actually deducts). When the analyst persisted a
      ``capex_plan.non_pip`` plan (% of revenue / per-key floor), that plan's
      ``max(pct × revenue, floor × keys)`` is used instead — the CapexPlanPanel
      formula with the analyst's own inputs.
    * **ROI** — persisted ``capex_plan.roi_projects`` through the shared pure
      :func:`app.engines.capex_plan.build_capex_schedule` helper; zero otherwise.

    Returns ``None`` when every bucket is zero, so a deal with no renovation,
    no FF&E reserve and no plan gets no sheet (never a table of zeros).
    """
    rev_years = revenue.get("years") if isinstance(revenue.get("years"), list) else []
    exp_years = expense.get("years") if isinstance(expense.get("years"), list) else []
    hold = int(_num(returns.get("hold_years")) or 0) or len(rev_years)
    if hold <= 0:
        return None

    revenue_by_year = [_num(y.get("total_revenue")) or 0.0 for y in rev_years if isinstance(y, dict)]
    ffe_by_year: dict[int, float | None] = {}
    for y in exp_years:
        if isinstance(y, dict) and y.get("year") is not None:
            try:
                ffe_by_year[int(y["year"])] = _num(y.get("ffe_reserve"))
            except (TypeError, ValueError):
                continue

    ovr = capex_overrides if isinstance(capex_overrides, dict) else {}
    pip_ovr = ovr.get("pip") if isinstance(ovr.get("pip"), dict) else {}
    non_pip_ovr = ovr.get("non_pip") if isinstance(ovr.get("non_pip"), dict) else {}
    roi_ovr = ovr.get("roi_projects") if isinstance(ovr.get("roi_projects"), list) else []

    pip_total = _num(pip_ovr.get("total_usd"))
    if pip_total is None:
        uses = capital.get("uses") if isinstance(capital.get("uses"), list) else []
        pip_total = _use_amount(uses, "renovation", "pip")
    timing_raw = pip_ovr.get("timing_pct_by_year")
    timing: list[float] = [1.0]
    if isinstance(timing_raw, list) and timing_raw:
        parsed = [_num(t) for t in timing_raw]
        if all(t is not None for t in parsed):
            timing = [float(t) for t in parsed]  # type: ignore[arg-type]

    # Non-PIP plan (analyst-persisted) — schema defaults fill any field the
    # override map didn't carry, exactly as the engine would construct it.
    plan_non_pip: Any = None
    if non_pip_ovr:
        try:
            from fondok_schemas.underwriting import NonPIPCapex

            plan_non_pip = NonPIPCapex(
                **{k: v for k, v in non_pip_ovr.items() if k in NonPIPCapex.model_fields}
            )
        except Exception:  # noqa: BLE001 — malformed override → fall back to ffe_reserve
            plan_non_pip = None

    # ROI bucket via the shared pure helper (PIP / non-PIP zeroed here — they
    # are sourced above).
    roi_rows: dict[int, Any] = {}
    if roi_ovr:
        try:
            from fondok_schemas.underwriting import CapexPlan, NonPIPCapex, ROICapex

            from ..engines.capex_plan import build_capex_schedule

            projects = [
                ROICapex(**{k: v for k, v in p.items() if k in ROICapex.model_fields})
                for p in roi_ovr
                if isinstance(p, dict)
            ]
            plan = CapexPlan(
                pip=None,
                non_pip=NonPIPCapex(annual_pct_of_revenue=0.0, minimum_per_key_per_year=0.0),
                roi_projects=projects,
            )
            roi_rows = {
                r.year: r
                for r in build_capex_schedule(
                    plan, hold_years=hold, revenue_by_year=[0.0] * hold, room_count=0
                )
            }
        except Exception:  # noqa: BLE001
            logger.debug("live export: ROI capex plan build failed", exc_info=True)
            roi_rows = {}

    schedule: list[dict[str, Any]] = []
    any_nonzero = False
    for y in range(1, hold + 1):
        share = timing[y - 1] if y - 1 < len(timing) else 0.0
        pip_usd = (pip_total or 0.0) * share
        rev_y = revenue_by_year[y - 1] if y - 1 < len(revenue_by_year) else 0.0
        if plan_non_pip is not None:
            non_pip = max(
                max(0.0, rev_y) * float(plan_non_pip.annual_pct_of_revenue),
                max(0, keys or 0) * float(plan_non_pip.minimum_per_key_per_year),
            )
        else:
            non_pip = ffe_by_year.get(y) or 0.0
        roi = roi_rows.get(y)
        roi_inv = float(roi.roi_investment_usd) if roi is not None else 0.0
        roi_lift = float(roi.roi_noi_lift_usd) if roi is not None else 0.0
        total = pip_usd + non_pip + roi_inv
        if total > 0 or roi_lift > 0:
            any_nonzero = True
        schedule.append(
            {
                "year": y,
                "pip_usd": pip_usd,
                "non_pip_usd": non_pip,
                "roi_investment_usd": roi_inv,
                "roi_noi_lift_usd": roi_lift,
                "total_capex_usd": total,
            }
        )
    return schedule if any_nonzero else None


# Op-ratio keys the engine loader resolves (``base['overrides']`` for the
# per-line ratios, top-level for mgmt fee / FF&E) — both the ``pnl_benchmark``
# and ``portfolio_pnl`` maps in engine_runner land on these names.
_OP_RATIO_CATALOG: tuple[tuple[str, str], ...] = (
    ("rooms_dept_pct", "Rooms Dept Exp %"),
    ("fb_dept_pct", "F&B Dept Exp %"),
    ("other_dept_pct", "Other Dept Exp %"),
    ("other_ops_dept_pct", "Other Ops Dept Exp %"),
    ("admin_pct", "A&G %"),
    ("undistributed_pct_revenue", "Undistributed %"),
    ("sales_marketing_pct", "Sales & Marketing %"),
    ("sales_pct", "Sales %"),
    ("marketing_pct", "Marketing %"),
    ("prop_ops_pct", "Property Ops & Maintenance %"),
    ("utilities_pct", "Utilities %"),
    ("fixed_pct_revenue", "Fixed Charges %"),
    ("property_taxes_pct", "Property Tax %"),
    ("property_tax_pct", "Property Tax %"),
    ("insurance_pct", "Insurance %"),
    ("mgmt_fee_pct", "Management Fee %"),
    ("ffe_reserve_pct", "FF&E Reserve %"),
    ("gop_margin", "GOP Margin"),
    ("noi_margin", "NOI Margin"),
)


def _build_op_ratio_provenance(base: dict[str, Any]) -> dict[str, Any] | None:
    """Op-Ratio Provenance rows from the loader's ``__sources__`` map.

    One line per catalog ratio that the loader resolved AND tagged with a
    source (``t12_actual`` / ``portfolio_pnl`` / ``cbre_horizons`` /
    ``pnl_benchmark`` / ``analyst_override`` / ``seed``). Untagged values are
    skipped rather than guessed. The sheet is omitted when every resolved ratio
    is a seed — that would only restate the fixture defaults.
    """
    sources = base.get("__sources__") if isinstance(base.get("__sources__"), dict) else {}
    overrides = base.get("overrides") if isinstance(base.get("overrides"), dict) else {}
    lines: list[dict[str, Any]] = []
    non_seed = False
    for key, label in _OP_RATIO_CATALOG:
        value = _num(overrides.get(key)) if key in overrides else _num(base.get(key))
        if value is None or key not in sources:
            continue
        source = str(sources[key] or "seed")
        if source != "seed":
            non_seed = True
        lines.append({"field": label, "value": value, "source": source, "document_id": None})
    if not lines or not non_seed:
        return None
    return {"lines": lines}


async def _pricing_sheets(
    session: AsyncSession,
    deal_id: str,
    tenant_id: str,
    *,
    asset_name: str,
    asset_address: str,
    rooms: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    """``(sensitivity_grid, max_price, loi_draft)`` — the Pricing panel's math.

    Reuses ``analysis._build_returns_input_for_deal`` (the read-only in-memory
    chain walk behind ``POST /analysis/{id}/pricing/*``) and the three
    endpoints' request-model defaults (target IRR / EM; template LOI terms).
    The LOI is the same one-click draft the panel renders: the proposed price
    is the live max-price-for-IRR solve; buyer / seller / EMD / DD days are the
    generator's documented placeholders, not deal data — the analyst edits
    them in the panel. Any of the three is ``None`` on failure.
    """
    try:
        from ..api.analysis import (
            _LOIRequest,
            _MaxPriceRequest,
            _SensitivityRequest,
            _build_returns_input_for_deal,
        )
        from ..engines.loi_generator import draft_loi
        from ..engines.price_solver import solve_max_price
        from ..engines.pricing_sensitivity import run_sensitivity_grid

        base_input = await _build_returns_input_for_deal(
            session, deal_id=UUID(deal_id), tenant_id=UUID(tenant_id)
        )
    except Exception:  # noqa: BLE001
        logger.debug("live export: pricing base input failed", exc_info=True)
        return None, None, None

    grid_d: dict[str, Any] | None = None
    try:
        sens_req = _SensitivityRequest()
        kwargs = {
            k: getattr(sens_req, k)
            for k in ("target_irr", "cap_axis", "noi_axis")
            if hasattr(sens_req, k)
        }
        grid = run_sensitivity_grid(base_input, **kwargs)
        grid_d = asdict(grid)
        _set(grid_d, "target_irr", kwargs.get("target_irr"))
        if not grid_d.get("cells"):
            grid_d = None
    except Exception:  # noqa: BLE001
        logger.debug("live export: sensitivity grid failed", exc_info=True)
        grid_d = None

    mp_d: dict[str, Any] | None = None
    mpr: Any = None
    try:
        mp_req = _MaxPriceRequest()
        mpr = solve_max_price(
            base_input,
            target_irr=mp_req.target_irr,
            target_em=mp_req.target_em,
            rooms=rooms or None,
        )
        mp_d = asdict(mpr)
    except Exception:  # noqa: BLE001
        logger.debug("live export: max-price solve failed", exc_info=True)
        mpr = None
        mp_d = None

    loi_d: dict[str, Any] | None = None
    if mpr is not None and rooms > 0:
        try:
            loi_req = _LOIRequest()
            loi_kwargs = {
                k: v
                for k, v in loi_req.model_dump().items()
                if k not in ("target_irr", "target_em")
            }
            draft = draft_loi(
                asset_name=asset_name,
                asset_address=asset_address,
                rooms=rooms,
                max_price_result=mpr,
                **loi_kwargs,
            )
            loi_d = asdict(draft)
            loi_d["binding_constraint"] = mpr.binding_constraint
            if not (loi_d.get("rendered_markdown") or "").strip():
                loi_d = None
        except Exception:  # noqa: BLE001
            logger.debug("live export: LOI draft failed", exc_info=True)
            loi_d = None

    return grid_d, mp_d, loi_d


# ─────────────────────────── public entrypoint ───────────────────────────


async def load_live_payload(
    session: AsyncSession, deal_id: str, tenant_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return ``(deal, model, memo)`` for a live deal in the builder shape.

    Degrades gracefully: a deal with no completed engine run yields empty engine
    blocks (builders render ``—`` / empty sections); a deal with no cached memo
    yields a header-only memo. Never raises for missing data.
    """
    from ..api.market import _extracted_property_meta
    from ..services.engine_runner import get_run_scoped_outputs

    row = await _deal_row(session, deal_id, tenant_id)
    deal_name = str((row or {}).get("name") or "")
    city = (row or {}).get("city")
    brand = (row or {}).get("brand")
    service = (row or {}).get("service")
    deal_stage = (row or {}).get("deal_stage") or (row or {}).get("status")
    ai_confidence = _num((row or {}).get("ai_confidence"))
    keys_val = (row or {}).get("keys")
    try:
        keys = int(keys_val) if keys_val is not None else None
    except (TypeError, ValueError):
        keys = None

    # field_overrides blob (memo_* verdict/thesis/highlights/risks live here).
    raw_ovr = (row or {}).get("field_overrides")
    if isinstance(raw_ovr, str):
        try:
            overrides = json.loads(raw_ovr)
        except json.JSONDecodeError:
            overrides = {}
    elif isinstance(raw_ovr, dict):
        overrides = raw_ovr
    else:
        overrides = {}

    # Extracted subject-property name (OM-first) — the hotel's real name, used as
    # the export title; distinct from the project name (deal_name).
    property_name: str | None = None
    year_built: Any = None
    try:
        meta = await _extracted_property_meta(
            session, deal_id=UUID(deal_id), tenant_id=UUID(tenant_id)
        )
        property_name = meta.get("name")
        year_built = meta.get("year_built")
    except Exception:  # noqa: BLE001 — property meta is best-effort
        logger.debug("live export: property meta read failed", exc_info=True)

    # Canonical-run engine snapshot ({engine: {"outputs": {...}}}).
    snapshot = await get_run_scoped_outputs(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    revenue = _outputs(snapshot, "revenue")
    expense = _outputs(snapshot, "expense")
    capital = _outputs(snapshot, "capital")
    debt = _outputs(snapshot, "debt")
    returns = _outputs(snapshot, "returns")
    partnership = _outputs(snapshot, "partnership")

    proforma, cash_flow_engine, noi_y1 = _build_proforma_and_cf(revenue, expense, debt)

    model: dict[str, Any] = {
        "deal_id": deal_id,
        "deal_name": deal_name,
        "model_version": "fondok-engine-live",
    }
    if keys is not None:
        model["keys"] = keys
    if brand:
        model["brand"] = brand
    if city:
        model["location"] = city

    investment = _build_investment(capital, keys, noi_y1)
    if investment:
        model["investment_engine"] = investment
    if proforma:
        model["p_and_l_engine_proforma"] = proforma
    debt_block = _build_debt(debt, capital)
    if debt_block:
        model["debt_engine"] = debt_block
    returns_block = _build_returns(returns)
    if returns_block:
        model["returns_engine"] = returns_block
    if cash_flow_engine:
        model["cash_flow_engine"] = cash_flow_engine
    partnership_block = _build_partnership(partnership)
    if partnership_block:
        model["partnership_engine"] = partnership_block

    sources, uses = _build_sources_uses(capital)
    if sources:
        model["sources"] = sources
    if uses:
        model["uses"] = uses

    segments = _build_segments_by_year(revenue)
    if segments:
        model["segments_by_year"] = segments

    model["market"] = {"kpis": await _market_kpis(session, deal_id, tenant_id, city=city)}

    # ── Wave 2/3 conditional sheets + PPTX scenario cards. Each key is set
    # ── only when the live deal has data for it (see the helper block above);
    # ── the builders skip absent sheets. The modeled sheets (scenarios, capex,
    # ── pricing) additionally require a completed run so they stay consistent
    # ── with the Returns / Proforma sheets exported from the same snapshot.
    variance_flags = await _variance_flags(session, deal_id, tenant_id)
    if variance_flags:
        model["variance_flags"] = variance_flags

    market_comps = await _market_comps(session, deal_id, tenant_id)
    if market_comps:
        model["market_comps"] = market_comps

    comp_sales = await _comp_sales(session, deal_id, tenant_id)
    if comp_sales:
        model["comp_sales"] = comp_sales

    historical = await _historical_baseline(session, deal_id, tenant_id)
    if historical:
        model["historical_baseline"] = historical

    str_forecast = await _str_forecast(session, deal_id, tenant_id)
    if str_forecast:
        model["str_forecast"] = str_forecast

    if returns:
        named_scenarios, scenario_outputs = await _scenarios(
            session, deal_id, tenant_id, snapshot
        )
        if named_scenarios:
            model["named_scenarios"] = named_scenarios
        if scenario_outputs:
            model["scenario_outputs"] = scenario_outputs

        engine_base = await _engine_base_inputs(session, deal_id, tenant_id)
        capex = _build_capex_schedule(
            capital=capital,
            expense=expense,
            revenue=revenue,
            returns=returns,
            keys=keys,
            capex_overrides=engine_base.get("capex_plan_overrides"),
        )
        if capex:
            model["capex_schedule"] = capex
        op_prov = _build_op_ratio_provenance(engine_base)
        if op_prov:
            model["op_ratio_provenance"] = op_prov

        grid, max_price, loi = await _pricing_sheets(
            session,
            deal_id,
            tenant_id,
            asset_name=property_name or deal_name or "—",
            asset_address=city or "—",
            rooms=keys or 0,
        )
        if grid:
            model["sensitivity_grid"] = grid
        if max_price:
            model["max_price"] = max_price
        if loi:
            model["loi_draft"] = loi

    # ── Deal dict (title + property/market slides). String-safe values with
    # ── "—" for absent fields so the deck never shows "None" or a fixture
    # ── default (e.g. the pptx "Lifestyle Boutique" service fallback).
    deal: dict[str, Any] = {
        "id": deal_id,
        "name": property_name or deal_name or "Hotel",
        "city": city or "—",
        "location": city or "—",
        "brand": brand or "—",
        "keys": keys if keys is not None else "—",
        "year_built": year_built if year_built is not None else "—",
        "service": service or "—",
    }

    # ── Memo (cache snapshot + analyst overrides + real docs).
    from ..streaming.broadcast import get_memo_cache

    snapshot_memo = await get_memo_cache().get(deal_id)
    live_sections = (snapshot_memo or {}).get("sections") or []
    documents_reviewed = await _documents_reviewed(session, deal_id, tenant_id)
    engines_run = [name for name in snapshot if _outputs(snapshot, name)]

    memo = _build_memo(
        live_sections=live_sections,
        overrides=overrides,
        property_name=property_name,
        deal_name=deal_name,
        location=city,
        keys=keys,
        deal_stage=deal_stage,
        ai_confidence=ai_confidence,
        engines_run=engines_run,
        documents_reviewed=documents_reviewed,
    )

    return deal, model, memo


__all__ = ["load_live_payload"]
