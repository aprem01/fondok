'use client';

/**
 * DocumentsStep — Wave 1 expansion (June 2026).
 *
 * Replaces the legacy 4-stage pill row with an 11-category vertical
 * sidebar matching the canonical IC-grade checklist:
 *
 *   1. Offering Memorandum
 *   2. T-12 / Trailing Twelve Months
 *   3. Annual / YTD / Monthly P&L
 *   4. STR / Comp Set Report
 *   5. Insurance Records
 *   6. Property Taxes
 *   7. Room Mix / Unit Mix
 *   8. Historical CapEx
 *   9. Basic Property Info
 *  10. Leases & Agreements
 *  11. Surveys & Reviews (Optional)
 *
 * Locked Wave 1 product decision — ONLY Financials is hard-required to
 * advance Step 3 → Step 4. Financials = T-12 OR Annual / YTD / Monthly
 * P&L (either sub-stage with at least one upload satisfies the gate).
 * The other 9 stages surface red "Missing" pills in the right-rail but
 * never block the wizard. Most deals start without all docs; locking
 * the wizard behind 11 = no one ever finishes.
 *
 * The persistent right-rail DocumentsChecklist lives outside this
 * component — DocumentsStep is the content column. The page wires up
 * both and shares the WizardFile[] state.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  Briefcase,
  Building2,
  Check,
  ClipboardCheck,
  FileSearch,
  FileSpreadsheet,
  FileText,
  Hammer,
  Info,
  Plus,
  Receipt,
  ShieldCheck,
  Trash2,
  UploadCloud,
} from 'lucide-react';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/format';
import type {
  WizardCategory,
  WizardFile,
  WizardUserDocType,
} from '@/lib/api';
import { YearCoverageHint } from './YearCoverageHint';

// ─────────────────────────── allowlist (B3) ───────────────────────────
// Mirrors the worker's _ALLOWED_EXTENSIONS in apps/worker/app/api/documents.py.
// The HTML <input accept=> attribute alone only filters the picker dialog
// (and unreliably across browsers) — the drag-drop handlers filter using
// this set so a misformatted file never sneaks into the staged list.
const ALLOWED_EXTENSIONS = new Set([
  '.pdf',
  '.xls',
  '.xlsx',
  '.xlsm',
  '.csv',
  '.doc',
  '.docx',
]);
const ACCEPT = '.pdf,.xls,.xlsx,.xlsm,.csv,.doc,.docx,application/pdf';

function getExtension(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

function isAllowedFile(file: File): boolean {
  return ALLOWED_EXTENSIONS.has(getExtension(file.name));
}

// ─────────────────────────── category catalog ───────────────────────────

type WizardCategorySpec = {
  id: WizardCategory;
  label: string;
  /** Recommended for IC — false only for SURVEYS. */
  requiredForIc: boolean;
  /** Whether this category accepts multiple files (almost everything does). */
  multiFile: boolean;
  /** Icon shown in the sidebar + content-panel header. */
  Icon: typeof FileText;
  /** One-sentence institutional copy that runs under the panel heading. */
  description: string;
  /** Read on the chip below the heading: "e.g. ..." */
  exampleChip: string;
  /** Optional per-file picker (only relevant for stages with sub-types). */
  picker?: {
    label: string;
    options: { value: WizardUserDocType | ''; label: string; help: string }[];
  };
  /** Optional default doc-type when picker is not shown (e.g. INSURANCE). */
  defaultDocType?: WizardUserDocType | null;
  /** Empty-state copy. */
  emptyState: string;
  /** Hint text on the drop zone. */
  dropHint: string;
  /** Skip-warning copy — surfaced inline below the panel when the user
   *  clicks Skip without any files. */
  skipWarning: string;
  /** Show year tagging on each file row? Only the two financial stages. */
  showYearTagging: boolean;
};

export const WIZARD_CATEGORIES: WizardCategorySpec[] = [
  {
    id: 'om',
    label: 'Offering Memorandum',
    requiredForIc: true,
    multiFile: false,
    Icon: FileText,
    description:
      'Broker pitch deck. Fondok pulls property metadata (keys, brand, year built, address) plus the broker pro forma. Every extracted field is editable downstream.',
    exampleChip: 'e.g. teaser deck, confidential offering memorandum, executive summary',
    defaultDocType: 'OM',
    emptyState:
      'No OM uploaded yet. The broker memorandum is the first read of every deal — it anchors property metadata before extraction.',
    dropHint: 'One file · PDF or Word. Click to browse.',
    skipWarning:
      'Most IC reviewers expect the OM. You can add it later from the Data Room.',
    showYearTagging: false,
  },
  {
    id: 't12',
    label: 'T-12 / Trailing Twelve Months',
    requiredForIc: true,
    multiFile: true,
    Icon: FileSpreadsheet,
    description:
      'Trailing-twelve-month P&L. Drives revenue, departmental expense, and GOP/NOI engines. Tag the year so the historical-variance detector can align it to the prior period.',
    exampleChip: 'e.g. T-12 ending Mar 2026, rolling-twelve P&L',
    defaultDocType: 'T12',
    emptyState:
      'No T-12 staged. A trailing-twelve-month P&L is the single most load-bearing input — every NOI projection grounds against it.',
    dropHint: 'Multiple files welcome · PDF / Excel / CSV.',
    skipWarning:
      'Financials are required to advance — drop either a T-12 here or an Annual / YTD / Monthly P&L in the next stage.',
    showYearTagging: true,
  },
  {
    id: 'historical_pnl',
    label: 'Annual / YTD / Monthly P&L',
    requiredForIc: true,
    multiFile: true,
    Icon: FileSpreadsheet,
    description:
      'Calendar-year actuals, year-to-date rolls, and monthly detail. Use the picker to disambiguate so the engines bucket each file correctly — annuals become baseline years, YTDs become partial rolls, monthlies feed the seasonality model.',
    exampleChip: 'e.g. 2024 annual P&L, May 2025 monthly, YTD through Q1',
    picker: {
      label: 'Period type',
      options: [
        {
          value: 'PNL',
          label: 'Annual',
          help: 'Full-year actuals (Jan-Dec or fiscal year).',
        },
        {
          value: 'PNL_YTD',
          label: 'Year-to-Date',
          help: 'Partial-year roll-up through the most recent close.',
        },
        {
          value: 'PNL_MONTHLY',
          label: 'Monthly',
          help: 'Single month or month-by-month detail.',
        },
        {
          value: '',
          label: 'Not sure',
          help: 'Fondok will classify on extraction.',
        },
      ],
    },
    defaultDocType: null,
    emptyState:
      'No historical P&Ls staged. Two-to-three prior years lets the historical-variance engine flag broker-vs-actual divergence at the line-item level.',
    dropHint: 'Multiple files welcome · PDF / Excel / CSV.',
    skipWarning:
      'Financials are required to advance — drop a T-12 or an Annual / YTD / Monthly P&L to continue.',
    showYearTagging: true,
  },
  {
    id: 'str',
    label: 'STR / Comp Set Report',
    requiredForIc: true,
    multiFile: true,
    Icon: ClipboardCheck,
    description:
      'CoStar STR exports. Trend reports power the comp-set drift detector and feed the Market tab; Star benchmarks anchor RGI / ARI / MPI.',
    exampleChip: 'e.g. STR Trend (TTM), STR Star daily snapshot, comp-set summary',
    picker: {
      label: 'Report type',
      options: [
        {
          value: 'STR_TREND',
          label: 'STR Trend (TTM)',
          help: 'Trailing twelve months across the comp set with penetration indices.',
        },
        {
          value: 'STR',
          label: 'STR Star (Daily)',
          help: 'Single-period STR benchmark snapshot.',
        },
        {
          value: '',
          label: 'Not sure',
          help: 'Fondok will classify on extraction.',
        },
      ],
    },
    defaultDocType: 'STR_TREND',
    emptyState:
      'No STR exports yet. Without a comp set, RevPAR penetration analysis falls back to broad chain-scale benchmarks.',
    dropHint: 'Multiple files welcome · .xls / .xlsx / PDF.',
    skipWarning:
      'Most IC reviewers expect at least one trailing-twelve STR Trend. You can add it later from the Data Room.',
    showYearTagging: false,
  },
  {
    id: 'insurance',
    label: 'Insurance Records',
    requiredForIc: true,
    multiFile: true,
    Icon: ShieldCheck,
    description:
      'Certificates of insurance, declaration pages, and loss runs. Surfaces premium burden in the expense engine and feeds risk-adjusted returns for coastal / wildfire markets.',
    exampleChip: 'e.g. COI, property + liability dec page, loss run',
    defaultDocType: 'INSURANCE',
    emptyState:
      'No insurance records uploaded yet. Most IC reviewers expect at least the most recent annual COI.',
    dropHint: 'Multiple files welcome · PDF / Word.',
    skipWarning:
      'Insurance is recommended for IC. You can add it later from the Data Room.',
    showYearTagging: false,
  },
  {
    id: 'property_tax',
    label: 'Property Taxes',
    requiredForIc: true,
    multiFile: true,
    Icon: Receipt,
    description:
      'Tax bills and assessment notices. Used by the expense engine to verify the broker tax line and by the underwriter to test post-acquisition reassessment risk.',
    exampleChip: 'e.g. property tax bill, assessment notice, tax-abatement agreement',
    defaultDocType: 'PROPERTY_TAX',
    emptyState:
      'No property tax records uploaded yet. Most IC reviewers expect at least the most recent assessment notice.',
    dropHint: 'Multiple files welcome · PDF / Excel.',
    skipWarning:
      'Property taxes are recommended for IC. You can add them later from the Data Room.',
    showYearTagging: false,
  },
  {
    id: 'room_mix',
    label: 'Room Mix / Unit Mix',
    requiredForIc: true,
    multiFile: true,
    Icon: Building2,
    description:
      'Room-type breakdown (king, double, suite) by floor and category. Used to verify keys count against the OM and to seed brand-system distributions.',
    exampleChip: 'e.g. room types breakdown, unit mix lookup, key count by floor',
    defaultDocType: 'ROOM_MIX',
    emptyState:
      'No room mix uploaded yet. Most IC reviewers expect a category-by-floor breakdown to test the broker keys count.',
    dropHint: 'Single tab welcome · Excel / PDF.',
    skipWarning:
      'Room mix is recommended for IC. You can add it later from the Data Room.',
    showYearTagging: false,
  },
  {
    id: 'capex',
    label: 'Historical CapEx',
    requiredForIc: true,
    multiFile: true,
    Icon: Hammer,
    description:
      'Capital-expenditure history, FF&E reserve reports, and priced PIP scopes. Feeds the capital engine and PIP-displacement model.',
    exampleChip: 'e.g. 3-year CapEx schedule, FF&E reserve, PIP scope',
    defaultDocType: 'CAPEX',
    emptyState:
      'No CapEx history yet. Most IC reviewers expect a multi-year schedule plus a PIP scope.',
    dropHint: 'Multiple files welcome · PDF / Excel.',
    skipWarning:
      'Historical CapEx is recommended for IC. You can add it later from the Data Room.',
    showYearTagging: false,
  },
  {
    id: 'property_info',
    label: 'Basic Property Info',
    requiredForIc: true,
    multiFile: true,
    Icon: Briefcase,
    description:
      'Floorplans, photos, brand standards, franchise agreements, PIP letters. Anchors property metadata when the OM is thin and feeds the property-condition narrative in the IC memo.',
    exampleChip: 'e.g. floorplans, brand standards, franchise agreement, PIP letter',
    defaultDocType: 'PROPERTY_INFO',
    emptyState:
      'No property info uploaded yet. Most IC reviewers expect floorplans + the current franchise agreement.',
    dropHint: 'Multiple files welcome · PDF / Word.',
    skipWarning:
      'Property info is recommended for IC. You can add it later from the Data Room.',
    showYearTagging: false,
  },
  {
    id: 'leases',
    label: 'Leases & Agreements',
    requiredForIc: true,
    multiFile: true,
    Icon: FileText,
    description:
      'Operator and management agreements, ground leases, tenant leases. Drives the operator-economics block and ground-lease cash-flow check.',
    exampleChip: 'e.g. management agreement, ground lease, tenant lease, license',
    defaultDocType: 'LEASES',
    emptyState:
      'No leases or agreements uploaded yet. Most IC reviewers expect at minimum the current operator agreement.',
    dropHint: 'Multiple files welcome · PDF / Word.',
    skipWarning:
      'Leases & agreements are recommended for IC. You can add them later from the Data Room.',
    showYearTagging: false,
  },
  {
    id: 'surveys',
    label: 'Surveys & Reviews',
    requiredForIc: false,
    multiFile: true,
    Icon: FileSearch,
    description:
      'ALTA surveys, structural / engineering reports, Phase I environmental, PCAs. Optional at screening but expected by Closing — surface them now so the IC narrative is ready.',
    exampleChip: 'e.g. ALTA survey, Phase I environmental, PCA, engineering report',
    defaultDocType: 'SURVEYS',
    emptyState:
      'No third-party reports yet. Optional at screening, expected by Closing — drop them as the broker drips them in.',
    dropHint: 'Multiple files welcome · PDF.',
    skipWarning:
      'Surveys & reviews are optional. They unlock once the broker shares them.',
    showYearTagging: false,
  },
];

export interface DocumentsStepProps {
  files: WizardFile[];
  onChange: (files: WizardFile[]) => void;
  onCanContinueChange: (canContinue: boolean) => void;
  /** Optional callback fired when a drag-drop filters out an unsupported
   *  file. The wizard page wires this to ``useToast`` so the toast lives
   *  on the same surface as the upload errors. */
  onUnsupportedFile?: (filename: string) => void;
}

const dedupeKey = (f: WizardFile) =>
  `${f.file.name}::${f.file.size}::${f.fiscal_year ?? ''}::${f.category}`;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function DocumentsStep({
  files,
  onChange,
  onCanContinueChange,
  onUnsupportedFile,
}: DocumentsStepProps) {
  // Default landing stage: the first FINANCIAL stage (T-12) so the user
  // lands on the one that gates the wizard. Falls back to the first
  // category if the catalog is ever reordered.
  const [stage, setStage] = useState<WizardCategory>(
    WIZARD_CATEGORIES.find((c) => c.id === 't12')?.id ?? WIZARD_CATEGORIES[0].id,
  );
  const [skipNoticeShown, setSkipNoticeShown] = useState<Set<WizardCategory>>(
    new Set(),
  );

  const filesByCategory = useMemo(() => {
    const m = {} as Record<WizardCategory, WizardFile[]>;
    for (const c of WIZARD_CATEGORIES) m[c.id] = [];
    for (const f of files) {
      if (m[f.category]) m[f.category].push(f);
    }
    return m;
  }, [files]);

  // Wave 1 gate — financials = T-12 OR historical P&L. ONE upload in
  // either bucket clears the gate.
  const canContinue =
    filesByCategory.t12.length > 0 ||
    filesByCategory.historical_pnl.length > 0;
  useEffect(() => {
    onCanContinueChange(canContinue);
  }, [canContinue, onCanContinueChange]);

  const addFiles = useCallback(
    (
      incoming: File[],
      category: WizardCategory,
      meta: {
        user_doc_type?: WizardUserDocType | null;
        fiscal_year?: number | null;
      } = {},
    ) => {
      if (!incoming.length) return;
      const spec = WIZARD_CATEGORIES.find((c) => c.id === category);
      const filtered: File[] = [];
      for (const f of incoming) {
        if (isAllowedFile(f)) {
          filtered.push(f);
        } else {
          onUnsupportedFile?.(f.name);
        }
      }
      if (!filtered.length) return;

      const existing = new Set(files.map(dedupeKey));
      const next: WizardFile[] = [...files];
      for (const file of filtered) {
        const docType =
          meta.user_doc_type !== undefined
            ? meta.user_doc_type
            : spec?.defaultDocType ?? null;
        const candidate: WizardFile = {
          file,
          category,
          user_doc_type: docType,
          fiscal_year: meta.fiscal_year ?? null,
        };
        if (existing.has(dedupeKey(candidate))) continue;
        existing.add(dedupeKey(candidate));
        next.push(candidate);
      }
      onChange(next);
    },
    [files, onChange, onUnsupportedFile],
  );

  const removeAt = useCallback(
    (target: WizardFile) => {
      const key = dedupeKey(target);
      onChange(files.filter((f) => dedupeKey(f) !== key));
    },
    [files, onChange],
  );

  const updateFile = useCallback(
    (target: WizardFile, patch: Partial<WizardFile>) => {
      const key = dedupeKey(target);
      onChange(
        files.map((f) => (dedupeKey(f) === key ? { ...f, ...patch } : f)),
      );
    },
    [files, onChange],
  );

  const activeIdx = WIZARD_CATEGORIES.findIndex((c) => c.id === stage);
  const activeSpec = WIZARD_CATEGORIES[activeIdx];

  const goNextStage = () => {
    const nextIdx = Math.min(WIZARD_CATEGORIES.length - 1, activeIdx + 1);
    setStage(WIZARD_CATEGORIES[nextIdx].id);
  };
  const goPrevStage = () => {
    const prevIdx = Math.max(0, activeIdx - 1);
    setStage(WIZARD_CATEGORIES[prevIdx].id);
  };

  const onSkip = () => {
    if (filesByCategory[stage].length === 0) {
      setSkipNoticeShown((prev) => new Set(prev).add(stage));
    }
    goNextStage();
  };

  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">
        Add documents
      </h2>
      <p className="text-[12.5px] text-ink-500 mb-3">
        Walk the 11 categories on the left. Financials are required to
        advance — the other nine are recommended for IC and can be added
        later from the Data Room.
      </p>
      <div className="rounded-md bg-brand-50 border border-brand-100 p-3 text-[12px] text-ink-700 leading-relaxed mb-5">
        Drop documents into the matching section so Fondok can route them
        to the right extractor. Year-tagging the financials powers the
        coverage line and the historical-variance detector — everything
        else is single-bucket.
      </div>

      <div className="grid grid-cols-12 gap-5">
        {/* ─────────────── Vertical sidebar ─────────────── */}
        <nav
          aria-label="Document categories"
          className="col-span-12 lg:col-span-4 xl:col-span-3"
        >
          <ul
            className="sticky top-4 space-y-1.5 max-h-[calc(100vh-8rem)] overflow-y-auto pr-1 scrollbar-thin"
            role="list"
          >
            {WIZARD_CATEGORIES.map((spec) => {
              const count = filesByCategory[spec.id].length;
              const active = spec.id === stage;
              const covered = count > 0;
              const Icon = spec.Icon;
              const missing = !covered && spec.requiredForIc;
              return (
                <li key={spec.id} role="listitem">
                  <button
                    type="button"
                    onClick={() => setStage(spec.id)}
                    aria-pressed={active}
                    aria-label={`${spec.label} (${covered ? `${count} file${count === 1 ? '' : 's'}` : spec.requiredForIc ? 'missing' : 'optional'})`}
                    className={cn(
                      'w-full text-left px-3 py-2 rounded-md border flex items-start gap-2.5',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                      'transition-colors motion-reduce:transition-none',
                      active
                        ? 'bg-brand-50 border-brand-500 border-l-[3px] shadow-sm'
                        : 'bg-white border-border hover:bg-ink-100 border-l-[3px] border-l-transparent',
                    )}
                  >
                    <span
                      className={cn(
                        'mt-0.5 inline-flex items-center justify-center w-5 h-5 rounded',
                        active
                          ? 'bg-brand-500 text-white'
                          : covered
                            ? 'bg-success-50 text-success-700'
                            : missing
                              ? 'bg-danger-50 text-danger-700'
                              : 'bg-ink-100 text-ink-500',
                      )}
                      aria-hidden="true"
                    >
                      {covered ? (
                        <Check size={11} strokeWidth={3} />
                      ) : (
                        <Icon size={11} />
                      )}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div
                        className={cn(
                          'text-[12.5px] font-medium leading-tight truncate',
                          active ? 'text-brand-700' : 'text-ink-900',
                        )}
                      >
                        {spec.label}
                      </div>
                      <div className="mt-1">
                        {covered ? (
                          <span className="inline-flex items-center px-1.5 py-0 rounded text-[10.5px] tabular-nums font-medium bg-success-50 text-success-700 border border-success-500/30">
                            {count} file{count === 1 ? '' : 's'}
                          </span>
                        ) : missing ? (
                          <span className="inline-flex items-center px-1.5 py-0 rounded text-[10.5px] font-medium bg-danger-50 text-danger-700 border border-danger-500/30">
                            Missing
                          </span>
                        ) : (
                          <span className="inline-flex items-center px-1.5 py-0 rounded text-[10.5px] font-medium bg-ink-100 text-ink-500 border border-border">
                            Optional
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        {/* ─────────────── Content panel ─────────────── */}
        <div className="col-span-12 lg:col-span-8 xl:col-span-9">
          <CategoryPanel
            spec={activeSpec}
            files={filesByCategory[activeSpec.id]}
            onAdd={(fs, meta) =>
              addFiles(fs, activeSpec.id, meta ?? {})
            }
            onRemove={removeAt}
            onUpdate={updateFile}
            skipNoticed={skipNoticeShown.has(activeSpec.id)}
          />

          {/* Stage navigation */}
          <div className="mt-6 flex items-center justify-between">
            <Button
              variant="ghost"
              size="sm"
              onClick={goPrevStage}
              disabled={activeIdx === 0}
              aria-label="Previous category"
            >
              <ArrowLeft size={12} aria-hidden="true" /> Previous
            </Button>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                onClick={onSkip}
                disabled={activeIdx === WIZARD_CATEGORIES.length - 1}
                aria-label="Skip and continue"
              >
                Skip
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={goNextStage}
                disabled={activeIdx === WIZARD_CATEGORIES.length - 1}
                aria-label="Next category"
              >
                Next <ArrowRight size={12} aria-hidden="true" />
              </Button>
            </div>
          </div>

          {!canContinue && (
            <div
              role="alert"
              className="mt-4 px-3 py-2 rounded-md bg-warn-50 border border-warn-500/30 text-[12px] text-warn-700 flex items-center gap-2"
            >
              <Info size={13} aria-hidden="true" />
              Add at least one financial (T-12 or Annual / YTD / Monthly
              P&amp;L) to continue. Year tagging is optional but recommended.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────── content panel ───────────────────────────

function CategoryPanel({
  spec,
  files,
  onAdd,
  onRemove,
  onUpdate,
  skipNoticed,
}: {
  spec: WizardCategorySpec;
  files: WizardFile[];
  onAdd: (
    files: File[],
    meta?: {
      user_doc_type?: WizardUserDocType | null;
      fiscal_year?: number | null;
    },
  ) => void;
  onRemove: (file: WizardFile) => void;
  onUpdate: (file: WizardFile, patch: Partial<WizardFile>) => void;
  skipNoticed: boolean;
}) {
  const Icon = spec.Icon;
  return (
    <section aria-label={spec.label}>
      <header className="mb-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Icon size={16} className="text-brand-500" aria-hidden="true" />
          <h3 className="text-[18px] font-semibold text-ink-900">
            {spec.label}
          </h3>
          {spec.requiredForIc ? (
            <Badge tone="red">Required for IC</Badge>
          ) : (
            <Badge tone="gray">Optional</Badge>
          )}
        </div>
        <p className="text-[12.5px] text-ink-500 mt-2 leading-relaxed max-w-[640px]">
          {spec.description}
        </p>
        <div className="mt-2.5 inline-flex items-center gap-1.5 text-[11.5px] text-ink-500 italic">
          <span className="inline-block w-1 h-1 rounded-full bg-ink-400" aria-hidden="true" />
          {spec.exampleChip}
        </div>
      </header>

      {/* Financials carry the year-coverage line above the drop zone. */}
      {spec.showYearTagging && <FinancialYearHint files={files} />}

      <DropZone
        spec={spec}
        onFiles={(fs) => onAdd(fs)}
      />

      {files.length === 0 ? (
        <div className="mt-3 px-4 py-5 rounded-md border border-dashed border-border bg-bg text-[12px] text-ink-500 leading-relaxed">
          {spec.emptyState}
        </div>
      ) : (
        <ul
          className="mt-3 space-y-2"
          role="list"
          aria-label={`Selected ${spec.label} files`}
        >
          {files.map((f) => (
            <li key={dedupeKey(f)}>
              <FileRow
                spec={spec}
                file={f}
                onRemove={onRemove}
                onUpdate={onUpdate}
              />
            </li>
          ))}
        </ul>
      )}

      {skipNoticed && files.length === 0 && (
        <div
          role="status"
          className="mt-4 px-3 py-2 rounded-md bg-warn-50 border border-warn-500/30 text-[12px] text-warn-700 flex items-start gap-2"
        >
          <AlertCircle size={13} className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>{spec.skipWarning}</span>
        </div>
      )}
    </section>
  );
}

function FinancialYearHint({ files }: { files: WizardFile[] }) {
  const coveredYears = useMemo(
    () =>
      new Set(
        files
          .map((f) => f.fiscal_year)
          .filter((y): y is number => typeof y === 'number'),
      ),
    [files],
  );
  const visibleYears = useMemo(() => {
    const now = new Date().getUTCFullYear();
    const defaults = [now, now - 1, now - 2, now - 3, now - 4];
    return Array.from(new Set([...defaults, ...coveredYears])).sort(
      (a, b) => a - b,
    );
  }, [coveredYears]);
  return (
    <div className="mb-3">
      <YearCoverageHint coveredYears={coveredYears} years={visibleYears} />
    </div>
  );
}

// ─────────────────────────── drop zone ───────────────────────────

function DropZone({
  spec,
  onFiles,
}: {
  spec: WizardCategorySpec;
  onFiles: (files: File[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);

  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDrag(false);
    const dropped = Array.from(e.dataTransfer.files ?? []);
    if (dropped.length === 0) return;
    onFiles(spec.multiFile ? dropped : dropped.slice(0, 1));
  };
  const onClick = () => inputRef.current?.click();
  const inputId = `wizard-${spec.id}-drop`;
  return (
    <div>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        multiple={spec.multiFile}
        accept={ACCEPT}
        className="hidden"
        aria-label={`Add ${spec.label} files`}
        onChange={(e) => {
          const list = e.target.files ? Array.from(e.target.files) : [];
          e.target.value = '';
          onFiles(spec.multiFile ? list : list.slice(0, 1));
        }}
      />
      <div
        role="button"
        tabIndex={0}
        onClick={onClick}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            onClick();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        aria-label={`Drop ${spec.label} files`}
        className={cn(
          'border-2 border-dashed rounded-lg text-center cursor-pointer',
          'transition-colors motion-reduce:transition-none',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
          'py-7 px-5',
          drag
            ? 'border-brand-500 bg-brand-50'
            : 'border-ink-300 hover:border-brand-500 hover:bg-brand-50/40',
        )}
      >
        <UploadCloud
          size={24}
          className="text-ink-400 mx-auto mb-2"
          aria-hidden="true"
        />
        <div className="text-[13px] font-medium text-ink-900">
          {drag ? 'Drop to add' : `Drop ${spec.label.toLowerCase()} here`}
        </div>
        <div className="text-[11.5px] text-ink-500 mt-1">{spec.dropHint}</div>
      </div>
    </div>
  );
}

// ─────────────────────────── file row ───────────────────────────

function FileRow({
  spec,
  file,
  onRemove,
  onUpdate,
}: {
  spec: WizardCategorySpec;
  file: WizardFile;
  onRemove: (file: WizardFile) => void;
  onUpdate: (file: WizardFile, patch: Partial<WizardFile>) => void;
}) {
  const IconForExt = file.file.name.toLowerCase().endsWith('.xlsx')
    ? FileSpreadsheet
    : FileText;
  return (
    <div className="rounded-md border border-border bg-white px-3 py-2.5 flex items-center gap-3 flex-wrap">
      <IconForExt
        size={14}
        className="text-ink-700 flex-shrink-0"
        aria-hidden="true"
      />
      <div className="flex-1 min-w-0">
        <div className="text-[12.5px] font-medium text-ink-900 truncate">
          {file.file.name}
        </div>
        <div className="text-[11px] text-ink-500 tabular-nums">
          {formatBytes(file.file.size)}
        </div>
      </div>
      {spec.showYearTagging && (
        <YearField
          value={file.fiscal_year ?? null}
          onChange={(yr) => onUpdate(file, { fiscal_year: yr })}
          fileKey={dedupeKey(file)}
        />
      )}
      {spec.picker && (
        <>
          <label className="sr-only" htmlFor={`type-${dedupeKey(file)}`}>
            {spec.picker.label}
          </label>
          <select
            id={`type-${dedupeKey(file)}`}
            value={file.user_doc_type ?? ''}
            onChange={(e) =>
              onUpdate(file, {
                user_doc_type:
                  (e.target.value as WizardUserDocType) || null,
              })
            }
            aria-label={`Set ${spec.picker.label.toLowerCase()} for ${file.file.name}`}
            className="px-2 py-1 text-[12px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          >
            {spec.picker.options.map((t) => (
              <option key={t.label} value={t.value} title={t.help}>
                {t.label}
              </option>
            ))}
          </select>
        </>
      )}
      <button
        type="button"
        onClick={() => onRemove(file)}
        aria-label={`Remove ${file.file.name}`}
        className="p-1 rounded text-ink-400 hover:text-danger-700 hover:bg-danger-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-500"
      >
        <Trash2 size={13} aria-hidden="true" />
      </button>
    </div>
  );
}

function YearField({
  value,
  onChange,
  fileKey,
}: {
  value: number | null;
  onChange: (year: number | null) => void;
  fileKey: string;
}) {
  const [editing, setEditing] = useState(value === null);
  const [draft, setDraft] = useState(value !== null ? String(value) : '');
  if (!editing && value !== null) {
    return (
      <button
        type="button"
        onClick={() => {
          setDraft(String(value));
          setEditing(true);
        }}
        aria-label={`Edit year ${value}`}
        className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] tabular-nums font-medium bg-success-50 text-success-700 border border-success-500/30 hover:bg-success-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        FY {value}
      </button>
    );
  }
  const commit = () => {
    const n = Number.parseInt(draft, 10);
    if (Number.isFinite(n) && n >= 1900 && n <= 2100) {
      onChange(n);
      setEditing(false);
    } else if (draft.trim() === '') {
      onChange(null);
      setEditing(false);
    }
  };
  return (
    <div className="flex items-center gap-1">
      <label className="sr-only" htmlFor={`fy-${fileKey}`}>
        Fiscal year
      </label>
      <input
        id={`fy-${fileKey}`}
        type="number"
        min={1900}
        max={2100}
        placeholder="2025"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            commit();
          }
        }}
        className="w-20 px-2 py-1 text-[12px] tabular-nums bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
      />
      {value !== null && (
        <button
          type="button"
          onClick={() => {
            setDraft('');
            onChange(null);
            setEditing(false);
          }}
          aria-label="Clear year"
          className="p-1 rounded text-ink-400 hover:text-danger-700 hover:bg-danger-50"
        >
          <Plus size={11} className="rotate-45" aria-hidden="true" />
        </button>
      )}
    </div>
  );
}
