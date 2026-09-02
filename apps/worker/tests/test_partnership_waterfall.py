"""FON-66 — partnership waterfall seed + per-tier override wiring.

Slice A makes the promote waterfall editable from the Partnership tab via
indexed scalar overrides (``partnership.waterfall.<idx>.<field>``). These
tests cover the deal-agnostic Kimpton reference seed, the split-derivation
that keeps a single-field edit valid, the path parser, and the override
key family.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.partnership import PartnershipEngine, PartnershipInputExt
from app.services.engine_runner import (
    _KIMPTON_WATERFALL_REFERENCE,
    _OVERRIDE_PARTNERSHIP_KEYS,
    _build_partnership_waterfall,
    _parse_partnership_override_path,
)


def test_default_stack_is_kimpton_benchmark() -> None:
    wf = _build_partnership_waterfall(None)
    assert len(wf) == len(_KIMPTON_WATERFALL_REFERENCE) == 6
    # Preferred tier: 0% GP / 100% LP at the 10% hurdle.
    assert (wf[0].hurdle_rate, wf[0].gp_split, wf[0].lp_split) == (0.10, 0.0, 1.0)
    # Top promote band: 50/50 above 30%.
    assert (wf[5].gp_split, wf[5].lp_split) == (0.50, 0.50)
    # Every tier's splits sum to 1.0 (WaterfallTier validator invariant).
    for t in wf:
        assert round(t.gp_split + t.lp_split, 6) == 1.0


def test_gp_split_override_derives_lp_split() -> None:
    wf = _build_partnership_waterfall({1: {"gp_split": 0.30}})
    assert wf[1].gp_split == 0.30
    assert wf[1].lp_split == 0.70  # derived, keeps the sum at 1.0


def test_lp_split_override_derives_gp_split() -> None:
    wf = _build_partnership_waterfall({2: {"lp_split": 0.60}})
    assert wf[2].lp_split == 0.60
    assert wf[2].gp_split == 0.40


def test_hurdle_override_leaves_splits_at_seed() -> None:
    wf = _build_partnership_waterfall({3: {"hurdle_rate": 0.22}})
    assert wf[3].hurdle_rate == 0.22
    assert (wf[3].gp_split, wf[3].lp_split) == (0.25, 0.75)


def test_both_splits_override_uses_analyst_values() -> None:
    wf = _build_partnership_waterfall({4: {"gp_split": 0.35, "lp_split": 0.65}})
    assert (wf[4].gp_split, wf[4].lp_split) == (0.35, 0.65)


def test_parse_override_path() -> None:
    assert _parse_partnership_override_path(
        "partnership.waterfall.3.gp_split"
    ) == (3, "gp_split")
    assert _parse_partnership_override_path("partnership.waterfall.3.bogus") is None
    assert _parse_partnership_override_path("debt_stack.tranches.0.rate_pct") is None
    assert _parse_partnership_override_path("partnership.waterfall.x.gp_split") is None


def test_override_key_family_shape() -> None:
    # 6 tiers x 3 fields = 18 keys.
    assert len(_OVERRIDE_PARTNERSHIP_KEYS) == 18
    assert "partnership.waterfall.0.hurdle_rate" in _OVERRIDE_PARTNERSHIP_KEYS
    assert "partnership.waterfall.5.lp_split" in _OVERRIDE_PARTNERSHIP_KEYS


def test_engine_runs_on_default_stack() -> None:
    eng = PartnershipEngine()
    out = eng.run(
        PartnershipInputExt(
            deal_id=uuid4(),
            total_equity=10_000_000,
            gp_equity_pct=0.10,
            lp_equity_pct=0.90,
            pref_rate=0.10,
            waterfall=_build_partnership_waterfall(None),
            cash_flows=[1_000_000, 1_500_000, 2_000_000, 2_500_000, 15_000_000],
        )
    )
    # GP earns a promote above its pro-rata; LP clears its preferred.
    assert out.gp.irr > out.lp.irr
    assert out.promote_amount > 0
    assert out.gp.contributed_equity == pytest.approx(1_000_000)
    assert out.lp.contributed_equity == pytest.approx(9_000_000)


# ─────────────── FON-72 dollar waterfall + GP catch-up ───────────────


def _annual_input(catch_up: bool = False) -> PartnershipInputExt:
    return PartnershipInputExt(
        deal_id=uuid4(),
        total_equity=10_000_000,
        gp_equity_pct=0.10,
        lp_equity_pct=0.90,
        pref_rate=0.08,
        waterfall=_build_partnership_waterfall(None),
        cash_flows=[1_000_000, 1_500_000, 2_000_000, 2_500_000, 15_000_000],
        catch_up=catch_up,
    )


def test_dollar_tiers_reconcile_to_total() -> None:
    """The 'Allocation of Projected Proceeds' rows must sum to every dollar
    distributed — the tab's 'Reconciles' badge reads this."""
    out = PartnershipEngine().run(_annual_input())
    assert out.tier_allocations, "no dollar tiers emitted"
    tier_sum = sum(t.total_amount for t in out.tier_allocations)
    distributed = out.gp.distributions + out.lp.distributions
    assert tier_sum == pytest.approx(distributed, rel=1e-9, abs=1.0)
    assert out.total_distributable == pytest.approx(distributed)
    assert out.reconciles is True
    # Each row's total equals gp + lp.
    for t in out.tier_allocations:
        assert t.total_amount == pytest.approx(t.gp_amount + t.lp_amount)
    # Return of Capital + Preferred rows are present (annual path decomposes them).
    kinds = {t.kind for t in out.tier_allocations}
    assert "return_of_capital" in kinds
    assert "preferred" in kinds


def test_catch_up_tier_present_when_set() -> None:
    """catch_up=True adds a GP Catch-Up tier row (100% to GP) and still
    reconciles to the total distributed."""
    out = PartnershipEngine().run(_annual_input(catch_up=True))
    catchup_rows = [t for t in out.tier_allocations if t.kind == "catch_up"]
    assert len(catchup_rows) == 1, "catch-up tier row missing when catch_up set"
    cu = catchup_rows[0]
    assert cu.label == "GP Catch-Up"
    assert cu.lp_amount == pytest.approx(0.0)  # catch-up is GP-only
    assert cu.gp_amount > 0
    assert out.catch_up_amount == pytest.approx(cu.gp_amount)
    # Still fully reconciled.
    assert out.reconciles is True


def test_catch_up_absent_by_default() -> None:
    """No catch-up row when catch_up is unset — default deals are unchanged."""
    out = PartnershipEngine().run(_annual_input(catch_up=False))
    assert not any(t.kind == "catch_up" for t in out.tier_allocations)
    assert out.catch_up_amount == pytest.approx(0.0)
