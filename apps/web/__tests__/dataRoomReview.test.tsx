/**
 * Data Room — per-document "N to review" badge + deep-link (FON-41).
 *
 * Contracts locked here (Sam's finding, decision D7):
 *
 *  1. A financial statement's badge is the number of flagged cells in ITS
 *     column of Financials → Historicals — computed from the shared
 *     lib/reviewState over the same documents / extractions (no separate
 *     field-alias predicate). The global "N financial values need your
 *     review" CTA is their sum.
 *
 *  2. Clicking the badge (or "View Financials") navigates to
 *     `?tab=pl&fin=historicals&doc=<id>` so the worksheet pins that
 *     statement's column and lands on its first flagged cell.
 *
 *  3. When the live extraction shows a field accepted, the document badge and
 *     the global count both decrement from the same state.
 *
 * The tab reads exclusively from mocked hooks / api — no prototype numbers.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';
import type { ExtractionResult } from '@/lib/api';
import {
  DEAL_ID,
  DOCS,
  EXTRACTIONS,
  EXPECTED,
  EX_2023_AFTER_ACCEPT_FB,
  KEYS,
} from './helpers/fon41Fixture';

const pushSpy = vi.fn();
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: pushSpy, replace: vi.fn() }),
  useSearchParams: () => ({ get: (_k: string) => null }),
}));

vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  workerUrl: () => 'http://worker.test',
  api: {
    documents: {
      downloadUrl: (_deal: string, _doc: string) => 'http://worker.test/dl',
      reviewField: vi.fn().mockResolvedValue(undefined),
      reclassify: vi.fn().mockResolvedValue(undefined),
      acceptClassification: vi.fn().mockResolvedValue(undefined),
      acceptYear: vi.fn().mockResolvedValue(undefined),
    },
  },
}));

// Read at render time so a test can swap in the post-accept extraction.
let LIVE_EXTRACTIONS: Record<string, ExtractionResult> = EXTRACTIONS;
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: DOCS,
    loading: false,
    error: null,
    uploading: false,
    upload: vi.fn(),
    extractions: LIVE_EXTRACTIONS,
    refresh: vi.fn(),
    refreshExtraction: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: DEAL_ID, keys: KEYS, field_overrides: {} },
    status: null,
    loading: false,
    error: null,
    fromMock: false,
    refresh: vi.fn(),
  }),
}));

vi.mock('@/lib/hooks/useEngineOutputs', () => ({ useEngineOutputs: () => ({ outputs: null }) }));
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ status: 'idle', run: vi.fn() }) }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/auth', () => ({ useCurrentRole: () => 'org:member' }));
vi.mock('@/components/project/validation/GapChipsStrip', () => ({ GapChipsStrip: () => null }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));

// The worksheet module is imported for its REAL row model (WORKSHEET_ROWS) so
// the badge is computed over exactly the rows the grid renders; the component
// itself never mounts here.
import DataRoomTab from '@/components/project/DataRoomTab';

afterEach(() => {
  cleanup();
  pushSpy.mockClear();
  LIVE_EXTRACTIONS = EXTRACTIONS;
});

const badgeFor = (n: number, name: string) =>
  screen.getByRole('button', { name: `Review ${n} flagged value${n === 1 ? '' : 's'} in ${name}` });

describe('Data Room — "N to review" reconciles to the Historicals column (FON-41)', () => {
  it('shows one badge per statement equal to the flagged cells in that statement’s column', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    expect(badgeFor(EXPECTED.byDoc.d2019, '2019 P&L.xlsx')).toBeInTheDocument();
    expect(badgeFor(EXPECTED.byDoc.d2023, '2023 P&L.xlsx')).toBeInTheDocument();
  });

  it('the global CTA is the sum of the document badges', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    expect(
      screen.getByText(`${EXPECTED.total} financial values need your review`),
    ).toBeInTheDocument();
  });

  it('clicking a badge deep-links to Financials → Historicals pinned to that document', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    fireEvent.click(badgeFor(EXPECTED.byDoc.d2023, '2023 P&L.xlsx'));
    expect(pushSpy).toHaveBeenCalledWith(
      `/projects/${DEAL_ID}?tab=pl&fin=historicals&doc=d2023`,
      { scroll: false },
    );
  });

  it('"View Financials" carries the same document pin', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    const buttons = screen.getAllByRole('button', { name: 'View Financials' });
    // Files list in upload order → 2019 first.
    fireEvent.click(buttons[0]);
    expect(pushSpy).toHaveBeenLastCalledWith(
      `/projects/${DEAL_ID}?tab=pl&fin=historicals&doc=d2019`,
      { scroll: false },
    );
  });

  it('an accepted field decrements the document badge AND the global count from the same state', () => {
    LIVE_EXTRACTIONS = { ...EXTRACTIONS, d2023: EX_2023_AFTER_ACCEPT_FB };
    render(<DataRoomTab projectId={DEAL_ID} />);
    expect(badgeFor(1, '2023 P&L.xlsx')).toBeInTheDocument();
    expect(badgeFor(1, '2019 P&L.xlsx')).toBeInTheDocument();
    expect(screen.getByText('2 financial values need your review')).toBeInTheDocument();
    expect(screen.queryByText(/3 financial values/)).not.toBeInTheDocument();
  });
});
