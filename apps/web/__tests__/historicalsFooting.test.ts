/**
 * Wave 5 (Sam #3) — the Historical P&L foots.
 *
 *  a) "Other Revenue" is Other Operated Departments PLUS Miscellaneous Income.
 *     ``findField`` returns the first match, so the column used to show one of
 *     the two and drop the other: Kimpton FY2023 rooms 9,807,990 + F&B
 *     2,111,540 + OOD 15,886 = 11.94M against a stated 12.94M — the gap was
 *     exactly Misc. Income 1,004,790.
 *  b) The fixed block carries FF&E reserve, rent and other non-operating
 *     expense, and non-operating income has its own line, so
 *     NOI = GOP − fees & fixed + non-operating income reconciles where the
 *     statement states them. A column that still does not foot says so:
 *     "doesn't foot by $X — source gap".
 */
import { describe, it, expect } from 'vitest';
import type { ExtractionField } from '@/lib/api';
import { buildHistYear, type HistYear } from '@/components/project/pl/HistoricalsSection';
import { histValue } from '@/lib/reviewState';
import { footNoi, footRevenue, footingGapText } from '@/lib/worksheetFooting';

const f = (field_name: string, value: number, confidence = 0.95): ExtractionField =>
  ({ field_name, value, confidence } as unknown as ExtractionField);

// FY2023 Kimpton Angler P&L (prod copy b67bfdee), the lines that matter.
const FY2023: ExtractionField[] = [
  f('p_and_l_usali.operating_revenue.rooms_revenue', 9_807_990),
  f('p_and_l_usali.operating_revenue.fb_revenue', 2_111_540),
  f('p_and_l_usali.operating_revenue.other_revenue', 15_886, 0.9),
  f('p_and_l_usali.operating_revenue.misc_revenue', 1_004_790, 0.7),
  f('p_and_l_usali.total_revenue_usd', 12_940_206),
  f('p_and_l_usali.gross_operating_profit_usd', 4_000_000),
  f('p_and_l_usali.fees_and_reserves.mgmt_fee', 388_206),
  f('p_and_l_usali.fees_and_reserves.ffe_reserve', 517_608),
  f('p_and_l_usali.non_operating.property_taxes', 600_000),
  f('p_and_l_usali.non_operating.insurance_usd', 250_000),
  f('p_and_l_usali.non_operating.rent_usd', 17_918),
  f('p_and_l_usali.non_operating.income_usd', 9_638),
  // 4,000,000 − 388,206 − 517,608 − 600,000 − 250,000 − 17,918 + 9,638
  f('p_and_l_usali.ebitda_less_replacement_reserve_usd', 2_235_906),
];

function col(fields: ExtractionField[]): HistYear {
  const h = buildHistYear(fields, 120, '2023', 'doc-2023');
  expect(h).not.toBeNull();
  return h!;
}

describe('Other Revenue = Other Operated Departments + Miscellaneous Income', () => {
  it('sums both lines, so the revenue lines foot to the stated total', () => {
    const h = col(FY2023);
    expect(h.other_operated_revenue).toBe(15_886);
    expect(h.misc_income).toBe(1_004_790);
    expect(histValue('other_rev', h)).toBe(1_020_676);
    const r = footRevenue(h);
    expect(r.ok && r.footing.foots).toBe(true);
  });

  it('counts a field once when both concepts resolve to it', () => {
    const h = col([
      f('p_and_l_usali.operating_revenue.rooms_revenue', 9_000_000),
      f('other_revenue', 400_000),
    ]);
    expect(histValue('other_rev', h)).toBe(400_000);
  });

  it('a statement with neither line shows a dash, never $0', () => {
    const h = col([f('p_and_l_usali.operating_revenue.rooms_revenue', 9_000_000)]);
    expect(histValue('other_rev', h)).toBeNull();
  });

  it('the cell’s source meta names both lines and carries the lower confidence', () => {
    const h = col(FY2023);
    expect(h.meta?.misc?.confidence).toBe(0.7);
    expect(h.meta?.misc?.parts?.map((p) => p.field)).toEqual([
      'p_and_l_usali.operating_revenue.other_revenue',
      'p_and_l_usali.operating_revenue.misc_revenue',
    ]);
  });
});

describe('fixed block + non-operating income', () => {
  it('reads FF&E reserve, rent and non-operating income off the statement', () => {
    const h = col(FY2023);
    expect(histValue('ffe', h)).toBe(517_608);
    expect(histValue('rent', h)).toBe(17_918);
    expect(histValue('nonop_income', h)).toBe(9_638);
    expect(histValue('other_nonop', h)).toBeNull();
    // Total Fees & Fixed = mgmt + FF&E + taxes + insurance + rent (+ other non-op).
    expect(histValue('fixed_total', h)).toBe(388_206 + 517_608 + 600_000 + 250_000 + 17_918);
  });

  it('NOI = GOP − fees & fixed + non-operating income foots', () => {
    const r = footNoi(col(FY2023));
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.footing.foots).toBe(true);
      expect(footingGapText(r.footing)).toBe('✓ foots');
    }
  });

  it('a statement whose NOI still does not foot says so, with the amount', () => {
    // Drop the FF&E line: the stated NOI is after a reserve the column no
    // longer carries, so the lines overstate NOI by exactly that reserve.
    const r = footNoi(col(FY2023.filter((x) => !x.field_name.includes('ffe_reserve'))));
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.footing.foots).toBe(false);
      expect(r.footing.difference).toBeCloseTo(517_608, 0);
      expect(footingGapText(r.footing)).toBe('doesn’t foot by $517,608 — source gap');
    }
  });

  it('no stated NOI or GOP — nothing to foot against', () => {
    const r = footNoi(col(FY2023.filter((x) => !x.field_name.includes('ebitda_less'))));
    expect(r).toEqual({ ok: false, reason: 'no_stated_total' });
  });
});
