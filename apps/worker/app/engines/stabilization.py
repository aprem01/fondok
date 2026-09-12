"""The stabilized operating year — ONE resolver, ONE published block.

FON-41 / FON-59 #3. Four incompatible "stabilized" definitions used to ship
side by side: the debt engine's occupancy/NOI-plateau signal, Scenario
Analysis' last element of ``noi_by_year``, the IC memo's ``years[0].noi`` and
Overview's ``returns.terminal_noi`` (the year hold+1 reversion). None of them
was analyst-selectable, and no two agreed.

This module is the single source. It publishes:

* :func:`resolve_stabilized_year` — the Fondok-derived signal, returning both
  the 0-based projection-year index and WHICH signal found it, so a surface can
  say *how* the year was derived rather than asserting it.
* :func:`resolve_stabilized_year_index` — the index alone. This is the exact
  function that used to live in ``engines/debt.py`` as
  ``_resolve_stabilized_year_index``; debt re-exports it so its stabilized DSCR
  / debt yield keep resolving byte-identically.
* :class:`StabilizedYear` — the coherent block every consumer reads: one year
  index and the occupancy / ADR / revenue / NOI / margin OF THAT YEAR, so
  Overview, the IC memo and Scenario Analysis cannot drift apart again.

The analyst owns the year (``stabilization_year``, 1-based). Absent one, the
block reports the derived seed and labels itself ``fondok_derived`` so the UI
can badge it "Fondok-derived — confirm". An analyst value that EQUALS the
derived seed stays ``fondok_derived`` — re-saving a seed unchanged is not an
override (the same rule ``engine_runner._is_shadow_override`` applies to every
scalar assumption).
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# How the derived year was found.
StabilizationSignal = Literal["occupancy", "noi_plateau"]
# Who owns the year on the block.
StabilizationSource = Literal["fondok_derived", "analyst_override"]


def resolve_stabilized_year(
    *,
    occupancy_by_year: list[float] | None,
    stabilized_occupancy: float | None,
    noi_by_year: list[float],
) -> tuple[int | None, StabilizationSignal | None]:
    """0-based index of the first stabilized projection year + the signal used.

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
        # blanked every Stabilization row on any deal whose ramp tops out
        # below its own stabilized-occupancy assumption, which is the common
        # case on a renovation deal (found live on Sam MVP Test 2: occupancy
        # projects 71.6% -> 73.9% against a higher target, so the whole block
        # came back null and Overview showed five dashes).

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
) -> int | None:
    """The index alone — the debt engine's historical entry point."""
    index, _signal = resolve_stabilized_year(
        occupancy_by_year=occupancy_by_year,
        stabilized_occupancy=stabilized_occupancy,
        noi_by_year=noi_by_year,
    )
    return index


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
    # Who owns the year. ``fondok_derived`` covers both "nobody set one, this
    # is the signal" and "the analyst confirmed the signal unchanged".
    source: StabilizationSource
    # Which signal produced the derived seed (None when no seed resolved and
    # the analyst supplied the year outright).
    signal: StabilizationSignal | None = None
    # The derived seed itself, kept alongside an analyst override so the UI can
    # say what Fondok would have picked.
    derived_year: Annotated[int, Field(ge=1)] | None = None
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
    (or absent) falls back to the derived signal; equal to the derived signal
    it stays ``fondok_derived`` — re-confirming a seed is not an override.
    """
    n = len(total_revenue_by_year)
    derived_index, signal = resolve_stabilized_year(
        occupancy_by_year=occupancy_by_year,
        stabilized_occupancy=stabilized_occupancy,
        noi_by_year=cash_noi_by_year,
    )

    analyst_index: int | None = None
    if stabilization_year is not None:
        try:
            analyst_index = int(stabilization_year) - 1
        except (TypeError, ValueError):
            analyst_index = None
        if analyst_index is not None and not (0 <= analyst_index < n):
            analyst_index = None

    if analyst_index is not None:
        index = analyst_index
        source: StabilizationSource = (
            "fondok_derived" if index == derived_index else "analyst_override"
        )
    elif derived_index is not None and 0 <= derived_index < n:
        index = derived_index
        source = "fondok_derived"
    else:
        return None

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
        signal=signal,
        derived_year=(derived_index + 1) if derived_index is not None else None,
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
    "StabilizationSignal",
    "StabilizationSource",
    "StabilizedYear",
    "build_stabilized_year",
    "resolve_stabilized_year",
    "resolve_stabilized_year_index",
]
