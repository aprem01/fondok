"""Asking vs Bidding price (tester round R-051) — two comparable summaries.

Overview shows one set of summary metrics. R-051 asks for two:

* **Asking** — the deal priced at the seller's asking price (the OM's
  ``asking_price.headline_price_usd`` when extracted; otherwise the price the
  model runs on, labelled with its source by the caller).
* **Bidding** — the deal priced at the purchase price whose levered IRR equals
  the analyst's Target LIRR, solved backward with the EXISTING max-price solver
  (``price_solver.solve_max_price``, IRR constraint only).

Both scenarios hold every other assumption at the base case — exactly the
solver's convention: the loan amount is fixed and equity absorbs the price
difference (``price_solver._flex_price``). Each scenario's levered / unlevered
IRR is a fresh ``ReturnsEngine`` run at that price, so the numbers on screen
are engine outputs, not interpolations.

No target → the bidding scenario is ``status="no_target"`` with every number
``None`` (the UI renders "Set Target LIRR to solve"). A target the solver
cannot bracket (50%-200% of the base price) reports ``unreachable`` /
``above_ceiling`` — never the bracket end dressed up as a price.

Pure: no DB, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .price_solver import _flex_price, solve_max_price
from .returns import ReturnsEngine, ReturnsEngineInputExt

BidStatus = Literal["converged", "no_target", "unreachable", "above_ceiling"]

NO_TARGET_COPY = "Set Target LIRR to solve"


@dataclass
class PriceScenario:
    """One priced summary — the metrics Overview compares side by side."""

    purchase_price: float | None
    price_per_key: float | None
    # Equity + loan at this price (the solver's capitalization identity).
    total_capitalization: float | None
    equity: float | None
    levered_irr: float | None
    unlevered_irr: float | None
    equity_multiple: float | None


@dataclass
class AskingBidResult:
    target_irr: float | None
    base_purchase_price: float
    asking: PriceScenario
    # True when the asking price IS the price the model runs on (to the
    # dollar) — the UI then shows the canonical run's figures.
    asking_is_model_price: bool
    bidding: PriceScenario
    bid_status: BidStatus
    bid_message: str | None
    # Bid - asking (negative = the bid is below the ask). None without a bid.
    bid_vs_asking: float | None


def _scenario_at(
    base: ReturnsEngineInputExt, price: float, rooms: int | None
) -> PriceScenario:
    flexed = base if abs(price - base.assumptions.purchase_price) < 0.5 else _flex_price(base, price)
    out = ReturnsEngine().run(flexed)
    return PriceScenario(
        purchase_price=price,
        price_per_key=(price / rooms) if rooms else None,
        total_capitalization=flexed.equity + flexed.loan_amount,
        equity=flexed.equity,
        levered_irr=out.levered_irr,
        unlevered_irr=out.unlevered_irr,
        equity_multiple=out.equity_multiple,
    )


_EMPTY = PriceScenario(None, None, None, None, None, None, None)


def solve_asking_and_bid(
    base_input: ReturnsEngineInputExt,
    *,
    asking_price: float,
    target_irr: float | None,
    rooms: int | None = None,
) -> AskingBidResult:
    """Price the deal at ``asking_price`` and at the Target-LIRR bid."""
    base_price = base_input.assumptions.purchase_price
    asking = _scenario_at(base_input, asking_price, rooms)

    status: BidStatus
    message: str | None
    bidding = _EMPTY
    if target_irr is None:
        status, message = "no_target", NO_TARGET_COPY
    else:
        res = solve_max_price(
            base_input, target_irr=target_irr, target_em=None, rooms=rooms
        )
        status = res.irr_status  # type: ignore[assignment]
        if res.irr_status == "converged" and res.max_price_for_irr is not None:
            bidding = _scenario_at(base_input, res.max_price_for_irr, rooms)
            message = None
        elif res.irr_status == "unreachable":
            message = (
                "No price at or above 50% of the modeled price reaches the Target LIRR"
            )
        else:
            message = (
                "The Target LIRR clears even at 2x the modeled price — the bid is above the solver range"
            )

    return AskingBidResult(
        target_irr=target_irr,
        base_purchase_price=base_price,
        asking=asking,
        asking_is_model_price=abs(asking_price - base_price) < 1.0,
        bidding=bidding,
        bid_status=status,
        bid_message=message,
        bid_vs_asking=(
            bidding.purchase_price - asking_price
            if bidding.purchase_price is not None
            else None
        ),
    )


__all__ = [
    "NO_TARGET_COPY",
    "AskingBidResult",
    "BidStatus",
    "PriceScenario",
    "solve_asking_and_bid",
]
