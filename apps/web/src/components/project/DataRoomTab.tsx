'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import {
  UploadCloud, FileText, FileSpreadsheet,
  CheckCircle2, Loader2, Circle, AlertTriangle, ArrowRight,
  ClipboardList, Sparkles, Wallet, Receipt, Banknote, TrendingUp, Coins, Users2,
  Search, X as CloseIcon, Star, Filter,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge, StatusBadge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import { engines, templates } from '@/lib/mockData';
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
import { ProvenanceDot, SectionCard, palette, radius } from '@/components/design';
import { CoachMark } from '@/components/help/CoachMark';
import { UsaliBadge } from './validation/UsaliBadge';
import { UsaliDeviationsAccordion } from './validation/UsaliDeviationsAccordion';
import { GapChipsStrip } from './validation/GapChipsStrip';
import { MisclassificationBanner } from './wizard/MisclassificationBanner';
import { YearMismatchBanner } from './wizard/YearMismatchBanner';
import { DocumentCoverage, type CoverageFile } from './DocumentCoverage';
import { isReviewableFinancialField } from './pl/GroundedWorksheet';

// FON-41 — doc types whose data lands in the Financials historical view. Their
// "to review" count is reconciled to what that view surfaces.
const FINANCIAL_DOC_TYPES = new Set([
  'T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD', 'PNL_BENCHMARK',
]);

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
    return [];
  }, [liveMode, documents, extractions]);

  const reviewDocRow = useMemo(
    () => docs.find((d) => d.id === reviewDocId) ?? null,
    [docs, reviewDocId],
  );

  // Coverage vs. inline detail-review sub-view (canonical Data Room v2
  // `isDetailView`). Selecting a non-financial document (reviewDocId) swaps the
  // coverage list for the inline field-review table; clearing it returns to
  // coverage. Financial statements still deep-link to the Financials tab.
  const view: 'coverage' | 'detail' = reviewDocRow ? 'detail' : 'coverage';

  // Build the required-doc checklist by intersecting our canonical 10-item
  // list against the live `documents` array's doc_type values. An item
  // flips to "complete" the moment any uploaded doc carries one of its
  // mapped tokens.
  const uploadedDocTypes = useMemo(() => {
    if (liveMode) {
      return new Set(
        documents
          .map((d) => (d.doc_type ?? '').toUpperCase().trim())
          .filter(Boolean),
      );
    }
    return new Set<string>();
  }, [liveMode, documents]);

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

      {/* Intro card — Data Room title + one-line purpose. Stays visible above
          both the coverage list and the inline field-review (canonical Data
          Room v2 keeps this and the Data Key strip across both sub-views). */}
      <div
        style={{
          background: palette.cardWhite,
          border: `1px solid ${palette.border}`,
          borderRadius: radius.card,
          padding: '12px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 3,
        }}
      >
        <span style={{ fontSize: 13.5, fontWeight: 700, color: palette.ink }}>Data Room</span>
        <span
          style={{ fontSize: 12.5, color: palette.textSecondary, lineHeight: 1.55, maxWidth: 960 }}
        >
          Every diligence document for this deal, what Fondok extracted from each one, and what is
          still missing. Confirm classifications here, then validate any statement’s extracted data
          in Financials.
        </span>
      </div>

      {view === 'coverage' && (
        <>
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
      {fullRunRunning && (
        <div className="flex items-center justify-between gap-3 -mb-1">
          <span />
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
              // FON-41 — on financial statements, count only low-confidence
              // fields the analyst can actually review in the Financials
              // historical view, so this badge reconciles with what that view
              // surfaces. Non-financial docs keep their full low-confidence
              // count (reviewed via the document-detail panel).
              toReview: (d.fieldList ?? []).filter((f) => {
                if (Math.round((f.confidence ?? 0) * 100) >= 85 || f.reviewed) {
                  return false;
                }
                return FINANCIAL_DOC_TYPES.has(d.type)
                  ? isReviewableFinancialField(f.field_name ?? '')
                  : true;
              }).length,
              fiscalYear: d.fiscalYear ?? null,
              status: d.rawStatus,
            }),
          )}
          onReclassify={handleReclassify}
          onOpenDoc={(docId, financial) => {
            // Sam QA 8/21: Financial Statements' "View data" jumps straight to
            // the Financials tab (where their data lands). Other documents open
            // the inline field-review in place (canonical Data Room v2
            // `isDetailView`) — the /documents/[docId] route still works if a
            // deep-link lands on it directly, we just no longer hop to it.
            if (financial) {
              router.push(`/projects/${rawId}?tab=pl&fin=historicals`, { scroll: false });
            } else {
              setReviewDocId(docId);
            }
          }}
          onOpenInNewTab={(docId) => {
            const u = api.documents.downloadUrl(rawId, docId);
            if (u) window.open(u, '_blank', 'noopener,noreferrer');
          }}
          onDownload={(docId) => {
            const u = api.documents.downloadUrl(rawId, docId);
            if (!u) return;
            const a = document.createElement('a');
            a.href = u;
            a.rel = 'noopener';
            a.click();
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

        </>
      )}

      {view === 'detail' && reviewDocRow && (
        <InlineDocumentReview
          doc={reviewDocRow}
          liveMode={liveMode}
          highlightField={highlightField}
          onBack={() => { setReviewDocId(null); setHighlightField(null); }}
          onReview={handleReviewField}
        />
      )}
    </div>
  );
}

// ── Inline document review — canonical Data Room v2 `isDetailView` ─────────
// Replaces the old right-side field drawer with the full-width, section-grouped
// field table the design specifies: filter pills (All / Needs Review /
// Reviewed), a Confidence + Section facet dropdown, a per-field plain-language
// "why flagged" reason, colored confidence pills, and inline Accept / Edit with
// ✓ Verified / ✎ Edited markers. Extraction values, the accept/edit action and
// the refetch are the SAME production wiring the drawer used
// (api.documents.reviewField via `onReview`, refetched upstream).

// Section = the schema prefix before the first dot, mapped to a business label.
const SECTION_LABELS: Record<string, string> = {
  property_overview: 'Property Overview',
  ttm_summary_per_om: 'TTM Summary',
  ttm_performance: 'TTM Performance',
  p_and_l_usali: 'P&L (USALI)',
  fb_operations: 'F&B Operations',
  parking_operations: 'Parking Operations',
  in_place_debt: 'In-Place Debt',
  transaction_comps: 'Transaction Comps',
  comparable_sales: 'Comparable Sales',
  market_overview: 'Market Overview',
  broker_proforma: 'Broker Pro Forma',
  asking_price: 'Pricing',
  capex: 'Capital Plan',
  capital_plan: 'Capital Plan',
};

function sectionLabelFor(fieldName: string): string {
  if (!fieldName) return 'Extracted Fields';
  const top = fieldName.includes('.') ? fieldName.split('.')[0].toLowerCase() : '';
  if (!top) return 'Extracted Fields';
  return SECTION_LABELS[top] ?? humanizeFieldName(top);
}

// The three confidence bands the design tints (green ≥90, amber ≥80, red <80).
function confStyle(conf: number): { color: string; bg: string } {
  if (conf >= 90) return { color: 'oklch(45% 0.12 155)', bg: 'oklch(56% 0.12 155 / .12)' };
  if (conf >= 80) return { color: 'oklch(50% 0.12 55)', bg: 'oklch(56% 0.1 55 / .12)' };
  return { color: 'oklch(50% 0.14 40)', bg: 'oklch(56% 0.12 40 / .12)' };
}

// Plain-language "why is this flagged" line. Production extraction carries no
// free-text reason, so we synthesize one from the signal we do have — the
// confidence band + the source page the analyst can verify against.
function reviewReasonFor(conf: number, sourcePage: number | null | undefined): string {
  const where = sourcePage != null && sourcePage > 0 ? ` (source p.${sourcePage})` : '';
  if (conf < 70) {
    return `Low extraction confidence (${conf}%) — the source is ambiguous; verify against the document${where}.`;
  }
  return `Below the review threshold (${conf}%) — confirm this value against the source document${where}.`;
}

// Needs-review gate — the 85% threshold used across the app, so the per-doc
// "N to review" counts reconcile with the coverage card.
const REVIEW_THRESHOLD = 85;

type ReviewField = {
  key: string;
  fieldName: string;
  label: string;
  value: string;
  conf: number;
  reviewed: string | null;
  resolved: boolean;
  needsReview: boolean;
  sourcePage: number | null;
};

function InlineDocumentReview({
  doc,
  liveMode,
  highlightField,
  onBack,
  onReview,
}: {
  doc: { id: string; name: string; type: string; confidence: number; fieldList?: ExtractionField[] };
  liveMode: boolean;
  highlightField?: string | null;
  onBack: () => void;
  onReview: (
    docId: string,
    fieldName: string,
    action: 'accept' | 'edit' | 'reject',
    value?: string,
  ) => Promise<void>;
}) {
  const [pill, setPill] = useState<'all' | 'review' | 'reviewed'>('all');
  const [q, setQ] = useState('');
  const [confFacet, setConfFacet] = useState<'all' | 'lt90' | 'mid' | 'high'>('all');
  const [sectionFacet, setSectionFacet] = useState<string>('all');
  const [filterOpen, setFilterOpen] = useState(false);

  const fields = doc.fieldList ?? [];

  // Group extracted fields under section labels, in first-appearance (schema)
  // order so the table reads like the document.
  const sections = useMemo(() => {
    const order: string[] = [];
    const map = new Map<string, ReviewField[]>();
    for (const f of fields) {
      const label = sectionLabelFor(f.field_name);
      if (!map.has(label)) {
        map.set(label, []);
        order.push(label);
      }
      const conf = Math.round((f.confidence ?? 0) * 100);
      const reviewed = f.reviewed ?? null;
      const resolved = reviewed === 'verified' || reviewed === 'edited' || reviewed === 'accepted';
      map.get(label)!.push({
        key: f.field_name,
        fieldName: f.field_name,
        label: humanizeFieldName(f.field_name),
        value: formatValue(f.value, f.unit, f.field_name),
        conf,
        reviewed,
        resolved,
        needsReview: !resolved && conf < REVIEW_THRESHOLD,
        sourcePage: f.source_page ?? null,
      });
    }
    return order.map((label) => ({ label, fields: map.get(label)! }));
  }, [fields]);

  const sectionNames = useMemo(() => sections.map((s) => s.label), [sections]);
  const fieldTotal = fields.length;
  const reviewTotal = useMemo(
    () => sections.reduce((a, s) => a + s.fields.filter((f) => f.needsReview).length, 0),
    [sections],
  );

  const query = q.trim().toLowerCase();
  const matchesConf = (conf: number) =>
    confFacet === 'all' ||
    (confFacet === 'lt90'
      ? conf < 90
      : confFacet === 'mid'
        ? conf >= 90 && conf < 95
        : conf >= 95);

  const visibleSections = useMemo(
    () =>
      sections
        .map((sec) => ({
          label: sec.label,
          fields: sec.fields.filter((f) => {
            const matchesSearch =
              !query ||
              [f.label, f.value, sec.label].some((t) => String(t).toLowerCase().includes(query));
            const matchesPill =
              pill === 'all' || (pill === 'review' ? f.needsReview : !f.needsReview);
            const matchesSection = sectionFacet === 'all' || sectionFacet === sec.label;
            return matchesSearch && matchesPill && matchesConf(f.conf) && matchesSection;
          }),
        }))
        .filter((sec) => sec.fields.length > 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sections, query, pill, confFacet, sectionFacet],
  );

  const shownTotal = visibleSections.reduce((a, s) => a + s.fields.length, 0);
  const hasFilters = pill !== 'all' || !!query || confFacet !== 'all' || sectionFacet !== 'all';
  const clearFilters = () => {
    setPill('all');
    setQ('');
    setConfFacet('all');
    setSectionFacet('all');
    setFilterOpen(false);
  };

  const pillDefs = [
    { id: 'all' as const, label: 'All', n: fieldTotal, dot: false },
    { id: 'review' as const, label: 'Needs Review', n: reviewTotal, dot: true },
    { id: 'reviewed' as const, label: 'Reviewed', n: fieldTotal - reviewTotal, dot: false },
  ];
  const confFacetOptions = [
    { id: 'all' as const, label: 'All confidence' },
    { id: 'lt90' as const, label: 'Below 90%' },
    { id: 'mid' as const, label: '90–95%' },
    { id: 'high' as const, label: '95% and above' },
  ];
  const resultLabel =
    'Showing ' +
    shownTotal +
    ' of ' +
    (pill === 'review'
      ? reviewTotal + ' review items'
      : pill === 'reviewed'
        ? fieldTotal - reviewTotal + ' reviewed fields'
        : fieldTotal + ' fields');

  const gridCols = '1fr 200px 110px 150px';
  const headCell = (extra?: CSSProperties): CSSProperties => ({
    padding: '9px 16px',
    fontSize: 11,
    fontWeight: 600,
    color: palette.gridHeaderText,
    textAlign: 'right',
    ...extra,
  });

  return (
    <div>
      {/* Back + document name */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 6 }}>
        <button
          type="button"
          onClick={onBack}
          style={{
            background: '#fff',
            border: `1px solid ${palette.disabledBorder}`,
            color: palette.hoverInk,
            borderRadius: 6,
            padding: '6px 12px',
            fontSize: 12,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          ← Back
        </button>
        <div style={{ fontSize: 15, fontWeight: 700, color: palette.ink }} title={doc.name}>
          {doc.name}
        </div>
      </div>

      {/* Fields extracted · confidence · needs-review */}
      <div style={{ fontSize: 12.5, color: palette.eyebrow, margin: '2px 0 14px' }}>
        {fieldTotal} fields extracted ·{' '}
        <span style={{ color: confStyle(doc.confidence).color, fontWeight: 600 }}>
          {doc.confidence}% confidence
        </span>
        {' · '}
        <span
          role="button"
          tabIndex={0}
          onClick={() => setPill('review')}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') setPill('review');
          }}
          style={{
            color: reviewTotal ? 'oklch(50% 0.14 40)' : palette.eyebrow,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          {reviewTotal} need review
        </span>
      </div>

      {/* Filter pills + search + facet dropdown */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          flexWrap: 'wrap',
          marginBottom: 10,
          position: 'relative',
        }}
      >
        {pillDefs.map((p) => {
          const active = pill === p.id;
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => setPill(p.id)}
              aria-pressed={active}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 7,
                fontFamily: 'inherit',
                fontSize: 12,
                fontWeight: 600,
                padding: '6px 12px',
                borderRadius: 6,
                border: active ? `1px solid ${palette.inkNavy}` : `1px solid ${palette.disabledBorder}`,
                background: active ? palette.inkNavy : '#fff',
                color: active ? '#fff' : palette.hoverInk,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              {p.dot && (
                <span
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: '50%',
                    background: 'oklch(50% 0.14 40)',
                    display: 'inline-block',
                    flexShrink: 0,
                  }}
                />
              )}
              {p.label}
              <span style={{ color: active ? '#9fb2df' : palette.textMuted, fontWeight: 600 }}>
                {p.n}
              </span>
            </button>
          );
        })}
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 7,
              border: `1px solid ${palette.disabledBorder}`,
              background: '#fff',
              borderRadius: 6,
              padding: '6px 10px',
              width: 300,
              maxWidth: '60vw',
            }}
          >
            <Search size={13} style={{ flexShrink: 0, color: palette.textMuted }} aria-hidden="true" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search fields or values…"
              aria-label="Search extracted fields"
              style={{
                border: 'none',
                outline: 'none',
                fontFamily: 'inherit',
                fontSize: 12.5,
                color: palette.ink,
                width: '100%',
                background: 'transparent',
              }}
            />
          </span>
          <span style={{ position: 'relative', display: 'inline-flex' }}>
            <button
              type="button"
              onClick={() => setFilterOpen((o) => !o)}
              title="Filter"
              aria-haspopup="menu"
              aria-expanded={filterOpen}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                fontFamily: 'inherit',
                fontSize: 12,
                fontWeight: 600,
                color: palette.hoverInk,
                background: confFacet !== 'all' || sectionFacet !== 'all' ? '#eef2fb' : '#fff',
                border: `1px solid ${palette.disabledBorder}`,
                borderRadius: 6,
                padding: '6px 11px',
                cursor: 'pointer',
              }}
            >
              <Filter size={13} aria-hidden="true" /> Filter
            </button>
            {filterOpen && (
              <div
                role="menu"
                style={{
                  position: 'absolute',
                  top: 'calc(100% + 6px)',
                  right: 0,
                  zIndex: 30,
                  background: '#fff',
                  border: `1px solid ${palette.disabledBorder}`,
                  borderRadius: 8,
                  boxShadow: '0 10px 26px rgba(0,0,0,.12)',
                  padding: 10,
                  width: 240,
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 8,
                }}
              >
                <div
                  style={{
                    fontSize: 9.5,
                    fontWeight: 700,
                    letterSpacing: '.07em',
                    color: palette.textFaint,
                    textTransform: 'uppercase',
                  }}
                >
                  Confidence
                </div>
                {confFacetOptions.map((o) => (
                  <div
                    key={o.id}
                    role="menuitemradio"
                    aria-checked={confFacet === o.id}
                    onClick={() => setConfFacet(o.id)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      fontSize: 12.5,
                      color: palette.ink,
                      fontWeight: confFacet === o.id ? 600 : 400,
                      padding: '5px 6px',
                      borderRadius: 5,
                      cursor: 'pointer',
                    }}
                  >
                    <span style={{ width: 11 }}>{confFacet === o.id ? '✓' : ''}</span>
                    {o.label}
                  </div>
                ))}
                <div
                  style={{
                    fontSize: 9.5,
                    fontWeight: 700,
                    letterSpacing: '.07em',
                    color: palette.textFaint,
                    textTransform: 'uppercase',
                    borderTop: `1px solid ${palette.hairlineSection}`,
                    paddingTop: 8,
                  }}
                >
                  Section
                </div>
                {['all', ...sectionNames].map((name) => (
                  <div
                    key={name}
                    role="menuitemradio"
                    aria-checked={sectionFacet === name}
                    onClick={() => setSectionFacet(name)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      fontSize: 12.5,
                      color: palette.ink,
                      fontWeight: sectionFacet === name ? 600 : 400,
                      padding: '5px 6px',
                      borderRadius: 5,
                      cursor: 'pointer',
                    }}
                  >
                    <span style={{ width: 11 }}>{sectionFacet === name ? '✓' : ''}</span>
                    {name === 'all' ? 'All sections' : name}
                  </div>
                ))}
              </div>
            )}
          </span>
        </span>
      </div>

      {/* Result count + clear */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginBottom: 10,
          fontSize: 11.5,
          color: palette.textMuted,
        }}
      >
        <span>{resultLabel}</span>
        {hasFilters && (
          <span
            role="button"
            tabIndex={0}
            onClick={clearFilters}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') clearFilters();
            }}
            style={{ color: palette.linkBlue, fontWeight: 600, cursor: 'pointer' }}
          >
            Clear filters
          </span>
        )}
      </div>

      {/* Section-grouped field table (navy header, per-section subheads) */}
      <SectionCard variant="title" style={{ boxShadow: '0 1px 2px rgba(0,0,0,.02)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: gridCols, background: palette.inkNavy }}>
          <div style={headCell({ textAlign: 'left', padding: '9px 22px' })}>FIELD</div>
          <div style={headCell()}>EXTRACTED VALUE</div>
          <div style={headCell()}>CONFIDENCE</div>
          <div style={headCell()} />
        </div>
        {!liveMode ? (
          <div
            style={{
              padding: '26px 22px',
              fontSize: 12.5,
              color: palette.textMuted,
              textAlign: 'center',
            }}
          >
            Field review is available on live deals.
          </div>
        ) : shownTotal === 0 ? (
          <div
            style={{
              padding: '26px 22px',
              fontSize: 12.5,
              color: palette.textMuted,
              textAlign: 'center',
            }}
          >
            No fields match these filters.
          </div>
        ) : (
          visibleSections.map((sec) => (
            <div key={sec.label}>
              <div
                style={{
                  padding: '9px 22px',
                  background: '#f0f0ee',
                  borderBottom: '1px solid #d8d7d1',
                  fontSize: 11,
                  fontWeight: 700,
                  color: palette.textSecondary,
                  textTransform: 'uppercase',
                  letterSpacing: '.03em',
                }}
              >
                {sec.label}
              </div>
              {sec.fields.map((f) => (
                <ReviewFieldRow
                  key={f.key}
                  field={f}
                  gridCols={gridCols}
                  highlight={highlightField != null && f.fieldName === highlightField}
                  onReview={(action, value) => onReview(doc.id, f.fieldName, action, value)}
                />
              ))}
            </div>
          ))
        )}
      </SectionCard>
    </div>
  );
}

// A single field row in the inline review table — owns its edit draft + busy
// state, renders the value / confidence pill / action cell, and scrolls into
// view when a validation finding deep-links to it (FON-24).
function ReviewFieldRow({
  field,
  gridCols,
  highlight,
  onReview,
}: {
  field: ReviewField;
  gridCols: string;
  highlight?: boolean;
  onReview: (action: 'accept' | 'edit' | 'reject', value?: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(field.value);
  const [busy, setBusy] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (highlight && rowRef.current) {
      rowRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, [highlight]);

  const c = confStyle(field.conf);
  const flagged = field.needsReview && !field.resolved;

  const act = async (action: 'accept' | 'edit' | 'reject', v?: string) => {
    setBusy(true);
    try {
      await onReview(action, v);
    } finally {
      setBusy(false);
      setEditing(false);
    }
  };

  const rowBg = highlight ? '#f4f6fb' : flagged ? 'oklch(56% 0.12 40 / .06)' : '#fff';
  const btnGhost: CSSProperties = {
    background: '#fff',
    border: `1px solid ${palette.disabledBorder}`,
    color: palette.hoverInk,
    borderRadius: 6,
    padding: '5px 10px',
    fontSize: 11.5,
    fontWeight: 600,
    cursor: 'pointer',
    whiteSpace: 'nowrap',
  };

  return (
    <div
      ref={rowRef}
      style={{
        display: 'grid',
        gridTemplateColumns: gridCols,
        borderBottom: `1px solid ${palette.hairlineSection}`,
        background: rowBg,
        alignItems: 'center',
        boxShadow: highlight ? 'inset 2px 0 0 #1a4fa0' : undefined,
      }}
    >
      <div
        style={{
          padding: '11px 22px',
          fontSize: 13,
          color: palette.ink,
          display: 'flex',
          flexDirection: 'column',
          gap: 3,
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
          <ProvenanceDot state="document_sourced" review={flagged} size={8} />
          {field.label}
        </span>
        {flagged && (
          <span style={{ fontSize: 11, color: 'oklch(50% 0.14 40)', lineHeight: 1.45 }}>
            {reviewReasonFor(field.conf, field.sourcePage)}
          </span>
        )}
      </div>

      {editing ? (
        <div style={{ padding: '6px 16px', textAlign: 'right' }}>
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            aria-label={`Corrected value for ${field.label}`}
            style={{
              width: '100%',
              fontSize: 13,
              textAlign: 'right',
              border: `1px solid ${palette.disabledBorder}`,
              borderRadius: 6,
              padding: '5px 8px',
              fontVariantNumeric: 'tabular-nums',
            }}
          />
        </div>
      ) : (
        <div
          style={{
            padding: '11px 16px',
            fontSize: 13,
            color: palette.ink,
            textAlign: 'right',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {field.value}
        </div>
      )}

      <div style={{ padding: '11px 16px', textAlign: 'right' }}>
        <span
          style={{
            fontSize: 11,
            fontWeight: 600,
            color: c.color,
            background: c.bg,
            padding: '3px 9px',
            borderRadius: 14,
          }}
        >
          {field.conf}%
        </span>
      </div>

      <div
        style={{
          padding: '11px 16px',
          textAlign: 'right',
          display: 'flex',
          gap: 6,
          justifyContent: 'flex-end',
          alignItems: 'center',
        }}
      >
        {editing ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => act('edit', draft)}
              style={{
                background: palette.inkNavy,
                color: '#fff',
                border: 'none',
                borderRadius: 6,
                padding: '5px 10px',
                fontSize: 11.5,
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Save
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setEditing(false);
                setDraft(field.value);
              }}
              style={btnGhost}
            >
              Cancel
            </button>
          </>
        ) : field.resolved ? (
          field.reviewed === 'edited' ? (
            <span style={{ fontSize: 11, color: 'oklch(45% 0.1 260)' }}>✎ Edited</span>
          ) : (
            <span style={{ fontSize: 11, color: 'oklch(45% 0.12 155)' }}>✓ Verified</span>
          )
        ) : flagged ? (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => act('accept')}
              title="Accept"
              style={{
                background: 'oklch(56% 0.12 155)',
                color: '#fff',
                border: 'none',
                borderRadius: 6,
                padding: '5px 10px',
                fontSize: 11.5,
                fontWeight: 600,
                cursor: 'pointer',
                whiteSpace: 'nowrap',
              }}
            >
              Accept
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setDraft(field.value);
                setEditing(true);
              }}
              title="Edit"
              style={btnGhost}
            >
              Edit ✎
            </button>
          </>
        ) : (
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              setDraft(field.value);
              setEditing(true);
            }}
            title="Edit"
            style={btnGhost}
          >
            Edit ✎
          </button>
        )}
      </div>
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
