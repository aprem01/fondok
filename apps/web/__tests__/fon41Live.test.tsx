/**
 * FON-41 live regression — Sam’s deal e577f547 (QA 2026-09-09).
 *
 * Observed: the Data Room showed "9 to review" on the March-2025 T-12 while
 * Financials → Historicals, deep-linked to that document, rendered zero red
 * cells and no review banner.
 *
 * Root cause: the two surfaces did not share their INPUTS. The Data Room
 * counted over useDocuments' state (document list + every extraction fetched
 * in parallel from page load); the worksheet's `useHistoricals` ran its own
 * fetch chain — list, then each extraction SERIALLY — and only started after
 * the Financials tab's skeleton gate. Until that chain finished the worksheet
 * had no columns at all (no pill, no pin notice, no banner). The hook now
 * builds columns from the caller's useDocuments state through the same pure
 * `buildHistoricalYears`, so both surfaces are one function over one state.
 *
 * This suite drives the REAL useDocuments + useHistoricals + worksheet over
 * live-shaped extraction data (real low-confidence field names / values), with
 * only the api mocked, and asserts per statement:
 *   Data Room badge (pure review state) == red cells in that column.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import {
  LIVE_DEAL_ID, LIVE_KEYS, LIVE_DOCS, LIVE_EXTRACTIONS, LIVE_BADGES,
  DOC_T12_2025, DOC_PNL_2024, DOC_PNL_2023, DOC_PNL_2019,
} from './helpers/fon41LiveFixture';
import { buildHistoricalYears } from '@/lib/hooks/useHistoricals';
import { buildReviewState, histHasData } from '@/lib/reviewState';
import { WORKSHEET_ROWS } from '@/components/project/pl/GroundedWorksheet';

let PARAMS: Record<string, string> = {};
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'e577f547-a3cd-4e78-9ee1-8d761b0c4777' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: (k: string) => PARAMS[k] ?? null }),
}));

// Only the api is mocked — it serves the live-shaped documents + extractions.
vi.mock('@/lib/api', async () => {
  const fx = await import('./helpers/fon41LiveFixture');
  return {
    isWorkerConnected: () => true,
    workerUrl: () => 'http://worker.test',
    api: {
      documents: {
        list: async () => fx.LIVE_DOCS,
        extraction: async (_deal: string, docId: string) => {
          const r = fx.LIVE_EXTRACTIONS[docId];
          if (!r) throw new Error(`no extraction for ${docId}`);
          return r;
        },
        reviewField: vi.fn(async () => ({})),
      },
      deals: { update: vi.fn(async () => ({})), get: vi.fn(), status: vi.fn() },
    },
  };
});

const OUTPUTS = {
  deal_id: LIVE_DEAL_ID,
  engines: {
    expense: {
      deal_id: LIVE_DEAL_ID, engine: 'expense', status: 'complete', summary: '',
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
  useDeal: () => ({
    deal: { id: 'e577f547-a3cd-4e78-9ee1-8d761b0c4777', keys: 132, field_overrides: {} },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTrace: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import GroundedWorksheet from '@/components/project/pl/GroundedWorksheet';

const RED_CELL = /^Low confidence \(\d+%\) — click to review its source$/;
const activePill = (label: string) => {
  const b = screen.getByRole('button', { name: label });
  expect(b.className).toContain('bg-ink-900');
};

beforeEach(() => {
  PARAMS = {};
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.localStorage.clear();
});
afterEach(cleanup);

// What the Data Room computes (pure path over the same state).
const dataRoomBadges = () => {
  const years = buildHistoricalYears(LIVE_DOCS, LIVE_EXTRACTIONS, LIVE_KEYS).filter(histHasData);
  return buildReviewState(LIVE_DOCS, LIVE_EXTRACTIONS, WORKSHEET_ROWS, years).byDoc;
};

describe('FON-41 live — pure review state over live-shaped extractions (the Data Room side)', () => {
  it('builds one column per statement, each pinned to its document', () => {
    const years = buildHistoricalYears(LIVE_DOCS, LIVE_EXTRACTIONS, LIVE_KEYS).filter(histHasData);
    const byDoc = new Map(years.map((y) => [y.docId, y.year]));
    expect(byDoc.get(DOC_T12_2025.id)).toBe('T-12');
    expect(byDoc.get(DOC_PNL_2024.id)).toBe('2024');
    expect(byDoc.get(DOC_PNL_2023.id)).toBe('2023');
    expect(byDoc.get(DOC_PNL_2019.id)).toBe('2019');
  });

  it('counts the T-12’s nine real low-confidence lines (the live badge) and none on the clean 2024 P&L', () => {
    const badges = dataRoomBadges();
    expect(badges.get(DOC_T12_2025.id)).toBe(LIVE_BADGES.T12_2025);
    expect(badges.get(DOC_PNL_2024.id) ?? 0).toBe(LIVE_BADGES.PNL_2024);
  });
});

describe('FON-41 live — real loaders + worksheet: red cells == Data Room badge, per statement', () => {
  const cases = [
    { doc: DOC_T12_2025, pill: 'T-12', other: '2023' },
    { doc: DOC_PNL_2024, pill: '2024', other: 'T-12' },
    { doc: DOC_PNL_2023, pill: '2023', other: 'T-12' },
    { doc: DOC_PNL_2019, pill: '2019', other: 'T-12' },
  ];

  for (const c of cases) {
    it(`?doc=${c.pill} pins the column and shows exactly the badge count (${c.doc.filename})`, async () => {
      PARAMS = { doc: c.doc.id };
      const expected = dataRoomBadges().get(c.doc.id) ?? 0;
      render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
      // (The shared per-deal store is warm after the first test, so a
      // skeleton is not guaranteed here — only the settled, pinned grid is.
      // Store updates land outside act, so wait for the pin to be applied.)
      await waitFor(() => {
        activePill(c.pill);
        expect(screen.getByRole('button', { name: c.other }).className).toContain('text-ink-400');
      }, { timeout: 8000 });
      expect(screen.queryAllByRole('button', { name: RED_CELL })).toHaveLength(expected);
      if (expected > 0) expect(screen.getByText(String(expected))).toBeInTheDocument();
    }, 10000);
  }

  it('the T-12 shows nine red cells — the number the Data Room badge showed live', async () => {
    PARAMS = { doc: DOC_T12_2025.id };
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
    await waitFor(
      () => expect(screen.getAllByRole('button', { name: RED_CELL })).toHaveLength(LIVE_BADGES.T12_2025),
      { timeout: 8000 },
    );
  }, 10000);
});
