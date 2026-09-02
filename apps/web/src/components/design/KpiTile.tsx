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
  /** Value color — defaults to ink navy; pass a provenance color for grounded tiles. */
  valueColor?: string;
  className?: string;
  style?: CSSProperties;
}

export function KpiTile({ label, value, sub, valueColor = palette.ink, className, style }: KpiTileProps) {
  return (
    <div
      className={className}
      style={{
        background: palette.cardWhite,
        border: `1px solid ${palette.border}`,
        borderRadius: radius.card,
        padding: '13px 15px',
        ...style,
      }}
    >
      <div
        style={{
          fontSize: 10,
          fontWeight: 700,
          letterSpacing: '.04em',
          color: palette.eyebrow,
          textTransform: 'uppercase',
          marginBottom: 6,
        }}
      >
        {label}
      </div>
      <div
        style={{
          fontSize: 19,
          fontWeight: 700,
          color: valueColor,
          fontVariantNumeric: 'tabular-nums',
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
