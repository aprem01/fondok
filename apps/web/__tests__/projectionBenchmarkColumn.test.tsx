/**
 * FON-97 — the Future P&L's in-table Benchmark column.
 *
 * Sam / Rani: each P&L line's projected expense ratio should be compared with
 * the CBRE / HotStats comp-set ratio on the SAME row. The Base-year column
 * group gains a "Benchmark" sub-column when — and only when — the deal
 * carries a ``pnl_benchmark`` block, each mapped row prints the benchmark
 * ratio on the row's own basis plus the model-minus-benchmark points, rows
 * with nothing to compare say so, the header switch is remembered per
 * device, the old panel is collapsed under "Benchmark detail", and the data
 * is fetched exactly once for both.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

const HOLD_YEARS = 3;
const CALENDAR = [2025, 2026, 2027];
const TOTAL_REVENUE = 13_600_000;
const ROOMS_REVENUE = 10_000_000;
const ROOMS_EXPENSE = 3_000_000; // 30.0% of Rooms revenue (dept basis)
const AG = 900_000; //              6.6% of Total revenue
const GOP = 5_400_000; //           39.7% of Total revenue

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
  dept_expenses: { rooms: ROOMS_EXPENSE, food_beverage: 1_800_000, other_operated: 200_000, total: 5_000_000 },
  undistributed: {
    administrative_general: AG, information_telecom: 180_000, sales_marketing: 800_000,
    property_operations: 760_000, utilities: 560_000, total: 3_200_000,
  },
  mgmt_fee: 408_000,
  ffe_reserve: 552_613,
  fixed_charges: { property_taxes: 700_000, insurance: 300_000, rent: 0, other_fixed: 0, total: 1_000_000 },
  gop: GOP,
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

/** What ``GET /deals/{id}/market-data`` serves when a CBRE report is on the deal. */
const WITH_BENCHMARK = {
  deal_id: 'deal-uuid-1',
  pnl_benchmark: {
    peer_set_size: 9,
    source_filename: 'CBRE-Trends-2025-upper-upscale.pdf',
    categories: [
      { key: 'rooms', label: 'Rooms Expense', benchmark_line: 'rooms_dept_expense', ratio: 0.25,
        ratio_basis: 'department_revenue', ratio_source: 'computed_from_totals', par_usd: 12000, por_usd: 45.5 },
      { key: 'administrative_general', label: 'Administrative & General', benchmark_line: 'a_and_g',
        ratio: 0.08, ratio_basis: 'total_revenue', ratio_source: 'reported', par_usd: null, por_usd: null },
      { key: 'gop', label: 'Gross Operating Profit', benchmark_line: 'gop',
        ratio: 0.42, ratio_basis: 'total_revenue', ratio_source: 'legacy_summary', par_usd: null, por_usd: null },
      // POR only — the report prices insurance per room but publishes no ratio.
      { key: 'insurance', label: 'Insurance', benchmark_line: 'insurance', ratio: null,
        ratio_basis: 'total_revenue', ratio_source: null, par_usd: null, por_usd: 6.25 },
    ],
  },
  sources: { PNL_BENCHMARK: ['doc-1'] },
};
const WITHOUT_BENCHMARK = { deal_id: 'deal-uuid-1', pnl_benchmark: null, sources: {} };

// ── Mocks ────────────────────────────────────────────────────────────
const fx = vi.hoisted(() => ({
  toast: vi.fn(),
  update: vi.fn(async () => ({ id: 'deal-uuid-1' })),
  run: vi.fn(async () => {}),
  market: vi.fn(async (_id: string, _signal?: AbortSignal): Promise<unknown> => null),
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
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: {
      id: 'deal-uuid-1', name: 'Kimpton Angler', keys: 132,
      field_overrides: { acquisition_close_date: { value: '2025-09-30', note: 'PSA' } },
    },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: fx.run, running: false, status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: fx.toast }) }));
vi.mock('@/lib/exportXlsx', () => ({ downloadXlsx: vi.fn(async () => {}) }));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: fx.update },
      market: { ...actual.api.market, data: (id: string, signal?: AbortSignal) => fx.market(id, signal) },
    },
  };
});

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';
import { BENCHMARK_TOGGLE_STORAGE_KEY } from '@/components/project/pl/BenchmarkColumn';

beforeEach(() => {
  cleanup();
  fx.market.mockReset();
  fx.market.mockImplementation(async () => WITH_BENCHMARK);
  window.localStorage.clear();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const header0 = () => screen.getByTestId('projection-col-header-0');
const header1 = () => screen.getByTestId('projection-col-header-1');
const cellFor = (key: string) =>
  document.querySelector<HTMLElement>(`[data-testid="benchmark-cell"][data-benchmark-key="${key}"]`);
const benchmarkCellInRow = (row: HTMLElement) => within(row).getByTestId('benchmark-cell');

async function renderWithBenchmark() {
  render(<ProjectionsSection dealId="deal-uuid-1" />);
  await screen.findByTestId('projection-benchmark-col-header');
}

// ── 1. Present only with a benchmark ─────────────────────────────────
describe('FON-97 · the Benchmark column exists only when the deal has a benchmark', () => {
  it('is absent — no header, no cells, no switch, no detail panel — without a pnl_benchmark block', async () => {
    fx.market.mockImplementation(async () => WITHOUT_BENCHMARK);
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    await waitFor(() => expect(fx.market).toHaveBeenCalledTimes(1));
    // Let the (null) read settle before asserting absence.
    await waitFor(() => expect(screen.getByTestId('projection-col-header-0').getAttribute('colspan')).toBe('4'));

    expect(screen.queryByTestId('projection-benchmark-col-header')).toBeNull();
    expect(screen.queryByTestId('projection-show-benchmark')).toBeNull();
    expect(document.querySelectorAll('[data-testid="benchmark-cell"]')).toHaveLength(0);
    expect(screen.queryByTestId('benchmark-detail')).toBeNull();
    expect(screen.queryByTestId('projections-legend-benchmark')).toBeNull();
    // Every year group is the plain 4 sub-columns (Amount · % Rev · PAR · POR).
    expect(header0().getAttribute('colspan')).toBe('4');
    expect(header1().getAttribute('colspan')).toBe('4');
  });

  it('is absent when the market-data read fails — never a blank column', async () => {
    fx.market.mockImplementation(async () => { throw new Error('502'); });
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    await waitFor(() => expect(fx.market).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('projection-benchmark-col-header')).toBeNull();
    expect(screen.queryByTestId('projection-show-benchmark')).toBeNull();
  });

  it('adds one sub-column to the Base-year group only, on by default, with the legend line', async () => {
    await renderWithBenchmark();
    expect(screen.getByTestId('projection-benchmark-col-header').textContent).toBe('Benchmark');
    // Base year: Amount · % Rev · Benchmark · PAR · POR; later years unchanged.
    expect(header0().getAttribute('colspan')).toBe('5');
    expect(header1().getAttribute('colspan')).toBe('4');
    expect(screen.getByTestId('projection-col-header-exit').getAttribute('colspan')).toBe('4');
    const sw = screen.getByTestId('projection-show-benchmark') as HTMLInputElement;
    expect(sw.checked).toBe(true);
    expect(screen.getByTestId('projections-legend-benchmark').textContent).toMatch(/direction only/);
    // The header names the source document.
    expect(screen.getByTestId('projection-benchmark-col-header').getAttribute('title')).toContain(
      'CBRE-Trends-2025-upper-upscale.pdf',
    );
  });

  it('keeps the Base-year group wider than the others with "% Rev" switched off too', async () => {
    await renderWithBenchmark();
    fireEvent.click(screen.getByTestId('projection-show-pct'));
    expect(header0().getAttribute('colspan')).toBe('4'); // Amount · Benchmark · PAR · POR
    expect(header1().getAttribute('colspan')).toBe('3');
    expect(cellFor('rooms')).not.toBeNull();
  });
});

// ── 2. Basis per row + delta sign ───────────────────────────────────
describe('FON-97 · each row compares on its own basis and shows the signed difference', () => {
  it('departmental line: benchmark % of Rooms revenue, delta vs the model dept ratio, direction above', async () => {
    await renderWithBenchmark();
    const rooms = cellFor('rooms')!;
    expect(rooms.getAttribute('data-benchmark-state')).toBe('compared');
    expect(rooms.getAttribute('data-benchmark-basis')).toBe('department_revenue');
    expect(within(rooms).getByTestId('benchmark-ratio').textContent).toBe('25.0%');
    // Model 3.0M / 10.0M = 30.0% − 25.0% = +5.0 pts → above the benchmark.
    expect(within(rooms).getByTestId('benchmark-delta').textContent).toBe('▲+5.0 pts');
    expect(rooms.getAttribute('data-direction')).toBe('above');
    // The row's own % Rev cell is the number the delta was taken from.
    const row = rooms.closest('tr')!;
    const pctCell = row.querySelector('[data-ratio-basis="department"]')!;
    expect(pctCell.textContent).toContain('30.0%');
    // Tooltip: line, peer set, POR / PAR, source document, how the ratio was derived.
    const title = rooms.getAttribute('title') ?? '';
    expect(title).toContain('rooms_dept_expense');
    expect(title).toContain('Peer set: 9 hotels');
    expect(title).toContain('POR $45.5');
    expect(title).toContain('PAR $12,000');
    expect(title).toContain('CBRE-Trends-2025-upper-upscale.pdf');
    expect(title).toMatch(/line total ÷ its revenue total/);
    expect(title).toContain('30.00% − benchmark 25.00% = +5.00 pts');
  });

  it('undistributed line: benchmark % of Total revenue, direction below', async () => {
    await renderWithBenchmark();
    const ag = cellFor('administrative_general')!;
    expect(ag.getAttribute('data-benchmark-basis')).toBe('total_revenue');
    expect(within(ag).getByTestId('benchmark-ratio').textContent).toBe('8.0%');
    // Model 0.9M / 13.6M = 6.6% − 8.0% = −1.4 pts → below.
    expect(within(ag).getByTestId('benchmark-delta').textContent).toBe('▼-1.4 pts');
    expect(ag.getAttribute('data-direction')).toBe('below');
    expect(ag.getAttribute('title')).toMatch(/as printed in the report/i);
  });

  it('a profit line (GOP) is compared on total revenue with the same sign convention — no verdict colouring', async () => {
    await renderWithBenchmark();
    const gop = cellFor('gop')!;
    expect(gop.closest('tr')!.getAttribute('data-testid')).toBe('row-gop');
    // 5.4M / 13.6M = 39.7% − 42.0% = −2.3 pts → below (direction only).
    expect(within(gop).getByTestId('benchmark-delta').textContent).toBe('▼-2.3 pts');
    expect(gop.getAttribute('data-direction')).toBe('below');
    expect(gop.getAttribute('title')).toMatch(/colour marks direction only/);
  });

  it('wires all thirteen mapped lines (empty cells for the ones this report did not publish)', async () => {
    await renderWithBenchmark();
    const keys = Array.from(document.querySelectorAll('[data-testid="benchmark-cell"]'))
      .map((c) => c.getAttribute('data-benchmark-key'))
      .filter((k) => k);
    for (const k of [
      'rooms', 'food_beverage', 'other_operated', 'administrative_general', 'information_telecom',
      'sales_marketing', 'property_operations', 'utilities', 'gop', 'mgmt_fee',
      'property_taxes', 'insurance', 'rent',
    ]) expect(keys, k).toContain(k);
  });
});

// ── 3. Rows with nothing to compare ─────────────────────────────────
describe('FON-97 · rows the benchmark does not cover say so', () => {
  it('an unmapped row (Total Revenue) prints a dash with "not published in the benchmark"', async () => {
    await renderWithBenchmark();
    const row = screen.getByText('Total Revenue').closest('tr') as HTMLElement;
    const cell = benchmarkCellInRow(row);
    expect(cell.textContent?.trim()).toBe('—');
    expect(cell.getAttribute('data-benchmark-state')).toBe('unmapped');
    expect(cell.getAttribute('title')).toMatch(/not published in the benchmark/);
  });

  it('a mapped line the report left out (Utilities here) prints a dash — never a zero', async () => {
    await renderWithBenchmark();
    const util = cellFor('utilities')!;
    expect(util.textContent?.trim()).toBe('—');
    expect(util.getAttribute('data-benchmark-state')).toBe('unmapped');
    expect(util.getAttribute('title')).toMatch(/not published in the benchmark/);
  });

  it('a line priced POR-only (no ratio) prints a dash and keeps the POR in the tooltip', async () => {
    await renderWithBenchmark();
    const ins = cellFor('insurance')!;
    expect(ins.textContent?.trim()).toBe('—');
    expect(ins.getAttribute('data-benchmark-state')).toBe('no_ratio');
    expect(ins.getAttribute('title')).toContain('POR $6.25');
    expect(ins.getAttribute('title')).toMatch(/not published in the benchmark/);
  });
});

// ── 4. Header switch, remembered per device ─────────────────────────
describe('FON-97 · the Benchmark switch hides the column and is remembered in localStorage', () => {
  it('toggling off removes the column (not blanks it) and stores the choice; a fresh mount honours it', async () => {
    await renderWithBenchmark();
    fireEvent.click(screen.getByTestId('projection-show-benchmark'));
    expect(screen.queryByTestId('projection-benchmark-col-header')).toBeNull();
    expect(document.querySelectorAll('[data-testid="benchmark-cell"]')).toHaveLength(0);
    expect(header0().getAttribute('colspan')).toBe('4');
    expect(window.localStorage.getItem(BENCHMARK_TOGGLE_STORAGE_KEY)).toBe('0');
    // The switch stays offered (the benchmark still exists) and reads "off".
    expect((screen.getByTestId('projection-show-benchmark') as HTMLInputElement).checked).toBe(false);
    // The detail panel is independent of the column switch.
    expect(screen.getByTestId('benchmark-detail')).toBeTruthy();

    cleanup();
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const sw = await screen.findByTestId('projection-show-benchmark');
    expect((sw as HTMLInputElement).checked).toBe(false);
    expect(screen.queryByTestId('projection-benchmark-col-header')).toBeNull();

    fireEvent.click(sw);
    expect(await screen.findByTestId('projection-benchmark-col-header')).toBeTruthy();
    expect(window.localStorage.getItem(BENCHMARK_TOGGLE_STORAGE_KEY)).toBe('1');
  });

  it('defaults to ON when storage is unavailable and never throws', async () => {
    const getSpy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    const setSpy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    await renderWithBenchmark();
    expect((screen.getByTestId('projection-show-benchmark') as HTMLInputElement).checked).toBe(true);
    fireEvent.click(screen.getByTestId('projection-show-benchmark'));
    expect(screen.queryByTestId('projection-benchmark-col-header')).toBeNull();
    getSpy.mockRestore();
    setSpy.mockRestore();
  });
});

// ── 5. One fetch, collapsed detail ──────────────────────────────────
describe('FON-97 · one market-data read feeds both the column and the collapsed detail panel', () => {
  it('fetches market-data once and renders the detail collapsed by default', async () => {
    await renderWithBenchmark();
    await waitFor(() => expect(fx.market).toHaveBeenCalledTimes(1));
    expect(fx.market.mock.calls[0][0]).toBe('deal-uuid-1');
    const details = screen.getByTestId('benchmark-detail') as HTMLDetailsElement;
    expect(details.open).toBe(false);
    expect(screen.getByTestId('benchmark-detail-summary').textContent).toContain('Benchmark detail');
    expect(screen.getByTestId('benchmark-detail-summary').textContent).toContain('9 hotels');
    // The full table (POR / PAR) is still inside it.
    expect(within(details).getByTestId('benchmark-comparison-table')).toBeTruthy();
  });
});
