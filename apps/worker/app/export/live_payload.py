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
