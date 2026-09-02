'use client';
import { useMemo, useState, type CSSProperties } from 'react';
import { useParams } from 'next/navigation';
import { Activity } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import EngineRunHistory from './EngineRunHistory';
import { fmtCurrency, fmtMillions, cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
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
          <EngineLegend />
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
        <EngineLegend />

        <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
          {tab === 'Summary' && <SummaryView cf={cf} />}
          {tab === 'Unlevered' && (
            <StatementTable
              cf={cf}
              section="unlevered"
              title="Unlevered Cash Flow"
              caption="Property level · before financing"
              footnote="Terminal value is shown as its own bridge — gross sale proceeds less selling and disposition costs — rather than folded into a single unexplained exit-year number."
            />
          )}
          {tab === 'Levered / Equity' && (
            <StatementTable
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
  return (
    <Card className={cn('p-4', flash && 'value-flash')}>
      <div className="text-[10px] font-semibold tracking-wide text-ink-400 uppercase">{kpi.label}</div>
      <div className="text-[19px] font-semibold tabular-nums mt-1.5 text-ink-900">
        {kpi.value == null ? '—' : fmtMillions(kpi.value, 2)}
      </div>
      <div className="text-[10.5px] text-ink-500 mt-1 leading-snug">{kpi.sub}</div>
    </Card>
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

      <Card className="p-0 overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-baseline justify-between gap-3 flex-wrap">
          <h3 className="text-[13px] font-semibold text-ink-900">Cash Flow Bridge</h3>
          <span className="text-[11px] text-ink-500">Property cash flow → financing → equity</span>
        </div>
        <div className="overflow-x-auto px-4 pt-3">
          <table className="w-full text-[12px] min-w-[720px]">
            <thead>
              <tr className="text-ink-500 text-[10.5px] border-b border-border">
                <th className="text-left font-medium pb-2 w-64">LINE ITEM</th>
                {headers.map((h) => (
                  <th key={h} className="text-right font-medium pb-2">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bridge.map((row, ri) => {
                const bold = !!row.bold;
                return (
                  <tr
                    key={row.label}
                    className={cn(
                      'border-b border-border/40',
                      ri % 2 === 1 && !bold && 'bg-ink-300/5',
                      bold && 'font-semibold bg-brand-50/40 border-t border-border',
                    )}
                  >
                    <td className="py-1.5">{row.label}</td>
                    {headers.map((_, ci) => {
                      const v = row.values[ci];
                      const neg = (v ?? 0) < 0;
                      return (
                        <td
                          key={ci}
                          className={cn('text-right tabular-nums', neg && !bold && 'text-danger-700')}
                        >
                          {cell(v)}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="px-4 py-3 text-[11px] text-ink-500 leading-relaxed">
          Every column foots: property cash flow plus financing equals net cash flow to equity.
          Net cash flow to equity is the canonical deal-level equity cash-flow series — Partnership
          allocates it between GP and LP; Returns calculates performance from it.
        </div>
      </Card>
    </>
  );
}

function StatementTable({
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
  const lines = cf[section];
  const headers = periodHeaders(cf);
  const states = statesPresent(cf, section, lines);
  return (
    <Card className="p-0 overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-baseline justify-between gap-3 flex-wrap">
        <h3 className="text-[13px] font-semibold text-ink-900">{title}</h3>
        <span className="text-[11px] text-ink-500">{caption}</span>
      </div>
      <div className="px-4 pt-3">
        <DataKey states={states} />
      </div>
      <div className="overflow-x-auto px-4">
        <table className="w-full text-[12px] min-w-[720px]">
          <thead>
            <tr className="text-ink-500 text-[10.5px] border-b border-border">
              <th className="text-left font-medium pb-2 w-64">LINE ITEM</th>
              {headers.map((h) => (
                <th key={h} className="text-right font-medium pb-2">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {lines.map((line, li) => {
              const isCalc = line.kind === 'calc';
              const st = rowState(cf, section, line);
              return (
                <tr
                  key={`${line.label}-${li}`}
                  className={cn(
                    'border-b border-border/40',
                    li % 2 === 1 && !isCalc && 'bg-ink-300/5',
                    isCalc && 'font-semibold bg-brand-50/40 border-t border-border',
                  )}
                >
                  <td className="py-1.5">
                    <span className="flex items-center gap-2" title={line.note ?? undefined}>
                      <StateDot state={st} />
                      <span>{line.label}</span>
                    </span>
                  </td>
                  {headers.map((_, ci) => {
                    const v = line.values[ci];
                    const neg = (v ?? 0) < 0;
                    return (
                      <td
                        key={ci}
                        className={cn(
                          'text-right tabular-nums',
                          v == null && 'text-ink-400',
                          neg && !isCalc && 'text-danger-700',
                        )}
                      >
                        {cell(v)}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {footnote && (
        <div className="px-4 py-3 text-[11px] text-ink-500 leading-relaxed">{footnote}</div>
      )}
    </Card>
  );
}
