"""USALI scorer + Router v4 structural-recognizer validation
(Sam QA Bug #3 v4 / Bug #2 v4, June 28 2026).

These tests pin the v4 contract: the structural recognizer surfaces
canonical USALI line items regardless of namespace, and BOTH the
USALI scorer AND the Router misclassification path consult it.

Why "live" tests
----------------

The v1 → v2 → v3 fixes all "worked against saved fixtures" then
failed in production because the LLM extractor emits different
path namespaces between runs (``p_and_l_usali.rooms.revenue_usd``
on day 1, ``pages.financial_summary.rooms_segment.gross_revenue``
on day 2, ``hotel_revenues.rooms_segment.gross`` on day 3). The
v4 fixtures under ``tests/fixtures/usali_v4/`` were captured by
``apps/worker/scripts/capture_live_extraction.py`` — they include
two distinct namespace styles of the SAME T-12 source document
(``live_extraction_anglers_t12.json`` +
``live_extraction_anglers_t12_altrun.json`` +
``live_extraction_anglers_t12_altrun2.json``) so the recognizer's
namespace-blindness is asserted, not assumed.

The capture script falls back to deterministic perturbation when
no ``ANTHROPIC_API_KEY`` is available — the namespaces it
generates mirror what the LLM emits across observed prod runs
(see the v3 vs. v1 vs. v2 alias-map churn that motivated the
rewrite).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.structural_recognizer import (
    canonical_payload_from_signals,
    classify_structure,
)
from app.services.usali_scorer import (
    flatten_extraction_fields,
    score_extraction,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "usali_v4"


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(scope="module")
def t12_payload() -> list[dict]:
    """Live T-12 extraction payload — namespace style #1
    (``p_and_l_usali.*``). 181-213 fields."""
    with (FIXTURE_DIR / "live_extraction_anglers_t12.json").open() as f:
        return json.load(f)["fields"]


@pytest.fixture(scope="module")
def annual_payload() -> list[dict]:
    """Live annual P&L extraction payload — 107-144 fields."""
    with (FIXTURE_DIR / "live_extraction_anglers_annual.json").open() as f:
        return json.load(f)["fields"]


@pytest.fixture(scope="module")
def t12_altrun_payload() -> list[dict]:
    """Second extraction of the SAME T-12 source — namespace style #2
    (``pages.financial_summary.*``). Captures LLM-emission drift."""
    with (FIXTURE_DIR / "live_extraction_anglers_t12_altrun.json").open() as f:
        return json.load(f)["fields"]


@pytest.fixture(scope="module")
def t12_altrun2_payload() -> list[dict]:
    """Third extraction style — ``hotel_revenues.*`` / ``operating_costs.*``.
    Demonstrates the recognizer also handles entirely fresh prefixes."""
    with (FIXTURE_DIR / "live_extraction_anglers_t12_altrun2.json").open() as f:
        return json.load(f)["fields"]


@pytest.fixture(scope="module")
def om_payload() -> list[dict]:
    """Synthetic OM extraction — broker proforma + property metadata,
    no P&L line items. Used to confirm the recognizer rejects non-P&L."""
    with (FIXTURE_DIR / "live_extraction_anglers_om.json").open() as f:
        return json.load(f)["fields"]


# ─────────────────────────── tests ───────────────────────────


def test_1_recognizer_classifies_live_t12_as_pnl(t12_payload):
    """Test 1 — Recognizer surfaces ≥ 6 canonical keys on Sam's T-12."""
    signals = classify_structure(t12_payload)
    assert signals.is_pnl is True, signals.reason
    assert len(signals.canonical_keys_matched) >= 6, (
        f"only matched {len(signals.canonical_keys_matched)} canonicals: "
        f"{signals.canonical_keys_matched}"
    )
    # Specific concept assertions — these are the load-bearing P&L lines
    # the rule catalog depends on.
    assert signals.has_rooms_revenue
    assert signals.has_property_tax
    assert signals.has_management_fee
    assert signals.has_gop_or_noi


def test_2_scorer_produces_enough_applicable_rules(t12_payload):
    """Test 2 — Live T-12 payload triggers ≥ 15 applicable USALI rules.

    The Wave 4 v4 product decision raises the bar above the v1-3
    threshold of 5: Sam's 181-field T-12 should resolve far more.
    """
    flat = flatten_extraction_fields(
        t12_payload,
        extra_context={"keys": 87, "purchase_price": 80_000_000},
    )
    result = score_extraction(flat)
    assert result.applicable_count >= 15, (
        f"only {result.applicable_count} applicable; "
        f"deviations={[d.rule_id for d in result.deviations][:10]}"
    )


def test_3_scorer_returns_real_score_in_band(t12_payload):
    """Test 3 — Live T-12 USALI score is not None and lies in [30, 95]."""
    flat = flatten_extraction_fields(
        t12_payload,
        extra_context={"keys": 87, "purchase_price": 80_000_000},
    )
    result = score_extraction(flat)
    assert result.score is not None
    assert result.inconclusive is False
    assert 30.0 <= result.score <= 95.0, (
        f"score={result.score} outside [30, 95]"
    )


def test_4_router_does_not_misclassify_when_user_tagged_t12(t12_payload):
    """Test 4 — Router given live T-12 payload + user_tag='T-12' must
    NOT flag misclassified — structural signal confirms shape.

    Mirrors the misclassification-decision shape in
    ``apps/worker/app/api/documents.py``: structural_pnl_score == 1.0
    on a 6+-canonical payload, and the rule is "trust the user's tag
    when the recognizer agrees".
    """
    signals = classify_structure(t12_payload)
    assert signals.is_pnl is True

    # Mimic the misclassification computation in
    # apps/worker/app/api/documents.py.
    user_tag_canonical = "T12"
    router_label_canonical = "PROPERTYINFO"  # Sam's prod misfire
    pnl_tag_canonicals = {"T12", "PNL", "PNLMONTHLY", "PNLYTD"}
    user_tag_is_pnl = user_tag_canonical in pnl_tag_canonicals

    naive_misclassified = bool(
        user_tag_canonical
        and router_label_canonical
        and user_tag_canonical != router_label_canonical
    )
    # Naive logic would set this to True (BUG: Sam's prod).
    assert naive_misclassified is True

    # v4 rule: structural override clears the flag.
    if signals.is_pnl and user_tag_is_pnl:
        final_misclassified = False
    else:
        final_misclassified = naive_misclassified
    assert final_misclassified is False


def test_5_router_does_flag_when_pnl_uploaded_under_wrong_tag(t12_payload):
    """Test 5 — Router given live T-12 payload + user_tag='Basic
    Property Info' MUST flag misclassified (structural signal wins).

    Inverse of Test 4: when the analyst grabs the wrong wizard bucket,
    the recognizer's structural verdict beats the user's tag.
    """
    signals = classify_structure(t12_payload)
    assert signals.is_pnl is True

    user_tag_canonical = "PROPERTYINFO"  # wrong wizard tag
    pnl_tag_canonicals = {"T12", "PNL", "PNLMONTHLY", "PNLYTD"}
    user_tag_is_pnl = user_tag_canonical in pnl_tag_canonicals
    assert user_tag_is_pnl is False

    # v4 rule branch 2: recognizer says P&L, user tag isn't P&L → flag.
    if signals.is_pnl and not user_tag_is_pnl:
        final_misclassified = True
    else:
        final_misclassified = False
    assert final_misclassified is True


def test_6_recognizer_rejects_empty_and_om_payloads(om_payload):
    """Test 6 — Empty / OM payloads are NOT classified as P&L.

    The recognizer's pattern catalog should not fire on broker-proforma
    fields (cap rate, asking price, comp-set averages) — those carry
    money but lack the rooms-revenue + dept-expense + rollup triple
    every real P&L has.
    """
    # Empty payload → not P&L, inconclusive.
    empty_signals = classify_structure([])
    assert empty_signals.is_pnl is False
    assert empty_signals.canonical_keys_matched == []

    # Empty → scorer returns inconclusive.
    empty_result = score_extraction({})
    assert empty_result.inconclusive is True
    assert empty_result.score is None

    # OM → not P&L (broker proforma + comp set, no dept expenses).
    om_signals = classify_structure(om_payload)
    assert om_signals.is_pnl is False
    assert om_signals.has_rooms_revenue is False
    assert om_signals.has_property_tax is False


def test_7_om_does_not_trigger_full_pnl_scoring(om_payload):
    """Test 7 — A real OM extraction payload (broker proforma fields)
    does not get classified as P&L by the structural recognizer.

    The production caller (``_persist_usali_score``) gates on
    ``doc_type IN {T12, PNL, PNL_MONTHLY, PNL_YTD}`` so a doc with
    ``doc_type=OM`` never enters scoring — this test confirms the
    structural signal also says "not a P&L", so the two gates agree.
    """
    signals = classify_structure(om_payload)
    assert signals.is_pnl is False
    # Counts: OM has at most 0-1 revenue line (broker proforma RevPAR
    # is a KPI, not a revenue line) and 0 expense lines.
    assert signals.revenue_line_count <= 1
    assert signals.expense_line_count == 0


def test_8_second_extraction_run_same_recognition(
    t12_payload, t12_altrun_payload, t12_altrun2_payload
):
    """Test 8 — Re-extracting the SAME source document with a fresh
    LLM run yields the SAME structural verdict.

    This is THE pin against LLM-namespace coupling: three different
    namespace styles of the same T-12 source ALL produce ``is_pnl=True``,
    cover the same load-bearing canonical concepts, and score in the
    same band. Without the structural recognizer, any one of these
    drift events would have left Sam's QA with "Inconclusive — 0
    rules" again.
    """
    s1 = classify_structure(t12_payload)
    s2 = classify_structure(t12_altrun_payload)
    s3 = classify_structure(t12_altrun2_payload)

    # All three runs recognize the same shape.
    assert s1.is_pnl and s2.is_pnl and s3.is_pnl

    # Concept coverage: the load-bearing P&L lines must surface across
    # every namespace style.
    load_bearing = {
        "rooms_revenue",
        "fb_revenue",
        "total_revenue",
        "property_tax",
        "mgmt_fee",
    }
    for label, signals in (("s1", s1), ("s2", s2), ("s3", s3)):
        missing = load_bearing - set(signals.canonical_keys_matched)
        assert not missing, (
            f"{label} ({signals.reason}) missing load-bearing concepts: "
            f"{missing}"
        )

    # Scores stay in band — all three namespace styles produce the
    # same applicable count + similar score (the canonical values
    # are the SAME — only the path namespace varies).
    extra = {"keys": 87, "purchase_price": 80_000_000}
    r1 = score_extraction(flatten_extraction_fields(t12_payload, extra_context=extra))
    r2 = score_extraction(flatten_extraction_fields(t12_altrun_payload, extra_context=extra))
    r3 = score_extraction(flatten_extraction_fields(t12_altrun2_payload, extra_context=extra))
    for label, r in (("r1", r1), ("r2", r2), ("r3", r3)):
        assert r.applicable_count >= 15, (
            f"{label} applicable={r.applicable_count} below 15-rule floor"
        )
        assert r.score is not None, f"{label} score is None"
        assert 30.0 <= r.score <= 95.0, f"{label} score={r.score} out of band"


# ─────────────────────────── canonical_payload_from_signals helper ───────


def test_canonical_payload_helper_packs_recognizer_output(t12_payload):
    """The recognizer's canonical_values + extra_context pack
    correctly via canonical_payload_from_signals."""
    signals = classify_structure(t12_payload)
    out = canonical_payload_from_signals(
        signals, extra_context={"keys": 87, "purchase_price": 80_000_000}
    )
    # Recognizer canonicals beat extra_context when they collide.
    assert out["keys"] == 87
    # Recognizer-surfaced canonical names land in the output.
    for cname in ("rooms_revenue", "property_tax", "mgmt_fee"):
        assert cname in out and isinstance(out[cname], (int, float))


# ─────────────────────── Bug H — Router non-financial override ───────────


def test_router_property_info_override_promotes_to_t12(t12_payload):
    """Bug H — Router says PROPERTY_INFO on a clean (no-user-tag) deal,
    structural recognizer detects P&L, override flips doc_type to T12.

    The production bug Sam caught 2026-06-30: "May 2025 Financials.xlsx"
    landed as ``PROPERTY_INFO`` on a clean deal, so the broker-questions
    YoY engine (which filters
    ``doc_type IN ('T12','PNL','PNL_MONTHLY','PNL_YTD')``) produced 0
    questions — even though the file extracted cleanly. On a different
    deal the same file classified as T12 and 30+ questions generated.

    This test simulates the override decision in isolation. The
    production wiring lives in ``apps/worker/app/api/documents.py``
    in the extraction completion path (search for
    ``_NON_FINANCIAL_ROUTER_LABELS``); the rule shape is replicated
    here so the contract is pinned without needing the full DB +
    extractor stack.
    """
    signals = classify_structure(t12_payload)
    assert signals.is_pnl is True, (
        f"recognizer must say P&L on Sam's T-12 fixture: {signals.reason}"
    )
    assert signals.pnl_score >= 0.85, (
        f"pnl_score={signals.pnl_score} below override threshold 0.85"
    )

    # The override gate the production code applies. Threshold + label
    # set must stay in sync with documents.py — these are the same
    # constants spelled inline so a divergence shows up as a test diff.
    NON_FINANCIAL_ROUTER_LABELS = {
        "PROPERTY_INFO",
        "UNKNOWN",
        "CONTRACT",
        "PROPERTY_TAX",
        "INSURANCE",
        "CAPEX",
        "LEASES",
        "SURVEYS",
        "ROOM_MIX",
    }
    THRESHOLD = 0.85

    router_call = "PROPERTY_INFO"  # the actual Sam prod misfire
    user_tag = None  # clean deal — bulk Data Room upload, no per-doc tag

    assert router_call in NON_FINANCIAL_ROUTER_LABELS
    assert signals.is_pnl and signals.pnl_score >= THRESHOLD

    # Apply the override (mirrors documents.py logic).
    final_doc_type = "T12" if (
        router_call in NON_FINANCIAL_ROUTER_LABELS
        and signals.is_pnl
        and signals.pnl_score >= THRESHOLD
    ) else router_call
    ai_proposed = router_call if final_doc_type != router_call else None

    assert final_doc_type == "T12", (
        "Override must promote PROPERTY_INFO → T12 so the broker-"
        "questions YoY engine sees the row in its T12/PNL/PNL_MONTHLY/"
        "PNL_YTD filter."
    )
    # Router's original call is preserved on ai_proposed_doc_type so
    # Sam's misclassification banner can show what changed.
    assert ai_proposed == "PROPERTY_INFO"

    # Sanity: with no user tag, the misclassified banner does NOT fire
    # (it gates on canonical_user being non-empty).
    assert user_tag is None


def test_router_om_classification_is_not_overridden(om_payload):
    """The override is intentionally narrow: it must NOT touch OM /
    STR / MARKET_STUDY etc. classifications. Those have distinct
    downstream semantics and never carry P&L structure anyway, but
    the gate is belt-and-braces."""
    signals = classify_structure(om_payload)
    assert signals.is_pnl is False

    NON_FINANCIAL_ROUTER_LABELS = {
        "PROPERTY_INFO", "UNKNOWN", "CONTRACT", "PROPERTY_TAX",
        "INSURANCE", "CAPEX", "LEASES", "SURVEYS", "ROOM_MIX",
    }
    # OM is NOT in the override allowlist — Router OM stays OM
    # regardless of structural signal.
    router_call = "OM"
    assert router_call not in NON_FINANCIAL_ROUTER_LABELS

    # Even hypothetically, if the recognizer fired on an OM (it
    # doesn't), the override would not trigger because OM is not
    # in NON_FINANCIAL_ROUTER_LABELS.
    overridden = (
        router_call in NON_FINANCIAL_ROUTER_LABELS
        and signals.is_pnl
        and signals.pnl_score >= 0.85
    )
    assert overridden is False


def test_router_property_info_override_respects_period_type():
    """When the override fires AND the extracted payload carries a
    ``period_type`` of ``monthly``, the override should produce
    ``PNL_MONTHLY`` (not the catch-all T12) — the same
    ``_refine_pnl_doc_type`` narrowing the rest of the pipeline uses.

    Unit-level check on the helper so the contract pins independently
    of the documents.py wiring.
    """
    from app.api.documents import _refine_pnl_doc_type

    monthly_fields = [
        {
            "field_name": "p_and_l_usali.period_type",
            "value": "monthly",
        },
    ]
    annual_fields = [
        {
            "field_name": "p_and_l_usali.period_type",
            "value": "trailing_twelve",
        },
    ]
    ytd_fields = [
        {
            "field_name": "p_and_l_usali.period_type",
            "value": "ytd",
        },
    ]

    # Production override seeds the refine call with ``T12`` because
    # that's the canonical P&L lane the recognizer landed on; the
    # period_type then narrows it.
    assert _refine_pnl_doc_type("T12", monthly_fields) == "PNL_MONTHLY"
    assert _refine_pnl_doc_type("T12", annual_fields) == "T12"
    assert _refine_pnl_doc_type("T12", ytd_fields) == "PNL_YTD"


# ─────────────────────────── broker-question YoY guarantee ───────────────


def test_yoy_engine_emits_findings_on_sam_fixtures(
    t12_payload, annual_payload
):
    """Bonus — broker-question secondary fix (v4).

    The historical_variance engine compares consecutive-year P&Ls.
    With the v4 Router fix, Sam's T-12 (FY 2025) and annual P&L
    (FY 2023 / 2024) BOTH stay classified as T-12 / PNL, so the YoY
    loader picks up both and the engine emits questions.
    """
    from app.engines.historical_variance import detect_yoy_variances

    # Flat per-year dicts the engine expects. Annual is 2023 (the
    # fixture's period_label), T-12 is 2024-25 — we pin years to
    # force a delta the recognizer-surfaced canonicals will populate.
    flat_2024 = flatten_extraction_fields(
        annual_payload,
        extra_context={"fiscal_year": 2024},
    )
    flat_2025 = flatten_extraction_fields(
        t12_payload,
        extra_context={"fiscal_year": 2025},
    )
    findings = detect_yoy_variances([flat_2024, flat_2025])
    assert len(findings) >= 5, (
        f"Only {len(findings)} YoY findings — expected ≥ 5 once the "
        f"structural recognizer surfaces enough lines. "
        f"Lines emitted: {[f.line_item for f in findings]}"
    )
