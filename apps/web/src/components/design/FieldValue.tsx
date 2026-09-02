'use client';

/**
 * FieldValue — an inline value cell carrying the canonical field-level
 * provenance + editability treatment. Extracted EXACTLY from
 * `design/canonical/Fondok Field System.dc.html` (the `field()` builder + the
 * `specimens` table + the row markup), which is the authority for how a single
 * value renders inline (distinct from the Data Key STRIP dots).
 *
 * The whole vocabulary — nothing else may be invented per screen:
 *   input (Editable assumption) → blue #1a4fa0 · dashed underline
 *   override (Overridden)       → blue #1a4fa0 · dashed underline · violet corner ▲
 *   doc (Document-sourced)      → ink · dotted underline · ◱ page-corner glyph (green)
 *   link (Linked)               → ink · no rule · ↗ outbound-arrow glyph (blue)
 *   calc (Calculated)           → ink · no mark at all (the default)
 *   review (Needs review)       → ink · amber dotted rule · ◱ glyph · amber corner ▲
 *   awaiting (Awaiting data)    → muted em dash (from the Data Key; Field System
 *                                 has no inline awaiting specimen)
 *
 * Editing affordance (the "Editing" specimen): a framed blue editor box with a
 * caret. Pass `editing` for the visual; pass children to drop in a live input.
 *
 * Presentational + props-driven. `onClick` opens the provenance popover /
 * begins editing in the host tab — this component owns no data.
 */

import type { CSSProperties, ReactNode } from 'react';
import type { ValueState } from '@/lib/api';
import { field } from './tokens';

export type FieldKind = 'input' | 'override' | 'doc' | 'link' | 'calc' | 'review' | 'awaiting';

/** Map the canonical `ValueState` to a Field-System inline kind. */
export function fieldKindFromState(state: ValueState, opts?: { override?: boolean }): FieldKind {
  if (opts?.override) return 'override';
  switch (state) {
    case 'document_sourced':
      return 'doc';
    case 'linked':
      return 'link';
    case 'assumption':
      return 'input';
    case 'calculated':
      return 'calc';
    case 'needs_review':
      return 'review';
    case 'awaiting_data':
      return 'awaiting';
    default:
      return 'calc';
  }
}

interface KindTreatment {
  color: string;
  rule: string; // border-bottom value or 'none'
  glyph: string | false;
  glyphColor: string;
  corner: boolean;
  cornerColor: string;
}

/** The exact treatment table from `field()` in the Field System file. */
function treatment(kind: FieldKind): KindTreatment {
  const isInput = kind === 'input' || kind === 'override';
  const base: KindTreatment = {
    color: isInput ? field.input : kind === 'awaiting' ? '#b0afaa' : field.ink,
    rule:
      isInput
        ? field.inputRule
        : kind === 'doc'
          ? field.docRule
          : kind === 'review'
            ? `1px dotted ${field.flag}`
            : 'none',
    glyph: kind === 'doc' || kind === 'review' ? field.glyphDoc : kind === 'link' ? field.glyphLink : false,
    glyphColor: kind === 'link' ? field.link : field.doc,
    corner: kind === 'override' || kind === 'review',
    cornerColor: kind === 'review' ? field.flag : field.override,
  };
  return base;
}

export interface FieldValueProps {
  value: ReactNode;
  /** Field-System kind. Use `fieldKindFromState` to derive from a ValueState. */
  kind: FieldKind;
  /** Total rows render at weight 700. */
  total?: boolean;
  /** Show the framed blue editor visual. Provide children to host a live input. */
  editing?: boolean;
  /** Value type size — 13.5px in rows, 14px in the specimen table. */
  fontSize?: number;
  onClick?: (e: React.MouseEvent) => void;
  className?: string;
  style?: CSSProperties;
  children?: ReactNode;
}

export function FieldValue({
  value,
  kind,
  total = false,
  editing = false,
  fontSize = 13.5,
  onClick,
  className,
  style,
  children,
}: FieldValueProps) {
  if (editing) {
    return (
      <span
        className={className}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          background: '#fff',
          border: field.editBorder,
          boxShadow: field.editRing,
          borderRadius: 5,
          padding: '3px 9px',
          fontSize: 14,
          color: field.input,
          fontWeight: 600,
          fontVariantNumeric: 'tabular-nums',
          ...style,
        }}
      >
        {children ?? (
          <>
            {value}
            <span
              aria-hidden
              style={{ width: 1, height: 15, background: field.input, marginLeft: 2, display: 'inline-block' }}
            />
          </>
        )}
      </span>
    );
  }

  const t = treatment(kind);
  return (
    <span
      className={className}
      onClick={onClick}
      style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 5,
        fontSize,
        fontVariantNumeric: 'tabular-nums',
        color: t.color,
        fontWeight: total ? 700 : 500,
        cursor: onClick ? 'pointer' : undefined,
        ...style,
      }}
    >
      {t.glyph && (
        <span aria-hidden style={{ fontSize: 9, color: t.glyphColor, lineHeight: 1 }}>
          {t.glyph}
        </span>
      )}
      <span style={{ borderBottom: t.rule === 'none' ? undefined : t.rule, paddingBottom: 1 }}>
        {value}
      </span>
      {t.corner && (
        <span
          aria-hidden
          style={{
            width: 0,
            height: 0,
            borderLeft: '5px solid transparent',
            borderTop: `5px solid ${t.cornerColor}`,
            alignSelf: 'flex-start',
            marginLeft: -2,
          }}
        />
      )}
    </span>
  );
}
