'use client';
import { useState } from 'react';
import { Card } from '@/components/ui/Card';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import { fmtCurrency, cn } from '@/lib/format';

const subTabs = ['Summary', 'Waterfall Structure', 'Distribution Timeline', 'Returns Summary'];

const waterfall = [
  { tier: 'Pref Return (10%)', gp: 10, lp: 100 },
  { tier: 'Hurdle #2 (15%)', gp: 20, lp: 80 },
  { tier: 'Hurdle #3 (20%)', gp: 25, lp: 75 },
  { tier: 'Hurdle #4 (25%)', gp: 25, lp: 75 },
  { tier: 'Hurdle #5 (30%)', gp: 25, lp: 75 },
  { tier: 'Hurdle #6 (>30%)', gp: 50, lp: 50 },
];

export default function PartnershipTab() {
  const [tab, setTab] = useState('Summary');

  return (
    <div className="flex gap-4">
      <div className="flex-1 min-w-0">
      <EngineHeader
        name="Partnership Engine"
        desc="Models GP/LP waterfall structures, promote calculations, and investor distributions."
        outputs={['GP IRR', 'LP IRR', 'GP Promote', '+1']}
        dependsOn="Returns"
        complete
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

      {tab === 'Summary' && (
        <>
          <div className="grid grid-cols-4 gap-4 mb-5">
            <KPI label="GP LIRR (Net to Sponsor)" value="42.18%" />
            <KPI label="LP LIRR (Net to Investors)" value="20.45%" />
            <KPI label="GP Profit (Carry)" value={fmtCurrency(2_840_000, { compact: true })} />
            <KPI label="Deal Profit (Levered)" value={fmtCurrency(22_120_000, { compact: true })} />
          </div>

          <div className="grid grid-cols-2 gap-5 mb-5">
            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Equity Structure</h3>
              <table className="w-full text-[12.5px]">
                <thead>
                  <tr className="text-ink-500 text-[11px] border-b border-border">
                    <th className="text-left font-medium pb-2">Partner</th>
                    <th className="text-right font-medium pb-2">% Ownership</th>
                    <th className="text-right font-medium pb-2">Equity</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-border/50">
                    <td className="py-2">Sponsor / GP (General Partner)</td>
                    <td className="text-right tabular-nums">10.0%</td>
                    <td className="text-right tabular-nums">{fmtCurrency(1_388_960)}</td>
                  </tr>
                  <tr className="border-b border-border/50">
                    <td className="py-2">LP Investors (Limited Partners)</td>
                    <td className="text-right tabular-nums">90.0%</td>
                    <td className="text-right tabular-nums">{fmtCurrency(12_447_110)}</td>
                  </tr>
                  <tr className="font-semibold border-t border-border">
                    <td className="py-2">Total Equity</td>
                    <td className="text-right tabular-nums">100.0%</td>
                    <td className="text-right tabular-nums">{fmtCurrency(13_836_070)}</td>
                  </tr>
                </tbody>
              </table>
            </Card>

            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Partner Returns Comparison</h3>
              <table className="w-full text-[12.5px]">
                <thead>
                  <tr className="text-ink-500 text-[11px] border-b border-border">
                    <th className="text-left font-medium pb-2">&nbsp;</th>
                    <th className="text-right font-medium pb-2">LIRR</th>
                    <th className="text-right font-medium pb-2">Multiple</th>
                    <th className="text-right font-medium pb-2">Profit</th>
                  </tr>
                </thead>
                <tbody>
                  <tr className="border-b border-border/50">
                    <td className="py-2">GP / Sponsor</td>
                    <td className="text-right tabular-nums">42.18%</td>
                    <td className="text-right tabular-nums">3.04x</td>
                    <td className="text-right tabular-nums">{fmtCurrency(2_840_000)}</td>
                  </tr>
                  <tr>
                    <td className="py-2">LP / Investors</td>
                    <td className="text-right tabular-nums">20.45%</td>
                    <td className="text-right tabular-nums">2.02x</td>
                    <td className="text-right tabular-nums">{fmtCurrency(19_280_000)}</td>
                  </tr>
                </tbody>
              </table>
            </Card>
          </div>

          <Card className="p-5">
            <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Cash Flow Waterfall</h3>
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-ink-500 text-[11px] border-b border-border">
                  <th className="text-left font-medium pb-2">Tier</th>
                  <th className="text-right font-medium pb-2">GP Cash Flow</th>
                  <th className="text-right font-medium pb-2">LP Cash Flow</th>
                </tr>
              </thead>
              <tbody>
                {waterfall.map(w => (
                  <tr key={w.tier} className="border-b border-border/50">
                    <td className="py-2">{w.tier}</td>
                    <td className="text-right tabular-nums">{w.gp}%</td>
                    <td className="text-right tabular-nums">{w.lp}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}

      {tab === 'Waterfall Structure' && (
        <Card className="p-5">
          <h3 className="text-[13px] font-semibold text-ink-900 mb-4">Equity Breakdown</h3>
          <div className="grid grid-cols-2 gap-5">
            {[
              ['Sponsor / GP %', '10%'],
              ['LP Investor %', '90%'],
              ['GP Amount', fmtCurrency(1_388_960)],
              ['LP Amount', fmtCurrency(12_447_110)],
            ].map(([k, v]) => (
              <div key={k}>
                <label className="block text-[11.5px] text-ink-500 mb-1">{k}</label>
                <input value={v} readOnly className="w-full px-3 py-2 text-[13px] border border-border rounded-md bg-ink-300/10" />
              </div>
            ))}
          </div>
          <div className="mt-5 text-[11.5px] text-ink-500">
            Adjust hurdle rates and split percentages to model promote scenarios.
          </div>
        </Card>
      )}

      {tab === 'Distribution Timeline' && (
        <Card className="p-5">
          <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Annual Distributions</h3>
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-ink-500 text-[11px] border-b border-border">
                <th className="text-left font-medium pb-2">Year</th>
                <th className="text-right font-medium pb-2">GP Distribution</th>
                <th className="text-right font-medium pb-2">LP Distribution</th>
                <th className="text-right font-medium pb-2">Total</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['Year 1', 0, 309_500, 309_500],
                ['Year 2', 0, 345_600, 345_600],
                ['Year 3', 0, 384_200, 384_200],
                ['Year 4', 92_000, 425_300, 517_300],
                ['Year 5 (Exit)', 2_748_000, 17_815_400, 20_563_400],
              ].map(([y, gp, lp, t]) => (
                <tr key={y as string} className="border-b border-border/50">
                  <td className="py-2 font-medium">{y}</td>
                  <td className="text-right tabular-nums">{fmtCurrency(gp as number)}</td>
                  <td className="text-right tabular-nums">{fmtCurrency(lp as number)}</td>
                  <td className="text-right tabular-nums font-medium">{fmtCurrency(t as number)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {tab === 'Returns Summary' && (
        <div className="grid grid-cols-2 gap-5">
          <Card className="p-5">
            <h3 className="text-[13px] font-semibold text-ink-900 mb-3">GP Returns</h3>
            <div className="space-y-2 text-[12.5px]">
              <Row k="LIRR" v="42.18%" /><Row k="Equity Multiple" v="3.04x" />
              <Row k="Promote" v={fmtCurrency(2_840_000)} /><Row k="Total Distributions" v={fmtCurrency(2_840_000)} />
            </div>
          </Card>
          <Card className="p-5">
            <h3 className="text-[13px] font-semibold text-ink-900 mb-3">LP Returns</h3>
            <div className="space-y-2 text-[12.5px]">
              <Row k="LIRR" v="20.45%" /><Row k="Equity Multiple" v="2.02x" />
              <Row k="Pref Met" v="Yes" /><Row k="Total Distributions" v={fmtCurrency(19_280_000)} />
            </div>
          </Card>
        </div>
      )}
      </div>
      <EngineRightRail />
    </div>
  );
}

function KPI({ label, value }: { label: string; value: string }) {
  return (
    <Card className="p-4">
      <div className="text-[10.5px] text-ink-500 uppercase tracking-wide">{label}</div>
      <div className="text-[20px] font-semibold tabular-nums mt-1 text-ink-900">{value}</div>
    </Card>
  );
}
function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between py-1.5 border-b border-border/50 last:border-0">
      <span className="text-ink-500">{k}</span>
      <span className="font-medium tabular-nums">{v}</span>
    </div>
  );
}
