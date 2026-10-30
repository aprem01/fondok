"""Tests for the LOI draft generator (Wave 2 P2.8)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Per-test SQLite database BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-loi.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

import pytest  # noqa: E402

from app.engines.loi_generator import (  # noqa: E402
    DEFAULT_VALID_UNTIL,
    LOI_NO_PRICE_MESSAGE,
    LOI_NO_ROOMS_MESSAGE,
    LOIDraft,
    LOIInputMissing,
    draft_loi,
)
from app.engines.price_solver import MaxPriceResult  # noqa: E402


def _sample_max_price() -> MaxPriceResult:
    """A converged two-hurdle solve (the deal's own targets, arbitrary here);
    EM binds, so the offerable headline is the EM price."""
    return MaxPriceResult(
        target_irr=0.14,
        target_em=1.9,
        max_price_for_irr=42_800_000.0,
        max_price_for_em=40_100_000.0,
        binding_constraint="em",
        final_price_per_key=200_500.0,
        iters=18,
        max_price=40_100_000.0,
        irr_status="converged",
        em_status="converged",
    )


def test_renders_required_fields_present() -> None:
    """Markdown contains buyer, seller, asset, price, rooms, address."""
    draft = draft_loi(
        asset_name="Hyatt Centric Houston",
        asset_address="1234 Main St, Houston, TX",
        rooms=200,
        max_price_result=_sample_max_price(),
        buyer="Fondok Capital LP",
        seller="ABC Hospitality Holdings",
    )
    md = draft.rendered_markdown
    assert "Fondok Capital LP" in md
    assert "ABC Hospitality Holdings" in md
    assert "Hyatt Centric Houston" in md
    assert "1234 Main St, Houston, TX" in md
    assert "200" in md  # rooms appears in the body
    assert "Earnest Money" in md
    assert "Due Diligence" in md
    assert "Closing" in md
    assert "Exclusivity" in md


def test_proposed_price_defaults_to_the_offerable_headline() -> None:
    """With no override the draft prices at ``max_price`` — the lower of the
    converged prices for the hurdles the deal set (EM here) — never at a
    price that would miss one of the deal's own hurdles."""
    mpr = _sample_max_price()
    draft = draft_loi(
        asset_name="Test Hotel",
        asset_address="Somewhere",
        rooms=200,
        max_price_result=mpr,
    )
    assert draft.proposed_price == mpr.max_price == 40_100_000.0
    assert draft.proposed_price_per_key == 40_100_000.0 / 200
    assert "$40,100,000" in draft.rendered_markdown


def test_override_price_wins_over_the_solve() -> None:
    draft = draft_loi(
        asset_name="Test Hotel",
        asset_address="Somewhere",
        rooms=200,
        max_price_result=_sample_max_price(),
        proposed_price_override=38_000_000.0,
    )
    assert draft.proposed_price == 38_000_000.0
    assert draft.proposed_price_per_key == 190_000.0


# ── Go-live Wave 0 (A8): the LOI refuses instead of rendering a zero ──────


def test_loi_refuses_without_a_key_count() -> None:
    """"comprising 0 guest rooms" is never produced — missing keys refuse."""
    for rooms in (0, -3):
        with pytest.raises(LOIInputMissing) as exc:
            draft_loi(
                asset_name="Test Hotel",
                asset_address="Somewhere",
                rooms=rooms,
                max_price_result=_sample_max_price(),
            )
        assert str(exc.value) == LOI_NO_ROOMS_MESSAGE
        assert "0 guest rooms" not in str(exc.value)


def test_loi_refuses_when_the_solve_did_not_converge_and_no_override() -> None:
    """An unreachable hurdle has no offerable price; the refusal names the
    hurdle and the solver status rather than printing the bracket end."""
    unsolved = MaxPriceResult(
        target_irr=0.14,
        target_em=None,
        max_price_for_irr=None,
        max_price_for_em=None,
        binding_constraint="irr",
        final_price_per_key=0.0,
        iters=40,
        max_price=None,
        irr_status="unreachable",
        em_status="not_requested",
    )
    with pytest.raises(LOIInputMissing) as exc:
        draft_loi(
            asset_name="Test Hotel",
            asset_address="Somewhere",
            rooms=200,
            max_price_result=unsolved,
        )
    assert str(exc.value) == LOI_NO_PRICE_MESSAGE.format(hurdle="IRR", status="unreachable")

    # ...but an explicit override is a real price and drafts fine.
    draft = draft_loi(
        asset_name="Test Hotel",
        asset_address="Somewhere",
        rooms=200,
        max_price_result=unsolved,
        proposed_price_override=35_000_000.0,
    )
    assert draft.proposed_price == 35_000_000.0
    assert "$0" not in draft.rendered_markdown.replace("$0,", "x")  # no $0 figure


def test_buyer_seller_overrides_propagate_to_markdown() -> None:
    """Buyer + seller fields render verbatim into the markdown body."""
    draft = draft_loi(
        asset_name="X Hotel",
        asset_address="Y",
        rooms=100,
        max_price_result=_sample_max_price(),
        buyer="Brookfield Asset Mgmt",
        seller="Marriott Hawaii LLC",
    )
    assert "Brookfield Asset Mgmt" in draft.rendered_markdown
    assert "Marriott Hawaii LLC" in draft.rendered_markdown
    assert draft.buyer == "Brookfield Asset Mgmt"
    assert draft.seller == "Marriott Hawaii LLC"


def test_due_diligence_days_appears_in_markdown() -> None:
    """Custom DD-days value lands in the markdown verbatim."""
    draft = draft_loi(
        asset_name="X",
        asset_address="Y",
        rooms=100,
        max_price_result=_sample_max_price(),
        due_diligence_days=45,
    )
    assert "45 days" in draft.rendered_markdown


def test_contingencies_list_renders_as_bulleted_block() -> None:
    """Each contingency renders as a separate ``-`` bullet in section 9."""
    contingencies = [
        "Brand approval (Marriott)",
        "Liquor license transfer",
        "Customary title and survey review",
    ]
    draft = draft_loi(
        asset_name="X",
        asset_address="Y",
        rooms=100,
        max_price_result=_sample_max_price(),
        contingencies=contingencies,
    )
    for c in contingencies:
        # Each contingency renders prefixed by a ``-`` bullet inside
        # section 9.
        assert f"- {c}" in draft.rendered_markdown


def test_valid_until_includes_default_phrasing() -> None:
    """Default valid_until string is rendered verbatim into the body."""
    draft = draft_loi(
        asset_name="X",
        asset_address="Y",
        rooms=100,
        max_price_result=_sample_max_price(),
    )
    assert DEFAULT_VALID_UNTIL in draft.rendered_markdown
    assert isinstance(draft, LOIDraft)
