/**
 * W5 review R2-I1 — a value the display-string repair cleared is never
 * invisible. The migration sets such a field to empty, confidence 0, and a
 * note starting "Needs review: …". That field:
 *
 *  1. counts as needing review (lib/reviewReasons.flaggedFieldsForDoc) for a
 *     financial statement too — it is in the "Review Recommended" count, the
 *     reason line and the global "values need your review" count;
 *  2. is listed under "Needs Review" in the Data Room inline review, with "—"
 *     and the note;
 *  3. shows on the document-detail page with "—" and the note.
 *
 * A null field WITHOUT such a note (nothing extracted) stays as before.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';
import type { ExtractionField, ExtractionResult } from '@/lib/api';
import { flaggedFieldsForDoc, isClearedForReview } from '@/lib/reviewReasons';
import { DEAL_ID, DOCS, EXTRACTIONS, KEYS } from './helpers/fon41Fixture';

if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

const NOTE =
  "Needs review: an earlier edit saved the display text '$10M' instead of a figure, and the figure it rounded cannot be recovered from it. Enter the statement's figure or re-extract the document.";

const cleared = (name: string): ExtractionField => ({
  field_name: name,
  value: null,
  unit: 'USD',
  source_page: 3,
  confidence: 0,
  raw_text: 'D_ROOMS_CON 400000 ... 9.49641e+06',
  note: NOTE,
});

describe('isClearedForReview', () => {
  it('is true only for an empty numeric field carrying a Needs review note', () => {
    expect(isClearedForReview(cleared('p_and_l_usali.operating_revenue.rooms_revenue'))).toBe(true);
    expect(isClearedForReview({ ...cleared('x'), note: null })).toBe(false);
    expect(isClearedForReview({ ...cleared('x'), value: 9_496_407.22 })).toBe(false);
    expect(isClearedForReview({ ...cleared('x'), reviewed: 'edited' })).toBe(false);
    expect(isClearedForReview({ ...cleared('x'), unit: null })).toBe(false);
    expect(isClearedForReview({ ...cleared('x'), note: 'Reconciled to the Summary sheet' })).toBe(false);
  });

  it('a financial statement lists it after its worksheet cells', () => {
    const fields: ExtractionField[] = [
      { field_name: 'rooms_revenue', value: 11_000_000, unit: 'USD', source_page: 2, confidence: 0.95, raw_text: null },
      cleared('p_and_l_usali.operating_revenue.rooms_revenue'),
      { field_name: 'p_and_l_usali.fixed_charges.insurance', value: null, unit: 'USD', source_page: null, confidence: null, raw_text: null },
    ];
    const byCell = new Map([
      ['fb|2023', { rowId: 'fb', year: '2023', docId: 'd2023', field: 'fb_revenue', confidence: 0.5 }],
    ]);
    const out = flaggedFieldsForDoc({ docId: 'd2023', financial: true, fields, reviewState: { byCell } });
    expect(out.map((f) => f.field)).toEqual(['fb_revenue', 'p_and_l_usali.operating_revenue.rooms_revenue']);
    expect(out[1].confidence).toBe(0);
  });
});

// ── Data Room ─────────────────────────────────────────────────────────────

let LIVE: Record<string, ExtractionResult> = EXTRACTIONS;
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1', docId: 'd2023' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: (_k: string) => null }),
}));
vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  workerUrl: () => 'http://worker.test',
  api: {
    documents: {
      downloadUrlSigned: vi.fn(),
      download: vi.fn(),
      reviewField: vi.fn().mockResolvedValue(undefined),
      reclassify: vi.fn(),
      acceptClassification: vi.fn(),
      acceptYear: vi.fn(),
    },
  },
}));
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: DOCS,
    loading: false,
    error: null,
    uploading: false,
    upload: vi.fn(),
    extractions: LIVE,
    refresh: vi.fn(),
    refreshExtraction: vi.fn().mockResolvedValue(undefined),
  }),
}));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: DEAL_ID, keys: KEYS, field_overrides: {} },
    status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
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

import DataRoomTab from '@/components/project/DataRoomTab';
import DocumentDetailPage from '@/app/projects/[id]/documents/[docId]/page';

const withCleared = (): Record<string, ExtractionResult> => ({
  ...EXTRACTIONS,
  d2023: {
    ...EXTRACTIONS.d2023,
    fields: [...EXTRACTIONS.d2023.fields, cleared('p_and_l_usali.operating_revenue.rooms_revenue')],
  },
});

afterEach(() => {
  cleanup();
  LIVE = EXTRACTIONS;
});

describe('Data Room — a cleared value needs review and says why', () => {
  it('counts in Review Recommended, the reason line and the global count', () => {
    LIVE = withCleared();
    render(<DataRoomTab projectId={DEAL_ID} />);
    // 2023 had 2 flagged worksheet cells; the cleared rooms revenue makes 3.
    expect(screen.getByRole('button', { name: 'Review 3 flagged values in 2023 P&L.xlsx' })).toBeInTheDocument();
    expect(screen.getByText(/^3 fields at 0–70% · /)).toBeInTheDocument();
    expect(screen.getByText('4 financial values need your review')).toBeInTheDocument();
  });

  it('lists it under Needs Review with "—" and the note', () => {
    LIVE = withCleared();
    render(<DataRoomTab projectId={DEAL_ID} />);
    fireEvent.click(screen.getByText(/^3 fields at 0–70% · /));
    expect(screen.getByText('3 need review')).toBeInTheDocument();
    expect(screen.getByText(NOTE)).toBeInTheDocument();
    expect(screen.getByText('0% confidence · p.3')).toBeInTheDocument();
  });

  it('a null field with no note is not counted (nothing changes)', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    expect(screen.getByRole('button', { name: 'Review 2 flagged values in 2023 P&L.xlsx' })).toBeInTheDocument();
    expect(screen.getByText('3 financial values need your review')).toBeInTheDocument();
  });
});

describe('Document detail — a cleared value is shown with "—" and the note', () => {
  it('renders the row, the dash and the note', () => {
    LIVE = withCleared();
    render(<DocumentDetailPage />);
    const row = screen.getByText('p_and_l_usali.operating_revenue.rooms_revenue').closest('tr')!;
    expect(row).toBeTruthy();
    expect(row.textContent).toContain('—');
    expect(row.textContent).toContain(NOTE);
  });
});
