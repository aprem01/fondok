'use client';

/**
 * Overview tab — canonical rebuild against `design/canonical/Overview Tab v3.dc.html`.
 *
 * "The deal on one page — what you're buying, how it's capitalized, when each
 * milestone lands, and the returns that fall out of it." Built entirely from
 * the shared design system (`@/components/design`) — KpiTile · SectionCard ·
 * ProvenanceDot · WhereThisCameFrom · tokens — and driven by real engine
 * outputs read through `getEngineField` (never prototype placeholders).
 *
 * DEAL-TYPE-AWARE (v3): the section set + KPI tiles switch on the deal's
 * `deal_type` (+ `return_profile`):
 *   development         → Project · Land/Site · Development Budget ·
 *                         Construction Financing · Opening & Stabilization ·
 *                         Exit · Sources & Uses · Development Timeline
 *   acquisition · core  → Property · Entry Valuation · Capitalization · Exit ·
 *                         Sources & Uses · Transaction Timeline
 *   acquisition · value-add → Property · Entry Valuation · Renovation/CapEx ·
 *                         Capitalization · Stabilization · Exit ·
 *                         Sources & Uses · Transaction Timeline
 * Fields an engine doesn't emit render as "awaiting data" em-dashes.
 *
 * OWNERSHIP (design + DESIGN_MAP): Acquisition / Reversion / Financing rows are
 * READ-ONLY / linked — operating overrides stay owned by Financials, debt by
 * Debt. The only assumptions edited here are the Investment Profile (deal type,
 * returns profile, brand, positioning, and — FON-68 — the return targets:
 * Target Levered IRR / Target MOIC), and a deal-type change routes through a
 * confirmation ("Update model") before the model re-runs.
 *
 * RETURN TARGETS (FON-68): `deal.target_irr` / `deal.target_moic` are analyst
 * inputs and the single source of truth for the Return benchmark strip and
 * the Returns → Pricing Max Price Solver. The returns-profile band ("12-18%")
 * is only a suggestion — "Use profile midpoint" writes it explicitly; nothing
 * defaults. Saving a target never re-runs the model (benchmark only).
 *
 * PROVENANCE: dots + the anchored "Where this came from" popover read the real
 * per-value `state` / formula / inputs from GET /deals/{id}/provenance via
 * `useTraceGraph`, falling back to the canonical semantic kind.
 * The Data Key strip is mounted once in `page.tsx` — no per-tab legend here.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { useRouter, useParams } from 'next/navigation';
import { useToast } from '@/components/ui/Toast';
import { fmtCurrency, fmtPct, fmtMillions } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useTraceGraph } from '@/lib/hooks/useValueTrace';
import { Refused, useRefusal, REFUSAL_GLYPH } from '@/components/help/Refused';
import { returnProfiles, positioningTiers, brandFamilies } from '@/lib/mockData';
import {
  KpiTile,
  SectionCard,
  ProvenanceDot,
  WhereThisCameFrom,
  palette,
  prov,
  popoverKind,
  fontStack,
  type WhereThisCameFromProps,
} from '@/components/design';
import {
  api,
  isWorkerConnected,
  WorkerError,
  type EngineOutputsResponse,
  type MarketOverviewResult,
  type PropertyNameOriginal,
  type TimelineResponse,
  type ValueState,
} from '@/lib/api';

// ─── Canonical value vocabulary (design/canonical/Overview Tab v3.dc.html) ────
// doc → document sourced · linked → owned by another engine/module · input →
// editable assumption · calc → calculated by Fondok · awaiting → not in yet.
type ValueKind = 'doc' | 'linked' | 'input' | 'calc' | 'awaiting';
type Cfg = 'va' | 'core' | 'dev';

/** Row value color — mirrors the canonical `colorFor()` (BLUE/GREEN/GRAY/BLACK/MUTED). */
function valueColor(kind: ValueKind, bold: boolean, overridden: boolean): string {
  if (overridden) return prov.blue;
  if (bold) return prov.black;
  if (kind === 'input') return prov.blue;
  if (kind === 'linked' || kind === 'doc') return prov.green;
  if (kind === 'awaiting') return prov.muted;
  return prov.gray;
}

/** Provenance-dot state — the real /provenance `state` wins, else the canonical kind. */
function kindToState(kind: ValueKind): ValueState {
  switch (kind) {
    case 'doc': return 'document_sourced';
    case 'linked': return 'linked';
    case 'input': return 'assumption';
    case 'awaiting': return 'awaiting_data';
    default: return 'calculated';
  }
}

const KIND_LABEL: Record<ValueKind, string> = {
  doc: 'Document sourced', linked: 'Linked', input: 'Assumption',
  calc: 'Calculated', awaiting: 'Awaiting data',
};
const KIND_POPOVER_COLOR: Record<ValueKind, string> = {
  doc: popoverKind.document_sourced, linked: popoverKind.linked,
  input: popoverKind.editable_assumption, calc: popoverKind.calculated,
  awaiting: palette.textMuted,
};

interface RowProvInput { name: string; from: string; kind: ValueKind }

interface RowDef {
  id: string;
  label: string;
  kind: ValueKind;
  /** Formatted display string (Overview rows are read-only / linked). */
  value: string;
  /** Resolved provenance state — drives the leading dot + review flag. */
  state: ValueState;
  bold?: boolean;
  overridden?: boolean;
  /** Where this came from — popover payload. */
  docName?: string;
  docPage?: string;
  linkLabel?: string;   // originating module (linked rows)
  linkTab?: string;     // deep-link target for the popover action
  /** Sub-tab slug on that target — `?tab=<linkTab>&sub=<linkSub>` (FON-59 #4). */
  linkSub?: string;
  formula?: string;     // human formula (calc rows)
  formulaNumbers?: string;
  inputs?: RowProvInput[];
  /** Provenance trace lookup (engine, dotted output path) for real formula/state. */
  trace?: { engine: 'capital' | 'returns' | 'debt' | 'expense'; path: string };
  /** Explicit popover "where" (wins over docPage / docName). */
  where?: string;
  /** Popover sub-line under the value — what this field is and how it behaves. */
  sub?: string;
  /**
   * FON-59 — the `deal.field_overrides` key this row's worker read honors.
   * Declaring it enables Override / Restore on the row. Wire it ONLY to rows
   * whose backend path applies the override (Property Name today); keys /
   * brand / city are deal columns synced from extraction and stay off it.
   */
  overridePath?: string;
  /** The document-extracted value an override replaced (+ its source). */
  original?: { value: string; docName?: string | null; page?: number | null };
  /** FON-59 — analyst-input row persisted to a deal column (Project Name → deals.name). */
  dealField?: 'name';
  /**
   * Phase 4.4 — the canonical assumption key whose refusal explains this
   * row's dash. Set it ONLY where the dash is attributable to exactly one
   * key the worker tags in ``assumption_sources.reasons``; a row without it
   * (or whose value is not the bare glyph) renders as it always has.
   */
  reasonKey?: string;
}

/** FON-59 — the field_overrides key the worker's market_overview honors as the Property Name. */
const PROPERTY_NAME_OVERRIDE_PATH = 'property_overview.name';

/** Read a string out of a field_overrides entry ({value, note} or bare string); blank → null. */
function overrideText(overrides: Record<string, unknown>, path: string): string | null {
  const raw = overrides[path];
  const v = raw != null && typeof raw === 'object' && 'value' in (raw as object) ? (raw as { value?: unknown }).value : raw;
  return typeof v === 'string' && v.trim() ? v.trim() : null;
}
/** The analyst note stored alongside a structured override, if any. */
function overrideNote(overrides: Record<string, unknown>, path: string): string | null {
  const raw = overrides[path];
  const n = raw != null && typeof raw === 'object' ? (raw as { note?: unknown }).note : null;
  return typeof n === 'string' && n.trim() ? n.trim() : null;
}

interface PropertyMeta {
  name: string | null;
  nameOriginal: PropertyNameOriginal | null;
  nameSource: 'analyst_override' | 'document' | null;
  year_built: number | null;
  gba_sf: number | null;
  labor: string | null;
  trailingOcc: number | null;
  trailingAdr: number | null;
}
const EMPTY_META: PropertyMeta = {
  name: null, nameOriginal: null, nameSource: null, year_built: null, gba_sf: null, labor: null, trailingOcc: null, trailingAdr: null,
};

interface RowsSection {
  kind: 'rows';
  title: string;
  note?: string;
  /** Header link — `sub` names the target's sub-tab (`?tab=pl&sub=projections`). */
  action?: { label: string; tab: string; sub?: string };
  rows: RowDef[];
}
interface SuSection { kind: 'su'; title: string }
interface TimelineSection { kind: 'timeline'; title: string }
type SectionSpec = RowsSection | SuSection | TimelineSection;

const DEAL_TYPES: { label: string; id: 'acquisition' | 'development' }[] = [
  { label: 'Acquisition', id: 'acquisition' },
  { label: 'Development', id: 'development' },
];

/** Section titles for the deal-type-change confirmation ("Sections after the change"). */
function sectionTitles(cfg: Cfg): string {
  if (cfg === 'dev') {
    return ['Project', 'Land / Site Acquisition', 'Development Budget', 'Construction Financing',
      'Opening & Stabilization', 'Exit', 'Sources & Uses', 'Development Timeline'].join(' · ');
  }
  if (cfg === 'core') {
    return ['Property', 'Entry', 'Capitalization', 'Exit',
      'Sources & Uses', 'Transaction Timeline'].join(' · ');
  }
  return ['Property', 'Entry Valuation', 'Renovation / CapEx', 'Capitalization', 'Stabilization',
    'Exit', 'Sources & Uses', 'Transaction Timeline'].join(' · ');
}

export default function OverviewTab({ projectId }: { projectId: number | string }) {
  const router = useRouter();
  const params = useParams();
  const { toast } = useToast();
  const dealId = (params?.id as string | undefined) ?? String(projectId);
  const isMockId = /^\d+$/.test(dealId);
  const liveMode = isWorkerConnected() && !isMockId;

  const { outputs } = useEngineOutputs(dealId);
  const { deal, refresh: refreshDeal } = useDeal(dealId);

  // Computed-value provenance graphs — the dot + popover read the real
  // /provenance `state` / formula / inputs, falling back to the canonical kind.
  const capitalTrace = useTraceGraph('capital');
  const returnsTrace = useTraceGraph('returns');
  const debtTrace = useTraceGraph('debt');
  const expenseTrace = useTraceGraph('expense');
  const traceGet = useCallback(
    (engine: 'capital' | 'returns' | 'debt' | 'expense', path: string) => {
      const g = engine === 'capital' ? capitalTrace
        : engine === 'returns' ? returnsTrace
          : engine === 'debt' ? debtTrace : expenseTrace;
      return g?.get(path) ?? null;
    },
    [capitalTrace, returnsTrace, debtTrace, expenseTrace],
  );
  const tracedState = useCallback(
    (engine: 'capital' | 'returns' | 'debt' | 'expense', path: string): ValueState | null =>
      traceGet(engine, path)?.state ?? null,
    [traceGet],
  );

  // Dated transaction timeline (FON-71) — refetched after a run.
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const [runToken, setRunToken] = useState(0);
  useEffect(() => {
    if (!liveMode) { setTimeline(null); return; }
    const ac = new AbortController();
    api.engines.timeline(dealId, ac.signal)
      .then(setTimeline)
      .catch(() => { /* best-effort; rail shows pending */ });
    return () => ac.abort();
  }, [dealId, liveMode, runToken]);

  // Descriptive property metadata resolved cross-document by the worker
  // (OM-first): asset name / year_built / gba_sf / labor. NOT engine output —
  // read once so the Property rows populate for live deals, and re-read after
  // a Property Name override / restore (FON-59) so `property_name_original`
  // and the resolved name stay in step with the worker.
  const [meta, setMeta] = useState<PropertyMeta>(EMPTY_META);
  const [metaToken, setMetaToken] = useState(0);
  useEffect(() => {
    if (!liveMode) { setMeta(EMPTY_META); return; }
    const ac = new AbortController();
    api.market.overview(dealId, ac.signal)
      .then((d) => {
        const o = (d ?? {}) as MarketOverviewResult;
        setMeta({
          name: o.property_name?.trim() || null,
          nameOriginal: o.property_name_original?.value ? o.property_name_original : null,
          nameSource: o.property_name_source ?? null,
          year_built: o.year_built ?? null, gba_sf: o.gba_sf ?? null, labor: o.labor_type ?? null,
          trailingOcc: o.trailing_12_occupancy ?? null, trailingAdr: o.trailing_12_adr ?? null,
        });
      })
      .catch(() => { /* best-effort */ });
    return () => ac.abort();
  }, [dealId, liveMode, metaToken]);

  // ─── Investment Profile persistence + debounced re-run ─────────────────
  const overrides = (deal?.field_overrides ?? {}) as Record<string, unknown>;
  const fullRun = useEngineRun(liveMode ? dealId : '', 'returns', { runMode: 'all' });
  const rerunRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (rerunRef.current) clearTimeout(rerunRef.current); }, []);
  const scheduleRun = useCallback(() => {
    if (!liveMode) return;
    if (rerunRef.current) clearTimeout(rerunRef.current);
    rerunRef.current = setTimeout(() => { void fullRun.run().then(() => setRunToken(Date.now())); }, 1400);
  }, [liveMode, fullRun]);

  const persist = useCallback(
    async (patch: Parameters<typeof api.deals.update>[1], msg = 'Saved — re-running the model…') => {
      if (!liveMode) { toast('Editing is disabled on demo deals', { type: 'info' }); return; }
      try {
        await api.deals.update(dealId, patch);
        toast(msg, { type: 'success' });
        void refreshDeal?.();
        scheduleRun();
      } catch (err) {
        const detail = err instanceof WorkerError ? err.body : String(err);
        toast(`Save failed: ${detail || 'worker rejected update'}`, { type: 'error' });
      }
    },
    [dealId, liveMode, toast, refreshDeal, scheduleRun],
  );

  // ─── FON-68 — return targets (benchmark + pricing hurdles; no re-run) ───
  const persistTarget = useCallback(
    async (patch: { target_irr?: number | null; target_moic?: number | null }) => {
      if (!liveMode) { toast('Editing is disabled on demo deals', { type: 'info' }); return; }
      try {
        await api.deals.update(dealId, patch);
        toast('Target saved — benchmark and pricing hurdle only; the model is unchanged', { type: 'success' });
        void refreshDeal?.();
      } catch (err) {
        const detail = err instanceof WorkerError ? err.body : String(err);
        toast(`Save failed: ${detail || 'worker rejected update'}`, { type: 'error' });
      }
    },
    [dealId, liveMode, toast, refreshDeal],
  );

  // ─── FON-59 — per-row analyst override + Project Name rename ───────────
  // Property Name writes `field_overrides[row.overridePath] = { value, note }`
  // through the canonical PATCH path; the worker reads it back as
  // `property_name` and keeps the extracted value in `property_name_original`
  // so Restore (delete the key) brings it back. Project Name writes the deal
  // column (`deals.name`) and never touches field_overrides. Neither re-runs
  // the model — names feed no engine — but both refresh the deal so the page
  // header / rows update.
  const [editing, setEditing] = useState<{ rowId: string; draft: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const saveFailed = useCallback((err: unknown) => {
    const detail = err instanceof WorkerError ? err.body : String(err);
    toast(`Save failed: ${detail || 'worker rejected update'}`, { type: 'error' });
  }, [toast]);
  const saveOverride = useCallback(async (row: RowDef, draft: string) => {
    if (!row.overridePath) return;
    if (!liveMode) { toast('Editing is disabled on demo deals', { type: 'info' }); return; }
    const value = draft.trim();
    if (!value) { toast(`Enter a ${row.label.toLowerCase()}`, { type: 'error' }); return; }
    setSaving(true);
    try {
      await api.deals.update(dealId, {
        field_overrides: { ...overrides, [row.overridePath]: { value, note: `Analyst override — Overview · ${row.label}` } },
      });
      toast(`${row.label} overridden — the extracted value is kept so you can restore it`, { type: 'success' });
      setEditing(null);
      void refreshDeal?.();
      setMetaToken(Date.now());
    } catch (err) {
      saveFailed(err);
    } finally {
      setSaving(false);
    }
  }, [dealId, liveMode, overrides, toast, refreshDeal, saveFailed]);
  const restoreOverride = useCallback(async (row: RowDef) => {
    if (!row.overridePath) return;
    if (!liveMode) { toast('Editing is disabled on demo deals', { type: 'info' }); return; }
    const { [row.overridePath]: _drop, ...rest } = overrides;
    setSaving(true);
    try {
      await api.deals.update(dealId, { field_overrides: rest });
      toast(row.original ? `${row.label} restored to the sourced value` : `${row.label} override cleared`, { type: 'success' });
      setEditing(null);
      void refreshDeal?.();
      setMetaToken(Date.now());
    } catch (err) {
      saveFailed(err);
    } finally {
      setSaving(false);
    }
  }, [dealId, liveMode, overrides, toast, refreshDeal, saveFailed]);
  const renameProject = useCallback(async (draft: string) => {
    if (!liveMode) { toast('Editing is disabled on demo deals', { type: 'info' }); return; }
    const name = draft.trim();
    if (!name) { toast('Enter a project name', { type: 'error' }); return; }
    setSaving(true);
    try {
      await api.deals.update(dealId, { name });
      toast('Project name updated', { type: 'success' });
      setEditing(null);
      void refreshDeal?.();
    } catch (err) {
      saveFailed(err);
    } finally {
      setSaving(false);
    }
  }, [dealId, liveMode, toast, refreshDeal, saveFailed]);

  // ─── UI state ──────────────────────────────────────────────────────────
  const [reviewOnly, setReviewOnly] = useState(false);
  const [pendingDealType, setPendingDealType] = useState<'acquisition' | 'development' | null>(null);
  const [popover, setPopover] = useState<{ row: RowDef; top: number; left: number; caretRight: number } | null>(null);

  // ─── Deal configuration ────────────────────────────────────────────────
  const dealType: 'acquisition' | 'development' = deal?.deal_type === 'development' ? 'development' : 'acquisition';
  const returnProfileId = deal?.return_profile ?? 'value-add';
  const positioningId = deal?.positioning ?? 'default';
  const brand = deal?.brand ?? '';
  const cfg: Cfg = dealType === 'development' ? 'dev' : (returnProfileId === 'core' ? 'core' : 'va');
  const keys = (deal?.keys && deal.keys > 0) ? deal.keys : undefined;
  const isDev = cfg === 'dev';

  // ─── Engine reads (no fixtures — '—' until the engine emits) ────────────
  const has = (v: number | undefined): v is number => v != null && Number.isFinite(v);
  const cap = (...p: string[]): number | undefined => getEngineField<number>(outputs, 'capital', ...p);
  const ret = (...p: string[]): number | undefined => getEngineField<number>(outputs, 'returns', ...p);
  const dbt = (...p: string[]): number | undefined => getEngineField<number>(outputs, 'debt', ...p);

  const capUses = getEngineField<Array<{ label?: string; amount?: number }>>(outputs, 'capital', 'uses') ?? [];
  const findUse = (re: RegExp): number | undefined => {
    const row = capUses.find((u) => re.test(String(u?.label ?? '')));
    return typeof row?.amount === 'number' ? row.amount : undefined;
  };

  const expYears = getEngineField<Array<{ noi?: number; noi_institutional?: number }>>(outputs, 'expense', 'years');
  const y1Noi = expYears && expYears.length > 0 ? (expYears[0].noi_institutional ?? expYears[0].noi) : undefined;

  const wPurchase = cap('purchase_price');
  const wPricePerKey = cap('price_per_key');
  const wEntryCap = cap('entry_cap_rate');
  const wTotalCapital = cap('total_capital_usd') ?? cap('total_capital');
  const wEquity = cap('equity_amount');
  const wDebtAmount = cap('debt_amount');
  const wLtv = cap('ltv');
  const wLtc = cap('ltc');
  const wLoanAmount = dbt('loan_amount') ?? wDebtAmount;
  const wInterestRate = dbt('interest_rate');
  const wExitCap = ret('exit_cap_rate');
  const wTerminalNoi = ret('terminal_noi_usd') ?? ret('terminal_noi');
  const wGrossSale = ret('gross_sale_price');
  const wSellingCosts = ret('selling_costs');
  const wHoldYears = ret('hold_years');
  const wLeveredIrr = ret('levered_irr');

  // Derived (all engine-sourced or engine-arithmetic — never fabricated).
  const purchase = wPurchase ?? findUse(/purchase/i);
  const pricePerKey = wPricePerKey ?? (has(purchase) && has(keys) ? purchase / keys : undefined);
  const entryCap = wEntryCap ?? (has(y1Noi) && has(purchase) && purchase > 0 ? y1Noi / purchase : undefined);
  const closing = findUse(/closing/i);
  const totalCapital = wTotalCapital;
  const totalPerKey = has(totalCapital) && has(keys) ? totalCapital / keys : undefined;
  const loan = wLoanAmount;
  const equity = wEquity ?? (has(totalCapital) && has(loan) ? totalCapital - loan : undefined);
  const ltv = wLtv ?? (has(loan) && has(purchase) && purchase > 0 ? loan / purchase : undefined);
  const ltc = wLtc ?? (has(loan) && has(totalCapital) && totalCapital > 0 ? loan / totalCapital : undefined);
  const financingCosts = findUse(/financ|loan cost|lender/i);
  const renoBudget = findUse(/renovat|pip/i);
  const hasReno = has(renoBudget) && renoBudget > 0;
  // Hard / soft / professional-fee split — read straight from the capital
  // engine's renovation_breakdown (defaults 75/15/10), never re-derived here.
  // Absent (no renovation budget → breakdown is null) renders '—' via money().
  const renoBreakdown = getEngineField<{ hard?: number; soft?: number; fees?: number }>(outputs, 'capital', 'renovation_breakdown');
  const renoHard = renoBreakdown?.hard;
  const renoSoft = renoBreakdown?.soft;
  const renoProf = renoBreakdown?.fees;
  const terminalNoi = wTerminalNoi;
  const exitCap = wExitCap;
  const grossExit = wGrossSale;
  const exitPerKey = has(grossExit) && has(keys) ? grossExit / keys : undefined;
  const sellingCosts = wSellingCosts;
  const holdYears = wHoldYears;
  const leveredIrr = wLeveredIrr;
  // Development-specific budget lines (read by label where the engine emits them).
  const landPrice = findUse(/land/i);
  const hardCosts = findUse(/hard/i);
  const softCosts = findUse(/soft/i);
  const ffe = findUse(/ff&?e|ffe/i);
  const profFees = findUse(/profession/i);
  const contingency = findUse(/contingen/i);

  // Formatting helpers (mirror the canonical money / mm / pct).
  const money = (v: number | undefined): string => (has(v) ? fmtCurrency(v) : '—');
  const mm = (v: number | undefined): string => (has(v) ? fmtMillions(v, 2) : '—');
  const pctv = (v: number | undefined, d = 2): string => (has(v) ? fmtPct(v, d) : '—');
  const perKey = (v: number | undefined): string => (has(v) && has(keys) ? fmtCurrency(v / keys) : '—');

  // ─── Row factory (resolves provenance state + review flag) ─────────────
  const mk = (r: Omit<RowDef, 'state'> & { state?: ValueState }): RowDef => {
    const state = r.trace ? (tracedState(r.trace.engine, r.trace.path) ?? kindToState(r.kind)) : (r.state ?? kindToState(r.kind));
    return { ...r, state };
  };
  const doc = (id: string, label: string, value: string, docName?: string, docPage?: string, extra?: Partial<RowDef>): RowDef =>
    mk({ id, label, kind: 'doc', value, docName, docPage, ...extra });
  // `extra` carries the sub-tab: `{ linkSub: 'projections' }` → the popover's
  // "Open module →" routes to ?tab=pl&sub=projections (FON-59 #4).
  const lnk = (id: string, label: string, value: string, linkLabel: string, linkTab: string, extra?: Partial<RowDef>): RowDef =>
    mk({ id, label, kind: 'linked', value, linkLabel, linkTab, ...extra });
  const cal = (id: string, label: string, value: string, extra?: Partial<RowDef>): RowDef =>
    mk({ id, label, kind: 'calc', value, ...extra });
  const awa = (id: string, label: string, extra?: Partial<RowDef>): RowDef =>
    mk({ id, label, kind: 'awaiting', value: '—', ...extra });

  // ─── Deal-type-aware section set ───────────────────────────────────────
  const sections: SectionSpec[] = useMemo(() => {
    // FON-59 — Project Name (the analyst's confidential deal identifier,
    // deals.name) sits directly above Property Name (the asset as named in
    // the OM). Stored independently, displayed distinctly; editing one never
    // touches the other. Property Name NEVER falls back to the project name —
    // it reads '—' until the OM is extracted (or the analyst overrides it).
    const identityRows = (): RowDef[] => {
      // Overridden when the deal carries the key (immediate after a save) OR
      // the worker resolved the name from an override (authoritative read-back).
      const nameOverride = overrideText(overrides, PROPERTY_NAME_OVERRIDE_PATH);
      const overridden = nameOverride != null || (meta.nameSource === 'analyst_override' && meta.name != null);
      const propertyName = nameOverride ?? meta.name ?? null;
      const original = meta.nameOriginal;
      const docName = original?.doc_name?.trim() || 'Offering Memorandum';
      const docPage = original?.page != null ? `p. ${original.page}` : undefined;
      return [
        mk({
          id: 'projName', label: 'Project Name', kind: 'input', state: 'assumption',
          value: deal?.name?.trim() || '—', dealField: 'name', where: 'Analyst input',
          sub: 'Your confidential identifier for this deal (e.g. "Project Unicorn"). Never overwritten by document extraction; renaming it never changes the Property Name.',
        }),
        mk({
          id: 'pName', label: 'Property Name',
          kind: propertyName ? 'doc' : 'awaiting',
          state: overridden ? 'assumption' : propertyName ? 'document_sourced' : 'awaiting_data',
          value: propertyName ?? '—',
          docName, docPage,
          where: overridden ? 'Analyst override' : propertyName ? [docName, docPage].filter(Boolean).join(' · ') : 'Awaiting the Offering Memorandum',
          sub: 'The asset as named in the offering documents — distinct from the Project Name. Overriding keeps the extracted value so you can restore it.',
          overridePath: PROPERTY_NAME_OVERRIDE_PATH,
          overridden,
          original: original ? { value: original.value, docName: original.doc_name, page: original.page } : undefined,
        }),
      ];
    };

    const propertyRows = (): RowDef[] => [
      ...identityRows(),
      doc('pType', 'Property Type', deal?.service ?? '—', 'Offering Memorandum', 'Property Overview'),
      doc('pLoc', 'Location', deal?.city ?? '—', 'Offering Memorandum', 'Location'),
      doc('pYear', 'Year Built', meta.year_built != null ? String(Math.round(meta.year_built)) : '—', 'Offering Memorandum', 'Property History'),
      doc('pKeys', isDev ? 'Planned Keys' : 'Keys', keys != null ? String(keys) : '—', 'Offering Memorandum', 'Room Mix', { reasonKey: 'keys' }),
      awa('pFloors', isDev ? 'Planned Floors' : 'Floors'),
      doc('pSF', isDev ? 'Planned SF' : 'Total SF', meta.gba_sf != null ? `${Math.round(meta.gba_sf).toLocaleString('en-US')} SF` : '—', 'Offering Memorandum', 'Building Summary'),
      awa('pTitle', 'Title / Ownership'),
      doc('pLabor', 'Labor / Union Status', meta.labor ?? '—', 'Offering Memorandum', 'Operations'),
      // Canonical Overview v3 — single combined trailing-12 occupancy / ADR row,
      // sourced from the STR historicals (Financials → Historicals). '—' when
      // the deal has no STR history on file.
      lnk('opsTrail', 'Trailing-12 Occupancy / ADR',
        (meta.trailingOcc != null && Number.isFinite(meta.trailingOcc) && meta.trailingAdr != null && Number.isFinite(meta.trailingAdr))
          ? `${fmtPct(meta.trailingOcc, 1)} / ${fmtCurrency(meta.trailingAdr)}`
          : '—',
        '→ Financials (historicals)', 'pl'),
      lnk('brand', 'Brand', brand || '—', '→ Investment Profile', ''),
      lnk('positioning', 'Positioning', positioningTiers.find((p) => p.id === positioningId)?.label ?? '—', '→ Investment Profile', ''),
      lnk('mgmtFee', 'Management Fee', '—', '→ Financials', 'pl', { reasonKey: 'mgmt_fee_pct', linkSub: 'historicals' }),
      lnk('franchiseFee', 'Franchise / Brand Fee', '—', '→ Financials', 'pl', { linkSub: 'historicals' }),
    ];

    const entryRows = (): RowDef[] => [
      lnk('entryNOI', 'Run-Rate / Entry NOI', money(y1Noi), '→ Financials', 'pl', { linkSub: 'historicals' }),
      cal('entryCap', 'Entry Cap Rate', pctv(entryCap), { trace: { engine: 'capital', path: 'entry_cap_rate' }, formula: 'Entry NOI ÷ Purchase Price', inputs: [{ name: 'Entry NOI', from: 'Financials → Historicals', kind: 'linked' }, { name: 'Purchase Price', from: 'Calculated', kind: 'calc' }] }),
      cal('purchase', 'Purchase Price', money(purchase), { bold: true, trace: { engine: 'capital', path: 'purchase_price' }, formula: 'Entry NOI ÷ Entry Cap Rate', inputs: [{ name: 'Entry NOI', from: 'Financials → Historicals', kind: 'linked' }, { name: 'Entry Cap Rate', from: 'Calculated', kind: 'calc' }] }),
      cal('pricePerKey', 'Price / Key', money(pricePerKey), { trace: { engine: 'capital', path: 'price_per_key' }, formula: 'Purchase Price ÷ Keys', inputs: [{ name: 'Purchase Price', from: 'Calculated', kind: 'calc' }, { name: 'Keys', from: 'OM · Room Mix', kind: 'doc' }] }),
      lnk('acqDate', 'Acquisition Date', fmtISODate(timeline?.close_date), '→ Timeline (drives the schedule)', ''),
      awa('closingPct', 'Closing Costs %'),
      cal('closing', 'Closing Costs', money(closing), { trace: { engine: 'capital', path: 'closing_costs' }, formula: 'Purchase Price × Closing Costs %' }),
    ];

    const renovationRows = (): RowDef[] => [
      doc('renoScope', 'Renovation Scope', '—', 'PIP Scope of Work', 'Scope Summary'),
      lnk('renoPerKey', 'Renovation / Key', perKey(renoBudget), '→ Investment (renovation)', 'investment'),
      cal('renoHard', 'Hard Costs', money(renoHard), { formula: '75% of base renovation budget (PIP allocation)' }),
      cal('renoSoft', 'Soft Costs', money(renoSoft), { formula: '15% of base renovation budget' }),
      cal('renoProf', 'Professional Fees', money(renoProf), { formula: '10% of base renovation budget' }),
      awa('renoContPct', 'Contingency %'),
      cal('renoTotal', 'Total Renovation Budget', money(renoBudget), { bold: true, formula: 'Base renovation budget incl. contingency' }),
      cal('renoTotalPerKey', 'Total Renovation / Key', perKey(renoBudget), { formula: 'Total Renovation Budget ÷ Keys' }),
      awa('renoStart', 'Renovation Start'),
      lnk('renoDuration', 'Renovation Duration', timelineDuration(timeline, /renov/i), '→ Investment (schedule)', 'investment'),
    ];

    const capitalizationRows = (): RowDef[] => [
      lnk('loan', isDev ? 'Construction Loan' : 'Acquisition Loan', money(loan), '→ Debt (senior loan)', 'debt'),
      lnk('ltv', 'LTV', pctv(ltv, 1), '→ Debt (capital structure)', 'debt', { reasonKey: 'ltv' }),
      cal('ltc', 'LTC', pctv(ltc, 1), { formula: 'Loan ÷ Total Uses', inputs: [{ name: 'Loan', from: 'Debt module', kind: 'linked' }, { name: 'Total Uses', from: 'Calculated', kind: 'calc' }] }),
      lnk('bench', 'Benchmark', '—', '→ Debt (loan terms)', 'debt'),
      doc('spread', 'Spread over Benchmark', '—', 'Senior Loan Term Sheet', 'Pricing'),
      cal('allIn', 'All-In Rate', pctv(wInterestRate), { trace: { engine: 'debt', path: 'interest_rate' }, formula: 'Benchmark + Spread' }),
      lnk('finCosts', 'Financing Costs', money(financingCosts), '→ Debt (origination + legal)', 'debt'),
      cal('equity', isDev ? 'Equity Contribution' : 'Equity', money(equity), { bold: true, trace: { engine: 'capital', path: 'equity_amount' }, formula: 'Total Uses − Loan', inputs: [{ name: 'Total Uses', from: 'Calculated', kind: 'calc' }, { name: 'Loan', from: 'Debt module', kind: 'linked' }] }),
      awa('refi', isDev ? 'Permanent Financing' : 'Planned Refinancing'),
    ];

    const stabilizationRows = (): RowDef[] => [
      ...(hasReno ? [lnk('renoImpact', 'Renovation Impact', '—', '→ Financials (disruption)', 'pl', { linkSub: 'projections' })] : []),
      awa('stabDate', 'Stabilization Date'),
      lnk('stabOcc', 'Stabilized Occupancy', '—', '→ Financials (projections)', 'pl', { reasonKey: 'starting_occupancy', linkSub: 'projections' }),
      lnk('stabADR', 'Stabilized ADR', '—', '→ Financials (projections)', 'pl', { reasonKey: 'starting_adr', linkSub: 'projections' }),
      lnk('stabRev', 'Stabilized Revenue', '—', '→ Financials (projections)', 'pl', { linkSub: 'projections' }),
      lnk('stabNOI', 'Stabilized NOI', money(terminalNoi), '→ Financials (projections)', 'pl', { bold: true, linkSub: 'projections', trace: { engine: 'returns', path: 'terminal_noi' } }),
      cal('stabMargin', 'Stabilized NOI Margin', '—', { formula: 'Stabilized NOI ÷ Stabilized Revenue' }),
    ];

    const exitRows = (): RowDef[] => [
      lnk('hold', 'Hold Period', has(holdYears) ? `${holdYears} years` : '—', '→ Investment (exit)', 'investment', { reasonKey: 'hold_years' }),
      cal('exitDate', 'Exit Date', fmtISODate(timeline?.exit_date), { formula: 'Acquisition Date + Hold Period' }),
      lnk('fwdNOI', 'Forward 12-Month NOI', money(terminalNoi), '→ Financials (projections)', 'pl', { linkSub: 'projections' }),
      lnk('exitCap', 'Exit Cap Rate', pctv(exitCap), '→ Investment (exit)', 'investment', { reasonKey: 'exit_cap_rate' }),
      cal('exitValue', 'Gross Exit Value', money(grossExit), { bold: true, trace: { engine: 'returns', path: 'gross_sale_price' }, formula: 'Forward NOI ÷ Exit Cap Rate', inputs: [{ name: 'Forward NOI', from: 'Financials → Projections', kind: 'linked' }, { name: 'Exit Cap Rate', from: 'Investment assumption', kind: 'input' }] }),
      cal('exitPerKey', 'Exit Value / Key', money(exitPerKey), { formula: 'Gross Exit Value ÷ Keys' }),
      lnk('salesPct', 'Disposition Costs', money(sellingCosts), '→ Returns', 'returns', { trace: { engine: 'returns', path: 'selling_costs' } }),
      awa('transferPct', 'Transfer Tax'),
    ];

    // ── Development-specific sections ──
    const projectRows = (): RowDef[] => [
      ...identityRows(),
      lnk('pLoc', 'Location', deal?.city ?? '—', '→ Investment Profile', ''),
      lnk('brand', 'Brand', brand || '—', '→ Investment Profile', ''),
      lnk('positioning', 'Positioning', positioningTiers.find((p) => p.id === positioningId)?.label ?? '—', '→ Investment Profile', ''),
      lnk('pKeys', 'Planned Keys', keys != null ? String(keys) : '—', '→ Investment Profile', '', { reasonKey: 'keys' }),
      awa('pFloors', 'Planned Floors'),
      awa('pSF', 'Planned SF'),
      doc('pZoning', 'Zoning / Entitlement', '—', 'Zoning Report', 'Entitlement Status'),
      lnk('mgmtFee', 'Management Fee', '—', '→ Financials', 'pl', { reasonKey: 'mgmt_fee_pct', linkSub: 'historicals' }),
      lnk('franchiseFee', 'Franchise / Brand Fee', '—', '→ Financials', 'pl', { linkSub: 'historicals' }),
    ];
    const landRows = (): RowDef[] => [
      doc('landPrice', 'Land Purchase Price', money(landPrice), 'Land Purchase & Sale Agreement', 'Purchase Price'),
      cal('landPerKey', 'Land Cost / Key', perKey(landPrice), { formula: 'Land Purchase Price ÷ Planned Keys' }),
      awa('landClose', 'Land Closing Date'),
      awa('landCostPct', 'Acquisition Costs %'),
      awa('landCosts', 'Acquisition Costs'),
    ];
    const devBudgetRows = (): RowDef[] => [
      cal('bLand', 'Land Cost', money(landPrice), { formula: 'Land Purchase Price + Acquisition Costs' }),
      awa('hardPerKey', 'Hard Cost / Key'),
      cal('hard', 'Hard Costs', money(hardCosts), { trace: { engine: 'capital', path: 'hard_costs' } }),
      awa('softPct', 'Soft Costs %'),
      cal('soft', 'Soft Costs', money(softCosts)),
      awa('ffePerKey', 'FF&E / Key'),
      cal('ffe', 'FF&E', money(ffe)),
      cal('profFees', 'Professional Fees', money(profFees)),
      awa('devFee', 'Development Fee'),
      awa('preOpen', 'Pre-Opening Costs'),
      awa('contPct', 'Contingency %'),
      cal('contingency', 'Contingency', money(contingency)),
      lnk('loanFees', 'Financing Costs', money(financingCosts), '→ Debt', 'debt'),
      lnk('interestReserve', 'Interest Reserve', '—', '→ Debt (draw schedule)', 'debt'),
      cal('tdc', 'Total Development Cost', money(totalCapital), { bold: true, trace: { engine: 'capital', path: 'total_capital_usd' }, formula: 'Sum of all development budget lines' }),
      cal('tdcPerKey', 'Development Cost / Key', money(totalPerKey), { formula: 'Total Development Cost ÷ Planned Keys' }),
    ];
    const constFinRows = (): RowDef[] => [
      lnk('loan', 'Construction Loan', money(loan), '→ Debt (construction facility)', 'debt'),
      cal('ltc', 'LTC', pctv(ltc, 1), { formula: 'Construction Loan ÷ Total Development Cost' }),
      lnk('bench', 'Benchmark', '—', '→ Debt (loan terms)', 'debt'),
      doc('spread', 'Spread over Benchmark', '—', 'Construction Loan Term Sheet', 'Pricing'),
      cal('allIn', 'All-In Rate', pctv(wInterestRate), { trace: { engine: 'debt', path: 'interest_rate' }, formula: 'Benchmark + Spread' }),
      lnk('loanFees', 'Financing Costs', money(financingCosts), '→ Debt', 'debt'),
      lnk('draws', 'Loan Draws', '—', '→ Debt (draw schedule)', 'debt'),
      lnk('interestReserve', 'Interest Reserve', '—', '→ Debt (draw schedule)', 'debt'),
      cal('equity', 'Equity Contribution', money(equity), { bold: true, trace: { engine: 'capital', path: 'equity_amount' }, formula: 'Total Development Cost − Construction Loan' }),
      awa('permFin', 'Permanent Financing'),
    ];
    const openingRows = (): RowDef[] => [
      cal('openDate', 'Opening Date', fmtISODate(timeline?.stabilization_date), { formula: 'Land Close + pre-construction + build' }),
      awa('ramp', 'Ramp-Up Period'),
      awa('stabDate', 'Stabilization Date'),
      lnk('stabOcc', 'Stabilized Occupancy', '—', '→ Financials (projections)', 'pl', { reasonKey: 'starting_occupancy', linkSub: 'projections' }),
      lnk('stabADR', 'Stabilized ADR', '—', '→ Financials (projections)', 'pl', { reasonKey: 'starting_adr', linkSub: 'projections' }),
      cal('stabRevPAR', 'Stabilized RevPAR', '—', { formula: 'Stabilized Occupancy × Stabilized ADR' }),
      lnk('stabRev', 'Stabilized Revenue', '—', '→ Financials (projections)', 'pl', { linkSub: 'projections' }),
      lnk('stabNOI', 'Stabilized NOI', money(terminalNoi), '→ Financials (projections)', 'pl', { bold: true, linkSub: 'projections', trace: { engine: 'returns', path: 'terminal_noi' } }),
      cal('yieldOnCost', 'Yield on Cost', has(terminalNoi) && has(totalCapital) && totalCapital > 0 ? fmtPct(terminalNoi / totalCapital, 2) : '—', { formula: 'Stabilized NOI ÷ Total Development Cost' }),
    ];

    if (cfg === 'dev') {
      return [
        { kind: 'rows', title: 'Project', rows: projectRows() },
        { kind: 'rows', title: 'Land / Site Acquisition', rows: landRows() },
        { kind: 'rows', title: 'Development Budget', action: { label: 'View development details →', tab: 'investment' }, rows: devBudgetRows() },
        { kind: 'rows', title: 'Construction Financing', action: { label: 'View Debt details →', tab: 'debt' }, rows: constFinRows() },
        { kind: 'rows', title: 'Opening & Stabilization', action: { label: 'View Projections →', tab: 'pl', sub: 'projections' }, rows: openingRows() },
        { kind: 'rows', title: 'Exit', rows: exitRows() },
        { kind: 'su', title: 'Transaction Sources & Uses' },
        { kind: 'timeline', title: 'Development Timeline' },
      ];
    }
    if (cfg === 'core') {
      return [
        { kind: 'rows', title: 'Property', note: 'Extracted from diligence documents — override where needed', rows: propertyRows() },
        { kind: 'rows', title: 'Entry', rows: entryRows() },
        { kind: 'rows', title: 'Capitalization', action: { label: 'View Debt details →', tab: 'debt' }, rows: capitalizationRows() },
        { kind: 'rows', title: 'Exit', rows: exitRows() },
        { kind: 'su', title: 'Transaction Sources & Uses' },
        { kind: 'timeline', title: 'Transaction Timeline' },
      ];
    }
    // value-add
    return [
      { kind: 'rows', title: 'Property', note: 'Extracted from diligence documents — override where needed', rows: propertyRows() },
      { kind: 'rows', title: 'Entry Valuation', rows: entryRows() },
      { kind: 'rows', title: 'Renovation / CapEx', action: { label: 'View renovation details →', tab: 'investment' }, rows: renovationRows() },
      { kind: 'rows', title: 'Capitalization', action: { label: 'View Debt details →', tab: 'debt' }, rows: capitalizationRows() },
      { kind: 'rows', title: 'Stabilization', action: { label: 'View Projections →', tab: 'pl', sub: 'projections' }, rows: stabilizationRows() },
      { kind: 'rows', title: 'Exit', rows: exitRows() },
      { kind: 'su', title: 'Transaction Sources & Uses' },
      { kind: 'timeline', title: 'Transaction Timeline' },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg, isDev, meta, deal, overrides, keys, brand, positioningId, timeline, outputs, tracedState]);

  // ─── Review count (real needs-review provenance) ───────────────────────
  const reviewCount = useMemo(() => {
    let n = 0;
    for (const s of sections) if (s.kind === 'rows') for (const r of s.rows) if (r.state === 'needs_review') n++;
    return n;
  }, [sections]);

  const displaySections: SectionSpec[] = useMemo(() => {
    if (!reviewOnly) return sections;
    return sections
      .filter((s): s is RowsSection => s.kind === 'rows')
      .map((s) => ({ ...s, action: undefined, note: undefined, rows: s.rows.filter((r) => r.state === 'needs_review') }))
      .filter((s) => s.rows.length > 0);
  }, [reviewOnly, sections]);

  // ─── KPI tiles (deal-type-aware) ───────────────────────────────────────
  const kpis: { label: string; value: string; sub?: string }[] = useMemo(() => {
    if (cfg === 'dev') {
      return [
        { label: 'Total Dev. Cost', value: mm(totalCapital) },
        { label: 'Cost / Key', value: money(totalPerKey) },
        { label: 'Stabilized NOI', value: mm(terminalNoi) },
        { label: 'Exit Value', value: mm(grossExit), sub: has(exitCap) ? `${fmtPct(exitCap, 2)} exit cap` : undefined },
        { label: 'Levered IRR', value: pctv(leveredIrr, 1) },
      ];
    }
    if (cfg === 'core') {
      return [
        { label: 'Purchase Price', value: mm(purchase), sub: has(entryCap) ? `${fmtPct(entryCap, 2)} going-in` : undefined },
        { label: 'Going-In Cap', value: pctv(entryCap) },
        { label: 'Equity', value: mm(equity), sub: has(equity) && has(totalCapital) && totalCapital > 0 ? `${fmtPct(equity / totalCapital, 1)} of total uses` : undefined },
        { label: 'Exit Value', value: mm(grossExit), sub: has(exitCap) ? `${fmtPct(exitCap, 2)} exit cap` : undefined },
        { label: 'Levered IRR', value: pctv(leveredIrr, 1) },
      ];
    }
    return [
      { label: 'Purchase Price', value: mm(purchase), sub: has(entryCap) ? `${fmtPct(entryCap, 2)} going-in` : undefined },
      { label: 'Total Capitalization', value: mm(totalCapital), sub: has(totalPerKey) ? `${fmtCurrency(totalPerKey)} / key` : undefined },
      { label: 'Renovation', value: mm(renoBudget), sub: hasReno && has(keys) ? `${fmtCurrency((renoBudget as number) / keys)} / key` : undefined },
      { label: 'Stabilized NOI', value: mm(terminalNoi) },
      { label: 'Levered IRR', value: pctv(leveredIrr, 1) },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cfg, purchase, entryCap, totalCapital, totalPerKey, equity, grossExit, exitCap, terminalNoi, renoBudget, hasReno, keys, leveredIrr]);

  // ─── Return targets + benchmark strip (FON-68) ─────────────────────────
  // The strip compares the CALCULATED levered IRR (canonical returns run)
  // against the analyst's Target Levered IRR on the deal. Display only.
  const targetIrr = deal?.target_irr ?? null;
  const targetMoic = deal?.target_moic ?? null;
  const profileSuggestion = useMemo(
    () => suggestedTarget(returnProfiles.find((p) => p.id === returnProfileId)?.target),
    [returnProfileId],
  );
  const benchmark = useMemo(() => {
    const status = benchmarkStatus(targetIrr, has(leveredIrr) ? leveredIrr : null);
    const statusColor = status === 'Above target' ? 'oklch(45% 0.12 155)' : status === 'Within target' ? prov.green : status === 'Below target' ? 'oklch(50% 0.14 40)' : palette.textMuted;
    const statusBg = status === 'Below target' ? 'oklch(56% 0.12 40 / .12)' : (status === 'Pending' || status === 'No target') ? palette.hairlineSection : 'oklch(45% 0.12 155 / .1)';
    return { target: targetIrr != null ? fmtPct(targetIrr, 1) : '—', actual: pctv(leveredIrr, 1), status, statusColor, statusBg };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetIrr, leveredIrr]);

  // ─── Popover open / close ──────────────────────────────────────────────
  const openProv = useCallback((e: React.MouseEvent, row: RowDef) => {
    // Nothing to explain yet — unless the row can be overridden (Property
    // Name pre-OM) or is an analyst input, which still open for editing.
    if (row.kind === 'awaiting' && !row.overridePath && !row.dealField) return;
    const b = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const width = 346;
    const vw = typeof window !== 'undefined' ? window.innerWidth : 1440;
    let left = b.left;
    if (left + width > vw - 12) left = Math.max(12, vw - width - 12);
    const caretRight = Math.max(16, Math.min(width - 24, (b.left + b.width / 2) - left));
    const top = b.bottom + 8;
    setEditing(null);
    setPopover({ row, top, left, caretRight });
  }, []);

  const provProps: WhereThisCameFromProps | null = useMemo(() => {
    if (!popover) return null;
    // Editable rows resolve live so the popover reflects a save / restore
    // (value, kind, Original line) without being reopened.
    const editable = !!(popover.row.overridePath || popover.row.dealField);
    const live = editable
      ? sections.flatMap((s) => (s.kind === 'rows' ? s.rows : [])).find((r) => r.id === popover.row.id)
      : undefined;
    const row = live ?? popover.row;
    const t = row.trace ? traceGet(row.trace.engine, row.trace.path) : null;
    const kindLabel = row.overridden ? 'Overridden' : KIND_LABEL[row.kind];
    const kindColor = row.overridden ? popoverKind.overridden : KIND_POPOVER_COLOR[row.kind];
    const where = row.where ?? (row.kind === 'doc' ? (row.docPage ?? row.docName ?? 'Uploaded document')
      : row.kind === 'linked' ? (row.linkLabel ?? 'Another module')
        : row.kind === 'input' ? 'Investment Profile'
          : 'Calculated by Fondok');
    const inputs = (row.inputs ?? []).map((i) => ({
      name: i.name, path: i.from,
      dotColor: i.kind === 'linked' || i.kind === 'doc' ? prov.green : i.kind === 'input' ? prov.blue : prov.gray,
    }));
    const isEditing = editing?.rowId === row.id;
    const startEdit = () => setEditing({ rowId: row.id, draft: row.value === '—' ? '' : row.value });
    const actions: WhereThisCameFromProps['actions'] = [];
    if (row.kind === 'linked' && row.linkTab) {
      actions.push({ label: 'Open module →', primary: true, onClick: () => { navigate(row.linkTab!, row.linkSub); setPopover(null); } });
    }
    if (row.kind === 'doc' && row.docName && !row.overridden) {
      actions.push({ label: 'View source ↗', onClick: () => { navigate(''); setPopover(null); } });
    }
    if (row.dealField === 'name' && !isEditing) {
      actions.push({ label: 'Rename', primary: true, onClick: startEdit });
    }
    if (row.overridePath && !isEditing) {
      actions.push({ label: row.overridden ? 'Edit override' : 'Override', primary: !row.overridden, onClick: startEdit });
      if (row.overridden) {
        actions.push({ label: row.original ? 'Restore sourced value' : 'Clear override', onClick: () => { void restoreOverride(row); } });
      }
    }
    // Override card — "Original: <extracted> · <doc> p.<n>" (+ the stored note).
    const originalLine = row.original
      ? `${row.original.value} · ${row.original.docName?.trim() || 'Offering Memorandum'}${row.original.page != null ? ` p.${row.original.page}` : ''}`
      : '— (awaiting the Offering Memorandum)';
    const note = row.overridePath ? overrideNote(overrides, row.overridePath) : null;
    const override: WhereThisCameFromProps['override'] = row.overridden && row.overridePath
      ? {
        orig: row.original?.value ?? '—', current: row.value,
        meta: [{ k: 'Original', v: originalLine }, ...(note ? [{ k: 'Note', v: note }] : [])],
      }
      : undefined;
    const editor: WhereThisCameFromProps['editor'] = isEditing && editing
      ? {
        value: editing.draft,
        onChange: (v) => setEditing({ rowId: row.id, draft: v }),
        onSave: () => { if (row.dealField === 'name') void renameProject(editing.draft); else void saveOverride(row, editing.draft); },
        onCancel: () => setEditing(null),
        hint: row.dealField === 'name'
          ? 'Shown in the deal header and the Pipeline. Never changes the Property Name.'
          : 'Overrides keep their original source so you can restore it.',
        saveLabel: row.dealField === 'name' ? 'Save name' : 'Save value',
        placeholder: row.dealField === 'name' ? 'e.g. Project Unicorn' : row.label,
        saving,
        ariaLabel: `${row.label} value`,
      }
      : undefined;
    return {
      kind: kindLabel, kindColor, label: row.label, where, value: row.value,
      valueColor: valueColor(row.kind, !!row.bold, !!row.overridden),
      sub: row.sub,
      source: row.kind === 'doc' && row.docName && !row.overridden ? { doc: row.docName, loc: row.docPage ?? '', text: t?.note ?? '' } : undefined,
      calc: row.kind === 'calc' && (t?.formula || row.formula)
        ? { expr: t?.formula ?? row.formula, numbers: row.formulaNumbers, inputs: inputs.length ? inputs : undefined }
        : undefined,
      override,
      editor,
      actions: actions.length ? actions : undefined,
      position: 'fixed', top: popover.top, left: popover.left, caretRight: popover.caretRight,
      onClose: () => { setEditing(null); setPopover(null); },
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [popover, traceGet, sections, editing, saving, overrides, saveOverride, restoreOverride, renameProject]);

  // FON-59 #4 — `?tab=<tab>&sub=<subtab>` is the sub-tab routing convention;
  // without the `sub` half every "→ Financials (projections)" row landed on
  // Historicals (the target's default).
  function navigate(tab: string, sub?: string) {
    if (!tab) { router.push(`/projects/${dealId}`); return; }
    router.push(`/projects/${dealId}?tab=${tab}${sub ? `&sub=${sub}` : ''}`);
  }

  // Deal-type toggle → confirmation → persist + re-run.
  const onSelectDealType = (id: 'acquisition' | 'development') => {
    if (id === dealType) return;
    setPendingDealType(id);
    setPopover(null);
  };
  const confirmDealType = () => {
    if (!pendingDealType) return;
    void persist({ deal_type: pendingDealType }, 'Deal type updated — re-running the model…');
    setPendingDealType(null);
  };
  const pendingCfg: Cfg = pendingDealType === 'development' ? 'dev' : (returnProfileId === 'core' ? 'core' : 'va');

  // ─── Render ────────────────────────────────────────────────────────────
  return (
    <div style={{ fontFamily: fontStack, display: 'flex', flexDirection: 'column', gap: 14, maxWidth: 1320 }}>
      {/* Overview header card */}
      <div style={{ ...cardShell, padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ fontSize: 13.5, fontWeight: 700, color: palette.ink }}>Overview</span>
        <span style={{ fontSize: 12.5, color: palette.textSecondary, lineHeight: 1.55, maxWidth: 960 }}>
          The deal on one page — what you&apos;re buying, how it&apos;s capitalized, when each milestone lands,
          and the returns that fall out of it.
        </span>
      </div>

      {/* Review bar (only when Fondok flagged values for review) */}
      {reviewCount > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontSize: 11.5, color: palette.textSecondary }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 7, fontWeight: 600, color: palette.ink }}>
            <ProvenanceDot state="needs_review" size={9} />
            {reviewCount} field{reviewCount === 1 ? '' : 's'} need review
          </span>
          <button
            type="button"
            onClick={() => setReviewOnly((v) => !v)}
            style={{
              fontSize: 11.5, fontWeight: 600, color: palette.ink, fontFamily: 'inherit', cursor: 'pointer',
              background: reviewOnly ? 'oklch(94% 0.05 65)' : '#fff',
              border: `1px solid ${reviewOnly ? 'oklch(80% 0.09 65)' : palette.disabledBorder}`,
              borderRadius: 6, padding: '4px 11px', whiteSpace: 'nowrap',
            }}
          >
            {reviewOnly ? 'Showing review only — clear filter' : 'Filter to these'}
          </button>
          <span style={{ color: palette.textFaint }}>
            Values Fondok flagged as uncertain or contradicted by a second source.
          </span>
        </div>
      )}

      {/* Investment Profile card + return benchmark strip */}
      <div style={{ ...cardShell, padding: '11px 16px 12px', display: 'flex', flexDirection: 'column', gap: 11 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '118px 1fr', gap: 16, alignItems: 'center' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.06em', color: palette.eyebrow, textTransform: 'uppercase', lineHeight: 1.25 }}>
              Investment Profile
            </div>
            <div style={{ fontSize: 10, color: palette.textFaint, lineHeight: 1.35 }}>Deal Type drives the model</div>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(168px,1fr))', gap: '9px 18px' }}>
            {/* Deal Type toggle */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
              <div style={profileLabel}>Deal Type</div>
              <div style={{ display: 'inline-flex', background: '#f3f2ee', border: `1px solid ${palette.border}`, borderRadius: 6, padding: 2, width: 'fit-content' }}>
                {DEAL_TYPES.map((d) => {
                  const active = d.id === dealType;
                  return (
                    <button
                      key={d.id}
                      type="button"
                      onClick={() => onSelectDealType(d.id)}
                      style={{
                        fontSize: 11.5, fontFamily: 'inherit', border: 'none', cursor: 'pointer',
                        fontWeight: active ? 700 : 500, color: active ? prov.blue : '#a8a7a2',
                        background: active ? '#fff' : 'transparent', borderRadius: 4, padding: '4px 11px',
                        whiteSpace: 'nowrap', boxShadow: active ? '0 1px 2px rgba(0,0,0,.08)' : 'none',
                      }}
                    >
                      {d.label}
                    </button>
                  );
                })}
              </div>
              <div style={{ fontSize: 10, color: palette.textFaint, lineHeight: 1.3 }}>Configures the underwriting model</div>
            </div>

            <ProfileSelect
              label="Returns Profile" hint="Suggests the target band only" value={returnProfileId}
              options={returnProfiles.map((p) => ({ value: p.id, label: `${p.label} (${p.target})` }))}
              onChange={(v) => void persist({ return_profile: v })}
            />
            <ProfileSelect
              label="Brand" hint="Property classification" value={brand}
              options={brandFamilies.flatMap((f) => f.brands.map((b) => ({ value: b.name, label: `${b.name} (${b.tier})` })))}
              onChange={(v) => void persist({ brand: v })}
            />
            <ProfileSelect
              label="Positioning" hint="Property classification" value={positioningId}
              options={positioningTiers.map((p) => ({ value: p.id, label: p.label }))}
              onChange={(v) => void persist({ positioning: v })}
            />
            {/* FON-68 — return targets: analyst inputs, source of truth for the
                benchmark strip + Returns → Pricing. Unset renders "—"; the
                profile band is offered as an explicit action, never applied. */}
            <TargetField
              label="Target Levered IRR"
              hint="Analyst input · benchmark and pricing hurdle"
              value={targetIrr}
              unit="%"
              step={0.5}
              onSave={(v) => void persistTarget({ target_irr: v })}
              action={targetIrr == null && profileSuggestion ? {
                label: `Use profile ${profileSuggestion.kind} (${profileSuggestion.pct}%)`,
                onClick: () => void persistTarget({ target_irr: profileSuggestion.pct / 100 }),
              } : undefined}
            />
            <TargetField
              label="Target MOIC"
              hint="Analyst input · pricing hurdle"
              value={targetMoic}
              unit="x"
              step={0.05}
              onSave={(v) => void persistTarget({ target_moic: v })}
            />
          </div>
        </div>

        {/* Return benchmark strip */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', background: palette.surfaceTint, border: `1px solid ${palette.border}`, borderRadius: 7, padding: '8px 12px' }}>
          <span style={{ fontSize: 9.5, fontWeight: 700, letterSpacing: '.06em', color: palette.eyebrow, textTransform: 'uppercase' }}>Return benchmark</span>
          <span style={{ fontSize: 11.5, color: palette.textSecondary }}>Target levered IRR <b style={{ color: prov.blue }}>{benchmark.target}</b></span>
          <span style={{ fontSize: 11.5, color: palette.textSecondary }}>Calculated <b style={{ color: prov.green }}>{benchmark.actual}</b></span>
          <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '.03em', textTransform: 'uppercase', color: benchmark.statusColor, background: benchmark.statusBg, borderRadius: 5, padding: '3px 8px' }}>{benchmark.status}</span>
          {targetIrr == null && (
            <span style={{ fontSize: 10.5, color: palette.textMuted }}>Set Target Levered IRR above</span>
          )}
          <span style={{ fontSize: 10.5, color: palette.textFaint, marginLeft: 'auto' }}>Benchmark only — it does not drive the model</span>
        </div>
      </div>

      {/* KPI tiles */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(150px,1fr))', gap: 12 }}>
        {kpis.map((k) => (
          <KpiTile key={k.label} label={k.label} value={k.value} sub={k.sub} />
        ))}
      </div>

      {/* Deal-type-aware sections */}
      {displaySections.map((s, i) => {
        if (s.kind === 'su') return <SourcesUsesSection key={`su-${i}`} title={s.title} outputs={outputs} keys={keys} money={money} onRowClick={openProv} tracedState={tracedState} />;
        if (s.kind === 'timeline') return <TimelineSectionCard key={`tl-${i}`} title={s.title} timeline={timeline} liveMode={liveMode} holdYears={holdYears} />;
        const headerNote: ReactNode = s.action
          ? (
            <span onClick={() => navigate(s.action!.tab, s.action!.sub)} style={{ color: palette.linkBlue, cursor: 'pointer', fontWeight: 600, whiteSpace: 'nowrap' }}>
              {s.action.label}
            </span>
          )
          : s.note;
        return (
          <SectionCard key={s.title} title={s.title} note={headerNote}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: '0 32px' }}>
              {s.rows.map((r) => <OverviewRow key={r.id} row={r} onClick={openProv} />)}
            </div>
          </SectionCard>
        );
      })}

      {/* Where this came from — anchored provenance popover */}
      {provProps && (
        <>
          <div onClick={() => setPopover(null)} style={{ position: 'fixed', inset: 0, zIndex: 30 }} aria-hidden />
          <WhereThisCameFrom {...provProps} style={{ zIndex: 31 }} />
        </>
      )}

      {/* Deal-type change confirmation — "Update model" re-runs */}
      {pendingDealType && (
        <>
          <div onClick={() => setPendingDealType(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(16,24,40,.34)', zIndex: 80 }} aria-hidden />
          <div role="dialog" aria-label="Change deal type" style={{ position: 'fixed', left: '50%', top: '22vh', transform: 'translateX(-50%)', width: 432, background: '#fff', borderRadius: 11, boxShadow: '0 24px 56px rgba(16,24,40,.28)', zIndex: 81, padding: '22px 24px', display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ fontSize: 15, fontWeight: 700, color: palette.ink }}>
              Change deal type to {pendingDealType === 'development' ? 'Development' : 'Acquisition'}?
            </div>
            <div style={{ fontSize: 12.5, color: palette.hoverInk, lineHeight: 1.55 }}>
              Changing the deal type will update the assumptions and modeling sections used for this investment.
              Existing values that are no longer applicable will be preserved but removed from the active model.
            </div>
            <div style={{ background: palette.surfaceTint, border: `1px solid ${palette.border}`, borderRadius: 8, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 5 }}>
              <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: palette.eyebrow, textTransform: 'uppercase' }}>Sections after the change</div>
              <div style={{ fontSize: 12, color: palette.ink, lineHeight: 1.5 }}>{sectionTitles(pendingCfg)}</div>
            </div>
            <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end', marginTop: 2 }}>
              <button type="button" onClick={() => setPendingDealType(null)} style={secondaryBtn}>Cancel</button>
              <button type="button" onClick={confirmDealType} style={primaryBtn}>Update model</button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Section row — canonical Overview row (dot · label · link · value), clickable
// to open the "Where this came from" popover.
// ─────────────────────────────────────────────────────────────────────────
function OverviewRow({ row, onClick }: { row: RowDef; onClick: (e: React.MouseEvent, row: RowDef) => void }) {
  const color = valueColor(row.kind, !!row.bold, !!row.overridden);
  const showDot = row.kind === 'doc' || row.kind === 'linked' || row.state === 'needs_review';
  const underline = row.kind === 'input' || row.overridden ? 'underline dotted' : 'none';
  // Phase 4.4 — a bare dash on a row whose assumption key the worker has
  // refused gets the reason on hover. Null reason (every build today, and
  // every row with no `reasonKey`) renders the glyph and nothing else.
  const reason = useRefusal(row.reasonKey);
  const refusable = row.value === REFUSAL_GLYPH && reason != null;
  // Awaiting rows are inert — unless they can be overridden / edited (FON-59).
  const interactive = row.kind !== 'awaiting' || !!row.overridePath || !!row.dealField;
  return (
    <div
      onClick={(e) => onClick(e, row)}
      style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, fontSize: 13,
        padding: '7px 0', cursor: interactive ? 'pointer' : 'default', borderBottom: `1px solid ${palette.hairlineRow}`,
      }}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
        <span style={{ color: palette.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.label}</span>
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
        {showDot && <ProvenanceDot state={row.state} size={8} review={row.state === 'needs_review'} />}
        <span style={{ color, fontWeight: row.bold ? 700 : 400, textDecoration: underline, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>
          {refusable ? <Refused reason={reason} /> : row.value}
        </span>
      </span>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Investment Profile select (Returns Profile / Brand / Positioning).
// ─────────────────────────────────────────────────────────────────────────
function ProfileSelect({
  label, hint, value, options, onChange,
}: {
  label: string; hint: string; value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  const known = options.some((o) => o.value === value);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
      <div style={profileLabel}>{label}</div>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: '100%', fontSize: 11.5, fontFamily: 'inherit', fontWeight: 600, color: prov.blue,
          background: palette.surfaceTint, border: `1px solid ${palette.disabledBorder}`, borderRadius: 6,
          padding: '5px 9px', cursor: 'pointer', textOverflow: 'ellipsis',
        }}
      >
        {!known && value && <option value={value}>{value}</option>}
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <div style={{ fontSize: 10, color: palette.textFaint, lineHeight: 1.3 }}>{hint}</div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// FON-68 — Investment Profile return target (Target Levered IRR / Target MOIC).
// An analyst input (blue assumption dot). Unset renders "—" in the input
// with the optional explicit action beside it; Enter / blur saves; the ×
// clears (writes null). Percent targets are typed as "15" and stored 0.15.
// ─────────────────────────────────────────────────────────────────────────
function TargetField({
  label, hint, value, unit, step, onSave, action,
}: {
  label: string; hint: string; value: number | null; unit: '%' | 'x'; step: number;
  onSave: (v: number | null) => void;
  action?: { label: string; onClick: () => void };
}) {
  const shown = value == null ? '' : unit === '%' ? String(Math.round(value * 1000) / 10) : String(Math.round(value * 100) / 100);
  const [draft, setDraft] = useState(shown);
  useEffect(() => { setDraft(shown); }, [shown]);
  const commit = () => {
    const t = draft.trim();
    if (t === '') { if (value != null) onSave(null); return; }
    const n = Number(t);
    if (!Number.isFinite(n)) { setDraft(shown); return; }
    const next = unit === '%' ? n / 100 : n;
    if (value != null && Math.abs(next - value) < 1e-9) return;
    onSave(next);
  };
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>
      <div style={{ ...profileLabel, display: 'flex', alignItems: 'center', gap: 5 }}>
        <ProvenanceDot state="assumption" size={7} title="Analyst input" />
        {label}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 0 }}>
          <input
            type="number"
            step={step}
            aria-label={`${label} value`}
            placeholder="—"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commit}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); (e.currentTarget as HTMLInputElement).blur(); } }}
            style={{
              width: '100%', fontSize: 11.5, fontFamily: 'inherit', fontWeight: 600, color: prov.blue,
              background: palette.surfaceTint, border: `1px solid ${palette.disabledBorder}`, borderRadius: 6,
              padding: '5px 22px 5px 9px', fontVariantNumeric: 'tabular-nums',
            }}
          />
          <span style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', fontSize: 11, color: palette.textMuted, pointerEvents: 'none' }}>{unit}</span>
        </div>
        {value != null && (
          <button type="button" aria-label={`Clear ${label}`} title="Clear target" onClick={() => onSave(null)}
            style={{ background: 'none', border: 'none', color: palette.textMuted, cursor: 'pointer', fontSize: 13, padding: '0 2px', fontFamily: 'inherit' }}>
            ×
          </button>
        )}
      </div>
      {action && (
        <button type="button" onClick={action.onClick}
          style={{ alignSelf: 'flex-start', background: 'none', border: 'none', padding: 0, fontFamily: 'inherit', fontSize: 10.5, fontWeight: 600, color: palette.linkBlue, cursor: 'pointer' }}>
          {action.label}
        </button>
      )}
      <div style={{ fontSize: 10, color: palette.textFaint, lineHeight: 1.3 }}>{hint}</div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Sources & Uses — single section, Uses / Sources columns (Amount / Key / %)
// + "Equity is the calculated plug" note. From the capital engine arrays.
// ─────────────────────────────────────────────────────────────────────────
interface CapitalLine { label: string; amount: number; pct?: number | null; is_total?: boolean }

function SourcesUsesSection({
  title, outputs, keys, money, onRowClick, tracedState,
}: {
  title: string;
  outputs: EngineOutputsResponse | null;
  keys: number | undefined;
  money: (v: number | undefined) => string;
  onRowClick: (e: React.MouseEvent, row: RowDef) => void;
  tracedState: (engine: 'capital' | 'returns' | 'debt' | 'expense', path: string) => ValueState | null;
}) {
  const uses = getEngineField<CapitalLine[]>(outputs, 'capital', 'uses') ?? [];
  const sources = getEngineField<CapitalLine[]>(outputs, 'capital', 'sources') ?? [];
  const usesTotal = uses.find((r) => r.is_total)?.amount ?? uses.reduce((s, r) => (r.is_total ? s : s + (r.amount ?? 0)), 0);
  const sourcesTotal = sources.find((r) => r.is_total)?.amount ?? sources.reduce((s, r) => (r.is_total ? s : s + (r.amount ?? 0)), 0);

  const suGrid = 'minmax(88px,1fr) minmax(76px,96px) minmax(58px,82px) minmax(40px,50px)';

  const classify = (label: string): ValueKind => {
    if (/senior loan|senior debt|loan|key money/i.test(label)) return 'linked';
    if (/equity/i.test(label)) return 'calc';
    if (/purchase/i.test(label)) return 'calc';
    return 'calc';
  };
  const toRow = (l: CapitalLine, side: 'uses' | 'sources'): RowDef => {
    const total = !!l.is_total;
    const kind: ValueKind = total ? 'calc' : classify(l.label);
    return {
      id: `${side}-${l.label}`, label: l.label, kind, value: money(l.amount), bold: total,
      state: total ? 'calculated' : (tracedState('capital', `${side}.${l.label}`) ?? kindToState(kind)),
      linkLabel: kind === 'linked' ? (/key money/i.test(l.label) ? '→ Partnership' : '→ Debt') : undefined,
      linkTab: kind === 'linked' ? (/key money/i.test(l.label) ? 'partnership' : 'debt') : undefined,
    };
  };

  const columns: { heading: string; rows: CapitalLine[]; total: number; footnote?: ReactNode }[] = [
    { heading: 'Uses', rows: uses, total: usesTotal },
    { heading: 'Sources', rows: sources, total: sourcesTotal, footnote: 'Equity is the calculated plug — total uses less all other sources.' },
  ];

  return (
    <SectionCard title={title}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(340px,1fr))', gap: '14px 36px' }}>
        {columns.map((col) => (
          <div key={col.heading}>
            <div style={{ display: 'grid', gridTemplateColumns: suGrid, fontSize: 10.5, fontWeight: 700, letterSpacing: '.05em', color: palette.textFaint, textTransform: 'uppercase', paddingBottom: 6, borderBottom: `1px solid ${palette.border}` }}>
              <span>{col.heading}</span>
              <span style={{ textAlign: 'right' }}>Amount</span>
              <span style={{ textAlign: 'right' }}>/ Key</span>
              <span style={{ textAlign: 'right' }}>%</span>
            </div>
            {col.rows.length === 0 && (
              <div style={{ fontSize: 12.5, color: palette.textMuted, padding: '10px 0' }}>Run the Capital engine to populate {col.heading.toLowerCase()}.</div>
            )}
            {col.rows.map((l, i) => {
              const row = toRow(l, col.heading.toLowerCase() as 'uses' | 'sources');
              const total = !!l.is_total;
              const color = valueColor(row.kind, total, false);
              const pk = (has2(l.amount) && keys && keys > 0 && !total) ? fmtCurrency(l.amount / keys) : (total ? '' : '—');
              const pct = total ? '100.0%' : (has2(l.amount) && col.total ? `${((l.amount / col.total) * 100).toFixed(1)}%` : '—');
              const showDot = row.kind === 'linked' || row.state === 'needs_review';
              return (
                <div
                  key={`${l.label}-${i}`}
                  onClick={(e) => onRowClick(e, row)}
                  style={{ display: 'grid', gridTemplateColumns: suGrid, fontSize: 12.5, padding: '6px 0', cursor: 'pointer', borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center' }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                    {showDot && <ProvenanceDot state={row.state} size={8} />}
                    <span style={{ color: palette.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.label}</span>
                  </span>
                  <span style={{ textAlign: 'right', color, fontWeight: total ? 700 : 400, fontVariantNumeric: 'tabular-nums' }}>{money(l.amount)}</span>
                  <span style={{ textAlign: 'right', color: palette.textMuted, fontVariantNumeric: 'tabular-nums' }}>{pk}</span>
                  <span style={{ textAlign: 'right', color: palette.textMuted, fontVariantNumeric: 'tabular-nums' }}>{pct}</span>
                </div>
              );
            })}
            {col.footnote && (
              <div style={{ fontSize: 11.5, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>{col.footnote}</div>
            )}
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

// ─────────────────────────────────────────────────────────────────────────
// Milestone timeline rail — from GET /deals/{id}/engines/timeline (FON-71).
// ─────────────────────────────────────────────────────────────────────────
function TimelineSectionCard({
  title, timeline, liveMode, holdYears,
}: {
  title: string;
  timeline: TimelineResponse | null;
  liveMode: boolean;
  holdYears: number | undefined;
}) {
  const events = timeline?.events ?? [];
  const pending = !timeline?.close_date;
  const holdCaption = (!pending && timeline?.close_date && timeline?.exit_date)
    ? `${holdYears != null ? `${holdYears}-year hold` : 'Hold'} · ${fmtLongDate(timeline.close_date)} → ${fmtLongDate(timeline.exit_date)}`
    : 'Set the Acquisition Date to populate dates';
  const stateForBasis = (basis: string): ValueState =>
    basis === 'assumption' ? 'assumption' : basis === 'pending' ? 'awaiting_data' : 'calculated';
  const railCols = `repeat(${Math.max(1, events.length)},minmax(0,1fr))`;

  return (
    <SectionCard title={title} note={holdCaption} bodyStyle={{ padding: '18px 20px' }}>
      {events.length === 0 ? (
        <p style={{ fontSize: 12.5, color: palette.textMuted, paddingTop: 10 }}>
          {liveMode ? 'Run the model to build the timeline.' : 'Timeline is available on live deals.'}
        </p>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: railCols, position: 'relative', marginTop: 14 }}>
          <div style={{ position: 'absolute', left: '6%', right: '6%', top: 5, height: 2, background: palette.border }} />
          {events.map((m, i) => (
            <div key={`${m.event}-${i}`} style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, padding: '0 4px', textAlign: 'center' }}>
              <ProvenanceDot state={stateForBasis(m.basis)} size={12} style={{ boxShadow: '0 0 0 2px #fff, 0 0 0 4px #eae9e4', zIndex: 1 }} />
              <span style={{ fontSize: 12.5, fontWeight: 700, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>{fmtISODate(m.start ?? m.finish)}</span>
              <span style={{ fontSize: 11.5, color: palette.textSecondary, lineHeight: 1.35 }}>{m.event}</span>
              <span style={{ fontSize: 10.5, color: palette.textFaint }}>{(m.duration_months ?? 0) > 0 ? `${m.duration_months} months` : ''}</span>
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  );
}

// ─── Local helpers / style constants ─────────────────────────────────────
const cardShell: CSSProperties = { background: palette.cardWhite, border: `1px solid ${palette.border}`, borderRadius: 10 };
const profileLabel: CSSProperties = { fontSize: 9.5, fontWeight: 700, letterSpacing: '.06em', color: palette.textMuted, textTransform: 'uppercase' };
const primaryBtn: CSSProperties = { background: palette.inkNavy, color: '#fff', border: 'none', borderRadius: 6, padding: '8px 16px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' };
const secondaryBtn: CSSProperties = { background: '#fff', border: `1px solid ${palette.disabledBorder}`, color: palette.hoverInk, borderRadius: 6, padding: '8px 16px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' };

function has2(v: number | undefined | null): v is number { return v != null && Number.isFinite(v); }

/** Parse a return-profile target string ("8-12%", "18%+") to [lo, hi|null]. */
function parseTarget(target: string | undefined): [number | null, number | null] {
  if (!target) return [null, null];
  const plus = /\+/.test(target);
  const nums = (target.match(/\d+(?:\.\d+)?/g) ?? []).map(Number);
  if (nums.length === 0) return [null, null];
  if (plus || nums.length === 1) return [nums[0], null];
  return [nums[0], nums[1]];
}

/** FON-68 — the explicit action offered for an unset Target Levered IRR:
 *  the band midpoint ("12-18%" → 15) or, for an open band ("18%+"), its
 *  floor. Never applied implicitly. */
function suggestedTarget(target: string | undefined): { pct: number; kind: 'midpoint' | 'floor' } | null {
  const [lo, hi] = parseTarget(target);
  if (lo == null) return null;
  if (hi == null) return { pct: lo, kind: 'floor' };
  return { pct: Math.round(((lo + hi) / 2) * 10) / 10, kind: 'midpoint' };
}

/** FON-68 — benchmark badge. A hurdle is a floor: below it = "Below target";
 *  at or above it = "Within target" up to 200bp over (the same 200bp band the
 *  sensitivity classification uses), beyond that "Above target". */
function benchmarkStatus(target: number | null, calculated: number | null):
  'Within target' | 'Above target' | 'Below target' | 'Pending' | 'No target' {
  if (target == null) return 'No target';
  if (calculated == null || !Number.isFinite(calculated)) return 'Pending';
  if (calculated < target) return 'Below target';
  if (calculated >= target + 0.02) return 'Above target';
  return 'Within target';
}

/** Duration (in months) of the timeline event matching `re`, formatted. */
function timelineDuration(timeline: TimelineResponse | null, re: RegExp): string {
  const e = (timeline?.events ?? []).find((ev) => re.test(ev.event));
  return e && (e.duration_months ?? 0) > 0 ? `${e.duration_months} months` : '—';
}

/** Format an ISO date (YYYY-MM-DD) as M/D/YYYY without a timezone shift. */
function fmtISODate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return '—';
  return `${Number(m[2])}/${Number(m[3])}/${m[1]}`;
}

/** Format an ISO date as "Mon D, YYYY" (the canonical caption format). */
function fmtLongDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return '—';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${months[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`;
}
