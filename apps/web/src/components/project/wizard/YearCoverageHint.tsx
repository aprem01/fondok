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
 * FON-26: distinguish *why* a year has no data. A bare green/gray split
 * left analysts guessing ("missing? failed? not uploaded? doesn't
 * exist?"). Each pill now carries one of four states with a legend:
 *   🟢 Uploaded   — an extracted financial doc covers the year
 *   🟡 Processing — a doc for the year is still extracting
 *   🔴 Failed     — a doc for the year failed extraction
 *   ⚪ Not uploaded — no doc for the year yet
 *
 * Note: processing/failed are only shown for years whose doc has already
 * been placed (e.g. a re-extraction of a known year). A doc that has
 * never extracted has no known year, so it can't be bucketed here — the
 * caller surfaces those as an aggregate ("N processing") separately.
 */

import { Check, Loader2, AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/format';

type YearState = 'uploaded' | 'processing' | 'failed' | 'missing';

export interface YearCoverageHintProps {
  /** Years covered by at least one extracted financial file. */
  coveredYears: ReadonlySet<number>;
  /** The full set of pills to show. Caller is responsible for ordering. */
  years: number[];
  /** Years whose doc is still extracting (FON-26). Optional. */
  processingYears?: ReadonlySet<number>;
  /** Years whose doc failed extraction (FON-26). Optional. */
  failedYears?: ReadonlySet<number>;
}

const STATE_STYLES: Record<YearState, string> = {
  uploaded: 'bg-success-50 text-success-700 border-success-500/30',
  processing: 'bg-brand-50 text-brand-700 border-brand-500/30',
  failed: 'bg-danger-50 text-danger-700 border-danger-500/30',
  missing: 'bg-ink-100 text-ink-500 border-ink-200',
};

const STATE_LABEL: Record<YearState, string> = {
  uploaded: 'Uploaded',
  processing: 'Processing',
  failed: 'Extraction failed',
  missing: 'Not uploaded',
};

function LegendDot({ state }: { state: YearState }) {
  return (
    <span className="inline-flex items-center gap-1 text-[10.5px] text-ink-500">
      <span
        className={cn(
          'inline-block w-2 h-2 rounded-full border',
          STATE_STYLES[state],
        )}
        aria-hidden="true"
      />
      {STATE_LABEL[state]}
    </span>
  );
}

export function YearCoverageHint({
  coveredYears,
  years,
  processingYears,
  failedYears,
}: YearCoverageHintProps) {
  const total = years.length;
  const covered = years.filter((y) => coveredYears.has(y)).length;

  const stateFor = (y: number): YearState => {
    if (coveredYears.has(y)) return 'uploaded';
    if (failedYears?.has(y)) return 'failed';
    if (processingYears?.has(y)) return 'processing';
    return 'missing';
  };

  // Only legend the states actually present so we don't imply states the
  // current data can't reach.
  const present = new Set<YearState>(years.map(stateFor));

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
          const state = stateFor(y);
          return (
            <span
              key={y}
              role="listitem"
              aria-label={`${y} ${STATE_LABEL[state].toLowerCase()}`}
              className={cn(
                'inline-flex items-center gap-1 px-2 py-0.5 rounded-md border',
                'text-[11.5px] font-medium tabular-nums whitespace-nowrap',
                'transition-colors motion-reduce:transition-none',
                STATE_STYLES[state],
              )}
            >
              {state === 'uploaded' && <Check size={10} aria-hidden="true" />}
              {state === 'processing' && (
                <Loader2 size={10} aria-hidden="true" className="animate-spin" />
              )}
              {state === 'failed' && (
                <AlertTriangle size={10} aria-hidden="true" />
              )}
              {y}
            </span>
          );
        })}
      </div>
      {/* Legend — only the states currently in play (FON-26). Skip it when
          everything is a plain covered/missing split so the wizard's
          first-run view stays uncluttered. */}
      {(present.has('processing') || present.has('failed')) && (
        <div className="flex items-center gap-3 flex-wrap mt-2 pt-2 border-t border-border">
          {(['uploaded', 'processing', 'failed', 'missing'] as YearState[])
            .filter((s) => present.has(s))
            .map((s) => (
              <LegendDot key={s} state={s} />
            ))}
        </div>
      )}
    </div>
  );
}
