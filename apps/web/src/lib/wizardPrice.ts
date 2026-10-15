/**
 * FON-99 — the wizard's "Purchase / Indicative Price" field, parsed.
 *
 * The create-deal wizard collected a free-text price ("$120-140M") and never
 * sent it, so `deals.purchase_price` stayed NULL and the model ran on the seed
 * price badged "Seed". This module turns the analyst's text into the one
 * number the worker accepts (`CreateDealBody.purchase_price`, whole dollars)
 * and says exactly what it did, so the Review step and the field hint can
 * show it before anything is persisted.
 *
 * Accepted shapes (case-insensitive, `$` and whitespace optional):
 *   "$36.4M" · "36,400,000" · "36.4 mm" · "$1.2B" · "120k"
 *   "$120-140M" · "120–140M" · "$120M - $140M" · "120 to 140 M"   (a range)
 *
 * A range stores its MIDPOINT and is labelled "range midpoint" wherever the
 * number is shown — the analyst is never shown a midpoint as if it were the
 * quoted price. Text that does not read as a price is NOT guessed at: the
 * payload carries `null`, the raw text stays on Review as a note, and the
 * analyst sets the price on CAPEX → Deal Summary.
 *
 * A bare number with NO unit below $1,000,000 ("36", "36.4", "36400",
 * "1,200") is ambiguous — dollars? thousands? millions? — and a hotel never
 * trades for that little, so it is unparseable rather than persisted as
 * $36,400 (review finding). Likewise "2024-2025" / "2024–2025" is a period,
 * not a price range: a four-digit YYYY-YYYY pattern is refused outright
 * rather than stored as a $2,025 midpoint.
 */

export type ParsedPrice =
  | { kind: 'empty' }
  | { kind: 'single'; value: number; display: string }
  | { kind: 'range'; low: number; high: number; value: number; display: string; lowDisplay: string; highDisplay: string }
  | {
      kind: 'unparseable';
      raw: string;
      /** Why, when the text LOOKED numeric: a unit-less figure too small for
       *  a hotel price, or a YYYY-YYYY period. Absent for free text. */
      why?: 'too_small' | 'year_range';
    };

/** A unit-less figure below this is not a hotel purchase price. */
export const MIN_UNITLESS_DOLLARS = 1_000_000;

// "2024-2025", "2024 – 2025", "2024—2025": a period, never a price range.
const YEAR_RANGE = /^\d{4}\s*(?:-|–|—|\bto\b)\s*\d{4}$/i;

const UNIT: Record<string, number> = {
  k: 1e3,
  thousand: 1e3,
  m: 1e6,
  mm: 1e6,
  mn: 1e6,
  mil: 1e6,
  million: 1e6,
  b: 1e9,
  bn: 1e9,
  billion: 1e9,
};

// One amount: optional $, digits with optional thousands separators and
// decimals, optional unit word.
const AMOUNT = /^\$?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([a-z]+)?$/i;

interface Amount { value: number; unit: string | null }

function parseAmount(text: string): Amount | null {
  const t = text.trim().replace(/\s*usd$/i, '').trim();
  const m = AMOUNT.exec(t);
  if (!m) return null;
  const whole = m[1].replace(/,/g, '');
  const n = Number(`${whole}${m[2] ?? ''}`);
  if (!Number.isFinite(n) || n <= 0) return null;
  const unit = m[3] ? m[3].toLowerCase() : null;
  if (unit != null && !(unit in UNIT)) return null;
  return { value: n, unit };
}

/** Whole dollars, or `'too_small'` for a unit-less figure under $1M. */
function dollars(a: Amount, inheritedUnit: string | null): number | 'too_small' {
  const unit = a.unit ?? inheritedUnit;
  const scaled = unit ? a.value * UNIT[unit] : a.value;
  // A unit-less number below $1,000,000 is ambiguous ("36400" — thousands?
  // dollars?) and too small for a hotel — refuse rather than guess.
  if (!unit && scaled < MIN_UNITLESS_DOLLARS) return 'too_small';
  return Math.round(scaled);
}

/** Whole-dollar display, e.g. "$36,400,000". */
export function fmtWholeDollars(n: number): string {
  return `$${Math.round(n).toLocaleString('en-US')}`;
}

export function parseIndicativePrice(raw: string): ParsedPrice {
  const text = raw.trim();
  if (!text) return { kind: 'empty' };

  // "2024-2025" is a period (two four-digit years), not a price range.
  if (YEAR_RANGE.test(text)) return { kind: 'unparseable', raw: text, why: 'year_range' };

  // Range: "A - B", "A – B", "A — B", "A to B". The low side inherits the high
  // side's unit when it has none ("$120-140M" → 120M–140M).
  const range = /^(.+?)\s*(?:-|–|—|\bto\b)\s*(.+)$/i.exec(text);
  if (range) {
    const lo = parseAmount(range[1]);
    const hi = parseAmount(range[2]);
    if (lo && hi) {
      const hiDollars = dollars(hi, lo.unit);
      const loDollars = dollars(lo, hi.unit);
      if (loDollars === 'too_small' || hiDollars === 'too_small') {
        return { kind: 'unparseable', raw: text, why: 'too_small' };
      }
      const low = Math.min(loDollars, hiDollars);
      const high = Math.max(loDollars, hiDollars);
      const value = Math.round((low + high) / 2);
      return {
        kind: 'range', low, high, value,
        display: fmtWholeDollars(value),
        lowDisplay: fmtWholeDollars(low),
        highDisplay: fmtWholeDollars(high),
      };
    }
    return { kind: 'unparseable', raw: text };
  }

  const single = parseAmount(text);
  if (!single) return { kind: 'unparseable', raw: text };
  const value = dollars(single, null);
  if (value === 'too_small') return { kind: 'unparseable', raw: text, why: 'too_small' };
  return { kind: 'single', value, display: fmtWholeDollars(value) };
}

/** The number the create payload carries — `null` unless the text parsed. */
export function purchasePriceFromText(raw: string): number | null {
  const p = parseIndicativePrice(raw);
  return p.kind === 'single' || p.kind === 'range' ? p.value : null;
}

/** Label for the Review row: the parsed figure, how it was read, or the note. */
export function describeIndicativePrice(raw: string): string {
  const p = parseIndicativePrice(raw);
  switch (p.kind) {
    case 'empty':
      return '—';
    case 'single':
      return p.display;
    case 'range':
      return `${p.display} (range midpoint of ${raw.trim()})`;
    case 'unparseable':
      return `"${p.raw}" — not read as a price; set it on CAPEX → Deal Summary`;
  }
}

/** The one-line hint under the wizard field, or null when the field is blank. */
export function indicativePriceHint(raw: string): string | null {
  const p = parseIndicativePrice(raw);
  switch (p.kind) {
    case 'empty':
      return null;
    case 'single':
      return `Parsed as ${p.display} — seeds the model's purchase price.`;
    case 'range':
      return `Range ${p.lowDisplay}–${p.highDisplay} → the midpoint ${p.display} is stored as the purchase price (range midpoint). Edit it later on CAPEX → Deal Summary.`;
    case 'unparseable': {
      const lead =
        p.why === 'too_small'
          ? 'Could not read a price from this text: a figure under $1,000,000 with no unit is too small for a hotel price. Add M / MM ("36.4M") or type the full dollars ("36,400,000").'
          : p.why === 'year_range'
            ? 'Could not read a price from this text: two four-digit years read as a period, not a price range. Write the price with M / MM ("$120-140M") or in full dollars.'
            : 'Could not read a price from this text. Write it with M / MM ("$36.4M", "36.4MM") or in full dollars ("36,400,000").';
      return `${lead} The deal is created without one (the text is kept as a note on Review) — set the purchase price on CAPEX → Deal Summary.`;
    }
  }
}
