'use client';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useParams } from 'next/navigation';
import { TrendingUp } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import PricingSensitivityPanel from './PricingSensitivityPanel';
import MaxPricePanel from './MaxPricePanel';
import { fmtPct, cn } from '@/lib/format';
import { api, isWorkerConnected, type ReturnsPreviewResponse } from '@/lib/api';
// Sensitivity grid shapes (relocated here when the client-side lib/engines model
// was retired — the worker sensitivity engine is now the only source).
interface SensitivityCell {
  value: number;
  rowVal: number;
  colVal: number;
  isBase: boolean;
}
interface SensitivityMatrix {
  rowLabel: string;
  colLabel: string;
  rows: number[];
  cols: number[];
  cells: SensitivityCell[][];
  unit: 'pct' | 'multiple';
  baseRow: number;
  baseCol: number;
}
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

  // Output-only tab: every sub-view reads the canonical worker engine
  // outputs. The Returns tab no longer consumes the page assumptions
  // provider — its Live Assumptions sliders are a local, ephemeral sandbox
  // (FON-68 step 3), so nothing here can mutate Investment/Debt's model.
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

      <WhatJustHappened
        engine="returns"
        engineLabel="Returns"
        outputs={outputs}
        previous={previous}
        runToken={runToken}
      />

      <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
        {tab === 'Returns Summary' && <LiveReturnsSummary outputs={outputs} />}
        {tab === 'Sensitivities' && <LiveSensitivities />}
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
// Live Returns Summary — headline reads WORKER outputs only; the Live
// Assumptions card is an EPHEMERAL, LOCAL sensitivity sandbox backed by
// the non-persisting /engines/returns/preview endpoint (FON-68 step 3).
// Dragging a slider NEVER mutates the shared assumptions store, so the
// canonical case in Investment / Debt is untouched.
// ───────────────────────────────────────────────────────────────────

type SandboxKey = 'exitCapRate' | 'revparGrowth' | 'holdYears' | 'ltv' | 'interestRate';
type SandboxValues = Record<SandboxKey, number>;

// Read the canonical slider base case straight off the returns engine's
// persisted ``inputs.assumptions`` blob — the SAME canonical run the headline
// reads — so the sandbox starts on the deal's real numbers with no dependency
// on the page assumptions provider.
function readSandboxBase(
  outputs: ReturnType<typeof useEngineOutputs>['outputs'],
): SandboxValues {
  const rIn = ((outputs?.engines?.returns?.inputs as Record<string, unknown> | undefined)
    ?.assumptions ?? {}) as Record<string, unknown>;
  const n = (v: unknown, fallback: number) =>
    typeof v === 'number' && Number.isFinite(v) ? v : fallback;
  return {
    exitCapRate: n(rIn.exit_cap_rate, 0.07),
    revparGrowth: n(rIn.revpar_growth, 0.03),
    holdYears: Math.round(n(rIn.hold_years, 5)),
    ltv: n(rIn.ltv, 0.65),
    interestRate: n(rIn.interest_rate, 0.068),
  };
}

function sandboxDiffers(a: SandboxValues, b: SandboxValues): boolean {
  return (Object.keys(a) as SandboxKey[]).some(k => Math.abs(a[k] - b[k]) > 1e-9);
}

function LiveReturnsSummary({ outputs }: { outputs: ReturnType<typeof useEngineOutputs>['outputs'] }) {
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';

  // ── Canonical headline — WORKER outputs only, no client TS fallback. ──
  // FON-68 split-headline fix: the CoC field is ``year_one_coc`` on the returns
  // engine (returns.py:563). The old ``cash_on_cash_year_one`` read always
  // missed and silently fell back to the client TS model, so IRR/EM came from
  // the worker while CoC came from TS — a split headline. Every KPI below now
  // resolves from the same worker run.
  const irr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const mult = getEngineField<number>(outputs, 'returns', 'equity_multiple');
  const coc = getEngineField<number>(outputs, 'returns', 'year_one_coc');
  const exitValue = getEngineField<number>(outputs, 'returns', 'gross_sale_price');
  const dscrY1 = getEngineField<number>(outputs, 'debt', 'year_one_dscr');
  const holdYears = getEngineField<number>(outputs, 'returns', 'hold_years');

  // ── Ephemeral sandbox — LOCAL state only. ──
  const base = useMemo(() => readSandboxBase(outputs), [outputs]);
  const [sandbox, setSandbox] = useState<SandboxValues>(base);
  const prevBaseRef = useRef(base);
  // Follow a new canonical base (e.g. after a real re-run) only when the user
  // hasn't started testing an override; otherwise their sandbox persists across
  // a background refetch.
  useEffect(() => {
    const prev = prevBaseRef.current;
    setSandbox(cur => (sandboxDiffers(cur, prev) ? cur : base));
    prevBaseRef.current = base;
  }, [base]);

  const dirty = sandboxDiffers(sandbox, base);
  const [preview, setPreview] = useState<ReturnsPreviewResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // Debounced, non-persisting preview call. Clears when the sandbox is back on
  // the base case. Aborts in-flight requests as the slider moves.
  useEffect(() => {
    if (!dirty) {
      setPreview(null);
      setPreviewing(false);
      return;
    }
    if (!isWorkerConnected() || !dealId) return;
    const ctrl = new AbortController();
    setPreviewing(true);
    const t = setTimeout(async () => {
      try {
        const res = await api.engines.returnsPreview(
          dealId,
          {
            overrides: {
              exit_cap_rate: sandbox.exitCapRate,
              revpar_growth: sandbox.revparGrowth,
              hold_years: sandbox.holdYears,
              ltv: sandbox.ltv,
              interest_rate: sandbox.interestRate,
            },
          },
          ctrl.signal,
        );
        setPreview(res);
      } catch {
        // Silent — keep the last good preview; the slider stays usable.
      } finally {
        setPreviewing(false);
      }
    }, 250);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [dirty, dealId, sandbox]);

  const resetToBase = () => setSandbox(base);

  // What the card's live-result line shows: the sandbox preview when an
  // override is active, else the canonical values (so it always reconciles
  // with the headline on the base case).
  const sIrr = dirty && preview ? preview.levered_irr : irr;
  const sMult = dirty && preview ? preview.equity_multiple : mult;
  const sExit = dirty && preview ? preview.exit_value : exitValue;
  const sDscr = dirty && preview ? preview.dscr_y1 : dscrY1;
  const fmtM = (v: number | null | undefined) =>
    v == null ? '—' : `$${(v / 1e6).toFixed(2)}M`;
  const fmtX = (v: number | null | undefined) =>
    v == null ? '—' : `${v.toFixed(2)}x`;

  return (
    <>
      {dirty && (
        <div className="flex items-center gap-3 flex-wrap mb-4 rounded-md border border-brand-200 bg-brand-50 px-3.5 py-2 text-[12px] text-ink-900">
          <span className="text-[10px] font-bold tracking-wide uppercase text-brand-700 whitespace-nowrap">
            Sensitivity override active
          </span>
          <span className="text-ink-500">
            Testing only — the canonical assumptions in Investment and Debt are unchanged.
          </span>
          <button
            onClick={resetToBase}
            className="ml-auto text-brand-700 font-semibold whitespace-nowrap hover:underline"
          >
            Reset to base case
          </button>
        </div>
      )}

      <div className="grid grid-cols-3 gap-4 mb-5">
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
            value={<Traced engine="returns" path="levered_irr">{fmtPct(irr ?? 0, 2)}</Traced>} />
        </CoachMark>
        <KPI label="Equity Multiple" tip={GLOSSARY['Equity Multiple']} flashKey={mult}
          value={<Traced engine="returns" path="equity_multiple">{`${(mult ?? 0).toFixed(2)}x`}</Traced>} />
        <KPI label="Cash-on-Cash" tip={GLOSSARY['CoC']} value={fmtPct(coc ?? 0, 2)} flashKey={coc} />
        <KPI label="Exit Value" value={fmtM(exitValue)} flashKey={exitValue} />
        <KPI label="DSCR Y1" tip={GLOSSARY['DSCR']} value={fmtX(dscrY1)} flashKey={dscrY1} />
        <KPI label="Hold Period" tip={GLOSSARY['Hold Period']}
          value={holdYears != null ? `${holdYears} Years` : '—'} flashKey={holdYears} />
      </div>

      <Card className="p-5 mb-5">
        <div className="flex items-baseline justify-between mb-3">
          <h3 className="text-[14px] font-semibold text-ink-900">Live Assumptions</h3>
          <span className="text-[11px] text-ink-500">
            Temporary overrides for testing — the source of truth stays in Investment and Debt.
          </span>
        </div>
        <div className="grid grid-cols-2 gap-x-8 gap-y-3">
          <Slider
            label="Exit Cap Rate"
            min={0.04} max={0.12} step={0.001}
            value={sandbox.exitCapRate}
            onChange={v => setSandbox(s => ({ ...s, exitCapRate: v }))}
            format={v => fmtPct(v, 2)}
          />
          <Slider
            label="RevPAR Growth"
            min={0} max={0.06} step={0.0025}
            value={sandbox.revparGrowth}
            onChange={v => setSandbox(s => ({ ...s, revparGrowth: v }))}
            format={v => fmtPct(v, 2)}
          />
          <Slider
            label="Hold Period"
            min={3} max={10} step={1}
            value={sandbox.holdYears}
            onChange={v => setSandbox(s => ({ ...s, holdYears: Math.round(v) }))}
            format={v => `${Math.round(v)} years`}
          />
          <Slider
            label="LTV"
            min={0.40} max={0.80} step={0.01}
            value={sandbox.ltv}
            onChange={v => setSandbox(s => ({ ...s, ltv: v }))}
            format={v => fmtPct(v, 0)}
          />
          <Slider
            label="Interest Rate"
            min={0.04} max={0.10} step={0.00125}
            value={sandbox.interestRate}
            onChange={v => setSandbox(s => ({ ...s, interestRate: v }))}
            format={v => fmtPct(v, 3)}
          />
        </div>
        <div className="flex items-center gap-3 flex-wrap mt-4 pt-3 border-t border-border">
          <span className="text-[11.5px] text-ink-500 tabular-nums">
            {dirty ? (previewing ? 'Recomputing sandbox…' : 'Sandbox result:') : 'Base case:'}
            <span className="mx-1.5 font-medium text-ink-900">IRR {fmtPct(sIrr ?? 0, 2)}</span>·
            <span className="mx-1.5 font-medium text-ink-900">EM {fmtX(sMult)}</span>·
            <span className="mx-1.5 font-medium text-ink-900">Exit {fmtM(sExit)}</span>·
            <span className="mx-1.5 font-medium text-ink-900">DSCR {fmtX(sDscr)}</span>
          </span>
          <button
            onClick={resetToBase}
            disabled={!dirty}
            className={cn(
              'ml-auto rounded-md border px-3 py-1.5 text-[11.5px] font-semibold',
              dirty
                ? 'bg-white border-border text-ink-700 hover:bg-ink-50 cursor-pointer'
                : 'bg-ink-50 border-border text-ink-400 cursor-not-allowed',
            )}
          >
            Reset to base case
          </button>
        </div>
      </Card>
    </>
  );
}

function LiveSensitivities() {
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs } = useEngineOutputs(dealId);

  // Worker sensitivity grids ONLY. The canonical engine is the single source of
  // truth — mixing worker grids with client-TS ``defaultSensitivities`` is the
  // exact cross-tab "two different Base" drift the engine-output contract
  // exists to prevent. The engine emits named matrices for Levered IRR and
  // Equity Multiple (FON-53 ``matrices[]``); older runs carry only the
  // top-level primary matrix, which we still accept as the IRR grid.
  const cards = useMemo(() => {
    const list =
      getEngineField<WorkerMatrixRaw[]>(outputs, 'sensitivity', 'matrices') ?? [];
    const byKey = (k: string) => list.find(m => m?.key === k) ?? null;
    const irrRaw = byKey('irr_exit_revpar') ?? topLevelSensitivityMatrix(outputs);
    const emRaw = byKey('em_exit_revpar');
    return [
      { title: 'Levered IRR', matrix: irrRaw ? matrixFromWorkerObj(irrRaw) : null },
      { title: 'Equity Multiple (MOIC)', matrix: emRaw ? matrixFromWorkerObj(emRaw) : null },
      // TODO(FON-68 step 5): the worker sensitivity engine ships no year_one_coc
      // spec, so there is no canonical Year-1 Cash-on-Cash grid to render here.
      // Do NOT reintroduce lib/engines' defaultSensitivities() TS grid to fill
      // it (step 5 owns removing that module) — add a worker CoC spec instead.
    ].filter((c): c is { title: string; matrix: SensitivityMatrix } => c.matrix != null);
  }, [outputs]);

  if (cards.length === 0) {
    return (
      <Card className="p-8 text-center text-[12.5px] text-ink-500">
        Sensitivity grids appear once the Returns engine has run.
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-2 gap-4">
      {cards.map((c, i) => (
        <SensitivityCard key={i} matrix={c.matrix} title={c.title} source="worker" />
      ))}
    </div>
  );
}

interface WorkerCellRaw {
  row_value: number;
  col_value: number;
  value: number;
  is_base: boolean;
}
interface WorkerMatrixRaw {
  key?: string;
  label?: string;
  row_variable: string;
  col_variable: string;
  metric: string;
  rows: number[];
  cols: number[];
  cells: WorkerCellRaw[];
}

// The sensitivity engine's top-level primary matrix (pre-FON-53 shape).
function topLevelSensitivityMatrix(
  outputs: ReturnType<typeof useEngineOutputs>['outputs'],
): WorkerMatrixRaw | null {
  const out = getEngineField<WorkerMatrixRaw>(outputs, 'sensitivity');
  if (!out || !Array.isArray(out.rows) || !Array.isArray(out.cols)) return null;
  if (!Array.isArray(out.cells) || out.cells.length === 0) return null;
  return out;
}

// Map a worker sensitivity matrix (top-level primary OR a named entry from
// ``matrices[]``) into the SensitivityMatrix shape the card renders.
function matrixFromWorkerObj(out: WorkerMatrixRaw): SensitivityMatrix | null {
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

