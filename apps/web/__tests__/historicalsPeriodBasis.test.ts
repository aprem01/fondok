/**
 * Historicals period BASIS — FY / YTD / T12, and the refusal (FON-41 #4).
 *
 * Sam, 2026-09-11: "the current generic 2025 column is too ambiguous when its
 * provenance points to March 2025 Financials.xlsx … Provenance should identify
 * the source period/basis in addition to the source document (e.g. `T12 ending
 * Mar 31, 2025`)."
 *
 * `derivePeriodBasis` is the web mirror of the worker's `registry._doc_scope`
 * (`period_type` through the generated `PERIOD_TYPES` rank map, then the
 * doc-type default T12→ttm / PNL→annual / PNL_MONTHLY→monthly / PNL_YTD→ytd).
 * `apps/worker/tests/test_doc_scope_pnl_family.py` pins the worker half so the
 * two cannot drift.
 *
 * The regression this suite exists for: `deriveYearLabel` used to map
 * `period_type` ∈ {ytd, quarter, month} to "T-12", asserting a trailing twelve
 * that does not exist (FON-29). A YTD statement is now a YTD statement.
 */
import { describe, it, expect } from 'vitest';
import { derivePeriodBasis, deriveYearLabel } from '@/components/project/pl/HistoricalsSection';
import type { ExtractionField } from '@/lib/api';

function mkField(name: string, value: unknown): ExtractionField {
  return { field_name: name, value, unit: null, source_page: 1, confidence: 0.9, raw_text: null };
}

describe('derivePeriodBasis — the three MVP bases', () => {
  it('reads an annual P&L closing December as a full year', () => {
    const r = derivePeriodBasis(
      [mkField('p_and_l_usali.period_ending', '2024-12-31')],
      'Angler_s 2024 Full Year Detailed P&L.xlsm',
      'PNL',
      2024,
    );
    expect(r.periodBasis).toBe('FY');
    expect(r.periodLabel).toBe('FY2024');
    expect(r.periodEnd).toBe('2024-12-31');
    expect(r.basisReason).toBeUndefined();
  });

  it('reads a PNL_YTD through a month end as year-to-date', () => {
    const r = derivePeriodBasis(
      [mkField('p_and_l_usali.period_ending', '2025-03-31')],
      'Angler_s YTD.xlsx',
      'PNL_YTD',
      2025,
    );
    expect(r.periodBasis).toBe('YTD');
    expect(r.periodLabel).toBe('YTD Mar 2025');
    expect(r.periodEnd).toBe('2025-03-31');
  });

  it('reads a T12 through a month end as a trailing twelve', () => {
    const r = derivePeriodBasis(
      [mkField('p_and_l_usali.period_ending', '2025-03-31')],
      'Copy of The Angler_s - March 2025 Financials.xlsx',
      'T12',
      2025,
    );
    expect(r.periodBasis).toBe('T12');
    expect(r.periodLabel).toBe('T12 Mar 2025');
    expect(r.periodEnd).toBe('2025-03-31');
  });

  it('takes the period end from the statement’s own filename when the extraction has none', () => {
    // Sam's live document: doc_type T12, fiscal_year 2025, no period metadata
    // at all — the column read a generic "2025" while its provenance pointed
    // at "March 2025 Financials.xlsx".
    const r = derivePeriodBasis([], 'Copy of The Angler_s - March 2025 Financials.xlsx', 'T12', 2025);
    expect(r.periodBasis).toBe('T12');
    expect(r.periodLabel).toBe('T12 Mar 2025');
    expect(r.periodEnd).toBe('2025-03-31');
  });
});

describe('derivePeriodBasis — a YTD statement is never a trailing twelve (FON-29)', () => {
  it('resolves period_type "ytd" to YTD, not T12', () => {
    const fields = [
      mkField('p_and_l_usali.period_type', 'ytd'),
      mkField('p_and_l_usali.period_ending', '2025-03-31'),
    ];
    const r = derivePeriodBasis(fields, 'financials.xlsx', 'PNL', null);
    expect(r.periodBasis).toBe('YTD');
    expect(r.periodLabel).toBe('YTD Mar 2025');
    expect(r.periodLabel).not.toContain('T12');
    // …and the column key follows the calendar year it falls in, so Historical
    // Coverage still counts it as 2025 rather than the T-12 slot.
    expect(deriveYearLabel(fields, 'financials.xlsx', 'PNL', null)).toBe('2025');
  });

  it('resolves period_type "monthly" to a single month, not T12', () => {
    const r = derivePeriodBasis(
      [mkField('p_and_l_usali.period_type', 'monthly'), mkField('p_and_l_usali.period_ending', '2025-02-28')],
      'feb.xlsx',
      'PNL_MONTHLY',
      null,
    );
    expect(r.periodBasis).toBe('MONTHLY');
    expect(r.periodLabel).toBe('Feb 2025');
  });

  it('refuses a quarter rather than rounding it into a year or a T-12', () => {
    const r = derivePeriodBasis(
      [mkField('p_and_l_usali.period_type', 'quarterly'), mkField('p_and_l_usali.period_ending', '2025-03-31')],
      'q1.xlsx',
      'PNL',
      null,
    );
    expect(r.periodBasis).toBe('UNKNOWN');
    expect(r.basisReason).toBe('period_mismatch');
    expect(r.periodLabel).toBe('2025');
  });
});

describe('derivePeriodBasis — never a guessed FY', () => {
  it('refuses when a statement carries no period metadata and no FY filename', () => {
    const r = derivePeriodBasis([], 'Copy of financials.xlsx', 'PNL', null);
    expect(r.periodBasis).toBe('UNKNOWN');
    expect(r.basisReason).toBe('period_mismatch');
    expect(r.periodLabel).not.toContain('FY');
    expect(r.periodLabel).not.toContain('T12');
    expect(r.periodEnd).toBeNull();
  });

  it('refuses an annual classification whose period plainly does not close the year', () => {
    // The live "2025" column: classified PNL, tagged fiscal_year 2025, and a
    // filename that says March. Nothing establishes it as a full year — so it
    // renders the bare year plus the reason, not "FY2025".
    const r = derivePeriodBasis([], 'The Angler_s - March 2025 Financials.xlsx', 'PNL', 2025);
    expect(r.periodBasis).toBe('UNKNOWN');
    expect(r.basisReason).toBe('period_mismatch');
    expect(r.periodLabel).toBe('2025');
  });

  it('keeps a real March fiscal year when the statement itself says "annual"', () => {
    const r = derivePeriodBasis(
      [mkField('p_and_l_usali.period_type', 'annual'), mkField('p_and_l_usali.period_ending', '2025-03-31')],
      'fy.xlsx',
      'PNL',
      null,
    );
    expect(r.periodBasis).toBe('FY');
    expect(r.periodLabel).toBe('FY2025');
  });

  it('drops a period end that belongs to another year rather than reporting it', () => {
    // Live extraction artefact on Sam's 2019 P&L: a `statement_period` from
    // 2024 on a 2019 statement.
    const r = derivePeriodBasis(
      [mkField('property_overview.statement_period', '2024-12-31')],
      'Copy of Angler_s 2019 P&L.xlsx',
      'PNL',
      2019,
    );
    expect(r.periodBasis).toBe('FY');
    expect(r.periodLabel).toBe('FY2019');
    expect(r.periodEnd).toBeNull();
  });

  it('does not read "Mayfair 2025" as May 2025', () => {
    const r = derivePeriodBasis([], 'Mayfair 2025 P&L.xlsx', 'PNL', 2025);
    expect(r.periodEnd).toBeNull();
    expect(r.periodBasis).toBe('FY');
    expect(r.periodLabel).toBe('FY2025');
  });
});
