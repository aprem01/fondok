/**
 * Financials tab — Projections "Assumptions" panel (canonical rebuild).
 *
 * Contracts locked here (design/canonical/Financials Tab.dc.html):
 *
 *  1. ASSUMPTIONS PANEL RENDERS from mocked engine outputs — the three cards
 *     (Growth / Resort fee revenue / Deal economics) and every driver field
 *     the canonical shows are present, keyed off the worker projection years.
 *
 *  2. CANONICAL EDIT PATH — editing an assumption (Management fee) PATCHes the
 *     deal's ``field_overrides`` via api.deals.update and re-runs the model
 *     (the same path the driver cells use). pct fields persist as a fraction.
 *
 *  3. EXIT CAP IS INVESTMENT-OWNED — it is shown here linked / read-only (a
 *     "sourced from Investment →" reference), NOT as a second editable owner:
 *     its row carries no input.
 *
 * Write-only — not part of the run set for this change.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

// One base year + two forecast years of worker revenue/fb/expense output —
// enough for buildFromWorker() to produce a non-null `years` (the render gate).
const revYear = (year: number) => ({
  year,
  occupancy: 0.75,
  adr: 300,
  revpar: 225,
  rooms_revenue: 10_000_000,
  fb_revenue: 2_000_000,
  other_revenue: 500_000,
  total_revenue: 12_500_000,
});
const fbYear = (year: number) => ({
  year,
  rooms_revenue: 10_000_000,
  fb_revenue: 2_000_000,
  resort_fees: 0,
  other_revenue: 500_000,
  total_revenue: 12_500_000,
});
const expYear = (year: number) => ({
  year,
  total_revenue: 12_500_000,
  dept_expenses: { rooms: 2_500_000, food_beverage: 1_500_000, other_operated: 250_000, total: 4_250_000 },
  undistributed: {
    administrative_general: 900_000,
    information_telecom: 180_000,
    sales_marketing: 800_000,
    property_operations: 500_000,
    utilities: 560_000,
    total: 2_940_000,
  },
  mgmt_fee: 375_000,
  ffe_reserve: 500_000,
  fixed_charges: { property_taxes: 700_000, insurance: 200_000, rent: 0, other_fixed: 0, total: 900_000 },
  gop: 5_310_000,
  noi: 4_035_000,
  noi_institutional: 4_035_000,
});

const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    revenue: {
      deal_id: 'deal-uuid-1', engine: 'revenue', status: 'complete', summary: '',
      outputs: { years: [2025, 2026, 2027].map(revYear) },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
    fb: {
      deal_id: 'deal-uuid-1', engine: 'fb', status: 'complete', summary: '',
      outputs: { years: [2025, 2026, 2027].map(fbYear) },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
    expense: {
      deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [2025, 2026, 2027].map(expYear) },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

// Keep the REAL getEngineField; only swap the hook to serve our fixture.
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS,
      previous: null,
      loading: false,
      lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});

const refreshDealSpy = vi.fn();
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', keys: 132, field_overrides: {} },
    status: null,
    loading: false,
    error: null,
    fromMock: false,
    refresh: refreshDealSpy,
  }),
}));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, status: 'idle', error: null }),
}));

// Exit cap has no resolvable source in this fixture → panel falls back to the
// engine default (7.0%). The reference stays linked / read-only regardless.
vi.mock('@/lib/hooks/useDealProvenance', () => ({
  useSource: () => null,
}));

// api surface — spy on the field_overrides PATCH (the canonical edit path).
const updateSpy = vi.fn(async (_id: string, _body: unknown) => ({ id: 'deal-uuid-1' }));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: updateSpy },
    },
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  engineRunSpy.mockClear();
  refreshDealSpy.mockClear();
});

describe('Financials · Projections — Assumptions panel renders from engine output', () => {
  it('shows the panel intro + every canonical driver field', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.getByText('Assumptions')).toBeInTheDocument();
    expect(
      screen.getByText(/These drive every projected year below/i),
    ).toBeInTheDocument();

    // Growth card.
    expect(screen.getByText('Revenue inflation')).toBeInTheDocument();
    expect(screen.getByText('Dept. expense inflation')).toBeInTheDocument();
    expect(screen.getByText('Other expense inflation')).toBeInTheDocument();
    // Resort fee card.
    expect(screen.getByText('Resort fee revenue')).toBeInTheDocument();
    expect(screen.getByText('Resort fee')).toBeInTheDocument();
    expect(screen.getByText('Capture Yr 1')).toBeInTheDocument();
    expect(screen.getByText('Capture Yr 2')).toBeInTheDocument();
    expect(screen.getByText('Capture Yr 3+')).toBeInTheDocument();
    // Deal economics card.
    expect(screen.getByText('Deal economics')).toBeInTheDocument();
    expect(screen.getByText('Management fee')).toBeInTheDocument();
    expect(screen.getByText('Exit cap rate')).toBeInTheDocument();
  });
});

describe('Financials · Projections — canonical edit path (field_overrides + re-run)', () => {
  it('editing Management fee PATCHes field_overrides (as a fraction) and re-runs', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    const mgmtRow = screen.getByText('Management fee').parentElement as HTMLElement;
    const mgmtInput = mgmtRow.querySelector('input[type="number"]') as HTMLInputElement;
    expect(mgmtInput).toBeTruthy();
    expect(mgmtInput.value).toBe('3'); // default 0.03 → 3.0%

    fireEvent.change(mgmtInput, { target: { value: '5' } });
    fireEvent.blur(mgmtInput);

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const lastCall = updateSpy.mock.calls.at(-1) as [string, { field_overrides: Record<string, unknown> }];
    const entry = lastCall[1].field_overrides.mgmt_fee_pct as { value: number };
    expect(entry.value).toBeCloseTo(0.05); // pct persisted as a fraction
    expect(engineRunSpy).toHaveBeenCalled(); // full re-run
  });
});

describe('Financials · Projections — Exit cap is Investment-owned (read-only)', () => {
  it('renders exit cap linked with an Investment reference and NO editable input', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    const exitRow = screen.getByText('Exit cap rate').parentElement as HTMLElement;
    // No input in the exit-cap row — it is not a second editable owner.
    expect(exitRow.querySelector('input')).toBeNull();

    // A "sourced from Investment →" reference deep-links to the Investment tab.
    const ref = screen.getByText('Investment →');
    expect(ref).toBeInTheDocument();
    expect(ref.closest('a')?.getAttribute('href')).toContain('tab=investment');
  });
});
