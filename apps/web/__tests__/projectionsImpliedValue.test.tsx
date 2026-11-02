/**
 * Wave 5 (Sam #1) — the Projections "Implied Value" row renders on the
 * Returns engine's basis: each operating year's Cash NOI ÷ the engine's exit
 * cap, and the Exit Year column is the engine's gross sale price itself. The
 * old EBITDA-capitalised "Implied Exit Value" row is gone.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

const HOLD_YEARS = 2;
const fx2 = vi.hoisted(() => ({ returns: {} as Record<string, unknown> }));

const expYear = (i: number) => ({
  year: i + 1,
  total_revenue: 12_000_000,
  dept_expenses: { rooms: 2_000_000, food_beverage: 1_500_000, other_operated: 100_000, total: 3_600_000 },
  undistributed: { total: 2_400_000 },
  gop: 6_000_000,
  mgmt_fee: 360_000,
  fixed_charges: { property_taxes: 700_000, insurance: 300_000, rent: 0, other_fixed: 0, total: 1_000_000 },
  noi_institutional: 4_640_000 + i * 100_000,
  ffe_reserve: 480_000,
  noi: 4_160_000 + i * 100_000,
});

function buildOutputs(): EngineOutputsResponse {
  const engine = (name: string, outputs: Record<string, unknown>) => ({
    deal_id: 'deal-uuid-1', engine: name, status: 'complete', summary: '',
    outputs, inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
  });
  return {
    deal_id: 'deal-uuid-1',
    engines: {
      revenue: engine('revenue', {
        years: Array.from({ length: HOLD_YEARS }, (_, i) => ({
          year: i + 1, occupancy: 0.75, adr: 300, revpar: 225, rooms_revenue: 9_000_000,
          fb_revenue: 2_500_000, other_revenue: 500_000, total_revenue: 12_000_000,
        })),
        projection_calendar_years: [2025, 2026],
      }),
      expense: engine('expense', { years: Array.from({ length: HOLD_YEARS }, (_, i) => expYear(i)) }),
      returns: engine('returns', fx2.returns),
    },
  } as unknown as EngineOutputsResponse;
}

const fx = vi.hoisted(() => ({ download: vi.fn(async () => {}) }));

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
      outputs: buildOutputs(), previous: null, loading: false, settled: true, error: null, lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', name: 'Project Harbor', keys: 132, field_overrides: { acquisition_close_date: { value: '2025-09-30', note: 'PSA' } } },
    status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), running: false, status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({ flags: [], critical: 0, warn: 0, info: 0, note: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn() }),
}));
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: [], extractions: {}, loading: false, settled: true,
    extractionFailures: {}, error: null, uploading: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/lib/api', async (importOriginal) => {
  const { withQuietWorker } = await import('./helpers/quietWorker');
  const actual = withQuietWorker(await importOriginal<typeof import('@/lib/api')>());
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: vi.fn(async () => ({ id: 'deal-uuid-1' })) },
      plRoundTrip: { ...actual.api.plRoundTrip, download: fx.download },
    },
  };
});

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';

beforeEach(() => {
  cleanup();
  fx2.returns = {
    hold_years: HOLD_YEARS, exit_cap_rate: 0.08, terminal_noi: 4_400_000,
    gross_sale_price: 55_000_000, exit_noi_period_label: 'Forward 12-month NOI (Year 3)',
  };
});

function rowByLabel(label: string): HTMLTableRowElement | undefined {
  const rows = Array.from(document.querySelectorAll<HTMLTableRowElement>('table tbody tr'));
  return rows.find((r) => (r.cells[0]?.textContent ?? '').trim().startsWith(label));
}

describe('Projections — Implied Value row', () => {
  it('capitalises each year\u2019s Cash NOI (not EBITDA) at the engine exit cap; the Exit Year is the engine sale price', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(rowByLabel('Implied Exit Value')).toBeUndefined();
    const row = document.querySelector<HTMLTableRowElement>('[data-testid="row-implied-value"]');
    expect(row).not.toBeNull();
    expect(row!.cells[0].textContent).toMatch(/Cash NOI/);
    const cells = Array.from(row!.cells).slice(2);
    // Y1 Cash NOI 4,160,000 ÷ 8% = 52,000,000 (EBITDA basis would be 70,500,000).
    expect(cells[0].textContent).toBe('$52,000,000');
    expect(cells[1].textContent).toBe('$53,250,000');
    expect(cells[2].textContent).toBe('$55,000,000');
    expect(cells[0].getAttribute('title')).toMatch(/Cash NOI/);
    expect(cells[2].getAttribute('title')).toMatch(/gross sale price/i);
  });

  it('sits below Cash NOI', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const rows = Array.from(document.querySelectorAll('table tbody tr'));
    const cashNoi = rows.findIndex((r) => r.getAttribute('data-testid') === 'row-cash-noi' || r.querySelector('[data-testid="row-cash-noi"]'));
    const implied = rows.findIndex((r) => r.getAttribute('data-testid') === 'row-implied-value');
    expect(cashNoi).toBeGreaterThanOrEqual(0);
    expect(implied).toBeGreaterThan(cashNoi);
  });

  it('with no Returns run it is a dash with the reason — no default cap rate', () => {
    fx2.returns = { hold_years: HOLD_YEARS };
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const row = document.querySelector<HTMLTableRowElement>('[data-testid="row-implied-value"]')!;
    const cells = Array.from(row.cells).slice(2);
    expect(cells.map((c) => c.textContent)).toEqual(['—', '—', '—']);
    expect(cells[0].getAttribute('title')).toMatch(/exit cap rate/i);
    expect(cells[2].getAttribute('title')).toMatch(/gross sale price/i);
  });
});
