"""Sam QA 2026-11-27 — the Excel export of deal b67bfdee (Kimpton, $36.4M).

Five findings on the acquisition-model workbook, each pinned here on the
Kimpton export fixture (label, value, formula string, number format) and —
where the fix is in the live payload — on a real capital / debt engine run,
so "the engine emits it" is proved rather than assumed:

    1. Assumptions — Soft Costs / Contingency said "not available" while the
       Investment tab showed Hard $4.725M / Soft $945K / Fees $630K. The sheet
       now reads the capital engine's own ``renovation_breakdown`` and
       contingency fold-in: Hard Costs, Soft Costs, Professional Fees,
       Contingency, Renovation Total.
    2. Assumptions — "LTV (cost)" (a key no engine emits, always "not
       available") sat two rows above "LTC 53.4%". One LTC row per block.
    3. Cover — Year 1 NOI printed $1,646,000 for $1,646,245: it was read back
       off the $000 proforma rows. The live payload carries the whole-dollar
       figure and the cover prefers it.
    4. Cover — "Model Version" read the placeholder "fondok-engine-live". It is
       the worker build SHA (``/health.build_sha``) plus the canonical run, or
       "not available" when no SHA is set.
    5. Cover — the entry cap was a bare number and the entry NOI appeared
       nowhere. Each NOI is its own row and each cap is the Excel FORMULA
       NOI ÷ Purchase Price over the sheet's cells, on the cover and on the
       Assumptions sheet; "not available: no twelve-month statement" when the
       T-12 figure is absent.
"""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

USD_FMT = "$#,##0;[Red]($#,##0)"
PCT_FMT = "0.00%"

ENTRY_NOI = "Entry NOI (Year 1, undisplaced)"
ENTRY_CAP = "Entry Cap (Year 1, undisplaced)"
T12_NOI = "T-12 NOI (before FF&E reserve)"
IN_PLACE_CAP = "In-place Cap (T-12)"
NO_T12 = "not available: no twelve-month statement"


@pytest.fixture
def tmp_out() -> Path:
    d = Path(tempfile.mkdtemp(prefix="fondok-excel-qa1127-"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _workbook(tmp_out: Path, model: dict):
    """Build and reopen (NOT read-only: formulas and number formats are read back)."""
    from openpyxl import load_workbook

    from app.export import build_excel

    out = tmp_out / f"{uuid4().hex}.xlsx"
    build_excel(model.get("deal_id", "qa-1127"), model, out)
    return load_workbook(out)


def _rows(ws) -> dict[str, int]:
    """Label → FIRST row carrying it (column A)."""
    out: dict[str, int] = {}
    for r in range(1, ws.max_row + 1):
        label = ws.cell(row=r, column=1).value
        if isinstance(label, str) and label not in out:
            out[label] = r
    return out


def _labels(ws) -> list[str]:
    return [
        ws.cell(row=r, column=1).value
        for r in range(1, ws.max_row + 1)
        if isinstance(ws.cell(row=r, column=1).value, str)
    ]


def _block(ws, heading: str) -> list[str]:
    """Labels of one Assumptions section: from its heading to the blank row."""
    labels: list[str] = []
    started = False
    for r in range(1, ws.max_row + 1):
        label = ws.cell(row=r, column=1).value
        if label == heading and not started:
            started = True
            continue
        if started:
            if label is None:
                break
            labels.append(str(label))
    return labels


def _cell(ws, label: str, col: int = 2):
    return ws.cell(row=_rows(ws)[label], column=col)


def _kimpton() -> dict:
    from tests.fixtures.kimpton_export_payload import kimpton_model

    return kimpton_model()


def _sam_capital():
    """Sam's deal: $36.4M, 132 keys, a $6.3M PIP with a 5% contingency on hard
    costs — the platform split gives Hard $4.725M / Soft $945K / Fees $630K."""
    from app.engines.capital import CapitalEngine, CapitalEngineInput

    return CapitalEngine().run(
        CapitalEngineInput(
            deal_id=uuid4(), purchase_price=36_400_000, keys=132,
            closing_costs_pct=0.02, renovation_budget=6_300_000,
            renovation_contingency_pct=0.05, working_capital=500_000,
            ltv=0.65, loan_costs_pct=0.015,
        )
    )


# ═══════════════ 1. renovation breakdown reads the capital engine ═══════════════


def test_live_investment_block_carries_the_capital_engines_renovation_breakdown() -> None:
    """The live payload reads ``renovation_breakdown`` (hard / soft / fees), the
    contingency fold-in and the total straight off the capital engine output —
    the same fields the Investment tab reads — never re-splitting the budget."""
    from app.export.live_payload import _build_investment

    cap = _sam_capital()
    inv = _build_investment(cap.model_dump(mode="json"), 132, 1_646_245.0)

    assert inv["renovation_hard_costs_usd"] == pytest.approx(4_725_000)
    assert inv["renovation_soft_costs_usd"] == pytest.approx(945_000)
    assert inv["renovation_professional_fees_usd"] == pytest.approx(630_000)
    assert inv["renovation_base_usd"] == pytest.approx(6_300_000)
    assert inv["renovation_contingency_pct"] == pytest.approx(0.05)
    assert inv["renovation_contingency_usd"] == pytest.approx(0.05 * 4_725_000)
    assert inv["renovation_total_usd"] == pytest.approx(6_300_000 + 0.05 * 4_725_000)
    # ...and they are the engine's own numbers, field for field.
    assert inv["renovation_hard_costs_usd"] == cap.renovation_breakdown.hard
    assert inv["renovation_soft_costs_usd"] == cap.renovation_breakdown.soft
    assert inv["renovation_professional_fees_usd"] == cap.renovation_breakdown.fees
    assert inv["renovation_contingency_usd"] == cap.renovation_contingency_usd
    assert inv["renovation_total_usd"] == cap.renovation_total_usd
    # The Renovation use line IS the total (budget + contingency).
    assert inv["renovation_budget_usd"] == pytest.approx(inv["renovation_total_usd"])
    # The engine's provenance badge for the split travels with the values.
    assert inv["renovation_split_note"] == cap.renovation_breakdown.note
    assert "75% hard costs" in inv["renovation_split_note"]


def test_live_investment_block_omits_the_breakdown_the_engine_did_not_emit() -> None:
    """No renovation budget → the engine publishes no breakdown → the payload
    carries none (the sheet then says why), never a $0 split."""
    from app.engines.capital import CapitalEngine, CapitalEngineInput
    from app.export.live_payload import _build_investment

    cap = CapitalEngine().run(
        CapitalEngineInput(deal_id=uuid4(), purchase_price=36_400_000, keys=132, renovation_budget=0)
    )
    assert cap.renovation_breakdown is None
    inv = _build_investment(cap.model_dump(mode="json"), 132, None)
    for key in ("renovation_hard_costs_usd", "renovation_soft_costs_usd",
                "renovation_professional_fees_usd", "renovation_split_note", "renovation_budget_usd"):
        assert key not in inv, key


def test_live_investment_block_falls_back_to_the_renovation_line_for_a_pre_fon71_run() -> None:
    """A run persisted before the fold-in fields: base and total are the
    Renovation use line (the Investment tab's own fallback)."""
    from app.export.live_payload import _build_investment

    legacy = {
        "uses": [
            {"label": "Purchase Price", "amount": 36_400_000.0},
            {"label": "Renovation", "amount": 5_280_000.0},
            {"label": "Total Uses", "amount": 41_680_000.0, "is_total": True},
        ],
        "total_capital": 41_680_000.0,
    }
    inv = _build_investment(legacy, 132, None)
    assert inv["renovation_base_usd"] == 5_280_000.0
    assert inv["renovation_total_usd"] == 5_280_000.0
    assert "renovation_hard_costs_usd" not in inv


def test_assumptions_sheet_prints_hard_soft_fees_contingency_and_total(tmp_out: Path) -> None:
    ws = _workbook(tmp_out, _kimpton())["Assumptions"]
    acquisition = _block(ws, "Acquisition")

    want = [
        ("Renovation Budget (before contingency)", 5_280_000),
        ("Hard Costs", 3_960_000),
        ("Soft Costs", 792_000),
        ("Professional Fees", 528_000),
        ("Contingency", 0),
        ("Renovation Total (incl. contingency)", 5_280_000),
    ]
    # In this order, consecutively.
    labels = [w[0] for w in want]
    start = acquisition.index(labels[0])
    assert acquisition[start:start + len(labels)] == labels
    for label, value in want:
        c = _cell(ws, label)
        assert c.value == value, label
        assert c.number_format == USD_FMT, label
    # The engine's split badge sits beside each split part; the contingency
    # row names its rule.
    for label in ("Hard Costs", "Soft Costs", "Professional Fees"):
        assert _cell(ws, label, 3).value.startswith("Platform default split — 75% hard costs")
    assert _cell(ws, "Contingency", 3).value == "0.00% of renovation hard costs"
    assert _cell(ws, "Renovation Total (incl. contingency)", 3).value == (
        "Budget + contingency; the Renovation line in Sources & Uses"
    )
    # The retired single "Renovation Budget" row is gone; the deal-level S&U
    # lines keep a label that says what they are.
    assert "Renovation Budget" not in acquisition
    assert "Soft Costs (Sources & Uses line, outside renovation)" in acquisition
    assert _cell(ws, "Soft Costs (Sources & Uses line, outside renovation)").value == 528_000


def test_assumptions_sheet_prints_sams_numbers_off_a_real_engine_run(tmp_out: Path) -> None:
    """End to end: the engine run → live investment block → the cells Sam read."""
    from app.export.live_payload import _build_investment

    model = _kimpton()
    model["investment_engine"] = _build_investment(_sam_capital().model_dump(mode="json"), 132, 1_646_245.0)
    ws = _workbook(tmp_out, model)["Assumptions"]
    assert _cell(ws, "Hard Costs").value == pytest.approx(4_725_000)
    assert _cell(ws, "Soft Costs").value == pytest.approx(945_000)
    assert _cell(ws, "Professional Fees").value == pytest.approx(630_000)
    assert _cell(ws, "Contingency").value == pytest.approx(236_250)
    assert _cell(ws, "Contingency", 3).value == "5.00% of renovation hard costs"
    assert _cell(ws, "Renovation Total (incl. contingency)").value == pytest.approx(6_536_250)
    # No deal-level S&U Soft Costs / Contingency were entered → no such rows.
    acquisition = _block(ws, "Acquisition")
    assert "Soft Costs (Sources & Uses line, outside renovation)" not in acquisition
    assert "Contingency (Sources & Uses line, outside renovation)" not in acquisition


def test_assumptions_sheet_says_why_when_the_engine_emitted_no_breakdown(tmp_out: Path) -> None:
    model = _kimpton()
    inv = model["investment_engine"]
    # (a) a run predating the split fields: budget known, parts not.
    for key in ("renovation_hard_costs_usd", "renovation_soft_costs_usd",
                "renovation_professional_fees_usd", "renovation_contingency_usd",
                "renovation_contingency_pct", "renovation_split_note"):
        inv.pop(key)
    ws = _workbook(tmp_out, model)["Assumptions"]
    for label in ("Hard Costs", "Soft Costs", "Professional Fees", "Contingency"):
        assert _cell(ws, label).value == (
            "not available: no renovation breakdown on the deal's capital engine output — run the model again"
        ), label
    assert _cell(ws, "Contingency", 3).value == "Percent of renovation hard costs"
    # (b) no renovation budget at all.
    for key in ("renovation_budget_usd", "renovation_base_usd", "renovation_total_usd", "renovation_per_key_usd"):
        inv.pop(key)
    ws = _workbook(tmp_out, model)["Assumptions"]
    for label in ("Renovation Budget (before contingency)", "Hard Costs", "Renovation Total (incl. contingency)"):
        assert _cell(ws, label).value == (
            "not available: no renovation budget on the deal's capital engine output"
        ), label


# ═══════════════ 2. one LTC row, reading what the engine emits ═══════════════


def test_live_debt_block_ltc_is_the_capital_engines_senior_ltc() -> None:
    """``debt_engine.ltc`` ← ``capital.senior_ltc`` (senior ÷ total uses; equal
    to ``capital.ltc`` without a junior tranche). No engine emits ``ltv_cost``."""
    from app.engines.debt import DebtEngine, DebtEngineInputExt
    from app.export.live_payload import _build_debt

    cap = _sam_capital()
    debt = DebtEngine().run(
        DebtEngineInputExt(
            deal_id=uuid4(), loan_amount=cap.debt_amount, ltv=0.65, interest_rate=0.068,
            term_years=5, amortization_years=30, interest_only_years=0,
            noi_by_year=[3_000_000, 3_100_000, 3_200_000, 3_300_000, 3_400_000],
        )
    )
    cap_json = cap.model_dump(mode="json")
    block = _build_debt(debt.model_dump(mode="json"), cap_json)
    assert cap.senior_ltc is not None
    assert block["ltc"] == pytest.approx(cap.senior_ltc)
    assert block["ltc"] == pytest.approx(cap.debt_amount / cap.total_capital)
    assert block["total_ltc"] == pytest.approx(cap.ltc)
    assert "ltv_cost" not in block
    assert "ltv_cost" not in cap_json and "ltv_cost" not in debt.model_dump()


def test_assumptions_senior_debt_block_has_one_ltc_row_and_no_ltv_cost(tmp_out: Path) -> None:
    ws = _workbook(tmp_out, _kimpton())["Assumptions"]
    senior = _block(ws, "Senior Debt")
    assert senior == [
        "Loan Amount", "LTV (value)", "LTC", "Interest Rate",
        "Amortization (yrs)", "Term (yrs)", "IO Period (months)",
    ]
    assert "LTV (cost)" not in _labels(ws)
    assert senior.count("LTC") == 1
    c = _cell(ws, "LTC")
    assert c.value == 0.547
    assert c.number_format == PCT_FMT
    assert _cell(ws, "LTC", 3).value == "Senior loan ÷ total uses"
    # The all-debt block keeps its own LTC (a different basis), once.
    assert _block(ws, "All Debt (senior + priced junior tranches)").count("LTC") == 1
    # Nothing in the Senior Debt block says "not available" for this complete deal.
    for label in senior:
        assert not str(_cell(ws, label).value).startswith("not available"), label


# ═══════════════ 3. the cover's Year 1 NOI is the exact dollar figure ═══════════════


def test_live_investment_block_carries_the_whole_dollar_year1_noi() -> None:
    from app.export.live_payload import _build_investment

    inv = _build_investment(_sam_capital().model_dump(mode="json"), 132, 1_646_245.0)
    assert inv["year1_noi_before_reserve_usd"] == 1_646_245.0
    assert "year1_noi_before_reserve_usd" not in _build_investment({"uses": []}, 132, None)


def test_cover_year1_noi_is_not_rounded_to_thousands(tmp_out: Path) -> None:
    model = _kimpton()
    model["investment_engine"]["year1_noi_before_reserve_usd"] = 1_646_245
    # The proforma row is in $000s — what the cover used to read back.
    for line in model["p_and_l_engine_proforma"]["lines"]:
        if line["label"] == "Net Operating Income (before FF&E reserve)":
            line["y1"] = 1646
    ws = _workbook(tmp_out, model)["Cover"]
    c = _cell(ws, "Year 1 NOI (before FF&E reserve)")
    assert c.value == 1_646_245
    assert c.value != 1_646_000
    assert c.number_format == USD_FMT

    # Every other dollar cell on the cover is the payload's exact figure too —
    # none is read off the $000 proforma rows.
    inv, ret, debt = model["investment_engine"], model["returns_engine"], model["debt_engine"]
    assert _cell(ws, "Purchase Price").value == inv["purchase_price_usd"]
    assert _cell(ws, "$ / Key").value == inv["price_per_key_usd"]
    assert _cell(ws, "Total Capital").value == inv["total_capital_usd"]
    assert _cell(ws, "Senior Loan Amount").value == debt["loan_amount_usd"]
    assert _cell(ws, ENTRY_NOI).value == inv["entry_noi_usd"]
    assert _cell(ws, T12_NOI).value == inv["t12_noi_before_reserve_usd"]
    assert _cell(ws, "Levered IRR").value == ret["levered_irr"]
    for label in ("Purchase Price", "$ / Key", "Total Capital", "Senior Loan Amount", ENTRY_NOI, T12_NOI):
        assert _cell(ws, label).number_format == USD_FMT, label


def test_cover_year1_noi_falls_back_to_the_proforma_row_only_without_the_exact_figure(tmp_out: Path) -> None:
    model = _kimpton()
    model["investment_engine"].pop("year1_noi_before_reserve_usd")
    ws = _workbook(tmp_out, model)["Cover"]
    assert _cell(ws, "Year 1 NOI (before FF&E reserve)").value == 5_308 * 1000


# ═══════════════ 4. Model Version = build SHA · run ═══════════════


def test_cover_model_version_is_the_build_sha_and_run(tmp_out: Path) -> None:
    model = _kimpton()
    model["build_sha"] = "84f2281b9c0d4e5f6a7b8c9d0e1f2a3b4c5d6e7f"
    model["run_id"] = "5d8ebae8-1111-4222-8333-444455556666"
    ws = _workbook(tmp_out, model)["Cover"]
    assert ws["A8"].value == "Model Version"
    assert ws["B8"].value == "84f2281b · run 5d8ebae8"
    # The "Generated" stamp no longer carries a version placeholder.
    assert ws["B7"].value.startswith("Generated by Fondok · ")
    assert "fondok-engine" not in ws["B7"].value

    # SHA without a run: the SHA alone.
    model.pop("run_id")
    assert _workbook(tmp_out, model)["Cover"]["B8"].value == "84f2281b"


def test_cover_model_version_is_not_available_without_a_build_sha(tmp_out: Path) -> None:
    """The fixture carries no SHA (it was never a worker build): the cell says
    so, and no placeholder string appears anywhere on the cover."""
    model = _kimpton()
    assert "build_sha" not in model and "model_version" not in model
    ws = _workbook(tmp_out, model)["Cover"]
    assert ws["B8"].value == (
        "not available: no build SHA in the worker environment "
        "(RAILWAY_GIT_COMMIT_SHA / FONDOK_BUILD_SHA unset)"
    )
    blob = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)
    assert "fondok-engine" not in blob
    # A blank SHA is "unset" too — never printed as an empty version.
    model["build_sha"] = "   "
    assert _workbook(tmp_out, model)["Cover"]["B8"].value.startswith("not available: no build SHA")


def test_model_version_reads_the_same_source_as_health_build_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    """``build_info()`` is what ``/health`` reports; the cover label is built
    from it (RAILWAY_GIT_COMMIT_SHA first, else FONDOK_BUILD_SHA, else null)."""
    from app.api.health import build_info
    from app.export.excel import _model_version_label

    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("FONDOK_BUILD_SHA", raising=False)
    assert build_info()["build_sha"] is None
    assert _model_version_label({"build_sha": build_info()["build_sha"]}) is None

    monkeypatch.setenv("FONDOK_BUILD_SHA", "84f2281b9c0d4e5f6a7b8c9d0e1f2a3b4c5d6e7f")
    sha = build_info()["build_sha"]
    assert sha == "84f2281b9c0d4e5f6a7b8c9d0e1f2a3b4c5d6e7f"
    assert _model_version_label({"build_sha": sha, "run_id": "5d8ebae8-1111-4222-8333-444455556666"}) == (
        "84f2281b · run 5d8ebae8"
    )
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "deadbeefcafe")
    assert _model_version_label({"build_sha": build_info()["build_sha"]}) == "deadbeef"


# ═══════════════ 5. entry NOI row + formula caps ═══════════════


def test_live_investment_block_carries_the_entry_noi_and_t12_noi() -> None:
    from app.export.live_payload import _build_investment

    cap = _sam_capital().model_dump(mode="json")
    # Open, displaced deal: the undisplaced entry NOI (run-rate) beats Year 1.
    inv = _build_investment(cap, 132, 1_500_000.0, False, 1_646_245.0, t12_ebitda=1_420_000.0)
    assert inv["entry_noi_usd"] == 1_646_245.0
    assert inv["year1_noi_before_reserve_usd"] == 1_500_000.0
    assert inv["t12_noi_before_reserve_usd"] == 1_420_000.0
    # The published caps ARE those NOIs over the price — what the formulas reproduce.
    assert inv["entry_cap_rate_year1_uw"] == pytest.approx(1_646_245.0 / 36_400_000)
    assert inv["entry_cap_rate_t12"] == pytest.approx(1_420_000.0 / 36_400_000)
    # Nothing without a statement.
    bare = _build_investment(cap, 132, 1_500_000.0)
    assert "t12_noi_before_reserve_usd" not in bare
    assert "entry_cap_rate_t12" not in bare
    assert bare["entry_noi_usd"] == 1_500_000.0
    # A closed hotel with no entry NOI has none to print.
    closed = _build_investment(cap, 132, -200_000.0, True)
    assert "entry_noi_usd" not in closed
    assert closed["entry_cap_not_yet_open"] is True


def _assert_formula_caps(ws, *, price_label: str = "Purchase Price") -> None:
    rows = _rows(ws)
    price_row, entry_noi_row, t12_row = rows[price_label], rows[ENTRY_NOI], rows[T12_NOI]
    assert ws.cell(row=price_row, column=2).value == 36_400_000

    entry_noi = ws.cell(row=entry_noi_row, column=2)
    assert entry_noi.value == 5_308_245
    assert entry_noi.number_format == USD_FMT
    entry_cap = ws.cell(row=rows[ENTRY_CAP], column=2)
    assert entry_cap.value == f"=B{entry_noi_row}/B{price_row}"
    assert entry_cap.number_format == PCT_FMT

    t12 = ws.cell(row=t12_row, column=2)
    assert t12.value == 4_180_000
    assert t12.number_format == USD_FMT
    in_place = ws.cell(row=rows[IN_PLACE_CAP], column=2)
    assert in_place.value == f"=B{t12_row}/B{price_row}"
    assert in_place.number_format == PCT_FMT

    # The NOI row sits directly above its cap; the two pairs are adjacent.
    assert rows[ENTRY_CAP] == entry_noi_row + 1
    assert rows[T12_NOI] == rows[ENTRY_CAP] + 1
    assert rows[IN_PLACE_CAP] == t12_row + 1


def test_cover_entry_noi_row_and_formula_caps(tmp_out: Path) -> None:
    ws = _workbook(tmp_out, _kimpton())["Cover"]
    _assert_formula_caps(ws)
    # The cover's price cell is the Quick Read one (B11).
    assert _rows(ws)["Purchase Price"] == 11
    assert _cell(ws, ENTRY_CAP).value == f"=B{_rows(ws)[ENTRY_NOI]}/B11"


def test_assumptions_entry_noi_row_and_formula_caps(tmp_out: Path) -> None:
    ws = _workbook(tmp_out, _kimpton())["Assumptions"]
    _assert_formula_caps(ws)
    assert _cell(ws, ENTRY_CAP, 3).value == "= Entry NOI ÷ Purchase Price"
    assert _cell(ws, IN_PLACE_CAP, 3).value == "= T-12 NOI ÷ Purchase Price"
    assert _cell(ws, ENTRY_NOI, 3).value == "Year 1 NOI before FF&E reserve, no closure, no displacement"


def test_formula_caps_reproduce_the_engines_caps() -> None:
    """The formula divides the same two numbers the engine divided."""
    inv = _kimpton()["investment_engine"]
    assert inv["entry_noi_usd"] / inv["purchase_price_usd"] == pytest.approx(inv["entry_cap_rate_year1_uw"])
    assert inv["t12_noi_before_reserve_usd"] / inv["purchase_price_usd"] == pytest.approx(inv["entry_cap_rate_t12"])


def test_in_place_cap_says_no_twelve_month_statement_when_the_t12_is_absent(tmp_out: Path) -> None:
    model = _kimpton()
    model["investment_engine"].pop("t12_noi_before_reserve_usd")
    model["investment_engine"].pop("entry_cap_rate_t12")
    wb = _workbook(tmp_out, model)
    for sheet in ("Cover", "Assumptions"):
        ws = wb[sheet]
        assert _cell(ws, T12_NOI).value == NO_T12, sheet
        assert _cell(ws, IN_PLACE_CAP).value == NO_T12, sheet
        # The entry pair beside it is untouched.
        assert _cell(ws, ENTRY_NOI).value == 5_308_245, sheet
        assert str(_cell(ws, ENTRY_CAP).value).startswith("="), sheet


def test_entry_cap_keeps_its_refusal_in_words_when_the_engine_refused_it(tmp_out: Path) -> None:
    """A closed hotel on a stale T-12 (FON-122): no formula, the dated reason."""
    model = _kimpton()
    inv = model["investment_engine"]
    inv.pop("entry_cap_rate_year1_uw")
    inv.pop("entry_noi_usd")
    inv["entry_cap_t12_out_of_date"] = "T-12 ends Mar 2025, 14 months before close"
    wb = _workbook(tmp_out, model)
    for sheet in ("Cover", "Assumptions"):
        ws = wb[sheet]
        assert _cell(ws, ENTRY_CAP).value == "not available: n/a, T-12 ends Mar 2025, 14 months before close", sheet
        assert _cell(ws, ENTRY_NOI).value == "not available: n/a, T-12 ends Mar 2025, 14 months before close", sheet
        # The in-place pair still traces.
        assert str(_cell(ws, IN_PLACE_CAP).value).startswith("="), sheet


def test_a_cap_without_its_noi_row_is_written_as_the_engines_value(tmp_out: Path) -> None:
    """A payload that carries the cap but not the NOI (pre-upgrade worker):
    the cap is still the engine's number, not a formula over a missing cell."""
    model = _kimpton()
    inv = model["investment_engine"]
    inv.pop("entry_noi_usd")
    cap = inv["entry_cap_rate_year1_uw"]
    wb = _workbook(tmp_out, model)
    for sheet in ("Cover", "Assumptions"):
        ws = wb[sheet]
        assert _cell(ws, ENTRY_NOI).value == "not available: no entry NOI on the deal's expense engine output", sheet
        c = _cell(ws, ENTRY_CAP)
        assert c.value == pytest.approx(cap), sheet
        assert c.number_format == PCT_FMT, sheet


def test_no_formula_divides_by_a_missing_or_zero_price(tmp_out: Path) -> None:
    model = copy.deepcopy(_kimpton())
    inv = model["investment_engine"]
    inv.pop("purchase_price_usd")
    wb = _workbook(tmp_out, model)
    for sheet in ("Cover", "Assumptions"):
        ws = wb[sheet]
        # The caps the payload still carries are written as values, never as
        # a formula over a "not available" price cell.
        assert isinstance(_cell(ws, ENTRY_CAP).value, float), sheet
        assert isinstance(_cell(ws, IN_PLACE_CAP).value, float), sheet
