'use client';
import {
  useState,
  useEffect,
  useRef,
  useCallback,
  type ReactNode,
} from 'react';
import { useParams } from 'next/navigation';
import { api, isWorkerConnected, type ValueState } from '@/lib/api';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useDeal } from '@/lib/hooks/useDeal';
import { useToast } from '@/components/ui/Toast';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useTraceGraph } from '@/lib/hooks/useValueTrace';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import { IntroCard } from '@/components/help/IntroCard';
import { fmtCurrency, fmtPct, cn } from '@/lib/format';
import {
  SectionCard,
  SubTabNav,
  StatementTable,
  ProvenanceDot,
  palette,
  prov,
  radius,
} from '@/components/design';

// ─── Canonical structure (design/canonical/Partnership Tab.dc.html) ──────────
// Three sub-tabs, exactly as the prototype: Summary · Waterfall · Cash Flows.
const SUB_TABS = [
  { id: 'Summary', label: 'Summary' },
  { id: 'Waterfall', label: 'Waterfall' },
  { id: 'Cash Flows', label: 'Cash Flows' },
];
const SUB_CAPTION: Record<string, string> = {
  Summary: 'Equity split, waterfall terms and partner returns',
  Waterfall: 'The partnership assumption workspace',
  'Cash Flows': 'Contributions and distributions through exit',
};

const COMPOUNDING_OPTIONS = [
  'Annual / cumulative',
  'Annual / non-cumulative',
  'Quarterly / cumulative',
];

// FON-66 — the editable promote-band seed. This MIRRORS the worker's
// `_KIMPTON_WATERFALL_REFERENCE` (engine_runner.py): the deal-agnostic
// institutional benchmark an analyst edits from the Waterfall sub-tab. Each
// tier's editable fields persist as indexed field_overrides
// (`partnership.waterfall.<idx>.<field>`) the worker layers over this seed.
// `hurdle`/`gp` are fractions; LP split derives as `1 - gp`. Index order is
// load-bearing — the worker keys overrides by position, so the six bands stay
// editable here to avoid regressing the existing save path.
const WATERFALL_SEED: Array<{ hurdle: number; gp: number }> = [
  { hurdle: 0.10, gp: 0.00 },
  { hurdle: 0.15, gp: 0.20 },
  { hurdle: 0.20, gp: 0.25 },
  { hurdle: 0.25, gp: 0.25 },
  { hurdle: 0.30, gp: 0.25 },
  { hurdle: 0.50, gp: 0.50 },
];
// FON-66 Part A — the analyst can change the promote-tier COUNT (add/remove
// tiers), not just edit the seed bands. The count persists as a single
// `partnership.waterfall.tier_count` field_override the worker reads. Absent →
// the seed length exactly (byte-identical default). Bounds mirror the worker
// (`_MAX_PARTNERSHIP_TIERS`) so the UI can't request a stack the engine clamps.
const TIER_COUNT_PATH = 'partnership.waterfall.tier_count';
const MAX_TIERS = 12;

// Worker partnership PartnerReturn shape (runtime nested `gp`/`lp` objects).
interface PartnerReturn {
  partner: string;
  contributed_equity: number;
  distributions: number;
  irr: number;
  equity_multiple: number;
}

// FON-72 — one row of the dollar waterfall ("Allocation of Projected Proceeds").
interface TierAllocation {
  label: string;
  kind: 'return_of_capital' | 'preferred' | 'catch_up' | 'promote';
  gp_amount: number;
  lp_amount: number;
  total_amount: number;
}

const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const money = (v: number | undefined): string => (has(v) ? fmtCurrency(v) : '—');
const moneyC = (v: number | undefined): string =>
  has(v) ? fmtCurrency(v, { compact: true }) : '—';
const pctv = (v: number | undefined, d = 1): string => (has(v) ? fmtPct(v, d) : '—');
const multv = (v: number | undefined): string => (has(v) ? `${v.toFixed(2)}x` : '—');

function round6(n: number): number {
  return Math.round(n * 1e6) / 1e6;
}

// Read a scalar override, tolerating both the flat scalar and the structured
// `{ value, note }` shape the override panel writes.
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

export default function PartnershipTab() {
  const [tab, setTab] = useState('Summary');
  const { toast } = useToast();
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs, previous } = useEngineOutputs(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  // Compounding is a display-only workspace control in this release (the
  // prototype models it locally; no backend field consumes it yet).
  const [compounding, setCompounding] = useState(COMPOUNDING_OPTIONS[0]);

  // Single inline-editor cursor (canonical `state.editing`) + its draft string.
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');

  // ─── FON-66: editable waterfall assumptions ────────────────────────
  // Live deals (real UUID + worker connected) can edit ownership, preferred
  // return, and the promote bands. Edits PATCH the deal's field_overrides and
  // kick a debounced run-all so GP/LP outputs re-derive. Demo / mock numeric
  // deals stay read-only.
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

  // Provenance dots read the real /provenance `state` for a partnership output
  // path, falling back to the value's semantic kind. The partnership engine may
  // ship without a provenance sidecar — the fallback is expected, not an error.
  const partnershipTrace = useTraceGraph('partnership');
  const dotState = useCallback(
    (path: string, fallback: ValueState): ValueState =>
      partnershipTrace.get(path)?.state ?? fallback,
    [partnershipTrace],
  );

  // Persist one or more overrides in a single PATCH. Complementary fields
  // (GP/LP ownership, GP/LP tier split) are saved together so the engine never
  // sees an inconsistent pair. A null value clears that override.
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

  // Commit the inline editor: `raw` (whole-percent string) → fraction, routed to
  // the given override key(s). Complementary keys derive `1 - fraction`.
  const commitPct = useCallback(
    (primaryKey: string, complementKey?: string) => {
      const t = draft.trim();
      setEditing(null);
      setDraft('');
      if (t === '') return;
      const p = Number(t.replace(/[^0-9.\-]/g, ''));
      if (!Number.isFinite(p)) return;
      const frac = round6(p / 100);
      const patch: Record<string, number> = { [primaryKey]: frac };
      if (complementKey) patch[complementKey] = round6(1 - frac);
      void onSaveOverride(patch);
    },
    [draft, onSaveOverride],
  );
  const startEdit = useCallback((id: string, currentFraction: number) => {
    setEditing(id);
    setDraft((currentFraction * 100).toFixed(currentFraction * 100 % 1 === 0 ? 0 : 1));
  }, []);
  const cancelEdit = useCallback(() => {
    setEditing(null);
    setDraft('');
  }, []);

  // ─── FON-66 Part A: variable promote-tier count ────────────────────
  // How many promote tiers this deal has. An analyst override wins; absent →
  // the seed length. Clamped to the worker's [1, MAX_TIERS] range so the UI
  // and engine never disagree on the count.
  const tierCount = Math.max(
    1,
    Math.min(
      MAX_TIERS,
      Math.round(readOverrideNum(overrides, TIER_COUNT_PATH, WATERFALL_SEED.length)),
    ),
  );
  // A tier's current hurdle/GP split: analyst override wins, else the seed
  // value (in-seed indices only — beyond-seed tiers have no seed fallback).
  const tierHurdleAt = useCallback(
    (i: number): number =>
      readOverrideNum(overrides, `partnership.waterfall.${i}.hurdle_rate`, WATERFALL_SEED[i]?.hurdle ?? 0),
    [overrides],
  );

  // "+ Add tier" — append a promote tier. It starts as a coherent, honest
  // default the analyst must complete: a hurdle one step above the current top
  // tier and a 100% LP / 0% GP split (NO promote is fabricated — the analyst
  // raises the GP split deliberately). Persisted together with the bumped
  // tier_count so the worker sees a complete tier on the next run.
  const onAddTier = useCallback(() => {
    if (!liveMode) {
      toast('Editing is disabled on demo deals', { type: 'info' });
      return;
    }
    if (tierCount >= MAX_TIERS) {
      toast(`Waterfalls are capped at ${MAX_TIERS} tiers`, { type: 'info' });
      return;
    }
    const newIdx = tierCount; // 0-based index of the appended tier
    const prevHurdle = tierHurdleAt(tierCount - 1);
    const newHurdle = round6(Math.min(0.99, prevHurdle + 0.05));
    void onSaveOverride({
      [TIER_COUNT_PATH]: tierCount + 1,
      [`partnership.waterfall.${newIdx}.hurdle_rate`]: newHurdle,
      [`partnership.waterfall.${newIdx}.gp_split`]: 0,
      [`partnership.waterfall.${newIdx}.lp_split`]: 1,
    });
  }, [liveMode, tierCount, tierHurdleAt, onSaveOverride, toast]);

  // "Remove" — drop the top (highest) promote tier. Decrements tier_count and
  // clears that index's per-tier overrides so they can't linger. When the
  // count returns to the seed length we clear tier_count entirely (restores the
  // pure default). Only the last tier is removable — this mirrors the worker,
  // where a lower tier_count drops the highest tiers.
  const onRemoveTier = useCallback(
    (idx: number) => {
      if (!liveMode) {
        toast('Editing is disabled on demo deals', { type: 'info' });
        return;
      }
      if (tierCount <= 1) return;
      const newCount = tierCount - 1;
      void onSaveOverride({
        [TIER_COUNT_PATH]: newCount === WATERFALL_SEED.length ? null : newCount,
        [`partnership.waterfall.${idx}.hurdle_rate`]: null,
        [`partnership.waterfall.${idx}.gp_split`]: null,
        [`partnership.waterfall.${idx}.lp_split`]: null,
      });
    },
    [liveMode, tierCount, onSaveOverride, toast],
  );

  // ─── Worker partnership fields (dual-shape: nested objects OR flat export) ──
  const wGp = getEngineField<PartnerReturn>(outputs, 'partnership', 'gp');
  const wLp = getEngineField<PartnerReturn>(outputs, 'partnership', 'lp');
  const wGpEquityFlat = getEngineField<number>(outputs, 'partnership', 'gp_equity_usd');
  const wLpEquityFlat = getEngineField<number>(outputs, 'partnership', 'lp_equity_usd');
  const wTotalEquityFlat = getEngineField<number>(outputs, 'partnership', 'total_equity_usd');
  const wLpPrefPct = getEngineField<number>(outputs, 'partnership', 'lp_pref_pct');
  const wGpIrrFlat = getEngineField<number>(outputs, 'partnership', 'gp_irr')
    ?? getEngineField<number>(outputs, 'partnership', 'gp_irr_after_promote');
  const wLpIrrFlat = getEngineField<number>(outputs, 'partnership', 'lp_irr')
    ?? getEngineField<number>(outputs, 'partnership', 'lp_irr_after_promote');
  const wGpMultipleFlat = getEngineField<number>(outputs, 'partnership', 'gp_multiple')
    ?? getEngineField<number>(outputs, 'partnership', 'gp_equity_multiple');
  const wLpMultipleFlat = getEngineField<number>(outputs, 'partnership', 'lp_multiple')
    ?? getEngineField<number>(outputs, 'partnership', 'lp_equity_multiple');

  const gpEquity = wGp?.contributed_equity ?? wGpEquityFlat;
  const lpEquity = wLp?.contributed_equity ?? wLpEquityFlat;
  const gpIrr = wGp?.irr ?? wGpIrrFlat;
  const lpIrr = wLp?.irr ?? wLpIrrFlat;
  const gpMultiple = wGp?.equity_multiple ?? wGpMultipleFlat;
  const lpMultiple = wLp?.equity_multiple ?? wLpMultipleFlat;
  const gpDist = wGp?.distributions;
  const lpDist = wLp?.distributions;
  const wGpFlows = getEngineField<number[]>(outputs, 'partnership', 'gp_cash_flows');
  const wLpFlows = getEngineField<number[]>(outputs, 'partnership', 'lp_cash_flows');
  const promote = getEngineField<number>(outputs, 'partnership', 'promote_amount')
    ?? getEngineField<number>(outputs, 'partnership', 'promote_earned');

  // FON-72 — the dollar waterfall + reconciliation, straight from the engine.
  const tierAllocations = getEngineField<TierAllocation[]>(outputs, 'partnership', 'tier_allocations');
  const totalDistributableFlat = getEngineField<number>(outputs, 'partnership', 'total_distributable');
  const reconcilesFlag = getEngineField<boolean>(outputs, 'partnership', 'reconciles');
  const catchUpAmount = getEngineField<number>(outputs, 'partnership', 'catch_up_amount');

  // Deal-level economics come from the Returns engine source-of-truth (levered),
  // never a prototype placeholder.
  const dealIrr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const dealMoicEngine = getEngineField<number>(outputs, 'returns', 'equity_multiple');
  const holdYears = getEngineField<number>(outputs, 'returns', 'hold_years');

  // Derived totals — every value is grounded in an engine field or undefined.
  const totalEquity = (has(gpEquity) && has(lpEquity))
    ? gpEquity + lpEquity
    : wTotalEquityFlat;
  const totalDist = has(totalDistributableFlat)
    ? totalDistributableFlat
    : (has(gpDist) && has(lpDist) ? gpDist + lpDist : undefined);
  const dealMoic = has(dealMoicEngine)
    ? dealMoicEngine
    : (has(totalDist) && has(totalEquity) && totalEquity > 0 ? totalDist / totalEquity : undefined);
  const dealProfit = (has(totalDist) && has(totalEquity)) ? totalDist - totalEquity : undefined;
  const gpProfit = (has(gpDist) && has(gpEquity)) ? gpDist - gpEquity : undefined;
  const lpProfit = (has(lpDist) && has(lpEquity)) ? lpDist - lpEquity : undefined;

  // Ownership split — an analyst override wins, else derived from the engine's
  // contributed equity, else the institutional default.
  const gpPctOverride = 'gp_equity_pct' in overrides
    ? readOverrideNum(overrides, 'gp_equity_pct', 0.10)
    : undefined;
  const gpPctComputed = (has(gpEquity) && has(lpEquity) && gpEquity + lpEquity > 0)
    ? gpEquity / (gpEquity + lpEquity)
    : undefined;
  const gpPct = gpPctOverride ?? gpPctComputed ?? 0.10;
  const lpPct = 1 - gpPct;

  const prefOverride = 'pref_rate' in overrides
    ? readOverrideNum(overrides, 'pref_rate', 0.08)
    : undefined;
  const prefRate = prefOverride ?? (has(wLpPrefPct) ? wLpPrefPct : 0.08);

  const hasWorkerPartnership = wGp != null || wLp != null
    || (tierAllocations != null && tierAllocations.length > 0)
    || wGpEquityFlat != null || wLpEquityFlat != null
    || wGpIrrFlat != null || wLpIrrFlat != null;

  const hasCatchUp = (has(catchUpAmount) && catchUpAmount > 0)
    || !!tierAllocations?.some((t) => t.kind === 'catch_up');

  // Dollar-waterfall totals (sum the tier rows; total prefers the engine field).
  const lpAllocTotal = tierAllocations?.reduce((s, t) => s + (t.lp_amount || 0), 0);
  const gpAllocTotal = tierAllocations?.reduce((s, t) => s + (t.gp_amount || 0), 0);
  const allocTotal = has(totalDistributableFlat)
    ? totalDistributableFlat
    : tierAllocations?.reduce((s, t) => s + (t.total_amount || 0), 0);

  // Exit-year distribution = final period of the partner cash-flow series.
  const exitDist = (Array.isArray(wGpFlows) && Array.isArray(wLpFlows)
    && wGpFlows.length > 0 && wGpFlows.length === wLpFlows.length)
    ? (wGpFlows[wGpFlows.length - 1] ?? 0) + (wLpFlows[wLpFlows.length - 1] ?? 0)
    : undefined;

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
          complete={hasWorkerPartnership}
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

        <SubTabNav
          items={SUB_TABS}
          activeId={tab}
          onSelect={(id) => { setTab(id); cancelEdit(); }}
          caption={SUB_CAPTION[tab]}
          style={{ marginBottom: 14 }}
        />

        {/* Manual-inputs banner — partnership terms are entered by hand in this
            release (not extracted from the JV agreement). Canonical blue card. */}
        <ManualInputsBanner onEdit={() => setTab('Waterfall')} />
        {/* TODO(FON-72): a manual-entry-only preview endpoint would let the
            waterfall/allocation render from unsaved inputs before a full engine
            run. Backend flagged this; endpoint intentionally not built here. */}

        <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
          {tab === 'Summary' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* Equity Structure + Waterfall Terms */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(430px,1fr))', gap: 14 }}>
                <SectionCard title="Equity Structure" note="Total equity comes from the deal financing">
                  <KeyRow
                    label="Total Equity"
                    dot={dotState('total_equity_usd', 'linked')}
                    value={money(totalEquity)}
                    valueColor={prov.green}
                    bold
                    link={{ label: '→ Investment', tab: 'investment' }}
                    note="Total uses less senior debt and key money — set by the deal financing, not here"
                  />
                  <KeyRow
                    label="GP / Sponsor Ownership"
                    dot="assumption"
                    editable={liveMode}
                    editing={editing === 'gpPct-sum'}
                    draft={draft}
                    onStart={() => startEdit('gpPct-sum', gpPct)}
                    onDraft={setDraft}
                    onCommit={() => commitPct('gp_equity_pct', 'lp_equity_pct')}
                    onCancel={cancelEdit}
                    value={pctv(gpPct, 0)}
                    valueColor={prov.blue}
                  />
                  <KeyRow
                    label="LP Investor Ownership"
                    dot="calculated"
                    value={pctv(lpPct, 0)}
                    valueColor={prov.gray}
                  />
                  <KeyRow label="GP Contribution" dot="calculated" value={money(gpEquity)} valueColor={prov.gray} />
                  <KeyRow label="LP Contribution" dot="calculated" value={money(lpEquity)} valueColor={prov.gray} />
                </SectionCard>

                <SectionCard title="Waterfall Terms" note="Your inputs — not extracted from the JV agreement">
                  <KeyRow
                    label="Preferred Return"
                    dot="assumption"
                    editable={liveMode}
                    editing={editing === 'pref-sum'}
                    draft={draft}
                    onStart={() => startEdit('pref-sum', prefRate)}
                    onDraft={setDraft}
                    onCommit={() => commitPct('pref_rate')}
                    onCancel={cancelEdit}
                    value={pctv(prefRate, 0)}
                    valueColor={prov.blue}
                  />
                  <KeyRow
                    label="Compounding"
                    dot="assumption"
                    value={compounding}
                    valueColor={prov.blue}
                    note="Edit in the Waterfall tab"
                  />
                  <KeyRow
                    label="GP Catch-Up"
                    dot="assumption"
                    value={hasCatchUp ? 'Full catch-up until GP promote share met' : 'None configured'}
                    valueColor={hasCatchUp ? prov.blue : prov.muted}
                  />
                  <KeyRow
                    label="Promote Above Pref"
                    dot="assumption"
                    value={pctv(readOverrideNum(overrides, 'partnership.waterfall.1.gp_split', WATERFALL_SEED[1].gp), 0)}
                    valueColor={prov.blue}
                    note="Edit in the Waterfall tab"
                  />
                  <KeyRow
                    label="Additional Hurdles"
                    dot="calculated"
                    value={`${pctv(tierHurdleAt(1), 0)} LP IRR · +${Math.max(0, tierCount - 2)} tiers`}
                    valueColor={prov.gray}
                  />
                </SectionCard>
              </div>

              {/* Partner Returns — deal, LP, GP cards */}
              <SectionCard title="Partner Returns" note="Deal-level economics, then what each partner receives after the waterfall">
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(250px,1fr))', gap: 12, marginTop: 4 }}>
                  <MetricCard
                    title="Deal level"
                    note="Before the partnership split — what the investment itself generates."
                    metrics={[
                      { label: 'IRR', value: pctv(dealIrr), size: 18 },
                      { label: 'MOIC', value: multv(dealMoic), size: 18 },
                      { label: 'Total profit', value: money(dealProfit), size: 14 },
                    ]}
                  />
                  <MetricCard
                    title="LP investors"
                    note={`${pctv(lpPct, 0)} of the equity · receives pref before any promote is paid.`}
                    metrics={[
                      { label: 'LP IRR', value: pctv(lpIrr), size: 18 },
                      { label: 'LP MOIC', value: multv(lpMultiple), size: 18 },
                      { label: 'LP profit', value: money(lpProfit), size: 14 },
                    ]}
                  />
                  <MetricCard
                    title="GP / sponsor"
                    accent
                    note={`Co-invest of ${pctv(gpPct, 0)} plus promote earned through the waterfall.`}
                    metrics={[
                      { label: 'GP IRR', value: pctv(gpIrr), size: 18 },
                      { label: 'GP MOIC', value: multv(gpMultiple), size: 18 },
                      { label: 'Total GP profit', value: money(gpProfit), size: 14 },
                      { label: 'Promote / carry earned', value: money(promote), size: 14 },
                      { label: 'GP co-invest', value: pctv(gpPct, 0), size: 14 },
                    ]}
                  />
                </div>
              </SectionCard>

              {/* Waterfall Allocation Preview — the dollar waterfall */}
              <SectionCard
                title="Waterfall Allocation Preview"
                note={
                  <span
                    onClick={() => setTab('Waterfall')}
                    style={{ fontSize: 11.5, color: palette.linkBlue, fontWeight: 600, cursor: 'pointer' }}
                  >
                    View / edit waterfall →
                  </span>
                }
              >
                <AllocationTable
                  allocations={tierAllocations}
                  lpTotal={lpAllocTotal}
                  gpTotal={gpAllocTotal}
                  total={allocTotal}
                  reconciles={reconcilesFlag === true}
                  footnote={`Allocation of all projected distributions across the ${has(holdYears) ? holdYears : '—'}-year hold. The promote applies only to residual proceeds above the preferred return and catch-up.`}
                />
              </SectionCard>
            </div>
          )}

          {tab === 'Waterfall' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* Ownership & Preferred Return */}
              <SectionCard
                title="Ownership & Preferred Return"
                note="Entered by you — Fondok does not read the JV agreement in this release"
              >
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(320px,1fr))', gap: '0 32px' }}>
                  <KeyRow
                    label="GP / Sponsor Ownership"
                    dot="assumption"
                    editable={liveMode}
                    editing={editing === 'gpPct-wf'}
                    draft={draft}
                    onStart={() => startEdit('gpPct-wf', gpPct)}
                    onDraft={setDraft}
                    onCommit={() => commitPct('gp_equity_pct', 'lp_equity_pct')}
                    onCancel={cancelEdit}
                    value={pctv(gpPct, 0)}
                    valueColor={prov.blue}
                  />
                  <KeyRow label="LP Investor Ownership" dot="calculated" value={pctv(lpPct, 0)} valueColor={prov.gray} />
                  <KeyRow
                    label="Preferred Return"
                    dot="assumption"
                    editable={liveMode}
                    editing={editing === 'pref-wf'}
                    draft={draft}
                    onStart={() => startEdit('pref-wf', prefRate)}
                    onDraft={setDraft}
                    onCommit={() => commitPct('pref_rate')}
                    onCancel={cancelEdit}
                    value={pctv(prefRate, 0)}
                    valueColor={prov.blue}
                  />
                  <CompoundingRow value={compounding} onChange={setCompounding} />
                </div>
              </SectionCard>

              {/* Promote Waterfall — typed tiers (ROC / Preferred / Catch-Up / Promote) */}
              <SectionCard title="Promote Waterfall">
                <PromoteWaterfall
                  prefRate={prefRate}
                  hasCatchUp={hasCatchUp}
                  liveMode={liveMode}
                  overrides={overrides}
                  editing={editing}
                  draft={draft}
                  setDraft={setDraft}
                  startEdit={startEdit}
                  cancelEdit={cancelEdit}
                  commitPct={commitPct}
                  tierCount={tierCount}
                  onAddTier={onAddTier}
                  onRemoveTier={onRemoveTier}
                />
              </SectionCard>

              {/* Allocation of Projected Proceeds — the dollar waterfall */}
              <SectionCard
                title="Allocation of Projected Proceeds"
                note="Calculated from the tiers above and the modeled deal cash flow"
              >
                <AllocationTable
                  allocations={tierAllocations}
                  lpTotal={lpAllocTotal}
                  gpTotal={gpAllocTotal}
                  total={allocTotal}
                  reconciles={reconcilesFlag === true}
                />
              </SectionCard>
            </div>
          )}

          {tab === 'Cash Flows' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <SectionCard
                variant="title"
                title="Partner Cash Flows"
                note={`Annual · close through disposition in year ${has(holdYears) ? holdYears : '—'}`}
              >
                <PartnerCashFlows
                  gpEquity={gpEquity}
                  lpEquity={lpEquity}
                  gpFlows={wGpFlows}
                  lpFlows={wLpFlows}
                  gpDist={gpDist}
                  lpDist={lpDist}
                  totalDist={totalDist}
                  holdYears={holdYears}
                />
              </SectionCard>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(360px,1fr))', gap: 14 }}>
                <ReconCard
                  title="Contributions, distributions and profit"
                  note="Net profit is total distributions less total contributions — not a cash-flow total."
                  rows={[
                    { label: 'Total contributions', value: money(totalEquity) },
                    { label: 'Total distributions', value: money(totalDist) },
                    { label: 'Net profit', value: money(dealProfit), total: true },
                  ]}
                />
                <ReconCard
                  title="Invested equity"
                  note="Contributions are drawn in full at close in this structure."
                  rows={[
                    { label: 'Initial equity required', value: money(totalEquity) },
                    { label: 'Additional contributions', value: money(0), muted: true },
                    { label: 'Total invested equity', value: money(totalEquity), total: true },
                    { label: 'GP share', value: money(gpEquity) },
                    { label: 'LP share', value: money(lpEquity) },
                  ]}
                />
                <ReconCard
                  title="Distributions"
                  note="GP and LP distributions reconcile to the deal cash flow above."
                  rows={[
                    { label: 'Operating distributions', value: (has(totalDist) && has(exitDist)) ? money(totalDist - exitDist) : '—' },
                    { label: 'Exit distributions', value: money(exitDist) },
                    { label: 'Total distributions', value: money(totalDist), total: true },
                    { label: 'GP distributions', value: money(gpDist) },
                    { label: 'LP distributions', value: money(lpDist) },
                  ]}
                />
              </div>
            </div>
          )}

          {computing && (
            <div className="absolute inset-0 bg-bg/60 backdrop-blur-[1px] flex items-start justify-center pt-12 rounded-md">
              <span className="inline-flex items-center gap-2 px-3 py-1.5 bg-white border border-border rounded-md shadow-card text-[12.5px] font-medium text-ink-700">
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

// ─────────────────────────────────────────────────────────────────────
// Manual-inputs banner (canonical blue card between the sub-tabs and body).
// ─────────────────────────────────────────────────────────────────────
function ManualInputsBanner({ onEdit }: { onEdit: () => void }) {
  return (
    <div style={{
      background: 'oklch(97.5% 0.015 250)', border: '1px solid #dbe3f5', borderRadius: 9,
      padding: '12px 16px', marginBottom: 14, display: 'flex', flexDirection: 'column', gap: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
        <span style={{
          display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, fontWeight: 700,
          letterSpacing: '.05em', color: palette.linkBlue, textTransform: 'uppercase', whiteSpace: 'nowrap',
        }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: prov.blue, display: 'inline-block' }} />
          Manual inputs · current release
        </span>
        <span style={{ fontSize: 12.5, color: palette.ink, lineHeight: 1.5 }}>
          Partnership terms are entered manually in this release and are not extracted from JV or
          partnership documents. Fondok calculates the waterfall, allocations and partner returns from
          what you enter.
        </span>
        <button
          onClick={onEdit}
          style={{
            marginLeft: 'auto', background: palette.inkNavy, color: '#fff', border: 'none',
            borderRadius: radius.button, padding: '6px 13px', fontSize: 11.5, fontWeight: 600,
            cursor: 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap',
          }}
        >
          Edit assumptions →
        </button>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', borderTop: '1px solid #dbe3f5', paddingTop: 8 }}>
        <span style={{
          fontSize: 9.5, fontWeight: 700, letterSpacing: '.06em', color: palette.textSecondary,
          textTransform: 'uppercase', background: '#fff', border: '1px solid #e2e1dc',
          borderRadius: radius.pill, padding: '3px 9px', whiteSpace: 'nowrap',
        }}>
          Coming soon · document extraction
        </span>
        <span style={{ fontSize: 11.5, color: palette.textSecondary, lineHeight: 1.45 }}>
          Upload partnership documents and automatically extract ownership, preferred return, promote and
          waterfall terms in a future release.
        </span>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Key/value row — dot · label · optional link · value (editable inline).
// ─────────────────────────────────────────────────────────────────────
interface KeyRowProps {
  label: string;
  dot: ValueState;
  value: ReactNode;
  valueColor?: string;
  bold?: boolean;
  note?: string;
  link?: { label: string; tab: string };
  editable?: boolean;
  editing?: boolean;
  draft?: string;
  onStart?: () => void;
  onDraft?: (v: string) => void;
  onCommit?: () => void;
  onCancel?: () => void;
}

function KeyRow(p: KeyRowProps) {
  return (
    <>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
        fontSize: 13, padding: '7px 0', borderBottom: `1px solid ${palette.hairlineRow}`,
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
          <ProvenanceDot state={p.dot} size={8} />
          <span style={{ color: palette.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {p.label}
          </span>
          {p.link && (
            <a href={`?tab=${p.link.tab}`} style={{ fontSize: 10.5, color: palette.linkBlue, fontWeight: 600, whiteSpace: 'nowrap', textDecoration: 'none' }}>
              {p.link.label}
            </a>
          )}
        </span>
        {p.editing ? (
          <InlineEditor
            draft={p.draft ?? ''}
            width={120}
            onDraft={p.onDraft}
            onCommit={p.onCommit}
            onCancel={p.onCancel}
          />
        ) : (
          <span
            onClick={p.editable ? p.onStart : undefined}
            style={{
              color: p.valueColor ?? palette.ink,
              fontWeight: p.bold ? 700 : 400,
              textDecoration: p.editable ? 'underline dotted' : undefined,
              cursor: p.editable ? 'pointer' : 'default',
              fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', flexShrink: 0,
            }}
          >
            {p.value}
          </span>
        )}
      </div>
      {p.note && (
        <div style={{ fontSize: 10.5, color: palette.textMuted, padding: '0 0 6px 15px', lineHeight: 1.45 }}>
          {p.note}
        </div>
      )}
    </>
  );
}

// Compounding — canonical <select> (display-only workspace control).
function CompoundingRow({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
      fontSize: 13, padding: '7px 0', borderBottom: `1px solid ${palette.hairlineRow}`,
    }}>
      <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
        <ProvenanceDot state="assumption" size={8} />
        <span style={{ color: palette.textSecondary, whiteSpace: 'nowrap' }}>Compounding</span>
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Compounding"
        style={{
          fontSize: 12, fontFamily: 'inherit', fontWeight: 600, color: prov.blue,
          background: palette.surfaceTint, border: '1px solid #e2e1dc', borderRadius: radius.button,
          padding: '4px 8px', cursor: 'pointer',
        }}
      >
        {COMPOUNDING_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  );
}

// Shared inline percent editor (input + navy Save), canonical styling.
function InlineEditor({
  draft, width, onDraft, onCommit, onCancel,
}: {
  draft: string;
  width: number;
  onDraft?: (v: string) => void;
  onCommit?: () => void;
  onCancel?: () => void;
}) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
      <input
        autoFocus
        value={draft}
        onChange={(e) => onDraft?.(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') onCommit?.();
          if (e.key === 'Escape') onCancel?.();
        }}
        inputMode="decimal"
        aria-label="percent"
        style={{
          width, fontSize: 12.5, fontFamily: 'inherit', border: `1px solid ${palette.linkBlue}`,
          borderRadius: radius.control, padding: '4px 7px', textAlign: 'right', fontVariantNumeric: 'tabular-nums',
        }}
      />
      <button
        onClick={onCommit}
        style={{
          background: palette.inkNavy, color: '#fff', border: 'none', borderRadius: radius.control,
          padding: '5px 9px', fontSize: 11, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
        }}
      >
        Save
      </button>
    </span>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Partner Returns metric card (Deal / LP / GP).
// ─────────────────────────────────────────────────────────────────────
function MetricCard({
  title, note, metrics, accent,
}: {
  title: string;
  note: string;
  metrics: { label: string; value: ReactNode; size: number }[];
  accent?: boolean;
}) {
  return (
    <div style={{
      border: `1px solid ${accent ? '#dbe3f5' : palette.border}`,
      background: accent ? 'oklch(97.5% 0.015 250)' : palette.cardWhite,
      borderRadius: 9, padding: '14px 16px',
    }}>
      <div style={{
        fontSize: 10, fontWeight: 700, letterSpacing: '.05em',
        color: accent ? palette.linkBlue : palette.eyebrow, textTransform: 'uppercase', marginBottom: 10,
      }}>
        {title}
      </div>
      {metrics.map((m) => (
        <div key={m.label} style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 12,
          padding: '5px 0', borderBottom: `1px solid ${palette.hairlineSection}`,
        }}>
          <span style={{ fontSize: 12, color: palette.textSecondary }}>{m.label}</span>
          <span style={{ fontSize: m.size, fontWeight: 700, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>
            {m.value}
          </span>
        </div>
      ))}
      <div style={{ fontSize: 10.5, color: palette.textMuted, marginTop: 8, lineHeight: 1.45 }}>{note}</div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Allocation of Projected Proceeds — the dollar waterfall (Tier / LP / GP /
// Allocated proceeds) with a "Reconciles ✓" badge. Reads tier_allocations[].
// ─────────────────────────────────────────────────────────────────────
const ALLOC_GRID = 'minmax(190px,1.6fr) minmax(110px,1fr) minmax(110px,1fr) minmax(120px,1fr)';

function AllocationTable({
  allocations, lpTotal, gpTotal, total, reconciles, footnote,
}: {
  allocations: TierAllocation[] | undefined;
  lpTotal: number | undefined;
  gpTotal: number | undefined;
  total: number | undefined;
  reconciles: boolean;
  footnote?: string;
}) {
  if (!allocations || allocations.length === 0) {
    return (
      <div style={{ fontSize: 12.5, color: palette.textMuted, padding: '10px 0' }}>
        Run the Partnership engine to populate the allocation of projected proceeds.
      </div>
    );
  }
  const cell = (v: number | undefined, color: string, weight: number): ReactNode => (
    <span style={{ textAlign: 'right', color, fontWeight: weight, fontVariantNumeric: 'tabular-nums' }}>
      {money(v)}
    </span>
  );
  return (
    <>
      <div style={{
        display: 'grid', gridTemplateColumns: ALLOC_GRID, fontSize: 10, fontWeight: 700,
        letterSpacing: '.05em', color: palette.textFaint, textTransform: 'uppercase',
        paddingBottom: 7, borderBottom: `1px solid ${palette.border}`,
      }}>
        <span>Tier</span>
        <span style={{ textAlign: 'right' }}>LP</span>
        <span style={{ textAlign: 'right' }}>GP</span>
        <span style={{ textAlign: 'right' }}>Allocated proceeds</span>
      </div>
      {allocations.map((a, i) => (
        <div key={`${a.label}-${i}`} style={{
          display: 'grid', gridTemplateColumns: ALLOC_GRID, fontSize: 12.5, padding: '7px 0',
          borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center',
        }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
            <ProvenanceDot state="calculated" size={8} />
            <span style={{ color: palette.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {a.label}
            </span>
          </span>
          {cell(a.lp_amount, prov.gray, 400)}
          {cell(a.gp_amount, prov.gray, 400)}
          {cell(a.total_amount, prov.gray, 400)}
        </div>
      ))}
      <div style={{
        display: 'grid', gridTemplateColumns: ALLOC_GRID, fontSize: 12.5, padding: '7px 0',
        borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center',
      }}>
        <span style={{ color: palette.ink, fontWeight: 700 }}>Total distributions</span>
        {cell(lpTotal, prov.black, 700)}
        {cell(gpTotal, prov.black, 700)}
        {cell(total, prov.black, 700)}
      </div>
      {reconciles && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
          background: 'oklch(96.5% 0.03 155)', border: '1px solid oklch(88% 0.05 155)',
          borderRadius: 7, padding: '8px 12px', marginTop: 10,
        }}>
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: 'oklch(40% 0.12 155)', textTransform: 'uppercase' }}>
            Reconciles
          </span>
          <span style={{ fontSize: 11.5, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>
            LP {money(lpTotal)} + GP {money(gpTotal)} = {money(total)} total deal distributions ✓
          </span>
        </div>
      )}
      {footnote && (
        <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>{footnote}</div>
      )}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Promote Waterfall — typed tiers. Structural rows (ROC / Preferred /
// Catch-Up) are derived read-only; the promote bands are the editable seed
// mapped 1:1 to the worker override indices (existing save path preserved).
// ─────────────────────────────────────────────────────────────────────
const TIER_GRID = '38px minmax(180px,1.5fr) minmax(120px,1fr) 90px 90px minmax(210px,1.5fr)';

function PromoteWaterfall({
  prefRate, hasCatchUp, liveMode, overrides, editing, draft, setDraft, startEdit, cancelEdit, commitPct,
  tierCount, onAddTier, onRemoveTier,
}: {
  prefRate: number;
  hasCatchUp: boolean;
  liveMode: boolean;
  overrides: Record<string, unknown>;
  editing: string | null;
  draft: string;
  setDraft: (v: string) => void;
  startEdit: (id: string, fraction: number) => void;
  cancelEdit: () => void;
  commitPct: (primaryKey: string, complementKey?: string) => void;
  // FON-66 Part A — variable tier count + add/remove controls.
  tierCount: number;
  onAddTier: () => void;
  onRemoveTier: (idx: number) => void;
}) {
  // Structural (read-only) rows first, then the editable promote bands.
  const structural: Array<{ name: string; hurdle: string; gp: string; lp: string; desc: string; dot: ValueState }> = [
    {
      name: 'Tier I — Return of Capital', hurdle: 'Contributed capital', gp: '—', lp: '—',
      desc: 'Contributed capital returned pro-rata before any return', dot: 'calculated',
    },
    {
      name: 'Tier II — Preferred Return', hurdle: `${fmtPct(prefRate, 0)} preferred return`, gp: '—', lp: '—',
      desc: 'LP preferred return on unreturned capital', dot: 'calculated',
    },
  ];
  if (hasCatchUp) {
    structural.push({
      name: 'Tier III — GP Catch-Up', hurdle: 'Until GP catches up', gp: '100%', lp: '0%',
      desc: 'Until the GP has caught up to its promote share', dot: 'calculated',
    });
  }

  let idx = 0;
  const structuralRows = structural.map((s) => {
    idx += 1;
    return (
      <div key={s.name} style={{
        display: 'grid', gridTemplateColumns: TIER_GRID, fontSize: 12.5, padding: '8px 0',
        borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center',
      }}>
        <span style={{ color: palette.textMuted, fontVariantNumeric: 'tabular-nums' }}>{idx}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
          <ProvenanceDot state={s.dot} size={8} />
          <span style={{ color: palette.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.name}</span>
        </span>
        <span style={{ textAlign: 'right', color: prov.gray, fontVariantNumeric: 'tabular-nums' }}>{s.hurdle}</span>
        <span style={{ textAlign: 'right', color: prov.gray, fontVariantNumeric: 'tabular-nums' }}>{s.gp}</span>
        <span style={{ textAlign: 'right', color: prov.gray, fontVariantNumeric: 'tabular-nums', paddingRight: 16 }}>{s.lp}</span>
        <span style={{ color: palette.textMuted, lineHeight: 1.4, fontSize: 11.5 }}>{s.desc}</span>
      </div>
    );
  });

  const seedLen = WATERFALL_SEED.length;
  const promoteRows = Array.from({ length: tierCount }).map((_, i) => {
    idx += 1;
    const seed = WATERFALL_SEED[i]; // undefined for analyst-added tiers
    const beyondSeed = i >= seedLen;
    const gpPath = `partnership.waterfall.${i}.gp_split`;
    const hurdlePath = `partnership.waterfall.${i}.hurdle_rate`;
    // In-seed tiers always have a value (seed fallback). Beyond-seed tiers are
    // defined ONLY by the analyst's overrides — a missing value renders as
    // incomplete (NaN → "Set …"), never a fabricated number, mirroring the
    // worker (which omits an incomplete added tier rather than inventing one).
    const gpFrac = readOverrideNum(overrides, gpPath, seed ? seed.gp : NaN);
    const hurdleFrac = readOverrideNum(overrides, hurdlePath, seed ? seed.hurdle : NaN);
    const gpComplete = Number.isFinite(gpFrac);
    const hurdleComplete = Number.isFinite(hurdleFrac);
    const incomplete = beyondSeed && !(gpComplete && hurdleComplete);
    const last = i === tierCount - 1;
    const removable = liveMode && tierCount > 1 && last;
    const name = incomplete
      ? `Promote — tier ${i + 1} · incomplete`
      : last
        ? `Promote — above ${fmtPct(hurdleFrac, 0)} LP IRR`
        : `Promote — to ${fmtPct(hurdleFrac, 0)} LP IRR`;
    const hurdleId = `t${i}-h`;
    const splitId = `t${i}-s`;
    // Safe seed values for the inline editor when a value is not yet set.
    const hurdleStart = hurdleComplete ? hurdleFrac : 0;
    const gpStart = gpComplete ? gpFrac : 0;
    return (
      <div key={`promote-${i}`} style={{
        display: 'grid', gridTemplateColumns: TIER_GRID, fontSize: 12.5, padding: '8px 0',
        borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center',
      }}>
        <span style={{ color: palette.textMuted, fontVariantNumeric: 'tabular-nums' }}>{idx}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
          <ProvenanceDot state="assumption" size={8} />
          <span style={{ color: incomplete ? prov.amber : palette.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</span>
        </span>
        {/* Hurdle (editable) */}
        <span style={{ display: 'flex', justifyContent: 'flex-end' }}>
          {editing === hurdleId ? (
            <InlineEditor
              draft={draft} width={66}
              onDraft={setDraft}
              onCommit={() => commitPct(hurdlePath)}
              onCancel={cancelEdit}
            />
          ) : (
            <span
              onClick={liveMode ? () => startEdit(hurdleId, hurdleStart) : undefined}
              style={{
                textAlign: 'right', color: !hurdleComplete ? prov.amber : liveMode ? prov.blue : prov.gray,
                textDecoration: liveMode ? 'underline dotted' : undefined,
                cursor: liveMode ? 'pointer' : 'default', fontVariantNumeric: 'tabular-nums',
              }}
            >
              {hurdleComplete ? `Until ${fmtPct(hurdleFrac, 0)} LP IRR` : 'Set hurdle'}
            </span>
          )}
        </span>
        {/* GP split (editable) */}
        <span style={{ display: 'flex', justifyContent: 'flex-end' }}>
          {editing === splitId ? (
            <InlineEditor
              draft={draft} width={56}
              onDraft={setDraft}
              onCommit={() => commitPct(gpPath, `partnership.waterfall.${i}.lp_split`)}
              onCancel={cancelEdit}
            />
          ) : (
            <span
              onClick={liveMode ? () => startEdit(splitId, gpStart) : undefined}
              style={{
                textAlign: 'right', color: !gpComplete ? prov.amber : liveMode ? prov.blue : prov.gray,
                textDecoration: liveMode ? 'underline dotted' : undefined,
                cursor: liveMode ? 'pointer' : 'default', fontVariantNumeric: 'tabular-nums',
              }}
            >
              {gpComplete ? fmtPct(gpFrac, 0) : 'Set split'}
            </span>
          )}
        </span>
        <span style={{ textAlign: 'right', color: prov.gray, fontVariantNumeric: 'tabular-nums', paddingRight: 16 }}>
          {gpComplete ? fmtPct(1 - gpFrac, 0) : '—'}
        </span>
        <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, minWidth: 0 }}>
          <span style={{ color: incomplete ? prov.amber : palette.textMuted, lineHeight: 1.4, fontSize: 11.5, minWidth: 0 }}>
            {incomplete
              ? 'Set the hurdle and GP split to activate this tier'
              : last ? 'All remaining proceeds above the final hurdle' : 'Residual split until LP IRR reaches the hurdle'}
          </span>
          {removable && (
            <button
              onClick={() => onRemoveTier(i)}
              aria-label={`Remove tier ${i + 1}`}
              title="Remove this tier"
              style={{
                flexShrink: 0, background: 'transparent', border: `1px solid ${palette.border}`,
                borderRadius: radius.control, color: palette.textSecondary, cursor: 'pointer',
                fontSize: 11, fontWeight: 600, padding: '2px 8px', fontFamily: 'inherit', lineHeight: 1.4,
              }}
            >
              Remove
            </button>
          )}
        </span>
      </div>
    );
  });

  return (
    <>
      <div style={{
        display: 'grid', gridTemplateColumns: TIER_GRID, fontSize: 10, fontWeight: 700,
        letterSpacing: '.05em', color: palette.textFaint, textTransform: 'uppercase',
        paddingBottom: 7, borderBottom: `1px solid ${palette.border}`,
      }}>
        <span>Tier</span>
        <span>Name</span>
        <span style={{ textAlign: 'right' }}>Hurdle</span>
        <span style={{ textAlign: 'right' }}>GP split</span>
        <span style={{ textAlign: 'right', paddingRight: 16 }}>LP split</span>
        <span>Description</span>
      </div>
      {structuralRows}
      {promoteRows}
      {liveMode && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
          <button
            onClick={onAddTier}
            disabled={tierCount >= MAX_TIERS}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              background: 'transparent', border: `1px dashed ${palette.linkBlue}`, borderRadius: radius.button,
              color: tierCount >= MAX_TIERS ? palette.textMuted : palette.linkBlue,
              cursor: tierCount >= MAX_TIERS ? 'default' : 'pointer',
              fontSize: 11.5, fontWeight: 600, padding: '6px 12px', fontFamily: 'inherit',
              opacity: tierCount >= MAX_TIERS ? 0.55 : 1,
            }}
          >
            + Add tier
          </button>
          <span style={{ fontSize: 11, color: palette.textMuted }}>
            {tierCount >= MAX_TIERS
              ? `Maximum ${MAX_TIERS} tiers`
              : 'New tiers start at 100% LP / 0% GP — set the GP split to create a promote.'}
          </span>
        </div>
      )}
      <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>
        LP split is always 100% − GP split. Hurdles are LP IRR thresholds; the final tier takes everything
        above the last hurdle.
        {!liveMode && ' Waterfall editing is available on live deals.'}
      </div>
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Partner Cash Flows — navy statement grid (contributions vs distributions).
// Grounded entirely in the partnership engine's own series; the deal
// operating/exit split is intentionally omitted (needs the Returns/Cash Flow
// series threaded in — flagged, not fabricated).
// ─────────────────────────────────────────────────────────────────────
function PartnerCashFlows({
  gpEquity, lpEquity, gpFlows, lpFlows, gpDist, lpDist, totalDist, holdYears,
}: {
  gpEquity: number | undefined;
  lpEquity: number | undefined;
  gpFlows: number[] | undefined;
  lpFlows: number[] | undefined;
  gpDist: number | undefined;
  lpDist: number | undefined;
  totalDist: number | undefined;
  holdYears: number | undefined;
}) {
  const usable = Array.isArray(gpFlows) && Array.isArray(lpFlows)
    && gpFlows.length > 0 && gpFlows.length === lpFlows.length;

  if (!usable) {
    return (
      <div style={{ fontSize: 12.5, color: palette.textMuted, padding: '14px 18px' }}>
        Run the Partnership engine to populate partner cash flows.
      </div>
    );
  }

  const columns = ['Total deal cash flow', 'GP contribution', 'LP contribution', 'GP distribution', 'LP distribution'];
  const dash = { text: '—', color: palette.textFaint };

  const rows = [
    {
      label: 'Close', bg: palette.surfaceTint,
      cells: [
        dash,
        { text: money(gpEquity), color: prov.blue },
        { text: money(lpEquity), color: prov.blue },
        dash, dash,
      ],
    },
    ...gpFlows!.map((gp, i) => {
      const lp = lpFlows![i] ?? 0;
      const isExit = i === gpFlows!.length - 1;
      return {
        label: isExit ? `Year ${i + 1} / Exit` : `Year ${i + 1}`,
        bg: 'transparent',
        cells: [
          { text: money(gp + lp), color: prov.gray },
          dash, dash,
          { text: money(gp), color: prov.gray },
          { text: money(lp), color: prov.gray },
        ],
      };
    }),
    {
      label: 'Total', bg: palette.surfaceTint, total: true,
      cells: [
        { text: money(totalDist), color: prov.black },
        { text: money(gpEquity), color: prov.black },
        { text: money(lpEquity), color: prov.black },
        { text: money(gpDist), color: prov.black },
        { text: money(lpDist), color: prov.black },
      ],
    },
  ];

  return (
    <StatementTable
      columns={columns}
      lineItemHeader="Period"
      showDots={false}
      gridTemplateColumns={`100px repeat(${columns.length}, minmax(122px,1fr))`}
      rows={rows.map((r) => ({
        label: r.label,
        total: (r as { total?: boolean }).total,
        bg: r.bg,
        cells: r.cells.map((c) => ({ text: c.text, color: c.color })),
      }))}
      footnote={`Contributions and distributions are shown separately — a contribution is never a negative distribution. The schedule ends at the modeled disposition in year ${has(holdYears) ? holdYears : '—'}.`}
    />
  );
}

// ─────────────────────────────────────────────────────────────────────
// Reconciliation card — label / value list (no dots), bold totals.
// ─────────────────────────────────────────────────────────────────────
function ReconCard({
  title, note, rows,
}: {
  title: string;
  note: string;
  rows: { label: string; value: ReactNode; total?: boolean; muted?: boolean }[];
}) {
  return (
    <SectionCard title={title}>
      {rows.map((r) => (
        <div key={r.label} style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
          fontSize: 13, padding: '7px 0', borderBottom: `1px solid ${palette.hairlineRow}`,
        }}>
          <span style={{ color: r.total ? palette.ink : palette.textSecondary, fontWeight: r.total ? 700 : 400 }}>
            {r.label}
          </span>
          <span style={{
            color: r.total ? prov.black : r.muted ? prov.muted : prov.gray,
            fontWeight: r.total ? 700 : 400, fontVariantNumeric: 'tabular-nums',
          }}>
            {r.value}
          </span>
        </div>
      ))}
      <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>{note}</div>
    </SectionCard>
  );
}
