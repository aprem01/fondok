/**
 * StatementTable — the dark-navy statement grid used by Cash Flow / Financials /
 * Index Analysis / Scenarios. Extracted EXACTLY from the grid in
 * `design/canonical/Cash Flow Tab.dc.html` (lines ~107-123) and the dense
 * "colour-only" grid in `Fondok Data Key.dc.html` (lines ~96-114).
 *
 * Chrome:
 *   header row → #14213d navy bg · #c9cede text · LINE ITEM 11px/600 sticky-left ·
 *                data cols 10.5px/600 right-aligned · 1px #2a3a5c left divider
 *   label cell → 6px 14px · 12px · #1a2233 · 1px #f7f6f3 bottom hairline · sticky
 *                left · optional 8px provenance dot + label · indent per row
 *   data cell  → 6px 12px · 12px tabular-nums · right-aligned · 1px #f7f6f3
 *                bottom + left hairlines · colour + weight carry origin/total
 *
 * Two tiers (per the Data Key "when there is no room for a dot" section):
 *   showDots=true  → Tier 2: dot beside the label (Cash Flow rows)
 *   showDots=false → Tier 3: no dot, the value's own colour carries origin
 *
 * Card-less: compose inside a <SectionCard variant="title">. Presentational.
 */

import type { CSSProperties, ReactNode } from 'react';
import type { ValueState } from '@/lib/api';
import { palette, prov } from './tokens';
import { ProvenanceDot } from './ProvenanceDot';

/** Dense colour-only value color for a state (Data Key `denseLegend`). */
export function denseValueColor(state: ValueState): string {
  switch (state) {
    case 'document_sourced':
    case 'linked':
    case 'needs_review':
      return prov.green; // "Sourced" — the popover names doc vs linked
    case 'assumption':
      return prov.blue;
    case 'calculated':
      return prov.gray;
    case 'awaiting_data':
      return prov.muted;
    default:
      return prov.black;
  }
}

export interface StatementCell {
  text: ReactNode;
  /** Explicit color (Cash Flow passes this). */
  color?: string;
  /** Or derive the colour-only color from a state (dense Tier-3 grids). */
  state?: ValueState;
  /** Amber needs-review cell tint. */
  reviewTint?: boolean;
  /** Dotted-underline (editable) affordance. */
  editable?: boolean;
}

export interface StatementRow {
  label: ReactNode;
  cells: StatementCell[];
  /** Origin dot rendered beside the label when `showDots`. */
  state?: ValueState;
  /** Total rows render bold near-black. */
  total?: boolean;
  /** Left indent (px) for nested line items. */
  indent?: number;
  /** Row background (e.g. subtotal band). */
  bg?: string;
  /** Hover title on the label. */
  title?: string;
}

export interface StatementTableProps {
  /** Period column headers, e.g. ['FY24A','FY25A','FY26E']. */
  columns: string[];
  rows: StatementRow[];
  /** First (sticky) column header label. */
  lineItemHeader?: string;
  /** Tier 2 (dots) vs Tier 3 (colour-only). Default true. */
  showDots?: boolean;
  /** CSS grid template; defaults to a sticky label col + N metric cols. */
  gridTemplateColumns?: string;
  footnote?: ReactNode;
  className?: string;
  style?: CSSProperties;
}

const navyHeader: CSSProperties = {
  background: palette.inkNavy,
  color: palette.gridHeaderText,
  fontWeight: 600,
};

export function StatementTable({
  columns,
  rows,
  lineItemHeader = 'LINE ITEM',
  showDots = true,
  gridTemplateColumns,
  footnote,
  className,
  style,
}: StatementTableProps) {
  const cols =
    gridTemplateColumns ?? `minmax(180px,1.4fr) repeat(${columns.length}, minmax(96px,1fr))`;

  return (
    <div className={className} style={style}>
      <div style={{ overflowX: 'auto' }}>
        <div
          role="table"
          style={{
            display: 'grid',
            gridTemplateColumns: cols,
            width: 'max-content',
            minWidth: '100%',
          }}
        >
          {/* Header */}
          <div
            role="columnheader"
            style={{
              ...navyHeader,
              padding: '7px 14px',
              fontSize: 11,
              position: 'sticky',
              left: 0,
              zIndex: 2,
            }}
          >
            {lineItemHeader}
          </div>
          {columns.map((h, i) => (
            <div
              key={i}
              role="columnheader"
              style={{
                ...navyHeader,
                padding: '7px 12px',
                fontSize: 10.5,
                textAlign: 'right',
                borderLeft: `1px solid ${palette.gridHeaderDivider}`,
                whiteSpace: 'nowrap',
              }}
            >
              {h}
            </div>
          ))}

          {/* Body */}
          {rows.map((row, ri) => {
            const weight = row.total ? 700 : 400;
            const rowBg = row.bg ?? 'transparent';
            return (
              <div key={ri} style={{ display: 'contents' }}>
                <div
                  role="rowheader"
                  title={row.title}
                  style={{
                    padding: '6px 14px',
                    paddingLeft: row.indent != null ? row.indent : undefined,
                    borderBottom: `1px solid ${palette.hairlineRow}`,
                    fontSize: 12,
                    color: palette.ink,
                    fontWeight: weight,
                    background: rowBg,
                    position: 'sticky',
                    left: 0,
                    zIndex: 1,
                    whiteSpace: 'nowrap',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 7,
                  }}
                >
                  {showDots && row.state && <ProvenanceDot state={row.state} size={8} />}
                  <span>{row.label}</span>
                </div>
                {row.cells.map((c, ci) => {
                  const color =
                    c.color ?? (c.state ? denseValueColor(c.state) : row.total ? prov.black : palette.ink);
                  return (
                    <div
                      key={ci}
                      role="cell"
                      style={{
                        padding: '6px 12px',
                        borderBottom: `1px solid ${palette.hairlineRow}`,
                        borderLeft: `1px solid ${palette.hairlineRow}`,
                        textAlign: 'right',
                        fontSize: 12,
                        fontVariantNumeric: 'tabular-nums',
                        color,
                        fontWeight: weight,
                        background: c.reviewTint ? prov.reviewCellTint : rowBg,
                        textDecoration: c.editable ? 'underline dotted' : undefined,
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {c.text}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>
      {footnote != null && (
        <div style={{ padding: '11px 18px', fontSize: 11, color: palette.textMuted, lineHeight: 1.5 }}>
          {footnote}
        </div>
      )}
    </div>
  );
}
