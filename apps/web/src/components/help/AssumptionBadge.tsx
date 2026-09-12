'use client';
import { Database, Sparkles, Pencil, FileText, BarChart3, Map, ExternalLink } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/format';
import type { AssumptionSource } from '@/lib/api';
import {
  SOURCE_BADGE_FROM_REGISTRY, SOURCE_EXPLANATION_FROM_REGISTRY,
} from '@/lib/ontology/adapters';
import { Tooltip } from './Tooltip';

/**
 * Tiny badge that explains where an assumption value came from.
 *
 * Sam v2 QA #11: "Kimpton defaults (RevPAR 4.5%, exit cap 7%) silently
 * applied to every deal." Each assumption-driven number on Investment /
 * Returns / Overview gets one of these next to it so the reviewer can
 * see whether the value is a seed, extracted from a real doc, or an
 * analyst override.
 *
 * When ``onOverride`` is provided, the badge renders an extra small
 * "Override" button alongside the source label so the analyst can
 * hard-code the value with a mandatory justification note (roadmap
 * item #6 from the June 2026 call). The callback opens the
 * ``OverridePanel`` (right-anchored drawer); the page that owns the
 * data is responsible for the actual PATCH + re-render.
 *
 * Use inline: `Net Operating Income $4.2M <AssumptionBadge source="t12_actual"/>`
 */
export function AssumptionBadge({
  source,
  documentId,
  dealId,
  className,
  onOverride,
  overrideNote,
}: {
  source: AssumptionSource | string | undefined;
  /** Optional: when present, the badge becomes clickable and routes
   *  to the Data Room with the contributing doc preselected.
   *  Sam P3 doc-to-engine traceability — "click the badge → jump
   *  to the document that produced this assumption." */
  documentId?: string | null;
  /** Optional deal id for the routing target. Falls back to no-op
   *  when omitted (badge still renders, just not clickable). */
  dealId?: string | null;
  className?: string;
  /** When provided, renders an "Override" pencil button next to the
   *  badge. The callback receives no args — the parent owns the field
   *  identity and renders the modal. */
  onOverride?: () => void;
  /** When the active source is ``analyst_override``, surface the note
   *  the analyst recorded in the badge tooltip. */
  overrideNote?: string | null;
}) {
  const router = useRouter();
  if (!source) return null;
  const cfg = SOURCE_META[source as AssumptionSource] ?? SOURCE_META.seed;
  const Icon = cfg.Icon;
  const clickable = !!(documentId && dealId);
  let tooltip = cfg.tooltip;
  if (clickable) {
    tooltip = `${tooltip} Click to open the source document.`;
  }
  if (source === 'analyst_override' && overrideNote) {
    tooltip = `${tooltip}\n\nNote: ${overrideNote}`;
  }

  const content = (
    <>
      <Icon size={9} aria-hidden="true" />
      {cfg.label}
      {clickable && <ExternalLink size={8} aria-hidden="true" className="opacity-70" />}
    </>
  );

  const classes = cn(
    'inline-flex items-center gap-0.5 px-1 py-0 rounded text-[9.5px] font-medium align-middle leading-none border tabular-nums whitespace-nowrap',
    cfg.tone,
    clickable && 'hover:underline cursor-pointer',
    className,
  );

  const tooltipBody = (
    <span className="whitespace-pre-wrap leading-relaxed">{tooltip}</span>
  );

  const sourceEl = clickable ? (
    <Tooltip content={tooltipBody} side="top" learnMoreHref="/methodology#sources">
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          router.push(`/projects/${dealId}?tab=&doc=${documentId}`);
        }}
        className={classes}
      >
        {content}
      </button>
    </Tooltip>
  ) : (
    <Tooltip content={tooltipBody} side="top" learnMoreHref="/methodology#sources">
      <span className={classes} tabIndex={0}>
        {content}
      </span>
    </Tooltip>
  );

  if (!onOverride) return sourceEl;

  return (
    <span className="inline-flex items-center gap-1 align-middle">
      {sourceEl}
      <Tooltip content="Override this value with an analyst note" side="top">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onOverride();
          }}
          className="inline-flex items-center justify-center w-4 h-4 rounded text-ink-500 hover:text-blue-700 hover:bg-blue-50 transition-colors"
          aria-label="Override value"
        >
          <Pencil size={9} aria-hidden="true" />
        </button>
      </Tooltip>
    </span>
  );
}

type SourceMeta = {
  label: string;
  tone: string;
  tooltip: string;
  Icon: typeof Database;
};

/**
 * Phase 1.4 — the badge TEXT and the set of labels come from the generated
 * registry: ``label`` is ``SOURCES[id].badge`` and ``AssumptionSource`` is the
 * registry's ``SourceId``. All 12 badge strings this map hand-maintained match
 * the registry byte-for-byte; the widening is that the six labels the worker
 * emits which this map never had — ``str_segmentation_default``, ``pip_om``,
 * ``pip_user``, ``capex_ffe_default``, ``roi_user``, ``partnership_doc`` — now
 * badge as themselves instead of falling through to ``SOURCE_META.seed``. A
 * PIP read off the OM used to render "Seed".
 *
 * TONE, ICON and TOOLTIP COPY stay hand-authored. The tooltip is the wording
 * Sam reads (the seed line is quoted verbatim on /methodology), so a refactor
 * must not touch it — where the registry's explanation differs, the web text
 * below wins and the difference is recorded in
 * ``lib/ontology/DRIFT_NOTES.web.md``. The six new labels have no web copy and
 * take the registry's explanation.
 */
const TONE = {
  neutral: 'bg-ink-300/20 text-ink-700 border-ink-300/40',
  grounded: 'bg-success-50 text-success-700 border-success-500/30',
  brand: 'bg-brand-50 text-brand-700 border-brand-500/30',
  input: 'bg-blue-50 text-blue-700 border-blue-500/30',
  warn: 'bg-warn-50 text-warn-700 border-warn-500/30',
} as const;

/** Hand-authored presentation per source. ``tooltip`` omitted → the registry's
 *  explanation is used. */
type SourceStyle = { tone: string; Icon: typeof Database; tooltip?: string };

const SOURCE_STYLE: Record<AssumptionSource, SourceStyle> = {
  seed: {
    tone: TONE.neutral,
    tooltip:
      'Kimpton fixture default — no deal-specific data has overridden this yet. Upload an OM / T-12 / CBRE Horizons doc to ground it.',
    Icon: Sparkles,
  },
  deal_row: {
    tone: TONE.neutral,
    tooltip:
      'Sourced from the deals table (entered on the create-deal wizard or PATCHed via the API).',
    Icon: Database,
  },
  t12_actual: {
    tone: TONE.grounded,
    tooltip:
      'Year-1 actual from the deal’s extracted T-12. Out-years grown forward at the configured expense / revenue growth rate.',
    Icon: BarChart3,
  },
  cbre_horizons: {
    tone: TONE.brand,
    tooltip:
      'Forecast curve extracted from an uploaded CBRE Horizons report (subject submarket / chain-scale segment).',
    Icon: Map,
  },
  pnl_benchmark: {
    tone: TONE.brand,
    tooltip:
      'Industry benchmark margin (HotStats-equivalent P&L benchmark doc) applied as a USALI ratio override.',
    Icon: BarChart3,
  },
  // Wave 2 P2.7 — analyst's in-house portfolio P&L roll-up. Outranks
  // PNL Bench (generic HostStats) and CBRE Horizons for op-ratios.
  portfolio_pnl: {
    tone: TONE.brand,
    tooltip:
      "Ratio sourced from your firm's in-house portfolio P&L benchmark — aggregated across hotels you already operate at this chain scale. Outranks generic HostStats / CBRE because you own the underlying P&Ls.",
    Icon: BarChart3,
  },
  om_comps: {
    tone: TONE.brand,
    tooltip:
      'Median cap rate derived from the OM’s "Comparable Sales" transaction-comps table.',
    Icon: FileText,
  },
  om_broker: {
    tone: TONE.brand,
    tooltip:
      'Broker proforma value extracted from the Offering Memorandum.',
    Icon: FileText,
  },
  analyst_override: {
    // FON-65 — analyst override is an "input/assumption" value, so it uses the
    // canonical blue (matches KIND_TONE.override + the DATA KEY), not amber.
    tone: TONE.input,
    tooltip:
      'Analyst override set via the Overview inline editor. Wins over every other source.',
    Icon: Pencil,
  },
  // Wave 3 W3.3 — STR forward-forecast seed. When the analyst opts in
  // (revenue_seed_from_str_forecast=True), starting_occupancy and
  // starting_adr are pulled from the BASE scenario's Month-12 forecast
  // point so the revenue engine inherits the forecast's bottom-up math.
  // FON-61 (61.2) — three provenances used to share this one badge, so a Base
  // Year that was the subject's own TTM ACTUAL was labelled a forecast. Each
  // now has its own tooltip; the revert path is identical for all three.
  str_forecast: {
    tone: TONE.brand,
    tooltip:
      'Seeded from the BASE STR forward-forecast scenario (the Month-12 point) — a projection of the subject, not an actual. Revert from the Market tab or Financials → Projections to fall back to T-12 / CBRE / seed defaults.',
    Icon: BarChart3,
  },
  str_subject_ttm: {
    tone: TONE.grounded,
    tooltip:
      'The subject property’s OWN trailing-twelve-month Occupancy / ADR as reported by STR — an actual, not a forecast and not the comp set. This is what the model seeds Year-1 from when the STR basis is on. Revert from the Market tab or Financials → Projections to fall back to T-12 / CBRE / seed defaults.',
    Icon: BarChart3,
  },
  str_comp_set: {
    tone: TONE.brand,
    tooltip:
      'The STR comp-set blended rates shown on the Market tab, applied as the Year-1 input by “Use STR rates in the model” — the competitive set’s performance, not the subject’s own. Revert from the Market tab or Financials → Projections to fall back to T-12 / CBRE / seed defaults.',
    Icon: BarChart3,
  },
  // FON-61 (D4) — the STR seed is never silent. The analyst asked for STR
  // rates but the worker could not populate them (no STR Trend extraction,
  // coverage too low, or a loader failure) — the model stayed on the T-12
  // base and this badge says so rather than claiming STR is active.
  str_forecast_unavailable: {
    tone: TONE.warn,
    tooltip:
      'STR rates were requested but could not populate (no STR Trend extraction or coverage too low) — the model is on the T-12 base. Upload an STR Trend report or use the Market tab’s comp-set rates.',
    Icon: BarChart3,
  },
  // Phase 2.1 — the as-of siblings of str_forecast_unavailable. Emitted only
  // when the deal sets an underwriting as-of date and the market report is
  // dated after it. ``SOURCE_STYLE`` is ``Record<SourceId, …>``, so these two
  // entries are what keeps the type exhaustive after the registry edit.
  cbre_horizons_unavailable: {
    tone: TONE.warn,
    tooltip:
      'A CBRE Horizons report is on the deal but it is dated after the underwriting as-of date, so it was not knowable then — the growth rates stay on their prior basis.',
    Icon: Map,
  },
  om_comps_unavailable: {
    tone: TONE.warn,
    tooltip:
      'The offering memorandum’s comparable-sales table is dated after the underwriting as-of date, so it was not knowable then — the exit cap rate stays on its prior basis.',
    Icon: Map,
  },
  // FON-69 — an analyst RevPAR-growth override derives adr_growth so
  // operating NOI moves: adr_growth = (1 + revpar_growth) / (1 + occupancy_growth) − 1.
  derived_from_revpar_growth: {
    tone: TONE.input,
    tooltip:
      'ADR growth derived from the analyst’s RevPAR-growth override — (1 + RevPAR growth) ÷ (1 + occupancy growth) − 1 — with the occupancy path held. Set ADR growth explicitly to take direct control.',
    Icon: Pencil,
  },
  // ── Phase 1.4: labels the worker has always emitted that this map did not
  //    know. They badged as "Seed". Tooltip copy comes from the registry.
  str_segmentation_default: { tone: TONE.brand, Icon: BarChart3 },
  pip_om: { tone: TONE.brand, Icon: FileText },
  pip_user: { tone: TONE.input, Icon: Pencil },
  capex_ffe_default: { tone: TONE.neutral, Icon: Sparkles },
  roi_user: { tone: TONE.input, Icon: Pencil },
  partnership_doc: { tone: TONE.brand, Icon: FileText },
};

const SOURCE_META: Record<AssumptionSource, SourceMeta> = Object.fromEntries(
  (Object.keys(SOURCE_STYLE) as AssumptionSource[]).map((id) => [
    id,
    {
      label: SOURCE_BADGE_FROM_REGISTRY[id],
      tone: SOURCE_STYLE[id].tone,
      Icon: SOURCE_STYLE[id].Icon,
      tooltip: SOURCE_STYLE[id].tooltip ?? SOURCE_EXPLANATION_FROM_REGISTRY[id],
    },
  ]),
) as Record<AssumptionSource, SourceMeta>;
