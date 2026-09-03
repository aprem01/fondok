'use client';

/**
 * DocumentCoverage — FON-18 / FON-22 / FON-31 unified Data Room surface.
 *
 * One "Document coverage" view that replaces the stack of (Deal readiness
 * card + legacy Document Checklist + flat doc list): a coverage header with
 * the run-the-model gate, then one row per required category. Covered
 * categories expand to their files; financial files carry inline
 * [T-12 / P&L] · [Annual / Monthly / YTD] · [Year] dropdowns wired to the
 * reclassify endpoint (POST-upload correction, re-buckets ranking + coverage).
 *
 * Currently rendered behind a ``?coverage=1`` preview flag so the live Data
 * Room is untouched until the layout is signed off.
 */

import { useState } from 'react';
import {
  CheckCircle2,
  Circle,
  ChevronRight,
  ChevronDown,
  FileText,
  Rocket,
  AlertCircle,
  GripVertical,
  ExternalLink,
  Download,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';

export interface CoverageFile {
  id: string;
  name: string;
  /** Canonical doc_type token (OM / T12 / PNL / PNL_MONTHLY / STR / …). */
  docType: string;
  fields: number;
  /** 0-100 overall extraction confidence. */
  confidence: number;
  /** Count of fields still needing review (<85%, not yet accepted). */
  toReview: number;
  fiscalYear: number | null;
  /** Upstream doc status (UPLOADED / EXTRACTED / FAILED / …) — drives the
   *  processing-state badge (FON-40). */
  status?: string;
}

// FON-40 — a single processing state per document, so a parsing file reads
// "Processing" rather than "0 fields", and a done file tells the user whether
// review is recommended.
function docStatusState(
  file: CoverageFile,
): { label: string; tone: 'gray' | 'blue' | 'amber' | 'green' | 'red' } {
  const s = (file.status ?? '').toUpperCase();
  if (s === 'FAILED' || s === 'PARSE_FAILED') return { label: 'Processing Failed', tone: 'red' };
  if (s === 'UPLOADING') return { label: 'Uploading', tone: 'gray' };
  const extracted = s === 'EXTRACTED' || file.fields > 0;
  if (!extracted) return { label: 'Processing', tone: 'blue' };
  if (file.toReview > 0) return { label: 'Review Recommended', tone: 'amber' };
  return { label: 'Ready for Review', tone: 'green' };
}

export interface DocumentCoverageProps {
  files: CoverageFile[];
  /** Reclassify a financial doc's type / year (fires the PATCH endpoint). */
  onReclassify: (
    docId: string,
    body: { doc_type?: string; fiscal_year?: number },
  ) => void;
  /** Open a file's extracted-data / review panel. */
  onOpenDoc: (docId: string, financial?: boolean) => void;
  /** Open the raw uploaded file in a new browser tab (↗). */
  onOpenInNewTab?: (docId: string) => void;
  /** Download the raw uploaded file (⬇). */
  onDownload?: (docId: string) => void;
  /** Doc id whose reclassify is in flight (disables its controls). */
  busyDocId?: string | null;
  className?: string;
}

type CategorySpec = {
  id: string;
  label: string;
  /** doc_type tokens that count toward this category. */
  match: string[];
  optional?: boolean;
  financial?: boolean;
};

// Mirrors the worker's COMPLETENESS_CATEGORIES, with T-12 + P&L collapsed
// into one "Financial Statements" row (FON-18). Order = the Data Room list.
const CATEGORIES: CategorySpec[] = [
  { id: 'om', label: 'Offering Memorandum', match: ['OM'] },
  {
    id: 'financials',
    label: 'Financial Statements',
    match: ['T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD', 'PNL_BENCHMARK'],
    financial: true,
  },
  { id: 'str', label: 'STR / Comp Set Report', match: ['STR', 'STR_TREND'] },
  { id: 'insurance', label: 'Insurance Records', match: ['INSURANCE'] },
  { id: 'property_tax', label: 'Property Taxes', match: ['PROPERTY_TAX'] },
  { id: 'room_mix', label: 'Room Mix / Unit Mix', match: ['ROOM_MIX'] },
  { id: 'capex', label: 'Historical CapEx', match: ['CAPEX'] },
  { id: 'property_info', label: 'Basic Property Info', match: ['PROPERTY_INFO'] },
  { id: 'leases', label: 'Leases & Agreements', match: ['LEASES', 'CONTRACT'] },
  { id: 'surveys', label: 'Surveys & Reviews', match: ['SURVEYS'], optional: true },
  // FON-64 — Debt / Partnership source docs + catch-all (all optional).
  { id: 'debt', label: 'Debt / Loan Docs', match: ['DEBT'], optional: true },
  { id: 'partnership', label: 'Partnership / JV Docs', match: ['PARTNERSHIP'], optional: true },
  { id: 'other', label: 'Other', match: ['OTHER'], optional: true },
];

const REQUIRED_TOTAL = CATEGORIES.filter((c) => !c.optional).length;

// doc_type ⇄ (family, period) for the financial dropdowns.
function familyOf(docType: string): 'T-12' | 'P&L' {
  return docType.toUpperCase() === 'T12' ? 'T-12' : 'P&L';
}
// Full-label period vocabulary (canonical Data Room v2 uses the spelled-out
// labels plus a "Not Sure" escape hatch). "Not Sure" carries no distinct
// backend token — it resolves to a generic annual P&L so the doc still counts.
type Period = 'Annual' | 'Monthly' | 'Year-To-Date' | 'Not Sure';
const PERIOD_OPTIONS: Period[] = ['Annual', 'Monthly', 'Year-To-Date', 'Not Sure'];
function periodOf(docType: string): Period {
  const t = docType.toUpperCase();
  if (t === 'PNL_MONTHLY') return 'Monthly';
  if (t === 'PNL_YTD') return 'Year-To-Date';
  return 'Annual';
}
function composeDocType(family: 'T-12' | 'P&L', period: Period): string {
  if (family === 'T-12') return 'T12';
  if (period === 'Monthly') return 'PNL_MONTHLY';
  if (period === 'Year-To-Date') return 'PNL_YTD';
  return 'PNL'; // Annual or Not Sure
}

const YEARS: number[] = (() => {
  const now = new Date().getFullYear();
  const out: number[] = [];
  for (let y = now + 1; y >= now - 7; y -= 1) out.push(y);
  return out;
})();

// Normalize a doc_type for matching: uppercase, drop separators. The stored
// data sometimes carries non-canonical spellings ("PNLMONTHLY" for
// "PNL_MONTHLY", "T 12" for "T12"); normalizing both sides means those still
// land in the right category instead of vanishing from coverage.
function normToken(t: string | null | undefined): string {
  return (t ?? '').toUpperCase().replace(/[-_ ]/g, '');
}

// Full doc-type picker for the "Needs classification" bucket, so a mis-tagged
// file (e.g. an OM stored as "EXTRACTOR") can be set to the right type.
const DOC_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'OM', label: 'Offering Memorandum' },
  { value: 'T12', label: 'T-12' },
  { value: 'PNL', label: 'Annual P&L' },
  { value: 'PNL_MONTHLY', label: 'Monthly P&L' },
  { value: 'PNL_YTD', label: 'YTD P&L' },
  { value: 'STR_TREND', label: 'STR / Comp Set' },
  { value: 'INSURANCE', label: 'Insurance Records' },
  { value: 'PROPERTY_TAX', label: 'Property Taxes' },
  { value: 'ROOM_MIX', label: 'Room Mix / Unit Mix' },
  { value: 'CAPEX', label: 'Historical CapEx' },
  { value: 'PROPERTY_INFO', label: 'Basic Property Info' },
  { value: 'LEASES', label: 'Leases & Agreements' },
  { value: 'SURVEYS', label: 'Surveys & Reviews' },
  // FON-64 — Debt / Partnership source docs + catch-all.
  { value: 'DEBT', label: 'Debt / Loan Docs' },
  { value: 'PARTNERSHIP', label: 'Partnership / JV Docs' },
  { value: 'OTHER', label: 'Other' },
];

export function DocumentCoverage({
  files,
  onReclassify,
  onOpenDoc,
  onOpenInNewTab,
  onDownload,
  busyDocId,
  className,
}: DocumentCoverageProps) {
  const byCategory = new Map<string, CoverageFile[]>();
  for (const c of CATEGORIES) byCategory.set(c.id, []);
  const unclassified: CoverageFile[] = [];
  for (const f of files) {
    const t = normToken(f.docType);
    const cat = t
      ? CATEGORIES.find((c) => c.match.some((m) => normToken(m) === t))
      : undefined;
    if (cat) byCategory.get(cat.id)!.push(f);
    else unclassified.push(f);
  }

  // The canonical Data Room v2 header counts against the 10 core diligence
  // types (the required checklist) — the first 10 CATEGORIES; Debt / Partnership
  // / Other are extra optional buckets that don't move the "of 10" number.
  const CORE_TOTAL = 10;
  const coreCovered = CATEGORIES.slice(0, CORE_TOTAL).filter(
    (c) => byCategory.get(c.id)!.length > 0,
  ).length;
  const canRun = (byCategory.get('financials')!.length ?? 0) > 0;
  const pct = Math.round((coreCovered / CORE_TOTAL) * 100);

  // Covered categories are open by default; we track only what the analyst
  // explicitly COLLAPSED. This way async-loaded files show without a click
  // (a lazy expanded-set initializer would miss files that arrive later).
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // Whole-card collapse (the header chevron ▾/▸ in the design).
  const [cardCollapsed, setCardCollapsed] = useState(false);

  // Drag-to-recategorize: the doc id currently being dragged. Dropping it on a
  // category row reclassifies it to that category's representative doc_type
  // (financials default to a generic annual P&L) — the same reclassify endpoint
  // the inline type dropdowns fire.
  const [dragDocId, setDragDocId] = useState<string | null>(null);
  const dropDocInto = (cat: CategorySpec) => {
    if (!dragDocId) return;
    const token = cat.id === 'financials' ? 'PNL' : cat.match[0];
    onReclassify(dragDocId, { doc_type: token });
    setDragDocId(null);
  };

  return (
    <Card className={cn('overflow-hidden', className)} aria-label="Document coverage">
      {/* Header + gate — clicking the header collapses/expands the whole card. */}
      <div className="p-5 border-b border-border">
        <button
          type="button"
          onClick={() => setCardCollapsed((v) => !v)}
          aria-expanded={!cardCollapsed}
          className="w-full flex items-start justify-between gap-3 mb-2 text-left"
        >
          <div className="flex items-center gap-2">
            {cardCollapsed ? (
              <ChevronRight size={15} className="text-ink-400 flex-shrink-0" aria-hidden="true" />
            ) : (
              <ChevronDown size={15} className="text-ink-400 flex-shrink-0" aria-hidden="true" />
            )}
            <span className="w-1.5 h-1.5 rounded-full bg-brand-500" aria-hidden="true" />
            <h3 className="text-[15px] font-semibold text-ink-900">Document coverage</h3>
          </div>
          <div className="text-right">
            <span className="text-[15px] font-semibold tabular-nums text-ink-900">
              {files.length}
            </span>
            <span className="text-[11px] text-ink-500 ml-1">docs</span>
          </div>
        </button>
        <p className="text-[12.5px] text-ink-500 leading-relaxed mb-3">
          {files.length} document{files.length === 1 ? '' : 's'} uploaded across{' '}
          {coreCovered} of {CORE_TOTAL} types
          {canRun ? (
            <> — you have enough to run the model; the rest sharpen the projection.</>
          ) : (
            <> — add a T-12 or P&amp;L to run the model.</>
          )}
        </p>
        <div className="flex items-center gap-2">
          <div className="flex-1 h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
            <div
              className={cn('h-full transition-all', canRun ? 'bg-success-500' : 'bg-brand-500')}
              style={{ width: `${pct}%` }}
              aria-hidden="true"
            />
          </div>
          <span
            className={cn(
              'inline-flex items-center gap-1 text-[11px] font-medium',
              canRun ? 'text-success-700' : 'text-brand-700',
            )}
          >
            {canRun ? <Rocket size={12} /> : <AlertCircle size={12} />}
            {canRun ? 'Ready to run' : 'Needs financials'}
          </span>
        </div>
      </div>

      {/* Category rows */}
      {!cardCollapsed && (
      <ul role="list">
        {CATEGORIES.map((cat) => {
          const catFiles = byCategory.get(cat.id)!;
          const covered = catFiles.length > 0;
          const isOpen = covered && !collapsed.has(cat.id);
          const dropActive = dragDocId != null;
          return (
            <li
              key={cat.id}
              className={cn(
                'border-b border-border last:border-0 transition-[outline] outline-offset-[-2px]',
                dropActive && 'outline-dashed outline-2 outline-brand-500/50',
              )}
              onDragOver={(e) => {
                if (dragDocId) e.preventDefault();
              }}
              onDrop={(e) => {
                e.preventDefault();
                dropDocInto(cat);
              }}
            >
              <button
                type="button"
                onClick={() => covered && toggle(cat.id)}
                disabled={!covered}
                className={cn(
                  'w-full flex items-center gap-3 px-5 py-3 text-left transition-colors',
                  covered ? 'hover:bg-ink-300/10 cursor-pointer' : 'cursor-default',
                )}
                aria-expanded={covered ? isOpen : undefined}
              >
                {covered ? (
                  <CheckCircle2 size={16} className="text-success-500 flex-shrink-0" />
                ) : (
                  <Circle size={16} className="text-ink-300 flex-shrink-0" />
                )}
                <span
                  className={cn(
                    'text-[13.5px] flex-1',
                    covered ? 'font-medium text-ink-900' : 'text-ink-500',
                  )}
                >
                  {cat.label}
                  {cat.optional && (
                    <span className="text-[10.5px] text-ink-400 ml-1.5">optional</span>
                  )}
                </span>
                {covered ? (
                  <>
                    <Badge tone="green" className="text-[10px]">
                      {catFiles.length} file{catFiles.length === 1 ? '' : 's'}
                    </Badge>
                    <ChevronRight
                      size={15}
                      className={cn(
                        'text-ink-400 transition-transform',
                        isOpen && 'rotate-90',
                      )}
                      aria-hidden="true"
                    />
                  </>
                ) : (
                  <span className="text-[10.5px] text-ink-400 bg-ink-300/15 rounded px-2 py-0.5">
                    Not uploaded
                  </span>
                )}
              </button>

              {covered && isOpen && (
                <ul role="list" className="bg-ink-300/5">
                  {catFiles.map((f) => (
                    <CoverageFileRow
                      key={f.id}
                      file={f}
                      financial={!!cat.financial}
                      busy={busyDocId === f.id}
                      dragging={dragDocId === f.id}
                      onDragStart={() => setDragDocId(f.id)}
                      onDragEnd={() => setDragDocId(null)}
                      onReclassify={onReclassify}
                      onOpenDoc={onOpenDoc}
                      onOpenInNewTab={onOpenInNewTab}
                      onDownload={onDownload}
                    />
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      )}

      {/* Mis-tagged files (doc_type matched no category — e.g. an OM stored
          as "EXTRACTOR"). Surfaced so they don't vanish from coverage; set
          the right type to make them count. */}
      {!cardCollapsed && unclassified.length > 0 && (
        <div className="border-t border-border">
          <div className="flex items-center gap-2 px-5 py-2.5 bg-warn-50/50">
            <AlertCircle size={15} className="text-warn-700 flex-shrink-0" />
            <span className="text-[13px] font-medium text-ink-900">Needs classification</span>
            <span className="text-[11px] text-ink-500">
              {unclassified.length} file{unclassified.length === 1 ? '' : 's'} Fondok
              couldn&rsquo;t categorize — set the type so they count toward coverage.
            </span>
          </div>
          <ul role="list" className="bg-warn-50/20">
            {unclassified.map((f) => (
              <UnclassifiedRow
                key={f.id}
                file={f}
                busy={busyDocId === f.id}
                onReclassify={onReclassify}
                onOpenDoc={onOpenDoc}
                onOpenInNewTab={onOpenInNewTab}
                onDownload={onDownload}
              />
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

// The per-row "Open in new tab (↗)" + "Download (⬇)" affordances from the
// canonical Data Room v2 doc rows. Rendered only when the host wires handlers.
function FileActions({
  docId,
  name,
  onOpenInNewTab,
  onDownload,
}: {
  docId: string;
  name: string;
  onOpenInNewTab?: (docId: string) => void;
  onDownload?: (docId: string) => void;
}) {
  if (!onOpenInNewTab && !onDownload) return null;
  return (
    <span className="inline-flex items-center gap-1.5 flex-shrink-0">
      {onOpenInNewTab && (
        <button
          type="button"
          onClick={() => onOpenInNewTab(docId)}
          title="Open in new tab"
          aria-label={`Open ${name} in a new tab`}
          className="text-ink-500 hover:text-ink-900 transition-colors"
        >
          <ExternalLink size={13} aria-hidden="true" />
        </button>
      )}
      {onDownload && (
        <button
          type="button"
          onClick={() => onDownload(docId)}
          title="Download"
          aria-label={`Download ${name}`}
          className="text-ink-500 hover:text-ink-900 transition-colors"
        >
          <Download size={13} aria-hidden="true" />
        </button>
      )}
    </span>
  );
}

function UnclassifiedRow({
  file,
  busy,
  onReclassify,
  onOpenDoc,
  onOpenInNewTab,
  onDownload,
}: {
  file: CoverageFile;
  busy: boolean;
  onReclassify: DocumentCoverageProps['onReclassify'];
  onOpenDoc: DocumentCoverageProps['onOpenDoc'];
  onOpenInNewTab?: (docId: string) => void;
  onDownload?: (docId: string) => void;
}) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3 pl-12 border-t border-border/60">
      <FileText size={14} className="text-ink-500 flex-shrink-0" aria-hidden="true" />
      <span
        className="text-[12.5px] text-ink-900 font-medium truncate max-w-[220px]"
        title={file.name}
      >
        {file.name}
      </span>
      <FileActions
        docId={file.id}
        name={file.name}
        onOpenInNewTab={onOpenInNewTab}
        onDownload={onDownload}
      />
      {file.docType && (
        <span
          className="text-[10px] text-ink-500"
          title="Current (unrecognized) type"
        >
          {file.docType}
        </span>
      )}
      <select
        aria-label={`Set document type for ${file.name}`}
        className="text-[11px] rounded border border-warn-500/40 bg-card px-1.5 py-0.5 text-ink-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-50"
        defaultValue=""
        disabled={busy}
        onChange={(e) => {
          if (e.target.value) onReclassify(file.id, { doc_type: e.target.value });
        }}
      >
        <option value="" disabled>
          Set type…
        </option>
        {DOC_TYPE_OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <div className="ml-auto flex items-center gap-3 text-[11px] tabular-nums">
        <Badge tone={docStatusState(file).tone} className="text-[10px]">
          {docStatusState(file).label}
        </Badge>
        {file.fields > 0 && (
          <span className="text-ink-500">
            {file.fields} field{file.fields === 1 ? '' : 's'}
          </span>
        )}
        <button
          type="button"
          onClick={() => onOpenDoc(file.id)}
          className="text-[10.5px] font-medium px-2.5 py-1 rounded border border-border text-ink-700 hover:bg-ink-100"
        >
          View
        </button>
      </div>
    </li>
  );
}

function CoverageFileRow({
  file,
  financial,
  busy,
  dragging,
  onDragStart,
  onDragEnd,
  onReclassify,
  onOpenDoc,
  onOpenInNewTab,
  onDownload,
}: {
  file: CoverageFile;
  financial: boolean;
  busy: boolean;
  dragging?: boolean;
  onDragStart?: () => void;
  onDragEnd?: () => void;
  onReclassify: DocumentCoverageProps['onReclassify'];
  onOpenDoc: DocumentCoverageProps['onOpenDoc'];
  onOpenInNewTab?: (docId: string) => void;
  onDownload?: (docId: string) => void;
}) {
  const family = familyOf(file.docType);
  const period = periodOf(file.docType);
  const state = docStatusState(file);
  const confTone =
    file.confidence >= 95 ? 'text-success-700' : file.confidence >= 85 ? 'text-warn-700' : 'text-danger-700';

  const selectCls =
    'text-[11px] rounded border border-border bg-card px-1.5 py-0.5 text-ink-900 ' +
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-50';

  return (
    <li
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={cn(
        'flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3 pl-12 border-t border-border/60 cursor-grab',
        dragging && 'opacity-50',
      )}
    >
      <GripVertical
        size={14}
        className="text-ink-400 flex-shrink-0 -ml-6"
        aria-label="Drag to move to another category"
      />
      <FileText size={14} className="text-ink-500 flex-shrink-0" aria-hidden="true" />
      <span className="text-[12.5px] text-ink-900 font-medium truncate max-w-[220px]" title={file.name}>
        {file.name}
      </span>
      <FileActions
        docId={file.id}
        name={file.name}
        onOpenInNewTab={onOpenInNewTab}
        onDownload={onDownload}
      />

      {financial ? (
        <div className="flex items-center gap-1.5 flex-wrap">
          {/* FON-64 — a financial doc can be re-typed to ANY type (e.g. a T-12
              mistagged over a loan doc → Debt); family/period stay as additive
              controls while the type remains financial. */}
          <select
            aria-label={`Document type for ${file.name}`}
            className={selectCls}
            value={file.docType}
            disabled={busy}
            onChange={(e) => {
              if (e.target.value && e.target.value !== file.docType) {
                onReclassify(file.id, { doc_type: e.target.value });
              }
            }}
          >
            {DOC_TYPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <select
            aria-label={`Statement family for ${file.name}`}
            className={selectCls}
            value={family}
            disabled={busy}
            onChange={(e) => {
              const fam = e.target.value as 'T-12' | 'P&L';
              onReclassify(file.id, { doc_type: composeDocType(fam, period) });
            }}
          >
            <option value="T-12">T-12</option>
            <option value="P&L">P&amp;L</option>
          </select>
          <select
            aria-label={`Period for ${file.name}`}
            className={selectCls}
            value={period}
            disabled={busy || family === 'T-12'}
            onChange={(e) => {
              const per = e.target.value as Period;
              onReclassify(file.id, { doc_type: composeDocType(family, per) });
            }}
          >
            {PERIOD_OPTIONS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <select
            aria-label={`Year for ${file.name}`}
            className={selectCls}
            value={file.fiscalYear ?? ''}
            disabled={busy}
            onChange={(e) => {
              const y = parseInt(e.target.value, 10);
              if (!Number.isNaN(y)) onReclassify(file.id, { fiscal_year: y });
            }}
          >
            <option value="">Year</option>
            {YEARS.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </div>
      ) : (
        // FON-58 — any classified document can be re-typed inline (e.g. an OM
        // mis-tagged as a comp set → STR / Comp Set). Extracted data is kept;
        // the reclassify endpoint just re-buckets it.
        <select
          aria-label={`Document type for ${file.name}`}
          className={selectCls}
          value={file.docType}
          disabled={busy}
          onChange={(e) => {
            if (e.target.value && e.target.value !== file.docType) {
              onReclassify(file.id, { doc_type: e.target.value });
            }
          }}
        >
          {DOC_TYPE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}

      <div className="ml-auto flex items-center gap-3 text-[11px] tabular-nums">
        <Badge tone={state.tone} className="text-[10px]">
          {state.label}
        </Badge>
        {file.fields > 0 && (
          <span className="text-ink-500">
            {file.fields} field{file.fields === 1 ? '' : 's'}
          </span>
        )}
        {file.confidence > 0 && (
          <span className={confTone}>{file.confidence}% confidence</span>
        )}
        {file.toReview > 0 && (
          <span className="inline-flex items-center gap-1 text-danger-700">
            <AlertCircle size={11} /> {file.toReview} to review
          </span>
        )}
        <button
          type="button"
          onClick={() => onOpenDoc(file.id, financial)}
          className="text-[10.5px] font-medium px-2.5 py-1 rounded bg-brand-600 text-white hover:bg-brand-700"
        >
          {financial ? 'View Financials' : 'View data'}
        </button>
      </div>
    </li>
  );
}
