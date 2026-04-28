"""Tests for the rule-based memo quality eval.

Hotel-investment-banker voice rules: marketing adjectives must be
backed by digits, no filler, compact number formatting, occupancy in
percent form, ADR with $ prefix, RevPAR paired with ADR + Occupancy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.evals import evaluate_memo
from fondok_schemas import (
    Citation,
    ConfidenceReport,
    InvestmentMemo,
    MemoSection,
    MemoSectionId,
)


# ─────────────────────── helpers ───────────────────────


def _make_memo(*sections: MemoSection) -> InvestmentMemo:
    return InvestmentMemo(
        deal_id=uuid4(),
        sections=list(sections),
        generated_at=datetime.now(UTC),
        confidence=ConfidenceReport(
            overall=0.9,
            by_field={},
            low_confidence_fields=[],
            requires_human_review=False,
        ),
        version=1,
    )


def _section(
    section_id: MemoSectionId,
    body: str,
    *,
    title: str | None = None,
) -> MemoSection:
    return MemoSection(
        section_id=section_id,
        title=title or section_id.value.replace("_", " ").title(),
        body=body,
        citations=[
            Citation(
                document_id=uuid4(),
                page=1,
                excerpt="ref",
            )
        ],
    )


def _has_finding(findings, rule_id: str) -> bool:
    return any(f.rule_id == rule_id for f in findings)


# ─────────────────────── marketing adjectives ───────────────────────


def test_strong_without_number_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.INVESTMENT_THESIS,
            "The asset shows strong returns and a robust market position.",
        )
    )
    result = evaluate_memo(memo)
    assert _has_finding(
        result.findings, "marketing_adjective_without_number"
    ), "expected 'strong' to be flagged without a nearby digit"


def test_strong_near_number_not_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.INVESTMENT_THESIS,
            "Strong 24.5% IRR justifies the basis at this price point.",
        )
    )
    result = evaluate_memo(memo)
    assert not any(
        f.rule_id == "marketing_adjective_without_number"
        for f in result.findings
    )


def test_premier_flagged_no_digit() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.MARKET_ANALYSIS,
            "Located in a premier oceanfront submarket with great views.",
        )
    )
    result = evaluate_memo(memo)
    assert _has_finding(
        result.findings, "marketing_adjective_without_number"
    )


# ─────────────────────── filler phrases ───────────────────────


def test_it_is_worth_noting_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.RISK_FACTORS,
            "It is worth noting that the property has 132 keys and a 24.5% IRR.",
        )
    )
    result = evaluate_memo(memo)
    assert _has_finding(result.findings, "filler_phrase")
    # Filler is an error, not a warning.
    assert any(f.severity == "error" for f in result.findings)


def test_going_forward_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.RECOMMENDATION,
            "Going forward we expect 12.4% growth in NOI.",
        )
    )
    result = evaluate_memo(memo)
    assert _has_finding(result.findings, "filler_phrase")


# ─────────────────────── number formatting ───────────────────────


def test_long_currency_form_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.FINANCIAL_ANALYSIS,
            "Purchase price of $36,400,000 yields a 6.80% cap rate.",
        )
    )
    result = evaluate_memo(memo)
    finding = next(
        (f for f in result.findings if f.rule_id == "number_formatting_long_currency"),
        None,
    )
    assert finding is not None
    assert finding.severity == "error"
    # Suggestion should compact to $36.4M.
    assert finding.suggestion is not None
    assert "$36.4M" in finding.suggestion


def test_compact_currency_not_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.FINANCIAL_ANALYSIS,
            "Purchase price of $36.4M yields a 6.80% cap rate.",
        )
    )
    result = evaluate_memo(memo)
    assert not any(
        f.rule_id == "number_formatting_long_currency" for f in result.findings
    )


def test_overspec_percent_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.RETURNS_SUMMARY,
            "Year-1 levered IRR projects to 23.481% on a 5-year hold.",
        )
    )
    result = evaluate_memo(memo)
    assert _has_finding(result.findings, "number_formatting_overspec_percent")


def test_two_decimal_percent_not_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.RETURNS_SUMMARY,
            "Year-1 levered IRR projects to 23.48% on a 5-year hold.",
        )
    )
    result = evaluate_memo(memo)
    assert not any(
        f.rule_id == "number_formatting_overspec_percent" for f in result.findings
    )


# ─────────────────────── hotel-specific ───────────────────────


def test_occupancy_as_fraction_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.MARKET_ANALYSIS,
            "T-12 occupancy of 0.762 trails the comp set average by 240 bps.",
        )
    )
    result = evaluate_memo(memo)
    finding = next(
        (f for f in result.findings if f.rule_id == "hotel_occupancy_as_fraction"),
        None,
    )
    assert finding is not None
    assert "76.2%" in (finding.suggestion or "")


def test_occupancy_as_percent_not_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.MARKET_ANALYSIS,
            "T-12 occupancy of 76.2% trails the comp set average by 240 bps.",
        )
    )
    result = evaluate_memo(memo)
    assert not any(
        f.rule_id == "hotel_occupancy_as_fraction" for f in result.findings
    )


def test_adr_without_dollar_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.MARKET_ANALYSIS,
            "ADR was 285 last year, holding into 2025 per STR data.",
        )
    )
    result = evaluate_memo(memo)
    # Note: this section doesn't mention occupancy, so revpar-pair rule
    # won't trigger; we only care about the dollar rule here.
    assert _has_finding(result.findings, "hotel_adr_missing_dollar")


def test_adr_with_dollar_not_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.MARKET_ANALYSIS,
            "ADR was $285 last year, holding into 2025 per STR data.",
        )
    )
    result = evaluate_memo(memo)
    assert not any(
        f.rule_id == "hotel_adr_missing_dollar" for f in result.findings
    )


def test_adr_plus_occupancy_without_revpar_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.MARKET_ANALYSIS,
            "ADR of $285 paired with 76.2% occupancy underwrites the 5-year proforma.",
        )
    )
    result = evaluate_memo(memo)
    assert _has_finding(result.findings, "hotel_revpar_paired")


def test_adr_plus_occupancy_with_revpar_not_flagged() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.MARKET_ANALYSIS,
            "ADR $285 × 76.2% Occupancy = RevPAR $217.17 per the 2024 STR run.",
        )
    )
    result = evaluate_memo(memo)
    assert not any(
        f.rule_id == "hotel_revpar_paired" for f in result.findings
    )


# ─────────────────────── aggregate ───────────────────────


def test_clean_memo_passes() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.INVESTMENT_THESIS,
            "Recommend Approve. Strong 24.5% IRR on a $36.4M basis with "
            "6.80% cap. ADR $285, occupancy 76.2%, RevPAR $217.17.",
        )
    )
    result = evaluate_memo(memo)
    assert result.passed
    assert not result.needs_regeneration


def test_dirty_memo_needs_regeneration() -> None:
    memo = _make_memo(
        _section(
            MemoSectionId.FINANCIAL_ANALYSIS,
            "It is worth noting that the $36,400,000 basis reflects strong "
            "demand. Going forward we expect growth.",
        )
    )
    result = evaluate_memo(memo)
    assert not result.passed
    assert result.needs_regeneration
