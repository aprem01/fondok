"""Timeline engine — projects the transaction schedule onto calendar dates.

Given the acquisition close date and the timing already modeled elsewhere
(hold period from the returns engine, loan term / interest-only stub from the
debt engine, renovation window, refinance month), compute the dated
transaction timeline shown on the Investment tab.

Durations are known even without a close date; start/finish dates stay ``None``
("pending") until the analyst enters the acquisition date, so the tab shows the
schedule structure immediately and fills the dates once the close date lands.

The renovation window and ramp-to-stabilization are configurable defaults
(renovation starts at close and runs 12 months; stabilization is renovation-end
+ 12 months), consistent with the monthly cash-flow engine's deferred-capital
window — applied uniformly to every deal, not a per-deal fixture. Every other
date is derived from a real engine value (hold_years, term_years,
interest_only, refi_month); rows with no source are simply omitted rather than
shown with a fabricated date.
"""

from __future__ import annotations

import calendar
from datetime import date

from pydantic import BaseModel, ConfigDict

# ── Configurable schedule assumptions (months), applied uniformly ──────────
RENOVATION_START_OFFSET_MONTHS = 0
RENOVATION_DURATION_MONTHS = 12
RAMP_TO_STABILIZATION_MONTHS = 12


class TimelineEvent(BaseModel):
    """One row of the transaction timeline (START | DURATION | FINISH)."""

    model_config = ConfigDict(extra="forbid")

    event: str
    start: str | None = None  # ISO date (YYYY-MM-DD), None until close date set
    duration_months: int | None = None
    finish: str | None = None
    # 'derived' = from a real engine value; 'assumption' = default reno/ramp
    # window; 'pending' = awaiting the acquisition close date.
    basis: str = "derived"


def _add_months(d: date, months: int) -> date:
    """Add ``months`` to ``d``, clamping the day to the target month's length."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d is not None else None


def parse_iso_date(value: object) -> date | None:
    """Best-effort parse of an ISO ``YYYY-MM-DD`` (or full ISO) date string."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def build_timeline(
    *,
    close_date: date | None,
    hold_years: int | None,
    term_years: int | None = None,
    interest_only_months: int | None = None,
    refi_month: int | None = None,
    renovation_budget: float = 0.0,
) -> list[TimelineEvent]:
    """Assemble the dated transaction timeline from the deal's timing inputs."""
    events: list[TimelineEvent] = []
    pending = close_date is None
    dbasis = "pending" if pending else "derived"

    def at(offset_months: int | None) -> str | None:
        if close_date is None or offset_months is None:
            return None
        return _iso(_add_months(close_date, offset_months))

    # 1. Hotel Purchase — the acquisition close (point event).
    events.append(
        TimelineEvent(
            event="Hotel Purchase",
            start=_iso(close_date),
            duration_months=0,
            finish=_iso(close_date),
            basis=dbasis,
        )
    )

    # 2. Renovation window + ramp to stabilization (assumption) — only when
    #    there is an actual renovation budget.
    if renovation_budget and renovation_budget > 0:
        reno_start = RENOVATION_START_OFFSET_MONTHS
        reno_end = reno_start + RENOVATION_DURATION_MONTHS
        ramp_end = reno_end + RAMP_TO_STABILIZATION_MONTHS
        abasis = "pending" if pending else "assumption"
        events.append(
            TimelineEvent(
                event="Renovation",
                start=at(reno_start),
                duration_months=RENOVATION_DURATION_MONTHS,
                finish=at(reno_end),
                basis=abasis,
            )
        )
        events.append(
            TimelineEvent(
                event="Practical Completion (FTM NOI, Value)",
                start=at(reno_end),
                duration_months=0,
                finish=at(reno_end),
                basis=abasis,
            )
        )
        events.append(
            TimelineEvent(
                event="Ramp-Up Period",
                start=at(reno_end),
                duration_months=RAMP_TO_STABILIZATION_MONTHS,
                finish=at(ramp_end),
                basis=abasis,
            )
        )
        events.append(
            TimelineEvent(
                event="Stabilized (FTM NOI, Value)",
                start=at(ramp_end),
                duration_months=0,
                finish=at(ramp_end),
                basis=abasis,
            )
        )

    # 3. Senior loan interest-only stub (derived from the debt engine).
    if interest_only_months and interest_only_months > 0:
        events.append(
            TimelineEvent(
                event="Senior Loan Interest-Only Period",
                start=_iso(close_date),
                duration_months=interest_only_months,
                finish=at(interest_only_months),
                basis=dbasis,
            )
        )

    # 4. Senior loan maturity (derived from the loan term; point event).
    if term_years and term_years > 0:
        events.append(
            TimelineEvent(
                event="Senior Loan Maturity",
                start=at(term_years * 12),
                duration_months=0,
                finish=at(term_years * 12),
                basis=dbasis,
            )
        )

    # 5. Refinance (derived, only when a refi month is modeled; point event).
    if refi_month and refi_month > 0:
        events.append(
            TimelineEvent(
                event="Senior Loan Refi",
                start=at(refi_month),
                duration_months=0,
                finish=at(refi_month),
                basis=dbasis,
            )
        )

    # 6. Investment hold period + disposition (derived from hold_years).
    if hold_years and hold_years > 0:
        hold_months = hold_years * 12
        events.append(
            TimelineEvent(
                event="Investment Hold Period",
                start=_iso(close_date),
                duration_months=hold_months,
                finish=at(hold_months),
                basis=dbasis,
            )
        )
        events.append(
            TimelineEvent(
                event="Disposition / Exit",
                start=at(hold_months),
                duration_months=0,
                finish=at(hold_months),
                basis=dbasis,
            )
        )

    return events


__all__ = ["TimelineEvent", "build_timeline", "parse_iso_date"]
