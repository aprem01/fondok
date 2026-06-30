"""Engine tests for ``apps/worker/app/engines/historical_variance.py``.

The engine is the deterministic-math half of Wave 1 #4 — these tests
pin Eshan's confirmed thresholds, the severity ladder, the
question-text template, and the multi-year period_key fan-out.
"""

from __future__ import annotations

import pytest

from app.engines.historical_variance import (
    VARIANCE_THRESHOLDS,
    YoYVarianceFinding,
    detect_yoy_variances,
)


def _row(year: int, **overrides: float) -> dict[str, object]:
    """Build a flat P&L dict carrying every tracked line at a baseline
    value so we can perturb one at a time without accidentally tripping
    a different threshold.
    """
    base: dict[str, object] = {
        "year": year,
        "rooms_revenue": 5_000_000.0,
        "rooms_dept_expense": 1_500_000.0,
        "fb_revenue": 1_000_000.0,
        "fb_dept_expense": 700_000.0,
        "other_operated_revenue": 200_000.0,
        "other_operated_expense": 120_000.0,
        "noi": 1_800_000.0,
        "gop": 2_400_000.0,
        "total_revenue": 6_300_000.0,
    }
    base.update(overrides)
    return base


def test_thresholds_match_eshan_spec() -> None:
    """The threshold catalog is load-bearing. Pin every value so a
    silent edit gets caught by CI."""
    assert VARIANCE_THRESHOLDS["rooms_revenue"] == pytest.approx(0.10)
    assert VARIANCE_THRESHOLDS["rooms_dept_expense"] == pytest.approx(0.10)
    assert VARIANCE_THRESHOLDS["fb_revenue"] == pytest.approx(0.15)
    assert VARIANCE_THRESHOLDS["fb_dept_expense"] == pytest.approx(0.15)
    assert VARIANCE_THRESHOLDS["other_operated_revenue"] == pytest.approx(0.20)
    assert VARIANCE_THRESHOLDS["other_operated_expense"] == pytest.approx(0.20)
    assert VARIANCE_THRESHOLDS["noi"] == pytest.approx(0.05)
    assert VARIANCE_THRESHOLDS["gop"] == pytest.approx(0.05)
    assert VARIANCE_THRESHOLDS["total_revenue"] == pytest.approx(0.05)


def test_no_findings_when_all_within_threshold() -> None:
    """Every line moves by <= 4% — under every threshold, including
    the strict 5% NOI/GOP cutoff. Engine should emit nothing.
    """
    y1 = _row(2023)
    y2 = _row(
        2024,
        rooms_revenue=5_200_000.0,        # +4%
        rooms_dept_expense=1_560_000.0,   # +4%
        fb_revenue=1_040_000.0,           # +4%
        fb_dept_expense=720_000.0,        # +~2.9%
        other_operated_revenue=205_000.0,  # +2.5%
        other_operated_expense=123_000.0,  # +2.5%
        noi=1_872_000.0,                  # +4%
        gop=2_490_000.0,                  # +3.75%
        total_revenue=6_440_000.0,        # +~2.2%
    )
    findings = detect_yoy_variances([y1, y2])
    assert findings == []


def test_fb_revenue_above_threshold_emits_warn() -> None:
    """F&B revenue swings 16% YoY (above 15% threshold, below 30% =
    2× threshold) — expect exactly one WARN finding for ``fb_revenue``.
    """
    y1 = _row(2024)
    y2 = _row(2025, fb_revenue=840_000.0)  # -16% — declined
    findings = detect_yoy_variances([y1, y2])

    fb_findings = [f for f in findings if f.line_item == "fb_revenue"]
    assert len(fb_findings) == 1
    f = fb_findings[0]
    assert f.severity == "WARN"
    assert f.variance_pct == pytest.approx(-0.16, rel=1e-3)
    assert f.period_key == "2024_vs_2025"
    assert f.threshold_pct == pytest.approx(0.15)
    assert f.actual_prior == pytest.approx(1_000_000.0)
    assert f.actual_current == pytest.approx(840_000.0)


def test_fb_revenue_extreme_swing_emits_critical() -> None:
    """F&B revenue swings 35% YoY — past 2× the 15% threshold (=30%).
    Severity must escalate to CRITICAL.
    """
    y1 = _row(2024)
    y2 = _row(2025, fb_revenue=650_000.0)  # -35%
    findings = detect_yoy_variances([y1, y2])

    fb_findings = [f for f in findings if f.line_item == "fb_revenue"]
    assert len(fb_findings) == 1
    assert fb_findings[0].severity == "CRITICAL"
    assert fb_findings[0].variance_pct == pytest.approx(-0.35, rel=1e-3)


def test_noi_5pct_threshold_is_strict() -> None:
    """A 6% NOI swing trips the 5% rolled-up threshold even though it
    wouldn't trip ANY departmental threshold. Sanity-check the rolled-up
    line gets a tighter cutoff.
    """
    y1 = _row(2024)
    y2 = _row(2025, noi=1_692_000.0)  # -6%
    findings = detect_yoy_variances([y1, y2])

    noi_findings = [f for f in findings if f.line_item == "noi"]
    assert len(noi_findings) == 1
    assert noi_findings[0].severity == "WARN"
    assert noi_findings[0].threshold_pct == pytest.approx(0.05)


def test_question_text_format_matches_template() -> None:
    """The broker-facing string is what Eshan asked for verbatim — pin
    every piece of the template (label, direction, percent format,
    currency format, year fence).
    """
    y1 = _row(2024, fb_revenue=1_200_000.0)
    y2 = _row(2025, fb_revenue=1_008_000.0)  # -16%, declined
    findings = detect_yoy_variances([y1, y2])

    fb = next(f for f in findings if f.line_item == "fb_revenue")
    # Template: "F&B revenue declined 16.0% YoY (2024: $1,200,000 → 2025: $1,008,000). What drove this swing?"
    assert fb.question_text.startswith("F&B revenue declined ")
    assert "16.0% YoY" in fb.question_text
    assert "2024: $1,200,000" in fb.question_text
    assert "2025: $1,008,000" in fb.question_text
    assert fb.question_text.endswith("What drove this swing?")


def test_positive_variance_uses_increased_direction() -> None:
    """A swing UP should use ``increased``, not ``declined``."""
    y1 = _row(2024)
    y2 = _row(2025, fb_revenue=1_180_000.0)  # +18%
    findings = detect_yoy_variances([y1, y2])

    fb = next(f for f in findings if f.line_item == "fb_revenue")
    assert "increased" in fb.question_text
    assert "declined" not in fb.question_text
    assert fb.variance_pct == pytest.approx(0.18, rel=1e-3)


def test_three_years_produces_two_period_keys() -> None:
    """3-year input → 2 consecutive pair keys. Every above-threshold
    line should appear in exactly one of the two periods (or both).
    """
    y1 = _row(2023)
    y2 = _row(2024, fb_revenue=850_000.0)   # 2023→2024: -15%, below threshold (not strict >)
    y3 = _row(2025, fb_revenue=700_000.0)   # 2024→2025: ~-17.6%, above 15% threshold

    findings = detect_yoy_variances([y1, y2, y3])

    period_keys = {f.period_key for f in findings}
    # The 2023→2024 move is exactly -15%, which is NOT strictly > the
    # 15% threshold; only the 2024→2025 move trips it. So we expect
    # exactly one period_key.
    assert "2024_vs_2025" in period_keys
    fb_findings = [f for f in findings if f.line_item == "fb_revenue"]
    assert len(fb_findings) == 1
    assert fb_findings[0].period_key == "2024_vs_2025"


def test_three_years_both_periods_trip() -> None:
    """When both YoY pairs cross threshold, both period_keys appear."""
    y1 = _row(2023, fb_revenue=1_000_000.0)
    y2 = _row(2024, fb_revenue=800_000.0)    # -20% — trips
    y3 = _row(2025, fb_revenue=1_000_000.0)  # +25% — trips

    findings = [f for f in detect_yoy_variances([y1, y2, y3]) if f.line_item == "fb_revenue"]
    period_keys = sorted(f.period_key for f in findings)
    assert period_keys == ["2023_vs_2024", "2024_vs_2025"]


def test_unsorted_input_still_yields_correct_pairs() -> None:
    """Caller hands rows in random order — engine must sort by year
    before walking. Otherwise period_key would be garbage.
    """
    y1 = _row(2023)
    y2 = _row(2024)
    y3 = _row(2025, fb_revenue=700_000.0)  # -30% from 2024
    findings = detect_yoy_variances([y3, y1, y2])

    fb_findings = [f for f in findings if f.line_item == "fb_revenue"]
    assert len(fb_findings) == 1
    # 2024 → 2025 (not 2025 → 2023, not 2023 → 2025).
    assert fb_findings[0].period_key == "2024_vs_2025"


def test_aliased_line_names_normalize() -> None:
    """The engine accepts ``food_beverage_revenue`` and the path-style
    ``p_and_l_usali.operating_revenue.food_beverage_revenue`` — both
    map to ``fb_revenue``.
    """
    y1 = {
        "year": 2024,
        "p_and_l_usali.operating_revenue.food_beverage_revenue": 1_000_000.0,
    }
    y2 = {
        "year": 2025,
        "food_beverage_revenue": 800_000.0,  # -20%
    }
    findings = detect_yoy_variances([y1, y2])
    fb = [f for f in findings if f.line_item == "fb_revenue"]
    assert len(fb) == 1
    assert fb[0].severity == "WARN"
    assert fb[0].variance_pct == pytest.approx(-0.20, rel=1e-3)


def test_zero_prior_value_is_skipped() -> None:
    """Division by zero is silently dropped (a different rule should
    flag "line went from 0 to something", not this engine).
    """
    y1 = _row(2024, fb_revenue=0.0)
    y2 = _row(2025, fb_revenue=500_000.0)
    findings = detect_yoy_variances([y1, y2])
    fb = [f for f in findings if f.line_item == "fb_revenue"]
    assert fb == []


def test_rows_without_year_are_dropped() -> None:
    """No year → can't compute a YoY pair → row is excluded silently."""
    y1 = {"fb_revenue": 1_000_000.0}  # no year/period/fiscal_year
    y2 = _row(2025, fb_revenue=500_000.0)
    findings = detect_yoy_variances([y1, y2])
    # With only one year-resolved row, no pair → no findings.
    assert findings == []


def test_year_extracted_from_period_label() -> None:
    """Loader sometimes only carries ``period_label`` (e.g. "FY2024").
    The engine still resolves the year and pairs the rows.
    """
    y1 = {
        "period_label": "FY2024",
        "fb_revenue": 1_000_000.0,
    }
    y2 = {
        "period_label": "FY2025",
        "fb_revenue": 800_000.0,  # -20%
    }
    findings = detect_yoy_variances([y1, y2])
    fb = [f for f in findings if f.line_item == "fb_revenue"]
    assert len(fb) == 1


def test_finding_dataclass_round_trips() -> None:
    """Sanity check — the engine emits ``YoYVarianceFinding`` instances
    (not bare tuples), so the API layer can dot-access them safely.
    """
    y1 = _row(2024)
    y2 = _row(2025, fb_revenue=800_000.0)  # -20%
    findings = detect_yoy_variances([y1, y2])
    assert all(isinstance(f, YoYVarianceFinding) for f in findings)


def test_dict_valued_gop_is_skipped_not_coerced_to_zero() -> None:
    """Sam QA 2026-06-29 (batch B, GOP-zero canary).

    The 2022 P&L extraction landed ``p_and_l_usali.gop`` as a NESTED
    OBJECT (``{gop_margin: 0.30, monthly: {apr_2022: {gop: 0}, ...}}``)
    instead of a scalar. The engine used to fall through to ``return
    None`` on dict via the coercer's catch-all, which was fine for THIS
    layout — but a scorer fallback that returned the dict at a different
    key could still leak. We now reject structured payloads explicitly
    BEFORE coercing, both in the alias-walk loop and in the scorer
    fallback, so a dict-year is skipped (no GOP key in the normalized
    output for that year) — never silently zero-filled.

    Without this guard the variance engine would emit
    "GOP $4.85M → $0 (-100%)" — a bogus broker question that
    Sam flagged on his 2026-06-25 call.
    """
    # 2021: clean scalar GOP via the dot-flattened USALI path.
    y2021 = {
        "year": 2021,
        "p_and_l_usali.gross_operating_profit_usd": 4_850_000.0,
        "rooms_revenue": 5_000_000.0,
    }
    # 2022: GOP arrived as a nested object — exactly the shape Sam's
    # bad extraction shipped. ``gop_margin`` is a ratio, not a $ amount;
    # ``monthly`` is a per-month dict that's irrelevant for the YoY
    # rollup. Engine must NOT coerce this into a scalar.
    y2022 = {
        "year": 2022,
        "p_and_l_usali.gop": {
            "gop_margin": 0.30,
            "monthly": {"apr_2022": {"gop": 0}, "may_2022": {"gop": 0}},
        },
        "rooms_revenue": 5_000_000.0,
    }
    findings = detect_yoy_variances([y2021, y2022])

    # The headline assertion: no bogus GOP finding for the 2021↔2022 pair.
    gop_findings = [f for f in findings if f.line_item == "gop"]
    assert gop_findings == [], (
        "GOP-zero canary: the engine emitted a bogus finding for a "
        f"dict-valued 2022 GOP — got {gop_findings!r}"
    )


def test_dict_valued_at_bare_canonical_is_skipped() -> None:
    """Sister case to ``test_dict_valued_gop_is_skipped_not_coerced_to_zero``
    — the dict lands on the BARE canonical key (``gop``) rather than
    the dotted USALI path. Same coerce-or-skip contract: skip the year.
    """
    y2023 = _row(2023)
    y2024 = {
        "year": 2024,
        # The extractor occasionally shoves a nested object straight
        # onto the bare canonical when it tried to "normalize" the
        # source workbook's GOP block.
        "gop": {"gop_margin": 0.32, "monthly": {}},
        "rooms_revenue": 5_000_000.0,
    }
    findings = detect_yoy_variances([y2023, y2024])
    gop_findings = [f for f in findings if f.line_item == "gop"]
    assert gop_findings == [], (
        f"dict at bare 'gop' must be skipped — got {gop_findings!r}"
    )


def test_list_and_nonparseable_string_are_skipped() -> None:
    """``_coerce_value`` and ``_normalize_pnl`` both reject list/tuple/set
    and strings that don't parse as a number. The engine should never
    emit a finding from any of these — they're all "data quality, skip"
    not "the line went to zero".
    """
    y1 = _row(2024)
    # Pile of garbage shapes on the 2025 row's GOP slot.
    for bad in ([1, 2, 3], (1, 2), {1, 2}, "not a number", "$$"):
        y2 = _row(2025)
        y2["gop"] = bad  # type: ignore[assignment]
        findings = detect_yoy_variances([y1, y2])
        gop_findings = [f for f in findings if f.line_item == "gop"]
        assert gop_findings == [], (
            f"bad gop value {bad!r} must be skipped — got {gop_findings!r}"
        )


def test_nan_and_inf_values_are_skipped() -> None:
    """NaN / ±inf are numerically coercible (``float('nan')`` returns
    nan) but propagate into a nonsense variance. The coercer rejects
    non-finite values so the engine never emits ``inf% YoY`` findings.
    """
    y1 = _row(2024)
    for bad in (float("nan"), float("inf"), float("-inf")):
        y2 = _row(2025)
        y2["gop"] = bad
        findings = detect_yoy_variances([y1, y2])
        gop_findings = [f for f in findings if f.line_item == "gop"]
        assert gop_findings == [], (
            f"non-finite gop value {bad!r} must be skipped — got {gop_findings!r}"
        )


def test_scalar_year_still_emits_finding_alongside_dict_year() -> None:
    """When ONE year has the bad nested-dict GOP and the next has a
    clean scalar, the dict-year is dropped from the GOP key but its
    OTHER line items (e.g. ``rooms_revenue``) still participate in the
    next-year pairing. Coerce-or-skip is per-line, not per-year.
    """
    y1 = _row(2023)
    y2 = {
        "year": 2024,
        # GOP arrives as junk dict → skipped for the GOP key only.
        "gop": {"gop_margin": 0.30},
        # Rooms revenue still scalar → still in play.
        "rooms_revenue": 6_000_000.0,  # +20% vs y1's 5M baseline
    }
    y3 = _row(2025, rooms_revenue=6_000_000.0)  # flat vs y2

    findings = detect_yoy_variances([y1, y2, y3])
    rooms_findings = [f for f in findings if f.line_item == "rooms_revenue"]
    # 2023→2024 rooms swing trips the 10% threshold; 2024→2025 doesn't.
    assert len(rooms_findings) == 1
    assert rooms_findings[0].period_key == "2023_vs_2024"
    # And no GOP finding leaks from the dict-year side.
    assert [f for f in findings if f.line_item == "gop"] == []


def test_gop_dollar_resolves_over_sibling_margin_pct() -> None:
    """Sam QA 2026-06-29 follow-up (post-287f602).

    The 287f602 dict/list/NaN guard only catches non-numeric values.
    The 2022 extraction Sam re-ran shipped a NUMERIC sibling at
    ``p_and_l_usali.gop.gop_margin_pct`` (0.3975) alongside the real
    dollar field at ``p_and_l_usali.gop.gross_operating_profit_usd``
    ($5.86M). The token-match v3 fallback picked the shorter / tighter
    margin field first → variance engine read 0.40 as GOP, compared
    to 2021's $4.85M, and emitted a bogus "GOP -100% ($4.85M → $0)"
    finding (the $0 in the message is the rounded-down formatter on
    a $0.40 value).

    Fix: the explicit alias chain for ``gop`` now lists
    ``p_and_l_usali.gop.gross_operating_profit_usd`` so the resolver
    finds the dollar field before token-match runs. The
    ``_TOKEN_FORBIDDEN`` map also rejects ``margin``/``pct`` candidates
    for ``gop`` / ``noi`` canonicals as belt-and-braces.

    This test reproduces Sam's exact extraction shape (dollar + margin
    siblings on the same nested object) and asserts the engine
    reads the dollar number, not the margin ratio.
    """
    y2021 = {
        "year": 2021,
        "p_and_l_usali.gop.gross_operating_profit_usd": 4_851_110.0,
        "rooms_revenue": 5_000_000.0,
    }
    y2022 = {
        "year": 2022,
        # Both siblings present, exactly as the re-extracted 2022 doc.
        "p_and_l_usali.gop.gross_operating_profit_usd": 5_855_510.0,
        "p_and_l_usali.gop.gop_margin_pct": 0.3975,
        "rooms_revenue": 5_000_000.0,
    }
    findings = detect_yoy_variances([y2021, y2022])

    gop_findings = [f for f in findings if f.line_item == "gop"]
    # 2021→2022 GOP went from $4.85M to $5.86M = +20.7%. Threshold is
    # 10% for GOP so it should fire as WARN. The KEY assertion is that
    # variance is positive and ~+0.21, NOT -1.00 (which is what the
    # margin-leak bug emitted).
    assert len(gop_findings) == 1, (
        f"expected exactly one GOP 2021↔2022 finding, got {gop_findings!r}"
    )
    f = gop_findings[0]
    assert f.period_key == "2021_vs_2022"
    assert f.actual_prior == 4_851_110.0
    assert f.actual_current == 5_855_510.0, (
        f"actual_current must be the $5.86M dollar field, not the "
        f"0.3975 margin ratio — got {f.actual_current}"
    )
    assert 0.15 < f.variance_pct < 0.30, (
        f"variance_pct should be ~+21% (5.86M vs 4.85M), got {f.variance_pct}"
    )


def test_noi_dollar_resolves_over_sibling_margin_pct() -> None:
    """Same margin-leak protection for NOI as for GOP — both canonicals
    are listed in ``_TOKEN_FORBIDDEN`` rejecting ``margin``/``pct``.
    """
    y2023 = {
        "year": 2023,
        "p_and_l_usali.noi.net_operating_income_usd": 2_000_000.0,
        "rooms_revenue": 5_000_000.0,
    }
    y2024 = {
        "year": 2024,
        "p_and_l_usali.noi.net_operating_income_usd": 2_500_000.0,
        "p_and_l_usali.noi.noi_margin_pct": 0.40,
        "rooms_revenue": 5_000_000.0,
    }
    findings = detect_yoy_variances([y2023, y2024])
    noi_findings = [f for f in findings if f.line_item == "noi"]
    assert len(noi_findings) == 1
    f = noi_findings[0]
    assert f.actual_current == 2_500_000.0, (
        f"NOI actual_current must be $2.5M, not the 0.40 margin — got {f.actual_current}"
    )
