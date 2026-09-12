/**
 * fieldValue — the one place that decides whether an inline edit CHANGED
 * anything (FON-63 / FON-66 §1 / FON-65).
 *
 * Sam, 2026-09-11: *"clicking Save still causes Fondok to treat the value as an
 * analyst Override, even though the value itself was unchanged… simply opening
 * a field to inspect it should never change the model's data lineage."*
 *
 * Ten inline editors were written tab-by-tab and none of them compared the
 * draft to the value already on screen, so re-saving an untouched field minted
 * a `field_overrides` entry and flipped the Data Key dot to blue
 * "Analyst override". The comparison is not a plain `===`: the draft is a
 * human-typed string in display units ("6.80", "65", "$23,660,000") while the
 * stored value is the engine's number in persisted units (0.068, 0.65,
 * 23660000). Both sides have to be pushed into one canonical form first —
 * that is what `normalizeForCompare` does, and `isNoOpEdit` is the only
 * question the editors ask.
 *
 * Deliberately conservative: rounding is to each unit's PERSISTED precision
 * (cents for dollars, 1e-6 for fractions, whole numbers for months / years /
 * counts), never looser. A genuinely changed value must never be swallowed.
 */

/**
 * How a field's value is persisted — the unit the engine reads, not the unit
 * the analyst types.
 *
 * - `usd`           — dollars (23_660_000), compared to the cent.
 * - `pct_fraction`  — a rate stored as a fraction (0.068); a typed string is
 *                     read as whole percent ("6.80" → 0.068).
 * - `pct_whole`     — a percent stored whole (1.50 = 1.5%); both sides are
 *                     folded to a fraction before comparing.
 * - `ratio`         — a plain multiple / ratio (MOIC 2.47, DSCR 1.35) — no
 *                     percent conversion, 1e-6 precision.
 * - `months` / `years` / `count` — integers.
 * - `date`          — ISO `YYYY-MM-DD`.
 * - `text`          — a string (property name, note) compared after trimming.
 */
export type FieldUnit =
  | 'usd'
  | 'pct_fraction'
  | 'pct_whole'
  | 'ratio'
  | 'months'
  | 'years'
  | 'count'
  | 'date'
  | 'text';

/**
 * Round to a unit's persisted precision — `factor` is the reciprocal of the
 * step (100 for cents, 1e6 for a rate fraction). Multiplying by an integer
 * factor and dividing back is float-safe; dividing by the step first is not
 * (`Math.round(1.35 / 1e-6) * 1e-6` lands on 1.3499999999999999).
 */
function quantize(n: number, factor: number): number {
  return Math.round(n * factor) / factor;
}

/** `$ 1,234.50 %` → `1234.50`. Only ever applied to strings. */
function stripAffixes(raw: string): string {
  return raw.replace(/[$,%\s]/g, '');
}

function toNumber(raw: string | number): number | null {
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null;
  const cleaned = stripAffixes(raw);
  if (cleaned === '' || cleaned === '-' || cleaned === '.') return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

/**
 * Push a raw draft / stored value into the canonical comparable form for its
 * unit. `null` means "no value" — an empty draft, a missing assumption, an
 * unparseable number.
 *
 * The string-vs-number asymmetry on the percent units is deliberate and is
 * exactly how the UI behaves: an analyst types whole percent ("6.80"), the
 * deal stores a fraction (0.068). Both land on 0.068 here.
 */
export function normalizeForCompare(
  raw: string | number | null | undefined,
  unit: FieldUnit,
): number | string | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw === 'string' && raw.trim() === '') return null;

  switch (unit) {
    case 'text': {
      const s = String(raw).trim();
      return s === '' ? null : s;
    }
    case 'date': {
      const s = String(raw).trim();
      if (s === '') return null;
      const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
      return m ? `${m[1]}-${m[2]}-${m[3]}` : s;
    }
    case 'usd': {
      const n = toNumber(raw);
      return n === null ? null : quantize(n, 100);
    }
    case 'pct_fraction': {
      // Stored as a fraction; a typed string is whole percent.
      const n = toNumber(raw);
      if (n === null) return null;
      return quantize(typeof raw === 'string' ? n / 100 : n, 1e6);
    }
    case 'pct_whole': {
      // Stored whole (1.50 = 1.5%); fold both sides to a fraction.
      const n = toNumber(raw);
      return n === null ? null : quantize(n / 100, 1e6);
    }
    case 'ratio': {
      const n = toNumber(raw);
      return n === null ? null : quantize(n, 1e6);
    }
    case 'months':
    case 'years':
    case 'count': {
      const n = toNumber(raw);
      return n === null ? null : Math.round(n);
    }
    default: {
      const n = toNumber(raw);
      return n === null ? null : n;
    }
  }
}

/**
 * True when saving `next` over `current` would change nothing — the editor
 * must then exit WITHOUT a network call, so no override is minted and the
 * value keeps reporting the source it actually came from.
 *
 * Null-safe: two absent values are a no-op; an absent value replaced by a real
 * one (or cleared) is a real edit.
 */
export function isNoOpEdit(
  next: string | number | null | undefined,
  current: string | number | null | undefined,
  unit: FieldUnit,
): boolean {
  const a = normalizeForCompare(next, unit);
  const b = normalizeForCompare(current, unit);
  if (a === null || b === null) return a === b;
  return a === b;
}
