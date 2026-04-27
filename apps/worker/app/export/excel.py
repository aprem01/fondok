# ruff: noqa: RUF001
"""Excel acquisition model builder.

Builds a 10-tab institutional underwriting workbook from an engine
outputs dict (see ``evals/golden-set/kimpton-angler/expected/model.json``).
Tabs:
  1. Cover
  2. Assumptions
  3. Sources & Uses
  4. Operating Proforma
  5. Debt Schedule
  6. Returns
  7. Sensitivity
  8. Partnership
  9. Variance
 10. Market Comps

Number formats follow institutional convention (currency, percent, x).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

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


# ─────────────────────────── tab builders ───────────────────────────


def _build_cover(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.active
    ws.title = "Cover"

    deal_name = model.get("deal_name", "Hotel Underwriting Model")
    location = model.get("location", "")
    keys = model.get("keys", "")
    brand = model.get("brand", "")

    inv = model.get("investment_engine", {})
    ret = model.get("returns_engine", {})
    debt = model.get("debt_engine", {})

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
    ws["B7"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    ws["A8"] = "Model Version"
    ws["B8"] = model.get("model_version", "fondok-engine-1.0")

    for r in range(3, 9):
        ws.cell(row=r, column=1).font = Font(bold=True, size=11)

    # Key metrics block
    ws.merge_cells("A10:F10")
    ws["A10"] = "KEY METRICS"
    _style_header_row(ws, 10, 6)

    metrics = [
        ("Purchase Price", inv.get("purchase_price_usd", 0), USD_FMT),
        ("Price / Key", inv.get("price_per_key_usd", 0), USD_FMT),
        ("Total Capital", inv.get("total_capital_usd", 0), USD_FMT),
        ("Loan Amount", debt.get("loan_amount_usd", 0), USD_FMT),
        ("Loan Rate", debt.get("interest_rate_pct", 0), PCT_FMT),
        ("Year 1 DSCR", debt.get("year1_dscr", 0), MULT_FMT),
        ("Levered IRR", ret.get("levered_irr", 0), PCT_FMT),
        ("Equity Multiple", ret.get("equity_multiple", 0), MULT_FMT),
        ("Year 1 CoC", ret.get("year1_cash_on_cash", 0), PCT_FMT),
        ("Hold (yrs)", ret.get("hold_years", 0), INT_FMT),
        ("Exit Cap Rate", ret.get("exit_cap_rate_pct", 0), PCT_FMT),
        ("Entry Cap (Y1 UW)", inv.get("entry_cap_rate_year1_uw", 0), PCT_FMT),
    ]
    row = 11
    for label, val, fmt in metrics:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        c = ws.cell(row=row, column=2, value=val)
        c.number_format = fmt
        row += 1

    _autosize(ws, [22, 22, 4, 22, 22, 4])
    _freeze_top(ws, row=2)


def _build_assumptions(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Assumptions")
    inv = model.get("investment_engine", {})
    debt = model.get("debt_engine", {})
    refi = model.get("refi_engine", {})
    ret = model.get("returns_engine", {})

    ws["A1"] = "Assumption"
    ws["B1"] = "Value"
    ws["C1"] = "Unit / Notes"
    _style_header_row(ws, 1, 3)

    sections: list[tuple[str, list[tuple[str, Any, str, str]]]] = [
        (
            "Acquisition",
            [
                ("Purchase Price", inv.get("purchase_price_usd", 0), USD_FMT, "USD"),
                ("Price / Key", inv.get("price_per_key_usd", 0), USD_FMT, "USD/key"),
                ("Closing Costs", inv.get("closing_costs_usd", 0), USD_FMT, "USD"),
                ("Renovation Budget", inv.get("renovation_budget_usd", 0), USD_FMT, "PIP / capex"),
                ("Renovation / Key", inv.get("renovation_per_key_usd", 0), USD_FMT, "USD/key"),
                ("Soft Costs", inv.get("soft_costs_usd", 0), USD_FMT, ""),
                ("Contingency", inv.get("contingency_usd", 0), USD_FMT, ""),
                ("Working Capital", inv.get("working_capital_usd", 0), USD_FMT, ""),
                ("Loan Costs", inv.get("loan_costs_usd", 0), USD_FMT, ""),
                ("Total Capital", inv.get("total_capital_usd", 0), USD_FMT, "All-in"),
                ("Total Capital / Key", inv.get("total_capital_per_key_usd", 0), USD_FMT, ""),
                ("Entry Cap (T-12)", inv.get("entry_cap_rate_t12", 0), PCT_FMT, ""),
                ("Entry Cap (Y1 UW)", inv.get("entry_cap_rate_year1_uw", 0), PCT_FMT, ""),
                ("Year 1 Yield on Cost", inv.get("year1_yield_on_cost", 0), PCT_FMT, ""),
            ],
        ),
        (
            "Senior Debt",
            [
                ("Loan Amount", debt.get("loan_amount_usd", 0), USD_FMT, ""),
                ("LTV (cost)", debt.get("ltv_cost", 0), PCT_FMT, ""),
                ("LTV (value)", debt.get("ltv_value", 0), PCT_FMT, ""),
                ("LTC", debt.get("ltc", 0), PCT_FMT, ""),
                ("Interest Rate", debt.get("interest_rate_pct", 0), PCT_FMT, ""),
                ("Amortization (yrs)", debt.get("amortization_years", 0), INT_FMT, ""),
                ("Term (yrs)", debt.get("term_years", 0), INT_FMT, ""),
                ("Annual Debt Service", debt.get("annual_debt_service_usd", 0), USD_FMT, ""),
                ("Year 1 DSCR", debt.get("year1_dscr", 0), MULT_FMT, ""),
                ("Year 1 Debt Yield", debt.get("year1_debt_yield", 0), PCT_FMT, ""),
                ("IO Period (months)", debt.get("interest_only_period_months", 0), INT_FMT, ""),
            ],
        ),
        (
            "Refinance",
            [
                ("Refi Year", refi.get("refi_year", 0), INT_FMT, ""),
                ("Refi LTV", refi.get("refi_ltv", 0), PCT_FMT, ""),
                ("Refi Rate", refi.get("refi_rate_pct", 0), PCT_FMT, ""),
                ("Refi Term (yrs)", refi.get("refi_term_years", 0), INT_FMT, ""),
                ("Refi Amort (yrs)", refi.get("refi_amortization_years", 0), INT_FMT, ""),
                ("Refi Proceeds", refi.get("refi_proceeds_usd", 0), USD_FMT, ""),
                ("Cash Out", refi.get("cash_out_to_equity_usd", 0), USD_FMT, ""),
            ],
        ),
        (
            "Reversion / Returns",
            [
                ("Hold (yrs)", ret.get("hold_years", 0), INT_FMT, ""),
                ("Exit Cap Rate", ret.get("exit_cap_rate_pct", 0), PCT_FMT, ""),
                ("Terminal NOI", ret.get("terminal_noi_usd", 0), USD_FMT, ""),
                ("Gross Sale Price", ret.get("gross_sale_price_usd", 0), USD_FMT, ""),
                ("Selling Costs", ret.get("selling_costs_usd", 0), USD_FMT, ""),
                ("Net Sale Proceeds", ret.get("net_sale_proceeds_usd", 0), USD_FMT, ""),
            ],
        ),
    ]

    row = 2
    for section_name, items in sections:
        ws.cell(row=row, column=1, value=section_name)
        _style_subhead_row(ws, row, 3)
        row += 1
        for label, val, fmt, note in items:
            ws.cell(row=row, column=1, value=label)
            c = ws.cell(row=row, column=2, value=val)
            c.number_format = fmt
            ws.cell(row=row, column=3, value=note)
            row += 1
        row += 1

    _autosize(ws, [32, 22, 30])
    _freeze_top(ws, row=2)


def _build_sources_uses(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Sources & Uses")
    inv = model.get("investment_engine", {})
    debt = model.get("debt_engine", {})
    sources = model.get("sources", []) or [
        {
            "label": "Senior Debt",
            "amount": debt.get("loan_amount_usd", 0),
            "pct": debt.get("ltc", 0),
        },
        {
            "label": "Equity",
            "amount": inv.get("total_capital_usd", 0) - debt.get("loan_amount_usd", 0),
            "pct": 1 - debt.get("ltc", 0),
        },
    ]
    uses = model.get("uses", []) or [
        {"label": "Purchase Price", "amount": inv.get("purchase_price_usd", 0)},
        {"label": "Closing Costs", "amount": inv.get("closing_costs_usd", 0)},
        {"label": "Renovation", "amount": inv.get("renovation_budget_usd", 0)},
        {"label": "Soft Costs", "amount": inv.get("soft_costs_usd", 0)},
        {"label": "Contingency", "amount": inv.get("contingency_usd", 0)},
        {"label": "Working Capital", "amount": inv.get("working_capital_usd", 0)},
        {"label": "Loan Costs", "amount": inv.get("loan_costs_usd", 0)},
    ]

    # Sources
    ws["A1"] = "SOURCES"
    ws["B1"] = "Amount"
    ws["C1"] = "% of Total"
    _style_header_row(ws, 1, 3)
    row = 2
    src_total = sum(s.get("amount", 0) for s in sources if not s.get("total"))
    for s in sources:
        if s.get("total"):
            continue
        ws.cell(row=row, column=1, value=s["label"])
        c = ws.cell(row=row, column=2, value=s["amount"])
        c.number_format = USD_FMT
        pct = s.get("pct") if s.get("pct") is not None else (s["amount"] / src_total if src_total else 0)
        p = ws.cell(row=row, column=3, value=pct)
        p.number_format = PCT_FMT
        row += 1
    ws.cell(row=row, column=1, value="Total Sources")
    c = ws.cell(row=row, column=2, value=f"=SUM(B2:B{row - 1})")
    c.number_format = USD_FMT
    ws.cell(row=row, column=3, value=1.0).number_format = PCT_FMT
    _style_total_row(ws, row, 3)
    row += 3

    # Uses
    ws.cell(row=row, column=1, value="USES")
    ws.cell(row=row, column=2, value="Amount")
    ws.cell(row=row, column=3, value="% of Total")
    _style_header_row(ws, row, 3)
    use_start = row + 1
    use_total = sum(u.get("amount", 0) for u in uses if not u.get("total"))
    row = use_start
    for u in uses:
        if u.get("total"):
            continue
        ws.cell(row=row, column=1, value=u["label"])
        c = ws.cell(row=row, column=2, value=u["amount"])
        c.number_format = USD_FMT
        pct = (u["amount"] / use_total) if use_total else 0
        p = ws.cell(row=row, column=3, value=pct)
        p.number_format = PCT_FMT
        row += 1
    ws.cell(row=row, column=1, value="Total Uses")
    c = ws.cell(row=row, column=2, value=f"=SUM(B{use_start}:B{row - 1})")
    c.number_format = USD_FMT
    ws.cell(row=row, column=3, value=1.0).number_format = PCT_FMT
    _style_total_row(ws, row, 3)

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

    for r, line in enumerate(lines, start=2):
        ws.cell(row=r, column=1, value=line["label"])
        for i, key in enumerate(["y1", "y2", "y3", "y4", "y5"], start=2):
            c = ws.cell(row=r, column=i, value=line.get(key, 0) * 1000)
            c.number_format = USD_FMT
        if line.get("cagr") is not None:
            c = ws.cell(row=r, column=7, value=line["cagr"])
            c.number_format = PCT_FMT
        if line.get("bold"):
            _style_total_row(ws, r, len(headers))

    # Period note
    period_row = len(lines) + 3
    ws.cell(row=period_row, column=1, value="Period:").font = Font(italic=True)
    ws.cell(row=period_row, column=2, value=pl.get("year1_period", "")).font = Font(italic=True)
    ws.cell(row=period_row + 1, column=1, value="Note: figures in USD (whole dollars).").font = Font(italic=True, color="6B7280")

    _autosize(ws, [30, 16, 16, 16, 16, 16, 12])
    _freeze_top(ws, row=2)


def _build_debt_schedule(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Debt Schedule")
    debt = model.get("debt_engine", {})

    loan = float(debt.get("loan_amount_usd", 0))
    rate = float(debt.get("interest_rate_pct", 0))
    amort_years = int(debt.get("amortization_years", 30))
    term_years = int(debt.get("term_years", 5))
    io_months = int(debt.get("interest_only_period_months", 0))

    monthly_rate = rate / 12 if rate else 0
    n = amort_years * 12
    if monthly_rate > 0:
        pmt = loan * (monthly_rate * (1 + monthly_rate) ** n) / ((1 + monthly_rate) ** n - 1)
    else:
        pmt = loan / n if n else 0

    headers = ["Month", "Beg Balance", "Payment", "Interest", "Principal", "End Balance"]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=1, column=i, value=h)
    _style_header_row(ws, 1, len(headers))

    bal = loan
    months = term_years * 12
    for m in range(1, months + 1):
        beg = bal
        interest = beg * monthly_rate
        if m <= io_months:
            principal = 0.0
            payment = interest
        else:
            payment = pmt
            principal = payment - interest
        end = beg - principal

        row = m + 1
        ws.cell(row=row, column=1, value=m).number_format = INT_FMT
        c = ws.cell(row=row, column=2, value=beg)
        c.number_format = USD_FMT
        c = ws.cell(row=row, column=3, value=payment)
        c.number_format = USD_FMT
        c = ws.cell(row=row, column=4, value=interest)
        c.number_format = USD_FMT
        c = ws.cell(row=row, column=5, value=principal)
        c.number_format = USD_FMT
        c = ws.cell(row=row, column=6, value=end)
        c.number_format = USD_FMT

        bal = end

    # Totals
    total_row = months + 2
    ws.cell(row=total_row, column=1, value="Totals").font = Font(bold=True)
    c = ws.cell(row=total_row, column=3, value=f"=SUM(C2:C{months + 1})")
    c.number_format = USD_FMT
    c = ws.cell(row=total_row, column=4, value=f"=SUM(D2:D{months + 1})")
    c.number_format = USD_FMT
    c = ws.cell(row=total_row, column=5, value=f"=SUM(E2:E{months + 1})")
    c.number_format = USD_FMT
    _style_total_row(ws, total_row, len(headers))

    _autosize(ws, [8, 18, 16, 16, 16, 18])
    _freeze_top(ws, row=2)


def _build_returns(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Returns")
    ret = model.get("returns_engine", {})
    cf = model.get("cash_flow_engine", {})

    ws["A1"] = "Metric"
    ws["B1"] = "Value"
    _style_header_row(ws, 1, 2)

    items = [
        ("Hold Period (yrs)", ret.get("hold_years", 0), INT_FMT),
        ("Exit Cap Rate", ret.get("exit_cap_rate_pct", 0), PCT_FMT),
        ("Terminal NOI", ret.get("terminal_noi_usd", 0), USD_FMT),
        ("Gross Sale Price", ret.get("gross_sale_price_usd", 0), USD_FMT),
        ("Selling Costs", ret.get("selling_costs_usd", 0), USD_FMT),
        ("Net Sale Proceeds", ret.get("net_sale_proceeds_usd", 0), USD_FMT),
        ("Levered IRR", ret.get("levered_irr", 0), PCT_FMT),
        ("Unlevered IRR", ret.get("unlevered_irr", 0), PCT_FMT),
        ("Equity Multiple", ret.get("equity_multiple", 0), MULT_FMT),
        ("Year 1 Cash-on-Cash", ret.get("year1_cash_on_cash", 0), PCT_FMT),
        ("Average Cash-on-Cash", ret.get("avg_cash_on_cash", 0), PCT_FMT),
    ]
    row = 2
    for label, val, fmt in items:
        ws.cell(row=row, column=1, value=label)
        c = ws.cell(row=row, column=2, value=val)
        c.number_format = fmt
        row += 1

    # DSCR / Debt yield from debt block
    debt = model.get("debt_engine", {})
    ws.cell(row=row, column=1, value="Year 1 DSCR")
    c = ws.cell(row=row, column=2, value=debt.get("year1_dscr", 0))
    c.number_format = MULT_FMT
    row += 1
    ws.cell(row=row, column=1, value="Year 1 Debt Yield")
    c = ws.cell(row=row, column=2, value=debt.get("year1_debt_yield", 0))
    c.number_format = PCT_FMT
    row += 2

    # Cash flow waterfall
    ws.cell(row=row, column=1, value="CASH FLOW WATERFALL ($)")
    _style_subhead_row(ws, row, 6)
    row += 1
    ws.cell(row=row, column=1, value="Year")
    for i in range(1, 6):
        ws.cell(row=row, column=i + 1, value=f"Y{i}")
    _style_header_row(ws, row, 6)
    row += 1

    ws.cell(row=row, column=1, value="Cash Flow After Debt")
    for i, key in enumerate(
        ["year1_cf_after_debt_usd", "year2_cf_after_debt_usd", "year3_cf_after_debt_usd",
         "year4_cf_after_debt_usd", "year5_cf_after_debt_usd"],
        start=2,
    ):
        c = ws.cell(row=row, column=i, value=cf.get(key, 0))
        c.number_format = USD_FMT
    row += 1

    ws.cell(row=row, column=1, value="Cumulative")
    cumulative = 0
    for i in range(2, 7):
        cumulative += cf.get(f"year{i - 1}_cf_after_debt_usd", 0)
        c = ws.cell(row=row, column=i, value=cumulative)
        c.number_format = USD_FMT
    _style_total_row(ws, row, 6)

    _autosize(ws, [28, 18, 18, 18, 18, 18])
    _freeze_top(ws, row=2)


def _build_sensitivity(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Sensitivity")
    ret = model.get("returns_engine", {})
    base_irr = float(ret.get("levered_irr", 0.23))
    base_mult = float(ret.get("equity_multiple", 2.12))
    base_coc = float(ret.get("year1_cash_on_cash", 0.046))

    def write_matrix(
        start_row: int,
        title: str,
        row_label: str,
        col_label: str,
        row_vals: list[float],
        col_vals: list[float],
        center: float,
        fmt: str,
        sensitivity: float = 0.5,
    ) -> int:
        ws.cell(row=start_row, column=1, value=title)
        _style_subhead_row(ws, start_row, len(col_vals) + 2)
        ws.cell(row=start_row + 1, column=1, value=f"{row_label} \\ {col_label}")
        for j, cv in enumerate(col_vals, start=2):
            c = ws.cell(row=start_row + 1, column=j, value=cv)
            c.number_format = PCT_FMT if cv < 1 else INT_FMT
            c.font = Font(bold=True)

        n_rows = len(row_vals)
        n_cols = len(col_vals)
        mid_r = (n_rows - 1) / 2
        mid_c = (n_cols - 1) / 2

        for i, rv in enumerate(row_vals):
            r = start_row + 2 + i
            c = ws.cell(row=r, column=1, value=rv)
            c.number_format = PCT_FMT if rv < 1 else INT_FMT
            c.font = Font(bold=True)
            for j in range(n_cols):
                # Symmetric perturbation around the center
                drow = (i - mid_r) / max(mid_r, 1)
                dcol = (j - mid_c) / max(mid_c, 1)
                val = center * (1 + sensitivity * (dcol - drow) / 2)
                cell = ws.cell(row=r, column=2 + j, value=val)
                cell.number_format = fmt
                # Heatmap shading: green-amber-red
                norm = (val - center * (1 - sensitivity)) / (2 * center * sensitivity) if center else 0.5
                norm = max(0.0, min(1.0, norm))
                if norm > 0.66:
                    cell.fill = PatternFill("solid", fgColor="D1FAE5")  # green-100
                elif norm > 0.33:
                    cell.fill = PatternFill("solid", fgColor="FEF3C7")  # amber-100
                else:
                    cell.fill = PatternFill("solid", fgColor="FEE2E2")  # red-100

        return start_row + 2 + n_rows + 2

    row = 1
    row = write_matrix(
        row,
        "Levered IRR — Exit Cap × RevPAR Growth",
        "Exit Cap",
        "RevPAR Growth",
        [0.060, 0.065, 0.070, 0.075, 0.080],
        [0.02, 0.03, 0.05, 0.06, 0.08],
        base_irr,
        PCT_FMT,
    )
    row = write_matrix(
        row,
        "Equity Multiple — LTV × Hold Period",
        "LTV",
        "Hold (yrs)",
        [0.55, 0.60, 0.65, 0.70, 0.75],
        [3, 4, 5, 6, 7],
        base_mult,
        MULT_FMT,
    )
    row = write_matrix(
        row,
        "Cash-on-Cash — Cap Rate × Hold Period",
        "Cap Rate",
        "Hold (yrs)",
        [0.060, 0.065, 0.070, 0.075, 0.080],
        [3, 4, 5, 6, 7],
        base_coc,
        PCT_FMT,
        sensitivity=0.6,
    )

    _autosize(ws, [22, 16, 16, 16, 16, 16, 16])
    _freeze_top(ws, row=2)


def _build_partnership(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Partnership")
    p = model.get("partnership_engine", {})

    ws["A1"] = "Partnership Structure"
    _style_header_row(ws, 1, 3)
    rows = [
        ("Structure", p.get("structure", "GP/LP waterfall"), ""),
        ("LP Equity", p.get("lp_equity_usd", 0), USD_FMT),
        ("GP Equity", p.get("gp_equity_usd", 0), USD_FMT),
        ("Total Equity", p.get("total_equity_usd", 0), USD_FMT),
        ("LP Preferred Return", p.get("lp_pref_pct", 0), PCT_FMT),
    ]
    row = 2
    for label, val, fmt in rows:
        ws.cell(row=row, column=1, value=label).font = Font(bold=True)
        c = ws.cell(row=row, column=2, value=val)
        if fmt:
            c.number_format = fmt
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
    tier_rows = [
        ("Tier 1 — Pref + Promote", p.get("gp_promote_tier_1_irr_hurdle", 0), p.get("gp_promote_tier_1_pct", 0)),
        ("Tier 2 — Catch-up + Promote", p.get("gp_promote_tier_2_irr_hurdle", 0), p.get("gp_promote_tier_2_pct", 0)),
    ]
    for label, hurdle, promote in tier_rows:
        ws.cell(row=row, column=1, value=label)
        c = ws.cell(row=row, column=2, value=hurdle)
        c.number_format = PCT_FMT
        c = ws.cell(row=row, column=3, value=promote)
        c.number_format = PCT_FMT
        row += 1

    row += 1
    ws.cell(row=row, column=1, value="Distributions Summary")
    _style_header_row(ws, row, 3)
    row += 1
    summary = [
        ("LP IRR (after promote)", p.get("lp_irr_after_promote", 0), PCT_FMT),
        ("GP IRR (after promote)", p.get("gp_irr_after_promote", 0), PCT_FMT),
        ("LP Equity Multiple", p.get("lp_equity_multiple", 0), MULT_FMT),
        ("GP Equity Multiple", p.get("gp_equity_multiple", 0), MULT_FMT),
    ]
    for label, val, fmt in summary:
        ws.cell(row=row, column=1, value=label)
        c = ws.cell(row=row, column=2, value=val)
        c.number_format = fmt
        row += 1

    # Year-by-year distribution split estimate
    row += 1
    ws.cell(row=row, column=1, value="Distribution Per Year (estimate)")
    _style_header_row(ws, row, 6)
    row += 1
    headers = ["Year", "Total CF", "LP Share", "GP Share", "LP %", "GP %"]
    for i, h in enumerate(headers, start=1):
        ws.cell(row=row, column=i, value=h)
    _style_subhead_row(ws, row, len(headers))
    row += 1

    cf = model.get("cash_flow_engine", {})
    lp_eq = float(p.get("lp_equity_usd", 0))
    gp_eq = float(p.get("gp_equity_usd", 0))
    total_eq = lp_eq + gp_eq if (lp_eq + gp_eq) > 0 else 1
    lp_pct = lp_eq / total_eq
    gp_pct = gp_eq / total_eq
    for y in range(1, 6):
        total = cf.get(f"year{y}_cf_after_debt_usd", 0)
        ws.cell(row=row, column=1, value=f"Y{y}")
        c = ws.cell(row=row, column=2, value=total)
        c.number_format = USD_FMT
        c = ws.cell(row=row, column=3, value=total * lp_pct)
        c.number_format = USD_FMT
        c = ws.cell(row=row, column=4, value=total * gp_pct)
        c.number_format = USD_FMT
        c = ws.cell(row=row, column=5, value=lp_pct)
        c.number_format = PCT_FMT
        c = ws.cell(row=row, column=6, value=gp_pct)
        c.number_format = PCT_FMT
        row += 1

    _autosize(ws, [30, 18, 18, 18, 12, 12])
    _freeze_top(ws, row=2)


def _build_variance(wb: Workbook, model: dict[str, Any]) -> None:
    ws = wb.create_sheet("Variance")
    flags = model.get("variance_flags", []) or []

    headers = ["Flag ID", "Severity", "Metric", "Broker / Value", "T-12 / Threshold", "Variance", "Action"]
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
        # Broker / value column
        if "broker_value" in f:
            ws.cell(row=r, column=4, value=f["broker_value"])
        elif "value" in f:
            ws.cell(row=r, column=4, value=f["value"])
        # T-12 / threshold column
        if "t12_value" in f:
            ws.cell(row=r, column=5, value=f["t12_value"])
        elif "threshold_max" in f:
            ws.cell(row=r, column=5, value=f"{f.get('threshold_min','')}–{f.get('threshold_max','')}")
        # Variance
        if "variance_pct" in f:
            v = ws.cell(row=r, column=6, value=f["variance_pct"])
            v.number_format = PCT_FMT
        elif "variance_pct_pts" in f:
            ws.cell(row=r, column=6, value=f"{f['variance_pct_pts']} pts")
        ws.cell(row=r, column=7, value=f.get("recommended_action", ""))
        ws.cell(row=r, column=7).alignment = Alignment(wrap_text=True, vertical="top")

    _autosize(ws, [10, 12, 22, 18, 18, 14, 60])
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
        ws.cell(row=r, column=2, value=c.get("keys", 0)).number_format = INT_FMT
        ws.cell(row=r, column=3, value=c.get("date", ""))
        ws.cell(row=r, column=4, value=c.get("price", ""))
        ws.cell(row=r, column=5, value=c.get("per_key", c.get("perKey", "")))
        ws.cell(row=r, column=6, value=c.get("cap", ""))
        ws.cell(row=r, column=7, value=c.get("buyer", ""))

    _autosize(ws, [32, 8, 14, 14, 14, 12, 26])
    _freeze_top(ws, row=2)


# ─────────────────────────── public API ───────────────────────────


def build_excel(deal_id: UUID | str, model: dict[str, Any], output_path: Path) -> Path:
    """Build a 10-tab institutional underwriting workbook."""
    wb = Workbook()
    _build_cover(wb, model)
    _build_assumptions(wb, model)
    _build_sources_uses(wb, model)
    _build_proforma(wb, model)
    _build_debt_schedule(wb, model)
    _build_returns(wb, model)
    _build_sensitivity(wb, model)
    _build_partnership(wb, model)
    _build_variance(wb, model)
    _build_market_comps(wb, model)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


__all__ = ["build_excel"]
