'use client';
/**
 * Sticky right-side glyph rail rendered next to engine tabs. The icons map
 * to engine-tab concepts (Calendar → Investment Timeline, DollarSign →
 * Financing, Target → IRR target on Returns, etc.) so readers can scan the
 * rail to recall what's covered. They're non-interactive on purpose: this
 * is a legend, not a navigation strip. Converting to <button> would imply
 * shortcuts we don't have — see project notes on the cosmetic-affordance
 * rule. If we ever wire real shortcuts, swap the spans back to <button>
 * with concrete onClick handlers.
 */
import {
  Pin, Calendar, Target, TrendingUp, Building2, Lightbulb, MessageSquare, DollarSign,
} from 'lucide-react';
import { cn } from '@/lib/format';

const items = [
  { icon: Pin, label: 'Pinned' },
  { icon: Calendar, label: 'Timeline' },
  { icon: Target, label: 'Targets' },
  { icon: TrendingUp, label: 'Trend' },
  { icon: Building2, label: 'Property' },
  { icon: Lightbulb, label: 'Insights' },
  { icon: MessageSquare, label: 'Comments' },
  { icon: DollarSign, label: 'Financials' },
];

export default function EngineRightRail({ className }: { className?: string }) {
  return (
    <aside
      aria-label="Engine sections — visual legend"
      className={cn(
        'w-8 flex-shrink-0 sticky top-4 self-start hidden lg:flex flex-col items-center gap-1 py-2 bg-white border border-border rounded-md shadow-card',
        className,
      )}
    >
      {items.map(({ icon: Icon, label }) => (
        <span
          key={label}
          role="presentation"
          title={label}
          className="w-7 h-7 flex items-center justify-center rounded text-ink-300"
        >
          <Icon size={13} aria-hidden="true" />
        </span>
      ))}
    </aside>
  );
}
