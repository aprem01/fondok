'use client';

/**
 * DocumentsStep — guided per-category onboarding (ROADMAP #1).
 *
 * Replaces the legacy "drag 72 files into one bucket" Step 3 with a
 * four-stage sub-wizard:
 *
 *   3.1 OM (optional, single file)
 *   3.2 Financials by year (REQUIRED, ≥ 1 file) — year cards 2025…2021
 *       chronological with "+ Different year"; per-file pre-categorize
 *       (Annual / T-12, Monthly, Year-to-Date, Not sure).
 *   3.3 STR comp-set reports (optional, multi-file)
 *   3.4 Catch-all bucket (optional, multi-file, no per-file
 *       categorization required)
 *
 * The persistent right-rail DocumentsChecklist lives outside this
 * component — DocumentsStep is the content column. The page wires up
 * both and shares the WizardFile[] state.
 *
 * Sub-stage navigation is internal (a horizontal pill nav) so the
 * wizard-level Next button still gates Step 3 → Step 4 strictly on
 * "financials uploaded". The page receives the canContinue signal via
 * onCanContinueChange.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  Check,
  ChevronRight,
  FileSpreadsheet,
  FileText,
  FolderOpen,
  Info,
  Plus,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';
import type {
  WizardFile,
  WizardCategory,
  WizardUserDocType,
} from '@/lib/api';
import { YearCoverageHint } from './YearCoverageHint';

export interface DocumentsStepProps {
  files: WizardFile[];
  onChange: (files: WizardFile[]) => void;
  onCanContinueChange: (canContinue: boolean) => void;
}

type SubStageId = 'om' | 'financials' | 'str' | 'other';

const SUB_STAGES: { id: SubStageId; label: string; required: boolean }[] = [
  { id: 'om', label: 'OM', required: false },
  { id: 'financials', label: 'Financials', required: true },
  { id: 'str', label: 'STR comps', required: false },
  { id: 'other', label: 'Other', required: false },
];

const FIN_DOC_TYPES: {
  value: WizardUserDocType | '';
  label: string;
  help: string;
}[] = [
  {
    value: 'T12',
    label: 'Annual / T-12',
    help: 'Full-year actuals or trailing twelve months.',
  },
  {
    value: 'PNL_MONTHLY',
    label: 'Monthly',
    help: 'Single-month P&L or detailed month-by-month breakdown.',
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
];

const STR_DOC_TYPES: {
  value: WizardUserDocType | '';
  label: string;
  help: string;
}[] = [
  {
    value: 'STR_TREND',
    label: 'STR Trend',
    help: 'CoStar / STR competitive-set Trend report (subject + comps).',
  },
  {
    value: 'STR',
    label: 'STR Benchmark',
    help: 'Single-period STR benchmark snapshot.',
  },
  {
    value: '',
    label: 'Not sure',
    help: 'Fondok will classify on extraction.',
  },
];

const ACCEPT = '.pdf,.xls,.xlsx,.xlsm,.csv,.doc,.docx,application/pdf';

const dedupeKey = (f: WizardFile) =>
  `${f.file.name}::${f.file.size}::${f.fiscal_year ?? ''}`;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function DocumentsStep({
  files,
  onChange,
  onCanContinueChange,
}: DocumentsStepProps) {
  const [stage, setStage] = useState<SubStageId>('financials');
  const filesByCategory = useMemo(() => {
    const m: Record<WizardCategory, WizardFile[]> = {
      om: [],
      financials: [],
      str: [],
      other: [],
    };
    for (const f of files) m[f.category].push(f);
    return m;
  }, [files]);

  const canContinue = filesByCategory.financials.length > 0;
  useEffect(() => {
    onCanContinueChange(canContinue);
  }, [canContinue, onCanContinueChange]);

  const addFiles = useCallback(
    (
      incoming: File[],
      category: WizardCategory,
      meta: { user_doc_type?: WizardUserDocType | null; fiscal_year?: number | null } = {},
    ) => {
      if (!incoming.length) return;
      const existing = new Set(files.map(dedupeKey));
      const next: WizardFile[] = [...files];
      for (const file of incoming) {
        const candidate: WizardFile = {
          file,
          category,
          user_doc_type: meta.user_doc_type ?? null,
          fiscal_year: meta.fiscal_year ?? null,
        };
        if (existing.has(dedupeKey(candidate))) continue;
        existing.add(dedupeKey(candidate));
        next.push(candidate);
      }
      onChange(next);
    },
    [files, onChange],
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
        files.map((f) =>
          dedupeKey(f) === key ? { ...f, ...patch } : f,
        ),
      );
    },
    [files, onChange],
  );

  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">
        Add documents
      </h2>
      <p className="text-[12.5px] text-ink-500 mb-3">
        Fondok works best when financials are tagged by year. Walk through the
        sections below — financials are required, the rest are optional.
      </p>
      <div className="rounded-md bg-brand-50 border border-brand-100 p-3 text-[12px] text-ink-700 leading-relaxed mb-5">
        Drop documents into the matching section so Fondok can route them to the
        right extractor and the year coverage line stays accurate. You can still
        skip the optional sections — but financials by year are how every model
        gets grounded.
      </div>

      {/* Sub-stage pill nav */}
      <nav
        className="flex items-center gap-1.5 mb-5"
        aria-label="Document categories"
      >
        {SUB_STAGES.map((s, idx) => {
          const count = filesByCategory[s.id].length;
          const active = stage === s.id;
          const done = count > 0;
          const danger = s.required && count === 0;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => setStage(s.id)}
              aria-pressed={active}
              aria-label={`${s.label}${s.required ? ' (required)' : ' (optional)'} · ${count} file${count === 1 ? '' : 's'}`}
              className={cn(
                'inline-flex items-center gap-2 px-3 py-1.5 rounded-md border text-[12px] font-medium',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1',
                'transition-colors motion-reduce:transition-none',
                active
                  ? 'bg-brand-500 text-white border-brand-700 shadow-card'
                  : done
                    ? 'bg-success-50 text-success-700 border-success-500/30 hover:bg-success-100'
                    : danger
                      ? 'bg-danger-50 text-danger-700 border-danger-500/30 hover:bg-danger-100'
                      : 'bg-white text-ink-700 border-border hover:bg-ink-100',
              )}
            >
              <span
                className={cn(
                  'inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] font-semibold tabular-nums',
                  active
                    ? 'bg-white/20'
                    : done
                      ? 'bg-success-500 text-white'
                      : 'bg-ink-300/30',
                )}
                aria-hidden="true"
              >
                {done ? <Check size={9} /> : `3.${idx + 1}`}
              </span>
              {s.label}
              {count > 0 && (
                <span className="text-[11px] tabular-nums opacity-80">
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {stage === 'om' && (
        <OMSubStage
          files={filesByCategory.om}
          onAdd={(fs) => addFiles(fs, 'om', { user_doc_type: 'OM' })}
          onRemove={removeAt}
        />
      )}
      {stage === 'financials' && (
        <FinancialsSubStage
          files={filesByCategory.financials}
          onAdd={(fs, year, type) =>
            addFiles(fs, 'financials', {
              user_doc_type: type ?? null,
              fiscal_year: year ?? null,
            })
          }
          onRemove={removeAt}
          onUpdate={updateFile}
        />
      )}
      {stage === 'str' && (
        <STRSubStage
          files={filesByCategory.str}
          onAdd={(fs, type) => addFiles(fs, 'str', { user_doc_type: type ?? null })}
          onRemove={removeAt}
          onUpdate={updateFile}
        />
      )}
      {stage === 'other' && (
        <OtherSubStage
          files={filesByCategory.other}
          onAdd={(fs) => addFiles(fs, 'other')}
          onRemove={removeAt}
        />
      )}

      {/* Stage navigation */}
      <div className="mt-6 flex items-center justify-between">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => {
            const idx = SUB_STAGES.findIndex((s) => s.id === stage);
            if (idx > 0) setStage(SUB_STAGES[idx - 1].id);
          }}
          disabled={SUB_STAGES[0].id === stage}
          aria-label="Previous category"
        >
          <ArrowLeft size={12} aria-hidden="true" /> Previous
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            const idx = SUB_STAGES.findIndex((s) => s.id === stage);
            if (idx < SUB_STAGES.length - 1) setStage(SUB_STAGES[idx + 1].id);
          }}
          disabled={SUB_STAGES[SUB_STAGES.length - 1].id === stage}
          aria-label="Next category"
        >
          Next category <ChevronRight size={12} aria-hidden="true" />
        </Button>
      </div>

      {!canContinue && (
        <div className="mt-4 px-3 py-2 rounded-md bg-warn-50 border border-warn-500/30 text-[12px] text-warn-700 flex items-center gap-2">
          <Info size={13} aria-hidden="true" />
          Add at least one financial to continue. Year tagging is optional, but
          recommended — it powers the year-coverage line and gap detection.
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── shared dropzone ───────────────────────────

function DropZone({
  onFiles,
  multiple = true,
  hint,
  label,
  compact = false,
  inputId,
}: {
  onFiles: (files: File[]) => void;
  multiple?: boolean;
  hint: string;
  label: string;
  compact?: boolean;
  inputId: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDrag(false);
    const dropped = Array.from(e.dataTransfer.files ?? []);
    if (dropped.length === 0) return;
    onFiles(multiple ? dropped : dropped.slice(0, 1));
  };
  const onClick = () => inputRef.current?.click();
  return (
    <div>
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        multiple={multiple}
        accept={ACCEPT}
        className="hidden"
        aria-label={label}
        onChange={(e) => {
          const list = e.target.files ? Array.from(e.target.files) : [];
          e.target.value = '';
          onFiles(multiple ? list : list.slice(0, 1));
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
        aria-label={label}
        className={cn(
          'border-2 border-dashed rounded-lg text-center cursor-pointer',
          'transition-colors motion-reduce:transition-none',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
          compact ? 'py-5 px-4' : 'py-8 px-5',
          drag
            ? 'border-brand-500 bg-brand-50'
            : 'border-ink-300 hover:border-brand-500 hover:bg-brand-50/40',
        )}
      >
        <UploadCloud
          size={compact ? 22 : 28}
          className="text-ink-400 mx-auto mb-2"
          aria-hidden="true"
        />
        <div className="text-[13px] font-medium text-ink-900">
          {drag ? 'Drop to add' : label}
        </div>
        <div className="text-[11.5px] text-ink-500 mt-1">{hint}</div>
      </div>
    </div>
  );
}

// ─────────────────────────── OM sub-stage ───────────────────────────

function OMSubStage({
  files,
  onAdd,
  onRemove,
}: {
  files: WizardFile[];
  onAdd: (files: File[]) => void;
  onRemove: (file: WizardFile) => void;
}) {
  return (
    <section aria-label="Offering memorandum upload">
      <header className="mb-3">
        <div className="flex items-center gap-2">
          <FileText size={14} className="text-brand-500" aria-hidden="true" />
          <h3 className="text-[15px] font-semibold text-ink-900">
            Offering Memorandum
          </h3>
          <Badge tone="gray">Optional</Badge>
        </div>
        <p className="text-[12px] text-ink-500 mt-1 leading-relaxed">
          The broker&rsquo;s pitch deck. Fondok extracts property metadata (keys,
          brand, year built, address) and the broker&rsquo;s pro forma. You can
          skip this — every field is editable later.
        </p>
      </header>
      {files.length === 0 ? (
        <DropZone
          inputId="wizard-om-drop"
          onFiles={onAdd}
          multiple={false}
          label="Drop the OM here"
          hint="One file · PDF or Word. Click to browse."
        />
      ) : (
        <ul className="space-y-2" role="list" aria-label="Selected OM file">
          {files.map((f) => (
            <FileRow key={dedupeKey(f)} file={f} onRemove={onRemove} />
          ))}
        </ul>
      )}
    </section>
  );
}

// ─────────────────────────── Financials sub-stage ───────────────────────────

function FinancialsSubStage({
  files,
  onAdd,
  onRemove,
  onUpdate,
}: {
  files: WizardFile[];
  onAdd: (
    files: File[],
    year: number | null,
    docType: WizardUserDocType | null,
  ) => void;
  onRemove: (file: WizardFile) => void;
  onUpdate: (file: WizardFile, patch: Partial<WizardFile>) => void;
}) {
  // Default year window: current year and 4 prior — chronological newest first.
  const defaultYears = useMemo(() => {
    const now = new Date().getUTCFullYear();
    return [now, now - 1, now - 2, now - 3, now - 4];
  }, []);
  const [customYears, setCustomYears] = useState<number[]>([]);
  const [adding, setAdding] = useState(false);
  const [pendingYearInput, setPendingYearInput] = useState('');

  const filesByYear = useMemo(() => {
    const m = new Map<number | 'unspecified', WizardFile[]>();
    for (const f of files) {
      const key = f.fiscal_year ?? 'unspecified';
      const list = m.get(key) ?? [];
      list.push(f);
      m.set(key, list);
    }
    return m;
  }, [files]);

  const coveredYears = useMemo(
    () =>
      new Set(
        files
          .map((f) => f.fiscal_year)
          .filter((y): y is number => typeof y === 'number'),
      ),
    [files],
  );

  // Sort: default years (newest first) → custom years (newest first).
  const visibleYears = useMemo(() => {
    const fromFiles = Array.from(coveredYears);
    const all = new Set<number>([...defaultYears, ...customYears, ...fromFiles]);
    return Array.from(all).sort((a, b) => b - a);
  }, [defaultYears, customYears, coveredYears]);

  const handleAddCustomYear = () => {
    const n = Number.parseInt(pendingYearInput, 10);
    if (Number.isFinite(n) && n >= 1900 && n <= 2100) {
      if (!visibleYears.includes(n)) {
        setCustomYears((c) => [...c, n]);
      }
      setPendingYearInput('');
      setAdding(false);
    }
  };

  return (
    <section aria-label="Financials by year">
      <header className="mb-3">
        <div className="flex items-center gap-2">
          <FileSpreadsheet
            size={14}
            className="text-brand-500"
            aria-hidden="true"
          />
          <h3 className="text-[15px] font-semibold text-ink-900">
            Financials by year
          </h3>
          <Badge tone="red">Required</Badge>
        </div>
        <p className="text-[12px] text-ink-500 mt-1 leading-relaxed">
          Drop a P&amp;L into the year it represents. We&rsquo;ll route each one
          to the right extractor — annual T-12s, monthly P&amp;Ls, and YTD rolls
          all live here.
        </p>
      </header>

      <div className="mb-4">
        <YearCoverageHint
          coveredYears={coveredYears}
          years={visibleYears.slice().sort((a, b) => a - b)}
        />
      </div>

      <div className="space-y-3">
        {visibleYears.map((year) => (
          <YearCard
            key={year}
            year={year}
            files={filesByYear.get(year) ?? []}
            onAdd={(fs, type) => onAdd(fs, year, type)}
            onRemove={onRemove}
            onUpdate={onUpdate}
          />
        ))}
        {(filesByYear.get('unspecified') ?? []).length > 0 && (
          <UnspecifiedYearCard
            files={filesByYear.get('unspecified') ?? []}
            onRemove={onRemove}
            onUpdate={onUpdate}
          />
        )}
      </div>

      <div className="mt-4 flex items-center justify-between">
        {adding ? (
          <div className="flex items-center gap-2">
            <label htmlFor="wizard-custom-year" className="sr-only">
              Custom year
            </label>
            <input
              id="wizard-custom-year"
              type="number"
              min={1900}
              max={2100}
              autoFocus
              value={pendingYearInput}
              onChange={(e) => setPendingYearInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleAddCustomYear();
                if (e.key === 'Escape') {
                  setAdding(false);
                  setPendingYearInput('');
                }
              }}
              placeholder="2018"
              className="w-24 px-2 py-1 text-[12.5px] tabular-nums bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
            />
            <Button
              size="sm"
              variant="primary"
              onClick={handleAddCustomYear}
              aria-label="Add custom year"
            >
              Add
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setAdding(false);
                setPendingYearInput('');
              }}
              aria-label="Cancel adding custom year"
            >
              Cancel
            </Button>
          </div>
        ) : (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setAdding(true)}
            aria-label="Add a different year"
          >
            <Plus size={12} aria-hidden="true" /> Different year
          </Button>
        )}
        <UntaggedDropZone
          onFiles={(fs) => onAdd(fs, null, null)}
        />
      </div>
    </section>
  );
}

function YearCard({
  year,
  files,
  onAdd,
  onRemove,
  onUpdate,
}: {
  year: number;
  files: WizardFile[];
  onAdd: (files: File[], type: WizardUserDocType | null) => void;
  onRemove: (file: WizardFile) => void;
  onUpdate: (file: WizardFile, patch: Partial<WizardFile>) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDrag(false);
    const dropped = Array.from(e.dataTransfer.files ?? []);
    if (dropped.length === 0) return;
    onAdd(dropped, null);
  };
  const covered = files.length > 0;
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDrag(true);
      }}
      onDragLeave={() => setDrag(false)}
      onDrop={onDrop}
      className={cn(
        'rounded-md border bg-white transition-colors motion-reduce:transition-none',
        drag
          ? 'border-brand-500 bg-brand-50/60'
          : covered
            ? 'border-success-500/30'
            : 'border-border',
      )}
    >
      <div className="flex items-center justify-between px-4 py-2.5 border-b border-border">
        <div className="flex items-center gap-2.5">
          <span
            className={cn(
              'inline-flex items-center justify-center w-7 h-7 rounded-md text-[12px] font-semibold tabular-nums',
              covered
                ? 'bg-success-50 text-success-700 border border-success-500/30'
                : 'bg-ink-100 text-ink-700 border border-border',
            )}
            aria-hidden="true"
          >
            {String(year).slice(-2)}
          </span>
          <div>
            <div className="text-[13px] font-semibold text-ink-900 tabular-nums">
              {year}
            </div>
            <div className="text-[11px] text-ink-500">
              {covered
                ? `${files.length} file${files.length === 1 ? '' : 's'} staged`
                : 'No financials yet'}
            </div>
          </div>
        </div>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          aria-label={`Add financials for ${year}`}
          onChange={(e) => {
            const list = e.target.files ? Array.from(e.target.files) : [];
            e.target.value = '';
            if (list.length) onAdd(list, null);
          }}
        />
        <Button
          size="sm"
          variant={covered ? 'secondary' : 'primary'}
          onClick={() => inputRef.current?.click()}
          aria-label={`Drop a P&L for ${year}`}
        >
          <UploadCloud size={11} aria-hidden="true" />
          {covered ? 'Add more' : 'Drop P&L'}
        </Button>
      </div>
      {covered && (
        <ul className="divide-y divide-border" role="list">
          {files.map((f) => (
            <li key={dedupeKey(f)}>
              <FinancialFileRow
                file={f}
                onRemove={onRemove}
                onUpdate={onUpdate}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UnspecifiedYearCard({
  files,
  onRemove,
  onUpdate,
}: {
  files: WizardFile[];
  onRemove: (file: WizardFile) => void;
  onUpdate: (file: WizardFile, patch: Partial<WizardFile>) => void;
}) {
  return (
    <div className="rounded-md border border-warn-500/30 bg-warn-50/50">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-warn-500/20">
        <Info size={13} className="text-warn-700" aria-hidden="true" />
        <div className="text-[12.5px] font-semibold text-warn-700">
          Year not set
        </div>
        <div className="text-[11px] text-warn-700/80">
          Pick a year so the coverage line stays accurate.
        </div>
      </div>
      <ul className="divide-y divide-warn-500/20" role="list">
        {files.map((f) => (
          <li key={dedupeKey(f)}>
            <FinancialFileRow
              file={f}
              onRemove={onRemove}
              onUpdate={onUpdate}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}

function UntaggedDropZone({
  onFiles,
}: {
  onFiles: (files: File[]) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        aria-label="Drop financials without a year tag"
        onChange={(e) => {
          const list = e.target.files ? Array.from(e.target.files) : [];
          e.target.value = '';
          if (list.length) onFiles(list);
        }}
      />
      <Button
        size="sm"
        variant="ghost"
        onClick={() => inputRef.current?.click()}
        aria-label="Drop a financial without picking a year"
      >
        Skip year tagging…
      </Button>
    </>
  );
}

function FinancialFileRow({
  file,
  onRemove,
  onUpdate,
}: {
  file: WizardFile;
  onRemove: (file: WizardFile) => void;
  onUpdate: (file: WizardFile, patch: Partial<WizardFile>) => void;
}) {
  return (
    <div className="px-4 py-2.5 flex items-center gap-3">
      <FileSpreadsheet
        size={14}
        className="text-success-700 flex-shrink-0"
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
      <label className="sr-only" htmlFor={`type-${dedupeKey(file)}`}>
        Financial type
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
        className="px-2 py-1 text-[12px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
        aria-label={`Set financial type for ${file.file.name}`}
      >
        {FIN_DOC_TYPES.map((t) => (
          <option key={t.label} value={t.value} title={t.help}>
            {t.label}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => onRemove(file)}
        className="p-1 rounded text-ink-400 hover:text-danger-700 hover:bg-danger-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-500"
        aria-label={`Remove ${file.file.name}`}
      >
        <Trash2 size={13} aria-hidden="true" />
      </button>
    </div>
  );
}

// ─────────────────────────── STR sub-stage ───────────────────────────

function STRSubStage({
  files,
  onAdd,
  onRemove,
  onUpdate,
}: {
  files: WizardFile[];
  onAdd: (files: File[], type: WizardUserDocType | null) => void;
  onRemove: (file: WizardFile) => void;
  onUpdate: (file: WizardFile, patch: Partial<WizardFile>) => void;
}) {
  return (
    <section aria-label="STR comp-set reports">
      <header className="mb-3">
        <div className="flex items-center gap-2">
          <FileText size={14} className="text-brand-500" aria-hidden="true" />
          <h3 className="text-[15px] font-semibold text-ink-900">
            STR / Comp-set reports
          </h3>
          <Badge tone="gray">Optional</Badge>
        </div>
        <p className="text-[12px] text-ink-500 mt-1 leading-relaxed">
          CoStar STR Trend exports or comparable benchmarks. These feed the
          comp-set drift detector and the market tab.
        </p>
      </header>
      <DropZone
        inputId="wizard-str-drop"
        onFiles={(fs) => onAdd(fs, 'STR_TREND')}
        label="Drop STR exports"
        hint="Multiple files welcome · .xls / .xlsx / PDF."
        compact={files.length > 0}
      />
      {files.length > 0 && (
        <ul
          className="mt-3 space-y-2"
          role="list"
          aria-label="Selected STR files"
        >
          {files.map((f) => (
            <li key={dedupeKey(f)}>
              <STRFileRow file={f} onRemove={onRemove} onUpdate={onUpdate} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function STRFileRow({
  file,
  onRemove,
  onUpdate,
}: {
  file: WizardFile;
  onRemove: (file: WizardFile) => void;
  onUpdate: (file: WizardFile, patch: Partial<WizardFile>) => void;
}) {
  return (
    <div className="rounded-md border border-border bg-white px-3 py-2.5 flex items-center gap-3">
      <FileText size={14} className="text-ink-700 flex-shrink-0" aria-hidden="true" />
      <div className="flex-1 min-w-0">
        <div className="text-[12.5px] font-medium text-ink-900 truncate">
          {file.file.name}
        </div>
        <div className="text-[11px] text-ink-500 tabular-nums">
          {formatBytes(file.file.size)}
        </div>
      </div>
      <select
        value={file.user_doc_type ?? ''}
        onChange={(e) =>
          onUpdate(file, {
            user_doc_type: (e.target.value as WizardUserDocType) || null,
          })
        }
        aria-label={`Set STR type for ${file.file.name}`}
        className="px-2 py-1 text-[12px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
      >
        {STR_DOC_TYPES.map((t) => (
          <option key={t.label} value={t.value} title={t.help}>
            {t.label}
          </option>
        ))}
      </select>
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

// ─────────────────────────── Other sub-stage ───────────────────────────

function OtherSubStage({
  files,
  onAdd,
  onRemove,
}: {
  files: WizardFile[];
  onAdd: (files: File[]) => void;
  onRemove: (file: WizardFile) => void;
}) {
  return (
    <section aria-label="Other documents">
      <header className="mb-3">
        <div className="flex items-center gap-2">
          <FolderOpen size={14} className="text-brand-500" aria-hidden="true" />
          <h3 className="text-[15px] font-semibold text-ink-900">
            Other documents
          </h3>
          <Badge tone="gray">Optional</Badge>
        </div>
        <p className="text-[12px] text-ink-500 mt-1 leading-relaxed">
          Catch-all bucket for anything that doesn&rsquo;t fit the categories
          above — PIP reports, leases, room mix lookups, CBRE Horizons forecasts,
          benchmark P&amp;Ls. No per-file tagging required; Fondok classifies on
          extraction.
        </p>
      </header>
      <DropZone
        inputId="wizard-other-drop"
        onFiles={onAdd}
        label="Drop anything else here"
        hint="Multiple files welcome · all supported formats."
        compact={files.length > 0}
      />
      {files.length > 0 && (
        <ul
          className="mt-3 space-y-2"
          role="list"
          aria-label="Selected other files"
        >
          {files.map((f) => (
            <FileRow key={dedupeKey(f)} file={f} onRemove={onRemove} />
          ))}
        </ul>
      )}
    </section>
  );
}

// ─────────────────────────── Generic file row ───────────────────────────

function FileRow({
  file,
  onRemove,
}: {
  file: WizardFile;
  onRemove: (file: WizardFile) => void;
}) {
  return (
    <li
      className="rounded-md border border-border bg-white px-3 py-2.5 flex items-center gap-3"
      role="listitem"
    >
      <FileText
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
      <button
        type="button"
        onClick={() => onRemove(file)}
        aria-label={`Remove ${file.file.name}`}
        className="p-1 rounded text-ink-400 hover:text-danger-700 hover:bg-danger-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-danger-500"
      >
        <X size={13} aria-hidden="true" />
      </button>
    </li>
  );
}
