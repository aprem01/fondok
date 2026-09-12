/**
 * provenance.ts — shared source-of-truth classification + formatting.
 *
 * One place to answer "where did this number come from, and how do I show
 * it?" — used by the Provenance Ledger, the <Sourced> hover primitive, and
 * (rolling out) every screen. Kinds map to the SourcedValue color system:
 *   grounded  🟢  from THIS deal's documents / entry
 *   benchmark 🟡  a market / seed default — NOT this deal's data
 *   override  🟣  analyst-set
 *
 * Phase 1.4 — the vocabulary (which labels exist, what each is called, what
 * each means) is the GENERATED registry ``SOURCES``; the 3-colour mapping and
 * the wording the web shows stay here. Every disagreement between the two is
 * pinned below and recorded in ``lib/ontology/DRIFT_NOTES.web.md``.
 */

import {
  SOURCE_IDS,
  SOURCE_LABEL_FROM_REGISTRY,
  SOURCE_EXPLANATION_FROM_REGISTRY,
  SOURCE_REGISTRY_KIND,
} from '@/lib/ontology/adapters';

export type SourceKind = 'grounded' | 'benchmark' | 'override';

/**
 * Registry kind → the web's 3-colour kind.
 *
 * ``calculated`` maps to ``override``: FON-69's ``derived_from_revpar_growth``
 * is a formula over an analyst override, so it reads as analyst intent (blue)
 * — the registry records it as `calculated` and explicitly leaves the colour
 * to the UI (worker DRIFT_NOTES.md §3.10). ``refusal`` maps to ``benchmark``
 * because a refused STR seed falls back to a T-12/seed value.
 */
const KIND_FROM_REGISTRY: Record<string, SourceKind> = {
  grounded: 'grounded',
  override: 'override',
  calculated: 'override',
  assumption: 'benchmark',
  refusal: 'benchmark',
};

/**
 * DRIFT PIN — the six labels the web never listed. They have always fallen
 * through ``sourceKind`` to ``benchmark``; the registry would put ``pip_om``
 * and ``partnership_doc`` on green (grounded) and ``pip_user`` / ``roi_user``
 * on override. Phase 1.4 changes no colours and no Provenance Ledger counts,
 * so the OLD classification wins and flipping them stays a product decision —
 * delete an id from this set to adopt the registry's. Their LABEL and BADGE
 * do widen (see SOURCE_LABEL). DRIFT_NOTES.web.md → "kind disagreements".
 */
const LEGACY_UNCLASSIFIED = new Set<string>([
  'str_segmentation_default', 'pip_om', 'pip_user',
  'capex_ffe_default', 'roi_user', 'partnership_doc',
]);

const kindOf = (id: string): SourceKind | null =>
  LEGACY_UNCLASSIFIED.has(id) ? null : KIND_FROM_REGISTRY[SOURCE_REGISTRY_KIND[id]] ?? null;

const GROUNDED_SOURCES = new Set<string>(SOURCE_IDS.filter((id) => kindOf(id) === 'grounded'));
// FON-69 — ``adr_growth`` derived by the worker from an analyst RevPAR-growth
// override is analyst intent, so it reads as an input / assumption (blue).
const OVERRIDE_SOURCES = new Set<string>(SOURCE_IDS.filter((id) => kindOf(id) === 'override'));

/**
 * FON-61 (D4) — the EXACT note the Market tab writes on the explicit
 * ``starting_occupancy`` / ``starting_adr`` field_overrides when "Use STR
 * rates in the model" is clicked. The worker recognizes this note and tags
 * those keys ``str_forecast`` (STR data) rather than a generic analyst
 * override. Mirrors ``STR_MARKET_OVERRIDE_NOTE`` in
 * apps/worker/app/services/engine_runner.py — keep the string identical.
 */
export const STR_MARKET_OVERRIDE_NOTE = 'STR comp-set market rates (Market tab)';

/** True when a ``field_overrides`` entry is the Market tab's STR comp-set
 *  seed (a structured ``{value, note}`` record carrying the exact note). */
export function isStrMarketOverride(entry: unknown): boolean {
  return (
    typeof entry === 'object' &&
    entry !== null &&
    (entry as { note?: unknown }).note === STR_MARKET_OVERRIDE_NOTE
  );
}

export function sourceKind(source: string): SourceKind {
  if (OVERRIDE_SOURCES.has(source)) return 'override';
  if (GROUNDED_SOURCES.has(source)) return 'grounded';
  return 'benchmark'; // seed / cbre_horizons / pnl_benchmark / *_default
}

/**
 * Long source labels — straight from the registry (``SOURCES[id].label``).
 *
 * All 12 labels the web hand-maintained match the registry byte-for-byte, so
 * this is a pure widening: the six labels the worker emits that the web never
 * knew (``str_segmentation_default``, ``pip_om``, ``pip_user``,
 * ``capex_ffe_default``, ``roi_user``, ``partnership_doc``) now read as
 * themselves instead of falling through to the underscore-stripped id.
 */
export const SOURCE_LABEL: Record<string, string> = { ...SOURCE_LABEL_FROM_REGISTRY };

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source.replace(/_/g, ' ');
}

/**
 * DRIFT PIN — hover copy the web has shipped, kept verbatim.
 *
 * The registry carries a longer, worker-oriented explanation for most of
 * these (it appends the growth-forward rule to ``t12_actual``, "Wins over
 * every other source" to ``analyst_override``, and so on). Phase 1.4 changes
 * no copy Sam sees, so where the two disagree the WEB text wins and the
 * registry's is recorded in DRIFT_NOTES.web.md. ``seed`` and
 * ``str_forecast_unavailable`` already match the registry exactly and are
 * left to fall through.
 */
const WEB_EXPLANATION: Record<string, string> = {
  t12_actual: 'Extracted from the deal’s T-12 actuals.',
  deal_row: 'Entered on the deal record.',
  om_comps: 'From the offering memorandum’s comparable set.',
  om_broker: 'From the broker’s pro forma in the OM.',
  portfolio_pnl: 'From your portfolio P&L library.',
  str_forecast: 'From the STR / comp-set forecast.',
  derived_from_revpar_growth:
    'Derived from the analyst’s RevPAR-growth override: ADR growth = (1 + RevPAR growth) ÷ (1 + occupancy growth) − 1, with the occupancy path held.',
  cbre_horizons: 'CBRE Horizons market benchmark — not this deal’s own data.',
  pnl_benchmark: 'Industry (USALI/HOST) benchmark — not this deal’s own data.',
  analyst_override: 'Set by an analyst with a justification note.',
};

/** One-line, human explanation of what a source means — the hover body.
 *  Web copy first, then the registry's (which covers the six labels the web
 *  never had), then the seed default for anything unknown. */
export function sourceExplanation(source: string): string {
  return (
    WEB_EXPLANATION[source] ??
    SOURCE_EXPLANATION_FROM_REGISTRY[source] ??
    SOURCE_EXPLANATION_FROM_REGISTRY.seed
  );
}

// Design-mockup taxonomy: green = linked / extracted (this deal's data),
// blue = user input / assumption (seed defaults + analyst overrides),
// gray = calculated. "Needs review" (red) is a per-value flag, not a
// SourceKind. Benchmark + override both read as blue "input / assumption".
export const KIND_TONE: Record<SourceKind, { text: string; bg: string; dot: string }> = {
  grounded: { text: 'text-success-700', bg: 'bg-success-50', dot: 'bg-success-500' },
  benchmark: { text: 'text-blue-700', bg: 'bg-blue-50', dot: 'bg-blue-500' },
  override: { text: 'text-blue-700', bg: 'bg-blue-50', dot: 'bg-blue-500' },
};

/** Humanize an assumption key: strip the USALI prefix, expand _pct/_usd,
 *  title-case, and keep known acronyms uppercased. */
const ACRONYMS = new Set(['adr', 'noi', 'revpar', 'irr', 'ltv', 'dscr', 'gp', 'lp', 'fb', 'coc', 'ffe', 'str', 'om', 'usali', 'ytd', 'ttm', 'pip']);
export function humanizeAssumptionKey(k: string): string {
  const base = k.replace(/^p_and_l_usali\./, '').replace(/^property_overview\./, '');
  return base
    .replace(/_pct$/, '_%')
    .replace(/_usd$/, '')
    .split(/[._]/)
    .filter(Boolean)
    .map((s) =>
      s === '%'
        ? '%'
        : ACRONYMS.has(s.toLowerCase())
          ? s.toUpperCase()
          : s.charAt(0).toUpperCase() + s.slice(1),
    )
    .join(' ');
}

// Keys whose values are ratios stored as fractions (0.65 → 65%).
const RATIO_KEY = /(_pct|growth|occupancy|cap_rate|ltv|rate|ratio|margin|_pc$|equity_)/;
// Keys that are plain counts / labels — never currency.
const COUNT_KEY = /(keys|year|years|outlets|spaces|_sf$|space_sf|count|per_occupied|per_room|_ratio$)/;

/** Format an assumption value for display, inferring % vs $ vs count. */
export function formatAssumptionValue(
  key: string,
  v: number | string | boolean | null | undefined,
): string {
  if (v == null) return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'string') return v;
  const k = key.toLowerCase();

  if (RATIO_KEY.test(k) && !COUNT_KEY.test(k)) {
    const pct = Math.abs(v) <= 1 ? v * 100 : v;
    return `${pct.toFixed(1)}%`;
  }
  if (COUNT_KEY.test(k)) {
    // counts / SF / per-room ratios: no currency sign, tidy decimals
    return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }
  // currency-ish
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `$${Math.round(v / 1_000)}K`;
  if (abs >= 100) return `$${Math.round(v)}`;
  if (Number.isInteger(v)) return String(v);
  return v.toFixed(2);
}

// ───────────────── Period basis (FON-41 #4 / FON-61 §2) ─────────────────
//
// Sam: provenance must identify the source PERIOD/BASIS as well as the source
// document — "T-12 ending Mar 31, 2025", "YTD through Mar 31, 2025", "FY2024".
// Both halves of the app format it here: the worksheet passes the column's
// resolved ``HistYear.periodBasis``, the ledger passes the worker's own
// ``scope`` off ``__source_fields__``. Nothing is invented — a basis or a
// date we were not given simply does not appear.

/** The worker's ``Scope`` vocabulary (``registry.Scope``) plus the web's
 *  ``PeriodBasis`` spellings, so either side can call this. */
const SCOPE_ALIASES: Record<string, 'annual' | 'ttm' | 'ytd' | 'quarterly' | 'monthly'> = {
  annual: 'annual', fy: 'annual', FY: 'annual',
  ttm: 'ttm', t12: 'ttm', T12: 'ttm',
  ytd: 'ytd', YTD: 'ytd',
  quarterly: 'quarterly',
  monthly: 'monthly', MONTHLY: 'monthly',
};

const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "Mar 31, 2025" from an ISO date. ``null`` when it is not one. */
export function formatAsOfDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = /^((?:19|20)\d{2})-(\d{2})-(\d{2})/.exec(iso.trim());
  if (!m) return null;
  const mon = MONTHS_SHORT[Number(m[2]) - 1];
  if (!mon) return null;
  return `${mon} ${Number(m[3])}, ${m[1]}`;
}

/**
 * "T-12 ending Mar 31, 2025" / "YTD through Mar 31, 2025" / "FY2024".
 * Returns ``null`` when the basis is unknown/absent — the caller then shows
 * nothing rather than a guess.
 */
export function formatPeriodBasis(
  scope: string | null | undefined,
  asOf: string | null | undefined,
): string | null {
  const s = scope ? SCOPE_ALIASES[scope] ?? SCOPE_ALIASES[String(scope).toLowerCase()] : undefined;
  if (!s) return null;
  const when = formatAsOfDate(asOf);
  if (s === 'annual') {
    const yr = asOf ? /^((?:19|20)\d{2})/.exec(String(asOf).trim())?.[1] : null;
    return yr ? `FY${yr}` : 'Full year';
  }
  if (s === 'ttm') return when ? `T-12 ending ${when}` : 'Trailing 12 months';
  if (s === 'ytd') return when ? `YTD through ${when}` : 'Year to date';
  if (s === 'quarterly') return when ? `Quarter ending ${when}` : 'Quarterly';
  return when ? `Month ending ${when}` : 'Single month';
}
