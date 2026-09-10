/**
 * Investment tab — canonical rebuild (design-match) + Move-2 step 4.
 *
 * Contracts locked here:
 *
 *  1. ENGINE-SOURCED RENDER — the 5 KPI tiles, the Sources & Uses balance
 *     banner, and the Transaction Timeline all resolve from a single mocked
 *     worker engine-outputs envelope (the real ``getEngineField`` is exercised).
 *     No fixtures, no prototype numbers.
 *
 *  2. PROVIDER-FREE — InvestmentTab renders with NO <AssumptionsProvider>
 *     present. The old ``ctx &&`` gate on Sources & Uses is gone, so the S&U
 *     view renders from engine output alone (Move-2 step 4 — Investment is off
 *     the page assumptions provider).
 *
 *  3. CANONICAL SAVE PATH — editing an assumption (Purchase Price) PATCHes
 *     ``field_overrides`` via api.deals.update — the same path Deal Summary
 *     already used — NOT the local assumptionsStore. This fixes the dual-store
 *     data-integrity bug.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse, TimelineResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
}));

// The worker outputs under test — the whole tab reads from these.
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    capital: {
      deal_id: 'deal-uuid-1',
      engine: 'capital',
      status: 'complete',
      summary: '',
      outputs: {
        purchase_price: 34_000_000,
        price_per_key: 257_576,
        entry_cap_rate: 0.075,
        total_capital_usd: 43_000_000,
        total_capital_per_key: 325_758,
        equity_amount: 17_000_000,
        debt_amount: 26_000_000,
        uses: [
          { label: 'Purchase Price', amount: 34_000_000, pct: 0.79 },
          { label: 'Closing Costs', amount: 680_000, pct: 0.016 },
          { label: 'Renovation Budget', amount: 4_620_000, pct: 0.107 },
          { label: 'Total Uses', amount: 43_000_000, pct: 1, is_total: true },
        ],
        sources: [
          { label: 'Senior Loan', amount: 26_000_000, pct: 0.6 },
          { label: 'Equity', amount: 17_000_000, pct: 0.4 },
          { label: 'Total Sources', amount: 43_000_000, pct: 1, is_total: true },
        ],
      },
      inputs: {},
      error: null,
      runtime_ms: 10,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
    returns: {
      deal_id: 'deal-uuid-1',
      engine: 'returns',
      status: 'complete',
      summary: '',
      outputs: {
        gross_sale_price: 52_000_000,
        exit_cap_rate: 0.07,
        terminal_noi: 3_640_000,
        selling_costs: 520_000,
        hold_years: 5,
      },
      inputs: {},
      error: null,
      runtime_ms: 9,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
    expense: {
      deal_id: 'deal-uuid-1',
      engine: 'expense',
      status: 'complete',
      summary: '',
      outputs: { years: [{ year: 1, noi: 2_550_000 }] },
      inputs: {},
      error: null,
      runtime_ms: 5,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

const TIMELINE = {
  deal_id: 'deal-uuid-1',
  close_date: '2027-03-31',
  exit_date: '2032-03-31',
  stabilization_date: '2029-06-30',
  events: [
    { event: 'Hotel Purchase', start: '2027-03-31', duration_months: 0, finish: '2027-03-31', basis: 'derived' },
    { event: 'Renovation', start: '2027-06-30', duration_months: 12, finish: '2028-06-30', basis: 'assumption' },
    { event: 'Stabilized (FTM NOI, Value)', start: '2029-06-30', duration_months: 0, finish: '2029-06-30', basis: 'derived' },
    { event: 'Senior Loan Maturity', start: '2032-03-31', duration_months: 0, finish: '2032-03-31', basis: 'derived' },
  ],
} as unknown as TimelineResponse;

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

vi.mock('@/lib/hooks/useHistoricalBaseline', () => ({
  useHistoricalBaseline: () => ({ baseline: null }),
}));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, running: false, error: null }),
}));

// api surface — spy on the field_overrides PATCH; serve the timeline.
const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
const timelineSpy = vi.fn(async () => TIMELINE);
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
      engines: { ...actual.api.engines, timeline: (...a: unknown[]) => timelineSpy(...(a as [])) },
    },
  };
});

// Trim the heavy chrome to nothing — the test only cares about the sub-tab bodies.
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/project/CapexPlanPanel', () => ({
  default: () => null,
  DEFAULT_CAPEX_PLAN: {},
}));
vi.mock('@/components/project/HistoricalBaselinePanel', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

// Phase 4.4 — the deal's refusal codes ride the assumption_sources payload
// (`reasons[key]`, a bare ReasonCode) beside the source tags. EMPTY unless a
// test sets one, which is exactly what this provider-free render resolved to
// before, so every pre-existing expectation below is untouched.
let mockReasons: Record<string, string> = {};
vi.mock('@/lib/hooks/useDealProvenance', () => ({
  useSource: (key: string | undefined) =>
    key && mockReasons[key] ? { source: '', value: null, reason: mockReasons[key] } : null,
}));

import InvestmentTab from '@/components/project/InvestmentTab';
import { REASONS } from '@/lib/ontology/reasons.generated';

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  timelineSpy.mockClear();
  engineRunSpy.mockClear();
  refreshDealSpy.mockClear();
  mockReasons = {};
});

describe('InvestmentTab — engine-sourced KPI tiles (no provider present)', () => {
  it('renders all 5 KPI tiles from the mocked engine outputs', () => {
    // NOTE: rendered bare — NO <AssumptionsProvider>. Proves Move-2 step 4.
    render(<InvestmentTab />);
    // KPI labels (some also appear as section-row labels → use getAllByText).
    expect(screen.getByText('Total Cost Basis')).toBeInTheDocument();
    expect(screen.getByText('Required Equity')).toBeInTheDocument();
    expect(screen.getAllByText('Purchase Price').length).toBeGreaterThan(0);
    expect(screen.getByText('Renovation / PIP')).toBeInTheDocument();
    expect(screen.getAllByText('Gross Exit Value').length).toBeGreaterThan(0);

    // Values are the $M engine figures, not prototype placeholders.
    expect(screen.getByText('$43.00M')).toBeInTheDocument(); // total cost basis
    expect(screen.getByText('$34.00M')).toBeInTheDocument(); // purchase price
    expect(screen.getByText('$4.62M')).toBeInTheDocument();  // renovation / PIP
    expect(screen.getByText('$17.00M')).toBeInTheDocument(); // required equity
    expect(screen.getByText('$52.00M')).toBeInTheDocument(); // gross exit value
  });
});

describe('InvestmentTab — Sources & Uses (ctx gate removed)', () => {
  it('renders the in-balance banner from engine sources/uses with no provider', () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Sources & Uses'));

    // Balance banner resolves from the engine totals (43M == 43M → In balance).
    expect(screen.getByText('In balance')).toBeInTheDocument();
    expect(
      screen.getByText(/Required equity is the plug — every other line is owned by/i),
    ).toBeInTheDocument();
    // The canonical Amount / Key / % column headers are present (Uses + Sources).
    expect(screen.getAllByText('/ Key').length).toBe(2);
  });

  it('does NOT render the removed editable LTV field', () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Sources & Uses'));
    // LTV is Debt-owned; it must not appear as an editable field here.
    expect(screen.queryByText('LTV')).not.toBeInTheDocument();
  });
});

describe('InvestmentTab — Transaction Timeline', () => {
  it('renders the rail, hold caption and the "Owned by" detail column', async () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByText('Timeline'));

    await waitFor(() => expect(timelineSpy).toHaveBeenCalled());

    expect(await screen.findByText('Transaction Timeline')).toBeInTheDocument();
    // "X-year hold · <close> → <exit>" caption.
    expect(screen.getByText(/5-year hold/)).toBeInTheDocument();
    // A milestone from the endpoint.
    expect(screen.getAllByText('Renovation').length).toBeGreaterThan(0);
    // Detail table "Owned by" column + a derived owner label.
    expect(screen.getByText('Owned by')).toBeInTheDocument();
    expect(screen.getByText('Investment assumption')).toBeInTheDocument();
    expect(screen.getByText('Linked from Debt')).toBeInTheDocument();
  });
});

describe('InvestmentTab — canonical save path (field_overrides, not local store)', () => {
  it('editing Purchase Price PATCHes field_overrides via api.deals.update', async () => {
    render(<InvestmentTab />);

    // The Acquisition row shows the editable Purchase Price ($34,000,000).
    const cell = screen.getByText('$34,000,000');
    fireEvent.click(cell);

    // An input appears (draft prefilled) — change it and Save.
    const input = document.querySelector('input[type="number"]') as HTMLInputElement;
    expect(input).toBeTruthy();
    fireEvent.change(input, { target: { value: '35000000' } });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides.purchase_price).toBe(35_000_000);
  });
});

// ── Phase 4.4 — the bare dash learns to say why ──────────────────────────
// Ongoing Capex → FF&E Reserve is the one Investment dash attributable to a
// single assumption key (`ffe_reserve_pct`), so it reads the worker's
// `assumption_sources.reasons` entry and carries it on hover. Everything else
// on the tab is left exactly as it was.
describe('InvestmentTab — the FF&E Reserve dash carries its refusal code', () => {
  /** The value cell of the section row with this label. */
  function valueCell(label: string): HTMLElement {
    const labelEl = screen.getAllByText(label)[0];
    const row = labelEl.closest('div') as HTMLElement; // dot · label · link | value
    return row.lastElementChild as HTMLElement;
  }

  it('renders the reason from the worker code, still showing the em dash', () => {
    mockReasons = { ffe_reserve_pct: 'no_document' };
    render(<InvestmentTab />);
    const cell = valueCell('FF&E Reserve');
    const refusal = cell.querySelector('[data-refused]') as HTMLElement;
    expect(refusal).toBeTruthy();
    expect(refusal.getAttribute('data-refused')).toBe('no_document');
    expect(refusal.getAttribute('aria-label')).toBe(REASONS.no_document.label);
    expect(cell.textContent).toBe('—');
  });

  it('with the code ABSENT renders a bare dash — no wrapper, no tooltip', () => {
    mockReasons = {}; // every worker build today
    render(<InvestmentTab />);
    const cell = valueCell('FF&E Reserve');
    expect(cell.querySelector('[data-refused]')).toBeNull();
    expect(cell.textContent).toBe('—');
  });

  it('leaves the unwired Ongoing Capex dashes exactly as they were', () => {
    mockReasons = { ffe_reserve_pct: 'no_source' };
    render(<InvestmentTab />);
    for (const label of ['ROI Projects', 'Other Recurring Capex', 'Transfer Tax']) {
      const cell = valueCell(label);
      expect(cell.querySelector('[data-refused]')).toBeNull();
      expect(cell.textContent).toBe('—');
    }
  });
});

