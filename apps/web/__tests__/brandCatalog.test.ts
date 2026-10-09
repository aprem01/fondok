/**
 * R-020 / R-021 / R-022 / R-024 — the brand catalog.
 *
 *   R-020  Accor, Meliá, Minor Hotels, Motel One are groups; "Starwood" finds
 *          the legacy Starwood brands under Marriott; Club Quarters is an
 *          independent option under Other Brands.
 *   R-021  group-owned brands no longer sit under Other Brands.
 *   R-022  verified missing brands are present (Dream → Hyatt, Graduate → Hilton).
 *   R-024  every chain-scaled brand has a default positioning that is a real
 *          positioningTiers id.
 */
import { describe, expect, it } from 'vitest';
import {
  brandChain,
  brandDefaultPositioning,
  brandFamilies,
  positioningTiers,
  searchBrandFamilies,
} from '@/lib/mockData';

const fam = (name: string) => brandFamilies.find((f) => f.family === name);
const names = (family: string) => (fam(family)?.brands ?? []).map((b) => b.name);

describe('R-020 — new groups', () => {
  it.each(['Accor', 'Meliá Hotels International', 'Minor Hotels', 'Motel One Group'])('%s is a group', (g) => {
    expect(fam(g)).toBeDefined();
    expect(fam(g)!.brands.length).toBeGreaterThan(0);
  });

  it('carries each group\'s current brands', () => {
    expect(names('Accor')).toEqual(expect.arrayContaining(['Sofitel', 'Raffles Hotels & Resorts', 'Fairmont Hotels and Resorts', 'Novotel', 'Pullman Hotels and Resorts', 'Swissôtel', 'Mövenpick Hotels and Resorts', 'Mercure', 'ibis']));
    expect(names('Meliá Hotels International')).toEqual(expect.arrayContaining(['Gran Meliá', 'ME by Meliá', 'Paradisus by Meliá', 'INNSiDE by Meliá', 'Sol by Meliá']));
    expect(names('Minor Hotels')).toEqual(expect.arrayContaining(['Anantara', 'Avani', 'NH Hotels', 'NH Collection', 'nhow', 'Tivoli']));
    expect(names('Motel One Group')).toEqual(['Motel One', 'The Cloud One Hotels']);
  });

  it('there is no "Starwood" group; searching "Starwood" lists the legacy brands under Marriott', () => {
    expect(brandFamilies.some((f) => /starwood/i.test(f.family))).toBe(false);
    const hits = searchBrandFamilies('Starwood');
    const marriott = hits.find((f) => f.family === 'Marriott International');
    expect(marriott).toBeDefined();
    const legacy = marriott!.brands.map((b) => b.name);
    for (const b of ['Sheraton', 'The Westin Hotels & Resorts', 'W Hotels', 'St. Regis Hotels & Resorts', 'The Luxury Collection', 'Le Méridien', 'Aloft Hotels', 'Element Hotels', 'Four Points by Sheraton', 'Tribute Portfolio', 'Design Hotels']) {
      expect(legacy).toContain(b);
    }
    // Non-Starwood Marriott brands are not swept in by the alias.
    expect(legacy).not.toContain('Courtyard by Marriott');
    expect(fam('Marriott International')!.note).toMatch(/Starwood/);
  });

  it('Club Quarters is an independent option under Other Brands', () => {
    expect(names('Other Brands')).toContain('Club Quarters Hotels');
    expect(brandChain('Club Quarters Hotels')).toBe('Other Brands');
  });
});

describe('R-021 — group-owned brands left Other Brands', () => {
  it.each([
    ['Sofitel', 'Accor'], ['Fairmont Hotels and Resorts', 'Accor'], ['Raffles Hotels & Resorts', 'Accor'],
    ['Novotel', 'Accor'], ['ibis', 'Accor'], ['SLS Hotels', 'Accor'], ['Mondrian', 'Accor'], ['Delano', 'Accor'],
    ['citizenM', 'Marriott'],
  ])('%s → %s', (brand, chain) => {
    expect(names('Other Brands')).not.toContain(brand);
    expect(brandChain(brand)).toBe(chain);
  });
});

describe('R-022 — verified missing brands', () => {
  it('Dream Hotels is a Hyatt brand; Graduate is a Hilton brand', () => {
    expect(brandChain('Dream Hotels')).toBe('Hyatt');
    expect(brandChain('Graduate by Hilton')).toBe('Hilton');
  });

  it('Kimpton still resolves to IHG (wizard "· IHG")', () => {
    expect(brandChain('Kimpton')).toBe('IHG');
    expect(brandChain('Kimpton Hotels & Restaurants')).toBe('IHG');
  });
});

describe('catalog integrity', () => {
  it('no duplicate brand names across the catalog', () => {
    const all = brandFamilies.flatMap((f) => f.brands.map((b) => b.name.toLowerCase()));
    expect(new Set(all).size).toBe(all.length);
  });

  it('every group records at least one source URL; counts match the lists', () => {
    for (const f of brandFamilies) {
      expect(f.sources.length, f.family).toBeGreaterThan(0);
      for (const u of f.sources) expect(u, f.family).toMatch(/^https:\/\//);
      expect(f.count, f.family).toBe(f.brands.length);
    }
  });

  it('every brand keeps a tier', () => {
    for (const f of brandFamilies) for (const b of f.brands) expect(b.tier, b.name).toBeTruthy();
  });
});

describe('R-024 — default positioning per brand', () => {
  const tierIds = new Set(positioningTiers.map((p) => p.id));

  it('maps every chain-scaled brand onto a real positioning tier', () => {
    for (const f of brandFamilies) {
      for (const b of f.brands) {
        const pos = brandDefaultPositioning(b.name);
        if (b.tier === 'Various' && !b.scale) expect(pos, b.name).toBeNull();
        else expect(tierIds.has(pos ?? ''), b.name).toBe(true);
      }
    }
  });

  it('uses the STR chain scale when STR publishes one', () => {
    expect(brandDefaultPositioning('Kimpton Hotels & Restaurants')).toBe('upper-upscale');
    expect(brandDefaultPositioning('Motel One')).toBe('upper-midscale');
    expect(brandDefaultPositioning('Club Quarters Hotels')).toBe('upper-upscale');
    expect(brandDefaultPositioning('Sofitel')).toBe('luxury');
    // STR places Holiday Inn in Upper Midscale even though the legacy catalog
    // tier reads Upscale — the published chain scale wins for the prefill.
    expect(brandDefaultPositioning('Holiday Inn')).toBe('upper-midscale');
  });

  it('pre-fills nothing for unknown brands or brands without a single scale', () => {
    expect(brandDefaultPositioning('agnostic')).toBeNull();
    expect(brandDefaultPositioning('Independent / Unflagged')).toBeNull();
    expect(brandDefaultPositioning('Design Hotels')).toBeNull();
    expect(brandDefaultPositioning(null)).toBeNull();
  });
});
