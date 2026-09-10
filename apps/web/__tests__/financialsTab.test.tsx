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
      outputs: OUTPUTS_OVERRIDE === undefined ? OUTPUTS : OUTPUTS_OVERRIDE,
      previous: null,
      loading: false,
      settled: SETTLED,
      lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});

const refreshDealSpy = vi.fn();
// Read at render time by the hoisted useEngineOutputs mock: `settled` gates
// PLTab's loading skeleton; OUTPUTS_OVERRIDE lets a test serve null outputs.
let SETTLED = true;
let OUTPUTS_OVERRIDE: EngineOutputsResponse | null | undefined = undefined;
void SETTLED; void OUTPUTS_OVERRIDE;
// Settable per-test (read at render time — the factory itself is hoisted) so
// the FON-61 Revert can start from a deal that carries the STR seed.
let mockFieldOverrides: Record<string, unknown> = {};
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', keys: 132, field_overrides: mockFieldOverrides },
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
// Settable per-test so the FON-61 Year-1 basis chip (driven by the worker's
// source tags, never the flag alone) can be exercised.
let mockSources: Record<string, string> = {};
vi.mock('@/lib/hooks/useDealProvenance', () => ({
  useSource: (key: string | undefined) =>
    key && mockSources[key] ? { source: mockSources[key], value: null } : null,
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
      // Lazy-wrapped: a direct `update: updateSpy` is read when the hoisted
      // vi.mock factory builds this object (before `updateSpy`'s const is
      // initialized) → "Cannot access 'updateSpy' before initialization".
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as Parameters<typeof updateSpy>)) },
    },
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';
import { STR_MARKET_OVERRIDE_NOTE } from '@/lib/provenance';

beforeEach(() => {
  cleanup();
  mockSources = {};
  mockFieldOverrides = {};
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
    // FON-69 — relabeled: the lever drives ADR growth with the occupancy path held.
    expect(screen.getByText('RevPAR growth (drives ADR; occupancy path held)')).toBeInTheDocument();
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

// FON-61 (D4) — the Year-1 basis chip reads the WORKER's source tags. It is
// never inferred from the flag alone, so the STR seed can't be shown as
// "active" when it did not populate.
describe('Financials · Projections — Year-1 basis chip is driven by worker source tags', () => {
  it('shows "Active basis: Market / STR · Revert" on str_forecast; Revert drops the flag + STR-noted keys only', async () => {
    mockSources = {
      starting_occupancy: 'str_forecast',
      starting_adr: 'str_forecast',
      revenue_seed_from_str_forecast: 'str_forecast',
    };
    mockFieldOverrides = {
      revenue_seed_from_str_forecast: { value: true, note: 'STR market rates enabled from the Market tab' },
      starting_occupancy: { value: 0.692, note: STR_MARKET_OVERRIDE_NOTE },
      starting_adr: { value: 295, note: STR_MARKET_OVERRIDE_NOTE },
      mgmt_fee_pct: { value: 0.03, note: 'Analyst' }, // unrelated — must survive
    };
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.getByTestId('str-basis-chip')).toHaveTextContent('Active basis: Market / STR');
    expect(screen.queryByTestId('str-basis-unavailable')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Revert' }));
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    const [, body] = updateSpy.mock.calls[0] as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides).toEqual({ mgmt_fee_pct: { value: 0.03, note: 'Analyst' } });
    expect(engineRunSpy).toHaveBeenCalled(); // re-modeled
  });

  it('shows the honest "STR rates unavailable — using T-12 base" state on str_forecast_unavailable', () => {
    mockSources = {
      revenue_seed_from_str_forecast: 'str_forecast_unavailable',
      starting_occupancy: 't12_actual',
      starting_adr: 't12_actual',
    };
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.getByTestId('str-basis-unavailable')).toHaveTextContent('STR rates unavailable — using T-12 base');
    expect(screen.queryByTestId('str-basis-chip')).not.toBeInTheDocument();
  });

  it('renders no basis chip for analyst / seed sources', () => {
    mockSources = { starting_occupancy: 'analyst_override', starting_adr: 'seed' };
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.queryByTestId('str-basis-chip')).not.toBeInTheDocument();
    expect(screen.queryByTestId('str-basis-unavailable')).not.toBeInTheDocument();
  });
});
