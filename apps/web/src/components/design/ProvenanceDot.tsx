/**
 * ProvenanceDot / StateBadge — the per-value 6-state origin indicator.
 *
 * Source: `design/canonical/Fondok Data Key.dc.html` (the `tokens()` strip
 * dots) — the same 9px dot rendered in the Data Key strip and, at 8px, beside
 * values in Cash Flow / Investment rows. Keyed off the canonical
 * `ValueState` union from `@/lib/api` so one component covers every engine.
 *
 * Origin renderings (exact, from the design):
 *   document_sourced → filled green
 *   linked           → hollow green (white fill, 2px green ring)
 *   assumption       → filled blue
 *   calculated       → filled grey
 *   awaiting_data    → hollow dashed grey
 *   needs_review     → green origin + amber halo ring
 *
 * `needs_review` is really a MODIFIER halo that rides on any origin ("flags
 * stack, never replace"). Pass `review` to overlay the amber ring on a
 * different origin dot; when `state === 'needs_review'` alone, the canonical
 * green-origin + ring is drawn (as the strip and tabs show it).
 *
 * Presentational only — no data fetching.
 */

import type { CSSProperties } from 'react';
import type { ValueState } from '@/lib/api';
import { provDot, prov } from './tokens';

export interface ProvenanceDotProps {
  state: ValueState;
  /** Overlay the amber needs-review halo on top of the origin dot. */
  review?: boolean;
  /** Dot diameter in px. Canonical: 9 (strip / legend), 8 (row values). */
  size?: number;
  className?: string;
  style?: CSSProperties;
  title?: string;
}

export function ProvenanceDot({
  state,
  review = false,
  size = 9,
  className,
  style,
  title,
}: ProvenanceDotProps) {
  const spec = provDot[state];
  const ring = review && state !== 'needs_review' ? prov.reviewRing : spec.ring;
  return (
    <span
      role="img"
      aria-label={spec.label}
      title={title ?? spec.label}
      className={className}
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: spec.fill,
        border: spec.border === 'none' ? undefined : spec.border,
        boxShadow: ring === 'none' ? undefined : ring,
        display: 'inline-block',
        flexShrink: 0,
        ...style,
      }}
    />
  );
}

export interface StateBadgeProps {
  state: ValueState;
  /** Overlay the amber needs-review halo. */
  review?: boolean;
  /** Override the canonical label. */
  label?: string;
  className?: string;
  style?: CSSProperties;
}

/** Dot + canonical label, as rendered inside the Data Key strip / help popover. */
export function StateBadge({ state, review = false, label, className, style }: StateBadgeProps) {
  return (
    <span
      className={className}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        fontSize: 11,
        color: '#6b6f76',
        ...style,
      }}
    >
      <ProvenanceDot state={state} review={review} />
      {label ?? provDot[state].label}
    </span>
  );
}
