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

Domain-lead decisions 2026-11-03 (``test_topline_decisions.py``): occupancy is
FLAT after stabilization (1b) and ADR past a forecast grows at the forecast's
average (1a). The reference tab keeps moving occupancy after stabilization
(CoStar's 2029 +3.0%), so 1b DELIBERATELY differs from it: with the default
Year-3 stabilization (2028) the ADR path and occupancy through 2028 match
the tab exactly; occupancy from 2029 on is the tab's ÷ 1.03 (the held 2029
step), pinned below. 1a never meets the tab (her 2030–2032 ADR growth is
her own per-year figure); it is pinned on the defaults path.

Cases:

* ``exact`` — the reference's own keyed comp-set level → the ADR path every
  year, occupancy / RevPAR through stabilization and the growth rows match
  to 1e-9 (the arithmetic is identical); 2029–2032 occupancy differs by
  exactly the held +3.0% step (decision 1b).
* ``exact_s4`` — the same inputs with stabilization in Year 4 (2029, the year
  her ADR penetration reaches 100): 2029 is the stabilization year itself, so
  its growth applies, and her 2030 +0.5% is her own per-year figure (it still
  wins) — the whole tab reproduces to 1e-9.
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

from app.engines.deal_years import deal_years
from app.engines.revenue import RevenueEngine
from app.services.index_methodology import rows_from_records
from app.services.topline import build_topline, read_topline_inputs
from app.services.topline_benchmark import BenchmarkResolution, resolve_benchmark
from app.services.topline_growth import resolve_growth_by_year
from app.tenants import TenantDataPolicies
from tests.fixtures.deal_calendar import calendar_deal_years
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


def _run(
    bench: BenchmarkResolution,
    overrides: dict[str, object],
    rows: list,
    *,
    close: str = "2025-12-31",
    hold: int = HOLD,
):
    base = dict(overrides)
    inputs = read_topline_inputs(base, {}, set(overrides))
    growth = resolve_growth_by_year(
        rows, inputs.growth, deal_years=deal_years(close, hold), hold_years=hold,
        class_bucket="luxury_upper_upscale", policy=TenantDataPolicies(),
        benchmark_base_year=bench.base_calendar_year,
        # As the loader does: occupancy flat after the stabilization year (1b).
        stabilization_year=inputs.stabilization_year,
    )
    build = build_topline(bench, growth, inputs, hold_years=hold, t12=None)
    out = RevenueEngine().run(
        RevenueEngineInput(
            deal_id=uuid4(),
            keys=132,
            starting_occupancy=0.83,  # the subject's own level — never read: every year is driven
            starting_adr=233.45,
            hold_years=hold,
            acquisition_close_date=close,
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


#: Decision 1b against the tab, default Year-3 stabilization (2028): the
#: tab's 2029 +3.0% occupancy step is held, so occupancy from 2029 on is the
#: tab's ÷ 1.03 (2030's +0.5% is her own per-year figure and still applies).
HELD_2029_STEP = 1.03
HELD_FROM = 2029


def test_exact_reference_path_from_the_keyed_comp_set() -> None:
    build, out = _run(_keyed_comp_set(), SCENARIO, costar_rows())
    subj, comp = REFERENCE["SUBJECT PROPERTY"], REFERENCE["COMPETITIVE SET"]
    assert out.topline_methodology_version == 7
    assert [d.label for d in out.projection_deal_years] == [f"Jan–Dec {c}" for c in YEARS]
    assert build.status["stabilization_year"] == 3
    for i, cal in enumerate(YEARS):
        y = out.years[i]
        k = str(cal)
        held = HELD_2029_STEP if cal >= HELD_FROM else 1.0
        # ADR: the tab exactly, every year.
        assert y.adr == pytest.approx(subj["ADR"][k], rel=1e-9, abs=0), cal
        # Occupancy: the tab exactly through stabilization; after it, the
        # tab without the held 2029 step (decision 1b).
        assert y.occupancy == pytest.approx(subj["Occupancy"][k] / held, rel=1e-9, abs=0), cal
        assert y.revpar == pytest.approx(subj["RevPAR"][k] / held, rel=1e-9, abs=0), cal
        b = build.drivers.benchmark_by_year[i]
        assert b.occupancy == pytest.approx(comp["Occupancy"][k] / held, rel=1e-9, abs=0), cal
        assert b.adr == pytest.approx(comp["ADR"][k], rel=1e-9, abs=0), cal
        g = build.status["growth_by_year"][i]
        expected_occ_g = 0.0 if cal == HELD_FROM else comp["Occupancy Growth"][k]
        assert g["occupancy"]["value"] == pytest.approx(expected_occ_g, abs=1e-12), cal
        assert g["adr"]["value"] == pytest.approx(comp["ADR Growth"][k], abs=1e-12), cal
    srcs = [(g["occupancy"]["source"], g["adr"]["source"]) for g in build.status["growth_by_year"]]
    assert srcs[0] == ("analyst_override", "analyst_override")
    # 2027–2028 are the CoStar forecast the reference also used; 2029's ADR
    # too, while its occupancy is held after the Year-3 stabilization.
    assert srcs[1:3] == [("costar_submarket_forecast", "costar_submarket_forecast")] * 2
    assert srcs[3] == ("held_at_stabilization", "costar_submarket_forecast")
    # 2030: her own per-year +0.5% still wins over the rule.
    assert srcs[4] == ("analyst_override", "analyst_override")
    assert build.status["growth_state"] == "sourced"


def test_reference_differences_under_decision_1b_are_the_held_2029_step() -> None:
    """The exact per-year gap to the tab (reported to the domain lead)."""
    _build, out = _run(_keyed_comp_set(), SCENARIO, costar_rows())
    subj = REFERENCE["SUBJECT PROPERTY"]
    gaps = {
        cal: out.years[i].occupancy - subj["Occupancy"][str(cal)] for i, cal in enumerate(YEARS)
    }
    assert all(gaps[c] == pytest.approx(0.0, abs=1e-12) for c in (2026, 2027, 2028))
    for c in (2029, 2030, 2031, 2032):
        assert gaps[c] == pytest.approx(subj["Occupancy"][str(c)] * (1 / 1.03 - 1), rel=1e-9)
    # 2029: 76.8318% in the tab vs 74.5940% (the 2028 level held).
    assert out.years[3].occupancy == pytest.approx(0.755 * 0.988, rel=1e-12)
    assert subj["Occupancy"]["2029"] == pytest.approx(0.7683182, abs=1e-7)


def test_exact_reference_path_with_stabilization_in_year_4_is_the_whole_tab() -> None:
    build, out = _run(_keyed_comp_set(), {**SCENARIO, "stabilization_year": 4}, costar_rows())
    subj = REFERENCE["SUBJECT PROPERTY"]
    assert build.status["stabilization_year"] == 4
    for i, cal in enumerate(YEARS):
        y, k = out.years[i], str(cal)
        assert y.occupancy == pytest.approx(subj["Occupancy"][k], rel=1e-9, abs=0), cal
        assert y.adr == pytest.approx(subj["ADR"][k], rel=1e-9, abs=0), cal
        assert y.revpar == pytest.approx(subj["RevPAR"][k], rel=1e-9, abs=0), cal
    occ_sources = [g["occupancy"]["source"] for g in build.status["growth_by_year"]]
    # 2031–2032 are her own 0.0% per-year figures (they win over the rule).
    assert occ_sources[4:] == ["analyst_override"] * 3


def test_costar_growth_equals_the_reference_submarket_row() -> None:
    """The submarket row of the reference grows at exactly the CoStar Luxury &
    Upper Upscale forecast Fondok reads off p.26 for 2026–2029."""
    inputs = read_topline_inputs({}, {}, set())
    growth = resolve_growth_by_year(
        costar_rows(), inputs.growth, deal_years=calendar_deal_years(YEARS), hold_years=HOLD,
        class_bucket="luxury_upper_upscale", policy=TenantDataPolicies(),
    )
    sub = REFERENCE["Miami Beach Luxury & Upper Upscale"]
    for i, cal in enumerate(range(2026, 2030)):
        occ, adr = growth.year(i + 1, "occupancy"), growth.year(i + 1, "adr")
        assert occ.source == adr.source == "costar_submarket_forecast"
        assert occ.page == 26
        assert occ.value == pytest.approx(sub["Occupancy Growth"][str(cal)], abs=1e-12)
        assert adr.value == pytest.approx(sub["ADR Growth"][str(cal)], abs=1e-12)
    # Past the report's last forecast year (2029): ADR at the report's
    # published forecast average (FON-121 decision 2: p.4, +2.5%), not 2029's
    # +2.8%.
    assert growth.year(5, "adr").source == "carried_forward"
    assert growth.year(5, "adr").value == pytest.approx(0.025)


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
        held = HELD_2029_STEP if cal >= HELD_FROM else 1.0  # decision 1b, see the exact case
        assert y.occupancy == pytest.approx(subj["Occupancy"][k] * occ_ratio / held, rel=1e-9), cal
        assert y.adr == pytest.approx(subj["ADR"][k] * adr_ratio, rel=1e-9), cal
        assert y.revpar == pytest.approx(subj["RevPAR"][k] * occ_ratio * adr_ratio / held, rel=1e-9), cal


def test_defaults_path_is_penetration_100_on_the_straight_line_from_todays_index() -> None:
    rows = [*str_rows(), *costar_rows()]
    bench = resolve_benchmark(rows, class_bucket="luxury_upper_upscale", forced_level=None, policy=TenantDataPolicies())
    build, out = _run(bench, {}, rows)
    status = build.status
    assert status["penetration_occ"] == status["penetration_adr"] == 100.0
    assert status["penetration_occ_source"] == "default"
    assert status["ramp_mode"] == "operating"
    assert status["ramp_by_year"] == [] and status["ramp_source"] == "straight_line"
    assert status["stabilization_year"] == 3
    # The current STR indices are printed beside the 100 default.
    assert status["current_mpi"]["value"] == pytest.approx(109.793)
    assert "MPI 109.8" in status["current_mpi"]["label"]
    mpi, ari = status["current_mpi"]["value"], status["current_ari"]["value"]
    for i, b in enumerate(build.drivers.benchmark_by_year):
        t = min(i + 1, 3) / 3
        assert out.years[i].occupancy == pytest.approx(b.occupancy * (mpi + (100 - mpi) * t) / 100, rel=1e-12)
        assert out.years[i].adr == pytest.approx(b.adr * (ari + (100 - ari) * t) / 100, rel=1e-12)
    # Document growth only: CoStar 2026–2029, carried forward 2030–2032.
    sources = [g["adr"]["source"] for g in status["growth_by_year"]]
    assert sources == ["costar_submarket_forecast"] * 4 + ["carried_forward"] * 3
    assert status["suggestions"] == []
    # Decision 1b: occupancy flat after the Year-3 stabilization (2028 level).
    occ_src = [g["occupancy"]["source"] for g in status["growth_by_year"]]
    assert occ_src[3:] == ["held_at_stabilization"] * 4
    assert all(out.years[i].occupancy == pytest.approx(out.years[2].occupancy, rel=1e-12) for i in range(3, 7))
    # Decision 1a / FON-121 decision 2: ADR 2030–2032 at CoStar's published
    # forecast average (+2.5%, p.4).
    for i in (4, 5, 6):
        assert status["growth_by_year"][i]["adr"]["value"] == pytest.approx(0.025, abs=1e-15)
        assert status["growth_by_year"][i]["adr"]["chip"] == "CoStar p.4"
        assert out.years[i].adr == pytest.approx(out.years[i - 1].adr * 1.025, rel=1e-12)
    # Pinned default path (the comp set grown at CoStar L&UU growth).
    line = lambda i, y: (i + (100 - i) * y / 3) / 100  # noqa: E731
    expected_occ = [0.754959 * 0.988 * line(mpi, 1), 0.754959 * 0.988 * line(mpi, 2)]
    assert out.years[0].occupancy == pytest.approx(expected_occ[0], rel=1e-6)
    assert out.years[1].occupancy == pytest.approx(expected_occ[1], rel=1e-6)
    assert out.years[0].adr == pytest.approx(310.7753 * 1.018 * line(ari, 1), rel=1e-6)


#: FON-121 — Sam's REAL close, 2025-09-30, on her own convention: model years
#: run twelve months from the close, so Year 1 = Oct 2025–Sep 2026 — her
#: "2026" column — and the tab's per-year keys apply unshifted. What differs
#: from her tab is the market growth: she applies each CoStar CALENDAR year
#: straight to the column ending in it; Fondok blends the calendar years each
#: deal year spans (Oct 2026–Sep 2027 = 3 months of 2026 + 9 months of 2027).
REAL_CLOSE = "2025-09-30"
#: CoStar Miami Beach L&UU (p.26) — occupancy / ADR change by calendar year.
LUU_OCC = {2026: -0.012, 2027: 0.0, 2028: -0.012, 2029: 0.03}
LUU_ADR = {2026: 0.018, 2027: -0.006, 2028: 0.038, 2029: 0.028}


def _blend(series: dict[int, float], end_year: int) -> float:
    """Growth INTO the deal year Oct (end_year − 1)–Sep end_year."""
    return 3 / 12 * series[end_year - 1] + 9 / 12 * series[end_year]


def test_reference_on_the_real_close_is_her_tab_with_deal_year_blended_growth() -> None:
    build, out = _run(_keyed_comp_set(), SCENARIO, costar_rows(), close=REAL_CLOSE)
    subj, comp = REFERENCE["SUBJECT PROPERTY"], REFERENCE["COMPETITIVE SET"]
    assert [d.label for d in out.projection_deal_years] == [
        f"Oct {c - 1}–Sep {c}" for c in YEARS
    ]
    assert build.status["stabilization_year"] == 3
    g = build.status["growth_by_year"]
    # Year 1 (Oct 2025–Sep 2026, her 2026): her own 0.0% / −2.0% — exact.
    assert out.years[0].occupancy == pytest.approx(subj["Occupancy"]["2026"], rel=1e-12)
    assert out.years[0].adr == pytest.approx(subj["ADR"]["2026"], rel=1e-12)
    # Years 2–4: the blended CoStar growth (occupancy held after Year 3).
    occ_g = [0.0, _blend(LUU_OCC, 2027), _blend(LUU_OCC, 2028), 0.0, 0.005, 0.0, 0.0]
    adr_g = [-0.02, _blend(LUU_ADR, 2027), _blend(LUU_ADR, 2028), _blend(LUU_ADR, 2029), 0.02, 0.0425, 0.04]
    assert [row["occupancy"]["value"] for row in g] == pytest.approx(occ_g, abs=1e-15)
    assert [row["adr"]["value"] for row in g] == pytest.approx(adr_g, abs=1e-15)
    assert g[3]["occupancy"]["source"] == "held_at_stabilization"
    assert "Year 3 (Oct 2027–Sep 2028)" in g[3]["occupancy"]["label"]
    assert g[1]["adr"]["label"].startswith("Forecast · Oct 2026–Sep 2027 = 3 mo × +1.8% CoStar 2026 forecast p.26")
    # The whole path: her comp-set level grown by those figures × her penetration.
    ari = [97, 98, 99, 100, 100, 100, 100]
    occ, adr = comp["Occupancy"]["2025"], comp["ADR"]["2025"]
    for i in range(HOLD):
        occ *= 1 + occ_g[i]
        adr *= 1 + adr_g[i]
        assert out.years[i].occupancy == pytest.approx(occ, rel=1e-12), i + 1
        assert out.years[i].adr == pytest.approx(adr * ari[i] / 100, rel=1e-12), i + 1


def test_real_close_gap_to_her_tab_is_the_calendar_blend_alone() -> None:
    """Where Fondok and her tab differ on her convention, pinned (reported to
    the domain lead): Year 1 matches; from Year 2 every gap is the blend of
    the CoStar calendar years (plus decision 1b's held occupancy from Year 4)."""
    _build, out = _run(_keyed_comp_set(), SCENARIO, costar_rows(), close=REAL_CLOSE)
    subj = REFERENCE["SUBJECT PROPERTY"]
    adr = {c: out.years[i].adr for i, c in enumerate(YEARS)}
    occ = {c: out.years[i].occupancy for i, c in enumerate(YEARS)}
    assert adr[2026] == pytest.approx(subj["ADR"]["2026"], rel=1e-12)
    # Her 2027 ADR grows −0.6% (CoStar 2027) over 2026 at ARI 97 → 98;
    # Fondok's Oct 2026–Sep 2027 grows 0.0% (¼ × +1.8% + ¾ × −0.6%).
    assert adr[2027] / adr[2026] == pytest.approx(1.0 * 98 / 97, rel=1e-12)
    assert subj["ADR"]["2027"] / subj["ADR"]["2026"] == pytest.approx(0.994 * 98 / 97, rel=1e-9)
    # Occupancy: her 2027 is flat (CoStar 2027 0.0%); Fondok −0.3% (¼ × −1.2%).
    assert occ[2027] / occ[2026] == pytest.approx(1 - 0.003, rel=1e-12)
    assert subj["Occupancy"]["2027"] / subj["Occupancy"]["2026"] == pytest.approx(1.0, rel=1e-9)
