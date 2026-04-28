"""Tests for the deterministic citation verifier.

Covers the parsers (currency / percent / multiplier) and the classifier
that maps an ``ExtractionField`` against a page of source text into one
of four ``CitationStatus`` outcomes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.extraction.models import ParsedDocument, ParsedPage
from app.verification import verify_citations
from app.verification.numerics import (
    parse_currency,
    parse_percent,
)
from fondok_schemas import CitationStatus, ExtractionField


# ─────────────────────── parser tests ───────────────────────


class TestParseCurrency:
    def test_compact_million(self) -> None:
        # "$36.4M" → 36_400_000
        assert parse_currency("Purchase price $36.4M was negotiated.") == [
            36_400_000.0
        ]

    def test_full_form_with_commas(self) -> None:
        assert parse_currency("Purchase price $36,400,000.") == [36_400_000.0]

    def test_compact_thousand_per_key(self) -> None:
        # "$303k/key" should yield 303,000.
        out = parse_currency("Going-in basis is $303k/key on 132 keys.")
        # The parser may also capture the trailing 132. Just assert 303k is present.
        assert 303_000.0 in out

    def test_parens_negative(self) -> None:
        # "(1.2M)" → -1_200_000
        out = parse_currency("Distribution adjustment ($1.2M) for refi costs.")
        assert -1_200_000.0 in out

    def test_billion(self) -> None:
        assert parse_currency("Portfolio NAV $2.5B as of Q1") == [2_500_000_000.0]

    def test_explicit_negative(self) -> None:
        assert parse_currency("NOI variance -1,234 vs broker.") == [-1234.0]


class TestParsePercent:
    def test_simple_percent(self) -> None:
        assert parse_percent("Cap rate 6.8%.") == [pytest.approx(0.068)]

    def test_bps(self) -> None:
        assert parse_percent("Spread tightened 680 bps.") == [
            pytest.approx(0.068)
        ]

    def test_multiplier(self) -> None:
        # "1.57x DSCR" — multipliers are NOT divided by 100
        assert parse_percent("DSCR of 1.57x in year 1.") == [
            pytest.approx(1.57)
        ]

    def test_mixed(self) -> None:
        out = parse_percent("Cap 6.80% (680 bps) yielding 1.57x DSCR.")
        # 6.80%, 680bps both → 0.068; 1.57x → 1.57
        assert pytest.approx(0.068) in [round(v, 4) for v in out]
        assert 1.57 in out


# ─────────────────────── classifier tests ───────────────────────


def _page(text: str, page_num: int = 1) -> ParsedDocument:
    return ParsedDocument(
        filename="t.pdf",
        total_pages=1,
        pages=[ParsedPage(page_num=page_num, text=text)],
        content_hash="0" * 64,
        parsed_at=datetime.now(UTC),
        parser="pymupdf",
    )


def _field(name: str, value: object, source_page: int = 1) -> ExtractionField:
    return ExtractionField(
        field_name=name,
        value=value,  # type: ignore[arg-type]
        source_page=source_page,
        confidence=0.9,
    )


def test_exact_match() -> None:
    deal_id = uuid4()
    field = _field("noi_year_1", 1_234_567.0)
    docs = {"d1": _page("Net Operating Income: $1,234,567")}
    report = verify_citations(
        [field], docs, deal_id=deal_id, field_doc_ids={"noi_year_1": "d1"}
    )
    assert len(report.checks) == 1
    check = report.checks[0]
    assert check.status == CitationStatus.MATCH
    assert check.parsed_value == 1_234_567.0
    assert check.found_in_source == 1_234_567.0


def test_within_tolerance_close() -> None:
    deal_id = uuid4()
    # Extracted 36.4M, page says 36.5M — 0.27% delta → CLOSE.
    field = _field("purchase_price", 36_400_000.0)
    docs = {"d1": _page("Asking price was $36.5M, on 132 keys.")}
    report = verify_citations(
        [field], docs, deal_id=deal_id, field_doc_ids={"purchase_price": "d1"}
    )
    assert report.checks[0].status == CitationStatus.CLOSE


def test_outside_tolerance_mismatch() -> None:
    deal_id = uuid4()
    # Extracted 36.4M, page says 50M — way off → MISMATCH.
    field = _field("purchase_price", 36_400_000.0)
    docs = {"d1": _page("Asking price was $50M, on 132 keys.")}
    report = verify_citations(
        [field], docs, deal_id=deal_id, field_doc_ids={"purchase_price": "d1"}
    )
    assert report.checks[0].status == CitationStatus.MISMATCH


def test_unverifiable_no_numbers_on_page() -> None:
    deal_id = uuid4()
    field = _field("noi_year_1", 1_234_567.0)
    docs = {"d1": _page("This page contains only narrative text, no numbers.")}
    report = verify_citations(
        [field], docs, deal_id=deal_id, field_doc_ids={"noi_year_1": "d1"}
    )
    assert report.checks[0].status == CitationStatus.UNVERIFIABLE


def test_pass_rate_excludes_unverifiable() -> None:
    """Unverifiable counts must not drag the denominator.

    UNVERIFIABLE means the cited page contained no parseable numbers —
    we couldn't even attempt the comparison. A field on a numeric page
    that doesn't match is MISMATCH, which DOES count against pass_rate.
    """
    deal_id = uuid4()
    fields = [
        _field("a", 100.0, source_page=1),  # match (page 1)
        _field("b", 50.0, source_page=1),   # match (page 1)
        _field("c", 999.0, source_page=2),  # unverifiable (page 2 has no numbers)
    ]
    docs = {
        "d1": ParsedDocument(
            filename="t.pdf",
            total_pages=2,
            pages=[
                ParsedPage(page_num=1, text="Values: $100 and $50."),
                ParsedPage(page_num=2, text="Narrative paragraph, no figures."),
            ],
            content_hash="0" * 64,
            parsed_at=datetime.now(UTC),
            parser="pymupdf",
        ),
    }
    field_doc_ids = {f.field_name: "d1" for f in fields}
    report = verify_citations(
        fields, docs, deal_id=deal_id, field_doc_ids=field_doc_ids
    )
    # 2 verifiable, both passed → 1.0; the 1 unverifiable is excluded.
    assert report.pass_rate == 1.0
    assert report.unverifiable_count == 1


def test_pass_rate_zero_when_all_unverifiable() -> None:
    deal_id = uuid4()
    field = _field("x", 999.0)
    docs = {"d1": _page("no numbers here")}
    report = verify_citations(
        [field], docs, deal_id=deal_id, field_doc_ids={"x": "d1"}
    )
    assert report.pass_rate == 0.0
    assert report.unverifiable_count == 1


def test_occupancy_decimal_vs_percent() -> None:
    """Hotel-specific: extracted 0.762 vs page text "76.2%" should match."""
    deal_id = uuid4()
    field = _field("occupancy_year_1", 0.762)
    docs = {"d1": _page("Occupancy: 76.2% at Q4")}
    report = verify_citations(
        [field], docs, deal_id=deal_id, field_doc_ids={"occupancy_year_1": "d1"}
    )
    # 0.762 vs 0.762 (parsed from "76.2%") → MATCH
    assert report.checks[0].status == CitationStatus.MATCH


def test_string_value_currency_parsed() -> None:
    deal_id = uuid4()
    # Value provided as a string with $ — coerce path.
    field = _field("noi_year_1", "$1,234,567")
    docs = {"d1": _page("Net Operating Income: $1,234,567")}
    report = verify_citations(
        [field], docs, deal_id=deal_id, field_doc_ids={"noi_year_1": "d1"}
    )
    assert report.checks[0].status == CitationStatus.MATCH


def test_skip_non_numeric_fields() -> None:
    """Non-numeric fields (e.g. brand names) should be skipped silently."""
    deal_id = uuid4()
    fields = [
        _field("brand", "Kimpton"),  # skipped — no numeric value
        _field("noi", 1000.0),
    ]
    docs = {"d1": _page("NOI $1,000.")}
    report = verify_citations(
        [field for field in fields],
        docs,
        deal_id=deal_id,
        field_doc_ids={"noi": "d1"},
    )
    assert len(report.checks) == 1
    assert report.checks[0].field_name == "noi"


def test_excerpt_around_match() -> None:
    deal_id = uuid4()
    field = _field("purchase_price", 36_400_000.0)
    docs = {"d1": _page(
        "The Kimpton Angler is offered at $36,400,000 reflecting "
        "$275,758 per key on 132 keys."
    )}
    report = verify_citations(
        [field], docs, deal_id=deal_id, field_doc_ids={"purchase_price": "d1"}
    )
    excerpt = report.checks[0].excerpt
    assert excerpt is not None
    assert "36,400,000" in excerpt


def test_report_has_generated_at() -> None:
    deal_id = uuid4()
    report = verify_citations(
        [], {}, deal_id=deal_id, field_doc_ids={}
    )
    assert isinstance(report.generated_at, datetime)
    assert report.deal_id == deal_id
