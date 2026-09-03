'use client';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { TrendingUp } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineRunHistory from './EngineRunHistory';
import PricingSensitivityPanel from './PricingSensitivityPanel';
import MaxPricePanel from './MaxPricePanel';
import { fmtPct, cn } from '@/lib/format';
import { api, isWorkerConnected, type ReturnsPreviewResponse, type ValueState } from '@/lib/api';
// Sensitivity grid shapes (relocated here when the client-side lib/engines model
// was retired — the worker sensitivity engine is now the only source).
interface SensitivityCell {
  value: number;
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
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
import { CoachMark } from '@/components/help/CoachMark';
import { Traced } from '@/components/help/Traced';
import {
  SubTabNav,
  KpiTile,
  SectionCard,
  ProvenanceDot,
  palette,
  prov,
  radius,
} from '@/components/design';

// FON-68 — MVP Returns is three sub-tabs. Scenario management lives on
// Scenario Analysis; comps live on Market → Transaction Comps.
const SUB_TABS = ['Returns Summary', 'Sensitivities', 'Pricing'] as const;
type SubTab = (typeof SUB_TABS)[number];

type EngineOutputs = ReturnType<typeof useEngineOutputs>['outputs'];

// ── Formatting helpers (canonical: money() / mm() / pct() / x()) ──
const money = (v: number) =>
  `${v < 0 ? '−$' : '$'}${Math.round(Math.abs(v)).toLocaleString('en-US')}`;
const mm = (v: number) => `${v < 0 ? '−$' : '$'}${(Math.abs(v) / 1e6).toFixed(2)}M`;
const fmtM = (v: number | null | undefined) => (v == null ? '—' : mm(v));
const fmtX = (v: number | null | undefined) => (v == null ? '—' : `${v.toFixed(2)}x`);

export default function ReturnsTab() {
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { toast } = useToast();
  const { outputs } = useEngineOutputs(dealId);

  // Has the Returns engine been run for this deal? Used to decide whether to
  // render the placeholder or the live UI.
  const wReturnsIrr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const hasWorkerReturns = wReturnsIrr != null;

  if (!hasWorkerReturns) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          <IntroCard
            dismissKey="returns-intro"
            title="The Returns Engine"
            body={
              <>
                The headline numbers — IRR, equity multiple, cash-on-cash — and how sensitive
                they are to your assumptions. This is what investors actually earn over the
                hold period after debt service.
              </>
            }
          />
          <EngineHeader
            name="Returns Engine"
            desc="Computes IRR, equity multiple, and scenario sensitivities for investment analysis."
            outputs={['Levered IRR', 'Unlevered IRR', 'Equity Multiple', '+1']}
            dependsOn="Cash Flow"
            dealId={dealId}
            engineName="returns"
          />
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <TrendingUp size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">Returns Engine unavailable</h3>
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              IRR, equity multiple, and sensitivity analysis depend on the
              <span className="font-medium"> Cash Flow</span> engine. Run that first, or upload an OM
              and T-12 if you haven&apos;t.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => toast('Engine queued — check back shortly', { type: 'info' })}
            >
              Run Returns Engine
            </Button>
          </Card>
          <EngineRunHistory dealId={dealId} />
        </div>
        <EngineRightRail />
      </div>
    );
  }

  // Output-only tab: every sub-view reads the canonical worker engine
  // outputs. The Returns tab no longer consumes the page assumptions
  // provider — its Live Assumptions sliders are a local, ephemeral sandbox
  // (FON-68 step 3), so nothing here can mutate Investment/Debt's model.
  return <ReturnsWorkspace outputs={outputs} dealId={dealId} />;
}

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

// Read the canonical slider base case straight off the returns engine's
// persisted ``inputs.assumptions`` blob — the SAME canonical run the headline
// reads — so the sandbox starts on the deal's real numbers with no dependency
// on the page assumptions provider.
function readSandboxBase(outputs: EngineOutputs): SandboxValues {
  const rIn = ((outputs?.engines?.returns?.inputs as Record<string, unknown> | undefined)
    ?.assumptions ?? {}) as Record<string, unknown>;
  const n = (v: unknown, fallback: number) =>
    typeof v === 'number' && Number.isFinite(v) ? v : fallback;
  return {
    exitCapRate: n(rIn.exit_cap_rate, 0.07),
    revparGrowth: n(rIn.revpar_growth, 0.03),
    holdYears: Math.round(n(rIn.hold_years, 5)),
    ltv: n(rIn.ltv, 0.65),
    interestRate: n(rIn.interest_rate, 0.068),
  };
}

function sandboxDiffers(a: SandboxValues, b: SandboxValues): boolean {
  return (Object.keys(a) as SandboxKey[]).some((k) => Math.abs(a[k] - b[k]) > 1e-9);
}

interface SandboxState {
  sandbox: SandboxValues;
  setSandbox: React.Dispatch<React.SetStateAction<SandboxValues>>;
  base: SandboxValues;
  dirty: boolean;
  preview: ReturnsPreviewResponse | null;
  previewing: boolean;
  resetToBase: () => void;
}

function useReturnsSandbox(outputs: EngineOutputs, dealId: string): SandboxState {
  const base = useMemo(() => readSandboxBase(outputs), [outputs]);
  const [sandbox, setSandbox] = useState<SandboxValues>(base);
  const prevBaseRef = useRef(base);
  // Follow a new canonical base (e.g. after a real re-run) only when the user
  // hasn't started testing an override; otherwise their sandbox persists across
  // a background refetch.
  useEffect(() => {
    const prev = prevBaseRef.current;
    setSandbox((cur) => (sandboxDiffers(cur, prev) ? cur : base));
    prevBaseRef.current = base;
  }, [base]);

  const dirty = sandboxDiffers(sandbox, base);
  const [preview, setPreview] = useState<ReturnsPreviewResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);

  // Debounced, non-persisting preview call. Clears when the sandbox is back on
  // the base case. Aborts in-flight requests as the slider moves.
  useEffect(() => {
    if (!dirty) {
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

  const resetToBase = () => setSandbox(base);
  return { sandbox, setSandbox, base, dirty, preview, previewing, resetToBase };
}

// ───────────────────────────────────────────────────────────────────
// Workspace — canonical single-column layout (Returns Tab.dc.html):
// title card → Data Key strip → sub-tab nav → override banner → sub-tab
// content. Rebuilt on the shared design system (@/components/design).
// ───────────────────────────────────────────────────────────────────

function ReturnsWorkspace({ outputs, dealId }: { outputs: EngineOutputs; dealId: string }) {
  const router = useRouter();
  const [tab, setTab] = useState<SubTab>('Returns Summary');
  const { sandbox, setSandbox, base, dirty, preview, previewing, resetToBase } = useReturnsSandbox(
    outputs,
    dealId,
  );

  const subTabCaption =
    tab === 'Returns Summary'
      ? 'What the deal earns and where it comes from'
      : tab === 'Sensitivities'
        ? 'How returns move with the key assumptions'
        : 'What price the deal can carry';

  const overrideSummary = SANDBOX_FIELDS.filter((f) => Math.abs(sandbox[f.key] - base[f.key]) > 1e-9)
    .map((f) => `${f.label} ${f.fmt(base[f.key])} → ${f.fmt(sandbox[f.key])}`)
    .join(' · ');

  const goInvestment = () => router.push(`/projects/${dealId}?tab=investment`, { scroll: false });
  const goCashFlow = () => router.push(`/projects/${dealId}?tab=cash-flow`, { scroll: false });

  return (
    <div style={{ maxWidth: 1320 }}>
      {/* Title card — canonical "Returns" + subtitle (replaces the old
          IntroCard + EngineHeader "Returns Engine" chrome). */}
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

      <SubTabNav
        items={SUB_TABS.map((t) => ({ id: t, label: t }))}
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
            Testing only — the canonical assumptions in Investment and Debt are unchanged.
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

      {tab === 'Returns Summary' && (
        <ReturnsSummary outputs={outputs} onEditInvestment={goInvestment} onViewCashFlow={goCashFlow} />
      )}
      {tab === 'Sensitivities' && (
        <Sensitivities
          outputs={outputs}
          sandbox={sandbox}
          setSandbox={setSandbox}
          dirty={dirty}
          preview={preview}
          previewing={previewing}
          resetToBase={resetToBase}
        />
      )}
      {tab === 'Pricing' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <PricingSensitivityPanel dealId={dealId} />
          <MaxPricePanel dealId={dealId} />
        </div>
      )}
    </div>
  );
}

// ───────────────────────────────────────────────────────────────────
// Returns Summary — headline KPIs (WORKER outputs only) + Exit Assumptions
// and Return Bridge cards (the two canonical cards previously missing).
// ───────────────────────────────────────────────────────────────────

function ReturnsSummary({
  outputs,
  onEditInvestment,
  onViewCashFlow,
}: {
  outputs: EngineOutputs;
  onEditInvestment: () => void;
  onViewCashFlow: () => void;
}) {
  // ── Hero row (4 navy tiles) — WORKER outputs only, no client TS fallback. ──
  const irr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const mult = getEngineField<number>(outputs, 'returns', 'equity_multiple');
  // Canonical headline shows the HOLD-AVERAGE cash-on-cash (``avg_coc``), not
  // year 1 — both exist on the returns engine (returns.py). year_one_coc feeds
  // the sublabel only.
  const avgCoc = getEngineField<number>(outputs, 'returns', 'avg_coc');
  const yearOneCoc = getEngineField<number>(outputs, 'returns', 'year_one_coc');
  const holdYears = getEngineField<number>(outputs, 'returns', 'hold_years');

  // Yield on Cost — DERIVED (no stored field): stabilized NOI ÷ total cost
  // basis. Stabilized NOI = the last operating-year NOI from the returns
  // engine's own ``noi_by_year``; total cost basis = ``capital.total_capital``.
  // If either is unsourceable we render '—' rather than a fabricated number.
  const noiByYear = getEngineField<number[]>(outputs, 'returns', 'noi_by_year');
  const totalCapital = getEngineField<number>(outputs, 'capital', 'total_capital');
  const stabilizedNoi =
    Array.isArray(noiByYear) && noiByYear.length > 0 ? noiByYear[noiByYear.length - 1] : undefined;
  const yieldOnCost =
    stabilizedNoi != null && totalCapital != null && totalCapital > 0
      ? stabilizedNoi / totalCapital
      : undefined;

  // ── Secondary row (3 white cards). Equity Profit + Initial Equity read off
  // the canonical levered cash-flow series so they reconcile to the bridge. ──
  const flows = getEngineField<number[]>(outputs, 'returns', 'cash_flows');
  const exitValue = getEngineField<number>(outputs, 'returns', 'gross_sale_price');
  const exitCap = getEngineField<number>(outputs, 'returns', 'exit_cap_rate');
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
              engine="returns"
              path="levered_irr"
              flashKey={irr}
              sub={holdYears != null ? `Equity IRR over the ${holdYears}-year hold` : 'Equity IRR after debt service'}
              value={fmtPct(irr ?? 0, 2)}
            />
          </CoachMark>
          <ReturnsKpi
            variant="navy"
            label="Equity Multiple"
            engine="returns"
            path="equity_multiple"
            flashKey={mult}
            sub={initialEquity != null ? `MOIC on ${mm(initialEquity)} invested` : 'MOIC — total distributions ÷ equity'}
            value={`${(mult ?? 0).toFixed(2)}x`}
          />
          <ReturnsKpi
            variant="navy"
            label="Avg. Cash-on-Cash"
            engine="returns"
            path="avg_coc"
            flashKey={avgCoc}
            sub={
              holdYears != null && yearOneCoc != null
                ? `Years 1–${holdYears} average — year 1 alone is ${fmtPct(yearOneCoc, 2)}`
                : 'Hold-average annual cash yield'
            }
            value={fmtPct(avgCoc ?? 0, 2)}
          />
          <ReturnsKpi
            variant="navy"
            label="Yield on Cost"
            flashKey={yieldOnCost}
            sub="Stabilized NOI ÷ total cost basis"
            value={yieldOnCost != null ? fmtPct(yieldOnCost, 2) : '—'}
          />
        </div>
      </div>

      {/* Secondary — 3 white cards (canonical `secondary`). */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(190px,1fr))', gap: 12 }}>
        <ReturnsKpi
          label="Exit Value"
          engine="returns"
          path="gross_sale_price"
          flashKey={exitValue}
          sub={exitCap != null ? `${fmtPct(exitCap, 2)} exit cap` : 'Gross sale — forward NOI ÷ exit cap'}
          value={fmtM(exitValue)}
        />
        <ReturnsKpi
          label="Equity Profit"
          flashKey={equityProfit}
          sub="Cash returned less equity invested"
          value={fmtM(equityProfit)}
        />
        <ReturnsKpi
          label="Initial Equity Invested"
          flashKey={initialEquity}
          sub="Funded in full at close"
          value={fmtM(initialEquity)}
        />
      </div>

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
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  flashKey?: unknown;
  engine?: string;
  path?: string;
  variant?: 'white' | 'navy';
}) {
  const flash = useFlash(flashKey ?? value);
  const body =
    engine && path ? (
      <Traced engine={engine} path={path}>
        {value}
      </Traced>
    ) : (
      value
    );
  return (
    <KpiTile
      className={cn(flash && 'value-flash')}
      style={{ height: '100%' }}
      variant={variant}
      label={label}
      sub={sub}
      value={body}
    />
  );
}

// ── Exit Assumptions — read-only; exit cap / hold / etc. are Investment- and
// Debt-owned, so they are shown linked/calculated here with an "Edit in
// Investment →" link. Values from the returns engine outputs (no fabrication;
// missing values render as —). ──
function ExitAssumptionsCard({ outputs, onEdit }: { outputs: EngineOutputs; onEdit: () => void }) {
  const holdYears = getEngineField<number>(outputs, 'returns', 'hold_years');
  const exitCap = getEngineField<number>(outputs, 'returns', 'exit_cap_rate');
  const grossSale = getEngineField<number>(outputs, 'returns', 'gross_sale_price');
  const sellingCosts = getEngineField<number>(outputs, 'returns', 'selling_costs');
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
  }
  const rows: Row[] = [
    {
      label: 'Hold Period',
      value: holdYears != null ? `${holdYears} years` : '—',
      source: 'Investment',
      state: 'linked',
      color: prov.green,
      weight: 400,
      title: 'Linked from Investment → Exit / Reversion',
    },
    {
      label: 'Exit Year',
      value: holdYears != null ? `Year ${holdYears}` : '—',
      source: 'Calculated',
      state: 'calculated',
      color: prov.gray,
      weight: 400,
      title: 'Acquisition date plus hold period',
    },
    {
      label: 'Exit Cap Rate',
      value: exitCap != null ? fmtPct(exitCap, 2) : '—',
      source: 'Investment',
      state: 'linked',
      color: prov.green,
      weight: 400,
      title: 'Linked from Investment → Exit / Reversion',
    },
    {
      label: 'Gross Sale Price',
      value: grossSale != null ? money(grossSale) : '—',
      source: 'Calculated',
      state: 'calculated',
      color: prov.black,
      weight: 700,
      title: 'Forward NOI ÷ exit cap rate',
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
          Edit in Investment →
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
              {r.value}
            </span>
            <span style={{ fontSize: 10.5, color: palette.textFaint, whiteSpace: 'nowrap' }}>
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
  const netExit = netProceeds ?? 0;
  const refi = refiYear != null && refiCashOut != null && refiCashOut > 0 ? refiCashOut : 0;
  // Net operating cash flow to equity (after debt, before refi/exit) — the leg
  // that operating + debt service must net to.
  const operatingNet = totalToEquity - netExit - refi;

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
  let debtService: number | undefined;
  if (dsFromStatement != null) {
    debtService = Math.abs(dsFromStatement);
  } else if (Array.isArray(noiByYear) && noiByYear.length > 0) {
    debtService = noiByYear.reduce((a, b) => a + b, 0) - operatingNet;
  }
  const operatingGross = debtService != null ? operatingNet + debtService : operatingNet;

  interface Bar {
    label: string;
    value: number;
    bold?: boolean;
    /** No refi on this deal — list the row (design always shows it) but as '—'. */
    awaiting?: boolean;
  }
  const items: Bar[] = [
    { label: 'Initial equity', value: initialEquity },
    { label: 'Operating cash flow', value: operatingGross },
  ];
  if (debtService != null) items.push({ label: 'Debt service', value: -debtService });
  items.push({ label: 'Refinance proceeds', value: refi, awaiting: refi === 0 });
  items.push({ label: 'Net exit proceeds', value: netExit });
  items.push({ label: 'Total cash flow to equity', value: totalToEquity, bold: true });

  const maxAbs = Math.max(1, ...items.filter((b) => !b.awaiting).map((b) => Math.abs(b.value)));

  return (
    <SectionCard title="Return Bridge" note={note}>
      <div style={{ marginTop: 2 }}>
        {items.map((b) => {
          const abs = Math.abs(b.value);
          const width = b.awaiting ? '0%' : `${(abs / maxAbs) * 50}%`;
          const left = b.value < 0 ? `${50 - (abs / maxAbs) * 50}%` : '50%';
          const barBg = b.bold
            ? palette.inkNavy
            : b.value < 0
              ? 'oklch(70% 0.10 40)'
              : 'oklch(60% 0.10 155)';
          const valueColor = b.awaiting
            ? palette.textFaint
            : b.bold
              ? prov.black
              : b.value < 0
                ? prov.amber
                : prov.gray;
          return (
            <div key={b.label} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '5px 0' }}>
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
                {b.awaiting ? '—' : mm(b.value)}
              </span>
            </div>
          );
        })}
      </div>
      <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 10, lineHeight: 1.5 }}>
        Deal-level returns · before GP/LP allocation. Reconciles to the Cash Flow series:{' '}
        {mm(operatingNet)} operating {refi > 0 ? `+ ${mm(refi)} refinance ` : ''}+ {mm(netExit)} exit ={' '}
        {mm(totalToEquity)} returned on {mm(equity)} invested.
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
  sandbox,
  setSandbox,
  dirty,
  preview,
  previewing,
  resetToBase,
}: {
  outputs: EngineOutputs;
  sandbox: SandboxValues;
  setSandbox: React.Dispatch<React.SetStateAction<SandboxValues>>;
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
        note="Temporary overrides for testing — the source of truth stays in Investment and Debt"
      >
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))',
            gap: '14px 32px',
            marginTop: 4,
          }}
        >
          {SANDBOX_FIELDS.map((f) => (
            <Slider
              key={f.key}
              label={f.label}
              min={f.min}
              max={f.max}
              step={f.step}
              value={sandbox[f.key]}
              onChange={(v) =>
                setSandbox((s) => ({ ...s, [f.key]: f.key === 'holdYears' ? Math.round(v) : v }))
              }
              format={f.fmt}
            />
          ))}
        </div>
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
              IRR {fmtPct(sIrr ?? 0, 2)}
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
        value: found?.value ?? 0,
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
  const formatCell = (v: number) =>
    matrix.unit === 'multiple' ? `${v.toFixed(2)}x` : `${(v * 100).toFixed(1)}%`;
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
                  )}${cell.isBase ? ' — base case' : ''}`}
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
