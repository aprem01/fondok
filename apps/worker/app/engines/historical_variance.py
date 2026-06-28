"""Historical variance engine — YoY deltas on the property's OWN financials.

This is **roadmap item #4**: deterministic year-over-year variance
detection that drafts a copy-paste-ready broker question whenever a
P&L line moves more than its threshold.

Distinct from ``apps/worker/app/agents/variance.py``
----------------------------------------------------

``agents/variance.py`` compares the broker's proforma against the
T-12 actuals (LLM-orchestrated, broker-comparison). This engine
compares consecutive years of the property's OWN historical
financials (deterministic, no LLM, no I/O).

Thresholds (confirmed by Eshan on 2026-06-25)
---------------------------------------------

* 10% YoY on departmental revenue/expenses (rooms, F&B dept, other operated)
* 15% YoY on F&B specifically (higher scrutiny per Eshan)
* 20% YoY on Other Operated departments
* 5% YoY on NOI / GOP / Total Revenue (stricter — rolled-up metrics)

Severity ladder
---------------

* ``CRITICAL`` — ``abs(variance) > 2 × threshold``
* ``WARN``     — ``abs(variance) > threshold``
* otherwise   — not emitted

Question text template (Eshan's framing, productized)
-----------------------------------------------------

``"{LineItemLabel} {direction} {pct}% YoY ({prior_year}: {prior_$} →
{current_year}: {current_$}). What drove this swing?"``

* Currency formatted as ``${:,.0f}``
* Percent formatted as ``{:.1%}``
* Direction: ``"increased"`` (positive) or ``"declined"`` (negative)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ──────────────────────────── thresholds ─────────────────────────────


@dataclass(frozen=True)
class VarianceThreshold:
    """One (line_item, threshold) pair. Kept as a dataclass so callers
    can iterate and project the rule catalog into UI tooltips.
    """

    line_item: str
    threshold_pct: float


# Canonical thresholds — see module docstring for Eshan's confirmation.
# Keys are the canonical line-item slugs the engine works in (snake-case,
# USALI-aligned). ``_LINE_ITEM_ALIASES`` below maps the raw extraction
# field names onto these.
VARIANCE_THRESHOLDS: dict[str, float] = {
    # departmental revenue + expense — 10%
    "rooms_revenue": 0.10,
    "rooms_dept_expense": 0.10,
    # F&B specifically — 15% (Eshan's example used 15%)
    "fb_revenue": 0.15,
    "fb_dept_expense": 0.15,
    # other operated — 20% (lower-volume, noisier lines)
    "other_operated_revenue": 0.20,
    "other_operated_expense": 0.20,
    # rolled-up metrics — 5% (each $ counts; small drifts matter)
    "noi": 0.05,
    "gop": 0.05,
    "total_revenue": 0.05,
}


# Human-readable labels rendered in ``question_text``. Keeping these
# alongside the threshold map (not inside the dataclass) so adding a new
# line-item only requires touching the two dicts.
_LINE_ITEM_LABELS: dict[str, str] = {
    "rooms_revenue": "Rooms revenue",
    "rooms_dept_expense": "Rooms department expense",
    "fb_revenue": "F&B revenue",
    "fb_dept_expense": "F&B department expense",
    "other_operated_revenue": "Other operated revenue",
    "other_operated_expense": "Other operated expense",
    "noi": "NOI",
    "gop": "GOP",
    "total_revenue": "Total revenue",
}


# Tolerant key aliasing: the historical P&L extractions arrive with a
# few distinct naming conventions across OM-embedded summaries, the
# T-12 schema (``p_and_l_usali.*``), and PNL benchmarks. We canonicalize
# at the engine boundary so the threshold map stays small.
_LINE_ITEM_ALIASES: dict[str, str] = {
    # rooms
    "rooms_revenue": "rooms_revenue",
    "rooms_revenue_usd": "rooms_revenue",
    "p_and_l_usali.operating_revenue.rooms_revenue": "rooms_revenue",
    "rooms_department_revenue": "rooms_revenue",
    "rooms": "rooms_revenue",
    "rooms_dept_expense": "rooms_dept_expense",
    "rooms_department_expense": "rooms_dept_expense",
    "p_and_l_usali.departmental_expenses.rooms": "rooms_dept_expense",
    # F&B revenue
    "fb_revenue": "fb_revenue",
    "f_and_b_revenue": "fb_revenue",
    "food_beverage": "fb_revenue",
    "food_beverage_revenue": "fb_revenue",
    "food_and_beverage_revenue": "fb_revenue",
    "p_and_l_usali.operating_revenue.food_beverage_revenue": "fb_revenue",
    # F&B expense
    "fb_dept_expense": "fb_dept_expense",
    "fb_department_expense": "fb_dept_expense",
    "food_beverage_expense": "fb_dept_expense",
    "p_and_l_usali.departmental_expenses.food_beverage": "fb_dept_expense",
    # other operated
    "other_operated_revenue": "other_operated_revenue",
    "other_operated_departments_revenue": "other_operated_revenue",
    "p_and_l_usali.operating_revenue.other_revenue": "other_operated_revenue",
    "other_operated_expense": "other_operated_expense",
    "other_operated_departments_expense": "other_operated_expense",
    "p_and_l_usali.departmental_expenses.other_operated": "other_operated_expense",
    # rolled-up
    "noi": "noi",
    "noi_usd": "noi",
    "net_operating_income": "noi",
    "p_and_l_usali.net_operating_income.noi_usd": "noi",
    "gop": "gop",
    "gop_usd": "gop",
    "gross_operating_profit": "gop",
    "total_revenue": "total_revenue",
    "total_revenue_usd": "total_revenue",
    "p_and_l_usali.operating_revenue.total_revenue": "total_revenue",
}


# ────────────────────────── data structures ──────────────────────────


@dataclass
class YoYVarianceFinding:
    """One above-threshold YoY change on a single line-item.

    Returned by ``detect_yoy_variances`` and trivially projected into a
    ``BrokerQuestion`` row by the API layer (the engine itself never
    touches the DB or the UUID minting — kept pure-functional).
    """

    line_item: str
    period_key: str
    variance_pct: float
    actual_prior: float
    actual_current: float
    threshold_pct: float
    severity: str  # "CRITICAL" | "WARN"
    question_text: str


# ────────────────────────────── helpers ──────────────────────────────


def _canonicalize(key: str) -> str | None:
    """Map a raw extraction key (or path-style field name) to the
    canonical line-item slug. Returns ``None`` if the key doesn't
    correspond to a tracked line-item.

    The match is case-insensitive and tolerant of an ``_usd`` suffix —
    OM and T-12 extractions sometimes carry the unit in the key tail.
    """
    if not key:
        return None
    lowered = key.strip().lower()
    if lowered in _LINE_ITEM_ALIASES:
        return _LINE_ITEM_ALIASES[lowered]
    # Strip a trailing ``_usd`` and retry — common in OM exports.
    if lowered.endswith("_usd"):
        trimmed = lowered[:-4]
        if trimmed in _LINE_ITEM_ALIASES:
            return _LINE_ITEM_ALIASES[trimmed]
    return None


def _extract_year(pnl: dict[str, Any]) -> int | None:
    """Pick a calendar year off a flat P&L dict.

    Accepts any of these keys (first hit wins):

    * ``year`` — preferred, set explicitly by the loader
    * ``fiscal_year`` — matches ``documents.fiscal_year``
    * ``period_label`` — e.g. ``"FY2023"``, ``"Year Ended December 31, 2024"``
    * ``period_ending`` — ISO-ish date string

    Returns ``None`` when no year can be determined; the caller drops
    such entries rather than risk pairing the wrong years.
    """
    for key in ("year", "fiscal_year"):
        v = pnl.get(key)
        if isinstance(v, int) and 1900 < v < 2100:
            return v
        if isinstance(v, str) and v.isdigit() and 1900 < int(v) < 2100:
            return int(v)
    for key in ("period_label", "period_ending", "period"):
        v = pnl.get(key)
        if isinstance(v, str):
            # Greedy 4-digit year match — fine for our inputs (no
            # property is going to have a 1900s-era P&L in scope).
            # Uses a regex so concatenated tokens like ``"FY2024"`` or
            # ``"FYE12/31/2024"`` still resolve.
            for match in re.finditer(r"(\d{4})", v):
                token = match.group(1)
                if 1900 < int(token) < 2100:
                    return int(token)
    return None


def _coerce_value(raw: Any) -> float | None:
    """Best-effort numeric coercion. Returns ``None`` on garbage so the
    engine cleanly skips a missing/non-numeric line rather than raise.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool is a subclass of int — skip
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        cleaned = raw.replace(",", "").replace("$", "").strip()
        # Parenthesized negatives — accountants love these.
        if cleaned.startswith("(") and cleaned.endswith(")"):
            cleaned = "-" + cleaned[1:-1]
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _format_question(
    line_item: str,
    prior_year: int,
    current_year: int,
    prior_val: float,
    current_val: float,
    variance_pct: float,
) -> str:
    """Render the broker-facing question. Format spec lives in the
    module docstring; we keep this helper so the engine and the tests
    share one source of truth on the template.
    """
    label = _LINE_ITEM_LABELS.get(line_item, line_item.replace("_", " ").title())
    direction = "increased" if variance_pct >= 0 else "declined"
    return (
        f"{label} {direction} {abs(variance_pct):.1%} YoY "
        f"({prior_year}: ${prior_val:,.0f} → {current_year}: ${current_val:,.0f}). "
        f"What drove this swing?"
    )


def _normalize_pnl(pnl: dict[str, Any]) -> dict[str, float]:
    """Project a flat P&L dict into ``{canonical_line_item: value}``.

    Resolution chain (Sam QA Bug #3 v3, June 2026):

    1. **Direct alias** — ``_LINE_ITEM_ALIASES`` covers the schema-doc
       paths and the bare canonical names.
    2. **USALI scorer fallback** — the canonical line items the engine
       tracks (``rooms_revenue``, ``gop``, ``noi``, …) are a strict
       subset of the canonicals the scorer's ``_resolve_field``
       resolves. Reusing it picks up the prod paths
       (``p_and_l_usali.rooms.revenue_usd``,
       ``p_and_l_usali.gross_operating_profit_usd``, …) the engine's
       own alias map doesn't enumerate. This is the same v3
       token-aware resolver that unblocked the USALI compliance score
       — the broker-questions panel now benefits too.

    Drops keys that don't map to a tracked line-item, drops non-numeric
    values, deduplicates aliases (last-write-wins is fine — the loader
    is expected to dedupe upstream; this is a safety net).
    """
    out: dict[str, float] = {}
    for raw_key, raw_val in pnl.items():
        canonical = _canonicalize(str(raw_key))
        if canonical is None:
            continue
        coerced = _coerce_value(raw_val)
        if coerced is None:
            continue
        out[canonical] = coerced

    # USALI scorer fallback: for every tracked line item that didn't
    # resolve via the direct alias map, ask the scorer's resolver to
    # find it. The scorer's canonical names differ slightly from the
    # engine's (``rooms_dept_expense`` here ↔ ``rooms_dept_expense``
    # there — they match for the tracked subset; ``other_operated_*``
    # here maps to ``other_revenue`` / ``other_dept_expense`` on the
    # scorer side — handled below).
    from ..services.usali_scorer import _resolve_field as _scorer_resolve

    scorer_canonical_map = {
        "rooms_revenue": "rooms_revenue",
        "rooms_dept_expense": "rooms_dept_expense",
        "fb_revenue": "fb_revenue",
        "fb_dept_expense": "fb_dept_expense",
        "other_operated_revenue": "other_revenue",
        "other_operated_expense": "other_dept_expense",
        "noi": "noi",
        "gop": "gop",
        "total_revenue": "total_revenue",
    }
    for engine_canonical, scorer_canonical in scorer_canonical_map.items():
        if engine_canonical in out:
            continue
        v = _scorer_resolve(pnl, scorer_canonical)
        coerced = _coerce_value(v)
        if coerced is not None:
            out[engine_canonical] = coerced
    return out


# ────────────────────────────── engine ───────────────────────────────


def detect_yoy_variances(
    historical_pnls: list[dict[str, Any]],
) -> list[YoYVarianceFinding]:
    """Walk consecutive year pairs and emit findings per line per threshold.

    ``historical_pnls`` is expected to be a list of flat P&L dicts (one
    per fiscal year) carrying the line-item amounts plus a year-bearing
    field (``year`` / ``fiscal_year`` / ``period_label`` / ``period_ending``).
    The engine:

    1. Drops entries with no resolvable year.
    2. Sorts the remaining entries ascending by year.
    3. Walks consecutive pairs ``(Y_n, Y_{n+1})`` and computes
       ``(current - prior) / prior`` for every tracked line-item that
       appears in BOTH pairs with non-zero prior.
    4. Emits a finding whenever ``abs(variance) > threshold``, tagging
       ``CRITICAL`` past ``2 × threshold``, ``WARN`` otherwise.

    A 3-year input (Y1, Y2, Y3) produces two ``period_key`` slots
    (``"Y1_vs_Y2"`` and ``"Y2_vs_Y3"``) — one finding per (line, pair)
    over threshold.
    """
    # Year-resolved, normalized rows ordered ascending.
    annotated: list[tuple[int, dict[str, float]]] = []
    for pnl in historical_pnls:
        if not isinstance(pnl, dict):
            continue
        year = _extract_year(pnl)
        if year is None:
            continue
        normalized = _normalize_pnl(pnl)
        if not normalized:
            continue
        annotated.append((year, normalized))
    annotated.sort(key=lambda pair: pair[0])

    findings: list[YoYVarianceFinding] = []
    for i in range(len(annotated) - 1):
        prior_year, prior_row = annotated[i]
        current_year, current_row = annotated[i + 1]
        # Guard against duplicate-year inputs (would yield a 0-delta /
        # nonsense period_key). The loader should dedupe upstream;
        # belt-and-braces here.
        if prior_year == current_year:
            continue
        period_key = f"{prior_year}_vs_{current_year}"

        # Stable iteration order = test ergonomics.
        for line_item, threshold in VARIANCE_THRESHOLDS.items():
            if line_item not in prior_row or line_item not in current_row:
                continue
            prior_val = prior_row[line_item]
            current_val = current_row[line_item]
            if prior_val == 0:
                # Can't compute a percentage off a zero baseline; the
                # business signal here ("the line went from nil to
                # something") deserves a different rule than YoY %
                # variance — leave that to a follow-up.
                continue
            variance_pct = (current_val - prior_val) / prior_val
            magnitude = abs(variance_pct)
            if magnitude <= threshold:
                continue
            severity = "CRITICAL" if magnitude > 2 * threshold else "WARN"
            question_text = _format_question(
                line_item=line_item,
                prior_year=prior_year,
                current_year=current_year,
                prior_val=prior_val,
                current_val=current_val,
                variance_pct=variance_pct,
            )
            findings.append(
                YoYVarianceFinding(
                    line_item=line_item,
                    period_key=period_key,
                    variance_pct=variance_pct,
                    actual_prior=prior_val,
                    actual_current=current_val,
                    threshold_pct=threshold,
                    severity=severity,
                    question_text=question_text,
                )
            )
    return findings


__all__ = [
    "VARIANCE_THRESHOLDS",
    "VarianceThreshold",
    "YoYVarianceFinding",
    "detect_yoy_variances",
]
