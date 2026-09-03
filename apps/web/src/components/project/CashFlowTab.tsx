'use client';
import { useMemo, useState, type CSSProperties } from 'react';
import { useParams } from 'next/navigation';
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
  type CashFlowSection,
} from './cashFlowStatement';

const subTabs = ['Summary', 'Unlevered', 'Levered / Equity'];

// ── Data Key — the 6-state provenance vocabulary (FON-65). A minimal inline
// mapping lives here; the shared canonical Data Key strip is a separate task.
const STATE_ORDER: ValueState[] = [
  'document_sourced',
  'linked',
  'assumption',
  'calculated',
  'awaiting_data',
  'needs_review',
];

const STATE_META: Record<ValueState, { label: string; style: CSSProperties }> = {
  document_sourced: { label: 'Document sourced', style: { background: 'oklch(45% 0.12 155)' } },
  linked: { label: 'Linked', style: { background: '#fff', border: '2px solid oklch(45% 0.12 155)' } },
  assumption: { label: 'Assumption', style: { background: 'oklch(45% 0.14 260)' } },
  calculated: { label: 'Calculated', style: { background: '#5f656e' } },
  awaiting_data: { label: 'Awaiting data', style: { background: '#fff', border: '1px dashed #b0afaa' } },
  needs_review: { label: 'Needs review', style: { background: 'oklch(45% 0.12 155)', boxShadow: '0 0 0 3px oklch(88% 0.07 45)' } },
};

function StateDot({ state }: { state: ValueState }) {
  return (
    <span
      title={STATE_META[state].label}
      aria-label={STATE_META[state].label}
      className="inline-block w-2 h-2 rounded-full flex-shrink-0"
      style={STATE_META[state].style}
    />
  );
}

function DataKey({ states }: { states: ValueState[] }) {
  if (states.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 mb-3 text-[11px] text-ink-500 bg-ink-300/5 border border-border rounded-md px-3 py-2">
      <span className="font-semibold uppercase tracking-wide text-ink-400">Data Key</span>
      {states.map((s) => (
        <span key={s} className="flex items-center gap-1.5">
          <StateDot state={s} /> {STATE_META[s].label}
        </span>
      ))}
    </div>
  );
}

function statesPresent(
  cf: CashFlowStatementOutput,
  section: CashFlowSection,
  lines: CashFlowStatementLine[],
): ValueState[] {
  const seen = new Set<ValueState>();
  for (const l of lines) seen.add(rowState(cf, section, l));
  return STATE_ORDER.filter((s) => seen.has(s));
}

function OutputOnlyBanner() {
  return (
    <div className="flex flex-wrap items-center gap-3 bg-ink-300/5 border border-border rounded-md px-3.5 py-2.5 mb-4 text-[11.5px] text-ink-500">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-400 whitespace-nowrap">
        Output only
      </span>
      <span>
        No assumptions are entered here — values flow from Financials, Investment and Debt.
        GP/LP allocation happens in Partnership.
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
  const { toast } = useToast();
  const dealId = (params?.id as string | undefined) ?? '';
  const [computing, setComputing] = useState(false);
  const { outputs } = useEngineOutputs(dealId);

  // The composed cash-flow statement now comes straight from the worker's
  // ``cash_flow`` engine — no in-browser re-derivation.
  const cfOut = getEngineField<CashFlowStatementOutput>(outputs, 'cash_flow');
  const cf = useMemo(() => (hasCashFlowStatement(cfOut) ? cfOut : null), [cfOut]);

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

        <OutputOnlyBanner />

        <div className="flex items-center gap-1 mb-3 border-b border-border">
          {subTabs.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={cn(
                'px-4 py-2 text-[12.5px] border-b-2 transition-colors -mb-px',
                tab === t
                  ? 'border-brand-500 text-brand-700 font-medium'
                  : 'border-transparent text-ink-500 hover:text-ink-900',
              )}
            >
              {t}
            </button>
          ))}
        </div>

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
      label={kpi.label}
      value={kpi.value == null ? '—' : fmtMillions(kpi.value, 2)}
      sub={kpi.sub}
    />
  );
}

function SummaryView({ cf }: { cf: CashFlowStatementOutput }) {
  const { kpis, bridge } = buildSummary(cf);
  const headers = periodHeaders(cf);
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
      label: line.label,
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
  const states = statesPresent(cf, section, cf[section]);
  return (
    <SectionCard variant="title" title={title} note={caption}>
      {states.length > 0 && (
        <div style={{ padding: '12px 18px 0' }}>
          <DataKey states={states} />
        </div>
      )}
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
