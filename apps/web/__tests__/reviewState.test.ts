/**
 * FON-41 — one canonical review state (lib/reviewState) for extracted
 * historical values, shared by the Data Room badge and the Financials →
 * Historicals worksheet.
 *
 * Sam's finding: "the 2023 P&L shows 6 values to review … filtering to 2023
 * shows no corresponding flagged values … the provenance panel showed a 2019
 * P&L as the source document while the correction action referenced the 2023
 * P&L". Contracts locked here:
 *
 *  1. byDoc[doc] === number of flagged cells in THAT document's column, and
 *     every flagged cell is pinned to its own column's document.
 *  2. The LIVE extraction wins — an accepted / edited field clears its cell
 *     (and decrements the doc + global counts) even when the columns were
 *     built from an older copy of the extraction.
 *  3. Only rows that exist in the worksheet (metaKey + a rendered value) can
 *     be counted anywhere; expense lines (Part B) now qualify.
 *  4. Same-period statements keep separate, uniquely-labelled columns.
 */
import { describe, it, expect } from 'vitest';
import {
  buildReviewState,
  cellKey,
  cellsForYear,
  histHasData,
  histValue,
  needsReview,
  REVIEW_THRESHOLD,
  type ReviewRow,
} from '@/lib/reviewState';
import { buildHistoricalYears } from '@/lib/hooks/useHistoricals';
import { baseYearLabel, labelOrdinal, uniqueYearLabel } from '@/components/project/pl/HistoricalsSection';
import type { ExtractionField, ExtractionResult, WorkerDocument } from '@/lib/api';

// The worksheet rows the review state cares about (id + metaKey). `ag` has no
// metaKey on purpose — the P&Ls don't break it out, so it has no cell.
const ROWS: ReviewRow[] = [
  { id: 'occ', metaKey: 'occ' },
  { id: 'rooms_rev', metaKey: 'rooms' },
  { id: 'fb_rev', metaKey: 'fb' },
  { id: 'rooms_dept', metaKey: 'rooms_dept' },
  { id: 'ag' },
  { id: 'insurance', metaKey: 'insurance' },
  { id: 'noi', metaKey: 'noi' },
];

function doc(id: string, over: Partial<WorkerDocument> = {}): WorkerDocument {
  return {
    id,
    deal_id: 'deal-1',
    tenant_id: 't',
    filename: `${id}.xlsx`,
    doc_type: 'PNL',
    status: 'EXTRACTED',
    uploaded_at: '2026-01-01T00:00:00Z',
    content_hash: null,
    storage_key: null,
    size_bytes: 1,
    page_count: 1,
    parser: null,
    error_kind: null,
    error_message: null,
    ...over,
  };
}

function field(
  field_name: string,
  value: unknown,
  confidence: number | null,
  reviewed: string | null = null,
): ExtractionField {
  return { field_name, value, unit: null, source_page: 1, confidence, raw_text: null, reviewed };
}

function extraction(document_id: string, fields: ExtractionField[]): ExtractionResult {
  return { document_id, status: 'EXTRACTED', fields, confidence_report: null, agent_version: null, created_at: null };
}

const KEYS = 100;

// Two annual statements. 2019: Rooms is low-confidence. 2023: F&B and Rooms
// expense are low-confidence; Rooms is fine. Same field names in both docs —
// the exact shape that let a row-keyed lookup answer 2023 with 2019's field.
const d2019 = doc('d2019', { fiscal_year: 2019, uploaded_at: '2026-01-01T00:00:00Z' });
const d2023 = doc('d2023', { fiscal_year: 2023, uploaded_at: '2026-01-02T00:00:00Z' });
const ex2019 = extraction('d2019', [
  field('occupancy', 0.70, 0.95),
  field('rooms_revenue', 9_000_000, 0.60),
  field('fb_revenue', 1_500_000, 0.95),
  field('rooms_dept_expense', 2_000_000, 0.95),
  field('noi', 3_000_000, 0.95),
]);
const ex2023 = extraction('d2023', [
  field('occupancy', 0.75, 0.95),
  field('rooms_revenue', 11_000_000, 0.95),
  field('fb_revenue', 2_000_000, 0.50),
  field('rooms_dept_expense', 2_400_000, 0.70),
  field('noi', 4_000_000, 0.95),
]);

describe('needsReview — the single predicate', () => {
  it('flags strictly below the 85% threshold, never once accepted / edited', () => {
    expect(REVIEW_THRESHOLD).toBe(0.85);
    expect(needsReview({ confidence: 0.849 })).toBe(true);
    expect(needsReview({ confidence: 0.85 })).toBe(false);
    expect(needsReview({ confidence: 0.5, reviewed: 'accepted' })).toBe(false);
    expect(needsReview({ confidence: 0.5, reviewed: 'edited' })).toBe(false);
    expect(needsReview({ confidence: null })).toBe(false);
    expect(needsReview({ confidence: undefined })).toBe(false);
  });
});

describe('buildReviewState — one state for the Data Room badge and the worksheet cells', () => {
  const docs = [d2019, d2023];
  const extractions = { d2019: ex2019, d2023: ex2023 };
  const years = buildHistoricalYears(docs, extractions, KEYS).filter(histHasData);

  it('byDoc equals the flagged cells in that document’s column; every cell is pinned to its own document', () => {
    const s = buildReviewState(docs, extractions, ROWS, years);
    expect(s.byDoc.get('d2019')).toBe(1);
    expect(s.byDoc.get('d2023')).toBe(2);
    expect(s.total).toBe(3);

    // 2019 column → the 2019 statement; 2023 column → the 2023 statement.
    expect(s.byCell.get(cellKey('rooms_rev', '2019'))?.docId).toBe('d2019');
    expect(s.byCell.get(cellKey('fb_rev', '2023'))?.docId).toBe('d2023');
    expect(s.byCell.get(cellKey('rooms_dept', '2023'))?.docId).toBe('d2023');
    // Rooms is fine in 2023 — a low-confidence 2019 Rooms field must NOT bleed
    // into the 2023 column (the old row-keyed lookup did exactly that).
    expect(s.byCell.has(cellKey('rooms_rev', '2023'))).toBe(false);
    // The cell names the exact extracted field so Accept / Edit target it.
    expect(s.byCell.get(cellKey('fb_rev', '2023'))?.field).toBe('fb_revenue');
    expect(s.byCell.get(cellKey('fb_rev', '2023'))?.confidence).toBe(0.5);
  });

  it('badge and column counts reconcile by construction (Σ byDoc === byCell.size === total)', () => {
    const s = buildReviewState(docs, extractions, ROWS, years);
    let sum = 0;
    for (const n of s.byDoc.values()) sum += n;
    expect(sum).toBe(s.byCell.size);
    expect(sum).toBe(s.total);
  });

  it('the LIVE extraction wins: an accepted field clears its cell and decrements the doc count immediately', () => {
    // Columns were built from the pre-accept extraction (what useHistoricals
    // holds); the live map now carries the worker's post-accept field
    // (confidence 1.0, reviewed="accepted").
    const live = {
      d2019: ex2019,
      d2023: extraction('d2023', ex2023.fields.map((f) =>
        f.field_name === 'fb_revenue' ? { ...f, confidence: 1.0, reviewed: 'accepted' } : f,
      )),
    };
    const s = buildReviewState(docs, live, ROWS, years);
    expect(s.byDoc.get('d2023')).toBe(1);
    expect(s.byCell.has(cellKey('fb_rev', '2023'))).toBe(false);
    expect(s.byCell.has(cellKey('rooms_dept', '2023'))).toBe(true);
    expect(s.total).toBe(2);
  });

  it('an edited field (value corrected at source) clears the same way', () => {
    const live = {
      d2019: extraction('d2019', ex2019.fields.map((f) =>
        f.field_name === 'rooms_revenue' ? { ...f, value: 9_250_000, confidence: 1.0, reviewed: 'edited' } : f,
      )),
      d2023: ex2023,
    };
    const s = buildReviewState(docs, live, ROWS, years);
    expect(s.byDoc.get('d2019')).toBeUndefined();
    expect(s.total).toBe(2);
  });

  it('falls back to the captured meta until the live extraction has loaded', () => {
    const s = buildReviewState(docs, {}, ROWS, years);
    expect(s.total).toBe(3);
  });

  it('never counts a row without a worksheet cell (no metaKey), even when its field is low-confidence', () => {
    const withAg = {
      d2019: ex2019,
      d2023: extraction('d2023', [...ex2023.fields, field('administrative_general', 500_000, 0.4)]),
    };
    const yrs = buildHistoricalYears(docs, withAg, KEYS).filter(histHasData);
    const s = buildReviewState(docs, withAg, ROWS, yrs);
    expect(s.byDoc.get('d2023')).toBe(2);
    expect(s.byCell.has(cellKey('ag', '2023'))).toBe(false);
  });

  it('expense lines flag too (Part B): insurance and NOI carry per-year meta and a rendered cell', () => {
    const d = doc('dx', { fiscal_year: 2024 });
    const ex = extraction('dx', [
      field('occupancy', 0.7, 0.95),
      field('rooms_revenue', 10_000_000, 0.95),
      field('p_and_l_usali.non_operating.insurance_usd', 180_000, 0.55),
      field('noi', 3_500_000, 0.62),
    ]);
    const yrs = buildHistoricalYears([d], { dx: ex }, KEYS).filter(histHasData);
    const s = buildReviewState([d], { dx: ex }, ROWS, yrs);
    expect(s.byDoc.get('dx')).toBe(2);
    expect(s.byCell.get(cellKey('insurance', '2024'))?.field).toBe('p_and_l_usali.non_operating.insurance_usd');
    expect(s.byCell.get(cellKey('noi', '2024'))?.docId).toBe('dx');
    // The flagged cell renders the extracted value (not a placeholder).
    const col = yrs.find((y) => y.year === '2024')!;
    expect(histValue('insurance', col)).toBe(180_000);
    expect(histValue('noi', col)).toBe(3_500_000);
  });

  it('a column pinned to a document no longer in the deal is orphaned — never counted', () => {
    const s = buildReviewState([d2023], extractions, ROWS, years);
    expect(s.byDoc.has('d2019')).toBe(false);
    expect(s.byDoc.get('d2023')).toBe(2);
    expect(s.total).toBe(2);
  });

  it('cellsForYear lists a column’s flagged cells in row order (the deep-link scroll target)', () => {
    const s = buildReviewState(docs, extractions, ROWS, years);
    expect(cellsForYear(s, ROWS, '2023').map((c) => c.rowId)).toEqual(['fb_rev', 'rooms_dept']);
    expect(cellsForYear(s, ROWS, '2019').map((c) => c.rowId)).toEqual(['rooms_rev']);
    expect(cellsForYear(s, ROWS, '2021')).toEqual([]);
  });
});

describe('buildHistoricalYears — same-period statements keep separate columns', () => {
  it('labels the collision "2023" / "2023 (2)" in upload order and keeps both documents reviewable', () => {
    const a = doc('dA', { fiscal_year: 2023, uploaded_at: '2026-01-01T00:00:00Z' });
    const b = doc('dB', { fiscal_year: 2023, uploaded_at: '2026-01-05T00:00:00Z' });
    const exA = extraction('dA', [field('occupancy', 0.7, 0.95), field('rooms_revenue', 10_000_000, 0.6)]);
    const exB = extraction('dB', [field('occupancy', 0.72, 0.95), field('rooms_revenue', 10_500_000, 0.7)]);
    const yrs = buildHistoricalYears([a, b], { dA: exA, dB: exB }, KEYS).filter(histHasData);
    expect(yrs.map((y) => y.year)).toEqual(['2023', '2023 (2)']);
    expect(yrs[0].docId).toBe('dA');
    expect(yrs[1].docId).toBe('dB');
    // Day-count logic still keys off the base year (2023 = 365 days).
    expect(yrs[1].days).toBe(365);

    const s = buildReviewState([a, b], { dA: exA, dB: exB }, ROWS, yrs);
    expect(s.byDoc.get('dA')).toBe(1);
    expect(s.byDoc.get('dB')).toBe(1);
    expect(s.byCell.get(cellKey('rooms_rev', '2023 (2)'))?.docId).toBe('dB');
  });

  it('two T-12s become "T-12" / "T-12 (2)" and annual columns still sort ahead of them', () => {
    const y23 = doc('y23', { fiscal_year: 2023, uploaded_at: '2026-01-03T00:00:00Z' });
    const t1 = doc('t1', { doc_type: 'T12', uploaded_at: '2026-01-01T00:00:00Z' });
    const t2 = doc('t2', { doc_type: 'T12', uploaded_at: '2026-01-02T00:00:00Z' });
    const ex = (id: string) => extraction(id, [field('occupancy', 0.7, 0.95), field('rooms_revenue', 10_000_000, 0.95)]);
    const yrs = buildHistoricalYears([y23, t1, t2], { y23: ex('y23'), t1: ex('t1'), t2: ex('t2') }, KEYS).filter(histHasData);
    expect(yrs.map((y) => y.year)).toEqual(['2023', 'T-12', 'T-12 (2)']);
  });

  it('label helpers round-trip', () => {
    expect(baseYearLabel('2023 (2)')).toBe('2023');
    expect(baseYearLabel('2023')).toBe('2023');
    expect(baseYearLabel('T-12 (3)')).toBe('T-12');
    expect(labelOrdinal('2023')).toBe(1);
    expect(labelOrdinal('2023 (3)')).toBe(3);
    expect(uniqueYearLabel('2023', new Set())).toBe('2023');
    expect(uniqueYearLabel('2023', new Set(['2023']))).toBe('2023 (2)');
    expect(uniqueYearLabel('2023', new Set(['2023', '2023 (2)']))).toBe('2023 (3)');
  });

  it('returns no columns until the deal’s key count is known (same gate as the worksheet)', () => {
    expect(buildHistoricalYears([d2019], { d2019: ex2019 }, 0)).toEqual([]);
  });
});
