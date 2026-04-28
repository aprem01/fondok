'use client';
import { useState } from 'react';
import { useParams } from 'next/navigation';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { BarChart3 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import { kimptonAnglerOverview } from '@/lib/mockData';
import { fmtCurrency, fmtMillions, cn } from '@/lib/format';
import { useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';
import { GLOSSARY } from '@/lib/glossary';

const subTabs = ['Operating Statement', 'Departmental', 'Per-Key Metrics', 'Historical vs Projected'];

const tooltipStyle = {
  contentStyle: { background: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, fontSize: 12 },
  labelStyle: { color: '#64748b', fontSize: 11 },
};

// Build USALI-style operating statement rows from existing proforma totals.
// All numbers are in thousands (matching the existing mockData proforma convention).
function buildStatement() {
  const p = kimptonAnglerOverview.proforma;
  const get = (label: string) => p.find(r => r.label === label)!;
  const room = get('Room Revenue');
  const fb = get('F&B Revenue');
  const other = get('Other Revenue');
  const totalRev = get('Total Revenue');
  const opex = get('Operating Expenses');
  const mgmt = get('Management Fee');
  const ffe = get('FF&E Reserve');
  const noi = get('Net Operating Income');

  const years: ('y1' | 'y2' | 'y3' | 'y4' | 'y5')[] = ['y1', 'y2', 'y3', 'y4', 'y5'];

  // Departmental expenses synthesized from realistic ratios for select-service Lifestyle
  // Rooms ~25% of room rev, F&B ~75% of F&B rev, Other ~50%
  const roomsDept = years.map(y => Math.round(room[y] * 0.25));
  const fbDept = years.map(y => Math.round(fb[y] * 0.75));
  const otherDept = years.map(y => Math.round(other[y] * 0.50));
  const deptProfit = years.map((y, i) =>
    totalRev[y] - roomsDept[i] - fbDept[i] - otherDept[i]
  );

  // Undistributed operating expenses — break down the existing aggregate "Operating Expenses" line.
  // Existing opex includes departmental costs above; allocate UOE as ~58% of original opex line.
  // Splits target USALI standard ratios for lifestyle: A&G 8%, S&M 7%, POM 4%, Utilities 4.5%, IT 1.5%
  const uoePct = { ag: 0.08, sm: 0.07, pom: 0.04, util: 0.045, it: 0.015 };
  const ag = years.map(y => Math.round(totalRev[y] * uoePct.ag));
  const sm = years.map(y => Math.round(totalRev[y] * uoePct.sm));
  const pom = years.map(y => Math.round(totalRev[y] * uoePct.pom));
  const util = years.map(y => Math.round(totalRev[y] * uoePct.util));
  const it = years.map(y => Math.round(totalRev[y] * uoePct.it));
  const gop = years.map((_, i) => deptProfit[i] - ag[i] - sm[i] - pom[i] - util[i] - it[i]);

  // Fixed charges
  const insurance = years.map(y => Math.round(totalRev[y] * 0.012));
  const propTax = years.map(y => Math.round(totalRev[y] * 0.028));
  const equipLease = years.map(y => Math.round(totalRev[y] * 0.005));
  const noiCalc = years.map((_, i) => gop[i] - insurance[i] - propTax[i] - equipLease[i]);

  // Net income = NOI - FF&E Reserve - Management Fee
  const netIncome = years.map((y, i) => noiCalc[i] - ffe[y] - mgmt[y]);

  type RowKind = 'group' | 'subtotal' | 'detail' | 'total';
  type Row = { label: string; values: number[]; cagr?: number; kind: RowKind };

  const cagr = (start: number, end: number, years = 4) =>
    Math.pow(end / start, 1 / years) - 1;

  const rows: Row[] = [
    { label: 'REVENUES', values: [], kind: 'group' },
    { label: 'Room Revenue', values: years.map(y => room[y]), cagr: room.cagr, kind: 'detail' },
    { label: 'F&B Revenue', values: years.map(y => fb[y]), cagr: fb.cagr, kind: 'detail' },
    { label: 'Other Revenue', values: years.map(y => other[y]), cagr: other.cagr, kind: 'detail' },
    { label: 'Total Revenue', values: years.map(y => totalRev[y]), cagr: totalRev.cagr, kind: 'subtotal' },

    { label: 'DEPARTMENTAL EXPENSES', values: [], kind: 'group' },
    { label: 'Rooms Department', values: roomsDept, cagr: cagr(roomsDept[0], roomsDept[4]), kind: 'detail' },
    { label: 'F&B Department', values: fbDept, cagr: cagr(fbDept[0], fbDept[4]), kind: 'detail' },
    { label: 'Other Department', values: otherDept, cagr: cagr(otherDept[0], otherDept[4]), kind: 'detail' },
    { label: 'Departmental Profit', values: deptProfit, cagr: cagr(deptProfit[0], deptProfit[4]), kind: 'subtotal' },

    { label: 'UNDISTRIBUTED OPERATING EXPENSES', values: [], kind: 'group' },
    { label: 'Administrative & General', values: ag, cagr: cagr(ag[0], ag[4]), kind: 'detail' },
    { label: 'Sales & Marketing', values: sm, cagr: cagr(sm[0], sm[4]), kind: 'detail' },
    { label: 'Property Operations & Maintenance', values: pom, cagr: cagr(pom[0], pom[4]), kind: 'detail' },
    { label: 'Utilities', values: util, cagr: cagr(util[0], util[4]), kind: 'detail' },
    { label: 'Information & Telecom', values: it, cagr: cagr(it[0], it[4]), kind: 'detail' },
    { label: 'Gross Operating Profit (GOP)', values: gop, cagr: cagr(gop[0], gop[4]), kind: 'subtotal' },

    { label: 'FIXED CHARGES', values: [], kind: 'group' },
    { label: 'Insurance', values: insurance, cagr: cagr(insurance[0], insurance[4]), kind: 'detail' },
    { label: 'Property Taxes', values: propTax, cagr: cagr(propTax[0], propTax[4]), kind: 'detail' },
    { label: 'Equipment Lease', values: equipLease, cagr: cagr(equipLease[0], equipLease[4]), kind: 'detail' },
    { label: 'Net Operating Income', values: noiCalc, cagr: cagr(noiCalc[0], noiCalc[4]), kind: 'subtotal' },

    { label: 'FF&E Reserve', values: years.map(y => ffe[y]), cagr: cagr(ffe.y1, ffe.y5), kind: 'detail' },
    { label: 'Management Fee', values: years.map(y => mgmt[y]), cagr: cagr(mgmt.y1, mgmt.y5), kind: 'detail' },
    { label: 'Net Income', values: netIncome, cagr: cagr(netIncome[0], netIncome[4]), kind: 'total' },
  ];

  return { rows, totals: { totalRev, noi: noiCalc, gop, deptProfit } };
}

const statement = buildStatement();

export default function PLTab({ projectId }: { projectId: number }) {
  const [tab, setTab] = useState('Operating Statement');
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs, previous } = useEngineOutputs(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  if (projectId !== 7) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          <IntroCard
            dismissKey="pl-intro"
            title="The P&L Engine"
            body={
              <>
                Profit and Loss — every dollar of revenue and expense across the hold period,
                formatted in industry-standard <span className="font-semibold">USALI</span> categories
                so you can compare any two hotels apples-to-apples.
              </>
            }
          />
          <EngineHeader
            name="P&L Engine"
            desc="Models room revenue, F&B, and operating expenses across the projection period in USALI format."
            outputs={['Total Revenue', 'NOI', 'GOP', 'Margin']}
            dependsOn={null}
            dealId={dealId}
            engineName="expense"
            runMode="all"
            onRunStart={() => setComputing(true)}
            onRunComplete={() => {
              setComputing(false);
              setRunToken(Date.now());
            }}
          />
          <EngineLegend />
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <BarChart3 size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">No P&L output yet</h3>
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              We need a <span className="font-medium">T-12</span> (the last 12 months of profit &amp; loss) to project
              forward revenue and expenses. Drop it into the Data Room, then run the model.
            </p>
            <Button variant="primary" size="sm" className="mt-4">Run Model</Button>
          </Card>
          <EngineRunHistory dealId={dealId} />
        </div>
        <EngineRightRail />
      </div>
    );
  }

  const { totals } = statement;
  const y1Rev = totals.totalRev.y1 * 1000;
  const y1NOI = totals.noi[0] * 1000;
  const margin = y1NOI / y1Rev;
  const noiCagr = Math.pow(totals.noi[4] / totals.noi[0], 1 / 4) - 1;

  return (
    <div className="flex gap-4">
      <div className="flex-1 min-w-0">
      <IntroCard
        dismissKey="pl-intro"
        title="The P&L Engine"
        body={
          <>
            Profit and Loss — every dollar of revenue and expense across the hold period,
            formatted in industry-standard <span className="font-semibold">USALI</span> categories
            so you can compare any two hotels apples-to-apples.
          </>
        }
      />
      <EngineHeader
        name="P&L Engine"
        desc="Models room revenue, F&B, and operating expenses across the projection period in USALI format."
        outputs={['Total Revenue', 'NOI', 'GOP', 'Margin']}
        dependsOn={null}
        complete
        dealId={dealId}
        engineName="expense"
        runMode="all"
        onRunStart={() => setComputing(true)}
        onRunComplete={() => {
          setComputing(false);
          setRunToken(Date.now());
        }}
      />

      <WhatJustHappened
        engine="expense"
        engineLabel="P&L"
        outputs={outputs}
        previous={previous}
        runToken={runToken}
      />

      <div className={cn('grid grid-cols-4 gap-4 mb-5', computing && 'pointer-events-none opacity-60')}>
        <KPI label="Year 1 Revenue" tip="Total top-line revenue projected for the first full year of ownership — rooms, F&B, and other revenue combined." value={fmtMillions(y1Rev, 2)} flashKey={y1Rev} />
        <KPI label="Year 1 NOI" tip={GLOSSARY['NOI']} value={fmtMillions(y1NOI, 2)} tone="green" flashKey={y1NOI} />
        <KPI label="NOI Margin" tip="NOI as a percentage of total revenue. Higher margins mean a more efficient hotel — typical for select-service hotels: 30–40%." value={`${(margin * 100).toFixed(1)}%`} flashKey={margin} />
        <KPI label="5-Year NOI CAGR" tip="Compound Annual Growth Rate of NOI over the five-year hold. How fast the hotel's earning power grows year-over-year." value={`${(noiCagr * 100).toFixed(1)}%`} tone="green" flashKey={noiCagr} />
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
        {tab === 'Operating Statement' && <OperatingStatement />}
        {tab === 'Departmental' && <Departmental />}
        {tab === 'Per-Key Metrics' && <PerKey />}
        {tab === 'Historical vs Projected' && <HistoricalProjected />}
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

function OperatingStatement() {
  const { rows } = statement;
  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">USALI Operating Statement</h3>
        <span className="text-[11px] text-ink-500">($ in 000s, FYE Dec 31)</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12px] min-w-[700px]">
          <thead>
            <tr className="text-ink-500 text-[10.5px] border-b border-border">
              <th className="text-left font-medium pb-2 w-72">&nbsp;</th>
              <th className="text-right font-medium pb-2">Year 1</th>
              <th className="text-right font-medium pb-2">Year 2</th>
              <th className="text-right font-medium pb-2">Year 3</th>
              <th className="text-right font-medium pb-2">Year 4</th>
              <th className="text-right font-medium pb-2">Year 5</th>
              <th className="text-right font-medium pb-2">CAGR</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              if (r.kind === 'group') {
                return (
                  <tr key={r.label}>
                    <td colSpan={7} className="pt-3 pb-1.5 text-[10.5px] uppercase tracking-wide text-ink-500 font-semibold">
                      {r.label}
                    </td>
                  </tr>
                );
              }
              const isSubtotal = r.kind === 'subtotal';
              const isTotal = r.kind === 'total';
              const tint = i % 2 === 0 ? '' : 'bg-ink-300/5';
              return (
                <tr key={r.label} className={cn(
                  'border-b border-border/40',
                  tint,
                  isSubtotal && 'font-semibold bg-brand-50/40 border-t border-border',
                  isTotal && 'font-semibold bg-success-50/40 border-t-2 border-border text-success-700'
                )}>
                  <td className={cn('py-1.5', !isSubtotal && !isTotal && 'pl-3')}>{r.label}</td>
                  {r.values.map((v, vi) => (
                    <td key={vi} className="text-right tabular-nums">{v.toLocaleString()}</td>
                  ))}
                  <td className="text-right tabular-nums">
                    {r.cagr !== undefined ? `${(r.cagr * 100).toFixed(1)}%` : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-4 pt-4 border-t border-border text-[11px] text-ink-500 space-y-1">
        <div>• Departmental expenses synthesized at USALI ratios: Rooms 25% of room rev, F&B 75% of F&B rev, Other 50%.</div>
        <div>• Undistributed operating expenses follow lifestyle-tier benchmarks (A&G 8%, S&M 7%, POM 4%, Utilities 4.5%, IT 1.5%).</div>
      </div>
    </Card>
  );
}

function Departmental() {
  const p = kimptonAnglerOverview.proforma;
  const room = p.find(r => r.label === 'Room Revenue')!;
  const fb = p.find(r => r.label === 'F&B Revenue')!;
  const other = p.find(r => r.label === 'Other Revenue')!;
  const total = p.find(r => r.label === 'Total Revenue')!;
  const keys = kimptonAnglerOverview.general.keys;

  // Year-1 (in 000s)
  const depts = [
    { name: 'Rooms', revenue: room.y1, expense: Math.round(room.y1 * 0.25), tone: 'brand' },
    { name: 'Food & Beverage', revenue: fb.y1, expense: Math.round(fb.y1 * 0.75), tone: 'amber' },
    { name: 'Other Operating', revenue: other.y1, expense: Math.round(other.y1 * 0.50), tone: 'success' },
  ].map(d => ({
    ...d,
    profit: d.revenue - d.expense,
    margin: (d.revenue - d.expense) / d.revenue,
    profitPerKey: ((d.revenue - d.expense) * 1000) / keys,
  }));

  const totalRev = total.y1;
  const totalExp = depts.reduce((s, d) => s + d.expense, 0);
  const totalProfit = totalRev - totalExp;
  const totalCard = {
    name: 'Total',
    revenue: totalRev,
    expense: totalExp,
    profit: totalProfit,
    margin: totalProfit / totalRev,
    profitPerKey: (totalProfit * 1000) / keys,
    tone: 'slate',
  };

  return (
    <>
      <div className="grid grid-cols-4 gap-4">
        {[...depts, totalCard].map(d => (
          <Card key={d.name} className={cn('p-5', d.name === 'Total' && 'bg-brand-50 border-brand-100')}>
            <div className="text-[11px] text-ink-500 uppercase tracking-wide">{d.name}</div>
            <div className="text-[18px] font-semibold tabular-nums mt-1 text-ink-900">
              {fmtMillions(d.revenue * 1000, 2)}
            </div>
            <div className="mt-3 space-y-1.5 text-[12px]">
              <Row k="Revenue" v={`$${d.revenue.toLocaleString()}K`} />
              <Row k="Direct Expense" v={`$${d.expense.toLocaleString()}K`} />
              <Row k="Dept Profit" v={`$${d.profit.toLocaleString()}K`} bold />
              <Row k="Profit Margin" v={`${(d.margin * 100).toFixed(1)}%`} />
              <Row k="Profit / Key" v={`$${Math.round(d.profitPerKey).toLocaleString()}`} />
            </div>
          </Card>
        ))}
      </div>
      <Card className="p-5 mt-5">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Departmental Mix (Year 1)</h3>
        <div className="space-y-2">
          {depts.map(d => {
            const pct = (d.revenue / totalRev) * 100;
            const fill =
              d.tone === 'brand' ? 'bg-brand-500'
              : d.tone === 'amber' ? 'bg-warn-500'
              : 'bg-success-500';
            return (
              <div key={d.name}>
                <div className="flex justify-between text-[11.5px] mb-1">
                  <span className="text-ink-700">{d.name}</span>
                  <span className="tabular-nums text-ink-500">{pct.toFixed(1)}% of revenue</span>
                </div>
                <div className="w-full h-2.5 bg-ink-300/20 rounded">
                  <div className={cn('h-2.5 rounded', fill)} style={{ width: `${pct}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      </Card>
    </>
  );
}

function PerKey() {
  const p = kimptonAnglerOverview.proforma;
  const keys = kimptonAnglerOverview.general.keys;
  const room = p.find(r => r.label === 'Room Revenue')!;
  const fb = p.find(r => r.label === 'F&B Revenue')!;
  const total = p.find(r => r.label === 'Total Revenue')!;
  const noi = p.find(r => r.label === 'Net Operating Income')!;

  const years: ('y1' | 'y2' | 'y3' | 'y4' | 'y5')[] = ['y1', 'y2', 'y3', 'y4', 'y5'];
  const availableRoomNights = keys * 365;

  // GOP synthesized as ~37% of total revenue (NOI is ~31%)
  const gop = years.map(y => Math.round(total[y] * 0.37 * 1000));

  // ADR/Occupancy schedule — Y1 ramp from renovation, stabilizes Y3
  const occ = [0.701, 0.738, 0.762, 0.776, 0.787];
  const rows = [
    { label: 'Total Revenue / Key', vals: years.map(y => (total[y] * 1000) / keys), fmt: 'k' as const },
    { label: 'Rooms Revenue / Key', vals: years.map(y => (room[y] * 1000) / keys), fmt: 'k' as const },
    { label: 'F&B Revenue / Key', vals: years.map(y => (fb[y] * 1000) / keys), fmt: 'k' as const },
    { label: 'GOP / Key', vals: gop.map(v => v / keys), fmt: 'k' as const, bold: true },
    { label: 'NOI / Key', vals: years.map(y => (noi[y] * 1000) / keys), fmt: 'k' as const, bold: true },
    { label: 'RevPAR', vals: years.map(y => (room[y] * 1000) / availableRoomNights), fmt: 'd' as const },
    { label: 'ADR', vals: years.map((y, i) => (room[y] * 1000) / (availableRoomNights * occ[i])), fmt: 'd' as const },
    { label: 'Occupancy', vals: occ.map(v => v * 100), fmt: 'pct' as const },
  ];

  const fmtVal = (v: number, fmt: 'k' | 'd' | 'pct') => {
    if (fmt === 'k') return `$${Math.round(v).toLocaleString()}`;
    if (fmt === 'd') return `$${v.toFixed(0)}`;
    return `${v.toFixed(1)}%`;
  };

  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">Per-Key Operating Metrics</h3>
        <span className="text-[11px] text-ink-500">{keys} keys · 132-room lifestyle boutique</span>
      </div>
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="text-ink-500 text-[10.5px] border-b border-border">
            <th className="text-left font-medium pb-2">Metric</th>
            <th className="text-right font-medium pb-2">Year 1</th>
            <th className="text-right font-medium pb-2">Year 2</th>
            <th className="text-right font-medium pb-2">Year 3</th>
            <th className="text-right font-medium pb-2">Year 4</th>
            <th className="text-right font-medium pb-2">Year 5</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.label} className={cn(
              'border-b border-border/40',
              i % 2 === 1 && 'bg-ink-300/5',
              r.bold && 'font-semibold bg-brand-50/40'
            )}>
              <td className="py-2">{r.label}</td>
              {r.vals.map((v, vi) => (
                <td key={vi} className="text-right tabular-nums">{fmtVal(v, r.fmt)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function HistoricalProjected() {
  // Historical: 3 years of pre-acquisition operating performance (in $000s)
  // Projected: y1-y5 from proforma
  const p = kimptonAnglerOverview.proforma;
  const totalRev = p.find(r => r.label === 'Total Revenue')!;
  const noi = p.find(r => r.label === 'Net Operating Income')!;

  // Synthesize historical (declining pre-renovation, depressed NOI)
  const data = [
    { year: '2023', revenue: 13_240, noi: 2_120, gop: 4_520, kind: 'historical' },
    { year: '2024', revenue: 13_680, noi: 2_280, gop: 4_780, kind: 'historical' },
    { year: '2025', revenue: 13_950, noi: 2_481, gop: 4_950, kind: 'historical' },
    { year: '2026', revenue: totalRev.y1, noi: noi.y1, gop: Math.round(totalRev.y1 * 0.37), kind: 'projected' },
    { year: '2027', revenue: totalRev.y2, noi: noi.y2, gop: Math.round(totalRev.y2 * 0.37), kind: 'projected' },
    { year: '2028', revenue: totalRev.y3, noi: noi.y3, gop: Math.round(totalRev.y3 * 0.37), kind: 'projected' },
    { year: '2029', revenue: totalRev.y4, noi: noi.y4, gop: Math.round(totalRev.y4 * 0.37), kind: 'projected' },
    { year: '2030', revenue: totalRev.y5, noi: noi.y5, gop: Math.round(totalRev.y5 * 0.37), kind: 'projected' },
  ];

  return (
    <>
      <Card className="p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-[13px] font-semibold text-ink-900">Historical vs Projected Performance</h3>
            <div className="text-[11px] text-ink-500 mt-0.5">Pre-acquisition T-3 (2023-2025) and underwritten projection (2026-2030) · $ in 000s</div>
          </div>
          <div className="flex items-center gap-3 text-[11px]">
            <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 bg-[#0d9488]" /> Historical</span>
            <span className="flex items-center gap-1.5"><span className="w-3 h-0.5 bg-[#f59e0b]" /> Projected</span>
          </div>
        </div>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={data} margin={{ top: 10, right: 25, left: 5, bottom: 0 }}>
            <CartesianGrid strokeDasharray="2 2" stroke="#e5e7eb" />
            <XAxis dataKey="year" stroke="#64748b" fontSize={11} />
            <YAxis stroke="#64748b" fontSize={11} tickFormatter={v => `$${(v / 1000).toFixed(1)}M`} />
            <Tooltip {...tooltipStyle} formatter={(v: number) => `$${v.toLocaleString()}K`} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <ReferenceLine x="2025" stroke="#94a3b8" strokeDasharray="3 3" label={{ value: 'Acquisition', position: 'top', fontSize: 10, fill: '#64748b' }} />
            <Line type="monotone" dataKey="revenue" name="Total Revenue" stroke="#0d9488" strokeWidth={2.5} dot={{ r: 3 }} />
            <Line type="monotone" dataKey="gop" name="GOP" stroke="#f59e0b" strokeWidth={2.5} dot={{ r: 3 }} />
            <Line type="monotone" dataKey="noi" name="NOI" stroke="#3b82f6" strokeWidth={2.5} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </Card>

      <Card className="p-5 mt-5">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Detail Table</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] min-w-[600px]">
            <thead>
              <tr className="text-ink-500 text-[10.5px] border-b border-border">
                <th className="text-left font-medium pb-2">Year</th>
                <th className="text-right font-medium pb-2">Total Revenue</th>
                <th className="text-right font-medium pb-2">GOP</th>
                <th className="text-right font-medium pb-2">NOI</th>
                <th className="text-right font-medium pb-2">NOI Margin</th>
                <th className="text-right font-medium pb-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d, i) => {
                const margin = (d.noi / d.revenue) * 100;
                return (
                  <tr key={d.year} className={cn(
                    'border-b border-border/40',
                    i % 2 === 1 && 'bg-ink-300/5'
                  )}>
                    <td className="py-1.5 font-medium">{d.year}</td>
                    <td className="text-right tabular-nums">{d.revenue.toLocaleString()}</td>
                    <td className="text-right tabular-nums">{d.gop.toLocaleString()}</td>
                    <td className="text-right tabular-nums">{d.noi.toLocaleString()}</td>
                    <td className="text-right tabular-nums">{margin.toFixed(1)}%</td>
                    <td className="text-right">
                      <span className={cn(
                        'text-[10.5px] px-1.5 py-0.5 rounded',
                        d.kind === 'historical'
                          ? 'bg-[#ccfbf1] text-[#0f766e]'
                          : 'bg-warn-50 text-warn-700'
                      )}>{d.kind === 'historical' ? 'Historical' : 'Projected'}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
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

function Row({ k, v, bold }: { k: string; v: string; bold?: boolean }) {
  return (
    <div className="flex justify-between py-1 border-b border-border/40 last:border-0">
      <span className="text-ink-500">{k}</span>
      <span className={cn('tabular-nums', bold ? 'font-semibold text-ink-900' : 'font-medium text-ink-900')}>{v}</span>
    </div>
  );
}
