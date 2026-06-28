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

from app.engines.loi_generator import (  # noqa: E402
    DEFAULT_VALID_UNTIL,
    LOIDraft,
    draft_loi,
)
from app.engines.price_solver import MaxPriceResult  # noqa: E402


def _sample_max_price() -> MaxPriceResult:
    return MaxPriceResult(
        target_irr=0.15,
        target_em=1.8,
        max_price_for_irr=42_800_000.0,
        max_price_for_em=40_100_000.0,
        binding_constraint="em",
        final_price_per_key=200_500.0,
        iters=18,
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


def test_proposed_price_defaults_to_max_price_for_irr() -> None:
    """When no override, draft.proposed_price == max_price_result.max_price_for_irr."""
    mpr = _sample_max_price()
    draft = draft_loi(
        asset_name="Test Hotel",
        asset_address="X",
        rooms=100,
        max_price_result=mpr,
    )
    assert draft.proposed_price == mpr.max_price_for_irr
    # And the per-key derives correctly.
    assert abs(draft.proposed_price_per_key - mpr.max_price_for_irr / 100) < 0.01


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
