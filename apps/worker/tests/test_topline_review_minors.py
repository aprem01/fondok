"""Review minors M-2 / M-4 (W5 topline fix)."""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from uuid import uuid4

import pytest

from app.services.topline import build_topline, read_topline_inputs
from app.services.topline_benchmark import BenchmarkResolution
from app.services.topline_growth import resolve_growth_by_year
from app.tenants import TenantDataPolicies


def _build(bench: BenchmarkResolution):
    inputs = read_topline_inputs({"starting_adr": 280.0}, {}, {"starting_adr"}, str_noted_keys={"starting_adr"})
    g = resolve_growth_by_year([], inputs.growth, calendar_years=[2026, 2027, 2028], hold_years=3,
                               class_bucket="all", policy=TenantDataPolicies())
    return build_topline(bench, g, inputs, hold_years=3, t12=None)


def test_str_noted_year1_flag_says_what_happens_with_a_benchmark() -> None:
    build = _build(BenchmarkResolution(level="comp_set", label="Comp", occupancy=0.7, adr=300.0,
                                       base_calendar_year=2026, basis="actual"))
    flag = build.status["legacy_flags"][0]
    assert flag["treatment"] == "treated_as_legacy_seed"
    assert "benchmark × penetration × ramp sets the year" in flag["detail"]


def test_str_noted_year1_flag_with_no_benchmark_says_the_rate_stays() -> None:
    """M-2 — with no benchmark the STR-noted value stays Year 1; the flag
    must not claim the benchmark sets the year."""
    build = _build(BenchmarkResolution(level="none", label="No benchmark"))
    flag = build.status["legacy_flags"][0]
    assert flag["treatment"] == "kept_no_benchmark"
    assert "stays the Year-1 rate" in flag["detail"]
    assert "sets the year" not in flag["detail"]


def test_revenue_input_without_the_resolved_lists_fails_loud() -> None:
    """M-4 — never silently compound the 0.0 scalar fallback."""
    from app.services.engine_runner import _build_input_for, _default_assumptions

    base = {**_default_assumptions(), "keys": 120, "purchase_price": 1.0e7,
            "starting_occupancy": 0.7, "starting_adr": 250.0}
    with pytest.raises(ValueError, match="per-year growth lists"):
        _build_input_for("revenue", str(uuid4()), base, {})
    base.update(occupancy_growth_by_year=[None] * 5, adr_growth_by_year=[None] * 5)
    assert _build_input_for("revenue", str(uuid4()), base, {}).starting_adr == 250.0
