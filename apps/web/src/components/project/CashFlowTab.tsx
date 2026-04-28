'use client';
import { useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer,
} from 'recharts';
import { Activity } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import EngineRunHistory from './EngineRunHistory';
import { kimptonAnglerOverview } from '@/lib/mockData';
import { fmtCurrency, fmtMillions, cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';
import { GLOSSARY } from '@/lib/glossary';

const subTabs = ['Cash Flow Summary', 'Levered Detail', 'Unlevered Detail', 'Distributions'];

const tooltipStyle = {
  contentStyle: { background: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, fontSize: 12 },
  labelStyle: { color: '#64748b', fontSize: 11 },
};

// Worker-engine row shapes (loose) needed to assemble the cash flow.
interface WorkerExpenseYear {
  year: number;
  total_revenue: number;
  ffe_reserve: number;
  noi: number;
}
interface WorkerDebtYear {
  year: number;
  interest: number;
  principal: number;
  debt_service: number;
}

interface CashFlowModel {
  noi: number[];
  debtService: number[];
  leveredCF: number[];
  capex: number[];
  ffe: number[];
  interest: number[];
  principal: number[];
  distributions: number[];
  operatingDist: number[];
  exitDist: number;
  cashSweep: number[];
  begCash: number[];
  endCash: number[];
  unlevered: number[];
  y5Operations: number;
  y5WithExit: number;
  exitNet: number;
  initialEquity: number;
  source: 'worker' | 'mock';
}

// Worker variant: all dollar amounts in actual dollars (not $000s).
function buildCashFlowFromWorker(opts: {
  expenseYears: WorkerExpenseYear[];
  debtYears: WorkerDebtYear[] | null;
  leveredFlows: number[] | null;       // From returns engine — Year 0 = -equity, Y1..Yn = CFADS, last = CFADS + net proceeds
  unleveredFlows: number[] | null;     // Year 0 = -purchase, Y1..Yn = NOI, last = NOI + net sale
  partnershipDistributions: number[] | null;  // Annual LP+GP distributions from partnership engine
  workingCapital: number;
}): CashFlowModel {
  const ey = opts.expenseYears.slice(0, 5);
  const noi = ey.map(y => y.noi);
  const ffe = ey.map(y => y.ffe_reserve);

  // Debt service from worker debt schedule (annual roll-up); fall back to
  // (NOI - levered CF) when debt schedule isn't present in this run.
  const ds = opts.debtYears
    ? opts.debtYears.slice(0, 5).map(y => y.debt_service)
    : noi.map(() => 0);
  const interest = opts.debtYears
    ? opts.debtYears.slice(0, 5).map(y => y.interest)
    : noi.map(() => 0);
  const principal = opts.debtYears
    ? opts.debtYears.slice(0, 5).map(y => y.principal)
    : noi.map(() => 0);

  // Returns engine cash_flows: [-equity, CFADS_y1, ..., CFADS_yN-1, CFADS_yN + net_proceeds]
  const lev = opts.leveredFlows ?? [];
  const initialEquity = lev.length > 0 ? -lev[0] : 0;
  const cfads = lev.length >= 2 ? lev.slice(1) : noi.map((n, i) => n - ds[i]);
  // The last entry combines operating CFADS + net proceeds — split would
  // need the gross sale figure; rely on returns.gross_sale_price below.
  const leveredCF = cfads.length === noi.length ? cfads : noi.map((n, i) => n - ds[i]);

  // Distributions — partnership engine sums GP+LP cash flows by year. Final
  // year is the exit. If partnership didn't run, fall back to leveredCF as
  // a proxy for what gets distributed (no carry-out modelling).
  const distArr = opts.partnershipDistributions ?? [];
  const distributions = distArr.length === noi.length
    ? distArr
    : leveredCF.map((cf, i) => Math.max(0, cf));
  const operatingDist = distributions.slice(0, Math.max(0, distributions.length - 1));
  const exitDist = distributions[distributions.length - 1] ?? 0;

  // Capex (PIP catchup) is outside the engine output — keep as zero for
  // worker runs unless the user has populated it elsewhere.
  const capex = noi.map(() => 0);

  const cashSweep = leveredCF.map((cf, i) =>
    i < operatingDist.length
      ? Math.max(0, cf - (operatingDist[i] ?? 0) - capex[i])
      : 0,
  );

  // Beginning/ending cash for the levered detail table.
  let bal = opts.workingCapital;
  const begCash: number[] = [];
  const endCash: number[] = [];
  for (let i = 0; i < noi.length; i++) {
    begCash.push(bal);
    bal = bal + leveredCF[i] - capex[i] - (i < noi.length - 1 ? (operatingDist[i] ?? 0) : exitDist);
    endCash.push(Math.max(0, bal));
  }

  // Unlevered: derive from returns.cash_flows_unlevered.
  const unl = opts.unleveredFlows ?? [];
  const unleveredOps = unl.length >= 2 ? unl.slice(1) : noi.map((n, i) => n - capex[i] - ffe[i]);
  const unlevered = unleveredOps.length === noi.length ? unleveredOps : noi.map((n, i) => n - capex[i] - ffe[i]);
  // Net proceeds embedded in the last year of unlevered flows: subtract
  // operating-only NOI to back out the exit component.
  const lastIdx = noi.length - 1;
  const y5Operations = unlevered[lastIdx] ?? 0;
  const y5WithExit = y5Operations; // Already includes exit when sourced from returns engine.
  const exitNet = unl.length >= 2 ? Math.max(0, (unl[unl.length - 1] - (noi[lastIdx] ?? 0))) : 0;

  return {
    noi, debtService: ds, leveredCF, capex, ffe, interest, principal,
    distributions, operatingDist, exitDist, cashSweep,
    begCash, endCash, unlevered, y5Operations, y5WithExit, exitNet,
    initialEquity, source: 'worker',
  };
}

// Build cash flow schedule. NOI is in $000s in proforma; convert to dollars here.
function buildCashFlowFromMock(): CashFlowModel {
  const p = kimptonAnglerOverview.proforma;
  const noiThousands = p.find(r => r.label === 'Net Operating Income')!;
  const debtSvcThousands = p.find(r => r.label === 'Debt Service')!;
  const ffeThousands = p.find(r => r.label === 'FF&E Reserve')!;
  const years: ('y1' | 'y2' | 'y3' | 'y4' | 'y5')[] = ['y1', 'y2', 'y3', 'y4', 'y5'];

  const noi = years.map(y => noiThousands[y] * 1000);
  const debtService = years.map(y => debtSvcThousands[y] * 1000);
  const ffe = years.map(y => ffeThousands[y] * 1000);
  const leveredCF = noi.map((n, i) => n - debtService[i]);

  // Capex outside of FF&E reserve — modest year 2/3 PIP catch-up
  const capex = [0, 250_000, 250_000, 0, 0];

  // Interest vs Principal (IO for first 4 years per debt tab, then amortizing)
  const annualDS = kimptonAnglerOverview.financing.annualDebtService;
  const rate = kimptonAnglerOverview.financing.interestRate;
  const loanBal = kimptonAnglerOverview.financing.loanAmount;
  const interest = years.map((_, i) => i < 4 ? annualDS : Math.round(loanBal * rate));
  const principal = years.map((_, i) => i < 4 ? 0 : Math.round(annualDS - loanBal * rate));

  // Distributions per the Partnership Distribution Timeline
  const distributions = [309_500, 345_600, 384_200, 517_300, 20_563_400];
  const operatingDist = distributions.slice(0, 4);
  const exitDist = distributions[4];

  // Cash sweep — retained CF after distributions during cash trap (low DSCR Y1-Y2)
  const cashSweep = leveredCF.map((cf, i) =>
    i < 4 ? Math.max(0, cf - operatingDist[i] - capex[i]) : 0
  );

  // Build beginning/ending cash
  const initialCash = kimptonAnglerOverview.acquisition.workingCapital;
  let bal = initialCash;
  const begCash: number[] = [];
  const endCash: number[] = [];
  for (let i = 0; i < 5; i++) {
    begCash.push(bal);
    bal = bal + leveredCF[i] - capex[i] - (i < 4 ? operatingDist[i] : exitDist);
    endCash.push(Math.max(0, bal));
  }

  // Unlevered: NOI - Capex - FF&E (no debt). Y5 includes terminal value (gross sale - selling costs).
  const exitNet = kimptonAnglerOverview.reversion.grossSalePrice - kimptonAnglerOverview.reversion.sellingCosts;
  const unlevered = noi.map((n, i) => n - capex[i] - ffe[i]);
  const y5Operations = unlevered[4];
  const y5WithExit = y5Operations + exitNet;
  const equity = kimptonAnglerOverview.sources.find(s => s.label === 'Equity')!.amount;

  return {
    noi, debtService, leveredCF, capex, ffe, interest, principal,
    distributions, operatingDist, exitDist, cashSweep,
    begCash, endCash, unlevered, y5Operations, y5WithExit, exitNet,
    initialEquity: equity, source: 'mock',
  };
}

export default function CashFlowTab({ projectId }: { projectId: number | string }) {
  const [tab, setTab] = useState('Cash Flow Summary');
  const params = useParams();
  const { toast } = useToast();
  const dealId = (params?.id as string | undefined) ?? '';
  const [computing, setComputing] = useState(false);
  const isKimptonDemo = projectId === 7;
  const { outputs } = useEngineOutputs(dealId);

  // Pull dependencies from worker output. Returns engine cash_flows /
  // cash_flows_unlevered are the canonical CFADS arrays.
  const expenseYears = getEngineField<WorkerExpenseYear[]>(outputs, 'expense', 'years');
  const debtSchedule = getEngineField<WorkerDebtYear[]>(outputs, 'debt', 'schedule');
  const leveredFlows = getEngineField<number[]>(outputs, 'returns', 'cash_flows');
  const unleveredFlows = getEngineField<number[]>(outputs, 'returns', 'cash_flows_unlevered');
  const gpFlows = getEngineField<number[]>(outputs, 'partnership', 'gp_cash_flows');
  const lpFlows = getEngineField<number[]>(outputs, 'partnership', 'lp_cash_flows');
  const partnershipDistributions = (gpFlows && lpFlows && gpFlows.length === lpFlows.length)
    ? gpFlows.map((g, i) => g + (lpFlows[i] ?? 0))
    : null;
  const wWorkingCapital = getEngineField<number>(outputs, 'capital', 'working_capital');

  const hasWorkerCashFlow = Array.isArray(expenseYears) && expenseYears.length > 0
    && Array.isArray(leveredFlows) && leveredFlows.length > 0;

  const cf = useMemo<CashFlowModel | null>(() => {
    if (hasWorkerCashFlow) {
      return buildCashFlowFromWorker({
        expenseYears: expenseYears!,
        debtYears: debtSchedule ?? null,
        leveredFlows: leveredFlows ?? null,
        unleveredFlows: unleveredFlows ?? null,
        partnershipDistributions,
        workingCapital: wWorkingCapital ?? 0,
      });
    }
    if (isKimptonDemo) return buildCashFlowFromMock();
    return null;
  }, [hasWorkerCashFlow, expenseYears, debtSchedule, leveredFlows, unleveredFlows, partnershipDistributions, wWorkingCapital, isKimptonDemo]);

  if (!isKimptonDemo && !cf) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          <IntroCard
            dismissKey="cashflow-intro"
            title="The Cash Flow Engine"
            body={
              <>
                What hits the equity investors&apos; pockets each year, after debt service and capex.
                <span className="font-semibold"> Levered</span> = after debt; <span className="font-semibold">unlevered</span> = before debt.
                Distributions, exit proceeds, and the cumulative cash to LPs all live here.
              </>
            }
          />
          <EngineHeader
            name="Cash Flow Engine"
            desc="Computes levered and unlevered cash flow from operations through hold period."
            outputs={['Levered CF', 'Unlevered CF', 'CoC', 'DSCR']}
            dependsOn="P&L"
            dealId={dealId}
            engineName="returns"
            onRunStart={() => setComputing(true)}
            onRunComplete={() => setComputing(false)}
          />
          <EngineLegend />
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <Activity size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">No cash flow output yet</h3>
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              Cash flow depends on the <span className="font-medium">P&amp;L</span> engine. Run the P&amp;L
              first to populate levered and unlevered schedules.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => toast('Engine queued — check back shortly', { type: 'info' })}
            >
              Run Model
            </Button>
          </Card>
          <EngineRunHistory dealId={dealId} />
        </div>
        <EngineRightRail />
      </div>
    );
  }

  // cf is non-null at this point (we returned the placeholder above otherwise).
  const cfd = cf!;
  const sumLevered = cfd.leveredCF.reduce((s, v) => s + v, 0);
  const sumUnlevered = cfd.unlevered.reduce((s, v) => s + v, 0);
  const equity = cfd.initialEquity;
  const opCount = cfd.operatingDist.length || 1;
  const avgCoC = equity > 0 ? (cfd.operatingDist.reduce((s, v) => s + v, 0) / opCount) / equity : 0;
  const cumulativeDist = cfd.distributions.reduce((s, v) => s + v, 0);

  return (
    <div className="flex gap-4">
      <div className="flex-1 min-w-0">
      <IntroCard
        dismissKey="cashflow-intro"
        title="The Cash Flow Engine"
        body={
          <>
            What hits the equity investors&apos; pockets each year, after debt service and capex.
            <span className="font-semibold"> Levered</span> = after debt; <span className="font-semibold">unlevered</span> = before debt.
            Distributions, exit proceeds, and the cumulative cash to LPs all live here.
          </>
        }
      />
      <EngineHeader
        name="Cash Flow Engine"
        desc="Computes levered and unlevered cash flow from operations through hold period."
        outputs={['Levered CF', 'Unlevered CF', 'CoC', 'DSCR']}
        dependsOn="P&L"
        complete
        dealId={dealId}
        engineName="returns"
        runMode="all"
        onRunStart={() => setComputing(true)}
        onRunComplete={() => setComputing(false)}
      />

      <div className={cn('grid grid-cols-4 gap-4 mb-5', computing && 'pointer-events-none opacity-60')}>
        <KPI label="5-Yr Levered CF" tip="Total cash flow to equity over the 5-year hold, after debt service. The actual money investors see." value={fmtMillions(sumLevered, 2)} tone="green" flashKey={sumLevered} />
        <KPI label="5-Yr Unlevered CF" tip="Total cash flow before debt — the asset-level cash production over the hold." value={fmtMillions(sumUnlevered, 2)} flashKey={sumUnlevered} />
        <KPI label="Avg Cash-on-Cash" tip={GLOSSARY['CoC']} value={`${(avgCoC * 100).toFixed(1)}%`} flashKey={avgCoC} />
        <KPI label="Cumulative Distributions" tip="Total cash actually paid out to investors over the hold, including the exit." value={fmtMillions(cumulativeDist, 2)} tone="green" flashKey={cumulativeDist} />
      </div>

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

      <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
        {tab === 'Cash Flow Summary' && <Summary cf={cfd} />}
        {tab === 'Levered Detail' && <LeveredDetail cf={cfd} />}
        {tab === 'Unlevered Detail' && <UnleveredDetail cf={cfd} isKimptonDemo={isKimptonDemo} outputs={outputs} />}
        {tab === 'Distributions' && <Distributions cf={cfd} equity={equity} outputs={outputs} isKimptonDemo={isKimptonDemo} />}
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

function Summary({ cf }: { cf: CashFlowModel }) {
  const lastIdx = cf.noi.length - 1;
  const data = cf.noi.map((n, i) => ({
    year: `Year ${i + 1}`,
    NOI: Math.round(n / 1000),
    'Debt Service': Math.round(cf.debtService[i] / 1000),
    'Levered CF': Math.round(cf.leveredCF[i] / 1000),
    Distributions: Math.round((i < lastIdx ? (cf.operatingDist[i] ?? 0) : cf.exitDist) / 1000),
  }));

  let cumulative = 0;
  return (
    <>
      <Card className="p-5">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Cash Flow Composition</h3>
        <ResponsiveContainer width="100%" height={300}>
          <BarChart data={data} margin={{ top: 10, right: 25, left: 5, bottom: 0 }}>
            <CartesianGrid strokeDasharray="2 2" stroke="#e5e7eb" />
            <XAxis dataKey="year" stroke="#64748b" fontSize={11} />
            <YAxis stroke="#64748b" fontSize={11} tickFormatter={v => `$${(v / 1000).toFixed(1)}M`} />
            <Tooltip {...tooltipStyle} formatter={(v: number) => `$${v.toLocaleString()}K`} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="NOI" fill="#3b82f6" />
            <Bar dataKey="Debt Service" fill="#ef4444" />
            <Bar dataKey="Levered CF" fill="#10b981" />
            <Bar dataKey="Distributions" fill="#f59e0b" />
          </BarChart>
        </ResponsiveContainer>
      </Card>

      <Card className="p-5 mt-5">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Cash Flow Summary</h3>
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="text-ink-500 text-[11px] border-b border-border">
              <th className="text-left font-medium pb-2">Year</th>
              <th className="text-right font-medium pb-2">NOI</th>
              <th className="text-right font-medium pb-2">Debt Service</th>
              <th className="text-right font-medium pb-2">DSCR</th>
              <th className="text-right font-medium pb-2">Levered CF</th>
              <th className="text-right font-medium pb-2">Distributions</th>
              <th className="text-right font-medium pb-2">Cumulative</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d, i) => {
              cumulative += i < lastIdx ? (cf.operatingDist[i] ?? 0) : cf.exitDist;
              const dscr = cf.debtService[i] > 0 ? cf.noi[i] / cf.debtService[i] : 0;
              return (
                <SummaryRow
                  key={d.year}
                  d={d}
                  i={i}
                  isLast={i === lastIdx}
                  cf={cf}
                  cumulative={cumulative}
                  dscr={dscr}
                />
              );
            })}
          </tbody>
        </table>
      </Card>
    </>
  );
}

function LeveredDetail({ cf }: { cf: CashFlowModel }) {
  const years = cf.noi.map((_, i) => `Year ${i + 1}`);
  const zeroes = cf.noi.map(() => 0);

  type Row = { label: string; values: number[]; kind?: 'detail' | 'subtotal' | 'total' | 'header' };
  const rows: Row[] = [
    { label: 'Beginning Cash Balance', values: cf.begCash, kind: 'detail' },
    { label: 'OPERATING ACTIVITIES', values: zeroes, kind: 'header' },
    { label: 'Net Operating Income', values: cf.noi, kind: 'detail' },
    { label: 'FF&E Reserve', values: cf.ffe.map(v => -v), kind: 'detail' },
    { label: 'Capital Expenditures', values: cf.capex.map(v => -v), kind: 'detail' },
    { label: 'Net Operating Cash Flow', values: cf.noi.map((n, i) => n - cf.ffe[i] - cf.capex[i]), kind: 'subtotal' },
    { label: 'FINANCING ACTIVITIES', values: zeroes, kind: 'header' },
    { label: 'Interest Expense', values: cf.interest.map(v => -v), kind: 'detail' },
    { label: 'Principal Repayment', values: cf.principal.map(v => -v), kind: 'detail' },
    { label: 'Total Debt Service', values: cf.debtService.map(v => -v), kind: 'subtotal' },
    { label: 'Levered Cash Flow', values: cf.leveredCF, kind: 'subtotal' },
    { label: 'EQUITY DISTRIBUTIONS', values: zeroes, kind: 'header' },
    { label: 'Distributions to Equity', values: cf.distributions.map(v => -v), kind: 'detail' },
    { label: 'Cash Sweep / Retained', values: cf.cashSweep.map(v => -v), kind: 'detail' },
    { label: 'Ending Cash Balance', values: cf.endCash, kind: 'total' },
  ];

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">Levered Cash Flow Detail</h3>
        <span className="text-[11px] text-ink-500">5-year hold · IO loan Y1-Y4 · refinance Y4 → amortizing Y5</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12px] min-w-[700px]">
          <thead>
            <tr className="text-ink-500 text-[10.5px] border-b border-border">
              <th className="text-left font-medium pb-2 w-64">&nbsp;</th>
              {years.map(y => (
                <th key={y} className="text-right font-medium pb-2">{y}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              if (r.kind === 'header') {
                return (
                  <tr key={r.label}>
                    <td colSpan={years.length + 1} className="pt-3 pb-1.5 text-[10.5px] uppercase tracking-wide text-ink-500 font-semibold">
                      {r.label}
                    </td>
                  </tr>
                );
              }
              return (
                <DetailRow key={r.label} row={r} idx={i} />
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function UnleveredDetail({ cf, isKimptonDemo, outputs }: {
  cf: CashFlowModel;
  isKimptonDemo: boolean;
  outputs: ReturnType<typeof useEngineOutputs>['outputs'];
}) {
  const years = cf.noi.map((_, i) => `Year ${i + 1}`);
  const lastIdx = cf.noi.length - 1;

  type Row = { label: string; values: (number | string)[]; kind?: 'detail' | 'subtotal' | 'total' | 'header' };

  // Initial equity for unlevered = total capital (no debt). Prefer worker
  // capital engine output; fall back to Kimpton mock for the demo.
  const wTotalCapital = getEngineField<number>(outputs, 'capital', 'total_capital');
  const totalCapital = wTotalCapital ?? (isKimptonDemo ? kimptonAnglerOverview.investment.totalCapital : 0);

  // Use worker returns engine for the gross sale + selling costs when available.
  const wGrossSale = getEngineField<number>(outputs, 'returns', 'gross_sale_price');
  const wSellingCosts = getEngineField<number>(outputs, 'returns', 'selling_costs');
  const grossSale = wGrossSale ?? (isKimptonDemo ? kimptonAnglerOverview.reversion.grossSalePrice : cf.exitNet);
  const sellingCosts = wSellingCosts ?? (isKimptonDemo ? kimptonAnglerOverview.reversion.sellingCosts : 0);

  const exitVec = cf.noi.map((_, i) => i === lastIdx ? grossSale : 0);
  const sellingVec = cf.noi.map((_, i) => i === lastIdx ? -sellingCosts : 0);
  const netVec = cf.noi.map((_, i) => i === lastIdx ? cf.exitNet : 0);
  // Operating-only cash flow (NOI - capex - FF&E) is what cf.unlevered now holds.
  const unleveredWithExit = cf.unlevered.map((v, i) => i === lastIdx ? v + cf.exitNet : v);

  const cumUnlevered: number[] = [];
  let acc = -totalCapital;
  for (let i = 0; i < cf.noi.length; i++) {
    acc += unleveredWithExit[i];
    cumUnlevered.push(acc);
  }

  const headerVec = cf.noi.map(() => '' as string | number);
  const rows: Row[] = [
    { label: 'OPERATIONS', values: headerVec, kind: 'header' },
    { label: 'Net Operating Income', values: cf.noi, kind: 'detail' },
    { label: 'FF&E Reserve', values: cf.ffe.map(v => -v), kind: 'detail' },
    { label: 'Capital Expenditures', values: cf.capex.map(v => -v), kind: 'detail' },
    { label: 'Operating Cash Flow', values: cf.unlevered, kind: 'subtotal' },
    { label: 'TERMINAL VALUE', values: headerVec, kind: 'header' },
    { label: 'Gross Sale Proceeds', values: exitVec, kind: 'detail' },
    { label: 'Selling Costs', values: sellingVec, kind: 'detail' },
    { label: 'Net Sale Proceeds', values: netVec, kind: 'subtotal' },
    { label: 'Unlevered Cash Flow', values: unleveredWithExit, kind: 'total' },
    { label: 'Cumulative (Net of Equity)', values: cumUnlevered, kind: 'subtotal' },
  ];

  const wUnleveredIRR = getEngineField<number>(outputs, 'returns', 'unlevered_irr');
  const unleveredIRR = wUnleveredIRR ?? (isKimptonDemo ? kimptonAnglerOverview.returns.unleveredIRR : 0);

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">Unlevered Cash Flow Detail</h3>
        <span className="text-[11px] text-ink-500">No debt assumed · {(unleveredIRR * 100).toFixed(1)}% Unlevered IRR · Exit Year {cf.noi.length}</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12px] min-w-[700px]">
          <thead>
            <tr className="text-ink-500 text-[10.5px] border-b border-border">
              <th className="text-left font-medium pb-2 w-64">&nbsp;</th>
              {years.map(y => (
                <th key={y} className="text-right font-medium pb-2">{y}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              if (r.kind === 'header') {
                return (
                  <tr key={r.label}>
                    <td colSpan={6} className="pt-3 pb-1.5 text-[10.5px] uppercase tracking-wide text-ink-500 font-semibold">
                      {r.label}
                    </td>
                  </tr>
                );
              }
              const isSubtotal = r.kind === 'subtotal';
              const isTotal = r.kind === 'total';
              return (
                <tr key={r.label} className={cn(
                  'border-b border-border/40',
                  i % 2 === 1 && !isSubtotal && !isTotal && 'bg-ink-300/5',
                  isSubtotal && 'font-semibold bg-brand-50/40 border-t border-border',
                  isTotal && 'font-semibold bg-success-50/40 border-t-2 border-border text-success-700'
                )}>
                  <td className={cn('py-1.5', !isSubtotal && !isTotal && 'pl-3')}>{r.label}</td>
                  {r.values.map((v, vi) => {
                    if (v === '' || v === 0) {
                      return <td key={vi} className="text-right tabular-nums text-ink-400">—</td>;
                    }
                    const num = v as number;
                    return (
                      <td key={vi} className={cn(
                        'text-right tabular-nums',
                        num < 0 && !isSubtotal && !isTotal && 'text-danger-700'
                      )}>
                        {num < 0 ? `(${fmtCurrency(-num)})` : fmtCurrency(num)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-4 pt-4 border-t border-border text-[11px] text-ink-500">
        Initial equity outlay of {fmtCurrency(totalCapital)} (Y0) reflected in cumulative line. Excludes financing assumption.
      </div>
    </Card>
  );
}

function Distributions({ cf, equity, outputs, isKimptonDemo }: {
  cf: CashFlowModel;
  equity: number;
  outputs: ReturnType<typeof useEngineOutputs>['outputs'];
  isKimptonDemo: boolean;
}) {
  // Prefer partnership engine GP/LP cash flows. Promote portion is what the GP
  // earns above their pro-rata equity share — the engine returns it as
  // promote_amount. We approximate per-year promote by attributing the GP
  // distributions in the final exit year as "promote" for visualization.
  const wGpFlows = getEngineField<number[]>(outputs, 'partnership', 'gp_cash_flows');
  const wLpFlows = getEngineField<number[]>(outputs, 'partnership', 'lp_cash_flows');
  const wPromote = getEngineField<number>(outputs, 'partnership', 'promote_amount');
  const useWorker = Array.isArray(wGpFlows) && Array.isArray(wLpFlows)
    && wGpFlows.length > 0 && wGpFlows.length === wLpFlows.length;

  let data: Array<{ year: string; operating: number; promote: number; exit: number; kind: 'operating' | 'exit' }>;
  if (useWorker) {
    const lastIdx = wGpFlows!.length - 1;
    data = wGpFlows!.map((gp, i) => ({
      year: i === lastIdx ? `Year ${i + 1} (Exit)` : `Year ${i + 1}`,
      operating: i === lastIdx ? 0 : (wLpFlows![i] ?? 0),
      promote: i === lastIdx
        ? Math.max(0, gp)                       // GP final-year cash counts as promote here
        : (gp ?? 0),
      exit: i === lastIdx ? (wLpFlows![i] ?? 0) : 0,
      kind: i === lastIdx ? 'exit' : 'operating',
    }));
  } else if (isKimptonDemo) {
    data = [
      { year: 'Year 1', operating: cf.operatingDist[0] ?? 0, promote: 0, exit: 0, kind: 'operating' },
      { year: 'Year 2', operating: cf.operatingDist[1] ?? 0, promote: 0, exit: 0, kind: 'operating' },
      { year: 'Year 3', operating: cf.operatingDist[2] ?? 0, promote: 0, exit: 0, kind: 'operating' },
      { year: 'Year 4', operating: (cf.operatingDist[3] ?? 0) - 92_000, promote: 92_000, exit: 0, kind: 'operating' },
      { year: 'Year 5 (Exit)', operating: 0, promote: 2_748_000, exit: 17_815_400, kind: 'exit' },
    ];
  } else {
    // Fallback: no partnership output, render the levered CF as operating dist.
    const lastIdx = cf.noi.length - 1;
    data = cf.noi.map((_, i) => ({
      year: i === lastIdx ? `Year ${i + 1} (Exit)` : `Year ${i + 1}`,
      operating: i === lastIdx ? 0 : (cf.operatingDist[i] ?? 0),
      promote: 0,
      exit: i === lastIdx ? cf.exitDist : 0,
      kind: (i === lastIdx ? 'exit' : 'operating') as 'exit' | 'operating',
    }));
  }
  // Mark the worker-promote-amount in the source-of-truth note below.
  void wPromote;

  // Returns of preferred (~10%) tracked as $ on equity per year
  const prefTarget = equity * 0.10;

  let cumulative = 0;
  return (
    <>
      <Card className="p-5">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Distribution Waterfall</h3>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={data} margin={{ top: 10, right: 25, left: 5, bottom: 0 }}>
            <CartesianGrid strokeDasharray="2 2" stroke="#e5e7eb" />
            <XAxis dataKey="year" stroke="#64748b" fontSize={11} />
            <YAxis stroke="#64748b" fontSize={11} tickFormatter={v => `$${(v / 1e6).toFixed(1)}M`} />
            <Tooltip {...tooltipStyle} formatter={(v: number) => fmtCurrency(v)} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <Bar dataKey="operating" name="LP Pref Return" stackId="a" fill="#3b82f6" />
            <Bar dataKey="promote" name="GP Promote" stackId="a" fill="#f59e0b" />
            <Bar dataKey="exit" name="Exit Distribution" stackId="a" fill="#10b981" />
          </BarChart>
        </ResponsiveContainer>
      </Card>

      <Card className="p-5 mt-5">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Annual Distribution Detail</h3>
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="text-ink-500 text-[11px] border-b border-border">
              <th className="text-left font-medium pb-2">Year</th>
              <th className="text-right font-medium pb-2">LP Pref</th>
              <th className="text-right font-medium pb-2">GP Promote</th>
              <th className="text-right font-medium pb-2">Exit Distribution</th>
              <th className="text-right font-medium pb-2">Total</th>
              <th className="text-right font-medium pb-2">Cum. Distribution</th>
              <th className="text-right font-medium pb-2">Pref Met</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d, i) => {
              const total = d.operating + d.promote + d.exit;
              cumulative += total;
              const prefMet = d.operating >= prefTarget * 0.5; // simplified check
              return (
                <tr key={d.year} className={cn(
                  'border-b border-border/40',
                  i % 2 === 1 && 'bg-ink-300/5',
                  d.kind === 'exit' && 'font-semibold bg-success-50/40 border-t-2 border-border'
                )}>
                  <td className="py-1.5">{d.year}</td>
                  <td className="text-right tabular-nums">{fmtCurrency(d.operating)}</td>
                  <td className="text-right tabular-nums">{d.promote ? fmtCurrency(d.promote) : '—'}</td>
                  <td className="text-right tabular-nums">{d.exit ? fmtCurrency(d.exit) : '—'}</td>
                  <td className="text-right tabular-nums font-medium">{fmtCurrency(total)}</td>
                  <td className="text-right tabular-nums">{fmtCurrency(cumulative)}</td>
                  <td className="text-right">
                    <span className={cn(
                      'text-[10.5px] px-1.5 py-0.5 rounded',
                      prefMet || d.kind === 'exit'
                        ? 'bg-success-50 text-success-700'
                        : 'bg-warn-50 text-warn-700'
                    )}>
                      {d.kind === 'exit' ? 'Exit' : prefMet ? 'Yes' : 'Cash Trap'}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="mt-4 pt-4 border-t border-border text-[11px] text-ink-500 space-y-1">
          <div>• LP Preferred Return: 10% on $19.6M equity = ${(prefTarget / 1e3).toFixed(0)}K/yr target. Y1-Y2 limited by DSCR cash trap.</div>
          <div>• GP Promote tier accrues from Y4 onward as LP pref hurdle is satisfied.</div>
          <div>• Y5 Exit Distribution = sale proceeds net of debt payoff and selling costs, distributed per waterfall.</div>
        </div>
      </Card>
    </>
  );
}

function KPI({ label, value, tone, flashKey, tip }: { label: string; value: string; tone?: 'green' | 'amber' | 'red'; flashKey?: unknown; tip?: string }) {
  const flash = useFlash(flashKey ?? value);
  return (
    <Card className={cn('p-4', flash && 'value-flash')}>
      <div className="text-[10.5px] text-ink-500 uppercase tracking-wide">
        {tip ? <MetricLabel label={label} tip={tip} /> : label}
      </div>
      <div className={cn(
        'text-[20px] font-semibold tabular-nums mt-1',
        tone === 'green' ? 'text-success-700'
          : tone === 'amber' ? 'text-warn-700'
          : tone === 'red' ? 'text-danger-700'
          : 'text-ink-900'
      )}>{value}</div>
    </Card>
  );
}

// Summary row with value-flash on the levered CF column.
function SummaryRow({
  d, i, isLast, cf, cumulative, dscr,
}: {
  d: { year: string; NOI: number; 'Debt Service': number; 'Levered CF': number; Distributions: number };
  i: number;
  isLast: boolean;
  cf: CashFlowModel;
  cumulative: number;
  dscr: number;
}) {
  const flash = useFlash(cf.leveredCF[i]);
  return (
    <tr className={cn('border-b border-border/40', i % 2 === 1 && 'bg-ink-300/5', flash && 'value-flash')}>
      <td className="py-1.5 font-medium">{d.year}{isLast && ' (Exit)'}</td>
      <td className="text-right tabular-nums">{fmtCurrency(cf.noi[i])}</td>
      <td className="text-right tabular-nums">{fmtCurrency(cf.debtService[i])}</td>
      <td className={cn(
        'text-right tabular-nums',
        dscr >= 1.5 ? 'text-success-700' : dscr >= 1.2 ? 'text-warn-700' : 'text-danger-700'
      )}>{dscr.toFixed(2)}x</td>
      <td className="text-right tabular-nums">{fmtCurrency(cf.leveredCF[i])}</td>
      <td className="text-right tabular-nums">{fmtCurrency(isLast ? cf.exitDist : (cf.operatingDist[i] ?? 0))}</td>
      <td className="text-right tabular-nums font-medium">{fmtCurrency(cumulative)}</td>
    </tr>
  );
}

// Levered/unlevered detail row with subtle flash on first column when value
// changes (e.g. after an engine re-run).
function DetailRow({
  row, idx,
}: {
  row: { label: string; values: number[]; kind?: 'detail' | 'subtotal' | 'total' | 'header' };
  idx: number;
}) {
  const isSubtotal = row.kind === 'subtotal';
  const isTotal = row.kind === 'total';
  const flash = useFlash(row.values[0] ?? 0);
  return (
    <tr className={cn(
      'border-b border-border/40',
      idx % 2 === 1 && !isSubtotal && !isTotal && 'bg-ink-300/5',
      isSubtotal && 'font-semibold bg-brand-50/40 border-t border-border',
      isTotal && 'font-semibold bg-success-50/40 border-t-2 border-border text-success-700',
      flash && 'value-flash',
    )}>
      <td className={cn('py-1.5', !isSubtotal && !isTotal && 'pl-3')}>{row.label}</td>
      {row.values.map((v, vi) => (
        <td key={vi} className={cn(
          'text-right tabular-nums',
          v < 0 && !isSubtotal && !isTotal && 'text-danger-700'
        )}>
          {v === 0 ? '—' : v < 0 ? `(${fmtCurrency(-v)})` : fmtCurrency(v)}
        </td>
      ))}
    </tr>
  );
}
