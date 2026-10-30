'use client';
/**
 * Returns — R-061: no longer a tab. This module renders the "Returns" section
 * at the foot of Overview (`OverviewTab` mounts {@link ReturnsSection}); the
 * former `?tab=returns[&sub=…]` deep links redirect to
 * `?tab=overview&sub=…` (see `lib/returnsSection.ts`) and this section
 * scrolls itself into view. Every element of the old tab is retained here:
 * the hero + secondary KPIs, Exit Assumptions, Return Bridge, the Live
 * Assumptions sandbox (with its override banner), the sensitivity grids and
 * the Pricing view (Max Price Solver + max-price grid).
 */
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import { useSubTab } from '@/lib/hooks/useSubTab';
import { TrendingUp } from 'lucide-react';
import NoiWarningStrip from './NoiWarningStrip';
import { RETURNS_SECTION_ID, RETURNS_VIEWS, RETURNS_VIEW_IDS, isReturnsView, type ReturnsViewId } from '@/lib/returnsSection';
import PricingSensitivityPanel from './PricingSensitivityPanel';
import MaxPricePanel from './MaxPricePanel';
import { fmtPct, cn } from '@/lib/format';
import { TAB_LABEL } from '@/lib/projectTabs';
import { api, isWorkerConnected, type ReturnsPreviewResponse, type ValueState } from '@/lib/api';
import { useDeal } from '@/lib/hooks/useDeal';
// Sensitivity grid shapes (relocated here when the client-side lib/engines model
// was retired — the worker sensitivity engine is now the only source).
interface SensitivityCell {
  /** Null when the engine returned no cell for this (row, col) — rendered '—'. */
  value: number | null;
  rowVal: number;
  colVal: number;
  isBase: boolean;
}
interface SensitivityMatrix {
  rowLabel: string;
  colLabel: string;
  rows: number[];
  cols: number[];
  cells: SensitivityCell[][];
  unit: 'pct' | 'multiple';
  baseRow: number;
  baseCol: number;
}
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import {
  stabilizedYearBlock,
  stabilizationAnchorCaption,
  stabilizationAnchorFallbackNote,
  exitNoiLabel,
} from '@/lib/engines/noi';
import { useFlash } from '@/lib/hooks/useFlash';
import { CoachMark } from '@/components/help/CoachMark';
import { Traced } from '@/components/help/Traced';
import { Sourced } from '@/components/help/Sourced';
import { useSource } from '@/lib/hooks/useDealProvenance';
import { sourceExplanation, sourceLabel, valueStateForSource } from '@/lib/provenance';
import {
  HOLD_DEAL_TYPE_DEFAULT_EXPLANATION,
  IRR_SENTINEL_NOTE,
  exitScheduleRows,
  headlineReturns,
  holdDefaultBadge,
  irrValue,
  selectedExitLabel,
  selectedExitTitle,
} from '@/lib/engines/returns';
import { ExitScheduleCard } from './ExitScheduleCard';
import { EngineFailuresBanner, TAB_OWNED_ENGINES, engineFailures } from './EngineFailuresBanner';
import { EngineOutputsError } from './EngineOutputsError';
import {
  SubTabNav,
  KpiTile,
  SectionCard,
  ProvenanceDot,
  palette,
  prov,
  radius,
} from '@/components/design';

// FON-68 — MVP Returns is three views. Scenario management lives on
// Scenario Analysis; comps live on Market → Transaction Comps.
// FON-59 #4 — the view *id* is the URL slug (`?sub=…`, now on
// `?tab=overview` — R-061); the label is display only.
const SUB_TABS = RETURNS_VIEWS;
type SubTab = ReturnsViewId;
const SUB_TAB_IDS = RETURNS_VIEW_IDS;

type EngineOutputs = ReturnType<typeof useEngineOutputs>['outputs'];

// ── Formatting helpers (canonical: money() / mm() / pct() / x()) ──
const money = (v: number) =>
  `${v < 0 ? '−$' : '$'}${Math.round(Math.abs(v)).toLocaleString('en-US')}`;
const mm = (v: number) => `${v < 0 ? '−$' : '$'}${(Math.abs(v) / 1e6).toFixed(2)}M`;
const fmtM = (v: number | null | undefined) => (v == null ? '—' : mm(v));
const fmtX = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(2)}x`);
const fmtP = (v: number | null | undefined, decimals = 2) => (v == null ? '—' : fmtPct(v, decimals));

/**
 * Standing rule (no invented data): a figure the engine did not produce reads
 * "—" with the missing engine field named — never a 0, never a default. The
 * string doubles as the tile's sub-label and its tooltip.
 */
const awaitingRun = (field: string) => `Awaiting model run — ${field} is not on this run`;

/**
 * R-061 — the Overview "Returns" section (formerly the Returns tab).
 *
 * Owns the DOM anchor (`#overview-returns`) and scrolls itself into view when
 * the URL asks for one of its views — a redirected `?tab=returns&sub=…`
 * bookmark, or a cross-tab "View Returns →" link (`?tab=overview&sub=…`).
 * A view change made INSIDE the section does not re-scroll the page.
 */
export function ReturnsSection() {
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs, error: outputsError, refresh: refreshOutputs } = useEngineOutputs(dealId);
  const searchParams = useSearchParams();
  const sectionRef = useRef<HTMLElement | null>(null);
  const internalNav = useRef(false);
  const requestedSub = searchParams?.get('sub') ?? null;
  const query = searchParams?.toString() ?? '';
  useEffect(() => {
    if (internalNav.current) { internalNav.current = false; return; }
    if (!isReturnsView(requestedSub)) return;
    sectionRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  // Has the Returns engine been run for this deal? Used to decide whether to
  // render the placeholder or the live UI.
  const wReturnsIrr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const hasWorkerReturns = wReturnsIrr != null;
  // D1 / D4 — with no returns on screen, say WHY before saying "unavailable":
  // the read failed (Retry), or one of THIS section's engines failed (the
  // banner names it and the reason; the page-level banner leaves these
  // engines to us on the Overview tab, so each failure shows once). Only a
  // healthy snapshot with no returns is "not run yet".
  const readFailed = outputsError != null && outputs == null;
  const ownedFailures = engineFailures(outputs, { only: TAB_OWNED_ENGINES.overview });

  return (
    <section
      id={RETURNS_SECTION_ID}
      ref={sectionRef}
      data-testid="overview-returns"
      aria-label="Returns"
      style={{ scrollMarginTop: 16 }}
    >
      {ownedFailures.length > 0 && (
        <EngineFailuresBanner outputs={outputs} dealId={dealId} engines={TAB_OWNED_ENGINES.overview} />
      )}
      {hasWorkerReturns ? (
        // Output-only: every view reads the canonical worker engine outputs.
        // The Live Assumptions sliders are a local, ephemeral sandbox
        // (FON-68 step 3), so nothing here can mutate CAPEX / Financing.
        <ReturnsWorkspace
          outputs={outputs}
          dealId={dealId}
          onInternalNav={() => { internalNav.current = true; }}
        />
      ) : readFailed ? (
        <SectionCard title="Returns" note="What the deal earns, and what price it can carry">
          <EngineOutputsError error={outputsError} onRetry={refreshOutputs} what="the returns" />
        </SectionCard>
      ) : ownedFailures.length > 0 ? null : (
        <SectionCard title="Returns" note="What the deal earns, and what price it can carry">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12.5, color: palette.textMuted, lineHeight: 1.5 }}>
            <TrendingUp size={18} aria-hidden="true" style={{ flexShrink: 0 }} />
            <span data-testid="overview-returns-unavailable">
              Returns engine unavailable — IRR, equity multiple, the sensitivity grids and the
              Pricing solver depend on the Cash Flow engine. Add the OM and T-12 in the Data Room;
              the model runs automatically once extraction finishes.
            </span>
          </div>
        </SectionCard>
      )}
    </section>
  );
}

/** Back-compat default export — the section, rendered standalone. */
export default ReturnsSection;

// ───────────────────────────────────────────────────────────────────
// Ephemeral Live-Assumptions sandbox — LOCAL state only (FON-68 step 3).
// Dragging a slider NEVER mutates the shared assumptions store; it drives
// the non-persisting POST /engines/returns/preview call. Canonical case in
// Investment / Debt is untouched. Lifted to workspace level so the override
// banner (top, spans sub-tabs) and the Live Assumptions card (Sensitivities)
// share one source of truth — exactly the canonical `state.ov` model.
// ───────────────────────────────────────────────────────────────────

type SandboxKey = 'exitCapRate' | 'revparGrowth' | 'holdYears' | 'ltv' | 'interestRate';
type SandboxValues = Record<SandboxKey, number>;

interface SandboxField {
  key: SandboxKey;
  label: string;
  min: number;
  max: number;
  step: number;
  fmt: (v: number) => string;
}

const SANDBOX_FIELDS: SandboxField[] = [
  { key: 'exitCapRate', label: 'Exit Cap Rate', min: 0.04, max: 0.12, step: 0.001, fmt: (v) => fmtPct(v, 2) },
  { key: 'revparGrowth', label: 'RevPAR Growth', min: 0, max: 0.06, step: 0.0025, fmt: (v) => fmtPct(v, 2) },
  { key: 'holdYears', label: 'Hold Period', min: 3, max: 10, step: 1, fmt: (v) => `${Math.round(v)} years` },
  { key: 'ltv', label: 'LTV', min: 0.4, max: 0.8, step: 0.01, fmt: (v) => fmtPct(v, 0) },
  { key: 'interestRate', label: 'Interest Rate', min: 0.04, max: 0.1, step: 0.00125, fmt: (v) => fmtPct(v, 3) },
];

// FON-68 §2 (Sam) — the slider stopped at 6.00% while the sensitivity grids ran
// past it, so the matrix offered a case the sandbox could not reproduce. Read
// the top of the grid's OWN RevPAR-growth axis rather than restating it here:
// the two cannot drift because there is only one number. Absent a matrix (an
// older run) the declared default stands.
function revparAxisMax(outputs: EngineOutputs): number | null {
  const named = getEngineField<WorkerMatrixRaw[]>(outputs, 'sensitivity', 'matrices') ?? [];
  const top = topLevelSensitivityMatrix(outputs);
  let max: number | null = null;
  const consider = (vals: unknown) => {
    if (!Array.isArray(vals)) return;
    for (const v of vals) {
      if (typeof v === 'number' && Number.isFinite(v)) max = max == null ? v : Math.max(max, v);
    }
  };
  for (const m of [...named, top]) {
    if (!m) continue;
    if (m.col_variable === 'revpar_growth') consider(m.cols);
    if (m.row_variable === 'revpar_growth') consider(m.rows);
  }
  return max;
}

/** SANDBOX_FIELDS with the RevPAR slider aligned to the live matrix axis. */
function sandboxFieldsFor(outputs: EngineOutputs): SandboxField[] {
  const axisMax = revparAxisMax(outputs);
  return SANDBOX_FIELDS.map((f) => {
    if (f.key !== 'revparGrowth' || axisMax == null || axisMax <= f.min) return f;
    return { ...f, max: Math.max(f.max, axisMax) };
  });
}

// ── Session-scoped sandbox persistence (FON-68 §1, Sam: "recommend preserving
// the active sandbox state while navigating within the deal until the user
// explicitly resets it"). Keyed per deal, held in sessionStorage — never a URL
// param and never written to the deal record, so it cannot outlive the tab or
// leak into another analyst's session. Cleared by Reset to base case, by a deal
// change, and by a canonical re-run (a new base). ──
const SANDBOX_STORAGE_PREFIX = 'fondok:returns-sandbox:';
const sandboxStorageKey = (dealId: string) => `${SANDBOX_STORAGE_PREFIX}${dealId}`;

function readPersistedSandbox(dealId: string): SandboxValues | null {
  if (typeof window === 'undefined' || !dealId) return null;
  try {
    const raw = window.sessionStorage.getItem(sandboxStorageKey(dealId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Record<SandboxKey, unknown>>;
    const out = {} as SandboxValues;
    for (const f of SANDBOX_FIELDS) {
      const v = parsed[f.key];
      // A partial or non-numeric blob is not a sandbox — fall back to base
      // rather than reconstructing half of one.
      if (typeof v !== 'number' || !Number.isFinite(v)) return null;
      out[f.key] = v;
    }
    return out;
  } catch {
    return null;
  }
}

function writePersistedSandbox(dealId: string, values: SandboxValues | null): void {
  if (typeof window === 'undefined' || !dealId) return;
  try {
    if (values == null) window.sessionStorage.removeItem(sandboxStorageKey(dealId));
    else window.sessionStorage.setItem(sandboxStorageKey(dealId), JSON.stringify(values));
  } catch {
    /* private mode / blocked storage — the sandbox still works in-memory */
  }
}

// The returns engine's ``inputs.assumptions`` key behind each slider.
const SANDBOX_ENGINE_KEY: Record<SandboxKey, string> = {
  exitCapRate: 'exit_cap_rate',
  revparGrowth: 'revpar_growth',
  holdYears: 'hold_years',
  ltv: 'ltv',
  interestRate: 'interest_rate',
};

interface SandboxBase {
  /** The canonical slider base, or null when the run did not record every key. */
  values: SandboxValues | null;
  /** Fully-qualified engine fields (`returns.inputs.assumptions.<key>`) the run
   *  did not record — named in the placeholder so the gap is actionable. */
  missing: string[];
}

// Read the canonical slider base case straight off the returns engine's
// persisted ``inputs.assumptions`` blob — the SAME canonical run the headline
// reads — so the sandbox starts on the deal's real numbers with no dependency
// on the page assumptions provider. There is deliberately NO client-side
// default: a run that did not record one of the five keys has no base for the
// sandbox, and the sliders are replaced by an "Awaiting model run" notice
// (standing rule — a default would be a number nobody underwrote).
function readSandboxBase(outputs: EngineOutputs): SandboxBase {
  const rIn = ((outputs?.engines?.returns?.inputs as Record<string, unknown> | undefined)
    ?.assumptions ?? {}) as Record<string, unknown>;
  const values = {} as SandboxValues;
  const missing: string[] = [];
  for (const f of SANDBOX_FIELDS) {
    const engineKey = SANDBOX_ENGINE_KEY[f.key];
    const v = rIn[engineKey];
    if (typeof v === 'number' && Number.isFinite(v)) {
      values[f.key] = f.key === 'holdYears' ? Math.round(v) : v;
    } else {
      missing.push(`returns.inputs.assumptions.${engineKey}`);
    }
  }
  return missing.length === 0 ? { values, missing } : { values: null, missing };
}

function sandboxDiffers(a: SandboxValues | null, b: SandboxValues | null): boolean {
  if (a == null || b == null) return a !== b;
  return (Object.keys(a) as SandboxKey[]).some((k) => Math.abs(a[k] - b[k]) > 1e-9);
}

interface SandboxState {
  /** Null when the canonical run did not record a complete slider base. */
  sandbox: SandboxValues | null;
  setSandbox: React.Dispatch<React.SetStateAction<SandboxValues | null>>;
  base: SandboxValues | null;
  /** Engine fields the run did not record (empty when `base` is non-null). */
  baseMissing: string[];
  dirty: boolean;
  preview: ReturnsPreviewResponse | null;
  previewing: boolean;
  resetToBase: () => void;
}

function useReturnsSandbox(outputs: EngineOutputs, dealId: string): SandboxState {
  const { values: base, missing: baseMissing } = useMemo(() => readSandboxBase(outputs), [outputs]);
  const [sandbox, setSandbox] = useState<SandboxValues | null>(base);
  const prevBaseRef = useRef(base);
  // Restore this deal's persisted sandbox — AFTER mount, because
  // sessionStorage does not exist during SSR and reading it in the state
  // initializer would desync hydration. Runs once per deal id: navigating to a
  // different deal drops the previous deal's sandbox rather than carrying it.
  const restoredForRef = useRef<string | null>(null);
  useEffect(() => {
    if (restoredForRef.current === dealId) return;
    restoredForRef.current = dealId;
    // No base on this run → no sandbox, whatever an older run left behind.
    if (base == null) { setSandbox(null); return; }
    const saved = readPersistedSandbox(dealId);
    setSandbox(saved ?? base);
  }, [dealId, base]);
  // Follow a new canonical base (e.g. after a real re-run) only when the user
  // hasn't started testing an override; otherwise their sandbox persists across
  // a background refetch.
  useEffect(() => {
    const prev = prevBaseRef.current;
    if (sandboxDiffers(base, prev)) {
      // A genuinely new canonical base — a re-run landed. The stale sandbox is
      // no longer "an override of THIS run", so it is cleared from storage too.
      setSandbox((cur) => {
        if (base == null) {
          writePersistedSandbox(dealId, null);
          return null;
        }
        if (cur != null && prev != null && sandboxDiffers(cur, prev)) return cur;
        writePersistedSandbox(dealId, null);
        return base;
      });
    }
    prevBaseRef.current = base;
  }, [base, dealId]);

  const dirty = base != null && sandbox != null && sandboxDiffers(sandbox, base);

  // Persist (or clear) whenever the sandbox settles. Writing only after the
  // restore has run keeps a first paint from stamping the base over a saved
  // sandbox.
  useEffect(() => {
    if (restoredForRef.current !== dealId) return;
    writePersistedSandbox(dealId, dirty ? sandbox : null);
  }, [dealId, sandbox, dirty]);
  const [preview, setPreview] = useState<ReturnsPreviewResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // Debounced, non-persisting preview call. Clears when the sandbox is back on
  // the base case. Aborts in-flight requests as the slider moves.
  useEffect(() => {
    if (!dirty || sandbox == null) {
      setPreview(null);
      setPreviewing(false);
      return;
    }
    if (!isWorkerConnected() || !dealId) return;
    const ctrl = new AbortController();
    setPreviewing(true);
    const t = setTimeout(async () => {
      try {
        const res = await api.engines.returnsPreview(
          dealId,
          {
            overrides: {
              exit_cap_rate: sandbox.exitCapRate,
              revpar_growth: sandbox.revparGrowth,
              hold_years: sandbox.holdYears,
              ltv: sandbox.ltv,
              interest_rate: sandbox.interestRate,
            },
          },
          ctrl.signal,
        );
        setPreview(res);
      } catch {
        // Silent — keep the last good preview; the slider stays usable.
      } finally {
        setPreviewing(false);
      }
    }, 250);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [dirty, dealId, sandbox]);

  const resetToBase = () => {
    writePersistedSandbox(dealId, null);
    setSandbox(base);
  };
  return { sandbox, setSandbox, base, baseMissing, dirty, preview, previewing, resetToBase };
}

// ───────────────────────────────────────────────────────────────────
// Workspace — canonical single-column layout (Returns Tab.dc.html):
// title card → Data Key strip → sub-tab nav → override banner → sub-tab
// content. Rebuilt on the shared design system (@/components/design).
// ───────────────────────────────────────────────────────────────────

function ReturnsWorkspace({
  outputs,
  dealId,
  onInternalNav,
}: {
  outputs: EngineOutputs;
  dealId: string;
  /** Called before a view change made inside the section, so the section
   *  does not scroll the page to its own top on the resulting URL change. */
  onInternalNav?: () => void;
}) {
  const router = useRouter();
  // `?tab=overview&sub=<slug>` (R-061; legacy `?tab=returns&sub=` redirects
  // here) — deep-linkable, back-button correct. The sandbox lives above this
  // hook and is keyed on the deal, so switching views (a `router.replace`, no
  // remount) never drops an override.
  const { sub: tab, setSub: setSubRaw } = useSubTab(SUB_TAB_IDS, 'returns-summary');
  const setTab = (id: SubTab) => { onInternalNav?.(); setSubRaw(id); };
  const { sandbox, setSandbox, base, baseMissing, dirty, preview, previewing, resetToBase } =
    useReturnsSandbox(outputs, dealId);

  const subTabCaption =
    tab === 'returns-summary'
      ? 'What the deal earns and where it comes from'
      : tab === 'sensitivities'
        ? 'How returns move with the key assumptions'
        : 'What price the deal can carry';

  // One fields list for the banner and the sliders, with the RevPAR range read
  // off the live sensitivity matrix so the two can never disagree.
  const fields = useMemo(() => sandboxFieldsFor(outputs), [outputs]);
  const overrideSummary =
    base != null && sandbox != null
      ? fields
          .filter((f) => Math.abs(sandbox[f.key] - base[f.key]) > 1e-9)
          .map((f) => `${f.label} ${f.fmt(base[f.key])} → ${f.fmt(sandbox[f.key])}`)
          .join(' · ')
      : '';

  // FON-59 #4 — name the sub-tab that holds the value. "Edit in Investment →"
  // sits on Exit Assumptions, which are Investment → Deal Summary rows.
  const goInvestment = () => router.push(`/projects/${dealId}?tab=investment&sub=deal-summary`, { scroll: false });
  const goCashFlow = () => router.push(`/projects/${dealId}?tab=cash-flow&sub=summary`, { scroll: false });
  // FON-68 — the pricing hurdles live on Overview → Investment Profile, which
  // is on this same page now (R-061): scroll up to it instead of navigating.
  const goProfile = () => {
    const el = typeof document !== 'undefined' ? document.getElementById('overview-investment-profile') : null;
    if (el) el.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
    else router.push(`/projects/${dealId}?tab=overview`, { scroll: false });
  };

  return (
    <div style={{ maxWidth: 1320 }}>
      {/* Section header — R-061: the former Returns tab's title card, now the
          heading of Overview's Returns section. */}
      <div
        style={{
          background: palette.cardWhite,
          border: `1px solid ${palette.border}`,
          borderRadius: radius.card,
          padding: '12px 16px',
          marginBottom: 14,
          display: 'flex',
          flexDirection: 'column',
          gap: 3,
        }}
      >
        <span style={{ fontSize: 13.5, fontWeight: 700, color: palette.ink }}>Returns</span>
        <span
          style={{ fontSize: 12.5, color: palette.textSecondary, lineHeight: 1.55, maxWidth: 960 }}
        >
          What the deal earns, what drives those returns, and what price it can carry while still
          clearing the hurdles. Calculated from the Cash Flow series — nothing is underwritten here.
        </span>
      </div>

      {/* FON-63 — negative-NOI warning, the same one-liner the Debt tab shows. */}
      <NoiWarningStrip outputs={outputs} />

      <SubTabNav
        items={SUB_TABS.map((t) => ({ id: t.id, label: t.label }))}
        activeId={tab}
        onSelect={(id) => setTab(id as SubTab)}
        caption={subTabCaption}
        style={{ marginBottom: 14 }}
      />

      {/* Override banner — canonical: spans all sub-tabs while an override is
          active. LOCAL sandbox only; Investment / Debt are untouched. */}
      {dirty && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            flexWrap: 'wrap',
            background: 'oklch(97% 0.03 250)',
            border: '1px solid #c9d4ee',
            borderRadius: 8,
            padding: '9px 14px',
            marginBottom: 14,
            fontSize: 12,
            color: palette.ink,
          }}
        >
          <span
            style={{
              fontSize: 10,
              fontWeight: 700,
              letterSpacing: '.05em',
              color: palette.linkBlue,
              textTransform: 'uppercase',
              whiteSpace: 'nowrap',
            }}
          >
            Sensitivity override active
          </span>
          {overrideSummary && <span>{overrideSummary}</span>}
          <span style={{ color: palette.textSecondary }}>
            Testing only — the canonical assumptions in CAPEX and Financing are unchanged.
          </span>
          <button
            onClick={resetToBase}
            style={{
              marginLeft: 'auto',
              background: 'none',
              border: 'none',
              fontFamily: 'inherit',
              color: palette.linkBlue,
              fontWeight: 600,
              cursor: 'pointer',
              whiteSpace: 'nowrap',
            }}
          >
            Reset to base case
          </button>
        </div>
      )}

      {tab === 'returns-summary' && (
        <ReturnsSummary
          outputs={outputs}
          preview={preview}
          dirty={dirty}
          onEditInvestment={goInvestment}
          onViewCashFlow={goCashFlow}
        />
      )}
      {tab === 'sensitivities' && (
        <Sensitivities
          outputs={outputs}
          fields={fields}
          sandbox={sandbox}
          setSandbox={setSandbox}
          baseMissing={baseMissing}
          dirty={dirty}
          preview={preview}
          previewing={previewing}
          resetToBase={resetToBase}
        />
      )}
      {tab === 'pricing' && (
        <PricingSubTab
          dealId={dealId}
          outputs={outputs}
          sandboxActive={dirty}
          onGoToProfile={goProfile}
        />
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Pricing — canonical order: Max Price Solver block, then the max-price
// grid (FON-68). Both read the hurdles from the deal record (Target
// Levered IRR / Target MOIC on the Investment Profile); neither carries a
// local hurdle input or a default. Mounted only on this sub-tab so the
// deal fetch happens when the analyst actually opens Pricing.
// ───────────────────────────────────────────────────────────────────

function PricingSubTab({
  dealId,
  outputs,
  sandboxActive = false,
  onGoToProfile,
}: {
  dealId: string;
  outputs: EngineOutputs;
  /** True while a Live-Assumptions sandbox is active. Pricing does NOT consume
   *  it (a "max price under a hypothetical LTV" is a different question), so
   *  both panels say so inline rather than silently answering the other one. */
  sandboxActive?: boolean;
  onGoToProfile: () => void;
}) {
  const { deal } = useDeal(dealId);
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <MaxPricePanel
        dealId={dealId}
        deal={deal}
        outputs={outputs}
        sandboxActive={sandboxActive}
        onGoToProfile={onGoToProfile}
      />
      <PricingSensitivityPanel
        dealId={dealId}
        deal={deal}
        sandboxActive={sandboxActive}
        onGoToProfile={onGoToProfile}
      />
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Returns Summary — headline KPIs (WORKER outputs only) + Exit Assumptions
// and Return Bridge cards (the two canonical cards previously missing).
// ───────────────────────────────────────────────────────────────────

function ReturnsSummary({
  outputs,
  preview,
  dirty = false,
  onEditInvestment,
  onViewCashFlow,
}: {
  outputs: EngineOutputs;
  /** The non-persisting sandbox preview, when one is active. */
  preview?: ReturnsPreviewResponse | null;
  /** True while the Live-Assumptions sandbox differs from the base case. */
  dirty?: boolean;
  onEditInvestment: () => void;
  onViewCashFlow: () => void;
}) {
  // FON-68 §1 — with a sandbox active the banner announced an override while
  // these tiles kept showing the canonical run. Every headline figure now reads
  // the preview while dirty and carries a "Sandbox" chip, so the number and the
  // banner can never tell two different stories. `sb` falls back to the
  // canonical value whenever the preview has not returned that field.
  const sandboxOn = dirty && preview != null;
  const sb = <T,>(previewValue: T | null | undefined, canonical: T | undefined): T | undefined =>
    sandboxOn && previewValue != null ? previewValue : canonical;

  // ── Hero row (4 navy tiles) — WORKER outputs only, no client TS fallback. ──
  // FON-118 — every headline figure comes through the ONE reader
  // (`lib/engines/returns.ts`); the headline IS the selected row of the exit
  // schedule, and nothing here derives a return or an exit year.
  const head = headlineReturns(getEngineField<unknown>(outputs, 'returns'));
  // A sentinel (the monthly solver's 10.0 / −0.9999) is never printed as a
  // rate — on the canonical run or the sandbox preview.
  const irrRaw = sandboxOn && preview?.levered_irr != null ? preview.levered_irr : head.leveredIrr;
  const irr = irrValue(irrRaw) ?? undefined;
  const irrUnavailable =
    irr != null
      ? undefined
      : irrRaw != null
        ? IRR_SENTINEL_NOTE
        : head.leveredIrrNote ?? awaitingRun('returns.levered_irr');
  const mult = sb(preview?.equity_multiple, head.equityMultiple ?? undefined);
  // Canonical headline shows the HOLD-AVERAGE cash-on-cash (``avg_coc``), not
  // year 1 — both exist on the returns engine (returns.py). year_one_coc feeds
  // the sublabel only.
  const avgCoc = sb(preview?.avg_coc, head.avgCoc ?? undefined);
  const yearOneCoc = sb(preview?.year_one_coc, head.yearOneCoc ?? undefined);
  const holdYears = sb(preview?.hold_years, head.holdYears ?? undefined);
  // The hero's period: the selected exit, from the reader. A mid-year
  // disposition names its month; a year-end exit before the hold's last year
  // names that year; only an exit at the hold reads "over the N-year hold".
  // Canonical run only — the sandbox may have moved the hold.
  const irrPeriodSub =
    !sandboxOn && head.selectedExitMonth != null && head.selectedExitYear == null
      ? `Equity IRR to the month-${head.selectedExitMonth} disposition`
      : !sandboxOn && head.selectedExitYear != null && head.selectedExitYear !== holdYears
        ? `Equity IRR to the Year ${head.selectedExitYear} exit (month ${head.selectedExitMonth})`
        : holdYears != null
          ? `Equity IRR over the ${holdYears}-year hold`
          : 'Equity IRR after debt service';

  // Yield on Cost — DERIVED (no stored field): stabilized NOI ÷ total cost
  // basis. Stabilized NOI is THE stabilized year's NOI before the FF&E reserve,
  // off the ONE block the expense engine publishes (`expense.stabilization`,
  // FON-41 / FON-59 #3) — the same year and basis the Overview's Stabilized NOI
  // row, its Yield on Cost row and its YoC benchmark print. It used to be the
  // LAST hold year's after-reserve cash NOI (`returns.noi_by_year[-1]`), so the
  // same page showed two different "Yield on Cost" figures under one caption.
  // Total cost basis = `capital.total_capital`. Either unsourceable → '—' with
  // the reason, never a borrowed year. A sandbox preview carries no
  // stabilization block, so the tile is withheld while one is active rather
  // than printing a canonical figure under a "Sandbox" chip.
  const stab = stabilizedYearBlock(getEngineField<unknown>(outputs, 'expense'));
  const stabilizedNoi =
    stab?.stabilized_noi_before_reserve != null && Number.isFinite(stab.stabilized_noi_before_reserve)
      ? stab.stabilized_noi_before_reserve
      : undefined;
  const totalCapital = getEngineField<number>(outputs, 'capital', 'total_capital');
  const yieldOnCost =
    !sandboxOn && stabilizedNoi != null && totalCapital != null && totalCapital > 0
      ? stabilizedNoi / totalCapital
      : undefined;
  const yieldOnCostUnavailable =
    yieldOnCost != null
      ? undefined
      : sandboxOn
        ? 'Sandbox case — Yield on Cost reads the canonical run’s stabilized year (expense.stabilization); reset to base to see it'
        : stabilizedNoi == null
          ? awaitingRun('expense.stabilization.stabilized_noi_before_reserve')
          : awaitingRun('capital.total_capital');

  // ── Secondary row (3 white cards). Equity Profit + Initial Equity read off
  // the canonical levered cash-flow series so they reconcile to the bridge. ──
  const flows = sb(preview?.cash_flows, head.cashFlows ?? undefined);
  const exitValue = sb(preview?.exit_value, head.grossSalePrice ?? undefined);
  const exitCap = sb(preview?.exit_cap_rate, head.exitCapRate ?? undefined);
  // FON-44 (R-059) — the basis + period of the NOI the exit capitalizes, in
  // the ONE spelling the Overview's Exit row uses (`exitNoiLabel`): the run's
  // own label when it carries one, else the pre-R-059 forward-12 wording that
  // every such run ran on. Canonical run only: a sandbox preview can move the
  // hold, and with it the period, so the caption is withheld while a preview
  // is showing — never a basis the preview may not have used.
  const exitNoiCaption = sandboxOn ? undefined : exitNoiLabel(head.exitNoiPeriodLabel);
  // FON-118 — the schedule by exit year. The canonical run's rows, or — while
  // a sandbox preview is showing — the preview's own rows and nothing else
  // (a preview from a worker build without them says so; the canonical table
  // is never shown under a sandbox case). `undefined` = none on this run.
  const calendarYears = getEngineField<number[]>(outputs, 'revenue', 'projection_calendar_years');
  const scheduleRows = sandboxOn
    ? preview?.exit_schedule != null
      ? exitScheduleRows(preview.exit_schedule)
      : undefined
    : head.hasExitSchedule
      ? head.exitSchedule
      : undefined;
  // FON-98 — the stabilized year's anchor, as the Overview states it, under the
  // Yield on Cost tile; the Sam #7 exception note travels with it.
  const yocAnchor = sandboxOn ? null : stabilizationAnchorCaption(stab);
  const yocAnchorNote = sandboxOn ? null : stabilizationAnchorFallbackNote(stab);
  const hasFlows = Array.isArray(flows) && flows.length >= 2;
  const initialEquity = hasFlows ? -flows![0] : undefined; // −close-period outflow
  const totalToEquity = hasFlows ? flows!.slice(1).reduce((a, b) => a + b, 0) : undefined;
  const equityProfit =
    totalToEquity != null && initialEquity != null ? totalToEquity - initialEquity : undefined;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Hero — 4 dark-navy tiles (canonical `headline`). */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <span
          style={{
            fontSize: 10.5,
            fontWeight: 700,
            letterSpacing: '.06em',
            color: palette.eyebrow,
            textTransform: 'uppercase',
          }}
        >
          Deal-level returns · before GP/LP allocation
          {sandboxOn && (
            <span style={{ marginLeft: 8, color: palette.linkBlue, letterSpacing: '.04em' }}>
              · sandbox case — CAPEX and Financing are unchanged
            </span>
          )}
        </span>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 12 }}>
          <CoachMark
            anchorId="returns-levered-irr"
            viewKey="returns"
            order={0}
            title="Why this number leads"
            body="Levered IRR is the most institutionally cited return — it captures what your equity actually earns after debt service. Fondok solves it with Newton's method and a bisection fallback for numerical stability, same approach Argus uses."
            side="top"
            learnMoreHref="/methodology#engines"
          >
            <ReturnsKpi
              variant="navy"
              label="Levered IRR"
              sandbox={sandboxOn}
              engine="returns"
              path="levered_irr"
              flashKey={irr}
              sub={irrPeriodSub}
              value={fmtP(irr)}
              unavailable={irrUnavailable}
            />
          </CoachMark>
          <ReturnsKpi
            variant="navy"
            label="Equity Multiple"
            sandbox={sandboxOn}
            engine="returns"
            path="equity_multiple"
            flashKey={mult}
            sub={initialEquity != null ? `MOIC on ${mm(initialEquity)} invested` : 'MOIC — total distributions ÷ equity'}
            value={fmtX(mult)}
            unavailable={mult == null ? awaitingRun('returns.equity_multiple') : undefined}
          />
          <ReturnsKpi
            variant="navy"
            label="Avg. Cash-on-Cash"
            sandbox={sandboxOn}
            engine="returns"
            path="avg_coc"
            flashKey={avgCoc}
            sub={
              holdYears != null && yearOneCoc != null
                ? `Years 1–${holdYears} average — year 1 alone is ${fmtPct(yearOneCoc, 2)}`
                : 'Hold-average annual cash yield'
            }
            value={fmtP(avgCoc)}
            unavailable={avgCoc == null ? awaitingRun('returns.avg_coc') : undefined}
          />
          <ReturnsKpi
            variant="navy"
            label="Yield on Cost"
            sandbox={sandboxOn}
            flashKey={yieldOnCost}
            sub={
              <>
                <span>
                  {stab
                    ? `Stabilized NOI (Year ${stab.stabilized_year}, before FF&E reserve) ÷ total capital`
                    : 'Stabilized NOI (before FF&E reserve) ÷ total capital'}
                </span>
                {yocAnchor && (
                  <span data-testid="yoc-anchor" style={{ display: 'block', marginTop: 3 }}>
                    Stabilized year: {yocAnchor}
                  </span>
                )}
                {yocAnchorNote && (
                  <span data-testid="yoc-anchor-note" style={{ display: 'block', marginTop: 3 }}>
                    {yocAnchorNote}
                  </span>
                )}
              </>
            }
            value={fmtP(yieldOnCost)}
            unavailable={yieldOnCostUnavailable}
          />
        </div>
      </div>

      {/* Secondary — 3 white cards (canonical `secondary`). */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 12 }}>
        <ReturnsKpi
          label="Exit Value"
          sandbox={sandboxOn}
          engine="returns"
          path="gross_sale_price"
          flashKey={exitValue}
          sub={
            exitCap != null
              ? `${fmtPct(exitCap, 2)} exit cap${exitNoiCaption ? ` · ${exitNoiCaption}` : ''}`
              : exitNoiCaption
                ? `Gross sale — ${exitNoiCaption} ÷ exit cap`
                : 'Gross sale — exit NOI ÷ exit cap'
          }
          value={fmtM(exitValue)}
          unavailable={exitValue == null ? awaitingRun('returns.gross_sale_price') : undefined}
        />
        <ReturnsKpi
          label="Equity Profit"
          sandbox={sandboxOn}
          flashKey={equityProfit}
          sub="Cash returned less equity invested"
          value={fmtM(equityProfit)}
          unavailable={equityProfit == null ? awaitingRun('returns.cash_flows') : undefined}
        />
        <ReturnsKpi
          label="Initial Equity Invested"
          sandbox={sandboxOn}
          flashKey={initialEquity}
          sub="Funded in full at close"
          value={fmtM(initialEquity)}
          unavailable={initialEquity == null ? awaitingRun('returns.cash_flows') : undefined}
        />
      </div>

      <ExitScheduleCard rows={scheduleRows} calendarYears={calendarYears} sandbox={sandboxOn} />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(430px,1fr))', gap: 14 }}>
        <ExitAssumptionsCard outputs={outputs} onEdit={onEditInvestment} />
        <ReturnBridgeCard outputs={outputs} onViewCashFlow={onViewCashFlow} />
      </div>
    </div>
  );
}

// One KPI tile on the shared KpiTile chrome, with the flash-on-change highlight
// (useFlash) and — when an engine/path is given — the <Traced> computed-value
// popover so the number stays interrogable. The canonical Returns Summary tiles
// carry no always-on provenance dot (provenance stays available via <Traced>).
function ReturnsKpi({
  label,
  value,
  sub,
  flashKey,
  engine,
  path,
  variant = 'white',
  sandbox = false,
  unavailable,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  flashKey?: unknown;
  engine?: string;
  path?: string;
  variant?: 'white' | 'navy';
  /** Renders the "Sandbox" chip: this tile is showing the Live-Assumptions
   *  preview, not the canonical run. */
  sandbox?: boolean;
  /** Set when the engine did not produce this figure: the tile reads "—",
   *  the reason (naming the missing engine field) becomes the sub-label and
   *  the tooltip, and no provenance popover is offered for a number that is
   *  not there. */
  unavailable?: string;
}) {
  const flash = useFlash(flashKey ?? value);
  const shown: ReactNode = unavailable ? (
    <span title={unavailable} data-unavailable="true">
      —
    </span>
  ) : (
    value
  );
  // Provenance points at the canonical run, so it is suppressed on a sandbox
  // tile — a trace popover explaining a number the tile is not showing is
  // worse than none. Likewise on an unavailable tile.
  const traced =
    engine && path && !sandbox && !unavailable ? (
      <Traced engine={engine} path={path}>
        {shown}
      </Traced>
    ) : (
      shown
    );
  const body = sandbox ? (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
      {traced}
      <SandboxChip variant={variant} />
    </span>
  ) : (
    traced
  );
  return (
    <KpiTile
      className={cn(flash && 'value-flash')}
      style={{ height: '100%' }}
      variant={variant}
      label={label}
      sub={unavailable ?? sub}
      value={body}
    />
  );
}

/** "Sandbox" — this figure is the ephemeral preview, not the canonical run. */
function SandboxChip({ variant = 'white' }: { variant?: 'white' | 'navy' }) {
  const navy = variant === 'navy';
  return (
    <span
      title="Live-Assumptions sandbox — the canonical assumptions in CAPEX and Financing are unchanged"
      style={{
        fontSize: 9.5,
        fontWeight: 700,
        letterSpacing: '.06em',
        textTransform: 'uppercase',
        padding: '2px 6px',
        borderRadius: 999,
        whiteSpace: 'nowrap',
        color: navy ? '#cddbff' : palette.linkBlue,
        background: navy ? 'rgba(159,178,223,.22)' : 'oklch(97% 0.03 250)',
        border: `1px solid ${navy ? 'rgba(205,219,255,.35)' : '#c9d4ee'}`,
      }}
    >
      Sandbox
    </span>
  );
}

// ── Exit Assumptions — read-only; exit cap / hold / etc. are Investment- and
// Debt-owned, so they carry an "Edit in CAPEX →" link. Values from the returns
// engine outputs (no fabrication; missing values render as —). The Hold
// Period and Exit Cap badges read the worker's `assumption_sources` tag for
// the key — document-sourced, an assumption (override / default), or, with no
// tag, "Source unknown" — exactly as the Overview's assumption rows do. They
// are never hard-coded "linked" (go-live Wave 0, B1). ──
function ExitAssumptionsCard({ outputs, onEdit }: { outputs: EngineOutputs; onEdit: () => void }) {
  const head = headlineReturns(getEngineField<unknown>(outputs, 'returns'));
  const holdYears = head.holdYears ?? undefined;
  const exitCap = head.exitCapRate ?? undefined;
  const holdSource = useSource('hold_years');
  // FON-44 §3.2 — a hold the deal type set (7 development / 6 adaptive reuse)
  // is a labelled default: badged, with the reason, until the analyst confirms
  // or overrides it. Pattern: STABILIZATION_DEFAULT_BADGE (lib/engines/noi.ts).
  const holdBadge = holdDefaultBadge(holdSource?.source);
  const exitCapSource = useSource('exit_cap_rate');
  const taggedRow = (resolved: { source: string } | null) => {
    const src = resolved?.source || null;
    const state = valueStateForSource(src);
    return {
      state,
      source: src ? sourceLabel(src) : 'Source unknown',
      color: state === 'document_sourced' ? prov.green : state === 'assumption' ? prov.blue : palette.textFaint,
      title: src ? sourceExplanation(src) : 'No source recorded for this value on this run',
    };
  };
  const holdTag = taggedRow(holdSource);
  const exitCapTag = taggedRow(exitCapSource);
  const grossSale = head.grossSalePrice ?? undefined;
  const sellingCosts = head.sellingCosts ?? undefined;
  const exitNoiPeriod = head.exitNoiPeriodLabel ?? undefined;
  // Net Sale Proceeds here is GROSS − SELLING (computed client-side) so the
  // three visible rows foot. (The engine's ``net_proceeds`` is net-to-equity —
  // gross less selling, transfer tax AND the loan payoff — which would not
  // reconcile with only the two rows above it; that figure lives in the Return
  // Bridge's "Net exit proceeds".)
  const netSaleProceeds =
    grossSale != null && sellingCosts != null
      ? grossSale - Math.abs(sellingCosts)
      : undefined;

  interface Row {
    label: string;
    value: string;
    source: string;
    state: ValueState;
    color: string;
    weight: number;
    title: string;
    /** `assumption_sources` key — wraps the value in <Sourced> for the hover. */
    sourceKey?: string;
  }
  const rows: Row[] = [
    {
      label: 'Hold Period',
      value: holdYears != null ? `${holdYears} years` : '—',
      ...holdTag,
      source: holdBadge ?? holdTag.source,
      title: holdBadge ? HOLD_DEAL_TYPE_DEFAULT_EXPLANATION : holdTag.title,
      weight: 400,
      sourceKey: 'hold_years',
    },
    {
      // FON-118 — the selected exit the engine published, never "Year {hold}"
      // (wrong for a mid-year disposition). A run that predates it is a dash
      // naming the field.
      label: 'Exit Year',
      value: selectedExitLabel(head) ?? '—',
      source: 'Calculated',
      state: 'calculated',
      color: prov.gray,
      weight: 400,
      title: selectedExitTitle(head),
    },
    {
      label: 'Exit Cap Rate',
      value: exitCap != null ? fmtPct(exitCap, 2) : '—',
      ...exitCapTag,
      weight: 400,
      sourceKey: 'exit_cap_rate',
    },
    {
      label: 'Gross Sale Price',
      value: grossSale != null ? money(grossSale) : '—',
      source: 'Calculated',
      state: 'calculated',
      color: prov.black,
      weight: 700,
      // Same spelling as the Overview's Exit row and the Exit Value tile.
      title: `${exitNoiLabel(exitNoiPeriod)} ÷ exit cap rate`,
    },
    {
      label: 'Selling Costs',
      value: sellingCosts != null ? money(-Math.abs(sellingCosts)) : '—',
      source: 'Calculated',
      state: 'calculated',
      color: prov.gray,
      weight: 400,
      title: 'Disposition costs and transfer tax',
    },
    {
      label: 'Net Sale Proceeds',
      value: netSaleProceeds != null ? money(netSaleProceeds) : '—',
      source: 'Calculated',
      state: 'calculated',
      color: prov.black,
      weight: 700,
      title: 'Gross sale price less selling costs',
    },
  ];

  return (
    <SectionCard
      title="Exit Assumptions"
      note={
        <span
          role="button"
          tabIndex={0}
          onClick={onEdit}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') onEdit();
          }}
          style={{ color: palette.linkBlue, fontWeight: 600, cursor: 'pointer', fontSize: 11.5 }}
        >
          Edit in CAPEX →
        </span>
      }
    >
      {rows.map((r) => (
        <div
          key={r.label}
          title={r.title}
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 12,
            fontSize: 13,
            padding: '7px 0',
            borderBottom: `1px solid ${palette.hairlineRow}`,
          }}
        >
          <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
            <ProvenanceDot state={r.state} size={8} />
            <span style={{ color: palette.textSecondary }}>{r.label}</span>
          </span>
          <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ color: r.color, fontWeight: r.weight, fontVariantNumeric: 'tabular-nums' }}>
              {r.sourceKey ? <Sourced sourceKey={r.sourceKey}>{r.value}</Sourced> : r.value}
            </span>
            <span
              style={{ fontSize: 10.5, color: palette.textFaint, whiteSpace: 'nowrap' }}
              data-testid={r.sourceKey ? `exit-assumption-source-${r.sourceKey}` : undefined}
            >
              {r.source}
            </span>
          </span>
        </div>
      ))}
    </SectionCard>
  );
}

// ── Return Bridge — deal-level returns, before GP/LP allocation. Anchored to
// the levered cash-flow series the returns engine emits (``cash_flows`` =
// [-equity, cf₁ … cfₙ + net exit proceeds]) so every number reconciles to Cash
// Flow. Canonical 6-row breakdown: initial equity (−) · operating cash flow (+)
// · debt service (−) · refinance proceeds (+) · net exit proceeds (+) · total.
// "View cash flow →" jumps to the full series. ──
function ReturnBridgeCard({
  outputs,
  onViewCashFlow,
}: {
  outputs: EngineOutputs;
  onViewCashFlow: () => void;
}) {
  const flows = getEngineField<number[]>(outputs, 'returns', 'cash_flows');
  const netProceeds = getEngineField<number>(outputs, 'returns', 'net_proceeds');
  const noiByYear = getEngineField<number[]>(outputs, 'returns', 'noi_by_year');
  const refiCashOut = getEngineField<number>(outputs, 'debt', 'refi_cash_out');
  const refiYear = getEngineField<number>(outputs, 'debt', 'refi_year');
  // The composed, reconciled levered statement — its Interest Expense +
  // Principal Amortization (+ any Refinance/Junior debt-service delta) rows are
  // the debt-service figure this bridge displays.
  const leveredStmt = getEngineField<LeveredStatementLine[]>(outputs, 'cash_flow', 'levered');

  const note = (
    <span
      role="button"
      tabIndex={0}
      onClick={onViewCashFlow}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') onViewCashFlow();
      }}
      style={{ color: palette.linkBlue, fontWeight: 600, cursor: 'pointer', fontSize: 11.5 }}
    >
      View cash flow →
    </span>
  );

  if (!Array.isArray(flows) || flows.length < 2) {
    return (
      <SectionCard title="Return Bridge" note={note}>
        <div style={{ fontSize: 12, color: palette.textMuted, lineHeight: 1.5, paddingTop: 2 }}>
          Deal-level returns · before GP/LP allocation. The cash-flow series is unavailable — run the
          Returns engine to see the bridge.
        </div>
      </SectionCard>
    );
  }

  const initialEquity = flows[0]; // negative outflow at close
  const equity = -initialEquity;
  const totalToEquity = flows.slice(1).reduce((a, b) => a + b, 0);
  // A run without ``net_proceeds`` cannot split the series into its operating
  // and exit legs — those rows read "—" (never a $0 exit that silently folds
  // the sale into operating cash flow). Initial equity and the total still
  // come straight off the series.
  const netExit: number | null = typeof netProceeds === 'number' ? netProceeds : null;
  const NET_EXIT_MISSING = awaitingRun('returns.net_proceeds');
  const refi = refiYear != null && refiCashOut != null && refiCashOut > 0 ? refiCashOut : 0;
  // Net operating cash flow to equity (after debt, before refi/exit) — the leg
  // that operating + debt service must net to.
  const operatingNet: number | null = netExit != null ? totalToEquity - netExit - refi : null;

  // Debt service Σ(Interest + Principal) over the hold. Prefer the reconciled
  // cash_flow levered statement; else reconstruct it exactly from the returns
  // NOI identity (Σ NOI − net operating). Operating cash flow is then the
  // PRE-debt-service figure (operatingNet + debt service), so the bridge always
  // foots to Total cash flow to equity and ties to the levered series.
  const sumStatementRows = (labels: string[]): number | undefined => {
    if (!Array.isArray(leveredStmt)) return undefined;
    let total = 0;
    let found = false;
    for (const line of leveredStmt) {
      if (line && labels.includes(line.label) && Array.isArray(line.values)) {
        found = true;
        for (const v of line.values) if (typeof v === 'number') total += v;
      }
    }
    return found ? total : undefined;
  };
  const dsFromStatement = sumStatementRows([
    'Interest Expense',
    'Principal Amortization',
    'Refinance / Junior Debt Service',
  ]);
  let debtService: number | null = null;
  if (dsFromStatement != null) {
    debtService = Math.abs(dsFromStatement);
  } else if (operatingNet != null && Array.isArray(noiByYear) && noiByYear.length > 0) {
    debtService = noiByYear.reduce((a, b) => a + b, 0) - operatingNet;
  }
  const operatingGross: number | null =
    operatingNet == null ? null : debtService != null ? operatingNet + debtService : operatingNet;

  interface Bar {
    label: string;
    /** Null only when `awaiting` is set. */
    value: number | null;
    bold?: boolean;
    /** The row is listed (the design always shows it) but reads '—'; the
     *  string names why and is the row's tooltip. */
    awaiting?: string;
  }
  const items: Bar[] = [
    { label: 'Initial equity', value: initialEquity },
    {
      label: 'Operating cash flow',
      value: operatingGross,
      awaiting: operatingGross == null ? NET_EXIT_MISSING : undefined,
    },
    {
      label: 'Debt service',
      value: debtService != null ? -debtService : null,
      awaiting:
        debtService != null
          ? undefined
          : operatingNet == null
            ? NET_EXIT_MISSING
            : 'Awaiting model run — neither the cash_flow levered statement nor returns.noi_by_year is on this run',
    },
    {
      label: 'Refinance proceeds',
      value: refi,
      awaiting:
        refi > 0
          ? undefined
          : refiYear == null
            ? 'No refinance modelled on this deal (debt.refi_year is not set)'
            : `The year-${refiYear} refinance returns no cash out to equity`,
    },
    { label: 'Net exit proceeds', value: netExit, awaiting: netExit == null ? NET_EXIT_MISSING : undefined },
    { label: 'Total cash flow to equity', value: totalToEquity, bold: true },
  ];

  const maxAbs = Math.max(
    1,
    ...items.filter((b) => !b.awaiting && b.value != null).map((b) => Math.abs(b.value as number)),
  );

  return (
    <SectionCard title="Return Bridge" note={note}>
      <div style={{ marginTop: 2 }}>
        {items.map((b) => {
          const v = b.awaiting || b.value == null ? 0 : b.value;
          const abs = Math.abs(v);
          const width = b.awaiting ? '0%' : `${(abs / maxAbs) * 50}%`;
          const left = v < 0 ? `${50 - (abs / maxAbs) * 50}%` : '50%';
          const barBg = b.bold
            ? palette.inkNavy
            : v < 0
              ? 'oklch(70% 0.10 40)'
              : 'oklch(60% 0.10 155)';
          const valueColor = b.awaiting
            ? palette.textFaint
            : b.bold
              ? prov.black
              : v < 0
                ? prov.amber
                : prov.gray;
          return (
            <div
              key={b.label}
              title={b.awaiting}
              data-awaiting={b.awaiting ? 'true' : undefined}
              style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '5px 0' }}
            >
              <span
                style={{
                  width: 150,
                  fontSize: 12,
                  color: palette.ink,
                  fontWeight: b.bold ? 700 : 400,
                  flexShrink: 0,
                }}
              >
                {b.label}
              </span>
              <span
                style={{
                  flex: 1,
                  height: 10,
                  background: '#f3f2ee',
                  borderRadius: 5,
                  position: 'relative',
                  overflow: 'hidden',
                }}
              >
                {!b.awaiting && (
                  <span
                    style={{ position: 'absolute', left, width, top: 0, bottom: 0, background: barBg, borderRadius: 5 }}
                  />
                )}
              </span>
              <span
                style={{
                  width: 112,
                  textAlign: 'right',
                  fontSize: 12.5,
                  color: valueColor,
                  fontWeight: b.bold ? 700 : 400,
                  fontVariantNumeric: 'tabular-nums',
                  flexShrink: 0,
                }}
              >
                {b.awaiting || b.value == null ? '—' : mm(b.value)}
              </span>
            </div>
          );
        })}
      </div>
      <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 10, lineHeight: 1.5 }}>
        {operatingNet != null && netExit != null ? (
          <>
            Deal-level returns · before GP/LP allocation. Reconciles to the Cash Flow series:{' '}
            {mm(operatingNet)} operating {refi > 0 ? `+ ${mm(refi)} refinance ` : ''}+ {mm(netExit)} exit ={' '}
            {mm(totalToEquity)} returned on {mm(equity)} invested.
          </>
        ) : (
          <span title={NET_EXIT_MISSING}>
            Deal-level returns · before GP/LP allocation. {mm(totalToEquity)} returned on {mm(equity)}{' '}
            invested, per the Cash Flow series. The operating / exit split is awaiting a model run —
            returns.net_proceeds is not on this run.
          </span>
        )}
      </div>
    </SectionCard>
  );
}

// Minimal shape of a composed levered cash-flow statement row (see
// apps/worker/app/engines/cash_flow.py → CashFlowStatementLine). ``values`` is
// indexed 0..hold (index 0 = at close); nulls mark periods with no entry.
interface LeveredStatementLine {
  label: string;
  values: (number | null)[];
}

// ───────────────────────────────────────────────────────────────────
// Sensitivities — Live Assumptions sandbox (sliders + non-persisting preview)
// then the worker sensitivity grids (IRR, Equity Multiple, Year-1 CoC).
// ───────────────────────────────────────────────────────────────────

function Sensitivities({
  outputs,
  fields,
  sandbox,
  setSandbox,
  baseMissing,
  dirty,
  preview,
  previewing,
  resetToBase,
}: {
  outputs: EngineOutputs;
  /** Slider definitions with the RevPAR range read off the live matrix axis. */
  fields: SandboxField[];
  /** Null when the canonical run did not record a complete slider base. */
  sandbox: SandboxValues | null;
  setSandbox: React.Dispatch<React.SetStateAction<SandboxValues | null>>;
  /** The `returns.inputs.assumptions.*` keys the run did not record. */
  baseMissing: string[];
  dirty: boolean;
  preview: ReturnsPreviewResponse | null;
  previewing: boolean;
  resetToBase: () => void;
}) {
  // Canonical base values (worker outputs) for the sandbox result line.
  const irr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const mult = getEngineField<number>(outputs, 'returns', 'equity_multiple');
  const exitValue = getEngineField<number>(outputs, 'returns', 'gross_sale_price');
  const dscrY1 = getEngineField<number>(outputs, 'debt', 'year_one_dscr');

  // What the result line shows: the sandbox preview when an override is active,
  // else the canonical values (so it reconciles with the Summary headline).
  const sIrr = dirty && preview ? preview.levered_irr : irr;
  const sMult = dirty && preview ? preview.equity_multiple : mult;
  const sExit = dirty && preview ? preview.exit_value : exitValue;
  const sDscr = dirty && preview ? preview.dscr_y1 : dscrY1;

  // Worker sensitivity grids ONLY. The canonical engine is the single source of
  // truth — mixing worker grids with client-TS ``defaultSensitivities`` is the
  // exact cross-tab "two different Base" drift the engine-output contract
  // exists to prevent. The engine emits named matrices for Levered IRR, Equity
  // Multiple, and (FON-68 step 5) Year-1 Cash-on-Cash (FON-53 ``matrices[]``);
  // older runs carry only the top-level primary matrix, which we still accept
  // as the IRR grid.
  const cards = useMemo(() => {
    const list = getEngineField<WorkerMatrixRaw[]>(outputs, 'sensitivity', 'matrices') ?? [];
    const byKey = (k: string) => list.find((m) => m?.key === k) ?? null;
    const irrRaw = byKey('irr_exit_revpar') ?? topLevelSensitivityMatrix(outputs);
    const emRaw = byKey('em_exit_revpar');
    // FON-68 step 5 — the Year-1 Cash-on-Cash grid. Unlike the IRR/EM grids
    // (exit cap × RevPAR growth), CoC is flat against those levers, so the
    // worker flexes it over a financing pair: loan amount × loan rate
    // (``coc_loan_rate``, metric ``year_one_coc``). Its axes come from the
    // matrix's own row/col_variable via matrixFromWorkerObj — we do NOT assume
    // the IRR grid's axes. Absent on an older run → the .filter below drops it,
    // so the third grid simply doesn't render (never a fabricated grid).
    const cocRaw = byKey('coc_loan_rate');
    return [
      { title: 'Levered IRR', matrix: irrRaw ? matrixFromWorkerObj(irrRaw) : null },
      { title: 'Equity Multiple (MOIC)', matrix: emRaw ? matrixFromWorkerObj(emRaw) : null },
      { title: 'Year-1 Cash-on-Cash', matrix: cocRaw ? matrixFromWorkerObj(cocRaw) : null },
    ].filter((c): c is { title: string; matrix: SensitivityMatrix } => c.matrix != null);
  }, [outputs]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <SectionCard
        title="Live Assumptions"
        note="Temporary overrides for testing — the source of truth stays in CAPEX and Financing"
      >
        {sandbox == null ? (
          // Standing rule — the sliders start from the assumptions the returns
          // engine recorded for the canonical run. A run that did not record
          // one of the five keys gets no default substituted; it gets told.
          <div
            data-testid="returns-sandbox-unavailable"
            title={`Not recorded on this run: ${baseMissing.join(', ')}`}
            style={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 10,
              marginTop: 4,
              fontSize: 12.5,
              color: palette.textMuted,
              lineHeight: 1.5,
            }}
          >
            <TrendingUp size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
            <span>
              Awaiting model run — the Live Assumptions sliders start from the assumptions the
              returns engine recorded for the canonical run, and this run did not record{' '}
              <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 11.5 }}>
                {baseMissing.join(', ')}
              </span>
              . Re-run the model to enable the sandbox; no default is substituted.
            </span>
          </div>
        ) : (
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))',
              gap: '14px 32px',
              marginTop: 4,
            }}
          >
            {fields.map((f) => (
              <Slider
                key={f.key}
                label={f.label}
                min={f.min}
                max={f.max}
                step={f.step}
                value={sandbox[f.key]}
                onChange={(v) =>
                  setSandbox((s) =>
                    s == null ? s : { ...s, [f.key]: f.key === 'holdYears' ? Math.round(v) : v },
                  )
                }
                format={f.fmt}
              />
            ))}
          </div>
        )}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 14,
            flexWrap: 'wrap',
            marginTop: 12,
            paddingTop: 12,
            borderTop: `1px solid ${palette.border}`,
          }}
        >
          <span style={{ fontSize: 11.5, color: palette.textSecondary, fontVariantNumeric: 'tabular-nums' }}>
            {dirty ? (previewing ? 'Recomputing sandbox…' : 'Sandbox result:') : 'Base case:'}
            <span style={{ margin: '0 6px', fontWeight: 600, color: palette.ink }}>
              IRR {fmtP(sIrr)}
            </span>
            ·
            <span style={{ margin: '0 6px', fontWeight: 600, color: palette.ink }}>EM {fmtX(sMult)}</span>·
            <span style={{ margin: '0 6px', fontWeight: 600, color: palette.ink }}>Exit {fmtM(sExit)}</span>·
            <span style={{ margin: '0 6px', fontWeight: 600, color: palette.ink }}>DSCR {fmtX(sDscr)}</span>
          </span>
          <button
            onClick={resetToBase}
            disabled={!dirty}
            style={{
              marginLeft: 'auto',
              fontFamily: 'inherit',
              borderRadius: radius.button,
              padding: '6px 12px',
              fontSize: 11.5,
              fontWeight: 600,
              border: `1px solid ${palette.buttonSecondaryBorder}`,
              background: dirty ? palette.cardWhite : palette.surfaceTint,
              color: dirty ? palette.hoverInk : palette.textFaint,
              cursor: dirty ? 'pointer' : 'not-allowed',
            }}
          >
            Reset to base case
          </button>
        </div>
      </SectionCard>

      {cards.length === 0 ? (
        <SectionCard>
          <div style={{ padding: '18px 0', textAlign: 'center', fontSize: 12.5, color: palette.textMuted }}>
            Sensitivity grids appear once the Returns engine has run.
          </div>
        </SectionCard>
      ) : (
        cards.map((c) => <SensitivityCard key={c.title} matrix={c.matrix} title={c.title} />)
      )}
    </div>
  );
}

interface WorkerCellRaw {
  row_value: number;
  col_value: number;
  value: number;
  is_base: boolean;
}
interface WorkerMatrixRaw {
  key?: string;
  label?: string;
  row_variable: string;
  col_variable: string;
  metric: string;
  rows: number[];
  cols: number[];
  cells: WorkerCellRaw[];
}

// The sensitivity engine's top-level primary matrix (pre-FON-53 shape).
function topLevelSensitivityMatrix(outputs: EngineOutputs): WorkerMatrixRaw | null {
  const out = getEngineField<WorkerMatrixRaw>(outputs, 'sensitivity');
  if (!out || !Array.isArray(out.rows) || !Array.isArray(out.cols)) return null;
  if (!Array.isArray(out.cells) || out.cells.length === 0) return null;
  return out;
}

// Map a worker sensitivity matrix (top-level primary OR a named entry from
// ``matrices[]``) into the SensitivityMatrix shape the card renders.
function matrixFromWorkerObj(out: WorkerMatrixRaw): SensitivityMatrix | null {
  if (!out || !Array.isArray(out.rows) || !Array.isArray(out.cols)) return null;
  if (!Array.isArray(out.cells) || out.cells.length === 0) return null;

  // Worker emits a flat cell list — re-shape to a 2D grid keyed by (row, col).
  const grid: SensitivityCell[][] = [];
  let baseRow = 0,
    baseCol = 0;
  for (let i = 0; i < out.rows.length; i++) {
    const row: SensitivityCell[] = [];
    for (let j = 0; j < out.cols.length; j++) {
      const found = out.cells.find(
        (c) => Math.abs(c.row_value - out.rows[i]) < 1e-9 && Math.abs(c.col_value - out.cols[j]) < 1e-9,
      );
      const cell: SensitivityCell = {
        value: typeof found?.value === 'number' && Number.isFinite(found.value) ? found.value : null,
        rowVal: out.rows[i],
        colVal: out.cols[j],
        isBase: !!found?.is_base,
      };
      if (cell.isBase) {
        baseRow = i;
        baseCol = j;
      }
      row.push(cell);
    }
    grid.push(row);
  }

  // Pretty labels for axes — fall back to the raw key when unknown.
  const labelFor = (key: string) =>
    (
      {
        exit_cap_rate: 'Exit Cap',
        revpar_growth: 'RevPAR Growth',
        ltv: 'LTV',
        interest_rate: 'Interest Rate',
        hold_years: 'Hold',
        purchase_price: 'Purchase Price',
        loan_amount: 'Loan Amount',
      } as Record<string, string>
    )[key] ?? key;

  return {
    rowLabel: labelFor(out.row_variable),
    colLabel: labelFor(out.col_variable),
    rows: out.rows,
    cols: out.cols,
    cells: grid,
    unit: out.metric === 'equity_multiple' ? 'multiple' : 'pct',
    baseRow,
    baseCol,
  };
}

// Footnotes per grid (matched by title). Absent → no footnote.
const SENSITIVITY_FOOTNOTE: Record<string, string> = {
  'Levered IRR':
    'Each cell re-runs the full model at that combination; the ringed cell is your base case.',
  'Equity Multiple (MOIC)':
    'Higher leverage shrinks the equity base and lifts the multiple; the ringed cell is your base case.',
  'Year-1 Cash-on-Cash':
    'Year-1 operating cash flow after debt, over equity — both terms move with leverage. The ringed cell is your base case.',
};

// Canonical sensitivity grid (Returns Tab.dc.html `matrix`): navy corner +
// column headers, tinted row-label column, and cell colour carrying only the
// base-case highlight (navy ring + tint + bold) — no heat-map. Built on the
// design tokens rather than the shared StatementTable because StatementTable's
// cells cannot express the single-cell base-case ring/tint the canonical grid
// depends on.
function SensitivityCard({ matrix, title }: { matrix: SensitivityMatrix; title: string }) {
  const corner = `${matrix.rowLabel} \\ ${matrix.colLabel}`.toUpperCase();
  const caption = `${matrix.rowLabel} × ${matrix.colLabel}`;
  const cols = `150px repeat(${matrix.cols.length}, minmax(96px,1fr))`;
  // Axis headers: Hold is years, Loan Amount is dollars (the CoC grid's row
  // axis is a loan balance, not a rate — formatting it as a percent would print
  // a nonsense value like 3500000000.0%); everything else is a rate/percent.
  const formatHeader = (v: number, key: string) =>
    key === 'Hold' ? `${v}y` : key === 'Loan Amount' ? `$${(v / 1e6).toFixed(1)}M` : `${(v * 100).toFixed(1)}%`;
  const formatCell = (v: number | null) =>
    v == null ? '—' : matrix.unit === 'multiple' ? `${v.toFixed(2)}x` : `${(v * 100).toFixed(1)}%`;
  const MISSING_CELL = 'no cell returned by the sensitivity engine for this combination';
  const footnote = SENSITIVITY_FOOTNOTE[title];

  const navyHead: React.CSSProperties = {
    padding: '7px 12px',
    background: palette.inkNavy,
    color: palette.gridHeaderText,
    whiteSpace: 'nowrap',
  };

  return (
    <SectionCard title={title} note={caption}>
      <div style={{ overflowX: 'auto', marginTop: 4 }}>
        <div style={{ display: 'grid', gridTemplateColumns: cols, width: 'max-content', minWidth: '100%' }}>
          <div style={{ ...navyHead, fontSize: 10, fontWeight: 700, letterSpacing: '.04em' }}>{corner}</div>
          {matrix.cols.map((c, j) => (
            <div
              key={j}
              style={{
                ...navyHead,
                fontSize: 10.5,
                fontWeight: 600,
                textAlign: 'right',
                borderLeft: `1px solid ${palette.gridHeaderDivider}`,
              }}
            >
              {formatHeader(c, matrix.colLabel)}
            </div>
          ))}
          {matrix.cells.map((row, ri) => (
            <div key={ri} style={{ display: 'contents' }}>
              <div
                style={{
                  padding: '7px 12px',
                  borderBottom: `1px solid ${palette.hairlineRow}`,
                  fontSize: 12,
                  color: palette.ink,
                  fontWeight: 600,
                  background: palette.surfaceTint,
                  whiteSpace: 'nowrap',
                }}
              >
                {formatHeader(matrix.rows[ri], matrix.rowLabel)}
              </div>
              {row.map((cell, ci) => (
                <div
                  key={ci}
                  title={`${formatHeader(matrix.rows[ri], matrix.rowLabel)} · ${formatHeader(
                    cell.colVal,
                    matrix.colLabel,
                  )}${cell.isBase ? ' — base case' : ''}${cell.value == null ? ` — ${MISSING_CELL}` : ''}`}
                  style={{
                    padding: '7px 12px',
                    borderBottom: `1px solid ${palette.hairlineRow}`,
                    borderLeft: `1px solid ${palette.hairlineRow}`,
                    textAlign: 'right',
                    fontSize: 12,
                    fontVariantNumeric: 'tabular-nums',
                    color: cell.isBase ? prov.black : prov.gray,
                    fontWeight: cell.isBase ? 700 : 400,
                    background: cell.isBase ? 'oklch(97% 0.03 250)' : 'transparent',
                    boxShadow: cell.isBase ? 'inset 0 0 0 2px #2f4a8c' : undefined,
                    whiteSpace: 'nowrap',
                  }}
                >
                  {formatCell(cell.value)}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
      {footnote && (
        <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>{footnote}</div>
      )}
    </SectionCard>
  );
}

// ───────────────────────────────────────────────────────────────────
// Shared bits
// ───────────────────────────────────────────────────────────────────

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  format,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  format: (v: number) => string;
}) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 4 }}>
        <label
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '.04em',
            color: palette.eyebrow,
            textTransform: 'uppercase',
          }}
        >
          {label}
        </label>
        <span style={{ fontSize: 13, fontWeight: 700, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>
          {format(value)}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        style={{ width: '100%', accentColor: palette.linkBlue }}
      />
    </div>
  );
}
