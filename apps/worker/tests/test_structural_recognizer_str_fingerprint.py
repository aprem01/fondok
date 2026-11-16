"""FON-123 — the pre-router STR override needs STR's TEMPLATE fingerprint.

Live (deal b67bfdee, 2026-11): "Copy of 2024 Business Plan.docx" — a one-page
narrative with a five-row "HOTEL SUPPLY" table and goals phrased as "Increase
mid-week RevPAR index to 100" / "Exceed 90 in ADR index" — matched five of the
flat STR text markers (comp set, competitive set, ARI, ADR index, RevPAR
index) against one P&L marker, so ``detect_text_signals`` said ``looks_str``
and ``classify_for_extraction`` pinned STR_TREND without asking the Router
(``router:extract-pre-router-structural-override;dt:STR_TREND``). Its comp
list was then unioned into the Market tab roster: 7 hotels / 978 keys
instead of 4 / 344.

Every marker it hit is hotel VOCABULARY. What it never carries is the STR
report TEMPLATE: the "STAR Report" / "Property ID 56387" header,
"Competitive Set Data Excludes Subject Property", the "Index (MPI)" /
"My Property" row labels, the tab names, the CoStar / str.com footer.
``looks_str`` now requires at least two template markers on top of the
old floor; vocabulary-only text falls through to the Router.

Fixtures:
  * the structural signature of the three live STR workbooks (header + Table
    of Contents + first table rows, as ``parse_document`` renders them —
    figures omitted, they are not what the recognizer reads);
  * a Business-Plan-like .docx text with a comp-set table and a closed-hotel
    mention (fictional names; the shape is what matters).

Offline — no LLM, no DB.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.services.structural_recognizer import (
    _STR_TEMPLATE_MARKERS,
    _STR_TEMPLATE_MIN_HITS,
    _STR_TEXT_MIN_HITS,
    _STR_VOCAB_MARKERS,
    detect_text_signals,
)

# ─────────────────────────── fixtures ───────────────────────────

# "Copy of 56387-20250713-USD-E.xlsx" — weekly STAR report (26 sheets).
WEEKLY_STAR_REPORT = """\
STR # 56387 / Created July 22, 2025
\tWeekly STAR Report : Sample Hotel
\tFor the Week of: July 13, 2025 - July 19, 2025
\tCurrency: US Dollar  /  Competitive Set Data Excludes Subject Property
\tTable Of Contents\t1
\tsupport@str.com     www.str.com
\t2025 © CoStar Group. This STR Report is a publication of STR, LLC and STR Global, Ltd.

Tab 2 - Combined Summary - My Hotel vs. Comp Set\tCreated: Jul 22, 2025
\tOccupancy\tSunday\tMonday\tTuesday\tWednesday\tThursday\tFriday\tSaturday\tTotal
\tTotal\tMy Property
\t\tComp Set
\t\tIndex (MPI)
\t\tRank\t2 of 5

Tab 4 - Weekly Performance at a Glance - My Property vs. Comp Set
\tADR\tMy Prop\tComp Set\tIndex (ARI)
\tRevPAR\tMy Prop\tComp Set\tIndex (RGI)

Tab 14 - Response Report
\tSTR Code\tName of Establishment\tCity & State\tRooms\tOpen Date
"""

# "Copy of ANG-20231200-USD-E.xlsx" / "Copy of ANG-20250500-USD-E.xlsx" —
# monthly STAR reports (41 / 59 sheets). Same template, different month.
def monthly_star_report(month_label: str, created: str) -> str:
    return f"""\
\tProperty ID 56387 / Created {created} / Live Data / Fiscal Year January 1 / Weekends: Friday & Saturday
\tMonthly STAR Report : Sample Hotel
\tFor the Month of: {month_label}
\tCurrency: U.S. Dollar / Competitive Set Data Excludes Subject Property
\tTable Of Contents\t1
\tMonthly Performance at a Glance - Performance Set\t2
\tSTAR Summary - Performance Set\t3
\tCompetitive Set Report - Performance Set\t4
\tIndustry Report\t5
\tDay of Week and Weekday/Weekend Report - Performance Set\t6
\tSegmentation Index Analysis - Performance Set\t14
\tResponse Report - Performance Set\t22
\tHelp\t59

\tOccupancy (%)\tMy Property\tComp Set\tIndex (MPI)\tRank
\tADR\tMy Property\tComp Set\tIndex (ARI)\tRank
\tRevPAR\tMy Property\tComp Set\tIndex (RGI)\tRank
\tsupport@str.com     www.str.com
\t© CoStar Group.
"""


MONTHLY_STAR_DEC_2023 = monthly_star_report("December 2023", "January 18, 2024")
MONTHLY_STAR_MAY_2025 = monthly_star_report("May 2025", "June 21, 2025")

# "Copy of 2024 Business Plan.docx" — the shape of the live false positive.
# python-docx renders the one page as text; the table arrives tab-separated.
BUSINESS_PLAN_DOCX = """\
Sample Hotel South Beach
100 Example Avenue
Miami Beach, FL 33139
2024 BUSINESS PLAN
MARKET OVERVIEW + COMPETITION
HOTEL, RESTAURANT, BAR + MARKET OVERVIEW
Looking towards 2024, Miami RevPAR is expected to grow 4.8%.
We will also continue to utilize a wholesale partner as our base.
HOTEL SUPPLY
Hotel & Brand*\tMeeting Space Details\tNumber of Rooms\tCurrent + Future notes about property status
Alpha Hotel\t600 sq. ft.\t45\tRebranded and is undergoing a soft goods renovation
Beta South Beach\tN/A\t107\tRebranding into another flag
Gamma Hotel\t5,000 sq. ft.\t339\tConverting and renovating
Delta South Beach\t2,000 sq. ft.\t250\tRebranded
Epsilon Hotel\t8,000 sq. ft.\t270\tOfficially closed, undergoing renovations and reopening in late 2024
SWOT ANALYSIS: HOTEL, RESTAURANT + BAR COMPETITIVE SET
STRENGTHS\tWEAKNESSES
Newer spacious room product\tLocation not ocean front
OPPORTUNITIES\tTHREATS
Strategy shift with higher rate differentials\tMore hotels rebranding
Protecting LOS during high compressed periods\tComp set in closer proximity to the beach
GOALS + OBJECTIVES
Goal 1: Increase mid-week RevPAR index to 100 during summer months\tObjective 1: Pro-actively layer in group business in advance
Goal 1: Increase mid-week RevPAR index to 100 during summer months\tObjective 2: Track hotel pace vs. competitive set in Demand360
Goal 2: Exceed 90 in ADR index\tObjective 1: Protecting length of stay patterns
Catering: Increase ROGR to $126 by upselling our food and beverage offerings
Our director of sales, Ari, will own the group segment.
MARKET MIX + SEGMENT STRATEGIES
2024 Group RevPAR Goal:\t$26
"""


# ─────────────────────────── the false positive ───────────────────────────


def test_business_plan_docx_hits_vocabulary_but_no_template_marker() -> None:
    """The live document's signature: ≥4 vocabulary markers, one P&L marker,
    zero template markers. Before FON-123 this was ``looks_str=True``."""
    sig = detect_text_signals(BUSINESS_PLAN_DOCX)
    assert sig.str_marker_hits >= _STR_TEXT_MIN_HITS, sig.str_markers_matched
    assert sig.pnl_marker_hits <= 1
    assert sig.template_marker_hits == 0, sig.template_markers_matched
    assert sig.looks_str is False


def test_business_plan_docx_would_have_fired_under_the_vocabulary_only_rule() -> None:
    """Pin WHAT fooled the old rule so the fixture stays honest: the old
    floor (4 distinct markers, 2× the P&L vocab) is met by vocabulary alone."""
    sig = detect_text_signals(BUSINESS_PLAN_DOCX)
    vocab_only = sig.str_marker_hits - sig.template_marker_hits
    assert vocab_only >= _STR_TEXT_MIN_HITS
    assert sig.pnl_marker_hits == 0 or vocab_only / sig.pnl_marker_hits >= 2.0


# ─────────────────────────── recall on the real reports ───────────────────────────


@pytest.mark.parametrize(
    "label, text",
    [
        ("56387-20250713 weekly STAR", WEEKLY_STAR_REPORT),
        ("ANG-20231200 monthly STAR", MONTHLY_STAR_DEC_2023),
        ("ANG-20250500 monthly STAR", MONTHLY_STAR_MAY_2025),
    ],
)
def test_real_str_report_signatures_still_fire(label: str, text: str) -> None:
    sig = detect_text_signals(text)
    assert sig.looks_str is True, (label, sig.str_markers_matched, sig.pnl_marker_hits)
    assert sig.template_marker_hits >= _STR_TEMPLATE_MIN_HITS, (label, sig.template_markers_matched)
    # The header alone carries the template several times over — a report
    # with its first sheet parsed is enough; recall does not hinge on one token.
    assert sig.template_marker_hits >= 5, (label, sig.template_markers_matched)


def test_first_sheet_header_alone_is_enough() -> None:
    """A report whose later sheets failed to parse still pins: the header
    block ("STAR Report", "Excludes Subject Property", "Table Of Contents",
    publisher footer) is the fingerprint."""
    header_only = "\n".join(MONTHLY_STAR_MAY_2025.splitlines()[:6])
    sig = detect_text_signals(header_only + "\n\tComp Set\tMPI\tARI\tRGI\n")
    assert sig.looks_str is True, sig.str_markers_matched


def test_template_and_vocabulary_tiers_are_disjoint() -> None:
    assert not ({rx.pattern for rx in _STR_TEMPLATE_MARKERS} & {rx.pattern for rx in _STR_VOCAB_MARKERS})


# ─────────────────────────── pre-router: ambiguous → Router ───────────────────────────


@dataclass
class _RouterOut:
    doc_type: str | None
    route: str = "extract"


@pytest.mark.asyncio
async def test_business_plan_docx_falls_through_to_the_router(monkeypatch) -> None:
    """End to end on the decision function with the REAL recognizer: the
    Business Plan text reaches the Router (mocked here) and takes its call;
    the override route sentinel is never written."""
    from app.api.documents import classify_for_extraction

    calls: list[str] = []

    async def fake_router(router_input):
        calls.append(getattr(router_input, "filename", "?"))
        return _RouterOut(doc_type="OM")

    monkeypatch.setattr("app.agents.router.run_router", fake_router)
    decision = await classify_for_extraction(
        filename="Copy of 2024 Business Plan.docx",
        pages=[{"text": BUSINESS_PLAN_DOCX}],
        tenant_id="t", deal_id="d", doc_id="doc-plan",
    )
    assert calls == ["Copy of 2024 Business Plan.docx"]
    assert decision.source == "router"
    assert decision.doc_type == "OM"
    assert decision.route != "extract-pre-router-structural-override"


@pytest.mark.asyncio
async def test_real_str_workbook_still_skips_the_router(monkeypatch) -> None:
    from app.api.documents import classify_for_extraction

    async def fake_router(router_input):  # pragma: no cover — must not be called
        raise AssertionError("Router must not run for an STR workbook")

    monkeypatch.setattr("app.agents.router.run_router", fake_router)
    pages = [{"text": chunk} for chunk in MONTHLY_STAR_MAY_2025.split("\n\n")]
    decision = await classify_for_extraction(
        filename="Copy of ANG-20250500-USD-E.xlsx",
        pages=pages,
        tenant_id="t", deal_id="d", doc_id="doc-str",
    )
    assert decision.source == "pre-router-override"
    assert decision.doc_type == "STR_TREND"
    assert decision.route == "extract-pre-router-structural-override"
