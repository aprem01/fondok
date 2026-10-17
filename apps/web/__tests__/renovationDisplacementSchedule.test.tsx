/**
 * Renovation displacement schedule editor — Sam, 2026-10-15.
 *
 *   • `parseRenovationSchedule` reads the saved override in both shapes
 *     (bare list, `{value, note}` envelope) and ignores junk.
 *   • `draftsToPeriods` validates the way the worker will (whole months 1–240,
 *     last ≥ first, shares 0–100%) and converts percents to fractions.
 *   • The editor requires a justification (the key is an engine input), adds /
 *     removes rows, and calls `onSave(periods, note)`; removing every saved
 *     row calls `onSave(null, '')` — a revert, no note needed.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';

import RenovationDisplacementSchedule, {
  RENOVATION_SCHEDULE_KEY,
  draftsToPeriods,
  parseRenovationSchedule,
} from '@/components/project/RenovationDisplacementSchedule';
import { requiresNote } from '@/lib/overrideNote';

afterEach(cleanup);

describe('renovation displacement schedule — helpers', () => {
  it('writes the key the worker routes (renovation_displacement_schedule) and that key needs a note', () => {
    expect(RENOVATION_SCHEDULE_KEY).toBe('renovation_displacement_schedule');
    expect(requiresNote(RENOVATION_SCHEDULE_KEY)).toBe(true);
  });

  it('parses the saved override in both shapes and drops junk', () => {
    const list = [{ start_month: 1, end_month: 6, rooms_offline_pct: 0.25, rate_discount_pct: 0.1, note: 'A' }];
    expect(parseRenovationSchedule(list)).toEqual(list);
    expect(parseRenovationSchedule({ value: list, note: 'why' })).toEqual(list);
    expect(parseRenovationSchedule(undefined)).toEqual([]);
    expect(parseRenovationSchedule('nope')).toEqual([]);
    expect(parseRenovationSchedule([{ start_month: 'x' }, 7, null])).toEqual([]);
    // Missing shares default to 0, never to an invented figure.
    expect(parseRenovationSchedule([{ start_month: 2, end_month: 3 }])).toEqual([
      { start_month: 2, end_month: 3, rooms_offline_pct: 0, rate_discount_pct: 0, note: undefined },
    ]);
  });

  it('validates drafts like the worker schema and converts percents to fractions', () => {
    const ok = draftsToPeriods([
      { start_month: '1', end_month: '6', rooms_offline_pct: '25', rate_discount_pct: '10', note: ' Tower A ' },
      { start_month: '13', end_month: '13', rooms_offline_pct: '', rate_discount_pct: '', note: '' },
    ]);
    expect(ok.error).toBeNull();
    expect(ok.periods).toEqual([
      { start_month: 1, end_month: 6, rooms_offline_pct: 0.25, rate_discount_pct: 0.1, note: 'Tower A' },
      { start_month: 13, end_month: 13, rooms_offline_pct: 0, rate_discount_pct: 0 },
    ]);
    expect(draftsToPeriods([{ start_month: '0', end_month: '6', rooms_offline_pct: '', rate_discount_pct: '', note: '' }]).error)
      .toMatch(/first month/);
    expect(draftsToPeriods([{ start_month: '6', end_month: '1', rooms_offline_pct: '', rate_discount_pct: '', note: '' }]).error)
      .toMatch(/last month/);
    expect(draftsToPeriods([{ start_month: '1.5', end_month: '6', rooms_offline_pct: '', rate_discount_pct: '', note: '' }]).error)
      .toMatch(/whole number/);
    expect(draftsToPeriods([{ start_month: '1', end_month: '6', rooms_offline_pct: '150', rate_discount_pct: '', note: '' }]).error)
      .toMatch(/rooms offline/);
    expect(draftsToPeriods([{ start_month: '1', end_month: '6', rooms_offline_pct: '', rate_discount_pct: '-1', note: '' }]).error)
      .toMatch(/rate discount/);
  });
});

describe('renovation displacement schedule — editor', () => {
  it('adds and removes rows, refuses a save without a note, then saves periods with the note', async () => {
    const onSave = vi.fn(async () => {});
    render(<RenovationDisplacementSchedule periods={[]} editable onSave={onSave} />);
    expect(screen.getByTestId('renovation-displacement-schedule').getAttribute('data-schedule-state')).toBe('off');
    fireEvent.click(screen.getByTestId('renovation-schedule-edit'));
    fireEvent.click(screen.getByTestId('renovation-schedule-add'));
    expect(screen.getAllByTestId('renovation-schedule-draft-row')).toHaveLength(2);
    fireEvent.click(screen.getByLabelText('Remove period 2'));
    expect(screen.getAllByTestId('renovation-schedule-draft-row')).toHaveLength(1);

    fireEvent.change(screen.getByLabelText('Period 1 first month'), { target: { value: '13' } });
    fireEvent.change(screen.getByLabelText('Period 1 last month'), { target: { value: '18' } });
    fireEvent.change(screen.getByLabelText('Period 1 rooms offline %'), { target: { value: '50' } });
    fireEvent.click(screen.getByLabelText('Save schedule'));
    expect((await screen.findByTestId('renovation-schedule-error')).textContent).toMatch(/note/i);
    expect(onSave).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Schedule justification'), { target: { value: 'Tower B phase' } });
    fireEvent.click(screen.getByLabelText('Save schedule'));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith(
      [{ start_month: 13, end_month: 18, rooms_offline_pct: 0.5, rate_discount_pct: 0 }],
      'Tower B phase',
    );
  });

  it('shows an invalid period as an error instead of saving it', async () => {
    const onSave = vi.fn(async () => {});
    render(<RenovationDisplacementSchedule periods={[]} editable onSave={onSave} />);
    fireEvent.click(screen.getByTestId('renovation-schedule-edit'));
    fireEvent.change(screen.getByLabelText('Period 1 first month'), { target: { value: '6' } });
    fireEvent.change(screen.getByLabelText('Period 1 last month'), { target: { value: '1' } });
    fireEvent.change(screen.getByLabelText('Schedule justification'), { target: { value: 'x' } });
    fireEvent.click(screen.getByLabelText('Save schedule'));
    expect((await screen.findByTestId('renovation-schedule-error')).textContent).toMatch(/last month/);
    expect(onSave).not.toHaveBeenCalled();
  });

  it('removing every saved period clears the schedule with no note (a revert)', async () => {
    const onSave = vi.fn(async () => {});
    render(
      <RenovationDisplacementSchedule
        periods={[{ start_month: 1, end_month: 6, rooms_offline_pct: 0.25, rate_discount_pct: 0.1 }]}
        savedNote="GC plan"
        byYear={[{ year: 1, entries: [0], months_in_period: 6, rooms_offline_share: 0.125, occupancy_factor: 0.875, adr_factor: 0.9571 }]}
        editable
        onSave={onSave}
      />,
    );
    expect(screen.getByTestId('renovation-schedule-by-year').textContent).toContain('6 of 12 months');
    fireEvent.click(screen.getByTestId('renovation-schedule-edit'));
    fireEvent.click(screen.getByLabelText('Remove period 1'));
    expect(screen.getByLabelText('Save schedule').textContent).toBe('Clear schedule');
    fireEvent.click(screen.getByLabelText('Save schedule'));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith(null, ''));
  });

  it('is read-only on a demo deal — no edit affordance', () => {
    render(<RenovationDisplacementSchedule periods={[]} editable={false} onSave={vi.fn()} />);
    expect(screen.queryByTestId('renovation-schedule-edit')).toBeNull();
  });
});
