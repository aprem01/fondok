'use client';

/**
 * SubTabNav — the one canonical sub-tab control: underline tabs on a full-width
 * content-column hairline. Extracted EXACTLY from `design/canonical/Fondok
 * Sub-Tabs.dc.html` (the live demo control + its `spec`/`states`), cross-checked
 * against the sub-tab row in `Cash Flow Tab.dc.html`.
 *
 * Spec (from the file):
 *   label     → 13.5px · 500 rest / 700 active · no icons
 *   active    → navy #14213d label · 2px navy underline
 *   rest      → #9a9a95 label · 2px transparent underline (keeps baseline stable)
 *   hover     → #3a3f47 label · 2px #d9d8d2 underline preview
 *   disabled  → #b0afaa · transparent underline · opacity .55 (source not in yet)
 *   row       → 1px #e6e5e0 hairline, full content width · gap 28 · pb 10 · mb −1
 *   caption   → 11.5px #9a9a95, one line, right-aligned on the row (optional)
 *
 * Controlled + presentational: pass `items`, `activeId`, `onSelect`.
 */

import type { CSSProperties } from 'react';
import { palette, border as B, type } from './tokens';

export interface SubTabItem {
  id: string;
  label: string;
  /** "Unavailable" — its source data is not in yet. Renders dimmed, non-clickable. */
  disabled?: boolean;
  /** Tooltip naming the missing document (shown on a disabled tab). */
  disabledHint?: string;
}

export interface SubTabNavProps {
  items: SubTabItem[];
  activeId: string;
  onSelect?: (id: string) => void;
  /** One line of grey text describing the active view, right-aligned on the row. */
  caption?: string;
  className?: string;
  style?: CSSProperties;
}

export function SubTabNav({ items, activeId, onSelect, caption, className, style }: SubTabNavProps) {
  return (
    <div
      role="tablist"
      className={className}
      style={{
        display: 'flex',
        alignItems: 'flex-end',
        gap: 28,
        borderBottom: `1px solid ${palette.subtabHairline}`,
        overflowX: 'auto',
        ...style,
      }}
    >
      {items.map((t) => {
        const active = t.id === activeId;
        return (
          <button
            key={t.id}
            role="tab"
            aria-selected={active}
            aria-disabled={t.disabled || undefined}
            disabled={t.disabled}
            title={t.disabled ? t.disabledHint : undefined}
            onClick={() => !t.disabled && onSelect?.(t.id)}
            style={{
              fontSize: type.subtab.size,
              fontFamily: 'inherit',
              background: 'none',
              border: 'none',
              borderBottom: active ? B.subtabActive : B.subtabRest,
              cursor: t.disabled ? 'default' : 'pointer',
              fontWeight: active ? type.subtabActiveWeight : 500,
              color: t.disabled ? palette.textFaint : active ? palette.inkNavy : palette.textMuted,
              opacity: t.disabled ? 0.55 : 1,
              padding: '0 0 10px',
              marginBottom: -1,
              whiteSpace: 'nowrap',
            }}
          >
            {t.label}
          </button>
        );
      })}
      {caption != null && (
        <span
          style={{
            fontSize: 11.5,
            color: palette.textMuted,
            marginLeft: 'auto',
            paddingBottom: 11,
            whiteSpace: 'nowrap',
          }}
        >
          {caption}
        </span>
      )}
    </div>
  );
}
