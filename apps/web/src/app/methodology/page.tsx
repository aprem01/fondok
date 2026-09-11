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
import { REASONS, type ReasonCode } from '@/lib/ontology/reasons.generated';
import { CONCEPTS, CONCEPT_IDS, REGISTRY_VERSION } from '@/lib/ontology/concepts.generated';

// Section 8 — rows come from the GENERATED registry module, never literals.
// Critic cross-field checks (group "synthetic") are computed inputs with no
// document line, so they are left off the reference table.
const REGISTRY_ROWS = CONCEPT_IDS.map((id) => CONCEPTS[id]).filter((c) => c.group !== 'synthetic');

// Section 9 — the node vocabulary of the lineage graph, mirroring
// `fondok_schemas.lineage.NodeKind` and its id prefixes. `line:` is reserved
// (no normalized-statement hop is emitted yet) and is listed so the shape of
// the chain is documented before it gains that step.
const LINEAGE_KINDS: { label: string; id: string; desc: string }[] = [
  { label: 'KPI', id: 'kpi:', desc: 'A headline number a tab renders — the root of a chain.' },
  { label: 'Engine value', id: 'engine:', desc: 'One modeled value in an engine’s output, with the formula and inputs that produced it.' },
  { label: 'Assumption', id: 'assumption:', desc: 'A canonical underwriting input, carrying the source that set it.' },
  { label: 'Statement line', id: 'line:', desc: 'A USALI-normalized statement line. Reserved — the walk gains this step when normalized spreads are stored per run.' },
  { label: 'Extracted field', id: 'field:', desc: 'One field on one extraction, with its concept, basis and period.' },
  { label: 'Document', id: 'doc:', desc: 'An uploaded document on the deal.' },
  { label: 'Page', id: 'page:', desc: 'The page of that document the value was read from.' },
  { label: 'Override', id: 'override:', desc: 'An analyst override, carrying the justification note. Terminal.' },
  { label: 'Seed', id: 'seed:', desc: 'A platform default no document supports. Terminal, with its reason.' },
  { label: 'Benchmark', id: 'benchmark:', desc: 'A benchmark or market feed — CBRE Horizons, HOST, your portfolio P&L, STR.' },
  { label: 'Memo section', id: 'memo:', desc: 'An IC-memo section, linked from the document and page it cites.' },
];


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
 *   5. Return targets & pricing (FON-68) — the Investment Profile owns
 *      the hurdles; the Max Price Solver and pricing grid read them and
 *      never default one.
 *   6. IC Memo — diligence flags and the decision (FON-54a).
 *   7. What a dash means (Phase 0.3) — the ReasonCode vocabulary, rendered
 *      from `@/lib/ontology/reasons.generated` so the page cannot drift
 *      from the code.
 *   8. Concept registry (Phase 1.1 / 1.2) — one vocabulary for every named
 *      figure, rendered from the generated registry.
 *   9. Every number traces back (Phase 2.3) — the KPI → engine →
 *      assumption → field → document → page walk that
 *      `GET /deals/{id}/lineage` serves, what each step is, when a link
 *      states a reason instead of closing, and what `stale` means.
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
            Departmental, undistributed, and fixed-charge lines source from the T-12 first. Zero-valued extractor rows are treated as &quot;not present&quot; rather than authoritative — those gaps are filled from USALI 11th industry benchmarks (CBRE Benchmarker / HotStats) when uploaded, with brand-specific overrides as the final layer.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            The waterfall produces <strong>two</strong> distinct NOI figures, and every screen names which one it is showing:
          </p>
          <ul className="text-[12.5px] text-ink-500 leading-relaxed mt-2 space-y-2 list-disc pl-5">
            <li>
              <strong>NOI (before FF&amp;E reserve)</strong> — GOP minus management fee minus fixed charges, <em>excluding</em> the FF&amp;E reserve, matching the US cap-rate convention. Engine field <code className="text-[11.5px]">expense.years[].noi_institutional</code> (registry concept <code className="text-[11.5px]">ebitda</code>). This is the headline figure and the numerator of the entry cap rate. Where the product says simply &quot;NOI&quot;, it means this.
            </li>
            <li>
              <strong>Cash NOI (after FF&amp;E reserve)</strong> — the same waterfall less the FF&amp;E reserve, i.e. cash flow after reserves. Engine field <code className="text-[11.5px]">expense.years[].noi</code> (registry concept <code className="text-[11.5px]">noi</code>). The Debt engine divides by it for DSCR and debt yield, and the Returns engine capitalises it at the exit cap rate to derive the reversion — so the exit value is a Cash-NOI valuation, not a before-reserve one.
            </li>
          </ul>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            The two differ by exactly the FF&amp;E reserve. The reserve sits between them in the waterfall and contributes to Net Cash Flow. A pre-upgrade engine run that never persisted <code className="text-[11.5px]">noi_institutional</code> is labelled <em>&quot;NOI (basis unconfirmed — pre-upgrade run)&quot;</em> rather than claiming a basis it cannot prove.
          </p>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">When a chain falls through to the seed</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            Every precedence chain above can end at the Kimpton seed, and when it does the seed now carries a machine-readable reason rather than an unexplained default — one of the codes in Section 7: <code className="text-[11.5px]">no_document</code> when the deal carries no document of the type that would ground it, <code className="text-[11.5px]">no_source</code> when the document is there but none of its extracted fields resolves to that assumption, and <code className="text-[11.5px]">str_unavailable</code> when STR rates were requested but could not populate. Each grounded value carries the opposite record: the exact extraction row behind it — document, field path, page and the concept it resolved to — so &quot;which line of which statement is this?&quot; is answered from data, not inference. A figure derived from several rows (a growth CAGR, a corroborated median across statements) names its document and says so instead of pointing at a row that does not carry the number.
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
            <BadgeRow source="str_forecast" name="STR Forecast">
              Year-1 occupancy &amp; ADR seeded from STR — the Market tab&apos;s &quot;Use STR rates in the model&quot; writes the comp-set rates the card shows as explicit overrides (note: &quot;STR comp-set market rates (Market tab)&quot;), else the subject TTM or the BASE forward forecast. Financials → Projections shows &quot;Active basis: Market / STR · Revert&quot; only when the rates carry this tag, and the Market tab&apos;s STR card reads the same tags — &quot;STR rates active&quot;, &quot;STR rates unavailable — using T-12 base&quot;, or &quot;Pending re-run&quot; when the worker has not tagged the rates yet — never the request flag alone.
            </BadgeRow>
            <BadgeRow source="str_forecast_unavailable" name="STR Unavailable">
              STR rates were requested but could not populate (no STR Trend extraction, coverage too low, or a loader failure). The model stays on the T-12 base and says so — the STR seed is never silently &quot;active&quot;. Reason code: <ReasonTag code="str_unavailable" />.
            </BadgeRow>
            <BadgeRow source="derived_from_revpar_growth" name="Derived from RevPAR Growth">
              An analyst RevPAR-growth override derives ADR growth — (1 + RevPAR growth) ÷ (1 + occupancy growth) − 1 — with the occupancy path held, so the lever moves operating NOI. Setting ADR growth explicitly takes direct control.
            </BadgeRow>
            <BadgeRow source="seed" name="Seed Default">
              Kimpton fixture default. Surfaced as a Seed badge with grey tone — no deal-specific data has overridden this yet.
            </BadgeRow>
            <BadgeRow source="deal_row" name="Deal Row">
              Sourced from the deals table (entered on the create-deal wizard or PATCHed via the API). Project name, city, brand, keys, service level. The Property Name is not a deal-row field — see below.
            </BadgeRow>
          </div>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Project Name vs Property Name</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            <span className="font-semibold text-ink-900">Project Name</span> is the analyst&apos;s confidential deal identifier (e.g. &quot;Project Unicorn&quot;) — a deal-row field you set and rename on the Overview; document extraction never writes it. <span className="font-semibold text-ink-900">Property Name</span> is the asset as named in the offering documents (OM first, then the STR subject name) and is never inferred from the Project Name — it shows &quot;—&quot; until the OM is extracted (<ReasonTag code="no_document" />). The two are stored independently and editing one never changes the other. An analyst may override the Property Name from its Overview row: the override is stored as <code className="text-[11.5px]">field_overrides[&quot;property_overview.name&quot;]</code>, the extracted value and its source page are preserved, and &quot;Restore sourced value&quot; drops the override so the extracted name comes back.
          </p>
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
              <span className="font-semibold text-ink-900">NOI pin (reconciliation override).</span>{' '}
              A deal can carry <code>noi_override_by_year</code> — an analyst-entered per-year NOI schedule (the FON-67 lever used to reconcile to a source model) — and optionally <code>terminal_noi_override</code> for the exit-year reversion NOI. While either is set, the Debt and Returns engines read that schedule instead of the operating model, so RevPAR-growth / expense edits do not move NOI. Financials → Projections shows an &quot;NOI pinned to an analyst schedule&quot; notice whenever the pin is present (and says when terminal NOI is also pinned) — the operating model&apos;s own figure is withheld under <ReasonTag code="pin_active" />; <b>Clear pin</b> deletes the override(s) from <code>field_overrides</code> and re-runs the model, after which NOI follows the operating assumptions again.
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
            <li>
              <span className="font-semibold text-ink-900">One review state, Data Room ↔ Financials.</span>{' '}
              An extracted historical value is &quot;to review&quot; when its extraction confidence is below 85% and it hasn&apos;t been accepted or corrected — and only if it has a cell in Financials → Historicals (a value with nowhere to land is never counted). The Data Room&apos;s per-statement &quot;N to review&quot; badge is exactly the number of red cells in that statement&apos;s column; the global count is their sum. Every red cell is pinned to its own column&apos;s statement, so its SOURCE panel names — and its Accept / Edit acts on — that document, never another year&apos;s. Clicking a badge opens Historicals with that statement&apos;s column pinned and the first flagged cell in view; accepting or correcting a value clears the cell and both counts at once. Two statements that resolve to the same period keep separate columns (&quot;2023&quot;, &quot;2023 (2)&quot;) and the coverage strip says so.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">As-of rule</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            An underwriting is a claim about what was knowable on a date. When a deal sets an underwriting as-of date, Fondok will not ground a market assumption on a report published after it: a CBRE Horizons forecast, an OM comparable-sales table or an STR report dated later is refused, the assumption keeps the basis it already had, and the badge says <em>unavailable</em> with the reason <ReasonTag code="not_knowable_as_of" /> — never a silent substitution. The comparison respects how precisely the report is dated: a document known only to a year is compared year-to-year, a quarter quarter-to-quarter, so a report from the underwriting year is admitted rather than refused on a 31-December stamp. A report whose date cannot be established is <em>used</em>, flagged <ReasonTag code="as_of_unknown" /> — a missing date is a caveat, not grounds to withhold a number. The same date anchors the comparable-sales engine: the five-year lookback and the recency weighting are measured from the underwriting date rather than from today, so re-opening a deal months later does not quietly age its comp set out of the window. A deal that has not set an underwriting as-of date is unaffected in every respect — the acquisition close date is a modelling input and is never read as a knowledge horizon.
          </p>
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
              ['Debt', 'Senior + PACE tranche stack from analyst-entered terms (fixed or index + spread with floor / cap, amortization or interest-only, IO stub, maturity); monthly amortization schedule; DSCR, debt yield, LTV / LTC; analyst-entered covenant thresholds; refi optionality.'],
              ['Returns', 'Levered + unlevered IRR, equity multiple, Year-1 CoC, terminal value via exit cap × terminal NOI. Handles loss-making (underwater) deals — a negative IRR or sub-1x multiple is reported honestly, not floored or crashed.'],
              ['Sensitivity', 'IRR heatmap across exit cap × hold years (or other configurable pairs).'],
              ['Partnership', 'GP / LP waterfall with preferred return, catch-up, promote tiers. A deficit period is funded as a dated pro-rata GP/LP capital call (by ownership split) that adds to unreturned capital — the preferred return accrues on it — and is reported as additional contributions; Cash Flow → Partnership → Returns carry one treatment of additional equity.'],
            ].map(([name, desc]) => (
              <li key={name} className="flex items-start gap-2">
                <span className="font-semibold text-ink-900 min-w-[88px]">{name}</span>
                <span className="text-ink-500">{desc}</span>
              </li>
            ))}
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Debt is an assumptions workspace</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mb-3">
            Debt documents are optional. The Debt tab is where the financing is entered, and every core term is an analyst input the model runs on — Fondok does not read the term sheet in this release. Each edit is persisted as an analyst override <AssumptionBadge source="analyst_override" /> and re-runs the chain, so the Debt Schedule, DSCR, debt yield, LTV / LTC, Cash Flow and Returns all reflect what was entered.
          </p>
          <ul className="space-y-2 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Senior loan (tranche 1).</span>{' '}
              Amount (or LTV — either resizes the same loan), rate basis, amortization (0 = interest-only for the full term), an interest-only stub in months before principal begins, and maturity. Fixed prices off the entered coupon; Floating prices off the index assumption plus spread, clamped to an optional floor / cap. Switching basis asks for the term the new basis needs (a spread or a coupon) in the same save. Until an index is entered the floating build-up shows Fondok&apos;s flat SOFR assumption and says so — it is not market data.
            </li>
            <li>
              <span className="font-semibold text-ink-900">PACE loan (tranche 2).</span>{' '}
              Funding an amount adds it to Total Debt, LTV, LTC and debt yield immediately. Until a rate is entered the tranche is <em>terms pending</em>: it stays out of debt service and DSCR rather than running on an invented rate, and the tab says so next to the input.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Covenants.</span>{' '}
              Maximum LTV / LTC and minimum DSCR / debt yield are thresholds you enter. There is no default package — a covenant without an entered threshold shows its live Current reading, an &ldquo;Enter threshold&rdquo; input, and no pass / fail verdict. Entered thresholds are tested against the modeled Year-1 metrics (LTV / LTC at close).
            </li>
            <li>
              <span className="font-semibold text-ink-900">Maturity.</span>{' '}
              The schedule runs to maturity. A take-out before exit is modeled on the Refinance sub-tab; without one the loan balance at the end of the schedule is what the model repays at sale.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Fees.</span>{' '}
              Origination and exit fees are displayed from the entered percentages but are not yet carried into Sources &amp; Uses, Cash Flow or Returns — the tab labels them display-only rather than implying they move the numbers.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Missing inputs are inputs.</span>{' '}
              Wherever a required assumption is absent, the Debt tab renders the input to provide (&ldquo;Enter rate&rdquo;, &ldquo;Enter spread&rdquo;, &ldquo;Enter threshold&rdquo;) with the consequence stated, instead of an unexplained dash.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">When an engine can&apos;t finish</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            Engines are deterministic and independent, so one can fail without taking down the rest. When one does, the deal shows a clear red banner naming the model, a plain-language reason, and a one-click Re-run — the numbers it feeds read as an explicit error, never as silent “—” dashes that look like missing data. Engines are also hardened against valid-but-extreme inputs (a deeply negative-return scenario, a zero base), so an ugly deal computes rather than crashes.
          </p>
        </Card>
      </Section>

      {/* ─── 5. Return targets & pricing (FON-68) ─────────────────── */}
      <Section
        id="pricing"
        number="5"
        title="Return targets & the Max Price Solver"
        intro="The hurdles a deal must clear are the analyst's, set once on Overview → Investment Profile. Returns → Pricing reads them from the deal — there is no hidden 15% / 1.80x default anywhere in the pricing path."
      >
        <Card className="p-5">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">The Investment Profile is the source of truth</h4>
          <ul className="space-y-2 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Two analyst inputs.</span>{' '}
              <span className="font-medium">Target Levered IRR</span> and <span className="font-medium">Target MOIC</span> are stored on the deal (<code className="text-[11.5px]">target_irr</code> / <code className="text-[11.5px]">target_moic</code>) as analyst inputs <AssumptionBadge source="analyst_override" />. Either may be unset; an unset target renders as &ldquo;—&rdquo;.
            </li>
            <li>
              <span className="font-semibold text-ink-900">The returns profile only suggests.</span>{' '}
              A profile band such as Value Add (12-18%) is a suggestion. The &ldquo;Use profile midpoint&rdquo; action writes that midpoint (15%) to the deal explicitly — Fondok never applies a band implicitly, and an open band (18%+) offers its floor.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Benchmark only.</span>{' '}
              Saving a target never re-runs the model. The Overview&apos;s Return benchmark strip compares the calculated levered IRR from the canonical returns run against Target Levered IRR: below the target is <em>Below target</em>, at or above it up to 200bp over is <em>Within target</em>, and more than 200bp over is <em>Above target</em>. It is display only and does not drive any engine.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Max Price Solver (Returns → Pricing)</h4>
          <ul className="space-y-2 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">No silent hurdles.</span>{' '}
              <code className="text-[11.5px]">POST /analysis/{'{id}'}/pricing/max-price</code> reads the deal&apos;s targets when the request omits them. If neither the deal nor the request carries at least one target, the worker answers 422 — &ldquo;No return target set — set Target Levered IRR / Target MOIC on the Investment Profile or pass them explicitly&rdquo; — and the Pricing block shows that message with a link to the Investment Profile and no numbers.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Lower-of rule.</span>{' '}
              Each hurdle is solved independently by bisecting on purchase price (loan amount held fixed, equity flexes; 50%–200% of the current basis; ≤ 40 iterations). Max Price is the lower of the IRR-solved and MOIC-solved prices and the <em>binding constraint</em> is named (IRR, MOIC, or both when they land within $50K). With a single target set, that hurdle governs on its own.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Unreachable hurdles stay unreachable.</span>{' '}
              A hurdle that no price down to 50% of the basis can clear reports &ldquo;no price clears the hurdles&rdquo; (Max Price &ldquo;—&rdquo;); one that clears even at 2× the basis reports &ldquo;≥ 2× basis&rdquo;. The bracket endpoint is never shown as a price.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Pricing sensitivity grid.</span>{' '}
              <code className="text-[11.5px]">POST /analysis/{'{id}'}/pricing/max-price-grid</code> re-runs the solver per cell over exit cap rate (base ± 100bp in 50bp steps) × NOI growth (base ± 2pp in 1pp steps), at most 25 cells. Each cell shows the maximum purchase price clearing both hurdles with its binding constraint. NOI growth re-tilts the model&apos;s canonical NOI series relative to the base growth assumption — year 1 is unchanged, later years and the exit NOI move — so the base cell reproduces the headline solve exactly.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Known gap — exports.</span>{' '}
              The IC memo / Excel max-price section still prints the legacy 15% / 1.80x hurdles until the export path is migrated to the deal&apos;s targets.
            </li>
          </ul>
        </Card>
      </Section>

      {/* ─── 6. IC Memo — diligence flags and the decision (FON-54a) ── */}
      <Section
        id="ic-memo"
        number="6"
        title="IC Memo — diligence flags and the decision"
        intro="How broker-vs-T-12 variance flags reach the committee, what an 'NOI impact' on a flag means, how diligence status is kept, and why the IC recommendation is a recorded decision rather than a model inference."
      >
        <Card className="p-5">
          <ul className="space-y-2.5 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">One flag per business concept.</span>{' '}
              The extractor can emit the same broker line under several field paths (for example <code className="text-[11.5px]">broker_proforma.rooms_revenue_usd</code>, <code className="text-[11.5px]">broker.rooms_revenue</code> and a flat <code className="text-[11.5px]">rooms_revenue_usd</code>). The variance report groups those by their normalized concept — rooms revenue, F&amp;B revenue, total revenue, occupancy, ADR, RevPAR, GOP, NOI, and each expense line — and the IC memo lists one item per concept with a business-readable title (&ldquo;Rooms revenue — broker overstates T-12 by 5.3%&rdquo;). The consolidated severity is the highest severity across the merged rows. The normalized keys, every raw field path, its own broker and T-12 values, and the USALI rule ids stay available under <em>Technical detail</em> on each item and in the Technical detail column of the exported Variance sheet.
            </li>
            <li>
              <span className="font-semibold text-ink-900">&ldquo;Estimated NOI impact&rdquo; only when the delta is an NOI delta.</span>{' '}
              A dollar NOI impact is shown only for the NOI and GOP concepts, where the broker-vs-T-12 delta <em>is</em> the NOI difference. A revenue-line, KPI, or expense-line variance shows &ldquo;Revenue-line variance — NOI impact not estimated&rdquo; (or the expense / market-forecast equivalent) rather than a dollar figure — Fondok does not translate a revenue or expense delta into NOI without a flow-through assumption, and it never presents a revenue delta as if it were NOI.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Diligence status is persisted per concept.</span>{' '}
              Resolve, Accept variance and Reopen are recorded on the deal (<code className="text-[11.5px]">memo_diligence</code>, keyed by concept), so the IC-readiness checklist reads the same open / resolved status after a reload, and the Excel export&apos;s Variance sheet prints the same status in its Diligence column. A concept with no recorded status is Open. Status is analyst-recorded only — Fondok never auto-resolves a flag.
            </li>
            <li>
              <span className="font-semibold text-ink-900">The IC recommendation is a decision, not an inference.</span>{' '}
              The Model Assessment card shows the model&apos;s own read of the Base Case (Clears Hurdles / Clears with Conditions / Below Hurdles, with the inferred verdict labelled as the model&apos;s). The IC recommendation reads <em>Pending analyst decision</em> until the analyst selects Proceed, Proceed with Conditions or Do Not Proceed <em>and</em> confirms it. Only the confirmed verdict is written into the memo&apos;s Recommendation section and the export header; a selected-but-unconfirmed verdict, or a verdict recorded before confirmation existed, still reads Pending analyst decision, under the reason code <ReasonTag code="awaiting_analyst" />. Confirmation is part of the IC-readiness checklist.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">What a variance flag compares — and what it refuses to</h4>
          <ul className="space-y-2.5 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Only the broker&apos;s own claim is the &ldquo;broker&rdquo; side.</span>{' '}
              A row counts as the broker&apos;s claim about the subject only when it comes from the OM / broker materials under a claim path (<code className="text-[11.5px]">broker_proforma.*</code>, <code className="text-[11.5px]">broker.*</code>, <code className="text-[11.5px]">ttm_summary_per_om.*</code>, <code className="text-[11.5px]">ttm_performance.subject.*</code>). A line from a T-12 or P&amp;L document is an actual, never a broker figure, even when the extractor labelled it with an OM-style path; a subject or submarket reading from an STR / CoStar report is STR-reported performance, not the broker&apos;s claim; a competitive-set stat (<code className="text-[11.5px]">ttm_performance.segment.*</code>), the OM&apos;s historical-year block (<code className="text-[11.5px]">p_and_l_usali.2021.*</code>), and lines from CBRE, CapEx, insurance or property-information documents are none of these either. Rows excluded for one of these reasons are still listed under Technical detail with the reason and their source document, each carrying <ReasonTag code="basis_excluded" /> — the report says what it left out.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Units are normalised before comparing.</span>{' '}
              Occupancy and other ratio lines are compared as fractions (an extracted 83 or 83% reads as 0.83; the conversion is noted on the row); currency is compared in whole dollars (a &ldquo;$000&rdquo; unit is scaled). A value whose unit cannot be established — an occupancy above 100, for example — is not compared at all (<ReasonTag code="unit_unknown" />), and the Technical detail says why.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Annual against annual.</span>{' '}
              The T-12 side is the document&apos;s annual / trailing-twelve-month line (annual rooms revenue, gross operating profit, net operating income), never a monthly or quarterly slice, and the T-12 is preferred over a supporting P&amp;L. If no annual actual exists for a concept there is no flag — Fondok never compares a month against a year, and the report names the concept it could not compare with <ReasonTag code="period_mismatch" /> or <ReasonTag code="no_source" />.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Plausibility guard.</span>{' '}
              As a last line of defence, a pair of figures more than 300% apart is reported as &ldquo;Basis mismatch — needs review&rdquo; (<ReasonTag code="basis_mismatch" />) with both raw figures and no severity, instead of a Critical variance: two numbers that far apart are on different bases, not evidence of a broker overstatement.
            </li>
          </ul>
        </Card>
      </Section>

      {/* ─── 7. What a dash means (Phase 0.3 — ReasonCode vocabulary) ── */}
      <Section
        id="reasons"
        number="7"
        title="What a dash means"
        intro="Where Fondok cannot produce a figure it shows a dash — never a placeholder, a zero, or a number carried over from a prototype. Every such refusal resolves to one of the reason codes below. They are one shared vocabulary (packages/schemas-py/fondok_schemas/reasons.py, mirrored in TypeScript) and this table is rendered from that module, so it cannot drift from what the code does."
      >
        <Card className="p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]" data-testid="reason-code-table">
              <thead>
                <tr className="text-ink-500 text-[10.5px] border-b border-border bg-ink-300/5">
                  <th className="text-left font-medium px-5 py-2">Shown</th>
                  <th className="text-left font-medium px-3 py-2">Code</th>
                  <th className="text-left font-medium px-3 py-2">Meaning</th>
                  <th className="text-left font-medium px-3 py-2">What it tells you</th>
                </tr>
              </thead>
              <tbody>
                {(Object.keys(REASONS) as ReasonCode[]).map((code) => {
                  const meta = REASONS[code];
                  return (
                    <tr
                      key={code}
                      data-reason-code={code}
                      className="border-b border-border last:border-b-0 align-top"
                    >
                      <td className="px-5 py-2.5 text-ink-900 tabular-nums">{meta.ui}</td>
                      <td className="px-3 py-2.5">
                        <code className="text-[11.5px] text-ink-700">{code}</code>
                      </td>
                      <td className="px-3 py-2.5 font-medium text-ink-900">{meta.label}</td>
                      <td className="px-3 py-2.5 text-ink-500 leading-relaxed">{meta.explanation}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">How to read a dash</h4>
          <ul className="space-y-2 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">One glyph, many reasons.</span>{' '}
              Today every code renders the same &ldquo;&mdash;&rdquo;; the code is what distinguishes them. It travels on the value&apos;s provenance record (<code className="text-[11.5px]">reason</code>, alongside the source and formula that &ldquo;Hover any number to trace it&rdquo; in Section 3 describes) as each tab and export adopts the vocabulary.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Where it lands on the Data Key.</span>{' '}
              <code className="text-[11.5px]">needs_review</code>, <code className="text-[11.5px]">basis_mismatch</code>, <code className="text-[11.5px]">period_mismatch</code> and <code className="text-[11.5px]">unit_unknown</code> describe a figure that exists but cannot be trusted or compared, so they read as <em>Needs review</em>. Every other code means the figure is not there yet and reads as <em>Awaiting data</em>.
            </li>
            <li>
              <span className="font-semibold text-ink-900">A dash is not an error.</span>{' '}
              An engine that could not finish shows the red banner described in Section 4, with a Re-run. A dash is the model declining to invent a number it has no grounds for — the code says which grounds are missing.
            </li>
          </ul>
        </Card>
      </Section>

      {/* ─── 8. Concept registry (Phase 1.1 / 1.2) ─────────────────── */}
      <Section
        id="concept-registry"
        number="8"
        title="Concept registry"
        intro={`Every P&L line, operating statistic, OM figure, debt term and market metric Fondok names is defined once, in a registry the worker validates at boot and the web app is generated from. Registry v${REGISTRY_VERSION} · ${REGISTRY_ROWS.length} concepts on this table.`}
      >
        <Card className="p-5">
          <p className="text-[12.5px] text-ink-600 leading-relaxed">
            The same line reaches Fondok under many names — an extractor may emit <code className="text-[11.5px]">p_and_l_usali.gross_operating_profit</code>, <code className="text-[11.5px]">gop_usd</code> or <code className="text-[11.5px]">ttm_summary_per_om.gop_usd</code> for one GOP figure — and until now each screen and engine kept its own list of those names. The registry (<code className="text-[11.5px]">apps/worker/app/ontology/concepts.yaml</code>) holds one entry per concept: its USALI line, unit, sign and period, the identity it must satisfy (GOP = Total Revenue − Departmental − Undistributed), the USALI rules that test it, the engines that consume it, and its aliases per document type — each alias tagged with the basis it carries (a T-12 line is an <em>actual</em>; a <code className="text-[11.5px]">broker_proforma.*</code> line is the <em>broker&apos;s claim</em>; the OM&apos;s historical-year block and comp-set stats are neither) and, where the path says so, its period. The worker validates the registry when it starts (duplicate aliases, unknown rule ids or engines, an identity naming an unknown concept all fail the load) and reports the version on <code className="text-[11.5px]">/health</code> as <code className="text-[11.5px]">ontology_version</code>; <code className="text-[11.5px]">GET /ontology/concepts</code> serves it, and this page and the web app read a generated copy that CI refuses to let drift. Resolution is deterministic: an exact path listed for the document type wins, then a path listed for any document, then a unit-suffix-stripped match, then the field&apos;s last segment against a bare alias — a monthly, quarterly or year-to-date slice is never served as an annual figure, and a request for the broker&apos;s claim never returns the OM&apos;s history. The dash reasons in Section 7 are drawn from this same registry. Today the registry is the declared vocabulary and the drift gate; the engines, the USALI scorer, the variance report and the worksheet are re-wired onto it in the next phase, and their behaviour on the live system is unchanged until then.
          </p>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Concepts (generated from the registry)</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-ink-500 text-[11px] uppercase tracking-wide">
                  <th className="text-left font-medium px-2 py-1.5">Concept</th>
                  <th className="text-left font-medium px-2 py-1.5">USALI line</th>
                  <th className="text-left font-medium px-2 py-1.5">Unit</th>
                  <th className="text-left font-medium px-2 py-1.5">Period</th>
                  <th className="text-left font-medium px-2 py-1.5">Engines</th>
                </tr>
              </thead>
              <tbody>
                {REGISTRY_ROWS.map((c) => (
                  <tr key={c.id} className="border-t border-border/50 align-top">
                    <td className="px-2 py-1.5 whitespace-nowrap">
                      <span className="font-medium text-ink-900">{c.label}</span>{' '}
                      <code className="text-[11px] text-ink-500">{c.id}</code>
                    </td>
                    <td className="px-2 py-1.5 text-ink-600 whitespace-nowrap">{c.usali?.line ?? '—'}</td>
                    <td className="px-2 py-1.5 text-ink-600">{c.unit}</td>
                    <td className="px-2 py-1.5 text-ink-600">{c.period}</td>
                    <td className="px-2 py-1.5 text-ink-600">{c.engines.length ? c.engines.join(', ') : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </Section>

      {/* ─── 9. Every number traces back (Phase 2.3) ───────────────── */}
      <Section
        id="lineage"
        number="9"
        title="Every number traces back"
        intro="Pick any headline number on a deal and Fondok can show you the chain that produced it, one step at a time, down to the page of the document it was read from — or state, in the vocabulary of Section 7, exactly why the chain stops short."
      >
        <Card className="p-5">
          <p className="text-[12.5px] text-ink-600 leading-relaxed">
            The pieces already existed separately: each engine emits a per-value trace (the formula, its named inputs, and a pointer at whichever other value fed it — Section 3&apos;s &ldquo;hover any number&rdquo;), the assumption loader records which source produced every input, each extracted field carries the page it was read from, and the IC memo carries its citations. <code className="text-[11.5px]">GET /deals/{'{id}'}/lineage</code> joins them into one graph and serves it for the same run every tab is pinned to, so the chain always describes the numbers on screen rather than a newer or older model run. Each link in that graph also says how it was established: <strong className="text-ink-700">asserted</strong> when the engine that computed the value named the assumption or the upstream value itself, and <strong className="text-ink-700">inferred</strong> when Fondok had to derive the connection from the engine dependency graph instead — a genuine link either way, but only the asserted one is the engine&apos;s own claim, and every deal&apos;s chain carries the count of each.
          </p>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">The walk</h4>
          <Chain
            steps={[
              { label: 'KPI', desc: 'a headline number — levered IRR, unlevered IRR, equity multiple, Year-1 cash-on-cash, minimum DSCR, Year-1 NOI. These are the roots you can start from.' },
              { label: 'Engine value', desc: 'the modeled value behind it, and each modeled value behind that — IRR to the cash flows, cash flows to NOI and debt service, NOI to GOP, GOP to total revenue, revenue to rooms revenue.' },
              { label: 'Assumption', desc: 'the underwriting input the calculation rests on — starting occupancy, starting ADR, exit cap rate, LTV — carrying the source that produced it.' },
              { label: 'Extracted field', desc: 'the exact field on the exact extraction that supplied the assumption, with the concept it resolved to (Section 8) and the basis and period it carries.' },
              { label: 'Document → page', desc: 'the uploaded document, and the page the number sits on. This is where a walk that closes ends.' },
            ]}
          />
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            Three chains end deliberately short of a page, and each says so rather than looking grounded. An <strong className="text-ink-700">analyst override</strong> ends at the override, carrying the justification note the analyst saved with it. A <strong className="text-ink-700">seed</strong> — a platform default no document on the deal supports — ends at the seed. A <strong className="text-ink-700">benchmark</strong> — CBRE Horizons, the HOST default set, your own portfolio P&amp;L, an STR feed — ends at the benchmark, and continues to a page only when the benchmark itself arrived as an uploaded document.
          </p>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">What each step is</h4>
          <p className="text-[12.5px] text-ink-600 leading-relaxed mb-3">
            Every step in a chain is one of eleven kinds, and its identifier says which — so a link into the chain is stable and points at exactly one thing.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-ink-500 text-[11px] uppercase tracking-wide">
                  <th className="text-left font-medium px-2 py-1.5">Step</th>
                  <th className="text-left font-medium px-2 py-1.5">Identifier</th>
                  <th className="text-left font-medium px-2 py-1.5">What it is</th>
                </tr>
              </thead>
              <tbody>
                {LINEAGE_KINDS.map((k) => (
                  <tr key={k.id} className="border-t border-border/50 align-top">
                    <td className="px-2 py-1.5 whitespace-nowrap font-medium text-ink-900">{k.label}</td>
                    <td className="px-2 py-1.5"><code className="text-[11px] text-ink-500">{k.id}</code></td>
                    <td className="px-2 py-1.5 text-ink-600">{k.desc}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">A link that does not close states its reason</h4>
          <p className="text-[12.5px] text-ink-600 leading-relaxed">
            Anything the walk cannot take all the way to a page is reported, never dropped: each unresolved link names the step it broke on and carries a reason code from the Section 7 vocabulary — <code className="text-[11.5px]">no_document</code> when nothing on the deal could ground it, <code className="text-[11.5px]">no_source</code> when documents exist but none of their fields resolves to the concept, <code className="text-[11.5px]">str_unavailable</code> when a requested feed did not populate. An empty list means every root reached a document page. It never means the search stopped early.
          </p>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">What &ldquo;stale&rdquo; means</h4>
          <p className="text-[12.5px] text-ink-600 leading-relaxed">
            A chain describes one engine run, so it is only as current as that run&apos;s inputs. When a document is uploaded — or the deal record edited, or a document the run relied on deleted — <em>after</em> the run started, the chain is marked <strong className="text-ink-700">stale</strong>. Stale does not mean wrong and it does not hide the chain: the evidence shown is still exactly what produced the numbers you are looking at. It means the deal has moved since, and re-running the model will produce a different chain. It is the same condition Section 7&apos;s <code className="text-[11.5px]">stale_run</code> describes, surfaced on the evidence rather than on a single figure.
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

/**
 * Section 4.5 — name the reason code beside a refusal sentence.
 *
 * The label and the hover explanation come from
 * `@/lib/ontology/reasons.generated` (which CI regenerates from
 * `reasons.py`), never from a literal here, so a rename in the vocabulary
 * reaches this page instead of drifting from it. Purely additive: the
 * sentence it sits beside is unchanged.
 */
function ReasonTag({ code }: { code: ReasonCode }) {
  return (
    <span
      data-reason-tag={code}
      title={REASONS[code].explanation}
      className="inline-flex items-baseline gap-1 whitespace-nowrap align-baseline"
    >
      <code className="text-[11.5px] text-ink-700">{code}</code>
      <span className="text-[11px] text-ink-500">({REASONS[code].label})</span>
    </span>
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
