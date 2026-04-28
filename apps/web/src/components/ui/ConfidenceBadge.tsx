/**
 * ConfidenceBadge — slim 3-tier pill that surfaces extraction certainty.
 *
 *   ≥ 0.95 → green (high confidence, trust as-is)
 *   ≥ 0.85 → amber (analyst should glance at the source)
 *   < 0.85 → red   (review needed before relying on this value)
 *
 * Renders as a one-line dot + tabular percent, designed for dense
 * surfaces — variance rows, extracted-data side panels, memo
 * citations. Tone matches the brand palette in tailwind.config.ts.
 */

import { cn } from '@/lib/format';

export interface ConfidenceBadgeProps {
  /** 0–1 fractional confidence value. */
  value: number;
  className?: string;
}

export function ConfidenceBadge({ value, className }: ConfidenceBadgeProps) {
  // Clamp + normalize so out-of-range data still renders sanely.
  const clamped = Math.max(0, Math.min(1, value));
  const pct = Math.round(clamped * 100);
  const tone = clamped >= 0.95 ? 'green' : clamped >= 0.85 ? 'amber' : 'red';

  const styles: Record<typeof tone, { surface: string; dot: string }> = {
    green: {
      surface: 'bg-success-50 text-success-700 border-success-500/25',
      dot: 'bg-success-500',
    },
    amber: {
      surface: 'bg-warn-50 text-warn-700 border-warn-500/30',
      dot: 'bg-warn-500',
    },
    red: {
      surface: 'bg-danger-50 text-danger-700 border-danger-500/25',
      dot: 'bg-danger-500',
    },
  };

  return (
    <span
      title={`Extraction confidence: ${pct}%`}
      className={cn(
        'inline-flex items-center gap-1 px-1.5 py-0 rounded border',
        'text-[10.5px] font-semibold tabular-nums leading-[1.4]',
        styles[tone].surface,
        className,
      )}
    >
      <span className={cn('w-1 h-1 rounded-full', styles[tone].dot)} />
      {pct}%
    </span>
  );
}
