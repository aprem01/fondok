'use client';
import { useMemo, useState } from 'react';
import { LayoutGrid, Download, Pencil, Link2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import {
  kimptonAnglerOverview, findBrand, returnProfiles, positioningTiers,
  brandFamilies,
} from '@/lib/mockData';
import { fmtCurrency, fmtPct, fmtMillions, fmtNumber, cn } from '@/lib/format';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';
import { Term } from '@/components/help/Term';
import { GLOSSARY } from '@/lib/glossary';

export default function OverviewTab({ projectId }: { projectId: number | string }) {
  if (projectId !== 7) {
    return (
      <Card className="p-16 text-center">
        <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
          <LayoutGrid size={20} className="text-ink-400" />
        </div>
        <h3 className="text-[15px] font-semibold text-ink-900">No underwriting data yet</h3>
        <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
          We need an Offering Memorandum (the broker&apos;s pitch deck) and a T-12 (the last 12 months
          of profit &amp; loss) before we can build the model. Drop them into the
          <span className="font-medium"> Data Room</span> tab to get started.
        </p>
        <Button variant="primary" size="sm" className="mt-4">Run Underwriting</Button>
      </Card>
    );
  }

  const o = kimptonAnglerOverview;

  // Brand tier enrichment: if the deal's brand string resolves to a known
  // catalog brand, render "Kimpton (Upper Upscale)" instead of just "Kimpton".
  const brandMatch = findBrand(o.general.brand);
  const brandDisplay = brandMatch
    ? `${o.general.brand} (${brandMatch.brand.tier})`
    : o.general.brand;

  // Investment Profile rows (return strategy, IRR target, positioning tier).
  const profile = returnProfiles.find(r => r.id === o.investmentProfile.returnProfile);
  const positioning = positioningTiers.find(p => p.id === o.investmentProfile.positioning);

  return (
    <div className="space-y-5">
      <IntroCard
        dismissKey="overview-intro"
        title="The complete underwriting model on one page"
        body={
          <>
            Acquisition assumptions, financing, returns — every input and output the AI built from
            your documents. The colored dots in the legend below tell you which numbers you can edit
            (amber), which are derived from other engines (green), and which are read-only.
          </>
        }
      />

      <Card className="p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-5 text-[11.5px] text-ink-500">
            <span className="flex items-center gap-1.5">
              <Pencil size={11} className="text-warn-500" /> Editable
            </span>
            <span className="flex items-center gap-1.5">
              <Link2 size={11} className="text-success-500" /> Linked
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded bg-ink-300/40" /> Read-Only
            </span>
          </div>
          <Button variant="secondary" size="sm"><Download size={12} /> Export to Excel</Button>
        </div>
      </Card>

      <ModelSettings
        defaults={{
          dealType: 'acquisition',
          returnProfile: o.investmentProfile.returnProfile,
          brand: o.general.brand,
          positioning: 'default',
        }}
      />

      <div className="grid grid-cols-2 gap-5">
        <Section title="General Information" rows={[
          ['Property Name', o.general.name],
          ['Location', o.general.location],
          ['Type', o.general.type],
          ['Brand', brandDisplay],
          ['Keys', fmtNumber(o.general.keys)],
          ['Year Built', o.general.yearBuilt.toString()],
          ['GBA (SF)', fmtNumber(o.general.gba)],
          ['Meeting Space', o.general.meetingSpace],
          ['Parking Spaces', o.general.parking.toString()],
          ['F&B Outlets', o.general.fbOutlets.toString()],
        ]} />

        <Section title="Investment Profile" rows={[
          ['Return Strategy', profile?.label ?? '—'],
          ['IRR Target', profile?.target ?? '—'],
          ['Positioning Tier', positioning?.label ?? '—'],
        ]} />
      </div>

      <div className="grid grid-cols-1 gap-5">
        <Section title="Acquisition Assumptions" rows={[
          ['Purchase Price', fmtCurrency(o.acquisition.purchasePrice)],
          ['Price/Key', fmtCurrency(o.acquisition.pricePerKey)],
          ['Entry Cap Rate', fmtPct(o.acquisition.entryCapRate, 2)],
          ['Closing Costs', fmtCurrency(o.acquisition.closingCosts)],
          ['Working Capital', fmtCurrency(o.acquisition.workingCapital)],
        ]} />
      </div>

      <Card className="p-5 bg-brand-50 border-brand-100">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-1">Returns Summary</h3>
        <p className="text-[11.5px] text-ink-500 mb-4">What investors will earn over the hold period.</p>
        <div className="grid grid-cols-5 gap-4">
          {([
            { label: 'Levered IRR', value: fmtPct(o.returns.leveredIRR, 2),
              tip: GLOSSARY['IRR'] + ' "Levered" means after debt service.' },
            { label: 'Unlevered IRR', value: fmtPct(o.returns.unleveredIRR, 2),
              tip: 'Asset-level IRR before debt — what you\'d earn if the hotel were paid for in cash.' },
            { label: 'Equity Multiple', value: `${o.returns.equityMultiple.toFixed(2)}x`,
              tip: GLOSSARY['Equity Multiple'] },
            { label: 'Year-1 CoC', value: fmtPct(o.returns.yearOneCoC, 1),
              tip: GLOSSARY['CoC'] + ' Year-1 is the first full year after acquisition.' },
            { label: 'Hold Period', value: `${o.returns.hold} Years`,
              tip: GLOSSARY['Hold Period'] },
          ]).map(s => (
            <div key={s.label}>
              <div className="text-[11px] text-ink-500 uppercase tracking-wide">
                <MetricLabel label={s.label} tip={s.tip} />
              </div>
              <div className="text-[20px] font-semibold text-brand-700 tabular-nums mt-0.5">{s.value}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-5">
        <Section title="Reversion Assumptions" rows={[
          ['Exit Cap Rate', fmtPct(o.reversion.exitCapRate, 2)],
          ['Exit Year', `Year ${o.reversion.exitYear}`],
          ['Terminal NOI', fmtCurrency(o.reversion.terminalNOI)],
          ['Gross Sale Price', fmtCurrency(o.reversion.grossSalePrice)],
          ['Selling Costs', fmtCurrency(o.reversion.sellingCosts)],
        ]} />

        <Section title="Investment Assumptions" rows={[
          ['Renovation Budget', fmtCurrency(o.investment.renovationBudget)],
          ['Hard Costs/Key', fmtCurrency(o.investment.hardCostsPerKey)],
          ['Soft Costs', fmtCurrency(o.investment.softCosts)],
          ['Contingency', fmtCurrency(o.investment.contingency)],
          ['Total Capital', fmtCurrency(o.investment.totalCapital)],
        ]} />
      </div>

      <div className="grid grid-cols-2 gap-5">
        <Section title="Acquisition Financing" rows={[
          ['Loan Amount', fmtCurrency(o.financing.loanAmount)],
          ['LTV', fmtPct(o.financing.ltv, 0)],
          ['Interest Rate', fmtPct(o.financing.interestRate, 2)],
          ['DSCR', `${o.financing.dscr.toFixed(2)}x`],
          ['Annual Debt Service', fmtCurrency(o.financing.annualDebtService)],
          ['Term', `${o.financing.term} Years`],
          ['Amortization', `${o.financing.amortization} Years`],
        ]} />

        <Section title="Refinancing Assumptions" rows={[
          ['Refi Year', `Year ${o.refi.refiYear}`],
          ['Refi LTV', fmtPct(o.refi.refiLTV, 0)],
          ['Refi Rate', fmtPct(o.refi.refiRate, 2)],
          ['Refi Term', `${o.refi.refiTerm} Years`],
          ['Amortization', `${o.refi.refiAmortization} Years`],
        ]} />
      </div>

      <div className="grid grid-cols-2 gap-5">
        <Card className="p-5">
          <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Sources <span className="text-[11px] text-ink-500 font-normal">($ in mm)</span></h3>
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-ink-500 text-[11px]">
                <th className="text-left font-medium pb-2">&nbsp;</th>
                <th className="text-right font-medium pb-2">Amount</th>
                <th className="text-right font-medium pb-2">% Total</th>
                <th className="text-right font-medium pb-2">Per Key</th>
              </tr>
            </thead>
            <tbody>
              {o.sources.map(s => (
                <tr key={s.label} className={s.total ? 'font-semibold border-t border-border' : ''}>
                  <td className="py-1.5">{s.label}</td>
                  <td className="text-right tabular-nums">{(s.amount / 1e6).toFixed(2)}</td>
                  <td className="text-right tabular-nums">{(s.pct * 100).toFixed(1)}%</td>
                  <td className="text-right tabular-nums">{(s.amount / o.general.keys / 1e3).toFixed(0)}K</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card className="p-5">
          <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Uses <span className="text-[11px] text-ink-500 font-normal">($ in mm)</span></h3>
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-ink-500 text-[11px]">
                <th className="text-left font-medium pb-2">&nbsp;</th>
                <th className="text-right font-medium pb-2">Amount</th>
              </tr>
            </thead>
            <tbody>
              {o.uses.map(u => (
                <tr key={u.label} className={u.total ? 'font-semibold border-t border-border' : ''}>
                  <td className="py-1.5">{u.label}</td>
                  <td className="text-right tabular-nums">{(u.amount / 1e6).toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <Card className="p-5">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-1">Proforma Operating Summary</h3>
        <div className="text-[11px] text-ink-500 mb-3">($ in 000s, FYE Dec 31)</div>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px] min-w-[600px]">
            <thead>
              <tr className="text-ink-500 text-[10.5px] border-b border-border">
                <th className="text-left font-medium pb-2 w-48">&nbsp;</th>
                <th className="text-right font-medium pb-2">Year 1</th>
                <th className="text-right font-medium pb-2">Year 2</th>
                <th className="text-right font-medium pb-2">Year 3</th>
                <th className="text-right font-medium pb-2">Year 4</th>
                <th className="text-right font-medium pb-2">Year 5</th>
                <th className="text-right font-medium pb-2">CAGR</th>
              </tr>
            </thead>
            <tbody>
              {o.proforma.map(r => (
                <tr key={r.label} className={`border-b border-border/50 ${r.bold ? 'font-semibold bg-ink-300/5' : ''}`}>
                  <td className="py-1.5">{r.label}</td>
                  <td className="text-right tabular-nums">{r.y1.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{r.y2.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{r.y3.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{r.y4.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{r.y5.toLocaleString()}</td>
                  <td className="text-right tabular-nums">{r.cagr ? `${(r.cagr * 100).toFixed(1)}%` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <SensitivityAnalysis />
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Model Settings — inline editor sitting above the General Info grid.
// Local state only; "X Changes Pending" pill flips amber when any
// field diverges from its initial default. No persistence yet.
// ──────────────────────────────────────────────────────────

interface ModelSettingsState {
  dealType: 'acquisition' | 'development';
  returnProfile: string;
  brand: string;
  positioning: string;
}

function ModelSettings({ defaults }: { defaults: ModelSettingsState }) {
  const [state, setState] = useState<ModelSettingsState>(defaults);
  const changeCount = (Object.keys(defaults) as (keyof ModelSettingsState)[])
    .reduce((n, k) => n + (state[k] !== defaults[k] ? 1 : 0), 0);

  // Flatten all known brands for the picker (family > brand[]).
  const brandOptions = useMemo(() => {
    return brandFamilies.flatMap(f =>
      f.brands.map(b => ({ value: b.name, label: `${b.name} (${b.tier})`, family: f.family }))
    );
  }, []);

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-[14px] font-semibold text-ink-900">Model Settings</h3>
        {changeCount === 0 ? (
          <span className="px-2 py-0.5 text-[10.5px] font-medium rounded-full bg-ink-300/30 text-ink-700">
            No Changes
          </span>
        ) : (
          <span className="px-2 py-0.5 text-[10.5px] font-medium rounded-full bg-warn-50 text-warn-700 border border-warn-500/30">
            {changeCount} Change{changeCount === 1 ? '' : 's'} Pending
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-4">
        {/* Deal Type pill toggle */}
        <div>
          <label className="block text-[11px] font-medium text-ink-700 uppercase tracking-wide mb-1.5">
            Deal Type
          </label>
          <div className="inline-flex bg-ink-300/15 p-0.5 rounded-md">
            {(['acquisition', 'development'] as const).map(opt => (
              <button
                key={opt}
                type="button"
                onClick={() => setState(s => ({ ...s, dealType: opt }))}
                className={cn(
                  'px-3 py-1 text-[12px] rounded transition-colors capitalize',
                  state.dealType === opt
                    ? 'bg-white text-brand-700 font-medium shadow-sm'
                    : 'text-ink-500 hover:text-ink-900'
                )}
              >
                {opt}
              </button>
            ))}
          </div>
        </div>

        {/* Returns Profile dropdown */}
        <div>
          <label className="block text-[11px] font-medium text-ink-700 uppercase tracking-wide mb-1.5">
            Returns Profile
          </label>
          <select
            value={state.returnProfile}
            onChange={e => setState(s => ({ ...s, returnProfile: e.target.value }))}
            className="w-full px-3 py-1.5 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          >
            {returnProfiles.map(p => (
              <option key={p.id} value={p.id}>{p.label} ({p.target})</option>
            ))}
          </select>
        </div>

        {/* Brand picker */}
        <div>
          <label className="block text-[11px] font-medium text-ink-700 uppercase tracking-wide mb-1.5">
            Brand
          </label>
          <select
            value={state.brand}
            onChange={e => setState(s => ({ ...s, brand: e.target.value }))}
            className="w-full px-3 py-1.5 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          >
            {/* Keep the deal's current brand string as the head option even if
                it doesn't match a catalog entry exactly (e.g. "Kimpton"). */}
            {!brandOptions.some(b => b.value === state.brand) && (
              <option value={state.brand}>{state.brand}</option>
            )}
            {brandFamilies.map(fam => (
              <optgroup key={fam.family} label={fam.family}>
                {fam.brands.map(b => (
                  <option key={b.name} value={b.name}>{b.name} ({b.tier})</option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>

        {/* Positioning dropdown */}
        <div>
          <label className="block text-[11px] font-medium text-ink-700 uppercase tracking-wide mb-1.5">
            Positioning
          </label>
          <select
            value={state.positioning}
            onChange={e => setState(s => ({ ...s, positioning: e.target.value }))}
            className="w-full px-3 py-1.5 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          >
            {positioningTiers.map(p => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </div>
      </div>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────
// Sensitivity Analysis — three side-by-side heatmaps anchored
// on the Kimpton base case (IRR 23.01% / MOIC 2.37x / CoC 15.8%).
// Numbers are synthesized — meant to convey shape, not be canonical.
// ──────────────────────────────────────────────────────────

function SensitivityAnalysis() {
  return (
    <Card className="p-5">
      <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Sensitivity Analysis</h3>
      <div className="text-[11px] text-ink-500 mb-4">
        Base case highlighted; cells coloured green (best) → red (worst).
      </div>
      <div className="grid grid-cols-3 gap-4">
        <Heatmap
          title="Levered IRR"
          rowLabel="Exit Cap"
          colLabel="Purchase Price"
          rows={['6.0%', '6.5%', '7.0%', '7.5%', '8.0%']}
          cols={['-10%', '-5%', 'Base', '+5%', '+10%']}
          // 5x5 — base centred on 23.01%. Lower exit cap + lower price → richer IRR.
          data={[
            [33.4, 30.6, 28.1, 25.8, 23.7],
            [30.7, 28.0, 25.5, 23.2, 21.1],
            [28.0, 25.3, 23.01, 20.8, 18.7],
            [25.4, 22.8, 20.5, 18.3, 16.3],
            [22.9, 20.4, 18.1, 15.9, 13.9],
          ]}
          baseRow={2} baseCol={2} unit="%"
        />
        <Heatmap
          title="Equity Multiple (MOIC)"
          rowLabel="Exit Cap"
          colLabel="LTC"
          rows={['6.0%', '6.5%', '7.0%', '7.5%', '8.0%']}
          cols={['60%', '65%', '70%', '75%']}
          data={[
            [2.61, 2.74, 2.88, 3.04],
            [2.46, 2.58, 2.72, 2.87],
            [2.31, 2.37, 2.49, 2.62],
            [2.17, 2.27, 2.38, 2.50],
            [2.04, 2.13, 2.24, 2.36],
          ]}
          baseRow={2} baseCol={1} unit="x"
        />
        <Heatmap
          title="Year-1 Cash-on-Cash"
          rowLabel="Cap Rate"
          colLabel="Hold"
          rows={['6.0%', '6.5%', '7.0%', '7.5%', '8.0%']}
          cols={['3y', '4y', '5y', '6y', '7y']}
          data={[
            [13.1, 13.9, 14.7, 15.5, 16.3],
            [13.7, 14.5, 15.3, 16.1, 16.9],
            [14.2, 15.0, 15.8, 16.6, 17.4],
            [14.7, 15.5, 16.3, 17.1, 17.9],
            [15.2, 16.0, 16.8, 17.6, 18.4],
          ]}
          baseRow={2} baseCol={2} unit="%"
        />
      </div>
    </Card>
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
    const t = max === min ? 0.5 : (v - min) / (max - min);
    if (t > 0.66) return 'bg-success-50 text-success-700';
    if (t > 0.33) return 'bg-warn-50 text-warn-700';
    return 'bg-danger-50 text-danger-700';
  };
  return (
    <Card className="p-4">
      <h4 className="text-[12.5px] font-semibold text-ink-900 mb-2">{title}</h4>
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

function Section({ title, rows }: { title: string; rows: string[][] }) {
  return (
    <Card className="p-5">
      <h3 className="text-[13px] font-semibold text-ink-900 mb-3">{title}</h3>
      <div className="space-y-1.5 text-[12.5px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between py-1.5 border-b border-border/50 last:border-0">
            <span className="text-ink-500">{k}</span>
            <span className="font-medium tabular-nums text-ink-900">{v}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}
