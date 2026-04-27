'use client';
import { useState } from 'react';
import { TrendingDown, Minus, TrendingUp } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import EngineHeader from './EngineHeader';
import { dealScenarios, kimptonAnglerOverview } from '@/lib/mockData';
import { fmtPct, cn } from '@/lib/format';

const subTabs = ['Returns Summary', 'Sensitivities'];

export default function ReturnsTab() {
  const [tab, setTab] = useState('Returns Summary');
  const o = kimptonAnglerOverview;

  return (
    <div>
      <EngineHeader
        name="Returns Engine"
        desc="Computes IRR, equity multiple, and scenario sensitivities for investment analysis."
        outputs={['Levered IRR', 'Unlevered IRR', 'Equity Multiple', '+1']}
        dependsOn="Cash Flow"
        complete
      />

      <div className="flex items-center gap-1 mb-5 border-b border-border">
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

      {tab === 'Returns Summary' && (
        <>
          <div className="grid grid-cols-4 gap-4 mb-5">
            <KPI label="Levered IRR" value={fmtPct(o.returns.leveredIRR, 2)} />
            <KPI label="Equity Multiple" value={`${o.returns.equityMultiple.toFixed(2)}x`} />
            <KPI label="Cash-on-Cash" value="4.6%" />
            <KPI label="Hold Period" value={`${o.returns.hold} Years`} />
          </div>

          <Card className="p-5">
            <h3 className="text-[14px] font-semibold text-ink-900 mb-4">Scenario Analysis</h3>
            <div className="grid grid-cols-3 gap-4">
              {dealScenarios.map(s => {
                const Icon = s.name === 'Downside' ? TrendingDown : s.name.includes('Base') ? Minus : TrendingUp;
                const tone = s.name === 'Downside' ? 'text-danger-700' : s.name.includes('Base') ? 'text-ink-700' : 'text-success-700';
                return (
                  <div key={s.name} className={cn(
                    'p-4 rounded-lg border-2',
                    s.base ? 'border-brand-500 bg-brand-50' : 'border-border'
                  )}>
                    <div className="flex items-center gap-2 mb-3">
                      <Icon size={16} className={tone} />
                      <div className="text-[13px] font-semibold text-ink-900">{s.name}</div>
                    </div>
                    <div className="space-y-2 text-[12.5px]">
                      <Row k="Levered IRR" v={`${s.irr.toFixed(2)}%`} />
                      <Row k="Unlevered IRR" v={`${s.unleveredIrr.toFixed(2)}%`} />
                      <Row k="Multiple" v={`${s.multiple.toFixed(2)}x`} />
                      <Row k="Avg CoC" v={`${s.avgCoC.toFixed(1)}%`} />
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>
        </>
      )}

      {tab === 'Sensitivities' && (
        <div className="grid grid-cols-3 gap-4">
          <Heatmap title="Levered IRR" rowLabel="Exit Cap" colLabel="RevPAR Growth"
            rows={['6.0%', '6.5%', '7.0%', '7.5%', '8.0%']}
            cols={['2.0%', '2.5%', '3.0%', '3.5%', '4.0%']}
            data={[
              [29.4, 31.2, 33.0, 34.7, 36.5],
              [25.6, 27.4, 29.2, 31.0, 32.8],
              [21.9, 23.5, 23.48, 27.4, 29.2],
              [18.4, 20.0, 21.7, 23.4, 25.1],
              [15.0, 16.6, 18.3, 20.0, 21.7],
            ]}
            baseRow={2} baseCol={2} unit="%" />
          <Heatmap title="Equity Multiple (MOIC)" rowLabel="LTV" colLabel="Hold"
            rows={['55%', '60%', '65%', '70%', '75%']}
            cols={['3y', '4y', '5y', '6y', '7y']}
            data={[
              [1.62, 1.81, 1.99, 2.16, 2.32],
              [1.68, 1.88, 2.06, 2.24, 2.41],
              [1.74, 1.94, 2.12, 2.31, 2.49],
              [1.80, 2.00, 2.18, 2.38, 2.56],
              [1.86, 2.06, 2.24, 2.45, 2.63],
            ]}
            baseRow={2} baseCol={2} unit="x" />
          <Heatmap title="Year-1 Cash-on-Cash" rowLabel="Cap Rate" colLabel="Hold"
            rows={['6.0%', '6.5%', '7.0%', '7.5%', '8.0%']}
            cols={['3y', '4y', '5y', '6y', '7y']}
            data={[
              [3.4, 3.9, 4.4, 4.9, 5.4],
              [3.6, 4.1, 4.6, 5.1, 5.6],
              [3.8, 4.3, 4.8, 5.3, 5.8],
              [4.0, 4.5, 5.0, 5.5, 6.0],
              [4.2, 4.7, 5.2, 5.7, 6.2],
            ]}
            baseRow={2} baseCol={2} unit="%" />
        </div>
      )}
    </div>
  );
}

function KPI({ label, value }: { label: string; value: string }) {
  return (
    <Card className="p-4">
      <div className="text-[10.5px] text-ink-500 uppercase tracking-wide">{label}</div>
      <div className="text-[22px] font-semibold tabular-nums mt-1 text-brand-700">{value}</div>
    </Card>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between border-b border-border/30 py-1 last:border-0">
      <span className="text-ink-500">{k}</span>
      <span className="font-medium tabular-nums">{v}</span>
    </div>
  );
}

function Heatmap({
  title, rowLabel, colLabel, rows, cols, data, baseRow, baseCol, unit,
}: {
  title: string; rowLabel: string; colLabel: string;
  rows: string[]; cols: string[]; data: number[][];
  baseRow: number; baseCol: number; unit: string;
}) {
  const flat = data.flat();
  const min = Math.min(...flat); const max = Math.max(...flat);
  const colorFor = (v: number) => {
    const t = (v - min) / (max - min);
    // green to amber to red
    if (t > 0.66) return 'bg-success-50 text-success-700';
    if (t > 0.33) return 'bg-warn-50 text-warn-700';
    return 'bg-danger-50 text-danger-700';
  };

  return (
    <Card className="p-4">
      <h3 className="text-[12.5px] font-semibold text-ink-900 mb-2">{title}</h3>
      <div className="text-[10.5px] text-ink-500 mb-3">{rowLabel} ↓ × {colLabel} →</div>
      <table className="w-full text-[10.5px]">
        <thead>
          <tr>
            <th></th>
            {cols.map(c => <th key={c} className="font-medium text-ink-500 pb-1 px-1">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {data.map((row, ri) => (
            <tr key={ri}>
              <td className="font-medium text-ink-500 pr-1 tabular-nums">{rows[ri]}</td>
              {row.map((v, ci) => {
                const isBase = ri === baseRow && ci === baseCol;
                return (
                  <td key={ci} className="p-0.5">
                    <div className={cn(
                      'rounded px-1 py-1.5 text-center font-medium tabular-nums',
                      colorFor(v),
                      isBase && 'ring-2 ring-brand-500'
                    )}>
                      {v.toFixed(unit === 'x' ? 2 : 1)}{unit}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
