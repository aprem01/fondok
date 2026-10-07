"""FON-61 E-009 / E-007 — the ONE comp-set derivation and the TTM blend's inputs.

E-009: the STR report listed 5 hotels / 419 keys with one closed ("Closed -
Blue Moon", 0 rooms). Fondok showed 344 keys labelled "5 hotels" because
the count came from the extracted ``comp_set.comp_set_size`` rollup while
the keys were summed over the active roster. One derivation now feeds
both, and "closed" comes ONLY from an explicit marker: the extracted
``status`` field or STR's own ``"Closed - "`` roster label.

E-007: the "TTM · comp-set blend" names the rows (document / field / page)
and the period it was recovered from.

Pure unit tests — no DB, no LLM, no I/O.
"""

from __future__ import annotations

from typing import Any

from app.services.market_comp_set import (
    STR_CLOSED_LABEL_RE,
    TTM_BLEND_METHOD,
    build_str_inputs,
    classify_hotel_status,
    derive_comp_set,
    derive_comp_set_from_inputs,
    derive_ttm_blend,
)
from app.services.market_fields import FieldRow, parse_extraction_records

# The tester's roster: 5 hotels, 419 keys as listed, Blue Moon closed with
# 0 rooms → 4 active hotels / 344 keys.
ROSTER_WITH_CLOSED: dict[int, dict[str, Any]] = {
    1: {"name": "Z Ocean Hotel", "keys": 40},
    2: {"name": "Closed - Blue Moon Hotel", "keys": 0},
    3: {"name": "The Betsy South Beach", "keys": 129},
    4: {"name": "The Tony Hotel of South Beach", "keys": 68},
    5: {"name": "Dream South Beach", "keys": 107},
}


def test_closed_hotel_excluded_from_both_count_and_keys() -> None:
    d = derive_comp_set(
        ROSTER_WITH_CLOSED, reported_comp_set_size=5, reported_total_keys=344
    )
    assert d.active_count == 4
    assert d.active_keys == 40 + 129 + 68 + 107 == 344
    assert d.closed_count == 1
    assert d.closed_names == ["Blue Moon Hotel"]
    assert d.count_basis == "active_roster" and d.keys_basis == "active_roster"
    assert d.status_available is True
    # The report's own rollup is surfaced for transparency, never the headline.
    assert d.reported_comp_set_size == 5
    assert d.reported_total_keys == 344
    closed = [h for h in d.hotels if h.status == "closed"]
    assert len(closed) == 1
    assert closed[0].name == "Blue Moon Hotel"
    assert closed[0].name_as_reported == "Closed - Blue Moon Hotel"
    assert closed[0].status_source == "str_closed_label"
    assert "Blue Moon Hotel" in d.note and "excluded" in d.note


def test_closed_hotel_with_keys_is_still_excluded_from_keys() -> None:
    """A closed hotel that still lists rooms contributes nothing — the count
    and the keys are over the same set."""
    roster = {**ROSTER_WITH_CLOSED, 2: {"name": "Closed - Blue Moon Hotel", "keys": 75}}
    d = derive_comp_set(roster)
    assert d.active_count == 4
    assert d.active_keys == 344


def test_no_closed_marker_counts_every_hotel_and_says_so() -> None:
    roster = {
        1: {"name": "Z Ocean Hotel", "keys": 40},
        2: {"name": "Blue Moon Hotel", "keys": 75},
        3: {"name": "The Betsy South Beach", "keys": 129},
    }
    d = derive_comp_set(roster, reported_comp_set_size=3, reported_total_keys=244)
    assert d.active_count == 3
    assert d.active_keys == 244
    assert d.closed_count == 0
    assert d.status_available is False
    assert "No hotel in the roster carries a closed marker" in d.note


def test_zero_room_row_without_marker_is_not_inferred_closed() -> None:
    """No heuristic: a 0-room row with no label and no status stays active."""
    roster = {
        1: {"name": "Z Ocean Hotel", "keys": 40},
        2: {"name": "Blue Moon Hotel", "keys": 0},
    }
    d = derive_comp_set(roster)
    assert d.active_count == 2
    assert d.active_keys == 40
    assert d.closed_count == 0
    assert d.status_available is False


def test_explicit_status_field_wins_over_name() -> None:
    assert classify_hotel_status("Blue Moon Hotel", "closed") == ("closed", "extracted_status_field")
    assert classify_hotel_status("Blue Moon Hotel", "Closed") == ("closed", "extracted_status_field")
    # An explicit "open" status overrides even a "Closed - " label.
    assert classify_hotel_status("Closed - Blue Moon", "open") == ("active", "extracted_status_field")
    assert classify_hotel_status("Closed - Blue Moon", None) == ("closed", "str_closed_label")
    assert classify_hotel_status("Closed – Blue Moon", None) == ("closed", "str_closed_label")  # noqa: RUF001 — STR's en dash
    assert classify_hotel_status("Blue Moon", None) == ("active", None)
    # Only the leading label counts — a hotel NAMED with the word is not closed.
    assert classify_hotel_status("The Closed Door Inn", None) == ("active", None)
    assert STR_CLOSED_LABEL_RE.match("closed: Foo")


def test_rollup_fallback_only_when_no_roster() -> None:
    d = derive_comp_set({}, reported_comp_set_size=5, reported_total_keys=419)
    assert d.hotels == []
    assert d.active_count == 5 and d.active_keys == 419
    assert d.count_basis == "reported_rollup" and d.keys_basis == "reported_rollup"
    assert d.status_available is False
    assert "closed hotels cannot be identified" in d.note

    empty = derive_comp_set({})
    assert empty.active_count is None and empty.active_keys is None
    assert empty.count_basis == "none" and empty.keys_basis == "none"


def test_roster_without_room_counts_uses_report_total_and_says_so() -> None:
    roster = {1: {"name": "A"}, 2: {"name": "B"}}
    d = derive_comp_set(roster, reported_total_keys=200)
    assert d.active_count == 2
    assert d.active_keys == 200
    assert d.keys_basis == "reported_rollup"
    assert "report's rollup" in d.note


# ─────────────────────────── inputs from extraction rows ───────────────────────────


def _row(name: str, value: Any, *, ext: str = "e1", doc: str = "STR Trend Jun-2026.xlsx", page: int | None = 22) -> FieldRow:
    return FieldRow(
        field_name=name, value=value, unit=None, page=page, doc_name=doc,
        doc_id=f"doc-{ext}", extraction_id=ext, doc_type="STR_TREND",
    )


def test_roster_comes_from_one_extraction_and_status_field_is_read() -> None:
    rows = [
        # Newest extraction: roster with an explicit status field.
        _row("ttm_performance.compset.1.name", "Z Ocean Hotel"),
        _row("ttm_performance.compset.1.keys", 40),
        _row("ttm_performance.compset.2.name", "Blue Moon Hotel"),
        _row("ttm_performance.compset.2.keys", 0),
        _row("ttm_performance.compset.2.status", "closed"),
        _row("comp_set.comp_set_size", 2),
        # An older extraction's roster must NOT be merged in by index.
        _row("ttm_performance.compset.3.name", "Ghost Hotel", ext="e0", doc="old.xlsx"),
        _row("ttm_performance.compset.3.keys", 500, ext="e0", doc="old.xlsx"),
    ]
    inputs = build_str_inputs(rows)
    assert set(inputs.roster) == {1, 2}
    assert inputs.roster_doc_name == "STR Trend Jun-2026.xlsx"
    assert inputs.roster_page == 22
    d = derive_comp_set_from_inputs(inputs)
    assert d.active_count == 1 and d.active_keys == 40
    assert d.closed_names == ["Blue Moon Hotel"]
    assert d.hotels[1].status_source == "extracted_status_field"
    assert d.source_doc_name == "STR Trend Jun-2026.xlsx"
    assert d.reported_comp_set_size == 2


def test_parse_extraction_records_keeps_document_identity() -> None:
    records = [
        {
            "extraction_id": "e1",
            "document_id": "d1",
            "filename": "STR.xlsx",
            "doc_type": "STR_TREND",
            "fields": '[{"field_name": "comp_set.total_keys", "value": "344", "unit": "rooms", "source_page": 22}, {"nope": 1}, 7]',
        },
        {"extraction_id": "e2", "document_id": "d2", "filename": "bad.xlsx", "doc_type": "STR_TREND", "fields": "{not json"},
    ]
    rows = parse_extraction_records(records)
    assert len(rows) == 1
    assert rows[0].field_name == "comp_set.total_keys"
    assert rows[0].doc_name == "STR.xlsx" and rows[0].doc_id == "d1" and rows[0].page == 22
    assert rows[0].unit == "rooms"


# ─────────────────────────── TTM blend (E-007) ───────────────────────────


def _blend_rows(*, with_monthly: bool = True) -> list[FieldRow]:
    rows = [
        _row("ttm_performance.subject.occupancy_pct", 0.714, page=3),
        _row("ttm_performance.subject.adr_usd", 278.0, page=3),
        _row("ttm_performance.subject.revpar_usd", 198.5, page=3),
        _row("ttm_performance.indices.mpi_occupancy_index", 103.2, page=4),
        _row("ttm_performance.indices.ari_adr_index", 0.942, page=4),
        _row("ttm_performance.indices.rgi_revpar_index", 97.2, page=4),
        _row("str_trend.report_year", 2026, page=1),
    ]
    if with_monthly:
        for ym in ("2025_07", "2025_08", "2025_09", "2025_10", "2025_11", "2025_12",
                   "2026_01", "2026_02", "2026_03", "2026_04", "2026_05", "2026_06"):
            rows.append(_row(f"ttm_performance.subject.monthly.{ym}.occupancy_pct", 0.7, page=8))
    return rows


def test_ttm_blend_recovers_comp_set_and_names_rows_and_period() -> None:
    b = derive_ttm_blend(build_str_inputs(_blend_rows()))
    assert b is not None
    # occ = 71.4 ÷ 1.032 ; adr = 278 ÷ 0.942 ; revpar = 198.5 ÷ 0.972
    # (the wire value is rounded to 4 dp — the web shows 1 dp / whole $).
    assert abs(b.occupancy_pct - 71.4 / 1.032) < 1e-3
    assert abs(b.adr_usd - 278 / 0.942) < 1e-3
    assert abs(b.revpar_usd - 198.5 / 0.972) < 1e-3
    assert b.subject_occupancy_pct == 71.4
    assert (b.mpi, b.ari, b.rgi) == (1.032, 0.942, 0.972)
    assert b.period_basis == "subject_monthly_series"
    assert (b.period_start, b.period_end, b.months) == ("2025-07", "2026-06", 12)
    assert b.documents == ["STR Trend Jun-2026.xlsx"]
    names = [r.field_name for r in b.inputs]
    assert names == [
        "ttm_performance.subject.occupancy_pct",
        "ttm_performance.subject.adr_usd",
        "ttm_performance.subject.revpar_usd",
        "ttm_performance.indices.mpi_occupancy_index",
        "ttm_performance.indices.ari_adr_index",
        "ttm_performance.indices.rgi_revpar_index",
    ]
    assert b.inputs[3].page == 4 and b.inputs[0].doc_name == "STR Trend Jun-2026.xlsx"
    assert b.method == TTM_BLEND_METHOD and "÷ MPI" in b.method


def test_ttm_blend_falls_back_to_report_year_period() -> None:
    b = derive_ttm_blend(build_str_inputs(_blend_rows(with_monthly=False)))
    assert b is not None
    assert b.period_basis == "report_year" and b.report_year == 2026
    assert b.period_start is None and b.months is None
    assert b.inputs[-1].field_name == "str_trend.report_year"


def test_ttm_blend_never_substitutes_a_missing_index() -> None:
    rows = [r for r in _blend_rows() if "ari_adr_index" not in r.field_name]
    b = derive_ttm_blend(build_str_inputs(rows))
    assert b is not None
    assert b.adr_usd is None  # no ARI → no comp ADR
    assert b.occupancy_pct is not None
    assert derive_ttm_blend(build_str_inputs([_row("comp_set.total_keys", 344)])) is None


def test_ttm_blend_newest_extraction_wins_per_field() -> None:
    newest = _row("ttm_performance.subject.occupancy_pct", 0.70, ext="e2", doc="new.xlsx")
    older = _row("ttm_performance.subject.occupancy_pct", 0.60, ext="e1", doc="old.xlsx")
    index = _row("ttm_performance.indices.mpi_occupancy_index", 1.0, ext="e1", doc="old.xlsx")
    b = derive_ttm_blend(build_str_inputs([newest, older, index]))
    assert b is not None
    assert b.occupancy_pct == 70.0
    assert b.documents == ["new.xlsx", "old.xlsx"]
