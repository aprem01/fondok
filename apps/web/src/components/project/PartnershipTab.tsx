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

// InvestmentTab-style display helpers.
const pickNum = (
  worker: number | undefined,
  fixture: number,
  isKimptonDemo: boolean,
): number | undefined => (worker != null ? worker : (isKimptonDemo ? fixture : undefined));
const fmtOrDash = (
  n: number | undefined,
  formatter: (v: number) => string,
): string => (n != null ? formatter(n) : '—');

// Default Kimpton-fixture waterfall — only rendered for the demo deal.
const kimptonWaterfall = [
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

  // Worker partnership engine fields. The runtime engine returns nested
  // `gp` / `lp` PartnerReturn objects; the export schema flattens them as
  // `gp_equity_usd` / `lp_irr` / etc. We accept both shapes — whichever the
  // worker produced for this deal.
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

  // Flat-schema reads (export-style fixtures: lp_equity_usd, gp_irr, ...).
  const wGpEquityFlat = getEngineField<number>(outputs, 'partnership', 'gp_equity_usd');
  const wLpEquityFlat = getEngineField<number>(outputs, 'partnership', 'lp_equity_usd');
  const wTotalEquityFlat = getEngineField<number>(outputs, 'partnership', 'total_equity_usd');
  const wLpPrefPct = getEngineField<number>(outputs, 'partnership', 'lp_pref_pct');
  const wTier1Pct = getEngineField<number>(outputs, 'partnership', 'gp_promote_tier_1_pct');
  const wTier1Hurdle = getEngineField<number>(outputs, 'partnership', 'gp_promote_tier_1_irr_hurdle');
  const wTier2Pct = getEngineField<number>(outputs, 'partnership', 'gp_promote_tier_2_pct');
  const wTier2Hurdle = getEngineField<number>(outputs, 'partnership', 'gp_promote_tier_2_irr_hurdle');
  const wGpIrrFlat = getEngineField<number>(outputs, 'partnership', 'gp_irr')
    ?? getEngineField<number>(outputs, 'partnership', 'gp_irr_after_promote');
  const wLpIrrFlat = getEngineField<number>(outputs, 'partnership', 'lp_irr')
    ?? getEngineField<number>(outputs, 'partnership', 'lp_irr_after_promote');
  const wGpMultipleFlat = getEngineField<number>(outputs, 'partnership', 'gp_multiple')
    ?? getEngineField<number>(outputs, 'partnership', 'gp_equity_multiple');
  const wLpMultipleFlat = getEngineField<number>(outputs, 'partnership', 'lp_multiple')
    ?? getEngineField<number>(outputs, 'partnership', 'lp_equity_multiple');

  const wGpIrr = wGp?.irr ?? wGpIrrFlat;
  const wLpIrr = wLp?.irr ?? wLpIrrFlat;
  const wGpEquity = wGp?.contributed_equity ?? wGpEquityFlat;
  const wLpEquity = wLp?.contributed_equity ?? wLpEquityFlat;
  const wGpMultiple = wGp?.equity_multiple ?? wGpMultipleFlat;
  const wLpMultiple = wLp?.equity_multiple ?? wLpMultipleFlat;
  const wGpDist = wGp?.distributions;
  const wLpDist = wLp?.distributions;

  const hasWorkerPartnership = wGp != null || wLp != null
    || wGpEquityFlat != null || wLpEquityFlat != null
    || wGpIrrFlat != null || wLpIrrFlat != null;
  // 0.4218 / 0.2045 are the Kimpton-fixture demo values; only rendered for
  // the demo deal. fmtOrDash handles the '—' fallback for other deals.
  const gpIrrPick = pickNum(wGpIrr, 0.4218, isKimptonDemo);
  const lpIrrPick = pickNum(wLpIrr, 0.2045, isKimptonDemo);
  const promotePick = pickNum(wPromote, 2_840_000, isKimptonDemo);
  const gpIrrLabel = fmtOrDash(gpIrrPick, v => fmtPct(v, 2));
  const lpIrrLabel = fmtOrDash(lpIrrPick, v => fmtPct(v, 2));
  const promoteLabel = fmtOrDash(promotePick, v => fmtCurrency(v, { compact: true }));

  // Total deal profit = total cash returned to all equity - equity contributed.
  // We can compute it when both distributions and equity are present from the
  // engine; otherwise fall back to the Kimpton fixture for the demo only.
  const totalDistributions = (wGpDist ?? 0) + (wLpDist ?? 0);
  const totalEquityRuntime = (wGpEquity ?? 0) + (wLpEquity ?? 0);
  const totalEquity = wTotalEquityFlat ?? totalEquityRuntime;
  const canComputeDealProfit = wGpDist != null && wLpDist != null
    && (wGpEquity != null || wLpEquity != null || wTotalEquityFlat != null);
  const dealProfitPick = canComputeDealProfit
    ? Math.max(0, totalDistributions - totalEquity)
    : (isKimptonDemo ? 22_120_000 : undefined);
  const dealProfitLabel = fmtOrDash(dealProfitPick, v => fmtCurrency(v, { compact: true }));

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
                    const gpEqPick = pickNum(wGpEquity, 1_388_960, isKimptonDemo);
                    const lpEqPick = pickNum(wLpEquity, 12_447_110, isKimptonDemo);
                    const totalPick = (gpEqPick != null && lpEqPick != null)
                      ? gpEqPick + lpEqPick
                      : pickNum(wTotalEquityFlat, 13_836_070, isKimptonDemo);
                    const gpPctStr = (gpEqPick != null && totalPick && totalPick > 0)
                      ? `${((gpEqPick / totalPick) * 100).toFixed(1)}%`
                      : '—';
                    const lpPctStr = (lpEqPick != null && totalPick && totalPick > 0)
                      ? `${((lpEqPick / totalPick) * 100).toFixed(1)}%`
                      : '—';
                    return (
                      <>
                        <tr className="border-b border-border/50">
                          <td className="py-2">Sponsor / GP (General Partner)</td>
                          <td className="text-right tabular-nums">{gpPctStr}</td>
                          <td className="text-right tabular-nums">{fmtOrDash(gpEqPick, fmtCurrency)}</td>
                        </tr>
                        <tr className="border-b border-border/50">
                          <td className="py-2">LP Investors (Limited Partners)</td>
                          <td className="text-right tabular-nums">{lpPctStr}</td>
                          <td className="text-right tabular-nums">{fmtOrDash(lpEqPick, fmtCurrency)}</td>
                        </tr>
                        <tr className="font-semibold border-t border-border">
                          <td className="py-2">Total Equity</td>
                          <td className="text-right tabular-nums">{totalPick != null ? '100.0%' : '—'}</td>
                          <td className="text-right tabular-nums">{fmtOrDash(totalPick, fmtCurrency)}</td>
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
                    const gpProfitNum = (wGpDist != null && wGpEquity != null)
                      ? Math.max(0, wGpDist - wGpEquity)
                      : undefined;
                    const lpProfitNum = (wLpDist != null && wLpEquity != null)
                      ? Math.max(0, wLpDist - wLpEquity)
                      : undefined;
                    const gpProfitPick = pickNum(gpProfitNum, 2_840_000, isKimptonDemo);
                    const lpProfitPick = pickNum(lpProfitNum, 19_280_000, isKimptonDemo);
                    const gpMultiplePick = pickNum(wGpMultiple, 3.04, isKimptonDemo);
                    const lpMultiplePick = pickNum(wLpMultiple, 2.02, isKimptonDemo);
                    return (
                      <>
                        <tr className="border-b border-border/50">
                          <td className="py-2">GP / Sponsor</td>
                          <td className="text-right tabular-nums">{gpIrrLabel}</td>
                          <td className="text-right tabular-nums">{fmtOrDash(gpMultiplePick, v => `${v.toFixed(2)}x`)}</td>
                          <td className="text-right tabular-nums">{fmtOrDash(gpProfitPick, fmtCurrency)}</td>
                        </tr>
                        <tr>
                          <td className="py-2">LP / Investors</td>
                          <td className="text-right tabular-nums">{lpIrrLabel}</td>
                          <td className="text-right tabular-nums">{fmtOrDash(lpMultiplePick, v => `${v.toFixed(2)}x`)}</td>
                          <td className="text-right tabular-nums">{fmtOrDash(lpProfitPick, fmtCurrency)}</td>
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
            {(() => {
              // Build the waterfall tier table from worker fields when the
              // export-style schema is present; fall back to the Kimpton
              // fixture only on the demo deal so non-Kimpton deals render '—'
              // instead of inheriting the demo's promote tiers.
              type Tier = { tier: string; gp: number; lp: number };
              const workerTiers: Tier[] | null = (() => {
                const tiers: Tier[] = [];
                if (wLpPrefPct != null) {
                  tiers.push({ tier: `Pref Return (${(wLpPrefPct * 100).toFixed(0)}%)`, gp: 0, lp: 100 });
                }
                if (wTier1Pct != null && wTier1Hurdle != null) {
                  tiers.push({
                    tier: `Tier 1 — Promote above ${(wTier1Hurdle * 100).toFixed(0)}% LP IRR`,
                    gp: Math.round(wTier1Pct * 100),
                    lp: Math.round((1 - wTier1Pct) * 100),
                  });
                }
                if (wTier2Pct != null && wTier2Hurdle != null) {
                  tiers.push({
                    tier: `Tier 2 — Promote above ${(wTier2Hurdle * 100).toFixed(0)}% LP IRR`,
                    gp: Math.round(wTier2Pct * 100),
                    lp: Math.round((1 - wTier2Pct) * 100),
                  });
                }
                return tiers.length > 0 ? tiers : null;
              })();
              const rows = workerTiers ?? (isKimptonDemo ? kimptonWaterfall : null);
              if (!rows) {
                return (
                  <div className="py-6 text-center text-[12px] text-ink-500">
                    Waterfall tiers will populate once the Partnership engine emits tier splits.
                  </div>
                );
              }
              return (
                <table className="w-full text-[12.5px]">
                  <thead>
                    <tr className="text-ink-500 text-[11px] border-b border-border">
                      <th className="text-left font-medium pb-2">Tier</th>
                      <th className="text-right font-medium pb-2">GP Cash Flow</th>
                      <th className="text-right font-medium pb-2">LP Cash Flow</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(w => (
                      <tr key={w.tier} className="border-b border-border/50">
                        <td className="py-2">{w.tier}</td>
                        <td className="text-right tabular-nums">{w.gp}%</td>
                        <td className="text-right tabular-nums">{w.lp}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              );
            })()}
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
          {(() => {
            // Worker is preferred; fall back to Kimpton fixture only on demo.
            const gpEqPick = pickNum(wGpEquity, 1_388_960, isKimptonDemo);
            const lpEqPick = pickNum(wLpEquity, 12_447_110, isKimptonDemo);
            const total = (gpEqPick != null && lpEqPick != null)
              ? gpEqPick + lpEqPick
              : pickNum(wTotalEquityFlat, 13_836_070, isKimptonDemo);
            const gpPct = (gpEqPick != null && total && total > 0)
              ? `${((gpEqPick / total) * 100).toFixed(0)}%`
              : '—';
            const lpPct = (lpEqPick != null && total && total > 0)
              ? `${((lpEqPick / total) * 100).toFixed(0)}%`
              : '—';
            const fields: Array<[string, string]> = [
              ['Sponsor / GP %', gpPct],
              ['LP Investor %', lpPct],
              ['GP Amount', fmtOrDash(gpEqPick, fmtCurrency)],
              ['LP Amount', fmtOrDash(lpEqPick, fmtCurrency)],
            ];
            return (
              <div className="grid grid-cols-2 gap-5">
                {fields.map(([k, v]) => (
                  <div key={k}>
                    <label className="block text-[11.5px] text-ink-500 mb-1">{k}</label>
                    <input value={v} readOnly className="w-full px-3 py-2 text-[13px] border border-border rounded-md bg-ink-300/10" />
                  </div>
                ))}
              </div>
            );
          })()}
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

      {tab === 'Returns Summary' && (() => {
        const gpMultiplePick = pickNum(wGpMultiple, 3.04, isKimptonDemo);
        const lpMultiplePick = pickNum(wLpMultiple, 2.02, isKimptonDemo);
        const gpDistPick = pickNum(wGpDist, 2_840_000, isKimptonDemo);
        const lpDistPick = pickNum(wLpDist, 19_280_000, isKimptonDemo);
        const prefMet = wLpIrr != null
          ? (wLpIrr >= 0.10 ? 'Yes' : 'No')
          : (isKimptonDemo ? 'Yes' : '—');
        return (
          <div className="grid grid-cols-2 gap-5">
            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-3">GP Returns</h3>
              <div className="space-y-2 text-[12.5px]">
                <Row k="LIRR" v={gpIrrLabel} />
                <Row k="Equity Multiple" v={fmtOrDash(gpMultiplePick, v => `${v.toFixed(2)}x`)} />
                <Row k="Promote" v={promoteLabel} />
                <Row k="Total Distributions" v={fmtOrDash(gpDistPick, fmtCurrency)} />
              </div>
            </Card>
            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-3">LP Returns</h3>
              <div className="space-y-2 text-[12.5px]">
                <Row k="LIRR" v={lpIrrLabel} />
                <Row k="Equity Multiple" v={fmtOrDash(lpMultiplePick, v => `${v.toFixed(2)}x`)} />
                <Row k="Pref Met" v={prefMet} />
                <Row k="Total Distributions" v={fmtOrDash(lpDistPick, fmtCurrency)} />
              </div>
            </Card>
          </div>
        );
      })()}
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
