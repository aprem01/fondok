/**
 * FON-41 E-011 — Excel-like cell comments on the Historical P&L.
 *
 * Contracts locked here:
 *   1. A commented cell carries a marker keyed on its SOURCE LINE
 *      (`hist:<document_id>::<field_name>`), showing the count and whether the
 *      thread is still open. Uncommented cells get a hover-only "add" marker.
 *   2. The marker opens a side thread with the full history; adding posts the
 *      cell key + a human label; resolving calls the worker and re-reads.
 *   3. "Commented cells" filters the worksheet to rows with a commented cell
 *      and lists every thread for revisiting.
 *   4. Threads of the OTHER view (`proj:` keys) never surface here.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { CellComment, EngineOutputsResponse, ExtractionResult } from '@/lib/api';
import { DEAL_ID, DOCS, EXTRACTIONS, KEYS } from './helpers/fon41Fixture';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

const ROOMS_KEY = 'hist:d2023::rooms_revenue';
const comment = (over: Partial<CellComment>): CellComment => ({
  id: 'c1', deal_id: DEAL_ID, cell_key: ROOMS_KEY, cell_label: 'Rooms Revenue · FY2023',
  body: 'Ties to the audited 2023 P&L?', author_id: 'u1', author_email: 'sam@fondok.test',
  created_at: '2026-10-01T10:00:00Z', resolved_at: null, resolved_by: null, ...over,
});
let SERVER: CellComment[] = [];
const listSpy = vi.fn(async () => SERVER);
const createSpy = vi.fn(async (_deal: string, body: { cell_key: string; body: string; cell_label?: string | null }) => {
  const c = comment({ id: `c${SERVER.length + 1}`, cell_key: body.cell_key, body: body.body, cell_label: body.cell_label ?? null, created_at: '2026-10-02T10:00:00Z' });
  SERVER = [...SERVER, c];
  return c;
});
const resolveSpy = vi.fn(async (_deal: string, key: string, resolved: boolean) => {
  SERVER = SERVER.map((c) => (c.cell_key === key ? { ...c, resolved_at: resolved ? '2026-10-03T00:00:00Z' : null, resolved_by: resolved ? 'sam@fondok.test' : null } : c));
  return { cell_key: key, resolved, updated: 1 };
});

vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  workerUrl: () => 'http://worker.test',
  api: {
    documents: { reviewField: vi.fn(), extraction: vi.fn(), list: vi.fn() },
    deals: { update: vi.fn(async () => ({})) },
    comments: {
      list: (...a: unknown[]) => listSpy(...(a as [])),
      create: (...a: unknown[]) => createSpy(...(a as [string, { cell_key: string; body: string }])),
      resolveThread: (...a: unknown[]) => resolveSpy(...(a as [string, string, boolean])),
    },
    plRoundTrip: { download: vi.fn() },
  },
}));

const OUTPUTS = {
  deal_id: DEAL_ID,
  engines: {
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
    useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, settled: true, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ run: vi.fn(async () => {}), status: 'idle', error: null }) }));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({ deal: { id: DEAL_ID, keys: KEYS, field_overrides: {} }, status: null, loading: false, error: null, fromMock: false, refresh: vi.fn() }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTrace: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: DOCS, settled: true, extractionFailures: {}, loading: false, error: null,
    uploading: false, upload: vi.fn(), extractions: EXTRACTIONS as Record<string, ExtractionResult>,
    refresh: vi.fn(), refreshExtraction: vi.fn(async () => {}),
  }),
}));
vi.mock('@/lib/hooks/useHistoricals', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useHistoricals')>('@/lib/hooks/useHistoricals');
  const { histHasData } = await import('@/lib/reviewState');
  const fx = await import('./helpers/fon41Fixture');
  const years = actual.buildHistoricalYears(fx.DOCS, fx.EXTRACTIONS, fx.KEYS).filter(histHasData);
  return { ...actual, useHistoricals: () => ({ years, keys: fx.KEYS, loading: false }) };
});

import GroundedWorksheet from '@/components/project/pl/GroundedWorksheet';

beforeEach(() => {
  SERVER = [
    comment({}),
    // A Future P&L thread — must never show on the Historical view.
    comment({ id: 'p1', cell_key: 'proj:expense.years[0].gop', cell_label: 'GOP · Year 1', body: 'projection note' }),
  ];
  listSpy.mockClear();
  createSpy.mockClear();
  resolveSpy.mockClear();
  window.localStorage.clear();
});
afterEach(cleanup);

const marker = () => screen.findByTestId(`comment-marker-${ROOMS_KEY}`);

describe('Historical P&L — cell comments (E-011)', () => {
  it('marks the commented cell by its source line, with count + open state', async () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    const m = await marker();
    expect(m).toHaveAttribute('data-comment-state', 'open');
    expect(m).toHaveTextContent('1');
    // The same line in the OTHER statement is a different cell — no thread.
    expect(screen.getByTestId('comment-marker-hist:d2019::rooms_revenue')).toHaveAttribute('data-comment-state', 'none');
    expect(listSpy).toHaveBeenCalledWith(DEAL_ID);
  });

  it('opens the thread with its history, adds a comment, then resolves it', async () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    fireEvent.click(await marker());
    const panel = screen.getByTestId('comment-thread-panel');
    expect(within(panel).getByText('Ties to the audited 2023 P&L?')).toBeInTheDocument();
    expect(within(panel).getByText('sam@fondok.test')).toBeInTheDocument();

    fireEvent.change(within(panel).getByLabelText('New comment'), { target: { value: 'Broker confirmed.' } });
    fireEvent.click(within(panel).getByRole('button', { name: /add comment/i }));
    await waitFor(() => expect(createSpy).toHaveBeenCalledWith(DEAL_ID, {
      cell_key: ROOMS_KEY, body: 'Broker confirmed.', cell_label: 'Rooms Revenue · FY2023',
    }));
    expect(await within(panel).findByText('Broker confirmed.')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId(`comment-marker-${ROOMS_KEY}`)).toHaveTextContent('2'));

    fireEvent.click(within(panel).getByRole('button', { name: /resolve thread/i }));
    await waitFor(() => expect(resolveSpy).toHaveBeenCalledWith(DEAL_ID, ROOMS_KEY, true));
    await waitFor(() => expect(screen.getByTestId(`comment-marker-${ROOMS_KEY}`)).toHaveAttribute('data-comment-state', 'resolved'));
    // History survives the resolve; the action flips to Reopen.
    expect(within(panel).getByText('Ties to the audited 2023 P&L?')).toBeInTheDocument();
    expect(within(panel).getByRole('button', { name: /reopen thread/i })).toBeInTheDocument();
  });

  it('"Commented cells" filters the rows and lists the threads to revisit', async () => {
    render(<GroundedWorksheet dealId={DEAL_ID} />);
    await marker();
    expect(screen.getByText('Food & Beverage Revenue')).toBeInTheDocument();
    const toggle = screen.getByTestId('commented-cells-toggle');
    expect(toggle).toHaveTextContent('· 1'); // the proj: thread is not counted here
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-pressed', 'true');
    // Only the row with a commented cell remains.
    expect(screen.queryByText('Food & Beverage Revenue')).not.toBeInTheDocument();
    expect(screen.getByText('Rooms Revenue')).toBeInTheDocument();
    const list = screen.getByTestId('commented-cells-list');
    expect(within(list).getByText('Rooms Revenue · FY2023')).toBeInTheDocument();
    expect(within(list).queryByText('GOP · Year 1')).not.toBeInTheDocument();
    fireEvent.click(within(list).getByText('Rooms Revenue · FY2023'));
    expect(screen.getByTestId('comment-thread-panel')).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText('Food & Beverage Revenue')).toBeInTheDocument();
  });
});
