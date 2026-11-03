"""FON-121 — every place that names a model year shows its deal-year span:
run compare, the exports' exit table and proforma headers, the P&L workbook
exit column. Pure — no DB."""

from __future__ import annotations

from app.engines.deal_years import deal_year_spans
from app.export.labels import deal_year_label, exit_row_label, year_heading
from app.services.run_compare import diff_fields, extract_headline_fields

SPANS = [s.model_dump() for s in deal_year_spans("2025-09-30", 5)]


def test_run_compare_rows_name_the_runs_deal_year() -> None:
    fields = extract_headline_fields({
        "revenue": {"years": [{"year": 1, "occupancy": 0.7}], "projection_deal_years": SPANS},
        "returns": {"exit_schedule": [{"exit_year": 3, "levered_irr": 0.1}], "peak_levered_exit_year": 3},
    })
    labels = [f["label"] for f in fields]
    assert any(lab.startswith("Year 1 · Oct 2025–Sep 2026 · ") for lab in labels)
    assert any(lab.startswith("Exit Year 3 (Oct 2027–Sep 2028)") for lab in labels if "IRR" in lab)


def test_run_compare_says_both_spans_when_the_runs_place_the_year_differently() -> None:
    old = extract_headline_fields({"revenue": {"years": [{"year": 1, "occupancy": 0.7}]}})
    new = extract_headline_fields({"revenue": {"years": [{"year": 1, "occupancy": 0.71}], "projection_deal_years": SPANS}})
    row = next(r for r in diff_fields(old, new) if r["key"] == "revenue.years[0].occupancy")
    assert row["label"].startswith("Year 1 · Oct 2025–Sep 2026 · ")
    assert "(baseline: Year 1 · " in row["label"]


def test_export_labels_carry_the_span_and_stay_bare_without_one() -> None:
    assert deal_year_label(SPANS, 3) == "Oct 2027–Sep 2028"
    assert deal_year_label(SPANS, 9) is None and deal_year_label(None, 1) is None
    assert year_heading(1, SPANS) == "Year 1 · Oct 2025–Sep 2026"
    assert year_heading(1, None) == "Year 1"
    assert exit_row_label({"exit_year": 3, "is_selected": True}, SPANS) == "Year 3 (Oct 2027–Sep 2028) · selected"
    assert exit_row_label({"exit_year": 3}) == "Year 3"
    assert exit_row_label({"exit_month": 52}, SPANS) == "Month 52"


def test_p_and_l_workbook_exit_column_names_the_exit_noi_deal_year() -> None:
    from app.export.pl_workbooks import projection_extras

    nxt = deal_year_spans("2025-09-30", 6)[5].model_dump()
    extras = projection_extras({"revenue": {"projection_deal_years": SPANS, "exit_noi_deal_year": nxt}}, 5)
    assert extras.exit_deal_year_label == "Oct 2030–Sep 2031"
    # A sheet that shows fewer years than the run is headed with the year after ITS last one.
    assert projection_extras({"revenue": {"projection_deal_years": SPANS, "exit_noi_deal_year": nxt}}, 3).exit_deal_year_label == "Oct 2028–Sep 2029"
    assert projection_extras({"revenue": {}}, 3).exit_deal_year_label is None
