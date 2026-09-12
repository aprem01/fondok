"""Smoke tests for the real export builders.

Builds each artifact from the Kimpton Angler fixture and asserts the
resulting file exists with the expected structure (10 sheets / 8 slides /
non-empty PDF).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")


@pytest.fixture
def tmp_out() -> Path:
    d = Path(tempfile.mkdtemp(prefix="fondok-exports-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_excel_builds(tmp_out: Path) -> None:
    """Excel builder produces the enriched W4.2 workbook.

    Wave 4 W4.2 swapped the legacy 10-sheet workbook for a conditional
    layout: 9 always-on sheets + 10 Wave 2/3 sheets that render only
    when their source data is present. The Kimpton fixture carries
    every Wave 2/3 artifact so the full 19-sheet workbook ships.
    Barebones-deal backward compat is covered in
    test_excel_wave2_3_sections.py.
    """
    from openpyxl import load_workbook

    from app.export import build_excel
    from app.export.fixtures import kimpton_model

    out = tmp_out / "model.xlsx"
    build_excel("kimpton-angler-2026", kimpton_model(), out)

    assert out.exists(), "xlsx not written"
    assert out.stat().st_size > 8_000, "xlsx is suspiciously small"

    wb = load_workbook(out, read_only=True)
    always_on = {
        "Cover", "Assumptions", "Sources & Uses", "Operating Proforma",
        "Debt Schedule", "Returns", "Partnership", "Variance", "Market Comps",
    }
    assert always_on.issubset(set(wb.sheetnames)), (
        f"missing always-on sheets: {always_on - set(wb.sheetnames)}"
    )
    wave_sheets = {
        "Revenue Mix", "Renovation Plan", "Capital Plan",
        "Op-Ratio Provenance", "Pricing Sensitivity", "Comparable Sales",
        "Historical Baseline", "STR Forecast", "Named Scenarios",
        "LOI Appendix",
    }
    assert wave_sheets.issubset(set(wb.sheetnames)), (
        f"missing Wave 2/3 sheets: {wave_sheets - set(wb.sheetnames)}"
    )
    assert len(wb.sheetnames) == 19, (
        f"expected 19 sheets, got {len(wb.sheetnames)}: {wb.sheetnames}"
    )
    wb.close()


def test_memo_pdf_builds(tmp_out: Path) -> None:
    """WeasyPrint memo PDF is non-empty and has the PDF magic bytes."""
    pytest.importorskip(
        "weasyprint",
        reason="weasyprint requires system libs (cairo/pango)",
    )

    from app.export import build_memo_pdf
    from app.export.fixtures import kimpton_memo, kimpton_model

    out = tmp_out / "memo.pdf"
    build_memo_pdf(kimpton_memo(), kimpton_model(), out)

    assert out.exists(), "memo pdf not written"
    size = out.stat().st_size
    assert size > 4_000, f"pdf is suspiciously small ({size} bytes)"

    with out.open("rb") as fh:
        magic = fh.read(5)
    assert magic == b"%PDF-", f"file does not start with PDF magic, got {magic!r}"


def test_pptx_builds(tmp_out: Path) -> None:
    """Presentation has exactly 8 slides and is a valid pptx zip."""
    from pptx import Presentation

    from app.export import build_pptx
    from app.export.fixtures import kimpton_deal, kimpton_memo, kimpton_model

    out = tmp_out / "deck.pptx"
    build_pptx(kimpton_deal(), kimpton_model(), kimpton_memo(), out)

    assert out.exists(), "pptx not written"
    assert out.stat().st_size > 12_000, "pptx is suspiciously small"
    assert zipfile.is_zipfile(out), "pptx is not a valid zip archive"

    prs = Presentation(str(out))
    assert len(prs.slides) == 8, f"expected 8 slides, got {len(prs.slides)}"


# ───────────── NOI basis in the exported proforma (FON-54 #8) ──────────────


def _proforma_lines(*, institutional: bool = True) -> list[dict]:
    """Run the live-payload proforma builder over a two-year toy deal."""
    from app.export.live_payload import _build_proforma_and_cf

    def rev_year(n: int, total: float) -> dict:
        return {
            "year": n,
            "rooms_revenue": total * 0.75,
            "fb_revenue": total * 0.20,
            "other_revenue": total * 0.05,
            "resort_fees": 0.0,
            "total_revenue": total,
        }

    def exp_year(n: int, total: float) -> dict:
        # GOP - mgmt fee - fixed = NOI before reserve; less the reserve = Cash NOI.
        dept, undist, fixed = total * 0.30, total * 0.20, total * 0.08
        mgmt, ffe = total * 0.03, total * 0.04
        gop = total - dept - undist
        noi_inst = gop - mgmt - fixed
        return {
            "year": n,
            "total_revenue": total,
            "dept_expenses": {"total": dept},
            "undistributed": {"total": undist},
            "fixed_charges": {"total": fixed},
            "mgmt_fee": mgmt,
            "ffe_reserve": ffe,
            "gop": gop,
            "noi": noi_inst - ffe,
            # A pre-upgrade engine_outputs row persists ``None`` here.
            "noi_institutional": noi_inst if institutional else None,
        }

    revenue = {"years": [rev_year(1, 10_000_000.0), rev_year(2, 10_500_000.0)]}
    expense = {"years": [exp_year(1, 10_000_000.0), exp_year(2, 10_500_000.0)]}
    proforma, _cf, _noi_y1 = _build_proforma_and_cf(
        revenue, expense, {"annual_debt_service": 1_000_000.0}
    )
    return proforma["lines"]


def test_proforma_carries_both_noi_rows_and_foots() -> None:
    """The exported workbook must show BOTH NOI bases, in waterfall order.

    Sam's FON-54 #8 reconciliation failed because the workbook headline used
    the before-reserve figure while the proforma row used the after-reserve
    one, both labelled "NOI". Now the proforma emits "NOI (before FF&E
    reserve)" above the reserve line and "Cash NOI (after FF&E reserve)"
    below it, so the statement foots on the page.
    """
    from app.export.labels import CASH_NOI, NOI_BEFORE_RESERVE

    lines = _proforma_lines()
    labels = [row["label"] for row in lines]
    assert NOI_BEFORE_RESERVE in labels
    assert CASH_NOI in labels
    # Order: ... Management Fee, NOI (before), FF&E Reserve, Cash NOI, ...
    assert labels.index("Management Fee") < labels.index(NOI_BEFORE_RESERVE)
    assert labels.index(NOI_BEFORE_RESERVE) < labels.index("FF&E Reserve")
    assert labels.index("FF&E Reserve") < labels.index(CASH_NOI)
    # No row is labelled a bare, ambiguous "NOI" / "Net Operating Income".
    assert "NOI" not in labels
    assert "Net Operating Income" not in labels

    by_label = {row["label"]: row for row in lines}
    for y in ("y1", "y2"):
        before = by_label[NOI_BEFORE_RESERVE][y]
        ffe = by_label["FF&E Reserve"][y]
        cash = by_label[CASH_NOI][y]
        # Values are rounded USD thousands, so allow a $1k rounding step.
        assert abs((before - ffe) - cash) <= 1
        assert before > cash
        # ...and the before-reserve row foots to the revenue/expense rows above.
        implied = (
            by_label["Total Revenue"][y]
            - by_label["Operating Expenses"][y]
            - by_label["Management Fee"][y]
        )
        assert abs(implied - before) <= 1


def test_proforma_legacy_run_does_not_claim_a_basis_it_cannot_prove() -> None:
    """A pre-upgrade run persisted ``noi_institutional: null``. The value
    still falls back to ``noi``, but the row must NOT then assert "before
    FF&E reserve" about an after-reserve number."""
    from app.export.labels import (
        CASH_NOI,
        NOI_BASIS_UNCONFIRMED,
        NOI_BEFORE_RESERVE,
        NOI_HEADLINE_LABELS,
    )

    labels = [row["label"] for row in _proforma_lines(institutional=False)]
    assert NOI_BASIS_UNCONFIRMED in labels
    assert NOI_BEFORE_RESERVE not in labels
    assert CASH_NOI in labels
    # The cover / memo headline lookup still finds the row.
    assert any(lbl in NOI_HEADLINE_LABELS for lbl in labels)


# ── FON-63 / FON-54 §8 — the Excel "Loan Costs" cell IS the Debt tab fee ──
# Sam, FON-54 §8: "Export carries $354,900 Loan Costs, reinforcing the separate
# Debt QA issue where the live Debt tab shows Origination Fee 0% / $0 despite
# the model carrying a fee." One assumption now feeds both, so they must agree
# to the cent — and the S&U label rename must not break the export's lookup.


def test_excel_loan_costs_cell_equals_the_debt_tab_origination_fee() -> None:
    from uuid import uuid4

    from app.engines.capital import CapitalEngine, CapitalEngineInput
    from app.engines.debt import DebtEngine, DebtEngineInputExt
    from app.export.live_payload import _build_investment

    # Sam MVP Test 2: $36.4M at 65% LTV → a $23,660,000 senior at a 1.50% fee.
    capital_out = CapitalEngine().run(
        CapitalEngineInput(
            deal_id=uuid4(), purchase_price=36_400_000, keys=132,
            closing_costs_pct=0.02, renovation_budget=5_280_000,
            working_capital=500_000, ltv=0.65, loan_costs_pct=0.015,
        )
    )
    debt_out = DebtEngine().run(
        DebtEngineInputExt(
            deal_id=uuid4(), loan_amount=capital_out.debt_amount, ltv=0.65,
            interest_rate=0.068, term_years=5, amortization_years=30,
            interest_only_years=0,
            noi_by_year=[3_000_000, 3_100_000, 3_200_000, 3_300_000, 3_400_000],
            senior_origination_fee_pct=1.50,
        )
    )

    inv = _build_investment(
        capital_out.model_dump(mode="json"), 132, 3_000_000.0
    )
    assert inv["loan_costs_usd"] == pytest.approx(354_900)
    assert inv["loan_costs_usd"] == pytest.approx(debt_out.origination_fee_usd)
    # The export reads the renamed Sources & Uses line, not the scalar fallback.
    labels = [u.label for u in capital_out.uses]
    assert "Senior Loan Origination Fee" in labels


def test_excel_loan_costs_still_reads_a_legacy_senior_loan_fee_row() -> None:
    """A run persisted before the rename still exports its Loan Costs cell."""
    from app.export.live_payload import _build_investment

    legacy = {
        "uses": [
            {"label": "Purchase Price", "amount": 36_400_000.0},
            {"label": "Senior Loan Fee", "amount": 354_900.0},
            {"label": "Total Uses", "amount": 43_262_900.0, "is_total": True},
        ],
        "total_capital": 43_262_900.0,
    }
    assert _build_investment(legacy, 132, None)["loan_costs_usd"] == pytest.approx(
        354_900
    )
