/**
 * FON-41 / R-012 — City / Submarket normalization for the create-deal wizard.
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
 * Normalize a typed City / Submarket for saving.
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
