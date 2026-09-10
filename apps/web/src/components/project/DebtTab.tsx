'use client';
/**
 * Debt tab — canonical rebuild (FON-72, design/canonical/Debt Tab.dc.html).
 *
 * Built entirely from the shared design system (`@/components/design`) and read
 * exclusively from worker engine output via `getEngineField(outputs, 'debt', …)`
 * / `'capital'` / `'returns'` — never prototype placeholders. The Data Key strip
 * is mounted once in page.tsx, so this tab renders NO per-tab legend.
 *
 * Sub-tabs mirror the canonical exactly:
 *   Debt Overview · Loan Terms & Covenants · Refinance · Debt Schedule
 *
 * New backend fields wired here (DebtEngineOutputExt):
 *   • origination_fee_pct / _usd + exit_fee_pct / _usd  (fees; origination editable)
 *   • covenants[] (DebtCovenantStatus — current, signed headroom, pass/fail)
 *   • LTV is now Debt-owned (Investment dropped its LTV) — editable here
 *   • refi_year / refi_cash_out / balance_at_exit (Refinance section)
 *   • benchmark_name / benchmark_rate / spread on the senior tranche — the
 *     Loan Terms Benchmark → Spread → All-In build-up (floating; "—" if fixed)
 *   • refi_value_at_refinance / refi_ltv / refi_new_loan_proceeds /
 *     refi_existing_balance_repaid / refi_new_interest_rate /
 *     refi_financing_costs — the Refinance Assumptions detail
 *   • entry_debt_yield / entry_dscr — the entry credit metrics (stabilized_*
 *     stay null until a stabilized-year source exists → "—" cards)
 *
 * Edits take the canonical path: PATCH field_overrides then a debounced full
 * run so DSCR / leverage / returns re-derive. LTV resizes the senior tranche
 * (`debt_stack.tranches.0.principal_usd`); the origination fee writes
 * `debt_stack.tranches.0.upfront_fee_pct` (0..10 percent convention).
 *
 * FON-63 (Wave 2) — Debt is an ASSUMPTIONS WORKSPACE; debt documents are
 * optional. Every core term is an input here, persisted as field_overrides the
 * worker consumes (fractions for rates / spreads / covenant ratios, raw units
 * for dollars / months / years):
 *   • per tranche (`debt_stack.tranches.<idx>.*`, senior = 0, PACE = 1):
 *     principal_usd · rate_type ("fixed" | "floating") · rate_pct (fixed) ·
 *     spread_pct + index_rate_pct + rate_floor_pct / rate_cap_pct (floating) ·
 *     amortization_months (0 = interest-only) · io_period_months (IO stub)
 *   • maturity → the top-level `term_years` the schedule + Returns run on
 *   • covenant thresholds → `debt_stack.covenant_max_ltv / covenant_max_ltc /
 *     covenant_min_dscr / covenant_min_debt_yield` (no defaults: a covenant
 *     with no entered threshold shows an "Enter threshold" input and no verdict)
 * A required assumption that is missing is rendered as the input to provide
 * (with its consequence stated) — never a bare "—". Fees stay display-only
 * (nothing downstream consumes them yet) and say so.
 */
import {
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from 'react';
import { useParams } from 'next/navigation';
import { DollarSign } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import { IntroCard } from '@/components/help/IntroCard';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';
import { useSource } from '@/lib/hooks/useDealProvenance';
import {
  api,
  isWorkerConnected,
  WorkerError,
  type EngineOutputsResponse,
  type ValueState,
  type DebtCovenantStatus,
} from '@/lib/api';
import { fmtCurrency, fmtPct, fmtMillions, cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useTraceGraph } from '@/lib/hooks/useValueTrace';
import {
  SectionCard,
  SubTabNav,
  StatementTable,
  ProvenanceDot,
  palette,
  prov,
  type StatementRow,
} from '@/components/design';

// ─── Canonical sub-tabs (design/canonical/Debt Tab.dc.html) ─────────────
const SUB_TABS = [
  { id: 'Debt Overview', label: 'Debt Overview' },
  { id: 'Loan Terms & Covenants', label: 'Loan Terms & Covenants' },
  { id: 'Refinance', label: 'Refinance' },
  { id: 'Debt Schedule', label: 'Debt Schedule' },
];

// ─── Worker output shapes (only the fields we read) ─────────────────────
interface DebtYearLite {
  year: number;
  interest: number;
  principal: number;
  debt_service: number;
  ending_balance: number;
  dscr: number | null;
}
interface DebtMonthLite {
  month: number;
  interest: number;
  principal: number;
  payment: number;
  ending_balance: number;
}
interface DebtStackTrancheLite {
  kind: string;
  label?: string;
  loan_amount?: number;
  rate_type: string;
  all_in_rate?: number | null;
  annual_debt_service?: number | null;
  interest_only?: boolean;
  terms_pending?: boolean;
  amortization_years?: number | null;
  io_months?: number | null;
  benchmark_name?: string | null;
  benchmark_rate?: number | null;
  benchmark_is_default?: boolean | null;
  spread?: number | null;
  rate_floor?: number | null;
  rate_cap?: number | null;
}
interface DebtStackLite {
  tranches?: DebtStackTrancheLite[];
  total_debt?: number;
  priced_debt?: number;
  total_annual_debt_service?: number;
  warnings?: string[];
}

// ─── Override keys (worker: engine_runner._OVERRIDE_DEBT_KEYS) ──────────
const SENIOR = 0;
const PACE = 1;
const tk = (idx: number, field: string) => `debt_stack.tranches.${idx}.${field}`;
/** Maturity is the deal-level term the schedule + Returns run on. */
const TERM_KEY = 'term_years';
const COVENANT_KEY: Record<DebtCovenantStatus['name'], string> = {
  ltv: 'debt_stack.covenant_max_ltv',
  ltc: 'debt_stack.covenant_max_ltc',
  dscr: 'debt_stack.covenant_min_dscr',
  debt_yield: 'debt_stack.covenant_min_debt_yield',
};

// ─── Input parsers (display units → worker units) ───────────────────────
/** "7.25" → 0.0725 (rates, spreads, covenant ratios stored as fractions). */
const parsePctFrac = (s: string): number | null => {
  const n = parseFloat(s);
  return Number.isFinite(n) && n >= 0 ? n / 100 : null;
};
const parseIntMin = (min: number) => (s: string): number | null => {
  const n = parseInt(s, 10);
  return Number.isFinite(n) && n >= min ? n : null;
};
const parseDollars = (s: string): number | null => {
  const n = parseFloat(s);
  return Number.isFinite(n) && n >= 0 ? Math.round(n) : null;
};
const parseRatio = (s: string): number | null => {
  const n = parseFloat(s);
  return Number.isFinite(n) && n >= 0 ? n : null;
};

// ─── Canonical value vocabulary (mirrors Investment tab) ────────────────
// doc → document sourced · linked → owned by another engine · input → editable
// assumption · calc → calculated by Fondok · awaiting → not yet available.
type ValueKind = 'doc' | 'linked' | 'input' | 'calc' | 'awaiting';

function valueColor(kind: ValueKind, bold: boolean, overridden: boolean): string {
  if (overridden) return prov.blue;
  if (bold) return prov.black;
  if (kind === 'input') return prov.blue;
  if (kind === 'linked' || kind === 'doc') return prov.green;
  if (kind === 'awaiting') return prov.muted;
  return prov.gray;
}
function kindToState(kind: ValueKind): ValueState {
  switch (kind) {
    case 'doc': return 'document_sourced';
    case 'linked': return 'linked';
    case 'input': return 'assumption';
    case 'awaiting': return 'awaiting_data';
    default: return 'calculated';
  }
}

interface RowDef {
  id: string;
  label: string;
  kind: ValueKind;        // value text color
  state: ValueState;      // provenance dot origin
  value: ReactNode;       // formatted string or a custom editor node
  bold?: boolean;
  overridden?: boolean;
  /** Live assumption source (seed / deal_row / …) for the provenance badge;
   *  an overridden row always badges `analyst_override`. */
  source?: string | null;
  note?: ReactNode;
  link?: { label: string; tab: string };
}

const has = (v: number | undefined | null): v is number => v != null && Number.isFinite(v);
const money = (v: number | undefined): string => (has(v) ? fmtCurrency(v) : '—');
const mm = (v: number | undefined): string => (has(v) ? fmtMillions(v, 2) : '—');
const pctv = (v: number | undefined, d = 1): string => (has(v) ? fmtPct(v, d) : '—');
const ratio = (v: number | undefined): string => (has(v) ? `${v.toFixed(2)}x` : '—');
// Fees arrive in the codebase 0..10 PERCENT convention (1.0 = 1.00%).
const feePct = (v: number | undefined): string => (has(v) ? `${v.toFixed(2)}%` : '—');

// Covenant column formatting — LTV/LTC/DY are fractions, DSCR is a ratio.
function covCurrent(c: DebtCovenantStatus): string {
  if (c.current == null) return '—';
  return c.name === 'dscr' ? `${c.current.toFixed(2)}x` : fmtPct(c.current, 1);
}
function covThreshold(c: DebtCovenantStatus): string {
  if (c.threshold == null) return '—';
  return c.name === 'dscr' ? `${c.threshold.toFixed(2)}x` : fmtPct(c.threshold, 1);
}
function covCovenantCaption(c: DebtCovenantStatus): string {
  if (c.threshold == null) return '—';
  const t = c.name === 'dscr' ? `${c.threshold.toFixed(2)}x` : fmtPct(c.threshold, 1);
  return `${c.kind === 'max' ? 'Max' : 'Min'} ${t}`;
}
function covHeadroom(c: DebtCovenantStatus): string {
  if (c.headroom == null) return '—';
  const sign = c.headroom >= 0 ? '+' : '−';
  const mag = Math.abs(c.headroom);
  return c.name === 'dscr' ? `${sign}${mag.toFixed(2)}x` : `${sign}${(mag * 100).toFixed(1)} pts`;
}
function covHeadroomColor(c: DebtCovenantStatus): string {
  if (c.passes == null) return prov.muted;
  return c.passes ? prov.green : prov.amber;
}
const COV_BASIS: Record<string, string> = {
  ltv: 'Loan ÷ property value',
  ltc: 'Loan ÷ total cost basis',
  dscr: 'Year-1 NOI ÷ year-1 debt service',
  debt_yield: 'Year-1 NOI ÷ loan',
};

// FON-72 follow-up — Completion Guarantee qualitative statuses.
const CG_OPTIONS: { key: string; label: string }[] = [
  { key: 'required', label: 'Required' },
  { key: 'in_place', label: 'In place' },
  { key: 'not_required', label: 'Not required' },
];
const cgLabel = (v: string | undefined): string =>
  CG_OPTIONS.find((o) => o.key === v)?.label ?? '—';

function readOverrideNum(
  overrides: Record<string, unknown>,
  path: string,
  fallback: number,
): number {
  const raw = overrides[path];
  const val = raw && typeof raw === 'object' && 'value' in raw
    ? (raw as { value: unknown }).value
    : raw;
  if (val == null || val === '') return fallback;
  const n = typeof val === 'number' ? val : Number(val);
  return Number.isFinite(n) ? n : fallback;
}

export default function DebtTab() {
  const [tab, setTab] = useState('Debt Overview');
  const [period, setPeriod] = useState<'Annual' | 'Monthly'>('Annual');
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { toast } = useToast();
  const { outputs, previous } = useEngineOutputs(dealId);
  const { deal, refresh: refreshDeal } = useDeal(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  // Computed-value provenance — dots read the real /provenance `state` when a
  // provider is present, else fall back to the canonical semantic kind.
  const debtTrace = useTraceGraph('debt');
  const capitalTrace = useTraceGraph('capital');
  const tracedState = useCallback(
    (engine: 'debt' | 'capital', path: string): ValueState | null =>
      (engine === 'debt' ? debtTrace : capitalTrace).get(path)?.state ?? null,
    [debtTrace, capitalTrace],
  );
  // Live assumption sources for the senior seed terms (seed / deal_row / …) so
  // an untouched term badges where it came from; null without a provider.
  const srcRate = useSource('interest_rate');
  const srcTerm = useSource('term_years');
  const srcAmort = useSource('amortization_years');

  // ─── Editable overrides (canonical path: field_overrides + full run) ──
  const isMockId = /^\d+$/.test(dealId);
  const liveMode = isWorkerConnected() && !isMockId;
  const [overrides, setOverrides] = useState<Record<string, unknown>>({});
  useEffect(() => {
    setOverrides((deal?.field_overrides as Record<string, unknown> | undefined) ?? {});
  }, [deal?.field_overrides]);
  const fullRun = useEngineRun(liveMode ? dealId : '', 'returns', { runMode: 'all' });
  const rerunTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (rerunTimerRef.current) clearTimeout(rerunTimerRef.current);
  }, []);
  const onSaveOverride = useCallback(
    async (patch: Record<string, number | string | null>) => {
      if (!liveMode) {
        toast('Editing is disabled on demo deals', { type: 'info' });
        return;
      }
      const next = { ...overrides };
      for (const [path, value] of Object.entries(patch)) {
        if (value === null) delete next[path];
        else next[path] = value;
      }
      setOverrides(next); // optimistic
      try {
        await api.deals.update(dealId, { field_overrides: next });
        toast('Saved — re-running the model…', { type: 'success' });
        void refreshDeal?.();
        if (rerunTimerRef.current) clearTimeout(rerunTimerRef.current);
        rerunTimerRef.current = setTimeout(() => { void fullRun.run(); }, 1200);
      } catch (err) {
        setOverrides(overrides); // rollback
        const detail = err instanceof WorkerError ? err.body : String(err);
        toast(`Save failed: ${detail || 'worker rejected update'}`, { type: 'error' });
      }
    },
    [overrides, dealId, liveMode, toast, refreshDeal, fullRun],
  );
  const overridden = (key: string): boolean => overrides[key] !== undefined;

  // ─── Engine field reads (no fixtures — '—' until the engine emits) ────
  const wLoan = getEngineField<number>(outputs, 'debt', 'loan_amount');
  const wDscr = getEngineField<number>(outputs, 'debt', 'year_one_dscr');
  const wDy = getEngineField<number>(outputs, 'debt', 'year_one_debt_yield');
  const wAvgDscr = getEngineField<number>(outputs, 'debt', 'avg_dscr');
  const wRate = getEngineField<number>(outputs, 'debt', 'interest_rate');
  const wTermYears = getEngineField<number>(outputs, 'debt', 'term_years');
  const wAmortYears = getEngineField<number>(outputs, 'debt', 'amortization_years');
  const wIoMonths = getEngineField<number>(outputs, 'debt', 'interest_only_months');
  const wOrigFeePct = getEngineField<number>(outputs, 'debt', 'origination_fee_pct');
  const wOrigFeeUsd = getEngineField<number>(outputs, 'debt', 'origination_fee_usd');
  const wExitFeePct = getEngineField<number>(outputs, 'debt', 'exit_fee_pct');
  const wExitFeeUsd = getEngineField<number>(outputs, 'debt', 'exit_fee_usd');
  const wCovenants = getEngineField<DebtCovenantStatus[]>(outputs, 'debt', 'covenants') ?? [];
  const wAnnual = getEngineField<DebtYearLite[]>(outputs, 'debt', 'schedule') ?? [];
  const wMonthly = getEngineField<DebtMonthLite[]>(outputs, 'debt', 'monthly_schedule') ?? [];
  const wStack = getEngineField<DebtStackLite>(outputs, 'debt', 'debt_stack');
  const wRefiYear = getEngineField<number>(outputs, 'debt', 'refi_year');
  const wRefiCashOut = getEngineField<number>(outputs, 'debt', 'refi_cash_out');
  const wBalanceAtExit = getEngineField<number>(outputs, 'debt', 'balance_at_exit');
  // FON-72 follow-up — refinance detail + entry/stabilized credit split.
  const wRefiValue = getEngineField<number>(outputs, 'debt', 'refi_value_at_refinance');
  const wRefiLtv = getEngineField<number>(outputs, 'debt', 'refi_ltv');
  const wRefiProceeds = getEngineField<number>(outputs, 'debt', 'refi_new_loan_proceeds');
  const wRefiPayoff = getEngineField<number>(outputs, 'debt', 'refi_existing_balance_repaid');
  const wRefiRate = getEngineField<number>(outputs, 'debt', 'refi_new_interest_rate');
  const wRefiCosts = getEngineField<number>(outputs, 'debt', 'refi_financing_costs');
  const wStabDy = getEngineField<number>(outputs, 'debt', 'stabilized_debt_yield');
  const wStabDscr = getEngineField<number>(outputs, 'debt', 'stabilized_dscr');
  // FON-72 follow-up — Completion Guarantee covenant status (qualitative). The
  // engine echoes the saved override; before a re-run lands we read the pending
  // override optimistically so the control reflects the analyst's pick at once.
  const wCompletionGuarantee = getEngineField<string>(outputs, 'debt', 'completion_guarantee');
  const cgOverride = overrides['debt.completion_guarantee'];
  const completionGuarantee: string | undefined =
    (typeof cgOverride === 'string' ? cgOverride : undefined) ??
    (wCompletionGuarantee ?? undefined);

  const wPurchase = getEngineField<number>(outputs, 'capital', 'purchase_price');
  const wTotalBasis =
    getEngineField<number>(outputs, 'capital', 'total_capital_usd') ??
    getEngineField<number>(outputs, 'capital', 'total_capital');
  const wEquity = getEngineField<number>(outputs, 'capital', 'equity_amount');
  const wDebtAmount = getEngineField<number>(outputs, 'capital', 'debt_amount');
  const wLtvCapital = getEngineField<number>(outputs, 'capital', 'ltv');
  const wLtcCapital = getEngineField<number>(outputs, 'capital', 'ltc');

  const wLeveredIrr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const wMoic = getEngineField<number>(outputs, 'returns', 'equity_multiple');

  const covByName = (n: DebtCovenantStatus['name']): DebtCovenantStatus | null =>
    wCovenants.find((c) => c.name === n) ?? null;

  const loanN = wLoan ?? wDebtAmount;
  const ltvN =
    covByName('ltv')?.current ??
    wLtvCapital ??
    (has(loanN) && has(wPurchase) && wPurchase > 0 ? loanN / wPurchase : undefined);
  const ltcN = covByName('ltc')?.current ?? wLtcCapital;

  const seniorTranche = wStack?.tranches?.find((t) => t.kind === 'senior') ?? wStack?.tranches?.[0];
  // Rate basis: the analyst's pick (optimistic, before the re-run lands) wins
  // over the engine echo, so the Loan Terms card re-shapes immediately.
  const rateTypeOverride = overrides[tk(SENIOR, 'rate_type')];
  const seniorRateType: string | undefined =
    (rateTypeOverride === 'fixed' || rateTypeOverride === 'floating' ? rateTypeOverride : undefined) ??
    seniorTranche?.rate_type;
  const seniorFloating = seniorRateType === 'floating';
  // The floating rate build-up echoed from the senior tranche (index actually
  // used + whether it is Fondok's flat default, spread, floor / cap).
  const benchmarkName = seniorTranche?.benchmark_name ?? undefined;
  const benchmarkRate = seniorTranche?.benchmark_rate ?? undefined;
  const benchmarkIsDefault = seniorTranche?.benchmark_is_default === true;
  const seniorSpread = seniorTranche?.spread ?? undefined;
  const seniorFloor = seniorTranche?.rate_floor ?? undefined;
  const seniorCap = seniorTranche?.rate_cap ?? undefined;
  const seniorPending = seniorTranche?.terms_pending === true;
  const allInRate = seniorTranche?.all_in_rate ?? wRate;
  const hasBenchmarkBuildUp = seniorFloating;

  // PACE (tranche index 1). The engine only emits it once funded; read the
  // pending override optimistically so "Enter amount" flips to the terms at once.
  const paceTranche = wStack?.tranches?.find((t) => t.kind === 'pace');
  const paceAmount = readOverrideNum(overrides, tk(PACE, 'principal_usd'), paceTranche?.loan_amount ?? 0);
  const paceFunded = paceAmount > 0;
  const paceRateOv = readOverrideNum(overrides, tk(PACE, 'rate_pct'), Number.NaN);
  const paceRate: number | undefined =
    paceTranche?.all_in_rate ?? (Number.isFinite(paceRateOv) ? paceRateOv : undefined);
  // Pending = funded with no rate the engine can price (engine flag, or a
  // fresh amount the engine hasn't seen yet).
  const pacePending = paceFunded && (paceTranche ? paceTranche.terms_pending === true : !has(paceRate));
  const paceAmortOv = readOverrideNum(overrides, tk(PACE, 'amortization_months'), Number.NaN);
  const paceAmortYears: number = Number.isFinite(paceAmortOv)
    ? Math.round(paceAmortOv / 12)
    : (paceTranche ? (paceTranche.interest_only ? 0 : (paceTranche.amortization_years ?? 0)) : 0);
  const paceIoOv = readOverrideNum(overrides, tk(PACE, 'io_period_months'), Number.NaN);
  const paceIoMonths: number = Number.isFinite(paceIoOv) ? Math.round(paceIoOv) : (paceTranche?.io_months ?? 0);
  const paceDebtService = paceTranche && !paceTranche.terms_pending ? (paceTranche.annual_debt_service ?? undefined) : undefined;
  const totalDebt = wStack?.total_debt;

  // Debt engine hasn't produced a loan → empty state (Sam QA #4 short-circuit).
  const hasWorkerDebtOutput = wLoan != null;

  const INTRO = (
    <IntroCard
      dismissKey="debt-intro"
      title="The Debt Engine"
      body={
        <>
          How the debt is structured: loan amount, interest rate, covenants, and any refinancing.
          This is where you stress-test whether the hotel earns enough to comfortably service its loan
          — the headline ratio is <span className="font-semibold">DSCR</span> (Debt Service Coverage Ratio).
        </>
      }
    />
  );

  if (!hasWorkerDebtOutput) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          {INTRO}
          <EngineHeader
            name="Debt Engine"
            desc="Structures senior and mezzanine debt, calculates debt service, and models refinancing scenarios."
            outputs={['Loan Amount', 'DSCR', 'Debt Yield', '+1']}
            dependsOn="P&L"
            dealId={dealId}
            engineName="debt"
            onRunStart={() => setComputing(true)}
            onRunComplete={() => { setComputing(false); setRunToken(Date.now()); }}
          />
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <DollarSign size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">Debt Engine unavailable</h3>
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              Debt structuring needs the <span className="font-medium">P&amp;L</span> engine to finish first
              (it sizes the loan against year-1 NOI). Run the model from the P&amp;L tab, or upload a T-12
              if you haven&apos;t yet.
            </p>
            <Button variant="primary" size="sm" className="mt-4"
              onClick={() => toast('Engine queued — check back shortly', { type: 'info' })}>
              Run Debt Engine
            </Button>
          </Card>
          <EngineRunHistory dealId={dealId} />
        </div>
        <EngineRightRail />
      </div>
    );
  }

  const refiActive = has(wRefiYear) && wRefiYear > 0;
  const refiYearOverride = readOverrideNum(overrides, 'debt_stack.refi_test_year', 0);

  const SUB_CAPTION: Record<string, string> = {
    'Debt Overview': 'Sizing, pricing and the annual schedule',
    'Loan Terms & Covenants': 'Full term sheet and covenant tests',
    Refinance: refiActive ? 'Included in the model' : 'Excluded from the model until enabled',
    'Debt Schedule': 'Period-by-period detail',
  };

  // ─── Senior loan inputs (index 0) ────────────────────────────────────
  // One editor per override key — the Capital Structure amount / LTV pair
  // both resize `principal_usd`, so they can never disagree after the re-run.
  const seniorPrincipalKey = tk(SENIOR, 'principal_usd');
  const ltvOverridden = overridden(seniorPrincipalKey);
  // `loan_amount` on the output is the whole stack; the senior's own balance
  // comes from its tranche result (equal to the total on a senior-only deal).
  const seniorAmount = seniorTranche?.loan_amount ?? loanN;
  const seniorAmountNode = (
    <EditableValue
      display={money(seniorAmount)}
      draftValue={has(seniorAmount) ? String(Math.round(seniorAmount)) : ''}
      parse={parseDollars}
      onSave={(v) => onSaveOverride({ [seniorPrincipalKey]: v })}
      editable={liveMode}
      suffix="$"
      color={valueColor('input', false, ltvOverridden)}
      testId="edit-senior-amount"
    />
  );
  // Editable LTV — Debt owns it (Investment dropped its LTV). LTV is total debt
  // ÷ property value, so the target resizes the SENIOR net of any funded PACE.
  const ltvEditable = liveMode && has(wPurchase) && wPurchase > 0;
  const ltvNode = (
    <EditableValue
      display={pctv(ltvN, 1)}
      draftValue={has(ltvN) ? (ltvN * 100).toFixed(1) : ''}
      parse={(s) => { const n = parseFloat(s); return Number.isFinite(n) && n > 0 ? n / 100 : null; }}
      onSave={(frac) => onSaveOverride({
        [seniorPrincipalKey]: Math.max(0, Math.round(frac * (wPurchase as number) - (paceFunded ? paceAmount : 0))),
      })}
      editable={ltvEditable}
      suffix="%"
      color={valueColor('input', false, ltvOverridden)}
      testId="edit-ltv"
    />
  );

  // PACE amount — funding it adds the tranche to Total Debt / LTV / LTC / debt
  // yield at once; its debt service waits for a rate (Loan Terms & Covenants).
  const paceAmountKey = tk(PACE, 'principal_usd');
  const paceAmountOverridden = overridden(paceAmountKey);
  const paceAmountNode = (
    <EditableValue
      display={paceFunded ? money(paceAmount) : '—'}
      emptyLabel="Enter amount"
      draftValue={paceFunded ? String(Math.round(paceAmount)) : ''}
      parse={parseDollars}
      onSave={(v) => onSaveOverride({ [paceAmountKey]: v > 0 ? v : null })}
      editable={liveMode}
      suffix="$"
      color={valueColor('input', false, paceAmountOverridden)}
      testId="edit-pace-amount"
      title="Click to change — enter 0 to remove PACE from the stack"
    />
  );
  const paceStatusNote = !paceFunded
    ? 'Not funded — enter an amount to add a PACE tranche to the capital stack'
    : pacePending
      ? 'Terms pending — counts in Total Debt, LTV, LTC and debt yield; excluded from debt service and DSCR until a rate is entered'
      : has(paceDebtService)
        ? `Priced — ${fmtCurrency(paceDebtService)} / yr of debt service is in DSCR, Cash Flow and Returns`
        : 'Priced — its debt service is in DSCR, Cash Flow and Returns';

  // Editable origination fee — writes the senior tranche upfront fee (percent).
  // Display-only downstream: nothing in Cash Flow / Returns consumes it yet.
  const feeEditable = liveMode;
  const feeOverridden = overridden(tk(SENIOR, 'upfront_fee_pct'));
  const origFeeDisplay = has(wOrigFeePct)
    ? `${wOrigFeePct.toFixed(2)}%${has(wOrigFeeUsd) ? ` · ${fmtCurrency(wOrigFeeUsd)}` : ''}`
    : '—';
  const origFeeNode = (
    <EditableValue
      display={origFeeDisplay}
      draftValue={has(wOrigFeePct) ? wOrigFeePct.toFixed(2) : ''}
      parse={(s) => { const n = parseFloat(s); return Number.isFinite(n) && n >= 0 ? n : null; }}
      onSave={(v) => onSaveOverride({ [tk(SENIOR, 'upfront_fee_pct')]: v })}
      editable={feeEditable}
      suffix="%"
      color={valueColor('input', false, feeOverridden)}
      testId="edit-orig-fee"
    />
  );

  // ─── Debt Overview rows ───────────────────────────────────────────────
  const capitalStructure: RowDef[] = [
    { id: 'purchase', label: 'Purchase Price / Property Value', kind: 'linked', state: 'linked',
      value: money(wPurchase), link: { label: '→ Investment', tab: 'investment' },
      note: 'The LTV denominator — purchase price at close' },
    { id: 'basis', label: 'Total Cost / Basis', kind: 'linked', state: 'linked',
      value: money(wTotalBasis), link: { label: '→ Investment', tab: 'investment' },
      note: 'Purchase plus renovation, closing costs and reserves — the LTC denominator' },
    { id: 'loan', label: 'Senior Loan Amount', kind: 'input',
      state: 'assumption', value: seniorAmountNode, overridden: ltvOverridden,
      note: 'Enter the amount or the LTV below — either one resizes the senior loan' },
    { id: 'pace', label: 'PACE Loan Amount', kind: 'input',
      state: paceFunded ? 'assumption' : 'awaiting_data',
      value: paceAmountNode, overridden: paceAmountOverridden,
      note: paceStatusNote },
    { id: 'totalDebt', label: 'Total Debt', kind: 'calc', bold: true,
      state: tracedState('debt', 'loan_amount') ?? 'calculated',
      value: money(totalDebt ?? loanN),
      note: paceFunded && pacePending ? 'Senior + PACE (PACE terms pending)' : undefined },
    { id: 'ltv', label: 'LTV', kind: 'input',
      state: ltvOverridden ? 'assumption' : (tracedState('debt', 'ltv') ?? 'calculated'),
      value: ltvNode, overridden: ltvOverridden,
      note: 'Total debt ÷ property value — editing resizes the senior loan' },
    { id: 'ltc', label: 'LTC', kind: 'calc',
      state: tracedState('capital', 'ltc') ?? 'calculated', value: pctv(ltcN, 1) },
    { id: 'equity', label: 'Equity Requirement', kind: 'calc', bold: true, state: 'linked',
      value: money(wEquity), link: { label: '→ Investment', tab: 'investment' } },
  ];

  // ─── Loan Terms (senior) — every core term is an input ───────────────
  // Floating: Benchmark → Spread (→ Floor / Cap) → All-In. Fixed: the rate.
  // A term the analyst has not entered renders as the input to provide, with
  // the consequence stated — never a bare "—".
  const rateKey = tk(SENIOR, 'rate_pct');
  const spreadKey = tk(SENIOR, 'spread_pct');
  const indexKey = tk(SENIOR, 'index_rate_pct');
  const floorKey = tk(SENIOR, 'rate_floor_pct');
  const capKey = tk(SENIOR, 'rate_cap_pct');
  const amortKey = tk(SENIOR, 'amortization_months');
  const ioKey = tk(SENIOR, 'io_period_months');
  const rateOverridden = overridden(rateKey) || overridden(tk(SENIOR, 'rate_type'));
  const seniorIsFullIo = has(wAmortYears) && wAmortYears === 0;
  const fallbackRateNote = seniorFloating && !has(seniorSpread)
    ? `Required to price the floating loan — until entered the schedule runs at the fixed rate on file (${pctv(wRate, 2)})`
    : undefined;

  const pctEditor = (opts: {
    key: string; value: number | undefined; emptyLabel?: string; testId: string;
    bold?: boolean; clearOnZero?: boolean; suffix?: string; title?: string;
  }) => (
    <EditableValue
      display={has(opts.value) ? pctv(opts.value, 2) : (opts.clearOnZero ? 'None' : '—')}
      emptyLabel={opts.emptyLabel}
      draftValue={has(opts.value) ? (opts.value * 100).toFixed(2) : ''}
      parse={parsePctFrac}
      onSave={(frac) => onSaveOverride({ [opts.key]: opts.clearOnZero && frac <= 0 ? null : frac })}
      editable={liveMode}
      suffix={opts.suffix ?? '%'}
      bold={opts.bold}
      color={valueColor('input', !!opts.bold, overridden(opts.key))}
      testId={opts.testId}
      title={opts.title}
    />
  );

  const loanTerms: RowDef[] = [
    ...(seniorFloating
      ? [
          { id: 'benchmark', label: 'Benchmark', kind: 'input' as const, state: 'assumption' as const,
            overridden: overridden(indexKey),
            value: (
              <EditableValue
                display={has(benchmarkRate) ? `${benchmarkName ?? 'SOFR'} · ${pctv(benchmarkRate, 2)}` : '—'}
                emptyLabel="Enter index rate"
                draftValue={has(benchmarkRate) ? (benchmarkRate * 100).toFixed(2) : ''}
                parse={parsePctFrac}
                onSave={(frac) => onSaveOverride({ [indexKey]: frac })}
                editable={liveMode}
                suffix="%"
                color={valueColor('input', false, overridden(indexKey))}
                testId="edit-benchmark"
              />
            ),
            note: benchmarkIsDefault
              ? 'Fondok’s flat SOFR assumption, not market data — enter the index you underwrite to'
              : 'Your index assumption — clamped to the floor / cap below' },
          { id: 'spread', label: 'Spread', kind: 'input' as const,
            state: has(seniorSpread) ? 'assumption' as const : 'awaiting_data' as const,
            overridden: overridden(spreadKey),
            value: pctEditor({ key: spreadKey, value: seniorSpread, emptyLabel: 'Enter spread', testId: 'edit-spread' }),
            note: fallbackRateNote },
          { id: 'floor', label: 'Index Floor', kind: 'input' as const, state: 'assumption' as const,
            overridden: overridden(floorKey),
            value: pctEditor({ key: floorKey, value: seniorFloor, clearOnZero: true, testId: 'edit-floor', title: 'Optional — 0 clears the floor' }),
            note: 'Optional — the index never prices below this' },
          { id: 'cap', label: 'Index Cap', kind: 'input' as const, state: 'assumption' as const,
            overridden: overridden(capKey),
            value: pctEditor({ key: capKey, value: seniorCap, clearOnZero: true, testId: 'edit-cap', title: 'Optional — 0 clears the cap' }),
            note: 'Optional — the index never prices above this' },
          { id: 'rate', label: 'Underwritten All-In Rate',
            kind: seniorPending ? 'awaiting' as const : 'calc' as const,
            state: seniorPending ? 'awaiting_data' as const : 'calculated' as const,
            value: seniorPending ? 'Awaiting spread' : pctv(allInRate, 2), bold: !seniorPending,
            note: 'Benchmark (clamped) + Spread' },
        ]
      : [
          { id: 'rate', label: 'Fixed Interest Rate', kind: 'input' as const, state: 'assumption' as const,
            overridden: rateOverridden, source: srcRate?.source,
            value: pctEditor({ key: rateKey, value: allInRate, emptyLabel: 'Enter rate', testId: 'edit-rate', bold: true }),
            note: 'All-in fixed coupon the schedule runs on' },
        ]),
    { id: 'amort', label: 'Amortization', kind: 'input', state: 'assumption',
      overridden: overridden(amortKey), source: srcAmort?.source,
      value: (
        <EditableValue
          display={has(wAmortYears) ? (wAmortYears === 0 ? 'Interest-only' : `${wAmortYears} years`) : '—'}
          emptyLabel="Enter amortization"
          draftValue={has(wAmortYears) ? String(wAmortYears) : ''}
          parse={parseIntMin(0)}
          onSave={(yrs) => onSaveOverride({ [amortKey]: Math.round(yrs) * 12 })}
          editable={liveMode}
          suffix="years"
          color={valueColor('input', false, overridden(amortKey))}
          testId="edit-amort"
          title="Click to change — 0 = interest-only for the full term"
        />
      ),
      note: '0 = interest-only for the full term' },
    { id: 'term', label: 'Maturity', kind: 'input', state: 'assumption',
      overridden: overridden(TERM_KEY), source: srcTerm?.source,
      value: (
        <EditableValue
          display={has(wTermYears) ? `${wTermYears} years` : '—'}
          emptyLabel="Enter term"
          draftValue={has(wTermYears) ? String(wTermYears) : ''}
          parse={parseIntMin(1)}
          onSave={(yrs) => onSaveOverride({ [TERM_KEY]: Math.round(yrs) })}
          editable={liveMode}
          suffix="years"
          color={valueColor('input', false, overridden(TERM_KEY))}
          testId="edit-term"
        />
      ),
      note: 'The schedule runs to maturity — include a refinance (Refinance tab) to model a take-out before exit' },
    { id: 'io', label: 'Interest-Only Period', kind: seniorIsFullIo ? 'calc' : 'input',
      state: seniorIsFullIo ? 'calculated' : 'assumption', overridden: overridden(ioKey),
      value: seniorIsFullIo
        ? 'Full term'
        : (
          <EditableValue
            display={has(wIoMonths) ? (wIoMonths > 0 ? `${wIoMonths} months` : 'None') : '—'}
            emptyLabel="Enter IO period"
            draftValue={has(wIoMonths) ? String(wIoMonths) : ''}
            parse={parseIntMin(0)}
            onSave={(m) => onSaveOverride({ [ioKey]: Math.round(m) })}
            editable={liveMode}
            suffix="months"
            color={valueColor('input', false, overridden(ioKey))}
            testId="edit-io"
          />
        ),
      note: seniorIsFullIo
        ? 'Set an amortization above to model an interest-only stub instead'
        : 'Interest-only stub before principal amortization begins' },
    { id: 'orig', label: 'Origination Fee', kind: 'input',
      state: 'assumption', value: origFeeNode, overridden: feeOverridden,
      note: 'Display only in this release — not yet carried into Sources & Uses, Cash Flow or Returns' },
  ];

  // ─── PACE loan terms (index 1) — fixed-rate tranche ──────────────────
  const paceRateKey = tk(PACE, 'rate_pct');
  const paceAmortKey = tk(PACE, 'amortization_months');
  const paceIoKey = tk(PACE, 'io_period_months');
  const paceTerms: RowDef[] = [
    { id: 'paceAmount', label: 'PACE Loan Amount', kind: 'input',
      state: paceFunded ? 'assumption' : 'awaiting_data', overridden: paceAmountOverridden,
      value: paceAmountNode, note: paceFunded ? undefined : paceStatusNote },
    ...(paceFunded
      ? [
          { id: 'paceRate', label: 'Fixed Interest Rate', kind: 'input' as const,
            state: has(paceRate) ? 'assumption' as const : 'awaiting_data' as const,
            overridden: overridden(paceRateKey), bold: true,
            value: pctEditor({ key: paceRateKey, value: paceRate, emptyLabel: 'Enter rate', testId: 'edit-pace-rate', bold: true }),
            note: pacePending ? paceStatusNote : undefined },
          { id: 'paceAmort', label: 'Amortization', kind: 'input' as const, state: 'assumption' as const,
            overridden: overridden(paceAmortKey),
            value: (
              <EditableValue
                display={paceAmortYears === 0 ? 'Interest-only' : `${paceAmortYears} years`}
                draftValue={String(paceAmortYears)}
                parse={parseIntMin(0)}
                onSave={(yrs) => onSaveOverride({ [paceAmortKey]: Math.round(yrs) * 12 })}
                editable={liveMode}
                suffix="years"
                color={valueColor('input', false, overridden(paceAmortKey))}
                testId="edit-pace-amort"
                title="Click to change — 0 = interest-only for the full term"
              />
            ),
            note: '0 = interest-only for the full term' },
          ...(paceAmortYears > 0
            ? [{ id: 'paceIo', label: 'Interest-Only Period', kind: 'input' as const, state: 'assumption' as const,
                overridden: overridden(paceIoKey),
                value: (
                  <EditableValue
                    display={paceIoMonths > 0 ? `${paceIoMonths} months` : 'None'}
                    draftValue={String(paceIoMonths)}
                    parse={parseIntMin(0)}
                    onSave={(m) => onSaveOverride({ [paceIoKey]: Math.round(m) })}
                    editable={liveMode}
                    suffix="months"
                    color={valueColor('input', false, overridden(paceIoKey))}
                    testId="edit-pace-io"
                  />
                ),
                note: 'Interest-only stub before principal amortization begins' }]
            : []),
          { id: 'paceDs', label: 'Annual Debt Service', kind: 'calc' as const,
            state: has(paceDebtService) ? 'calculated' as const : 'awaiting_data' as const,
            value: has(paceDebtService) ? fmtCurrency(paceDebtService) : '—', bold: true,
            note: pacePending ? 'Excluded until a rate is entered' : 'Year-1 debt service on this tranche — included in DSCR, Cash Flow and Returns' },
        ]
      : []),
  ];

  // ─── Loan Terms & Covenants — full term sheet (descriptive fields await
  //     the loan doc, deferred out of MVP scope → honest em-dashes). ──────
  const cgOverridden = overridden('debt.completion_guarantee');
  const fullTerms: RowDef[] = [
    ...loanTerms,
    { id: 'exit', label: 'Exit Fee', kind: 'calc', state: has(wExitFeePct) ? 'calculated' : 'awaiting_data',
      value: has(wExitFeePct) ? `${feePct(wExitFeePct)}${has(wExitFeeUsd) ? ` · ${fmtCurrency(wExitFeeUsd)}` : ''}` : '—',
      note: 'Display only — not modeled in the schedule, Cash Flow or Returns in this release' },
    // Completion Guarantee — analyst-entered qualitative status (feeds the
    // Completion Guarantee covenant row in the Covenants card).
    { id: 'completionGuarantee', label: 'Completion Guarantee', kind: 'input',
      state: completionGuarantee ? 'assumption' : 'awaiting_data',
      overridden: cgOverridden,
      value: (
        <CompletionGuaranteeControl
          value={completionGuarantee}
          editable={liveMode}
          onSave={(v) => onSaveOverride({ 'debt.completion_guarantee': v })}
        />
      ),
      note: 'Lender completion guarantee on the renovation — enter its status' },
    { id: 'lender', label: 'Lender', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'recourse', label: 'Recourse', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'prepay', label: 'Prepayment', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'extension', label: 'Extension Options', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'ratecap', label: 'Rate Cap', kind: 'awaiting', state: 'awaiting_data', value: '—' },
  ];

  // Rate-basis pill (canonical Loan Terms toggle, now live). Switching bases
  // requires the term the new basis needs — a spread for Floating, a coupon
  // for Fixed — in the same save, so the senior never lands in a pending
  // state the schedule would silently price around.
  const rateTypeControl: ReactNode = seniorRateType ? (
    <RateTypeToggle
      rateType={seniorFloating ? 'floating' : 'fixed'}
      editable={liveMode}
      currentRate={allInRate}
      onSwitch={(patch) => onSaveOverride(patch)}
    />
  ) : 'Entered by you';

  // Credit metric cards (Debt Overview) — canonical entry-vs-stabilized split:
  // LTV · LTC · Debt Yield — Entry · Debt Yield — Stabilized · DSCR — Year 1 ·
  // DSCR — Stabilized. Entry aliases the Year-1 metrics (the debt_yield / dscr
  // covenants); stabilized reads stabilized_debt_yield / stabilized_dscr, which
  // are null until a stabilized-year source exists → those cards render "—".
  const covLtv = covByName('ltv');
  const covLtc = covByName('ltc');
  const covDy = covByName('debt_yield');
  const covDscr = covByName('dscr');
  // No threshold entered → no verdict (and the caption is the input to
  // provide); no current value → Awaiting. A verdict is never fabricated.
  const statusOf = (passes: boolean | null, hasThreshold: boolean) => ({
    status: !hasThreshold ? 'No threshold set' : passes == null ? 'Awaiting' : passes ? 'Within covenant' : 'Breach',
    statusColor: !hasThreshold || passes == null ? palette.textMuted : passes ? 'oklch(40% 0.12 155)' : 'oklch(45% 0.15 40)',
    statusBg: !hasThreshold || passes == null ? '#f5f4f0' : passes ? 'oklch(96.5% 0.03 155)' : 'oklch(96% 0.04 40)',
    needsThreshold: !hasThreshold,
  });
  const covCard = (c: DebtCovenantStatus | null, label: string, basis: string) =>
    c
      ? { label, value: covCurrent(c), basis, covenant: c.threshold != null ? covCovenantCaption(c) : 'Enter threshold →', ...statusOf(c.passes, c.threshold != null) }
      : { label, value: '—', basis, covenant: 'Enter threshold →', ...statusOf(null, false) };
  const stabCard = (
    label: string, v: number | undefined, isDscr: boolean,
    threshCov: DebtCovenantStatus | null, basis: string,
  ) => {
    // DSCR and debt yield are floors → pass when value ≥ threshold. Null (no
    // stabilized value) renders "—" with an Awaiting status — never fabricated.
    const threshold = threshCov?.threshold ?? null;
    const passes = has(v) && threshold != null ? v >= threshold : null;
    return {
      label,
      value: has(v) ? (isDscr ? `${v.toFixed(2)}x` : fmtPct(v, 1)) : '—',
      basis,
      covenant: threshold != null && threshCov ? covCovenantCaption(threshCov) : 'Enter threshold →',
      ...statusOf(passes, threshold != null),
    };
  };
  const creditMetrics = [
    covCard(covLtv, covLtv?.label ?? 'LTV', COV_BASIS.ltv),
    covCard(covLtc, covLtc?.label ?? 'LTC', COV_BASIS.ltc),
    covCard(covDy, 'Debt Yield — Entry', 'Entry NOI ÷ loan'),
    stabCard('Debt Yield — Stabilized', wStabDy, false, covDy, 'Stabilized NOI ÷ loan'),
    covCard(covDscr, 'DSCR — Year 1', COV_BASIS.dscr),
    stabCard('DSCR — Stabilized', wStabDscr, true, covDscr, 'Stabilized NOI ÷ stabilized-year debt service'),
  ];

  const financingImpact = [
    { label: 'Equity Requirement', value: mm(wEquity), source: 'Calculated in Investment', state: 'linked' as ValueState },
    { label: 'Levered IRR', value: pctv(wLeveredIrr, 1), source: 'Returns output', state: 'linked' as ValueState },
    { label: 'MOIC', value: ratio(wMoic), source: 'Returns output', state: 'linked' as ValueState },
    { label: 'Avg. DSCR', value: ratio(wAvgDscr), source: 'Calculated from this schedule', state: 'calculated' as ValueState },
  ];

  // ─── Schedule builders ────────────────────────────────────────────────
  const annualSrc = wAnnual.map((y) => ({
    label: `Year ${y.year}`,
    begin: (y.ending_balance ?? 0) + (y.principal ?? 0),
    interest: y.interest, principal: y.principal, end: y.ending_balance, ds: y.debt_service,
  }));
  const monthlySrc = wMonthly.slice(0, 24).map((m) => ({
    label: `M${m.month}`,
    begin: (m.ending_balance ?? 0) + (m.principal ?? 0),
    interest: m.interest, principal: m.principal, end: m.ending_balance, ds: m.payment,
  }));

  // A priced PACE tranche adds its (flat) debt service so Total Debt Service
  // is the same figure DSCR, Cash Flow and Returns use; monthly shows 1/12.
  const scheduleRows = (
    src: { begin: number; interest: number; principal: number; end: number; ds: number }[],
    periodsPerYear: 1 | 12,
  ): StatementRow[] => {
    const c = (v: number, color: string) => ({ text: has(v) ? fmtCurrency(v) : '—', color });
    const paceDs = has(paceDebtService) ? paceDebtService / periodsPerYear : null;
    return [
      { label: 'Beginning Balance', cells: src.map((p) => c(p.begin, prov.gray)) },
      { label: 'Draws', cells: src.map(() => ({ text: fmtCurrency(0), color: prov.muted })) },
      { label: 'Interest', cells: src.map((p) => c(p.interest, prov.gray)) },
      { label: 'Principal', cells: src.map((p) => ({ text: has(p.principal) ? fmtCurrency(p.principal) : '—', color: p.principal ? prov.gray : prov.muted })) },
      ...(paceDs != null
        ? [{ label: 'PACE Debt Service', cells: src.map(() => c(paceDs, prov.gray)) }]
        : []),
      { label: 'Ending Balance', total: true, cells: src.map((p) => c(p.end, prov.black)) },
      { label: 'Total Debt Service', total: true, cells: src.map((p) => c(p.ds + (paceDs ?? 0), prov.black)) },
    ];
  };
  const scheduleFootnote = (monthly: boolean) =>
    `${monthly ? 'Monthly · first 24 periods' : 'Annual · full term'} · senior loan balances. Draws are zero — the loan funds in full at close.${
      has(paceDebtService)
        ? ` PACE debt service is shown ${monthly ? 'at 1/12 of its annual figure' : 'flat'} and counted in Total Debt Service.`
        : paceFunded && pacePending ? ' PACE is excluded until its rate is entered.' : ''
    }`;

  return (
    <div className="flex gap-4">
      <div className="flex-1 min-w-0">
        {INTRO}
        <EngineHeader
          name="Debt Engine"
          desc="Financing mechanics for this deal — sizing, pricing, covenants and the schedule behind them, as written in the term sheet on file."
          outputs={['Loan Amount', 'DSCR', 'Debt Yield', '+1']}
          dependsOn="P&L"
          complete
          dealId={dealId}
          engineName="debt"
          runMode="all"
          onRunStart={() => setComputing(true)}
          onRunComplete={() => { setComputing(false); setRunToken(Date.now()); }}
        />

        <WhatJustHappened
          engine="debt"
          engineLabel="Debt"
          outputs={outputs}
          previous={previous}
          runToken={runToken}
        />

        {/* Manual-inputs banner (canonical) — honest about the current release:
            loan terms are entered manually, not extracted from financing docs. */}
        <div style={{
          background: 'oklch(97.5% 0.015 250)', border: `1px solid #dbe3f5`, borderRadius: 9,
          padding: '12px 16px', marginBottom: 14, display: 'flex', flexDirection: 'column', gap: 8,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            <span style={{
              display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, fontWeight: 700,
              letterSpacing: '.05em', color: palette.linkBlue, textTransform: 'uppercase', whiteSpace: 'nowrap',
            }}>
              <span style={{ width: 8, height: 8, borderRadius: '50%', background: prov.blue, display: 'inline-block' }} />
              Manual inputs · current release
            </span>
            <span style={{ fontSize: 12.5, color: palette.ink, lineHeight: 1.5 }}>
              Loan terms are entered manually in this release and are not extracted from financing documents.
              Fondok sizes the schedule, credit metrics and covenants from what you enter.
            </span>
            {/* Divergence #6 (canonical): "Edit terms →" jumps to the Full Loan
                Terms sheet on the Loan Terms & Covenants sub-tab. */}
            <button
              type="button"
              onClick={() => setTab('Loan Terms & Covenants')}
              style={{
                marginLeft: 'auto', background: palette.inkNavy, color: '#fff', border: 'none',
                borderRadius: 6, padding: '6px 13px', fontSize: 11.5, fontWeight: 600,
                cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap',
              }}
            >
              Edit terms →
            </button>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', borderTop: `1px solid #dbe3f5`, paddingTop: 8 }}>
            <span style={{
              fontSize: 9.5, fontWeight: 700, letterSpacing: '.06em', color: palette.textSecondary,
              textTransform: 'uppercase', background: '#fff', border: `1px solid ${palette.disabledBorder}`,
              borderRadius: 20, padding: '3px 9px', whiteSpace: 'nowrap',
            }}>
              Coming soon · document extraction
            </span>
            <span style={{ fontSize: 11.5, color: palette.textSecondary, lineHeight: 1.45 }}>
              Upload the term sheet or loan agreement and automatically extract pricing, amortization, covenants and fees in a future release.
            </span>
          </div>
        </div>

        <SubTabNav
          items={SUB_TABS}
          activeId={tab}
          onSelect={setTab}
          caption={SUB_CAPTION[tab]}
          style={{ marginBottom: 14 }}
        />

        <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
          {/* ─── Debt Overview ─────────────────────────────────────────── */}
          {tab === 'Debt Overview' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(430px,1fr))', gap: 14 }}>
                <SectionCard title="Capital Structure" note="Amounts and LTV are your inputs — LTC and equity are outputs">
                  {capitalStructure.map((r) => <DebtRow key={r.id} row={r} />)}
                </SectionCard>
                <SectionCard
                  title="Loan Terms"
                  note={rateTypeControl}
                >
                  {loanTerms.map((r) => <DebtRow key={r.id} row={r} />)}
                </SectionCard>
              </div>

              <SectionCard title="Credit Metrics" note="Each metric names the NOI period behind it">
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(196px,1fr))', gap: 12, marginTop: 4 }}>
                  {creditMetrics.length === 0 && (
                    <div style={{ fontSize: 12.5, color: palette.textMuted }}>Run the model to compute credit metrics.</div>
                  )}
                  {creditMetrics.map((m) => (
                    <div key={m.label} style={{ border: `1px solid ${palette.border}`, borderRadius: 8, padding: '12px 14px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8 }}>
                        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.04em', color: palette.eyebrow, textTransform: 'uppercase' }}>{m.label}</span>
                        <span style={{ fontSize: 19, fontWeight: 700, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>{m.value}</span>
                      </div>
                      <div style={{ fontSize: 10.5, color: palette.textMuted, marginTop: 5, lineHeight: 1.4 }}>{m.basis}</div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginTop: 8 }}>
                        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.03em', textTransform: 'uppercase', color: m.statusColor, background: m.statusBg, borderRadius: 5, padding: '3px 7px' }}>{m.status}</span>
                        {m.needsThreshold ? (
                          <button
                            type="button"
                            onClick={() => setTab('Loan Terms & Covenants')}
                            style={{ fontSize: 10.5, color: palette.linkBlue, fontWeight: 600, background: 'none', border: 'none', padding: 0, cursor: 'pointer', fontFamily: 'inherit' }}
                          >
                            {m.covenant}
                          </button>
                        ) : (
                          <span style={{ fontSize: 10.5, color: palette.textMuted }}>{m.covenant}</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </SectionCard>

              <SectionCard
                variant="title"
                title="Debt Schedule — Annual"
                note={<a href="?tab=debt" onClick={(e) => { e.preventDefault(); setTab('Debt Schedule'); }} style={{ color: palette.linkBlue, fontWeight: 600, cursor: 'pointer', textDecoration: 'none' }}>Monthly detail in Debt Schedule →</a>}
              >
                {annualSrc.length > 0 ? (
                  <StatementTable
                    columns={annualSrc.map((y) => y.label)}
                    rows={scheduleRows(annualSrc, 1)}
                    showDots={false}
                    gridTemplateColumns={`190px repeat(${annualSrc.length},minmax(120px,1fr))`}
                    footnote={has(paceDebtService) || (paceFunded && pacePending) ? scheduleFootnote(false) : undefined}
                  />
                ) : (
                  <div style={{ padding: '14px 18px', fontSize: 12.5, color: palette.textMuted }}>Run the model to build the schedule.</div>
                )}
              </SectionCard>

              <SectionCard
                title="Financing Impact on Returns"
                note={<a href="?tab=returns" style={{ color: palette.linkBlue, fontWeight: 600, cursor: 'pointer', textDecoration: 'none' }}>View Returns →</a>}
              >
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: 12, marginTop: 4 }}>
                  {financingImpact.map((m) => (
                    <div key={m.label} style={{ border: `1px solid ${palette.border}`, borderRadius: 8, padding: '12px 14px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                        <ProvenanceDot state={m.state} size={8} />
                        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.04em', color: palette.eyebrow, textTransform: 'uppercase' }}>{m.label}</span>
                      </div>
                      <div style={{ fontSize: 19, fontWeight: 700, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>{m.value}</div>
                      <div style={{ fontSize: 10.5, color: palette.textMuted, marginTop: 4 }}>{m.source}</div>
                    </div>
                  ))}
                </div>
                <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 10, lineHeight: 1.5 }}>
                  Levered IRR and MOIC are Returns outputs, not Debt assumptions — Debt supplies the debt service and balances behind them.
                </div>
              </SectionCard>
            </div>
          )}

          {/* ─── Loan Terms & Covenants ────────────────────────────────── */}
          {tab === 'Loan Terms & Covenants' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(430px,1fr))', gap: 14 }}>
              <SectionCard title="Full Loan Terms" note="Your inputs — Fondok does not read the term sheet in this release">
                {fullTerms.map((r) => <DebtRow key={r.id} row={r} />)}
              </SectionCard>
              <SectionCard title="Covenants" note="Thresholds are your inputs — tested on the modeled Year-1 metrics">
                <div style={{
                  display: 'grid', gridTemplateColumns: 'minmax(150px,1.3fr) minmax(90px,auto) 90px minmax(88px,1fr)',
                  fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: palette.textFaint,
                  textTransform: 'uppercase', paddingBottom: 7, borderBottom: `1px solid ${palette.border}`, marginTop: 4,
                }}>
                  <span>Covenant</span>
                  <span style={{ textAlign: 'right' }}>Threshold</span>
                  <span style={{ textAlign: 'right' }}>Current</span>
                  <span style={{ textAlign: 'right' }}>Headroom</span>
                </div>
                {wCovenants.length === 0 && (
                  <div style={{ fontSize: 12.5, color: palette.textMuted, padding: '10px 0' }}>Run the model to test covenants.</div>
                )}
                {wCovenants.map((c) => {
                  // Threshold = the analyst's entered covenant (persisted on the
                  // exact key the engine reads); none entered → the input to
                  // provide, and no headroom / verdict.
                  const key = COVENANT_KEY[c.name];
                  const isDscr = c.name === 'dscr';
                  const hasThreshold = c.threshold != null;
                  return (
                    <div key={c.name}>
                      <div style={{
                        display: 'grid', gridTemplateColumns: 'minmax(150px,1.3fr) minmax(90px,auto) 90px minmax(88px,1fr)',
                        fontSize: 12.5, padding: '7px 0', borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center',
                      }}>
                        <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                          <ProvenanceDot state={hasThreshold ? 'assumption' : 'awaiting_data'} size={8} />
                          <span style={{ color: palette.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.label}</span>
                        </span>
                        <span style={{ display: 'flex', justifyContent: 'flex-end', fontVariantNumeric: 'tabular-nums' }}>
                          <EditableValue
                            display={covThreshold(c)}
                            emptyLabel="Enter threshold"
                            draftValue={c.threshold == null ? '' : isDscr ? c.threshold.toFixed(2) : (c.threshold * 100).toFixed(1)}
                            parse={isDscr ? parseRatio : parsePctFrac}
                            onSave={(v) => onSaveOverride({ [key]: v > 0 ? v : null })}
                            editable={liveMode}
                            suffix={isDscr ? 'x' : '%'}
                            color={valueColor('input', false, overridden(key))}
                            testId={`edit-cov-${c.name}`}
                            title={`Click to change — the lender’s ${c.kind === 'max' ? 'maximum' : 'minimum'} ${c.label}`}
                          />
                        </span>
                        <span style={{ textAlign: 'right', color: palette.ink, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{covCurrent(c)}</span>
                        <span style={{ textAlign: 'right', color: covHeadroomColor(c), fontVariantNumeric: 'tabular-nums' }}>{covHeadroom(c)}</span>
                      </div>
                      {!hasThreshold && (
                        <div style={{ fontSize: 10.5, color: palette.textMuted, padding: '0 0 6px 15px', lineHeight: 1.45 }}>
                          No threshold entered — no pass / fail until you enter the lender’s {c.kind === 'max' ? 'ceiling' : 'floor'}
                        </div>
                      )}
                    </div>
                  );
                })}
                {/* Completion Guarantee — qualitative covenant. Reads the
                    analyst-entered status (set in Full Loan Terms); "—" until
                    entered (never a fabricated status). */}
                {wCovenants.length > 0 && (
                  <div style={{
                    display: 'grid', gridTemplateColumns: 'minmax(150px,1.3fr) 90px 90px minmax(88px,1fr)',
                    fontSize: 12.5, padding: '7px 0', borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center',
                  }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                      <ProvenanceDot state={completionGuarantee ? 'assumption' : 'awaiting_data'} size={8} />
                      <span style={{ color: palette.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>Completion Guarantee</span>
                    </span>
                    <span style={{ textAlign: 'right', color: palette.textSecondary, fontVariantNumeric: 'tabular-nums' }}>—</span>
                    <span style={{ textAlign: 'right', color: completionGuarantee ? palette.linkBlue : palette.ink, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{cgLabel(completionGuarantee)}</span>
                    <span style={{ textAlign: 'right', color: palette.textMuted, fontVariantNumeric: 'tabular-nums' }}>—</span>
                  </div>
                )}
                <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>
                  LTV and LTC are ceilings; DSCR and debt yield are floors. Headroom is signed room toward a breach.
                  Thresholds are not read from loan documents in this release — enter the lender’s package here.
                </div>
              </SectionCard>
              <SectionCard
                title="PACE Loan · Tranche 2"
                note={!paceFunded ? 'Not funded' : pacePending ? 'Terms pending' : 'Priced'}
              >
                {paceTerms.map((r) => <DebtRow key={r.id} row={r} />)}
                <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>
                  Fixed-rate tranche. A funded PACE loan counts in Total Debt, LTV, LTC and debt yield at once;
                  its debt service enters DSCR, Cash Flow and Returns once a rate is entered.
                </div>
              </SectionCard>
            </div>
          )}

          {/* ─── Refinance ─────────────────────────────────────────────── */}
          {tab === 'Refinance' && (
            <RefinanceView
              active={refiActive}
              liveMode={liveMode}
              refiYear={wRefiYear}
              refiYearOverride={refiYearOverride}
              refiCashOut={wRefiCashOut}
              balanceAtExit={wBalanceAtExit}
              leveredIrr={wLeveredIrr}
              refiValue={wRefiValue}
              refiLtv={wRefiLtv}
              refiProceeds={wRefiProceeds}
              refiPayoff={wRefiPayoff}
              refiRate={wRefiRate}
              refiCosts={wRefiCosts}
              onSaveOverride={onSaveOverride}
              toast={toast}
            />
          )}

          {/* ─── Debt Schedule ─────────────────────────────────────────── */}
          {tab === 'Debt Schedule' && (
            <SectionCard
              variant="title"
              title="Debt Schedule"
              note={
                <span style={{ display: 'inline-flex', gap: 10, alignItems: 'center' }}>
                  <Pill options={['Annual', 'Monthly']} value={period} onSelect={(v) => setPeriod(v as 'Annual' | 'Monthly')} />
                  <Pill options={['Consolidated', 'By tranche']} value="Consolidated" onSelect={() => { /* single tranche */ }} disabled={['By tranche']} />
                </span>
              }
            >
              {(period === 'Annual' ? annualSrc : monthlySrc).length > 0 ? (
                <StatementTable
                  columns={(period === 'Annual' ? annualSrc : monthlySrc).map((y) => y.label)}
                  rows={scheduleRows(period === 'Annual' ? annualSrc : monthlySrc, period === 'Annual' ? 1 : 12)}
                  showDots={false}
                  gridTemplateColumns={`190px repeat(${(period === 'Annual' ? annualSrc : monthlySrc).length},minmax(104px,1fr))`}
                  footnote={scheduleFootnote(period === 'Monthly')}
                />
              ) : (
                <div style={{ padding: '14px 18px', fontSize: 12.5, color: palette.textMuted }}>Run the Debt engine to populate the schedule.</div>
              )}
            </SectionCard>
          )}

          {computing && (
            <div className="absolute inset-0 bg-bg/60 backdrop-blur-[1px] flex items-start justify-center pt-12 rounded-md">
              <span className="inline-flex items-center gap-2 px-3 py-1.5 bg-white border border-border rounded-md shadow-card text-[12.5px] font-medium text-ink-700">
                <span className="w-1.5 h-1.5 rounded-full bg-brand-500 animate-pulse" />
                Recomputing…
              </span>
            </div>
          )}
        </div>
        <EngineRunHistory dealId={dealId} seedDemo />
      </div>
      <EngineRightRail />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Row — canonical dot · label · link · value.
// ─────────────────────────────────────────────────────────────────────
function DebtRow({ row }: { row: RowDef }) {
  const color = valueColor(row.kind, !!row.bold, !!row.overridden);
  const valueIsNode = typeof row.value !== 'string' && typeof row.value !== 'number';
  // Provenance badge (shared AssumptionBadge vocabulary): an analyst edit
  // always reads "Override"; an untouched seed / deal-row term says which.
  const badgeSource = row.overridden ? 'analyst_override' : row.source ?? undefined;
  return (
    <>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
        fontSize: 13, padding: '7px 0', borderBottom: `1px solid ${palette.hairlineRow}`,
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
          <ProvenanceDot state={row.state} size={8} />
          <span style={{ color: palette.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.label}</span>
          {row.link && (
            <a href={`?tab=${row.link.tab}`} style={{ fontSize: 10.5, color: palette.linkBlue, fontWeight: 600, whiteSpace: 'nowrap', textDecoration: 'none' }}>{row.link.label}</a>
          )}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
          {badgeSource && <AssumptionBadge source={badgeSource} />}
          {valueIsNode ? (
            <span>{row.value}</span>
          ) : (
            <span style={{ color, fontWeight: row.bold ? 700 : 400, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{row.value}</span>
          )}
        </span>
      </div>
      {row.note && (
        <div style={{ fontSize: 10.5, color: palette.textMuted, padding: '0 0 6px 15px', lineHeight: 1.45 }}>{row.note}</div>
      )}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Inline editable value — canonical blue dotted-underline → input + Save.
// On save it hands the parsed number to onSave (which PATCHes
// field_overrides + re-runs). Read-only when not editable.
// ─────────────────────────────────────────────────────────────────────
function EditableValue({
  display, emptyLabel, draftValue, parse, onSave, editable, suffix, bold, color, testId, title,
}: {
  display: string;
  /** Shown in place of a bare "—" when the value is missing and editable —
   *  the input the analyst needs to provide (e.g. "Enter rate"). */
  emptyLabel?: string;
  draftValue: string;
  parse: (s: string) => number | null;
  onSave: (v: number) => void | Promise<void>;
  editable: boolean;
  suffix?: string;
  bold?: boolean;
  color?: string;
  testId?: string;
  title?: string;
}) {
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const textColor = color ?? prov.blue;
  const isEmpty = display === '—' || display === '';

  if (!editable) {
    return <span style={{ color: isEmpty ? prov.muted : textColor, fontWeight: bold ? 700 : 400, fontVariantNumeric: 'tabular-nums' }}>{display}</span>;
  }
  if (!editing) {
    const showAffordance = isEmpty && !!emptyLabel;
    return (
      <span
        data-testid={testId}
        onClick={() => { setDraft(draftValue); setEditing(true); }}
        title={title ?? (showAffordance ? 'Required input — click to enter' : 'Click to change — Debt owns this term')}
        style={{
          color: showAffordance ? prov.blue : textColor, fontWeight: showAffordance ? 600 : bold ? 700 : 500,
          fontVariantNumeric: 'tabular-nums', textDecoration: 'underline dotted', cursor: 'pointer',
        }}
      >
        {showAffordance ? emptyLabel : display}
      </span>
    );
  }
  const submit = async () => {
    const parsed = parse(draft);
    if (parsed == null) { toast('Enter a valid number.', { type: 'error' }); return; }
    setSaving(true);
    try { await onSave(parsed); setEditing(false); } finally { setSaving(false); }
  };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <input
        type="number" value={draft} autoFocus disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') void submit(); if (e.key === 'Escape') setEditing(false); }}
        style={{ width: 120, fontSize: 12.5, fontFamily: 'inherit', border: `1px solid ${palette.linkBlue}`, borderRadius: 5, padding: '4px 7px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
      />
      {suffix && <span style={{ fontSize: 11, color: palette.textMuted }}>{suffix}</span>}
      <button type="button" onClick={() => void submit()} disabled={saving}
        style={{ background: palette.inkNavy, color: '#fff', border: 'none', borderRadius: 5, padding: '5px 9px', fontSize: 11, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
        Save
      </button>
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Segmented pill toggle (canonical `pill()` control).
// ─────────────────────────────────────────────────────────────────────
function Pill({
  options, value, onSelect, disabled = [], disabledTitle,
}: {
  options: string[];
  value: string;
  onSelect: (v: string) => void;
  disabled?: string[];
  disabledTitle?: string;
}) {
  return (
    <span style={{ display: 'inline-flex', background: '#eeede8', border: `1px solid ${palette.disabledBorder}`, borderRadius: 7, padding: 2, gap: 2 }}>
      {options.map((o) => {
        const active = o === value;
        const isDisabled = disabled.includes(o);
        return (
          <button key={o} type="button" disabled={isDisabled}
            onClick={() => !isDisabled && onSelect(o)}
            title={isDisabled ? (disabledTitle ?? 'Single tranche — senior loan only.') : undefined}
            style={{
              fontSize: 11.5, fontFamily: 'inherit', border: 'none', cursor: isDisabled ? 'not-allowed' : 'pointer',
              fontWeight: active ? 700 : 500, color: active ? palette.inkNavy : palette.eyebrow,
              background: active ? '#fff' : 'transparent', borderRadius: 5, padding: '4px 11px',
              boxShadow: active ? '0 1px 2px rgba(0,0,0,.09)' : 'none', opacity: isDisabled ? 0.5 : 1,
            }}>
            {o}
          </button>
        );
      })}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Floating / Fixed rate-basis pill (canonical Loan Terms toggle, live).
// Picking the other basis opens the one input that basis needs — a spread
// over the index for Floating, a coupon for Fixed — and saves both keys
// together (`debt_stack.tranches.0.rate_type` + `spread_pct` / `rate_pct`).
// The engine prices Floating off index + spread (clamped to floor / cap), so a
// switch without the spread would leave the senior pending; requiring it here
// keeps the schedule honest. The Fixed draft pre-fills the current all-in rate
// for the analyst to confirm or change (nothing is invented on save).
// ─────────────────────────────────────────────────────────────────────
function RateTypeToggle({
  rateType, editable, currentRate, onSwitch,
}: {
  rateType: 'fixed' | 'floating';
  editable: boolean;
  currentRate?: number;
  onSwitch: (patch: Record<string, number | string | null>) => void | Promise<void>;
}) {
  const { toast } = useToast();
  const [pending, setPending] = useState<'fixed' | 'floating' | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const active = rateType === 'floating' ? 'Floating' : 'Fixed';

  const begin = (option: string) => {
    const next: 'fixed' | 'floating' = option === 'Floating' ? 'floating' : 'fixed';
    if (next === rateType) { setPending(null); return; }
    if (!editable) { toast('Editing is disabled on demo deals', { type: 'info' }); return; }
    setDraft(next === 'fixed' && has(currentRate) ? (currentRate * 100).toFixed(2) : '');
    setPending(next);
  };
  const submit = async () => {
    if (!pending) return;
    const frac = parsePctFrac(draft);
    if (frac == null) {
      toast(pending === 'floating' ? 'Enter the spread over the index to switch to floating.' : 'Enter the fixed rate to switch.', { type: 'error' });
      return;
    }
    setSaving(true);
    try {
      await onSwitch(
        pending === 'floating'
          ? { [tk(SENIOR, 'rate_type')]: 'floating', [tk(SENIOR, 'spread_pct')]: frac }
          : { [tk(SENIOR, 'rate_type')]: 'fixed', [tk(SENIOR, 'rate_pct')]: frac },
      );
      setPending(null);
    } finally {
      setSaving(false);
    }
  };

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
      <Pill options={['Floating', 'Fixed']} value={active} onSelect={begin} />
      {pending && (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 11, color: palette.textSecondary, whiteSpace: 'nowrap' }}>
            {pending === 'floating' ? 'Spread over SOFR' : 'Fixed rate'}
          </span>
          <input
            type="number" value={draft} autoFocus disabled={saving} data-testid="rate-basis-input"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') void submit(); if (e.key === 'Escape') setPending(null); }}
            style={{ width: 90, fontSize: 12.5, fontFamily: 'inherit', border: `1px solid ${palette.linkBlue}`, borderRadius: 5, padding: '4px 7px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
          />
          <span style={{ fontSize: 11, color: palette.textMuted }}>%</span>
          <button type="button" onClick={() => void submit()} disabled={saving} data-testid="rate-basis-save"
            style={{ background: palette.inkNavy, color: '#fff', border: 'none', borderRadius: 5, padding: '5px 9px', fontSize: 11, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
            Save
          </button>
          <button type="button" onClick={() => setPending(null)} disabled={saving}
            style={{ background: 'none', color: palette.textSecondary, border: `1px solid ${palette.disabledBorder}`, borderRadius: 5, padding: '4px 8px', fontSize: 11, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
            Cancel
          </button>
        </span>
      )}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Completion Guarantee — segmented Required / In place / Not required. A
// qualitative covenant input; persists debt.completion_guarantee via the
// override save path. Read-only (status text) on demo deals.
// ─────────────────────────────────────────────────────────────────────
function CompletionGuaranteeControl({
  value, editable, onSave,
}: {
  value: string | undefined;
  editable: boolean;
  onSave: (v: string) => void | Promise<void>;
}) {
  if (!editable) {
    return (
      <span style={{ color: value ? prov.blue : prov.muted, fontVariantNumeric: 'tabular-nums' }}>
        {cgLabel(value)}
      </span>
    );
  }
  return (
    <span style={{ display: 'inline-flex', background: '#eeede8', border: `1px solid ${palette.disabledBorder}`, borderRadius: 7, padding: 2, gap: 2 }}>
      {CG_OPTIONS.map((o) => {
        const active = o.key === value;
        return (
          <button
            key={o.key}
            type="button"
            data-testid={`cg-${o.key}`}
            onClick={() => { if (!active) void onSave(o.key); }}
            style={{
              fontSize: 11, fontFamily: 'inherit', border: 'none', cursor: 'pointer',
              fontWeight: active ? 700 : 500, color: active ? palette.inkNavy : palette.eyebrow,
              background: active ? '#fff' : 'transparent', borderRadius: 5, padding: '3px 9px',
              boxShadow: active ? '0 1px 2px rgba(0,0,0,.09)' : 'none',
            }}
          >
            {o.label}
          </button>
        );
      })}
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Refinance view — banner + include/remove toggle · assumptions · impact.
// Reads refi fields from the debt output; values the engine doesn't emit
// render as canonical "awaiting data" em-dashes (never fabricated).
// ─────────────────────────────────────────────────────────────────────
function RefinanceView({
  active, liveMode, refiYear, refiYearOverride, refiCashOut, balanceAtExit, leveredIrr,
  refiValue, refiLtv, refiProceeds, refiPayoff, refiRate, refiCosts,
  onSaveOverride, toast,
}: {
  active: boolean;
  liveMode: boolean;
  refiYear?: number;
  refiYearOverride: number;
  refiCashOut?: number;
  balanceAtExit?: number;
  leveredIrr?: number;
  refiValue?: number;
  refiLtv?: number;
  refiProceeds?: number;
  refiPayoff?: number;
  refiRate?: number;
  refiCosts?: number;
  onSaveOverride: (patch: Record<string, number | string | null>) => void | Promise<void>;
  toast: ReturnType<typeof useToast>['toast'];
}) {
  const bannerColor = active ? 'oklch(40% 0.12 155)' : palette.eyebrow;
  const bannerBg = active ? 'oklch(96.5% 0.03 155)' : palette.surfaceTint;
  const bannerBorder = active ? 'oklch(88% 0.05 155)' : palette.border;

  const toggle = () => {
    if (!liveMode) { toast('Editing is disabled on demo deals', { type: 'info' }); return; }
    if (active) { void onSaveOverride({ 'debt_stack.refi_test_year': null }); return; }
    if (refiYearOverride > 0) { void onSaveOverride({ 'debt_stack.refi_test_year': Math.round(refiYearOverride) }); return; }
    toast('Set a refinance year below to include the refinance.', { type: 'info' });
  };

  const detailRow = (
    id: string, label: string, present: boolean, value: string,
    opts: { kind?: ValueKind; state?: ValueState; bold?: boolean } = {},
  ): RowDef =>
    present
      ? { id, label, kind: opts.kind ?? 'calc', state: opts.state ?? 'calculated', value, bold: opts.bold }
      : { id, label, kind: 'awaiting', state: 'awaiting_data', value: '—' };

  const refiRows: RowDef[] = [
    {
      id: 'refiYear', label: 'Refinance Year', kind: 'input',
      state: refiYearOverride > 0 ? 'assumption' : 'awaiting_data',
      overridden: refiYearOverride > 0,
      value: (
        <EditableValue
          display={refiYearOverride > 0 ? `Year ${Math.round(refiYearOverride)}` : '—'}
          draftValue={refiYearOverride > 0 ? String(Math.round(refiYearOverride)) : ''}
          parse={(s) => { const n = parseInt(s, 10); return Number.isFinite(n) && n >= 1 ? n : null; }}
          onSave={(v) => onSaveOverride({ 'debt_stack.refi_test_year': Math.round(v) })}
          editable={liveMode}
          color={valueColor('input', false, refiYearOverride > 0)}
          testId="edit-refi-year"
        />
      ),
      note: 'Blank = single-phase deal. Sets the mid-hold refinance year the engine sizes off.',
    },
    // Canonical refinance detail — every value the refi model already computes.
    // Absent (no-refi deal) → canonical "awaiting data" em-dashes, never faked.
    detailRow('refiValue', 'Value at Refinance', has(refiValue), money(refiValue)),
    detailRow('refiLtv', 'Refinance LTV', has(refiLtv), pctv(refiLtv, 1)),
    detailRow('refiProceeds', 'New Loan Proceeds', has(refiProceeds), money(refiProceeds), { bold: true }),
    detailRow('refiPayoff', 'Existing Balance Repaid', has(refiPayoff), money(refiPayoff)),
    detailRow('refiRate', 'New Interest Rate', has(refiRate), pctv(refiRate, 2), { kind: 'input', state: 'assumption' }),
    detailRow('refiCost', 'Financing Costs', has(refiCosts), money(refiCosts)),
  ];

  const impact = [
    { label: 'Cash-Out to Equity', value: money(refiCashOut), color: prov.green, sub: 'Net proceeds returned to equity', avail: has(refiCashOut) },
    { label: 'Balance at Exit', value: money(balanceAtExit), color: prov.black, sub: 'Loan balance carried to sale', avail: has(balanceAtExit) },
    { label: 'Equity Returned', value: '—', color: prov.muted, sub: 'Awaiting the equity basis', avail: false },
    { label: 'Levered IRR', value: pctv(leveredIrr, 1), color: has(leveredIrr) ? prov.green : prov.muted, sub: 'Returns output', avail: has(leveredIrr) },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
        background: bannerBg, border: `1px solid ${bannerBorder}`, borderRadius: 8, padding: '10px 14px',
      }}>
        <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: bannerColor, textTransform: 'uppercase' }}>
          {active ? 'Included in the model' : 'Excluded from the model'}
        </span>
        <span style={{ fontSize: 12.5, color: palette.ink }}>
          {active
            ? 'The refinance is running in the model — proceeds, new debt service and returns reflect it.'
            : 'These assumptions are held aside. Nothing here affects Cash Flow or Returns until you include it.'}
        </span>
        <button type="button" onClick={toggle}
          style={{
            marginLeft: 'auto', background: active ? '#fff' : palette.inkNavy, color: active ? palette.hoverInk : '#fff',
            border: `1px solid ${active ? palette.disabledBorder : palette.inkNavy}`, borderRadius: 6,
            padding: '6px 13px', fontSize: 11.5, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
          }}>
          {active ? 'Remove from model' : 'Include in model'}
        </button>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(430px,1fr))', gap: 14, opacity: active ? 1 : 0.85 }}>
        <SectionCard title="Refinance Assumptions">
          {refiRows.map((r) => <DebtRow key={r.id} row={r} />)}
        </SectionCard>
        <SectionCard title="Refinance Impact">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: 12, marginTop: 4 }}>
            {impact.map((m) => (
              <div key={m.label} style={{ border: `1px solid ${palette.border}`, borderRadius: 8, padding: '12px 14px' }}>
                <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.04em', color: palette.eyebrow, textTransform: 'uppercase', marginBottom: 5 }}>{m.label}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: m.color, fontVariantNumeric: 'tabular-nums' }}>{m.value}</div>
                <div style={{ fontSize: 10.5, color: palette.textMuted, marginTop: 4 }}>{m.sub}</div>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 10, lineHeight: 1.5 }}>
            {refiYear != null && `Refinance modeled in Year ${refiYear}. `}
            Cash-out and exit balance come from the Debt engine; the levered IRR is a Returns output.
          </div>
        </SectionCard>
      </div>
    </div>
  );
}
