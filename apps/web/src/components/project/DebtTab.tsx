'use client';
import { useState, useEffect, useRef, useCallback, type ReactNode } from 'react';
import { useParams } from 'next/navigation';
import { DollarSign, AlertTriangle, Layers } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import { api, isWorkerConnected } from '@/lib/api';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import type { EngineOutputsResponse } from '@/lib/api';
import { kimptonAnglerOverview } from '@/lib/mockData';
import { fmtCurrency, fmtPct, cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
import { Sourced, SourcedValue } from '@/components/help/Sourced';
import { Traced } from '@/components/help/Traced';
import { MetricLabel } from '@/components/help/MetricLabel';
import { GLOSSARY } from '@/lib/glossary';

const subTabs = ['Capital Stack', 'Debt Summary', 'Rates & Covenants', 'Term & Refinance', 'Debt Schedule'];

// FON-63 — shape of the multi-tranche stack the debt engine now emits.
interface StackTranche {
  kind: string;
  label: string;
  loan_amount: number;
  all_in_rate: number | null;
  rate_type: string;
  annual_debt_service: number | null;
  interest_only: boolean;
  terms_pending: boolean;
  amortization_years: number | null;
}
interface DebtStackOutput {
  tranches: StackTranche[];
  total_debt: number;
  priced_debt: number;
  total_annual_debt_service: number;
  weighted_avg_rate: number | null;
  year_one_dscr: number | null;
  ltv: number | null;
  debt_yield: number | null;
  ltc: number | null;
  covenants?: {
    max_ltv?: number | null;
    min_debt_yield?: number | null;
    min_dscr?: number | null;
    combined_min_dscr?: number | null;
    cash_trap?: boolean | null;
    notes?: string[];
  } | null;
  warnings: string[];
}

// Formatters for <SourcedValue> on term inputs (years / months).
const yrs = (v: number | string | boolean) => `${Number(v)} ${Number(v) === 1 ? 'Year' : 'Years'}`;
const months = (v: number | string | boolean) => `${Math.round(Number(v) * 12)}`;

export default function DebtTab({ projectId }: { projectId: number | string }) {
  const [tab, setTab] = useState('Capital Stack');
  const o = kimptonAnglerOverview;
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { toast } = useToast();
  const isKimptonDemo = projectId === 7;
  const { outputs, previous } = useEngineOutputs(dealId);
  const { deal, refresh: refreshDeal } = useDeal(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  // ─── FON-63: editable debt tranches ────────────────────────────────
  // Live deals can edit tranche terms (Senior rate/principal, activate
  // PACE) from the Capital Stack table. Edits PATCH field_overrides and
  // kick a debounced run-all so DSCR / leverage / returns re-derive.
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
        toast('Saved — re-running engines', { type: 'success' });
        void refreshDeal?.();
        if (rerunTimerRef.current) clearTimeout(rerunTimerRef.current);
        rerunTimerRef.current = setTimeout(() => { void fullRun.run(); }, 1500);
      } catch (err) {
        setOverrides(overrides); // rollback
        toast(`Save failed: ${err instanceof Error ? err.message : 'Unknown error'}`, { type: 'error' });
      }
    },
    [overrides, dealId, liveMode, toast, refreshDeal, fullRun],
  );

  // ─── Worker engine field reads ────────────────────────────────────
  // Sam QA: panels used to read Kimpton fixture (`o.financing.*`,
  // `o.investment.*`) for every deal. We now prefer worker engine output
  // and fall back to the Kimpton fixture only on the demo deal
  // (projectId === 7); other deals show '—' until the engine has
  // produced the value.
  const wLoan = getEngineField<number>(outputs, 'debt', 'loan_amount');
  const wDscr = getEngineField<number>(outputs, 'debt', 'year_one_dscr');
  const wDy = getEngineField<number>(outputs, 'debt', 'year_one_debt_yield');
  const wLtc = getEngineField<number>(outputs, 'capital', 'ltc');
  const wLtv = getEngineField<number>(outputs, 'capital', 'ltv');
  const wDebtAmount = getEngineField<number>(outputs, 'capital', 'debt_amount');
  const wTotalCapital =
    getEngineField<number>(outputs, 'capital', 'total_capital_usd') ??
    getEngineField<number>(outputs, 'capital', 'total_capital');
  const wPurchase = getEngineField<number>(outputs, 'capital', 'purchase_price');
  const stack = getEngineField<DebtStackOutput>(outputs, 'debt', 'debt_stack');
  // FON-67 — refinance outputs (populated when a refi year is set).
  const wRefiYear = getEngineField<number>(outputs, 'debt', 'refi_year');
  const wRefiCashOut = getEngineField<number>(outputs, 'debt', 'refi_cash_out');
  const wRefiProceeds = getEngineField<number>(outputs, 'debt', 'balance_at_exit');

  // Display helpers: prefer worker → fixture (Kimpton demo only) → '—'.
  const pickNum = (worker: number | undefined, fixture: number): number | undefined =>
    worker != null ? worker : (isKimptonDemo ? fixture : undefined);
  const fmtOrDash = (
    n: number | undefined,
    formatter: (v: number) => string,
  ): string => (n != null ? formatter(n) : '—');

  // Resolved values (undefined = no data → render '—').
  const loanAmountN = pickNum(wLoan ?? wDebtAmount, o.financing.loanAmount);
  const ltcN = pickNum(wLtc, o.financing.ltv);
  const ltvN = pickNum(wLtv, o.financing.ltv);
  const dscrN = pickNum(wDscr, o.financing.dscr);
  const dyN = pickNum(wDy, 0.068);
  const totalCapN = pickNum(wTotalCapital, o.investment.totalCapital);
  const purchaseN = pickNum(wPurchase, o.acquisition.purchasePrice);

  // Per-key keys count: prefer real deal keys, then fixture only on demo.
  const propertyKeys =
    (deal?.keys && deal.keys > 0) ? deal.keys : (isKimptonDemo ? o.general.keys : undefined);
  const perKeyN =
    loanAmountN != null && propertyKeys != null && propertyKeys > 0
      ? loanAmountN / propertyKeys
      : undefined;

  // Display strings used in KPIs / Panels / Covenant rows.
  const loanAmountStr = fmtOrDash(loanAmountN, (v) => fmtCurrency(v));
  const loanCompactStr = fmtOrDash(loanAmountN, (v) => fmtCurrency(v, { compact: true }));
  const ltcStr = fmtOrDash(ltcN, (v) => fmtPct(v, 1));
  const ltvStr = fmtOrDash(ltvN, (v) => fmtPct(v, 1));
  const dscrStr = fmtOrDash(dscrN, (v) => `${v.toFixed(2)}x`);
  const debtYield = fmtOrDash(dyN, (v) => fmtPct(v, 1));
  const hasWorkerDebtOutput = wLoan != null;

  // Non-Kimpton deals: show empty placeholder until engines have run.
  if (!isKimptonDemo && !hasWorkerDebtOutput) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
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
          <EngineHeader
            name="Debt Engine"
            desc="Structures senior and mezzanine debt, calculates debt service, and models refinancing scenarios."
            outputs={['Loan Amount', 'DSCR', 'Debt Yield', '+1']}
            dependsOn="P&L"
            dealId={dealId}
            engineName="debt"
            onRunStart={() => setComputing(true)}
            onRunComplete={() => {
              setComputing(false);
              setRunToken(Date.now());
            }}
          />
          <EngineLegend />
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
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => toast('Engine queued — check back shortly', { type: 'info' })}
            >
              Run Debt Engine
            </Button>
          </Card>
          <EngineRunHistory dealId={dealId} />
        </div>
        <EngineRightRail />
      </div>
    );
  }

  return (
    <div className="flex gap-4">
      <div className="flex-1 min-w-0">
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
      <EngineHeader
        name="Debt Engine"
        desc="Structures senior and mezzanine debt, calculates debt service, and models refinancing scenarios."
        outputs={['Loan Amount', 'DSCR', 'Debt Yield', '+1']}
        dependsOn="P&L"
        complete
        dealId={dealId}
        engineName="debt"
        runMode="all"
        onRunStart={() => setComputing(true)}
        onRunComplete={() => {
          setComputing(false);
          setRunToken(Date.now());
        }}
      />

      <WhatJustHappened
        engine="debt"
        engineLabel="Debt"
        outputs={outputs}
        previous={previous}
        runToken={runToken}
      />

      <div className="flex items-center gap-1 mb-3 border-b border-border">
        {subTabs.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cn(
              'px-4 py-2 text-[12.5px] border-b-2 transition-colors -mb-px',
              tab === t ? 'border-brand-500 text-brand-700 font-medium' : 'border-transparent text-ink-500 hover:text-ink-900'
            )}>
            {t}
          </button>
        ))}
      </div>
      <EngineLegend />

      {tab === 'Capital Stack' && (
        <CapitalStack
          stack={stack ?? null}
          liveMode={liveMode}
          overrides={overrides}
          onSave={onSaveOverride}
        />
      )}

      {tab === 'Debt Summary' && (
        <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
          <div className="grid grid-cols-4 gap-4 mb-5">
            <KPI label="Total Debt" tip="Total senior and mezzanine debt on the deal." value={loanCompactStr} flashKey={loanAmountN} />
            <KPI label="LTC" tip={GLOSSARY['LTC']} value={ltcStr} flashKey={ltcN} />
            <KPI label="DSCR" tip={GLOSSARY['DSCR']} value={dscrStr} tone="green" flashKey={dscrN} />
            <KPI label="Debt Yield" tip={GLOSSARY['Debt Yield']} value={debtYield} tone="amber" flashKey={debtYield} />
          </div>
          <div className="grid grid-cols-2 gap-5">
            <Panel title="Debt Summary" rows={[
              ['Total Debt', loanAmountStr],
              ['Senior Loan', loanAmountStr],
              ['PACE Loan', isKimptonDemo ? '$0' : '—'],
              ['LTC %', ltcStr],
              ['Debt Yield', debtYield],
              ['DSCR', <Traced key="dscr" engine="debt" path="schedule[0].dscr">{dscrStr}</Traced>],
            ]} />
            <Panel title="Loan Identification" rows={[
              ['Borrower', isKimptonDemo ? 'Brookfield Hotel Holdings LLC' : '—'],
              ['Lender', isKimptonDemo ? 'Wells Fargo Real Estate' : '—'],
              ['Loan Type', isKimptonDemo ? 'Acquisition' : '—'],
              ['Property Name', deal?.name ?? (isKimptonDemo ? o.general.name : '—')],
            ]} />
            <Panel title="Senior Loan Terms" rows={[
              ['Loan Amount', loanAmountStr],
              ['LTC Amount', loanAmountStr],
              ['Per Key', fmtOrDash(perKeyN, (v) => fmtCurrency(v))],
              ['Origination Fee %', isKimptonDemo ? '1.5%' : '—'],
              // Origination fee $ only when both loan amount and the
              // (Kimpton-only) 1.5% assumption are available.
              ['Origination Fee $', isKimptonDemo
                ? fmtOrDash(loanAmountN, (v) => fmtCurrency(v * 0.015))
                : '—'],
            ]} />
            <Panel title="Valuation & Metrics" rows={[
              ['Total Uses', fmtOrDash(totalCapN, (v) => fmtCurrency(v))],
              ['Hotel Purchase Price', <Sourced key="pp" sourceKey="purchase_price">{fmtOrDash(purchaseN, (v) => fmtCurrency(v))}</Sourced>],
              ['LTV', <Sourced key="ltv" sourceKey="ltv">{ltvStr}</Sourced>],
              ['DY (FTM NOI)', debtYield],
            ]} />
            <Panel title="Computed Values" rows={[
              // Term inputs come from the deal's assumptions (seed default
              // until debt actuals are extracted) — sourced on hover. Dates
              // have no worker source yet, so they stay Kimpton-fixture only.
              ['Interest Only Period', isKimptonDemo ? '48 Months' : <SourcedValue key="io" sourceKey="interest_only_years" fmt={yrs} />],
              ['Amortization Period', isKimptonDemo ? '30 Years' : <SourcedValue key="am" sourceKey="amortization_years" fmt={yrs} />],
              ['Maturity Date', isKimptonDemo ? '9/30/2029' : '—'],
              ['Cap. Interest Reserve', isKimptonDemo ? fmtCurrency(980_000) : '—'],
            ]} />
            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Covenant Status</h3>
              <CovenantRow
                label="DSCR Min 1.20x"
                pass={dscrN != null ? dscrN >= 1.2 : false}
                value={dscrStr}
                missing={dscrN == null}
              />
              <CovenantRow
                label="Debt Yield Min 10%"
                pass={dyN != null ? dyN >= 0.1 : false}
                value={debtYield}
                missing={dyN == null}
              />
              <CovenantRow
                label="LTV Max 75%"
                pass={ltvN != null ? ltvN <= 0.75 : false}
                value={ltvStr}
                missing={ltvN == null}
              />
              <div className="mt-4 pt-3 border-t border-border text-[11px] text-ink-500">
                Additional metrics available in Rates & Covenants tab
              </div>
            </Card>
          </div>
          {computing && (
            <div className="absolute inset-0 bg-bg/60 backdrop-blur-[1px] flex items-start justify-center pt-12 rounded-md">
              <span className="inline-flex items-center gap-2 px-3 py-1.5 bg-white border border-border rounded-md shadow-card text-[12.5px] font-medium text-ink-700">
                <span className="w-1.5 h-1.5 rounded-full bg-brand-500 animate-pulse" />
                Recomputing…
              </span>
            </div>
          )}
        </div>
      )}

      {tab === 'Rates & Covenants' && (
        <>
          <div className="grid grid-cols-4 gap-4 mb-5">
            <KPI label="Senior Rate" flashKey="senior-rate" value={isKimptonDemo ? '6.80%' : <SourcedValue sourceKey="interest_rate" />} />
            <KPI label="PACE Rate" value={isKimptonDemo ? '7.99%' : '—'} />
            <KPI label="Rate Cap" value={isKimptonDemo ? '8.33%' : '—'} />
            <KPI label="Cap Expiry" value={isKimptonDemo ? '9/30/2027' : '—'} />
          </div>
          <div className="grid grid-cols-2 gap-5">
            <Panel title="Rate Configuration" rows={[
              ['Rate Type', isKimptonDemo ? 'Variable' : '—'],
              ['Spread over SOFR', isKimptonDemo ? '2.9%' : '—'],
              ['SOFR Ceiling', isKimptonDemo ? '8.33%' : '—'],
              ['SOFR Floor', isKimptonDemo ? '0%' : '—'],
            ]} />
            <Panel title="Rate Cap / Hedge" rows={[
              ['Rate Cap', isKimptonDemo ? '8.33%' : '—'],
              ['Rate Cap Expiry', isKimptonDemo ? '9/30/2027' : '—'],
              ['Rate Floor', isKimptonDemo ? 'N/A' : '—'],
              ['Effective Rate', isKimptonDemo ? '6.80%' : <SourcedValue key="er" sourceKey="interest_rate" />],
              ['Swap Expiry Date', isKimptonDemo ? 'N/A' : '—'],
            ]} />
            <Panel title="Current Rate Summary" rows={[
              ['SOFR Ceiling', isKimptonDemo ? '8.33%' : '—'],
              ['Floating SOFR', isKimptonDemo ? '3.5%' : '—'],
              ['Spread over SOFR', isKimptonDemo ? '2.9%' : '—'],
              ['SOFR Floor', isKimptonDemo ? '0%' : '—'],
              ['Interest Rate Used', isKimptonDemo ? '6.8%' : <SourcedValue key="iru" sourceKey="interest_rate" />],
            ]} />
            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Covenant Status</h3>
              <CovenantRow
                label="DSCR Status"
                pass={dscrN != null ? dscrN >= 1.2 : false}
                value={dscrStr}
                missing={dscrN == null}
              />
              <CovenantRow
                label="Debt Yield Status"
                pass={dyN != null ? dyN >= 0.1 : false}
                value={debtYield}
                missing={dyN == null}
              />
              <CovenantRow
                label="LTV Status"
                pass={ltvN != null ? ltvN <= 0.75 : false}
                value={ltvStr}
                missing={ltvN == null}
              />
              <CovenantRow
                label="Cash Trap"
                pass={isKimptonDemo}
                value={isKimptonDemo ? 'Not Triggered' : '—'}
                missing={!isKimptonDemo}
              />
            </Card>
          </div>
        </>
      )}

      {tab === 'Term & Refinance' && (
        <>
          {/* FON-67 — editable mid-hold refinance. Set a refi year to model
              the senior→refi phases; the debt engine sizes the new loan off
              that year's NOI, retires the senior, and returns the cash-out to
              equity (lifting levered IRR). Blank year = single-phase deal. */}
          <RefinancePanel
            liveMode={liveMode}
            overrides={overrides}
            onSave={onSaveOverride}
            refiYear={wRefiYear}
            refiProceeds={wRefiProceeds}
            refiCashOut={wRefiCashOut}
          />
          <div className="grid grid-cols-4 gap-4 mb-5">
            <KPI label="Loan Term" flashKey="loan-term" value={isKimptonDemo ? '5 Years' : <SourcedValue sourceKey="term_years" fmt={yrs} />} />
            <KPI label="IO Period" flashKey="io-period" value={isKimptonDemo ? '4 Years' : <SourcedValue sourceKey="interest_only_years" fmt={yrs} />} />
            <KPI label="Maturity" value={isKimptonDemo ? '3/31/2029' : '—'} />
            <KPI
              label="Refi Status"
              value={wRefiYear != null ? `Year ${wRefiYear}` : (isKimptonDemo ? 'Disabled' : 'Single-phase')}
              tone={wRefiYear != null ? 'green' : undefined}
            />
          </div>
          <div className="grid grid-cols-2 gap-5">
            <Panel title="Key Dates" rows={[
              ['Funding', isKimptonDemo ? '9/30/2025' : '—'],
              ['Origination', isKimptonDemo ? '3/31/2026' : '—'],
              ['Initial Maturity', isKimptonDemo ? '3/31/2029' : '—'],
              ['Current Maturity', isKimptonDemo ? '3/31/2029' : '—'],
            ]} />
            <Panel title="Amortization" rows={[
              ['Amortization', isKimptonDemo ? '30 Years' : <SourcedValue key="am2" sourceKey="amortization_years" fmt={yrs} />],
              ['(Months)', isKimptonDemo ? '360' : <SourcedValue key="amm" sourceKey="amortization_years" fmt={months} />],
              ['Funding Month', isKimptonDemo ? '0' : '—'],
              ['Payoff Month', isKimptonDemo ? '30' : '—'],
            ]} />
            <Panel title="Interest-Only" rows={[
              ['IO Period', isKimptonDemo ? '4 Years' : <SourcedValue key="io2" sourceKey="interest_only_years" fmt={yrs} />],
              ['IO (Months)', isKimptonDemo ? '48' : <SourcedValue key="iom" sourceKey="interest_only_years" fmt={months} />],
              ['IO Status', isKimptonDemo ? 'Active' : '—'],
            ]} />
            <Panel title="Extension Options" rows={[
              ['Extension Options', isKimptonDemo ? 'Two 1-year terms' : '—'],
              ['Open Prepay Date', isKimptonDemo ? '9/30/2027' : '—'],
              ['Lockout Date', isKimptonDemo ? 'N/A' : '—'],
            ]} />
          </div>
        </>
      )}

      {tab === 'Debt Schedule' && (
        <DebtScheduleTable outputs={outputs} isKimptonDemo={isKimptonDemo} />
      )}
      <EngineRunHistory dealId={dealId} seedDemo />
      </div>
      <EngineRightRail />
    </div>
  );
}

function KPI({ label, value, tone, flashKey, tip }: { label: string; value: ReactNode; tone?: 'green' | 'amber' | 'red'; flashKey?: unknown; tip?: string }) {
  const flash = useFlash(flashKey ?? value);
  return (
    <Card className={cn('p-4', flash && 'value-flash')}>
      <div className="text-[10.5px] text-ink-500 uppercase tracking-wide">
        {tip ? <MetricLabel label={label} tip={tip} /> : label}
      </div>
      <div className={`text-[20px] font-semibold tabular-nums mt-1 ${
        tone === 'green' ? 'text-success-700' : tone === 'amber' ? 'text-warn-700' : tone === 'red' ? 'text-danger-700' : 'text-ink-900'
      }`}>{value}</div>
    </Card>
  );
}

function Panel({ title, rows }: { title: string; rows: [string, ReactNode][] }) {
  return (
    <Card className="p-5">
      <h3 className="text-[13px] font-semibold text-ink-900 mb-3">{title}</h3>
      <div className="space-y-1 text-[12.5px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between py-1.5 border-b border-border/50 last:border-0">
            <span className="text-ink-500">{k}</span>
            <span className="font-medium tabular-nums text-ink-900">{v}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function CovenantRow({
  label,
  pass,
  value,
  missing,
}: {
  label: string;
  pass: boolean;
  value: string;
  missing?: boolean;
}) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
      <span className="text-[12.5px] text-ink-700">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-[12px] tabular-nums">{value}</span>
        {missing
          ? <Badge tone="amber">—</Badge>
          : <Badge tone={pass ? 'green' : 'red'}>{pass ? '✓' : '✗'}</Badge>}
      </div>
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Debt Schedule — preferred source: worker monthly_schedule[].
// For Kimpton demo (no worker), synthesize a static IO schedule from
// the mock financing assumptions so the demo never goes blank.
// ───────────────────────────────────────────────────────────────────

interface DebtMonthRow {
  month: number;
  interest: number;
  principal: number;
  payment: number;
  ending_balance: number;
}

function DebtScheduleTable({
  outputs,
  isKimptonDemo,
}: {
  outputs: EngineOutputsResponse | null;
  isKimptonDemo: boolean;
}) {
  const workerSchedule = getEngineField<DebtMonthRow[]>(outputs, 'debt', 'monthly_schedule');
  const hasWorker = Array.isArray(workerSchedule) && workerSchedule.length > 0;

  // Anchor month/year for column headers — same start date used by Investment tab.
  const startYear = 2025, startMonth = 9; // Sep 2025
  const monthLabel = (idx: number) => {
    const m = (startMonth - 1 + idx) % 12;
    const y = startYear + Math.floor((startMonth - 1 + idx) / 12);
    const names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    return `${names[m]}-${String(y).slice(-2)}`;
  };

  // Build the schedule we'll render. Worker wins; Kimpton demo synthesizes; otherwise empty state.
  let rows: DebtMonthRow[] = [];
  let beginBalances: number[] = [];

  if (hasWorker) {
    rows = workerSchedule!.slice(0, 12); // First 12 months for the table
    let bal = (rows[0]?.ending_balance ?? 0) + (rows[0]?.principal ?? 0);
    beginBalances = rows.map((r) => {
      const beg = bal;
      bal = r.ending_balance;
      return beg;
    });
  } else if (isKimptonDemo) {
    const o = kimptonAnglerOverview;
    const loan = o.financing.loanAmount;
    const monthlyRate = o.financing.interestRate / 12;
    const monthlyInterest = Math.round(loan * monthlyRate);
    rows = Array.from({ length: 8 }, (_, i) => ({
      month: i + 1,
      interest: monthlyInterest,
      principal: 0,
      payment: monthlyInterest,
      ending_balance: loan,
    }));
    beginBalances = rows.map(() => loan);
  }

  if (rows.length === 0) {
    return (
      <Card className="p-12 text-center">
        <div className="w-10 h-10 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-3">
          <DollarSign size={18} className="text-ink-400" />
        </div>
        <h3 className="text-[14px] font-semibold text-ink-900">Debt schedule not yet built</h3>
        <p className="text-[12px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
          Run the Debt engine to populate the monthly amortization schedule.
        </p>
      </Card>
    );
  }

  const monthsToShow = Math.min(rows.length, hasWorker ? 12 : 8);

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">Monthly Debt Service Schedule</h3>
        <span className="text-[11px] text-ink-500">
          {hasWorker ? `Showing first ${monthsToShow} of ${workerSchedule!.length} months` : 'IO Period (no principal)'}
        </span>
      </div>
      <div className="overflow-x-auto text-[11.5px]">
        <table className="min-w-[800px] w-full">
          <thead>
            <tr className="text-ink-500 text-[10.5px] border-b border-border">
              <th className="text-left font-medium py-2 sticky left-0 bg-white">Metric</th>
              {Array.from({ length: monthsToShow }, (_, i) => (
                <th key={i} className="text-right font-medium py-2 px-2">{monthLabel(i)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[
              { label: 'Beginning Balance', vals: beginBalances.slice(0, monthsToShow) },
              { label: 'Interest', vals: rows.slice(0, monthsToShow).map(r => r.interest) },
              { label: 'Principal', vals: rows.slice(0, monthsToShow).map(r => r.principal) },
              { label: 'Total Payment', vals: rows.slice(0, monthsToShow).map(r => r.payment) },
              { label: 'Ending Balance', vals: rows.slice(0, monthsToShow).map(r => r.ending_balance) },
            ].map(row => (
              <tr key={row.label} className="border-b border-border/50">
                <td className="py-1.5 sticky left-0 bg-white">{row.label}</td>
                {row.vals.map((v, i) =>
                  <td key={i} className="text-right tabular-nums px-2">{Math.round(v).toLocaleString()}</td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-4 pt-4 border-t border-border text-[11px] text-ink-500 space-y-1">
        <div>• Debt Yield = TTM NOI / Total Loan Balance</div>
        <div>• NOI excludes debt service and depreciation</div>
        <div>• DSCR = TTM NOI / Next TM Debt Service</div>
        {hasWorker && <div>• Schedule sourced from latest Debt engine run.</div>}
      </div>
    </Card>
  );
}

// ─────────────────────────── Capital Stack (FON-63) ───────────────────────────
// Renders the multi-tranche debt stack the engine emits: per-tranche terms +
// consolidated leverage/coverage metrics + covenants + honest warnings.
// Senior amount/rate are editable and PACE can be activated in-place; edits
// persist as debt_stack.tranches.<idx>.* overrides and re-run the model.
function CapitalStack({
  stack, liveMode, overrides, onSave,
}: {
  stack: DebtStackOutput | null;
  liveMode: boolean;
  overrides: Record<string, unknown>;
  onSave: (patch: Record<string, number | null>) => void;
}) {
  if (!stack || stack.tranches.length === 0) {
    return (
      <Card className="p-10 text-center">
        <div className="w-11 h-11 mx-auto rounded-lg bg-brand-50 flex items-center justify-center mb-3">
          <Layers size={19} className="text-brand-500" />
        </div>
        <div className="text-[14px] font-semibold text-ink-900 mb-1">Capital stack not computed</div>
        <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">
          Run the Debt engine and the tranche stack populates here — seeded from the deal&apos;s senior
          loan. You can then layer on PACE / mezzanine and floating-rate terms.
        </p>
      </Card>
    );
  }

  const pct = (v: number | null | undefined, dp = 1) => (v == null ? '—' : fmtPct(v, dp));
  const usd = (v: number | null | undefined) => (v == null ? '—' : fmtCurrency(v, { compact: true }));

  return (
    <div className="space-y-5">
      {/* Consolidated metrics */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
        {[
          ['Total Debt', usd(stack.total_debt)],
          ['Wtd. Avg Rate', pct(stack.weighted_avg_rate)],
          ['DSCR', stack.year_one_dscr == null ? '—' : `${stack.year_one_dscr.toFixed(2)}x`],
          ['LTV', pct(stack.ltv, 0)],
          ['Debt Yield', pct(stack.debt_yield)],
          ['LTC', pct(stack.ltc, 0)],
        ].map(([label, value]) => (
          <Card key={label} className="p-3">
            <div className="text-[10px] uppercase tracking-wide text-ink-400">{label}</div>
            <div className="text-[16px] font-semibold text-ink-900 tabular-nums mt-0.5">{value}</div>
          </Card>
        ))}
      </div>

      {/* Warnings */}
      {stack.warnings.length > 0 && (
        <div className="rounded-md border border-warn-200 bg-warn-50 px-4 py-3 space-y-1.5">
          {stack.warnings.map((w, i) => (
            <div key={i} className="flex items-start gap-2 text-[12px] text-warn-900 leading-relaxed">
              <AlertTriangle size={13} className="text-warn-600 shrink-0 mt-0.5" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {/* Tranche table — Senior + PACE, amount/rate editable in place. */}
      {(() => {
        // Always surface a PACE row so it can be activated; the engine only
        // returns PACE once it's funded, so synthesize a placeholder (index 1)
        // that writes the same debt_stack.tranches.1.* overrides.
        type Row = StackTranche & { idx: number; synthetic: boolean };
        const rows: Row[] = stack.tranches.map((t, i) => ({ ...t, idx: i, synthetic: false }));
        if (!rows.some((r) => r.kind === 'pace')) {
          rows.push({
            idx: rows.length, kind: 'pace', label: 'PACE Loan',
            loan_amount: 0, all_in_rate: null, rate_type: 'fixed', amortization_years: null,
            annual_debt_service: null, interest_only: true, terms_pending: true, synthetic: true,
          });
        }
        return (
      <Card className="p-0 overflow-hidden">
        <div className="px-5 py-3 border-b border-border flex items-center gap-2">
          <Layers size={15} className="text-ink-500" />
          <h3 className="text-[13px] font-semibold text-ink-900">Capital Stack</h3>
          <span className="text-[11px] text-ink-400">{stack.tranches.length} tranche{stack.tranches.length === 1 ? '' : 's'}</span>
          <span className={cn('ml-auto text-[11px]', liveMode ? 'text-ink-500' : 'text-ink-400')}>
            {liveMode ? 'Editable · changes re-run the model' : 'Read-only on demo deals'}
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="bg-ink-900 text-white text-[10px] uppercase tracking-wider">
                <th className="text-left font-semibold px-5 py-2.5">Tranche</th>
                <th className="text-right font-semibold px-5 py-2.5 w-40">Amount</th>
                <th className="text-left font-semibold px-5 py-2.5">Rate Type</th>
                <th className="text-right font-semibold px-5 py-2.5 w-36">All-in Rate</th>
                <th className="text-right font-semibold px-5 py-2.5 w-28">Amort</th>
                <th className="text-right font-semibold px-5 py-2.5">Debt Service</th>
                <th className="text-left font-semibold px-5 py-2.5 w-28">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) => {
                const amtPath = `debt_stack.tranches.${t.idx}.principal_usd`;
                const ratePath = `debt_stack.tranches.${t.idx}.rate_pct`;
                const amortPath = `debt_stack.tranches.${t.idx}.amortization_months`;
                const amt = readOverrideNum(overrides, amtPath, t.loan_amount);
                const rawRate = readOverrideNum(overrides, ratePath, t.all_in_rate ?? Number.NaN);
                const rateVal = Number.isFinite(rawRate) ? rawRate : null;
                const amortMonthsOv = readOverrideNum(overrides, amortPath, Number.NaN);
                const amortYears = Number.isFinite(amortMonthsOv)
                  ? Math.round(amortMonthsOv / 12)
                  : (t.interest_only ? 0 : (t.amortization_years ?? 0));
                const muted = t.synthetic && amt <= 0;
                return (
                  <tr key={`${t.kind}-${t.idx}`} className={cn('border-t border-border', muted && 'opacity-70')}>
                    <td className="px-5 py-2.5 text-ink-900 font-medium">
                      {t.label}
                      <span className="ml-2 text-[10px] uppercase tracking-wide text-ink-400">{t.kind}</span>
                    </td>
                    <td className="px-4 py-2 text-right">
                      <CellUsd value={amt} overridden={amtPath in overrides} liveMode={liveMode}
                        onCommit={(v) => onSave({ [amtPath]: v })} />
                    </td>
                    <td className="px-5 py-2.5 text-ink-700 capitalize">
                      {t.rate_type}
                    </td>
                    <td className="px-4 py-2 text-right">
                      <CellPct value={rateVal} overridden={ratePath in overrides} liveMode={liveMode}
                        onCommit={(f) => onSave({ [ratePath]: f })} />
                    </td>
                    <td className="px-4 py-2 text-right">
                      <CellYears value={amortYears} overridden={amortPath in overrides} liveMode={liveMode}
                        onCommit={(yrs) => onSave({ [amortPath]: Math.round(yrs) * 12 })} />
                    </td>
                    <td className="px-5 py-2.5 text-right tabular-nums text-ink-900">{t.annual_debt_service == null ? '—' : fmtCurrency(t.annual_debt_service, { compact: true })}</td>
                    <td className="px-5 py-2.5">
                      {muted
                        ? <span className="text-[10.5px] text-ink-400">Not funded</span>
                        : t.terms_pending
                          ? <Badge tone="amber">Terms pending</Badge>
                          : <Badge tone="green">Priced</Badge>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {liveMode && (
          <div className="px-5 py-2.5 border-t border-border text-[11px] text-ink-500 leading-relaxed">
            Edit an amount or rate to reprice a tranche. Enter a PACE amount + rate to add it to the stack — leave a tranche&apos;s rate blank to keep it in leverage but out of debt service.
          </div>
        )}
      </Card>
        );
      })()}

      {/* Covenants */}
      {stack.covenants && (
        <Card className="p-5">
          <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Covenants</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-[12.5px]">
            {[
              ['Max LTV', pct(stack.covenants.max_ltv, 0)],
              ['Min Debt Yield', pct(stack.covenants.min_debt_yield)],
              ['Min DSCR', stack.covenants.min_dscr == null ? '—' : `${stack.covenants.min_dscr.toFixed(2)}x`],
              ['Combined DSCR', stack.covenants.combined_min_dscr == null ? '—' : `${stack.covenants.combined_min_dscr.toFixed(2)}x`],
            ].map(([label, value]) => (
              <div key={label}>
                <div className="text-[10px] uppercase tracking-wide text-ink-400">{label}</div>
                <div className="text-[13.5px] font-semibold text-ink-900 tabular-nums">{value}</div>
              </div>
            ))}
          </div>
          {stack.covenants.cash_trap != null && (
            <div className="mt-3 text-[12px] text-ink-600">
              Cash trap: <span className="font-medium text-ink-900">{stack.covenants.cash_trap ? 'Yes' : 'No'}</span>
            </div>
          )}
          {stack.covenants.notes && stack.covenants.notes.length > 0 && (
            <ul className="mt-2 space-y-1 text-[11.5px] text-ink-500 list-disc pl-5">
              {stack.covenants.notes.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}
        </Card>
      )}

      <p className="text-[11px] text-ink-400 leading-relaxed">
        Seeded from the deal&apos;s extracted senior loan plus an institutional PACE placeholder.
        Amount, rate and amortization are editable (0 yrs = interest-only). Origination / exit
        fees and live covenant testing are the next build steps.
      </p>
    </div>
  );
}

// FON-63 — editable-cell helpers (mirror the Partnership tab pattern).
function round6(n: number): number {
  return Math.round(n * 1e6) / 1e6;
}
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

// Editable whole-percent cell (displays %, saves a fraction).
function CellPct({
  value, overridden, liveMode, onCommit,
}: {
  value: number | null;
  overridden: boolean;
  liveMode: boolean;
  onCommit: (fraction: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value == null ? '' : (value * 100).toFixed(2));
  const commit = () => {
    if (draft === null) return;
    const t = draft.trim();
    setDraft(null);
    if (t === '') return;
    const pct = Number(t);
    if (Number.isFinite(pct)) onCommit(round6(pct / 100));
  };
  return (
    <span className={cn(
      'inline-flex items-center gap-0.5 justify-end rounded border px-1.5 py-1',
      liveMode
        ? 'border-border focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-100'
        : 'border-transparent',
      overridden && 'border-blue-400 bg-blue-50',
    )}>
      <input
        value={shown}
        placeholder={liveMode ? '—' : ''}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur();
          if (e.key === 'Escape') { setDraft(null); e.currentTarget.blur(); }
        }}
        readOnly={!liveMode}
        inputMode="decimal"
        aria-label="all-in rate percent"
        className="w-12 bg-transparent text-right text-[12.5px] tabular-nums text-ink-900 focus:outline-none"
      />
      <span className="text-ink-400 text-[11px]">%</span>
    </span>
  );
}

// Editable dollar cell — displays/edits in $millions for a friendly input.
function CellUsd({
  value, overridden, liveMode, onCommit,
}: {
  value: number;
  overridden: boolean;
  liveMode: boolean;
  onCommit: (usd: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value ? (value / 1e6).toFixed(2) : '0');
  const commit = () => {
    if (draft === null) return;
    const t = draft.trim();
    setDraft(null);
    if (t === '') return;
    const m = Number(t);
    if (Number.isFinite(m)) onCommit(Math.max(0, Math.round(m * 1e6)));
  };
  return (
    <span className={cn(
      'inline-flex items-center gap-0.5 justify-end rounded border px-1.5 py-1',
      liveMode
        ? 'border-border focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-100'
        : 'border-transparent',
      overridden && 'border-blue-400 bg-blue-50',
    )}>
      <span className="text-ink-400 text-[11px]">$</span>
      <input
        value={shown}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur();
          if (e.key === 'Escape') { setDraft(null); e.currentTarget.blur(); }
        }}
        readOnly={!liveMode}
        inputMode="decimal"
        aria-label="tranche amount in millions"
        className="w-14 bg-transparent text-right text-[12.5px] tabular-nums text-ink-900 focus:outline-none"
      />
      <span className="text-ink-400 text-[11px]">M</span>
    </span>
  );
}

// Editable amortization cell — years; 0 shows/means interest-only (IO).
function CellYears({
  value, overridden, liveMode, onCommit,
}: {
  value: number;
  overridden: boolean;
  liveMode: boolean;
  onCommit: (years: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const isIO = value <= 0;
  const shown = draft ?? (isIO ? 'IO' : String(value));
  const commit = () => {
    if (draft === null) return;
    const t = draft.trim();
    setDraft(null);
    if (t === '') return;
    // Accept "IO" (case-insensitive) or 0 as interest-only.
    if (/^io$/i.test(t)) { onCommit(0); return; }
    const yrs = Number(t);
    if (Number.isFinite(yrs) && yrs >= 0) onCommit(Math.round(yrs));
  };
  return (
    <span className={cn(
      'inline-flex items-center gap-0.5 justify-end rounded border px-1.5 py-1',
      liveMode
        ? 'border-border focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-100'
        : 'border-transparent',
      overridden && 'border-blue-400 bg-blue-50',
    )}>
      <input
        value={shown}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onFocus={(e) => { if (isIO && draft === null) { setDraft(''); e.currentTarget.select(); } }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur();
          if (e.key === 'Escape') { setDraft(null); e.currentTarget.blur(); }
        }}
        readOnly={!liveMode}
        inputMode="numeric"
        aria-label="amortization years (0 = interest-only)"
        className="w-10 bg-transparent text-right text-[12.5px] tabular-nums text-ink-900 focus:outline-none"
      />
      <span className="text-ink-400 text-[11px]">{isIO && draft === null ? '' : 'yr'}</span>
    </span>
  );
}

// FON-67 — editable mid-hold refinance assumptions + computed cash-out.
function RefinancePanel({
  liveMode, overrides, onSave, refiYear, refiProceeds, refiCashOut,
}: {
  liveMode: boolean;
  overrides: Record<string, unknown>;
  onSave: (patch: Record<string, number | null>) => void;
  refiYear?: number;
  refiProceeds?: number;
  refiCashOut?: number;
}) {
  const usd = (v?: number) => (v == null ? '—' : fmtCurrency(v, { compact: true }));
  const active = refiYear != null && refiYear > 0;
  return (
    <Card className="p-5 mb-5">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-[13px] font-semibold text-ink-900">Refinance (mid-hold)</h3>
        <span className={cn('text-[11px]', liveMode ? 'text-ink-500' : 'text-ink-400')}>
          {liveMode ? 'Editable · set a refi year to model it' : 'Read-only on demo deals'}
        </span>
      </div>
      <p className="text-[11.5px] text-ink-500 mb-4 leading-relaxed">
        Set a refi year to model a mid-hold refinance: the new loan is sized off that year&apos;s NOI
        (min of the debt-yield and DSCR limits), retires the senior balance, and returns the net
        cash-out to equity — lifting the levered IRR. Leave the year blank for a single-phase deal.
      </p>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-5">
        <RefiField label="Refi Year" path="debt_stack.refi_test_year" seed={0} kind="int"
          overrides={overrides} liveMode={liveMode} onSave={onSave} hint="blank = no refi" />
        <RefiField label="Refi Debt Yield" path="debt_stack.refi_market_debt_yield_pct" seed={0.10} kind="pct"
          overrides={overrides} liveMode={liveMode} onSave={onSave} />
        <RefiField label="Refi Min DSCR" path="debt_stack.refi_market_dscr_min" seed={1.25} kind="ratio"
          overrides={overrides} liveMode={liveMode} onSave={onSave} />
        <RefiField label="Refi Rate" path="debt_stack.refi_market_rate_pct" seed={0.068} kind="pct"
          overrides={overrides} liveMode={liveMode} onSave={onSave} />
      </div>
      {active && (
        <div className="mt-4 pt-4 border-t border-border grid grid-cols-3 gap-4">
          <div>
            <div className="text-[10px] uppercase tracking-wide text-ink-400">Refi Year</div>
            <div className="text-[15px] font-semibold text-ink-900 tabular-nums">Year {refiYear}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-ink-400">Refi Proceeds</div>
            <div className="text-[15px] font-semibold text-ink-900 tabular-nums">{usd(refiProceeds)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wide text-ink-400">Cash-Out to Equity</div>
            <div className="text-[15px] font-semibold text-success-700 tabular-nums">{usd(refiCashOut)}</div>
          </div>
        </div>
      )}
    </Card>
  );
}

// Labeled editable refi field — int (year), pct (fraction), or ratio.
function RefiField({
  label, path, seed, kind, overrides, liveMode, onSave, hint,
}: {
  label: string;
  path: string;
  seed: number;
  kind: 'int' | 'pct' | 'ratio';
  overrides: Record<string, unknown>;
  liveMode: boolean;
  onSave: (patch: Record<string, number | null>) => void;
  hint?: string;
}) {
  const overridden = path in overrides;
  const current = readOverrideNum(overrides, path, seed);
  const [draft, setDraft] = useState<string | null>(null);
  const fmtVal = kind === 'pct'
    ? (current * 100).toFixed(2)
    : kind === 'ratio'
      ? current.toFixed(2)
      : (current > 0 ? String(Math.round(current)) : '');
  const shown = draft ?? fmtVal;
  const suffix = kind === 'pct' ? '%' : kind === 'ratio' ? 'x' : 'yr';
  const commit = () => {
    if (draft === null) return;
    const t = draft.trim();
    setDraft(null);
    if (t === '') { onSave({ [path]: null }); return; } // clear the override
    const n = Number(t);
    if (!Number.isFinite(n)) return;
    const val = kind === 'pct' ? round6(n / 100) : kind === 'int' ? Math.round(n) : round6(n);
    onSave({ [path]: val });
  };
  return (
    <div>
      <label className="block text-[11.5px] text-ink-500 mb-1">
        {label}
        {overridden && (
          <span className="ml-1.5 text-[10px] text-blue-600" title="Analyst override">• edited</span>
        )}
      </label>
      <div className={cn(
        'flex items-center gap-1 px-3 py-2 rounded-md border',
        liveMode
          ? 'border-border focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-100'
          : 'border-transparent bg-ink-300/10',
        overridden && 'border-blue-400',
      )}>
        <input
          value={shown}
          placeholder={kind === 'int' ? 'none' : ''}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.currentTarget.blur();
            if (e.key === 'Escape') { setDraft(null); e.currentTarget.blur(); }
          }}
          readOnly={!liveMode}
          inputMode="decimal"
          aria-label={label}
          className="w-full bg-transparent text-[13px] tabular-nums text-ink-900 focus:outline-none"
        />
        <span className="text-ink-400 text-[12px]">{suffix}</span>
      </div>
      {hint && <div className="text-[10.5px] text-ink-400 mt-1">{hint}</div>}
    </div>
  );
}
