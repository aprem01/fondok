/**
 * SectionCard — the standard white panel with an optional eyebrow/title header
 * and right-aligned note. It is the single card chrome the canonical tabs reuse.
 *
 * Source: the `sections` cards in `design/canonical/Investment Tab.dc.html`
 * (card 16px 18px padding, eyebrow title 12px/700/.03em/uppercase/#8a8a86 with
 * an 11px #b0afaa right note) and the table-card header in
 * `Cash Flow Tab.dc.html` (13.5px/700/#1a2233 title + 11px/#9a9a95 caption on a
 * 1px #f2f1ec divider). Two header variants:
 *   variant="eyebrow" (default) → uppercase gray section label, inline note
 *   variant="title"             → sentence-case bold title on a bottom divider
 *
 * Presentational; children render the card body.
 */

import type { CSSProperties, ReactNode } from 'react';
import { palette, radius } from './tokens';

export interface SectionCardProps {
  title?: ReactNode;
  /** Right-aligned note (eyebrow variant) / caption (title variant). */
  note?: ReactNode;
  variant?: 'eyebrow' | 'title';
  children?: ReactNode;
  /** Padding for the header + body. Title variant draws its own header divider. */
  className?: string;
  style?: CSSProperties;
  bodyStyle?: CSSProperties;
}

export function SectionCard({
  title,
  note,
  variant = 'eyebrow',
  children,
  className,
  style,
  bodyStyle,
}: SectionCardProps) {
  const shell: CSSProperties = {
    background: palette.cardWhite,
    border: `1px solid ${palette.border}`,
    borderRadius: radius.card,
    ...style,
  };

  if (variant === 'title') {
    // Title header sits on a full-width bottom divider, body below (table cards).
    return (
      <div className={className} style={{ ...shell, overflow: 'hidden' }}>
        {(title != null || note != null) && (
          <div
            style={{
              padding: '13px 18px',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'baseline',
              gap: 12,
              flexWrap: 'wrap',
              borderBottom: `1px solid ${palette.hairlineSection}`,
            }}
          >
            {title != null && (
              <span style={{ fontSize: 13.5, fontWeight: 700, color: palette.ink }}>{title}</span>
            )}
            {note != null && <span style={{ fontSize: 11, color: palette.textMuted }}>{note}</span>}
          </div>
        )}
        <div style={bodyStyle}>{children}</div>
      </div>
    );
  }

  // Eyebrow header inline with the body (Investment section cards).
  return (
    <div
      className={className}
      style={{ ...shell, padding: '16px 18px', display: 'flex', flexDirection: 'column', ...bodyStyle }}
    >
      {(title != null || note != null) && (
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'baseline',
            gap: 14,
            marginBottom: 4,
          }}
        >
          {title != null && (
            <span
              style={{
                fontSize: 12,
                fontWeight: 700,
                color: palette.eyebrow,
                textTransform: 'uppercase',
                letterSpacing: '.03em',
              }}
            >
              {title}
            </span>
          )}
          {note != null && (
            <span style={{ fontSize: 11, color: palette.textFaint, textAlign: 'right' }}>{note}</span>
          )}
        </div>
      )}
      {children}
    </div>
  );
}
