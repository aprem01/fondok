"""Max-price grid — the Pricing sub-tab's sensitivity of *price*, not returns.

FON-68 (Sam): "Pricing sensitivity cells solve for max purchase price
clearing both hurdles." Where ``pricing_sensitivity.py`` answers "how do
returns move" (IRR per cell), this module answers "how much can we pay"
(max purchase price per cell). Every cell is an independent
``solve_max_price`` run — two bisections × ≤ 40 iterations — over an
Exit Cap × NOI-growth grid anchored at the deal's own assumptions:

    Exit cap axis:    base exit cap ± {-100bp, -50bp, 0, +50bp, +100bp}
    NOI growth axis:  base growth  ± {-2pp,  -1pp,  0, +1pp,  +2pp}

The grid is capped at 25 cells (≤ 5 × ≤ 5) so a request stays sub-second.

How "NOI growth" flexes the model
---------------------------------
The returns input carries the canonical NOI series (``noi_by_year``) from
the expense engine — ramp, PIP displacement and all. A cell with growth
``g`` re-tilts that series relative to the deal's base growth assumption
``g0`` (``assumptions.revpar_growth``, the same base the Sensitivities
sub-tab's RevPAR-growth slider reads):

    noi[i] → noi[i] × ((1 + g) / (1 + g0)) ** i

so year 1 is unchanged, later years grow faster or slower, and the
terminal NOI (forward NOI ÷ exit cap) moves with it. At ``g == g0`` the
series is untouched — the base cell reproduces the Max Price Solver
headline exactly. ``assumptions.revpar_growth`` is set to ``g`` so the
engine's own series extension / terminal growth uses the cell's rate.

Pure: no DB, no I/O. Raises ``ValueError`` on an oversized grid or when
neither hurdle is provided (there is no default hurdle).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .price_solver import (
    NO_TARGET_MESSAGE,
    MaxPriceResult,
    SolveStatus,
    solve_max_price,
)
from .returns import ReturnsEngineInputExt

# ─────────────────────────── default axes ────────────────────────────

# Exit-cap deltas in absolute rate points — mirrors pricing_sensitivity.
DEFAULT_CAP_DELTAS: tuple[float, ...] = (-0.01, -0.005, 0.0, 0.005, 0.01)

# NOI-growth deltas in absolute rate points (percentage points / 100).
DEFAULT_GROWTH_DELTAS: tuple[float, ...] = (-0.02, -0.01, 0.0, 0.01, 0.02)

# Hard cap on cells — 25 solves × 2 bisections × ≤ 40 iterations.
MAX_CELLS: int = 25

# ``ModelAssumptions`` bounds (fondok_schemas.financial) — clamp the
# default axes so an extreme base never produces an invalid cell.
_CAP_MIN, _CAP_MAX = 0.005, 0.30
_GROWTH_MIN, _GROWTH_MAX = -0.49, 0.49

TOO_MANY_CELLS_MESSAGE: str = (
    f"Grid is capped at {MAX_CELLS} cells — pass at most 5 exit caps × 5 "
    "NOI-growth rates"
)


# ─────────────────────────── dataclasses ─────────────────────────────


@dataclass
class MaxPriceGridCell:
    """One (exit cap, NOI growth) cell — a full ``solve_max_price`` run.

    ``max_price_for_irr`` / ``max_price_for_em`` are ``None`` unless that
    search converged; ``max_price`` is the lower of the converged
    requested prices (``None`` when the binding search did not converge —
    "no price clears the hurdles at this combination").
    """

    exit_cap_pct: float
    noi_growth_pct: float
    max_price_for_irr: float | None
    max_price_for_em: float | None
    max_price: float | None
    binding_constraint: Literal["irr", "em", "both"]
    irr_status: SolveStatus
    em_status: SolveStatus
    price_per_key: float | None
    is_base: bool


@dataclass
class MaxPriceGrid:
    """Row-major cells: outer loop exit cap (low → high), inner loop NOI
    growth (low → high) — the canonical "EXIT CAP \\ NOI GROWTH" layout."""

    target_irr: float | None
    target_em: float | None
    base_exit_cap_pct: float
    base_noi_growth_pct: float
    base_purchase_price: float
    cap_axis: list[float]
    noi_growth_axis: list[float]
    cells: list[MaxPriceGridCell] = field(default_factory=list)


# ─────────────────────────── helpers ─────────────────────────────────


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _dedupe_sorted(values: list[float]) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or abs(v - out[-1]) > 1e-12:
            out.append(v)
    return out


def flex_returns_input(
    base: ReturnsEngineInputExt,
    *,
    exit_cap_pct: float,
    noi_growth_pct: float,
) -> ReturnsEngineInputExt:
    """Apply one (exit cap, NOI growth) flex to a returns input.

    See the module docstring for the re-tilt rule. Year 1 NOI is never
    touched — the growth axis moves the trajectory, not the anchor.
    """
    g0 = base.assumptions.revpar_growth
    ratio = (1.0 + noi_growth_pct) / (1.0 + g0)
    new_assumptions = base.assumptions.model_copy(
        update={"exit_cap_rate": exit_cap_pct, "revpar_growth": noi_growth_pct}
    )
    new_noi_by_year = [n * (ratio**i) for i, n in enumerate(base.noi_by_year)]
    hold = int(base.assumptions.hold_years)
    new_terminal = (
        base.terminal_noi_override * (ratio**hold)
        if base.terminal_noi_override is not None
        else None
    )
    return base.model_copy(
        update={
            "assumptions": new_assumptions,
            "noi_by_year": new_noi_by_year,
            "terminal_noi_override": new_terminal,
        }
    )


def default_cap_axis(base: ReturnsEngineInputExt) -> list[float]:
    base_cap = base.assumptions.exit_cap_rate
    return _dedupe_sorted(
        [_clamp(base_cap + d, _CAP_MIN, _CAP_MAX) for d in DEFAULT_CAP_DELTAS]
    )


def default_growth_axis(base: ReturnsEngineInputExt) -> list[float]:
    g0 = base.assumptions.revpar_growth
    return _dedupe_sorted(
        [_clamp(g0 + d, _GROWTH_MIN, _GROWTH_MAX) for d in DEFAULT_GROWTH_DELTAS]
    )


# ─────────────────────────── main entrypoint ─────────────────────────


def run_max_price_grid(
    base_input: ReturnsEngineInputExt,
    *,
    target_irr: float | None,
    target_em: float | None,
    cap_axis: list[float] | None = None,
    noi_growth_axis: list[float] | None = None,
    rooms: int | None = None,
) -> MaxPriceGrid:
    """Solve the max purchase price for every (exit cap, NOI growth) cell.

    Parameters
    ----------
    base_input:
        The canonical returns input (same object the Max Price Solver
        headline uses).
    target_irr / target_em:
        The analyst's hurdles from the Investment Profile. Either may be
        ``None`` (single-constraint grid); both ``None`` raises.
    cap_axis / noi_growth_axis:
        Explicit absolute axes. Omitted → the default ±100bp / ±2pp
        windows anchored at the deal's base assumptions.
    rooms:
        For the per-key figure in each cell.
    """
    if target_irr is None and target_em is None:
        raise ValueError(NO_TARGET_MESSAGE)

    caps = _dedupe_sorted(list(cap_axis)) if cap_axis else default_cap_axis(base_input)
    growths = (
        _dedupe_sorted(list(noi_growth_axis))
        if noi_growth_axis
        else default_growth_axis(base_input)
    )
    if not caps or not growths:
        raise ValueError("Grid axes must be non-empty")
    if len(caps) * len(growths) > MAX_CELLS:
        raise ValueError(TOO_MANY_CELLS_MESSAGE)

    base_cap = base_input.assumptions.exit_cap_rate
    base_growth = base_input.assumptions.revpar_growth

    cells: list[MaxPriceGridCell] = []
    for cap in caps:
        for g in growths:
            is_base = abs(cap - base_cap) < 1e-9 and abs(g - base_growth) < 1e-9
            flexed = (
                base_input
                if is_base
                else flex_returns_input(base_input, exit_cap_pct=cap, noi_growth_pct=g)
            )
            res: MaxPriceResult = solve_max_price(
                flexed, target_irr=target_irr, target_em=target_em, rooms=rooms
            )
            cells.append(
                MaxPriceGridCell(
                    exit_cap_pct=cap,
                    noi_growth_pct=g,
                    max_price_for_irr=(
                        res.max_price_for_irr
                        if res.irr_status == "converged"
                        else None
                    ),
                    max_price_for_em=(
                        res.max_price_for_em if res.em_status == "converged" else None
                    ),
                    max_price=res.max_price,
                    binding_constraint=res.binding_constraint,
                    irr_status=res.irr_status,
                    em_status=res.em_status,
                    price_per_key=(
                        res.max_price / rooms
                        if res.max_price is not None and rooms and rooms > 0
                        else None
                    ),
                    is_base=is_base,
                )
            )

    return MaxPriceGrid(
        target_irr=target_irr,
        target_em=target_em,
        base_exit_cap_pct=base_cap,
        base_noi_growth_pct=base_growth,
        base_purchase_price=base_input.assumptions.purchase_price,
        cap_axis=caps,
        noi_growth_axis=growths,
        cells=cells,
    )


__all__ = [
    "DEFAULT_CAP_DELTAS",
    "DEFAULT_GROWTH_DELTAS",
    "MAX_CELLS",
    "TOO_MANY_CELLS_MESSAGE",
    "MaxPriceGrid",
    "MaxPriceGridCell",
    "flex_returns_input",
    "run_max_price_grid",
]
