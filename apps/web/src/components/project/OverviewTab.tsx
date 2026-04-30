'use client';
import { useMemo, useState } from 'react';
import { useRouter, useParams } from 'next/navigation';
import { LayoutGrid, Download, Pencil, Link2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import {
  kimptonAnglerOverview, findBrand, returnProfiles, positioningTiers,
  brandFamilies,
} from '@/lib/mockData';
import { isWorkerConnected, workerUrl } from '@/lib/api';
import { fmtCurrency, fmtPct, fmtMillions, fmtNumber, cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useFlash } from '@/lib/hooks/useFlash';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';
import { Term } from '@/components/help/Term';
import { GLOSSARY } from '@/lib/glossary';

interface SourceUseLine {
  label: string;
  amount: number;
  pct?: number | null;
  is_total?: boolean;
}

export default function OverviewTab({ projectId }: { projectId: number | string }) {
  const router = useRouter();
  const params = useParams();
  const { toast } = useToast();
  const dealId = (params?.id as string | undefined) ?? String(projectId);
  const isMockId = /^\d+$/.test(dealId);
  const liveMode = isWorkerConnected() && !isMockId;

  // Empty state CTA — route the user to the Data Room (where uploads live).
  // Once docs are dropped, the engine tabs handle the actual run.
  const onRunUnderwriting = () => {
    router.push(`/projects/${dealId}`); // Data Room tab is the default
  };

  // Export-to-Excel on the Overview tab streams the worker's full deal
  // workbook for live deals; mock deals get a toast that points them at
  // the Export tab (which holds the canned deliverables).
  const onExportExcel = () => {
    if (liveMode) {
      window.location.href = `${workerUrl()}/deals/${dealId}/export/excel`;
    } else {
      toast('Excel export available from the Export tab once the model has run', { type: 'info' });
    }
  };

  // Pull worker output for the Sources/Uses, Proforma, Sensitivity sections.
  const { outputs } = useEngineOutputs(dealId);
  const { deal } = useDeal(dealId);
  const wSources = getEngineField<SourceUseLine[]>(outputs, 'capital', 'sources');
  const wUses = getEngineField<SourceUseLine[]>(outputs, 'capital', 'uses');
  const wReturnsIrr = getEngineField<number>(outputs, 'returns', 'levered_irr');
  const hasWorkerCapital = Array.isArray(wSources) && wSources.length > 0
    && Array.isArray(wUses) && wUses.length > 0;
  const hasWorkerReturns = wReturnsIrr != null;
  const hasAnyWorker = hasWorkerCapital || hasWorkerReturns;

  if (projectId !== 7 && !hasAnyWorker) {
    return (
      <Card className="p-16 text-center">
        <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
          <LayoutGrid size={20} className="text-ink-400" />
        </div>
        <h3 className="text-[15px] font-semibold text-ink-900">No underwriting data yet</h3>
        <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
          We need an Offering Memorandum (the broker&apos;s pitch deck) and a T-12 (the last 12 months
          of profit &amp; loss) before we can build the model. Drop them into the
          <span className="font-medium"> Data Room</span> tab to get started.
        </p>
        <Button
          variant="primary"
          size="sm"
          className="mt-4"
          onClick={onRunUnderwriting}
        >
          Open Data Room
        </Button>
      </Card>
    );
  }

  const o = kimptonAnglerOverview;
  const isKimptonDemo = projectId === 7;

  // ─── Fixture-leak gate ───────────────────────────────────────────
  // Same pattern as InvestmentTab.tsx: prefer worker engine output and
  // fall back to the Kimpton fixture only on the demo deal (projectId
  // === 7); other deals show '—' until the engine has produced the
  // value. `pickNum` returns a raw number for arithmetic;
  // `fmtOrDash` formats with a fallback to '—'.
  const pickNum = (worker: number | undefined, fixture: number): number | undefined =>
    worker != null ? worker : (isKimptonDemo ? fixture : undefined);
  const fmtOrDash = (
    n: number | undefined,
    formatter: (v: number) => string,
  ): string => (n != null ? formatter(n) : '—');

  // ─── Property metadata from the deal row (useDeal) ───────────────
  // Worker doesn't yet surface yearBuilt/gba/meetingSpace/parking/fbOutlets,
  // so those stay fixture-only on the Kimpton demo and render '—' on
  // real deals. On the Kimpton demo we pin to the fixture (name/type/
  // location) for byte-identical demo output; on real deals we read
  // from the deal row.
  const propertyName = isKimptonDemo
    ? o.general.name
    : (deal?.name ?? undefined);
  const propertyCity = isKimptonDemo
    ? o.general.location
    : (deal?.city ?? undefined);
  const propertyKeys = isKimptonDemo
    ? o.general.keys
    : ((deal?.keys && deal.keys > 0) ? deal.keys : undefined);
  const propertyBrand = isKimptonDemo
    ? o.general.brand
    : (deal?.brand ?? undefined);
  const propertyService = isKimptonDemo
    ? o.general.type
    : (deal?.service ?? undefined);

  // Brand tier enrichment: only valid when we have a brand string.
  const brandMatch = propertyBrand ? findBrand(propertyBrand) : null;
  const brandDisplay = propertyBrand
    ? (brandMatch ? `${propertyBrand} (${brandMatch.brand.tier})` : propertyBrand)
    : '—';

  // Investment Profile rows (return strategy, IRR target, positioning tier).
  // Profile / positioning live on the deal-creation wizard's enum and
  // currently surface only on the Kimpton demo.
  const profile = isKimptonDemo
    ? returnProfiles.find(r => r.id === o.investmentProfile.returnProfile)
    : undefined;
  const positioning = isKimptonDemo
    ? positioningTiers.find(p => p.id === o.investmentProfile.positioning)
    : undefined;

  // ─── Headline engine reads ───────────────────────────────────────
  // Acquisition / Reversion / Investment / Financing / Refi tiles all
  // map to capital + returns + debt engine fields. Per-key derivations
  // use propertyKeys; arithmetic falls back to undefined when the
  // divisor is missing.
  const wPurchase = getEngineField<number>(outputs, 'capital', 'purchase_price');
  const wPricePerKey = getEngineField<number>(outputs, 'capital', 'price_per_key');
  const wEntryCap = getEngineField<number>(outputs, 'capital', 'entry_cap_rate');
  const wTotalCapital =
    getEngineField<number>(outputs, 'capital', 'total_capital_usd') ??
    getEngineField<number>(outputs, 'capital', 'total_capital');
  const wDebtAmount = getEngineField<number>(outputs, 'capital', 'debt_amount');
  const wLtv = getEngineField<number>(outputs, 'capital', 'ltv');
  const wExitCap = getEngineField<number>(outputs, 'returns', 'exit_cap_rate');
  const wTerminalNoi =
    getEngineField<number>(outputs, 'returns', 'terminal_noi_usd') ??
    getEngineField<number>(outputs, 'returns', 'terminal_noi');
  const wGrossSale = getEngineField<number>(outputs, 'returns', 'gross_sale_price');
  const wSellingCosts = getEngineField<number>(outputs, 'returns', 'selling_costs');
  const wUnleveredIrr = getEngineField<number>(outputs, 'returns', 'unlevered_irr');
  const wEquityMultiple =
    getEngineField<number>(outputs, 'returns', 'equity_multiple') ??
    getEngineField<number>(outputs, 'returns', 'moic');
  const wYearOneCoC = getEngineField<number>(outputs, 'returns', 'year_one_coc');
  const wHold = getEngineField<number>(outputs, 'returns', 'hold_years');
  const wLoanAmount = getEngineField<number>(outputs, 'debt', 'loan_amount');
  const wInterestRate = getEngineField<number>(outputs, 'debt', 'interest_rate');
  const wDscr = getEngineField<number>(outputs, 'debt', 'year_one_dscr');
  const wAnnualDebtService = getEngineField<number>(outputs, 'debt', 'annual_debt_service');
  const wDebtTerm = getEngineField<number>(outputs, 'debt', 'term_years');
  const wDebtAmort = getEngineField<number>(outputs, 'debt', 'amortization_years');

  // Acquisition Assumptions
  const acqPurchase = pickNum(wPurchase, o.acquisition.purchasePrice);
  const acqPricePerKey = wPricePerKey != null
    ? wPricePerKey
    : (acqPurchase != null && propertyKeys && propertyKeys > 0)
      ? acqPurchase / propertyKeys
      : (isKimptonDemo ? o.acquisition.pricePerKey : undefined);
  const acqEntryCap = pickNum(wEntryCap, o.acquisition.entryCapRate);
  const acqClosingCosts = isKimptonDemo ? o.acquisition.closingCosts : undefined;
  const acqWorkingCapital = isKimptonDemo ? o.acquisition.workingCapital : undefined;

  // Returns Summary
  const retLeveredIrr = pickNum(wReturnsIrr, o.returns.leveredIRR);
  const retUnleveredIrr = pickNum(wUnleveredIrr, o.returns.unleveredIRR);
  const retEquityMultiple = pickNum(wEquityMultiple, o.returns.equityMultiple);
  const retYearOneCoC = pickNum(wYearOneCoC, o.returns.yearOneCoC);
  const retHold = pickNum(wHold, o.returns.hold);

  // Reversion
  const revExitCap = pickNum(wExitCap, o.reversion.exitCapRate);
  const revExitYear = isKimptonDemo ? o.reversion.exitYear : undefined;
  const revTerminalNoi = pickNum(wTerminalNoi, o.reversion.terminalNOI);
  const revGrossSale = pickNum(wGrossSale, o.reversion.grossSalePrice);
  const revSellingCosts = pickNum(wSellingCosts, o.reversion.sellingCosts);

  // Investment (renovation breakdown sub-rows aren't on capital engine yet)
  const invRenoBudget = isKimptonDemo ? o.investment.renovationBudget : undefined;
  const invHardPerKey = isKimptonDemo ? o.investment.hardCostsPerKey : undefined;
  const invSoftCosts = isKimptonDemo ? o.investment.softCosts : undefined;
  const invContingency = isKimptonDemo ? o.investment.contingency : undefined;
  const invTotalCapital = pickNum(wTotalCapital, o.investment.totalCapital);

  // Acquisition Financing
  const finLoanAmount = pickNum(wLoanAmount ?? wDebtAmount, o.financing.loanAmount);
  const finLtv = pickNum(wLtv, o.financing.ltv);
  const finInterestRate = pickNum(wInterestRate, o.financing.interestRate);
  const finDscr = pickNum(wDscr, o.financing.dscr);
  const finAnnualDebtService = pickNum(wAnnualDebtService, o.financing.annualDebtService);
  const finTerm = pickNum(wDebtTerm, o.financing.term);
  const finAmort = pickNum(wDebtAmort, o.financing.amortization);

  // Refi (no worker engine output yet)
  const refiYear = isKimptonDemo ? o.refi.refiYear : undefined;
  const refiLtv = isKimptonDemo ? o.refi.refiLTV : undefined;
  const refiRate = isKimptonDemo ? o.refi.refiRate : undefined;
  const refiTerm = isKimptonDemo ? o.refi.refiTerm : undefined;
  const refiAmort = isKimptonDemo ? o.refi.refiAmortization : undefined;

  return (
    <div className="space-y-5">
      <IntroCard
        dismissKey="overview-intro"
        title="The complete underwriting model on one page"
        body={
          <>
            Acquisition assumptions, financing, returns — every input and output the AI built from
            your documents. The colored dots in the legend below tell you which numbers you can edit
            (amber), which are derived from other engines (green), and which are read-only.
          </>
        }
      />

      <Card className="p-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-5 text-[11.5px] text-ink-500">
            <span className="flex items-center gap-1.5">
              <Pencil size={11} className="text-warn-500" /> Editable
            </span>
            <span className="flex items-center gap-1.5">
              <Link2 size={11} className="text-success-500" /> Linked
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2.5 h-2.5 rounded bg-ink-300/40" /> Read-Only
            </span>
          </div>
          <Button variant="secondary" size="sm" onClick={onExportExcel}><Download size={12} /> Export to Excel</Button>
        </div>
      </Card>

      <ModelSettings
        defaults={{
          dealType: 'acquisition',
          returnProfile: isKimptonDemo ? o.investmentProfile.returnProfile : 'value-add',
          brand: propertyBrand ?? '',
          positioning: 'default',
        }}
      />

      <div className="grid grid-cols-2 gap-5">
        <Section title="General Information" rows={[
          ['Property Name', propertyName ?? '—'],
          ['Location', propertyCity ?? '—'],
          ['Type', propertyService ?? '—'],
          ['Brand', brandDisplay],
          ['Keys', propertyKeys != null ? fmtNumber(propertyKeys) : '—'],
          ['Year Built', isKimptonDemo ? o.general.yearBuilt.toString() : '—'],
          ['GBA (SF)', isKimptonDemo ? fmtNumber(o.general.gba) : '—'],
          ['Meeting Space', isKimptonDemo ? o.general.meetingSpace : '—'],
          ['Parking Spaces', isKimptonDemo ? o.general.parking.toString() : '—'],
          ['F&B Outlets', isKimptonDemo ? o.general.fbOutlets.toString() : '—'],
        ]} />

        <Section title="Investment Profile" rows={[
          ['Return Strategy', profile?.label ?? '—'],
          ['IRR Target', profile?.target ?? '—'],
          ['Positioning Tier', positioning?.label ?? '—'],
        ]} />
      </div>

      <div className="grid grid-cols-1 gap-5">
        <Section title="Acquisition Assumptions" rows={[
          ['Purchase Price', fmtOrDash(acqPurchase, fmtCurrency)],
          ['Price/Key', fmtOrDash(acqPricePerKey, fmtCurrency)],
          ['Entry Cap Rate', fmtOrDash(acqEntryCap, v => fmtPct(v, 2))],
          ['Closing Costs', fmtOrDash(acqClosingCosts, fmtCurrency)],
          ['Working Capital', fmtOrDash(acqWorkingCapital, fmtCurrency)],
        ]} />
      </div>

      <Card className="p-5 bg-brand-50 border-brand-100">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-1">Returns Summary</h3>
        <p className="text-[11.5px] text-ink-500 mb-4">What investors will earn over the hold period.</p>
        <div className="grid grid-cols-5 gap-4">
          {([
            { label: 'Levered IRR', value: fmtOrDash(retLeveredIrr, v => fmtPct(v, 2)),
              tip: GLOSSARY['IRR'] + ' "Levered" means after debt service.' },
            { label: 'Unlevered IRR', value: fmtOrDash(retUnleveredIrr, v => fmtPct(v, 2)),
              tip: 'Asset-level IRR before debt — what you\'d earn if the hotel were paid for in cash.' },
            { label: 'Equity Multiple', value: fmtOrDash(retEquityMultiple, v => `${v.toFixed(2)}x`),
              tip: GLOSSARY['Equity Multiple'] },
            { label: 'Year-1 CoC', value: fmtOrDash(retYearOneCoC, v => fmtPct(v, 1)),
              tip: GLOSSARY['CoC'] + ' Year-1 is the first full year after acquisition.' },
            { label: 'Hold Period', value: fmtOrDash(retHold, v => `${v} Years`),
              tip: GLOSSARY['Hold Period'] },
          ]).map(s => (
            <div key={s.label}>
              <div className="text-[11px] text-ink-500 uppercase tracking-wide">
                <MetricLabel label={s.label} tip={s.tip} />
              </div>
              <div className="text-[20px] font-semibold text-brand-700 tabular-nums mt-0.5">{s.value}</div>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid grid-cols-2 gap-5">
        <Section title="Reversion Assumptions" rows={[
          ['Exit Cap Rate', fmtOrDash(revExitCap, v => fmtPct(v, 2))],
          ['Exit Year', revExitYear != null ? `Year ${revExitYear}` : '—'],
          ['Terminal NOI', fmtOrDash(revTerminalNoi, fmtCurrency)],
          ['Gross Sale Price', fmtOrDash(revGrossSale, fmtCurrency)],
          ['Selling Costs', fmtOrDash(revSellingCosts, fmtCurrency)],
        ]} />

        <Section title="Investment Assumptions" rows={[
          ['Renovation Budget', fmtOrDash(invRenoBudget, fmtCurrency)],
          ['Hard Costs/Key', fmtOrDash(invHardPerKey, fmtCurrency)],
          ['Soft Costs', fmtOrDash(invSoftCosts, fmtCurrency)],
          ['Contingency', fmtOrDash(invContingency, fmtCurrency)],
          ['Total Capital', fmtOrDash(invTotalCapital, fmtCurrency)],
        ]} />
      </div>

      <div className="grid grid-cols-2 gap-5">
        <Section title="Acquisition Financing" rows={[
          ['Loan Amount', fmtOrDash(finLoanAmount, fmtCurrency)],
          ['LTV', fmtOrDash(finLtv, v => fmtPct(v, 0))],
          ['Interest Rate', fmtOrDash(finInterestRate, v => fmtPct(v, 2))],
          ['DSCR', fmtOrDash(finDscr, v => `${v.toFixed(2)}x`)],
          ['Annual Debt Service', fmtOrDash(finAnnualDebtService, fmtCurrency)],
          ['Term', fmtOrDash(finTerm, v => `${v} Years`)],
          ['Amortization', fmtOrDash(finAmort, v => `${v} Years`)],
        ]} />

        <Section title="Refinancing Assumptions" rows={[
          ['Refi Year', refiYear != null ? `Year ${refiYear}` : '—'],
          ['Refi LTV', fmtOrDash(refiLtv, v => fmtPct(v, 0))],
          ['Refi Rate', fmtOrDash(refiRate, v => fmtPct(v, 2))],
          ['Refi Term', fmtOrDash(refiTerm, v => `${v} Years`)],
          ['Amortization', fmtOrDash(refiAmort, v => `${v} Years`)],
        ]} />
      </div>

      <div className="grid grid-cols-2 gap-5">
        <SourcesPanel
          sources={
            hasWorkerCapital
              ? wSources!
              : isKimptonDemo
                ? o.sources.map((s) => ({
                    label: s.label,
                    amount: s.amount,
                    pct: s.pct,
                    is_total: s.total,
                  }))
                : []
          }
          keys={propertyKeys ?? 0}
          source={hasWorkerCapital ? 'worker' : 'mock'}
        />
        <UsesPanel
          uses={
            hasWorkerCapital
              ? wUses!
              : isKimptonDemo
                ? o.uses.map((u) => ({ label: u.label, amount: u.amount, is_total: u.total }))
                : []
          }
          source={hasWorkerCapital ? 'worker' : 'mock'}
        />
      </div>

      <ProformaPanel outputs={outputs} isKimptonDemo={isKimptonDemo} />

      <SensitivityAnalysis outputs={outputs} isKimptonDemo={isKimptonDemo} />
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Model Settings — inline editor sitting above the General Info grid.
// Local state only; "X Changes Pending" pill flips amber when any
// field diverges from its initial default. No persistence yet.
// ──────────────────────────────────────────────────────────

interface ModelSettingsState {
  dealType: 'acquisition' | 'development';
  returnProfile: string;
  brand: string;
  positioning: string;
}

function ModelSettings({ defaults }: { defaults: ModelSettingsState }) {
  const [state, setState] = useState<ModelSettingsState>(defaults);
  const changeCount = (Object.keys(defaults) as (keyof ModelSettingsState)[])
    .reduce((n, k) => n + (state[k] !== defaults[k] ? 1 : 0), 0);

  // Flatten all known brands for the picker (family > brand[]).
  const brandOptions = useMemo(() => {
    return brandFamilies.flatMap(f =>
      f.brands.map(b => ({ value: b.name, label: `${b.name} (${b.tier})`, family: f.family }))
    );
  }, []);

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-[14px] font-semibold text-ink-900">Model Settings</h3>
        {changeCount === 0 ? (
          <span className="px-2 py-0.5 text-[10.5px] font-medium rounded-full bg-ink-300/30 text-ink-700">
            No Changes
          </span>
        ) : (
          <span className="px-2 py-0.5 text-[10.5px] font-medium rounded-full bg-warn-50 text-warn-700 border border-warn-500/30">
            {changeCount} Change{changeCount === 1 ? '' : 's'} Pending
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-4">
        {/* Deal Type pill toggle */}
        <div>
          <label className="block text-[11px] font-medium text-ink-700 uppercase tracking-wide mb-1.5">
            Deal Type
          </label>
          <div className="inline-flex bg-ink-300/15 p-0.5 rounded-md">
            {(['acquisition', 'development'] as const).map(opt => (
              <button
                key={opt}
                type="button"
                onClick={() => setState(s => ({ ...s, dealType: opt }))}
                className={cn(
                  'px-3 py-1 text-[12px] rounded transition-colors capitalize',
                  state.dealType === opt
                    ? 'bg-white text-brand-700 font-medium shadow-sm'
                    : 'text-ink-500 hover:text-ink-900'
                )}
              >
                {opt}
              </button>
            ))}
          </div>
        </div>

        {/* Returns Profile dropdown */}
        <div>
          <label className="block text-[11px] font-medium text-ink-700 uppercase tracking-wide mb-1.5">
            Returns Profile
          </label>
          <select
            value={state.returnProfile}
            onChange={e => setState(s => ({ ...s, returnProfile: e.target.value }))}
            className="w-full px-3 py-1.5 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          >
            {returnProfiles.map(p => (
              <option key={p.id} value={p.id}>{p.label} ({p.target})</option>
            ))}
          </select>
        </div>

        {/* Brand picker */}
        <div>
          <label className="block text-[11px] font-medium text-ink-700 uppercase tracking-wide mb-1.5">
            Brand
          </label>
          <select
            value={state.brand}
            onChange={e => setState(s => ({ ...s, brand: e.target.value }))}
            className="w-full px-3 py-1.5 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          >
            {/* Keep the deal's current brand string as the head option even if
                it doesn't match a catalog entry exactly (e.g. "Kimpton"). */}
            {!brandOptions.some(b => b.value === state.brand) && (
              <option value={state.brand}>{state.brand}</option>
            )}
            {brandFamilies.map(fam => (
              <optgroup key={fam.family} label={fam.family}>
                {fam.brands.map(b => (
                  <option key={b.name} value={b.name}>{b.name} ({b.tier})</option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>

        {/* Positioning dropdown */}
        <div>
          <label className="block text-[11px] font-medium text-ink-700 uppercase tracking-wide mb-1.5">
            Positioning
          </label>
          <select
            value={state.positioning}
            onChange={e => setState(s => ({ ...s, positioning: e.target.value }))}
            className="w-full px-3 py-1.5 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          >
            {positioningTiers.map(p => (
              <option key={p.id} value={p.id}>{p.label}</option>
            ))}
          </select>
        </div>
      </div>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────
// Sensitivity Analysis — three side-by-side heatmaps anchored
// on the Kimpton base case (IRR 23.01% / MOIC 2.37x / CoC 15.8%).
// Numbers are synthesized — meant to convey shape, not be canonical.
// ──────────────────────────────────────────────────────────

function SensitivityAnalysis({ outputs, isKimptonDemo }: {
  outputs: ReturnType<typeof useEngineOutputs>['outputs'];
  isKimptonDemo: boolean;
}) {
  // When the worker sensitivity engine has run, render its matrix as the
  // first heatmap. Other two stay as static for shape only.
  type WorkerCell = { row_value: number; col_value: number; value: number; is_base: boolean };
  const wOut = getEngineField<{
    row_variable: string;
    col_variable: string;
    metric: string;
    rows: number[];
    cols: number[];
    cells: WorkerCell[];
  }>(outputs, 'sensitivity');
  const labelFor = (key: string) => ({
    exit_cap_rate: 'Exit Cap',
    revpar_growth: 'RevPAR Growth',
    ltv: 'LTV',
    interest_rate: 'Interest Rate',
    hold_years: 'Hold',
    purchase_price: 'Purchase Price',
  } as Record<string, string>)[key] ?? key;

  return (
    <Card className="p-5">
      <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Sensitivity Analysis</h3>
      <div className="text-[11px] text-ink-500 mb-4">
        Base case highlighted; cells coloured green (best) → red (worst).
      </div>
      <div className="grid grid-cols-3 gap-4">
        {wOut ? (
          <WorkerHeatmap
            title="Levered IRR"
            rowLabel={labelFor(wOut.row_variable)}
            colLabel={labelFor(wOut.col_variable)}
            rows={wOut.rows}
            cols={wOut.cols}
            cells={wOut.cells}
            metric={wOut.metric}
          />
        ) : isKimptonDemo ? (
          <Heatmap
            title="Levered IRR"
            rowLabel="Exit Cap"
            colLabel="Purchase Price"
            rows={['6.0%', '6.5%', '7.0%', '7.5%', '8.0%']}
            cols={['-10%', '-5%', 'Base', '+5%', '+10%']}
            data={[
              [33.4, 30.6, 28.1, 25.8, 23.7],
              [30.7, 28.0, 25.5, 23.2, 21.1],
              [28.0, 25.3, 23.01, 20.8, 18.7],
              [25.4, 22.8, 20.5, 18.3, 16.3],
              [22.9, 20.4, 18.1, 15.9, 13.9],
            ]}
            baseRow={2} baseCol={2} unit="%"
          />
        ) : (
          <Card className="p-4 flex items-center justify-center text-[11px] text-ink-500">
            Run the Sensitivity engine to populate this matrix.
          </Card>
        )}
        {isKimptonDemo ? (
          <>
            <Heatmap
              title="Equity Multiple (MOIC)"
              rowLabel="Exit Cap"
              colLabel="LTC"
              rows={['6.0%', '6.5%', '7.0%', '7.5%', '8.0%']}
              cols={['60%', '65%', '70%', '75%']}
              data={[
                [2.61, 2.74, 2.88, 3.04],
                [2.46, 2.58, 2.72, 2.87],
                [2.31, 2.37, 2.49, 2.62],
                [2.17, 2.27, 2.38, 2.50],
                [2.04, 2.13, 2.24, 2.36],
              ]}
              baseRow={2} baseCol={1} unit="x"
            />
            <Heatmap
              title="Year-1 Cash-on-Cash"
              rowLabel="Cap Rate"
              colLabel="Hold"
              rows={['6.0%', '6.5%', '7.0%', '7.5%', '8.0%']}
              cols={['3y', '4y', '5y', '6y', '7y']}
              data={[
                [13.1, 13.9, 14.7, 15.5, 16.3],
                [13.7, 14.5, 15.3, 16.1, 16.9],
                [14.2, 15.0, 15.8, 16.6, 17.4],
                [14.7, 15.5, 16.3, 17.1, 17.9],
                [15.2, 16.0, 16.8, 17.6, 18.4],
              ]}
              baseRow={2} baseCol={2} unit="%"
            />
          </>
        ) : null}
      </div>
    </Card>
  );
}

// Worker-engine heatmap — same UI as the static Heatmap but reads a flat
// cell list rather than a pre-shaped 2D grid.
function WorkerHeatmap({
  title, rowLabel, colLabel, rows, cols, cells, metric,
}: {
  title: string; rowLabel: string; colLabel: string;
  rows: number[]; cols: number[];
  cells: { row_value: number; col_value: number; value: number; is_base: boolean }[];
  metric: string;
}) {
  const grid: { value: number; isBase: boolean }[][] = [];
  for (let i = 0; i < rows.length; i++) {
    const row: { value: number; isBase: boolean }[] = [];
    for (let j = 0; j < cols.length; j++) {
      const found = cells.find(
        c => Math.abs(c.row_value - rows[i]) < 1e-9 && Math.abs(c.col_value - cols[j]) < 1e-9,
      );
      row.push({ value: found?.value ?? 0, isBase: !!found?.is_base });
    }
    grid.push(row);
  }
  const flat = grid.flat().map(c => c.value);
  const min = Math.min(...flat); const max = Math.max(...flat);
  const colorFor = (v: number) => {
    const t = max === min ? 0.5 : (v - min) / (max - min);
    if (t > 0.66) return 'bg-success-50 text-success-700';
    if (t > 0.33) return 'bg-warn-50 text-warn-700';
    return 'bg-danger-50 text-danger-700';
  };
  const isMultiple = metric === 'equity_multiple';
  const fmtCell = (v: number) => isMultiple ? `${v.toFixed(2)}x` : `${(v * 100).toFixed(1)}%`;
  const fmtHeader = (v: number, key: string) =>
    key === 'Hold' ? `${v}y` : `${(v * 100).toFixed(1)}%`;
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-[12.5px] font-semibold text-ink-900">{title}</h4>
        <span className="text-[9.5px] uppercase tracking-wide text-success-700 bg-success-50 rounded px-1.5 py-0.5">Live</span>
      </div>
      <div className="text-[10.5px] text-ink-500 mb-3">{rowLabel} ↓ × {colLabel} →</div>
      <table className="w-full text-[10.5px]">
        <thead>
          <tr>
            <th></th>
            {cols.map((c, j) => (
              <th key={j} className="font-medium text-ink-500 pb-1 px-1">{fmtHeader(c, colLabel)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {grid.map((row, ri) => (
            <tr key={ri}>
              <td className="font-medium text-ink-500 pr-1 tabular-nums">{fmtHeader(rows[ri], rowLabel)}</td>
              {row.map((cell, ci) => (
                <td key={ci} className="p-0.5">
                  <div className={cn(
                    'rounded px-1 py-1.5 text-center font-medium tabular-nums',
                    colorFor(cell.value),
                    cell.isBase && 'ring-2 ring-brand-500',
                  )}>{fmtCell(cell.value)}</div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Heatmap({
  title, rowLabel, colLabel, rows, cols, data, baseRow, baseCol, unit,
}: {
  title: string; rowLabel: string; colLabel: string;
  rows: string[]; cols: string[]; data: number[][];
  baseRow: number; baseCol: number; unit: string;
}) {
  const flat = data.flat();
  const min = Math.min(...flat); const max = Math.max(...flat);
  const colorFor = (v: number) => {
    const t = max === min ? 0.5 : (v - min) / (max - min);
    if (t > 0.66) return 'bg-success-50 text-success-700';
    if (t > 0.33) return 'bg-warn-50 text-warn-700';
    return 'bg-danger-50 text-danger-700';
  };
  return (
    <Card className="p-4">
      <h4 className="text-[12.5px] font-semibold text-ink-900 mb-2">{title}</h4>
      <div className="text-[10.5px] text-ink-500 mb-3">{rowLabel} ↓ × {colLabel} →</div>
      <table className="w-full text-[10.5px]">
        <thead>
          <tr>
            <th></th>
            {cols.map(c => <th key={c} className="font-medium text-ink-500 pb-1 px-1">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {data.map((row, ri) => (
            <tr key={ri}>
              <td className="font-medium text-ink-500 pr-1 tabular-nums">{rows[ri]}</td>
              {row.map((v, ci) => {
                const isBase = ri === baseRow && ci === baseCol;
                return (
                  <td key={ci} className="p-0.5">
                    <div className={cn(
                      'rounded px-1 py-1.5 text-center font-medium tabular-nums',
                      colorFor(v),
                      isBase && 'ring-2 ring-brand-500'
                    )}>
                      {v.toFixed(unit === 'x' ? 2 : 1)}{unit}
                    </div>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function Section({ title, rows }: { title: string; rows: string[][] }) {
  return (
    <Card className="p-5">
      <h3 className="text-[13px] font-semibold text-ink-900 mb-3">{title}</h3>
      <div className="space-y-1.5 text-[12.5px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between py-1.5 border-b border-border/50 last:border-0">
            <span className="text-ink-500">{k}</span>
            <span className="font-medium tabular-nums text-ink-900">{v}</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

// Sources panel — prefers worker capital engine output. Per-key column hides
// when total keys is 0 (non-Kimpton deal without keys metadata).
function SourcesPanel({ sources, keys, source }: {
  sources: SourceUseLine[];
  keys: number;
  source: 'worker' | 'mock';
}) {
  const total = sources.find(s => s.is_total)?.amount
    ?? sources.reduce((sum, s) => s.is_total ? sum : sum + s.amount, 0);
  const flash = useFlash(total);
  if (sources.length === 0) {
    return (
      <Card className="p-5 flex items-center justify-center min-h-[120px] text-[12px] text-ink-500">
        Run the Capital engine to populate Sources.
      </Card>
    );
  }
  return (
    <Card className={cn('p-5', flash && 'value-flash')}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">
          Sources <span className="text-[11px] text-ink-500 font-normal">($ in mm)</span>
        </h3>
        {source === 'worker' && (
          <span className="text-[9.5px] uppercase tracking-wide text-success-700 bg-success-50 rounded px-1.5 py-0.5">Live</span>
        )}
      </div>
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="text-ink-500 text-[11px]">
            <th className="text-left font-medium pb-2">&nbsp;</th>
            <th className="text-right font-medium pb-2">Amount</th>
            <th className="text-right font-medium pb-2">% Total</th>
            {keys > 0 && <th className="text-right font-medium pb-2">Per Key</th>}
          </tr>
        </thead>
        <tbody>
          {sources.map(s => (
            <tr key={s.label} className={s.is_total ? 'font-semibold border-t border-border' : ''}>
              <td className="py-1.5">{s.label}</td>
              <td className="text-right tabular-nums">{(s.amount / 1e6).toFixed(2)}</td>
              <td className="text-right tabular-nums">
                {s.pct != null ? `${(s.pct * 100).toFixed(1)}%` : '—'}
              </td>
              {keys > 0 && (
                <td className="text-right tabular-nums">{(s.amount / keys / 1e3).toFixed(0)}K</td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function UsesPanel({ uses, source }: { uses: SourceUseLine[]; source: 'worker' | 'mock' }) {
  const total = uses.find(u => u.is_total)?.amount
    ?? uses.reduce((sum, u) => u.is_total ? sum : sum + u.amount, 0);
  const flash = useFlash(total);
  if (uses.length === 0) {
    return (
      <Card className="p-5 flex items-center justify-center min-h-[120px] text-[12px] text-ink-500">
        Run the Capital engine to populate Uses.
      </Card>
    );
  }
  return (
    <Card className={cn('p-5', flash && 'value-flash')}>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">
          Uses <span className="text-[11px] text-ink-500 font-normal">($ in mm)</span>
        </h3>
        {source === 'worker' && (
          <span className="text-[9.5px] uppercase tracking-wide text-success-700 bg-success-50 rounded px-1.5 py-0.5">Live</span>
        )}
      </div>
      <table className="w-full text-[12.5px]">
        <thead>
          <tr className="text-ink-500 text-[11px]">
            <th className="text-left font-medium pb-2">&nbsp;</th>
            <th className="text-right font-medium pb-2">Amount</th>
          </tr>
        </thead>
        <tbody>
          {uses.map(u => (
            <tr key={u.label} className={u.is_total ? 'font-semibold border-t border-border' : ''}>
              <td className="py-1.5">{u.label}</td>
              <td className="text-right tabular-nums">{(u.amount / 1e6).toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

// Proforma panel — prefer the worker expense engine years[] (in dollars,
// converted to $000s for display). Falls back to Kimpton mock for the demo.
function ProformaPanel({ outputs, isKimptonDemo }: {
  outputs: ReturnType<typeof useEngineOutputs>['outputs'];
  isKimptonDemo: boolean;
}) {
  type WorkerExpenseYear = {
    year: number;
    total_revenue: number;
    mgmt_fee: number;
    ffe_reserve: number;
    gop: number;
    noi: number;
    dept_expenses: { total: number };
    undistributed: { total: number };
    fixed_charges: { total: number };
  };
  type WorkerFBYear = {
    year: number;
    rooms_revenue: number;
    fb_revenue: number;
    other_revenue: number;
  };
  const expenseYears = getEngineField<WorkerExpenseYear[]>(outputs, 'expense', 'years');
  const fbYears = getEngineField<WorkerFBYear[]>(outputs, 'fb', 'years');
  const wDebtSchedule = getEngineField<{ year: number; debt_service: number }[]>(outputs, 'debt', 'schedule');

  const hasWorker = Array.isArray(expenseYears) && expenseYears.length > 0;

  type Row = { label: string; vals: number[]; cagr?: number; bold?: boolean };
  const cagr = (start: number, end: number, years = 4) =>
    start > 0 ? Math.pow(end / start, 1 / years) - 1 : 0;
  let rows: Row[] = [];

  if (hasWorker) {
    const ey = expenseYears!.slice(0, 5);
    const k = (v: number) => Math.round(v / 1000);
    const totalRev = ey.map(y => k(y.total_revenue));
    const noi = ey.map(y => k(y.noi));
    const opex = ey.map(y => k(y.dept_expenses.total + y.undistributed.total + y.fixed_charges.total));
    const mgmt = ey.map(y => k(y.mgmt_fee));
    const ffe = ey.map(y => k(y.ffe_reserve));

    const fbY = fbYears?.slice(0, 5) ?? [];
    const rooms = fbY.map(y => k(y.rooms_revenue));
    const fb = fbY.map(y => k(y.fb_revenue));
    const other = fbY.map(y => k(y.other_revenue));
    const ds = wDebtSchedule?.slice(0, 5).map(y => k(y.debt_service)) ?? totalRev.map(() => 0);
    const cfad = totalRev.map((_, i) => (noi[i] ?? 0) - (ds[i] ?? 0));

    const row = (label: string, vals: number[], bold = false): Row => ({
      label, vals, cagr: cagr(vals[0] ?? 0, vals[vals.length - 1] ?? 0), bold,
    });
    rows = [
      row('Room Revenue', rooms),
      row('F&B Revenue', fb),
      row('Other Revenue', other),
      row('Total Revenue', totalRev, true),
      row('Operating Expenses', opex),
      row('Management Fee', mgmt),
      row('FF&E Reserve', ffe),
      row('Net Operating Income', noi, true),
      row('Debt Service', ds),
      row('Cash Flow After Debt', cfad, true),
    ];
  } else if (isKimptonDemo) {
    rows = kimptonAnglerOverview.proforma.map(p => ({
      label: p.label,
      vals: [p.y1, p.y2, p.y3, p.y4, p.y5],
      cagr: p.cagr,
      bold: p.bold,
    }));
  }

  if (rows.length === 0) {
    return (
      <Card className="p-12 text-center text-[12px] text-ink-500">
        Run the P&amp;L engines (Revenue, F&amp;B, Expense) to populate the proforma.
      </Card>
    );
  }

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-[13px] font-semibold text-ink-900">Proforma Operating Summary</h3>
        {hasWorker && (
          <span className="text-[9.5px] uppercase tracking-wide text-success-700 bg-success-50 rounded px-1.5 py-0.5">Live</span>
        )}
      </div>
      <div className="text-[11px] text-ink-500 mb-3">($ in 000s, FYE Dec 31)</div>
      <div className="overflow-x-auto">
        <table className="w-full text-[12px] min-w-[600px]">
          <thead>
            <tr className="text-ink-500 text-[10.5px] border-b border-border">
              <th className="text-left font-medium pb-2 w-48">&nbsp;</th>
              <th className="text-right font-medium pb-2">Year 1</th>
              <th className="text-right font-medium pb-2">Year 2</th>
              <th className="text-right font-medium pb-2">Year 3</th>
              <th className="text-right font-medium pb-2">Year 4</th>
              <th className="text-right font-medium pb-2">Year 5</th>
              <th className="text-right font-medium pb-2">CAGR</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <ProformaRow key={r.label} row={r} />
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function ProformaRow({ row }: { row: { label: string; vals: number[]; cagr?: number; bold?: boolean } }) {
  const flash = useFlash(row.vals[0] ?? 0);
  return (
    <tr className={cn(
      'border-b border-border/50',
      row.bold && 'font-semibold bg-ink-300/5',
      flash && 'value-flash',
    )}>
      <td className="py-1.5">{row.label}</td>
      {row.vals.slice(0, 5).map((v, i) => (
        <td key={i} className="text-right tabular-nums">{v.toLocaleString()}</td>
      ))}
      <td className="text-right tabular-nums">{row.cagr ? `${(row.cagr * 100).toFixed(1)}%` : '—'}</td>
    </tr>
  );
}
