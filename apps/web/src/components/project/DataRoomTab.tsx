'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  UploadCloud, FolderOpen, Info, FileText, FileSpreadsheet,
  CheckCircle2, Loader2, Circle, AlertTriangle, ArrowRight, Play,
  ClipboardList, Sparkles, Wallet, Receipt, Banknote, TrendingUp, Coins, Users2,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge, StatusBadge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import { ConfidenceBadge } from '@/components/ui/ConfidenceBadge';
import { engines, kimptonDocuments, templates } from '@/lib/mockData';
import { criticalCount, warnCount, varianceFlags } from '@/lib/varianceData';
import {
  isWorkerConnected,
  workerUrl,
  WorkerDocument,
  ExtractionField,
  EngineName,
  EngineOutputResponse,
} from '@/lib/api';
import { useDocuments } from '@/lib/hooks/useDocuments';
import { useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/format';
import EngineRunProgress from './EngineRunProgress';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';

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

// Friendly labels for the doc-type breakdown shown in the checklist
// header. Anything not in this map gets a Title-Cased fallback.
const DOC_TYPE_LABEL: Record<string, string> = {
  OM: 'OM',
  T12: 'T-12',
  PNL: 'P&L',
  PNL_MONTHLY: 'Monthly P&L',
  PNL_YTD: 'YTD P&L',
  STR: 'STR',
  STR_TREND: 'STR',
  BUDGET: 'Budget',
  DEBT: 'Debt',
  INSURANCE: 'Insurance',
  PROPERTY_TAXES: 'Prop. Taxes',
  CONTRACT: 'Contract',
};

// Documents with broker-vs-T12 variance flags raised against them.
const VARIANCE_DOCS = new Set([
  'Offering_Memorandum_Final.pdf',
  'T12_FinancialStatement.xlsx',
]);

// Canonical 10-item required-document checklist surfaced in the Data Room.
// Each row maps to zero or more upstream `doc_type` tokens — when a live
// uploaded document carries a matching doc_type the row flips green and
// drops its REQ badge. Items with an empty `match` set have no extractor
// today and stay REQ until the worker learns them.
const REQUIRED_CHECKLIST: { label: string; match: string[] }[] = [
  { label: 'Offering Memorandum',           match: ['OM'] },
  { label: 'T-12 / Trailing Twelve Months', match: ['T12'] },
  { label: 'Annual / YTD / Monthly P&L',    match: ['PNL', 'PNL_MONTHLY', 'PNL_YTD'] },
  { label: 'STR / Comp Set Report',         match: ['STR', 'STR_TREND'] },
  { label: 'Insurance Records',             match: ['INSURANCE'] },
  { label: 'Property Taxes',                match: ['PROPERTY_TAXES'] },
  { label: 'Room Mix / Unit Mix',           match: [] },
  { label: 'Historical CapEx',              match: [] },
  { label: 'Basic Property Info',           match: [] },
  { label: 'Leases & Agreements',           match: ['CONTRACT'] },
  { label: 'Surveys & Reviews',             match: [] },
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

// Map a 0-100 confidence percent to the agreed three-tier rendering:
//   ≥95 → green / "High"
//   ≥85 → amber / "Medium"
//   <85 → red   / "Needs review"
// Kept colocated with DataRoomTab because it's purely a display-layer
// concern and matches the thresholds already baked into ConfidenceBadge.
function confidenceTier(pct: number): { tone: 'green' | 'amber' | 'red'; label: string } {
  if (pct >= 95) return { tone: 'green', label: 'High' };
  if (pct >= 85) return { tone: 'amber', label: 'Medium' };
  return { tone: 'red', label: 'Needs review' };
}

function formatValue(v: unknown, unit: string | null): string {
  if (v == null) return '—';
  if (typeof v === 'number') {
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
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  // Per-doc "Needs Review" filter — when true the right panel shows only
  // fields with <85% confidence. Reset whenever the user switches docs.
  const [needsReviewOnly, setNeedsReviewOnly] = useState(false);
  // Browse Templates popover — anchored to whichever button the user clicked.
  const [templatesAnchor, setTemplatesAnchor] = useState<'empty' | 'inline' | null>(null);
  const { toast } = useToast();

  useEffect(() => {
    setNeedsReviewOnly(false);
  }, [selectedDoc]);

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

  const { documents, uploading, upload, extractions, error: docsError, refresh } =
    useDocuments(rawId);

  // Surface a toast each time a doc transitions to EXTRACTED.
  useEffect(() => {
    documents.forEach((d) => {
      if (extractionToastedRef.current.has(d.id)) return;
      const ex = extractions[d.id];
      if (ex && ex.status === 'EXTRACTED') {
        extractionToastedRef.current.add(d.id);
        const fieldCount = ex.fields?.length ?? 0;
        toast(`Extracted ${fieldCount} field${fieldCount === 1 ? '' : 's'} from ${d.filename}`, {
          type: 'success',
        });
      } else if (d.status === 'FAILED') {
        if (!extractionToastedRef.current.has(d.id)) {
          extractionToastedRef.current.add(d.id);
          toast(`Extraction failed for ${d.filename}`, { type: 'error' });
        }
      }
    });
  }, [documents, extractions, toast]);

  // When we're on a real (UUID) deal, use live documents; otherwise mock.
  const liveMode = isWorkerConnected() && !isMockId;

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

  const selectedDocRow = useMemo(
    () => docs.find((d) => d.name === selectedDoc) ?? null,
    [docs, selectedDoc],
  );
  const selectedHasVariance = selectedDoc !== null && VARIANCE_DOCS.has(selectedDoc);
  const selectedVarianceFlags = selectedHasVariance
    ? varianceFlags.filter((f) =>
        f.source_documents.some(
          (s) =>
            (selectedDoc === 'Offering_Memorandum_Final.pdf' && s.document_id === 'kimpton-angler-om-2026') ||
            (selectedDoc === 'T12_FinancialStatement.xlsx' && s.document_id === 'kimpton-angler-t12-2026q1'),
        ),
      )
    : [];
  const selectedCriticalCount = selectedVarianceFlags.filter((f) => f.severity === 'CRITICAL').length;

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
  const extracted = docs.filter((d) => d.status === 'Extracted').length;
  const processing = docs.filter((d) => d.status === 'Processing').length;

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
  const fullRun = useEngineRun(liveMode ? rawId : '', 'returns', {
    runMode: 'all',
    onRunAllStarted: (id, eng) => {
      setFullRunId(id);
      setFullRunRows([]);
      setFullRunStartedAt(Date.now());
      setFullRunExpected(eng.length > 0 ? eng : ENGINE_ORDER);
      setFullRunNumber((n) => n + 1);
      toast(
        'Underwriting kicked off — switch to any engine tab to watch the results',
        { type: 'success' },
      );
    },
    onRunAllProgress: (rows) => {
      setFullRunRows(rows);
    },
    onAllComplete: (rows) => {
      setFullRunRows(rows);
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
    files.forEach((f) =>
      toast(`Uploading ${f.name}…`, { type: 'info', duration: 2500 }),
    );
    try {
      await upload(files);
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

      <IntroCard
        dismissKey="dataroom-intro"
        title="The Data Room"
        body={
          <>
            Drop your deal documents here. Our AI reads each PDF and Excel end-to-end and pulls
            out every number, every assumption, every risk — automatically. The
            <span className="font-semibold"> Document Checklist</span> tracks what
            we still need to fully underwrite the deal.
          </>
        }
      />

      <Card className="p-5">
        <div className="flex items-start gap-3">
          <FolderOpen size={20} className="text-brand-500 mt-0.5" />
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-[15px] font-semibold text-ink-900">Data Room</h2>
              <Info size={13} className="text-ink-400" />
            </div>
            <p className="text-[12.5px] text-ink-500 mt-1">
              Upload and manage deal documents for AI-powered extraction and underwriting automation.
              {' '}({extracted} of {checklist.length} extracted)
            </p>
          </div>
          {isFullDoc && criticalCount > 0 && (
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
          )}
          {/* Underwriting kicks off automatically once any document
              finishes extracting (debounced so a multi-doc upload only
              fires one run). Each engine tab still exposes a "Re-run"
              button in its header for manual refreshes. */}
          {fullRunRunning && (
            <span className="inline-flex items-center gap-2 text-[12px] text-ink-500">
              <span className="inline-block w-2 h-2 rounded-full bg-brand-500 animate-pulse" />
              Running underwriting…
            </span>
          )}
        </div>
      </Card>

      {/* Floating progress strip — appears bottom-right while the run-all
          chain is in flight, auto-dismisses on completion. */}
      <EngineRunProgress
        runId={fullRunId}
        expectedEngines={fullRunExpected}
        rows={fullRunRows}
        startedAt={fullRunStartedAt}
        runNumber={fullRunNumber}
        onClose={() => setFullRunId(null)}
      />

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
              Upload an OM, T-12, or rent roll to start the AI underwriting flow
            </div>
            <div className="text-[12px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              Drag &amp; drop or click anywhere in this box. <span className="font-medium">OM</span> = Offering Memorandum
              (the broker&apos;s pitch deck). <span className="font-medium">T-12</span> = the last 12 months of profit &amp;
              loss. PDF, Excel, CSV, Word — all welcome.
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

      <div className={cn('grid gap-5', SHOW_ENGINE_STATUS ? 'grid-cols-2' : 'grid-cols-1')}>
        {/* Document Checklist — required-doc list + actual upload count.
            The counter is the total number of uploaded documents (with a
            per-type breakdown line) rather than checklist-row coverage,
            so a 2nd P&L upload visibly moves the number. */}
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

      {docs.length > 0 && (
        <Card className="p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <h3 className="text-[14px] font-semibold text-ink-900">Documents ({docs.length})</h3>
              <Badge tone="green">{extracted} Extracted</Badge>
              {processing > 0 && <Badge tone="blue">{processing} Processing</Badge>}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-5">
            <div className="col-span-2 space-y-2">
              {docs.map((d) => {
                const hasVariance = VARIANCE_DOCS.has(d.name);
                const flagsForDoc = hasVariance
                  ? varianceFlags.filter((f) =>
                      f.source_documents.some(
                        (s) =>
                          (d.name === 'Offering_Memorandum_Final.pdf' && s.document_id === 'kimpton-angler-om-2026') ||
                          (d.name === 'T12_FinancialStatement.xlsx' && s.document_id === 'kimpton-angler-t12-2026q1'),
                      ),
                    )
                  : [];
                const docCritical = flagsForDoc.filter((f) => f.severity === 'CRITICAL').length;

                // Per-row kebab — Preview / Download / Delete. Delete & Preview
                // are stubs until the worker exposes the matching routes.
                const rowMenu = [
                  {
                    label: 'Preview',
                    onSelect: () => toast('Preview available on enterprise plans', { type: 'info' }),
                  },
                  {
                    label: 'Download',
                    onSelect: () => {
                      if (liveMode) {
                        // Worker download endpoint may not exist yet — best-effort.
                        window.location.href = `${workerUrl()}/deals/${rawId}/documents/${d.id}/download`;
                      } else {
                        toast(`Download URL: ${d.id}`, { type: 'info' });
                      }
                    },
                  },
                  {
                    label: 'Delete',
                    danger: true,
                    onSelect: () => toast('Document removal available on enterprise plans', { type: 'info' }),
                  },
                ];
                return (
                  <button key={d.id} onClick={() => setSelectedDoc(d.name)}
                    className={`w-full text-left p-3 rounded-md border transition-colors ${
                      selectedDoc === d.name ? 'bg-brand-50 border-brand-500' : 'border-border hover:bg-ink-300/10'
                    }`}>
                    <div className="flex items-start gap-3">
                      <div className="w-9 h-9 rounded bg-ink-300/30 flex items-center justify-center flex-shrink-0">
                        {d.name.endsWith('.xlsx') ? <FileSpreadsheet size={16} className="text-success-700" /> : <FileText size={16} className="text-ink-700" />}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <div className="text-[12.5px] font-medium text-ink-900 truncate">{d.name}</div>
                          <StatusBadge value={d.status} />
                          <Badge tone="gray">{d.type}</Badge>
                          {docCritical > 0 && (
                            <span
                              role="button"
                              onClick={(e) => { e.stopPropagation(); goToVariance(); }}
                              className="inline-flex items-center gap-1 px-2 py-0.5 text-[10.5px] font-semibold rounded-md bg-danger-50 text-danger-700 border border-danger-500/30 hover:bg-danger-500 hover:text-white transition-colors cursor-pointer"
                              title="Open Broker Variance tab"
                            >
                              <AlertTriangle size={10} />
                              {docCritical} critical variance flag{docCritical === 1 ? '' : 's'}
                            </span>
                          )}
                        </div>
                        <div className="text-[11px] text-ink-500 mt-1">{d.size} · {d.date}</div>
                        {d.status === 'Extracted' && (
                          <div className="flex items-center gap-3 mt-2">
                            {d.fields > 0 ? (
                              (() => {
                                // Color the avg-confidence percent + flag low
                                // averages with a "Needs review" pill so the
                                // doc card mirrors the field-level tiering.
                                const tier = confidenceTier(d.confidence);
                                const pctClass =
                                  tier.tone === 'green' ? 'text-success-700'
                                  : tier.tone === 'amber' ? 'text-warn-700'
                                  : 'text-danger-700';
                                return (
                                  <div className="flex items-center gap-2 text-[11px] text-ink-700">
                                    <span>
                                      <span className="text-brand-700 font-medium">{d.fields}</span> fields extracted
                                      {' · '}<span className={`font-medium ${pctClass}`}>{d.confidence}%</span> confidence
                                    </span>
                                    {tier.tone === 'red' && (
                                      <Badge tone="red">Needs review</Badge>
                                    )}
                                  </div>
                                );
                              })()
                            ) : (
                              // The doc is EXTRACTED on the worker but the
                              // extraction results poll hasn't caught up yet,
                              // OR the LLM Extractor returned 0 scalar fields
                              // for a narrative-heavy OM. Don't show
                              // "0 fields · 0% confidence" — that contradicts
                              // the right panel which shows the same data.
                              <div className="flex items-center gap-1.5 text-[11px] text-ink-500">
                                <Loader2 size={11} className="animate-spin" />
                                Loading extraction details…
                              </div>
                            )}
                            {d.populates.length > 0 && (
                              <div className="flex gap-1">
                                {d.populates.map((p) => <Badge key={p} tone="blue">{p}</Badge>)}
                              </div>
                            )}
                          </div>
                        )}
                        {d.status === 'Processing' && (
                          <div className="flex items-center gap-1.5 mt-2 text-[11px] text-brand-700">
                            <Loader2 size={11} className="animate-spin" /> Extracting…
                          </div>
                        )}
                        {d.status === 'Failed' && d.errorMessage && (
                          <div className="mt-2 flex items-start gap-1.5 text-[11px] text-danger-700">
                            <AlertTriangle size={11} className="mt-0.5 shrink-0" />
                            <span>
                              <span className="font-semibold">
                                {d.errorKind === 'billing'
                                  ? 'API credit exhausted'
                                  : d.errorKind === 'auth'
                                    ? 'API key rejected'
                                    : d.errorKind === 'rate_limit'
                                      ? 'Rate limited'
                                      : d.errorKind === 'parse'
                                        ? 'Parser couldn’t read the file'
                                        : d.errorKind === 'empty_envelope'
                                          ? 'Extraction returned 0 fields'
                                          : 'Extraction failed'}
                                .
                              </span>{' '}
                              {d.errorMessage}
                            </span>
                          </div>
                        )}
                      </div>
                      <div onClick={(e) => e.stopPropagation()}>
                        <KebabMenu items={rowMenu} />
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>

            <Card className="p-4 bg-ink-300/5">
              <h4 className="text-[12px] font-semibold text-ink-900 mb-2">Extracted Data</h4>
              {selectedDoc ? (
                <div>
                  <div className="text-[11px] text-ink-500 mb-3 truncate">{selectedDoc}</div>
                  {selectedHasVariance && selectedVarianceFlags.length > 0 && (
                    <button
                      onClick={goToVariance}
                      className="w-full mb-3 p-2.5 rounded-md border border-danger-500/40 bg-danger-50 hover:bg-danger-500 hover:text-white group transition-colors text-left"
                    >
                      <div className="flex items-start gap-2">
                        <AlertTriangle size={13} className="text-danger-700 group-hover:text-white mt-0.5 flex-shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="text-[11.5px] font-semibold text-danger-700 group-hover:text-white">
                            {selectedCriticalCount} critical · {selectedVarianceFlags.length - selectedCriticalCount} other variance flags
                          </div>
                          <div className="text-[10.5px] text-danger-700/80 group-hover:text-white/90 mt-0.5">
                            Broker pro forma vs T-12 actuals diverge materially. View Variance tab.
                          </div>
                        </div>
                        <ArrowRight size={12} className="text-danger-700 group-hover:text-white mt-0.5" />
                      </div>
                    </button>
                  )}
                  {(() => {
                    if (!selectedDocRow || selectedDocRow.status !== 'Extracted') {
                      return <div className="text-[11.5px] text-ink-500">Document still processing…</div>;
                    }

                    // Build a uniform [{label, value, pct}] list so the
                    // summary strip + filter logic doesn't fork between live
                    // and mock branches.
                    type FieldRow = { label: string; value: string; pct: number };
                    let rows: FieldRow[];
                    if (liveMode && selectedDocRow.fieldList && selectedDocRow.fieldList.length > 0) {
                      rows = selectedDocRow.fieldList.slice(0, 12).map((f) => ({
                        label: f.field_name,
                        value: formatValue(f.value, f.unit),
                        pct: Math.round((f.confidence ?? 0) * 100),
                      }));
                    } else if (!liveMode) {
                      // Demo / mock fallback (no live worker connection):
                      // show curated KPIs so the Kimpton demo still
                      // looks populated.
                      rows = [
                        { label: 'ADR', value: '$385', pct: 96 },
                        { label: 'Occupancy', value: '76.2%', pct: 94 },
                        { label: 'RevPAR', value: '$293', pct: 97 },
                        { label: 'NOI (T-12)', value: '$4.28M', pct: 92 },
                        { label: 'Gross Revenue', value: '$15.08M', pct: 95 },
                        { label: 'Operating Expenses', value: '$9.32M', pct: 89 },
                      ];
                    } else {
                      // Live mode but the worker returned 0 fields — show
                      // an honest empty state instead of the curated mock
                      // KPIs that misled Sam into thinking extraction
                      // worked (QA 2026-05-13).
                      return (
                        <div className="space-y-3 text-[11.5px]">
                          <div className="flex items-start gap-2 p-3 rounded-md bg-warn-50 border border-warn-500/30">
                            <AlertTriangle size={14} className="text-warn-700 mt-0.5 shrink-0" />
                            <div>
                              <div className="font-semibold text-ink-900">
                                Extraction returned no fields
                              </div>
                              <div className="text-ink-700 mt-0.5">
                                The worker parsed the document but the
                                Extractor agent emitted an empty result.
                                Common causes: the doc is image-heavy
                                without enough OCR'd text, the LLM hit a
                                structured-output edge case, or
                                Anthropic API credits dipped mid-call.
                                Re-upload to retry, or check the worker
                                logs.
                              </div>
                            </div>
                          </div>
                        </div>
                      );
                    }

                    const high = rows.filter((r) => r.pct >= 95).length;
                    const medium = rows.filter((r) => r.pct >= 85 && r.pct < 95).length;
                    const low = rows.filter((r) => r.pct < 85).length;
                    const visible = needsReviewOnly ? rows.filter((r) => r.pct < 85) : rows;

                    return (
                      <>
                        <div className="flex items-center gap-2 mb-3 text-[11px] text-ink-700 tabular-nums">
                          <span className="inline-flex items-center gap-1">
                            <span className="w-1.5 h-1.5 rounded-full bg-success-500" aria-hidden="true" />
                            <span className="font-medium">{high}</span> high
                          </span>
                          <span className="text-ink-300" aria-hidden="true">·</span>
                          <span className="inline-flex items-center gap-1">
                            <span className="w-1.5 h-1.5 rounded-full bg-warn-500" aria-hidden="true" />
                            <span className="font-medium">{medium}</span> medium
                          </span>
                          <span className="text-ink-300" aria-hidden="true">·</span>
                          <button
                            type="button"
                            onClick={() => setNeedsReviewOnly((v) => !v)}
                            disabled={low === 0}
                            aria-pressed={needsReviewOnly}
                            title={low === 0 ? 'No fields need review' : 'Filter to fields under 85% confidence'}
                            className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 -my-0.5 transition-colors ${
                              needsReviewOnly
                                ? 'bg-danger-50 text-danger-700 border border-danger-500/25'
                                : 'text-danger-700 hover:bg-danger-50 border border-transparent'
                            } ${low === 0 ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                          >
                            <span className="w-1.5 h-1.5 rounded-full bg-danger-500" aria-hidden="true" />
                            <span className="font-medium">{low}</span> needs review
                          </button>
                        </div>
                        {visible.length === 0 ? (
                          <div className="text-[11.5px] text-ink-500 py-4 text-center">
                            No fields match the current filter.
                          </div>
                        ) : (
                          <div className="space-y-2 text-[11.5px]">
                            {visible.map((r) => (
                              <DataRow
                                key={r.label}
                                label={r.label}
                                value={r.value}
                                confidence={r.pct}
                              />
                            ))}
                          </div>
                        )}
                      </>
                    );
                  })()}
                </div>
              ) : (
                <div className="text-center py-8">
                  <div className="text-[11.5px] text-ink-500">Select a document to view extracted data</div>
                </div>
              )}
            </Card>
          </div>
        </Card>
      )}
    </div>
  );
}

function DataRow({ label, value, confidence }: { label: string; value: string; confidence: number }) {
  // Tier label sits next to the numeric ConfidenceBadge so analysts get the
  // shared red/amber/green semantics at a glance without losing the precise %.
  const tier = confidenceTier(confidence);
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
      <span className="text-ink-500">{label}</span>
      <div className="flex items-center gap-2">
        <span className="font-medium tabular-nums text-ink-900">{value}</span>
        <Badge tone={tier.tone}>{tier.label}</Badge>
        {/* `confidence` is a 0–100 percent at this row's call sites; convert
            to the 0–1 scale ConfidenceBadge expects so the same component
            grades to red/amber/green at the agreed-upon thresholds. */}
        <ConfidenceBadge value={confidence / 100} />
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
