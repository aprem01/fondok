/**
 * Sam #6 — Market / Submarket helpers in lib/markets. Synthetic TEST ids only.
 */
import { describe, it, expect } from 'vitest';
import {
  marketOptions, resolveMarket, submarketOptions, US_HOTEL_MARKETS, type StrMarketRow,
} from '@/lib/markets';

const row = (over: Partial<StrMarketRow>): StrMarketRow => ({
  str_market_id: 'TEST-M1', market_name: 'Test Market One', submarket_id: null,
  submarket_name: null, state: 'TS', country: 'Testland', ...over,
});
const ROWS: StrMarketRow[] = [
  row({}),
  row({ submarket_id: 'TEST-S1', submarket_name: 'Test Sub Alpha' }),
  row({ submarket_id: 'TEST-S2', submarket_name: 'Test Sub Beta' }),
  row({ str_market_id: 'TEST-M2', market_name: 'Test Market Two', submarket_id: 'TEST-S3', submarket_name: 'Test Sub Gamma' }),
];

describe('marketOptions', () => {
  it('without STR rows: the 62 name-only market names, no id', () => {
    const opts = marketOptions([]);
    expect(US_HOTEL_MARKETS).toHaveLength(62);
    expect(opts).toHaveLength(62);
    expect(opts.every((o) => o.strMarketId === null)).toBe(true);
    expect(opts[0].detail).toBe('STR market name · no STR id');
  });

  it('with STR rows: one option per STR market id, carrying the id', () => {
    const opts = marketOptions(ROWS);
    expect(opts.map((o) => [o.name, o.strMarketId])).toEqual([
      ['Test Market One', 'TEST-M1'],
      ['Test Market Two', 'TEST-M2'],
    ]);
    expect(opts[0].detail).toBe('STR id TEST-M1 · TS, Testland');
  });
});

describe('resolveMarket', () => {
  it('an exact (case / punctuation-insensitive) unique match takes STR spelling + id', () => {
    expect(resolveMarket('  test market ONE ', ROWS)).toEqual({ market: 'Test Market One', strMarketId: 'TEST-M1' });
  });

  it('typed text with no match keeps the text and no id', () => {
    expect(resolveMarket('Test Market', ROWS)).toEqual({ market: 'Test Market', strMarketId: null });
  });

  it('two STR markets with the same name → no id (never guessed)', () => {
    const dup = [...ROWS, row({ str_market_id: 'TEST-M9', market_name: 'Test Market One' })];
    expect(resolveMarket('Test Market One', dup)).toEqual({ market: 'Test Market One', strMarketId: null });
  });

  it('without STR rows it folds onto the known spelling, still no id', () => {
    expect(resolveMarket('washington dc', [])).toEqual({ market: 'Washington, DC', strMarketId: null });
    expect(resolveMarket('   ', [])).toEqual({ market: '', strMarketId: null });
  });
});

describe('submarketOptions', () => {
  it('only from STR rows — none without them', () => {
    expect(submarketOptions([], null)).toEqual([]);
    expect(submarketOptions(ROWS, null)).toEqual(['Test Sub Alpha', 'Test Sub Beta', 'Test Sub Gamma']);
  });

  it('narrows to the picked STR market', () => {
    expect(submarketOptions(ROWS, 'TEST-M2')).toEqual(['Test Sub Gamma']);
  });
});
