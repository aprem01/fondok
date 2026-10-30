'use client';

/**
 * Returns by exit year (FON-118 / FON-44) — the returns engine's exit
 * schedule as a table, on the Returns Summary and (compact) on the IC memo.
 *
 * Every figure is one of the engine's `exit_schedule[i]` fields; nothing is
 * computed here. The selected row IS the headline, so its Levered IRR text is
 * the hero tile's text. A `not_available` row renders "—" in every figure
 * cell with the reason's label (from the registry, or the raw code when this
 * bundle predates it) and a title carrying the engine's detail — never 0.
 *
 * Look: the Returns Summary card chrome (`SectionCard`, hairline rows,
 * tabular numerals, the base-case tint the sensitivity grid already uses for
 * the highlighted cell). No new styling vocabulary.
 */

import type { ReactNode } from 'react';
import { fmtPct } from '@/lib/format';
import type { ReturnsExitScheduleRow } from '@/lib/api';
import {
  type ExitScheduleRow,
  EXIT_SCHEDULE_MISSING,
  EXIT_SCHEDULE_PEAK_BADGE,
  EXIT_SCHEDULE_SANDBOX_MISSING,
  EXIT_SCHEDULE_SELECTED_BADGE,
  EXIT_SCHEDULE_TITLE,
  IRR_SENTINEL_NOTE,
  OFF_CYCLE_EXIT_NOTE,
  exitRowCalendarYear,
  exitRowLabel,
  exitRowReasonLabel,
  exitRowReasonTitle,
  irrValue,
} from '@/lib/engines/returns';
import { Traced } from '@/components/help/Traced';
import { SectionCard, palette, prov } from '@/components/design';

const mm = (v: number) => `${v < 0 ? '−$' : '$'}${(Math.abs(v) / 1e6).toFixed(2)}M`;

/** The highlighted-cell tint the Sensitivities grid uses for the base case. */
const SELECTED_TINT = 'oklch(97% 0.03 250)';

type Col = {
  key: string;
  label: string;
  /** Which engine field the cell's provenance key names (computed rows). */
  field?: keyof ReturnsExitScheduleRow;
  render: (row: ReturnsExitScheduleRow) => { text: string; title?: string; sub?: string };
  compactHidden?: boolean;
};

const irrCell = (field: 'levered_irr' | 'unlevered_irr') => (row: ReturnsExitScheduleRow) => {
  const raw = row[field];
  const v = irrValue(raw);
  if (v == null) {
    return { text: '—', title: typeof raw === 'number' ? IRR_SENTINEL_NOTE : `returns.exit_schedule[].${field} is not on this row` };
  }
  return { text: fmtPct(v, 2) };
};

const moneyCell = (field: 'gross_sale_price' | 'loan_payoff' | 'net_proceeds') => (row: ReturnsExitScheduleRow) => {
  const v = row[field];
  return typeof v === 'number' && Number.isFinite(v)
    ? { text: mm(v), sub: field === 'loan_payoff' && row.refi_included ? 'after refinance' : undefined }
    : { text: '—', title: `returns.exit_schedule[].${field} is not on this row` };
};

const COLUMNS: Col[] = [
  {
    key: 'exit_noi', label: 'Exit NOI', field: 'terminal_noi',
    render: (row) =>
      typeof row.terminal_noi === 'number' && Number.isFinite(row.terminal_noi)
        ? { text: mm(row.terminal_noi), sub: row.exit_noi_period_label || undefined }
        : { text: '—', title: 'returns.exit_schedule[].terminal_noi is not on this row' },
  },
  { key: 'gross', label: 'Gross sale', field: 'gross_sale_price', render: moneyCell('gross_sale_price'), compactHidden: true },
  { key: 'payoff', label: 'Payoff', field: 'loan_payoff', render: moneyCell('loan_payoff'), compactHidden: true },
  { key: 'net', label: 'Net proceeds', field: 'net_proceeds', render: moneyCell('net_proceeds') },
  { key: 'unlevered', label: 'Unlevered IRR', field: 'unlevered_irr', render: irrCell('unlevered_irr') },
  { key: 'levered', label: 'Levered IRR', field: 'levered_irr', render: irrCell('levered_irr') },
  {
    key: 'em', label: 'EM', field: 'equity_multiple',
    render: (row) =>
      typeof row.equity_multiple === 'number' && Number.isFinite(row.equity_multiple)
        ? { text: `${row.equity_multiple.toFixed(2)}x` }
        : { text: '—', title: 'returns.exit_schedule[].equity_multiple is not on this row' },
  },
];

/** Provenance keys the worker writes — bracket form, exact string. */
const TRACED_FIELDS = new Set<keyof ReturnsExitScheduleRow>([
  'levered_irr', 'unlevered_irr', 'equity_multiple', 'terminal_noi', 'gross_sale_price', 'net_proceeds',
]);

function Pill({ children, tone }: { children: ReactNode; tone: 'selected' | 'peak' }) {
  const selected = tone === 'selected';
  return (
    <span
      style={{
        fontSize: 9.5,
        fontWeight: 700,
        letterSpacing: '.06em',
        textTransform: 'uppercase',
        padding: '2px 6px',
        borderRadius: 999,
        whiteSpace: 'nowrap',
        color: selected ? palette.linkBlue : prov.green,
        background: selected ? SELECTED_TINT : 'oklch(96% 0.03 155)',
        border: `1px solid ${selected ? '#c9d4ee' : 'oklch(85% 0.06 155)'}`,
      }}
    >
      {children}
    </span>
  );
}

export function ExitScheduleTable({
  rows,
  calendarYears,
  compact = false,
  traced = true,
}: {
  rows: ExitScheduleRow[];
  /** `revenue.projection_calendar_years` — set only with a close date. */
  calendarYears?: readonly number[] | null;
  /** IC memo: fewer columns, tighter type. */
  compact?: boolean;
  /** Wrap computed figures in <Traced> (off while a sandbox preview shows). */
  traced?: boolean;
}) {
  const cols = compact ? COLUMNS.filter((c) => !c.compactHidden) : COLUMNS;
  const fontSize = compact ? 11.5 : 12.5;
  const headStyle: React.CSSProperties = {
    padding: compact ? '5px 10px' : '7px 12px',
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: '.04em',
    textTransform: 'uppercase',
    color: palette.eyebrow,
    textAlign: 'right',
    whiteSpace: 'nowrap',
    borderBottom: `1px solid ${palette.hairlineRow}`,
  };
  const cellStyle = (selected: boolean): React.CSSProperties => ({
    padding: compact ? '5px 10px' : '7px 12px',
    fontSize,
    fontVariantNumeric: 'tabular-nums',
    textAlign: 'right',
    whiteSpace: 'nowrap',
    verticalAlign: 'top',
    color: selected ? prov.black : prov.gray,
    fontWeight: selected ? 700 : 400,
    borderBottom: `1px solid ${palette.hairlineRow}`,
  });

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: compact ? 520 : 760 }}>
        <thead>
          <tr>
            <th scope="col" style={{ ...headStyle, textAlign: 'left' }}>Exit</th>
            {cols.map((c) => (
              <th key={c.key} scope="col" style={headStyle}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const label = exitRowLabel(row);
            const cal = exitRowCalendarYear(row, calendarYears);
            const unavailable = row.status === 'not_available';
            const reasonLabel = exitRowReasonLabel(row);
            const reasonTitle = exitRowReasonTitle(row) ?? undefined;
            return (
              <tr
                key={`${row.exit_month}-${row.exit_year ?? 'off'}`}
                data-exit-row={label}
                data-exit-month={row.exit_month}
                data-exit-index={row.index}
                data-selected={row.is_selected ? 'true' : undefined}
                data-peak={row.is_peak_levered ? 'true' : undefined}
                data-reason={unavailable ? row.reason ?? 'not_available' : undefined}
                title={unavailable ? reasonTitle : undefined}
                style={{
                  background: row.is_selected ? SELECTED_TINT : 'transparent',
                  boxShadow: row.is_selected ? 'inset 2px 0 0 #2f4a8c' : undefined,
                }}
              >
                <th
                  scope="row"
                  style={{
                    ...cellStyle(row.is_selected),
                    textAlign: 'left',
                    color: palette.ink,
                    fontWeight: 600,
                  }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                    <span>
                      {label}
                      {cal != null && <span style={{ color: palette.textFaint, fontWeight: 400 }}> · {cal}</span>}
                    </span>
                    {row.is_selected && <Pill tone="selected">{EXIT_SCHEDULE_SELECTED_BADGE}</Pill>}
                    {row.is_peak_levered && <Pill tone="peak">{EXIT_SCHEDULE_PEAK_BADGE}</Pill>}
                  </span>
                  <span style={{ display: 'block', fontSize: 10.5, fontWeight: 400, color: palette.textFaint, marginTop: 2 }}>
                    {row.exit_year != null ? `month ${row.exit_month}` : OFF_CYCLE_EXIT_NOTE}
                  </span>
                </th>
                {cols.map((c, ci) => {
                  if (unavailable) {
                    return (
                      <td key={c.key} style={cellStyle(false)} title={reasonTitle}>
                        <span data-unavailable="true">—</span>
                        {ci === 0 && reasonLabel && (
                          <span
                            data-testid="exit-row-reason"
                            style={{ display: 'block', fontSize: 10.5, color: palette.textMuted, whiteSpace: 'normal', maxWidth: 220 }}
                          >
                            {reasonLabel}
                          </span>
                        )}
                      </td>
                    );
                  }
                  const cell = c.render(row);
                  const isDash = cell.text === '—';
                  const value = <span data-unavailable={isDash ? 'true' : undefined}>{cell.text}</span>;
                  const body =
                    traced && !isDash && c.field && TRACED_FIELDS.has(c.field) ? (
                      <Traced engine="returns" path={`exit_schedule[${row.index}].${c.field}`}>{value}</Traced>
                    ) : (
                      value
                    );
                  return (
                    <td key={c.key} style={cellStyle(row.is_selected)} title={cell.title}>
                      {body}
                      {cell.sub && (
                        <span style={{ display: 'block', fontSize: 10.5, fontWeight: 400, color: palette.textFaint, whiteSpace: 'normal', maxWidth: 220, marginLeft: 'auto' }}>
                          {cell.sub}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The Returns Summary card. `rows` undefined = the run carries no schedule
 * (the card names the missing field). While a sandbox preview is showing,
 * only the preview's own schedule is shown — never the canonical rows under
 * a sandbox case; a preview without one says so.
 */
export function ExitScheduleCard({
  rows,
  calendarYears,
  sandbox = false,
}: {
  rows: ExitScheduleRow[] | undefined;
  calendarYears?: readonly number[] | null;
  sandbox?: boolean;
}) {
  const missing = rows == null ? (sandbox ? EXIT_SCHEDULE_SANDBOX_MISSING : EXIT_SCHEDULE_MISSING) : null;
  return (
    <SectionCard
      title={EXIT_SCHEDULE_TITLE}
      note={
        sandbox
          ? 'Sandbox case · each projection year-end evaluated as a sale date'
          : 'Each projection year-end evaluated as a sale date · deal-level, before GP/LP allocation'
      }
      data-testid="exit-schedule-card"
    >
      {missing ? (
        <div
          title={missing}
          data-unavailable="true"
          style={{ fontSize: 12, color: palette.textMuted, lineHeight: 1.5, paddingTop: 2 }}
        >
          <span style={{ color: palette.textFaint, marginRight: 6 }}>—</span>
          <span>{missing}</span>
        </div>
      ) : (
        <>
          <div style={{ marginTop: 4 }}>
            <ExitScheduleTable rows={rows!} calendarYears={sandbox ? null : calendarYears} traced={!sandbox} />
          </div>
          <div style={{ fontSize: 11, color: palette.textMuted, marginTop: 9, lineHeight: 1.5 }}>
            One projection, moved only in sale date: each row sells at that year-end on the same operating
            model, with the loan balance at that date paid off and the refinance included only when it
            precedes the sale. The selected exit is the deal&apos;s own — the headline above. A dashed row
            names why no return is reported for that year.
          </div>
        </>
      )}
    </SectionCard>
  );
}
