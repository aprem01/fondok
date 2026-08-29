'use client';
import { useMemo, useState, type ReactNode } from 'react';
import { useParams } from 'next/navigation';
import { TrendingUp } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import PricingSensitivityPanel from './PricingSensitivityPanel';
import MaxPricePanel from './MaxPricePanel';
import { fmtPct, cn } from '@/lib/format';
import { useAssumptionsOptional } from '@/stores/assumptionsStore';
import { defaultSensitivities, SensitivityMatrix } from '@/lib/engines';
import type { SensitivityCell } from '@/lib/engines/types';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';
import { CoachMark } from '@/components/help/CoachMark';
import { Traced } from '@/components/help/Traced';
import { GLOSSARY } from '@/lib/glossary';

// FON-68 — MVP Returns is three sub-tabs. Scenario management lives on
// Scenario Analysis; comps live on Market → Transaction Comps.
const subTabs = ['Returns Summary', 'Sensitivities', 'Pricing'];

export default function ReturnsTab() {
  const [tab, setTab] = useState('Returns Summary');
  const ctx = useAssumptionsOptional();
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { toast } = useToast();
  const { outputs, previous } = useEngineOutputs(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  // Has the Returns engine been run for this deal? Used to decide whether to
  // render the placeholder or the live UI.
  const wReturnsIrr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const hasWorkerReturns = wReturnsIrr != null;

  if (!hasWorkerReturns) {
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
          // AssumptionsProvider is now mounted universally on every
          // deal page, so ``ctx`` is always non-null in production.
          // The Static* fallbacks below leak Kimpton's hardcoded
          // dealScenarios (Base 23.01%, etc.) onto unrelated deals
          // when ctx briefly is null on first paint — Sam re-test
          // saw a 36.92% headline next to a 23.01% Base Case via
          // this exact path. We render Live unconditionally so the
          // scenarios card always reconciles with the headline.
          <LiveReturnsSummary outputs={outputs} />
        )}
        {tab === 'Sensitivities' && ctx && <LiveSensitivities />}
        {tab === 'Pricing' && (
          <div className="flex flex-col gap-4">
            <PricingSensitivityPanel dealId={dealId} />
            <MaxPricePanel dealId={dealId} />
          </div>
        )}
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
  // The headline KPIs and the Base Case scenario card MUST resolve to the same
  // numbers; otherwise an analyst sees "Levered IRR 18.07%" up top and a Base
  // Case card showing "23.01%" on the same page (Sam QA #5). We compute the
  // canonical triple here and reuse it both places.
  const wIrr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const wMult = getEngineField<number>(outputs, 'returns', 'equity_multiple');
  const wCoC = getEngineField<number>(outputs, 'returns', 'cash_on_cash_year_one');
  const irr = wIrr ?? model.leveredIrr;
  const mult = wMult ?? model.equityMultiple;
  const coc = wCoC ?? model.cashOnCash;

  return (
    <>
      <div className="grid grid-cols-4 gap-4 mb-5">
        <CoachMark
          anchorId="returns-levered-irr"
          viewKey="returns"
          order={0}
          title="Why this number leads"
          body="Levered IRR is the most institutionally cited return — it captures what your equity actually earns after debt service. Fondok solves it with Newton's method and a bisection fallback for numerical stability, same approach Argus uses."
          side="top"
          learnMoreHref="/methodology#engines"
        >
          <KPI label="Levered IRR" tip={GLOSSARY['IRR']} flashKey={irr}
            value={<Traced engine="returns" path="levered_irr">{fmtPct(irr, 2)}</Traced>} />
        </CoachMark>
        <KPI label="Equity Multiple" tip={GLOSSARY['Equity Multiple']} flashKey={mult}
          value={<Traced engine="returns" path="equity_multiple">{`${mult.toFixed(2)}x`}</Traced>} />
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
            min={0.04} max={0.12} step={0.001}
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
            min={0.40} max={0.80} step={0.01}
            value={assumptions.ltv}
            onChange={v => setAssumption('ltv', v)}
            format={v => fmtPct(v, 0)}
          />
          <Slider
            label="Interest Rate"
            min={0.04} max={0.10} step={0.00125}
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
    </>
  );
}

function LiveSensitivities() {
  const { assumptions } = useAssumptionsOptional()!;
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs } = useEngineOutputs(dealId);
  // Sensitivity matrices recompute on assumption change. 5x5x3 = 75 model runs;
  // each run is fast so the user perceives no lag.
  const tsMatrices = useMemo(() => defaultSensitivities(assumptions), [assumptions]);

  // Worker sensitivity output: a single matrix (the first one in our suite).
  // When present, we prefer it and merge it as the first matrix in the trio.
  const workerMatrix = useMemo(() => {
    return matrixFromWorker(outputs);
  }, [outputs]);

  const matrices = workerMatrix
    ? [workerMatrix, ...tsMatrices.slice(1)]
    : tsMatrices;
  const titles = ['Levered IRR', 'Equity Multiple (MOIC)', 'Year-1 Cash-on-Cash'];

  return (
    <div className="grid grid-cols-3 gap-4">
      {matrices.map((m, i) => (
        <SensitivityCard
          key={i}
          matrix={m}
          title={titles[i]}
          source={i === 0 && workerMatrix ? 'worker' : 'ts'}
        />
      ))}
    </div>
  );
}

// Map a worker sensitivity engine output into the SensitivityMatrix shape the
// existing card uses. Returns null when the engine hasn't run yet.
function matrixFromWorker(
  outputs: ReturnType<typeof useEngineOutputs>['outputs'],
): SensitivityMatrix | null {
  const out = getEngineField<{
    row_variable: string;
    col_variable: string;
    metric: string;
    rows: number[];
    cols: number[];
    cells: { row_value: number; col_value: number; value: number; is_base: boolean }[];
  }>(outputs, 'sensitivity');
  if (!out || !Array.isArray(out.rows) || !Array.isArray(out.cols)) return null;
  if (!Array.isArray(out.cells) || out.cells.length === 0) return null;

  // Worker emits a flat cell list — re-shape to a 2D grid keyed by (row, col).
  const grid: SensitivityCell[][] = [];
  let baseRow = 0, baseCol = 0;
  for (let i = 0; i < out.rows.length; i++) {
    const row: SensitivityCell[] = [];
    for (let j = 0; j < out.cols.length; j++) {
      const found = out.cells.find(
        c => Math.abs(c.row_value - out.rows[i]) < 1e-9 && Math.abs(c.col_value - out.cols[j]) < 1e-9,
      );
      const cell: SensitivityCell = {
        value: found?.value ?? 0,
        rowVal: out.rows[i],
        colVal: out.cols[j],
        isBase: !!found?.is_base,
      };
      if (cell.isBase) { baseRow = i; baseCol = j; }
      row.push(cell);
    }
    grid.push(row);
  }

  // Pretty labels for axes — fall back to the raw key when unknown.
  const labelFor = (key: string) => ({
    exit_cap_rate: 'Exit Cap',
    revpar_growth: 'RevPAR Growth',
    ltv: 'LTV',
    interest_rate: 'Interest Rate',
    hold_years: 'Hold',
    purchase_price: 'Purchase Price',
  } as Record<string, string>)[key] ?? key;

  return {
    rowLabel: labelFor(out.row_variable),
    colLabel: labelFor(out.col_variable),
    rows: out.rows,
    cols: out.cols,
    cells: grid,
    unit: out.metric === 'equity_multiple' ? 'multiple' : 'pct',
    baseRow,
    baseCol,
  };
}

function SensitivityCard({ matrix, title, source = 'ts' }: { matrix: SensitivityMatrix; title: string; source?: 'worker' | 'ts' }) {
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
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[12.5px] font-semibold text-ink-900">{title}</h3>
        {source === 'worker' && (
          <span className="text-[9.5px] uppercase tracking-wide text-success-700 bg-success-50 rounded px-1.5 py-0.5">
            Live
          </span>
        )}
      </div>
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
// Shared bits
// ───────────────────────────────────────────────────────────────────

function KPI({ label, value, flashKey, tip }: { label: string; value: ReactNode; flashKey?: unknown; tip?: string }) {
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

