# ruff: noqa: RUF001, RUF003
"""IC presentation deck builder (PowerPoint .pptx).

8 slides following an institutional-investor template:
  1. Title
  2. Executive Summary
  3. Property & Market Overview
  4. Operating Performance
  5. Sources & Uses + Capital Stack
  6. Returns Summary + 3-Scenario card
  7. Risk Assessment + Sensitivities
  8. Recommendation

No-fake-data contract (go-live rule 1): every figure on a slide comes off the
payload. A missing value renders as the refusal glyph (dense tables) or as
``not available: <reason>`` (headline cells / notes) — never a default, a
sample value or a synthesized grid. The sensitivity heat-map is the persisted
``sensitivity`` engine output (``model["sensitivity_engine"]``) or a sentence
saying why there is none.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fondok_schemas.reasons import ReasonCode
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN
from pptx.slide import Slide
from pptx.util import Inches, Pt

from .refusals import collect_refusals, refuse, unavailable

# ─────────────────────────── palette ───────────────────────────
SLATE = RGBColor(0x1F, 0x29, 0x37)
AMBER = RGBColor(0xF5, 0x9E, 0x0B)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
INK = RGBColor(0x11, 0x18, 0x27)
INK_500 = RGBColor(0x6B, 0x72, 0x80)
INK_300 = RGBColor(0xD1, 0xD5, 0xDB)
GRAY_50 = RGBColor(0xF9, 0xFA, 0xFB)
GRAY_100 = RGBColor(0xF3, 0xF4, 0xF6)
GREEN_50 = RGBColor(0xEC, 0xFD, 0xF5)
GREEN_500 = RGBColor(0x10, 0xB9, 0x81)
AMBER_50 = RGBColor(0xFF, 0xFB, 0xEB)
RED_50 = RGBColor(0xFE, 0xE2, 0xE2)


# A ``None`` reaching a formatter means the payload omitted the key rather
# than carrying a zero, so the default code is ``no_source``; ``concept`` names
# what the dash stands for so the Refusals channel can list it.


def _fmt_usd(v: float | int | None, concept: str | None = None) -> str:
    if v is None:
        return refuse(ReasonCode.NO_SOURCE, "no value on the export payload", concept)
    try:
        return f"${float(v):,.0f}"
    except (TypeError, ValueError):
        return refuse(ReasonCode.NO_SOURCE, f"value {v!r} is not a number", concept)


def _fmt_usd_m(v: float | int | None, concept: str | None = None) -> str:
    if v is None:
        return refuse(ReasonCode.NO_SOURCE, "no value on the export payload", concept)
    try:
        return f"${float(v) / 1_000_000:.1f}M"
    except (TypeError, ValueError):
        return refuse(ReasonCode.NO_SOURCE, f"value {v!r} is not a number", concept)


def _fmt_pct(v: float | None, concept: str | None = None) -> str:
    if v is None:
        return refuse(ReasonCode.NO_SOURCE, "no value on the export payload", concept)
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return refuse(ReasonCode.NO_SOURCE, f"value {v!r} is not a number", concept)


def _fmt_mult(v: float | None, concept: str | None = None) -> str:
    if v is None:
        return refuse(ReasonCode.NO_SOURCE, "no value on the export payload", concept)
    try:
        return f"{float(v):.2f}x"
    except (TypeError, ValueError):
        return refuse(ReasonCode.NO_SOURCE, f"value {v!r} is not a number", concept)


def _fmt_int(v: float | int | None, concept: str | None = None) -> str:
    if v is None:
        return refuse(ReasonCode.NO_SOURCE, "no value on the export payload", concept)
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return refuse(ReasonCode.NO_SOURCE, f"value {v!r} is not a number", concept)


def _fmt_thousands(v: float | int | None, concept: str | None = None) -> str:
    """Proforma cell (already in USD thousands)."""
    if v is None:
        return refuse(ReasonCode.NO_SOURCE, "year not projected on the proforma", concept)
    try:
        return f"{float(v):,.0f}"
    except (TypeError, ValueError):
        return refuse(ReasonCode.NO_SOURCE, f"value {v!r} is not a number", concept)


# ─────────────────────────── shared chrome ───────────────────────────


def _add_top_ribbon(slide: Slide, title: str, subtitle: str = "") -> None:
    """Slate ribbon at the top with brand-amber accent stripe."""
    # Ribbon background
    ribbon = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.33), Inches(0.85)
    )
    ribbon.fill.solid()
    ribbon.fill.fore_color.rgb = SLATE
    ribbon.line.fill.background()

    # Amber stripe at bottom edge of ribbon
    stripe = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(0), Inches(0.82), Inches(13.33), Inches(0.05)
    )
    stripe.fill.solid()
    stripe.fill.fore_color.rgb = AMBER
    stripe.line.fill.background()

    # Title
    tb = slide.shapes.add_textbox(Inches(0.35), Inches(0.10), Inches(11), Inches(0.8))
    tf = tb.text_frame
    tf.margin_top = Pt(0)
    tf.margin_bottom = Pt(0)
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = title
    r.font.size = Pt(20)
    r.font.bold = True
    r.font.color.rgb = WHITE
    if subtitle:
        p2 = tf.add_paragraph()
        r2 = p2.add_run()
        r2.text = subtitle
        r2.font.size = Pt(10)
        r2.font.color.rgb = INK_300

    # Brand mark — top right
    brand = slide.shapes.add_textbox(Inches(11.0), Inches(0.25), Inches(2.1), Inches(0.4))
    p = brand.text_frame.paragraphs[0]
    p.alignment = PP_ALIGN.RIGHT
    r = p.add_run()
    r.text = "FONDOK"
    r.font.size = Pt(10)
    r.font.bold = True
    r.font.color.rgb = AMBER


def _add_textbox(
    slide: Slide,
    x: float,
    y: float,
    w: float,
    h: float,
    text: str,
    font_size: int = 11,
    bold: bool = False,
    color: RGBColor = INK,
    align: int = PP_ALIGN.LEFT,
) -> None:
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_top = Pt(0)
    tf.margin_left = Pt(0)
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    r.font.size = Pt(font_size)
    r.font.bold = bold
    r.font.color.rgb = color


def _add_bullet_list(
    slide: Slide,
    x: float,
    y: float,
    w: float,
    h: float,
    items: list[str],
    font_size: int = 12,
) -> None:
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_top = Pt(0)
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.space_after = Pt(6)
        r = p.add_run()
        r.text = "•  " + item
        r.font.size = Pt(font_size)
        r.font.color.rgb = INK


def _add_filled_box(
    slide: Slide,
    x: float,
    y: float,
    w: float,
    h: float,
    fill: RGBColor,
    line: RGBColor | None = None,
) -> None:
    box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    box.adjustments[0] = 0.04
    box.fill.solid()
    box.fill.fore_color.rgb = fill
    if line is None:
        box.line.fill.background()
    else:
        box.line.color.rgb = line
        box.line.width = Pt(0.75)


def _add_table(
    slide: Slide,
    x: float,
    y: float,
    w: float,
    h: float,
    header: list[str],
    rows: list[list[str]],
    bold_last_row: bool = False,
    bold_rows: set[int] | None = None,
) -> None:
    bold_rows = bold_rows or set()
    n_rows = len(rows) + 1
    n_cols = len(header)
    table_shape = slide.shapes.add_table(n_rows, n_cols, Inches(x), Inches(y), Inches(w), Inches(h))
    table = table_shape.table

    for j, h_text in enumerate(header):
        cell = table.cell(0, j)
        cell.text = ""
        cell.fill.solid()
        cell.fill.fore_color.rgb = SLATE
        p = cell.text_frame.paragraphs[0]
        r = p.add_run()
        r.text = h_text
        r.font.size = Pt(10)
        r.font.bold = True
        r.font.color.rgb = WHITE

    for i, row in enumerate(rows, start=1):
        for j, val in enumerate(row):
            cell = table.cell(i, j)
            cell.text = ""
            is_bold = (bold_last_row and i == len(rows)) or (i in bold_rows)
            if is_bold:
                cell.fill.solid()
                cell.fill.fore_color.rgb = GRAY_100
            else:
                cell.fill.solid()
                cell.fill.fore_color.rgb = WHITE if i % 2 else GRAY_50
            p = cell.text_frame.paragraphs[0]
            if j > 0:
                p.alignment = PP_ALIGN.RIGHT
            r = p.add_run()
            r.text = str(val)
            r.font.size = Pt(9)
            r.font.bold = is_bold
            r.font.color.rgb = INK


# ─────────────────────────── slide builders ───────────────────────────


def _slide_title(prs: Presentation, deal: dict[str, Any]) -> None:
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)

    # Full-bleed slate background
    bg = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(13.33), Inches(7.5))
    bg.fill.solid()
    bg.fill.fore_color.rgb = SLATE
    bg.line.fill.background()

    # Amber accent bar
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(2.4), Inches(0.18), Inches(2.6))
    bar.fill.solid()
    bar.fill.fore_color.rgb = AMBER
    bar.line.fill.background()

    # FONDOK wordmark
    _add_textbox(slide, 0.6, 0.6, 4, 0.5, "FONDOK", 14, True, AMBER)

    # Title block — the payload names the property or says it cannot.
    name = deal.get("name") or deal.get("subject_property") or unavailable(
        ReasonCode.NO_SOURCE, "no property name on the deal", "property_overview.name"
    )
    location = deal.get("city") or deal.get("location") or ""
    brand = deal.get("brand", "")
    keys = deal.get("keys", "")

    _add_textbox(slide, 1.0, 2.4, 11.5, 0.6, "INVESTMENT COMMITTEE MEMORANDUM", 12, True, AMBER)
    _add_textbox(slide, 1.0, 3.05, 11.5, 1.4, name, 40, True, WHITE)
    subtitle = location
    if brand:
        subtitle += f"   ·   {brand}"
    if keys:
        subtitle += f"   ·   {keys} Keys"
    _add_textbox(slide, 1.0, 4.5, 11.5, 0.5, subtitle, 16, False, INK_300)

    today = datetime.now(UTC).strftime("%B %d, %Y")
    _add_textbox(slide, 1.0, 6.6, 11.5, 0.4, f"Investment Committee Memo  ·  {today}", 11, False, INK_300)


def _slide_exec_summary(prs: Presentation, memo: dict[str, Any], model: dict[str, Any]) -> None:
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)
    _add_top_ribbon(slide, "Executive Summary", "Key takeaways from underwriting")

    # Pull 4 takeaways from memo summary or insights
    sections = memo.get("sections", [])
    takeaways: list[str] = []

    # Try from key_insights items first
    for s in sections:
        if s.get("section_id") == "key_insights":
            for item in s.get("items", [])[:4]:
                title = item.get("title", "")
                body = item.get("body", "")
                takeaways.append(f"{title}: {body}")
            break

    # Fallback: pull body from exec/thesis/recommendation
    if not takeaways:
        for sid in ("executive_summary", "investment_thesis", "recommendation"):
            for s in sections:
                if s.get("section_id") == sid and s.get("body"):
                    takeaways.append(s["body"])

    takeaways = takeaways[:4] or ["—"]
    _add_bullet_list(slide, 0.8, 1.4, 11.5, 4.8, takeaways, font_size=14)

    # Confidence footer — stated only when the deal recorded one.
    appendix = memo.get("appendix") or {}
    confidence = appendix.get("ai_confidence")
    confidence_txt = (
        f"{int(round(float(confidence) * 100))}%"
        if confidence is not None
        else unavailable(
            ReasonCode.NO_SOURCE, "no confidence score recorded on the deal", "ai_confidence"
        )
    )
    _add_textbox(
        slide, 0.8, 6.6, 11.5, 0.4,
        f"AI Confidence: {confidence_txt}   ·   Engines: {len(appendix.get('engines_run', []))}",
        10, False, INK_500,
    )


def _slide_property_market(prs: Presentation, deal: dict[str, Any], model: dict[str, Any]) -> None:
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)
    _add_top_ribbon(slide, "Property & Market Overview")

    inv = model.get("investment_engine", {})
    market = model.get("market", {})

    # Left column — hotel facts
    _add_filled_box(slide, 0.5, 1.3, 6.0, 5.6, GRAY_50)
    _add_textbox(slide, 0.8, 1.5, 5.5, 0.4, "Hotel Facts", 14, True, INK)
    def fact(key: str, concept: str, what: str) -> str:
        v = deal.get(key)
        if v is None or v == "":
            return refuse(ReasonCode.NO_SOURCE, f"no {what} on the deal", concept)
        return str(v)

    facts = [
        ("Property", fact("name", "property_overview.name", "property name")),
        ("Brand", fact("brand", "deal.brand", "brand")),
        ("Location", fact("city", "deal.city", "city")),
        ("Keys", fact("keys", "deal.keys", "key count")),
        ("Year Built", fact("year_built", "property_overview.year_built", "year built")),
        ("Service Tier", fact("service", "deal.service", "service level")),
        ("Purchase Price", _fmt_usd(inv.get("purchase_price_usd"), "purchase_price")),
        ("Price / Key", _fmt_usd(inv.get("price_per_key_usd"), "price_per_key")),
        ("Total Capital", _fmt_usd(inv.get("total_capital_usd"), "total_capital")),
    ]
    y = 2.1
    for k, v in facts:
        _add_textbox(slide, 0.8, y, 2.5, 0.32, k, 10, False, INK_500)
        _add_textbox(slide, 3.3, y, 3.0, 0.32, v, 11, True, INK)
        y += 0.42

    # Right column — submarket KPIs
    _add_filled_box(slide, 6.85, 1.3, 6.0, 5.6, GRAY_50)
    _add_textbox(slide, 7.15, 1.5, 5.5, 0.4, "Submarket KPIs", 14, True, INK)
    # Only what the payload states — never a sample submarket.
    kpis = market.get("kpis") or {
        "Submarket": unavailable(
            ReasonCode.NO_SOURCE, "no market data on the deal", "market.kpis"
        )
    }
    y = 2.1
    for k, v in kpis.items():
        _add_textbox(slide, 7.15, y, 2.8, 0.32, str(k), 10, False, INK_500)
        _add_textbox(slide, 9.95, y, 3.0, 0.32, str(v), 11, True, INK)
        y += 0.42


def _slide_operating(prs: Presentation, model: dict[str, Any]) -> None:
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)
    _add_top_ribbon(slide, "Operating Performance", "5-Year Underwritten Proforma  (USD thousands)")

    pl = model.get("p_and_l_engine_proforma", {})
    lines = pl.get("lines", [])

    header = ["Line Item", "Y1", "Y2", "Y3", "Y4", "Y5", "CAGR"]
    rows = []
    bold_rows: set[int] = set()
    for i, line in enumerate(lines, start=1):
        label = str(line.get("label", ""))
        cagr = line.get("cagr")
        row = [
            label,
            *[_fmt_thousands(line.get(f"y{n}"), f"proforma.{label}") for n in range(1, 6)],
            f"{float(cagr) * 100:.1f}%" if cagr is not None else "",
        ]
        rows.append(row)
        if line.get("bold"):
            bold_rows.add(i)

    if not rows:
        _add_textbox(
            slide, 0.5, 1.3, 12.3, 0.5,
            "Operating proforma "
            + unavailable(
                ReasonCode.ENGINE_SKIPPED,
                "no revenue / expense engine output on the deal's canonical run",
                "proforma",
            ),
            12, False, INK_500,
        )
        return
    _add_table(slide, 0.5, 1.3, 12.3, 5.6, header, rows, bold_rows=bold_rows)


def _su_rows(
    items: list[dict[str, Any]], total_label: str, concept: str, what: str
) -> list[list[str]]:
    """``[label, amount, %]`` rows plus a total — amounts only as stated.

    The total and the shares are computed only when every line carries an
    amount; otherwise the total cell says what is missing (no silent zero).
    """
    lines = [x for x in items if isinstance(x, dict) and not x.get("total")]
    if not lines:
        return [[
            unavailable(
                ReasonCode.ENGINE_SKIPPED,
                f"no {what} on the deal's capital engine output",
                concept,
            ),
            "",
            "",
        ]]
    amounts = [x.get("amount") for x in lines]
    complete = all(isinstance(a, (int, float)) for a in amounts)
    total = float(sum(amounts)) if complete else None  # type: ignore[arg-type]
    rows: list[list[str]] = []
    for x in lines:
        amt = x.get("amount")
        pct = (
            f"{float(amt) / total * 100:.1f}%"
            if total and isinstance(amt, (int, float))
            else refuse(ReasonCode.NO_SOURCE, f"{what} share needs every line's amount", concept)
        )
        rows.append([str(x.get("label", "")), _fmt_usd(amt, concept), pct])
    rows.append([
        total_label,
        _fmt_usd(total, concept) if total is not None else unavailable(
            ReasonCode.NO_SOURCE, f"a {what} line has no amount", concept
        ),
        "100.0%" if total else "",
    ])
    return rows


def _slide_sources_uses(prs: Presentation, model: dict[str, Any]) -> None:
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)
    _add_top_ribbon(slide, "Sources & Uses · Capital Stack")

    debt = model.get("debt_engine", {})
    # Sources / uses come from the capital engine's own line items. There is
    # no fallback that rebuilds them from scalar fields — a missing list is
    # stated as such.
    src_rows = _su_rows(model.get("sources") or [], "Total Sources", "sources", "sources")
    use_rows = _su_rows(model.get("uses") or [], "Total Uses", "uses", "uses")

    _add_textbox(slide, 0.5, 1.3, 5.5, 0.4, "Sources", 13, True, INK)
    _add_table(
        slide, 0.5, 1.7, 6.0, 4.0, ["Source", "Amount", "%"], src_rows, bold_last_row=True
    )

    _add_textbox(slide, 6.85, 1.3, 5.5, 0.4, "Uses", 13, True, INK)
    _add_table(
        slide, 6.85, 1.7, 6.0, 4.5, ["Use", "Amount", "%"], use_rows, bold_last_row=True
    )

    # Capital stack summary
    _add_filled_box(slide, 0.5, 6.2, 12.3, 0.9, AMBER_50, AMBER)
    _add_textbox(slide, 0.8, 6.35, 4, 0.3, "CAPITAL STACK", 9, True, AMBER)
    stack_line = (
        f"LTC {_fmt_pct(debt.get('ltc'), 'ltc')}   ·   LTV {_fmt_pct(debt.get('ltv_value'), 'ltv')}"
        f"   ·   Rate {_fmt_pct(debt.get('interest_rate_pct'), 'interest_rate')}"
        f"   ·   DSCR Y1 {_fmt_mult(debt.get('year1_dscr'), 'dscr')}"
        f"   ·   Debt Yield Y1 {_fmt_pct(debt.get('year1_debt_yield'), 'debt_yield')}"
    )
    # B9 — the flat-SOFR provenance note travels with the debt figures.
    note = debt.get("benchmark_note")
    if note:
        stack_line += f"\n{note}"
    _add_textbox(slide, 0.8, 6.6, 12.0, 0.4, stack_line, 12 if not note else 10, True, INK)


def _slide_returns(prs: Presentation, model: dict[str, Any]) -> None:
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)
    _add_top_ribbon(slide, "Returns Summary · Scenarios")

    ret = model.get("returns_engine", {})
    scenarios = model.get("scenario_outputs", [])

    # Top metrics tiles
    tiles = [
        ("Levered IRR", _fmt_pct(ret.get("levered_irr"), "levered_irr")),
        ("Equity Multiple", _fmt_mult(ret.get("equity_multiple"), "equity_multiple")),
        ("Year 1 CoC", _fmt_pct(ret.get("year1_cash_on_cash"), "year_one_coc")),
        ("Hold (yrs)", _fmt_int(ret.get("hold_years"), "hold_years")),
    ]
    x = 0.5
    for k, v in tiles:
        _add_filled_box(slide, x, 1.3, 3.0, 1.4, GRAY_50, INK_300)
        _add_textbox(slide, x + 0.2, 1.45, 2.8, 0.3, k, 10, False, INK_500)
        _add_textbox(slide, x + 0.2, 1.85, 2.8, 0.7, v, 28, True, AMBER)
        x += 3.16

    # Scenario cards
    _add_textbox(slide, 0.5, 3.05, 12, 0.4, "3-SCENARIO ANALYSIS", 11, True, AMBER)
    if not scenarios:
        _add_textbox(
            slide, 0.5, 3.5, 12, 0.4,
            "Scenario analysis "
            + unavailable(
                ReasonCode.ENGINE_SKIPPED,
                "no saved scenario has a completed run on this deal",
                "scenarios",
            ),
            11, False, INK_500,
        )
    if scenarios:
        x = 0.5
        for sc in scenarios:
            name = sc.get("name", "")
            tone = GREEN_50 if sc.get("base") else (AMBER_50 if "Up" in name else RED_50)
            line = GREEN_500 if sc.get("base") else (AMBER if "Up" in name else RGBColor(0xEF, 0x44, 0x44))
            _add_filled_box(slide, x, 3.5, 4.1, 3.5, tone, line)
            _add_textbox(slide, x + 0.25, 3.65, 3.7, 0.4, name.upper(), 11, True, INK)
            metrics = [
                ("IRR", _fmt_pct(sc.get("irr"), "scenario.levered_irr")),
                ("Unlev IRR", _fmt_pct(sc.get("unlevered_irr"), "scenario.unlevered_irr")),
                ("Multiple", _fmt_mult(sc.get("multiple"), "scenario.equity_multiple")),
                ("Avg CoC", _fmt_pct(sc.get("avg_coc"), "scenario.avg_coc")),
                ("Exit Value", _fmt_usd_m(sc.get("exit_value_usd"), "scenario.exit_value")),
            ]
            yy = 4.05
            for k, v in metrics:
                _add_textbox(slide, x + 0.25, yy, 2.0, 0.3, k, 10, False, INK_500)
                _add_textbox(slide, x + 2.2, yy, 1.8, 0.3, v, 11, True, INK)
                yy += 0.5
            x += 4.27


#: Display names for the sensitivity engine's axis variables / metrics
#: (``engines/sensitivity.py`` ``SensitivityVariable`` / ``SensitivityMetric``).
_SENS_VARIABLE_LABEL: dict[str, str] = {
    "exit_cap_rate": "Exit Cap",
    "revpar_growth": "RevPAR Growth",
    "ltv": "LTV",
    "interest_rate": "Interest Rate",
    "hold_years": "Hold (yrs)",
    "purchase_price": "Purchase Price",
    "loan_amount": "Loan Amount",
}
_SENS_METRIC_LABEL: dict[str, str] = {
    "levered_irr": "Levered IRR",
    "unlevered_irr": "Unlevered IRR",
    "equity_multiple": "Equity Multiple",
    "year_one_coc": "Year 1 CoC",
    "gross_sale_price": "Gross Sale Price",
}


def _fmt_sens_axis(variable: str | None, value: float | None) -> str:
    """Axis tick for a sensitivity variable, in that variable's own unit."""
    if value is None:
        return refuse(ReasonCode.NO_SOURCE, "axis value missing on the sensitivity output", "sensitivity")
    if variable == "hold_years":
        return f"{value:.0f}"
    if variable in ("purchase_price", "loan_amount"):
        return f"${value / 1_000_000:.1f}M"
    return f"{value * 100:.1f}%" if variable == "exit_cap_rate" else f"{value * 100:.0f}%"


def _fmt_sens_metric(metric: str | None, value: float | None) -> str:
    if value is None:
        return refuse(ReasonCode.NO_SOURCE, "cell value missing on the sensitivity output", "sensitivity")
    if metric == "equity_multiple":
        return f"{value:.2f}x"
    if metric == "gross_sale_price":
        return f"${value / 1_000_000:.1f}M"
    return f"{value * 100:.1f}%"


def _render_sensitivity_grid(slide: Slide, sens: dict[str, Any]) -> None:
    """The persisted sensitivity engine grid, coloured relative to the base
    cell (green at/above base, amber within 2pp / 0.1x below, red further).
    Colouring is presentation only — every number is the engine's."""
    rows = [r for r in (sens.get("rows") or []) if r is not None]
    cols = [c for c in (sens.get("cols") or []) if c is not None]
    cells = sens.get("cells") or []
    row_var = sens.get("row_variable")
    col_var = sens.get("col_variable")
    metric = sens.get("metric")
    lookup: dict[tuple[float, float], dict[str, Any]] = {}
    for c in cells:
        rv, cv = c.get("row_value"), c.get("col_value")
        if rv is None or cv is None:
            continue
        lookup[(round(float(rv), 9), round(float(cv), 9))] = c
    base = next((c for c in cells if c.get("is_base")), None)
    base_val = base.get("value") if base else None
    tolerance = 0.1 if metric == "equity_multiple" else 0.02

    title = (
        f"{_SENS_METRIC_LABEL.get(str(metric), str(metric))} — "
        f"{_SENS_VARIABLE_LABEL.get(str(row_var), str(row_var))} × "
        f"{_SENS_VARIABLE_LABEL.get(str(col_var), str(col_var))}"
    )
    _add_textbox(slide, 6.85, 1.3, 5.5, 0.4, title, 13, True, INK)

    n_cols = len(cols) + 1
    n_rows = len(rows) + 1
    table_shape = slide.shapes.add_table(n_rows, n_cols, Inches(6.85), Inches(1.7), Inches(6.0), Inches(3.5))
    table = table_shape.table

    table.cell(0, 0).text = ""
    table.cell(0, 0).fill.solid()
    table.cell(0, 0).fill.fore_color.rgb = SLATE
    for j, cv in enumerate(cols, start=1):
        cell = table.cell(0, j)
        cell.fill.solid()
        cell.fill.fore_color.rgb = SLATE
        p = cell.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER
        r = p.add_run()
        r.text = _fmt_sens_axis(col_var, cv)
        r.font.color.rgb = WHITE
        r.font.bold = True
        r.font.size = Pt(9)

    for i, rv in enumerate(rows):
        cell = table.cell(i + 1, 0)
        cell.fill.solid()
        cell.fill.fore_color.rgb = GRAY_100
        p = cell.text_frame.paragraphs[0]
        rr = p.add_run()
        rr.text = _fmt_sens_axis(row_var, rv)
        rr.font.bold = True
        rr.font.size = Pt(9)
        for j, cv in enumerate(cols):
            data = lookup.get((round(float(rv), 9), round(float(cv), 9)))
            val = data.get("value") if data else None
            cell = table.cell(i + 1, j + 1)
            cell.fill.solid()
            if val is None or base_val is None:
                cell.fill.fore_color.rgb = WHITE
            elif val >= base_val:
                cell.fill.fore_color.rgb = GREEN_50
            elif val >= base_val - tolerance:
                cell.fill.fore_color.rgb = AMBER_50
            else:
                cell.fill.fore_color.rgb = RED_50
            p = cell.text_frame.paragraphs[0]
            p.alignment = PP_ALIGN.CENTER
            r = p.add_run()
            r.text = _fmt_sens_metric(metric, val)
            r.font.size = Pt(9)
            r.font.bold = bool(data and data.get("is_base"))
            r.font.color.rgb = INK
    _add_textbox(
        slide, 6.85, 5.25, 6.0, 0.3,
        "Source: sensitivity engine (canonical run). Bold = base case; shading relative to base.",
        8, False, INK_500,
    )


def _slide_risk_sensitivity(prs: Presentation, memo: dict[str, Any], model: dict[str, Any]) -> None:
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)
    _add_top_ribbon(slide, "Risk Assessment · Sensitivities")

    # Risk subscores (left)
    risks = []
    for s in memo.get("sections", []):
        if s.get("section_id") == "risk_assessment":
            risks = s.get("subscores", [])
            break

    _add_textbox(slide, 0.5, 1.3, 5.5, 0.4, "Risk Profile", 13, True, INK)
    risk_rows = []
    for r in risks:
        risk_rows.append([
            r.get("name") or refuse(ReasonCode.NO_SOURCE, "risk subscore has no name", "risk.name"),
            r.get("tier") or refuse(ReasonCode.NO_SOURCE, "risk subscore has no tier", "risk.tier"),
            _fmt_int(r.get("score"), "risk.score"),
        ])
    if not risk_rows:
        risk_rows = [[
            unavailable(
                ReasonCode.AWAITING_ANALYST,
                "no risk assessment section on the memo",
                "risk_assessment",
            ),
            "",
            "",
        ]]
    _add_table(slide, 0.5, 1.7, 6.0, 3.5, ["Risk", "Tier", "Score"], risk_rows)

    # Sensitivity heat-map (right) — A1: the sensitivity ENGINE's persisted
    # output, or a sentence saying why there is none. Never synthesized.
    sens = model.get("sensitivity_engine")
    if isinstance(sens, dict) and sens.get("cells") and sens.get("rows") and sens.get("cols"):
        _render_sensitivity_grid(slide, sens)
    else:
        reason = (
            str(sens.get("unavailable_reason"))
            if isinstance(sens, dict) and sens.get("unavailable_reason")
            else "no sensitivity engine output on the export payload"
        )
        refuse(ReasonCode.ENGINE_SKIPPED, reason, "sensitivity")
        _add_textbox(slide, 6.85, 1.3, 5.5, 0.4, "Sensitivities", 13, True, INK)
        _add_textbox(
            slide, 6.85, 1.7, 6.0, 1.0,
            f"Sensitivity analysis not available: {reason}",
            11, False, INK_500,
        )

    # Variance disclosure footer
    var_body = ""
    for s in memo.get("sections", []):
        if s.get("section_id") == "variance_disclosure":
            var_body = s.get("body", "")
            break
    if var_body:
        _add_filled_box(slide, 0.5, 5.5, 12.3, 1.5, AMBER_50, AMBER)
        _add_textbox(slide, 0.7, 5.6, 4, 0.3, "VARIANCE DISCLOSURE", 9, True, AMBER)
        _add_textbox(slide, 0.7, 5.85, 12.0, 1.1, var_body, 11, False, INK)


def _slide_recommendation(prs: Presentation, memo: dict[str, Any], model: dict[str, Any]) -> None:
    blank = prs.slide_layouts[6]
    slide = prs.slides.add_slide(blank)
    _add_top_ribbon(slide, "Recommendation")

    header = memo.get("header", {})
    # The analyst's confirmed IC verdict, or the fact that it is pending —
    # never a default verdict.
    rec_text = header.get("recommendation") or unavailable(
        ReasonCode.AWAITING_ANALYST,
        "no IC recommendation confirmed by the analyst",
        "ic_recommendation",
    )
    pending = not header.get("recommendation") or (
        header.get("recommendation_reason") == ReasonCode.AWAITING_ANALYST.value
    )

    # Big recommendation card
    _add_filled_box(slide, 0.5, 1.3, 12.3, 2.0, AMBER_50 if pending else GREEN_50, AMBER if pending else GREEN_500)
    _add_textbox(slide, 0.8, 1.45, 12, 0.4, "INVESTMENT COMMITTEE RECOMMENDATION", 11, True, AMBER if pending else GREEN_500)
    _add_textbox(slide, 0.8, 1.85, 12, 1.0, rec_text, 30 if not pending else 18, True, INK)

    # Pull rec body
    rec_body = ""
    for s in memo.get("sections", []):
        if s.get("section_id") == "recommendation":
            rec_body = s.get("body", "")
            break
    if rec_body:
        _add_textbox(slide, 0.8, 2.65, 12, 0.6, rec_body, 11, False, INK)

    # A2 — no "Next Steps" block: the deck carries no action list unless the
    # deal supplies one, and today no engine or table does. Nothing generic.

    # Footer signature line — the analyst's name only when the memo names one.
    today = datetime.now(UTC).strftime("%B %d, %Y")
    analyst = header.get("lead_analyst")
    footer = f"Prepared by Fondok AI · {analyst} · {today}" if analyst else f"Prepared by Fondok AI · {today}"
    _add_textbox(slide, 0.5, 6.85, 12.3, 0.3, footer, 9, False, INK_500)


# ─────────────────────────── public API ───────────────────────────


def build_pptx(
    deal: dict[str, Any],
    model: dict[str, Any],
    memo: dict[str, Any],
    output_path: Path,
) -> Path:
    """Build the 8-slide IC presentation deck.

    The build runs inside a refusal collector so every dash / not-available
    the slides write is typed; the deck itself has no refusals page — the
    collector keeps the channel consistent with the workbook and memo.
    """
    with collect_refusals():
        return _build_pptx_slides(deal, model, memo, output_path)


def _build_pptx_slides(
    deal: dict[str, Any],
    model: dict[str, Any],
    memo: dict[str, Any],
    output_path: Path,
) -> Path:
    prs = Presentation()
    # 16:9 slide
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    _slide_title(prs, deal)
    _slide_exec_summary(prs, memo, model)
    _slide_property_market(prs, deal, model)
    _slide_operating(prs, model)
    _slide_sources_uses(prs, model)
    _slide_returns(prs, model)
    _slide_risk_sensitivity(prs, memo, model)
    _slide_recommendation(prs, memo, model)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(output_path))
    return output_path


__all__ = ["build_pptx"]
