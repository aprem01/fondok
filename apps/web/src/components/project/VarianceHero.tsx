'use client';

/**
 * VarianceHero — "Fondok's read vs the broker's pitch" scorecard.
 *
 * Big bet #2: the analyst's first question on any deal is "is it as good as
 * the broker says?" — so answer it at the TOP of the Overview instead of
 * burying it in the Variance tab. Leads with the NOI verdict (e.g. "Broker
 * NOI overstated by $1.0M / 19.6%") + the material line-item variances, each
 * with broker vs Fondok(T-12) + delta. Reuses the deterministic variance
 * report via useVariance; renders nothing on demo/empty deals.
 */

import { useRouter } from 'next/navigation';
import { AlertTriangle, ArrowRight, ShieldCheck } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { cn } from '@/lib/format';
import { useVariance } from '@/lib/hooks/useVariance';
import type { VarianceFlag } from '@/lib/varianceData';

function fmtValue(v: number | undefined, format: VarianceFlag['format']): string {
  if (v == null || Number.isNaN(v)) return '—';
  if (format === 'percent') {
    const pct = v <= 1 ? v * 100 : v;
    return `${pct.toFixed(1)}%`;
  }
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${Math.round(abs / 1_000)}K`;
  return `${sign}$${Math.round(abs)}`;
}

function fmtPct(p: number | undefined): string {
  if (p == null || Number.isNaN(p)) return '—';
  const pct = Math.abs(p) <= 1 ? p * 100 : p;
  return `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`;
}

export function VarianceHero({ dealId }: { dealId: string }) {
  const router = useRouter();
  const v = useVariance(dealId);
  const flags = v.flags ?? [];

  // Nothing to show on demo/empty/loading-first-paint.
  if (v.loading && flags.length === 0) return null;
  if (flags.length === 0) return null;

  const material = flags
    .filter((f) => f.severity === 'CRITICAL' || f.severity === 'WARN')
    .sort((a, b) => (b.noi_impact_usd ?? 0) - (a.noi_impact_usd ?? 0));

  // Headline: the NOI flag if present, else the highest-impact material flag.
  const headline =
    material.find((f) => /noi/i.test(f.metric)) ?? material[0] ?? null;

  const goToVariance = () =>
    router.push(`/projects/${dealId}?tab=analysis&sub=variance`, { scroll: false });

  const clean = material.length === 0;
  const overstated = headline?.broker_overstates ?? false;

  return (
    <Card className="p-0 overflow-hidden" aria-label="Broker variance summary">
      {/* Verdict banner */}
      <div
        className={cn(
          'px-5 py-4 flex items-start gap-3',
          clean ? 'bg-success-500/10' : 'bg-danger-500/10',
        )}
      >
        {clean ? (
          <ShieldCheck size={20} className="text-success-700 flex-shrink-0 mt-0.5" />
        ) : (
          <AlertTriangle size={20} className="text-danger-700 flex-shrink-0 mt-0.5" />
        )}
        <div className="min-w-0 flex-1">
          <div className="text-[10.5px] uppercase tracking-wider font-semibold text-ink-500">
            Fondok&rsquo;s read vs the broker
          </div>
          {clean ? (
            <div className="text-[15px] font-semibold text-success-700 mt-0.5">
              No material variances — the broker&rsquo;s pro forma ties to the T-12.
            </div>
          ) : headline ? (
            <div className="text-[15px] font-semibold text-danger-700 mt-0.5">
              Broker {headline.field_label}{' '}
              {overstated ? 'overstated' : 'understated'} by{' '}
              {fmtValue(Math.abs(headline.variance_abs ?? 0), headline.format)}
              {headline.variance_pct != null && (
                <span> ({fmtPct(headline.variance_pct)})</span>
              )}
            </div>
          ) : (
            <div className="text-[15px] font-semibold text-danger-700 mt-0.5">
              {material.length} material variance{material.length === 1 ? '' : 's'} vs the T-12
            </div>
          )}
          <div className="text-[11.5px] text-ink-600 mt-1">
            {v.critical} critical · {v.warn} warning
            {v.info ? ` · ${v.info} info` : ''} — cross-checked against the
            uploaded T-12.
          </div>
        </div>
      </div>

      {/* Top material line-item variances */}
      {material.length > 0 && (
        <div className="px-5 py-3">
          <div className="grid grid-cols-[1fr_auto_auto_auto] gap-x-4 gap-y-1.5 text-[12px] items-center">
            <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold">
              Line item
            </div>
            <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold text-right">
              Broker
            </div>
            <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold text-right">
              Fondok
            </div>
            <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold text-right">
              Δ
            </div>
            {material.slice(0, 5).map((f) => (
              <RowFrag key={f.flag_id} f={f} />
            ))}
          </div>
        </div>
      )}

      {/* CTA */}
      <button
        type="button"
        onClick={goToVariance}
        className="w-full flex items-center justify-center gap-1.5 px-5 py-2.5 border-t border-border text-[12px] font-medium text-brand-700 hover:bg-brand-50 transition-colors"
      >
        See all {flags.length} variance{flags.length === 1 ? '' : 's'} + line-item detail
        <ArrowRight size={13} />
      </button>
    </Card>
  );
}

function RowFrag({ f }: { f: VarianceFlag }) {
  const sevTone =
    f.severity === 'CRITICAL'
      ? 'text-danger-700'
      : f.severity === 'WARN'
        ? 'text-warn-700'
        : 'text-ink-500';
  return (
    <>
      <div className="text-ink-900 font-medium truncate flex items-center gap-1.5">
        <span
          className={cn(
            'w-1.5 h-1.5 rounded-full flex-shrink-0',
            f.severity === 'CRITICAL'
              ? 'bg-danger-500'
              : f.severity === 'WARN'
                ? 'bg-warn-500'
                : 'bg-ink-300',
          )}
          aria-hidden="true"
        />
        {f.field_label}
      </div>
      <div className="text-right tabular-nums text-ink-700">
        {fmtValue(f.broker_value, f.format)}
      </div>
      <div className="text-right tabular-nums text-ink-700">
        {fmtValue(f.t12_value, f.format)}
      </div>
      <div className={cn('text-right tabular-nums font-medium', sevTone)}>
        {fmtPct(f.variance_pct)}
      </div>
    </>
  );
}
