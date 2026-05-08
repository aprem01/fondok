"""Generate the three external-report mock PDFs used by the Market tab
and forward-projection engine.

Outputs (under ``apps/web/public/test-documents/``):
  * ``sample_STR_TREND.pdf``     — Miami Beach lifestyle subject + 7
                                   competitors, RGI 1.05 / ARI 1.08 /
                                   MPI 0.97 (subject slightly
                                   overperforming on rate and revenue).
  * ``sample_CBRE_Horizons.pdf`` — Miami Beach upper-upscale 5-year
                                   forecast: occupancy stable at
                                   76-78%, ADR ~3% CAGR, RevPAR
                                   3-4% growth.
  * ``sample_PNL_Benchmark.pdf`` — Miami Beach lifestyle peer set
                                   (n=18): rooms expense 25% of rooms
                                   revenue, F&B margin 30%, GOP
                                   margin 38%.

Usage::

    cd apps/worker
    .venv/bin/python scripts/gen_external_reports.py

The script is deterministic — running it twice produces the same
bytes (modulo PDF metadata timestamps), so PR diffs against the
generated PDFs stay tractable.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
OUT_DIR = REPO_ROOT / "apps" / "web" / "public" / "test-documents"


# ─────────────────────── shared styles ───────────────────────


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    title = ParagraphStyle(
        "TitleBig",
        parent=base["Title"],
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#0F2747"),
        spaceAfter=8,
        alignment=0,
    )
    subtitle = ParagraphStyle(
        "Subtitle",
        parent=base["Heading2"],
        fontSize=13,
        leading=16,
        textColor=colors.HexColor("#1F4470"),
        spaceAfter=4,
    )
    meta = ParagraphStyle(
        "Meta",
        parent=base["BodyText"],
        fontSize=9,
        textColor=colors.HexColor("#666666"),
        spaceAfter=2,
    )
    h2 = ParagraphStyle(
        "H2",
        parent=base["Heading2"],
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#0F2747"),
        spaceBefore=14,
        spaceAfter=6,
    )
    body = ParagraphStyle(
        "Body",
        parent=base["BodyText"],
        fontSize=10,
        leading=13,
        spaceAfter=6,
    )
    foot = ParagraphStyle(
        "Foot",
        parent=base["BodyText"],
        fontSize=8,
        textColor=colors.HexColor("#888888"),
        spaceBefore=12,
    )
    return {
        "title": title,
        "subtitle": subtitle,
        "meta": meta,
        "h2": h2,
        "body": body,
        "foot": foot,
    }


def _table(rows: list[list[str]], *, header: bool = True) -> Table:
    t = Table(rows, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style.extend(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F0F2F5")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#0F2747")),
            ]
        )
    t.setStyle(TableStyle(style))
    return t


def _build(path: Path, story: list) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.7 * inch,
        bottomMargin=0.7 * inch,
    )
    doc.build(story)
    print(f"  wrote {path.relative_to(REPO_ROOT)} ({path.stat().st_size:,} bytes)")


# ─────────────────────── STR TREND ───────────────────────


def gen_str_trend() -> Path:
    """Miami Beach lifestyle: subject is the Coral Bay Resort, with 7
    boutique / lifestyle competitors and a slight rate-led
    overperformance (RGI 1.05, ARI 1.08, MPI 0.97).
    """
    s = _styles()
    out = OUT_DIR / "sample_STR_TREND.pdf"

    # Subject: occupancy 75.4% / ADR $312 / RevPAR $235.
    # Comp-set average occ 77.6% / ADR $289 / RevPAR $224.
    # → MPI = 0.754/0.776 = 0.972; ARI = 312/289 = 1.080; RGI = 235/224 = 1.049.
    subject = {
        "name": "Coral Bay Resort (Subject)",
        "keys": 248,
        "occ": "75.4%",
        "adr": "$312",
        "revpar": "$235",
    }
    competitors = [
        {"n": 1, "name": "Kimpton Surfcomber Hotel",   "keys": 186, "occ": "78.1%", "adr": "$278", "revpar": "$217"},
        {"n": 2, "name": "1 Hotel South Beach",        "keys": 426, "occ": "79.4%", "adr": "$345", "revpar": "$274"},
        {"n": 3, "name": "The Betsy Hotel",            "keys": 127, "occ": "76.8%", "adr": "$295", "revpar": "$227"},
        {"n": 4, "name": "Hotel Victor",               "keys":  91, "occ": "74.9%", "adr": "$268", "revpar": "$201"},
        {"n": 5, "name": "The Plymouth Hotel",         "keys":  98, "occ": "77.2%", "adr": "$251", "revpar": "$194"},
        {"n": 6, "name": "Royal Palm South Beach",     "keys": 393, "occ": "78.5%", "adr": "$284", "revpar": "$223"},
        {"n": 7, "name": "Cadillac Hotel & Beach Club","keys": 357, "occ": "78.4%", "adr": "$298", "revpar": "$234"},
    ]
    total_keys = sum(c["keys"] for c in competitors)

    story = [
        Paragraph("STR TREND REPORT", s["title"]),
        Paragraph("Coral Bay Resort vs Competitive Set — Miami Beach", s["subtitle"]),
        Paragraph("Smith Travel Research — TTM as of March 31, 2026", s["meta"]),
        Paragraph("Confidential — STR Property Code: 56821", s["meta"]),
        Spacer(1, 0.18 * inch),

        Paragraph("Competitive Set", s["h2"]),
        Paragraph(
            "Comp set comprises 7 lifestyle / boutique hotels in the South of Fifth and "
            "Mid-Beach districts, selected on geographic proximity, service tier, and "
            "target leisure / lifestyle segment.",
            s["body"],
        ),
        _table(
            [["#", "Property", "Keys"]]
            + [[str(c["n"]), c["name"], str(c["keys"])] for c in competitors]
            + [["", "Comp Set Total", f"{total_keys:,}"]],
        ),
        Spacer(1, 0.12 * inch),

        Paragraph("TTM Performance — Subject vs Comp Set", s["h2"]),
        _table(
            [
                ["Property", "Keys", "Occupancy", "ADR", "RevPAR"],
                [subject["name"], str(subject["keys"]), subject["occ"], subject["adr"], subject["revpar"]],
            ]
            + [
                [c["name"], str(c["keys"]), c["occ"], c["adr"], c["revpar"]]
                for c in competitors
            ]
            + [["Comp Set Average", "—", "77.6%", "$289", "$224"]],
        ),
        Spacer(1, 0.12 * inch),

        Paragraph("Penetration Indices (TTM)", s["h2"]),
        _table(
            [
                ["Index", "Value", "Reading"],
                ["RGI (Revenue Generation)", "1.05",
                 "Subject earns 105% of comp-set RevPAR — modest revenue overperformance."],
                ["ARI (Average Rate)",        "1.08",
                 "Subject's ADR is 108% of comp-set average — strongest pricing in segment."],
                ["MPI (Market Penetration)",  "0.97",
                 "Subject captures 97% of fair-share occupancy — slight demand drag."],
            ],
        ),
        Spacer(1, 0.10 * inch),
        Paragraph(
            "<b>Bottom line:</b> Pricing premium (ARI 1.08) more than offsets a small "
            "occupancy gap (MPI 0.97), driving overall RevPAR premium of ~5% (RGI 1.05). "
            "Yield curve indicates room for ADR expansion before MPI compresses further.",
            s["body"],
        ),

        Paragraph(
            "STR Disclaimer: This report is for the exclusive use of the subscriber and "
            "may not be reproduced without permission. Smith Travel Research, LLC. www.str.com",
            s["foot"],
        ),
    ]
    _build(out, story)
    return out


# ─────────────────────── CBRE HORIZONS ───────────────────────


def gen_cbre_horizons() -> Path:
    """Miami Beach upper-upscale 5-year forecast.

    Year 1 baseline: occ 76.0%, ADR $295, RevPAR $224.20.
    ADR CAGR ~3.0%, occupancy stable in the 76-78% band.
    RevPAR growth 3-4% per year (inflation-tracking).
    """
    s = _styles()
    out = OUT_DIR / "sample_CBRE_Horizons.pdf"

    # Hand-tuned to land RevPAR growth in the 3-4% range with a slight
    # occupancy uptick year 2-4 then a flat year 5 (cycle softening).
    years = [
        {"n": 1, "year": 2026, "occ": 0.760, "adr": 295.00},
        {"n": 2, "year": 2027, "occ": 0.770, "adr": 304.00},
        {"n": 3, "year": 2028, "occ": 0.778, "adr": 313.00},
        {"n": 4, "year": 2029, "occ": 0.778, "adr": 322.00},
        {"n": 5, "year": 2030, "occ": 0.776, "adr": 331.50},
    ]
    rows = []
    prev_revpar = None
    for y in years:
        revpar = y["occ"] * y["adr"]
        growth = ((revpar - prev_revpar) / prev_revpar) if prev_revpar else None
        y["revpar"] = revpar
        y["growth"] = growth
        rows.append(
            [
                f"Year {y['n']} ({y['year']})",
                f"{y['occ'] * 100:.1f}%",
                f"${y['adr']:.2f}",
                f"${revpar:.2f}",
                "—" if growth is None else f"{growth * 100:.1f}%",
            ]
        )
        prev_revpar = revpar

    story = [
        Paragraph("CBRE HORIZONS — HOTEL FORECAST", s["title"]),
        Paragraph("Miami Beach Submarket — Upper Upscale Chain Scale", s["subtitle"]),
        Paragraph("Publication Date: April 18, 2026", s["meta"]),
        Paragraph("CBRE Hotels Research — Confidential to Subscriber", s["meta"]),
        Spacer(1, 0.18 * inch),

        Paragraph("Forecast Summary", s["h2"]),
        Paragraph(
            "Five-year outlook for the Miami Beach Upper Upscale segment. "
            "Forecast assumes continued leisure-led demand normalization, group "
            "recovery completing by year 2, and ADR growth tracking core inflation "
            "after the year-1 reset. RevPAR CAGR over the forecast horizon is 3.4%, "
            "with occupancy stable in the 76-78% band.",
            s["body"],
        ),

        Paragraph("Annual Forecast — Miami Beach Upper Upscale", s["h2"]),
        _table(
            [["Period", "Occupancy", "ADR", "RevPAR", "RevPAR Growth"]] + rows,
        ),
        Spacer(1, 0.12 * inch),

        Paragraph("Key Drivers", s["h2"]),
        Paragraph(
            "• <b>ADR:</b> 3.0% CAGR; pricing power preserved by limited new supply "
            "(South Beach historic district).<br/>"
            "• <b>Occupancy:</b> stable 76-78%; demand growth absorbed by 1-2% supply growth.<br/>"
            "• <b>RevPAR growth:</b> 3-4% per year, modestly above market consensus.<br/>"
            "• <b>Risk:</b> insurance / operating cost inflation may compress flow-through "
            "even as RevPAR grows.",
            s["body"],
        ),

        Paragraph(
            "© 2026 CBRE, Inc. CBRE Hotels Research. All rights reserved. This forecast "
            "represents CBRE's view at the publication date; actual performance may vary.",
            s["foot"],
        ),
    ]
    _build(out, story)
    return out


# ─────────────────────── P&L BENCHMARK ───────────────────────


def gen_pnl_benchmark() -> Path:
    """HotStats-style line-item benchmark for a Miami Beach lifestyle peer set.

    Peer set n=18. Headline ratios:
      * Rooms department expense 25% of rooms revenue
      * F&B margin 30% (so F&B dept expense = 70% of F&B revenue)
      * GOP margin 38%
      * A&G 7.5%, Sales/Marketing 6.0%, Utilities 3.8%
      * Property taxes 4.2%, Insurance 2.6%
    """
    s = _styles()
    out = OUT_DIR / "sample_PNL_Benchmark.pdf"

    margins = [
        ("Rooms Departmental Expense (% rooms revenue)", "25.0%"),
        ("F&B Departmental Margin",                       "30.0%"),
        ("GOP Margin (% total revenue)",                  "38.0%"),
        ("A&G (% total revenue)",                         "7.5%"),
        ("Sales & Marketing (% total revenue)",           "6.0%"),
        ("Utilities (% total revenue)",                   "3.8%"),
        ("Property Taxes (% total revenue)",              "4.2%"),
        ("Insurance (% total revenue)",                   "2.6%"),
    ]
    par_por = [
        ("Rooms Revenue PAR (per available room, annual)",   "$78,400"),
        ("Total Revenue PAR (per available room, annual)",   "$112,800"),
        ("NOI PAR (per available room, annual)",             "$28,150"),
        ("Rooms Revenue POR (per occupied room)",            "$285.20"),
        ("F&B Revenue POR (per occupied room)",              "$94.50"),
    ]

    story = [
        Paragraph("HOTEL P&L BENCHMARK", s["title"]),
        Paragraph("Miami Beach Lifestyle Peer Set — TTM March 2026", s["subtitle"]),
        Paragraph("Source: Industry P&L Benchmark Database (HotStats-equivalent)", s["meta"]),
        Paragraph("Peer Set Size: 18 hotels (lifestyle + upper-upscale, Miami Beach)", s["meta"]),
        Spacer(1, 0.18 * inch),

        Paragraph("Margin & Expense Ratios", s["h2"]),
        Paragraph(
            "Aggregate ratios are equal-weighted across the 18-property peer set. "
            "All percentages are stated against the parent revenue line "
            "(rooms-dept against rooms revenue; A&G / S&M / utilities / fixed "
            "charges against total revenue).",
            s["body"],
        ),
        _table([["Line Item", "Peer Set Median"]] + [[k, v] for k, v in margins]),
        Spacer(1, 0.12 * inch),

        Paragraph("Per-Room Productivity (PAR & POR)", s["h2"]),
        Paragraph(
            "<b>PAR</b> = per available room (annualized; total ÷ keys). "
            "<b>POR</b> = per occupied room (revenue ÷ occupied roomnights).",
            s["body"],
        ),
        _table([["Metric", "Peer Set Median"]] + [[k, v] for k, v in par_por]),
        Spacer(1, 0.12 * inch),

        Paragraph("Reading the Benchmark", s["h2"]),
        Paragraph(
            "• A subject hotel running rooms-dept expense above 27% is a flag for "
            "labor-cost or productivity drift.<br/>"
            "• F&B margin in this peer set is unusually strong — driven by amenity "
            "fees and grab-and-go formats; full-service F&B alone runs closer to 18-22%.<br/>"
            "• GOP margin of 38% is at the high end of the lifestyle range; full-service "
            "comparables typically sit at 32-36%.<br/>"
            "• PAR / POR figures normalize across keys + occupancy; use them to compare "
            "the subject to peer set without size distortion.",
            s["body"],
        ),

        Paragraph(
            "Industry P&L Benchmark — provided to subscribers of the Miami Beach "
            "lifestyle peer cut. Confidential. n=18 hotels, TTM March 2026.",
            s["foot"],
        ),
    ]
    _build(out, story)
    return out


# ─────────────────────── entry point ───────────────────────


def main() -> None:
    print(f"Generating external-report mock PDFs into {OUT_DIR}")
    gen_str_trend()
    gen_cbre_horizons()
    gen_pnl_benchmark()
    print("Done.")


if __name__ == "__main__":
    main()
