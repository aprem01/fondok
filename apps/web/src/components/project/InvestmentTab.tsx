'use client';
import {
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from 'react';
import { useParams } from 'next/navigation';
import { Briefcase, Pencil, Check, X } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import CapexPlanPanel, { DEFAULT_CAPEX_PLAN, type CapexPlanState } from './CapexPlanPanel';
import HistoricalBaselinePanel from './HistoricalBaselinePanel';
import { useHistoricalBaseline } from '@/lib/hooks/useHistoricalBaseline';
import { fmtCurrency, fmtPct, fmtMillions, cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useTraceGraph } from '@/lib/hooks/useValueTrace';
import { IntroCard } from '@/components/help/IntroCard';
import {
  KpiTile,
  SectionCard,
  SubTabNav,
  ProvenanceDot,
  palette,
  prov,
} from '@/components/design';
import {
  api,
  isWorkerConnected,
  WorkerError,
  type TimelineResponse,
  type ValueState,
} from '@/lib/api';

// Expense engine year shape — mirrors apps/worker/app/engines/expense.py.
// Only the fields we read on the Investment tab are typed here.
interface ExpenseYearLite { year: number; noi: number }
// Revenue engine year shape - only the fields the capex panel reads.
interface RevenueYearLite { year: number; total_revenue: number }

const SUB_TABS = [
  { id: 'Deal Summary', label: 'Deal Summary' },
  { id: 'Sources & Uses', label: 'Sources & Uses' },
  { id: 'Timeline', label: 'Timeline' },
];

const SUB_CAPTION: Record<string, string> = {
  'Deal Summary': 'The assumptions that define the transaction',
  'Sources & Uses': 'Capital required, and where it comes from',
  Timeline: 'Key dates from acquisition through exit',
};

// ─── Canonical value vocabulary (design/canonical/Investment Tab.dc.html) ──────
// doc → document sourced · linked → owned by another engine · input → editable
// assumption · calc → calculated by Fondok · awaiting → not yet available.
type ValueKind = 'doc' | 'linked' | 'input' | 'calc' | 'awaiting';

/** Row value color — mirrors the canonical `colorFor()` (BLUE/GREEN/GRAY/BLACK/MUTED). */
function valueColor(kind: ValueKind, bold: boolean, overridden: boolean): string {
  if (overridden) return prov.blue;
  if (bold) return prov.black;
  if (kind === 'input') return prov.blue;
  if (kind === 'linked' || kind === 'doc') return prov.green;
  if (kind === 'awaiting') return prov.muted;
  return prov.gray;
}

/** Provenance dot state — real /provenance `state` wins, else the canonical kind. */
function kindToState(kind: ValueKind): ValueState {
  switch (kind) {
    case 'doc': return 'document_sourced';
    case 'linked': return 'linked';
    case 'input': return 'assumption';
    case 'awaiting': return 'awaiting_data';
    default: return 'calculated';
  }
}

interface RowDef {
  id: string;
  label: string;
  kind: ValueKind;
  value: ReactNode;          // formatted display, or a custom editor node
  state: ValueState;         // resolved provenance state (drives the dot)
  bold?: boolean;
  overridden?: boolean;
  note?: string;
  link?: { label: string; tab: string };
}

export default function InvestmentTab() {
  const [tab, setTab] = useState('Deal Summary');
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { toast } = useToast();
  const { outputs, previous } = useEngineOutputs(dealId);
  const { deal, refresh: refreshDeal } = useDeal(dealId);
  const { baseline: historicalBaseline } = useHistoricalBaseline(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  // Computed-value provenance graphs — dots read the real /provenance `state`
  // for capital / returns outputs, falling back to the canonical semantic kind.
  const capitalTrace = useTraceGraph('capital');
  const returnsTrace = useTraceGraph('returns');
  const tracedState = useCallback(
    (engine: 'capital' | 'returns', path: string): ValueState | null => {
      const g = engine === 'capital' ? capitalTrace : returnsTrace;
      return g?.get(path)?.state ?? null;
    },
    [capitalTrace, returnsTrace],
  );

  // ─── Editable transaction assumptions (canonical path) ────────────────
  // Edits PATCH field_overrides and kick a debounced run-all so the downstream
  // Debt / Cash Flow / Returns engines re-derive. This is the SAME path Deal
  // Summary already used — never the local assumptions store (Move-2 step 4).
  const isMockIdInv = /^\d+$/.test(dealId);
  const liveMode = isWorkerConnected() && !isMockIdInv;
  const invOverrides = (deal?.field_overrides ?? {}) as Record<string, unknown>;
  const invRun = useEngineRun(liveMode ? dealId : '', 'returns', { runMode: 'all' });
  const invRerunRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onSaveAssumption = useCallback(
    async (key: string, value: number) => {
      if (!liveMode) {
        toast('Editing is disabled on demo deals', { type: 'info' });
        return;
      }
      const next = { ...invOverrides, [key]: value };
      try {
        await api.deals.update(dealId, { field_overrides: next });
        toast('Saved — re-running the model…', { type: 'success' });
        void refreshDeal?.();
        if (invRerunRef.current) clearTimeout(invRerunRef.current);
        invRerunRef.current = setTimeout(() => { void invRun.run(); }, 1200);
      } catch (err) {
        const detail = err instanceof WorkerError ? err.body : String(err);
        toast(`Save failed: ${detail || 'worker rejected update'}`, { type: 'error' });
      }
    },
    [invOverrides, dealId, liveMode, toast, refreshDeal, invRun],
  );

  // Dated transaction timeline (FON-71). Refetched after a save or a run.
  const [timeline, setTimeline] = useState<TimelineResponse | null>(null);
  const refreshTimeline = useCallback(() => {
    if (!liveMode) { setTimeline(null); return; }
    const ac = new AbortController();
    api.engines.timeline(dealId, ac.signal)
      .then(setTimeline)
      .catch(() => { /* best-effort; tab shows pending rows */ });
    return () => ac.abort();
  }, [dealId, liveMode]);
  useEffect(() => { refreshTimeline(); }, [refreshTimeline, runToken]);

  // Acquisition close date drives every timeline date. It feeds no engine, so
  // we save it and just refetch the timeline — no full model re-run.
  const onSaveCloseDate = useCallback(
    async (iso: string) => {
      if (!liveMode) {
        toast('Editing is disabled on demo deals', { type: 'info' });
        return;
      }
      const next = { ...invOverrides, acquisition_close_date: iso };
      try {
        await api.deals.update(dealId, { field_overrides: next });
        toast('Acquisition date saved', { type: 'success' });
        void refreshDeal?.();
        refreshTimeline();
      } catch (err) {
        const detail = err instanceof WorkerError ? err.body : String(err);
        toast(`Save failed: ${detail || 'worker rejected update'}`, { type: 'error' });
      }
    },
    [invOverrides, dealId, liveMode, toast, refreshDeal, refreshTimeline],
  );

  // Wave 2 P2.5 — local capex plan state (worker capex_plan output not wired yet).
  const [capexPlan, setCapexPlan] = useState<CapexPlanState>(DEFAULT_CAPEX_PLAN);

  // ─── Worker engine field reads (no fixtures — '—' until the engine emits) ──
  const wPurchase = getEngineField<number>(outputs, 'capital', 'purchase_price');
  const wPricePerKey = getEngineField<number>(outputs, 'capital', 'price_per_key');
  const wEntryCap = getEngineField<number>(outputs, 'capital', 'entry_cap_rate');
  const wTotalCapital =
    getEngineField<number>(outputs, 'capital', 'total_capital_usd') ??
    getEngineField<number>(outputs, 'capital', 'total_capital');
  const wTotalCapitalPerKey = getEngineField<number>(outputs, 'capital', 'total_capital_per_key');
  const wEquity = getEngineField<number>(outputs, 'capital', 'equity_amount');
  const wDebtAmount = getEngineField<number>(outputs, 'capital', 'debt_amount');
  const wLoanAmount = getEngineField<number>(outputs, 'debt', 'loan_amount');

  const wExpenseYears = getEngineField<ExpenseYearLite[]>(outputs, 'expense', 'years');
  const wYearOneNoi = wExpenseYears && wExpenseYears.length > 0 ? wExpenseYears[0].noi : undefined;

  const wGrossSale = getEngineField<number>(outputs, 'returns', 'gross_sale_price');
  const wExitCap = getEngineField<number>(outputs, 'returns', 'exit_cap_rate');
  const wTerminalNoi =
    getEngineField<number>(outputs, 'returns', 'terminal_noi_usd') ??
    getEngineField<number>(outputs, 'returns', 'terminal_noi');
  const wSellingCosts = getEngineField<number>(outputs, 'returns', 'selling_costs');
  const wHoldYears = getEngineField<number>(outputs, 'returns', 'hold_years');

  const wCapitalUses = getEngineField<Array<{ label?: string; amount?: number }>>(
    outputs, 'capital', 'uses',
  );
  const findUse = (re: RegExp): number | undefined => {
    const hit = (wCapitalUses ?? []).find((u) => re.test(String(u?.label ?? '')));
    return typeof hit?.amount === 'number' ? hit.amount : undefined;
  };

  const hasCapitalOutput = wPurchase != null || wPricePerKey != null || wEntryCap != null;

  const propertyKeys = (deal?.keys && deal.keys > 0) ? deal.keys : undefined;
  const has = (v: number | undefined): v is number => v != null && Number.isFinite(v);
  const overridden = (key: string): boolean => invOverrides[key] !== undefined;

  if (!hasCapitalOutput) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          <IntroCard
            dismissKey="investment-intro"
            title="The Investment Engine"
            body={
              <>
                Defines the deal structure: what you&apos;re buying, what you&apos;re paying,
                when you&apos;re selling. Sources &amp; Uses, key dates, and the entry cap rate live
                here. This is the starting point of the model — every other engine builds on it.
              </>
            }
          />
          <EngineHeader
            name="Investment Engine"
            desc="Defines deal structure, purchase price, key dates, and investment thesis for the transaction."
            outputs={['Purchase Price', 'Price/Key', 'Entry Cap', '+1']}
            dependsOn={null}
            dealId={dealId}
            engineName="capital"
            runMode="all"
            onRunStart={() => setComputing(true)}
            onRunComplete={() => {
              setComputing(false);
              setRunToken(Date.now());
            }}
          />
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <Briefcase size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">Investment Engine unavailable</h3>
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              Upload an <span className="font-medium">Offering Memorandum</span> (the broker&apos;s pitch deck) into
              the Data Room and we&apos;ll populate the investment summary automatically.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => toast('Engine queued — check back shortly', { type: 'info' })}
            >
              Run Investment Engine
            </Button>
          </Card>
          <EngineRunHistory dealId={dealId} />
        </div>
        <EngineRightRail />
      </div>
    );
  }

  // ─── Derived model (all values engine-sourced) ────────────────────────
  const keys = propertyKeys;
  const entryNoi = wYearOneNoi;
  const pricePerKey = wPricePerKey;
  const purchase = wPurchase
    ?? (has(pricePerKey) && has(keys) ? pricePerKey * keys : undefined);
  const entryCap = (has(entryNoi) && has(purchase) && purchase > 0)
    ? entryNoi / purchase
    : wEntryCap;
  const closing = findUse(/closing/i);
  const closingPct = (has(closing) && has(purchase) && purchase > 0) ? closing / purchase : undefined;

  const renoBudget = findUse(/renovat/i);
  // Hard / soft / professional-fee split — read straight from the capital
  // engine's renovation_breakdown output (defaults 75/15/10, per
  // design/canonical/Investment Tab.dc.html), never re-derived from the total
  // here. Absent (no renovation budget → breakdown is null) renders '—'.
  const renoBreakdown = getEngineField<{ hard?: number; soft?: number; fees?: number }>(outputs, 'capital', 'renovation_breakdown');
  const renoHard = renoBreakdown?.hard;
  const renoSoft = renoBreakdown?.soft;
  const renoProf = renoBreakdown?.fees;

  const totalUses = wTotalCapital;
  const totalUsesPerKey = has(wTotalCapitalPerKey)
    ? wTotalCapitalPerKey
    : (has(totalUses) && has(keys) ? totalUses / keys : undefined);
  const loan = wLoanAmount ?? wDebtAmount;
  const equity = has(wEquity)
    ? wEquity
    : (has(totalUses) && has(loan) ? totalUses - loan : undefined);

  const terminalNoi = wTerminalNoi;
  const exitCap = wExitCap;
  const grossExit = wGrossSale;
  const exitPerKey = (has(grossExit) && has(keys)) ? grossExit / keys : undefined;
  const sellingCosts = wSellingCosts;
  const netExit = has(grossExit)
    ? grossExit - (sellingCosts ?? 0)
    : undefined;
  const holdYears = wHoldYears;

  // Formatting helpers matching the canonical (money / mm / pct).
  const money = (v: number | undefined): ReactNode => (has(v) ? fmtCurrency(v) : '—');
  const mm = (v: number | undefined): string => (has(v) ? fmtMillions(v, 2) : '—');
  const pctv = (v: number | undefined, d = 2): ReactNode => (has(v) ? fmtPct(v, d) : '—');

  return (
    <div className="flex gap-4">
      <div className="flex-1 min-w-0">
        <IntroCard
          dismissKey="investment-intro"
          title="The Investment Engine"
          body={
            <>
              Defines the deal structure: what you&apos;re buying, what you&apos;re paying,
              when you&apos;re selling. Sources &amp; Uses, key dates, and the entry cap rate live
              here. This is the starting point of the model — every other engine builds on it.
            </>
          }
        />
        <EngineHeader
          name="Investment Engine"
          desc="Deal structure, transaction capitalization and key dates — what we're buying, for how much, when, and the capital required to execute."
          outputs={['Purchase Price', 'Price/Key', 'Entry Cap', '+1']}
          dependsOn={null}
          complete
          dealId={dealId}
          engineName="capital"
          runMode="all"
          onRunStart={() => setComputing(true)}
          onRunComplete={() => {
            setComputing(false);
            setRunToken(Date.now());
          }}
        />

        <WhatJustHappened
          engine="capital"
          engineLabel="Capital"
          outputs={outputs}
          previous={previous}
          runToken={runToken}
        />

        <SubTabNav
          items={SUB_TABS}
          activeId={tab}
          onSelect={setTab}
          caption={SUB_CAPTION[tab]}
          style={{ marginBottom: 14 }}
        />

        <div className={cn(computing && 'relative pointer-events-none opacity-60')}>

          {tab === 'Deal Summary' && (() => {
            // ─── KPI tiles (5) — design/canonical `kpis` ──────────────
            const kpis = [
              { label: 'Total Cost Basis', value: mm(totalUses), sub: has(totalUsesPerKey) ? `${fmtCurrency(totalUsesPerKey)} / key` : '' },
              { label: 'Purchase Price', value: mm(purchase), sub: has(entryCap) ? `${fmtPct(entryCap, 2)} going-in` : '' },
              { label: 'Renovation / PIP', value: mm(renoBudget), sub: (has(renoBudget) && has(keys)) ? `${fmtCurrency(renoBudget / keys)} / key` : '' },
              { label: 'Required Equity', value: mm(equity), sub: (has(equity) && has(totalUses) && totalUses > 0) ? `${fmtPct(equity / totalUses, 1)} of total uses` : '' },
              { label: 'Gross Exit Value', value: mm(grossExit), sub: has(exitCap) ? `${fmtPct(exitCap, 2)} exit cap` : '' },
            ];

            // ─── Acquisition section ──────────────────────────────────
            const acquisition: RowDef[] = [
              {
                id: 'keys', label: 'Keys', kind: 'doc', state: 'document_sourced',
                value: (
                  <KeysOverride dealId={dealId} currentKeys={keys} onSaved={() => refreshDeal()} />
                ),
              },
              {
                id: 'acqDate', label: 'Acquisition Date', kind: 'input',
                state: overridden('acquisition_close_date') ? 'assumption' : 'assumption',
                overridden: overridden('acquisition_close_date'),
                value: (
                  <CloseDateField
                    iso={(invOverrides['acquisition_close_date'] as string | undefined) ?? timeline?.close_date ?? null}
                    editable={liveMode}
                    onSave={onSaveCloseDate}
                  />
                ),
              },
              {
                id: 'entryNoi', label: 'Entry / Run-Rate NOI', kind: 'linked', state: 'linked',
                value: money(entryNoi), link: { label: '→ Financials', tab: 'pl' },
              },
              {
                id: 'entryCap', label: 'Entry Cap Rate', kind: 'calc',
                state: tracedState('capital', 'entry_cap_rate') ?? 'calculated',
                value: pctv(entryCap, 2),
              },
              {
                id: 'purchase', label: 'Purchase Price', kind: 'input', bold: true,
                state: overridden('purchase_price') ? 'assumption' : (tracedState('capital', 'purchase_price') ?? 'calculated'),
                overridden: overridden('purchase_price'),
                value: (
                  <AssumptionField value={purchase} editable={liveMode} format={fmtCurrency}
                    toDraft={(v) => String(Math.round(v))}
                    parse={(s) => { const n = parseFloat(s.replace(/[,$\s]/g, '')); return Number.isFinite(n) && n > 0 ? n : null; }}
                    onSave={(v) => onSaveAssumption('purchase_price', v)} width="w-36"
                    color={valueColor('input', true, overridden('purchase_price'))} bold />
                ),
              },
              { id: 'ppk', label: 'Purchase Price / Key', kind: 'calc', state: 'calculated', value: money(pricePerKey) },
              {
                id: 'closingPct', label: 'Closing Costs %', kind: 'input',
                state: overridden('closing_costs_pct') ? 'assumption' : 'assumption',
                overridden: overridden('closing_costs_pct'),
                value: (
                  <AssumptionField value={closingPct} editable={liveMode} format={(v) => fmtPct(v, 2)}
                    toDraft={(v) => (v * 100).toFixed(2)} suffix="%"
                    parse={(s) => { const n = parseFloat(s); return Number.isFinite(n) && n >= 0 ? n / 100 : null; }}
                    onSave={(v) => onSaveAssumption('closing_costs_pct', v)} width="w-20"
                    color={valueColor('input', false, overridden('closing_costs_pct'))} />
                ),
              },
              {
                id: 'closing', label: 'Acquisition / Closing Costs', kind: 'calc',
                state: tracedState('capital', 'closing_costs') ?? 'calculated', value: money(closing),
              },
            ];

            // ─── Exit / Reversion section ─────────────────────────────
            const exit: RowDef[] = [
              {
                id: 'holdYears', label: 'Hold Period', kind: 'input',
                state: overridden('hold_years') ? 'assumption' : 'assumption',
                overridden: overridden('hold_years'),
                value: (
                  <AssumptionField value={holdYears} editable={liveMode} format={(v) => `${v} years`}
                    toDraft={(v) => String(v)} suffix="yrs"
                    parse={(s) => { const n = parseInt(s, 10); return Number.isFinite(n) && n > 0 && n <= 20 ? n : null; }}
                    onSave={(v) => onSaveAssumption('hold_years', v)} width="w-16"
                    color={valueColor('input', false, overridden('hold_years'))} />
                ),
              },
              { id: 'exitDate', label: 'Exit Date', kind: 'calc', state: 'calculated', value: timeline?.exit_date ? fmtISODate(timeline.exit_date) : '—' },
              {
                id: 'fwdNoi', label: 'Forward 12-Month NOI', kind: 'linked', state: 'linked',
                value: money(terminalNoi), link: { label: '→ Financials', tab: 'pl' },
              },
              {
                id: 'exitCap', label: 'Exit Cap Rate', kind: 'input',
                state: overridden('exit_cap_rate') ? 'assumption' : 'assumption',
                overridden: overridden('exit_cap_rate'),
                value: (
                  <AssumptionField value={exitCap} editable={liveMode} format={(v) => fmtPct(v, 2)}
                    toDraft={(v) => (v * 100).toFixed(2)} suffix="%"
                    parse={(s) => { const n = parseFloat(s); return Number.isFinite(n) && n > 0 ? n / 100 : null; }}
                    onSave={(v) => onSaveAssumption('exit_cap_rate', v)} width="w-20"
                    color={valueColor('input', false, overridden('exit_cap_rate'))} />
                ),
              },
              {
                id: 'grossExit', label: 'Gross Exit Value', kind: 'calc', bold: true,
                state: tracedState('returns', 'gross_sale_price') ?? 'calculated', value: money(grossExit),
              },
              { id: 'exitPerKey', label: 'Exit Value / Key', kind: 'calc', state: 'calculated', value: money(exitPerKey) },
              {
                id: 'sellingCosts', label: 'Disposition Costs', kind: 'calc',
                state: tracedState('returns', 'selling_costs') ?? 'calculated', value: money(sellingCosts),
              },
              { id: 'transferTax', label: 'Transfer Tax', kind: 'awaiting', state: 'awaiting_data', value: '—' },
              {
                id: 'netExit', label: 'Net Exit Proceeds', kind: 'calc', bold: true,
                state: 'calculated', value: money(netExit),
              },
            ];

            // ─── Initial Renovation / PIP section ─────────────────────
            const renovation: RowDef[] = [
              { id: 'renoBudget', label: 'Renovation Budget', kind: 'input', bold: false,
                state: overridden('renovation_budget') ? 'assumption' : 'assumption',
                overridden: overridden('renovation_budget'),
                value: (
                  <AssumptionField value={renoBudget} editable={liveMode} format={fmtCurrency}
                    toDraft={(v) => String(Math.round(v))}
                    parse={(s) => { const n = parseFloat(s.replace(/[,$\s]/g, '')); return Number.isFinite(n) && n >= 0 ? n : null; }}
                    onSave={(v) => onSaveAssumption('renovation_budget', v)} width="w-36"
                    color={valueColor('input', false, overridden('renovation_budget'))} />
                ),
              },
              { id: 'renoPerKey', label: 'Budget / Key', kind: 'calc', state: 'calculated', value: (has(renoBudget) && has(keys)) ? money(renoBudget / keys) : '—' },
              { id: 'renoHard', label: 'Hard Costs', kind: 'calc', state: 'calculated', value: money(renoHard), note: '75% of base budget per the PIP scope of work' },
              { id: 'renoSoft', label: 'Soft Costs', kind: 'calc', state: 'calculated', value: money(renoSoft) },
              { id: 'renoProf', label: 'Professional Fees', kind: 'calc', state: 'calculated', value: money(renoProf) },
              { id: 'renoCont', label: 'Contingency', kind: 'awaiting', state: 'awaiting_data', value: '—' },
              { id: 'renoTotal', label: 'Total Renovation / PIP', kind: 'calc', bold: true, state: 'calculated', value: money(renoBudget) },
              { id: 'renoSf', label: '$ / SF', kind: 'awaiting', state: 'awaiting_data', value: '—' },
            ];

            // ─── Ongoing Capex section (hold-period, funded from operations) ──
            const ongoing: RowDef[] = [
              { id: 'ffee', label: 'FF&E Reserve', kind: 'linked', state: 'linked', value: '—', link: { label: '→ Financials', tab: 'pl' } },
              { id: 'roi', label: 'ROI Projects', kind: 'awaiting', state: 'awaiting_data', value: '—', note: 'Funded from operations — never appears in Sources & Uses' },
              { id: 'otherCapex', label: 'Other Recurring Capex', kind: 'awaiting', state: 'awaiting_data', value: '—', note: 'Awaiting the property condition assessment' },
            ];

            const sections: {
              title: string; note: string; rows: RowDef[]; formula?: string;
            }[] = [
              {
                title: 'Acquisition', note: 'Investment owns these assumptions', rows: acquisition,
                formula: (has(entryNoi) && has(entryCap) && has(purchase))
                  ? `Entry NOI ${fmtCurrency(entryNoi)} ÷ Entry Cap ${fmtPct(entryCap, 2)} → Purchase Price ${fmtCurrency(purchase)}`
                  : undefined,
              },
              {
                title: 'Exit / Reversion', note: 'One place for every exit assumption', rows: exit,
                formula: (has(terminalNoi) && has(exitCap) && has(grossExit) && has(netExit))
                  ? `Forward NOI ${fmtCurrency(terminalNoi)} ÷ Exit Cap ${fmtPct(exitCap, 2)} → Gross Exit ${fmtCurrency(grossExit)} − costs → Net ${fmtCurrency(netExit)}`
                  : undefined,
              },
              { title: 'Initial Renovation / PIP', note: 'Day-one capital · sits in total uses', rows: renovation },
              {
                title: 'Ongoing Capex', note: 'Hold-period capital · funded from operations', rows: ongoing,
                formula: 'Ongoing capex never enters Sources & Uses — only the day-one renovation above does.',
              },
            ];

            return (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                {/* KPI tiles */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: 12 }}>
                  {kpis.map((k) => (
                    <KpiTile key={k.label} label={k.label} value={k.value} sub={k.sub || undefined} />
                  ))}
                </div>

                {/* Section cards */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(430px,1fr))', gap: 14 }}>
                  {sections.map((s) => (
                    <SectionCard key={s.title} title={s.title} note={s.note}>
                      {s.rows.map((r) => (
                        <SectionRow key={r.id} row={r} />
                      ))}
                      {s.formula && (
                        <div style={{
                          marginTop: 10, background: palette.surfaceTint, border: `1px solid ${palette.border}`,
                          borderRadius: 7, padding: '8px 11px', fontSize: 11.5, color: palette.hoverInk, lineHeight: 1.5,
                        }}>
                          {s.formula}
                        </div>
                      )}
                    </SectionCard>
                  ))}
                </div>

                {/* Wave 2 P2.5 — interactive three-bucket capex plan (extends Ongoing Capex). */}
                <CapexPlanPanel
                  keys={keys}
                  revenueByYear={(() => {
                    const wRevYears = getEngineField<RevenueYearLite[]>(outputs, 'revenue', 'years');
                    return (wRevYears ?? []).map((y) => y.total_revenue);
                  })()}
                  holdYears={(holdYears as number | undefined) ?? 5}
                  state={capexPlan}
                  onChange={setCapexPlan}
                />

                {/* Wave 2 P2.6 — historical baseline (silent when no historical docs). */}
                <HistoricalBaselinePanel
                  baseline={historicalBaseline}
                  dealId={dealId}
                  forecastY1={(() => {
                    const rev = getEngineField<RevenueYearLite[]>(outputs, 'revenue', 'years');
                    const exp = getEngineField<ExpenseYearLite[]>(outputs, 'expense', 'years');
                    return { total_revenue: rev?.[0]?.total_revenue ?? null, noi: exp?.[0]?.noi ?? null };
                  })()}
                />

                {/* Debt configuration lives on the Debt tab; here debt is a read-only source. */}
                <div className="rounded-md border border-border bg-ink-50/40 px-4 py-3 flex items-center justify-between gap-3">
                  <div className="text-[12.5px] text-ink-600 leading-relaxed">
                    <span className="font-medium text-ink-900">Debt configuration lives on the Debt tab.</span>{' '}
                    Loan terms, tranches, and the schedule are managed there — debt shows here only as a source in Sources &amp; Uses.
                  </div>
                  <a href="?tab=debt" className="shrink-0 text-[12px] font-medium px-3 py-1.5 rounded-md border border-border text-brand-700 hover:bg-brand-50 whitespace-nowrap">
                    Open Debt tab →
                  </a>
                </div>
              </div>
            );
          })()}

          {tab === 'Sources & Uses' && (
            <SourcesUses outputs={outputs} money={money} keys={keys} />
          )}

          {tab === 'Timeline' && (
            <TimelinePanel timeline={timeline} liveMode={liveMode} holdYears={holdYears} />
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
// Section row — canonical Deal Summary row (dot · label · link · value).
// ─────────────────────────────────────────────────────────────────────
function SectionRow({ row }: { row: RowDef }) {
  const color = valueColor(row.kind, !!row.bold, !!row.overridden);
  const valueIsNode = typeof row.value !== 'string' && typeof row.value !== 'number';
  return (
    <>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
        fontSize: 13, padding: '7px 0', borderBottom: `1px solid ${palette.hairlineRow}`,
      }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
          <ProvenanceDot state={row.state} size={8} />
          <span style={{ color: palette.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {row.label}
          </span>
          {row.link && (
            <a href={`?tab=${row.link.tab}`}
              style={{ fontSize: 10.5, color: palette.linkBlue, cursor: 'pointer', fontWeight: 600, whiteSpace: 'nowrap', textDecoration: 'none' }}>
              {row.link.label}
            </a>
          )}
        </span>
        {valueIsNode ? (
          <span style={{ flexShrink: 0 }}>{row.value}</span>
        ) : (
          <span style={{
            color, fontWeight: row.bold ? 700 : 400,
            fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap', flexShrink: 0,
          }}>
            {row.value}
          </span>
        )}
      </div>
      {row.note && (
        <div style={{ fontSize: 10.5, color: palette.textMuted, padding: '0 0 6px 15px', lineHeight: 1.45 }}>
          {row.note}
        </div>
      )}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Sources & Uses — balance banner + Uses / Sources tables (Amount / Key / %).
// Worker capital engine sources/uses arrays are the authoritative source.
// ─────────────────────────────────────────────────────────────────────
interface CapitalLine { label: string; amount: number; pct?: number | null; is_total?: boolean }

interface SURow {
  label: string; amount: number | null; total: boolean; kind: ValueKind;
  state: ValueState; note?: string; link?: { label: string; tab: string };
}

function classifySU(label: string): { kind: ValueKind; note?: string; link?: { label: string; tab: string } } {
  if (/senior loan|senior debt/i.test(label)) {
    return { kind: 'linked', link: { label: '→ Debt', tab: 'debt' }, note: 'Sized in Debt — Investment consumes the result' };
  }
  if (/lender fee|loan fee|loan cost/i.test(label)) return { kind: 'linked', link: { label: '→ Debt', tab: 'debt' } };
  if (/key money/i.test(label)) return { kind: 'linked', link: { label: '→ Partnership', tab: 'partnership' } };
  if (/equity/i.test(label)) return { kind: 'calc', note: 'Allocated between sponsor and LP in Partnership' };
  return { kind: 'calc' };
}

function SourcesUses({
  outputs, money, keys,
}: {
  outputs: import('@/lib/api').EngineOutputsResponse | null;
  money: (v: number | undefined) => ReactNode;
  keys: number | undefined;
}) {
  const wSources = getEngineField<CapitalLine[]>(outputs, 'capital', 'sources') ?? [];
  const wUses = getEngineField<CapitalLine[]>(outputs, 'capital', 'uses') ?? [];

  const toRows = (lines: CapitalLine[], side: 'uses' | 'sources'): SURow[] =>
    lines.map((l) => {
      const total = !!l.is_total;
      const c = classifySU(l.label);
      return {
        label: l.label,
        amount: typeof l.amount === 'number' ? l.amount : null,
        total,
        kind: total ? 'calc' : c.kind,
        state: total ? 'calculated' : kindToState(c.kind),
        note: total ? undefined : c.note,
        link: total ? undefined : c.link,
      };
    });

  const usesRows = toRows(wUses, 'uses');
  const sourcesRows = toRows(wSources, 'sources');

  const usesTotal = usesRows.find((r) => r.total)?.amount ?? usesRows.reduce((s, r) => s + (r.amount ?? 0), 0);
  const sourcesTotal = sourcesRows.find((r) => r.total)?.amount ?? sourcesRows.reduce((s, r) => s + (r.amount ?? 0), 0);
  const balanced = Math.abs(usesTotal - sourcesTotal) < 1;
  const delta = Math.abs(usesTotal - sourcesTotal);

  const columns: { title: string; rows: SURow[]; footnote: string }[] = [
    { title: 'Uses', rows: usesRows, footnote: 'Renovation appears once, as the total PIP budget including contingency.' },
    { title: 'Sources', rows: sourcesRows, footnote: 'Required equity is the plug — total uses less debt and key money. Partnership decides how it splits.' },
  ];

  const suGrid = 'minmax(150px,1fr) minmax(96px,116px) minmax(66px,86px) minmax(44px,54px)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Balance banner */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap',
        background: balanced ? 'oklch(96.5% 0.03 155)' : 'oklch(96% 0.04 40)',
        border: `1px solid ${balanced ? 'oklch(88% 0.05 155)' : 'oklch(85% 0.07 40)'}`,
        borderRadius: 8, padding: '10px 14px',
      }}>
        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '.05em', textTransform: 'uppercase',
          color: balanced ? 'oklch(40% 0.12 155)' : 'oklch(45% 0.15 40)',
        }}>
          {balanced ? 'In balance' : 'Out of balance'}
        </span>
        <span style={{ fontSize: 12.5, color: palette.ink }}>
          Total sources <b>{money(sourcesTotal)}</b> · total uses <b>{money(usesTotal)}</b>
          {balanced ? ' — sources equal uses' : ` — difference ${fmtCurrency(delta)}`}
        </span>
        <span style={{ fontSize: 11, color: palette.textMuted, marginLeft: 'auto' }}>
          Required equity is the plug — every other line is owned by Investment, Debt or Partnership
        </span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(420px,1fr))', gap: 14 }}>
        {columns.map((col) => (
          <SectionCard key={col.title}>
            {/* Column header */}
            <div style={{
              display: 'grid', gridTemplateColumns: suGrid, fontSize: 10, fontWeight: 700,
              letterSpacing: '.05em', color: palette.textFaint, textTransform: 'uppercase',
              paddingBottom: 7, borderBottom: `1px solid ${palette.border}`,
            }}>
              <span>{col.title}</span>
              <span style={{ textAlign: 'right' }}>Amount</span>
              <span style={{ textAlign: 'right' }}>/ Key</span>
              <span style={{ textAlign: 'right' }}>%</span>
            </div>
            {col.rows.map((r, i) => {
              const perKey = (r.amount != null && keys && keys > 0 && !r.total) ? fmtCurrency(r.amount / keys) : (r.total ? '' : '—');
              const pct = r.amount != null && usesTotal ? `${((r.amount / (col.title === 'Uses' ? usesTotal : sourcesTotal)) * 100).toFixed(1)}%` : '—';
              const color = valueColor(r.total ? 'calc' : r.kind, r.total, false);
              return (
                <div key={`${r.label}-${i}`}>
                  <div style={{
                    display: 'grid', gridTemplateColumns: suGrid, fontSize: 12.5, padding: '6px 0',
                    borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center',
                  }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                      <ProvenanceDot state={r.state} size={8} />
                      <span style={{ color: palette.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {r.label}
                      </span>
                      {r.link && (
                        <a href={`?tab=${r.link.tab}`}
                          style={{ fontSize: 10.5, color: palette.linkBlue, cursor: 'pointer', fontWeight: 600, whiteSpace: 'nowrap', textDecoration: 'none' }}>
                          {r.link.label}
                        </a>
                      )}
                    </span>
                    <span style={{ textAlign: 'right', color, fontWeight: r.total ? 700 : 400, fontVariantNumeric: 'tabular-nums' }}>
                      {r.amount != null ? fmtCurrency(r.amount) : '—'}
                    </span>
                    <span style={{ textAlign: 'right', color: palette.textMuted, fontVariantNumeric: 'tabular-nums' }}>{perKey}</span>
                    <span style={{ textAlign: 'right', color: palette.textMuted, fontVariantNumeric: 'tabular-nums' }}>{r.total ? '100.0%' : pct}</span>
                  </div>
                  {r.note && (
                    <div style={{ fontSize: 10.5, color: palette.textMuted, padding: '0 0 6px 15px', lineHeight: 1.45 }}>
                      {r.note}
                    </div>
                  )}
                </div>
              );
            })}
            {col.rows.length === 0 && (
              <div style={{ fontSize: 12.5, color: palette.textMuted, padding: '10px 0' }}>
                Run the model to populate {col.title.toLowerCase()}.
              </div>
            )}
            <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>{col.footnote}</div>
          </SectionCard>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Timeline — dated rail + phase bars + hold caption + detail table.
// Data from GET /deals/{id}/engines/timeline (FON-71).
// ─────────────────────────────────────────────────────────────────────
function TimelinePanel({
  timeline, liveMode, holdYears,
}: {
  timeline: TimelineResponse | null;
  liveMode: boolean;
  holdYears: number | undefined;
}) {
  const events = timeline?.events ?? [];
  const pending = !timeline?.close_date;

  const closeMs = timeline?.close_date ? Date.parse(timeline.close_date) : null;
  const exitMs = timeline?.exit_date ? Date.parse(timeline.exit_date) : null;
  const span = (closeMs != null && exitMs != null && exitMs > closeMs) ? exitMs - closeMs : null;
  const pos = (iso: string | null | undefined): number | null => {
    if (span == null || closeMs == null || !iso) return null;
    const t = Date.parse(iso);
    if (Number.isNaN(t)) return null;
    return Math.max(0, Math.min(100, ((t - closeMs) / span) * 100));
  };

  const holdCaption = (!pending && timeline?.close_date && timeline?.exit_date)
    ? `${holdYears != null ? `${holdYears}-year hold` : 'Hold'} · ${fmtLongDate(timeline.close_date)} → ${fmtLongDate(timeline.exit_date)}`
    : 'Set the Acquisition Date on Deal Summary to populate dates';

  const ownerFor = (basis: string): string => {
    if (basis === 'assumption') return 'Investment assumption';
    if (basis === 'pending') return 'Awaiting acquisition date';
    return 'Calculated';
  };
  const stateForBasis = (basis: string): ValueState =>
    basis === 'assumption' ? 'assumption' : basis === 'pending' ? 'awaiting_data' : 'calculated';

  const phaseColor = (label: string): string =>
    /renov/i.test(label) ? 'oklch(55% 0.12 260)'
      : /ramp/i.test(label) ? 'oklch(65% 0.09 200)'
        : /stabil/i.test(label) ? 'oklch(55% 0.11 155)'
          : 'oklch(55% 0.11 155)';

  // Phase bars: any event carrying a positive duration that we can place on the
  // acquisition→exit span, plus a full-width "Total hold" bar.
  const phases = events
    .filter((e) => (e.duration_months ?? 0) > 0 && e.start && e.finish)
    .map((e) => {
      const left = pos(e.start);
      const right = pos(e.finish);
      return {
        label: e.event,
        left: left != null ? `${left}%` : '0%',
        width: (left != null && right != null) ? `${Math.max(2, right - left)}%` : '2%',
        bg: phaseColor(e.event),
        duration: `${e.duration_months} months`,
      };
    });
  if (span != null) {
    phases.push({ label: 'Total hold', left: '0%', width: '100%', bg: '#c9c8c2', duration: holdYears != null ? `${holdYears} years` : '' });
  }

  const railCols = `repeat(${Math.max(1, events.length)},minmax(0,1fr))`;
  const detailGrid = 'minmax(180px,1.3fr) minmax(120px,.9fr) minmax(110px,.8fr) minmax(220px,1.4fr)';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* Rail card */}
      <SectionCard title="Transaction Timeline" note={holdCaption} bodyStyle={{ padding: '18px 20px' }}>
        {events.length === 0 ? (
          <p style={{ fontSize: 12.5, color: palette.textMuted, paddingTop: 10 }}>
            {liveMode ? 'Run the model to build the timeline.' : 'Timeline is available on live deals.'}
          </p>
        ) : (
          <div style={{ marginTop: 14 }}>
            {/* Milestone rail */}
            <div style={{ display: 'grid', gridTemplateColumns: railCols, position: 'relative' }}>
              <div style={{ position: 'absolute', left: '6%', right: '6%', top: 5, height: 2, background: palette.border }} />
              {events.map((m, i) => (
                <div key={`${m.event}-${i}`} style={{
                  position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center',
                  gap: 6, padding: '0 4px', textAlign: 'center',
                }}>
                  <ProvenanceDot state={stateForBasis(m.basis)} size={12}
                    style={{ boxShadow: '0 0 0 2px #fff, 0 0 0 4px #eae9e4', zIndex: 1 }} />
                  <span style={{ fontSize: 12.5, fontWeight: 700, color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>
                    {fmtISODate(m.start ?? m.finish)}
                  </span>
                  <span style={{ fontSize: 11.5, color: palette.textSecondary, lineHeight: 1.35 }}>{m.event}</span>
                  <span style={{ fontSize: 10.5, color: palette.textFaint }}>
                    {(m.duration_months ?? 0) > 0 ? `${m.duration_months} months` : ''}
                  </span>
                </div>
              ))}
            </div>
            {/* Phase bars */}
            {phases.length > 0 && (
              <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 7 }}>
                {phases.map((p, i) => (
                  <div key={`${p.label}-${i}`} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span style={{ width: 132, fontSize: 11.5, color: palette.textSecondary, flexShrink: 0 }}>{p.label}</span>
                    <span style={{ flex: 1, height: 9, background: '#f3f2ee', borderRadius: 5, position: 'relative', overflow: 'hidden' }}>
                      <span style={{ position: 'absolute', left: p.left, width: p.width, top: 0, bottom: 0, background: p.bg, borderRadius: 5 }} />
                    </span>
                    <span style={{ width: 96, textAlign: 'right', fontSize: 11.5, color: palette.hoverInk, fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
                      {p.duration}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </SectionCard>

      {/* Detail table */}
      {events.length > 0 && (
        <SectionCard>
          <div style={{
            display: 'grid', gridTemplateColumns: detailGrid, fontSize: 10, fontWeight: 700,
            letterSpacing: '.05em', color: palette.textFaint, textTransform: 'uppercase',
            paddingBottom: 7, borderBottom: `1px solid ${palette.border}`,
          }}>
            <span>Milestone</span><span>Date</span><span>Duration</span><span>Owned by</span>
          </div>
          {events.map((r, i) => {
            const isDebt = /loan|debt|refi/i.test(r.event);
            return (
              <div key={`${r.event}-${i}`} style={{
                display: 'grid', gridTemplateColumns: detailGrid, fontSize: 12.5, padding: '7px 0',
                borderBottom: `1px solid ${palette.hairlineRow}`, alignItems: 'center',
              }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                  <ProvenanceDot state={isDebt ? 'linked' : stateForBasis(r.basis)} size={8} />
                  <span style={{ color: palette.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.event}</span>
                </span>
                <span style={{ color: palette.ink, fontVariantNumeric: 'tabular-nums' }}>{fmtISODate(r.start ?? r.finish)}</span>
                <span style={{ color: palette.textSecondary, fontVariantNumeric: 'tabular-nums' }}>
                  {(r.duration_months ?? 0) > 0 ? `${r.duration_months} months` : '—'}
                </span>
                <span style={{ color: palette.textMuted, display: 'flex', alignItems: 'center', gap: 8 }}>
                  {isDebt ? 'Linked from Debt' : ownerFor(r.basis)}
                  {isDebt && (
                    <a href="?tab=debt" style={{ color: palette.linkBlue, cursor: 'pointer', fontWeight: 600, textDecoration: 'none' }}>Open Debt →</a>
                  )}
                </span>
              </div>
            );
          })}
        </SectionCard>
      )}
      {pending && events.length > 0 && (
        <p style={{ fontSize: 11, color: palette.textMuted }}>
          Set the Acquisition Date on Deal Summary to populate dates.
        </p>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Inline editors + date/format helpers.
// ─────────────────────────────────────────────────────────────────────

/**
 * An editable transaction-assumption cell. Shows the value with the canonical
 * blue dotted-underline (editable) affordance; on click it becomes an input +
 * navy Save button. On save it hands the parsed number back to onSave (which
 * PATCHes field_overrides + re-runs). Read-only when not live.
 */
function AssumptionField({
  value, format, toDraft, parse, onSave, editable, suffix, width = 'w-32', color, bold,
}: {
  value: number | undefined;
  format: (v: number) => string;
  toDraft: (v: number) => string;
  parse: (s: string) => number | null;
  onSave: (v: number) => void | Promise<void>;
  editable: boolean;
  suffix?: string;
  width?: string;
  color?: string;
  bold?: boolean;
}) {
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const display = value != null ? format(value) : '—';
  const textColor = color ?? palette.ink;

  if (!editable) {
    return <span style={{ color: textColor, fontWeight: bold ? 700 : 400, fontVariantNumeric: 'tabular-nums' }}>{display}</span>;
  }
  if (!editing) {
    return (
      <span style={{
        color: textColor, fontWeight: bold ? 700 : 500, fontVariantNumeric: 'tabular-nums',
        textDecoration: 'underline dotted', cursor: 'pointer',
      }}
        onClick={() => { setDraft(value != null ? toDraft(value) : ''); setEditing(true); }}
        title="Click to change — Investment owns this assumption">
        {display}
      </span>
    );
  }
  const submit = async () => {
    const parsed = parse(draft);
    if (parsed == null) { toast('Enter a valid number.', { type: 'error' }); return; }
    setSaving(true);
    try { await onSave(parsed); setEditing(false); } finally { setSaving(false); }
  };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <input
        type="number" value={draft} autoFocus disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') void submit(); if (e.key === 'Escape') setEditing(false); }}
        className={cn(width)}
        style={{
          fontSize: 12.5, fontFamily: 'inherit', border: `1px solid ${palette.linkBlue}`,
          borderRadius: 5, padding: '4px 7px', fontVariantNumeric: 'tabular-nums', textAlign: 'right',
        }}
      />
      {suffix && <span style={{ fontSize: 11, color: palette.textMuted }}>{suffix}</span>}
      <button type="button" aria-label="Save" onClick={() => void submit()} disabled={saving}
        style={{
          background: palette.inkNavy, color: '#fff', border: 'none', borderRadius: 5,
          padding: '5px 9px', fontSize: 11, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit',
        }}>
        Save
      </button>
      <button type="button" aria-label="Cancel" onClick={() => setEditing(false)}
        style={{ background: 'none', border: 'none', color: palette.textFaint, cursor: 'pointer' }}>
        <X className="w-3.5 h-3.5" />
      </button>
    </span>
  );
}

/** Format an ISO date (YYYY-MM-DD) as M/D/YYYY without a timezone shift. */
function fmtISODate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return '—';
  return `${Number(m[2])}/${Number(m[3])}/${m[1]}`;
}

/** Format an ISO date as "Mon D, YYYY" (the canonical caption format). */
function fmtLongDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return '—';
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${months[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`;
}

/** The acquisition close date — an editable date cell that drives the Timeline. */
function CloseDateField({
  iso, editable, onSave,
}: {
  iso: string | null;
  editable: boolean;
  onSave: (iso: string) => void | Promise<void>;
}) {
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const display = fmtISODate(iso);

  if (!editable) {
    return <span style={{ color: prov.blue, fontVariantNumeric: 'tabular-nums' }}>{display}</span>;
  }
  if (!editing) {
    return (
      <span
        style={{ color: prov.blue, fontWeight: 500, fontVariantNumeric: 'tabular-nums', textDecoration: 'underline dotted', cursor: 'pointer' }}
        onClick={() => { setDraft(iso ? iso.slice(0, 10) : ''); setEditing(true); }}
        title="Click to change — Investment owns this assumption">
        {iso ? display : <span style={{ color: palette.textFaint, fontWeight: 400 }}>Set date</span>}
      </span>
    );
  }
  const submit = async () => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(draft)) { toast('Pick a valid date.', { type: 'error' }); return; }
    setSaving(true);
    try { await onSave(draft); setEditing(false); } finally { setSaving(false); }
  };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <input
        type="date" value={draft} autoFocus disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') void submit(); if (e.key === 'Escape') setEditing(false); }}
        style={{ fontSize: 12.5, fontFamily: 'inherit', border: `1px solid ${palette.linkBlue}`, borderRadius: 5, padding: '4px 7px' }}
      />
      <button type="button" aria-label="Save" onClick={() => void submit()} disabled={saving}
        style={{ background: palette.inkNavy, color: '#fff', border: 'none', borderRadius: 5, padding: '5px 9px', fontSize: 11, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
        Save
      </button>
      <button type="button" aria-label="Cancel" onClick={() => setEditing(false)}
        style={{ background: 'none', border: 'none', color: palette.textFaint, cursor: 'pointer' }}>
        <X className="w-3.5 h-3.5" />
      </button>
    </span>
  );
}

/**
 * Inline keys override — the analyst escape hatch when the OM key count was
 * wrong. Read-only when there is no worker connection (mock data flow).
 */
function KeysOverride({
  dealId, currentKeys, onSaved,
}: {
  dealId: string;
  currentKeys: number | null | undefined;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  const editable = isWorkerConnected() && !/^\d+$/.test(dealId) && dealId.length > 0;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>(currentKeys != null ? String(currentKeys) : '');
  const [saving, setSaving] = useState(false);
  const display = currentKeys != null ? String(currentKeys) : '—';

  if (!editable) {
    return <span style={{ color: prov.green, fontVariantNumeric: 'tabular-nums' }}>{display}</span>;
  }
  if (!editing) {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span style={{ color: prov.green, fontVariantNumeric: 'tabular-nums' }}>{display}</span>
        <button type="button" aria-label="Override room count" title="Override room count"
          onClick={() => { setDraft(currentKeys != null ? String(currentKeys) : ''); setEditing(true); }}
          style={{ background: 'none', border: 'none', color: palette.textFaint, cursor: 'pointer' }}>
          <Pencil className="w-3 h-3" />
        </button>
      </span>
    );
  }
  const submit = async () => {
    const parsed = Number.parseInt(draft, 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
      toast('Enter a positive whole number for room count.', { type: 'error' });
      return;
    }
    setSaving(true);
    try {
      await api.deals.update(dealId, { keys: parsed });
      toast(`Room count saved: ${parsed} keys. Re-run engines to recompute.`, { type: 'success' });
      setEditing(false);
      onSaved();
    } catch (err) {
      const detail = err instanceof WorkerError ? err.body : String(err);
      toast(`Failed to save room count: ${detail || 'worker rejected update'}`, { type: 'error' });
    } finally {
      setSaving(false);
    }
  };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <input
        type="number" min={1} value={draft} autoFocus disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') void submit(); if (e.key === 'Escape') setEditing(false); }}
        style={{ width: 80, fontSize: 12.5, fontFamily: 'inherit', border: `1px solid ${palette.linkBlue}`, borderRadius: 5, padding: '4px 7px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}
      />
      <button type="button" aria-label="Save room count override" onClick={() => void submit()} disabled={saving}
        style={{ background: palette.inkNavy, color: '#fff', border: 'none', borderRadius: 5, padding: '5px 9px', fontSize: 11, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}>
        <Check className="w-3 h-3" />
      </button>
      <button type="button" aria-label="Cancel" onClick={() => setEditing(false)} disabled={saving}
        style={{ background: 'none', border: 'none', color: palette.textFaint, cursor: 'pointer' }}>
        <X className="w-3.5 h-3.5" />
      </button>
    </span>
  );
}
