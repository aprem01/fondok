/**
 * Partnership tab — canonical rebuild (FON-72, design-match).
 *
 * Contracts locked here:
 *
 *  1. ENGINE-SOURCED DOLLAR WATERFALL — the "Allocation of Projected Proceeds"
 *     table renders one row per `tier_allocations[]` entry (Return of Capital /
 *     Preferred / GP Catch-Up / Promote) with LP $, GP $ and total, straight
 *     from a single mocked worker `partnership` envelope (the real
 *     ``getEngineField`` is exercised). No fixtures, no prototype numbers.
 *
 *  2. RECONCILES BADGE — when the envelope's `reconciles` flag is true, the
 *     green "Reconciles" badge renders with the LP + GP = total sentence.
 *
 *  3. CATCH-UP TIER — the `catch_up` tier from `tier_allocations` (and the
 *     `catch_up_amount`) surfaces both as a dollar-waterfall row and as the
 *     typed "GP Catch-Up" tier in the Promote Waterfall.
 *
 *  4. UNGATED — the tab renders its content (no "unavailable" dead-end) whenever
 *     the engine output is present.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import type { EngineOutputsResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
}));

// The worker outputs under test — the whole tab reads from these.
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    partnership: {
      deal_id: 'deal-uuid-1',
      engine: 'partnership',
      status: 'complete',
      summary: '',
      outputs: {
        gp: { partner: 'GP', contributed_equity: 1_883_668, distributions: 4_583_668, irr: 0.284, equity_multiple: 2.43 },
        lp: { partner: 'LP', contributed_equity: 16_953_008, distributions: 26_753_008, irr: 0.176, equity_multiple: 1.58 },
        promote_amount: 3_000_000,
        promote_earned: 3_000_000,
        gp_cash_flows: [100_000, 200_000, 300_000, 400_000, 3_583_668],
        lp_cash_flows: [500_000, 600_000, 700_000, 800_000, 24_153_008],
        tier_allocations: [
          { label: 'Return of Capital', kind: 'return_of_capital', gp_amount: 1_883_668, lp_amount: 16_953_008, total_amount: 18_836_676 },
          { label: 'Preferred Return', kind: 'preferred', gp_amount: 200_000, lp_amount: 1_800_000, total_amount: 2_000_000 },
          { label: 'GP Catch-Up', kind: 'catch_up', gp_amount: 500_000, lp_amount: 0, total_amount: 500_000 },
          { label: 'Promote — above 15% LP IRR', kind: 'promote', gp_amount: 2_000_000, lp_amount: 8_000_000, total_amount: 10_000_000 },
        ],
        total_distributable: 31_336_676,
        reconciles: true,
        catch_up_amount: 500_000,
      },
      inputs: {},
      error: null,
      runtime_ms: 12,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
    returns: {
      deal_id: 'deal-uuid-1',
      engine: 'returns',
      status: 'complete',
      summary: '',
      outputs: { levered_irr: 0.198, equity_multiple: 1.66, hold_years: 5 },
      inputs: {},
      error: null,
      runtime_ms: 9,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
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
// STABLE identity — see debtTab.test for the full rationale: PartnershipTab has
// `useEffect(() => setOverrides(deal?.field_overrides ?? {}), [deal?.field_overrides])`,
// so a fresh `{}` per render loops the passive effect forever and hangs the test.
const mockDeal = { id: 'deal-uuid-1', keys: 132, field_overrides: {} };
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: mockDeal,
    status: null,
    loading: false,
    error: null,
    fromMock: false,
    refresh: refreshDealSpy,
  }),
}));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, running: false, error: null }),
}));

const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
    },
  };
});

// Trim the heavy chrome to nothing — the test only cares about the sub-tab bodies.
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import PartnershipTab from '@/components/project/PartnershipTab';

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  engineRunSpy.mockClear();
  refreshDealSpy.mockClear();
});

describe('PartnershipTab — canonical structure', () => {
  it('renders the three canonical sub-tabs (no "unavailable" dead-end)', () => {
    render(<PartnershipTab />);
    expect(screen.getByRole('tab', { name: 'Summary' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Waterfall' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Cash Flows' })).toBeInTheDocument();
    // Ungated: the old "Partnership Engine unavailable" card must be gone.
    expect(screen.queryByText(/unavailable/i)).not.toBeInTheDocument();
    // Manual-inputs banner (canonical) is present.
    expect(screen.getByText(/Manual inputs · current release/i)).toBeInTheDocument();
  });
});

describe('PartnershipTab — dollar waterfall from tier_allocations', () => {
  it('renders a row per tier_allocation with the engine dollar amounts', () => {
    render(<PartnershipTab />);
    // Summary shows the "Waterfall Allocation Preview" dollar waterfall.
    expect(screen.getByText('Waterfall Allocation Preview')).toBeInTheDocument();

    // One row per typed tier (labels straight from the engine envelope).
    expect(screen.getAllByText('Return of Capital').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Preferred Return').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Promote — above 15% LP IRR').length).toBeGreaterThan(0);

    // Engine dollar amounts, not prototype placeholders.
    expect(screen.getAllByText('$18,836,676').length).toBeGreaterThan(0); // ROC total
    expect(screen.getAllByText('$31,336,676').length).toBeGreaterThan(0); // total distributable
  });

  it('renders the "Reconciles" badge with the LP + GP = total sentence', () => {
    render(<PartnershipTab />);
    expect(screen.getByText('Reconciles')).toBeInTheDocument();
    expect(
      screen.getByText(/LP \$26,753,008 \+ GP \$4,583,668 = \$31,336,676 total deal distributions ✓/),
    ).toBeInTheDocument();
  });

  it('surfaces the catch-up tier both as a dollar row and a typed waterfall tier', () => {
    render(<PartnershipTab />);
    // Summary allocation row.
    expect(screen.getAllByText('GP Catch-Up').length).toBeGreaterThan(0);

    // Waterfall sub-tab: the typed Promote Waterfall carries the Catch-Up tier.
    fireEvent.click(screen.getByRole('tab', { name: 'Waterfall' }));
    expect(screen.getByText('Allocation of Projected Proceeds')).toBeInTheDocument();
    expect(screen.getByText('Promote Waterfall')).toBeInTheDocument();
    expect(screen.getByText('Tier I — Return of Capital')).toBeInTheDocument();
    expect(screen.getByText('Tier II — Preferred Return')).toBeInTheDocument();
    expect(screen.getByText('Tier III — GP Catch-Up')).toBeInTheDocument();
  });
});

describe('PartnershipTab — partner returns + cash flows', () => {
  it('renders the Deal / LP / GP partner-returns cards with promote', () => {
    render(<PartnershipTab />);
    expect(screen.getByText('Partner Returns')).toBeInTheDocument();
    expect(screen.getByText('Deal level')).toBeInTheDocument();
    expect(screen.getByText('LP investors')).toBeInTheDocument();
    expect(screen.getByText('GP / sponsor')).toBeInTheDocument();
    expect(screen.getByText('Promote / carry earned')).toBeInTheDocument();
    // Deal-level IRR comes from the Returns engine (levered_irr 0.198 → 19.8%).
    expect(screen.getByText('19.8%')).toBeInTheDocument();
  });

  it('renders the contributions-vs-distributions cash-flow grid', () => {
    render(<PartnershipTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'Cash Flows' }));
    expect(screen.getByText('Partner Cash Flows')).toBeInTheDocument();
    expect(screen.getByText('GP contribution')).toBeInTheDocument();
    expect(screen.getByText('LP distribution')).toBeInTheDocument();
    // Reconciliation cards.
    expect(screen.getByText('Contributions, distributions and profit')).toBeInTheDocument();
    expect(screen.getByText('Invested equity')).toBeInTheDocument();
  });
});
