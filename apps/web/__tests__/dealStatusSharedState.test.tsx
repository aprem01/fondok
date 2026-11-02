/**
 * Wave 5 round 2 (review I2) — one source of truth for the deal's status on
 * the deal page. The header status menu and the IC Memo card each read the
 * deal through `useDeal`; a status write from either one re-reads the deal
 * EVERYWHERE (`notifyDealChanged`), and every write carries the status the
 * control was showing (`expected_status`), so a stale "Mark as IC Ready" can
 * never downgrade an IC decision.
 *
 * Real `useDeal`, real `DealStatusMenu`, real `ICMemoTab`; only the worker
 * (api.deals.get / status / setStatus) is a fake holding one deal row.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within, act } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'deal-uuid-1' }) }));

const engine = (name: string, outputs: Record<string, unknown>) => ({
  deal_id: 'deal-uuid-1', engine: name, status: 'complete', summary: '', outputs, inputs: {},
  error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
});
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  ic_status_gate: { ok: true, reason: null, failed_engines: [] },
  engines: {
    capital: engine('capital', { purchase_price: 34_000_000, price_per_key: 257_576, entry_cap_rate: 0.075 }),
    returns: engine('returns', { levered_irr: 0.26, unlevered_irr: 0.13, equity_multiple: 2.5, hold_years: 5 }),
    expense: engine('expense', { years: [{ year: 1, noi: 2_550_000 }] }),
    revenue: engine('revenue', { years: [{ year: 1, revpar: 204, adr: 280, occupancy: 0.73, total_revenue: 14_000_000 }] }),
    debt: engine('debt', { year_one_dscr: 1.59, year_one_debt_yield: 0.11, interest_rate: 0.0766 }),
  },
} as unknown as EngineOutputsResponse;

// One unresolved critical item: "Mark as IC Ready" opens once it is
// acknowledged (the card's own checklist; the worker gates on the run).
const CRITICAL = {
  flag_id: 'F-0', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'CRITICAL',
  metric: 'noi', field_label: 'NOI', broker_value: 5_200_000, t12_value: 4_181_000,
  variance_abs: -1_019_000, variance_pct: -0.2437, format: 'currency', broker_overstates: true,
  noi_impact_usd: 1_019_000, explanation: 'NOI: broker overstates the T-12.',
  recommended_action: 'Review.', source_documents: [], concept: 'noi', impact_basis: 'noi',
  raw_fields: [],
};

vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});
vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({ flags: [CRITICAL], critical: 1, warn: 0, info: 0, note: null, loading: false, error: null }),
}));

const server = vi.hoisted(() => ({
  status: 'In Progress',
  setStatus: vi.fn(),
  get: vi.fn(),
  toast: vi.fn(),
}));

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: server.toast }) }));
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
        get: (...a: unknown[]) => server.get(...a),
        status: async () => null,
        update: vi.fn(async () => ({ id: 'deal-uuid-1' })),
        setStatus: (...a: unknown[]) => server.setStatus(...a),
      },
      scenarios: {
        ...actual.api.scenarios,
        list: vi.fn(async () => []),
        compare: vi.fn(async () => ({ deal_id: 'deal-uuid-1', base_scenario_id: null, scenarios: [] })),
      },
    },
  };
});

import ICMemoTab from '@/components/project/ICMemoTab';
import DealStatusMenu from '@/components/project/DealStatusMenu';
import { useDeal } from '@/lib/hooks/useDeal';
import { WorkerError } from '@/lib/api';
import { isProjectStatus } from '@/lib/catalogs';
import { hasCompletedModelRun } from '@/lib/dealStatus';
import type { Project } from '@/lib/project';

const PROJECT = { id: 0, name: 'Kimpton Hotel' } as unknown as Project;
const row = () => ({
  id: 'deal-uuid-1', tenant_id: 't', name: 'Harbor', city: 'Boston', keys: 100, service: null, brand: null,
  status: server.status, deal_stage: null, risk: null, ai_confidence: null, lifecycle: 'active',
  field_overrides: {}, created_at: '2026-10-01', updated_at: '2026-10-01',
});

/** The deal page's header pill — the page's own `useDeal`, as in page.tsx. */
function Header() {
  const { deal, refresh } = useDeal('deal-uuid-1');
  if (!deal) return null;
  return (
    <DealStatusMenu
      dealId="deal-uuid-1"
      status={isProjectStatus(deal.status) ? deal.status : null}
      hasCompletedRun={hasCompletedModelRun(OUTPUTS)}
      onChanged={() => refresh()}
    />
  );
}

beforeEach(() => {
  server.status = 'In Progress';
  server.get.mockReset().mockImplementation(async () => row());
  server.setStatus.mockReset().mockImplementation(async (_id: string, next: string, expected?: string) => {
    if (expected != null && expected !== server.status) {
      throw new WorkerError('409', 409, JSON.stringify({ detail: {
        code: 'status_changed', current: server.status,
        message: `The deal's status changed to ${server.status} since this page loaded. Nothing was changed — check the status and try again.`,
      } }));
    }
    server.status = next;
    return row();
  });
  server.toast.mockReset();
});
afterEach(cleanup);

async function renderPage() {
  render(<><Header /><ICMemoTab project={PROJECT} /></>);
  await screen.findByRole('button', { name: 'Deal status: In Progress' });
  await screen.findByRole('button', { name: 'Mark as IC Ready' });
  fireEvent.click(screen.getByText('Acknowledge the unresolved critical items and proceed to committee'));
}

describe('header pill and IC Memo card share the deal status', () => {
  it('Mark as IC Ready on the IC Memo card updates the header pill', async () => {
    await renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Mark as IC Ready' }));
    await screen.findByRole('button', { name: 'Deal status: IC Ready' });
    expect(server.setStatus).toHaveBeenCalledWith('deal-uuid-1', 'IC Ready', 'In Progress');
    expect(screen.getByText('✓ IC Ready · 1 critical item unresolved')).toBeInTheDocument();
  });

  it('IC Approved from the header removes the IC Memo toggle — it can no longer downgrade the decision', async () => {
    await renderPage();
    fireEvent.click(screen.getByRole('button', { name: 'Deal status: In Progress' }));
    fireEvent.click(within(screen.getByRole('menu')).getByRole('menuitemradio', { name: /^IC Approved/ }));
    await screen.findByText('Committee decision: IC Approved');
    expect(server.setStatus).toHaveBeenCalledWith('deal-uuid-1', 'IC Approved', 'In Progress');
    expect(screen.queryByRole('button', { name: 'Mark as IC Ready' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Undo IC Ready' })).toBeNull();
    expect(server.status).toBe('IC Approved');
  });

  it('a card left stale by a change made elsewhere is refused by the worker and catches up', async () => {
    await renderPage();
    // Someone else (another tab) records the committee decision.
    server.status = 'IC Not Approved';
    fireEvent.click(screen.getByRole('button', { name: 'Mark as IC Ready' }));
    await waitFor(() => expect(server.toast).toHaveBeenCalledWith(
      "Couldn't change the status — The deal's status changed to IC Not Approved since this page loaded. Nothing was changed — check the status and try again.",
      { type: 'error' },
    ));
    expect(server.status).toBe('IC Not Approved');
    // Both controls re-read the deal and show the decision.
    await screen.findByText('Committee decision: IC Not Approved');
    await screen.findByRole('button', { name: 'Deal status: IC Not Approved' });
  });

  it('a status change re-reads every useDeal for that deal, and only that deal', async () => {
    function Probe({ id }: { id: string }) { useDeal(id); return null; }
    render(<><Probe id="deal-uuid-1" /><Probe id="deal-uuid-1" /><Probe id="deal-uuid-2" /></>);
    await waitFor(() => expect(server.get).toHaveBeenCalledTimes(3));
    const { notifyDealChanged } = await import('@/lib/dealEvents');
    act(() => notifyDealChanged('deal-uuid-1'));
    await waitFor(() => expect(server.get).toHaveBeenCalledTimes(5));
    expect(server.get.mock.calls.slice(3).map((c) => c[0])).toEqual(['deal-uuid-1', 'deal-uuid-1']);
  });
});
