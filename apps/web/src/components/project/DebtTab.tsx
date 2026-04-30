'use client';
import { useState } from 'react';
import { useParams } from 'next/navigation';
import { DollarSign } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
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
import { MetricLabel } from '@/components/help/MetricLabel';
import { GLOSSARY } from '@/lib/glossary';

const subTabs = ['Debt Summary', 'Rates & Covenants', 'Term & Refinance', 'Debt Schedule'];

export default function DebtTab({ projectId }: { projectId: number | string }) {
  const [tab, setTab] = useState('Debt Summary');
  const o = kimptonAnglerOverview;
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { toast } = useToast();
  const isKimptonDemo = projectId === 7;
  const { outputs, previous } = useEngineOutputs(dealId);
  const { deal } = useDeal(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

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
              ['DSCR', dscrStr],
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
              ['Hotel Purchase Price', fmtOrDash(purchaseN, (v) => fmtCurrency(v))],
              ['LTV', ltvStr],
              ['DY (FTM NOI)', debtYield],
            ]} />
            <Panel title="Computed Values" rows={[
              // No worker source for these terms today — Kimpton fixture only.
              ['Interest Only Period', isKimptonDemo ? '48 Months' : '—'],
              ['Amortization Period', isKimptonDemo ? '30 Years' : '—'],
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
            <KPI label="Senior Rate" value={isKimptonDemo ? '6.80%' : '—'} />
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
              ['Effective Rate', isKimptonDemo ? '6.80%' : '—'],
              ['Swap Expiry Date', isKimptonDemo ? 'N/A' : '—'],
            ]} />
            <Panel title="Current Rate Summary" rows={[
              ['SOFR Ceiling', isKimptonDemo ? '8.33%' : '—'],
              ['Floating SOFR', isKimptonDemo ? '3.5%' : '—'],
              ['Spread over SOFR', isKimptonDemo ? '2.9%' : '—'],
              ['SOFR Floor', isKimptonDemo ? '0%' : '—'],
              ['Interest Rate Used', isKimptonDemo ? '6.8%' : '—'],
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
          {/*
            Term & Refinance has no worker engine source today — refi_year /
            refi_proceeds aren't on the debt engine output. For non-Kimpton
            deals we render '—' across the board rather than leak the
            Kimpton fixture (5-yr term, $32M proceeds, etc.) onto an
            unrelated deal.
          */}
          <div className="grid grid-cols-4 gap-4 mb-5">
            <KPI label="Loan Term" value={isKimptonDemo ? '5 Years' : '—'} />
            <KPI label="IO Period" value={isKimptonDemo ? '4 Years' : '—'} />
            <KPI label="Maturity" value={isKimptonDemo ? '3/31/2029' : '—'} />
            <KPI
              label="Refi Status"
              value={isKimptonDemo ? 'Disabled' : '—'}
              tone={isKimptonDemo ? 'amber' : undefined}
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
              ['Amortization', isKimptonDemo ? '30 Years' : '—'],
              ['(Months)', isKimptonDemo ? '360' : '—'],
              ['Funding Month', isKimptonDemo ? '0' : '—'],
              ['Payoff Month', isKimptonDemo ? '30' : '—'],
            ]} />
            <Panel title="Interest-Only" rows={[
              ['IO Period', isKimptonDemo ? '4 Years' : '—'],
              ['IO (Months)', isKimptonDemo ? '48' : '—'],
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

function KPI({ label, value, tone, flashKey, tip }: { label: string; value: string; tone?: 'green' | 'amber' | 'red'; flashKey?: unknown; tip?: string }) {
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

function Panel({ title, rows }: { title: string; rows: string[][] }) {
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
