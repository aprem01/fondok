'use client';

/**
 * DocumentsChecklist — persistent right-rail for the guided-onboarding
 * wizard Step 3.
 *
 * Eshan's framing on the June 25 design-partner call: "It's kind of
 * dashboard, says done, done, done — financial document, three years
 * got it. Then they know what's missing."
 *
 * The checklist reflects the wizard's four sub-stages:
 *   - 3.1 OM (optional)
 *   - 3.2 Financials by year (REQUIRED)
 *   - 3.3 STR comps (optional)
 *   - 3.4 Catch-all bucket (optional)
 *
 * Financials get a year list under their row so the user sees which
 * years they've staged. The row tones:
 *   - success → at least one file dropped in that category
 *   - danger  → required and empty (only financials)
 *   - gray    → optional and empty
 */

import { CheckCircle2, Circle, AlertCircle, FileText, FileSpreadsheet, BarChart3, FolderOpen } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';

export interface DocumentsChecklistProps {
  omCount: number;
  financialYears: number[]; // distinct years user has staged
  financialCount: number; // total file count regardless of year
  strCount: number;
  otherCount: number;
}

interface Row {
  key: string;
  label: string;
  Icon: typeof FileText;
  required: boolean;
  done: boolean;
  countLabel: string;
  detail?: React.ReactNode;
}

export function DocumentsChecklist({
  omCount,
  financialYears,
  financialCount,
  strCount,
  otherCount,
}: DocumentsChecklistProps) {
  const rows: Row[] = [
    {
      key: 'om',
      label: 'Offering Memorandum',
      Icon: FileText,
      required: false,
      done: omCount > 0,
      countLabel:
        omCount === 0
          ? 'Optional'
          : `${omCount} file${omCount === 1 ? '' : 's'}`,
    },
    {
      key: 'financials',
      label: 'Financials by year',
      Icon: FileSpreadsheet,
      required: true,
      done: financialCount > 0,
      countLabel:
        financialCount === 0
          ? 'Required'
          : `${financialCount} file${financialCount === 1 ? '' : 's'}`,
      detail: financialYears.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5">
          {financialYears
            .slice()
            .sort((a, b) => b - a)
            .map((y) => (
              <span
                key={y}
                className="inline-flex items-center px-1.5 py-0 rounded text-[10.5px] tabular-nums font-medium bg-success-50 text-success-700 border border-success-500/30"
              >
                {y}
              </span>
            ))}
        </div>
      ),
    },
    {
      key: 'str',
      label: 'STR comp-set reports',
      Icon: BarChart3,
      required: false,
      done: strCount > 0,
      countLabel:
        strCount === 0
          ? 'Optional'
          : `${strCount} file${strCount === 1 ? '' : 's'}`,
    },
    {
      key: 'other',
      label: 'Other documents',
      Icon: FolderOpen,
      required: false,
      done: otherCount > 0,
      countLabel:
        otherCount === 0
          ? 'Optional'
          : `${otherCount} file${otherCount === 1 ? '' : 's'}`,
    },
  ];

  const doneCount = rows.filter((r) => r.done).length;
  const totalCount = rows.length;
  const pct = Math.round((doneCount / totalCount) * 100);

  return (
    <Card className="p-4 sticky top-4" aria-label="Document checklist">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">Checklist</h3>
        <span className="text-[11px] text-ink-500 tabular-nums">
          {doneCount} / {totalCount}
        </span>
      </div>
      {/* Progress bar — matches DataRoomTab visual idiom. */}
      <div className="mb-4">
        <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
          <div
            className="h-full bg-brand-500 transition-all motion-reduce:transition-none"
            style={{ width: `${pct}%` }}
            aria-hidden="true"
          />
        </div>
      </div>
      <ul className="space-y-2.5" role="list">
        {rows.map((r) => {
          const Icon = r.Icon;
          const StatusIcon = r.done
            ? CheckCircle2
            : r.required
              ? AlertCircle
              : Circle;
          const statusClass = r.done
            ? 'text-success-500'
            : r.required
              ? 'text-danger-700'
              : 'text-ink-300';
          return (
            <li key={r.key} className="flex items-start gap-2.5" role="listitem">
              <StatusIcon
                size={14}
                className={cn('flex-shrink-0 mt-0.5', statusClass)}
                aria-hidden="true"
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <Icon size={12} className="text-ink-500 flex-shrink-0" aria-hidden="true" />
                  <div className="text-[12px] font-medium text-ink-900 truncate">
                    {r.label}
                  </div>
                </div>
                <div className="mt-0.5 flex items-center gap-2">
                  {r.done ? (
                    <span className="text-[11px] text-success-700 tabular-nums font-medium">
                      {r.countLabel}
                    </span>
                  ) : r.required ? (
                    <Badge tone="red">{r.countLabel}</Badge>
                  ) : (
                    <span className="text-[11px] text-ink-500">{r.countLabel}</span>
                  )}
                </div>
                {r.detail}
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
