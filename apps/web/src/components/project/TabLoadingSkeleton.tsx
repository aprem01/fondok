import { Card } from '@/components/ui/Card';

/**
 * Lightweight placeholder rendered while a lazy-loaded tab fetches its
 * JS chunk. Uses a CSS-only shimmer so we don't pull in any animation
 * libraries on the critical path.
 */
export default function TabLoadingSkeleton({ rows = 6 }: { rows?: number }) {
  return (
    <Card className="p-5" aria-busy="true" aria-live="polite">
      <div className="space-y-4">
        <div className="h-5 w-1/3 rounded bg-ink-300/30 animate-pulse" />
        <div className="h-3 w-2/3 rounded bg-ink-300/20 animate-pulse" />
        <div className="grid grid-cols-4 gap-3 mt-6">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-16 rounded-md bg-ink-300/15 animate-pulse" />
          ))}
        </div>
        <div className="space-y-2 mt-4">
          {Array.from({ length: rows }).map((_, i) => (
            <div
              key={i}
              className="h-3 rounded bg-ink-300/15 animate-pulse"
              style={{ width: `${60 + ((i * 7) % 35)}%` }}
            />
          ))}
        </div>
      </div>
      <span className="sr-only">Loading tab content…</span>
    </Card>
  );
}
