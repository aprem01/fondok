/**
 * FON-41 / R-012 — City / Submarket normalization + suggestions, and the
 * R-047 deal-type vocabulary helpers.
 */
import { describe, it, expect } from 'vitest';
import {
  normalizeLocation, tidyLocation, locationKey, locationSuggestions, US_HOTEL_MARKETS,
} from '@/lib/markets';
import { normalizeDealType, dealTypeLabel, isDevelopmentLike, DEAL_TYPE_OPTIONS } from '@/lib/dealTypes';

describe('normalizeLocation', () => {
  it.each([
    ['Washington, DC'],
    ['Washington,DC'],
    ['washington dc'],
    ['  Washington ,  DC  '],
    ['Washington, D.C.'],
    ['WASHINGTON DC'],
    ['Washington, DC-MD-VA'],
  ])('%j → "Washington, DC"', (raw) => {
    expect(normalizeLocation(raw)).toBe('Washington, DC');
  });

  it('folds STR composite market names onto the principal city', () => {
    expect(normalizeLocation('Tampa/St Petersburg, FL')).toBe('Tampa, FL');
    expect(normalizeLocation('anaheim/santa ana, ca')).toBe('Anaheim, CA');
    expect(normalizeLocation('nyc')).toBe('New York, NY');
    expect(normalizeLocation('st louis, mo')).toBe('St. Louis, MO');
  });

  it('keeps free text when nothing matches — only whitespace / comma spacing is tidied', () => {
    expect(normalizeLocation('  River North ,Chicago  ')).toBe('River North, Chicago');
    expect(normalizeLocation('Bozeman,MT')).toBe('Bozeman, MT');
    expect(normalizeLocation('Downtown   Asheville')).toBe('Downtown Asheville');
  });

  it('matches a tenant city spelling when it is not a known market', () => {
    expect(normalizeLocation('bozeman mt', ['Bozeman, MT'])).toBe('Bozeman, MT');
  });

  it('blank → empty string', () => {
    expect(normalizeLocation('   ')).toBe('');
    expect(normalizeLocation(' , ')).toBe('');
  });

  it('tidy / key helpers', () => {
    expect(tidyLocation('a ,b')).toBe('a, b');
    expect(locationKey('Washington, D.C.')).toBe('washington dc');
  });
});

describe('locationSuggestions', () => {
  it('lists tenant cities first (folded + deduped), then the market list without duplicates', () => {
    const out = locationSuggestions(['Bozeman,MT', 'washington dc', null, '', 'Bozeman, MT']);
    expect(out[0]).toBe('Bozeman, MT');
    expect(out[1]).toBe('Washington, DC');
    expect(out.filter((c) => c === 'Washington, DC')).toHaveLength(1);
    expect(out.length).toBe(US_HOTEL_MARKETS.length + 1);
  });

  it('market list is a non-trivial unique name list', () => {
    const keys = US_HOTEL_MARKETS.map((m) => locationKey(m.name));
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys.length).toBeGreaterThanOrEqual(50);
  });
});

describe('R-047 deal types', () => {
  it('offers Acquisition, Development, Adaptive Reuse', () => {
    expect(DEAL_TYPE_OPTIONS.map((d) => d.label)).toEqual(['Acquisition', 'Development', 'Adaptive Reuse']);
  });
  it('reads legacy "redevelopment" as Adaptive Reuse; unknown / null as Acquisition', () => {
    expect(normalizeDealType('adaptive_reuse')).toBe('adaptive_reuse');
    expect(normalizeDealType('redevelopment')).toBe('adaptive_reuse');
    expect(normalizeDealType(null)).toBe('acquisition');
    expect(dealTypeLabel(null)).toBe('—');
    expect(dealTypeLabel('adaptive_reuse')).toBe('Adaptive Reuse');
  });
  it('Adaptive Reuse takes the Development configuration', () => {
    expect(isDevelopmentLike('adaptive_reuse')).toBe(true);
    expect(isDevelopmentLike('development')).toBe(true);
    expect(isDevelopmentLike('acquisition')).toBe(false);
  });
});
