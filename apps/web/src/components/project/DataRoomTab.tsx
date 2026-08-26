'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import {
  UploadCloud, FileText, FileSpreadsheet,
  CheckCircle2, Loader2, Circle, AlertTriangle, ArrowRight,
  ClipboardList, Sparkles, Wallet, Receipt, Banknote, TrendingUp, Coins, Users2,
  Search, X as CloseIcon, Star,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge, StatusBadge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import { engines, kimptonDocuments, templates } from '@/lib/mockData';
import { criticalCount, warnCount, varianceFlags } from '@/lib/varianceData';
import {
  api,
  isWorkerConnected,
  workerUrl,
  WorkerDocument,
  WorkerUsaliPayload,
  WorkerUsaliDeviation,
  ExtractionField,
  EngineName,
  EngineOutputResponse,
  WorkerError,
} from '@/lib/api';
import { useDocuments } from '@/lib/hooks/useDocuments';
import { useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useToast } from '@/components/ui/Toast';
import { useCurrentRole } from '@/lib/auth';
import { cn } from '@/lib/format';
import { humanizeFieldName } from '@/lib/fieldLabels';
import { CoachMark } from '@/components/help/CoachMark';
import { UsaliBadge } from './validation/UsaliBadge';
import { UsaliDeviationsAccordion } from './validation/UsaliDeviationsAccordion';
import { GapChipsStrip } from './validation/GapChipsStrip';
import { MisclassificationBanner } from './wizard/MisclassificationBanner';
import { YearMismatchBanner } from './wizard/YearMismatchBanner';
import { DocumentCoverage, type CoverageFile } from './DocumentCoverage';

// Same dependency order EngineHeader uses for run-all fallbacks — mirrors the
// worker's chain in apps/worker/app/api/model.py.
const ENGINE_ORDER: EngineName[] = [
  'revenue',
  'fb',
  'expense',
  'capital',
  'debt',
  'returns',
  'sensitivity',
  'partnership',
];

// Engine Status panel is hidden until the per-engine confidence scores
// are calibrated. Flip back to true once we trust what the bars say.
const SHOW_ENGINE_STATUS = false;

// FON-33 — the per-document USALI compliance badge is hidden for now
// (scoring not yet calibrated for prod). Flip back to true to restore
// the badge + its deviation accordion. Same escape-hatch as ENGINE_STATUS.
const SHOW_USALI_BADGE = false;

// Friendly labels for the doc-type breakdown shown in the checklist
// header. Anything not in this map gets a Title-Cased fallback.
const DOC_TYPE_LABEL: Record<string, string> = {
  OM: 'OM',
  T12: 'T-12',
  PNL: 'P&L',
  PNL_MONTHLY: 'Monthly P&L',
  PNL_YTD: 'YTD P&L',
  PNL_BENCHMARK: 'P&L Benchmark',
  STR: 'STR',
  STR_TREND: 'STR',
  CBRE_HORIZONS: 'CBRE',
  BUDGET: 'Budget',
  DEBT: 'Debt', PARTNERSHIP: 'Partnership / JV', OTHER: 'Other',
  INSURANCE: 'Insurance',
  PROPERTY_TAX: 'Prop. Tax',
  CAPEX: 'CapEx',
  PROPERTY_INFO: 'Property Info',
  LEASES: 'Leases',
  CONTRACT: 'Contract',
  SURVEYS: 'Surveys',
  ROOM_MIX: 'Room Mix',
  RENT_ROLL: 'Rent Roll',
  MARKET_STUDY: 'Market Study',
  UNKNOWN: 'Uncategorized',
};

// Canonical 10-item Data Room checklist — mirrors the wizard's
// COMPLETENESS_CATEGORIES so the two surfaces never drift. Each row
// maps to one or more upstream `doc_type` tokens; when any live
// uploaded document carries a matching token the row flips green and
// drops its REQ badge. Wave 1 expanded the DocType enum to cover every
// category — Surveys is the only one marked optional.
const REQUIRED_CHECKLIST: { label: string; match: string[] }[] = [
  { label: 'Offering Memorandum',           match: ['OM'] },
  // FON-18: a single "Financial Statements" requirement satisfied by ANY
  // financial doc (T-12 OR Annual/YTD/Monthly P&L). Previously T-12 and
  // P&L were separate required rows, so uploading a P&L still left
  // "T-12 Missing" — analysts shouldn't need to know Fondok's internal
  // taxonomy to clear the financials requirement.
  { label: 'Financial Statements (T-12 or P&L)', match: ['T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD', 'PNL_BENCHMARK'] },
  { label: 'STR / Comp Set Report',         match: ['STR', 'STR_TREND'] },
  { label: 'Insurance Records',             match: ['INSURANCE'] },
  { label: 'Property Taxes',                match: ['PROPERTY_TAX'] },
  { label: 'Room Mix / Unit Mix',           match: ['ROOM_MIX'] },
  { label: 'Historical CapEx',              match: ['CAPEX'] },
  { label: 'Basic Property Info',           match: ['PROPERTY_INFO'] },
  { label: 'Leases & Agreements',           match: ['LEASES', 'CONTRACT'] },
  { label: 'Surveys & Reviews',             match: ['SURVEYS'] },
];

// Engine Status card mapping — UI label/icon plus the underlying worker
// engine name(s) the readiness % is sourced from. Mirrors the canonical
// six-engine column the Lovable reference renders.
const ENGINE_STATUS_ROWS: {
  id: string;
  label: string;
  icon: typeof Wallet;
  engines: EngineName[];
}[] = [
  { id: 'investment',  label: 'Investment',  icon: Wallet,     engines: ['capital'] },
  { id: 'pl',          label: 'P&L',         icon: Receipt,    engines: ['revenue', 'fb', 'expense'] },
  { id: 'debt',        label: 'Debt',        icon: Banknote,   engines: ['debt'] },
  { id: 'cash-flow',   label: 'Cash Flow',   icon: TrendingUp, engines: ['revenue', 'expense'] },
  { id: 'returns',     label: 'Returns',     icon: Coins,      engines: ['returns'] },
  { id: 'partnership', label: 'Partnership', icon: Users2,     engines: ['partnership'] },
];

// Status → readiness percent. We don't have a per-engine confidence on
// the worker today, so use status as a proxy: complete=100, running/queued=50,
// failed/missing=0. Averaged across the engine ids that back a UI row.
function engineStatusReadiness(status: string | null | undefined): number {
  if (status === 'complete') return 100;
  if (status === 'running' || status === 'queued') return 50;
  return 0;
}

// Map worker doc statuses to a single label the StatusBadge knows about.
function statusLabel(s: string): string {
  switch (s) {
    case 'EXTRACTED':
      return 'Extracted';
    case 'EXTRACTING':
    case 'CLASSIFYING':
    case 'PROCESSING':
    case 'PARSING':
      return 'Processing';
    case 'FAILED':
    case 'PARSE_FAILED':
      // Previously mapped to 'Pending', which silently hid extraction
      // failures (Sam QA 2026-05-13). 'Failed' surfaces the problem
      // and the row's error_kind + error_message tell the user what
      // to do next.
      return 'Failed';
    case 'UPLOADED':
    default:
      return 'Pending';
  }
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '—';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatValue(v: unknown, unit: string | null, fieldName?: string): string {
  if (v == null) return '—';
  const fn = (fieldName ?? '').toLowerCase();
  // FON retest #5 — string values: humanize a raw period_type
  // ("trailing_twelve" → "Trailing Twelve") instead of surfacing the enum.
  if (typeof v === 'string') {
    if (fn.endsWith('period_type')) {
      return v.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }
    return v;
  }
  if (typeof v === 'number') {
    // FON retest #5 — occupancy / margin / *_pct come in as a 0..1 ratio;
    // show them as a percent (79%, not 0.79). GOP/NOI are normally dollars,
    // but when the extractor emits a margin (≤1.5) treat it as a percent.
    const pctField =
      fn.includes('occupancy') ||
      fn.endsWith('_pct') ||
      fn.endsWith('margin') ||
      ((fn.includes('gop') || fn.includes('noi')) && Math.abs(v) <= 1.5);
    if (pctField && Math.abs(v) <= 1.5) {
      return `${(v * 100).toFixed(1)}%`;
    }
    if (unit === 'USD') {
      const abs = Math.abs(v);
      if (abs >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
      if (abs >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
      return `$${v.toFixed(0)}`;
    }
    if (unit === 'ratio' || unit === 'percent') {
      return `${(v * (unit === 'percent' ? 1 : 100)).toFixed(1)}%`;
    }
    return v.toLocaleString();
  }
  return String(v);
}

export default function DataRoomTab({ projectId }: { projectId: number | string }) {
  const router = useRouter();
  const params = useParams();
  // Raw id from the URL — always a string. Could be a numeric mock id or a
  // real worker UUID. Never coerce through Number() to avoid stringifying
  // NaN into the API path.
  const projectIdStr = String(projectId);
  const fallback = projectIdStr === 'NaN' ? '' : projectIdStr;
  const rawId = (params?.id as string | undefined) ?? fallback;
  const isMockId = /^\d+$/.test(rawId);
  const isFullDoc = isMockId && Number(rawId) === 7; // Kimpton Angler

  const fileInputRef = useRef<HTMLInputElement>(null);
  const searchParams = useSearchParams();
  // Focused field-review drawer (opened by "View data" on any document).
  const [reviewDocId, setReviewDocId] = useState<string | null>(null);
  // FON-24: field name a validation finding deep-linked to (via
  // ?reviewField=…). The matching review row highlights + scrolls into view.
  const [highlightField, setHighlightField] = useState<string | null>(null);
  // Browse Templates popover — anchored to whichever button the user clicked.
  const [templatesAnchor, setTemplatesAnchor] = useState<'empty' | 'inline' | null>(null);
  const { toast } = useToast();
  // Wave 5 RBAC — per-document hard-delete admin gate.
  const currentRole = useCurrentRole();
  const isAdmin = currentRole === 'org:admin';

  // Close the templates popover on outside click / Escape.
  useEffect(() => {
    if (!templatesAnchor) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setTemplatesAnchor(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [templatesAnchor]);

  const onApplyTemplate = (name: string) => {
    setTemplatesAnchor(null);
    toast(`Template applied: ${name} (assumptions loaded)`, { type: 'success' });
  };
  // Track which doc IDs we've already toasted on extraction so we don't
  // re-fire as the polling loop re-emits the same EXTRACTED record.
  const extractionToastedRef = useRef<Set<string>>(new Set());

  const { documents, uploading, upload, extractions, error: docsError, refresh, refreshExtraction } =
    useDocuments(rawId);

  // FON-24: deep-link from a validation finding → open the cited doc's
  // review and highlight the cited field. A finding on the Analysis tab
  // routes here with ?reviewDoc=<docId>&reviewField=<field_name>.
  useEffect(() => {
    const reviewDoc = searchParams.get('reviewDoc');
    const reviewField = searchParams.get('reviewField');
    if (!reviewDoc && !reviewField) return;
    if (reviewDoc) {
      // Open the field-review drawer for the cited doc (the master-detail pane
      // was retired — the drawer is the single review surface now).
      const doc = documents.find((d) => d.id === reviewDoc);
      if (doc) setReviewDocId(doc.id);
    }
    setHighlightField(reviewField);
    // Clear the highlight after a few seconds so it reads as a transient
    // "here it is" cue, not a permanent selection.
    if (reviewField) {
      const t = setTimeout(() => setHighlightField(null), 6000);
      return () => clearTimeout(t);
    }
  }, [searchParams, documents]);

  // Wave 1 #1 — resolve a misclassification banner. The worker keeps
  // the analyst's tag until they explicitly accept Fondok's read, so
  // the resolution call is a no-op on the rare race where two analysts
  // click at once (last write wins, banner clears either way).
  const resolveClassification = useCallback(
    async (doc: WorkerDocument, useAi: boolean) => {
      try {
        await api.documents.acceptClassification(rawId, doc.id, useAi);
        toast(
          useAi
            ? `Accepted Fondok’s classification for ${doc.filename}`
            : `Kept your tag for ${doc.filename}`,
          { type: 'success' },
        );
        refresh();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        toast(`Couldn’t update classification: ${msg}`, { type: 'error' });
      }
    },
    [rawId, refresh, toast],
  );

  // Wave 1 #4 — resolve a year-mismatch banner. Symmetric with the
  // classification flow above: either side clears ``year_mismatch``.
  const resolveYear = useCallback(
    async (doc: WorkerDocument, useAi: boolean) => {
      try {
        await api.documents.acceptYear(rawId, doc.id, useAi);
        toast(
          useAi
            ? `Adopted Fondok’s year for ${doc.filename}`
            : `Kept your year for ${doc.filename}`,
          { type: 'success' },
        );
        refresh();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        toast(`Couldn’t update year: ${msg}`, { type: 'error' });
      }
    },
    [rawId, refresh, toast],
  );

  // FON-23 — accept / edit / reject a low-confidence extracted field.
  // Awaits the mutation + a refetch so the row's confidence badge and
  // "Verified/Edited" marker reflect the new state before controls re-enable.
  const handleReviewField = useCallback(
    async (
      docId: string,
      fieldName: string,
      action: 'accept' | 'edit' | 'reject',
      value?: string,
    ) => {
      try {
        await api.documents.reviewField(rawId, docId, {
          field_name: fieldName,
          action,
          ...(action === 'edit' ? { value } : {}),
        });
        const verb =
          action === 'accept' ? 'Accepted' : action === 'edit' ? 'Updated' : 'Rejected';
        toast(`${verb} ${humanizeFieldName(fieldName)}`, { type: 'success' });
        // Force-refetch this doc's extraction so the row reflects the new
        // confidence / value (the poll loop skips already-EXTRACTED docs).
        await refreshExtraction(docId);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        toast(`Couldn’t ${action} field: ${msg}`, { type: 'error' });
      }
    },
    [rawId, refreshExtraction, toast],
  );

  // FON-23 — bulk-accept every remaining needs-review field on a doc in one
  // action, then refetch ONCE. This is the scale escape-hatch: after an
  // analyst spot-checks a few against the source, they clear the tail with a
  // single click instead of N per-row accepts. In-flight state disables the
  // button so a slow batch can't be double-fired.

  // FON-18 / FON-22 — reclassify a document post-upload from the
  // DocumentCoverage dropdowns. Refetches the documents list so the new
  // doc_type / year re-buckets coverage + ranking immediately.
  const [reclassifyingDoc, setReclassifyingDoc] = useState<string | null>(null);
  const handleReclassify = useCallback(
    async (docId: string, body: { doc_type?: string; fiscal_year?: number }) => {
      setReclassifyingDoc(docId);
      try {
        await api.documents.reclassify(rawId, docId, body);
        toast('Reclassified document', { type: 'success' });
        refresh();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        toast(`Couldn’t reclassify: ${msg}`, { type: 'error' });
      } finally {
        setReclassifyingDoc(null);
      }
    },
    [rawId, refresh, toast],
  );

  // Enterprise toast policy (Sam QA 2026-06-29):
  //   • Successes: NEVER fire per-doc. The doc row already shows
  //     "N fields · X% confidence" — the row IS the status surface.
  //     A 16-doc upload should produce 0 success toasts, not 16
  //     stacked ones covering the page.
  //   • Failures: DO fire. They need attention and bypass the
  //     aggregation rule. We dedupe via the ref so a flaky polling
  //     loop doesn't re-toast the same failure.
  useEffect(() => {
    documents.forEach((d) => {
      if (extractionToastedRef.current.has(d.id)) return;
      const ex = extractions[d.id];
      if (ex && ex.status === 'EXTRACTED') {
        // Mark seen so the failure branch below doesn't fire later
        // if the row transitions back through a state machine, and
        // so any future aggregate-toast logic doesn't double-count.
        extractionToastedRef.current.add(d.id);
      } else if (d.status === 'FAILED') {
        extractionToastedRef.current.add(d.id);
        toast(`Extraction failed for ${d.filename}`, { type: 'error' });
      }
    });
  }, [documents, extractions, toast]);

  // When we're on a real (UUID) deal, use live documents; otherwise mock.
  const liveMode = isWorkerConnected() && !isMockId;

  // FON-19: how many financial docs are still moving through the pipeline
  // (parse → classify → extract). Feeds the coverage strip so it says
  // "processing N statements" instead of "no financials uploaded yet" when
  // uploads have landed but extraction hasn't finished. Not-yet-classified
  // docs (doc_type null while parsing/classifying) are counted too — they
  // may resolve to a P&L.
  const { processingFinancialsCount, failedFinancialsCount } = useMemo(() => {
    const PROCESSING = new Set(['PARSING', 'UPLOADED', 'CLASSIFYING', 'EXTRACTING']);
    const FAILED = new Set(['FAILED', 'PARSE_FAILED']);
    const FINANCIAL = new Set(['T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD', 'PNL_BENCHMARK']);
    const isFin = (d: (typeof documents)[number]) => {
      const dt = (d.doc_type || '').toUpperCase();
      return dt === '' || FINANCIAL.has(dt);
    };
    let processing = 0;
    let failed = 0;
    for (const d of documents) {
      const s = (d.status || '').toUpperCase();
      if (PROCESSING.has(s) && isFin(d)) processing += 1;
      // FON-26: failed financial extractions explain an empty coverage
      // year that isn't simply "not uploaded".
      else if (FAILED.has(s) && (d.doc_type || '').toUpperCase() !== 'OM') failed += 1;
    }
    return { processingFinancialsCount: processing, failedFinancialsCount: failed };
  }, [documents]);

  const goToVariance = () =>
    router.push(`/projects/${rawId}?tab=analysis&sub=variance`, { scroll: false });

  // Build the unified doc rows the UI renders.
  type Row = {
    id: string;
    name: string;
    type: string;
    status: string; // human-friendly status label
    rawStatus: string; // upstream status (UPLOADED / EXTRACTED / Extracted / etc.)
    size: string;
    date: string;
    fields: number;
    confidence: number;
    populates: string[];
    fieldList?: ExtractionField[];
    errorKind?: string | null;
    errorMessage?: string | null;
    /** USALI compliance scoring (ROADMAP #3). Only populated for live
     *  documents whose worker scoring has completed. */
    usaliScore?: number | null;
    usaliPayload?: WorkerUsaliPayload | WorkerUsaliDeviation[] | null;
    /** Wizard ROADMAP #1 signals — surfaced on live docs only.
     *  When ``misclassified`` is true, the row renders a
     *  ``MisclassificationBanner`` between the row card and the
     *  USALI accordion. */
    userProvidedDocType?: string | null;
    fiscalYear?: number | null;
    misclassified?: boolean;
    /** Sam QA Bug #2 v2 — Router's read at extraction time, kept
     *  separate from ``type`` so the banner renders both sides. */
    aiProposedDocType?: string | null;
    /** Wave 1 #4 signals. ``yearMismatch`` true triggers a
     *  YearMismatchBanner alongside the category banner so the analyst
     *  can resolve both with one accept/keep round-trip. */
    yearMismatch?: boolean;
    extractedPeriodYear?: number | null;
    /** FON-22 — the primary financial source of truth for this deal. */
    primaryFinancialSource?: boolean;
  };

  const docs: Row[] = useMemo(() => {
    if (liveMode) {
      return documents.map((d: WorkerDocument): Row => {
        const ex = extractions[d.id];
        const fieldList = ex?.fields ?? [];
        const overall = ex?.confidence_report?.overall ?? 0;
        return {
          id: d.id,
          name: d.filename,
          type: d.doc_type ?? '—',
          status: statusLabel(d.status),
          rawStatus: d.status,
          size: formatBytes(d.size_bytes),
          date: d.uploaded_at ? new Date(d.uploaded_at).toLocaleDateString() : '—',
          fields: fieldList.length,
          confidence: Math.round(overall * 100),
          populates: [],
          fieldList,
          errorKind: d.error_kind,
          errorMessage: d.error_message,
          usaliScore: d.usali_score ?? null,
          usaliPayload: d.usali_deviations ?? null,
          userProvidedDocType: d.user_provided_doc_type ?? null,
          fiscalYear: d.fiscal_year ?? null,
          misclassified: d.misclassified ?? false,
          aiProposedDocType: d.ai_proposed_doc_type ?? null,
          yearMismatch: d.year_mismatch ?? false,
          extractedPeriodYear: d.extracted_period_year ?? null,
          primaryFinancialSource: d.primary_financial_source ?? false,
        };
      });
    }
    if (isFullDoc) {
      return kimptonDocuments.map((d) => ({
        id: d.name,
        name: d.name,
        type: d.type,
        status: d.status,
        rawStatus: d.status,
        size: d.size,
        date: d.date,
        fields: d.fields,
        confidence: d.confidence,
        populates: d.populates,
      }));
    }
    return [];
  }, [liveMode, documents, extractions, isFullDoc]);

  // Deep-link a reviewed field to the screen where its data is seeded/used —
  // with an optional focus field so the destination can highlight it.
  const goToScreen = useCallback(
    (tab: string, focus?: string) => {
      let q = tab === 'pl' ? '?tab=pl&fin=historicals' : `?tab=${tab}`;
      if (focus) q += `&focus=${encodeURIComponent(focus)}`;
      router.push(`/projects/${rawId}${q}`, { scroll: false });
    },
    [router, rawId],
  );

  const reviewDocRow = useMemo(
    () => docs.find((d) => d.id === reviewDocId) ?? null,
    [docs, reviewDocId],
  );

  // Build the required-doc checklist by intersecting our canonical 10-item
  // list against the live `documents` array's doc_type values. An item
  // flips to "complete" the moment any uploaded doc carries one of its
  // mapped tokens. Mock mode (Kimpton id=7) sets the first four complete
  // so the demo deal still shows progress without needing a live worker.
  const uploadedDocTypes = useMemo(() => {
    if (liveMode) {
      return new Set(
        documents
          .map((d) => (d.doc_type ?? '').toUpperCase().trim())
          .filter(Boolean),
      );
    }
    if (isFullDoc) return new Set(['OM', 'T12', 'STR', 'STR_TREND']);
    return new Set<string>();
  }, [liveMode, documents, isFullDoc]);

  const checklist = REQUIRED_CHECKLIST.map((item) => ({
    name: item.label,
    complete: item.match.some((m) => uploadedDocTypes.has(m)),
  }));

  const completeCount = checklist.filter((d) => d.complete).length;

  // Per-doc-type breakdown for the Document Checklist header — shows the
  // actual document count (not the checklist-row count) so uploading a
  // 2nd P&L visibly moves the number, and groups identical types.
  const docCount = docs.length;
  const typeBreakdown = useMemo(() => {
    const counts = new Map<string, number>();
    for (const d of docs) {
      const raw = (d.type ?? '').toUpperCase().trim();
      if (!raw || raw === '—') continue;
      const label = DOC_TYPE_LABEL[raw] ?? raw
        .toLowerCase()
        .split('_')
        .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
        .join(' ');
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    // Stable, readable ordering: by count desc, then label asc.
    return [...counts.entries()]
      .sort((a, b) => (b[1] - a[1]) || a[0].localeCompare(b[0]))
      .map(([label, n]) => `${n} ${label}`);
  }, [docs]);

  // Engine readiness — derived from live engine outputs when available,
  // otherwise the static mock progress per engine label.
  const { outputs: engineOutputs } = useEngineOutputs(liveMode ? rawId : '');

  const engineRows = ENGINE_STATUS_ROWS.map((row) => {
    if (liveMode && engineOutputs?.engines) {
      const pcts = row.engines.map((name) =>
        engineStatusReadiness(engineOutputs.engines[name]?.status),
      );
      const avg = pcts.length
        ? Math.round(pcts.reduce((a, b) => a + b, 0) / pcts.length)
        : 0;
      return { ...row, progress: avg };
    }
    // Mock fallback — re-use the existing mock engines list keyed by id
    // so the visual stays identical for non-live deals.
    const mock = engines.find((e) => e.id === row.id);
    return { ...row, progress: mock?.progress ?? 0 };
  });

  // ─── Run Full Underwriting (Data Room CTA) ─────────────────────────
  // Mirrors EngineHeader's run-all wiring but lives at the Data Room level
  // so users have a single, prominent kickoff after uploads land. Each
  // engine tab still exposes a per-engine Run button as a secondary
  // affordance for re-runs.
  const [fullRunId, setFullRunId] = useState<string | null>(null);
  const [fullRunRows, setFullRunRows] = useState<EngineOutputResponse[]>([]);
  const [fullRunStartedAt, setFullRunStartedAt] = useState<number | null>(null);
  const [fullRunExpected, setFullRunExpected] = useState<EngineName[]>([]);
  const [fullRunNumber, setFullRunNumber] = useState(0);

  // The hook must always be called (Rules of Hooks). When the deal id is
  // not a real worker UUID we just never invoke `run()`.
  // Enterprise notification policy (Sam QA 2026-06-29 — "i want
  // enterprise standard only"):
  //   • Run start: SILENT. The inline "Running underwriting…"
  //     strip below renders progress. No kickoff toast.
  //   • Run complete (cached, all runtime_ms ~0): SILENT. Data
  //     just appears on the Engines tab. Don't celebrate 0ms.
  //   • Run complete (real): ONE concise toast pointing at the
  //     Engines tab. No headline metric in the toast (it would
  //     duplicate what the user is about to see) — just a clean
  //     "go look" signal.
  //   • Run failure: ONE toast with the failure summary
  //     (failures bypass aggregation — they need attention).
  const fullRun = useEngineRun(liveMode ? rawId : '', 'returns', {
    runMode: 'all',
    onRunAllStarted: (id, eng) => {
      setFullRunId(id);
      setFullRunRows([]);
      setFullRunStartedAt(Date.now());
      setFullRunExpected(eng.length > 0 ? eng : ENGINE_ORDER);
      setFullRunNumber((n) => n + 1);
    },
    onRunAllProgress: (rows) => {
      setFullRunRows(rows);
    },
    onAllComplete: (rows) => {
      setFullRunRows(rows);
      // Cached runs return effectively instantly. Sum the
      // per-engine runtimes (null → 0). If the total is under
      // half a second, this was a no-op refresh — stay silent.
      const totalRuntimeMs = rows.reduce(
        (sum, r) => sum + (r.runtime_ms ?? 0),
        0,
      );
      if (totalRuntimeMs < 500) return;
      const failed = rows.filter((r) => r.status === 'failed').length;
      if (failed > 0) {
        toast(
          `Underwriting finished with ${failed} engine${failed === 1 ? '' : 's'} failing — open the Engines tab to inspect`,
          { type: 'error' },
        );
      } else {
        toast('Underwriting complete — results on the Engines tab', {
          type: 'success',
        });
      }
    },
  });

  // Live worker uses raw `EXTRACTED`; mock kimpton rows use `'Extracted'`.
  const hasExtractedDoc = docs.some(
    (d) => d.rawStatus === 'EXTRACTED' || d.rawStatus === 'Extracted',
  );
  const fullRunRunning = fullRun.status === 'running';
  // Gate the button on liveMode so the Kimpton demo deal (numeric id)
  // doesn't trigger the "Deal id missing — open the deal page first"
  // toast: useEngineRun is constructed with an empty dealId in non-live
  // mode, so .run() short-circuits to that error message. Mock deals
  // already display pre-computed engine outputs so the button is moot.
  const fullRunDisabled = !liveMode || !hasExtractedDoc || fullRunRunning;
  const fullRunTooltip = !liveMode
    ? isWorkerConnected()
      ? 'Demo deal — engine outputs are pre-computed. Create a new project to run the full pipeline.'
      : 'Worker not connected — engines are read-only on the demo'
    : !hasExtractedDoc
      ? 'Upload + extract a T-12 and OM first'
      : fullRunRunning
        ? 'Underwriting in progress…'
        : 'Run all 8 engines in dependency order';

  const onRunFullUnderwriting = () => {
    if (fullRunDisabled) return;
    void fullRun.run();
  };

  // ─── Auto-run on extraction complete ───────────────────────────────
  // Sam asked for engines to fire automatically once a document finishes
  // extracting, instead of users having to click a CTA. We track the
  // EXTRACTED count and trigger fullRun.run() whenever it ticks up,
  // debounced 2.5s so a multi-doc upload only kicks off one run. The
  // ref keeps the latest fullRun closure without forcing it into the
  // effect dep list (which would re-fire on every render).
  const extractedDocCount = docs.filter(
    (d) => d.rawStatus === 'EXTRACTED' || d.rawStatus === 'Extracted',
  ).length;
  const autoRunRef = useRef<{
    initialized: boolean;
    lastSeen: number;
    run: () => void;
  }>({ initialized: false, lastSeen: 0, run: () => {} });
  autoRunRef.current.run = onRunFullUnderwriting;

  useEffect(() => {
    if (!liveMode) return;
    if (!autoRunRef.current.initialized) {
      // First render — record the current count as baseline so we don't
      // auto-fire on a page refresh against an already-extracted deal.
      autoRunRef.current.initialized = true;
      autoRunRef.current.lastSeen = extractedDocCount;
      return;
    }
    if (extractedDocCount <= autoRunRef.current.lastSeen) return;
    if (fullRunRunning || fullRunDisabled) return;
    const t = setTimeout(() => {
      autoRunRef.current.lastSeen = extractedDocCount;
      autoRunRef.current.run();
    }, 2500);
    return () => clearTimeout(t);
  }, [extractedDocCount, fullRunRunning, fullRunDisabled, liveMode]);

  const onPickFiles = () => fileInputRef.current?.click();

  // Shared upload path used by both the <input> picker and the
  // drag-and-drop handlers. The drop zone was previously visual-only
  // (Rani's QA flagged "drag-and-drop stopped working" — it never had
  // a real handler attached).
  //
  // Wave 1 B3: filter drag-drop input by extension before staging so
  // a stray .heic / .mov / .zip never even hits the worker. The
  // <input accept=> attribute only filters the picker dialog, NOT
  // drag-drop, on every browser.
  const ALLOWED_DROP_EXTENSIONS = new Set([
    '.pdf', '.xls', '.xlsx', '.xlsm', '.csv', '.doc', '.docx',
  ]);
  // Mirrors the worker's MAX_UPLOAD_MB default (apps/worker/app/config.py).
  // Client-side check is purely a UX shortcut — the server is still the
  // source of truth, so a tenant bumping MAX_UPLOAD_MB above 50 will
  // still see the upload land (the toast just fires pre-flight at the
  // old cap until the constant here is bumped to match).
  const MAX_UPLOAD_MB = 50;
  const MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024;
  const handleUpload = async (files: File[]) => {
    if (files.length === 0) return;
    if (!liveMode) {
      toast(
        isWorkerConnected()
          ? 'Uploads available on deals created via "New Project".'
          : 'Uploads available once the workspace is provisioned.',
        { type: 'error' },
      );
      return;
    }
    const allowed: File[] = [];
    for (const f of files) {
      const dot = f.name.lastIndexOf('.');
      const ext = dot >= 0 ? f.name.slice(dot).toLowerCase() : '';
      if (!ALLOWED_DROP_EXTENSIONS.has(ext)) {
        toast(
          `${f.name}: unsupported file type — Fondok accepts PDF, Excel, CSV, Word.`,
          { type: 'error' },
        );
        continue;
      }
      if (f.size > MAX_UPLOAD_BYTES) {
        const mb = (f.size / 1024 / 1024).toFixed(1);
        toast(
          `${f.name}: ${mb} MB exceeds the ${MAX_UPLOAD_MB} MB upload cap — compress the PDF or split the workbook.`,
          { type: 'error' },
        );
        continue;
      }
      allowed.push(f);
    }
    if (allowed.length === 0) return;
    allowed.forEach((f) =>
      toast(`Uploading ${f.name}…`, { type: 'info', duration: 2500 }),
    );
    try {
      await upload(allowed);
    } catch (err) {
      console.error('upload failed', err);
      const msg = err instanceof Error ? err.message : String(err);
      toast(`Upload failed: ${msg}`, { type: 'error' });
    }
  };

  const onFilesSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = ''; // reset so same file can be re-picked
    await handleUpload(files);
  };

  // Drag-and-drop wiring. `isDragActive` flips the dashed-border zone
  // to brand color while a drag is in progress so the user gets
  // feedback before they drop.
  const [isDragActive, setIsDragActive] = useState(false);
  const dragCounterRef = useRef(0);
  const onDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer?.types?.includes('Files')) {
      dragCounterRef.current += 1;
      setIsDragActive(true);
    }
  };
  const onDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Required for the drop event to fire on most browsers.
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  };
  const onDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = Math.max(0, dragCounterRef.current - 1);
    if (dragCounterRef.current === 0) setIsDragActive(false);
  };
  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current = 0;
    setIsDragActive(false);
    const dropped = Array.from(e.dataTransfer?.files ?? []);
    await handleUpload(dropped);
  };

  // Data Room → Financials: count low-confidence values on financial docs so
  // we can route the analyst into the guided Review on the Financials tab
  // (validation lives there now, not a duplicate P&L here — team sync 2026-08).
  const flaggedFinancialCount = useMemo(() => {
    if (!liveMode) return 0;
    const FIN = new Set(['T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD', 'PNL_BENCHMARK']);
    let n = 0;
    for (const d of documents) {
      if (!FIN.has((d.doc_type ?? '').toUpperCase())) continue;
      for (const f of extractions[d.id]?.fields ?? []) {
        if (!f.reviewed && (f.confidence ?? 1) < 0.85) n += 1;
      }
    }
    return n;
  }, [liveMode, documents, extractions]);

  return (
    <div className="space-y-5">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.xlsx,.xlsm,.xls,.csv,.doc,.docx,.ppt,.pptx"
        onChange={onFilesSelected}
        className="hidden"
      />

      {/* Route financial-data validation to the Financials tab's guided
          Review (source doc + line for each flagged value). */}
      {liveMode && flaggedFinancialCount > 0 && (
        <button
          type="button"
          onClick={() => router.push(`/projects/${rawId}?tab=pl&fin=historicals`, { scroll: false })}
          className="w-full flex items-center gap-3 px-4 py-3 rounded-lg border border-warn-500/40 bg-warn-50 hover:bg-warn-100 transition-colors text-left"
        >
          <ClipboardList size={18} className="text-warn-700 flex-shrink-0" aria-hidden="true" />
          <div className="flex-1 min-w-0">
            <div className="text-[13px] font-semibold text-warn-700">
              {flaggedFinancialCount} financial value{flaggedFinancialCount === 1 ? '' : 's'} need your review
            </div>
            <div className="text-[12px] text-warn-700/80">
              Fix them in place on the Historicals worksheet — flagged cells show the source document + line.
            </div>
          </div>
          <span className="inline-flex items-center gap-1 text-[12.5px] font-medium text-warn-700 flex-shrink-0">
            Open Historicals <ArrowRight size={14} />
          </span>
        </button>
      )}

      {/* Coverage gap chips — wave 1 ROADMAP #7. Sits at the top of the
          Data Room so missing-year flags greet the user before they
          start scrolling through the upload list. The same component
          also renders on the Validation tab (different mount, same
          backend). Auto-hides for mock/numeric ids. */}
      <CoachMark
        anchorId="dataroom-gap-chips"
        viewKey="dataroom"
        order={0}
        title="Coverage gaps you can fix in one click"
        body="Coverage gaps surface missing years and partial months. Click any chip to upload the specific document we're missing — Fondok routes it to the right extractor automatically."
        side="bottom"
        learnMoreHref="/methodology#extraction"
      >
        <GapChipsStrip
          dealId={rawId}
          surface="dataroom"
          onUploadClick={() => onPickFiles()}
          processingCount={processingFinancialsCount}
          failedCount={failedFinancialsCount}
        />
      </CoachMark>

      {/* Critical-variance / running-underwriting strip — single inline
          row that surfaces the two cross-cutting signals without
          consuming a full Card. The intro/header card and the
          "Data Room" title were deleted (Wave 1 UX reduction): the
          tab nav already labels the surface, the Document Checklist
          carries the "X extracted of Y required" progress, and the
          per-row pills carry per-doc status. */}
      {((isFullDoc && criticalCount > 0) || fullRunRunning) && (
        <div className="flex items-center justify-between gap-3 -mb-1">
          {isFullDoc && criticalCount > 0 ? (
            <button
              onClick={goToVariance}
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-md bg-danger-50 hover:bg-danger-500 hover:text-white text-danger-700 border border-danger-500/30 transition-colors group"
            >
              <AlertTriangle size={13} />
              <span className="text-[12px] font-semibold">
                {criticalCount} critical · {warnCount} warn variance flags
              </span>
              <ArrowRight size={12} />
            </button>
          ) : <span />}
          {fullRunRunning && (
            <span className="inline-flex items-center gap-2 text-[12px] text-ink-500">
              <span className="inline-block w-2 h-2 rounded-full bg-brand-500 animate-pulse" />
              Running underwriting · {fullRunRows.filter((r) => r.status === 'complete').length}/{fullRunExpected.length || 8} complete
              {fullRunStartedAt
                ? ` · ${((Date.now() - fullRunStartedAt) / 1000).toFixed(0)}s`
                : ''}
            </span>
          )}
        </div>
      )}

      {/* Engine-run progress lives inline above (single status line on
          this tab) and on the Engines tab itself. The old
          bottom-right floating panel was deleted (Sam QA 2026-06-29
          — "i want enterprise standard only"): institutional users
          expect inline status, not consumer-app overlays that block
          content. Status surfaces:
            • this tab while running → inline strip above
            • completion → ONE concise success toast (see
              onAllComplete handler) — cached runs stay silent
            • Engines tab → per-engine rows render live as they
              finish, no duplication needed. */}

      {liveMode && docs.length === 0 ? (
        <Card className="p-8">
          <div
            onClick={onPickFiles}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') onPickFiles();
            }}
            onDragEnter={onDragEnter}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            className={cn(
              'cursor-pointer border-2 border-dashed rounded-lg py-12 px-6 text-center transition-colors',
              isDragActive
                ? 'border-brand-500 bg-brand-50/60'
                : 'border-ink-300 hover:border-brand-500 hover:bg-brand-50/40',
            )}
          >
            <div className="w-14 h-14 mx-auto rounded-lg bg-brand-50 flex items-center justify-center mb-3">
              <UploadCloud size={26} className="text-brand-500" />
            </div>
            <div className="text-[14px] font-semibold text-ink-900">
              Upload OM, T-12, monthly P&amp;Ls, STR, or CBRE Horizons to begin underwriting
            </div>
            <div className="text-[12px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              Drag and drop, or click to select. Accepts PDF, .xlsx / .xlsm, .xls, and .csv —
              extractor routes each file by type and writes structured fields to the deal record.
            </div>
            <div className="flex items-center justify-center gap-2 mt-4">
              <Button variant="primary" size="sm" disabled={uploading}>
                {uploading ? <Loader2 size={12} className="animate-spin" /> : null}
                {uploading ? 'Uploading…' : 'Choose Files'}
              </Button>
              <div className="relative">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setTemplatesAnchor((cur) => (cur === 'empty' ? null : 'empty'));
                  }}
                  aria-haspopup="menu"
                  aria-expanded={templatesAnchor === 'empty'}
                >
                  Browse Templates
                </Button>
                {templatesAnchor === 'empty' && (
                  <TemplatesPopover
                    onApply={onApplyTemplate}
                    onClose={() => setTemplatesAnchor(null)}
                  />
                )}
              </div>
            </div>
          </div>
          {docsError && (
            <div className="mt-3 px-3 py-2 rounded-md bg-danger-50 text-danger-700 text-[11.5px] flex items-center gap-2">
              <AlertTriangle size={12} /> {docsError}
              <button onClick={refresh} className="ml-auto underline hover:no-underline">Retry</button>
            </div>
          )}
        </Card>
      ) : (
        <Card
          className={cn(
            'p-5 transition-colors',
            isDragActive && 'ring-2 ring-brand-500 bg-brand-50/40',
          )}
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
        >
          <div className="flex items-center gap-4">
            <div className="w-14 h-14 rounded-lg bg-brand-50 flex items-center justify-center flex-shrink-0">
              <UploadCloud size={24} className="text-brand-500" />
            </div>
            <div className="flex-1">
              <h3 className="text-[14px] font-semibold text-ink-900">Upload Documents</h3>
              <p className="text-[12px] text-ink-500 mt-0.5">
                Drag and drop OM, T12, STR reports · AI auto-extracts key data
              </p>
            </div>
            <Button variant="primary" size="sm" onClick={onPickFiles} disabled={uploading}>
              {uploading ? <Loader2 size={12} className="animate-spin" /> : null}
              {uploading ? 'Uploading…' : 'Choose Files'}
            </Button>
            <div className="relative">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setTemplatesAnchor((cur) => (cur === 'inline' ? null : 'inline'))}
                aria-haspopup="menu"
                aria-expanded={templatesAnchor === 'inline'}
              >
                Browse Templates
              </Button>
              {templatesAnchor === 'inline' && (
                <TemplatesPopover
                  onApply={onApplyTemplate}
                  onClose={() => setTemplatesAnchor(null)}
                />
              )}
            </div>
          </div>
          {docsError && liveMode && (
            <div className="mt-3 px-3 py-2 rounded-md bg-danger-50 text-danger-700 text-[11.5px] flex items-center gap-2">
              <AlertTriangle size={12} /> {docsError}
              <button onClick={refresh} className="ml-auto underline hover:no-underline">Retry</button>
            </div>
          )}
        </Card>
      )}

      {/* Sam QA 8/21 — Upload + Coverage gaps now sit at the top; the document
          coverage list (category rows + files + reclassify + review) follows. */}
      {liveMode && (
        <DocumentCoverage
          files={docs.map(
            (d): CoverageFile => ({
              id: d.id,
              name: d.name,
              docType: d.type === '—' ? '' : d.type,
              fields: d.fields,
              confidence: d.confidence,
              toReview: (d.fieldList ?? []).filter(
                (f) => Math.round((f.confidence ?? 0) * 100) < 85 && !f.reviewed,
              ).length,
              fiscalYear: d.fiscalYear ?? null,
              status: d.rawStatus,
            }),
          )}
          onReclassify={handleReclassify}
          onOpenDoc={(docId, financial) => {
            // Sam QA 8/21: Financial Statements' "View data" jumps straight to
            // the Financials tab (where their data lands); other documents open
            // a dedicated document-detail screen.
            if (financial) {
              router.push(`/projects/${rawId}?tab=pl&fin=historicals`, { scroll: false });
            } else {
              router.push(`/projects/${rawId}/documents/${docId}`, { scroll: false });
            }
          }}
          busyDocId={reclassifyingDoc}
        />
      )}

      {/* FON-18 / FON-31 — the old worker-fed Deal Readiness card is retired
          to avoid two coverage cards. Demo deals still get the Checklist. */}
      <div className={cn('grid gap-5', SHOW_ENGINE_STATUS ? 'grid-cols-2' : 'grid-cols-1')}>
        {/* Document Checklist — demo-deal fallback only. Live deals use the
            unified DocumentCoverage surface above (FON-18 / FON-31). */}
        {!liveMode && (
        <Card className="p-5">
          <div className="flex items-start justify-between mb-4 gap-3">
            <div className="flex items-center gap-2">
              <ClipboardList size={16} className="text-brand-500" />
              <h3 className="text-[14px] font-semibold text-ink-900">Document Checklist</h3>
            </div>
            <div className="text-right">
              <div className="text-[12px] text-ink-700 tabular-nums">
                {docCount} {docCount === 1 ? 'document' : 'documents'}
              </div>
              {typeBreakdown.length > 0 && (
                <div className="text-[11px] text-ink-500 mt-0.5">
                  {typeBreakdown.join(' · ')}
                </div>
              )}
            </div>
          </div>
          <div className="mb-4">
            <div className="flex justify-between text-[11px] text-ink-500 mb-1">
              <span>Underwriting Ready</span>
              <span className="tabular-nums">
                {Math.round((completeCount / checklist.length) * 100)}%
              </span>
            </div>
            <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
              <div
                className="h-full bg-brand-500 transition-all"
                style={{ width: `${(completeCount / checklist.length) * 100}%` }}
              />
            </div>
          </div>
          <div className="space-y-2">
            {checklist.map((d) => (
              <div key={d.name} className="flex items-center gap-3 py-1.5">
                {d.complete
                  ? <CheckCircle2 size={15} className="text-success-500 flex-shrink-0" />
                  : <Circle size={15} className="text-ink-300 flex-shrink-0" />}
                <span
                  className={`text-[12.5px] flex-1 ${d.complete ? 'text-ink-900' : 'text-ink-500'}`}
                >
                  {d.name}
                </span>
                {!d.complete && <Badge tone="red">REQ</Badge>}
              </div>
            ))}
          </div>
        </Card>
        )}

        {/* Engine Status — per-engine readiness derived from live worker
            engine outputs when available, mock progress otherwise.
            Currently hidden via SHOW_ENGINE_STATUS until confidence
            scores are calibrated. */}
        {SHOW_ENGINE_STATUS && (
        <Card className="p-5">
          <div className="flex items-center gap-2 mb-1">
            <Sparkles size={16} className="text-brand-500" />
            <h3 className="text-[14px] font-semibold text-ink-900">Engine Status</h3>
          </div>
          <p className="text-[11.5px] text-ink-500 mb-4 leading-relaxed">
            Each engine builds part of the model (P&amp;L, Debt, Returns, etc.). The
            percentage is how confident the engine is, based on which documents
            you&apos;ve uploaded.
          </p>
          <div className="space-y-3.5">
            {engineRows.map((e) => {
              const Icon = e.icon;
              return (
                <div
                  key={e.id}
                  title={`${e.label} is ${e.progress}% ready — climbs as you upload the documents this engine needs.`}
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="inline-flex items-center gap-2 text-[12px] text-ink-700 font-medium">
                      <Icon size={13} className="text-ink-500" />
                      {e.label}
                    </span>
                    <span className="text-[11px] text-ink-500 tabular-nums">{e.progress}%</span>
                  </div>
                  <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-brand-500 transition-all"
                      style={{ width: `${e.progress}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
          <div className="text-[11px] text-ink-500 mt-4 pt-4 border-t border-border">
            Upload more documents to increase confidence.
          </div>
        </Card>
        )}
      </div>

      {reviewDocRow && (
        <DocumentReviewDrawer
          doc={reviewDocRow}
          liveMode={liveMode}
          highlightField={highlightField}
          onClose={() => setReviewDocId(null)}
          onGoTo={(t, focus) => { setReviewDocId(null); goToScreen(t, focus); }}
          onReview={handleReviewField}
        />
      )}
    </div>
  );
}

// Focused, in-place field review — opens as a right-side drawer from "View
// data" so the analyst reviews a document AND jumps to where each field is used,
// without scrolling to a distant section. Reuses DataRow (accept/edit + the
// per-field "→ Screen" deep-link).
function DocumentReviewDrawer({
  doc, liveMode, highlightField, onClose, onGoTo, onReview,
}: {
  doc: { id: string; name: string; type: string; confidence: number; fieldList?: ExtractionField[] };
  liveMode: boolean;
  highlightField?: string | null;
  onClose: () => void;
  onGoTo: (tab: string, focus?: string) => void;
  onReview: (docId: string, fieldName: string, action: 'accept' | 'edit' | 'reject', value?: string) => Promise<void>;
}) {
  const fields = doc.fieldList ?? [];
  // Needs-review first (low-confidence, unreviewed), then by confidence asc.
  const sorted = useMemo(
    () => [...fields].sort((a, b) => (a.confidence ?? 1) - (b.confidence ?? 1)),
    [fields],
  );
  const low = fields.filter((f) => (f.confidence ?? 1) < 0.85 && !f.reviewed).length;
  // Distinct screens this document feeds — a multi-destination doc (the OM)
  // can jump to any of them directly, not just per-field.
  const dests = useMemo(() => {
    const ORDER = ['overview', 'investment', 'pl', 'debt', 'cash-flow', 'returns', 'market', 'forecasting'];
    const seen = new Map<string, { tab: string; label: string }>();
    for (const f of fields) {
      const d = fieldDestination(f.field_name);
      if (d && !seen.has(d.tab)) seen.set(d.tab, d);
    }
    // A financial statement doesn't only feed Financials — its NOI / GOP drive
    // Cash Flow, Returns, Debt (DSCR) and the Overview KPIs. Surface that reach.
    const dt = (doc.type ?? '').toUpperCase();
    if (['T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD'].includes(dt)) {
      for (const d of [
        { tab: 'pl', label: 'Financials' },
        { tab: 'overview', label: 'Overview' },
        { tab: 'cash-flow', label: 'Cash Flow' },
        { tab: 'returns', label: 'Returns' },
        { tab: 'debt', label: 'Debt' },
      ]) if (!seen.has(d.tab)) seen.set(d.tab, d);
    }
    return [...seen.values()].sort((a, b) => ORDER.indexOf(a.tab) - ORDER.indexOf(b.tab));
  }, [fields, doc.type]);

  // The document's dominant per-field destination. A field only shows its
  // "→ Screen" chip when it DIFFERS from this, so a P&L (every field →
  // Financials) isn't a wall of identical chips, while the OM still flags the
  // fields that land somewhere unexpected. The top "Feeds these screens" row
  // carries the always-visible navigation.
  const dominantTab = useMemo(() => {
    const tally = new Map<string, number>();
    for (const f of fields) {
      const d = fieldDestination(f.field_name);
      if (d) tally.set(d.tab, (tally.get(d.tab) ?? 0) + 1);
    }
    let best: string | null = null;
    let max = 0;
    for (const [tab, n] of tally) if (n > max) { max = n; best = tab; }
    return best;
  }, [fields]);

  // Search across all extracted fields (label, raw path, or value) — a doc can
  // carry hundreds of fields, so jumping to "insurance" / "mgmt fee" / a number
  // matters.
  const [q, setQ] = useState('');
  const query = q.trim().toLowerCase();
  const visible = useMemo(() => {
    if (!query) return sorted;
    return sorted.filter((f) => (
      humanizeFieldName(f.field_name).toLowerCase().includes(query) ||
      (f.field_name ?? '').toLowerCase().includes(query) ||
      String(formatValue(f.value, f.unit, f.field_name)).toLowerCase().includes(query)
    ));
  }, [sorted, query]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-ink-900/25" />
      <div
        className="relative w-full max-w-[540px] h-full bg-card border-l border-border shadow-xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 z-10 bg-card border-b border-border px-5 py-4 flex items-start justify-between">
          <div className="min-w-0">
            <div className="text-[10.5px] uppercase tracking-wider text-ink-500">Field review</div>
            <h4 className="text-[14px] font-semibold text-ink-900 truncate" title={doc.name}>{doc.name}</h4>
            <div className="flex items-center gap-2 mt-1 text-[11px] text-ink-500">
              <Badge tone="gray">{DOC_TYPE_LABEL[doc.type] ?? doc.type}</Badge>
              <span className="tabular-nums">{fields.length} fields</span>
              {low > 0 && <span className="text-warn-700 tabular-nums">· {low} to review</span>}
            </div>
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="p-1 text-ink-400 hover:text-ink-900">
            <CloseIcon size={16} />
          </button>
        </div>
        {dests.length > 0 && (
          <div className="px-5 py-3 border-b border-border">
            <div className="text-[10px] uppercase tracking-wider text-ink-500 mb-1.5">Feeds these screens</div>
            <div className="flex flex-wrap gap-1.5">
              {dests.map((d) => (
                <button
                  key={d.tab}
                  type="button"
                  onClick={() => onGoTo(d.tab)}
                  className="text-[11px] px-2.5 py-1 rounded-full border border-border text-ink-700 hover:border-brand-500 hover:text-brand-700 transition-colors inline-flex items-center gap-1"
                >
                  {d.label} <ArrowRight size={11} />
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="px-5 py-2.5 border-b border-border bg-card sticky top-0 z-[5]">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-400 pointer-events-none" aria-hidden="true" />
            <input
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search fields — e.g. insurance, mgmt fee, 88,150"
              aria-label="Search extracted fields"
              className="w-full text-[11.5px] rounded-md border border-border bg-card pl-8 pr-8 py-1.5 text-ink-900 placeholder:text-ink-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            />
            {q && (
              <button type="button" onClick={() => setQ('')} aria-label="Clear search" className="absolute right-2 top-1/2 -translate-y-1/2 text-ink-400 hover:text-ink-700">
                <CloseIcon size={13} />
              </button>
            )}
          </div>
          <div className="text-[10.5px] text-ink-500 mt-1.5">
            {query
              ? `${visible.length} result${visible.length === 1 ? '' : 's'} for “${q.trim()}”`
              : <>Click any field’s <span className="text-brand-700 font-medium">→</span> to jump to exactly where it’s used.</>}
          </div>
        </div>
        <div className="px-5 pb-6">
          {!liveMode ? (
            <div className="text-[11.5px] text-ink-500 py-6 text-center">Field review is available on live deals.</div>
          ) : visible.length === 0 ? (
            <div className="text-[11.5px] text-ink-500 py-6 text-center">
              {query ? `No fields match “${q.trim()}”.` : 'No extracted fields on this document.'}
            </div>
          ) : (
            visible.map((f) => (
              <DataRow
                key={f.field_name}
                label={humanizeFieldName(f.field_name)}
                value={formatValue(f.value, f.unit, f.field_name)}
                confidence={Math.round((f.confidence ?? 0) * 100)}
                rawLabel={f.field_name}
                reviewed={f.reviewed ?? null}
                snippet={f.raw_text ?? null}
                sourcePage={f.source_page ?? null}
                highlight={highlightField != null && f.field_name === highlightField}
                destination={fieldDestination(f.field_name)}
                dominantTab={dominantTab}
                onGoTo={onGoTo}
                onReview={(action, value) => onReview(doc.id, f.field_name, action, value)}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}

// Map an extracted field to the screen where its data is seeded / used, so the
// review pane can deep-link any field straight to where it lands — for ANY
// document type. Prefix-first (authoritative), keyword fallback second.
function fieldDestination(fieldName?: string): { tab: string; label: string } | null {
  if (!fieldName) return null;
  const n = fieldName.toLowerCase();
  const FIN = { tab: 'pl', label: 'Financials' };
  // Prefix routing (the field's schema section is the strongest signal).
  if (n.startsWith('p_and_l_usali') || n.startsWith('fb_operations') || n.startsWith('parking_operations')) return FIN;
  if (n.startsWith('ttm_performance')) return { tab: 'forecasting', label: 'Forecasting' };
  if (n.startsWith('in_place_debt')) return { tab: 'debt', label: 'Debt' };
  if (n.startsWith('transaction_comps') || n.startsWith('comparable_sales') || n.startsWith('market_overview')) return { tab: 'market', label: 'Market' };
  if (n.startsWith('broker_proforma') || n.startsWith('asking_price')) return { tab: 'investment', label: 'Investment' };
  if (n.startsWith('property_overview') || n.startsWith('ttm_summary_per_om')) return { tab: 'overview', label: 'Overview' };
  if (n.startsWith('capex') || n.startsWith('capital_plan')) return { tab: 'investment', label: 'Investment' };
  // Keyword fallback for flat/legacy field names.
  if (/(occupancy|adr|revpar|revenue|expense|\bnoi\b|\bgop\b|insurance|property_tax|mgmt|ffe|departmental|undistributed|fixed_charge)/.test(n)) return FIN;
  if (/(loan|interest_rate|amortization|\bltv\b|debt)/.test(n)) return { tab: 'debt', label: 'Debt' };
  if (/(cap_rate|comp_|comparable)/.test(n)) return { tab: 'market', label: 'Market' };
  if (/(purchase|price_per_key|renovation|asking)/.test(n)) return { tab: 'investment', label: 'Investment' };
  if (/(keys|year_built|chain_scale|brand)/.test(n)) return { tab: 'overview', label: 'Overview' };
  return null;
}

// Reformat scientific-notation numbers (4.30618e+06) inside a source snippet
// into readable, comma-grouped integers so the grounding quote reads like the
// document, not a machine dump.
function formatSnippet(s: string): string {
  return s.replace(/\b\d+(?:\.\d+)?e[+-]?\d+\b/gi, (m) => {
    const n = Number(m);
    return Number.isFinite(n) ? Math.round(n).toLocaleString() : m;
  });
}

function DataRow({
  label,
  value,
  confidence,
  rawLabel,
  reviewed,
  snippet,
  sourcePage,
  highlight,
  onReview,
  onViewSource,
  destination,
  dominantTab,
  onGoTo,
}: {
  label: string;
  value: string;
  confidence: number;
  rawLabel?: string;
  reviewed?: string | null;
  // FON-24: true when a validation finding deep-linked to this field.
  highlight?: boolean;
  // FON-23: present only for live low-confidence rows. Runs the
  // accept/edit/reject action and resolves once the refetch lands.
  onReview?: (action: 'accept' | 'edit' | 'reject', value?: string) => Promise<void>;
  // FON-23 — opens the source document at this field's page for validation.
  onViewSource?: () => void;
  // FON-23 — source snippet + page shown inline on needs-review rows so the
  // analyst can validate against the document without opening the pane.
  snippet?: string | null;
  sourcePage?: number | null;
  // Where this field's data is seeded/used — deep-link to that screen.
  destination?: { tab: string; label: string } | null;
  // Only show this field's "→ Screen" chip when its destination differs from
  // the document's dominant one (avoids an identical chip on every P&L row).
  dominantTab?: string | null;
  onGoTo?: (tab: string, focus?: string) => void;
}) {
  const reviewable = !!onReview && confidence < 85 && !reviewed;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  const [busy, setBusy] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);
  // FON-24: scroll the deep-linked field into view when a finding links here.
  useEffect(() => {
    if (highlight && rowRef.current) {
      rowRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [highlight]);

  const act = async (action: 'accept' | 'edit' | 'reject', v?: string) => {
    if (!onReview) return;
    setBusy(true);
    try {
      await onReview(action, v);
    } finally {
      setBusy(false);
      setEditing(false);
    }
  };

  return (
    <div
      ref={rowRef}
      className={cn(
        'py-2.5 border-b border-border last:border-0 transition-colors',
        highlight && 'bg-brand-50 ring-2 ring-brand-500/40 rounded-md -mx-1.5 px-1.5',
      )}
    >
      {/* Line 1 — label + value only, so neither gets crowded. */}
      <div className="flex items-start justify-between gap-3">
        <span className="text-[12.5px] font-medium text-ink-800 leading-snug" title={rawLabel && rawLabel !== label ? rawLabel : undefined}>
          {label}
        </span>
        <div className="flex items-center gap-1.5 shrink-0">
          {editing ? (
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="w-28 text-right font-semibold tabular-nums text-ink-900 border border-brand-500/40 rounded px-1.5 py-0.5 text-[12.5px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              aria-label={`Corrected value for ${label}`}
            />
          ) : (
            <span className="font-semibold tabular-nums text-ink-900 text-[14px] whitespace-nowrap">{value}</span>
          )}
          {onViewSource && !editing && (
            <button
              type="button"
              onClick={onViewSource}
              title="View in source document"
              aria-label={`View ${label} in source document`}
              className="text-ink-400 hover:text-brand-600 transition-colors p-0.5 -m-0.5 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              <FileText size={12} aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
      {/* Line 2 — meta: where it's used, confidence, review status. */}
      <div className="flex items-center gap-2 flex-wrap mt-1.5">
        {destination && onGoTo && destination.tab !== dominantTab && (
          <button
            type="button"
            onClick={() => onGoTo(destination.tab, rawLabel)}
            title={`See where this is used — ${destination.label}`}
            className="text-[10px] px-2 py-0.5 rounded-full border border-border text-brand-700 hover:border-brand-500 hover:text-brand-500 transition-colors inline-flex items-center gap-0.5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            {destination.label} <ArrowRight size={9} aria-hidden="true" />
          </button>
        )}
        {reviewed ? (
          <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-success-50 text-success-700 border border-success-500/25">
            {reviewed === 'edited' ? '✓ Edited' : '✓ Verified'}
          </span>
        ) : confidence < 85 ? (
          <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-warn-50 text-warn-700 border border-warn-500/25 tabular-nums">
            Needs review · {confidence}%
          </span>
        ) : (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-ink-300/10 text-ink-500 tabular-nums">
            {confidence}%
          </span>
        )}
      </div>
      {/* FON-23: inline accept/edit/reject for Needs-Review fields. */}
      {reviewable && snippet && !editing && (
        <div className="mt-1.5 text-[10.5px] text-ink-500 bg-ink-300/10 rounded px-2 py-1 leading-snug">
          {sourcePage != null && (
            <span className="font-mono text-ink-700">p.{sourcePage} · </span>
          )}
          <span className="italic">
            &ldquo;{(() => { const s = formatSnippet(snippet); return s.length > 140 ? `${s.slice(0, 140)}…` : s; })()}&rdquo;
          </span>
        </div>
      )}
      {reviewable && (
        <div className="flex items-center gap-1 mt-1.5 justify-end">
          {editing ? (
            <>
              <button
                type="button"
                disabled={busy}
                onClick={() => act('edit', draft)}
                className="text-[10.5px] font-medium px-2.5 py-1 rounded-md bg-brand-600 text-white hover:bg-brand-700 disabled:opacity-50"
              >
                Save
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => { setEditing(false); setDraft(value); }}
                className="text-[10.5px] font-medium px-2 py-1 rounded-md text-ink-600 hover:bg-ink-100 disabled:opacity-50"
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              {/* Accept is the primary action → soft filled. Edit / Reject are
                  quiet ghosts so the value stays the hero, not the buttons. */}
              <button
                type="button"
                disabled={busy}
                onClick={() => act('accept')}
                className="text-[10.5px] font-medium px-2.5 py-1 rounded-md bg-success-50 text-success-700 border border-success-500/25 hover:bg-success-100 disabled:opacity-50"
              >
                Accept
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => { setDraft(value); setEditing(true); }}
                className="text-[10.5px] font-medium px-2 py-1 rounded-md text-ink-600 hover:bg-ink-100 disabled:opacity-50"
              >
                Edit
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => act('reject')}
                className="text-[10.5px] font-medium px-2 py-1 rounded-md text-danger-700 hover:bg-danger-50 disabled:opacity-50"
              >
                Reject
              </button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// Browse Templates popover — anchors to the trigger via absolute positioning.
// Backdrop catches outside clicks; the parent owns the open/close state so
// the same component can render under both Browse Templates buttons.
function TemplatesPopover({
  onApply,
  onClose,
}: {
  onApply: (name: string) => void;
  onClose: () => void;
}) {
  return (
    <>
      <div
        className="fixed inset-0 z-30"
        onClick={onClose}
        aria-hidden="true"
      />
      <div
        role="menu"
        aria-label="Templates"
        className="absolute right-0 top-full mt-1.5 z-40 w-72 rounded-md border border-border bg-white shadow-card-hover py-1.5"
      >
        <div className="px-3 py-1.5 text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold">
          Templates
        </div>
        {templates.map((t) => (
          <button
            key={t.name}
            type="button"
            role="menuitem"
            onClick={() => onApply(t.name)}
            className="w-full text-left px-3 py-2 hover:bg-ink-100 focus-visible:outline-none focus-visible:bg-ink-100"
          >
            <div className="text-[12.5px] font-medium text-ink-900">{t.name}</div>
            <div className="text-[11px] text-ink-500 mt-0.5 leading-snug">{t.description}</div>
          </button>
        ))}
      </div>
    </>
  );
}
