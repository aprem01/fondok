"""FON-116 / FON-117 — the topline growth and benchmark resolvers and the
builder (``topline-growth-plan.md`` §2.2–2.5, §3.4)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

import pytest

from app.services.market_fields import FieldRow
from app.services.topline import build_topline, coerce_ramp_list, read_topline_inputs
from app.services.topline_benchmark import BenchmarkResolution, resolve_benchmark
from app.services.topline_growth import (
    GrowthOverrides,
    class_bucket_from_positioning,
    coerce_growth_list,
    resolve_growth_by_year,
)
from app.tenants import TenantDataPolicies
from tests.fixtures.deal_calendar import calendar_deal_years
from fondok_schemas.reasons import ReasonCode
from tests.test_topline_kimpton_reference import costar_rows, str_rows

ALLOW = TenantDataPolicies()
WITHHOLD = TenantDataPolicies(costar_derived_defaults="withhold")
CAL = [2026, 2027, 2028, 2029, 2030]


def _row(name: str, value, *, doc_type: str = "CBRE_HORIZONS", doc: str = "Horizons.xlsx",
         page: int | None = 4, ext: str = "cbre-1", period: str | None = None) -> FieldRow:
    return FieldRow(field_name=name, value=value, unit=None, page=page, doc_name=doc, doc_id="d-" + ext,
                    extraction_id=ext, doc_type=doc_type, period=period)


def cbre_rows(years: dict[int, tuple[float, float]], *, period: str = "forecast") -> list[FieldRow]:
    out: list[FieldRow] = []
    for y, (occ, adr) in years.items():
        out += [
            _row(f"cbre_horizons.segment_all.{y}.occupancy_change_pct", occ),
            _row(f"cbre_horizons.segment_all.{y}.adr_change_pct", adr),
            _row(f"cbre_horizons.segment_all.{y}.period", period),
        ]
    return out


def _growth(rows, overrides=GrowthOverrides(), *, cal=CAL, bucket="luxury_upper_upscale", policy=ALLOW, **kw):
    return resolve_growth_by_year(rows, overrides, deal_years=calendar_deal_years(cal), hold_years=len(cal) or 5,
                                  class_bucket=bucket, policy=policy, **kw)


# ─────────────────────────── growth precedence ───────────────────────────


def test_precedence_override_then_cbre_then_costar_then_carried() -> None:
    rows = [*cbre_rows({2026: (0.01, 0.02)}), *costar_rows()]
    g = _growth(rows, GrowthOverrides(adr_by_year=(None, 0.05)))
    # 2026: CBRE beats CoStar (plan order); 2027 ADR: the analyst's Year-2 figure.
    assert g.year(1, "adr").source == "cbre_horizons" and g.year(1, "adr").value == pytest.approx(0.02)
    assert g.year(2, "adr").source == "analyst_override" and g.year(2, "adr").value == pytest.approx(0.05)
    # 2027 occupancy: CBRE's series ended in 2026, but CoStar prints 2027 — a
    # document forecast wins over carrying CBRE forward.
    assert g.year(2, "occupancy").source == "costar_submarket_forecast"
    # 2030: past CoStar's last forecast year (2029) → ADR at the report's
    # published forecast average (FON-121 decision 2; p.4), occupancy HELD at
    # its 2029 level (review I-2).
    occ30, adr30 = g.year(5, "occupancy"), g.year(5, "adr")
    assert occ30.source == "carried_forward" and occ30.value == 0.0
    assert "held at its 2029 level" in occ30.label and "CoStar Miami Beach submarket" in occ30.label
    assert adr30.source == "carried_forward" and adr30.value == pytest.approx(0.025)
    assert "CoStar forecast average (as published) +2.5% · Miami Beach submarket" in adr30.label


def test_all_years_scalar_beats_documents_and_legacy_key_reads_as_scalar() -> None:
    base = {"index_market_occupancy_growth": 0.01, "adr_growth": 0.03}
    inputs = read_topline_inputs(base, {}, {"index_market_occupancy_growth", "adr_growth"})
    g = _growth(costar_rows(), inputs.growth)
    assert all(g.year(y, "occupancy").value == pytest.approx(0.01) for y in range(1, 6))
    assert "legacy key index_market_occupancy_growth" in g.year(1, "occupancy").label
    assert all(g.year(y, "adr").source == "analyst_override" for y in range(1, 6))
    assert inputs.legacy_flags[0]["reason"] == ReasonCode.LEGACY_KEY_TRANSLATED.value


def test_revpar_override_derives_adr_per_year_over_the_resolved_occupancy() -> None:
    g = _growth(costar_rows(), GrowthOverrides(revpar_all_years=0.04))
    for y in range(1, 6):
        occ, adr = g.year(y, "occupancy").value, g.year(y, "adr").value
        assert (1 + occ) * (1 + adr) - 1 == pytest.approx(0.04)
        assert g.year(y, "adr").source == "analyst_override"


def test_no_source_is_unavailable_never_a_seed() -> None:
    g = _growth([])
    for y in range(1, 6):
        for m in ("occupancy", "adr"):
            assert g.year(y, m).value is None
            assert g.year(y, m).source == "unavailable"
            assert g.year(y, m).reason is ReasonCode.NO_MARKET_FORECAST
    assert g.state(range(2, 6)) == "unavailable"


def test_a_hole_inside_a_document_series_is_unavailable_not_patched() -> None:
    rows = cbre_rows({2026: (0.01, 0.02), 2028: (0.01, 0.02)})
    g = _growth(rows, bucket="all")
    assert g.year(2, "adr").source == "unavailable"  # 2027 inside 2026..2028
    assert g.year(3, "adr").source == "cbre_horizons"
    assert g.year(4, "adr").source == "carried_forward"  # 2029 past the series
    assert g.state(range(2, 6)) == "partially_sourced"


def test_withhold_policy_withholds_costar_growth() -> None:
    g = _growth(costar_rows(), policy=WITHHOLD)
    hit = g.year(2, "adr")
    assert hit.value is None and hit.source == "withheld_by_policy"
    assert hit.reason is ReasonCode.LICENSE_WITHHELD
    assert "data policy" in hit.label
    assert g.state(range(2, 6)) == "withheld"
    # Carry-forward never extends a withheld year.
    assert g.year(5, "adr").source != "carried_forward"


def test_no_close_date_places_no_document_growth() -> None:
    g = _growth(costar_rows(), GrowthOverrides(occupancy_by_year=(None, 0.01)), cal=[])
    assert g.calendar_known is False
    assert g.year(2, "occupancy").value == pytest.approx(0.01)
    assert g.year(2, "adr").value is None and "close date" in g.year(2, "adr").label


def test_reports_gated_by_the_as_of_date_say_so() -> None:
    g = _growth([], gated_doc_types=["MARKET_STUDY"])
    assert g.year(2, "adr").reason is ReasonCode.NOT_KNOWABLE_AS_OF


def test_bucket_mapping_and_overall_fallback() -> None:
    assert class_bucket_from_positioning("upper-upscale") == "luxury_upper_upscale"
    assert class_bucket_from_positioning("Luxury") == "luxury_upper_upscale"
    assert class_bucket_from_positioning("Upper Midscale") == "upscale_upper_midscale"
    assert class_bucket_from_positioning("economy") == "midscale_economy"
    assert class_bucket_from_positioning("default") == "all"
    assert class_bucket_from_positioning(None) == "all"
    g = _growth(costar_rows(), bucket="all")
    assert g.bucket_note == "All classes — no positioning tier set on the deal"
    assert g.year(1, "adr").value == pytest.approx(0.03)  # overall 2026 ADR change


def test_growth_list_and_ramp_validation() -> None:
    vals, problems = coerce_growth_list([0.02, 3, None, "", 80])
    assert vals == [0.02, 0.03, None, None, None]
    assert problems == ["Year 5: 80 is outside −50%..+50% growth"]
    ramp, rp = coerce_ramp_list([85, 0.95, None, 0])
    assert ramp == [0.85, 0.95, None, None] and len(rp) == 1


# ─────────────────────────── benchmark levels ───────────────────────────


def test_comp_set_first_then_submarket_then_market_then_none() -> None:
    rows = [*str_rows(), *costar_rows()]
    assert resolve_benchmark(rows, class_bucket="luxury_upper_upscale", forced_level=None, policy=ALLOW).level == "comp_set"

    sub = resolve_benchmark(costar_rows(), class_bucket="luxury_upper_upscale", forced_level=None, policy=ALLOW)
    assert sub.level == "submarket" and sub.source_id == "costar_submarket"
    # 2025 is tagged forecast in the report: the latest ACTUAL year is 2024.
    assert sub.base_calendar_year == 2024
    assert sub.occupancy == pytest.approx(0.71) and sub.adr == pytest.approx(392.11)
    assert "Luxury & Upper Upscale 2024 actual" in sub.label and "p.26" in sub.label
    assert sub.fallback_reason and sub.fallback_reason.startswith("Competitive set:")

    levels = [
        _row("cbre_horizons.segment_all.2025.occupancy_pct", 72.0),
        _row("cbre_horizons.segment_all.2025.adr_usd", 250.0),
        _row("cbre_horizons.segment_all.2025.period", "historical"),
    ]
    mkt = resolve_benchmark(levels, class_bucket="all", forced_level=None, policy=ALLOW)
    assert mkt.level == "market" and mkt.source_id == "cbre_horizons"
    assert mkt.occupancy == pytest.approx(0.72) and mkt.base_calendar_year == 2025

    none = resolve_benchmark([], class_bucket="all", forced_level=None, policy=ALLOW)
    assert none.level == "none"
    assert [lv for lv, _ in none.skipped] == ["comp_set", "submarket", "market"]


def test_withhold_skips_the_costar_submarket_level_and_says_so() -> None:
    b = resolve_benchmark(costar_rows(), class_bucket="luxury_upper_upscale", forced_level=None, policy=WITHHOLD)
    assert b.level == "none"
    assert "data policy" in (b.fallback_reason or "")


def test_forced_level_is_honoured_and_never_silently_replaced() -> None:
    rows = [*str_rows(), *costar_rows()]
    b = resolve_benchmark(rows, class_bucket="luxury_upper_upscale", forced_level="submarket", policy=ALLOW)
    assert b.level == "submarket" and b.forced_level == "submarket"
    b = resolve_benchmark(costar_rows(), class_bucket="all", forced_level="comp_set", policy=ALLOW)
    assert b.level == "none" and "You chose the competitive set level" in (b.fallback_reason or "")


# ─────────────────────────── builder ───────────────────────────


def _bench(**kw) -> BenchmarkResolution:
    return BenchmarkResolution(**{"level": "comp_set", "label": "Comp", "occupancy": 0.7, "adr": 300.0,
                                  "base_calendar_year": 2025, "basis": "actual", **kw})


def test_benchmark_grows_into_year_one_only_when_its_period_is_earlier() -> None:
    inputs = read_topline_inputs({}, {}, set())
    g = _growth(costar_rows(), inputs.growth, benchmark_base_year=2025)
    build = build_topline(_bench(), g, inputs, hold_years=5, t12=None)
    assert [gy.year for gy in build.drivers.growth_by_year] == [1, 2, 3, 4, 5]
    b1 = build.drivers.benchmark_by_year[0]
    assert b1.adr == pytest.approx(300.0 * 1.018) and b1.basis == "forecast"
    # The engine lists carry the applied Year-1 growth.
    assert build.adr_growth_by_year[0] == pytest.approx(0.018)

    same = build_topline(_bench(base_calendar_year=2026), _growth(costar_rows(), inputs.growth), inputs, hold_years=5, t12=None)
    assert [gy.year for gy in same.drivers.growth_by_year] == [2, 3, 4, 5]
    assert same.adr_growth_by_year[0] is None
    assert same.drivers.benchmark_by_year[0].adr == pytest.approx(300.0)
    assert same.drivers.benchmark_by_year[0].basis == "actual"


def test_unavailable_step_holds_the_benchmark_flat_and_offers_the_labelled_suggestion() -> None:
    inputs = read_topline_inputs({}, {}, set())
    build = build_topline(_bench(), _growth([], inputs.growth, benchmark_base_year=2025), inputs, hold_years=5, t12=None)
    assert all(b.adr == pytest.approx(300.0) for b in build.drivers.benchmark_by_year)
    assert all(b.basis == "assumption" for b in build.drivers.benchmark_by_year)
    assert "held flat" in build.drivers.benchmark_by_year[1].label
    assert build.status["growth_state"] == "unavailable"
    assert build.status["suggestions"] == [
        {
            "key": "revpar_growth",
            "value": 0.045,
            "label": "Apply platform suggestion 4.5% RevPAR growth (labelled; not a market forecast)",
            "note_prefill": "Platform suggestion 4.5% — no market forecast on file",
        }
    ]
    assert build.terminal_revpar_growth == 0.0
    assert build.terminal_revpar_source == "market_forecast_unavailable"


def test_explicit_year_one_override_is_not_penetrated_but_str_noted_one_is() -> None:
    inputs = read_topline_inputs({"starting_adr": 280.0}, {}, {"starting_adr"})
    build = build_topline(_bench(), _growth(costar_rows(), inputs.growth, benchmark_base_year=2025), inputs, hold_years=5, t12=None)
    assert build.drivers.explicit_year1 == ["adr"]
    assert all(b.adr is None for b in build.drivers.benchmark_by_year)
    assert build.year1_adr is None and build.year1_occupancy is not None

    legacy = read_topline_inputs({"starting_adr": 280.0}, {}, {"starting_adr"}, str_noted_keys={"starting_adr"})
    assert legacy.explicit_metrics == ()
    assert legacy.legacy_flags[0]["treatment"] == "treated_as_legacy_seed"


def test_terminal_growth_is_the_last_years_adr_growth() -> None:
    """Review I-2 (the full contract: ``test_topline_terminal_growth.py``)."""
    inputs = read_topline_inputs({}, {}, set())
    build = build_topline(_bench(), _growth(costar_rows(), inputs.growth, benchmark_base_year=2025), inputs, hold_years=3, t12=None)
    # Hold ends 2028, so the exit NOI is 2029's: CoStar L&UU +2.8% ADR; its
    # +3.0% occupancy does not enter.
    assert build.terminal_revpar_growth == pytest.approx(0.028)
    assert build.terminal_revpar_source == "costar_submarket_forecast"


def test_default_penetration_and_ramp_set_year_one() -> None:
    inputs = read_topline_inputs({}, {}, set())
    build = build_topline(_bench(), _growth(costar_rows(), inputs.growth, benchmark_base_year=2025), inputs, hold_years=5, t12=None)
    assert build.year1_occupancy == pytest.approx(0.7 * 0.988 * 0.85)
    assert build.status["ramp_state"] == "defined"
    s4 = read_topline_inputs({"stabilization_year": 4}, {}, {"stabilization_year"})
    b4 = build_topline(_bench(), _growth(costar_rows(), s4.growth, benchmark_base_year=2025), s4, hold_years=5, t12=None)
    assert b4.status["ramp_state"] == "undefined_for_stabilization_year"
    assert b4.status["ramp_by_year"] == [0.85, 0.95, None]
