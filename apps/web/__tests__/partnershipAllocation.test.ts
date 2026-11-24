/**
 * FON-66 (Sam, 9-14) — the "Allocation of Projected Proceeds" preview must
 * list EVERY configured promote tier, not only the bands the waterfall
 * reached. Pure-function tests over the merge the Partnership tab renders.
 */
import { describe, expect, it } from 'vitest';
import {
  allocationPreviewRows,
  pct0,
  promoteRowName,
  promoteTierHurdleCell,
  promoteTierLabel,
  promoteTierName,
  STRUCTURAL_TIER_NAME,
  type ConfiguredTier,
  type TierAllocationRow,
} from '@/lib/partnershipAllocation';

const row = (label: string, kind: TierAllocationRow['kind'], gp: number, lp: number): TierAllocationRow => ({
  label, kind, gp_amount: gp, lp_amount: lp, total_amount: gp + lp,
});

// Sam's stack: 8% pref band (100% LP), then promote bands above it.
const SAM: ConfiguredTier[] = [
  { hurdle: 0.08, gp: 0 },
  { hurdle: 0.12, gp: 0.2 },
  { hurdle: 0.15, gp: 0.3 },
  { hurdle: 0.20, gp: 0.4 },
];

describe('promoteTierLabel mirrors the worker', () => {
  it('labels the pref band, mid bands and the open-ended top band', () => {
    expect(promoteTierLabel(0, 0.08, 0, undefined, false)).toBe('Preferred (to 8%)');
    expect(promoteTierLabel(1, 0.12, 0.2, 0.08, false)).toBe('Tier 2 (to 12%)');
    expect(promoteTierLabel(3, 0.20, 0.4, 0.15, true)).toBe('Tier 4 (>15%)');
    // A single-band stack is its own top: no previous hurdle → "to".
    expect(promoteTierLabel(0, 0.10, 0.3, undefined, true)).toBe('Tier 1 (to 10%)');
  });

  // FON-66 (Sam, 2026-11-16) — the Promote Waterfall row name carries the SAME
  // number as the worker's label for the band, and the same number as the
  // hurdle cell beside it for every capped band. A one-tier stack read
  // "above 12%" next to "Until 12%" before this.
  it('promoteTierName carries the same hurdle as the worker label and the hurdle cell', () => {
    const stack = SAM.slice(1) as { hurdle: number; gp: number }[]; // the promote bands: 12 / 15 / 20
    stack.forEach((t, pos) => {
      const isTop = pos === stack.length - 1;
      const prev = pos > 0 ? stack[pos - 1].hurdle : undefined;
      const worker = promoteTierLabel(pos, t.hurdle, t.gp, prev, isTop && stack.length > 1);
      const name = promoteTierName(pos, t.hurdle, prev, isTop);
      const pctOf = (s: string) => /(\d+)%/.exec(s)?.[1];
      expect(pctOf(name)).toBe(pctOf(worker));
      if (!(isTop && pos > 0)) {
        expect(name).toBe(`Promote — to ${pct0(t.hurdle)} LP IRR`);
        expect(promoteTierHurdleCell(t.hurdle)).toBe(`Until ${pct0(t.hurdle)} LP IRR`);
      } else {
        expect(name).toBe(`Promote — above ${pct0(prev as number)} LP IRR`);
      }
    });
    // Sam's one-tier entry (12% hurdle): "to 12%" beside "Until 12%".
    expect(promoteTierName(0, 0.12, undefined, true)).toBe('Promote — to 12% LP IRR');
    expect(promoteTierHurdleCell(0.12)).toBe('Until 12% LP IRR');
    expect(promoteTierLabel(0, 0.12, 0.2, undefined, false)).toBe('Tier 1 (to 12%)');
  });

  it('rounds like Python .0% (ties to even)', () => {
    expect(pct0(0.125)).toBe('12%');
    expect(pct0(0.135)).toBe('14%');
    expect(pct0(0.15)).toBe('15%');
    expect(pct0(0.3)).toBe('30%');
  });
});

describe('allocationPreviewRows', () => {
  it("lists every configured tier when the waterfall stopped in one band (Sam's 9-14 run)", () => {
    const engine = [
      row('Return of Capital', 'return_of_capital', 2_000_000, 8_000_000),
      row('Preferred Return', 'preferred', 656_000, 2_624_000),
      row('Tier 2 (to 12%)', 'promote', 4_000_000, 16_000_000),
    ];
    const rows = allocationPreviewRows(engine, SAM);
    expect(rows.map((r) => r.label)).toEqual([
      'Return of Capital', 'Preferred Return',
      'Preferred (to 8%)', 'Tier 2 (to 12%)', 'Tier 3 (to 15%)', 'Tier 4 (>15%)',
    ]);
    expect(rows.map((r) => r.status)).toEqual([
      'reached', 'reached', 'not_reached', 'reached', 'not_reached', 'not_reached',
    ]);
    // Not-reached bands carry $0, never a fabricated amount; the engine's
    // dollars are untouched.
    const t3 = rows.find((r) => r.label === 'Tier 3 (to 15%)')!;
    expect([t3.gp_amount, t3.lp_amount, t3.total_amount]).toEqual([0, 0, 0]);
    const t2 = rows.find((r) => r.label === 'Tier 2 (to 12%)')!;
    expect(t2.total_amount).toBe(20_000_000);
    // Every engine dollar is still in the table.
    const sum = rows.reduce((s, r) => s + r.total_amount, 0);
    expect(sum).toBe(engine.reduce((s, r) => s + r.total_amount, 0));
  });

  it('keeps the engine rows in place when every band was reached', () => {
    const engine = [
      row('Return of Capital', 'return_of_capital', 2_000_000, 8_000_000),
      row('Preferred Return', 'preferred', 656_000, 2_624_000),
      row('Preferred (to 8%)', 'promote', 0, 509_399),
      row('Tier 2 (to 12%)', 'promote', 499_403, 1_997_613),
      row('Tier 3 (to 15%)', 'promote', 735_193, 1_715_450),
      row('Tier 4 (>15%)', 'promote', 6_825_175, 10_237_763),
    ];
    const rows = allocationPreviewRows(engine, SAM);
    expect(rows.map(({ displayLabel: _d, ...r }) => r)).toEqual(engine.map((r) => ({ ...r, status: 'reached' })));
  });

  it('keeps the GP catch-up row ahead of the promote bands', () => {
    const engine = [
      row('Return of Capital', 'return_of_capital', 1, 9),
      row('Preferred Return', 'preferred', 1, 9),
      row('GP Catch-Up', 'catch_up', 5, 0),
      row('Tier 2 (to 12%)', 'promote', 2, 8),
    ];
    const rows = allocationPreviewRows(engine, SAM);
    expect(rows.slice(0, 3).map((r) => r.kind)).toEqual(['return_of_capital', 'preferred', 'catch_up']);
    expect(rows.slice(3).map((r) => r.label)).toEqual([
      'Preferred (to 8%)', 'Tier 2 (to 12%)', 'Tier 3 (to 15%)', 'Tier 4 (>15%)',
    ]);
  });

  it('shows a half-entered band as incomplete, with no amounts, and labels the survivors as the worker packs them', () => {
    const configured: ConfiguredTier[] = [
      { hurdle: 0.08, gp: 0 },
      { hurdle: 0.12, gp: 0.2 },
      { hurdle: undefined, gp: 0.3 }, // analyst has not set the hurdle
      { hurdle: 0.20, gp: 0.4 },
    ];
    // The worker omits the incomplete band, so its packed stack is three
    // tiers and the top one is "Tier 3 (>12%)".
    const engine = [
      row('Return of Capital', 'return_of_capital', 1, 9),
      row('Tier 2 (to 12%)', 'promote', 2, 8),
      row('Tier 3 (>12%)', 'promote', 4, 6),
    ];
    const rows = allocationPreviewRows(engine, configured);
    expect(rows.map((r) => [r.label, r.status])).toEqual([
      ['Return of Capital', 'reached'],
      ['Preferred (to 8%)', 'not_reached'],
      ['Tier 2 (to 12%)', 'reached'],
      ['Tier 3 · incomplete', 'incomplete'],
      ['Tier 3 (>12%)', 'reached'],
    ]);
  });

  it('never drops an engine promote row the configured stack does not name', () => {
    const engine = [
      row('Return of Capital', 'return_of_capital', 1, 9),
      row('Tier 2 (to 25%)', 'promote', 3, 7), // an older run's label
    ];
    const rows = allocationPreviewRows(engine, SAM);
    // No configured band names it, so the engine label is the only name it has.
    expect(rows[rows.length - 1]).toEqual({ ...engine[1], status: 'reached', displayLabel: 'Tier 2 (to 25%)' });
    expect(rows.reduce((s, r) => s + r.total_amount, 0)).toBe(20);
  });

  it('returns the engine rows untouched with no configured stack, and nothing with no run', () => {
    const engine = [row('Return of Capital', 'return_of_capital', 1, 9)];
    expect(allocationPreviewRows(engine, [])).toEqual([
      { ...engine[0], status: 'reached', displayLabel: STRUCTURAL_TIER_NAME.return_of_capital },
    ]);
    expect(allocationPreviewRows(undefined, SAM)).toEqual([]);
    expect(allocationPreviewRows([], SAM)).toEqual([]);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// FON-66 (Sam's QA, 2026-11-23, d0bfe816) — the Allocation Preview still
// called the promote tier "Tier 1 (to 12%)" while the tier table above it
// said "Promote — to 12% LP IRR". The preview now DISPLAYS the tier table's
// name (`displayLabel`) and keeps the engine's positional label only as the
// key it matches `tier_allocations` rows by.
// ─────────────────────────────────────────────────────────────────────────
describe('allocationPreviewRows — displayLabel is the tier table’s name (FON-66, Sam 2026-11-23)', () => {
  /** Sam's one-tier entry: 12% hurdle, 20 / 80 — the engine labels it "Tier 1 (to 12%)". */
  const ONE_TIER: ConfiguredTier[] = [{ hurdle: 0.12, gp: 0.2 }];

  it('a one-tier 20/80 stack shows the promote under the tier table’s name, matched on the engine label', () => {
    const engine = [
      row('Return of Capital', 'return_of_capital', 1_000_000, 9_000_000),
      row('Preferred Return', 'preferred', 80_000, 720_000),
      row('Tier 1 (to 12%)', 'promote', 400_000, 1_600_000),
    ];
    const rows = allocationPreviewRows(engine, ONE_TIER);
    expect(rows.map((r) => r.displayLabel)).toEqual([
      'Tier I — Return of Capital', 'Tier II — Preferred Return', 'Promote — to 12% LP IRR',
    ]);
    // The same name the tier table computes for the band.
    expect(rows[2].displayLabel).toBe(promoteRowName(ONE_TIER, 0));
    expect(rows[2].displayLabel).toBe(promoteTierName(0, 0.12, undefined, true));
    // Matched on the engine's key, dollars untouched, nothing invented.
    expect(rows[2].label).toBe('Tier 1 (to 12%)');
    expect(rows[2].status).toBe('reached');
    expect(rows[2].total_amount).toBe(2_000_000);
    expect(rows.some((r) => r.displayLabel.startsWith('Tier 1'))).toBe(false);
  });

  it('a one-tier stack the waterfall never reached is a $0 row under the same name', () => {
    const engine = [row('Return of Capital', 'return_of_capital', 1, 9)];
    const rows = allocationPreviewRows(engine, ONE_TIER);
    expect(rows[1]).toEqual({
      label: 'Tier 1 (to 12%)', displayLabel: 'Promote — to 12% LP IRR', kind: 'promote',
      gp_amount: 0, lp_amount: 0, total_amount: 0, status: 'not_reached',
    });
  });

  it('a two-tier stack names the capped band "to" and the open-ended top band "above" the previous hurdle', () => {
    const twoTier: ConfiguredTier[] = [{ hurdle: 0.13, gp: 0.2 }, { hurdle: 0.18, gp: 0.35 }];
    const engine = [
      row('Return of Capital', 'return_of_capital', 1, 9),
      row('Tier 1 (to 13%)', 'promote', 2, 8),
      row('Tier 2 (>13%)', 'promote', 7, 13),
    ];
    const rows = allocationPreviewRows(engine, twoTier);
    expect(rows.map((r) => [r.label, r.displayLabel, r.status])).toEqual([
      ['Return of Capital', 'Tier I — Return of Capital', 'reached'],
      ['Tier 1 (to 13%)', 'Promote — to 13% LP IRR', 'reached'],
      ['Tier 2 (>13%)', 'Promote — above 13% LP IRR', 'reached'],
    ]);
    expect(rows.slice(1).map((r) => r.displayLabel)).toEqual([promoteRowName(twoTier, 0), promoteRowName(twoTier, 1)]);
  });

  it('names Sam’s 9-14 stack as the tier table does, including the incomplete and catch-up rows', () => {
    const configured: ConfiguredTier[] = [
      { hurdle: 0.08, gp: 0 },
      { hurdle: 0.12, gp: 0.2 },
      { hurdle: undefined, gp: 0.3 },
      { hurdle: 0.20, gp: 0.4 },
    ];
    const engine = [
      row('Return of Capital', 'return_of_capital', 1, 9),
      row('Preferred Return', 'preferred', 1, 9),
      row('GP Catch-Up', 'catch_up', 5, 0),
      row('Tier 2 (to 12%)', 'promote', 2, 8),
    ];
    const rows = allocationPreviewRows(engine, configured);
    expect(rows.map((r) => r.displayLabel)).toEqual([
      'Tier I — Return of Capital',
      'Tier II — Preferred Return',
      'Tier III — GP Catch-Up',
      'Promote — to 8% LP IRR',
      'Promote — to 12% LP IRR',
      'Promote — tier 3 · incomplete',
      // The top band after an incomplete neighbour has no previous hurdle to be
      // open-ended above — exactly what the tier table renders for it.
      'Promote — to 20% LP IRR',
    ]);
    // The engine keys are untouched underneath.
    expect(rows.slice(3).map((r) => r.label)).toEqual([
      'Preferred (to 8%)', 'Tier 2 (to 12%)', 'Tier 3 · incomplete', 'Tier 3 (>12%)',
    ]);
  });
});
