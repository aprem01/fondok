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
import type { ExtractionField, ExtractionResult, WorkerDocument } from '@/lib/api';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useDeal } from '@/lib/hooks/useDeal';
import { useDocuments } from '@/lib/hooks/useDocuments';
import { useHistoricals } from '@/lib/hooks/useHistoricals';
import type { HistYear } from '@/components/project/pl/HistoricalsSection';
import { useSource } from '@/lib/hooks/useDealProvenance';
import { sourceKind, sourceExplanation } from '@/lib/provenance';
import { useWorksheetLayout } from '@/lib/hooks/useWorksheetLayout';
import type { SplitChild, CuratedLine } from '@/lib/hooks/useWorksheetLayout';

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

const ROWS: RowDef[] = [
  { id: 's_ops', label: 'Operating Statistics', kind: 'section' },
  { id: 'occ', label: 'Occupancy', kind: 'input', fmt: 'pct', y1Src: 'revenue', y1Read: ['occupancy'] },
  { id: 'adr', label: 'ADR', kind: 'input', fmt: 'dollar', y1Src: 'revenue', y1Read: ['adr'] },
  { id: 'revpar', label: 'RevPAR', kind: 'input', fmt: 'dollar', y1Src: 'revenue', y1Read: ['revpar'] },

  { id: 's_rev', label: 'Revenue', kind: 'section' },
  { id: 'rooms_rev', label: 'Rooms Revenue', kind: 'input', y1Src: 'fb', y1Read: ['rooms_revenue'], reviewKey: 'rooms_revenue', metaKey: 'rooms' },
  { id: 'fb_rev', label: 'Food & Beverage Revenue', kind: 'input', y1Src: 'fb', y1Read: ['fb_revenue'], reviewKey: 'fb_revenue', metaKey: 'fb' },
  { id: 'other_rev', label: 'Other Revenue', kind: 'input', y1Src: 'fb', y1Read: ['other_revenue'], reviewKey: 'other_revenue', metaKey: 'misc' },
  { id: 'total_rev', label: 'Total Revenue', kind: 'subtotal',
    compute: (v) => v.rooms_rev + v.fb_rev + v.other_rev },

  { id: 's_dept', label: 'Departmental Expenses', kind: 'section' },
  { id: 'rooms_dept', label: 'Rooms', kind: 'input', overrideKey: 'rooms_dept_expense', y1Read: ['dept_expenses', 'rooms'] },
  { id: 'fb_dept', label: 'Food & Beverage', kind: 'input', overrideKey: 'fb_dept_expense', y1Read: ['dept_expenses', 'food_beverage'] },
  { id: 'other_dept', label: 'Other Operated', kind: 'input', overrideKey: 'other_dept_expense', y1Read: ['dept_expenses', 'other_operated'] },

  { id: 's_undist', label: 'Undistributed Operating Expenses', kind: 'section' },
  { id: 'ag', label: 'Administrative & General', kind: 'input', overrideKey: 'administrative_general', y1Read: ['undistributed', 'administrative_general'] },
  { id: 'sm', label: 'Sales & Marketing', kind: 'input', overrideKey: 'sales_marketing', y1Read: ['undistributed', 'sales_marketing'] },
  { id: 'pom', label: 'Property Operations', kind: 'input', overrideKey: 'property_operations', y1Read: ['undistributed', 'property_operations'] },
  { id: 'util', label: 'Utilities', kind: 'input', overrideKey: 'utilities', y1Read: ['undistributed', 'utilities'] },
  { id: 'it', label: 'Information & Telecom', kind: 'input', overrideKey: 'information_telecom', y1Read: ['undistributed', 'information_telecom'] },
  { id: 'undist_total', label: 'Total Undistributed', kind: 'subtotal',
    compute: (v) => sumKeys(v, UNDIST_IDS) },

  { id: 'gop', label: 'Gross Operating Profit (GOP)', kind: 'computed',
    compute: (v) => v.total_rev - sumKeys(v, DEPT_IDS) - sumKeys(v, UNDIST_IDS) },

  { id: 's_fixed', label: 'Management Fee & Fixed Charges', kind: 'section' },
  { id: 'mgmt', label: 'Management Fee', kind: 'input', overrideKey: 'mgmt_fee', y1Read: ['mgmt_fee'] },
  { id: 'ffe', label: 'FF&E Reserve', kind: 'input', overrideKey: 'ffe_reserve', y1Read: ['ffe_reserve'] },
  { id: 'taxes', label: 'Property Taxes', kind: 'input', overrideKey: 'property_taxes', y1Read: ['fixed_charges', 'property_taxes'] },
  { id: 'insurance', label: 'Insurance', kind: 'input', overrideKey: 'insurance', y1Read: ['fixed_charges', 'insurance'] },
  { id: 'fixed_total', label: 'Total Fees & Fixed', kind: 'subtotal',
    compute: (v) => sumKeys(v, FIXED_FEE_IDS) },

  { id: 'noi', label: 'Net Operating Income (NOI)', kind: 'computed',
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

const nOrNull = (x: unknown): number | null =>
  typeof x === 'number' && Number.isFinite(x) ? x : null;

// Historical value for a worksheet row from one HistYear (see useHistoricals).
// Detail rows (A&G, insurance, mgmt fee, …) return null — the source P&Ls only
// carry the rolled-up subtotals, so those cells render "—".
function histValue(rowId: string, h: HistYear): number | null {
  switch (rowId) {
    case 'occ': return nOrNull(h.occupancyPct);
    case 'adr': return nOrNull(h.adr);
    case 'revpar': return nOrNull(h.revpar);
    case 'rooms_rev': return nOrNull(h.rooms);
    case 'fb_rev': return nOrNull(h.fb);
    case 'other_rev': return nOrNull(h.misc);
    case 'total_rev': {
      const parts = [h.rooms, h.fb, h.misc].map(nOrNull).filter((x): x is number => x != null);
      return parts.length ? parts.reduce((a, b) => a + b, 0) : null;
    }
    case 'rooms_dept': return nOrNull(h.rooms_dept_expense);
    case 'fb_dept': return nOrNull(h.fb_dept_expense);
    case 'other_dept': return nOrNull(h.other_dept_expense);
    case 'undist_total': return nOrNull(h.undistributed);
    case 'gop': return nOrNull(h.gop);
    case 'fixed_total': return nOrNull(h.fixed_expenses);
    case 'noi': return nOrNull(h.noi);
    default: return null;
  }
}

// A year earns a column only if it carries REAL data — skeleton placeholders
// (populated:false) and all-zero years are dropped so the grid isn't padded
// with empty $0 columns.
const histHasData = (h: HistYear) =>
  h.populated !== false &&
  [h.rooms, h.fb, h.gop, h.noi].some((x) => {
    const n = nOrNull(x);
    return n != null && n !== 0;
  });

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
function buildCoverage(docs: WorkerDocument[], populatedYears: HistYear[]): { year: string; state: CoverState }[] {
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
    if (/^\d{4}$/.test(y.year) || y.year === 'T-12') bump(y.year, 'uploaded');
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
  return order.map((year) => ({ year, state: byYear.get(year)! }));
}

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
}

type RenderItem =
  | { type: 'section'; id: string; label: string }
  | { type: 'anchor'; row: RowDef }
  | { type: 'input'; row: RowDef; sectionId: string; siblings: string[]; splitDelta: number | null }
  | { type: 'split'; parentId: string; child: SplitChild }
  | { type: 'curated'; line: CuratedLine };

export default function GroundedWorksheet({
  dealId,
  isKimptonDemo,
}: {
  dealId: string | number;
  isKimptonDemo?: boolean;
}) {
  const rawId = String(dealId);
  const { outputs, refresh } = useEngineOutputs(rawId);
  const { deal, refresh: refreshDeal } = useDeal(rawId);
  const { documents, extractions, refreshExtraction } = useDocuments(rawId);
  const { toast } = useToast();
  const { run, status } = useEngineRun(rawId, 'returns', { runMode: 'all' });
  const running = status === 'running' || status === 'queued';

  const searchParams = useSearchParams();
  const wl = useWorksheetLayout(rawId);
  // Design rewire: structure-editing (Customize) removed from Historicals —
  // the layout hooks stay wired but the mode is never entered.
  const customize = false;
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [inspect, setInspect] = useState<InspectTarget | null>(null);
  // Design rewire: year-pill filtering + line-item search.
  const [hiddenYears, setHiddenYears] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState('');

  const expY0 = useMemo(() => (getEngineField<Record<string, unknown>[]>(outputs, 'expense', 'years') ?? [])[0] ?? {}, [outputs]);
  const fbY0 = useMemo(() => (getEngineField<Record<string, unknown>[]>(outputs, 'fb', 'years') ?? [])[0] ?? {}, [outputs]);
  const revY0 = useMemo(() => (getEngineField<Record<string, unknown>[]>(outputs, 'revenue', 'years') ?? [])[0] ?? {}, [outputs]);

  // Multi-year grounded columns — reuses HistoricalsSection's tested loader
  // (endpoint + multi-doc fallback) via useHistoricals. Keep the last 4
  // populated years; empty on deals with no extracted P&Ls (Model col alone).
  const { years: allHistYears } = useHistoricals(rawId, { keys: deal?.keys });
  const populatedHistYears = useMemo(() => allHistYears.filter(histHasData), [allHistYears]);
  const histYears = useMemo(() => populatedHistYears.slice(-4), [populatedHistYears]);
  const coverage = useMemo(() => buildCoverage(documents, populatedHistYears), [documents, populatedHistYears]);
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

  // Low-confidence review, folded IN (no separate screen): map each editable
  // model key to its unreviewed low-confidence extracted field, so the cell can
  // flag it amber and the source panel can accept/edit it in place.
  const reviewMap = useMemo<Record<string, { docId: string; field: string; confidence: number }>>(() => {
    const out: Record<string, { docId: string; field: string; confidence: number }> = {};
    for (const d of documents) {
      const ex = extractions[d.id];
      if (!ex?.fields) continue;
      for (const f of ex.fields) {
        if (f.confidence == null || f.confidence >= 0.85 || f.reviewed) continue;
        for (const r of ROWS) {
          const rk = r.overrideKey ?? r.reviewKey;
          if (!rk || out[rk]) continue;
          if (fieldMatchesKey(f.field_name, rk)) {
            out[rk] = { docId: d.id, field: f.field_name, confidence: f.confidence };
            break;
          }
        }
      }
    }
    return out;
  }, [documents, extractions]);
  const reviewCount = Object.keys(reviewMap).length;

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

  // Design rewire: line-item search filters the rendered rows by label.
  const visibleRendered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return rendered;
    return rendered.filter((item) => {
      if (item.type === 'section') return false;
      const label =
        item.type === 'split' ? item.child.label
        : item.type === 'curated' ? item.line.label
        : item.row.label;
      return label.toLowerCase().includes(q);
    });
  }, [rendered, search]);

  // Deep-link focus: a "→ Financials" jump from the Data Room field review
  // carries ?focus=<field_name>. Resolve it to a worksheet row and scroll +
  // pulse it so the analyst lands exactly on the value that needs attention.
  const focusField = searchParams?.get('focus') ?? null;
  const focusRowId = useMemo(() => {
    if (!focusField) return null;
    for (const r of ROWS) {
      const rk = r.overrideKey ?? r.reviewKey;
      if (rk && fieldMatchesKey(focusField, rk)) return r.id;
    }
    return null;
  }, [focusField]);
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
    async (key: string) => {
      const s = draft[key];
      if (s == null) return;
      const n = Number(s.replace(/[$,\s]/g, ''));
      if (!Number.isFinite(n)) { toast('Enter a number', { type: 'error' }); return; }
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

  if (isKimptonDemo) {
    return <Card className="p-6 text-[13px] text-ink-500">The editable worksheet is available on live deals.</Card>;
  }
  if (num(expY0['total_revenue']) === 0) {
    return (
      <Card className="p-6 text-[13px] text-ink-500">
        Run the model (upload financials + run the engines) to populate the worksheet.
      </Card>
    );
  }

  // Design rewire: Historicals shows historical actuals only — the forward
  // model lives in Projections. Year pills filter which years render.
  const shownYears = histYears.filter((y) => !hiddenYears.has(y.year));
  const cols = shownYears.map((y, i) => ({
    id: `h${y.year}-${i}`,
    label: y.year,
    historical: true as const,
    year: y,
  }));

  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 border-b border-border bg-surface-2/40">
        <div>
          <h3 className="text-[14px] font-semibold text-ink-900">Financials</h3>
          <p className="text-[11.5px] text-ink-500 mt-0.5">
            Historical operating actuals — click any cell’s dot to see its source, or a red-flagged value to review it.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {running && (
            <span className="inline-flex items-center gap-1.5 text-[11.5px] text-brand-700">
              <Loader2 size={12} className="animate-spin" /> Working…
            </span>
          )}
          {avgConfidence != null && (
            <span className="inline-flex items-center gap-1 text-[11px] font-medium text-success-700 bg-success-50 border border-success-500/25 rounded-full px-2 py-0.5 tabular-nums">
              {Math.round(avgConfidence * 100)}% extraction confidence
            </span>
          )}
        </div>
      </div>
      {histYears.length > 0 && (
        <div className="flex flex-wrap items-center gap-3 px-5 py-2 border-b border-border">
          <span className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold">Years</span>
          <div className="flex items-center gap-1">
            {histYears.map((y) => {
              const hidden = hiddenYears.has(y.year);
              return (
                <button
                  key={y.year}
                  type="button"
                  onClick={() =>
                    setHiddenYears((prev) => {
                      const n = new Set(prev);
                      if (n.has(y.year)) n.delete(y.year);
                      else n.add(y.year);
                      return n;
                    })
                  }
                  className={cn(
                    'px-2.5 py-1 rounded-md text-[11.5px] font-medium tabular-nums border transition-colors',
                    hidden ? 'border-border text-ink-400' : 'border-ink-900 bg-ink-900 text-white',
                  )}
                >
                  {y.year}
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
      {reviewCount > 0 && (
        <div className="px-5 py-2 bg-red-50 border-b border-red-500/30 text-[11.5px] text-red-800 flex items-center gap-1.5">
          <Info size={11} className="shrink-0" />
          <span>
            <span className="font-semibold">{reviewCount}</span> value{reviewCount === 1 ? '' : 's'} came in low-confidence —
            they’re flagged <span className="text-red-700 font-medium">red</span> below. Click one to check its source and accept or edit it.
          </span>
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
              {c.year}
            </span>
          ))}
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
                      review={!c.historical ? reviewMap[row.overrideKey ?? row.reviewKey ?? ''] : undefined}
                      draft={row.overrideKey ? draft[row.overrideKey] : undefined}
                      saving={!!row.overrideKey && savingKey === row.overrideKey}
                      onDraft={(s) => row.overrideKey && setDraft((d) => ({ ...d, [row.overrideKey!]: s }))}
                      onSave={() => row.overrideKey && save(row.overrideKey)}
                      onCancel={() => row.overrideKey && setDraft((d) => { const { [row.overrideKey!]: _x, ...rest } = d; return rest; })}
                      onInspect={(t) => setInspect(t)}
                      colLabel={c.label}
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
  onDraft, onSave, onCancel, onInspect,
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
  onSave: () => void;
  onCancel: () => void;
  onInspect: (t: InspectTarget) => void;
}) {
  const resolved = useSource(!historical ? row.overrideKey : undefined);

  // Value for this cell.
  let value: number | null = null;
  if (historical) {
    value = histYear ? histValue(row.id, histYear) : null;
  } else {
    value = modelLive[row.id] ?? 0;
  }

  // Design rewire: historical cells flag from per-cell extraction confidence
  // captured on HistYear.meta (low confidence → red, opens the SOURCE panel at
  // that year's document). Model cells keep the passed-in `review`.
  const histMeta = historical && row.metaKey ? histYear?.meta?.[row.metaKey] : undefined;
  const histReview =
    histMeta && histMeta.docId && histMeta.confidence < 0.85
      ? { docId: histMeta.docId, field: histMeta.field, confidence: histMeta.confidence }
      : undefined;
  const effReview = review ?? histReview;

  // Provenance kind for the dot.
  let kind: InspectTarget['kind'];
  if (row.kind === 'computed' || row.kind === 'subtotal') kind = 'computed';
  else if (historical) kind = 'grounded';
  else if (overridden) kind = 'override';
  else {
    const k = resolved?.source ? sourceKind(resolved.source) : null;
    kind = k ?? 'benchmark';
  }
  // Design taxonomy: grounded/extracted = green, input/assumption
  // (override + benchmark) = blue, calculated = gray.
  const dotCls =
    kind === 'grounded' ? 'bg-emerald-500'
    : kind === 'computed' ? 'bg-slate-400'
    : 'bg-blue-500';

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
    });
  };

  if (value == null) {
    return <td className="px-3 py-1.5 text-right text-ink-300">—</td>;
  }

  const editing = draft != null;
  const editable = !historical && !!row.overrideKey;

  return (
    <td className="px-3 py-1.5 text-right whitespace-nowrap">
      <span className="inline-flex items-center gap-1.5 justify-end">
        <button
          type="button"
          onClick={openInspect}
          title="See where this came from"
          className={cn('w-1.5 h-1.5 rounded-full shrink-0 hover:ring-2 hover:ring-offset-1 hover:ring-ink-300', dotCls)}
        />
        {editing ? (
          <input
            autoFocus
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') onSave(); if (e.key === 'Escape') onCancel(); }}
            onBlur={onSave}
            className="w-24 px-1.5 py-0.5 text-[12.5px] text-right tabular-nums border border-brand-500 rounded focus:outline-none focus:ring-2 focus:ring-brand-100"
          />
        ) : editable ? (
          <button
            type="button"
            onClick={() => onDraft(String(Math.round(value!)))}
            disabled={saving}
            title={review ? `Low confidence (${Math.round(review.confidence * 100)}%) — click to check or edit` : 'Click to edit'}
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
            title={review ? `Low confidence (${Math.round(review.confidence * 100)}%) — click to check or correct` : 'Click to see source or correct'}
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
            title={`Low confidence (${Math.round(effReview.confidence * 100)}%) — click to review its source`}
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
            title="Extracted — click to see its source"
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

// A curated memo line — the analyst's own annotation. Informational only.
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
          <span className="w-1.5 h-1.5 rounded-full bg-ink-400" />
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
          <span className="text-[9px] uppercase tracking-wide px-1 py-0.5 rounded bg-ink-100 text-ink-500">memo</span>
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
          <span className="tabular-nums text-ink-700">{fmtCurrency(line.value, { compact: true })}</span>
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

  // Try to locate the exact extracted field behind a model-column / review key.
  const key = target.reviewKey ?? target.overrideKey;
  const field: (ExtractionField & { docName: string; docId: string }) | null = (() => {
    if (!key) return null;
    for (const d of documents) {
      const ex = extractions[d.id];
      if (!ex?.fields) continue;
      const f = ex.fields.find((ff) => fieldMatchesKey(ff.field_name, key));
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
              <div className="text-[10.5px] uppercase tracking-wide text-ink-500 font-semibold">Correct at source</div>
              {editVal == null ? (
                <button
                  type="button"
                  onClick={() => setEditVal(String(typeof field.value === 'number' ? Math.round(field.value) : Math.round(target.value)))}
                  className="text-[12px] text-brand-700 hover:text-brand-500 font-medium"
                >
                  Fix this value on the document →
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
              <p className="text-[11px] text-ink-500 leading-relaxed">Updates the extracted value on {field.docName} and re-grounds the model.</p>
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
function fieldMatchesKey(fieldName: string, key: string): boolean {
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
