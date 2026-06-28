'use client';

/**
 * STR comp-set drift side-note (ROADMAP #8, Feature D).
 *
 * Eshan's framing on the June 25 2026 call: "In 2024 you had Hilton South
 * Beach in your comp set; in 2025 it was replaced with W South Beach.
 * Fondok could make those notes on the side." This is that note —
 * intentionally the smallest card on the Validation tab, intentionally
 * silent when nothing changed.
 *
 *   - Silent (renders nothing) when the report has no drifts.
 *   - One small card per consecutive-year diff.
 *   - Added → green "+ Hotel" chips, Removed → red "− Hotel" chips,
 *     Uncertain (>80% Levenshtein but not exact) → amber chip with hover
 *     tooltip showing both names + similarity score.
 *   - Footer: "N comp properties unchanged".
 */

import { useEffect, useState } from 'react';
import { AlertTriangle, Map, RefreshCw } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import {
  api,
  isWorkerConnected,
  CompSetDrift,
  CompSetDriftResponse,
  CompSetUncertainMatch,
} from '@/lib/api';
import { cn } from '@/lib/format';

interface State {
  loading: boolean;
  data: CompSetDriftResponse | null;
  error: string | null;
}

function isLiveDealId(id: string): boolean {
  return isWorkerConnected() && !!id && !/^\d+$/.test(id);
}

export function CompSetDriftCallout({ dealId }: { dealId: string }) {
  const [state, setState] = useState<State>({
    loading: true,
    data: null,
    error: null,
  });
  const [retrySeq, setRetrySeq] = useState(0);
  const liveDeal = isLiveDealId(dealId);

  useEffect(() => {
    if (!liveDeal) {
      setState({ loading: false, data: null, error: null });
      return;
    }
    const ctrl = new AbortController();
    setState({ loading: true, data: null, error: null });
    api.validation
      .compSetDrift(dealId, ctrl.signal)
      .then((data) => setState({ loading: false, data, error: null }))
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === 'AbortError') return;
        const msg = err instanceof Error ? err.message : String(err);
        setState({ loading: false, data: null, error: msg });
      });
    return () => ctrl.abort();
  }, [dealId, liveDeal, retrySeq]);

  // Silent on non-live deals — there's no comp set to compare against.
  if (!liveDeal) return null;

  if (state.loading) {
    return (
      <Card className="p-4" aria-busy="true" aria-label="Loading comp-set drift">
        <div className="flex items-center gap-2 mb-3">
          <div className="w-4 h-4 rounded bg-ink-100 animate-pulse" aria-hidden="true" />
          <div className="h-3.5 w-40 rounded bg-ink-100 animate-pulse" aria-hidden="true" />
        </div>
        <div className="space-y-1.5">
          <div className="h-4 w-3/4 rounded bg-ink-100 animate-pulse" aria-hidden="true" />
          <div className="h-4 w-2/3 rounded bg-ink-100 animate-pulse" aria-hidden="true" />
        </div>
      </Card>
    );
  }

  if (state.error) {
    return (
      <Card className="p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-[12px] text-danger-700">
            <AlertTriangle size={13} aria-hidden="true" />
            <span>Couldn't load comp-set drift — {state.error}</span>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setRetrySeq((n) => n + 1)}
            aria-label="Retry loading comp-set drift"
          >
            <RefreshCw size={11} aria-hidden="true" /> Try again
          </Button>
        </div>
      </Card>
    );
  }

  const drifts = state.data?.drifts ?? [];
  if (drifts.length === 0) {
    // Silent per spec — render nothing when there are no drifts. Eshan
    // specifically wanted this to feel subtle.
    return null;
  }

  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 mb-3">
        <Map size={14} className="text-brand-500" aria-hidden="true" />
        <h3 className="text-[13px] font-semibold text-ink-900">
          STR comp-set drift
        </h3>
        <span className="text-[11px] text-ink-500">
          year-over-year roster changes
        </span>
      </div>

      <div className="space-y-4">
        {drifts.map((d) => (
          <DriftBlock key={`${d.year_from}-${d.year_to}`} drift={d} />
        ))}
      </div>
    </Card>
  );
}

function DriftBlock({ drift }: { drift: CompSetDrift }) {
  const hasMovement =
    drift.added.length > 0 ||
    drift.removed.length > 0 ||
    drift.uncertain_matches.length > 0;

  return (
    <div className="space-y-2">
      <div className="text-[12px] font-semibold text-ink-900 flex items-center gap-1.5">
        Comp set changed
        <span className="tabular-nums text-ink-700">
          {drift.year_from} → {drift.year_to}
        </span>
      </div>

      {!hasMovement ? (
        <div className="text-[11.5px] text-ink-500">
          No additions or removals — roster matches across both years.
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-1.5">
          {drift.added.map((c) => (
            <span
              key={`add-${c.name}`}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-success-50 text-success-700 border border-success-500/30 text-[11px] font-medium"
              title={`Added in ${drift.year_to}${c.keys ? ` · ${c.keys} keys` : ''}`}
            >
              <span aria-hidden="true">+</span>
              {c.name}
              {c.keys != null && (
                <span className="text-[10px] text-success-700/70 tabular-nums">
                  {c.keys}k
                </span>
              )}
            </span>
          ))}
          {drift.removed.map((c) => (
            <span
              key={`rm-${c.name}`}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-danger-50 text-danger-700 border border-danger-500/30 text-[11px] font-medium"
              title={`Removed from ${drift.year_to}${c.keys ? ` · ${c.keys} keys` : ''}`}
            >
              <span aria-hidden="true">−</span>
              {c.name}
              {c.keys != null && (
                <span className="text-[10px] text-danger-700/70 tabular-nums">
                  {c.keys}k
                </span>
              )}
            </span>
          ))}
          {drift.uncertain_matches.map((m) => (
            <UncertainChip key={`u-${m.from_name}-${m.to_name}`} match={m} />
          ))}
        </div>
      )}

      {drift.unchanged.length > 0 && (
        <div className="text-[11px] text-ink-500 pt-1 border-t border-border">
          <span className="tabular-nums font-medium text-ink-700">
            {drift.unchanged.length}
          </span>{' '}
          comp propert{drift.unchanged.length === 1 ? 'y' : 'ies'} unchanged
        </div>
      )}
    </div>
  );
}

function UncertainChip({ match }: { match: CompSetUncertainMatch }) {
  const pct = Math.round((match.similarity ?? 0) * 100);
  const title = `Possibly the same property under a different name.\n${match.from_name} → ${match.to_name}\n${pct}% name similarity`;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0.5 rounded-md',
        'bg-warn-50 text-warn-700 border border-warn-500/30 text-[11px] font-medium',
        'cursor-help',
      )}
      title={title}
      aria-label={`Uncertain match: ${match.from_name} to ${match.to_name}, ${pct} percent similarity`}
    >
      ?{' '}
      <span className="truncate max-w-[180px]">
        {match.from_name} → {match.to_name}
      </span>
      <span className="text-[10px] text-warn-700/70 tabular-nums">{pct}%</span>
    </span>
  );
}
