/**
 * FON-41 / R-012 — City normalization for the create-deal wizard; Sam #6 —
 * Market / Submarket suggestions (bottom of the file).
 *
 * `US_HOTEL_MARKETS` is a NAME-ONLY list used for autocomplete and for
 * folding spelling variants ("washington dc", "Washington,DC") onto one
 * canonical label. It carries no performance data and is never shown as a
 * metric.
 *
 * Source: the U.S. lodging markets defined by STR (CoStar Group) and named
 * in STR's weekly / monthly "U.S. hotel performance" press releases
 * (str.com/press-releases; costar.com/products/str-benchmark). STR's
 * composite market names (e.g. "Washington, DC-MD-VA",
 * "Anaheim/Santa Ana, CA", "Tampa/St Petersburg, FL") are shortened here to
 * their principal city + state so they read the way analysts type a deal's
 * location; the STR name is kept as an alias where it differs so it still
 * matches. Order: STR's Top 25 Markets first, then other large STR-defined
 * markets alphabetically.
 */

export interface MarketEntry {
  /** Canonical label written to the deal. */
  name: string;
  /** Other spellings that fold onto `name` (STR composite names etc.). */
  aliases?: readonly string[];
}

export const US_HOTEL_MARKETS: readonly MarketEntry[] = [
  // STR Top 25 Markets
  { name: 'Anaheim, CA', aliases: ['Anaheim/Santa Ana, CA'] },
  { name: 'Atlanta, GA' },
  { name: 'Boston, MA' },
  { name: 'Chicago, IL' },
  { name: 'Dallas, TX' },
  { name: 'Denver, CO' },
  { name: 'Detroit, MI' },
  { name: 'Houston, TX' },
  { name: 'Las Vegas, NV' },
  { name: 'Los Angeles, CA', aliases: ['Los Angeles/Long Beach, CA'] },
  { name: 'Miami, FL', aliases: ['Miami/Hialeah, FL'] },
  { name: 'Minneapolis, MN', aliases: ['Minneapolis/St Paul, MN-WI'] },
  { name: 'Nashville, TN' },
  { name: 'New Orleans, LA' },
  { name: 'New York, NY', aliases: ['New York City', 'NYC', 'New York City, NY'] },
  { name: 'Norfolk, VA', aliases: ['Norfolk/Virginia Beach, VA'] },
  { name: 'Oahu, HI', aliases: ['Oahu Island, HI'] },
  { name: 'Orlando, FL' },
  { name: 'Philadelphia, PA', aliases: ['Philadelphia, PA-NJ'] },
  { name: 'Phoenix, AZ' },
  { name: 'St. Louis, MO', aliases: ['St Louis, MO-IL', 'St Louis, MO', 'Saint Louis, MO'] },
  { name: 'San Diego, CA' },
  { name: 'San Francisco, CA', aliases: ['San Francisco/San Mateo, CA'] },
  { name: 'Seattle, WA' },
  { name: 'Tampa, FL', aliases: ['Tampa/St Petersburg, FL'] },
  { name: 'Washington, DC', aliases: ['Washington, DC-MD-VA', 'Washington, D.C.'] },
  // Other large STR-defined U.S. markets
  { name: 'Albuquerque, NM' },
  { name: 'Austin, TX' },
  { name: 'Baltimore, MD' },
  { name: 'Birmingham, AL' },
  { name: 'Buffalo, NY' },
  { name: 'Charleston, SC' },
  { name: 'Charlotte, NC' },
  { name: 'Cincinnati, OH' },
  { name: 'Cleveland, OH' },
  { name: 'Columbus, OH' },
  { name: 'Fort Lauderdale, FL' },
  { name: 'Fort Myers, FL' },
  { name: 'Fort Worth, TX', aliases: ['Fort Worth/Arlington, TX'] },
  { name: 'Hartford, CT' },
  { name: 'Indianapolis, IN' },
  { name: 'Jacksonville, FL' },
  { name: 'Kansas City, MO' },
  { name: 'Louisville, KY' },
  { name: 'Maui, HI', aliases: ['Maui Island, HI'] },
  { name: 'Memphis, TN' },
  { name: 'Milwaukee, WI' },
  { name: 'Myrtle Beach, SC' },
  { name: 'Newark, NJ' },
  { name: 'Oakland, CA' },
  { name: 'Oklahoma City, OK' },
  { name: 'Omaha, NE' },
  { name: 'Pittsburgh, PA' },
  { name: 'Portland, OR' },
  { name: 'Raleigh, NC', aliases: ['Raleigh/Durham/Chapel Hill, NC'] },
  { name: 'Richmond, VA' },
  { name: 'Sacramento, CA' },
  { name: 'Salt Lake City, UT', aliases: ['Salt Lake City/Ogden, UT'] },
  { name: 'San Antonio, TX' },
  { name: 'San Jose, CA', aliases: ['San Jose/Santa Cruz, CA'] },
  { name: 'Savannah, GA' },
  { name: 'Tucson, AZ' },
];

/** Trim, collapse internal whitespace, and space commas canonically
 *  ("Washington ,DC" → "Washington, DC"). Never changes letters. */
export function tidyLocation(raw: string): string {
  return (raw ?? '')
    .replace(/\s+/g, ' ')
    .replace(/\s*,\s*/g, ', ')
    .trim()
    .replace(/^,\s*|,\s*$/g, '')
    .trim();
}

/** Comparison key: case-, punctuation- and whitespace-insensitive.
 *  "Washington, D.C." / "washington dc" / "Washington,DC" → "washington dc". */
export function locationKey(raw: string): string {
  return (raw ?? '')
    .toLowerCase()
    .replace(/\./g, '')
    .replace(/[,/\-]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/**
 * Normalize a typed City (or a fallback Market name) for saving.
 *
 * - Tidies whitespace and comma spacing.
 * - If the result matches a `US_HOTEL_MARKETS` name / alias, or an `extra`
 *   value (a city already used on one of the tenant's deals), case- and
 *   punctuation-insensitively, returns that entry's canonical spelling.
 * - Otherwise keeps the analyst's free text (tidied). Blank → ''.
 */
export function normalizeLocation(raw: string, extra: readonly string[] = []): string {
  const tidy = tidyLocation(raw);
  if (!tidy) return '';
  const key = locationKey(tidy);
  for (const m of US_HOTEL_MARKETS) {
    if (locationKey(m.name) === key) return m.name;
    if (m.aliases?.some((a) => locationKey(a) === key)) return m.name;
  }
  for (const e of extra) {
    const t = tidyLocation(e);
    if (t && locationKey(t) === key) return t;
  }
  return tidy;
}

/**
 * Autocomplete options: the tenant's existing deal cities first (folded onto
 * a market name when they match one, deduped), then the static market list.
 */
export function locationSuggestions(tenantCities: readonly (string | null | undefined)[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  const push = (v: string) => {
    const k = locationKey(v);
    if (!k || seen.has(k)) return;
    seen.add(k);
    out.push(v);
  };
  for (const c of tenantCities) {
    if (typeof c === 'string' && c.trim()) push(normalizeLocation(c));
  }
  for (const m of US_HOTEL_MARKETS) push(m.name);
  return out;
}

// ─────────────────────────────────────────────────────────────────────────
// Sam #6 / FON-96 — Market / Submarket (separate from City).
//
// When the market / submarket taxonomy list is available to this workspace
// (`GET /reference/str-markets` returns rows — the CoStar geography list,
// served to a workspace only under its licence), the Market field suggests
// CoStar's markets and a pick carries the market's taxonomy key; Submarket
// suggests CoStar's submarkets, each with its market so two submarkets
// sharing a name stay distinguishable, and a pick carries the submarket key.
// The keys are Fondok's own (`costar:<slug>` / `costar:<slug>/<slug>`) — the
// list has no vendor ids — and are never shown. When the list is withheld
// (not licensed for this workspace, worker down, older worker), the Market
// field falls back to the public name-only `US_HOTEL_MARKETS` list above (no
// key — never invented) and Submarket has no suggestions. Typed values are
// always allowed and are stored with null keys as analyst-entered.
// ─────────────────────────────────────────────────────────────────────────

/** Mirrors `StrMarketRecord` in lib/api.ts (kept structural so this module
 *  stays free of the API client). The FON-96 fields are optional — an older
 *  worker omits them. */
export interface StrMarketRow {
  str_market_id: string;
  market_name: string;
  submarket_id: string | null;
  submarket_name: string | null;
  state: string | null;
  country: string | null;
  market_type?: string | null;
  market_key?: string;
  submarket_key?: string | null;
  label?: string;
}

export interface MarketOption {
  /** The value written to `deals.market`. */
  name: string;
  /** The market's taxonomy key — only for an option from the loaded list. */
  strMarketId: string | null;
  /** Secondary text shown beside the option — never the key. */
  detail: string;
}

export interface SubmarketOption {
  /** The datalist option value: the submarket name when a market is picked,
   *  else "{submarket} · {market}" so same-named submarkets differ. */
  value: string;
  /** Secondary text shown beside the option — never the key. */
  label: string;
  name: string;
  market: string;
  submarketKey: string;
  marketKey: string;
}

/** What the Market suggestions are, in words the analyst sees. */
export const MARKET_SOURCE_LABEL = {
  reference: 'Suggestions: CoStar market list',
  fallback: 'Suggestions: STR market names (public list — the CoStar market list is not available to this workspace)',
} as const;

/** The provenance line under a saved market / submarket. */
export const MARKET_PROVENANCE_LABEL = {
  market: 'CoStar market',
  submarketOf: (market: string) => `CoStar submarket of ${market}`,
  analyst: 'Analyst-entered · not in the CoStar list',
  analystNoList: 'Analyst-entered · saved as typed (the CoStar market list is not available to this workspace)',
  fromOm: 'matched from the Offering Memorandum',
  notSet: 'Not set',
} as const;

/** Comparison key for a market name: `locationKey` plus a trailing "usa"
 *  token dropped, so "Chicago - IL USA" (CoStar's spelling) and "Chicago, IL"
 *  (how analysts type it) compare equal. Same shape as the worker's
 *  `market_name_key`. */
export function marketNameKey(raw: string): string {
  const k = locationKey(raw);
  return k.endsWith(' usa') ? k.slice(0, -4).trim() : k;
}

function marketTypeLabel(row: StrMarketRow): string {
  if (row.market_type === 'metro') return 'Metro market';
  if (row.market_type === 'regional') return 'Regional market';
  return 'CoStar market';
}

/**
 * Market suggestions. From the reference rows when there are any (one option
 * per market key), else the static name-only list (`strMarketId` null).
 */
export function marketOptions(refRows: readonly StrMarketRow[]): MarketOption[] {
  if (refRows.length > 0) {
    const seen = new Set<string>();
    const out: MarketOption[] = [];
    for (const r of refRows) {
      if (seen.has(r.str_market_id)) continue;
      seen.add(r.str_market_id);
      const where = [r.state, r.country].filter(Boolean).join(', ');
      out.push({
        name: r.market_name,
        strMarketId: r.str_market_id,
        detail: `${marketTypeLabel(r)}${where ? ` · ${where}` : ''}`,
      });
    }
    return out;
  }
  return US_HOTEL_MARKETS.map((m) => ({ name: m.name, strMarketId: null, detail: 'STR market name · public list' }));
}

/**
 * Resolve a typed / picked Market against the reference rows: an exact name
 * match (case- and punctuation-insensitive, "Chicago, IL" ≡ "Chicago - IL
 * USA") to exactly ONE market key carries that key and the list's spelling;
 * anything else keeps the analyst's text (folded onto a known spelling when
 * no list is loaded) with no key. Two markets sharing the name → no key
 * (ambiguous — never guessed).
 */
export function resolveMarket(
  typed: string,
  refRows: readonly StrMarketRow[],
): { market: string; strMarketId: string | null } {
  const tidy = tidyLocation(typed);
  if (!tidy) return { market: '', strMarketId: null };
  const key = marketNameKey(tidy);
  const ids = new Map<string, string>();
  for (const r of refRows) {
    if (marketNameKey(r.market_name) === key) ids.set(r.str_market_id, r.market_name);
  }
  if (ids.size === 1) {
    const [[id, name]] = Array.from(ids.entries());
    return { market: name, strMarketId: id };
  }
  return { market: refRows.length > 0 ? tidy : normalizeLocation(tidy), strMarketId: null };
}

/**
 * Submarket suggestions — ONLY from the reference rows (there is no fallback
 * list: Fondok has no submarket names of its own). Narrowed to the picked
 * market when there is one; otherwise each option is "{submarket} · {market}"
 * so Arlington (TX) and Arlington (VA) stay distinguishable.
 */
export function submarketOptions(refRows: readonly StrMarketRow[], strMarketId: string | null): SubmarketOption[] {
  const seen = new Set<string>();
  const out: SubmarketOption[] = [];
  for (const r of refRows) {
    if (!r.submarket_name || !r.submarket_id) continue;
    if (strMarketId && r.str_market_id !== strMarketId) continue;
    if (seen.has(r.submarket_id)) continue;
    seen.add(r.submarket_id);
    const label = `${r.submarket_name} · ${r.market_name}`;
    out.push({
      value: strMarketId ? r.submarket_name : label,
      label: strMarketId ? MARKET_PROVENANCE_LABEL.submarketOf(r.market_name) : label,
      name: r.submarket_name,
      market: r.market_name,
      submarketKey: r.submarket_id,
      marketKey: r.str_market_id,
    });
  }
  return out;
}

export interface SubmarketPick {
  /** The value written to `deals.submarket` ('' when blank). */
  submarket: string;
  /** The submarket key — only on an exact, unique match. */
  submarketKey: string | null;
  /** The matched submarket's market (key + name) — lets a pick made with no
   *  market selected set the market too. Null when nothing matched. */
  marketKey: string | null;
  market: string | null;
}

/**
 * Resolve a typed / picked Submarket with the same discipline as
 * `resolveMarket`: an exact name match to exactly ONE submarket — within the
 * picked market when there is one, or within the market named after " · "
 * when the analyst picked a "{submarket} · {market}" option — carries the
 * key and the list's spelling; anything else keeps the text (the submarket
 * part only) with no key. Two candidates → no key, never guessed.
 */
export function resolveSubmarket(
  typed: string,
  refRows: readonly StrMarketRow[],
  marketKey: string | null,
): SubmarketPick {
  const tidy = tidyLocation(typed);
  if (!tidy) return { submarket: '', submarketKey: null, marketKey: null, market: null };
  let subText = tidy;
  let marketText: string | null = null;
  const sep = tidy.lastIndexOf(' · ');
  if (sep > 0) {
    subText = tidy.slice(0, sep).trim();
    marketText = tidy.slice(sep + 3).trim() || null;
  }
  const key = locationKey(subText);
  const hits = new Map<string, StrMarketRow>();
  for (const r of refRows) {
    if (!r.submarket_name || !r.submarket_id) continue;
    if (marketKey && r.str_market_id !== marketKey) continue;
    if (marketText && marketNameKey(r.market_name) !== marketNameKey(marketText)) continue;
    if (locationKey(r.submarket_name) === key) hits.set(r.submarket_id, r);
  }
  if (hits.size === 1) {
    const r = Array.from(hits.values())[0];
    return { submarket: r.submarket_name ?? subText, submarketKey: r.submarket_id, marketKey: r.str_market_id, market: r.market_name };
  }
  return { submarket: subText, submarketKey: null, marketKey: null, market: null };
}
