/**
 * FON-121 — model years are DEAL years: twelve months from the acquisition
 * close (worker `engines/deal_years.py`). A 2025-09-30 close puts Year 1 on
 * Oct 2025–Sep 2026. The worker publishes every span
 * (`revenue.projection_deal_years`, `revenue.exit_noi_deal_year`, and a
 * `deal_year` on every topline / growth / benchmark record); this module only
 * reads and words them — it never derives a calendar of its own. No close
 * date → no spans → the ordinal alone ("Year 1"), never a guessed year.
 */

export interface DealYearCalendarMonths {
  calendar_year: number;
  months: number;
}

export interface DealYearSpan {
  year: number;
  /** ISO first day of the first month. */
  start: string;
  /** ISO last day of the last month. */
  end: string;
  /** "Oct 2025–Sep 2026" / "Jan–Dec 2026". */
  label: string;
  /** The calendar years the deal year spans and its months in each. */
  calendar_months?: DealYearCalendarMonths[];
  days: number;
}

function isSpan(v: unknown): v is DealYearSpan {
  return !!v && typeof v === 'object' && typeof (v as DealYearSpan).label === 'string' && (v as DealYearSpan).label !== '';
}

/** The published spans (`revenue.projection_deal_years`), or [] (none / an older run). */
export function asDealYears(v: unknown): DealYearSpan[] {
  return Array.isArray(v) ? v.filter(isSpan) : [];
}

/** The span label of model year `year` (1-based), or null. */
export function dealYearLabel(spans: readonly DealYearSpan[] | null | undefined, year: number): string | null {
  if (!spans || !Number.isInteger(year) || year < 1 || year > spans.length) return null;
  const s = spans[year - 1];
  return isSpan(s) ? s.label : null;
}

/** "Year 3 (Oct 2027–Sep 2028)" — or "Year 3" without a span. */
export function yearWithSpan(year: number, span: DealYearSpan | string | null | undefined): string {
  const label = typeof span === 'string' ? span : span?.label;
  return label ? `Year ${year} (${label})` : `Year ${year}`;
}

/** "3 mo of 2026 + 9 mo of 2027" — the calendar years a deal year blends, or null for one calendar year. */
export function calendarMixText(span: DealYearSpan | null | undefined): string | null {
  const parts = span?.calendar_months ?? [];
  if (parts.length < 2) return null;
  return parts.map((p) => `${p.months} mo of ${p.calendar_year}`).join(' + ');
}
