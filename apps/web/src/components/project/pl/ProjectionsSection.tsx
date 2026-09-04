'use client';
/**
 * ProjectionsSection — Lovable-parity multi-year proforma projections.
 *
 * Renders the "PRELIMINARY HOTEL UNDERWRITING / Proforma Projections"
 * table with a Base Year + 5 forecast year span. Each year column
 * shows Amount / % Rev / PAR / POR sub-columns. Rows include hotel
 * delivery, days, room counts, occupancy, ADR, RevPAR + RevPAR growth,
 * and the REVENUES section (Rooms / F&B / Other / Total).
 *
 * Sources:
 *  - Worker: ``revenue.years`` + ``fb.years`` + ``expense.years`` via
 *    ``useEngineOutputs``. Year 0 (Base) = first engine year, treated
 *    as the T-12 anchor; Years 1-5 = engine years[0..4].
 *
 * Helpers (mirroring Historicals):
 *   PAR  = Amount / Available Rooms × 1000
 *   POR  = Amount / Occupied Rooms  × 1000
 *   %Rev = Amount / Total Revenue   × 100
 */

import { useMemo, useState, useEffect, useCallback, useContext, createContext, type ReactNode, type CSSProperties } from 'react';
import Link from 'next/link';
import { Sparkles, Download, FileText, ExternalLink } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { ProvenanceDot } from '@/components/design';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import Modal from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/format';
import { Traced } from '@/components/help/Traced';
import { Sourced } from '@/components/help/Sourced';
import { useSource } from '@/lib/hooks/useDealProvenance';
import { sourceKind, sourceLabel, sourceExplanation, KIND_TONE } from '@/lib/provenance';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useDeal } from '@/lib/hooks/useDeal';
import {
  api,
  isWorkerConnected,
  workerUrl,
  WorkerError,
  type AskAnswerResult,
} from '@/lib/api';
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
  year: number;
  // Available Rooms = keys × days (in days for the year).
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
  ffeReserve?: number;
  // Net cash flow after FF&E reserve = NOI - FF&E.
  netCashFlow?: number;
}

const isLeap = (y: number) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;

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
        await api.deals.update(dealId, { field_overrides: { ...overrides, [key]: { value, note } } });
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
  const hasWorker =
    Array.isArray(revenueYears) && revenueYears.length > 0 &&
    Array.isArray(expenseYears) && expenseYears.length > 0;

  const years = useMemo<ProjYear[] | null>(() => {
    if (hasWorker && keys > 0) {
      return buildFromWorker(revenueYears!, fbYears ?? null, expenseYears!, keys);
    }
    return null;
  }, [hasWorker, revenueYears, fbYears, expenseYears, keys]);

  // ── AI NOI Summary modal ────────────────────────────────────────
  // Hits the worker's grounded Q&A endpoint (`/deals/{id}/ask`),
  // which returns answer + per-fact citations back to source PDF
  // pages. The fixed prompt frames the question around the projection
  // years rendered in this table — keeps the answer on-topic for the
  // P&L tab without dragging in unrelated assumptions.
  //
  // CRITICAL: these hooks MUST be declared BEFORE the early-return
  // empty-state guard below. React's Rules of Hooks require the same
  // hook count on every render; placing them after the guard caused
  // React error #310 when `years` flipped from null → populated
  // between renders (2026-05-12 prod crash on the P&L tab).
  const [noiModalOpen, setNoiModalOpen] = useState(false);
  const [noiLoading, setNoiLoading] = useState(false);
  const [noiResult, setNoiResult] = useState<AskAnswerResult | null>(null);
  const [noiError, setNoiError] = useState<string | null>(null);

  // Canonical Projections view controls. Period trims the forecast horizon
  // shown in the table (real behaviour, capped at the engine-provided years);
  // Base year + View are the design's chrome — the projection horizon and the
  // Monthly breakout come from the engine, so they are presentational until a
  // base-year override + monthly series are wired through (flagged follow-up).
  const [projYearsSel, setProjYearsSel] = useState<number | null>(null);
  const [projView, setProjView] = useState<'annual' | 'monthly'>('annual');

  const noiQuestion = useMemo(() => {
    if (!years || years.length === 0) {
      return 'Summarize the deal NOI trajectory across the projection horizon.';
    }
    const span = years.length - 1;
    const baseYear = years[0]?.year;
    const exitYear = years[years.length - 1]?.year;
    const revSeries = years
      .map(
        (y) =>
          `Year ${y.year}: Occ ${(y.occupancy * 100).toFixed(1)}%, ADR $${y.adr.toFixed(0)}, Rev $${y.totalRevenue.toLocaleString()}`,
      )
      .join('; ');
    return [
      `Summarize the NOI trajectory across this ${span}-year projection`,
      `(${baseYear} → ${exitYear}). Underlying revenue series: ${revSeries}.`,
      "Cover: (1) what's driving Year-1 NOI vs the broker proforma,",
      '(2) the key revenue / expense levers in the ramp years,',
      '(3) the terminal Year NOI vs entry, and (4) the top two risks',
      'that would compress NOI below this trajectory. Cite source pages',
      'when grounded.',
    ].join(' ');
  }, [years]);

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

  const onNoiSummary = async () => {
    if (!isWorkerConnected() || !dealId || /^\d+$/.test(dealId)) {
      toast('AI NOI Summary needs a live deal — try the demo deal.', {
        type: 'info',
      });
      return;
    }
    setNoiModalOpen(true);
    setNoiLoading(true);
    setNoiError(null);
    setNoiResult(null);
    try {
      const res = await api.dossier.ask(dealId, noiQuestion);
      setNoiResult(res);
    } catch (err) {
      const detail = err instanceof WorkerError ? err.body : String(err);
      setNoiError(detail || 'Worker rejected the request.');
    } finally {
      setNoiLoading(false);
    }
  };

  const onExport = async () => {
    const headers: XlsxCell[] = [
      'Metric',
      ...years.flatMap((y, i) => {
        const label = i === 0 ? `Base Year ${y.year}` : `Year ${i} ${y.year}`;
        return [
          `${label} Amount`,
          `${label} % Rev`,
          `${label} PAR`,
          `${label} POR`,
        ];
      }),
    ];
    const rows: XlsxCell[][] = [headers];
    const trMap = years.map(y => y.totalRevenue);
    const arMap = years.map(y => y.availableRooms);
    const orMap = years.map(y => y.occupiedRooms);
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
      ]);
    };

    rows.push(['Days', ...years.flatMap(y => [y.days, '', '', '']) as XlsxCell[]]);
    rows.push(['Number of Rooms', ...years.flatMap(y => [y.rooms, '', '', '']) as XlsxCell[]]);
    rows.push(['Available Rooms', ...years.flatMap(y => [y.availableRooms, '', '', '']) as XlsxCell[]]);
    rows.push(['Occupied Rooms', ...years.flatMap(y => [y.occupiedRooms, '', '', '']) as XlsxCell[]]);
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
    plain('Net Operating Income (NOI)', years.map(y => y.noiInstitutional));
    plain('FF&E Reserve', years.map(y => y.ffeReserve));
    plain('Net Cash Flow', years.map(y => y.netCashFlow));

    await downloadXlsx(`projections-${dealId || 'deal'}`, [
      { name: 'Projections', rows },
    ]);
    toast('Projections exported', { type: 'success' });
  };

  // Period control trims the forecast horizon shown (base year + N forecast
  // years), capped at the engine-provided years.
  const forecastCount = Math.max(1, years.length - 1);
  const shownForecast = projYearsSel == null ? forecastCount : Math.max(1, Math.min(projYearsSel, forecastCount));
  const visibleYears = years.slice(0, shownForecast + 1);

  return (
    <>
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
            <Button variant="secondary" size="sm" onClick={onNoiSummary}>
              <Sparkles size={11} />
              NOI Summary
              <Badge tone="blue" className="ml-1 !py-0 !px-1.5 !text-[9px]">AI</Badge>
            </Button>
            <Button variant="secondary" size="sm" onClick={onExport}>
              <Download size={11} /> Export
            </Button>
          </div>
        </div>

        <ProjectionsControls
          years={years}
          shownForecast={shownForecast}
          forecastCount={forecastCount}
          onDec={() => setProjYearsSel(Math.max(1, shownForecast - 1))}
          onInc={() => setProjYearsSel(Math.min(forecastCount, shownForecast + 1))}
          view={projView}
          onView={setProjView}
        />

        <AssumptionsPanel
          dealId={dealId}
          overrides={overrides}
          onApply={applyOverride}
          running={overrideCtx.running}
        />

        <AssumptionOverrideContext.Provider value={overrideCtx}>
          <ProjectionsTable years={visibleYears} exitCapRate={exitCapRate} />
        </AssumptionOverrideContext.Provider>
      </Card>

      <Modal
        open={noiModalOpen}
        onClose={() => setNoiModalOpen(false)}
        title="AI NOI Summary"
        maxWidth="max-w-2xl"
      >
        <div className="px-5 py-4 space-y-3">
          {noiLoading && (
            <div className="text-[12.5px] text-ink-500 py-6 text-center">
              <Sparkles className="inline-block w-3.5 h-3.5 mr-1.5 animate-pulse text-brand-500" />
              Synthesizing NOI summary from extracted deal data…
            </div>
          )}

          {noiError && (
            <div className="text-[12.5px] text-error-700 bg-error-50 border border-error-200 rounded p-3">
              <div className="font-semibold mb-1">Couldn&apos;t generate summary</div>
              <div className="text-error-600">{noiError}</div>
            </div>
          )}

          {noiResult && !noiLoading && (
            <>
              <div className="text-[12px] text-ink-500 leading-relaxed border-l-2 border-brand-200 pl-3 italic">
                {noiQuestion}
              </div>
              <div className="text-[13px] text-ink-900 leading-relaxed whitespace-pre-wrap">
                {noiResult.answer}
              </div>
              {noiResult.confidence != null && (
                <div className="text-[11px] text-ink-500">
                  Model confidence: {(noiResult.confidence * 100).toFixed(0)}%
                  {noiResult.note && ` · ${noiResult.note}`}
                </div>
              )}
              {noiResult.citations && noiResult.citations.length > 0 && (
                <div className="border-t border-border pt-3">
                  <div className="text-[10.5px] uppercase tracking-wide text-ink-500 font-semibold mb-2">
                    Citations
                  </div>
                  <ul className="space-y-1.5 text-[11.5px] text-ink-700">
                    {noiResult.citations.map((c, i) => {
                      const href =
                        c.document_id && c.page
                          ? `${workerUrl()}/deals/${dealId}/documents/${c.document_id}/download#page=${c.page}`
                          : null;
                      return (
                        <li key={`citation-${i}`} className="flex items-start gap-1.5">
                          <FileText size={11} className="mt-0.5 text-ink-400 shrink-0" />
                          <span>
                            {href ? (
                              <a
                                href={href}
                                target="_blank"
                                rel="noreferrer"
                                className="hover:underline inline-flex items-center gap-0.5"
                              >
                                {c.field ?? 'source'} (page {c.page})
                                <ExternalLink size={10} />
                              </a>
                            ) : (
                              <span className="text-ink-500">
                                {c.field ?? 'source'}
                              </span>
                            )}
                            {c.excerpt && (
                              <span className="block text-[11px] text-ink-500 italic mt-0.5">
                                &ldquo;{c.excerpt.slice(0, 200)}
                                {c.excerpt.length > 200 ? '…' : ''}&rdquo;
                              </span>
                            )}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      </Modal>
    </>
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
): ProjYear[] {
  // Slice up to first 6 entries; if worker only emits 5 forecast years
  // without a base year, we render whatever we have anchored on Y0.
  const span = Math.min(6, revenueYears.length);
  const out: ProjYear[] = [];
  for (let i = 0; i < span; i++) {
    const r = revenueYears[i];
    const f = fbYears?.[i];
    const e = expenseYears[i];
    const days = isLeap(r.year) ? 366 : 365;
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
    const ffe = e?.ffe_reserve;
    const netCashFlow =
      noiInst != null && ffe != null ? noiInst - ffe : undefined;
    out.push({
      year: r.year,
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
      ffeReserve: ffe,
      netCashFlow,
    });
  }
  return out;
}

// ────────────────────────────────────────────────────────────────────
// Table
// ────────────────────────────────────────────────────────────────────

function ProjectionsTable({ years, exitCapRate }: { years: ProjYear[]; exitCapRate: number }) {
  // Hotel Delivery — render the base year period-end as the anchor date.
  const baseYear = years[0]?.year ?? new Date().getFullYear();
  const hotelDelivery = `9/30/${baseYear}`;

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
                {i === 0 ? 'Base Year' : `Year ${i}`}
              </th>
            ))}
          </tr>
          {/* Subtitle — actual years */}
          <tr className="border-b border-border">
            {years.map((y, i) => (
              <th
                key={`ys-${i}`}
                colSpan={4}
                className={cn(
                  'text-center text-[11px] font-semibold tabular-nums px-2 pb-1',
                  i === 0 ? 'bg-ink-300/10 text-ink-900' : 'bg-brand-50/40 text-ink-900',
                  'border-l border-border',
                )}
              >
                {y.year}
              </th>
            ))}
          </tr>
          {/* Sub-column headers */}
          <tr className="border-b border-border text-[9.5px] uppercase tracking-wider text-ink-500">
            {years.map((_, i) => (
              <SubHeaderGroup key={`sh-${i}`} dim={i === 0} />
            ))}
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
          </tr>

          {/* REVENUES section header */}
          <tr className="bg-brand-500/95">
            <td
              colSpan={2 + years.length * 4}
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
                  colSpan={2 + years.length * 4}
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
                  colSpan={2 + years.length * 4}
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
              </tr>
            </>
          )}
        </tbody>
      </table>
      <div className="px-5 py-3 border-t border-border text-[11px] text-ink-500 flex items-center gap-1.5">
        <FileText size={11} />
        PAR = $/available room. POR = $/occupied room. % Rev = share of Total Revenue.
      </div>
    </div>
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
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);

  const overridden = !!ctx && overrideKey in ctx.overrides;
  const src = overridden ? 'analyst_override' : resolved?.source;
  if (!ctx || !src) return <>{display}</>;

  const kind = sourceKind(src);
  const tone = KIND_TONE[kind];
  const decoColor =
    kind === 'grounded' ? 'decoration-success-500'
      : kind === 'override' ? 'decoration-brand-500'
        : 'decoration-warn-500';

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
    setSaving(true);
    try {
      await ctx.apply(overrideKey, unit === 'pct' ? n / 100 : n, note.trim() || 'Overridden on the Projections page');
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
          <span className="fixed inset-0 z-40" onClick={() => setOpen(false)} aria-hidden="true" />
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
                    inputMode="decimal"
                    autoFocus
                    className="w-24 rounded-md border border-border px-2 py-1 text-[12px] tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                  />
                </span>
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Why? (note, optional)"
                  className="w-full rounded-md border border-border px-2 py-1 text-[11px] focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                />
                <span className="flex items-center gap-2">
                  <button type="button" onClick={apply} disabled={saving} className={cn(btn, 'text-white bg-brand-600 hover:bg-brand-700')}>
                    {saving ? 'Applying…' : 'Apply & re-model'}
                  </button>
                  <button type="button" onClick={() => setEditing(false)} className={cn(btn, 'text-ink-500 hover:text-ink-900')}>
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

// Canonical segmented control (#f0efeb track, white active pill).
function SegControl<T extends string>({
  options, value, onChange,
}: {
  options: { id: T; label: string }[];
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
            onClick={() => onChange(o.id)}
            style={{
              padding: '5px 11px', fontSize: 11.5, fontWeight: 600, cursor: 'pointer',
              borderRadius: 5, border: 'none', fontFamily: 'inherit',
              background: active ? '#fff' : 'transparent',
              color: active ? '#1a2233' : '#6b6f76',
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

// The Base year / Period / View control bar above the Assumptions panel.
function ProjectionsControls({
  years, shownForecast, forecastCount, onDec, onInc, view, onView,
}: {
  years: ProjYear[];
  shownForecast: number;
  forecastCount: number;
  onDec: () => void;
  onInc: () => void;
  view: 'annual' | 'monthly';
  onView: (v: 'annual' | 'monthly') => void;
}) {
  const baseYear = years[0]?.year ?? new Date().getFullYear();
  const ctrlLabel: CSSProperties = { fontSize: 11, color: '#6b6f76', fontWeight: 600 };
  const stepBtn: CSSProperties = {
    width: 24, height: 26, border: '1px solid #e2e1dc', background: '#fff',
    borderRadius: 6, cursor: 'pointer', fontSize: 14, color: '#3a3f47', lineHeight: 1,
  };
  return (
    <div style={{ padding: '12px 22px', borderBottom: '1px solid #eee', background: '#fbfbf9', display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={ctrlLabel}>Base year</span>
        <span
          title="Base year follows the earliest engine projection year."
          style={{ fontSize: 12.5, fontWeight: 600, border: '1px solid #e2e1dc', borderRadius: 6, padding: '6px 8px', color: '#1a2233', background: '#fff' }}
        >
          {baseYear}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={ctrlLabel}>Period</span>
        <button type="button" onClick={onDec} disabled={shownForecast <= 1} style={{ ...stepBtn, opacity: shownForecast <= 1 ? 0.5 : 1 }}>−</button>
        <span style={{ fontSize: 12.5, fontWeight: 700, color: '#1a2233', minWidth: 56, textAlign: 'center' }}>{shownForecast} years</span>
        <button type="button" onClick={onInc} disabled={shownForecast >= forecastCount} style={{ ...stepBtn, opacity: shownForecast >= forecastCount ? 0.5 : 1 }}>+</button>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={ctrlLabel}>View</span>
        <SegControl
          options={[{ id: 'annual', label: 'Annual' }, { id: 'monthly', label: 'Monthly' }]}
          value={view}
          onChange={onView}
        />
      </div>
    </div>
  );
}

// One editable assumption row: label + right-aligned numeric input with affixes.
// Commits on blur / Enter; pct fields store as a fraction (value/100).
function AssumptionField({
  label, value, unit, prefix, suffix, onCommit, disabled,
}: {
  label: string;
  value: number;
  unit: 'pct' | 'dollar';
  prefix?: string;
  suffix?: string;
  onCommit: (engineValue: number) => void;
  disabled?: boolean;
}) {
  const display = unit === 'pct' ? value * 100 : value;
  const fmt = (n: number) => (Number.isInteger(n) ? String(n) : String(Math.round(n * 10) / 10));
  const [draft, setDraft] = useState<string>(fmt(display));
  useEffect(() => { setDraft(fmt(display)); }, [display]);
  const commit = () => {
    const n = Number(draft.replace(/[$,%\s]/g, ''));
    if (!Number.isFinite(n)) { setDraft(fmt(display)); return; }
    const eng = unit === 'pct' ? n / 100 : n;
    if (Math.abs(eng - value) < 1e-9) return; // no change — skip the re-run
    onCommit(eng);
  };
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
      <span style={{ fontSize: 12, color: '#6b6f76' }}>{label}</span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
        {prefix && <span style={{ fontSize: 11, color: '#6b6f76' }}>{prefix}</span>}
        <input
          type="number"
          value={draft}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
            if (e.key === 'Escape') { setDraft(fmt(display)); (e.target as HTMLInputElement).blur(); }
          }}
          style={{ fontSize: 13, fontWeight: 600, border: '1px solid #e2e1dc', borderRadius: 6, padding: '5px 7px', color: '#1a2233', width: 46, textAlign: 'right' }}
        />
        {suffix && <span style={{ fontSize: 11, color: '#6b6f76' }}>{suffix}</span>}
      </div>
    </div>
  );
}

// "These drive every projected year" — the canonical Assumptions panel.
// Each editable field writes the deal's field_overrides and re-runs the whole
// model (the canonical edit path, shared with the driver cells). Exit cap rate
// is Investment-owned, so it is shown here linked / read-only.
function AssumptionsPanel({
  dealId, overrides, onApply, running,
}: {
  dealId: string;
  overrides: Record<string, unknown>;
  onApply: (key: string, value: number, note: string) => Promise<void>;
  running: boolean;
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
            <AssumptionField label="Revenue inflation" unit="pct" suffix="%/yr" value={cur('revpar_growth')} disabled={running} onCommit={(v) => onApply('revpar_growth', v, 'Revenue inflation set on the Projections page')} />
            <AssumptionField label="Dept. expense inflation" unit="pct" suffix="%/yr" value={cur('expense_growth')} disabled={running} onCommit={(v) => onApply('expense_growth', v, 'Dept. expense inflation set on the Projections page')} />
            <AssumptionField label="Other expense inflation" unit="pct" suffix="%/yr" value={cur('other_expense_growth')} disabled={running} onCommit={(v) => onApply('other_expense_growth', v, 'Other expense inflation set on the Projections page')} />
          </div>
        </div>
        {/* Resort fee revenue */}
        <div style={cardStyle}>
          <div style={cardTitle}>Resort fee revenue</div>
          <div style={rowsWrap}>
            <AssumptionField label="Resort fee" unit="dollar" prefix="$" suffix="/night" value={cur('resort_fee_per_night')} disabled={running} onCommit={(v) => onApply('resort_fee_per_night', v, 'Resort fee/night set on the Projections page')} />
            <AssumptionField label="Capture Yr 1" unit="pct" suffix="%" value={cur('resort_fee_capture_y1')} disabled={running} onCommit={(v) => onApply('resort_fee_capture_y1', v, 'Resort-fee capture Yr 1 set on the Projections page')} />
            <AssumptionField label="Capture Yr 2" unit="pct" suffix="%" value={cur('resort_fee_capture_y2')} disabled={running} onCommit={(v) => onApply('resort_fee_capture_y2', v, 'Resort-fee capture Yr 2 set on the Projections page')} />
            <AssumptionField label="Capture Yr 3+" unit="pct" suffix="%" value={cur('resort_fee_capture_y3')} disabled={running} onCommit={(v) => onApply('resort_fee_capture_y3', v, 'Resort-fee capture Yr 3+ set on the Projections page')} />
          </div>
        </div>
        {/* Deal economics */}
        <div style={cardStyle}>
          <div style={cardTitle}>Deal economics</div>
          <div style={rowsWrap}>
            <AssumptionField label="Management fee" unit="pct" suffix="% of rev" value={cur('mgmt_fee_pct')} disabled={running} onCommit={(v) => onApply('mgmt_fee_pct', v, 'Management fee set on the Projections page')} />
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
