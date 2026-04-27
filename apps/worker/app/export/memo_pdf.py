"""IC memo PDF builder.

Renders a 2-3 page Investment Committee memo PDF from a memo dict +
engine outputs dict. Uses WeasyPrint for clean HTML→PDF with CSS support
(see https://doc.courtbouillon.org/weasyprint/).

WeasyPrint requires system libraries: cairo, pango, gdk-pixbuf, and
libffi. On macOS:
    brew install cairo pango gdk-pixbuf libffi
On Debian/Ubuntu:
    apt-get install -y libcairo2 libpango-1.0-0 libpangoft2-1.0-0
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# WeasyPrint must be importable. We import lazily inside build_memo_pdf
# so module import doesn't blow up when system libs are missing.


def _fmt_usd(v: float | int | None, scale: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v) / scale:,.0f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_usd_m(v: float | int | None) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v) / 1_000_000:.2f}M"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_mult(v: float | None) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.2f}x"
    except (TypeError, ValueError):
        return "—"


CSS = """
@page {
    size: Letter;
    margin: 0.5in 0.6in;
    @bottom-left {
        content: "Fondok AI · Investment Committee Memorandum";
        font-size: 8pt;
        color: #6B7280;
    }
    @bottom-right {
        content: "Page " counter(page) " of " counter(pages);
        font-size: 8pt;
        color: #6B7280;
    }
}
* { box-sizing: border-box; }
body {
    font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
    font-size: 9.5pt;
    color: #111827;
    line-height: 1.45;
    margin: 0;
}
.header {
    background: #1F2937;
    color: white;
    padding: 14pt 16pt;
    margin: 0 0 14pt 0;
    border-radius: 4pt;
}
.header .label {
    font-size: 8pt;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #F59E0B;
    font-weight: 600;
}
.header h1 {
    font-size: 18pt;
    margin: 4pt 0 2pt 0;
    color: white;
}
.header .meta {
    font-size: 9pt;
    color: #D1D5DB;
}
.badges {
    margin-top: 6pt;
}
.badge {
    display: inline-block;
    background: rgba(255,255,255,0.12);
    color: white;
    padding: 2pt 8pt;
    border-radius: 99pt;
    font-size: 8pt;
    margin-right: 4pt;
    font-weight: 600;
}
.badge.ok { background: #10B981; }
.badge.warn { background: #F59E0B; }
.badge.amber { background: #F59E0B; }
h2 {
    font-size: 11pt;
    color: #111827;
    border-bottom: 1pt solid #E5E7EB;
    padding-bottom: 3pt;
    margin: 14pt 0 6pt 0;
}
.exec p {
    margin: 0 0 6pt 0;
    color: #374151;
}
.metrics {
    display: flex;
    gap: 6pt;
    margin: 8pt 0 14pt 0;
}
.metric {
    flex: 1;
    border: 1pt solid #E5E7EB;
    border-radius: 4pt;
    padding: 8pt;
    text-align: center;
}
.metric .k { font-size: 7.5pt; color: #6B7280; text-transform: uppercase; letter-spacing: 0.06em; }
.metric .v { font-size: 14pt; font-weight: 700; color: #1F2937; margin-top: 3pt; }
.two-col {
    display: flex;
    gap: 10pt;
    margin: 8pt 0;
}
.col {
    flex: 1;
    border-radius: 4pt;
    padding: 10pt 12pt;
}
.col.highlights {
    background: #ECFDF5;
    border-left: 3pt solid #10B981;
}
.col.risks {
    background: #FFFBEB;
    border-left: 3pt solid #F59E0B;
}
.col h3 {
    margin: 0 0 6pt 0;
    font-size: 10pt;
    color: #111827;
}
.col ul {
    margin: 0;
    padding-left: 14pt;
    font-size: 9pt;
}
.col li { margin-bottom: 3pt; color: #374151; }
table.mini {
    width: 100%;
    border-collapse: collapse;
    font-size: 9pt;
    margin: 6pt 0;
}
table.mini th, table.mini td {
    text-align: left;
    padding: 4pt 6pt;
    border-bottom: 1pt solid #E5E7EB;
}
table.mini th {
    background: #F3F4F6;
    color: #374151;
    font-weight: 600;
    font-size: 8pt;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
table.mini td.num { text-align: right; font-variant-numeric: tabular-nums; }
table.mini tr.total td {
    background: #FEF3C7;
    font-weight: 700;
}
.footer-note {
    font-size: 8pt;
    color: #6B7280;
    margin-top: 10pt;
    border-top: 1pt solid #E5E7EB;
    padding-top: 6pt;
}
.cite { font-size: 7.5pt; color: #6B7280; margin-top: 2pt; }
.section-grid {
    display: flex;
    gap: 10pt;
    margin: 8pt 0;
}
.section-grid > div { flex: 1; }
"""


def _render_html(memo: dict[str, Any], model: dict[str, Any]) -> str:
    header = memo.get("header", {})
    sections = memo.get("sections", [])
    appendix = memo.get("appendix", {}) or {}

    def find_section(sid: str) -> dict[str, Any]:
        for s in sections:
            if s.get("section_id") == sid:
                return s
        return {}

    exec_summary = find_section("executive_summary")
    thesis = find_section("investment_thesis")
    recommendation = find_section("recommendation")
    insights = find_section("key_insights").get("items", [])
    risks_section = find_section("risk_assessment")
    variance_section = find_section("variance_disclosure")

    inv = model.get("investment_engine", {})
    ret = model.get("returns_engine", {})
    debt = model.get("debt_engine", {})
    pl = model.get("p_and_l_engine_proforma", {})
    lines = pl.get("lines", [])

    # Pull NOI from Y1 of the proforma if available
    noi_y1 = next((row.get("y1") for row in lines if row.get("label") == "Net Operating Income"), None)

    # RevPAR — derive if we have keys + room revenue
    keys = model.get("keys") or 132
    room_rev_y1 = next((row.get("y1") for row in lines if row.get("label") == "Room Revenue"), None)
    revpar_y1 = (room_rev_y1 * 1000 / (keys * 365)) if room_rev_y1 and keys else None

    title = header.get("title", "Investment Committee Memorandum")
    subject = header.get("subject_property", "Hotel")
    location = header.get("location", "")
    rec = header.get("recommendation", "")
    confidence = appendix.get("ai_confidence", 0)

    # Highlights = top 4 insights
    highlight_items = "".join(
        f"<li><strong>{html.escape(i.get('title', ''))}</strong> — {html.escape(i.get('body', ''))}</li>"
        for i in insights[:4]
    )

    # Risks = subscores
    risk_items = "".join(
        f"<li><strong>{html.escape(s.get('name', ''))}</strong> — {html.escape(s.get('tier', ''))} ({s.get('score', 0)})</li>"
        for s in risks_section.get("subscores", [])[:4]
    )

    # Highlights default fallback
    if not highlight_items:
        highlight_items = "<li>—</li>"
    if not risk_items:
        risk_items = "<li>—</li>"

    # Sources & Uses mini table
    sources = model.get("sources") or [
        {"label": "Senior Debt", "amount": debt.get("loan_amount_usd", 0)},
        {"label": "Equity", "amount": inv.get("total_capital_usd", 0) - debt.get("loan_amount_usd", 0)},
    ]
    sources_rows = ""
    src_total = 0
    for s in sources:
        if s.get("total"):
            continue
        amt = s.get("amount", 0)
        src_total += amt
        sources_rows += (
            f"<tr><td>{html.escape(str(s.get('label', '')))}</td>"
            f"<td class='num'>{_fmt_usd(amt)}</td></tr>"
        )
    sources_rows += (
        f"<tr class='total'><td>Total</td>"
        f"<td class='num'>{_fmt_usd(src_total)}</td></tr>"
    )

    # Returns mini table
    returns_rows = "".join([
        f"<tr><td>Levered IRR</td><td class='num'>{_fmt_pct(ret.get('levered_irr'))}</td></tr>",
        f"<tr><td>Equity Multiple</td><td class='num'>{_fmt_mult(ret.get('equity_multiple'))}</td></tr>",
        f"<tr><td>Year 1 CoC</td><td class='num'>{_fmt_pct(ret.get('year1_cash_on_cash'))}</td></tr>",
        f"<tr><td>Hold Period</td><td class='num'>{ret.get('hold_years', 0)} yrs</td></tr>",
        f"<tr><td>Year 1 DSCR</td><td class='num'>{_fmt_mult(debt.get('year1_dscr'))}</td></tr>",
        f"<tr><td>Year 1 Debt Yield</td><td class='num'>{_fmt_pct(debt.get('year1_debt_yield'))}</td></tr>",
    ])

    # Citations footer
    docs = appendix.get("documents_reviewed", [])
    docs_html = ", ".join(html.escape(d) for d in docs[:6])

    drafted_at = memo.get("drafted_at", datetime.now(UTC).isoformat())

    # Variance disclosure
    variance_html = ""
    if variance_section:
        variance_html = (
            f"<h2>Variance Disclosure</h2>"
            f"<p style='margin:0;color:#374151;'>{html.escape(variance_section.get('body', ''))}</p>"
        )

    # Combined exec summary text
    exec_para = html.escape(exec_summary.get("body", ""))
    thesis_para = html.escape(thesis.get("body", ""))
    rec_para = html.escape(recommendation.get("body", ""))

    rec_badge_class = "ok" if "PROCEED" in rec.upper() else "warn"

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>

<div class="header">
  <div class="label">Investment Committee Memorandum</div>
  <h1>{html.escape(subject)}</h1>
  <div class="meta">{html.escape(location)} · {keys} Keys</div>
  <div class="badges">
    <span class="badge {rec_badge_class}">{html.escape(rec or 'IN REVIEW')}</span>
    <span class="badge">AI Confidence: {int(confidence * 100)}%</span>
    <span class="badge">{html.escape(header.get('deal_stage', ''))}</span>
  </div>
</div>

<h2>Executive Summary</h2>
<div class="exec">
  <p>{exec_para}</p>
  <p>{thesis_para}</p>
  <p>{rec_para}</p>
</div>

<div class="metrics">
  <div class="metric"><div class="k">RevPAR (Y1)</div><div class="v">{("$" + f"{revpar_y1:,.0f}") if revpar_y1 else "—"}</div></div>
  <div class="metric"><div class="k">NOI (Y1)</div><div class="v">{_fmt_usd_m((noi_y1 or 0) * 1000) if noi_y1 else "—"}</div></div>
  <div class="metric"><div class="k">Cap Rate</div><div class="v">{_fmt_pct(inv.get('entry_cap_rate_year1_uw'))}</div></div>
  <div class="metric"><div class="k">Levered IRR</div><div class="v">{_fmt_pct(ret.get('levered_irr'))}</div></div>
</div>

<div class="two-col">
  <div class="col highlights">
    <h3>Investment Highlights</h3>
    <ul>{highlight_items}</ul>
  </div>
  <div class="col risks">
    <h3>Key Risks</h3>
    <ul>{risk_items}</ul>
  </div>
</div>

<div class="section-grid">
  <div>
    <h2>Sources &amp; Uses</h2>
    <table class="mini">
      <thead><tr><th>Source</th><th style="text-align:right">Amount</th></tr></thead>
      <tbody>{sources_rows}</tbody>
    </table>
  </div>
  <div>
    <h2>Returns Summary</h2>
    <table class="mini">
      <thead><tr><th>Metric</th><th style="text-align:right">Value</th></tr></thead>
      <tbody>{returns_rows}</tbody>
    </table>
  </div>
</div>

{variance_html}

<div class="footer-note">
  <strong>Documents reviewed:</strong> {docs_html or '—'}<br/>
  <strong>Engines run:</strong> {", ".join(html.escape(e) for e in appendix.get("engines_run", []))}<br/>
  Drafted by Fondok AI · {html.escape(drafted_at)}
</div>

</body>
</html>
"""


def build_memo_pdf(memo: dict[str, Any], model: dict[str, Any], output_path: Path) -> Path:
    """Render the IC memo to PDF via WeasyPrint."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html_str = _render_html(memo, model)

    # Lazy import to keep module import safe even when WeasyPrint's
    # native deps aren't installed.
    from weasyprint import HTML  # type: ignore[import-untyped]

    HTML(string=html_str).write_pdf(str(output_path))
    return output_path


__all__ = ["build_memo_pdf"]
