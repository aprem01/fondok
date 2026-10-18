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
  // A duplicated / newly created deal: every run post-dates the default
  // cutoff, so the worker names no baseline.
  const FRESH_RUNS = {
    deal_id: 'deal-copy',
    canonical_run_id: 'fresh-head-0000',
    baseline_before: '2026-10-15T21:40:00+00:00',
    baseline_run_id: null,
    runs: [
      {
        run_id: 'fresh-head-0000', started_at: '2026-10-17T12:00:00+00:00',
        completed_at: '2026-10-17T12:00:30+00:00', engines: ['returns'],
        status: 'complete', is_full_chain: true, is_canonical: true, scenario: null,
      },
      {
        run_id: 'fresh-scen-0000', started_at: '2026-10-17T11:30:00+00:00',
        completed_at: '2026-10-17T11:30:30+00:00', engines: ['returns'],
        status: 'complete', is_full_chain: true, is_canonical: false,
        scenario: { id: 's1', name: 'Downside', is_base: false },
      },
      {
        run_id: 'fresh-prev-0000', started_at: '2026-10-17T11:00:00+00:00',
        completed_at: '2026-10-17T11:00:30+00:00', engines: ['returns'],
        status: 'complete', is_full_chain: true, is_canonical: false, scenario: null,
      },
      {
        run_id: 'fresh-partial-00', started_at: '2026-10-17T10:00:00+00:00',
        completed_at: '2026-10-17T10:00:10+00:00', engines: ['revenue'],
        status: 'complete', is_full_chain: false, is_canonical: false, scenario: null,
      },
    ],
  };
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
  // Per-test knobs: a deferred compare (resolved by the test) and the "fresh deal" runs.
  const knobs: { deferCompare: null | (() => void); fresh: boolean } = { deferCompare: null, fresh: false };
  return { runsSpy, compareSpy, xlsxSpy, RUNS, FRESH_RUNS, COMPARE, knobs };
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
          if (fx.knobs.fresh) return fx.FRESH_RUNS;
          return fx.RUNS(
            before && before.startsWith('2026-10-14') ? null : 'b5a08654-52e9-4738-addc-eaf4e1019aae',
          );
        },
        compare: async (
          dealId: string, base: string, head: string, opts: { all?: boolean; signal?: AbortSignal },
        ) => {
          fx.compareSpy(dealId, base, head, !!opts?.all);
          if (fx.knobs.deferCompare !== null) {
            // Resolve when the test says so, or reject like fetch when aborted.
            await new Promise<void>((resolve, reject) => {
              fx.knobs.deferCompare = resolve;
              opts?.signal?.addEventListener('abort', () => {
                const e = new Error('aborted');
                e.name = 'AbortError';
                reject(e);
              });
            });
          }
          return { ...fx.COMPARE, base: { ...fx.COMPARE.base, run_id: base } };
        },
        downloadCompareXlsx: async (...args: unknown[]) => {
          fx.xlsxSpy(...args);
        },
      },
    },
  };
});

import RunComparePanel from '@/components/project/RunComparePanel';
import { fmtRunTime } from '@/lib/runCompare';

describe('RunComparePanel', () => {
  beforeEach(() => {
    fx.runsSpy.mockClear();
    fx.compareSpy.mockClear();
    fx.xlsxSpy.mockClear();
    fx.knobs.deferCompare = null;
    fx.knobs.fresh = false;
  });
  afterEach(() => cleanup());

  // A duplicated deal (Sam's QA copy) or any deal created after the deploy the
  // default date names: the worker finds no run before the cutoff. The panel
  // falls back to the newest complete full-chain run that is neither the head
  // nor a non-Base scenario's latest run — and says so.
  it('falls back to the newest earlier complete run when nothing predates the default cutoff, and labels it', async () => {
    fx.knobs.fresh = true;
    render(<RunComparePanel dealId="deal-copy" />);
    await waitFor(() => expect(fx.compareSpy).toHaveBeenCalled());
    // Not the canonical head, not the Downside scenario's run, not the partial chain.
    expect(fx.compareSpy).toHaveBeenLastCalledWith('deal-copy', 'fresh-prev-0000', 'latest', false);
    expect((screen.getByTestId('run-compare-base') as HTMLSelectElement).value).toBe('fresh-prev-0000');
    const note = screen.getByTestId('run-compare-fallback-note');
    expect(note.textContent).toContain('No complete run finished before 2026-10-15 21:40 UTC');
    expect(note.textContent).toContain(fmtRunTime('2026-10-17T11:00:30+00:00'));
    expect(note.textContent).not.toContain('fresh-pr');
    expect(screen.queryByText(/no complete run before this date/)).toBeNull();
    // Moving the date is the analyst's call — no fallback then (worker still names none).
    fireEvent.change(screen.getByTestId('run-compare-cutoff'), { target: { value: '2026-10-17T10:30' } });
    await waitFor(() =>
      expect((screen.getByTestId('run-compare-base') as HTMLSelectElement).value).toBe(''),
    );
    expect(screen.queryByTestId('run-compare-fallback-note')).toBeNull();
    expect(screen.getByText(/no complete run before this date/)).toBeTruthy();
  });

  // Toggling "Show unchanged" aborts the in-flight compare and starts another.
  // The aborted request's cleanup must not clear the loading flag the new one
  // set, or the OLD pair shows for the duration with no "Comparing…" sign.
  it('keeps "Comparing…" up while a replacement request is in flight after an abort', async () => {
    render(<RunComparePanel dealId="deal-1" />);
    await screen.findByTestId('run-compare-table');
    expect(screen.getByTestId('run-compare-summary').textContent).toContain('2 of 120');

    // Next compares hang until the test releases them.
    fx.knobs.deferCompare = () => {};
    fireEvent.click(screen.getByLabelText('Show unchanged'));
    await waitFor(() => expect(fx.compareSpy).toHaveBeenLastCalledWith(
      'deal-1', 'b5a08654-52e9-4738-addc-eaf4e1019aae', 'latest', true,
    ));
    // Abort the pending one by toggling again — its finally must leave loading alone.
    fireEvent.click(screen.getByLabelText('Show unchanged'));
    await waitFor(() => expect(fx.compareSpy).toHaveBeenCalledTimes(3));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByTestId('run-compare-summary').textContent).toBe('Comparing…');

    const release = fx.knobs.deferCompare;
    fx.knobs.deferCompare = null;
    release?.();
    await waitFor(() =>
      expect(screen.getByTestId('run-compare-summary').textContent).toContain('2 of 120'),
    );
  });

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
