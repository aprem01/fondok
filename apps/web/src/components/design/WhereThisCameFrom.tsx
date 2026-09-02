'use client';

/**
 * WhereThisCameFrom — the single anchored provenance / lineage popover ("one
 * popover, anchored — same anatomy on every tab"). Extracted EXACTLY from the
 * detail popover in `design/canonical/Fondok Field System.dc.html` (markup lines
 * ~224-306 + the `insp` data objects), cross-referenced with the two-axis model
 * in `Data Provenance System.dc.html`.
 *
 * Anatomy (each section optional, in this order):
 *   header  → kind eyebrow (kindColor) + ✕ · "label · where" · 24px value · sub
 *   calc    → CALCULATION · expr · mono numbers · input rows (dot · name · path ↗)
 *   source  → SOURCE · doc thumbnail · doc name · location · quoted line · confidence bar
 *   override→ OVERRIDE · struck source value → current (blue) · meta rows
 *   deps    → AFFECTS DOWNSTREAM · count chip · dependency rows (↳ name · where)
 *   actions → footer buttons (primary navy / secondary white)
 *
 * Positioning mirrors the source's `place()` output — pass `top` / `left` /
 * `caretRight`; the host tab computes them from the clicked cell. Presentational.
 */

import type { CSSProperties, ReactNode } from 'react';
import { palette, shadow, popoverKind } from './tokens';

export interface ProvInput {
  name: string;
  path: string;
  dotColor: string;
  onClick?: () => void;
}
export interface ProvSource {
  doc: string;
  loc: string;
  text: string;
  /** e.g. "74%". */
  confidence?: string;
  confColor?: string;
  /** Highlighted-line color in the mini thumbnail. */
  highlightColor?: string;
}
export interface ProvOverride {
  orig: ReactNode;
  current: ReactNode;
  meta: { k: string; v: ReactNode }[];
}
export interface ProvAction {
  label: string;
  primary?: boolean;
  onClick?: () => void;
}

export interface WhereThisCameFromProps {
  /** e.g. "Document-sourced", "Overridden · was sourced". */
  kind: string;
  /** Kind eyebrow color. See `popoverKind` tokens for the canonical values. */
  kindColor?: string;
  label: string;
  where: string;
  value: ReactNode;
  valueColor?: string;
  sub?: ReactNode;
  calc?: { expr: ReactNode; numbers?: ReactNode; inputs?: ProvInput[] };
  source?: ProvSource;
  override?: ProvOverride;
  deps?: { count: string; items: { name: string; where: string }[] };
  actions?: ProvAction[];
  /** Absolute-position offsets (host computes from the anchor cell). */
  top?: number | string;
  left?: number | string;
  caretRight?: number | string;
  position?: 'absolute' | 'fixed';
  onClose?: () => void;
  className?: string;
  style?: CSSProperties;
}

const eyebrowLabel: CSSProperties = {
  fontSize: 10,
  fontWeight: 700,
  letterSpacing: '.06em',
  color: palette.textMuted,
  textTransform: 'uppercase',
};
const section: CSSProperties = {
  padding: '13px 17px',
  borderBottom: `1px solid ${palette.hairlineSection}`,
};

export function WhereThisCameFrom({
  kind,
  kindColor = popoverKind.calculated,
  label,
  where,
  value,
  valueColor = palette.ink,
  sub,
  calc,
  source,
  override,
  deps,
  actions,
  top,
  left,
  caretRight = 20,
  position = 'absolute',
  onClose,
  className,
  style,
}: WhereThisCameFromProps) {
  return (
    <div
      role="dialog"
      aria-label={`Where ${label} came from`}
      className={className}
      onClick={(e) => e.stopPropagation()}
      style={{
        position,
        zIndex: 40,
        width: 346,
        top,
        left,
        background: '#fff',
        border: '1px solid #dfdeda',
        borderRadius: 11,
        boxShadow: shadow.popover,
        overflow: 'hidden',
        ...style,
      }}
    >
      <div
        aria-hidden
        style={{
          position: 'absolute',
          top: -6,
          right: caretRight,
          width: 11,
          height: 11,
          background: '#fff',
          borderLeft: '1px solid #dfdeda',
          borderTop: '1px solid #dfdeda',
          transform: 'rotate(45deg)',
        }}
      />

      {/* Header */}
      <div style={{ padding: '15px 17px 13px', borderBottom: `1px solid ${palette.hairlineSection}`, position: 'relative' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 7 }}>
          <span style={{ ...eyebrowLabel, color: kindColor }}>{kind}</span>
          {onClose && (
            <span
              role="button"
              tabIndex={0}
              onClick={onClose}
              onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onClose()}
              style={{ marginLeft: 'auto', fontSize: 13, color: '#b5b4ae', cursor: 'pointer', lineHeight: 1 }}
            >
              ✕
            </span>
          )}
        </div>
        <div style={{ fontSize: 12.5, color: palette.textSecondary, marginBottom: 2 }}>
          {label}
          <span style={{ color: '#b5b4ae' }}> · </span>
          <span style={{ color: palette.textMuted }}>{where}</span>
        </div>
        <div
          style={{
            fontSize: 24,
            fontWeight: 600,
            fontVariantNumeric: 'tabular-nums',
            color: valueColor,
            letterSpacing: '-.01em',
          }}
        >
          {value}
        </div>
        {sub != null && (
          <div style={{ fontSize: 11.5, color: palette.textMuted, marginTop: 4, lineHeight: 1.45 }}>{sub}</div>
        )}
      </div>

      {/* Calculation */}
      {calc && (
        <div style={section}>
          <div style={{ ...eyebrowLabel, marginBottom: 8 }}>Calculation</div>
          <div style={{ fontSize: 12.5, color: palette.ink, fontWeight: 600, marginBottom: 4 }}>{calc.expr}</div>
          {calc.numbers != null && (
            <div
              style={{
                fontSize: 12,
                color: palette.textSecondary,
                fontVariantNumeric: 'tabular-nums',
                fontFamily: 'ui-monospace, monospace',
              }}
            >
              {calc.numbers}
            </div>
          )}
          {calc.inputs && calc.inputs.length > 0 && (
            <div style={{ marginTop: 11, display: 'flex', flexDirection: 'column', gap: 7 }}>
              {calc.inputs.map((i, k) => (
                <div
                  key={k}
                  onClick={i.onClick}
                  style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: i.onClick ? 'pointer' : undefined }}
                >
                  <span style={{ width: 5, height: 5, borderRadius: '50%', background: i.dotColor, flexShrink: 0 }} />
                  <span style={{ fontSize: 12, color: palette.ink, fontWeight: 500 }}>{i.name}</span>
                  <span style={{ fontSize: 11, color: palette.textMuted, marginLeft: 'auto' }}>{i.path} ↗</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Source */}
      {source && (
        <div style={section}>
          <div style={{ ...eyebrowLabel, marginBottom: 9 }}>Source</div>
          <div style={{ display: 'flex', gap: 11, alignItems: 'flex-start' }}>
            <div
              aria-hidden
              style={{
                width: 34,
                height: 44,
                border: '1px solid #e2e1dc',
                borderRadius: 3,
                background: palette.surfaceTint,
                flexShrink: 0,
                padding: 5,
                display: 'flex',
                flexDirection: 'column',
                gap: 3,
              }}
            >
              <span style={{ height: 2, background: '#dcdad3', display: 'block' }} />
              <span style={{ height: 2, background: '#dcdad3', display: 'block' }} />
              <span style={{ height: 2, background: source.highlightColor ?? '#dcdad3', display: 'block' }} />
              <span style={{ height: 2, background: '#dcdad3', display: 'block' }} />
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: palette.ink, lineHeight: 1.35 }}>{source.doc}</div>
              <div style={{ fontSize: 11.5, color: palette.textMuted, marginTop: 2 }}>{source.loc}</div>
              <div style={{ fontSize: 11.5, color: palette.textSecondary, marginTop: 6, fontStyle: 'italic', lineHeight: 1.4 }}>
                “{source.text}”
              </div>
            </div>
          </div>
          {source.confidence && (
            <div style={{ marginTop: 11, display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ flex: 1, height: 4, borderRadius: 2, background: '#f0efea', overflow: 'hidden' }}>
                <div style={{ width: source.confidence, height: '100%', background: source.confColor ?? palette.textSecondary }} />
              </div>
              <span style={{ fontSize: 11, fontWeight: 600, color: source.confColor ?? palette.textSecondary }}>
                {source.confidence} confidence
              </span>
            </div>
          )}
        </div>
      )}

      {/* Override */}
      {override && (
        <div style={section}>
          <div style={{ ...eyebrowLabel, marginBottom: 9 }}>Override</div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 10 }}>
            <div>
              <div style={{ fontSize: 10.5, color: palette.textMuted, marginBottom: 2 }}>Source value</div>
              <div style={{ fontSize: 13, color: palette.textMuted, textDecoration: 'line-through', fontVariantNumeric: 'tabular-nums' }}>
                {override.orig}
              </div>
            </div>
            <span style={{ color: '#c9c8c2' }}>→</span>
            <div>
              <div style={{ fontSize: 10.5, color: palette.textMuted, marginBottom: 2 }}>Current</div>
              <div style={{ fontSize: 13, color: '#1a4fa0', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                {override.current}
              </div>
            </div>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            {override.meta.map((m, k) => (
              <div key={k} style={{ display: 'flex', gap: 10, fontSize: 11.5 }}>
                <span style={{ color: palette.textMuted, width: 66, flexShrink: 0 }}>{m.k}</span>
                <span style={{ color: palette.ink }}>{m.v}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Affects downstream */}
      {deps && (
        <div style={section}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 9 }}>
            <span style={eyebrowLabel}>Affects downstream</span>
            <span style={{ fontSize: 10.5, fontWeight: 700, color: '#8a5a12', background: '#fdf4e3', padding: '2px 6px', borderRadius: 4 }}>
              {deps.count}
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {deps.items.map((d, k) => (
              <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }}>
                <span style={{ color: '#c9c8c2' }}>↳</span>
                <span style={{ color: palette.ink }}>{d.name}</span>
                <span style={{ color: palette.textMuted, marginLeft: 'auto', fontSize: 11 }}>{d.where}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Actions */}
      {actions && actions.length > 0 && (
        <div style={{ padding: '12px 17px', background: palette.surfaceTint, display: 'flex', gap: 7, flexWrap: 'wrap' }}>
          {actions.map((a, k) => (
            <button
              key={k}
              onClick={a.onClick}
              style={{
                borderRadius: 6,
                padding: '6px 11px',
                fontSize: 11.5,
                fontWeight: 600,
                cursor: 'pointer',
                fontFamily: 'inherit',
                border: a.primary ? 'none' : `1px solid ${palette.buttonSecondaryBorder}`,
                background: a.primary ? palette.inkNavy : '#fff',
                color: a.primary ? '#fff' : palette.ink,
              }}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
