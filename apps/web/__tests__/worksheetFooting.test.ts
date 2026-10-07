/**
 * worksheetFooting — E-012 (FON-41): does a historical column foot, and what
 * did a correct-at-source edit change in it?
 *
 * Built on the facts in lib/worksheetFooting: Total Revenue in a historical
 * column is a client-side SUM (rooms + F&B + misc); GOP / NOI are the
 * statement's own stated lines; the worker never re-foots a statement.
 */
import { describe, it, expect } from 'vitest';
import type { HistYear } from '@/components/project/pl/HistoricalsSection';
import { histValue } from '@/lib/reviewState';
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

describe('diffHistColumn — what a correct-at-source edit changed', () => {
  const ROW_IDS = ['rooms_rev', 'fb_rev', 'other_rev', 'total_rev', 'rooms_dept', 'gop', 'mgmt', 'taxes', 'insurance', 'fixed_total', 'noi'];
  // The same client-side read the worksheet makes (lib/reviewState histValue).
  const snapshot = (h: HistYear) => Object.fromEntries(ROW_IDS.map((id) => [id, histValue(id, h)]));

  it('+$5 on Rooms Revenue moves Rooms Revenue and the derived Total Revenue by $5 — and nothing stated', () => {
    const before = snapshot(hy());
    const changes = diffHistColumn(before, hy({ rooms: 11_000_005 }), ROW_IDS);
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
