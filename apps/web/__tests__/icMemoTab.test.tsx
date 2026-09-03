/**
 * IC Memo tab — canonical rebuild (FON-54 / FON-72): the EDITABLE decision
 * workspace. These tests lock the four workspace editors the canonical design
 * introduces on top of the read-only one-pager:
 *
 *  1. VERDICT OVERRIDE — the IC recommendation is a selectable dropdown that
 *     DEFAULTS to the deterministic, numbers-grounded verdict and, when the
 *     analyst overrides it, PATCHes `field_overrides.memo_recommendation_override`
 *     via `api.deals.update`.
 *
 *  2. EDITABLE THESIS — Edit toggles the thesis paragraph into an editable
 *     state ("Done editing"); the "narrative only" guarantee is shown.
 *
 *  3. HIGHLIGHTS — "+ Add point" appends a highlight and persists the list to
 *     `field_overrides.memo_highlights`; the ••• "Move down" action reorders it.
 *
 *  4. DILIGENCE — an open broker-vs-T-12 variance flag renders with a Resolve
 *     action that flips the item's status and clears the open-critical summary.
 *
 * NOTE: written per the task but NOT run here (tsc-only verification).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import type { VarianceFlag } from '@/lib/varianceData';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
}));

// Live Base Case outputs — the whole memo reads from these.
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    capital: {
      deal_id: 'deal-uuid-1', engine: 'capital', status: 'complete', summary: '',
      outputs: {
        purchase_price: 34_000_000, price_per_key: 257_576, entry_cap_rate: 0.075,
        total_capital: 43_000_000, equity_amount: 17_000_000, debt_amount: 26_000_000,
        uses: [
          { label: 'Purchase Price', amount: 34_000_000 },
          { label: 'Renovation Budget', amount: 4_620_000 },
        ],
      },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    returns: {
      deal_id: 'deal-uuid-1', engine: 'returns', status: 'complete', summary: '',
      outputs: {
        levered_irr: 0.26, unlevered_irr: 0.13, equity_multiple: 2.5,
        hold_years: 5, gross_sale_price: 52_000_000,
      },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    expense: {
      deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 1, noi: 2_550_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    revenue: {
      deal_id: 'deal-uuid-1', engine: 'revenue', status: 'complete', summary: '',
      outputs: { years: [{ year: 1, revpar: 204, adr: 280, occupancy: 0.73, total_revenue: 14_000_000 }], total_revenue_cagr: 0.03 },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    debt: {
      deal_id: 'deal-uuid-1', engine: 'debt', status: 'complete', summary: '',
      outputs: { year_one_dscr: 1.59, year_one_debt_yield: 0.11, interest_rate: 0.0766 },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
  },
} as unknown as EngineOutputsResponse;

// Keep the REAL getEngineField; only swap the hook to serve our fixture.
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});

const refreshDealSpy = vi.fn();
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', name: 'Kimpton Angler', city: 'Miami Beach, FL', keys: 132, brand: 'Kimpton', field_overrides: {} },
    status: null, loading: false, error: null, fromMock: false, refresh: refreshDealSpy,
  }),
}));

const CRITICAL_FLAG = {
  flag_id: 'f1', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'CRITICAL',
  metric: 'total_revenue', field_label: 'Total Revenue',
  noi_impact_usd: 180_000, explanation: 'Broker materials report $12.9M vs $12.3M in the TTM statements.',
  recommended_action: 'Review the cited T-12 line and re-underwrite the broker assumption.',
} as unknown as VarianceFlag;

vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({ flags: [CRITICAL_FLAG], critical: 1, warn: 0, info: 0, note: null, loading: false, error: null }),
}));

// api surface — spy the field_overrides PATCH; serve empty scenarios.
const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
      scenarios: { ...actual.api.scenarios, list: vi.fn(async () => []), compare: vi.fn(async () => ({ deal_id: 'deal-uuid-1', base_scenario_id: null, scenarios: [] })) },
    },
  };
});

import ICMemoTab from '@/components/project/ICMemoTab';
import type { Project } from '@/lib/mockData';

const PROJECT = { id: 0, name: 'Kimpton Hotel' } as unknown as Project;

function lastOverrides(): Record<string, unknown> {
  const call = updateSpy.mock.calls[updateSpy.mock.calls.length - 1] as unknown[];
  const patch = call?.[1] as { field_overrides?: Record<string, unknown> } | undefined;
  return patch?.field_overrides ?? {};
}

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  refreshDealSpy.mockClear();
});

describe('ICMemoTab — IC recommendation verdict override', () => {
  it('defaults to the derived verdict and persists an override', async () => {
    render(<ICMemoTab project={PROJECT} />);
    // Strong returns → the deterministic default is "Proceed".
    expect(screen.getByText('Proceed')).toBeInTheDocument();

    // Open the recommendation dropdown and override the verdict.
    fireEvent.click(screen.getByText('Proceed'));
    fireEvent.click(screen.getByText('Do Not Proceed'));

    // The banner now shows the overridden verdict…
    expect(screen.getByText('Do Not Proceed')).toBeInTheDocument();
    // …and the override is persisted through field_overrides.
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    expect(lastOverrides().memo_recommendation_override).toBe('Do Not Proceed');
  });
});

describe('ICMemoTab — editable investment thesis', () => {
  it('toggles into an editable state and shows the narrative-only guarantee', () => {
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.getByText(/Narrative only — editing this text never changes an underwriting assumption\./)).toBeInTheDocument();

    // Edit → the affordance flips to "Done editing".
    fireEvent.click(screen.getByText('Edit'));
    expect(screen.getByText('Done editing')).toBeInTheDocument();
    // A Regenerate affordance is present alongside.
    expect(screen.getByText('Regenerate')).toBeInTheDocument();
  });
});

describe('ICMemoTab — highlights: add + reorder', () => {
  it('appends a highlight and persists the list', async () => {
    render(<ICMemoTab project={PROJECT} />);
    // The first "+ Add point" belongs to Key highlights (rendered before risks).
    const addButtons = screen.getAllByText('+ Add point');
    fireEvent.click(addButtons[0]);

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const points = lastOverrides().memo_highlights as { t: string }[];
    expect(Array.isArray(points)).toBe(true);
    // 5 derived highlights + 1 appended.
    expect(points.length).toBe(6);
    expect(points[points.length - 1].t).toMatch(/New highlight/);
  });

  it('reorders a highlight via the ••• Move down action', async () => {
    render(<ICMemoTab project={PROJECT} />);
    // First highlight row's overflow menu.
    const more = screen.getAllByTitle('More')[0];
    fireEvent.click(more);
    fireEvent.click(screen.getByText('Move down'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const points = lastOverrides().memo_highlights as { t: string }[];
    // The original first highlight (levered IRR) moved into slot 2.
    expect(points[1].t).toMatch(/Levered IRR/);
  });
});

describe('ICMemoTab — diligence resolve action', () => {
  it('resolves an open critical variance item', () => {
    render(<ICMemoTab project={PROJECT} />);
    // The open critical item surfaces its severity + Resolve action.
    expect(screen.getByText('Critical')).toBeInTheDocument();
    expect(screen.getByText(/critical diligence item.*remain open/)).toBeInTheDocument();

    fireEvent.click(screen.getByText('Resolve'));

    // Status flips to Resolved and the summary clears.
    expect(screen.getByText('Resolved')).toBeInTheDocument();
    expect(screen.getByText('All critical diligence items resolved')).toBeInTheDocument();
  });
});
