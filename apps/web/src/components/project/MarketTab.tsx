'use client';
/**
 * MarketTab — canonical rebuild (FON-72).
 *
 * Rebuilt to match `design/canonical/Market Tab.dc.html` using the shared
 * design-system layer (`@/components/design`: KpiTile, SectionCard, SubTabNav,
 * StatementTable, ProvenanceDot + tokens). Three sub-tabs — Market Overview,
 * Transaction Comps, Index Analysis — each wired to live data from the market
 * API / engine outputs. NOTHING is a prototype placeholder: every value reads
 * from `api.market.*`, `getEngineField`, or is rendered as the canonical
 * "awaiting data" treatment (never a fabricated number). Provenance origin is
 * consulted from `/provenance` where a matching engine field exists and falls
 * back to the value's derived origin otherwise.
 *
 * The one canonical Data Key strip lives in `projects/[id]/page.tsx` (full
 * width, under the tab bar) — this tab renders NO per-tab legend.
 */
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { useParams } from 'next/navigation';
import { useSubTab } from '@/lib/hooks/useSubTab';
import Link from 'next/link';
import { MapPinned, Loader2 } from 'lucide-react';
import {
  api,
  isWorkerConnected,
  type TransactionCompEntry,
  type TransactionCompsResult,
  type ValueState,
  type EngineName,
  type DealProvenanceResponse,
} from '@/lib/api';
import { useDeal } from '@/lib/hooks/useDeal';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useEngineOutputs, getEngineField } from '@/lib/hooks/useEngineOutputs';
import { useSource, useProvenanceState } from '@/lib/hooks/useDealProvenance';
import { useRefusal } from '@/components/help/Refused';
import { useToast } from '@/components/ui/Toast';
import {
  STR_MARKET_OVERRIDE_NOTE,
  isStrMarketOverride,
  isStrBasisSource,
  sourceLabel,
} from '@/lib/provenance';
import {
  palette,
  prov,
  KpiTile,
  SectionCard,
  SubTabNav,
  ProvenanceDot,
  StatementTable,
} from '@/components/design';
import IndexAnalysisSection from './pl/IndexAnalysisSection';

// ─── worker payload shapes ──────────────────────────────────────────────────
// Real STR/CoStar market data served by /deals/{id}/market-data
// (apps/worker/app/api/documents.py _aggregate_market_data). Populated once an
// STR_TREND / CoStar report is uploaded + extracted.
interface StrCompRow {
  name: string;
  keys: number | null;
  occupancy_pct: number | null;
  adr_usd: number | null;
  revpar_usd: number | null;
}
interface StrTrend {
  subject_occupancy_pct: number | null;
  subject_adr_usd: number | null;
  subject_revpar_usd: number | null;
  rgi_revpar_index: number | null;
  ari_adr_index: number | null;
  mpi_occupancy_index: number | null;
  comp_set_size: number | null;
  total_keys: number | null;
  compset: StrCompRow[];
}
interface MarketDataResp {
  deal_id: string;
  str_trend: StrTrend | null;
  sources?: Record<string, unknown>;
}
// `GET /market/{deal_id}/overview` — mirrors apps/worker/app/api/market.py
// MarketOverview. Indices are null until the STR/CoStar feed is wired up.
interface WorkerMarketOverview {
  deal_id: string;
  market: string | null;
  keys: number | null;
  brand: string | null;
  service: string | null;
  property_name: string | null;
  occupancy_index: number | null;
  adr_index: number | null;
  revpar_index: number | null;
}

// FON-59 #4 / FON-61 §3 — the sub-tab *id* is the URL slug
// (`?tab=market&sub=…`); the label is display only.
const SUB_TABS = [
  { id: 'market-overview', label: 'Market Overview' },
  { id: 'transaction-comps', label: 'Transaction Comps' },
  { id: 'index-analysis', label: 'Index Analysis' },
] as const;
type SubTab = (typeof SUB_TABS)[number]['id'];
const SUB_TAB_IDS = SUB_TABS.map((t) => t.id) as readonly SubTab[];

/**
 * FON-61 (D4) — what the "STR rates" card says, read from the WORKER's source
 * tags (never inferred from the ``revenue_seed_from_str_forecast`` flag alone),
 * exactly like Financials → Projections' Year-1 basis chip:
 *   off         — flag not set → "Model input · Use STR rates in the model".
 *   active      — flag set AND starting_occupancy / starting_adr carry one of
 *                 the STR basis tags (``isStrBasisSource``: the subject's TTM
 *                 actual, the comp-set rates, or the forward forecast) →
 *                 "STR / Market basis active".
 *   unavailable — flag set but the worker tagged ``str_forecast_unavailable``
 *                 (no STR Trend extraction / coverage too low) → the model is
 *                 on the T-12 base and the card says so.
 *   pending     — flag set but the worker returned NO tag for these keys (the
 *                 provenance fetch failed, or the deployed worker predates the
 *                 tags) → "Pending re-run" — never shown as "active".
 */
type StrBasis = 'off' | 'active' | 'unavailable' | 'pending';

// ─── canonical colors (from design tokens) ──────────────────────────────────
const GREEN = prov.green; // document-sourced market metric
const GRAY = prov.gray; // calculated
const MUTED = prov.muted; // awaiting data
const AMBER = 'oklch(50% 0.14 40)'; // negative delta (canonical AMBER)

// ─── formatters (never fabricate — null → em dash) ──────────────────────────
const money0 = (v: number): string => `$${Math.round(v).toLocaleString('en-US')}`;
const pct1 = (v: number): string => `${v.toFixed(1)}%`;
// STR occupancy arrives as a 0..1 fraction (0.715) or a whole percent (71.5);
// normalize to a whole-percent number so we never render "0.7%" for a 72% hotel.
const occPct = (v: number): number => (v <= 1.5 ? v * 100 : v);

function fmtSalePrice(usd: number | null): string {
  if (usd == null) return '—';
  if (usd >= 1_000_000) return `$${(usd / 1_000_000).toFixed(1)}M`;
  if (usd >= 1_000) return `$${(usd / 1_000).toFixed(0)}K`;
  return `$${usd.toFixed(0)}`;
}
function fmtPerKey(usd: number | null): string {
  if (usd == null) return '—';
  return `$${usd.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}
function fmtCap2(pct: number | null): string {
  if (pct == null) return '—';
  return `${pct.toFixed(2)}%`;
}
function fmtSaleDate(s: string | null): string {
  if (!s) return '—';
  const t = s.trim();
  return /^\d{4}-\d{2}-\d{2}/.test(t) ? t.slice(0, 10) : t;
}

// STR / CoStar anonymize per-property comp performance, so every competitor's
// Occ/ADR/RevPAR comes back null. But the report publishes the subject's index
// vs the comp-set aggregate — MPI (occupancy), ARI (ADR), RGI (RevPAR) — where
// each index = subject metric ÷ comp-set metric. So the blended comp-set
// performance is recoverable: comp metric = subject metric ÷ index. Indices may
// arrive as a ratio (1.098) or index points (109.8); normalize both.
type DerivedComp = { occ: number | null; adr: number | null; revpar: number | null } | null;
function deriveCompSet(t: StrTrend | null): DerivedComp {
  if (!t) return null;
  const ratio = (idx: number | null): number | null =>
    idx == null || idx <= 0 ? null : idx > 3 ? idx / 100 : idx;
  const div = (subj: number | null, idx: number | null, normalizeSubj = false): number | null => {
    const r = ratio(idx);
    if (subj == null || r == null) return null;
    return (normalizeSubj ? occPct(subj) : subj) / r;
  };
  const occ = div(t.subject_occupancy_pct, t.mpi_occupancy_index, true);
  const adr = div(t.subject_adr_usd, t.ari_adr_index);
  const revpar = div(t.subject_revpar_usd, t.rgi_revpar_index);
  if (occ == null && adr == null && revpar == null) return null;
  return { occ, adr, revpar };
}
// STR penetration index → display points (109.8). ratio (1.098) or points both ok.
const idxPoints = (idx: number | null): number | null =>
  idx == null || idx <= 0 ? null : idx > 3 ? idx : idx * 100;

// ─── FON-60 (60.1) — extracted transactions vs SELECTED comps ───────────────
//
// Sam: "Add an Include as Comp control per row. Summary cards should calculate
// from selected comps only and clearly show something like `6 of 28 selected`."
//
// An extracted transaction is not automatically a comparable — the analyst
// decides. The selection is DEAL state (the IC reviewer must see the same comp
// set the analyst built), so it rides ``field_overrides`` like the worksheet
// layout does, and like the worksheet layout it is on the worker's
// ``_OVERRIDE_NON_ENGINE_KEYS`` skip list: analyst curation never reaches
// engine input. The default is ABSENT = all selected, so no existing deal's
// Market tab changes the day this ships.
export const SELECTED_COMPS_KEY = 'market.selected_comps';
export const SELECTED_COMPS_NOTE = 'Transaction comps included in the Market tab summary';

/** Stable per-row identity: the three fields that identify a sale. */
export function compKey(c: TransactionCompEntry): string {
  return [c.name ?? '', c.sale_date ?? '', c.sale_price_usd ?? ''].join('|');
}

/** The persisted selection, or ``null`` when the analyst has not curated. */
export function readSelectedComps(
  overrides: Record<string, unknown> | null | undefined,
): string[] | null {
  const raw = overrides?.[SELECTED_COMPS_KEY];
  const list = Array.isArray(raw)
    ? raw
    : raw && typeof raw === 'object' && Array.isArray((raw as { value?: unknown }).value)
      ? ((raw as { value: unknown[] }).value)
      : null;
  if (!list) return null;
  return list.filter((k): k is string => typeof k === 'string');
}

/** The worker's median, to the digit — ``apps/worker/app/api/market.py``
 *  ``_median``. The tiles recompute from the SELECTED subset, so they must
 *  agree with the worker's whole-set figure when everything is selected. */
export function medianOf(xs: number[]): number | null {
  if (xs.length === 0) return null;
  const s = [...xs].sort((a, b) => a - b);
  const n = s.length;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}

const plural = (n: number, one: string, many: string): string => (n === 1 ? one : many);

// ─── small shared bits ──────────────────────────────────────────────────────
function AwaitingPanel({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        border: '1px dashed #d9d8d2',
        borderRadius: 8,
        background: palette.surfaceTint,
        padding: '16px 18px',
        display: 'flex',
        flexDirection: 'column',
        gap: 7,
      }}
    >
      <span
        style={{
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: '.05em',
          color: palette.eyebrow,
          textTransform: 'uppercase',
        }}
      >
        Awaiting data
      </span>
      <span style={{ fontSize: 12.5, color: '#3a3f47', lineHeight: 1.5 }}>{children}</span>
    </div>
  );
}

// The canonical 12-bar seasonality curve (design's bars). Rendered only when a
// monthly index series is available; otherwise the host card shows the awaiting
// state (no monthly STR feed is extracted yet — flagged gap).
function SeasonalityBars({ data }: { data: number[] }) {
  const months = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'];
  const max = Math.max(...data, 1);
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 72, marginTop: 4 }}>
      {data.map((v, i) => {
        const h = (v / max) * 100;
        return (
          <div
            key={i}
            title={`Index ${v}`}
            style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}
          >
            <div
              style={{
                width: '100%',
                height: `${h}%`,
                background: h > 85 ? 'oklch(55% 0.09 200)' : 'oklch(78% 0.05 200)',
                borderRadius: '2px 2px 0 0',
              }}
            />
            <span style={{ fontSize: 9, color: palette.textFaint }}>{months[i]}</span>
          </div>
        );
      })}
    </div>
  );
}

// ─── §1 Submarket Snapshot ──────────────────────────────────────────────────
function SubmarketSnapshot({
  derivedComp,
  compSetSize,
  compKeyCount,
  submarketLabel,
}: {
  derivedComp: DerivedComp;
  compSetSize: number | null;
  compKeyCount: number | null;
  submarketLabel: string | null;
}) {
  const inventory =
    compSetSize && compKeyCount
      ? `${compSetSize} hotels`
      : compKeyCount
        ? `${compKeyCount.toLocaleString()} keys`
        : '—';
  const invSub = compKeyCount ? `${compKeyCount.toLocaleString()} keys in comp set` : 'competitive set';
  const tiles: { label: string; value: string; sub: string; color: string; awaiting?: boolean }[] = [
    { label: 'Inventory', value: inventory, sub: invSub, color: GREEN },
    {
      label: 'Market Occupancy',
      value: derivedComp?.occ != null ? pct1(derivedComp.occ) : '—',
      sub: derivedComp?.occ != null ? 'TTM · comp-set blend' : 'awaiting STR report',
      color: derivedComp?.occ != null ? GREEN : MUTED,
      awaiting: derivedComp?.occ == null,
    },
    {
      label: 'Market ADR',
      value: derivedComp?.adr != null ? money0(derivedComp.adr) : '—',
      sub: derivedComp?.adr != null ? 'TTM · comp-set blend' : 'awaiting STR report',
      color: derivedComp?.adr != null ? GREEN : MUTED,
      awaiting: derivedComp?.adr == null,
    },
    {
      label: 'Market RevPAR',
      value: derivedComp?.revpar != null ? money0(derivedComp.revpar) : '—',
      sub: derivedComp?.revpar != null ? 'Occupancy × ADR' : 'awaiting STR report',
      color: derivedComp?.revpar != null ? GRAY : MUTED,
      awaiting: derivedComp?.revpar == null,
    },
    { label: 'Demand Growth', value: '—', sub: 'awaiting CoStar submarket report', color: MUTED, awaiting: true },
    { label: 'Supply Growth', value: '—', sub: 'awaiting CoStar submarket report', color: MUTED, awaiting: true },
  ];
  const context = submarketLabel
    ? `${submarketLabel} · competitive-set aggregate`
    : "Subject's local competitive set";
  return (
    <SectionCard title="Submarket Snapshot" note={context}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit,minmax(158px,1fr))',
          gap: 10,
          marginTop: 12,
        }}
      >
        {tiles.map((t) => (
          <KpiTile
            key={t.label}
            label={t.label}
            value={t.value}
            sub={t.sub}
            valueColor={t.color}
            style={t.awaiting ? { background: palette.surfaceTint } : undefined}
          />
        ))}
      </div>
    </SectionCard>
  );
}

// ─── §2 Historical Market Performance ───────────────────────────────────────
// Multi-year submarket history needs an STR Trend Report (36-month). Only the
// trailing-twelve-month blend is extracted today, so the table renders a single
// real TTM column (from the recovered comp set) — never fabricated year columns.
function HistoricalMarketPerf({ derivedComp, ttmLabel }: { derivedComp: DerivedComp; ttmLabel: string }) {
  const has = derivedComp && (derivedComp.occ != null || derivedComp.adr != null);
  return (
    <SectionCard variant="title" title="Historical Market Performance" note="STR · comp-set blend">
      {has && derivedComp ? (
        <StatementTable
          lineItemHeader="METRIC"
          columns={[ttmLabel]}
          showDots={false}
          gridTemplateColumns="minmax(150px,1.4fr) minmax(110px,1fr)"
          rows={[
            {
              label: 'Market Occupancy',
              cells: [{ text: derivedComp.occ != null ? pct1(derivedComp.occ) : '—', color: derivedComp.occ != null ? GREEN : MUTED }],
            },
            {
              label: 'Market ADR',
              cells: [{ text: derivedComp.adr != null ? money0(derivedComp.adr) : '—', color: derivedComp.adr != null ? GREEN : MUTED }],
            },
            {
              label: 'Market RevPAR',
              cells: [{ text: derivedComp.revpar != null ? money0(derivedComp.revpar) : '—', color: derivedComp.revpar != null ? GRAY : MUTED }],
            },
            { label: 'RevPAR Growth', cells: [{ text: '—', color: MUTED }] },
          ]}
          footnote="Multi-year submarket Occupancy / ADR / RevPAR history populates from an STR Trend Report (36-month). Prior-year columns stay blank until it is in the Data Room."
        />
      ) : (
        <div style={{ padding: '16px 18px' }}>
          <AwaitingPanel>
            Multi-year submarket Occupancy / ADR / RevPAR trends populate from an <b>STR Trend Report
            (36-month)</b> for this deal&apos;s comp set.
          </AwaitingPanel>
        </div>
      )}
    </SectionCard>
  );
}

// ─── §3 Monthly / TTM Performance ───────────────────────────────────────────
// Monthly movement + the 12-bar seasonality curve need a monthly STR Trend
// Report, which is not extracted yet — so this renders the canonical awaiting
// state. `SeasonalityBars` is wired for when a monthly index series lands.
function MonthlyTtmCard({ seasonality }: { seasonality: number[] | null }) {
  return (
    <SectionCard variant="title" title="Monthly / TTM Performance" note="STR Trend Report" bodyStyle={{ padding: '16px 18px' }}>
      {seasonality && seasonality.length === 12 ? (
        <SeasonalityBars data={seasonality} />
      ) : (
        <AwaitingPanel>
          Monthly movement and the 12-month seasonality curve populate from an <b>STR Trend Report
          (36-month, monthly)</b>. Only the trailing-twelve-month blend (shown in Subject vs. Comp Set
          below) is available today.
        </AwaitingPanel>
      )}
    </SectionCard>
  );
}

// ─── §4 Subject vs. Comp Set ────────────────────────────────────────────────
function SubjectVsCompSet({
  strTrend,
  derivedComp,
  compKeyCount,
  strBasis,
  strBasisSource,
  strBasisSettled,
  baseYearOccPct,
  baseYearAdr,
  strRunning,
  onToggleStr,
  onRerun,
  dealId,
  subjectState,
}: {
  strTrend: StrTrend;
  derivedComp: DerivedComp;
  compKeyCount: number | null;
  /** Tag-honest card state — see ``StrBasis``. */
  strBasis: StrBasis;
  /** FON-61 (61.2) — WHICH STR basis the worker tagged the Year-1 rates with
   *  (``str_subject_ttm`` / ``str_comp_set`` / ``str_forecast``), so the card
   *  can name the basis instead of a rate. Empty when the model has none. */
  strBasisSource: string | null;
  /** False while the provenance map is still loading (pending card reads
   *  "checking" instead of "pending re-run"). */
  strBasisSettled: boolean;
  /** FON-61 (61.1) — the Base Year Financials → Projections ACTUALLY shows
   *  (revenue engine ``years[0]``, falling back to the resolved assumption).
   *  Null until the model has run: the card then claims no Base Year rather
   *  than printing the comp set's number in its place. */
  baseYearOccPct: number | null;
  baseYearAdr: number | null;
  strRunning: boolean;
  onToggleStr: () => void;
  onRerun: () => void;
  dealId: string;
  subjectState: ValueState;
}) {
  const subjOcc = strTrend.subject_occupancy_pct != null ? occPct(strTrend.subject_occupancy_pct) : null;
  const subjAdr = strTrend.subject_adr_usd;
  const subjRevpar =
    strTrend.subject_revpar_usd != null
      ? strTrend.subject_revpar_usd
      : subjOcc != null && subjAdr != null
        ? (subjOcc / 100) * subjAdr
        : null;

  // Subject-vs-comp delta annotations.
  const delta = (subj: number | null, comp: number | null): number | null =>
    subj != null && comp != null ? subj - comp : null;
  const occDelta = delta(subjOcc, derivedComp?.occ ?? null);
  const adrDelta = delta(subjAdr, derivedComp?.adr ?? null);
  const revparDelta = delta(subjRevpar, derivedComp?.revpar ?? null);
  const deltaColor = (d: number | null): string => (d == null ? MUTED : d >= 0 ? GREEN : AMBER);
  const ptsText = (d: number | null): string =>
    d == null ? 'no comp set' : `${d >= 0 ? '+' : '−'}${Math.abs(d).toFixed(1)} pts vs. comp set`;
  const dollarText = (d: number | null): string =>
    d == null ? 'no comp set' : `${d >= 0 ? '+' : '−'}$${Math.abs(d).toFixed(0)} vs. comp set`;

  const subjectMetrics = [
    { label: 'Occupancy', value: subjOcc != null ? pct1(subjOcc) : '—', delta: ptsText(occDelta), dc: deltaColor(occDelta) },
    { label: 'ADR', value: subjAdr != null ? `$${subjAdr.toFixed(2)}` : '—', delta: dollarText(adrDelta), dc: deltaColor(adrDelta) },
    { label: 'RevPAR', value: subjRevpar != null ? `$${subjRevpar.toFixed(2)}` : '—', delta: dollarText(revparDelta), dc: deltaColor(revparDelta) },
  ];
  const blended = [
    { label: 'Keys', value: compKeyCount != null ? compKeyCount.toLocaleString() : '—' },
    { label: 'Occupancy', value: derivedComp?.occ != null ? pct1(derivedComp.occ) : '—' },
    { label: 'ADR', value: derivedComp?.adr != null ? money0(derivedComp.adr) : '—' },
    { label: 'RevPAR', value: derivedComp?.revpar != null ? money0(derivedComp.revpar) : '—' },
  ];

  const strOcc = derivedComp?.occ != null ? pct1(derivedComp.occ) : '—';
  const strAdr = derivedComp?.adr != null ? money0(derivedComp.adr) : '—';
  const t12Occ = subjOcc != null ? pct1(subjOcc) : '—';
  const t12Adr = subjAdr != null ? money0(subjAdr) : '—';

  // ── FON-61 (61.1) — two different things, said separately ───────────────
  //
  // Sam: "Market Overview currently says the model is using 65.2% Occupancy /
  // $383 ADR as Year-1 assumptions. However, Financials → Projections actually
  // shows Base Year 71.6% / $288."
  //
  // Both numbers were right; the SENTENCE was wrong. The card was printing the
  // comp-set blend (``derivedComp``, the same object behind the blended card
  // above) and asserting it was the underwriting input. It is not: the worker
  // deliberately seeds Year-1 from the SUBJECT's own STR trailing twelve months
  // — using the forward forecast's Month-12 point instead could land far below
  // the subject's actual and tank the deal (engine_runner.py, the −19% IRR
  // foot-gun comment). So the card now names a BASIS rather than a rate, and
  // states the comp-set benchmark and the model's Base Year as two separate
  // facts, each from its own source.
  const benchmarkClause = (
    <>
      Comp-set benchmark: <b>{strOcc}</b> Occ · <b>{strAdr}</b> ADR.
    </>
  );
  const hasBaseYear = baseYearOccPct != null || baseYearAdr != null;
  const baseYearClause = hasBaseYear ? (
    <>
      Financials → Projections Base Year:{' '}
      <b>{baseYearOccPct != null ? pct1(baseYearOccPct) : '—'}</b> Occ ·{' '}
      <b>{baseYearAdr != null ? money0(baseYearAdr) : '—'}</b> ADR.
    </>
  ) : (
    <>The Base Year in Financials → Projections is not available until the model has run.</>
  );
  // Which basis the model is on, named — ``str_comp_set`` is the one case where
  // the comp-set rates ARE the Year-1 input (the analyst applied them here as
  // explicit overrides), so the card must not claim otherwise.
  const basisIsCompSet = strBasisSource === 'str_comp_set';
  const basisName = strBasisSource ? sourceLabel(strBasisSource) : null;

  const contextNote = `Trailing 12 months · STR${
    strTrend.comp_set_size ? ` · ${strTrend.comp_set_size}-property comp set` : ''
  }${compKeyCount ? ` · ${compKeyCount.toLocaleString()} keys` : ''}`;

  const compset = strTrend.compset ?? [];
  const anonymized = compset.length > 0 && compset.every((c) => c.occupancy_pct == null && c.adr_usd == null && c.revpar_usd == null);
  const compGrid = 'minmax(200px,2fr) 68px repeat(3,minmax(72px,1fr))';

  const secBtn: CSSProperties = {
    background: '#fff',
    border: '1px solid #e2e1dc',
    color: '#3a3f47',
    borderRadius: 5,
    padding: '6px 12px',
    fontSize: 11.5,
    fontWeight: 600,
    cursor: 'pointer',
    fontFamily: 'inherit',
  };
  const navyBtn: CSSProperties = {
    background: palette.inkNavy,
    color: '#fff',
    border: 'none',
    borderRadius: 5,
    padding: '6px 12px',
    fontSize: 11.5,
    fontWeight: 600,
    cursor: strRunning ? 'default' : 'pointer',
    fontFamily: 'inherit',
    opacity: strRunning ? 0.6 : 1,
  };

  return (
    <SectionCard title="Subject vs. Comp Set" note={contextNote}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 12 }}>
        {/* Subject property + blended benchmark */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 12 }}>
          <div style={{ border: '1px solid #dbe3f5', background: 'oklch(97.5% 0.015 250)', borderRadius: 9, padding: '13px 15px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 9 }}>
              <ProvenanceDot state={subjectState} size={8} />
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: '#2f4a8c', textTransform: 'uppercase' }}>
                Subject property
              </span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10 }}>
              {subjectMetrics.map((m) => (
                <div key={m.label}>
                  <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.04em', color: palette.eyebrow, textTransform: 'uppercase', marginBottom: 4 }}>
                    {m.label}
                  </div>
                  <div style={{ fontSize: 20, fontWeight: 700, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>{m.value}</div>
                  <div style={{ fontSize: 10.5, color: m.dc, marginTop: 3 }}>{m.delta}</div>
                </div>
              ))}
            </div>
          </div>
          <div style={{ border: `1px solid ${palette.border}`, borderRadius: 9, padding: '13px 15px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 9 }}>
              <ProvenanceDot state="calculated" size={8} />
              <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: palette.eyebrow, textTransform: 'uppercase' }}>
                Blended competitive set — benchmark
              </span>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 10 }}>
              {blended.map((m) => (
                <div key={m.label}>
                  <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.04em', color: palette.eyebrow, textTransform: 'uppercase', marginBottom: 4 }}>
                    {m.label}
                  </div>
                  <div style={{ fontSize: 17, fontWeight: 700, color: GREEN, fontVariantNumeric: 'tabular-nums' }}>{m.value}</div>
                </div>
              ))}
            </div>
            <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.45 }}>
              The blended comp set is the benchmark every index on this page is measured against — recovered
              from the STR penetration indices (MPI · ARI · RGI) against the subject shown at left.
            </div>
          </div>
        </div>

        {/* Comp-set roster (per-property perf anonymized by STR) */}
        {compset.length > 0 && (
          <div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: compGrid,
                fontSize: 10,
                fontWeight: 700,
                letterSpacing: '.05em',
                color: palette.textFaint,
                textTransform: 'uppercase',
                paddingBottom: 6,
                borderBottom: `1px solid ${palette.border}`,
              }}
            >
              <span>Competitive set hotel</span>
              <span style={{ textAlign: 'right' }}>Keys</span>
              <span style={{ textAlign: 'right' }}>Occ</span>
              <span style={{ textAlign: 'right' }}>ADR</span>
              <span style={{ textAlign: 'right' }}>RevPAR</span>
            </div>
            {compset.map((h, i) => (
              <div
                key={`${h.name}-${i}`}
                style={{
                  display: 'grid',
                  gridTemplateColumns: compGrid,
                  fontSize: 12.5,
                  padding: '6px 0',
                  borderBottom: `1px solid ${palette.hairlineRow}`,
                  alignItems: 'center',
                }}
              >
                <span style={{ color: palette.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.name}</span>
                <span style={{ textAlign: 'right', color: '#3a3f47', fontVariantNumeric: 'tabular-nums' }}>{h.keys ?? '—'}</span>
                <span style={{ textAlign: 'right', color: h.occupancy_pct != null ? '#3a3f47' : palette.textFaint }}>
                  {h.occupancy_pct != null ? pct1(occPct(h.occupancy_pct)) : '—'}
                </span>
                <span style={{ textAlign: 'right', color: h.adr_usd != null ? '#3a3f47' : palette.textFaint }}>
                  {h.adr_usd != null ? money0(h.adr_usd) : '—'}
                </span>
                <span style={{ textAlign: 'right', color: h.revpar_usd != null ? '#3a3f47' : palette.textFaint }}>
                  {h.revpar_usd != null ? money0(h.revpar_usd) : '—'}
                </span>
              </div>
            ))}
            {anonymized && (
              <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 8, lineHeight: 1.45 }}>
                STR does not publish per-property Occupancy, ADR or RevPAR for the comp set — only the blended
                figures above. Individual rows show key counts only.
              </div>
            )}
          </div>
        )}

        {/* STR-rate model input toggle — the card state is the WORKER's
            source tag (see ``StrBasis``), never the flag alone. */}
        {strBasis === 'active' ? (
          <div
            data-testid="str-card-active"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              flexWrap: 'wrap',
              background: 'oklch(96.5% 0.03 155)',
              border: '1px solid oklch(85% 0.05 155)',
              borderRadius: 8,
              padding: '10px 14px',
            }}
          >
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: 'oklch(40% 0.12 155)', textTransform: 'uppercase' }}>
              STR / Market basis active
            </span>
            <span style={{ fontSize: 12.5, color: palette.ink }}>
              {benchmarkClause}{' '}
              {basisIsCompSet
                ? 'The analyst applied these comp-set rates as the Year-1 input.'
                : `The model does not substitute these — it projects the subject off its own STR basis${
                    basisName ? ` (${basisName})` : ''
                  }.`}{' '}
              {baseYearClause}
            </span>
            <span style={{ display: 'flex', gap: 8, marginLeft: 'auto', alignItems: 'center' }}>
              {strRunning && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: '#2f4a8c' }}>
                  <Loader2 size={12} className="animate-spin" /> Re-modeling…
                </span>
              )}
              <Link href={`/projects/${dealId}?tab=pl&sub=projections`} style={{ textDecoration: 'none' }}>
                <span style={secBtn}>View Projections →</span>
              </Link>
              <button onClick={onToggleStr} disabled={strRunning} style={navyBtn}>
                Revert to T-12 actuals
              </button>
            </span>
          </div>
        ) : strBasis === 'unavailable' ? (
          <div
            data-testid="str-card-unavailable"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              flexWrap: 'wrap',
              background: 'oklch(97% 0.03 80)',
              border: '1px solid oklch(85% 0.08 80)',
              borderRadius: 8,
              padding: '10px 14px',
            }}
          >
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: 'oklch(42% 0.12 60)', textTransform: 'uppercase' }}>
              STR rates unavailable — using T-12 base
            </span>
            <span style={{ fontSize: 12.5, color: palette.ink }}>
              STR rates were requested but could not populate (no STR Trend extraction or coverage too low),
              so the model is on the T-12 base, not the STR rates. {baseYearClause}
            </span>
            <span style={{ display: 'flex', gap: 8, marginLeft: 'auto', alignItems: 'center' }}>
              {strRunning && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: '#2f4a8c' }}>
                  <Loader2 size={12} className="animate-spin" /> Re-modeling…
                </span>
              )}
              <Link href={`/projects/${dealId}?tab=pl&sub=projections`} style={{ textDecoration: 'none' }}>
                <span style={secBtn}>View Projections →</span>
              </Link>
              <button onClick={onToggleStr} disabled={strRunning} style={navyBtn}>
                Clear STR request
              </button>
            </span>
          </div>
        ) : strBasis === 'pending' ? (
          <div
            data-testid="str-card-pending"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              flexWrap: 'wrap',
              background: palette.surfaceTint,
              border: `1px solid ${palette.border}`,
              borderRadius: 8,
              padding: '10px 14px',
            }}
          >
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: palette.eyebrow, textTransform: 'uppercase' }}>
              {strBasisSettled ? 'Pending re-run' : 'Checking model basis…'}
            </span>
            <span style={{ fontSize: 12.5, color: palette.ink }}>
              {benchmarkClause} An STR basis was requested, but the model has not confirmed which basis
              Year-1 is on
              {strBasisSettled ? ' — the saved run predates the source tags. Re-run the model to apply and confirm them.' : '.'}
              {' '}{baseYearClause}
            </span>
            <span style={{ display: 'flex', gap: 8, marginLeft: 'auto', alignItems: 'center' }}>
              {strRunning && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: '#2f4a8c' }}>
                  <Loader2 size={12} className="animate-spin" /> Re-modeling…
                </span>
              )}
              <button onClick={onToggleStr} disabled={strRunning} style={secBtn}>
                Revert to T-12 actuals
              </button>
              {strBasisSettled && (
                <button onClick={onRerun} disabled={strRunning} style={navyBtn}>
                  Re-run model
                </button>
              )}
            </span>
          </div>
        ) : (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 14,
              flexWrap: 'wrap',
              background: palette.surfaceTint,
              border: `1px solid ${palette.border}`,
              borderRadius: 8,
              padding: '10px 14px',
            }}
          >
            <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.05em', color: palette.eyebrow, textTransform: 'uppercase' }}>
              Model input
            </span>
            <span style={{ fontSize: 12.5, color: palette.ink }}>
              {baseYearClause} {benchmarkClause} Applying it would set Year-1 Occupancy &amp; ADR to those
              comp-set figures. The subject&apos;s own STR trailing twelve months read {t12Occ} · {t12Adr}.
            </span>
            <span style={{ display: 'flex', gap: 8, marginLeft: 'auto', alignItems: 'center' }}>
              {strRunning && (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: '#2f4a8c' }}>
                  <Loader2 size={12} className="animate-spin" /> Re-modeling…
                </span>
              )}
              <button onClick={onToggleStr} disabled={strRunning} style={navyBtn}>
                Use STR rates in the model
              </button>
            </span>
          </div>
        )}
      </div>
    </SectionCard>
  );
}

// ─── §5 Index Summary ───────────────────────────────────────────────────────
function IndexSummary({ strTrend, onOpenIndex, state }: { strTrend: StrTrend; onOpenIndex: () => void; state: ValueState }) {
  const items = [
    { key: 'MPI', label: 'Occupancy Index / MPI', v: idxPoints(strTrend.mpi_occupancy_index) },
    { key: 'ARI', label: 'ADR Index / ARI', v: idxPoints(strTrend.ari_adr_index) },
    { key: 'RGI', label: 'RevPAR Index / RGI', v: idxPoints(strTrend.rgi_revpar_index) },
  ];
  if (!items.some((i) => i.v != null)) return null;
  const note = (
    <span onClick={onOpenIndex} style={{ color: palette.linkBlue, fontWeight: 600, cursor: 'pointer' }}>
      Full 2019–2033 series in Index Analysis →
    </span>
  );
  return (
    <SectionCard title="Index Summary" note={note}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(210px,1fr))', gap: 12, marginTop: 12 }}>
        {items.map((it) => {
          const over = it.v != null && it.v >= 100;
          const color = it.v == null ? MUTED : over ? GREEN : AMBER;
          const width = it.v != null ? Math.min(100, it.v / 2) : 0;
          return (
            <div key={it.key} style={{ border: `1px solid ${palette.border}`, borderRadius: 8, padding: '12px 14px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 10, fontWeight: 700, letterSpacing: '.04em', color: palette.eyebrow, textTransform: 'uppercase' }}>
                  <ProvenanceDot state={state} size={7} /> {it.label}
                </span>
                <span style={{ fontSize: 19, fontWeight: 700, color, fontVariantNumeric: 'tabular-nums' }}>
                  {it.v != null ? it.v.toFixed(1) : '—'}
                </span>
              </div>
              <div style={{ position: 'relative', height: 6, background: '#f0efeb', borderRadius: 3, overflow: 'hidden' }}>
                <div style={{ position: 'absolute', left: 0, top: 0, height: '100%', width: `${width}%`, background: color, borderRadius: 3 }} />
                <div style={{ position: 'absolute', left: '50%', top: 0, height: '100%', width: 1, background: '#c9c8c2' }} />
              </div>
              <div style={{ fontSize: 11, color: palette.textSecondary, marginTop: 7 }}>
                {it.v == null
                  ? 'not published'
                  : over
                    ? `Subject leads by ${(it.v - 100).toFixed(1)} points`
                    : `Subject trails by ${(100 - it.v).toFixed(1)} points`}
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 10 }}>
        100 = at par with the comp set · &gt;100 = subject outperforms
      </div>
    </SectionCard>
  );
}

// ─── §6 Supply Pipeline ─────────────────────────────────────────────────────
function SupplyPipeline({ compKeyCount }: { compKeyCount: number | null }) {
  const rows: { label: string; value: string; color: string; weight: number; awaiting?: boolean }[] = [
    {
      label: 'Existing comp set supply',
      value: compKeyCount != null ? `${compKeyCount.toLocaleString()} keys` : '—',
      color: compKeyCount != null ? GREEN : MUTED,
      weight: 400,
      awaiting: compKeyCount == null,
    },
    { label: 'Under construction', value: '—', color: MUTED, weight: 400, awaiting: true },
    { label: 'Expected deliveries', value: '—', color: MUTED, weight: 400, awaiting: true },
    { label: 'Comp set growth', value: '—', color: MUTED, weight: 400, awaiting: true },
    { label: 'Planned / unentitled', value: '—', color: MUTED, weight: 400, awaiting: true },
  ];
  return (
    <SectionCard title="Supply Pipeline" note="CoStar Hospitality">
      <div style={{ marginTop: 10 }}>
        {rows.map((r) => (
          <div
            key={r.label}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              gap: 12,
              fontSize: 12.5,
              padding: '6px 0',
              borderBottom: `1px solid ${palette.hairlineRow}`,
            }}
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: palette.textSecondary }}>
              {r.awaiting && <ProvenanceDot state="awaiting_data" size={7} />}
              {r.label}
            </span>
            <span style={{ color: r.color, fontWeight: r.weight, fontVariantNumeric: 'tabular-nums' }}>{r.value}</span>
          </div>
        ))}
      </div>
    </SectionCard>
  );
}

// ─── §7 Demand Drivers ──────────────────────────────────────────────────────
function DemandDrivers() {
  return (
    <SectionCard title="Demand Drivers" note="CoStar Submarket Report" bodyStyle={{ minHeight: 0 }}>
      <div style={{ marginTop: 10 }}>
        <AwaitingPanel>
          Demand segmentation — transient / group / contract mix and demand growth by source — populates
          from a <b>CoStar Submarket Report</b>.
        </AwaitingPanel>
      </div>
    </SectionCard>
  );
}

// ─── Transaction Comps sub-tab ──────────────────────────────────────────────
type SortKey = 'Sale date' | '$ / Key' | 'Cap rate';

function TransactionCompsSection({
  workerComps,
  dealId,
  entryPerKey,
  entryCapPct,
  exitCapPct,
  storedSelection,
  onPersistSelection,
}: {
  workerComps: TransactionCompsResult | null;
  dealId: string;
  entryPerKey: number | null;
  entryCapPct: number | null;
  exitCapPct: number | null;
  /** FON-60 (60.1) — the persisted comp selection, or ``null`` when the
   *  analyst has not curated this deal (the default: everything counts). */
  storedSelection: string[] | null;
  onPersistSelection: (keys: string[]) => Promise<void>;
}) {
  const [sort, setSort] = useState<SortKey>('Sale date');
  const [showAll, setShowAll] = useState(false);
  // Local intent wins until the PATCH round-trips through the deal row, so a
  // click lands immediately. ``undefined`` = untouched this session.
  const [draftSelection, setDraftSelection] = useState<string[] | undefined>(undefined);
  const [savingSelection, setSavingSelection] = useState(false);

  if (!workerComps) {
    return (
      <SectionCard variant="title" title="Transaction Comparables">
        <div style={{ fontSize: 12, color: palette.textMuted, padding: '32px 18px', textAlign: 'center' }}>
          Loading transaction comps…
        </div>
      </SectionCard>
    );
  }
  const comps = workerComps.comps;
  if (comps.length === 0) {
    return (
      <SectionCard variant="title" title="Transaction Comparables">
        <div style={{ fontSize: 12, color: palette.textMuted, padding: '32px 18px', textAlign: 'center' }}>
          {workerComps.note ??
            'No comparable sales extracted yet. Upload an OM with a Comparable Sales table to populate this view.'}
        </div>
      </SectionCard>
    );
  }

  // ── FON-60 (60.1) — the summary is computed on the SELECTED comps ───────
  const allKeys = comps.map(compKey);
  const persisted = draftSelection ?? storedSelection;
  // An analyst has curated this deal only once a selection exists. Absent →
  // everything counts, which is exactly today's behaviour for every deal that
  // ships before this change.
  const curated = persisted != null;
  const knownKeys = new Set(allKeys);
  const selectedKeys = new Set(
    persisted == null ? allKeys : persisted.filter((k) => knownKeys.has(k)),
  );
  const selectedComps = comps.filter((c) => selectedKeys.has(compKey(c)));
  const selectedCount = selectedComps.length;
  const isSubset = selectedCount !== comps.length;

  const toggleComp = async (key: string) => {
    const next = new Set(selectedKeys);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    const ordered = allKeys.filter((k) => next.has(k));
    setDraftSelection(ordered);
    setSavingSelection(true);
    try {
      await onPersistSelection(ordered);
    } finally {
      setSavingSelection(false);
    }
  };

  const perKeys = selectedComps
    .map((c) => c.price_per_key_usd)
    .filter((v): v is number => v != null);
  const caps = selectedComps
    .map((c) => c.cap_rate_pct)
    .filter((v): v is number => v != null);
  // Same median convention as apps/worker/app/api/market.py, so an uncurated
  // deal's tile is the worker's whole-set figure to the digit.
  const median = medianOf(perKeys);
  const medianCap = medianOf(caps);
  // The worker's number over EVERY extracted transaction, kept so a curated
  // deal can still see what it started from.
  const allExtractedMedian = workerComps.median_price_per_key;
  const allExtractedCap = workerComps.median_cap_rate_pct;
  const subsetNote = `Computed on the ${selectedCount} selected ${plural(selectedCount, 'comp', 'comps')} of ${comps.length} extracted.`;

  // Context callouts — entry basis / cap from engine outputs (getEngineField),
  // never a placeholder. Falls back to a neutral anchor line when unavailable.
  //
  // FON-60 (60.2) — the judgement clause ("supportive of the entry valuation")
  // is an investment conclusion, and it was drawn from an UNCURATED set: every
  // transaction the extractor found, comparable or not. While nothing has been
  // curated the tile states the arithmetic fact and stops there; once the
  // analyst has chosen a comp set, the conclusion returns and names the set it
  // was computed on.
  const perKeyFact =
    entryPerKey != null && median != null && median > 0
      ? `Underwritten entry basis of ${money0(entryPerKey)} / key sits ${Math.abs((1 - entryPerKey / median) * 100).toFixed(1)}% ${
          entryPerKey <= median ? 'below' : 'above'
        } the median of ${curated ? `the ${selectedCount} selected ${plural(selectedCount, 'comp', 'comps')}` : `all ${comps.length} extracted ${plural(comps.length, 'transaction', 'transactions')}`}.`
      : null;
  const perKeyContext =
    perKeyFact == null
      ? 'Anchor for entry / exit valuation.'
      : curated
        ? `${perKeyFact} ${entryPerKey != null && median != null && entryPerKey <= median ? 'Supportive of' : 'Rich versus'} the entry valuation on that set.`
        : `${perKeyFact} Include the transactions that are genuinely comparable to draw a conclusion from them.`;
  const capFact =
    entryCapPct != null && medianCap != null
      ? `Entry cap of ${entryCapPct.toFixed(2)}% is ${Math.abs(Math.round((entryCapPct - medianCap) * 100))} bps ${
          entryCapPct >= medianCap ? 'above' : 'below'
        } the median${exitCapPct != null ? `; the ${exitCapPct.toFixed(2)}% exit assumption anchors terminal value` : ''}.`
      : null;

  // ── FON-60 (60.3) — a "median" of one observation is not a median ────────
  //
  // Sam: "Current UI shows Median Cap Rate = 7.81% even though only 1 of 28
  // transactions discloses a cap rate and calls it an `Anchor for exit-cap rate
  // selection`." Two observations is the floor for the word median; the
  // observation count is stated at every n; at n = 1 the tile says what the
  // number is — one reported cap rate — and uses neither "median" nor
  // "anchor"; at n = 0 it renders a dash and claims nothing at all.
  const obs = (n: number): string => `${n} ${plural(n, 'observation', 'observations')}`;
  const perKeyTile =
    perKeys.length === 0
      ? {
          testId: 'per-key',
          label: '$ / Key',
          value: '—',
          range: `${obs(0)} — no selected comp discloses a $ / key`,
          context: null,
        }
      : perKeys.length === 1
        ? {
            testId: 'per-key',
            label: 'Reported $ / Key',
            value: money0(perKeys[0]),
            range: obs(1),
            context: `One disclosed $ / key — a single sale, not a distribution. ${subsetNote}`,
          }
        : {
            testId: 'per-key',
            label: 'Median $ / Key',
            value: median != null ? money0(median) : '—',
            range: `Range ${money0(Math.min(...perKeys))} – ${money0(Math.max(...perKeys))} · ${obs(perKeys.length)}`,
            context: perKeyContext,
          };
  const capTile =
    caps.length === 0
      ? {
          testId: 'cap-rate',
          label: 'Cap Rate',
          value: '—',
          range: `${obs(0)} — no selected comp discloses a cap rate`,
          context: null,
        }
      : caps.length === 1
        ? {
            testId: 'cap-rate',
            label: 'Reported Cap Rate',
            value: fmtCap2(caps[0]),
            // Neither the word "median" nor the word "anchor" belongs on a
            // single observation — Sam quoted both back at us. One disclosed
            // cap rate is a fact about one sale, and the tile says only that.
            range: obs(1),
            context: `One disclosed cap rate — a single sale, too thin to carry the exit-cap selection on its own. ${subsetNote}`,
          }
        : {
            testId: 'cap-rate',
            label: 'Median Cap Rate',
            value: fmtCap2(medianCap),
            range: `${caps.length} of ${selectedCount} selected ${plural(selectedCount, 'comp', 'comps')} disclose a cap rate · ${obs(caps.length)}`,
            context: capFact ?? 'Anchor for exit-cap rate selection.',
          };
  const summary = [
    {
      ...perKeyTile,
      allExtracted:
        isSubset && allExtractedMedian != null
          ? `All ${comps.length} extracted: ${money0(allExtractedMedian)}`
          : null,
    },
    {
      ...capTile,
      allExtracted:
        isSubset && allExtractedCap != null
          ? `All ${comps.length} extracted: ${fmtCap2(allExtractedCap)}`
          : null,
    },
  ];

  const sorted = [...comps].sort((a, b) => {
    if (sort === '$ / Key') return (b.price_per_key_usd ?? -Infinity) - (a.price_per_key_usd ?? -Infinity);
    if (sort === 'Cap rate') return (a.cap_rate_pct ?? Infinity) - (b.cap_rate_pct ?? Infinity);
    return 0; // Sale date — preserve the worker's date-sorted order
  });
  const visible = showAll ? sorted : sorted.slice(0, 14);

  const compsCaption =
    'Extracted from Offering Memorandums and market reports in the Data Room';
  const columns: { label: string; align: 'left' | 'right' }[] = [
    // FON-60 (60.1) — Sam: "Add an Include as Comp control per row."
    { label: 'INCLUDE', align: 'left' },
    { label: 'PROPERTY', align: 'left' },
    { label: 'MARKET', align: 'left' },
    { label: 'SALE DATE', align: 'left' },
    { label: 'KEYS', align: 'right' },
    { label: 'SALE PRICE', align: 'right' },
    { label: '$ / KEY', align: 'right' },
    { label: 'CAP RATE', align: 'right' },
    { label: 'BUYER', align: 'left' },
    { label: 'SELLER', align: 'left' },
  ];
  const gridCols =
    '76px minmax(210px,1.6fr) minmax(120px,1fr) 96px 62px 108px 100px 84px minmax(150px,1fr) minmax(150px,1fr)';

  const pill = (label: SortKey): CSSProperties => {
    const active = label === sort;
    return {
      fontSize: 11.5,
      fontFamily: 'inherit',
      border: `1px solid ${active ? '#dbe3f5' : '#e2e1dc'}`,
      background: active ? '#eef2fb' : '#fff',
      color: active ? '#2f4a8c' : palette.textSecondary,
      fontWeight: active ? 700 : 500,
      borderRadius: 6,
      padding: '5px 11px',
      cursor: 'pointer',
      whiteSpace: 'nowrap',
    };
  };
  const cellBase: CSSProperties = {
    padding: '7px 12px',
    borderBottom: `1px solid ${palette.hairlineSection}`,
    fontSize: 12,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Headline anchors */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(300px,1fr))', gap: 14 }}>
        {summary.map((c) => (
          <div key={c.label} data-testid={`comp-tile-${c.testId}`} style={{ background: palette.cardWhite, border: `1px solid ${palette.border}`, borderRadius: 10, padding: '16px 18px' }}>
            <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '.04em', color: palette.eyebrow, textTransform: 'uppercase', marginBottom: 7 }}>
              {c.label}
            </div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
              <span style={{ fontSize: 26, fontWeight: 700, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>{c.value}</span>
              <span style={{ fontSize: 11.5, color: palette.textMuted }}>{c.range}</span>
            </div>
            {/* FON-60 (60.3) — at n = 0 the tile makes no claim at all. */}
            {c.context && (
              <div style={{ fontSize: 12, color: '#3a3f47', lineHeight: 1.5, marginTop: 8 }}>{c.context}</div>
            )}
            {/* The worker's whole-set figure stays visible once the analyst
                has narrowed the set, so the curation is legible. */}
            {c.allExtracted && (
              <div style={{ fontSize: 11.5, color: palette.textMuted, marginTop: 6 }}>{c.allExtracted}</div>
            )}
          </div>
        ))}
      </div>

      {/* Comparables grid */}
      <SectionCard
        variant="title"
        title={
          <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <span style={{ fontSize: 13.5, fontWeight: 700, color: palette.ink }}>Transaction Comparables</span>
            <span style={{ fontSize: 11, color: palette.textMuted, fontWeight: 400 }}>{compsCaption}</span>
          </span>
        }
        note={
          <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {(['Sale date', '$ / Key', 'Cap rate'] as SortKey[]).map((s) => (
              <button key={s} onClick={() => setSort(s)} style={pill(s)}>
                {s}
              </button>
            ))}
          </span>
        }
      >
        <div style={{ overflowX: 'auto' }}>
          <div style={{ display: 'grid', gridTemplateColumns: gridCols, width: 'max-content', minWidth: '100%' }}>
            {columns.map((h) => (
              <div
                key={h.label}
                style={{
                  padding: '8px 12px',
                  background: palette.inkNavy,
                  color: palette.gridHeaderText,
                  fontSize: 10.5,
                  fontWeight: 600,
                  letterSpacing: '.03em',
                  textAlign: h.align,
                  borderRight: `1px solid ${palette.gridHeaderDivider}`,
                  position: 'sticky',
                  top: 0,
                }}
              >
                {h.label}
              </div>
            ))}
            {visible.map((c, i) => {
              const bg = i % 2 ? palette.surfaceTint : '#fff';
              const buyer = c.buyer_name ?? c.buyer_type ?? null;
              const key = compKey(c);
              const included = selectedKeys.has(key);
              return (
                <div key={`${c.name}-${i}`} style={{ display: 'contents' }}>
                  {/* FON-60 (60.1) — an extracted transaction is not a comp
                      until the analyst says so. Unchecking one recomputes the
                      tiles above and persists on the DEAL (never engine
                      input), so the IC reviewer opens the same comp set. */}
                  <div style={{ ...cellBase, background: bg, overflow: 'visible' }}>
                    <input
                      type="checkbox"
                      checked={included}
                      disabled={savingSelection}
                      onChange={() => void toggleComp(key)}
                      aria-label={`Include ${c.name} as a comp`}
                      style={{ cursor: savingSelection ? 'progress' : 'pointer', margin: 0 }}
                    />
                  </div>
                  {/* FON-60 §4 — the property name is plain text. The old
                      source deep link was a raw worker download URL built from
                      an unvalidated `source_document_id`, which returned
                      "document not found on deal". `source_document_id` /
                      `source_page` stay on the API type and in the export
                      payload for a post-MVP restore. */}
                  <div style={{ ...cellBase, background: bg, color: palette.ink, fontWeight: 600, fontSize: 12.5 }}>
                    {c.name}
                  </div>
                  <div style={{ ...cellBase, background: bg, color: palette.textSecondary }}>{c.market ?? '—'}</div>
                  <div style={{ ...cellBase, background: bg, color: palette.textSecondary }}>{fmtSaleDate(c.sale_date)}</div>
                  <div style={{ ...cellBase, background: bg, color: '#3a3f47', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {c.keys != null ? c.keys.toLocaleString() : '—'}
                  </div>
                  <div style={{ ...cellBase, background: bg, color: '#3a3f47', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {fmtSalePrice(c.sale_price_usd)}
                  </div>
                  <div style={{ ...cellBase, background: bg, color: palette.ink, fontWeight: 600, fontSize: 12.5, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                    {fmtPerKey(c.price_per_key_usd)}
                  </div>
                  <div
                    style={{
                      ...cellBase,
                      background: bg,
                      color: c.cap_rate_pct != null ? palette.ink : MUTED,
                      fontWeight: 600,
                      fontSize: 12.5,
                      textAlign: 'right',
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {fmtCap2(c.cap_rate_pct)}
                  </div>
                  <div style={{ ...cellBase, background: bg, color: buyer ? palette.textSecondary : MUTED }}>{buyer ?? '—'}</div>
                  <div style={{ ...cellBase, background: bg, color: c.seller ? palette.textSecondary : MUTED }}>{c.seller ?? '—'}</div>
                </div>
              );
            })}
          </div>
        </div>
        <div style={{ padding: '11px 18px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11.5, color: palette.textMuted }}>
            Showing {visible.length} of {comps.length} comps ·{' '}
            <b style={{ color: palette.ink, fontWeight: 600 }}>
              {selectedCount} of {comps.length} selected as comps
            </b>{' '}
            · sorted by {sort.toLowerCase()}
          </span>
          {comps.length > 14 && (
            <button
              onClick={() => setShowAll((s) => !s)}
              style={{
                background: '#fff',
                border: '1px solid #e2e1dc',
                color: '#3a3f47',
                borderRadius: 6,
                padding: '6px 13px',
                fontSize: 11.5,
                fontWeight: 600,
                cursor: 'pointer',
                fontFamily: 'inherit',
              }}
            >
              {showAll ? 'Show top 14' : `Show all ${comps.length} comps`}
            </button>
          )}
        </div>
      </SectionCard>
    </div>
  );
}

// ─── main ───────────────────────────────────────────────────────────────────
export default function MarketTab({ projectId }: { projectId: number | string }) {
  // One convention — `?tab=market&sub=<slug>`, deep-linkable and back-button
  // correct, with every other query param (`doc`, `focus`, `reviewField`) kept.
  const { sub: tab, setSub: setTab } = useSubTab(SUB_TAB_IDS, 'market-overview');
  const { toast } = useToast();
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? String(projectId);
  const { deal, refresh: refreshDeal } = useDeal(dealId);
  const liveDeal = !!dealId && !/^\d+$/.test(dealId);

  // Real aggregated STR/CoStar market data.
  const [marketData, setMarketData] = useState<MarketDataResp | null>(null);
  useEffect(() => {
    if (!isWorkerConnected() || !liveDeal) return;
    const ctrl = new AbortController();
    api.market
      .data(dealId, ctrl.signal)
      .then((d) => { if (d) setMarketData(d as MarketDataResp); })
      .catch(() => { /* silent — empty state covers it */ });
    return () => ctrl.abort();
  }, [dealId, liveDeal]);

  // Worker market overview (submarket label / keys).
  const [workerMarket, setWorkerMarket] = useState<WorkerMarketOverview | null>(null);
  useEffect(() => {
    if (!isWorkerConnected() || !liveDeal) return;
    const ctrl = new AbortController();
    api.market
      .overview(dealId, ctrl.signal)
      .then((d) => { if (d) setWorkerMarket(d as WorkerMarketOverview); })
      .catch(() => {});
    return () => ctrl.abort();
  }, [dealId, liveDeal]);

  // Transaction comps (extracted from OMs).
  const [workerComps, setWorkerComps] = useState<TransactionCompsResult | null>(null);
  useEffect(() => {
    if (!isWorkerConnected() || !liveDeal) return;
    const ctrl = new AbortController();
    api.market
      .transactionComps(dealId, ctrl.signal)
      .then((res) => setWorkerComps(res))
      .catch(() => {});
    return () => ctrl.abort();
  }, [dealId, liveDeal]);

  // Per-value provenance — consulted for the origin dot where an engine field
  // matches; market-derived values fall back to their derived origin.
  const [dealProv, setDealProv] = useState<DealProvenanceResponse | null>(null);
  useEffect(() => {
    if (!isWorkerConnected() || !liveDeal) return;
    const ctrl = new AbortController();
    api.deals
      .provenance(dealId, ctrl.signal)
      .then((p) => { if (p) setDealProv(p); })
      .catch(() => {});
    return () => ctrl.abort();
  }, [dealId, liveDeal]);
  const stateOf = (engine: EngineName, path: string, fallback: ValueState): ValueState =>
    (dealProv?.engines?.[engine]?.[path]?.state as ValueState | undefined) ?? fallback;

  // Engine outputs — entry basis / cap for the comps context callouts.
  const { outputs } = useEngineOutputs(dealId);
  const entryPerKey = getEngineField<number>(outputs, 'capital', 'price_per_key') ?? null;
  const entryCapRaw = getEngineField<number>(outputs, 'capital', 'entry_cap_rate') ?? null;
  const exitCapRaw = getEngineField<number>(outputs, 'returns', 'exit_cap_rate') ?? null;
  // Engine cap rates are fractions (0.075); the median from the comps endpoint
  // is already a percent (7.5). Normalize both to percent for the callout math.
  const asPct = (v: number | null): number | null => (v == null ? null : v <= 1 ? v * 100 : v);
  const entryCapPct = asPct(entryCapRaw);
  const exitCapPct = asPct(exitCapRaw);

  const strTrend = marketData?.str_trend ?? null;
  const hasStr = !!(strTrend && (strTrend.subject_occupancy_pct != null || strTrend.subject_adr_usd != null));
  const derivedComp = deriveCompSet(strTrend);
  // Comp-set room count = sum of the named roster's keys (the extracted
  // total_keys rollup has proven unreliable). Shown consistently across the tab.
  const compKeyCount =
    strTrend?.compset?.reduce((s, c) => s + (c.keys && c.keys > 0 ? c.keys : 0), 0) || null;

  // STR-rate model seed — same field_overrides mechanism as the FON-27 overrides.
  const { run, status: runStatus } = useEngineRun(dealId, 'returns', { runMode: 'all' });
  const strRunning = runStatus === 'running' || runStatus === 'queued';
  const overrides = (deal?.field_overrides ?? {}) as Record<string, unknown>;
  const rawSeed = overrides['revenue_seed_from_str_forecast'];
  const strSeeded =
    rawSeed === true ||
    (typeof rawSeed === 'object' && rawSeed !== null && (rawSeed as { value?: unknown }).value === true);
  // Tag-honest card state — the same worker source tags Financials →
  // Projections reads for its Year-1 basis chip. The flag only says the seed
  // was REQUESTED; only an STR basis tag on the rate keys says it is
  // what the model is actually using.
  const occSrc = useSource('starting_occupancy');
  const adrSrc = useSource('starting_adr');
  const seedSrc = useSource('revenue_seed_from_str_forecast');
  const { settled: strBasisSettled } = useProvenanceState();
  // Phase 4.4 — the worker's machine-readable refusal is read FIRST: a
  // ``str_unavailable`` code on the seed key says "unavailable" outright. The
  // ``str_forecast_unavailable`` source-tag sniff stays as the fallback, and
  // the worker sets both together today, so this card is identical before and
  // after the code ships. An ACTIVE tag still wins over either — a populated
  // model is never re-labelled as a refusal.
  const strSeedRefusal = useRefusal('revenue_seed_from_str_forecast');
  // FON-61 (61.2) — ONE helper for "is this an STR basis", shared with
  // Financials → Projections. The worker's single ``str_forecast`` id was split
  // into the subject's TTM actual, the comp-set rates and the forward forecast;
  // a hand-rolled equality here would have silently dropped two of the three.
  const strBasisSource =
    [occSrc?.source, adrSrc?.source].find((src) => isStrBasisSource(src)) ?? null;
  const strBasis: StrBasis = !strSeeded
    ? 'off'
    : strBasisSource
      ? 'active'
      : strSeedRefusal === 'str_unavailable'
        || [seedSrc, occSrc, adrSrc].some((s) => s?.source === 'str_forecast_unavailable')
        ? 'unavailable'
        : 'pending';
  // FON-61 (61.1) — the Base Year the model ACTUALLY uses. First choice is the
  // revenue engine's ``years[0]``, which is the very row Financials →
  // Projections renders as "Base Year (Year 1)"; the resolved assumption is the
  // fallback before a run has landed. Never the comp set — that was the bug.
  const revenueYears = getEngineField<{ occupancy?: number; adr?: number }[]>(
    outputs,
    'revenue',
    'years',
  );
  const finiteOr = (v: unknown): number | null =>
    typeof v === 'number' && Number.isFinite(v) ? v : null;
  const baseYearOccRaw =
    finiteOr(revenueYears?.[0]?.occupancy) ?? finiteOr(occSrc?.value);
  const baseYearOccPct = baseYearOccRaw == null ? null : occPct(baseYearOccRaw);
  const baseYearAdr = finiteOr(revenueYears?.[0]?.adr) ?? finiteOr(adrSrc?.value);
  // FON-61 (D4) — Market → Financials propagation is EXPLICIT. "Use STR rates"
  // writes ``starting_occupancy`` / ``starting_adr`` field_overrides carrying
  // exactly the comp-set values the card displays (occupancy at the card's
  // 0.1-pt precision as a fraction; ADR at the card's whole-dollar precision),
  // each with the exact ``STR_MARKET_OVERRIDE_NOTE`` so the worker badges
  // them ``str_comp_set`` rather than a generic analyst override. When the
  // comp set can't supply both values the card shows "—" and only the flag
  // is written — the worker then seeds from the subject TTM / forecast, or
  // tags the flag ``str_forecast_unavailable`` (never a silent "active").
  // "Revert" deletes the flag AND both keys — but only keys that carry the
  // STR note, so an analyst's later explicit override on either key survives.
  const toggleStrSeed = async () => {
    const next = { ...overrides };
    if (strSeeded) {
      delete next['revenue_seed_from_str_forecast'];
      for (const key of ['starting_occupancy', 'starting_adr'] as const) {
        if (isStrMarketOverride(next[key])) delete next[key];
      }
    } else {
      next['revenue_seed_from_str_forecast'] = { value: true, note: 'STR market rates enabled from the Market tab' };
      if (derivedComp?.occ != null && derivedComp?.adr != null) {
        next['starting_occupancy'] = {
          value: Math.round(derivedComp.occ * 10) / 10 / 100,
          note: STR_MARKET_OVERRIDE_NOTE,
        };
        next['starting_adr'] = { value: Math.round(derivedComp.adr), note: STR_MARKET_OVERRIDE_NOTE };
      }
    }
    try {
      await api.deals.update(dealId, { field_overrides: next });
      refreshDeal();
      await run();
      toast(
        strSeeded ? 'Reverted Year-1 to the T-12 actuals' : 'Year-1 occupancy & ADR now driven by STR market rates — re-modeled',
        { type: 'success' },
      );
    } catch {
      toast('Could not update the model', { type: 'error' });
    }
  };

  // ── FON-60 (60.1) — the comp selection lives on the DEAL ────────────────
  //
  // Same ``field_overrides`` channel as the STR seed, and deliberately NOT the
  // same consequence: this key is on the worker's ``_OVERRIDE_NON_ENGINE_KEYS``
  // skip list, so it never reaches engine input and there is nothing to re-run.
  // Curating the comp set moves the Market tab's summary tiles and nothing else
  // in the model.
  const selectedComps = readSelectedComps(overrides);
  const persistSelectedComps = async (keys: string[]) => {
    // The PATCH sends the WHOLE blob, so writing before the deal row has
    // loaded would send ``{}`` plus this key and drop every other override.
    // Refuse instead — the analyst re-clicks a moment later.
    if (!deal) {
      toast('Still loading this deal — try again in a moment', { type: 'error' });
      return;
    }
    const next = { ...overrides, [SELECTED_COMPS_KEY]: { value: keys, note: SELECTED_COMPS_NOTE } };
    try {
      await api.deals.update(dealId, { field_overrides: next });
      refreshDeal();
    } catch {
      toast('Could not save the comp selection', { type: 'error' });
    }
  };

  const submarketLabel = deal?.city ?? workerMarket?.market ?? null;
  const compCount = workerComps?.comps.length ?? null;
  const tabCaption =
    tab === 'market-overview'
      ? `STR · CoStar Hospitality${strTrend?.comp_set_size ? ` — ${strTrend.comp_set_size}-property comp set` : ''}`
      : tab === 'transaction-comps'
        ? compCount != null
          ? `${compCount} comp${compCount === 1 ? '' : 's'} extracted from OMs`
          : 'Extracted from Offering Memorandums'
        : 'Subject vs. competitive set · 2019–2033';
  const ttmLabel = 'TTM';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Intro card (canonical copy) */}
      <div
        style={{
          background: palette.cardWhite,
          border: `1px solid ${palette.border}`,
          borderRadius: 10,
          padding: '12px 16px',
          display: 'flex',
          flexDirection: 'column',
          gap: 3,
        }}
      >
        <span style={{ fontSize: 13.5, fontWeight: 700, color: palette.ink }}>Market</span>
        <span style={{ fontSize: 12.5, color: palette.textSecondary, lineHeight: 1.55, maxWidth: 960 }}>
          What&apos;s happening in this submarket — recent performance trends, the supply pipeline, demand
          drivers, and recent sales of comparable hotels. The basis for your projections and exit valuation.
        </span>
      </div>

      <SubTabNav
        items={SUB_TABS.map((s) => ({ id: s.id, label: s.label }))}
        activeId={tab}
        onSelect={(id) => setTab(id as SubTab)}
        caption={tabCaption}
      />

      {tab === 'market-overview' &&
        (hasStr && strTrend ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <SubmarketSnapshot
              derivedComp={derivedComp}
              compSetSize={strTrend.comp_set_size ?? null}
              compKeyCount={compKeyCount}
              submarketLabel={submarketLabel}
            />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(430px,1fr))', gap: 14 }}>
              <HistoricalMarketPerf derivedComp={derivedComp} ttmLabel={ttmLabel} />
              <MonthlyTtmCard seasonality={null} />
            </div>
            <SubjectVsCompSet
              strTrend={strTrend}
              derivedComp={derivedComp}
              compKeyCount={compKeyCount}
              strBasis={strBasis}
              strBasisSource={strBasisSource}
              strBasisSettled={strBasisSettled}
              baseYearOccPct={baseYearOccPct}
              baseYearAdr={baseYearAdr}
              strRunning={strRunning}
              onToggleStr={toggleStrSeed}
              onRerun={() => void run()}
              dealId={dealId}
              subjectState={stateOf('revenue', 'year1_occupancy', 'document_sourced')}
            />
            <IndexSummary
              strTrend={strTrend}
              onOpenIndex={() => setTab('index-analysis')}
              state={stateOf('revenue', 'rgi_revpar_index', 'document_sourced')}
            />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(400px,1fr))', gap: 14 }}>
              <SupplyPipeline compKeyCount={compKeyCount} />
              <DemandDrivers />
            </div>
          </div>
        ) : (
          <div
            style={{
              background: palette.cardWhite,
              border: `1px solid ${palette.border}`,
              borderRadius: 10,
              padding: '64px 24px',
              textAlign: 'center',
            }}
          >
            <div
              style={{
                width: 48,
                height: 48,
                borderRadius: 10,
                background: 'rgba(176,175,170,.18)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                margin: '0 auto 16px',
              }}
            >
              <MapPinned size={20} color={palette.textFaint} />
            </div>
            <h3 style={{ fontSize: 15, fontWeight: 700, color: palette.ink, margin: 0 }}>No market data yet</h3>
            {submarketLabel && (
              <p style={{ fontSize: 12, color: palette.textSecondary, marginTop: 6, fontWeight: 600 }}>{submarketLabel}</p>
            )}
            <p style={{ fontSize: 12.5, color: palette.textMuted, marginTop: 4, maxWidth: 460, marginInline: 'auto', lineHeight: 1.6 }}>
              We don&apos;t have benchmark data for this submarket yet. Open the Data Library to add it (paste in
              an STR report or attach a saved market).
            </p>
            <Link
              href="/data-library?tab=market"
              style={{
                display: 'inline-block',
                marginTop: 16,
                background: palette.inkNavy,
                color: '#fff',
                borderRadius: 6,
                padding: '8px 16px',
                fontSize: 12.5,
                fontWeight: 600,
                textDecoration: 'none',
              }}
            >
              Open Data Library
            </Link>
          </div>
        ))}

      {tab === 'transaction-comps' && (
        <TransactionCompsSection
          workerComps={workerComps}
          dealId={dealId}
          entryPerKey={entryPerKey}
          entryCapPct={entryCapPct}
          exitCapPct={exitCapPct}
          storedSelection={selectedComps}
          onPersistSelection={persistSelectedComps}
        />
      )}

      {tab === 'index-analysis' && <IndexAnalysisSection dealId={dealId} />}
    </div>
  );
}
