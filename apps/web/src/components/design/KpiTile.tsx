/**
 * KpiTile — the summary metric tile used across the tabs.
 *
 * Source: the `summary` tiles in `design/canonical/Cash Flow Tab.dc.html`
 * (identical markup to the `kpis` tiles in `Investment Tab.dc.html`):
 *   card  → #fff · 1px #eae9e4 · radius 10 · padding 13px 15px
 *   label → 10px / 700 / .04em / uppercase / #8a8a86 · mb 6
 *   value → 19px / 700 / tabular-nums (ink by default)
 *   sub   → 10.5px / #9a9a95 · mt 4 · line-height 1.4
 *
 * Presentational + props-driven.
 */

import type { CSSProperties, ReactNode } from 'react';
import { palette, radius } from './tokens';

export interface KpiTileProps {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  /** Value color — defaults to ink navy; pass a provenance color for grounded tiles.
   *  Ignored by the `navy` variant, whose text is always white. */
  valueColor?: string;
  /**
   * Visual variant.
   *   `white` (default) — the standard #fff panel tile used across every tab.
   *   `navy`            — the filled dark hero tile from the Returns Summary
   *                       headline (canonical: the #14213d hero cards in
   *                       `design/canonical/Returns Tab.dc.html`), white text.
   */
  variant?: 'white' | 'navy';
  /**
   * FON-59 R-053 — reserve a fixed two-line label slot so every tile in a row
   * puts its primary number on the same baseline. Without it a label that
   * wraps ("TOTAL CAPITALIZATION" at the grid's 150px minimum) pushes its
   * value one line lower than its neighbours. Labels sit at the bottom of the
   * slot (hugging the number), and the value's size / weight / line-height are
   * pinned so the baselines match across the row. `white` variant only.
   */
  alignValues?: boolean;
  className?: string;
  style?: CSSProperties;
  /** Stable hook for tests that need to find one tile. */
  'data-testid'?: string;
}

/** Canonical label metrics (10px / line-height 1.3) — two lines reserved. */
const LABEL_LINE_HEIGHT = 1.3;
const LABEL_FONT_SIZE = 10;
const LABEL_SLOT_MIN_HEIGHT = Math.round(LABEL_FONT_SIZE * LABEL_LINE_HEIGHT * 2);

export function KpiTile({
  label,
  value,
  sub,
  valueColor = palette.ink,
  variant = 'white',
  alignValues = false,
  className,
  style,
  'data-testid': testId,
}: KpiTileProps) {
  if (variant === 'navy') {
    // Canonical navy hero tile (Returns Tab.dc.html `headline`): filled navy,
    // no border, larger value, white label/value with a muted-light sublabel.
    return (
      <div
        className={className}
        style={{
          background: palette.inkNavy,
          borderRadius: radius.card,
          padding: '15px 17px',
          color: '#fff',
          ...style,
        }}
      >
        <div
          style={{
            fontSize: 10,
            fontWeight: 700,
            letterSpacing: '.05em',
            color: '#8b93a7',
            textTransform: 'uppercase',
            marginBottom: 7,
          }}
        >
          {label}
        </div>
        <div
          style={{
            fontSize: 25,
            fontWeight: 700,
            color: '#fff',
            fontVariantNumeric: 'tabular-nums',
            lineHeight: 1.1,
          }}
        >
          {value}
        </div>
        {sub != null && (
          <div style={{ fontSize: 10.5, color: '#9fb2df', marginTop: 5, lineHeight: 1.4 }}>{sub}</div>
        )}
      </div>
    );
  }

  return (
    <div
      className={className}
      data-testid={testId}
      style={{
        background: palette.cardWhite,
        border: `1px solid ${palette.border}`,
        borderRadius: radius.card,
        padding: '13px 15px',
        ...style,
      }}
    >
      <div
        data-kpi-label=""
        style={{
          fontSize: LABEL_FONT_SIZE,
          fontWeight: 700,
          letterSpacing: '.04em',
          color: palette.eyebrow,
          textTransform: 'uppercase',
          marginBottom: 6,
          ...(alignValues
            ? {
                lineHeight: LABEL_LINE_HEIGHT,
                minHeight: LABEL_SLOT_MIN_HEIGHT,
                display: 'flex',
                alignItems: 'flex-end',
              }
            : {}),
        }}
      >
        {label}
      </div>
      <div
        data-kpi-value=""
        style={{
          fontSize: 19,
          fontWeight: 700,
          color: valueColor,
          fontVariantNumeric: 'tabular-nums',
          ...(alignValues ? { lineHeight: 1.15, whiteSpace: 'nowrap' } : {}),
        }}
      >
        {value}
      </div>
      {sub != null && (
        <div style={{ fontSize: 10.5, color: palette.textMuted, marginTop: 4, lineHeight: 1.4 }}>
          {sub}
        </div>
      )}
    </div>
  );
}
