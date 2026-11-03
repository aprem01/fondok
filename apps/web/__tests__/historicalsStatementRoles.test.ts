/**
 * FON-22 — the Historical P&L reads the worker's ONE statement choice.
 *
 * Deal b67bfdee (prod, read-only probe 2026-11-03): the FY2024 Detailed P&L
 * (1,072 fields, uploaded 10-19) drives the model — occupancy 83.26%, ADR
 * $236.09 — while the FY2024 Summary CSV (23 fields, uploaded 10-20) states
 * no operating statistics. The old Data Room badge ranked on a saturated
 * structural score, tied, and handed the newer CSV the badge; the FY2024
 * column then came from the CSV with occupancy and ADR blank ("—").
 *
 * Contracts:
 *  - With worker roles, a period's column is the worker's chosen statement
 *    (``statement_period_rank`` 0); every other statement of that period is
 *    supplemental. No field-count or upload-order re-ranking.
 *  - Occupancy / ADR / RevPAR the chosen statement lacks are read only from a
 *    statement of the SAME period (``statement_period``), full-period lines
 *    only, with a source label. Never another period, an STR, an OM or a
 *    monthly slice — those stay "—".
 */
import { describe, it, expect } from 'vitest';
import { buildHistoricalYears } from '@/lib/hooks/useHistoricals';
import { histHasData } from '@/lib/reviewState';
import type { ExtractionField, ExtractionResult, StatementRole, WorkerDocument } from '@/lib/api';

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

const f = (field_name: string, value: unknown, confidence: number | null = 0.95): ExtractionField =>
  ({ field_name, value, unit: null, source_page: 1, confidence, raw_text: null, reviewed: null });
const ex = (document_id: string, fields: ExtractionField[]): ExtractionResult =>
  ({ document_id, status: 'EXTRACTED', fields, confidence_report: null, agent_version: null, created_at: null });

const KEYS = 132;
const DETAILED = 'Copy of Angler_s 2024 Full Year Detailed P&L.xlsm';
const CSV = 'Angler_FY2024_Summary_PL.csv';
const FY2024 = 'full:20241231';

const detailedFields = [
  f('ttm_performance.subject.occupancy_pct', 0.832567),
  f('ttm_performance.subject.adr_usd', 236.094),
  f('ttm_performance.subject.revpar_usd', 196.564),
  f('p_and_l_usali.operating_revenue.rooms_revenue', 9_496_407.22),
  f('p_and_l_usali.operating_revenue.food_beverage_revenue', 2_739_040.71),
  f('p_and_l_usali.operating_revenue.total_revenue', 13_481_730.29),
  ...Array.from({ length: 60 }, (_, i) => f(`p_and_l_usali.detail.gl_${i}`, 1_000 + i)),
];
const csvFields = [
  f('p_and_l_usali.period_type', 'annual'),
  f('p_and_l_usali.period_ending', '2024-12-31'),
  f('p_and_l_usali.operating_revenue.rooms_revenue', 8_900_000),
  f('p_and_l_usali.operating_revenue.food_beverage_revenue', 1_300_000),
  f('p_and_l_usali.operating_revenue.total_revenue', 10_520_000),
];

const role = (
  statement_role: StatementRole, rank: number, primary: string, period = FY2024, words = 'FY2024',
): Partial<WorkerDocument> => ({
  statement_role,
  statement_period: period,
  statement_period_words: words,
  statement_period_rank: rank,
  period_primary_document_id: primary,
  primary_financial_source: statement_role === 'year_one_primary',
});

describe('buildHistoricalYears — the worker statement choice (FON-22)', () => {
  it('b67bfdee: FY2024 is the Detailed P&L the model uses; the newer Summary CSV is supplemental', () => {
    const detailed = doc('det', {
      filename: DETAILED, extracted_period_year: 2024, uploaded_at: '2026-10-19T18:46:52Z',
      ...role('year_one_primary', 0, 'det'),
    });
    const csv = doc('csv', {
      filename: CSV, extracted_period_year: 2024, uploaded_at: '2026-10-20T17:52:18Z',
      ...role('supplemental', 1, 'det'),
    });
    const years = buildHistoricalYears(
      [csv, detailed], { det: ex('det', detailedFields), csv: ex('csv', csvFields) }, KEYS,
    ).filter(histHasData);
    const y24 = years.find((y) => y.year === '2024')!;
    expect(years.filter((y) => y.year.startsWith('2024'))).toHaveLength(1);
    expect(y24.docId).toBe('det');
    expect(y24.occupancyPct).toBeCloseTo(0.832567, 6);
    expect(y24.adr).toBeCloseTo(236.094, 3);
    expect(y24.rooms).toBeCloseTo(9_496_407.22, 2);
    expect(y24.supplementalDocs?.map((d) => d.docId)).toEqual(['csv']);
    expect(y24.filled ?? {}).toEqual({});
  });

  it('the period rank decides, never the field count or upload order', () => {
    // The worker chose the SMALLER, OLDER statement (e.g. the footing switch).
    const small = doc('small', { fiscal_year: 2024, uploaded_at: '2026-01-01T00:00:00Z', ...role('year_one_primary', 0, 'small') });
    const big = doc('big', { fiscal_year: 2024, uploaded_at: '2026-01-05T00:00:00Z', ...role('supplemental', 1, 'small') });
    const years = buildHistoricalYears(
      [small, big],
      { small: ex('small', csvFields), big: ex('big', detailedFields) },
      KEYS,
    ).filter(histHasData);
    expect(years.map((y) => y.year)).toEqual(['2024']);
    expect(years[0].docId).toBe('small');
  });

  it('groups by the worker period, so a non-basis period keeps its own period_primary', () => {
    const a = doc('a23', { fiscal_year: 2023, ...role('period_primary', 0, 'a23', 'full:20231231', 'FY2023') });
    const b = doc('b23', { fiscal_year: 2023, uploaded_at: '2026-02-01T00:00:00Z', ...role('supplemental', 1, 'a23', 'full:20231231', 'FY2023') });
    const years = buildHistoricalYears(
      [a, b],
      { a23: ex('a23', [f('occupancy', 0.7), f('rooms_revenue', 9_000_000)]), b23: ex('b23', detailedFields) },
      KEYS,
    ).filter(histHasData);
    expect(years.map((y) => y.year)).toEqual(['2023']);
    expect(years[0].docId).toBe('a23');
    expect(years[0].supplementalDocs?.map((d) => d.docId)).toEqual(['b23']);
  });
});

describe('occupancy / ADR the chosen statement lacks — same period only (FON-22)', () => {
  it('fills from a same-period sibling, labelled with its source', () => {
    const csv = doc('csv', { filename: CSV, extracted_period_year: 2024, ...role('year_one_primary', 0, 'csv') });
    const detailed = doc('det', { filename: DETAILED, extracted_period_year: 2024, ...role('supplemental', 1, 'csv') });
    const y24 = buildHistoricalYears(
      [csv, detailed], { csv: ex('csv', csvFields), det: ex('det', detailedFields) }, KEYS,
    ).find((y) => y.year === '2024')!;
    expect(y24.docId).toBe('csv');
    expect(y24.rooms).toBe(8_900_000); // the chosen statement's own lines stay
    expect(y24.occupancyPct).toBeCloseTo(0.832567, 6);
    expect(y24.adr).toBeCloseTo(236.094, 3);
    expect(y24.revpar).toBeCloseTo(196.564, 3);
    for (const key of ['occ', 'adr', 'revpar']) {
      expect(y24.filled?.[key]).toMatchObject({ docId: 'det', filledFrom: 'same_period' });
      expect(y24.meta?.[key]?.docId).toBe('det');
    }
    expect(y24.filled?.occ?.note).toBe(
      `Occupancy is not on ${CSV}; read from ${DETAILED} (the same period, FY2024).`,
    );
  });

  it('RevPAR is derived only when occupancy and ADR come from the same statement', () => {
    const primary = doc('p', { fiscal_year: 2024, ...role('year_one_primary', 0, 'p') });
    const sib = doc('s', { fiscal_year: 2024, ...role('supplemental', 1, 'p') });
    const years = buildHistoricalYears(
      [primary, sib],
      {
        p: ex('p', [f('adr_usd', 240), f('rooms_revenue', 9_000_000)]),
        s: ex('s', [f('occupancy_pct', 0.8), f('rooms_revenue', 8_000_000)]),
      },
      KEYS,
    );
    const y = years.find((c) => c.year === '2024')!;
    expect(y.adr).toBe(240);
    expect(y.occupancyPct).toBeCloseTo(0.8, 6);
    expect(y.filled?.occ?.docId).toBe('s');
    expect(y.revpar).toBeNull();
  });

  it('never fills from a monthly slice, another period, an STR or an OM — the cell stays "—"', () => {
    const csv = doc('csv', { filename: CSV, extracted_period_year: 2024, ...role('year_one_primary', 0, 'csv') });
    // Same period, but its only occupancy is a monthly slice.
    const sliced = doc('sl', { fiscal_year: 2024, ...role('supplemental', 1, 'csv') });
    // A different period (the March 2025 TTM) that does state occupancy.
    const ttm = doc('ttm', {
      doc_type: 'PNL_MONTHLY', fiscal_year: 2025,
      ...role('period_primary', 0, 'ttm', 'partial:9:monthly:20250331', 'the monthly period ending 2025-03-31'),
    });
    const str = doc('str', { doc_type: 'STR', fiscal_year: 2024 });
    const om = doc('om', { doc_type: 'OM' });
    const years = buildHistoricalYears(
      [csv, sliced, ttm, str, om],
      {
        csv: ex('csv', csvFields),
        sl: ex('sl', [f('p_and_l_usali.monthly.apr_2024.occupancy_pct', 0.9), f('p_and_l_usali.monthly.apr_2024.adr_usd', 300), f('rooms_revenue', 8_800_000)]),
        ttm: ex('ttm', [f('occupancy_pct', 0.832109), f('adr_usd', 232.773), f('rooms_revenue', 9_300_000)]),
        str: ex('str', [f('subject.occupancy_pct', 0.8239), f('subject.adr_usd', 247.08)]),
        om: ex('om', [f('ttm_summary_per_om.occupancy_pct', 0.831), f('ttm_summary_per_om.adr_usd', 240.5)]),
      },
      KEYS,
    );
    const y24 = years.find((y) => y.year === '2024')!;
    expect(y24.docId).toBe('csv');
    expect(y24.occupancyPct).toBeNull();
    expect(y24.adr).toBeNull();
    expect(y24.revpar).toBeNull();
    expect(y24.filled ?? {}).toEqual({});
  });

  it('without worker roles (older payload) the column keeps today’s behaviour and never fills', () => {
    const csv = doc('csv', { filename: CSV, extracted_period_year: 2024, primary_financial_source: true, uploaded_at: '2026-10-20T00:00:00Z' });
    const detailed = doc('det', { filename: DETAILED, extracted_period_year: 2024, uploaded_at: '2026-10-19T00:00:00Z' });
    const y24 = buildHistoricalYears(
      [csv, detailed], { csv: ex('csv', csvFields), det: ex('det', detailedFields) }, KEYS,
    ).find((y) => y.year === '2024')!;
    expect(y24.docId).toBe('csv');
    expect(y24.occupancyPct).toBeNull();
    expect(y24.filled ?? {}).toEqual({});
  });
});

describe('the chosen statement is re-extracting or its re-extract failed (FON-22 R2-I1)', () => {
  const pair = (detailedStatus: string) => [
    doc('det', {
      filename: DETAILED, extracted_period_year: 2024, status: detailedStatus,
      ...role('year_one_primary', 0, 'det'),
    }),
    doc('csv', { filename: CSV, extracted_period_year: 2024, ...role('supplemental', 1, 'det') }),
  ];
  const extractions = { det: ex('det', detailedFields), csv: ex('csv', csvFields) };

  it.each([
    ['EXTRACTING', 'reextracting', `${DETAILED} is re-extracting; showing its last extraction, which the model still reads.`],
    ['FAILED', 'reextract_failed', `${DETAILED}: re-extract failed — showing the last extraction, which the model still reads.`],
  ])('%s: the column keeps the chosen statement’s last extraction, labelled', (status, state, note) => {
    const y24 = buildHistoricalYears(pair(status), extractions, KEYS).find((y) => y.year === '2024')!;
    expect(y24.docId).toBe('det');
    expect(y24.occupancyPct).toBeCloseTo(0.832567, 6);
    expect(y24.adr).toBeCloseTo(236.094, 3);
    expect(y24.supplementalDocs?.map((d) => d.docId)).toEqual(['csv']);
    expect(y24.statementStatus).toEqual({ state, note });
  });

  it('with no extraction loaded for it, the supplemental is NOT promoted — the period shows “—”', () => {
    const years = buildHistoricalYears(pair('EXTRACTING'), { csv: ex('csv', csvFields) }, KEYS);
    const y24 = years.find((y) => y.year === '2024')!;
    expect(y24.populated).toBe(false);
    expect(y24.docId).toBeUndefined();
    expect(y24.statementStatus?.state).toBe('reextracting');
    expect(years.filter((y) => y.year.startsWith('2024'))).toHaveLength(1);
  });

  it('an EXTRACTED chosen statement carries no status label', () => {
    const y24 = buildHistoricalYears(pair('EXTRACTED'), extractions, KEYS).find((y) => y.year === '2024')!;
    expect(y24.statementStatus).toBeUndefined();
  });

  it('M6: a chosen PNL_MONTHLY statement sharing its period with a P&L takes the column', () => {
    const t12 = doc('t12', { doc_type: 'PNL_MONTHLY', fiscal_year: 2024, ...role('year_one_primary', 0, 't12') });
    const pnl = doc('pnl', { fiscal_year: 2024, ...role('supplemental', 1, 't12') });
    const y = buildHistoricalYears(
      [t12, pnl],
      { t12: ex('t12', [f('occupancy_pct', 0.81), f('rooms_revenue', 9_100_000)]), pnl: ex('pnl', csvFields) },
      KEYS,
    ).filter(histHasData);
    expect(y).toHaveLength(1);
    expect(y[0].docId).toBe('t12');
    expect(y[0].supplementalDocs?.map((d) => d.docId)).toEqual(['pnl']);
  });
});
