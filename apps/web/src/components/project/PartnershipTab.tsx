'use client';
import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams } from 'next/navigation';
import { Users } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import { api, isWorkerConnected } from '@/lib/api';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useDeal } from '@/lib/hooks/useDeal';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
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
): number | undefined => (worker != null ? worker : undefined);
const fmtOrDash = (
  n: number | undefined,
  formatter: (v: number) => string,
): string => (n != null ? formatter(n) : '—');

// FON-66 — the promote waterfall seed. This MIRRORS the worker's
// `_KIMPTON_WATERFALL_REFERENCE` (engine_runner.py): the deal-agnostic
// institutional benchmark an analyst edits from the Waterfall Structure
// sub-tab. Each tier's editable fields persist as indexed field_overrides
// (`partnership.waterfall.<idx>.<field>`) the worker layers over this
// seed. `hurdle`/`gp` are fractions; LP split derives as `1 - gp`.
const WATERFALL_SEED: Array<{ label: string; hurdle: number; gp: number }> = [
  { label: 'Preferred (to 10%)', hurdle: 0.10, gp: 0.00 },
  { label: 'Tier 2 (to 15%)', hurdle: 0.15, gp: 0.20 },
  { label: 'Tier 3 (to 20%)', hurdle: 0.20, gp: 0.25 },
  { label: 'Tier 4 (to 25%)', hurdle: 0.25, gp: 0.25 },
  { label: 'Tier 5 (to 30%)', hurdle: 0.30, gp: 0.25 },
  { label: 'Tier 6 (>30%)', hurdle: 0.50, gp: 0.50 },
];

// Read a scalar override, tolerating both the flat scalar and the
// structured `{ value, note }` shape the override panel writes.
function readOverrideNum(
  overrides: Record<string, unknown>,
  path: string,
  fallback: number,
): number {
  const raw = overrides[path];
  const val = raw && typeof raw === 'object' && 'value' in raw
    ? (raw as { value: unknown }).value
    : raw;
  if (val == null || val === '') return fallback;
  const n = typeof val === 'number' ? val : Number(val);
  return Number.isFinite(n) ? n : fallback;
}

// Resolve the live promote structure = seed + any analyst tier overrides.
// Returns display rows { tier, gp, lp } as whole-percent numbers.
function resolveWaterfall(
  overrides: Record<string, unknown>,
): Array<{ tier: string; gp: number; lp: number }> {
  return WATERFALL_SEED.map((t, idx) => {
    const gp = readOverrideNum(overrides, `partnership.waterfall.${idx}.gp_split`, t.gp);
    return { tier: t.label, gp: Math.round(gp * 100), lp: Math.round((1 - gp) * 100) };
  });
}

export default function PartnershipTab() {
  const [tab, setTab] = useState('Summary');
  const { toast } = useToast();
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs, previous } = useEngineOutputs(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  // ─── FON-66: editable waterfall assumptions ────────────────────────
  // Live deals (real UUID + worker connected) can edit ownership,
  // preferred return, and the promote tiers. Edits PATCH the deal's
  // field_overrides and kick a debounced run-all so GP/LP outputs
  // re-derive. Demo (id 7) and mock numeric deals stay read-only.
  const isMockId = /^\d+$/.test(dealId);
  const liveMode = isWorkerConnected() && !isMockId;
  const { deal, refresh: refreshDeal } = useDeal(dealId);
  const [overrides, setOverrides] = useState<Record<string, unknown>>({});
  useEffect(() => {
    setOverrides((deal?.field_overrides as Record<string, unknown> | undefined) ?? {});
  }, [deal?.field_overrides]);
  const fullRun = useEngineRun(liveMode ? dealId : '', 'returns', { runMode: 'all' });
  const rerunTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (rerunTimerRef.current) clearTimeout(rerunTimerRef.current);
  }, []);

  // Persist one or more overrides in a single PATCH. Complementary fields
  // (GP/LP ownership, GP/LP tier split) are saved together so the engine
  // never sees an inconsistent pair. A null value clears that override.
  const onSaveOverride = useCallback(
    async (patch: Record<string, number | null>) => {
      if (!liveMode) {
        toast('Editing is disabled on demo deals', { type: 'info' });
        return;
      }
      const next = { ...overrides };
      for (const [path, value] of Object.entries(patch)) {
        if (value === null) delete next[path];
        else next[path] = value;
      }
      setOverrides(next); // optimistic
      try {
        await api.deals.update(dealId, { field_overrides: next });
        toast('Saved — re-running engines', { type: 'success' });
        void refreshDeal?.();
        if (rerunTimerRef.current) clearTimeout(rerunTimerRef.current);
        rerunTimerRef.current = setTimeout(() => {
          void fullRun.run();
        }, 1500);
      } catch (err) {
        setOverrides(overrides); // rollback
        const msg = err instanceof Error ? err.message : 'Unknown error';
        toast(`Save failed: ${msg}`, { type: 'error' });
      }
    },
    [overrides, dealId, liveMode, toast, refreshDeal, fullRun],
  );

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
  const gpIrrPick = pickNum(wGpIrr);
  const lpIrrPick = pickNum(wLpIrr);
  const promotePick = pickNum(wPromote);
  const gpIrrLabel = fmtOrDash(gpIrrPick, v => fmtPct(v, 2));
  const lpIrrLabel = fmtOrDash(lpIrrPick, v => fmtPct(v, 2));
  const promoteLabel = fmtOrDash(promotePick, v => fmtCurrency(v, { compact: true }));

  // Total deal profit = total cash returned to all equity - equity contributed.
  // We can compute it when both distributions and equity are present from the
  // engine; otherwise it is undefined and renders as '—'.
  const totalDistributions = (wGpDist ?? 0) + (wLpDist ?? 0);
  const totalEquityRuntime = (wGpEquity ?? 0) + (wLpEquity ?? 0);
  const totalEquity = wTotalEquityFlat ?? totalEquityRuntime;
  const canComputeDealProfit = wGpDist != null && wLpDist != null
    && (wGpEquity != null || wLpEquity != null || wTotalEquityFlat != null);
  const dealProfitPick = canComputeDealProfit
    ? Math.max(0, totalDistributions - totalEquity)
    : undefined;
  const dealProfitLabel = fmtOrDash(dealProfitPick, v => fmtCurrency(v, { compact: true }));

  if (!hasWorkerPartnership) {
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
                    const gpEqPick = pickNum(wGpEquity);
                    const lpEqPick = pickNum(wLpEquity);
                    const totalPick = (gpEqPick != null && lpEqPick != null)
                      ? gpEqPick + lpEqPick
                      : pickNum(wTotalEquityFlat);
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
                    const gpProfitPick = pickNum(gpProfitNum);
                    const lpProfitPick = pickNum(lpProfitNum);
                    const gpMultiplePick = pickNum(wGpMultiple);
                    const lpMultiplePick = pickNum(wLpMultiple);
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
              // export-style schema is present.
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
              // Prefer worker-emitted tier fields; otherwise show the live
              // promote structure (benchmark seed + analyst overrides) so the
              // waterfall is never empty on a real deal.
              const rows = workerTiers ?? resolveWaterfall(overrides);
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
        <div className="space-y-5">
          {/* Ownership & preferred return — editable assumptions */}
          <Card className="p-5">
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-[13px] font-semibold text-ink-900">Ownership &amp; Preferred Return</h3>
              <span className={cn('text-[11px]', liveMode ? 'text-ink-500' : 'text-ink-400')}>
                {liveMode ? 'Editable · changes re-run the model' : 'Read-only on demo deals'}
              </span>
            </div>
            <p className="text-[11.5px] text-ink-500 mb-4">
              The equity split between sponsor and investors, and the LP preferred return paid before any promote.
              LP ownership derives from GP so the two always total 100%.
            </p>
            {(() => {
              const gpOwn = readOverrideNum(overrides, 'gp_equity_pct', 0.10);
              const lpOwn = readOverrideNum(overrides, 'lp_equity_pct', 0.90);
              const gpEqPick = pickNum(wGpEquity);
              const lpEqPick = pickNum(wLpEquity);
              return (
                <div className="grid grid-cols-3 gap-5">
                  <PctField
                    label="GP / Sponsor Ownership"
                    valueFraction={gpOwn}
                    overridden={'gp_equity_pct' in overrides}
                    liveMode={liveMode}
                    onCommit={(f) => onSaveOverride({ gp_equity_pct: f, lp_equity_pct: round6(1 - f) })}
                    sub={fmtOrDash(gpEqPick, v => fmtCurrency(v, { compact: true }))}
                  />
                  <div>
                    <label className="block text-[11.5px] text-ink-500 mb-1">LP Investor Ownership</label>
                    <div className="w-full px-3 py-2 text-[13px] border border-transparent rounded-md bg-ink-300/10 tabular-nums text-ink-600">
                      {(lpOwn * 100).toFixed(0)}%
                    </div>
                    <div className="text-[10.5px] text-ink-400 mt-1">
                      {fmtOrDash(lpEqPick, v => fmtCurrency(v, { compact: true }))} · derived
                    </div>
                  </div>
                  <PctField
                    label="Preferred Return"
                    valueFraction={readOverrideNum(overrides, 'pref_rate', 0.10)}
                    overridden={'pref_rate' in overrides}
                    liveMode={liveMode}
                    onCommit={(f) => onSaveOverride({ pref_rate: f })}
                    sub="LP hurdle before promote"
                  />
                </div>
              );
            })()}
          </Card>

          {/* Promote waterfall — editable hurdles + GP split per tier */}
          <Card className="p-5">
            <div className="flex items-center justify-between mb-1">
              <h3 className="text-[13px] font-semibold text-ink-900">Promote Waterfall</h3>
              <span className={cn('text-[11px]', liveMode ? 'text-ink-500' : 'text-ink-400')}>
                {liveMode ? 'Editable · LP split derives from GP' : 'Read-only on demo deals'}
              </span>
            </div>
            <p className="text-[11.5px] text-ink-500 mb-4">
              Each tier splits residual cash once cumulative LP IRR clears its hurdle. Seeded from the
              institutional benchmark — edit any hurdle or GP split to model a different promote.
            </p>
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-ink-500 text-[11px] border-b border-border">
                  <th className="text-left font-medium pb-2">Tier</th>
                  <th className="text-right font-medium pb-2 w-36">IRR Hurdle</th>
                  <th className="text-right font-medium pb-2 w-36">GP Split</th>
                  <th className="text-right font-medium pb-2 w-24">LP Split</th>
                </tr>
              </thead>
              <tbody>
                {WATERFALL_SEED.map((t, idx) => {
                  const gpPath = `partnership.waterfall.${idx}.gp_split`;
                  const hurdlePath = `partnership.waterfall.${idx}.hurdle_rate`;
                  const gpFrac = readOverrideNum(overrides, gpPath, t.gp);
                  const hurdleFrac = readOverrideNum(overrides, hurdlePath, t.hurdle);
                  return (
                    <tr key={t.label} className="border-b border-border/50">
                      <td className="py-2 text-ink-800">{t.label}</td>
                      <td className="py-1 text-right">
                        <CellPct
                          valueFraction={hurdleFrac}
                          overridden={hurdlePath in overrides}
                          liveMode={liveMode}
                          onCommit={(f) => onSaveOverride({ [hurdlePath]: f })}
                        />
                      </td>
                      <td className="py-1 text-right">
                        <CellPct
                          valueFraction={gpFrac}
                          overridden={gpPath in overrides}
                          liveMode={liveMode}
                          onCommit={(f) => onSaveOverride({ [gpPath]: f, [`partnership.waterfall.${idx}.lp_split`]: round6(1 - f) })}
                        />
                      </td>
                      <td className="py-2 text-right tabular-nums text-ink-500">
                        {Math.round((1 - gpFrac) * 100)}%
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {!liveMode && (
              <div className="mt-4 text-[11.5px] text-ink-400">
                Waterfall editing is available on live deals. The demo shows the benchmark structure.
              </div>
            )}
          </Card>
        </div>
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
        const gpMultiplePick = pickNum(wGpMultiple);
        const lpMultiplePick = pickNum(wLpMultiple);
        const gpDistPick = pickNum(wGpDist);
        const lpDistPick = pickNum(wLpDist);
        const prefMet = wLpIrr != null
          ? (wLpIrr >= 0.10 ? 'Yes' : 'No')
          : '—';
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

function round6(n: number): number {
  return Math.round(n * 1e6) / 1e6;
}

// Labeled editable percent field — displays whole-percent, saves a fraction.
function PctField({
  label, valueFraction, overridden, liveMode, onCommit, sub,
}: {
  label: string;
  valueFraction: number;
  overridden: boolean;
  liveMode: boolean;
  onCommit: (fraction: number) => void;
  sub?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (valueFraction * 100).toFixed(0);
  const commit = () => {
    if (draft === null) return;
    const t = draft.trim();
    setDraft(null);
    if (t === '') return;
    const pct = Number(t);
    if (Number.isFinite(pct)) onCommit(round6(pct / 100));
  };
  return (
    <div>
      <label className="block text-[11.5px] text-ink-500 mb-1">
        {label}
        {overridden && (
          <span className="ml-1.5 text-[10px] text-blue-600" title="Analyst override">• edited</span>
        )}
      </label>
      <div className={cn(
        'flex items-center gap-1 px-3 py-2 rounded-md border',
        liveMode
          ? 'border-border focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-100'
          : 'border-transparent bg-ink-300/10',
        overridden && 'border-blue-400',
      )}>
        <input
          value={shown}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.currentTarget.blur();
            if (e.key === 'Escape') { setDraft(null); e.currentTarget.blur(); }
          }}
          readOnly={!liveMode}
          inputMode="decimal"
          aria-label={label}
          className="w-full bg-transparent text-[13px] tabular-nums text-ink-900 focus:outline-none"
        />
        <span className="text-ink-400 text-[12px]">%</span>
      </div>
      {sub && <div className="text-[10.5px] text-ink-400 mt-1">{sub}</div>}
    </div>
  );
}

// Compact editable percent cell for the waterfall table.
function CellPct({
  valueFraction, overridden, liveMode, onCommit,
}: {
  valueFraction: number;
  overridden: boolean;
  liveMode: boolean;
  onCommit: (fraction: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (valueFraction * 100).toFixed(0);
  const commit = () => {
    if (draft === null) return;
    const t = draft.trim();
    setDraft(null);
    if (t === '') return;
    const pct = Number(t);
    if (Number.isFinite(pct)) onCommit(round6(pct / 100));
  };
  return (
    <span className={cn(
      'inline-flex items-center gap-0.5 justify-end rounded border px-1.5 py-0.5',
      liveMode
        ? 'border-border focus-within:border-brand-500 focus-within:ring-2 focus-within:ring-brand-100'
        : 'border-transparent',
      overridden && 'border-blue-400 bg-blue-50',
    )}>
      <input
        value={shown}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur();
          if (e.key === 'Escape') { setDraft(null); e.currentTarget.blur(); }
        }}
        readOnly={!liveMode}
        inputMode="decimal"
        aria-label="percent"
        className="w-9 bg-transparent text-right text-[12.5px] tabular-nums text-ink-900 focus:outline-none"
      />
      <span className="text-ink-400 text-[11px]">%</span>
    </span>
  );
}
