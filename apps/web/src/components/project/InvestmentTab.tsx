'use client';
import { useState } from 'react';
import { Card } from '@/components/ui/Card';
import EngineHeader from './EngineHeader';
import { kimptonAnglerOverview } from '@/lib/mockData';
import { fmtCurrency, fmtPct, cn } from '@/lib/format';

const subTabs = ['Deal Summary', 'Sources & Uses', 'Timeline'];

export default function InvestmentTab() {
  const [tab, setTab] = useState('Deal Summary');
  const o = kimptonAnglerOverview;

  return (
    <div>
      <EngineHeader
        name="Investment Engine"
        desc="Defines deal structure, purchase price, key dates, and investment thesis for the transaction."
        outputs={['Purchase Price', 'Price/Key', 'Entry Cap', '+1']}
        dependsOn={null}
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

      {tab === 'Deal Summary' && (
        <div className="grid grid-cols-2 gap-5">
          <Panel title="Property Overview" rows={[
            ['Name', o.general.name], ['Type', o.general.type], ['Location', o.general.location],
            ['Year Built', o.general.yearBuilt.toString()], ['Pre-Renovation Keys', o.general.keys.toString()],
            ['Post-Renovation Keys', o.general.keys.toString()], ['Post-Renovation SF', o.general.gba.toLocaleString()],
          ]} />
          <Panel title="Entry Valuation" rows={[
            ['NOI', fmtCurrency(2_481_478)], ['Entry Cap Rate', fmtPct(o.acquisition.entryCapRate, 2)],
            ['2025 Run-Rate NOI', '$2,481,478'], ['FTM Date', '12/31/2025'],
            ['Hotel Purchase Price', fmtCurrency(o.acquisition.purchasePrice)],
            ['Per Key', fmtCurrency(o.acquisition.pricePerKey)],
          ]} />
          <Panel title="Exit Valuation" rows={[
            ['Exit Month', '60'], ['Exit Date', '9/30/2030'], ['Fwd. 12 Mo NOI', fmtCurrency(o.reversion.terminalNOI)],
            ['Exit Cap Rate', fmtPct(o.reversion.exitCapRate, 2)], ['Gross Exit Value', fmtCurrency(o.reversion.grossSalePrice)],
            ['Per Key', fmtCurrency(o.reversion.grossSalePrice / o.general.keys)],
            ['Exit Sales Cost', fmtCurrency(o.reversion.sellingCosts)], ['Transfer Tax', '0.6%'],
          ]} />
          <Panel title="Renovation Budget" rows={[
            ['Renovation Budget', fmtCurrency(o.investment.renovationBudget)],
            ['Per Key', fmtCurrency(o.investment.renovationBudget / o.general.keys)],
            ['Per SF', fmtCurrency(o.investment.renovationBudget / o.general.gba)],
            ['Hard Costs (75%)', fmtCurrency(3_960_000)],
            ['Soft Costs (20%)', fmtCurrency(528_000)],
            ['Professional Fees (5%)', fmtCurrency(264_000)],
            ['Contingency', fmtCurrency(o.investment.contingency)],
            ['Total Renovation', fmtCurrency(o.investment.renovationBudget)],
          ]} />
        </div>
      )}

      {tab === 'Sources & Uses' && (
        <div className="grid grid-cols-2 gap-5">
          <Card className="p-5">
            <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Transaction Uses</h3>
            <table className="w-full text-[12.5px]">
              <tbody>
                {o.uses.map(u => (
                  <tr key={u.label} className={u.total ? 'font-semibold border-t border-border' : 'border-b border-border/50'}>
                    <td className="py-2">{u.label}</td>
                    <td className="text-right tabular-nums">{fmtCurrency(u.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card className="p-5">
            <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Transaction Sources</h3>
            <table className="w-full text-[12.5px]">
              <tbody>
                {o.sources.map(s => (
                  <tr key={s.label} className={s.total ? 'font-semibold border-t border-border' : 'border-b border-border/50'}>
                    <td className="py-2">{s.label}</td>
                    <td className="text-right tabular-nums">{fmtCurrency(s.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>
      )}

      {tab === 'Timeline' && (
        <Card className="p-5">
          <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Transaction Timeline</h3>
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-ink-500 text-[11px] border-b border-border">
                <th className="text-left font-medium pb-2">Event</th>
                <th className="text-right font-medium pb-2">Start</th>
                <th className="text-right font-medium pb-2">Duration</th>
                <th className="text-right font-medium pb-2">Finish</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['Hotel Purchase', '9/30/2025', '0 mo', '9/30/2025'],
                ['Senior Loan Interest-Only Period', '9/30/2025', '48 mo', '9/30/2029'],
                ['Senior Loan Perm Loan Payoff', '9/30/2029', '12 mo', '9/30/2030'],
                ['Acq. To Renovation', '9/30/2025', '3 mo', '12/31/2025'],
                ['Renovation', '1/1/2026', '12 mo', '12/31/2026'],
                ['Completed Renovation', '1/1/2027', '—', '—'],
                ['Receive Key Money', '1/1/2027', '1 mo', '2/1/2027'],
                ['Ramp-Up Period', '1/1/2027', '12 mo', '12/31/2027'],
                ['Senior Loan Refi', '9/30/2029', '—', '—'],
                ['Disposition After Refi', '9/30/2030', '—', '—'],
                ['Investment Hold Period', '9/30/2025', '60 mo', '9/30/2030'],
                ['Practical Completion (FTM NOI, Value)', '12/31/2026', '—', '—'],
                ['Stabilized (FTM NOI, Value)', '12/31/2027', '—', '—'],
              ].map(row => (
                <tr key={row[0]} className="border-b border-border/50">
                  <td className="py-2">{row[0]}</td>
                  <td className="text-right tabular-nums text-ink-700">{row[1]}</td>
                  <td className="text-right tabular-nums text-ink-700">{row[2]}</td>
                  <td className="text-right tabular-nums text-ink-700">{row[3]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

function Panel({ title, rows }: { title: string; rows: string[][] }) {
  return (
    <Card className="p-5">
      <h3 className="text-[13px] font-semibold text-ink-900 mb-3">{title}</h3>
      <div className="space-y-1 text-[12.5px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between py-1.5 border-b border-border/50 last:border-0">
            <span className="text-ink-500">{k}</span>
            <span className="font-medium tabular-nums text-ink-900">{v}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}
