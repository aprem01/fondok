/**
 * Historicals year-column labelling — normalized-period source of truth (FON-15).
 *
 * Sam QA 2026-09-01 on the Kimpton end-to-end run: Historical Coverage
 * recognized 2019 and 2025 as uploaded, but the Historicals table only rendered
 * columns for 2021–2024. Root cause — Coverage reads each doc's normalized
 * period (``fiscal_year`` ?? ``extracted_period_year``), while the table
 * re-derived the year from raw extraction fields / filename via
 * ``deriveYearLabel`` and, when a doc lacked clean period metadata, silently
 * dropped it into the T-12 slot. The two views disagreed.
 *
 * ``deriveYearLabel`` now takes the same normalized period as its most
 * authoritative signal, so every year Coverage recognizes gets its own column.
 * This suite locks that contract.
 */
import { describe, it, expect } from 'vitest';
import { deriveYearLabel } from '@/components/project/pl/HistoricalsSection';
import type { ExtractionField } from '@/lib/api';

function mkField(name: string, value: unknown): ExtractionField {
  return { field_name: name, value, unit: null, source_page: 1, confidence: 0.9, raw_text: null };
}

describe('deriveYearLabel — normalized period is authoritative (FON-15)', () => {
  it('labels a column from fiscal_year even when the extraction fields lack period metadata', () => {
    // No period_type / period_ending / period_label and a filename with no
    // year — pre-fix this fell through to the T-12 default and vanished from
    // the annual columns. The normalized fiscal_year now wins.
    expect(deriveYearLabel([], 'Copy of financials.xlsx', 'PNL', 2019)).toBe('2019');
    expect(deriveYearLabel([], 'Copy of financials.xlsx', 'PNL', 2025)).toBe('2025');
  });

  it('accepts a numeric or string normalized year, and extracted_period_year as the fallback', () => {
    expect(deriveYearLabel([], 'x.xlsx', 'P&L', '2019')).toBe('2019');
    // Callers pass ``fiscal_year ?? extracted_period_year`` — either resolves.
    expect(deriveYearLabel([], 'x.xlsx', 'PNL', 2022)).toBe('2022');
  });

  it('keeps a trailing-twelve doc in the T-12 slot regardless of a stray normalized year', () => {
    expect(deriveYearLabel([], 'May 2025 Financials.xlsx', 'T12', 2025)).toBe('T-12');
    expect(deriveYearLabel([], 'ttm.xlsx', 'T-12', 2024)).toBe('T-12');
  });

  it('still resolves from extraction fields / filename when no normalized year is supplied', () => {
    // Backward compatible: the original resolution chain is unchanged when the
    // 4th argument is omitted or null.
    const annual2023 = [
      mkField('period_type', 'annual'),
      mkField('period_ending', '2023-12-31'),
    ];
    expect(deriveYearLabel(annual2023, 'whatever.xlsx', 'PNL')).toBe('2023');
    expect(deriveYearLabel([], "Angler's 2024 P&L.xlsx", 'PNL', null)).toBe('2024');
    // A mid-year period-ending with no normalized year is still a T-12.
    expect(deriveYearLabel([mkField('period_ending', '2025-05-31')], 'x.xlsx', 'PNL')).toBe('T-12');
  });

  it('does not let a stray filename year override an explicit annual normalized period', () => {
    // "Copy of Angler_s 2023 P&L.xlsx" tagged fiscal_year 2025 in the Data Room:
    // the column follows the normalized period (2025), matching Coverage. The
    // correctness of that classification is a separate year-mismatch concern.
    expect(deriveYearLabel([], "Copy of Angler_s 2023 P&L.xlsx", 'PNL', 2025)).toBe('2025');
  });
});
