'use client';
import { useState, useCallback, useRef, type ReactNode } from 'react';
import { useParams } from 'next/navigation';
import { ChevronDown, ChevronUp, Briefcase, Pencil, Check, X } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import CapexPlanPanel, { DEFAULT_CAPEX_PLAN, type CapexPlanState } from './CapexPlanPanel';
import HistoricalBaselinePanel from './HistoricalBaselinePanel';
import { useHistoricalBaseline } from '@/lib/hooks/useHistoricalBaseline';
import { fmtCurrency, fmtPct, cn } from '@/lib/format';
import { useAssumptionsOptional } from '@/stores/assumptionsStore';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { IntroCard } from '@/components/help/IntroCard';
import { Sourced } from '@/components/help/Sourced';
import { api, isWorkerConnected, WorkerError } from '@/lib/api';

// Expense engine year shape — mirrors apps/worker/app/engines/expense.py.
// Only the fields we read on the Investment tab are typed here; the
// canonical shape lives in PLTab.tsx.
interface ExpenseYearLite { year: number; noi: number }

// Revenue engine year shape - only the fields the capex panel reads.
interface RevenueYearLite { year: number; total_revenue: number }

const subTabs = ['Deal Summary', 'Sources & Uses', 'Timeline'];

export default function InvestmentTab() {
  const [tab, setTab] = useState('Deal Summary');
  const ctx = useAssumptionsOptional();
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { toast } = useToast();
  const { outputs, previous } = useEngineOutputs(dealId);
  const { deal, refresh: refreshDeal } = useDeal(dealId);
  // Wave 2 P2.6 — historical baseline (Sam's multi-year walk ask).
  const { baseline: historicalBaseline } = useHistoricalBaseline(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  // ─── FON-44: editable transaction assumptions ─────────────────────
  // Sam's spec — the Deal Summary is the transaction-assumptions layer: the
  // analyst edits the core inputs (purchase price, renovation, hold, exit cap)
  // and the derived values (entry/exit NOI + cap, price/key, exit date) stay
  // linked. Edits PATCH field_overrides and kick a debounced run-all so the
  // downstream Debt / Cash Flow / Returns engines re-derive.
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
  // Wave 2 P2.5 - local capex plan state. The worker's ``capex_plan``
  // engine output isn't wired into useEngineOutputs yet; until it is,
  // the panel owns the source of truth in memory.
  const [capexPlan, setCapexPlan] = useState<CapexPlanState>(DEFAULT_CAPEX_PLAN);

  // ─── Worker engine field reads ────────────────────────────────────
  // Every panel reads worker engine output and shows '—' until the engine
  // has produced the value — no fixtures.
  const wPurchase = getEngineField<number>(outputs, 'capital', 'purchase_price');
  const wPricePerKey = getEngineField<number>(outputs, 'capital', 'price_per_key');
  const wEntryCap = getEngineField<number>(outputs, 'capital', 'entry_cap_rate');
  const wTotalCapital =
    getEngineField<number>(outputs, 'capital', 'total_capital_usd') ??
    getEngineField<number>(outputs, 'capital', 'total_capital');
  const wTotalCapitalPerKey = getEngineField<number>(
    outputs, 'capital', 'total_capital_per_key',
  );
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
  // Capital uses — the "Renovation" / "Contingency" line items live here.
  const wCapitalUses = getEngineField<Array<{ label?: string; amount?: number }>>(
    outputs, 'capital', 'uses',
  );
  const findUse = (re: RegExp): number | undefined => {
    const hit = (wCapitalUses ?? []).find((u) => re.test(String(u?.label ?? '')));
    return typeof hit?.amount === 'number' ? hit.amount : undefined;
  };

  const hasCapitalOutput =
    wPurchase != null || wPricePerKey != null || wEntryCap != null;

  // Display helper: format a worker value, or '—' when absent.
  const fmtOrDash = (
    n: number | undefined,
    formatter: (v: number) => string,
  ): string => (n != null ? formatter(n) : '—');

  // Per-key keys count: prefer real deal keys, then the fixture only on
  // the demo. Used as the divider when worker engine output omits the
  // pre-computed per-key value.
  const propertyKeys =
    (deal?.keys && deal.keys > 0)
      ? deal.keys
      : undefined;

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
          <EngineLegend />
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

      {/* Sam v2: Asking Price and IRR should remain analyst-driven
          until the investment engine is fully wired up with exit /
          cap-rate assumptions. The Capital + Returns engines run
          today but the values flow through default seeds where deal
          data is missing — surface that explicitly so reviewers
          don't take the numbers as committed. */}
      <Card className="p-3 mb-3 border-l-4 border-l-warn-500 bg-warn-50/40">
        <p className="text-[12px] text-ink-700">
          <span className="font-semibold">Some fields aren&apos;t populated yet.</span>{' '}
          Core economics — purchase price, entry / exit cap, NOI and returns —
          are live from the Capital / Returns engines. A few details still show
          &ldquo;—&rdquo; because they aren&apos;t extracted from the deal docs
          (year built, labor / title, the renovation cost split) or need the
          timeline engine (acquisition / exit dates). Treat those as pending,
          not committed.
        </p>
      </Card>

      <WhatJustHappened
        engine="capital"
        engineLabel="Capital"
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
      <EngineLegend />

      <div className={cn(computing && 'relative pointer-events-none opacity-60')}>

      {tab === 'Deal Summary' && (() => {
        // ─── Worker field wiring for every panel below ──────────
        // Keys come from the deal row (useDeal); everything else is engine
        // output or an editable assumption. Descriptive property facts live on
        // Overview, not here (FON-44).
        const dealKeys = (deal?.keys && deal.keys > 0)
          ? deal.keys
          : undefined;
        const dealGba: number | undefined = undefined;

        // Entry Valuation
        const entryNoi = wYearOneNoi;
        const entryPricePerKey = wPricePerKey;
        // Purchase price: engine value, else derive from price/key × keys
        // (the capital engine emits price_per_key, not purchase_price).
        const entryPurchase = wPurchase
          ?? (entryPricePerKey != null && propertyKeys && propertyKeys > 0
            ? entryPricePerKey * propertyKeys
            : undefined);
        // Entry cap = Y1 NOI ÷ purchase price (the engine doesn't emit it).
        const entryCap = (entryNoi != null && entryPurchase != null && entryPurchase > 0)
          ? entryNoi / entryPurchase
          : wEntryCap;

        // Exit Valuation
        const exitTerminalNoi = wTerminalNoi;
        const exitCap = wExitCap;
        const exitGross = wGrossSale;
        const exitPerKey = (exitGross != null && propertyKeys && propertyKeys > 0)
          ? exitGross / propertyKeys : undefined;
        const exitSellingCosts = wSellingCosts;

        // Renovation Budget — capital engine doesn't break out
        // hard/soft/fees yet; sub-rows show '—' until it does.
        const renoBudget = findUse(/renovat/i);
        const renoPerKey = (renoBudget != null && propertyKeys && propertyKeys > 0)
          ? renoBudget / propertyKeys : undefined;
        const renoPerSf = (renoBudget != null && dealGba && dealGba > 0)
          ? renoBudget / dealGba : undefined;
        const renoContingency = findUse(/conting/i);
        const renoHard = undefined;
        const renoSoft = undefined;
        const renoFees = undefined;

        // Valuation Assumptions
        const totalCapital = wTotalCapital;
        const totalCapitalPerKey = wTotalCapitalPerKey != null
          ? wTotalCapitalPerKey
          : (totalCapital != null && propertyKeys && propertyKeys > 0)
            ? totalCapital / propertyKeys
            : undefined;
        const holdYears = wHoldYears;
        const exitMonth = holdYears != null ? holdYears * 12 : undefined;

        // Senior Loan Financing — prefer debt engine echo, fall
        // back to capital engine debt_amount. The simplified Lovable
        // layout only surfaces term here, but we keep loanAmount
        // derived for completeness / future rows.
        const loanAmount = wLoanAmount ?? wDebtAmount;
        void loanAmount;

        return (
        <>
          <div className="grid grid-cols-2 gap-5">
            {/* FON-44 — Investment is the transaction-assumptions layer, not a
                property directory. Descriptive property facts (name, location,
                year built, labor/title) live on Overview; Investment references
                only Keys (needed for per-key math). */}
            <Panel title="Acquisition" rows={[
              [
                'Keys',
                <KeysOverride
                  key="keys-override"
                  dealId={dealId}
                  currentKeys={dealKeys}
                  onSaved={() => refreshDeal()}
                />,
              ],
              ['Hotel Purchase Price', <AssumptionField key="pp" value={entryPurchase} editable={liveMode}
                format={fmtCurrency} toDraft={(v) => String(Math.round(v))}
                parse={(s) => { const n = parseFloat(s.replace(/[,$\s]/g, '')); return Number.isFinite(n) && n > 0 ? n : null; }}
                onSave={(v) => onSaveAssumption('purchase_price', v)} width="w-36" />],
              ['Purchase Price / Key', fmtOrDash(entryPricePerKey, fmtCurrency)],
              ['Entry NOI', fmtOrDash(entryNoi, fmtCurrency)],
              ['Entry Cap Rate', fmtOrDash(entryCap, v => fmtPct(v, 2))],
            ]} />
            <Panel title="Exit Valuation" rows={[
              ['Exit Month', exitMonth != null ? String(exitMonth) : '—'],
              ['Exit Date', '—'],
              ['Fwd. 12 Mo NOI', fmtOrDash(exitTerminalNoi, fmtCurrency)],
              ['Exit Cap Rate', <AssumptionField key="xc-edit" value={exitCap} editable={liveMode}
                format={(v) => fmtPct(v, 2)} toDraft={(v) => (v * 100).toFixed(2)}
                parse={(s) => { const n = parseFloat(s); return Number.isFinite(n) && n > 0 ? n / 100 : null; }}
                onSave={(v) => onSaveAssumption('exit_cap_rate', v)} suffix="%" width="w-20" />],
              ['Gross Exit Value', fmtOrDash(exitGross, fmtCurrency)],
              ['Per Key', fmtOrDash(exitPerKey, fmtCurrency)],
              ['Exit Sales Cost', fmtOrDash(exitSellingCosts, fmtCurrency)],
              ['Transfer Tax', '—'],
            ]} />
            <Panel title="Renovation Budget" rows={[
              ['Renovation Budget', <AssumptionField key="rb" value={renoBudget} editable={liveMode}
                format={fmtCurrency} toDraft={(v) => String(Math.round(v))}
                parse={(s) => { const n = parseFloat(s.replace(/[,$\s]/g, '')); return Number.isFinite(n) && n >= 0 ? n : null; }}
                onSave={(v) => onSaveAssumption('renovation_budget', v)} width="w-36" />],
              ['Per Key', fmtOrDash(renoPerKey, fmtCurrency)],
              ['Per SF', fmtOrDash(renoPerSf, fmtCurrency)],
              ['Hard Costs (75%)', fmtOrDash(renoHard, fmtCurrency)],
              ['Soft Costs (20%)', fmtOrDash(renoSoft, fmtCurrency)],
              ['Professional Fees (5%)', fmtOrDash(renoFees, fmtCurrency)],
              ['Contingency', fmtOrDash(renoContingency, fmtCurrency)],
              ['Total Renovation', fmtOrDash(renoBudget, fmtCurrency)],
            ]} />
          </div>
          <div className="grid grid-cols-1 gap-5 mt-5">
            <Panel title="Valuation Assumptions" rows={[
              ['Total Dev. Cost', fmtOrDash(totalCapital, fmtCurrency)],
              ['Per Key', fmtOrDash(totalCapitalPerKey, fmtCurrency)],
              ['Hold Years', <AssumptionField key="hy" value={holdYears} editable={liveMode}
                format={(v) => `${v} yrs`} toDraft={(v) => String(v)}
                parse={(s) => { const n = parseInt(s, 10); return Number.isFinite(n) && n > 0 && n <= 20 ? n : null; }}
                onSave={(v) => onSaveAssumption('hold_years', v)} suffix="yrs" width="w-16" />],
              ['Stabilized NOI FWD 12', fmtOrDash(exitTerminalNoi, fmtCurrency)],
              ['Exit Cap Rate', <Sourced key="xc" sourceKey="exit_cap_rate">{fmtOrDash(exitCap, v => fmtPct(v, 2))}</Sourced>],
              ['Sale Price', fmtOrDash(exitGross, fmtCurrency)],
              ['Disposition Fees', fmtOrDash(exitSellingCosts, fmtCurrency)],
            ]} />
          </div>
          {/* Wave 2 P2.5 - three-bucket capex plan (PIP / Non-PIP / ROI). */}
          <div className="grid grid-cols-1 gap-5 mt-5">
            <CapexPlanPanel
              keys={propertyKeys}
              revenueByYear={(() => {
                const wRevYears = getEngineField<RevenueYearLite[]>(
                  outputs, 'revenue', 'years',
                );
                return (wRevYears ?? []).map(y => y.total_revenue);
              })()}
              holdYears={(holdYears as number | undefined) ?? 5}
              state={capexPlan}
              onChange={setCapexPlan}
            />
          </div>
          {/* Wave 2 P2.6 — historical baseline (multi-year P&L walk).
              Silent when coverage_pct === 0 (no historical docs uploaded). */}
          <div className="grid grid-cols-1 gap-5 mt-5">
            <HistoricalBaselinePanel
              baseline={historicalBaseline}
              dealId={dealId}
              forecastY1={(() => {
                // Pull Y1 forecast cells from the worker engine output —
                // revenue.years[0] + expense.years[0] feed the rightmost
                // "Y1 Forecast" column on the panel. We only map the
                // canonical fields the panel renders; the rest fall back
                // to em-dashes.
                const rev = getEngineField<RevenueYearLite[]>(
                  outputs, 'revenue', 'years',
                );
                const exp = getEngineField<ExpenseYearLite[]>(
                  outputs, 'expense', 'years',
                );
                const r0 = rev?.[0];
                const e0 = exp?.[0];
                return {
                  total_revenue: r0?.total_revenue ?? null,
                  noi: e0?.noi ?? null,
                };
              })()}
            />
          </div>
          {/* FON-62 — detailed debt configuration lives on the Debt tab now,
              so there's one authoritative place for loan terms + the schedule.
              Investment keeps debt only as a read-only source (Sources & Uses).
              This pointer replaces the editable Debt Stack panel that used to
              sit here. */}
          <div className="mt-5 rounded-md border border-border bg-ink-50/40 px-4 py-3 flex items-center justify-between gap-3">
            <div className="text-[12.5px] text-ink-600 leading-relaxed">
              <span className="font-medium text-ink-900">Debt configuration moved to the Debt tab.</span>{' '}
              All loan terms, tranches, and the debt schedule are managed there — debt shows
              here only as a source in Sources &amp; Uses.
            </div>
            <a
              href="?tab=debt"
              className="shrink-0 text-[12px] font-medium px-3 py-1.5 rounded-md border border-border text-brand-700 hover:bg-brand-50 whitespace-nowrap"
            >
              Open Debt tab →
            </a>
          </div>
        </>
        );
      })()}

      {tab === 'Sources & Uses' && ctx && <LiveSourcesUses />}

      {tab === 'Timeline' && (() => {
        // ─── Transaction Timeline (Lovable spec) ─────────────────
        // Render all 13 rows in fixed order with three numeric
        // columns (START | DURATION | FINISH). Most rows have no
        // engine source yet, so we surface fixture values on the
        // demo and '—' otherwise — Lovable's screenshot keeps the
        // row visible even when values are missing.
        const TIMELINE: { event: string; start?: string; duration?: string; finish?: string }[] = [
          { event: 'Hotel Purchase',                          start: '9/30/2025',  duration: '0 mo',  finish: '9/30/2025' },
          { event: 'Senior Loan Interest-Only Period',        start: '9/30/2025',  duration: '48 mo', finish: '9/30/2029' },
          { event: 'Senior Loan Perm Loan Payoff',            start: '9/30/2029',  duration: '12 mo', finish: '9/30/2030' },
          { event: 'Acq. To Renovation',                      start: '9/30/2025',  duration: '3 mo',  finish: '12/31/2025' },
          { event: 'Renovation',                              start: '1/1/2026',   duration: '12 mo', finish: '12/31/2026' },
          { event: 'Completed Renovation',                    start: '1/1/2027' },
          { event: 'Receive Key Money',                       start: '1/1/2027',   duration: '1 mo',  finish: '2/1/2027' },
          { event: 'Ramp-Up Period',                          start: '1/1/2027',   duration: '12 mo', finish: '12/31/2027' },
          { event: 'Senior Loan Refi',                        start: '9/30/2029' },
          { event: 'Disposition After Refi',                  start: '9/30/2030' },
          { event: 'Investment Hold Period',                  start: '9/30/2025',  duration: '60 mo', finish: '9/30/2030' },
          { event: 'Practical Completion (FTM NOI, Value)',   start: '12/31/2026' },
          { event: 'Stabilized (FTM NOI, Value)',             start: '12/31/2027' },
        ];
        const cell = (v: string | undefined) =>
          v != null && v !== '' ? '—' : '—';
        return (
          <Card className="p-5">
            <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Transaction Timeline</h3>
            {/* Thin colored bar separator between title and column headers
                — matches Lovable's reference screenshot. */}
            <div className="h-1 w-full rounded bg-brand-500 mb-3" />
            <table className="w-full text-[12.5px]">
              <thead>
                <tr className="text-ink-500 text-[11px] border-b border-border">
                  <th className="text-left font-medium pb-2">Event</th>
                  <th className="text-right font-medium pb-2">START</th>
                  <th className="text-right font-medium pb-2">DURATION</th>
                  <th className="text-right font-medium pb-2">FINISH</th>
                </tr>
              </thead>
              <tbody>
                {TIMELINE.map(row => (
                  <tr key={row.event} className="border-b border-border/50">
                    <td className="py-2">{row.event}</td>
                    <td className="text-right tabular-nums text-ink-700">{cell(row.start)}</td>
                    <td className="text-right tabular-nums text-ink-700">{cell(row.duration)}</td>
                    <td className="text-right tabular-nums text-ink-700">{cell(row.finish)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        );
      })()}
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

// ───────────────────────────────────────────────────────────────────
// Live (Kimpton) Sources & Uses with editable assumptions.
// Worker capital engine output (sources / uses arrays) is the persisted
// authoritative version; we prefer it over the live-slider model when
// both are present.
// ───────────────────────────────────────────────────────────────────

interface CapitalLine { label: string; amount: number; pct?: number | null; is_total?: boolean }

// ─── Sources & Uses label canonicalization (Lovable spec) ────────────
// Lovable's reference shows an exact ordered set of rows on each side.
// The worker capital engine emits a different but overlapping set of
// labels (see apps/worker/app/engines/capital.py); a few Lovable rows
// (Soft Costs Arch/Design, Lender Fees & Costs, Interest Reserve,
// Op. Shortfall Reserve, Mezzanine, Sponsor Equity, LP Equity, Key
// Money) are not yet emitted by the engine. We render the full Lovable
// row list in order and fill from the engine where available, '—'
// otherwise.
const USES_ORDER: { label: string; aliases: string[] }[] = [
  { label: 'Purchase Price', aliases: ['Purchase Price'] },
  { label: 'Renovation Budget', aliases: ['Renovation Budget', 'Renovation'] },
  { label: 'Soft Costs (Arch/Design)', aliases: ['Soft Costs (Arch/Design)', 'Soft Costs'] },
  { label: 'Closing Costs', aliases: ['Closing Costs'] },
  { label: 'Working Capital / Reserves', aliases: ['Working Capital / Reserves', 'Working Capital'] },
  { label: 'Lender Fees & Costs', aliases: ['Lender Fees & Costs', 'Loan Costs'] },
  { label: 'Interest Reserve', aliases: ['Interest Reserve'] },
  { label: 'Op. Shortfall Reserve', aliases: ['Op. Shortfall Reserve', 'Operating Shortfall Reserve'] },
];
const SOURCES_ORDER: { label: string; aliases: string[] }[] = [
  { label: 'Senior Loan', aliases: ['Senior Loan', 'Senior Debt'] },
  { label: 'Mezzanine', aliases: ['Mezzanine'] },
  { label: 'Sponsor Equity', aliases: ['Sponsor Equity'] },
  { label: 'LP Equity', aliases: ['LP Equity'] },
  { label: 'Key Money', aliases: ['Key Money'] },
];

interface CanonicalRow { label: string; amount: number | null; total: boolean; pct: number }

function canonicalizeUses(raw: { label: string; amount: number; total?: boolean; pct?: number }[]): CanonicalRow[] {
  const total = raw.find(r => r.total) ?? null;
  const rows: CanonicalRow[] = USES_ORDER.map(({ label, aliases }) => {
    const hit = raw.find(r => !r.total && aliases.includes(r.label));
    return { label, amount: hit ? hit.amount : null, total: false, pct: hit?.pct ?? 0 };
  });
  rows.push({
    label: 'Total Uses',
    amount: total ? total.amount : rows.reduce((s, r) => s + (r.amount ?? 0), 0),
    total: true,
    pct: 1,
  });
  return rows;
}

function canonicalizeSources(raw: { label: string; amount: number; total?: boolean; pct?: number }[]): CanonicalRow[] {
  const total = raw.find(r => r.total) ?? null;
  const rows: CanonicalRow[] = SOURCES_ORDER.map(({ label, aliases }) => {
    const hit = raw.find(r => !r.total && aliases.includes(r.label));
    return { label, amount: hit ? hit.amount : null, total: false, pct: hit?.pct ?? 0 };
  });
  // The engine emits a single "Equity" line — surface it under
  // "Sponsor Equity" if no LP/Sponsor split is provided.
  const hasAnySource = rows.some(r => r.amount != null);
  if (!hasAnySource) {
    const equity = raw.find(r => !r.total && /^Equity$/i.test(r.label));
    if (equity) {
      const sponsorRow = rows.find(r => r.label === 'Sponsor Equity');
      if (sponsorRow) {
        sponsorRow.amount = equity.amount;
        sponsorRow.pct = equity.pct ?? 0;
      }
    }
  }
  rows.push({
    label: 'Total Sources',
    amount: total ? total.amount : rows.reduce((s, r) => s + (r.amount ?? 0), 0),
    total: true,
    pct: 1,
  });
  return rows;
}

function LiveSourcesUses() {
  const { assumptions, setAssumption, model } = useAssumptionsOptional()!;
  const [edit, setEdit] = useState(false);
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs } = useEngineOutputs(dealId);

  // Worker capital engine wins when present.
  const wSources = getEngineField<CapitalLine[]>(outputs, 'capital', 'sources');
  const wUses = getEngineField<CapitalLine[]>(outputs, 'capital', 'uses');

  const rawUses = (wUses && wUses.length > 0)
    ? wUses.map(u => ({ label: u.label, amount: u.amount, total: !!u.is_total, pct: u.pct ?? 0 }))
    : model.sourcesAndUses.uses.map(u => ({ ...u, total: u.total ?? false, pct: 0 }));
  const rawSources = (wSources && wSources.length > 0)
    ? wSources.map(s => ({ label: s.label, amount: s.amount, total: !!s.is_total, pct: s.pct ?? 0 }))
    : model.sourcesAndUses.sources.map(s => ({ ...s, total: s.total ?? false, pct: s.pct ?? 0 }));
  const usesRows = canonicalizeUses(rawUses);
  const sourcesRows = canonicalizeSources(rawSources);
  const usingWorker = (wSources && wSources.length > 0) || (wUses && wUses.length > 0);

  return (
    <>
      <div className="grid grid-cols-2 gap-5 mb-5">
        <Card className="p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[13px] font-semibold text-ink-900">Transaction Uses</h3>
            {usingWorker && (
              <span className="text-[9.5px] uppercase tracking-wide text-success-700 bg-success-50 rounded px-1.5 py-0.5">Live</span>
            )}
          </div>
          <table className="w-full text-[12.5px]">
            <tbody>
              {usesRows.map(u => (
                <tr key={u.label}
                  className={u.total ? 'font-semibold border-t border-border' : 'border-b border-border/50'}>
                  <td className="py-2">{u.label}</td>
                  <td className="text-right tabular-nums">
                    {u.amount != null ? fmtCurrency(u.amount) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card className="p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[13px] font-semibold text-ink-900">Transaction Sources</h3>
            {usingWorker && (
              <span className="text-[9.5px] uppercase tracking-wide text-success-700 bg-success-50 rounded px-1.5 py-0.5">Live</span>
            )}
          </div>
          <table className="w-full text-[12.5px]">
            <tbody>
              {sourcesRows.map(s => (
                <tr key={s.label}
                  className={s.total ? 'font-semibold border-t border-border' : 'border-b border-border/50'}>
                  <td className="py-2">
                    {s.label}
                    {!s.total && s.pct ? <span className="ml-2 text-ink-500 text-[11px]">{(s.pct * 100).toFixed(1)}%</span> : null}
                  </td>
                  <td className="text-right tabular-nums">
                    {s.amount != null ? fmtCurrency(s.amount) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>

      <Card className="p-5">
        <button onClick={() => setEdit(e => !e)}
          className="flex items-center gap-2 text-[12.5px] font-medium text-brand-700 hover:text-brand-800">
          {edit ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          {edit ? 'Hide' : 'Edit'} Assumptions
        </button>
        {edit && (
          <div className="mt-4 grid grid-cols-3 gap-4">
            <NumberField
              label="Purchase Price"
              value={assumptions.purchasePrice}
              onChange={v => setAssumption('purchasePrice', v)}
              format={v => `$${(v / 1e6).toFixed(2)}M`}
              step={100_000}
            />
            <NumberField
              label="Closing Costs %"
              value={assumptions.closingCostsPct}
              onChange={v => setAssumption('closingCostsPct', v)}
              format={v => `${(v * 100).toFixed(2)}%`}
              step={0.0025}
              min={0} max={0.10}
            />
            <NumberField
              label="Working Capital"
              value={assumptions.workingCapital}
              onChange={v => setAssumption('workingCapital', v)}
              format={v => `$${(v / 1e3).toFixed(0)}K`}
              step={25_000}
            />
            <NumberField
              label="Renovation Budget"
              value={assumptions.renovationBudget}
              onChange={v => setAssumption('renovationBudget', v)}
              format={v => `$${(v / 1e6).toFixed(2)}M`}
              step={100_000}
            />
            <NumberField
              label="LTV"
              value={assumptions.ltv}
              onChange={v => setAssumption('ltv', v)}
              format={v => `${(v * 100).toFixed(0)}%`}
              step={0.01}
              min={0.30} max={0.80}
            />
            <div className="text-[11.5px] text-ink-500 self-end pb-1">
              Equity Required:{' '}
              <span className="font-semibold text-ink-900 tabular-nums">{fmtCurrency(model.equity)}</span>
            </div>
          </div>
        )}
      </Card>
    </>
  );
}

function NumberField({
  label, value, onChange, format, step = 1, min, max,
}: {
  label: string; value: number; onChange: (v: number) => void; format: (v: number) => string;
  step?: number; min?: number; max?: number;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1">
        <label className="text-[11px] text-ink-500 uppercase tracking-wide">{label}</label>
        <span className="text-[12.5px] font-semibold text-brand-700 tabular-nums">{format(value)}</span>
      </div>
      <input
        type="number"
        value={value}
        step={step}
        min={min}
        max={max}
        onChange={e => {
          const v = parseFloat(e.target.value);
          if (!isNaN(v)) onChange(v);
        }}
        className="w-full px-2 py-1.5 text-[12.5px] border border-border rounded-md tabular-nums focus:outline-none focus:ring-2 focus:ring-brand-500"
      />
    </div>
  );
}

type PanelRow = [label: string, value: ReactNode];

function Panel({ title, rows }: { title: string; rows: PanelRow[] }) {
  return (
    <Card className="p-5">
      <h3 className="text-[13px] font-semibold text-ink-900 mb-3">{title}</h3>
      <div className="space-y-1 text-[12.5px]">
        {rows.map(([k, v], idx) => (
          <div
            key={`${k}-${idx}`}
            className="flex justify-between py-1.5 border-b border-border/50 last:border-0"
          >
            <span className="text-ink-500">{k}</span>
            <span className="font-medium tabular-nums text-ink-900">{v}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

/**
 * Inline keys override.
 *
 * Auto-sync (docs > wizard) lands in apps/worker/.../documents.py via
 * `_sync_deal_metadata_from_extraction`: when an OM extraction carries
 * `property_overview.keys`, the deals row is updated and an audit_log
 * entry is written. This widget is the user-facing escape hatch — when
 * the OM was wrong (e.g. pre-renovation key count vs post-renovation),
 * the analyst overrides here and the UI refreshes from the worker.
 *
 * Read-only when no worker connection (mock data flow). Reverts to
 * read-only when the deal id isn't a UUID (numeric mock ids).
 */
/**
 * FON-44 — an editable transaction-assumption cell. Shows the value with a
 * pencil affordance; on save it hands the parsed number back to the parent's
 * onSave (which PATCHes field_overrides + re-runs). Read-only when not live.
 */
function AssumptionField({
  value,
  format,
  toDraft,
  parse,
  onSave,
  editable,
  suffix,
  width = 'w-32',
}: {
  value: number | undefined;
  format: (v: number) => string;
  toDraft: (v: number) => string;
  parse: (s: string) => number | null;
  onSave: (v: number) => void | Promise<void>;
  editable: boolean;
  suffix?: string;
  width?: string;
}) {
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);
  const display = value != null ? format(value) : '—';

  if (!editable) {
    return <span className="tabular-nums font-medium text-ink-900">{display}</span>;
  }
  if (!editing) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <span className="tabular-nums font-medium text-ink-900">{display}</span>
        <button
          type="button"
          aria-label="Edit assumption"
          title="Edit assumption"
          onClick={() => { setDraft(value != null ? toDraft(value) : ''); setEditing(true); }}
          className="text-ink-400 hover:text-brand-600 transition-colors"
        >
          <Pencil className="w-3 h-3" />
        </button>
      </span>
    );
  }
  const submit = async () => {
    const parsed = parse(draft);
    if (parsed == null) {
      toast('Enter a valid number.', { type: 'error' });
      return;
    }
    setSaving(true);
    try {
      await onSave(parsed);
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };
  return (
    <span className="inline-flex items-center gap-1">
      <input
        type="number"
        value={draft}
        autoFocus
        disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void submit();
          if (e.key === 'Escape') setEditing(false);
        }}
        className={cn(width, 'px-2 py-0.5 text-[12.5px] border border-border rounded text-right tabular-nums')}
      />
      {suffix && <span className="text-[11px] text-ink-500">{suffix}</span>}
      <button type="button" aria-label="Save" onClick={() => void submit()} disabled={saving}
        className="text-emerald-600 hover:text-emerald-800 disabled:opacity-50">
        <Check className="w-3.5 h-3.5" />
      </button>
      <button type="button" aria-label="Cancel" onClick={() => setEditing(false)}
        className="text-ink-400 hover:text-ink-700">
        <X className="w-3.5 h-3.5" />
      </button>
    </span>
  );
}

function KeysOverride({
  dealId,
  currentKeys,
  onSaved,
}: {
  dealId: string;
  currentKeys: number | null | undefined;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  const editable = isWorkerConnected() && !/^\d+$/.test(dealId) && dealId.length > 0;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<string>(
    currentKeys != null ? String(currentKeys) : '',
  );
  const [saving, setSaving] = useState(false);

  if (!editable) {
    return (
      <span className="font-medium tabular-nums text-ink-900">
        {currentKeys != null ? String(currentKeys) : '—'}
      </span>
    );
  }

  const display = currentKeys != null ? String(currentKeys) : '—';

  if (!editing) {
    return (
      <span className="inline-flex items-center gap-2">
        <span className="font-medium tabular-nums text-ink-900">{display}</span>
        <button
          type="button"
          aria-label="Override room count"
          title="Override room count"
          onClick={() => {
            setDraft(currentKeys != null ? String(currentKeys) : '');
            setEditing(true);
          }}
          className="text-ink-400 hover:text-ink-700 transition-colors"
        >
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
      toast(`Room count saved: ${parsed} keys. Re-run engines to recompute.`, {
        type: 'success',
      });
      setEditing(false);
      onSaved();
    } catch (err) {
      const detail = err instanceof WorkerError ? err.body : String(err);
      toast(`Failed to save room count: ${detail || 'worker rejected update'}`, {
        type: 'error',
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <span className="inline-flex items-center gap-1.5">
      <input
        type="number"
        min={1}
        value={draft}
        autoFocus
        disabled={saving}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') void submit();
          if (e.key === 'Escape') setEditing(false);
        }}
        className="w-20 px-2 py-0.5 text-[12.5px] border border-border rounded text-right tabular-nums"
      />
      <button
        type="button"
        aria-label="Save room count override"
        onClick={() => void submit()}
        disabled={saving}
        className="text-emerald-600 hover:text-emerald-800 disabled:opacity-50"
      >
        <Check className="w-3.5 h-3.5" />
      </button>
      <button
        type="button"
        aria-label="Cancel"
        onClick={() => setEditing(false)}
        disabled={saving}
        className="text-ink-400 hover:text-ink-700 disabled:opacity-50"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </span>
  );
}
