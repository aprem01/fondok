/**
 * Sam #6 / FON-96 — Market / Submarket helpers in lib/markets. Every key and
 * name here is a synthetic TEST value (fictional markets); no CoStar row.
 */
import { describe, it, expect } from 'vitest';
import {
  MARKET_PROVENANCE_LABEL, MARKET_SOURCE_LABEL, marketNameKey, marketOptions, resolveMarket,
  resolveSubmarket, submarketOptions, US_HOTEL_MARKETS, type StrMarketRow,
} from '@/lib/markets';

const NORTH = 'costar:northhaven-ts-usa';
const SOUTH = 'costar:southport-tz-usa';
const row = (over: Partial<StrMarketRow>): StrMarketRow => ({
  str_market_id: NORTH, market_name: 'Northhaven - TS USA', submarket_id: null,
  submarket_name: null, state: 'TS', country: 'USA', market_type: 'metro', ...over,
});
/** The FON-96 shape: market rows + submarkets, "Riverside" under two markets. */
const ROWS: StrMarketRow[] = [
  row({}),
  row({ submarket_id: `${NORTH}/harbor-district`, submarket_name: 'Harbor District' }),
  row({ submarket_id: `${NORTH}/riverside`, submarket_name: 'Riverside' }),
  row({ str_market_id: SOUTH, market_name: 'Southport - TZ USA', state: 'TZ' }),
  row({ str_market_id: SOUTH, market_name: 'Southport - TZ USA', state: 'TZ', submarket_id: `${SOUTH}/riverside`, submarket_name: 'Riverside' }),
  row({ str_market_id: 'costar:testland-north-usa', market_name: 'Testland North USA', market_type: 'regional', submarket_id: 'costar:testland-north-usa/lakeshore', submarket_name: 'Lakeshore' }),
];
/** A pre-FON-96 worker: no market_type, no market rows, vendor-style ids. */
const LEGACY: StrMarketRow[] = [
  { str_market_id: 'TEST-M1', market_name: 'Test Market One', submarket_id: 'TEST-S1', submarket_name: 'Test Sub Alpha', state: 'TS', country: 'Testland' },
  { str_market_id: 'TEST-M2', market_name: 'Test Market Two', submarket_id: 'TEST-S3', submarket_name: 'Test Sub Gamma', state: 'TS', country: 'Testland' },
];

describe('marketNameKey', () => {
  it('folds the CoStar spelling and the typed spelling together, drops a trailing USA', () => {
    expect(marketNameKey('Northhaven - TS USA')).toBe('northhaven ts');
    expect(marketNameKey('Northhaven, TS')).toBe('northhaven ts');
    expect(marketNameKey('  northhaven ,ts ')).toBe('northhaven ts');
    expect(marketNameKey('Testland North USA')).toBe('testland north');
  });
});

describe('marketOptions', () => {
  it('without reference rows: the 62 public name-only market names, no key', () => {
    const opts = marketOptions([]);
    expect(US_HOTEL_MARKETS).toHaveLength(62);
    expect(opts).toHaveLength(62);
    expect(opts.every((o) => o.strMarketId === null)).toBe(true);
    expect(opts[0].detail).toBe('STR market name · public list');
  });

  it('with reference rows: one option per market key; the detail names the type, never the key', () => {
    const opts = marketOptions(ROWS);
    expect(opts.map((o) => [o.name, o.strMarketId])).toEqual([
      ['Northhaven - TS USA', NORTH],
      ['Southport - TZ USA', SOUTH],
      ['Testland North USA', 'costar:testland-north-usa'],
    ]);
    expect(opts[0].detail).toBe('Metro market · TS, USA');
    expect(opts[2].detail).toBe('Regional market · TS, USA');
    expect(opts.some((o) => o.detail.includes('costar:'))).toBe(false);
  });

  it('feature-detects an older worker: no market_type → "CoStar market"', () => {
    expect(marketOptions(LEGACY)[0].detail).toBe('CoStar market · TS, Testland');
  });

  it('labels say CoStar / analyst-entered and never "STR id"', () => {
    expect(MARKET_SOURCE_LABEL.reference).toBe('Suggestions: CoStar market list');
    expect(MARKET_SOURCE_LABEL.fallback).toContain('public list');
    expect(MARKET_PROVENANCE_LABEL.market).toBe('CoStar market');
    expect(MARKET_PROVENANCE_LABEL.submarketOf('Northhaven - TS USA')).toBe('CoStar submarket of Northhaven - TS USA');
    expect(MARKET_PROVENANCE_LABEL.analyst).toBe('Analyst-entered · not in the CoStar list');
    for (const v of Object.values({ ...MARKET_SOURCE_LABEL, ...MARKET_PROVENANCE_LABEL })) {
      if (typeof v === 'string') expect(v).not.toMatch(/STR id/);
    }
  });
});

describe('resolveMarket', () => {
  it('an exact (case / punctuation-insensitive) unique match takes the list spelling + key', () => {
    expect(resolveMarket('  northhaven - ts USA ', ROWS)).toEqual({ market: 'Northhaven - TS USA', strMarketId: NORTH });
  });

  it('"City, ST" as analysts type it matches the "City - ST USA" market name', () => {
    expect(resolveMarket('Northhaven, TS', ROWS)).toEqual({ market: 'Northhaven - TS USA', strMarketId: NORTH });
    expect(resolveMarket('southport, tz', ROWS)).toEqual({ market: 'Southport - TZ USA', strMarketId: SOUTH });
  });

  it('typed text with no match keeps the text and no key', () => {
    expect(resolveMarket('Northhaven', ROWS)).toEqual({ market: 'Northhaven', strMarketId: null });
  });

  it('two markets with the same name → no key (never guessed)', () => {
    const dup = [...ROWS, row({ str_market_id: 'costar:other', market_name: 'Northhaven - TS USA' })];
    expect(resolveMarket('Northhaven - TS USA', dup)).toEqual({ market: 'Northhaven - TS USA', strMarketId: null });
  });

  it('without reference rows it folds onto the known public spelling, still no key', () => {
    expect(resolveMarket('washington dc', [])).toEqual({ market: 'Washington, DC', strMarketId: null });
    expect(resolveMarket('   ', [])).toEqual({ market: '', strMarketId: null });
  });

  it('still resolves against an older worker’s rows', () => {
    expect(resolveMarket('test market one', LEGACY)).toEqual({ market: 'Test Market One', strMarketId: 'TEST-M1' });
  });
});

describe('submarketOptions', () => {
  it('only from reference rows — none without them', () => {
    expect(submarketOptions([], null)).toEqual([]);
  });

  it('with no market picked, every option is "{submarket} · {market}" so same names differ', () => {
    const opts = submarketOptions(ROWS, null);
    expect(opts.map((o) => o.value)).toEqual([
      'Harbor District · Northhaven - TS USA',
      'Riverside · Northhaven - TS USA',
      'Riverside · Southport - TZ USA',
      'Lakeshore · Testland North USA',
    ]);
    expect(opts[1].submarketKey).toBe(`${NORTH}/riverside`);
    expect(opts[2].submarketKey).toBe(`${SOUTH}/riverside`);
    expect(opts.some((o) => o.value.includes('costar:') || o.label.includes('costar:'))).toBe(false);
  });

  it('narrows to the picked market and shows the plain submarket name', () => {
    const opts = submarketOptions(ROWS, SOUTH);
    expect(opts.map((o) => o.value)).toEqual(['Riverside']);
    expect(opts[0].label).toBe('CoStar submarket of Southport - TZ USA');
  });
});

describe('resolveSubmarket', () => {
  it('within the picked market: an exact unique name → key + list spelling', () => {
    expect(resolveSubmarket(' riverside ', ROWS, NORTH)).toEqual({
      submarket: 'Riverside', submarketKey: `${NORTH}/riverside`, marketKey: NORTH, market: 'Northhaven - TS USA',
    });
  });

  it('with no market picked, a name under two markets is ambiguous → no key (Arlington rule)', () => {
    expect(resolveSubmarket('Riverside', ROWS, null)).toEqual({
      submarket: 'Riverside', submarketKey: null, marketKey: null, market: null,
    });
  });

  it('a "{submarket} · {market}" pick resolves the submarket AND names its market', () => {
    expect(resolveSubmarket('Riverside · Southport - TZ USA', ROWS, null)).toEqual({
      submarket: 'Riverside', submarketKey: `${SOUTH}/riverside`, marketKey: SOUTH, market: 'Southport - TZ USA',
    });
  });

  it('a submarket of another market is not matched within the picked one; the text is kept', () => {
    expect(resolveSubmarket('Harbor District', ROWS, SOUTH)).toEqual({
      submarket: 'Harbor District', submarketKey: null, marketKey: null, market: null,
    });
    expect(resolveSubmarket('Harbor District · Southport - TZ USA', ROWS, null).submarketKey).toBeNull();
  });

  it('typed text with no match keeps the submarket part only; blank → empty', () => {
    expect(resolveSubmarket('Somewhere Typed', ROWS, NORTH)).toEqual({
      submarket: 'Somewhere Typed', submarketKey: null, marketKey: null, market: null,
    });
    expect(resolveSubmarket('Nowhere · Northhaven - TS USA', ROWS, null).submarket).toBe('Nowhere');
    expect(resolveSubmarket('   ', ROWS, null)).toEqual({ submarket: '', submarketKey: null, marketKey: null, market: null });
    expect(resolveSubmarket('Riverside', [], NORTH).submarketKey).toBeNull();
  });
});
