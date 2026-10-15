"""Index Analysis bugs (Sam #3).

Pinned here (pure — no DB, no LLM):

(a) A LEGACY stored ``index_mpi_target`` / ``index_ari_target`` outside the
    accepted band (25–250 index points or a 0.25–2.5 ratio) — written before
    the write-time range gate — is IGNORED at every read that feeds the engine
    or the by-year grid, and the reason ("stored ... 5 is outside 25–250 ...
    re-enter it") is surfaced; it never moves Year-1 occupancy / ADR.
(b) A scalar target stored as a ratio (1.10) reads as 110 index points in the
    panel, the grid AND the engine seam.
(c) The STR comp set by model year (TTM level grown at the market growth
    assumption — the same ``_grown_series`` the ``str_comp_set`` method uses)
    is on the endpoint whichever method is selected, and is ``None`` with the
    reason when no growth value exists.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.services.index_methodology import (
    BASE_OCC_BY_YEAR,
    KEY_ADR_GROWTH,
    KEY_ARI_TARGET,
    KEY_MPI_TARGET,
    KEY_MPI_TARGET_BY_YEAR,
    KEY_OCC_GROWTH,
    METHOD_MARKET_BENCHMARK,
    SOURCE_INDEX_ASSUMPTION,
    apply_index_assumptions,
    build_index_drivers,
    read_targets_by_year,
    resolve_index_by_year,
    resolve_index_methodology,
    stored_target_problem,
    wants_benchmark,
)
from app.services.market_fields import FieldRow

STR_DOC = "Subject STR Trend May 2025.xlsx"
MARKET_DOC = "Miami Beach Submarket 2025.pdf"
STR_BASIS = frozenset({"str_forecast", "str_subject_ttm", "str_comp_set"})


def _row(name: str, value: Any, *, doc: str, doc_type: str, page: int | None = 2) -> FieldRow:
    return FieldRow(
        field_name=name, value=value, unit=None, page=page, doc_name=doc,
        doc_id=f"doc-{doc_type.lower()}", extraction_id=f"ext-{doc_type.lower()}",
        doc_type=doc_type, period=None,
    )


STR_ROWS = [
    _row("ttm_performance.subject.occupancy_pct", 84.6, doc=STR_DOC, doc_type="STR_TREND", page=3),
    _row("ttm_performance.subject.adr_usd", 255.0, doc=STR_DOC, doc_type="STR_TREND", page=3),
    _row("ttm_performance.indices.mpi_occupancy_index", 110.0, doc=STR_DOC, doc_type="STR_TREND", page=4),
    _row("ttm_performance.indices.ari_adr_index", 102.0, doc=STR_DOC, doc_type="STR_TREND", page=4),
]
COMP_OCC = 0.846 / 1.10
COMP_ADR = 255.0 / 1.02
MARKET_ROWS = [
    _row("pnl_benchmark.market.occupancy_pct", 0.712, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=5),
    _row("pnl_benchmark.market.adr_usd", 231.0, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=5),
    _row("pnl_benchmark.market.adr_change_2026_forecast", 0.031, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=6),
    _row("pnl_benchmark.market.occupancy_change_2026_forecast", 0.005, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=6),
]


def _base(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "starting_occupancy": 0.846, "starting_adr": 255.0,
        "occupancy_growth": 0.008, "adr_growth": 0.04, "hold_years": 3,
    }
    base.update(kw)
    return base


# ─────────────────────────── (a) legacy out-of-range targets ───────────────────────────


def test_stored_target_problem_names_the_value_the_band_and_the_fix() -> None:
    why = stored_target_problem(5, KEY_MPI_TARGET)
    assert why is not None
    assert "5 is outside 25–250" in why and "re-enter it" in why and "not applied" in why
    assert "MPI" in why
    assert stored_target_problem(110, KEY_MPI_TARGET) is None
    assert stored_target_problem(1.1, KEY_MPI_TARGET) is None
    assert stored_target_problem(None, KEY_MPI_TARGET) is None
    assert "Year-2" in (stored_target_problem(1000, KEY_MPI_TARGET_BY_YEAR, year=2) or "")


@pytest.mark.parametrize("legacy", [5, 12, 0.1, 1000, "300"])
def test_engine_seam_ignores_a_legacy_out_of_range_scalar(legacy: Any) -> None:
    base = _base(**{KEY_MPI_TARGET: legacy, KEY_ARI_TARGET: legacy})
    sources = {"starting_occupancy": "str_subject_ttm", "starting_adr": "str_subject_ttm"}
    paths = {KEY_MPI_TARGET, KEY_ARI_TARGET}
    assert wants_benchmark(base, paths) is False
    before = dict(base)
    assert apply_index_assumptions(base, sources, paths, rows=STR_ROWS, str_basis_sources=STR_BASIS) == []
    assert build_index_drivers(
        base, sources, paths, rows=STR_ROWS, str_basis_sources=STR_BASIS, hold_years=3, calendar_years=[]
    ) == []
    assert base == before
    assert sources["starting_occupancy"] == "str_subject_ttm"


def test_panel_shows_no_value_and_the_reason_for_a_legacy_scalar() -> None:
    r = resolve_index_methodology(STR_ROWS, {KEY_MPI_TARGET: {"value": 5, "note": "old"}})
    fig = r.assumptions[KEY_MPI_TARGET]
    # Distinct from an active override AND from "no source": a stored value
    # exists, the model ignores it, and the detail says why.
    assert fig.value is None and fig.source == "ignored_override"
    assert "5 is outside 25–250" in (fig.detail or "") and "re-enter it" in (fig.detail or "")


def test_by_year_grid_ignores_a_legacy_scalar_and_says_why() -> None:
    r = resolve_index_by_year(
        STR_ROWS, {KEY_MPI_TARGET: {"value": 5, "note": "old"}}, hold_years=2, calendar_years=[2025, 2026]
    )
    for y in r.years:
        assert y.mpi_target.value is None and y.occupancy is None
        assert y.occupancy_status == "no_target"
        assert "5 is outside 25–250" in (y.mpi_target.problem or "")
        assert "Occupancy: Stored all-years MPI target 5" in (y.reason or "")


def test_a_legacy_by_year_entry_falls_back_to_the_valid_scalar_and_is_named() -> None:
    mpi, _ = read_targets_by_year(
        {KEY_MPI_TARGET_BY_YEAR: {"value": [120, 7], "note": "n"}, KEY_MPI_TARGET: {"value": 110, "note": "n"}}, 2
    )
    assert (mpi[0].value, mpi[0].source, mpi[0].problem) == (120.0, "by_year", None)
    assert (mpi[1].value, mpi[1].source) == (110.0, "scalar")
    assert "Year-2 MPI target 7 is outside" in (mpi[1].problem or "")


def test_endpoint_carries_the_problem_on_the_target() -> None:
    from app.api.market import index_methodology_response

    overrides = {KEY_ARI_TARGET: {"value": 1000, "note": "old"}}
    reading = resolve_index_methodology(STR_ROWS, overrides)
    by_year = resolve_index_by_year(STR_ROWS, overrides, hold_years=2, calendar_years=[2025, 2026])
    out = index_methodology_response(uuid4(), reading, by_year).model_dump(mode="json")
    assert out["assumptions"][KEY_ARI_TARGET]["value"] is None
    assert "1000 is outside" in out["assumptions"][KEY_ARI_TARGET]["detail"]
    assert "1000 is outside" in out["by_year"][0]["ari_target"]["problem"]


# ─────────────────────────── (b) ratio vs points ───────────────────────────


def test_a_ratio_scalar_reads_as_index_points_in_panel_grid_and_engine() -> None:
    overrides = {KEY_MPI_TARGET: {"value": 1.10, "note": "ratio"}}
    assert resolve_index_methodology(STR_ROWS, overrides).assumptions[KEY_MPI_TARGET].value == pytest.approx(110.0)
    by_year = resolve_index_by_year(STR_ROWS, overrides, hold_years=1, calendar_years=[2025])
    assert by_year.years[0].mpi_target.value == pytest.approx(110.0)
    assert by_year.years[0].occupancy == pytest.approx(COMP_OCC * 1.10)
    base = _base(**{KEY_MPI_TARGET: 1.10})
    sources = {"starting_occupancy": "str_subject_ttm"}
    assert apply_index_assumptions(base, sources, {KEY_MPI_TARGET}, rows=STR_ROWS, str_basis_sources=STR_BASIS) == [
        "starting_occupancy"
    ]
    assert base["starting_occupancy"] == pytest.approx(COMP_OCC * 1.10)
    assert sources["starting_occupancy"] == SOURCE_INDEX_ASSUMPTION


def test_valid_targets_still_drive_exactly_as_before() -> None:
    base = _base(**{KEY_MPI_TARGET_BY_YEAR: [120.0, None, None], KEY_MPI_TARGET: 110.0, KEY_OCC_GROWTH: 0.012})
    sources = {"starting_occupancy": "str_subject_ttm", "starting_adr": "str_subject_ttm"}
    build_index_drivers(
        base, sources, {KEY_MPI_TARGET_BY_YEAR, KEY_MPI_TARGET, KEY_OCC_GROWTH},
        rows=STR_ROWS, str_basis_sources=STR_BASIS, hold_years=3, calendar_years=[],
    )
    assert base[BASE_OCC_BY_YEAR][0] == pytest.approx(COMP_OCC * 1.20)
    assert base[BASE_OCC_BY_YEAR][1] == pytest.approx(COMP_OCC * 1.012 * 1.10)


# ─────────────────────────── (c) comp set by year ───────────────────────────


def test_comp_set_series_is_the_ttm_grown_at_market_growth_whatever_method_is_selected() -> None:
    from app.api.market import index_methodology_response

    for overrides in ({}, {"index_methodology": METHOD_MARKET_BENCHMARK}):
        rows = [*STR_ROWS, *MARKET_ROWS]
        r = resolve_index_by_year(rows, overrides, hold_years=3, calendar_years=[2025, 2026, 2027])
        assert r.comp_set_occupancy[0].value == pytest.approx(COMP_OCC)
        assert r.comp_set_occupancy[1].value == pytest.approx(COMP_OCC * 1.005)
        assert r.comp_set_adr[2].value == pytest.approx(COMP_ADR * 1.031**2)
        assert "grown 2 years at +3.10% market ADR growth" in (r.comp_set_adr[2].detail or "")
        # The growth document is named as an input of the grown cell.
        assert any(ref.doc_name == MARKET_DOC for ref in r.comp_set_adr[2].inputs)
        out = index_methodology_response(uuid4(), resolve_index_methodology(rows, overrides), r).model_dump(mode="json")
        comp = out["comp_set_by_year"]
        assert [c["year"] for c in comp] == [1, 2, 3]
        assert [c["calendar_year"] for c in comp] == [2025, 2026, 2027]
        assert comp[1]["occupancy"]["value"] == pytest.approx(COMP_OCC * 1.005)
        assert comp[1]["occupancy"]["source"] == "computed"


def test_comp_set_out_years_are_none_with_the_reason_without_a_growth_value() -> None:
    r = resolve_index_by_year(STR_ROWS, {}, hold_years=3, calendar_years=[2025, 2026, 2027])
    assert r.comp_set_occupancy[0].value == pytest.approx(COMP_OCC)
    assert r.comp_set_occupancy[1].value is None
    assert "No market occupancy growth assumption" in (r.comp_set_occupancy[1].detail or "")
    # An analyst override is a growth value too.
    r2 = resolve_index_by_year(
        STR_ROWS, {KEY_ADR_GROWTH: {"value": 0.02, "note": "g"}}, hold_years=2, calendar_years=[2025, 2026]
    )
    assert r2.comp_set_adr[1].value == pytest.approx(COMP_ADR * 1.02)
    assert "your override" in (r2.comp_set_adr[1].detail or "")


def test_no_str_report_means_no_comp_set_series_with_the_reason() -> None:
    r = resolve_index_by_year(MARKET_ROWS, {}, hold_years=2, calendar_years=[])
    assert all(f.value is None for f in r.comp_set_occupancy)
    assert "No STR Trend report" in (r.comp_set_occupancy[0].detail or "")
