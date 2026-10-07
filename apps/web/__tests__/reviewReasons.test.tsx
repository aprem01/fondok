/**
 * Data Room — "Review Recommended" reason line + per-field explanation
 * (FON-41 external testers E-002 / R-041 / R-042 / R-043 / R-045).
 *
 * Contracts locked here:
 *
 *  1. A flagged document row carries a one-line, DATA-DERIVED reason under
 *     its status: `N fields at X% · <name> (p.N), …` — built from the SAME
 *     flagged list the "N to review" badge counts. A document with nothing
 *     flagged renders no reason at all.
 *
 *  2. Clicking the reason opens the document's inline field review filtered
 *     to "Needs Review", and works for EVERY doc type with an extraction
 *     (STR_TREND, CBRE_HORIZONS — not only financial statements). The
 *     existing Accept / Edit correction path is reachable from it.
 *
 *  3. Each flagged row explains itself with field data only: `X% confidence
 *     · PDF p.N`, the verbatim raw_text under a "source text" label (or
 *     "no source text captured"), and the `chart / table read` tag ONLY at
 *     exactly 0.5 confidence with raw text present.
 *
 *  4. The humanizer keeps ADR / RevPAR / F&B and carries a pipeline stage
 *     from the field path (R-045).
 *
 * The tab reads exclusively from mocked hooks / api — no prototype numbers.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { ExtractionField, ExtractionResult, WorkerDocument } from '@/lib/api';
import {
  buildReviewReason,
  fieldExplanation,
  flaggedFieldsForDoc,
  humanizeReviewField,
  isChartTableRead,
  isFlaggedField,
  sourcePageLabel,
  type FlaggedField,
} from '@/lib/reviewReasons';
import type { ReviewCell } from '@/lib/reviewState';

// jsdom has no scrollIntoView; the field review scrolls the highlighted row.
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};

// ── Shared mocks (mirror dataRoomTab.test.tsx) ────────────────────────────
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

const DEAL_ID = 'deal-uuid-1';

function doc(id: string, filename: string, doc_type: string): WorkerDocument {
  return {
    id,
    deal_id: DEAL_ID,
    tenant_id: 't',
    filename,
    doc_type,
    status: 'EXTRACTED',
    uploaded_at: '2026-01-01T00:00:00Z',
    content_hash: null,
    storage_key: null,
    size_bytes: 2048,
    page_count: 20,
    parser: null,
    error_kind: null,
    error_message: null,
  };
}

function field(
  field_name: string,
  value: unknown,
  confidence: number,
  source_page: number | null,
  raw_text: string | null,
  reviewed: string | null = null,
): ExtractionField {
  return { field_name, value, unit: null, source_page, confidence, raw_text, reviewed };
}

function extraction(document_id: string, fields: ExtractionField[], overall: number): ExtractionResult {
  return {
    document_id,
    status: 'EXTRACTED',
    fields,
    confidence_report: {
      overall,
      by_field: {},
      low_confidence_fields: fields.filter((f) => (f.confidence ?? 0) < 0.85).map((f) => f.field_name),
      requires_human_review: fields.some((f) => (f.confidence ?? 0) < 0.85),
    },
    agent_version: null,
    created_at: null,
  };
}

// R-043: two CoStar chart / table reads at exactly 50% with their raw rows.
const STR = doc('str1', 'CoStar Market Report.pdf', 'STR_TREND');
const STR_RAW_ADR = '2021 | $230 | -10%';
const STR_RAW_REVPAR = '2020 | $95 | -41%';
const EX_STR = extraction(
  'str1',
  [
    field('ttm_performance.market.occupancy_pct', 0.71, 0.96, 4, null),
    field('ttm_performance.market.adr_2021_annual', 230, 0.5, 8, STR_RAW_ADR),
    field('ttm_performance.market.revpar_2020_trough', 95, 0.5, 9, STR_RAW_REVPAR),
  ],
  0.82,
);

// R-042: a CBRE Horizons doc — one 62% field WITH raw text (no tag), one at
// exactly 50% WITHOUT raw text (no tag, "no source text captured").
const CBRE = doc('cbre1', 'CBRE Horizons.pdf', 'CBRE_HORIZONS');
const EX_CBRE = extraction(
  'cbre1',
  [
    field('cbre_horizons.long_run_avg.revpar_change_pct', 0.031, 0.97, 3, null),
    field('cbre_horizons.long_run_avg.supply_change_pct', 0.021, 0.62, 5, 'Supply change 2.1%'),
    field('cbre_horizons.short_term_rental.available_supply', 1200, 0.5, 12, null),
  ],
  0.85,
);

// A clean OM — nothing flagged → no reason line.
const OM = doc('om1', 'Offering Memorandum.pdf', 'OM');
const EX_OM = extraction(
  'om1',
  [
    field('property_overview.property_name', 'The Angler’s', 0.98, 1, null),
    field('property_overview.key_count', 132, 0.95, 1, null),
  ],
  0.97,
);

const DOCS: WorkerDocument[] = [OM, STR, CBRE];
const EXTRACTIONS: Record<string, ExtractionResult> = { om1: EX_OM, str1: EX_STR, cbre1: EX_CBRE };

vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: DOCS,
    loading: false,
    error: null,
    uploading: false,
    upload: vi.fn(),
    extractions: EXTRACTIONS,
    refresh: vi.fn(),
    refreshExtraction: vi.fn().mockResolvedValue(undefined),
  }),
}));

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: DEAL_ID, keys: 132, field_overrides: {} },
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
vi.mock('@/components/project/pl/GroundedWorksheet', () => ({ WORKSHEET_ROWS: [] }));
vi.mock('@/components/project/validation/GapChipsStrip', () => ({ GapChipsStrip: () => null }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
}));

import DataRoomTab from '@/components/project/DataRoomTab';

afterEach(() => {
  cleanup();
  pushSpy.mockClear();
});

const STR_REASON = '2 fields at 50% · ADR 2021 (p.8), RevPAR 2020 Trough (p.9)';
const CBRE_REASON = '2 fields at 50–62% · Supply Change (p.5), Available Supply (p.12)';

// ── Helpers ───────────────────────────────────────────────────────────────

describe('reviewReasons — humanizer', () => {
  it('keeps ADR / RevPAR / F&B and drops the period suffix', () => {
    expect(humanizeReviewField('ttm_performance.market.adr_2021_annual')).toBe('ADR 2021');
    expect(humanizeReviewField('ttm_performance.market.revpar_2020_trough')).toBe('RevPAR 2020 Trough');
    expect(humanizeReviewField('p_and_l_usali.fb_revenue')).toBe('F&B Revenue');
    expect(humanizeReviewField('ttm_summary_per_om.revpar_usd')).toBe('RevPAR');
  });

  it('carries the pipeline stage from the field path (R-045)', () => {
    expect(humanizeReviewField('market_overview.supply_pipeline.under_construction.rooms')).toBe(
      'Under construction rooms',
    );
    expect(humanizeReviewField('market_overview.supply_pipeline.final_planning.rooms')).toBe(
      'Final planning rooms',
    );
    expect(humanizeReviewField('market_overview.supply_pipeline.under_construction.open_date')).toBe(
      'Under construction · Open Date',
    );
    // The leaf IS the stage — no doubling.
    expect(humanizeReviewField('market_overview.supply_pipeline.under_construction')).toBe(
      'Under Construction',
    );
  });
});

describe('reviewReasons — reason line', () => {
  const ff = (field: string, confidence: number, sourcePage: number | null = null): FlaggedField => ({
    field,
    confidence,
    sourcePage,
    rawText: null,
  });

  it('two 50% fields → one band, both humanized names with pages', () => {
    const r = buildReviewReason([
      ff('ttm_performance.market.adr_2021_annual', 0.5, 8),
      ff('ttm_performance.market.revpar_2020_trough', 0.5, 9),
    ]);
    expect(r?.count).toBe(2);
    expect(r?.text).toBe(STR_REASON);
  });

  it('mixed confidences → a lo–hi band; a missing page → no "(p.N)"', () => {
    const r = buildReviewReason([ff('property_overview.year_built', 0.62, 3), ff('x.adr', 0.5)]);
    expect(r?.text).toBe('2 fields at 50–62% · Year Built (p.3), ADR');
  });

  it('singular / cap at 3 names + N more / empty → null', () => {
    expect(buildReviewReason([ff('property_overview.year_built', 0.62, 3)])?.text).toBe(
      '1 field at 62% · Year Built (p.3)',
    );
    const five = buildReviewReason([
      ff('a.one', 0.5, 1), ff('a.two', 0.5, 1), ff('a.three', 0.5, 1), ff('a.four', 0.5, 1), ff('a.five', 0.5, 1),
    ]);
    expect(five?.text).toBe('5 fields at 50% · One (p.1), Two (p.1), Three (p.1), +2 more');
    expect(buildReviewReason([])).toBeNull();
  });
});

describe('reviewReasons — per-field explanation', () => {
  it('chart / table tag only at exactly 0.5 WITH raw text', () => {
    expect(isChartTableRead(0.5, STR_RAW_ADR)).toBe(true);
    expect(isChartTableRead(0.5, null)).toBe(false);
    expect(isChartTableRead(0.5, '   ')).toBe(false);
    expect(isChartTableRead(0.62, STR_RAW_ADR)).toBe(false);
    expect(isChartTableRead(0.504, STR_RAW_ADR)).toBe(false);
    expect(isChartTableRead(null, STR_RAW_ADR)).toBe(false);
  });

  it('confidence as a percentage + "PDF p.N" for PDFs, "p.N" otherwise', () => {
    expect(fieldExplanation(0.5, 8, 'CoStar Market Report.pdf')).toBe('50% confidence · PDF p.8');
    expect(fieldExplanation(0.62, 3, '2023 P&L.xlsx')).toBe('62% confidence · p.3');
    expect(fieldExplanation(0.7, null, 'x.pdf')).toBe('70% confidence');
    expect(sourcePageLabel(0, 'x.pdf')).toBeNull();
  });
});

describe('reviewReasons — the flagged rule is the coverage card’s rule', () => {
  it('isFlaggedField: rounded confidence < 85% and not reviewed', () => {
    expect(isFlaggedField({ confidence: 0.84, reviewed: null })).toBe(true);
    expect(isFlaggedField({ confidence: 0.845, reviewed: null })).toBe(false); // rounds to 85
    expect(isFlaggedField({ confidence: 0.5, reviewed: 'accepted' })).toBe(false);
    expect(isFlaggedField({ confidence: null, reviewed: null })).toBe(true);
  });

  it('non-financial docs flag their OWN sub-85% unreviewed fields, in field order', () => {
    const out = flaggedFieldsForDoc({
      docId: 'str1',
      financial: false,
      fields: EX_STR.fields,
      reviewState: { byCell: new Map() },
    });
    expect(out.map((f) => f.field)).toEqual([
      'ttm_performance.market.adr_2021_annual',
      'ttm_performance.market.revpar_2020_trough',
    ]);
    expect(out[0]).toMatchObject({ confidence: 0.5, sourcePage: 8, rawText: STR_RAW_ADR });
  });

  it('financial docs take exactly their own worksheet cells from the shared review state', () => {
    const cell = (docId: string, field: string, rowId: string): [string, ReviewCell] => [
      `${rowId}|2023`,
      { rowId, year: '2023', docId, field, confidence: 0.5 },
    ];
    const byCell = new Map<string, ReviewCell>([
      cell('d2023', 'fb_revenue', 'fb_rev'),
      cell('d2019', 'rooms_revenue', 'rooms_rev'),
    ]);
    const mine = flaggedFieldsForDoc({
      docId: 'd2023',
      financial: true,
      fields: [field('fb_revenue', 1, 0.5, 2, null), field('other_low', 1, 0.3, 2, null)],
      reviewState: { byCell },
    });
    // The other document's cell and this doc's non-worksheet low field are NOT included.
    expect(mine.map((f) => f.field)).toEqual(['fb_revenue']);
    expect(mine[0].sourcePage).toBe(2);
  });
});

// ── Rendering ─────────────────────────────────────────────────────────────

describe('Data Room — reason line under "Review Recommended" (E-002 / R-041 / R-042)', () => {
  it('(a) a doc with two 50% fields renders the reason with both names and pages', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    expect(screen.getByText(STR_REASON)).toBeInTheDocument();
    // The CBRE Horizons doc (R-042) explains itself too — not only financial docs.
    expect(screen.getByText(CBRE_REASON)).toBeInTheDocument();
  });

  it('(b) a doc with no flagged fields renders no reason', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    const reasons = screen.getAllByTestId('review-reason');
    expect(reasons).toHaveLength(2);
    for (const r of reasons) expect(r.textContent).not.toMatch(/Property Name|Keys/);
    // The OM row is "Ready for Review" with no "to review" badge.
    expect(screen.getAllByText('Ready for Review')).toHaveLength(1);
    expect(screen.queryByRole('button', { name: /flagged value.* in Offering Memorandum\.pdf/ })).toBeNull();
  });

  it('the reason counts exactly what the badge counts', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    expect(
      screen.getByRole('button', { name: 'Review 2 flagged values in CoStar Market Report.pdf' }),
    ).toBeInTheDocument();
    expect(screen.getByText(STR_REASON).textContent).toMatch(/^2 fields/);
  });
});

describe('Data Room — click-through to the field view (R-043 / R-042)', () => {
  it('STR_TREND: opens the inline review filtered to the flagged fields, with the tag + verbatim source text', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    fireEvent.click(screen.getByText(STR_REASON));

    // Inline review, not the Financials deep link.
    expect(pushSpy).not.toHaveBeenCalled();
    expect(screen.getByText('CoStar Market Report.pdf')).toBeInTheDocument();
    expect(screen.getByText('2 need review')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Needs Review/ })).toHaveAttribute('aria-pressed', 'true');

    // Only the flagged rows are listed; the 96% occupancy field is filtered out.
    expect(screen.getByText('ADR 2021')).toBeInTheDocument();
    expect(screen.getByText('RevPAR 2020 Trough')).toBeInTheDocument();
    expect(screen.queryByText('Occupancy')).toBeNull();

    // (c) per-field explanation: % + PDF page, the tag at exactly 0.5 with
    // raw text, and the raw text verbatim under "source text".
    expect(screen.getByText('50% confidence · PDF p.8')).toBeInTheDocument();
    expect(screen.getByText('50% confidence · PDF p.9')).toBeInTheDocument();
    expect(screen.getAllByText('chart / table read')).toHaveLength(2);
    expect(screen.getAllByText('source text')).toHaveLength(2);
    expect(screen.getByText(STR_RAW_ADR)).toBeInTheDocument();
    expect(screen.getByText(STR_RAW_REVPAR)).toBeInTheDocument();

    // The existing correction path is reachable: Accept + Edit per flagged row.
    expect(screen.getAllByRole('button', { name: 'Accept' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Edit ✎' })).toHaveLength(2);
  });

  it('CBRE_HORIZONS: opens too; no tag at 62% or at 0.5 without raw text, which reads "no source text captured"', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    fireEvent.click(screen.getByText(CBRE_REASON));

    expect(screen.getByText('CBRE Horizons.pdf')).toBeInTheDocument();
    expect(screen.getByText('2 need review')).toBeInTheDocument();
    expect(screen.getByText('62% confidence · PDF p.5')).toBeInTheDocument();
    expect(screen.getByText('Supply change 2.1%')).toBeInTheDocument();
    expect(screen.getByText('50% confidence · PDF p.12')).toBeInTheDocument();
    expect(screen.getByText('no source text captured')).toBeInTheDocument();
    expect(screen.queryByText('chart / table read')).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Accept' })).toHaveLength(2);
  });

  it('Back returns to coverage with the filter reset; "View data" opens unfiltered', () => {
    render(<DataRoomTab projectId={DEAL_ID} />);
    fireEvent.click(screen.getByText(STR_REASON));
    fireEvent.click(screen.getByRole('button', { name: '← Back' }));
    expect(screen.getByText(STR_REASON)).toBeInTheDocument();

    // Plain "View data" on the STR row opens on "All" (occupancy visible).
    const row = screen.getByText('CoStar Market Report.pdf').closest('li')!;
    fireEvent.click(within(row).getByRole('button', { name: 'View data' }));
    expect(screen.getByRole('button', { name: /^All/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Occupancy')).toBeInTheDocument();
  });
});
