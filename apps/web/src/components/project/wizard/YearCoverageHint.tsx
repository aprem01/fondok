'use client';

/**
 * YearCoverageHint — horizontal year-pill row sitting above the
 * "Financials by year" wizard sub-stage.
 *
 * Sam's framing (June 25 2026): "Maybe before you add any files, let's
 * address the most important ones, which I would argue are the
 * financials, broken down by each year." Eshan's reinforcement: "It's
 * kind of dashboard, says done, done, done — financial document, three
 * years got it. Then they know what's missing."
 *
 * Visual idiom: gray pill = needed (no financial yet), green pill =
 * covered. Default range is the current year minus four (5 calendar
 * years, chronological left→right). Adding a year via "+ Different
 * year" injects a custom pill ahead of the default range so the
 * coverage line stays sorted.
 */

import { Check } from 'lucide-react';
import { cn } from '@/lib/format';

export interface YearCoverageHintProps {
  /** Years currently covered by at least one financial file the user has dropped. */
  coveredYears: ReadonlySet<number>;
  /** The full set of pills to show. Caller is responsible for ordering. */
  years: number[];
}

export function YearCoverageHint({ coveredYears, years }: YearCoverageHintProps) {
  const total = years.length;
  const covered = years.filter((y) => coveredYears.has(y)).length;
  return (
    <div
      className="rounded-md bg-bg border border-border px-3 py-2.5"
      role="region"
      aria-label="Financial-year coverage"
    >
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] uppercase tracking-wider text-ink-500 font-semibold">
          Year coverage
        </div>
        <div className="text-[11px] text-ink-700 tabular-nums">
          <span className="font-semibold text-ink-900">{covered}</span>
          <span className="text-ink-500"> / {total} years</span>
        </div>
      </div>
      <div
        className="flex items-center gap-1.5 flex-wrap"
        role="list"
        aria-label="Years"
      >
        {years.map((y) => {
          const done = coveredYears.has(y);
          return (
            <span
              key={y}
              role="listitem"
              aria-label={`${y} ${done ? 'covered' : 'needed'}`}
              className={cn(
                'inline-flex items-center gap-1 px-2 py-0.5 rounded-md border',
                'text-[11.5px] font-medium tabular-nums whitespace-nowrap',
                'transition-colors motion-reduce:transition-none',
                done
                  ? 'bg-success-50 text-success-700 border-success-500/30'
                  : 'bg-ink-100 text-ink-500 border-ink-200',
              )}
            >
              {done && <Check size={10} aria-hidden="true" />}
              {y}
            </span>
          );
        })}
      </div>
    </div>
  );
}
