'use client';

/**
 * GroundedWorksheet — the "editable Excel, grounded" worksheet.
 *
 * A multi-year USALI operating statement where:
 *   • Historical year columns are GROUNDED FACTS pulled from the deal's
 *     extracted P&Ls (read-only here — you correct history at its source
 *     document, via the click-to-source panel → Review).
 *   • The "Year 1 · Model" column is your ASSUMPTION LAYER — expense-actual
 *     lines are inline-editable, GOP & NOI recompute live, and edits persist
 *     as tracked field_overrides that re-model the whole deal.
 *
 * Every cell carries its provenance (🟢 grounded / 🟡 seed-benchmark /
 * 🟣 override / 🔵 computed) and opens a source panel showing the exact
 * document, extracted line, page, and confidence it came from.
 *
 * Historical detail (A&G, insurance, mgmt fee, …) is often not broken out in
 * the source P&Ls — those cells render "—" while the Undistributed / Fixed
 * SUBTOTALS still reconcile against the historical aggregate. That's honest:
 * we show the totals we have and the Y1 detail you can edit.
 */

import { useMemo, useState, useEffect, useRef, useCallback } from 'react';
import type { ReactNode } from 'react';
import { useSearchParams } from 'next/navigation';
import {
  Loader2, RotateCcw, X, FileText, Info, SlidersHorizontal, Search, AlertTriangle,
  EyeOff, Eye, ChevronUp, ChevronDown, Plus, Scissors, Trash2, Check,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { cn, fmtCurrency } from '@/lib/format';
import { useToast } from '@/components/ui/Toast';
import { api } from '@/lib/api';
import type { ExtractionField, ExtractionResult, WorkerDocument, ValueState } from '@/lib/api';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useTrace } from '@/lib/hooks/useValueTrace';
import { ProvenanceDot, NO_OP_EDIT_MESSAGE } from '@/components/design';
import { isNoOpEdit } from '@/lib/fieldValue';
import { useDeal } from '@/lib/hooks/useDeal';
import { useDocuments } from '@/lib/hooks/useDocuments';
import { isHistoricalSourceDoc, useHistoricals } from '@/lib/hooks/useHistoricals';
import TabLoadingSkeleton from '@/components/project/TabLoadingSkeleton';
import { baseYearLabel, type HistYear, type PeriodBasis } from '@/components/project/pl/HistoricalsSection';
import { buildReviewState, cellKey, cellsForYear, histHasData, histValue, type ReviewRow } from '@/lib/reviewState';
import { useSource } from '@/lib/hooks/useDealProvenance';
import { sourceKind, sourceExplanation, formatPeriodBasis } from '@/lib/provenance';
import { useWorksheetLayout } from '@/lib/hooks/useWorksheetLayout';
import type { SplitChild, CuratedLine } from '@/lib/hooks/useWorksheetLayout';
import { worksheetBinding } from '@/lib/ontology/adapters';

// ── Row model ──────────────────────────────────────────────────────────
// Historical values are mapped by row id in histValue(); overrideKey (present
// → editable in the Model column) is the canonical field_overrides key.
type RowKind = 'section' | 'input' | 'subtotal' | 'computed';
type RowFmt = 'currency' | 'pct' | 'dollar';
interface RowDef {
  id: string;
  label: string;
  kind: RowKind;
  overrideKey?: string;    // editable model-column key
  reviewKey?: string;      // canonical extraction field for read-only rows (revenue) — reviewable at source
  metaKey?: string;        // HistYear.meta key — per-year source/confidence for historical-cell review flags
  y1Read?: string[];       // dotted path into the y1Src engine's years[0]
  y1Src?: 'expense' | 'fb' | 'revenue'; // which engine feeds the Model column (default expense)
  fmt?: RowFmt;            // cell number format (default currency)
  compute?: (v: Record<string, number>) => number; // model-col derived value
}

// Cell number format. Occupancy is a 0..1 ratio shown as %; ADR/RevPAR are
// plain dollars-per-unit; everything else is compact currency.
function fmtRowValue(v: number, fmt: RowFmt | undefined): string {
  if (fmt === 'pct') return `${(v * 100).toFixed(1)}%`;
  if (fmt === 'dollar') return `$${Math.round(v).toLocaleString()}`;
  return fmtCurrency(v, { compact: true });
}

const sumKeys = (v: Record<string, number>, keys: string[]) =>
  keys.reduce((s, k) => s + (v[k] ?? 0), 0);

const UNDIST_IDS = ['ag', 'sm', 'pom', 'util', 'it'];
const FIXED_FEE_IDS = ['mgmt', 'ffe', 'taxes', 'insurance'];
const DEPT_IDS = ['rooms_dept', 'fb_dept', 'other_dept'];

// metaKey (FON-41 Part B): every line ``buildHistYear`` resolves from a P&L
// carries its per-year source meta under this key, so ANY extracted historical
// cell — revenue, expense, subtotal or NOI — can flag low confidence and open
// the SOURCE panel at its own statement. Lines the P&Ls don't break out
// (A&G, S&M, FF&E, …) have no metaKey and render "—" in historical columns.
//
// Phase 1.4: a row's KEYS (overrideKey / reviewKey / metaKey / y1Read / y1Src /
// fmt) come from the generated registry — ``CONCEPTS[id].bindings.worksheet``,
// via ``ws()`` below — so the field_overrides key, the HistYear.meta key and
// the engine path can never drift from the worker again. What stays
// hand-authored here is LAYOUT: which rows exist, in what ORDER, under which
// section, with which label, and how a subtotal/computed row is computed.
/** Registry-bound keys for one worksheet row. Emits a key only when the
 *  registry binds one, so an unbound row keeps `undefined` (``y1Read`` is
 *  read as a truthiness test — an empty array would change behaviour). */
function ws(row: string): Pick<RowDef, 'id' | 'overrideKey' | 'reviewKey' | 'metaKey' | 'y1Read' | 'y1Src' | 'fmt'> {
  const b = worksheetBinding(row);
  return {
    id: row,
    ...(b.override_key ? { overrideKey: b.override_key } : {}),
    ...(b.review_key ? { reviewKey: b.review_key } : {}),
    ...(b.meta_key ? { metaKey: b.meta_key } : {}),
    ...(b.y1_read.length ? { y1Read: [...b.y1_read] } : {}),
    ...(b.y1_src ? { y1Src: b.y1_src } : {}),
    // 'currency' is the RowDef default — leaving it undefined keeps the row
    // objects identical to the hand-written table they replace.
    ...(b.fmt !== 'currency' ? { fmt: b.fmt } : {}),
  };
}

const ROWS: RowDef[] = [
  { id: 's_ops', label: 'Operating Statistics', kind: 'section' },
  { ...ws('occ'), label: 'Occupancy', kind: 'input' },
  { ...ws('adr'), label: 'ADR', kind: 'input' },
  { ...ws('revpar'), label: 'RevPAR', kind: 'input' },

  { id: 's_rev', label: 'Revenue', kind: 'section' },
  { ...ws('rooms_rev'), label: 'Rooms Revenue', kind: 'input' },
  { ...ws('fb_rev'), label: 'Food & Beverage Revenue', kind: 'input' },
  { ...ws('other_rev'), label: 'Other Revenue', kind: 'input' },
  { ...ws('total_rev'), label: 'Total Revenue', kind: 'subtotal',
    compute: (v) => v.rooms_rev + v.fb_rev + v.other_rev },

  { id: 's_dept', label: 'Departmental Expenses', kind: 'section' },
  { ...ws('rooms_dept'), label: 'Rooms', kind: 'input' },
  { ...ws('fb_dept'), label: 'Food & Beverage', kind: 'input' },
  { ...ws('other_dept'), label: 'Other Operated', kind: 'input' },

  { id: 's_undist', label: 'Undistributed Operating Expenses', kind: 'section' },
  { ...ws('ag'), label: 'Administrative & General', kind: 'input' },
  { ...ws('sm'), label: 'Sales & Marketing', kind: 'input' },
  { ...ws('pom'), label: 'Property Operations', kind: 'input' },
  { ...ws('util'), label: 'Utilities', kind: 'input' },
  { ...ws('it'), label: 'Information & Telecom', kind: 'input' },
  { ...ws('undist_total'), label: 'Total Undistributed', kind: 'subtotal',
    compute: (v) => sumKeys(v, UNDIST_IDS) },

  { ...ws('gop'), label: 'Gross Operating Profit (GOP)', kind: 'computed',
    compute: (v) => v.total_rev - sumKeys(v, DEPT_IDS) - sumKeys(v, UNDIST_IDS) },

  { id: 's_fixed', label: 'Management Fee & Fixed Charges', kind: 'section' },
  { ...ws('mgmt'), label: 'Management Fee', kind: 'input' },
  { ...ws('ffe'), label: 'FF&E Reserve', kind: 'input' },
  { ...ws('taxes'), label: 'Property Taxes', kind: 'input' },
  { ...ws('insurance'), label: 'Insurance', kind: 'input' },
  { ...ws('fixed_total'), label: 'Total Fees & Fixed', kind: 'subtotal',
    compute: (v) => sumKeys(v, FIXED_FEE_IDS) },

  { ...ws('noi'), label: 'Net Operating Income (NOI)', kind: 'computed',
    compute: (v) => v.total_rev - sumKeys(v, DEPT_IDS) - sumKeys(v, UNDIST_IDS) - sumKeys(v, FIXED_FEE_IDS) },
];

// Input (editable) member rows per section, and a row lookup — used by the
// presentation layer (reorder / hide / add-curated / split) to keep canonical
// subtotals + computed rows anchored while detail lines stay flexible.
const SECTION_INPUTS: Record<string, string[]> = (() => {
  const out: Record<string, string[]> = {};
  let cur = '';
  for (const r of ROWS) {
    if (r.kind === 'section') { cur = r.id; out[cur] = []; }
    else if (r.kind === 'input') out[cur]?.push(r.id);
  }
  return out;
})();
const INPUT_BY_ID: Record<string, RowDef> = Object.fromEntries(
  ROWS.filter((r) => r.kind === 'input').map((r) => [r.id, r]),
);

const num = (x: unknown): number => (typeof x === 'number' && Number.isFinite(x) ? x : 0);

// Historical cell values (histValue) and the "year earns a column" filter
// (histHasData) come from lib/reviewState — shared with the review predicate
// and the Data Room so "renders a cell" and "can be flagged" never drift.

// FON-26: historical-coverage strip. The worksheet drops years that carry no
// data, which left analysts unable to tell a missing year apart from one that
// is still extracting or that failed. This surfaces every financial year the
// deal touches with an explicit status instead of a silent gap.
type CoverState = 'uploaded' | 'processing' | 'failed' | 'gap';

// P&L family that represents a specific year of actuals (benchmark/comp docs
// excluded — they don't stand in for an operating year).
const isFinancialPnlDoc = (d: WorkerDocument) => {
  const dt = (d.doc_type ?? '').toUpperCase();
  return (
    dt.includes('T12') || dt === 'T-12' || dt === 'PNL' || dt === 'P&L' ||
    dt === 'PNL_MONTHLY' || dt === 'PNL_YTD' || dt.includes('PROFIT')
  );
};

const coverStateFromStatus = (status: string): CoverState => {
  if (status === 'EXTRACTED') return 'uploaded';
  if (status === 'FAILED' || status === 'PARSE_FAILED') return 'failed';
  return 'processing'; // UPLOADED / PARSING / CLASSIFYING / EXTRACTING
};

const COVER_RANK: Record<CoverState, number> = { uploaded: 3, processing: 2, failed: 1, gap: 0 };
const COVER_TONE: Record<CoverState, string> = {
  uploaded: 'border-emerald-500/30 bg-emerald-50 text-emerald-700',
  processing: 'border-amber-500/30 bg-amber-50 text-amber-700',
  failed: 'border-danger-500/30 bg-danger-50 text-danger-700',
  gap: 'border-ink-300 bg-ink-100/40 text-ink-500',
};
const COVER_DOT: Record<CoverState, string> = {
  uploaded: 'bg-emerald-500',
  processing: 'bg-amber-500 animate-pulse',
  failed: 'bg-danger-500',
  gap: 'bg-ink-300',
};
const COVER_TITLE: Record<CoverState, string> = {
  uploaded: 'Uploaded — this year’s P&L is extracted and feeding the model.',
  processing: 'Processing — a statement for this year is still extracting.',
  failed: 'Extraction failed — re-upload or open this year’s statement to retry.',
  gap: 'Not uploaded — no statement for this year in the operating history.',
};

// Build the ordered year → status list from the deal's financial docs, folding
// in years already populated in the worksheet (incl. OM-embedded history) and
// filling interior gaps so a missing middle year reads as "not uploaded".
function buildCoverage(
  docs: WorkerDocument[],
  populatedYears: HistYear[],
): { year: string; state: CoverState; label: string }[] {
  const byYear = new Map<string, CoverState>();
  const bump = (yr: string, s: CoverState) => {
    const prev = byYear.get(yr);
    if (!prev || COVER_RANK[s] > COVER_RANK[prev]) byYear.set(yr, s);
  };
  for (const d of docs) {
    if (!isFinancialPnlDoc(d)) continue;
    const dt = (d.doc_type ?? '').toUpperCase();
    const isT12 = dt.includes('T12') || dt === 'T-12';
    const yr = isT12 ? 'T-12' : String(d.fiscal_year ?? d.extracted_period_year ?? '').trim();
    if (!yr) continue;
    bump(yr, coverStateFromStatus(d.status));
  }
  // A year that made it into the grid is uploaded regardless of doc source
  // (an OM-embedded P&L has no standalone financial doc of its own).
  for (const y of populatedYears) {
    const base = baseYearLabel(y.year); // "2023 (2)" still counts as 2023 coverage
    if (/^\d{4}$/.test(base) || base === 'T-12') bump(base, 'uploaded');
  }
  const numeric = [...byYear.keys()].filter((y) => /^\d{4}$/.test(y)).map(Number).sort((a, b) => a - b);
  if (numeric.length >= 2) {
    for (let y = numeric[0]; y <= numeric[numeric.length - 1]; y++) {
      if (!byYear.has(String(y))) byYear.set(String(y), 'gap');
    }
  }
  const order = [...byYear.keys()].sort((a, b) => {
    if (a === 'T-12') return 1;
    if (b === 'T-12') return -1;
    return Number(a) - Number(b);
  });
  // FON-41 #4 — the chip says what the period IS ("T12 Mar 2025", "FY2024")
  // whenever exactly one column covers that year. The COUNTING above is
  // unchanged and still keys on the bare year, so a "YTD Mar 2025" column
  // still counts as 2025 coverage and interior gaps still fill.
  const labelByYear = new Map<string, string>();
  for (const y of populatedYears) {
    const base = baseYearLabel(y.year);
    const prev = labelByYear.get(base);
    if (prev === undefined) labelByYear.set(base, y.periodLabel);
    else if (prev !== y.periodLabel) labelByYear.set(base, base); // two statements disagree — stay neutral
  }
  return order.map((year) => ({ year, state: byYear.get(year)!, label: labelByYear.get(year) ?? year }));
}

// What the Period control can select. ``ALL`` is the DEFAULT and is not a
// basis: it is "don't filter". It exists because filtering by default would
// hide a low-confidence statement whose "N to review" badge the Data Room is
// still showing — the exact badge-vs-grid split FON-41 closed, which
// ``evidenceAblation`` guards. The analyst opts into a basis.
type PeriodFilter = PeriodBasis | 'ALL';

// The three bases the MVP models, in the canonical Financials order. The
// wording is the design's ("Trailing 12 (month-end)"); the ids are the
// resolved ``HistYear.periodBasis``.
const PERIOD_OPTIONS: { id: PeriodBasis; label: string }[] = [
  { id: 'FY', label: 'Full Year' },
  { id: 'YTD', label: 'Year-to-date' },
  { id: 'T12', label: 'Trailing 12 (month-end)' },
];
// Not MVP bases — offered ONLY when a statement actually resolved to one, so
// a monthly or unstated-basis column is never left unreachable behind the
// filter. (Annual / Monthly GRANULARITY is the separate toggle below.)
const EXTRA_BASIS_OPTIONS: { id: PeriodBasis; label: string }[] = [
  { id: 'MONTHLY', label: 'Single month' },
  { id: 'UNKNOWN', label: 'Basis not stated' },
];

interface InspectTarget {
  rowLabel: string;
  colLabel: string;
  kind: 'grounded' | 'benchmark' | 'override' | 'computed';
  value: number;
  overrideKey?: string;
  reviewKey?: string;
  docIds: string[];
  formula?: string;
  review?: { docId: string; field: string; confidence: number };
  fmt?: RowFmt;
  /** FON-41 #4 — the column's period, already formatted ("T-12 ending
   *  Mar 31, 2025"). Absent on the Model column and on columns whose basis
   *  could not be established. */
  periodText?: string | null;
}

type RenderItem =
  | { type: 'section'; id: string; label: string }
  | { type: 'anchor'; row: RowDef }
  | { type: 'input'; row: RowDef; sectionId: string; siblings: string[]; splitDelta: number | null }
  | { type: 'split'; parentId: string; child: SplitChild }
  | { type: 'curated'; line: CuratedLine };

export default function GroundedWorksheet({
  dealId,
}: {
  dealId: string | number;
}) {
  const rawId = String(dealId);
  const { outputs, refresh, settled: outputsSettled } = useEngineOutputs(rawId);
  const { deal, error: dealError, refresh: refreshDeal } = useDeal(rawId);
  const { documents, extractions, refreshExtraction, settled: documentsSettled } = useDocuments(rawId);
  const { toast } = useToast();
  const { run, status } = useEngineRun(rawId, 'returns', { runMode: 'all' });
  const running = status === 'running' || status === 'queued';

  const searchParams = useSearchParams();
  // FON-41 §3 — structure editing (add / move / rename / hide / split) is an
  // analyst affordance again. It is PRESENTATION-ONLY, but it is a DEAL
  // artifact, not a per-browser preference: the layout persists to
  // ``field_overrides.worksheet_layout`` so every reviewer opens the same
  // statement. It still never reaches the engines.
  const wl = useWorksheetLayout(rawId, {
    deal,
    onError: (msg) => toast(msg, { type: 'error' }),
    // Keep the deal row current so the next layout PATCH merges onto the
    // overrides the worker actually holds, not a stale copy.
    onSaved: () => { void refreshDeal?.(); },
  });
  const [customize, setCustomize] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [inspect, setInspect] = useState<InspectTarget | null>(null);
  // Design rewire: year-pill filtering + line-item search.
  const [hiddenYears, setHiddenYears] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');
  // Canonical Historicals toolbar toggles (design/canonical/Financials Tab.dc.html):
  //   period      → Full Year / Year-to-date / Trailing 12 (month-end)
  //   format      → Summary (subtotals only) / Detailed (every line)
  //   granularity → Annual / Monthly
  // Format and Period both drive REAL filtering: Summary collapses detail
  // lines to the canonical subtotals + computed anchors, and Period selects
  // the columns whose resolved basis matches (FON-41 #4 — it used to be
  // chrome that changed nothing). Granularity stays a view toggle until
  // monthly series are wired through the historicals loader.
  const [periodBasis, setPeriodBasis] = useState<PeriodFilter>('ALL');
  const [format, setFormat] = useState<'summary' | 'detailed'>('detailed');
  const [granularity, setGranularity] = useState<'annual' | 'monthly'>('annual');

  const expY0 = useMemo(() => (getEngineField<Record<string, unknown>[]>(outputs, 'expense', 'years') ?? [])[0] ?? {}, [outputs]);
  const fbY0 = useMemo(() => (getEngineField<Record<string, unknown>[]>(outputs, 'fb', 'years') ?? [])[0] ?? {}, [outputs]);
  const revY0 = useMemo(() => (getEngineField<Record<string, unknown>[]>(outputs, 'revenue', 'years') ?? [])[0] ?? {}, [outputs]);

  // Multi-year grounded columns, built from the SAME documents + extractions
  // the review state (and the Data Room badge) reads — FON-41: the hook used
  // to run its own serial fetch chain after this component mounted, and until
  // it finished the grid had no columns while the Data Room already counted
  // "N to review". One state, one builder, both surfaces.
  const { years: allHistYears, loading: histLoading } = useHistoricals(rawId, {
    keys: deal?.keys,
    documents,
    extractions,
    documentsSettled,
  });
  const populatedHistYears = useMemo(() => allHistYears.filter(histHasData), [allHistYears]);
  // FON-15 — render EVERY available normalized period, not a fixed last-4
  // window. Any year the analyst uploaded (2019, a partial/YTD 2025, …) is
  // recognized by Historical Coverage, so it must be accessible here too; the
  // table scrolls horizontally (minWidth scales with column count) and the
  // year-pill filter lets the reviewer hide any period they don't want.
  const histYears = populatedHistYears;
  const coverage = useMemo(() => buildCoverage(documents, populatedHistYears), [documents, populatedHistYears]);
  // How many columns each basis has — drives the Period options (a basis with
  // no columns is DISABLED rather than blanking the grid) and keeps the count
  // visible so nothing is silently filtered away.
  const basisCounts = useMemo(() => {
    const m = new Map<PeriodBasis, number>();
    for (const y of histYears) m.set(y.periodBasis, (m.get(y.periodBasis) ?? 0) + 1);
    return m;
  }, [histYears]);
  const hasMonthlyStatement = (basisCounts.get('MONTHLY') ?? 0) > 0;
  const effectiveBasis: PeriodFilter = useMemo(() => {
    if (periodBasis === 'ALL') return 'ALL';
    // A basis whose last column just disappeared (a statement removed, a
    // re-classification) falls back to "all" rather than blanking the grid.
    return (basisCounts.get(periodBasis) ?? 0) > 0 ? periodBasis : 'ALL';
  }, [basisCounts, periodBasis]);
  // Resolve a source docId → its filename for the per-cell source tooltip
  // (canonical Financials design). Same documents list the SourcePanel resolves
  // against (d.filename); unresolved ids fall back to a generic label.
  const docNameById = useCallback(
    (id: string): string | undefined => documents.find((d) => d.id === id)?.filename,
    [documents],
  );
  // Design rewire: overall extraction confidence chip — average of the
  // per-line confidences captured on the historical years.
  const avgConfidence = useMemo(() => {
    const vals: number[] = [];
    for (const y of histYears) {
      if (!y.meta) continue;
      for (const m of Object.values(y.meta)) {
        if (typeof m.confidence === 'number') vals.push(m.confidence);
      }
    }
    return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
  }, [histYears]);

  // Model-column base (pre-edit) values per row id.
  const modelBase = useMemo<Record<string, number>>(() => {
    const readPath = (obj: Record<string, unknown>, path: string[]) => {
      let cur: unknown = obj;
      for (const p of path) cur = (cur as Record<string, unknown>)?.[p];
      return num(cur);
    };
    const srcOf = (s: RowDef['y1Src']) => (s === 'fb' ? fbY0 : s === 'revenue' ? revY0 : expY0);
    const v: Record<string, number> = {};
    for (const r of ROWS) {
      if (!r.y1Read) continue;
      v[r.id] = readPath(srcOf(r.y1Src), r.y1Read);
    }
    return v;
  }, [expY0, fbY0, revY0]);

  // Model-column live values (base + draft edits), then derived rows.
  const modelLive = useMemo<Record<string, number>>(() => {
    const v = { ...modelBase };
    for (const [k, s] of Object.entries(draft)) {
      const n = Number(s.replace(/[$,\s]/g, ''));
      if (Number.isFinite(n)) {
        const row = ROWS.find((r) => r.overrideKey === k);
        if (row) v[row.id] = n;
      }
    }
    // resolve derived rows in declared order (total_rev before gop/noi)
    for (const r of ROWS) {
      if (r.compute) v[r.id] = r.compute(v);
    }
    return v;
  }, [modelBase, draft]);

  const overrides = (deal?.field_overrides ?? {}) as Record<string, unknown>;
  const isOverridden = (key?: string) => !!key && key in overrides;

  // FON-41 — ONE canonical review state (lib/reviewState), shared with the
  // Data Room badge: a flag lives on a (row × year) cell and is pinned to that
  // column's own statement. It reads the LIVE extractions, so an Accept/Edit
  // clears the cell, the banner count and the Data Room counts from the same
  // state. (The old row-keyed reviewMap let the first low-confidence field
  // across ALL documents answer for every year — Sam's 2019-vs-2023 mix-up.)
  const reviewState = useMemo(
    () => buildReviewState(documents, extractions, ROWS, histYears),
    [documents, extractions, histYears],
  );

  const acceptReview = useCallback(
    async (docId: string, field: string) => {
      try {
        await api.documents.reviewField(rawId, docId, { field_name: field, action: 'accept' });
        await refreshExtraction(docId);
        toast('Marked reviewed', { type: 'success' });
      } catch (err) {
        toast(`Couldn’t accept: ${err instanceof Error ? err.message : String(err)}`, { type: 'error' });
      }
    },
    [rawId, refreshExtraction, toast],
  );

  // Correct a wrong extracted value AT SOURCE (reviewField edit) — the fix path
  // for read-only cells like revenue, whose model value is computed and can't be
  // inline-overridden. Re-runs so the grounded model reflects the correction.
  const editExtraction = useCallback(
    async (docId: string, field: string, value: number) => {
      try {
        await api.documents.reviewField(rawId, docId, { field_name: field, action: 'edit', value });
        await refreshExtraction(docId);
        await run();
        await refresh();
        toast('Corrected + re-modeled', { type: 'success' });
      } catch (err) {
        toast(`Couldn’t update: ${err instanceof Error ? err.message : String(err)}`, { type: 'error' });
      }
    },
    [rawId, refreshExtraction, run, refresh, toast],
  );

  const labelOf = useCallback(
    (id: string, fallback: string) => wl.layout.relabels[id] ?? fallback,
    [wl.layout.relabels],
  );

  // The presentation tree: canonical rows in ROWS order, but with per-section
  // reordering, hidden lines, curated memo lines, and split children applied.
  // Subtotals + computed rows stay anchored in their canonical position.
  const rendered = useMemo<RenderItem[]>(() => {
    const items: RenderItem[] = [];
    const curatedBySection: Record<string, CuratedLine[]> = {};
    for (const c of wl.layout.curated) (curatedBySection[c.section] ??= []).push(c);

    for (const row of ROWS) {
      if (row.kind === 'section') {
        items.push({ type: 'section', id: row.id, label: row.label });
        const defaults = SECTION_INPUTS[row.id] ?? [];
        const curated = curatedBySection[row.id] ?? [];
        const universe = [...defaults, ...curated.map((c) => c.id)];
        const saved = wl.layout.order[row.id];
        const ordered = saved
          ? [...saved.filter((id) => universe.includes(id)), ...universe.filter((id) => !saved.includes(id))]
          : universe;
        for (const id of ordered) {
          const cur = curated.find((c) => c.id === id);
          if (cur) { items.push({ type: 'curated', line: cur }); continue; }
          const r = INPUT_BY_ID[id];
          if (!r) continue;
          if (wl.layout.hidden.includes(id) && !customize) continue;
          const kids = wl.layout.splits[id];
          const splitDelta = kids?.length ? (modelLive[id] ?? 0) - kids.reduce((s, k) => s + k.value, 0) : null;
          items.push({ type: 'input', row: r, sectionId: row.id, siblings: ordered, splitDelta });
          if (kids?.length) for (const k of kids) items.push({ type: 'split', parentId: id, child: k });
        }
        continue;
      }
      if (row.kind === 'input') continue; // emitted under its section header
      items.push({ type: 'anchor', row });
    }
    return items;
  }, [wl.layout, customize, modelLive]);

  // Design rewire: line-item search + Format toggle filter the rendered rows.
  // Summary collapses to the canonical subtotals/computed anchors (drops the
  // editable detail lines, their splits, and memo lines); Detailed shows all.
  const visibleRendered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rendered.filter((item) => {
      if (format === 'summary' && (item.type === 'input' || item.type === 'split' || item.type === 'curated')) {
        return false;
      }
      if (!q) return true;
      if (item.type === 'section') return false;
      const label =
        item.type === 'split' ? item.child.label
        : item.type === 'curated' ? item.line.label
        : item.row.label;
      return label.toLowerCase().includes(q);
    });
  }, [rendered, search, format]);

  // Deep-link focus: a "→ Financials" jump from the Data Room field review
  // carries ?focus=<field_name>. Resolve it to a worksheet row and scroll +
  // pulse it so the analyst lands exactly on the value that needs attention.
  const focusField = searchParams?.get('focus') ?? null;
  // FON-41 — a document deep-link (?doc=<id>, from the Data Room's "N to
  // review" badge / "View Financials") pins that statement's column: every
  // other year pill is switched off once and the first flagged cell in the
  // column is scrolled into view, so the badge count and the red cells in the
  // grid line up 1:1. The analyst can re-enable the other pills afterwards.
  const docParam = searchParams?.get('doc') ?? null;
  const pinnedCol = useMemo(
    () => (docParam ? histYears.find((y) => y.docId === docParam) ?? null : null),
    [docParam, histYears],
  );
  const pinnedYear = pinnedCol?.year ?? null;
  const pinnedRef = useRef<string | null>(null);
  useEffect(() => {
    // Pin only once the historicals have settled: extractions stream in from
    // the shared store one document at a time, and pinning on the first
    // column to appear would leave later columns un-hidden.
    if (!pinnedCol || histLoading || pinnedRef.current === pinnedCol.year) return;
    pinnedRef.current = pinnedCol.year;
    setHiddenYears(new Set(histYears.filter((y) => y.year !== pinnedCol.year).map((y) => y.year)));
    setFormat('detailed');
    // A deep-link must land ON the statement it names, whatever its basis.
    setPeriodBasis(pinnedCol.periodBasis);
  }, [pinnedCol, histYears, histLoading]);
  const focusRowId = useMemo(() => {
    if (focusField) {
      for (const r of ROWS) {
        const rk = r.overrideKey ?? r.reviewKey;
        if (rk && fieldMatchesKey(focusField, rk)) return r.id;
      }
    }
    // Pinned document: land on its first flagged cell (row order); after each
    // Accept the focus advances to the next one until the column is clean.
    if (pinnedYear) return cellsForYear(reviewState, ROWS, pinnedYear)[0]?.rowId ?? null;
    return null;
  }, [focusField, pinnedYear, reviewState]);
  const focusRowRef = useRef<HTMLTableRowElement>(null);
  const [pulse, setPulse] = useState(false);
  useEffect(() => {
    if (!focusRowId || !focusRowRef.current) return;
    focusRowRef.current.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setPulse(true);
    const t = setTimeout(() => setPulse(false), 2400);
    return () => clearTimeout(t);
  }, [focusRowId, rendered]);

  const save = useCallback(
    async (key: string, current: number | null) => {
      const s = draft[key];
      if (s == null) return;
      const n = Number(s.replace(/[$,\s]/g, ''));
      if (!Number.isFinite(n)) { toast('Enter a number', { type: 'error' }); return; }
      // FON-63 — re-saving the number already in the cell writes nothing, so
      // the line keeps reporting the source it came from.
      if (isNoOpEdit(n, current, 'usd')) {
        setDraft((d) => { const { [key]: _d, ...rest } = d; return rest; });
        toast(NO_OP_EDIT_MESSAGE, { type: 'info' });
        return;
      }
      setSavingKey(key);
      try {
        const next = { ...overrides, [key]: { value: n, note: 'Edited on the Financials worksheet' } };
        await api.deals.update(rawId, { field_overrides: next });
        await refreshDeal();
        await run();
        await refresh();
        setDraft((d) => { const { [key]: _d, ...rest } = d; return rest; });
        toast('Saved + re-modeled', { type: 'success' });
      } catch (err) {
        toast(`Couldn’t save: ${err instanceof Error ? err.message : String(err)}`, { type: 'error' });
      } finally {
        setSavingKey(null);
      }
    },
    [draft, overrides, rawId, refreshDeal, run, refresh, toast],
  );

  const reset = useCallback(
    async (key: string) => {
      setSavingKey(key);
      try {
        const { [key]: _drop, ...rest } = overrides;
        await api.deals.update(rawId, { field_overrides: rest });
        await refreshDeal();
        await run();
        await refresh();
        toast('Reset to source', { type: 'success' });
      } catch (err) {
        toast(`Couldn’t reset: ${err instanceof Error ? err.message : String(err)}`, { type: 'error' });
      } finally {
        setSavingKey(null);
      }
    },
    [overrides, rawId, refreshDeal, run, refresh, toast],
  );

  // FON-41 (Sam QA 9/9): the empty state used to render whenever the engine's
  // Year-0 revenue was 0 — which is also true while outputs, the deal and the
  // extractions are still loading, so a complete deal showed "Run the model…"
  // for ~50 s. Hold a skeleton until every input has settled (this component's
  // own engine-outputs fetch, the deal row, the document list + extractions);
  // only then is the empty state a true statement about the deal.
  const dealPending = !deal && !dealError;
  if (!outputsSettled || dealPending || histLoading) {
    return (
      <div data-testid="worksheet-loading">
        <TabLoadingSkeleton rows={10} />
      </div>
    );
  }
  if (histYears.length === 0) {
    const hasStatements = documents.some(isHistoricalSourceDoc);
    return (
      <Card className="p-6 text-[13px] text-ink-500" data-testid="worksheet-empty">
        {hasStatements
          ? 'Extracted statements are present, but no historical column could be built yet — the deal’s key count is missing or no P&L lines were recognized. Check the statements in the Data Room.'
          : 'No extracted financial statements yet — upload a T-12 or annual P&L in the Data Room; each extracted year appears here as a column.'}
      </Card>
    );
  }

  // Design rewire: Historicals shows historical actuals only — the forward
  // model lives in Projections. Two filters stack: the Period control selects
  // the basis (FY / YTD / T12), the year pills hide individual columns.
  const basisYears = effectiveBasis === 'ALL'
    ? histYears
    : histYears.filter((y) => y.periodBasis === effectiveBasis);
  const shownYears = basisYears.filter((y) => !hiddenYears.has(y.year));
  const cols = shownYears.map((y, i) => ({
    id: `h${y.year}-${i}`,
    // The column says WHAT PERIOD it is ("FY2024" / "T12 Mar 2025"); `y.year`
    // stays the stable key everything else pins on.
    label: y.periodLabel,
    historical: true as const,
    year: y,
  }));

  // FON-41 — the banner counts exactly the cells that render red in the shown
  // columns, from the same review state the Data Room badge reads (Sam's
  // 8-vs-3 / 6-vs-0 mismatches). Hidden year pills drop their cells.
  const flaggedCount = cols.reduce(
    (acc, c) => acc + cellsForYear(reviewState, ROWS, c.year.year).length,
    0,
  );
  // Same-period collisions ("2023" + "2023 (2)") — surfaced on the coverage
  // strip so the analyst knows two statements claim one year (fix the year
  // tag in the Data Room) rather than wondering why a column repeats.
  const collidedPeriods = Array.from(
    new Set(histYears.filter((y) => y.year !== baseYearLabel(y.year)).map((y) => baseYearLabel(y.year))),
  );

  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 border-b border-border bg-surface-2/40">
        <div className="flex items-center gap-3 flex-wrap">
          <div>
            <h3 className="text-[14px] font-semibold text-ink-900">Financials</h3>
            <p className="text-[11.5px] text-ink-500 mt-0.5">
              Historical operating actuals — click any cell’s dot to see its source, or a red-flagged value to review it.
            </p>
          </div>
          <select
            value={effectiveBasis}
            onChange={(e) => setPeriodBasis(e.target.value as PeriodFilter)}
            aria-label="Period basis"
            title="Show the columns that are on this period basis"
            style={{ fontSize: 12, border: '1px solid #e2e1dc', borderRadius: 6, padding: '6px 9px', color: '#3a3f47', background: '#fff' }}
          >
            <option value="ALL">All periods · {histYears.length}</option>
            {PERIOD_OPTIONS.map((o) => {
              const n = basisCounts.get(o.id) ?? 0;
              // A basis with no columns is disabled — never a blank grid.
              return (
                <option key={o.id} value={o.id} disabled={n === 0}>
                  {o.label} · {n}
                </option>
              );
            })}
            {/* Bases outside the three MVP options only appear when a column
                actually has one, so no statement is unreachable. */}
            {EXTRA_BASIS_OPTIONS.filter((o) => (basisCounts.get(o.id) ?? 0) > 0).map((o) => (
              <option key={o.id} value={o.id}>
                {o.label} · {basisCounts.get(o.id)}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-2">
          {running && (
            <span className="inline-flex items-center gap-1.5 text-[11.5px] text-brand-700">
              <Loader2 size={12} className="animate-spin" /> Working…
            </span>
          )}
        </div>
      </div>
      {/* Canonical row 2 — Format + Granularity view toggles, structure editing, confidence chip. */}
      <div className="flex flex-wrap items-center gap-4 px-5 py-2.5 border-b border-border" style={{ background: '#fbfbf9' }}>
        <div className="flex items-center gap-2">
          <span className="text-[11.5px] text-ink-500">Format:</span>
          <SegToggle
            options={[{ id: 'summary', label: 'Summary' }, { id: 'detailed', label: 'Detailed' }]}
            value={format}
            onChange={setFormat}
          />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11.5px] text-ink-500">Granularity:</span>
          <SegToggle
            options={[
              { id: 'annual', label: 'Annual' },
              // FON-41 — a control that does nothing is worse than one that is
              // plainly unavailable. Monthly is offered only when a monthly
              // statement actually reached the worksheet; otherwise it says why.
              {
                id: 'monthly',
                label: 'Monthly',
                disabled: !hasMonthlyStatement,
                title: hasMonthlyStatement
                  ? 'Show the monthly statement columns'
                  : 'No monthly statement has been extracted for this deal yet — upload a monthly P&L to enable this view.',
              },
            ]}
            value={granularity}
            onChange={setGranularity}
          />
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setCustomize((v) => !v)}
            aria-pressed={customize}
            title="Add, rename, reorder, split or hide lines. Presentation only — the engines read the canonical lines either way."
            className={cn(
              'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11.5px] font-medium border transition-colors',
              customize ? 'border-ink-900 bg-ink-900 text-white' : 'border-border text-ink-600 hover:text-ink-900',
            )}
          >
            <SlidersHorizontal size={11} aria-hidden="true" /> Customize structure
          </button>
          {wl.isCustomized && (
            <button
              type="button"
              onClick={() => wl.reset()}
              title="Drop every structure edit and restore the canonical statement order."
              className="inline-flex items-center gap-1 text-[11.5px] text-ink-500 hover:text-ink-900"
            >
              <RotateCcw size={11} aria-hidden="true" /> Reset layout
            </button>
          )}
          {/* A layout edit that did not reach the deal must never look saved
              (FON-41). The chip stays until a retry succeeds. */}
          {wl.saveError && (
            <span
              role="status"
              title={wl.saveError}
              className="inline-flex items-center gap-1.5 text-[11.5px] font-medium text-danger-700 bg-danger-50 border border-danger-500/30 rounded-md px-2 py-0.5"
            >
              <AlertTriangle size={11} aria-hidden="true" /> Layout not saved
              <button
                type="button"
                onClick={() => wl.retrySave()}
                className="underline underline-offset-2 hover:text-danger-600"
              >
                Retry
              </button>
            </span>
          )}
        </div>
        <span className="text-[11px]" style={{ color: '#c3c2bd' }}>Budget / prior-year comparison shown in annual view</span>
        {avgConfidence != null && (
          <span
            className="ml-auto inline-flex items-center gap-1 text-[11px] font-medium text-success-700 bg-success-50 border border-success-500/25 rounded-full px-2 py-0.5 tabular-nums"
            title="Average extraction confidence across the cells in this view — flagged (red) cells are the ones dragging it down."
          >
            {Math.round(avgConfidence * 100)}% extraction confidence
          </span>
        )}
      </div>
      {customize && (
        <div className="px-5 py-2 bg-ink-100/60 border-b border-border text-[11.5px] text-ink-600 flex items-center gap-1.5">
          <Info size={11} className="shrink-0" aria-hidden="true" />
          <span>
            Structure editing is on — rename, reorder, hide or split a line, or add your own.
            It changes how this statement <span className="font-medium text-ink-900">reads</span>, never what the model computes,
            and a line you add is an analyst line, not a document-sourced one. Your layout is saved
            on the deal, so every reviewer opens the statement as you arranged it.
          </span>
        </div>
      )}
      {histYears.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 px-5 py-2 border-b border-border">
          <span className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold">Years</span>
          <div className="flex items-center gap-1">
            {histYears.map((y) => {
              const offBasis = effectiveBasis !== 'ALL' && y.periodBasis !== effectiveBasis;
              const hidden = offBasis || hiddenYears.has(y.year);
              return (
                <button
                  key={y.year}
                  type="button"
                  title={
                    offBasis
                      ? `${y.periodLabel} is on a different period basis — show it`
                      : y.basisReason
                        ? 'This statement never stated its period basis, so only its year is shown (period_mismatch).'
                        : y.periodLabel
                  }
                  onClick={() => {
                    // Off-basis pill: switch the Period control to it rather
                    // than toggling a column the current filter excludes.
                    if (offBasis) {
                      setPeriodBasis(y.periodBasis);
                      setHiddenYears((prev) => {
                        const n = new Set(prev);
                        n.delete(y.year);
                        return n;
                      });
                      return;
                    }
                    setHiddenYears((prev) => {
                      const n = new Set(prev);
                      if (n.has(y.year)) n.delete(y.year);
                      else n.add(y.year);
                      return n;
                    });
                  }}
                  className={cn(
                    'px-2.5 py-1 rounded-md text-[11.5px] font-medium tabular-nums border transition-colors',
                    hidden ? 'border-border text-ink-400' : 'border-ink-900 bg-ink-900 text-white',
                  )}
                >
                  {y.periodLabel}
                </button>
              );
            })}
          </div>
          <div className="relative ml-auto">
            <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-400" aria-hidden="true" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search line items…"
              className="w-48 pl-7 pr-2 py-1 text-[11.5px] rounded-md border border-border focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
            />
          </div>
        </div>
      )}
      {flaggedCount > 0 && (
        <div className="px-5 py-2 bg-red-50 border-b border-red-500/30 text-[11.5px] text-red-800 flex items-center gap-1.5">
          <Info size={11} className="shrink-0" />
          <span>
            <span className="font-semibold">{flaggedCount}</span> value{flaggedCount === 1 ? '' : 's'} came in low-confidence —
            they’re flagged <span className="text-red-700 font-medium">red</span> below. Click one to check its source and accept or edit it.
          </span>
        </div>
      )}
      {docParam && histYears.length > 0 && !pinnedYear && (
        <div className="px-5 py-2 bg-warn-50 border-b border-warn-500/30 text-[11.5px] text-warn-800 flex items-center gap-1.5">
          <AlertTriangle size={11} className="shrink-0" />
          <span>The statement you opened has no extracted column here yet — it may still be processing, or its P&amp;L lines could not be read.</span>
        </div>
      )}
      {coverage.length > 0 && (
        <div className="px-5 py-2.5 border-b border-border bg-surface-2/20 flex flex-wrap items-center gap-x-2 gap-y-1.5">
          <span className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mr-1">Historical coverage</span>
          {coverage.map((c) => (
            <span
              key={c.year}
              title={COVER_TITLE[c.state]}
              className={cn('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border tabular-nums', COVER_TONE[c.state])}
            >
              <span className={cn('w-1.5 h-1.5 rounded-full', COVER_DOT[c.state])} />
              {c.label}
            </span>
          ))}
          {collidedPeriods.length > 0 && (
            <span
              title="Two or more statements resolve to the same period, so each gets its own column (e.g. “2023” and “2023 (2)”). Fix the year tag on one of them in the Data Room if that isn’t intended."
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border border-warn-500/30 bg-warn-50 text-warn-700"
            >
              <AlertTriangle size={10} aria-hidden="true" />
              {collidedPeriods.join(', ')}: more than one statement — check year tags
            </span>
          )}
          <span className="text-[10.5px] text-ink-400 ml-auto hidden md:inline">
            <span className="text-emerald-500">●</span> uploaded ·{' '}
            <span className="text-amber-500">●</span> processing ·{' '}
            <span className="text-danger-500">●</span> failed ·{' '}
            <span className="text-ink-400">●</span> not uploaded
          </span>
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full table-fixed text-[12.5px]" style={{ minWidth: 300 + cols.length * 96 }}>
          <colgroup>
            <col style={{ width: 300 }} />
            {cols.map((c) => <col key={c.id} />)}
          </colgroup>
          <thead>
            <tr className="bg-ink-900 text-white text-[10px] uppercase tracking-wider">
              <th className="text-left font-semibold px-5 py-2.5 sticky left-0 bg-ink-900 z-10">Line item</th>
              {cols.map((c) => (
                <th key={c.id} className={cn('text-right font-semibold px-3 py-2.5', !c.historical && 'text-brand-200')}>
                  {c.label}
                  {c.year.basisReason && (
                    <span
                      className="block text-[9px] font-medium normal-case tracking-normal text-warn-200"
                      title="This statement never stated its period basis (full year / YTD / trailing 12), so only its year is shown — period_mismatch. Nothing is assumed."
                    >
                      basis unknown
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRendered.map((item, idx) => {
              if (item.type === 'section') {
                return (
                  <tr key={`sec-${item.id}`} className="bg-ink-100/50">
                    <td className="px-5 py-1.5 text-[10.5px] font-semibold uppercase tracking-wide text-ink-600 sticky left-0 bg-ink-100/50 z-10">
                      {item.label}
                    </td>
                    <td colSpan={cols.length} className="px-3 py-1.5 text-right">
                      {customize && (
                        <button
                          type="button"
                          onClick={() => wl.addCurated(item.id, 'New line', 0)}
                          className="inline-flex items-center gap-1 text-[10.5px] text-brand-700 hover:text-brand-500"
                        >
                          <Plus size={11} /> Add line
                        </button>
                      )}
                    </td>
                  </tr>
                );
              }

              if (item.type === 'split') {
                return (
                  <SplitChildRow
                    key={`split-${item.child.id}`}
                    child={item.child}
                    colCount={cols.length}
                    customize={customize}
                    onLabel={(v) => wl.updateSplitChild(item.parentId, item.child.id, { label: v })}
                    onValue={(v) => wl.updateSplitChild(item.parentId, item.child.id, { value: v })}
                    onRemove={() => wl.removeSplitChild(item.parentId, item.child.id)}
                  />
                );
              }

              if (item.type === 'curated') {
                return (
                  <CuratedRow
                    key={`cur-${item.line.id}`}
                    line={item.line}
                    colCount={cols.length}
                    customize={customize}
                    onLabel={(v) => wl.updateCurated(item.line.id, { label: v })}
                    onValue={(v) => wl.updateCurated(item.line.id, { value: v })}
                    onRemove={() => wl.removeCurated(item.line.id)}
                  />
                );
              }

              const row = item.row;
              const emphatic = row.kind === 'computed' || row.kind === 'subtotal';
              const isInput = item.type === 'input';
              const hidden = isInput && wl.layout.hidden.includes(row.id);
              const focused = row.id === focusRowId;
              return (
                <tr
                  key={`row-${row.id}-${idx}`}
                  ref={focused ? focusRowRef : undefined}
                  className={cn(
                    'border-t border-border hover:bg-ink-100/30 group transition-colors',
                    emphatic && 'bg-brand-50/25',
                    hidden && 'opacity-45',
                    focused && pulse && 'bg-warn-100 ring-2 ring-warn-400',
                  )}
                >
                  <td className={cn('px-5 py-1.5 text-ink-800 sticky left-0 z-10', emphatic ? 'font-semibold text-ink-900 bg-brand-50/25' : 'pl-9 bg-bg')}>
                    {customize && isInput ? (
                      <RowControls
                        label={labelOf(row.id, row.label)}
                        hidden={hidden}
                        onRename={(v) => wl.setLabel(row.id, v)}
                        onHide={() => wl.toggleHidden(row.id)}
                        onUp={() => wl.move(item.sectionId, row.id, -1, item.siblings)}
                        onDown={() => wl.move(item.sectionId, row.id, 1, item.siblings)}
                        onSplit={() => wl.addSplitChild(row.id, `${labelOf(row.id, row.label)} — part`, 0)}
                      />
                    ) : (
                      <span className="inline-flex items-center gap-2">
                        {labelOf(row.id, row.label)}
                        {isInput && item.splitDelta != null && (
                          <span
                            title="Split parts vs this line"
                            className={cn(
                              'text-[10px] px-1.5 py-0.5 rounded tabular-nums',
                              Math.abs(item.splitDelta) < 1 ? 'bg-success-50 text-success-700' : 'bg-warn-50 text-warn-700',
                            )}
                          >
                            {Math.abs(item.splitDelta) < 1 ? '✓ reconciles' : `Δ ${fmtCurrency(item.splitDelta, { compact: true })}`}
                          </span>
                        )}
                      </span>
                    )}
                  </td>
                  {cols.map((c) => (
                    <WorksheetCell
                      key={c.id}
                      row={row}
                      historical={c.historical}
                      histYear={c.year}
                      modelLive={modelLive}
                      overridden={!c.historical && isOverridden(row.overrideKey)}
                      review={reviewState.byCell.get(cellKey(row.id, c.year.year))}
                      draft={row.overrideKey ? draft[row.overrideKey] : undefined}
                      saving={!!row.overrideKey && savingKey === row.overrideKey}
                      onDraft={(s) => row.overrideKey && setDraft((d) => ({ ...d, [row.overrideKey!]: s }))}
                      onSave={(current) => row.overrideKey && save(row.overrideKey, current)}
                      onCancel={() => row.overrideKey && setDraft((d) => { const { [row.overrideKey!]: _x, ...rest } = d; return rest; })}
                      onInspect={(t) => setInspect(t)}
                      colLabel={c.label}
                      docNameById={docNameById}
                    />
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="px-5 py-2.5 border-t border-border text-[11px] text-ink-500 flex items-center gap-1.5">
        <Info size={11} /> Editable lines are the operating-expense actuals in the Model column. Revenue &amp; subtotals are derived; historical columns are read-only facts — correct them at their source document.
      </div>

      {inspect && (
        <SourcePanel
          target={inspect}
          documents={documents}
          extractions={extractions}
          overrides={overrides}
          onClose={() => setInspect(null)}
          onAccept={inspect.review
            ? () => { const r = inspect.review!; acceptReview(r.docId, r.field); setInspect(null); }
            : undefined}
          onEdit={(docId, field, value) => { editExtraction(docId, field, value); setInspect(null); }}
          onReset={inspect.overrideKey && isOverridden(inspect.overrideKey)
            ? () => { reset(inspect.overrideKey!); setInspect(null); }
            : undefined}
        />
      )}
    </Card>
  );
}

// ── One cell (historical read-only, model editable, computed) ──────────
function WorksheetCell({
  row, historical, histYear, modelLive, overridden, review, draft, saving, colLabel,
  onDraft, onSave, onCancel, onInspect, docNameById,
}: {
  row: RowDef;
  historical: boolean;
  histYear: HistYear | null;
  modelLive: Record<string, number>;
  overridden: boolean;
  review?: { docId: string; field: string; confidence: number };
  draft: string | undefined;
  saving: boolean;
  colLabel: string;
  onDraft: (s: string) => void;
  /** Save this cell — carries the value on screen so a no-op can be refused. */
  onSave: (current: number | null) => void;
  onCancel: () => void;
  onInspect: (t: InspectTarget) => void;
  docNameById: (id: string) => string | undefined;
}) {
  const [hovered, setHovered] = useState(false);
  const resolved = useSource(!historical ? row.overrideKey : undefined);
  // FON-65 — per-value grounding state from GET /deals/{id}/provenance, powering
  // the canonical 6-state dot for the Model (year-0) column. Historical columns
  // derive their state from extraction confidence below.
  const traced = useTrace(
    !historical ? (row.y1Src ?? 'expense') : undefined,
    !historical && row.y1Read ? `years[0].${row.y1Read.join('.')}` : undefined,
  );

  // Value for this cell.
  let value: number | null = null;
  if (historical) {
    value = histYear ? histValue(row.id, histYear) : null;
  } else {
    value = modelLive[row.id] ?? 0;
  }

  // FON-41: the red flag is the shared review state's cell for (row × this
  // column) — pinned to the column's own statement and read from the live
  // extraction, so it clears the moment the value is accepted/edited. histMeta
  // only supplies the source document for in-confidence (green) cells.
  const histMeta = historical && row.metaKey ? histYear?.meta?.[row.metaKey] : undefined;
  const effReview = review;

  // Provenance kind for the dot.
  let kind: InspectTarget['kind'];
  if (row.kind === 'computed' || row.kind === 'subtotal') kind = 'computed';
  else if (historical) kind = 'grounded';
  else if (overridden) kind = 'override';
  else {
    const k = resolved?.source ? sourceKind(resolved.source) : null;
    kind = k ?? 'benchmark';
  }
  // Canonical 6-state dot: map the cell's origin to a ValueState, preferring
  // the engine's own grounding state (/provenance) for the Model column.
  // needs_review rides on top as the amber halo (flags stack, never replace).
  const derivedState: ValueState =
    kind === 'grounded' ? 'document_sourced'
    : kind === 'computed' ? 'calculated'
    : 'assumption'; // override + seed/benchmark are assumption-layer (blue)
  const dotState: ValueState = (traced?.state as ValueState | undefined) ?? derivedState;
  const dotReview = !!effReview;

  const openInspect = () => {
    if (value == null) return;
    // Historical cells carry their source doc via histMeta (extracted) — both
    // green (in-confidence) and red (flagged) cells open the SOURCE panel at
    // that document; only flagged cells get the review Accept/Edit flow.
    const histDocId = historical ? histMeta?.docId : undefined;
    const docIds = historical
      ? (histDocId ? [histDocId] : [])
      : (resolved?.docId ? [resolved.docId] : []);
    onInspect({
      rowLabel: row.label,
      colLabel,
      kind,
      value,
      overrideKey: !historical ? row.overrideKey : undefined,
      reviewKey: !historical ? (row.overrideKey ?? row.reviewKey) : (effReview?.field ?? histMeta?.field),
      docIds: effReview ? [effReview.docId, ...docIds.filter((id) => id !== effReview.docId)] : docIds,
      formula: row.kind === 'computed' || row.kind === 'subtotal' ? formulaFor(row.id) : undefined,
      review: effReview,
      fmt: row.fmt,
      // FON-41 #4 — the panel names the document AND the period it covers.
      // A full year is named by the column's own resolved label ("FY2019");
      // the dated bases spell their end out ("T-12 ending Mar 31, 2025").
      periodText: !historical || !histYear
        ? null
        : histYear.periodBasis === 'FY'
          ? histYear.periodLabel
          : formatPeriodBasis(histYear.periodBasis, histYear.periodEnd),
    });
  };

  if (value == null) {
    return <td className="px-3 py-1.5 text-right text-ink-300">—</td>;
  }

  const editing = draft != null;
  const editable = !historical && !!row.overrideKey;

  // Custom source tooltip (canonical Financials design): a dark navy bubble
  // that NAMES the actual source document on hover. Supplements — never
  // replaces — the existing click-to-source (openInspect).
  const sourceDocId = historical ? histMeta?.docId : resolved?.docId;
  const sourceDoc =
    overridden ? 'Entered by you'
    : kind === 'computed' ? 'Calculated total — sum of the lines above'
    : sourceDocId ? `Extracted from ${docNameById(sourceDocId) ?? 'the source document'}`
    : kind === 'grounded' ? 'Extracted from the source document'
    : 'Calculated by Fondok';

  return (
    <td
      className="relative px-3 py-1.5 text-right whitespace-nowrap"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {hovered && (
        <div
          role="tooltip"
          className="pointer-events-none absolute bottom-full right-2 z-30 mb-1.5 rounded-md bg-fondok-navy px-2 py-1 text-[11px] font-medium text-white whitespace-nowrap shadow-lg"
        >
          {sourceDoc}
        </div>
      )}
      <span className="inline-flex items-center gap-1.5 justify-end">
        <button
          type="button"
          onClick={openInspect}
          aria-label="See where this came from"
          className="shrink-0 inline-flex rounded-full hover:ring-2 hover:ring-offset-1 hover:ring-ink-300"
        >
          <ProvenanceDot state={dotState} review={dotReview} size={8} />
        </button>
        {editing ? (
          <input
            autoFocus
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') onSave(value); if (e.key === 'Escape') onCancel(); }}
            onBlur={onCancel}
            title="Enter to save — Esc or clicking away discards the edit"
            className="w-24 px-1.5 py-0.5 text-[12.5px] text-right tabular-nums border border-brand-500 rounded focus:outline-none focus:ring-2 focus:ring-brand-100"
          />
        ) : editable ? (
          <button
            type="button"
            onClick={() => onDraft(String(Math.round(value!)))}
            disabled={saving}
            aria-label={review ? `Low confidence (${Math.round(review.confidence * 100)}%) — click to check or edit` : 'Click to edit'}
            className={cn(
              'tabular-nums px-1 rounded hover:bg-brand-50',
              review ? 'ring-1 ring-red-400 bg-red-50 text-red-700' : overridden ? 'text-blue-700 font-medium' : 'text-ink-900',
            )}
          >
            {saving ? <Loader2 size={11} className="animate-spin inline" /> : fmtRowValue(value, row.fmt)}
          </button>
        ) : (!historical && row.reviewKey) ? (
          <button
            type="button"
            onClick={openInspect}
            aria-label={review ? `Low confidence (${Math.round(review.confidence * 100)}%) — click to check or correct` : 'Click to see source or correct'}
            className={cn('tabular-nums px-1 rounded hover:bg-brand-50', review ? 'ring-1 ring-red-400 bg-red-50 text-red-700' : 'text-ink-700')}
          >
            {fmtRowValue(value, row.fmt)}
          </button>
        ) : (historical && effReview) ? (
          // Design rewire: low-confidence historical cell — red flag, opens the
          // SOURCE panel at its document.
          <button
            type="button"
            onClick={openInspect}
            aria-label={`Low confidence (${Math.round(effReview.confidence * 100)}%) — click to review its source`}
            className="tabular-nums px-1 rounded ring-1 ring-red-400 bg-red-50 text-red-700 inline-flex items-center gap-1"
          >
            {fmtRowValue(value, row.fmt)}
            <AlertTriangle size={10} className="shrink-0" aria-hidden="true" />
          </button>
        ) : (historical && histMeta) ? (
          // Extracted historical value with known source — green, click for source.
          <button
            type="button"
            onClick={openInspect}
            aria-label="Extracted — click to see its source"
            className="tabular-nums px-1 rounded text-emerald-700 underline decoration-dotted decoration-emerald-500/60 underline-offset-2 hover:bg-emerald-50"
          >
            {fmtRowValue(value, row.fmt)}
          </button>
        ) : (
          <span className={cn('tabular-nums', (row.kind === 'computed' || row.kind === 'subtotal') ? 'font-semibold text-ink-900' : 'text-ink-700')}>
            {fmtRowValue(value, row.fmt)}
          </span>
        )}
      </span>
    </td>
  );
}

// Canonical segmented toggle (design/canonical/Financials Tab.dc.html):
// #f0efeb track, white active pill with a soft shadow. Used for the
// Format (Summary/Detailed) + Granularity (Annual/Monthly) view controls.
function SegToggle<T extends string>({
  options, value, onChange,
}: {
  options: { id: T; label: string; disabled?: boolean; title?: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div style={{ display: 'flex', background: '#f0efeb', borderRadius: 6, padding: 2 }}>
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            onClick={() => { if (!o.disabled) onChange(o.id); }}
            disabled={o.disabled}
            title={o.title}
            style={{
              padding: '4px 12px',
              fontSize: 11.5,
              fontWeight: 600,
              cursor: o.disabled ? 'not-allowed' : 'pointer',
              borderRadius: 5,
              border: 'none',
              fontFamily: 'inherit',
              background: active ? '#fff' : 'transparent',
              color: o.disabled ? '#a8acb3' : active ? '#1a2233' : '#6b6f76',
              boxShadow: active ? '0 1px 2px rgba(0,0,0,.08)' : 'none',
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

// ── Structure-editing sub-rows (customize mode) ────────────────────────
function IconBtn({ title, onClick, children }: { title: string; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" title={title} onClick={onClick} className="p-1 rounded text-ink-400 hover:text-ink-900 hover:bg-ink-100">
      {children}
    </button>
  );
}

function RowControls({
  label, hidden, onRename, onHide, onUp, onDown, onSplit,
}: {
  label: string; hidden: boolean;
  onRename: (v: string) => void; onHide: () => void; onUp: () => void; onDown: () => void; onSplit: () => void;
}) {
  return (
    <span className="inline-flex items-center gap-1">
      <input
        key={label}
        defaultValue={label}
        onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== label) onRename(v); }}
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
        className="w-40 px-1.5 py-0.5 text-[12px] border border-border rounded bg-bg focus:outline-none focus:ring-2 focus:ring-brand-100"
      />
      <IconBtn title="Move up" onClick={onUp}><ChevronUp size={12} /></IconBtn>
      <IconBtn title="Move down" onClick={onDown}><ChevronDown size={12} /></IconBtn>
      <IconBtn title="Split into parts" onClick={onSplit}><Scissors size={12} /></IconBtn>
      <IconBtn title={hidden ? 'Show' : 'Hide'} onClick={onHide}>{hidden ? <Eye size={12} /> : <EyeOff size={12} />}</IconBtn>
    </span>
  );
}

// A split part (Model column only). Value is presentation-only — it never
// reaches the engine; the parent's canonical value still drives the model.
function SplitChildRow({
  child, colCount, customize, onLabel, onValue, onRemove,
}: {
  child: SplitChild; colCount: number; customize: boolean;
  onLabel: (v: string) => void; onValue: (v: number) => void; onRemove: () => void;
}) {
  return (
    <tr className="border-t border-border/60 bg-violet-50/20">
      <td className="pl-14 pr-5 py-1 sticky left-0 bg-violet-50/20 z-10">
        <span className="inline-flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-violet-400" />
          {customize ? (
            <input
              key={child.label}
              defaultValue={child.label}
              onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== child.label) onLabel(v); }}
              className="w-36 px-1.5 py-0.5 text-[12px] border border-border rounded bg-bg focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          ) : (
            <span className="text-[12px] text-ink-600">{child.label}</span>
          )}
          {customize && <IconBtn title="Remove" onClick={onRemove}><Trash2 size={12} /></IconBtn>}
        </span>
      </td>
      {Array.from({ length: colCount - 1 }).map((_, i) => (
        <td key={i} className="px-3 py-1 text-right text-ink-300">—</td>
      ))}
      <td className="px-3 py-1 text-right">
        {customize ? (
          <input
            key={child.value}
            defaultValue={String(Math.round(child.value))}
            onBlur={(e) => { const n = Number(e.target.value.replace(/[$,\s]/g, '')); if (Number.isFinite(n)) onValue(n); }}
            className="w-24 px-1.5 py-0.5 text-[12px] text-right tabular-nums border border-border rounded bg-bg focus:outline-none focus:ring-2 focus:ring-brand-100"
          />
        ) : (
          <span className="tabular-nums text-ink-600">{fmtCurrency(child.value, { compact: true })}</span>
        )}
      </td>
    </tr>
  );
}

// A curated line — the analyst's own, not the document's. Founder decision
// (FON-41 §3): manual rows are presentation-only for the MVP and never feed
// the engines, so they carry the ASSUMPTION dot and an "Analyst line" chip —
// never the green document-sourced dot.
function CuratedRow({
  line, colCount, customize, onLabel, onValue, onRemove,
}: {
  line: CuratedLine; colCount: number; customize: boolean;
  onLabel: (v: string) => void; onValue: (v: number) => void; onRemove: () => void;
}) {
  return (
    <tr className="border-t border-border/60">
      <td className="pl-9 pr-5 py-1 sticky left-0 bg-bg z-10">
        <span className="inline-flex items-center gap-1.5">
          <ProvenanceDot state="assumption" size={8} title="Analyst line — entered here, not read from a document." />
          {customize ? (
            <input
              key={line.label}
              defaultValue={line.label}
              onBlur={(e) => { const v = e.target.value.trim(); if (v && v !== line.label) onLabel(v); }}
              className="w-36 px-1.5 py-0.5 text-[12px] border border-border rounded bg-bg focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
          ) : (
            <span className="text-[12px] text-ink-700">{line.label}</span>
          )}
          <span
            className="text-[9px] uppercase tracking-wide px-1 py-0.5 rounded bg-ink-100 text-ink-500"
            title="Entered by an analyst. It is not extracted from any statement and no engine reads it."
          >
            Analyst line
          </span>
          {customize && <IconBtn title="Remove" onClick={onRemove}><Trash2 size={12} /></IconBtn>}
        </span>
      </td>
      {Array.from({ length: colCount - 1 }).map((_, i) => (
        <td key={i} className="px-3 py-1 text-right text-ink-300">—</td>
      ))}
      <td className="px-3 py-1 text-right">
        {customize ? (
          <input
            key={line.value}
            defaultValue={String(Math.round(line.value))}
            onBlur={(e) => { const n = Number(e.target.value.replace(/[$,\s]/g, '')); if (Number.isFinite(n)) onValue(n); }}
            className="w-24 px-1.5 py-0.5 text-[12px] text-right tabular-nums border border-border rounded bg-bg focus:outline-none focus:ring-2 focus:ring-brand-100"
          />
        ) : (
          <span className="inline-flex items-center gap-1.5 justify-end">
            <ProvenanceDot state="assumption" size={8} title="Analyst line — entered here, not read from a document." />
            <span className="tabular-nums text-ink-700">{fmtCurrency(line.value, { compact: true })}</span>
          </span>
        )}
      </td>
    </tr>
  );
}

function formulaFor(id: string): string {
  switch (id) {
    case 'total_rev': return 'Rooms + F&B + Other Revenue';
    case 'undist_total': return 'A&G + Sales & Marketing + Property Ops + Utilities + Info & Telecom';
    case 'fixed_total': return 'Management Fee + FF&E Reserve + Property Taxes + Insurance';
    case 'gop': return 'Total Revenue − Departmental Expenses − Total Undistributed';
    case 'noi': return 'Total Revenue − Departmental − Undistributed − Fees & Fixed';
    default: return '';
  }
}

// ── Click-to-source slide-over ─────────────────────────────────────────
function SourcePanel({
  target, documents, extractions, overrides, onClose, onReset, onAccept, onEdit,
}: {
  target: InspectTarget;
  documents: WorkerDocument[];
  extractions: Record<string, ExtractionResult | undefined>;
  overrides: Record<string, unknown>;
  onClose: () => void;
  onReset?: () => void;
  onAccept?: () => void;
  onEdit?: (docId: string, field: string, value: number) => void;
}) {
  const [editVal, setEditVal] = useState<string | null>(null);
  const docs = target.docIds
    .map((id) => documents.find((d) => d.id === id))
    .filter((d): d is WorkerDocument => Boolean(d));

  // Locate the exact extracted field behind the cell. When the target pins a
  // document (every historical cell does — its column's own statement) look
  // ONLY there, exact field name first: another year's statement must never
  // answer for this one (FON-41 — the panel named the 2019 P&L for a 2023
  // cell because the scan below used to run over every document).
  const key = target.reviewKey ?? target.overrideKey;
  const pinnedDocId = target.review?.docId ?? (target.kind === 'grounded' ? target.docIds[0] : undefined);
  const field: (ExtractionField & { docName: string; docId: string }) | null = (() => {
    if (!key) return null;
    const scan = pinnedDocId ? documents.filter((d) => d.id === pinnedDocId) : documents;
    for (const d of scan) {
      const ex = extractions[d.id];
      if (!ex?.fields) continue;
      const f =
        ex.fields.find((ff) => ff.field_name === key) ??
        ex.fields.find((ff) => fieldMatchesKey(ff.field_name, key));
      if (f) return { ...f, docName: d.filename, docId: d.id };
    }
    return null;
  })();

  const overrideNote = (() => {
    if (target.kind !== 'override' || !target.overrideKey) return null;
    const raw = overrides[target.overrideKey];
    if (raw && typeof raw === 'object' && 'note' in raw) return String((raw as { note?: unknown }).note ?? '');
    return null;
  })();

  const kindLabel =
    target.kind === 'grounded' ? 'Grounded in your documents'
    : target.kind === 'override' ? 'Your override'
    : target.kind === 'computed' ? 'Computed'
    : 'Seed / benchmark default';
  const kindDot =
    target.kind === 'grounded' ? 'bg-emerald-500'
    : target.kind === 'override' ? 'bg-violet-500'
    : target.kind === 'computed' ? 'bg-sky-500'
    : 'bg-amber-500';

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-ink-900/20" />
      <div
        className="relative w-full max-w-sm h-full bg-bg border-l border-border shadow-xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-5 py-4 border-b border-border">
          <div>
            <div className="text-[10.5px] uppercase tracking-wider text-ink-500">{target.colLabel}</div>
            <h4 className="text-[15px] font-semibold text-ink-900 mt-0.5">{target.rowLabel}</h4>
            <div className="text-[19px] font-semibold tabular-nums text-ink-900 mt-1">
              {target.fmt === 'pct' ? `${(target.value * 100).toFixed(1)}%` : target.fmt === 'dollar' ? `$${Math.round(target.value).toLocaleString()}` : fmtCurrency(target.value)}
            </div>
          </div>
          <button type="button" onClick={onClose} className="p-1 text-ink-400 hover:text-ink-900">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div className="flex items-center gap-2 text-[12.5px]">
            <span className={cn('w-2 h-2 rounded-full', kindDot)} />
            <span className="font-medium text-ink-900">{kindLabel}</span>
          </div>

          {target.formula && (
            <div className="rounded-lg bg-sky-50 border border-sky-100 px-3 py-2.5">
              <div className="text-[10.5px] uppercase tracking-wide text-sky-700 font-semibold mb-1">Formula</div>
              <div className="text-[12px] text-ink-700">{target.formula}</div>
            </div>
          )}

          {overrideNote && (
            <div className="rounded-lg bg-violet-50 border border-violet-100 px-3 py-2.5">
              <div className="text-[10.5px] uppercase tracking-wide text-violet-700 font-semibold mb-1">Your note</div>
              <div className="text-[12px] text-ink-700">{overrideNote}</div>
            </div>
          )}

          {docs.length > 0 && (
            <div>
              <div className="text-[10.5px] uppercase tracking-wide text-ink-500 font-semibold mb-1.5">
                Source document{docs.length > 1 ? 's' : ''}
              </div>
              <div className="space-y-1.5">
                {docs.map((d) => (
                  <div key={d.id} className="flex items-start gap-2 rounded-lg border border-border px-3 py-2">
                    <FileText size={14} className="text-ink-400 mt-0.5 shrink-0" />
                    <div className="min-w-0">
                      <div className="text-[12px] text-ink-900 truncate">{d.filename}</div>
                      {d.doc_type && <div className="text-[10.5px] text-ink-500">{d.doc_type}</div>}
                      {target.periodText && (
                        <div className="text-[10.5px] text-ink-700">{target.periodText}</div>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {field && (
            <div className="rounded-lg bg-emerald-50 border border-emerald-100 px-3 py-2.5 space-y-1.5">
              <div className="text-[10.5px] uppercase tracking-wide text-emerald-700 font-semibold">Extracted line</div>
              <div className="text-[12px] text-ink-800"><span className="text-ink-500">field</span> · {field.field_name}</div>
              {field.raw_text && <div className="text-[12px] text-ink-800 italic">“{field.raw_text}”</div>}
              <div className="flex items-center gap-3 text-[11px] text-ink-600">
                {field.source_page != null && <span>page {field.source_page}</span>}
                {field.confidence != null && <span>{Math.round(field.confidence * 100)}% confidence</span>}
                {field.reviewed && <span className="text-emerald-700">✓ {field.reviewed}</span>}
              </div>
            </div>
          )}

          {target.kind === 'grounded' && docs.length === 0 && (
            <p className="text-[11.5px] text-ink-500 leading-relaxed">Extracted from this year’s uploaded P&amp;L.</p>
          )}
          {target.kind === 'benchmark' && (
            <p className="text-[11.5px] text-ink-500 leading-relaxed">{sourceExplanation('seed')}</p>
          )}

          {target.review && onAccept && (
            <div className="rounded-lg bg-warn-50 border border-warn-500/30 px-3 py-2.5 space-y-2">
              <div className="text-[11.5px] text-warn-800">
                This value came in at <span className="font-semibold">{Math.round(target.review.confidence * 100)}%</span> confidence.
                Check it against the source above, then accept it or edit the value in the Model column.
              </div>
              <button
                type="button"
                onClick={onAccept}
                className="inline-flex items-center gap-1.5 text-[12px] font-medium px-2.5 py-1 rounded-md bg-emerald-600 text-white hover:bg-emerald-700"
              >
                <Check size={12} /> Looks right — accept
              </button>
            </div>
          )}

          {field && onEdit && (
            <div className="rounded-lg border border-border px-3 py-2.5 space-y-2">
              <div className="text-[10.5px] uppercase tracking-wide text-ink-500 font-semibold">Correct extracted value</div>
              {editVal == null ? (
                <button
                  type="button"
                  onClick={() => setEditVal(String(typeof field.value === 'number' ? Math.round(field.value) : Math.round(target.value)))}
                  className="text-[12px] text-brand-700 hover:text-brand-500 font-medium"
                >
                  Correct this value →
                </button>
              ) : (
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    autoFocus
                    value={editVal}
                    onChange={(e) => setEditVal(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Escape') setEditVal(null); }}
                    className="w-32 px-2 py-1 text-[12.5px] text-right tabular-nums border border-brand-500 rounded focus:outline-none focus:ring-2 focus:ring-brand-100"
                  />
                  <button
                    type="button"
                    onClick={() => { const n = Number(editVal.replace(/[$,\s]/g, '')); if (Number.isFinite(n)) onEdit(field.docId, field.field_name, n); }}
                    className="text-[12px] font-medium px-2.5 py-1 rounded-md bg-brand-600 text-white hover:bg-brand-700"
                  >
                    Save + re-model
                  </button>
                  <button type="button" onClick={() => setEditVal(null)} className="text-[12px] text-ink-500 hover:text-ink-900">Cancel</button>
                </div>
              )}
              <p className="text-[11px] text-ink-500 leading-relaxed">
                Updates the extracted value read from {field.docName} and re-grounds the model.
                The original source document will not be changed.
              </p>
            </div>
          )}

          {onReset && (
            <button
              type="button"
              onClick={onReset}
              className="inline-flex items-center gap-1.5 text-[12px] text-brand-700 hover:text-brand-500 font-medium"
            >
              <RotateCcw size={12} /> Reset to source value
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// Fuzzy match an extraction field_name to a canonical override key. Handles
// USALI dotted paths, *_usd suffixes, and fb/f_and_b variants.
export function fieldMatchesKey(fieldName: string, key: string): boolean {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z]/g, '');
  const fn = norm(fieldName);
  const aliases: Record<string, string[]> = {
    insurance: ['insurance'],
    property_taxes: ['propertytax', 'propertytaxes', 'realestatetax'],
    mgmt_fee: ['managementfee', 'mgmtfee', 'basemanagementfee'],
    ffe_reserve: ['ffereserve', 'ffe', 'reservereplacement', 'replacementreserve'],
    rooms_revenue: ['roomsrevenue', 'roomrevenue', 'roomsdepartmentrevenue'],
    fb_revenue: ['foodbeveragerevenue', 'fbrevenue', 'foodandbeveragerevenue'],
    other_revenue: ['otherrevenue', 'otheroperatedrevenue', 'miscincome', 'miscellaneousrevenue'],
    administrative_general: ['administrativegeneral', 'adminandgeneral'],
    sales_marketing: ['salesmarketing', 'salesandmarketing'],
    property_operations: ['propertyoperations', 'propertyoperationsmaintenance'],
    utilities: ['utilities'],
    information_telecom: ['informationtelecom', 'infotelecom'],
    rooms_dept_expense: ['roomsexpense', 'roomsdepartmentexpense', 'roomsdept'],
    fb_dept_expense: ['fbexpense', 'foodbeverageexpense'],
    other_dept_expense: ['otheroperatedexpense', 'otherdepartmentexpense'],
  };
  const cands = aliases[key] ?? [norm(key)];
  return cands.some((c) => c.length > 2 && fn.includes(c));
}

// FON-41 — the worksheet's row model (id + metaKey), exported so the Data Room
// builds its per-document "to review" badge from the SAME rows this grid
// renders, through the same lib/reviewState predicate. This replaces the old
// key-alias predicate (isReviewableFinancialField), which counted fields that
// had no cell here and so could never be flagged.
export const WORKSHEET_ROWS: ReadonlyArray<ReviewRow> = ROWS;
