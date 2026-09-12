/**
 * fieldValue — the one comparison every inline editor asks before it writes
 * (FON-63 / FON-66 §1 / FON-65).
 *
 * Sam, 2026-09-11: *"clicking Save still causes Fondok to treat the value as an
 * analyst Override, even though the value itself was unchanged."* The draft is
 * a human-typed string in display units; the stored value is the engine's
 * number in persisted units. This table pins that both sides land on the same
 * canonical form — and, just as important, that a genuinely changed value is
 * NEVER swallowed.
 */
import { describe, it, expect } from 'vitest';
import { normalizeForCompare, isNoOpEdit, type FieldUnit } from '@/lib/fieldValue';

describe('normalizeForCompare', () => {
  const cases: [string | number | null | undefined, FieldUnit, number | string | null][] = [
    // Dollars — affixes stripped, compared to the cent.
    ['$23,660,000', 'usd', 23_660_000],
    ['23660000.004', 'usd', 23_660_000],
    [23_660_000, 'usd', 23_660_000],
    // A rate stored as a fraction; a typed string is whole percent.
    ['6.80', 'pct_fraction', 0.068],
    ['6.8', 'pct_fraction', 0.068],
    [0.068, 'pct_fraction', 0.068],
    ['65', 'pct_fraction', 0.65],
    ['65%', 'pct_fraction', 0.65],
    // A percent stored whole (1.50 = 1.5%) — both sides fold to a fraction.
    ['1.50', 'pct_whole', 0.015],
    [1.5, 'pct_whole', 0.015],
    // Plain multiples / ratios keep their scale.
    ['2.47', 'ratio', 2.47],
    [1.35, 'ratio', 1.35],
    // Whole-number units.
    ['24', 'months', 24],
    [24.0, 'months', 24],
    ['7', 'years', 7],
    ['132', 'count', 132],
    // Dates + text.
    ['2026-03-01T00:00:00Z', 'date', '2026-03-01'],
    ['2026-03-01', 'date', '2026-03-01'],
    ['  Kimpton EPIC  ', 'text', 'Kimpton EPIC'],
    // Absence, in every shape.
    ['', 'usd', null],
    ['   ', 'text', null],
    [null, 'usd', null],
    [undefined, 'pct_fraction', null],
    ['not a number', 'usd', null],
  ];

  it.each(cases)('normalizes %o as %s → %o', (raw, unit, expected) => {
    expect(normalizeForCompare(raw, unit)).toBe(expected);
  });
});

describe('isNoOpEdit — an unchanged value is not an override', () => {
  it('collapses the ways one rate can be spelled', () => {
    // The exact case from Sam's report: the editor opens on "6.80" over 0.068.
    expect(isNoOpEdit('6.80', 0.068, 'pct_fraction')).toBe(true);
    expect(isNoOpEdit('6.8', 0.068, 'pct_fraction')).toBe(true);
    expect(isNoOpEdit('65', 0.65, 'pct_fraction')).toBe(true);
    expect(isNoOpEdit(0.068, 0.068, 'pct_fraction')).toBe(true);
  });

  it('collapses trailing zeros and thousands separators on dollars', () => {
    expect(isNoOpEdit('23,660,000', 23_660_000, 'usd')).toBe(true);
    expect(isNoOpEdit('$23660000.00', 23_660_000, 'usd')).toBe(true);
  });

  it('never swallows a real change', () => {
    expect(isNoOpEdit('7.00', 0.068, 'pct_fraction')).toBe(false);
    expect(isNoOpEdit('6.81', 0.068, 'pct_fraction')).toBe(false);
    expect(isNoOpEdit('23,660,001', 23_660_000, 'usd')).toBe(false);
    expect(isNoOpEdit('25', 24, 'months')).toBe(false);
    expect(isNoOpEdit('Kimpton EPIC Miami', 'Kimpton EPIC', 'text')).toBe(false);
    expect(isNoOpEdit('2026-03-02', '2026-03-01', 'date')).toBe(false);
  });

  it('is null-safe: filling an empty field, or clearing one, is a real edit', () => {
    expect(isNoOpEdit(0.068, null, 'pct_fraction')).toBe(false);
    expect(isNoOpEdit(null, 0.068, 'pct_fraction')).toBe(false);
    expect(isNoOpEdit('', '', 'usd')).toBe(true);
    expect(isNoOpEdit(null, undefined, 'text')).toBe(true);
  });

  it('compares a whole-percent field in its own unit', () => {
    // Origination fee: 1.50% is persisted as 1.50, not 0.015.
    expect(isNoOpEdit('1.50', 1.5, 'pct_whole')).toBe(true);
    expect(isNoOpEdit('1.75', 1.5, 'pct_whole')).toBe(false);
  });
});
