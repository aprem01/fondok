'use client';

/**
 * DataKey — the one canonical Data Key strip that sits directly under the tab
 * bar on every tab. Extracted EXACTLY from `design/canonical/Fondok Data
 * Key.dc.html` (the `renderVals()` strip + `spec` + "How to read this" modal),
 * cross-checked against the inline strip in `Cash Flow Tab.dc.html`.
 *
 * Anatomy (fixed order): "DATA KEY" label · six origin dots (Document sourced ·
 * Linked · Assumption · Calculated · Awaiting data · Needs review) · a bold
 * Total / summary sample · a dotted-underline Editable sample · "ⓘ How to read
 * this" which opens the centered help modal (intro + all eight tokens + footer).
 *
 * Props-driven: the two in-strip numbers (Total / Editable samples) are props.
 * NOTE: the canonical strip carries NO per-state counts — only these two
 * illustrative numbers. See README / handoff note.
 *
 * Presentational only. `helpOpen` is optionally controlled.
 */

import { useState, type CSSProperties } from 'react';
import type { ValueState } from '@/lib/api';
import { ProvenanceDot } from './ProvenanceDot';
import { provDot, prov, palette, shadow, overlay } from './tokens';

/** Canonical origin order + the "means" copy from `Fondok Data Key.dc.html`. */
const ORIGINS: { state: ValueState; means: string }[] = [
  {
    state: 'document_sourced',
    means:
      'Extracted by Fondok from a document in the Data Room — the source page is one click away.',
  },
  { state: 'linked', means: 'Owned by another Fondok module and mirrored here. Change it where it lives.' },
  { state: 'assumption', means: 'You entered it. Fondok never changes it silently.' },
  { state: 'calculated', means: 'Derived by Fondok from the values above. Edit its inputs to move it.' },
  {
    state: 'awaiting_data',
    means: 'The source document is not in the Data Room yet. Shown as — , never as zero.',
  },
  {
    state: 'needs_review',
    means:
      'A halo on the origin dot: extracted but low confidence, or an override that conflicts with its source. Click to verify.',
  },
];

/** The two modifier rows appended in the help modal `spec`. */
const MODIFIER_SPEC = [
  {
    label: 'Total / summary',
    means:
      'Bold, in near-black. Structure, not origin — a total is bold and still carries its own dot.',
    dot: { fill: prov.black, border: 'none' as const, ring: 'none' as const },
  },
  {
    label: 'Editable',
    means:
      'Dotted underline. Independent of origin — sourced and calculated values can both be overridable.',
    dot: { fill: '#fff', border: '1px solid #d9d8d2', ring: 'none' as const },
  },
];

export interface DataKeyProps {
  /** Bold in-strip "Total / summary" sample number. */
  totalSample?: string;
  /** Dotted-underline in-strip "Editable — click to change" sample. */
  editableSample?: string;
  /** Controlled help-modal open state. Omit for internal state. */
  helpOpen?: boolean;
  onHelpOpenChange?: (open: boolean) => void;
  className?: string;
  style?: CSSProperties;
}

const eyebrow: CSSProperties = { fontWeight: 700, color: palette.eyebrow, letterSpacing: '.02em' };
const item: CSSProperties = { display: 'flex', alignItems: 'center', gap: 6 };

export function DataKey({
  // Canonical legend swatches from `Fondok Data Key.dc.html` — illustrative
  // examples that demonstrate the two structural modifiers (bold = Total /
  // summary, dotted underline = Editable), exactly like the six origin dots are
  // colour examples. Part of the KEY, not deal data. Defaulted to the canonical
  // values so the strip matches the design on every tab; pass to override.
  totalSample = '1,240',
  editableSample = '123',
  helpOpen,
  onHelpOpenChange,
  className,
  style,
}: DataKeyProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = helpOpen ?? internalOpen;
  const setOpen = (v: boolean) => {
    setInternalOpen(v);
    onHelpOpenChange?.(v);
  };

  return (
    <>
      <div
        className={className}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          flexWrap: 'wrap',
          background: palette.surfaceTint,
          border: `1px solid ${palette.border}`,
          borderRadius: 8,
          padding: '9px 14px',
          fontSize: 11,
          color: palette.textSecondary,
          ...style,
        }}
      >
        <span style={eyebrow}>DATA KEY</span>

        {ORIGINS.map((o) => (
          <span key={o.state} style={item}>
            <ProvenanceDot state={o.state} />
            {provDot[o.state].label}
          </span>
        ))}

        {totalSample != null && (
          <span style={item}>
            <b style={{ color: palette.ink }}>{totalSample}</b>Total / summary
          </span>
        )}
        {editableSample != null && (
          <span style={item}>
            <span style={{ textDecoration: 'underline dotted', color: palette.textSecondary }}>
              {editableSample}
            </span>
            Editable — click to change
          </span>
        )}

        <span
          role="button"
          tabIndex={0}
          onClick={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') setOpen(true);
          }}
          style={{
            cursor: 'pointer',
            color: palette.textMuted,
            marginLeft: 'auto',
            whiteSpace: 'nowrap',
          }}
        >
          ⓘ How to read this
        </span>
      </div>

      {open && (
        <>
          <div
            onClick={() => setOpen(false)}
            style={{ position: 'fixed', inset: 0, background: overlay.scrim, zIndex: 80 }}
          />
          <div
            role="dialog"
            aria-label="How to read this"
            style={{
              position: 'fixed',
              left: '50%',
              top: '14vh',
              transform: 'translateX(-50%)',
              width: 460,
              maxHeight: '72vh',
              overflowY: 'auto',
              background: '#fff',
              borderRadius: 11,
              boxShadow: shadow.helpModal,
              zIndex: 81,
              padding: '22px 24px',
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12 }}>
              <span style={{ fontSize: 15, fontWeight: 700, color: palette.ink }}>How to read this</span>
              <span
                role="button"
                tabIndex={0}
                onClick={() => setOpen(false)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') setOpen(false);
                }}
                style={{ cursor: 'pointer', color: palette.textMuted, fontSize: 13 }}
              >
                ✕
              </span>
            </div>

            <div style={{ fontSize: 12.5, color: '#3a3f47', lineHeight: 1.6 }}>
              Every number in Fondok carries its origin as a coloured dot, and its status and role as
              modifiers on top. Provenance and editability are separate: a document-sourced value can
              also be editable — Fondok extracted it, you may override it, and the override keeps the
              original source so you can restore it.
            </div>

            {ORIGINS.map((o) => (
              <SpecRow
                key={o.state}
                dot={<ProvenanceDot state={o.state} style={{ marginTop: 4 }} />}
                label={provDot[o.state].label}
                means={o.means}
              />
            ))}
            {MODIFIER_SPEC.map((m) => (
              <SpecRow
                key={m.label}
                dot={
                  <span
                    style={{
                      width: 9,
                      height: 9,
                      borderRadius: '50%',
                      background: m.dot.fill,
                      border: m.dot.border === 'none' ? undefined : m.dot.border,
                      display: 'inline-block',
                      marginTop: 4,
                      flexShrink: 0,
                    }}
                  />
                }
                label={m.label}
                means={m.means}
              />
            ))}

            <div
              style={{
                fontSize: 11.5,
                color: palette.textMuted,
                lineHeight: 1.5,
                borderTop: '1px solid #f2f1ec',
                paddingTop: 10,
              }}
            >
              Click any value to see where it came from, the calculation behind it, and whether you can
              change it.
            </div>
          </div>
        </>
      )}
    </>
  );
}

function SpecRow({
  dot,
  label,
  means,
}: {
  dot: React.ReactNode;
  label: string;
  means: string;
}) {
  return (
    <div
      style={{
        display: 'flex',
        gap: 10,
        alignItems: 'flex-start',
        paddingTop: 9,
        borderTop: '1px solid #f2f1ec',
      }}
    >
      {dot}
      <span style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ fontSize: 12.5, color: palette.ink, fontWeight: 600 }}>{label}</span>
        <span style={{ fontSize: 12, color: palette.textSecondary, lineHeight: 1.5 }}>{means}</span>
      </span>
    </div>
  );
}
