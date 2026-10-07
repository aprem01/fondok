/**
 * Financials → Historicals worksheet — a "correct at source" save shows what
 * it changed (E-012 / FON-41: "after changing a source line it was not clear
 * whether the adjustment flowed through the rest of the P&L; a $5 increase
 * in Rooms Revenue should raise Total Revenue by $5 and update dependent
 * calculations").
 *
 * Contracts locked here:
 *
 *  1. THE GRID REFRESHES WITHOUT A RELOAD. The save refetches THAT
 *     statement's extraction (`refreshExtraction(docId)`); the REAL
 *     `useHistoricals` rebuilds the column from the live store, so the
 *     corrected value is on screen.
 *
 *  2. DERIVED TOTALS ARE REPORTED WITH THE NUMBERS. Total Revenue in a
 *     historical column is a client-side sum, so "+$5 on Rooms Revenue"
 *     reads "Rooms Revenue +$5 → Total Revenue +$5 (calculated)" with the
 *     exact before → after (the compact cell cannot show a $5 move), and
 *     both cells light up.
 *
 *  3. STATED LINES ARE NOT FAKED. GOP / NOI are the statement's own lines —
 *     the worker does not re-foot them — so the note says so, the model
 *     re-run is kicked, and the footing check shows the resulting
 *     difference ("Lines sum to $X · stated total $Y · difference $Z").
 *
 * Reads exclusively from mocked hooks / api — no prototype numbers.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse, ExtractionResult } from '@/lib/api';
import { DEAL_ID, KEYS, doc, field, extraction } from './helpers/fon41Fixture';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

// ── api: spy on the review endpoint ──────────────────────────────────────
const reviewFieldSpy = vi.fn(async (..._args: unknown[]) => ({}));
vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  workerUrl: () => 'http://worker.test',
  api: {
    documents: {
      reviewField: (...args: unknown[]) => reviewFieldSpy(...args),
      extraction: vi.fn(),
      list: vi.fn(),
    },
    deals: { update: vi.fn(async () => ({})) },
  },
}));

const OUTPUTS = {
  deal_id: DEAL_ID,
  engines: {
    revenue: {
      deal_id: DEAL_ID, engine: 'revenue', status: 'complete', summary: '',
      outputs: { years: [{ year: 2025, occupancy: 0.75, adr: 300, revpar: 225, rooms_revenue: 10_000_000, fb_revenue: 2_000_000, other_revenue: 500_000, total_revenue: 12_500_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r',
    },
    fb: {
      deal_id: DEAL_ID, engine: 'fb', status: 'complete', summary: '',
      outputs: { years: [{ year: 2025, rooms_revenue: 10_000_000, fb_revenue: 2_000_000, other_revenue: 500_000, total_revenue: 12_500_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r',
    },
    expense: {
      deal_id: DEAL_ID, engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 2025, total_revenue: 12_500_000, gop: 5_000_000, noi: 4_000_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r',
    },
  },
} as unknown as EngineOutputsResponse;

vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS, previous: null, loading: false, settled: true, lastRunAt: null, refresh: vi.fn(async () => {}),
    }),
  };
});
const runSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: runSpy, status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: DEAL_ID, keys: KEYS, field_overrides: {} },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTrace: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

// ── one annual statement whose lines foot against its own stated totals ──
//   rooms 11.0M + F&B 2.0M = stated total 13.0M
//   13.0M − dept (2.4M + 1.5M + 0.1M) − undistributed 3.0M = stated GOP 6.0M
const DOC_2023 = doc('d2023', { filename: '2023 P&L.xlsx', fiscal_year: 2023, uploaded_at: '2026-01-02T00:00:00Z' });
const EX_2023 = extraction('d2023', [
  field('occupancy', 0.75, 0.95),
  field('rooms_revenue', 11_000_000, 0.95),
  field('fb_revenue', 2_000_000, 0.95),
  field('total_revenue', 13_000_000, 0.95),
  field('rooms_dept_expense', 2_400_000, 0.95),
  field('fb_dept_expense', 1_500_000, 0.95),
  field('other_dept_expense', 100_000, 0.95),
  field('undistributed_expenses', 3_000_000, 0.95),
  field('gop', 6_000_000, 0.95),
  field('noi', 4_000_000, 0.95),
]);
/** What the worker returns after an edit — THAT field replaced, nothing else re-footed. */
const afterEdit = (name: string, value: number): ExtractionResult =>
  extraction('d2023', EX_2023.fields.map((f) =>
    f.field_name === name ? { ...f, value, confidence: 1.0, reviewed: 'edited' } : f,
  ));

// ── documents: STATEFUL mock — refreshExtraction() swaps in the worker's
//    post-edit extraction, exactly like the shared store broadcast does. ──
let LIVE_AFTER_REFRESH: Record<string, ExtractionResult> = {};
const refreshSpy = vi.fn();
vi.mock('@/lib/hooks/useDocuments', async () => {
  const ReactMod = await import('react');
  return {
    useDocuments: () => {
      const [extractions, setExtractions] = ReactMod.useState<Record<string, ExtractionResult | undefined>>({ d2023: EX_2023 });
      return {
        documents: [DOC_2023],
        settled: true,
        extractionFailures: {},
        loading: false,
        error: null,
        uploading: false,
        upload: vi.fn(),
        extractions,
        refresh: vi.fn(),
        refreshExtraction: async (docId: string) => {
          refreshSpy(docId);
          const next = LIVE_AFTER_REFRESH[docId];
          if (next) setExtractions((prev) => ({ ...prev, [docId]: next }));
        },
      };
    },
  };
});

// NOTE: `useHistoricals` is the REAL hook here — contract 1 is that the
// column rebuilds from the live extractions after the refetch.
import GroundedWorksheet from '@/components/project/pl/GroundedWorksheet';

const GREEN_CELL = 'Extracted — click to see its source';
const rowOf = (label: string) => screen.getByText(label).closest('tr') as HTMLTableRowElement;

/** Open the SOURCE panel on a green cell and correct its value at source. */
async function correctAtSource(rowLabel: string, value: string) {
  fireEvent.click(within(rowOf(rowLabel)).getByRole('button', { name: GREEN_CELL }));
  fireEvent.click(screen.getByText('Correct this value →'));
  const input = screen.getByDisplayValue(/^\d+$/) as HTMLInputElement;
  fireEvent.change(input, { target: { value } });
  fireEvent.click(screen.getByText('Save + re-model'));
}

beforeEach(() => {
  LIVE_AFTER_REFRESH = {};
  reviewFieldSpy.mockClear();
  refreshSpy.mockClear();
  runSpy.mockClear();
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
});
afterEach(cleanup);

describe('Historicals worksheet — a $5 Rooms Revenue correction flows through (E-012)', () => {
  it('saves the edit at source, refetches THAT statement, and reports Rooms Revenue +$5 → Total Revenue +$5 with exact figures', async () => {
    LIVE_AFTER_REFRESH = { d2023: afterEdit('rooms_revenue', 11_000_005) };
    render(<GroundedWorksheet dealId={DEAL_ID} />);

    // Before: the column foots.
    expect(within(screen.getByTestId('footing-revenue')).getByText('✓ foots')).toBeInTheDocument();

    await correctAtSource('Rooms Revenue', '11000005');

    expect(reviewFieldSpy).toHaveBeenCalledWith(DEAL_ID, 'd2023', { field_name: 'rooms_revenue', action: 'edit', value: 11_000_005 });
    // Contract 1 — the statement's extraction is refetched, not the whole deal.
    expect(refreshSpy).toHaveBeenCalledWith('d2023');

    const note = await screen.findByTestId('worksheet-propagation');
    // Contract 2 — the edited line and the derived total, with exact figures.
    expect(note.textContent).toContain('Saved · FY2023');
    expect(note.textContent).toContain('Rooms Revenue +$5 ($11,000,000 → $11,000,005)');
    expect(note.textContent).toContain('→ Total Revenue +$5 ($13,000,000 → $13,000,005) (calculated)');
    // Contract 3 — stated lines are not faked; the model re-run is kicked.
    expect(note.textContent).toContain('GOP and NOI are the statement’s own stated lines — they do not re-foot from this edit');
    expect(note.textContent).toContain('model re-run');
    await waitFor(() => expect(runSpy).toHaveBeenCalled());

    // Both moved cells light up; a stated line does not.
    expect(rowOf('Rooms Revenue').querySelector('td[data-changed="true"]')).not.toBeNull();
    expect(rowOf('Total Revenue').querySelector('td[data-changed="true"]')).not.toBeNull();
    expect(rowOf('Gross Operating Profit (GOP)').querySelector('td[data-changed="true"]')).toBeNull();
  });

  it('the footing check shows the $5 the worker did not re-foot — in the note and in the grid', async () => {
    LIVE_AFTER_REFRESH = { d2023: afterEdit('rooms_revenue', 11_000_005) };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    await correctAtSource('Rooms Revenue', '11000005');

    const footing = await screen.findByTestId('worksheet-propagation-footing');
    expect(footing.textContent).toContain('Revenue: Lines sum to $13,000,005 · stated total $13,000,000 · difference +$5');
    expect(footing.textContent).toContain('GOP: Lines sum to $6,000,005 · stated GOP $6,000,000 · difference +$5');

    const revenueRow = screen.getByTestId('footing-revenue');
    expect(within(revenueRow).getByText('Δ +$5')).toBeInTheDocument();
    expect(revenueRow.querySelector('td[data-foots="false"]')?.getAttribute('title'))
      .toBe('Lines sum to $13,000,005 · stated total $13,000,000 · difference +$5');
    expect(within(revenueRow).getByText('calculated')).toBeInTheDocument();
  });

  it('a large correction is visible in the refreshed cell itself (the grid rebuilt from the refetched extraction)', async () => {
    LIVE_AFTER_REFRESH = { d2023: afterEdit('rooms_revenue', 11_500_000) };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(within(rowOf('Rooms Revenue')).getByRole('button', { name: GREEN_CELL }).textContent).toBe('$11.0M');
    expect(rowOf('Total Revenue').textContent).toContain('$13.0M');

    await correctAtSource('Rooms Revenue', '11500000');

    await waitFor(() => {
      expect(within(rowOf('Rooms Revenue')).getByRole('button', { name: GREEN_CELL }).textContent).toBe('$11.5M');
    });
    expect(rowOf('Total Revenue').textContent).toContain('$13.5M');
    expect((await screen.findByTestId('worksheet-propagation')).textContent)
      .toContain('Rooms Revenue +$500,000 ($11,000,000 → $11,500,000) → Total Revenue +$500,000 ($13,000,000 → $13,500,000) (calculated)');
  });
});

describe('Historicals worksheet — a correction to a line with no derived total (E-012)', () => {
  it('says no derived total re-sums from it, that GOP / NOI are stated, and that the model re-runs', async () => {
    LIVE_AFTER_REFRESH = { d2023: afterEdit('rooms_dept_expense', 2_400_005) };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    // "Rooms" is the departmental-expense line (Rooms Revenue is a different row).
    await correctAtSource('Rooms', '2400005');

    const note = await screen.findByTestId('worksheet-propagation');
    expect(note.textContent).toContain('Rooms +$5 ($2,400,000 → $2,400,005)');
    expect(note.textContent).toContain('no derived total in this column re-sums from this line');
    expect(note.textContent).toContain('GOP and NOI are the statement’s own stated lines');
    expect(note.textContent).toContain('model re-run');
    expect(runSpy).toHaveBeenCalled();
    // Only the edited cell lights up.
    expect(document.querySelectorAll('td[data-changed="true"]')).toHaveLength(1);
    // The GOP footing now carries the $5 the stated GOP did not absorb.
    expect(within(screen.getByTestId('footing-gop')).getByText('Δ −$5')).toBeInTheDocument();
  });

  it('the panel says up front which totals re-sum here and which are stated', () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    fireEvent.click(within(rowOf('Rooms Revenue')).getByRole('button', { name: GREEN_CELL }));
    expect(screen.getByText(/Totals this column derives \(Total Revenue, Total Fees & Fixed\) re-sum here at once/)).toBeInTheDocument();
    expect(screen.getByText(/the statement’s own stated lines \(GOP, NOI\) do not/)).toBeInTheDocument();
  });
});

describe('Historicals worksheet — footing check is honest about what it cannot foot', () => {
  it('a statement with no stated total shows a dash with the reason, never a fabricated total', () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    // GOP foots on this fixture; both rows render with the "calculated" label.
    expect(within(screen.getByTestId('footing-gop')).getByText('✓ foots')).toBeInTheDocument();
    expect(screen.getAllByText('calculated')).toHaveLength(2);
  });
});
