'use client';
import { LayoutGrid, Download, Pencil, Link2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { kimptonAnglerOverview, findBrand, returnProfiles, positioningTiers } from '@/lib/mockData';
import { fmtCurrency, fmtPct, fmtMillions, fmtNumber } from '@/lib/format';

export default function OverviewTab({ projectId }: { projectId: number }) {
  if (projectId !== 7) {
    return (
      <Card className="p-16 text-center">
        <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
          <LayoutGrid size={20} className="text-ink-400" />
        </div>
        <h3 className="text-[15px] font-semibold text-ink-900">No Underwriting Data</h3>
        <p className="text-[12.5px] text-ink-500 mt-1">Run underwriting to populate the overview.</p>
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
        <h3 className="text-[13px] font-semibold text-ink-900 mb-4">Returns Summary</h3>
        <div className="grid grid-cols-5 gap-4">
          {[
            ['Levered IRR', fmtPct(o.returns.leveredIRR, 2)],
            ['Unlevered IRR', fmtPct(o.returns.unleveredIRR, 2)],
            ['Equity Multiple', `${o.returns.equityMultiple.toFixed(2)}x`],
            ['Year-1 CoC', fmtPct(o.returns.yearOneCoC, 1)],
            ['Hold Period', `${o.returns.hold} Years`],
          ].map(([k, v]) => (
            <div key={k}>
              <div className="text-[11px] text-ink-500 uppercase tracking-wide">{k}</div>
              <div className="text-[20px] font-semibold text-brand-700 tabular-nums mt-0.5">{v}</div>
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
    </div>
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
