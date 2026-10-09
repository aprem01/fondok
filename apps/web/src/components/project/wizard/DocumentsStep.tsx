'use client';

/**
 * DocumentsStep — Wave 1 expansion (June 2026).
 *
 * Replaces the legacy 4-stage pill row with a vertical category sidebar
 * matching the canonical IC-grade checklist. `WIZARD_CATEGORIES` below is
 * the catalog — read it there rather than trusting a list in a comment.
 * FON-34 merged the old "T-12 / Trailing Twelve Months" and "Annual / YTD /
 * Monthly P&L" buckets into one "Financial Statements" category with a
 * per-file statement-type picker.
 *
 * Locked Wave 1 product decision — ONLY Financial Statements is hard-required
 * to advance Step 3 → Step 4; one file of any statement type satisfies the
 * gate. Every other category surfaces a red "Missing" dot in the sidebar but
 * never blocks the wizard. Most deals start without all docs; locking the
 * wizard behind the full checklist = no one ever finishes.
 *
 * The persistent right-rail DocumentsChecklist lives outside this
 * component — DocumentsStep is the content column. The page wires up
 * both and shares the WizardFile[] state.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Briefcase,
  Building2,
  CalendarClock,
  ChevronRight,
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
  TrendingUp,
  UploadCloud,
} from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { cn } from '@/lib/format';
import type {
  WizardCategory,
  WizardFile,
  WizardUserDocType,
} from '@/lib/api';
import { YearCoverageHint } from './YearCoverageHint';
import { CoachMark } from '@/components/help/CoachMark';
import {
  ACCEPTED_FORMATS_LABEL,
  GOOGLE_EXPORT_GUIDANCE,
  UPLOAD_ACCEPT,
  isUploadableFile,
} from '@/lib/uploadFormats';

// ─────────────────────────── allowlist (B3) ───────────────────────────
// R-030 — the accepted formats (incl. PowerPoint, and the Excel / PowerPoint
// exports of Google Sheets / Slides) live in lib/uploadFormats, which mirrors
// the worker's _ALLOWED_EXTENSIONS. The HTML <input accept=> attribute alone
// only filters the picker dialog (and unreliably across browsers) — the
// drag-drop handlers filter with the same list so a misformatted file never
// sneaks into the staged list.
const ACCEPT = UPLOAD_ACCEPT;

// R-029 — the drop prompts name the document exactly as labelled
// ("Offering Memorandum", not "offering memorandum"), so the article has
// to follow the label's first letter.
function indefiniteArticle(label: string): 'a' | 'an' {
  return /^[aeiou]/i.test(label) ? 'an' : 'a';
}

function isAllowedFile(file: File): boolean {
  return isUploadableFile(file.name);
}

// ─────────────────────────── category catalog ───────────────────────────

type WizardCategorySpec = {
  id: WizardCategory;
  label: string;
  /** Tight single-line label used in the sidebar (falls back to
   *  `label` when undefined). Keep ≤ 24 chars so the row never
   *  truncates at standard sidebar widths. */
  sidebarLabel?: string;
  /** Counts toward the IC-readiness percentage — false only for SURVEYS
   *  (Due Diligence). Does NOT change the sidebar status colour (R-031). */
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

// FON-41 / Sam's decision 5 (R-027, R-031, R-034..R-038) — slot order and
// labels follow the IC diligence reading order. This is a DISPLAY taxonomy
// over the existing DocType enum: two slots share a doc type with a sibling
// (Comp Set / Market Reports → STR_TREND, Future CapEx → CAPEX). Historic vs
// Future CapEx travel as ``user_doc_subtypes[]`` (FON-41 / R-036 — see
// ``WIZARD_CATEGORY_DOC_SUBTYPE`` in lib/api), so the Data Room can list them
// apart. The Router still lanes CoStar / market files to MARKET_STUDY on
// extraction.
export const WIZARD_CATEGORIES: WizardCategorySpec[] = [
  {
    id: 'om',
    label: 'Offering Memorandum',
    sidebarLabel: 'Offering Memorandum',
    requiredForIc: true,
    multiFile: false,
    Icon: FileText,
    description:
      'Seller pitch deck. Fondok pulls property metadata (keys, brand, year built, address) plus the seller pro forma. Every extracted field is editable downstream.',
    exampleChip: 'e.g. teaser deck, confidential offering memorandum, executive summary',
    defaultDocType: 'OM',
    emptyState:
      'No OM uploaded yet. The broker memorandum is the first read of every deal — it anchors property metadata before extraction.',
    dropHint: 'One file · PDF, PowerPoint or Word. Click to browse.',
    skipWarning:
      'Most IC reviewers expect the OM. You can add it later from the Data Room.',
    showYearTagging: false,
  },
  {
    // R-035 — "Room Mix / Unit Mix" widened to the whole hotel program.
    id: 'room_mix',
    label: 'Hotel Program',
    sidebarLabel: 'Hotel Program',
    requiredForIc: true,
    multiFile: true,
    Icon: Building2,
    description:
      'Room mix and unit count, floor plans, design documents, and program summaries. Used to verify the keys count against the OM and to seed brand-system distributions.',
    exampleChip: 'e.g. room mix / unit count, floor plans, design documents, program summary',
    defaultDocType: 'ROOM_MIX',
    emptyState:
      'No hotel program uploaded yet. Most IC reviewers expect a room mix to test the broker keys count.',
    dropHint: 'Multiple files welcome · Excel / PDF.',
    skipWarning:
      'The hotel program is recommended for IC. You can add it later from the Data Room.',
    showYearTagging: false,
  },
  {
    // FON-34 — one merged "Financial Statements" category (matches the Data
    // Room). The old split T-12 vs Annual/Monthly buckets confused users; now
    // every P&L family drops here and the per-file picker tags the type.
    id: 'financials',
    label: 'Financial Statements',
    sidebarLabel: 'Financial Statements',
    requiredForIc: true,
    multiFile: true,
    Icon: FileSpreadsheet,
    description:
      'T-12, full-year, YTD, and monthly P&Ls — the load-bearing inputs. Tag each file so the engines bucket it correctly: a T-12 or full year anchors the NOI baseline, YTDs are partial rolls, monthlies feed seasonality. Tag the year (and for a T-12, the month it ends).',
    exampleChip: 'e.g. T-12 ending Mar 2026, 2024 full-year P&L, May 2025 monthly',
    picker: {
      label: 'Statement type',
      options: [
        {
          value: 'T12',
          label: 'T-12 (trailing 12mo)',
          help: 'Rolling twelve months ending in a given month — your Year-1 baseline.',
        },
        {
          value: 'PNL',
          label: 'Full Year',
          help: 'A complete calendar or fiscal year (Jan–Dec).',
        },
        {
          value: 'PNL_MONTHLY',
          label: 'Monthly',
          help: 'A single month or month-by-month detail.',
        },
        {
          value: 'PNL_YTD',
          label: 'Year-to-Date',
          help: 'Partial-year roll-up through the most recent close.',
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
      'No financial statements staged. A T-12 or P&L is the single most load-bearing input — every NOI projection grounds against it.',
    dropHint: 'Multiple files welcome · PDF / Excel / CSV.',
    skipWarning:
      'Financials are required to advance — drop at least one T-12 or P&L.',
    showYearTagging: true,
  },
  {
    // R-034 — STR exports only; CoStar / market files moved to their own
    // slot below. "Not sure" is still offered: once the file is extracted
    // the Data Room shows the report type Fondok detected next to it.
    id: 'str',
    label: 'STR Reports',
    sidebarLabel: 'STR Reports',
    requiredForIc: true,
    multiFile: true,
    Icon: ClipboardCheck,
    description:
      'STR exports. Trend reports power the comp-set drift detector and feed the Market tab; Star benchmarks anchor RGI / ARI / MPI. Unsure of the report type? Leave it on "Not sure" — the Data Room shows the type Fondok detected.',
    exampleChip: 'e.g. STR Trend (TTM), monthly STAR, STR Star daily snapshot',
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
    // R-036 — CoStar / market reports get their own slot. Uploads as
    // STR_TREND (the existing comp-set lane); the Router re-lanes CoStar
    // submarket / pipeline / sales files to MARKET_STUDY on extraction.
    id: 'comp_set',
    label: 'Comp Set / Market Reports',
    sidebarLabel: 'Comp Set / Market',
    requiredForIc: true,
    multiFile: true,
    Icon: TrendingUp,
    // R-067 — a CBRE / HotStats P&L benchmark for the submarket and
    // positioning also lands here; tag it so its cost ratios are mapped to
    // the expense categories and shown as the Future P&L's Benchmark column.
    description:
      'CoStar submarket, pipeline, and sales reports plus comp-set definitions. Frames the competitive set and new supply around the hotel. Add a CBRE / HotStats P&L benchmark for the submarket and positioning to compare the business plan’s cost ratios.',
    exampleChip: 'e.g. CoStar submarket report, pipeline report, sales comps, comp-set definition, CBRE P&L benchmark',
    picker: {
      label: 'Report type',
      options: [
        {
          value: 'STR_TREND',
          label: 'Market / comp-set report',
          help: 'CoStar submarket, pipeline or sales report, or a comp-set definition.',
        },
        {
          value: 'PNL_BENCHMARK',
          label: 'P&L Benchmark (CBRE / HotStats)',
          help: 'Peer-set operating P&L (% of revenue, POR, PAR) for the submarket and positioning — shown as the Benchmark column on the Future P&L.',
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
      'No comp set or market reports yet. Most IC reviewers expect a comp-set definition and a submarket view.',
    dropHint: 'Multiple files welcome · PDF / Excel / PowerPoint.',
    skipWarning:
      'Comp set and market reports are recommended for IC. You can add them later from the Data Room.',
    showYearTagging: false,
  },
  {
    id: 'capex',
    label: 'Historic CapEx',
    sidebarLabel: 'Historic CapEx',
    requiredForIc: true,
    multiFile: true,
    Icon: Hammer,
    description:
      'Capital-expenditure history and FF&E reserve reports. Feeds the capital engine with what has already been spent on the asset.',
    exampleChip: 'e.g. 3-year CapEx schedule, FF&E reserve report',
    defaultDocType: 'CAPEX',
    emptyState:
      'No CapEx history yet. Most IC reviewers expect a multi-year schedule.',
    dropHint: 'Multiple files welcome · PDF / Excel.',
    skipWarning:
      'Historic CapEx is recommended for IC. You can add it later from the Data Room.',
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
    // R-037 — forward-looking capital gets its own slot. It uploads as
    // CAPEX like Historic CapEx, tagged doc_subtype ``future`` (R-036);
    // the Historic CapEx slot tags ``historic``.
    id: 'future_capex',
    label: 'Future CapEx',
    sidebarLabel: 'Future CapEx',
    requiredForIc: true,
    multiFile: true,
    Icon: CalendarClock,
    description:
      'PIP budgets and rebranding / repositioning capital plans. Feeds the capital engine and the PIP-displacement model.',
    exampleChip: 'e.g. PIP budget, rebranding capital plan, repositioning budget',
    defaultDocType: 'CAPEX',
    emptyState:
      'No forward capital plan yet. Most IC reviewers expect the PIP budget when a brand change or renovation is planned.',
    dropHint: 'Multiple files welcome · PDF / Excel.',
    skipWarning:
      'Future CapEx is recommended for IC. You can add it later from the Data Room.',
    showYearTagging: false,
  },
  {
    // R-038 — agreements and ownership docs lead; floor plans moved to
    // Hotel Program.
    id: 'property_info',
    label: 'Other Property Info',
    sidebarLabel: 'Other Property Info',
    requiredForIc: true,
    multiFile: true,
    Icon: Briefcase,
    description:
      'Management Agreement, Franchise Agreement, business plans, and ownership / entity documents. Anchors property metadata when the OM is thin and feeds the IC memo narrative.',
    exampleChip: 'e.g. management agreement, franchise agreement, business plan, ownership / entity docs',
    defaultDocType: 'PROPERTY_INFO',
    emptyState:
      'No property info uploaded yet. Most IC reviewers expect the current management and franchise agreements.',
    dropHint: 'Multiple files welcome · PDF / Word.',
    skipWarning:
      'Property info is recommended for IC. You can add it later from the Data Room.',
    showYearTagging: false,
  },
  {
    id: 'leases',
    label: 'Leases & Agreements',
    sidebarLabel: 'Leases',
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
    // R-031 — "Surveys & Reviews" widened to Due Diligence. Still excluded
    // from the IC-readiness percentage (matches the worker's completeness
    // categories), but its sidebar status colour is the same as every
    // other slot's — see the dot in DocumentsStep below.
    id: 'surveys',
    label: 'Due Diligence',
    sidebarLabel: 'Due Diligence',
    requiredForIc: false,
    multiFile: true,
    Icon: FileSearch,
    description:
      'Property condition reports (PCRs), legal memos, surveys, and reviews. Optional at screening but expected by Closing — surface them now so the IC narrative is ready.',
    exampleChip: 'e.g. PCR, legal memo, ALTA survey, Phase I environmental, reviews',
    defaultDocType: 'SURVEYS',
    emptyState:
      'No due diligence reports yet. Optional at screening, expected by Closing — drop them as the broker drips them in.',
    dropHint: 'Multiple files welcome · PDF.',
    skipWarning:
      'Due diligence reports are optional. They unlock once the broker shares them.',
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
  /** When true (the page flips this only after the user attempts a
   *  gated Next), the inline WARN banner appears under the panel.
   *  Otherwise the surface stays quiet — the right-rail completeness
   *  ring is the only ambient cue. */
  showGateWarning?: boolean;
  /** Active sub-stage — exposed so the right-rail can mirror the
   *  current selection without owning the state. */
  onStageChange?: (stage: WizardCategory) => void;
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
  showGateWarning = false,
  onStageChange,
}: DocumentsStepProps) {
  // Default landing stage: Offering Memorandum. Natural reading order —
  // when a broker sends a deal an analyst opens the OM first. Financials
  // still gate the wizard (Next stays disabled until a T-12 or
  // historical P&L lands) but they don't need to be the first screen.
  const [stage, setStageState] = useState<WizardCategory>(
    WIZARD_CATEGORIES.find((c) => c.id === 'om')?.id ?? WIZARD_CATEGORIES[0].id,
  );
  const setStage = useCallback(
    (next: WizardCategory) => {
      setStageState(next);
      onStageChange?.(next);
    },
    [onStageChange],
  );
  // Tell the parent about the initial landing stage on first paint so
  // the right rail and any external mirrors stay in sync from frame 1.
  useEffect(() => {
    onStageChange?.(stage);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const filesByCategory = useMemo(() => {
    const m = {} as Record<WizardCategory, WizardFile[]>;
    for (const c of WIZARD_CATEGORIES) m[c.id] = [];
    for (const f of files) {
      if (m[f.category]) m[f.category].push(f);
    }
    return m;
  }, [files]);

  // R-026 — files dropped in the "Drop everything here" zone. Not a slot:
  // they upload untagged and the Router assigns each one's category.
  const autoFiles = useMemo(() => files.filter((f) => f.category === 'auto'), [files]);

  // Wave 1 gate — financials required. ONE upload in the merged
  // Financial Statements bucket (T-12 / full year / monthly / YTD) clears it.
  // R-026: files in the auto-classify zone also clear it — the analyst who
  // drops the whole data room in one place should not be blocked on a
  // category Fondok assigns only after upload. If none of them turns out to
  // be a financial statement, the Data Room's Financial Statements row
  // reads "Not uploaded" and the coverage gap chips say so.
  const canContinue = (filesByCategory.financials?.length ?? 0) > 0 || autoFiles.length > 0;
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
    // Quiet skip — the sidebar dot signals "missing" and the right-rail
    // ring tracks coverage, so a separate skip-warning banner under the
    // panel is redundant noise.
    goNextStage();
  };

  return (
    // `data-testid` rather than the heading text: the E2E suite needs a way
    // to say "the wizard is on Step 3" that a copy edit cannot break. The
    // previous spec waited on the words "Add documents", which this step has
    // not said for some time, and it failed on every run instead of being
    // updated.
    <div data-testid="wizard-documents-step">
      <h2 className="text-[18px] font-semibold text-ink-900 mb-5">
        Documents
      </h2>

      <AutoClassifyZone
        files={autoFiles}
        onAdd={(fs) => addFiles(fs, 'auto', { user_doc_type: null })}
        onRemove={removeAt}
      />

      <div className="grid grid-cols-12 gap-5">
        {/* ─────────────── Vertical sidebar — single-row, status dot ─────────────── */}
        <nav
          aria-label="Document categories"
          className="col-span-12 lg:col-span-4 xl:col-span-3"
        >
          <ul
            className="sticky top-4 space-y-0.5 max-h-[calc(100vh-8rem)] overflow-y-auto pr-1 scrollbar-thin"
            role="list"
          >
            {WIZARD_CATEGORIES.map((spec) => {
              const count = filesByCategory[spec.id].length;
              const active = spec.id === stage;
              const covered = count > 0;
              // Status dot IS the status. Green = covered, red = not yet
              // covered. R-031: the colour no longer depends on
              // ``requiredForIc`` — keying it off that flag is what rendered
              // the optional Surveys / Due Diligence slot gray while every
              // other empty slot was red. ``requiredForIc`` still drives the
              // readiness percentage and the accessible "optional" wording.
              const dotClass = covered ? 'bg-success-500' : 'bg-danger-500/80';
              const triggerBtn = (
                <button
                  type="button"
                  onClick={() => setStage(spec.id)}
                  aria-pressed={active}
                  aria-label={`${spec.label} (${covered ? `${count} file${count === 1 ? '' : 's'}` : spec.requiredForIc ? 'missing' : 'optional'})`}
                  className={cn(
                    'w-full text-left pl-3 pr-2 py-2 rounded-md flex items-center gap-2.5',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                    'border-l-[2px] transition-[background-color,border-color] duration-150 motion-reduce:transition-none',
                    active
                      ? 'bg-brand-50 border-l-brand-500'
                      : 'bg-transparent border-l-transparent hover:bg-ink-100/60',
                  )}
                >
                  <span
                    data-testid={`slot-status-${spec.id}`}
                    className={cn(
                      'inline-block w-1.5 h-1.5 rounded-full flex-shrink-0',
                      dotClass,
                    )}
                    aria-hidden="true"
                  />
                  <span
                    className={cn(
                      'flex-1 min-w-0 text-[12.5px] leading-tight truncate',
                      active
                        ? 'font-semibold text-brand-700'
                        : 'font-medium text-ink-900',
                    )}
                  >
                    {spec.sidebarLabel ?? spec.label}
                  </span>
                  <ChevronRight
                    size={12}
                    className={cn(
                      'flex-shrink-0',
                      active ? 'text-brand-500' : 'text-ink-300',
                    )}
                    aria-hidden="true"
                  />
                </button>
              );
              return (
                <li key={spec.id} role="listitem">
                  {active && spec.id === 'financials' ? (
                    <CoachMark
                      anchorId="wizard-step3-sidebar-required"
                      viewKey="wizard-step3"
                      order={0}
                      title="Only Financials are required"
                      body="Other categories surface as 'Missing' until covered — that's by design, not a block. You can advance as soon as you've added at least one financial statement (T-12 or P&L) and circle back to the rest later from the Data Room."
                      side="right"
                      learnMoreHref="/methodology#extraction"
                    >
                      {triggerBtn}
                    </CoachMark>
                  ) : (
                    triggerBtn
                  )}
                </li>
              );
            })}
          </ul>
        </nav>

        {/* ─────────────── Content panel ─────────────── */}
        <div className="col-span-12 lg:col-span-8 xl:col-span-9">
          {/* key={stage} re-mounts on sub-stage switch so the fade-in
              animation plays each time. Replaces the previous jarring
              snap-replace. */}
          <div key={stage} className="wizard-fade-in">
            {/* The .wizard-fade-in keyframe is opt-out via the
                prefers-reduced-motion rule in globals.css. */}
            <CategoryPanel
              spec={activeSpec}
              files={filesByCategory[activeSpec.id]}
              onAdd={(fs, meta) => addFiles(fs, activeSpec.id, meta ?? {})}
              onRemove={removeAt}
              onUpdate={updateFile}
            />
          </div>

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

          {/* WARN banner fires only after the analyst attempts the
              page-level Next while the financials gate is locked.
              Otherwise the surface stays quiet. */}
          {showGateWarning && !canContinue && (
            <div
              role="alert"
              className="mt-4 px-3 py-2 rounded-md bg-warn-50 border border-warn-500/30 text-[12px] text-warn-700 flex items-center gap-2"
            >
              <Info size={13} aria-hidden="true" />
              Add at least one financial (T-12 or Annual / YTD / Monthly
              P&amp;L) — or drop your files in “Drop everything here” — to
              continue.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ─────────────────────── auto-classify zone (R-026) ───────────────────────

/**
 * R-026 — one place to drop every document. Files staged here are uploaded
 * with no category tag; the Router reads each one and assigns its category
 * (``ai_proposed_doc_type`` → ``doc_type``). The Data Room then lists each
 * file under the category it landed in with a "Classified automatically —
 * confirm" chip and the usual reclassify control. The per-category slots
 * below stay for analysts who prefer to tag files themselves.
 */
export function AutoClassifyZone({
  files,
  onAdd,
  onRemove,
}: {
  files: WizardFile[];
  onAdd: (files: File[]) => void;
  onRemove: (file: WizardFile) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const open = () => inputRef.current?.click();
  return (
    <section
      aria-label="Drop everything here"
      data-testid="wizard-auto-classify"
      className="mb-5"
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        aria-label="Add files for automatic classification"
        onChange={(e) => {
          const list = e.target.files ? Array.from(e.target.files) : [];
          e.target.value = '';
          onAdd(list);
        }}
      />
      <div
        role="button"
        tabIndex={0}
        onClick={open}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            open();
          }
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          const dropped = Array.from(e.dataTransfer.files ?? []);
          if (dropped.length) onAdd(dropped);
        }}
        aria-label="Drop everything here — Fondok sorts each file into its category"
        className={cn(
          'border-2 border-dashed rounded-lg cursor-pointer px-5 py-5 flex items-start gap-4',
          'transition-colors motion-reduce:transition-none',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
          drag
            ? 'border-brand-500 bg-brand-50'
            : 'border-brand-500/40 bg-brand-50/30 hover:border-brand-500 hover:bg-brand-50/60',
        )}
      >
        <UploadCloud size={24} className="text-brand-500 flex-shrink-0 mt-0.5" aria-hidden="true" />
        <div className="min-w-0">
          <div className="text-[13.5px] font-semibold text-ink-900">
            {drag ? 'Drop to add' : 'Drop everything here'}
          </div>
          <p className="text-[12px] text-ink-500 mt-0.5 leading-snug max-w-[680px]">
            The whole data room in one go — Fondok reads each file and assigns it to its
            category. You review and correct every assignment in the Data Room. Prefer to tag
            files yourself? Use the category slots below.
          </p>
          <p className="text-[11px] text-ink-500 mt-1.5">
            {ACCEPTED_FORMATS_LABEL}. {GOOGLE_EXPORT_GUIDANCE}
          </p>
        </div>
      </div>
      {files.length > 0 && (
        <ul
          className="mt-2.5 space-y-1.5"
          role="list"
          aria-label="Files to classify automatically"
        >
          {files.map((f) => (
            <li
              key={dedupeKey(f)}
              className="rounded-md border border-border bg-white px-3 py-2 flex items-center gap-3"
            >
              <FileText size={14} className="text-ink-700 flex-shrink-0" aria-hidden="true" />
              <div className="flex-1 min-w-0">
                <div className="text-[12.5px] font-medium text-ink-900 truncate">{f.file.name}</div>
                <div className="text-[11px] text-ink-500 tabular-nums">{formatBytes(f.file.size)}</div>
              </div>
              <span className="text-[11px] text-ink-500 italic">Category assigned on upload</span>
              <button
                type="button"
                onClick={() => onRemove(f)}
                aria-label={`Remove ${f.file.name}`}
                className="p-1 rounded text-ink-400 hover:text-danger-700 hover:bg-danger-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-500"
              >
                <Trash2 size={13} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ─────────────────────────── content panel ───────────────────────────

function CategoryPanel({
  spec,
  files,
  onAdd,
  onRemove,
  onUpdate,
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
}) {
  const Icon = spec.Icon;
  // Required-for-IC cue lives on the sidebar dot — drop it from the
  // sub-stage header. Description trims to one tight line; the
  // exampleChip carries the concrete "what this looks like".
  return (
    <section aria-label={spec.label}>
      <header className="mb-3">
        <div className="flex items-center gap-2">
          <Icon size={16} className="text-brand-500" aria-hidden="true" />
          <h3 className="text-[14px] font-semibold text-ink-900">
            {spec.label}
          </h3>
        </div>
        <p className="text-[12.5px] text-ink-500 mt-1.5 leading-snug max-w-[640px] line-clamp-2">
          {spec.description}
        </p>
        <div className="mt-2 inline-flex items-center gap-1.5 text-[11px] text-ink-500 italic">
          <span
            className="inline-block w-1 h-1 rounded-full bg-ink-400"
            aria-hidden="true"
          />
          {spec.exampleChip}
        </div>
      </header>

      {/* Financials carry the year-coverage line above the drop zone. */}
      {spec.showYearTagging && <FinancialYearHint files={files} />}

      {spec.id === 'financials' ? (
        <CoachMark
          anchorId="wizard-step3-dropzone-t12"
          viewKey="wizard-step3"
          order={1}
          title="Drop your financial statements here"
          body="T-12, full-year, monthly, or YTD P&Ls — drop them all here and tag each with its type. Fondok extracts USALI line items, scores compliance, and grounds your Year-1 baseline on them."
          side="bottom"
          learnMoreHref="/methodology#extraction"
        >
          <DropZone spec={spec} onFiles={(fs) => onAdd(fs)} />
        </CoachMark>
      ) : (
        <DropZone spec={spec} onFiles={(fs) => onAdd(fs)} />
      )}

      {files.length === 0 ? (
        // Quiet empty-state — one line, no callout. The dropzone above
        // already explains "what to do here".
        <p className="mt-3 text-[11px] text-ink-500">
          Drop {indefiniteArticle(spec.label)} {spec.label} here or skip for now.
        </p>
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
          {drag ? 'Drop to add' : `Drop ${spec.label} here`}
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
        placeholder="detected from the statement"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            commit();
          }
        }}
        title="Leave blank — Fondok reads the year from the statement. Type a year only to override it."
        className="w-48 px-2 py-1 text-[12px] tabular-nums bg-white border border-border rounded-md placeholder:text-ink-400 placeholder:text-[11px] focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
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
