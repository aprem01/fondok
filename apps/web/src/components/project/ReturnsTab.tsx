'use client';
import { useMemo, useState } from 'react';
import { useParams } from 'next/navigation';
import { TrendingDown, Minus, TrendingUp } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import { dealScenarios, kimptonAnglerOverview } from '@/lib/mockData';
import { fmtPct, cn } from '@/lib/format';
import { useAssumptionsOptional } from '@/stores/assumptionsStore';
import { defaultSensitivities, SensitivityMatrix } from '@/lib/engines';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';
import { GLOSSARY } from '@/lib/glossary';

const subTabs = ['Returns Summary', 'Sensitivities'];

export default function ReturnsTab({ projectId }: { projectId: number | string }) {
  const [tab, setTab] = useState('Returns Summary');
  const ctx = useAssumptionsOptional();
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { toast } = useToast();
  const isKimptonDemo = projectId === 7;
  const { outputs, previous } = useEngineOutputs(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  if (!isKimptonDemo) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          <IntroCard
            dismissKey="returns-intro"
            title="The Returns Engine"
            body={
              <>
                The headline numbers — IRR, equity multiple, cash-on-cash — and how sensitive
                they are to your assumptions. This is what investors actually earn over the
                hold period after debt service.
              </>
            }
          />
          <EngineHeader
            name="Returns Engine"
            desc="Computes IRR, equity multiple, and scenario sensitivities for investment analysis."
            outputs={['Levered IRR', 'Unlevered IRR', 'Equity Multiple', '+1']}
            dependsOn="Cash Flow"
            dealId={dealId}
            engineName="returns"
            onRunComplete={() => {
              setComputing(false);
              setRunToken(Date.now());
            }}
          />
          <EngineLegend />
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <TrendingUp size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">Returns Engine unavailable</h3>
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              IRR, equity multiple, and sensitivity analysis depend on the
              <span className="font-medium"> Cash Flow</span> engine. Run that first, or upload an OM
              and T-12 if you haven&apos;t.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => toast('Engine queued — check back shortly', { type: 'info' })}
            >
              Run Returns Engine
            </Button>
          </Card>
          <EngineRunHistory dealId={dealId} />
        </div>
        <EngineRightRail />
      </div>
    );
  }

  // If we're inside the AssumptionsProvider (Kimpton deal), use live model.
  // Otherwise fall back to static mock data.
  return (
    <div className="flex gap-4">
      <div className="flex-1 min-w-0">
      <IntroCard
        dismissKey="returns-intro"
        title="The Returns Engine"
        body={
          <>
            The headline numbers — IRR, equity multiple, cash-on-cash — and how sensitive
            they are to your assumptions. This is what investors actually earn over the
            hold period after debt service.
          </>
        }
      />
      <EngineHeader
        name="Returns Engine"
        desc="Computes IRR, equity multiple, and scenario sensitivities for investment analysis."
        outputs={['Levered IRR', 'Unlevered IRR', 'Equity Multiple', '+1']}
        dependsOn="Cash Flow"
        complete
        dealId={dealId}
        engineName="returns"
        runMode="all"
        onRunStart={() => setComputing(true)}
        onRunComplete={() => {
          setComputing(false);
          setRunToken(Date.now());
        }}
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

      <WhatJustHappened
        engine="returns"
        engineLabel="Returns"
        outputs={outputs}
        previous={previous}
        runToken={runToken}
      />

      <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
        {tab === 'Returns Summary' && (
          ctx ? <LiveReturnsSummary outputs={outputs} /> : <StaticReturnsSummary outputs={outputs} />
        )}
        {tab === 'Sensitivities' && (ctx ? <LiveSensitivities /> : <StaticSensitivities />)}
        {computing && (
          <div className="absolute inset-0 bg-bg/60 backdrop-blur-[1px] flex items-center justify-center text-[12.5px] font-medium text-ink-700 rounded-md">
            <span className="inline-flex items-center gap-2 px-3 py-1.5 bg-white border border-border rounded-md shadow-card">
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

// ───────────────────────────────────────────────────────────────────
// Live (Kimpton) version — wired to the assumptions store + engine.
// ───────────────────────────────────────────────────────────────────

function LiveReturnsSummary({ outputs }: { outputs: ReturnType<typeof useEngineOutputs>['outputs'] }) {
  const { assumptions, setAssumption, model } = useAssumptionsOptional()!;
  // Worker overrides — fall back to live in-page model when worker has no data.
  const wIrr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const wMult = getEngineField<number>(outputs, 'returns', 'equity_multiple');
  const wCoC = getEngineField<number>(outputs, 'returns', 'cash_on_cash_year_one');
  const irr = wIrr ?? model.leveredIrr;
  const mult = wMult ?? model.equityMultiple;
  const coc = wCoC ?? model.cashOnCash;

  return (
    <>
      <div className="grid grid-cols-4 gap-4 mb-5">
        <KPI label="Levered IRR" tip={GLOSSARY['IRR']} value={fmtPct(irr, 2)} flashKey={irr} />
        <KPI label="Equity Multiple" tip={GLOSSARY['Equity Multiple']} value={`${mult.toFixed(2)}x`} flashKey={mult} />
        <KPI label="Cash-on-Cash" tip={GLOSSARY['CoC']} value={fmtPct(coc, 2)} flashKey={coc} />
        <KPI label="Hold Period" tip={GLOSSARY['Hold Period']} value={`${assumptions.holdYears} Years`} flashKey={assumptions.holdYears} />
      </div>

      <Card className="p-5 mb-5">
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="text-[14px] font-semibold text-ink-900">Live Assumptions</h3>
          <span className="text-[11px] text-ink-500">Drag a slider — IRR, multiple and exit value recompute instantly.</span>
        </div>
        <div className="grid grid-cols-2 gap-x-8 gap-y-3">
          <Slider
            label="Exit Cap Rate"
            min={0.05} max={0.09} step={0.001}
            value={assumptions.exitCapRate}
            onChange={v => setAssumption('exitCapRate', v)}
            format={v => fmtPct(v, 2)}
          />
          <Slider
            label="RevPAR Growth"
            min={0} max={0.06} step={0.0025}
            value={assumptions.revparGrowth}
            onChange={v => setAssumption('revparGrowth', v)}
            format={v => fmtPct(v, 2)}
          />
          <Slider
            label="Hold Period"
            min={3} max={10} step={1}
            value={assumptions.holdYears}
            onChange={v => setAssumption('holdYears', Math.round(v))}
            format={v => `${Math.round(v)} years`}
          />
          <Slider
            label="LTV"
            min={0.50} max={0.75} step={0.01}
            value={assumptions.ltv}
            onChange={v => setAssumption('ltv', v)}
            format={v => fmtPct(v, 0)}
          />
          <Slider
            label="Interest Rate"
            min={0.045} max={0.085} step={0.00125}
            value={assumptions.interestRate}
            onChange={v => setAssumption('interestRate', v)}
            format={v => fmtPct(v, 3)}
          />
          <div className="text-[11.5px] text-ink-500 self-end pb-1">
            Exit Value: <span className="font-medium text-ink-900 tabular-nums">${(model.exitValue / 1e6).toFixed(2)}M</span>
            <span className="mx-2">·</span>
            DSCR Y1: <span className="font-medium text-ink-900 tabular-nums">{model.dscrY1.toFixed(2)}x</span>
          </div>
        </div>
      </Card>

      <Card className="p-5">
        <h3 className="text-[14px] font-semibold text-ink-900 mb-4">Scenario Analysis</h3>
        <div className="grid grid-cols-3 gap-4">
          {[
            { name: 'Downside', sc: model.scenarios.downside },
            { name: 'Base Case', sc: model.scenarios.base, base: true },
            { name: 'Upside', sc: model.scenarios.upside },
          ].map(({ name, sc, base }) => {
            const Icon = name === 'Downside' ? TrendingDown : name === 'Base Case' ? Minus : TrendingUp;
            const tone = name === 'Downside' ? 'text-danger-700' : name === 'Base Case' ? 'text-ink-700' : 'text-success-700';
            return (
              <div key={name} className={cn(
                'p-4 rounded-lg border-2',
                base ? 'border-brand-500 bg-brand-50' : 'border-border'
              )}>
                <div className="flex items-center gap-2 mb-3">
                  <Icon size={16} className={tone} />
                  <div className="text-[13px] font-semibold text-ink-900">{name}</div>
                </div>
                <div className="space-y-2 text-[12.5px]">
                  <Row k="Levered IRR" v={fmtPct(sc.irr, 2)} />
                  <Row k="Unlevered IRR" v={fmtPct(sc.unleveredIrr ?? 0, 2)} />
                  <Row k="Multiple" v={`${sc.multiple.toFixed(2)}x`} />
                  <Row k="Y1 CoC" v={fmtPct(sc.coc, 1)} />
                  <Row k="Exit Value" v={`$${((sc.exitValue ?? 0) / 1e6).toFixed(1)}M`} />
                </div>
              </div>
            );
          })}
        </div>
      </Card>
    </>
  );
}

function LiveSensitivities() {
  const { assumptions } = useAssumptionsOptional()!;
  // Sensitivity matrices recompute on assumption change. 5x5x3 = 75 model runs;
  // each run is fast so the user perceives no lag.
  const matrices = useMemo(() => defaultSensitivities(assumptions), [assumptions]);
  return (
    <div className="grid grid-cols-3 gap-4">
      {matrices.map((m, i) => (
        <SensitivityCard key={i} matrix={m} title={['Levered IRR', 'Equity Multiple (MOIC)', 'Year-1 Cash-on-Cash'][i]} />
      ))}
    </div>
  );
}

function SensitivityCard({ matrix, title }: { matrix: SensitivityMatrix; title: string }) {
  const flat = matrix.cells.flat().map(c => c.value);
  const min = Math.min(...flat);
  const max = Math.max(...flat);
  const colorFor = (v: number) => {
    const t = max === min ? 0.5 : (v - min) / (max - min);
    if (t > 0.66) return 'bg-success-50 text-success-700';
    if (t > 0.33) return 'bg-warn-50 text-warn-700';
    return 'bg-danger-50 text-danger-700';
  };
  const formatHeader = (v: number, key: string) =>
    key === 'Hold' ? `${v}y` : `${(v * 100).toFixed(1)}%`;
  const formatCell = (v: number) =>
    matrix.unit === 'multiple' ? `${v.toFixed(2)}x` : `${(v * 100).toFixed(1)}%`;

  return (
    <Card className="p-4">
      <h3 className="text-[12.5px] font-semibold text-ink-900 mb-2">{title}</h3>
      <div className="text-[10.5px] text-ink-500 mb-3">
        {matrix.rowLabel} ↓ × {matrix.colLabel} →
      </div>
      <table className="w-full text-[10.5px]">
        <thead>
          <tr>
            <th></th>
            {matrix.cols.map((c, j) => (
              <th key={j} className="font-medium text-ink-500 pb-1 px-1">
                {formatHeader(c, matrix.colLabel)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.cells.map((row, ri) => (
            <tr key={ri}>
              <td className="font-medium text-ink-500 pr-1 tabular-nums">
                {formatHeader(matrix.rows[ri], matrix.rowLabel)}
              </td>
              {row.map((cell, ci) => (
                <td key={ci} className="p-0.5">
                  <div className={cn(
                    'rounded px-1 py-1.5 text-center font-medium tabular-nums',
                    colorFor(cell.value),
                    cell.isBase && 'ring-2 ring-brand-500',
                  )}>
                    {formatCell(cell.value)}
                  </div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

// ───────────────────────────────────────────────────────────────────
// Static fallback for non-Kimpton deals (preserves original look).
// ───────────────────────────────────────────────────────────────────

function StaticReturnsSummary({ outputs }: { outputs: ReturnType<typeof useEngineOutputs>['outputs'] }) {
  const o = kimptonAnglerOverview;
  const wIrr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const wMult = getEngineField<number>(outputs, 'returns', 'equity_multiple');
  const wCoC = getEngineField<number>(outputs, 'returns', 'cash_on_cash_year_one');
  const irr = wIrr ?? o.returns.leveredIRR;
  const mult = wMult ?? o.returns.equityMultiple;
  const cocLabel = wCoC != null ? fmtPct(wCoC, 2) : '4.6%';
  return (
    <>
      <div className="grid grid-cols-4 gap-4 mb-5">
        <KPI label="Levered IRR" tip={GLOSSARY['IRR']} value={fmtPct(irr, 2)} flashKey={irr} />
        <KPI label="Equity Multiple" tip={GLOSSARY['Equity Multiple']} value={`${mult.toFixed(2)}x`} flashKey={mult} />
        <KPI label="Cash-on-Cash" tip={GLOSSARY['CoC']} value={cocLabel} flashKey={cocLabel} />
        <KPI label="Hold Period" tip={GLOSSARY['Hold Period']} value={`${o.returns.hold} Years`} flashKey={o.returns.hold} />
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
  );
}

function StaticSensitivities() {
  return (
    <div className="grid grid-cols-3 gap-4">
      <StaticHeatmap title="Levered IRR" rowLabel="Exit Cap" colLabel="RevPAR Growth"
        rows={['6.0%', '6.5%', '7.0%', '7.5%', '8.0%']}
        cols={['2.0%', '2.5%', '3.0%', '3.5%', '4.0%']}
        data={[
          [29.4, 31.2, 33.0, 34.7, 36.5],
          [25.6, 27.4, 29.2, 31.0, 32.8],
          [21.9, 23.5, 23.48, 27.4, 29.2],
          [18.4, 20.0, 21.7, 23.4, 25.1],
          [15.0, 16.6, 18.3, 20.0, 21.7],
        ]} baseRow={2} baseCol={2} unit="%" />
      <StaticHeatmap title="Equity Multiple (MOIC)" rowLabel="LTV" colLabel="Hold"
        rows={['55%', '60%', '65%', '70%', '75%']}
        cols={['3y', '4y', '5y', '6y', '7y']}
        data={[
          [1.62, 1.81, 1.99, 2.16, 2.32],
          [1.68, 1.88, 2.06, 2.24, 2.41],
          [1.74, 1.94, 2.12, 2.31, 2.49],
          [1.80, 2.00, 2.18, 2.38, 2.56],
          [1.86, 2.06, 2.24, 2.45, 2.63],
        ]} baseRow={2} baseCol={2} unit="x" />
      <StaticHeatmap title="Year-1 Cash-on-Cash" rowLabel="Cap Rate" colLabel="Hold"
        rows={['6.0%', '6.5%', '7.0%', '7.5%', '8.0%']}
        cols={['3y', '4y', '5y', '6y', '7y']}
        data={[
          [3.4, 3.9, 4.4, 4.9, 5.4],
          [3.6, 4.1, 4.6, 5.1, 5.6],
          [3.8, 4.3, 4.8, 5.3, 5.8],
          [4.0, 4.5, 5.0, 5.5, 6.0],
          [4.2, 4.7, 5.2, 5.7, 6.2],
        ]} baseRow={2} baseCol={2} unit="%" />
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Shared bits
// ───────────────────────────────────────────────────────────────────

function KPI({ label, value, flashKey, tip }: { label: string; value: string; flashKey?: unknown; tip?: string }) {
  const flash = useFlash(flashKey ?? value);
  return (
    <Card className={cn('p-4', flash && 'value-flash')}>
      <div className="text-[10.5px] text-ink-500 uppercase tracking-wide">
        {tip ? <MetricLabel label={label} tip={tip} /> : label}
      </div>
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

function Slider({
  label, value, min, max, step, onChange, format,
}: {
  label: string; value: number; min: number; max: number; step: number;
  onChange: (v: number) => void; format: (v: number) => string;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label className="text-[11.5px] text-ink-500 uppercase tracking-wide">{label}</label>
        <span className="text-[12.5px] font-semibold text-brand-700 tabular-nums">{format(value)}</span>
      </div>
      <input
        type="range"
        min={min} max={max} step={step}
        value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className="w-full accent-brand-500"
      />
    </div>
  );
}

function StaticHeatmap({
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
