/**
 * worksheetFooting — E-012 (FON-41): does a historical column foot, and what
 * did a correct-at-source edit change in it?
 *
 * Built on the facts in lib/worksheetFooting: Total Revenue in a historical
 * column is the statement's OWN stated total when it publishes one (FON-90)
 * and the client-side SUM (rooms + F&B + misc) only when it does not; the
 * footing always compares the SUM against the stated total; GOP / NOI are
 * the statement's own stated lines; the worker never re-foots a statement.
 */
import { describe, it, expect } from 'vitest';
import type { HistYear } from '@/components/project/pl/HistoricalsSection';
import { histDerivedTotalRevenue, histStatedTotalRevenue, histValue } from '@/lib/reviewState';
import {
  diffHistColumn, footGop, footRevenue, footingSentence, fmtSignedCurrency,
} from '@/lib/worksheetFooting';

function hy(over: Partial<HistYear> = {}): HistYear {
  return {
    year: '2023', periodBasis: 'FY', periodEnd: null, periodLabel: 'FY2023', days: 365,
    occupancyPct: 0.75, adr: 300, revpar: 225,
    rooms: 11_000_000, fb: 2_000_000, misc: 0,
    rooms_dept_expense: 2_400_000, fb_dept_expense: 1_500_000, other_dept_expense: 100_000,
    undistributed: 3_000_000, gop: 6_000_000, fixed_expenses: 2_000_000, noi: 4_000_000,
    total_revenue_stated: 13_000_000,
    populated: true,
    ...over,
  };
}

describe('footRevenue — lines vs the statement’s own stated total', () => {
  it('foots when rooms + F&B + misc equal the stated total', () => {
    const r = footRevenue(hy());
    expect(r.ok && r.footing.foots).toBe(true);
    if (r.ok) expect(footingSentence(r.footing)).toBe('Lines sum to $13,000,000 · stated total $13,000,000 · difference $0');
  });

  it('a $5 line edit the worker does not re-foot shows as a $5 difference', () => {
    const r = footRevenue(hy({ rooms: 11_000_005 }));
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.footing.foots).toBe(false);
      expect(r.footing.difference).toBe(5);
      expect(footingSentence(r.footing)).toBe('Lines sum to $13,000,005 · stated total $13,000,000 · difference +$5');
    }
  });

  it('says why when the statement states no total — never foots against nothing', () => {
    const r = footRevenue(hy({ total_revenue_stated: null }));
    expect(r).toEqual({ ok: false, reason: 'no_stated_total' });
  });
});

describe('footGop — Total Revenue − departmental − undistributed vs the stated GOP', () => {
  it('foots on a consistent statement', () => {
    const r = footGop(hy());
    expect(r.ok && r.footing.foots).toBe(true);
  });

  it('a revenue edit moves the calculated GOP but not the stated one', () => {
    const r = footGop(hy({ rooms: 11_000_005 }));
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.footing.lines).toBe(6_000_005);
      expect(r.footing.stated).toBe(6_000_000);
      expect(footingSentence(r.footing)).toBe('Lines sum to $6,000,005 · stated GOP $6,000,000 · difference +$5');
    }
  });

  it('cannot foot when a departmental or undistributed line was not extracted', () => {
    expect(footGop(hy({ undistributed: null }))).toEqual({ ok: false, reason: 'lines_missing' });
    expect(footGop(hy({ other_dept_expense: null }))).toEqual({ ok: false, reason: 'lines_missing' });
  });

  it('cannot foot when the statement never published GOP', () => {
    expect(footGop(hy({ gop: null }))).toEqual({ ok: false, reason: 'no_stated_total' });
  });
});

describe('histValue(total_rev) — the stated total first, the sum of the lines only without one (FON-90)', () => {
  it('shows the statement’s stated total when the lines do not reproduce it (the tester deals: $13.48M stated, $12.3M of lines)', () => {
    const h = hy({ rooms: 10_000_000, fb: 1_800_000, misc: 500_000, total_revenue_stated: 13_480_000 });
    expect(histValue('total_rev', h)).toBe(13_480_000);
    expect(histStatedTotalRevenue(h)).toBe(13_480_000);
    expect(histDerivedTotalRevenue(h)).toBe(12_300_000);
    // The footing keeps comparing the SUM against the stated total — never the cell against itself.
    expect(footRevenue(h)).toMatchObject({ ok: true, footing: { lines: 12_300_000, stated: 13_480_000, difference: -1_180_000, foots: false } });
  });

  it('falls back to the sum of the lines when the statement states no total', () => {
    const h = hy({ rooms: 10_000_000, fb: 1_800_000, misc: 500_000, total_revenue_stated: null });
    expect(histValue('total_rev', h)).toBe(12_300_000);
    expect(histStatedTotalRevenue(h)).toBeNull();
    expect(footRevenue(h)).toEqual({ ok: false, reason: 'no_stated_total' });
  });

  it('a stated total of 0 is a stated total, not "none"', () => {
    expect(histValue('total_rev', hy({ total_revenue_stated: 0 }))).toBe(0);
  });
});

describe('diffHistColumn — what a correct-at-source edit changed', () => {
  const ROW_IDS = ['rooms_rev', 'fb_rev', 'other_rev', 'total_rev', 'rooms_dept', 'gop', 'mgmt', 'taxes', 'insurance', 'fixed_total', 'noi'];
  // The same client-side read the worksheet makes (lib/reviewState histValue).
  const snapshot = (h: HistYear) => Object.fromEntries(ROW_IDS.map((id) => [id, histValue(id, h)]));

  it('+$5 on Rooms Revenue moves Rooms Revenue only when the statement states its total — the stated Total Revenue does not re-sum (FON-90)', () => {
    const before = snapshot(hy());
    const changes = diffHistColumn(before, hy({ rooms: 11_000_005 }), ROW_IDS);
    expect(changes.map((c) => [c.rowId, c.delta])).toEqual([['rooms_rev', 5]]);
    // The stated total is what the cell shows, before and after.
    expect(histValue('total_rev', hy({ rooms: 11_000_005 }))).toBe(13_000_000);
    // …and the footing carries the $5 the stated total did not absorb.
    expect(footRevenue(hy({ rooms: 11_000_005 }))).toMatchObject({ ok: true, footing: { lines: 13_000_005, stated: 13_000_000, difference: 5, foots: false } });
  });

  it('+$5 on Rooms Revenue moves the derived Total Revenue by $5 when the statement states no total', () => {
    const noStated = (over: Partial<HistYear> = {}) => hy({ total_revenue_stated: null, ...over });
    const before = snapshot(noStated());
    const changes = diffHistColumn(before, noStated({ rooms: 11_000_005 }), ROW_IDS);
    expect(changes.map((c) => [c.rowId, c.delta])).toEqual([
      ['rooms_rev', 5],
      ['total_rev', 5],
    ]);
    expect(changes.find((c) => c.rowId === 'total_rev')).toMatchObject({ before: 13_000_000, after: 13_000_005 });
  });

  it('an expense-line edit moves only that line — GOP / NOI are stated and do not re-sum', () => {
    const before = snapshot(hy());
    const changes = diffHistColumn(before, hy({ rooms_dept_expense: 2_400_005 }), ROW_IDS);
    expect(changes.map((c) => c.rowId)).toEqual(['rooms_dept']);
  });

  it('an identical value is not a change', () => {
    expect(diffHistColumn(snapshot(hy()), hy(), ROW_IDS)).toEqual([]);
  });
});

describe('fmtSignedCurrency', () => {
  it('signs every non-zero delta and never shows a sign on zero', () => {
    expect(fmtSignedCurrency(5)).toBe('+$5');
    expect(fmtSignedCurrency(-250_000)).toBe('−$250,000');
    expect(fmtSignedCurrency(0)).toBe('$0');
  });
});
