/**
 * FON-41 E-011 — cell comments on the Future P&L.
 *
 * A projected cell's comment is keyed `proj:<engine>.years[<i>].<path>` — the
 * same engine path + year index the lineage drawer opens — so the marker sits
 * on exactly that year's cell, and "Commented cells" filters the statement to
 * the rows that carry one. Historical (`hist:`) threads never surface here.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { CellComment, EngineOutputsResponse } from '@/lib/api';

const YEARS = 3;
const revYear = (i: number) => ({ year: i + 1, occupancy: 0.75, adr: 300, revpar: 225, rooms_revenue: 10_000_000, fb_revenue: 3_000_000, other_revenue: 600_000, total_revenue: 13_600_000 });
const fbYear = (i: number) => ({ year: i + 1, rooms_revenue: 10_000_000, fb_revenue: 3_000_000, resort_fees: 0, other_revenue: 600_000, total_revenue: 13_600_000 });
const expYear = (i: number) => ({
  year: i + 1, total_revenue: 13_600_000,
  dept_expenses: { rooms: 3_000_000, food_beverage: 1_800_000, other_operated: 200_000, total: 5_000_000 },
  undistributed: { administrative_general: 900_000, information_telecom: 180_000, sales_marketing: 800_000, property_operations: 760_000, utilities: 560_000, total: 3_200_000 },
  mgmt_fee: 408_000, ffe_reserve: 552_613,
  fixed_charges: { property_taxes: 700_000, insurance: 300_000, rent: 0, other_fixed: 0, total: 1_000_000 },
  gop: 5_400_000, noi: 1_448_443, noi_institutional: 2_001_056,
});
const stub = { deal_id: 'deal-uuid-1', status: 'complete', summary: '', inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1' };
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    revenue: { ...stub, engine: 'revenue', outputs: { years: Array.from({ length: YEARS }, (_, i) => revYear(i)), projection_calendar_years: [2025, 2026, 2027] } },
    fb: { ...stub, engine: 'fb', outputs: { years: Array.from({ length: YEARS }, (_, i) => fbYear(i)) } },
    expense: { ...stub, engine: 'expense', outputs: { years: Array.from({ length: YEARS }, (_, i) => expYear(i)) } },
    returns: { ...stub, engine: 'returns', outputs: { hold_years: YEARS, terminal_noi: 2_000_000, exit_cap_rate: 0.07, revpar_growth: 0.045 } },
  },
} as unknown as EngineOutputsResponse;

const GOP_Y2 = 'proj:expense.years[1].gop';
const COMMENTS: CellComment[] = [
  { id: 'c1', deal_id: 'deal-uuid-1', cell_key: GOP_Y2, cell_label: 'Gross Operating Profit · Year 2 · 2026', body: 'GOP margin jump looks aggressive', author_id: 'u', author_email: 'eshan@fondok.test', created_at: '2026-10-01T00:00:00Z', resolved_at: null, resolved_by: null },
  { id: 'h1', deal_id: 'deal-uuid-1', cell_key: 'hist:d2023::rooms_revenue', cell_label: 'Rooms Revenue · FY2023', body: 'historical note', author_id: 'u', author_email: null, created_at: '2026-10-01T00:00:00Z', resolved_at: null, resolved_by: null },
];

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/projects/deal-uuid-1',
}));
vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/useEngineOutputs')>();
  return { ...actual, useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, settled: true, lastRunAt: null, refresh: vi.fn(async () => {}) }) };
});
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({ deal: { id: 'deal-uuid-1', name: 'D', keys: 132, field_overrides: {} }, status: null, loading: false, error: null, fromMock: false, refresh: vi.fn() }),
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ run: vi.fn(async () => {}), running: false, status: 'idle', error: null }) }));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/exportXlsx', () => ({ downloadXlsx: vi.fn(async () => {}) }));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: vi.fn(async () => ({})) },
      comments: { list: vi.fn(async () => COMMENTS), create: vi.fn(), resolveThread: vi.fn() },
    },
  };
});

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';

beforeEach(() => cleanup());

describe('Future P&L — cell comments (E-011)', () => {
  it('marks exactly the commented year cell, keyed by engine path + year index', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const m = await screen.findByTestId(`comment-marker-${GOP_Y2}`);
    expect(m).toHaveAttribute('data-comment-state', 'open');
    expect(m).toHaveTextContent('1');
    // Year 1's GOP is a different cell.
    expect(screen.getByTestId('comment-marker-proj:expense.years[0].gop')).toHaveAttribute('data-comment-state', 'none');
    fireEvent.click(m);
    expect(within(screen.getByTestId('comment-thread-panel')).getByText('GOP margin jump looks aggressive')).toBeInTheDocument();
  });

  it('"Commented cells" keeps only rows with a commented cell', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    await screen.findByTestId(`comment-marker-${GOP_Y2}`);
    expect(screen.getByTestId('row-gop')).toBeInTheDocument();
    expect(screen.getByText('Management Fees')).toBeInTheDocument();
    const toggle = screen.getByTestId('commented-cells-toggle');
    expect(toggle).toHaveTextContent('· 1'); // the hist: thread is the other view's
    fireEvent.click(toggle);
    expect(screen.getByTestId('row-gop')).toBeInTheDocument();
    expect(screen.queryByText('Management Fees')).not.toBeInTheDocument();
    expect(within(screen.getByTestId('commented-cells-list')).getByText('Gross Operating Profit · Year 2 · 2026')).toBeInTheDocument();
  });
});
