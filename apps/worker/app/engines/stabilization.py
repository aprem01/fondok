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

FON-82 (Sam, 2026-11-11). The headline **Stabilized NOI** — and the yield on
cost built on it — is the stabilized year's Cash NOI **after** the FF&E
reserve (``stabilized_cash_noi``), the same basis as the exit NOI and how a
lender reads it. ``stabilized_noi_before_reserve`` / ``stabilized_noi_margin``
stay on the block, byte-identical, for the entry-cap basis and older readers;
``stabilized_cash_noi_margin`` is the headline margin.

FON-98 (Rani + Eshan, 2026-10). The anchor is resolved, not a constant:

* **No reopening date** — Year 3 counted from the **acquisition close**
  (projection Year 1 is the first operating year after close, so "Year 3
  after close" is projection index 2). Source ``default_year_3``.
* **A material repositioning / re-flag** — the deal carries
  ``deals.reopening_date``; Year 3 is counted from the **first full deal
  year after reopening** (the first deal year — twelve months from the close,
  FON-121 ``engines.deal_years`` — that starts on or after the reopening
  date). Source
  ``default_year_3_reopening``, anchor ``reopening``. A reopening that cannot
  be placed (no acquisition close date to derive the projection calendar, or a
  reopening whose Year 3 falls before the projection starts) falls back to the
  close anchor and SAYS SO (``anchor_fallback_reason``) — it never guesses.

The analyst's ``stabilization_year`` wins over either anchor, exactly as
before. Goldens carry no reopening date, so they are byte-identical.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .timeline import parse_iso_date

# FON-59 R-057 — the default stabilized year (1-based) and what it counts
# from when the deal has no reopening date (FON-98).
DEFAULT_STABILIZATION_YEAR = 3
DEFAULT_STABILIZATION_ANCHOR = "acquisition_close"
REOPENING_STABILIZATION_ANCHOR = "reopening"

# How the model-detected hint was found.
StabilizationSignal = Literal["occupancy", "noi_plateau"]
# Who owns the year on the block.
StabilizationSource = Literal[
    "default_year_3", "default_year_3_reopening", "analyst_override"
]
StabilizationAnchor = Literal["acquisition_close", "reopening"]
# FON-98 — why a reopening date the deal DOES carry was not used as the anchor.
StabilizationAnchorFallback = Literal[
    "no_close_date",  # no acquisition close → no projection calendar
    "reopening_before_projection",  # Year 3 after reopening predates Year 1
]


def _deal_years(acquisition_close_date: object, n: int) -> list:
    """The projection's deal years (FON-121, ``engines.deal_years`` — twelve
    months from the close; the revenue engine's calendar). Imported lazily so
    this module stays free of the engine imports."""
    from .deal_years import deal_years

    if isinstance(acquisition_close_date, date):
        return deal_years(acquisition_close_date, n)
    return deal_years(acquisition_close_date if isinstance(acquisition_close_date, str) else None, n)


def reopening_stabilized_index(acquisition_close_date: object, reopening: date) -> int | None:
    """0-based deal-year index of "Year 3 after reopening" — counted from the
    first FULL deal year after ``reopening`` (the first deal year starting on
    or after it; that year is Year 1) — NOT clamped to the hold. None without
    a close date."""
    from .deal_years import first_full_deal_year_index

    first = first_full_deal_year_index(_deal_years(acquisition_close_date, 1), reopening)
    return None if first is None else first + DEFAULT_STABILIZATION_YEAR - 1


def reopening_anchor_index(
    *,
    acquisition_close_date: object,
    reopening_date: object,
    n_years: int,
) -> tuple[int | None, StabilizationAnchorFallback | None]:
    """0-based projection index of "Year 3 after reopening", or why not.

    ``(index, None)`` when the reopening places on the projection — the index
    is clamped to the last projected year (the caller reports ``clamped``).
    ``(None, reason)`` when the deal carries a reopening date that cannot be
    used; ``(None, None)`` when there is no reopening date at all.
    """
    reopening = parse_iso_date(reopening_date)
    if reopening is None or n_years <= 0:
        return None, None
    index = reopening_stabilized_index(acquisition_close_date, reopening)
    if index is None:
        return None, "no_close_date"
    if index < 0:
        return None, "reopening_before_projection"
    return min(index, n_years - 1), None


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
    acquisition_close_date: str | None = None,
    reopening_date: str | None = None,
) -> int | None:
    """0-based index of THE stabilized year — the debt engine's entry point.

    FON-59 R-057: the analyst's ``stabilization_year`` when it names a
    projected year, else the Year-3 default clamped to the last projected
    year — counted from the reopening date when the deal has one that places
    on the projection (FON-98), else from the acquisition close.
    ``occupancy_by_year`` / ``stabilized_occupancy`` are kept in the signature
    so every caller stays source-compatible; they feed only the detected-year
    hint (:func:`resolve_stabilized_year`), never this index. ``None`` only
    when ``noi_by_year`` is empty.
    """
    del occupancy_by_year, stabilized_occupancy  # hint inputs, not selectors
    n = len(noi_by_year)
    analyst = _analyst_index(stabilization_year, n)
    if analyst is not None:
        return analyst
    reopening_index, _reason = reopening_anchor_index(
        acquisition_close_date=acquisition_close_date,
        reopening_date=reopening_date,
        n_years=n,
    )
    if reopening_index is not None:
        return reopening_index
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
    # Who owns the year. ``default_year_3`` / ``default_year_3_reopening``
    # cover both "nobody set one" and "the analyst re-saved the default
    # unchanged".
    source: StabilizationSource
    # The rule the default follows: ``default_year`` years after ``anchor``.
    # FON-98: ``reopening`` when the deal's reopening date placed on the
    # projection; ``acquisition_close`` otherwise.
    anchor: StabilizationAnchor = DEFAULT_STABILIZATION_ANCHOR
    default_year: Annotated[int, Field(ge=1)] = DEFAULT_STABILIZATION_YEAR
    # True when the default was pulled back to the last projected year because
    # the hold ends before ``default_year`` after the anchor (default only).
    clamped: bool = False
    # FON-98 — the deal's reopening / re-flag date (ISO), echoed so the UI can
    # say "Year 3 after reopening (Mar 2027)"; None when the deal has none.
    reopening_date: str | None = None
    # FON-98 — set ONLY when the deal carries a reopening date that could not
    # anchor the default (so ``anchor`` fell back to the close). Never a guess.
    anchor_fallback_reason: StabilizationAnchorFallback | None = None
    # The model-detected hint (occupancy reaches its stabilized assumption,
    # else the NOI plateau) — 1-based, shown beneath the year, never selects it.
    detected_year: Annotated[int, Field(ge=1)] | None = None
    detected_signal: StabilizationSignal | None = None
    stabilized_occupancy: float | None = None
    stabilized_adr: float | None = None
    stabilized_revenue: float | None = None
    # NOI BEFORE the FF&E reserve (``expense.years[].noi_institutional``) —
    # after property tax and insurance, before the reserve; the entry-cap
    # numerator. Kept on the block so nothing downstream loses it, but it is
    # NOT the headline "Stabilized NOI" (FON-82). See apps/web/src/lib/engines/noi.ts.
    stabilized_noi_before_reserve: float | None = None
    # Cash NOI (AFTER the FF&E reserve) of the same year. FON-82 (Sam,
    # 2026-11-11): this is THE "Stabilized NOI" every surface prints and the
    # yield-on-cost numerator — the same basis as the exit NOI and the way a
    # lender reads it.
    stabilized_cash_noi: float | None = None
    # Margin on the BEFORE-reserve NOI (``stabilized_noi_before_reserve`` ÷
    # ``stabilized_revenue``). Kept byte-identical for older readers; not the
    # headline margin since FON-82.
    stabilized_noi_margin: float | None = None
    # FON-82 — margin on the headline basis: ``stabilized_cash_noi`` ÷
    # ``stabilized_revenue``, the same year index as everything else here.
    stabilized_cash_noi_margin: float | None = None


def build_stabilized_year(
    *,
    total_revenue_by_year: list[float],
    noi_before_reserve_by_year: list[float | None],
    cash_noi_by_year: list[float],
    occupancy_by_year: list[float] | None = None,
    adr_by_year: list[float] | None = None,
    stabilized_occupancy: float | None = None,
    stabilization_year: int | None = None,
    acquisition_close_date: str | None = None,
    reopening_date: str | None = None,
) -> StabilizedYear | None:
    """Assemble the block for one projection, or ``None`` when no year resolves.

    ``stabilization_year`` is the analyst's 1-based model year. Out of range
    (or absent) falls back to the Year-3 default (clamped to the hold); equal
    to the default it stays a default source — re-confirming is not an
    override. FON-98: with ``reopening_date`` (and the ``acquisition_close_date``
    that places it on the projection calendar) the default counts Year 3 from
    the first full operating year after reopening. Both default to None, so a
    caller that omits them gets the Year-3-after-close block byte-identical to
    before.
    """
    n = len(total_revenue_by_year)
    detected_index, detected_signal = resolve_stabilized_year(
        occupancy_by_year=occupancy_by_year,
        stabilized_occupancy=stabilized_occupancy,
        noi_by_year=cash_noi_by_year,
    )
    close_default_index = default_stabilized_year_index(n)
    if close_default_index is None:
        return None

    # FON-98 — which anchor the default counts from.
    reopening_index, fallback_reason = reopening_anchor_index(
        acquisition_close_date=acquisition_close_date,
        reopening_date=reopening_date,
        n_years=n,
    )
    reopening = parse_iso_date(reopening_date)
    if reopening_index is not None and reopening is not None:
        default_index = reopening_index
        anchor: StabilizationAnchor = REOPENING_STABILIZATION_ANCHOR
        default_source: StabilizationSource = "default_year_3_reopening"
        # Clamped when Year 3 after reopening lands past the last projected
        # year — the hold ends before the asset stabilizes on this rule.
        default_clamped = (
            reopening_stabilized_index(acquisition_close_date, reopening) or 0
        ) > default_index
    else:
        default_index = close_default_index
        anchor = DEFAULT_STABILIZATION_ANCHOR
        default_source = "default_year_3"
        default_clamped = default_index < DEFAULT_STABILIZATION_YEAR - 1

    analyst_index = _analyst_index(stabilization_year, n)
    if analyst_index is not None and analyst_index != default_index:
        index = analyst_index
        source: StabilizationSource = "analyst_override"
    else:
        index = default_index
        source = default_source
    clamped = source != "analyst_override" and default_clamped

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
    cash_noi = cash_noi_by_year[index] if index < len(cash_noi_by_year) else None
    # FON-82 — the headline margin is on the after-reserve basis, same index.
    cash_margin = (
        (cash_noi / revenue)
        if (cash_noi is not None and revenue and revenue > 0)
        else None
    )
    return StabilizedYear(
        stabilized_year_index=index,
        stabilized_year=index + 1,
        source=source,
        anchor=anchor,
        clamped=clamped,
        reopening_date=reopening.isoformat() if reopening else None,
        anchor_fallback_reason=fallback_reason,
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
        stabilized_cash_noi=cash_noi,
        stabilized_noi_margin=margin,
        stabilized_cash_noi_margin=cash_margin,
    )


__all__ = [
    "DEFAULT_STABILIZATION_ANCHOR",
    "DEFAULT_STABILIZATION_YEAR",
    "REOPENING_STABILIZATION_ANCHOR",
    "StabilizationAnchor",
    "StabilizationAnchorFallback",
    "reopening_stabilized_index",
    "reopening_anchor_index",
    "StabilizationSignal",
    "StabilizationSource",
    "StabilizedYear",
    "build_stabilized_year",
    "default_stabilized_year_index",
    "resolve_stabilized_year",
    "resolve_stabilized_year_index",
]
