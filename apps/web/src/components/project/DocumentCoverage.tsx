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
 * CapEx files carry a [Historic / Future] select (FON-41 / R-036 doc_subtype).
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
  Loader2,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';
import { useNow } from '@/lib/hooks/useNow';
import {
  describeDocYear,
  docStageLabel,
  elapsedSince,
  formatElapsed,
  isProcessingStatus,
} from '@/lib/progress';
import { DOC_SUBTYPES_BY_DOC_TYPE, effectiveDocSubtype, type DocSubtype } from '@/lib/docSubtype';

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
  /** FON-41 testers (E-002 / R-041): the one-line, data-derived reason the
   *  document is "Review Recommended" — built by lib/reviewReasons from the
   *  SAME flagged list `toReview` counts. Null / absent when nothing is
   *  flagged (no line is rendered). */
  reviewReason?: string | null;
  fiscalYear: number | null;
  /** FON-41 / R-036 — the worker's ``doc_subtype`` (CAPEX: ``future`` |
   *  ``historic``). Null / absent groups a CAPEX file under Historic CapEx. */
  docSubtype?: string | null;
  /** Upstream doc status (UPLOADED / EXTRACTED / FAILED / …) — drives the
   *  processing-state badge (FON-40). */
  status?: string;
}

/** E-001 / R-032 — per-document provenance the host passes alongside the
 *  file list (keyed by document id): when it was uploaded, so an in-flight
 *  row can show a measured elapsed timer, and the year the Extractor read
 *  vs the analyst's tag. */
export interface CoverageDocMeta {
  /** ISO ``uploaded_at`` from the worker row. */
  uploadedAt?: string | null;
  /** Year the Extractor read off the statement's period ending. */
  extractedPeriodYear?: number | null;
  /** Worker's unresolved analyst-vs-detected year disagreement flag. */
  yearMismatch?: boolean;
  /** E-004 — whether this document's extraction record has been fetched
   *  yet. ``useDocuments`` lazy-loads extractions one per doc after the
   *  list says EXTRACTED, so for a moment a row is EXTRACTED with no fields
   *  and no review count; ``false`` holds the row in "Loading fields…"
   *  instead of flashing "Ready for Review" and then flipping. Omit (or
   *  ``true``) when the host has no lazy extraction step. */
  extractionLoaded?: boolean;
  /** R-034 — the analyst's upload-time tag (``user_provided_doc_type``).
   *  Empty when they left the report type on "Not sure" (or uploaded
   *  straight into the Data Room). */
  userProvidedDocType?: string | null;
  /** R-034 — the Router's proposal, when it differs from the stored type. */
  aiProposedDocType?: string | null;
  /** R-034 — the extraction's ``confidence_report.coverage_note``; the STR
   *  template leads it with ``variant=…`` (monthly / weekly / daily STAR,
   *  legacy Custom Trend). */
  coverageNote?: string | null;
}

// R-034 — the STR report type Fondok detected, read ONLY from what the worker
// returned for the document: the STR template's ``variant=`` in the
// extraction coverage note first (the most specific read), then the Router's
// proposal, then the stored doc_type. Null when none of them name an STR
// report (no browser-side guessing from the filename).
const STR_VARIANT_LABEL: Record<string, string> = {
  monthly_star_xlsx: 'STR Trend (TTM) · monthly STAR',
  weekly_star_xlsx: 'STR Star (Weekly)',
  daily_star_xlsx: 'STR Star (Daily)',
  custom_trend_xls: 'STR Trend · Custom Trend',
};
const STR_DOC_TYPE_LABEL: Record<string, string> = {
  STR_TREND: 'STR Trend (TTM)',
  STR: 'STR Star (Daily)',
};
export function detectedStrReportType(
  docType: string | null | undefined,
  meta?: Pick<CoverageDocMeta, 'aiProposedDocType' | 'coverageNote'>,
): string | null {
  const variant = /variant=([a-z_]+)/.exec(meta?.coverageNote ?? '')?.[1];
  if (variant && STR_VARIANT_LABEL[variant]) return STR_VARIANT_LABEL[variant];
  for (const t of [meta?.aiProposedDocType, docType]) {
    const label = STR_DOC_TYPE_LABEL[(t ?? '').toUpperCase().trim()];
    if (label) return label;
  }
  return null;
}

// FON-40 — a single processing state per document, so a parsing file reads
// "Processing" rather than "0 fields", and a done file tells the user whether
// review is recommended.
function docStatusState(
  file: CoverageFile,
  meta?: CoverageDocMeta,
): { label: string; tone: 'gray' | 'blue' | 'amber' | 'green' | 'red' } {
  const s = (file.status ?? '').toUpperCase();
  if (s === 'FAILED' || s === 'PARSE_FAILED') return { label: 'Processing Failed', tone: 'red' };
  if (s === 'UPLOADING') return { label: 'Uploading', tone: 'gray' };
  // E-004 — never render a review verdict before the extraction is here.
  if (s === 'EXTRACTED' && meta?.extractionLoaded === false) {
    return { label: 'Loading fields…', tone: 'gray' };
  }
  const extracted = s === 'EXTRACTED' || file.fields > 0;
  // E-001 — name the pipeline stage (Uploaded → Parsing → Classifying →
  // Extracting) instead of a flat "Processing", so the analyst can see
  // which files are still moving and where each one is.
  if (!extracted) return { label: docStageLabel(s) ?? 'Processing', tone: 'blue' };
  if (file.toReview > 0) return { label: 'Review Recommended', tone: 'amber' };
  return { label: 'Ready for Review', tone: 'green' };
}

/** What a reclassify can change — ``doc_subtype`` omitted = unchanged. */
export type ReclassifyBody = {
  doc_type?: string;
  fiscal_year?: number;
  doc_subtype?: DocSubtype | null;
};

export interface DocumentCoverageProps {
  files: CoverageFile[];
  /** Reclassify a financial doc's type / year (fires the PATCH endpoint). */
  onReclassify: (docId: string, body: ReclassifyBody) => void;
  /** Open a file's extracted-data / review panel. */
  onOpenDoc: (docId: string, financial?: boolean) => void;
  /** Open the document's field review focused on its flagged fields — the
   *  reason line's click-through (FON-41 E-002). Falls back to `onOpenDoc`. */
  onOpenReview?: (docId: string) => void;
  /** Open the raw uploaded file in a new browser tab (↗). */
  onOpenInNewTab?: (docId: string) => void;
  /** Download the raw uploaded file (⬇). */
  onDownload?: (docId: string) => void;
  /** Doc id whose reclassify is in flight (disables its controls). */
  busyDocId?: string | null;
  /** Per-document provenance (upload time, detected year) keyed by id. */
  docMeta?: Record<string, CoverageDocMeta>;
  className?: string;
}

type CategorySpec = {
  id: string;
  label: string;
  /** doc_type tokens that count toward this category. */
  match: string[];
  /** FON-41 / R-036 — only files whose effective ``doc_subtype`` equals this
   *  count (CAPEX: Historic vs Future). A drop onto the row sets it too. */
  subtype?: DocSubtype;
  optional?: boolean;
  financial?: boolean;
  /** Token a file dragged onto this row is reclassified to (defaults to
   *  ``match[0]``; financials use a generic annual P&L). */
  dropAs?: string;
  /** Shown instead of "Not uploaded" (e.g. a row that can't be filled by
   *  doc_type alone). */
  note?: string;
};

// Mirrors the wizard slots (DocumentsStep WIZARD_CATEGORIES — FON-41 decision
// 5: same labels, same order), with T-12 + P&L collapsed into one "Financial
// Statements" row (FON-18). Order = the Data Room list. Rows bucket by stored
// doc_type, so:
//  - Comp Set / Market Reports holds MARKET_STUDY — the Router's lane for
//    CoStar / market files (the wizard slot uploads them as STR_TREND).
//  - Historic CapEx and Future CapEx both hold CAPEX files, split by the
//    worker's ``doc_subtype`` (FON-41 / R-036): ``future`` → Future CapEx,
//    ``historic`` or unstated (legacy uploads, bulk drop) → Historic CapEx.
export const CATEGORIES: CategorySpec[] = [
  { id: 'om', label: 'Offering Memorandum', match: ['OM'] },
  { id: 'room_mix', label: 'Hotel Program', match: ['ROOM_MIX'] },
  {
    id: 'financials',
    label: 'Financial Statements',
    match: ['T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD', 'PNL_BENCHMARK'],
    financial: true,
    dropAs: 'PNL',
  },
  { id: 'str', label: 'STR Reports', match: ['STR', 'STR_TREND'] },
  { id: 'comp_set', label: 'Comp Set / Market Reports', match: ['MARKET_STUDY'] },
  { id: 'capex', label: 'Historic CapEx', match: ['CAPEX'], subtype: 'historic' },
  { id: 'insurance', label: 'Insurance Records', match: ['INSURANCE'] },
  { id: 'property_tax', label: 'Property Taxes', match: ['PROPERTY_TAX'] },
  { id: 'future_capex', label: 'Future CapEx', match: ['CAPEX'], subtype: 'future' },
  { id: 'property_info', label: 'Other Property Info', match: ['PROPERTY_INFO'] },
  { id: 'leases', label: 'Leases & Agreements', match: ['LEASES', 'CONTRACT'] },
  // R-031 — Due Diligence is a regular row (no muted "optional" tag).
  { id: 'surveys', label: 'Due Diligence', match: ['SURVEYS'] },
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
  { value: 'ROOM_MIX', label: 'Hotel Program' },
  // R-034 — the STR row's type select doubles as the report-type sub-select.
  { value: 'STR_TREND', label: 'STR Trend (TTM)' },
  { value: 'STR', label: 'STR Star (Daily)' },
  { value: 'MARKET_STUDY', label: 'Comp Set / Market Reports' },
  { value: 'CAPEX', label: 'CapEx' },  // Historic / Future via the subtype select
  { value: 'INSURANCE', label: 'Insurance Records' },
  { value: 'PROPERTY_TAX', label: 'Property Taxes' },
  { value: 'PROPERTY_INFO', label: 'Other Property Info' },
  { value: 'LEASES', label: 'Leases & Agreements' },
  { value: 'SURVEYS', label: 'Due Diligence' },
  // FON-64 — Debt / Partnership source docs + catch-all.
  { value: 'DEBT', label: 'Debt / Loan Docs' },
  { value: 'PARTNERSHIP', label: 'Partnership / JV Docs' },
  { value: 'OTHER', label: 'Other' },
];

const SUBTYPE_LABEL: Record<DocSubtype, string> = {
  historic: 'Historic',
  future: 'Future',
};

/** The coverage category a file lands in: doc_type match, then — for rows
 *  pinned to a subtype — the file's effective ``doc_subtype``. */
export function categoryForFile(
  file: Pick<CoverageFile, 'docType' | 'docSubtype'>,
): CategorySpec | undefined {
  const t = normToken(file.docType);
  if (!t) return undefined;
  return CATEGORIES.find((c) => {
    const m = c.match.find((x) => normToken(x) === t);
    if (!m) return false;
    return !c.subtype || effectiveDocSubtype(m, file.docSubtype) === c.subtype;
  });
}

export function DocumentCoverage({
  files,
  onReclassify,
  onOpenDoc,
  onOpenReview,
  onOpenInNewTab,
  onDownload,
  busyDocId,
  docMeta,
  className,
}: DocumentCoverageProps) {
  // E-001 — one 1 Hz ticker for the whole card, only while something is
  // still in the pipeline; rows read the sampled ``now`` to render their
  // elapsed-since-upload timer. Idle cards never tick.
  const anyProcessing = files.some((f) => isProcessingStatus(f.status));
  const now = useNow(anyProcessing);
  const byCategory = new Map<string, CoverageFile[]>();
  for (const c of CATEGORIES) byCategory.set(c.id, []);
  const unclassified: CoverageFile[] = [];
  for (const f of files) {
    const cat = categoryForFile(f);
    if (cat) byCategory.get(cat.id)!.push(f);
    else unclassified.push(f);
  }

  // The header counts against the core diligence types — every non-optional
  // row (Future CapEx counts since FON-41 gave it its own doc_subtype). Debt /
  // Partnership / Other are extra buckets that don't move the "of N" number.
  const coreCats = CATEGORIES.filter((c) => !c.optional);
  const CORE_TOTAL = coreCats.length;
  const coreCovered = coreCats.filter(
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
    const token = cat.dropAs ?? cat.match[0];
    if (token) {
      onReclassify(
        dragDocId,
        cat.subtype ? { doc_type: token, doc_subtype: cat.subtype } : { doc_type: token },
      );
    }
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
              data-category={cat.id}
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
                  {cat.optional && !cat.note && (
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
                    {cat.note ?? 'Not uploaded'}
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
                      strReport={cat.id === 'str'}
                      busy={busyDocId === f.id}
                      dragging={dragDocId === f.id}
                      onDragStart={() => setDragDocId(f.id)}
                      onDragEnd={() => setDragDocId(null)}
                      meta={docMeta?.[f.id]}
                      now={now}
                      onReclassify={onReclassify}
                      onOpenDoc={onOpenDoc}
                      onOpenReview={onOpenReview}
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
                meta={docMeta?.[f.id]}
                now={now}
                onReclassify={onReclassify}
                onOpenDoc={onOpenDoc}
                onOpenReview={onOpenReview}
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

// FON-41 testers (E-002 / R-041 / R-042): "Review Recommended" beside 97%
// overall confidence never said WHY. The reason is built upstream
// (lib/reviewReasons) from the SAME flagged list `toReview` counts — never a
// second heuristic — and clicking it lands on those fields in the document's
// field review. Renders nothing when the document has no flagged field.
function ReviewReasonLine({
  file,
  onOpen,
}: {
  file: CoverageFile;
  onOpen: (docId: string) => void;
}) {
  if (!(file.toReview > 0) || !file.reviewReason) return null;
  return (
    <button
      type="button"
      onClick={() => onOpen(file.id)}
      data-testid="review-reason"
      title={`${file.reviewReason} — open these fields`}
      className="basis-full text-left text-[11px] leading-snug text-warn-700 hover:underline underline-offset-2 truncate"
    >
      {file.reviewReason}
    </button>
  );
}

function UnclassifiedRow({
  file,
  busy,
  meta,
  now,
  onReclassify,
  onOpenDoc,
  onOpenReview,
  onOpenInNewTab,
  onDownload,
}: {
  file: CoverageFile;
  busy: boolean;
  meta?: CoverageDocMeta;
  now: number;
  onReclassify: DocumentCoverageProps['onReclassify'];
  onOpenDoc: DocumentCoverageProps['onOpenDoc'];
  onOpenReview?: DocumentCoverageProps['onOpenReview'];
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
        <StageBadge file={file} meta={meta} now={now} />
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
      <ReviewReasonLine
        file={file}
        onOpen={(id) => (onOpenReview ? onOpenReview(id) : onOpenDoc(id))}
      />
    </li>
  );
}

function CoverageFileRow({
  file,
  financial,
  strReport = false,
  busy,
  dragging,
  onDragStart,
  onDragEnd,
  onReclassify,
  onOpenDoc,
  onOpenReview,
  onOpenInNewTab,
  onDownload,
  meta,
  now,
}: {
  file: CoverageFile;
  financial: boolean;
  /** R-034 — STR Reports row: show the detected report type when the
   *  analyst left it on "Not sure". */
  strReport?: boolean;
  busy: boolean;
  meta?: CoverageDocMeta;
  now: number;
  dragging?: boolean;
  onDragStart?: () => void;
  onDragEnd?: () => void;
  onReclassify: DocumentCoverageProps['onReclassify'];
  onOpenDoc: DocumentCoverageProps['onOpenDoc'];
  onOpenReview?: DocumentCoverageProps['onOpenReview'];
  onOpenInNewTab?: (docId: string) => void;
  onDownload?: (docId: string) => void;
}) {
  const family = familyOf(file.docType);
  const period = periodOf(file.docType);
  // R-032 — the year shown is the one the Extractor read from the
  // statement; the analyst's tag only takes over when nothing was detected
  // or they resolved a mismatch in their favour. A differing tag is shown
  // beside it, never silently dropped — and never a default.
  const yearView = describeDocYear({
    fiscalYear: file.fiscalYear,
    extractedPeriodYear: meta?.extractedPeriodYear,
    yearMismatch: meta?.yearMismatch,
  });
  // Keep a detected year selectable even when it falls outside the default
  // window — the dropdown must never blank a year that came from the document.
  const yearOptions =
    yearView.year != null && !YEARS.includes(yearView.year)
      ? [yearView.year, ...YEARS].sort((a, b) => b - a)
      : YEARS;
  // R-034 — only when the analyst didn't pick a report type themselves.
  const detectedReport =
    strReport && !(meta?.userProvidedDocType ?? '').trim()
      ? detectedStrReportType(file.docType, meta)
      : null;
  // FON-41 / R-036 — doc_types that split by subtype (CAPEX) get a second
  // select; ``subtype`` is the effective one (unstated CAPEX → Historic).
  const subtypeOptions = DOC_SUBTYPES_BY_DOC_TYPE[(file.docType ?? '').toUpperCase().trim()];
  const subtype = effectiveDocSubtype(file.docType, file.docSubtype);
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
            title={yearView.label ?? undefined}
            className={selectCls}
            value={yearView.year ?? ''}
            disabled={busy}
            onChange={(e) => {
              const y = parseInt(e.target.value, 10);
              if (!Number.isNaN(y)) onReclassify(file.id, { fiscal_year: y });
            }}
          >
            <option value="">Year</option>
            {yearOptions.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
          {yearView.note && (
            // "FY 2024 (you said 2025)" — the detected year is the row's
            // year; the analyst's differing wizard tag stays visible beside it.
            <span
              className="text-[10.5px] text-warn-700 whitespace-nowrap"
              title={yearView.label ?? undefined}
            >
              <span className="sr-only">{yearView.label}</span>
              <span aria-hidden="true">({yearView.note})</span>
            </span>
          )}
        </div>
      ) : (
        // FON-58 — any classified document can be re-typed inline (e.g. an OM
        // mis-tagged as a comp set → STR / Comp Set). Extracted data is kept;
        // the reclassify endpoint just re-buckets it.
        <div className="flex items-center gap-1.5 flex-wrap">
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
          {subtypeOptions && (
            // FON-41 / R-036 — CapEx files move between Historic and Future
            // CapEx by subtype; the doc_type stays CAPEX.
            <select
              aria-label={`CapEx timing for ${file.name}`}
              className={selectCls}
              value={subtype ?? ''}
              disabled={busy}
              onChange={(e) => {
                const next = e.target.value as DocSubtype;
                if (next && next !== subtype) {
                  onReclassify(file.id, { doc_subtype: next });
                }
              }}
            >
              {subtypeOptions.map((o) => (
                <option key={o} value={o}>
                  {SUBTYPE_LABEL[o] ?? o}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
      {detectedReport && (
        <span
          data-testid="detected-report-type"
          className="text-[10.5px] text-ink-500 whitespace-nowrap"
          title="Report type Fondok detected from the file (you left it on “Not sure”)"
        >
          Detected: {detectedReport}
        </span>
      )}

      <div className="ml-auto flex items-center gap-3 text-[11px] tabular-nums">
        <StageBadge file={file} meta={meta} now={now} />
        {file.fields > 0 && (
          <span className="text-ink-500">
            {file.fields} field{file.fields === 1 ? '' : 's'}
          </span>
        )}
        {file.confidence > 0 && (
          <span className={confTone}>{file.confidence}% confidence</span>
        )}
        {file.toReview > 0 && (
          // FON-41 — the count is an action: it lands on the flagged cells
          // themselves (Financials → Historicals pinned to this statement, or
          // the inline field review for non-financial docs).
          <button
            type="button"
            onClick={() => onOpenDoc(file.id, financial)}
            aria-label={`Review ${file.toReview} flagged value${file.toReview === 1 ? '' : 's'} in ${file.name}`}
            title={financial ? 'Open P&L → Historical P&L at this statement’s flagged cells' : 'Open this document’s field review'}
            className="inline-flex items-center gap-1 text-danger-700 hover:underline underline-offset-2"
          >
            <AlertCircle size={11} /> {file.toReview} to review
          </button>
        )}
        <button
          type="button"
          onClick={() => onOpenDoc(file.id, financial)}
          className="text-[10.5px] font-medium px-2.5 py-1 rounded bg-brand-600 text-white hover:bg-brand-700"
        >
          {financial ? 'View P&L' : 'View data'}
        </button>
      </div>
      <ReviewReasonLine
        file={file}
        onOpen={(id) => (onOpenReview ? onOpenReview(id) : onOpenDoc(id, financial))}
      />
    </li>
  );
}

/** E-001 — the per-row status pill. While a document is still in the
 *  pipeline it names the stage AND a live elapsed timer measured from the
 *  worker's ``uploaded_at`` ("Parsing · 4:12"); terminal rows keep the
 *  FON-40 states. No timer when the row isn't processing. */
function StageBadge({
  file,
  meta,
  now,
}: {
  file: CoverageFile;
  meta?: CoverageDocMeta;
  now: number;
}) {
  const state = docStatusState(file, meta);
  const processing = state.tone === 'blue' && isProcessingStatus(file.status);
  // E-004 — EXTRACTED but the extraction record hasn't been fetched yet.
  const loadingFields = state.label === 'Loading fields…';
  const elapsed = processing ? elapsedSince(meta?.uploadedAt, now) : null;
  const title =
    elapsed != null
      ? `${state.label} · ${formatElapsed(elapsed)} since upload`
      : loadingFields
        ? 'Extraction finished — fetching its fields and review count'
        : undefined;
  return (
    <span className="inline-flex" title={title}>
      <Badge tone={state.tone} className={cn('text-[10px]', loadingFields && 'opacity-70')}>
        {(processing || loadingFields) && (
          <Loader2
            size={10}
            className="mr-1 animate-spin inline-block align-[-1px]"
            aria-hidden="true"
          />
        )}
        {state.label}
        {elapsed != null && (
          <span className="ml-1 tabular-nums font-normal opacity-80">
            · {formatElapsed(elapsed)}
          </span>
        )}
      </Badge>
    </span>
  );
}
