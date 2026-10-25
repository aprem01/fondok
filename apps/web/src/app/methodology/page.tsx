'use client';
import {
  FileText, BarChart3, Database, Sparkles, Link2, BookOpen, ArrowRight, Pencil,
} from 'lucide-react';
import Link from 'next/link';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';
import { ShowHintsAgainButton } from '@/components/help/HintOptionsMenu';
import { REASONS, REASON_CODES, type ReasonCode } from '@/lib/ontology/reasons.generated';
import { clientReasonExplanation } from '@/lib/reasonCopy';
import { OPERATING_MODEL_OPTIONS, MGMT_FEE_GUIDANCE_BY_MODEL, MGMT_FEE_GUIDANCE_DISCLAIMER } from '@/lib/operatingModel';

/**
 * Methodology — how Fondok underwrites, written for the people who read the
 * numbers: analysts, principals and an investment committee.
 *
 * This page describes live behaviour in plain language. It names what is
 * extracted, how projections, market assumptions, debt and returns are built,
 * and which assumptions are the analyst's. It never names an engine field, an
 * API route, a ticket, a release date or a model vendor — the engineering
 * reference behind it lives on the diagnostics-gated internal route and in
 * the repository's engineering notes. A test asserts that discipline.
 */
export default function MethodologyPage() {
  return (
    <div className="max-w-4xl" data-testid="methodology-client">
      <PageHeader
        eyebrow="Methodology"
        title="How Fondok underwrites"
        subtitle="The reasoning behind every number on the platform — what is read from your documents, how the projection, market, financing and returns are built, and which assumptions are yours to set."
        action={<ShowHintsAgainButton />}
      />

      {/* ─── 1. What Fondok reads from your documents ─────────────── */}
      <Section
        id="extraction"
        number="1"
        title="What Fondok reads from your documents"
        intro="Every uploaded document moves through the same stages: it is read, filed by type, its figures are extracted and checked, and the result is reconciled against the statement's own totals. Nothing is typed in by Fondok — a figure the documents do not state stays a dash."
      >
        <Stages>
          <Stage
            n="1"
            title="Read"
            Icon={FileText}
            body="PDFs, Excel workbooks, CSV files, Word documents and PowerPoint decks are read into text and tables. A scanned PDF with no readable text layer is marked as unreadable in the Data Room with a Retry, rather than quietly producing nothing."
          />
          <Stage
            n="2"
            title="Classify"
            Icon={Sparkles}
            body="Fondok reads the file name and first pages and files the document as an Offering Memorandum, T-12, P&L, STR report, market study, benchmark report, rent roll, hotel program, capital plan, insurance, tax or contract. A document dropped without a tag is marked “Classified automatically — confirm” in the Data Room until you confirm or change the type."
          />
          <Stage
            n="3"
            title="Extract"
            Icon={BarChart3}
            body="Every grounded figure, identifier and date is pulled into a typed list, each entry carrying the page it was read from and a confidence score. Anything not grounded in the source is dropped. A field below the review threshold is flagged in the Data Room and in the Historical P&L until you accept or correct it."
          />
          <Stage
            n="4"
            title="Reconcile"
            Icon={Database}
            body="When a T-12 or P&L carries its own summary sheet, the extracted lines are checked against it: a department total that disagrees with the statement's own summary by more than five percent is replaced with the summary value, and the original is kept beside it for review. A revenue line far below the deal's other full-period statements is held for review so a single outlet line can never pass as the department total."
          />
          <Stage
            n="5"
            title="Verify"
            Icon={Link2}
            body="A second pass re-reads each cited figure against the page it came from. A figure that matches earns a high confidence; one that does not is lowered so it shows as “to review”. The confidence badge beside each field is this result."
          />
          <Stage
            n="6"
            title="File the period"
            Icon={ArrowRight}
            body="The statement's own period — a full year, a year to date or a trailing twelve months — is read off the document, so a single-month upload never outranks an annual statement when the model chooses its Year-1 basis."
          />
        </Stages>
        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Jobs that stall are timed out, never left spinning</h4>
          <p className="text-[12.5px] text-ink-700 leading-relaxed">
            A document that stops moving for half an hour is marked Failed with the stage it stopped in and
            the time elapsed, and the Data Room offers Retry. Nothing is wrong with the file — the same bytes
            run again from the start. A retry on a document that is still moving is refused, so two runs
            never work on one file at once.
          </p>
        </Card>
        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Re-running extraction</h4>
          <p className="text-[12.5px] text-ink-700 leading-relaxed">
            Any finished document can be extracted again from the Data Room — one at a time, or every
            document on the deal. A confirmation step says what will happen first. The re-run is fresh under
            the current version of the pipeline. Field reviews made on the previous result (accepted, edited or
            rejected values) are not carried over, so anything the new result flags needs reviewing again; the
            previous result is kept as history. The document type and year you set are kept, and a disagreement
            with the new read is flagged for confirmation. Your model assumptions and their justification notes
            live on the deal and are untouched. When the last document finishes, the model runs once.
          </p>
        </Card>
        <Card className="p-5 mt-4" data-testid="methodology-duplicate-deal">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Duplicating a deal before re-testing</h4>
          <p className="text-[12.5px] text-ink-700 leading-relaxed">
            Because a re-extraction does not carry field reviews over, &ldquo;Duplicate deal&rdquo; in the deal header
            menu makes a working copy first. The copy carries the deal details, every assumption with its
            justification note, the scenarios, and every document with its latest extraction — including the
            fields you accepted, edited or rejected. The documents point at the same stored files; nothing is
            uploaded again. P&amp;L cell comments are copied only when you tick the box in the confirmation.
            Model runs and the activity history are not copied: the copy shows no results until the model is
            run on it. The original is not changed.
          </p>
        </Card>
      </Section>

      {/* ─── 2. How the Future P&L is built ───────────────────────── */}
      <Section
        id="projection"
        number="2"
        title="How the Future P&L is built"
        intro="Year 1 is anchored on the deal's own statements whenever they exist; later years grow from an undisplaced baseline, so a renovation year does not permanently depress the curve. The order of precedence for each input is below."
      >
        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Year-1 occupancy and ADR</h4>
          <Chain
            steps={[
              { label: 'Your override', desc: 'Set in the inline editor on the Overview or the P&L — wins over every other source and carries your justification note.' },
              { label: 'T-12 actual', desc: 'Read from the statement the Data Room badges as Primary source: full-year statements first, the most recent period first, a T-12 ahead of a calendar-year P&L of the same year.' },
              { label: 'Market forecast', desc: 'When the deal has no T-12 but a CBRE Hotel Horizons report is uploaded, its Year-1 forecast for the submarket and chain scale anchors the year.' },
              { label: 'Platform default', desc: 'A last-resort fallback used only on deals with no extracted data, badged as a default so it is never mistaken for a sourced figure.' },
            ]}
          />
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">The projection calendar</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            The model projects operating years — Year 1 through the end of the hold — and places them on
            the calendar from one date: the <strong>acquisition close date</strong>. Operating Year 1 is the
            calendar year of the first operating month after close, so a September close projects Year 1 in
            the same year and a December close projects it in the next. With no close date the statement
            prints no calendar year at all; a guessed year would be an invented number.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            The first column is labelled <strong>Base year (Year 1)</strong> because that is what it is: the
            model&apos;s first operating year, the year the returns and the Cash Flow statement call Year 1.
            The horizon is the hold period plus one <strong>Exit Year</strong> column, which carries only the
            forward twelve-month NOI the sale is valued on.
          </p>
        </Card>

        <Card className="p-5 mb-4" data-testid="methodology-line-methods">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Expense lines and their methods</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            Eight expense lines — Rooms, F&amp;B and Other Operated departmental expense, and Administrative
            &amp; General, Information &amp; Telecom, Sales &amp; Marketing, Property Operations and Utilities
            — can each be driven one of four ways from the method chip on their Future P&amp;L row:
            <strong> Growth</strong> (Year 1 grown each year), <strong>% of revenue</strong> (the department&apos;s
            own revenue for departmental lines, total revenue for undistributed lines), <strong>per occupied
            room</strong> and <strong>per available room</strong>. A method picked without a value keeps the
            line&apos;s own Year-1 ratio, so no benchmark figure is introduced. A line nobody has touched runs
            exactly as before. Every method change needs a justification note, and the Excel export lists each
            line&apos;s method and value on its Assumptions sheet.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            <strong>Operating model.</strong> The new-deal wizard records whether the hotel is owner-operated,
            third-party managed or brand-managed. It is shown on the Overview and beside the management fee. It
            moves no number: the management fee is yours to set, and the check against the usual two to six
            percent of revenue applies to every operating model.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3" data-testid="methodology-incentive-fee">
            <strong>Base and incentive management fee.</strong> The management fee is the base fee — a share of
            each year&apos;s total revenue. The incentive fee beside it is optional and blank by default. When
            set, each year&apos;s incentive fee is a share of gross operating profit after the base fee, never
            negative, deducted before NOI, so it lowers NOI and everything downstream. It appears as its own
            row only when set, and travels through the Excel export.
          </p>
          <div className="mt-3 rounded-md border border-border bg-ink-50/40 p-3" data-testid="methodology-mgmt-fee-guidance">
            <div className="text-[12px] font-semibold text-ink-900 mb-1.5">Management fee — directional guidance by operating model</div>
            <p className="text-[11.5px] text-ink-500 leading-relaxed mb-2">
              <em>{MGMT_FEE_GUIDANCE_DISCLAIMER}</em> The same text is shown beside the management fee on the Future P&amp;L.
            </p>
            <ul className="list-disc pl-5 space-y-1 text-[12px] text-ink-700 leading-relaxed">
              {OPERATING_MODEL_OPTIONS.map((o) => (
                <li key={o.id}>
                  <span className="font-medium text-ink-900">{o.label}.</span>{' '}
                  {MGMT_FEE_GUIDANCE_BY_MODEL[o.id]}
                </li>
              ))}
            </ul>
          </div>
        </Card>

        <Card className="p-5 mb-4" data-testid="methodology-future-pl">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Reading the Future P&L</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            The P&amp;L tab has two views: <strong>Historical P&amp;L</strong> — the normalised actuals, one
            column per statement — and <strong>Future P&amp;L</strong>, the forward plan the returns are built
            on. Every projected column is headed by its operating year and calendar year; the first column
            also names the statement period it is anchored on, exactly as the Historical P&amp;L heads that
            column.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            <strong>Click any number.</strong> Every projected occupancy, ADR, RevPAR and statement amount opens
            a trace of the inputs behind it — starting value, growth, index or penetration where present —
            down to the source rows. When no trace was recorded for a cell the drawer says so; Fondok never
            prints a formula it did not record.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3" data-testid="benchmark-column-methodology">
            <strong>Benchmark column.</strong> When the deal carries a P&amp;L benchmark report (CBRE or
            HotStats), the Base-year column gains a Benchmark sub-column showing the comparable set&apos;s
            ratio for thirteen lines on the same basis as the model&apos;s own ratio, and the difference in
            points. Colour marks direction only, never a verdict. A line the report did not publish shows a
            dash with the reason. The fuller comparison sits under the collapsed Benchmark detail below the
            table, and the Excel export carries the ratios as a read-only column.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3" data-testid="benchmark-suggestions-methodology">
            <strong>Benchmarks are suggestions.</strong> A benchmark never flows into the model on its own. For
            the Rooms and F&amp;B departmental ratios, the management fee and the FF&amp;E reserve, Fondok turns
            the report into a suggestion that names the value, the benchmark line, the peer-set size, the
            document and page, and the value the model is on now. <em>Apply benchmark</em> opens a prefilled
            justification note you can edit; saving writes an ordinary analyst override and re-runs the model.
            <em> Reset to source</em> removes it again. Until you act, each assumption keeps its existing
            source: your portfolio ratio when one is on the deal, else the platform default, labelled as such.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            <strong>The waterfall below Total Revenue</strong> runs gross operating profit, then management
            fees (and the incentive fee when one is modelled), then fixed charges — property taxes, insurance,
            rent, other — to <strong>NOI (before FF&amp;E reserve)</strong>, then the FF&amp;E reserve, to
            <strong> Cash NOI (after FF&amp;E reserve)</strong>. Each line is labelled with its basis, and the
            two NOI figures differ by exactly the reserve. Where the product says simply &ldquo;NOI&rdquo; it
            means the before-reserve figure, the numerator of the entry cap rate. The after-reserve figure is
            what debt coverage and the exit valuation use.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">The Stabilization Year</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            &ldquo;Stabilized&rdquo; names one projection year, and every stabilized figure on every screen —
            occupancy, ADR, revenue, NOI, NOI margin, yield on cost, the IC memo&apos;s scenario summary and
            Scenario Analysis&apos; comparison row — is read from that year, published once so two screens
            cannot disagree.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            The stabilization year defaults to <strong>Year 3 of operations</strong> (badged &ldquo;Default —
            confirm&rdquo;); on a shorter hold it is the last projected year, and the card says so. You can
            override it on the Future P&amp;L&apos;s Assumptions panel. It is a <strong>reporting</strong>
            assumption: it selects which year the stabilized figures are read from and moves no cash flow, so
            changing it leaves the sale price, both IRRs and the equity multiple unchanged.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3" data-testid="methodology-stabilization-anchor">
            <strong>What Year 3 counts from.</strong> With no reopening date the default counts from the
            acquisition close, and the Overview reads &ldquo;Year 3 after close&rdquo;. For a material
            repositioning or re-flag, set the <strong>reopening date</strong> on the Overview&apos;s Investment
            Profile card: the default then counts Year 3 from the first full operating year after reopening, and
            every stabilized figure says so (&ldquo;Year 3 after reopening&rdquo;). A reopening Fondok cannot
            place — no close date, or a Year 3 that falls before the projection starts — keeps the close
            anchor and states the exception in plain sight wherever the anchor is shown. Stabilized NOI stays
            a separate figure from the exit NOI the sale is valued on; the two are never printed under one
            label.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">What the exit is valued on</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            The sale capitalises the <strong>forward twelve-month Cash NOI</strong> — the after-reserve basis
            — at the exit cap rate. That forward figure is the last hold year&apos;s Cash NOI grown at the
            RevPAR growth rate; the Exit Year column shows this and nothing else. On CAPEX → Exit you can switch
            the basis to <strong>Stabilized NOI</strong>, and an analyst-entered terminal NOI beats either
            basis. Wherever the exit NOI is printed its label names the basis and period it was taken on.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            The entry cap rate is struck on NOI <em>before</em> the reserve while the exit is struck on Cash NOI
            <em> after</em> it. Both are defensible conventions; Fondok keeps both and names each one wherever it
            prints it, never a bare &ldquo;NOI&rdquo;.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Renovation and Year-1 displacement</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mb-2" data-testid="methodology-displacement-explicit-only">
            <strong>Only explicit assumptions displace Year 1.</strong> A renovation budget is a capital cost:
            it flows into Sources &amp; Uses and the equity requirement, and it never reduces Year-1 occupancy or
            ADR on its own. Year 1 is displaced only when you set an assumption on CAPEX → Initial Renovation:
            a Year-1 rooms-offline and rate displacement (each up to half, with a justification note), a
            renovation displacement schedule, or a structured closure schedule. All default to off, and no
            document sets them. Years the renovation does not fall in grow from the undisplaced baseline.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mb-2" data-testid="methodology-displacement-schedule">
            <strong>The renovation displacement schedule</strong> is a list of periods, each with a first and last
            month counted from close, the share of rooms offline and the rate discount on the rooms that keep
            selling. Fondok turns it into one occupancy factor and one ADR factor per model year — every month
            weighs one twelfth, rooms offline across overlapping periods add up, and the rate discount applies
            only to the rooms that actually sold. Six months at a quarter of rooms offline and ten percent off
            therefore give Year 1 occupancy times 0.875 and ADR times 0.957. When a schedule is set it is the only
            renovation impact applied, so nothing is counted twice.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed" data-testid="methodology-displacement-status">
            <strong>The model says what it decided.</strong> The CAPEX tab&apos;s renovation row and the Future
            P&amp;L&apos;s Year-1 basis line print the displacement the model actually applied — &ldquo;No Year-1
            renovation impact&rdquo;, or the percentages it used, or the schedule it ran — so a reader never has
            to infer it.
          </p>
        </Card>

        <Card className="p-5 mb-4" data-testid="methodology-renovation-budget-precedence">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Renovation budget — which document the model reads</h4>
          <Chain
            steps={[
              { label: 'Your override', desc: 'A budget you set — including one you selected with "Use this" on CAPEX → Renovation budget sources — wins, with its justification note.' },
              { label: 'PIP or capital plan — latest plan date', desc: 'A separate PIP, owner budget, approved capital plan or estimate filed in the PIP / Renovation Budget lane that states a total. It is the more detailed source, so it outranks the OM; among several, the newest plan date wins.' },
              { label: 'OM broker pro forma', desc: 'The renovation budget the offering memorandum states, when no PIP or capital plan does.' },
              { label: 'Platform default', desc: 'Used only when no document states a budget, with the reason shown. No budget, whatever its source, displaces Year 1 on its own.' },
            ]}
          />
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            Every candidate stays visible. CAPEX → Initial Renovation → <strong>Renovation budget sources</strong>
            lists the OM figure and each PIP or capital-plan figure with its page, plan date, status and preparer,
            marks the one the model is on, and says why each other one is not. A PIP whose stated total is more
            than two percent away from the sum of its own scope items is flagged for review; the value is never
            altered.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-2" data-testid="methodology-capex-lanes">
            <strong>Three CapEx lanes, one of which feeds the model.</strong> Every capital document is filed in
            a lane: <strong>PIP / Renovation Budget</strong> is the day-one plan and the only lane the renovation
            budget reads; <strong>Historic CapEx</strong> is spend already incurred; <strong>Future CapEx</strong>
            is a forward capital plan. Historic and Future CapEx are listed as &ldquo;Reference only — not in the
            model&rdquo; and never enter the renovation budget. A capital file dropped without a lane is filed
            under PIP / Renovation Budget so that is visible; move it to take it out.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Exit cap rate</h4>
          <Chain
            steps={[
              { label: 'Your override', desc: 'An inline-edited value wins.' },
              { label: 'OM comparable sales', desc: 'When the OM carries three or more comparable sales with cap rates in a sensible band, their median is the anchor.' },
              { label: 'Platform default of 7.0%', desc: 'Used when no comparable sales are extracted, badged as a default.' },
            ]}
          />
        </Card>

        <Card className="p-5">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Where expense lines come from</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            Departmental, undistributed and fixed-charge lines come from the T-12 first. A zero on the
            statement is treated as &ldquo;not stated&rdquo; rather than a fact, and a line the statement does
            not carry runs on a ratio: your override first, else your portfolio&apos;s ratio when one is on the
            deal, else the platform default for the hotel type. An uploaded benchmark report changes nothing by
            itself; its ratio becomes an assumption only when you apply it.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            When a precedence chain ends at a platform default, the default carries the reason — no document
            of the needed type on the deal, a document that does not state the figure, or market rates that could
            not be read — in the vocabulary of Section 7. Each grounded value carries the opposite record: the
            document, page and line behind it.
          </p>
        </Card>
      </Section>

      {/* ─── 3. Where market assumptions come from ────────────────── */}
      <Section
        id="sources"
        number="3"
        title="Where market assumptions come from"
        intro="Every assumption on the Overview carries a source badge saying where the value came from. The legend below explains each badge and the order of precedence between them."
      >
        <Card className="p-5 mb-4" data-testid="market-tab-methodology">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">The Market Comps tab: comp set, the trailing-twelve-month blend, demand and supply</h4>
          <p className="text-[12.5px] text-ink-600 leading-relaxed">
            <strong className="text-ink-700">One comp set, one derivation.</strong> The hotel count and key
            count on the Market Comps tab come from the same set: the active hotels on the STR roster, combined
            across every STR report on the deal. A hotel is treated as closed only when a report says so
            explicitly; closed hotels are listed with a &ldquo;closed · excluded&rdquo; chip and are left out of
            both the count and the keys. Which reports count as STR is decided by the lane each document was
            processed in, not by its tag, so a market report filed under STR is read as a market study and never
            feeds the comp set.
          </p>
          <p className="text-[12.5px] text-ink-600 leading-relaxed mt-3">
            <strong className="text-ink-700">&ldquo;TTM · comp-set blend&rdquo; is defined on the card.</strong>
            Market occupancy, ADR and RevPAR are the comp set&apos;s blended trailing-twelve-month figures,
            recovered exactly from the STR penetration indices: comp-set occupancy is the subject&apos;s
            occupancy divided by its occupancy index, and likewise for ADR and RevPAR. The card prints a
            methodology line composed from the inputs actually used — the reports, the period and the active
            hotels. A metric whose index is missing shows a dash rather than a substitute.
          </p>
          <p className="text-[12.5px] text-ink-600 leading-relaxed mt-3">
            <strong className="text-ink-700">Demand growth and supply growth read the market study.</strong> A
            submarket report feeds both tiles, and every figure names its document and page. Demand growth is
            the report&apos;s own occupied-room-night growth. Under-construction rooms are shown as a share of
            existing inventory, with the report&apos;s own supply-change figure shown alongside and never
            substituted. A tile the reports cannot populate shows a dash with its reason — no market study on
            the deal, or a report that does not carry the series. No growth rate is ever assumed.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[12.5px]">
            <BadgeRow source="t12_actual" name="T-12 Actual">
              Year-1 anchor read from the deal&apos;s uploaded T-12. Later years grow forward at the configured
              revenue and expense growth rates.
            </BadgeRow>
            <BadgeRow source="cbre_horizons" name="CBRE Horizons">
              Forecast curve read from an uploaded CBRE Hotel Horizons report for the subject&apos;s submarket
              and chain scale.
            </BadgeRow>
            <BadgeRow source="om_comps" name="OM Comps">
              Median cap rate derived from the OM&apos;s comparable-sales table.
            </BadgeRow>
            <BadgeRow source="om_broker" name="OM Broker">
              A broker pro forma value read from the OM. Treat with appropriate scepticism — these are the
              broker&apos;s pitched numbers.
            </BadgeRow>
            <BadgeRow source="pnl_benchmark" name="P&L Benchmark">
              An industry benchmark ratio from a CBRE or HotStats report. The model no longer applies these on
              its own; an applied suggestion shows as Analyst Override with its note. The badge can still appear
              on older runs.
            </BadgeRow>
            <BadgeRow source="analyst_override" name="Analyst Override">
              Set through an inline editor and carrying your written justification. Wins over every other
              source — but only a value that actually changed is recorded as one; see &ldquo;What counts as an
              override&rdquo; below.
            </BadgeRow>
            <BadgeRow source="str_subject_ttm" name="STR · Subject TTM Actual">
              The subject property&apos;s own trailing-twelve-month occupancy and ADR as reported by STR — an
              actual, not a forecast. This is what the Base Year is seeded from when the STR basis is on.
            </BadgeRow>
            <BadgeRow source="str_comp_set" name="STR Comp-Set Rates">
              The comp set&apos;s blended occupancy and ADR shown on the Market Comps tab, applied as the Year-1 input
              by &ldquo;Use STR rates in the model&rdquo;. This is the competitive set&apos;s performance, not the
              subject&apos;s own, and the Market Comps tab shows the two as separate figures.
            </BadgeRow>
            <BadgeRow source="str_forecast" name="STR Trend Forecast">
              The forward-forecast scenario&apos;s month-twelve point — a projection of the subject, used only
              when the subject&apos;s own trailing twelve months is not extracted. The P&amp;L shows the active
              basis and a one-click Revert whenever rates carry any STR tag.
            </BadgeRow>
            <BadgeRow source="str_forecast_unavailable" name="STR Unavailable">
              STR rates were requested but could not populate. The model stays on the T-12 base and says so —
              an STR basis is never silently &ldquo;active&rdquo;.
            </BadgeRow>
            <BadgeRow source="derived_from_revpar_growth" name="Derived from RevPAR Growth">
              A RevPAR-growth override derives ADR growth with the occupancy path held, so the lever moves
              operating NOI. Setting ADR growth explicitly takes direct control.
            </BadgeRow>
            <BadgeRow source="index_assumption" name="Index Analysis Assumption">
              Index Analysis indexes the subject against one of three benchmarks, chosen on Market Comps → Index
              Analysis: the <b>STR competitive set</b> (the default), a <b>market or chain-scale benchmark</b>
              from the deal&apos;s market reports, or a <b>CoStar comp set</b> when that export has been
              extracted. Four assumptions ride on the choice — market occupancy growth, market ADR growth, and
              the occupancy and rate penetration targets — each showing its document source or &ldquo;Your
              override&rdquo;. They feed the Future P&amp;L only while &ldquo;Use STR rates in the model&rdquo; is
              on; with the toggle off nothing moves.
              <br />
              <b>Targets by year.</b> Penetration targets can be set per model year; a by-year entry beats the
              all-years target. The all-years target drives Year 1 only — later years are indexed only where a
              by-year target is set or you override that metric&apos;s market growth; otherwise the year follows
              the model&apos;s own growth and is flagged as applying the all-years target to Year 1 only. A year
              with a target but no benchmark value is flagged as benchmark unavailable: the model keeps its own
              growth and no figure is invented.
              <br />
              <b>Target units.</b> A target is index points from 25 to 250 (100 is parity) or a ratio from 0.25
              to 2.5 (1.0 is parity); the two bands do not overlap, so a value names its own unit. A stored target
              outside both bands is ignored by the model and the panel says so, with a prompt to re-enter it.
            </BadgeRow>
            <BadgeRow source="index_assumption" name="Index Analysis · Columns &amp; Rows">
              The Index Analysis year columns follow the acquisition close calendar — the same calendar the
              Future P&amp;L prints. The six columns before Year 1 are history; the forecast columns are exactly
              the model horizon, and nothing is carried beyond it. Each benchmark value names its kind with a
              worded chip — <b>Actual</b> with its period and document, <b>Forecast</b> with the report and year,
              or <b>Assumption</b> for a comp set grown at market growth — never colour alone. STR publishes no
              comp-set forecast, so comp-set out-years are always an assumption and say so. Penetration rows
              print a target only when the subject rows actually run on it; otherwise the published index is held
              flat with a note. Every cell shows its formula and inputs on hover; a cell with no reported input is
              a dash.
            </BadgeRow>
            <BadgeRow source="seed" name="Platform Default">
              A Fondok default used because no deal document has set the value yet, shown in grey. The senior
              origination fee (1.50% of the senior loan) is one of these: editable on Financing, and the single
              source for the Sources &amp; Uses origination-fee line and the Overview&apos;s Financing Costs.
            </BadgeRow>
            <BadgeRow source="deal_row" name="Deal Details">
              Entered on the new-deal wizard or the Overview: project name, city, brand, keys,
              the reopening date — and the <strong>purchase price</strong>. The wizard&apos;s price field is read
              to whole dollars (&ldquo;$36.4M&rdquo;, &ldquo;36,400,000&rdquo;; a range stores its midpoint and is
              labelled as such). Text that does not read as a price is kept as a note and sends nothing — the
              model keeps its default until you set the price on CAPEX → Deal Summary.
            </BadgeRow>
          </div>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">What counts as an override</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            Opening a field to inspect it never changes its source. An override is recorded only when the saved
            value <span className="font-semibold text-ink-900">differs from the current effective value</span> —
            compared in the field&apos;s own unit, so &ldquo;6.80&rdquo; typed over a stored 6.8% writes
            nothing. Every editor exits without writing on Cancel, Escape or a click outside, and a Save that
            changed nothing says so. The same rule holds for overrides already stored: a value equal to what the
            deal resolves to on its own is reported under its source&apos;s badge, not as an override. Use
            <b> Clear override</b> in the source popover to remove one.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Every override carries a justification</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            An override that <span className="font-semibold text-ink-900">changes a number the model runs
            on</span> carries your written justification, required at the moment of the edit and shown to
            reviewers beside the number and in the activity trail. The rule is enforced on the server as well as
            in the editor, so no path — screen, spreadsheet import or integration — can record an unexplained
            number. The justification is never written for you.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-2">
            <span className="font-semibold text-ink-900">What is exempt, and why.</span> Edits that change no
            modelled number need no note: the Historical P&amp;L&apos;s row layout, the Stabilization Year, the
            Property Name, the completion-guarantee status, the number of promote tiers, and the IC memo&apos;s
            prose. Clearing an override is a return to source and needs no note either.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-2">
            <span className="font-semibold text-ink-900">Target levered IRR, target equity multiple and target
            stabilized yield on cost are not overrides.</span> They are stated objectives — the benchmark the
            returns are read against and the hurdle the Max Price Solver prices to — not replacements for a
            value Fondok read from a document, so they are editable without a justification.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Project Name and Property Name</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            <span className="font-semibold text-ink-900">Project Name</span> is your confidential deal
            identifier — set and renamed on the Overview; a document never writes it.
            <span className="font-semibold text-ink-900"> Property Name</span> is the asset as named in the
            offering documents and is never inferred from the Project Name; it shows a dash until the OM is
            extracted. You may override the Property Name from its Overview row; the extracted value and its
            source page are kept, and &ldquo;Restore sourced value&rdquo; brings them back.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Which documents set the deal&apos;s key count, brand and city</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            Only documents about the subject property — the Offering Memorandum, a T-12, P&amp;Ls, a hotel
            program or a property information sheet — can set the key count, and when more than one states a room
            count the Offering Memorandum wins. The existing brand comes from the OM and fills a blank only; the
            proposed brand is yours and is never touched by a document. Market reports, STR reports, comparable
            sets, capital budgets, insurance documents and leases describe other things and never change the key
            count, brand or city. Until a qualifying document is extracted the deal header shows a dash for keys,
            never zero.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3" data-testid="brand-positioning-methodology">
            <strong>Brand and positioning.</strong> Positioning is a deal setting, chosen on the wizard&apos;s
            Brand and Positioning step and editable on the Overview. Choosing a brand <em>suggests</em> that
            brand&apos;s STR chain scale wherever STR lists it; a suggestion is only that, and the tier changes
            only when you select one. When an Offering Memorandum is extracted and the deal has no tier, a
            confident match on the brand the OM names sets the positioning, and the Overview says where the tier
            came from — &ldquo;From OM brand&rdquo; with the document and page, or &ldquo;Analyst&rdquo; for a tier
            you chose. A tier you chose is never replaced by an OM. The stored tier is what the model reads for
            the comparable-sales match and for which portfolio ratios may contribute.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3" data-testid="property-type-methodology">
            <strong>Property type.</strong> A deal&apos;s property type is one of five: Full-Service,
            Select-Service, Limited-Service, Extended-Stay or Resort. It is read from the Offering Memorandum
            only, and only when the OM states it plainly (&ldquo;boutique lifestyle full-service&rdquo; reads as
            Full-Service; &ldquo;luxury&rdquo; on its own names a positioning, not a type, and sets nothing).
            Nothing is assumed when the OM is silent. The comparable-sales match uses the positioning tier; only
            a deal with no tier falls back to its property type for an approximate scale &mdash; Full-Service and
            Resort as Upper Upscale, Select-Service as Upper Midscale, Limited-Service as Midscale. Extended-Stay
            spans several scales, so it sets no fallback.
          </p>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3" data-testid="market-submarket-methodology">
            <strong>City, market and submarket.</strong> These are three separate deal fields. City is the
            property&apos;s city, filled from the OM only while empty. Market and Submarket are your choice on
            the wizard&apos;s first step and correctable on the Overview; no document writes them and no model
            reads them, so changing them never re-runs the model. The Market field suggests STR-defined market
            names; typing a market or submarket that is not suggested is always allowed, and Fondok never invents
            an STR identifier or a submarket name.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Which statement is the Year-1 basis</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            Year-1 occupancy, ADR, revenue lines and expense lines all come from one statement: the document
            the Data Room badges as <span className="font-semibold text-ink-900">Primary source</span>. The
            model and the badge share one ranking — full-year statements before partial ones, the most recent
            period first, a T-12 ahead of a calendar-year P&amp;L of the same year. Older annual P&amp;Ls never
            blend into Year 1: a line is corroborated only across statements that describe the same period, so
            every Year-1 number traces to a single row of the primary statement. Earlier years still feed the
            Historical P&amp;L and the variance checks.
          </p>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">Hover any number to trace it</h4>
          <ul className="space-y-2.5 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Inputs — &ldquo;where did this come from?&rdquo;</span>{' '}
              Assumptions show a source badge coloured by origin: green when grounded in the deal&apos;s own
              documents, amber when still a default or benchmark, violet when you overrode it. A badge backed by
              a document is clickable and jumps to that source in the Data Room.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Outputs — &ldquo;how was this computed?&rdquo;</span>{' '}
              Modelled values — revenue, NOI, debt service, DSCR, equity multiple, sale price, IRR — show their
              formula and every named input on hover, and each input chains one hop further, back to a document, a
              default, an override or another computed value.
            </li>
            <li>
              <span className="font-semibold text-ink-900">NOI pin.</span>{' '}
              A deal can carry an analyst-entered NOI schedule used to reconcile to a source model. While it is
              set, the Debt and Returns models read that schedule instead of the operating model, so operating
              edits do not move NOI; the Future P&amp;L shows an &ldquo;NOI pinned&rdquo; notice with a one-click
              clear, after which NOI follows the operating assumptions again.
            </li>
            <li>
              <span className="font-semibold text-ink-900">IRR is calculated, and says so.</span>{' '}
              IRR is the discount rate that sets the equity cash flows to zero, solved iteratively. Its hover
              says so and lists the year-by-year cash flows the solver ran over.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Total Revenue is the statement&apos;s own line.</span>{' '}
              On the Historical P&amp;L the Total Revenue row shows the statement&apos;s stated total when it
              publishes one, and the sum of the extracted lines — labelled calculated — only when it does not.
              The Revenue footing row under the grid always compares the two and shows the difference; no total
              is invented when neither exists.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Every historical column states its period.</span>{' '}
              A statement is read as a full year, a year to date through a month end, or a trailing twelve months
              ending at a month end, and the column header says which. When the basis cannot be established the
              column shows the bare year with a &ldquo;basis unknown&rdquo; chip, never an invented period.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Cross-checked across statements.</span>{' '}
              When more than one full-year statement covers the same period, each revenue and expense line is
              cross-checked and Year 1 is grounded on the corroborated value, so one misread line cannot skew
              the model. A line reported by only one statement is used as-is.
            </li>
            <li>
              <span className="font-semibold text-ink-900">One review state, Data Room and P&amp;L.</span>{' '}
              A value is &ldquo;to review&rdquo; when its extraction confidence is below the threshold and it has
              not been accepted or corrected. The Data Room&apos;s per-statement count is exactly the number of
              flagged cells in that statement&apos;s column; accepting or correcting a value clears both at once.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mb-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">As-of rule</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            An underwriting is a claim about what was knowable on a date. When a deal sets an underwriting as-of
            date, Fondok will not ground a market assumption on a report published after it: the assumption
            keeps the basis it already had and the badge says unavailable with the reason, never a silent
            substitution. A report whose date cannot be established is used and flagged — a missing date is a
            caveat, not grounds to withhold a number. The same date anchors the comparable-sales lookback, so
            re-opening a deal months later does not quietly age its comp set out of the window.
          </p>
        </Card>

        <Card className="p-5">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-3">The full ledger</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            The Sources ledger, linked from every traced figure&apos;s tooltip, lists every modelled assumption,
            its value and its origin in one table. Filter to the ungrounded set to see exactly what is still
            riding on a default, and export the ledger so every figure in the IC memo is defensible.
          </p>
        </Card>
      </Section>

      {/* ─── 4. How the model is organised ────────────────────────── */}
      <Section
        id="engines"
        number="4"
        title="How the model is organised"
        intro="Eight deterministic models run in dependency order and publish their results together, so every tab reads one consistent snapshot."
      >
        <Card className="p-5">
          <ul className="space-y-2.5 text-[12.5px] text-ink-700">
            {[
              ['Revenue', 'Rooms × occupancy × ADR, plus F&B, other operated departments, resort fees and miscellaneous income.'],
              ['F&B', 'A per-occupied-room F&B model with a food and beverage split; resort fees are a separate line.'],
              ['Expense', 'USALI departmental and undistributed lines, management fee, FF&E reserve and fixed charges, to gross operating profit, NOI and net cash flow.'],
              ['Capital', 'Purchase price, closing costs, renovation budget and working capital, plus the senior origination fee owned by the Financing tab — the Sources & Uses.'],
              ['Debt', 'The senior and PACE tranches from your entered terms — fixed or floating, amortising or interest-only, maturity — with the monthly schedule, DSCR, debt yield, LTV and LTC, covenants and refinancing.'],
              ['Returns', 'Levered and unlevered IRR, equity multiple, cash-on-cash, and the sale value from the exit cap rate and exit NOI. A loss-making deal is reported honestly — a negative IRR or a multiple below one is never floored.'],
              ['Sensitivity', 'IRR and equity multiple across exit cap, hold, growth and other pairs.'],
              ['Partnership', 'The GP / LP waterfall with preferred return, catch-up and promote tiers. A shortfall is funded as a dated pro-rata capital call that adds to unreturned capital, and is reported as additional contributions.'],
            ].map(([name, desc]) => (
              <li key={name} className="flex items-start gap-2">
                <span className="font-semibold text-ink-900 min-w-[88px]">{name}</span>
                <span className="text-ink-500">{desc}</span>
              </li>
            ))}
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Financing is an assumptions workspace</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mb-3">
            Debt documents are optional. The Financing tab is where the financing is entered, and every core
            term is your input — Fondok does not read a term sheet in this release. Each edit is saved as an
            override <AssumptionBadge source="analyst_override" /> and re-runs the chain, so the schedule, DSCR,
            debt yield, Cash Flow and Returns reflect what was entered. A year whose NOI is negative does not stop
            the model: its DSCR shows as not applicable, the uncovered debt service is reported as that
            year&apos;s shortfall, and the tab names the negative years in one warning.
          </p>
          <ul className="space-y-2 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Senior loan.</span>{' '}
              Amount or LTV (either resizes the same loan), rate basis, amortisation, an interest-only period,
              and maturity. A floating rate prices off the index assumption plus spread, clamped to an optional
              floor and cap; until an index is entered the build-up shows Fondok&apos;s flat index assumption and
              says it is not market data.
            </li>
            <li>
              <span className="font-semibold text-ink-900">PACE loan.</span>{' '}
              Funding an amount adds it to total debt, LTV, LTC and debt yield immediately. Until a rate is
              entered the tranche is <em>terms pending</em>: it stays out of debt service and DSCR rather than
              running on an invented rate.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Covenants.</span>{' '}
              Maximum LTV and LTC and minimum DSCR and debt yield are thresholds you enter. There is no default
              package — a covenant without a threshold shows its live reading, an input, and no pass or fail
              verdict.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Senior origination fee.</span>{' '}
              A platform default of 1.50% of the senior loan <AssumptionBadge source="seed" /> until you change
              it. Financing is its only owner: Sources &amp; Uses, total uses, required equity and LTC all move
              with it, and at zero the line disappears.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Missing inputs are inputs.</span>{' '}
              Wherever a required assumption is absent, the tab renders the input to provide — &ldquo;Enter
              rate&rdquo;, &ldquo;Enter spread&rdquo;, &ldquo;Enter threshold&rdquo; — with the consequence stated,
              instead of an unexplained dash.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">The Returns sandbox</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed mb-3">
            Overview → Returns → Sensitivities carries a <span className="font-medium">Live Assumptions</span>
            card whose sliders are a sandbox, not an edit. It answers &ldquo;what would this deal do if…&rdquo;
            without changing what the deal <em>is</em>.
          </p>
          <ul className="space-y-2 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Five assumptions, and only five.</span>{' '}
              Exit cap rate, RevPAR growth, hold period, LTV and interest rate. Nothing else can be reached from
              the sandbox.
            </li>
            <li>
              <span className="font-semibold text-ink-900">The sliders start where the run started.</span>{' '}
              Each slider&apos;s base is the value the saved run recorded, never a default typed into the screen.
              A run that did not record one of the five shows &ldquo;Awaiting model run&rdquo; in place of the
              sliders.
            </li>
            <li>
              <span className="font-semibold text-ink-900">It runs the whole chain, in memory.</span>{' '}
              Nothing is written: no run, no change to the deal&apos;s assumptions. CAPEX and Financing keep
              showing the Base Case throughout. While the sandbox differs from the Base Case a banner spans the
              Returns section and every tile that moved carries a Sandbox chip.
            </li>
            <li>
              <span className="font-semibold text-ink-900">It survives navigation, and only that.</span>{' '}
              Held in your browser session, per deal. Reset to base case, opening a different deal or a fresh
              model run clears it. It is never shared, bookmarked or seen by anyone else, and the Max Price
              Solver and the pricing grid do not consume it.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">When a model cannot finish</h4>
          <p className="text-[12.5px] text-ink-500 leading-relaxed">
            The models are independent, so one can fail without taking down the rest. When one does, the deal
            shows a red banner naming the model, a plain-language reason and a one-click Re-run; the numbers it
            feeds read as an explicit error, never as silent dashes that look like missing data. When a
            deal&apos;s saved run is older than the current version of Fondok, an amber banner says so and
            names the tabs affected, with the same Re-run.
          </p>
        </Card>
      </Section>

      {/* ─── 5. Return targets and pricing ────────────────────────── */}
      <Section
        id="pricing"
        number="5"
        title="Return targets and the Max Price Solver"
        intro="The hurdles a deal must clear are yours, set once on Overview → Investment Profile. Pricing reads them from the deal — there is no hidden default hurdle anywhere in the pricing path."
      >
        <Card className="p-5">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">The Investment Profile is the source of truth</h4>
          <ul className="space-y-2 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Two pricing hurdles.</span>{' '}
              <span className="font-medium">Target levered IRR</span> and <span className="font-medium">target
              equity multiple</span> are stored on the deal as your inputs <AssumptionBadge source="analyst_override" />.
              Either may be unset; an unset target renders as a dash.
            </li>
            <li>
              <span className="font-semibold text-ink-900">The return profile is a preset.</span>{' '}
              The target levered IRR control carries a preset for each return profile — Core 5–10%, Core Plus
              7–12%, Value Add 11–17%, Opportunistic 18%+. Choosing one writes the band&apos;s midpoint as the
              target (an open band writes its floor) — an explicit action, never applied implicitly. You can then
              edit the number.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Benchmark only.</span>{' '}
              Saving a target never re-runs the model. The Overview compares the calculated levered IRR against
              the target: below it is <em>Below target</em>, at or up to two points over is <em>Within target</em>,
              and more than two points over is <em>Above target</em>. Display only.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Target stabilized yield on cost.</span>{' '}
              Compared against the stabilized year&apos;s NOI divided by total capital, both from the model run —
              Pass at or above the target, Short below it. With no stabilized year or no total capital the actual
              is a dash, never estimated.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Max Price Solver (Overview → Returns → Pricing)</h4>
          <ul className="space-y-2 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">No silent hurdles.</span>{' '}
              If the deal carries no target, the Pricing block says so with a link to the Investment Profile and
              shows no numbers.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Lower-of rule.</span>{' '}
              Each hurdle is solved independently by searching on purchase price with the loan amount held fixed
              and equity flexing, between half and twice the current basis. Max Price is the lower of the two
              solved prices, and the binding constraint is named. With one target set, that hurdle governs alone.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Unreachable hurdles stay unreachable.</span>{' '}
              A hurdle no price down to half the basis can clear reports &ldquo;no price clears the hurdles&rdquo;;
              one that clears even at twice the basis reports that instead. The search boundary is never shown as
              a price.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Pricing sensitivity grid.</span>{' '}
              The solver re-runs per cell over exit cap rate and RevPAR growth around the base assumptions. Each
              cell shows the maximum purchase price clearing both hurdles with its binding constraint, and the
              base cell reproduces the headline solve exactly.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Known gap — exports.</span>{' '}
              The Excel model&apos;s max-price section still prints the legacy hurdles rather than the
              deal&apos;s targets. The on-screen solver is authoritative.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Asking versus bidding price (Overview KPIs)</h4>
          <ul className="space-y-2 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Two comparable sets.</span>{' '}
              On an acquisition the Overview shows the same metrics twice — purchase price, total capitalisation,
              renovation, stabilized NOI, levered and unlevered IRR — once at the <em>asking</em> price and once at
              the <em>bidding</em> price. Nothing is saved.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Asking price.</span>{' '}
              The OM&apos;s asking price as extracted. With no OM price the modelled purchase price stands in and
              is labelled with its source.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Bidding price — solved backward from the target.</span>{' '}
              The Max Price Solver run on the IRR hurdle alone: the purchase price at which the levered IRR equals
              your target, with the loan held and equity absorbing the difference. With no target the bidding set
              reads &ldquo;Set target levered IRR to solve&rdquo; and shows no numbers.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Investment Bridge</h4>
          <ul className="space-y-2 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Equity invested to equity returned.</span>{' '}
              A waterfall from the equity funded at close to the total cash returned to equity, deal-level before
              the GP / LP split, read from the Base Case run.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Five legs.</span>{' '}
              <em>Acquisition</em> is every use except renovation and the loan fee; <em>Renovation</em> is the
              renovation line including contingency; <em>Operations</em> is NOI over the hold; <em>Financing</em>
              is loan proceeds less the origination fee, debt service and the payoff at exit, plus any refinancing
              proceeds; <em>Exit</em> is the sale price less selling costs and transfer tax.
            </li>
            <li>
              <span className="font-semibold text-ink-900">It foots by construction.</span>{' '}
              Equity invested plus the five legs equals equity returned exactly, and the tab states the check. A
              leg the run cannot support shows a dash with the reason, never a zero bar, and the bridge then says it
              does not foot.
            </li>
          </ul>
        </Card>
      </Section>

      {/* ─── 6. IC Memo ───────────────────────────────────────────── */}
      <Section
        id="ic-memo"
        number="6"
        title="IC Memo — diligence flags and the decision"
        intro="How broker-versus-actual variance flags reach the committee, what an NOI impact on a flag means, how diligence status is kept, and why the recommendation is a recorded decision rather than a model inference."
      >
        <Card className="p-5">
          <ul className="space-y-2.5 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">One flag per business concept.</span>{' '}
              The variance report groups the broker&apos;s claims by concept — rooms revenue, F&amp;B revenue,
              total revenue, occupancy, ADR, RevPAR, gross operating profit, NOI and each expense line — and the
              memo lists one item per concept with a readable title (&ldquo;Rooms revenue — broker overstates
              T-12 by 5.3%&rdquo;). The underlying rows stay available under Technical detail on each item and in
              the exported variance sheet.
            </li>
            <li>
              <span className="font-semibold text-ink-900">&ldquo;Estimated NOI impact&rdquo; only when the delta is an NOI delta.</span>{' '}
              A dollar NOI impact is shown only for the NOI and gross-operating-profit concepts. A revenue,
              KPI or expense variance says that its NOI impact is not estimated rather than showing a dollar
              figure — Fondok does not translate a revenue delta into NOI without a flow-through assumption.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Diligence status is kept per concept.</span>{' '}
              Resolve, Accept variance and Reopen are recorded on the deal, so the IC-readiness checklist and the
              Excel export read the same status after a reload. Fondok never resolves a flag on its own.
            </li>
            <li>
              <span className="font-semibold text-ink-900">The recommendation is a decision, not an inference.</span>{' '}
              The Model Assessment card shows the model&apos;s own read of the Base Case — Clears Hurdles, Clears
              with Conditions or Below Hurdles — labelled as the model&apos;s. The IC recommendation reads
              <em> Pending analyst decision</em> until you select Proceed, Proceed with Conditions or Do Not Proceed
              <em> and</em> confirm it. Only the confirmed verdict is written into the memo and the export header.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">What a variance flag compares — and what it refuses to</h4>
          <ul className="space-y-2.5 text-[12.5px] text-ink-600 leading-relaxed">
            <li>
              <span className="font-semibold text-ink-900">Only the broker&apos;s own claim is the broker side.</span>{' '}
              A figure from a T-12 or P&amp;L is an actual, never a broker figure; a reading from an STR or market
              report is reported performance, not the broker&apos;s claim; a competitive-set statistic or the
              OM&apos;s historical block is neither. Rows excluded for one of these reasons are still listed under
              Technical detail with the reason.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Units are normalised before comparing.</span>{' '}
              Ratios are compared as fractions and currency in whole dollars. A value whose unit cannot be
              established is not compared, and a percentage claim is never read against a dollar line — Fondok
              does not reconstruct dollars from a percent whose denominator is itself an unverified broker figure.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Annual against annual.</span>{' '}
              The actual side is the document&apos;s annual or trailing-twelve-month line, never a monthly or
              quarterly slice, and the T-12 is preferred over a supporting P&amp;L. Fondok never compares a month
              against a year.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Plausibility guard.</span>{' '}
              A pair of figures more than three hundred percent apart is reported as &ldquo;Basis mismatch — needs
              review&rdquo; with both figures and no severity: two numbers that far apart are on different bases,
              not evidence of an overstatement.
            </li>
            <li>
              <span className="font-semibold text-ink-900">NOI is compared like for like across the FF&amp;E reserve.</span>{' '}
              A broker pro forma states NOI before the reserve while the normalised actual is after it. When the
              statement states its own before-reserve line, the broker&apos;s claim is compared against that one
              and the row says so; when it does not, the pair is refused as a basis mismatch. The before-reserve
              figure is only ever read from the document, never reconstructed.
            </li>
          </ul>
        </Card>
      </Section>

      {/* ─── 7. What a dash means ─────────────────────────────────── */}
      <Section
        id="reasons"
        number="7"
        title="What a dash means"
        intro="Where Fondok cannot produce a figure it shows a dash — never a placeholder, a zero, or a number carried over from a prototype. Every such refusal has one of the reasons below, and hovering the dash shows it."
      >
        <Card className="p-0 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-[12.5px]" data-testid="reason-code-table">
              <thead>
                <tr className="text-ink-500 text-[10.5px] border-b border-border bg-ink-300/5">
                  <th className="text-left font-medium px-5 py-2">Shown</th>
                  <th className="text-left font-medium px-3 py-2">Meaning</th>
                  <th className="text-left font-medium px-3 py-2">What it tells you</th>
                </tr>
              </thead>
              <tbody>
                {REASON_CODES.map((code: ReasonCode) => {
                  const meta = REASONS[code];
                  return (
                    <tr
                      key={code}
                      data-reason-code={code}
                      className="border-b border-border last:border-b-0 align-top"
                    >
                      <td className="px-5 py-2.5 text-ink-900 tabular-nums">{meta.ui}</td>
                      <td className="px-3 py-2.5 font-medium text-ink-900">{meta.label}</td>
                      <td className="px-3 py-2.5 text-ink-500 leading-relaxed">{clientReasonExplanation(code)}</td>
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
              Every reason renders the same dash; the hover text is what distinguishes them.
            </li>
            <li>
              <span className="font-semibold text-ink-900">Where it lands on the Data Key.</span>{' '}
              &ldquo;Needs review&rdquo;, &ldquo;Basis mismatch&rdquo;, &ldquo;Period mismatch&rdquo; and
              &ldquo;Unit unknown&rdquo; describe a figure that exists but cannot be trusted or compared, so they
              read as <em>Needs review</em>. Every other reason means the figure is not there yet and reads as
              <em> Awaiting data</em>.
            </li>
            <li>
              <span className="font-semibold text-ink-900">A dash is not an error.</span>{' '}
              A model that could not finish shows the red banner described in Section 4, with a Re-run. A dash is
              the model declining to invent a number it has no grounds for — the reason says which grounds are
              missing.
            </li>
          </ul>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">Structure edits change how a statement reads, never what it computes</h4>
          <p className="text-[12.5px] text-ink-600 leading-relaxed">
            On the Historical P&amp;L, <strong className="text-ink-700">Customize structure</strong> lets you
            relabel a line, split one line into parts that must reconcile to the locked parent, reorder lines
            within their section, hide a line, and add a line of your own. All five are presentation: the value
            behind each row, the document it was read from and its Data Key state are untouched, and no model
            reads an added line. The layout is stored on the deal, so every reviewer reads the statement as you
            arranged it, and <strong className="text-ink-700">Reset layout</strong> restores the canonical
            statement.
          </p>
        </Card>
      </Section>

      {/* ─── 8. Every number traces back ──────────────────────────── */}
      <Section
        id="lineage"
        number="8"
        title="Every number traces back"
        intro="Pick any headline number on a deal and Fondok can show the chain that produced it, one step at a time, down to the page of the document it was read from — or state, in the vocabulary of Section 7, exactly why the chain stops short."
      >
        <Card className="p-5">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">The walk</h4>
          <Chain
            steps={[
              { label: 'Headline number', desc: 'levered IRR, unlevered IRR, equity multiple, Year-1 cash-on-cash, minimum DSCR, Year-1 NOI. These are the roots you can start from.' },
              { label: 'Modelled value', desc: 'the value behind it, and each value behind that — IRR to the cash flows, cash flows to NOI and debt service, NOI to gross operating profit, profit to revenue, revenue to rooms revenue.' },
              { label: 'Assumption', desc: 'the underwriting input the calculation rests on — starting occupancy, starting ADR, exit cap rate, LTV — carrying the source that produced it.' },
              { label: 'Extracted field', desc: 'the exact field on the exact extraction that supplied the assumption, with the concept it resolved to and the basis and period it carries.' },
              { label: 'Document and page', desc: 'the uploaded document, and the page the number sits on. This is where a walk that closes ends.' },
            ]}
          />
          <p className="text-[12.5px] text-ink-500 leading-relaxed mt-3">
            Three chains end deliberately short of a page, and each says so. Your <strong className="text-ink-700">override</strong>
            ends at the override, carrying your justification. A <strong className="text-ink-700">platform default</strong>
            ends at the default. A <strong className="text-ink-700">benchmark</strong> — a market forecast, an
            industry data set, your own portfolio, an STR feed — ends at the benchmark, and continues to a page only
            when the benchmark itself arrived as an uploaded document. Each link also says whether the model
            asserted it or Fondok inferred it from the dependency between models.
          </p>
        </Card>

        <Card className="p-5 mt-4">
          <h4 className="text-[13px] font-semibold text-ink-900 mb-2">What &ldquo;stale&rdquo; means</h4>
          <p className="text-[12.5px] text-ink-600 leading-relaxed">
            A chain describes one model run, so it is only as current as that run&apos;s inputs. When a document
            is uploaded, the deal record edited, or a document the run relied on deleted <em>after</em> the run
            started, the chain is marked stale. Stale does not mean wrong and it does not hide the chain: the
            evidence shown is still exactly what produced the numbers on screen. It means the deal has moved
            since, and re-running the model will produce a different chain.
          </p>
        </Card>
      </Section>

      {/* ─── 9. Comparing model runs ──────────────────────────────── */}
      <Section
        id="regression-baselines"
        number="9"
        title="Telling an intended change from a calculation drift"
        intro="Every model run is kept, so the numbers a deal showed before a change can always be put next to the numbers it shows after — field by field, with the source of each side."
      >
        <Card className="p-5" data-testid="methodology-regression-baselines">
          <p className="text-[12.5px] text-ink-600 leading-relaxed">
            Each run is kept and never overwrites an earlier one. The <strong className="text-ink-700">Compare
            runs</strong> panel at the top of a deal&apos;s Activity tab picks a baseline — by default the newest
            complete run before a date you choose — and a head run, by default the run every tab is showing now.
            It lists each headline output that moved, grouped by model: occupancy, ADR, RevPAR, revenue, expense
            lines, management fee, FF&amp;E reserve, gross operating profit and both NOI figures for each year;
            purchase price, total capitalisation, equity and renovation; loan amount and DSCR by year; levered and
            unlevered IRR, equity multiple, exit value and exit NOI; the stabilization block; and LP and GP IRR.
            Each row shows both values, the absolute change and the percentage change, with each side&apos;s
            source. The same table exports to Excel. Comparing never re-runs or edits the model.
          </p>
        </Card>
      </Section>

      {/* ─── 10. What you control ─────────────────────────────────── */}
      <Section
        id="controls"
        number="10"
        title="What is yours to set, and what the model derives"
        intro="A short summary of the division of labour."
      >
        <Card className="p-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 text-[12.5px]">
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Pencil size={13} className="text-brand-500" aria-hidden="true" />
                <h4 className="text-[13px] font-semibold text-ink-900">Analyst-controlled</h4>
              </div>
              <ul className="list-disc pl-5 space-y-1 text-ink-600 leading-relaxed">
                <li>Deal details: name, market, submarket, keys, brands, operating model, positioning, dates.</li>
                <li>Purchase price and the acquisition close date.</li>
                <li>Every financing term, covenant threshold and refinancing assumption.</li>
                <li>Return targets and the return profile.</li>
                <li>Renovation displacement, the stabilization year and the exit NOI basis.</li>
                <li>Any assumption override, each with a justification note.</li>
                <li>The IC recommendation and the diligence status of each flag.</li>
              </ul>
            </div>
            <div>
              <div className="flex items-center gap-2 mb-2">
                <BarChart3 size={13} className="text-brand-500" aria-hidden="true" />
                <h4 className="text-[13px] font-semibold text-ink-900">Model-derived</h4>
              </div>
              <ul className="list-disc pl-5 space-y-1 text-ink-600 leading-relaxed">
                <li>Every extracted figure, with its page and confidence.</li>
                <li>Year-1 anchors from the primary statement and the growth of later years.</li>
                <li>Sources &amp; Uses, the debt schedule, coverage ratios and the cash flow statement.</li>
                <li>Returns, the sale value, the sensitivity grids and the partnership waterfall.</li>
                <li>Variance flags and the model&apos;s own assessment of the Base Case.</li>
                <li>The source and lineage of every number on screen.</li>
              </ul>
            </div>
          </div>
        </Card>
      </Section>

      <div className="flex items-center gap-3 mt-8">
        <Link href="/projects">
          <Button variant="primary">
            <BookOpen size={14} /> Back to deals
          </Button>
        </Link>
        <Link href="/settings/portfolio-library">
          <Button variant="secondary">
            <Database size={14} /> Portfolio Library
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
