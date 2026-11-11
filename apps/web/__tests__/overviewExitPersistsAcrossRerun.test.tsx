/**
 * FON-41 — the Exit section survives the re-run an occupancy change triggers.
 *
 * Sam saw Hold Period / Exit Cap / Gross Exit Value clear once after editing
 * occupancy. The regression pass/fail on the ticket: those rows keep the
 * last run's figures WHILE the model re-runs and swap to the new run's
 * figures when it lands — never a dash in between.
 *
 * Unlike overviewTab.test.tsx this drives the REAL `useEngineOutputs`
 * (the deal-wide snapshot + pubsub) and the REAL `useEngineRun` (run-all
 * kickoff + poll) against a mocked worker, because the persistence contract
 * lives in those hooks: `refresh()` only swaps the snapshot on a successful
 * read, and a run in flight never resets it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse, TimelineResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams('tab=overview'),
  usePathname: () => '/projects/deal-uuid-1',
}));

const REFUSAL_MESSAGE =
  'The deal has no Year-1 occupancy basis — no T-12, no STR or market benchmark and no analyst value sets it. Upload a T-12 or STR report, or enter Year-1 occupancy and ADR on the Future P&L, and run the model again — the model never substitutes a placeholder rate.';

const engineRow = (engine: string, runId: string, outputs: Record<string, unknown>) => ({
  deal_id: 'deal-uuid-1', engine, status: 'complete', summary: '', outputs,
  inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: runId,
});

/** The snapshot on screen before the edit: a 5-year hold at a 7.00% exit cap. */
const RUN_A = {
  deal_id: 'deal-uuid-1',
  engines: {
    capital: engineRow('capital', 'run-1', { purchase_price: 34_000_000, uses: [], sources: [] }),
    returns: engineRow('returns', 'run-1', {
      gross_sale_price: 52_000_000, exit_cap_rate: 0.07, terminal_noi: 3_640_000,
      selling_costs: 520_000, hold_years: 5, levered_irr: 0.198, unlevered_irr: 0.142,
    }),
    expense: engineRow('expense', 'run-1', { years: [{ year: 1, noi: 2_550_000, noi_institutional: 3_100_000 }] }),
  },
  // The worker's refusal that puts the Year-1 occupancy entry on the Overview —
  // the occupancy-change path under test.
  failures: [{ engine: 'revenue', reason: 'no_revenue_basis', message: REFUSAL_MESSAGE, missing: ['starting_occupancy'] }],
} as unknown as EngineOutputsResponse;

/** The snapshot the re-run produces: a 7-year hold at a 6.50% exit cap. */
const RUN_B = {
  deal_id: 'deal-uuid-1',
  engines: {
    capital: engineRow('capital', 'run-2', { purchase_price: 34_000_000, uses: [], sources: [] }),
    returns: engineRow('returns', 'run-2', {
      gross_sale_price: 58_000_000, exit_cap_rate: 0.065, terminal_noi: 3_770_000,
      selling_costs: 580_000, hold_years: 7, levered_irr: 0.21, unlevered_irr: 0.15,
    }),
    expense: engineRow('expense', 'run-2', { years: [{ year: 1, noi: 2_600_000, noi_institutional: 3_150_000 }] }),
  },
  failures: [],
} as unknown as EngineOutputsResponse;

const TIMELINE = {
  deal_id: 'deal-uuid-1', close_date: '2027-03-31', exit_date: '2032-03-31', stabilization_date: null, events: [],
} as unknown as TimelineResponse;

// The mocked worker: `getAll` serves RUN_A until the run-all has finished,
// then RUN_B. The run stays `running` on every poll until `finishRun()`.
let runDone = false;
// When set, every snapshot read fails (a worker blip after the run lands).
let failRefresh = false;
const getAllSpy = vi.fn(async () => {
  if (failRefresh) throw new Error('worker unreachable');
  return runDone ? RUN_B : RUN_A;
});
const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
const runAllSpy = vi.fn(async () => ({
  deal_id: 'deal-uuid-1', run_id: 'run-2', started_at: '2026-11-10T00:00:00Z',
  engines: [{ name: 'returns', status: 'queued' }],
}));
const pollRow = (status: string) => ({
  deal_id: 'deal-uuid-1', engine: 'returns', status, summary: '', outputs: null, inputs: null,
  error: null, runtime_ms: null, started_at: null, completed_at: null, run_id: 'run-2',
});
const getRunSpy = vi.fn(async () => ({
  deal_id: 'deal-uuid-1', run_id: 'run-2', engines: [pollRow(runDone ? 'complete' : 'running')], failures: [],
}));
const finishRun = () => { runDone = true; };

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
      engines: {
        ...actual.api.engines,
        getAll: (...a: unknown[]) => getAllSpy(...(a as [])),
        runAll: (...a: unknown[]) => runAllSpy(...(a as [])),
        getRun: (...a: unknown[]) => getRunSpy(...(a as [])),
        timeline: async () => TIMELINE,
      },
      market: { ...actual.api.market, overview: async () => ({}) },
      reference: { strMarkets: async () => [] },
      analysis: {
        ...actual.api.analysis,
        pricing: { ...actual.api.analysis.pricing, askingVsBid: () => new Promise(() => {}) },
      },
    },
  };
});

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: {
      id: 'deal-uuid-1', keys: 132, deal_type: 'acquisition', return_profile: 'value-add',
      positioning: 'default', brand: 'Kimpton Hotels & Restaurants', field_overrides: {},
    },
    status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import OverviewTab from '@/components/project/OverviewTab';

/** The Overview row element for a label (label span → left span → row div), outside any popover. */
function rowFor(label: string): HTMLElement | null {
  const span = screen.queryAllByText(label).find((el) => !el.closest('[role="dialog"]'));
  return span ? span.parentElement!.parentElement! : null;
}
/** The value cell text of a row, or null when the row is not on screen. */
function rowValue(label: string): string | null {
  const row = rowFor(label);
  if (!row) return null;
  const right = row.lastElementChild as HTMLElement;
  return (right.lastElementChild as HTMLElement).textContent ?? '';
}

const EXIT_ROWS = ['Hold Period', 'Exit Cap Rate', 'Gross Exit Value'] as const;

let observer: MutationObserver | null = null;

beforeEach(() => {
  cleanup();
  runDone = false;
  failRefresh = false;
  getAllSpy.mockClear();
  updateSpy.mockClear();
  runAllSpy.mockClear();
  getRunSpy.mockClear();
});
afterEach(() => {
  observer?.disconnect();
  observer = null;
  cleanup();
});

describe('OverviewTab — Exit section persists across the occupancy re-run (FON-41)', () => {
  it('keeps Hold Period / Exit Cap Rate / Gross Exit Value on screen while the model re-runs, then shows the new run', async () => {
    render(<OverviewTab projectId="deal-uuid-1" />);
    await waitFor(() => expect(rowValue('Hold Period')).toBe('5 years'));
    expect(rowValue('Exit Cap Rate')).toBe('7.00%');
    expect(rowValue('Gross Exit Value')).toContain('52,000,000');

    // Record every value each Exit row passes through from here on — a dash
    // at ANY point between the edit and the new run is the failure Sam saw.
    const seen: Record<string, Set<string>> = Object.fromEntries(EXIT_ROWS.map((l) => [l, new Set<string>()]));
    const snapshot = () => {
      for (const label of EXIT_ROWS) {
        const v = rowValue(label);
        seen[label].add(v == null ? '(row missing)' : v);
      }
    };
    snapshot();
    observer = new MutationObserver(snapshot);
    observer.observe(document.body, { subtree: true, childList: true, characterData: true });

    // The occupancy change: the Year-1 entry the worker's refusal put on the
    // Overview, saved as a noted override → the debounced full re-run.
    fireEvent.change(screen.getByLabelText('Year-1 occupancy (%)'), { target: { value: '68' } });
    fireEvent.change(screen.getByLabelText('Year-1 occupancy and ADR — override justification'), { target: { value: 'Operator budget' } });
    fireEvent.click(screen.getByTestId('revenue-basis-save'));
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    expect(updateSpy).toHaveBeenCalledWith('deal-uuid-1', {
      field_overrides: { starting_occupancy: { value: 0.68, note: 'Operator budget' } },
    });

    // The run kicks off (1.4s debounce) and the poll sees it in flight.
    await waitFor(() => expect(runAllSpy).toHaveBeenCalledTimes(1), { timeout: 4000 });
    await waitFor(() => expect(getRunSpy).toHaveBeenCalled(), { timeout: 3000 });

    // DURING the run: the last run's figures are still on screen.
    expect(rowValue('Hold Period')).toBe('5 years');
    expect(rowValue('Exit Cap Rate')).toBe('7.00%');
    expect(rowValue('Gross Exit Value')).toContain('52,000,000');

    // AFTER the run: the new run's figures replace them.
    finishRun();
    await waitFor(() => expect(rowValue('Hold Period')).toBe('7 years'), { timeout: 4000 });
    expect(rowValue('Exit Cap Rate')).toBe('6.50%');
    expect(rowValue('Gross Exit Value')).toContain('58,000,000');
    snapshot();

    // Nothing in between was ever a dash or a missing row.
    expect([...seen['Hold Period']].sort()).toEqual(['5 years', '7 years']);
    expect([...seen['Exit Cap Rate']].sort()).toEqual(['6.50%', '7.00%']);
    for (const label of EXIT_ROWS) {
      expect(seen[label].has('—')).toBe(false);
      expect(seen[label].has('')).toBe(false);
      expect(seen[label].has('(row missing)')).toBe(false);
    }
  });

  it('a failed refresh after the run keeps the last run on screen rather than clearing it', async () => {
    render(<OverviewTab projectId="deal-uuid-1" />);
    await waitFor(() => expect(rowValue('Hold Period')).toBe('5 years'));

    // The run completes but the snapshot read that follows fails (worker
    // blip): `useEngineOutputs` keeps the last good snapshot — never a dash.
    fireEvent.change(screen.getByLabelText('Year-1 occupancy (%)'), { target: { value: '70' } });
    fireEvent.change(screen.getByLabelText('Year-1 occupancy and ADR — override justification'), { target: { value: 'STR' } });
    fireEvent.click(screen.getByTestId('revenue-basis-save'));
    await waitFor(() => expect(runAllSpy).toHaveBeenCalledTimes(1), { timeout: 4000 });
    const before = getAllSpy.mock.calls.length;
    finishRun();
    failRefresh = true;
    // The post-run refresh fires (and fails) once the poll sees the run complete.
    await waitFor(() => expect(getAllSpy.mock.calls.length).toBeGreaterThan(before), { timeout: 4000 });
    await waitFor(() => expect(getAllSpy.mock.results.at(-1)?.type).toBe('return'));
    await getAllSpy.mock.results.at(-1)!.value.catch(() => undefined);

    expect(rowValue('Hold Period')).toBe('5 years');
    expect(rowValue('Exit Cap Rate')).toBe('7.00%');
    expect(rowValue('Gross Exit Value')).toContain('52,000,000');
  });
});
