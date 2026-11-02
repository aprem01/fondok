"""FON-116 / FON-117 — topline v2 against the reference Index Analysis tab
(``Kimpton Angler 12.10.25 v1.xlsx`` → ``evals/golden-set/kimpton-angler/expected/
index_analysis_reference.json``, a copy of the plan folder's
``docs/plans/go-live-2026-10-30/kimpton-index-analysis-reference.json``), plan §5.1.4.

The reference builds the subject's occupancy / ADR as
``competitive set × penetration``, where the competitive set is the STR
Running-12-Month comp set to May 2025 (keyed 75.5% / $310.78) grown year by
year: 2026 at the analyst's own 0.0% / −2.0%, 2027–2029 at the CoStar Miami
Beach Luxury & Upper Upscale forecast (p.26), 2030–2032 at the analyst's own
figures; occupancy penetration 100 every year, ADR 97 / 98 / 99 / 100.

Three cases:

* ``exact`` — the reference's own keyed comp-set level → every year's
  occupancy / ADR / RevPAR, the comp-set row and the growth rows match to
  1e-9 (the arithmetic is identical).
* ``documents`` — the same scenario read end to end from the deal's
  documents (the May 2025 STAR report through the STR template extractor,
  the CoStar submarket extraction). The only difference from the reference is
  the comp-set LEVEL, which the reference keyed rounded (75.496% → 75.5%,
  $310.776 → $310.78): every year's occupancy (ADR) differs by exactly that
  one ratio, nothing else.
* ``defaults`` — no analyst topline input: penetration 100 with the
  85 / 95 / 100 ramp for a Year-3 stabilization and the document growth
  (CoStar 2026–2029, carried forward after) — pins our default path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

import pytest

from app.engines.revenue import RevenueEngine
from app.services.index_methodology import rows_from_records
from app.services.topline import build_topline, read_topline_inputs
from app.services.topline_benchmark import BenchmarkResolution, resolve_benchmark
from app.services.topline_growth import resolve_growth_by_year
from app.tenants import TenantDataPolicies
from fondok_schemas.underwriting import RevenueEngineInput

REPO = Path(__file__).resolve().parents[3]
REFERENCE = json.loads(
    (REPO / "evals" / "golden-set" / "kimpton-angler" / "expected" / "index_analysis_reference.json").read_text()
)["blocks"]
INPUT = REPO / "evals" / "golden-set" / "kimpton-angler" / "input"
YEARS = list(range(2026, 2033))
HOLD = len(YEARS)

#: The reference's analyst inputs, in Fondok's override keys (index y−1 =
#: model year y = calendar 2026 + y − 1). ``None`` = not set → the document
#: forecast for that year (CoStar 2027–2029).
SCENARIO: dict[str, object] = {
    "occupancy_growth_by_year": [0.0, None, None, None, 0.005, 0.0, 0.0],
    "adr_growth_by_year": [-0.02, None, None, None, 0.02, 0.0425, 0.04],
    "index_mpi_target": 100,
    "index_ari_target": 100,
    "index_mpi_target_by_year": [100, 100, 100, 100],
    "index_ari_target_by_year": [97, 98, 99, 100],
}


def _rows(name: str, doc_type: str) -> list:
    d = json.loads((INPUT / name).read_text())
    return rows_from_records(
        [
            {
                "extraction_id": f"ext-{name}",
                "fields": d["fields"],
                "document_id": d["document_id"],
                "filename": d["filename"],
                "doc_type": doc_type,
                "report_as_of": d.get("report_as_of"),
                "created_at": "2025-12-11",
            }
        ]
    )


def costar_rows() -> list:
    return _rows("costar_submarket_extracted.json", "MARKET_STUDY")


def str_rows() -> list:
    return _rows("str_ang_2025_05_extracted.json", "STR_TREND")


def _run(bench: BenchmarkResolution, overrides: dict[str, object], rows: list):
    base = dict(overrides)
    inputs = read_topline_inputs(base, {}, set(overrides))
    growth = resolve_growth_by_year(
        rows, inputs.growth, calendar_years=YEARS, hold_years=HOLD,
        class_bucket="luxury_upper_upscale", policy=TenantDataPolicies(),
        first_calendar_year=(bench.base_calendar_year + 1) if bench.base_calendar_year else None,
    )
    build = build_topline(bench, growth, inputs, hold_years=HOLD, t12=None)
    out = RevenueEngine().run(
        RevenueEngineInput(
            deal_id=uuid4(),
            keys=132,
            starting_occupancy=0.83,  # the subject's own level — never read: every year is driven
            starting_adr=233.45,
            hold_years=HOLD,
            acquisition_close_date="2025-12-31",
            occupancy_growth=0.0,
            adr_growth=0.0,
            occupancy_growth_by_year=build.occupancy_growth_by_year,
            adr_growth_by_year=build.adr_growth_by_year,
            topline=build.drivers,
        )
    )
    return build, out


def _keyed_comp_set() -> BenchmarkResolution:
    comp = REFERENCE["COMPETITIVE SET"]
    return BenchmarkResolution(
        level="comp_set",
        label="Reference comp set 2025 (keyed)",
        occupancy=comp["Occupancy"]["2025"],
        adr=comp["ADR"]["2025"],
        base_calendar_year=2025,
        basis="actual",
    )


def test_exact_reference_path_from_the_keyed_comp_set() -> None:
    build, out = _run(_keyed_comp_set(), SCENARIO, costar_rows())
    subj, comp = REFERENCE["SUBJECT PROPERTY"], REFERENCE["COMPETITIVE SET"]
    assert out.topline_methodology_version == 2
    assert out.projection_calendar_years == YEARS
    for i, cal in enumerate(YEARS):
        y = out.years[i]
        k = str(cal)
        assert y.occupancy == pytest.approx(subj["Occupancy"][k], rel=1e-9, abs=0), cal
        assert y.adr == pytest.approx(subj["ADR"][k], rel=1e-9, abs=0), cal
        assert y.revpar == pytest.approx(subj["RevPAR"][k], rel=1e-9, abs=0), cal
        b = build.drivers.benchmark_by_year[i]
        assert b.occupancy == pytest.approx(comp["Occupancy"][k], rel=1e-9, abs=0), cal
        assert b.adr == pytest.approx(comp["ADR"][k], rel=1e-9, abs=0), cal
        g = build.status["growth_by_year"][i]
        assert g["occupancy"]["value"] == pytest.approx(comp["Occupancy Growth"][k], abs=1e-12), cal
        assert g["adr"]["value"] == pytest.approx(comp["ADR Growth"][k], abs=1e-12), cal
    # 2027–2029 are the CoStar forecast the reference also used.
    srcs = [(g["occupancy"]["source"], g["adr"]["source"]) for g in build.status["growth_by_year"]]
    assert srcs[1:4] == [("costar_submarket_forecast", "costar_submarket_forecast")] * 3
    assert srcs[0] == ("analyst_override", "analyst_override")
    assert build.status["growth_state"] == "sourced"


def test_costar_growth_equals_the_reference_submarket_row() -> None:
    """The submarket row of the reference grows at exactly the CoStar Luxury &
    Upper Upscale forecast Fondok reads off p.26 for 2026–2029."""
    inputs = read_topline_inputs({}, {}, set())
    growth = resolve_growth_by_year(
        costar_rows(), inputs.growth, calendar_years=YEARS, hold_years=HOLD,
        class_bucket="luxury_upper_upscale", policy=TenantDataPolicies(),
    )
    sub = REFERENCE["Miami Beach Luxury & Upper Upscale"]
    for i, cal in enumerate(range(2026, 2030)):
        occ, adr = growth.year(i + 1, "occupancy"), growth.year(i + 1, "adr")
        assert occ.source == adr.source == "costar_submarket_forecast"
        assert occ.page == 26
        assert occ.value == pytest.approx(sub["Occupancy Growth"][str(cal)], abs=1e-12)
        assert adr.value == pytest.approx(sub["ADR Growth"][str(cal)], abs=1e-12)
    # Past the report's last forecast year (2029) the growth is carried forward.
    assert growth.year(5, "adr").source == "carried_forward"
    assert growth.year(5, "adr").value == pytest.approx(0.028)


def test_documents_path_differs_only_by_the_reference_rounding_of_the_comp_set() -> None:
    rows = [*str_rows(), *costar_rows()]
    bench = resolve_benchmark(rows, class_bucket="luxury_upper_upscale", forced_level=None, policy=TenantDataPolicies())
    assert bench.level == "comp_set" and bench.source_id == "str_comp_set"
    assert bench.base_calendar_year == 2025
    assert bench.label.startswith("STR comp set TTM May 2025")
    # The STR Running-12-Month comp set (subject ÷ index), unrounded.
    assert bench.occupancy == pytest.approx(0.754959, abs=5e-7)
    assert bench.adr == pytest.approx(310.7753, abs=5e-4)
    assert bench.published_mpi == pytest.approx(109.793)
    assert bench.published_ari == pytest.approx(75.1173)

    _build, out = _run(bench, SCENARIO, rows)
    subj, comp = REFERENCE["SUBJECT PROPERTY"], REFERENCE["COMPETITIVE SET"]
    occ_ratio = bench.occupancy / comp["Occupancy"]["2025"]
    adr_ratio = bench.adr / comp["ADR"]["2025"]
    assert abs(occ_ratio - 1) < 6e-5 and abs(adr_ratio - 1) < 2e-5
    for i, cal in enumerate(YEARS):
        y, k = out.years[i], str(cal)
        assert y.occupancy == pytest.approx(subj["Occupancy"][k] * occ_ratio, rel=1e-9), cal
        assert y.adr == pytest.approx(subj["ADR"][k] * adr_ratio, rel=1e-9), cal
        assert y.revpar == pytest.approx(subj["RevPAR"][k] * occ_ratio * adr_ratio, rel=1e-9), cal


def test_defaults_path_is_penetration_100_with_the_85_95_ramp() -> None:
    rows = [*str_rows(), *costar_rows()]
    bench = resolve_benchmark(rows, class_bucket="luxury_upper_upscale", forced_level=None, policy=TenantDataPolicies())
    build, out = _run(bench, {}, rows)
    status = build.status
    assert status["penetration_occ"] == status["penetration_adr"] == 100.0
    assert status["penetration_occ_source"] == "default"
    assert status["ramp_by_year"] == [0.85, 0.95] and status["ramp_source"] == "default"
    assert status["stabilization_year"] == 3
    # The current STR indices are printed beside the 100 default.
    assert status["current_mpi"]["value"] == pytest.approx(109.793)
    assert "MPI 109.8" in status["current_mpi"]["label"]
    ramps = [0.85, 0.95, 1, 1, 1, 1, 1]
    for i, b in enumerate(build.drivers.benchmark_by_year):
        assert out.years[i].occupancy == pytest.approx(b.occupancy * ramps[i], rel=1e-12)
        assert out.years[i].adr == pytest.approx(b.adr * ramps[i], rel=1e-12)
    # Document growth only: CoStar 2026–2029, carried forward 2030–2032.
    sources = [g["adr"]["source"] for g in status["growth_by_year"]]
    assert sources == ["costar_submarket_forecast"] * 4 + ["carried_forward"] * 3
    assert status["suggestions"] == []
    # Pinned default path (the comp set grown at CoStar L&UU growth).
    expected_occ = [0.754959 * 0.988 * 0.85, 0.754959 * 0.988 * 0.95]
    assert out.years[0].occupancy == pytest.approx(expected_occ[0], rel=1e-6)
    assert out.years[1].occupancy == pytest.approx(expected_occ[1], rel=1e-6)
    assert out.years[0].adr == pytest.approx(310.7753 * 1.018 * 0.85, rel=1e-6)
