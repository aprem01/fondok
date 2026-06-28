'use client';

/**
 * Expandable USALI deviation list for one document (ROADMAP #3).
 *
 * Renders under the document card when the user clicks the
 * ``UsaliBadge``. Grouped by severity (CRITICAL first), each row pairs a
 * rule name + plain-language message + the actual extracted value
 * Fondok evaluated. Rules flagged ``requires_market_context`` render
 * grayed out with a tooltip explaining why we couldn't evaluate them
 * against the deal.
 *
 * No interactive controls beyond the toggle — this is a read-only
 * audit surface. Overrides land in the variance / data-room edit flow.
 */

import { AlertTriangle, AlertCircle, Info, ShieldCheck } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';
import type { WorkerUsaliDeviation } from '@/lib/api';
import { normalizeUsali, type NormalizedUsali } from './UsaliBadge';

type Sev = 'CRITICAL' | 'WARN' | 'INFO';

const SEV_META: Record<
  Sev,
  {
    label: string;
    Icon: typeof AlertTriangle;
    badgeTone: 'red' | 'amber' | 'blue';
    rowBg: string;
    leftRule: string;
    iconColor: string;
  }
> = {
  CRITICAL: {
    label: 'Critical',
    Icon: AlertTriangle,
    badgeTone: 'red',
    rowBg: 'bg-danger-50/40',
    leftRule: 'border-l-danger-500',
    iconColor: 'text-danger-700',
  },
  WARN: {
    label: 'Warn',
    Icon: AlertCircle,
    badgeTone: 'amber',
    rowBg: 'bg-warn-50/40',
    leftRule: 'border-l-warn-500',
    iconColor: 'text-warn-700',
  },
  INFO: {
    label: 'Info',
    Icon: Info,
    badgeTone: 'blue',
    rowBg: 'bg-brand-50/30',
    leftRule: 'border-l-brand-500',
    iconColor: 'text-brand-700',
  },
};

function formatActual(v: number | null): string {
  if (v == null || Number.isNaN(v)) return '—';
  // USALI rules deal in ratios (e.g. 0.34), dollar values, and bare
  // counts. Trust the scorer to have done the math — render compactly:
  // |v| < 1 → percent; else compact $ or count.
  if (Math.abs(v) < 1) return `${(v * 100).toFixed(1)}%`;
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  // Small integers (e.g. count of items) — render raw with separators.
  return v.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function formatThreshold(min: number | null, max: number | null): string | null {
  const fmt = (n: number) => {
    if (Math.abs(n) < 1) return `${(n * 100).toFixed(1)}%`;
    if (Math.abs(n) >= 1_000) return n.toLocaleString('en-US');
    return n.toFixed(2);
  };
  if (min != null && max != null) return `${fmt(min)} – ${fmt(max)}`;
  if (min != null) return `≥ ${fmt(min)}`;
  if (max != null) return `≤ ${fmt(max)}`;
  return null;
}

function DeviationRow({
  deviation,
}: {
  deviation: WorkerUsaliDeviation;
}) {
  const sev = (deviation.severity ?? 'INFO') as Sev;
  const meta = SEV_META[sev] ?? SEV_META.INFO;
  const Icon = meta.Icon;
  const greyed = !!deviation.requires_market_context;
  const tooltip = greyed
    ? 'Requires market context Fondok could not source for this deal — rule deferred until the analyst provides comparable benchmarks.'
    : undefined;
  const threshold = formatThreshold(
    deviation.threshold_min,
    deviation.threshold_max,
  );

  return (
    <div
      title={tooltip}
      className={cn(
        'border-l-4 px-3 py-2 rounded-r-md',
        meta.rowBg,
        meta.leftRule,
        greyed && 'opacity-50',
      )}
    >
      <div className="flex items-start gap-2">
        <Icon
          size={13}
          className={cn('mt-0.5 flex-shrink-0', meta.iconColor)}
          aria-hidden="true"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[12.5px] font-medium text-ink-900">
              {deviation.rule_name}
            </span>
            <span className="text-[10.5px] font-mono text-ink-500">
              {deviation.rule_id}
            </span>
            {greyed && (
              <Badge tone="gray">Market context required</Badge>
            )}
          </div>
          <p className="text-[12px] text-ink-700 mt-1 leading-relaxed">
            {deviation.message}
          </p>
          {(deviation.actual_value != null || threshold) && (
            <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[11px] tabular-nums">
              {deviation.actual_value != null && (
                <span className="inline-flex items-center gap-1 text-ink-700">
                  <span className="text-ink-500">Actual</span>
                  <span className="font-semibold text-ink-900">
                    {formatActual(deviation.actual_value)}
                  </span>
                </span>
              )}
              {threshold && (
                <span className="inline-flex items-center gap-1 text-ink-500">
                  <span>vs USALI</span>
                  <span className="font-medium text-ink-700">{threshold}</span>
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function UsaliDeviationsAccordion({
  score,
  payload,
  className,
}: {
  score: number | null | undefined;
  payload: Parameters<typeof normalizeUsali>[1];
  className?: string;
}) {
  const n: NormalizedUsali | null = normalizeUsali(score, payload);
  if (!n) return null;

  // Sort: CRITICAL > WARN > INFO; market-context ones to the bottom of
  // their bucket so attention lands on the actionable deviations first.
  const order: Record<Sev, number> = { CRITICAL: 0, WARN: 1, INFO: 2 };
  const sortedDeviations = [...n.deviations].sort((a, b) => {
    const sa = order[(a.severity ?? 'INFO') as Sev] ?? 99;
    const sb = order[(b.severity ?? 'INFO') as Sev] ?? 99;
    if (sa !== sb) return sa - sb;
    if (!!a.requires_market_context !== !!b.requires_market_context) {
      return a.requires_market_context ? 1 : -1;
    }
    return a.rule_name.localeCompare(b.rule_name);
  });

  const counts = {
    CRITICAL: n.deviations.filter((d) => d.severity === 'CRITICAL').length,
    WARN: n.deviations.filter((d) => d.severity === 'WARN').length,
    INFO: n.deviations.filter((d) => d.severity === 'INFO').length,
  };

  const headerText =
    n.tier === 'inconclusive'
      ? n.applicableCount != null
        ? `${n.applicableCount} USALI rule${n.applicableCount === 1 ? '' : 's'} applicable — need ≥ 5 to produce a score.`
        : 'Insufficient applicable rules to produce a USALI score.'
      : `${n.applicableCount ?? 0} rule${(n.applicableCount ?? 0) === 1 ? '' : 's'} applicable, ${n.passedCount ?? 0} passed.`;

  return (
    <Card
      className={cn('p-4 bg-bg fade-in-up', className)}
      role="region"
      aria-label="USALI deviations"
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="flex items-start gap-2.5 min-w-0">
          <ShieldCheck size={15} className="text-brand-500 mt-0.5 flex-shrink-0" />
          <div className="min-w-0">
            <h4 className="text-[13px] font-semibold text-ink-900">
              USALI compliance
              {n.score != null && (
                <span className="ml-2 inline-flex items-center gap-1 text-ink-700 tabular-nums">
                  <span className="font-semibold">{Math.round(n.score)}</span>
                  <span className="text-ink-500 text-[11px]">/ 100</span>
                </span>
              )}
            </h4>
            <p className="text-[11.5px] text-ink-500 mt-0.5 leading-relaxed">
              {headerText}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {counts.CRITICAL > 0 && <Badge tone="red">{counts.CRITICAL} Critical</Badge>}
          {counts.WARN > 0 && <Badge tone="amber">{counts.WARN} Warn</Badge>}
          {counts.INFO > 0 && <Badge tone="blue">{counts.INFO} Info</Badge>}
        </div>
      </div>

      {sortedDeviations.length === 0 ? (
        <div className="flex items-center gap-2 text-[12px] text-ink-500 py-2">
          <ShieldCheck size={13} className="text-success-700" aria-hidden="true" />
          {n.tier === 'inconclusive'
            ? 'No deviations to report — score deferred.'
            : 'No deviations — every applicable USALI rule passed.'}
        </div>
      ) : (
        <div className="space-y-1.5">
          {sortedDeviations.map((d) => (
            <DeviationRow key={d.rule_id} deviation={d} />
          ))}
        </div>
      )}
    </Card>
  );
}
