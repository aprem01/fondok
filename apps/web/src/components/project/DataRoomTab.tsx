'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  UploadCloud, FolderOpen, Info, FileText, FileSpreadsheet,
  CheckCircle2, Loader2, Circle, AlertTriangle, ArrowRight,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge, StatusBadge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import { ConfidenceBadge } from '@/components/ui/ConfidenceBadge';
import { documentChecklist, engines, kimptonDocuments, templates } from '@/lib/mockData';
import { criticalCount, warnCount, varianceFlags } from '@/lib/varianceData';
import { isWorkerConnected, workerUrl, WorkerDocument, ExtractionField } from '@/lib/api';
import { useDocuments } from '@/lib/hooks/useDocuments';
import { useToast } from '@/components/ui/Toast';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';

// Documents with broker-vs-T12 variance flags raised against them.
const VARIANCE_DOCS = new Set([
  'Offering_Memorandum_Final.pdf',
  'T12_FinancialStatement.xlsx',
]);

// Map worker doc statuses to a single label the StatusBadge knows about.
function statusLabel(s: string): string {
  switch (s) {
    case 'EXTRACTED':
      return 'Extracted';
    case 'EXTRACTING':
    case 'CLASSIFYING':
    case 'PROCESSING':
      return 'Processing';
    case 'FAILED':
      return 'Pending';
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

export default function DataRoomTab({ projectId }: { projectId: number }) {
  const router = useRouter();
  const params = useParams();
  // Raw id from the URL — could be a numeric mock id or a real worker UUID.
  const rawId = (params?.id as string | undefined) ?? String(projectId);
  const isMockId = /^\d+$/.test(rawId);
  const isFullDoc = isMockId && Number(rawId) === 7; // Kimpton Angler

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedDoc, setSelectedDoc] = useState<string | null>(null);
  // Browse Templates popover — anchored to whichever button the user clicked.
  const [templatesAnchor, setTemplatesAnchor] = useState<'empty' | 'inline' | null>(null);
  const { toast } = useToast();

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

  const checklist = documentChecklist.map((d, i) => ({
    name: d,
    complete: liveMode
      ? i < docs.filter((x) => x.status === 'Extracted').length
      : isFullDoc && i < 4,
  }));

  const completeCount = checklist.filter((d) => d.complete).length;
  const extracted = docs.filter((d) => d.status === 'Extracted').length;
  const processing = docs.filter((d) => d.status === 'Processing').length;

  const onPickFiles = () => fileInputRef.current?.click();

  const onFilesSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files ? Array.from(e.target.files) : [];
    e.target.value = ''; // reset so same file can be re-picked
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

  return (
    <div className="space-y-5">
      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.xlsx,.xls,.csv,.doc,.docx"
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
            <span className="font-semibold"> Document Checklist</span> on the right tracks what
            we still need to fully underwrite the deal; the
            <span className="font-semibold"> Engine Status</span> bars climb as the AI gets more confident.
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
        </div>
      </Card>

      {liveMode && docs.length === 0 ? (
        <Card className="p-8">
          <div
            onClick={onPickFiles}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') onPickFiles();
            }}
            className="cursor-pointer border-2 border-dashed border-ink-300 rounded-lg py-12 px-6 text-center hover:border-brand-500 hover:bg-brand-50/40 transition-colors"
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
        <Card className="p-5">
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

      <div className="grid grid-cols-3 gap-5">
        {/* Document Checklist */}
        <Card className="col-span-2 p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-[14px] font-semibold text-ink-900">Document Checklist</h3>
            <span className="text-[12px] text-ink-500 tabular-nums">{completeCount}/{checklist.length}</span>
          </div>
          <div className="mb-4">
            <div className="flex justify-between text-[11px] text-ink-500 mb-1">
              <span>Underwriting Ready</span>
              <span>{Math.round((completeCount / checklist.length) * 100)}%</span>
            </div>
            <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
              <div className="h-full bg-success-500" style={{ width: `${(completeCount / checklist.length) * 100}%` }} />
            </div>
          </div>
          <div className="space-y-2">
            {checklist.map((d) => (
              <div key={d.name} className="flex items-center gap-3 py-1.5">
                {d.complete
                  ? <CheckCircle2 size={15} className="text-success-500 flex-shrink-0" />
                  : <Circle size={15} className="text-ink-300 flex-shrink-0" />}
                <span className={`text-[12.5px] flex-1 ${d.complete ? 'text-ink-900' : 'text-ink-500'}`}>{d.name}</span>
                <Badge tone="red">REQ</Badge>
              </div>
            ))}
          </div>
        </Card>

        {/* Engine Status */}
        <Card className="p-5">
          <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Engine Status</h3>
          <p className="text-[11.5px] text-ink-500 mb-4 leading-relaxed">
            Each engine builds part of the model (P&amp;L, Debt, Returns, etc.). The
            percentage is how confident the engine is, based on which documents
            you&apos;ve uploaded.
          </p>
          <div className="space-y-3.5">
            {engines.map((e) => (
              <div key={e.id} title={`${e.label} is ${e.progress}% confident — climbs as you upload the documents this engine needs.`}>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-[12px] text-ink-700 font-medium">{e.label}</span>
                  <span className="text-[11px] text-ink-500 tabular-nums">{e.progress}%</span>
                </div>
                <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
                  <div className="h-full bg-brand-500" style={{ width: `${e.progress}%` }} />
                </div>
              </div>
            ))}
          </div>
          <div className="text-[11px] text-ink-500 mt-4 pt-4 border-t border-border">
            Upload more documents to increase confidence
          </div>
        </Card>
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
                            <div className="text-[11px] text-ink-700">
                              <span className="text-brand-700 font-medium">{d.fields}</span> fields extracted
                              {' · '}<span className="font-medium">{d.confidence}%</span> confidence
                            </div>
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
                    if (liveMode && selectedDocRow.fieldList && selectedDocRow.fieldList.length > 0) {
                      return (
                        <div className="space-y-2 text-[11.5px]">
                          {selectedDocRow.fieldList.slice(0, 12).map((f) => (
                            <DataRow
                              key={f.field_name}
                              label={f.field_name}
                              value={formatValue(f.value, f.unit)}
                              confidence={Math.round((f.confidence ?? 0) * 100)}
                            />
                          ))}
                        </div>
                      );
                    }
                    // Mock fallback
                    return (
                      <div className="space-y-2 text-[11.5px]">
                        <DataRow label="ADR" value="$385" confidence={96} />
                        <DataRow label="Occupancy" value="76.2%" confidence={94} />
                        <DataRow label="RevPAR" value="$293" confidence={97} />
                        <DataRow label="NOI (T-12)" value="$4.28M" confidence={92} />
                        <DataRow label="Gross Revenue" value="$15.08M" confidence={95} />
                        <DataRow label="Operating Expenses" value="$9.32M" confidence={89} />
                      </div>
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
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border last:border-0">
      <span className="text-ink-500">{label}</span>
      <div className="flex items-center gap-2">
        <span className="font-medium tabular-nums text-ink-900">{value}</span>
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
