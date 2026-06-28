'use client';

/**
 * USALI compliance badge (ROADMAP #3, Feature A).
 *
 * Renders next to a document card's status pill on the Data Room. Wraps
 * the deviation-count + score into a single, low-friction signal so the
 * analyst can see at a glance whether the uploaded P&L conforms to the
 * institutional USALI standard.
 *
 * States — driven entirely by the worker payload on ``WorkerDocument``:
 *   score >= 90              → green   "Compliant {N}"
 *   70 <= score < 90         → amber   "Review {N}"
 *   score < 70               → red     "High risk {N}"
 *   score === null,
 *     payload.inconclusive   → gray    "Inconclusive"
 *   score === null,
 *     no deviations payload  → renders nothing (P&L scoring not applicable)
 *
 * Click toggles the deviation accordion in the parent. Hover surfaces
 * the top-line summary so reviewers don't have to expand for context.
 */

import { ShieldCheck, AlertTriangle, AlertCircle, HelpCircle } from 'lucide-react';
import { cn } from '@/lib/format';
import type {
  WorkerDocument,
  WorkerUsaliPayload,
  WorkerUsaliDeviation,
} from '@/lib/api';
import { Tooltip } from '@/components/help/Tooltip';

type Tier = 'compliant' | 'review' | 'high_risk' | 'inconclusive';

export interface NormalizedUsali {
  tier: Tier;
  /** 0–100, or null when inconclusive. */
  score: number | null;
  applicableCount: number | null;
  passedCount: number | null;
  deviations: WorkerUsaliDeviation[];
}

/** Worker emits either the rich ``USALIScore`` envelope (with
 *  ``inconclusive``/``applicable_count``/``passed_count``/``deviations``)
 *  or a flat ``USALIDeviation[]``. Normalize both for downstream UI. */
export function normalizeUsali(
  score: number | null | undefined,
  payload: WorkerUsaliPayload | WorkerUsaliDeviation[] | null | undefined,
): NormalizedUsali | null {
  // Payload shape sniff. ``Array.isArray`` is the cheapest reliable check.
  const envelope: WorkerUsaliPayload | null = Array.isArray(payload)
    ? { deviations: payload }
    : payload ?? null;

  // Nothing scored — caller renders no badge.
  if (
    score == null &&
    (envelope == null ||
      (envelope.deviations == null && envelope.applicable_count == null))
  ) {
    return null;
  }

  const applicableCount = envelope?.applicable_count ?? null;
  const passedCount = envelope?.passed_count ?? null;
  const deviations = envelope?.deviations ?? [];

  if (score == null) {
    // Worker explicitly said "inconclusive" OR carrying deviations but
    // no headline score (defensive — never seen in practice).
    return {
      tier: 'inconclusive',
      score: null,
      applicableCount,
      passedCount,
      deviations,
    };
  }

  const tier: Tier =
    score >= 90 ? 'compliant' : score >= 70 ? 'review' : 'high_risk';

  return { tier, score, applicableCount, passedCount, deviations };
}

const TIER_META: Record<
  Tier,
  {
    label: (score: number | null) => string;
    classes: string;
    Icon: typeof ShieldCheck;
    aria: (n: NormalizedUsali) => string;
  }
> = {
  compliant: {
    label: (s) => `Compliant ${Math.round(s ?? 0)}`,
    classes:
      'bg-success-50 text-success-700 border-success-500/30 hover:bg-success-100',
    Icon: ShieldCheck,
    aria: (n) => `USALI compliant — score ${Math.round(n.score ?? 0)} of 100`,
  },
  review: {
    label: (s) => `Review ${Math.round(s ?? 0)}`,
    classes:
      'bg-warn-50 text-warn-700 border-warn-500/30 hover:bg-warn-100',
    Icon: AlertCircle,
    aria: (n) => `USALI review — score ${Math.round(n.score ?? 0)} of 100`,
  },
  high_risk: {
    label: (s) => `High risk ${Math.round(s ?? 0)}`,
    classes:
      'bg-danger-50 text-danger-700 border-danger-500/30 hover:bg-danger-100',
    Icon: AlertTriangle,
    aria: (n) => `USALI high risk — score ${Math.round(n.score ?? 0)} of 100`,
  },
  inconclusive: {
    label: () => 'Inconclusive',
    classes:
      'bg-ink-100 text-ink-700 border-ink-200 hover:bg-ink-200',
    Icon: HelpCircle,
    aria: () =>
      'USALI compliance inconclusive — too few applicable rules to score',
  },
};

function buildTooltip(n: NormalizedUsali): string {
  if (n.tier === 'inconclusive') {
    const a = n.applicableCount ?? 0;
    return `Inconclusive — only ${a} USALI rule${a === 1 ? '' : 's'} applicable (need ≥ 5 to score).`;
  }
  const crit = n.deviations.filter((d) => d.severity === 'CRITICAL').length;
  const warn = n.deviations.filter((d) => d.severity === 'WARN').length;
  const passed = n.passedCount ?? 0;
  const applicable = n.applicableCount ?? 0;
  const dev = `${crit} critical · ${warn} warn deviations`;
  return `USALI score ${Math.round(n.score ?? 0)}/100 — ${passed}/${applicable} rules passed · ${dev}. Click for full deviation list.`;
}

export function UsaliBadge({
  doc,
  open,
  onToggle,
  className,
}: {
  doc: Pick<WorkerDocument, 'usali_score' | 'usali_deviations' | 'filename'>;
  /** Controlled-open state from the parent (accordion drives this). */
  open?: boolean;
  /** Toggle callback; when omitted the badge renders as a static <span>. */
  onToggle?: () => void;
  className?: string;
}) {
  const normalized = normalizeUsali(doc.usali_score, doc.usali_deviations);
  if (!normalized) return null;

  const meta = TIER_META[normalized.tier];
  const Icon = meta.Icon;
  const tooltip = buildTooltip(normalized);
  const label = meta.label(normalized.score);

  const baseClasses = cn(
    'inline-flex items-center gap-1 px-2 py-0.5 rounded-md border',
    'text-[11px] font-medium tabular-nums whitespace-nowrap transition-colors',
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1',
    meta.classes,
    className,
  );

  if (!onToggle) {
    return (
      <Tooltip
        content={<span className="whitespace-pre-wrap leading-relaxed">{tooltip}</span>}
        side="top"
        learnMoreHref="/methodology#projection"
      >
        <span
          className={baseClasses}
          role="img"
          aria-label={meta.aria(normalized)}
          tabIndex={0}
        >
          <Icon size={11} aria-hidden="true" />
          {label}
        </span>
      </Tooltip>
    );
  }

  return (
    <Tooltip
      content={<span className="whitespace-pre-wrap leading-relaxed">{tooltip}</span>}
      side="top"
      learnMoreHref="/methodology#projection"
    >
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onToggle();
        }}
        className={baseClasses}
        aria-expanded={!!open}
        aria-label={`${meta.aria(normalized)}. ${open ? 'Collapse' : 'Expand'} deviation list.`}
      >
        <Icon size={11} aria-hidden="true" />
        {label}
      </button>
    </Tooltip>
  );
}
