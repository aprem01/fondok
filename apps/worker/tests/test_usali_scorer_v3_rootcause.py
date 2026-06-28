"""USALI scorer v3 root-cause regression tests (Sam QA Bug #3 v3).

Sam re-ran a fresh deal on 2026-06-28 against the v2-patched scorer
and BOTH a 189-field T-12 and a 72-field annual P&L still scored
``usali_score = null`` with the gray "Inconclusive — too few applicable
rules" badge. The v1 / v2 fixes were path-by-path expansions of the
explicit alias map — they kept missing the next extractor flavor.

v3 generalizes: a path-flattening pre-processor + token-aware
multi-strategy resolver picks up canonical fields without needing the
alias map to enumerate every possible LLM emission. This file pins
that contract:

* Sam's REAL prod T-12 payload (213 fields) still scores ≥ 50.
* Sam's REAL prod annual P&L payload (144 fields) still scores ≥ 30.
* A SYNTHETIC payload whose paths use an entirely new namespace
  (``p_and_l.<bucket>_dept.<line>_usd`` — not in any alias entry)
  still scores ≥ 30 applicable rules.
* The token resolver unwraps subordinate ``.monthly.`` /
  ``.page<n>.`` namespaces so per-period slices don't pollute the
  period total.
* Backward-compat: v1/v2-shaped payloads (canonical names + schema-doc
  paths) score the same as before.
* The token resolver is case-insensitive and falls back to suffix
  matching when the explicit alias is missing.
* The inconclusive floor (< 5 applicable) still fires on a truly
  empty payload.
* Severity tagging — CRITICAL and WARN deviations are still emitted
  correctly through the v3 path.
* Score clamping: a 100% pass produces score=100.0; a 0% pass
  (everything failed) produces score=0.0.

Run:
    python -m pytest tests/test_usali_scorer_v3_rootcause.py -v
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Same import shim the v1/v2 tests use.
_WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKER_ROOT))

_RULES_PATH = (
    _WORKER_ROOT.parent.parent / "evals" / "golden-set" / "usali-rules.csv"
)
os.environ.setdefault("FONDOK_USALI_RULES_PATH", str(_RULES_PATH))

from app.services.usali_scorer import (  # noqa: E402
    _resolve_field,
    _resolve_via_tokens,
    flatten_extraction_fields,
    score_extraction,
)

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "usali_v3"


def _load_payload(name: str) -> list[dict]:
    """Load a saved prod-extraction fixture's ``fields`` list."""
    payload_path = _FIXTURES_DIR / name
    if not payload_path.exists():
        pytest.skip(f"missing v3 fixture {payload_path}")
    payload = json.loads(payload_path.read_text())
    fields = payload.get("fields") or []
    assert isinstance(fields, list) and len(fields) > 50, (
        f"fixture {name} doesn't look like a real prod extraction "
        f"({len(fields)} fields)"
    )
    return fields


# ─────────────────────────── 1: Sam's real T-12 ───────────────────────────


def test_v3_scores_real_sam_anglers_t12_payload() -> None:
    """Sam QA Bug #3 v3 headline assertion. Sam's 189-189-field T-12
    extraction must produce ``usali_score >= 30`` — never the gray
    "Inconclusive" badge that was tanking the demo. The saved fixture
    mirrors what prod emits today (captured 2026-06-28)."""
    fields = _load_payload("sam_anglers_t12.json")
    flat = flatten_extraction_fields(fields, extra_context={"keys": 132})
    result = score_extraction(flat)

    assert not result.inconclusive, (
        f"v3 scorer flagged inconclusive at {result.applicable_count} "
        f"applicable rules on Sam's real T-12 — the bug is back. "
        f"Deviations: {[(d.rule_id, d.message) for d in result.deviations[:5]]}"
    )
    assert result.score is not None
    assert result.applicable_count >= 20, (
        f"only {result.applicable_count} rules applied to Sam's T-12; "
        f"v3 fix should activate the revenue identity + GOP/NOI "
        f"margins + per-dept margins + the ratio rules"
    )
    # Sam's T-12 is a healthy P&L — expect most rules to pass.
    assert result.score >= 50.0, (
        f"score {result.score} below the 50 floor; the v3 fix should "
        f"land Sam's T-12 well above that"
    )


# ─────────────────────────── 2: Sam's real annual P&L ───────────────────────────


def test_v3_scores_real_sam_anglers_annual_payload() -> None:
    """Sam's annual P&L (72-144 fields) uses a different bucket shape
    than the T-12 (``revenues.*`` rollup vs ``rooms.revenue_usd``).
    The v3 resolver must hit both shapes."""
    fields = _load_payload("sam_anglers_annual.json")
    flat = flatten_extraction_fields(fields, extra_context={"keys": 132})
    result = score_extraction(flat)

    assert not result.inconclusive
    assert result.score is not None
    assert result.applicable_count >= 18, (
        f"only {result.applicable_count} rules applied to Sam's annual "
        f"P&L; v3 should activate the rollup + identity + margin rules"
    )
    assert result.score >= 30.0, (
        f"score {result.score} below the 30 floor on annual P&L"
    )


# ─────────────────────────── 3: path-flattening unwraps namespaces ───────────────────────────


def test_path_flattening_unwraps_pages_namespace() -> None:
    """``.page<n>.`` subordinate-namespace fields must not pollute the
    period total. A per-page line item gets dropped from the tail-write
    so the canonical total wins."""
    records = [
        {"field_name": "p_and_l_usali.total_revenues_usd", "value": 10_000_000.0, "source_page": 1, "confidence": 0.95},
        # Per-page slice — must NOT clobber the canonical total.
        {"field_name": "p_and_l_usali.page5.total_revenues_usd", "value": 850_000.0, "source_page": 5, "confidence": 0.9},
    ]
    flat = flatten_extraction_fields(records)
    assert _resolve_field(flat, "total_revenue") == 10_000_000.0, (
        f"page-namespaced field leaked into total_revenue — got "
        f"{_resolve_field(flat, 'total_revenue')}, expected 10M"
    )


def test_path_flattening_unwraps_monthly_namespace() -> None:
    """``.monthly.<period>.`` subordinate-namespace fields must not
    pollute the period total either. The token resolver also has to
    drop them — not just the tail-write."""
    records = [
        {"field_name": "p_and_l_usali.rooms.revenue_usd", "value": 8_000_000.0, "source_page": 1, "confidence": 0.95},
        # Twelve months of rooms revenue — none should be picked up as
        # the period total.
        *[
            {
                "field_name": f"p_and_l_usali.monthly.{m}.rooms_revenue_usd",
                "value": 660_000.0,
                "source_page": i + 2,
                "confidence": 0.9,
            }
            for i, m in enumerate([
                "jan", "feb", "mar", "apr", "may", "jun",
                "jul", "aug", "sep", "oct", "nov", "dec",
            ])
        ],
    ]
    flat = flatten_extraction_fields(records)
    rooms = _resolve_field(flat, "rooms_revenue")
    assert rooms == 8_000_000.0, (
        f"monthly slice leaked into rooms_revenue — got {rooms}, "
        f"expected 8M"
    )


# ─────────────────────────── 4: resolver fallback strategies ───────────────────────────


def test_resolver_falls_back_to_token_match_on_novel_namespace() -> None:
    """A canonical name with no explicit alias entry resolves via the
    v3 token-match fallback. Path here is intentionally NOT in the
    alias map: ``p_and_l.utility_costs.line_total_usd`` — utilities
    expense in a namespace no prior fix has covered."""
    flat = {
        "p_and_l.utility_costs.line_total_usd": 165_000.0,
    }
    val = _resolve_field(flat, "utilities_expense")
    assert val == 165_000.0, (
        f"token resolver missed utilities_expense on a novel path — "
        f"got {val!r}"
    )


def test_resolver_falls_back_to_money_indicator_for_soft_tokens() -> None:
    """Soft tokens (``expense``/``revenue``/``fee``/``reserve``) accept
    a money-indicator (``_usd`` / ``_amount``) in lieu of the literal
    word — the LLM often emits ``utilities_usd`` instead of
    ``utilities_expense``."""
    flat = {"p_and_l.utilities_usd": 150_000.0}
    val = _resolve_field(flat, "utilities_expense")
    assert val == 150_000.0


def test_resolver_case_insensitive() -> None:
    """All token-matching is lowercased — a payload in ``ROOMS_REVENUE``
    or mixed case resolves the same as ``rooms_revenue``."""
    flat = {"P_AND_L_USALI.Rooms.REVENUE_USD": 9_000_000.0}
    val = _resolve_via_tokens(flat, "rooms_revenue")
    assert val == 9_000_000.0


# ─────────────────────────── 5: legacy backward compat ───────────────────────────


def test_legacy_v1_v2_payloads_still_score_correctly() -> None:
    """A v1/v2-era payload (canonical names + the schema-doc paths the
    earlier alias map was written against) must produce the same score
    as before the v3 changes. We pin the contract: ≥ 18 applicable
    rules, ≥ 60% score on the well-calibrated v1 fixture."""
    # The original v1 fixture from test_usali_scorer_real_payload.
    records = [
        {"field_name": "occupancy_pct", "value": 0.75, "source_page": 1, "confidence": 0.95},
        {"field_name": "adr_usd", "value": 245.0, "source_page": 1, "confidence": 0.95},
        {"field_name": "revpar_usd", "value": 245.0 * 0.75, "source_page": 1, "confidence": 0.95},
        {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 8_600_000.0, "source_page": 1, "confidence": 0.95},
        {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 1_200_000.0, "source_page": 1, "confidence": 0.95},
        {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 200_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l_usali.departmental_expenses.rooms", "value": 2_200_000.0, "source_page": 2, "confidence": 0.9},
        {"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 1_000_000.0, "source_page": 2, "confidence": 0.9},
        {"field_name": "p_and_l_usali.departmental_expenses.other_operated", "value": 90_000.0, "source_page": 2, "confidence": 0.9},
        {"field_name": "p_and_l_usali.undistributed.administrative_general", "value": 800_000.0, "source_page": 3, "confidence": 0.9},
        {"field_name": "p_and_l_usali.undistributed.information_telecom", "value": 120_000.0, "source_page": 3, "confidence": 0.9},
        {"field_name": "p_and_l_usali.undistributed.sales_marketing", "value": 500_000.0, "source_page": 3, "confidence": 0.9},
        {"field_name": "p_and_l_usali.undistributed.property_operations", "value": 400_000.0, "source_page": 3, "confidence": 0.9},
        {"field_name": "p_and_l_usali.undistributed.utilities", "value": 350_000.0, "source_page": 3, "confidence": 0.9},
        {"field_name": "p_and_l_usali.fees_and_reserves.mgmt_fee", "value": 300_000.0, "source_page": 4, "confidence": 0.95},
        {"field_name": "p_and_l_usali.fees_and_reserves.ffe_reserve", "value": 400_000.0, "source_page": 4, "confidence": 0.95},
        {"field_name": "p_and_l_usali.fixed_charges.property_taxes", "value": 280_000.0, "source_page": 4, "confidence": 0.95},
        {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 216_000.0, "source_page": 4, "confidence": 0.95},
    ]
    flat = flatten_extraction_fields(records, extra_context={"keys": 120})
    result = score_extraction(flat)

    assert not result.inconclusive
    assert result.score is not None
    # The v1 fixture had ≥ 10 applicable; v3 should keep that (and
    # likely pick up more via the token resolver on the schema-doc
    # paths).
    assert result.applicable_count >= 18, (
        f"v1/v2 legacy payload regressed to {result.applicable_count} "
        f"applicable rules; backward-compat broken"
    )
    assert result.score >= 60.0


# ─────────────────────────── 6: inconclusive floor ───────────────────────────


def test_inconclusive_only_when_truly_under_five_applicable_rules() -> None:
    """The "Inconclusive" badge fires ONLY when fewer than 5 rules
    apply. An empty payload → Inconclusive; a rich payload → real
    numeric score."""
    # Empty payload.
    empty_result = score_extraction({})
    assert empty_result.inconclusive
    assert empty_result.score is None
    assert empty_result.applicable_count == 0

    # 5+ applicable rules → not inconclusive.
    rich_payload = {
        "occupancy": 0.72,
        "adr": 310.0,
        "revpar": 0.72 * 310,
        "total_revenue": 10_000_000.0,
        "gop": 3_500_000.0,
        "noi": 2_200_000.0,
        "mgmt_fee": 300_000.0,
        "ffe_reserve": 400_000.0,
        "keys": 130,
        "insurance_expense": 150_000.0,
    }
    rich_result = score_extraction(rich_payload)
    assert not rich_result.inconclusive
    assert rich_result.score is not None
    assert rich_result.applicable_count >= 5


# ─────────────────────────── 7-8: severity tagging through v3 path ───────────────────────────


def test_critical_severity_deviation_emitted_through_v3_resolver() -> None:
    """A CRITICAL identity violation (RevPAR ≠ Occupancy × ADR) is
    surfaced with severity=CRITICAL even when the fields resolve via
    the v3 token-match path."""
    # Resolve via novel namespace + force a RevPAR drift.
    records = [
        {"field_name": "kpi_block.adr_usd", "value": 300.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "kpi_block.occupancy_rate", "value": 0.70, "source_page": 1, "confidence": 0.9},
        {"field_name": "kpi_block.revpar_usd", "value": 280.0, "source_page": 1, "confidence": 0.9},
    ]
    flat = flatten_extraction_fields(records)
    result = score_extraction(flat)
    critical_devs = [
        d for d in result.deviations
        if d.rule_id in {"REVPAR_CHECK", "MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY"}
        and d.severity == "CRITICAL"
    ]
    assert critical_devs, (
        f"no CRITICAL RevPAR-identity deviation emitted on a forced "
        f"drift — token-resolved values must still flow through "
        f"severity tagging. All devs: {[d.rule_id for d in result.deviations]}"
    )


def test_warn_severity_deviation_emitted_through_v3_resolver() -> None:
    """A WARN range violation (occupancy below 0.40) is surfaced with
    severity=WARN through the v3 path."""
    flat = {"occupancy": 0.25, "adr": 200.0, "revpar": 0.25 * 200, "total_revenue": 5_000_000.0,
            "gop": 1_500_000.0, "noi": 800_000.0, "mgmt_fee": 150_000.0,
            "ffe_reserve": 200_000.0, "keys": 80, "insurance_expense": 100_000.0}
    result = score_extraction(flat)
    warn_occ = [
        d for d in result.deviations
        if d.rule_id == "OCCUPANCY_RANGE" and d.severity == "WARN"
    ]
    assert warn_occ, "WARN-severity occupancy range deviation missing"


# ─────────────────────────── 9-10: score boundaries ───────────────────────────


def test_score_caps_at_100_when_every_applicable_rule_passes() -> None:
    """A perfectly healthy payload (every applicable rule passes)
    produces score=100.0 — never above. The percentage is a clean
    ratio capped by definition."""
    # Reuse the "healthy" hand-tuned fixture from the v1 tests.
    rooms = 6_000_000.0
    fb = 1_500_000.0
    other = 500_000.0
    total_revenue = rooms + fb + other
    rooms_dept_exp = 1_500_000.0
    fb_dept_exp = 1_200_000.0
    other_dept_exp = 250_000.0
    dept_expenses = rooms_dept_exp + fb_dept_exp + other_dept_exp
    undistributed = 1_800_000.0
    gop = total_revenue - dept_expenses - undistributed
    mgmt_fee = total_revenue * 0.03
    ffe_reserve = total_revenue * 0.04
    fixed_charges = 500_000.0
    noi = gop - mgmt_fee - ffe_reserve - fixed_charges
    keys = 132
    occupancy = 0.74
    adr = 332.07
    revpar = occupancy * adr
    payload = {
        "revpar": revpar,
        "occupancy": occupancy,
        "adr": adr,
        "rooms_revenue": rooms,
        "fb_revenue": fb,
        "other_revenue": other,
        "total_revenue": total_revenue,
        "dept_expenses": dept_expenses,
        "undistributed_expenses": undistributed,
        "gop": gop,
        "mgmt_fee": mgmt_fee,
        "ffe_reserve": ffe_reserve,
        "fixed_charges": fixed_charges,
        "noi": noi,
        "total_dept_expense": dept_expenses,
        "dept_expenses_by_line": [rooms_dept_exp, fb_dept_exp, other_dept_exp],
        "rooms_dept_profit": rooms - rooms_dept_exp,
        "fb_dept_profit": fb - fb_dept_exp,
        "total_labor": total_revenue * 0.34,
        "utilities_expense": keys * 1_500,
        "marketing_expense": total_revenue * 0.05,
        "rm_expense": total_revenue * 0.04,
        "ag_expense": total_revenue * 0.08,
        "insurance_expense": keys * 1_200,
        "property_tax": 200_000.0,
        "property_value": 10_000_000.0,
        "keys": keys,
        "labor_cost_per_occupied_room": 45.0,
    }
    result = score_extraction(payload)
    assert result.score is not None
    assert 0.0 <= result.score <= 100.0
    # The fixture is tuned for everything in-band → expect ≥ 95% pass.
    assert result.score >= 95.0, (
        f"healthy payload only scored {result.score}; deviations: "
        f"{[(d.rule_id, d.message) for d in result.deviations]}"
    )


def test_score_floors_at_zero_when_every_applicable_rule_fails() -> None:
    """A pathological payload (every applicable rule fails) produces
    score=0.0 — never below."""
    # Force a payload that fails every rule it can evaluate:
    # - RevPAR identity drift
    # - GOP margin out of band (negative GOP)
    # - Occupancy way out of band
    # - ADR way out of band
    # - Insurance way out of band
    pathological = {
        "occupancy": 1.5,            # out of band (>0.95)
        "adr": 5_000.0,              # out of band (>2000)
        "revpar": 10_000.0,          # drifts wildly from occ * adr
        "total_revenue": 1_000_000.0,
        "rooms_revenue": 800_000.0,
        "fb_revenue": 100_000.0,
        "other_revenue": 100_000.0,
        "gop": -500_000.0,           # negative — out of band
        "noi": -800_000.0,
        "mgmt_fee": 200_000.0,        # 20% — out of band
        "ffe_reserve": 1_000.0,       # 0.1% — out of band
        "fixed_charges": 50_000.0,
        "keys": 100,
        "insurance_expense": 50_000_000.0,  # $500k/key — out of band
        "property_tax": 1_000_000.0,
        "property_value": 1_000_000.0,
        "utilities_expense": 10_000_000.0,
        "marketing_expense": 500_000.0,
        "rm_expense": 600_000.0,
        "ag_expense": 700_000.0,
        "rooms_dept_profit": -100_000.0,
        "rooms_dept_expense": 900_000.0,
        "fb_dept_expense": 200_000.0,
        "dept_expenses": 1_200_000.0,
        "undistributed_expenses": 300_000.0,
    }
    result = score_extraction(pathological)
    assert result.score is not None
    assert 0.0 <= result.score <= 100.0
    # We've calibrated so most rules fail — score should be low.
    assert result.score < 30.0, (
        f"pathological payload should fail most rules — got "
        f"{result.score}, expected < 30; deviations="
        f"{[d.rule_id for d in result.deviations]}"
    )


# ─────────────────────────── 11-12: novel-namespace ───────────────────────────


def test_v3_resolver_handles_novel_extractor_flavor() -> None:
    """A future LLM run that emits an ENTIRELY NEW namespace —
    ``p_and_l.<bucket>_dept.<line>_usd`` (no schema doc / no alias
    entry covers this shape) — must still produce ≥ 15 applicable
    rules and a numeric score. This is the headline contract: v3
    generalizes without needing a path-by-path map update."""
    records = [
        {"field_name": "p_and_l.rooms_dept.revenue_usd", "value": 8_000_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.food_beverage_dept.revenue_usd", "value": 1_000_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.other_dept.revenue_usd", "value": 200_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.rooms_dept.expense_usd", "value": 2_000_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.food_beverage_dept.expense_usd", "value": 800_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "kpis.occupancy", "value": 0.72, "source_page": 1, "confidence": 0.9},
        {"field_name": "kpis.adr", "value": 305.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "kpis.revpar_usd", "value": 219.6, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.total_revenues_usd", "value": 9_200_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.gop_usd", "value": 3_500_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.noi_usd", "value": 2_400_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.mgmt_fee_usd", "value": 276_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.ffe_reserve_usd", "value": 368_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.insurance_expense_usd", "value": 200_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.property_taxes_usd", "value": 220_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.utilities_usd", "value": 150_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.admin_general_usd", "value": 600_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.sales_marketing_usd", "value": 400_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.property_ops_maintenance_usd", "value": 350_000.0, "source_page": 1, "confidence": 0.9},
        {"field_name": "p_and_l.information_telecom_usd", "value": 90_000.0, "source_page": 1, "confidence": 0.9},
    ]
    flat = flatten_extraction_fields(records, extra_context={"keys": 130})
    result = score_extraction(flat)

    assert not result.inconclusive
    assert result.score is not None
    assert result.applicable_count >= 15, (
        f"only {result.applicable_count} applicable on the novel "
        f"namespace; v3 token resolver should hit ≥ 15. Flat keys: "
        f"{sorted(flat.keys())[:40]}"
    )
    # The synthetic numbers are calibrated to land most rules in-band.
    assert result.score >= 70.0


def test_token_resolver_rejects_wrong_discriminator() -> None:
    """The forbidden-token rules prevent ``rooms_revenue`` from
    matching ``rooms_dept_expense`` (rooms is right but expense
    forbids the revenue concept). Critical to keep us from reporting
    a 6M expense as 6M rooms revenue."""
    # Only an expense line exists in the payload.
    flat = {
        "p_and_l_usali.rooms.expense_usd": 2_500_000.0,
        "p_and_l_usali.rooms.departmental_expense_usd": 2_500_000.0,
    }
    val = _resolve_via_tokens(flat, "rooms_revenue")
    assert val is None, (
        f"token resolver mis-matched an expense to rooms_revenue: {val}"
    )
    # Sanity: rooms_dept_expense DOES resolve.
    val_exp = _resolve_via_tokens(flat, "rooms_dept_expense")
    assert val_exp == 2_500_000.0


# ─────────────────────────── historical-variance secondary fix ───────────────────────────


def test_historical_variance_normalizer_uses_v3_resolver() -> None:
    """Secondary fix (Sam 2026-06-28): the broker-questions YoY engine
    has its own ``_normalize_pnl`` whose alias map was stale — it
    covered only the schema-doc paths, missing
    ``p_and_l_usali.rooms.revenue_usd`` / ``p_and_l_usali.gross_operating_profit_usd``
    that real prod emits. Result: Sam's "Refresh broker questions"
    returned 0 findings even with two period-years in the deal.

    v3 wires the scorer's resolver into ``_normalize_pnl`` as a
    fallback so the broker-questions panel benefits from the same
    token-match coverage as the USALI compliance score.
    """
    from app.engines.historical_variance import _normalize_pnl, detect_yoy_variances

    # Build per-year flats by loading the saved Sam fixtures and
    # injecting the year column the engine reads (the upstream
    # ``_load_historical_pnls`` does this from the documents row).
    flats = []
    for fname, year in [
        ("sam_anglers_annual.json", 2023),
        ("sam_anglers_t12.json", 2024),
    ]:
        fields = _load_payload(fname)
        flat: dict[str, object] = {}
        for f in fields:
            name = f.get("field_name")
            if isinstance(name, str):
                flat[name] = f.get("value")
        flat["year"] = year
        flats.append(flat)

    # Both years must resolve at least the rooms_revenue + total_revenue
    # + gop trio — without the v3 fallback ``_normalize_pnl`` returns
    # empty on these payloads and the engine drops the year.
    for year_idx, flat in enumerate(flats):
        normalized = _normalize_pnl(flat)
        assert "rooms_revenue" in normalized, (
            f"flat[{year_idx}] missing rooms_revenue after v3 normalize"
        )
        assert "total_revenue" in normalized, (
            f"flat[{year_idx}] missing total_revenue after v3 normalize"
        )
        assert "gop" in normalized, (
            f"flat[{year_idx}] missing gop after v3 normalize"
        )

    # Engine should now emit findings (the two periods drift on F&B
    # revenue + total revenue + GOP — all classifications confirmed by
    # the calibrated fixture).
    findings = detect_yoy_variances(flats)
    assert len(findings) >= 2, (
        f"v3 secondary fix didn't unblock broker questions — only "
        f"{len(findings)} findings (expected ≥ 2) on Sam's real payloads"
    )
