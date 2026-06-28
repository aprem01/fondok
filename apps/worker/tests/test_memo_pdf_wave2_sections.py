"""Wave 3 W3.4 — IC memo PDF refresh tests.

Covers the seven new memo sections added by the Wave 2 refresh:

    1. Revenue Mix
    2. Renovation Plan (PIP displacement)
    3. Capital Plan (three-bucket capex)
    4. Operating Ratios Provenance
    5. Pricing Sensitivity Grid (5x5)
    6. Max-Price Findings
    7. Historical Baseline Walk
    8. LOI Draft Appendix

Plus backward-compat: a barebones deal (no Wave 2 data on the model)
must still render a clean memo without raising or producing malformed
HTML. The end-to-end test builds a real PDF via WeasyPrint and asserts
the file is non-trivial size + parses as a valid PDF.

Most tests render HTML-only (via ``_render_html`` or the per-section
helpers) and assert against the HTML string. The end-to-end test calls
``build_memo_pdf`` which exercises WeasyPrint; that test skips with a
loud reason when weasyprint's system libs are missing.
"""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")


@pytest.fixture
def tmp_out() -> Path:
    d = Path(tempfile.mkdtemp(prefix="fondok-memo-wave2-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ─────────────────────── helpers ───────────────────────


def _barebones_model() -> dict:
    """A pre-Wave-2 minimal model — no segments / pip / capex etc.

    The legacy memo (Executive Summary + Returns + Sources & Uses)
    should still render cleanly off this dict. No Wave 2 keys present.
    """
    return {
        "deal_id": "barebones-test",
        "keys": 100,
        "investment_engine": {
            "total_capital_usd": 20_000_000,
            "entry_cap_rate_year1_uw": 0.08,
        },
        "p_and_l_engine_proforma": {
            "lines": [
                {"label": "Room Revenue", "y1": 8000},
                {"label": "Total Revenue", "y1": 10000, "bold": True},
                {"label": "Net Operating Income", "y1": 2000, "bold": True},
            ],
        },
        "debt_engine": {
            "loan_amount_usd": 12_000_000,
            "year1_dscr": 1.45,
            "year1_debt_yield": 0.16,
        },
        "returns_engine": {
            "hold_years": 5,
            "levered_irr": 0.12,
            "equity_multiple": 1.7,
            "year1_cash_on_cash": 0.03,
        },
    }


def _barebones_memo() -> dict:
    return {
        "deal_id": "barebones-test",
        "drafted_at": "2026-06-28T00:00:00Z",
        "header": {
            "title": "IC Memo",
            "subject_property": "Test Hotel",
            "location": "Nowhere, USA",
            "recommendation": "IN REVIEW",
            "deal_stage": "Initial",
        },
        "sections": [
            {"section_id": "executive_summary", "body": "Test summary."},
            {"section_id": "investment_thesis", "body": "Test thesis."},
            {"section_id": "recommendation", "body": "Test rec."},
        ],
        "appendix": {
            "documents_reviewed": [],
            "engines_run": ["investment", "p_and_l"],
            "ai_confidence": 0.5,
        },
    }


# ──────────────────────── tests ────────────────────────


def test_memo_renders_without_wave2_data() -> None:
    """Backward compat — a barebones deal with no Wave 2 data still
    renders a clean HTML memo. No section should leak in when its data
    is absent."""
    from app.export.memo_pdf import _render_html

    html_str = _render_html(_barebones_memo(), _barebones_model())

    # Existing sections still present.
    assert "<h2>Executive Summary</h2>" in html_str
    assert "<h2>Sources &amp; Uses</h2>" in html_str
    assert "<h2>Returns Summary</h2>" in html_str

    # NONE of the Wave 2 section headers should appear when data is absent.
    assert "Revenue Mix" not in html_str
    assert "Renovation Plan" not in html_str
    assert "Capital Plan" not in html_str
    assert "Operating Ratios" not in html_str
    assert "Pricing Sensitivity" not in html_str
    assert "Max-Price Findings" not in html_str
    assert "Historical Baseline Walk" not in html_str
    assert "Letter of Intent" not in html_str


def test_segments_section_renders_when_segments_present() -> None:
    """Year-1 segment mix table renders with one row per segment.

    OTA row (channel_cost_pct=0.18) should flip the warn callout on.
    """
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html

    html_str = _render_html(kimpton_memo(), kimpton_model())

    assert "<h2>Revenue Mix (Y1)</h2>" in html_str
    # Each of the 5 fixture segments shows up.
    for name in (
        "transient_bar", "transient_ota", "corporate", "group", "contract"
    ):
        assert name in html_str
    # OTA-heavy warn callout fired.
    assert "Distribution drag" in html_str


def test_segments_section_absent_when_segments_empty() -> None:
    """Empty segment list -> section is omitted entirely."""
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html

    model = copy.deepcopy(kimpton_model())
    model["segments_by_year"] = []
    # Also defensively clear the revenue_engine fallback path.
    if "revenue_engine" in model:
        model["revenue_engine"].pop("segments_by_year", None)
        model["revenue_engine"].pop("segment_breakdown", None)

    html_str = _render_html(kimpton_memo(), model)
    assert "Revenue Mix" not in html_str


def test_pip_section_renders_when_pip_displacement_set() -> None:
    """Renovation Plan section renders strategy chip + monthly schedule
    + Y1 displacement $ + Y2 recovery curve."""
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html

    html_str = _render_html(kimpton_memo(), kimpton_model())

    assert "<h2>Renovation Plan</h2>" in html_str
    assert "Rolling" in html_str  # strategy chip
    assert "Y1 displacement:" in html_str
    assert "$1,180,000" in html_str  # fixture Y1 displacement
    # Recovery curve section
    assert "Y2 Recovery Curve" in html_str


def test_pip_section_absent_when_pip_displacement_none() -> None:
    """Closure strategy 'none' (or missing pip_displacement entirely)
    -> Renovation Plan section omitted."""
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html

    model = copy.deepcopy(kimpton_model())

    # Case 1: closure_strategy = "none"
    model["pip_displacement"] = {
        "closure_strategy": "none",
        "pct_rooms_offline_by_month": [],
    }
    html1 = _render_html(kimpton_memo(), model)
    assert "Renovation Plan" not in html1

    # Case 2: missing entirely
    model.pop("pip_displacement", None)
    html2 = _render_html(kimpton_memo(), model)
    assert "Renovation Plan" not in html2


def test_capex_three_bucket_table_includes_phasing() -> None:
    """Capital Plan renders the 3 buckets x 5 years with totals row."""
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html

    html_str = _render_html(kimpton_memo(), kimpton_model())

    assert "<h2>Capital Plan (Three-Bucket)</h2>" in html_str
    # Header columns present
    assert "PIP" in html_str
    assert "Non-PIP FF&amp;E" in html_str
    assert "ROI Investment" in html_str
    assert "ROI NOI Lift" in html_str
    # All 5 fixture years rendered
    for y in range(1, 6):
        assert f"Year {y}" in html_str
    # Totals row present
    assert "<tr class='total'><td>Total</td>" in html_str


def test_op_ratio_provenance_callout_shows_winning_source_per_line() -> None:
    """Each op-ratio line shows its winning source chip with the right
    display label."""
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html

    html_str = _render_html(kimpton_memo(), kimpton_model())

    assert "Operating Ratios" in html_str
    assert "Source precedence" in html_str
    # Every fixture source chip appears with the right display label
    # (the fixture has lines tagged each of the 5 sources).
    assert ">T-12<" in html_str
    assert ">Portfolio<" in html_str
    assert ">CBRE<" in html_str
    assert ">HOST<" in html_str
    assert ">Override<" in html_str
    # And the document_id citations leak through.
    assert "T12_FinancialStatement.xlsx" in html_str


def test_pricing_sensitivity_grid_renders_5x5() -> None:
    """Sensitivity grid renders 5 NOI rows x 5 cap columns + a legend
    with the breakeven scalars."""
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html

    html_str = _render_html(kimpton_memo(), kimpton_model())

    assert "<h2>Pricing Sensitivity (5x5 Grid)</h2>" in html_str
    # Count of NOI-axis rows = 5 (each rendered with the axis cell).
    axis_count = html_str.count("class='axis'")
    assert axis_count == 5, f"expected 5 NOI-axis cells, got {axis_count}"
    # Cap-axis header row has 5 cap columns + the corner cell.
    # Each cap renders as a percent header → at least 5 distinct ones.
    for cap in ("6.00%", "6.50%", "7.00%", "7.50%", "8.00%"):
        assert cap in html_str
    # Both breakeven scalars surface in the legend.
    assert "Breakeven exit cap" in html_str
    assert "breakeven NOI multiplier" in html_str
    # And the DSCR-breach row is coloured red (cell-red present).
    assert "cell-red" in html_str


def test_max_price_callout_renders_both_constraints() -> None:
    """Max-price callout shows both IRR and EM price + binding chip."""
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html

    html_str = _render_html(kimpton_memo(), kimpton_model())

    assert "<h2>Max-Price Findings</h2>" in html_str
    assert "Max price for 15.00% IRR" in html_str
    assert "Max price for 1.80x EM" in html_str
    assert "$42,800,000" in html_str   # fixture IRR-binding price
    assert "$44,100,000" in html_str   # fixture EM price
    # Binding chip — fixture says IRR is the constraint.
    assert "<span class='badge warn'>IRR</span>" in html_str


def test_historical_walk_section_renders_when_coverage_high() -> None:
    """Historical Baseline Walk renders the per-year P&L table + YoY
    chips when coverage_pct > 0."""
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html

    html_str = _render_html(kimpton_memo(), kimpton_model())

    assert "<h2>Historical Baseline Walk</h2>" in html_str
    # Coverage chip shows 60% (fixture: 0.6).
    assert "60.00%" in html_str
    # Year columns rendered
    for fy in ("FY2022", "FY2023", "FY2024"):
        assert fy in html_str
    # YoY chips for at least one walk entry.
    assert "Top YoY Swings" in html_str


def test_loi_appendix_renders_markdown_as_html() -> None:
    """LOI rendered_markdown body is converted to HTML and lands in
    the appendix block at the end of the memo."""
    from app.export.fixtures import kimpton_memo, kimpton_model
    from app.export.memo_pdf import _render_html

    html_str = _render_html(kimpton_memo(), kimpton_model())

    # The appendix wrapper is present.
    assert "loi-appendix" in html_str
    assert "Letter of Intent" in html_str
    # Markdown # heading -> <h1>
    assert "<h1>LETTER OF INTENT</h1>" in html_str
    # Markdown ## heading -> <h2>
    assert "<h2>1. Property</h2>" in html_str
    # Markdown **bold** -> <strong>
    assert "<strong>To:</strong>" in html_str
    # Bulleted contingency list -> <ul><li>
    assert "<li>Satisfactory Phase I ESA</li>" in html_str


def test_memo_pdf_builds_end_to_end_with_all_wave2_sections(
    tmp_out: Path,
) -> None:
    """End-to-end smoke — WeasyPrint produces a real PDF that
    includes every Wave 2 section. Asserts > 50 KB (sections add
    real weight vs. the legacy ~14KB memo) and a valid PDF magic
    header.

    Skips with a loud reason if WeasyPrint's system libs (cairo /
    pango) aren't installed in the test environment — the test is
    NOT silently passing; pytest reports the skip and CI catches
    missing libs explicitly.
    """
    pytest.importorskip(
        "weasyprint",
        reason="weasyprint requires system libs (cairo/pango/gdk-pixbuf); "
        "install via `brew install cairo pango gdk-pixbuf libffi` on macOS "
        "or `apt-get install -y libcairo2 libpango-1.0-0` on Debian/Ubuntu.",
    )

    from app.export import build_memo_pdf
    from app.export.fixtures import kimpton_memo, kimpton_model

    out = tmp_out / "kimpton-wave2-memo.pdf"
    build_memo_pdf(kimpton_memo(), kimpton_model(), out)

    assert out.exists(), "memo pdf not written"
    size = out.stat().st_size
    # Wave 2 sections add ~30-60KB of vector rendering; a refreshed
    # memo with every section populated should comfortably clear 50KB.
    assert size > 50_000, (
        f"refreshed memo pdf is suspiciously small ({size} bytes) — "
        "expected the new Wave 2 sections to add meaningful weight"
    )

    with out.open("rb") as fh:
        magic = fh.read(5)
    assert magic == b"%PDF-", (
        f"file does not start with PDF magic, got {magic!r}"
    )
