"""Provenance sidecar (FON-25) — the revenue engine must emit a navigable,
self-consistent :class:`ValueTrace` for every canonical revenue line.

These lock the *mechanism*, not the revenue math (that's covered by
test_revenue_segmentation / test_engine_runner):

  1. Every projected year gets a trace for rooms_revenue and total_revenue.
  2. Each trace's declared inputs reconcile to its stated value via its
     formula — i.e. the rationale is arithmetically honest, not decorative.
  3. Every ``traces_to`` pointer resolves to another key in the same map
     (no dangling provenance edges).
  4. The single-line and segmented paths both populate provenance.
"""

from __future__ import annotations

import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from fondok_schemas.underwriting import (  # noqa: E402
    RevenueEngineInput,
    RevenueSegment,
)

from app.engines.revenue import RevenueEngine  # noqa: E402

TOL = 0.01


def _single_line_input(hold_years: int = 3) -> RevenueEngineInput:
    return RevenueEngineInput(
        deal_id=uuid4(),
        keys=120,
        starting_occupancy=0.72,
        starting_adr=210.0,
        adr_growth=0.03,
        fb_revenue_per_occupied_room=35.0,
        other_revenue_pct_of_rooms=0.05,
        starting_resort_fees=90_000.0,
        hold_years=hold_years,
    )


def test_every_year_traces_rooms_and_total_revenue() -> None:
    out = RevenueEngine().run(_single_line_input(hold_years=3))
    assert out.provenance, "expected a populated provenance sidecar"
    for i in range(len(out.years)):
        assert f"years[{i}].rooms_revenue" in out.provenance
        assert f"years[{i}].total_revenue" in out.provenance


def test_trace_value_matches_output_value() -> None:
    """The trace's stored value must equal the actual output field."""
    out = RevenueEngine().run(_single_line_input())
    for i, yr in enumerate(out.years):
        assert (
            abs(out.provenance[f"years[{i}].rooms_revenue"].value - yr.rooms_revenue)
            < TOL
        )
        assert (
            abs(out.provenance[f"years[{i}].total_revenue"].value - yr.total_revenue)
            < TOL
        )


def test_single_line_rooms_inputs_reconcile() -> None:
    """occupied_rooms × ADR must reproduce the traced rooms_revenue."""
    out = RevenueEngine().run(_single_line_input())
    for i in range(len(out.years)):
        tr = out.provenance[f"years[{i}].rooms_revenue"]
        by_name = {inp.name: inp.value for inp in tr.inputs}
        assert abs(by_name["occupied_rooms"] * by_name["adr"] - tr.value) < TOL


def test_total_revenue_inputs_sum_to_value() -> None:
    """The four total_revenue components must sum to the traced total."""
    out = RevenueEngine().run(_single_line_input())
    for i in range(len(out.years)):
        tr = out.provenance[f"years[{i}].total_revenue"]
        assert abs(sum(inp.value for inp in tr.inputs) - tr.value) < TOL


def test_no_dangling_traces_to_pointers() -> None:
    """Every ``traces_to`` edge must resolve to a key in the same map."""
    out = RevenueEngine().run(_single_line_input())
    keys = set(out.provenance)
    for trace in out.provenance.values():
        for inp in trace.inputs:
            if inp.traces_to is not None:
                assert inp.traces_to in keys, f"dangling traces_to: {inp.traces_to}"


def test_total_revenue_chains_to_rooms_revenue() -> None:
    """total_revenue's rooms_revenue input points at the rooms_revenue trace."""
    out = RevenueEngine().run(_single_line_input())
    for i in range(len(out.years)):
        tr = out.provenance[f"years[{i}].total_revenue"]
        rooms_inp = next(inp for inp in tr.inputs if inp.name == "rooms_revenue")
        assert rooms_inp.traces_to == f"years[{i}].rooms_revenue"


def test_segmented_path_also_traces() -> None:
    payload = _single_line_input(hold_years=2)
    payload = payload.model_copy(
        update={
            "segments": [
                RevenueSegment(
                    name="transient_bar",
                    mix_pct=0.5,
                    adr=230.0,
                    channel_cost_pct=0.02,
                ),
                RevenueSegment(
                    name="transient_ota",
                    mix_pct=0.5,
                    adr=200.0,
                    channel_cost_pct=0.15,
                ),
            ]
        }
    )
    out = RevenueEngine().run(payload)
    assert "years[0].rooms_revenue" in out.provenance
    seg_trace = out.provenance["years[0].rooms_revenue"]
    assert "segment" in (seg_trace.formula or "").lower()
    # Net = gross − channel cost must reconcile.
    by_name = {inp.name: inp.value for inp in seg_trace.inputs}
    assert (
        abs(
            (by_name["gross_rooms_revenue"] - by_name["channel_cost_total"])
            - seg_trace.value
        )
        < TOL
    )
