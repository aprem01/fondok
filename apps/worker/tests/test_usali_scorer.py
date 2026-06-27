"""Unit tests for ``services.usali_scorer.score_extraction``.

The scorer is pure Python (no DB, no LLM, no I/O beyond the cached
rule loader), so these tests run fast and exercise every branch of
the rule-evaluation matrix:

* math-identity rule passes when fields reconcile
* math-identity rule fails when drift exceeds the tolerance
* range rule passes when value is in band
* range rule fails when value is outside band
* missing fields gracefully skip rules (not "fail")
* fewer than 5 applicable rules ⇒ inconclusive (score = None)
* deviation severity is inherited from the catalog rule
* market-context-dependent rules are parked, not failed, when the
  deal lacks the context flag

The scorer is wired against the production rule catalog
(``evals/golden-set/usali-rules.csv``) — we don't mock the rule list
because the production behavior of the alias map + identity detection
depends on real catalog shapes.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import pytest

# Make ``apps.worker.app...`` importable when pytest is invoked from the
# repo root. The worker's pyproject already declares ``app`` as a package
# under ``apps/worker``; setting that as the path lets us reach it from
# any cwd a CI/dev shell might invoke pytest from.
_WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKER_ROOT))

# Force-pin the rule catalog to the canonical location so tests pass
# regardless of cwd. The loader honors FONDOK_USALI_RULES_PATH for this.
_RULES_PATH = (
    _WORKER_ROOT.parent.parent / "evals" / "golden-set" / "usali-rules.csv"
)
os.environ.setdefault("FONDOK_USALI_RULES_PATH", str(_RULES_PATH))

from app.services.usali_scorer import (  # noqa: E402
    USALIDeviation,
    USALIScore,
    deviations_to_jsonb,
    flatten_extraction_fields,
    score_extraction,
)
from app.usali_rules import load_usali_rules  # noqa: E402


# ─────────────────────────── helpers ───────────────────────────


def _ids(score: USALIScore) -> set[str]:
    return {d.rule_id for d in score.deviations}


def _by_id(score: USALIScore, rule_id: str) -> USALIDeviation | None:
    for d in score.deviations:
        if d.rule_id == rule_id:
            return d
    return None


# A "healthy" P&L payload that satisfies every numeric identity + sits
# squarely inside every ratio band. Used as the baseline; individual
# tests perturb one field to surface a specific deviation.
@pytest.fixture
def healthy_fields() -> dict[str, float]:
    rooms = 6_000_000.0
    fb = 1_500_000.0
    other = 500_000.0
    total_revenue = rooms + fb + other  # 8_000_000

    rooms_dept_exp = 1_500_000.0  # rooms dept profit = 4.5M → margin 75% (full-service band)
    fb_dept_exp = 1_200_000.0     # fb dept profit = 300k → margin 20% (full-service band)
    other_dept_exp = 250_000.0
    dept_expenses = rooms_dept_exp + fb_dept_exp + other_dept_exp  # 2.95M
    undistributed = 1_800_000.0   # admin/sales/marketing/utilities/r&m roll-up
    gop = total_revenue - dept_expenses - undistributed  # 3.25M (40.6% margin)

    mgmt_fee = total_revenue * 0.03   # 240k (3% — in band)
    ffe_reserve = total_revenue * 0.04  # 320k (4% — brand-floor compliant)
    fixed_charges = 500_000.0
    noi = gop - mgmt_fee - ffe_reserve - fixed_charges  # 2,190,000 (27.4% margin)

    keys = 132
    occupancy = 0.74
    adr = 332.07
    revpar = occupancy * adr  # 245.73 ≈ occupancy × ADR

    return {
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
        # Identities expressed with `total_dept_expense`-style names too.
        "total_dept_expense": dept_expenses,
        "dept_expenses_by_line": [rooms_dept_exp, fb_dept_exp, other_dept_exp],
        # Department profits for the rooms/fb margin rules.
        "rooms_dept_profit": rooms - rooms_dept_exp,
        "fb_dept_profit": fb - fb_dept_exp,
        # Operating costs that drive labor / utilities / R&M / A&G rules.
        "total_labor": total_revenue * 0.34,       # 34% (in band)
        "utilities_expense": keys * 1_500,         # $1500/key (in band)
        "marketing_expense": total_revenue * 0.05,  # 5% (in band)
        "rm_expense": total_revenue * 0.04,        # 4% (in band)
        "ag_expense": total_revenue * 0.08,        # 8% (in band)
        "insurance_expense": keys * 1_200,         # $1200/key (in band, non-coastal)
        "property_tax": 200_000.0,                  # 2% of property_value
        "property_value": 10_000_000.0,
        "keys": keys,
        "labor_cost_per_occupied_room": 45.0,      # in 15..90 band
    }


# ─────────────────────────── canary: catalog loads ───────────────────────────


def test_rule_catalog_loads_nonempty() -> None:
    rules = load_usali_rules()
    assert len(rules) >= 60, (
        f"USALI rule catalog should be ≥60 rules; got {len(rules)}"
    )


# ─────────────────────────── math-identity rule pass ───────────────────────────


def test_revpar_identity_passes_when_consistent(healthy_fields: dict[str, float]) -> None:
    score = score_extraction(healthy_fields)
    # REVPAR_CHECK should NOT appear in deviations when revpar = occ × adr.
    assert "REVPAR_CHECK" not in _ids(score)


def test_gop_identity_passes_when_consistent(healthy_fields: dict[str, float]) -> None:
    score = score_extraction(healthy_fields)
    assert "GOP_IDENTITY" not in _ids(score)


def test_noi_identity_passes_when_consistent(healthy_fields: dict[str, float]) -> None:
    score = score_extraction(healthy_fields)
    assert "NOI_IDENTITY" not in _ids(score)


def test_revenue_sum_identity_passes_when_consistent(
    healthy_fields: dict[str, float],
) -> None:
    score = score_extraction(healthy_fields)
    assert "REVENUE_SUM" not in _ids(score)


# ─────────────────────────── math-identity rule fail ───────────────────────────


def test_revpar_identity_fails_when_drift_exceeds_tolerance(
    healthy_fields: dict[str, float],
) -> None:
    # Set revpar 5% off so abs(revpar - occ*adr)/revpar ≈ 5% >> 0.5%.
    bad = dict(healthy_fields)
    bad["revpar"] = bad["revpar"] * 1.05

    score = score_extraction(bad)
    dev = _by_id(score, "REVPAR_CHECK")
    assert dev is not None, "REVPAR_CHECK should fire when revpar drifts >0.5%"
    assert dev.severity == "CRITICAL"
    assert dev.actual_value is not None
    assert dev.actual_value > 0.04  # ≈ 5% drift
    assert "doesn't reconcile" in dev.message or "drift" in dev.message.lower()


def test_gop_identity_fails_when_gop_misreported(
    healthy_fields: dict[str, float],
) -> None:
    bad = dict(healthy_fields)
    # Inflate reported GOP by 10% while leaving revenue/expense lines intact.
    bad["gop"] = bad["gop"] * 1.10
    # Adjust NOI to still match its identity (so we isolate GOP_IDENTITY).
    bad["noi"] = bad["gop"] - bad["mgmt_fee"] - bad["ffe_reserve"] - bad["fixed_charges"]

    score = score_extraction(bad)
    dev = _by_id(score, "GOP_IDENTITY")
    assert dev is not None
    assert dev.severity == "CRITICAL"


# ─────────────────────────── range rule pass ───────────────────────────


def test_gop_margin_range_passes_when_in_band(
    healthy_fields: dict[str, float],
) -> None:
    score = score_extraction(healthy_fields)
    assert "GOP_MARGIN_RANGE" not in _ids(score), (
        "GOP margin 40.6% is well inside the 15-55% band"
    )


def test_occupancy_range_passes_when_in_band(
    healthy_fields: dict[str, float],
) -> None:
    score = score_extraction(healthy_fields)
    assert "OCCUPANCY_RANGE" not in _ids(score)


# ─────────────────────────── range rule fail ───────────────────────────


def test_mgmt_fee_range_fires_when_above_band(
    healthy_fields: dict[str, float],
) -> None:
    # Push management fee to 8.2% — above the 2-6% band.
    bad = dict(healthy_fields)
    bad["mgmt_fee"] = bad["total_revenue"] * 0.082
    # Recompute NOI so NOI_IDENTITY stays clean (isolate the fee rule).
    bad["noi"] = (
        bad["gop"] - bad["mgmt_fee"] - bad["ffe_reserve"] - bad["fixed_charges"]
    )

    score = score_extraction(bad)
    dev = _by_id(score, "MGMT_FEE_RANGE")
    assert dev is not None
    assert dev.severity == "WARN"
    assert dev.actual_value is not None
    assert math.isclose(dev.actual_value, 0.082, abs_tol=1e-6)
    # The message should reference both the actual percent and the band.
    assert "8.2%" in dev.message
    assert "2.0%" in dev.message or "2%" in dev.message


def test_occupancy_range_fires_when_below_floor(
    healthy_fields: dict[str, float],
) -> None:
    bad = dict(healthy_fields)
    bad["occupancy"] = 0.25  # below 0.40 floor
    bad["revpar"] = bad["occupancy"] * bad["adr"]  # keep the identity clean
    score = score_extraction(bad)
    dev = _by_id(score, "OCCUPANCY_RANGE")
    assert dev is not None
    assert dev.severity == "WARN"


def test_ffe_reserve_range_fires_when_below_floor(
    healthy_fields: dict[str, float],
) -> None:
    bad = dict(healthy_fields)
    bad["ffe_reserve"] = bad["total_revenue"] * 0.01  # 1% — below 3% floor
    bad["noi"] = (
        bad["gop"] - bad["mgmt_fee"] - bad["ffe_reserve"] - bad["fixed_charges"]
    )
    score = score_extraction(bad)
    dev = _by_id(score, "FFE_RESERVE_RANGE")
    assert dev is not None
    assert dev.severity == "WARN"


# ─────────────────────────── severity assignment ───────────────────────────


def test_deviation_severity_inherited_from_catalog(
    healthy_fields: dict[str, float],
) -> None:
    # Build a payload that fails one CRITICAL and one WARN rule.
    bad = dict(healthy_fields)
    bad["revpar"] = bad["revpar"] * 1.10           # CRITICAL identity fail
    bad["mgmt_fee"] = bad["total_revenue"] * 0.10   # WARN range fail
    bad["noi"] = (
        bad["gop"] - bad["mgmt_fee"] - bad["ffe_reserve"] - bad["fixed_charges"]
    )

    score = score_extraction(bad)
    crit = _by_id(score, "REVPAR_CHECK")
    warn = _by_id(score, "MGMT_FEE_RANGE")
    assert crit is not None and crit.severity == "CRITICAL"
    assert warn is not None and warn.severity == "WARN"


# ─────────────────────────── missing fields ───────────────────────────


def test_missing_fields_skip_rules_not_fail() -> None:
    # A payload with only one ratio populated — almost every rule
    # should "skip" (not appear as a deviation), not fail.
    sparse = {"occupancy": 0.74, "adr": 332.0, "revpar": 245.68}

    score = score_extraction(sparse)
    # Every deviation must be either one of the three rules whose
    # inputs we provided, or a market-context placeholder (the coastal
    # / seasonal rules park themselves on every deal that doesn't carry
    # the flag — that's the policy, not a failure).
    expected_numeric = {
        "REVPAR_CHECK",
        "MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY",
        "OCCUPANCY_RANGE",
        "ADR_RANGE",
    }
    for d in score.deviations:
        if d.requires_market_context:
            continue
        assert d.rule_id in expected_numeric, (
            f"unexpected non-context deviation on sparse payload: {d.rule_id}"
        )
    # No P&L identity should fire — those rules' inputs aren't present.
    assert "GOP_IDENTITY" not in _ids(score)
    assert "NOI_IDENTITY" not in _ids(score)
    assert "REVENUE_SUM" not in _ids(score)


def test_zero_denominator_skips_gracefully() -> None:
    # A brand-new property with no revenue would otherwise crash a
    # `gop / total_revenue` rule. The scorer must skip cleanly.
    payload = {
        "total_revenue": 0.0,
        "gop": 0.0,
        "noi": 0.0,
        "occupancy": 0.50,
        "adr": 200.0,
        "revpar": 100.0,
    }
    score = score_extraction(payload)
    # No ratio rule should appear as a deviation.
    assert "GOP_MARGIN_RANGE" not in _ids(score)
    assert "NOI_MARGIN_RANGE" not in _ids(score)


# ─────────────────────────── inconclusive ───────────────────────────


def test_inconclusive_when_fewer_than_five_applicable_rules() -> None:
    # Provide just two fields → at most 2 rules can evaluate.
    score = score_extraction({"occupancy": 0.74, "adr": 332.0})
    assert score.inconclusive is True
    assert score.score is None
    assert score.applicable_count < 5


def test_not_inconclusive_when_many_rules_apply(
    healthy_fields: dict[str, float],
) -> None:
    score = score_extraction(healthy_fields)
    assert score.inconclusive is False
    assert score.applicable_count >= 5
    assert score.score is not None
    assert 0.0 <= score.score <= 100.0
    # The healthy fixture is full-service-shaped (F&B ≈ 19% of revenue),
    # so the catalog's select-service-targeted rules (FB_DEPT_NA_SELECT,
    # LABOR_PCT_REVENUE_SELECT, etc.) WILL fire — they're testing a
    # different property type. That's correct catalog behavior. We
    # assert the score stays high (≥90), not perfect, to allow for
    # those mutually-exclusive select-vs-full counterparts.
    assert score.score >= 90.0, (
        f"healthy full-service doc should still score ≥90; got {score.score} "
        f"with deviations: {[d.rule_id for d in score.deviations]}"
    )


def test_score_reflects_pass_rate(healthy_fields: dict[str, float]) -> None:
    # Add a single WARN failure on top of the healthy baseline. Score
    # should drop below 100 but stay well above 80.
    bad = dict(healthy_fields)
    bad["mgmt_fee"] = bad["total_revenue"] * 0.10
    bad["noi"] = (
        bad["gop"] - bad["mgmt_fee"] - bad["ffe_reserve"] - bad["fixed_charges"]
    )

    score = score_extraction(bad)
    assert score.score is not None
    assert 80.0 <= score.score < 100.0
    # passed_count = applicable_count - deviations (non-context).
    real_failures = sum(
        1 for d in score.deviations if not d.requires_market_context
    )
    assert score.applicable_count - score.passed_count == real_failures


# ─────────────────────────── market-context handling ───────────────────────────


def test_coastal_rule_marked_requires_context_when_flag_absent(
    healthy_fields: dict[str, float],
) -> None:
    # Bait the coastal insurance rule: set insurance per key to $4,000
    # (above the non-coastal $500-$2500 band, inside the coastal
    # $2500-$8000 band). Without a ``coastal`` flag on the payload, the
    # scorer should park the coastal-flagged rule with
    # requires_market_context=True INSTEAD of evaluating it.
    bad = dict(healthy_fields)
    bad["insurance_expense"] = bad["keys"] * 4_000

    score = score_extraction(bad)
    dev = _by_id(score, "INSURANCE_PER_KEY_COASTAL")
    assert dev is not None
    assert dev.requires_market_context is True
    assert "coastal" in dev.message.lower()


def test_coastal_rule_evaluates_when_flag_set(
    healthy_fields: dict[str, float],
) -> None:
    # With coastal=True, INSURANCE_PER_KEY_COASTAL should evaluate
    # normally — passes if $4,000/key is inside $2500-$8000.
    bad = dict(healthy_fields)
    bad["insurance_expense"] = bad["keys"] * 4_000
    bad["coastal"] = True
    score = score_extraction(bad)
    dev = _by_id(score, "INSURANCE_PER_KEY_COASTAL")
    # When the flag is set, the rule either passes (no deviation) or
    # fails with a numeric value — but never as requires_market_context.
    if dev is not None:
        assert dev.requires_market_context is False


def test_context_dependent_rule_excluded_from_applicable_count(
    healthy_fields: dict[str, float],
) -> None:
    # The applicable count when running on the healthy doc (no coastal
    # flag) should NOT include INSURANCE_PER_KEY_COASTAL or
    # MULTI_FIELD_INSURANCE_COASTAL_RISK. Set things up so those rules
    # would have evaluated successfully if context were present.
    score = score_extraction(healthy_fields)
    devs_with_ctx = [d for d in score.deviations if d.requires_market_context]
    # Counts: passed + numeric_failed == applicable. Context-tagged
    # deviations are excluded.
    numeric_failed = sum(
        1 for d in score.deviations if not d.requires_market_context
    )
    assert score.applicable_count == score.passed_count + numeric_failed
    # And there's at least one context-tagged deviation (the coastal rule).
    assert len(devs_with_ctx) >= 1


# ─────────────────────────── JSONB serializer ───────────────────────────


def test_deviations_to_jsonb_round_trip(healthy_fields: dict[str, float]) -> None:
    bad = dict(healthy_fields)
    bad["mgmt_fee"] = bad["total_revenue"] * 0.10
    bad["noi"] = (
        bad["gop"] - bad["mgmt_fee"] - bad["ffe_reserve"] - bad["fixed_charges"]
    )
    score = score_extraction(bad)
    payload = deviations_to_jsonb(score)
    assert payload["applicable_count"] == score.applicable_count
    assert payload["passed_count"] == score.passed_count
    assert payload["inconclusive"] is score.inconclusive
    assert isinstance(payload["deviations"], list)
    # Each entry must carry the keys the web UI consumes.
    if payload["deviations"]:
        first = payload["deviations"][0]
        for key in (
            "rule_id",
            "rule_name",
            "severity",
            "message",
            "actual_value",
            "threshold_min",
            "threshold_max",
            "requires_market_context",
        ):
            assert key in first


# ─────────────────────────── extraction-payload adapter ───────────────────────────


def test_flatten_extraction_fields_exposes_tail_aliases() -> None:
    # The extractor emits dotted-path field names; the flatten helper
    # should also expose the tail token so the scorer's alias map can
    # resolve canonical names.
    extraction_payload = [
        {"field_name": "p_and_l_usali.revpar_usd", "value": 245.0},
        {"field_name": "p_and_l_usali.occupancy_pct", "value": 0.74},
        {"field_name": "p_and_l_usali.adr_usd", "value": 331.08},
    ]
    flat = flatten_extraction_fields(extraction_payload)
    # Both the full path and the tail are present.
    assert flat["p_and_l_usali.revpar_usd"] == 245.0
    assert flat["revpar_usd"] == 245.0


def test_scorer_resolves_dotted_paths_via_alias_map() -> None:
    # End-to-end: a payload entirely in dotted-path form should still
    # let the scorer score the doc (via the alias map).
    extraction_payload = [
        {"field_name": "p_and_l_usali.revpar_usd", "value": 245.0},
        {"field_name": "p_and_l_usali.occupancy_pct", "value": 0.74},
        {"field_name": "p_and_l_usali.adr_usd", "value": 331.08},
        {"field_name": "p_and_l_usali.total_revenue_usd", "value": 8_000_000.0},
        {"field_name": "p_and_l_usali.rooms_revenue", "value": 6_000_000.0},
        {"field_name": "p_and_l_usali.fb_revenue", "value": 1_500_000.0},
        {"field_name": "p_and_l_usali.other_revenue", "value": 500_000.0},
        {"field_name": "p_and_l_usali.gop", "value": 3_250_000.0},
        {"field_name": "p_and_l_usali.noi", "value": 2_190_000.0},
        {"field_name": "p_and_l_usali.mgmt_fee", "value": 240_000.0},
        {"field_name": "p_and_l_usali.ffe_reserve", "value": 320_000.0},
        {"field_name": "p_and_l_usali.fixed_charges", "value": 500_000.0},
        {"field_name": "p_and_l_usali.dept_expenses", "value": 2_950_000.0},
        {"field_name": "p_and_l_usali.undistributed_expenses", "value": 1_800_000.0},
    ]
    flat = flatten_extraction_fields(extraction_payload)
    score = score_extraction(flat)
    assert score.applicable_count >= 5
    # The REVPAR identity should pass (revpar ≈ occ × adr).
    assert "REVPAR_CHECK" not in _ids(score)


# ─────────────────────────── numeric string tolerance ───────────────────────────


def test_numeric_strings_are_coerced() -> None:
    # The extractor occasionally emits values as strings ("$185.40",
    # "74%"). The scorer must coerce these so the rule fires correctly.
    payload = {
        "occupancy": "74%",   # → 0.74
        "adr": "$331.08",     # → 331.08
        "revpar": "245.00",    # → 245.0 (within tolerance of 0.74 × 331.08)
    }
    score = score_extraction(payload)
    # REVPAR identity should evaluate, not skip due to type errors.
    # Drift: |245 - 244.999| / 245 ≈ 0.000004 → passes.
    assert "REVPAR_CHECK" not in _ids(score)


# ─────────────────────────── safety: no eval ───────────────────────────


def test_scorer_does_not_use_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    """If we ever regress and use ``eval()`` to interpret a formula, this
    test snapshots that behavior is forbidden — replacing builtins.eval
    with a tripwire and confirming a scoring run still completes.
    """
    import builtins

    def _tripwire(*args: object, **kwargs: object) -> None:
        raise AssertionError("scorer used builtins.eval — must use AST interpreter")

    monkeypatch.setattr(builtins, "eval", _tripwire)
    score = score_extraction({"occupancy": 0.74, "adr": 332.0, "revpar": 245.7})
    # Should produce a result without raising.
    assert isinstance(score, USALIScore)
