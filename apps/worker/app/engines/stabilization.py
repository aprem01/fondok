"""The stabilized operating year — ONE resolver, ONE published block.

FON-41 / FON-59 #3. Four incompatible "stabilized" definitions used to ship
side by side: the debt engine's occupancy/NOI-plateau signal, Scenario
Analysis' last element of ``noi_by_year``, the IC memo's ``years[0].noi`` and
Overview's ``returns.terminal_noi`` (the year hold+1 reversion). None of them
was analyst-selectable, and no two agreed.

This module is the single source. It publishes:

* :func:`resolve_stabilized_year_index` — THE stabilized year index: the
  analyst's year, else the Year-3-after-close default clamped to the hold.
  Debt re-exports it as ``_resolve_stabilized_year_index`` for its stabilized
  DSCR / debt yield.
* :func:`resolve_stabilized_year` — the model-DETECTED hint (occupancy signal,
  NOI-plateau fallback), returning the 0-based index and WHICH signal found
  it. Published on the block; it no longer selects the year.
* :class:`StabilizedYear` — the coherent block every consumer reads: one year
  index and the occupancy / ADR / revenue / NOI / margin OF THAT YEAR, so
  Overview, the IC memo and Scenario Analysis cannot drift apart again.

FON-59 R-057 (Sam's decision 1). The stabilized year is a FIXED DEFAULT —
**Year 3 after acquisition close** (:data:`DEFAULT_STABILIZATION_YEAR`,
anchored at :data:`DEFAULT_STABILIZATION_ANCHOR`) — that the analyst may
override (``stabilization_year``, 1-based). It is no longer derived from the
projection: an un-displaced deal's occupancy reaches its own stabilized
assumption in Year 1, so the old occupancy/NOI-plateau seed told testers the
asset "stabilizes" in Year 1. That detection still runs, but only as a hint
(``detected_year`` / ``detected_signal``) — it never selects the year.

A hold shorter than three projected years clamps the default to the last
projected year and says so (``clamped``). An analyst value equal to the
default stays ``default_year_3`` — re-saving the default unchanged is not an
override (the same rule ``engine_runner._is_shadow_override`` applies to every
scalar assumption). Stabilized NOI here is the operating NOI of that year; the
exit NOI (the year hold+1 reversion) is a separate concept owned by Returns.

Rani is still confirming whether the anchor is acquisition close or the
re-flag date. The anchor is this ONE constant; projection Year 1 is the first
year after close, so "Year 3 after close" is projection index 2.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# FON-59 R-057 — the default stabilized year (1-based) and what it counts
# from. Change the anchor HERE only if Rani lands on the re-flag date.
DEFAULT_STABILIZATION_YEAR = 3
DEFAULT_STABILIZATION_ANCHOR = "acquisition_close"

# How the model-detected hint was found.
StabilizationSignal = Literal["occupancy", "noi_plateau"]
# Who owns the year on the block.
StabilizationSource = Literal["default_year_3", "analyst_override"]
StabilizationAnchor = Literal["acquisition_close"]


def default_stabilized_year_index(n_years: int) -> int | None:
    """0-based index of the default stabilized year, clamped to the projection.

    ``DEFAULT_STABILIZATION_YEAR - 1``, or the last projected year when the
    projection is shorter. ``None`` only when there is no projection at all.
    """
    if n_years <= 0:
        return None
    return min(DEFAULT_STABILIZATION_YEAR - 1, n_years - 1)


def _analyst_index(stabilization_year: object, n_years: int) -> int | None:
    """The analyst's 1-based year as a 0-based index, or ``None`` when absent
    or outside the projection (a year the projection does not have)."""
    if stabilization_year is None or isinstance(stabilization_year, bool):
        return None
    try:
        index = int(stabilization_year) - 1  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < n_years else None


def resolve_stabilized_year(
    *,
    occupancy_by_year: list[float] | None,
    stabilized_occupancy: float | None,
    noi_by_year: list[float],
) -> tuple[int | None, StabilizationSignal | None]:
    """MODEL-DETECTED hint: the first stabilized projection year + its signal.

    FON-59 R-057 — this no longer selects the stabilized year (that is the
    Year-3 default or the analyst's override, see
    :func:`resolve_stabilized_year_index`). It is published on the block as
    ``detected_year`` / ``detected_signal`` so the analyst can see what the
    projection shape suggests.

    Primary signal (approved definition): the first year the projected
    occupancy reaches the deal's post-ramp stabilized-occupancy assumption.
    The revenue engine treats ``starting_occupancy`` as the stabilized
    baseline and only Year 1 (and, under a PIP, its recovery) sits below it,
    so an un-displaced deal stabilizes in Year 1.

    Fallback (no occupancy signal): the NOI plateau — walk the NOI series and
    take the year AFTER the last above-terminal growth step, i.e. the first
    year year-over-year NOI growth has settled to the terminal (final-period)
    rate and the ramp is complete. A series with no ramp (growth never exceeds
    terminal) is stabilized from Year 1 (index 0).

    Returns ``(None, None)`` only when neither signal resolves — the caller
    renders a dash with a reason, never a guessed year. An occupancy target
    the projection never reaches is NOT a refusal: it hands off to the NOI
    plateau.
    """
    # Primary — occupancy reaches the stabilized assumption.
    if (
        occupancy_by_year
        and stabilized_occupancy is not None
        and stabilized_occupancy > 0
    ):
        eps = 1e-9
        for i, occ in enumerate(occupancy_by_year):
            if occ is not None and occ >= stabilized_occupancy - eps:
                return i, "occupancy"
        # Occupancy never reaches the target. That is not "there is no
        # stabilized year" — it is this signal declining to answer, so fall
        # through to the NOI plateau rather than refusing. Returning here
        # blanks every Stabilization row on any deal whose ramp tops out below
        # its own stabilized-occupancy assumption, which is an ordinary shape
        # for a renovation deal.
        #
        # Provenance of this fix, stated honestly: it was written while
        # chasing a blank Stabilization block on Sam MVP Test 2, and the
        # commit first claimed this was the cause. It was not — that deal's
        # occupancy assumption IS reached in Year 1, so this branch returned
        # normally and the blank was a stale engine run from before the block
        # existed. The defect below is real and reachable (any target above
        # the ramp's ceiling), but it was not what was on screen.

    # Fallback — NOI plateau.
    n = len(noi_by_year)
    if n == 0:
        return None, None
    if n == 1:
        return 0, "noi_plateau"
    terminal_growth = (
        (noi_by_year[-1] / noi_by_year[-2] - 1.0)
        if noi_by_year[-2] > 0
        else 0.0
    )
    tol = 0.005  # 0.5 percentage-point tolerance on the terminal rate
    last_ramp_step = -1
    for j in range(n - 1):
        prev = noi_by_year[j]
        growth = (noi_by_year[j + 1] / prev - 1.0) if prev > 0 else 0.0
        if growth > terminal_growth + tol:
            last_ramp_step = j
    if last_ramp_step < 0:
        return 0, "noi_plateau"
    return min(last_ramp_step + 1, n - 1), "noi_plateau"


def resolve_stabilized_year_index(
    *,
    occupancy_by_year: list[float] | None,
    stabilized_occupancy: float | None,
    noi_by_year: list[float],
    stabilization_year: int | None = None,
) -> int | None:
    """0-based index of THE stabilized year — the debt engine's entry point.

    FON-59 R-057: the analyst's ``stabilization_year`` when it names a
    projected year, else the Year-3-after-close default clamped to the last
    projected year. ``occupancy_by_year`` / ``stabilized_occupancy`` are kept
    in the signature so every caller stays source-compatible; they feed only
    the detected-year hint (:func:`resolve_stabilized_year`), never this index.
    ``None`` only when ``noi_by_year`` is empty.
    """
    del occupancy_by_year, stabilized_occupancy  # hint inputs, not selectors
    n = len(noi_by_year)
    analyst = _analyst_index(stabilization_year, n)
    if analyst is not None:
        return analyst
    return default_stabilized_year_index(n)


class StabilizedYear(BaseModel):
    """The stabilized operating year, as ONE object every surface reads.

    Every figure is read from the SAME ``stabilized_year_index`` — that is the
    whole point of the block (Sam: *"All metrics must reconcile to the same
    projection year"*). A figure the projection cannot supply is ``None`` and
    renders as a dash; it is never zero and never borrowed from another year.
    """

    model_config = ConfigDict(extra="forbid")

    # 0-based index into the projection years; ``stabilized_year`` is the
    # 1-based model year the analyst sees ("Year 2").
    stabilized_year_index: Annotated[int, Field(ge=0)]
    stabilized_year: Annotated[int, Field(ge=1)]
    # Who owns the year. ``default_year_3`` covers both "nobody set one" and
    # "the analyst re-saved the default unchanged".
    source: StabilizationSource
    # The rule the default follows: ``default_year`` years after ``anchor``.
    anchor: StabilizationAnchor = DEFAULT_STABILIZATION_ANCHOR
    default_year: Annotated[int, Field(ge=1)] = DEFAULT_STABILIZATION_YEAR
    # True when the default was pulled back to the last projected year because
    # the hold is shorter than ``default_year`` (only ever on the default).
    clamped: bool = False
    # The model-detected hint (occupancy reaches its stabilized assumption,
    # else the NOI plateau) — 1-based, shown beneath the year, never selects it.
    detected_year: Annotated[int, Field(ge=1)] | None = None
    detected_signal: StabilizationSignal | None = None
    stabilized_occupancy: float | None = None
    stabilized_adr: float | None = None
    stabilized_revenue: float | None = None
    # NOI BEFORE the FF&E reserve — the bare word "NOI" in Fondok
    # (``expense.years[].noi_institutional``). See apps/web/src/lib/engines/noi.ts.
    stabilized_noi_before_reserve: float | None = None
    # Cash NOI (after the FF&E reserve) of the same year, so a surface that
    # needs the after-reserve basis does not go read another year to get it.
    stabilized_cash_noi: float | None = None
    stabilized_noi_margin: float | None = None


def build_stabilized_year(
    *,
    total_revenue_by_year: list[float],
    noi_before_reserve_by_year: list[float | None],
    cash_noi_by_year: list[float],
    occupancy_by_year: list[float] | None = None,
    adr_by_year: list[float] | None = None,
    stabilized_occupancy: float | None = None,
    stabilization_year: int | None = None,
) -> StabilizedYear | None:
    """Assemble the block for one projection, or ``None`` when no year resolves.

    ``stabilization_year`` is the analyst's 1-based model year. Out of range
    (or absent) falls back to the Year-3 default (clamped to the hold); equal
    to the default it stays ``default_year_3`` — re-confirming is not an
    override.
    """
    n = len(total_revenue_by_year)
    detected_index, detected_signal = resolve_stabilized_year(
        occupancy_by_year=occupancy_by_year,
        stabilized_occupancy=stabilized_occupancy,
        noi_by_year=cash_noi_by_year,
    )
    default_index = default_stabilized_year_index(n)
    if default_index is None:
        return None

    analyst_index = _analyst_index(stabilization_year, n)
    if analyst_index is not None and analyst_index != default_index:
        index = analyst_index
        source: StabilizationSource = "analyst_override"
    else:
        index = default_index
        source = "default_year_3"
    clamped = (
        source == "default_year_3"
        and index < DEFAULT_STABILIZATION_YEAR - 1
    )

    revenue = total_revenue_by_year[index]
    # A pre-upgrade projection never carried ``noi_institutional``; the block
    # then publishes no before-reserve NOI and no margin rather than passing
    # the after-reserve figure off as one. ``stabilized_cash_noi`` still
    # carries the honest after-reserve number.
    noi_before = (
        noi_before_reserve_by_year[index]
        if index < len(noi_before_reserve_by_year)
        else None
    )
    margin = (
        (noi_before / revenue)
        if (noi_before is not None and revenue and revenue > 0)
        else None
    )
    return StabilizedYear(
        stabilized_year_index=index,
        stabilized_year=index + 1,
        source=source,
        clamped=clamped,
        detected_year=(
            detected_index + 1
            if detected_index is not None and 0 <= detected_index < n
            else None
        ),
        detected_signal=(
            detected_signal
            if detected_index is not None and 0 <= detected_index < n
            else None
        ),
        stabilized_occupancy=(
            occupancy_by_year[index]
            if occupancy_by_year and index < len(occupancy_by_year)
            else None
        ),
        stabilized_adr=(
            adr_by_year[index]
            if adr_by_year and index < len(adr_by_year)
            else None
        ),
        stabilized_revenue=revenue,
        stabilized_noi_before_reserve=noi_before,
        stabilized_cash_noi=(
            cash_noi_by_year[index] if index < len(cash_noi_by_year) else None
        ),
        stabilized_noi_margin=margin,
    )


__all__ = [
    "DEFAULT_STABILIZATION_ANCHOR",
    "DEFAULT_STABILIZATION_YEAR",
    "StabilizationAnchor",
    "StabilizationSignal",
    "StabilizationSource",
    "StabilizedYear",
    "build_stabilized_year",
    "default_stabilized_year_index",
    "resolve_stabilized_year",
    "resolve_stabilized_year_index",
]
