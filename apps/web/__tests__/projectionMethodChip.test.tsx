/**
 * E-016 — per-line projection method chip on the Future P&L, and R-025 —
 * the operating model shown beside the management fee.
 *
 * The chip shows the method the expense engine RAN (`expense.line_methods`);
 * clicking it opens an editor (method + value + required note) that PATCHes
 * `field_overrides.projection_methods.<line>.method|value` and re-runs.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import { NOTE_REQUIRED_MESSAGE } from '@/lib/overrideNote';

const HOLD_YEARS = 5;
const CALENDAR = [2025, 2026, 2027, 2028, 2029];
const TOTAL_REVENUE = 13_600_000;

const revYear = (i: number) => ({
  year: i + 1,
  occupancy: 0.75,
  adr: 300,
  revpar: 225,
  rooms_revenue: 10_000_000,
  fb_revenue: 3_000_000,
  other_revenue: 600_000,
  total_revenue: TOTAL_REVENUE,
});
const fbYear = (i: number) => ({
  year: i + 1,
  rooms_revenue: 10_000_000,
  fb_revenue: 3_000_000,
  resort_fees: 0,
  other_revenue: 600_000,
  total_revenue: TOTAL_REVENUE,
});
const expYear = (i: number) => ({
  year: i + 1,
  total_revenue: TOTAL_REVENUE,
  dept_expenses: { rooms: 3_000_000, food_beverage: 1_800_000, other_operated: 200_000, total: 5_000_000 },
  undistributed: {
    administrative_general: 900_000,
    information_telecom: 180_000,
    sales_marketing: 800_000,
    property_operations: 760_000,
    utilities: 560_000,
    total: 3_200_000,
  },
  mgmt_fee: 408_000,
  ffe_reserve: 552_613,
  fixed_charges: { property_taxes: 700_000, insurance: 300_000, rent: 0, other_fixed: 0, total: 1_000_000 },
  gop: 5_400_000,
  noi: 1_448_443,
  noi_institutional: 2_001_056,
});

function buildOutputs(calendar: number[] | undefined): EngineOutputsResponse {
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
          projection_start_year: calendar ? calendar[0] : null,
          projection_calendar_years: calendar ?? [],
        },
      },
      fb: { ...stub, engine: 'fb', outputs: { years: Array.from({ length: HOLD_YEARS }, (_, i) => fbYear(i)) } },
      expense: {
        ...stub, engine: 'expense',
        outputs: {
          years: Array.from({ length: HOLD_YEARS }, (_, i) => expYear(i)),
          // The published stabilized block — the Assumptions panel's one other
          // editable non-AssumptionField control reads it.
          stabilization: { stabilized_year: 3, stabilized_year_index: 2, source: 'fondok_signal' },
          line_methods: LINE_METHODS,
        },
      },
      returns: {
        ...stub, engine: 'returns',
        outputs: {
          hold_years: HOLD_YEARS, terminal_noi: 2_759_000, exit_cap_rate: 0.07, revpar_growth: 0.045,
        },
      },
      debt: { ...stub, engine: 'debt', outputs: { year_one_dscr: 1.59 } },
      capital: { ...stub, engine: 'capital', outputs: { purchase_price: 34_000_000, uses: [], sources: [] } },
    },
  } as unknown as EngineOutputsResponse;
}

// ── Mocks ────────────────────────────────────────────────────────────
const fx = vi.hoisted(() => ({
  toast: vi.fn(),
  update: vi.fn(async () => ({ id: 'deal-uuid-1' })),
  run: vi.fn(async () => {}),
}));

let CALENDAR_YEARS: number[] | undefined = CALENDAR;
let FIELD_OVERRIDES: Record<string, unknown> = {};
let OPERATING_MODEL: string | null = null;
let LINE_METHODS: Record<string, unknown> = {};

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
      outputs: buildOutputs(CALENDAR_YEARS),
      previous: null, loading: false, settled: true, lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', name: 'Kimpton Angler', keys: 132, field_overrides: FIELD_OVERRIDES, operating_model: OPERATING_MODEL },
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
    api: { ...actual.api, deals: { ...actual.api.deals, update: fx.update } },
  };
});

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';

beforeEach(() => {
  cleanup();
  fx.toast.mockClear();
  fx.update.mockClear();
  fx.run.mockClear();
  CALENDAR_YEARS = CALENDAR;
  FIELD_OVERRIDES = { acquisition_close_date: { value: '2025-09-30', note: 'PSA' } };
  OPERATING_MODEL = null;
  LINE_METHODS = {
    rooms_dept_expense: { method: 'growth', value: 0.035, source: 'default' },
    utilities: { method: 'par', value: 12.5, source: 'override' },
  };
});


describe('E-016 · MethodChip shows the active method per expense line', () => {
  it('labels each line with the engine-published method and value', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const rooms = screen.getByTestId('method-chip-rooms_dept_expense');
    expect(rooms.textContent).toContain('Growth');
    expect(rooms.textContent).toContain('3.5%');
    expect(rooms.getAttribute('title')).toMatch(/Model default/);
    const util = screen.getByTestId('method-chip-utilities');
    expect(util.textContent).toContain('PAR');
    expect(util.textContent).toContain('$12.50');
    expect(util.getAttribute('title')).toMatch(/Analyst override/);
    // A line the run did not publish still gets a chip (to set a method).
    expect(screen.getByTestId('method-chip-sales_marketing').textContent).toContain('Method');
  });

  it('refuses to save without a note, then saves method + value with the note and re-runs', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByTestId('method-chip-rooms_dept_expense'));
    const editor = screen.getByTestId('method-editor-rooms_dept_expense');
    fireEvent.click(within(editor).getByRole('radio', { name: 'POR' }));
    fireEvent.change(within(editor).getByLabelText('Method value'), { target: { value: '38' } });
    fireEvent.click(within(editor).getByText(/Save/));
    expect(within(editor).getByRole('alert').textContent).toBe(NOTE_REQUIRED_MESSAGE);
    expect(fx.update).not.toHaveBeenCalled();

    fireEvent.change(within(editor).getByLabelText('Override note'), {
      target: { value: 'Brand standard cost per occupied room' },
    });
    fireEvent.click(within(editor).getByText(/Save/));
    await waitFor(() => expect(fx.update).toHaveBeenCalledTimes(1));
    const [, patch] = fx.update.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(patch.field_overrides['projection_methods.rooms_dept_expense.method']).toEqual({
      value: 'por', note: 'Brand standard cost per occupied room',
    });
    expect(patch.field_overrides['projection_methods.rooms_dept_expense.value']).toEqual({
      value: 38, note: 'Brand standard cost per occupied room',
    });
    // Existing overrides survive.
    expect(patch.field_overrides.acquisition_close_date).toBeTruthy();
    await waitFor(() => expect(fx.run).toHaveBeenCalledTimes(1));
  });

  it('a percent method stores a fraction; a blank value stores no value key (= default)', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByTestId('method-chip-sales_marketing'));
    const editor = screen.getByTestId('method-editor-sales_marketing');
    fireEvent.click(within(editor).getByRole('radio', { name: '% of rev' }));
    fireEvent.change(within(editor).getByLabelText('Method value'), { target: { value: '6.5' } });
    fireEvent.change(within(editor).getByLabelText('Override note'), { target: { value: 'Plan' } });
    fireEvent.click(within(editor).getByText(/Save/));
    await waitFor(() => expect(fx.update).toHaveBeenCalledTimes(1));
    const [, patch] = fx.update.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect((patch.field_overrides['projection_methods.sales_marketing.value'] as { value: number }).value)
      .toBeCloseTo(0.065);

    fx.update.mockClear();
    cleanup();
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByTestId('method-chip-information_telecom'));
    const ed2 = screen.getByTestId('method-editor-information_telecom');
    fireEvent.click(within(ed2).getByRole('radio', { name: 'Growth' }));
    fireEvent.change(within(ed2).getByLabelText('Override note'), { target: { value: 'Model growth' } });
    fireEvent.click(within(ed2).getByText(/Save/));
    await waitFor(() => expect(fx.update).toHaveBeenCalledTimes(1));
    const [, patch2] = fx.update.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(patch2.field_overrides['projection_methods.information_telecom.method']).toEqual({
      value: 'growth', note: 'Model growth',
    });
    expect('projection_methods.information_telecom.value' in patch2.field_overrides).toBe(false);
  });
});

describe('R-025 · operating model beside the management fee', () => {
  it('shows nothing when no operating model was captured', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(screen.queryByTestId('mgmt-fee-operating-model')).toBeNull();
  });

  it('names the operating model and only the documented (generic) fee range', () => {
    OPERATING_MODEL = 'third_party';
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const hint = screen.getByTestId('mgmt-fee-operating-model').textContent ?? '';
    expect(hint).toContain('Third-party operator');
    expect(hint).toContain('2–6%');
    expect(hint).toMatch(/no operator-specific range/);
  });
});
