// Sam 2026-10-15 (om-brand-str-scale) — export the web brand catalog
// (apps/web/src/lib/mockData.ts `brandFamilies`) to the JSON the worker reads
// (apps/worker/app/data/brand_chain_scales.json), so the OM brand → STR chain
// scale suggestion uses exactly the catalog the wizard / Overview picker show.
//
//   node scripts/export_brand_catalog.mjs          # rewrite the JSON
//   node scripts/export_brand_catalog.mjs --check  # exit 1 if it is stale
//
// Node >= 22.18 strips the TypeScript types of mockData.ts natively (the file
// has no imports). apps/web/__tests__/brandCatalogExport.test.ts fails the web
// test run when the committed JSON and the catalog disagree.
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
export const CATALOG_JSON_PATH = resolve(ROOT, 'apps/worker/app/data/brand_chain_scales.json');
const MOCK_DATA_PATH = resolve(ROOT, 'apps/web/src/lib/mockData.ts');

/** STR chain-scale label → web `positioningTiers` id (mirrors the web's
 *  POSITIONING_BY_SCALE). A scale outside this map is a catalog error. */
export const POSITIONING_ID_BY_STR_SCALE = {
  Luxury: 'luxury',
  'Upper Upscale': 'upper-upscale',
  Upscale: 'upscale',
  'Upper Midscale': 'upper-midscale',
  Midscale: 'midscale',
  Economy: 'economy',
};

/**
 * The JSON document for a `brandFamilies` array. Only what the worker's
 * matcher needs: each brand's name, explicit aliases, and its STR chain scale
 * WHERE STR PUBLISHES ONE (`scale`). The catalog's own `tier` is deliberately
 * not exported — the OM suggestion is STR's scale or nothing.
 */
export function buildBrandCatalog(brandFamilies) {
  const brands = [];
  for (const fam of brandFamilies) {
    for (const b of fam.brands) {
      const scale = b.scale ?? null;
      if (scale !== null && !(scale in POSITIONING_ID_BY_STR_SCALE)) {
        throw new Error(`brand ${b.name}: unknown STR scale ${scale}`);
      }
      brands.push({
        name: b.name,
        family: fam.family,
        aliases: [...(b.aliases ?? [])],
        str_scale: scale,
        positioning: scale === null ? null : POSITIONING_ID_BY_STR_SCALE[scale],
      });
    }
  }
  return {
    _generated_by:
      'scripts/export_brand_catalog.mjs from apps/web/src/lib/mockData.ts (brandFamilies) — do not edit by hand',
    str_source:
      'STR Chain Scales (February 2026), https://www.costar.com/products/str-benchmark/resources/guidelines/str-chain-scales',
    positioning_by_str_scale: POSITIONING_ID_BY_STR_SCALE,
    brands,
  };
}

export function serializeBrandCatalog(doc) {
  return JSON.stringify(doc, null, 2) + '\n';
}

async function main() {
  const { brandFamilies } = await import(pathToFileURL(MOCK_DATA_PATH).href);
  const out = serializeBrandCatalog(buildBrandCatalog(brandFamilies));
  if (process.argv.includes('--check')) {
    let current = '';
    try {
      current = readFileSync(CATALOG_JSON_PATH, 'utf8');
    } catch {
      /* missing → stale */
    }
    if (current !== out) {
      console.error(`${CATALOG_JSON_PATH} is stale — run: node scripts/export_brand_catalog.mjs`);
      process.exit(1);
    }
    console.log('brand catalog JSON is up to date');
    return;
  }
  writeFileSync(CATALOG_JSON_PATH, out);
  console.log(`wrote ${CATALOG_JSON_PATH} (${JSON.parse(out).brands.length} brands)`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}
