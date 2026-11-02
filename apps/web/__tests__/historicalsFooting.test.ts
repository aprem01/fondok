/**
 * Wave 5 (Sam #3) — the Historical P&L foots, on the REAL Kimpton Angler
 * statements (apps/worker/tests/fixtures/real_payloads — the extractor's own
 * output for the FY2023 annual P&L and the T-12).
 *
 *  a) "Other Revenue" is Other Operated Departments PLUS Miscellaneous Income.
 *     FY2023: 15,885.6 + 1,004,790 = 1,020,675.6 (the column used to show one
 *     line and drop the other — short by exactly the Misc. Income).
 *  b) The fixed block carries FF&E reserve, rent and other non-operating
 *     expense; non-operating income is its own line, so NOI = GOP − fees &
 *     fixed + non-operating income reconciles.
 *  c) Non-operating income is signed by the statement's OWN convention. The
 *     FY2023 statement prints its Income line in the expense-signed
 *     non-operating section (Income −9,638.44 + Rent + Taxes + Insurance =
 *     stated total 1,856,960 → income is +9,638.44); the T-12 prints it
 *     income-positive (Rent + Taxes + Insurance − 21,923.6 = 1,922,240). Read
 *     as printed, FY2023 showed a false $19,287 "source gap".
 *  d) The footing tolerance is the precision of the summed values — the
 *     extractor reads "1.29402e+07" (6 significant figures, ±50) — so
 *     rounding never reads as a gap, while a real missing line still does.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import type { ExtractionField } from '@/lib/api';
import { buildHistYear, type HistYear } from '@/components/project/pl/HistoricalsSection';
import { histValue } from '@/lib/reviewState';
import { footGop, footNoi, footRevenue, footingGapText } from '@/lib/worksheetFooting';
import { halfUnitOf } from '@/lib/statementLines';

const FIXTURES = resolve(__dirname, '../../worker/tests/fixtures/real_payloads');
const load = (name: string): ExtractionField[] =>
  (JSON.parse(readFileSync(resolve(FIXTURES, name), 'utf8')) as { fields: ExtractionField[] }).fields;

const FY2023 = load('anglers_annual_pnl_real.json');
const T12 = load('anglers_t12_real.json');

function col(fields: ExtractionField[], label = '2023'): HistYear {
  const h = buildHistYear(fields, 97, label, `doc-${label}`);
  expect(h).not.toBeNull();
  return h!;
}

describe('real Kimpton FY2023 annual P&L', () => {
  const h = col(FY2023);

  it('Other Revenue = Other Operated + Misc. Income, and revenue foots', () => {
    expect(h.other_operated_revenue).toBe(15_885.6);
    expect(h.misc_income).toBe(1_004_790);
    expect(histValue('other_rev', h)).toBeCloseTo(1_020_675.6, 1);
    const r = footRevenue(h);
    expect(r.ok && r.footing.foots).toBe(true);
  });

  it('GOP foots within the precision of the extracted figures', () => {
    const r = footGop(h);
    expect(r.ok && r.footing.foots).toBe(true);
  });

  it('reads FF&E, rent and other non-op into Total Fees & Fixed', () => {
    expect(histValue('ffe', h)).toBe(517_608);
    expect(histValue('rent', h)).toBe(17_917.7);
    expect(histValue('other_nonop', h)).toBe(0);
    expect(histValue('fixed_total', h)).toBeCloseTo(2_788_808.7, 1);
  });

  it('non-operating income printed −9,638.44 in the expense-signed section is +9,638.44 of income', () => {
    expect(h.nonop_income_basis).toBe('expense_signed');
    expect(histValue('nonop_income', h)).toBe(9_638.44);
  });

  it('NOI foots — no false "source gap"', () => {
    const r = footNoi(h);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.footing.foots).toBe(true);
      expect(footingGapText(r.footing)).toBe('✓ foots');
    }
  });

  it('a line genuinely missing from the column still shows as a source gap, with its amount', () => {
    const r = footNoi(col(FY2023.filter((x) => !x.field_name.includes('ffe_reserve'))));
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.footing.foots).toBe(false);
      // The gap is the FF&E line, to within the precision of the figures.
      expect(Math.abs(r.footing.difference - 517_608)).toBeLessThanOrEqual(r.footing.tolerance);
      expect(footingGapText(r.footing)).toMatch(/^doesn’t foot by \$517,\d{3} — source gap$/);
    }
  });
});

describe('real Kimpton T-12', () => {
  const h = col(T12, 'T-12');

  it('Other Revenue = 21,656.6 + 1,270,770', () => {
    expect(histValue('other_rev', h)).toBeCloseTo(1_292_426.6, 1);
  });

  it('non-operating income printed +21,923.6 in an income-positive section stays +21,923.6', () => {
    expect(h.nonop_income_basis).toBe('income_positive');
    expect(histValue('nonop_income', h)).toBe(21_923.6);
  });

  it('revenue, GOP and NOI all foot — rounding of 6-significant-figure reads is not a gap', () => {
    for (const foot of [footRevenue, footGop, footNoi]) {
      const r = foot(h);
      expect(r.ok, foot.name).toBe(true);
      if (r.ok) expect(r.footing.foots, `${foot.name} Δ ${r.footing.difference}`).toBe(true);
    }
  });
});

describe('non-operating income whose sign the statement does not settle', () => {
  it('with no stated non-operating total the sign is unresolved — a dash, and the footing says why', () => {
    const fields = FY2023.filter((x) => !/non_operating\.total|total_non_operating/.test(x.field_name));
    const h = col(fields);
    expect(h.nonop_income_basis).toBe('unresolved');
    expect(histValue('nonop_income', h)).toBeNull();
    expect(footNoi(h)).toEqual({ ok: false, reason: 'nonop_sign_unresolved' });
  });
});

describe('halfUnitOf — the precision the extractor read the figure at', () => {
  const f = (value: number, raw_text: string | null) => ({ field_name: 'x', value, raw_text } as unknown as ExtractionField);
  it('reads exponent notation: 1.29402e+07 is ±50', () => {
    expect(halfUnitOf(f(12_940_200, 'Total Revenues ... 1.29402e+07'))).toBe(50);
  });
  it('picks the token that IS the value (the period total, not a month)', () => {
    expect(halfUnitOf(f(21_923.6, 'Income\t2077.49\t1734.91\t21923.6'))).toBeCloseTo(0.05);
  });
  it('without a matching raw token, the value’s own digits', () => {
    expect(halfUnitOf(f(11_000_005, null))).toBe(0.5);
    expect(halfUnitOf(f(15_885.6, null))).toBeCloseTo(0.05);
  });
});

describe('Other Revenue edge cases', () => {
  const f = (field_name: string, value: number) => ({ field_name, value, confidence: 0.95, raw_text: null } as unknown as ExtractionField);
  it('counts a field once', () => {
    const h = col([f('p_and_l_usali.operating_revenue.rooms_revenue', 9_000_000), f('other_revenue', 400_000)]);
    expect(histValue('other_rev', h)).toBe(400_000);
  });
  it('a statement with neither line shows a dash, never $0', () => {
    const h = col([f('p_and_l_usali.operating_revenue.rooms_revenue', 9_000_000)]);
    expect(histValue('other_rev', h)).toBeNull();
  });
});
