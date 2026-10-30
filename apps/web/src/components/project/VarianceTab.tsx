'use client';
import { useMemo, useState, useRef } from 'react';
import { useParams } from 'next/navigation';
import {
  Sparkles, AlertTriangle, AlertCircle, Info, ArrowDown, ArrowUp,
  ShieldCheck, X, Check, FileSearch,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { cn, fmtCurrency, fmtPct } from '@/lib/format';
import { rulesById, type VarianceFlag, type Severity } from '@/lib/variance';
import { useVariance } from '@/lib/hooks/useVariance';
import { Citation } from '@/components/citations/Citation';
import { WorkerNotConfigured } from '@/components/WorkerNotConfigured';

type RowDecision = 'pending' | 'accepted' | 'overridden';

const severityTone: Record<Severity, 'red' | 'amber' | 'blue'> = {
  CRITICAL: 'red',
  WARN: 'amber',
  INFO: 'blue',
};

const severityLabel: Record<Severity, string> = {
  CRITICAL: 'Critical',
  WARN: 'Warn',
  INFO: 'Info',
};

const severityBorder: Record<Severity, string> = {
  CRITICAL: 'border-l-danger-500 bg-danger-50/40',
  WARN: 'border-l-warn-500 bg-warn-50/40',
  INFO: 'border-l-brand-500/50 bg-brand-50/30',
};

const severityIcon: Record<Severity, typeof AlertTriangle> = {
  CRITICAL: AlertTriangle,
  WARN: AlertCircle,
  INFO: Info,
};

function formatValue(v: number | undefined, format: VarianceFlag['format']): string {
  if (v === undefined || v === null || Number.isNaN(v)) return '—';
  switch (format) {
    case 'currency':
      return v >= 1_000_000
        ? fmtCurrency(v, { compact: true })
        : fmtCurrency(v);
    case 'percent':
      return fmtPct(v, 1);
    case 'currency_per_key':
      return `${fmtCurrency(v)}/key`;
    case 'index':
      return v.toFixed(2);
    default:
      return String(v);
  }
}

function formatDelta(flag: VarianceFlag): { text: string; pct?: string } {
  if (flag.broker_value === undefined || flag.t12_value === undefined) {
    return { text: '—' };
  }
  const abs = flag.broker_value - flag.t12_value;
  if (flag.format === 'percent') {
    const pts = (abs * 100).toFixed(1);
    return { text: `${abs >= 0 ? '+' : ''}${pts} pp` };
  }
  if (flag.format === 'currency') {
    const text =
      Math.abs(abs) >= 1_000_000
        ? fmtCurrency(abs, { compact: true })
        : fmtCurrency(abs);
    const pct = flag.t12_value !== 0
      ? `${abs >= 0 ? '+' : ''}${((abs / flag.t12_value) * 100).toFixed(1)}%`
      : undefined;
    return { text: `${abs >= 0 ? '+' : ''}${text}`, pct };
  }
  if (flag.format === 'currency_per_key') {
    return {
      text: `${abs >= 0 ? '+' : ''}${fmtCurrency(abs)}/key`,
      pct: flag.t12_value !== 0
        ? `${abs >= 0 ? '+' : ''}${((abs / flag.t12_value) * 100).toFixed(1)}%`
        : undefined,
    };
  }
  return { text: String(abs) };
}

export default function VarianceTab() {
  const params = useParams();
  const rawId = (params?.id as string | undefined) ?? '';
  const [decisions, setDecisions] = useState<Record<string, RowDecision>>({});
  const rowRefs = useRef<Record<string, HTMLTableRowElement | null>>({});

  // The worker's variance report for this deal — the ONLY source of flags.
  // `flags` is null until the report lands (or when nothing can be fetched);
  // the states below say which: not configured, loading, failed, no flags.
  const live = useVariance(rawId);
  const varianceFlags: VarianceFlag[] = live.flags ?? [];
  const criticalCount = live.critical;
  const warnCount = live.warn;
  const infoCount = live.info;
  const liveNote = live.note;

  const setDecision = (id: string, d: RowDecision) =>
    setDecisions(prev => ({ ...prev, [id]: prev[id] === d ? 'pending' : d }));

  // Heatmap is sorted by absolute NOI impact desc.
  const heatmap = useMemo(() => {
    const total = varianceFlags.reduce((s, f) => s + Math.abs(f.noi_impact_usd), 0) || 1;
    return [...varianceFlags]
      .sort((a, b) => Math.abs(b.noi_impact_usd) - Math.abs(a.noi_impact_usd))
      .map(f => ({ flag: f, share: Math.abs(f.noi_impact_usd) / total }));
  }, [varianceFlags]);

  const scrollToFlag = (id: string) => {
    const el = rowRefs.current[id];
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.classList.add('ring-2', 'ring-brand-500');
      setTimeout(() => el.classList.remove('ring-2', 'ring-brand-500'), 1400);
    }
  };

  if (!live.workerConfigured) {
    return <WorkerNotConfigured surface="variance report" />;
  }
  // A failed fetch is NOT "no flags yet": say the report did not load and
  // offer the retry (D1 — error and empty are different states).
  if (live.error && live.flags === null) {
    return (
      <Card className="p-5 border-danger-500/30 bg-danger-50" role="alert" data-testid="variance-load-error">
        <div className="flex items-start gap-3">
          <AlertTriangle size={16} className="text-danger-700 flex-shrink-0 mt-0.5" aria-hidden="true" />
          <div className="flex-1">
            <div className="text-[13px] font-semibold text-danger-700">The variance report did not load</div>
            <p className="text-[12px] text-danger-700/80 mt-1">{live.error}</p>
          </div>
          <Button variant="secondary" size="sm" onClick={live.refresh}>Retry</Button>
        </div>
      </Card>
    );
  }
  if (live.flags === null) {
    return (
      <Card className="p-5" role="status" aria-live="polite" data-testid="variance-loading">
        <div className="text-[12.5px] text-ink-500">Loading the variance report…</div>
      </Card>
    );
  }
  // No flags: the worker has not compared both sides yet (only the OM, only
  // the T-12). Render its structured "what's missing" note.
  if (varianceFlags.length === 0) {
    return (
      <div className="space-y-5">
        <Card className="p-5">
          <div className="flex items-start gap-3">
            <div className="w-9 h-9 rounded-md bg-bg flex items-center justify-center flex-shrink-0">
              <FileSearch size={16} className="text-ink-500" />
            </div>
            <div className="flex-1">
              <div className="flex items-center gap-2 mb-1.5">
                <h3 className="text-[14px] font-semibold text-ink-900">
                  Broker Variance — awaiting inputs
                </h3>
                <Badge tone="gray">No flags yet</Badge>
              </div>
              <p className="text-[12.5px] text-ink-700 leading-relaxed">
                {liveNote ??
                  'Variance compares the broker pro forma (from the OM) against T-12 actuals. Upload and extract both an Offering Memorandum and a T-12 to populate this tab.'}
              </p>
            </div>
          </div>
        </Card>
      </div>
    );
  }

  // The narrative ("Most material: broker NOI overstated by $X (Y%), driven
  // by …") is populated from the deal's actual flags. Drivers are the
  // top-three flags by absolute NOI impact, prefixed with severity.
  const totalFlags = varianceFlags.length;

  // Prefer the dedicated NOI flag for the headline overstate $; fall
  // back to the sum of broker-vs-T12 deltas across flags so a deal
  // without an explicit NOI flag still shows a directional anchor.
  const liveNoiFlag = varianceFlags.find((f) => f.metric.toLowerCase() === 'noi');
  const noiOverstateUsd =
    liveNoiFlag && liveNoiFlag.broker_value != null && liveNoiFlag.t12_value != null
      ? liveNoiFlag.broker_value - liveNoiFlag.t12_value
      : varianceFlags.reduce(
          (s, f) =>
            s + Math.max(0, f.broker_value ?? 0) - Math.max(0, f.t12_value ?? 0),
          0,
        );
  const noiOverstatePct =
    liveNoiFlag && liveNoiFlag.t12_value && liveNoiFlag.broker_value != null
      ? liveNoiFlag.broker_value / liveNoiFlag.t12_value - 1
      : 0;

  // Top three NOI drivers — sorted by absolute NOI impact, NOI flag
  // itself excluded since it's the rollup. Each driver renders as a
  // human-readable phrase ("ADR overstated", "RevPAR optimistic",
  // "Insurance understated") derived from the metric name + delta sign.
  const liveTopDrivers = [...varianceFlags]
        .filter((f) => f.metric.toLowerCase() !== 'noi')
        .sort((a, b) => Math.abs(b.noi_impact_usd) - Math.abs(a.noi_impact_usd))
        .slice(0, 3)
        .map((f) => {
          const direction =
            (f.broker_value ?? 0) > (f.t12_value ?? 0)
              ? f.metric.toLowerCase().includes('expense') ||
                f.metric.toLowerCase().includes('insurance') ||
                f.metric.toLowerCase().includes('opex')
                ? 'understated'
                : 'overstated'
              : f.metric.toLowerCase().includes('expense') ||
                f.metric.toLowerCase().includes('insurance') ||
                f.metric.toLowerCase().includes('opex')
                ? 'overstated'
                : 'understated';
          return `${f.metric} ${direction}`.toLowerCase();
        });

  const liveTopDriversPhrase =
    liveTopDrivers.length === 0
      ? ''
      : liveTopDrivers.length === 1
        ? liveTopDrivers[0]
        : liveTopDrivers.length === 2
          ? `${liveTopDrivers[0]} and ${liveTopDrivers[1]}`
          : `${liveTopDrivers.slice(0, -1).join(', ')}, and ${liveTopDrivers[liveTopDrivers.length - 1]}`;

  // Direction-aware verb so a broker who *understated* NOI (rare, but
  // happens on stabilized refis) isn't framed as "overstated".
  const noiVerb = noiOverstateUsd >= 0 ? 'overstated' : 'understated';
  const showLiveNoiHeadline = Math.abs(noiOverstateUsd) >= 1_000;

  return (
    <div className="space-y-5">
      {/* AI Summary Card */}
      <Card className="p-5 border-l-4 border-l-danger-500">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-md bg-danger-50 flex items-center justify-center flex-shrink-0">
            <Sparkles size={16} className="text-danger-700" />
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1.5">
              <h3 className="text-[14px] font-semibold text-ink-900">
                Fondok Variance Detection
              </h3>
              <Badge tone="red">{criticalCount} Critical</Badge>
              <Badge tone="amber">{warnCount} Warn</Badge>
              <Badge tone="blue">{infoCount} Info</Badge>
            </div>
            <p className="text-[12.5px] text-ink-700 leading-relaxed">
              Fondok detected{' '}
              <span className="font-semibold text-ink-900">
                {totalFlags} variance flag{totalFlags === 1 ? '' : 's'}
              </span>{' '}
              between the broker pro forma and T-12 actuals.
              {showLiveNoiHeadline ? (
                <>
                  {' '}Most material: broker NOI {noiVerb} by{' '}
                  <span className="font-semibold text-danger-700">
                    {fmtCurrency(Math.abs(noiOverstateUsd), { compact: true })}
                    {noiOverstatePct !== 0 && (
                      <> ({(Math.abs(noiOverstatePct) * 100).toFixed(1)}%)</>
                    )}
                  </span>
                  {liveTopDriversPhrase && (
                    <>, driven by {liveTopDriversPhrase}</>
                  )}
                  .{' '}
                </>
              ) : (
                <>
                  {' '}Each flag below cites the source page and rule it
                  tripped.{' '}
                </>
              )}
              <span className="text-ink-900 font-medium">
                Recommended action: re-underwrite at the corrected NOI level before LOI.
              </span>
            </p>
          </div>
        </div>
      </Card>

      {/* KPI Strip */}
      <div className="grid grid-cols-5 gap-4">
        <KpiCard
          label="Critical Flags"
          value={criticalCount.toString()}
          tone="danger"
          icon={AlertTriangle}
        />
        <KpiCard
          label="Warn Flags"
          value={warnCount.toString()}
          tone="warn"
          icon={AlertCircle}
        />
        <KpiCard
          label="Info Flags"
          value={infoCount.toString()}
          tone="brand"
          icon={Info}
        />
        <KpiCard
          label="NOI Variance"
          value={fmtCurrency(noiOverstateUsd, { compact: true })}
          sub={`${(Math.abs(noiOverstatePct) * 100).toFixed(1)}% overstate`}
          tone="danger"
          icon={ArrowDown}
        />
        <KpiCard
          label="Sources Reconciled"
          value={`${varianceFlags.filter((f) => f.source_documents.length > 0).length} of ${varianceFlags.length}`}
          tone="success"
          icon={ShieldCheck}
        />
      </div>

      {/* Heatmap */}
      <Card className="p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-[14px] font-semibold text-ink-900">
              NOI Impact Heatmap
            </h3>
            <p className="text-[11.5px] text-ink-500 mt-0.5">
              Click a bar to jump to the row. Sized by $ impact on Year-1 underwritten NOI.
            </p>
          </div>
          <span className="text-[11.5px] text-ink-500 tabular-nums">
            Total at risk:{' '}
            <span className="font-semibold text-danger-700">
              {fmtCurrency(
                varianceFlags.reduce((s, f) => s + Math.abs(f.noi_impact_usd), 0),
                { compact: true },
              )}
            </span>
          </span>
        </div>
        <div className="space-y-1.5">
          {heatmap.map(({ flag, share }) => (
            <button
              key={flag.flag_id}
              onClick={() => scrollToFlag(flag.flag_id)}
              className="w-full flex items-center gap-3 group"
              title={`Click to view ${flag.field_label}`}
            >
              <div className="w-44 text-[11.5px] text-ink-700 text-left truncate group-hover:text-ink-900">
                {flag.field_label}
              </div>
              <div className="flex-1 h-5 bg-ink-300/15 rounded overflow-hidden relative">
                <div
                  className={cn(
                    'h-full transition-all',
                    flag.severity === 'CRITICAL' && 'bg-danger-500',
                    flag.severity === 'WARN' && 'bg-warn-500',
                    flag.severity === 'INFO' && 'bg-brand-500',
                  )}
                  style={{ width: `${Math.max(share * 100, 1.5)}%` }}
                />
              </div>
              <div className="w-24 text-right text-[11.5px] tabular-nums text-ink-700">
                {flag.noi_impact_usd > 0
                  ? fmtCurrency(flag.noi_impact_usd, { compact: true })
                  : '—'}
              </div>
              <div className="w-12 text-right">
                <Badge tone={severityTone[flag.severity]}>
                  {flag.severity[0]}
                </Badge>
              </div>
            </button>
          ))}
        </div>
      </Card>

      {/* Side-by-side comparison */}
      <Card className="p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <FileSearch size={15} className="text-brand-500" />
            <h3 className="text-[14px] font-semibold text-ink-900">
              Broker Pro Forma vs T-12 Actuals
            </h3>
          </div>
          <div className="flex items-center gap-2 text-[11.5px] text-ink-500">
            <span className="inline-flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-danger-500" /> Broker overstates
            </span>
            <span className="inline-flex items-center gap-1">
              <span className="w-2 h-2 rounded-full bg-success-500" /> Conservative
            </span>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px] border-separate border-spacing-y-1">
            <thead>
              <tr className="text-ink-500 text-[11px]">
                <th className="text-left font-medium pb-2 px-2">Field</th>
                <th className="text-right font-medium pb-2 px-2">Broker Pro Forma</th>
                <th className="text-right font-medium pb-2 px-2">T-12 Actual</th>
                <th className="text-right font-medium pb-2 px-2">Δ</th>
                <th className="text-center font-medium pb-2 px-2">Severity</th>
                <th className="text-left font-medium pb-2 px-2">Rule</th>
                <th className="text-right font-medium pb-2 px-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {varianceFlags.map(flag => {
                const decision = decisions[flag.flag_id] || 'pending';
                const Icon = severityIcon[flag.severity] ?? AlertTriangle;
                const delta = formatDelta(flag);
                const overstate = flag.broker_overstates && flag.severity !== 'INFO';
                const rule = rulesById[flag.rule_id];
                return (
                  <tr
                    key={flag.flag_id}
                    ref={el => { rowRefs.current[flag.flag_id] = el; }}
                    className={cn(
                      'border-l-4 transition-colors align-top',
                      severityBorder[flag.severity],
                      decision === 'accepted' && 'bg-success-50/60',
                      decision === 'overridden' && 'bg-danger-50/60',
                    )}
                  >
                    <td className="px-2 py-2.5 align-top">
                      <div className="flex items-start gap-2">
                        <Icon
                          size={13}
                          className={cn(
                            'mt-0.5 flex-shrink-0',
                            flag.severity === 'CRITICAL' && 'text-danger-700',
                            flag.severity === 'WARN' && 'text-warn-700',
                            flag.severity === 'INFO' && 'text-brand-700',
                          )}
                        />
                        <div>
                          <div className="font-medium text-ink-900">
                            {flag.field_label}
                          </div>
                          <div className="text-[10.5px] text-ink-500 mt-0.5 font-mono">
                            {flag.flag_id}
                          </div>
                          {flag.source_documents.length > 0 && (
                            // Each chip opens the SourceDocPane on the
                            // exact page the variance was computed from.
                            <div className="mt-1.5 flex flex-wrap items-center gap-1">
                              {flag.source_documents.map((src, idx) => {
                                const name = src.document_id;
                                const tag =
                                  src.document_id.includes('om')
                                    ? 'OM'
                                    : src.document_id.includes('t12')
                                      ? 'T12'
                                      : 'DOC';
                                return (
                                  <Citation
                                    key={`${src.document_id}-${src.page}-${idx}`}
                                    data={{
                                      documentId: src.document_id,
                                      documentName: name,
                                      page: src.page,
                                      field: src.field,
                                    }}
                                    label={`${tag}:p${src.page}`}
                                  />
                                );
                              })}
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td className="px-2 py-2.5 text-right tabular-nums text-ink-500 align-top">
                      {formatValue(flag.broker_value, flag.format)}
                    </td>
                    <td className="px-2 py-2.5 text-right tabular-nums font-semibold text-ink-900 align-top">
                      {formatValue(
                        flag.t12_value !== undefined ? flag.t12_value : flag.value,
                        flag.format,
                      )}
                    </td>
                    <td className="px-2 py-2.5 text-right tabular-nums align-top">
                      <div
                        className={cn(
                          'inline-flex items-center gap-1 font-medium',
                          overstate ? 'text-danger-700' : 'text-success-700',
                        )}
                      >
                        {flag.broker_value !== undefined &&
                          flag.t12_value !== undefined &&
                          (flag.broker_value > flag.t12_value ? (
                            <ArrowUp size={11} />
                          ) : flag.broker_value < flag.t12_value ? (
                            <ArrowDown size={11} />
                          ) : null)}
                        {delta.text}
                      </div>
                      {delta.pct && (
                        <div
                          className={cn(
                            'text-[10.5px] tabular-nums',
                            overstate ? 'text-danger-500' : 'text-success-500',
                          )}
                        >
                          {delta.pct}
                        </div>
                      )}
                    </td>
                    <td className="px-2 py-2.5 text-center align-top">
                      <Badge tone={severityTone[flag.severity]}>
                        {severityLabel[flag.severity]}
                      </Badge>
                    </td>
                    <td className="px-2 py-2.5 align-top max-w-[220px]">
                      <div className="group relative inline-block">
                        <span
                          className="inline-flex items-center px-1.5 py-0.5 text-[10.5px] font-mono rounded bg-ink-300/30 text-ink-700 border border-ink-300/40 cursor-help"
                          title={rule?.description || flag.rule_id}
                        >
                          {flag.rule_id}
                        </span>
                        {rule && (
                          <div className="absolute left-0 top-full mt-1 z-10 hidden group-hover:block w-64 p-2 bg-ink-900 text-white text-[10.5px] rounded shadow-lg leading-relaxed">
                            <div className="font-semibold mb-1">{rule.name}</div>
                            <div className="text-white/80">{rule.description}</div>
                          </div>
                        )}
                      </div>
                      <p className="text-[11px] text-ink-500 italic mt-1.5 leading-snug flex items-start gap-1">
                        <Sparkles size={10} className="text-brand-500 mt-0.5 flex-shrink-0" />
                        <span>{flag.explanation}</span>
                      </p>
                    </td>
                    <td className="px-2 py-2.5 text-right align-top">
                      <div className="inline-flex flex-col gap-1 items-end">
                        <button
                          onClick={() => setDecision(flag.flag_id, 'accepted')}
                          className={cn(
                            'inline-flex items-center gap-1 px-2 py-1 text-[10.5px] rounded border transition-colors',
                            decision === 'accepted'
                              ? 'bg-success-500 text-white border-success-500'
                              : 'bg-white text-success-700 border-success-500/40 hover:bg-success-50',
                          )}
                        >
                          <Check size={10} /> Accept
                        </button>
                        <button
                          onClick={() => setDecision(flag.flag_id, 'overridden')}
                          className={cn(
                            'inline-flex items-center gap-1 px-2 py-1 text-[10.5px] rounded border transition-colors',
                            decision === 'overridden'
                              ? 'bg-danger-500 text-white border-danger-500'
                              : 'bg-white text-danger-700 border-danger-500/40 hover:bg-danger-50',
                          )}
                        >
                          <X size={10} /> Override
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="mt-4 pt-3 border-t border-border flex items-center justify-between text-[11.5px] text-ink-500">
          <span>
            {Object.values(decisions).filter(d => d === 'accepted').length} accepted ·{' '}
            {Object.values(decisions).filter(d => d === 'overridden').length} overridden ·{' '}
            {varianceFlags.length -
              Object.values(decisions).filter(d => d !== 'pending').length}{' '}
            pending
          </span>
          <Button variant="primary" size="sm">
            Apply Adjustments to Underwriting
          </Button>
        </div>
      </Card>
    </div>
  );
}

// ----- helpers -----

function KpiCard({
  label,
  value,
  sub,
  tone,
  icon: Icon,
}: {
  label: string;
  value: string;
  sub?: string;
  tone: 'danger' | 'warn' | 'brand' | 'success';
  icon: typeof AlertTriangle;
}) {
  const map = {
    danger: { bg: 'bg-danger-50', text: 'text-danger-700', accent: 'text-danger-500' },
    warn: { bg: 'bg-warn-50', text: 'text-warn-700', accent: 'text-warn-500' },
    brand: { bg: 'bg-brand-50', text: 'text-brand-700', accent: 'text-brand-500' },
    success: { bg: 'bg-success-50', text: 'text-success-700', accent: 'text-success-500' },
  }[tone];
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between mb-2">
        <span className="text-[11px] uppercase tracking-wide text-ink-500 font-medium">
          {label}
        </span>
        <div className={cn('w-7 h-7 rounded flex items-center justify-center', map.bg)}>
          <Icon size={13} className={map.accent} />
        </div>
      </div>
      <div className={cn('text-[20px] font-semibold tabular-nums', map.text)}>{value}</div>
      {sub && <div className="text-[11px] text-ink-500 mt-0.5">{sub}</div>}
    </Card>
  );
}
