/**
 * FON-63 — the negative-NOI sentence builder (lib/noiWarning.ts).
 *
 *  • the engine's `noi_warning` is rendered VERBATIM when present;
 *  • otherwise the sentence is composed from engine numbers only, with USD
 *    separators and a true leading minus;
 *  • a run that predates the fields reads `present: false` → no strip.
 */
import { describe, it, expect } from 'vitest';
import {
  composeNoiWarning,
  fmtUsdShort,
  fmtUsdSigned,
  readNoiWarning,
  yearList,
} from '@/lib/noiWarning';

const env = (engines: Record<string, Record<string, unknown>>) => ({
  engines: Object.fromEntries(Object.entries(engines).map(([k, outputs]) => [k, { outputs }])),
});

describe('formatting', () => {
  it('USD with separators and a leading true minus', () => {
    expect(fmtUsdSigned(-69_983)).toBe('−$69,983');
    expect(fmtUsdSigned(-69_983.4)).toBe('−$69,983');
    expect(fmtUsdSigned(1_234_567)).toBe('$1,234,567');
    expect(fmtUsdSigned(-0.2)).toBe('$0');
  });

  it('shortfall: $M from a million up, whole dollars below', () => {
    expect(fmtUsdShort(1_920_000)).toBe('$1.92M');
    expect(fmtUsdShort(1_915_432)).toBe('$1.92M');
    expect(fmtUsdShort(845_000)).toBe('$845,000');
  });

  it('year lists', () => {
    expect(yearList([1])).toBe('Year 1');
    expect(yearList([2, 1])).toBe('Years 1 & 2');
    expect(yearList([1, 2, 3])).toBe('Years 1, 2 & 3');
    expect(yearList([])).toBe('');
  });
});

describe('composeNoiWarning', () => {
  it('composes the canonical one-liner', () => {
    expect(
      composeNoiWarning({
        negativeYears: [1],
        noiForYear: (y) => (y === 1 ? -69_983 : undefined),
        totalShortfallUsd: 1_920_000,
      }),
    ).toBe('Year 1 NOI is negative (−$69,983) · debt service shortfall $1.92M · DSCR N/A for Year 1');
  });

  it('omits the NOI figure when the run does not carry it — never estimated', () => {
    expect(composeNoiWarning({ negativeYears: [1], totalShortfallUsd: 1_920_000 })).toBe(
      'Year 1 NOI is negative · debt service shortfall $1.92M · DSCR N/A for Year 1',
    );
  });

  it('omits the shortfall when absent or zero', () => {
    expect(composeNoiWarning({ negativeYears: [1], totalShortfallUsd: 0 })).toBe(
      'Year 1 NOI is negative · DSCR N/A for Year 1',
    );
  });

  it('lists several years and their NOIs', () => {
    expect(
      composeNoiWarning({
        negativeYears: [2, 1],
        noiForYear: (y) => ({ 1: -69_983, 2: -12_000 } as Record<number, number>)[y],
        totalShortfallUsd: 3_500_000,
      }),
    ).toBe(
      'Years 1 & 2 NOI is negative (−$69,983 / −$12,000) · debt service shortfall $3.50M · DSCR N/A for Years 1 & 2',
    );
  });

  it('is null when nothing is negative', () => {
    expect(composeNoiWarning({ negativeYears: [] })).toBeNull();
  });
});

describe('readNoiWarning', () => {
  it('a run predating the fields is not present and warns about nothing', () => {
    const r = readNoiWarning(env({ debt: { year_one_dscr: 1.3 } }));
    expect(r).toEqual({ present: false, negativeYears: [], totalShortfallUsd: null, warning: null });
    expect(readNoiWarning(null).present).toBe(false);
  });

  it('present but nothing negative → no warning', () => {
    const r = readNoiWarning(env({ debt: { negative_noi_years: [], total_shortfall_usd: 0, noi_warning: null } }));
    expect(r.present).toBe(true);
    expect(r.warning).toBeNull();
  });

  it('renders the engine sentence verbatim', () => {
    const r = readNoiWarning(env({
      debt: { negative_noi_years: [1], total_shortfall_usd: 1_920_000, noi_warning: 'Engine says: Year 1 NOI < 0.' },
    }));
    expect(r.warning).toBe('Engine says: Year 1 NOI < 0.');
  });

  it('composes from expense NOI + schedule DSCR when the engine sent no sentence', () => {
    const r = readNoiWarning(env({
      debt: {
        negative_noi_years: [1],
        total_shortfall_usd: 1_920_000,
        noi_warning: null,
        schedule: [{ year: 1, dscr: null }, { year: 2, dscr: 1.4 }],
      },
      expense: { years: [{ noi: -69_983 }, { noi: 2_400_000 }] },
    }));
    expect(r.warning).toBe(
      'Year 1 NOI is negative (−$69,983) · debt service shortfall $1.92M · DSCR N/A for Year 1',
    );
  });

  it('falls back to returns.noi_by_year for the NOI figure', () => {
    const r = readNoiWarning(env({
      debt: { negative_noi_years: [1], total_shortfall_usd: 500_000 },
      returns: { noi_by_year: [-69_983, 2_000_000] },
    }));
    expect(r.warning).toBe('Year 1 NOI is negative (−$69,983) · debt service shortfall $500,000 · DSCR N/A for Year 1');
  });
});
