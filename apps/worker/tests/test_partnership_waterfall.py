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
    _MAX_PARTNERSHIP_TIERS,
    _OVERRIDE_PARTNERSHIP_KEYS,
    _build_partnership_waterfall,
    _parse_partnership_override_path,
    _resolve_partnership_tier_count,
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


# ─────────────── FON-66 Part A — variable promote-tier count ───────────────

_CASH_FLOWS = [1_000_000, 1_500_000, 2_000_000, 2_500_000, 15_000_000]


def _run_with(waterfall: list) -> "object":
    return PartnershipEngine().run(
        PartnershipInputExt(
            deal_id=uuid4(),
            total_equity=10_000_000,
            gp_equity_pct=0.10,
            lp_equity_pct=0.90,
            pref_rate=0.08,
            waterfall=waterfall,
            cash_flows=list(_CASH_FLOWS),
        )
    )


def _tier_tuples(wf: list) -> list[tuple]:
    return [(t.label, t.hurdle_rate, t.gp_split, t.lp_split) for t in wf]


def test_resolve_tier_count_clamps() -> None:
    seed_len = len(_KIMPTON_WATERFALL_REFERENCE)
    assert _resolve_partnership_tier_count(None) == seed_len  # default
    assert _resolve_partnership_tier_count(seed_len) == seed_len
    assert _resolve_partnership_tier_count(0) == 1  # floor
    assert _resolve_partnership_tier_count(-3) == 1
    assert _resolve_partnership_tier_count(999) == _MAX_PARTNERSHIP_TIERS  # ceil
    assert _resolve_partnership_tier_count("not-a-number") == seed_len


def test_no_tier_count_is_byte_identical_default() -> None:
    """(a) No tier_count override → the seed-length default, unchanged.

    Passing ``None``, omitting the arg, or passing exactly the seed length all
    build the identical tier list AND the identical GP/LP dollar allocation.
    """
    seed_len = len(_KIMPTON_WATERFALL_REFERENCE)
    base = _build_partnership_waterfall(None)  # legacy single-arg call
    assert _tier_tuples(_build_partnership_waterfall(None, None)) == _tier_tuples(base)
    assert _tier_tuples(_build_partnership_waterfall({}, seed_len)) == _tier_tuples(base)
    assert len(base) == seed_len

    # And the engine output is identical (same GP/LP dollars, same reconcile).
    out_default = _run_with(_build_partnership_waterfall(None))
    out_count = _run_with(_build_partnership_waterfall({}, seed_len))
    assert out_count.gp.distributions == pytest.approx(out_default.gp.distributions)
    assert out_count.lp.distributions == pytest.approx(out_default.lp.distributions)
    assert out_count.promote_amount == pytest.approx(out_default.promote_amount)
    assert out_count.reconciles is True and out_default.reconciles is True


def test_add_tier_builds_n_plus_one_and_reconciles() -> None:
    """(b) tier_count=N+1 with a complete new tier → N+1 tiers that reconcile."""
    seed_len = len(_KIMPTON_WATERFALL_REFERENCE)
    new_idx = seed_len  # first index beyond the seed
    overrides = {
        new_idx: {"hurdle_rate": 0.55, "gp_split": 0.60, "lp_split": 0.40},
    }
    wf = _build_partnership_waterfall(overrides, seed_len + 1)
    assert len(wf) == seed_len + 1
    added = wf[-1]
    assert (added.hurdle_rate, added.gp_split, added.lp_split) == (0.55, 0.60, 0.40)
    assert round(added.gp_split + added.lp_split, 6) == 1.0

    out = _run_with(wf)
    distributed = out.gp.distributions + out.lp.distributions
    assert sum(t.total_amount for t in out.tier_allocations) == pytest.approx(
        distributed, rel=1e-9, abs=1.0
    )
    assert out.reconciles is True


def test_add_tier_derives_complement_from_single_split() -> None:
    """A beyond-seed tier with only a GP split derives its LP split (no fabrication
    of the whole tier — the analyst supplied hurdle + one split)."""
    seed_len = len(_KIMPTON_WATERFALL_REFERENCE)
    wf = _build_partnership_waterfall(
        {seed_len: {"hurdle_rate": 0.60, "gp_split": 0.30}}, seed_len + 1
    )
    assert len(wf) == seed_len + 1
    assert (wf[-1].gp_split, wf[-1].lp_split) == (0.30, 0.70)


def test_remove_tier_drops_top_and_reconciles() -> None:
    """(c) tier_count=N-1 → the highest seed tier is dropped; still reconciles."""
    seed_len = len(_KIMPTON_WATERFALL_REFERENCE)
    full = _build_partnership_waterfall(None)
    wf = _build_partnership_waterfall(None, seed_len - 1)
    assert len(wf) == seed_len - 1
    # It is exactly the lowest N-1 seed tiers — the top tier is gone.
    assert _tier_tuples(wf) == _tier_tuples(full)[: seed_len - 1]

    out = _run_with(wf)
    distributed = out.gp.distributions + out.lp.distributions
    assert sum(t.total_amount for t in out.tier_allocations) == pytest.approx(
        distributed, rel=1e-9, abs=1.0
    )
    assert out.reconciles is True


def test_removal_ignores_orphaned_overrides() -> None:
    """Dropping the top tier ignores any override still sitting on that index —
    a lower count never revives it."""
    seed_len = len(_KIMPTON_WATERFALL_REFERENCE)
    top = seed_len - 1
    wf = _build_partnership_waterfall({top: {"gp_split": 0.90}}, seed_len - 1)
    assert len(wf) == seed_len - 1
    # The 0.90-GP override lived on the now-dropped index; nothing carries it.
    assert all(t.gp_split != 0.90 for t in wf)


def test_incomplete_added_tier_is_omitted_not_fabricated() -> None:
    """A tier_count bump with an incomplete new tier does NOT invent values —
    the tier is omitted, so the built stack falls back to the completed tiers.

    Missing hurdle, missing both splits, or no overrides at all for the new
    index each yield a stack of exactly the seed length (the added index is a
    no-op until the analyst completes it)."""
    seed_len = len(_KIMPTON_WATERFALL_REFERENCE)
    new_idx = seed_len
    # No overrides at all for the new index.
    assert len(_build_partnership_waterfall({}, seed_len + 1)) == seed_len
    # Hurdle present but no split → incomplete → omitted.
    assert (
        len(_build_partnership_waterfall({new_idx: {"hurdle_rate": 0.55}}, seed_len + 1))
        == seed_len
    )
    # Split present but no hurdle → incomplete → omitted.
    assert (
        len(_build_partnership_waterfall({new_idx: {"gp_split": 0.5}}, seed_len + 1))
        == seed_len
    )


def test_partial_incomplete_among_added_tiers() -> None:
    """Two added tiers where only the first is complete → seed_len + 1 tiers
    (the incomplete second index is omitted; no gap is fabricated)."""
    seed_len = len(_KIMPTON_WATERFALL_REFERENCE)
    overrides = {
        seed_len: {"hurdle_rate": 0.55, "gp_split": 0.60, "lp_split": 0.40},
        seed_len + 1: {"hurdle_rate": 0.60},  # incomplete — no split
    }
    wf = _build_partnership_waterfall(overrides, seed_len + 2)
    assert len(wf) == seed_len + 1  # only the complete added tier is emitted


def test_invalid_added_tier_splits_omitted_not_crashed() -> None:
    """A beyond-seed tier whose explicit splits don't sum to 1.0 is omitted
    (WaterfallTier would reject it) rather than crashing the whole build."""
    seed_len = len(_KIMPTON_WATERFALL_REFERENCE)
    wf = _build_partnership_waterfall(
        {seed_len: {"hurdle_rate": 0.55, "gp_split": 0.6, "lp_split": 0.6}},
        seed_len + 1,
    )
    assert len(wf) == seed_len  # bad tier dropped, seed intact


def test_tier_count_path_not_parsed_as_field() -> None:
    """The count key must not be mistaken for a per-tier field path, and any
    tier index (including beyond the seed) must parse as a field path."""
    assert _parse_partnership_override_path("partnership.waterfall.tier_count") is None
    assert _parse_partnership_override_path("partnership.waterfall.6.gp_split") == (
        6,
        "gp_split",
    )
