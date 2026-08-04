'use client';
import {
  FileText, BarChart3, Map, Database, Pencil, Sparkles, Link2,
  Play, BookOpen, ArrowRight,
} from 'lucide-react';
import Link from 'next/link';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';

/**
 * Methodology — institutional explanation of how Fondok underwrites.
 *
 * Sam v2 P3 ask: "Explanation of extraction workflow / projection
 * methodology / market-data assumptions." This page is the durable
 * permalink for the platform's reasoning, paired with the in-context
 * AssumptionBadge tooltips that ship next to each number on the Overview.
 *
 * Sections mirror Sam's exact P3 sub-asks:
 *   1. Extraction workflow — how Fondok turns uploaded documents into
 *      typed fields that engines consume.
 *   2. Projection methodology — how Year-1 anchors are sourced, when
 *      PIP displacement applies, how out-years compound.
 *   3. Market-data assumptions — what each provenance source means
 *      and the precedence rules between them.
 *   4. Engine architecture — one-screen summary of the 8 engines and
 *      the dependency graph between them.
 *
 * Loom walkthrough embed slot is reserved at the top — drops in
 * without a code change once the video lands.
 */
export default function MethodologyPage() {
  return (
    <div className="max-w-4xl">
      <PageHeader
        eyebrow="Methodology"
        title="How Fondok underwrites"
        subtitle="The reasoning behind every number on the platform — what gets extracted, how projections are built, where market data comes from, and which assumptions are model-driven vs analyst-controlled."
      />

      {/* ─── Video walkthrough ─────────────────────────────────────── */}
      <Card className="p-6 mb-8 bg-brand-50/30 border-brand-100">
        <div className="flex items-start gap-4">
          <div className="w-12 h-12 rounded-lg bg-brand-50 flex items-center justify-center flex-shrink-0">
            <Play size={20} className="text-brand-500" />
          </div>
          <div className="flex-1">
            <h3 className="text-[14px] font-semibold text-ink-900">Walkthrough video</h3>
            <p className="text-[12.5px] text-ink-500 mt-1 leading-relaxed">
              A 5-minute Loom covering data-room upload → extraction → engine run → IC memo. We embed it here when it&apos;s recorded. In the meantime, the sections below contain the same content in written form.
            </p>
          </div>
        </div>
      </Card>

      {/* ─── 1. Extraction workflow ────────────────────────────────── */}
      <Section
        id="extraction"
        number="1"
        title="Extraction workflow"
        intro="Every uploaded document moves through five stages — parse, classify, extract, verify, reclassify. The pipeline is format-agnostic by design: a single OM may be a text PDF, scanned image, multi-tab Excel, or PowerPoint deck."
      >
        <Stages>
          <Stage
            n="1"
            title="Parse"
            Icon={FileText}
            body="The parser reads raw bytes into text + tables. PDFs use LlamaParse (when configured) with a PyMuPDF fallback. Excel (.xlsx / .xlsm) uses openpyxl; legacy .xls uses xlrd. PowerPoint (.pptx) uses python-pptx. Image-only PDFs without an OCR layer surface as error_kind=no_text so the user gets an actionable retry path."
          />
          <Stage
            n="2"
            title="Classify"
            Icon={Sparkles}
            body="A Haiku 4.5 Router agent reads the filename plus the first ~2K characters and emits a doc_type: OM, T12, PNL, STR, STR_TREND, CBRE_HORIZONS, PNL_BENCHMARK, RENT_ROLL, MARKET_STUDY, ROOM_MIX, or CONTRACT. A filename heuristic provides the fallback when the Router is unsure."
          />
          <Stage
            n="3"
            title="Extract"
            Icon={BarChart3}
            body="A Sonnet 4.6 Extractor agent loads a per-doc-type schema (apps/worker/app/agents/extraction_schemas/) and pulls every grounded number, identifier, and date into a flat list of typed ExtractionField rows: field_name, value, unit, source_page, confidence, raw_text. Anything not grounded in the source is dropped."
          />
          <Stage
            n="4"
            title="Verify"
            Icon={Link2}
            body="A Critic pass re-reads each cited number against the parser cache. Verified fields get a 0.98 confidence floor; mismatches drop to 0.50. The downstream UI surfaces this as the field-level confidence badge."
          />
          <Stage
            n="5"
            title="Reclassify"
            Icon={ArrowRight}
            body="A post-extraction reclassifier reads p_and_l_usali.period_type off the extracted fields and narrows broad PNL/T12 classifications into PNL_MONTHLY, PNL_YTD, or T12. This is why a single-month upload no longer outranks an annual T-12 in the engine actuals loaders."
          />
        </Stages>
      </Section>

      {/* ─── 2. Projection methodology ─────────────────────────────── */}
      <Section
        id="projection"
        number="2"
        title="Projection methodology"
        intro="Year-1 anchors source from the deal's actual extracted data when available; out-years compound from the un-displaced baseline so a heavy PIP doesn't permanently depress the curve. The full precedence chain by metric:"
      >
        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Year-1 Occupancy + ADR</h4>
          <Chain
            steps={[
              { label: 'Analyst override', desc: 'Set via the Overview inline editor — wins over every other source.' },
              { label: 'T-12 actual', desc: "Extracted from the deal's annual T-12 (ranked above YTD/monthly by period_type)." },
              { label: 'CBRE Horizons Y1 forecast', desc: 'When the deal has no T-12 but a CBRE Horizons report is uploaded, the segmented Y1 forecast feeds the anchor.' },
              { label: 'Kimpton seed default', desc: 'Last-resort fallback used only on deals with no extracted data. Surfaced as a Seed badge.' },
            ]}
          />
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Year-1 PIP displacement</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mb-2">
            When the capital engine carries a renovation budget &gt; $5,000 per key, Year-1 occupancy is depressed 15% and Year-1 ADR is depressed 8% to reflect rooms out of service and disruption pricing. The thresholds are tunable per deal via field_overrides. Year-2 onwards snap back to the stabilized baseline — a heavy PIP affects only the construction year, not the underwriting trajectory.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Exit cap rate</h4>
          <Chain
            steps={[
              { label: 'Analyst override', desc: 'Inline-edited value wins.' },
              { label: 'OM transaction-comps median', desc: 'When the OM carries 3+ comparable sales with cap rates in the 3–15% sanity band, the median is the anchor.' },
              { label: 'Kimpton 7.0% seed', desc: 'Default when no comps are extracted.' },
            ]}
          />
        </Card>

        <Card className="p-5">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Expense waterfall</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            Departmental, undistributed, and fixed-charge lines source from the T-12 first. Zero-valued extractor rows are treated as "not present" rather than authoritative — those gaps are filled from USALI 11th industry benchmarks (CBRE Benchmarker / HotStats) when uploaded, with brand-specific overrides as the final layer. NOI is computed as GOP minus management fee minus fixed charges (excludes FF&E reserve, matching the US cap-rate convention). FF&E reserve sits below NOI in the waterfall and contributes to Net Cash Flow.
          </p>
        </Card>
      </Section>

      {/* ─── 3. Market-data assumptions ────────────────────────────── */}
      <Section
        id="sources"
        number="3"
        title="Market-data assumptions"
        intro="Every assumption surfaced on the Overview carries a provenance badge telling you exactly where the value came from. The legend below explains each source label and its precedence."
      >
        <Card className="p-5 mb-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[12.5px]">
            <BadgeRow source="t12_actual" name="T-12 Actual">
              Year-1 anchor extracted from the deal&apos;s uploaded T-12. Out-years are grown forward at the configured expense / revenue growth rate.
            </BadgeRow>
            <BadgeRow source="cbre_horizons" name="CBRE Horizons">
              Forecast curve extracted from an uploaded CBRE Hotel Horizons report (subject submarket + chain-scale segment).
            </BadgeRow>
            <BadgeRow source="om_comps" name="OM Comps">
              Median cap rate derived from the OM&apos;s &quot;Comparable Sales&quot; transaction-comps table.
            </BadgeRow>
            <BadgeRow source="om_broker" name="OM Broker">
              Broker proforma value extracted from the OM. Treat with appropriate skepticism — these are the broker&apos;s pitched numbers.
            </BadgeRow>
            <BadgeRow source="pnl_benchmark" name="P&L Benchmark">
              Industry benchmark margin from a HotStats-style P&L benchmark report applied as a USALI ratio override.
            </BadgeRow>
            <BadgeRow source="analyst_override" name="Analyst Override">
              Set via the Overview inline editor. Wins over every other source.
            </BadgeRow>
            <BadgeRow source="seed" name="Seed Default">
              Kimpton fixture default. Surfaced as a Seed badge with grey tone — no deal-specific data has overridden this yet.
            </BadgeRow>
            <BadgeRow source="deal_row" name="Deal Row">
              Sourced from the deals table (entered on the create-deal wizard or PATCHed via the API). Property name, city, brand, keys, service level.
            </BadgeRow>
          </div>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Hover any number to trace it</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mb-3">
            Provenance runs on two levels, and every figure on the platform carries one or the other:
          </p>
          <ul className="space-y-2.5 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Inputs — “where did this come from?”</span>{' '}
              Assumptions (occupancy, ADR, growth rates, LTV, rate, exit cap) show a source badge / dotted underline colored by origin: green when grounded in the deal&apos;s own docs (T-12, OM, CBRE), amber when it&apos;s still a seed/benchmark default, violet when an analyst overrode it. When it&apos;s backed by a document the badge is clickable — a <Link2 size={10} className="inline" /> jumps you to that source in the Data Room.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Outputs — “how was this computed?”</span>{' '}
              Modeled values (rooms &amp; total revenue, NOI, GOP, debt service, DSCR, equity multiple, gross sale, IRR) hover to show the exact formula plus every named input, and each input chains one hop further — back to a source document, a seed/benchmark, an analyst override, or another computed value. Follow any number to ground.
            </li>
            <li>
              <span className="font-semibold text-ink-900">IRR is calculated, and says so.</span>{' '}
              IRR has no closed form — it&apos;s the discount rate that sets the NPV of the equity cash flows to zero, solved iteratively (Newton&apos;s method, bisection fallback). Its hover states that explicitly and lists the year-by-year cash-flow stream the solver ran over.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Primary financial source.</span>{' '}
              When several statements cover the same periods, Fondok ranks them — full-year sources over partial (monthly / YTD), most-recent period, then most-detailed — and badges the winner “Primary source” in the Data Room so it&apos;s clear which statement drives the historicals.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Cross-checked across statements.</span>{' '}
              When more than one full-year statement is uploaded, Fondok doesn&apos;t just trust the top-ranked one — it cross-checks each revenue and expense line across all of them and grounds Year-1 on the corroborated (median) value. So if a single statement mis-reads one line — say F&amp;B revenue comes through an order of magnitude low — the other full-year statements outvote it and the model isn&apos;t skewed by one bad extraction. A line reported by only one statement is used as-is.
            </li>
          </ul>
        </Card>

        <Card className="p-5">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">The full ledger</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            Analysis → Sources lists every modeled assumption, its value, and its origin in one table — filter to the ungrounded set to see exactly what&apos;s still riding on a default, and export the whole ledger to CSV so every figure in the IC memo is defensible.
          </p>
        </Card>
      </Section>

      {/* ─── 4. Engine architecture ────────────────────────────────── */}
      <Section
        id="engines"
        number="4"
        title="Engine architecture"
        intro="Eight deterministic engines run in dependency order. Engine outputs persist as typed payloads; the web app reads them via /deals/{id}/engine_outputs."
      >
        <Card className="p-5">
          <ul className="space-y-2.5 text-[12.5px] text-ink-700">
            {[
              ['Revenue', 'Rooms × occupancy × ADR projection + F&B + Other Operated + Resort Fees + Misc.'],
              ['F&B', 'Per-occupied-room F&B model with food/beverage split; resort fees handled as a separate line.'],
              ['Expense', 'USALI 11th departmental + undistributed + management fee + FF&E reserve + fixed charges → GOP, NOI (institutional), Net Cash Flow.'],
              ['Capital', 'Purchase price + closing costs + renovation budget + working capital → total capital; Sources & Uses.'],
              ['Debt', 'Senior loan amortization with hand-rolled IRR (Newton method, bisection fallback); DSCR; refi optionality.'],
              ['Returns', 'Levered + unlevered IRR, equity multiple, Year-1 CoC, terminal value via exit cap × terminal NOI. Handles loss-making (underwater) deals — a negative IRR or sub-1x multiple is reported honestly, not floored or crashed.'],
              ['Sensitivity', 'IRR heatmap across exit cap × hold years (or other configurable pairs).'],
              ['Partnership', 'GP / LP waterfall with preferred return, catch-up, promote tiers.'],
            ].map(([name, desc]) => (
              <li key={name} className="flex items-start gap-2">
                <span className="font-semibold text-ink-900 min-w-[88px]">{name}</span>
                <span className="text-ink-500">{desc}</span>
              </li>
            ))}
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">When an engine can&apos;t finish</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            Engines are deterministic and independent, so one can fail without taking down the rest. When one does, the deal shows a clear red banner naming the model, a plain-language reason, and a one-click Re-run — the numbers it feeds read as an explicit error, never as silent “—” dashes that look like missing data. Engines are also hardened against valid-but-extreme inputs (a deeply negative-return scenario, a zero base), so an ugly deal computes rather than crashes.
          </p>
        </Card>
      </Section>

      <div className="flex items-center gap-3 mt-8">
        <Link href="/projects">
          <Button variant="primary">
            <BookOpen size={14} /> Back to deals
          </Button>
        </Link>
        <Link href="/data-library">
          <Button variant="secondary">
            <Database size={14} /> Data Library
          </Button>
        </Link>
      </div>
    </div>
  );
}

function Section({
  number, title, intro, children, id,
}: {
  number: string; title: string; intro: string; children: React.ReactNode;
  /** Anchor id so external "Learn more →" links can scroll-jump here. */
  id?: string;
}) {
  return (
    <section id={id} className="mb-10 scroll-mt-24">
      <div className="flex items-baseline gap-3 mb-2">
        <span className="text-[11px] font-semibold text-brand-500 uppercase tracking-wide tabular-nums">
          Section {number}
        </span>
      </div>
      <h2 className="text-[20px] font-semibold text-ink-900 mb-2">{title}</h2>
      <p className="text-[13px] text-ink-500 mb-5 leading-relaxed max-w-3xl">{intro}</p>
      {children}
    </section>
  );
}

function Stages({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-1 gap-3">{children}</div>;
}

function Stage({
  n, title, Icon, body,
}: {
  n: string; title: string; Icon: typeof FileText; body: string;
}) {
  return (
    <Card className="p-4">
      <div className="flex items-start gap-4">
        <div className="flex flex-col items-center gap-1 flex-shrink-0">
          <div className="w-9 h-9 rounded-lg bg-brand-50 flex items-center justify-center">
            <Icon size={16} className="text-brand-500" />
          </div>
          <span className="text-[10px] text-ink-500 tabular-nums">Stage {n}</span>
        </div>
        <div className="flex-1">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-1">{title}</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">{body}</p>
        </div>
      </div>
    </Card>
  );
}

function Chain({ steps }: { steps: { label: string; desc: string }[] }) {
  return (
    <ol className="space-y-2.5">
      {steps.map((s, i) => (
        <li key={s.label} className="flex items-start gap-3 text-[12.5px]">
          <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-ink-300/30 text-ink-700 text-[10.5px] font-semibold tabular-nums flex-shrink-0">
            {i + 1}
          </span>
          <div>
            <span className="font-medium text-ink-900">{s.label}</span>
            <span className="text-ink-500"> — {s.desc}</span>
          </div>
        </li>
      ))}
    </ol>
  );
}

function BadgeRow({
  source, name, children,
}: {
  source: string; name: string; children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-2.5">
      <AssumptionBadge source={source} className="flex-shrink-0 mt-0.5" />
      <div>
        <div className="font-medium text-ink-900">{name}</div>
        <div className="text-ink-500 leading-relaxed">{children}</div>
      </div>
    </div>
  );
}
