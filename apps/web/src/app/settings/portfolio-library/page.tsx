'use client';

/**
 * Settings → Portfolio P&L Library.
 *
 * Wave 4 W4.1. Firm-level surface for managing portfolio benchmark
 * roll-ups. Every active entry is queried by the engine_runner at
 * model time — entries whose ``chain_scales_covered`` overlap the
 * subject deal's chain scale (within the 3-year vintage look-back)
 * feed the per-ratio median into the ``portfolio_pnl`` candidate of
 * the precedence chain.
 */

import { Database, Plus, Search, ToggleLeft, ToggleRight } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';
import { api, isWorkerConnected, type PortfolioLibraryEntry } from '@/lib/api';
import PortfolioLibraryEntryDetail from '@/components/settings/PortfolioLibraryEntryDetail';
import PortfolioLibraryUploadPanel from '@/components/settings/PortfolioLibraryUploadPanel';

type Filter = 'all' | 'active' | 'inactive';

export default function PortfolioLibraryPage() {
  const { toast } = useToast();
  const workerConnected = isWorkerConnected();
  const [entries, setEntries] = useState<PortfolioLibraryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<PortfolioLibraryEntry | null>(null);
  const [showUpload, setShowUpload] = useState(false);

  const refresh = useCallback(async () => {
    if (!workerConnected) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const rows = await api.portfolioLibrary.list();
      setEntries(rows);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast(`Couldn't load library: ${msg}`, { type: 'error' });
    } finally {
      setLoading(false);
    }
  }, [workerConnected, toast]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const filtered = useMemo(() => {
    const lower = query.trim().toLowerCase();
    return entries.filter((e) => {
      if (filter === 'active' && !e.is_active) return false;
      if (filter === 'inactive' && e.is_active) return false;
      if (!lower) return true;
      if (e.name.toLowerCase().includes(lower)) return true;
      if (
        e.chain_scales_covered.some((cs) => cs.toLowerCase().includes(lower))
      )
        return true;
      return false;
    });
  }, [entries, filter, query]);

  const onToggleActive = async (entry: PortfolioLibraryEntry) => {
    try {
      if (entry.is_active) {
        await api.portfolioLibrary.deactivate(entry.id);
        toast(`Deactivated "${entry.name}"`, { type: 'success' });
      } else {
        await api.portfolioLibrary.activate(entry.id);
        toast(`Reactivated "${entry.name}"`, { type: 'success' });
      }
      void refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast(`Couldn't update: ${msg}`, { type: 'error' });
    }
  };

  return (
    <div className="px-8 py-8 max-w-[1100px]">
      <div className="text-[11.5px] text-ink-500 mb-2">
        <Link href="/settings" className="hover:text-ink-900">
          Settings
        </Link>
        <span className="mx-1.5">/</span>
        <span className="text-ink-700">Portfolio Library</span>
      </div>
      <PageHeader
        title="Portfolio P&L Library"
        subtitle="Firm-level benchmark roll-ups applied across every deal that matches the chain scale."
      />

      <div className="flex items-center justify-between gap-2 mb-4">
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search
              size={12}
              aria-hidden="true"
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-500"
            />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name or chain scale…"
              className="pl-7 pr-3 py-1.5 w-[280px] text-[12.5px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
            />
          </div>
          <div className="flex items-center bg-white border border-border rounded-md p-0.5 inline-flex">
            {(['all', 'active', 'inactive'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={
                  'px-2.5 py-1 text-[11.5px] rounded transition-colors ' +
                  (filter === f
                    ? 'bg-brand-50 text-brand-700 font-medium'
                    : 'text-ink-500 hover:text-ink-900')
                }
              >
                {f === 'all' ? 'All' : f === 'active' ? 'Active' : 'Inactive'}
              </button>
            ))}
          </div>
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => setShowUpload(true)}
          disabled={!workerConnected}
        >
          <Plus size={12} aria-hidden="true" />
          Add portfolio benchmark
        </Button>
      </div>

      {!workerConnected ? (
        <Card className="p-8 text-center">
          <p className="text-[13px] text-ink-700">
            Worker not connected. Set <code>NEXT_PUBLIC_WORKER_URL</code> and
            reload.
          </p>
        </Card>
      ) : loading ? (
        <Card className="p-8 text-center">
          <p className="text-[12.5px] text-ink-500">Loading library…</p>
        </Card>
      ) : entries.length === 0 ? (
        <Card className="p-8 text-center">
          <div className="w-12 h-12 mx-auto mb-3 rounded-full bg-brand-50 flex items-center justify-center">
            <Database size={20} className="text-brand-700" aria-hidden="true" />
          </div>
          <h3 className="text-[14px] font-semibold text-ink-900 mb-1">
            No portfolio benchmarks yet
          </h3>
          <p className="text-[12.5px] text-ink-500 mb-4 max-w-md mx-auto leading-relaxed">
            Add your firm&rsquo;s in-house roll-up so Fondok applies your
            portfolio op-ratios across every deal that matches the chain scale.
            Beats generic HostStats / CBRE defaults every time.
          </p>
          <ul className="text-[12px] text-ink-500 max-w-sm mx-auto mb-4 space-y-1 text-left list-disc list-inside">
            <li>Apollo Select-Service Marriott 2024 portfolio</li>
            <li>IHG Full-Service 2024 portfolio</li>
            <li>Independent boutique 2024 portfolio</li>
          </ul>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setShowUpload(true)}
          >
            <Plus size={12} aria-hidden="true" />
            Add your first benchmark
          </Button>
          <p className="text-[11px] text-ink-500 mt-4">
            Need methodology context?{' '}
            <Link
              href="/methodology"
              className="text-brand-700 hover:underline"
            >
              See how the precedence chain works
            </Link>
            .
          </p>
        </Card>
      ) : filtered.length === 0 ? (
        <Card className="p-8 text-center">
          <p className="text-[12.5px] text-ink-500">
            No entries match your filter.
          </p>
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]">
              <thead className="bg-ink-50 text-ink-700">
                <tr>
                  <Th>Name</Th>
                  <Th align="right">Vintage</Th>
                  <Th align="right">Assets</Th>
                  <Th align="right">Rooms</Th>
                  <Th>Chain scales</Th>
                  <Th align="center">Active</Th>
                  <Th>Updated</Th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((entry) => (
                  <tr
                    key={entry.id}
                    onClick={() => setSelected(entry)}
                    className="border-t border-border hover:bg-ink-50 cursor-pointer"
                  >
                    <Td>
                      <div className="font-medium text-ink-900">
                        {entry.name}
                      </div>
                      {entry.description && (
                        <div className="text-[11px] text-ink-500 truncate max-w-[280px]">
                          {entry.description}
                        </div>
                      )}
                    </Td>
                    <Td align="right">{entry.vintage_year}</Td>
                    <Td align="right">{entry.asset_count}</Td>
                    <Td align="right">
                      {entry.total_rooms_modeled.toLocaleString()}
                    </Td>
                    <Td>
                      <div className="flex flex-wrap gap-1">
                        {entry.chain_scales_covered.length === 0 ? (
                          <span className="text-[11px] text-ink-500">
                            (covers all)
                          </span>
                        ) : (
                          entry.chain_scales_covered.map((cs) => (
                            <Badge key={cs} tone="blue">
                              {cs}
                            </Badge>
                          ))
                        )}
                      </div>
                    </Td>
                    <Td align="center">
                      <button
                        type="button"
                        aria-label={
                          entry.is_active
                            ? `Deactivate ${entry.name}`
                            : `Activate ${entry.name}`
                        }
                        onClick={(e) => {
                          e.stopPropagation();
                          void onToggleActive(entry);
                        }}
                        className="inline-flex items-center text-ink-700 hover:text-ink-900"
                      >
                        {entry.is_active ? (
                          <ToggleRight
                            size={20}
                            className="text-success-500"
                          />
                        ) : (
                          <ToggleLeft size={20} className="text-ink-400" />
                        )}
                      </button>
                    </Td>
                    <Td>
                      <span className="text-[11.5px] text-ink-500">
                        {_fmtRelative(entry.updated_at)}
                      </span>
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {selected && (
        <PortfolioLibraryEntryDetail
          entry={selected}
          onClose={() => setSelected(null)}
          onMutated={() => {
            void refresh();
            setSelected(null);
          }}
        />
      )}
      {showUpload && (
        <PortfolioLibraryUploadPanel
          onClose={() => setShowUpload(false)}
          onCreated={() => {
            void refresh();
            setShowUpload(false);
          }}
        />
      )}
    </div>
  );
}

function Th({
  children,
  align,
}: {
  children: React.ReactNode;
  align?: 'left' | 'right' | 'center';
}) {
  return (
    <th
      className={
        'px-3 py-2 font-medium text-[11px] uppercase tracking-wide ' +
        (align === 'right'
          ? 'text-right'
          : align === 'center'
            ? 'text-center'
            : 'text-left')
      }
    >
      {children}
    </th>
  );
}

function Td({
  children,
  align,
}: {
  children: React.ReactNode;
  align?: 'left' | 'right' | 'center';
}) {
  return (
    <td
      className={
        'px-3 py-2.5 ' +
        (align === 'right'
          ? 'text-right'
          : align === 'center'
            ? 'text-center'
            : 'text-left')
      }
    >
      {children}
    </td>
  );
}

function _fmtRelative(iso: string): string {
  try {
    const d = new Date(iso);
    const ms = Date.now() - d.getTime();
    const days = Math.floor(ms / (1000 * 60 * 60 * 24));
    if (days < 1) return 'today';
    if (days < 2) return 'yesterday';
    if (days < 30) return `${days}d ago`;
    return d.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return iso;
  }
}
