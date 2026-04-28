'use client';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import {
  Plus, Search, ChevronDown, LayoutGrid, List, Building2,
  MapPin, AlertTriangle, Upload, Loader2, RefreshCw,
} from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import { projects as mockProjects, projectStatuses, Project } from '@/lib/mockData';
import { cn } from '@/lib/format';
import { useDeals } from '@/lib/hooks/useDeals';
import { WorkerDeal } from '@/lib/api';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';

const projectMenu = (id: string) => [
  { label: 'View Details', onSelect: () => { window.location.href = `/projects/${id}`; } },
  { label: 'Export Excel', onSelect: () => {} },
  { label: 'Export Memo', onSelect: () => {} },
  { label: 'Archive', onSelect: () => {}, danger: true },
];

const riskTone = (r: string | null | undefined) =>
  r === 'Low' ? 'text-success-700 bg-success-50' :
  r === 'Medium' ? 'text-warn-700 bg-warn-50' :
  r === 'High' ? 'text-danger-700 bg-danger-50' :
  'text-ink-500 bg-ink-300/15';

interface DisplayDeal {
  id: string;
  name: string;
  city: string;
  keys: number;
  service: string;
  status: string;
  dealStage: string;
  revpar: number | null;
  irr: number | null;
  noi: number | null;
  risk: string | null;
  aiConfidence: number; // 0–100
  assignee: string;
  docs: string;
  updatedAt: string;
  noDocs: boolean;
  isMock: boolean;
}

function fromMockProject(p: Project): DisplayDeal {
  return {
    id: String(p.id),
    name: p.name,
    city: p.city,
    keys: p.keys,
    service: p.service,
    status: p.status,
    dealStage: p.dealStage,
    revpar: p.revpar,
    irr: p.irr,
    noi: p.noi ?? null,
    risk: p.risk,
    aiConfidence: p.aiConfidence,
    assignee: p.assignee,
    docs: p.docs,
    updatedAt: p.updatedAt,
    noDocs: !!p.noDocs,
    isMock: true,
  };
}

function fromWorkerDeal(d: WorkerDeal): DisplayDeal {
  return {
    id: d.id,
    name: d.name,
    city: d.city ?? '—',
    keys: d.keys ?? 0,
    service: d.service ?? '—',
    status: d.status || 'Draft',
    dealStage: d.deal_stage || 'Teaser',
    revpar: null,
    irr: null,
    noi: null,
    risk: d.risk,
    aiConfidence: Math.round((d.ai_confidence ?? 0) * 100),
    assignee: '—',
    docs: '0/0',
    updatedAt: d.updated_at ? new Date(d.updated_at).toLocaleDateString() : '—',
    noDocs: true,
    isMock: false,
  };
}

export default function ProjectsPage() {
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const [filter, setFilter] = useState<string>('All Status');
  const [search, setSearch] = useState('');
  const [filterOpen, setFilterOpen] = useState(false);

  const { deals, loading, error, fromMock, refresh } = useDeals();

  // Always show the mock projects so the demo deals (Kimpton Angler etc.) stay
  // available alongside any real worker deals while we're still mid-migration.
  const display: DisplayDeal[] = useMemo(() => {
    const workerRows = deals.map(fromWorkerDeal);
    const mockRows = mockProjects.map(fromMockProject);
    if (fromMock) return mockRows;
    // De-dupe by id (worker uses uuid, mock uses numeric).
    const seen = new Set(workerRows.map((d) => d.id));
    return [...workerRows, ...mockRows.filter((m) => !seen.has(m.id))];
  }, [deals, fromMock]);

  const filtered = display.filter((p) =>
    (filter === 'All Status' || p.status === filter) &&
    (!search || p.name.toLowerCase().includes(search.toLowerCase()) || (p.city || '').toLowerCase().includes(search.toLowerCase()))
  );

  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <PageHeader
        title="Projects"
        subtitle={`${display.length} total projects · ${display.filter((p) => p.status !== 'Archived').length} active`}
        action={
          <Link href="/projects/new">
            <Button variant="primary"><Plus size={14} /> New Project</Button>
          </Link>
        }
      />

      <IntroCard
        dismissKey="projects-list"
        title="Your deal pipeline"
        body={
          <>
            Each card below is one hotel deal you&apos;re evaluating. The
            <span className="font-semibold"> AI Confidence</span> score (0–100) tells you
            how complete our model is — green means we&apos;ve extracted enough data to be
            confident in the numbers, red means we&apos;re still missing key documents.
            Click any deal to open its full underwriting.
          </>
        }
      />

      {/* Toolbar */}
      <div className="flex items-center gap-3 mb-5">
        <div className="relative flex-1 max-w-md">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search projects..."
            className="w-full pl-9 pr-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          />
        </div>

        <div className="relative">
          <button onClick={() => setFilterOpen(!filterOpen)}
            className="flex items-center gap-2 px-3 py-2 text-[12.5px] bg-white border border-border rounded-md hover:bg-ink-300/10">
            {filter === 'All Status' ? 'All' : filter} <ChevronDown size={13} className="text-ink-400" />
          </button>
          {filterOpen && (
            <div className="absolute right-0 top-full mt-1 bg-white border border-border rounded-lg shadow-lg py-1 z-10 min-w-[140px]">
              {projectStatuses.map(s => (
                <button key={s} onClick={() => { setFilter(s); setFilterOpen(false); }}
                  className="w-full px-3 py-2 text-[12.5px] hover:bg-ink-300/10 text-left">
                  {s === 'All Status' ? 'All' : s}
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center bg-white border border-border rounded-md p-0.5">
          <button onClick={() => setView('grid')}
            className={cn('p-1.5 rounded', view === 'grid' ? 'bg-ink-300/30 text-ink-900' : 'text-ink-400')}>
            <LayoutGrid size={14} />
          </button>
          <button onClick={() => setView('list')}
            className={cn('p-1.5 rounded', view === 'list' ? 'bg-ink-300/30 text-ink-900' : 'text-ink-400')}>
            <List size={14} />
          </button>
        </div>
      </div>

      {error && !fromMock && (
        <Card className="p-5 mb-5 border-danger-500/30 bg-danger-50">
          <div className="flex items-start gap-3">
            <AlertTriangle size={16} className="text-danger-700 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="text-[13px] font-semibold text-danger-700">Couldn’t reach the worker</div>
              <p className="text-[12px] text-danger-700/80 mt-1">{error}</p>
            </div>
            <Button variant="secondary" size="sm" onClick={refresh}><RefreshCw size={12} /> Retry</Button>
          </div>
        </Card>
      )}

      {loading && deals.length === 0 ? (
        <div className="grid grid-cols-3 gap-4">
          {[0, 1, 2].map((i) => (
            <Card key={i} className="p-5 animate-pulse">
              <div className="h-10 bg-ink-300/30 rounded mb-3" />
              <div className="h-3 bg-ink-300/20 rounded w-2/3 mb-2" />
              <div className="h-3 bg-ink-300/20 rounded w-1/3 mb-4" />
              <div className="h-12 bg-ink-300/20 rounded" />
            </Card>
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <Card className="p-12 text-center">
          <Building2 size={36} className="text-ink-400 mx-auto mb-3" />
          <div className="text-[14px] font-semibold text-ink-900">No deals yet</div>
          <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
            A deal is one hotel acquisition you&apos;re evaluating. Click <span className="font-medium">+ New Project</span> to set up
            your first one — you&apos;ll add the deal name, market, and key count, then drop in
            the offering memorandum and T-12 to start underwriting.
          </p>
          <div className="mt-5">
            <Link href="/projects/new">
              <Button variant="primary"><Plus size={14} /> New Project</Button>
            </Link>
          </div>
        </Card>
      ) : view === 'grid' ? (
        <div className="grid grid-cols-3 gap-4">
          {filtered.map(p => (
            <Link key={p.id} href={`/projects/${p.id}`}>
              <Card className="p-5 hover:shadow-md transition-shadow cursor-pointer">
                <div className="flex items-start justify-between mb-3">
                  <div className="flex items-start gap-3 flex-1">
                    <div className="w-10 h-10 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
                      <Building2 size={18} className="text-brand-500" />
                    </div>
                    <div className="min-w-0">
                      <div className="text-[14px] font-semibold text-ink-900 truncate">{p.name}</div>
                      <div className="flex items-center gap-1 text-[11.5px] text-ink-500 mt-0.5">
                        <MapPin size={10} /> {p.city}
                      </div>
                    </div>
                  </div>
                  <span className="text-[11px] text-ink-500 whitespace-nowrap">{p.keys} keys</span>
                </div>

                <div className="flex items-center gap-1.5 mb-4">
                  <StatusBadge value={p.status} />
                  <StatusBadge value={p.dealStage} />
                </div>

                {p.noDocs ? (
                  <>
                    <div className="bg-warn-50 border border-warn-500/30 rounded-md px-3 py-2 mb-3 flex items-center gap-2">
                      <AlertTriangle size={12} className="text-warn-700" />
                      <span className="text-[11.5px] text-warn-700 font-medium">No documents uploaded</span>
                    </div>
                    <div className="border border-dashed border-ink-300 rounded-md p-4 text-center mb-3">
                      <Upload size={16} className="text-ink-400 mx-auto mb-1" />
                      <div className="text-[11px] text-ink-500 leading-relaxed">
                        Upload documents to unlock modeling — T-12, rent roll, or offering memorandum
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div className="grid grid-cols-3 gap-2 mb-4">
                      <div>
                        <div className="text-[10px] text-ink-500 uppercase tracking-wide">RevPAR</div>
                        <div className="text-[14px] font-semibold tabular-nums">{p.revpar != null ? `$${p.revpar}` : '—'}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-ink-500 uppercase tracking-wide">IRR</div>
                        <div className="text-[14px] font-semibold tabular-nums">{p.irr != null ? `${p.irr.toFixed(2)}%` : '—'}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-ink-500 uppercase tracking-wide">Risk</div>
                        <div className={cn('text-[12px] font-medium px-1.5 py-0.5 rounded inline-block', riskTone(p.risk))}>
                          {p.risk ?? '—'}
                        </div>
                      </div>
                    </div>
                    <div>
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-[11px] text-ink-500">AI Confidence</span>
                        <span className="text-[11.5px] font-semibold tabular-nums">{p.aiConfidence}%</span>
                      </div>
                      <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
                        <div className="h-full bg-brand-500" style={{ width: `${p.aiConfidence}%` }} />
                      </div>
                    </div>
                  </>
                )}

                <div className="flex items-center justify-between pt-4 mt-3 border-t border-border">
                  <div className="flex items-center gap-2">
                    <div className="w-6 h-6 rounded-full bg-ink-300/30 flex items-center justify-center text-[10px] font-semibold">{p.assignee}</div>
                    <span className="text-[11px] text-ink-500">{p.updatedAt}</span>
                  </div>
                  <span className="text-[11px] text-ink-500">{p.docs} docs</span>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <Card>
          {/* Header row */}
          <div className="flex items-center gap-3 px-5 py-2.5 border-b border-border bg-ink-100/40 text-[10.5px] font-semibold uppercase tracking-wide text-ink-500">
            <div className="w-9 flex-shrink-0" aria-hidden="true" />
            <div className="flex-1 min-w-0">Project</div>
            <div className="w-20 flex justify-end">
              <MetricLabel label="Stage" tip="Where this deal is in the acquisition process. Teaser → Under NDA → LOI → PSA → Closed." />
            </div>
            <div className="w-20 flex justify-end">
              <MetricLabel label="RevPAR" tip="Revenue per Available Room — average daily rate × occupancy. The single best yardstick of a hotel's revenue performance." />
            </div>
            <div className="w-24 flex justify-end">
              <MetricLabel label="NOI" tip="Net Operating Income — revenue minus operating expenses, before debt and capex. The truest measure of the hotel's earning power." />
            </div>
            <div className="w-16 flex justify-end">
              <MetricLabel label="IRR" tip="Internal Rate of Return — the annualized return on equity over the hold period. The headline return metric." />
            </div>
            <div className="w-16 flex justify-center">
              <MetricLabel label="Risk" tip="Fondok's overall risk grade for the deal — combines market, brand, debt, and execution risk." />
            </div>
            <div className="w-24 flex justify-end">
              <MetricLabel label="AI Conf." tip="How confident the AI is in the model, based on document completeness and extraction quality. Climbs toward 100% as you upload the full doc set." />
            </div>
            <div className="w-14 flex justify-end">
              <MetricLabel label="Docs" tip="Number of documents uploaded vs. the underwriting checklist (OM, T-12, STR report, rent roll, PIP estimate, etc.)." />
            </div>
            <div className="w-16 text-right">Updated</div>
            <div className="w-7 text-center">Owner</div>
            <div className="w-7" aria-hidden="true" />
          </div>
          {filtered.map((p, i) => (
            <Link key={p.id} href={`/projects/${p.id}`}
              className={cn(
                'flex items-center gap-3 px-5 py-3.5 hover:bg-ink-300/10 transition-colors',
                i < filtered.length - 1 && 'border-b border-border'
              )}>
              <div className="w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
                <Building2 size={16} className="text-brand-500" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <div className="text-[13.5px] font-medium text-ink-900 truncate">{p.name}</div>
                  <StatusBadge value={p.status} />
                </div>
                <div className="text-[12px] text-ink-500 mt-0.5">{p.city} · {p.keys} keys · {p.service}</div>
              </div>
              <div className="w-20 flex justify-end">
                <StatusBadge value={p.dealStage} />
              </div>
              <div className="text-[12.5px] tabular-nums w-20 text-right">{p.revpar != null ? `$${p.revpar}` : '—'}</div>
              <div className="text-[12.5px] tabular-nums w-24 text-right">{p.noi != null ? `$${(p.noi / 1e6).toFixed(2)}M` : '—'}</div>
              <div className="text-[12.5px] tabular-nums w-16 text-right font-medium">{p.irr != null ? `${p.irr.toFixed(2)}%` : '—'}</div>
              <div className="w-16 flex justify-center">
                <span className={cn('text-[11.5px] font-medium px-2 py-0.5 rounded text-center', riskTone(p.risk))}>{p.risk ?? '—'}</span>
              </div>
              <div className="w-24">
                <div className="flex items-center gap-1.5">
                  <div className="flex-1 h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
                    <div className="h-full bg-brand-500" style={{ width: `${p.aiConfidence}%` }} />
                  </div>
                  <span className="text-[11px] tabular-nums text-ink-700 w-7 text-right">{p.aiConfidence}%</span>
                </div>
              </div>
              <div className="text-[12px] tabular-nums w-14 text-right text-ink-700">{p.docs}</div>
              <div className="text-[11.5px] w-16 text-right text-ink-500">{p.updatedAt}</div>
              <div className="w-7 h-7 rounded-full bg-ink-300/30 flex items-center justify-center text-[10px] font-semibold">{p.assignee}</div>
              <KebabMenu items={projectMenu(p.id)} />
            </Link>
          ))}
        </Card>
      )}

      {loading && deals.length > 0 && (
        <div className="mt-3 inline-flex items-center gap-2 text-[11.5px] text-ink-500">
          <Loader2 size={11} className="animate-spin" /> Refreshing…
        </div>
      )}
    </div>
  );
}
