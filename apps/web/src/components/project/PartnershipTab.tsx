'use client';
import { useState } from 'react';
import { useParams } from 'next/navigation';
import { Users } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import { fmtCurrency, fmtPct, cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';
import { GLOSSARY } from '@/lib/glossary';

const subTabs = ['Summary', 'Waterfall Structure', 'Distribution Timeline', 'Returns Summary'];

const waterfall = [
  { tier: 'Pref Return (10%)', gp: 10, lp: 100 },
  { tier: 'Hurdle #2 (15%)', gp: 20, lp: 80 },
  { tier: 'Hurdle #3 (20%)', gp: 25, lp: 75 },
  { tier: 'Hurdle #4 (25%)', gp: 25, lp: 75 },
  { tier: 'Hurdle #5 (30%)', gp: 25, lp: 75 },
  { tier: 'Hurdle #6 (>30%)', gp: 50, lp: 50 },
];

export default function PartnershipTab({ projectId }: { projectId: number | string }) {
  const [tab, setTab] = useState('Summary');
  const { toast } = useToast();
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const isKimptonDemo = projectId === 7;
  const { outputs, previous } = useEngineOutputs(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  // Worker partnership engine fields. Note: schema is `gp.irr` / `lp.irr`, not
  // `gp_irr`. We support both shapes for forward compatibility.
  type PartnerReturn = {
    partner: string;
    contributed_equity: number;
    distributions: number;
    irr: number;
    equity_multiple: number;
  };
  const wGp = getEngineField<PartnerReturn>(outputs, 'partnership', 'gp');
  const wLp = getEngineField<PartnerReturn>(outputs, 'partnership', 'lp');
  const wPromote = getEngineField<number>(outputs, 'partnership', 'promote_amount');
  const wGpFlows = getEngineField<number[]>(outputs, 'partnership', 'gp_cash_flows');
  const wLpFlows = getEngineField<number[]>(outputs, 'partnership', 'lp_cash_flows');

  const wGpIrr = wGp?.irr;
  const wLpIrr = wLp?.irr;
  const wGpEquity = wGp?.contributed_equity;
  const wLpEquity = wLp?.contributed_equity;
  const wGpMultiple = wGp?.equity_multiple;
  const wLpMultiple = wLp?.equity_multiple;
  const wGpDist = wGp?.distributions;
  const wLpDist = wLp?.distributions;

  const hasWorkerPartnership = wGp != null || wLp != null;
  const gpIrrLabel = wGpIrr != null
    ? fmtPct(wGpIrr, 2)
    : (isKimptonDemo ? '42.18%' : '—');
  const lpIrrLabel = wLpIrr != null
    ? fmtPct(wLpIrr, 2)
    : (isKimptonDemo ? '20.45%' : '—');
  const promoteLabel = wPromote != null
    ? fmtCurrency(wPromote, { compact: true })
    : (isKimptonDemo ? fmtCurrency(2_840_000, { compact: true }) : '—');

  // Total deal profit = total cash returned to all equity - equity contributed.
  const totalDistributions = (wGpDist ?? 0) + (wLpDist ?? 0);
  const totalEquity = (wGpEquity ?? 0) + (wLpEquity ?? 0);
  const dealProfitLabel = hasWorkerPartnership
    ? fmtCurrency(Math.max(0, totalDistributions - totalEquity), { compact: true })
    : (isKimptonDemo ? fmtCurrency(22_120_000, { compact: true }) : '—');

  if (!isKimptonDemo && !hasWorkerPartnership) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          <IntroCard
            dismissKey="partnership-intro"
            title="The Partnership Engine"
            body={
              <>
                How the deal&apos;s profits split between the sponsor (you, the
                <span className="font-semibold"> GP</span>) and outside investors
                (<span className="font-semibold">LPs</span>). The waterfall pays LPs their preferred
                return first, then promotes the GP on the upside.
              </>
            }
          />
          <EngineHeader
            name="Partnership Engine"
            desc="Models GP/LP waterfall structures, promote calculations, and investor distributions."
            outputs={['GP IRR', 'LP IRR', 'GP Promote', '+1']}
            dependsOn="Returns"
            dealId={dealId}
            engineName="partnership"
            onRunStart={() => setComputing(true)}
            onRunComplete={() => {
              setComputing(false);
              setRunToken(Date.now());
            }}
          />
          <EngineLegend />
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <Users size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">Partnership Engine unavailable</h3>
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              The waterfall splits depend on total deal returns, so this engine waits for
              <span className="font-medium"> Returns</span> to finish. Run the model from the Returns
              tab to populate GP/LP splits.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => toast('Engine queued — check back shortly', { type: 'info' })}
            >
              Run Partnership Engine
            </Button>
          </Card>
          <EngineRunHistory dealId={dealId} />
        </div>
        <EngineRightRail />
      </div>
    );
  }

  return (
    <div className="flex gap-4">
      <div className="flex-1 min-w-0">
      <IntroCard
        dismissKey="partnership-intro"
        title="The Partnership Engine"
        body={
          <>
            How the deal&apos;s profits split between the sponsor (you, the
            <span className="font-semibold"> GP</span>) and outside investors
            (<span className="font-semibold">LPs</span>). The waterfall pays LPs their preferred
            return first, then promotes the GP on the upside.
          </>
        }
      />
      <EngineHeader
        name="Partnership Engine"
        desc="Models GP/LP waterfall structures, promote calculations, and investor distributions."
        outputs={['GP IRR', 'LP IRR', 'GP Promote', '+1']}
        dependsOn="Returns"
        complete
        dealId={dealId}
        engineName="partnership"
        runMode="all"
        onRunStart={() => setComputing(true)}
        onRunComplete={() => {
          setComputing(false);
          setRunToken(Date.now());
        }}
      />

      <WhatJustHappened
        engine="partnership"
        engineLabel="Partnership"
        outputs={outputs}
        previous={previous}
        runToken={runToken}
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
        <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
          <div className="grid grid-cols-4 gap-4 mb-5">
            <KPI label="GP LIRR (Net to Sponsor)" tip="The General Partner's (sponsor's) levered IRR after the promote — what you take home for putting the deal together." value={gpIrrLabel} flashKey={gpIrrLabel} />
            <KPI label="LP LIRR (Net to Investors)" tip="The Limited Partners' (outside investors') levered IRR after waterfall splits. What your LPs actually earn." value={lpIrrLabel} flashKey={lpIrrLabel} />
            <KPI label="GP Profit (Carry)" tip={GLOSSARY['Promote']} value={promoteLabel} flashKey={promoteLabel} />
            <KPI label="Deal Profit (Levered)" tip="Total cash to all equity holders over the hold, minus equity invested. The pie that gets split GP/LP." value={dealProfitLabel} flashKey={dealProfitLabel} />
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
                  {(() => {
                    const gpEq = wGpEquity ?? (isKimptonDemo ? 1_388_960 : 0);
                    const lpEq = wLpEquity ?? (isKimptonDemo ? 12_447_110 : 0);
                    const total = gpEq + lpEq;
                    const gpPct = total > 0 ? (gpEq / total) * 100 : 0;
                    const lpPct = total > 0 ? (lpEq / total) * 100 : 0;
                    return (
                      <>
                        <tr className="border-b border-border/50">
                          <td className="py-2">Sponsor / GP (General Partner)</td>
                          <td className="text-right tabular-nums">{gpPct.toFixed(1)}%</td>
                          <td className="text-right tabular-nums">{fmtCurrency(gpEq)}</td>
                        </tr>
                        <tr className="border-b border-border/50">
                          <td className="py-2">LP Investors (Limited Partners)</td>
                          <td className="text-right tabular-nums">{lpPct.toFixed(1)}%</td>
                          <td className="text-right tabular-nums">{fmtCurrency(lpEq)}</td>
                        </tr>
                        <tr className="font-semibold border-t border-border">
                          <td className="py-2">Total Equity</td>
                          <td className="text-right tabular-nums">100.0%</td>
                          <td className="text-right tabular-nums">{fmtCurrency(total)}</td>
                        </tr>
                      </>
                    );
                  })()}
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
                  {(() => {
                    // Profit = distributions - contributed equity for each partner.
                    const gpProfit = (wGpDist ?? 0) - (wGpEquity ?? 0);
                    const lpProfit = (wLpDist ?? 0) - (wLpEquity ?? 0);
                    const gpMultiple = wGpMultiple ?? (isKimptonDemo ? 3.04 : 0);
                    const lpMultiple = wLpMultiple ?? (isKimptonDemo ? 2.02 : 0);
                    const gpProfitLabel = hasWorkerPartnership
                      ? fmtCurrency(Math.max(0, gpProfit))
                      : (isKimptonDemo ? fmtCurrency(2_840_000) : '—');
                    const lpProfitLabel = hasWorkerPartnership
                      ? fmtCurrency(Math.max(0, lpProfit))
                      : (isKimptonDemo ? fmtCurrency(19_280_000) : '—');
                    return (
                      <>
                        <tr className="border-b border-border/50">
                          <td className="py-2">GP / Sponsor</td>
                          <td className="text-right tabular-nums">{gpIrrLabel}</td>
                          <td className="text-right tabular-nums">{gpMultiple.toFixed(2)}x</td>
                          <td className="text-right tabular-nums">{gpProfitLabel}</td>
                        </tr>
                        <tr>
                          <td className="py-2">LP / Investors</td>
                          <td className="text-right tabular-nums">{lpIrrLabel}</td>
                          <td className="text-right tabular-nums">{lpMultiple.toFixed(2)}x</td>
                          <td className="text-right tabular-nums">{lpProfitLabel}</td>
                        </tr>
                      </>
                    );
                  })()}
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
          {computing && (
            <div className="absolute inset-0 bg-bg/60 backdrop-blur-[1px] flex items-start justify-center pt-12 rounded-md">
              <span className="inline-flex items-center gap-2 px-3 py-1.5 bg-white border border-border rounded-md shadow-card text-[12.5px] font-medium text-ink-700">
                <span className="w-1.5 h-1.5 rounded-full bg-brand-500 animate-pulse" />
                Recomputing…
              </span>
            </div>
          )}
        </div>
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
          {(() => {
            // Worker GP/LP cash flows are the source of truth. Final element is the exit year.
            const useWorker = Array.isArray(wGpFlows) && Array.isArray(wLpFlows)
              && wGpFlows.length > 0 && wGpFlows.length === wLpFlows.length;
            const rows: Array<[string, number, number, number]> = useWorker
              ? wGpFlows!.map((gp, i) => {
                  const lp = wLpFlows![i] ?? 0;
                  const yearLabel = i === wGpFlows!.length - 1
                    ? `Year ${i + 1} (Exit)`
                    : `Year ${i + 1}`;
                  return [yearLabel, gp, lp, gp + lp];
                })
              : isKimptonDemo
                ? [
                    ['Year 1', 0, 309_500, 309_500],
                    ['Year 2', 0, 345_600, 345_600],
                    ['Year 3', 0, 384_200, 384_200],
                    ['Year 4', 92_000, 425_300, 517_300],
                    ['Year 5 (Exit)', 2_748_000, 17_815_400, 20_563_400],
                  ]
                : [];
            if (rows.length === 0) {
              return (
                <div className="py-6 text-center text-[12px] text-ink-500">
                  Run the Partnership engine to populate annual distributions.
                </div>
              );
            }
            return (
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
                  {rows.map(([y, gp, lp, t]) => (
                    <DistRow key={y} y={y} gp={gp} lp={lp} t={t} />
                  ))}
                </tbody>
              </table>
            );
          })()}
        </Card>
      )}

      {tab === 'Returns Summary' && (
        <div className="grid grid-cols-2 gap-5">
          <Card className="p-5">
            <h3 className="text-[13px] font-semibold text-ink-900 mb-3">GP Returns</h3>
            <div className="space-y-2 text-[12.5px]">
              <Row k="LIRR" v={gpIrrLabel} />
              <Row k="Equity Multiple" v={`${(wGpMultiple ?? (isKimptonDemo ? 3.04 : 0)).toFixed(2)}x`} />
              <Row k="Promote" v={promoteLabel} />
              <Row k="Total Distributions" v={
                wGpDist != null
                  ? fmtCurrency(wGpDist)
                  : (isKimptonDemo ? fmtCurrency(2_840_000) : '—')
              } />
            </div>
          </Card>
          <Card className="p-5">
            <h3 className="text-[13px] font-semibold text-ink-900 mb-3">LP Returns</h3>
            <div className="space-y-2 text-[12.5px]">
              <Row k="LIRR" v={lpIrrLabel} />
              <Row k="Equity Multiple" v={`${(wLpMultiple ?? (isKimptonDemo ? 2.02 : 0)).toFixed(2)}x`} />
              <Row k="Pref Met" v={hasWorkerPartnership ? (wLpIrr != null && wLpIrr >= 0.10 ? 'Yes' : 'No') : (isKimptonDemo ? 'Yes' : '—')} />
              <Row k="Total Distributions" v={
                wLpDist != null
                  ? fmtCurrency(wLpDist)
                  : (isKimptonDemo ? fmtCurrency(19_280_000) : '—')
              } />
            </div>
          </Card>
        </div>
      )}
      <EngineRunHistory dealId={dealId} seedDemo />
      </div>
      <EngineRightRail />
    </div>
  );
}

function KPI({ label, value, flashKey, tip }: { label: string; value: string; flashKey?: unknown; tip?: string }) {
  const flash = useFlash(flashKey ?? value);
  return (
    <Card className={cn('p-4', flash && 'value-flash')}>
      <div className="text-[10.5px] text-ink-500 uppercase tracking-wide">
        {tip ? <MetricLabel label={label} tip={tip} /> : label}
      </div>
      <div className="text-[20px] font-semibold tabular-nums mt-1 text-ink-900">{value}</div>
    </Card>
  );
}
function DistRow({ y, gp, lp, t }: { y: string; gp: number; lp: number; t: number }) {
  const flash = useFlash(t);
  return (
    <tr className={cn('border-b border-border/50', flash && 'value-flash')}>
      <td className="py-2 font-medium">{y}</td>
      <td className="text-right tabular-nums">{fmtCurrency(gp)}</td>
      <td className="text-right tabular-nums">{fmtCurrency(lp)}</td>
      <td className="text-right tabular-nums font-medium">{fmtCurrency(t)}</td>
    </tr>
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
