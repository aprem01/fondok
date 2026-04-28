'use client';
import { useState } from 'react';
import { HelpCircle } from 'lucide-react';
import { cn } from '@/lib/format';

/**
 * Metric label with a small help icon that reveals a plain-English
 * definition on hover/click. Used for KPI labels, column headers, and
 * any other dense jargon.
 *
 * Set `eyebrow` to apply the codebase's small-caps eyebrow styling so the
 * label can drop into an existing card header without restyling.
 */
export function MetricLabel({
  label,
  tip,
  className,
  eyebrow = false,
  iconSize = 11,
}: {
  label: string;
  tip: string;
  className?: string;
  eyebrow?: boolean;
  iconSize?: number;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className={cn('inline-flex items-center gap-1 relative', className)}>
      <span className={cn(eyebrow && 'eyebrow')}>{label}</span>
      <button
        type="button"
        aria-label={`What is ${label}?`}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((o) => !o);
        }}
        className="text-ink-400 hover:text-ink-700 inline-flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded"
      >
        <HelpCircle size={iconSize} />
      </button>
      {open && (
        <span
          role="tooltip"
          className="absolute top-full left-0 mt-1 z-50 w-72 px-3 py-2 bg-ink-900 text-white text-[11.5px] rounded-md shadow-lg leading-relaxed normal-case tracking-normal font-normal"
        >
          {tip}
        </span>
      )}
    </span>
  );
}

export default MetricLabel;
