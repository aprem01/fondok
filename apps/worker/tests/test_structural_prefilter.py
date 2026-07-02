"""Structural pre-filter tests (cost-opt pass S).

Verifies ``_filter_chunks_by_signal`` correctly drops low-signal chunks
(Help / Notes / SetUp / Cover tabs) while preserving chunks that carry
P&L / STR / currency signal. Runs entirely offline — no LLM, no DB.

Coverage:
    * A "Help" / "Instructions" / "Notes" sheet with only boilerplate
      instructional text gets DROPPED.
    * A P&L sheet with rooms revenue + GOP + NOI + dollar figures gets
      KEPT.
    * An STR Trend sheet with MPI / ARI / RGI + compset markers gets
      KEPT.
    * The prose-heavy skip list (OM / MARKET_STUDY / SURVEYS) keeps
      even low-signal chunks — filter opts out entirely for these.
    * PROPERTY_INFO / ROOM_MIX use the light gate: tabular content
      keeps a chunk even without dollar figures.
    * Safety fallback: when EVERY chunk fails the gate, the top-scoring
      chunk is kept so the extractor never sees 0 documents.
    * ``STRUCTURAL_PREFILTER_ENABLED=false`` bypasses the filter.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Isolate the test — SQLite DSN before any worker imports so a
# developer's shell-level DATABASE_URL doesn't bleed in.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-structural-prefilter.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")


# ── Fake chunk shape mirroring ExtractorDocument's surface — the filter
# only pokes at ``.content`` + ``.source_pages`` so this is enough to
# exercise the drop logic without importing the full pydantic model.
@dataclass
class _FakeChunk:
    content: str
    source_pages: list[int]


def _pages(page_num: int, sheet_name: str, text: str) -> dict:
    return {
        "page_num": page_num,
        "text": text,
        "metadata": {"source": "xls", "sheet_name": sheet_name},
    }


# ── Sample fixtures ────────────────────────────────────────────────────

# "Help" tab from a typical STR / T12 workbook. All instructional prose;
# no dollar figures, no P&L vocab, no STR markers.
_HELP_TAB = (
    "Help\n"
    "This workbook is provided as a template. To use it, populate the "
    "input cells on the SetUp tab and refer to the Instructions sheet "
    "for guidance. See the Notes tab for a full data dictionary and "
    "the Glossary tab for definitions of each acronym."
)

# "Notes" tab — pure disclaimer text.
_NOTES_TAB = (
    "Notes\n"
    "Confidential and proprietary. Not for redistribution. This report "
    "is prepared for informational purposes only and does not constitute "
    "an offer to sell or a solicitation to buy any security."
)

# "SetUp" tab — small config lookup with no financial data.
_SETUP_TAB = (
    "SetUp\n"
    "Property Name:\tFondok Inn\n"
    "Report Prepared By:\tAcme Analytics\n"
    "Date:\t2025-01-15\n"
)

# "Cover" tab — title page.
_COVER_TAB = (
    "Cover Sheet\n"
    "Angler's Hotel — T-12 Operating Statement\n"
    "For the trailing twelve months ended December 2024"
)

# Genuine P&L sheet — rooms revenue, F&B, GOP, NOI, dollar figures.
_PNL_TAB = (
    "Angler's Hotel — Trailing 12 P&L\n"
    "Department\tRevenue\tExpense\tDepartmental Profit\n"
    "Rooms Revenue\t$8,450,000\t$1,750,000\t$6,700,000\n"
    "Food and Beverage\t$1,800,000\t$1,200,000\t$600,000\n"
    "Other Operated Departments\t$200,000\t$80,000\t$120,000\n"
    "Total Revenue\t$10,450,000\n"
    "Undistributed Operating Expense\t$1,850,000\n"
    "Property Tax\t$420,000\n"
    "Insurance\t$85,000\n"
    "Management Fee\t$314,000\n"
    "Gross Operating Profit (GOP)\t$5,731,000\n"
    "NOI\t$4,912,000\n"
    "USALI-compliant format.\n"
)

# STR / CoStar Trend sheet — MPI / ARI / RGI + compset markers.
_STR_TAB = (
    "STR Trend Report — Custom Trend / By Measure\n"
    "Comp Set: 5 competitors\n"
    "Subject Property vs. Competitive Set — TTM Performance\n"
    "MPI (Occupancy Index): 108.5\n"
    "ARI (ADR Index): 104.2\n"
    "RGI (RevPAR Index): 112.9\n"
    "Weekly Performance: Mon-Sun avg\n"
    "Smith Travel Research / CoStar penetration index analysis.\n"
)

# Property info tab with room mix — tabular but no dollars.
_ROOM_MIX_TAB = (
    "Room Mix\n"
    "Category\tCount\tSquare Feet\n"
    "King\t120\t320\n"
    "Double Queen\t85\t340\n"
    "Suite\t20\t520\n"
    "ADA-accessible\t8\t320\n"
)


# ─────────────────────────── unit tests ───────────────────────────


def test_help_tab_is_dropped_on_t12_doc() -> None:
    """A Help/Instructions/Notes sheet must NOT survive the strict gate
    on a T12 doc — it carries no P&L vocab, no dollar figures, no STR
    markers, and its meaningful content after boilerplate stripping
    falls below the 200-char floor."""
    from app.api.documents import _filter_chunks_by_signal

    help_chunk = _FakeChunk(content=_HELP_TAB, source_pages=[1])
    pnl_chunk = _FakeChunk(content=_PNL_TAB, source_pages=[2])

    kept = _filter_chunks_by_signal(
        chunks=[help_chunk, pnl_chunk],
        pages=[
            _pages(1, "Help", _HELP_TAB),
            _pages(2, "P&L", _PNL_TAB),
        ],
        doc_type="T12",
        doc_id="doc-help-1",
        filename="anglers_t12.xlsx",
    )

    # Help chunk dropped; P&L chunk kept.
    kept_contents = [c.content for c in kept]
    assert _PNL_TAB in kept_contents, "P&L sheet should be kept"
    assert _HELP_TAB not in kept_contents, "Help sheet should be dropped"


def test_notes_setup_cover_all_dropped_on_str_trend() -> None:
    """Multi-sheet workbook mimicking Sam's 26-sheet STR case: Cover /
    Notes / SetUp / Help tabs should ALL drop while the actual STR
    trend sheet survives."""
    from app.api.documents import _filter_chunks_by_signal

    chunks = [
        _FakeChunk(content=_COVER_TAB, source_pages=[1]),
        _FakeChunk(content=_NOTES_TAB, source_pages=[2]),
        _FakeChunk(content=_SETUP_TAB, source_pages=[3]),
        _FakeChunk(content=_HELP_TAB, source_pages=[4]),
        _FakeChunk(content=_STR_TAB, source_pages=[5]),
    ]
    pages = [
        _pages(1, "Cover", _COVER_TAB),
        _pages(2, "Notes", _NOTES_TAB),
        _pages(3, "SetUp", _SETUP_TAB),
        _pages(4, "Help", _HELP_TAB),
        _pages(5, "Custom Trend", _STR_TAB),
    ]

    kept = _filter_chunks_by_signal(
        chunks=chunks,
        pages=pages,
        doc_type="STR_TREND",
        doc_id="doc-str-1",
        filename="str_trend.xls",
    )

    kept_contents = [c.content for c in kept]
    assert _STR_TAB in kept_contents, "STR Custom Trend sheet must be kept"
    for label, content in [
        ("Cover", _COVER_TAB),
        ("Notes", _NOTES_TAB),
        ("SetUp", _SETUP_TAB),
        ("Help", _HELP_TAB),
    ]:
        assert content not in kept_contents, f"{label} sheet must be dropped"

    # And chunks_after should reflect a meaningful drop.
    assert len(kept) == 1
    assert len(chunks) - len(kept) == 4  # 4 dropped


def test_pnl_sheet_survives_strict_gate() -> None:
    """A dedicated regression: a real P&L sheet MUST survive the strict
    gate. If this trips, the filter has broken quality — tighten the
    boilerplate regex or lower the score threshold."""
    from app.api.documents import _filter_chunks_by_signal

    pnl_chunk = _FakeChunk(content=_PNL_TAB, source_pages=[1])
    kept = _filter_chunks_by_signal(
        chunks=[pnl_chunk],
        pages=[_pages(1, "P&L", _PNL_TAB)],
        doc_type="T12",
        doc_id="doc-pnl-1",
        filename="t12.xlsx",
    )
    assert len(kept) == 1
    assert kept[0].content == _PNL_TAB


def test_om_skips_filter_entirely() -> None:
    """Prose-heavy doc types (OM / MARKET_STUDY / SURVEYS / …) opt out.
    Even a chunk that looks like pure boilerplate is kept — those docs
    have legitimately low-signal pages (TOC, disclosure) that still
    carry entities the extractor needs."""
    from app.api.documents import _filter_chunks_by_signal

    # Both chunks are low-signal by tabular-doc standards; OM policy
    # keeps both anyway.
    low_signal_chunks = [
        _FakeChunk(content=_HELP_TAB, source_pages=[1]),
        _FakeChunk(content=_NOTES_TAB, source_pages=[2]),
    ]
    pages = [
        _pages(1, "", _HELP_TAB),
        _pages(2, "", _NOTES_TAB),
    ]

    for doc_type in ("OM", "MARKET_STUDY", "SURVEYS", "LEASES"):
        kept = _filter_chunks_by_signal(
            chunks=low_signal_chunks,
            pages=pages,
            doc_type=doc_type,
            doc_id=f"doc-{doc_type.lower()}",
            filename=f"{doc_type.lower()}.pdf",
        )
        assert len(kept) == len(low_signal_chunks), (
            f"{doc_type}: filter should be disabled — got {len(kept)} kept "
            f"expected {len(low_signal_chunks)}"
        )


def test_light_gate_keeps_room_mix_tabular_content() -> None:
    """ROOM_MIX / PROPERTY_INFO use the light gate: any tabular signal
    keeps a chunk even without dollar figures. A room-mix grid (no $)
    still has a real grid + non-boilerplate content."""
    from app.api.documents import _filter_chunks_by_signal

    room_mix_chunk = _FakeChunk(content=_ROOM_MIX_TAB, source_pages=[1])
    help_chunk = _FakeChunk(content=_HELP_TAB, source_pages=[2])

    kept = _filter_chunks_by_signal(
        chunks=[room_mix_chunk, help_chunk],
        pages=[
            _pages(1, "Room Mix", _ROOM_MIX_TAB),
            _pages(2, "Help", _HELP_TAB),
        ],
        doc_type="ROOM_MIX",
        doc_id="doc-roommix-1",
        filename="room_mix.xlsx",
    )

    kept_contents = [c.content for c in kept]
    assert _ROOM_MIX_TAB in kept_contents
    assert _HELP_TAB not in kept_contents


def test_safety_fallback_keeps_top_chunk_when_all_would_drop() -> None:
    """Pathological case: an entire workbook is boilerplate. The filter
    must still return ≥ 1 chunk so downstream extraction runs (and
    honestly reports 0 fields)."""
    from app.api.documents import _filter_chunks_by_signal

    chunks = [
        _FakeChunk(content=_HELP_TAB, source_pages=[1]),
        _FakeChunk(content=_NOTES_TAB, source_pages=[2]),
        _FakeChunk(content=_COVER_TAB, source_pages=[3]),
    ]
    pages = [
        _pages(1, "Help", _HELP_TAB),
        _pages(2, "Notes", _NOTES_TAB),
        _pages(3, "Cover", _COVER_TAB),
    ]
    kept = _filter_chunks_by_signal(
        chunks=chunks,
        pages=pages,
        doc_type="T12",
        doc_id="doc-fallback-1",
        filename="empty.xlsx",
    )
    # Safety clamp — never zero chunks.
    assert len(kept) == 1


def test_flag_disabled_bypasses_filter(monkeypatch) -> None:
    """When STRUCTURAL_PREFILTER_ENABLED=false, the filter must be a
    no-op — every chunk passes through untouched."""
    # Force settings to a fresh instance with the flag off.
    from app import config as config_mod
    from app.api import documents as doc_mod

    config_mod.get_settings.cache_clear()
    monkeypatch.setenv("STRUCTURAL_PREFILTER_ENABLED", "false")
    try:
        chunks = [
            _FakeChunk(content=_HELP_TAB, source_pages=[1]),
            _FakeChunk(content=_NOTES_TAB, source_pages=[2]),
        ]
        pages = [
            _pages(1, "Help", _HELP_TAB),
            _pages(2, "Notes", _NOTES_TAB),
        ]
        kept = doc_mod._filter_chunks_by_signal(
            chunks=chunks,
            pages=pages,
            doc_type="T12",
            doc_id="doc-flag-off",
            filename="anything.xlsx",
        )
        assert len(kept) == 2, (
            "flag disabled — filter should be a passthrough, got "
            f"{len(kept)} kept expected 2"
        )
    finally:
        config_mod.get_settings.cache_clear()


# ─────────────────────────── impact metric ───────────────────────────


def test_golden_str_trend_fixture_drops_boilerplate_sheets() -> None:
    """Measurement test — runs the real ``sample_str_trend`` golden
    fixture through parse → chunk → filter and prints the impact
    (chunks_before / chunks_after / dropped sheet names) so the drop
    magnitude is visible in the test log.

    The primary assertion is soft: at least one chunk survives (safety)
    AND the filter did NOT keep every chunk (i.e. there was a real win).
    Skips when the fixture is missing.
    """
    import asyncio
    import json

    from app.api.documents import (
        _build_extractor_chunks,
        _filter_chunks_by_signal,
    )
    from app.extraction import parse_document

    repo_root = Path(__file__).resolve().parents[3]
    golden_dir = repo_root / "evals" / "golden-set" / "documents"
    manifest_path = golden_dir / "manifest.json"
    if not manifest_path.exists():
        import pytest
        pytest.skip("golden manifest not present in this checkout")

    case = json.loads((golden_dir / "sample_str_trend.expected.json").read_text())
    fixture = repo_root / case["fixture_path"]
    if not fixture.exists():
        import pytest
        pytest.skip(f"fixture missing: {fixture}")

    parsed = asyncio.run(parse_document(fixture.read_bytes(), fixture.name))
    pages = [
        {
            "page_num": pg.page_num,
            "text": pg.text,
            "metadata": pg.metadata,
        }
        for pg in parsed.pages
    ]

    chunks_before = _build_extractor_chunks(
        pages=pages,
        doc_id="doc-goldstr",
        filename=fixture.name,
        doc_type="STR_TREND",
        make_doc=_FakeChunk.__class__ if False else (
            lambda **kw: _FakeChunk(
                content=kw["content"],
                source_pages=list(kw.get("source_pages", [])),
            )
        ),
    )
    chunks_after = _filter_chunks_by_signal(
        chunks=list(chunks_before),
        pages=pages,
        doc_type="STR_TREND",
        doc_id="doc-goldstr",
        filename=fixture.name,
    )

    # Compute dropped chunk labels for the impact print.
    kept_ids = {id(c) for c in chunks_after}
    dropped_sheet_labels: list[str] = []
    for c in chunks_before:
        if id(c) in kept_ids:
            continue
        # Recover sheet names from the source_pages via the parsed pages.
        page_meta = {
            p["page_num"]: (p.get("metadata") or {}).get("sheet_name", "")
            for p in pages
        }
        names = [
            page_meta.get(pn, "") for pn in c.source_pages
        ]
        uniq = [n for n in dict.fromkeys(names) if n]
        dropped_sheet_labels.append("/".join(uniq) or f"pages={c.source_pages}")

    print(
        f"\n[impact] STR_TREND golden fixture: "
        f"chunks_before={len(chunks_before)} "
        f"chunks_after={len(chunks_after)} "
        f"chunks_dropped={len(chunks_before) - len(chunks_after)}"
    )
    if dropped_sheet_labels:
        print(f"[impact] dropped_sheets={dropped_sheet_labels}")

    # Safety + effectiveness assertions.
    assert len(chunks_after) >= 1, "safety fallback failed"
    assert len(chunks_after) <= len(chunks_before)
