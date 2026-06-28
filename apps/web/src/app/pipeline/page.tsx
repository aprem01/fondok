'use client';
//
// Multi-deal Pipeline view (Wave 3 W3.5).
//
// One table-shaped page that lets an analyst see every active deal at
// once, sortable + filterable, with portfolio-level KPIs at the top
// (deal count, median IRR, median $/key, deals meeting target).
//
// Backed by GET /deals/pipeline — see apps/worker/app/api/deals.py.
// When the worker isn't reachable (env-var unset) we render a friendly
// empty state instead of mock data; mock pipelines would invite
// real-vs-fake confusion the dashboard already wrestles with.
//
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  ArrowDown, ArrowUp, Plus, RefreshCw, Target, AlertCircle,
  LineChart as LineChartIcon,
} from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import {
  api, isWorkerConnected, PipelineDealRow, PipelineQuery, PipelineResponse,
  PipelineSort,
} from '@/lib/api';
import { cn, fmtCurrency, fmtPct } from '@/lib/format';
import { MetricLabel } from '@/components/help/MetricLabel';

// ─────────────────────────── filters/sort state ───────────────────────────

type State = 'all' | 'ONBOARDING' | 'VALIDATING' | 'READY';

type Filters = {
  state: State;
  minIrr: number | null;
  maxPerKey: number | null;
  sort: PipelineSort;
};

const DEFAULT_FILTERS: Filters = {
  state: 'all',
  minIrr: null,
  maxPerKey: null,
  sort: 'last_activity_desc',
};

const SORT_LABELS: Record<PipelineSort, string> = {
  irr_desc: 'IRR · highest first',
  irr_asc: 'IRR · lowest first',
  em_desc: 'Equity multiple · highest first',
  em_asc: 'Equity multiple · lowest first',
  per_key_asc: '$/key · lowest first',
  per_key_desc: '$/key · highest first',
  cap_rate_asc: 'Cap rate · lowest first',
  cap_rate_desc: 'Cap rate · highest first',
  noi_y1_desc: 'NOI Y1 · highest first',
  noi_y1_asc: 'NOI Y1 · lowest first',
  name_asc: 'Name · A → Z',
  name_desc: 'Name · Z → A',
  last_activity_desc: 'Last activity · most recent',
  last_activity_asc: 'Last activity · oldest',
};

// Tuple [token, column header label, sort key it toggles]. The header
// click toggles between asc/desc for the chosen column.
type ColumnSortable = { ascKey: PipelineSort; descKey: PipelineSort };

const COLUMN_SORTS: Record<string, ColumnSortable> = {
  name: { ascKey: 'name_asc', descKey: 'name_desc' },
  irr: { ascKey: 'irr_asc', descKey: 'irr_desc' },
  em: { ascKey: 'em_asc', descKey: 'em_desc' },
  per_key: { ascKey: 'per_key_asc', descKey: 'per_key_desc' },
  cap_rate: { ascKey: 'cap_rate_asc', descKey: 'cap_rate_desc' },
  noi_y1: { ascKey: 'noi_y1_asc', descKey: 'noi_y1_desc' },
  last_activity: {
    ascKey: 'last_activity_asc',
    descKey: 'last_activity_desc',
  },
};

// ─────────────────────────── helpers ───────────────────────────

const dashIfNull = (n: number | null | undefined, fmt: (v: number) => string) =>
  n == null ? <span className="text-ink-400">—</span> : fmt(n);

const relativeTime = (iso: string | null | undefined): string => {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  const now = Date.now();
  const mins = Math.round((now - t) / 60000);
  if (mins < 1) return 'moments ago';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
};

// ─────────────────────────── component ───────────────────────────

export default function PipelinePage() {
  const [data, setData] = useState<PipelineResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);

  const query: PipelineQuery = useMemo(() => {
    const q: PipelineQuery = { sort: filters.sort, limit: 200 };
    if (filters.state !== 'all') q.state = filters.state;
    if (filters.minIrr != null) q.min_irr = filters.minIrr;
    if (filters.maxPerKey != null) q.max_per_key = filters.maxPerKey;
    return q;
  }, [filters]);

  const load = useMemo(
    () => async (signal?: AbortSignal) => {
      if (!isWorkerConnected()) {
        setData(null);
        setError('Worker not connected');
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const res = await api.deals.pipeline(query, signal);
        setData(res);
      } catch (e) {
        if ((e as { name?: string }).name === 'AbortError') return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [query],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    void load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const deals = data?.deals ?? [];
  const summary = data?.summary;

  const setSortFromColumn = (col: keyof typeof COLUMN_SORTS) => {
    const { ascKey, descKey } = COLUMN_SORTS[col];
    setFilters((f) => ({
      ...f,
      sort: f.sort === descKey ? ascKey : descKey,
    }));
  };

  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <PageHeader
        eyebrow={
          summary ? `${summary.deal_count} active deals` : 'Loading pipeline'
        }
        title="Pipeline"
        subtitle="Every active deal at a glance — sortable, filterable, with portfolio-level returns at the top."
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              onClick={() => void load()}
              disabled={loading}
            >
              <RefreshCw
                size={14}
                className={cn(loading && 'animate-spin')}
              />
              Refresh
            </Button>
            <Link href="/projects/new" data-tour="new-deal">
              <Button variant="primary">
                <Plus size={14} /> New Project
              </Button>
            </Link>
          </div>
        }
      />

      {/* Portfolio KPI strip — 4 cards. */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        <Card className="p-5">
          <MetricLabel
            label="Deals in Pipeline"
            tip="Total active deals (excluding archived) in your tenant."
            eyebrow
          />
          <div className="text-display-lg text-ink-900 mt-2 tabular-nums font-display">
            {summary?.deal_count ?? '—'}
          </div>
          {summary && (
            <div className="text-[11.5px] mt-1.5 tabular-nums text-ink-500">
              {Object.entries(summary.deals_by_state)
                .map(([s, n]) => `${n} ${s.toLowerCase()}`)
                .join(' · ') || 'no state yet'}
            </div>
          )}
        </Card>
        <Card className="p-5">
          <MetricLabel
            label="Median Levered IRR"
            tip="The midpoint IRR across deals with a Run-Model snapshot. Half above, half below."
            eyebrow
          />
          <div className="text-display-lg text-ink-900 mt-2 tabular-nums font-display">
            {summary?.median_irr != null ? fmtPct(summary.median_irr) : '—'}
          </div>
          {summary?.p25_irr != null && summary?.p75_irr != null && (
            <div className="text-[11.5px] mt-1.5 tabular-nums text-ink-500">
              p25 {fmtPct(summary.p25_irr)} · p75 {fmtPct(summary.p75_irr)}
            </div>
          )}
        </Card>
        <Card className="p-5">
          <MetricLabel
            label="Median $/Key"
            tip="The midpoint price-per-key across deals — a quick read on how the pipeline is priced overall."
            eyebrow
          />
          <div className="text-display-lg text-ink-900 mt-2 tabular-nums font-display">
            {summary?.median_per_key != null
              ? fmtCurrency(summary.median_per_key, { compact: true })
              : '—'}
          </div>
          {summary?.median_cap_rate != null && (
            <div className="text-[11.5px] mt-1.5 tabular-nums text-ink-500">
              Median exit cap {fmtPct(summary.median_cap_rate)}
            </div>
          )}
        </Card>
        <Card tone="luxe" className="p-5 pl-6">
          <MetricLabel
            label="Meeting Target IRR"
            tip="Deals whose latest levered IRR clears the deal-level target the analyst set. Deals with no target are excluded."
            eyebrow
          />
          <div className="text-display-lg text-ink-900 mt-2 tabular-nums font-display">
            {summary
              ? `${summary.deals_meeting_target_irr}/${summary.deals_with_target_irr || 0}`
              : '—'}
          </div>
          {summary && summary.deals_with_target_irr === 0 && (
            <div className="text-[11.5px] mt-1.5 text-ink-500">
              Set a target on a deal to populate this KPI
            </div>
          )}
        </Card>
      </div>

      {/* Filter bar */}
      <Card className="mb-4 p-4 flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1 min-w-[160px]">
          <label className="text-[11px] text-ink-500 uppercase tracking-wide">
            State
          </label>
          <select
            value={filters.state}
            onChange={(e) =>
              setFilters((f) => ({ ...f, state: e.target.value as State }))
            }
            className="border border-border rounded-md px-3 py-1.5 text-[13px] bg-white"
          >
            <option value="all">All states</option>
            <option value="ONBOARDING">Onboarding</option>
            <option value="VALIDATING">Validating</option>
            <option value="READY">Ready</option>
          </select>
        </div>
        <div className="flex flex-col gap-1 min-w-[160px]">
          <label className="text-[11px] text-ink-500 uppercase tracking-wide">
            Min IRR
          </label>
          <input
            type="number"
            step="0.01"
            min={0}
            max={1}
            placeholder="e.g. 0.15"
            value={filters.minIrr ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              setFilters((f) => ({
                ...f,
                minIrr: v === '' ? null : Number(v),
              }));
            }}
            className="border border-border rounded-md px-3 py-1.5 text-[13px] bg-white tabular-nums"
          />
        </div>
        <div className="flex flex-col gap-1 min-w-[160px]">
          <label className="text-[11px] text-ink-500 uppercase tracking-wide">
            Max $/Key
          </label>
          <input
            type="number"
            step="10000"
            min={0}
            placeholder="e.g. 300000"
            value={filters.maxPerKey ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              setFilters((f) => ({
                ...f,
                maxPerKey: v === '' ? null : Number(v),
              }));
            }}
            className="border border-border rounded-md px-3 py-1.5 text-[13px] bg-white tabular-nums"
          />
        </div>
        <div className="flex flex-col gap-1 min-w-[260px]">
          <label className="text-[11px] text-ink-500 uppercase tracking-wide">
            Sort by
          </label>
          <select
            value={filters.sort}
            onChange={(e) =>
              setFilters((f) => ({
                ...f,
                sort: e.target.value as PipelineSort,
              }))
            }
            className="border border-border rounded-md px-3 py-1.5 text-[13px] bg-white"
          >
            {Object.entries(SORT_LABELS).map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <Button
          variant="secondary"
          onClick={() => setFilters(DEFAULT_FILTERS)}
        >
          Reset
        </Button>
      </Card>

      {/* Error banner */}
      {error && (
        <Card className="mb-4 p-4 flex items-center gap-3 border-danger-200">
          <AlertCircle size={16} className="text-danger-700" />
          <div className="text-[13px] text-ink-900">
            Pipeline failed to load: {error}
          </div>
        </Card>
      )}

      {/* Table */}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead className="bg-ink-50 sticky top-0 z-10">
              <tr>
                <Th
                  label="Deal"
                  col="name"
                  currentSort={filters.sort}
                  onSort={setSortFromColumn}
                />
                <th className="px-4 py-3 text-left text-[11.5px] font-semibold text-ink-700 uppercase tracking-wide">
                  State
                </th>
                <Th
                  label="$/Key"
                  col="per_key"
                  currentSort={filters.sort}
                  onSort={setSortFromColumn}
                  align="right"
                />
                <Th
                  label="NOI Y1"
                  col="noi_y1"
                  currentSort={filters.sort}
                  onSort={setSortFromColumn}
                  align="right"
                />
                <Th
                  label="Exit Cap"
                  col="cap_rate"
                  currentSort={filters.sort}
                  onSort={setSortFromColumn}
                  align="right"
                />
                <Th
                  label="Lev. IRR"
                  col="irr"
                  currentSort={filters.sort}
                  onSort={setSortFromColumn}
                  align="right"
                />
                <Th
                  label="EM"
                  col="em"
                  currentSort={filters.sort}
                  onSort={setSortFromColumn}
                  align="right"
                />
                <th className="px-4 py-3 text-right text-[11.5px] font-semibold text-ink-700 uppercase tracking-wide">
                  DSCR Y1
                </th>
                <th className="px-4 py-3 text-right text-[11.5px] font-semibold text-ink-700 uppercase tracking-wide">
                  Target
                </th>
                <Th
                  label="Last Activity"
                  col="last_activity"
                  currentSort={filters.sort}
                  onSort={setSortFromColumn}
                  align="right"
                />
              </tr>
            </thead>
            <tbody>
              {deals.length === 0 && !loading && (
                <tr>
                  <td colSpan={10} className="px-6 py-16 text-center">
                    <div className="flex flex-col items-center gap-3">
                      <LineChartIcon
                        size={28}
                        className="text-ink-300"
                        strokeWidth={1.5}
                      />
                      <div className="text-[14px] font-medium text-ink-900">
                        No deals match your filters
                      </div>
                      <div className="text-[12.5px] text-ink-500 max-w-md">
                        Loosen the filter bar above or start a fresh deal to
                        see it land here.
                      </div>
                      <Link href="/projects/new">
                        <Button variant="primary">
                          <Plus size={14} /> Create your first deal
                        </Button>
                      </Link>
                    </div>
                  </td>
                </tr>
              )}
              {deals.map((d) => (
                <DealRow key={d.deal_id} d={d} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {data && data.total_count > deals.length && (
        <div className="mt-3 text-[12px] text-ink-500 text-center">
          Showing {deals.length} of {data.total_count} deals — refine the
          filters to narrow further.
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── sub-components ───────────────────────────

function Th({
  label,
  col,
  currentSort,
  onSort,
  align = 'left',
}: {
  label: string;
  col: keyof typeof COLUMN_SORTS;
  currentSort: PipelineSort;
  onSort: (col: keyof typeof COLUMN_SORTS) => void;
  align?: 'left' | 'right';
}) {
  const { ascKey, descKey } = COLUMN_SORTS[col];
  const active = currentSort === ascKey || currentSort === descKey;
  const direction = currentSort === ascKey ? 'asc' : 'desc';
  return (
    <th
      className={cn(
        'px-4 py-3 text-[11.5px] font-semibold text-ink-700 uppercase tracking-wide cursor-pointer select-none hover:bg-ink-100',
        align === 'right' ? 'text-right' : 'text-left',
      )}
      onClick={() => onSort(col)}
    >
      <span
        className={cn(
          'inline-flex items-center gap-1',
          active ? 'text-ink-900' : '',
        )}
      >
        {label}
        {active && direction === 'desc' && (
          <ArrowDown size={11} className="text-ink-700" />
        )}
        {active && direction === 'asc' && (
          <ArrowUp size={11} className="text-ink-700" />
        )}
      </span>
    </th>
  );
}

function DealRow({ d }: { d: PipelineDealRow }) {
  return (
    <tr className="border-t hairline hover:bg-ink-50/60 transition-colors">
      <td className="px-4 py-3">
        <Link
          href={`/projects/${d.deal_id}`}
          className="block group focus:outline-none"
        >
          <div className="font-medium text-ink-900 group-hover:text-brand-700 truncate max-w-[280px]">
            {d.name}
          </div>
          <div className="text-[11.5px] text-ink-500 truncate max-w-[280px]">
            {[d.city, d.brand, d.keys != null ? `${d.keys} keys` : null]
              .filter(Boolean)
              .join(' · ') || '—'}
          </div>
        </Link>
      </td>
      <td className="px-4 py-3">
        <StatusBadge value={prettyState(d.state)} />
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-ink-900">
        {dashIfNull(d.price_per_key, (v) =>
          fmtCurrency(v, { compact: true }),
        )}
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-ink-900">
        {dashIfNull(d.noi_y1, (v) => fmtCurrency(v, { compact: true }))}
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-ink-900">
        {dashIfNull(d.exit_cap_rate, (v) => fmtPct(v, 2))}
      </td>
      <td
        className={cn(
          'px-4 py-3 text-right tabular-nums font-medium',
          d.target_irr_met === true && 'text-success-700',
          d.target_irr_met === false && 'text-danger-700',
          d.target_irr_met == null && 'text-ink-900',
        )}
      >
        {dashIfNull(d.levered_irr, (v) => fmtPct(v))}
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-ink-900">
        {dashIfNull(d.equity_multiple, (v) => `${v.toFixed(2)}x`)}
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-ink-900">
        {dashIfNull(d.dscr_y1, (v) => `${v.toFixed(2)}x`)}
      </td>
      <td className="px-4 py-3 text-right tabular-nums">
        {d.target_irr == null ? (
          <span className="text-ink-400">—</span>
        ) : (
          <span className="inline-flex items-center gap-1 text-ink-700">
            <Target size={11} className="text-ink-500" />
            {fmtPct(d.target_irr)}
          </span>
        )}
      </td>
      <td className="px-4 py-3 text-right tabular-nums text-ink-500">
        {relativeTime(d.last_activity_at)}
      </td>
    </tr>
  );
}

function prettyState(s: string): string {
  if (s === 'ONBOARDING') return 'Draft';
  if (s === 'VALIDATING') return 'Active';
  if (s === 'READY') return 'Ready';
  return s;
}
