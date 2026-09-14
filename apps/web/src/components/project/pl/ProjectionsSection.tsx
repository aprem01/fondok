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
 *    both 2025"*). The first column is now **Base Year (Year 1)** and the rest
 *    are shifted, so index 1 heads "Year 2". No engine value moved: the labels
 *    were wrong, not the math.
 *  • ``RevenueProjectionYear.year`` is an ORDINAL (1..hold_years), never a
 *    calendar year. The calendar comes from ``revenue.projection_calendar_years``
 *    (anchored on the acquisition close date). With no close date the column
 *    shows its label alone — never a guessed year.
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
import { Sparkles, Download, FileText } from 'lucide-react';
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
  overrideEnvelope,
  overrideNoteFor,
  requiresNote,
  NOTE_PLACEHOLDER,
  NOTE_REQUIRED_MESSAGE,
} from '@/lib/overrideNote';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/format';
import {
  noiBeforeReserveLabel,
  stabilizedYearBlock,
  stabilizationBadge,
  stabilizationSignalNote,
  type StabilizedYearBlock,
} from '@/lib/engines/noi';
import { Traced } from '@/components/help/Traced';
import { Sourced } from '@/components/help/Sourced';
import { useRefusal } from '@/components/help/Refused';
import { useSource } from '@/lib/hooks/useDealProvenance';
import { sourceKind, sourceLabel, sourceExplanation, KIND_TONE, isStrMarketOverride, isStrBasisSource } from '@/lib/provenance';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useDeal } from '@/lib/hooks/useDeal';
import { api } from '@/lib/api';
import { downloadXlsx, type XlsxCell } from '@/lib/exportXlsx';

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
  occupiedRooms: number;
  occupancy: number;     // 0..1
  adr: number;           // dollars
  revpar: number;        // dollars
  roomsRevenue: number;  // dollars
  fbRevenue: number;     // dollars
  // USALI 11th: Other Operated Departments (spa, golf, parking,
  // rentals — anything ancillary that runs as its own department)
  // sits as its own revenue line. Distinct from Resort Fees and
  // Misc. Income, both of which are smaller / non-departmental.
  otherOperatedRevenue: number;
  resortFees: number;
  miscRevenue: number;   // dollars (Other Misc Income only — small ancillary)
  totalRevenue: number;  // dollars
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
  return i === 0 ? 'Base Year (Year 1)' : `Year ${i + 1}`;
}

/** The Exit Year column's header — display-only, never a modelled year. */
export const EXIT_COLUMN_LABEL = 'Exit Year';

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
}: {
  dealId: string;
}) {
  const { toast } = useToast();
  const { outputs } = useEngineOutputs(dealId);
  const { deal, refresh: refreshDeal } = useDeal(dealId);

  // FON-27: inline override of a driver assumption. Writes the deal's
  // field_overrides (the engine reads starting_occupancy / starting_adr from
  // there — analyst intent wins over every data source) then re-runs the
  // model. useEngineOutputs auto-refreshes when the run completes.
  const { run, status: runStatus } = useEngineRun(dealId, 'returns', { runMode: 'all' });
  const overrides = useMemo(
    () => (deal?.field_overrides ?? {}) as Record<string, unknown>,
    [deal],
  );
  const applyOverride = useCallback(
    async (key: string, value: number, note: string) => {
      try {
        await api.deals.update(dealId, {
          field_overrides: { ...overrides, [key]: overrideEnvelope(key, value, note) },
        });
        refreshDeal();
        await run();
        toast('Override applied — re-modeled', { type: 'success' });
      } catch {
        toast('Could not apply override', { type: 'error' });
      }
    },
    [overrides, dealId, refreshDeal, run, toast],
  );
  const resetOverride = useCallback(
    async (key: string) => {
      const { [key]: _drop, ...rest } = overrides;
      try {
        await api.deals.update(dealId, { field_overrides: rest });
        refreshDeal();
        await run();
        toast('Reset to source — re-modeled', { type: 'success' });
      } catch {
        toast('Could not reset override', { type: 'error' });
      }
    },
    [overrides, dealId, refreshDeal, run, toast],
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

  const onExport = async () => {
    // FON-41 #2 — the export header is the table header. They change together
    // or the Excel column mapping shifts against what the analyst reviewed.
    // ``y.year`` (the ordinal) is NEVER printed as a year; the calendar year
    // is appended only when the deal carries an acquisition close date.
    const colHeader = (label: string, calendarYear?: number) =>
      calendarYear != null ? `${label} ${calendarYear}` : label;
    const headers: XlsxCell[] = [
      'Metric',
      ...years.flatMap((y, i) => {
        const label = colHeader(projectionColumnLabel(i), y.calendarYear);
        return [
          `${label} Amount`,
          `${label} % Rev`,
          `${label} PAR`,
          `${label} POR`,
        ];
      }),
      ...(() => {
        const label = colHeader(EXIT_COLUMN_LABEL, exitCalendarYear);
        return [
          `${label} Amount`,
          `${label} % Rev`,
          `${label} PAR`,
          `${label} POR`,
        ] as XlsxCell[];
      })(),
    ];
    const rows: XlsxCell[][] = [headers];
    const trMap = years.map(y => y.totalRevenue);
    const arMap = years.map(y => y.availableRooms);
    const orMap = years.map(y => y.occupiedRooms);
    // The Exit Year column is display-only on every row but the forward NOI.
    const EXIT_BLANKS: XlsxCell[] = ['', '', '', ''];
    const expand = (label: string, vals: number[], asPct = false) => {
      const cells: XlsxCell[] = [label];
      vals.forEach((v, i) => {
        // Keep numerics as numbers so Excel can re-sum / re-format. The
        // % Rev / PAR / POR columns stay blank when the row itself is a
        // percentage (e.g. Occupancy) — those derivations don't apply.
        const amount = asPct
          ? Number((v * 100).toFixed(1))
          : Number(v.toFixed(0));
        const pctRev = trMap[i] > 0 && !asPct
          ? Number(((v / trMap[i]) * 100).toFixed(1))
          : '';
        const par = arMap[i] > 0 && !asPct
          ? Number(((v / arMap[i]) * 1000).toFixed(2))
          : '';
        const por = orMap[i] > 0 && !asPct
          ? Number(((v / orMap[i]) * 1000).toFixed(2))
          : '';
        cells.push(amount, pctRev, par, por);
      });
      cells.push(...EXIT_BLANKS);
      rows.push(cells);
    };
    // Emit a row of pure-numeric values (no % Rev / PAR / POR
    // derivations) — used for headcount-style rows like Days,
    // Rooms, and the institutional NOI summary block.
    const plain = (label: string, vals: (number | undefined)[]) => {
      if (vals.every(v => v === undefined)) return; // skip if engine didn't emit any year
      rows.push([
        label,
        ...vals.flatMap(v =>
          [v == null ? '' : Number(v.toFixed(0)), '', '', ''] as XlsxCell[],
        ),
        ...EXIT_BLANKS,
      ]);
    };

    rows.push(['Days', ...years.flatMap(y => [y.days, '', '', '']) as XlsxCell[], ...EXIT_BLANKS]);
    rows.push(['Number of Rooms', ...years.flatMap(y => [y.rooms, '', '', '']) as XlsxCell[], ...EXIT_BLANKS]);
    rows.push(['Available Rooms', ...years.flatMap(y => [y.availableRooms, '', '', '']) as XlsxCell[], ...EXIT_BLANKS]);
    rows.push(['Occupied Rooms', ...years.flatMap(y => [y.occupiedRooms, '', '', '']) as XlsxCell[], ...EXIT_BLANKS]);
    expand('Occupancy', years.map(y => y.occupancy), true);
    expand('Average Rate', years.map(y => y.adr));
    expand('RevPAR', years.map(y => y.revpar));

    // REVENUES — USALI 11th order. Rooms → F&B → Other Operated
    // Departments (its own line per Sam's P4 ask) → Resort Fees →
    // Misc Income → Total Revenue.
    expand('Rooms Revenue', years.map(y => y.roomsRevenue));
    expand('Food & Beverage Revenue', years.map(y => y.fbRevenue));
    expand('Other Operated Departments', years.map(y => y.otherOperatedRevenue));
    if (years.some(y => y.resortFees > 0)) {
      expand('Resort Fees', years.map(y => y.resortFees));
    }
    if (years.some(y => y.miscRevenue > 0)) {
      expand('Miscellaneous Income', years.map(y => y.miscRevenue));
    }
    expand('Total Revenue', years.map(y => y.totalRevenue));

    // DEPARTMENTAL EXPENSES — only rendered when the engine emitted
    // them (real worker deals; not on Kimpton demo or revenue-only
    // engine output).
    if (years.some(y => y.deptTotalExpense != null)) {
      plain('Rooms Departmental Expense', years.map(y => y.deptRoomsExpense));
      plain('Food & Beverage Departmental Expense', years.map(y => y.deptFbExpense));
      plain('Other Operated Departmental Expense', years.map(y => y.deptOtherExpense));
      plain('Total Departmental Expenses', years.map(y => y.deptTotalExpense));
    }

    // UNDISTRIBUTED EXPENSES
    if (years.some(y => y.undistTotal != null)) {
      plain('Administrative & General', years.map(y => y.undistAdminGeneral));
      plain('Information & Telecom', years.map(y => y.undistInfoTelecom));
      plain('Sales & Marketing', years.map(y => y.undistSalesMarketing));
      plain('Property Operations & Maintenance', years.map(y => y.undistPropertyOps));
      plain('Utilities', years.map(y => y.undistUtilities));
      plain('Total Undistributed Expenses', years.map(y => y.undistTotal));
    }

    // SUBTOTALS + FIXED CHARGES + NOI + FCF
    plain('Gross Operating Profit (GOP)', years.map(y => y.gop));
    plain('Management Fee', years.map(y => y.mgmtFee));
    if (years.some(y => y.fixedTotal != null)) {
      plain('Property Taxes', years.map(y => y.fixedPropertyTaxes));
      plain('Insurance', years.map(y => y.fixedInsurance));
      plain('Equipment Lease / Rent', years.map(y => y.fixedRent));
      plain('Other Fixed Charges', years.map(y => y.fixedOther));
      plain('Total Fixed Charges', years.map(y => y.fixedTotal));
    }
    // FON-59 #1 / FON-67 #2 — name the basis. `noiInstitutional` is
    // `noi_institutional ?? noi`; a pre-upgrade run with neither field
    // confirmed is an after-reserve number and must not claim otherwise.
    plain(
      noiBeforeReserveLabel(years.some(y => y.noiBasisConfirmed)),
      years.map(y => y.noiInstitutional),
    );
    plain('FF&E Reserve', years.map(y => y.ffeReserve));
    plain('Net Cash Flow', years.map(y => y.netCashFlow));
    // FON-41 #2 — the Exit Year column's one figure: the forward 12-month Cash
    // NOI the reversion capitalises. Blank on every modelled year, because it
    // is not one of them.
    rows.push([
      FORWARD_NOI_LABEL,
      ...years.flatMap(() => ['', '', '', ''] as XlsxCell[]),
      terminalNoi != null ? Number(terminalNoi.toFixed(0)) : '',
      '', '', '',
    ]);

    await downloadXlsx(`projections-${dealId || 'deal'}`, [
      { name: 'Projections', rows },
    ]);
    toast('Projections exported', { type: 'success' });
  };

  // The "Show" control trims the columns RENDERED (base year + N forecast
  // years), capped at the engine-provided years. It is a view filter, not an
  // assumption — the modelled horizon is `hold_years` (the Projection period).
  const forecastCount = Math.max(1, years.length - 1);
  const shownForecast = projYearsSel == null ? forecastCount : Math.max(1, Math.min(projYearsSel, forecastCount));
  const visibleYears = years.slice(0, shownForecast + 1);

  return (
    <Card className="p-0 overflow-hidden">
      {/* Header */}
      <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border bg-bg/40">
        <div>
          <div className="text-[10.5px] uppercase tracking-[0.12em] text-ink-500 font-semibold">
            Preliminary Hotel Underwriting
          </div>
          <h3 className="text-[15px] font-semibold text-ink-900 mt-0.5">
            Proforma Projections
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
              title="field_overrides.noi_override_by_year pins the operating NOI path used by the Debt and Returns engines to an analyst-entered schedule (FON-67 reconciliation lever). Clear it to let NOI follow the operating assumptions again."
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
          <Button variant="secondary" size="sm" onClick={onExport}>
            <Download size={11} /> Export
          </Button>
        </div>
      </div>

      <ProjectionsControls
        dealId={dealId}
        years={years}
        closeDateIso={closeDateIso}
        // The projection period the editor compares Save against. Falls back to
        // the rendered horizon, which IS hold_years (the revenue engine emits
        // one row per modelled year) — never a guess.
        holdYears={holdYearsAssumption ?? years.length}
        onSaveHoldYears={(v, note) => applyOverride(HOLD_YEARS_KEY, v, note)}
        running={overrideCtx.running}
        shownForecast={shownForecast}
        forecastCount={forecastCount}
        onDec={() => setProjYearsSel(Math.max(1, shownForecast - 1))}
        onInc={() => setProjYearsSel(Math.min(forecastCount, shownForecast + 1))}
      />

      <AssumptionsPanel
        dealId={dealId}
        overrides={overrides}
        onApply={applyOverride}
        running={overrideCtx.running}
        stabilization={stabilization}
        modelYears={years.length}
        calendarYears={calendarYears ?? null}
      />

      <AssumptionOverrideContext.Provider value={overrideCtx}>
        <ProjectionsTable
          years={visibleYears}
          exitCapRate={exitCapRate}
          closeDateIso={closeDateIso}
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
      </AssumptionOverrideContext.Provider>
    </Card>
  );
}

// ────────────────────────────────────────────────────────────────────
// Builders
// ────────────────────────────────────────────────────────────────────

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
    const occupiedRooms = Math.round(availableRooms * (r.occupancy ?? 0));
    const totalRevenue = e?.total_revenue ?? r.total_revenue;
    // OOD revenue is its own USALI line — split it out from the
    // Misc bucket (Sam P4 ask). Resort fees are separate again.
    const otherOperatedRevenue = f?.other_revenue ?? r.other_revenue ?? 0;
    const resortFees = f?.resort_fees ?? 0;
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
      occupancy: r.occupancy ?? 0,
      adr: r.adr ?? 0,
      revpar: r.revpar ?? 0,
      roomsRevenue: f?.rooms_revenue ?? r.rooms_revenue ?? 0,
      fbRevenue: f?.fb_revenue ?? r.fb_revenue ?? 0,
      otherOperatedRevenue,
      resortFees,
      miscRevenue: 0, // Engine output doesn't carry a separate misc line yet.
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

/** Present-ness of the Exit Year column, so every row can close itself out
 *  with one extra cell without prop-drilling through six row components. */
const ExitColumnContext = createContext<boolean>(false);

/** The Exit Year cell for an ordinary row: a dash. Year hold+1 is not modelled
 *  through the expense waterfall, so there is nothing honest to print. */
function ExitCells({ children }: { children?: ReactNode }) {
  const present = useContext(ExitColumnContext);
  if (!present) return null;
  return (
    <td
      colSpan={4}
      className="px-2 py-2 text-center text-[11px] text-ink-400 tabular-nums border-l-2 border-border"
    >
      {children ?? '—'}
    </td>
  );
}

function ProjectionsTable({
  years, exitCapRate, closeDateIso, exitColumn, stabilizedYearIndex,
}: {
  years: ProjYear[];
  exitCapRate: number;
  closeDateIso: string | null;
  exitColumn: ExitColumnSpec | null;
  /** 0-based index of the published stabilized year — badges that column. */
  stabilizedYearIndex?: number;
}) {
  // Hotel Delivery — the deal's acquisition close date, or a dash. It used to
  // be `'9/30/' + years[0].year`, which printed "9/30/1" because `year` is an
  // ordinal: a fabricated date under the no-invented-numbers rule (FON-41 #2).
  const hotelDelivery = fmtIsoDate(closeDateIso);
  const hasExit = exitColumn != null;
  // Total year columns = every modelled year + the Exit Year (hold_years + 1).
  const columnCount = years.length + (hasExit ? 1 : 0);

  // The forward statement below Total Revenue only renders when the expense
  // engine emitted its waterfall (real worker runs). Demo / revenue-only
  // output leaves these undefined, so the table stays topline-only — same
  // gate the xlsx export uses.
  const hasExpenseDetail = years.some((y) => y.deptTotalExpense != null);

  // Annual RevPAR growth — Y0 N/A, Y1+ vs prior.
  const revparGrowth = years.map((y, i) => {
    if (i === 0) return null;
    const prev = years[i - 1].revpar;
    return prev > 0 ? (y.revpar - prev) / prev : null;
  });

  // Helpers for sub-columns.
  const fmtAmount = (v: number, opts?: { decimals?: number; prefix?: string }) => {
    const decimals = opts?.decimals ?? 0;
    const prefix = opts?.prefix ?? '';
    return `${prefix}${v.toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })}`;
  };
  const par = (v: number, available: number) =>
    available > 0 ? (v / available) * 1000 : 0;
  const por = (v: number, occupied: number) =>
    occupied > 0 ? (v / occupied) * 1000 : 0;
  const pctRev = (v: number, total: number) =>
    total > 0 ? (v / total) * 100 : 0;

  // Index column helper labels (growth-rate references shown for Year 1).
  const indexLabel = (key: string): string => {
    if (key === 'occupancy') return `${(years[0].occupancy * 100).toFixed(1)}%`;
    if (key === 'adr') return 'Growth';
    if (key === 'revpar') return '';
    if (key === 'rooms') return 'Mkt';
    if (key === 'fb') return 'Mkt';
    if (key === 'misc') return 'Mkt';
    return '';
  };

  return (
    <ExitColumnContext.Provider value={hasExit}>
    <div className="overflow-x-auto">
      <table className="w-full text-[11.5px] min-w-[1100px] border-collapse">
        <thead>
          {/* Top header row — BASE YEAR / YEAR N */}
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
                colSpan={4}
                className={cn(
                  'text-center text-[10.5px] font-semibold uppercase tracking-wider px-2 pt-2 pb-0',
                  i === 0 ? 'bg-ink-300/10 text-ink-700' : 'bg-brand-50/40 text-brand-700',
                  'border-l border-border',
                )}
              >
                {projectionColumnLabel(i)}
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
                colSpan={4}
                title="Display-only. Year hold+1 is not run through the expense waterfall — it is the forward NOI the reversion is valued on."
                className="text-center text-[10.5px] font-semibold uppercase tracking-wider px-2 pt-2 pb-0 bg-ink-300/10 text-ink-700 border-l-2 border-border"
              >
                {EXIT_COLUMN_LABEL}
              </th>
            )}
          </tr>
          {/* Subtitle — the CALENDAR year, or an em dash. Never the ordinal:
              printing `y.year` here is what produced "Base Year 1 / Year 1 2". */}
          <tr className="border-b border-border">
            {years.map((y, i) => (
              <th
                key={`ys-${i}`}
                colSpan={4}
                className={cn(
                  'text-center text-[11px] font-semibold tabular-nums px-2 pb-1',
                  i === 0 ? 'bg-ink-300/10 text-ink-900' : 'bg-brand-50/40 text-ink-900',
                  y.calendarYear == null && 'text-ink-400',
                  'border-l border-border',
                )}
                title={
                  y.calendarYear == null
                    ? 'No acquisition close date on this deal, so the projection has no calendar year.'
                    : undefined
                }
              >
                {projectionColumnSubtitle(y.calendarYear)}
              </th>
            ))}
            {hasExit && (
              <th
                key="ys-exit"
                colSpan={4}
                className={cn(
                  'text-center text-[11px] font-semibold tabular-nums px-2 pb-1 bg-ink-300/10 border-l-2 border-border',
                  exitColumn?.calendarYear == null ? 'text-ink-400' : 'text-ink-900',
                )}
              >
                {projectionColumnSubtitle(exitColumn?.calendarYear)}
              </th>
            )}
          </tr>
          {/* Sub-column headers */}
          <tr className="border-b border-border text-[9.5px] uppercase tracking-wider text-ink-500">
            {years.map((_, i) => (
              <SubHeaderGroup key={`sh-${i}`} dim={i === 0} />
            ))}
            {hasExit && <SubHeaderGroup key="sh-exit" dim />}
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
                colSpan={4}
                className="px-2 py-2 text-center text-[11px] text-ink-400 border-l border-border"
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
          />

          {/* Number of Rooms */}
          <SimpleRow
            label="Number of Rooms"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.rooms}
            fmt={(v) => v.toLocaleString()}
          />

          {/* Available Rooms */}
          <SimpleRow
            label="Available Rooms"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.availableRooms}
            fmt={(v) => v.toLocaleString()}
          />

          {/* Occupied Rooms */}
          <SimpleRow
            label="Occupied Rooms"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.occupiedRooms}
            fmt={(v) => v.toLocaleString()}
          />

          {/* Occupancy — base-year driver grounded in the T-12/historical
              actual (starting_occupancy); later years grow at occupancy_growth. */}
          <SimpleRow
            label="Occupancy"
            indexLabel={indexLabel('occupancy')}
            unit="%"
            years={years}
            value={(y) => y.occupancy * 100}
            fmt={(v) => `${v.toFixed(1)}%`}
            sourceKey="starting_occupancy"
            overrideKey="starting_occupancy"
            overrideUnit="pct"
          />

          {/* Average Rate (ADR) — base-year driver grounded in the T-12/detailed
              P&L actual (starting_adr); later years grow at adr_growth. */}
          <SimpleRow
            label="Average Rate"
            indexLabel={indexLabel('adr')}
            unit="$"
            years={years}
            value={(y) => y.adr}
            fmt={(v) => `$${v.toFixed(2)}`}
            sourceKey="starting_adr"
            overrideKey="starting_adr"
            overrideUnit="dollar"
          />

          {/* RevPAR — derived, not sourced: Occupancy × ADR. */}
          <SimpleRow
            label="RevPAR"
            indexLabel={indexLabel('revpar')}
            unit="$"
            years={years}
            value={(y) => y.revpar}
            fmt={(v) => `$${v.toFixed(2)}`}
            computedNote="Calculated: RevPAR = Occupancy × ADR"
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
                  colSpan={4}
                  className={cn(
                    'px-2 py-2 text-center text-[11px] tabular-nums border-l border-border',
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
          <tr className="bg-brand-500/95">
            <td
              colSpan={2 + columnCount * 4}
              className="px-3 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-white"
            >
              Revenues
            </td>
          </tr>

          {/* Rooms */}
          <FullRow
            label="Rooms"
            indexLabel={indexLabel('rooms')}
            unit="$"
            years={years}
            amountOf={(y) => y.roomsRevenue}
            fmtAmount={fmtAmount}
            pctRev={pctRev}
            par={par}
            por={por}
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
            fmtAmount={fmtAmount}
            pctRev={pctRev}
            par={par}
            por={por}
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
            fmtAmount={fmtAmount}
            pctRev={pctRev}
            par={par}
            por={por}
          />
          {/* Resort Fees — distinct from rooms and from OOD. Hidden
              when zero across every year (most deals don't carry them). */}
          {years.some((y) => y.resortFees > 0) && (
            <FullRow
              label="Resort Fees"
              indexLabel={indexLabel('resort_fees')}
              unit="$"
              years={years}
              amountOf={(y) => y.resortFees}
              fmtAmount={fmtAmount}
              pctRev={pctRev}
              par={par}
              por={por}
            />
          )}
          {/* Misc. Income — only renders when present so the table
              stays tight on deals that don't carry the bucket. */}
          {years.some((y) => y.miscRevenue > 0) && (
            <FullRow
              label="Misc. Income"
              indexLabel={indexLabel('misc')}
              unit="$"
              years={years}
              amountOf={(y) => y.miscRevenue}
              fmtAmount={fmtAmount}
              pctRev={pctRev}
              par={par}
              por={por}
            />
          )}
          {/* Total Revenue */}
          <FullRow
            label="Total Revenue"
            indexLabel=""
            unit="$"
            years={years}
            amountOf={(y) => y.totalRevenue}
            fmtAmount={fmtAmount}
            pctRev={pctRev}
            par={par}
            por={por}
            bold
            traceEngine="revenue"
            tracePath={(i) => `years[${i}].total_revenue`}
          />

          {/* ── Forward statement below Total Revenue (canonical Projections
              statement: Financials Tab.dc.html → projDefs). Every figure comes
              from the expense engine (apps/worker/app/engines/expense.py) that
              ProjYear already carries; subtotals foot to the visible rows. ── */}
          {hasExpenseDetail && (
            <>
              {/* DEPARTMENTAL EXPENSE */}
              <tr className="bg-brand-500/95">
                <td
                  colSpan={2 + columnCount * 4}
                  className="px-3 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-white"
                >
                  Departmental Expense
                </td>
              </tr>
              <FullRow
                label="Rooms"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.deptRoomsExpense ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
              />
              <FullRow
                label="Food & Beverage"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.deptFbExpense ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
              />
              <FullRow
                label="Other Operated Departments"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.deptOtherExpense ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
              />
              {/* Total Departmental Expense = Σ departmental expense lines
                  (worker dept_expenses.total). */}
              <FullRow
                label="Total Departmental Expense"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.deptTotalExpense ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
                bold
              />
              {/* Total Departmental Profit = Total Revenue − Total Departmental
                  Expense. */}
              <FullRow
                label="Total Departmental Profit"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.totalRevenue - (y.deptTotalExpense ?? 0)}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
                bold
              />

              {/* UNDISTRIBUTED EXPENSES */}
              <tr className="bg-brand-500/95">
                <td
                  colSpan={2 + columnCount * 4}
                  className="px-3 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-white"
                >
                  Undistributed Expenses
                </td>
              </tr>
              <FullRow
                label="Administrative & General"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistAdminGeneral ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
              />
              <FullRow
                label="Information & Telecom Systems"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistInfoTelecom ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
              />
              <FullRow
                label="Sales & Marketing"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistSalesMarketing ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
              />
              <FullRow
                label="Property Operation & Maintenance"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistPropertyOps ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
              />
              <FullRow
                label="Utilities"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistUtilities ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
              />
              {/* Total Undistributed Expenses = Σ undistributed lines
                  (worker undistributed.total). */}
              <FullRow
                label="Total Undistributed Expenses"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.undistTotal ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
                bold
              />
              {/* Gross Operating Profit = Total Departmental Profit − Total
                  Undistributed Expenses (worker gop). Traced to the engine. */}
              <FullRow
                label="Gross Operating Profit"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.gop ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
                bold
                traceEngine="expense"
                tracePath={(i) => `years[${i}].gop`}
              />
              {/* Management Fees — % of total revenue (worker mgmt_fee). */}
              <FullRow
                label="Management Fees"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => y.mgmtFee ?? 0}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
              />
              {/* EBITDA = Gross Operating Profit − Management Fees (canonical
                  Projections definition; foots to the two rows above). */}
              <FullRow
                label="EBITDA"
                indexLabel=""
                unit="$"
                years={years}
                amountOf={(y) => (y.gop ?? 0) - (y.mgmtFee ?? 0)}
                fmtAmount={fmtAmount}
                pctRev={pctRev}
                par={par}
                por={por}
                bold
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
                  const ebitda = (y.gop ?? 0) - (y.mgmtFee ?? 0);
                  const exitVal = exitCapRate > 0 ? ebitda / exitCapRate : 0;
                  return (
                    <td
                      key={`ev-${i}`}
                      colSpan={4}
                      title={`Implied Exit Value = EBITDA ÷ exit cap rate (${(exitCapRate * 100).toFixed(1)}%)`}
                      className="px-2 py-2 text-center text-[11px] text-ink-900 font-semibold tabular-nums border-l border-border"
                    >
                      {exitCapRate > 0 ? fmtAmount(exitVal, { prefix: '$' }) : '—'}
                    </td>
                  );
                })}
                <ExitCells />
              </tr>
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
                  colSpan={4}
                  className="px-2 py-2 text-center text-[11px] text-ink-400 border-l border-border"
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
      <div className="px-5 py-3 border-t border-border text-[11px] text-ink-500 flex items-center gap-1.5">
        <FileText size={11} />
        PAR = $/available room. POR = $/occupied room. % Rev = share of Total Revenue.
      </div>
    </div>
    </ExitColumnContext.Provider>
  );
}

// Sub-column header group: Amount | % Rev | PAR | POR.
function SubHeaderGroup({ dim }: { dim: boolean }) {
  const cls = cn(
    'px-2 py-1.5 text-right font-semibold border-l border-border',
    dim ? 'bg-ink-300/10' : 'bg-brand-50/40',
  );
  return (
    <>
      <th className={cls}>Amount</th>
      <th className={cls}>% Rev</th>
      <th className={cls}>PAR</th>
      <th className={cls}>POR</th>
    </>
  );
}

// Simple single-cell row (Days, Rooms, Occupancy, ADR, etc.) — value
// is rendered once per year, spanning all 4 sub-columns.
// FON-27: shared context so the deep-nested driver cells can persist an
// override + re-run without prop-drilling through ProjectionsTable/SimpleRow.
interface OverrideCtx {
  overrides: Record<string, unknown>;
  apply: (key: string, value: number, note: string) => Promise<void>;
  reset: (key: string) => Promise<void>;
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
}: {
  sourceKey: string;
  overrideKey: string;
  label: string;
  display: ReactNode;
  editValue: number;
  unit: 'pct' | 'dollar';
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
      await ctx.apply(overrideKey, next, justification);
      setOpen(false);
    } finally {
      setSaving(false);
    }
  };
  const reset = async () => {
    setSaving(true);
    try {
      await ctx.reset(overrideKey);
      setOpen(false);
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
}: {
  label: string;
  indexLabel: string;
  unit: string;
  years: ProjYear[];
  value: (y: ProjYear) => number;
  fmt: (v: number) => string;
  // FON-27 provenance (base-year anchor only): a source key + optional
  // override key (→ click-open source/override panel), OR a derived-value
  // note (→ "calculated" hint). Later years are grown off the base, so
  // attributing them to the same source would misread.
  sourceKey?: string;
  overrideKey?: string;
  overrideUnit?: 'pct' | 'dollar';
  computedNote?: string;
}) {
  return (
    <tr className="border-b border-border/60 hover:bg-ink-300/5">
      <td className="px-3 py-2 text-[11px] text-ink-700 font-medium border-r border-border bg-bg/30">
        {label}
      </td>
      <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30">
        {indexLabel || unit}
      </td>
      {years.map((y, i) => {
        const shown = fmt(value(y));
        const cell =
          i === 0 && sourceKey && overrideKey ? (
            <AssumptionCell
              sourceKey={sourceKey}
              overrideKey={overrideKey}
              unit={overrideUnit ?? 'dollar'}
              label={label}
              display={shown}
              editValue={value(y)}
            />
          ) : i === 0 && sourceKey ? (
            <DriverValue sourceKey={sourceKey}>{shown}</DriverValue>
          ) : i === 0 && computedNote ? (
            <ComputedValue note={computedNote}>{shown}</ComputedValue>
          ) : (
            shown
          );
        return (
          <td
            key={`sr-${i}`}
            colSpan={4}
            className="px-2 py-2 text-center text-[11px] text-ink-900 tabular-nums border-l border-border"
          >
            {cell}
          </td>
        );
      })}
      <ExitCells />
    </tr>
  );
}

// Full Amount/%Rev/PAR/POR row — used for revenue lines.
function FullRow({
  label,
  indexLabel,
  unit,
  years,
  amountOf,
  fmtAmount,
  pctRev,
  par,
  por,
  bold = false,
  traceEngine,
  tracePath,
}: {
  label: string;
  indexLabel: string;
  unit: string;
  years: ProjYear[];
  amountOf: (y: ProjYear) => number;
  fmtAmount: (v: number, opts?: { decimals?: number; prefix?: string }) => string;
  pctRev: (v: number, total: number) => number;
  par: (v: number, available: number) => number;
  por: (v: number, occupied: number) => number;
  bold?: boolean;
  // Provenance: when set, the Amount cell for column i is wrapped in
  // <Traced> so hovering shows the formula behind the computed value.
  // `tracePath(i)` maps the column index to the engine's dotted output
  // path (ProjYear[i] ↔ engine years[i], 1:1 from buildFromWorker).
  traceEngine?: string;
  tracePath?: (i: number) => string;
}) {
  return (
    <tr
      className={cn(
        'border-b border-border/60 hover:bg-ink-300/5',
        bold && 'bg-brand-50/30 font-semibold',
      )}
    >
      <td
        className={cn(
          'px-3 py-2 text-[11px] border-r border-border bg-bg/30',
          bold ? 'text-ink-900 font-semibold' : 'text-ink-700 font-medium',
        )}
      >
        {label}
      </td>
      <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30">
        {indexLabel || unit}
      </td>
      {years.map((y, i) => {
        const amt = amountOf(y);
        const tracedAmount =
          traceEngine && tracePath ? (
            <Traced engine={traceEngine} path={tracePath(i)}>
              {fmtAmount(amt, { prefix: '$' })}
            </Traced>
          ) : undefined;
        return (
          <SubCells
            key={`fr-${label}-${i}`}
            amount={amt}
            pctRev={pctRev(amt, y.totalRevenue)}
            par={par(amt, y.availableRooms)}
            por={por(amt, y.occupiedRooms)}
            fmtAmount={fmtAmount}
            tracedAmount={tracedAmount}
          />
        );
      })}
      <ExitCells />
    </tr>
  );
}

function SubCells({
  amount,
  pctRev,
  par,
  por,
  fmtAmount,
  tracedAmount,
}: {
  amount: number;
  pctRev: number;
  par: number;
  por: number;
  fmtAmount: (v: number, opts?: { decimals?: number; prefix?: string }) => string;
  /** Provenance-wrapped Amount cell content; falls back to plain text. */
  tracedAmount?: ReactNode;
}) {
  const td = 'px-2 py-2 text-right text-[11px] text-ink-900 tabular-nums border-l border-border';
  return (
    <>
      <td className={td}>{tracedAmount ?? fmtAmount(amount, { prefix: '$' })}</td>
      <td className={cn(td, 'text-ink-500')}>{pctRev > 0 ? `${pctRev.toFixed(1)}%` : '—'}</td>
      <td className={cn(td, 'text-ink-700')}>{par > 0 ? fmtAmount(par, { decimals: 0, prefix: '$' }) : '—'}</td>
      <td className={cn(td, 'text-ink-700')}>{por > 0 ? fmtAmount(por, { decimals: 0, prefix: '$' }) : '—'}</td>
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
  shownForecast, forecastCount, onDec, onInc,
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
    ? `Base Year (Year 1) is calendar ${baseYear} — derived from the acquisition close date${closeDateIso ? ` (${fmtIsoDate(closeDateIso)})` : ''}, whose first operating month starts the projection. It is not an input here; edit the Acquisition Date on the Investment tab.`
    : 'No acquisition close date on this deal, so the projection has no calendar year. The base year is derived from that date — set the Acquisition Date on the Investment tab.';
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
          title="The acquisition close date — the only input the base year is derived from — is owned by the Investment tab"
          style={ownerLink}
        >
          Investment →
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

      {/* What replaced the dead Annual / Monthly toggle: the basis, stated. */}
      <span
        data-testid="projection-basis-note"
        title="The revenue and expense engines project 365-day annual periods (apps/worker/app/engines/revenue.py, DAYS_PER_YEAR), so this statement has no monthly series to show. Monthly detail exists for the debt schedule on the Debt tab."
        style={{
          fontSize: 11, color: '#6b6f76', cursor: 'help',
          textDecoration: 'underline dotted', textUnderlineOffset: 3,
        }}
      >
        Annual periods
      </span>
    </div>
  );
}

// One editable assumption row: label + right-aligned numeric input with affixes.
// Commits on blur / Enter; pct fields store as a fraction (value/100).
function AssumptionField({
  label, value, unit, prefix, suffix, onCommit, disabled, overrideKey,
}: {
  label: string;
  value: number;
  unit: 'pct' | 'dollar';
  prefix?: string;
  suffix?: string;
  onCommit: (engineValue: number, note: string) => void;
  disabled?: boolean;
  /** FON-74 — the `field_overrides` key; drives whether Save needs a reason. */
  overrideKey: string;
}) {
  const display = unit === 'pct' ? value * 100 : value;
  const fmt = (n: number) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10));
  const [draft, setDraft] = useState<string>(fmt(display));
  // FON-74 — a real change parks here until the analyst justifies it. null =
  // nothing pending; a number = the engine value waiting on a reason.
  const [pending, setPending] = useState<number | null>(null);
  const [note, setNote] = useState('');
  const requireNote = requiresNote(overrideKey);
  useEffect(() => { setDraft(fmt(display)); }, [display]);
  const revert = () => { setDraft(fmt(display)); setPending(null); setNote(''); };
  const commit = () => {
    const n = Number(draft.replace(/[$,%\s]/g, ''));
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
  dealId, overrides, onApply, running, stabilization, modelYears, calendarYears,
}: {
  dealId: string;
  overrides: Record<string, unknown>;
  /** FON-74 — `note` is the ANALYST's justification, or '' on an exempt key. */
  onApply: (key: string, value: number, note: string) => Promise<void>;
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
              onCommit={(year) => onApply('stabilization_year', year, '')}
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
            <AssumptionField label="Capture — Base Year (Year 1)" unit="pct" suffix="%" overrideKey="resort_fee_capture_y1" value={cur('resort_fee_capture_y1')} disabled={running} onCommit={(v, note) => onApply('resort_fee_capture_y1', v, note)} />
            <AssumptionField label="Capture — Year 2" unit="pct" suffix="%" overrideKey="resort_fee_capture_y2" value={cur('resort_fee_capture_y2')} disabled={running} onCommit={(v, note) => onApply('resort_fee_capture_y2', v, note)} />
            <AssumptionField label="Capture — Year 3+" unit="pct" suffix="%" overrideKey="resort_fee_capture_y3" value={cur('resort_fee_capture_y3')} disabled={running} onCommit={(v, note) => onApply('resort_fee_capture_y3', v, note)} />
            <p style={{ fontSize: 11, color: '#6b6f76', lineHeight: 1.45, margin: 0 }}>
              Each capture applies to the column above it — Base Year (Year 1) is the model&apos;s
              first operating year, Year 2 is the one after it, and Year 3+ carries through every
              later year.
            </p>
          </div>
        </div>
        {/* Deal economics */}
        <div style={cardStyle}>
          <div style={cardTitle}>Deal economics</div>
          <div style={rowsWrap}>
            <AssumptionField label="Management fee" unit="pct" suffix="% of rev" overrideKey="mgmt_fee_pct" value={cur('mgmt_fee_pct')} disabled={running} onCommit={(v, note) => onApply('mgmt_fee_pct', v, note)} />
            {/* Exit cap rate is Investment-owned — linked / read-only reference. */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
              <span style={{ fontSize: 12, color: '#6b6f76' }}>Exit cap rate</span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 13, fontWeight: 600, color: '#1a2233', fontVariantNumeric: 'tabular-nums' }}>
                  <ProvenanceDot state="linked" size={8} title="Linked from the Investment tab" />
                  {(exitCapFraction * 100).toFixed(1)}%
                </span>
                <Link
                  href={`/projects/${dealId}?tab=investment`}
                  title="Exit cap rate is owned by the Investment tab"
                  style={{ fontSize: 10.5, color: '#2f4a8c', textDecoration: 'none', whiteSpace: 'nowrap' }}
                >
                  Investment →
                </Link>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
