"""Max-price solver — bisect the purchase price that hits a target return.

Question every institutional buyer answers at IC: "What's the maximum
I'd pay for this deal to still hit my fund's 15% IRR (or 1.8x EM)?".
This module bisects on purchase price, re-running the deterministic
``ReturnsEngine`` at each step. The output is two scalars:

    max_price_for_irr — purchase price s.t. levered_irr == target_irr
    max_price_for_em  — purchase price s.t. equity_multiple == target_em

plus a ``binding_constraint`` chip ("irr" / "em" / "both") indicating
which target is tighter — the actual max price the fund could pay is
``min(max_price_for_irr, max_price_for_em)``.

Bisection bounds: 50% to 200% of the base price. Tolerance: $50K on
price or 5 bp on IRR. Hard cap at 40 iterations — if we haven't
converged in 40 the deal probably can't hit the target inside the
bounds and we surface that to the UI.

The solver assumes a single moving piece: purchase price. Equity is
re-derived as (purchase_price - debt) — the LTV stays fixed. This is
the standard institutional question, not "what LTV would unlock the
target" (different solver, future work).

Wave 2 P2.8.

FON-68 — the hurdles are the analyst's, not the solver's. ``solve_max_price``
no longer carries default targets: the caller passes the Investment
Profile's Target Levered IRR / Target MOIC (either may be ``None`` to
solve a single constraint; both ``None`` is an error). Each requested
search reports a ``*_status`` so a target that lies outside the
50–200% price bracket renders as "no price clears the hurdle" rather
than as the bracket endpoint dressed up as a price.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .returns import ReturnsEngine, ReturnsEngineInputExt


# ─────────────────────────── tuning ─────────────────────────────────


# Legacy institutional norms. NOT used as solver defaults any more
# (FON-68): they remain only for the export path
# (``export/live_payload.py``) which still instantiates the historical
# ``_MaxPriceRequest`` defaults — a known follow-up.
DEFAULT_TARGET_IRR: float = 0.15
DEFAULT_TARGET_EM: float = 1.8

# The 422 / ValueError copy when neither hurdle is available.
NO_TARGET_MESSAGE: str = (
    "No return target set — set Target Levered IRR / Target MOIC on the "
    "Investment Profile or pass them explicitly"
)

SolveStatus = Literal["converged", "unreachable", "above_ceiling", "not_requested"]

# Bisection bounds expressed as a multiplier of the base purchase price.
PRICE_FLOOR_MULTIPLIER: float = 0.5
PRICE_CEILING_MULTIPLIER: float = 2.0

# Convergence tolerances. Either side terminates the search.
PRICE_TOLERANCE_USD: float = 50_000.0
IRR_TOLERANCE_PCT: float = 0.0005  # 5 bp

# Hard iteration cap. Bisection converges geometrically — at 40 iters
# the bracket is base_price * 2^-40 ≈ a few dollars, so anything not
# converged by 40 is genuinely outside the bracket, not just slow.
MAX_ITERS: int = 40


# ─────────────────────────── dataclass ──────────────────────────────


@dataclass
class MaxPriceResult:
    """Two max-price scalars + the binding-constraint chip.

    ``final_price_per_key`` is the per-key of the *binding* (i.e.
    actually offerable) price — ``min(irr_price, em_price)``.

    ``iters`` is the SUM of bisection iterations across both searches;
    useful for telemetry and tests, not for end-user display.
    """

    target_irr: float | None
    target_em: float | None
    max_price_for_irr: float | None
    max_price_for_em: float | None
    binding_constraint: Literal["irr", "em", "both"]
    final_price_per_key: float
    iters: int
    # FON-68 — the offerable headline: the lower of the *converged*
    # requested prices. ``None`` when the binding constraint's search did
    # not converge (target unreachable inside the bracket, or cleared even
    # at the 200% ceiling) — the UI renders "—", never the bracket end.
    max_price: float | None = None
    # Per-search outcome. ``unreachable`` = no price ≥ the 50% floor clears
    # the hurdle; ``above_ceiling`` = the hurdle clears even at 2× base
    # (max price ≥ ceiling); ``not_requested`` = target was ``None``.
    irr_status: SolveStatus = "converged"
    em_status: SolveStatus = "converged"


# ─────────────────────────── core bisect ────────────────────────────


def _flex_price(
    base: ReturnsEngineInputExt, new_price: float
) -> ReturnsEngineInputExt:
    """Return a copy of ``base`` with the purchase price replaced.

    Equity is re-derived as (new_price + closing + reno - debt). For
    bisection we hold the loan amount fixed — institutional convention
    is that the lender has approved a number, and the sponsor's equity
    cheque flexes to make the deal close.
    """
    new_assumptions = base.assumptions.model_copy(
        update={"purchase_price": new_price}
    )
    # Equity = original price + capex etc - debt = new_price + (original_equity - original_price + debt) - debt
    # Simpler: original_equity + (new_price - original_price).
    delta = new_price - base.assumptions.purchase_price
    new_equity = max(1.0, base.equity + delta)
    return base.model_copy(
        update={"assumptions": new_assumptions, "equity": new_equity}
    )


def _bisect(
    base: ReturnsEngineInputExt,
    *,
    target: float,
    metric: Literal["levered_irr", "equity_multiple"],
    lo: float,
    hi: float,
    max_iters: int = MAX_ITERS,
) -> tuple[float, int, bool]:
    """Generic bisection — returns ``(price, iters_used, converged)``.

    Standard institutional return surfaces are monotone-decreasing in
    purchase price (higher price → lower IRR / EM), so the bisection
    invariant is: if ``metric(price) > target`` we're paying too little
    (move price up); if < target, we're paying too much (move down).

    Returns the converged price + the iteration count + a ``converged``
    flag. When unconverged after ``max_iters``, returns the bracket
    midpoint and ``converged=False``; the caller uses the no-solution
    chip rather than displaying a misleading scalar.
    """
    engine = ReturnsEngine()

    # Sanity-check the bracket: target must lie between metric(lo) and
    # metric(hi). If it doesn't, the target is unreachable in the
    # bisection window — return the closer-of-the-two without
    # converging.
    metric_lo = getattr(engine.run(_flex_price(base, lo)), metric)
    metric_hi = getattr(engine.run(_flex_price(base, hi)), metric)

    if (metric_lo - target) * (metric_hi - target) > 0:
        # Same sign on both ends — target lies outside the bracket.
        if abs(metric_lo - target) < abs(metric_hi - target):
            return lo, 1, False
        return hi, 1, False

    iters = 0
    for _ in range(max_iters):
        iters += 1
        mid = (lo + hi) / 2.0
        flexed = _flex_price(base, mid)
        result = engine.run(flexed)
        value = getattr(result, metric)

        if abs(value - target) < (
            IRR_TOLERANCE_PCT if metric == "levered_irr" else 0.005
        ):
            return mid, iters, True
        if abs(hi - lo) < PRICE_TOLERANCE_USD:
            return mid, iters, True

        # Monotone-decreasing convention: at higher prices, returns drop.
        if value > target:
            lo = mid  # we can pay more
        else:
            hi = mid  # we must pay less

    return (lo + hi) / 2.0, iters, False


# ─────────────────────────── public entrypoint ──────────────────────


def _status(price: float, converged: bool, *, lo: float, hi: float) -> SolveStatus:
    """Classify one bisection outcome.

    ``_bisect`` only fails to converge when the target lies outside the
    bracket, in which case it hands back the closer bracket end: the
    floor (hurdle unreachable at any price ≥ 50% of base) or the ceiling
    (hurdle clears even at 200% of base).
    """
    if converged:
        return "converged"
    if abs(price - lo) <= abs(price - hi):
        return "unreachable"
    return "above_ceiling"


def solve_max_price(
    base_input: ReturnsEngineInputExt,
    *,
    target_irr: float | None,
    target_em: float | None,
    rooms: int | None = None,
) -> MaxPriceResult:
    """Solve for the max purchase price hitting ``target_irr`` AND ``target_em``.

    Runs one independent bisection per *requested* metric, then takes
    the binding (lower) price as the offerable headline. Both targets
    are surfaced so the analyst can see which constraint binds and by
    how much.

    Parameters
    ----------
    base_input:
        The returns engine input — same shape used by the rest of the
        pipeline.
    target_irr / target_em:
        The analyst's hurdles (Investment Profile). ``None`` skips that
        constraint; both ``None`` raises ``ValueError(NO_TARGET_MESSAGE)``
        — there is no default hurdle (FON-68).
    rooms:
        Used to derive ``final_price_per_key``. When omitted, the
        per-key field is 0.0 — the headline number is still meaningful
        but the per-key chip will render as empty.

    Returns
    -------
    MaxPriceResult
        Both prices + binding chip + headline ``max_price`` + statuses.
    """
    if target_irr is None and target_em is None:
        raise ValueError(NO_TARGET_MESSAGE)

    base_price = base_input.assumptions.purchase_price
    lo = base_price * PRICE_FLOOR_MULTIPLIER
    hi = base_price * PRICE_CEILING_MULTIPLIER

    irr_price: float | None = None
    em_price: float | None = None
    irr_status: SolveStatus = "not_requested"
    em_status: SolveStatus = "not_requested"
    iters_irr = iters_em = 0

    if target_irr is not None:
        raw, iters_irr, conv = _bisect(
            base_input,
            target=target_irr,
            metric="levered_irr",
            lo=lo,
            hi=hi,
        )
        # Floor: never report a price below 50% of base.
        irr_price = max(raw, lo)
        irr_status = _status(irr_price, conv, lo=lo, hi=hi)
    if target_em is not None:
        raw, iters_em, conv = _bisect(
            base_input,
            target=target_em,
            metric="equity_multiple",
            lo=lo,
            hi=hi,
        )
        em_price = max(raw, lo)
        em_status = _status(em_price, conv, lo=lo, hi=hi)

    # Binding constraint: the smaller (more conservative) max price wins.
    binding: Literal["irr", "em", "both"]
    if irr_price is not None and em_price is not None:
        diff = abs(irr_price - em_price)
        if diff < PRICE_TOLERANCE_USD:
            binding = "both"
            final_price = (irr_price + em_price) / 2.0
            solvable = irr_status == "converged" and em_status == "converged"
        elif irr_price < em_price:
            binding = "irr"
            final_price = irr_price
            solvable = irr_status == "converged"
        else:
            binding = "em"
            final_price = em_price
            solvable = em_status == "converged"
    elif irr_price is not None:
        binding = "irr"
        final_price = irr_price
        solvable = irr_status == "converged"
    else:
        assert em_price is not None
        binding = "em"
        final_price = em_price
        solvable = em_status == "converged"

    max_price = final_price if solvable else None
    per_key = (final_price / rooms) if rooms and rooms > 0 else 0.0

    return MaxPriceResult(
        target_irr=target_irr,
        target_em=target_em,
        max_price_for_irr=irr_price,
        max_price_for_em=em_price,
        binding_constraint=binding,
        final_price_per_key=per_key,
        iters=iters_irr + iters_em,
        max_price=max_price,
        irr_status=irr_status,
        em_status=em_status,
    )


__all__ = [
    "DEFAULT_TARGET_EM",
    "DEFAULT_TARGET_IRR",
    "MAX_ITERS",
    "NO_TARGET_MESSAGE",
    "MaxPriceResult",
    "PRICE_CEILING_MULTIPLIER",
    "PRICE_FLOOR_MULTIPLIER",
    "PRICE_TOLERANCE_USD",
    "SolveStatus",
    "solve_max_price",
]
