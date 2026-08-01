'use client';

/**
 * ProvenanceLedger — big bet #1 capstone: "every number, defensible."
 *
 * One deal-level view of every model assumption and where it came from:
 * grounded from the deal's own docs, a market/benchmark default (NOT this
 * deal's data — the risk to notice), or an analyst override. Filter to the
 * ungrounded set to see exactly what's still riding on a default, and export
 * the whole ledger to CSV so every figure in the IC memo is defensible.
 *
 * Reuses GET /deals/{id}/assumption_sources (sources + values + the
 * document_id backing each key). Renders a quiet state on demo deals.
 */

import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { AlertCircle, Download, FileText, Loader2, ShieldCheck } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { cn } from '@/lib/format';
import { api, isWorkerConnected } from '@/lib/api';
import type { AssumptionSourcesResponse } from '@/lib/api';

// Source label → kind. Grounded = from THIS deal's documents; benchmark =
// a market/seed default (not this deal); override = analyst-set.
const GROUNDED = new Set([
  't12_actual',
  'deal_row',
  'om_comps',
  'om_broker',
  'portfolio_pnl',
  'str_forecast',
]);
const OVERRIDE = new Set(['analyst_override']);

type Kind = 'grounded' | 'benchmark' | 'override';
function sourceKind(s: string): Kind {
  if (OVERRIDE.has(s)) return 'override';
  if (GROUNDED.has(s)) return 'grounded';
  return 'benchmark'; // seed / cbre_horizons / pnl_benchmark / *_default
}

const SOURCE_LABEL: Record<string, string> = {
  seed: 'Seed default',
  deal_row: 'Deal entry',
  t12_actual: 'T-12 actual',
  cbre_horizons: 'CBRE benchmark',
  pnl_benchmark: 'Industry benchmark',
  portfolio_pnl: 'Portfolio P&L',
  om_comps: 'OM comps',
  om_broker: 'OM broker',
  analyst_override: 'Analyst override',
  str_forecast: 'STR forecast',
};

function humanizeKey(k: string): string {
  return k
    .replace(/_pct$/, ' %')
    .replace(/_usd$/, '')
    .split('_')
    .map((s) => (s.length <= 3 ? s.toUpperCase() : s.charAt(0).toUpperCase() + s.slice(1)))
    .join(' ');
}

function fmtValue(k: string, v: number | string | boolean | null): string {
  if (v == null) return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'string') return v;
  const key = k.toLowerCase();
  const isPct = /_pct|growth|occupancy|cap_rate|margin|ratio/.test(key);
  if (isPct) {
    const pct = Math.abs(v) <= 1 ? v * 100 : v;
    return `${pct.toFixed(1)}%`;
  }
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `$${Math.round(v / 1_000)}K`;
  if (abs >= 100) return `$${Math.round(v)}`;
  return String(v);
}

interface Row {
  key: string;
  label: string;
  value: string;
  source: string;
  sourceLabel: string;
  kind: Kind;
  docId?: string;
}

export function ProvenanceLedger({ dealId }: { dealId: string }) {
  const router = useRouter();
  const [data, setData] = useState<AssumptionSourcesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'benchmark' | 'override'>('all');

  const isMock = /^\d+$/.test(dealId);
  useEffect(() => {
    if (!isWorkerConnected() || isMock) {
      setLoading(false);
      return;
    }
    const ac = new AbortController();
    setLoading(true);
    api.deals
      .assumptionSources(dealId, ac.signal)
      .then((r) => {
        setData(r);
        setError(null);
      })
      .catch((e) => {
        if ((e as { name?: string })?.name === 'AbortError') return;
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [dealId, isMock]);

  const rows = useMemo<Row[]>(() => {
    if (!data) return [];
    return Object.entries(data.sources)
      .map(([key, srcRaw]) => {
        const source = typeof srcRaw === 'string' ? srcRaw : String(srcRaw);
        return {
          key,
          label: humanizeKey(key),
          value: fmtValue(key, data.values?.[key] ?? null),
          source,
          sourceLabel: SOURCE_LABEL[source] ?? source,
          kind: sourceKind(source),
          docId: data.source_documents?.[key],
        };
      })
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [data]);

  const benchmarkCount = rows.filter((r) => r.kind === 'benchmark').length;
  const overrideCount = rows.filter((r) => r.kind === 'override').length;
  const groundedCount = rows.filter((r) => r.kind === 'grounded').length;
  const shown = rows.filter((r) => filter === 'all' || r.kind === filter);

  const exportCsv = () => {
    const header = 'Assumption,Value,Source,Kind,Document\n';
    const body = rows
      .map((r) =>
        [r.label, r.value, r.sourceLabel, r.kind, r.docId ?? '']
          .map((c) => `"${String(c).replace(/"/g, '""')}"`)
          .join(','),
      )
      .join('\n');
    const blob = new Blob([header + body], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `provenance-${dealId}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  if (isMock || !isWorkerConnected()) {
    return (
      <Card className="p-6 text-[12.5px] text-ink-500">
        Provenance is available on live deals — every assumption traces to the
        document, benchmark, or override it came from.
      </Card>
    );
  }
  if (loading) {
    return (
      <Card className="p-6 flex items-center gap-2 text-[12.5px] text-ink-500">
        <Loader2 size={14} className="animate-spin" /> Loading provenance…
      </Card>
    );
  }
  if (error || rows.length === 0) {
    return (
      <Card className="p-6 text-[12.5px] text-ink-500">
        {error
          ? `Couldn’t load provenance — ${error}`
          : 'No assumptions to trace yet — run the engines on this deal first.'}
      </Card>
    );
  }

  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex flex-wrap items-center gap-3 px-5 py-3.5 border-b border-border bg-surface-2/40">
        <div className="min-w-0">
          <h3 className="text-[14px] font-semibold text-ink-900">Provenance ledger</h3>
          <p className="text-[11.5px] text-ink-500 mt-0.5">
            Every modeled assumption + where it came from. Export for IC.
          </p>
        </div>
        <div className="flex-1" />
        <div className="flex items-center gap-1.5 flex-wrap text-[11px]">
          <FilterChip active={filter === 'all'} onClick={() => setFilter('all')}>
            All {rows.length}
          </FilterChip>
          <FilterChip
            active={filter === 'benchmark'}
            tone="amber"
            onClick={() => setFilter('benchmark')}
          >
            ⚠ {benchmarkCount} ungrounded
          </FilterChip>
          {overrideCount > 0 && (
            <FilterChip
              active={filter === 'override'}
              tone="violet"
              onClick={() => setFilter('override')}
            >
              {overrideCount} overrides
            </FilterChip>
          )}
          <button
            type="button"
            onClick={exportCsv}
            className="inline-flex items-center gap-1 rounded-full border border-border-strong px-2.5 py-1 text-ink-600 hover:bg-ink-100 font-medium"
          >
            <Download size={11} /> CSV
          </button>
        </div>
      </div>

      {/* Grounding summary bar */}
      <div className="flex items-center gap-4 px-5 py-2 text-[11px] text-ink-600 border-b border-border">
        <span className="inline-flex items-center gap-1.5">
          <ShieldCheck size={12} className="text-success-700" /> {groundedCount} grounded
        </span>
        <span className="inline-flex items-center gap-1.5 text-warn-700">
          <AlertCircle size={12} /> {benchmarkCount} benchmark / seed
        </span>
        {overrideCount > 0 && (
          <span className="text-[11px]" style={{ color: 'var(--p-override,#7C5CF0)' }}>
            {overrideCount} overrides
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]" style={{ minWidth: 520 }}>
          <thead>
            <tr className="text-ink-500 text-[10px] uppercase tracking-wider">
              <th className="text-left font-semibold px-5 py-2">Assumption</th>
              <th className="text-right font-semibold px-3 py-2">Value</th>
              <th className="text-left font-semibold px-3 py-2">Source</th>
              <th className="text-left font-semibold px-5 py-2">Doc</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.key} className="border-t border-border">
                <td className="px-5 py-2 text-ink-900">{r.label}</td>
                <td className="px-3 py-2 text-right tabular-nums font-medium text-ink-900">
                  {r.value}
                </td>
                <td className="px-3 py-2">
                  <KindChip kind={r.kind}>{r.sourceLabel}</KindChip>
                </td>
                <td className="px-5 py-2">
                  {r.docId ? (
                    <button
                      type="button"
                      onClick={() =>
                        router.push(`/projects/${dealId}?doc=${r.docId}`, { scroll: false })
                      }
                      className="inline-flex items-center gap-1 text-brand-700 hover:text-brand-500"
                      title="Open the source document"
                    >
                      <FileText size={12} /> View
                    </button>
                  ) : (
                    <span className="text-ink-400">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function FilterChip({
  active,
  tone,
  onClick,
  children,
}: {
  active: boolean;
  tone?: 'amber' | 'violet';
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-full px-2.5 py-1 font-medium border transition-colors',
        active
          ? tone === 'amber'
            ? 'bg-warn-50 text-warn-700 border-transparent'
            : tone === 'violet'
              ? 'bg-brand-50 text-brand-700 border-transparent'
              : 'bg-ink-900 text-white border-transparent'
          : 'border-border-strong text-ink-600 hover:bg-ink-100',
      )}
    >
      {children}
    </button>
  );
}

function KindChip({ kind, children }: { kind: Kind; children: React.ReactNode }) {
  const cls =
    kind === 'grounded'
      ? 'bg-success-50 text-success-700'
      : kind === 'override'
        ? 'bg-brand-50 text-brand-700'
        : 'bg-warn-50 text-warn-700';
  return (
    <span className={cn('inline-flex items-center rounded px-1.5 py-0.5 text-[10.5px] font-medium', cls)}>
      {children}
    </span>
  );
}
