'use client';
/**
 * CompSalesPanel — Wave 3 W3.1
 *
 * Comparable Sales table for the Investment / Returns tab. Renders the
 * raw extracted comp transactions the worker pulled out of the OM plus
 * a "Derived cap rate: X% (median of N comps · M excluded)" callout
 * driven by the deterministic Comparable Sales engine.
 *
 * The toggle in the header flips between median and weighted derivation
 * (recency 70% / market 20% / chain-scale 10%); when toggled the
 * weighting notes are rendered below the table as small print so the
 * analyst can see exactly which filter decisions shaped the headline.
 *
 * Per-row exclusion: each comp has a checkbox that POSTs to
 * /deals/{id}/comp-sales/exclude and re-renders the panel with the
 * updated derivation. The exclusion persists on ``deals.field_overrides``
 * so a subsequent re-run keeps it.
 *
 * Source badge: every panel-level number gets an AssumptionBadge with
 * source="om_comps" so the analyst can deep-link back to the OM page
 * the comp table was extracted from.
 */
import { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/ui/Card';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';
import { api } from '@/lib/api';
import type { CompSalesSetResponse, CompTransactionRow } from '@/lib/api';
import { fmtNumber, fmtPctRaw, cn } from '@/lib/format';

interface Props {
  dealId: string;
}

function fmtMoney(n: number | null): string {
  if (n == null || !Number.isFinite(n)) return '—';
  if (Math.abs(n) >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (Math.abs(n) >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${Math.round(n).toLocaleString()}`;
}

function fmtPerKey(n: number | null): string {
  if (n == null || !Number.isFinite(n)) return '—';
  return `$${Math.round(n).toLocaleString()}`;
}

function fmtCap(pct: number | null, fallback = '—'): string {
  if (pct == null || !Number.isFinite(pct)) return fallback;
  return `${pct.toFixed(2)}%`;
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  // ISO date — keep MM/YYYY for table density.
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const [, y, mo] = m;
  return `${mo}/${y}`;
}

function coverageBadge(quality: CompSalesSetResponse['coverage_quality']) {
  const cls: Record<typeof quality, string> = {
    high: 'bg-emerald-50 text-emerald-800 border-emerald-200',
    medium: 'bg-amber-50 text-amber-800 border-amber-200',
    low: 'bg-rose-50 text-rose-800 border-rose-200',
  };
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium border tabular-nums',
        cls[quality],
      )}
    >
      Coverage: {quality}
    </span>
  );
}

export default function CompSalesPanel({ dealId }: Props) {
  const [data, setData] = useState<CompSalesSetResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showWeighted, setShowWeighted] = useState(false);
  const [pendingId, setPendingId] = useState<string | null>(null);
  // Track which comp ids are excluded — derived client-side so the
  // checkbox state is stable while the network request is in flight.
  const [excluded, setExcluded] = useState<Set<string>>(new Set());

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.deals.compSales(dealId, signal);
        setData(res);
      } catch (e) {
        if ((e as Error).name === 'AbortError') return;
        setError((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [dealId],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const onToggleExclude = useCallback(
    async (transactionId: string | null) => {
      if (!transactionId) return;
      if (excluded.has(transactionId)) {
        // No "un-exclude" endpoint in this sprint — the panel is
        // additive only. The analyst removes an exclude by editing
        // ``field_overrides`` directly via the OverridePanel.
        return;
      }
      setPendingId(transactionId);
      try {
        const refreshed = await api.deals.excludeComp(dealId, transactionId);
        setData(refreshed);
        setExcluded((prev) => {
          const next = new Set(prev);
          next.add(transactionId);
          return next;
        });
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setPendingId(null);
      }
    },
    [dealId, excluded],
  );

  if (loading && !data) {
    return (
      <Card className="p-4">
        <div className="text-[12.5px] text-ink-500">Loading comparable sales…</div>
      </Card>
    );
  }
  if (error) {
    return (
      <Card className="p-4">
        <div className="text-[12.5px] text-rose-700">
          Failed to load comparable sales: {error}
        </div>
      </Card>
    );
  }
  if (!data) return null;

  const headlineMedian = data.derived_cap_rate_median;
  const headlineWeighted = data.derived_cap_rate_weighted;
  const headline = showWeighted ? headlineWeighted : headlineMedian;
  const headlineLabel = showWeighted ? 'weighted' : 'median';
  const excludedCount = data.total_count - data.transactions.length + excluded.size;
  const usableCount = data.transactions.length - excluded.size;

  return (
    <Card className="p-4">
      {/* ── Header callout ─────────────────────────────────────────── */}
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-[14px] font-semibold text-ink-900">
              Comparable Sales
            </h3>
            <AssumptionBadge source="om_comps" />
            {coverageBadge(data.coverage_quality)}
          </div>
          <div className="mt-1 text-[12.5px] text-ink-700 leading-snug">
            <span className="font-semibold tabular-nums">
              Derived cap rate: {fmtCap(headline)}
            </span>
            <span className="text-ink-500 ml-1">
              ({headlineLabel} of {usableCount} comp
              {usableCount === 1 ? '' : 's'}
              {excludedCount > 0 ? ` · ${excludedCount} excluded` : ''})
            </span>
          </div>
          {data.subject_market || data.subject_chain_scale ? (
            <div className="mt-0.5 text-[11px] text-ink-500 leading-snug">
              Subject:{' '}
              {data.subject_market ?? 'market unset'}
              {' · '}
              {data.subject_chain_scale ?? 'chain-scale unset'}
              {' · '}
              {data.lookback_years}-yr look-back
            </div>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => setShowWeighted((v) => !v)}
          className={cn(
            'shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded text-[11.5px] font-medium border tabular-nums',
            showWeighted
              ? 'bg-brand-50 border-brand-200 text-brand-800'
              : 'bg-white border-border text-ink-700 hover:bg-ink-50',
          )}
          disabled={data.derived_cap_rate_weighted == null}
          title={
            data.derived_cap_rate_weighted == null
              ? 'Weighted derivation unavailable — no subject market or chain-scale supplied.'
              : 'Toggle weighted (recency 70% / market 20% / chain-scale 10%) vs median'
          }
        >
          {showWeighted ? 'Showing weighted' : 'Show weighted'}
        </button>
      </div>

      {/* ── Comp table ────────────────────────────────────────────── */}
      <div className="border border-border rounded-md overflow-x-auto">
        <table className="w-full text-[12px] tabular-nums">
          <thead className="bg-ink-50 text-ink-600 uppercase tracking-wide text-[10px]">
            <tr>
              <th className="text-left px-2 py-1.5 font-semibold">Property</th>
              <th className="text-left px-2 py-1.5 font-semibold">Loc</th>
              <th className="text-left px-2 py-1.5 font-semibold">Date</th>
              <th className="text-right px-2 py-1.5 font-semibold">Keys</th>
              <th className="text-right px-2 py-1.5 font-semibold">Price</th>
              <th className="text-right px-2 py-1.5 font-semibold">$/Key</th>
              <th className="text-right px-2 py-1.5 font-semibold">NOI</th>
              <th className="text-right px-2 py-1.5 font-semibold">Cap</th>
              <th className="text-center px-2 py-1.5 font-semibold">Excl</th>
            </tr>
          </thead>
          <tbody>
            {data.transactions.length === 0 ? (
              <tr>
                <td
                  colSpan={9}
                  className="px-3 py-6 text-center text-[12px] text-ink-500"
                >
                  No comparable sales extracted from the OM yet.
                </td>
              </tr>
            ) : (
              data.transactions.map((row: CompTransactionRow, idx: number) => {
                const id = row.transaction_id;
                const isExcluded = id != null && excluded.has(id);
                return (
                  <tr
                    key={id ?? `row-${idx}`}
                    className={cn(
                      'border-t border-border',
                      isExcluded && 'opacity-40 line-through',
                    )}
                  >
                    <td className="px-2 py-1.5 text-ink-900 truncate max-w-[180px]">
                      {row.property_name ?? '—'}
                    </td>
                    <td className="px-2 py-1.5 text-ink-700">
                      {[row.city, row.state].filter(Boolean).join(', ') || '—'}
                    </td>
                    <td className="px-2 py-1.5 text-ink-700">
                      {fmtDate(row.sale_date)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-ink-700">
                      {row.keys != null ? fmtNumber(row.keys) : '—'}
                    </td>
                    <td className="px-2 py-1.5 text-right text-ink-900">
                      {fmtMoney(row.sale_price_usd)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-ink-700">
                      {fmtPerKey(row.sale_price_per_key_usd)}
                    </td>
                    <td className="px-2 py-1.5 text-right text-ink-700">
                      {fmtMoney(row.noi_usd)}
                    </td>
                    <td className="px-2 py-1.5 text-right font-medium text-ink-900">
                      {fmtCap(row.cap_rate_pct)}
                    </td>
                    <td className="px-2 py-1.5 text-center">
                      <input
                        type="checkbox"
                        aria-label={`Exclude ${row.property_name ?? 'comp'}`}
                        checked={isExcluded}
                        disabled={pendingId === id || isExcluded || id == null}
                        onChange={() => onToggleExclude(id)}
                        className="h-3.5 w-3.5 align-middle"
                      />
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {/* ── Weighting notes (small print) ──────────────────────────── */}
      {data.weighting_notes.length > 0 ? (
        <ul className="mt-3 text-[11px] text-ink-500 leading-snug space-y-0.5">
          {data.weighting_notes.map((n, i) => (
            <li key={i} className="flex gap-1">
              <span className="text-ink-300">•</span>
              <span>{n}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {/* ── Method tag ─────────────────────────────────────────────── */}
      <div className="mt-2 text-[10px] text-ink-400 uppercase tracking-wide">
        Method: {data.derived_cap_rate_method}
        {data.derived_cap_rate_method === 'weighted' && (
          <>
            {' '}— recency 70% / market 20% / chain-scale 10% ·{' '}
            <span className="tabular-nums">
              median {fmtCap(headlineMedian, 'n/a')}
            </span>
          </>
        )}
      </div>
    </Card>
  );
}
