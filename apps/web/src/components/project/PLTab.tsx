'use client';
import { useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { BarChart3 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import type { EngineOutputsResponse } from '@/lib/api';
import { kimptonAnglerOverview } from '@/lib/mockData';
import { fmtCurrency, fmtMillions, cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';
import { GLOSSARY } from '@/lib/glossary';

const subTabs = ['Operating Statement', 'Departmental', 'Per-Key Metrics', 'Historical vs Projected'];

const tooltipStyle = {
  contentStyle: { background: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, fontSize: 12 },
  labelStyle: { color: '#64748b', fontSize: 11 },
};

// Worker output shape (loose) — mirrors apps/worker/app/engines/expense.py + revenue.py.
interface ExpenseYearWorker {
  year: number;
  total_revenue: number;
  dept_expenses: { rooms: number; food_beverage: number; other_operated: number; total: number };
  undistributed: {
    administrative_general: number;
    information_telecom: number;
    sales_marketing: number;
    property_operations: number;
    utilities: number;
    total: number;
  };
  mgmt_fee: number;
  ffe_reserve: number;
  fixed_charges: { property_taxes: number; insurance: number; rent: number; other_fixed: number; total: number };
  gop: number;
  noi: number;
}

interface FBYearWorker {
  year: number;
  rooms_revenue: number;
  fb_revenue: number;
  other_revenue: number;
  total_revenue: number;
}

// Revenue engine projection year — mirrors fondok_schemas.RevenueProjectionYear.
// Occupancy is a 0..1 ratio; ADR / RevPAR are dollars (not thousands).
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

type RowKind = 'group' | 'subtotal' | 'detail' | 'total';
interface StatementRow { label: string; values: number[]; cagr?: number; kind: RowKind }
interface StatementResult {
  rows: StatementRow[];
  totals: { totalRev: number[]; noi: number[]; gop: number[]; deptProfit: number[] };
}

const cagrCalc = (start: number, end: number, years = 4) =>
  start > 0 ? Math.pow(end / start, 1 / years) - 1 : 0;

// Build USALI-style operating statement from worker expense + fb engine output.
// Numbers returned in $000s to match the rendered table convention.
function buildStatementFromWorker(
  expenseYears: ExpenseYearWorker[],
  fbYears: FBYearWorker[] | null,
): StatementResult {
  // Trim to 5 years; pad with zeros if worker returned fewer.
  const ey = expenseYears.slice(0, 5);
  const toThousands = (v: number) => Math.round(v / 1000);
  const pad = <T,>(arr: T[], filler: T): T[] =>
    arr.length >= 5 ? arr.slice(0, 5) : [...arr, ...Array(5 - arr.length).fill(filler)];

  const totalRev = pad(ey.map(y => toThousands(y.total_revenue)), 0);
  const fbBy = fbYears?.slice(0, 5) ?? [];
  const room = pad(fbBy.map(y => toThousands(y.rooms_revenue)), 0);
  const fb = pad(fbBy.map(y => toThousands(y.fb_revenue)), 0);
  const other = pad(fbBy.map(y => toThousands(y.other_revenue)), 0);

  const roomsDept = pad(ey.map(y => toThousands(y.dept_expenses.rooms)), 0);
  const fbDept = pad(ey.map(y => toThousands(y.dept_expenses.food_beverage)), 0);
  const otherDept = pad(ey.map(y => toThousands(y.dept_expenses.other_operated)), 0);
  const deptProfit = totalRev.map((tr, i) => tr - roomsDept[i] - fbDept[i] - otherDept[i]);

  const ag = pad(ey.map(y => toThousands(y.undistributed.administrative_general)), 0);
  const sm = pad(ey.map(y => toThousands(y.undistributed.sales_marketing)), 0);
  const pom = pad(ey.map(y => toThousands(y.undistributed.property_operations)), 0);
  const util = pad(ey.map(y => toThousands(y.undistributed.utilities)), 0);
  const it = pad(ey.map(y => toThousands(y.undistributed.information_telecom)), 0);
  const gop = pad(ey.map(y => toThousands(y.gop)), 0);

  const insurance = pad(ey.map(y => toThousands(y.fixed_charges.insurance)), 0);
  const propTax = pad(ey.map(y => toThousands(y.fixed_charges.property_taxes)), 0);
  const equipLease = pad(ey.map(y => toThousands(y.fixed_charges.other_fixed + y.fixed_charges.rent)), 0);
  const ffe = pad(ey.map(y => toThousands(y.ffe_reserve)), 0);
  const mgmt = pad(ey.map(y => toThousands(y.mgmt_fee)), 0);
  const noiCalc = pad(ey.map(y => toThousands(y.noi)), 0);
  const netIncome = noiCalc.map((n, i) => n - ffe[i] - mgmt[i]);

  const rows: StatementRow[] = [
    { label: 'REVENUES', values: [], kind: 'group' },
    { label: 'Room Revenue', values: room, cagr: cagrCalc(room[0], room[4]), kind: 'detail' },
    { label: 'F&B Revenue', values: fb, cagr: cagrCalc(fb[0], fb[4]), kind: 'detail' },
    { label: 'Other Revenue', values: other, cagr: cagrCalc(other[0], other[4]), kind: 'detail' },
    { label: 'Total Revenue', values: totalRev, cagr: cagrCalc(totalRev[0], totalRev[4]), kind: 'subtotal' },

    { label: 'DEPARTMENTAL EXPENSES', values: [], kind: 'group' },
    { label: 'Rooms Department', values: roomsDept, cagr: cagrCalc(roomsDept[0], roomsDept[4]), kind: 'detail' },
    { label: 'F&B Department', values: fbDept, cagr: cagrCalc(fbDept[0], fbDept[4]), kind: 'detail' },
    { label: 'Other Department', values: otherDept, cagr: cagrCalc(otherDept[0], otherDept[4]), kind: 'detail' },
    { label: 'Departmental Profit', values: deptProfit, cagr: cagrCalc(deptProfit[0], deptProfit[4]), kind: 'subtotal' },

    { label: 'UNDISTRIBUTED OPERATING EXPENSES', values: [], kind: 'group' },
    { label: 'Administrative & General', values: ag, cagr: cagrCalc(ag[0], ag[4]), kind: 'detail' },
    { label: 'Sales & Marketing', values: sm, cagr: cagrCalc(sm[0], sm[4]), kind: 'detail' },
    { label: 'Property Operations & Maintenance', values: pom, cagr: cagrCalc(pom[0], pom[4]), kind: 'detail' },
    { label: 'Utilities', values: util, cagr: cagrCalc(util[0], util[4]), kind: 'detail' },
    { label: 'Information & Telecom', values: it, cagr: cagrCalc(it[0], it[4]), kind: 'detail' },
    { label: 'Gross Operating Profit (GOP)', values: gop, cagr: cagrCalc(gop[0], gop[4]), kind: 'subtotal' },

    { label: 'FIXED CHARGES', values: [], kind: 'group' },
    { label: 'Insurance', values: insurance, cagr: cagrCalc(insurance[0], insurance[4]), kind: 'detail' },
    { label: 'Property Taxes', values: propTax, cagr: cagrCalc(propTax[0], propTax[4]), kind: 'detail' },
    { label: 'Equipment Lease', values: equipLease, cagr: cagrCalc(equipLease[0], equipLease[4]), kind: 'detail' },
    { label: 'Net Operating Income', values: noiCalc, cagr: cagrCalc(noiCalc[0], noiCalc[4]), kind: 'subtotal' },

    { label: 'FF&E Reserve', values: ffe, cagr: cagrCalc(ffe[0], ffe[4]), kind: 'detail' },
    { label: 'Management Fee', values: mgmt, cagr: cagrCalc(mgmt[0], mgmt[4]), kind: 'detail' },
    { label: 'Net Income', values: netIncome, cagr: cagrCalc(netIncome[0], netIncome[4]), kind: 'total' },
  ];

  return { rows, totals: { totalRev, noi: noiCalc, gop, deptProfit } };
}

// Build USALI-style operating statement rows from existing proforma totals.
// All numbers are in thousands (matching the existing mockData proforma convention).
function buildStatement(): StatementResult {
  const p = kimptonAnglerOverview.proforma;
  const get = (label: string) => p.find(r => r.label === label)!;
  const room = get('Room Revenue');
  const fb = get('F&B Revenue');
  const other = get('Other Revenue');
  const totalRev = get('Total Revenue');
  const mgmt = get('Management Fee');
  const ffe = get('FF&E Reserve');

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

  const rows: StatementRow[] = [
    { label: 'REVENUES', values: [], kind: 'group' },
    { label: 'Room Revenue', values: years.map(y => room[y]), cagr: room.cagr, kind: 'detail' },
    { label: 'F&B Revenue', values: years.map(y => fb[y]), cagr: fb.cagr, kind: 'detail' },
    { label: 'Other Revenue', values: years.map(y => other[y]), cagr: other.cagr, kind: 'detail' },
    { label: 'Total Revenue', values: years.map(y => totalRev[y]), cagr: totalRev.cagr, kind: 'subtotal' },

    { label: 'DEPARTMENTAL EXPENSES', values: [], kind: 'group' },
    { label: 'Rooms Department', values: roomsDept, cagr: cagrCalc(roomsDept[0], roomsDept[4]), kind: 'detail' },
    { label: 'F&B Department', values: fbDept, cagr: cagrCalc(fbDept[0], fbDept[4]), kind: 'detail' },
    { label: 'Other Department', values: otherDept, cagr: cagrCalc(otherDept[0], otherDept[4]), kind: 'detail' },
    { label: 'Departmental Profit', values: deptProfit, cagr: cagrCalc(deptProfit[0], deptProfit[4]), kind: 'subtotal' },

    { label: 'UNDISTRIBUTED OPERATING EXPENSES', values: [], kind: 'group' },
    { label: 'Administrative & General', values: ag, cagr: cagrCalc(ag[0], ag[4]), kind: 'detail' },
    { label: 'Sales & Marketing', values: sm, cagr: cagrCalc(sm[0], sm[4]), kind: 'detail' },
    { label: 'Property Operations & Maintenance', values: pom, cagr: cagrCalc(pom[0], pom[4]), kind: 'detail' },
    { label: 'Utilities', values: util, cagr: cagrCalc(util[0], util[4]), kind: 'detail' },
    { label: 'Information & Telecom', values: it, cagr: cagrCalc(it[0], it[4]), kind: 'detail' },
    { label: 'Gross Operating Profit (GOP)', values: gop, cagr: cagrCalc(gop[0], gop[4]), kind: 'subtotal' },

    { label: 'FIXED CHARGES', values: [], kind: 'group' },
    { label: 'Insurance', values: insurance, cagr: cagrCalc(insurance[0], insurance[4]), kind: 'detail' },
    { label: 'Property Taxes', values: propTax, cagr: cagrCalc(propTax[0], propTax[4]), kind: 'detail' },
    { label: 'Equipment Lease', values: equipLease, cagr: cagrCalc(equipLease[0], equipLease[4]), kind: 'detail' },
    { label: 'Net Operating Income', values: noiCalc, cagr: cagrCalc(noiCalc[0], noiCalc[4]), kind: 'subtotal' },

    { label: 'FF&E Reserve', values: years.map(y => ffe[y]), cagr: cagrCalc(ffe.y1, ffe.y5), kind: 'detail' },
    { label: 'Management Fee', values: years.map(y => mgmt[y]), cagr: cagrCalc(mgmt.y1, mgmt.y5), kind: 'detail' },
    { label: 'Net Income', values: netIncome, cagr: cagrCalc(netIncome[0], netIncome[4]), kind: 'total' },
  ];

  return {
    rows,
    totals: {
      totalRev: years.map(y => totalRev[y]),
      noi: noiCalc,
      gop,
      deptProfit,
    },
  };
}

const kimptonStatement = buildStatement();

export default function PLTab({ projectId }: { projectId: number | string }) {
  const [tab, setTab] = useState('Operating Statement');
  const params = useParams();
  const { toast } = useToast();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs, previous } = useEngineOutputs(dealId);
  const { deal } = useDeal(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);
  const isKimptonDemo = projectId === 7;
  // Per-Key / Departmental dividers must use the real deal's room count,
  // not a 100-key default. Sam QA #2: passing 100 for non-Kimpton deals
  // made every per-key figure mathematically wrong on real uploads.
  const propertyKeys = isKimptonDemo
    ? kimptonAnglerOverview.general.keys
    : (deal?.keys && deal.keys > 0 ? deal.keys : 100);

  // Worker → expense engine years[] is the canonical source for the operating
  // statement on a real run. Worker wins; Kimpton mock is the demo fallback.
  const expenseYears = getEngineField<ExpenseYearWorker[]>(outputs, 'expense', 'years');
  const fbYears = getEngineField<FBYearWorker[]>(outputs, 'fb', 'years');
  // Lines whose Y1 was lifted from the deal's T-12 extraction rather
  // than synthesized at USALI ratios. Used to badge those rows on the
  // Operating Statement so reviewers can tell real vs estimated at a
  // glance (Sam QA: "tell me which lines are T-12 vs estimated").
  const sourcedFromT12 = getEngineField<string[]>(outputs, 'expense', 'sourced_from_t12') ?? [];
  // Revenue engine emits occupancy / ADR / RevPAR per year — Sam QA #16:
  // before this, the Per-Key tab hardcoded a Kimpton-style occupancy
  // ramp [70.1, 73.8, …] and back-derived ADR from rooms revenue, which
  // ignored T-12 actuals on real deals. We pass the real series through.
  const revenueYears = getEngineField<RevenueYearWorker[]>(outputs, 'revenue', 'years');
  const hasWorkerStatement = Array.isArray(expenseYears) && expenseYears.length > 0;
  const statement = useMemo<StatementResult | null>(() => {
    if (hasWorkerStatement) {
      return buildStatementFromWorker(expenseYears!, fbYears ?? null);
    }
    if (isKimptonDemo) return kimptonStatement;
    return null;
  }, [hasWorkerStatement, expenseYears, fbYears, isKimptonDemo]);

  if (!isKimptonDemo && !statement) {
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

  // statement is non-null at this point (we returned the placeholder above otherwise),
  // but TS still wants the narrowing.
  const stmt = statement!;
  const { totals } = stmt;
  const y1Rev = totals.totalRev[0] * 1000;
  const y1NOI = totals.noi[0] * 1000;
  const margin = y1Rev ? y1NOI / y1Rev : 0;
  const noiCagr = totals.noi[0] > 0 && totals.noi[4] > 0
    ? Math.pow(totals.noi[4] / totals.noi[0], 1 / 4) - 1
    : 0;

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
        {tab === 'Operating Statement' && <OperatingStatement statement={stmt} sourceLabel={hasWorkerStatement ? 'worker' : 'mock'} sourcedFromT12={sourcedFromT12} />}
        {tab === 'Departmental' && <Departmental statement={stmt} keys={propertyKeys} />}
        {tab === 'Per-Key Metrics' && <PerKey statement={stmt} keys={propertyKeys} isKimptonDemo={isKimptonDemo} revenueYears={revenueYears ?? null} />}
        {tab === 'Historical vs Projected' && <HistoricalProjected statement={stmt} isKimptonDemo={isKimptonDemo} />}
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

// Map worker expense-line keys to the UI row labels rendered on the
// Operating Statement, so we can flag which Y1 lines came from the
// deal's T-12 actuals (vs synthesized at USALI ratios).
const T12_LINE_LABELS: Record<string, string> = {
  rooms_dept_expense: 'Rooms Department',
  fb_dept_expense: 'F&B Department',
  other_dept_expense: 'Other Department',
  administrative_general: 'Administrative & General',
  information_telecom: 'Information & Telecom',
  sales_marketing: 'Sales & Marketing',
  property_operations: 'Property Operations & Maintenance',
  utilities: 'Utilities',
  mgmt_fee: 'Management Fee',
  ffe_reserve: 'FF&E Reserve',
  property_taxes: 'Property Taxes',
  insurance: 'Insurance',
};

function OperatingStatement({
  statement,
  sourceLabel,
  sourcedFromT12,
}: {
  statement: StatementResult;
  sourceLabel: 'worker' | 'mock';
  sourcedFromT12: string[];
}) {
  const { rows } = statement;
  const t12LabelSet = new Set(
    sourcedFromT12
      .map((k) => T12_LINE_LABELS[k])
      .filter((l): l is string => Boolean(l))
  );
  return (
    <Card className="p-5">
      <div className="flex items-baseline justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">USALI Operating Statement</h3>
        <span className="text-[11px] text-ink-500">
          ($ in 000s, FYE Dec 31){sourceLabel === 'worker' ? ' · live engine output' : ''}
        </span>
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
              const isFromT12 = t12LabelSet.has(r.label);
              return (
                <StatementRowR
                  key={r.label}
                  row={r}
                  tint={tint}
                  isSubtotal={isSubtotal}
                  isTotal={isTotal}
                  fromT12={isFromT12}
                />
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="mt-4 pt-4 border-t border-border text-[11px] text-ink-500 space-y-1">
        {t12LabelSet.size > 0 ? (
          <>
            <div>
              • Lines marked <span className="inline-block px-1 py-0.5 rounded bg-success-50 text-success-700 text-[10px] font-semibold tabular-nums align-middle">T-12</span>{' '}
              use Year-1 actuals from the deal&apos;s extracted T-12; remaining lines synthesized at USALI ratios.
            </div>
            <div>• Out-years grow the Year-1 anchor at the configured expense growth rate (default 3.5%).</div>
          </>
        ) : (
          <>
            <div>• Departmental expenses synthesized at USALI ratios: Rooms 25% of room rev, F&B 75% of F&B rev, Other 50%.</div>
            <div>• Undistributed operating expenses follow lifestyle-tier benchmarks (A&G 8%, S&M 7%, POM 4%, Utilities 4.5%, IT 1.5%).</div>
            <div>• Upload a T-12 to ground these lines on actual operating data.</div>
          </>
        )}
      </div>
    </Card>
  );
}

function Departmental({ statement, keys }: { statement: StatementResult; keys: number }) {
  // Pull Y1 values directly from the statement we built (worker or mock).
  const findRow = (label: string) =>
    statement.rows.find(r => r.label === label)?.values ?? [0, 0, 0, 0, 0];
  const room = findRow('Room Revenue');
  const fb = findRow('F&B Revenue');
  const other = findRow('Other Revenue');
  const total = findRow('Total Revenue');
  const roomsDept = findRow('Rooms Department');
  const fbDept = findRow('F&B Department');
  const otherDept = findRow('Other Department');

  // Year-1 (in 000s) — use departmental expenses straight from the statement.
  const depts = [
    { name: 'Rooms', revenue: room[0], expense: roomsDept[0], tone: 'brand' },
    { name: 'Food & Beverage', revenue: fb[0], expense: fbDept[0], tone: 'amber' },
    { name: 'Other Operating', revenue: other[0], expense: otherDept[0], tone: 'success' },
  ].map(d => ({
    ...d,
    profit: d.revenue - d.expense,
    margin: d.revenue > 0 ? (d.revenue - d.expense) / d.revenue : 0,
    profitPerKey: keys > 0 ? ((d.revenue - d.expense) * 1000) / keys : 0,
  }));

  const totalRev = total[0];
  const totalExp = depts.reduce((s, d) => s + d.expense, 0);
  const totalProfit = totalRev - totalExp;
  const totalCard = {
    name: 'Total',
    revenue: totalRev,
    expense: totalExp,
    profit: totalProfit,
    margin: totalRev > 0 ? totalProfit / totalRev : 0,
    profitPerKey: keys > 0 ? (totalProfit * 1000) / keys : 0,
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
            const pct = totalRev > 0 ? (d.revenue / totalRev) * 100 : 0;
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

function PerKey({
  statement,
  keys,
  isKimptonDemo,
  revenueYears,
}: {
  statement: StatementResult;
  keys: number;
  isKimptonDemo: boolean;
  revenueYears: RevenueYearWorker[] | null;
}) {
  const findRow = (label: string) =>
    statement.rows.find(r => r.label === label)?.values ?? [0, 0, 0, 0, 0];
  const room = findRow('Room Revenue');
  const fb = findRow('F&B Revenue');
  const total = findRow('Total Revenue');
  const noi = findRow('Net Operating Income');
  const gopThousands = findRow('Gross Operating Profit (GOP)');

  const availableRoomNights = keys * 365;
  const safeDiv = (a: number, b: number) => (b > 0 ? a / b : 0);
  const padTo5 = <T,>(arr: T[], filler: T): T[] =>
    arr.length >= 5 ? arr.slice(0, 5) : [...arr, ...Array(5 - arr.length).fill(filler)];

  // Prefer the revenue engine's per-year occupancy / ADR / RevPAR (which
  // are now grounded in T-12 actuals via _load_engine_inputs in the
  // worker — Sam QA #16). Only fall back to the hardcoded Kimpton-style
  // ramp when no engine output is available (i.e. the demo card or
  // pre-run state).
  const hasRevenueOutput = Array.isArray(revenueYears) && revenueYears.length > 0;
  const occRamp = [0.701, 0.738, 0.762, 0.776, 0.787];
  const occ = hasRevenueOutput
    ? padTo5(revenueYears!.slice(0, 5).map(y => y.occupancy), 0)
    : occRamp;
  const adrSeries = hasRevenueOutput
    ? padTo5(revenueYears!.slice(0, 5).map(y => y.adr), 0)
    : room.map((v, i) => safeDiv(v * 1000, availableRoomNights * occRamp[i]));
  const revparSeries = hasRevenueOutput
    ? padTo5(revenueYears!.slice(0, 5).map(y => y.revpar), 0)
    : room.map(v => safeDiv(v * 1000, availableRoomNights));

  const rows = [
    { label: 'Total Revenue / Key', vals: total.map(v => safeDiv(v * 1000, keys)), fmt: 'k' as const },
    { label: 'Rooms Revenue / Key', vals: room.map(v => safeDiv(v * 1000, keys)), fmt: 'k' as const },
    { label: 'F&B Revenue / Key', vals: fb.map(v => safeDiv(v * 1000, keys)), fmt: 'k' as const },
    { label: 'GOP / Key', vals: gopThousands.map(v => safeDiv(v * 1000, keys)), fmt: 'k' as const, bold: true },
    { label: 'NOI / Key', vals: noi.map(v => safeDiv(v * 1000, keys)), fmt: 'k' as const, bold: true },
    { label: 'RevPAR', vals: revparSeries, fmt: 'd' as const },
    { label: 'ADR', vals: adrSeries, fmt: 'd' as const },
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
        <span className="text-[11px] text-ink-500">{keys} keys{isKimptonDemo ? ' · 132-room lifestyle boutique' : ''}</span>
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

function HistoricalProjected({ statement, isKimptonDemo }: { statement: StatementResult; isKimptonDemo: boolean }) {
  // Historical: 3 years of pre-acquisition operating performance (in $000s).
  // Real T-12 historical data isn't available outside the Kimpton demo, so
  // the historical block is omitted for non-Kimpton deals — projection-only view.
  const findRow = (label: string) =>
    statement.rows.find(r => r.label === label)?.values ?? [0, 0, 0, 0, 0];
  const totalRev = findRow('Total Revenue');
  const noi = findRow('Net Operating Income');
  const gopRow = findRow('Gross Operating Profit (GOP)');

  const projected = [2026, 2027, 2028, 2029, 2030].map((year, i) => ({
    year: String(year),
    revenue: totalRev[i] ?? 0,
    noi: noi[i] ?? 0,
    gop: gopRow[i] ?? 0,
    kind: 'projected' as const,
  }));
  const historical = isKimptonDemo
    ? [
        { year: '2023', revenue: 13_240, noi: 2_120, gop: 4_520, kind: 'historical' as const },
        { year: '2024', revenue: 13_680, noi: 2_280, gop: 4_780, kind: 'historical' as const },
        { year: '2025', revenue: 13_950, noi: 2_481, gop: 4_950, kind: 'historical' as const },
      ]
    : [];
  const data = [...historical, ...projected];

  return (
    <>
      <Card className="p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-[13px] font-semibold text-ink-900">Historical vs Projected Performance</h3>
            <div className="text-[11px] text-ink-500 mt-0.5">
              {historical.length > 0 ? (
                <>Pre-acquisition T-3 (2023-2025) and underwritten projection (2026-2030) · $ in 000s</>
              ) : (
                <>Underwritten projection (2026-2030) · $ in 000s · upload prior-period operating statements to see the historical baseline</>
              )}
            </div>
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

// Operating-statement row with value-flash on the year-1 column.
// Flashing the whole row on every refresh would be noisy; tying it to
// the leading number is enough to signal "this just changed".
function StatementRowR({
  row, tint, isSubtotal, isTotal, fromT12,
}: {
  row: StatementRow; tint: string; isSubtotal: boolean; isTotal: boolean; fromT12?: boolean;
}) {
  const flash = useFlash(row.values[0] ?? 0);
  return (
    <tr className={cn(
      'border-b border-border/40',
      tint,
      isSubtotal && 'font-semibold bg-brand-50/40 border-t border-border',
      isTotal && 'font-semibold bg-success-50/40 border-t-2 border-border text-success-700',
      flash && 'value-flash',
    )}>
      <td className={cn('py-1.5', !isSubtotal && !isTotal && 'pl-3')}>
        <span className="inline-flex items-center gap-1.5">
          {row.label}
          {fromT12 && (
            <span
              className="inline-block px-1 py-0.5 rounded bg-success-50 text-success-700 text-[10px] font-semibold tabular-nums leading-none"
              title="Year-1 anchored on the deal's T-12 actual; out-years grown at expense_growth"
            >
              T-12
            </span>
          )}
        </span>
      </td>
      {row.values.map((v, vi) => (
        <td key={vi} className="text-right tabular-nums">{v.toLocaleString()}</td>
      ))}
      <td className="text-right tabular-nums">
        {row.cagr !== undefined ? `${(row.cagr * 100).toFixed(1)}%` : '—'}
      </td>
    </tr>
  );
}
