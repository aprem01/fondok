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
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  // Worker overrides for the KPI strip — fall back to mock when missing.
  const wLoan = getEngineField<number>(outputs, 'debt', 'loan_amount');
  const wDscr = getEngineField<number>(outputs, 'debt', 'year_one_dscr');
  const wDy = getEngineField<number>(outputs, 'debt', 'year_one_debt_yield');
  const wLtc = getEngineField<number>(outputs, 'capital', 'ltc');
  // Non-Kimpton deals get worker data only — no mock fallback.
  const loanAmount = isKimptonDemo ? (wLoan ?? o.financing.loanAmount) : (wLoan ?? 0);
  const ltc = isKimptonDemo ? (wLtc ?? o.financing.ltv) : (wLtc ?? 0);
  const dscr = isKimptonDemo ? (wDscr ?? o.financing.dscr) : (wDscr ?? 0);
  const debtYield = wDy != null
    ? fmtPct(wDy, 1)
    : (isKimptonDemo ? '6.8%' : '—');
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
            <KPI label="Total Debt" tip="Total senior and mezzanine debt on the deal." value={fmtCurrency(loanAmount, { compact: true })} flashKey={loanAmount} />
            <KPI label="LTC" tip={GLOSSARY['LTC']} value={fmtPct(ltc, 1)} flashKey={ltc} />
            <KPI label="DSCR" tip={GLOSSARY['DSCR']} value={`${dscr.toFixed(2)}x`} tone="green" flashKey={dscr} />
            <KPI label="Debt Yield" tip={GLOSSARY['Debt Yield']} value={debtYield} tone="amber" flashKey={debtYield} />
          </div>
          <div className="grid grid-cols-2 gap-5">
            <Panel title="Debt Summary" rows={[
              ['Total Debt', fmtCurrency(loanAmount)],
              ['Senior Loan', fmtCurrency(loanAmount)],
              ['PACE Loan', '$0'],
              ['LTC %', fmtPct(ltc, 1)],
              ['Debt Yield', debtYield],
              ['DSCR', `${dscr.toFixed(2)}x`],
            ]} />
            <Panel title="Loan Identification" rows={[
              ['Borrower', isKimptonDemo ? 'Brookfield Hotel Holdings LLC' : '—'],
              ['Lender', isKimptonDemo ? 'Wells Fargo Real Estate' : '—'],
              ['Loan Type', 'Acquisition'],
              ['Property Name', isKimptonDemo ? o.general.name : '—'],
            ]} />
            <Panel title="Senior Loan Terms" rows={[
              ['Loan Amount', fmtCurrency(loanAmount)],
              ['LTC Amount', fmtCurrency(loanAmount)],
              ['Per Key', isKimptonDemo
                ? fmtCurrency(loanAmount / o.general.keys)
                : '—'],
              ['Origination Fee %', '1.5%'],
              ['Origination Fee $', fmtCurrency(loanAmount * 0.015)],
            ]} />
            <Panel title="Valuation & Metrics" rows={[
              ['Total Uses', isKimptonDemo
                ? fmtCurrency(o.investment.totalCapital)
                : (getEngineField<number>(outputs, 'capital', 'total_capital') != null
                    ? fmtCurrency(getEngineField<number>(outputs, 'capital', 'total_capital')!)
                    : '—')],
              ['Hotel Purchase Price', isKimptonDemo
                ? fmtCurrency(o.acquisition.purchasePrice)
                : '—'],
              ['LTV', fmtPct(ltc, 1)],
              ['DY (FTM NOI)', debtYield],
            ]} />
            <Panel title="Computed Values" rows={[
              ['Interest Only Period', '48 Months'],
              ['Amortization Period', '30 Years'],
              ['Maturity Date', '9/30/2029'],
              ['Cap. Interest Reserve', fmtCurrency(980_000)],
            ]} />
            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Covenant Status</h3>
              <CovenantRow label="DSCR Min 1.20x" pass={true} value="1.57x" />
              <CovenantRow label="Debt Yield Min 10%" pass={false} value="6.8%" />
              <CovenantRow label="LTV Max 75%" pass={true} value="65.0%" />
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
            <KPI label="Senior Rate" value="6.80%" />
            <KPI label="PACE Rate" value="7.99%" />
            <KPI label="Rate Cap" value="8.33%" />
            <KPI label="Cap Expiry" value="9/30/2027" />
          </div>
          <div className="grid grid-cols-2 gap-5">
            <Panel title="Rate Configuration" rows={[
              ['Rate Type', 'Variable'], ['Spread over SOFR', '2.9%'],
              ['SOFR Ceiling', '8.33%'], ['SOFR Floor', '0%'],
            ]} />
            <Panel title="Rate Cap / Hedge" rows={[
              ['Rate Cap', '8.33%'], ['Rate Cap Expiry', '9/30/2027'],
              ['Rate Floor', 'N/A'], ['Effective Rate', '6.80%'], ['Swap Expiry Date', 'N/A'],
            ]} />
            <Panel title="Current Rate Summary" rows={[
              ['SOFR Ceiling', '8.33%'], ['Floating SOFR', '3.5%'],
              ['Spread over SOFR', '2.9%'], ['SOFR Floor', '0%'], ['Interest Rate Used', '6.8%'],
            ]} />
            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Covenant Status</h3>
              <CovenantRow label="DSCR Status 1.57x" pass={true} value="1.57x" />
              <CovenantRow label="Debt Yield Status" pass={false} value="6.8%" />
              <CovenantRow label="LTV Status" pass={true} value="65.0%" />
              <CovenantRow label="Cash Trap" pass={true} value="Not Triggered" />
            </Card>
          </div>
        </>
      )}

      {tab === 'Term & Refinance' && (
        <>
          <div className="grid grid-cols-4 gap-4 mb-5">
            <KPI label="Loan Term" value="5 Years" />
            <KPI label="IO Period" value="4 Years" />
            <KPI label="Maturity" value="3/31/2029" />
            <KPI label="Refi Status" value="Disabled" tone="amber" />
          </div>
          <div className="grid grid-cols-2 gap-5">
            <Panel title="Key Dates" rows={[
              ['Funding', '9/30/2025'], ['Origination', '3/31/2026'],
              ['Initial Maturity', '3/31/2029'], ['Current Maturity', '3/31/2029'],
            ]} />
            <Panel title="Amortization" rows={[
              ['Amortization', '30 Years'], ['(Months)', '360'],
              ['Funding Month', '0'], ['Payoff Month', '30'],
            ]} />
            <Panel title="Interest-Only" rows={[
              ['IO Period', '4 Years'], ['IO (Months)', '48'], ['IO Status', 'Active'],
            ]} />
            <Panel title="Extension Options" rows={[
              ['Extension Options', 'Two 1-year terms'],
              ['Open Prepay Date', '9/30/2027'], ['Lockout Date', 'N/A'],
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

function CovenantRow({ label, pass, value }: { label: string; pass: boolean; value: string }) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
      <span className="text-[12.5px] text-ink-700">{label}</span>
      <div className="flex items-center gap-2">
        <span className="text-[12px] tabular-nums">{value}</span>
        <Badge tone={pass ? 'green' : 'red'}>{pass ? '✓' : '✗'}</Badge>
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
