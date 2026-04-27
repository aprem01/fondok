'use client';
import { useState } from 'react';
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, LabelList,
} from 'recharts';
import { Activity } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import { kimptonAnglerOverview } from '@/lib/mockData';
import { fmtCurrency, fmtMillions, cn } from '@/lib/format';

const subTabs = ['Cash Flow Summary', 'Levered Detail', 'Unlevered Detail', 'Distributions'];

const tooltipStyle = {
  contentStyle: { background: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, fontSize: 12 },
  labelStyle: { color: '#64748b', fontSize: 11 },
};

// Build cash flow schedule. NOI is in $000s in proforma; convert to dollars here.
function buildCashFlow() {
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
  const unleveredWithExit = [...unlevered];
  // Y5 = operations + exit proceeds
  const y5Operations = unlevered[4];
  const y5WithExit = y5Operations + exitNet;

  return {
    noi, debtService, leveredCF, capex, ffe, interest, principal,
    distributions, operatingDist, exitDist, cashSweep,
    begCash, endCash,
    unlevered, y5Operations, y5WithExit, exitNet,
  };
}

const cf = buildCashFlow();

export default function CashFlowTab({ projectId }: { projectId: number }) {
  const [tab, setTab] = useState('Cash Flow Summary');

  if (projectId !== 7) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          <EngineHeader
            name="Cash Flow Engine"
            desc="Computes levered and unlevered cash flow from operations through hold period."
            outputs={['Levered CF', 'Unlevered CF', 'CoC', 'DSCR']}
            dependsOn="P&L"
          />
          <EngineLegend />
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <Activity size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">No Cash Flow Output</h3>
            <p className="text-[12.5px] text-ink-500 mt-1">Run the cash flow engine to populate levered and unlevered schedules.</p>
            <Button variant="primary" size="sm" className="mt-4">Run Model</Button>
          </Card>
        </div>
        <EngineRightRail />
      </div>
    );
  }

  const sumLevered = cf.leveredCF.reduce((s, v) => s + v, 0);
  const sumUnlevered = cf.unlevered.reduce((s, v) => s + v, 0);
  const equity = kimptonAnglerOverview.sources.find(s => s.label === 'Equity')!.amount;
  const avgCoC = (cf.operatingDist.reduce((s, v) => s + v, 0) / 4) / equity;
  const cumulativeDist = cf.distributions.reduce((s, v) => s + v, 0);

  return (
    <div className="flex gap-4">
      <div className="flex-1 min-w-0">
      <EngineHeader
        name="Cash Flow Engine"
        desc="Computes levered and unlevered cash flow from operations through hold period."
        outputs={['Levered CF', 'Unlevered CF', 'CoC', 'DSCR']}
        dependsOn="P&L"
        complete
      />

      <div className="grid grid-cols-4 gap-4 mb-5">
        <KPI label="5-Yr Levered CF" value={fmtMillions(sumLevered, 2)} tone="green" />
        <KPI label="5-Yr Unlevered CF" value={fmtMillions(sumUnlevered, 2)} />
        <KPI label="Avg Cash-on-Cash" value={`${(avgCoC * 100).toFixed(1)}%`} />
        <KPI label="Cumulative Distributions" value={fmtMillions(cumulativeDist, 2)} tone="green" />
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

      {tab === 'Cash Flow Summary' && <Summary />}
      {tab === 'Levered Detail' && <LeveredDetail />}
      {tab === 'Unlevered Detail' && <UnleveredDetail />}
      {tab === 'Distributions' && <Distributions />}
      </div>
      <EngineRightRail />
    </div>
  );
}

function Summary() {
  const data = cf.noi.map((n, i) => ({
    year: `Year ${i + 1}`,
    NOI: Math.round(n / 1000),
    'Debt Service': Math.round(cf.debtService[i] / 1000),
    'Levered CF': Math.round(cf.leveredCF[i] / 1000),
    Distributions: Math.round((i < 4 ? cf.operatingDist[i] : cf.exitDist) / 1000),
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
              cumulative += i < 4 ? cf.operatingDist[i] : cf.exitDist;
              const dscr = cf.noi[i] / cf.debtService[i];
              return (
                <tr key={d.year} className={cn('border-b border-border/40', i % 2 === 1 && 'bg-ink-300/5')}>
                  <td className="py-1.5 font-medium">{d.year}{i === 4 && ' (Exit)'}</td>
                  <td className="text-right tabular-nums">{fmtCurrency(cf.noi[i])}</td>
                  <td className="text-right tabular-nums">{fmtCurrency(cf.debtService[i])}</td>
                  <td className={cn(
                    'text-right tabular-nums',
                    dscr >= 1.5 ? 'text-success-700' : dscr >= 1.2 ? 'text-warn-700' : 'text-danger-700'
                  )}>{dscr.toFixed(2)}x</td>
                  <td className="text-right tabular-nums">{fmtCurrency(cf.leveredCF[i])}</td>
                  <td className="text-right tabular-nums">{fmtCurrency(i < 4 ? cf.operatingDist[i] : cf.exitDist)}</td>
                  <td className="text-right tabular-nums font-medium">{fmtCurrency(cumulative)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>
    </>
  );
}

function LeveredDetail() {
  const years = ['Year 1', 'Year 2', 'Year 3', 'Year 4', 'Year 5'];

  type Row = { label: string; values: number[]; kind?: 'detail' | 'subtotal' | 'total' | 'header' };
  const rows: Row[] = [
    { label: 'Beginning Cash Balance', values: cf.begCash, kind: 'detail' },
    { label: 'OPERATING ACTIVITIES', values: [0, 0, 0, 0, 0], kind: 'header' },
    { label: 'Net Operating Income', values: cf.noi, kind: 'detail' },
    { label: 'FF&E Reserve', values: cf.ffe.map(v => -v), kind: 'detail' },
    { label: 'Capital Expenditures', values: cf.capex.map(v => -v), kind: 'detail' },
    { label: 'Net Operating Cash Flow', values: cf.noi.map((n, i) => n - cf.ffe[i] - cf.capex[i]), kind: 'subtotal' },
    { label: 'FINANCING ACTIVITIES', values: [0, 0, 0, 0, 0], kind: 'header' },
    { label: 'Interest Expense', values: cf.interest.map(v => -v), kind: 'detail' },
    { label: 'Principal Repayment', values: cf.principal.map(v => -v), kind: 'detail' },
    { label: 'Total Debt Service', values: cf.debtService.map(v => -v), kind: 'subtotal' },
    { label: 'Levered Cash Flow', values: cf.leveredCF, kind: 'subtotal' },
    { label: 'EQUITY DISTRIBUTIONS', values: [0, 0, 0, 0, 0], kind: 'header' },
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
                  {r.values.map((v, vi) => (
                    <td key={vi} className={cn(
                      'text-right tabular-nums',
                      v < 0 && !isSubtotal && !isTotal && 'text-danger-700'
                    )}>
                      {v === 0 ? '—' : v < 0 ? `(${fmtCurrency(-v).replace('$', '$')})` : fmtCurrency(v)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function UnleveredDetail() {
  const years = ['Year 1', 'Year 2', 'Year 3', 'Year 4', 'Year 5'];

  type Row = { label: string; values: (number | string)[]; kind?: 'detail' | 'subtotal' | 'total' | 'header' };

  // Initial equity for unlevered = total capital (no debt)
  const totalCapital = kimptonAnglerOverview.investment.totalCapital;
  const unleveredWithExit = [...cf.unlevered];
  unleveredWithExit[4] = cf.unlevered[4] + cf.exitNet;

  // Cumulative
  const cumUnlevered: number[] = [];
  let acc = -totalCapital;
  for (let i = 0; i < 5; i++) {
    acc += unleveredWithExit[i];
    cumUnlevered.push(acc);
  }

  const rows: Row[] = [
    { label: 'OPERATIONS', values: ['', '', '', '', ''], kind: 'header' },
    { label: 'Net Operating Income', values: cf.noi, kind: 'detail' },
    { label: 'FF&E Reserve', values: cf.ffe.map(v => -v), kind: 'detail' },
    { label: 'Capital Expenditures', values: cf.capex.map(v => -v), kind: 'detail' },
    { label: 'Operating Cash Flow', values: cf.unlevered, kind: 'subtotal' },
    { label: 'TERMINAL VALUE', values: ['', '', '', '', ''], kind: 'header' },
    { label: 'Gross Sale Proceeds', values: [0, 0, 0, 0, kimptonAnglerOverview.reversion.grossSalePrice], kind: 'detail' },
    { label: 'Selling Costs', values: [0, 0, 0, 0, -kimptonAnglerOverview.reversion.sellingCosts], kind: 'detail' },
    { label: 'Net Sale Proceeds', values: [0, 0, 0, 0, cf.exitNet], kind: 'subtotal' },
    { label: 'Unlevered Cash Flow', values: unleveredWithExit, kind: 'total' },
    { label: 'Cumulative (Net of Equity)', values: cumUnlevered, kind: 'subtotal' },
  ];

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">Unlevered Cash Flow Detail</h3>
        <span className="text-[11px] text-ink-500">No debt assumed · {(kimptonAnglerOverview.returns.unleveredIRR * 100).toFixed(1)}% Unlevered IRR · Exit Year 5</span>
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

function Distributions() {
  const equity = kimptonAnglerOverview.sources.find(s => s.label === 'Equity')!.amount;
  const data = [
    { year: 'Year 1', operating: cf.operatingDist[0], promote: 0, exit: 0, kind: 'operating' },
    { year: 'Year 2', operating: cf.operatingDist[1], promote: 0, exit: 0, kind: 'operating' },
    { year: 'Year 3', operating: cf.operatingDist[2], promote: 0, exit: 0, kind: 'operating' },
    { year: 'Year 4', operating: cf.operatingDist[3] - 92_000, promote: 92_000, exit: 0, kind: 'operating' },
    { year: 'Year 5 (Exit)', operating: 0, promote: 2_748_000, exit: 17_815_400, kind: 'exit' },
  ];

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

function KPI({ label, value, tone }: { label: string; value: string; tone?: 'green' | 'amber' | 'red' }) {
  return (
    <Card className="p-4">
      <div className="text-[10.5px] text-ink-500 uppercase tracking-wide">{label}</div>
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
