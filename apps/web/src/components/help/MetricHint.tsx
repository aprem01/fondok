'use client';

/**
 * MetricHint — quiet `Info` icon you drop next to any metric label.
 *
 * On hover/focus, the new `Tooltip` primitive surfaces a 1-sentence
 * definition + `Learn more →` link into the methodology page anchor.
 *
 * Definitions live in `apps/web/src/lib/glossary.ts` (`GLOSSARY_DEFINITIONS`).
 * Pass `metricId` and we look it up; or pass `definition` + optional
 * `learnMoreHref` directly for one-off metrics that don't deserve a
 * dictionary entry.
 *
 * Bar: institutional, silent until invited. Default opacity is 50% so it
 * never competes with the metric value for attention.
 *
 *   <MetricHint metricId="levered_irr" />
 *   <MetricHint definition="Median ADR across the comp set." />
 */

import { Info } from 'lucide-react';
import { Tooltip } from './Tooltip';
import { GLOSSARY_DEFINITIONS } from '@/lib/glossary';
import { cn } from '@/lib/format';

export interface MetricHintProps {
  metricId?: string;
  definition?: string;
  learnMoreHref?: string;
  /** Default 11. Slightly larger (12) reads as "more clickable" — useful
   *  next to bigger headlines. */
  size?: number;
  className?: string;
  /** Optional aria label override; default uses the metric id or "What is this?" */
  ariaLabel?: string;
}

export function MetricHint({
  metricId,
  definition,
  learnMoreHref,
  size = 11,
  className,
  ariaLabel,
}: MetricHintProps) {
  const dict = metricId ? GLOSSARY_DEFINITIONS[metricId] : undefined;
  const resolvedDefinition = definition ?? dict?.definition;
  const resolvedHref = learnMoreHref ?? dict?.learnMoreAnchor;

  if (!resolvedDefinition) return null;

  return (
    <Tooltip
      content={<span>{resolvedDefinition}</span>}
      learnMoreHref={resolvedHref}
      side="top"
      align="center"
    >
      <button
        type="button"
        aria-label={ariaLabel ?? `What is ${metricId ?? 'this metric'}?`}
        className={cn(
          'inline-flex items-center justify-center text-ink-400 opacity-50 hover:opacity-100 hover:text-ink-700 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded',
          className,
        )}
      >
        <Info size={size} aria-hidden="true" />
      </button>
    </Tooltip>
  );
}

export default MetricHint;
