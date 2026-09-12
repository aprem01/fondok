/**
 * IC Memo — a refused diligence row is disclosed, never priced (FON-54 §2).
 *
 * Sam 2026-09-11: an OM's %-of-revenue proforma column
 * (`broker_proforma.rooms_revenue_pct` = 1.0) was matched to the DOLLAR
 * concept, read as $1 against a $9,332,100 T-12 line and reported as a 100%
 * understatement. The worker now refuses that row before any comparison
 * (`unit_unknown`) and hands it to the memo as an EXCLUDED raw field: no
 * broker-vs-T-12 numbers, no delta, no percentage.
 *
 * This file pins what IC Memo does with such a row — it belongs under
 * Technical detail with its reason, it renders `—` where the comparison would
 * be, and the concept's headline variance is computed from the admitted dollar
 * row alone. Kept separate from `icMemoTab.test.tsx` on purpose: that file
 * pins the FON-54a consolidation contract and this one pins the refusal
 * channel hung beside it.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import type { VarianceFlag } from '@/lib/varianceData';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
}));

const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    capital: {
      deal_id: 'deal-uuid-1', engine: 'capital', status: 'complete', summary: '',
      outputs: { purchase_price: 34_000_000, entry_cap_rate: 0.075, equity_amount: 17_000_000, debt_amount: 26_000_000 },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    returns: {
      deal_id: 'deal-uuid-1', engine: 'returns', status: 'complete', summary: '',
      outputs: { levered_irr: 0.26, equity_multiple: 2.5, hold_years: 5 },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    expense: {
      deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 1, noi: 2_550_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
  },
} as unknown as EngineOutputsResponse;

vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});

const fx = vi.hoisted(() => ({
  fieldOverrides: {} as Record<string, unknown>,
  flags: [] as unknown[],
  refreshDealSpy: vi.fn(),
}));

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', name: 'Kimpton Angler', city: 'Miami Beach, FL', keys: 132, brand: 'Kimpton', field_overrides: fx.fieldOverrides },
    status: null, loading: false, error: null, fromMock: false, refresh: fx.refreshDealSpy,
  }),
}));

vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({
    flags: fx.flags,
    critical: (fx.flags as { severity: string }[]).filter((f) => f.severity === 'CRITICAL').length,
    warn: 0, info: 0, note: null, loading: false, error: null,
  }),
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
      scenarios: { ...actual.api.scenarios, list: vi.fn(async () => []), compare: vi.fn(async () => ({ deal_id: 'deal-uuid-1', base_scenario_id: null, scenarios: [] })) },
    },
  };
});

import ICMemoTab from '@/components/project/ICMemoTab';
import type { Project } from '@/lib/mockData';

const PROJECT = { id: 0, name: 'Kimpton Hotel' } as unknown as Project;

/**
 * The consolidated rooms-revenue flag exactly as the worker now emits it: the
 * dollar row is the comparison, the OM's %-of-revenue sibling is an excluded
 * row carrying `unit_unknown` and no numbers of its own.
 */
const ROOMS_WITH_REFUSED_PCT = {
  flag_id: 'BROKER_VS_T12_NOI_VARIANCE-0', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'CRITICAL',
  metric: 'rooms_revenue', field_label: 'Rooms revenue',
  broker_value: 9_708_984, t12_value: 9_332_100, variance_abs: -376_884, variance_pct: -0.0404,
  format: 'currency', broker_overstates: true, noi_impact_usd: 0,
  explanation: 'Rooms revenue: broker proforma $9,708,984 vs T-12 actual $9,332,100 — broker overstates the T-12 by 4.0%.',
  recommended_action: 'Review the cited T-12 line and re-underwrite the broker assumption.',
  source_documents: [{ document_id: 'deal-uuid-1', page: 14, field: 'rooms_revenue' }],
  concept: 'rooms_revenue', impact_basis: 'revenue',
  raw_fields: [
    {
      field: 'broker_proforma.rooms_revenue_usd', rule_id: 'BROKER_VS_T12_NOI_VARIANCE', severity: 'Critical',
      broker: 9_708_984, actual: 9_332_100, source_page: 14, source_doc_type: 'OM',
    },
    {
      field: 'broker_proforma.rooms_revenue_pct', severity: 'Info',
      broker: 1, actual: null, source_page: 14, source_doc_type: 'OM', source_document: 'Anglers OM.pdf',
      excluded_reason:
        'broker_proforma.rooms_revenue_pct declares pct but rooms_revenue is measured in usd — unit not established',
      reason: 'unit_unknown',
    },
  ],
} as unknown as VarianceFlag;

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  fx.fieldOverrides = {};
  fx.flags = [ROOMS_WITH_REFUSED_PCT];
});

describe('ICMemoTab — a unit_unknown diligence row is disclosed, not priced', () => {
  it('renders the refused row under Technical detail with its reason and no percentage', () => {
    render(<ICMemoTab project={PROJECT} />);

    // Collapsed: the refused path is not on screen at all.
    expect(screen.queryByText(/rooms_revenue_pct/)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('Technical detail'));
    const detail = screen.getByText(/concept rooms_revenue · impact basis revenue/);
    const text = detail.textContent ?? '';

    // The refused row is listed, with the worker's own reason, marked excluded.
    expect(text).toContain('1 raw field consolidated · 1 excluded');
    expect(text).toContain('excluded · field broker_proforma.rooms_revenue_pct');
    expect(text).toContain('unit not established');
    expect(text).toContain('source OM Anglers OM.pdf');

    // …and it carries no comparison: no "broker X vs T-12 Y" line and no
    // percentage of its own. The only "vs T-12" line is the dollar row.
    const pctLine = text.split('\n').find((l) => l.includes('rooms_revenue_pct')) ?? '';
    expect(pctLine).not.toContain('vs T-12');
    expect(pctLine).not.toMatch(/%/);
    expect(text).toContain(
      'rule BROKER_VS_T12_NOI_VARIANCE · field broker_proforma.rooms_revenue_usd · broker $9,708,984 vs T-12 $9,332,100 · p.14',
    );
  });

  it('computes the concept headline from the admitted dollar row alone — never a 100% understatement', () => {
    render(<ICMemoTab project={PROJECT} />);

    // The 4.0% dollar-vs-dollar variance, not the -100% the $1 would have made.
    expect(screen.getByText('Rooms revenue — broker overstates T-12 by 4.0%')).toBeInTheDocument();
    expect(screen.queryByText(/by 100.0%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/understates/)).not.toBeInTheDocument();
  });

  it('shows a dash, not a number, where a refused row would have a T-12 figure', () => {
    render(<ICMemoTab project={PROJECT} />);
    fireEvent.click(screen.getByText('Technical detail'));
    const text = screen.getByText(/concept rooms_revenue · impact basis revenue/).textContent ?? '';
    // The excluded line reports the raw OM value it refused and nothing else —
    // the T-12 side it was never compared against is absent, not invented.
    expect(text).toContain('excluded · field broker_proforma.rooms_revenue_pct · value $1');
    expect(text).not.toContain('$9,332,100 vs');
  });
});
