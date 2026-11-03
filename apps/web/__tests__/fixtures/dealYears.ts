/**
 * FON-121 test helper — Jan–Dec deal years (a 31 Dec close), so a test
 * written on calendar years keeps its meaning: `calDealYears([2025, 2026])`.
 */
import type { DealYearSpan } from '@/lib/dealYears';

export function calDealYears(years: readonly number[] | null | undefined): DealYearSpan[] {
  return (years ?? []).map((cal, i) => ({
    year: i + 1,
    start: `${cal}-01-01`,
    end: `${cal}-12-31`,
    label: `Jan–Dec ${cal}`,
    calendar_months: [{ calendar_year: cal, months: 12 }],
    days: (cal % 4 === 0 && cal % 100 !== 0) || cal % 400 === 0 ? 366 : 365,
  }));
}

/** The exit-NOI deal year after `years` (the year after the hold), or null. */
export function calExitYear(years: readonly number[] | null | undefined): DealYearSpan | null {
  const ys = years ?? [];
  if (!ys.length) return null;
  const [span] = calDealYears([ys[ys.length - 1] + 1]);
  return { ...span, year: ys.length + 1 };
}
