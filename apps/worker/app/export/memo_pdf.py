"""IC memo PDF builder.

Renders a 2-5 page Investment Committee memo PDF from a memo dict +
engine outputs dict. Uses WeasyPrint for clean HTML→PDF with CSS support
(see https://doc.courtbouillon.org/weasyprint/).

WeasyPrint requires system libraries: cairo, pango, gdk-pixbuf, and
libffi. On macOS:
    brew install cairo pango gdk-pixbuf libffi
On Debian/Ubuntu:
    apt-get install -y libcairo2 libpango-1.0-0 libpangoft2-1.0-0

──────────────────────────────────────────────────────────────────────
Wave 3 W3.4 — IC memo refresh.

The legacy memo (pre-Wave-2) only carried Executive Summary, Investment
Thesis, Highlights/Risks, Sources & Uses, Returns and Variance. Wave 2
shipped seven new analytical artifacts (revenue segmentation, structured
PIP displacement, three-bucket capex, op-ratio precedence, pricing
sensitivity grid, max-price solver and LOI draft) plus the 3-yr
historical baseline. None of those landed in the exported memo, so an
analyst clicking "Export memo" got a stale view.

This module now pulls every Wave 2 artifact into the memo. Each section
is **conditional** — it renders only when its data is present on the
``model`` dict, so a barebones deal still produces a clean memo.

Section catalog (in render order):

    1.  Header (recommendation chip, AI confidence, deal stage)
    2.  Executive Summary + Thesis + Recommendation paragraphs
    3.  KPI tiles (RevPAR, NOI, Cap Rate, IRR)
    4.  Highlights / Risks two-col
    5.  Revenue Mix              — when ``model["segments_by_year"]`` is set
    6.  Renovation Plan          — when ``model["pip_displacement"]`` is set
    7.  Historical Baseline Walk — when ``model["historical_baseline"]`` set
    8.  Sources & Uses + Returns Summary two-col
    9.  Capital Plan (3-bucket)  — when ``model["capex_schedule"]`` is set
    10. Op-Ratio Provenance      — when ``model["op_ratio_provenance"]`` set
    11. Pricing Sensitivity Grid — when ``model["sensitivity_grid"]`` set
    12. Max-Price Findings       — when ``model["max_price"]`` is set
    13. Variance Disclosure (existing section)
    14. LOI Draft Appendix       — when ``model["loi_draft"]`` is set
    15. Footer (documents reviewed, engines run, drafted-by stamp)

All new sections are pure HTML inline (no new files). Source-tagged where
the underlying data carries provenance (op-ratio precedence renders the
winning source chip per line; segments callout flags ≥15% OTA channel
cost).
"""

from __future__ import annotations

import html
import re
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
.badge.red { background: #DC2626; }
.badge.muted { background: #6B7280; }
h2 {
    font-size: 11pt;
    color: #111827;
    border-bottom: 1pt solid #E5E7EB;
    padding-bottom: 3pt;
    margin: 14pt 0 6pt 0;
}
h3.sub {
    font-size: 10pt;
    color: #1F2937;
    margin: 10pt 0 4pt 0;
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
table.mini tr.ota-flag td {
    background: #FEF3C7;
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

/* ──────── Wave 2 section styling ──────── */
.callout {
    border-left: 3pt solid #2563EB;
    background: #EFF6FF;
    padding: 8pt 12pt;
    border-radius: 4pt;
    margin: 6pt 0 10pt 0;
    font-size: 9pt;
    color: #1E3A8A;
}
.callout strong { color: #1E3A8A; }
.callout.warn {
    border-left-color: #F59E0B;
    background: #FFFBEB;
    color: #92400E;
}
.callout.warn strong { color: #92400E; }
.callout.success {
    border-left-color: #10B981;
    background: #ECFDF5;
    color: #065F46;
}
.callout.success strong { color: #065F46; }

table.grid {
    border-collapse: collapse;
    margin: 6pt 0;
    font-size: 8.5pt;
    width: 100%;
}
table.grid th, table.grid td {
    border: 1pt solid #D1D5DB;
    padding: 4pt 5pt;
    text-align: center;
    font-variant-numeric: tabular-nums;
}
table.grid th {
    background: #1F2937;
    color: white;
    font-weight: 600;
    font-size: 7.5pt;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}
table.grid td.axis {
    background: #F3F4F6;
    color: #374151;
    font-weight: 600;
}
table.grid td.cell-green { background: #BBF7D0; color: #065F46; font-weight: 600; }
table.grid td.cell-amber { background: #FDE68A; color: #92400E; font-weight: 600; }
table.grid td.cell-red { background: #FECACA; color: #991B1B; font-weight: 600; }
.source-tag {
    display: inline-block;
    background: #E0E7FF;
    color: #3730A3;
    border-radius: 99pt;
    padding: 1pt 6pt;
    font-size: 7.5pt;
    font-weight: 600;
    letter-spacing: 0.02em;
    text-transform: uppercase;
}
.source-tag.t12 { background: #DCFCE7; color: #166534; }
.source-tag.portfolio { background: #DBEAFE; color: #1E40AF; }
.source-tag.cbre { background: #FEF3C7; color: #92400E; }
.source-tag.host { background: #E5E7EB; color: #374151; }
.source-tag.override { background: #FCE7F3; color: #9D174D; }
.source-tag.seed { background: #F3F4F6; color: #6B7280; }

/* LOI appendix — markdown-rendered body */
.loi-appendix {
    border-top: 2pt dashed #9CA3AF;
    margin-top: 16pt;
    padding-top: 10pt;
    font-size: 9pt;
}
.loi-appendix h1 {
    font-size: 13pt;
    margin: 6pt 0 10pt 0;
    color: #1F2937;
}
.loi-appendix h2 {
    font-size: 10pt;
    border-bottom: 0;
    margin: 10pt 0 4pt 0;
    color: #1F2937;
}
.loi-appendix p { margin: 0 0 6pt 0; }
.loi-appendix ul { margin: 4pt 0; padding-left: 16pt; }
.loi-appendix hr {
    border: 0;
    border-top: 1pt solid #E5E7EB;
    margin: 8pt 0;
}
"""


# ───────────────────────────────────────────────────────────────────────
# Wave 2 aggregator + per-section renderers.
# ───────────────────────────────────────────────────────────────────────


def _aggregate_wave2_for_memo(model: dict[str, Any]) -> dict[str, Any]:
    """Read Wave 2 engine outputs off the ``model`` dict into a clean shape.

    The memo builder is the only consumer for now; this helper keeps the
    extraction logic out of the template so the same dict can be cached
    and re-used (e.g. by the future ``model["wave2_memo_cache"]`` slot).

    Returns a dict with one key per memo section. Each key holds either
    the section's data (dict / list) OR ``None`` when the underlying
    engine hasn't been run for this deal. Downstream renderers treat
    ``None`` as "skip the section entirely" — that's how we keep the
    barebones-deal memo backward-compatible.
    """
    # ── Revenue segments ─────────────────────────────────────────────
    segments = None
    rev = model.get("revenue_engine") or {}
    seg_by_year = (
        model.get("segments_by_year")
        or rev.get("segments_by_year")
        or rev.get("segment_breakdown")
    )
    # Accept either a flat list (single-year) or a list of years carrying
    # a ``segment_breakdown`` key.
    if isinstance(seg_by_year, list) and seg_by_year:
        first = seg_by_year[0]
        if isinstance(first, dict) and "segment_breakdown" in first:
            # Year 1 segment breakdown is what the memo shows.
            year_one = next(
                (y for y in seg_by_year if y.get("year") == 1), seg_by_year[0]
            )
            segs = year_one.get("segment_breakdown") or []
            if segs:
                segments = list(segs)
        else:
            segments = list(seg_by_year)

    # ── PIP displacement ─────────────────────────────────────────────
    pip = model.get("pip_displacement")
    pip_summary = None
    if pip and isinstance(pip, dict):
        # Treat ``closure_strategy == "none"`` as no PIP displacement
        # for memo purposes; the analyst left it as a placeholder.
        if pip.get("closure_strategy") and pip.get("closure_strategy") != "none":
            pip_summary = {
                "closure_strategy": pip.get("closure_strategy"),
                "schedule": pip.get("pct_rooms_offline_by_month") or [],
                "brand": pip.get("brand"),
                "revpar_index_post_reno": pip.get("revpar_index_post_reno"),
                "occupancy_recovery_months": pip.get("occupancy_recovery_months"),
                "y1_displacement_usd": pip.get("y1_displacement_usd"),
                "y2_recovery_curve": pip.get("y2_recovery_curve") or [],
            }

    # ── Capex 3-bucket schedule ──────────────────────────────────────
    capex_schedule = model.get("capex_schedule")
    if not capex_schedule or not isinstance(capex_schedule, list):
        capex_schedule = None

    # ── Op-ratio provenance ─────────────────────────────────────────
    op_prov = model.get("op_ratio_provenance")
    if not op_prov or not isinstance(op_prov, dict) or not op_prov.get("lines"):
        op_prov = None

    # ── Pricing sensitivity grid ─────────────────────────────────────
    grid = model.get("sensitivity_grid")
    if not grid or not isinstance(grid, dict) or not grid.get("cells"):
        grid = None

    # ── Max-price result ─────────────────────────────────────────────
    max_price = model.get("max_price")
    if not max_price or not isinstance(max_price, dict):
        max_price = None

    # ── Historical baseline + walk ───────────────────────────────────
    baseline = model.get("historical_baseline")
    if not baseline or not isinstance(baseline, dict) or not baseline.get("years"):
        baseline = None
    elif float(baseline.get("coverage_pct") or 0) <= 0:
        # Coverage 0 means no docs uploaded → suppress the section.
        baseline = None

    # ── LOI draft ────────────────────────────────────────────────────
    loi = model.get("loi_draft")
    if not loi or not isinstance(loi, dict) or not loi.get("rendered_markdown"):
        loi = None

    return {
        "segments": segments,
        "pip": pip_summary,
        "capex_schedule": capex_schedule,
        "op_ratio_provenance": op_prov,
        "sensitivity_grid": grid,
        "max_price": max_price,
        "historical_baseline": baseline,
        "loi": loi,
    }


def _render_revenue_mix(segments: list[dict[str, Any]] | None) -> str:
    """Render the Year-1 demand-segment mix table.

    Highlights any segment whose ``channel_cost_pct`` >= 15% (the OTA-heavy
    flag Sam asks IC analysts to surface in the memo body).
    """
    if not segments:
        return ""

    rows: list[str] = []
    ota_flagged = False
    for seg in segments:
        name = html.escape(str(seg.get("name", "—")))
        mix = float(seg.get("mix_pct") or 0)
        net_rev = float(seg.get("net_revenue") or 0)
        channel_cost = float(seg.get("channel_cost_pct") or 0)
        flag = "ota-flag" if channel_cost >= 0.15 else ""
        if flag:
            ota_flagged = True
        rows.append(
            f"<tr class='{flag}'>"
            f"<td>{name}</td>"
            f"<td class='num'>{_fmt_pct(mix)}</td>"
            f"<td class='num'>{_fmt_usd(net_rev)}</td>"
            f"<td class='num'>{_fmt_pct(channel_cost)}</td>"
            f"</tr>"
        )

    ota_note = (
        "<div class='callout warn'><strong>Distribution drag.</strong> "
        "One or more segments carry >=15% channel cost &mdash; flagged amber "
        "for IC review.</div>"
        if ota_flagged
        else ""
    )

    return (
        "<h2>Revenue Mix (Y1)</h2>"
        + ota_note
        + "<table class='mini'>"
        "<thead><tr><th>Segment</th><th style='text-align:right'>Mix %</th>"
        "<th style='text-align:right'>Net Revenue</th>"
        "<th style='text-align:right'>Channel Cost</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _render_pip_plan(pip: dict[str, Any] | None) -> str:
    """Render the Renovation Plan section.

    Includes closure strategy chip + month-by-month offline schedule +
    Y1 displacement $ + Y2 recovery curve. The schedule is rendered as
    inline pct chips so the table fits in a single PDF column.
    """
    if not pip:
        return ""

    strategy_label = {
        "rolling": "Rolling",
        "full_closure": "Full Closure",
        "wing_by_wing": "Wing-by-Wing",
    }.get(pip.get("closure_strategy") or "", str(pip.get("closure_strategy") or "—"))

    sched = pip.get("schedule") or []
    sched_html = "".join(
        f"<td class='num'>{_fmt_pct(p)}</td>" for p in sched
    ) or "<td class='num'>—</td>"

    months_header = "".join(
        f"<th>M{i+1}</th>" for i in range(len(sched) or 12)
    )

    y1_disp = pip.get("y1_displacement_usd")
    y2_curve = pip.get("y2_recovery_curve") or []
    y2_curve_html = (
        "<ul>"
        + "".join(
            f"<li>Month {i+1}: {_fmt_pct(v)} of baseline</li>"
            for i, v in enumerate(y2_curve[:4])
        )
        + "</ul>"
        if y2_curve
        else ""
    )

    brand = pip.get("brand") or "Independent"
    recovery_months = pip.get("occupancy_recovery_months") or "—"
    revpar_uplift = pip.get("revpar_index_post_reno")

    return (
        "<h2>Renovation Plan</h2>"
        f"<div class='callout'>"
        f"<strong>Strategy:</strong> {html.escape(strategy_label)} "
        f"&middot; <strong>Brand:</strong> {html.escape(str(brand))} "
        f"&middot; <strong>Recovery:</strong> {recovery_months} months "
        f"&middot; <strong>Y2+ RevPAR uplift:</strong> "
        f"{_fmt_pct((revpar_uplift or 1.0) - 1.0)}</div>"
        "<h3 class='sub'>Y1 Monthly Offline Schedule</h3>"
        "<table class='mini'>"
        f"<thead><tr>{months_header}</tr></thead>"
        f"<tbody><tr>{sched_html}</tr></tbody></table>"
        f"<p><strong>Y1 displacement:</strong> {_fmt_usd(y1_disp)}</p>"
        + (
            "<h3 class='sub'>Y2 Recovery Curve (first 4 months)</h3>"
            + y2_curve_html
            if y2_curve_html
            else ""
        )
    )


def _render_capex_plan(schedule: list[dict[str, Any]] | None) -> str:
    """Render the three-bucket capex table with year-by-year phasing.

    Buckets: PIP / Non-PIP FF&E / ROI investments. The ROI lift column is
    shown alongside the investment column so analysts can see the lift
    materializing one year after each project lands.
    """
    if not schedule:
        return ""

    rows = []
    tot_pip = tot_non = tot_roi_inv = tot_roi_lift = tot_total = 0.0
    for y in schedule:
        pip_v = float(y.get("pip_usd") or 0)
        non_v = float(y.get("non_pip_usd") or 0)
        roi_inv = float(y.get("roi_investment_usd") or 0)
        roi_lift = float(y.get("roi_noi_lift_usd") or 0)
        total = float(y.get("total_capex_usd") or pip_v + non_v + roi_inv)
        tot_pip += pip_v
        tot_non += non_v
        tot_roi_inv += roi_inv
        tot_roi_lift += roi_lift
        tot_total += total
        rows.append(
            f"<tr><td>Year {y.get('year', '?')}</td>"
            f"<td class='num'>{_fmt_usd(pip_v)}</td>"
            f"<td class='num'>{_fmt_usd(non_v)}</td>"
            f"<td class='num'>{_fmt_usd(roi_inv)}</td>"
            f"<td class='num'>{_fmt_usd(roi_lift)}</td>"
            f"<td class='num'>{_fmt_usd(total)}</td></tr>"
        )
    rows.append(
        "<tr class='total'><td>Total</td>"
        f"<td class='num'>{_fmt_usd(tot_pip)}</td>"
        f"<td class='num'>{_fmt_usd(tot_non)}</td>"
        f"<td class='num'>{_fmt_usd(tot_roi_inv)}</td>"
        f"<td class='num'>{_fmt_usd(tot_roi_lift)}</td>"
        f"<td class='num'>{_fmt_usd(tot_total)}</td></tr>"
    )

    return (
        "<h2>Capital Plan (Three-Bucket)</h2>"
        "<table class='mini'>"
        "<thead><tr><th>Year</th>"
        "<th style='text-align:right'>PIP</th>"
        "<th style='text-align:right'>Non-PIP FF&amp;E</th>"
        "<th style='text-align:right'>ROI Investment</th>"
        "<th style='text-align:right'>ROI NOI Lift</th>"
        "<th style='text-align:right'>Total Capex</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


_SOURCE_DISPLAY = {
    "analyst_override": ("Override", "override"),
    "t12_actual": ("T-12", "t12"),
    "portfolio_pnl": ("Portfolio", "portfolio"),
    "cbre_horizons": ("CBRE", "cbre"),
    "pnl_benchmark": ("HOST", "host"),
    "seed": ("Seed", "seed"),
}


def _render_op_ratio_provenance(op_prov: dict[str, Any] | None) -> str:
    """Render the per-line "which source won" callout for op-ratios.

    Each line has its winning source tag rendered as a coloured chip so
    IC reviewers can audit at-a-glance whether the underwriting is
    anchored on T-12 actuals (preferred), in-house portfolio, CBRE,
    HostStats, or the seed fallback.
    """
    if not op_prov:
        return ""
    lines = op_prov.get("lines") or []
    if not lines:
        return ""

    rows = []
    for line in lines:
        field_name = html.escape(str(line.get("field") or "—"))
        value = line.get("value")
        source = str(line.get("source") or "seed")
        display, tag_class = _SOURCE_DISPLAY.get(source, (source.title(), "seed"))
        doc = line.get("document_id")
        value_html = (
            _fmt_pct(value)
            if isinstance(value, float) and 0 <= value <= 1
            else _fmt_usd(value)
            if value
            else "—"
        )
        cite = (
            f"<div class='cite'>doc: {html.escape(str(doc))}</div>"
            if doc
            else ""
        )
        rows.append(
            f"<tr><td>{field_name}</td>"
            f"<td class='num'>{value_html}</td>"
            f"<td><span class='source-tag {tag_class}'>{display}</span>{cite}</td>"
            "</tr>"
        )

    return (
        "<h2>Operating Ratios &mdash; Provenance</h2>"
        "<div class='callout'><strong>Source precedence:</strong> Override &rarr; "
        "T-12 actuals &rarr; Portfolio P&amp;L &rarr; CBRE Horizons &rarr; HOST &rarr; Seed. "
        "Lower-tier sources are flagged for IC review.</div>"
        "<table class='mini'>"
        "<thead><tr><th>Expense Line</th>"
        "<th style='text-align:right'>Underwritten Value</th>"
        "<th>Winning Source</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _cell_class(
    irr: float, target_irr: float, dscr_breach: bool
) -> str:
    """Map an IRR cell to a green/amber/red class against the target.

    Same scale as the UI: cell >= target -> green; within 200bp below ->
    amber; otherwise red. DSCR breach overrides to red regardless.
    """
    if dscr_breach:
        return "cell-red"
    delta = irr - target_irr
    if delta >= 0:
        return "cell-green"
    if delta >= -0.02:
        return "cell-amber"
    return "cell-red"


def _render_sensitivity_grid(grid: dict[str, Any] | None) -> str:
    """Render the 5x5 pricing sensitivity grid with green/amber/red cells.

    Rows = NOI multiplier (high -> low, top-down). Columns = exit cap
    (low -> high, left-to-right). Each cell shows IRR over EM with the
    going-in cap rate as a subscript caption.
    """
    if not grid:
        return ""
    cells = grid.get("cells") or []
    if not cells:
        return ""

    target_irr = float(grid.get("target_irr") or 0.15)

    # Re-bucket the flat row-major list into a 2-D layout: NOI rows
    # (descending), cap-rate columns (ascending). The aggregator may
    # have already laid them out this way; we re-derive from the
    # individual cell keys to be safe.
    cap_axis = sorted({round(float(c.get("exit_cap_pct") or 0), 6) for c in cells})
    noi_axis = sorted(
        {round(float(c.get("noi_multiplier") or 0), 4) for c in cells},
        reverse=True,
    )
    cell_lookup = {
        (
            round(float(c.get("exit_cap_pct") or 0), 6),
            round(float(c.get("noi_multiplier") or 0), 4),
        ): c
        for c in cells
    }

    header = (
        "<tr><th>NOI &darr; / Cap &rarr;</th>"
        + "".join(f"<th>{_fmt_pct(c)}</th>" for c in cap_axis)
        + "</tr>"
    )

    body_rows = []
    for nm in noi_axis:
        row = [f"<td class='axis'>{nm:.2f}x</td>"]
        for cap in cap_axis:
            cell = cell_lookup.get((cap, nm))
            if cell is None:
                row.append("<td>&mdash;</td>")
                continue
            irr = float(cell.get("levered_irr") or 0)
            em = float(cell.get("equity_multiple") or 0)
            dscr_breach = bool(cell.get("breaches_dscr_floor"))
            cls = _cell_class(irr, target_irr, dscr_breach)
            warn = " !" if dscr_breach else ""
            row.append(
                f"<td class='{cls}'>{_fmt_pct(irr)}<br/>"
                f"<span style='font-size:7pt'>{em:.2f}x{warn}</span></td>"
            )
        body_rows.append("<tr>" + "".join(row) + "</tr>")

    breakeven_cap = grid.get("breakeven_exit_cap_pct")
    breakeven_noi = grid.get("breakeven_noi_multiplier")
    legend = (
        f"<p class='cite'>Cells colored against target IRR "
        f"{_fmt_pct(target_irr)} (green &ge; target, amber within 200bp, "
        f"red below). ! marks DSCR &lt; 1.0x. "
        f"Breakeven exit cap = "
        f"{_fmt_pct(breakeven_cap) if breakeven_cap is not None else 'outside grid'}; "
        f"breakeven NOI multiplier = "
        f"{f'{breakeven_noi:.2f}x' if breakeven_noi is not None else 'outside grid'}."
        "</p>"
    )

    return (
        "<h2>Pricing Sensitivity (5x5 Grid)</h2>"
        + f"<table class='grid'>{header}{''.join(body_rows)}</table>"
        + legend
    )


def _render_max_price_callout(max_price: dict[str, Any] | None) -> str:
    """Render the max-price-for-target-return callout with the binding chip."""
    if not max_price:
        return ""
    irr_price = max_price.get("max_price_for_irr")
    em_price = max_price.get("max_price_for_em")
    target_irr = max_price.get("target_irr") or 0.15
    target_em = max_price.get("target_em") or 1.8
    binding = str(max_price.get("binding_constraint") or "irr").upper()
    per_key = max_price.get("final_price_per_key") or 0

    badge_cls = "warn" if binding in ("IRR", "EM") else "muted"

    return (
        "<h2>Max-Price Findings</h2>"
        f"<div class='callout success'>"
        f"<strong>Max price for {_fmt_pct(target_irr)} IRR:</strong> "
        f"{_fmt_usd(irr_price)}<br/>"
        f"<strong>Max price for {target_em:.2f}x EM:</strong> "
        f"{_fmt_usd(em_price)}<br/>"
        f"<strong>Binding constraint:</strong> "
        f"<span class='badge {badge_cls}'>{html.escape(binding)}</span> "
        f"&nbsp; <strong>Headline price/key:</strong> "
        f"{_fmt_usd(per_key)}"
        "</div>"
    )


def _render_historical_walk(baseline: dict[str, Any] | None) -> str:
    """Render the historical baseline + top YoY swings table."""
    if not baseline:
        return ""
    years = baseline.get("years") or []
    walk = baseline.get("walk") or []
    coverage = float(baseline.get("coverage_pct") or 0)

    if not years:
        return ""

    # Build a wide table: rows = walk lines, columns = fiscal years.
    fys = sorted({int(y.get("fiscal_year")) for y in years if y.get("fiscal_year")})
    line_keys = (
        "total_revenue", "noi", "rooms_revenue", "gop"
    )
    year_lookup = {int(y["fiscal_year"]): y for y in years if y.get("fiscal_year")}

    header = (
        "<tr><th>Line</th>"
        + "".join(f"<th>FY{fy}</th>" for fy in fys)
        + "</tr>"
    )
    body_rows = []
    for line in line_keys:
        cells = []
        for fy in fys:
            v = year_lookup.get(fy, {}).get(line)
            cells.append(f"<td class='num'>{_fmt_usd(v)}</td>")
        body_rows.append(
            f"<tr><td>{html.escape(line.replace('_', ' ').title())}</td>"
            + "".join(cells)
            + "</tr>"
        )

    walk_chips = ""
    if walk:
        chips = []
        for d in walk[:6]:
            pct = d.get("yoy_pct")
            if pct is None:
                continue
            line = html.escape(str(d.get("line") or ""))
            yr = d.get("year") or ""
            cls = "ok" if pct > 0 else "warn"
            chips.append(
                f"<span class='badge {cls}' style='margin-bottom:2pt'>"
                f"{line.replace('_', ' ').title()} FY{yr}: "
                f"{pct*100:+.1f}%</span>"
            )
        walk_chips = (
            "<h3 class='sub'>Top YoY Swings</h3>"
            + "<div>" + " ".join(chips) + "</div>"
            if chips
            else ""
        )

    return (
        "<h2>Historical Baseline Walk</h2>"
        f"<div class='callout'>"
        f"<strong>Coverage:</strong> {_fmt_pct(coverage)} of look-back "
        f"window ({len(years)} of {baseline.get('look_back_years') or 5} years)."
        "</div>"
        f"<table class='mini'>{header}{''.join(body_rows)}</table>"
        + walk_chips
    )


# ── Tiny markdown to HTML (LOI appendix) ──────────────────────────────


def _markdown_to_html(md: str) -> str:
    """Convert the LOI markdown body to inline HTML.

    Scope: just enough to render ``loi_generator.py`` output (headings,
    bold via ``**``, ordered/unordered lists, ``---`` hr, paragraphs).
    Avoids an external markdown dependency so the worker venv stays
    light - WeasyPrint already pulls in pyphen + Pillow.
    """
    lines = md.splitlines()
    out: list[str] = []
    in_list = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    def render_inline(s: str) -> str:
        escaped = html.escape(s)
        # Bold: **text**
        escaped = re.sub(
            r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped
        )
        return escaped

    para_buf: list[str] = []

    def flush_para() -> None:
        if para_buf:
            out.append(f"<p>{render_inline(' '.join(para_buf))}</p>")
            para_buf.clear()

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            close_list()
            flush_para()
            continue
        if line.strip() == "---":
            close_list()
            flush_para()
            out.append("<hr/>")
            continue
        if line.startswith("# "):
            close_list()
            flush_para()
            out.append(f"<h1>{render_inline(line[2:].strip())}</h1>")
            continue
        if line.startswith("## "):
            close_list()
            flush_para()
            out.append(f"<h2>{render_inline(line[3:].strip())}</h2>")
            continue
        if line.startswith("### "):
            close_list()
            flush_para()
            out.append(f"<h3>{render_inline(line[4:].strip())}</h3>")
            continue
        # Bulleted list (allow leading whitespace then "- ")
        stripped = line.lstrip()
        if stripped.startswith("- "):
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{render_inline(stripped[2:])}</li>")
            continue
        # Paragraph continuation.
        close_list()
        para_buf.append(line.strip())

    close_list()
    flush_para()
    return "\n".join(out)


def _render_loi_appendix(loi: dict[str, Any] | None) -> str:
    """Render the LOI markdown body as the final appendix section."""
    if not loi:
        return ""
    md = loi.get("rendered_markdown") or ""
    if not md.strip():
        return ""
    body_html = _markdown_to_html(md)
    return (
        "<div class='loi-appendix'>"
        "<h2 style='border-bottom:2pt solid #1F2937'>Appendix &mdash; Draft Letter of Intent</h2>"
        f"{body_html}"
        "</div>"
    )


# ───────────────────────────────────────────────────────────────────────
# Main template entrypoint.
# ───────────────────────────────────────────────────────────────────────


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

    # Wave 2 aggregator — single pass, then conditional renderers.
    wave2 = _aggregate_wave2_for_memo(model)

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

    # Wave 2 conditional section HTML.
    segments_html = _render_revenue_mix(wave2["segments"])
    pip_html = _render_pip_plan(wave2["pip"])
    capex_html = _render_capex_plan(wave2["capex_schedule"])
    op_ratio_html = _render_op_ratio_provenance(wave2["op_ratio_provenance"])
    sensitivity_html = _render_sensitivity_grid(wave2["sensitivity_grid"])
    max_price_html = _render_max_price_callout(wave2["max_price"])
    historical_html = _render_historical_walk(wave2["historical_baseline"])
    loi_html = _render_loi_appendix(wave2["loi"])

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

{segments_html}

{pip_html}

{historical_html}

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

{capex_html}

{op_ratio_html}

{sensitivity_html}

{max_price_html}

{variance_html}

{loi_html}

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


__all__ = [
    "_aggregate_wave2_for_memo",
    "_markdown_to_html",
    "_render_capex_plan",
    "_render_historical_walk",
    "_render_html",
    "_render_loi_appendix",
    "_render_max_price_callout",
    "_render_op_ratio_provenance",
    "_render_pip_plan",
    "_render_revenue_mix",
    "_render_sensitivity_grid",
    "build_memo_pdf",
]
