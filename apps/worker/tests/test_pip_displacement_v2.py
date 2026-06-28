"""Wave 2 P2.4 — PIP displacement v2 engine tests.

Pins the institutional structured-PIP contract:

* When ``pip_displacement`` is None and both legacy flat-pct fields are
  zero, the engine runs the legacy single-line path with byte-identical
  numerical output. Every legacy revenue test still passes.
* When ``pip_displacement`` is None and a legacy flat-pct displacement
  is supplied, the engine applies it exactly as before.
* Closure strategies — rolling / full_closure / wing_by_wing — each
  scale Y1 rooms revenue per the spec'd formula.
* Brand multipliers shift ``occupancy_recovery_months`` and
  ``revpar_index_post_reno`` by the documented amounts.
* Y2 occupancy ramps linearly back to baseline over the brand-adjusted
  recovery window; Y2 ADR is uplifted by the brand-adjusted
  ``revpar_index_post_reno``.
* Pydantic validation rejects malformed ``pct_rooms_offline_by_month``
  schedules and inconsistent strategy/schedule combinations.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.engines.revenue import (
    RevenueEngine,
    _BRAND_DISPLACEMENT_MULTIPLIERS,
    _effective_recovery_months,
)
from fondok_schemas.underwriting import (
    PIPDisplacement,
    RevenueEngineInput,
)


KEYS = 200
ROOMS_AVAILABLE_PER_YEAR = KEYS * 365


def _baseline_input(**overrides) -> RevenueEngineInput:
    """Vanilla single-line revenue input. Tests override per case.

    No segments, no PIP, no flat-pct displacement — Y1 = baseline_occ ×
    baseline_adr exactly. Constants chosen for round-number arithmetic.
    """
    kwargs = dict(
        deal_id=uuid4(),
        keys=KEYS,
        starting_occupancy=0.80,
        starting_adr=250.0,
        occupancy_growth=0.0,
        adr_growth=0.0,
        fb_revenue_per_occupied_room=0.0,
        other_revenue_pct_of_rooms=0.0,
        hold_years=5,
        y1_occupancy_displacement_pct=0.0,
        y1_adr_displacement_pct=0.0,
    )
    kwargs.update(overrides)
    return RevenueEngineInput(**kwargs)


def _full_year_rooms_revenue(occ: float, adr: float, keys: int = KEYS) -> float:
    return keys * 365 * occ * adr


# ───────────────────── 1. Legacy path unchanged ─────────────────────


def test_no_pip_runs_legacy_path_unchanged() -> None:
    """Sanity: pip_displacement=None, y1_*_disp=0.0 → byte-identical to
    pre-P2.4 behavior. Y1 rooms revenue = occupied × ADR exactly."""
    payload = _baseline_input()
    out = RevenueEngine().run(payload)
    y1 = out.years[0]
    expected = _full_year_rooms_revenue(0.80, 250.0)
    assert y1.rooms_revenue == pytest.approx(expected, rel=1e-9)
    # No PIP → Y1 occ and ADR equal baseline.
    assert y1.occupancy == pytest.approx(0.80, rel=1e-9)
    assert y1.adr == pytest.approx(250.0, rel=1e-9)


def test_legacy_flat_pct_still_works() -> None:
    """The pre-P2.4 flat-pct path must still work for deals that don't
    yet have a structured PIPDisplacement on file. 10% occ haircut →
    Y1 rooms revenue = occupied × 0.9 × ADR."""
    payload = _baseline_input(
        y1_occupancy_displacement_pct=0.10,
        y1_adr_displacement_pct=0.0,
    )
    out = RevenueEngine().run(payload)
    y1 = out.years[0]
    expected = _full_year_rooms_revenue(0.80 * 0.90, 250.0)
    assert y1.rooms_revenue == pytest.approx(expected, rel=1e-9)
    assert y1.occupancy == pytest.approx(0.80 * 0.90, rel=1e-9)


# ───────────────────── 2. Rolling renovation ─────────────────────


def test_rolling_renovation_25pct_offline_6_months() -> None:
    """Spec example: 6 months at 25% offline, 6 months at 0%. Y1 rooms
    revenue = full × ((6 × 1.0 + 6 × 0.75) / 12) = full × 0.875."""
    schedule = [0.25] * 6 + [0.0] * 6
    pip = PIPDisplacement(
        closure_strategy="rolling",
        pct_rooms_offline_by_month=schedule,
        brand="Marriott",
    )
    payload = _baseline_input(pip_displacement=pip)
    out = RevenueEngine().run(payload)
    y1 = out.years[0]
    full = _full_year_rooms_revenue(0.80, 250.0)
    expected = full * 0.875
    assert y1.rooms_revenue == pytest.approx(expected, rel=1e-9)
    # Reported Y1 occ = baseline × 0.875, ADR unchanged under rolling.
    assert y1.occupancy == pytest.approx(0.80 * 0.875, rel=1e-9)
    assert y1.adr == pytest.approx(250.0, rel=1e-9)


# ───────────────────── 3. Full closure ─────────────────────


def test_full_closure_3_months() -> None:
    """3 months at 100% offline, 9 operating. Y1 rooms revenue = full × 9/12."""
    schedule = [1.0] * 3 + [0.0] * 9
    pip = PIPDisplacement(
        closure_strategy="full_closure",
        pct_rooms_offline_by_month=schedule,
        brand="Marriott",
    )
    payload = _baseline_input(pip_displacement=pip)
    out = RevenueEngine().run(payload)
    y1 = out.years[0]
    full = _full_year_rooms_revenue(0.80, 250.0)
    expected = full * (9.0 / 12.0)
    assert y1.rooms_revenue == pytest.approx(expected, rel=1e-9)
    assert y1.occupancy == pytest.approx(0.80 * 9.0 / 12.0, rel=1e-9)


# ───────────────────── 4. Wing-by-wing cap ─────────────────────


def test_wing_by_wing_caps_at_50pct() -> None:
    """A 0.70 pct_offline entry under wing_by_wing gets capped at 0.50,
    and Y1 ADR gets the 5% construction-nuisance drag.

    With 6 months at 0.7 (capped to 0.5) and 6 months at 0.0:
      factor = (6×1.0 + 6×0.5) / 12 = 0.75
      Y1 rooms revenue = full × 0.75 × 0.95
    """
    schedule = [0.70] * 6 + [0.0] * 6
    pip = PIPDisplacement(
        closure_strategy="wing_by_wing",
        pct_rooms_offline_by_month=schedule,
        brand="Marriott",
    )
    payload = _baseline_input(pip_displacement=pip)
    out = RevenueEngine().run(payload)
    y1 = out.years[0]
    full = _full_year_rooms_revenue(0.80, 250.0)
    expected = full * 0.75 * 0.95
    assert y1.rooms_revenue == pytest.approx(expected, rel=1e-9)
    # Reported Y1 occ reflects only the capacity factor; the ADR drag
    # is on the ADR line.
    assert y1.occupancy == pytest.approx(0.80 * 0.75, rel=1e-9)
    assert y1.adr == pytest.approx(250.0 * 0.95, rel=1e-9)


# ─────────────── 5. Brand multipliers — Marriott baseline ───────────────


def test_marriott_brand_uses_baseline_multipliers() -> None:
    """Marriott sits at (1.0, 1.0) — recovery months unchanged."""
    pip = PIPDisplacement(
        closure_strategy="rolling",
        pct_rooms_offline_by_month=[0.10] * 6 + [0.0] * 6,
        brand="Marriott",
        occupancy_recovery_months=12,
    )
    assert _BRAND_DISPLACEMENT_MULTIPLIERS["Marriott"] == (1.0, 1.0)
    assert _effective_recovery_months(pip, 1.0) == 12


# ─────────────── 6. Brand multipliers — Independent faster ───────────────


def test_independent_brand_recovers_faster() -> None:
    """Independent / soft brand = ×0.85 recovery → 12 × 0.85 = 10.2 → 10."""
    pip = PIPDisplacement(
        closure_strategy="rolling",
        pct_rooms_offline_by_month=[0.10] * 6 + [0.0] * 6,
        brand="Independent",
        occupancy_recovery_months=12,
    )
    brand_recovery_mult, _ = _BRAND_DISPLACEMENT_MULTIPLIERS["Independent"]
    assert brand_recovery_mult == 0.85
    assert _effective_recovery_months(pip, brand_recovery_mult) == 10


# ─────────────── 7. Y2 occupancy ramp ───────────────


def test_y2_occupancy_ramps_linearly() -> None:
    """With a 6-month recovery window and y1_factor=0.875 (rolling, 6 mo
    at 25% offline), Y2 should ramp linearly back to baseline.

    Months 0..5: 0.875 + (1.0 - 0.875) × m/6  → mean over months 0..5 =
        0.875 + 0.125 × (0+1+2+3+4+5)/6/6 = 0.875 + 0.125 × 15/36 = 0.927083
    Months 6..11: 1.0 (recovered)
    Y2 mean factor = (6 × 0.927083 + 6 × 1.0) / 12 = 0.96354166...

    With baseline_occ = 0.80, growth = 0%, Marriott (no brand multiplier):
        base_occ_y2 = 0.80
        reported Y2 occ = 0.80 × 0.96354166 ≈ 0.770833
    """
    schedule = [0.25] * 6 + [0.0] * 6
    pip = PIPDisplacement(
        closure_strategy="rolling",
        pct_rooms_offline_by_month=schedule,
        brand="Marriott",
        occupancy_recovery_months=6,
    )
    payload = _baseline_input(
        pip_displacement=pip,
        # Force constant baseline so the ramp math is easy to verify.
        occupancy_growth=0.0,
        adr_growth=0.0,
    )
    out = RevenueEngine().run(payload)
    y2 = out.years[1]
    y3 = out.years[2]
    # Hand-computed expected ramp factor.
    months = 6
    y1_factor = 0.875
    ramp_total = 0.0
    for m in range(12):
        if m < months:
            ramp_total += y1_factor + (1.0 - y1_factor) * (m / months)
        else:
            ramp_total += 1.0
    y2_factor = ramp_total / 12.0
    expected_y2_occ = 0.80 * y2_factor
    assert y2.occupancy == pytest.approx(expected_y2_occ, rel=1e-9)
    # Y3 is fully recovered.
    assert y3.occupancy == pytest.approx(0.80, rel=1e-9)


# ─────────────── 8. Y2 RevPAR uplift ───────────────


def test_revpar_index_post_reno_applied_in_y2() -> None:
    """Y2 ADR should be uplifted by ``revpar_index_post_reno`` × brand
    revpar multiplier. With revpar_index = 1.08, growth = 0, Marriott
    (mult = 1.0): Y2 ADR = 250 × 1.08 = 270."""
    pip = PIPDisplacement(
        closure_strategy="rolling",
        pct_rooms_offline_by_month=[0.10] * 6 + [0.0] * 6,
        brand="Marriott",
        revpar_index_post_reno=1.08,
        occupancy_recovery_months=12,
    )
    payload = _baseline_input(
        pip_displacement=pip,
        occupancy_growth=0.0,
        adr_growth=0.0,
    )
    out = RevenueEngine().run(payload)
    y2 = out.years[1]
    assert y2.adr == pytest.approx(250.0 * 1.08, rel=1e-9)


# ─────────────── 9. Validation — pct out of range ───────────────


def test_pct_offline_validation_rejects_above_one() -> None:
    """Each entry must lie in [0.0, 1.0]; 1.2 is rejected."""
    with pytest.raises(ValidationError):
        PIPDisplacement(
            closure_strategy="rolling",
            pct_rooms_offline_by_month=[0.10, 1.2, 0.0],
        )


def test_pct_offline_validation_rejects_negative() -> None:
    """Negative offline pct is nonsense — rejected."""
    with pytest.raises(ValidationError):
        PIPDisplacement(
            closure_strategy="rolling",
            pct_rooms_offline_by_month=[-0.1, 0.10, 0.0],
        )


# ─────────────── 10. Validation — full_closure consistency ───────────────


def test_full_closure_strategy_requires_all_1s_or_0s() -> None:
    """A 0.5 month under the full_closure label is an analyst error."""
    with pytest.raises(ValidationError):
        PIPDisplacement(
            closure_strategy="full_closure",
            pct_rooms_offline_by_month=[1.0, 0.5, 0.0],
        )


# ─────────────── 11. Validation — none strategy consistency ───────────────


def test_none_strategy_requires_zero_schedule() -> None:
    """If the analyst sets strategy='none', the schedule must be empty
    or all zeros — otherwise the engine would silently ignore real
    inputs."""
    with pytest.raises(ValidationError):
        PIPDisplacement(
            closure_strategy="none",
            pct_rooms_offline_by_month=[0.0, 0.25, 0.0],
        )
    # Empty schedule is fine under 'none'.
    PIPDisplacement(closure_strategy="none", pct_rooms_offline_by_month=[])
    # All-zero schedule is fine too.
    PIPDisplacement(
        closure_strategy="none", pct_rooms_offline_by_month=[0.0, 0.0, 0.0]
    )


# ─────────────── 12. None strategy is a no-op (backward compat) ───────────────


def test_none_strategy_is_a_noop() -> None:
    """A PIPDisplacement with strategy='none' should produce IDENTICAL
    output to ``pip_displacement=None``."""
    pip_none = PIPDisplacement(closure_strategy="none")
    payload_with_none_pip = _baseline_input(pip_displacement=pip_none)
    payload_without_pip = _baseline_input(pip_displacement=None)
    out_none = RevenueEngine().run(payload_with_none_pip)
    out_baseline = RevenueEngine().run(payload_without_pip)
    for a, b in zip(out_none.years, out_baseline.years):
        assert a.rooms_revenue == pytest.approx(b.rooms_revenue, rel=1e-12)
        assert a.occupancy == pytest.approx(b.occupancy, rel=1e-12)
        assert a.adr == pytest.approx(b.adr, rel=1e-12)
