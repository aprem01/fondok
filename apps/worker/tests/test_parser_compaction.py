"""Unit tests for the parser-text compaction helper.

The compaction module lives in ``app.extraction.compaction`` and strips
formatting noise (whitespace runs, decorative separator lines, duplicate
page headers) from parsed document text before it hits the Anthropic
extractor prompt. This test module guards the safety gates — the
extractor's whole job is to find NUMBERS, so any rule that could
alter/drop a numeric line would be a P1 regression.
"""

from __future__ import annotations

import os

import pytest

from app.extraction.compaction import (
    _compact_parsed_text,
    compact_for_prompt,
)


# ─────────────────────────── whitespace collapse ───────────────────────────


def test_whitespace_run_collapses_on_label_line() -> None:
    """Runs of 3+ whitespace chars on a non-data line collapse to one space."""
    text = "Section Header\t\t\t\t\tSubheader"
    out = _compact_parsed_text(text)
    assert out == "Section Header Subheader"


def test_tabs_between_empty_cells_collapse() -> None:
    """A row of empty cells rendered as tab-tab-tab collapses to blank."""
    text = "\t\t\t\t\t"
    out = _compact_parsed_text(text)
    # A pure-whitespace line trims to empty and blank-run collapse
    # trims trailing blanks — result is empty.
    assert out == ""


def test_data_row_preserves_visual_alignment() -> None:
    """A data-bearing line keeps its inner spacing so the LLM can parse it.

    We do collapse 6+-char runs on data rows down to 2 spaces (that's
    where the token savings come from on Excel dumps) but shorter runs
    stay intact.
    """
    text = "Rooms Revenue          $1,234,567"
    out = _compact_parsed_text(text)
    # Long run collapses to a 2-space gap; digit content preserved.
    assert "$1,234,567" in out
    assert "Rooms Revenue" in out
    # No 10-space runs left.
    assert "          " not in out


# ─────────────────────────── decorative separator drop ───────────────────────────


def test_dashes_only_line_drops() -> None:
    text = "Header\n----------\nBody"
    out = _compact_parsed_text(text)
    assert "-----" not in out
    assert "Header" in out
    assert "Body" in out


def test_equals_only_line_drops() -> None:
    text = "=====\nSection\n====="
    out = _compact_parsed_text(text)
    assert "=====" not in out
    assert out.strip() == "Section"


def test_asterisk_only_line_drops() -> None:
    text = "***\nNotes\n***"
    out = _compact_parsed_text(text)
    assert "***" not in out
    assert "Notes" in out


def test_decorative_line_with_digit_is_kept() -> None:
    """A page footer like ``---- 12 ----`` still holds the page number."""
    text = "Body\n---- 12 ----\nMore"
    out = _compact_parsed_text(text)
    assert "12" in out
    assert "Body" in out
    assert "More" in out


# ─────────────────────────── currency / digit safety ───────────────────────────


def test_currency_line_preserved_verbatim() -> None:
    """A line with $/digits must survive compaction with numbers intact."""
    text = "NOI:     $1,234,567.89"
    out = _compact_parsed_text(text)
    assert "$1,234,567.89" in out


def test_percent_line_preserved() -> None:
    text = "Occupancy: 74.3%"
    out = _compact_parsed_text(text)
    assert "74.3%" in out


def test_euro_pound_yen_preserved() -> None:
    text = "Revenue: €1,000 £500 ¥120000"
    out = _compact_parsed_text(text)
    assert "€1,000" in out
    assert "£500" in out
    assert "¥120000" in out


def test_indented_table_hierarchy_preserved() -> None:
    """Leading whitespace on data-bearing lines carries USALI hierarchy."""
    text = (
        "Rooms Department\n"
        "  Rooms Revenue        $6,500,000\n"
        "    Occupied Rooms     45,000\n"
        "  Rooms Expense        $1,500,000\n"
    )
    out = _compact_parsed_text(text)
    # Both indent levels survive.
    assert "  Rooms Revenue" in out
    assert "    Occupied Rooms" in out
    assert "$6,500,000" in out
    assert "45,000" in out


# ─────────────────────────── page header dedup ───────────────────────────


def test_consecutive_page_headers_dedup() -> None:
    text = "[Page 3]\n[Page 3]\nBody line"
    out = _compact_parsed_text(text)
    # Only one [Page 3] header survives.
    assert out.count("[Page 3]") == 1
    assert "Body line" in out


def test_non_consecutive_page_headers_kept() -> None:
    """Different page numbers stay — they're legitimate anchors."""
    text = "[Page 3]\nBody\n[Page 4]\nMore"
    out = _compact_parsed_text(text)
    assert "[Page 3]" in out
    assert "[Page 4]" in out


# ─────────────────────────── vertical whitespace ───────────────────────────


def test_multiple_blank_lines_collapse() -> None:
    text = "Line A\n\n\n\n\nLine B"
    out = _compact_parsed_text(text)
    # 5 blank lines collapse to at most 1.
    assert out.count("\n\n\n") == 0
    assert "Line A" in out
    assert "Line B" in out


def test_empty_input_handled() -> None:
    assert _compact_parsed_text("") == ""
    assert compact_for_prompt("") == ("", {"chars_before": 0, "chars_after": 0, "chars_saved": 0})


def test_whitespace_only_input_handled() -> None:
    out = _compact_parsed_text("   \n\t\t\n   \n")
    assert out == ""


# ─────────────────────────── idempotency ───────────────────────────


def test_compaction_is_idempotent() -> None:
    """Running twice must return the same result as running once."""
    text = (
        "===== Section =====\n"
        "Rooms Revenue\t\t\t\t\t$6,500,000\n"
        "\n\n\n\n"
        "[Page 12]\n"
        "[Page 12]\n"
        "----\n"
        "Occupancy: 74%\n"
    )
    once = _compact_parsed_text(text)
    twice = _compact_parsed_text(once)
    assert once == twice


# ─────────────────────────── env flag ───────────────────────────


def test_compaction_disabled_returns_input_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSER_COMPACTION_ENABLED", "false")
    noisy = "Header\n----------\n\t\t\tRooms   $1,000\n"
    out, stats = compact_for_prompt(noisy)
    assert out == noisy
    assert stats["chars_saved"] == 0


def test_compaction_enabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSER_COMPACTION_ENABLED", raising=False)
    noisy = "Header\n----------\nBody"
    out, stats = compact_for_prompt(noisy)
    assert "----" not in out
    assert stats["chars_saved"] > 0


# ─────────────────────────── stats surface ───────────────────────────


def test_stats_report_before_after_chars() -> None:
    """Sam's dashboard reads chars_before / chars_after to track savings."""
    text = "Header\n" + "-" * 40 + "\nBody"
    _, stats = compact_for_prompt(text)
    assert stats["chars_before"] == len(text)
    assert stats["chars_after"] < stats["chars_before"]
    assert stats["chars_saved"] == stats["chars_before"] - stats["chars_after"]


# ─────────────────────────── realistic doc snippet ───────────────────────────


def test_realistic_str_snippet_saves_15_pct_or_more() -> None:
    """A snippet that mirrors an STR dump should drop 15%+ of chars.

    The task memo targets a 15–25% reduction on real docs. This isn't a
    formal golden-set assertion — it's a smoke check so a future refactor
    that inadvertently disables a rule (e.g. drops the whitespace-run
    collapse) fails loudly in CI instead of quietly costing tokens in
    production.
    """
    # Simulate an Excel sheet dump: many empty cells (tab-tab-tab),
    # section separators, duplicate page headers.
    snippet = (
        "[Page 1]\n"
        "[Page 1]\n"
        "==============================================================\n"
        "SMITH TRAVEL RESEARCH TREND REPORT\n"
        "==============================================================\n"
        "\n\n\n\n"
        "Property\t\t\t\t\t\t\t\tThe Fondok Inn\n"
        "Market\t\t\t\t\t\t\t\t\tAustin, TX\n"
        "\n"
        "----\n"
        "Month\t\tOccupancy\t\tADR\t\t\tRevPAR\n"
        "Jan 2024\t\t72.3%\t\t\t$180.15\t\t\t$130.25\n"
        "Feb 2024\t\t74.1%\t\t\t$183.90\t\t\t$136.27\n"
        "----\n"
        "\n\n\n"
    )
    out, stats = compact_for_prompt(snippet)
    reduction_pct = stats["chars_saved"] / stats["chars_before"] * 100.0
    assert reduction_pct >= 15.0, (
        f"expected >=15% reduction, got {reduction_pct:.1f}% "
        f"(before={stats['chars_before']}, after={stats['chars_after']})"
    )
    # And the important content survived.
    assert "72.3%" in out
    assert "$180.15" in out
    assert "$130.25" in out
    assert "The Fondok Inn" in out
    assert "Austin, TX" in out
    # Only one [Page 1] header.
    assert out.count("[Page 1]") == 1
    # Decorative rules gone.
    assert "====" not in out
    assert "----" not in out
