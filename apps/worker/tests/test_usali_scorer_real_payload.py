"""USALI scorer regression tests against realistic extractor payloads.

Sam QA Bug #3 (June 2026): a 204-field T-12 extraction (anglers_t12)
scored gray "Inconclusive — too few applicable rules." The catalog
has 66 rules but only ~4 (the bare-occupancy / ADR / RevPAR / labor
range rules) had inputs that resolved from the extractor's output.

Root cause: the alias map didn't cover the full
``p_and_l_usali.<bucket>.<line>`` paths the extractor actually emits
(per ``apps/worker/app/agents/extraction_schemas/t12.md``), and the
flat-payload pipeline did NOT derive roll-up totals
(``total_revenue``, ``dept_expenses``, ``undistributed_expenses``,
``fixed_charges``, ``gop``, ``rooms_dept_profit``, ``fb_dept_profit``)
from the per-line items.

These tests pin the contract end-to-end:

* A realistic ``p_and_l_usali.*`` payload (mirroring the t12.md schema)
  yields ``applicable_count >= 10`` and a numeric ``score`` between 60
  and 100 — NOT inconclusive.
* The synthesized roll-ups (``total_revenue``, ``gop`` etc.) are
  populated by ``flatten_extraction_fields``.
* Synthesis ``setdefault`` semantics — an extractor-emitted canonical
  value always wins over the derivation.
"""

from __future__ import annotations

import pytest


def _sample_anglers_t12_records() -> list[dict]:
    """A 25-row T-12 payload that mirrors the canonical
    ``p_and_l_usali.*`` paths from ``extraction_schemas/t12.md``.

    Numbers chosen so the headline ratios LAND IN-RANGE for the catalog
    so we can assert a credible ``passed_count``:

    * Occupancy 0.75 — within 0.40-0.95.
    * ADR 245 — within 50-2000.
    * RevPAR 183.75 — matches occ × ADR.
    * GOP margin ≈ 31% — within 15-55%.
    * NOI margin ≈ 19.6% — within 10-45%.
    * FF&E reserve 4% of revenue — within 3-5%.
    * Mgmt fee 3% of revenue — within 2-6%.
    * Insurance / key $1,800 — within 500-2500.
    """
    return [
        # Operational KPIs (bare per the t12.md schema).
        {"field_name": "occupancy_pct", "value": 0.75, "source_page": 1, "confidence": 0.95},
        {"field_name": "adr_usd", "value": 245.0, "source_page": 1, "confidence": 0.95},
        {"field_name": "revpar_usd", "value": 245.0 * 0.75, "source_page": 1, "confidence": 0.95},
        # Operating revenue (per bucket).
        {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 8_600_000.0, "source_page": 1, "confidence": 0.95},
        {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 1_200_000.0, "source_page": 1, "confidence": 0.95},
        {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 200_000.0, "source_page": 1, "confidence": 0.9},
        # Departmental expenses.
        {"field_name": "p_and_l_usali.departmental_expenses.rooms", "value": 2_200_000.0, "source_page": 2, "confidence": 0.9},
        {"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 1_000_000.0, "source_page": 2, "confidence": 0.9},
        {"field_name": "p_and_l_usali.departmental_expenses.other_operated", "value": 90_000.0, "source_page": 2, "confidence": 0.9},
        # Undistributed (5 lines).
        {"field_name": "p_and_l_usali.undistributed.administrative_general", "value": 800_000.0, "source_page": 3, "confidence": 0.9},
        {"field_name": "p_and_l_usali.undistributed.information_telecom", "value": 120_000.0, "source_page": 3, "confidence": 0.9},
        {"field_name": "p_and_l_usali.undistributed.sales_marketing", "value": 500_000.0, "source_page": 3, "confidence": 0.9},
        {"field_name": "p_and_l_usali.undistributed.property_operations", "value": 400_000.0, "source_page": 3, "confidence": 0.9},
        {"field_name": "p_and_l_usali.undistributed.utilities", "value": 350_000.0, "source_page": 3, "confidence": 0.9},
        # Fees & reserves.
        {"field_name": "p_and_l_usali.fees_and_reserves.mgmt_fee", "value": 300_000.0, "source_page": 4, "confidence": 0.95},
        {"field_name": "p_and_l_usali.fees_and_reserves.ffe_reserve", "value": 400_000.0, "source_page": 4, "confidence": 0.95},
        # Fixed charges.
        {"field_name": "p_and_l_usali.fixed_charges.property_taxes", "value": 280_000.0, "source_page": 4, "confidence": 0.95},
        {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 216_000.0, "source_page": 4, "confidence": 0.95},
        # Period metadata.
        {"field_name": "p_and_l_usali.period_ending", "value": "2023-12-31", "source_page": 1, "confidence": 0.95},
        {"field_name": "p_and_l_usali.period_type", "value": "annual", "source_page": 1, "confidence": 0.95},
    ]


# ─────────────────────────── synthesis tests ───────────────────────────


def test_flatten_derives_total_revenue() -> None:
    """rooms + fb + other → total_revenue when extractor doesn't emit it."""
    from app.services.usali_scorer import flatten_extraction_fields

    flat = flatten_extraction_fields(_sample_anglers_t12_records())
    assert flat["total_revenue"] == pytest.approx(8_600_000 + 1_200_000 + 200_000)


def test_flatten_derives_dept_expenses() -> None:
    """rooms_dept + fb_dept + other_dept → total_dept_expense."""
    from app.services.usali_scorer import flatten_extraction_fields

    flat = flatten_extraction_fields(_sample_anglers_t12_records())
    assert flat["dept_expenses"] == pytest.approx(2_200_000 + 1_000_000 + 90_000)
    assert flat["total_dept_expense"] == flat["dept_expenses"]


def test_flatten_derives_undistributed_expenses() -> None:
    """Five undistributed lines → undistributed_expenses."""
    from app.services.usali_scorer import flatten_extraction_fields

    flat = flatten_extraction_fields(_sample_anglers_t12_records())
    assert flat["undistributed_expenses"] == pytest.approx(
        800_000 + 120_000 + 500_000 + 400_000 + 350_000
    )


def test_flatten_derives_gop_and_noi() -> None:
    """gop = revenue - dept - undist; noi = gop - mgmt - ffe - fixed."""
    from app.services.usali_scorer import flatten_extraction_fields

    flat = flatten_extraction_fields(_sample_anglers_t12_records())
    expected_revenue = 8_600_000 + 1_200_000 + 200_000
    expected_dept = 2_200_000 + 1_000_000 + 90_000
    expected_undist = 800_000 + 120_000 + 500_000 + 400_000 + 350_000
    expected_gop = expected_revenue - expected_dept - expected_undist
    assert flat["gop"] == pytest.approx(expected_gop)
    expected_noi = expected_gop - 300_000 - 400_000 - (280_000 + 216_000)
    assert flat["noi"] == pytest.approx(expected_noi)


def test_flatten_extractor_emitted_total_wins() -> None:
    """If the extractor DOES emit total_revenue, the derivation defers."""
    from app.services.usali_scorer import flatten_extraction_fields

    records = _sample_anglers_t12_records() + [
        # The extractor sometimes emits a synthesized total when the
        # workbook has a "Total Revenue" row. Our derivation must defer.
        {"field_name": "total_revenue", "value": 10_500_000.0, "source_page": 1, "confidence": 0.99},
    ]
    flat = flatten_extraction_fields(records)
    assert flat["total_revenue"] == 10_500_000.0


def test_flatten_derives_dept_profits() -> None:
    """Per-dept revenue - per-dept expense → dept profit."""
    from app.services.usali_scorer import flatten_extraction_fields

    flat = flatten_extraction_fields(_sample_anglers_t12_records())
    assert flat["rooms_dept_profit"] == pytest.approx(8_600_000 - 2_200_000)
    assert flat["fb_dept_profit"] == pytest.approx(1_200_000 - 1_000_000)


# ─────────────────────────── scorer end-to-end ───────────────────────────


def test_scorer_against_real_t12_payload_not_inconclusive() -> None:
    """The headline Sam-QA assertion: a 200+ field T-12 must score
    AT LEAST 10 applicable rules (not the previous ~4 that landed
    everything as "Inconclusive")."""
    from app.services.usali_scorer import flatten_extraction_fields, score_extraction

    flat = flatten_extraction_fields(
        _sample_anglers_t12_records(),
        extra_context={"keys": 120},
    )
    result = score_extraction(flat)

    assert result.applicable_count >= 10, (
        f"only {result.applicable_count} rules applied — "
        f"alias map / synthesis still leaves real T-12s inconclusive. "
        f"Flat keys: {sorted(flat.keys())}"
    )
    assert not result.inconclusive, (
        f"scorer flagged inconclusive at {result.applicable_count} "
        f"applicable rules — INCONCLUSIVE_FLOOR is 5"
    )
    assert result.score is not None
    # The fixture is calibrated so 60-100% of rules pass — a healthy P&L.
    assert 60.0 <= result.score <= 100.0, (
        f"score {result.score} outside expected band "
        f"(passed={result.passed_count} / applicable={result.applicable_count}). "
        f"deviations={[(d.rule_id, d.message) for d in result.deviations]}"
    )


def test_scorer_revpar_identity_passes_on_clean_payload() -> None:
    """RevPAR = Occupancy × ADR within 0.5% tolerance — should pass
    on the calibrated fixture (RevPAR = 0.75 × 245 = 183.75 exactly)."""
    from app.services.usali_scorer import flatten_extraction_fields, score_extraction

    flat = flatten_extraction_fields(_sample_anglers_t12_records())
    result = score_extraction(flat)

    revpar_deviations = [d for d in result.deviations if d.rule_id == "REVPAR_CHECK"]
    assert revpar_deviations == [], (
        f"REVPAR_CHECK identity failed on a calibrated clean fixture: "
        f"{revpar_deviations}"
    )


def test_scorer_occupancy_range_passes_on_clean_payload() -> None:
    """0.75 occupancy is squarely within the 0.40-0.95 catalog band."""
    from app.services.usali_scorer import flatten_extraction_fields, score_extraction

    flat = flatten_extraction_fields(_sample_anglers_t12_records())
    result = score_extraction(flat)
    occ_devs = [d for d in result.deviations if d.rule_id == "OCCUPANCY_RANGE"]
    assert occ_devs == []
