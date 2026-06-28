'use client';

/**
 * Document coverage gap chips strip (ROADMAP #7, Feature B).
 *
 * Sam's June 25 framing: "If I have financials from 2019 to 2025 but
 * I'm missing detailed for 2024 to 2025, only summary — that's a gap
 * I'd want Fondok to flag." This component is the flag.
 *
 * Horizontal, scrollable strip of severity-colored chips. Mounts at the
 * top of the Data Room and Validation tabs. Click a chip → modal with
 * the full gap detail + an "Upload this year's financials" CTA that
 * jumps to the upload zone (toast for now; wizard ships in next sprint).
 *
 * Dismissibles persist to localStorage keyed by ``{dealId}:{gap_type}:{year}``
 * — non-calendar fiscal year deals get this affordance per the Wave 1
 * product decision.
 *
 * Empty state copy: "No financial coverage gaps detected — your 5-year
 * history is complete."
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  AlertCircle,
  Info,
  RefreshCw,
  ShieldCheck,
  Upload,
  X,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import Modal from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { api, isWorkerConnected, CoverageGap, CoverageResponse } from '@/lib/api';
import { cn } from '@/lib/format';

type Sev = 'error' | 'warn' | 'info';

const SEV_META: Record<
  Sev,
  {
    Icon: typeof AlertTriangle;
    chip: string;
    chipHover: string;
    iconColor: string;
    label: string;
  }
> = {
  error: {
    Icon: AlertTriangle,
    chip: 'bg-danger-50 text-danger-700 border-danger-500/30',
    chipHover: 'hover:bg-danger-100 hover:border-danger-500/50',
    iconColor: 'text-danger-700',
    label: 'Critical',
  },
  warn: {
    Icon: AlertCircle,
    chip: 'bg-warn-50 text-warn-700 border-warn-500/30',
    chipHover: 'hover:bg-warn-100 hover:border-warn-500/50',
    iconColor: 'text-warn-700',
    label: 'Warn',
  },
  info: {
    Icon: Info,
    chip: 'bg-brand-50 text-brand-700 border-brand-500/30',
    chipHover: 'hover:bg-brand-100 hover:border-brand-500/50',
    iconColor: 'text-brand-700',
    label: 'Info',
  },
};

const GAP_TYPE_PHRASE: Record<string, (year: number) => string> = {
  year_missing: (y) => `Missing ${y} financials`,
  month_partial: (y) => `${y} monthly P&L incomplete`,
  annual_no_detail: (y) => `${y} annual only — no monthly`,
  summary_only: (y) => `${y} summary only — no detail`,
};

function chipLabel(gap: CoverageGap): string {
  const fn = GAP_TYPE_PHRASE[gap.gap_type];
  if (fn) {
    // For month_partial, append the missing months when available so the
    // chip reads "2024 monthly Nov-Dec missing" rather than the generic
    // detail-gap phrase.
    if (gap.gap_type === 'month_partial' && gap.months_missing?.length) {
      return `${gap.year} monthly ${formatMonths(gap.months_missing)} missing`;
    }
    return fn(gap.year);
  }
  return gap.message || `Gap in ${gap.year}`;
}

function formatMonths(months: number[]): string {
  if (months.length === 0) return '';
  const names = [
    '', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];
  const sorted = [...months].sort((a, b) => a - b);
  if (sorted.length <= 3) {
    return sorted.map((m) => names[m] ?? `M${m}`).join(', ');
  }
  // Contiguous range — render as "Nov-Dec" rather than enumerating.
  const isContiguous = sorted.every((m, i) =>
    i === 0 || m === sorted[i - 1] + 1,
  );
  if (isContiguous) {
    return `${names[sorted[0]] ?? sorted[0]}-${names[sorted[sorted.length - 1]] ?? sorted[sorted.length - 1]}`;
  }
  return sorted.map((m) => names[m] ?? `M${m}`).join(', ');
}

function normalizeSev(sev: string): Sev {
  if (sev === 'error' || sev === 'warn' || sev === 'info') return sev;
  return 'info';
}

function dismissKey(dealId: string, gap: CoverageGap): string {
  return `fondok:gap-dismiss:${dealId}:${gap.gap_type}:${gap.year}`;
}

interface State {
  loading: boolean;
  data: CoverageResponse | null;
  error: string | null;
}

// Heuristic so the Data Room can hide the strip on mock-id (numeric) deals.
function isLiveDealId(id: string): boolean {
  return isWorkerConnected() && !!id && !/^\d+$/.test(id);
}

export function GapChipsStrip({
  dealId,
  lookbackYears = 5,
  /** Where this strip is mounted — used only for the analytics-like
   *  ``aria-label`` so screen readers announce context. */
  surface = 'dataroom',
  /** Hook the modal's "Upload" CTA into the parent — most useful on
   *  the Data Room where we want to scroll to the actual drop zone. */
  onUploadClick,
}: {
  dealId: string;
  lookbackYears?: number;
  surface?: 'dataroom' | 'validation';
  onUploadClick?: (gap: CoverageGap) => void;
}) {
  const { toast } = useToast();
  const [state, setState] = useState<State>({
    loading: true,
    data: null,
    error: null,
  });
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [openGap, setOpenGap] = useState<CoverageGap | null>(null);
  const [refreshSeq, setRefreshSeq] = useState(0);
  const liveDeal = isLiveDealId(dealId);

  // Load dismissals from localStorage on mount.
  useEffect(() => {
    if (typeof window === 'undefined' || !dealId) return;
    const next = new Set<string>();
    try {
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith(`fondok:gap-dismiss:${dealId}:`)) {
          next.add(k);
        }
      }
    } catch {
      // Storage disabled (private mode); silently fall through.
    }
    setDismissed(next);
  }, [dealId]);

  // Fetch coverage.
  useEffect(() => {
    if (!liveDeal) {
      setState({ loading: false, data: null, error: null });
      return;
    }
    const ctrl = new AbortController();
    setState((s) => ({ ...s, loading: true, error: null }));
    api.validation
      .coverage(dealId, lookbackYears, ctrl.signal)
      .then((data) => setState({ loading: false, data, error: null }))
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === 'AbortError') return;
        const msg = err instanceof Error ? err.message : String(err);
        setState({ loading: false, data: null, error: msg });
      });
    return () => ctrl.abort();
  }, [dealId, lookbackYears, liveDeal, refreshSeq]);

  const dismissGap = useCallback(
    (gap: CoverageGap) => {
      const key = dismissKey(dealId, gap);
      try {
        window.localStorage.setItem(key, '1');
      } catch {
        // Fall through — in-session dismissal still works.
      }
      setDismissed((prev) => {
        const next = new Set(prev);
        next.add(key);
        return next;
      });
      toast(`Dismissed: ${chipLabel(gap)}`, { type: 'info' });
    },
    [dealId, toast],
  );

  const visibleGaps = useMemo(() => {
    if (!state.data) return [];
    return state.data.gaps.filter((g) => !dismissed.has(dismissKey(dealId, g)));
  }, [state.data, dismissed, dealId]);

  // Don't render anything on mock / non-live deals — saves a noisy
  // empty state for the Kimpton fixture which has no coverage data.
  if (!liveDeal) return null;

  // Loading skeleton — keep small footprint so the strip doesn't push
  // the rest of the tab around as it streams.
  if (state.loading) {
    return (
      <div
        className="flex items-center gap-2 overflow-hidden"
        aria-label="Loading document coverage gaps"
        aria-busy="true"
      >
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-7 w-44 rounded-md bg-ink-100 animate-pulse flex-shrink-0"
            aria-hidden="true"
          />
        ))}
      </div>
    );
  }

  if (state.error) {
    return (
      <Card className="p-3 border-l-4 border-l-danger-500">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-[12.5px] text-danger-700">
            <AlertTriangle size={14} aria-hidden="true" />
            <span>Couldn't load coverage gaps — {state.error}</span>
          </div>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setRefreshSeq((n) => n + 1)}
            aria-label="Retry loading document coverage"
          >
            <RefreshCw size={12} aria-hidden="true" /> Try again
          </Button>
        </div>
      </Card>
    );
  }

  if (visibleGaps.length === 0) {
    // Distinguish "nothing analyzed yet" from "fully covered". The
    // backend returns ``year_coverage`` keyed by every year the deal
    // has a financial doc for; empty means no financials uploaded.
    // Without this guard we'd cheerfully tell the user "5-year history
    // is complete" when the truth is the upload pipeline returned zero
    // documents.
    const coveredYearCount = Object.keys(state.data?.year_coverage ?? {}).length;
    if (coveredYearCount === 0) {
      return (
        <div
          className="flex items-center gap-2 px-3 py-2 rounded-md bg-ink-100/60 border border-border text-[12px] text-ink-700"
          role="status"
          aria-label="No financial coverage analyzed yet"
        >
          <Info size={13} aria-hidden="true" />
          <span>
            No financials uploaded yet — coverage analysis will run once
            you add at least one P&amp;L document.
          </span>
        </div>
      );
    }
    return (
      <div
        className="flex items-center gap-2 px-3 py-2 rounded-md bg-success-50/60 border border-success-500/20 text-[12px] text-success-700"
        role="status"
        aria-label="No financial coverage gaps detected"
      >
        <ShieldCheck size={13} aria-hidden="true" />
        <span>
          No financial coverage gaps detected — your {state.data?.lookback_years ?? lookbackYears}
          -year history is complete.
        </span>
      </div>
    );
  }

  const totalCount = visibleGaps.length;
  const critCount = visibleGaps.filter((g) => normalizeSev(g.severity) === 'error').length;
  const warnCount = visibleGaps.filter((g) => normalizeSev(g.severity) === 'warn').length;

  return (
    <div
      role="region"
      aria-label={`Document coverage gaps on ${surface === 'dataroom' ? 'Data Room' : 'Validation'}`}
      className="space-y-2"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <AlertTriangle size={14} className="text-warn-700" aria-hidden="true" />
          <span className="text-[12px] font-semibold text-ink-900">
            {totalCount} coverage gap{totalCount === 1 ? '' : 's'}
          </span>
          <span className="text-[11.5px] text-ink-500 tabular-nums">
            {critCount > 0 && (
              <span className="text-danger-700 font-medium">{critCount} critical</span>
            )}
            {critCount > 0 && warnCount > 0 && <span className="mx-1">·</span>}
            {warnCount > 0 && (
              <span className="text-warn-700 font-medium">{warnCount} warn</span>
            )}
          </span>
        </div>
        <button
          type="button"
          onClick={() => setRefreshSeq((n) => n + 1)}
          className="text-[11.5px] text-ink-500 hover:text-ink-900 inline-flex items-center gap-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded px-1"
          aria-label="Refresh coverage gaps"
          title="Refresh coverage analysis"
        >
          <RefreshCw size={11} aria-hidden="true" /> Refresh
        </button>
      </div>
      <div
        className="flex items-center gap-2 overflow-x-auto scrollbar-thin pb-1 -mx-1 px-1"
        role="list"
      >
        {visibleGaps.map((g) => {
          const sev = normalizeSev(g.severity);
          const meta = SEV_META[sev];
          const Icon = meta.Icon;
          const key = `${g.gap_type}-${g.year}-${(g.months_missing ?? []).join(',')}`;
          return (
            <div key={key} role="listitem" className="flex-shrink-0">
              <div
                className={cn(
                  'inline-flex items-center gap-1.5 pl-2 pr-1 py-1 rounded-md border text-[11.5px]',
                  meta.chip,
                  meta.chipHover,
                  'transition-colors',
                )}
              >
                <button
                  type="button"
                  onClick={() => setOpenGap(g)}
                  className="inline-flex items-center gap-1.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded"
                  aria-label={`${meta.label}: ${chipLabel(g)}. Click for details.`}
                >
                  <Icon size={11} className={meta.iconColor} aria-hidden="true" />
                  <span className="font-medium">{chipLabel(g)}</span>
                </button>
                {g.dismissible && (
                  <button
                    type="button"
                    onClick={() => dismissGap(g)}
                    className="ml-0.5 p-0.5 rounded text-ink-500 hover:text-ink-900 hover:bg-white/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    title="Dismiss (non-calendar fiscal year)"
                    aria-label={`Dismiss ${chipLabel(g)}`}
                  >
                    <X size={11} aria-hidden="true" />
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <Modal
        open={!!openGap}
        onClose={() => setOpenGap(null)}
        title={openGap ? chipLabel(openGap) : ''}
        maxWidth="max-w-lg"
      >
        {openGap && (
          <GapDetail
            gap={openGap}
            lookbackYears={state.data?.lookback_years ?? lookbackYears}
            onUpload={() => {
              setOpenGap(null);
              if (onUploadClick) {
                onUploadClick(openGap);
                return;
              }
              toast(
                'Per-year upload wizard ships next sprint — for now, drop the missing financials into the Data Room zone.',
                { type: 'info', duration: 6000 },
              );
            }}
            onDismiss={
              openGap.dismissible
                ? () => {
                    dismissGap(openGap);
                    setOpenGap(null);
                  }
                : undefined
            }
            onClose={() => setOpenGap(null)}
          />
        )}
      </Modal>
    </div>
  );
}

function GapDetail({
  gap,
  lookbackYears,
  onUpload,
  onDismiss,
  onClose,
}: {
  gap: CoverageGap;
  lookbackYears: number;
  onUpload: () => void;
  onDismiss?: () => void;
  onClose: () => void;
}) {
  const sev = normalizeSev(gap.severity);
  const meta = SEV_META[sev];
  const Icon = meta.Icon;
  return (
    <div className="p-5 space-y-4">
      <div className="flex items-start gap-3">
        <div
          className={cn(
            'w-9 h-9 rounded-md flex items-center justify-center flex-shrink-0 border',
            meta.chip,
          )}
        >
          <Icon size={16} className={meta.iconColor} aria-hidden="true" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-ink-500 font-semibold">
            <span>{meta.label}</span>
            <span className="text-ink-300" aria-hidden="true">·</span>
            <span className="tabular-nums">{gap.year}</span>
            <span className="text-ink-300" aria-hidden="true">·</span>
            <span className="font-mono text-[10.5px]">{gap.gap_type}</span>
          </div>
          <p className="text-[13px] text-ink-700 mt-1.5 leading-relaxed">
            {gap.message}
          </p>
        </div>
      </div>

      {gap.months_missing && gap.months_missing.length > 0 && (
        <div className="rounded-md bg-bg border border-border px-3 py-2.5">
          <div className="text-[11px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">
            Months missing
          </div>
          <div className="flex flex-wrap gap-1.5">
            {gap.months_missing.map((m) => (
              <span
                key={m}
                className="inline-flex items-center px-2 py-0.5 rounded bg-warn-50 text-warn-700 border border-warn-500/30 text-[11px] font-medium tabular-nums"
              >
                {formatMonths([m])}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="text-[11.5px] text-ink-500 leading-relaxed">
        Coverage audit looked back {lookbackYears} years from today. Fondok
        flags both sequential gaps (a year with zero P&L coverage) and
        detail-level gaps (a year with annual coverage but no monthly
        breakdown).
      </div>

      <div className="flex items-center justify-between gap-2 pt-2 border-t border-border">
        {onDismiss ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={onDismiss}
            aria-label="Dismiss this gap (non-calendar fiscal year)"
          >
            Dismiss
          </Button>
        ) : (
          <span />
        )}
        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={onClose}>
            Close
          </Button>
          <Button size="sm" variant="primary" onClick={onUpload}>
            <Upload size={12} aria-hidden="true" />
            Upload {gap.year} financials
          </Button>
        </div>
      </div>
    </div>
  );
}
