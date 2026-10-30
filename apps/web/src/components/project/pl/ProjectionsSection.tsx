'use client';
/**
 * ProjectionsSection — Lovable-parity multi-year proforma projections.
 *
 * Renders the "PRELIMINARY HOTEL UNDERWRITING / Proforma Projections"
 * table. Each year column shows Amount / % Rev / PAR / POR sub-columns.
 * Rows include hotel delivery, days, room counts, occupancy, ADR, RevPAR +
 * RevPAR growth, and the REVENUES section (Rooms / F&B / Other / Total).
 *
 * FON-41 #2 — the column vocabulary, and why it reads the way it does:
 *
 *  • ``revenue.years[0]`` IS model operating Year 1. The engine runner passes
 *    it to the returns engine as ``year_one_noi`` and the Cash Flow tab labels
 *    the same value "Year 1". This statement used to call it "Base Year" and
 *    then label index 1 "Year 1" — one number with two names, and every later
 *    column a year ahead of its own label (Sam: *"Base Year and Year 1 are
 *    both 2025"*). The first column is now **Base year (Year 1)** and the rest
 *    are shifted, so index 1 heads "Year 2". No engine value moved: the labels
 *    were wrong, not the math.
 *  • ``RevenueProjectionYear.year`` is an ORDINAL (1..hold_years), never a
 *    calendar year. The calendar comes from ``revenue.projection_calendar_years``
 *    (anchored on the acquisition close date). FON-41 E-014: every column
 *    header carries its calendar year inline — "Year 2 · 2026" — and the base
 *    column names the primary statement's period on its basis line ("T12 Mar
 *    2025") when the Data Room exposes one. With no close date the column shows
 *    its label alone and a one-line note says where the date is set — never a
 *    guessed year.
 *  • FON-41 E-015: every projected Occupancy / ADR / RevPAR / revenue cell is a
 *    click-through to the lineage drawer for its engine field
 *    (``revenue.years[i].adr``). A cell the lineage record does not cover says
 *    "No trace available for this cell." — this file never invents a formula.
 *  • The horizon is ``hold_years + 1`` columns: every modelled year plus the
 *    **Exit Year**, which is display-only. It carries the Forward 12-Month
 *    Cash NOI the reversion is valued on (``returns.terminal_noi``) and dashes
 *    everywhere else, because year hold+1 is NOT run through the expense
 *    waterfall. Modelling it would move gross sale, both IRRs and MOIC.
 *
 * Sources:
 *  - Worker: ``revenue.years`` + ``fb.years`` + ``expense.years`` via
 *    ``useEngineOutputs``. Column i = engine years[i], 1:1.
 *
 * Helpers (mirroring Historicals):
 *   PAR  = Amount / Available Rooms × 1000
 *   POR  = Amount / Occupied Rooms  × 1000
 *   %Rev = Amount / Total Revenue   × 100
 */

import { useMemo, useState, useEffect, useCallback, useContext, createContext, type ReactNode, type CSSProperties } from 'react';
import Link from 'next/link';
import { Sparkles, FileText } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import {
  ProvenanceDot,
  NO_OP_EDIT_MESSAGE,
  useInlineEdit,
  InlineEditControls,
  inlineEditInputStyle,
} from '@/components/design';
import { isNoOpEdit } from '@/lib/fieldValue';
import {
  applyOverridePatch,
  overrideEnvelope,
  overrideNoteFor,
  requiresNote,
  NOTE_PLACEHOLDER,
  NOTE_REQUIRED_MESSAGE,
} from '@/lib/overrideNote';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/format';
import { PL_SUB_LABEL, TAB_LABEL } from '@/lib/projectTabs';
import {
  CASH_NOI_LABEL,
  noiBeforeReserveLabel,
  stabilizedYearBlock,
  stabilizationBadge,
  stabilizationSignalNote,
  type StabilizedYearBlock,
} from '@/lib/engines/noi';
import { Traced } from '@/components/help/Traced';
import { openLineage, type LineageOpenDetail } from '@/components/project/LineageDrawer';
import { HScroll } from './HScroll';
import {
  CellCommentsProvider, CommentMarker, CommentedCellsToggle, useCellCommentsState, useCommentFilter,
} from './CellComments';
import { PlRoundTripControls } from './PlRoundTrip';
import { projCommentKey } from '@/lib/cellComments';
import { Sourced } from '@/components/help/Sourced';
import { useRefusal } from '@/components/help/Refused';
import { useSource } from '@/lib/hooks/useDealProvenance';
import { sourceKind, sourceLabel, sourceExplanation, KIND_TONE, isStrMarketOverride, isStrBasisSource } from '@/lib/provenance';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { displacementStatusLine, useDisplacementStatus } from '@/lib/hooks/useDisplacementStatus';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useDeal } from '@/lib/hooks/useDeal';
import { api } from '@/lib/api';
import {
  MethodChip,
  MethodChipContext,
  methodOverrideKeys,
  type LineMethodInfo,
  type MethodChipCtx,
  type ProjectionMethodLine,
} from './MethodChip';
import { mgmtFeeOperatingModelHint } from '@/lib/operatingModel';
import BenchmarkComparison, { type BenchmarkBlock } from './BenchmarkComparison'; // R-067 — read-only Benchmark detail
// FON-97 — the in-table Benchmark sub-column (Base-year group), context-wired like MethodChip.
import {
  BenchmarkCell,
  BenchmarkColumnContext,
  BenchmarkHeaderCell,
  benchmarkColumnCtx,
  readStoredBenchmarkToggle,
  storeBenchmarkToggle,
} from './BenchmarkColumn';
import { useDealBenchmark } from '@/lib/hooks/useDealBenchmark';
// FON-109 — benchmark ratios are suggestions the analyst applies (as an override with a note).
import {
  ApplyBenchmarkAction,
  BenchmarkSuggestionContext,
  useBenchmarkSuggestion,
  useBenchmarkSuggestions,
  type BenchmarkSuggestionCtx,
} from './BenchmarkSuggestion';

// ────────────────────────────────────────────────────────────────────
// Worker output shapes — mirror PLTab.tsx (kept local so this file
// stays self-contained in the new pl/ subdirectory).
// ────────────────────────────────────────────────────────────────────
interface RevenueYearWorker {
  year: number;
  occupancy: number;
  adr: number;
  revpar: number;
  rooms_revenue: number;
  fb_revenue: number;
  other_revenue: number;
  total_revenue: number;
}

interface FBYearWorker {
  year: number;
  rooms_revenue: number;
  fb_revenue: number;
  resort_fees?: number;
  other_revenue: number;
  total_revenue: number;
}

interface ExpenseYearWorker {
  year: number;
  total_revenue: number;
  dept_expenses?: {
    rooms: number;
    food_beverage: number;
    other_operated: number;
    total: number;
  };
  undistributed?: {
    administrative_general: number;
    information_telecom: number;
    sales_marketing: number;
    property_operations: number;
    utilities: number;
    total: number;
  };
  mgmt_fee?: number;
  // Sam #9 — the opt-in incentive management fee (share of GOP after the base
  // fee). null / absent = no incentive fee modelled on this run.
  incentive_mgmt_fee?: number | null;
  ffe_reserve?: number;
  fixed_charges?: {
    property_taxes: number;
    insurance: number;
    rent: number;
    other_fixed: number;
    total: number;
  };
  gop?: number;
  noi?: number;
  noi_institutional?: number | null;
}

// One year of normalized projection inputs — what the table renders +
// what the xlsx export emits. Sam's P4 ask: "Expand exports beyond
// topline revenue. Ensure Other Operated Departments are reflected
// appropriately." OOD revenue used to be collapsed into the Misc
// bucket; it's now its own field so the USALI waterfall renders
// honestly.
interface ProjYear {
  /** The engine's ORDINAL (1..hold_years), not a calendar year. */
  year: number;
  /** Calendar year from ``revenue.projection_calendar_years``; absent with no
   *  acquisition close date on the deal. */
  calendarYear?: number;
  // Available Rooms = keys × days (a flat 365 — see DAYS_PER_PROJECTION_YEAR).
  days: number;
  rooms: number;
  availableRooms: number;
  // Every figure below is `undefined` when the engine did not publish it for
  // this year — rendered "—" and exported as an empty cell, never 0 (go-live
  // Wave 0, A12). Occupied Rooms is derived, so it is undefined with occupancy.
  occupiedRooms?: number;
  occupancy?: number;     // 0..1
  adr?: number;           // dollars
  revpar?: number;        // dollars
  roomsRevenue?: number;  // dollars
  fbRevenue?: number;     // dollars
  // USALI 11th: Other Operated Departments (spa, golf, parking,
  // rentals — anything ancillary that runs as its own department)
  // sits as its own revenue line. Distinct from Resort Fees, which are
  // smaller / non-departmental. (The engine carries no separate
  // Misc. Income line, so the table has none — a line the model does not
  // produce is not shown as 0.)
  otherOperatedRevenue?: number;
  resortFees?: number;
  totalRevenue?: number;  // dollars
  // Optional expense + downstream lines (worker output only;
  // demo path leaves them undefined and the export skips them).
  deptRoomsExpense?: number;
  deptFbExpense?: number;
  deptOtherExpense?: number;
  deptTotalExpense?: number;
  undistAdminGeneral?: number;
  undistInfoTelecom?: number;
  undistSalesMarketing?: number;
  undistPropertyOps?: number;
  undistUtilities?: number;
  undistTotal?: number;
  mgmtFee?: number;
  /** Sam #9 — undefined when the run modelled no incentive fee. */
  incentiveMgmtFee?: number;
  fixedPropertyTaxes?: number;
  fixedInsurance?: number;
  fixedRent?: number;
  fixedOther?: number;
  fixedTotal?: number;
  gop?: number;
  // Institutional NOI (GOP - mgmt fee - fixed charges, BEFORE FF&E reserve).
  noiInstitutional?: number;
  // False when the run only carried the legacy after-reserve `noi` — the
  // label must then say the basis is unconfirmed (lib/engines/noi.ts).
  noiBasisConfirmed: boolean;
  ffeReserve?: number;
  // Net cash flow after FF&E reserve = NOI - FF&E.
  netCashFlow?: number;
}

// The revenue engine projects on a flat 365-day year
// (``apps/worker/app/engines/revenue.py`` DAYS_PER_YEAR), so the statement
// must too: RevPAR × Available Rooms only foots to Rooms Revenue on the same
// day count. This used to be ``isLeap(r.year)`` — which asked whether the
// ORDINAL 1 was a leap year, so it was always 365 anyway. Now that the column
// knows its calendar year, a leap-year 366 here would silently stop the
// statement reconciling to the engine.
const DAYS_PER_PROJECTION_YEAR = 365;

/**
 * The header for model-year column ``i``. Index 0 IS operating Year 1 — it
 * keeps the "Base Year" name analysts read the statement by, and says which
 * model year it is (founder decision, FON-41 #2). The engine is NOT re-indexed.
 */
export function projectionColumnLabel(i: number): string {
  return i === 0 ? 'Base year (Year 1)' : `Year ${i + 1}`;
}

/**
 * The FULL column header (FON-41 E-014): the label with its calendar year
 * inline — "Year 2 · 2026", "Base year (Year 1) · 2025" — or the bare label
 * when the deal carries no acquisition close date. The calendar year is the
 * revenue engine's ``projection_calendar_years[i]``; nothing here derives one.
 */
export function projectionColumnHeading(i: number, calendarYear?: number): string {
  const label = projectionColumnLabel(i);
  return calendarYear != null && Number.isFinite(calendarYear)
    ? `${label} · ${calendarYear}`
    : label;
}

/** The Exit Year column's header — display-only, never a modelled year. */
export const EXIT_COLUMN_LABEL = 'Exit Year';

/** The Exit Year heading with its calendar year inline, when there is one. */
export function exitColumnHeading(calendarYear?: number): string {
  return calendarYear != null && Number.isFinite(calendarYear)
    ? `${EXIT_COLUMN_LABEL} · ${calendarYear}`
    : EXIT_COLUMN_LABEL;
}

/**
 * The base column's BASIS line: the primary statement's period ("T12 Mar
 * 2025", "FY2024") exactly as the Historical P&L column heads it, when the
 * Data Room has flagged a primary financial statement and its extraction
 * carries a period. A dash otherwise — never an inferred period.
 */
export function baseYearBasisLabel(basePeriodLabel?: string | null): string {
  const t = basePeriodLabel?.trim();
  return t ? t : '—';
}

/** The basis line under every projected column. */
export const PROJECTED_COLUMN_BASIS = 'projected';
/** The basis line under the Exit Year column. */
export const EXIT_COLUMN_BASIS = 'display-only';

/** The one-line note shown when the deal has no acquisition close date. */
export const NO_CLOSE_DATE_NOTE =
  'Set the acquisition date on CAPEX to map years to calendar years';
/** The help line under the controls — why the columns carry the years they do. */
export const CALENDAR_MAPPING_HELP =
  'The acquisition close date sets the year mapping: Year 1 is the calendar year of the first operating month after close, and each later year is that year plus one.';
/** What the lineage drawer says for a cell the record does not cover. */
export const NO_TRACE_MESSAGE = 'No trace available for this cell.';

/** The row the Exit Year column exists for. */
export const FORWARD_NOI_LABEL = 'Forward 12-Month Cash NOI (after FF&E reserve)';

/**
 * A column subtitle: the calendar year, or an em dash. NEVER the ordinal —
 * printing ``years[i].year`` here is what produced "Base Year 1 / Year 1 2".
 */
export function projectionColumnSubtitle(calendarYear?: number): string {
  return calendarYear != null && Number.isFinite(calendarYear)
    ? String(calendarYear)
    : '—';
}

// Engine-default assumptions (mirror apps/worker services/engine_runner.py base).
// Used as the display fallback when a key has no override and no resolved source.
const ASSUMPTION_DEFAULTS: Record<string, number> = {
  revpar_growth: 0.045,
  expense_growth: 0.035,
  other_expense_growth: 0.03,
  resort_fee_per_night: 35,
  resort_fee_capture_y1: 0.6,
  resort_fee_capture_y2: 0.8,
  resort_fee_capture_y3: 0.95,
  mgmt_fee_pct: 0.03,
  exit_cap_rate: 0.07,
};

// True when a field_overrides entry is a non-empty JSON list — raw (legacy
// shape) or wrapped ``{value: [...], note}`` (the structured shape). The
// worker unwraps both (``_normalize_override_shape``) before the list guard.
function hasListOverride(overrides: Record<string, unknown>, key: string): boolean {
  const raw = overrides[key];
  if (Array.isArray(raw)) return raw.length > 0;
  if (typeof raw === 'object' && raw !== null && 'value' in (raw as object)) {
    const v = (raw as { value?: unknown }).value;
    return Array.isArray(v) && v.length > 0;
  }
  return false;
}

// Read a numeric value out of a field_overrides entry ({value, note} or scalar).
function ovValue(overrides: Record<string, unknown>, key: string): number | null {
  const raw = overrides[key];
  if (raw == null) return null;
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null;
  if (typeof raw === 'object' && 'value' in (raw as object)) {
    const v = (raw as { value?: unknown }).value;
    const n = typeof v === 'number' ? v : Number(v);
    return Number.isFinite(n) ? n : null;
  }
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

// Read an ISO date out of a field_overrides entry ({value, note} or scalar).
// Returns null for anything that is not a YYYY-MM-DD — a half-typed date is
// not a date, and this row must never render a guess.
function overrideDate(overrides: Record<string, unknown>, key: string): string | null {
  const raw = overrides[key];
  const v = raw != null && typeof raw === 'object' && 'value' in (raw as object)
    ? (raw as { value?: unknown }).value
    : raw;
  if (typeof v !== 'string') return null;
  return /^\d{4}-\d{2}-\d{2}/.test(v.trim()) ? v.trim().slice(0, 10) : null;
}

/** ISO ``YYYY-MM-DD`` → ``M/D/YYYY``; anything else → an em dash. */
function fmtIsoDate(iso: string | null | undefined): string {
  const m = iso ? /^(\d{4})-(\d{2})-(\d{2})/.exec(iso) : null;
  return m ? `${Number(m[2])}/${Number(m[3])}/${m[1]}` : '—';
}

export default function ProjectionsSection({
  dealId,
  basePeriodLabel = null,
}: {
  dealId: string;
  /** FON-41 E-014 — the primary financial statement's period ("T12 Mar 2025"),
   *  resolved by the host tab from the Data Room's primary-source flag + that
   *  document's extraction. Null when the worker exposes no primary statement
   *  or its period is not stated; the base column then shows a dash. */
  basePeriodLabel?: string | null;
}) {
  const { toast } = useToast();
  const { outputs } = useEngineOutputs(dealId);
  const { deal, refresh: refreshDeal } = useDeal(dealId);

  // FON-27: inline override of a driver assumption. Writes the deal's
  // field_overrides (the engine reads starting_occupancy / starting_adr from
  // there — analyst intent wins over every data source) then re-runs the
  // model. useEngineOutputs auto-refreshes when the run completes.
  const { run, status: runStatus } = useEngineRun(dealId, 'returns', { runMode: 'all' });
  // FON-41 E-011 — per-cell comment threads keyed proj:<engine>.years[i].<path>.
  const comments = useCellCommentsState(dealId, 'proj');
  const overrides = useMemo(
    () => (deal?.field_overrides ?? {}) as Record<string, unknown>,
    [deal],
  );
  // Both resolve to whether the write landed. Editors keep their panel (and
  // the analyst's typed note) open on `false` instead of closing over a toast
  // — a failed PATCH used to discard an edited justification silently.
  const applyOverride = useCallback(
    async (key: string, value: number, note: string): Promise<boolean> => {
      try {
        await api.deals.update(dealId, {
          field_overrides: { ...overrides, [key]: overrideEnvelope(key, value, note) },
        });
        refreshDeal();
        await run();
        toast('Override applied — re-modeled', { type: 'success' });
        return true;
      } catch {
        toast('Could not apply override', { type: 'error' });
        return false;
      }
    },
    [overrides, dealId, refreshDeal, run, toast],
  );
  const resetOverride = useCallback(
    async (key: string): Promise<boolean> => {
      const { [key]: _drop, ...rest } = overrides;
      try {
        await api.deals.update(dealId, { field_overrides: rest });
        refreshDeal();
        await run();
        toast('Reset to source — re-modeled', { type: 'success' });
        return true;
      } catch {
        toast('Could not reset override', { type: 'error' });
        return false;
      }
    },
    [overrides, dealId, refreshDeal, run, toast],
  );
  // E-016 — per-line projection method (MethodChip). Method + value land in
  // field_overrides as `projection_methods.<line>.method|value` with the
  // analyst's note on both, then the model re-runs — the same path as every
  // other override here. A null value drops the value key (= the method's
  // default).
  const lineMethods =
    getEngineField<Record<string, LineMethodInfo>>(outputs, 'expense', 'line_methods') ?? null;
  const saveMethod = useCallback<MethodChipCtx['save']>(
    async (line, method, value, note) => {
      const k = methodOverrideKeys(line);
      try {
        const next = applyOverridePatch(overrides, { [k.method]: method, [k.value]: value }, note);
        await api.deals.update(dealId, { field_overrides: next });
        refreshDeal();
        await run();
        toast('Projection method applied — re-modeled', { type: 'success' });
      } catch {
        toast('Could not apply the projection method', { type: 'error' });
      }
    },
    [overrides, dealId, refreshDeal, run, toast],
  );
  const resetMethod = useCallback<MethodChipCtx['reset']>(
    async (line) => {
      const k = methodOverrideKeys(line);
      const { [k.method]: _m, [k.value]: _v, ...rest } = overrides;
      try {
        await api.deals.update(dealId, { field_overrides: rest });
        refreshDeal();
        await run();
        toast('Projection method reset — re-modeled', { type: 'success' });
      } catch {
        toast('Could not reset the projection method', { type: 'error' });
      }
    },
    [overrides, dealId, refreshDeal, run, toast],
  );
  const methodCtx = useMemo<MethodChipCtx>(
    () => ({
      lineMethods,
      overrides,
      running: runStatus === 'running' || runStatus === 'queued',
      save: saveMethod,
      reset: resetMethod,
    }),
    [lineMethods, overrides, runStatus, saveMethod, resetMethod],
  );
  const overrideCtx = useMemo<OverrideCtx>(
    () => ({
      overrides,
      apply: applyOverride,
      reset: resetOverride,
      running: runStatus === 'running' || runStatus === 'queued',
    }),
    [overrides, applyOverride, resetOverride, runStatus],
  );

  // FON-61 (D4) — the Year-1 basis is read from the worker's source tags,
  // never inferred from the flag alone. An STR basis tag on starting_occupancy
  // / starting_adr (subject TTM, comp-set rates, or forward forecast — see
  // ``isStrBasisSource``) means the Market / STR rates ARE the active basis;
  // ``str_forecast_unavailable`` (the worker tags the flag key when the seed
  // was requested but could not populate) means the model stayed on T-12.
  const occSrc = useSource('starting_occupancy');
  const adrSrc = useSource('starting_adr');
  const strSeedSrc = useSource('revenue_seed_from_str_forecast');
  // FON-89 — the worker's Year-1 displacement verdict, one line under the
  // basis chips, printed verbatim (null until the provenance payload lands).
  const displacementLine = displacementStatusLine(useDisplacementStatus());
  // FON-61 (61.2) — ONE helper, not a hand-rolled equality: the STR seed's
  // source id was split three ways (forward forecast / subject TTM actual /
  // comp-set rates) and every one of them is an STR basis.
  const strBasisActive = isStrBasisSource(occSrc?.source) || isStrBasisSource(adrSrc?.source);
  const strBasisUnavailable =
    !strBasisActive &&
    [strSeedSrc, occSrc, adrSrc].some((s) => s?.source === 'str_forecast_unavailable');
  // Revert = the Market tab's revert: drop the flag + both STR-noted keys
  // (an analyst's later explicit override on either key survives), re-run.
  const revertStrBasis = useCallback(async () => {
    const next = { ...overrides };
    delete next['revenue_seed_from_str_forecast'];
    for (const key of ['starting_occupancy', 'starting_adr'] as const) {
      if (isStrMarketOverride(next[key])) delete next[key];
    }
    try {
      await api.deals.update(dealId, { field_overrides: next });
      refreshDeal();
      await run();
      toast('Reverted Year-1 to the T-12 actuals — re-modeled', { type: 'success' });
    } catch {
      toast('Could not revert the STR basis', { type: 'error' });
    }
  }, [overrides, dealId, refreshDeal, run, toast]);

  // FON-67 — the NOI reconciliation pin. While ``noi_override_by_year`` (a
  // per-year NOI list) is set, the worker pins the operating NOI path in the
  // debt + returns engines to that schedule (engine_runner reads
  // ``base['noi_override_by_year']``), so RevPAR-growth / expense edits made
  // here do NOT move NOI. ``terminal_noi_override`` likewise pins the exit-year
  // NOI for the reversion. Say so, and offer a one-click clear through the same
  // field_overrides PATCH path every other override in this section uses.
  // Phase 4.4 — the worker's own refusal code is the first authority: when it
  // tags either pin key ``pin_active`` we take its word for it. The local
  // field_overrides inspection stays as the fallback, so on a worker that does
  // not emit the code yet (all of them, today) this resolves EXACTLY as it did
  // before — same notice, same copy, same Clear-pin button.
  const noiPinReason = useRefusal('noi_override_by_year');
  const terminalNoiPinReason = useRefusal('terminal_noi_override');
  const noiPinned =
    noiPinReason === 'pin_active' || hasListOverride(overrides, 'noi_override_by_year');
  const terminalNoiPinned =
    terminalNoiPinReason === 'pin_active' || ovValue(overrides, 'terminal_noi_override') != null;
  const clearNoiPin = useCallback(async () => {
    const what = terminalNoiPinned
      ? 'the NOI schedule pin and the terminal NOI pin'
      : 'the NOI schedule pin';
    if (
      typeof window !== 'undefined' &&
      !window.confirm(`Clear ${what}? NOI will follow the operating assumptions again once the model re-runs.`)
    ) {
      return;
    }
    const {
      noi_override_by_year: _noiPin,
      terminal_noi_override: _terminalPin,
      ...rest
    } = overrides;
    try {
      await api.deals.update(dealId, { field_overrides: rest });
      refreshDeal();
      await run();
      toast('NOI pin cleared — re-modeled', { type: 'success' });
    } catch {
      toast('Could not clear the NOI pin', { type: 'error' });
    }
  }, [overrides, terminalNoiPinned, dealId, refreshDeal, run, toast]);

  // Exit cap rate drives the Implied Exit Value line at the bottom of the
  // forward statement. It's owned by the Investment tab, so we resolve it the
  // same way the Assumptions panel does: live provenance source → override →
  // engine default. Capitalising each year's EBITDA at this rate mirrors the
  // canonical Projections statement's "Implied Exit Value" line.
  const exitCapSrc = useSource('exit_cap_rate');
  const exitCapRate = useMemo(() => {
    if (typeof exitCapSrc?.value === 'number' && Number.isFinite(exitCapSrc.value)) {
      return exitCapSrc.value;
    }
    return ovValue(overrides, 'exit_cap_rate') ?? ASSUMPTION_DEFAULTS.exit_cap_rate;
  }, [exitCapSrc, overrides]);

  // Resolve key count: real deal.keys; default 0 until known.
  const keys = deal?.keys && deal.keys > 0 ? deal.keys : 0;

  // Pull engine years.
  const revenueYears = getEngineField<RevenueYearWorker[]>(outputs, 'revenue', 'years');
  const fbYears = getEngineField<FBYearWorker[]>(outputs, 'fb', 'years');
  const expenseYears = getEngineField<ExpenseYearWorker[]>(outputs, 'expense', 'years');
  // FON-41 #2 — the projection calendar, anchored on the acquisition close
  // date by the revenue engine. Empty when the deal has no close date; the
  // statement then shows column labels with no year underneath them.
  const calendarYears = getEngineField<number[]>(outputs, 'revenue', 'projection_calendar_years');
  // The exit is valued on the FORWARD 12-month Cash NOI — year hold+1, which
  // is never run through the expense waterfall (returns.py extrapolates the
  // last hold year at the RevPAR growth rate). Display-only here.
  const terminalNoi =
    getEngineField<number>(outputs, 'returns', 'terminal_noi_usd') ??
    getEngineField<number>(outputs, 'returns', 'terminal_noi');
  const revparGrowthAssumption =
    getEngineField<number>(outputs, 'returns', 'revpar_growth') ??
    ovValue(overrides, 'revpar_growth') ??
    ASSUMPTION_DEFAULTS.revpar_growth;
  // FON-41 — the deal's hold, which IS the projection period. Override first
  // (analyst intent shows the moment it is saved, before the re-run lands),
  // then the returns engine's published value. `years.length` is the same
  // number from the revenue engine's own horizon and is the last resort.
  const holdYearsAssumption =
    ovValue(overrides, HOLD_YEARS_KEY) ??
    getEngineField<number>(outputs, 'returns', 'hold_years') ??
    null;
  const hasWorker =
    Array.isArray(revenueYears) && revenueYears.length > 0 &&
    Array.isArray(expenseYears) && expenseYears.length > 0;

  const years = useMemo<ProjYear[] | null>(() => {
    if (hasWorker && keys > 0) {
      return buildFromWorker(revenueYears!, fbYears ?? null, expenseYears!, keys, calendarYears);
    }
    return null;
  }, [hasWorker, revenueYears, fbYears, expenseYears, keys, calendarYears]);

  // FON-41 / FON-59 #3 — the published stabilized year. The STABILIZED badge
  // sits on that column; the editable Stabilization Year lives in the
  // Assumptions panel and writes ``field_overrides.stabilization_year``.
  const stabilization = stabilizedYearBlock(getEngineField<unknown>(outputs, 'expense'));

  // CRITICAL: every hook below MUST be declared BEFORE the early-return
  // empty-state guard. React's Rules of Hooks require the same hook count on
  // every render; placing hooks after the guard caused React error #310 when
  // `years` flipped from null → populated between renders (2026-05-12 prod
  // crash on the P&L tab).
  //
  // FON-41b (Sam, 2026-09-09) — the "AI NOI Summary" button + modal that lived
  // here were removed: the button dead-ended in a 404/error page. The worker's
  // grounded Q&A endpoint (`/deals/{id}/ask`) is untouched; only this entry
  // point (button, handler, modal, prompt builder) is gone.

  // Canonical Projections view controls. This is the ONLY view state left on
  // the bar: how many columns render, capped at the engine-provided years.
  // FON-41 (2026-09-14) — Base year is derived and now says so, the projection
  // period is a real `hold_years` edit, and the dead Annual/Monthly `projView`
  // state is gone (nothing ever read it). See ``ProjectionsControls``.
  const [projYearsSel, setProjYearsSel] = useState<number | null>(null);
  // R-068 — the "% Rev" sub-column (share of Total Revenue; departmental
  // expenses as a share of their own department's revenue). View-only, on by
  // default; every ratio is computed from the same engine year values the
  // Amount cells print.
  const [showPct, setShowPct] = useState(true);
  // FON-97 — the deal's P&L benchmark (one fetch, shared with the detail
  // panel) and the "Benchmark" header switch. Default ON when a benchmark
  // exists; the choice is remembered per device (localStorage, try/catch —
  // storage that is blocked just means the switch resets next visit).
  const { block: benchmarkBlock } = useDealBenchmark(dealId);
  // FON-109 — the benchmark's per-assumption suggestions, re-read whenever the
  // deal's overrides change so an applied one reads as applied.
  const { byKey: suggestionsByKey } = useBenchmarkSuggestions(dealId, overrides);
  const suggestionCtx = useMemo<BenchmarkSuggestionCtx>(
    () => ({
      byKey: suggestionsByKey,
      apply: applyOverride,
      running: runStatus === 'running' || runStatus === 'queued',
    }),
    [suggestionsByKey, applyOverride, runStatus],
  );
  const [showBenchmark, setShowBenchmark] = useState<boolean>(() => readStoredBenchmarkToggle() ?? true);
  const toggleBenchmark = useCallback(() => {
    const next = !showBenchmark;
    storeBenchmarkToggle(next);
    setShowBenchmark(next);
  }, [showBenchmark]);

  // FON-41 #2 — the acquisition close date. This is the deal assumption the
  // TIMELINE engine is built on (``engine_runner`` reads
  // ``base['acquisition_close_date']``), so the statement's Hotel Delivery row
  // and the timeline rail cannot disagree. The old code synthesised
  // ``'9/30/' + baseYear`` off the ORDINAL and rendered a literal "9/30/1" —
  // a fabricated date. With no close date the row is a dash.
  const closeDateIso = overrideDate(overrides, 'acquisition_close_date');
  // The Exit Year is the year after the last modelled year — calendar only,
  // and only when the projection actually carries a calendar.
  const lastCalendarYear =
    Array.isArray(calendarYears) && calendarYears.length > 0
      ? calendarYears[calendarYears.length - 1]
      : undefined;
  const exitCalendarYear =
    typeof lastCalendarYear === 'number' ? lastCalendarYear + 1 : undefined;

  if (!years || years.length === 0) {
    return (
      <Card className="p-12 text-center">
        <Sparkles size={22} className="mx-auto text-brand-500 mb-3" />
        <div className="text-[14px] font-semibold text-ink-900">
          No projections yet
        </div>
        <p className="text-[12.5px] text-ink-500 mt-1.5 max-w-md mx-auto leading-relaxed">
          Upload a T-12 or P&amp;L to populate projections — engines run automatically once extraction completes.
        </p>
      </Card>
    );
  }



  // The "Show" control trims the columns RENDERED (base year + N forecast
  // years), capped at the engine-provided years. It is a view filter, not an
  // assumption — the modelled horizon is `hold_years` (the Projection period).
  const forecastCount = Math.max(1, years.length - 1);
  const shownForecast = projYearsSel == null ? forecastCount : Math.max(1, Math.min(projYearsSel, forecastCount));
  const visibleYears = years.slice(0, shownForecast + 1);

  return (
    <CellCommentsProvider state={comments}>
    <Card className="p-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border bg-bg/40">
        <div>
          <div className="text-[10.5px] uppercase tracking-[0.12em] text-ink-500 font-semibold">
            Preliminary Hotel Underwriting
          </div>
          <h3 className="text-[15px] font-semibold text-ink-900 mt-0.5">
            {PL_SUB_LABEL.projections}
          </h3>
        </div>
        <div className="flex items-center gap-2">
          {/* FON-61 (D4) — honest Year-1 basis. Active only when the worker
              tagged the rates with an STR basis; "unavailable" when the seed was
              requested but could not populate; nothing for analyst / seed. */}
          {strBasisActive && (
            <span
              data-testid="str-basis-chip"
              className="inline-flex items-center gap-1.5 rounded-md border border-success-500/30 bg-success-50 px-2 py-1 text-[11px] font-medium text-success-700"
            >
              <span className="w-1.5 h-1.5 rounded-full bg-success-500" aria-hidden="true" />
              Active basis: Market / STR
              <span className="text-success-700/50" aria-hidden="true">·</span>
              <button
                type="button"
                onClick={revertStrBasis}
                disabled={overrideCtx.running}
                className="font-semibold underline decoration-dotted underline-offset-2 hover:opacity-80 disabled:opacity-50"
              >
                Revert
              </button>
            </span>
          )}
          {strBasisUnavailable && (
            <span
              data-testid="str-basis-unavailable"
              className="inline-flex items-center gap-1.5 rounded-md border border-warn-500/30 bg-warn-50 px-2 py-1 text-[11px] font-medium text-warn-700"
              title="STR rates were requested but could not populate (no STR Trend extraction or coverage too low). The model is on the T-12 base."
            >
              <span className="w-1.5 h-1.5 rounded-full bg-warn-500" aria-hidden="true" />
              STR rates unavailable — using T-12 base
            </span>
          )}
          {/* FON-67 — NOI pin notice. Shown ONLY when the deal carries the
              per-year NOI override; explains why operating edits don't move
              NOI and offers the one-click clear. */}
          {noiPinned && (
            <span
              data-testid="noi-pin-notice"
              className="inline-flex flex-wrap items-center gap-1.5 rounded-md border border-warn-500/30 bg-warn-50 px-2 py-1 text-[11px] font-medium text-warn-700 max-w-[560px]"
              title="An analyst-entered NOI schedule is pinned on this deal, so Debt and Returns read it instead of the operating model. Clear the pin to let NOI follow the operating assumptions again."
            >
              <span className="w-1.5 h-1.5 rounded-full bg-warn-500" aria-hidden="true" />
              NOI pinned to an analyst schedule — operating assumption edits won&apos;t move NOI until the pin is cleared
              {terminalNoiPinned ? '. Terminal NOI is also pinned' : ''}
              <span className="text-warn-700/50" aria-hidden="true">·</span>
              <button
                type="button"
                onClick={clearNoiPin}
                disabled={overrideCtx.running}
                className="font-semibold underline decoration-dotted underline-offset-2 hover:opacity-80 disabled:opacity-50"
              >
                Clear pin
              </button>
            </span>
          )}
          <CommentedCellsToggle />
          {/* FON-41 E-017 — editable workbook (projection + Assumptions sheet
              with stable override keys); Import previews, Apply saves the
              assumptions through the field_overrides PATCH, then re-models.
              Wave 3 (C11) — this is the Future P&L's ONE export: the worker's
              workbook. The separate "Export" button downloaded the very same
              file, so it is gone rather than presented as a second format. */}
          <PlRoundTripControls
            dealId={dealId}
            kind="projections"
            onApplied={async (res) => {
              if (!res.applied.length) return;
              refreshDeal();
              await run();
            }}
          />
        </div>
      </div>

      {/* FON-89 — one line under the Year-1 basis: the worker's displacement
          verdict, verbatim. Sam (2026-10-15): no renovation budget — analyst,
          OM broker or PIP document — displaces Year 1 by itself; only the
          explicit Y1 displacement assumptions (owned by the CAPEX tab, linked
          here), a structured PIP closure schedule, or the explicit renovation
          displacement SCHEDULE do. When the schedule is active the worker's
          sentence names its periods and per-year factors (state
          `on_schedule`) — still this one line, nothing composed here. */}
      {displacementLine && (
        <p
          data-testid="y1-displacement-status"
          data-displacement-state={displacementLine.state}
          data-displacement-tone={displacementLine.tone}
          className={cn(
            'text-[11px] leading-snug -mt-2 mb-3',
            displacementLine.tone === 'on' ? 'text-ink-600' : 'text-ink-500',
          )}
        >
          <span className="font-semibold">Year-1 basis · renovation impact</span>
          {' — '}{displacementLine.text}{' '}
          <Link
            href={`/projects/${dealId}?tab=investment`}
            data-testid="y1-displacement-owner"
            title="The Year-1 displacement assumptions are owned by the CAPEX tab's Renovation section"
            className="underline decoration-dotted"
          >
            {TAB_LABEL.investment} inputs →
          </Link>
        </p>
      )}

      <ProjectionsControls
        dealId={dealId}
        years={years}
        closeDateIso={closeDateIso}
        // The projection period the editor compares Save against. Falls back to
        // the rendered horizon, which IS hold_years (the revenue engine emits
        // one row per modelled year) — never a guess.
        holdYears={holdYearsAssumption ?? years.length}
        onSaveHoldYears={(v, note) => applyOverride(HOLD_YEARS_KEY, v, note).then(() => undefined)}
        running={overrideCtx.running}
        shownForecast={shownForecast}
        forecastCount={forecastCount}
        onDec={() => setProjYearsSel(Math.max(1, shownForecast - 1))}
        onInc={() => setProjYearsSel(Math.min(forecastCount, shownForecast + 1))}
        showPct={showPct}
        onTogglePct={() => setShowPct((v) => !v)}
        benchmarkAvailable={benchmarkBlock != null}
        showBenchmark={showBenchmark}
        onToggleBenchmark={toggleBenchmark}
      />

      <BenchmarkSuggestionContext.Provider value={suggestionCtx}>
      <AssumptionsPanel
        dealId={dealId}
        overrides={overrides}
        onApply={applyOverride}
        onReset={resetOverride}
        running={overrideCtx.running}
        stabilization={stabilization}
        modelYears={years.length}
        calendarYears={calendarYears ?? null}
        operatingModel={deal?.operating_model ?? null}
      />

      <AssumptionOverrideContext.Provider value={overrideCtx}>
        <DealIdContext.Provider value={dealId}>
        <MethodChipContext.Provider value={methodCtx}>
        <ProjectionsTable
          years={visibleYears}
          exitCapRate={exitCapRate}
          closeDateIso={closeDateIso}
          showPct={showPct}
          benchmark={showBenchmark ? benchmarkBlock : null}
          basePeriodLabel={basePeriodLabel}
          exitColumn={
            // The Exit Year belongs to the FULL horizon. When the "Show"
            // control trims the view to a sub-window, hide it rather than let
            // an exit-year figure sit next to "Year 3" — the exit does not
            // move because fewer operating columns are on screen.
            shownForecast === forecastCount
              ? {
                  calendarYear: exitCalendarYear,
                  terminalNoi,
                  // The formula the Exit Year column shows for its one figure.
                  formula: `Forward 12-Month Cash NOI = Year ${years.length} Cash NOI × (1 + RevPAR growth ${(revparGrowthAssumption * 100).toFixed(1)}%)`,
                }
              : null
          }
          stabilizedYearIndex={stabilization?.stabilized_year_index}
        />
        </MethodChipContext.Provider>
        </DealIdContext.Provider>
      </AssumptionOverrideContext.Provider>
      </BenchmarkSuggestionContext.Provider>
      <BenchmarkComparison block={benchmarkBlock} modelYear={years[0] ?? null} />
    </Card>
    </CellCommentsProvider>
  );
}

// ────────────────────────────────────────────────────────────────────
// Builders
// ────────────────────────────────────────────────────────────────────

/** The Resort Fees line is shown only when the run carries a non-zero fee. */
const hasResortFees = (y: ProjYear): boolean => y.resortFees != null && y.resortFees > 0;

function buildFromWorker(
  revenueYears: RevenueYearWorker[],
  fbYears: FBYearWorker[] | null,
  expenseYears: ExpenseYearWorker[],
  keys: number,
  calendarYears?: number[] | null,
): ProjYear[] {
  // FON-41 #2 — every modelled year, not a hard-coded 6. The horizon is the
  // deal's hold period; the Exit Year column is appended by the table on top
  // of these (hold_years + 1 columns in total).
  const span = revenueYears.length;
  const out: ProjYear[] = [];
  for (let i = 0; i < span; i++) {
    const r = revenueYears[i];
    const f = fbYears?.[i];
    const e = expenseYears[i];
    const days = DAYS_PER_PROJECTION_YEAR;
    const availableRooms = keys * days;
    // Derived from occupancy — undefined (never 0) when the engine did not
    // publish an occupancy for this year.
    const occupiedRooms = r.occupancy == null ? undefined : Math.round(availableRooms * r.occupancy);
    const totalRevenue = e?.total_revenue ?? r.total_revenue ?? undefined;
    // OOD revenue is its own USALI line — split it out from the
    // Misc bucket (Sam P4 ask). Resort fees are separate again. Neither is
    // coalesced to 0: a line the run does not carry renders "—".
    const otherOperatedRevenue = f?.other_revenue ?? r.other_revenue ?? undefined;
    const resortFees = f?.resort_fees ?? undefined;
    // Pull the expense waterfall when the worker engine has emitted
    // it. Older engine_outputs rows may not carry every field — fall
    // back to undefined so the xlsx export simply skips those rows.
    const noiInst = e?.noi_institutional ?? e?.noi;
    const noiBasisConfirmed = typeof e?.noi_institutional === 'number';
    const ffe = e?.ffe_reserve;
    const netCashFlow =
      noiInst != null && ffe != null ? noiInst - ffe : undefined;
    out.push({
      year: r.year,
      calendarYear:
        Array.isArray(calendarYears) && typeof calendarYears[i] === 'number'
          ? calendarYears[i]
          : undefined,
      days,
      rooms: keys,
      availableRooms,
      occupiedRooms,
      occupancy: r.occupancy ?? undefined,
      adr: r.adr ?? undefined,
      revpar: r.revpar ?? undefined,
      roomsRevenue: f?.rooms_revenue ?? r.rooms_revenue ?? undefined,
      fbRevenue: f?.fb_revenue ?? r.fb_revenue ?? undefined,
      otherOperatedRevenue,
      resortFees,
      totalRevenue,
      deptRoomsExpense: e?.dept_expenses?.rooms,
      deptFbExpense: e?.dept_expenses?.food_beverage,
      deptOtherExpense: e?.dept_expenses?.other_operated,
      deptTotalExpense: e?.dept_expenses?.total,
      undistAdminGeneral: e?.undistributed?.administrative_general,
      undistInfoTelecom: e?.undistributed?.information_telecom,
      undistSalesMarketing: e?.undistributed?.sales_marketing,
      undistPropertyOps: e?.undistributed?.property_operations,
      undistUtilities: e?.undistributed?.utilities,
      undistTotal: e?.undistributed?.total,
      mgmtFee: e?.mgmt_fee,
      incentiveMgmtFee: e?.incentive_mgmt_fee ?? undefined,
      fixedPropertyTaxes: e?.fixed_charges?.property_taxes,
      fixedInsurance: e?.fixed_charges?.insurance,
      fixedRent: e?.fixed_charges?.rent,
      fixedOther: e?.fixed_charges?.other_fixed,
      fixedTotal: e?.fixed_charges?.total,
      gop: e?.gop,
      noiInstitutional: noiInst,
      noiBasisConfirmed,
      ffeReserve: ffe,
      netCashFlow,
    });
  }
  return out;
}

// ────────────────────────────────────────────────────────────────────
// Table
// ────────────────────────────────────────────────────────────────────

interface ExitColumnSpec {
  calendarYear?: number;
  /** ``returns.terminal_noi`` — the forward 12-month Cash NOI. */
  terminalNoi?: number;
  formula: string;
}

/** Present-ness of the Exit Year column and how many sub-columns one year
 *  group spans (4 with "% Rev" shown, 3 with it hidden), so every row can
 *  size its cells without prop-drilling through six row components. */
interface ColumnLayout {
  hasExit: boolean;
  subCols: number;
  showPct: boolean;
  /** FON-97 — the Base-year group carries one extra "Benchmark" sub-column. */
  benchmarkCol: boolean;
  /** Sub-columns in year group ``i`` (the Base year is wider with a benchmark). */
  spanFor: (i: number) => number;
}
const ColumnLayoutContext = createContext<ColumnLayout>({
  hasExit: true,
  subCols: 4,
  showPct: true,
  benchmarkCol: false,
  spanFor: () => 4,
});

/** The deal under the table — what a cell's trace click needs to open the
 *  lineage drawer for the right deal. Empty on a mock / unmounted host, in
 *  which case the cells render plain. */
const DealIdContext = createContext<string>('');

/** The Exit Year cell for an ordinary row: a dash. Year hold+1 is not modelled
 *  through the expense waterfall, so there is nothing honest to print. */
function ExitCells({ children }: { children?: ReactNode }) {
  const { hasExit, subCols } = useContext(ColumnLayoutContext);
  if (!hasExit) return null;
  return (
    <td
      colSpan={subCols}
      className="px-2 py-2 text-center text-[11px] text-ink-400 tabular-nums border-l-2 border-ink-300/70"
    >
      {children ?? '—'}
    </td>
  );
}

/**
 * FON-41 E-018 — year-group separation. A 2px rule opens every year group and
 * odd groups carry a faint tint, so five projected years read as five columns
 * rather than one twenty-cell smear. The tint is deliberately light so the
 * side-by-side comparison across years is undisturbed.
 */
export function yearGroupClass(i: number, groupStart: boolean): string {
  return cn(
    groupStart ? 'border-l-2 border-ink-300/70' : 'border-l border-border',
    i % 2 === 1 && 'bg-ink-100/45',
  );
}

/**
 * FON-41 E-015 — the lineage request behind one projected cell. The engine
 * field key is the root (``engine:revenue.years[1].adr``), offered as a
 * ``kpi:`` node too in case the graph published it as a headline figure. The
 * drawer walks whatever the worker recorded — the starting value, growth and
 * index / penetration inputs where the engine asserted them, down to the
 * source rows. When the record carries no walk for the key the drawer says
 * "No trace available for this cell." — nothing here invents a formula.
 */
/** Lines the expense engine prices from a ratio: their lineage lives on the
 *  assumption node (`assumption:<key>`), not on a per-year engine node. */
const ASSUMPTION_TRACE_FALLBACK: Record<string, string> = {
  'expense.ffe_reserve': 'assumption:ffe_reserve_pct',
  'expense.mgmt_fee': 'assumption:mgmt_fee_pct',
  'expense.incentive_mgmt_fee': 'assumption:incentive_mgmt_fee_pct_of_gop',
};

export function lineageRequestFor(
  dealId: string,
  engine: string,
  path: string,
  label: string,
  columnLabel: string,
): LineageOpenDetail {
  // FON-109 (Sam QA 2026-10-28): the expense engine publishes year nodes for
  // NOI and GOP only; a ratio-driven line (FF&E reserve, management fee) is
  // traced through its assumption node, which the graph does carry.
  const leaf = path.replace(/^years\[\d+\]\./, '');
  const assumptionFallback = ASSUMPTION_TRACE_FALLBACK[`${engine}.${leaf}`];
  return {
    dealId,
    rootId: [
      `kpi:${engine}.${path}`,
      `engine:${engine}.${path}`,
      ...(assumptionFallback ? [assumptionFallback] : []),
    ],
    title: `${label} — ${columnLabel}`,
    subtitle: `${engine}.${path} · the inputs, growth and source rows behind this cell`,
    emptyMessage: NO_TRACE_MESSAGE,
  };
}

/** A projected value that opens the lineage drawer for its engine field. */
function LineageCell({
  engine,
  path,
  label,
  columnLabel,
  note,
  children,
}: {
  engine: string;
  path: string;
  label: string;
  columnLabel: string;
  /** A derivation hint shown on hover (RevPAR = Occupancy × ADR). */
  note?: string;
  children: ReactNode;
}) {
  const dealId = useContext(DealIdContext);
  if (!dealId) return <>{children}</>;
  return (
    <span className="inline-flex items-center gap-1">
    <button
      type="button"
      data-testid={`lineage-cell-${engine}.${path}`}
      data-lineage-root={`engine:${engine}.${path}`}
      aria-label={`Trace ${label} (${columnLabel}) to its inputs`}
      title={note ? `${note} — click to trace this cell to its inputs` : 'Click to trace this projected value to its inputs and source rows'}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        openLineage(lineageRequestFor(dealId, engine, path, label, columnLabel));
      }}
      className="cursor-pointer rounded-sm border-0 bg-transparent p-0 px-0.5 -mx-0.5 tabular-nums underline decoration-dotted decoration-2 decoration-ink-300 underline-offset-[3px] hover:bg-brand-50 hover:decoration-brand-500"
    >
      {children}
    </button>
    {/* FON-41 E-011 — this cell's comment thread (engine path + year index). */}
    <CommentMarker cellKey={projCommentKey(engine, path)} label={`${label} · ${columnLabel}`} />
    </span>
  );
}

/** A full-width section band inside the statement. */
function SectionBand({ label, span }: { label: string; span: number }) {
  if (useCommentFilter()) return null;
  return (
    <tr className="bg-brand-500/95">
      <td
        colSpan={span}
        className="px-3 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-white"
      >
        {label}
      </td>
    </tr>
  );
}

function ProjectionsTable({
  years, exitCapRate, closeDateIso, exitColumn, stabilizedYearIndex, showPct, basePeriodLabel,
  benchmark = null,
}: {
  years: ProjYear[];
  exitCapRate: number;
  closeDateIso: string | null;
  exitColumn: ExitColumnSpec | null;
  /** 0-based index of the published stabilized year — badges that column. */
  stabilizedYearIndex?: number;
  /** R-068 — render the "% Rev" sub-column. */
  showPct: boolean;
  /** E-014 — the primary statement's period for the base column's basis line. */
  basePeriodLabel: string | null;
  /** FON-97 — the deal's P&L benchmark to show as a Base-year sub-column;
   *  null (no benchmark, or the switch is off) = the column does not exist. */
  benchmark?: BenchmarkBlock | null;
}) {
  // Hotel Delivery — the deal's acquisition close date, or a dash. It used to
  // be `'9/30/' + years[0].year`, which printed "9/30/1" because `year` is an
  // ordinal: a fabricated date under the no-invented-numbers rule (FON-41 #2).
  const hotelDelivery = fmtIsoDate(closeDateIso);
  const hasExit = exitColumn != null;
  const subCols = showPct ? 4 : 3;
  const benchmarkCol = benchmark != null;
  const spanFor = useCallback(
    (i: number) => subCols + (i === 0 && benchmarkCol ? 1 : 0),
    [subCols, benchmarkCol],
  );
  const layout = useMemo<ColumnLayout>(
    () => ({ hasExit, subCols, showPct, benchmarkCol, spanFor }),
    [hasExit, subCols, showPct, benchmarkCol, spanFor],
  );
  const benchmarkCtx = useMemo(() => benchmarkColumnCtx(benchmark), [benchmark]);
  // Total year columns = every modelled year + the Exit Year (hold_years + 1).
  const columnCount = years.length + (hasExit ? 1 : 0);
  const bandSpan = 2 + columnCount * subCols + (benchmarkCol ? 1 : 0);

  // The forward statement below Total Revenue only renders when the expense
  // engine emitted its waterfall (real worker runs). Demo / revenue-only
  // output leaves these undefined, so the table stays topline-only — same
  // gate the xlsx export uses.
  const hasExpenseDetail = years.some((y) => y.deptTotalExpense != null);
  const hasFixedDetail = years.some((y) => y.fixedTotal != null);
  // Sam #9 — the incentive fee row exists only when the run modelled one.
  const hasIncentiveFee = years.some((y) => y.incentiveMgmtFee != null);
  // FON-59 #1 / FON-67 #2 — name the NOI basis honestly: a pre-upgrade run
  // with no `noi_institutional` is an after-reserve number.
  const noiLabel = noiBeforeReserveLabel(years.some((y) => y.noiBasisConfirmed));

  // Annual RevPAR growth — Y0 N/A, Y1+ vs prior.
  const revparGrowth = years.map((y, i) => {
    if (i === 0) return null;
    const prev = years[i - 1].revpar;
    return prev != null && prev > 0 && y.revpar != null ? (y.revpar - prev) / prev : null;
  });

  // Helpers for sub-columns. `toLocaleString` never emits scientific
  // notation (FON-41 E-025) — a 1.2e9 total prints as 1,200,000,000.
  const fmtAmount = (v: number, opts?: { decimals?: number; prefix?: string }) => {
    const decimals = opts?.decimals ?? 0;
    const prefix = opts?.prefix ?? '';
    return `${prefix}${v.toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })}`;
  };
  // Dollars per available / occupied room, as the legend states. No room
  // base → no figure (null renders "—"), never 0.
  const par = (v: number, available: number): number | null =>
    available > 0 ? v / available : null;
  const por = (v: number, occupied: number): number | null =>
    occupied > 0 ? v / occupied : null;
  const pctRev = (v: number, total: number): number | null =>
    total > 0 ? (v / total) * 100 : null;

  // Index column helper labels (growth-rate references shown for Year 1).
  const indexLabel = (key: string): string => {
    if (key === 'occupancy') {
      const occ = years[0]?.occupancy;
      return occ == null ? '—' : `${(occ * 100).toFixed(1)}%`;
    }
    if (key === 'adr') return 'Growth';
    if (key === 'revpar') return '';
    if (key === 'rooms') return 'Mkt';
    if (key === 'fb') return 'Mkt';
    return '';
  };

  const columnHeading = (i: number) => projectionColumnHeading(i, years[i]?.calendarYear);
  const rowShared = { fmtAmount, pctRev, par, por, columnHeading };

  return (
    <ColumnLayoutContext.Provider value={layout}>
    <BenchmarkColumnContext.Provider value={benchmarkCtx}>
    <HScroll unit="year" data-testid="projections-hscroll">
      <table className="w-full text-[11.5px] min-w-[1100px] border-collapse" data-testid="projections-table">
        <thead>
          {/* Top header row — the FULL heading: label · calendar year (E-014). */}
          <tr className="border-b border-border">
            <th
              rowSpan={3}
              className="text-left text-[10.5px] font-semibold text-ink-700 uppercase tracking-wider px-3 py-2 align-bottom border-r border-border bg-bg/40"
            >
              Index
            </th>
            <th
              rowSpan={3}
              className="text-left text-[10.5px] font-semibold text-ink-700 uppercase tracking-wider px-3 py-2 align-bottom border-r border-border bg-bg/40"
            >
              $/%
            </th>
            {years.map((y, i) => (
              <th
                key={`yh-${i}`}
                colSpan={spanFor(i)}
                data-year-col
                data-testid={`projection-col-header-${i}`}
                title={
                  i === 0
                    ? `Base year (Year 1) is the model's first operating year — revenue.years[0]. ${
                        y.calendarYear != null
                          ? `Calendar ${y.calendarYear}, from the acquisition close date.`
                          : 'No acquisition close date on this deal, so it carries no calendar year.'
                      }`
                    : y.calendarYear != null
                      ? `Operating Year ${i + 1} — calendar ${y.calendarYear}, from the acquisition close date.`
                      : `Operating Year ${i + 1}. No acquisition close date on this deal, so it carries no calendar year.`
                }
                className={cn(
                  'text-center text-[10.5px] font-semibold uppercase tracking-wider px-2 pt-2 pb-0 border-l-2 border-ink-300/70',
                  i === 0 ? 'bg-ink-300/10 text-ink-700' : 'bg-brand-50/40 text-brand-700',
                )}
              >
                {columnHeading(i)}
                {i === stabilizedYearIndex && (
                  <span
                    data-testid="stabilized-badge"
                    title="The Stabilization Year — the projection year Overview's Stabilized Occupancy / ADR / Revenue / NOI all read."
                    className="ml-1.5 inline-block align-middle rounded-sm bg-success-500/15 px-1 py-px text-[8.5px] font-bold tracking-wide text-success-700"
                  >
                    STABILIZED
                  </span>
                )}
              </th>
            ))}
            {hasExit && (
              <th
                key="yh-exit"
                colSpan={subCols}
                data-year-col
                data-testid="projection-col-header-exit"
                title="Display-only. Year hold+1 is not run through the expense waterfall — it is the forward NOI the reversion is valued on."
                className="text-center text-[10.5px] font-semibold uppercase tracking-wider px-2 pt-2 pb-0 bg-ink-300/10 text-ink-700 border-l-2 border-ink-300/70"
              >
                {exitColumnHeading(exitColumn?.calendarYear)}
              </th>
            )}
          </tr>
          {/* Basis row — what each column IS. The base column names the primary
              statement's period ("T12 Mar 2025") when the Data Room exposes
              one, else a dash; projected columns say so; the exit column says
              display-only. Never the ordinal: printing `y.year` here is what
              produced "Base Year 1 / Year 1 2". */}
          <tr className="border-b border-border">
            {years.map((y, i) => {
              const basis = i === 0 ? baseYearBasisLabel(basePeriodLabel) : PROJECTED_COLUMN_BASIS;
              return (
                <th
                  key={`ys-${i}`}
                  colSpan={spanFor(i)}
                  data-testid={`projection-col-basis-${i}`}
                  className={cn(
                    'text-center text-[10.5px] font-medium tabular-nums px-2 pb-1 border-l-2 border-ink-300/70',
                    i === 0 ? 'bg-ink-300/10' : 'bg-brand-50/40',
                    i === 0 && basis === '—' ? 'text-ink-400' : 'text-ink-600',
                    i > 0 && 'normal-case italic',
                  )}
                  title={
                    i === 0
                      ? basis === '—'
                        ? 'The period of the primary financial statement is not exposed for this run — upload or flag a primary T-12 / P&L in the Data Room.'
                        : `Year-1 actuals are anchored on the primary statement's ${basis} period — the same column heading Historical P&L uses.`
                      : 'Projected from the base year at the growth assumptions above.'
                  }
                >
                  {basis}
                </th>
              );
            })}
            {hasExit && (
              <th
                key="ys-exit"
                colSpan={subCols}
                data-testid="projection-col-basis-exit"
                className="text-center text-[10.5px] font-medium italic px-2 pb-1 bg-ink-300/10 border-l-2 border-ink-300/70 text-ink-500"
              >
                {EXIT_COLUMN_BASIS}
              </th>
            )}
          </tr>
          {/* Sub-column headers */}
          <tr className="border-b border-border text-[9.5px] uppercase tracking-wider text-ink-500">
            {years.map((_, i) => (
              <SubHeaderGroup key={`sh-${i}`} dim={i === 0} showPct={showPct} benchmark={i === 0 && benchmarkCol} />
            ))}
            {hasExit && <SubHeaderGroup key="sh-exit" dim showPct={showPct} />}
          </tr>
        </thead>
        <tbody>
          {/* Hotel Delivery */}
          <tr className="border-b border-border/60">
            <td className="px-3 py-2 text-[11px] text-ink-700 font-medium border-r border-border bg-bg/30">
              Hotel Delivery
            </td>
            <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30 tabular-nums">
              {hotelDelivery}
            </td>
            {years.map((_, i) => (
              <td
                key={`hd-${i}`}
                colSpan={spanFor(i)}
                className={cn('px-2 py-2 text-center text-[11px] text-ink-400', yearGroupClass(i, true))}
              >
                —
              </td>
            ))}
            <ExitCells />
          </tr>

          {/* Days */}
          <SimpleRow
            label="Days"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.days}
            fmt={(v) => v.toLocaleString()}
            columnHeading={columnHeading}
          />

          {/* Number of Rooms */}
          <SimpleRow
            label="Number of Rooms"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.rooms}
            fmt={(v) => v.toLocaleString()}
            columnHeading={columnHeading}
          />

          {/* Available Rooms */}
          <SimpleRow
            label="Available Rooms"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.availableRooms}
            fmt={(v) => v.toLocaleString()}
            columnHeading={columnHeading}
          />

          {/* Occupied Rooms */}
          <SimpleRow
            label="Occupied Rooms"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.occupiedRooms}
            fmt={(v) => v.toLocaleString()}
            columnHeading={columnHeading}
          />

          {/* Occupancy — base-year driver grounded in the T-12/historical
              actual (starting_occupancy); later years grow at occupancy_growth.
              Every column is a click-through to revenue.years[i].occupancy. */}
          <SimpleRow
            label="Occupancy"
            indexLabel={indexLabel('occupancy')}
            unit="%"
            years={years}
            value={(y) => (y.occupancy == null ? undefined : y.occupancy * 100)}
            fmt={(v) => `${v.toFixed(1)}%`}
            sourceKey="starting_occupancy"
            overrideKey="starting_occupancy"
            overrideUnit="pct"
            traceEngine="revenue"
            tracePath={(i) => `years[${i}].occupancy`}
            columnHeading={columnHeading}
          />

          {/* Average Rate (ADR) — base-year driver grounded in the T-12/detailed
              P&L actual (starting_adr); later years grow at adr_growth. Printed
              to the cent (E-025). */}
          <SimpleRow
            label="Average Rate"
            indexLabel={indexLabel('adr')}
            unit="$"
            years={years}
            value={(y) => y.adr}
            fmt={(v) => fmtAmount(v, { decimals: 2, prefix: '$' })}
            sourceKey="starting_adr"
            overrideKey="starting_adr"
            overrideUnit="dollar"
            traceEngine="revenue"
            tracePath={(i) => `years[${i}].adr`}
            columnHeading={columnHeading}
          />

          {/* RevPAR — derived, not sourced: Occupancy × ADR. To the cent. */}
          <SimpleRow
            label="RevPAR"
            indexLabel={indexLabel('revpar')}
            unit="$"
            years={years}
            value={(y) => y.revpar}
            fmt={(v) => fmtAmount(v, { decimals: 2, prefix: '$' })}
            computedNote="Calculated: RevPAR = Occupancy × ADR"
            traceEngine="revenue"
            tracePath={(i) => `years[${i}].revpar`}
            columnHeading={columnHeading}
          />

          {/* Annual RevPAR Growth */}
          <tr className="border-b border-border/60">
            <td className="px-3 py-2 text-[11px] text-ink-700 font-medium border-r border-border bg-bg/30">
              Annual RevPAR Growth
            </td>
            <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30">
              %
            </td>
            {years.map((_, i) => {
              const g = revparGrowth[i];
              return (
                <td
                  key={`rg-${i}`}
                  colSpan={spanFor(i)}
                  className={cn(
                    'px-2 py-2 text-center text-[11px] tabular-nums',
                    yearGroupClass(i, true),
                    g === null ? 'text-ink-400' : 'text-ink-900',
                  )}
                >
                  {g === null ? 'N/A' : `${(g * 100).toFixed(1)}%`}
                </td>
              );
            })}
            <ExitCells />
          </tr>

          {/* REVENUES section header */}
          <SectionBand label="Revenues" span={bandSpan} />

          {/* Rooms */}
          <FullRow
            label="Rooms"
            indexLabel={indexLabel('rooms')}
            unit="$"
            years={years}
            amountOf={(y) => y.roomsRevenue}
            {...rowShared}
            traceEngine="revenue"
            tracePath={(i) => `years[${i}].rooms_revenue`}
          />
          {/* Food & Beverage */}
          <FullRow
            label="Food & Beverage"
            indexLabel={indexLabel('fb')}
            unit="$"
            years={years}
            amountOf={(y) => y.fbRevenue}
            {...rowShared}
            traceEngine="revenue"
            tracePath={(i) => `years[${i}].fb_revenue`}
          />
          {/* Other Operated Departments — USALI 11th line (spa, golf,
              parking, rentals, ancillary departments that run as their
              own profit centers). Sam's P4 ask: split this out from
              the Misc bucket so it's institutionally honest. */}
          <FullRow
            label="Other Operated Departments"
            indexLabel={indexLabel('other_operated')}
            unit="$"
            years={years}
            amountOf={(y) => y.otherOperatedRevenue}
            {...rowShared}
            traceEngine="revenue"
            tracePath={(i) => `years[${i}].other_revenue`}
          />
          {/* Resort Fees — distinct from rooms and from OOD. Hidden
              when absent or zero across every year (most deals don't carry them). */}
          {years.some((y) => hasResortFees(y)) && (
            <FullRow
              label="Resort Fees"
              indexLabel={indexLabel('resort_fees')}
              unit="$"
              years={years}
              amountOf={(y) => y.resortFees}
              {...rowShared}
              traceEngine="revenue"
              tracePath={(i) => `years[${i}].resort_fees`}
            />
          )}
          {/* Total Revenue */}
          <FullRow
            label="Total Revenue"
            indexLabel=""
            unit="$"
            years={years}
            amountOf={(y) => y.totalRevenue}
            {...rowShared}
            bold
            traceEngine="revenue"
            tracePath={(i) => `years[${i}].total_revenue`}
          />

          {/* ── Forward statement below Total Revenue (canonical Projections
              statement: Financials Tab.dc.html → projDefs). Every figure comes
              from the expense engine (apps/worker/app/engines/expense.py) that
              ProjYear already carries; subtotals foot to the visible rows.
              FON-41 E-030 — the waterfall runs all the way down: GOP →
              Management Fees → EBITDA → Fixed Charges → NOI (before FF&E
              reserve) → FF&E Reserve → Cash NOI (after FF&E reserve), each
              line labelled with its basis. The engine deducts the reserve
              AFTER fixed charges, so the reserve sits between the two NOI
              lines — not above the first one. ── */}
          {hasExpenseDetail && (
            <>
              {/* DEPARTMENTAL EXPENSE — R-068: each department's expense is
                  shown as a share of ITS OWN department's revenue (the USALI
                  departmental ratio), marked "dept" in the % cell. */}
              <SectionBand label="Departmental Expense" span={bandSpan} />
              <FullRow
                label="Rooms"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.deptRoomsExpense}
                ratioOf={(y) => y.roomsRevenue}
                ratioBasis="Rooms revenue"
                {...rowShared}
                traceEngine="expense"
                tracePath={(i) => `years[${i}].dept_expenses.rooms`}
                methodLine="rooms_dept_expense"
                benchmarkKey="rooms"
              />
              <FullRow
                label="Food & Beverage"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.deptFbExpense}
                ratioOf={(y) => y.fbRevenue}
                ratioBasis="Food & Beverage revenue"
                {...rowShared}
                traceEngine="expense"
                tracePath={(i) => `years[${i}].dept_expenses.food_beverage`}
                methodLine="fb_dept_expense"
                benchmarkKey="food_beverage"
              />
              <FullRow
                label="Other Operated Departments"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.deptOtherExpense}
                ratioOf={(y) => y.otherOperatedRevenue}
                ratioBasis="Other Operated Departments revenue"
                {...rowShared}
                traceEngine="expense"
                tracePath={(i) => `years[${i}].dept_expenses.other_operated`}
                methodLine="other_dept_expense"
                benchmarkKey="other_operated"
              />
              {/* Total Departmental Expense = Σ departmental expense lines
                  (worker dept_expenses.total). */}
              <FullRow
                label="Total Departmental Expense"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.deptTotalExpense}
                {...rowShared}
                bold
              />
              {/* Total Departmental Profit = Total Revenue − Total Departmental
                  Expense. */}
              <FullRow
                label="Total Departmental Profit"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => (y.deptTotalExpense == null || y.totalRevenue == null ? undefined : y.totalRevenue - y.deptTotalExpense)}
                {...rowShared}
                bold
              />

              {/* UNDISTRIBUTED EXPENSES */}
              <SectionBand label="Undistributed Expenses" span={bandSpan} />
              <FullRow
                label="Administrative & General"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistAdminGeneral}
                {...rowShared}
                traceEngine="expense"
                tracePath={(i) => `years[${i}].undistributed.administrative_general`}
                methodLine="administrative_general"
                benchmarkKey="administrative_general"
              />
              <FullRow
                label="Information & Telecom Systems"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistInfoTelecom}
                {...rowShared}
                traceEngine="expense"
                tracePath={(i) => `years[${i}].undistributed.information_telecom`}
                methodLine="information_telecom"
                benchmarkKey="information_telecom"
              />
              <FullRow
                label="Sales & Marketing"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistSalesMarketing}
                {...rowShared}
                traceEngine="expense"
                tracePath={(i) => `years[${i}].undistributed.sales_marketing`}
                methodLine="sales_marketing"
                benchmarkKey="sales_marketing"
              />
              <FullRow
                label="Property Operation & Maintenance"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistPropertyOps}
                {...rowShared}
                traceEngine="expense"
                tracePath={(i) => `years[${i}].undistributed.property_operations`}
                methodLine="property_operations"
                benchmarkKey="property_operations"
              />
              <FullRow
                label="Utilities"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistUtilities}
                {...rowShared}
                traceEngine="expense"
                tracePath={(i) => `years[${i}].undistributed.utilities`}
                methodLine="utilities"
                benchmarkKey="utilities"
              />
              {/* Total Undistributed Expenses = Σ undistributed lines
                  (worker undistributed.total). */}
              <FullRow
                label="Total Undistributed Expenses"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistTotal}
                {...rowShared}
                bold
              />
              {/* Gross Operating Profit = Total Departmental Profit − Total
                  Undistributed Expenses (worker gop). Traced to the engine. */}
              <FullRow
                label="Gross Operating Profit"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.gop}
                {...rowShared}
                bold
                traceEngine="expense"
                tracePath={(i) => `years[${i}].gop`}
                rowTestId="row-gop"
                benchmarkKey="gop"
              />
              {/* Management Fees — % of total revenue (worker mgmt_fee). */}
              <FullRow
                label="Management Fees"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.mgmtFee}
                {...rowShared}
                traceEngine="expense"
                tracePath={(i) => `years[${i}].mgmt_fee`}
                rowTestId="row-mgmt-fee"
                benchmarkKey="mgmt_fee"
              />
              {/* Sam #9 — Incentive management fee: a share of GOP after the
                  base fee (worker incentive_mgmt_fee), deducted before NOI.
                  Shown only when the run modelled one. */}
              {hasIncentiveFee && (
                <FullRow
                  label="Incentive management fee"
                  indexLabel=""
                  unit="$"
                  years={years}
                  amountOf={(y) => y.incentiveMgmtFee}
                  {...rowShared}
                  traceEngine="expense"
                  tracePath={(i) => `years[${i}].incentive_mgmt_fee`}
                  rowTestId="row-incentive-mgmt-fee"
                />
              )}
              {/* EBITDA = Gross Operating Profit − Management Fees (base, plus
                  the incentive fee when modelled) — canonical Projections
                  definition; foots to the rows above. */}
              <FullRow
                label="EBITDA"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => (y.gop == null || y.mgmtFee == null ? undefined : y.gop - y.mgmtFee - (y.incentiveMgmtFee ?? 0))}
                {...rowShared}
                bold
                rowTestId="row-ebitda"
              />
              {/* Implied Exit Value — each year's EBITDA capitalised at the
                  exit cap rate (canonical: ExitValue = EBITDA ÷ exit_cap_rate).
                  Rendered as a single spanning stat value per year (a valuation,
                  not an operating %Rev/PAR/POR breakdown). */}
              <tr className="border-b border-border/60 bg-brand-50/30 font-semibold">
                <td className="px-3 py-2 text-[11px] text-ink-900 font-semibold border-r border-border bg-bg/30">
                  Implied Exit Value
                </td>
                <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30">
                  $
                </td>
                {years.map((y, i) => {
                  // Same definition as the EBITDA row above: undefined (→ "—")
                  // when GOP or the base fee is missing; the incentive fee is
                  // genuinely zero when the run modelled none.
                  const ebitda =
                    y.gop == null || y.mgmtFee == null ? undefined : y.gop - y.mgmtFee - (y.incentiveMgmtFee ?? 0);
                  const exitVal = ebitda != null && exitCapRate > 0 ? ebitda / exitCapRate : undefined;
                  return (
                    <td
                      key={`ev-${i}`}
                      colSpan={spanFor(i)}
                      title={`Implied Exit Value = EBITDA ÷ exit cap rate (${(exitCapRate * 100).toFixed(1)}%)${exitVal != null ? ` — ${fmtAmount(exitVal, { decimals: 2, prefix: '$' })}` : ''}`}
                      className={cn('px-2 py-2 text-center text-[11px] text-ink-900 font-semibold tabular-nums', yearGroupClass(i, true))}
                    >
                      {exitVal != null ? fmtAmount(exitVal, { prefix: '$' }) : '—'}
                    </td>
                  );
                })}
                <ExitCells />
              </tr>

              {/* FIXED CHARGES — the engine deducts these BEFORE the FF&E
                  reserve (noi_institutional = gop − mgmt_fee − fixed). */}
              {hasFixedDetail && (
                <>
                  <SectionBand label="Fixed Charges" span={bandSpan} />
                  <FullRow
                    label="Property Taxes"
                    indexLabel=""
                    unit="$"
                    years={years}
                    amountOf={(y) => y.fixedPropertyTaxes}
                    {...rowShared}
                    traceEngine="expense"
                    tracePath={(i) => `years[${i}].fixed_charges.property_taxes`}
                    benchmarkKey="property_taxes"
                  />
                  <FullRow
                    label="Insurance"
                    indexLabel=""
                    unit="$"
                    years={years}
                    amountOf={(y) => y.fixedInsurance}
                    {...rowShared}
                    traceEngine="expense"
                    tracePath={(i) => `years[${i}].fixed_charges.insurance`}
                    benchmarkKey="insurance"
                  />
                  <FullRow
                    label="Equipment Lease / Rent"
                    indexLabel=""
                    unit="$"
                    years={years}
                    amountOf={(y) => y.fixedRent}
                    {...rowShared}
                    traceEngine="expense"
                    tracePath={(i) => `years[${i}].fixed_charges.rent`}
                    benchmarkKey="rent"
                  />
                  <FullRow
                    label="Other Fixed Charges"
                    indexLabel=""
                    unit="$"
                    years={years}
                    amountOf={(y) => y.fixedOther}
                    {...rowShared}
                    traceEngine="expense"
                    tracePath={(i) => `years[${i}].fixed_charges.other_fixed`}
                  />
                  <FullRow
                    label="Total Fixed Charges"
                    indexLabel=""
                    unit="$"
                    years={years}
                    amountOf={(y) => y.fixedTotal}
                    {...rowShared}
                    bold
                    rowTestId="row-fixed-total"
                  />
                </>
              )}

              {/* NET OPERATING INCOME — both bases, named (E-030). */}
              <SectionBand label="Net Operating Income" span={bandSpan} />
              <FullRow
                label={noiLabel}
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.noiInstitutional}
                {...rowShared}
                bold
                traceEngine="expense"
                tracePath={(i) => `years[${i}].noi_institutional`}
                rowTestId="row-noi-before-reserve"
              />
              <FullRow
                label="FF&E Reserve"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.ffeReserve}
                {...rowShared}
                traceEngine="expense"
                tracePath={(i) => `years[${i}].ffe_reserve`}
                rowTestId="row-ffe-reserve"
                benchmarkKey="ffe_reserve"
              />
              <FullRow
                label={CASH_NOI_LABEL}
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.netCashFlow}
                {...rowShared}
                bold
                traceEngine="expense"
                tracePath={(i) => `years[${i}].noi`}
                rowTestId="row-cash-noi"
              />
            </>
          )}
          {/* FON-41 #2 — the exit's forward 12-month Cash NOI. It is NOT a
              modelled column: returns.py extrapolates the last hold year at
              the RevPAR growth rate. Shown here, with its formula, so the
              horizon visibly reaches the figure the reversion is valued on
              — without introducing a year hold+1 through the expense
              waterfall, which would move gross sale and every IRR. */}
          {hasExit && (
            <tr className="border-b border-border/60 bg-ink-300/[0.06] font-semibold" data-testid="forward-noi-row">
              <td
                className="px-3 py-2 text-[11px] text-ink-900 font-semibold border-r border-border bg-bg/30"
                title={exitColumn!.formula}
              >
                {FORWARD_NOI_LABEL}
              </td>
              <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30">
                $
              </td>
              {years.map((_, i) => (
                <td
                  key={`fnoi-${i}`}
                  colSpan={spanFor(i)}
                  className={cn('px-2 py-2 text-center text-[11px] text-ink-400', yearGroupClass(i, true))}
                >
                  —
                </td>
              ))}
              <ExitCells>
                <ComputedValue note={exitColumn!.formula}>
                  <span className="text-ink-900 font-semibold">
                    {exitColumn!.terminalNoi != null
                      ? fmtAmount(exitColumn!.terminalNoi, { prefix: '$' })
                      : '—'}
                  </span>
                </ComputedValue>
              </ExitCells>
            </tr>
          )}
        </tbody>
      </table>
    </HScroll>
    <div className="px-5 py-3 border-t border-border text-[11px] text-ink-500 flex items-start gap-1.5" data-testid="projections-legend">
      <FileText size={11} className="mt-0.5 shrink-0" />
      <span>
        PAR = $/available room. POR = $/occupied room. % Rev = share of Total Revenue;
        departmental expense rows (marked <span className="text-[9px] uppercase tracking-wide text-ink-400">dept</span>)
        show the USALI ratio — expense ÷ that department&apos;s own revenue.
        {benchmarkCol && (
          <>
            {' '}
            <span data-testid="projections-legend-benchmark">
              Benchmark = the CBRE / HotStats comp-set ratio for the line on the same basis as % Rev, with the
              model&apos;s Year-1 difference in points (▲ above / ▼ below the benchmark — colour marks direction
              only, not a verdict). Read-only; a dash means the report did not publish that line.
            </span>
          </>
        )}
        {' '}
        Click any projected Occupancy, ADR, RevPAR or statement amount to trace it to its inputs.
      </span>
    </div>
    </BenchmarkColumnContext.Provider>
    </ColumnLayoutContext.Provider>
  );
}

// Sub-column header group: Amount | % Rev | [Benchmark] | PAR | POR.
// FON-97 — `benchmark` is true only for the Base-year group, and the header
// cell itself renders nothing without a benchmark provider.
function SubHeaderGroup({ dim, showPct, benchmark = false }: { dim: boolean; showPct: boolean; benchmark?: boolean }) {
  const cls = cn(
    'px-2 py-1.5 text-right font-semibold border-l border-border',
    dim ? 'bg-ink-300/10' : 'bg-brand-50/40',
  );
  return (
    <>
      <th className={cn(cls, 'border-l-2 border-ink-300/70')}>Amount</th>
      {showPct && <th className={cls}>% Rev</th>}
      {benchmark && <BenchmarkHeaderCell className={cls} />}
      <th className={cls}>PAR</th>
      <th className={cls}>POR</th>
    </>
  );
}

// Simple single-cell row (Days, Rooms, Occupancy, ADR, etc.) — value
// is rendered once per year, spanning all sub-columns.
// FON-27: shared context so the deep-nested driver cells can persist an
// override + re-run without prop-drilling through ProjectionsTable/SimpleRow.
interface OverrideCtx {
  overrides: Record<string, unknown>;
  /** Resolve to whether the write landed — callers close their editor only then. */
  apply: (key: string, value: number, note: string) => Promise<boolean>;
  reset: (key: string) => Promise<boolean>;
  running: boolean;
}
const AssumptionOverrideContext = createContext<OverrideCtx | null>(null);

// FON-27: click-open provenance + override panel for a driver assumption's
// base-year value. Shows where the number came from (source label, one-line
// explanation, "view source document") AND lets the analyst override it — the
// override persists to the deal's field_overrides and re-runs the model
// (the engine reads starting_occupancy / starting_adr from field_overrides;
// analyst intent wins over every source). Renders plain when there's no
// resolvable source and no provider — safe on mock / un-run deals.
function AssumptionCell({
  sourceKey,
  overrideKey,
  label,
  display,
  editValue,
  unit,
  trace,
}: {
  sourceKey: string;
  overrideKey: string;
  label: string;
  display: ReactNode;
  editValue: number;
  unit: 'pct' | 'dollar';
  /** E-015 — the lineage walk for this base-year cell, offered inside the
   *  panel (the cell itself is already the override button). */
  trace?: LineageOpenDetail;
}) {
  const ctx = useContext(AssumptionOverrideContext);
  const resolved = useSource(sourceKey);
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);

  const overridden = !!ctx && overrideKey in ctx.overrides;
  const storedNote = ctx ? overrideNoteFor(ctx.overrides, overrideKey) : null;
  const src = overridden ? 'analyst_override' : resolved?.source;
  if (!ctx || !src) return <>{display}</>;

  const kind = sourceKind(src);
  const tone = KIND_TONE[kind];
  const decoColor =
    kind === 'grounded' ? 'decoration-success-500'
      : kind === 'override' ? 'decoration-brand-500'
        : 'decoration-warn-500';

  // Cancel — restores the pre-fill and leaves the editor. No network call.
  const cancelEdit = () => {
    setDraft(unit === 'pct' ? editValue.toFixed(1) : editValue.toFixed(2));
    setNote('');
    setEditing(false);
  };
  const openPanel = () => {
    setDraft(unit === 'pct' ? editValue.toFixed(1) : editValue.toFixed(2));
    setNote('');
    setEditing(false);
    setOpen(true);
  };
  const openDoc = () => {
    if (!resolved?.docId || typeof window === 'undefined') return;
    window.dispatchEvent(
      new CustomEvent('fondok:citation-focus', {
        detail: { documentId: resolved.docId, page: 1, field: sourceKey },
      }),
    );
  };
  const apply = async () => {
    const n = Number(draft.replace(/[$,%\s]/g, ''));
    if (!Number.isFinite(n)) return;
    const next = unit === 'pct' ? n / 100 : n;
    // FON-63 — the panel opens pre-filled with the current value, so Apply on
    // an untouched panel must write nothing (it was minting an identical
    // override and flipping the dot to "Analyst override").
    if (isNoOpEdit(next, unit === 'pct' ? editValue / 100 : editValue, unit === 'pct' ? 'pct_fraction' : 'usd')) {
      setEditing(false);
      setOpen(false);
      toast(NO_OP_EDIT_MESSAGE, { type: 'info' });
      return;
    }
    // FON-74 — the justification, asked for only once the edit is a real
    // change (the no-op guard above has already exited otherwise). No
    // software-authored fallback: a blank note, never an invented one.
    const justification = note.trim();
    if (!justification && requiresNote(overrideKey)) {
      toast(NOTE_REQUIRED_MESSAGE, { type: 'error' });
      return;
    }
    setSaving(true);
    try {
      // Stay open (draft + note intact) when the write failed; the host toasted.
      if (await ctx.apply(overrideKey, next, justification)) setOpen(false);
    } finally {
      setSaving(false);
    }
  };
  const reset = async () => {
    setSaving(true);
    try {
      if (await ctx.reset(overrideKey)) setOpen(false);
    } finally {
      setSaving(false);
    }
  };

  const btn = 'text-[11px] font-medium rounded-md px-2 py-1 transition-colors disabled:opacity-50';

  return (
    <span className="relative inline-flex items-center gap-1">
      <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', tone.dot)} aria-hidden="true" />
      <button
        type="button"
        onClick={openPanel}
        className={cn('rounded-sm px-0.5 -mx-0.5 underline decoration-dotted decoration-2 underline-offset-[3px] cursor-pointer hover:opacity-80', decoColor)}
        aria-label={`${label}: ${sourceLabel(src)} — click for source and override`}
      >
        {display}
      </button>
      {open && (
        <>
          <span className="fixed inset-0 z-40" onClick={() => { cancelEdit(); setOpen(false); }} aria-hidden="true" />
          <span
            role="dialog"
            aria-label={`${label} provenance`}
            className="absolute z-50 left-1/2 -translate-x-1/2 top-full mt-1.5 w-72 rounded-lg border border-border bg-card shadow-card-hover p-3 text-left whitespace-normal"
          >
            <span className="flex items-center gap-1.5 mb-1">
              <span className={cn('w-2 h-2 rounded-full', tone.dot)} aria-hidden="true" />
              <span className={cn('text-[11px] font-semibold', tone.text)}>{sourceLabel(src)}</span>
              <span className="ml-auto text-[10px] uppercase tracking-wide text-ink-400">{label}</span>
            </span>
            <span className="block text-[11.5px] text-ink-600 leading-snug mb-2">{sourceExplanation(src)}</span>
            {/* FON-74 — the analyst's own reason, where a reviewer reads the
                number. Absent until one is stored; never a generated string. */}
            {overridden && storedNote && (
              <span
                className="block text-[11.5px] text-ink-700 leading-snug mb-2 border-l-2 border-brand-200 pl-2"
                data-testid={`assumption-why-${overrideKey}`}
              >
                <span className="font-semibold text-ink-900">Why: </span>{storedNote}
              </span>
            )}
            {resolved?.docId && kind === 'grounded' && (
              <button type="button" onClick={openDoc} className="mb-2 inline-flex items-center gap-1 text-[11px] font-medium text-brand-700 hover:text-brand-500">
                View source document →
              </button>
            )}
            {trace && (
              <button
                type="button"
                data-testid={`lineage-cell-${trace.rootId instanceof Array ? trace.rootId[1]?.replace(/^engine:/, '') : trace.rootId}`}
                onClick={(e) => { e.preventDefault(); e.stopPropagation(); openLineage(trace); }}
                className="mb-2 ml-3 inline-flex items-center gap-1 text-[11px] font-medium text-brand-700 hover:text-brand-500"
              >
                Trace to source →
              </button>
            )}
            {!editing ? (
              <span className="flex items-center gap-2 border-t border-border pt-2">
                <button type="button" onClick={() => setEditing(true)} className={cn(btn, 'text-brand-700 bg-brand-50 hover:bg-brand-100')}>
                  {overridden ? 'Edit override' : 'Override…'}
                </button>
                {overridden && (
                  <button type="button" onClick={reset} disabled={saving} className={cn(btn, 'text-ink-600 hover:text-danger-700')}>
                    Reset to source
                  </button>
                )}
              </span>
            ) : (
              <span className="block border-t border-border pt-2 space-y-1.5">
                <span className="flex items-center gap-1.5">
                  <span className="text-[11px] text-ink-500">{unit === 'pct' ? '%' : '$'}</span>
                  <input
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') { e.preventDefault(); void apply(); }
                      if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
                    }}
                    inputMode="decimal"
                    autoFocus
                    className="w-24 rounded-md border border-border px-2 py-1 text-[12px] tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                  />
                </span>
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.preventDefault(); void apply(); }
                    if (e.key === 'Escape') { e.preventDefault(); cancelEdit(); }
                  }}
                  aria-label="Override justification"
                  data-testid={`assumption-note-${overrideKey}`}
                  placeholder={requiresNote(overrideKey) ? NOTE_PLACEHOLDER : 'Why? (note)'}
                  className="w-full rounded-md border border-border px-2 py-1 text-[11px] focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                />
                <span className="flex items-center gap-2">
                  <button type="button" onClick={apply} disabled={saving} className={cn(btn, 'text-white bg-brand-600 hover:bg-brand-700')}>
                    {saving ? 'Applying…' : 'Apply & re-model'}
                  </button>
                  <button type="button" onClick={cancelEdit} className={cn(btn, 'text-ink-500 hover:text-ink-900')}>
                    Cancel
                  </button>
                </span>
              </span>
            )}
            {ctx.running && (
              <span className="block mt-2 text-[10.5px] text-brand-700">Re-modeling…</span>
            )}
          </span>
        </>
      )}
    </span>
  );
}

// FON-27: provenance affordance for a driver assumption's base-year value.
// A dot colored by source kind (🟢 grounded · 🟡 seed/benchmark · 🟣 override)
// plus the shared <Sourced> hover (source label, one-line explanation, "view
// source document"). Renders the value untouched when the deal has no
// resolvable source for the key — safe on mock deals / missing provider.
function DriverValue({ sourceKey, children }: { sourceKey?: string; children: ReactNode }) {
  const resolved = useSource(sourceKey);
  if (!sourceKey || !resolved?.source) return <>{children}</>;
  const tone = KIND_TONE[sourceKind(resolved.source)];
  return (
    <span className="inline-flex items-center gap-1">
      <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', tone.dot)} aria-hidden="true" />
      <Sourced sourceKey={sourceKey}>{children}</Sourced>
    </span>
  );
}

// FON-27: a purely-derived assumption (RevPAR = Occupancy × ADR) has no source
// document — show a sky "calculated" dot + the derivation on hover so the
// analyst still sees WHY the number is what it is.
function ComputedValue({ note, children }: { note?: string; children: ReactNode }) {
  if (!note) return <>{children}</>;
  return (
    <span className="inline-flex items-center gap-1" title={note}>
      <span className="w-1.5 h-1.5 rounded-full shrink-0 bg-slate-400" aria-hidden="true" />
      <span className="underline decoration-dotted decoration-2 decoration-slate-400 underline-offset-[3px] cursor-help">
        {children}
      </span>
    </span>
  );
}

function SimpleRow({
  label,
  indexLabel,
  unit,
  years,
  value,
  fmt,
  sourceKey,
  overrideKey,
  overrideUnit,
  computedNote,
  traceEngine,
  tracePath,
  columnHeading,
}: {
  label: string;
  indexLabel: string;
  unit: string;
  years: ProjYear[];
  /** Undefined when the engine did not publish the figure for that year → a dash. */
  value: (y: ProjYear) => number | undefined;
  fmt: (v: number) => string;
  // FON-27 provenance (base-year anchor only): a source key + optional
  // override key (→ click-open source/override panel), OR a derived-value
  // note (→ "calculated" hint). Later years are grown off the base, so
  // attributing them to the same source would misread.
  sourceKey?: string;
  overrideKey?: string;
  overrideUnit?: 'pct' | 'dollar';
  computedNote?: string;
  // E-015 — the engine field behind column i (`years[i].adr`); every column
  // becomes a click-through to the lineage drawer for that key.
  traceEngine?: string;
  tracePath?: (i: number) => string;
  columnHeading: (i: number) => string;
}) {
  const { spanFor } = useContext(ColumnLayoutContext);
  const dealId = useContext(DealIdContext);
  const commentFilter = useCommentFilter();
  if (commentFilter && !(traceEngine && tracePath && years.some((_, i) => commentFilter.has(projCommentKey(traceEngine, tracePath(i)))))) {
    return null;
  }
  return (
    <tr className="group border-b border-border/60 hover:bg-ink-300/5">
      <td className="px-3 py-2 text-[11px] text-ink-700 font-medium border-r border-border bg-bg/30">
        {label}
      </td>
      <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30">
        {indexLabel || unit}
      </td>
      {years.map((y, i) => {
        const raw = value(y);
        const shown = raw == null ? '—' : fmt(raw);
        const traced = traceEngine && tracePath;
        const trace =
          traced && dealId
            ? lineageRequestFor(dealId, traceEngine, tracePath(i), label, columnHeading(i))
            : undefined;
        const lineageWrap = (node: ReactNode) =>
          traced && raw != null ? (
            <LineageCell
              engine={traceEngine}
              path={tracePath(i)}
              label={label}
              columnLabel={columnHeading(i)}
              note={computedNote}
            >
              {node}
            </LineageCell>
          ) : node;
        const cell =
          i === 0 && sourceKey && overrideKey && raw != null ? (
            <AssumptionCell
              sourceKey={sourceKey}
              overrideKey={overrideKey}
              unit={overrideUnit ?? 'dollar'}
              label={label}
              display={shown}
              editValue={raw}
              trace={trace}
            />
          ) : i === 0 && sourceKey ? (
            <DriverValue sourceKey={sourceKey}>{lineageWrap(shown)}</DriverValue>
          ) : i === 0 && computedNote && !traced ? (
            <ComputedValue note={computedNote}>{shown}</ComputedValue>
          ) : (
            lineageWrap(shown)
          );
        return (
          <td
            key={`sr-${i}`}
            colSpan={spanFor(i)}
            className={cn('px-2 py-2 text-center text-[11px] text-ink-900 tabular-nums', yearGroupClass(i, true))}
          >
            {cell}
          </td>
        );
      })}
      <ExitCells />
    </tr>
  );
}

// Full Amount/%Rev/PAR/POR row — used for revenue + expense lines.
function FullRow({
  label,
  indexLabel,
  unit,
  years,
  amountOf,
  ratioOf,
  ratioBasis,
  fmtAmount,
  pctRev,
  par,
  por,
  bold = false,
  traceEngine,
  tracePath,
  columnHeading,
  rowTestId,
  methodLine,
  benchmarkKey,
}: {
  label: string;
  indexLabel: string;
  unit: string;
  years: ProjYear[];
  /** E-016 — the expense line whose projection method chip sits in the label cell. */
  methodLine?: ProjectionMethodLine;
  /** FON-97 — the ``pnl_benchmark.categories[].key`` this row benchmarks
   *  (Base-year Benchmark sub-column). Absent = "—, not published". */
  benchmarkKey?: string;
  /** Undefined when the engine did not emit the line for that year → a dash. */
  amountOf: (y: ProjYear) => number | undefined;
  /** R-068 — the denominator for the % cell when it is NOT Total Revenue
   *  (departmental expense ÷ that department's own revenue, per USALI). */
  ratioOf?: (y: ProjYear) => number | undefined;
  /** What `ratioOf` is, for the % cell's tooltip ("Rooms revenue"). */
  ratioBasis?: string;
  fmtAmount: (v: number, opts?: { decimals?: number; prefix?: string }) => string;
  pctRev: (v: number, total: number) => number | null;
  par: (v: number, available: number) => number | null;
  por: (v: number, occupied: number) => number | null;
  bold?: boolean;
  // Provenance: when set, the Amount cell for column i is wrapped in
  // <Traced> so hovering shows the formula behind the computed value, and
  // (E-015) becomes a click-through to the lineage drawer for that field.
  // `tracePath(i)` maps the column index to the engine's dotted output
  // path (ProjYear[i] ↔ engine years[i], 1:1 from buildFromWorker).
  traceEngine?: string;
  tracePath?: (i: number) => string;
  columnHeading: (i: number) => string;
  rowTestId?: string;
}) {
  const commentFilter = useCommentFilter();
  if (commentFilter && !(traceEngine && tracePath && years.some((_, i) => commentFilter.has(projCommentKey(traceEngine, tracePath(i)))))) {
    return null;
  }
  return (
    <tr
      className={cn(
        'group border-b border-border/60 hover:bg-ink-300/5',
        bold && 'bg-brand-50/30 font-semibold',
      )}
      data-testid={rowTestId}
    >
      <td
        className={cn(
          'px-3 py-2 text-[11px] border-r border-border bg-bg/30',
          bold ? 'text-ink-900 font-semibold' : 'text-ink-700 font-medium',
        )}
      >
        {label}
        {methodLine && <MethodChip line={methodLine} />}
      </td>
      <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30">
        {indexLabel || unit}
      </td>
      {years.map((y, i) => {
        const amt = amountOf(y);
        const shown = amt == null ? '—' : fmtAmount(amt, { prefix: '$' });
        const lineageWrapped =
          traceEngine && tracePath && amt != null ? (
            <LineageCell engine={traceEngine} path={tracePath(i)} label={label} columnLabel={columnHeading(i)}>
              {shown}
            </LineageCell>
          ) : (
            shown
          );
        const tracedAmount =
          traceEngine && tracePath && amt != null ? (
            <Traced engine={traceEngine} path={tracePath(i)}>
              {lineageWrapped}
            </Traced>
          ) : undefined;
        const denom = ratioOf ? ratioOf(y) : y.totalRevenue;
        const pct = amt == null || denom == null || denom <= 0 ? null : pctRev(amt, denom);
        return (
          <SubCells
            key={`fr-${label}-${i}`}
            columnIndex={i}
            amount={amt}
            pct={pct}
            pctBasis={ratioOf ? ratioBasis ?? 'department revenue' : undefined}
            par={amt == null ? null : par(amt, y.availableRooms)}
            por={amt == null || y.occupiedRooms == null ? null : por(amt, y.occupiedRooms)}
            fmtAmount={fmtAmount}
            tracedAmount={tracedAmount}
            benchmarkKey={benchmarkKey}
          />
        );
      })}
      <ExitCells />
    </tr>
  );
}

function SubCells({
  columnIndex,
  amount,
  pct,
  pctBasis,
  par,
  por,
  fmtAmount,
  tracedAmount,
  benchmarkKey,
}: {
  columnIndex: number;
  amount: number | undefined;
  /** Percent (0..100) or null when the denominator is missing / zero. */
  pct: number | null;
  /** Set when the ratio is a DEPARTMENTAL ratio rather than % of Total Revenue. */
  pctBasis?: string;
  par: number | null;
  por: number | null;
  fmtAmount: (v: number, opts?: { decimals?: number; prefix?: string }) => string;
  /** Provenance-wrapped Amount cell content; falls back to plain text. */
  tracedAmount?: ReactNode;
  /** FON-97 — the row's benchmark category key (Base-year group only). */
  benchmarkKey?: string;
}) {
  const { showPct, benchmarkCol } = useContext(ColumnLayoutContext);
  const base = 'px-2 py-2 text-right text-[11px] text-ink-900 tabular-nums';
  const tint = columnIndex % 2 === 1 ? 'bg-ink-100/45' : '';
  const td = cn(base, 'border-l border-border', tint);
  // E-025 — the full value, with cents and thousands separators, on hover.
  const fullAmount = amount == null ? undefined : fmtAmount(amount, { decimals: 2, prefix: '$' });
  return (
    <>
      <td className={cn(base, yearGroupClass(columnIndex, true))} title={fullAmount}>
        {tracedAmount ?? (amount == null ? '—' : fmtAmount(amount, { prefix: '$' }))}
      </td>
      {showPct && (
        <td
          className={cn(td, 'text-ink-500')}
          data-ratio-basis={pctBasis ? 'department' : 'total_revenue'}
          title={
            pct == null
              ? undefined
              : pctBasis
                ? `${pct.toFixed(2)}% of ${pctBasis} — the USALI departmental ratio (expense ÷ that department's revenue)`
                : `${pct.toFixed(2)}% of Total Revenue`
          }
        >
          {pct == null ? '—' : (
            <>
              {`${pct.toFixed(1)}%`}
              {pctBasis && (
                <span className="ml-0.5 text-[8.5px] uppercase tracking-wide text-ink-400" aria-label={`of ${pctBasis}`}>
                  dept
                </span>
              )}
            </>
          )}
        </td>
      )}
      {/* FON-97 — the Base-year Benchmark cell, on the SAME basis as the % cell
          above (pct is already departmental for `pctBasis` rows). */}
      {columnIndex === 0 && benchmarkCol && (
        <BenchmarkCell
          benchmarkKey={benchmarkKey}
          modelPct={pct}
          rowBasis={pctBasis ? 'department_revenue' : 'total_revenue'}
          className={td}
        />
      )}
      <td className={cn(td, 'text-ink-700')} title={par != null ? fmtAmount(par, { decimals: 2, prefix: '$' }) : undefined}>
        {par != null ? fmtAmount(par, { decimals: 0, prefix: '$' }) : '—'}
      </td>
      <td className={cn(td, 'text-ink-700')} title={por != null ? fmtAmount(por, { decimals: 2, prefix: '$' }) : undefined}>
        {por != null ? fmtAmount(por, { decimals: 0, prefix: '$' }) : '—'}
      </td>
    </>
  );
}

// ────────────────────────────────────────────────────────────────────
// Canonical Projections chrome — view controls + Assumptions panel
// (design/canonical/Financials Tab.dc.html). Built with the design's exact
// tokens (inline styles + oklch/hex from the source).
// ────────────────────────────────────────────────────────────────────

/**
 * The Projections control bar — FON-41 (Sam's MVP QA, 2026-09-14).
 *
 * Sam: *"Base Year / Period still appear non-editable. We should confirm
 * whether this is intentional for MVP. If analysts are expected to control the
 * projection period/base year, this needs to be resolved; otherwise we should
 * remove/disable the appearance of editability."*
 *
 * Three controls lived on this bar and every one of them was in the third
 * state — carrying an input's affordance while being something else. Each is
 * now in exactly one of two states: genuinely editable, or plainly not a
 * control.
 *
 *  • BASE YEAR — DERIVED. There is no ``base_year`` assumption anywhere in the
 *    model: ``revenue.projection_start_year`` is computed from the acquisition
 *    close date alone (``engines/revenue.py::projection_start_year`` — Year 1
 *    is the calendar year of the FIRST OPERATING MONTH, close + 1 month, so a
 *    December close starts Year 1 in the following year). A year-only editor
 *    here would have to invent a month and a day to write that key back, and
 *    the timeline engine anchors on the very same date. So the white boxed
 *    chip — 1px border, 6px radius, white ground, i.e. this file's own text
 *    input — is gone. The value now renders as a LINKED figure that names its
 *    owner, exactly like Exit cap rate in the Assumptions panel below.
 *
 *  • PROJECTION PERIOD — a REAL assumption the worker already accepts, so it is
 *    now genuinely editable. The key is ``hold_years``: the persisted-override
 *    loop in ``engine_runner._load_engine_inputs`` lands it on
 *    ``base['hold_years']`` (the generic scalar branch), and revenue / expense /
 *    returns all size their horizon from it. It edits through the shared
 *    ``useInlineEdit`` primitive with the FON-74 justification gate
 *    (``requiresNote('hold_years')`` is true — it moves every engine number),
 *    writing the SAME ``field_overrides.hold_years`` key the Investment tab
 *    writes. One key, one contract, two surfaces — not one number with two
 *    names.
 *
 *  • VIEW (Annual / Monthly) — REMOVED. ``projView`` was set by the toggle and
 *    read by nothing: it moved a pill and changed no column. Unlike the
 *    Grounded Worksheet's Granularity toggle (disabled with a reason until a
 *    monthly statement is extracted) this one could never become live — the
 *    revenue and expense engines project 365-day ANNUAL periods
 *    (``engines/revenue.py::DAYS_PER_YEAR``), so there is no monthly proforma
 *    series for it to show. The basis is stated in words instead.
 *
 * The −/+ stepper survives, relabelled for what it actually does: it trims the
 * columns ON SCREEN. It is not an assumption and never was, so it no longer
 * sits under a heading ("Period") that reads like the model's horizon.
 *
 * DELIBERATE DEVIATION from `design/canonical/Financials Tab.dc.html` (CLAUDE.md
 * conflict rule 1 — an explicit product clarification outranks the canonical
 * look — and rule 3, prototype values are never wired as data). The prototype
 * draws Base year as a `<select>` whose options are a hard-coded 2021 / 2022 /
 * 2023 / 2024: four fabricated years with nothing behind them, and no
 * ``base_year`` key for the chosen one to be written to. It also draws the
 * Annual / Monthly pair with no monthly series behind it. Do not "restore"
 * either from the prototype.
 */

/** The ``field_overrides`` key the Projection period edits — the deal's hold. */
const HOLD_YEARS_KEY = 'hold_years';

/** The widest hold the editor accepts, mirroring the Investment tab's parser. */
const MAX_HOLD_YEARS = 20;

/**
 * The editable Projection period (FON-41).
 *
 * ``field_overrides.hold_years`` — the same key, the same envelope and the same
 * justification gate as the Investment tab's Hold Period field. It goes through
 * ``useInlineEdit`` so Esc / click-away discard, and re-saving the current value
 * unchanged writes nothing and re-runs nothing (``isNoOpEdit``).
 */
function ProjectionPeriodField({
  holdYears, disabled, onCommit,
}: {
  /** The deal's hold. Always a real number — the section early-returns before
   *  this bar renders when the projection has no years at all. */
  holdYears: number;
  disabled?: boolean;
  onCommit: (years: number, note: string) => void | Promise<void>;
}) {
  const parse = (draft: string): number | null => {
    const n = Number(draft.replace(/[^\d.-]/g, ''));
    if (!Number.isFinite(n)) return null;
    const years = Math.round(n);
    // No clamping — a silent clamp persists a hold the analyst never chose.
    return years >= 1 && years <= MAX_HOLD_YEARS ? years : null;
  };
  const ed = useInlineEdit<number>({
    current: holdYears,
    unit: 'years',
    parse,
    onSave: onCommit,
    toDraft: (v) => String(v),
    invalidMessage: `Enter a hold period between 1 and ${MAX_HOLD_YEARS} years.`,
    // FON-74 — this one DOES move every engine number, so it is gated. Resolved
    // from the shared contract, never hard-coded.
    requireNote: requiresNote(HOLD_YEARS_KEY),
  });

  if (!ed.editing) {
    return (
      <button
        type="button"
        data-testid="projection-period-value"
        disabled={disabled}
        title="Click to change the projection period. Writes the deal’s hold period (field_overrides.hold_years) and re-runs the model."
        onClick={() => ed.start(String(holdYears))}
        style={{
          fontSize: 12.5, fontWeight: 600, color: '#1a2233',
          background: 'none', border: 'none', padding: 0, fontFamily: 'inherit',
          cursor: disabled ? 'default' : 'pointer',
          textDecoration: 'underline dotted',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {`${holdYears} years`}
      </button>
    );
  }
  return (
    <span ref={ed.containerRef} style={{ display: 'inline-flex', alignItems: 'flex-start', gap: 6 }}>
      <input
        type="number" min={1} max={MAX_HOLD_YEARS} value={ed.draft} autoFocus disabled={ed.saving}
        aria-label="Projection period"
        data-testid="projection-period-input"
        onChange={(e) => ed.setDraft(e.target.value)}
        onKeyDown={ed.onKeyDown}
        style={{ ...inlineEditInputStyle, width: 64 }}
      />
      <InlineEditControls
        onSave={() => void ed.submit()}
        onCancel={ed.cancel}
        saving={ed.saving}
        saveTestId="projection-period-save"
        cancelTestId="projection-period-cancel"
        noteTestId="projection-period-note"
        noteLabel="Projection period — override justification"
        {...(ed.requireNote ? { note: ed.note, onNote: ed.setNote } : null)}
      />
    </span>
  );
}

// The Base year / Projection period / Show control bar above the Assumptions panel.
function ProjectionsControls({
  dealId, years, closeDateIso, holdYears, onSaveHoldYears, running,
  shownForecast, forecastCount, onDec, onInc, showPct, onTogglePct,
  benchmarkAvailable = false, showBenchmark = true, onToggleBenchmark,
}: {
  dealId: string;
  years: ProjYear[];
  closeDateIso: string | null;
  /** The deal's hold, override-first — what the editor compares Save against. */
  holdYears: number;
  onSaveHoldYears: (years: number, note: string) => Promise<void>;
  running: boolean;
  shownForecast: number;
  forecastCount: number;
  onDec: () => void;
  onInc: () => void;
  /** R-068 — the "% Rev" sub-column switch (view only). */
  showPct: boolean;
  onTogglePct: () => void;
  /** FON-97 — the "Benchmark" sub-column switch. Rendered only when the deal
   *  carries a P&L benchmark; view only, remembered per device. */
  benchmarkAvailable?: boolean;
  showBenchmark?: boolean;
  onToggleBenchmark?: () => void;
}) {
  // The FIRST column's calendar year, derived by the revenue engine from the
  // acquisition close date. `years[0].year` is the ordinal 1 — showing it here
  // printed a literal "1" in the Base year chip.
  const baseYear = years[0]?.calendarYear;
  const ctrlLabel: CSSProperties = { fontSize: 11, color: '#6b6f76', fontWeight: 600 };
  const group: CSSProperties = { display: 'flex', alignItems: 'center', gap: 6 };
  const ownerLink: CSSProperties = {
    fontSize: 10.5, color: '#2f4a8c', textDecoration: 'none', whiteSpace: 'nowrap',
  };
  const stepBtn: CSSProperties = {
    width: 24, height: 26, border: '1px solid #e2e1dc', background: '#fff',
    borderRadius: 6, cursor: 'pointer', fontSize: 14, color: '#3a3f47', lineHeight: 1,
  };
  const baseYearTitle = baseYear != null
    ? `Base year (Year 1) is calendar ${baseYear} — derived from the acquisition close date${closeDateIso ? ` (${fmtIsoDate(closeDateIso)})` : ''}, whose first operating month starts the projection. It is not an input here; edit the Acquisition Date on the CAPEX tab.`
    : 'No acquisition close date on this deal, so the projection has no calendar year. The base year is derived from that date — set the Acquisition Date on the CAPEX tab.';
  return (
    <div
      data-testid="projections-controls"
      style={{ padding: '12px 22px', borderBottom: '1px solid #eee', background: '#fbfbf9', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}
    >
      {/* Base year — DERIVED. No box, no cursor, no editor: a linked figure
          that names the input it comes from. */}
      <div style={group}>
        <span style={ctrlLabel}>Base year</span>
        <span
          data-testid="projection-base-year"
          title={baseYearTitle}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 5,
            fontSize: 12.5, fontWeight: 600,
            color: baseYear != null ? '#1a2233' : '#9a9a95',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          <ProvenanceDot state="linked" size={8} title="Derived from the acquisition close date" />
          {baseYear ?? '—'}
        </span>
        <Link
          href={`/projects/${dealId}?tab=investment`}
          data-testid="projection-base-year-owner"
          title="The acquisition close date — the only input the base year is derived from — is owned by the CAPEX tab"
          style={ownerLink}
        >
          CAPEX →
        </Link>
      </div>

      {/* Projection period — a real assumption (field_overrides.hold_years),
          genuinely editable here with the FON-74 justification. */}
      <div style={{ ...group, alignItems: 'flex-start' }}>
        <span style={{ ...ctrlLabel, paddingTop: 3 }}>Projection period</span>
        <ProjectionPeriodField
          holdYears={holdYears}
          disabled={running}
          onCommit={onSaveHoldYears}
        />
      </div>

      {/* Show — a VIEW trim, labelled as one. It changes which columns render
          and nothing else; the modelled horizon is the Projection period. */}
      <div
        style={group}
        title="View only — trims the columns shown below. The model is unchanged; the modelled horizon is the Projection period."
      >
        <span style={ctrlLabel}>Show</span>
        <button type="button" aria-label="Show one fewer year" onClick={onDec} disabled={shownForecast <= 1} style={{ ...stepBtn, opacity: shownForecast <= 1 ? 0.5 : 1 }}>−</button>
        {/* Counted off the RENDERED columns, not off `forecastCount` — on a
            one-year projection `forecastCount` floors at 1 while there is only
            one column, and "2 of 2 years" would be a number nothing backs. */}
        <span data-testid="projection-columns-shown" style={{ fontSize: 12.5, fontWeight: 700, color: '#1a2233', minWidth: 78, textAlign: 'center', fontVariantNumeric: 'tabular-nums' }}>
          {Math.min(shownForecast + 1, years.length)} of {years.length} {years.length === 1 ? 'year' : 'years'}
        </span>
        <button type="button" aria-label="Show one more year" onClick={onInc} disabled={shownForecast >= forecastCount} style={{ ...stepBtn, opacity: shownForecast >= forecastCount ? 0.5 : 1 }}>+</button>
        <span style={{ fontSize: 10.5, color: '#9a9a95', fontStyle: 'italic' }}>view only</span>
      </div>

      {/* R-068 — Show %: the "% Rev" sub-column. A real control (it changes
          which cells render) and labelled as view-only; the ratios are
          computed from the same engine year values the Amount cells print. */}
      <label
        style={{ ...group, gap: 5 }}
        title="Show each line as a share of Total Revenue — departmental expenses as a share of their own department's revenue (USALI). View only; nothing is re-modelled."
      >
        <input
          type="checkbox"
          role="switch"
          aria-checked={showPct}
          checked={showPct}
          onChange={onTogglePct}
          aria-label="Show % of revenue"
          data-testid="projection-show-pct"
          style={{ margin: 0, cursor: 'pointer' }}
        />
        <span style={ctrlLabel}>Show %</span>
      </label>

      {/* FON-97 — Benchmark: the Base-year "Benchmark" sub-column. Only offered
          when the deal carries a P&L benchmark (no switch for a column that
          cannot exist); view only, remembered per device. */}
      {benchmarkAvailable && (
        <label
          style={{ ...group, gap: 5 }}
          title="Show the CBRE / HotStats comp-set ratio beside each Base-year line, with the model's difference in points. View only; the benchmark never changes the model. Remembered on this device."
        >
          <input
            type="checkbox"
            role="switch"
            aria-checked={showBenchmark}
            checked={showBenchmark}
            onChange={onToggleBenchmark}
            aria-label="Show benchmark column"
            data-testid="projection-show-benchmark"
            style={{ margin: 0, cursor: 'pointer' }}
          />
          <span style={ctrlLabel}>Benchmark</span>
        </label>
      )}

      {/* What replaced the dead Annual / Monthly toggle: the basis, stated. */}
      <span
        data-testid="projection-basis-note"
        title="The revenue and expense engines project 365-day annual periods (apps/worker/app/engines/revenue.py, DAYS_PER_YEAR), so this statement has no monthly series to show. Monthly detail exists for the debt schedule on the Financing tab."
        style={{
          fontSize: 11, color: '#6b6f76', cursor: 'help',
          textDecoration: 'underline dotted', textUnderlineOffset: 3,
        }}
      >
        Annual periods
      </span>

      {/* FON-41 E-014 — why the columns carry the years they do, and (when
          they carry none) the one line that says where the date is set. */}
      <div style={{ flexBasis: '100%', display: 'flex', flexDirection: 'column', gap: 3 }}>
        {baseYear == null && (
          <span
            data-testid="projection-no-close-date-note"
            role="note"
            style={{ fontSize: 11.5, color: '#7a5c17', fontWeight: 600, lineHeight: 1.45 }}
          >
            {NO_CLOSE_DATE_NOTE}
            {' — '}
            <Link
              href={`/projects/${dealId}?tab=investment`}
              style={{ color: '#2f4a8c', textDecoration: 'none', fontWeight: 600 }}
            >
              CAPEX →
            </Link>
          </span>
        )}
        <span data-testid="projection-calendar-help" style={{ fontSize: 11, color: '#6b6f76', lineHeight: 1.45 }}>
          {CALENDAR_MAPPING_HELP}
        </span>
      </div>
    </div>
  );
}

// One editable assumption row: label + right-aligned numeric input with affixes.
// Commits on blur / Enter; pct fields store as a fraction (value/100).
function AssumptionField({
  label, value, unit, prefix, suffix, onCommit, disabled, overrideKey, overridden, onReset,
}: {
  label: string;
  /** null = not set (an opt-in assumption): the input renders blank. */
  value: number | null;
  unit: 'pct' | 'dollar';
  prefix?: string;
  suffix?: string;
  onCommit: (engineValue: number, note: string) => void;
  disabled?: boolean;
  /** FON-74 — the `field_overrides` key; drives whether Save needs a reason. */
  overrideKey: string;
  /** An analyst override is stored for this key — shows "Reset to source" when `onReset` is given. */
  overridden?: boolean;
  /** Drop the stored override (the source value then drives the model again). */
  onReset?: () => void;
}) {
  const display = value == null ? null : unit === 'pct' ? value * 100 : value;
  const fmt = (n: number | null) =>
    n == null ? '' : Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10);
  const [draft, setDraft] = useState<string>(fmt(display));
  // FON-74 — a real change parks here until the analyst justifies it. null =
  // nothing pending; a number = the engine value waiting on a reason.
  const [pending, setPending] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const requireNote = requiresNote(overrideKey);
  useEffect(() => { setDraft(fmt(display)); }, [display]);
  const revert = () => { setDraft(fmt(display)); setPending(null); setNote(''); };
  const commit = () => {
    const cleaned = draft.replace(/[$,%\s]/g, '');
    // A blank field is never read as 0 — clearing is not an edit here.
    if (cleaned === '') { revert(); return; }
    const n = Number(cleaned);
    if (!Number.isFinite(n)) { revert(); return; }
    const eng = unit === 'pct' ? n / 100 : n;
    // FON-63 — one comparison for every editor (no change → no re-run, no override).
    // FON-74's gate sits strictly after it: an untouched field never asks why.
    if (isNoOpEdit(eng, value, unit === 'pct' ? 'pct_fraction' : 'usd')) { setPending(null); setNote(''); return; }
    if (!requireNote) { onCommit(eng, ''); return; }
    setPending(eng);
  };
  const save = () => {
    if (pending == null) return;
    if (!note.trim()) return;
    onCommit(pending, note.trim());
    setPending(null);
    setNote('');
  };
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10 }}>
      <span style={{ fontSize: 12, color: '#6b6f76', paddingTop: 5 }}>{label}</span>
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 5 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
        {prefix && <span style={{ fontSize: 11, color: '#6b6f76' }}>{prefix}</span>}
        {/* FON-41b (Sam, 2026-09-09) — at 46px "4.5" clipped to "4.!" and "60" to
            "6C" once the number spinner took its share. 72px + minWidth + tabular
            figures so any 1–4 char value renders whole. */}
        <input
          type="number"
          value={draft}
          disabled={disabled}
          aria-label={label}
          data-testid={`assumption-panel-input-${overrideKey}`}
          placeholder={value == null ? '—' : undefined}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
            if (e.key === 'Escape') { revert(); (e.target as HTMLInputElement).blur(); }
          }}
          style={{ fontSize: 13, fontWeight: 600, border: '1px solid #e2e1dc', borderRadius: 6, padding: '5px 7px', color: '#1a2233', width: 72, minWidth: 72, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
        />
        {suffix && <span style={{ fontSize: 11, color: '#6b6f76' }}>{suffix}</span>}
      </div>
      {overridden && onReset && pending == null && (
        <button
          type="button"
          data-testid={`assumption-panel-reset-${overrideKey}`}
          disabled={disabled}
          onClick={onReset}
          title="Remove the analyst override so the source value (portfolio P&L or platform default) drives the model again, and re-run."
          style={{ fontSize: 10.5, fontWeight: 600, color: '#2f4a8c', background: 'none', border: 'none', padding: 0, cursor: disabled ? 'default' : 'pointer', fontFamily: 'inherit' }}
        >
          Analyst override · Reset to source
        </button>
      )}
      {/* FON-74 — the change is held until it is explained. Nothing is written
          and nothing re-runs until Save; Cancel puts the field back. */}
      {pending != null && (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'stretch', gap: 5, width: 210 }}>
          <input
            value={note}
            autoFocus
            aria-label={`${label} — override justification`}
            data-testid={`assumption-panel-note-${overrideKey}`}
            placeholder={NOTE_PLACEHOLDER}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); save(); }
              if (e.key === 'Escape') { e.preventDefault(); revert(); }
            }}
            style={{ fontSize: 11, fontFamily: 'inherit', border: '1px solid #2f4a8c', borderRadius: 6, padding: '4px 7px', color: '#1a2233' }}
          />
          <InlineEditControls
            onSave={save}
            onCancel={revert}
            saveTestId={`assumption-panel-save-${overrideKey}`}
          />
        </div>
      )}
      </div>
    </div>
  );
}

/**
 * The editable Stabilization Year (FON-41 / FON-59 #3).
 *
 * A 1-based model year persisted to ``field_overrides.stabilization_year``.
 * It goes through ``useInlineEdit`` + ``isNoOpEdit`` like every other editor,
 * so Esc / click-away discard and re-saving the seeded value unchanged writes
 * nothing — the badge keeps reading "Fondok-derived", which is what the worker
 * publishes on ``stabilization.source`` for a value equal to its own signal.
 *
 * The year is DISPLAY-ONLY in the model: it selects which projection year the
 * stabilized figures are read from. It moves no return.
 */
function StabilizationYearField({
  stabilization, modelYears, calendarYears, disabled, onCommit,
}: {
  stabilization: StabilizedYearBlock | null;
  modelYears: number;
  calendarYears: number[] | null;
  disabled?: boolean;
  onCommit: (year: number) => void | Promise<void>;
}) {
  const current = stabilization?.stabilized_year ?? null;
  const parse = (draft: string): number | null => {
    const n = Number(draft.replace(/[^\d.-]/g, ''));
    if (!Number.isFinite(n)) return null;
    const year = Math.round(n);
    // A year the projection does not have is not a year. No clamping — a
    // silent clamp would persist a number the analyst never chose.
    return year >= 1 && year <= modelYears ? year : null;
  };
  const ed = useInlineEdit<number>({
    current,
    unit: 'count',
    parse,
    onSave: onCommit,
    toDraft: (v) => String(v),
    invalidMessage: `Enter a projection year between 1 and ${modelYears}.`,
  });

  const calendarYear =
    stabilization && calendarYears && calendarYears.length > stabilization.stabilized_year_index
      ? calendarYears[stabilization.stabilized_year_index]
      : undefined;
  const badge = stabilizationBadge(stabilization);
  const note = stabilizationSignalNote(stabilization);
  const derived = stabilization?.source !== 'analyst_override';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <span style={{ fontSize: 12, color: '#6b6f76' }}>Stabilization Year</span>
        {!ed.editing ? (
          <button
            type="button"
            data-testid="stabilization-year-value"
            disabled={disabled || current == null}
            title={current == null
              ? 'No projection year has resolved yet — run the model.'
              : 'Click to change the Stabilization Year'}
            onClick={() => ed.start(current != null ? String(current) : '')}
            style={{
              fontSize: 13, fontWeight: 600, color: current == null ? '#9a9a95' : '#1a2233',
              background: 'none', border: 'none', padding: 0, fontFamily: 'inherit',
              cursor: current == null ? 'default' : 'pointer',
              textDecoration: current == null ? 'none' : 'underline dotted',
              fontVariantNumeric: 'tabular-nums',
            }}
          >
            {current == null
              ? '—'
              : calendarYear != null
                ? `Year ${current} — ${calendarYear}`
                : `Year ${current}`}
          </button>
        ) : (
          <span ref={ed.containerRef} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <input
              type="number" min={1} max={modelYears} value={ed.draft} autoFocus disabled={ed.saving}
              aria-label="Stabilization Year"
              onChange={(e) => ed.setDraft(e.target.value)}
              onKeyDown={ed.onKeyDown}
              style={{ ...inlineEditInputStyle, width: 64 }}
            />
            <InlineEditControls onSave={() => void ed.submit()} onCancel={ed.cancel} saving={ed.saving} />
          </span>
        )}
      </div>
      {badge && (
        <span
          data-testid="stabilization-year-badge"
          title={note ?? undefined}
          style={{
            alignSelf: 'flex-end',
            fontSize: 10, fontWeight: 700, letterSpacing: '.03em',
            color: derived ? '#7a5c17' : '#2f4a8c',
            background: derived ? 'rgba(214,168,50,.14)' : 'rgba(47,74,140,.10)',
            borderRadius: 4, padding: '2px 5px',
          }}
        >
          {badge}
        </span>
      )}
      {note && (
        <p style={{ fontSize: 11, color: '#6b6f76', lineHeight: 1.45, margin: 0 }}>{note}</p>
      )}
      {stabilization == null && (
        <p style={{ fontSize: 11, color: '#6b6f76', lineHeight: 1.45, margin: 0 }}>
          Awaiting a model run — Overview&apos;s Stabilization rows stay blank until a
          projection year resolves.
        </p>
      )}
    </div>
  );
}

// "These drive every projected year" — the canonical Assumptions panel.
// Each editable field writes the deal's field_overrides and re-runs the whole
// model (the canonical edit path, shared with the driver cells). Exit cap rate
// is Investment-owned, so it is shown here linked / read-only.
function AssumptionsPanel({
  dealId, overrides, onApply, onReset, running, stabilization, modelYears, calendarYears, operatingModel,
}: {
  dealId: string;
  /** R-025 — the deal's intended operating model (descriptive; no engine reads it). */
  operatingModel?: string | null;
  overrides: Record<string, unknown>;
  /** FON-74 — `note` is the ANALYST's justification, or '' on an exempt key. */
  onApply: (key: string, value: number, note: string) => Promise<boolean>;
  /** Drop one `field_overrides` entry and re-run — the undo for an applied benchmark. */
  onReset: (key: string) => Promise<boolean>;
  running: boolean;
  /** The worker's published stabilized-year block (null until a run carries one). */
  stabilization: StabilizedYearBlock | null;
  /** How many modelled years the projection has — the editor's upper bound. */
  modelYears: number;
  calendarYears: number[] | null;
}) {
  // Exit cap rate is owned by Investment — resolve its live value (never edited here).
  const exitCapSrc = useSource('exit_cap_rate');
  const exitCapFraction =
    (typeof exitCapSrc?.value === 'number' ? exitCapSrc.value : ovValue(overrides, 'exit_cap_rate'))
    ?? ASSUMPTION_DEFAULTS.exit_cap_rate;
  const cur = (key: string) => ovValue(overrides, key) ?? ASSUMPTION_DEFAULTS[key];
  // FF&E reserve: the analyst's override, else the value the loader is on
  // (its provenance payload, else the benchmark suggestion's "in use"), else
  // blank — never a number the browser made up.
  const ffeSrc = useSource('ffe_reserve_pct');
  const { suggestion: ffeSuggestion } = useBenchmarkSuggestion('ffe_reserve_pct');
  const ffeReserve: number | null =
    ovValue(overrides, 'ffe_reserve_pct')
    ?? (typeof ffeSrc?.value === 'number' ? ffeSrc.value : null)
    ?? (typeof ffeSuggestion?.current_value === 'number' ? ffeSuggestion.current_value : null);

  const cardStyle: CSSProperties = { flex: 1, minWidth: 220, border: '1px solid #e2e1dc', borderRadius: 8, padding: '12px 14px', background: '#fbfbf9' };
  const cardTitle: CSSProperties = { fontSize: 11, fontWeight: 700, color: '#1a2233', marginBottom: 10 };
  const rowsWrap: CSSProperties = { display: 'flex', flexDirection: 'column', gap: 8 };

  return (
    <div style={{ padding: '16px 22px', borderBottom: '1px solid #eee', background: '#fff' }}>
      <div style={{ fontSize: 11, fontWeight: 700, color: '#6b6f76', textTransform: 'uppercase', letterSpacing: '.04em', marginBottom: 2 }}>
        Assumptions
      </div>
      <div style={{ fontSize: 12, color: '#9a9a95', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 8 }}>
        These drive every projected year below — change any value to see the statement recompute.
        {running && <span style={{ color: '#2f4a8c', fontWeight: 600 }}>Re-modeling…</span>}
      </div>
      <div style={{ display: 'flex', gap: 14, flexWrap: 'wrap' }}>
        {/* Growth */}
        <div style={cardStyle}>
          <div style={cardTitle}>Growth</div>
          <div style={rowsWrap}>
            {/* FON-69 — a RevPAR-growth override derives adr_growth in the worker
                (occupancy path held) so operating NOI moves; the label says so. */}
            <AssumptionField label="RevPAR growth (drives ADR; occupancy path held)" unit="pct" suffix="%/yr" overrideKey="revpar_growth" value={cur('revpar_growth')} disabled={running} onCommit={(v, note) => onApply('revpar_growth', v, note)} />
            <AssumptionField label="Dept. expense inflation" unit="pct" suffix="%/yr" overrideKey="expense_growth" value={cur('expense_growth')} disabled={running} onCommit={(v, note) => onApply('expense_growth', v, note)} />
            <AssumptionField label="Other expense inflation" unit="pct" suffix="%/yr" overrideKey="other_expense_growth" value={cur('other_expense_growth')} disabled={running} onCommit={(v, note) => onApply('other_expense_growth', v, note)} />
          </div>
        </div>
        {/* Stabilization Year — FON-41 / FON-59 #3. The analyst owns it; the
            worker seeds it from the occupancy / NOI-plateau signal and says so
            until it is confirmed. Overview's Stabilized Occupancy / ADR /
            Revenue / NOI / Margin and the STABILIZED column badge all read the
            year selected here. */}
        <div style={cardStyle}>
          <div style={cardTitle}>Stabilization</div>
          <div style={rowsWrap}>
            <StabilizationYearField
              stabilization={stabilization}
              modelYears={modelYears}
              calendarYears={calendarYears}
              disabled={running}
              // FON-74 — display-only in the model (it selects which projection
              // year the stabilized figures are read from and moves no return),
              // so it carries no justification. The software-authored one it
              // used to write is gone: a blank note, never an invented one.
              onCommit={(year) => onApply('stabilization_year', year, '').then(() => undefined)}
            />
          </div>
        </div>
        {/* Resort fee revenue */}
        <div style={cardStyle}>
          <div style={cardTitle}>Resort fee revenue</div>
          <div style={rowsWrap}>
            <AssumptionField label="Resort fee" unit="dollar" prefix="$" suffix="/night" overrideKey="resort_fee_per_night" value={cur('resort_fee_per_night')} disabled={running} onCommit={(v, note) => onApply('resort_fee_per_night', v, note)} />
            {/* FON-41 — the three capture inputs are labelled by the COLUMN
                they move, not by the engine's year index. The revenue engine
                runs y = 1…hold_years and the statement heads y=1 as "Base
                Year", so ``resort_fee_capture_y1`` has always landed on the
                Base Year column, ``_y2`` on Year 1, ``_y3`` on Year 2 onward.
                Founder decision: fix the COLUMN LABELS in Wave 3 rather than
                re-index the engine — so these labels (and the note below)
                name the columns the analyst actually reads. Engine math and
                the ``field_overrides`` keys are untouched. */}
            <AssumptionField label="Capture — Base year (Year 1)" unit="pct" suffix="%" overrideKey="resort_fee_capture_y1" value={cur('resort_fee_capture_y1')} disabled={running} onCommit={(v, note) => onApply('resort_fee_capture_y1', v, note)} />
            <AssumptionField label="Capture — Year 2" unit="pct" suffix="%" overrideKey="resort_fee_capture_y2" value={cur('resort_fee_capture_y2')} disabled={running} onCommit={(v, note) => onApply('resort_fee_capture_y2', v, note)} />
            <AssumptionField label="Capture — Year 3+" unit="pct" suffix="%" overrideKey="resort_fee_capture_y3" value={cur('resort_fee_capture_y3')} disabled={running} onCommit={(v, note) => onApply('resort_fee_capture_y3', v, note)} />
            <p style={{ fontSize: 11, color: '#6b6f76', lineHeight: 1.45, margin: 0 }}>
              Each capture applies to the column above it — Base year (Year 1) is the model&apos;s
              first operating year, Year 2 is the one after it, and Year 3+ carries through every
              later year.
            </p>
          </div>
        </div>
        {/* Deal economics */}
        <div style={cardStyle}>
          <div style={cardTitle}>Deal economics</div>
          <div style={rowsWrap}>
            <AssumptionField label="Management fee" unit="pct" suffix="% of rev" overrideKey="mgmt_fee_pct" value={cur('mgmt_fee_pct')} disabled={running} onCommit={(v, note) => onApply('mgmt_fee_pct', v, note)}
              overridden={'mgmt_fee_pct' in overrides} onReset={() => void onReset('mgmt_fee_pct')} />
            {/* FON-109 — the P&L benchmark's fee / FF&E reserve are suggestions:
                nothing changes until the analyst applies one (with a note). */}
            <ApplyBenchmarkAction overrideKey="mgmt_fee_pct" />
            {/* The reserve's own input: shows the value in use, takes an analyst
                override with its note, and — once an override (an applied
                benchmark included) exists — offers "Reset to source", the undo
                this action had nowhere else in the deal UI. */}
            <AssumptionField label="FF&E reserve" unit="pct" suffix="% of rev" overrideKey="ffe_reserve_pct" value={ffeReserve} disabled={running} onCommit={(v, note) => onApply('ffe_reserve_pct', v, note)}
              overridden={'ffe_reserve_pct' in overrides} onReset={() => void onReset('ffe_reserve_pct')} />
            <ApplyBenchmarkAction overrideKey="ffe_reserve_pct" label="FF&E reserve" />
            {/* R-025 — the operating model beside the fee it informs. Context
                only: the engine's fee math does not read it. */}
            {/* Sam #9 — the opt-in incentive fee beside the base fee. Blank =
                none modelled (the engine then emits no incentive fee). */}
            <AssumptionField label="Incentive fee" unit="pct" suffix="% of GOP after base" overrideKey="incentive_mgmt_fee_pct_of_gop" value={ovValue(overrides, 'incentive_mgmt_fee_pct_of_gop')} disabled={running} onCommit={(v, note) => onApply('incentive_mgmt_fee_pct_of_gop', v, note)} />
            {mgmtFeeOperatingModelHint(operatingModel) && (
              <p data-testid="mgmt-fee-operating-model" style={{ fontSize: 11, color: '#6b6f76', lineHeight: 1.45, margin: 0 }}>
                {mgmtFeeOperatingModelHint(operatingModel)}
              </p>
            )}
            {/* Exit cap rate is Investment-owned — linked / read-only reference. */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
              <span style={{ fontSize: 12, color: '#6b6f76' }}>Exit cap rate</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 13, fontWeight: 600, color: '#1a2233', fontVariantNumeric: 'tabular-nums' }}>
                  <ProvenanceDot state="linked" size={8} title="Linked from the CAPEX tab" />
                  {(exitCapFraction * 100).toFixed(1)}%
                </span>
                <Link
                  href={`/projects/${dealId}?tab=investment`}
                  title="Exit cap rate is owned by the CAPEX tab"
                  style={{ fontSize: 10.5, color: '#2f4a8c', textDecoration: 'none', whiteSpace: 'nowrap' }}
                >
                  CAPEX →
                </Link>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
