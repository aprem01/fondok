'use client';
import { useState } from 'react';
import { useParams } from 'next/navigation';
import { ChevronDown, ChevronUp, Briefcase } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineLegend from './EngineLegend';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import { kimptonAnglerOverview } from '@/lib/mockData';
import { fmtCurrency, fmtPct, cn } from '@/lib/format';
import { useAssumptionsOptional } from '@/stores/assumptionsStore';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { IntroCard } from '@/components/help/IntroCard';

// Expense engine year shape — mirrors apps/worker/app/engines/expense.py.
// Only the fields we read on the Investment tab are typed here; the
// canonical shape lives in PLTab.tsx.
interface ExpenseYearLite { year: number; noi: number }

const subTabs = ['Deal Summary', 'Sources & Uses', 'Timeline'];

export default function InvestmentTab({ projectId }: { projectId: number | string }) {
  const [tab, setTab] = useState('Deal Summary');
  const o = kimptonAnglerOverview;
  const ctx = useAssumptionsOptional();
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { toast } = useToast();
  const isKimptonDemo = projectId === 7;
  const { outputs, previous } = useEngineOutputs(dealId);
  const { deal } = useDeal(dealId);
  const [computing, setComputing] = useState(false);
  const [runToken, setRunToken] = useState<number | null>(null);

  // ─── Worker engine field reads ────────────────────────────────────
  // Sam QA #8: the Investment tab used to render the Kimpton fixture
  // (Miami Beach, 132 keys, $36.4M etc.) for every deal because every
  // panel read from `o = kimptonAnglerOverview`. We now prefer worker
  // engine output and fall back to the Kimpton fixture only on the
  // demo deal (projectId === 7); other deals show '—' until the
  // engine has produced the value.
  const wPurchase = getEngineField<number>(outputs, 'capital', 'purchase_price');
  const wPricePerKey = getEngineField<number>(outputs, 'capital', 'price_per_key');
  const wEntryCap = getEngineField<number>(outputs, 'capital', 'entry_cap_rate');
  const wTotalCapital =
    getEngineField<number>(outputs, 'capital', 'total_capital_usd') ??
    getEngineField<number>(outputs, 'capital', 'total_capital');
  const wTotalCapitalPerKey = getEngineField<number>(
    outputs, 'capital', 'total_capital_per_key',
  );
  const wLtc = getEngineField<number>(outputs, 'capital', 'ltc');
  const wLtv = getEngineField<number>(outputs, 'capital', 'ltv');
  const wDebtAmount = getEngineField<number>(outputs, 'capital', 'debt_amount');

  const wLoanAmount = getEngineField<number>(outputs, 'debt', 'loan_amount');
  const wYearOneDscr = getEngineField<number>(outputs, 'debt', 'year_one_dscr');
  const wYearOneDebtYield = getEngineField<number>(outputs, 'debt', 'year_one_debt_yield');
  const wAnnualDebtService = getEngineField<number>(outputs, 'debt', 'annual_debt_service');

  const wExpenseYears = getEngineField<ExpenseYearLite[]>(outputs, 'expense', 'years');
  const wYearOneNoi = wExpenseYears && wExpenseYears.length > 0 ? wExpenseYears[0].noi : undefined;

  const wGrossSale = getEngineField<number>(outputs, 'returns', 'gross_sale_price');
  const wExitCap = getEngineField<number>(outputs, 'returns', 'exit_cap_rate');
  const wTerminalNoi =
    getEngineField<number>(outputs, 'returns', 'terminal_noi_usd') ??
    getEngineField<number>(outputs, 'returns', 'terminal_noi');
  const wSellingCosts = getEngineField<number>(outputs, 'returns', 'selling_costs');

  const hasCapitalOutput =
    wPurchase != null || wPricePerKey != null || wEntryCap != null;

  // Display helpers: prefer worker → fixture (Kimpton demo only) → '—'.
  // `valOrDash` picks the first non-null source; `pickNum` returns the
  // raw number for arithmetic (per-key dividers, etc.).
  const pickNum = (worker: number | undefined, fixture: number): number | undefined =>
    worker != null ? worker : (isKimptonDemo ? fixture : undefined);
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
      : (isKimptonDemo ? kimptonAnglerOverview.general.keys : undefined);

  if (!isKimptonDemo && !hasCapitalOutput) {
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
        // ─── Worker → fixture wiring for every panel below ──────
        // Property Overview: name/location/keys come from the deal
        // row (useDeal). Worker doesn't surface year_built / address
        // yet, so those stay fixture-only on the demo.
        const dealName = deal?.name ?? (isKimptonDemo ? o.general.name : undefined);
        const dealLocation = deal?.city ?? (isKimptonDemo ? o.general.location : undefined);
        const dealKeys = (deal?.keys && deal.keys > 0)
          ? deal.keys
          : (isKimptonDemo ? o.general.keys : undefined);
        const dealType = isKimptonDemo ? o.general.type : undefined;
        const dealYearBuilt = isKimptonDemo ? o.general.yearBuilt : undefined;
        const dealGba = isKimptonDemo ? o.general.gba : undefined;

        // Entry Valuation
        const entryNoi = pickNum(wYearOneNoi, 2_481_478);
        const entryCap = pickNum(wEntryCap, o.acquisition.entryCapRate);
        const entryPurchase = pickNum(wPurchase, o.acquisition.purchasePrice);
        const entryPricePerKey = pickNum(wPricePerKey, o.acquisition.pricePerKey);

        // Exit Valuation
        const exitTerminalNoi = pickNum(wTerminalNoi, o.reversion.terminalNOI);
        const exitCap = pickNum(wExitCap, o.reversion.exitCapRate);
        const exitGross = pickNum(wGrossSale, o.reversion.grossSalePrice);
        const exitPerKey = (exitGross != null && propertyKeys && propertyKeys > 0)
          ? exitGross / propertyKeys : undefined;
        const exitSellingCosts = pickNum(wSellingCosts, o.reversion.sellingCosts);

        // Renovation Budget — capital engine doesn't break out
        // hard/soft/fees yet; sub-rows stay fixture-only on demo.
        const renoBudget = isKimptonDemo ? o.investment.renovationBudget : undefined;
        const renoPerKey = (renoBudget != null && propertyKeys && propertyKeys > 0)
          ? renoBudget / propertyKeys : undefined;
        const renoPerSf = (renoBudget != null && dealGba && dealGba > 0)
          ? renoBudget / dealGba : undefined;
        const renoContingency = isKimptonDemo ? o.investment.contingency : undefined;
        const renoHard = isKimptonDemo ? 3_960_000 : undefined;
        const renoSoft = isKimptonDemo ? 528_000 : undefined;
        const renoFees = isKimptonDemo ? 264_000 : undefined;

        // Valuation Assumptions
        const totalCapital = pickNum(wTotalCapital, o.investment.totalCapital);
        const totalCapitalPerKey = wTotalCapitalPerKey != null
          ? wTotalCapitalPerKey
          : (totalCapital != null && propertyKeys && propertyKeys > 0)
            ? totalCapital / propertyKeys
            : undefined;
        const holdYears = isKimptonDemo ? o.returns.hold : undefined;

        // Senior Loan Financing — prefer debt engine echo, fall
        // back to capital engine debt_amount, then fixture.
        const loanAmount = pickNum(wLoanAmount ?? wDebtAmount, o.financing.loanAmount);
        const loanLtc = pickNum(wLtc, 0.65);
        const loanLtv = pickNum(wLtv, o.financing.ltv);
        const loanDebtYield = pickNum(wYearOneDebtYield, 0.181);
        const loanAcqCost = loanAmount != null ? loanAmount * 0.015 : undefined;
        const loanTerm = isKimptonDemo ? o.financing.term : undefined;
        const loanAmort = isKimptonDemo ? o.financing.amortization : undefined;

        return (
        <>
          <div className="grid grid-cols-2 gap-5">
            <Panel title="Property Overview" rows={[
              ['Name', dealName ?? '—'],
              ['Type', dealType ?? '—'],
              ['Location', dealLocation ?? '—'],
              ['Year Built', dealYearBuilt != null ? String(dealYearBuilt) : '—'],
              ['Pre-Renovation Keys', dealKeys != null ? String(dealKeys) : '—'],
              ['Post-Renovation Keys', dealKeys != null ? String(dealKeys) : '—'],
              ['Post-Renovation SF', dealGba != null ? dealGba.toLocaleString() : '—'],
            ]} />
            <Panel title="Entry Valuation" rows={[
              ['NOI', fmtOrDash(entryNoi, fmtCurrency)],
              ['Entry Cap Rate', fmtOrDash(entryCap, v => fmtPct(v, 2))],
              ['2025 Run-Rate NOI', fmtOrDash(entryNoi, fmtCurrency)],
              ['FTM Date', isKimptonDemo ? '12/31/2025' : '—'],
              ['Hotel Purchase Price', fmtOrDash(entryPurchase, fmtCurrency)],
              ['Per Key', fmtOrDash(entryPricePerKey, fmtCurrency)],
            ]} />
            <Panel title="Exit Valuation" rows={[
              ['Exit Month', isKimptonDemo ? '60' : '—'],
              ['Exit Date', isKimptonDemo ? '9/30/2030' : '—'],
              ['Fwd. 12 Mo NOI', fmtOrDash(exitTerminalNoi, fmtCurrency)],
              ['Exit Cap Rate', fmtOrDash(exitCap, v => fmtPct(v, 2))],
              ['Gross Exit Value', fmtOrDash(exitGross, fmtCurrency)],
              ['Per Key', fmtOrDash(exitPerKey, fmtCurrency)],
              ['Exit Sales Cost', fmtOrDash(exitSellingCosts, fmtCurrency)],
              ['Transfer Tax', isKimptonDemo ? '0.6%' : '—'],
            ]} />
            <Panel title="Renovation Budget" rows={[
              ['Renovation Budget', fmtOrDash(renoBudget, fmtCurrency)],
              ['Per Key', fmtOrDash(renoPerKey, fmtCurrency)],
              ['Per SF', fmtOrDash(renoPerSf, fmtCurrency)],
              ['Hard Costs (75%)', fmtOrDash(renoHard, fmtCurrency)],
              ['Soft Costs (20%)', fmtOrDash(renoSoft, fmtCurrency)],
              ['Professional Fees (5%)', fmtOrDash(renoFees, fmtCurrency)],
              ['Contingency', fmtOrDash(renoContingency, fmtCurrency)],
              ['Total Renovation', fmtOrDash(renoBudget, fmtCurrency)],
            ]} />
          </div>
          <div className="grid grid-cols-2 gap-5 mt-5">
            <Panel title="Valuation Assumptions" rows={[
              ['Total Dev. Cost', fmtOrDash(totalCapital, fmtCurrency)],
              ['Per Key', fmtOrDash(totalCapitalPerKey, fmtCurrency)],
              ['Hold Years', holdYears != null ? `${holdYears} yrs` : '—'],
              ['Stabilized NOI FWD 12', fmtOrDash(exitTerminalNoi, fmtCurrency)],
              ['Exit Cap Rate', fmtOrDash(exitCap, v => fmtPct(v, 2))],
              ['Sale Price', fmtOrDash(exitGross, fmtCurrency)],
              ['Disposition Fees', fmtOrDash(exitSellingCosts, fmtCurrency)],
            ]} />
            <Panel title="Senior Loan Financing" rows={[
              ['Month Funding', isKimptonDemo ? '0' : '—'],
              ['Start Date', isKimptonDemo ? '9/30/2025' : '—'],
              ['Term', loanTerm != null ? `${loanTerm} yrs` : '—'],
              ['Maturity Date', isKimptonDemo ? '9/30/2030' : '—'],
              ['Senior Acq. Cost', fmtOrDash(loanAcqCost, fmtCurrency)],
              ['LTC Amount', fmtOrDash(loanAmount, fmtCurrency)],
              ['LTC %', fmtOrDash(loanLtc, v => fmtPct(v, 1))],
              ['LTV Amount', fmtOrDash(loanAmount, fmtCurrency)],
              ['LTV %', fmtOrDash(loanLtv, v => fmtPct(v, 1))],
              ['DY Amount', isKimptonDemo ? fmtCurrency(4_280_000) : '—'],
              ['DY Date', isKimptonDemo ? '12/31/2027' : '—'],
              ['DY NOI', isKimptonDemo ? fmtCurrency(4_280_000) : '—'],
              ['DY %', fmtOrDash(loanDebtYield, v => fmtPct(v, 1))],
              ['Loan Amount', fmtOrDash(loanAmount, fmtCurrency)],
              ['Variable / Fixed', isKimptonDemo ? 'Variable' : '—'],
              ['Spread over SOFR', isKimptonDemo ? '290 bps' : '—'],
              ['SOFR Rate', isKimptonDemo ? fmtPct(0.035, 2) : '—'],
              ['SOFR Floor', isKimptonDemo ? fmtPct(0, 2) : '—'],
              ['SOFR Ceiling', isKimptonDemo ? fmtPct(0.045, 2) : '—'],
              ['Interest Only Period', isKimptonDemo ? '24 mo' : '—'],
              ['Amortization Period', loanAmort != null ? `${loanAmort} yrs` : '—'],
              ['Origination Fee', isKimptonDemo ? fmtPct(0.015, 2) : '—'],
            ]} />
          </div>
          <div className="grid grid-cols-1 gap-5 mt-5">
            <Panel title="Senior Loan Refinancing" rows={[
              // Refi terms aren't in the worker engine output yet, so
              // these stay fixture-only on the Kimpton demo and show
              // '—' on real deals rather than leaking refi proceeds.
              ['Month Funding', isKimptonDemo ? '48' : '—'],
              ['Start Date', isKimptonDemo ? '9/30/2029' : '—'],
              ['Term', isKimptonDemo ? `${o.refi.refiTerm} yrs` : '—'],
              ['Maturity Date', isKimptonDemo ? '9/30/2034' : '—'],
              ['LTV Amount', isKimptonDemo ? fmtCurrency(31_200_000) : '—'],
              ['LTV %', isKimptonDemo ? fmtPct(o.refi.refiLTV, 1) : '—'],
              ['Loan Proceeds', isKimptonDemo ? fmtCurrency(31_200_000) : '—'],
              ['Loan Payoff', fmtOrDash(loanAmount, fmtCurrency)],
              ['Cash Pulled Out', isKimptonDemo
                ? fmtCurrency(31_200_000 - o.financing.loanAmount)
                : '—'],
              ['Variable / Fixed', isKimptonDemo ? 'Fixed' : '—'],
              ['Interest Rate', isKimptonDemo ? fmtPct(o.refi.refiRate, 2) : '—'],
              ['Interest Only Period', isKimptonDemo ? '12 mo' : '—'],
              ['Amortization Period', isKimptonDemo ? `${o.refi.refiAmortization} yrs` : '—'],
              ['Origination Fee', isKimptonDemo ? fmtPct(0.0125, 2) : '—'],
              ['Refi Acq. Cost', isKimptonDemo ? fmtCurrency(31_200_000 * 0.0125) : '—'],
            ]} />
          </div>
        </>
        );
      })()}

      {tab === 'Sources & Uses' && (ctx ? <LiveSourcesUses /> : <StaticSourcesUses />)}

      {tab === 'Timeline' && (
        <Card className="p-5">
          <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Transaction Timeline</h3>
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-ink-500 text-[11px] border-b border-border">
                <th className="text-left font-medium pb-2">Event</th>
                <th className="text-right font-medium pb-2">Start</th>
                <th className="text-right font-medium pb-2">Duration</th>
                <th className="text-right font-medium pb-2">Finish</th>
              </tr>
            </thead>
            <tbody>
              {[
                ['Hotel Purchase', '9/30/2025', '0 mo', '9/30/2025'],
                ['Senior Loan Interest-Only Period', '9/30/2025', '48 mo', '9/30/2029'],
                ['Senior Loan Perm Loan Payoff', '9/30/2029', '12 mo', '9/30/2030'],
                ['Acq. To Renovation', '9/30/2025', '3 mo', '12/31/2025'],
                ['Renovation', '1/1/2026', '12 mo', '12/31/2026'],
                ['Completed Renovation', '1/1/2027', '—', '—'],
                ['Receive Key Money', '1/1/2027', '1 mo', '2/1/2027'],
                ['Ramp-Up Period', '1/1/2027', '12 mo', '12/31/2027'],
                ['Senior Loan Refi', '9/30/2029', '—', '—'],
                ['Disposition After Refi', '9/30/2030', '—', '—'],
                ['Investment Hold Period', '9/30/2025', '60 mo', '9/30/2030'],
                ['Practical Completion (FTM NOI, Value)', '12/31/2026', '—', '—'],
                ['Stabilized (FTM NOI, Value)', '12/31/2027', '—', '—'],
              ].map(row => (
                <tr key={row[0]} className="border-b border-border/50">
                  <td className="py-2">{row[0]}</td>
                  <td className="text-right tabular-nums text-ink-700">{row[1]}</td>
                  <td className="text-right tabular-nums text-ink-700">{row[2]}</td>
                  <td className="text-right tabular-nums text-ink-700">{row[3]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
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

// ───────────────────────────────────────────────────────────────────
// Live (Kimpton) Sources & Uses with editable assumptions.
// Worker capital engine output (sources / uses arrays) is the persisted
// authoritative version; we prefer it over the live-slider model when
// both are present.
// ───────────────────────────────────────────────────────────────────

interface CapitalLine { label: string; amount: number; pct?: number | null; is_total?: boolean }

function LiveSourcesUses() {
  const { assumptions, setAssumption, model } = useAssumptionsOptional()!;
  const [edit, setEdit] = useState(false);
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs } = useEngineOutputs(dealId);

  // Worker capital engine wins when present.
  const wSources = getEngineField<CapitalLine[]>(outputs, 'capital', 'sources');
  const wUses = getEngineField<CapitalLine[]>(outputs, 'capital', 'uses');

  const usesRows = (wUses && wUses.length > 0)
    ? wUses.map(u => ({ label: u.label, amount: u.amount, total: !!u.is_total, pct: u.pct ?? 0 }))
    : model.sourcesAndUses.uses.map(u => ({ ...u, total: u.total ?? false, pct: 0 }));
  const sourcesRows = (wSources && wSources.length > 0)
    ? wSources.map(s => ({ label: s.label, amount: s.amount, total: !!s.is_total, pct: s.pct ?? 0 }))
    : model.sourcesAndUses.sources;
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
                  <td className="text-right tabular-nums">{fmtCurrency(u.amount)}</td>
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
                  <td className="text-right tabular-nums">{fmtCurrency(s.amount)}</td>
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

function StaticSourcesUses() {
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs } = useEngineOutputs(dealId);
  const wSources = getEngineField<CapitalLine[]>(outputs, 'capital', 'sources');
  const wUses = getEngineField<CapitalLine[]>(outputs, 'capital', 'uses');

  const o = kimptonAnglerOverview;
  const uses = (wUses && wUses.length > 0)
    ? wUses.map(u => ({ label: u.label, amount: u.amount, total: !!u.is_total }))
    : o.uses;
  const sources = (wSources && wSources.length > 0)
    ? wSources.map(s => ({ label: s.label, amount: s.amount, total: !!s.is_total }))
    : o.sources;
  const usingWorker = (wSources && wSources.length > 0) || (wUses && wUses.length > 0);

  return (
    <div className="grid grid-cols-2 gap-5">
      <Card className="p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[13px] font-semibold text-ink-900">Transaction Uses</h3>
          {usingWorker && (
            <span className="text-[9.5px] uppercase tracking-wide text-success-700 bg-success-50 rounded px-1.5 py-0.5">Live</span>
          )}
        </div>
        <table className="w-full text-[12.5px]">
          <tbody>
            {uses.map(u => (
              <tr key={u.label} className={u.total ? 'font-semibold border-t border-border' : 'border-b border-border/50'}>
                <td className="py-2">{u.label}</td>
                <td className="text-right tabular-nums">{fmtCurrency(u.amount)}</td>
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
            {sources.map(s => (
              <tr key={s.label} className={s.total ? 'font-semibold border-t border-border' : 'border-b border-border/50'}>
                <td className="py-2">{s.label}</td>
                <td className="text-right tabular-nums">{fmtCurrency(s.amount)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

function Panel({ title, rows }: { title: string; rows: string[][] }) {
  return (
    <Card className="p-5">
      <h3 className="text-[13px] font-semibold text-ink-900 mb-3">{title}</h3>
      <div className="space-y-1 text-[12.5px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between py-1.5 border-b border-border/50 last:border-0">
            <span className="text-ink-500">{k}</span>
            <span className="font-medium tabular-nums text-ink-900">{v}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}
