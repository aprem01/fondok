/**
 * Sam 2026-10-15 (om-brand-str-scale) — the worker's brand catalog JSON
 * (apps/worker/app/data/brand_chain_scales.json, read by the OM brand → STR
 * chain scale suggestion) is the export of this app's `brandFamilies`. This
 * test fails whenever the two drift: after any catalog edit run
 *
 *   node scripts/export_brand_catalog.mjs
 *
 * and commit the regenerated JSON.
 *
 * Also locks the chip copy for the Overview's positioning source.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { brandFamilies, positioningTiers } from '@/lib/catalogs';
import { positioningSourceChip } from '@/lib/positioningSource';
import {
  buildBrandCatalog,
  serializeBrandCatalog,
  POSITIONING_ID_BY_STR_SCALE,
} from '../../../scripts/export_brand_catalog.mjs';

const JSON_PATH = path.resolve(__dirname, '../../worker/app/data/brand_chain_scales.json');

describe('worker brand catalog JSON ↔ web brandFamilies', () => {
  it('the committed JSON is byte-identical to a fresh export of brandFamilies', () => {
    const committed = readFileSync(JSON_PATH, 'utf8');
    expect(committed).toBe(serializeBrandCatalog(buildBrandCatalog(brandFamilies)));
  });

  it('carries every brand with its aliases and STR scale (and only STR scale)', () => {
    const doc = JSON.parse(readFileSync(JSON_PATH, 'utf8'));
    const webBrands = brandFamilies.flatMap((f) => f.brands);
    expect(doc.brands).toHaveLength(webBrands.length);
    const kimpton = doc.brands.find((b: { name: string }) => b.name === 'Kimpton Hotels & Restaurants');
    expect(kimpton).toMatchObject({ str_scale: 'Upper Upscale', positioning: 'upper-upscale', family: 'IHG Hotels & Resorts' });
    // "Various" / no STR row → no scale exported, even where the catalog has a tier.
    const baccarat = doc.brands.find((b: { name: string }) => b.name === 'Baccarat Hotels & Resorts');
    expect(baccarat).toMatchObject({ str_scale: null, positioning: null });
    expect(doc.brands.find((b: { name: string }) => b.name === 'Sheraton').aliases).toEqual(['Starwood']);
  });

  it('every exported positioning id is a real positioningTiers id', () => {
    const ids = new Set(positioningTiers.map((p) => p.id));
    for (const id of Object.values(POSITIONING_ID_BY_STR_SCALE)) expect(ids.has(id)).toBe(true);
  });
});

describe('positioning source chip', () => {
  it('OM brand → STR scale names the brand, the scale, the document and page', () => {
    const chip = positioningSourceChip({
      positioning: 'upper-upscale',
      positioning_source: 'om_brand_str_scale',
      positioning_source_detail: {
        document_filename: 'Anglers OM.pdf', page: 4, om_brand: 'Kimpton',
        catalog_brand: 'Kimpton Hotels & Restaurants', str_scale: 'Upper Upscale',
      },
    });
    expect(chip?.kind).toBe('om_brand_str_scale');
    expect(chip?.text).toBe('From OM brand Kimpton → STR Upper Upscale (Anglers OM.pdf, p.4)');
  });

  it('never invents a page or filename', () => {
    const chip = positioningSourceChip({
      positioning: 'upper-upscale',
      positioning_source: 'om_brand_str_scale',
      positioning_source_detail: { om_brand: 'Kimpton', str_scale: 'Upper Upscale', page: null },
    });
    expect(chip?.text).toBe('From OM brand Kimpton → STR Upper Upscale (OM, page not cited)');
  });

  it('Analyst, unrecorded legacy tier, and Default (no chip)', () => {
    expect(positioningSourceChip({ positioning: 'luxury', positioning_source: 'analyst' })?.text).toBe('Analyst');
    expect(positioningSourceChip({ positioning: 'upscale', positioning_source: null })?.kind).toBe('unrecorded');
    expect(positioningSourceChip({ positioning: 'default', positioning_source: null })).toBeNull();
    expect(positioningSourceChip({ positioning: null })).toBeNull();
  });
});
