/**
 * FON-109 — CBRE / HotStats benchmarks are suggestions the analyst applies.
 *
 * Sam (2026-10-15): benchmarks "are suggestions that require analyst approval
 * rather than automatically flowing into the model". The Future P&L's
 * Benchmark column (Rooms / F&B / Management Fees rows) and the Assumptions
 * panel (management fee, FF&E reserve) carry an "Apply benchmark" action: it
 * opens the justification prefilled with the worker's note, editable, and
 * Save writes the ``field_overrides`` entry with that note and re-runs the
 * model. An applied suggestion reads as an analyst override. No suggestion →
 * no action.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { BenchmarkSuggestion, EngineOutputsResponse } from '@/lib/api';

const HOLD_YEARS = 3;
const CALENDAR = [2025, 2026, 2027];
const TOTAL_REVENUE = 13_600_000;
const ROOMS_REVENUE = 10_000_000;

const revYear = (i: number) => ({
  year: i + 1, occupancy: 0.75, adr: 300, revpar: 225,
  rooms_revenue: ROOMS_REVENUE, fb_revenue: 3_000_000, other_revenue: 600_000, total_revenue: TOTAL_REVENUE,
});
const fbYear = (i: number) => ({
  year: i + 1, rooms_revenue: ROOMS_REVENUE, fb_revenue: 3_000_000, resort_fees: 0,
  other_revenue: 600_000, total_revenue: TOTAL_REVENUE,
});
const expYear = (i: number) => ({
  year: i + 1,
  total_revenue: TOTAL_REVENUE,
  dept_expenses: { rooms: 3_000_000, food_beverage: 1_800_000, other_operated: 200_000, total: 5_000_000 },
  undistributed: {
    administrative_general: 900_000, information_telecom: 180_000, sales_marketing: 800_000,
    property_operations: 760_000, utilities: 560_000, total: 3_200_000,
  },
  mgmt_fee: 408_000,
  ffe_reserve: 552_613,
  fixed_charges: { property_taxes: 700_000, insurance: 300_000, rent: 0, other_fixed: 0, total: 1_000_000 },
  gop: 5_400_000,
  noi: 1_448_443,
  noi_institutional: 2_001_056,
});

function buildOutputs(): EngineOutputsResponse {
  const stub = {
    deal_id: 'deal-uuid-1', status: 'complete', summary: '',
    inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
  };
  return {
    deal_id: 'deal-uuid-1',
    engines: {
      revenue: {
        ...stub, engine: 'revenue',
        outputs: {
          years: Array.from({ length: HOLD_YEARS }, (_, i) => revYear(i)),
          projection_start_year: CALENDAR[0],
          projection_calendar_years: CALENDAR,
        },
      },
      fb: { ...stub, engine: 'fb', outputs: { years: Array.from({ length: HOLD_YEARS }, (_, i) => fbYear(i)) } },
      expense: {
        ...stub, engine: 'expense',
        outputs: {
          years: Array.from({ length: HOLD_YEARS }, (_, i) => expYear(i)),
          stabilization: { stabilized_year: 3, stabilized_year_index: 2, source: 'fondok_signal' },
        },
      },
      returns: {
        ...stub, engine: 'returns',
        outputs: { hold_years: HOLD_YEARS, terminal_noi: 2_759_000, exit_cap_rate: 0.07, revpar_growth: 0.045 },
      },
      debt: { ...stub, engine: 'debt', outputs: { year_one_dscr: 1.59 } },
      capital: { ...stub, engine: 'capital', outputs: { purchase_price: 34_000_000, uses: [], sources: [] } },
    },
  } as unknown as EngineOutputsResponse;
}

const MARKET = {
  deal_id: 'deal-uuid-1',
  pnl_benchmark: {
    peer_set_size: 9,
    source_filename: 'CBRE-Trends-2025.pdf',
    categories: [
      { key: 'rooms', label: 'Rooms Expense', benchmark_line: 'rooms_dept_expense', ratio: 0.25,
        ratio_basis: 'department_revenue', ratio_source: 'computed_from_totals', par_usd: 12000, por_usd: 45.5 },
      { key: 'administrative_general', label: 'Administrative & General', benchmark_line: 'a_and_g',
        ratio: 0.08, ratio_basis: 'total_revenue', ratio_source: 'reported', par_usd: null, por_usd: null },
    ],
  },
  sources: { PNL_BENCHMARK: ['doc-1'] },
};

const suggestion = (over: Partial<BenchmarkSuggestion>): BenchmarkSuggestion => ({
  key: 'rooms_dept_pct',
  label: 'Rooms departmental expense (% of rooms revenue)',
  suggested_value: 0.25,
  benchmark_line: 'rooms_dept_expense',
  benchmark_line_label: 'Rooms Expense',
  ratio_source: 'computed_from_totals',
  field_name: 'pnl_benchmark.peer.rooms_dept_expense.total_usd',
  peer_set_size: 9,
  document_id: 'doc-1',
  extraction_result_id: 'er-1',
  filename: 'CBRE-Trends-2025.pdf',
  page: 12,
  current_value: 0.3,
  current_source: 'seed',
  current_note: null,
  applied: false,
  year1_from_t12: false,
  suggested_note: 'Applied CBRE benchmark Rooms Expense 25% from CBRE-Trends-2025.pdf p.12',
  ...over,
});

const SUGGESTIONS = {
  id: 'deal-uuid-1',
  peer_set_size: 9,
  suggestions: [
    suggestion({}),
    suggestion({
      key: 'mgmt_fee_pct', label: 'Management fee (% of total revenue)', suggested_value: 0.025,
      benchmark_line: 'mgmt_fee_pct', benchmark_line_label: 'Management Fees', ratio_source: 'legacy_summary',
      page: 9, current_value: 0.03,
      suggested_note: 'Applied CBRE benchmark Management Fees 2.5% from CBRE-Trends-2025.pdf p.9',
    }),
    suggestion({
      key: 'ffe_reserve_pct', label: 'FF&E reserve (% of total revenue)', suggested_value: 0.05,
      benchmark_line: 'ffe_reserve_pct', benchmark_line_label: 'FF&E Reserve', ratio_source: 'legacy_summary',
      page: 9, current_value: 0.04,
      suggested_note: 'Applied CBRE benchmark FF&E Reserve 5% from CBRE-Trends-2025.pdf p.9',
    }),
  ],
};

const fx = vi.hoisted(() => ({
  toast: vi.fn(),
  update: vi.fn(async (_id: string, _body: unknown) => ({ id: 'deal-uuid-1' })),
  run: vi.fn(async () => {}),
  market: vi.fn(async (_id: string, _signal?: AbortSignal): Promise<unknown> => null),
  suggestions: vi.fn(async (_id: string, _signal?: AbortSignal): Promise<unknown> => null),
}));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/projects/deal-uuid-1',
}));
vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/useEngineOutputs')>();
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: buildOutputs(), previous: null, loading: false, settled: true, lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});
// The deal's stored overrides — a test may add one (an applied FF&E benchmark)
// before rendering; `beforeEach` puts the default back.
const dealFx = vi.hoisted(() => ({
  overrides: { acquisition_close_date: { value: '2025-09-30', note: 'PSA' } } as Record<string, unknown>,
}));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: {
      id: 'deal-uuid-1', name: 'Kimpton Angler', keys: 132,
      field_overrides: dealFx.overrides,
    },
    status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: fx.run, running: false, status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: fx.toast }) }));
vi.mock('@/lib/api', async (importOriginal) => {
  const { withQuietWorker } = await import('./helpers/quietWorker');
  const actual = withQuietWorker(await importOriginal<typeof import('@/lib/api')>());
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: {
        ...actual.api.deals,
        update: fx.update,
        benchmarkSuggestions: (id: string, signal?: AbortSignal) => fx.suggestions(id, signal),
      },
      market: { ...actual.api.market, data: (id: string, signal?: AbortSignal) => fx.market(id, signal) },
    },
  };
});

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';

beforeEach(() => {
  cleanup();
  dealFx.overrides = { acquisition_close_date: { value: '2025-09-30', note: 'PSA' } };
  fx.update.mockReset();
  fx.update.mockImplementation(async () => ({ id: 'deal-uuid-1' }));
  fx.run.mockClear();
  fx.market.mockReset();
  fx.market.mockImplementation(async () => MARKET);
  fx.suggestions.mockReset();
  fx.suggestions.mockImplementation(async () => SUGGESTIONS);
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const roomsCell = () =>
  document.querySelector<HTMLElement>('[data-testid="benchmark-cell"][data-benchmark-key="rooms"]');

describe('FON-109 · Apply benchmark in the Future P&L Benchmark column', () => {
  it('offers the suggestion on the Rooms row, prefills an editable note, writes the override and re-runs', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const btn = await screen.findByTestId('apply-benchmark-rooms_dept_pct');
    expect(within(roomsCell()!).getByTestId('apply-benchmark-rooms_dept_pct')).toBe(btn);
    expect(btn.textContent).toBe('Apply benchmark 25%');
    // Undistributed rows carry no suggestion → no action.
    const ag = document.querySelector<HTMLElement>('[data-testid="benchmark-cell"][data-benchmark-key="administrative_general"]')!;
    expect(within(ag).queryByRole('button')).toBeNull();

    fireEvent.click(btn);
    const note = screen.getByTestId('apply-benchmark-note-rooms_dept_pct') as HTMLInputElement;
    expect(note.value).toBe('Applied CBRE benchmark Rooms Expense 25% from CBRE-Trends-2025.pdf p.12');
    // Nothing is written until Save.
    expect(fx.update).not.toHaveBeenCalled();
    fireEvent.change(note, { target: { value: 'Applied CBRE benchmark Rooms Expense 25% from CBRE-Trends-2025.pdf p.12 — upper-upscale peers' } });
    fireEvent.click(screen.getByTestId('apply-benchmark-save-rooms_dept_pct'));

    await waitFor(() => expect(fx.update).toHaveBeenCalledTimes(1));
    const [, body] = fx.update.mock.calls[0] as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides.rooms_dept_pct).toEqual({
      value: 0.25,
      note: 'Applied CBRE benchmark Rooms Expense 25% from CBRE-Trends-2025.pdf p.12 — upper-upscale peers',
    });
    // Existing overrides are kept.
    expect(body.field_overrides.acquisition_close_date).toEqual({ value: '2025-09-30', note: 'PSA' });
    await waitFor(() => expect(fx.run).toHaveBeenCalledTimes(1));
  });

  it('refuses to save an emptied note — the override needs a justification', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(await screen.findByTestId('apply-benchmark-rooms_dept_pct'));
    fireEvent.change(screen.getByTestId('apply-benchmark-note-rooms_dept_pct'), { target: { value: '  ' } });
    fireEvent.click(screen.getByTestId('apply-benchmark-save-rooms_dept_pct'));
    await new Promise((r) => setTimeout(r, 0));
    expect(fx.update).not.toHaveBeenCalled();
    expect(fx.run).not.toHaveBeenCalled();
  });
});

describe('FON-109 · Apply benchmark in the Assumptions panel', () => {
  it('shows the fee and FF&E reserve suggestions beside the value in use, and applies with the note', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const fee = await screen.findByTestId('benchmark-suggestion-mgmt_fee_pct');
    expect(fee.textContent).toContain('Benchmark 2.5%');
    expect(fee.textContent).toContain('CBRE-Trends-2025.pdf p.9');
    expect(fee.textContent).toContain('In use: 3% (platform default)');
    const ffe = screen.getByTestId('benchmark-suggestion-ffe_reserve_pct');
    expect(ffe.textContent).toContain('FF&E reserve');
    expect(ffe.textContent).toContain('Benchmark 5%');

    fireEvent.click(within(ffe).getByTestId('apply-benchmark-ffe_reserve_pct'));
    fireEvent.click(screen.getByTestId('apply-benchmark-save-ffe_reserve_pct'));
    await waitFor(() => expect(fx.update).toHaveBeenCalledTimes(1));
    const [, body] = fx.update.mock.calls[0] as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides.ffe_reserve_pct).toEqual({
      value: 0.05,
      note: 'Applied CBRE benchmark FF&E Reserve 5% from CBRE-Trends-2025.pdf p.9',
    });
  });

  it('an applied suggestion reads as an analyst override with its note, and offers no second apply', async () => {
    fx.suggestions.mockImplementation(async () => ({
      ...SUGGESTIONS,
      suggestions: SUGGESTIONS.suggestions.map((s) =>
        s.key === 'mgmt_fee_pct'
          ? { ...s, applied: true, current_value: 0.025, current_source: 'analyst_override', current_note: s.suggested_note }
          : s,
      ),
    }));
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const badge = await screen.findByTestId('benchmark-applied-mgmt_fee_pct');
    expect(badge.textContent).toBe('Benchmark 2.5% applied · analyst override');
    expect(badge.getAttribute('title')).toContain('Applied CBRE benchmark Management Fees 2.5% from CBRE-Trends-2025.pdf p.9');
    expect(screen.queryByTestId('apply-benchmark-mgmt_fee_pct')).toBeNull();
  });

  it('renders no action at all when the deal has no suggestions (or the read fails)', async () => {
    fx.suggestions.mockImplementation(async () => { throw new Error('404'); });
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    await screen.findByTestId('projection-benchmark-col-header');
    await waitFor(() => expect(fx.suggestions).toHaveBeenCalled());
    expect(screen.queryByTestId('apply-benchmark-rooms_dept_pct')).toBeNull();
    expect(screen.queryByTestId('benchmark-suggestion-mgmt_fee_pct')).toBeNull();
    expect(screen.queryByTestId('benchmark-suggestion-ffe_reserve_pct')).toBeNull();
  });
});

// Approval must be reversible, and a failed approval must not eat the note.
describe('FON-109 · the applied benchmark can be seen and reset; a failed apply keeps the note', () => {
  it('a failed PATCH leaves the note editor open with the analyst\'s edit; success closes it', async () => {
    fx.update.mockImplementationOnce(async () => {
      throw new Error('PATCH /deals/deal-uuid-1 → 500');
    });
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(await screen.findByTestId('apply-benchmark-rooms_dept_pct'));
    const note = screen.getByTestId('apply-benchmark-note-rooms_dept_pct') as HTMLInputElement;
    fireEvent.change(note, { target: { value: 'Peer set matches our flag; applying.' } });
    fireEvent.click(screen.getByTestId('apply-benchmark-save-rooms_dept_pct'));
    await waitFor(() => expect(fx.update).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(fx.toast).toHaveBeenCalledWith('Could not apply override', { type: 'error' }));
    // Still open, note intact, nothing re-ran.
    const still = screen.getByTestId('apply-benchmark-note-rooms_dept_pct') as HTMLInputElement;
    expect(still.value).toBe('Peer set matches our flag; applying.');
    expect(fx.run).not.toHaveBeenCalled();
    // Retry succeeds → closes.
    fireEvent.click(screen.getByTestId('apply-benchmark-save-rooms_dept_pct'));
    await waitFor(() => expect(fx.update).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByTestId('apply-benchmark-note-rooms_dept_pct')).toBeNull());
    expect(fx.run).toHaveBeenCalledTimes(1);
  });

  it('the FF&E reserve has its own input showing the value in use, with no reset while nothing is overridden', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const input = (await screen.findByTestId('assumption-panel-input-ffe_reserve_pct')) as HTMLInputElement;
    // The suggestion's "in use" value (4%, platform default) — never a browser-made number.
    await waitFor(() => expect(input.value).toBe('4'));
    expect(screen.queryByTestId('assumption-panel-reset-ffe_reserve_pct')).toBeNull();
  });

  it('an applied FF&E benchmark shows as the override and "Reset to source" removes it and re-runs', async () => {
    dealFx.overrides = {
      ...dealFx.overrides,
      ffe_reserve_pct: { value: 0.05, note: 'Applied CBRE benchmark FF&E Reserve 5% from CBRE-Trends-2025.pdf p.9' },
    };
    fx.suggestions.mockImplementation(async () => ({
      ...SUGGESTIONS,
      suggestions: SUGGESTIONS.suggestions.map((s) =>
        s.key === 'ffe_reserve_pct'
          ? { ...s, applied: true, current_value: 0.05, current_source: 'analyst_override', current_note: s.suggested_note }
          : s,
      ),
    }));
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const input = (await screen.findByTestId('assumption-panel-input-ffe_reserve_pct')) as HTMLInputElement;
    expect(input.value).toBe('5');
    await screen.findByTestId('benchmark-applied-ffe_reserve_pct');
    fireEvent.click(screen.getByTestId('assumption-panel-reset-ffe_reserve_pct'));
    await waitFor(() => expect(fx.update).toHaveBeenCalledTimes(1));
    const [, body] = fx.update.mock.calls[0] as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides).not.toHaveProperty('ffe_reserve_pct');
    // The unrelated override survives the reset.
    expect(body.field_overrides.acquisition_close_date).toEqual({ value: '2025-09-30', note: 'PSA' });
    await waitFor(() => expect(fx.run).toHaveBeenCalledTimes(1));
  });
});
