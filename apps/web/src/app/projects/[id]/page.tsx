'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useSearchParams, useRouter } from 'next/navigation';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import {
  ArrowLeft, MapPin, Building2, Calendar, Users, Share2, X,
  Sparkles, FileText, Activity, AlertTriangle,
} from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';
import KebabMenu from '@/components/ui/KebabMenu';
import { useToast } from '@/components/ui/Toast';
import type { DealStage, ProjectStatus, Risk } from '@/lib/catalogs';
import { displayConfidence, type Project } from '@/lib/project';
import { WorkerNotConfigured } from '@/components/WorkerNotConfigured';
import { cn, fmtDate } from '@/lib/format';
import { formatKeys } from '@/lib/formatKeys';
import { useDeal } from '@/lib/hooks/useDeal';
import { ProvenanceProvider } from '@/lib/hooks/useDealProvenance';
import { ValueTraceProvider } from '@/lib/hooks/useValueTrace';
import { EngineFailuresBanner, TAB_OWNED_ENGINES } from '@/components/project/EngineFailuresBanner';
import { useVariance } from '@/lib/hooks/useVariance';
import { StaleRunBanner } from '@/components/project/StaleRunBanner';
import { IntroCard } from '@/components/help/IntroCard';
import { useDocuments } from '@/lib/hooks/useDocuments';
import { isWorkerConnected, userMessage } from '@/lib/api';
import { PROJECT_TABS, TAB_LABEL } from '@/lib/projectTabs';
import { legacyReturnsRedirect } from '@/lib/returnsSection';
import DataRoomTab from '@/components/project/DataRoomTab';
import OverviewTab from '@/components/project/OverviewTab';
import InvestmentTab from '@/components/project/InvestmentTab';
import DebtTab from '@/components/project/DebtTab';
import ScenarioComparePanel from '@/components/project/ScenarioComparePanel';
import ScenarioAnalysisTab from '@/components/project/ScenarioAnalysisTab';
import AtlasCopilot from '@/components/project/AtlasCopilot';
import ScenarioEditor from '@/components/project/ScenarioEditor';
import TabLoadingSkeleton from '@/components/project/TabLoadingSkeleton';
import { DataKey } from '@/components/design';
import { api as workerApi, type ScenarioRecord } from '@/lib/api';
import { useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useCurrentRole } from '@/lib/auth';
import {
  duplicateDealDoneText,
  duplicateDealErrorText,
  type DuplicateDealChoices,
} from '@/lib/duplicateDeal';
import DuplicateDealDialog from '@/components/project/DuplicateDealDialog';

// Heavy tabs (Recharts-bound) lazy-loaded so the initial /projects/[id]
// JS bundle drops by the size of recharts + each tab's own code.
// Light tabs (Data Room / Overview / Investment / Debt) stay
// eagerly loaded since they're the most common landing tabs.
const PLTab = dynamic(() => import('@/components/project/PLTab'), {
  loading: () => <TabLoadingSkeleton />,
});
const CashFlowTab = dynamic(() => import('@/components/project/CashFlowTab'), {
  loading: () => <TabLoadingSkeleton />,
});
// R-061 — Returns is a section of Overview now (no tab); R-073 — the
// Investment Bridge view sits immediately before Scenario Analysis.
const InvestmentBridgeTab = dynamic(() => import('@/components/project/InvestmentBridgeTab'), {
  loading: () => <TabLoadingSkeleton />,
});
const PartnershipTab = dynamic(() => import('@/components/project/PartnershipTab'), {
  loading: () => <TabLoadingSkeleton />,
});
const MarketTab = dynamic(() => import('@/components/project/MarketTab'), {
  loading: () => <TabLoadingSkeleton />,
});
const AnalysisTab = dynamic(() => import('@/components/project/AnalysisTab'), {
  loading: () => <TabLoadingSkeleton />,
});
// FON-54 — IC Memo one-pager (Deal Summary + dynamic recommendation + export).
const ICMemoTab = dynamic(() => import('@/components/project/ICMemoTab'), {
  loading: () => <TabLoadingSkeleton />,
});
const ValidationTab = dynamic(() => import('@/components/project/ValidationTab'), {
  loading: () => <TabLoadingSkeleton />,
});
// Wave 3 W3.3 — Forecasting tab hosts the STR forward-forecast panel.
const ForecastingTab = dynamic(() => import('@/components/project/ForecastingTab'), {
  loading: () => <TabLoadingSkeleton />,
});
// Wave 4 W4.3 — Activity Feed (per-deal audit log) lives in its own tab
// so the underwriting workflow tabs stay focused on the model. Lazy
// loaded for the same bundle-size reasons as the other heavy panels.
const ActivityFeed = dynamic(() => import('@/components/project/ActivityFeed'), {
  loading: () => <TabLoadingSkeleton />,
});

// Tab registry (ids, labels, order) lives in `@/lib/projectTabs` so the
// tab-order test can pin it — a Next.js page may only export its component.
// The removed tabs' render blocks below stay reachable via ?tab= for internal
// use — EXCEPT Export (FON-54b, 2026-09-09): the legacy ExportTab surface is
// no longer routed at all, so it can't disagree with the IC Memo export cards
// (it also used an unauthenticated window.location.href). The ExportTab file
// is kept, just unmounted.
const tabs = PROJECT_TABS;

export default function ProjectDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const { toast } = useToast();
  // Wave 5 RBAC — hard-delete is admin-only. Demo persona resolves to
  // ``org:admin`` so the affordance still renders for the local flow.
  const currentRole = useCurrentRole();
  const isAdmin = currentRole === 'org:admin';
  const rawId = (params?.id as string | undefined) ?? '';
  // The worker only knows UUIDs. A numeric id is a legacy demo link to a deal
  // that does not exist — it renders "not found" below, never a sample.
  const isMockId = /^\d+$/.test(rawId);
  // Children get the raw string so API calls never stringify NaN into a path.
  const id: string = rawId;
  const { deal, loading: dealLoading, error: dealError, refresh: refreshDeal } = useDeal(rawId);
  const workerConnected = isWorkerConnected();
  // Page-level engine outputs — powers the failure banner so a crashed
  // engine is loud on EVERY tab, not silently dashed.
  const { outputs: pageEngineOutputs } = useEngineOutputs(rawId);

  // A numeric id is not a deal Fondok knows: send the reader to the deal list.
  useEffect(() => {
    if (workerConnected && isMockId) router.replace('/projects');
  }, [workerConnected, isMockId, router]);

  // FON-79 — a draft has no underwriting to show. It opens in the wizard
  // (resume), never as a deal page with tabs; the gate below renders no
  // chrome for it while the redirect happens.
  const isDraft = deal?.lifecycle === 'draft';
  useEffect(() => {
    if (isDraft) router.replace(`/projects/new?draft=${encodeURIComponent(rawId)}`);
  }, [isDraft, rawId, router]);

  // Header kebab actions — Mark IC Ready / Archive Project. Exports live ONLY
  // on the IC Memo tab's 'Export & share' card (authed api.exports.download;
  // Excel live, PDF/PPTX Coming Soon per FON-54). The old kebab Excel/PDF items
  // used an unauthenticated window.location.href and disagreed with that card.
  // FON-54 #9 — persist IC-Ready status (was a stub toast) so the header pill
  // + pipeline reflect it.
  const onMarkICReady = async () => {
    if (!workerConnected || isMockId) {
      toast('Worker not configured — nothing to update.', { type: 'info' });
      return;
    }
    try {
      await workerApi.deals.update(rawId, { status: 'IC Ready' });
      toast('Marked as IC Ready', { type: 'success' });
      void refreshDeal?.();
    } catch (err) {
      toast(`Couldn't mark IC Ready — ${userMessage(err)}`, { type: 'error' });
    }
  };
  const onArchive = async () => {
    if (workerConnected && !isMockId) {
      try {
        await workerApi.deals.archive(rawId);
        toast('Project archived', { type: 'success' });
        router.push('/projects');
      } catch (err) {
        const msg = userMessage(err);
        toast(`Archive failed: ${msg}`, { type: 'error' });
      }
    } else {
      toast('Worker not configured — nothing to archive.', { type: 'info' });
    }
  };
  const onDeleteForever = async () => {
    if (!workerConnected || isMockId) {
      toast('Worker not configured — nothing to delete.', { type: 'info' });
      return;
    }
    // Two-step confirm: hard-delete cascades documents + extractions +
    // broker_questions + scenarios + engine_outputs. No undo. We
    // require the user to type the project name so a misclick or a
    // missed kebab tap can't wipe a deal mid-IC.
    const expected = (deal?.name || `deal ${rawId}`).trim();
    const typed = window.prompt(
      `Permanently delete "${expected}" and ALL its documents, ` +
        'extractions, broker questions, scenarios, and engine runs?\n\n' +
        `Type the project name to confirm:`,
      '',
    );
    if (typed == null) return;
    if (typed.trim() !== expected) {
      toast('Project name did not match — delete cancelled', { type: 'info' });
      return;
    }
    try {
      await workerApi.deals.delete(rawId);
      toast(`"${expected}" permanently deleted`, { type: 'success' });
      router.push('/projects');
    } catch (err) {
      const msg = userMessage(err);
      toast(`Delete failed: ${msg}`, { type: 'error' });
    }
  };

  // Duplicate deal — copies the deal, assumptions (with notes), scenarios
  // and documents with their reviewed extractions; cell comments only when
  // the analyst ticks the box in the confirm dialog (per copy, default off);
  // NOT the model runs, so the copy opens with "run the model". Lets an
  // analyst re-extract or experiment on a copy without losing reviewed
  // fields on the original.
  const [duplicating, setDuplicating] = useState(false);
  const [duplicateOpen, setDuplicateOpen] = useState(false);
  const duplicateName = (deal?.name || `deal ${rawId}`).trim();
  const onDuplicate = () => {
    if (!workerConnected || isMockId) {
      toast('Worker not configured — nothing to duplicate.', { type: 'info' });
      return;
    }
    if (duplicating) return;
    setDuplicateOpen(true);
  };
  const runDuplicate = async (choices: DuplicateDealChoices) => {
    if (duplicating) return;
    setDuplicating(true);
    try {
      const res = await workerApi.deals.duplicate(rawId, {
        include_cell_comments: choices.includeCellComments,
      });
      setDuplicateOpen(false);
      toast(duplicateDealDoneText(res.deal.name), { type: 'success' });
      router.push(`/projects/${res.deal.id}`);
    } catch (err) {
      // The worker's reason (409 "documents still being processed…") lives in
      // the WorkerError body, not its message — say it.
      toast(duplicateDealErrorText(err), { type: 'error' });
    } finally {
      setDuplicating(false);
    }
  };

  const headerMenu = [
    { label: 'Mark as IC Ready', onSelect: onMarkICReady },
    { label: duplicating ? 'Duplicating…' : 'Duplicate deal', onSelect: onDuplicate },
    { label: 'Archive Project', onSelect: onArchive, danger: true },
    // Wave 5 RBAC — hard-delete gated to org:admin. Non-admins get the
    // item omitted entirely (cleaner than a disabled row).
    ...(isAdmin
      ? [{ label: 'Delete Forever…', onSelect: onDeleteForever, danger: true }]
      : []),
  ];

  // The header view-model, built from the worker payload and nothing else.
  // (Hoisted above the header-pill / confidence helpers below so they can read it.)
  //
  // CRITICAL — this MUST NEVER fall back to a sample record: when the worker
  // fetch is in flight or errored, a stale fallback would render the wrong
  // deal's name/city/brand (data-leak adjacent — Sam QA Wave 4). We render
  // explicit loading / error states below before reaching the main UI;
  // ``project`` is only consumed when ``deal`` exists.
  const projectOrNull: Project | null = deal ? {
    id: 0,
    name: deal.name,
    city: deal.city ?? '—',
    // E-003 (FON-59) — the wizard sends keys as null and the worker fills it
    // from the OM a minute later. Null stays null: the meta line renders
    // `formatKeys(deal?.keys)`, a dash until then, never a fabricated 0.
    keys: deal.keys,
    service: deal.service ?? '—',
    // Absent on the row → null → no badge. Never an invented "Draft" / "Teaser".
    status: (deal.status as ProjectStatus) || null,
    dealStage: (deal.deal_stage as DealStage) || null,
    risk: ((deal.risk as Risk) || 'Medium'),
    // Null until the worker has scored the deal — the pill reads "Awaiting
    // docs"; a number (even 0) is the worker's own figure.
    aiConfidence: displayConfidence(deal.ai_confidence),
    assignee: '—',
    // Deterministic (UTC / en-US). ``toLocaleDateString()`` depends on the
    // renderer's zone + locale, so any render path that has ``deal`` on both
    // the server and the first client render would hydrate-mismatch (React
    // #418 → #423). Today ``deal`` only arrives client-side, but pin it anyway
    // so the header date can never diverge from the server or by viewer zone.
    updatedAt: deal.updated_at ? fmtDate(deal.updated_at) : '—',
    createdAt: deal.created_at ? fmtDate(deal.created_at) : undefined,
  } : null;

  // Header pill state — popovers/drawers/tooltips. We keep all four
  // independent so opening one doesn't smash the others. Each closes on
  // outside-click or Esc.
  const [docsOpen, setDocsOpen] = useState(false);
  const [confidenceOpen, setConfidenceOpen] = useState(false);
  const [collabOpen, setCollabOpen] = useState(false);
  const docsBtnRef = useRef<HTMLButtonElement | null>(null);
  const confidenceBtnRef = useRef<HTMLButtonElement | null>(null);
  const collabBtnRef = useRef<HTMLButtonElement | null>(null);

  // Pull live worker docs when on a real (UUID) deal so the Docs drawer
  // reflects what's actually in the data room.
  const liveMode = workerConnected && !isMockId;
  const { documents: liveDocs } = useDocuments(liveMode ? rawId : '');
  const drawerDocs = liveMode
    ? liveDocs.map((d) => ({ name: d.filename, status: d.status, type: d.doc_type ?? '—' }))
    : [];

  // Standard institutional underwriting checklist size (OM, T-12, STR
  // report, rent roll, PIP estimate). Used as the denominator in the
  // header "Docs N/M" pill so the count keeps moving as more documents
  // land.
  const EXPECTED_DOC_COUNT = 5;
  const liveDocsLabel = liveMode
    ? `${liveDocs.length}/${Math.max(EXPECTED_DOC_COUNT, liveDocs.length)}`
    : '—';

  // Esc closes any open header overlay.
  useEffect(() => {
    if (!docsOpen && !confidenceOpen && !collabOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setDocsOpen(false);
        setConfidenceOpen(false);
        setCollabOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [docsOpen, confidenceOpen, collabOpen]);

  const onShare = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast('Share link copied to clipboard', { type: 'success' });
    } catch {
      toast('Could not copy link', { type: 'error' });
    }
  };

  // Confidence breakdown — from the worker payload via useDeal; null (no
  // field scored yet) prints a dash in the breakdown, never 0%.
  const docCount = drawerDocs.length;
  const avgFieldConfidence = projectOrNull?.aiConfidence ?? null;
  // The worker's variance report for this deal — the same hook the Analysis
  // tab reads. Null until it has answered (or when it could not): "—".
  // Fetched ONLY while the deal is loaded and the tooltip is open: an empty
  // id short-circuits the hook, so no request fires on mount, on the load /
  // error path, or on every tab for the life of the page (and the Analysis
  // tab's own poller is not doubled). Closing the tooltip stops the poll.
  const variance = useVariance(deal && confidenceOpen ? rawId : '');
  const varianceFlagCount: number | null = variance.flags === null ? null : variance.flags.length;

  const activeTab = searchParams.get('tab') || '';
  const activeLabel = tabs.find(t => t.id === activeTab)?.label ?? TAB_LABEL.dataRoom;

  // R-061 — the Returns tab moved into Overview. A legacy `?tab=returns[&sub=…]`
  // link / bookmark is rewritten to `?tab=overview&sub=…`; Overview's Returns
  // section then scrolls itself into view on that view.
  const queryString = searchParams.toString();
  useEffect(() => {
    if (activeTab === 'returns') {
      router.replace(legacyReturnsRedirect(rawId, queryString), { scroll: false });
    }
  }, [activeTab, rawId, queryString, router]);

  const setTab = (tab: string) => {
    const url = tab ? `/projects/${id}?tab=${tab}` : `/projects/${id}`;
    router.push(url, { scroll: false });
  };

  // Wave 3 W3.2 — named scenarios. Only loaded for real (UUID) deals; mock
  // deals get an empty list and ScenarioComparePanel renders Base only.
  const [scenarios, setScenarios] = useState<ScenarioRecord[]>([]);
  const [scenarioEditor, setScenarioEditor] = useState<
    { open: boolean; scenario: ScenarioRecord | null }
  >({ open: false, scenario: null });

  useEffect(() => {
    if (!liveMode) return;
    let cancelled = false;
    workerApi.scenarios
      .list(rawId)
      .then((list) => {
        if (cancelled) return;
        setScenarios(list);
      })
      .catch(() => {
        /* worker not connected or older build — no-op */
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveMode, rawId]);

  const refreshScenarios = async () => {
    if (!liveMode) return;
    try {
      const list = await workerApi.scenarios.list(rawId);
      setScenarios(list);
    } catch {
      /* no-op */
    }
  };

  // Canonical ••• menu — Duplicate clones the selected scenario's overrides
  // into a brand-new scenario; Delete permanently removes a named one.
  const handleDuplicateScenario = async (s: ScenarioRecord) => {
    if (!liveMode) return;
    try {
      const created = await workerApi.scenarios.create(rawId, {
        name: `${s.name} copy`,
        description: s.description,
        overrides: s.overrides,
      });
      await refreshScenarios();
      toast(`Duplicated "${s.name}" → ${created.name}`, { type: 'success' });
    } catch (e) {
      toast(
        `Duplicate failed — ${userMessage(e)}`,
        { type: 'error' },
      );
    }
  };

  // FON-69 G5 — the override table's per-row ✕ ("Remove this override").
  // Persists through the same scenarios API the page already owns, then
  // refreshes so the chip's override count and the table agree.
  const handleRemoveScenarioOverride = async (
    s: ScenarioRecord,
    fieldPath: string,
  ) => {
    if (!liveMode) return;
    try {
      await workerApi.scenarios.update(rawId, s.id, {
        overrides: s.overrides.filter((o) => o.field_path !== fieldPath),
      });
      await refreshScenarios();
      toast(`Removed the override from "${s.name}".`, { type: 'info' });
    } catch (e) {
      toast(
        `Couldn't remove that override: ${
          userMessage(e)
        }`,
        { type: 'error' },
      );
    }
  };

  const handleDeleteScenario = async (s: ScenarioRecord) => {
    if (!liveMode) return;
    try {
      await workerApi.scenarios.delete(rawId, s.id);
      await refreshScenarios();
      toast(`Deleted scenario "${s.name}"`, { type: 'info' });
    } catch (e) {
      toast(
        `Delete failed — ${userMessage(e)}`,
        { type: 'error' },
      );
    }
  };

  // ─── Deal load gate (Wave 4 reliability fix — Bug #1) ─────────────
  // A deal MUST resolve to a real worker payload before rendering the
  // project chrome. The previous fallback to a sample record (a fictitious
  // Austin hotel) under fetch-in-flight or fetch-error conditions surfaced
  // a completely different deal at /projects/<uuid>, which is
  // data-leak-adjacent (Sam QA reproduced across tabs).
  //
  // Without a worker URL there is no deal to load at all: say so (the one
  // "Worker not configured" state every data surface shares).
  if (!workerConnected) {
    return (
      <div className="px-8 pt-7 pb-8">
        <Link
          href="/projects"
          className="inline-flex items-center gap-1 text-[12px] text-ink-500 hover:text-ink-900 mb-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded eyebrow normal-case tracking-wide"
        >
          <ArrowLeft size={13} aria-hidden="true" /> Back to Projects
        </Link>
        <WorkerNotConfigured surface="deal" />
      </div>
    );
  }
  // We early-return a deal-id-scoped skeleton on load and an error card
  // on failure. Both render the requested ``rawId`` at the top so the
  // analyst can confirm the URL is the deal they meant to open — and
  // neither shows any other deal's identifiers. A numeric id is not a deal
  // Fondok knows: say so plainly, with the way back, instead of a blank
  // frame (the effect above also sends the reader to the deal list).
  if (isMockId) {
    return (
      <div className="px-8 pt-7 pb-8">
        <Link
          href="/projects"
          className="inline-flex items-center gap-1 text-[12px] text-ink-500 hover:text-ink-900 mb-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded eyebrow normal-case tracking-wide"
        >
          <ArrowLeft size={13} aria-hidden="true" /> Back to Projects
        </Link>
        <div role="alert" data-testid="deal-not-found" className="rounded-md border border-border bg-white p-5">
          <div className="text-[14px] font-semibold text-ink-900">This deal was not found</div>
          <p className="text-[12.5px] text-ink-700 mt-1">
            It may have been archived, or the link is out of date. Your deals are listed under Projects.
          </p>
        </div>
      </div>
    );
  }
  if (isDraft) {
    return (
      <div className="px-8 pt-7 pb-8">
        <Link
          href="/projects"
          className="inline-flex items-center gap-1 text-[12px] text-ink-500 hover:text-ink-900 mb-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded eyebrow normal-case tracking-wide"
        >
          <ArrowLeft size={13} aria-hidden="true" /> Back to Projects
        </Link>
        <div role="status" data-testid="deal-is-draft" className="rounded-md border border-border bg-white p-5">
          <div className="text-[14px] font-semibold text-ink-900">This deal is still a draft</div>
          <p className="text-[12.5px] text-ink-700 mt-1">
            Its setup is not finished, so there are no model outputs to show. Opening the setup wizard…
          </p>
          <Link
            href={`/projects/new?draft=${encodeURIComponent(rawId)}`}
            className="inline-flex items-center gap-1 text-[12.5px] font-medium text-brand-700 hover:text-brand-500 mt-3"
          >
            Resume setup
          </Link>
        </div>
      </div>
    );
  }
  if (!projectOrNull) {
    return (
        <div className="px-8 pt-7 pb-8">
          <Link
            href="/projects"
            className="inline-flex items-center gap-1 text-[12px] text-ink-500 hover:text-ink-900 mb-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded eyebrow normal-case tracking-wide"
          >
            <ArrowLeft size={13} aria-hidden="true" /> Back to Projects
          </Link>
          {dealError ? (
            <div
              role="alert"
              data-testid="deal-load-error"
              className="rounded-md border border-danger-200 bg-danger-50 p-5"
            >
              <div className="flex items-start gap-3">
                <AlertTriangle size={18} className="text-danger-700 mt-0.5" aria-hidden="true" />
                <div className="flex-1 min-w-0">
                  <div className="text-[14px] font-semibold text-ink-900">
                    Could not load this deal
                  </div>
                  <div className="text-[12.5px] text-ink-700 mt-1 break-all">
                    Deal id: <span className="font-mono">{rawId}</span>
                  </div>
                  <div className="text-[12.5px] text-ink-700 mt-2">
                    {/^TimeoutError|timed out|timeout/i.test(dealError)
                      ? 'Fondok is still extracting your upload. Try again in 30 seconds.'
                      : dealError}
                  </div>
                  <div className="mt-3 flex items-center gap-2">
                    <Button size="sm" variant="secondary" onClick={() => refreshDeal()}>
                      Retry
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div
              role="status"
              aria-live="polite"
              data-testid="deal-load-skeleton"
              className="space-y-4"
            >
              <div className="text-[12.5px] text-ink-500">
                Loading deal <span className="font-mono">{rawId}</span>…
              </div>
              <div className="h-8 w-2/5 rounded bg-ink-100 animate-pulse" />
              <div className="h-4 w-1/3 rounded bg-ink-100 animate-pulse" />
              <div className="grid grid-cols-3 gap-4 pt-4">
                <div className="h-24 rounded bg-ink-100 animate-pulse" />
                <div className="h-24 rounded bg-ink-100 animate-pulse" />
                <div className="h-24 rounded bg-ink-100 animate-pulse" />
              </div>
            </div>
          )}
        </div>
    );
  }

  // Past the gate ``projectOrNull`` is non-null: we only reach here when
  // ``deal`` resolved.
  const project: Project = projectOrNull;

  const inner = (
    <div>
      {/* Header */}
      <div className="px-8 pt-7 pb-0 bg-white border-b hairline">
        <Link
          href="/projects"
          className="inline-flex items-center gap-1 text-[12px] text-ink-500 hover:text-ink-900 mb-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded eyebrow normal-case tracking-wide"
        >
          <ArrowLeft size={13} aria-hidden="true" /> Back to Projects
        </Link>

        {/* Title + IC-ready badge + right-side controls */}
        <div className="flex items-start justify-between gap-6 mb-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="font-display text-[28px] font-semibold tracking-[-0.018em] text-ink-900 leading-[1.15]">
                {project.name}
              </h1>
              <span title="Where this deal stands. Draft = early stage, no IC review yet. In Review = team is reviewing. IC Ready = ready for the investment committee.">
                {project.status && <StatusBadge value={project.status} />}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <div className="relative">
              <button
                ref={docsBtnRef}
                type="button"
                onClick={() => { setDocsOpen(o => !o); setConfidenceOpen(false); setCollabOpen(false); }}
                aria-label={`Open documents (${liveDocsLabel} files)`}
                aria-expanded={docsOpen}
                aria-haspopup="dialog"
                title="Documents uploaded vs the underwriting checklist (OM, T-12, STR report, rent roll, PIP estimate, etc.). Click to open the docs drawer."
                className="h-8 px-2.5 bg-white border border-border hover:border-ink-300 hover:bg-ink-100 rounded-md text-[12px] inline-flex items-center gap-1.5 text-ink-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <FileText size={12} aria-hidden="true" /> Docs <span className="text-ink-900 font-medium tabular-nums">{liveDocsLabel}</span>
              </button>
            </div>
            <div className="relative">
              <button
                ref={confidenceBtnRef}
                type="button"
                onMouseEnter={() => setConfidenceOpen(true)}
                onMouseLeave={() => setConfidenceOpen(false)}
                onFocus={() => setConfidenceOpen(true)}
                onBlur={() => setConfidenceOpen(false)}
                aria-label={project.aiConfidence == null ? 'Awaiting documents' : `AI confidence ${project.aiConfidence}%`}
                aria-describedby={confidenceOpen ? 'ai-confidence-tooltip' : undefined}
                title="The AI's certainty in the model, based on document completeness and extraction quality. Climbs toward 100% as you upload the full doc set."
                className="h-8 px-2.5 bg-brand-50 hover:bg-brand-100 border border-brand-100 rounded-md text-[12px] inline-flex items-center gap-1.5 text-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <Sparkles size={12} aria-hidden="true" />
                {project.aiConfidence == null ? 'Awaiting docs' : (
                  <span className="tabular-nums"><span className="font-semibold">{project.aiConfidence}%</span> AI Confidence</span>
                )}
              </button>
              {confidenceOpen && (
                <div
                  id="ai-confidence-tooltip"
                  role="tooltip"
                  className="absolute right-0 top-full mt-1.5 z-30 w-64 rounded-md border border-border bg-white p-3 shadow-card text-[11.5px] text-ink-700"
                >
                  <div className="text-[12px] font-semibold text-ink-900 mb-1.5">AI Confidence Breakdown</div>
                  <div className="space-y-1 tabular-nums">
                    <div className="flex justify-between"><span className="text-ink-500">Doc count</span><span className="font-medium text-ink-900">{docCount}</span></div>
                    <div className="flex justify-between"><span className="text-ink-500">Avg field confidence</span><span className="font-medium text-ink-900">{avgFieldConfidence == null ? '—' : `${avgFieldConfidence}%`}</span></div>
                    <div className="flex justify-between"><span className="text-ink-500">Variance flags</span><span className="font-medium text-ink-900">{varianceFlagCount == null ? '—' : varianceFlagCount}</span></div>
                  </div>
                </div>
              )}
            </div>
            <div className="w-px h-5 bg-ink-200 mx-1" aria-hidden="true" />
            <div className="relative">
              <button
                ref={collabBtnRef}
                type="button"
                onClick={() => { setCollabOpen(o => !o); setDocsOpen(false); }}
                aria-label="Manage collaborators"
                aria-expanded={collabOpen}
                aria-haspopup="dialog"
                className="h-8 w-8 inline-flex items-center justify-center hover:bg-ink-100 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <Users size={14} className="text-ink-700" aria-hidden="true" />
              </button>
              {collabOpen && (
                <>
                  <div className="fixed inset-0 z-20" onClick={() => setCollabOpen(false)} aria-hidden="true" />
                  <div
                    role="dialog"
                    aria-label="Collaborators"
                    className="absolute right-0 top-full mt-1.5 z-30 w-64 rounded-md border border-border bg-white p-3 shadow-card"
                  >
                    <div className="text-[12px] font-semibold text-ink-900 mb-2">Collaborators</div>
                    <div className="space-y-2">
                      {[
                        { initials: 'SC', name: 'Sarah Chen', role: 'Admin' },
                        { initials: 'MJ', name: 'Mike Johnson', role: 'Analyst' },
                        { initials: 'AW', name: 'Alex Wong', role: 'Analyst' },
                      ].map(p => (
                        <div key={p.name} className="flex items-center gap-2.5">
                          <div className="w-7 h-7 rounded-full bg-ink-300/30 flex items-center justify-center text-[10.5px] font-semibold text-ink-700">{p.initials}</div>
                          <div className="flex-1 min-w-0">
                            <div className="text-[12px] font-medium text-ink-900 truncate">{p.name}</div>
                            <div className="text-[10.5px] text-ink-500">{p.role}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                    <button
                      type="button"
                      onClick={() => { setCollabOpen(false); toast('Invitations available on enterprise plans', { type: 'info' }); }}
                      className="mt-3 w-full text-left text-[12px] text-brand-700 hover:text-brand-500 font-medium border-t border-border pt-2"
                    >
                      + Invite collaborators
                    </button>
                  </div>
                </>
              )}
            </div>
            <Button
              size="sm"
              variant={project.status === 'IC Ready' ? 'premium' : 'secondary'}
              aria-label="Share deal"
              onClick={onShare}
            >
              <Share2 size={12} aria-hidden="true" /> Share
            </Button>
            <KebabMenu items={headerMenu} />
            {/* Note: KebabMenu has no divider primitive; the danger styling on
                the last item visually separates Archive from the safe actions. */}
          </div>
        </div>

        {/* Hairline divider between title row and meta line. */}
        <div className="border-t hairline" />

        {/* Meta line — breathing room. */}
        <div className="flex items-center gap-5 text-[12.5px] text-ink-600 py-3">
          <div className="flex items-center gap-1.5"><MapPin size={12} className="text-ink-400" aria-hidden="true" /> {project.city}</div>
          <div className="w-px h-3 bg-ink-200" aria-hidden="true" />
          <div className="flex items-center gap-1.5">
            <Building2 size={12} className="text-ink-400" aria-hidden="true" />{' '}
            <span className="tabular-nums">{formatKeys(deal?.keys)}</span> keys
            {/* The service type is optional — omit the segment rather than print a bare dash. */}
            {project.service && project.service !== '—' ? ` · ${project.service}` : ''}
          </div>
          {/* Created date: the deal record's own, or nothing — never a literal. */}
          {project.createdAt && (
            <>
              <div className="w-px h-3 bg-ink-200" aria-hidden="true" />
              <div className="flex items-center gap-1.5"><Calendar size={12} className="text-ink-400" aria-hidden="true" /> Created {project.createdAt}</div>
            </>
          )}
        </div>

        {/* Tabs */}
        <nav
          role="tablist"
          aria-label="Project sections"
          className="flex items-center gap-1 -mb-px overflow-x-auto scrollbar-thin border-t hairline pt-1"
        >
          {tabs.filter(t => !t.adminOnly || isAdmin).map(t => {
            const Icon = t.icon;
            const isActive = activeTab === t.id;
            const isInactive = t.inactive === true;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                aria-label={t.label}
                tabIndex={isActive ? 0 : -1}
                onClick={() => setTab(t.id)}
                title={isInactive ? 'Coming soon.' : undefined}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-2.5 text-[12.5px] border-b-2 whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-t',
                  isActive
                    ? (isInactive ? 'border-ink-200 text-ink-400' : 'border-brand-500 text-brand-700 font-semibold')
                    : (isInactive ? 'border-transparent text-ink-400' : 'border-transparent text-ink-700 hover:text-ink-900'),
                )}>
                <Icon size={13} aria-hidden="true" className={isInactive ? 'opacity-60' : ''} />
                <span>{t.label}</span>
              </button>
            );
          })}
        </nav>
      </div>

      {/* Canonical Data Key — ONE strip, full width, directly under the tab bar
          on every tab (FON-72 · design/canonical/Fondok Data Key.dc.html:
          "sits directly under the tab bar on every tab · full width"). Replaces
          the per-tab EngineLegend / DataKeyLegend strips. Purely presentational,
          so it lives outside the provenance providers. */}
      <div className="px-8 pt-3">
        <DataKey />
      </div>

      {/* FON-69 — there is NO global scenario bar. Base / saved scenarios /
          + New scenario live inside the Scenario Comparison section of the
          Scenario Analysis tab (design/canonical/Scenarios Tab.dc.html:119-139),
          which is also the only place the SOURCE OF TRUTH badge renders. Model
          tabs (Overview, CAPEX, Market Comps, P&L, Financing, Partnership, Cash Flow,
          Overview → Returns, …) always show the Base Case, per FON-57 / FON-59. */}

      {/* Tab content — wrapped in ProvenanceProvider (input assumptions →
          <Sourced>) and ValueTraceProvider (computed outputs → <Traced>) so
          any number on any tab can explain itself from one pair of fetches. */}
      <ProvenanceProvider dealId={rawId}>
      <ValueTraceProvider dealId={rawId}>
      {/* Atlas AI Copilot docks to the right of the tab content and persists
          across tabs; the content column flexes narrower when it's open. */}
      <div className="flex items-start">
      <div className="p-8 max-w-[1800px] w-full min-w-0 flex-1" role="tabpanel" aria-label={`${activeLabel} content`}>
        {/* One banner per failure: the Overview (Returns section), Cash Flow and
            Scenario Analysis tabs report their own engines inline, so those are
            left out here while that tab is active. */}
        <EngineFailuresBanner
          outputs={pageEngineOutputs}
          dealId={rawId}
          excludeEngines={TAB_OWNED_ENGINES[activeTab] ?? []}
        />
        {/* FON-75 — a model CRASHED is the banner above; a model MOVED ON is
            this one. Page-level because a run that predates a block blanks a
            section on whichever tab happens to read it. */}
        <StaleRunBanner outputs={pageEngineOutputs} dealId={rawId} />
        {(
          <IntroCard
            dismissKey="trace-hint"
            title="Every number traces back"
            body={
              <>
                Figures with a{' '}
                <span className="underline decoration-dotted decoration-2 decoration-sky-500 underline-offset-2">dotted underline</span>{' '}
                are traceable — hover one (or tab to it) to see where it came from. A{' '}
                <span className="underline decoration-dotted decoration-2 decoration-emerald-500 text-emerald-700 font-medium">green</span> underline means it&apos;s grounded in your uploaded documents,{' '}
                <span className="underline decoration-dotted decoration-2 decoration-amber-500 text-amber-700 font-medium">amber</span> a seed / benchmark default, and{' '}
                <span className="underline decoration-dotted decoration-2 decoration-sky-500 text-sky-700 font-medium">blue</span> a computed value (the tooltip shows the formula + every input). The full list is in the{' '}
                <Link href={`/projects/${rawId}?tab=analysis&sub=sources`} className="font-medium text-brand-700 hover:underline">Sources ledger</Link>.
              </>
            }
          />
        )}
        {activeTab === '' && (
          <ErrorBoundary tabName={TAB_LABEL.dataRoom}><DataRoomTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'validation' && (
          <ErrorBoundary tabName="Validation"><ValidationTab dealId={String(id)} /></ErrorBoundary>
        )}
        {activeTab === 'overview' && (
          <ErrorBoundary tabName={TAB_LABEL.overview}><OverviewTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'investment' && (
          <ErrorBoundary tabName={TAB_LABEL.investment}><InvestmentTab /></ErrorBoundary>
        )}
        {activeTab === 'pl' && (
          <ErrorBoundary tabName={TAB_LABEL.pl}><PLTab /></ErrorBoundary>
        )}
        {activeTab === 'debt' && (
          <ErrorBoundary tabName={TAB_LABEL.debt}><DebtTab /></ErrorBoundary>
        )}
        {activeTab === 'cash-flow' && (
          <ErrorBoundary tabName={TAB_LABEL.cashFlow}><CashFlowTab /></ErrorBoundary>
        )}
        {activeTab === 'investment-bridge' && (
          <ErrorBoundary tabName={TAB_LABEL.investmentBridge}><InvestmentBridgeTab dealId={rawId} /></ErrorBoundary>
        )}
        {activeTab === 'partnership' && (
          <ErrorBoundary tabName={TAB_LABEL.partnership}><PartnershipTab /></ErrorBoundary>
        )}
        {activeTab === 'market' && (
          <ErrorBoundary tabName={TAB_LABEL.market}><MarketTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'forecasting' && (
          <ErrorBoundary tabName="Forecasting"><ForecastingTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'analysis' && (
          <ErrorBoundary tabName="Analysis"><AnalysisTab /></ErrorBoundary>
        )}
        {/* FON-53 — Scenario Analysis: the sensitivity experience (moved off
            Overview) up top, named-scenario comparison kept below. */}
        {activeTab === 'scenarios' && (
          <ErrorBoundary tabName={TAB_LABEL.scenarios}>
            <div className="space-y-6">
              <ScenarioAnalysisTab dealId={String(id)} />
              {/* Canonical (Scenarios Tab.dc.html) always shows the Scenario
                  Comparison section — the Base column always exists. The panel
                  self-guards live vs mock deals internally (compare API only
                  fires when ≥2 scenarios are saved). */}
              <ScenarioComparePanel
                dealId={rawId}
                scenarios={scenarios}
                {...(liveMode
                  ? {
                      onCreate: () => setScenarioEditor({ open: true, scenario: null }),
                      onEdit: (s: ScenarioRecord) =>
                        setScenarioEditor({ open: true, scenario: s }),
                      onDuplicate: (s: ScenarioRecord) => void handleDuplicateScenario(s),
                      onDelete: (s: ScenarioRecord) => void handleDeleteScenario(s),
                      onRemoveOverride: (s: ScenarioRecord, fieldPath: string) =>
                        void handleRemoveScenarioOverride(s, fieldPath),
                    }
                  : {})}
              />
            </div>
          </ErrorBoundary>
        )}
        {activeTab === 'activity' && (
          <ErrorBoundary tabName={TAB_LABEL.activity}>
            <ActivityFeed dealId={String(id)} />
          </ErrorBoundary>
        )}
        {/* FON-54 — IC Memo: the IC-ready one-pager (Deal Summary + dynamic
            recommendation) with Configure → Preview → Export/Share folded in,
            consolidating the former Analysis + Export tabs. */}
        {activeTab === 'ic-memo' && (
          <ErrorBoundary tabName={TAB_LABEL.icMemo}><ICMemoTab project={project} /></ErrorBoundary>
        )}
      </div>
      <ErrorBoundary tabName="Atlas">
        <AtlasCopilot dealId={rawId} activeTab={activeTab} activeLabel={activeLabel} />
      </ErrorBoundary>
      </div>
      </ValueTraceProvider>
      </ProvenanceProvider>

      {/* Docs side drawer — slides in from the right when the Docs pill is
          clicked. Live (UUID) deals pull from the worker via useDocuments. */}
      {docsOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-ink-900/30"
            onClick={() => setDocsOpen(false)}
            aria-hidden="true"
          />
          <aside
            role="dialog"
            aria-label="Documents"
            className="fixed right-0 top-0 bottom-0 z-50 w-[360px] bg-white border-l border-border shadow-card-hover flex flex-col"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <div>
                <div className="text-[13.5px] font-semibold text-ink-900">Documents</div>
                <div className="text-[11px] text-ink-500">{drawerDocs.length} file{drawerDocs.length === 1 ? '' : 's'}</div>
              </div>
              <button
                type="button"
                onClick={() => setDocsOpen(false)}
                aria-label="Close documents drawer"
                className="p-1 rounded hover:bg-ink-100 text-ink-500"
              >
                <X size={14} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
              {drawerDocs.length === 0 ? (
                <div className="text-[12px] text-ink-500 text-center py-6">
                  No documents yet. Upload via the Data Room tab.
                </div>
              ) : drawerDocs.map(d => (
                <div key={d.name} className="flex items-start gap-2.5 p-2 rounded-md border border-border hover:bg-ink-100">
                  <FileText size={14} className="text-ink-500 mt-0.5 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] font-medium text-ink-900 truncate">{d.name}</div>
                    <div className="text-[10.5px] text-ink-500 mt-0.5">{d.type} · {d.status}</div>
                  </div>
                </div>
              ))}
            </div>
            <div className="px-4 py-3 border-t border-border">
              <Button
                variant="secondary"
                size="sm"
                className="w-full"
                onClick={() => { setDocsOpen(false); setTab(''); }}
              >
                Open Data Room
              </Button>
            </div>
          </aside>
        </>
      )}

      {/* Duplicate deal — per-copy choice of whether cell comments travel. */}
      <DuplicateDealDialog
        open={duplicateOpen}
        dealName={duplicateName}
        busy={duplicating}
        onClose={() => setDuplicateOpen(false)}
        onConfirm={(choices) => void runDuplicate(choices)}
      />

      {/* Wave 3 W3.2 — scenario editor side panel (NO modal). */}
      {liveMode && (
        <ScenarioEditor
          open={scenarioEditor.open}
          dealId={rawId}
          scenario={scenarioEditor.scenario}
          onClose={() => setScenarioEditor({ open: false, scenario: null })}
          onSaved={() => {
            // FON-69 — which scenario's detail card is shown is owned by
            // ScenarioComparePanel's own chip row now; the page just refreshes
            // the list so the new/edited scenario and its override count land.
            void refreshScenarios();
          }}
          onRan={() => {
            void refreshScenarios();
          }}
        />
      )}
    </div>
  );

  return inner;
}

