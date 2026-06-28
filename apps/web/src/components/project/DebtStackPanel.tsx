'use client';
/**
 * DebtStackPanel — Wave 4 W4.4.
 *
 * Replaces the single-loan "Senior Loan Financing" card with an
 * institutional debt stack (senior + mezz + pref equity) plus the
 * Year-N refi test. Three collapsible tranche sections (Senior,
 * Mezz, Pref Equity) with inline edit-in-place; a header KPI strip
 * (Total Debt, LTC, LTV, blended Y1 DSCR, blended Y1 Debt Yield);
 * a "Refi Test (Year 5)" card with a green/red status pill; and a
 * preset dropdown that one-shot loads a templated stack
 * (Senior Only, Senior + Mezz 15%, ...).
 *
 * Mirrors the engine schemas in
 * ``packages/schemas-py/fondok_schemas/debt_stack.py`` — keep these
 * shapes in lockstep when the schema grows fields.
 */
import { useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, Check, AlertTriangle } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { fmtCurrency, fmtPct, cn } from '@/lib/format';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';

// Tranche shape mirrors fondok_schemas.debt_stack.DebtTranche.
export type TrancheKind = 'senior' | 'mezz' | 'pref_equity';

export interface DebtTrancheState {
  name: TrancheKind;
  label: string;
  principal_usd: number;
  rate_pct: number; // 0..1 fraction
  io_period_months: number;
  amortization_months: number;
  upfront_fee_pct: number; // 0..10 in percent (e.g. 1.0 = 1%)
  exit_fee_pct: number; // 0..10
  is_senior: boolean;
  priority_rank: 1 | 2 | 3;
}

export interface RefiTestState {
  refi_test_year: number;
  refi_market_debt_yield_pct: number; // 0..1
  refi_market_dscr_min: number;
  refi_market_rate_pct: number; // 0..1
  exit_cap_rate: number; // 0..1
}

export interface DebtStackState {
  tranches: DebtTrancheState[];
  refi: RefiTestState;
}

export interface DebtStackPanelProps {
  purchasePrice: number;
  totalCapital?: number;
  noiByYear: number[];
  termYears: number;
  state: DebtStackState;
  onChange: (next: DebtStackState) => void;
  sources?: Record<string, string>;
}

// ────────────────────────── Defaults ──────────────────────────

const DEFAULT_TERM_MONTHS = 60;
const DEFAULT_AMORT_MONTHS = 360;

export const DEFAULT_DEBT_STACK: DebtStackState = {
  tranches: [
    {
      name: 'senior',
      label: 'Senior Loan',
      principal_usd: 0,
      rate_pct: 0.065,
      io_period_months: 0,
      amortization_months: DEFAULT_AMORT_MONTHS,
      upfront_fee_pct: 0,
      exit_fee_pct: 0,
      is_senior: true,
      priority_rank: 1,
    },
  ],
  refi: {
    refi_test_year: 5,
    refi_market_debt_yield_pct: 0.09,
    refi_market_dscr_min: 1.3,
    refi_market_rate_pct: 0.075,
    exit_cap_rate: 0.07,
  },
};

// Five preset templates. Percentages scale off purchasePrice.
interface StackTemplate {
  id: string;
  label: string;
  description: string;
  build: (price: number) => DebtTrancheState[];
}

export const STACK_TEMPLATES: StackTemplate[] = [
  {
    id: 'senior_only',
    label: 'Senior Only',
    description: '60% LTV senior, no mezz or pref equity',
    build: (price) => [
      {
        name: 'senior',
        label: 'Senior Loan',
        principal_usd: Math.round(price * 0.6),
        rate_pct: 0.065,
        io_period_months: 0,
        amortization_months: DEFAULT_AMORT_MONTHS,
        upfront_fee_pct: 0,
        exit_fee_pct: 0,
        is_senior: true,
        priority_rank: 1,
      },
    ],
  },
  {
    id: 'senior_mezz_15',
    label: 'Senior + Mezz 15%',
    description: '60% senior + 15% mezz (75% total leverage)',
    build: (price) => [
      {
        name: 'senior',
        label: 'Senior Loan',
        principal_usd: Math.round(price * 0.6),
        rate_pct: 0.065,
        io_period_months: 0,
        amortization_months: DEFAULT_AMORT_MONTHS,
        upfront_fee_pct: 0,
        exit_fee_pct: 0,
        is_senior: true,
        priority_rank: 1,
      },
      {
        name: 'mezz',
        label: 'Mezzanine',
        principal_usd: Math.round(price * 0.15),
        rate_pct: 0.11,
        io_period_months: DEFAULT_TERM_MONTHS,
        amortization_months: DEFAULT_TERM_MONTHS,
        upfront_fee_pct: 1.0,
        exit_fee_pct: 0,
        is_senior: false,
        priority_rank: 2,
      },
    ],
  },
  {
    id: 'senior_mezz_20',
    label: 'Senior + Mezz 20%',
    description: '60% senior + 20% mezz (80% total leverage)',
    build: (price) => [
      {
        name: 'senior',
        label: 'Senior Loan',
        principal_usd: Math.round(price * 0.6),
        rate_pct: 0.065,
        io_period_months: 0,
        amortization_months: DEFAULT_AMORT_MONTHS,
        upfront_fee_pct: 0,
        exit_fee_pct: 0,
        is_senior: true,
        priority_rank: 1,
      },
      {
        name: 'mezz',
        label: 'Mezzanine',
        principal_usd: Math.round(price * 0.2),
        rate_pct: 0.115,
        io_period_months: DEFAULT_TERM_MONTHS,
        amortization_months: DEFAULT_TERM_MONTHS,
        upfront_fee_pct: 1.0,
        exit_fee_pct: 0,
        is_senior: false,
        priority_rank: 2,
      },
    ],
  },
  {
    id: 'senior_mezz_pref',
    label: 'Senior + Mezz + Pref Equity',
    description: '55% senior + 15% mezz + 10% pref equity (80% total)',
    build: (price) => [
      {
        name: 'senior',
        label: 'Senior Loan',
        principal_usd: Math.round(price * 0.55),
        rate_pct: 0.065,
        io_period_months: 12,
        amortization_months: DEFAULT_AMORT_MONTHS,
        upfront_fee_pct: 0,
        exit_fee_pct: 0,
        is_senior: true,
        priority_rank: 1,
      },
      {
        name: 'mezz',
        label: 'Apollo Mezz Fund III',
        principal_usd: Math.round(price * 0.15),
        rate_pct: 0.11,
        io_period_months: DEFAULT_TERM_MONTHS,
        amortization_months: DEFAULT_TERM_MONTHS,
        upfront_fee_pct: 1.0,
        exit_fee_pct: 0,
        is_senior: false,
        priority_rank: 2,
      },
      {
        name: 'pref_equity',
        label: 'Preferred Equity',
        principal_usd: Math.round(price * 0.1),
        rate_pct: 0.14,
        io_period_months: DEFAULT_TERM_MONTHS,
        amortization_months: DEFAULT_TERM_MONTHS,
        upfront_fee_pct: 0,
        exit_fee_pct: 0,
        is_senior: false,
        priority_rank: 3,
      },
    ],
  },
  {
    id: 'bridge_debt',
    label: 'Bridge Debt',
    description: '70% LTV short-term bridge, IO, exit fee',
    build: (price) => [
      {
        name: 'senior',
        label: 'Bridge Loan',
        principal_usd: Math.round(price * 0.7),
        rate_pct: 0.085,
        io_period_months: 36,
        amortization_months: 36,
        upfront_fee_pct: 1.5,
        exit_fee_pct: 0.5,
        is_senior: true,
        priority_rank: 1,
      },
    ],
  },
];

// ────────────────────────── Engine math (client-side preview) ──────────────────────────

function pmt(rate: number, nper: number, pv: number): number {
  if (nper <= 0) return 0;
  if (rate === 0) return pv / nper;
  const factor = Math.pow(1 + rate, nper);
  return (pv * (rate * factor)) / (factor - 1);
}

interface AmortYear {
  year: number;
  interest: number;
  principal: number;
  debt_service: number;
  ending_balance: number;
}

function buildAmortYears(t: DebtTrancheState, termYears: number): AmortYear[] {
  const monthlyRate = t.rate_pct / 12;
  const amortMonths = t.amortization_months;
  const ioMonths = t.io_period_months;
  const monthly = amortMonths > 0 ? pmt(monthlyRate, amortMonths, t.principal_usd) : 0;
  let balance = t.principal_usd;
  const totalMonths = Math.max(termYears * 12, 12);
  const months: { interest: number; principal: number; payment: number; ending: number }[] = [];
  for (let m = 1; m <= totalMonths; m++) {
    const interest = balance * monthlyRate;
    let principal = 0;
    let payment = interest;
    if (m > ioMonths && amortMonths > 0) {
      payment = monthly;
      principal = Math.max(0, payment - interest);
      if (principal > balance) {
        principal = balance;
        payment = principal + interest;
      }
    }
    balance = Math.max(0, balance - principal);
    if (m === totalMonths && t.exit_fee_pct > 0) {
      payment += t.principal_usd * (t.exit_fee_pct / 100);
    }
    months.push({ interest, principal, payment, ending: balance });
  }
  const years: AmortYear[] = [];
  for (let y = 1; y <= termYears; y++) {
    const window = months.slice((y - 1) * 12, y * 12);
    if (window.length === 0) break;
    const i = window.reduce((a, b) => a + b.interest, 0);
    const p = window.reduce((a, b) => a + b.principal, 0);
    const ds = window.reduce((a, b) => a + b.payment, 0);
    years.push({ year: y, interest: i, principal: p, debt_service: ds, ending_balance: window[window.length - 1].ending });
  }
  return years;
}

interface StackMetrics {
  totalDebt: number;
  ltc: number;
  ltv: number;
  totalDsY1: number;
  blendedDscrY1: number | null;
  debtYieldY1: number | null;
  refiOutcome: {
    triggeredYear: number;
    canRefi: boolean;
    maxRefiDebt: number;
    outstanding: number;
    cashToClose: number;
    refiPropertyValue: number;
    refiDscr: number | null;
  } | null;
}

function computeMetrics(state: DebtStackState, purchase: number, totalCapital: number, noiByYear: number[], termYears: number): StackMetrics {
  const totalDebt = state.tranches.reduce((acc, t) => acc + t.principal_usd, 0);
  const ltc = totalCapital > 0 ? totalDebt / totalCapital : 0;
  const ltv = purchase > 0 ? totalDebt / purchase : 0;
  const perTrancheYears = state.tranches.map(t => buildAmortYears(t, termYears));
  const dsY1 = perTrancheYears.reduce((acc, ys) => acc + (ys[0]?.debt_service ?? 0), 0);
  const noiY1 = noiByYear[0] ?? 0;
  const eopY1 = perTrancheYears.reduce((acc, ys) => acc + (ys[0]?.ending_balance ?? 0), 0);
  const blendedDscrY1 = dsY1 > 0 ? noiY1 / dsY1 : null;
  const debtYieldY1 = eopY1 > 0 ? noiY1 / eopY1 : null;
  // Refi test — simplified mirror of run_refi_test.
  const year = state.refi.refi_test_year;
  const refiYearIdx = year; // 1-indexed test year; noi[year] is next-year NOI for sizing
  const refiNoi = noiByYear[refiYearIdx] ?? noiByYear[noiByYear.length - 1] ?? 0;
  const maxDebtDy = state.refi.refi_market_debt_yield_pct > 0 ? refiNoi / state.refi.refi_market_debt_yield_pct : 0;
  const maxDebtDscr = state.refi.refi_market_rate_pct > 0 ? refiNoi / (state.refi.refi_market_dscr_min * state.refi.refi_market_rate_pct) : Number.POSITIVE_INFINITY;
  const maxRefiDebt = Math.min(maxDebtDy, maxDebtDscr);
  const eopIdx = year - 1;
  const outstanding = perTrancheYears.reduce((acc, ys) => acc + (ys[eopIdx]?.ending_balance ?? 0), 0);
  const refiPropertyValue = state.refi.exit_cap_rate > 0 ? refiNoi / state.refi.exit_cap_rate : 0;
  const canRefiDy = outstanding > 0 ? maxDebtDy >= outstanding : true;
  const dscrAtOutstanding = outstanding > 0 && state.refi.refi_market_rate_pct > 0 ? refiNoi / (outstanding * state.refi.refi_market_rate_pct) : Number.POSITIVE_INFINITY;
  const canRefiDscr = dscrAtOutstanding >= state.refi.refi_market_dscr_min;
  const canRefi = canRefiDy && canRefiDscr;
  const cashToClose = !canRefi && outstanding > maxRefiDebt ? outstanding - maxRefiDebt : 0;
  return {
    totalDebt,
    ltc,
    ltv,
    totalDsY1: dsY1,
    blendedDscrY1,
    debtYieldY1,
    refiOutcome: noiByYear.length > 0 ? {
      triggeredYear: year,
      canRefi,
      maxRefiDebt,
      outstanding,
      cashToClose,
      refiPropertyValue,
      refiDscr: outstanding > 0 && state.refi.refi_market_rate_pct > 0 ? refiNoi / (outstanding * state.refi.refi_market_rate_pct) : null,
    } : null,
  };
}

// ────────────────────────── Component ──────────────────────────

export default function DebtStackPanel({ purchasePrice, totalCapital, noiByYear, termYears, state, onChange, sources }: DebtStackPanelProps) {
  const [openSenior, setOpenSenior] = useState(true);
  const [openMezz, setOpenMezz] = useState(true);
  const [openPref, setOpenPref] = useState(false);
  const totalCap = totalCapital && totalCapital > 0 ? totalCapital : purchasePrice;
  const metrics = useMemo(() => computeMetrics(state, purchasePrice, totalCap, noiByYear, termYears), [state, purchasePrice, totalCap, noiByYear, termYears]);

  function applyTemplate(id: string) {
    const tpl = STACK_TEMPLATES.find(t => t.id === id);
    if (!tpl) return;
    onChange({
      ...state,
      tranches: tpl.build(purchasePrice),
    });
  }

  function updateTranche(name: TrancheKind, patch: Partial<DebtTrancheState>) {
    onChange({
      ...state,
      tranches: state.tranches.map(t => (t.name === name ? { ...t, ...patch } : t)),
    });
  }

  function addTranche(name: TrancheKind) {
    if (state.tranches.some(t => t.name === name)) return;
    const blank: DebtTrancheState = name === 'mezz'
      ? {
          name: 'mezz', label: 'Mezzanine', principal_usd: 0, rate_pct: 0.11, io_period_months: termYears * 12,
          amortization_months: termYears * 12, upfront_fee_pct: 1.0, exit_fee_pct: 0, is_senior: false, priority_rank: 2,
        }
      : {
          name: 'pref_equity', label: 'Preferred Equity', principal_usd: 0, rate_pct: 0.14, io_period_months: termYears * 12,
          amortization_months: termYears * 12, upfront_fee_pct: 0, exit_fee_pct: 0, is_senior: false, priority_rank: 3,
        };
    onChange({ ...state, tranches: [...state.tranches, blank] });
  }

  function removeTranche(name: TrancheKind) {
    if (name === 'senior') return; // senior is required
    onChange({ ...state, tranches: state.tranches.filter(t => t.name !== name) });
  }

  function updateRefi(patch: Partial<RefiTestState>) {
    onChange({ ...state, refi: { ...state.refi, ...patch } });
  }

  const senior = state.tranches.find(t => t.name === 'senior');
  const mezz = state.tranches.find(t => t.name === 'mezz');
  const pref = state.tranches.find(t => t.name === 'pref_equity');

  return (
    <Card className="p-5">
      {/* Header + template picker */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-[13px] font-semibold text-ink-900">Debt Stack</h3>
          <p className="text-[11px] text-ink-500 mt-0.5">
            Senior + Mezzanine + Pref Equity tranches with debt yield + refi test
          </p>
        </div>
        <select
          className="text-[11.5px] px-2 py-1 border border-border rounded-md bg-white"
          defaultValue=""
          onChange={(e) => {
            if (e.target.value) {
              applyTemplate(e.target.value);
              e.target.value = '';
            }
          }}
        >
          <option value="">Apply template…</option>
          {STACK_TEMPLATES.map(t => (
            <option key={t.id} value={t.id} title={t.description}>{t.label}</option>
          ))}
        </select>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-5 gap-3 mb-4">
        <KpiBox label="Total Debt" value={fmtCurrency(metrics.totalDebt)} />
        <KpiBox label="LTC" value={fmtPct(metrics.ltc, 1)} />
        <KpiBox label="LTV" value={fmtPct(metrics.ltv, 1)} />
        <KpiBox label="Blended Y1 DSCR" value={metrics.blendedDscrY1 != null ? `${metrics.blendedDscrY1.toFixed(2)}x` : '—'} />
        <KpiBox label="Y1 Debt Yield" value={metrics.debtYieldY1 != null ? fmtPct(metrics.debtYieldY1, 2) : '—'} />
      </div>

      {/* Tranches */}
      {senior && (
        <TrancheSection
          tranche={senior}
          open={openSenior}
          onToggle={() => setOpenSenior(o => !o)}
          onUpdate={(p) => updateTranche('senior', p)}
          sources={sources}
          trancheIndex={0}
          canRemove={false}
        />
      )}
      {mezz ? (
        <TrancheSection
          tranche={mezz}
          open={openMezz}
          onToggle={() => setOpenMezz(o => !o)}
          onUpdate={(p) => updateTranche('mezz', p)}
          onRemove={() => removeTranche('mezz')}
          sources={sources}
          trancheIndex={1}
          canRemove={true}
        />
      ) : (
        <AddTrancheButton kind="mezz" onAdd={() => addTranche('mezz')} />
      )}
      {pref ? (
        <TrancheSection
          tranche={pref}
          open={openPref}
          onToggle={() => setOpenPref(o => !o)}
          onUpdate={(p) => updateTranche('pref_equity', p)}
          onRemove={() => removeTranche('pref_equity')}
          sources={sources}
          trancheIndex={2}
          canRemove={true}
        />
      ) : (
        <AddTrancheButton kind="pref_equity" onAdd={() => addTranche('pref_equity')} />
      )}

      {/* Refi test card */}
      <div className="mt-4 border border-border rounded-md p-3 bg-ink-50/40">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <h4 className="text-[12.5px] font-semibold text-ink-900">Refi Test (Year {state.refi.refi_test_year})</h4>
            {metrics.refiOutcome && (
              <span
                className={cn(
                  'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium',
                  metrics.refiOutcome.canRefi
                    ? 'bg-success-100 text-success-700'
                    : 'bg-danger-100 text-danger-700',
                )}
              >
                {metrics.refiOutcome.canRefi ? <Check size={11} /> : <AlertTriangle size={11} />}
                {metrics.refiOutcome.canRefi ? 'CAN REFI' : 'CASH-IN REQUIRED'}
              </span>
            )}
          </div>
          <AssumptionBadge source={sources?.['debt_stack.refi_test_year'] ?? 'analyst_override'} />
        </div>
        <div className="grid grid-cols-4 gap-3 mb-3">
          <NumberField label="Test Year" value={state.refi.refi_test_year} onChange={v => updateRefi({ refi_test_year: Math.max(1, Math.round(v)) })} step={1} format={v => `Y${v}`} />
          <NumberField label="Mkt Debt Yield" value={state.refi.refi_market_debt_yield_pct} onChange={v => updateRefi({ refi_market_debt_yield_pct: v })} step={0.0025} format={v => fmtPct(v, 2)} />
          <NumberField label="Min DSCR" value={state.refi.refi_market_dscr_min} onChange={v => updateRefi({ refi_market_dscr_min: v })} step={0.05} format={v => `${v.toFixed(2)}x`} />
          <NumberField label="Mkt Refi Rate" value={state.refi.refi_market_rate_pct} onChange={v => updateRefi({ refi_market_rate_pct: v })} step={0.0025} format={v => fmtPct(v, 2)} />
        </div>
        {metrics.refiOutcome && (
          <div className="grid grid-cols-2 gap-3 text-[11.5px]">
            <Row label="Max Refi Debt">{fmtCurrency(metrics.refiOutcome.maxRefiDebt)}</Row>
            <Row label="Outstanding Y5 Balance">{fmtCurrency(metrics.refiOutcome.outstanding)}</Row>
            <Row label="Refi Property Value">{fmtCurrency(metrics.refiOutcome.refiPropertyValue)}</Row>
            <Row label="Cash to Close (Equity)" valueClass={metrics.refiOutcome.cashToClose > 0 ? 'text-danger-700 font-semibold' : ''}>
              {metrics.refiOutcome.cashToClose > 0 ? fmtCurrency(metrics.refiOutcome.cashToClose) : '—'}
            </Row>
          </div>
        )}
      </div>
    </Card>
  );
}

// ────────────────────────── Sub-components ──────────────────────────

function TrancheSection({ tranche, open, onToggle, onUpdate, onRemove, sources, trancheIndex, canRemove }: {
  tranche: DebtTrancheState;
  open: boolean;
  onToggle: () => void;
  onUpdate: (patch: Partial<DebtTrancheState>) => void;
  onRemove?: () => void;
  sources?: Record<string, string>;
  trancheIndex: number;
  canRemove: boolean;
}) {
  const heading = tranche.name === 'senior' ? 'Senior Loan' : tranche.name === 'mezz' ? 'Mezzanine' : 'Preferred Equity';
  const baseKey = `debt_stack.tranches.${trancheIndex}`;
  return (
    <div className="mb-3 border border-border rounded">
      <button type="button" onClick={onToggle}
        className="w-full flex items-center justify-between px-3 py-2 text-[12.5px] font-medium text-ink-900 hover:bg-ink-50 transition-colors">
        <div className="flex items-center gap-2">
          <span>{heading}</span>
          <span className="text-[11px] text-ink-500">·</span>
          <input
            type="text"
            value={tranche.label}
            onChange={e => onUpdate({ label: e.target.value })}
            onClick={e => e.stopPropagation()}
            className="text-[11.5px] text-ink-700 bg-transparent border-b border-dashed border-border focus:outline-none focus:border-brand-500 px-1"
            placeholder="Tranche label"
          />
          <AssumptionBadge source={sources?.[`${baseKey}.rate_pct`] ?? 'analyst_override'} />
        </div>
        <div className="flex items-center gap-3">
          {tranche.principal_usd > 0 && (
            <span className="text-[11.5px] text-ink-700 tabular-nums">
              {fmtCurrency(tranche.principal_usd)} @ {fmtPct(tranche.rate_pct, 2)}
            </span>
          )}
          {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </div>
      </button>
      {open && (
        <div className="px-3 pb-3">
          <div className="grid grid-cols-3 gap-3 mb-2">
            <NumberField label="Principal" value={tranche.principal_usd} onChange={v => onUpdate({ principal_usd: Math.max(0, v) })} step={250_000} format={v => fmtCurrency(v)} />
            <NumberField label="Rate" value={tranche.rate_pct} onChange={v => onUpdate({ rate_pct: Math.max(0, Math.min(1, v)) })} step={0.0025} format={v => fmtPct(v, 2)} />
            <NumberField label="Amort (mo)" value={tranche.amortization_months} onChange={v => onUpdate({ amortization_months: Math.max(0, Math.round(v)) })} step={12} format={v => `${v} mo`} />
          </div>
          <div className="grid grid-cols-3 gap-3 mb-2">
            <NumberField label="IO Period (mo)" value={tranche.io_period_months} onChange={v => onUpdate({ io_period_months: Math.max(0, Math.round(v)) })} step={6} format={v => `${v} mo`} />
            <NumberField label="Upfront Fee" value={tranche.upfront_fee_pct} onChange={v => onUpdate({ upfront_fee_pct: Math.max(0, v) })} step={0.25} format={v => `${v.toFixed(2)}%`} />
            <NumberField label="Exit Fee" value={tranche.exit_fee_pct} onChange={v => onUpdate({ exit_fee_pct: Math.max(0, v) })} step={0.25} format={v => `${v.toFixed(2)}%`} />
          </div>
          {canRemove && onRemove && (
            <div className="mt-2 flex justify-end">
              <Button type="button" variant="ghost" onClick={onRemove} className="text-[11px] text-danger-700 hover:bg-danger-50">
                Remove tranche
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function AddTrancheButton({ kind, onAdd }: { kind: TrancheKind; onAdd: () => void }) {
  const label = kind === 'mezz' ? '+ Add Mezzanine tranche' : '+ Add Preferred Equity tranche';
  return (
    <button
      type="button"
      onClick={onAdd}
      className="w-full mb-3 px-3 py-2 text-[11.5px] text-ink-500 hover:text-brand-700 border border-dashed border-border rounded hover:border-brand-500 transition-colors"
    >
      {label}
    </button>
  );
}

function KpiBox({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border rounded-md p-2 bg-white">
      <div className="text-[10px] text-ink-500 uppercase tracking-wide mb-0.5">{label}</div>
      <div className="text-[13.5px] font-semibold text-ink-900 tabular-nums">{value}</div>
    </div>
  );
}

function NumberField({ label, value, onChange, format, step = 1, min, max }: {
  label: string; value: number; onChange: (v: number) => void; format: (v: number) => string;
  step?: number; min?: number; max?: number;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label className="text-[10.5px] text-ink-500 uppercase tracking-wide">{label}</label>
        <span className="text-[11.5px] font-semibold text-brand-700 tabular-nums">{format(value)}</span>
      </div>
      <input type="number" value={value} step={step} min={min} max={max}
        onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) onChange(v); }}
        className="w-full px-2 py-1 text-[12px] border border-border rounded tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-500" />
    </div>
  );
}

function Row({ label, valueClass, children }: { label: string; valueClass?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-ink-500">{label}</span>
      <span className={cn('tabular-nums text-ink-900', valueClass)}>{children}</span>
    </div>
  );
}
