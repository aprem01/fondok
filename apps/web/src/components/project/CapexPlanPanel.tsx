'use client';
/**
 * CapexPlanPanel - Wave 2 P2.5
 *
 * Three-bucket capex split (PIP / Non-PIP / ROI projects) with timing
 * phasing. Mounts on the Investment tab. No modals - all edits are
 * inline-edit-in-place per the Wave 1 no-modals rule.
 *
 * Bucket semantics:
 *   PIP        - total $ + per-year %, sum-to-100% (auto-rebalances).
 *   Non-PIP    - % of revenue + per-key per-year floor.
 *   ROI        - list of projects: name, invest year, invest $, lift $,
 *                ramp months. Add / delete inline.
 */
import { useState, useMemo } from 'react';
import { ChevronDown, ChevronUp, Plus, X } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { fmtCurrency, cn } from '@/lib/format';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';

// Mirrors apps/worker/app/engines/capex_plan.py outputs.
export interface ROIProject {
  project_name: string;
  initial_investment_usd: number;
  investment_year: number;
  annual_noi_lift_usd: number;
  ramp_months: number;
}

export interface CapexPlanState {
  pip: {
    enabled: boolean;
    total_usd: number;
    per_key_usd: number;
    timing_pct_by_year: number[];
  };
  non_pip: {
    annual_pct_of_revenue: number;
    minimum_per_key_per_year: number;
  };
  roi_projects: ROIProject[];
}

export interface CapexPlanPanelProps {
  keys: number | undefined;
  revenueByYear: number[];
  holdYears: number;
  state: CapexPlanState;
  onChange: (next: CapexPlanState) => void;
  sources?: Record<string, string>;
}

const DEFAULT_PIP_SOURCE = 'analyst_override';
const DEFAULT_NON_PIP_SOURCE = 'analyst_override';
const DEFAULT_ROI_SOURCE = 'analyst_override';

export const DEFAULT_CAPEX_PLAN: CapexPlanState = {
  pip: {
    enabled: false,
    total_usd: 0,
    per_key_usd: 0,
    timing_pct_by_year: [1.0],
  },
  non_pip: {
    annual_pct_of_revenue: 0.04,
    minimum_per_key_per_year: 1500,
  },
  roi_projects: [],
};

function nonPIPForYear(annualPct: number, minPerKey: number, revenue: number, keys: number): number {
  return Math.max(Math.max(0, revenue) * annualPct, Math.max(0, keys) * minPerKey);
}

function roiLiftForYear(p: ROIProject, year: number): number {
  if (p.annual_noi_lift_usd <= 0 || p.ramp_months <= 0) return 0;
  const liftStartsYear = p.investment_year + 1;
  if (year < liftStartsYear) return 0;
  const monthsAtStart = (year - liftStartsYear) * 12;
  const monthsAtEnd = monthsAtStart + 12;
  const startFrac = Math.max(0, Math.min(1, monthsAtStart / p.ramp_months));
  const endFrac = Math.max(0, Math.min(1, monthsAtEnd / p.ramp_months));
  return p.annual_noi_lift_usd * ((startFrac + endFrac) / 2);
}

interface ScheduleRow {
  year: number;
  pip: number;
  non_pip: number;
  roi_invest: number;
  roi_lift: number;
  total: number;
}

function buildSchedule(state: CapexPlanState, keys: number, revenueByYear: number[], holdYears: number): ScheduleRow[] {
  const rows: ScheduleRow[] = [];
  for (let y = 1; y <= holdYears; y++) {
    const pipShare =
      state.pip.enabled && state.pip.timing_pct_by_year.length >= y
        ? state.pip.total_usd * state.pip.timing_pct_by_year[y - 1]
        : 0;
    const rev = revenueByYear[y - 1] ?? 0;
    const nonPip = nonPIPForYear(state.non_pip.annual_pct_of_revenue, state.non_pip.minimum_per_key_per_year, rev, keys);
    const roiInvest = state.roi_projects
      .filter(p => p.investment_year === y)
      .reduce((acc, p) => acc + p.initial_investment_usd, 0);
    const roiLift = state.roi_projects.reduce((acc, p) => acc + roiLiftForYear(p, y), 0);
    rows.push({ year: y, pip: pipShare, non_pip: nonPip, roi_invest: roiInvest, roi_lift: roiLift, total: pipShare + nonPip + roiInvest });
  }
  return rows;
}

export default function CapexPlanPanel({ keys, revenueByYear, holdYears, state, onChange, sources }: CapexPlanPanelProps) {
  const [openPIP, setOpenPIP] = useState(true);
  const [openNonPIP, setOpenNonPIP] = useState(false);
  const [openROI, setOpenROI] = useState(false);
  const keysSafe = keys && keys > 0 ? keys : 1;
  const schedule = useMemo(() => buildSchedule(state, keysSafe, revenueByYear, holdYears), [state, keysSafe, revenueByYear, holdYears]);
  const totalCapexAcrossHold = schedule.reduce((acc, r) => acc + r.total, 0);

  function updatePIPTotal(v: number) {
    onChange({ ...state, pip: { ...state.pip, enabled: v > 0, total_usd: v, per_key_usd: keysSafe > 0 ? v / keysSafe : 0 } });
  }
  function updatePIPPerKey(v: number) {
    const total = v * keysSafe;
    onChange({ ...state, pip: { ...state.pip, enabled: total > 0, total_usd: total, per_key_usd: v } });
  }
  function updatePIPYearPct(idx: number, newPct: number) {
    const clamped = Math.max(0, Math.min(1, newPct));
    const cur = [...state.pip.timing_pct_by_year];
    if (cur.length === 1) {
      onChange({ ...state, pip: { ...state.pip, timing_pct_by_year: [1.0] } });
      return;
    }
    const others = cur.reduce((acc, v, i) => (i === idx ? acc : acc + v), 0);
    const residual = 1 - clamped;
    const next = cur.map((v, i) => {
      if (i === idx) return clamped;
      return others > 0 ? (v / others) * residual : residual / (cur.length - 1);
    });
    onChange({ ...state, pip: { ...state.pip, timing_pct_by_year: next } });
  }
  function setPIPYears(n: number) {
    const cur = state.pip.timing_pct_by_year;
    const next: number[] = [];
    for (let i = 0; i < n; i++) next.push(cur[i] ?? 0);
    const sum = next.reduce((a, b) => a + b, 0);
    const normalized = sum > 0 ? next.map(v => v / sum) : next.map(() => 1 / n);
    onChange({ ...state, pip: { ...state.pip, timing_pct_by_year: normalized } });
  }

  function updateNonPipPct(v: number) {
    onChange({ ...state, non_pip: { ...state.non_pip, annual_pct_of_revenue: Math.max(0, Math.min(0.10, v)) } });
  }
  function updateNonPipMin(v: number) {
    onChange({ ...state, non_pip: { ...state.non_pip, minimum_per_key_per_year: Math.max(0, v) } });
  }

  function addROI() {
    const next: ROIProject = { project_name: 'Untitled project', initial_investment_usd: 0, investment_year: 1, annual_noi_lift_usd: 0, ramp_months: 12 };
    onChange({ ...state, roi_projects: [...state.roi_projects, next] });
  }
  function updateROI(idx: number, patch: Partial<ROIProject>) {
    onChange({ ...state, roi_projects: state.roi_projects.map((p, i) => (i === idx ? { ...p, ...patch } : p)) });
  }
  function deleteROI(idx: number) {
    onChange({ ...state, roi_projects: state.roi_projects.filter((_, i) => i !== idx) });
  }

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">Capex Plan</h3>
        <span className="text-[11px] text-ink-500 tabular-nums">Total {holdYears}-yr capex {fmtCurrency(totalCapexAcrossHold)}</span>
      </div>

      <Section title="PIP (Property Improvement Plan)" open={openPIP} onToggle={() => setOpenPIP(o => !o)}>
        <div className="grid grid-cols-2 gap-3">
          <NumberField label="Total PIP" value={state.pip.total_usd} onChange={updatePIPTotal} step={50_000} format={v => fmtCurrency(v)} />
          <NumberField label="Per Key" value={state.pip.per_key_usd} onChange={updatePIPPerKey} step={500} format={v => `$${v.toFixed(0)}/key`} />
        </div>
        <div className="mt-3">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[11px] text-ink-500 uppercase tracking-wide">Phasing</span>
            <div className="flex items-center gap-1">
              <span className="text-[11px] text-ink-500">Years:</span>
              {[1, 2, 3].map(n => (
                <button key={n} type="button" onClick={() => setPIPYears(n)}
                  className={cn('text-[11px] px-1.5 py-0.5 rounded border',
                    state.pip.timing_pct_by_year.length === n ? 'border-brand-500 text-brand-700 bg-brand-50' : 'border-border text-ink-500 hover:text-ink-900')}>
                  {n}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-2">
            {state.pip.timing_pct_by_year.map((pct, i) => (
              <div key={i} className="flex items-center gap-1">
                <label className="text-[11px] text-ink-500">Y{i + 1}</label>
                <input type="number" value={Math.round(pct * 1000) / 10} step={1} min={0} max={100}
                  onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) updatePIPYearPct(i, v / 100); }}
                  className="w-full px-2 py-1 text-[12px] border border-border rounded tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-500" />
                <span className="text-[11px] text-ink-500">%</span>
              </div>
            ))}
          </div>
          <div className="mt-2 text-[11px] text-ink-500 flex items-center justify-between">
            <AssumptionBadge source={sources?.['capex_plan.pip.total_usd'] ?? DEFAULT_PIP_SOURCE} />
            <span className="tabular-nums">Year-by-year sum: {(state.pip.timing_pct_by_year.reduce((a, b) => a + b, 0) * 100).toFixed(1)}%</span>
          </div>
        </div>
      </Section>

      <Section title="FF&E Reserve (Non-PIP)" open={openNonPIP} onToggle={() => setOpenNonPIP(o => !o)}>
        <div className="grid grid-cols-2 gap-3">
          <NumberField label="% of Revenue" value={state.non_pip.annual_pct_of_revenue} onChange={updateNonPipPct} step={0.0025} min={0} max={0.10} format={v => `${(v * 100).toFixed(2)}%`} />
          <NumberField label="Min per Key per Year" value={state.non_pip.minimum_per_key_per_year} onChange={updateNonPipMin} step={100} format={v => `$${v.toFixed(0)}/key/yr`} />
        </div>
        <div className="mt-2">
          <AssumptionBadge source={sources?.['capex_plan.non_pip.annual_pct_of_revenue'] ?? DEFAULT_NON_PIP_SOURCE} />
        </div>
      </Section>

      <Section title="ROI Projects" open={openROI} onToggle={() => setOpenROI(o => !o)}>
        {state.roi_projects.length === 0 ? (
          <p className="text-[12px] text-ink-500 italic">No ROI capex projects yet.</p>
        ) : (
          <div className="space-y-2">
            {state.roi_projects.map((p, i) => (
              <div key={i} className="grid grid-cols-12 gap-2 items-center border border-border rounded p-2">
                <input className="col-span-3 px-2 py-1 text-[12px] border border-border rounded focus:outline-none focus:ring-2 focus:ring-brand-500"
                  value={p.project_name} onChange={e => updateROI(i, { project_name: e.target.value })} placeholder="Project name" />
                <div className="col-span-2">
                  <label className="text-[10px] text-ink-500">Inv. Yr</label>
                  <input type="number" min={1} max={holdYears} value={p.investment_year}
                    onChange={e => { const v = parseInt(e.target.value, 10); if (!isNaN(v)) updateROI(i, { investment_year: v }); }}
                    className="w-full px-2 py-1 text-[12px] border border-border rounded tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-500" />
                </div>
                <div className="col-span-3">
                  <label className="text-[10px] text-ink-500">Investment</label>
                  <input type="number" min={0} step={25_000} value={p.initial_investment_usd}
                    onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) updateROI(i, { initial_investment_usd: v }); }}
                    className="w-full px-2 py-1 text-[12px] border border-border rounded tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-500" />
                </div>
                <div className="col-span-2">
                  <label className="text-[10px] text-ink-500">NOI lift/yr</label>
                  <input type="number" min={0} step={10_000} value={p.annual_noi_lift_usd}
                    onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) updateROI(i, { annual_noi_lift_usd: v }); }}
                    className="w-full px-2 py-1 text-[12px] border border-border rounded tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-500" />
                </div>
                <div className="col-span-1">
                  <label className="text-[10px] text-ink-500">Ramp mo.</label>
                  <input type="number" min={1} max={36} value={p.ramp_months}
                    onChange={e => { const v = parseInt(e.target.value, 10); if (!isNaN(v)) updateROI(i, { ramp_months: v }); }}
                    className="w-full px-2 py-1 text-[12px] border border-border rounded tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-500" />
                </div>
                <div className="col-span-1 flex justify-end">
                  <button type="button" onClick={() => deleteROI(i)}
                    className="inline-flex items-center justify-center w-6 h-6 rounded text-ink-500 hover:text-danger-700 hover:bg-danger-50 transition-colors"
                    aria-label={`Delete ${p.project_name}`}>
                    <X size={12} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="mt-3 flex items-center justify-between">
          <Button type="button" variant="ghost" onClick={addROI} className="text-[12px]">
            <Plus size={12} className="mr-1" /> Add ROI project
          </Button>
          {state.roi_projects.length > 0 && (<AssumptionBadge source={DEFAULT_ROI_SOURCE} />)}
        </div>
      </Section>

      <div className="mt-5 border-t border-border pt-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-[12px] font-medium text-ink-900">Capex Schedule</span>
          {totalCapexAcrossHold > 0 && (<span className="text-[11px] text-ink-500">Hits IRR through PIP cost basis + ongoing reserve drag.</span>)}
        </div>
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-ink-500 text-[11px] border-b border-border">
              <th className="text-left font-medium pb-1.5">Year</th>
              <th className="text-right font-medium pb-1.5">PIP</th>
              <th className="text-right font-medium pb-1.5">FF&amp;E</th>
              <th className="text-right font-medium pb-1.5">ROI inv.</th>
              <th className="text-right font-medium pb-1.5">ROI lift</th>
              <th className="text-right font-medium pb-1.5">Total</th>
            </tr>
          </thead>
          <tbody>
            {schedule.map(r => (
              <tr key={r.year} className="border-b border-border/50">
                <td className="py-1.5">Y{r.year}</td>
                <td className="text-right tabular-nums">{r.pip ? fmtCurrency(r.pip) : '—'}</td>
                <td className="text-right tabular-nums">{r.non_pip ? fmtCurrency(r.non_pip) : '—'}</td>
                <td className="text-right tabular-nums">{r.roi_invest ? fmtCurrency(r.roi_invest) : '—'}</td>
                <td className="text-right tabular-nums text-success-700">{r.roi_lift ? `+${fmtCurrency(r.roi_lift)}` : '—'}</td>
                <td className="text-right tabular-nums font-semibold">{r.total ? fmtCurrency(r.total) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Section({ title, open, onToggle, children }: { title: string; open: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <div className="mb-3 border border-border rounded">
      <button type="button" onClick={onToggle}
        className="w-full flex items-center justify-between px-3 py-2 text-[12.5px] font-medium text-ink-900 hover:bg-ink-50 transition-colors">
        <span>{title}</span>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
      </button>
      {open && <div className="px-3 pb-3">{children}</div>}
    </div>
  );
}

function NumberField({ label, value, onChange, format, step = 1, min, max }: {
  label: string; value: number; onChange: (v: number) => void; format: (v: number) => string;
  step?: number; min?: number; max?: number;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label className="text-[11px] text-ink-500 uppercase tracking-wide">{label}</label>
        <span className="text-[12px] font-semibold text-brand-700 tabular-nums">{format(value)}</span>
      </div>
      <input type="number" value={value} step={step} min={min} max={max}
        onChange={e => { const v = parseFloat(e.target.value); if (!isNaN(v)) onChange(v); }}
        className="w-full px-2 py-1.5 text-[12px] border border-border rounded-md tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-500" />
    </div>
  );
}
