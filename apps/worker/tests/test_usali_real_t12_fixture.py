"""USALI scoring against REAL production extraction payloads.

Sam QA Bug #3 v2 (June 2026): the v1 fix expanded the alias map and
added ``_derive_usali_rollups``, but Sam reported that BOTH rich
T-12 (213 fields @ 88% confidence) and annual P&L (144 fields @ 90%)
still scored gray "Inconclusive — too few applicable rules."

Root cause: v1's alias map was written against the schema-doc paths
(``p_and_l_usali.operating_revenue.rooms_revenue``,
``p_and_l_usali.departmental_expenses.rooms``). The REAL prod
Extractor emits ENTIRELY DIFFERENT paths — verified by querying prod:

  curl https://fondok-worker-production.up.railway.app/deals/
       4e6b96ec-32d1-47fa-8279-7c5fbe7a9776/documents/
       0c8fd0e5-c4bd-40ff-8213-037f06fb4471/extraction

The actual T-12 paths:
  * ``p_and_l_usali.rooms.revenue_usd``         (NOT operating_revenue.rooms_revenue)
  * ``p_and_l_usali.food_and_beverage.revenue_usd``
  * ``p_and_l_usali.total_revenues_usd``        (already canonical!)
  * ``p_and_l_usali.gross_operating_profit_usd`` (direct)
  * ``p_and_l_usali.management_fees_usd``       (direct, not fees_and_reserves)
  * ``p_and_l_usali.ffe_replacement_reserve_usd`` (not ffe_reserve)
  * ``p_and_l_usali.non_operating.insurance_usd`` (not fixed_charges.insurance)
  * ``ttm_summary_per_om.{occupancy,adr,revpar}_usd`` (not bare)

The annual P&L emits a different shape STILL:
  * ``p_and_l_usali.revenues.rooms_usd``  (under .revenues bucket)
  * ``p_and_l_usali.departmental_expense.rooms_usd``
  * ``p_and_l_usali.gross_operating_profit.total_usd``
  * ``p_and_l_usali.management_fees.total_usd``
  * ``p_and_l_usali.ffe_reserve.proforma_calculation_usd``

Both payloads are saved as fixtures so this test will fail loud the
moment the Extractor flavor shifts again.

These tests pin the contract:

* The real T-12 payload yields ``applicable_count >= 15`` (a healthy
  P&L should hit at least the revenue identity + GOP/NOI margins +
  the per-key fees + the ops-KPI sanity checks).
* The real annual P&L payload yields ``applicable_count >= 15`` too.
* Both yield a non-None ``score`` (not "Inconclusive").
* Resolution: every canonical name the catalog uses
  (``total_revenue``, ``gop``, ``rooms_dept_expense``,
  ``mgmt_fee``, ``ffe_reserve``, ``insurance_expense``,
  ``property_tax``, ``occupancy``, ``adr``, ``revpar``)
  resolves to a real number against the prod payload.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_payloads"


def _load_payload(name: str) -> list[dict]:
    """Load a saved prod-extraction fixture's ``fields`` list."""
    payload_path = _FIXTURES_DIR / name
    if not payload_path.exists():
        pytest.skip(f"missing real-payload fixture {payload_path}")
    payload = json.loads(payload_path.read_text())
    fields = payload.get("fields") or []
    assert isinstance(fields, list) and len(fields) > 100, (
        f"fixture {name} doesn't look like a rich extraction "
        f"({len(fields)} fields)"
    )
    return fields


# ─────────────────────────── alias resolution ───────────────────────────


def test_real_t12_resolves_canonical_revenue_paths() -> None:
    """Every canonical revenue path the catalog cites must resolve
    (else the GOP_MARGIN_RANGE / NOI_MARGIN_RANGE rules can't fire)."""
    from app.services.usali_scorer import flatten_extraction_fields

    flat = flatten_extraction_fields(_load_payload("anglers_t12_real.json"))

    # Resolve via the alias map — using the public ``_resolve_field`` API.
    from app.services.usali_scorer import _resolve_field

    canonicals = [
        "total_revenue",
        "rooms_revenue",
        "fb_revenue",
        "other_revenue",
        "gop",
        "mgmt_fee",
        "ffe_reserve",
        "insurance_expense",
        "property_tax",
        "rooms_dept_expense",
        "fb_dept_expense",
        "ag_expense",
        "marketing_expense",
        "rm_expense",
        "utilities_expense",
        "undistributed_expenses",
        "occupancy",
        "adr",
        "revpar",
    ]
    unresolved: list[str] = []
    for canonical in canonicals:
        value = _resolve_field(flat, canonical)
        if value is None:
            unresolved.append(canonical)
    assert not unresolved, (
        f"Real prod T-12 left these canonicals UNRESOLVED: {unresolved}. "
        f"Alias map / synthesis must cover them so the rule catalog can "
        f"score them. Flat keys (first 40): "
        f"{sorted(flat.keys())[:40]}"
    )


def test_real_annual_pnl_resolves_canonical_revenue_paths() -> None:
    """Same canonical-resolution check against the annual P&L
    extraction — its paths differ from the T-12 (revenues bucket)."""
    from app.services.usali_scorer import (
        _resolve_field,
        flatten_extraction_fields,
    )

    flat = flatten_extraction_fields(_load_payload("anglers_annual_pnl_real.json"))

    canonicals = [
        "total_revenue",
        "rooms_revenue",
        "fb_revenue",
        "other_revenue",
        "gop",
        "mgmt_fee",
        "ffe_reserve",
        "insurance_expense",
        "property_tax",
        "rooms_dept_expense",
        "fb_dept_expense",
        "ag_expense",
        "marketing_expense",
        "rm_expense",
        "utilities_expense",
        "undistributed_expenses",
        "occupancy",
        "adr",
        "revpar",
    ]
    unresolved: list[str] = []
    for canonical in canonicals:
        value = _resolve_field(flat, canonical)
        if value is None:
            unresolved.append(canonical)
    assert not unresolved, (
        f"Real prod annual P&L left these canonicals UNRESOLVED: "
        f"{unresolved}. Alias map / synthesis must cover them."
    )


# ─────────────────────────── headline assertion ───────────────────────────


def test_real_t12_payload_scores_with_at_least_15_applicable_rules() -> None:
    """The Sam-reported regression: a 213-field T-12 must NOT score
    "Inconclusive — too few applicable rules". Pre-v2 fix this asserts
    fail (was scoring ~3-4 applicable). Post-v2 fix the rich payload
    should activate the revenue / GOP / NOI identity + the per-dept
    margins + the ratio rules — comfortably above the floor of 15.
    """
    from app.services.usali_scorer import (
        flatten_extraction_fields,
        score_extraction,
    )

    fields = _load_payload("anglers_t12_real.json")
    flat = flatten_extraction_fields(
        fields,
        # Deal-level context — real prod merges this from the deals
        # row (keys, brand, etc.) before calling the scorer.
        extra_context={"keys": 132},
    )
    result = score_extraction(flat)

    assert result.applicable_count >= 15, (
        f"only {result.applicable_count} rules applied to Sam's REAL "
        f"prod T-12 (213 fields); v2 fix should push that above 15. "
        f"Flat keys: {sorted(flat.keys())[:50]}"
    )
    assert not result.inconclusive, (
        f"scorer flagged inconclusive at {result.applicable_count} "
        f"applicable rules on real T-12 — Bug #3 v2 regression"
    )
    assert result.score is not None, (
        "score is None on a 213-field T-12; the v2 fix should "
        "produce a numeric score on this payload"
    )


def test_real_annual_pnl_payload_scores_with_at_least_15_applicable_rules() -> None:
    """Sam's matching regression on the annual P&L (144 fields). The
    Extractor emits a totally different bucket shape from the T-12
    (``revenues.*`` instead of ``rooms.revenue_usd``). The alias map
    must cover both."""
    from app.services.usali_scorer import (
        flatten_extraction_fields,
        score_extraction,
    )

    fields = _load_payload("anglers_annual_pnl_real.json")
    flat = flatten_extraction_fields(
        fields,
        extra_context={"keys": 132},
    )
    result = score_extraction(flat)

    assert result.applicable_count >= 15, (
        f"only {result.applicable_count} rules applied to Sam's REAL "
        f"prod annual P&L (144 fields). Flat keys: "
        f"{sorted(flat.keys())[:50]}"
    )
    assert not result.inconclusive
    assert result.score is not None


# ─────────────────────────── derivation sanity ───────────────────────────


def test_real_t12_total_revenue_resolves_directly() -> None:
    """The T-12 emits ``p_and_l_usali.total_revenues_usd`` directly;
    the synthesis path must defer to it (not double-count). Asserts
    the alias resolves the prod field as-is and roughly matches the
    sum of the components."""
    from app.services.usali_scorer import (
        _resolve_field,
        flatten_extraction_fields,
    )

    flat = flatten_extraction_fields(_load_payload("anglers_t12_real.json"))
    total = _resolve_field(flat, "total_revenue")
    assert total is not None, "total_revenue must resolve on the real T-12"
    # From the prod payload: 14_009_800 USD reported.
    assert 13_000_000 <= float(total) <= 15_000_000, (
        f"total_revenue resolved to {total!r} — expected ~14M USD"
    )


def test_real_annual_pnl_gop_resolves() -> None:
    """The annual P&L emits ``p_and_l_usali.gross_operating_profit.total_usd``
    directly. The alias map must hit it (else GOP_MARGIN_RANGE skips)."""
    from app.services.usali_scorer import (
        _resolve_field,
        flatten_extraction_fields,
    )

    flat = flatten_extraction_fields(_load_payload("anglers_annual_pnl_real.json"))
    gop = _resolve_field(flat, "gop")
    assert gop is not None, "gop must resolve on the real annual P&L"
    # From the prod payload: 4_736_470 USD.
    assert 4_000_000 <= float(gop) <= 5_500_000, (
        f"gop resolved to {gop!r} — expected ~4.7M USD"
    )
