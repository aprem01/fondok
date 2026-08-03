'use client';

/**
 * PLReviewSection — "Review" on the P&L tab.
 *
 * The single place to validate a deal's FINANCIAL data (per the 2026-08 team
 * sync): instead of recreating the P&L in the Data Room, financial statements
 * are reviewed here, in the P&L tab, where the analyst already works. It pulls
 * every low-confidence field off the deal's financial documents (T-12 / P&L /
 * Monthly / YTD) into one queue and walks the analyst through them one at a
 * time — "item 3 of 47" — showing the extracted value, the source document +
 * highlighted snippet, and Accept / Edit actions. Reuses the FON-23 review
 * endpoint (api.documents.reviewField), so overrides are audited and re-flow
 * into the engines on the next run.
 */

import { useMemo, useState, useCallback } from 'react';
import {
  CheckCircle2, ChevronLeft, ChevronRight, Pencil, FileText, Loader2, Check,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';
import { useToast } from '@/components/ui/Toast';
import { api } from '@/lib/api';
import type { ExtractionField, WorkerDocument } from '@/lib/api';
import { useDocuments } from '@/lib/hooks/useDocuments';

const FINANCIAL_TYPES = new Set(['T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD', 'PNL_BENCHMARK']);
const REVIEW_THRESHOLD = 0.85; // fields below this need analyst review

const DOC_TYPE_LABEL: Record<string, string> = {
  T12: 'T-12', PNL: 'P&L', PNL_MONTHLY: 'Monthly P&L', PNL_YTD: 'YTD P&L',
  PNL_BENCHMARK: 'P&L Benchmark',
};

/** dotted USALI path → readable label (last 1-2 segments, de-underscored). */
function humanizeField(name: string): string {
  const clean = name.replace(/_(annual|budget|total|20\d\d)$/i, '');
  const seg = clean.split('.');
  const tail = seg.slice(-2).join(' · ').replace(/_/g, ' ');
  return tail.replace(/\b\w/g, (c) => c.toUpperCase());
}

function fmtValue(v: unknown, unit: string | null): string {
  if (v == null || v === '') return '—';
  if (typeof v === 'number') {
    const s = Math.abs(v) >= 1000 ? `$${Math.round(v).toLocaleString()}` : String(v);
    return unit === '%' ? `${v}%` : s;
  }
  return `${v}${unit && unit !== '$' ? ` ${unit}` : ''}`;
}

interface FlaggedField {
  doc: WorkerDocument;
  field: ExtractionField;
  pct: number;
}

export default function PLReviewSection({
  dealId,
  isKimptonDemo,
}: {
  dealId: string | number;
  isKimptonDemo?: boolean;
}) {
  const rawId = String(dealId);
  const { documents, extractions, refreshExtraction } = useDocuments(rawId);
  const { toast } = useToast();

  const [idx, setIdx] = useState(0);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [bulk, setBulk] = useState(false);

  // Build the flagged-field queue across every financial statement.
  const queue = useMemo<FlaggedField[]>(() => {
    const out: FlaggedField[] = [];
    for (const doc of documents) {
      if (!FINANCIAL_TYPES.has((doc.doc_type ?? '').toUpperCase())) continue;
      const ex = extractions[doc.id];
      for (const field of ex?.fields ?? []) {
        if (field.reviewed) continue;
        const conf = field.confidence ?? 1;
        if (conf >= REVIEW_THRESHOLD) continue;
        out.push({ doc, field, pct: Math.round(conf * 100) });
      }
    }
    return out.sort((a, b) => a.pct - b.pct); // lowest confidence first
  }, [documents, extractions]);

  const financialDocs = useMemo(
    () => documents.filter((d) => FINANCIAL_TYPES.has((d.doc_type ?? '').toUpperCase())),
    [documents],
  );

  const clampedIdx = Math.min(idx, Math.max(0, queue.length - 1));
  const current = queue[clampedIdx];

  const go = (delta: number) => {
    setEditing(false);
    setIdx((i) => Math.max(0, Math.min(queue.length - 1, i + delta)));
  };

  const review = useCallback(
    async (action: 'accept' | 'edit', value?: string) => {
      if (!current || busy) return;
      setBusy(true);
      try {
        await api.documents.reviewField(rawId, current.doc.id, {
          field_name: current.field.field_name,
          action,
          ...(action === 'edit' ? { value } : {}),
        });
        toast(`${action === 'edit' ? 'Updated' : 'Accepted'} ${humanizeField(current.field.field_name)}`, { type: 'success' });
        await refreshExtraction(current.doc.id);
        setEditing(false);
        // queue shrinks by one; keep the pointer on the same slot so the
        // next item slides in automatically.
        setIdx((i) => Math.min(i, Math.max(0, queue.length - 2)));
      } catch (err) {
        toast(`Couldn’t save: ${err instanceof Error ? err.message : String(err)}`, { type: 'error' });
      } finally {
        setBusy(false);
      }
    },
    [current, busy, rawId, refreshExtraction, toast, queue.length],
  );

  const acceptAll = useCallback(async () => {
    if (bulk || queue.length === 0) return;
    setBulk(true);
    let ok = 0;
    const byDoc = new Set<string>();
    try {
      for (const f of queue) {
        try {
          await api.documents.reviewField(rawId, f.doc.id, { field_name: f.field.field_name, action: 'accept' });
          ok += 1;
          byDoc.add(f.doc.id);
        } catch { /* keep going */ }
      }
      for (const d of byDoc) await refreshExtraction(d);
      toast(`Accepted ${ok} field${ok === 1 ? '' : 's'}`, { type: 'success' });
      setIdx(0);
    } finally {
      setBulk(false);
    }
  }, [bulk, queue, rawId, refreshExtraction, toast]);

  if (isKimptonDemo) {
    return (
      <Card className="p-6 text-[13px] text-ink-500">
        Financial data review is available on live deals — upload T-12 / P&amp;L
        statements and this walks you through every value that needs a look.
      </Card>
    );
  }

  if (financialDocs.length === 0) {
    return (
      <Card className="p-8 text-center">
        <FileText size={30} className="text-ink-400 mx-auto mb-3" strokeWidth={1.5} />
        <div className="text-[14px] font-semibold text-ink-900">No financial statements yet</div>
        <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto">
          Upload a T-12 or P&amp;L in the Data Room. Once extracted, every value that
          needs your review shows up here to accept or correct.
        </p>
      </Card>
    );
  }

  const reviewed = queue.length === 0;

  return (
    <div className="flex flex-col gap-4">
      {/* Header + progress */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-[14px] font-semibold text-ink-900">Review your financials</h3>
          <p className="text-[12px] text-ink-500 mt-0.5">
            {reviewed
              ? `All values across ${financialDocs.length} statement${financialDocs.length === 1 ? '' : 's'} are verified.`
              : `${queue.length} value${queue.length === 1 ? '' : 's'} need your review across ${financialDocs.length} statement${financialDocs.length === 1 ? '' : 's'}.`}
          </p>
        </div>
        {!reviewed && (
          <Button variant="secondary" size="sm" onClick={acceptAll} disabled={bulk}>
            {bulk ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
            Accept all remaining
          </Button>
        )}
      </div>

      {reviewed ? (
        <Card className="p-8 text-center border-success-500/30 bg-success-50/40">
          <CheckCircle2 size={34} className="text-success-700 mx-auto mb-3" />
          <div className="text-[14px] font-semibold text-ink-900">Financial data verified</div>
          <p className="text-[12.5px] text-ink-600 mt-1">
            Every extracted value cleared review. Head to Historicals / Projections to model.
          </p>
        </Card>
      ) : current ? (
        <Card className="p-0 overflow-hidden">
          {/* Which statement + position */}
          <div className="flex items-center justify-between px-5 py-3 border-b border-border bg-surface-2/40">
            <div className="flex items-center gap-2 min-w-0">
              <FileText size={14} className="text-ink-500 shrink-0" />
              <span className="text-[12.5px] font-medium text-ink-900 truncate">{current.doc.filename}</span>
              <Badge tone="gray">{DOC_TYPE_LABEL[(current.doc.doc_type ?? '').toUpperCase()] ?? current.doc.doc_type}</Badge>
              {current.doc.fiscal_year != null && <Badge tone="blue">FY {current.doc.fiscal_year}</Badge>}
            </div>
            <span className="text-[11.5px] text-ink-500 tabular-nums shrink-0">
              Item {clampedIdx + 1} of {queue.length}
            </span>
          </div>

          {/* Progress bar */}
          <div className="h-1 bg-ink-300/25">
            <div className="h-full bg-brand-500 transition-all" style={{ width: `${((clampedIdx) / queue.length) * 100}%` }} />
          </div>

          {/* The flagged value */}
          <div className="p-5">
            <div className="flex items-start justify-between gap-3 mb-3">
              <div className="text-[13px] font-semibold text-ink-900">{humanizeField(current.field.field_name)}</div>
              <Badge tone={current.pct < 70 ? 'red' : 'amber'}>{current.pct}% confidence · needs review</Badge>
            </div>

            {!editing ? (
              <div className="text-[24px] font-semibold tabular-nums text-ink-900 mb-3">
                {fmtValue(current.field.value, current.field.unit)}
              </div>
            ) : (
              <div className="flex items-center gap-2 mb-3">
                <input
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter') void review('edit', draft); if (e.key === 'Escape') setEditing(false); }}
                  className="w-48 px-3 py-2 text-[15px] tabular-nums border border-brand-500 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100"
                />
                <Button variant="primary" size="sm" onClick={() => void review('edit', draft)} disabled={busy}>Save</Button>
                <Button variant="secondary" size="sm" onClick={() => setEditing(false)}>Cancel</Button>
              </div>
            )}

            {/* Source: where the number came from */}
            <div className="rounded-md border border-border bg-ink-100/40 px-3 py-2.5 mb-4">
              <div className="text-[10.5px] uppercase tracking-wide text-ink-500 mb-1">
                Source · {current.doc.filename}{current.field.source_page != null ? ` · p.${current.field.source_page}` : ''}
              </div>
              <div className="text-[12px] text-ink-700 font-mono leading-relaxed break-words max-h-24 overflow-y-auto">
                {current.field.raw_text || <span className="text-ink-400 italic">No source snippet captured.</span>}
              </div>
            </div>

            {/* Actions + navigation */}
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Button variant="primary" size="sm" onClick={() => void review('accept')} disabled={busy || editing}>
                  {busy ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} Accept
                </Button>
                <Button variant="secondary" size="sm"
                  onClick={() => { setDraft(current.field.value == null ? '' : String(current.field.value)); setEditing(true); }}
                  disabled={busy}>
                  <Pencil size={12} /> Edit
                </Button>
              </div>
              <div className="flex items-center gap-1">
                <button type="button" onClick={() => go(-1)} disabled={clampedIdx === 0}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-md text-ink-600 hover:bg-ink-100 disabled:opacity-40">
                  <ChevronLeft size={13} /> Prev
                </button>
                <button type="button" onClick={() => go(1)} disabled={clampedIdx >= queue.length - 1}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 text-[12px] rounded-md text-ink-600 hover:bg-ink-100 disabled:opacity-40">
                  Skip <ChevronRight size={13} />
                </button>
              </div>
            </div>
          </div>
        </Card>
      ) : null}

      {/* Per-statement remaining counts */}
      {!reviewed && (
        <div className="flex flex-wrap gap-2">
          {financialDocs.map((d) => {
            const remaining = queue.filter((q) => q.doc.id === d.id).length;
            return (
              <span key={d.id} className={cn(
                'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] border',
                remaining === 0 ? 'border-success-500/30 bg-success-50 text-success-700' : 'border-border text-ink-600',
              )}>
                {remaining === 0 && <Check size={10} />}
                {DOC_TYPE_LABEL[(d.doc_type ?? '').toUpperCase()] ?? d.doc_type}
                {d.fiscal_year != null ? ` ${d.fiscal_year}` : ''}
                {remaining > 0 && <span className="tabular-nums font-medium">· {remaining}</span>}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
