'use client';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Plus, Search, ChevronDown, LayoutGrid, List, Building2,
  MapPin, AlertTriangle, Upload, Loader2, RefreshCw,
} from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge, StatusBadge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import { projectStatuses } from '@/lib/catalogs';
import { modelDataReadiness, Readiness, READINESS_STEPS } from '@/lib/project';
import { formatKeys } from '@/lib/formatKeys';
import { cn, fmtDate, fmtCurrency } from '@/lib/format';
import { useDeals } from '@/lib/hooks/useDeals';
import { WorkerDeal, PipelineDealRow, api, userMessage } from '@/lib/api';
import { staleDraftNote } from '@/lib/wizardDraft';
import { WorkerNotConfigured } from '@/components/WorkerNotConfigured';
import { OrgMember, useCurrentRole, useOrgMembers } from '@/lib/auth';
import { useToast } from '@/components/ui/Toast';
import { GetStartedHero, useGetStartedHeroVisible } from '@/components/help/GetStartedHero';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';

/** Wave 5 — the status filter: All plus exactly the seven statuses. "Draft"
 *  is a lifecycle draft (the status follows the wizard), so there is one
 *  entry for it, not a "Drafts" beside a "Draft". */
const FILTER_OPTIONS: string[] = projectStatuses;
/** Deep links written before Wave 5 (`?filter=Drafts`) open the Draft filter. */
const FILTER_ALIASES: Readonly<Record<string, string>> = { Drafts: 'Draft' };

/** Where a draft opens: back into the wizard, never the deal page. */
const resumeHref = (id: string) => `/projects/new?draft=${encodeURIComponent(id)}`;

// FON-79 — a draft's kebab: Resume / Discard draft. Not Archive and not
// Delete Forever — those are for underwritings, and the worker's draft
// route refuses an active deal anyway.
const buildDraftMenu = (
  id: string,
  name: string,
  toast: (m: string, opts?: { type?: 'success' | 'error' | 'info' }) => void,
  onDiscarded: () => void,
  navigate: (href: string) => void,
) => {
  const onDiscard = async () => {
    const expected = (name || 'this draft').trim();
    if (!window.confirm(`Discard the draft "${expected}"? Its uploaded documents are removed too.`)) return;
    try {
      await api.deals.discardDraft(id);
      toast('Draft discarded', { type: 'success' });
      onDiscarded();
    } catch (err) {
      // 409 documents_in_flight — the worker's own sentence names the count.
      toast(`Couldn't discard — ${userMessage(err)}`, { type: 'error' });
    }
  };
  return [
    { label: 'Resume', onSelect: () => navigate(resumeHref(id)) },
    { label: 'Discard draft', onSelect: onDiscard, danger: true },
  ];
};

// Per-row kebab actions — every row is a worker-backed deal (the list only
// renders with a worker configured).
const buildProjectMenu = (
  id: string,
  name: string,
  toast: (m: string, opts?: { type?: 'success' | 'error' | 'info' }) => void,
  onArchived: () => void,
  isAdmin: boolean,
) => {
  const onArchive = async () => {
    try {
      await api.deals.archive(id);
      toast('Project archived', { type: 'success' });
      onArchived();
    } catch (err) {
      toast(`Archive failed — ${userMessage(err)}`, { type: 'error' });
    }
  };
  const onDeleteForever = async () => {
    const expected = (name || 'this project').trim();
    const typed = window.prompt(
      `Permanently delete "${expected}" and ALL its documents, ` +
        'extractions, broker questions, scenarios, and engine runs?\n\n' +
        'Type the project name to confirm:',
      '',
    );
    if (typed == null) return;
    if (typed.trim() !== expected) {
      toast('Project name did not match — delete cancelled', { type: 'info' });
      return;
    }
    try {
      await api.deals.delete(id);
      toast(`"${expected}" permanently deleted`, { type: 'success' });
      onArchived();
    } catch (err) {
      toast(`Delete failed — ${userMessage(err)}`, { type: 'error' });
    }
  };
  return [
    { label: 'View Details', onSelect: () => { window.location.href = `/projects/${id}`; } },
    { label: 'Archive', onSelect: onArchive, danger: true },
    // Wave 5 RBAC — hard-delete gated to org:admin.
    ...(isAdmin
      ? [{ label: 'Delete Forever…', onSelect: onDeleteForever, danger: true }]
      : []),
  ];
};

/** FON-80 — the tooltip on the Model/Data Readiness column and card row. */
const READINESS_TIP =
  'Where this deal is in Fondok’s pipeline, read from its processing state — not a score: ' +
  'No documents → Documents uploaded → Extracted (with the extraction confidence) → Model run → Validated.';

interface DisplayDeal {
  id: string;
  name: string;
  /** FON-80 — the hotel's name from its documents (search matches it); null until extracted. */
  propertyName: string | null;
  city: string;
  /** Room count, or null until the OM extraction fills it — rendered "—", never 0. */
  keys: number | null;
  service: string;
  /** Null when the row carries none → no badge, never an invented "Draft" / "Teaser". */
  status: string | null;
  dealStage: string | null;
  /** FON-80 — purchase price from the latest Base Case run (the IC Memo's figure), else null. */
  purchasePrice: number | null;
  /** Acquisition basis per key, from the pipeline payload (the same figure the dashboard prints). */
  pricePerKey: number | null;
  /** Levered IRR in PERCENT (15.7 = 15.7%), from the pipeline payload. */
  irr: number | null;
  /** FON-80 — equity multiple as a raw ratio (2.1 = 2.1x) from the latest Base Case run. */
  equityMultiple: number | null;
  noi: number | null;
  /** FON-80 — processing step from the worker's facts, or null when none is known → "—". */
  readiness: Readiness | null;
  /** FON-80 — the creator's user id from the deal's create audit row; null when unknown. */
  ownerId: string | null;
  /** Document count as text, or "—" on a worker that does not report it. */
  docs: string;
  updatedAt: string;
  /** True only when the worker REPORTS zero documents — unknown is not zero. */
  noDocs: boolean;
  /** FON-79 — true for the wizard's server-side draft: a DRAFT badge, no
   *  return cells, Resume / Discard in the kebab, opens in the wizard. */
  isDraft: boolean;
  /** "Draft · untouched for N days" once a draft has sat ≥ 30 days; else null. */
  staleNote: string | null;
}

/**
 * A worker deal row joined with its pipeline row. The deal list and the
 * dashboard's Recent Projects read the SAME pipeline payload for IRR and
 * $/Key, so the two surfaces cannot disagree about one deal.
 */
function fromWorkerDeal(d: WorkerDeal, row?: PipelineDealRow): DisplayDeal {
  const isDraft = d.lifecycle === 'draft';
  return {
    id: d.id,
    name: d.name,
    propertyName: d.property_name?.trim() || null,
    city: d.city ?? '—',
    keys: d.keys,
    service: d.service ?? '—',
    status: d.status || null,
    dealStage: d.deal_stage || null,
    purchasePrice: row?.purchase_price != null && row.purchase_price > 0 ? row.purchase_price : null,
    pricePerKey: row?.price_per_key ?? null,
    irr: row?.levered_irr != null ? row.levered_irr * 100 : null,
    equityMultiple: row?.equity_multiple ?? null,
    noi: row?.noi_y1 ?? null,
    // A draft has no model and no readiness — the card says "Setup not finished".
    readiness: isDraft
      ? null
      : modelDataReadiness({
          documentCount: d.document_count,
          aiConfidence: d.ai_confidence,
          lastEngineRunAt: row?.last_engine_run_at ?? null,
          state: d.state ?? row?.state ?? null,
        }),
    ownerId: d.created_by?.trim() || null,
    docs: d.document_count != null ? String(d.document_count) : '—',
    updatedAt: d.updated_at ? fmtDate(d.updated_at) : '—',
    noDocs: d.document_count === 0,
    isDraft,
    staleNote: isDraft ? staleDraftNote(d.updated_at) : null,
  };
}

/** FON-80 — the owner filter's "no recorded creator" entry (header-only creates, older rows). */
const OWNER_UNKNOWN = '__unknown__';

/** The workspace member a deal's creator id resolves to, or null when the
 *  roster does not carry them (left the workspace, personal workspace, demo). */
const ownerOf = (ownerId: string | null, members: OrgMember[]): OrgMember | null =>
  ownerId ? members.find((m) => m.userId === ownerId) ?? null : null;

/** The one-line search index: project name, extracted property name, city. */
const searchText = (p: DisplayDeal) =>
  [p.name, p.propertyName ?? '', p.city].join('\n').toLowerCase();

export default function ProjectsPage() {
  const [view, setView] = useState<'grid' | 'list'>('grid');
  const [filter, setFilter] = useState<string>('All Status');
  // `/projects?filter=Draft` (the wizard's "You have N drafts" chip) opens
  // the list already narrowed. Read on mount so the server render and the
  // first client render agree.
  useEffect(() => {
    const raw = new URLSearchParams(window.location.search).get('filter');
    const wanted = raw ? (FILTER_ALIASES[raw] ?? raw) : null;
    if (wanted && FILTER_OPTIONS.includes(wanted)) setFilter(wanted);
  }, []);
  const [search, setSearch] = useState('');
  const [filterOpen, setFilterOpen] = useState(false);
  // FON-80 — the owner filter: a creator's user id, OWNER_UNKNOWN, or all.
  const [ownerFilter, setOwnerFilter] = useState<string>('All Owners');
  const [ownerOpen, setOwnerOpen] = useState(false);
  const { toast } = useToast();
  const router = useRouter();
  // Wave 5 RBAC — hard-delete admin gate.
  const currentRole = useCurrentRole();
  const isAdmin = currentRole === 'org:admin';
  // FON-80 — the workspace roster names each deal's creator (`created_by`
  // is a Clerk user id); the same source the deal header's Collaborators use.
  const { members } = useOrgMembers();

  const { deals, loading, error, workerConfigured, refresh } = useDeals();
  const heroVisible = useGetStartedHeroVisible();

  // The list is worker deals and nothing else. Without a worker URL there is
  // no list — the body below says so instead of showing sample deals.
  // The pipeline payload carries each deal's headline returns ($/Key, IRR,
  // NOI) from its latest run — the same payload the dashboard reads. Joined
  // here by deal id; a deal with no run shows "—" for those cells.
  const [pipelineRows, setPipelineRows] = useState<Record<string, PipelineDealRow>>({});
  useEffect(() => {
    if (!workerConfigured) return;
    const ctrl = new AbortController();
    api.deals
      .pipeline({ sort: 'last_activity_desc', limit: 200 }, ctrl.signal)
      .then((r) => setPipelineRows(Object.fromEntries(r.deals.map((d) => [d.deal_id, d]))))
      .catch(() => { /* the list still renders; return cells read "—" */ });
    return () => ctrl.abort();
  }, [workerConfigured, deals.length]);

  // Drafts sort first within the worker's newest-first order (A.2).
  const display: DisplayDeal[] = useMemo(() => {
    const rows = deals.map((d) => fromWorkerDeal(d, pipelineRows[d.id]));
    return [...rows.filter((p) => p.isDraft), ...rows.filter((p) => !p.isDraft)];
  }, [deals, pipelineRows]);
  // The subtitle's parts add up to the total: drafts (the wizard is not
  // finished), archived, and active — every other status.
  const draftCount = display.filter((p) => p.isDraft).length;
  const archivedCount = display.filter((p) => !p.isDraft && p.status === 'Archived').length;
  const activeCount = display.length - draftCount - archivedCount;

  // Draft is the lifecycle (a wizard not finished); every other status
  // filters underwritings by their stored status.
  const matchesFilter = (p: DisplayDeal) =>
    filter === 'All Status'
      ? true
      : filter === 'Draft'
        ? p.isDraft
        : !p.isDraft && p.status === filter;
  // FON-80 — the owner filter exists only when some deal's creator the
  // roster can name; "Unknown" joins it for rows whose creator it cannot
  // (left the workspace, header-only create). No creators → no filter.
  const ownerOptions = useMemo(() => {
    const named = new Map<string, string>();
    let unknown = false;
    for (const p of display) {
      const m = ownerOf(p.ownerId, members);
      if (m) named.set(m.userId, m.name);
      else unknown = true;
    }
    const opts = [...named.entries()]
      .sort((a, b) => a[1].localeCompare(b[1]))
      .map(([id, name]) => ({ id, name }));
    if (unknown && opts.length > 0) opts.push({ id: OWNER_UNKNOWN, name: 'Unknown' });
    return opts;
  }, [display, members]);
  const matchesOwner = (p: DisplayDeal) => {
    if (ownerFilter === 'All Owners') return true;
    const m = ownerOf(p.ownerId, members);
    return ownerFilter === OWNER_UNKNOWN ? m == null : m?.userId === ownerFilter;
  };
  // Search matches the project name, the extracted property name and the city.
  const needle = search.trim().toLowerCase();
  const filtered = display.filter((p) =>
    matchesFilter(p) && matchesOwner(p) && (!needle || searchText(p).includes(needle))
  );

  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <PageHeader
        title="Projects"
        subtitle={`${display.length} total projects · ${activeCount} active · ${draftCount} ${draftCount === 1 ? 'draft' : 'drafts'}${archivedCount > 0 ? ` · ${archivedCount} archived` : ''}`}
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
            Each card below is one hotel deal you&apos;re evaluating. Purchase price,
            $/Key, levered IRR and equity multiple come from the deal&apos;s latest Base
            Case run — the same figures its IC Memo prints — and read &ldquo;—&rdquo; until
            the model has run. <span className="font-semibold">Model/Data Readiness</span> is
            where the deal is in Fondok&apos;s pipeline (documents → extraction → model run →
            validated), not a score. Click any deal to open its full underwriting.
          </>
        }
      />

      {/* Toolbar */}
      <div className="flex items-center gap-3 mb-5">
        <div className="relative flex-1 max-w-md">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="Search by project, property or city…"
            className="w-full pl-9 pr-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          />
        </div>

        <div className="relative">
          <button onClick={() => setFilterOpen(!filterOpen)}
            className="flex items-center gap-2 px-3 py-2 text-[12.5px] bg-white border border-border rounded-md hover:bg-ink-300/10">
            {filter === 'All Status' ? 'All' : filter} <ChevronDown size={13} className="text-ink-400" />
          </button>
          {filterOpen && (
            <div data-testid="status-filter-menu" className="absolute right-0 top-full mt-1 bg-white border border-border rounded-lg shadow-lg py-1 z-10 min-w-[160px]">
              {FILTER_OPTIONS.map(s => (
                <button key={s} onClick={() => { setFilter(s); setFilterOpen(false); }}
                  className="w-full px-3 py-2 text-[12.5px] hover:bg-ink-300/10 text-left">
                  {s === 'All Status' ? 'All' : s}
                </button>
              ))}
            </div>
          )}
        </div>

        {ownerOptions.length > 0 && (
          <div className="relative">
            <button onClick={() => setOwnerOpen(!ownerOpen)} data-testid="owner-filter"
              className="flex items-center gap-2 px-3 py-2 text-[12.5px] bg-white border border-border rounded-md hover:bg-ink-300/10">
              Owner: {ownerFilter === 'All Owners' ? 'Any' : ownerOptions.find((o) => o.id === ownerFilter)?.name ?? 'Any'}
              <ChevronDown size={13} className="text-ink-400" />
            </button>
            {ownerOpen && (
              <div data-testid="owner-filter-menu" className="absolute right-0 top-full mt-1 bg-white border border-border rounded-lg shadow-lg py-1 z-10 min-w-[160px]">
                <button onClick={() => { setOwnerFilter('All Owners'); setOwnerOpen(false); }}
                  className="w-full px-3 py-2 text-[12.5px] hover:bg-ink-300/10 text-left">
                  Any owner
                </button>
                {ownerOptions.map((o) => (
                  <button key={o.id} onClick={() => { setOwnerFilter(o.id); setOwnerOpen(false); }}
                    className="w-full px-3 py-2 text-[12.5px] hover:bg-ink-300/10 text-left">
                    {o.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

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

      {error && workerConfigured && (
        <Card className="p-5 mb-5 border-danger-500/30 bg-danger-50">
          <div className="flex items-start gap-3">
            <AlertTriangle size={16} className="text-danger-700 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="text-[13px] font-semibold text-danger-700">Couldn’t reach Fondok</div>
              <p className="text-[12px] text-danger-700/80 mt-1">The deal list did not load. {error}</p>
            </div>
            <Button variant="secondary" size="sm" onClick={refresh}><RefreshCw size={12} /> Retry</Button>
          </div>
        </Card>
      )}

      {!workerConfigured ? (
        <WorkerNotConfigured surface="deal list" />
      ) : loading && deals.length === 0 ? (
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
      ) : deals.length === 0 ? (
        // Genuinely no deals — the guided first-run hero (opt-out), or a
        // compact empty state once it's been dismissed / coach marks are off.
        heroVisible ? (
          <GetStartedHero />
        ) : (
          <Card className="p-12 text-center">
            <Building2 size={36} className="text-ink-400 mx-auto mb-3" />
            <div className="text-[14px] font-semibold text-ink-900">No deals yet</div>
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              Click <span className="font-medium">+ New Project</span> to set up your first hotel —
              add the deal name, market, and key count, then drop in the OM and T-12 to start underwriting.
            </p>
            <div className="mt-5">
              <Link href="/projects/new">
                <Button variant="primary"><Plus size={14} /> New Project</Button>
              </Link>
            </div>
          </Card>
        )
      ) : filtered.length === 0 ? (
        // Has deals, but the search / filter matched none.
        <Card className="p-12 text-center">
          <Building2 size={36} className="text-ink-400 mx-auto mb-3" />
          <div className="text-[14px] font-semibold text-ink-900">No deals match your filters</div>
          <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
            Try a different search term or clear the status filter to see all your deals.
          </p>
        </Card>
      ) : view === 'grid' ? (
        <div className="grid grid-cols-3 gap-4">
          {filtered.map(p => (
            <div key={p.id} className="relative">
            {/* A draft's kebab sits beside the card's link, not inside it —
                a menu click must never also follow the link. */}
            {p.isDraft && (
              <div className="absolute top-3 right-3 z-10">
                <KebabMenu items={buildDraftMenu(p.id, p.name, toast, refresh, router.push)} />
              </div>
            )}
            <Link href={p.isDraft ? resumeHref(p.id) : `/projects/${p.id}`} data-testid={p.isDraft ? 'draft-card' : undefined}>
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
                  <span className={cn('text-[11px] text-ink-500 whitespace-nowrap tabular-nums', p.isDraft && 'mr-7')}>{formatKeys(p.keys)} keys</span>
                </div>

                <div className="flex items-center gap-1.5 mb-4">
                  {p.isDraft ? <DraftBadge /> : p.status && <StatusBadge value={p.status} />}
                  {p.dealStage && <StatusBadge value={p.dealStage} />}
                </div>

                {p.isDraft ? (
                  // A draft has no model outputs: no $/Key, IRR or confidence
                  // cell — not even a dash, which would read as "pending".
                  <DraftPanel staleNote={p.staleNote} />
                ) : p.noDocs ? (
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
                    {/* FON-80 — the four decision metrics from the latest
                        Base Case run; each "—" until the model has run. */}
                    <div className="grid grid-cols-2 gap-2 mb-4">
                      <CardMetric label="Purchase Price" value={p.purchasePrice != null ? fmtCurrency(p.purchasePrice, { compact: true }) : null} />
                      <CardMetric label="$/Key" value={p.pricePerKey != null ? fmtCurrency(p.pricePerKey) : null} />
                      <CardMetric label="Levered IRR" value={p.irr != null ? `${p.irr.toFixed(1)}%` : null} />
                      <CardMetric label="Equity Multiple" value={p.equityMultiple != null ? `${p.equityMultiple.toFixed(2)}x` : null} />
                    </div>
                    <div data-testid="readiness">
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-[11px] text-ink-500" title={READINESS_TIP}>Model/Data Readiness</span>
                        <ReadinessText readiness={p.readiness} />
                      </div>
                      <ReadinessSteps readiness={p.readiness} />
                    </div>
                  </>
                )}

                <div className="flex items-center justify-between pt-4 mt-3 border-t border-border">
                  <div className="flex items-center gap-2">
                    <OwnerAvatar owner={ownerOf(p.ownerId, members)} ownerId={p.ownerId} size="sm" />
                    <span className="text-[11px] text-ink-500">{p.updatedAt}</span>
                  </div>
                  <span className="text-[11px] text-ink-500">{p.docs} docs</span>
                </div>
              </Card>
            </Link>
            </div>
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
              <MetricLabel label="Price" tip="Purchase price from the latest Base Case run — the figure the IC Memo's Sources & Uses prints. Reads — until the model has run." />
            </div>
            <div className="w-20 flex justify-end">
              <MetricLabel label="$/Key" tip="Acquisition basis per hotel room from the latest model run — the yardstick for whether you are buying cheap or rich versus replacement cost." />
            </div>
            <div className="w-24 flex justify-end">
              <MetricLabel label="NOI" tip="Net Operating Income — revenue minus operating expenses, before debt and capex. The truest measure of the hotel's earning power." />
            </div>
            <div className="w-16 flex justify-end">
              <MetricLabel label="IRR" tip="Internal Rate of Return — the annualized return on equity over the hold period. The headline return metric." />
            </div>
            <div className="w-14 flex justify-end">
              <MetricLabel label="EM" tip="Equity multiple from the latest Base Case run — total distributions ÷ equity invested (2.0x = money doubled). The IC Memo's figure." />
            </div>
            <div className="w-28 flex justify-end">
              <MetricLabel label="Readiness" tip={READINESS_TIP} />
            </div>
            <div className="w-14 flex justify-end">
              <MetricLabel label="Docs" tip="Number of documents uploaded vs. the underwriting checklist (OM, T-12, STR report, rent roll, PIP estimate, etc.)." />
            </div>
            <div className="w-16 text-right">Updated</div>
            <div className="w-7 text-center">Owner</div>
            <div className="w-7" aria-hidden="true" />
          </div>
          {filtered.map((p, i) => (
            <Link key={p.id} href={p.isDraft ? resumeHref(p.id) : `/projects/${p.id}`}
              data-testid={p.isDraft ? 'draft-row' : undefined}
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
                  {p.isDraft ? <DraftBadge /> : p.status && <StatusBadge value={p.status} />}
                </div>
                <div className="text-[12px] text-ink-500 mt-0.5">
                  {p.city} · <span className="tabular-nums">{formatKeys(p.keys)}</span> keys · {p.service}
                  {p.staleNote && <span className="text-warn-700"> · {p.staleNote}</span>}
                </div>
              </div>
              <div className="w-20 flex justify-end">
                {p.dealStage ? <StatusBadge value={p.dealStage} /> : <span className="text-[11px] text-ink-500">—</span>}
              </div>
              {p.isDraft ? (
                // No Price · $/Key · NOI · IRR · EM · Readiness for a draft —
                // one span across the six model columns says why.
                <div className="w-[34.25rem] text-right text-[11.5px] text-ink-500 pr-1">Setup not finished — no model outputs</div>
              ) : (
                <>
                  <div className="text-[12.5px] tabular-nums w-20 text-right">{p.purchasePrice != null ? fmtCurrency(p.purchasePrice, { compact: true }) : '—'}</div>
                  <div className="text-[12.5px] tabular-nums w-20 text-right">{p.pricePerKey != null ? fmtCurrency(p.pricePerKey) : '—'}</div>
                  <div className="text-[12.5px] tabular-nums w-24 text-right">{p.noi != null ? `$${(p.noi / 1e6).toFixed(2)}M` : '—'}</div>
                  <div className="text-[12.5px] tabular-nums w-16 text-right font-medium">{p.irr != null ? `${p.irr.toFixed(1)}%` : '—'}</div>
                  <div className="text-[12.5px] tabular-nums w-14 text-right">{p.equityMultiple != null ? `${p.equityMultiple.toFixed(2)}x` : '—'}</div>
                  <div className="w-28 flex justify-end" data-testid="readiness">
                    <ReadinessText readiness={p.readiness} />
                  </div>
                </>
              )}
              <div className="text-[12px] tabular-nums w-14 text-right text-ink-700">{p.docs}</div>
              <div className="text-[11.5px] w-16 text-right text-ink-500">{p.updatedAt}</div>
              <OwnerAvatar owner={ownerOf(p.ownerId, members)} ownerId={p.ownerId} />
              <KebabMenu
                items={p.isDraft
                  ? buildDraftMenu(p.id, p.name, toast, refresh, router.push)
                  : buildProjectMenu(p.id, p.name, toast, refresh, isAdmin)}
              />
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

/** FON-80 — one card metric: its value, or "—" with a tooltip saying why. */
function CardMetric({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <div className="text-[10px] text-ink-500 uppercase tracking-wide">{label}</div>
      <div className="text-[14px] font-semibold tabular-nums" title={value == null ? 'Available after the model runs' : undefined}>
        {value ?? '—'}
      </div>
    </div>
  );
}

/** FON-80 — the readiness step as words, with the extraction confidence
 *  beside it once a document has been scored. "—" when nothing is known. */
function ReadinessText({ readiness }: { readiness: Readiness | null }) {
  if (!readiness) return <span className="text-[11px] text-ink-500" title={READINESS_TIP}>—</span>;
  return (
    <span className="text-[11px] text-ink-700 whitespace-nowrap" title={READINESS_TIP} data-testid="readiness-label">
      <span className="font-semibold">{readiness.label}</span>
      {readiness.extractionConfidence != null && (
        <span className="text-ink-500 tabular-nums"> · extraction {readiness.extractionConfidence}%</span>
      )}
    </span>
  );
}

/** FON-80 — a five-segment step indicator (never a percentage bar). */
function ReadinessSteps({ readiness }: { readiness: Readiness | null }) {
  return (
    <div className="flex gap-0.5" aria-hidden="true">
      {Array.from({ length: READINESS_STEPS }, (_, i) => (
        <div
          key={i}
          className={cn('h-1.5 flex-1 rounded-full', readiness && i <= readiness.index ? 'bg-brand-500' : 'bg-ink-300/30')}
        />
      ))}
    </div>
  );
}

/** FON-80 — the creator's initials from the workspace roster; "—" when the
 *  deal records no creator or the roster cannot name them. Never invented. */
function OwnerAvatar({ owner, ownerId, size = 'md' }: { owner: OrgMember | null; ownerId: string | null; size?: 'sm' | 'md' }) {
  const title = owner
    ? `Owner: ${owner.name}`
    : ownerId
      ? 'Owner: not in this workspace’s roster'
      : 'Owner: not recorded';
  return (
    <div
      className={cn(
        'rounded-full bg-ink-300/30 flex items-center justify-center text-[10px] font-semibold text-ink-700',
        size === 'sm' ? 'w-6 h-6' : 'w-7 h-7',
      )}
      title={title}
      aria-label={title}
      data-testid="deal-owner"
    >
      {owner ? owner.initials : '—'}
    </div>
  );
}

/** FON-79 — the badge that replaces the status badge on a draft. */
function DraftBadge() {
  return (
    <span data-testid="deal-draft-badge">
      <Badge tone="slate" dot uppercase>Draft</Badge>
    </span>
  );
}

/** The card body of a draft: what it is, where it opens, and — once it has
 *  sat for 30 days — how long nobody has touched it. */
function DraftPanel({ staleNote }: { staleNote: string | null }) {
  return (
    <div className="border border-dashed border-ink-300 rounded-md p-4 mb-3" data-testid="draft-panel">
      <div className="text-[12px] font-medium text-ink-900">Setup not finished</div>
      <div className="text-[11px] text-ink-500 leading-relaxed mt-0.5">
        No model outputs until you finish setup. Open the card to resume where you left off.
      </div>
      {staleNote && (
        <div className="text-[11px] text-warn-700 mt-2" data-testid="draft-stale-note">{staleNote}</div>
      )}
    </div>
  );
}
