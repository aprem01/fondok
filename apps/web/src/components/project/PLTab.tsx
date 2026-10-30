'use client';
import { useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'next/navigation';
import { BarChart3 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import EngineHeader from './EngineHeader';
import EngineRightRail from './EngineRightRail';
import EngineRunHistory from './EngineRunHistory';
import WhatJustHappened from './WhatJustHappened';
import { cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useSubTab } from '@/lib/hooks/useSubTab';
import { PL_SUB_LABEL } from '@/lib/projectTabs';
import { IntroCard } from '@/components/help/IntroCard';
import ProjectionsSection from './pl/ProjectionsSection';
import GroundedWorksheet, { fieldMatchesKey } from './pl/GroundedWorksheet';
import { actualsOnly, derivePeriodBasis } from './pl/HistoricalsSection';
import { SubTabNav } from '@/components/design';
import { useDocuments } from '@/lib/hooks/useDocuments';

// Review is NOT its own screen — it lives inside Historicals, where the
// financial data already is (upload → fix/review in place → use the app).
// Design sync: the mockup leads with Historicals + Projections (P&L Summary
// was folded into Historicals when review moved in-place). The analytical
// tabs stay available after the two primary financial views.
// FON-60/61 — Index Analysis + Competitive Set moved to the Market tab (they're
// competitive-set analysis, not financial statements). FON-61 (Sam QA) — Due
// Diligence removed from Financials for now too.
// FON-59 #4 / FON-61 §3 — the sub-tab *id* is the URL slug (`?tab=pl&sub=…`);
// the label is display only. Ids are what `useSubTab` matches against.
// FON-41 R-066 — testers could not find the future business-plan P&L under
// "Financials → Projections". The tab is now "P&L" and the two views are
// named for what they are: the Historical P&L and the Future P&L. The ids
// (and every deep link) are unchanged.
const subTabs = [
  { id: 'historicals', label: PL_SUB_LABEL.historicals },
  { id: 'projections', label: PL_SUB_LABEL.projections },
] as const;
type SubTab = typeof subTabs[number]['id'];
const subTabIds = subTabs.map((t) => t.id) as readonly SubTab[];

export default function PLTab() {
  const searchParams = useSearchParams();
  // FON-59 #4 / FON-61 §3 — one convention, `?tab=pl&sub=<slug>`. `fin` stays
  // an accepted alias for one release so the Data Room's deep links and any
  // bookmarks keep working. The hook (not a `useState` initializer) also
  // re-syncs when the param changes while this tab is already mounted, which
  // is what made Overview / Market "→ Projections" links land on Historicals.
  const { sub: tab, setSub: setTab } = useSubTab(subTabIds, 'historicals', { legacyParam: 'fin' });
  const params = useParams();
  const { toast } = useToast();
  const dealId = (params?.id as string | undefined) ?? '';
  const { outputs, previous, settled: outputsSettled } = useEngineOutputs(dealId);
  const { documents, extractions } = useDocuments(dealId);
  const [computing, setComputing] = useState(false);

  // Arrival banner (canonical: "Opened from Data Room · extracted from {doc}").
  // The Data Room's "review financials / view data" CTAs deep-link here with a
  // `fin` sub-tab selector (and optionally `?focus=<field>`); that routed
  // arrival is the trigger. Doc name resolves to the statement behind the
  // focused field, else the deal's most recent financial P&L.
  const focusField = searchParams?.get('focus') ?? null;
  // FON-41: a ?doc=<id> deep-link (Data Room badge / "View Financials") names
  // that exact statement — never a guess from the first matching field.
  const docParam = searchParams?.get('doc') ?? null;
  const [arrivalDismissed, setArrivalDismissed] = useState(false);
  const arrivalDocName = useMemo(() => {
    if (docParam) {
      const pinned = documents.find((d) => d.id === docParam);
      if (pinned) return pinned.filename;
    }
    if (focusField) {
      for (const d of documents) {
        const ex = extractions[d.id];
        if (ex?.fields?.some((f) => fieldMatchesKey(f.field_name, focusField))) {
          return d.filename;
        }
      }
    }
    const fin = documents.find((d) => {
      const t = (d.doc_type ?? '').toUpperCase();
      return t.includes('T12') || t.includes('PNL') || t.includes('P&L') || t.includes('PROFIT');
    });
    return fin?.filename ?? documents[0]?.filename ?? null;
  }, [documents, extractions, focusField, docParam]);
  // The arrival banner names the Data Room specifically, so it keys off the
  // Data Room's own params (`fin` alias / `doc`) read ONCE at mount: `setTab`
  // now writes `?sub=` on every manual sub-tab click, and the Overview / Market
  // "View Projections →" deep links are not Data Room arrivals.
  const [arrivedFromDataRoom] = useState(
    () => !!searchParams?.get('fin') || !!searchParams?.get('doc'),
  );
  const showArrival = (arrivedFromDataRoom || !!focusField) && !arrivalDismissed;
  const subTabCaption =
    tab === 'projections'
      ? 'The forward business plan — assumptions, growth and projected performance'
      : 'Actual operating history, normalized and traceable to the source statement';
  // FON-41 E-014 — the primary financial statement's period ("T12 Mar 2025")
  // for the Future P&L's base-year column. The worker flags ONE document as
  // the primary financial source (``_mark_primary_financial``, the Data
  // Room's "Primary source" badge); its extraction states the period exactly
  // the way the Historical P&L column heads it (``derivePeriodBasis``). Null
  // when nothing is flagged or the period is not stated — never a guess.
  const basePeriodLabel = useMemo<string | null>(() => {
    const primary = documents.find((d) => d.primary_financial_source);
    if (!primary) return null;
    const ex = extractions[primary.id];
    if (!ex?.fields) return null;
    const period = derivePeriodBasis(
      actualsOnly(ex.fields),
      primary.filename ?? '',
      primary.doc_type,
      primary.fiscal_year ?? primary.extracted_period_year,
    );
    return period.periodBasis === 'UNKNOWN' ? null : period.periodLabel;
  }, [documents, extractions]);
  const [runToken, setRunToken] = useState<number | null>(null);
  // The P&L views need a run of the expense engine. Only its PRESENCE gates
  // the tab: the two views (the grounded worksheet and the Future P&L) read
  // their own figures, so nothing here coalesces a missing line to 0.
  const expenseYears = getEngineField<unknown[]>(outputs, 'expense', 'years');
  const hasWorkerStatement = Array.isArray(expenseYears) && expenseYears.length > 0;

  // Sam QA 9/9 follow-up: the engine-outputs fetch is async and the empty
  // state below used to render during it — on a deal with a complete run,
  // reviewers saw "No P&L output yet" flash for seconds (longer on a cold
  // worker). Hold a skeleton until the first fetch settles, so the empty
  // state is only ever a true statement.
  if (!hasWorkerStatement && !outputsSettled) {
    return (
      <div className="flex gap-4" data-testid="pl-loading">
        <div className="flex-1 min-w-0">
          <Card className="p-6" aria-busy="true" aria-label="Loading financial statements">
            <div className="h-4 w-48 rounded bg-ink-300/20 animate-pulse" />
            <div className="mt-3 h-3 w-80 rounded bg-ink-300/10 animate-pulse" />
            <div className="mt-6 grid grid-cols-3 gap-3">
              <div className="h-20 rounded bg-ink-300/10 animate-pulse" />
              <div className="h-20 rounded bg-ink-300/10 animate-pulse" />
              <div className="h-20 rounded bg-ink-300/10 animate-pulse" />
            </div>
          </Card>
        </div>
        <EngineRightRail />
      </div>
    );
  }

  if (!hasWorkerStatement) {
    return (
      <div className="flex gap-4">
        <div className="flex-1 min-w-0">
          <IntroCard
            dismissKey="pl-intro"
            title="Financial Statements"
            body={
              <>
                Profit and Loss — every dollar of revenue and expense across the hold period,
                formatted in industry-standard <span className="font-semibold">USALI</span> categories
                so you can compare any two hotels apples-to-apples.
              </>
            }
          />
          <EngineHeader
            name="Financial Statements"
            desc="Models room revenue, F&B, and operating expenses across the projection period in USALI format."
            outputs={['Total Revenue', 'NOI', 'GOP', 'Margin']}
            dependsOn={null}
            dealId={dealId}
            engineName="expense"
            runMode="all"
            onRunStart={() => setComputing(true)}
            onRunComplete={() => {
              setComputing(false);
              setRunToken(Date.now());
            }}
          />
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <BarChart3 size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">No P&L output yet</h3>
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              We need a <span className="font-medium">financial statement</span> — a T-12 or an
              annual / monthly P&amp;L — to project forward revenue and expenses. Drop one into the
              Data Room, then run the model.
            </p>
            <Button
              variant="primary"
              size="sm"
              className="mt-4"
              onClick={() => toast('Nothing is queued yet — add the missing document in the Data Room and the model runs automatically once extraction finishes', { type: 'info' })}
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
      {showArrival && (
        <div
          style={{
            display: 'flex', alignItems: 'center', gap: 10,
            background: '#eef2fb', border: '1px solid #dbe3f5', borderRadius: 8,
            padding: '10px 16px', marginBottom: 18, fontSize: 12.5, color: '#2f4a8c',
          }}
        >
          <span aria-hidden>📄</span>
          <span style={{ flex: 1 }}>
            Opened from Data Room · extracted from{' '}
            <strong>{arrivalDocName ?? 'your uploaded documents'}</strong> — values
            populated from this document.
          </span>
          <button
            type="button"
            onClick={() => setArrivalDismissed(true)}
            aria-label="Dismiss"
            style={{ cursor: 'pointer', color: '#8a8a86', fontSize: 14, background: 'none', border: 'none', lineHeight: 1 }}
          >
            ✕
          </button>
        </div>
      )}
      <EngineHeader
        name="Financial Statements"
        desc="Models room revenue, F&B, and operating expenses across the projection period in USALI format."
        outputs={['Total Revenue', 'NOI', 'GOP', 'Margin']}
        dependsOn={null}
        complete
        dealId={dealId}
        engineName="expense"
        runMode="all"
        onRunStart={() => setComputing(true)}
        onRunComplete={() => {
          setComputing(false);
          setRunToken(Date.now());
        }}
      />

      <WhatJustHappened
        engine="expense"
        engineLabel="P&L"
        outputs={outputs}
        previous={previous}
        runToken={runToken}
      />

      {/* Canonical section header card — "P&L" + the two-sided framing. */}
      <div
        style={{
          background: '#fff', border: '1px solid #eae9e4', borderRadius: 10,
          padding: '12px 16px', marginBottom: 14, display: 'flex', flexDirection: 'column', gap: 3,
        }}
      >
        <span style={{ fontSize: 13.5, fontWeight: 700, color: '#1a2233' }}>P&amp;L</span>
        <span style={{ fontSize: 12.5, color: '#6b6f76', lineHeight: 1.55, maxWidth: 960 }}>
          Verify and shape this deal&apos;s P&amp;L — the Historical P&amp;L (normalized
          operating actuals) on one side, the Future P&amp;L (the forward business plan that
          drives value) on the other.
        </span>
      </div>

      <SubTabNav
        className="mb-3.5"
        items={subTabs.map((t) => ({ id: t.id, label: t.label }))}
        activeId={tab}
        onSelect={(id) => setTab(id as SubTab)}
        caption={subTabCaption}
      />

      <div className={cn(computing && 'relative pointer-events-none opacity-60')}>
        {tab === 'historicals' && (
          // One table: the grounded worksheet is the single financial-data
          // surface — multi-year grounded columns (incl. Occupancy/ADR/RevPAR),
          // inline-editable Model column, click-to-source, low-confidence flags,
          // and structure editing. It reuses HistoricalsSection's loader via
          // useHistoricals, so nothing was lost by retiring the old table here.
          <GroundedWorksheet dealId={dealId} />
        )}
        {tab === 'projections' && (
          <ProjectionsSection dealId={dealId} basePeriodLabel={basePeriodLabel} />
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
