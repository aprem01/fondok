'use client';
import { Database, Sparkles, Pencil, FileText, BarChart3, Map } from 'lucide-react';
import { cn } from '@/lib/format';
import type { AssumptionSource } from '@/lib/api';

/**
 * Tiny badge that explains where an assumption value came from.
 *
 * Sam v2 QA #11: "Kimpton defaults (RevPAR 4.5%, exit cap 7%) silently
 * applied to every deal." Each assumption-driven number on Investment /
 * Returns / Overview gets one of these next to it so the reviewer can
 * see whether the value is a seed, extracted from a real doc, or an
 * analyst override.
 *
 * Use inline: `Net Operating Income $4.2M <AssumptionBadge source="t12_actual"/>`
 */
export function AssumptionBadge({
  source,
  className,
}: {
  source: AssumptionSource | string | undefined;
  className?: string;
}) {
  if (!source) return null;
  const cfg = SOURCE_META[source as AssumptionSource] ?? SOURCE_META.seed;
  const Icon = cfg.Icon;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 px-1 py-0 rounded text-[9.5px] font-medium align-middle leading-none border tabular-nums whitespace-nowrap',
        cfg.tone,
        className,
      )}
      title={cfg.tooltip}
    >
      <Icon size={9} aria-hidden="true" />
      {cfg.label}
    </span>
  );
}

type SourceMeta = {
  label: string;
  tone: string;
  tooltip: string;
  Icon: typeof Database;
};

const SOURCE_META: Record<AssumptionSource, SourceMeta> = {
  seed: {
    label: 'Seed',
    tone: 'bg-ink-300/20 text-ink-700 border-ink-300/40',
    tooltip:
      'Kimpton fixture default — no deal-specific data has overridden this yet. Upload an OM / T-12 / CBRE Horizons doc to ground it.',
    Icon: Sparkles,
  },
  deal_row: {
    label: 'Deal',
    tone: 'bg-ink-300/20 text-ink-700 border-ink-300/40',
    tooltip:
      'Sourced from the deals table (entered on the create-deal wizard or PATCHed via the API).',
    Icon: Database,
  },
  t12_actual: {
    label: 'T-12',
    tone: 'bg-success-50 text-success-700 border-success-500/30',
    tooltip:
      'Year-1 actual from the deal’s extracted T-12. Out-years grown forward at the configured expense / revenue growth rate.',
    Icon: BarChart3,
  },
  cbre_horizons: {
    label: 'CBRE',
    tone: 'bg-brand-50 text-brand-700 border-brand-500/30',
    tooltip:
      'Forecast curve extracted from an uploaded CBRE Horizons report (subject submarket / chain-scale segment).',
    Icon: Map,
  },
  pnl_benchmark: {
    label: 'PNL Bench',
    tone: 'bg-brand-50 text-brand-700 border-brand-500/30',
    tooltip:
      'Industry benchmark margin (HotStats-equivalent P&L benchmark doc) applied as a USALI ratio override.',
    Icon: BarChart3,
  },
  om_comps: {
    label: 'OM Comps',
    tone: 'bg-brand-50 text-brand-700 border-brand-500/30',
    tooltip:
      'Median cap rate derived from the OM’s "Comparable Sales" transaction-comps table.',
    Icon: FileText,
  },
  om_broker: {
    label: 'OM',
    tone: 'bg-brand-50 text-brand-700 border-brand-500/30',
    tooltip:
      'Broker proforma value extracted from the Offering Memorandum.',
    Icon: FileText,
  },
  analyst_override: {
    label: 'Override',
    tone: 'bg-warn-50 text-warn-700 border-warn-500/30',
    tooltip:
      'Analyst override set via the Overview inline editor. Wins over every other source.',
    Icon: Pencil,
  },
};
