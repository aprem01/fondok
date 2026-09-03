'use client';
/**
 * ScenarioComparePanel — the tab body of Scenario Analysis (FON-53), below the
 * two-variable sensitivity grid. It renders the canonical
 * "Scenario comparison — multi-variable saved cases" experience:
 *
 *   1. a focus chip row (Base + saved scenarios), Base carrying the
 *      "SOURCE OF TRUTH" badge — the canonical Base-is-canonical rule;
 *   2. a read-only Base-case-assumptions panel (when Base is focused) OR an
 *      inline per-scenario override table (Assumption / Base / Scenario /
 *      Change / Source →) when a saved scenario is focused;
 *   3. the side-by-side compare table (up to 4 scenarios) with per-column
 *      "% delta vs Base" and an "Add to memo" action that persists the
 *      IC-memo comparison set.
 *
 * Base always reads the canonical run — the worker resolves it; nothing here
 * mutates the Base underwriting. Editing a scenario's overrides happens in the
 * ScenarioEditor drawer (opened from the ScenarioSelector pill row); this panel
 * is a read-only comparison lens.
 *
 * ── Add to memo persistence ──────────────────────────────────────────────
 * There is NO worker endpoint for an IC-memo scenario comparison set today
 * (only /memo/generate + /memo/{section}/edits). Per the build brief we do NOT
 * edit the worker: the set is persisted device-local (localStorage), matching
 * the established interim pattern in `useWorksheetLayout`. BACKEND FOLLOW-UP:
 * add a first-class `POST /deals/{id}/memo/scenarios` (or an `in_memo` flag on
 * the scenario record) and swap `readMemoSet`/`writeMemoSet` for the round-trip.
 */
import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, Check, FileText, Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import {
  api,
  type EngineName,
  type EngineOutputsResponse,
  type ScenarioCompareResponse,
  type ScenarioRecord,
} from '@/lib/api';
import { cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { ProvenanceDot } from '@/components/design';
import {
  CATALOG_BY_PATH,
  fmtAssumption,
  fmtAssumptionChange,
  labelForPath,
} from './ScenarioEditor';

interface Props {
  dealId: string;
  scenarios: ScenarioRecord[];
}

// Canonical palette (Scenarios Tab.dc.html).
const C = {
  cardBorder: '#e6e5e0',
  hairline: '#f2f1ec',
  rowLine: '#f7f6f3',
  eyebrow: '#8a8a86',
  ink: '#1a2233',
  muted: '#9a9a95',
  faint: '#b0afaa',
  label: '#7c8088',
  link: '#2f4a8c',
  activeBorder: '#14213d',
  activeBg: '#f3f4f7',
  chipBorder: '#e2e1dc',
  badgeBg: '#f3f2ee',
  blue: 'oklch(45% 0.14 260)',
} as const;

interface KpiRow {
  key: string;
  label: string;
  format: 'pct' | 'multiple' | 'usd' | 'ratio';
  pick: (engines: Record<string, EnginePayload>) => number | null;
}

interface EnginePayload {
  status: string;
  outputs?: unknown;
  summary?: string;
}

const KPI_ROWS: KpiRow[] = [
  { key: 'irr', label: 'Levered IRR', format: 'pct', pick: (e) => num(e.returns?.outputs, ['levered_irr']) },
  { key: 'em', label: 'Equity Multiple', format: 'multiple', pick: (e) => num(e.returns?.outputs, ['equity_multiple']) },
  { key: 'noi_y1', label: 'NOI Y1', format: 'usd', pick: (e) => num(e.expense?.outputs, ['years', 0, 'noi']) },
  { key: 'noi_y5', label: 'NOI Y5', format: 'usd', pick: (e) => num(e.expense?.outputs, ['years', 4, 'noi']) },
  {
    key: 'cap',
    label: 'Exit Cap Rate',
    format: 'pct',
    pick: (e) =>
      num(e.returns?.outputs, ['exit_cap_rate']) ?? num(e.sensitivity?.outputs, ['exit_cap_rate']),
  },
  {
    key: 'dscr',
    label: 'Avg DSCR',
    format: 'ratio',
    pick: (e) => num(e.debt?.outputs, ['avg_dscr']) ?? num(e.debt?.outputs, ['dscr']),
  },
  {
    key: 'total_cost',
    label: 'Total Project Cost',
    format: 'usd',
    pick: (e) =>
      num(e.capital?.outputs, ['total_project_cost']) ?? num(e.capital?.outputs, ['total_cost']),
  },
];

export default function ScenarioComparePanel({ dealId, scenarios }: Props) {
  const { toast } = useToast();
  const { outputs } = useEngineOutputs(dealId);

  const base = useMemo(() => scenarios.find((s) => s.is_base) ?? null, [scenarios]);
  const named = useMemo(() => scenarios.filter((s) => !s.is_base), [scenarios]);

  // Which scenario's detail panel is shown (Base panel vs override table).
  const [focusedId, setFocusedId] = useState<string | null>(base?.id ?? null);
  useEffect(() => {
    if (!focusedId || !scenarios.some((s) => s.id === focusedId)) {
      setFocusedId(base?.id ?? null);
    }
  }, [scenarios, base, focusedId]);
  const focused = scenarios.find((s) => s.id === focusedId) ?? base;

  // IC-memo comparison set (device-local — see file header BACKEND FOLLOW-UP).
  const [memoIds, setMemoIds] = useState<string[]>([]);
  useEffect(() => {
    setMemoIds(readMemoSet(dealId));
  }, [dealId]);
  function toggleMemo(scenario: ScenarioRecord) {
    setMemoIds((prev) => {
      const has = prev.includes(scenario.id);
      const next = has ? prev.filter((x) => x !== scenario.id) : [...prev, scenario.id];
      writeMemoSet(dealId, next);
      toast(
        has
          ? `Removed “${scenario.name}” from the IC memo comparison set.`
          : `Added “${scenario.name}” to the IC memo comparison set.`,
        { type: has ? 'info' : 'success' },
      );
      return next;
    });
  }

  // Compare-table selection (base + first two named by default; up to 4).
  const defaultIds = useMemo(() => {
    const others = named.slice(0, 3);
    return [base?.id, ...others.map((s) => s.id)].filter(
      (x): x is string => typeof x === 'string',
    );
  }, [base, named]);

  const [selectedIds, setSelectedIds] = useState<string[]>(defaultIds);
  const [data, setData] = useState<ScenarioCompareResponse | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setSelectedIds(defaultIds);
  }, [defaultIds]);

  const canCompare = scenarios.length >= 2;

  useEffect(() => {
    if (!canCompare || selectedIds.length === 0) {
      setData(null);
      return;
    }
    const ac = new AbortController();
    setLoading(true);
    api.scenarios
      .compare(dealId, selectedIds)
      .then((res) => setData(res))
      .catch((e) => {
        toast(`Compare failed: ${e instanceof Error ? e.message : 'Unknown error'}`, {
          type: 'error',
        });
      })
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [dealId, selectedIds, canCompare, toast]);

  return (
    <div className="space-y-4">
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: '.06em',
          color: C.eyebrow,
          textTransform: 'uppercase',
        }}
      >
        Scenario comparison — multi-variable saved cases
      </div>

      {/* Focus chip row — Base carries the SOURCE OF TRUTH badge. */}
      <div className="flex flex-wrap items-center gap-2">
        <FocusChip
          scenario={base}
          label="Base"
          isBase
          active={focused?.id === base?.id}
          onClick={() => base && setFocusedId(base.id)}
        />
        {named.map((s) => (
          <FocusChip
            key={s.id}
            scenario={s}
            label={s.name}
            active={focused?.id === s.id}
            onClick={() => setFocusedId(s.id)}
          />
        ))}
        <span
          style={{ fontSize: 11, color: C.muted, marginLeft: 'auto' }}
          className="whitespace-nowrap"
        >
          Comparing Base + {Math.max(0, selectedIds.length - 1)} of {named.length} saved
        </span>
      </div>

      {/* Detail: Base-case assumptions (read-only) OR override table. */}
      {focused?.is_base ? (
        <BaseCasePanel outputs={outputs} />
      ) : focused ? (
        <OverrideTable scenario={focused} base={base} outputs={outputs} />
      ) : null}

      {/* Compare table. */}
      {canCompare ? (
        <div className="space-y-3">
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
              <CompareTable
                response={data}
                baseId={data.base_scenario_id}
                memoIds={memoIds}
                scenarios={scenarios}
                onToggleMemo={toggleMemo}
              />
            )}
          </Card>
        </div>
      ) : (
        <Card className="p-6 text-[13px] text-ink-700">
          <div className="flex items-start gap-2">
            <FileText size={16} className="text-ink-500 mt-0.5" aria-hidden="true" />
            <div>
              <p className="font-semibold mb-1">Add a scenario to compare.</p>
              <p className="text-[12.5px] text-ink-600">
                Every deal starts with a single Base case. Once you save a downside or
                upside scenario, this panel renders the side-by-side IRR / EM / NOI
                deltas against Base.
              </p>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

// ─────────────────────────── focus chips ───────────────────────────

interface FocusChipProps {
  scenario: ScenarioRecord | null;
  label: string;
  isBase?: boolean;
  active: boolean;
  onClick: () => void;
}

function FocusChip({ scenario, label, isBase = false, active, onClick }: FocusChipProps) {
  if (!scenario) return null;
  const count = scenario.overrides.length;
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 7,
        border: `1px solid ${active ? C.activeBorder : C.chipBorder}`,
        background: active ? C.activeBg : '#fff',
        borderRadius: 6,
        padding: '5px 8px 5px 11px',
      }}
    >
      <button
        type="button"
        onClick={onClick}
        aria-pressed={active}
        style={{
          fontSize: 12.5,
          fontWeight: active ? 700 : 500,
          color: active ? C.activeBorder : '#6b6f76',
          background: 'transparent',
          border: 'none',
          padding: 0,
          cursor: 'pointer',
          whiteSpace: 'nowrap',
          fontFamily: 'inherit',
        }}
      >
        {label}
      </button>
      {isBase ? (
        <span
          title="The canonical underwriting — edited in Financials, Investment, Debt and Partnership"
          style={{
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: '.06em',
            color: C.eyebrow,
            background: C.badgeBg,
            borderRadius: 3,
            padding: '2px 5px',
            textTransform: 'uppercase',
          }}
        >
          SOURCE OF TRUTH
        </span>
      ) : (
        <span style={{ fontSize: 10.5, color: C.muted, whiteSpace: 'nowrap' }}>
          {count} override{count === 1 ? '' : 's'}
        </span>
      )}
    </span>
  );
}

// ─────────────────────── base-case assumptions ─────────────────────

function BaseCasePanel({ outputs }: { outputs: EngineOutputsResponse | null }) {
  const groups = [
    {
      title: 'Operating',
      link: 'Financials →',
      rows: [
        { label: 'RevPAR Growth', value: dash('revpar_growth', baseValueFor('revpar_growth', outputs)) },
        { label: 'Expense Growth', value: dash('expense_growth', baseValueFor('expense_growth', outputs)) },
      ],
    },
    {
      title: 'Financing',
      link: 'Debt →',
      rows: [
        { label: 'LTV', value: dash('ltv', baseValueFor('ltv', outputs)) },
        { label: 'Interest Rate', value: dash('interest_rate', baseValueFor('interest_rate', outputs)) },
        { label: 'Amortization / Term', value: amortTerm(outputs) },
      ],
    },
    {
      title: 'Investment & Exit',
      link: 'Investment →',
      rows: [
        { label: 'Purchase Price', value: dash('purchase_price', baseValueFor('purchase_price', outputs)) },
        { label: 'Hold Period', value: dash('hold_years', baseValueFor('hold_years', outputs)) },
        { label: 'Exit Cap Rate', value: dash('exit_cap_rate', baseValueFor('exit_cap_rate', outputs)) },
      ],
    },
  ];

  return (
    <div
      style={{ background: '#fff', border: `1px solid ${C.cardBorder}`, borderRadius: 10 }}
    >
      <div
        style={{
          padding: '9px 14px',
          borderBottom: `1px solid ${C.hairline}`,
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          gap: 12,
          flexWrap: 'wrap',
        }}
      >
        <span
          style={{
            fontSize: 10.5,
            fontWeight: 700,
            letterSpacing: '.06em',
            color: C.eyebrow,
            textTransform: 'uppercase',
          }}
        >
          Base case assumptions
        </span>
        <span style={{ fontSize: 10.5, color: C.faint }}>
          Read-only · sourced from the underwriting model
        </span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-3">
        {groups.map((g, i) => (
          <div
            key={g.title}
            style={{ padding: '10px 16px 12px', borderLeft: i === 0 ? 'none' : `1px solid ${C.hairline}` }}
          >
            <div
              style={{
                fontSize: 9.5,
                fontWeight: 700,
                letterSpacing: '.07em',
                color: C.faint,
                textTransform: 'uppercase',
                marginBottom: 2,
              }}
            >
              {g.title}
            </div>
            {g.rows.map((r) => (
              <div
                key={r.label}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: 12,
                  fontSize: 12.5,
                  height: 34,
                  borderBottom: `1px solid ${C.rowLine}`,
                }}
              >
                <span style={{ color: C.label }}>{r.label}</span>
                <span
                  style={{ color: C.ink, fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}
                >
                  {r.value}
                </span>
              </div>
            ))}
            <div style={{ fontSize: 11, color: C.link, fontWeight: 600, paddingTop: 9 }}>
              {g.link}
            </div>
          </div>
        ))}
      </div>
      <div
        style={{
          padding: '8px 14px',
          borderTop: `1px solid ${C.hairline}`,
          fontSize: 10.5,
          color: C.faint,
          lineHeight: 1.5,
        }}
      >
        Base Case is read-only here. Update assumptions in their source tabs; saved
        scenarios automatically re-run against the updated Base.
      </div>
    </div>
  );
}

// ─────────────────────── per-scenario override table ───────────────

function OverrideTable({
  scenario,
  base,
  outputs,
}: {
  scenario: ScenarioRecord;
  base: ScenarioRecord | null;
  outputs: EngineOutputsResponse | null;
}) {
  const count = scenario.overrides.length;
  const subtitle =
    `${count} override${count === 1 ? '' : 's'} from Base` +
    (scenario.description ? ` · ${scenario.description}` : '');
  const cols =
    'minmax(150px,1.5fr) minmax(80px,1fr) minmax(80px,1fr) minmax(80px,1fr) minmax(90px,1fr)';

  return (
    <div style={{ background: '#fff', border: `1px solid ${C.cardBorder}`, borderRadius: 8 }}>
      <div
        style={{
          padding: '12px 16px',
          borderBottom: `1px solid ${C.hairline}`,
          display: 'flex',
          flexDirection: 'column',
          gap: 2,
        }}
      >
        <span style={{ fontSize: 13.5, fontWeight: 700, color: C.ink }}>{scenario.name}</span>
        <span style={{ fontSize: 11.5, color: C.muted }}>{subtitle}</span>
      </div>
      <div style={{ padding: '10px 16px 4px' }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: cols,
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '.06em',
            color: C.faint,
            textTransform: 'uppercase',
            paddingBottom: 7,
            borderBottom: `1px solid ${C.chipBorder}`,
          }}
        >
          <span>Assumption</span>
          <span style={{ textAlign: 'right' }}>Base</span>
          <span style={{ textAlign: 'right' }}>Scenario</span>
          <span style={{ textAlign: 'right' }}>Change</span>
          <span style={{ textAlign: 'right' }}>Source</span>
        </div>
        {count === 0 && (
          <div style={{ padding: '14px 0', fontSize: 12.5, color: C.muted }}>
            No overrides yet — this scenario is identical to Base. Add one with “Edit
            scenario” on the pill above.
          </div>
        )}
        {scenario.overrides.map((o) => {
          const baseVal = baseValueFor(o.field_path, outputs, base);
          const scenVal = o.value;
          const numericBoth = typeof baseVal === 'number' && typeof scenVal === 'number';
          const meta = CATALOG_BY_PATH.get(o.field_path);
          return (
            <div
              key={o.field_path}
              style={{
                display: 'grid',
                gridTemplateColumns: cols,
                fontSize: 12.5,
                padding: '7px 0',
                borderBottom: `1px solid ${C.rowLine}`,
                alignItems: 'center',
              }}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                <ProvenanceDot state="assumption" size={8} />
                <span style={{ color: C.ink }}>{labelForPath(o.field_path)}</span>
              </span>
              <span
                style={{ textAlign: 'right', color: C.muted, fontVariantNumeric: 'tabular-nums' }}
              >
                {baseVal == null ? '—' : fmtAssumption(o.field_path, baseVal)}
              </span>
              <span
                style={{
                  textAlign: 'right',
                  color: C.blue,
                  fontWeight: 700,
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {fmtAssumption(o.field_path, scenVal)}
              </span>
              <span
                style={{
                  textAlign: 'right',
                  color: numericBoth ? '#5f656e' : C.faint,
                  fontVariantNumeric: 'tabular-nums',
                }}
              >
                {numericBoth
                  ? fmtAssumptionChange(o.field_path, baseVal as number, scenVal as number)
                  : '—'}
              </span>
              <span style={{ textAlign: 'right', color: C.link, fontSize: 11.5 }}>
                {(meta?.sourceLabel ?? o.source ?? '—') + ' →'}
              </span>
            </div>
          );
        })}
      </div>
      <div style={{ padding: '10px 16px', fontSize: 11, color: C.muted, lineHeight: 1.5 }}>
        Overrides apply only to this scenario. Everything not listed here is inherited
        from Base.
      </div>
    </div>
  );
}

// ─────────────────────────── compare table ─────────────────────────

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
      <span className="text-[11px] text-ink-400 ml-1">· deltas vs Base</span>
    </div>
  );
}

interface TableProps {
  response: ScenarioCompareResponse;
  baseId: string | null;
  memoIds: string[];
  scenarios: ScenarioRecord[];
  onToggleMemo: (scenario: ScenarioRecord) => void;
}

function CompareTable({ response, baseId, memoIds, scenarios, onToggleMemo }: TableProps) {
  const baseCell = response.scenarios.find((c) => c.scenario_id === baseId);
  const byId = new Map(scenarios.map((s) => [s.id, s]));

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-[12.5px]">
        <thead className="bg-surface-muted border-b border-border">
          <tr>
            <th className="text-left px-4 py-2 font-semibold text-ink-700">KPI</th>
            {response.scenarios.map((cell) => (
              <th
                key={cell.scenario_id}
                className="text-right px-4 py-2 font-semibold text-ink-900"
              >
                <div className="flex items-center justify-end gap-1">
                  {cell.is_base && cell.scenario_name.toLowerCase() !== 'base' && (
                    <span className="text-[10px] uppercase tracking-wide bg-ink-100 text-ink-600 px-1 rounded">
                      Base
                    </span>
                  )}
                  <span>{cell.scenario_name}</span>
                  {memoIds.includes(cell.scenario_id) && (
                    <span
                      title="In the IC memo comparison set"
                      className="text-[9px] uppercase tracking-wide bg-emerald-50 text-emerald-700 px-1 rounded"
                    >
                      In memo
                    </span>
                  )}
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
                  const v = row.pick(cell.engines as Record<string, EnginePayload>);
                  const delta =
                    baseVal !== null && v !== null && !cell.is_base
                      ? (v - baseVal) / Math.max(Math.abs(baseVal), 1e-9)
                      : null;
                  return (
                    <td key={cell.scenario_id} className="px-4 py-2 text-right tabular-nums">
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
            {response.scenarios.map((cell) => {
              const scenario = byId.get(cell.scenario_id);
              const inMemo = memoIds.includes(cell.scenario_id);
              return (
                <td key={cell.scenario_id} className="px-4 py-3 text-right">
                  <Button
                    variant={inMemo ? 'secondary' : 'ghost'}
                    size="sm"
                    disabled={!scenario}
                    onClick={() => scenario && onToggleMemo(scenario)}
                  >
                    {inMemo ? (
                      <Check size={11} aria-hidden="true" />
                    ) : (
                      <FileText size={11} aria-hidden="true" />
                    )}
                    <span className="ml-1">{inMemo ? 'In memo' : 'Add to memo'}</span>
                    {!inMemo && <ArrowRight size={11} className="ml-1" aria-hidden="true" />}
                  </Button>
                </td>
              );
            })}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ─────────────────────────── helpers ───────────────────────────

/** Resolve a field's Base value from the canonical run, then the base
 *  scenario's explicit overrides, else undefined ("—"). */
function baseValueFor(
  path: string,
  outputs: EngineOutputsResponse | null,
  base?: ScenarioRecord | null,
): number | undefined {
  const fromRun = BASE_RUN_RESOLVERS[path]?.(outputs);
  if (typeof fromRun === 'number' && Number.isFinite(fromRun)) return fromRun;
  const bo = base?.overrides.find((o) => o.field_path === path);
  if (bo && typeof bo.value === 'number' && Number.isFinite(bo.value)) return bo.value;
  return undefined;
}

const BASE_RUN_RESOLVERS: Record<
  string,
  (o: EngineOutputsResponse | null) => number | undefined
> = {
  exit_cap_rate: (o) =>
    getEngineField<number>(o, 'returns', 'exit_cap_rate') ??
    engineInputNum(o, 'returns', 'assumptions', 'exit_cap_rate'),
  interest_rate: (o) =>
    getEngineField<number>(o, 'debt', 'interest_rate') ??
    engineInputNum(o, 'returns', 'assumptions', 'interest_rate'),
  ltv: (o) =>
    getEngineField<number>(o, 'capital', 'ltv') ??
    engineInputNum(o, 'returns', 'assumptions', 'ltv'),
  purchase_price: (o) => getEngineField<number>(o, 'capital', 'purchase_price'),
  hold_years: (o) =>
    getEngineField<number>(o, 'returns', 'hold_years') ??
    engineInputNum(o, 'returns', 'assumptions', 'hold_years'),
  amortization_years: (o) => getEngineField<number>(o, 'debt', 'amortization_years'),
  term_years: (o) => getEngineField<number>(o, 'debt', 'term_years'),
  revpar_growth: (o) => engineInputNum(o, 'returns', 'assumptions', 'revpar_growth'),
  expense_growth: (o) => engineInputNum(o, 'expense', 'assumptions', 'expense_growth'),
};

function dash(path: string, v: number | undefined): string {
  return v == null ? '—' : fmtAssumption(path, v);
}

function amortTerm(outputs: EngineOutputsResponse | null): string {
  const amort = baseValueFor('amortization_years', outputs);
  const term = baseValueFor('term_years', outputs);
  if (amort == null && term == null) return '—';
  const a = amort == null ? '—' : `${amort} yrs`;
  const t = term == null ? '—' : `${term} yrs`;
  return `${a} / ${t}`;
}

/** Walk an engine row's `inputs` (optionally an `assumptions` sub-object). */
function engineInputNum(
  outputs: EngineOutputsResponse | null,
  engine: EngineName,
  ...path: string[]
): number | undefined {
  const row = outputs?.engines?.[engine];
  return numAt(row?.inputs, path);
}

function numAt(root: unknown, path: string[]): number | undefined {
  let cur: unknown = root;
  for (const p of path) {
    if (cur && typeof cur === 'object' && p in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[p];
    } else {
      return undefined;
    }
  }
  return typeof cur === 'number' && Number.isFinite(cur) ? cur : undefined;
}

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
      if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
      if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
      return `$${v.toFixed(0)}`;
  }
}

// ── IC-memo comparison set (device-local; see file-header BACKEND FOLLOW-UP) ──
const memoKey = (dealId: string) => `fondok:memoScenarios:${dealId}`;

function readMemoSet(dealId: string): string[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(memoKey(dealId));
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

function writeMemoSet(dealId: string, ids: string[]): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(memoKey(dealId), JSON.stringify(ids));
  } catch {
    /* quota / private mode — the set just won't persist */
  }
}
