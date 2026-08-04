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
  FileText,
  Rocket,
  AlertCircle,
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
];

const REQUIRED_TOTAL = CATEGORIES.filter((c) => !c.optional).length;

// doc_type ⇄ (family, period) for the financial dropdowns.
function familyOf(docType: string): 'T-12' | 'P&L' {
  return docType.toUpperCase() === 'T12' ? 'T-12' : 'P&L';
}
function periodOf(docType: string): 'Annual' | 'Monthly' | 'YTD' {
  const t = docType.toUpperCase();
  if (t === 'PNL_MONTHLY') return 'Monthly';
  if (t === 'PNL_YTD') return 'YTD';
  return 'Annual';
}
function composeDocType(
  family: 'T-12' | 'P&L',
  period: 'Annual' | 'Monthly' | 'YTD',
): string {
  if (family === 'T-12') return 'T12';
  if (period === 'Monthly') return 'PNL_MONTHLY';
  if (period === 'YTD') return 'PNL_YTD';
  return 'PNL';
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
];

export function DocumentCoverage({
  files,
  onReclassify,
  onOpenDoc,
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

  const coveredRequired = CATEGORIES.filter(
    (c) => !c.optional && (byCategory.get(c.id)!.length > 0),
  ).length;
  const typesCovered = CATEGORIES.filter(
    (c) => byCategory.get(c.id)!.length > 0,
  ).length;
  const canRun = (byCategory.get('financials')!.length ?? 0) > 0;
  const pct = Math.round((coveredRequired / REQUIRED_TOTAL) * 100);

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

  return (
    <Card className={cn('overflow-hidden', className)} aria-label="Document coverage">
      {/* Header + gate */}
      <div className="p-5 border-b border-border">
        <div className="flex items-start justify-between gap-3 mb-2">
          <div className="flex items-center gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-brand-500" aria-hidden="true" />
            <h3 className="text-[15px] font-semibold text-ink-900">Document coverage</h3>
          </div>
          <div className="text-right">
            <span className="text-[15px] font-semibold tabular-nums text-ink-900">
              {files.length}
            </span>
            <span className="text-[11px] text-ink-500 ml-1">docs</span>
          </div>
        </div>
        <p className="text-[12.5px] text-ink-500 leading-relaxed mb-3">
          {files.length} document{files.length === 1 ? '' : 's'} uploaded across{' '}
          {typesCovered} of {REQUIRED_TOTAL} types
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
      <ul role="list">
        {CATEGORIES.map((cat) => {
          const catFiles = byCategory.get(cat.id)!;
          const covered = catFiles.length > 0;
          const isOpen = covered && !collapsed.has(cat.id);
          return (
            <li key={cat.id} className="border-b border-border last:border-0">
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
                      onReclassify={onReclassify}
                      onOpenDoc={onOpenDoc}
                    />
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>

      {/* Mis-tagged files (doc_type matched no category — e.g. an OM stored
          as "EXTRACTOR"). Surfaced so they don't vanish from coverage; set
          the right type to make them count. */}
      {unclassified.length > 0 && (
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
              />
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

function UnclassifiedRow({
  file,
  busy,
  onReclassify,
  onOpenDoc,
}: {
  file: CoverageFile;
  busy: boolean;
  onReclassify: DocumentCoverageProps['onReclassify'];
  onOpenDoc: DocumentCoverageProps['onOpenDoc'];
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
        <span className="text-ink-500">
          {file.fields} field{file.fields === 1 ? '' : 's'}
        </span>
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
  onReclassify,
  onOpenDoc,
}: {
  file: CoverageFile;
  financial: boolean;
  busy: boolean;
  onReclassify: DocumentCoverageProps['onReclassify'];
  onOpenDoc: DocumentCoverageProps['onOpenDoc'];
}) {
  const family = familyOf(file.docType);
  const period = periodOf(file.docType);
  const confTone =
    file.confidence >= 95 ? 'text-success-700' : file.confidence >= 85 ? 'text-warn-700' : 'text-danger-700';

  const selectCls =
    'text-[11px] rounded border border-border bg-card px-1.5 py-0.5 text-ink-900 ' +
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 disabled:opacity-50';

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-5 py-3 pl-12 border-t border-border/60">
      <FileText size={14} className="text-ink-500 flex-shrink-0" aria-hidden="true" />
      <span className="text-[12.5px] text-ink-900 font-medium truncate max-w-[220px]" title={file.name}>
        {file.name}
      </span>

      {financial ? (
        <div className="flex items-center gap-1.5">
          <select
            aria-label={`Document type for ${file.name}`}
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
              const per = e.target.value as 'Annual' | 'Monthly' | 'YTD';
              onReclassify(file.id, { doc_type: composeDocType(family, per) });
            }}
          >
            <option value="Annual">Annual</option>
            <option value="Monthly">Monthly</option>
            <option value="YTD">YTD</option>
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
        <Badge tone="gray" className="text-[10px]">
          {file.docType}
        </Badge>
      )}

      <div className="ml-auto flex items-center gap-3 text-[11px] tabular-nums">
        <span className="text-ink-500">
          {file.fields} field{file.fields === 1 ? '' : 's'}
        </span>
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
          {financial ? 'View in Financials' : 'View data'}
        </button>
      </div>
    </li>
  );
}
