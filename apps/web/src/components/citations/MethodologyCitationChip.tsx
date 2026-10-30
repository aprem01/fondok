'use client';
/**
 * MethodologyCitationChip — the Atlas / Ask citation for an answer grounded in
 * the Methodology block rather than a document (FON-120). The researcher
 * returns ``(methodology, <section id>)`` as ``methodology_section``; the chip
 * links to that section of the public methodology page.
 */
import Link from 'next/link';
import { METHODOLOGY_SECTIONS } from '@/lib/methodology/content.generated';
import { cn } from '@/lib/format';

export function MethodologyCitationChip({ section, className }: { section: string; className?: string }) {
  const meta = METHODOLOGY_SECTIONS.find((s) => s.id === section);
  const title = meta?.title ?? section;
  return (
    <Link
      href={`/methodology#${section}`}
      title={`Methodology — ${title}`}
      data-methodology-citation={section}
      className={cn(
        'inline-flex items-baseline gap-0.5 px-1 py-0 rounded',
        'bg-ink-100 text-ink-700 hover:bg-ink-200',
        'text-[10.5px] font-semibold leading-none',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 transition-colors',
        className,
      )}
    >
      <span aria-hidden className="text-[9px]">§</span>
      methodology · {section}
    </Link>
  );
}
