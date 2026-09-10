/**
 * Financials → Historicals worksheet — review flags, document pin, SOURCE
 * panel and Accept (FON-41).
 *
 * Contracts locked here (Sam's finding, decision D7):
 *
 *  1. RED CELLS COME FROM THE SHARED REVIEW STATE. The banner count equals the
 *     red cells visible in the shown columns, and per document it equals the
 *     Data Room badge (same fixture: 2019 → 1, 2023 → 2).
 *
 *  2. `?doc=<id>` PINS THAT STATEMENT'S COLUMN — every other year pill is
 *     switched off, the first flagged cell's row is scrolled into view and
 *     pulsed, and the banner counts only that column.
 *
 *  3. THE SOURCE PANEL IS PINNED TO THE COLUMN'S DOCUMENT. Opening a flagged
 *     2023 cell names the 2023 statement (never 2019) and its "correct at
 *     source" action targets that same document.
 *
 *  4. ACCEPT DECREMENTS IMMEDIATELY. Accepting calls the review endpoint for
 *     the pinned document + exact field, and once the live extraction refreshes
 *     the cell un-flags and the banner count drops — no remount.
 *
 * Reads exclusively from mocked hooks / api — no prototype numbers.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse, ExtractionResult } from '@/lib/api';
import {
  DEAL_ID,
  DOCS,
  EXTRACTIONS,
  EXPECTED,
  EX_2023_AFTER_ACCEPT_FB,
  KEYS,
} from './helpers/fon41Fixture';

// ── URL params (settable per test; read at render time) ──────────────────
let PARAMS: Record<string, string> = {};
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: (k: string) => PARAMS[k] ?? null }),
}));

// ── api: spy on the review endpoint ──────────────────────────────────────
const reviewFieldSpy = vi.fn(async () => EX_2023_AFTER_ACCEPT_FB);
vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  workerUrl: () => 'http://worker.test',
  api: {
    documents: {
      reviewField: (...args: unknown[]) => reviewFieldSpy(...(args as [])),
      extraction: vi.fn(),
      list: vi.fn(),
    },
    deals: { update: vi.fn(async () => ({})) },
  },
}));

// ── engine outputs: the worksheet only renders once expense Y0 has revenue ──
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

// Read at render time (the factories are hoisted) so the loading-gate tests can
// drive each input independently: engine outputs settled?, deal loaded?,
// historicals loading? / present?
let SETTLED = true;
let OUTPUTS_OVERRIDE: EngineOutputsResponse | null | undefined = undefined;
let DEAL_OVERRIDE: { deal: unknown; error: string | null } | null = null;
let HIST_OVERRIDE: { years?: unknown[]; loading?: boolean } | null = null;
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS_OVERRIDE === undefined ? OUTPUTS : OUTPUTS_OVERRIDE,
      previous: null, loading: !SETTLED, settled: SETTLED, lastRunAt: null, refresh: vi.fn(async () => {}),
    }),
  };
});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: DEAL_OVERRIDE ? DEAL_OVERRIDE.deal : { id: DEAL_ID, keys: KEYS, field_overrides: {} },
    status: null, loading: false, error: DEAL_OVERRIDE ? DEAL_OVERRIDE.error : null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTrace: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

// ── documents: STATEFUL mock so refreshExtraction() re-renders with the
//    worker's post-accept field, exactly like the real hook does. ──────────
let LIVE_AFTER_REFRESH: Record<string, ExtractionResult> = {};
let DOCS_OVERRIDE: typeof DOCS | null = null;
vi.mock('@/lib/hooks/useDocuments', async () => {
  const ReactMod = await import('react');
  return {
    useDocuments: () => {
      const [extractions, setExtractions] = ReactMod.useState<Record<string, ExtractionResult | undefined>>(EXTRACTIONS);
      return {
        documents: DOCS_OVERRIDE ?? DOCS,
        settled: true,
        extractionFailures: {},
        loading: false,
        error: null,
        uploading: false,
        upload: vi.fn(),
        extractions,
        refresh: vi.fn(),
        refreshExtraction: async (docId: string) => {
          const next = LIVE_AFTER_REFRESH[docId];
          if (next) setExtractions((prev) => ({ ...prev, [docId]: next }));
        },
      };
    },
  };
});

// ── historicals: the REAL pure column builder over the fixture, frozen at
//    load time (the hook holds its own snapshot — flags must clear from the
//    LIVE extractions, not from this copy). ─────────────────────────────────
vi.mock('@/lib/hooks/useHistoricals', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useHistoricals')>('@/lib/hooks/useHistoricals');
  const { histHasData } = await import('@/lib/reviewState');
  const fx = await import('./helpers/fon41Fixture');
  const years = actual.buildHistoricalYears(fx.DOCS, fx.EXTRACTIONS, fx.KEYS).filter(histHasData);
  return {
    ...actual,
    useHistoricals: () => ({
      years: HIST_OVERRIDE?.years ?? years,
      keys: fx.KEYS,
      loading: HIST_OVERRIDE?.loading ?? false,
    }),
  };
});

import GroundedWorksheet from '@/components/project/pl/GroundedWorksheet';

const RED_CELL = /^Low confidence \(\d+%\) — click to review its source$/;
const scrollSpy = vi.fn();

beforeEach(() => {
  PARAMS = {};
  LIVE_AFTER_REFRESH = {};
  SETTLED = true;
  OUTPUTS_OVERRIDE = undefined;
  DEAL_OVERRIDE = null;
  HIST_OVERRIDE = null;
  DOCS_OVERRIDE = null;
  reviewFieldSpy.mockClear();
  scrollSpy.mockClear();
  // jsdom has no scrollIntoView; the deep-link focus calls it on the row.
  Element.prototype.scrollIntoView = scrollSpy as unknown as typeof Element.prototype.scrollIntoView;
  window.localStorage.clear();
});
afterEach(cleanup);

const pill = (label: string) => screen.getByRole('button', { name: label });
const rowOf = (label: string) => screen.getByText(label).closest('tr') as HTMLTableRowElement;

describe('Historicals worksheet — red cells come from the shared review state', () => {
  it('flags exactly the cells the Data Room counts (3 across both columns; 2019 → 1, 2023 → 2)', () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(screen.getByText(String(EXPECTED.total))).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: RED_CELL })).toHaveLength(EXPECTED.total);
    // Per column: hide 2019 → only the 2023 column's 2 remain.
    fireEvent.click(pill('2019'));
    expect(screen.getAllByRole('button', { name: RED_CELL })).toHaveLength(EXPECTED.byDoc.d2023);
    fireEvent.click(pill('2019'));
    fireEvent.click(pill('2023'));
    expect(screen.getAllByRole('button', { name: RED_CELL })).toHaveLength(EXPECTED.byDoc.d2019);
  });

  it('a low-confidence 2019 field never flags the 2023 column (flags are per row AND year)', () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    // Rooms Revenue: 2019 is 60% (red), 2023 is 95% (green). Both cells render.
    const rooms = rowOf('Rooms Revenue');
    const red = within(rooms).getAllByRole('button', { name: RED_CELL });
    expect(red).toHaveLength(1);
    expect(within(rooms).getAllByRole('button', { name: 'Extracted — click to see its source' })).toHaveLength(1);
  });
});

describe('Historicals worksheet — ?doc=<id> pins the statement and lands on its first flagged cell', () => {
  it('switches every other year pill off, scrolls to the first flagged row and counts only that column', () => {
    PARAMS = { doc: 'd2023' };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    // 2023 pinned (active), 2019 hidden.
    expect(pill('2023').className).toContain('bg-ink-900');
    expect(pill('2019').className).toContain('text-ink-400');
    // Only the 2023 column's cells are red; the banner agrees.
    expect(screen.getAllByRole('button', { name: RED_CELL })).toHaveLength(EXPECTED.byDoc.d2023);
    expect(screen.getByText(String(EXPECTED.byDoc.d2023))).toBeInTheDocument();
    // The first flagged row (row order) is scrolled into view and pulsed.
    expect(scrollSpy).toHaveBeenCalled();
    expect(rowOf(EXPECTED.firstFlaggedRow2023).className).toContain('ring-warn-400');
  });

  it('tells the analyst when the pinned document has no extracted column', () => {
    PARAMS = { doc: 'not-a-column' };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(screen.getByText(/has no extracted column here yet/)).toBeInTheDocument();
    // Nothing is hidden in that case.
    expect(pill('2019').className).toContain('bg-ink-900');
    expect(pill('2023').className).toContain('bg-ink-900');
  });
});

describe('Historicals worksheet — SOURCE panel is pinned to the column’s own document', () => {
  it('a flagged 2023 cell names the 2023 statement, its exact field, and corrects on that document', () => {
    PARAMS = { doc: 'd2023' };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    const fb = rowOf('Food & Beverage Revenue');
    fireEvent.click(within(fb).getByRole('button', { name: RED_CELL }));

    // Column label + the ONE source document — the 2023 statement.
    expect(screen.getByText('Source document')).toBeInTheDocument();
    expect(screen.getByText('2023 P&L.xlsx')).toBeInTheDocument();
    expect(screen.queryByText('2019 P&L.xlsx')).not.toBeInTheDocument();
    // The extracted line is the 2023 field at ITS confidence (50%), not 2019's.
    expect(screen.getByText((t) => t.includes('fb_revenue'))).toBeInTheDocument();
    expect(screen.getByText('50% confidence')).toBeInTheDocument();
    // The correction path targets the same document.
    expect(screen.getByText(/Updates the extracted value on 2023 P&L\.xlsx/)).toBeInTheDocument();
  });

  it('a flagged 2019 cell names the 2019 statement — even though 2023 carries the same field name', () => {
    PARAMS = { doc: 'd2019' };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    const rooms = rowOf('Rooms Revenue');
    fireEvent.click(within(rooms).getByRole('button', { name: RED_CELL }));
    expect(screen.getByText('2019 P&L.xlsx')).toBeInTheDocument();
    expect(screen.queryByText('2023 P&L.xlsx')).not.toBeInTheDocument();
    expect(screen.getByText('60% confidence')).toBeInTheDocument();
  });
});

describe('Historicals worksheet — loading gate before any empty state (FON-41a follow-up, Sam QA 9/9)', () => {
  const EMPTY = /No extracted financial statements yet/;

  it('holds a skeleton while this component’s own engine-outputs fetch is unsettled', () => {
    SETTLED = false;
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(screen.getByTestId('worksheet-loading')).toBeInTheDocument();
    expect(screen.queryByText(EMPTY)).not.toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('holds a skeleton while the deal row is still loading', () => {
    DEAL_OVERRIDE = { deal: null, error: null };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(screen.getByTestId('worksheet-loading')).toBeInTheDocument();
  });

  it('holds a skeleton while the historicals (documents / extractions) are still loading', () => {
    HIST_OVERRIDE = { years: [], loading: true };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(screen.getByTestId('worksheet-loading')).toBeInTheDocument();
    expect(screen.queryByText(EMPTY)).not.toBeInTheDocument();
  });

  it('shows the true empty state only once everything has settled with no columns (no statements uploaded)', () => {
    DOCS_OVERRIDE = [];
    HIST_OVERRIDE = { years: [], loading: false };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(screen.queryByTestId('worksheet-loading')).not.toBeInTheDocument();
    expect(screen.getByTestId('worksheet-empty')).toHaveTextContent(EMPTY);
  });

  it('says so honestly when statements exist but no column could be built (e.g. key count missing)', () => {
    HIST_OVERRIDE = { years: [], loading: false };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(screen.queryByTestId('worksheet-loading')).not.toBeInTheDocument();
    expect(screen.getByTestId('worksheet-empty')).toHaveTextContent(/Extracted statements are present, but no historical column could be built/);
    expect(screen.queryByText(EMPTY)).not.toBeInTheDocument();
  });

  it('renders the historical grid from extracted statements even before any engine run (outputs null)', () => {
    OUTPUTS_OVERRIDE = null;
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(screen.queryByTestId('worksheet-loading')).not.toBeInTheDocument();
    expect(screen.queryByTestId('worksheet-empty')).not.toBeInTheDocument();
    expect(pill('2019')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: RED_CELL })).toHaveLength(EXPECTED.total);
  });

  it('a failed deal fetch does not hold the skeleton forever', () => {
    DEAL_OVERRIDE = { deal: null, error: 'boom' };
    HIST_OVERRIDE = { years: [], loading: false };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(screen.queryByTestId('worksheet-loading')).not.toBeInTheDocument();
    expect(screen.getByTestId('worksheet-empty')).toBeInTheDocument();
  });
});

describe('Historicals worksheet — Accept decrements immediately', () => {
  it('accepting the 2023 F&B value calls the endpoint for THAT document + field, then un-flags the cell and drops the count', async () => {
    PARAMS = { doc: 'd2023' };
    LIVE_AFTER_REFRESH = { d2023: EX_2023_AFTER_ACCEPT_FB };
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    expect(screen.getAllByRole('button', { name: RED_CELL })).toHaveLength(2);

    const fb = rowOf('Food & Beverage Revenue');
    fireEvent.click(within(fb).getByRole('button', { name: RED_CELL }));
    fireEvent.click(screen.getByRole('button', { name: /Looks right — accept/ }));

    expect(reviewFieldSpy).toHaveBeenCalledWith(DEAL_ID, 'd2023', { field_name: 'fb_revenue', action: 'accept' });
    await waitFor(() => {
      expect(screen.getAllByRole('button', { name: RED_CELL })).toHaveLength(1);
    });
    // The remaining flag is the Rooms departmental expense; F&B is green now.
    expect(within(rowOf('Food & Beverage Revenue')).queryByRole('button', { name: RED_CELL })).toBeNull();
    expect(within(rowOf('Rooms')).getByRole('button', { name: RED_CELL })).toBeInTheDocument();
  });
});
