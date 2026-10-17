'use client';

/**
 * FON-89 — the Year-1 PIP displacement verdict, read from the deal's
 * provenance payload (`GET /deals/{id}/assumption_sources`
 * → `displacement_status`).
 *
 * Why its own context and not another getter on `useDealProvenance`: the
 * worker computes the verdict once per deal alongside the source map, so
 * `ProvenanceProvider` is the natural (and only) feeder — but a dozen test
 * harnesses mock `@/lib/hooks/useDealProvenance` down to `{ useSource }`.
 * Keeping the hook in a separate module means those harnesses render the
 * two consumers (CAPEX Renovation row, Future P&L Year-1 basis) with no
 * provider → `null` → nothing printed, exactly like a worker that does not
 * emit the field yet.
 *
 * The line printed is the worker's `detail`, verbatim. Every number in it is
 * the loader's own (the percentages the revenue engine will apply, the budget
 * per key); the browser adds only the tone.
 */

import { createContext, useContext } from 'react';
import type { DisplacementStatus } from '@/lib/api';

export const DisplacementStatusContext = createContext<DisplacementStatus | null>(null);

/** The deal's displacement verdict, or `null` while loading / absent / no provider. */
export function useDisplacementStatus(): DisplacementStatus | null {
  return useContext(DisplacementStatusContext);
}

export type DisplacementTone = 'on' | 'muted';

export interface DisplacementStatusLine {
  tone: DisplacementTone;
  /** The worker's sentence, printed as-is. */
  text: string;
  /** The worker's state id — carried onto the element for tests / QA. */
  state: string;
}

/**
 * Tone for the worker's verdict. Active (an explicit displacement assumption
 * is set) → "on"; otherwise "muted". No state is toned as a warning: since
 * Sam's 2026-10-15 decision an undisplaced Year 1 is the correct default, not
 * a gap — a renovation budget never implies an operating impact.
 * Returns `null` when there is nothing honest to print.
 */
export function displacementStatusLine(
  status: DisplacementStatus | null | undefined,
): DisplacementStatusLine | null {
  if (!status || typeof status.detail !== 'string' || !status.detail.trim()) return null;
  const state = String(status.state ?? '');
  const tone: DisplacementTone = status.active ? 'on' : 'muted';
  return { tone, text: status.detail.trim(), state };
}
