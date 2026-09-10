/**
 * FON-41 shared fixture — one deal, two annual P&Ls, so the Data Room test and
 * the worksheet test assert the SAME expected counts against the SAME data
 * (the acceptance criterion is that the two surfaces agree per document).
 *
 *   2019 P&L  → Rooms Revenue at 60%                 → 1 to review
 *   2023 P&L  → F&B Revenue at 50%, Rooms exp. at 70% → 2 to review
 *   global                                            → 3
 *
 * Same field names in both documents — the exact shape that let the old
 * row-keyed lookup answer a 2023 cell with the 2019 statement.
 */
import type { ExtractionField, ExtractionResult, WorkerDocument } from '@/lib/api';

export const DEAL_ID = 'deal-uuid-1';
export const KEYS = 100;

export function doc(id: string, over: Partial<WorkerDocument> = {}): WorkerDocument {
  return {
    id,
    deal_id: DEAL_ID,
    tenant_id: 't',
    filename: `${id}.xlsx`,
    doc_type: 'PNL',
    status: 'EXTRACTED',
    uploaded_at: '2026-01-01T00:00:00Z',
    content_hash: null,
    storage_key: null,
    size_bytes: 2048,
    page_count: 3,
    parser: null,
    error_kind: null,
    error_message: null,
    ...over,
  };
}

export function field(
  field_name: string,
  value: unknown,
  confidence: number | null,
  reviewed: string | null = null,
): ExtractionField {
  return { field_name, value, unit: 'USD', source_page: 2, confidence, raw_text: null, reviewed };
}

export function extraction(document_id: string, fields: ExtractionField[]): ExtractionResult {
  return {
    document_id,
    status: 'EXTRACTED',
    fields,
    confidence_report: { overall: 0.9, by_field: {}, low_confidence_fields: [], requires_human_review: false },
    agent_version: null,
    created_at: null,
  };
}

export const DOC_2019 = doc('d2019', {
  filename: '2019 P&L.xlsx',
  fiscal_year: 2019,
  uploaded_at: '2026-01-01T00:00:00Z',
});
export const DOC_2023 = doc('d2023', {
  filename: '2023 P&L.xlsx',
  fiscal_year: 2023,
  uploaded_at: '2026-01-02T00:00:00Z',
});

export const EX_2019 = extraction('d2019', [
  field('occupancy', 0.70, 0.95),
  field('rooms_revenue', 9_000_000, 0.60),
  field('fb_revenue', 1_500_000, 0.95),
  field('rooms_dept_expense', 2_000_000, 0.95),
  field('noi', 3_000_000, 0.95),
]);
export const EX_2023 = extraction('d2023', [
  field('occupancy', 0.75, 0.95),
  field('rooms_revenue', 11_000_000, 0.95),
  field('fb_revenue', 2_000_000, 0.50),
  field('rooms_dept_expense', 2_400_000, 0.70),
  field('noi', 4_000_000, 0.95),
]);

/** What the worker returns for the 2023 extraction after "accept" on F&B. */
export const EX_2023_AFTER_ACCEPT_FB = extraction(
  'd2023',
  EX_2023.fields.map((f) =>
    f.field_name === 'fb_revenue' ? { ...f, confidence: 1.0, reviewed: 'accepted' } : f,
  ),
);

export const DOCS: WorkerDocument[] = [DOC_2019, DOC_2023];
export const EXTRACTIONS: Record<string, ExtractionResult> = { d2019: EX_2019, d2023: EX_2023 };

export const EXPECTED = {
  byDoc: { d2019: 1, d2023: 2 },
  total: 3,
  /** Row order in the worksheet: F&B Revenue comes before Rooms (dept expense). */
  firstFlaggedRow2023: 'Food & Beverage Revenue',
};
