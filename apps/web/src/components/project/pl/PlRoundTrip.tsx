'use client';

/**
 * FON-41 E-013 / E-017 — editable P&L workbooks: Export → edit in Excel →
 * Import (validated preview) → Apply.
 *
 *   • Historical P&L — the workbook carries a hidden stable id per value
 *     (`document_id::field_name`). Apply writes each change through the same
 *     worker path a manual cell correction uses (`review_extraction_field`,
 *     reviewed = edited, audited) — never around it.
 *   • Future P&L — the workbook carries the projection (read-only) and an
 *     Assumptions sheet keyed by `field_overrides` key. Only assumptions are
 *     imported; rows that move the model need a Note (FON-74), and edits to
 *     engine-computed cells are reported as "computed — edit the assumption
 *     instead". Apply saves through the deal's override PATCH.
 *
 * The preview is the whole point: nothing in an upload is written until the
 * analyst has seen old → new for every change and every row that was refused.
 */

import { useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { AlertTriangle, Download, Loader2, Upload, X } from 'lucide-react';
import {
  api,
  type HistImportPreview,
  type ImportApplyResult,
  type ImportIssue,
  type ProjImportPreview,
} from '@/lib/api';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/format';

export type RoundTripKind = 'historicals' | 'projections';

type Preview =
  | { kind: 'historicals'; data: HistImportPreview }
  | { kind: 'projections'; data: ProjImportPreview };

const fmtVal = (v: unknown): string => {
  if (v == null || v === '') return '—';
  if (typeof v === 'number') return v.toLocaleString('en-US', { maximumFractionDigits: 6 });
  return String(v);
};

function errorText(err: unknown): string {
  if (err && typeof err === 'object' && 'body' in err) {
    try {
      const parsed = JSON.parse(String((err as { body: unknown }).body));
      const d = parsed?.detail;
      if (d && typeof d === 'object' && typeof d.message === 'string') return d.message;
      if (typeof d === 'string') return d;
    } catch { /* fall through */ }
  }
  return err instanceof Error ? err.message : String(err);
}

export function PlRoundTripControls({
  dealId,
  kind,
  onApplied,
}: {
  dealId: string;
  kind: RoundTripKind;
  /** Called after a successful Apply — the host refreshes (and re-models). */
  onApplied?: (result: ImportApplyResult, documentIds: string[]) => void | Promise<void>;
}) {
  const { toast } = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<'export' | 'import' | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);

  const doExport = async () => {
    setBusy('export');
    try {
      const name = kind === 'historicals' ? 'historical-pl' : 'future-pl';
      await api.plRoundTrip.download(dealId, kind, `fondok-${name}-${dealId}.xlsx`);
    } catch (err) {
      toast(`Export failed: ${errorText(err)}`, { type: 'error' });
    } finally {
      setBusy(null);
    }
  };

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    setBusy('import');
    try {
      const p = kind === 'historicals'
        ? { kind, data: await api.plRoundTrip.previewHistoricals(dealId, file) } as Preview
        : { kind, data: await api.plRoundTrip.previewProjections(dealId, file) } as Preview;
      setPreview(p);
    } catch (err) {
      toast(`Import refused: ${errorText(err)}`, { type: 'error' });
    } finally {
      setBusy(null);
      if (inputRef.current) inputRef.current.value = '';
    }
  };

  const label = kind === 'historicals' ? 'Historical P&L' : 'Future P&L';
  return (
    <>
      <span className="inline-flex items-center gap-1.5">
        <button
          type="button"
          data-testid={`${kind}-export-xlsx`}
          onClick={doExport}
          disabled={busy != null}
          title={`Download the ${label} as an editable Excel workbook (stable field ids) to re-import`}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11.5px] font-medium border border-border text-ink-600 hover:text-ink-900 disabled:opacity-50"
        >
          {busy === 'export' ? <Loader2 size={11} className="animate-spin" /> : <Download size={11} />} Editable .xlsx
        </button>
        <button
          type="button"
          data-testid={`${kind}-import-xlsx`}
          onClick={() => inputRef.current?.click()}
          disabled={busy != null}
          title={`Import an edited ${label} workbook — you'll see a preview before anything changes`}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11.5px] font-medium border border-border text-ink-600 hover:text-ink-900 disabled:opacity-50"
        >
          {busy === 'import' ? <Loader2 size={11} className="animate-spin" /> : <Upload size={11} />} Import
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          className="hidden"
          data-testid={`${kind}-import-file`}
          onChange={(e) => onFile(e.target.files?.[0])}
        />
      </span>
      {preview && (
        <ImportPreviewDialog
          dealId={dealId}
          preview={preview}
          onClose={() => setPreview(null)}
          onApplied={async (res, docIds) => {
            setPreview(null);
            const n = res.applied.length;
            const skipped = res.skipped.length;
            toast(
              `${n} change${n === 1 ? '' : 's'} applied${skipped ? ` · ${skipped} skipped (changed since the preview)` : ''}`,
              { type: n ? 'success' : 'info' },
            );
            await onApplied?.(res, docIds);
          }}
        />
      )}
    </>
  );
}

function IssueList({ title, tone, items, render }: {
  title: string;
  tone: 'warn' | 'danger' | 'muted';
  items: ImportIssue[];
  render: (i: ImportIssue) => ReactNode;
}) {
  if (!items.length) return null;
  return (
    <section className="mt-3" data-testid={`import-section-${title.toLowerCase().replace(/[^a-z]+/g, '-')}`}>
      <h5 className={cn(
        'text-[11px] font-semibold uppercase tracking-wide mb-1',
        tone === 'danger' ? 'text-danger-700' : tone === 'warn' ? 'text-amber-700' : 'text-ink-500',
      )}>
        {title} · {items.length}
      </h5>
      <ul className="space-y-1">
        {items.map((it, i) => (
          <li key={`${it.cell_ref}-${i}`} className="text-[11.5px] text-ink-700 flex gap-2">
            <span className="shrink-0 font-mono text-[10.5px] text-ink-500 w-10">{it.cell_ref}</span>
            <span>{render(it)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ImportPreviewDialog({
  dealId,
  preview,
  onClose,
  onApplied,
}: {
  dealId: string;
  preview: Preview;
  onClose: () => void;
  onApplied: (res: ImportApplyResult, documentIds: string[]) => void | Promise<void>;
}) {
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changes = preview.data.changes;
  const nChanges = changes.length;

  const apply = async () => {
    setApplying(true);
    setError(null);
    try {
      if (preview.kind === 'historicals') {
        const res = await api.plRoundTrip.applyHistoricals(
          dealId,
          preview.data.changes.map((c) => ({ cell_id: c.cell_id, new_value: c.new_value, old_value: c.old_value })),
        );
        const docIds = [...new Set(preview.data.changes.map((c) => c.document_id))];
        await onApplied(res, docIds);
      } else {
        const res = await api.plRoundTrip.applyProjections(
          dealId,
          preview.data.changes.map((c) => ({ key: c.key, new_value: c.new_value, note: c.note, old_value: c.old_value })),
        );
        await onApplied(res, []);
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setApplying(false);
    }
  };

  const d = preview.data;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 p-4" role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Import preview"
        data-testid="import-preview-dialog"
        className="w-full max-w-2xl max-h-[85vh] flex flex-col rounded-lg bg-white shadow-2xl"
      >
        <header className="flex items-start justify-between gap-3 px-5 py-3 border-b border-border">
          <div>
            <h4 className="text-[14px] font-semibold text-ink-900">
              Import preview — {preview.kind === 'historicals' ? 'Historical P&L' : 'Future P&L assumptions'}
            </h4>
            <p className="text-[11.5px] text-ink-500">
              Nothing has changed yet. {nChanges} change{nChanges === 1 ? '' : 's'} ready · {d.unchanged} unchanged
              {preview.kind === 'historicals'
                ? ' — each is saved as an analyst correction at its source document.'
                : ' — saved as analyst overrides with your notes, then the model re-runs.'}
            </p>
          </div>
          <button type="button" aria-label="Close preview" onClick={onClose} className="text-ink-500 hover:text-ink-900">
            <X size={14} />
          </button>
        </header>
        <div className="flex-1 overflow-auto px-5 py-3">
          {nChanges > 0 ? (
            <table className="w-full text-[11.5px]" data-testid="import-changes">
              <thead>
                <tr className="text-left text-[10px] uppercase tracking-wide text-ink-500">
                  <th className="py-1 pr-2">Cell</th>
                  <th className="py-1 pr-2">{preview.kind === 'historicals' ? 'Line · period' : 'Assumption'}</th>
                  <th className="py-1 pr-2 text-right">Old</th>
                  <th className="py-1 pr-2 text-right">New</th>
                  {preview.kind === 'projections' && <th className="py-1">Note</th>}
                </tr>
              </thead>
              <tbody>
                {preview.kind === 'historicals'
                  ? preview.data.changes.map((c) => (
                    <tr key={c.cell_id} className="border-t border-border/60">
                      <td className="py-1 pr-2 font-mono text-[10.5px] text-ink-500">{c.cell_ref}</td>
                      <td className="py-1 pr-2 text-ink-800">
                        {c.line_label} · {c.period_label}
                        {c.filename && <span className="block text-[10.5px] text-ink-500">{c.filename}</span>}
                      </td>
                      <td className="py-1 pr-2 text-right tabular-nums text-ink-500">{fmtVal(c.old_value)}</td>
                      <td className="py-1 pr-2 text-right tabular-nums font-semibold text-ink-900">{fmtVal(c.new_value)}</td>
                    </tr>
                  ))
                  : preview.data.changes.map((c) => (
                    <tr key={c.key} className="border-t border-border/60">
                      <td className="py-1 pr-2 font-mono text-[10.5px] text-ink-500">{c.cell_ref}</td>
                      <td className="py-1 pr-2 text-ink-800">
                        {c.label}
                        <span className="block font-mono text-[10px] text-ink-500">{c.key}</span>
                      </td>
                      <td className="py-1 pr-2 text-right tabular-nums text-ink-500">{fmtVal(c.old_value)}</td>
                      <td className="py-1 pr-2 text-right tabular-nums font-semibold text-ink-900">{fmtVal(c.new_value)}</td>
                      <td className="py-1 text-ink-700">{c.note || <span className="text-ink-400">—</span>}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          ) : (
            <p className="text-[12px] text-ink-600">No changed values found in this workbook.</p>
          )}

          <IssueList
            title="Mapping errors"
            tone="danger"
            items={d.mapping_errors}
            render={(i) => (
              <>
                <span className="font-medium">{i.line_label ?? i.key ?? '—'}{i.period_label ? ` · ${i.period_label}` : ''}</span>
                {' — '}{i.detail ?? i.reason}
              </>
            )}
          />
          <IssueList
            title="Non-numeric cells"
            tone="danger"
            items={d.non_numeric}
            render={(i) => (
              <>
                <span className="font-medium">{i.line_label ?? i.label ?? i.key}</span>
                {' — '}“{i.raw}” {i.detail ?? 'is not a number'}
              </>
            )}
          />
          {preview.kind === 'projections' && (
            <>
              <IssueList
                title="Rejected"
                tone="warn"
                items={preview.data.rejected}
                render={(i) => (
                  <>
                    <span className="font-medium">{i.label ?? i.key}</span>
                    {' '}{fmtVal(i.old_value)} → {fmtVal(i.new_value)} — {i.detail ?? i.reason}
                  </>
                )}
              />
              <IssueList
                title="Computed cells"
                tone="muted"
                items={preview.data.computed_edits}
                render={(i) => (
                  <>
                    <span className="font-medium">{i.line_label} · {i.period_label}</span>
                    {' '}{fmtVal(i.old_value)} → {fmtVal(i.new_value)} — computed — edit the assumption instead
                  </>
                )}
              />
            </>
          )}
        </div>
        <footer className="flex items-center justify-between gap-3 px-5 py-3 border-t border-border">
          {error ? (
            <span role="alert" className="inline-flex items-center gap-1.5 text-[11.5px] text-danger-700">
              <AlertTriangle size={12} /> {error}
            </span>
          ) : <span className="text-[11px] text-ink-500">Only the changes listed above are applied.</span>}
          <span className="inline-flex gap-2">
            <button type="button" onClick={onClose} className="px-3 py-1.5 rounded-md text-[12px] border border-border text-ink-700">
              Cancel
            </button>
            <button
              type="button"
              data-testid="import-apply"
              onClick={apply}
              disabled={applying || nChanges === 0}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] font-medium bg-ink-900 text-white disabled:opacity-40"
            >
              {applying && <Loader2 size={12} className="animate-spin" />}
              Apply {nChanges} change{nChanges === 1 ? '' : 's'}
            </button>
          </span>
        </footer>
      </div>
    </div>
  );
}
