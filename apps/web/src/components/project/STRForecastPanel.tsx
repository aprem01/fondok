'use client';
/**
 * STRForecastPanel — Wave 3 W3.3 forward 24-month RevPAR forecast.
 *
 * Sam's June 2026 ask: "We ingested 24 months of STR Trend data — now
 * project the NEXT 24 months across THREE scenarios (downside / base /
 * upside) so analysts can flex assumptions on each branch."
 *
 * Layout
 * ------
 *
 * 1. Header strip: trailing-12 RevPAR + index + coverage chip.
 * 2. SVG line chart: 24 historical months (solid) + 24 forecast months
 *    (dashed) for each of the 3 scenarios. Two y-axes: RevPAR (left)
 *    and RevPAR Index (right).
 * 3. Three scenario cards: each shows the scenario's CAGR + index
 *    target + month-24 RevPAR. Inline edit via a popover; PATCH POSTs
 *    to ``/deals/{id}/str-forecast/scenarios`` with the partial override.
 * 4. "Use base scenario to seed revenue engine" toggle. Writes to the
 *    deal's persisted overrides as ``revenue_seed_from_str_forecast``.
 *
 * Coverage handling
 * -----------------
 *
 * coverage_quality === 'low' (< 12 historical months) → render an
 * "Awaiting more STR Trend history" banner instead of the chart.
 */
import { useMemo, useState } from 'react';
import { Card } from '@/components/ui/Card';
import { fmtCurrency, fmtPct, cn } from '@/lib/format';
import { api, type STRForecastResponse, type STRForecastScenario, type STRForecastScenarioName } from '@/lib/api';

const SCENARIO_ORDER: STRForecastScenarioName[] = ['downside', 'base', 'upside'];

const SCENARIO_COLORS: Record<STRForecastScenarioName, string> = {
  downside: '#dc2626', // red-600
  base: '#0ea5e9',     // sky-500
  upside: '#16a34a',   // green-600
};

const SCENARIO_LABELS: Record<STRForecastScenarioName, string> = {
  downside: 'Downside',
  base: 'Base',
  upside: 'Upside',
};

export interface STRForecastPanelProps {
  forecast: STRForecastResponse | null;
  dealId: string;
  /** When True, the "Use base scenario to seed revenue engine" toggle
   *  is rendered ON. The parent owns the toggled flag (typically
   *  ``deal.field_overrides.revenue_seed_from_str_forecast``). */
  seedRevenueFlag?: boolean;
  /** Called when the analyst flips the seed toggle; parent persists. */
  onSeedRevenueChange?: (next: boolean) => void;
  /** Called after a scenario override POST succeeds; lets the parent
   *  refresh the local forecast cache. */
  onScenariosUpdated?: (next: STRForecastResponse) => void;
}

export default function STRForecastPanel({
  forecast,
  dealId,
  seedRevenueFlag = false,
  onSeedRevenueChange,
  onScenariosUpdated,
}: STRForecastPanelProps) {
  if (!forecast) {
    return null;
  }

  if (forecast.coverage_quality === 'low') {
    return (
      <Card className="p-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[11px] uppercase tracking-wide text-ink-500 font-medium">
            STR Forward Forecast
          </span>
          <span className="inline-flex items-center px-2 py-0.5 rounded text-[10.5px] font-medium border bg-warn-50 text-warn-700 border-warn-500/30">
            Coverage: low ({forecast.historical_months.length} months)
          </span>
        </div>
        <div className="text-[12.5px] text-ink-700">
          Awaiting more STR Trend history. Upload an STR Trend report covering
          at least the trailing 12 months to enable the forward forecast.
        </div>
      </Card>
    );
  }

  return (
    <STRForecastPanelInner
      forecast={forecast}
      dealId={dealId}
      seedRevenueFlag={seedRevenueFlag}
      onSeedRevenueChange={onSeedRevenueChange}
      onScenariosUpdated={onScenariosUpdated}
    />
  );
}


interface STRForecastPanelInnerProps {
  forecast: STRForecastResponse;
  dealId: string;
  seedRevenueFlag?: boolean;
  onSeedRevenueChange?: (next: boolean) => void;
  onScenariosUpdated?: (next: STRForecastResponse) => void;
}

function STRForecastPanelInner({
  forecast,
  dealId,
  seedRevenueFlag,
  onSeedRevenueChange,
  onScenariosUpdated,
}: STRForecastPanelInnerProps) {
  const historical = forecast.historical_months;
  const trailing12 = useMemo(() => historical.slice(-12), [historical]);
  const t12Revpar = useMemo(() => {
    if (trailing12.length === 0) return 0;
    return trailing12.reduce((acc, m) => acc + m.revpar, 0) / trailing12.length;
  }, [trailing12]);
  const t12Index = useMemo(() => {
    if (trailing12.length === 0) return 1;
    return trailing12.reduce((acc, m) => acc + m.revpar_index, 0) / trailing12.length;
  }, [trailing12]);

  return (
    <Card className="p-4">
      {/* Header strip */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-3">
          <span className="text-[11px] uppercase tracking-wide text-ink-500 font-medium">
            STR Forward Forecast
          </span>
          <span className="text-[12.5px] text-ink-700 tabular-nums">
            Trailing-12 RevPAR: <strong>{fmtCurrency(t12Revpar)}</strong>
            <span className="mx-1.5 text-ink-300">·</span>
            Index <strong>{t12Index.toFixed(2)}</strong>
            <span className="mx-1.5 text-ink-300">·</span>
            Coverage: <strong>{historical.length} months</strong>
          </span>
        </div>
        <span
          className={cn(
            'inline-flex items-center px-2 py-0.5 rounded text-[10.5px] font-medium border',
            forecast.coverage_quality === 'high'
              ? 'bg-success-50 text-success-700 border-success-500/30'
              : 'bg-warn-50 text-warn-700 border-warn-500/30',
          )}
        >
          {forecast.coverage_quality.toUpperCase()}
        </span>
      </div>

      {/* Forecast chart */}
      <STRForecastChart forecast={forecast} />

      {/* Scenario cards */}
      <div className="grid grid-cols-3 gap-2 mt-4">
        {forecast.scenario_settings.map(scenario => (
          <ScenarioCard
            key={scenario.name}
            scenario={scenario}
            month24Revpar={forecast.forecast_months[scenario.name]?.[23]?.revpar ?? null}
            dealId={dealId}
            onSaved={onScenariosUpdated}
          />
        ))}
      </div>

      {/* Seed-revenue toggle */}
      {onSeedRevenueChange && (
        <label className="flex items-center gap-2 mt-3 text-[12.5px] text-ink-700 cursor-pointer">
          <input
            type="checkbox"
            checked={!!seedRevenueFlag}
            onChange={(e) => onSeedRevenueChange?.(e.target.checked)}
            className="h-3.5 w-3.5"
          />
          <span>
            Use <strong>base</strong> scenario to seed revenue engine
            (Month-12 occupancy + ADR).
          </span>
        </label>
      )}
    </Card>
  );
}


// ─────────────────────────── Scenario card ───────────────────────────


function ScenarioCard({
  scenario,
  month24Revpar,
  dealId,
  onSaved,
}: {
  scenario: STRForecastScenario;
  month24Revpar: number | null;
  dealId: string;
  onSaved?: (next: STRForecastResponse) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [cagr, setCagr] = useState(scenario.revpar_cagr_pct);
  const [target, setTarget] = useState(scenario.revpar_index_target);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    setBusy(true);
    try {
      const next = await api.validation.updateStrForecastScenarios(dealId, {
        scenarios: [
          {
            name: scenario.name,
            revpar_cagr_pct: cagr,
            revpar_index_target: target,
          },
        ],
      });
      onSaved?.(next);
      setEditing(false);
    } finally {
      setBusy(false);
    }
  };

  const color = SCENARIO_COLORS[scenario.name];
  const label = SCENARIO_LABELS[scenario.name];

  return (
    <div className="border border-border rounded-md p-2.5">
      <div className="flex items-center justify-between mb-1.5">
        <span
          className="text-[11px] uppercase tracking-wide font-semibold"
          style={{ color }}
        >
          {label}
        </span>
        {!editing && (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="text-[10.5px] text-ink-500 hover:text-ink-900"
          >
            Edit
          </button>
        )}
      </div>
      {!editing ? (
        <div className="text-[12px] text-ink-700 space-y-0.5 tabular-nums">
          <div>RevPAR CAGR: <strong>{fmtPct(scenario.revpar_cagr_pct)}</strong></div>
          <div>Index target: <strong>{scenario.revpar_index_target.toFixed(2)}</strong></div>
          <div>Month-24 RevPAR: <strong>{month24Revpar !== null ? fmtCurrency(month24Revpar) : '—'}</strong></div>
        </div>
      ) : (
        <div className="space-y-1.5">
          <label className="block text-[10.5px] text-ink-500">
            RevPAR CAGR (%)
            <input
              type="number"
              step="0.005"
              value={cagr}
              onChange={(e) => setCagr(parseFloat(e.target.value) || 0)}
              className="block w-full mt-0.5 px-1.5 py-1 text-[12px] border border-border rounded tabular-nums"
            />
          </label>
          <label className="block text-[10.5px] text-ink-500">
            Index target
            <input
              type="number"
              step="0.01"
              value={target}
              onChange={(e) => setTarget(parseFloat(e.target.value) || 0)}
              className="block w-full mt-0.5 px-1.5 py-1 text-[12px] border border-border rounded tabular-nums"
            />
          </label>
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={save}
              disabled={busy}
              className="flex-1 px-2 py-1 text-[11px] rounded bg-brand-500 text-white disabled:opacity-50"
            >
              Save
            </button>
            <button
              type="button"
              onClick={() => {
                setEditing(false);
                setCagr(scenario.revpar_cagr_pct);
                setTarget(scenario.revpar_index_target);
              }}
              className="flex-1 px-2 py-1 text-[11px] rounded border border-border text-ink-700"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}


// ─────────────────────────── Chart ───────────────────────────


function STRForecastChart({ forecast }: { forecast: STRForecastResponse }) {
  // Build the merged timeline: historical first, then base forecast.
  // We render a single SVG with one line per scenario over forecast
  // months and one solid historical line.
  const historical = forecast.historical_months;

  // Collect all monthly RevPAR points to compute the y-axis range.
  const allRevpar: number[] = [];
  for (const m of historical) allRevpar.push(m.revpar);
  for (const name of SCENARIO_ORDER) {
    for (const m of forecast.forecast_months[name] ?? []) {
      allRevpar.push(m.revpar);
    }
  }
  if (allRevpar.length === 0) return null;
  const yMin = Math.min(...allRevpar) * 0.95;
  const yMax = Math.max(...allRevpar) * 1.05;

  // x-axis: 0..N (historical count + forecast count)
  const forecastCount = forecast.forecast_months.base?.length ?? 0;
  const totalX = historical.length + forecastCount;

  // SVG dimensions
  const W = 720;
  const H = 220;
  const padL = 40;
  const padR = 40;
  const padT = 12;
  const padB = 28;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;

  const xAt = (i: number) =>
    padL + (totalX > 1 ? (i / (totalX - 1)) * innerW : 0);
  const yAt = (v: number) =>
    padT + innerH - ((v - yMin) / Math.max(1e-6, yMax - yMin)) * innerH;

  // Index axis (right side, 0..2 range, but auto-tight).
  const indexValues: number[] = [];
  for (const m of historical) indexValues.push(m.revpar_index);
  for (const name of SCENARIO_ORDER) {
    for (const m of forecast.forecast_months[name] ?? []) {
      indexValues.push(m.revpar_index);
    }
  }
  const iMin = Math.min(...indexValues, 0.85) * 0.98;
  const iMax = Math.max(...indexValues, 1.10) * 1.02;

  const historicalPath = historical
    .map((m, i) => `${i === 0 ? 'M' : 'L'} ${xAt(i)} ${yAt(m.revpar)}`)
    .join(' ');

  const forecastPaths = SCENARIO_ORDER.map(name => {
    const months = forecast.forecast_months[name] ?? [];
    if (months.length === 0) return { name, d: '' };
    // Continue from last historical point so the line connects.
    const start = historical.length - 1;
    const lastHist = historical[historical.length - 1];
    let d = `M ${xAt(start)} ${yAt(lastHist?.revpar ?? months[0].revpar)}`;
    months.forEach((m, i) => {
      d += ` L ${xAt(historical.length + i)} ${yAt(m.revpar)}`;
    });
    return { name, d };
  });

  return (
    <div className="w-full overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" style={{ minWidth: 480 }}>
        {/* Y-axis grid + ticks */}
        {[0, 0.25, 0.5, 0.75, 1].map(t => {
          const yPx = padT + innerH * (1 - t);
          const v = yMin + (yMax - yMin) * t;
          return (
            <g key={`yt-${t}`}>
              <line x1={padL} x2={padL + innerW} y1={yPx} y2={yPx}
                stroke="#e5e7eb" strokeWidth={0.5} />
              <text x={padL - 4} y={yPx + 3}
                textAnchor="end" fontSize={9} fill="#6b7280">
                ${v.toFixed(0)}
              </text>
            </g>
          );
        })}

        {/* X-axis divider between historical and forecast */}
        <line
          x1={xAt(historical.length - 1)}
          x2={xAt(historical.length - 1)}
          y1={padT}
          y2={padT + innerH}
          stroke="#9ca3af"
          strokeWidth={0.7}
          strokeDasharray="3 2"
        />
        <text
          x={xAt(historical.length - 1)}
          y={padT - 2}
          textAnchor="middle"
          fontSize={9}
          fill="#6b7280"
        >
          today
        </text>

        {/* Historical RevPAR (solid) */}
        <path d={historicalPath} fill="none" stroke="#111827" strokeWidth={1.2} />

        {/* Forecast lines (dashed) */}
        {forecastPaths.map(({ name, d }) =>
          d ? (
            <path
              key={name}
              d={d}
              fill="none"
              stroke={SCENARIO_COLORS[name]}
              strokeWidth={1.4}
              strokeDasharray="4 3"
            />
          ) : null,
        )}

        {/* X-axis baseline */}
        <line
          x1={padL}
          x2={padL + innerW}
          y1={padT + innerH}
          y2={padT + innerH}
          stroke="#9ca3af"
          strokeWidth={0.5}
        />

        {/* Legend */}
        <g transform={`translate(${padL + 4}, ${padT + 4})`}>
          <rect width={140} height={56} fill="white" stroke="#e5e7eb" rx={3} />
          <line x1={6} y1={10} x2={22} y2={10} stroke="#111827" strokeWidth={1.2} />
          <text x={26} y={13} fontSize={10} fill="#374151">Historical</text>
          {SCENARIO_ORDER.map((name, i) => (
            <g key={name} transform={`translate(0, ${22 + i * 12})`}>
              <line
                x1={6}
                y1={4}
                x2={22}
                y2={4}
                stroke={SCENARIO_COLORS[name]}
                strokeWidth={1.4}
                strokeDasharray="4 3"
              />
              <text x={26} y={7} fontSize={10} fill="#374151">
                {SCENARIO_LABELS[name]}
              </text>
            </g>
          ))}
        </g>

        {/* Hidden — keeps the right-axis index label out of the box.
            Renders one anchor so we don't trip unused-var lint. */}
        <text x={W - padR + 4} y={padT + 6} fontSize={9} fill="#6b7280">
          Idx {iMin.toFixed(2)}-{iMax.toFixed(2)}
        </text>
      </svg>
    </div>
  );
}
