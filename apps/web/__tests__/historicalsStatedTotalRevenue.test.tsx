/**
 * FON-90 — the Historical P&L Total Revenue row shows the statement's OWN
 * stated total, not a client-side re-sum of the lines.
 *
 * Live fact on both tester deals: ``p_and_l_usali.total_revenue_usd`` is
 * $13.48M while rooms + F&B + other sum to $12.3M (the reconciler-added
 * ``operating_revenue.misc_revenue`` is not in the sum). The row derived
 * $12.3M and called it "Total Revenue" — a figure the statement never states.
 *
 * Contracts locked here:
 *
 *  1. WITH a stated total the row shows it — as an extracted (green) cell
 *     whose SOURCE panel names the statement's own Total Revenue field and
 *     value. No "calculated" label on the cell.
 *  2. The footing row still compares the SUM of the lines against the stated
 *     total and shows the difference (Δ −$1,180,000) — the cell never foots
 *     against itself.
 *  3. WITHOUT a stated total the row falls back to the sum of the lines,
 *     labelled "calculated", and the footing row shows a dash with the
 *     reason (nothing to foot against) — no total is ever fabricated.
 *  4. The stated total adds no "to review" count: it is reviewed through the
 *     lines that foot to it.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse, ExtractionResult } from '@/lib/api';
import { DEAL_ID, KEYS, doc, field, extraction } from './helpers/fon41Fixture';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  workerUrl: () => 'http://worker.test',
  api: {
    documents: { reviewField: vi.fn(async () => ({})), extraction: vi.fn(), list: vi.fn() },
    deals: { update: vi.fn(async () => ({})) },
  },
}));

const OUTPUTS = {
  deal_id: DEAL_ID,
  engines: {
    revenue: {
      deal_id: DEAL_ID, engine: 'revenue', status: 'complete', summary: '',
      outputs: { years: [{ year: 2025, occupancy: 0.75, adr: 300, revpar: 225, rooms_revenue: 10_000_000, fb_revenue: 1_800_000, other_revenue: 500_000, total_revenue: 12_300_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r',
    },
    fb: {
      deal_id: DEAL_ID, engine: 'fb', status: 'complete', summary: '',
      outputs: { years: [{ year: 2025, rooms_revenue: 10_000_000, fb_revenue: 1_800_000, other_revenue: 500_000, total_revenue: 12_300_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r',
    },
    expense: {
      deal_id: DEAL_ID, engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 2025, total_revenue: 12_300_000, gop: 5_000_000, noi: 4_000_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r',
    },
  },
} as unknown as EngineOutputsResponse;

vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, settled: true, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ run: vi.fn(async () => {}), status: 'idle', error: null }) }));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: DEAL_ID, keys: KEYS, field_overrides: {} },
    status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTrace: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

// ── the tester-deal shape: lines sum to $12.3M, the statement states $13.48M ──
const DOC_2024 = doc('d2024', { filename: 'Anglers 2024 Full Year P&L.xlsm', fiscal_year: 2024, uploaded_at: '2026-01-02T00:00:00Z' });
const LINES = [
  field('occupancy', 0.75, 0.95),
  field('rooms_revenue', 10_000_000, 0.95),
  field('fb_revenue', 1_800_000, 0.95),
  field('other_revenue', 500_000, 0.95),
  field('rooms_dept_expense', 2_400_000, 0.95),
  field('fb_dept_expense', 1_500_000, 0.95),
  field('other_dept_expense', 100_000, 0.95),
  field('undistributed_expenses', 3_000_000, 0.95),
  field('gop', 6_480_000, 0.95),
  field('noi', 4_000_000, 0.95),
];
const WITH_STATED = extraction('d2024', [...LINES, field('p_and_l_usali.total_revenue_usd', 13_480_000, 0.5)]);
const WITHOUT_STATED = extraction('d2024', LINES);
let EXTRACTION: ExtractionResult = WITH_STATED;

vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: [DOC_2024],
    settled: true,
    extractionFailures: {},
    loading: false,
    error: null,
    uploading: false,
    upload: vi.fn(),
    extractions: { d2024: EXTRACTION },
    refresh: vi.fn(),
    refreshExtraction: vi.fn(async () => {}),
  }),
}));

import GroundedWorksheet from '@/components/project/pl/GroundedWorksheet';
import { buildHistoricalYears } from '@/lib/hooks/useHistoricals';

const GREEN_CELL = 'Extracted — click to see its source';
const rowOf = (label: string) => screen.getByText(label).closest('tr') as HTMLTableRowElement;

beforeEach(() => {
  EXTRACTION = WITH_STATED;
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
});
afterEach(cleanup);

describe('useHistoricals — the column carries the stated total and its source meta', () => {
  // The hook pads a short history with skeleton years (populated: false), so
  // the statement's column is the populated one, not index 0.
  const realColumn = (ext: ExtractionResult) =>
    buildHistoricalYears([DOC_2024], { d2024: ext }, KEYS).find((y) => y.populated)!;

  it('reads total_revenue_stated from the statement’s own line and records its source under meta.total_rev', () => {
    const col = realColumn(WITH_STATED);
    expect(col.year).toBe('2024');
    expect(col.total_revenue_stated).toBe(13_480_000);
    expect(col.rooms + col.fb + col.misc).toBe(12_300_000);
    expect(col.meta?.total_rev).toEqual({ confidence: 0.5, field: 'p_and_l_usali.total_revenue_usd', docId: 'd2024' });
  });

  it('leaves total_revenue_stated null (and no meta) when the statement states no total', () => {
    const col = realColumn(WITHOUT_STATED);
    expect(col.total_revenue_stated).toBeNull();
    expect(col.meta?.total_rev).toBeUndefined();
  });
});

describe('Historical P&L — Total Revenue row shows the stated total (FON-90)', () => {
  it('shows $13.5M (the statement’s line), not the $12.3M re-sum, as an extracted cell sourced to the Total Revenue field', () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    const row = rowOf('Total Revenue');
    const cell = within(row).getByRole('button', { name: GREEN_CELL });
    expect(cell.textContent).toBe('$13.5M');
    expect(row.textContent).not.toContain('$12.3M');
    expect(within(row).queryByTestId('total-rev-calculated')).toBeNull();

    // SOURCE panel → grounded in the statement: its value, document, page
    // and confidence — not "Computed".
    fireEvent.click(cell);
    expect(screen.getByText('Grounded in your documents')).toBeInTheDocument();
    expect(screen.queryByText('Computed')).toBeNull();
    expect(screen.getByText('$13,480,000')).toBeInTheDocument();
    expect(screen.getAllByText(/Anglers 2024 Full Year P&L\.xlsm/).length).toBeGreaterThan(0);
    expect(screen.getByText('page 2')).toBeInTheDocument();
    expect(screen.getByText('50% confidence')).toBeInTheDocument();
    // And the panel's propagation copy names the stated total.
    expect(screen.getByText(/Total Revenue is the statement’s own stated total \(\$13,480,000\)/)).toBeInTheDocument();
  });

  it('the footing row still compares the SUM of the lines against the stated total and shows the $1.18M gap', () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    const footing = screen.getByTestId('footing-revenue');
    expect(within(footing).getByText('Δ −$1,180,000')).toBeInTheDocument();
    expect(footing.querySelector('td[data-foots="false"]')?.getAttribute('title'))
      .toBe('Lines sum to $12,300,000 · stated total $13,480,000 · difference −$1,180,000');
  });

  it('a stated total at 50% confidence adds no "to review" count of its own', () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    // Every line is ≥ 85%; the only sub-threshold field is the stated total.
    expect(document.querySelectorAll('[aria-label^="Low confidence"]')).toHaveLength(0);
  });
});

describe('Historical P&L — Total Revenue row without a stated total (FON-90 fallback)', () => {
  beforeEach(() => { EXTRACTION = WITHOUT_STATED; });

  it('falls back to the sum of the lines, labelled "calculated", and the footing row shows a dash with the reason', () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    const row = rowOf('Total Revenue');
    expect(row.textContent).toContain('$12.3M');
    expect(within(row).queryByRole('button', { name: GREEN_CELL })).toBeNull();
    expect(within(row).getByTestId('total-rev-calculated')).toHaveTextContent('calculated');

    const footing = screen.getByTestId('footing-revenue');
    expect(within(footing).queryByText(/Δ/)).toBeNull();
    expect(within(footing).queryByText('✓ foots')).toBeNull();
    const dash = Array.from(footing.querySelectorAll('td')).find((td) => td.textContent === '—');
    expect(dash?.getAttribute('title')).toBe('This statement publishes no stated total for this line, so there is nothing to foot against.');
  });
});
