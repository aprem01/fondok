'use client';

/**
 * DocumentsChecklist — Wave 1 expansion (June 2026).
 *
 * Right-rail of the new-project wizard's Step 3. Surfaces the canonical
 * 11-category IC checklist with per-category status:
 *
 *   * Green ✓ N files → covered
 *   * Red Missing      → required_for_ic and uncovered
 *   * Gray Optional    → required_for_ic=false (SURVEYS only)
 *
 * Eshan's framing on the June 25 design-partner call: "It's kind of
 * dashboard, says done, done, done — financial document, three years
 * got it. Then they know what's missing." This is that dashboard.
 *
 * Locked product decision — only Financials is hard-required to advance
 * the wizard. The other nine "Required for IC" rows still surface red
 * Missing pills but DON'T block the Next button; the wizard's gate
 * lives in DocumentsStep + page.tsx.
 */

import { CheckCircle2, AlertCircle, Circle } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';
import type { WizardCategory, WizardFile } from '@/lib/api';
import { WIZARD_CATEGORIES } from './DocumentsStep';

export interface DocumentsChecklistProps {
  files: WizardFile[];
  /** Optional callback when a row is clicked — wizard page jumps to
   *  that sub-stage. The Card stays presentational; the parent owns
   *  the actual stage state. */
  onJumpTo?: (category: WizardCategory) => void;
  /** Optional: which category is currently active in the content
   *  panel. Used to mirror the brand-50 active state from the sidebar
   *  here so the user always sees a single source of truth. */
  activeCategory?: WizardCategory | null;
}

export function DocumentsChecklist({
  files,
  onJumpTo,
  activeCategory,
}: DocumentsChecklistProps) {
  const counts = {} as Record<WizardCategory, number>;
  for (const c of WIZARD_CATEGORIES) counts[c.id] = 0;
  for (const f of files) {
    if (counts[f.category] !== undefined) counts[f.category] += 1;
  }

  // Split into Required vs Recommended-for-IC vs Optional sections. The
  // Required section is the financials gate; the Recommended-for-IC
  // section is the nine other required-for-IC categories; Optional is
  // SURVEYS alone.
  const financialIds: WizardCategory[] = ['t12', 'historical_pnl'];
  const financialRows = WIZARD_CATEGORIES.filter((c) =>
    financialIds.includes(c.id),
  );
  const recommendedRows = WIZARD_CATEGORIES.filter(
    (c) => c.requiredForIc && !financialIds.includes(c.id),
  );
  const optionalRows = WIZARD_CATEGORIES.filter((c) => !c.requiredForIc);

  const ficovered = financialRows.some((c) => counts[c.id] > 0);
  const recCoveredCount = recommendedRows.filter((c) => counts[c.id] > 0).length;
  const totalIc = financialRows.length + recommendedRows.length; // 10
  const coveredIc =
    (ficovered ? 1 : 0) +
    (financialRows.filter((c) => counts[c.id] > 0).length - (ficovered ? 1 : 0)) +
    recCoveredCount;
  // Cleaner: just count distinct covered required-for-IC categories.
  const icCovered = [...financialRows, ...recommendedRows].filter(
    (c) => counts[c.id] > 0,
  ).length;
  const pct = Math.round((icCovered / totalIc) * 100);

  return (
    <Card className="p-4 sticky top-4" aria-label="Document checklist">
      <div className="flex items-center justify-between mb-3">
        <div>
          <h3 className="text-[13px] font-semibold text-ink-900">
            IC Checklist
          </h3>
          <div className="text-[10.5px] text-ink-500 mt-0.5 leading-tight">
            10 categories underwriters expect
          </div>
        </div>
        <span className="text-[11px] text-ink-500 tabular-nums">
          {icCovered} / {totalIc}
        </span>
      </div>

      {/* Progress bar — IC coverage. */}
      <div className="mb-4">
        <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
          <div
            className="h-full bg-brand-500 transition-all motion-reduce:transition-none"
            style={{ width: `${pct}%` }}
            aria-hidden="true"
          />
        </div>
      </div>

      <ChecklistSection
        title="Required — Financials"
        subtitle="Either T-12 or Historical P&L satisfies the wizard gate."
        rows={financialRows}
        counts={counts}
        onJumpTo={onJumpTo}
        activeCategory={activeCategory ?? null}
        tone="gate"
      />
      <ChecklistSection
        title="Recommended for IC"
        subtitle="Missing items surface as red flags on the deal workspace."
        rows={recommendedRows}
        counts={counts}
        onJumpTo={onJumpTo}
        activeCategory={activeCategory ?? null}
        tone="ic"
      />
      <ChecklistSection
        title="Optional"
        subtitle="Expected by closing — broker often drips these in."
        rows={optionalRows}
        counts={counts}
        onJumpTo={onJumpTo}
        activeCategory={activeCategory ?? null}
        tone="optional"
      />
    </Card>
  );
}

function ChecklistSection({
  title,
  subtitle,
  rows,
  counts,
  onJumpTo,
  activeCategory,
  tone,
}: {
  title: string;
  subtitle: string;
  rows: typeof WIZARD_CATEGORIES;
  counts: Record<WizardCategory, number>;
  onJumpTo?: (category: WizardCategory) => void;
  activeCategory: WizardCategory | null;
  tone: 'gate' | 'ic' | 'optional';
}) {
  if (rows.length === 0) return null;
  return (
    <div className="mb-4 last:mb-0">
      <div className="flex items-center justify-between mb-1">
        <div className="text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold">
          {title}
        </div>
        {tone === 'gate' && (
          <Badge tone="red" className="text-[9.5px]">
            REQUIRED
          </Badge>
        )}
      </div>
      <div className="text-[10.5px] text-ink-500 mb-2 leading-tight">
        {subtitle}
      </div>
      <ul className="space-y-1.5" role="list">
        {rows.map((spec) => {
          const count = counts[spec.id];
          const covered = count > 0;
          const active = activeCategory === spec.id;
          const StatusIcon = covered
            ? CheckCircle2
            : tone === 'optional'
              ? Circle
              : AlertCircle;
          const statusClass = covered
            ? 'text-success-500'
            : tone === 'optional'
              ? 'text-ink-300'
              : 'text-danger-700';
          const row = (
            <div
              className={cn(
                'flex items-start gap-2 px-2 py-1.5 rounded-md',
                active
                  ? 'bg-brand-50 border border-brand-500/40'
                  : 'border border-transparent hover:bg-ink-100/50',
                onJumpTo ? 'cursor-pointer' : '',
              )}
            >
              <StatusIcon
                size={13}
                className={cn('flex-shrink-0 mt-0.5', statusClass)}
                aria-hidden="true"
              />
              <div className="flex-1 min-w-0">
                <div
                  className={cn(
                    'text-[12px] font-medium leading-tight truncate',
                    active ? 'text-brand-700' : 'text-ink-900',
                  )}
                >
                  {spec.label}
                </div>
                <div className="mt-0.5">
                  {covered ? (
                    <span className="text-[10.5px] tabular-nums font-medium text-success-700">
                      {count} file{count === 1 ? '' : 's'}
                    </span>
                  ) : tone === 'optional' ? (
                    <span className="text-[10.5px] text-ink-500">Optional</span>
                  ) : (
                    <Badge tone="red" className="text-[9.5px]">
                      Missing
                    </Badge>
                  )}
                </div>
              </div>
            </div>
          );
          if (onJumpTo) {
            return (
              <li key={spec.id} role="listitem">
                <button
                  type="button"
                  onClick={() => onJumpTo(spec.id)}
                  aria-label={`Jump to ${spec.label}`}
                  className="w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-md"
                >
                  {row}
                </button>
              </li>
            );
          }
          return (
            <li key={spec.id} role="listitem">
              {row}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
