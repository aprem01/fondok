/**
 * Compare runs panel (Activity tab) — regression baselines, 2026-10-16.
 *
 * Locks: the panel asks the worker for the runs before the prefilled cutoff
 * (2026-10-15T21:40Z), selects the worker-named baseline, compares it with
 * "latest", renders the changed fields grouped by engine with deltas, shows
 * "—" plus the worker's reason when a % delta is undefined, re-picks the
 * baseline when the date changes, and exports through the worker's xlsx
 * route.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';

const fx = vi.hoisted(() => {
  const runsSpy = vi.fn();
  const compareSpy = vi.fn();
  const xlsxSpy = vi.fn();
  const RUNS = (baseline: string | null) => ({
    deal_id: 'deal-1',
    canonical_run_id: 'head-run-0000',
    baseline_before: '2026-10-15T21:40:00+00:00',
    baseline_run_id: baseline,
    runs: [
      {
        run_id: 'head-run-0000', started_at: '2026-10-16T10:00:00+00:00',
        completed_at: '2026-10-16T10:00:30+00:00', engines: ['returns'],
        status: 'complete', is_full_chain: true, is_canonical: true, scenario: null,
      },
      {
        run_id: 'b5a08654-52e9-4738-addc-eaf4e1019aae', started_at: '2026-10-15T21:00:00+00:00',
        completed_at: '2026-10-15T21:00:30+00:00', engines: ['returns'],
        status: 'complete', is_full_chain: true, is_canonical: false, scenario: null,
      },
    ],
  });
  const COMPARE = {
    deal_id: 'deal-1',
    base: { run_id: 'b5a08654-52e9-4738-addc-eaf4e1019aae', started_at: null, completed_at: null, engines: {} },
    head: { run_id: 'head-run-0000', started_at: null, completed_at: null, engines: {} },
    include_unchanged: false,
    fields_compared: 120,
    fields_changed: 2,
    tolerance: { rel: 1e-9, abs: 1e-6 },
    fields: [
      {
        key: 'revenue.years[0].occupancy', engine: 'revenue', group: 'Revenue',
        label: 'Year 1 · Occupancy', unit: 'pct', status: 'changed',
        base_value: 0.762, head_value: 0.7, delta_abs: -0.062, delta_pct: -0.08136,
        delta_reason: null,
        base_provenance: { state: 'calculated', formula: 'occupancy = starting_occupancy' },
        head_provenance: { state: 'calculated', formula: 'occupancy = starting_occupancy' },
      },
      {
        key: 'expense.years[0].incentive_mgmt_fee', engine: 'expense', group: 'Expenses & NOI',
        label: 'Year 1 · Incentive management fee', unit: 'usd', status: 'changed',
        base_value: 0, head_value: 12000, delta_abs: 12000, delta_pct: null,
        delta_reason: 'base is 0 — % change undefined',
        base_provenance: null, head_provenance: null,
      },
    ],
  };
  return { runsSpy, compareSpy, xlsxSpy, RUNS, COMPARE };
});

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      engines: {
        ...actual.api.engines,
        runs: async (dealId: string, before?: string) => {
          fx.runsSpy(dealId, before);
          return fx.RUNS(
            before && before.startsWith('2026-10-14') ? null : 'b5a08654-52e9-4738-addc-eaf4e1019aae',
          );
        },
        compare: async (dealId: string, base: string, head: string, opts: { all?: boolean }) => {
          fx.compareSpy(dealId, base, head, !!opts?.all);
          return fx.COMPARE;
        },
        downloadCompareXlsx: async (...args: unknown[]) => {
          fx.xlsxSpy(...args);
        },
      },
    },
  };
});

import RunComparePanel from '@/components/project/RunComparePanel';

describe('RunComparePanel', () => {
  beforeEach(() => {
    fx.runsSpy.mockClear();
    fx.compareSpy.mockClear();
    fx.xlsxSpy.mockClear();
  });
  afterEach(() => cleanup());

  it('defaults the baseline to the newest run before 2026-10-15T21:40Z and compares with latest', async () => {
    render(<RunComparePanel dealId="deal-1" />);
    await waitFor(() => expect(fx.compareSpy).toHaveBeenCalled());
    expect(fx.runsSpy).toHaveBeenCalledWith('deal-1', '2026-10-15T21:40:00Z');
    expect(fx.compareSpy).toHaveBeenLastCalledWith(
      'deal-1', 'b5a08654-52e9-4738-addc-eaf4e1019aae', 'latest', false,
    );
    const table = await screen.findByTestId('run-compare-table');
    expect(within(table).getByText('Revenue')).toBeTruthy();
    expect(within(table).getByText('Expenses & NOI')).toBeTruthy();
    const occ = screen.getByTestId('run-compare-row-revenue.years[0].occupancy');
    expect(occ.textContent).toContain('76.20%');
    expect(occ.textContent).toContain('70.00%');
    expect(occ.textContent).toContain('-6.20 pts');
    expect(occ.textContent).toContain('-8.14%');
    // Undefined % delta → dash plus the worker's reason, never a made-up number.
    const fee = screen.getByTestId('run-compare-row-expense.years[0].incentive_mgmt_fee');
    expect(fee.textContent).toContain('—');
    expect(fee.textContent).toContain('base is 0');
    expect(fee.textContent).toContain('engine records no provenance');
    expect(screen.getByTestId('run-compare-summary').textContent).toContain('2 of 120');
  });

  it('re-picks the baseline when the date changes and says when none exists', async () => {
    render(<RunComparePanel dealId="deal-1" />);
    await waitFor(() => expect(fx.compareSpy).toHaveBeenCalled());
    fireEvent.change(screen.getByTestId('run-compare-cutoff'), {
      target: { value: '2026-10-14T00:00' },
    });
    await waitFor(() =>
      expect(fx.runsSpy).toHaveBeenLastCalledWith('deal-1', '2026-10-14T00:00:00Z'),
    );
    await waitFor(() =>
      expect(
        (screen.getByTestId('run-compare-base') as HTMLSelectElement).value,
      ).toBe(''),
    );
    expect(screen.getByText(/no complete run before this date/)).toBeTruthy();
  });

  it('exports the comparison through the worker xlsx route', async () => {
    render(<RunComparePanel dealId="deal-1" />);
    await screen.findByTestId('run-compare-table');
    fireEvent.click(screen.getByTestId('run-compare-export'));
    await waitFor(() => expect(fx.xlsxSpy).toHaveBeenCalled());
    const [dealId, base, head, filename] = fx.xlsxSpy.mock.calls[0];
    expect(dealId).toBe('deal-1');
    expect(base).toBe('b5a08654-52e9-4738-addc-eaf4e1019aae');
    expect(head).toBe('head-run-0000');
    expect(String(filename)).toMatch(/\.xlsx$/);
  });
});
