/**
 * Data Room tab — canonical rebuild regression suite (Data Room v2, FON-72).
 *
 * Contracts locked here mirror the canonical `Data Room v2.dc.html`:
 *
 *  1. FIELD REVIEW IS GROUPED BY SECTION. The inline document review
 *     (`isDetailView`) renders one uppercase section subhead per schema prefix
 *     ("Property Overview", "Financial Summary"), not a flat list.
 *
 *  2. FILTER PILLS. The review carries All / Needs Review / Reviewed pills with
 *     live counts; selecting "Needs Review" narrows the table to only the
 *     flagged rows (and drops sections that no longer have a visible field).
 *
 *  3. DOCUMENT COVERAGE CARD. The coverage card reports "N of 10 types",
 *     buckets files into their category, exposes the drag handle +
 *     open-in-new-tab / download affordances, and offers the full-label period
 *     dropdown including the "Not Sure" option.
 *
 * The tab reads exclusively from mocked hooks / api — no prototype numbers.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';

// ── Shared mocks ──────────────────────────────────────────────────────────
// Non-numeric id → the live worker path runs (numeric ids are demo/mock).
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  // ?reviewDoc deep-link opens the inline detail review for that document.
  useSearchParams: () => ({
    get: (k: string) => (k === 'reviewDoc' ? 'doc-1' : null),
  }),
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

// One extracted document with fields across two schema sections; one field is
// low-confidence (needs review) so the pills + review-reason line light up.
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: [
      {
        id: 'doc-1',
        filename: 'Miami Beach Offering Memorandum.pdf',
        doc_type: 'OM',
        status: 'EXTRACTED',
        uploaded_at: '2026-01-01T00:00:00Z',
        size_bytes: 1024,
      },
    ],
    uploading: false,
    upload: vi.fn(),
    extractions: {
      'doc-1': {
        status: 'EXTRACTED',
        confidence_report: { overall: 0.9 },
        fields: [
          { field_name: 'property_overview.property_name', value: "The Angler's", unit: null, confidence: 0.98, reviewed: null, source_page: 1, raw_text: null },
          { field_name: 'property_overview.year_built', value: 1998, unit: null, confidence: 0.62, reviewed: null, source_page: 3, raw_text: null },
          { field_name: 'financial_summary.asking_price', value: 28500000, unit: 'USD', confidence: 0.94, reviewed: null, source_page: 2, raw_text: null },
        ],
      },
    },
    error: null,
    refresh: vi.fn(),
    refreshExtraction: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock('@/lib/hooks/useEngineOutputs', () => ({ useEngineOutputs: () => ({ outputs: null }) }));
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ status: 'idle', run: vi.fn() }) }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/auth', () => ({ useCurrentRole: () => 'org:member' }));

// Heavy siblings that only render in the coverage sub-view (not the detail
// review under test) — neutralize their import graph.
vi.mock('@/components/project/pl/GroundedWorksheet', () => ({
  isReviewableFinancialField: () => true,
}));
vi.mock('@/components/project/validation/GapChipsStrip', () => ({ GapChipsStrip: () => null }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));

import DataRoomTab from '@/components/project/DataRoomTab';
// The coverage card is exported and light (Card / Badge / lucide only) — it is
// intentionally NOT mocked, so it is exercised for real in its own describe.
import { DocumentCoverage, type CoverageFile } from '@/components/project/DocumentCoverage';

afterEach(cleanup);

describe('Data Room — inline document review (isDetailView)', () => {
  it('groups extracted fields by section', () => {
    render(<DataRoomTab projectId="deal-uuid-1" />);
    // Section subheads derived from the schema prefix.
    expect(screen.getByText('Property Overview')).toBeInTheDocument();
    expect(screen.getByText('Financial Summary')).toBeInTheDocument();
    // Fields land under their sections with humanized labels.
    expect(screen.getByText('Property Name')).toBeInTheDocument();
    expect(screen.getByText('Year Built')).toBeInTheDocument();
    expect(screen.getByText('Asking Price')).toBeInTheDocument();
  });

  it('renders All / Needs Review / Reviewed filter pills with counts', () => {
    render(<DataRoomTab projectId="deal-uuid-1" />);
    expect(screen.getByRole('button', { name: /All/ })).toBeInTheDocument();
    const needsReview = screen.getByRole('button', { name: /Needs Review/ });
    const reviewed = screen.getByRole('button', { name: /Reviewed/ });
    // One low-confidence field (Year Built, 62%) is flagged.
    expect(needsReview.textContent).toContain('1');
    expect(reviewed.textContent).toContain('2');
  });

  it('shows a plain-language review reason on a flagged field', () => {
    render(<DataRoomTab projectId="deal-uuid-1" />);
    expect(
      screen.getByText((t) => /Low extraction confidence \(62%\)/.test(t)),
    ).toBeInTheDocument();
  });

  it('filters to only flagged rows when "Needs Review" is selected', () => {
    render(<DataRoomTab projectId="deal-uuid-1" />);
    fireEvent.click(screen.getByRole('button', { name: /Needs Review/ }));
    // The flagged field stays; a resolved/high-confidence one is filtered out,
    // and its now-empty section header disappears too.
    expect(screen.getByText('Year Built')).toBeInTheDocument();
    expect(screen.queryByText('Property Name')).not.toBeInTheDocument();
    expect(screen.queryByText('Financial Summary')).not.toBeInTheDocument();
  });
});

describe('Data Room — document coverage card', () => {
  const files: CoverageFile[] = [
    { id: 'om1', name: 'Offering Memorandum.pdf', docType: 'OM', fields: 440, confidence: 97, toReview: 0, fiscalYear: null, status: 'EXTRACTED' },
    { id: 'pnl1', name: 'November 2024 Financials.xlsx', docType: 'PNL_MONTHLY', fields: 156, confidence: 88, toReview: 4, fiscalYear: 2024, status: 'EXTRACTED' },
  ];

  const renderCard = (extra?: Partial<React.ComponentProps<typeof DocumentCoverage>>) =>
    render(
      <DocumentCoverage
        files={files}
        onReclassify={vi.fn()}
        onOpenDoc={vi.fn()}
        onOpenInNewTab={vi.fn()}
        onDownload={vi.fn()}
        {...extra}
      />,
    );

  it('reports coverage as "N of 10 types"', () => {
    renderCard();
    const summary = screen.getByText(
      (_c, el) => el?.tagName === 'P' && /2 of 10 types/.test(el.textContent || ''),
    );
    expect(summary).toBeInTheDocument();
  });

  it('buckets files into their category rows', () => {
    renderCard();
    // "Offering Memorandum" renders both as the category row label AND as an
    // <option> in the OM file's doc-type <select>, so scope to "at least one".
    expect(screen.getAllByText('Offering Memorandum').length).toBeGreaterThan(0);
    expect(screen.getByText('Financial Statements')).toBeInTheDocument();
  });

  it('exposes the drag handle and open-in-new-tab / download affordances', () => {
    renderCard();
    expect(screen.getAllByLabelText(/Drag to move/).length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText(/Open .* in a new tab/).length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText(/^Download /).length).toBeGreaterThan(0);
  });

  it('offers the full-label period dropdown including "Not Sure"', () => {
    renderCard();
    // The financial row's period <select> carries the spelled-out options.
    expect(screen.getByRole('option', { name: 'Year-To-Date' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'Not Sure' })).toBeInTheDocument();
  });

  it('collapses the whole card from the header chevron', () => {
    renderCard();
    // Renders in >1 place (category label + doc-type <option>); collapsing the
    // card unmounts the whole body, so every instance must be gone afterwards.
    expect(screen.getAllByText('Offering Memorandum').length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /Document coverage/ }));
    expect(screen.queryAllByText('Offering Memorandum').length).toBe(0);
  });

  it('calls the open + download handlers with the document id', () => {
    const onOpenInNewTab = vi.fn();
    const onDownload = vi.fn();
    renderCard({ onOpenInNewTab, onDownload });
    const pnlOpen = screen.getByLabelText('Open November 2024 Financials.xlsx in a new tab');
    fireEvent.click(pnlOpen);
    expect(onOpenInNewTab).toHaveBeenCalledWith('pnl1');
    const pnlDownload = screen.getByLabelText('Download November 2024 Financials.xlsx');
    fireEvent.click(pnlDownload);
    expect(onDownload).toHaveBeenCalledWith('pnl1');
  });
});
