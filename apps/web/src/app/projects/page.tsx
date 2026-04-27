'use client';
import Link from 'next/link';
import { useState } from 'react';
import {
  Plus, Search, ChevronDown, LayoutGrid, List, Building2,
  MapPin, AlertTriangle, Upload,
} from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import { projects, projectStatuses } from '@/lib/mockData';
import { cn } from '@/lib/format';

const projectMenu = (id: number) => [
  { label: 'View Details', onSelect: () => { window.location.href = `/projects/${id}`; } },
  { label: 'Export Excel', onSelect: () => {} },
  { label: 'Export Memo', onSelect: () => {} },
  { label: 'Archive', onSelect: () => {}, danger: true },
];

const riskTone = (r: string) =>
  r === 'Low' ? 'text-success-700 bg-success-50' : r === 'Medium' ? 'text-warn-700 bg-warn-50' : 'text-danger-700 bg-danger-50';

export default function ProjectsPage() {
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const [filter, setFilter] = useState<string>('All Status');
  const [search, setSearch] = useState('');
  const [filterOpen, setFilterOpen] = useState(false);

  const filtered = projects.filter(p =>
    (filter === 'All Status' || p.status === filter) &&
    (!search || p.name.toLowerCase().includes(search.toLowerCase()) || p.city.toLowerCase().includes(search.toLowerCase()))
  );

  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <PageHeader
        title="Projects"
        subtitle={`${projects.length} total projects · ${projects.filter(p => p.status !== 'Archived').length} active`}
        action={
          <Link href="/projects/new">
            <Button variant="primary"><Plus size={14} /> New Project</Button>
          </Link>
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

      {view === 'grid' ? (
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
                        <div className="text-[14px] font-semibold tabular-nums">${p.revpar}</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-ink-500 uppercase tracking-wide">IRR</div>
                        <div className="text-[14px] font-semibold tabular-nums">{p.irr.toFixed(2)}%</div>
                      </div>
                      <div>
                        <div className="text-[10px] text-ink-500 uppercase tracking-wide">Risk</div>
                        <div className={cn('text-[12px] font-medium px-1.5 py-0.5 rounded inline-block', riskTone(p.risk))}>
                          {p.risk}
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
              <div className="text-[12.5px] tabular-nums w-20 text-right">${p.revpar} <span className="text-ink-400">RevPAR</span></div>
              <div className="text-[12.5px] tabular-nums w-24 text-right">${(p.noi! / 1e6).toFixed(2)}M <span className="text-ink-400">NOI</span></div>
              <div className="text-[12.5px] tabular-nums w-16 text-right font-medium">{p.irr.toFixed(2)}%</div>
              <div className={cn('text-[11.5px] font-medium px-2 py-0.5 rounded w-16 text-center', riskTone(p.risk))}>{p.risk}</div>
              <div className="w-7 h-7 rounded-full bg-ink-300/30 flex items-center justify-center text-[10px] font-semibold">{p.assignee}</div>
              <KebabMenu items={projectMenu(p.id)} />
            </Link>
          ))}
        </Card>
      )}
    </div>
  );
}
