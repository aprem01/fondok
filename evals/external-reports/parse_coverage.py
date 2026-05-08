"""Parse-coverage benchmark for the three external market reports.

Background
----------
Sam's QA email asked for an 80% extraction-accuracy benchmark. Real
ground-truth labels need annotated deal docs (Sam-blocked); meanwhile
this script answers the upstream question: does the parser surface
enough source text for the extractor prompt to *find* the canonical
fields it knows about? If the answer is "no" the LLM extractor will
fail no matter how the prompt reads.

For each of the three real reports under ``apps/worker/tests/fixtures``
this script:

  1. Runs the same ``parse_document`` entry point production uploads use.
  2. Walks an anchor-keyword table that lists the canonical field
     paths the extractor is expected to emit, plus the term(s) that
     ground each field in the source.
  3. Records which pages contain each anchor, derives a coverage
     percentage, and writes the result to ``parse-coverage.json``.

Run::

    uv run --project apps/worker python evals/external-reports/parse_coverage.py

The companion test ``apps/worker/tests/test_external_reports_coverage.py``
re-runs the same coverage check in CI, asserting we never regress
below 80%.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKER_ROOT = REPO_ROOT / "apps" / "worker"
sys.path.insert(0, str(WORKER_ROOT))
sys.path.insert(0, str(REPO_ROOT / "packages" / "schemas-py"))

from app.extraction import parse_document  # noqa: E402

FIXTURES = WORKER_ROOT / "tests" / "fixtures"
OUTPUT = Path(__file__).parent / "parse-coverage.json"

# ──────────────────────────── anchor table ────────────────────────────
#
# Each entry maps an extractor field path to one or more anchor strings.
# A field is "covered" when at least one anchor appears in any parsed
# page (case-insensitive substring match). Anchors are intentionally
# loose — we are checking *whether the source carries the data*, not
# whether the extractor parses it correctly. That's a downstream
# question handled by the LLM extractor + golden-set tests.

CBRE_HORIZONS_ANCHORS: dict[str, list[str]] = {
    "cbre_horizons.market": ["Seattle, WA", "Hotel Horizons"],
    "cbre_horizons.publication_date": ["Q3 2024", "November 2024"],
    "cbre_horizons.long_run_avg.occupancy_pct": ["Long Run Averages", "67.4"],
    "cbre_horizons.long_run_avg.adr_change_pct": ["ADR Change", "2.7"],
    "cbre_horizons.long_run_avg.revpar_change_pct": ["RevPAR Change", "5.8"],
    "cbre_horizons.segment_all.2024.occupancy_pct": ["72.0", "2024F"],
    "cbre_horizons.segment_all.2024.adr_usd": ["$176.93", "176.93"],
    "cbre_horizons.segment_all.2024.revpar_usd": ["$127.35", "127.35"],
    "cbre_horizons.segment_all.2028.occupancy_pct": ["74.1", "2028F"],
    "cbre_horizons.segment_all.2028.adr_usd": ["$194.62", "194.62"],
    "cbre_horizons.segment_upper_priced.2024.adr_usd": ["$237.40", "237.40"],
    "cbre_horizons.segment_mid_priced.2024.adr_usd": ["$166.53", "166.53"],
    "cbre_horizons.segment_lower_priced.2024.adr_usd": ["$91.57", "91.57"],
    "cbre_horizons.guest_paid_adr.all.2023.adr_usd": ["Guest-Paid ADR", "$176.60"],
    "cbre_horizons.source_mix.brand_com.room_nights_pct_2024": [
        "Brand.com",
        "22.3",
    ],
    "cbre_horizons.source_mix.ota.room_nights_pct_2024": ["OTA", "19.6"],
    "cbre_horizons.source_mix.group.room_nights_pct_2024": ["Group", "13.4"],
    "cbre_horizons.length_of_stay.all.nights_2023": ["Length of Stay", "2.08"],
    "cbre_horizons.short_term_rental.active_units": [
        "Short-Term Rental",
        "AirDNA",
    ],
    "cbre_horizons.short_term_rental.adr_usd": ["$209.33", "209.33"],
}

PNL_BENCHMARK_ANCHORS: dict[str, list[str]] = {
    "pnl_benchmark.peer_set_size": ["Number of Properties", "Comparable"],
    "pnl_benchmark.peer_set_avg_keys": ["Average Number of Rooms", "203"],
    "pnl_benchmark.peer_set_avg_adr_usd": ["Average ADR", "$136.75"],
    "pnl_benchmark.subject_keys": ["Number of Rooms", "202"],
    "pnl_benchmark.subject_adr_usd": ["$141.59"],
    "pnl_benchmark.peer.rooms_revenue.total_usd": ["Rooms", "$7,001,677"],
    "pnl_benchmark.peer.fb_revenue.total_usd": ["Food and Beverage", "$1,201,360"],
    "pnl_benchmark.peer.total_revenue.total_usd": [
        "Total Operating Revenue",
        "$8,557,632",
    ],
    "pnl_benchmark.peer.rooms_dept_expense.total_usd": ["$1,611,568"],
    "pnl_benchmark.peer.a_and_g.total_usd": [
        "Administrative and General",
        "$749,139",
    ],
    "pnl_benchmark.peer.utilities.total_usd": ["Utility Costs", "$349,909"],
    "pnl_benchmark.peer.utilities.electricity_usd": ["Electricity", "$169,566"],
    "pnl_benchmark.peer.utilities.water_sewer_usd": ["Water", "$149,166"],
    "pnl_benchmark.peer.utilities.gas_fuel_usd": ["Gas", "$24,655"],
    "pnl_benchmark.peer.fb_revenue.food_venues_usd": ["Venues", "$272,767"],
    "pnl_benchmark.peer.fb_revenue.food_room_service_usd": [
        "Room Service",
        "$27,241",
    ],
    "pnl_benchmark.peer.fb_revenue.food_banquet_usd": ["Banquet", "$420,945"],
    "pnl_benchmark.peer.labor.rooms.salaries_management_usd": [
        "Salaries and Wages - Management",
        "$23,054",
    ],
    "pnl_benchmark.peer.labor.rooms.payroll_related_usd": [
        "Payroll-Related",
        "$255,669",
    ],
    "pnl_benchmark.peer.ebitda.total_usd": ["EBITDA", "$2,353,962"],
    "pnl_benchmark.subject.total_revenue.total_usd": ["$8,515,636"],
    "pnl_benchmark.subject.ebitda.total_usd": ["$1,971,583"],
    "pnl_benchmark.peer.gop.total_usd": ["Gross Operating Profit", "$3,346,545"],
    "pnl_benchmark.peer.mgmt_fee.total_usd": ["Management Fees", "$203,568"],
}

STR_TREND_ANCHORS: dict[str, list[str]] = {
    "ttm_performance.subject.name": ["Custom Trend", "Rosewood"],
    "ttm_performance.subject.annual.2022.occupancy_pct": ["2022", "64.1"],
    "ttm_performance.subject.annual.2022.adr_usd": ["1220.24", "1220"],
    "ttm_performance.subject.annual.2019.occupancy_pct": ["2019", "67.85"],
    "ttm_performance.subject.monthly.2023_01.occupancy_pct": ["Jan 23", "68.65"],
    "ttm_performance.subject.monthly.2022_12.adr_usd": ["Dec 22", "1672"],
    "ttm_performance.subject.day_of_week.mon": ["Day of Week"],
    "comp_set.comp_set_size": ["Selected Properties"],
    "ttm_performance.indices.rgi_revpar_index": ["Index", "Classic"],
    "ttm_performance.subject.annual.2023.occupancy_pct": ["Feb YTD 2023", "70.6"],
}

REPORTS: list[dict[str, object]] = [
    {
        "filename": "sample_cbre_horizons.pdf",
        "doc_type": "CBRE_HORIZONS",
        "anchors": CBRE_HORIZONS_ANCHORS,
    },
    {
        "filename": "sample_pnl_benchmark.pdf",
        "doc_type": "PNL_BENCHMARK",
        "anchors": PNL_BENCHMARK_ANCHORS,
    },
    {
        "filename": "sample_str_trend.xls",
        "doc_type": "STR_TREND",
        "anchors": STR_TREND_ANCHORS,
    },
]


async def _coverage_for(
    *, filename: str, doc_type: str, anchors: dict[str, list[str]]
) -> dict[str, object]:
    path = FIXTURES / filename
    if not path.exists():
        return {
            "filename": filename,
            "doc_type": doc_type,
            "error": f"fixture missing at {path}",
        }
    body = path.read_bytes()
    parsed = await parse_document(body, filename)

    page_texts: list[tuple[int, str]] = [
        (p.page_num, p.text.lower()) for p in parsed.pages
    ]

    expected_fields: list[dict[str, object]] = []
    found_count = 0
    for field_path, anchor_terms in anchors.items():
        terms = [a.lower() for a in anchor_terms]
        matched_pages = [
            page_num for page_num, text in page_texts if any(t in text for t in terms)
        ]
        is_found = bool(matched_pages)
        if is_found:
            found_count += 1
        expected_fields.append(
            {
                "path": field_path,
                "anchors": anchor_terms,
                "found_on_pages": matched_pages,
                "status": "found" if is_found else "missing",
            }
        )

    total = len(anchors)
    return {
        "filename": filename,
        "doc_type": doc_type,
        "parser": parsed.parser,
        "pages": parsed.total_pages,
        "expected_fields": expected_fields,
        "summary": {
            "total_expected": total,
            "found": found_count,
            "missing": total - found_count,
            "coverage_pct": round(found_count / total, 4) if total else 0.0,
        },
    }


async def main() -> None:
    results: list[dict[str, object]] = []
    for spec in REPORTS:
        results.append(
            await _coverage_for(
                filename=spec["filename"],  # type: ignore[arg-type]
                doc_type=spec["doc_type"],  # type: ignore[arg-type]
                anchors=spec["anchors"],  # type: ignore[arg-type]
            )
        )

    overall_total = sum(r["summary"]["total_expected"] for r in results if "summary" in r)  # type: ignore[index]
    overall_found = sum(r["summary"]["found"] for r in results if "summary" in r)  # type: ignore[index]
    payload = {
        "version": "1",
        "generated_at": datetime.now(UTC).isoformat(),
        "what_this_measures": (
            "Per-field anchor coverage: does the parser surface enough "
            "raw text to ground each canonical extractor field path? "
            "This is a parse-stage check, not an extraction-accuracy "
            "check. The LLM extractor still has to read the surrounding "
            "context and emit the right value — ground-truth label "
            "comparison ships in a separate suite once Sam-annotated "
            "deal docs land."
        ),
        "overall": {
            "total_expected": overall_total,
            "found": overall_found,
            "coverage_pct": round(overall_found / overall_total, 4)
            if overall_total
            else 0.0,
            "target_pct": 0.80,
        },
        "reports": results,
    }
    OUTPUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(
        f"wrote {OUTPUT} — overall {overall_found}/{overall_total} "
        f"({payload['overall']['coverage_pct']:.1%})"
    )
    for r in results:
        if "summary" in r:
            s = r["summary"]  # type: ignore[index]
            print(
                f"  {r['filename']:<32} {s['found']}/{s['total_expected']} "
                f"({s['coverage_pct']:.1%}) parser={r.get('parser')}"
            )


if __name__ == "__main__":
    asyncio.run(main())
