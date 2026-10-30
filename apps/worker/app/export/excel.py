# ruff: noqa: RUF001
"""Excel acquisition model builder.

Builds an institutional underwriting workbook from an engine outputs
dict (see ``evals/golden-set/kimpton-angler/expected/model.json``).

──────────────────────────────────────────────────────────────────────
Wave 4 W4.2 — Excel acquisition model refresh.

The legacy export (pre-Wave-2) shipped a fixed 10-sheet workbook with
none of the Wave 2/3 analytical artifacts (segment mix, PIP
displacement, three-bucket capex, op-ratio precedence, pricing
sensitivity grid, max-price solver, comparable sales, historical
baseline, STR forecast, named scenarios, LOI draft). Analysts who
exported the .xlsx saw a stale model.

This module now pulls every Wave 2/3 artifact into the workbook. Each
new sheet is **conditional** — it renders only when its data is
present on the ``model`` dict, so a barebones deal still produces a
clean model. The cover sheet's "Sections Included" list reflects what
actually shipped.

Sheet catalog (in build order — only those with data are included):

  Always (legacy):
    1.  Cover                       (refreshed)
    2.  Assumptions
    3.  Sources & Uses
    4.  Operating Proforma
    5.  Debt Schedule
    6.  Returns
    7.  Partnership
    8.  Variance
    9.  Market Comps

  Wave 2/3 (conditional):
    10. Revenue Mix                 — when segments_by_year set
    11. Renovation Plan             — when pip_displacement set
    12. Capital Plan (3-Bucket)     — when capex_schedule set
    13. Op-Ratio Provenance         — whenever the deal was modelled (says so
                                      in words when every ratio is a seed)
    14. Pricing Sensitivity         — when sensitivity_grid set, or a one-line
                                      sheet saying why pricing is unavailable
    15. Comparable Sales            — when comp_sales set
    16. Historical Baseline         — when historical_baseline set
    17. STR Forecast                — when str_forecast set
    18. Named Scenarios             — when named_scenarios set
    19. LOI Appendix                — when loi_draft set
    20. Refusals                    — when the payload carries refusals

Backward compat: every new sheet builder no-ops when its source data is
None / empty. The legacy 9-sheet workbook is still produced when no
Wave 2/3 data is on the model (the legacy "Sensitivity" sheet is
replaced by the W4.2 "Pricing Sensitivity" sheet — barebones deals
that lack sensitivity_grid drop the sensitivity sheet entirely rather
than synthesizing one).

Number formats follow institutional convention (currency, percent, x).

──────────────────────────────────────────────────────────────────────
Phase 4.2 — typed refusals.

The dashes this workbook writes go through
:func:`app.export.refusals.refuse`, which returns the same glyph and
records why. The "Refusals" sheet then lists what the export declined to
produce — the concept, where it appeared, the reason code, and its meaning
from ``REASON_META``. It renders only when the payload itself carried
refusals (``model["refusals"]``), so a complete test-fixture workbook and
a barebones workbook keep exactly the sheets they had before.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fondok_schemas.reasons import REASON_META, ReasonCode
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .labels import (
    EXIT_SCHEDULE_COLUMNS,
    EXIT_SCHEDULE_NO_RETURNS_NOTE,
    EXIT_SCHEDULE_STALE_NOTE,
    EXIT_SCHEDULE_TITLE,
    exit_row_label,
    COMP_CAP_METHOD_DISPLAY,
    COMP_COVERAGE_DISPLAY,
    NOI_HEADLINE_LABELS,
    SOLVER_STATUS_NOTE,
    line_label,
    source_display,
)
from .refusals import (
    as_float,
    coerce_reason,
    collect_refusals,
    dedupe,
    refuse,
    sum_strict,
    unavailable,
)

# ─────────────────────────── styling helpers ───────────────────────────

USD_FMT = '$#,##0;[Red]($#,##0)'
USD_2_FMT = '$#,##0.00'
PCT_FMT = '0.00%'
MULT_FMT = '0.00"x"'
INT_FMT = '#,##0'

HEADER_FILL = PatternFill("solid", fgColor="1F2937")  # slate-800
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
SUBHEAD_FILL = PatternFill("solid", fgColor="E5E7EB")  # gray-200
SUBHEAD_FONT = Font(bold=True, color="111827", size=10)
TOTAL_FILL = PatternFill("solid", fgColor="FEF3C7")  # amber-100
TOTAL_FONT = Font(bold=True, color="111827", size=10)
BRAND_FILL = PatternFill("solid", fgColor="F59E0B")  # amber-500
BRAND_FONT = Font(bold=True, color="FFFFFF", size=14)
LIGHT_BORDER = Border(
    left=Side(style="thin", color="E5E7EB"),
    right=Side(style="thin", color="E5E7EB"),
    top=Side(style="thin", color="E5E7EB"),
    bottom=Side(style="thin", color="E5E7EB"),
)

# Source-color map for op-ratio provenance rows. T-12 = green (best
# anchor), Portfolio = blue (in-house brand), CBRE = amber (market
# brand-light), HOST = gray (neutral fallback), Override = pink (warn —
# analyst intervened).
_SOURCE_ROW_FILL: dict[str, PatternFill] = {
    "t12_actual": PatternFill("solid", fgColor="DCFCE7"),       # green
    "portfolio_pnl": PatternFill("solid", fgColor="DBEAFE"),    # blue
    "cbre_horizons": PatternFill("solid", fgColor="FEF3C7"),    # amber
    "pnl_benchmark": PatternFill("solid", fgColor="E5E7EB"),    # gray
    "analyst_override": PatternFill("solid", fgColor="FCE7F3"), # pink
    "seed": PatternFill("solid", fgColor="F3F4F6"),             # neutral
}

#: Source code → display word. The full vocabulary lives in
#: :mod:`app.export.labels`; this alias keeps the module's call sites short.
_SOURCE_DISPLAY = source_display


def _style_header_row(ws: Worksheet, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")
        cell.border = LIGHT_BORDER


def _style_subhead_row(ws: Worksheet, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = SUBHEAD_FILL
        cell.font = SUBHEAD_FONT
        cell.border = LIGHT_BORDER


def _style_total_row(ws: Worksheet, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = TOTAL_FILL
        cell.font = TOTAL_FONT
        cell.border = LIGHT_BORDER


def _autosize(ws: Worksheet, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _freeze_top(ws: Worksheet, row: int = 2) -> None:
    ws.freeze_panes = ws.cell(row=row, column=1)


def _apply_color_scale(
    ws: Worksheet,
    range_str: str,
    *,
    lo_color: str = "FECACA",  # red-200
    mid_color: str = "FEF3C7", # amber-100
    hi_color: str = "BBF7D0",  # green-200
) -> None:
    """Attach a 3-colour conditional-format rule to a range.

    Uses ``openpyxl.formatting.rule.ColorScaleRule`` so the workbook
    carries a real conditional-format rule (analysts can see the rule
    in Excel's "Manage Rules" dialog rather than just baked-in fill).
    """
    rule = ColorScaleRule(
        start_type="min", start_color=lo_color,
        mid_type="percentile", mid_value=50, mid_color=mid_color,
        end_type="max", end_color=hi_color,
    )
    ws.conditional_formatting.add(range_str, rule)


# ─────────────────────────── Wave 2/3 aggregator ───────────────────────


_NA_FONT = Font(italic=True, color="92400E", size=9)


_as_float = as_float
_sum_strict = sum_strict


def _write_value(
    ws: Worksheet,
    row: int,
    col: int,
    value: Any,
    fmt: str | None,
    *,
    concept: str,
    missing: str,
    code: ReasonCode = ReasonCode.NO_SOURCE,
) -> None:
    """Write a numeric cell, or ``not available: <missing>`` when it is ``None``.

    The one seam every label/value row goes through: a value the payload did
    not state renders as an explicit sentence (and a typed refusal), never as
    0 / 0% / $0 carrying a number format.
    """
    if value is None:
        c = ws.cell(row=row, column=col, value=unavailable(code, missing, concept))
        c.font = _NA_FONT
        return
    c = ws.cell(row=row, column=col, value=value)
    if fmt:
        c.number_format = fmt


def _write_note(ws: Worksheet, row: int, col: int, text: str) -> None:
    c = ws.cell(row=row, column=col, value=text)
    c.font = _NA_FONT


def _aggregate_wave2_3_for_excel(model: dict[str, Any]) -> dict[str, Any]:
    """Read Wave 2/3 engine outputs off the ``model`` dict into a clean
    shape for the Excel sheet builders. Mirrors the memo PDF's
    ``_aggregate_wave2_for_memo`` so a single deal payload feeds both
    artifacts.

    Returns a dict with one key per Wave 2/3 section. Each key holds
    either the section's data (dict / list) OR ``None`` when the
    underlying engine hasn't been run. Downstream sheet builders treat
    ``None`` as "skip the sheet entirely" — that's how a barebones
    deal still produces a valid model.
    """
    # ── Revenue segments (multi-year preferred) ──────────────────────
    segments_by_year = model.get("segments_by_year")
    rev = model.get("revenue_engine") or {}
    if not segments_by_year:
        segments_by_year = rev.get("segments_by_year") or rev.get("segment_breakdown")
    if not segments_by_year or not isinstance(segments_by_year, list):
        segments_by_year = None

    # ── PIP displacement ─────────────────────────────────────────────
    pip = model.get("pip_displacement")
    if pip and isinstance(pip, dict):
        if not pip.get("closure_strategy") or pip.get("closure_strategy") == "none":
            pip = None
    else:
        pip = None

    # ── Capex 3-bucket schedule ──────────────────────────────────────
    capex_schedule = model.get("capex_schedule")
    if not capex_schedule or not isinstance(capex_schedule, list):
        capex_schedule = None

    # ── Op-ratio provenance — B2: present whenever the payload carries the
    # block, including the all-seed case (the block then carries a note).
    op_prov = model.get("op_ratio_provenance")
    if not isinstance(op_prov, dict) or not (op_prov.get("lines") or op_prov.get("note")):
        op_prov = None

    # ── Pricing sensitivity grid ─────────────────────────────────────
    grid = model.get("sensitivity_grid")
    if not grid or not isinstance(grid, dict) or not grid.get("cells"):
        grid = None

    # ── Max-price result ─────────────────────────────────────────────
    max_price = model.get("max_price")
    if not max_price or not isinstance(max_price, dict):
        max_price = None

    # ── A3: why the pricing sheet has no numbers, when it has none ──
    pricing_unavailable = model.get("pricing_unavailable")
    if not isinstance(pricing_unavailable, str) or not pricing_unavailable.strip():
        pricing_unavailable = None
    # ── EM-only deal: no IRR grid, stated; the max price still renders.
    grid_unavailable = model.get("sensitivity_grid_unavailable")
    if not isinstance(grid_unavailable, str) or not grid_unavailable.strip():
        grid_unavailable = None

    # ── Historical baseline ─────────────────────────────────────────
    baseline = model.get("historical_baseline")
    if not baseline or not isinstance(baseline, dict) or not baseline.get("years"):
        baseline = None
    else:
        coverage = _as_float(baseline.get("coverage_pct"))
        if coverage is None or coverage <= 0:
            baseline = None

    # ── Comp Sales (W3.1) ────────────────────────────────────────────
    comp_sales = model.get("comp_sales")
    if not comp_sales or not isinstance(comp_sales, dict) or not comp_sales.get("transactions"):
        comp_sales = None

    # ── STR forward forecast (W3.3) ──────────────────────────────────
    str_forecast = model.get("str_forecast")
    if not str_forecast or not isinstance(str_forecast, dict):
        str_forecast = None
    else:
        hist = str_forecast.get("historical_months") or []
        fcst = str_forecast.get("forecast_months") or {}
        if not hist and not fcst:
            str_forecast = None

    # ── Named scenarios (W3.2) ──────────────────────────────────────
    named_scenarios = model.get("named_scenarios")
    if (
        not named_scenarios
        or not isinstance(named_scenarios, list)
        or len(named_scenarios) < 2
    ):
        # Need at least 2 scenarios for a compare table.
        named_scenarios = None

    # ── LOI draft ────────────────────────────────────────────────────
    loi = model.get("loi_draft")
    if not loi or not isinstance(loi, dict) or not loi.get("rendered_markdown"):
        loi = None

    return {
        "segments_by_year": segments_by_year,
        "pip": pip,
        "capex_schedule": capex_schedule,
        "op_ratio_provenance": op_prov,
        "sensitivity_grid": grid,
        "max_price": max_price,
        "pricing_unavailable": pricing_unavailable,
        "grid_unavailable": grid_unavailable,
        "historical_baseline": baseline,
        "comp_sales": comp_sales,
        "str_forecast": str_forecast,
        "named_scenarios": named_scenarios,
        "loi": loi,
    }


# ─────────────────────────── tab builders ───────────────────────────


def _build_cover(wb: Workbook, model: dict[str, Any], sections: list[str]) -> None:
    """Cover sheet — refreshed for W4.2.

    Adds: a "Quick Read" block of six headline KPIs, a "Sections
    Included" list that automatically reflects which Wave 2/3 sheets
    actually rendered, and an explicit "Generated by Fondok · v3.x ·
    UTC timestamp" stamp.

    Caller is expected to have reserved the active workbook sheet
    (``wb.active``) for the Cover; this function populates it.
    """
    ws = wb.active
    ws.title = "Cover"

    deal_name = model.get("deal_name", "Hotel Underwriting Model")
    location = model.get("location", "")
    keys = model.get("keys", "")
    brand = model.get("brand", "")

    inv = model.get("investment_engine", {})
    ret = model.get("returns_engine", {})
    debt = model.get("debt_engine", {})
    pl_lines = (model.get("p_and_l_engine_proforma") or {}).get("lines", [])
    # The headline NOI row is the BEFORE-FF&E-reserve basis (FON-59 #1); the
    # proforma also carries a "Cash NOI (after FF&E reserve)" row below the
    # reserve line, which the cover deliberately does NOT use.
    noi_row = next((row for row in pl_lines if row.get("label") in NOI_HEADLINE_LABELS), None)
    noi_y1 = _as_float(noi_row.get("y1")) if noi_row else None
    noi_stab = _as_float(noi_row.get("y5")) if noi_row else None
    noi_y1 = noi_y1 * 1000 if noi_y1 is not None else None
    noi_stab = noi_stab * 1000 if noi_stab is not None else None

    # Title ribbon
    ws.merge_cells("A1:F1")
    title = ws["A1"]
    title.value = "FONDOK ACQUISITION MODEL"
    title.fill = BRAND_FILL
    title.font = BRAND_FONT
    title.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[1].height = 28

    ws["A3"] = "Property"
    ws["B3"] = deal_name
    ws["A4"] = "Location"
    ws["B4"] = location
    ws["A5"] = "Brand"
    ws["B5"] = brand
    ws["A6"] = "Keys"
    ws["B6"] = keys
    ws["A7"] = "Generated"
    ws["B7"] = (
        f"Generated by Fondok · "
        f"{model.get('model_version', 'fondok-engine-3.x')} · "
        f"{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    ws["A8"] = "Model Version"
    ws["B8"] = model.get("model_version", "fondok-engine-3.x")

    for r in range(3, 9):
        ws.cell(row=r, column=1).font = Font(bold=True, size=11)

    # ── Quick Read — 6 headline numbers ─────────────────────────────
    ws.merge_cells("A10:F10")
    ws["A10"] = "QUICK READ"
    _style_header_row(ws, 10, 6)

    quick = [
        ("Purchase Price", inv.get("purchase_price_usd"), USD_FMT, "purchase_price",
         "no purchase price on the deal's capital engine output"),
        ("$ / Key", inv.get("price_per_key_usd"), USD_FMT, "price_per_key",
         "no price per key on the deal's capital engine output"),
        ("Year 1 NOI", noi_y1, USD_FMT, "noi",
         "no Year 1 NOI on the deal's proforma"),
        ("Stabilized NOI", noi_stab, USD_FMT, "noi_stabilized",
         "no Year 5 NOI on the deal's proforma"),
        ("Levered IRR", ret.get("levered_irr"), PCT_FMT, "levered_irr",
         "no levered IRR on the deal's returns engine output"),
        ("Equity Multiple", ret.get("equity_multiple"), MULT_FMT, "equity_multiple",
         "no equity multiple on the deal's returns engine output"),
    ]
    row = 11
    for label, val, fmt, concept, missing in quick:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        _write_value(ws, row, 2, val, fmt, concept=concept, missing=missing)
        row += 1

    # ── Key Metrics block (existing) ────────────────────────────────
    row += 1
    ws.merge_cells(f"A{row}:F{row}")
    ws.cell(row=row, column=1, value="KEY METRICS")
    _style_header_row(ws, row, 6)
    row += 1

    metrics = [
        ("Total Capital", inv.get("total_capital_usd"), USD_FMT, "total_capital",
         "no total capital on the deal's capital engine output"),
        ("Loan Amount", debt.get("loan_amount_usd"), USD_FMT, "loan_amount",
         "no loan amount on the deal's debt engine output"),
        ("Loan Rate", debt.get("interest_rate_pct"), PCT_FMT, "interest_rate",
         "no interest rate on the deal's debt engine output"),
        ("Year 1 DSCR", debt.get("year1_dscr"), MULT_FMT, "dscr",
         "no Year 1 DSCR on the deal's debt engine output"),
        ("Year 1 CoC", ret.get("year1_cash_on_cash"), PCT_FMT, "year_one_coc",
         "no Year 1 cash-on-cash on the deal's returns engine output"),
        ("Hold (yrs)", ret.get("hold_years"), INT_FMT, "hold_years",
         "no hold period on the deal's returns engine output"),
        ("Exit Cap Rate", ret.get("exit_cap_rate_pct"), PCT_FMT, "exit_cap_rate",
         "no exit cap rate on the deal's returns engine output"),
        ("Entry Cap (Y1 UW)", inv.get("entry_cap_rate_year1_uw"), PCT_FMT, "entry_cap_rate",
         "no Year 1 NOI or purchase price to derive the entry cap"),
    ]
    benchmark_note = debt.get("benchmark_note")
    for label, val, fmt, concept, missing in metrics:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        _write_value(ws, row, 2, val, fmt, concept=concept, missing=missing)
        if label in ("Loan Rate", "Year 1 DSCR") and benchmark_note:
            _write_note(ws, row, 4, str(benchmark_note))
        row += 1

    # ── Sections Included — conditional list ────────────────────────
    row += 1
    ws.merge_cells(f"A{row}:F{row}")
    ws.cell(row=row, column=1, value="SECTIONS INCLUDED")
    _style_header_row(ws, row, 6)
    row += 1
    for name in sections:
        ws.cell(row=row, column=1, value=name).font = Font(size=10)
        row += 1

    _autosize(ws, [28, 30, 4, 22, 22, 4])
    _freeze_top(ws, row=2)


def _build_assumptions(wb: Workbook, model: dict[str, Any]) -> None:
    """Assumptions sheet — every value as the payload states it.

    A6 / C9: an assumption the deal does not carry renders as
    ``not available: <reason>`` naming the field, never 0 / 0% / $0. A whole
    section whose engine did not run (no refinance modelled, no debt) is one
    row saying so rather than a column of placeholders. B9: when the debt
    figures ride on the engine's flat SOFR default, the Senior Debt section
    prints the same provenance note the Debt tab shows.
    """
    ws = wb.create_sheet("Assumptions")
    inv = model.get("investment_engine") or {}
    debt = model.get("debt_engine") or {}
    refi = model.get("refi_engine") or {}
    ret = model.get("returns_engine") or {}

    ws["A1"] = "Assumption"
    ws["B1"] = "Value"
    ws["C1"] = "Unit / Notes"
    _style_header_row(ws, 1, 3)

    def item(label: str, src: dict[str, Any], key: str, fmt: str, note: str, concept: str, engine: str):
        return (label, src.get(key), fmt, note, concept, f"no {label.lower()} on the deal's {engine} output")

    sections: list[tuple[str, dict[str, Any], str, list[tuple[Any, ...]]]] = [
        (
            "Acquisition",
            inv,
            "no capital engine output on the deal's canonical run",
            [
                item("Purchase Price", inv, "purchase_price_usd", USD_FMT, "USD", "purchase_price", "capital engine"),
                item("Price / Key", inv, "price_per_key_usd", USD_FMT, "USD/key", "price_per_key", "capital engine"),
                item("Closing Costs", inv, "closing_costs_usd", USD_FMT, "USD", "closing_costs", "capital engine"),
                item("Renovation Budget", inv, "renovation_budget_usd", USD_FMT, "PIP / capex", "renovation_budget", "capital engine"),
                item("Renovation / Key", inv, "renovation_per_key_usd", USD_FMT, "USD/key", "renovation_per_key", "capital engine"),
                item("Soft Costs", inv, "soft_costs_usd", USD_FMT, "", "soft_costs", "capital engine"),
                item("Contingency", inv, "contingency_usd", USD_FMT, "", "contingency", "capital engine"),
                item("Working Capital", inv, "working_capital_usd", USD_FMT, "", "working_capital", "capital engine"),
                item("Loan Costs", inv, "loan_costs_usd", USD_FMT, "", "loan_costs", "capital engine"),
                item("Total Capital", inv, "total_capital_usd", USD_FMT, "All-in", "total_capital", "capital engine"),
                item("Total Capital / Key", inv, "total_capital_per_key_usd", USD_FMT, "", "total_capital_per_key", "capital engine"),
                item("Entry Cap (T-12)", inv, "entry_cap_rate_t12", PCT_FMT, "", "entry_cap_rate_t12", "T-12 / capital engine"),
                item("Entry Cap (Y1 UW)", inv, "entry_cap_rate_year1_uw", PCT_FMT, "", "entry_cap_rate", "capital engine"),
                item("Year 1 Yield on Cost", inv, "year1_yield_on_cost", PCT_FMT, "", "yield_on_cost", "capital engine"),
            ],
        ),
        (
            "Senior Debt",
            debt,
            "no debt engine output on the deal's canonical run",
            [
                item("Loan Amount", debt, "loan_amount_usd", USD_FMT, "", "loan_amount", "debt engine"),
                item("LTV (cost)", debt, "ltv_cost", PCT_FMT, "", "ltv_cost", "debt engine"),
                item("LTV (value)", debt, "ltv_value", PCT_FMT, "", "ltv", "debt engine"),
                item("LTC", debt, "ltc", PCT_FMT, "", "ltc", "debt engine"),
                item("Interest Rate", debt, "interest_rate_pct", PCT_FMT, "", "interest_rate", "debt engine"),
                item("Amortization (yrs)", debt, "amortization_years", INT_FMT, "", "amortization_years", "debt engine"),
                item("Term (yrs)", debt, "term_years", INT_FMT, "", "term_years", "debt engine"),
                item("Annual Debt Service", debt, "annual_debt_service_usd", USD_FMT, "", "annual_debt_service", "debt engine"),
                item("Year 1 DSCR", debt, "year1_dscr", MULT_FMT, "", "dscr", "debt engine"),
                item("Year 1 Debt Yield", debt, "year1_debt_yield", PCT_FMT, "", "debt_yield", "debt engine"),
                item("IO Period (months)", debt, "interest_only_period_months", INT_FMT, "", "interest_only_months", "debt engine"),
            ],
        ),
        (
            "Refinance",
            refi,
            "no refinance modelled on the deal's canonical run",
            [
                item("Refi Year", refi, "refi_year", INT_FMT, "", "refi_year", "debt engine"),
                item("Refi LTV", refi, "refi_ltv", PCT_FMT, "", "refi_ltv", "debt engine"),
                item("Refi Rate", refi, "refi_rate_pct", PCT_FMT, "", "refi_rate", "debt engine"),
                item("Refi Term (yrs)", refi, "refi_term_years", INT_FMT, "", "refi_term_years", "debt engine"),
                item("Refi Amort (yrs)", refi, "refi_amortization_years", INT_FMT, "", "refi_amortization_years", "debt engine"),
                item("Refi Proceeds", refi, "refi_proceeds_usd", USD_FMT, "", "refi_proceeds", "debt engine"),
                item("Cash Out", refi, "cash_out_to_equity_usd", USD_FMT, "", "refi_cash_out", "debt engine"),
            ],
        ),
        (
            "Reversion / Returns",
            ret,
            "no returns engine output on the deal's canonical run",
            [
                item("Hold (yrs)", ret, "hold_years", INT_FMT, "", "hold_years", "returns engine"),
                item("Exit Cap Rate", ret, "exit_cap_rate_pct", PCT_FMT, "", "exit_cap_rate", "returns engine"),
                item("Terminal NOI", ret, "terminal_noi_usd", USD_FMT, "", "terminal_noi", "returns engine"),
                item("Gross Sale Price", ret, "gross_sale_price_usd", USD_FMT, "", "gross_sale_price", "returns engine"),
                item("Selling Costs", ret, "selling_costs_usd", USD_FMT, "", "selling_costs", "returns engine"),
                item("Net Sale Proceeds", ret, "net_sale_proceeds_usd", USD_FMT, "", "net_proceeds", "returns engine"),
            ],
        ),
    ]

    row = 2
    for section_name, src, section_missing, items in sections:
        ws.cell(row=row, column=1, value=section_name)
        _style_subhead_row(ws, row, 3)
        row += 1
        if not src:
            ws.cell(row=row, column=1, value=section_name)
            _write_value(
                ws, row, 2, None, None,
                concept=section_name.lower().replace(" ", "_"),
                missing=section_missing,
                code=ReasonCode.ENGINE_SKIPPED,
            )
            row += 2
            continue
        for label, val, fmt, note, concept, missing in items:
            ws.cell(row=row, column=1, value=label)
            _write_value(ws, row, 2, val, fmt, concept=concept, missing=missing)
            ws.cell(row=row, column=3, value=note)
            row += 1
        if section_name == "Senior Debt" and src.get("benchmark_note"):
            ws.cell(row=row, column=1, value="Benchmark rate basis")
            _write_note(ws, row, 2, str(src["benchmark_note"]))
            row += 1
        row += 1

    _autosize(ws, [32, 22, 30])
    _freeze_top(ws, row=2)


def _su_table(
    ws: Worksheet,
    row: int,
    *,
    heading: str,
    items: list[dict[str, Any]],
    total_label: str,
    concept: str,
    what: str,
) -> int:
    """Write one Sources / Uses block starting at ``row``; return the next row.

    Lines are the capital engine's own; a block with no lines is one sentence,
    and the total / shares are stated only when every line has an amount.
    """
    ws.cell(row=row, column=1, value=heading)
    ws.cell(row=row, column=2, value="Amount")
    ws.cell(row=row, column=3, value="% of Total")
    _style_header_row(ws, row, 3)
    row += 1
    lines = [x for x in items if isinstance(x, dict) and not x.get("total")]
    if not lines:
        _write_value(
            ws, row, 1, None, None, concept=concept,
            missing=f"no {what} on the deal's capital engine output",
            code=ReasonCode.ENGINE_SKIPPED,
        )
        return row + 1
    amounts = [_as_float(x.get("amount")) for x in lines]
    total = _sum_strict(*amounts)
    first = row
    for x, amt in zip(lines, amounts, strict=True):
        ws.cell(row=row, column=1, value=str(x.get("label", "")))
        _write_value(
            ws, row, 2, amt, USD_FMT, concept=concept,
            missing=f"{x.get('label', what)} line carries no amount",
        )
        pct = _as_float(x.get("pct"))
        if pct is None and total and amt is not None:
            pct = amt / total
        _write_value(
            ws, row, 3, pct, PCT_FMT, concept=concept,
            missing=f"{what} share needs every line's amount",
        )
        row += 1
    ws.cell(row=row, column=1, value=total_label)
    if total is not None:
        c = ws.cell(row=row, column=2, value=f"=SUM(B{first}:B{row - 1})")
        c.number_format = USD_FMT
        ws.cell(row=row, column=3, value=1.0).number_format = PCT_FMT
    else:
        _write_value(
            ws, row, 2, None, None, concept=concept,
            missing=f"a {what} line has no amount, so the total is not stated",
        )
    _style_total_row(ws, row, 3)
    return row + 1


def _build_sources_uses(wb: Workbook, model: dict[str, Any]) -> None:
    """Sources & Uses — the capital engine's line items, nothing rebuilt
    from scalar fields when they are absent."""
    ws = wb.create_sheet("Sources & Uses")
    row = _su_table(
        ws, 1, heading="SOURCES", items=model.get("sources") or [],
        total_label="Total Sources", concept="sources", what="sources",
    )
    row += 2
    _su_table(
        ws, row, heading="USES", items=model.get("uses") or [],
        total_label="Total Uses", concept="uses", what="uses",
    )
    _autosize(ws, [28, 22, 14])
    _freeze_top(ws, row=2)


def _build_proforma(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Operating Proforma")
    pl = model.get("p_and_l_engine_proforma", {})
    lines = pl.get("lines", [])

    headers = ["Line Item", "Year 1", "Year 2", "Year 3", "Year 4", "Year 5", "CAGR"]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    _style_header_row(ws, 1, len(headers))

    if not lines:
        _write_value(
            ws, 2, 1, None, None, concept="proforma",
            missing="no revenue / expense engine output on the deal's canonical run",
            code=ReasonCode.ENGINE_SKIPPED,
        )
    for r, line in enumerate(lines, start=2):
        ws.cell(row=r, column=1, value=line["label"])
        for i, key in enumerate(["y1", "y2", "y3", "y4", "y5"], start=2):
            v = _as_float(line.get(key))
            _write_value(
                ws, r, i, None if v is None else v * 1000, USD_FMT,
                concept=f"proforma.{line['label']}",
                missing=f"Year {i - 1} not projected on the deal's run",
            )
        if line.get("cagr") is not None:
            c = ws.cell(row=r, column=7, value=line["cagr"])
            c.number_format = PCT_FMT
        if line.get("bold"):
            _style_total_row(ws, r, len(headers))

    period_row = len(lines) + 3
    ws.cell(row=period_row, column=1, value="Period:").font = Font(italic=True)
    ws.cell(row=period_row, column=2, value=pl.get("year1_period", "")).font = Font(italic=True)
    ws.cell(row=period_row + 1, column=1, value="Note: figures in USD (whole dollars).").font = Font(italic=True, color="6B7280")

    _autosize(ws, [30, 16, 16, 16, 16, 16, 12])
    _freeze_top(ws, row=2)


def _build_debt_schedule(wb: Workbook, model: dict[str, Any]) -> None:
    """Debt Schedule — the debt engine's own month-by-month schedule.

    The sheet prints ``debt_engine.monthly_schedule`` (interest / principal /
    payment / ending balance per month) exactly as the engine ran it. It
    never re-derives a schedule from headline terms, and never assumes a
    term, amortization or IO period the payload did not state: without the
    engine schedule the sheet says so. B9: the flat-SOFR provenance note is
    printed above the schedule when the rate rides on it.
    """
    ws = wb.create_sheet("Debt Schedule")
    debt = model.get("debt_engine") or {}

    headers = ["Month", "Beg Balance", "Payment", "Interest", "Principal", "End Balance"]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    _style_header_row(ws, 1, len(headers))

    schedule = debt.get("monthly_schedule")
    rows = [m for m in (schedule or []) if isinstance(m, dict) and m.get("month") is not None]
    if not rows:
        _write_value(
            ws, 2, 1, None, None, concept="debt.monthly_schedule",
            missing="no monthly debt schedule on the deal's debt engine output",
            code=ReasonCode.ENGINE_SKIPPED,
        )
        _autosize(ws, [8, 18, 16, 16, 16, 18])
        _freeze_top(ws, row=2)
        return

    row = 2
    note = debt.get("benchmark_note")
    if note:
        _write_note(ws, row, 1, str(note))
        row += 1

    first = row
    loan = _as_float(debt.get("loan_amount_usd"))
    prev_end: float | None = loan
    for m in rows:
        interest = _as_float(m.get("interest"))
        principal = _as_float(m.get("principal"))
        payment = _as_float(m.get("payment"))
        end = _as_float(m.get("ending_balance"))
        # Beginning balance is the prior month's ending balance (the loan
        # amount for month 1) — both engine figures, nothing solved here.
        beg = prev_end if prev_end is not None else (
            _sum_strict(*[end, principal])
        )
        ws.cell(row=row, column=1, value=int(m["month"])).number_format = INT_FMT
        _write_value(ws, row, 2, beg, USD_FMT, concept="debt.schedule.beg_balance",
                     missing="beginning balance not derivable from the engine schedule")
        _write_value(ws, row, 3, payment, USD_FMT, concept="debt.schedule.payment",
                     missing="payment missing on the engine schedule")
        _write_value(ws, row, 4, interest, USD_FMT, concept="debt.schedule.interest",
                     missing="interest missing on the engine schedule")
        _write_value(ws, row, 5, principal, USD_FMT, concept="debt.schedule.principal",
                     missing="principal missing on the engine schedule")
        _write_value(ws, row, 6, end, USD_FMT, concept="debt.schedule.end_balance",
                     missing="ending balance missing on the engine schedule")
        prev_end = end
        row += 1
    last = row - 1

    ws.cell(row=row, column=1, value="Totals").font = Font(bold=True)
    for col in ("C", "D", "E"):
        c = ws.cell(row=row, column=ord(col) - ord("A") + 1, value=f"=SUM({col}{first}:{col}{last})")
        c.number_format = USD_FMT
    _style_total_row(ws, row, len(headers))

    _autosize(ws, [8, 18, 16, 16, 16, 18])
    _freeze_top(ws, row=2)


def _build_returns(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Returns")
    ret = model.get("returns_engine") or {}
    cf = model.get("cash_flow_engine") or {}
    debt = model.get("debt_engine") or {}

    ws["A1"] = "Metric"
    ws["B1"] = "Value"
    _style_header_row(ws, 1, 2)

    items = [
        ("Hold Period (yrs)", ret.get("hold_years"), INT_FMT, "hold_years"),
        ("Exit Cap Rate", ret.get("exit_cap_rate_pct"), PCT_FMT, "exit_cap_rate"),
        ("Terminal NOI", ret.get("terminal_noi_usd"), USD_FMT, "terminal_noi"),
        ("Gross Sale Price", ret.get("gross_sale_price_usd"), USD_FMT, "gross_sale_price"),
        ("Selling Costs", ret.get("selling_costs_usd"), USD_FMT, "selling_costs"),
        ("Net Sale Proceeds", ret.get("net_sale_proceeds_usd"), USD_FMT, "net_proceeds"),
        ("Levered IRR", ret.get("levered_irr"), PCT_FMT, "levered_irr"),
        ("Unlevered IRR", ret.get("unlevered_irr"), PCT_FMT, "unlevered_irr"),
        ("Equity Multiple", ret.get("equity_multiple"), MULT_FMT, "equity_multiple"),
        ("Year 1 Cash-on-Cash", ret.get("year1_cash_on_cash"), PCT_FMT, "year_one_coc"),
        ("Average Cash-on-Cash", ret.get("avg_cash_on_cash"), PCT_FMT, "avg_coc"),
    ]
    row = 2
    for label, val, fmt, concept in items:
        ws.cell(row=row, column=1, value=label)
        _write_value(
            ws, row, 2, val, fmt, concept=concept,
            missing=f"no {label.lower()} on the deal's returns engine output",
        )
        row += 1

    benchmark_note = debt.get("benchmark_note")
    for label, val, fmt, concept in (
        ("Year 1 DSCR", debt.get("year1_dscr"), MULT_FMT, "dscr"),
        ("Year 1 Debt Yield", debt.get("year1_debt_yield"), PCT_FMT, "debt_yield"),
    ):
        ws.cell(row=row, column=1, value=label)
        _write_value(
            ws, row, 2, val, fmt, concept=concept,
            missing=f"no {label.lower()} on the deal's debt engine output",
        )
        if benchmark_note:
            _write_note(ws, row, 3, str(benchmark_note))
        row += 1
    row += 1

    # FON-118 — returns by exit year: the same projection sold at each
    # year-end. A row the engine did not price states why in its own words;
    # a payload without the table (an old run) says so, never a 0.
    ncols = len(EXIT_SCHEDULE_COLUMNS)
    ws.cell(row=row, column=1, value=EXIT_SCHEDULE_TITLE.upper())
    _style_subhead_row(ws, row, ncols)
    row += 1
    for j, header in enumerate(EXIT_SCHEDULE_COLUMNS, start=1):
        ws.cell(row=row, column=j, value=header)
    _style_header_row(ws, row, ncols)
    row += 1
    schedule = ret.get("exit_schedule")
    if not isinstance(schedule, list) or not schedule:
        _write_note(
            ws, row, 1,
            unavailable(
                ReasonCode.STALE_RUN if ret else ReasonCode.ENGINE_SKIPPED,
                EXIT_SCHEDULE_STALE_NOTE if ret else EXIT_SCHEDULE_NO_RETURNS_NOTE,
                "returns.exit_schedule",
            ),
        )
        row += 1
    else:
        for i, r in enumerate(schedule):
            if not isinstance(r, dict):
                continue
            concept = f"returns.exit_schedule[{i}]"
            ws.cell(row=row, column=1, value=exit_row_label(r))
            if r.get("status") != "computed":
                code = coerce_reason(r.get("reason"))
                _write_note(
                    ws, row, 2,
                    unavailable(
                        code,
                        str(r.get("reason_detail") or REASON_META[code]["explanation"]),
                        concept,
                    ),
                )
                row += 1
                continue
            for col, key, fmt in (
                (2, "terminal_noi_usd", USD_FMT),
                (3, "gross_sale_price_usd", USD_FMT),
                (4, "loan_payoff_usd", USD_FMT),
                (5, "net_sale_proceeds_usd", USD_FMT),
                (6, "unlevered_irr", PCT_FMT),
                (7, "levered_irr", PCT_FMT),
                (8, "equity_multiple", MULT_FMT),
            ):
                _write_value(
                    ws, row, col, _as_float(r.get(key)), fmt,
                    concept=f"{concept}.{key}",
                    missing=f"no {key.replace('_usd', '').replace('_', ' ')} on the exit-year row",
                )
            basis = r.get("exit_noi_period_label")
            if basis:
                _write_note(ws, row, ncols + 1, str(basis))
            row += 1
    row += 1

    ws.cell(row=row, column=1, value="CASH FLOW WATERFALL ($)")
    _style_subhead_row(ws, row, 6)
    row += 1
    ws.cell(row=row, column=1, value="Year")
    for i in range(1, 6):
        ws.cell(row=row, column=i + 1, value=f"Y{i}")
    _style_header_row(ws, row, 6)
    row += 1

    ws.cell(row=row, column=1, value="Cash Flow After Debt")
    cfs = [_as_float(cf.get(f"year{y}_cf_after_debt_usd")) for y in range(1, 6)]
    for i, v in enumerate(cfs, start=2):
        _write_value(
            ws, row, i, v, USD_FMT, concept="cash_flow_after_debt",
            missing=f"Year {i - 1} cash flow after debt not on the deal's run",
        )
    row += 1

    # Cumulative only through the years that are stated — a gap ends the sum.
    ws.cell(row=row, column=1, value="Cumulative")
    cumulative = 0.0
    for i, v in enumerate(cfs, start=2):
        if v is None:
            _write_value(
                ws, row, i, None, USD_FMT, concept="cash_flow_cumulative",
                missing=f"Year {i - 1} cash flow not stated, so the cumulative stops",
            )
            break
        cumulative += v
        c = ws.cell(row=row, column=i, value=cumulative)
        c.number_format = USD_FMT
    _style_total_row(ws, row, 6)

    _autosize(ws, [28, 18, 18, 18, 18, 18])
    _freeze_top(ws, row=2)


def _build_partnership(wb: Workbook, model: dict[str, Any]) -> None:
    """Partnership sheet — the partnership engine's returns and the waterfall
    terms the run used.

    D5: total equity is written only when the payload states it (both
    partners' equity present); otherwise the row names the missing side.
    The old "Distribution Per Year (estimate)" table — cash flow split by
    equity share, ignoring the promote — was a figure the engine never
    produced and is gone; the sheet prints each partner's total distributions
    as the engine computed them.
    """
    ws = wb.create_sheet("Partnership")
    p = model.get("partnership_engine") or {}

    ws["A1"] = "Partnership Structure"
    _style_header_row(ws, 1, 3)
    row = 2
    if not p:
        _write_value(
            ws, row, 1, None, None, concept="partnership",
            missing="no partnership engine output on the deal's canonical run",
            code=ReasonCode.ENGINE_SKIPPED,
        )
        _autosize(ws, [30, 18, 18])
        _freeze_top(ws, row=2)
        return

    ws.cell(row=row, column=1, value="Structure").font = Font(bold=True)
    ws.cell(row=row, column=2, value=p.get("structure") or refuse(
        ReasonCode.NO_SOURCE, "no structure on the partnership engine output", "partnership.structure"
    ))
    row += 1
    for label, key, fmt, concept, missing in (
        ("LP Equity", "lp_equity_usd", USD_FMT, "partnership.lp_equity",
         "no LP contributed equity on the partnership engine output"),
        ("GP Equity", "gp_equity_usd", USD_FMT, "partnership.gp_equity",
         "no GP contributed equity on the partnership engine output"),
        ("Total Equity", "total_equity_usd", USD_FMT, "partnership.total_equity",
         str(p.get("total_equity_missing") or "total equity not stated on the partnership engine output")),
        ("LP Preferred Return", "lp_pref_pct", PCT_FMT, "partnership.pref_rate",
         "no preferred return on the partnership engine inputs"),
    ):
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        _write_value(ws, row, 2, p.get(key), fmt, concept=concept, missing=missing)
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="Waterfall Tiers")
    _style_header_row(ws, row, 3)
    row += 1
    ws.cell(row=row, column=1, value="Tier")
    ws.cell(row=row, column=2, value="IRR Hurdle")
    ws.cell(row=row, column=3, value="GP Promote")
    _style_subhead_row(ws, row, 3)
    row += 1
    tiers = [t for t in (p.get("waterfall_tiers") or []) if isinstance(t, dict)]
    if not tiers:
        _write_value(
            ws, row, 1, None, None, concept="partnership.waterfall",
            missing="no waterfall tiers on the partnership engine inputs",
        )
        row += 1
    for t in tiers:
        ws.cell(row=row, column=1, value=str(t.get("label") or refuse(
            ReasonCode.NO_SOURCE, "waterfall tier has no label", "partnership.waterfall.label"
        )))
        _write_value(ws, row, 2, t.get("hurdle_rate"), PCT_FMT, concept="partnership.waterfall.hurdle",
                     missing="tier carries no IRR hurdle")
        _write_value(ws, row, 3, t.get("gp_split"), PCT_FMT, concept="partnership.waterfall.gp_split",
                     missing="tier carries no GP split")
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="Distributions Summary")
    _style_header_row(ws, row, 3)
    row += 1
    for label, key, fmt, concept in (
        ("LP IRR (after promote)", "lp_irr_after_promote", PCT_FMT, "partnership.lp_irr"),
        ("GP IRR (after promote)", "gp_irr_after_promote", PCT_FMT, "partnership.gp_irr"),
        ("LP Equity Multiple", "lp_equity_multiple", MULT_FMT, "partnership.lp_multiple"),
        ("GP Equity Multiple", "gp_equity_multiple", MULT_FMT, "partnership.gp_multiple"),
        ("LP Distributions (total)", "lp_distributions_usd", USD_FMT, "partnership.lp_distributions"),
        ("GP Distributions (total)", "gp_distributions_usd", USD_FMT, "partnership.gp_distributions"),
    ):
        ws.cell(row=row, column=1, value=label)
        _write_value(
            ws, row, 2, p.get(key), fmt, concept=concept,
            missing=f"no {label.lower()} on the partnership engine output",
        )
        row += 1

    _autosize(ws, [30, 18, 18])
    _freeze_top(ws, row=2)


def _build_variance(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Variance")
    flags = model.get("variance_flags", []) or []

    # FON-54a: the live payload rows also carry the analyst's persisted
    # diligence status (``diligence_status`` / ``diligence_note`` — from the
    # same ``memo_diligence`` override the IC Memo tab writes) and the raw
    # extractor paths that were consolidated into the row (``raw_fields``).
    # Both columns are blank for rows that don't carry them (fixture rows).
    headers = [
        "Flag ID", "Severity", "Metric", "Broker / Value", "T-12 / Threshold",
        "Variance", "Action", "Diligence", "Technical detail",
    ]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    _style_header_row(ws, 1, len(headers))

    severity_fills = {
        "CRITICAL": PatternFill("solid", fgColor="FEE2E2"),
        "WARN": PatternFill("solid", fgColor="FEF3C7"),
        "INFO": PatternFill("solid", fgColor="DBEAFE"),
    }

    for r, f in enumerate(flags, start=2):
        ws.cell(row=r, column=1, value=f.get("flag_id", ""))
        sev = f.get("severity", "INFO")
        sev_cell = ws.cell(row=r, column=2, value=sev)
        sev_cell.fill = severity_fills.get(sev, severity_fills["INFO"])
        sev_cell.font = Font(bold=True)
        ws.cell(row=r, column=3, value=f.get("metric", ""))
        if "broker_value" in f:
            ws.cell(row=r, column=4, value=f["broker_value"])
        elif "value" in f:
            ws.cell(row=r, column=4, value=f["value"])
        if "t12_value" in f:
            ws.cell(row=r, column=5, value=f["t12_value"])
        elif "threshold_max" in f:
            ws.cell(row=r, column=5, value=f"{f.get('threshold_min','')}–{f.get('threshold_max','')}")
        if "variance_pct" in f:
            v = ws.cell(row=r, column=6, value=f["variance_pct"])
            v.number_format = PCT_FMT
        elif "variance_pct_pts" in f:
            ws.cell(row=r, column=6, value=f"{f['variance_pct_pts']} pts")
        ws.cell(row=r, column=7, value=f.get("recommended_action", ""))
        ws.cell(row=r, column=7).alignment = Alignment(wrap_text=True, vertical="top")
        if f.get("diligence_status"):
            dil = str(f["diligence_status"])
            if f.get("diligence_note"):
                dil = f"{dil} — {f['diligence_note']}"
            ws.cell(row=r, column=8, value=dil)
            ws.cell(row=r, column=8).alignment = Alignment(wrap_text=True, vertical="top")
        tech_parts: list[str] = []
        if f.get("rule_id"):
            tech_parts.append(f"rule {f['rule_id']}")
        if f.get("raw_fields"):
            tech_parts.append("fields " + ", ".join(str(p) for p in f["raw_fields"]))
        if tech_parts:
            ws.cell(row=r, column=9, value=" · ".join(tech_parts))
            ws.cell(row=r, column=9).alignment = Alignment(wrap_text=True, vertical="top")

    _autosize(ws, [10, 12, 22, 18, 18, 14, 60, 22, 48])
    _freeze_top(ws, row=2)


def _build_market_comps(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Market Comps")
    comps = model.get("market_comps", []) or []

    headers = ["Property", "Keys", "Date", "Price", "Per Key", "Cap Rate", "Buyer"]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    _style_header_row(ws, 1, len(headers))

    for r, c in enumerate(comps, start=2):
        ws.cell(row=r, column=1, value=c.get("name", ""))
        ws.cell(row=r, column=2, value=c.get("keys")).number_format = INT_FMT
        ws.cell(row=r, column=3, value=c.get("date", ""))
        ws.cell(row=r, column=4, value=c.get("price", ""))
        ws.cell(row=r, column=5, value=c.get("per_key", c.get("perKey", "")))
        ws.cell(row=r, column=6, value=c.get("cap", ""))
        ws.cell(row=r, column=7, value=c.get("buyer", ""))

    _autosize(ws, [32, 8, 14, 14, 14, 12, 26])
    _freeze_top(ws, row=2)


# ──────────────────────── Wave 2/3 sheet builders ──────────────────────


def _build_revenue_mix(wb: Workbook, segments_by_year: list[dict[str, Any]] | None) -> bool:
    """Segment-by-year revenue mix table (5 segments × hold years).

    Cols per segment: Mix %, ADR, Gross Rev, Channel Cost %, Net Rev.
    Includes a Total row and an OTA-flag highlight when any segment
    carries ≥15% channel cost. Returns True iff the sheet rendered.
    """
    if not segments_by_year:
        return False

    ws = wb.create_sheet("Revenue Mix")

    headers = [
        "Segment", "Year", "Mix %", "ADR", "Gross Revenue",
        "Channel Cost %", "Net Revenue",
    ]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    _style_header_row(ws, 1, len(headers))

    ota_fill = PatternFill("solid", fgColor="FEF3C7")  # amber for OTA-heavy
    row = 2
    total_gross: list[float | None] = []
    total_net: list[float | None] = []
    for year_block in segments_by_year:
        year = year_block.get("year")
        breakdown = (
            year_block.get("segment_breakdown")
            or year_block.get("segments")
            or []
        )
        # Subhead for this year
        ws.cell(row=row, column=1, value=f"Year {year}")
        _style_subhead_row(ws, row, len(headers))
        row += 1
        year_gross: list[float | None] = []
        year_net: list[float | None] = []
        for seg in breakdown:
            ws.cell(
                row=row,
                column=1,
                value=str(seg["name"])
                if "name" in seg
                else refuse(
                    ReasonCode.NO_SOURCE, "revenue segment has no name", "segment.name"
                ),
            )
            ws.cell(row=row, column=2, value=year).number_format = INT_FMT
            gross_rev = _as_float(seg.get("gross_revenue"))
            channel = _as_float(seg.get("channel_cost_pct"))
            net_rev = _as_float(seg.get("net_revenue"))
            for col, val, fmt, what in (
                (3, _as_float(seg.get("mix_pct")), PCT_FMT, "mix %"),
                (4, _as_float(seg.get("adr")), USD_FMT, "ADR"),
                (5, gross_rev, USD_FMT, "gross revenue"),
                (6, channel, PCT_FMT, "channel cost %"),
                (7, net_rev, USD_FMT, "net revenue"),
            ):
                _write_value(
                    ws, row, col, val, fmt, concept=f"segment.{what}",
                    missing=f"segment carries no {what}",
                )
            # Highlight OTA-heavy segments
            if channel is not None and channel >= 0.15:
                for col in range(1, len(headers) + 1):
                    ws.cell(row=row, column=col).fill = ota_fill
            year_gross.append(gross_rev)
            year_net.append(net_rev)
            row += 1
        # Year total — only when every segment's figure is stated.
        ws.cell(row=row, column=1, value=f"Total Y{year}")
        yg, yn = _sum_strict(*year_gross), _sum_strict(*year_net)
        _write_value(ws, row, 5, yg, USD_FMT, concept="segment.gross_total",
                     missing="a segment carries no gross revenue")
        _write_value(ws, row, 7, yn, USD_FMT, concept="segment.net_total",
                     missing="a segment carries no net revenue")
        _style_total_row(ws, row, len(headers))
        total_gross.append(yg)
        total_net.append(yn)
        row += 1

    # Grand total across all years
    ws.cell(row=row, column=1, value="GRAND TOTAL")
    _write_value(ws, row, 5, _sum_strict(*total_gross), USD_FMT, concept="segment.gross_total",
                 missing="a year's gross revenue total is not stated")
    _write_value(ws, row, 7, _sum_strict(*total_net), USD_FMT, concept="segment.net_total",
                 missing="a year's net revenue total is not stated")
    _style_total_row(ws, row, len(headers))

    _autosize(ws, [24, 8, 12, 14, 18, 16, 18])
    _freeze_top(ws, row=2)
    return True


def _build_renovation_plan(wb: Workbook, pip: dict[str, Any] | None) -> bool:
    """PIP displacement summary sheet: closure strategy, schedule, Y1
    displacement $, Y2 recovery curve.

    B4: the former static "Brand Recovery Multipliers" table (five brands'
    post-renovation RevPAR index and recovery months) had no source in code
    or data and is not printed.
    """
    if not pip:
        return False

    ws = wb.create_sheet("Renovation Plan")

    _closure = pip.get("closure_strategy") or ""
    strategy_label = {
        "rolling": "Rolling",
        "full_closure": "Full Closure",
        "wing_by_wing": "Wing-by-Wing",
    }.get(_closure) or (
        str(_closure)
        if _closure
        else refuse(
            ReasonCode.NO_SOURCE,
            "PIP spec carries no closure strategy",
            "pip.closure_strategy",
        )
    )

    ws["A1"] = "Renovation Plan (PIP Displacement)"
    _style_header_row(ws, 1, 4)

    row = 2
    ws.cell(row=row, column=1, value="Closure Strategy").font = Font(bold=True)
    ws.cell(row=row, column=2, value=strategy_label)
    row += 1
    ws.cell(row=row, column=1, value="Brand").font = Font(bold=True)
    ws.cell(row=row, column=2, value=pip.get("brand") or refuse(
        ReasonCode.NO_SOURCE, "PIP spec carries no brand", "pip.brand"
    ))
    row += 1
    for label, key, fmt, missing in (
        ("Recovery Months", "occupancy_recovery_months", INT_FMT,
         "PIP spec carries no occupancy recovery period"),
        ("RevPAR Index (post-reno)", "revpar_index_post_reno", MULT_FMT,
         "PIP spec carries no post-renovation RevPAR index"),
        ("Y1 Displacement", "y1_displacement_usd", USD_FMT,
         "no Year 1 displacement on the PIP output"),
    ):
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        _write_value(ws, row, 2, _as_float(pip.get(key)), fmt, concept=f"pip.{key}", missing=missing)
        row += 1

    # Month-by-month offline schedule
    row += 1
    ws.cell(row=row, column=1, value="MONTHLY OFFLINE SCHEDULE (Y1)")
    _style_subhead_row(ws, row, 4)
    row += 1
    sched = pip.get("pct_rooms_offline_by_month") or []
    ws.cell(row=row, column=1, value="Month")
    ws.cell(row=row, column=2, value="% Rooms Offline")
    _style_header_row(ws, row, 2)
    row += 1
    for m, pct in enumerate(sched, start=1):
        ws.cell(row=row, column=1, value=f"M{m}").number_format = INT_FMT
        c = ws.cell(row=row, column=2, value=float(pct))
        c.number_format = PCT_FMT
        row += 1

    # Y2 recovery curve
    row += 1
    ws.cell(row=row, column=1, value="Y2 RECOVERY CURVE")
    _style_subhead_row(ws, row, 4)
    row += 1
    ws.cell(row=row, column=1, value="Month")
    ws.cell(row=row, column=2, value="% of Baseline RevPAR")
    _style_header_row(ws, row, 2)
    row += 1
    for i, v in enumerate(pip.get("y2_recovery_curve") or [], start=1):
        ws.cell(row=row, column=1, value=f"M{i}").number_format = INT_FMT
        c = ws.cell(row=row, column=2, value=float(v))
        c.number_format = PCT_FMT
        row += 1

    _autosize(ws, [32, 22, 22, 18])
    _freeze_top(ws, row=2)
    return True


def _build_capital_plan(wb: Workbook, schedule: list[dict[str, Any]] | None) -> bool:
    """Capital Plan — three-bucket capex (PIP / Non-PIP FF&E / ROI).

    Replaces the legacy single capex line in Assumptions. Rows are hold
    years plus a total row. Cell-level provenance comments distinguish
    analyst-override entries from defaults.
    """
    if not schedule:
        return False

    ws = wb.create_sheet("Capital Plan")
    headers = [
        "Year", "PIP", "Non-PIP FF&E", "ROI Investment",
        "ROI NOI Lift", "Total Capex",
    ]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    _style_header_row(ws, 1, len(headers))

    cols: dict[str, list[float | None]] = {
        "pip_usd": [], "non_pip_usd": [], "roi_investment_usd": [],
        "roi_noi_lift_usd": [], "total_capex_usd": [],
    }
    row = 2
    for y in schedule:
        year_label = y.get("year", "?")
        pip_v = _as_float(y.get("pip_usd"))
        non_v = _as_float(y.get("non_pip_usd"))
        roi_inv = _as_float(y.get("roi_investment_usd"))
        roi_lift = _as_float(y.get("roi_noi_lift_usd"))
        total = _as_float(y.get("total_capex_usd"))
        if total is None:
            total = _sum_strict(*[pip_v, non_v, roi_inv])

        ws.cell(row=row, column=1, value=f"Year {year_label}")
        for col, key, val, what in (
            (2, "pip_usd", pip_v, "PIP"),
            (3, "non_pip_usd", non_v, "non-PIP FF&E"),
            (4, "roi_investment_usd", roi_inv, "ROI investment"),
            (5, "roi_noi_lift_usd", roi_lift, "ROI NOI lift"),
            (6, "total_capex_usd", total, "total capex"),
        ):
            cols[key].append(val)
            _write_value(ws, row, col, val, USD_FMT, concept=f"capex.{key}",
                         missing=f"Year {year_label} {what} not stated on the capital plan")

        # Cell-level provenance note via openpyxl Comment, if set on
        # the schedule row.
        note = y.get("override_note")
        if note:
            ws.cell(row=row, column=2).comment = Comment(
                f"Override: {note}", "Fondok",
            )
        row += 1

    # Column totals only when every year's bucket is stated.
    ws.cell(row=row, column=1, value="Total")
    for col, key in ((2, "pip_usd"), (3, "non_pip_usd"), (4, "roi_investment_usd"),
                     (5, "roi_noi_lift_usd"), (6, "total_capex_usd")):
        _write_value(ws, row, col, _sum_strict(*cols[key]), USD_FMT, concept=f"capex.{key}.total",
                     missing="a year's figure is not stated, so the total is not")
    _style_total_row(ws, row, len(headers))

    _autosize(ws, [12, 16, 18, 18, 18, 18])
    _freeze_top(ws, row=2)
    return True


def _build_op_ratio_provenance(wb: Workbook, op_prov: dict[str, Any] | None) -> bool:
    """Op-ratio provenance sheet — one row per ratio with winning source.

    Cols: Field, Value, Source, Why. Each row is tinted by source
    (T-12 = green, Portfolio = blue, CBRE = amber, HOST = neutral,
    Override = pink).
    """
    if not op_prov:
        return False
    lines = op_prov.get("lines") or []
    note = op_prov.get("note")
    if not lines and not note:
        return False

    ws = wb.create_sheet("Op-Ratio Provenance")
    headers = ["Field", "Value", "Source", "Document / Note"]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    _style_header_row(ws, 1, len(headers))

    row = 2
    # B2 — all-seed (or nothing resolved) is stated in words, never implied
    # by a missing sheet.
    if note:
        ws.cell(row=row, column=1, value="Note").font = Font(bold=True)
        _write_note(ws, row, 2, str(note))
        row += 1
    for line in lines:
        field = str(
            line.get("field")
            or refuse(
                ReasonCode.NO_SOURCE,
                "op-ratio provenance line has no field name",
                "op_ratio.field",
            )
        )
        value = line.get("value")
        source = str(line.get("source") or "seed")
        doc = line.get("document_id") or refuse(
            ReasonCode.NO_DOCUMENT,
            "op-ratio source is not a document on the deal",
            "op_ratio.document_id",
        )

        ws.cell(row=row, column=1, value=field)
        # Value-formatting heuristic — fractions get pct, big numbers get $.
        fmt = PCT_FMT if isinstance(value, (int, float)) and 0 <= value <= 1 else USD_FMT
        _write_value(ws, row, 2, value, fmt, concept="op_ratio.value",
                     missing=f"{field} resolved without a value")
        ws.cell(row=row, column=3, value=_SOURCE_DISPLAY(source))
        ws.cell(row=row, column=4, value=str(doc))

        # Row tint by source
        fill = _SOURCE_ROW_FILL.get(source)
        if fill:
            for col in range(1, len(headers) + 1):
                ws.cell(row=row, column=col).fill = fill
        row += 1

    # Legend
    row += 1
    ws.cell(row=row, column=1, value="Source precedence:").font = Font(bold=True, italic=True)
    ws.cell(row=row, column=2, value="Override → T-12 actuals → Portfolio → CBRE → HOST → Seed")
    ws.cell(row=row, column=2).font = Font(italic=True)

    _autosize(ws, [30, 16, 18, 40])
    _freeze_top(ws, row=2)
    return True


def _max_price_cell(
    ws: Worksheet, row: int, col: int, max_price: dict[str, Any], *, hurdle: str
) -> None:
    """One solved price — only when that hurdle was set AND converged."""
    target = _as_float(max_price.get(f"target_{hurdle}"))
    if target is None:
        _write_value(ws, row, col, None, None, concept=f"max_price.{hurdle}",
                     missing=f"no {hurdle.upper()} target set on this deal")
        return
    status = str(max_price.get(f"{hurdle}_status") or "converged")
    price = _as_float(max_price.get(f"max_price_for_{hurdle}"))
    if status == "converged" and price is not None:
        c = ws.cell(row=row, column=col, value=price)
        c.number_format = USD_FMT
    elif status in SOLVER_STATUS_NOTE:
        _write_note(ws, row, col, SOLVER_STATUS_NOTE[status])
    else:
        _write_value(ws, row, col, None, None, concept=f"max_price.{hurdle}",
                     missing=f"the {hurdle.upper()} search did not converge ({status})",
                     code=ReasonCode.ENGINE_SKIPPED)


def _build_sensitivity_grid(
    wb: Workbook,
    grid: dict[str, Any] | None,
    max_price: dict[str, Any] | None = None,
    pricing_unavailable: str | None = None,
    grid_unavailable: str | None = None,
) -> bool:
    """Pricing Sensitivity — max-price summary + 5×5 IRR grid + 5×5 EM grid.

    Solved only against the deal's own return targets (A3). The max-price
    block is sourced from the solver independently of the grid: an EM-only
    deal gets its solved EM price and, in place of the grid, the stated
    reason (``grid_unavailable``). A deal with no target at all gets one
    sentence (``pricing_unavailable``) and no solver output. IRR grid
    carries a 3-color scale conditional format; DSCR-breach cells get a red
    fill + ``!`` cell comment + red border. EM grid gets its own color scale.
    """
    cells = (grid or {}).get("cells") or []
    cells = [
        c for c in cells
        if isinstance(c, dict)
        and c.get("exit_cap_pct") is not None
        and c.get("noi_multiplier") is not None
    ]
    if not cells and not max_price and not pricing_unavailable and not grid_unavailable:
        return False

    ws = wb.create_sheet("Pricing Sensitivity")
    if not cells and not max_price:
        ws["A1"] = "PRICING TO TARGET"
        _style_header_row(ws, 1, 4)
        _write_note(ws, 2, 1, pricing_unavailable or grid_unavailable or "")
        _autosize(ws, [90])
        return True

    cap_axis = sorted({round(float(c["exit_cap_pct"]), 6) for c in cells}) if cells else []
    width = max(len(cap_axis) + 2, 6)

    # ── Max-Price summary at top (independent of the grid) ──────────
    ws["A1"] = "MAX-PRICE FINDINGS"
    _style_header_row(ws, 1, width)
    row = 2
    if max_price:
        ws.cell(row=row, column=1, value="Max Price for IRR target").font = Font(bold=True)
        _max_price_cell(ws, row, 2, max_price, hurdle="irr")
        ws.cell(row=row, column=4, value="Max Price for EM target").font = Font(bold=True)
        _max_price_cell(ws, row, 5, max_price, hurdle="em")
        row += 1
        ws.cell(row=row, column=1, value="Binding Constraint").font = Font(bold=True)
        ws.cell(
            row=row,
            column=2,
            value=str(
                max_price.get("binding_constraint")
                or refuse(
                    ReasonCode.NO_SOURCE,
                    "max-price solve names no binding constraint",
                    "max_price.binding_constraint",
                )
            ).upper(),
        )
        row += 1
    else:
        _write_value(
            ws, row, 1, None, None, concept="max_price",
            missing="no max-price solve on the export payload",
            code=ReasonCode.ENGINE_SKIPPED,
        )
        row += 1

    if not cells:
        # No grid: the payload says why (EM-only deal, solver error, no
        # cells); a payload with no reason gets a stated refusal, never a
        # guessed one.
        row += 1
        ws.cell(row=row, column=1, value="IRR SENSITIVITY GRID")
        _style_header_row(ws, row, width)
        row += 1
        _write_note(
            ws, row, 1,
            grid_unavailable
            or unavailable(
                ReasonCode.ENGINE_SKIPPED,
                "no IRR sensitivity grid on the export payload",
                "pricing.sensitivity_grid",
            ),
        )
        _autosize(ws, [26] + [14] * (width - 1))
        _freeze_top(ws, row=2)
        return True
    assert grid is not None

    # The deal's own IRR hurdle (the live payload sets it from the
    # Investment Profile); ``None`` means no hurdle to name in the header.
    target_irr = _as_float(grid.get("target_irr"))
    noi_axis = sorted(
        {round(float(c["noi_multiplier"]), 4) for c in cells},
        reverse=True,
    )
    cell_lookup = {
        (
            round(float(c["exit_cap_pct"]), 6),
            round(float(c["noi_multiplier"]), 4),
        ): c
        for c in cells
    }

    # Breakeven ``None`` is the engine's own statement that the crossing is
    # outside the grid — printed as such, never as 0.
    ws.cell(row=row, column=1, value="Breakeven Exit Cap").font = Font(bold=True)
    be_cap = _as_float(grid.get("breakeven_exit_cap_pct"))
    if be_cap is not None:
        ws.cell(row=row, column=2, value=be_cap).number_format = PCT_FMT
    else:
        _write_note(ws, row, 2, "outside grid")
    ws.cell(row=row, column=4, value="Breakeven NOI Multiplier").font = Font(bold=True)
    be_noi = _as_float(grid.get("breakeven_noi_multiplier"))
    if be_noi is not None:
        ws.cell(row=row, column=5, value=be_noi).number_format = MULT_FMT
    else:
        _write_note(ws, row, 5, "outside grid")
    row += 2

    # ── IRR grid header ─────────────────────────────────────────────
    irr_header = (
        f"LEVERED IRR (deal target = {target_irr:.2%})"
        if target_irr is not None
        else "LEVERED IRR (no target levered IRR set on this deal)"
    )
    ws.cell(row=row, column=1, value=irr_header)
    _style_header_row(ws, row, len(cap_axis) + 2)
    row += 1
    ws.cell(row=row, column=1, value="NOI Multiplier \\ Exit Cap")
    for j, cap in enumerate(cap_axis, start=2):
        c = ws.cell(row=row, column=j, value=cap)
        c.number_format = PCT_FMT
        c.font = Font(bold=True)
    _style_subhead_row(ws, row, len(cap_axis) + 1)
    row += 1
    irr_start_row = row
    dscr_red_fill = PatternFill("solid", fgColor="FECACA")
    dscr_red_border = Border(
        left=Side(style="medium", color="DC2626"),
        right=Side(style="medium", color="DC2626"),
        top=Side(style="medium", color="DC2626"),
        bottom=Side(style="medium", color="DC2626"),
    )
    for nm in noi_axis:
        c = ws.cell(row=row, column=1, value=nm)
        c.number_format = MULT_FMT
        c.font = Font(bold=True)
        for j, cap in enumerate(cap_axis, start=2):
            cell_data = cell_lookup.get((cap, nm))
            if cell_data is None:
                ws.cell(row=row, column=j, value=None)
                continue
            irr = _as_float(cell_data.get("levered_irr"))
            _write_value(ws, row, j, irr, PCT_FMT, concept="sensitivity.levered_irr",
                         missing="cell carries no levered IRR")
            cell = ws.cell(row=row, column=j)
            if cell_data.get("breaches_dscr_floor"):
                cell.fill = dscr_red_fill
                cell.border = dscr_red_border
                cell.comment = Comment("! DSCR breach", "Fondok")
        row += 1
    irr_end_row = row - 1
    irr_first_col_letter = get_column_letter(2)
    irr_last_col_letter = get_column_letter(1 + len(cap_axis))
    irr_range = f"{irr_first_col_letter}{irr_start_row}:{irr_last_col_letter}{irr_end_row}"
    _apply_color_scale(ws, irr_range)
    row += 1

    # ── EM grid header ──────────────────────────────────────────────
    ws.cell(row=row, column=1, value="EQUITY MULTIPLE")
    _style_header_row(ws, row, len(cap_axis) + 2)
    row += 1
    ws.cell(row=row, column=1, value="NOI Multiplier \\ Exit Cap")
    for j, cap in enumerate(cap_axis, start=2):
        c = ws.cell(row=row, column=j, value=cap)
        c.number_format = PCT_FMT
        c.font = Font(bold=True)
    _style_subhead_row(ws, row, len(cap_axis) + 1)
    row += 1
    em_start_row = row
    for nm in noi_axis:
        c = ws.cell(row=row, column=1, value=nm)
        c.number_format = MULT_FMT
        c.font = Font(bold=True)
        for j, cap in enumerate(cap_axis, start=2):
            cell_data = cell_lookup.get((cap, nm))
            if cell_data is None:
                continue
            em = _as_float(cell_data.get("equity_multiple"))
            _write_value(ws, row, j, em, MULT_FMT, concept="sensitivity.equity_multiple",
                         missing="cell carries no equity multiple")
            cell = ws.cell(row=row, column=j)
            if cell_data.get("breaches_dscr_floor"):
                cell.fill = dscr_red_fill
                cell.border = dscr_red_border
        row += 1
    em_end_row = row - 1
    em_range = f"{irr_first_col_letter}{em_start_row}:{irr_last_col_letter}{em_end_row}"
    _apply_color_scale(ws, em_range)

    _autosize(ws, [26] + [14] * len(cap_axis))
    _freeze_top(ws, row=2)
    return True


def _build_comparable_sales(wb: Workbook, comp_sales: dict[str, Any] | None) -> bool:
    """Comparable Sales sheet — the comp universe with derivation summary."""
    if not comp_sales:
        return False
    txns = comp_sales.get("transactions") or []
    if not txns:
        return False

    ws = wb.create_sheet("Comparable Sales")

    # ── Summary callouts ────────────────────────────────────────────
    ws["A1"] = "DERIVED EXIT CAP RATE"
    _style_header_row(ws, 1, 6)
    row = 2
    median_cap = comp_sales.get("derived_cap_rate_median")
    weighted_cap = comp_sales.get("derived_cap_rate_weighted")
    method = str(comp_sales.get("derived_cap_rate_method") or "none").lower()
    coverage = str(comp_sales.get("coverage_quality") or "low").lower()

    ws.cell(row=row, column=1, value="Method").font = Font(bold=True)
    ws.cell(
        row=row, column=2,
        value=COMP_CAP_METHOD_DISPLAY.get(method, COMP_CAP_METHOD_DISPLAY["none"]),
    )
    ws.cell(row=row, column=3, value="Coverage").font = Font(bold=True)
    ws.cell(
        row=row, column=4,
        value=COMP_COVERAGE_DISPLAY.get(coverage, coverage.title()),
    )
    row += 1
    # No derivation (fewer than 3 comps) → the cap cells stay BLANK. A 0%
    # here would be a placeholder number, not a model output.
    derived = method != "none"
    ws.cell(row=row, column=1, value="Median Cap").font = Font(bold=True)
    if derived and median_cap is not None:
        c = ws.cell(row=row, column=2, value=float(median_cap) / 100.0)
        c.number_format = PCT_FMT
    ws.cell(row=row, column=3, value="Weighted Cap").font = Font(bold=True)
    if derived and weighted_cap is not None:
        c = ws.cell(row=row, column=4, value=float(weighted_cap) / 100.0)
        c.number_format = PCT_FMT
    row += 2

    # ── Weighting notes callout ─────────────────────────────────────
    ws.cell(row=row, column=1, value="WEIGHTING NOTES")
    _style_subhead_row(ws, row, 6)
    row += 1
    for note in comp_sales.get("weighting_notes") or []:
        ws.cell(row=row, column=1, value=str(note)).alignment = Alignment(wrap_text=True)
        row += 1
    row += 1

    # ── Transactions table ──────────────────────────────────────────
    headers = [
        "Property", "City", "Date", "Keys", "Price",
        "Per Key", "NOI", "Cap %", "Chain", "Excluded?",
    ]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=row, column=i, value=h)
    _style_header_row(ws, row, len(headers))
    row += 1
    excluded_fill = PatternFill("solid", fgColor="FEE2E2")
    for txn in txns:
        ws.cell(row=row, column=1, value=txn.get("property_name") or "")
        ws.cell(row=row, column=2, value=txn.get("city") or "")
        date_val = txn.get("sale_date") or ""
        ws.cell(row=row, column=3, value=str(date_val))
        keys_v = _as_float(txn.get("keys"))
        ws.cell(row=row, column=4, value=int(keys_v) if keys_v is not None else None).number_format = INT_FMT
        for col, key in ((5, "sale_price_usd"), (6, "sale_price_per_key_usd"), (7, "noi_usd")):
            # A comp without the figure stays blank — a dense table whose
            # header names the field; no zero is written.
            ws.cell(row=row, column=col, value=_as_float(txn.get(key))).number_format = USD_FMT
        cap_v = txn.get("cap_rate_pct")
        c = ws.cell(
            row=row, column=8,
            value=(float(cap_v) / 100.0) if cap_v is not None else None,
        )
        c.number_format = PCT_FMT
        ws.cell(
            row=row,
            column=9,
            value=txn.get("chain_scale")
            or refuse(
                ReasonCode.NO_SOURCE,
                "comparable sale carries no chain scale",
                "comp_sale.chain_scale",
            ),
        )
        excluded = bool(txn.get("excluded"))
        ws.cell(row=row, column=10, value="Yes" if excluded else "No")
        if excluded:
            for col in range(1, len(headers) + 1):
                ws.cell(row=row, column=col).fill = excluded_fill
        row += 1

    _autosize(ws, [32, 16, 14, 8, 16, 16, 16, 10, 16, 12])
    _freeze_top(ws, row=2)
    return True


def _build_historical_baseline(wb: Workbook, baseline: dict[str, Any] | None) -> bool:
    """Historical Baseline sheet — 5-year horizontal P&L table + YoY walk."""
    if not baseline:
        return False
    years = baseline.get("years") or []
    if not years:
        return False

    ws = wb.create_sheet("Historical Baseline")

    # ── Coverage banner ─────────────────────────────────────────────
    coverage = _as_float(baseline.get("coverage_pct"))
    look_back = _as_float(baseline.get("look_back_years"))
    coverage_txt = f"{coverage:.0%}" if coverage is not None else "not stated"
    window_txt = (
        f"{len(years)} of {int(look_back)} years" if look_back is not None else f"{len(years)} years"
    )
    ws["A1"] = f"COVERAGE: {coverage_txt} ({window_txt})"
    _style_header_row(ws, 1, 8)
    row = 2

    gaps = baseline.get("gaps") or []
    if gaps:
        ws.cell(row=row, column=1, value=f"Missing years: {', '.join(str(g) for g in gaps)}")
        ws.cell(row=row, column=1).font = Font(italic=True, color="92400E")
        row += 1

    # ── Horizontal P&L table ────────────────────────────────────────
    row += 1
    fy_list = sorted({int(y["fiscal_year"]) for y in years if y.get("fiscal_year")})
    year_lookup = {int(y["fiscal_year"]): y for y in years if y.get("fiscal_year")}

    headers = ["Line Item"] + [f"FY{fy}" for fy in fy_list]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=row, column=i, value=h)
    _style_header_row(ws, row, len(headers))
    row += 1

    p_and_l_lines = (
        ("rooms_revenue", "Rooms Revenue", USD_FMT),
        ("fnb_revenue", "F&B Revenue", USD_FMT),
        ("other_revenue", "Other Revenue", USD_FMT),
        ("total_revenue", "Total Revenue", USD_FMT),
        ("gop", "GOP", USD_FMT),
        ("noi", "NOI", USD_FMT),
        ("occupancy", "Occupancy", PCT_FMT),
        ("adr", "ADR", USD_FMT),
        ("revpar", "RevPAR", USD_FMT),
    )
    for key, label, fmt in p_and_l_lines:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        for i, fy in enumerate(fy_list, start=2):
            v = year_lookup.get(fy, {}).get(key)
            if v is None:
                continue
            c = ws.cell(row=row, column=i, value=float(v))
            c.number_format = fmt
        row += 1

    # ── YoY walk — top swings ───────────────────────────────────────
    row += 1
    walk = baseline.get("walk") or []
    if walk:
        ws.cell(row=row, column=1, value="TOP YoY SWINGS")
        _style_subhead_row(ws, row, 4)
        row += 1
        ws.cell(row=row, column=1, value="Line")
        ws.cell(row=row, column=2, value="Year")
        ws.cell(row=row, column=3, value="Value")
        ws.cell(row=row, column=4, value="YoY %")
        _style_header_row(ws, row, 4)
        row += 1
        walk_start = row
        for d in walk[:5]:
            ws.cell(row=row, column=1, value=line_label(d.get("line")))
            yr = _as_float(d.get("year"))
            ws.cell(row=row, column=2, value=int(yr) if yr is not None else None)
            ws.cell(row=row, column=3, value=_as_float(d.get("value"))).number_format = USD_FMT
            ws.cell(row=row, column=4, value=_as_float(d.get("yoy_pct"))).number_format = PCT_FMT
            row += 1
        walk_end = row - 1
        if walk_end >= walk_start:
            _apply_color_scale(
                ws,
                f"D{walk_start}:D{walk_end}",
                lo_color="FECACA",
                mid_color="FEF3C7",
                hi_color="BBF7D0",
            )

    _autosize(ws, [26] + [16] * max(1, len(fy_list)))
    _freeze_top(ws, row=2)
    return True


def _build_str_forecast(wb: Workbook, str_forecast: dict[str, Any] | None) -> bool:
    """STR Forward Forecast sheet — 48 rows (24 historical + 24 forecast).

    Cols: Month, Historical RevPAR, Downside RevPAR, Base RevPAR,
    Upside RevPAR, Comp Set RevPAR, Subject Index.
    """
    if not str_forecast:
        return False

    historical = str_forecast.get("historical_months") or []
    forecast = str_forecast.get("forecast_months") or {}
    if not historical and not forecast:
        return False

    ws = wb.create_sheet("STR Forecast")
    headers = [
        "Month", "Historical RevPAR", "Downside RevPAR", "Base RevPAR",
        "Upside RevPAR", "Comp Set RevPAR", "Subject Index",
    ]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    _style_header_row(ws, 1, len(headers))

    row = 2
    # Historical rows
    for m in historical:
        ws.cell(row=row, column=1, value=str(m.get("period") or ""))
        ws.cell(row=row, column=2, value=_as_float(m.get("revpar"))).number_format = USD_FMT
        # Forecast columns left blank for historical rows; a month without
        # a comp-set reading stays blank too.
        ws.cell(row=row, column=6, value=_as_float(m.get("comp_set_revpar"))).number_format = USD_FMT
        ws.cell(row=row, column=7, value=_as_float(m.get("revpar_index"))).number_format = MULT_FMT
        row += 1

    # Forecast rows — align downside / base / upside side-by-side by period.
    downside = forecast.get("downside") or []
    base = forecast.get("base") or []
    upside = forecast.get("upside") or []
    n = max(len(downside), len(base), len(upside))
    for i in range(n):
        d = downside[i] if i < len(downside) else {}
        b = base[i] if i < len(base) else {}
        u = upside[i] if i < len(upside) else {}
        period = b.get("period") or d.get("period") or u.get("period") or ""
        ws.cell(row=row, column=1, value=str(period))
        # Historical RevPAR column intentionally blank for forecast rows.
        if d:
            ws.cell(row=row, column=3, value=_as_float(d.get("revpar"))).number_format = USD_FMT
        if b:
            ws.cell(row=row, column=4, value=_as_float(b.get("revpar"))).number_format = USD_FMT
        if u:
            ws.cell(row=row, column=5, value=_as_float(u.get("revpar"))).number_format = USD_FMT
        # Comp set + index from base scenario (canonical).
        if b:
            ws.cell(row=row, column=6, value=_as_float(b.get("comp_set_revpar"))).number_format = USD_FMT
            ws.cell(row=row, column=7, value=_as_float(b.get("revpar_index"))).number_format = MULT_FMT
        row += 1

    _autosize(ws, [12, 18, 18, 18, 18, 18, 14])
    _freeze_top(ws, row=2)
    return True


def _build_named_scenarios(
    wb: Workbook, scenarios: list[dict[str, Any]] | None
) -> bool:
    """Named Scenarios Compare sheet.

    Rows = KPIs (IRR, EM, NOI Y1, NOI Stab, Exit Cap, DSCR).
    Cols = scenarios. % delta from base in adjacent cells.
    """
    if not scenarios or len(scenarios) < 2:
        return False

    ws = wb.create_sheet("Named Scenarios")
    base = next((s for s in scenarios if s.get("is_base")), scenarios[0])
    others = [s for s in scenarios if s is not base]

    # Header row: Scenario columns
    ws.cell(row=1, column=1, value="KPI")
    col = 2
    for s in [base] + others:
        ws.cell(
            row=1,
            column=col,
            value=str(
                s.get("name")
                or refuse(
                    ReasonCode.NO_SOURCE, "saved scenario has no name", "scenario.name"
                )
            ),
        )
        col += 1
        ws.cell(row=1, column=col, value="Δ vs Base")
        col += 1
    _style_header_row(ws, 1, col - 1)

    # Description row (optional)
    ws.cell(row=2, column=1, value="Description").font = Font(italic=True)
    col = 2
    for s in [base] + others:
        cell = ws.cell(row=2, column=col, value=s.get("description") or "")
        cell.alignment = Alignment(wrap_text=True)
        cell.font = Font(italic=True, color="6B7280")
        col += 2

    kpis = (
        ("levered_irr", "Levered IRR", PCT_FMT),
        ("equity_multiple", "Equity Multiple", MULT_FMT),
        ("year1_noi_usd", "Year 1 NOI", USD_FMT),
        ("stabilized_noi_usd", "Stabilized NOI", USD_FMT),
        ("exit_cap_pct", "Exit Cap Rate", PCT_FMT),
        ("year1_dscr", "Year 1 DSCR", MULT_FMT),
    )

    base_kpis = base.get("kpis") or {}
    row = 3
    for key, label, fmt in kpis:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        base_val = _as_float(base_kpis.get(key))
        col = 2
        for s in [base] + others:
            k = s.get("kpis") or {}
            v = _as_float(k.get(key))
            _write_value(ws, row, col, v, fmt, concept=f"scenario.{key}",
                         missing=f"{s.get('name', 'scenario')} run carries no {label.lower()}")
            col += 1
            # Delta column (blank for the base scenario itself); stated only
            # when both sides exist and the base is non-zero.
            if s is base or v is None or base_val is None or base_val == 0:
                ws.cell(row=row, column=col, value=None)
            else:
                c = ws.cell(row=row, column=col, value=(v - base_val) / abs(base_val))
                c.number_format = PCT_FMT
            col += 1
        row += 1

    widths = [22] + [14, 12] * (1 + len(others))
    _autosize(ws, widths)
    _freeze_top(ws, row=2)
    return True


def _build_loi_appendix(wb: Workbook, loi: dict[str, Any] | None) -> bool:
    """LOI Appendix sheet — plain-text markdown body in a wide column.

    Each markdown line lands on its own row in column A. Headings get
    bold formatting; ``---`` rules are skipped.
    """
    if not loi:
        return False
    md = loi.get("rendered_markdown") or ""
    if not md.strip():
        return False

    ws = wb.create_sheet("LOI Appendix")
    ws["A1"] = "DRAFT LETTER OF INTENT"
    _style_header_row(ws, 1, 1)

    row = 2
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.strip() == "---":
            row += 1
            continue
        cell = ws.cell(row=row, column=1, value=line)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if line.startswith("# "):
            cell.value = line[2:].strip()
            cell.font = Font(bold=True, size=14)
        elif line.startswith("## "):
            cell.value = line[3:].strip()
            cell.font = Font(bold=True, size=12)
        elif line.startswith("### "):
            cell.value = line[4:].strip()
            cell.font = Font(bold=True, size=11)
        row += 1

    ws.column_dimensions["A"].width = 100
    return True


def _refusal_row(item: Any) -> tuple[str, str, str, str] | None:
    """``(concept, where, code, meaning)`` for one refusal, or ``None``.

    Accepts a :class:`~fondok_schemas.reasons.Refusal` or the same shape as a
    plain dict, so a payload that has been round-tripped through JSON reads
    the same as one held in memory.
    """
    if isinstance(item, dict):
        raw_code = item.get("code")
        concept = item.get("concept")
        detail = item.get("detail")
    else:
        raw_code = getattr(item, "code", None)
        concept = getattr(item, "concept", None)
        detail = getattr(item, "detail", None)
    if raw_code is None:
        return None
    try:
        code = ReasonCode(getattr(raw_code, "value", raw_code))
    except ValueError:
        return None
    return (
        str(concept) if concept else "",
        str(detail) if detail else "",
        code.value,
        REASON_META[code]["label"],
    )


def _build_refusals(wb: Workbook, refusals: list[Any] | None) -> bool:
    """Refusals sheet — what this export declined to produce, and why.

    One row per distinct refusal the payload (and this workbook's own dash
    writes) recorded: the concept, where it appeared, the machine-readable
    reason code and its meaning straight from ``REASON_META``. Conditional
    like every Wave 2/3 sheet — a complete export gains no sheet.
    """
    rows = [r for r in (_refusal_row(item) for item in refusals or []) if r]
    if not rows:
        return False

    ws = wb.create_sheet("Refusals")
    ws["A1"] = "REFUSALS — FIGURES THIS EXPORT DECLINED TO PRODUCE"
    _style_header_row(ws, 1, 4)

    headers = ["Concept", "Where it appeared", "Reason code", "Meaning"]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=2, column=i, value=h)
    _style_subhead_row(ws, 2, len(headers))

    row = 3
    for concept, where, code, meaning in rows:
        ws.cell(row=row, column=1, value=concept)
        cell = ws.cell(row=row, column=2, value=where)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=row, column=3, value=code)
        ws.cell(row=row, column=4, value=meaning)
        row += 1

    ws.cell(
        row=row + 1,
        column=1,
        value=(
            "Every dash in this workbook is one of the codes above — Fondok "
            "never fills a gap with a placeholder value."
        ),
    ).font = Font(italic=True, size=9, color="6B7280")

    _autosize(ws, [30, 52, 22, 34])
    _freeze_top(ws, row=3)
    return True


# ─────────────────────────── public API ───────────────────────────


def build_excel(deal_id: UUID | str, model: dict[str, Any], output_path: Path) -> Path:
    """Build the institutional underwriting workbook.

    Always renders the 9 legacy sheets (Cover/Assumptions/Sources &
    Uses/Operating Proforma/Debt Schedule/Returns/Partnership/Variance/
    Market Comps). The legacy Sensitivity sheet is REPLACED by the W4.2
    Pricing Sensitivity sheet when ``sensitivity_grid`` is set (when
    absent, no sensitivity sheet is emitted — barebones deals get the
    9 legacy sheets cleanly).

    Wave 2/3 sheets render conditionally on presence of their source
    data, so a barebones deal still produces a valid workbook.

    Phase 4.2: the whole build runs inside a refusal collector, and the
    "Refusals" sheet is appended when the payload itself carried refusals
    (``model["refusals"]``) — so a fixture / barebones workbook keeps
    exactly the sheets it had, while a live export says why each dash is
    a dash.
    """
    payload_refusals = list(model.get("refusals") or [])
    with collect_refusals() as own_refusals:
        return _build_excel_sheets(
            deal_id, model, output_path, payload_refusals, own_refusals
        )


def _build_excel_sheets(
    deal_id: UUID | str,
    model: dict[str, Any],
    output_path: Path,
    payload_refusals: list[Any],
    own_refusals: list[Any],
) -> Path:
    """The sheet assembly — see :func:`build_excel`, which wraps it."""
    wave = _aggregate_wave2_3_for_excel(model)

    wb = Workbook()
    # Reserve the active sheet for Cover — we'll mutate it at the end
    # once we know which Wave 2/3 sheets actually rendered.
    cover_ws = wb.active
    cover_ws.title = "Cover"

    sections_included: list[str] = ["Cover"]

    _build_assumptions(wb, model)
    sections_included.append("Assumptions")
    _build_sources_uses(wb, model)
    sections_included.append("Sources & Uses")
    _build_proforma(wb, model)
    sections_included.append("Operating Proforma")
    _build_debt_schedule(wb, model)
    sections_included.append("Debt Schedule")
    _build_returns(wb, model)
    sections_included.append("Returns")
    _build_partnership(wb, model)
    sections_included.append("Partnership")
    _build_variance(wb, model)
    sections_included.append("Variance")
    _build_market_comps(wb, model)
    sections_included.append("Market Comps")

    # Wave 2/3 sheets — only when data present.
    if _build_revenue_mix(wb, wave["segments_by_year"]):
        sections_included.append("Revenue Mix")
    if _build_renovation_plan(wb, wave["pip"]):
        sections_included.append("Renovation Plan")
    if _build_capital_plan(wb, wave["capex_schedule"]):
        sections_included.append("Capital Plan")
    if _build_op_ratio_provenance(wb, wave["op_ratio_provenance"]):
        sections_included.append("Op-Ratio Provenance")
    if _build_sensitivity_grid(
        wb,
        wave["sensitivity_grid"],
        wave["max_price"],
        wave["pricing_unavailable"],
        wave["grid_unavailable"],
    ):
        sections_included.append("Pricing Sensitivity")
    if _build_comparable_sales(wb, wave["comp_sales"]):
        sections_included.append("Comparable Sales")
    if _build_historical_baseline(wb, wave["historical_baseline"]):
        sections_included.append("Historical Baseline")
    if _build_str_forecast(wb, wave["str_forecast"]):
        sections_included.append("STR Forecast")
    if _build_named_scenarios(wb, wave["named_scenarios"]):
        sections_included.append("Named Scenarios")
    if _build_loi_appendix(wb, wave["loi"]):
        sections_included.append("LOI Appendix")

    # Phase 4.2 — the typed twin of every dash above. Gated on the PAYLOAD's
    # refusals so a fixture / barebones workbook is untouched; when it does
    # render it also carries this workbook's own dash writes, deduped.
    if payload_refusals and _build_refusals(
        wb, dedupe([*payload_refusals, *own_refusals])
    ):
        sections_included.append("Refusals")

    # Now render the Cover with the actual section list. The active
    # sheet is still our reserved Cover blank, so _build_cover mutates
    # it in place.
    _build_cover(wb, model, sections_included)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


__all__ = [
    "_aggregate_wave2_3_for_excel",
    "_apply_color_scale",
    "_build_refusals",
    "build_excel",
]
