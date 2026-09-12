"""Tests for the timeline engine (FON-71)."""

from __future__ import annotations

from datetime import date

from app.engines.timeline import build_timeline, parse_iso_date


def _by_event(events):
    return {e.event: e for e in events}


def test_dates_project_from_close_date():
    events = build_timeline(
        close_date=date(2025, 9, 30),
        hold_years=5,
        term_years=5,
        renovation_budget=5_280_000,
    )
    by = _by_event(events)
    # Purchase is the close date (point event).
    assert by["Hotel Purchase"].start == "2025-09-30"
    assert by["Hotel Purchase"].duration_months == 0
    # 12-month renovation from close.
    assert by["Renovation"].start == "2025-09-30"
    assert by["Renovation"].finish == "2026-09-30"
    # Stabilization = reno end + 12 months (the FTM date).
    assert by["Stabilized (FTM NOI, Value)"].finish == "2027-09-30"
    # Maturity = close + term (5y); hold/exit = close + hold (5y).
    assert by["Senior Loan Maturity"].finish == "2030-09-30"
    assert by["Investment Hold Period"].duration_months == 60
    assert by["Disposition / Exit"].finish == "2030-09-30"


def test_pending_when_no_close_date():
    events = build_timeline(
        close_date=None,
        hold_years=5,
        term_years=5,
        renovation_budget=5_280_000,
    )
    # Durations still known; dates are None and basis is 'pending'.
    by = _by_event(events)
    assert by["Investment Hold Period"].duration_months == 60
    assert by["Investment Hold Period"].start is None
    assert all(e.basis == "pending" for e in events)


def test_no_renovation_omits_reno_rows():
    events = build_timeline(
        close_date=date(2025, 1, 1),
        hold_years=5,
        term_years=5,
        renovation_budget=0,
    )
    labels = {e.event for e in events}
    assert "Renovation" not in labels
    assert "Stabilized (FTM NOI, Value)" not in labels
    # Non-reno rows still present.
    assert "Hotel Purchase" in labels
    assert "Disposition / Exit" in labels


def test_interest_only_and_refi_rows_appear_only_when_set():
    with_io = build_timeline(
        close_date=date(2025, 1, 1),
        hold_years=5,
        term_years=5,
        interest_only_months=24,
        refi_month=30,
        renovation_budget=0,
    )
    by = _by_event(with_io)
    assert by["Senior Loan Interest-Only Period"].duration_months == 24
    assert by["Senior Loan Interest-Only Period"].finish == "2027-01-01"
    assert by["Senior Loan Refi"].finish == "2027-07-01"

    without = _by_event(
        build_timeline(close_date=date(2025, 1, 1), hold_years=5, renovation_budget=0)
    )
    assert "Senior Loan Interest-Only Period" not in without
    assert "Senior Loan Refi" not in without


def test_month_rollover_clamps_day():
    # Close on the 31st + 1 month lands on a 30-day month → clamp to the 30th.
    events = build_timeline(
        close_date=date(2025, 1, 31), hold_years=1, renovation_budget=5_000_000
    )
    reno = _by_event(events)["Renovation"]
    # 12 months from Jan 31 2025 → Jan 31 2026 (finish); start is Jan 31.
    assert reno.start == "2025-01-31"
    assert reno.finish == "2026-01-31"


def test_renovation_and_ramp_windows_flex_per_deal():
    # A 6-month reno starting 2 months after close, with an 18-month ramp.
    events = build_timeline(
        close_date=date(2025, 1, 1), hold_years=5, renovation_budget=5_000_000,
        renovation_start_offset_months=2, renovation_duration_months=6,
        ramp_months=18,
    )
    by = _by_event(events)
    assert by["Renovation"].start == "2025-03-01"       # close + 2 mo
    assert by["Renovation"].duration_months == 6
    assert by["Renovation"].finish == "2025-09-01"      # + 6 mo
    assert by["Ramp-Up Period"].duration_months == 18
    # Stabilization = reno end + 18 mo ramp → 2027-03-01
    assert by["Stabilized (FTM NOI, Value)"].finish == "2027-03-01"


def test_invalid_window_overrides_fall_back_to_defaults():
    events = build_timeline(
        close_date=date(2025, 1, 1), hold_years=5, renovation_budget=5_000_000,
        renovation_duration_months=-3, ramp_months=None,
    )
    by = _by_event(events)
    # Negative override ignored → 12-month default.
    assert by["Renovation"].duration_months == 12
    assert by["Ramp-Up Period"].duration_months == 12


def test_parse_iso_date_variants():
    assert parse_iso_date("2025-09-30") == date(2025, 9, 30)
    assert parse_iso_date("2025-09-30T00:00:00Z") == date(2025, 9, 30)
    assert parse_iso_date("") is None
    assert parse_iso_date(None) is None
    assert parse_iso_date("not-a-date") is None


def test_milestones_are_chronologically_ordered_with_durations():
    """FON-71 / Wave 0 — Investment + Overview render a milestone/duration
    timeline. Every milestone must carry a duration (point events are 0) and
    the dated rows must be non-decreasing in time (acquisition → renovation →
    stabilization → refi → maturity → exit)."""
    events = build_timeline(
        close_date=date(2025, 9, 30),
        hold_years=5,
        term_years=5,
        interest_only_months=24,
        refi_month=30,
        renovation_budget=5_280_000,
    )
    # Every milestone carries an explicit duration (0 for point events).
    assert all(e.duration_months is not None for e in events)
    # The key phases are all present.
    names = {e.event for e in events}
    assert "Hotel Purchase" in names
    assert "Renovation" in names
    assert "Stabilized (FTM NOI, Value)" in names
    assert "Senior Loan Refi" in names
    assert "Disposition / Exit" in names
    # Each dated milestone is internally chronological (finish >= start).
    for e in events:
        if e.start is not None and e.finish is not None:
            assert e.finish >= e.start, f"{e.event}: finish before start"
    # Acquisition opens the schedule; disposition closes it (last dated finish).
    assert events[0].event == "Hotel Purchase"
    finishes = [e.finish for e in events if e.finish is not None]
    exit_ev = next(e for e in events if e.event.startswith("Disposition"))
    assert exit_ev.finish == max(finishes)


# ── FON-44 §2 — Hotel Purchase is LINKED, not calculated ──────────────────
#
# Sam (9/11): "Hotel Purchase 6/1/2021 on Timeline is currently labeled
# Calculated. This milestone is consuming the editable Acquisition Date."
# It is: the event's date IS ``close_date``, unmodified. Everything else here
# is arithmetic on it and stays 'derived' — Sam named the Exit explicitly.


def test_hotel_purchase_is_linked_when_a_close_date_is_set():
    events = build_timeline(
        close_date=date(2025, 9, 30),
        hold_years=5,
        term_years=5,
        interest_only_months=24,
        refi_month=30,
        renovation_budget=5_280_000,
    )
    by = _by_event(events)
    assert by["Hotel Purchase"].basis == "linked"
    # The one editable-assumption milestone; the rest keep their own basis.
    assert by["Disposition / Exit"].basis == "derived"
    assert by["Investment Hold Period"].basis == "derived"
    assert by["Senior Loan Maturity"].basis == "derived"
    assert by["Senior Loan Refi"].basis == "derived"
    assert by["Senior Loan Interest-Only Period"].basis == "derived"
    # The renovation window stays the per-deal assumption it already was.
    assert by["Renovation"].basis == "assumption"
    # Exactly one linked row.
    assert [e.event for e in events if e.basis == "linked"] == ["Hotel Purchase"]


def test_hotel_purchase_is_pending_without_a_close_date():
    events = build_timeline(
        close_date=None,
        hold_years=5,
        term_years=5,
        renovation_budget=5_280_000,
    )
    by = _by_event(events)
    # Nothing is linked until there is a date to link to.
    assert by["Hotel Purchase"].basis == "pending"
    assert by["Hotel Purchase"].start is None
    assert all(e.basis == "pending" for e in events)
