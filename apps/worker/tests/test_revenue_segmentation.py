"""Wave 2 P2.1 — Revenue engine segmentation tests.

Pins the institutional 5-segment revenue model contract:

* ``segments == []`` runs the legacy single-line path with byte-identical
  numerical output. Every legacy revenue test passes unchanged.
* A populated ``segments`` list emits per-segment gross / channel-cost /
  net revenue per year, with ``rooms_revenue`` reported NET of channel
  cost and ``gross_rooms_revenue`` carrying the gross.
* Per-segment ``adr_growth`` overrides the engine-level ``adr_growth``.
* Y1 displacement applies inside each segment.
* Pydantic validation rejects mix shares that don't sum to 1.0, unknown
  segment names, and channel-cost percentages outside [0, 0.50].

These are PURE engine tests — no DB, no extraction wiring.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.engines.revenue import RevenueEngine
from fondok_schemas.underwriting import (
    RevenueEngineInput,
    RevenueSegment,
)


def _baseline_input(**overrides) -> RevenueEngineInput:
    """Build a vanilla single-line revenue input the legacy engine
    already knows how to project. Tests override individual fields as
    they need."""
    kwargs = dict(
        deal_id=uuid4(),
        keys=132,
        starting_occupancy=0.762,
        starting_adr=385.0,
        occupancy_growth=0.008,
        adr_growth=0.04,
        fb_revenue_per_occupied_room=88.0,
        other_revenue_pct_of_rooms=0.065,
        hold_years=5,
        y1_occupancy_displacement_pct=0.0,
        y1_adr_displacement_pct=0.0,
    )
    kwargs.update(overrides)
    return RevenueEngineInput(**kwargs)


# ─────────────────────── Legacy single-line regression ────────────────


def test_empty_segments_runs_legacy_single_line_path_unchanged() -> None:
    """The marquee backward-compat guarantee: when no segments are
    supplied, every year's totals must match the legacy formula
    occupied × ADR exactly. Wave 2 must not silently re-route the
    Kimpton demo deal through the segmented path."""
    payload = _baseline_input()
    engine = RevenueEngine()
    out = engine.run(payload)

    # Five projection years, occupancy + ADR ramp Kimpton-style.
    assert len(out.years) == 5
    keys = payload.keys
    baseline_occ = payload.starting_occupancy
    baseline_adr = payload.starting_adr
    for i, yr in enumerate(out.years, start=1):
        expected_occ = (
            baseline_occ
            if i == 1
            else min(0.95, baseline_occ * (1.0 + payload.occupancy_growth) ** (i - 1))
        )
        expected_adr = (
            baseline_adr if i == 1 else baseline_adr * (1.0 + payload.adr_growth) ** (i - 1)
        )
        expected_rooms = keys * 365 * expected_occ * expected_adr
        assert yr.rooms_revenue == pytest.approx(expected_rooms, rel=1e-9)
        # New fields default to gross == net == rooms_revenue, channel
        # cost 0; the UI hides the segmentation sub-section on this.
        assert yr.gross_rooms_revenue == pytest.approx(expected_rooms, rel=1e-9)
        assert yr.channel_cost_total == pytest.approx(0.0, abs=1e-9)
        assert yr.segment_breakdown == []


# ─────────────────────── Five-segment institutional model ─────────────


def test_five_segment_with_ota_at_20_pct_commission_reduces_rooms_revenue() -> None:
    """30% OTA mix at 20% commission must reduce the canonical
    `rooms_revenue` by the exact channel-cost delta vs gross.

    This is the headline IC-credibility test: an analyst points at the
    Kimpton-style $10M baseline and asks "what's the OTA drag?". The
    engine must reproduce gross − channel_cost = net to the dollar."""
    segments = [
        RevenueSegment(name="transient_bar", mix_pct=0.45, adr=312.0, channel_cost_pct=0.02),
        RevenueSegment(name="transient_ota", mix_pct=0.30, adr=312.0, channel_cost_pct=0.20),
        RevenueSegment(name="corporate", mix_pct=0.15, adr=290.0, channel_cost_pct=0.08),
        RevenueSegment(name="group", mix_pct=0.10, adr=260.0, channel_cost_pct=0.05),
        RevenueSegment(name="contract", mix_pct=0.00, adr=200.0, channel_cost_pct=0.02),
    ]
    payload = _baseline_input(
        # Make Y1 deterministic — no growth, no displacement — so the
        # math is easy to verify against the manual model.
        adr_growth=0.0,
        occupancy_growth=0.0,
        y1_adr_displacement_pct=0.0,
        y1_occupancy_displacement_pct=0.0,
        # Strip out F&B / other to isolate rooms revenue arithmetic.
        fb_revenue_per_occupied_room=0.0,
        other_revenue_pct_of_rooms=0.0,
        starting_resort_fees=0.0,
        segments=segments,
    )
    out = RevenueEngine().run(payload)
    y1 = out.years[0]
    occupied = payload.keys * 365 * payload.starting_occupancy

    expected_gross = (
        occupied * 0.45 * 312.0
        + occupied * 0.30 * 312.0
        + occupied * 0.15 * 290.0
        + occupied * 0.10 * 260.0
    )
    expected_channel = (
        occupied * 0.45 * 312.0 * 0.02
        + occupied * 0.30 * 312.0 * 0.20
        + occupied * 0.15 * 290.0 * 0.08
        + occupied * 0.10 * 260.0 * 0.05
    )
    expected_net = expected_gross - expected_channel

    assert y1.gross_rooms_revenue == pytest.approx(expected_gross, rel=1e-9)
    assert y1.channel_cost_total == pytest.approx(expected_channel, rel=1e-9)
    assert y1.rooms_revenue == pytest.approx(expected_net, rel=1e-9)
    # Net < gross — the institutional guarantee.
    assert y1.rooms_revenue < y1.gross_rooms_revenue
    # Per-segment breakdown matches what the IC memo cites.
    by_name = {s.name: s for s in y1.segment_breakdown}
    ota = by_name["transient_ota"]
    assert ota.mix_pct == pytest.approx(0.30)
    assert ota.gross_revenue == pytest.approx(occupied * 0.30 * 312.0, rel=1e-9)
    assert ota.gross_revenue - ota.net_revenue == pytest.approx(
        occupied * 0.30 * 312.0 * 0.20, rel=1e-9
    )
    # Contract row stays at zero — engine must handle 0% mix gracefully.
    contract = by_name["contract"]
    assert contract.gross_revenue == pytest.approx(0.0)
    assert contract.net_revenue == pytest.approx(0.0)


def test_per_segment_adr_growth_beats_engine_level_growth() -> None:
    """OTA pricing typically grows slower than BAR — the analyst can
    pin a per-segment `adr_growth` to model channel discipline. The
    per-segment override must beat the engine-level `adr_growth` for
    that segment only, leaving other segments on the engine default."""
    segments = [
        RevenueSegment(
            name="transient_bar",
            mix_pct=0.70,
            adr=300.0,
            channel_cost_pct=0.02,
            adr_growth=None,  # engine default applies (5%)
        ),
        RevenueSegment(
            name="transient_ota",
            mix_pct=0.30,
            adr=300.0,
            channel_cost_pct=0.20,
            adr_growth=0.01,  # OTA grows at 1%, much slower than 5%
        ),
    ]
    payload = _baseline_input(
        adr_growth=0.05,
        occupancy_growth=0.0,
        y1_adr_displacement_pct=0.0,
        y1_occupancy_displacement_pct=0.0,
        fb_revenue_per_occupied_room=0.0,
        other_revenue_pct_of_rooms=0.0,
        starting_resort_fees=0.0,
        segments=segments,
    )
    out = RevenueEngine().run(payload)
    # Year 3 — n-1 = 2 compounding periods. BAR ADR = 300 × 1.05² = 330.75;
    # OTA ADR = 300 × 1.01² = 306.03.
    y3 = out.years[2]
    by_name = {s.name: s for s in y3.segment_breakdown}
    assert by_name["transient_bar"].adr == pytest.approx(300.0 * (1.05) ** 2, rel=1e-9)
    assert by_name["transient_ota"].adr == pytest.approx(300.0 * (1.01) ** 2, rel=1e-9)


def test_y1_displacement_applies_inside_each_segment() -> None:
    """When the deal carries a heavy PIP, Y1 ADR gets knocked down
    across every segment uniformly. The engine must NOT skip per-segment
    Y1 displacement — that would understate channel cost on the
    displaced year."""
    segments = [
        RevenueSegment(name="transient_bar", mix_pct=0.50, adr=400.0, channel_cost_pct=0.02),
        RevenueSegment(name="transient_ota", mix_pct=0.50, adr=400.0, channel_cost_pct=0.20),
    ]
    payload = _baseline_input(
        adr_growth=0.0,
        occupancy_growth=0.0,
        y1_adr_displacement_pct=0.10,  # 10% Y1 ADR depression
        y1_occupancy_displacement_pct=0.0,
        fb_revenue_per_occupied_room=0.0,
        other_revenue_pct_of_rooms=0.0,
        starting_resort_fees=0.0,
        segments=segments,
    )
    out = RevenueEngine().run(payload)
    y1 = out.years[0]
    by_name = {s.name: s for s in y1.segment_breakdown}
    # ADR in Y1 == 400 * (1 - 0.10) == 360 for both segments.
    assert by_name["transient_bar"].adr == pytest.approx(360.0, rel=1e-9)
    assert by_name["transient_ota"].adr == pytest.approx(360.0, rel=1e-9)
    # Y2 snaps back to the un-displaced baseline (no engine growth here).
    y2 = out.years[1]
    y2_by_name = {s.name: s for s in y2.segment_breakdown}
    assert y2_by_name["transient_bar"].adr == pytest.approx(400.0, rel=1e-9)
    assert y2_by_name["transient_ota"].adr == pytest.approx(400.0, rel=1e-9)


# ─────────────────────── Validation guarantees ────────────────────────


def test_validation_rejects_mix_sum_below_tolerance() -> None:
    """Mix shares summing to 0.95 are outside the ±0.001 tolerance and
    must be rejected at construction time — silently rescaling would
    mask analyst-entry mistakes that materially mis-state revenue."""
    segments = [
        RevenueSegment(name="transient_bar", mix_pct=0.50, adr=300.0),
        RevenueSegment(name="transient_ota", mix_pct=0.45, adr=300.0),
    ]
    with pytest.raises(ValidationError) as exc:
        _baseline_input(segments=segments)
    assert "mix_pct must sum to 1.0" in str(exc.value)


def test_validation_rejects_mix_sum_above_tolerance() -> None:
    """Mix sum 1.005 is also rejected — the validator is symmetric."""
    segments = [
        RevenueSegment(name="transient_bar", mix_pct=0.505, adr=300.0),
        RevenueSegment(name="transient_ota", mix_pct=0.500, adr=300.0),
    ]
    with pytest.raises(ValidationError):
        _baseline_input(segments=segments)


def test_validation_rejects_unknown_segment_name() -> None:
    """Only the five canonical names are allowed. A typo like
    'weddings' or a non-canonical bucket like 'extended_stay' must be
    rejected — the engine's channel-cost defaults table is keyed by
    the canonical name."""
    with pytest.raises(ValidationError) as exc:
        RevenueSegment(name="weddings", mix_pct=1.0, adr=300.0)
    assert "weddings" in str(exc.value)


def test_validation_rejects_channel_cost_above_50_percent() -> None:
    """No legitimate channel costs 60%. Beyond the institutional
    benchmark for OTA opaque (~30%), values above 50% are almost
    always a percent-vs-ratio bug at the data layer."""
    with pytest.raises(ValidationError):
        RevenueSegment(
            name="transient_ota",
            mix_pct=0.30,
            adr=300.0,
            channel_cost_pct=0.60,
        )
