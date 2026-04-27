'use client';
import {
  Pin, Calendar, Target, TrendingUp, Building2, Lightbulb, MessageSquare, DollarSign,
} from 'lucide-react';
import { cn } from '@/lib/format';

const items = [
  { icon: Pin, label: 'Pin / Collapse' },
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
      className={cn(
        'w-8 flex-shrink-0 sticky top-4 self-start hidden lg:flex flex-col items-center gap-1 py-2 bg-white border border-border rounded-md shadow-card',
        className,
      )}
    >
      {items.map(({ icon: Icon, label }) => (
        <button
          key={label}
          aria-label={label}
          title={label}
          className="w-7 h-7 flex items-center justify-center rounded text-ink-500 hover:bg-brand-50 hover:text-brand-700 transition-colors"
        >
          <Icon size={13} />
        </button>
      ))}
    </aside>
  );
}
