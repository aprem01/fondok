"""Pricing sensitivity grid — 5x5 matrix flexing exit cap × NOI multiplier.

Every IC committee asks the same question at the end of a memo:

    "What happens to IRR if the exit cap moves +50bp, or if Y1 NOI
    comes in 10% lower than projected?"

This module answers it. It re-runs the deterministic ``ReturnsEngine``
across a small grid of (exit_cap_rate, noi_multiplier) cells anchored at
the base assumption set. The default grid is 5x5:

    Exit cap axis: base ± {-100bp, -50bp, 0, +50bp, +100bp}
    NOI axis:      base × {0.85, 0.925, 1.0, 1.075, 1.15}

It also locates the **breakeven** exit cap and NOI multiplier — the
single-axis sweep value at which levered IRR crosses a target. These
two numbers are what an IC analyst writes underneath the grid:
"this deal pencils until exit cap exceeds 8.4% or Y1 NOI falls below
$3.1M".

The function is pure: no DB, no LLM, no I/O. It takes a ``base`` returns
input and a small config dataclass and returns a ``SensitivityGrid``
suitable for direct JSON serialisation.

Wave 2 P2.8 — paired with ``price_solver.py`` (max-price-for-target-return)
and ``loi_generator.py`` (Letter of Intent draft). The trio is the
"headline numbers" Sam asks for at the bottom of every memo.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .returns import ReturnsEngine, ReturnsEngineInputExt


# ─────────────────────────── default axes ────────────────────────────


# Default exit-cap deltas in absolute rate points (not bp). e.g. -0.01
# means "100 bp below base". Symmetric ±100bp window with a 50bp grid
# resolution — the standard hospitality IC presentation.
DEFAULT_CAP_DELTAS: tuple[float, ...] = (-0.01, -0.005, 0.0, 0.005, 0.01)

# Default Y1-NOI scalar — 85% to 115% of underwritten NOI in 7.5% steps.
# Asymmetric look: more downside coverage matters more than upside.
DEFAULT_NOI_MULTIPLIERS: tuple[float, ...] = (0.85, 0.925, 1.0, 1.075, 1.15)


# ─────────────────────────── dataclasses ─────────────────────────────


@dataclass
class SensitivityCell:
    """One cell of the sensitivity grid — the output of a single flex.

    ``breaches_dscr_floor`` flags when the Y1 DSCR drops below 1.0x at
    this cell — the IRR/EM numbers are still computed and reported (the
    underwriter still wants to see "if NOI drops 15%, IRR goes to X"),
    but the cell should render with a warning glyph in the heatmap.
    """

    exit_cap_pct: float
    noi_multiplier: float
    levered_irr: float
    equity_multiple: float
    going_in_cap_rate: float
    dscr_y1: float
    breaches_dscr_floor: bool = False


@dataclass
class SensitivityGrid:
    """Full 2-D sensitivity grid plus the two breakeven scalars.

    ``cells`` is a flat list — 5x5 = 25 cells by default — laid out in
    row-major order: outer loop NOI multiplier (high → low), inner loop
    exit cap (low → high). The UI re-shapes this for rendering; the
    flat layout keeps JSON serialization trivial.
    """

    base_exit_cap_pct: float
    base_stabilized_noi: float
    cells: list[SensitivityCell] = field(default_factory=list)
    breakeven_exit_cap_pct: float | None = None
    breakeven_noi_multiplier: float | None = None


# ─────────────────────────── helpers ─────────────────────────────────


def _flex_returns_input(
    base: ReturnsEngineInputExt,
    *,
    exit_cap_pct: float,
    noi_multiplier: float,
) -> ReturnsEngineInputExt:
    """Apply one (exit_cap, noi_multiplier) flex to a returns input.

    The NOI multiplier scales the entire ``year_one_noi`` + ``noi_by_year``
    series uniformly — not just Y1 — because under a stress scenario the
    underwriter is asking "what if T-12 was overstated by 10% across the
    board". This mirrors how Brookfield + Apollo run their downside cases.
    """
    new_assumptions = base.assumptions.model_copy(
        update={"exit_cap_rate": exit_cap_pct}
    )
    new_year_one = base.year_one_noi * noi_multiplier
    new_noi_by_year = (
        [n * noi_multiplier for n in base.noi_by_year]
        if base.noi_by_year
        else []
    )
    new_terminal = (
        base.terminal_noi_override * noi_multiplier
        if base.terminal_noi_override is not None
        else None
    )
    return base.model_copy(
        update={
            "assumptions": new_assumptions,
            "year_one_noi": new_year_one,
            "noi_by_year": new_noi_by_year,
            "terminal_noi_override": new_terminal,
        }
    )


def _compute_dscr_y1(base: ReturnsEngineInputExt, noi_multiplier: float) -> float:
    """Y1 NOI / annual debt service for a flex cell.

    Falls back to ``inf`` (sentinel: comfortably above floor) when
    annual debt service is zero — common for an all-cash purchase or
    when the debt engine hasn't been run yet upstream.

    Debt service is held constant under flex — the loan is already
    closed; banks don't refinance per scenario. Only the NOI numerator
    moves.
    """
    flexed_y1 = base.year_one_noi * noi_multiplier
    if base.annual_debt_service <= 0:
        return float("inf")
    return flexed_y1 / base.annual_debt_service


def _solve_breakeven(
    samples: list[tuple[float, float]],
    target: float,
) -> float | None:
    """Linearly interpolate the x where y crosses ``target``.

    ``samples`` is ``[(x, y), ...]`` sorted in ascending x. Returns
    ``None`` when ``target`` lies outside the [min y, max y] range —
    i.e. no flex within the swept window hits the target.
    """
    if len(samples) < 2:
        return None
    ys = [y for _, y in samples]
    if target > max(ys) or target < min(ys):
        return None
    # Find the bracketing pair.
    for (x0, y0), (x1, y1) in zip(samples, samples[1:]):
        if (y0 - target) * (y1 - target) <= 0:
            if y1 == y0:
                return x0
            t = (target - y0) / (y1 - y0)
            return x0 + t * (x1 - x0)
    return None


# ─────────────────────────── main entrypoint ─────────────────────────


def run_sensitivity_grid(
    base_input: ReturnsEngineInputExt,
    *,
    target_irr: float = 0.15,
    cap_axis: list[float] | None = None,
    noi_axis: list[float] | None = None,
) -> SensitivityGrid:
    """Compute a 2-D sensitivity grid around ``base_input``.

    Parameters
    ----------
    base_input:
        The returns engine input as it would be built by
        ``engine_runner._build_input_for("returns", ...)``. Holds the
        baseline ``exit_cap_rate`` (on ``.assumptions``), ``year_one_noi``,
        ``noi_by_year`` and the closed-out ``annual_debt_service``.
    target_irr:
        IRR used for the breakeven sweep. The grid itself doesn't
        threshold IRR — the UI colours cells against ``target_irr``,
        and the breakeven scalars locate where IRR === target.
    cap_axis:
        Optional explicit list of exit-cap rates (absolute, e.g.
        ``[0.065, 0.07, 0.075]``). When omitted the default ±100bp
        window is anchored at the base exit cap.
    noi_axis:
        Optional explicit list of NOI multipliers (e.g. ``[0.9, 1.0,
        1.1]``). When omitted the default 0.85–1.15 window is used.

    Returns
    -------
    SensitivityGrid
        Flat list of cells plus two breakeven scalars. ``cells`` is in
        row-major (NOI high → low, cap low → high) layout — the UI
        re-shapes to a square for rendering.
    """
    base_exit_cap = base_input.assumptions.exit_cap_rate
    base_noi = base_input.year_one_noi
    engine = ReturnsEngine()

    if cap_axis is None:
        cap_axis = [round(base_exit_cap + d, 6) for d in DEFAULT_CAP_DELTAS]
    if noi_axis is None:
        noi_axis = list(DEFAULT_NOI_MULTIPLIERS)

    # Defensive: clamp exit cap rates to (0, 0.30] — the ModelAssumptions
    # validator would reject anything outside that band, and we'd rather
    # surface a clamped cell than a 422 mid-grid.
    cap_axis = [max(0.005, min(0.299, c)) for c in cap_axis]

    cells: list[SensitivityCell] = []
    # Row-major: NOI multiplier descends (high → low rendering top-down),
    # cap rate ascends (cheap → expensive left-to-right).
    for nm in sorted(noi_axis, reverse=True):
        for cap in sorted(cap_axis):
            flexed = _flex_returns_input(
                base_input, exit_cap_pct=cap, noi_multiplier=nm
            )
            result = engine.run(flexed)
            # Going-in cap rate uses the original purchase price (the
            # whole point of sensitivity is to fix price and flex the
            # rest) but the flexed Y1 NOI.
            going_in = (
                (base_noi * nm) / base_input.assumptions.purchase_price
                if base_input.assumptions.purchase_price > 0
                else 0.0
            )
            dscr = _compute_dscr_y1(base_input, nm)
            cells.append(
                SensitivityCell(
                    exit_cap_pct=cap,
                    noi_multiplier=nm,
                    levered_irr=result.levered_irr,
                    equity_multiple=result.equity_multiple,
                    going_in_cap_rate=going_in,
                    dscr_y1=dscr if dscr != float("inf") else 0.0,
                    breaches_dscr_floor=dscr < 1.0,
                )
            )

    # ── Breakeven sweep ──────────────────────────────────────────────
    # Single-axis sweep: hold the other axis at base, find where IRR
    # crosses the target. We sweep across the same axis values used in
    # the grid for consistency with what the analyst sees on screen.

    # Exit cap sweep at NOI multiplier == 1.0.
    cap_sweep: list[tuple[float, float]] = []
    for cap in sorted(cap_axis):
        flexed = _flex_returns_input(
            base_input, exit_cap_pct=cap, noi_multiplier=1.0
        )
        cap_sweep.append((cap, engine.run(flexed).levered_irr))
    breakeven_cap = _solve_breakeven(cap_sweep, target_irr)

    # NOI multiplier sweep at exit cap == base.
    noi_sweep: list[tuple[float, float]] = []
    for nm in sorted(noi_axis):
        flexed = _flex_returns_input(
            base_input, exit_cap_pct=base_exit_cap, noi_multiplier=nm
        )
        noi_sweep.append((nm, engine.run(flexed).levered_irr))
    breakeven_noi = _solve_breakeven(noi_sweep, target_irr)

    return SensitivityGrid(
        base_exit_cap_pct=base_exit_cap,
        base_stabilized_noi=base_noi,
        cells=cells,
        breakeven_exit_cap_pct=breakeven_cap,
        breakeven_noi_multiplier=breakeven_noi,
    )


__all__ = [
    "DEFAULT_CAP_DELTAS",
    "DEFAULT_NOI_MULTIPLIERS",
    "SensitivityCell",
    "SensitivityGrid",
    "run_sensitivity_grid",
]
