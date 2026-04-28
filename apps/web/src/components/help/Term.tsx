'use client';
import { useState } from 'react';
import { cn } from '@/lib/format';

/**
 * Inline jargon term with a hover/focus tooltip. Wrap acronyms and dense
 * terms inside narrative copy (e.g. <Term tip="...">NOI</Term>). The
 * dotted underline cues the reader that hovering will reveal a definition.
 */
export function Term({
  children,
  tip,
  className,
}: {
  children: React.ReactNode;
  tip: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span
      className={cn('relative inline-block', className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      tabIndex={0}
    >
      <span className="border-b border-dotted border-ink-400 cursor-help">
        {children}
      </span>
      {open && (
        <span
          role="tooltip"
          className="absolute bottom-full left-0 mb-2 z-50 w-64 px-3 py-2 bg-ink-900 text-white text-[11.5px] rounded-md shadow-lg leading-relaxed normal-case tracking-normal font-normal"
        >
          {tip}
        </span>
      )}
    </span>
  );
}

export default Term;
