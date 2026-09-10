'use client';

/**
 * Refused — the one honest way the app says "no value".
 *
 * A dash in Fondok is a refusal, not a blank: the model declined to show a
 * number and it knows why. Until now that "why" lived in a dozen bespoke
 * strips, `title=` attributes and bare `'—'` literals, each phrasing itself
 * differently or not at all. This primitive is the single rendering of the
 * refusal glyph, and it carries the machine-readable reason with it.
 *
 *   <Refused reason="pin_active" />          → — (hover: "NOI pinned" + why)
 *   <Refused reason={useRefusal('ltv')} />   → — (hover: whatever the worker said)
 *   <Refused reason={null} />                → — (a bare glyph, nothing else)
 *
 * ── The drop-in contract ────────────────────────────────────────────────
 * With NO resolvable reason the component renders the glyph and NOTHING
 * else — no wrapper element, no handlers, no attributes. The output is
 * byte-identical to the `'—'` string literal it replaces, so a row whose
 * refusal is not (yet) knowable keeps exactly the markup, width, alignment
 * and accessible name it has today. Only a row the worker can actually
 * explain gains anything, and what it gains is a tooltip.
 *
 * With a reason it renders a plain inline `<span>` (an inline box, exactly
 * like the text node it replaces — no display change, no padding, no
 * border, no decoration) wrapped in the shared <Tooltip>, which clones the
 * span rather than nesting a new element. So the only rendered difference
 * is the span itself: same width behaviour in a table cell, same baseline,
 * no layout shift.
 *
 * The vocabulary comes from `lib/ontology/reasons.generated` — the same 16
 * codes the worker emits, the Methodology page documents and the lineage
 * drawer reads. This component never invents a phrasing of its own.
 */

import type { CSSProperties, ReactNode } from 'react';
import { Tooltip, type TooltipSide } from '@/components/help/Tooltip';
import {
  REASONS,
  REFUSAL_GLYPH,
  isReasonCode,
  type ReasonCode,
} from '@/lib/ontology/reasons.generated';
import { useSource } from '@/lib/hooks/useDealProvenance';

export { REFUSAL_GLYPH } from '@/lib/ontology/reasons.generated';
export type { ReasonCode } from '@/lib/ontology/reasons.generated';

// ───────────────────────── reason resolution ─────────────────────────────

/**
 * Resolve one key against a `reasons` map — the bare-`ReasonCode` shape the
 * worker puts on the wire (`GET /deals/{id}/assumption_sources.reasons`,
 * `ValueTrace.reason`, `lineage.unresolved[].code`). Tolerates the richer
 * `{code, detail}` object the runner uses internally, and returns null for
 * anything that is not a code this build knows — an unknown string from a
 * newer worker degrades to a plain dash rather than an empty tooltip.
 *
 * Pure, so a caller that already holds the map (a row list, a memo payload)
 * resolves every key with it and calls no hook.
 */
export function reasonFor(
  reasons: Record<string, unknown> | null | undefined,
  key: string | null | undefined,
): ReasonCode | null {
  if (!reasons || !key) return null;
  return asReasonCode(reasons[key]);
}

/** Narrow one wire value to a ReasonCode — bare string or `{code}` object. */
export function asReasonCode(raw: unknown): ReasonCode | null {
  const v =
    raw && typeof raw === 'object' && 'code' in (raw as object)
      ? (raw as { code?: unknown }).code
      : raw;
  return isReasonCode(v) ? v : null;
}

/**
 * The deal's refusal code for one assumption key, or null when the worker
 * has not said (or there is no ProvenanceProvider above — mock deals, unit
 * tests). Reads the SAME payload `<Sourced>` already reads, so wiring a row
 * costs no extra fetch.
 *
 * Callers hold a key, not a map:
 *
 *   const reason = useRefusal('ffe_reserve_pct');
 *   return value == null ? <Refused reason={reason} /> : fmt(value);
 */
export function useRefusal(key: string | null | undefined): ReasonCode | null {
  const resolved = useSource(key ?? undefined);
  return asReasonCode(resolved?.reason);
}

// ───────────────────────────── component ─────────────────────────────────

export interface RefusedProps {
  /**
   * Why there is no value. `null` / `undefined` — the worker has not said —
   * degrades to a bare glyph with no wrapper (see the drop-in contract).
   */
  reason?: ReasonCode | null;
  /**
   * Optional case-specific prose from the worker (lineage `detail`), shown
   * under the ontology's own explanation. Never replaces it.
   */
  detail?: string | null;
  /**
   * Render something other than the glyph — a canonical label that already
   * says "no value" in words (e.g. the IC memo's "Pending analyst
   * decision"). The tooltip is the same either way.
   */
  children?: ReactNode;
  /** Tooltip edge. Default 'top' (the Tooltip flips it on overflow). */
  side?: TooltipSide;
  /**
   * Give the glyph a tab stop. OFF by default: these live in dense tables
   * and adding dozens of stops would change keyboard order. The reason is
   * still announced through `aria-label`.
   */
  focusable?: boolean;
  className?: string;
  style?: CSSProperties;
  /** Test hook on the trigger span (only present when a reason resolves). */
  testId?: string;
}

export function Refused({
  reason,
  detail,
  children,
  side = 'top',
  focusable = false,
  className,
  style,
  testId,
}: RefusedProps) {
  const code = asReasonCode(reason);

  // No knowable reason → the literal it replaces, and nothing more.
  if (!code) return <>{children ?? REFUSAL_GLYPH}</>;

  const meta = REASONS[code];
  return (
    <Tooltip
      side={side}
      content={
        <>
          <span className="block font-semibold">{meta.label}</span>
          <span className="block mt-1 text-white/80">{meta.explanation}</span>
          {detail ? <span className="block mt-1 text-white/70">{detail}</span> : null}
        </>
      }
    >
      <span
        data-refused={code}
        data-testid={testId}
        aria-label={meta.label}
        className={className}
        tabIndex={focusable ? 0 : undefined}
        style={{ cursor: 'help', ...style }}
      >
        {children ?? REFUSAL_GLYPH}
      </span>
    </Tooltip>
  );
}

export default Refused;
