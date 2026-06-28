'use client';

/**
 * DocumentsChecklist — Wave 1 UX reduction (June 2026).
 *
 * Was: a verbose three-section list (Required / Recommended / Optional)
 * that duplicated the sidebar's "what's missing" signal and shouted
 * REQUIRED + descriptive subtitles. The sidebar already covers per-row
 * status — keeping both inflated the wizard surface to ~9 distinct
 * information blocks.
 *
 * Now: a single radial completeness ring (covered / required-for-IC)
 * plus one quiet "Skip optional categories" link to the methodology
 * anchor. Hidden below xl (1280px) so the sidebar carries the load on
 * narrower viewports — when both rails are visible the rail must
 * justify itself in a glance.
 *
 * Locked product decision — only Financials hard-gate the wizard; that
 * gate lives in DocumentsStep + page.tsx, not here.
 */

import Link from 'next/link';
import { Card } from '@/components/ui/Card';
import { cn } from '@/lib/format';
import type { WizardCategory, WizardFile } from '@/lib/api';
import { WIZARD_CATEGORIES } from './DocumentsStep';

export interface DocumentsChecklistProps {
  files: WizardFile[];
  /** Kept for API parity with the previous list — the new collapsed
   *  rail doesn't expose per-row jump targets (the sidebar owns
   *  navigation now), but the prop is accepted so the page wiring
   *  doesn't have to change. */
  onJumpTo?: (category: WizardCategory) => void;
  /** Accepted for API parity (the previous rail mirrored the active
   *  category from the sidebar). Unused in the collapsed rail. */
  activeCategory?: WizardCategory | null;
}

export function DocumentsChecklist(_props: DocumentsChecklistProps) {
  const counts = {} as Record<WizardCategory, number>;
  for (const c of WIZARD_CATEGORIES) counts[c.id] = 0;
  for (const f of _props.files) {
    if (counts[f.category] !== undefined) counts[f.category] += 1;
  }

  // Coverage denominator is the 10 IC-required categories (everything
  // except Surveys). One file in either financial bucket counts as
  // covered for that row — the gate logic lives in DocumentsStep.
  const requiredCategories = WIZARD_CATEGORIES.filter(
    (c) => c.requiredForIc,
  );
  const total = requiredCategories.length;
  const covered = requiredCategories.filter((c) => counts[c.id] > 0).length;
  const pct = total > 0 ? Math.round((covered / total) * 100) : 0;

  // Stroke geometry — r=22, circumference 2πr ≈ 138.23.
  const radius = 22;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference - (pct / 100) * circumference;

  return (
    <Card
      className="p-5 sticky top-4 hidden xl:block"
      aria-label="Document completeness"
    >
      <div className="flex flex-col items-center gap-3">
        {/* Radial ring — single visual cue for coverage. */}
        <div
          className="relative w-14 h-14"
          role="img"
          aria-label={`${covered} of ${total} required categories covered (${pct}%)`}
        >
          <svg
            viewBox="0 0 56 56"
            className="w-14 h-14 -rotate-90"
            aria-hidden="true"
          >
            <circle
              cx="28"
              cy="28"
              r={radius}
              fill="none"
              stroke="currentColor"
              strokeWidth="4"
              className="text-ink-300/40"
            />
            <circle
              cx="28"
              cy="28"
              r={radius}
              fill="none"
              stroke="currentColor"
              strokeWidth="4"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={dashOffset}
              className={cn(
                'transition-[stroke-dashoffset] duration-300 motion-reduce:transition-none',
                pct === 100 ? 'text-success-500' : 'text-brand-500',
              )}
            />
          </svg>
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-[12.5px] font-semibold tabular-nums text-ink-900">
              {covered}
              <span className="text-ink-500 font-medium">/{total}</span>
            </span>
          </div>
        </div>

        <div className="text-[11px] text-ink-500 uppercase tracking-wider font-semibold">
          IC coverage
        </div>

        <Link
          href="/methodology#extraction"
          className="text-[11px] text-brand-500 hover:text-brand-700 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded"
        >
          Skip optional &rarr;
        </Link>
      </div>
    </Card>
  );
}
