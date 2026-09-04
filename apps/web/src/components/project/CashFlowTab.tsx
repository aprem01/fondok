'use client';
import { useMemo, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { Activity } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineRunHistory from './EngineRunHistory';
import { fmtCurrency, fmtMillions, cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
import {
  StatementTable,
  KpiTile,
  SectionCard,
  SubTabNav,
  ProvenanceDot,
  palette,
  prov,
  type StatementRow,
  type StatementCell,
} from '@/components/design';
import type {
  CashFlowStatementOutput,
  CashFlowStatementLine,
  ValueState,
} from '@/lib/api';
import {
  buildSummary,
  hasCashFlowStatement,
  lineByLabel,
  periodHeaders,
  rowState,
  type CashFlowKpi,
  type CashFlowBridgeRow,
} from './cashFlowStatement';

const subTabs = ['Summary', 'Unlevered', 'Levered / Equity'];

// Per-sub-tab caption, right-aligned on the sub-tab row (Cash Flow Tab.dc.html
// `subTabCaption`).
function subTabCaption(tab: string): string {
  if (tab === 'Summary') return 'What the deal returns to equity, and when';
  if (tab === 'Unlevered') return 'Property cash flow before financing';
  return 'How debt, refinance and exit reach equity';
}

// KPI tile labels — canonical casing (Cash Flow Tab.dc.html `summary`): sentence
// case, not the title-case the view-model emits.
const KPI_LABEL_CANONICAL: Record<string, string> = {
  'Total Equity Invested': 'Total equity invested',
  'Operating Cash Flow to Equity': 'Operating cash flow to equity',
  'Net Refinance Proceeds': 'Net refinance proceeds',
  'Net Exit Proceeds': 'Net exit proceeds',
  'Total Cash Returned to Equity': 'Total cash returned to equity',
};

// Statement row labels — canonical strings (Cash Flow Tab.dc.html `renderVals`).
// The worker's ``cash_flow`` engine emits the fuller "Net Operating Income" /
// "FF&E Reserve" labels; the canonical unlevered statement uses the terse "NOI" /
// "CapEx". This is a DISPLAY-ONLY relabel: the row's provenance dot, footing and
// every ``lineByLabel`` lookup keep using the worker's original ``line.label``
// (only the visible text changes), so no engine value or reconciliation moves.
const STATEMENT_LABEL_CANONICAL: Record<string, string> = {
  'Net Operating Income': 'NOI',
  'FF&E Reserve': 'CapEx',
};

// The Data Key legend is mounted ONCE at the page level (page.tsx renders
// <DataKey/> under the tab bar for every tab), so this tab shows no legend of
// its own — only the per-row ProvenanceDots the StatementTable draws.

// The 4 cross-tab link chips on the output-only banner (Cash Flow Tab.dc.html
// banner). Tab ids match the project page's ?tab= routing.
const CROSS_TAB_LINKS: { label: string; tab: string }[] = [
  { label: 'Financials →', tab: 'pl' },
  { label: 'Investment →', tab: 'investment' },
  { label: 'Debt →', tab: 'debt' },
  { label: 'Partnership →', tab: 'partnership' },
];

function OutputOnlyBanner({ onNavigate }: { onNavigate: (tab: string) => void }) {
  return (
    <div className="flex flex-wrap items-center gap-3 bg-ink-300/5 border border-border rounded-md px-3.5 py-2.5 mb-4 text-[11.5px] text-ink-500">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-400 whitespace-nowrap">
        Output only
      </span>
      <span>
        No assumptions are entered here — values flow from Financials, Investment and Debt.
        GP/LP allocation happens in Partnership.
      </span>
      <span className="flex gap-3 ml-auto">
        {CROSS_TAB_LINKS.map((l) => (
          <button
            key={l.tab}
            type="button"
            onClick={() => onNavigate(l.tab)}
            className="font-semibold whitespace-nowrap cursor-pointer"
            style={{ color: '#2f4a8c' }}
          >
            {l.label}
          </button>
        ))}
      </span>
    </div>
  );
}

// Render a signed dollar cell: '—' for null, parentheses for negatives.
function cell(v: number | null | undefined): string {
  if (v == null) return '—';
  return v < 0 ? `(${fmtCurrency(-v)})` : fmtCurrency(v);
}

export default function CashFlowTab() {
  const [tab, setTab] = useState('Summary');
  const params = useParams();
  const router = useRouter();
  const { toast } = useToast();
  const dealId = (params?.id as string | undefined) ?? '';
  const [computing, setComputing] = useState(false);
  const { outputs } = useEngineOutputs(dealId);

  // The composed cash-flow statement now comes straight from the worker's
  // ``cash_flow`` engine — no in-browser re-derivation.
  const cfOut = getEngineField<CashFlowStatementOutput>(outputs, 'cash_flow');
  const cf = useMemo(() => (hasCashFlowStatement(cfOut) ? cfOut : null), [cfOut]);

  // Cross-tab navigation for the output-only banner chips — mirrors the project
  // page's ?tab= routing without touching page.tsx.
  const go = (tabId: string) => {
    if (!dealId) return;
    router.push(`/projects/${dealId}?tab=${tabId}`, { scroll: false });
  };

  // Fallback: a deal whose canonical run predates the cash_flow engine has no
  // statement yet — keep the "Run Model" placeholder so the tab never renders
  // empty or crashes.
  if (!cf) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          <IntroCard
            dismissKey="cashflow-intro"
            title="The Cash Flow Engine"
            body={
              <>
                What hits the equity investors&apos; pockets each year, after debt service and capex.
                <span className="font-semibold"> Levered</span> = after debt; <span className="font-semibold">unlevered</span> = before debt.
                Distributions, exit proceeds, and the cumulative cash to LPs all live here.
              </>
            }
          />
          <EngineHeader
            name="Cash Flow Engine"
            desc="Computes levered and unlevered cash flow from operations through hold period."
            outputs={['Levered CF', 'Unlevered CF', 'CoC', 'DSCR']}
            dependsOn="P&L · Investment · Debt · Returns"
            dealId={dealId}
            engineName="returns"
            runMode="all"
            onRunStart={() => setComputing(true)}
            onRunComplete={() => setComputing(false)}
          />
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <Activity size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">No cash flow output yet</h3>
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              Cash flow depends on the <span className="font-medium">P&amp;L</span> engine. Run the P&amp;L
              first to populate levered and unlevered schedules.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => toast('Engine queued — check back shortly', { type: 'info' })}
            >
              Run Model
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
          dismissKey="cashflow-intro"
          title="The Cash Flow Engine"
          body={
            <>
              The operating model converted into property and equity cash flow.
              <span className="font-semibold"> Levered</span> = after debt; <span className="font-semibold">unlevered</span> = before debt.
              Values flow from Financials, Investment and Debt — nothing is entered directly here.
            </>
          }
        />
        <EngineHeader
          name="Cash Flow Engine"
          desc="Computes levered and unlevered cash flow from operations through hold period."
          outputs={['Levered CF', 'Unlevered CF', 'CoC', 'DSCR']}
          dependsOn="P&L · Investment · Debt · Returns"
          complete
          dealId={dealId}
          engineName="returns"
          runMode="all"
          onRunStart={() => setComputing(true)}
          onRunComplete={() => setComputing(false)}
        />

        <OutputOnlyBanner onNavigate={go} />

        <SubTabNav
          className="mb-3"
          items={subTabs.map((t) => ({ id: t, label: t }))}
          activeId={tab}
          onSelect={setTab}
          caption={subTabCaption(tab)}
        />

        <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
          {tab === 'Summary' && <SummaryView cf={cf} />}
          {tab === 'Unlevered' && (
            <CashFlowStatement
              cf={cf}
              section="unlevered"
              title="Unlevered Cash Flow"
              caption="Property level · before financing"
              footnote="Terminal value is shown as its own bridge — gross sale proceeds less selling and disposition costs — rather than folded into a single unexplained exit-year number."
            />
          )}
          {tab === 'Levered / Equity' && (
            <CashFlowStatement
              cf={cf}
              section="levered"
              title="Levered / Equity Cash Flow"
              caption="After financing · before the partnership waterfall"
              footnote={
                lineByLabel(cf.levered, 'Net Refinance Cash-Out')
                  ? 'Every column foots: at close, unlevered cash flow plus debt proceeds equals the equity required. The refinance is broken into its own line so the net cash-out is traceable rather than appearing as an unexplained spike.'
                  : 'Every column foots: at close, unlevered cash flow plus debt proceeds equals the equity required. This deal has no refinance.'
              }
            />
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

function KpiCard({ kpi }: { kpi: CashFlowKpi }) {
  const flash = useFlash(kpi.value ?? 0);
  // Design KPI tile (Cash Flow Tab.dc.html `summary` cards): #fff · 1px #eae9e4 ·
  // radius 10 · 13/15 padding · 10px/700 eyebrow · 19px/700 tabular value (ink).
  // `value-flash` keeps the on-recompute highlight the light card had.
  return (
    <KpiTile
      className={cn(flash && 'value-flash')}
      label={KPI_LABEL_CANONICAL[kpi.label] ?? kpi.label}
      value={kpi.value == null ? '—' : fmtMillions(kpi.value, 2)}
      sub={kpi.sub}
    />
  );
}

/**
 * "Equity Funding Reference" (Cash Flow Tab.dc.html) — restates the equity
 * funded into the deal, read straight from the worker ``cash_flow`` output
 * (``levered_cash_flow``): the close-period outflow is the initial equity, and
 * any negative operating period is an additional draw. Reference ONLY — this
 * figure is already inside net cash flow to equity (the levered CF at close), so
 * it is shown, never re-added. When the levered series is absent the rows fall
 * back to awaiting-data em-dashes.
 */
function EquityFundingReference({ cf }: { cf: CashFlowStatementOutput }) {
  const lev = cf.levered_cash_flow ?? [];
  const hasData = lev.length > 0 && typeof lev[0] === 'number';
  const initial = hasData ? lev[0] : null;
  const additional = hasData
    ? lev.slice(1).reduce<number>((s, v) => s + (typeof v === 'number' && v < 0 ? v : 0), 0)
    : null;

  const rows: { label: string; value: number | null; state: ValueState; title: string }[] = [
    {
      label: 'Initial equity required',
      value: initial,
      state: hasData ? 'linked' : 'awaiting_data',
      title:
        'Investment → Required equity. Reference only — equity funding is already reflected in net cash flow to equity and is not added again.',
    },
    {
      label: 'Additional equity required',
      value: additional,
      state: hasData ? 'calculated' : 'awaiting_data',
      title:
        'Reference only — any additional equity is already reflected in net cash flow to equity and is not added again.',
    },
  ];

  const valueColor = (state: ValueState, v: number | null): string =>
    v == null ? prov.muted : state === 'linked' ? prov.green : prov.gray;

  return (
    <div
      style={{
        background: '#fff',
        border: '1px solid #eae9e4',
        borderRadius: 10,
        padding: '16px 18px',
        marginTop: 14,
        marginBottom: 14,
      }}
    >
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          gap: 14,
          marginBottom: 8,
          flexWrap: 'wrap',
        }}
      >
        <span
          style={{
            fontSize: 12,
            fontWeight: 700,
            color: '#8a8a86',
            textTransform: 'uppercase',
            letterSpacing: '.03em',
          }}
        >
          Equity Funding Reference
        </span>
        <span style={{ fontSize: 11, color: '#b0afaa' }}>
          Reference only — already reflected in net cash flow to equity, never added again
        </span>
      </div>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))',
          gap: '0 32px',
        }}
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
              borderBottom: '1px solid #f7f6f3',
            }}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
              <ProvenanceDot state={r.state} size={8} />
              <span style={{ color: '#6b6f76' }}>{r.label}</span>
            </span>
            <span style={{ color: valueColor(r.state, r.value), fontVariantNumeric: 'tabular-nums' }}>
              {cell(r.value)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function SummaryView({ cf }: { cf: CashFlowStatementOutput }) {
  const { kpis, bridge } = buildSummary(cf);
  const headers = periodHeaders(cf);

  // summaryNote (Cash Flow Tab.dc.html) — the deal-level equity reconciliation
  // sentence, built from the already-composed KPI values (no new math).
  const kpiVal = (label: string) => kpis.find((k) => k.label === label)?.value ?? 0;
  const summaryNote =
    'Net cash flow to equity is the canonical deal-level equity cash-flow series: operating cash flow to equity ' +
    fmtMillions(kpiVal('Operating Cash Flow to Equity'), 2) +
    ' + net refinance ' +
    fmtMillions(kpiVal('Net Refinance Proceeds'), 2) +
    ' + net exit ' +
    fmtMillions(kpiVal('Net Exit Proceeds'), 2) +
    ' = ' +
    fmtMillions(kpiVal('Total Cash Returned to Equity'), 2) +
    '. Partnership allocates this cash between GP and LP; Returns calculates performance from the resulting cash flows.';

  return (
    <>
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3 mb-4">
        {kpis.map((k) => (
          <KpiCard key={k.label} kpi={k} />
        ))}
      </div>

      <SectionCard
        variant="title"
        title="Cash Flow Bridge"
        note="Property cash flow → financing → equity"
      >
        <StatementTable
          lineItemHeader="LINE ITEM"
          columns={headers}
          gridTemplateColumns={statementGridCols(headers.length)}
          rows={bridgeRows(bridge, headers.length)}
          footnote="Every column foots: property cash flow plus financing equals net cash flow to equity. Net cash flow to equity is the canonical deal-level equity cash-flow series — Partnership allocates it between GP and LP; Returns calculates performance from it."
        />
      </SectionCard>

      <EquityFundingReference cf={cf} />

      <div
        style={{
          background: '#fbfbf9',
          border: '1px solid #eae9e4',
          borderRadius: 10,
          padding: '14px 18px',
          fontSize: 11.5,
          color: '#6b6f76',
          lineHeight: 1.55,
        }}
      >
        {summaryNote}
      </div>
    </>
  );
}

// ── Dark-navy statement grid (design/canonical/Cash Flow Tab.dc.html) ───────
// The canonical grid: 230px sticky LINE ITEM column + N right-aligned metric
// cols; #14213d navy header, #c9cede header text, #2a3a5c header dividers, and
// #f7f6f3 row hairlines. Values carry origin by COLOUR — a `calc` (composed
// bottom line) renders bold near-black on the #fbfbf9 subtotal band; a `linked`
// row renders green; a period the row doesn't touch renders a muted em-dash.
// This is exactly the shared `StatementTable`'s `row()`/`dot()` treatment, so
// we map the worker's lines onto it rather than restyling a bespoke table.

/** Design grid template: 230px line-item column + N metric cols (minmax 126px). */
function statementGridCols(n: number): string {
  return `230px repeat(${n},minmax(126px,1fr))`;
}

/** Colour a value cell by the canonical `row()` rule — bold→near-black,
 *  linked→green, calc→grey, absent→muted. Text keeps the signed `cell()`
 *  formatting (parenthesised negatives, em-dash for null). */
function statementCells(
  values: (number | null | undefined)[],
  n: number,
  { total, linked }: { total: boolean; linked: boolean },
): StatementCell[] {
  const bodyColor = total ? prov.black : linked ? prov.green : prov.gray;
  return Array.from({ length: n }, (_, ci) => {
    const v = values[ci];
    return { text: cell(v), color: v == null ? prov.muted : bodyColor };
  });
}

/** Map a worker cash-flow section (unlevered / levered) onto dark-grid rows.
 *  `total` (bold, #fbfbf9 band) tracks the existing `kind === 'calc'` semantics;
 *  the provenance dot keeps the full 6-state `rowState`. */
function sectionRows(
  cf: CashFlowStatementOutput,
  section: 'unlevered' | 'levered',
  n: number,
): StatementRow[] {
  return cf[section].map((line: CashFlowStatementLine) => {
    const total = line.kind === 'calc';
    return {
      // Display-only canonical relabel (NOI / CapEx); provenance + footing below
      // still key off the worker's original ``line.label``.
      label: STATEMENT_LABEL_CANONICAL[line.label] ?? line.label,
      title: line.note ?? undefined,
      state: rowState(cf, section, line),
      total,
      bg: total ? palette.surfaceTint : undefined,
      cells: statementCells(line.values, n, { total, linked: line.kind === 'linked' }),
    };
  });
}

/** Summary "Cash Flow Bridge" rows — all composed (calc) lines, so the dot is
 *  Calculated and the emphasised total foots the column. */
function bridgeRows(bridge: CashFlowBridgeRow[], n: number): StatementRow[] {
  return bridge.map((row) => {
    const total = !!row.bold;
    return {
      label: row.label,
      state: 'calculated' as ValueState,
      total,
      bg: total ? palette.surfaceTint : undefined,
      cells: statementCells(row.values, n, { total, linked: false }),
    };
  });
}

function CashFlowStatement({
  cf,
  section,
  title,
  caption,
  footnote,
}: {
  cf: CashFlowStatementOutput;
  section: 'unlevered' | 'levered';
  title: string;
  caption: string;
  footnote?: string;
}) {
  const headers = periodHeaders(cf);
  return (
    <SectionCard variant="title" title={title} note={caption}>
      <StatementTable
        lineItemHeader="LINE ITEM"
        columns={headers}
        gridTemplateColumns={statementGridCols(headers.length)}
        rows={sectionRows(cf, section, headers.length)}
        footnote={footnote}
      />
    </SectionCard>
  );
}
