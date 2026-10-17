// Sam 2026-10-15 — "the source should remain visible": the provenance chip
// beside the Overview's Positioning select. The worker records where
// `deals.positioning` came from (`positioning_source`):
//
//   om_brand_str_scale — the OM's brand matched the brand catalog and STR's
//                        chain scale for it was filled while the deal had no
//                        tier (detail: document, page, OM brand, STR scale);
//   analyst            — chosen in the New Project wizard or on the Overview;
//   null               — Default (no tier), or a tier stored before the
//                        source was recorded.
//
// Nothing here is inferred: a missing page reads "page not cited", a missing
// filename reads "OM" — never an invented value.
import type { PositioningSourceDetail, WorkerDeal } from './api';

export type PositioningSourceChip = {
  kind: 'om_brand_str_scale' | 'analyst' | 'unrecorded';
  text: string;
  title: string;
};

export function positioningSourceChip(
  deal: Pick<WorkerDeal, 'positioning' | 'positioning_source' | 'positioning_source_detail'> | null | undefined,
): PositioningSourceChip | null {
  if (!deal) return null;
  const tier = (deal.positioning ?? '').trim();
  const source = deal.positioning_source ?? null;
  if (source === 'om_brand_str_scale') {
    const d: PositioningSourceDetail = deal.positioning_source_detail ?? {};
    const brand = d.om_brand || d.catalog_brand || 'brand';
    const scale = d.str_scale ? `STR ${d.str_scale}` : 'STR chain scale';
    const doc = d.document_filename || 'OM';
    const page = typeof d.page === 'number' ? `p.${d.page}` : 'page not cited';
    return {
      kind: 'om_brand_str_scale',
      text: `From OM brand ${brand} → ${scale} (${doc}, ${page})`,
      title:
        `The OM names the brand "${brand}"` +
        (d.catalog_brand ? `, matched to ${d.catalog_brand}` : '') +
        `; STR publishes its chain scale${d.str_scale ? ` as ${d.str_scale}` : ''}. ` +
        'Filled because the deal had no positioning. Choose another tier to override it.',
    };
  }
  if (source === 'analyst') {
    return { kind: 'analyst', text: 'Analyst', title: 'Positioning chosen by the analyst (wizard or Overview).' };
  }
  if (!tier || tier.toLowerCase() === 'default') return null;
  return {
    kind: 'unrecorded',
    text: 'Source not recorded',
    title: 'This tier was stored before Fondok recorded where positioning came from.',
  };
}
