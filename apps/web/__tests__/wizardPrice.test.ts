/**
 * FON-99 — the wizard's Purchase / Indicative Price parser.
 *
 * The one rule that matters: a number the analyst did not type is never
 * invented. A range stores its midpoint and SAYS "range midpoint"; text that
 * does not read as a price yields null and is kept as a note.
 */
import { describe, it, expect } from 'vitest';
import {
  parseIndicativePrice,
  purchasePriceFromText,
  describeIndicativePrice,
  indicativePriceHint,
} from '@/lib/wizardPrice';

describe('parseIndicativePrice — single figures', () => {
  it.each([
    ['$36.4M', 36_400_000],
    ['36.4M', 36_400_000],
    ['$36.4 mm', 36_400_000],
    ['36,400,000', 36_400_000],
    ['$36,400,000', 36_400_000],
    ['36400000', 36_400_000],
    ['$1.2B', 1_200_000_000],
    ['120k', 120_000],
    ['$120 million', 120_000_000],
    ['36.4M USD', 36_400_000],
  ])('%s → %d', (text, expected) => {
    const p = parseIndicativePrice(text);
    expect(p.kind).toBe('single');
    expect(purchasePriceFromText(text)).toBe(expected);
    expect(describeIndicativePrice(text)).toBe(`$${expected.toLocaleString('en-US')}`);
  });
});

describe('parseIndicativePrice — ranges store the midpoint and say so', () => {
  it.each([
    ['$120-140M', 120_000_000, 140_000_000, 130_000_000],
    ['120–140M', 120_000_000, 140_000_000, 130_000_000],
    ['$120M - $140M', 120_000_000, 140_000_000, 130_000_000],
    ['120 to 140 M', 120_000_000, 140_000_000, 130_000_000],
    ['$36.4M — $38M', 36_400_000, 38_000_000, 37_200_000],
    ['140-120M', 120_000_000, 140_000_000, 130_000_000],
  ])('%s → midpoint %d', (text, low, high, mid) => {
    const p = parseIndicativePrice(text);
    expect(p.kind).toBe('range');
    if (p.kind !== 'range') return;
    expect(p.low).toBe(low);
    expect(p.high).toBe(high);
    expect(p.value).toBe(mid);
    expect(purchasePriceFromText(text)).toBe(mid);
    expect(describeIndicativePrice(text)).toBe(`$${mid.toLocaleString('en-US')} (range midpoint of ${text})`);
    expect(indicativePriceHint(text)).toMatch(/range midpoint/);
  });
});

describe('parseIndicativePrice — never a guess', () => {
  it.each(['TBD after NDA', 'call broker', '36', '36.4', '$', 'abc-def', '120-140 keys'])(
    '%s is unparseable → null, kept as a note',
    (text) => {
      expect(parseIndicativePrice(text).kind).toBe('unparseable');
      expect(purchasePriceFromText(text)).toBeNull();
      expect(describeIndicativePrice(text)).toBe(`"${text}" — not read as a price; set it on CAPEX → Deal Summary`);
      expect(indicativePriceHint(text)).toMatch(/Could not read a price/);
    },
  );

  it('blank is empty: null, a dash on Review and no hint', () => {
    expect(parseIndicativePrice('   ').kind).toBe('empty');
    expect(purchasePriceFromText('')).toBeNull();
    expect(describeIndicativePrice('')).toBe('—');
    expect(indicativePriceHint('')).toBeNull();
  });
});
