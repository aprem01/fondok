'use client';
import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  type ReactNode,
} from 'react';
import { useParams } from 'next/navigation';
import { Plus, Trash2 } from 'lucide-react';
import { useSubTab } from '@/lib/hooks/useSubTab';
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
  InlineEditControls,
  inlineEditInputStyle,
  useCancelOnOutside,
  NO_OP_EDIT_MESSAGE,
} from '@/components/design';
import { isNoOpEdit } from '@/lib/fieldValue';
import {
  applyOverridePatch,
  patchRequiresNote,
  requiresNote,
  NOTE_REQUIRED_MESSAGE,
} from '@/lib/overrideNote';

// ─── Canonical structure (design/canonical/Partnership Tab.dc.html) ──────────
// Three sub-tabs, exactly as the prototype: Summary · Waterfall · Cash Flows.
// FON-59 #4 — the sub-tab *id* is the URL slug (`?tab=partnership&sub=…`); the
// label is display only.
const SUB_TABS = [
  { id: 'summary', label: 'Summary' },
  { id: 'waterfall', label: 'Waterfall' },
  { id: 'cash-flows', label: 'Cash Flows' },
] as const;
type SubTab = (typeof SUB_TABS)[number]['id'];
const SUB_TAB_IDS = SUB_TABS.map((t) => t.id) as readonly SubTab[];
const SUB_CAPTION: Record<SubTab, string> = {
  summary: 'Equity split, waterfall terms and partner returns',
  waterfall: 'The partnership assumption workspace',
  'cash-flows': 'Contributions and distributions through exit',
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
// FON-66 follow-up — an analyst can remove ANY promote tier (mid-stack, not
// just the top). A removed index persists as a per-index TOMBSTONE override
// (`partnership.waterfall.<idx>.removed = true`) that the worker's builder
// skips, packing the survivors contiguously. `tier_count` stays the upper
// bound of the index space; the UI renders only the non-tombstoned indices
// below it, numbered contiguously to match the built stack. `removed` is
// deliberately not one of the numeric tier fields, so the worker can never
// read it as a hurdle or split.
const tierRemovedPath = (i: number): string => `partnership.waterfall.${i}.removed`;

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

// Read a boolean override (the tier tombstone), tolerating the structured
// `{ value }` shape and string/number encodings. Absent or unreadable → false,
// so a tier is never hidden by accident.
function readOverrideFlag(overrides: Record<string, unknown>, path: string): boolean {
  const raw = overrides[path];
  const val = raw && typeof raw === 'object' && 'value' in raw
    ? (raw as { value: unknown }).value
    : raw;
  if (val === true || val === 1) return true;
  if (typeof val === 'string') return ['true', '1', 'yes'].includes(val.trim().toLowerCase());
  return false;
}

export default function PartnershipTab() {
  // `?tab=partnership&sub=<slug>` — one convention, deep-linkable.
  const { sub: tab, setSub: setTab } = useSubTab(SUB_TAB_IDS, 'summary');
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
  // FON-74 — the justification for the draft above. Cleared on open and on
  // every exit, so a reason can never outlive the change it explained.
  const [note, setNote] = useState('');
  // The value the open editor started on (a fraction), so Save can tell a real
  // change from a re-save of what was already on screen. Null = not yet set.
  const editStartRef = useRef<number | null>(null);

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
  // sees an inconsistent pair. A null value clears that override. Booleans
  // carry the per-tier tombstone (`<idx>.removed`).
  // FON-74 — the analyst's justification rides with every key in the patch, so
  // a complementary pair (GP% + LP%, GP split + LP split) carries one reason on
  // both sides. Refused here as well as by the API so the analyst reads the
  // message rather than a 422.
  const onSaveOverride = useCallback(
    async (patch: Record<string, number | boolean | null>, note = '') => {
      if (!liveMode) {
        toast('Editing is disabled on demo deals', { type: 'info' });
        return;
      }
      if (!note.trim() && patchRequiresNote(patch)) {
        toast(NOTE_REQUIRED_MESSAGE, { type: 'error' });
        return;
      }
      const next = applyOverridePatch(overrides, patch, note);
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
      const close = () => { editStartRef.current = null; setEditing(null); setDraft(''); setNote(''); };
      const t = draft.trim();
      if (t === '') { close(); return; }
      const p = Number(t.replace(/[^0-9.\-]/g, ''));
      if (!Number.isFinite(p)) { close(); return; }
      const frac = round6(p / 100);
      // FON-63 / FON-66 §1 — the guard runs on the PRIMARY key against the
      // value the editor opened on. An unchanged GP ownership must write
      // NEITHER gp nor lp: deriving the complement off a no-op was minting two
      // phantom overrides per stray Save.
      if (isNoOpEdit(frac, editStartRef.current, 'pct_fraction')) {
        close();
        toast(NO_OP_EDIT_MESSAGE, { type: 'info' });
        return;
      }
      const patch: Record<string, number> = { [primaryKey]: frac };
      if (complementKey) patch[complementKey] = round6(1 - frac);
      // FON-74 — AFTER the no-op guard: re-saving a value unchanged exits
      // quietly, and only a real change is asked to justify itself. The editor
      // stays open so the analyst types the reason instead of losing the edit.
      const justification = note.trim();
      if (!justification && patchRequiresNote(patch)) {
        toast(NOTE_REQUIRED_MESSAGE, { type: 'error' });
        return;
      }
      close();
      void onSaveOverride(patch, justification);
    },
    [draft, note, onSaveOverride, toast],
  );
  const startEdit = useCallback((id: string, currentFraction: number | null) => {
    editStartRef.current = currentFraction;
    setNote('');
    setEditing(id);
    setDraft(
      currentFraction == null
        ? ''
        : (currentFraction * 100).toFixed(currentFraction * 100 % 1 === 0 ? 0 : 1),
    );
  }, []);
  const cancelEdit = useCallback(() => {
    // Exits edit mode with no network call — Cancel, Esc and click-outside
    // all land here.
    editStartRef.current = null;
    setEditing(null);
    setDraft('');
    setNote('');
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
  // FON-66 follow-up — the tiers actually in the stack: every index below the
  // bound that has NOT been tombstoned, in index order. This mirrors the
  // worker builder exactly (skip tombstones, pack the survivors), so the rows
  // the analyst sees are the tiers the engine runs. Floor of one, also
  // mirrored: if every index is tombstoned the worker keeps the lowest one.
  const visibleTierIndices = useMemo(() => {
    const all = Array.from({ length: tierCount }, (_, i) => i);
    const visible = all.filter((i) => !readOverrideFlag(overrides, tierRemovedPath(i)));
    return visible.length > 0 ? visible : all.slice(0, 1);
  }, [overrides, tierCount]);
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
  // tier_count so the worker sees a complete tier on the next run. The new
  // tier is ALWAYS a fresh index at the bound — a tombstoned hole is never
  // reused, so a removed tier's values can never resurface through +Add.
  const onAddTier = useCallback((note: string) => {
    if (!liveMode) {
      toast('Editing is disabled on demo deals', { type: 'info' });
      return;
    }
    if (tierCount >= MAX_TIERS) {
      toast(`Waterfalls are capped at ${MAX_TIERS} tiers`, { type: 'info' });
      return;
    }
    const newIdx = tierCount; // 0-based index of the appended tier
    const topVisible = visibleTierIndices[visibleTierIndices.length - 1] ?? tierCount - 1;
    const prevHurdle = tierHurdleAt(topVisible);
    const newHurdle = round6(Math.min(0.99, prevHurdle + 0.05));
    // FON-74 — the tier COUNT is the waterfall's shape and needs no reason, but
    // the hurdle and splits it seeds are numbers the engine runs on, so the
    // analyst says why the tier exists.
    void onSaveOverride({
      [TIER_COUNT_PATH]: tierCount + 1,
      [`partnership.waterfall.${newIdx}.hurdle_rate`]: newHurdle,
      [`partnership.waterfall.${newIdx}.gp_split`]: 0,
      [`partnership.waterfall.${newIdx}.lp_split`]: 1,
    }, note);
  }, [liveMode, tierCount, visibleTierIndices, tierHurdleAt, onSaveOverride, toast]);

  // "Remove" — drop ANY promote tier (FON-66 follow-up). Two persistence
  // shapes, both read by the worker builder:
  //  * Removing the TOP visible tier lowers tier_count to just above the next
  //    surviving tier and clears every override (values AND tombstones) at or
  //    beyond the new bound. With no tombstones present this is exactly the
  //    shipped Part A behaviour (count − 1, clear that index); with trailing
  //    tombstones it compacts them away so the index space stays tight. When
  //    the count returns to the seed length we clear tier_count entirely
  //    (restores the pure default).
  //  * Removing a MID-STACK tier writes the per-index tombstone
  //    (`<idx>.removed = true`) and clears that index's hurdle/split
  //    overrides so no stale value can ever resurface. tier_count and every
  //    other tier's values are untouched — the worker skips the index and
  //    packs the survivors, each keeping its own hurdle and splits.
  // The last surviving tier can't be removed (the waterfall needs one promote
  // tier — the worker enforces the same floor).
  const onRemoveTier = useCallback(
    (idx: number) => {
      if (!liveMode) {
        toast('Editing is disabled on demo deals', { type: 'info' });
        return;
      }
      const visible = visibleTierIndices;
      if (visible.length <= 1 || !visible.includes(idx)) return;
      const patch: Record<string, number | boolean | null> = {};
      const topVisible = visible[visible.length - 1];
      if (idx === topVisible) {
        const newCount = visible[visible.length - 2] + 1; // next survivor + 1
        patch[TIER_COUNT_PATH] = newCount === WATERFALL_SEED.length ? null : newCount;
        for (let j = newCount; j < tierCount; j += 1) {
          patch[`partnership.waterfall.${j}.hurdle_rate`] = null;
          patch[`partnership.waterfall.${j}.gp_split`] = null;
          patch[`partnership.waterfall.${j}.lp_split`] = null;
          patch[tierRemovedPath(j)] = null;
        }
      } else {
        patch[tierRemovedPath(idx)] = true;
        patch[`partnership.waterfall.${idx}.hurdle_rate`] = null;
        patch[`partnership.waterfall.${idx}.gp_split`] = null;
        patch[`partnership.waterfall.${idx}.lp_split`] = null;
      }
      void onSaveOverride(patch);
    },
    [liveMode, tierCount, visibleTierIndices, onSaveOverride, toast],
  );
  // Summary-tab helper: the first promote band above the preferred tier is the
  // SECOND visible tier (not a fixed index — a tombstone may have removed 1).
  const promoteAbovePrefIdx: number | undefined = visibleTierIndices[1];

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

  // FON-67 (D3) — additional equity. A deficit period (negative project cash)
  // is funded by a dated PRO-RATA GP/LP capital call (by ownership split) that
  // adds to unreturned capital, so the preferred return accrues on it. The
  // worker reports the partner draws AFTER the close and the peak equity
  // funded (initial + every draw). All three are ABSENT on runs from before
  // this change — those render "—", never a fabricated $0.
  const gpAdditional = getEngineField<number>(outputs, 'partnership', 'gp_additional_contributions');
  const lpAdditional = getEngineField<number>(outputs, 'partnership', 'lp_additional_contributions');
  const totalContributionsFlat = getEngineField<number>(outputs, 'partnership', 'total_contributions');

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

  // FON-67 (D3) — invested-equity breakdown. On a run that reports the
  // additional-contribution fields, ``contributed_equity`` already INCLUDES
  // the draws, so the close draw is total − additional. On an older run the
  // fields are absent: ``contributed_equity`` was the close draw only, and
  // the additional / total-invested rows are unknowable → "—".
  const hasAdditional = has(gpAdditional) && has(lpAdditional);
  const additionalTotal = hasAdditional ? gpAdditional + lpAdditional : undefined;
  const totalContributions = has(totalContributionsFlat)
    ? totalContributionsFlat
    : (hasAdditional ? totalEquity : undefined);
  const initialEquity = hasAdditional
    ? ((has(totalContributions) && has(additionalTotal)) ? totalContributions - additionalTotal : undefined)
    : totalEquity;
  const gpInitialEquity = (has(gpEquity) && has(gpAdditional)) ? gpEquity - gpAdditional : gpEquity;
  const lpInitialEquity = (has(lpEquity) && has(lpAdditional)) ? lpEquity - lpAdditional : lpEquity;

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
          items={SUB_TABS.map((t) => ({ id: t.id, label: t.label }))}
          activeId={tab}
          onSelect={(id) => { setTab(id as SubTab); cancelEdit(); }}
          caption={SUB_CAPTION[tab]}
          style={{ marginBottom: 14 }}
        />

        {/* Partnership-terms banner (FON-66 decision D6, 2026-09-09): ONE truthful
            line — manual entry is always available AND JV / operating-agreement
            extraction is live (prose extraction validated on prod 9/5; terms are
            confirmed on the document page before they drive the model). */}
        <ManualInputsBanner onEdit={() => setTab('waterfall')} />
        {/* TODO(FON-72): a manual-entry-only preview endpoint would let the
            waterfall/allocation render from unsaved inputs before a full engine
            run. Backend flagged this; endpoint intentionally not built here. */}

        <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
          {tab === 'summary' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              {/* Equity Structure + Waterfall Terms */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(430px,1fr))', gap: 14 }}>
                {/* FON-66 §1 (Sam, 9/11): the Summary showed ONE "Total Equity
                    → Investment" row whose number included the deficit-period
                    capital calls, while the Investment page it links to shows
                    the close draw — "the Summary label is simply conflating
                    initial equity with subsequent capital calls." The bridge
                    already existed on Cash Flows; the Summary now carries it
                    too, and the "→ Investment" link sits on the INITIAL row,
                    the one number Investment actually owns. */}
                <SectionCard title="Equity Structure" note="Initial equity comes from the deal financing; later calls come from the waterfall">
                  <KeyRow
                    label="Initial Equity Required"
                    dot={dotState('total_equity_usd', 'linked')}
                    value={money(initialEquity)}
                    valueColor={prov.green}
                    link={{ label: '→ Investment', tab: 'investment', sub: 'sources-and-uses' }}
                    note="Drawn at close — this is the equity line in Investment › Sources & Uses"
                  />
                  <KeyRow
                    label="Additional Contributions"
                    dot="calculated"
                    value={money(additionalTotal)}
                    valueColor={prov.gray}
                    link={{ label: '→ Partner Cash Flows', tab: 'partnership', sub: 'cash-flows' }}
                    note={
                      hasAdditional
                        ? 'Deficit periods funded as dated pro-rata GP/LP capital calls — the preferred return accrues on them'
                        : 'This run predates additional-contribution tracking — re-run the Partnership engine to report deficit-period capital calls (funded pro-rata GP/LP; the preferred return accrues on them).'
                    }
                  />
                  <KeyRow
                    label="Total Invested Equity"
                    dot="calculated"
                    value={money(totalContributions)}
                    valueColor={prov.black}
                    bold
                    note="Initial equity plus every later call — the basis the equity multiple is struck on"
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
                    requireNote={requiresNote('gp_equity_pct')}
                    justification={note}
                    onJustification={setNote}
                    value={pctv(gpPct, 0)}
                    valueColor={prov.blue}
                  />
                  <KeyRow
                    label="LP Investor Ownership"
                    dot="calculated"
                    value={pctv(lpPct, 0)}
                    valueColor={prov.gray}
                  />
                  {/* Totals, so GP + LP ties to Total Invested Equity above;
                      the sub-line names the close draw so the split reconciles
                      against Initial Equity Required as well. */}
                  <KeyRow
                    label="GP Contribution"
                    dot="calculated"
                    value={money(gpEquity)}
                    valueColor={prov.gray}
                    note={hasAdditional ? `${money(gpInitialEquity)} at close` : undefined}
                  />
                  <KeyRow
                    label="LP Contribution"
                    dot="calculated"
                    value={money(lpEquity)}
                    valueColor={prov.gray}
                    note={hasAdditional ? `${money(lpInitialEquity)} at close` : undefined}
                  />
                </SectionCard>

                <SectionCard title="Waterfall Terms" note="Your inputs — manual entry, or JV-agreement terms confirmed on the document page">
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
                    requireNote={requiresNote('pref_rate')}
                    justification={note}
                    onJustification={setNote}
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
                  {/* FON-66 (Sam, 2026-09-09) — this row is the GP share of the FIRST
                      promote tier above the pref (a single tier, not a multi-tier
                      summary — "Additional Hurdles" below covers the rest), so it is
                      labelled "Initial Promote Split: 20% GP". */}
                  <KeyRow
                    label="Initial Promote Split"
                    dot="assumption"
                    value={promoteAbovePrefIdx == null
                      ? '—'
                      : `${pctv(readOverrideNum(
                        overrides,
                        `partnership.waterfall.${promoteAbovePrefIdx}.gp_split`,
                        WATERFALL_SEED[promoteAbovePrefIdx]?.gp ?? NaN,
                      ), 0)} GP`}
                    valueColor={prov.blue}
                    note="Edit in the Waterfall tab"
                  />
                  <KeyRow
                    label="Additional Hurdles"
                    dot="calculated"
                    value={promoteAbovePrefIdx == null
                      ? '—'
                      : `${pctv(tierHurdleAt(promoteAbovePrefIdx), 0)} LP IRR · +${Math.max(0, visibleTierIndices.length - 2)} tiers`}
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
                    onClick={() => setTab('waterfall')}
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

          {tab === 'waterfall' && (
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
                    requireNote={requiresNote('gp_equity_pct')}
                    justification={note}
                    onJustification={setNote}
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
                    requireNote={requiresNote('pref_rate')}
                    justification={note}
                    onJustification={setNote}
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
                  note={note}
                  setNote={setNote}
                  startEdit={startEdit}
                  cancelEdit={cancelEdit}
                  commitPct={commitPct}
                  tierCount={tierCount}
                  tierIndices={visibleTierIndices}
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

          {tab === 'cash-flows' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <SectionCard
                variant="title"
                title="Partner Cash Flows"
                note={`Annual · close through disposition in year ${has(holdYears) ? holdYears : '—'}`}
              >
                <PartnerCashFlows
                  gpEquity={gpEquity}
                  lpEquity={lpEquity}
                  gpInitialEquity={gpInitialEquity}
                  lpInitialEquity={lpInitialEquity}
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
                  note={
                    hasAdditional
                      ? 'Initial equity is drawn at close. A deficit period is funded as a dated pro-rata GP/LP capital call (by ownership split) that adds to unreturned capital — the preferred return accrues on it.'
                      : 'This run predates additional-contribution tracking — re-run the Partnership engine to report deficit-period capital calls (funded pro-rata GP/LP; the preferred return accrues on them).'
                  }
                  rows={[
                    { label: 'Initial equity required', value: money(initialEquity) },
                    // FON-67 (D3) — read from the engine; "—" when the run predates
                    // the fields. Muted only when the engine reports no draws.
                    { label: 'Additional contributions — GP', value: money(gpAdditional), muted: has(gpAdditional) && gpAdditional === 0 },
                    { label: 'Additional contributions — LP', value: money(lpAdditional), muted: has(lpAdditional) && lpAdditional === 0 },
                    { label: 'Additional contributions', value: money(additionalTotal), muted: has(additionalTotal) && additionalTotal === 0 },
                    { label: 'Total invested equity', value: money(totalContributions), total: true },
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
// Partnership-terms banner (canonical blue card between the sub-tabs and body).
// FON-66 D6 — a single truthful statement. Previously two rows said "manual only"
// and then "LIVE · document extraction", which contradicted each other.
// ─────────────────────────────────────────────────────────────────────
function ManualInputsBanner({ onEdit }: { onEdit: () => void }) {
  return (
    <div style={{
      background: 'oklch(97.5% 0.015 250)', border: '1px solid #dbe3f5', borderRadius: 9,
      padding: '12px 16px', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
    }}>
      <span style={{
        display: 'flex', alignItems: 'center', gap: 6, fontSize: 10, fontWeight: 700,
        letterSpacing: '.05em', color: palette.linkBlue, textTransform: 'uppercase', whiteSpace: 'nowrap',
      }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: prov.blue, display: 'inline-block' }} />
        Partnership terms
      </span>
      <span style={{ fontSize: 12.5, color: palette.ink, lineHeight: 1.5 }}>
        Manual entry always available · JV / operating-agreement extraction live — upload the
        agreement in the Data Room and confirm the extracted terms on the document page.
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
  /** FON-66 — `sub` names the target's sub-tab: Sam asked for "→ Investment"
   *  to land on Investment → Sources & Uses, where the initial equity
   *  requirement actually lives. */
  link?: { label: string; tab: string; sub?: string };
  editable?: boolean;
  editing?: boolean;
  draft?: string;
  onStart?: () => void;
  onDraft?: (v: string) => void;
  onCommit?: () => void;
  onCancel?: () => void;
  /** FON-74 — the analyst justification for this row's override. */
  requireNote?: boolean;
  justification?: string;
  onJustification?: (v: string) => void;
  justificationTestId?: string;
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
            <a href={`?tab=${p.link.tab}${p.link.sub ? `&sub=${p.link.sub}` : ''}`} style={{ fontSize: 10.5, color: palette.linkBlue, fontWeight: 600, whiteSpace: 'nowrap', textDecoration: 'none' }}>
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
            requireNote={p.requireNote}
            note={p.justification}
            onNote={p.onJustification}
            noteTestId={p.justificationTestId}
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

// Shared inline percent editor — the canonical input + Save · Cancel pair.
// FON-66 §1: Esc, Cancel and a click anywhere outside all discard the draft
// without a network call; Save runs the no-op guard in `commitPct` first.
function InlineEditor({
  draft, width, onDraft, onCommit, onCancel, requireNote, note, onNote, noteTestId,
}: {
  draft: string;
  width: number;
  onDraft?: (v: string) => void;
  onCommit?: () => void;
  onCancel?: () => void;
  /** FON-74 — this key routes into engine input, so Save needs a reason. */
  requireNote?: boolean;
  note?: string;
  onNote?: (v: string) => void;
  noteTestId?: string;
}) {
  const outsideRef = useCancelOnOutside(true, () => onCancel?.());
  return (
    <span ref={outsideRef} style={{ display: 'flex', alignItems: requireNote ? 'flex-start' : 'center', gap: 6, flexShrink: 0 }}>
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
        style={{ ...inlineEditInputStyle, width }}
      />
      <InlineEditControls
        onSave={() => onCommit?.()}
        onCancel={() => onCancel?.()}
        note={requireNote ? note ?? '' : undefined}
        onNote={requireNote ? onNote : undefined}
        noteTestId={noteTestId}
      />
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

// Renumber the "Tier N" captions to visible order — DISPLAY ONLY. After a
// mid-stack removal the worker keeps each surviving tier's original label (the
// labels key the tier_allocations rows, so the default stays byte-identical),
// which leaves gaps like "Tier 4" sitting at position 3. Here we rewrite only
// the leading tier number to a running counter, preserving each tier's own
// hurdle suffix (e.g. " (to 25%)") and every amount/kind/order. The first
// Tier-numbered row keeps its base number; later ones increment from it, so a
// deck that starts at "Tier 2" (after the Preferred row) still reads 2,3,4…
function renumberTierLabels(rows: TierAllocation[]): TierAllocation[] {
  let n: number | null = null;
  return rows.map((a) => {
    const m = /^Tier\s+(\d+)(.*)$/i.exec(a.label ?? '');
    if (!m) return a;
    n = n === null ? parseInt(m[1], 10) : n + 1;
    return { ...a, label: `Tier ${n}${m[2]}` };
  });
}

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
      {renumberTierLabels(allocations).map((a, i) => (
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
// FON-66 §2 (Sam, 9/11) — waterfall table polish.
//
//  · De-emphasise Description: it is context, not a number. The column loses
//    a third of its width to the numeric columns and drops to the faint ink,
//    so the eye lands on Hurdle / GP / LP first.
//  · The Remove control is revealed on row hover / keyboard focus and is an
//    icon with its label on `aria-label`, instead of a bordered button shouting
//    on every row.
//  · "+ Add tier" renders INSIDE the grid as a full-width dashed row under a
//    hairline, so it reads as adding a row to this table.
//
// DECLINED, deliberately — Sam's optional sixth: *"consider presenting each
// economic split together (20% GP / 80% LP)"*. Two right-aligned numeric
// columns scan down a table better than a combined string, and both halves are
// independently editable; collapsing them would cost an edit target to save a
// column. Recorded here so the decision is visible where the table is.
const TIER_GRID = '38px minmax(180px,1.5fr) minmax(120px,1fr) 90px 90px minmax(150px,1fr)';

function PromoteWaterfall({
  prefRate, hasCatchUp, liveMode, overrides, editing, draft, setDraft, note, setNote,
  startEdit, cancelEdit, commitPct,
  tierCount, tierIndices, onAddTier, onRemoveTier,
}: {
  prefRate: number;
  hasCatchUp: boolean;
  liveMode: boolean;
  overrides: Record<string, unknown>;
  editing: string | null;
  draft: string;
  setDraft: (v: string) => void;
  startEdit: (id: string, fraction: number | null) => void;
  cancelEdit: () => void;
  note: string;
  setNote: (v: string) => void;
  commitPct: (primaryKey: string, complementKey?: string) => void;
  // FON-66 Part A — variable tier count + add/remove controls. `tierCount` is
  // the index-space bound (the +Add cap); `tierIndices` are the surviving
  // (non-tombstoned) indices actually rendered, in index order.
  tierCount: number;
  tierIndices: number[];
  onAddTier: (note: string) => void;
  onRemoveTier: (idx: number) => void;
}) {
  const { toast } = useToast();
  // FON-74 — null: the "+ Add tier" button. A string: the justification row is
  // open and holds what the analyst has typed so far.
  const [addNote, setAddNote] = useState<string | null>(null);
  // FON-66 §2 — which promote row is hovered or keyboard-focused, so its Remove
  // control can reveal itself instead of shouting on every row. Focus counts:
  // a control that only appears on :hover is unreachable by keyboard.
  const [revealed, setRevealed] = useState<number | null>(null);
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
        <span style={{ color: palette.textFaint, lineHeight: 1.4, fontSize: 11 }}>{s.desc}</span>
      </div>
    );
  });

  const seedLen = WATERFALL_SEED.length;
  const visibleCount = tierIndices.length;
  // Rows are the SURVIVING indices, numbered contiguously (`idx`) so the
  // display matches the packed stack the worker builds; `i` stays the raw
  // override index each row's edits persist under.
  const promoteRows = tierIndices.map((i, pos) => {
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
    const last = pos === visibleCount - 1;
    // FON-66 follow-up — EVERY promote tier is removable (mid-stack included),
    // as long as one survives.
    const removable = liveMode && visibleCount > 1;
    const name = incomplete
      ? `Promote — tier ${pos + 1} · incomplete`
      : last
        ? `Promote — above ${fmtPct(hurdleFrac, 0)} LP IRR`
        : `Promote — to ${fmtPct(hurdleFrac, 0)} LP IRR`;
    const hurdleId = `t${i}-h`;
    const splitId = `t${i}-s`;
    return (
      <div
        key={`promote-${i}`}
        onMouseEnter={() => setRevealed(i)}
        onMouseLeave={() => setRevealed((cur) => (cur === i ? null : cur))}
        onFocus={() => setRevealed(i)}
        onBlur={() => setRevealed((cur) => (cur === i ? null : cur))}
        style={{
          display: 'grid', gridTemplateColumns: TIER_GRID, fontSize: 12.5, padding: '8px 0',
          borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center',
        }}
      >
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
              requireNote={requiresNote(hurdlePath)}
              note={note}
              onNote={setNote}
            />
          ) : (
            <span
              onClick={liveMode ? () => startEdit(hurdleId, hurdleComplete ? hurdleFrac : null) : undefined}
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
              requireNote={requiresNote(gpPath)}
              note={note}
              onNote={setNote}
            />
          ) : (
            <span
              onClick={liveMode ? () => startEdit(splitId, gpComplete ? gpFrac : null) : undefined}
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
          <span style={{ color: incomplete ? prov.amber : palette.textFaint, lineHeight: 1.4, fontSize: 11, minWidth: 0 }}>
            {incomplete
              ? 'Set the hurdle and GP split to activate this tier'
              : last ? 'All remaining proceeds above the final hurdle' : 'Residual split until LP IRR reaches the hurdle'}
          </span>
          {removable && (
            <button
              onClick={() => onRemoveTier(i)}
              aria-label={`Remove promote tier ${pos + 1}`}
              title="Remove this tier — the other tiers keep their own hurdles and splits"
              // FON-66 §2 — revealed on row hover / focus. It stays in the DOM
              // and in the tab order at all times, so the keyboard and the
              // screen reader never lose it; only the ink comes and goes.
              style={{
                flexShrink: 0, background: 'transparent', border: 'none', padding: 3,
                borderRadius: radius.control, color: palette.textMuted, cursor: 'pointer',
                display: 'inline-flex', alignItems: 'center', lineHeight: 1,
                opacity: revealed === i ? 1 : 0,
                transition: 'opacity 120ms ease-out',
              }}
            >
              <Trash2 size={13} aria-hidden="true" />
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
        <span style={{ fontWeight: 600, opacity: 0.7 }}>Description</span>
      </div>
      {structuralRows}
      {promoteRows}
      {/* FON-66 §2 — the add affordance is a ROW of this table, not a button
          parked beneath it: full width across the same grid, under the same
          hairline every tier row carries. */}
      {liveMode && addNote !== null && (
        <div style={{
          display: 'grid', gridTemplateColumns: TIER_GRID,
          borderTop: `1px solid ${palette.border}`, padding: '10px 0',
        }}>
          <span />
          <span style={{ gridColumn: '2 / -1', display: 'flex', alignItems: 'flex-start', gap: 10, flexWrap: 'wrap' }}>
            <InlineEditControls
              onSave={() => {
                if (!addNote.trim()) { toast(NOTE_REQUIRED_MESSAGE, { type: 'error' }); return; }
                onAddTier(addNote);
                setAddNote(null);
              }}
              onCancel={() => setAddNote(null)}
              saveLabel="Add tier"
              saveTestId="add-tier-save"
              note={addNote}
              onNote={setAddNote}
              noteTestId="add-tier-note"
            />
            <span style={{ fontSize: 11, color: palette.textMuted, maxWidth: 320, lineHeight: 1.45 }}>
              A new tier seeds a hurdle and a 100% LP / 0% GP split — say why it belongs in the waterfall.
            </span>
          </span>
        </div>
      )}
      {liveMode && addNote === null && (
        <button
          type="button"
          onClick={() => setAddNote('')}
          disabled={tierCount >= MAX_TIERS}
          title={tierCount >= MAX_TIERS
            ? `Maximum ${MAX_TIERS} tiers`
            : 'New tiers start at 100% LP / 0% GP — set the GP split to create a promote.'}
          style={{
            display: 'grid', gridTemplateColumns: TIER_GRID, width: '100%', textAlign: 'left',
            alignItems: 'center', gap: 0, padding: '9px 0', fontFamily: 'inherit',
            background: 'transparent', borderTop: `1px solid ${palette.border}`,
            borderLeft: 'none', borderRight: 'none',
            borderBottom: `1px dashed ${tierCount >= MAX_TIERS ? palette.border : palette.linkBlue}`,
            color: tierCount >= MAX_TIERS ? palette.textMuted : palette.linkBlue,
            cursor: tierCount >= MAX_TIERS ? 'default' : 'pointer',
            opacity: tierCount >= MAX_TIERS ? 0.55 : 1,
          }}
        >
          <span style={{ display: 'inline-flex', justifyContent: 'center', alignItems: 'center' }}>
            <Plus size={13} aria-hidden="true" />
          </span>
          <span style={{ gridColumn: '2 / -1', fontSize: 11.5, fontWeight: 600 }}>
            Add tier
            <span style={{ fontWeight: 400, color: palette.textMuted, marginLeft: 8 }}>
              {tierCount >= MAX_TIERS
                ? `Maximum ${MAX_TIERS} tiers`
                : 'starts at 100% LP / 0% GP — set the GP split to create a promote'}
            </span>
          </span>
        </button>
      )}
      <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>
        LP split is always 100% − GP split. Hurdles are LP IRR thresholds; the final tier takes everything
        above the last hurdle. Any tier can be removed; the remaining tiers keep their own hurdles and splits.
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
  gpEquity, lpEquity, gpInitialEquity, lpInitialEquity, gpFlows, lpFlows, gpDist, lpDist, totalDist, holdYears,
}: {
  /** Total contributed (close draw + every additional draw) — the Total row. */
  gpEquity: number | undefined;
  lpEquity: number | undefined;
  /** The close draw only — the Close row (FON-67: additional draws are dated
   *  in the year they are called, below). */
  gpInitialEquity: number | undefined;
  lpInitialEquity: number | undefined;
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
        { text: money(gpInitialEquity), color: prov.blue },
        { text: money(lpInitialEquity), color: prov.blue },
        dash, dash,
      ],
    },
    ...gpFlows!.map((gp, i) => {
      const lp = lpFlows![i] ?? 0;
      const isExit = i === gpFlows!.length - 1;
      // FON-67 (D3) — a negative partner flow is a dated pro-rata capital call
      // (the engine funds a deficit period that way). It belongs in the
      // contribution column, never rendered as a negative distribution.
      const gpDraw = gp < 0;
      const lpDraw = lp < 0;
      return {
        label: isExit ? `Year ${i + 1} / Exit` : `Year ${i + 1}`,
        bg: 'transparent',
        cells: [
          { text: money(gp + lp), color: prov.gray },
          gpDraw ? { text: money(-gp), color: prov.blue } : dash,
          lpDraw ? { text: money(-lp), color: prov.blue } : dash,
          gpDraw ? dash : { text: money(gp), color: prov.gray },
          lpDraw ? dash : { text: money(lp), color: prov.gray },
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
      footnote={`Contributions and distributions are shown separately — a contribution is never a negative distribution. A deficit period is funded as a dated pro-rata GP/LP capital call (the preferred return accrues on it). The schedule ends at the modeled disposition in year ${has(holdYears) ? holdYears : '—'}.`}
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
