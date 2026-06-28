'use client';
/**
 * ScenarioComparePanel — Wave 3 W3.2 side-by-side compare table.
 *
 * Rows are KPIs (IRR, Equity Multiple, NOI Y1, NOI Y5, Cap Rate,
 * DSCR, Total Cost). Columns are up to 4 selected scenarios. Each
 * column also shows a "% delta from base" pill and an "Add to memo"
 * CTA stub (stub for now — Wave 3 W3.4 will wire the memo append).
 */
import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, FileText, Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import {
  api,
  type ScenarioCompareResponse,
  type ScenarioRecord,
} from '@/lib/api';
import { cn } from '@/lib/format';

interface Props {
  dealId: string;
  scenarios: ScenarioRecord[];
}

interface KpiRow {
  key: string;
  label: string;
  format: 'pct' | 'multiple' | 'usd' | 'ratio';
  /** Extract the metric from an engine output map. */
  pick: (engines: Record<string, EnginePayload>) => number | null;
}

interface EnginePayload {
  status: string;
  outputs?: unknown;
  summary?: string;
}

const KPI_ROWS: KpiRow[] = [
  {
    key: 'irr',
    label: 'Levered IRR',
    format: 'pct',
    pick: (e) => num(e.returns?.outputs, ['levered_irr']),
  },
  {
    key: 'em',
    label: 'Equity Multiple',
    format: 'multiple',
    pick: (e) => num(e.returns?.outputs, ['equity_multiple']),
  },
  {
    key: 'noi_y1',
    label: 'NOI Y1',
    format: 'usd',
    pick: (e) => num(e.expense?.outputs, ['years', 0, 'noi']),
  },
  {
    key: 'noi_y5',
    label: 'NOI Y5',
    format: 'usd',
    pick: (e) => num(e.expense?.outputs, ['years', 4, 'noi']),
  },
  {
    key: 'cap',
    label: 'Exit Cap Rate',
    format: 'pct',
    pick: (e) =>
      num(e.returns?.outputs, ['exit_cap_rate']) ??
      num(e.sensitivity?.outputs, ['exit_cap_rate']),
  },
  {
    key: 'dscr',
    label: 'Avg DSCR',
    format: 'ratio',
    pick: (e) =>
      num(e.debt?.outputs, ['avg_dscr']) ?? num(e.debt?.outputs, ['dscr']),
  },
  {
    key: 'total_cost',
    label: 'Total Project Cost',
    format: 'usd',
    pick: (e) =>
      num(e.capital?.outputs, ['total_project_cost']) ??
      num(e.capital?.outputs, ['total_cost']),
  },
];

export default function ScenarioComparePanel({ dealId, scenarios }: Props) {
  const { toast } = useToast();
  // Default selection: base + first two named scenarios.
  const defaultIds = useMemo(() => {
    const base = scenarios.find((s) => s.is_base);
    const others = scenarios.filter((s) => !s.is_base).slice(0, 3);
    return [base?.id, ...others.map((s) => s.id)].filter(
      (x): x is string => typeof x === 'string',
    );
  }, [scenarios]);

  const [selectedIds, setSelectedIds] = useState<string[]>(defaultIds);
  const [data, setData] = useState<ScenarioCompareResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setSelectedIds(defaultIds);
  }, [defaultIds]);

  useEffect(() => {
    if (selectedIds.length === 0) {
      setData(null);
      return;
    }
    const ac = new AbortController();
    setLoading(true);
    api.scenarios
      .compare(dealId, selectedIds)
      .then((res) => setData(res))
      .catch((e) => {
        toast(
          `Compare failed: ${e instanceof Error ? e.message : 'Unknown error'}`,
          { type: 'error' },
        );
      })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [dealId, selectedIds, toast]);

  if (scenarios.length < 2) {
    return (
      <Card className="p-6 text-[13px] text-ink-700">
        <div className="flex items-start gap-2">
          <FileText size={16} className="text-ink-500 mt-0.5" aria-hidden="true" />
          <div>
            <p className="font-semibold mb-1">Add a scenario to compare.</p>
            <p className="text-[12.5px] text-ink-600">
              Every deal starts with a single base case. Once you save a
              downside or upside scenario, this panel renders the side-by-side
              IRR / EM / NOI deltas.
            </p>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <ScenarioPicker
        scenarios={scenarios}
        selectedIds={selectedIds}
        onChange={setSelectedIds}
      />

      <Card className="overflow-hidden">
        {loading && (
          <div className="flex items-center gap-2 px-4 py-3 border-b border-border text-[12px] text-ink-500">
            <Loader2 size={13} className="animate-spin" aria-hidden="true" />
            Running scenarios…
          </div>
        )}
        {data && data.scenarios.length > 0 && (
          <CompareTable response={data} baseId={data.base_scenario_id} />
        )}
      </Card>
    </div>
  );
}

interface PickerProps {
  scenarios: ScenarioRecord[];
  selectedIds: string[];
  onChange: (ids: string[]) => void;
}

function ScenarioPicker({ scenarios, selectedIds, onChange }: PickerProps) {
  function toggle(id: string) {
    if (selectedIds.includes(id)) {
      onChange(selectedIds.filter((x) => x !== id));
    } else if (selectedIds.length < 4) {
      onChange([...selectedIds, id]);
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-[11px] uppercase tracking-wide text-ink-500 mr-1">
        Compare (up to 4)
      </span>
      {scenarios.map((s) => {
        const checked = selectedIds.includes(s.id);
        const disabled = !checked && selectedIds.length >= 4;
        return (
          <button
            key={s.id}
            type="button"
            onClick={() => toggle(s.id)}
            disabled={disabled}
            aria-pressed={checked}
            className={cn(
              'px-3 py-1 text-[12px] rounded-full border transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
              checked
                ? 'bg-brand-500 text-white border-brand-600 font-medium'
                : 'bg-white text-ink-700 border-border hover:border-brand-400',
              disabled ? 'opacity-40 cursor-not-allowed' : '',
            )}
          >
            {s.name}
          </button>
        );
      })}
    </div>
  );
}

interface TableProps {
  response: ScenarioCompareResponse;
  baseId: string | null;
}

function CompareTable({ response, baseId }: TableProps) {
  const { toast } = useToast();
  const baseCell = response.scenarios.find((c) => c.scenario_id === baseId);

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-[12.5px]">
        <thead className="bg-surface-muted border-b border-border">
          <tr>
            <th className="text-left px-4 py-2 font-semibold text-ink-700">
              KPI
            </th>
            {response.scenarios.map((cell) => (
              <th
                key={cell.scenario_id}
                className="text-right px-4 py-2 font-semibold text-ink-900"
              >
                <div className="flex items-center justify-end gap-1">
                  {cell.is_base && (
                    <span className="text-[10px] uppercase tracking-wide bg-ink-100 text-ink-600 px-1 rounded">
                      Base
                    </span>
                  )}
                  <span>{cell.scenario_name}</span>
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {KPI_ROWS.map((row) => {
            const baseVal = baseCell
              ? row.pick(baseCell.engines as Record<string, EnginePayload>)
              : null;
            return (
              <tr key={row.key} className="border-b border-border last:border-b-0">
                <td className="px-4 py-2 text-ink-700">{row.label}</td>
                {response.scenarios.map((cell) => {
                  const v = row.pick(
                    cell.engines as Record<string, EnginePayload>,
                  );
                  const delta =
                    baseVal !== null && v !== null && !cell.is_base
                      ? (v - baseVal) / Math.max(Math.abs(baseVal), 1e-9)
                      : null;
                  return (
                    <td
                      key={cell.scenario_id}
                      className="px-4 py-2 text-right tabular-nums"
                    >
                      <div className="text-ink-900">
                        {v === null ? '—' : formatKpi(row.format, v)}
                      </div>
                      {delta !== null && (
                        <div
                          className={cn(
                            'text-[10.5px]',
                            delta >= 0 ? 'text-emerald-600' : 'text-red-600',
                          )}
                        >
                          {delta >= 0 ? '+' : ''}
                          {(delta * 100).toFixed(1)}%
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          <tr>
            <td className="px-4 py-3" />
            {response.scenarios.map((cell) => (
              <td key={cell.scenario_id} className="px-4 py-3 text-right">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    toast(
                      `Memo append — coming in Wave 3 W3.4. Scenario "${cell.scenario_name}" stub queued.`,
                      { type: 'info' },
                    )
                  }
                >
                  <FileText size={11} aria-hidden="true" />
                  <span className="ml-1">Add to memo</span>
                  <ArrowRight size={11} className="ml-1" aria-hidden="true" />
                </Button>
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ─────────────────────────── helpers ───────────────────────────

function num(obj: unknown, path: (string | number)[]): number | null {
  let cur: unknown = obj;
  for (const key of path) {
    if (cur === null || cur === undefined) return null;
    if (typeof key === 'number') {
      if (!Array.isArray(cur)) return null;
      cur = cur[key];
    } else if (typeof cur === 'object') {
      cur = (cur as Record<string, unknown>)[key];
    } else {
      return null;
    }
  }
  return typeof cur === 'number' ? cur : null;
}

function formatKpi(kind: KpiRow['format'], v: number): string {
  switch (kind) {
    case 'pct':
      return `${(v * 100).toFixed(1)}%`;
    case 'multiple':
      return `${v.toFixed(2)}x`;
    case 'ratio':
      return v.toFixed(2);
    case 'usd':
      if (Math.abs(v) >= 1_000_000) {
        return `$${(v / 1_000_000).toFixed(2)}M`;
      }
      if (Math.abs(v) >= 1_000) {
        return `$${(v / 1_000).toFixed(0)}K`;
      }
      return `$${v.toFixed(0)}`;
  }
}
