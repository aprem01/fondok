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
 *
 * Edits take the canonical path: PATCH field_overrides then a debounced full
 * run so DSCR / leverage / returns re-derive. LTV resizes the senior tranche
 * (`debt_stack.tranches.0.principal_usd`); the origination fee writes
 * `debt_stack.tranches.0.upfront_fee_pct` (0..10 percent convention).
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
interface DebtStackLite {
  tranches?: { kind: string; rate_type: string }[];
}

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
  note?: string;
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
    async (patch: Record<string, number | null>) => {
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

  const seniorRateType = wStack?.tranches?.find((t) => t.kind === 'senior')?.rate_type
    ?? wStack?.tranches?.[0]?.rate_type;

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

  // Editable LTV — Debt owns it (Investment dropped its LTV). Editing resizes
  // the senior tranche principal so LTV = loan ÷ property value re-derives.
  const ltvEditable = liveMode && has(wPurchase) && wPurchase > 0;
  const ltvOverridden = overridden('debt_stack.tranches.0.principal_usd');
  const ltvNode = (
    <EditableValue
      display={pctv(ltvN, 1)}
      draftValue={has(ltvN) ? (ltvN * 100).toFixed(1) : ''}
      parse={(s) => { const n = parseFloat(s); return Number.isFinite(n) && n > 0 ? n / 100 : null; }}
      onSave={(frac) => onSaveOverride({
        'debt_stack.tranches.0.principal_usd': Math.round(frac * (wPurchase as number)),
      })}
      editable={ltvEditable}
      suffix="%"
      color={valueColor('input', false, ltvOverridden)}
      testId="edit-ltv"
    />
  );

  // Editable origination fee — writes the senior tranche upfront fee (percent).
  const feeEditable = liveMode;
  const feeOverridden = overridden('debt_stack.tranches.0.upfront_fee_pct');
  const origFeeDisplay = has(wOrigFeePct)
    ? `${wOrigFeePct.toFixed(2)}%${has(wOrigFeeUsd) ? ` · ${fmtCurrency(wOrigFeeUsd)}` : ''}`
    : '—';
  const origFeeNode = (
    <EditableValue
      display={origFeeDisplay}
      draftValue={has(wOrigFeePct) ? wOrigFeePct.toFixed(2) : ''}
      parse={(s) => { const n = parseFloat(s); return Number.isFinite(n) && n >= 0 ? n : null; }}
      onSave={(v) => onSaveOverride({ 'debt_stack.tranches.0.upfront_fee_pct': v })}
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
    { id: 'loan', label: 'Senior Loan Amount', kind: 'calc',
      state: overridden('debt_stack.tranches.0.principal_usd') ? 'assumption' : 'document_sourced',
      value: money(loanN), overridden: overridden('debt_stack.tranches.0.principal_usd'),
      note: 'Sized in Debt from the term sheet — edit LTV to resize it' },
    { id: 'ltv', label: 'LTV', kind: 'input',
      state: ltvOverridden ? 'assumption' : (tracedState('debt', 'ltv') ?? 'calculated'),
      value: ltvNode, overridden: ltvOverridden,
      note: 'Debt owns this input — loan ÷ property value' },
    { id: 'ltc', label: 'LTC', kind: 'calc',
      state: tracedState('capital', 'ltc') ?? 'calculated', value: pctv(ltcN, 1) },
    { id: 'equity', label: 'Equity Requirement', kind: 'calc', bold: true, state: 'linked',
      value: money(wEquity), link: { label: '→ Investment', tab: 'investment' } },
  ];

  const loanTerms: RowDef[] = [
    { id: 'rate', label: 'Interest Rate', kind: 'calc', state: 'assumption', value: pctv(wRate, 2) },
    { id: 'amort', label: 'Amortization', kind: 'calc', state: 'assumption',
      value: has(wAmortYears) ? `${wAmortYears} years` : '—' },
    { id: 'term', label: 'Maturity', kind: 'calc', state: 'assumption',
      value: has(wTermYears) ? `${wTermYears} years` : '—' },
    { id: 'io', label: 'Interest-Only Period', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'orig', label: 'Origination Fee', kind: 'input',
      state: feeOverridden ? 'assumption' : 'assumption', value: origFeeNode, overridden: feeOverridden },
  ];

  // ─── Loan Terms & Covenants — full term sheet (descriptive fields await
  //     the loan doc, deferred out of MVP scope → honest em-dashes). ──────
  const fullTerms: RowDef[] = [
    ...loanTerms,
    { id: 'exit', label: 'Exit Fee', kind: 'calc', state: 'assumption',
      value: has(wExitFeePct) ? `${feePct(wExitFeePct)}${has(wExitFeeUsd) ? ` · ${fmtCurrency(wExitFeeUsd)}` : ''}` : '—' },
    { id: 'lender', label: 'Lender', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'recourse', label: 'Recourse', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'prepay', label: 'Prepayment', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'extension', label: 'Extension Options', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'ratecap', label: 'Rate Cap', kind: 'awaiting', state: 'awaiting_data', value: '—' },
  ];

  // Credit metric cards (Debt Overview) — read straight from covenants[].
  const creditMetrics = wCovenants.map((c) => ({
    label: c.label,
    value: covCurrent(c),
    basis: COV_BASIS[c.name] ?? '',
    covenant: covCovenantCaption(c),
    status: c.passes == null ? 'Awaiting' : c.passes ? 'Within covenant' : 'Breach',
    statusColor: c.passes == null ? palette.textMuted : c.passes ? 'oklch(40% 0.12 155)' : 'oklch(45% 0.15 40)',
    statusBg: c.passes == null ? '#f5f4f0' : c.passes ? 'oklch(96.5% 0.03 155)' : 'oklch(96% 0.04 40)',
  }));

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

  const scheduleRows = (
    src: { begin: number; interest: number; principal: number; end: number; ds: number }[],
  ): StatementRow[] => {
    const c = (v: number, color: string) => ({ text: has(v) ? fmtCurrency(v) : '—', color });
    return [
      { label: 'Beginning Balance', cells: src.map((p) => c(p.begin, prov.gray)) },
      { label: 'Draws', cells: src.map(() => ({ text: fmtCurrency(0), color: prov.muted })) },
      { label: 'Interest', cells: src.map((p) => c(p.interest, prov.gray)) },
      { label: 'Principal', cells: src.map((p) => ({ text: has(p.principal) ? fmtCurrency(p.principal) : '—', color: p.principal ? prov.gray : prov.muted })) },
      { label: 'Ending Balance', total: true, cells: src.map((p) => c(p.end, prov.black)) },
      { label: 'Total Debt Service', total: true, cells: src.map((p) => c(p.ds, prov.black)) },
    ];
  };

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
                <SectionCard title="Capital Structure" note="Loan amount is your input — LTV and LTC are outputs">
                  {capitalStructure.map((r) => <DebtRow key={r.id} row={r} />)}
                </SectionCard>
                <SectionCard
                  title="Loan Terms"
                  note={seniorRateType ? `${seniorRateType === 'floating' ? 'Floating' : 'Fixed'}-rate senior` : 'Entered by you'}
                >
                  {loanTerms.map((r) => <DebtRow key={r.id} row={r} />)}
                </SectionCard>
              </div>

              <SectionCard title="Credit Metrics" note="Each metric names the covenant behind it">
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
                        <span style={{ fontSize: 10.5, color: palette.textMuted }}>{m.covenant}</span>
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
                    rows={scheduleRows(annualSrc)}
                    showDots={false}
                    gridTemplateColumns={`190px repeat(${annualSrc.length},minmax(120px,1fr))`}
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
              <SectionCard title="Full Loan Terms" note="Entered by you — Fondok does not read the term sheet in this release">
                {fullTerms.map((r) => <DebtRow key={r.id} row={r} />)}
              </SectionCard>
              <SectionCard title="Covenants" note="Current reading vs each threshold">
                <div style={{
                  display: 'grid', gridTemplateColumns: 'minmax(150px,1.3fr) 90px 90px minmax(88px,1fr)',
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
                {wCovenants.map((c) => (
                  <div key={c.name}>
                    <div style={{
                      display: 'grid', gridTemplateColumns: 'minmax(150px,1.3fr) 90px 90px minmax(88px,1fr)',
                      fontSize: 12.5, padding: '7px 0', borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center',
                    }}>
                      <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                        <ProvenanceDot state={c.current == null ? 'awaiting_data' : 'document_sourced'} size={8} />
                        <span style={{ color: palette.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.label}</span>
                      </span>
                      <span style={{ textAlign: 'right', color: palette.textSecondary, fontVariantNumeric: 'tabular-nums' }}>{covThreshold(c)}</span>
                      <span style={{ textAlign: 'right', color: palette.ink, fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{covCurrent(c)}</span>
                      <span style={{ textAlign: 'right', color: covHeadroomColor(c), fontVariantNumeric: 'tabular-nums' }}>{covHeadroom(c)}</span>
                    </div>
                  </div>
                ))}
                <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>
                  LTV and LTC are ceilings; DSCR and debt yield are floors. Headroom is signed room toward a breach.
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
                  rows={scheduleRows(period === 'Annual' ? annualSrc : monthlySrc)}
                  showDots={false}
                  gridTemplateColumns={`190px repeat(${(period === 'Annual' ? annualSrc : monthlySrc).length},minmax(104px,1fr))`}
                  footnote={period === 'Monthly'
                    ? 'Monthly · first 24 periods · senior loan. Draws are zero — the loan funds in full at close.'
                    : 'Annual · full term · senior loan. Draws are zero — the loan funds in full at close.'}
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
        {valueIsNode ? (
          <span style={{ flexShrink: 0 }}>{row.value}</span>
        ) : (
          <span style={{ color, fontWeight: row.bold ? 700 : 400, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', flexShrink: 0 }}>{row.value}</span>
        )}
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
  display, draftValue, parse, onSave, editable, suffix, bold, color, testId, title,
}: {
  display: string;
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

  if (!editable) {
    return <span style={{ color: textColor, fontWeight: bold ? 700 : 400, fontVariantNumeric: 'tabular-nums' }}>{display}</span>;
  }
  if (!editing) {
    return (
      <span
        data-testid={testId}
        onClick={() => { setDraft(draftValue); setEditing(true); }}
        title={title ?? 'Click to change — Debt owns this term'}
        style={{ color: textColor, fontWeight: bold ? 700 : 500, fontVariantNumeric: 'tabular-nums', textDecoration: 'underline dotted', cursor: 'pointer' }}
      >
        {display}
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
  options, value, onSelect, disabled = [],
}: {
  options: string[];
  value: string;
  onSelect: (v: string) => void;
  disabled?: string[];
}) {
  return (
    <span style={{ display: 'inline-flex', background: '#eeede8', border: `1px solid ${palette.disabledBorder}`, borderRadius: 7, padding: 2, gap: 2 }}>
      {options.map((o) => {
        const active = o === value;
        const isDisabled = disabled.includes(o);
        return (
          <button key={o} type="button" disabled={isDisabled}
            onClick={() => !isDisabled && onSelect(o)}
            title={isDisabled ? 'Single tranche — senior loan only.' : undefined}
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
// Refinance view — banner + include/remove toggle · assumptions · impact.
// Reads refi fields from the debt output; values the engine doesn't emit
// render as canonical "awaiting data" em-dashes (never fabricated).
// ─────────────────────────────────────────────────────────────────────
function RefinanceView({
  active, liveMode, refiYear, refiYearOverride, refiCashOut, balanceAtExit, leveredIrr,
  onSaveOverride, toast,
}: {
  active: boolean;
  liveMode: boolean;
  refiYear?: number;
  refiYearOverride: number;
  refiCashOut?: number;
  balanceAtExit?: number;
  leveredIrr?: number;
  onSaveOverride: (patch: Record<string, number | null>) => void | Promise<void>;
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
    { id: 'refiValue', label: 'Value at Refinance', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'refiLtv', label: 'Refinance LTV', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'refiPayoff', label: 'Existing Balance Repaid', kind: 'awaiting', state: 'awaiting_data', value: '—' },
    { id: 'refiCost', label: 'Financing Costs', kind: 'awaiting', state: 'awaiting_data', value: '—' },
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
