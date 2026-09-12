'use client';

/**
 * useWorksheetLayout — the PRESENTATION layer over the canonical worksheet.
 *
 * The vision's "presentation tree over canonical mapping" split: canonical
 * values (what the engines consume) are locked; how the statement READS is
 * flexible. This hook holds that flexible layer — relabels, hidden lines,
 * per-section ordering, curated memo lines, and splits with roll-up
 * enforcement — WITHOUT touching any engine input. Splitting Insurance into
 * "Property" + "Liability" changes nothing the model computes; it only changes
 * how the analyst reads and annotates it, with a delta chip guaranteeing the
 * children reconcile to the locked parent.
 *
 * PERSISTENCE (FON-41, founder decision 6) — the layout lives ON THE DEAL, not
 * on the device. A reordered statement is a deal artifact: the IC reviewer who
 * opens the deal must read the same statement the analyst built, so
 * `field_overrides.worksheet_layout` is the store and `localStorage` is now only
 * a write-through cache / migration source:
 *
 *  - first load for a deal that has a server layout → the server wins;
 *  - first load for a deal with NO server layout → whatever this device already
 *    has in `localStorage` is adopted and written UP once (nobody's existing
 *    layout is silently discarded), after which the server is the source;
 *  - every mutation writes through to `localStorage` immediately and PATCHes the
 *    deal on a debounce, so dragging three rows is ONE round-trip;
 *  - a failed write is surfaced (`saveError` + `onError`) and retryable. The
 *    analyst must never believe a reorder saved when it did not.
 *
 * THE LAYOUT IS PRESENTATION-ONLY AND MUST NEVER REACH THE ENGINES. Every write
 * goes through `sanitize()`, which rebuilds the payload from the six known
 * layout keys, and the PATCH touches exactly one `field_overrides` key
 * (`worksheet_layout`) — never a value key. The worker skips it explicitly when
 * it routes `field_overrides` into engine input; this is the client half of that
 * guarantee.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, isWorkerConnected } from '@/lib/api';

export interface CuratedLine {
  id: string;
  section: string;   // section row id this line lives under
  label: string;
  value: number;     // manual memo value (Model column) — never engine-fed
}

export interface SplitChild {
  id: string;
  label: string;
  value: number;     // presentation value; siblings must sum to the parent
}

export interface WorksheetLayout {
  v: 1;
  relabels: Record<string, string>;      // rowId -> custom label
  hidden: string[];                      // rowIds hidden from the default view
  order: Record<string, string[]>;       // sectionId -> ordered child rowIds
  curated: CuratedLine[];
  splits: Record<string, SplitChild[]>;  // parent rowId -> presentation children
}

/** The deal's `field_overrides` key that holds the layout. Presentation only. */
export const WORKSHEET_LAYOUT_KEY = 'worksheet_layout';

/** The only keys a persisted layout may carry (the engine-safety allow-list). */
export const WORKSHEET_LAYOUT_FIELDS = ['v', 'relabels', 'hidden', 'order', 'curated', 'splits'] as const;

/** PATCHes are coalesced over this window — a drag of three rows is one write. */
const WRITE_DEBOUNCE_MS = 500;

const EMPTY: WorksheetLayout = { v: 1, relabels: {}, hidden: [], order: {}, curated: [], splits: {} };
const keyFor = (dealId: string) => `fondok:wslayout:${dealId}`;

/** Normalize any stored/parsed blob into the exact layout shape. */
function coerce(parsed: Partial<WorksheetLayout> | null | undefined): WorksheetLayout {
  return {
    v: 1,
    relabels: parsed?.relabels ?? {},
    hidden: parsed?.hidden ?? [],
    order: parsed?.order ?? {},
    curated: parsed?.curated ?? [],
    splits: parsed?.splits ?? {},
  };
}

/**
 * Rebuild the payload from the six known keys. Anything else an older client
 * (or a hand-edited row) left behind is dropped rather than PATCHed back — the
 * layout rides the engine-input channel, which is no place to be generous about
 * unknown keys.
 */
function sanitize(layout: WorksheetLayout): WorksheetLayout {
  return {
    v: 1,
    relabels: { ...layout.relabels },
    hidden: [...layout.hidden],
    order: Object.fromEntries(Object.entries(layout.order).map(([k, v]) => [k, [...v]])),
    curated: layout.curated.map((c) => ({ id: c.id, section: c.section, label: c.label, value: c.value })),
    splits: Object.fromEntries(
      Object.entries(layout.splits).map(([k, kids]) => [
        k,
        kids.map((c) => ({ id: c.id, label: c.label, value: c.value })),
      ]),
    ),
  };
}

function hasEdits(layout: WorksheetLayout): boolean {
  return (
    Object.keys(layout.relabels).length > 0 ||
    layout.hidden.length > 0 ||
    layout.curated.length > 0 ||
    Object.keys(layout.splits).length > 0 ||
    Object.keys(layout.order).length > 0
  );
}

function readStore(dealId: string): WorksheetLayout {
  if (typeof window === 'undefined') return EMPTY;
  try {
    const raw = window.localStorage.getItem(keyFor(dealId));
    if (!raw) return EMPTY;
    return coerce(JSON.parse(raw) as Partial<WorksheetLayout>);
  } catch {
    return EMPTY;
  }
}

function writeStore(dealId: string, layout: WorksheetLayout) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(keyFor(dealId), JSON.stringify(layout));
  } catch {
    /* quota / private mode — the deal row is the durable copy either way */
  }
}

/** Read the layout off a deal row. `null` when the deal carries none yet. */
export function layoutFromOverrides(
  overrides: Record<string, unknown> | null | undefined,
): WorksheetLayout | null {
  const raw = overrides?.[WORKSHEET_LAYOUT_KEY];
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  return coerce(raw as Partial<WorksheetLayout>);
}

// Unique id, collision-safe across reloads. This is client-only ('use client'
// + only called from event handlers after hydration), so Date.now / Math.random
// are available; a monotonic counter alone would collide with persisted ids
// after a page reload resets it.
let _counter = 0;
const newId = (prefix: string) =>
  `${prefix}_${Date.now().toString(36)}${(_counter += 1).toString(36)}${Math.random().toString(36).slice(2, 6)}`;

/** Minimal shape this hook needs off the deal row (`useDeal().deal`). */
export interface DealLayoutSource {
  field_overrides?: Record<string, unknown> | null;
}

export interface WorksheetLayoutOptions {
  /** The loaded deal row. `null` while it is still in flight. */
  deal?: DealLayoutSource | null;
  /** Surfaced to the analyst (a toast) whenever a write fails. */
  onError?: (message: string) => void;
  /** Fired after a successful write so the caller can refresh the deal row. */
  onSaved?: () => void;
}

export function useWorksheetLayout(dealId: string, opts?: WorksheetLayoutOptions) {
  const [layout, setLayout] = useState<WorksheetLayout>(EMPTY);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  // A deal the worker knows about. Numeric ids are mock/demo rows the worker
  // cannot PATCH — those keep the device-local store, the same code path minus
  // the round-trip.
  const serverBacked = isWorkerConnected() && !!dealId && !/^\d+$/.test(dealId);

  const deal = opts?.deal ?? null;
  const onError = opts?.onError;
  const onSaved = opts?.onSaved;

  // Latest values the write path needs, without re-creating every callback.
  const overridesRef = useRef<Record<string, unknown>>({});
  overridesRef.current = (deal?.field_overrides ?? {}) as Record<string, unknown>;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;
  const onSavedRef = useRef(onSaved);
  onSavedRef.current = onSaved;

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingRef = useRef<WorksheetLayout | null>(null);
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const push = useCallback(
    async (next: WorksheetLayout) => {
      const payload = sanitize(next);
      setSaving(true);
      try {
        // ONE key. The rest of `field_overrides` is carried through untouched
        // because the worker replaces the column wholesale on PATCH.
        await api.deals.update(dealId, {
          field_overrides: { ...overridesRef.current, [WORKSHEET_LAYOUT_KEY]: payload },
        });
        overridesRef.current = { ...overridesRef.current, [WORKSHEET_LAYOUT_KEY]: payload };
        if (mountedRef.current) {
          setSaveError(null);
          setSaving(false);
        }
        onSavedRef.current?.();
      } catch (err) {
        const detail = err instanceof Error ? err.message : String(err);
        const msg = `Layout not saved — ${detail || 'the worker rejected the update'}`;
        if (mountedRef.current) {
          setSaveError(msg);
          setSaving(false);
        }
        onErrorRef.current?.(msg);
      }
    },
    [dealId],
  );

  const schedule = useCallback(
    (next: WorksheetLayout) => {
      pendingRef.current = next;
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        const queued = pendingRef.current;
        pendingRef.current = null;
        if (queued) void push(queued);
      }, WRITE_DEBOUNCE_MS);
    },
    [push],
  );

  /** Send any debounced write immediately (e.g. before an export). */
  const flush = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const queued = pendingRef.current;
    pendingRef.current = null;
    if (queued) void push(queued);
  }, [push]);

  // Send a queued write rather than dropping it when the analyst navigates away
  // mid-debounce. Nothing can be rendered after unmount, so this one write
  // cannot raise its error in the UI — every other failure can.
  useEffect(
    () => () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
        const queued = pendingRef.current;
        pendingRef.current = null;
        if (queued) void push(queued);
      }
    },
    [push],
  );

  // ── Hydrate: server first, local only as the migration source ────────────
  const hydratedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!dealId || hydratedFor.current === dealId) return;
    const local = readStore(dealId);
    if (!serverBacked) {
      hydratedFor.current = dealId;
      setLayout(local);
      return;
    }
    if (!deal) return; // the deal row is still in flight — do not decide yet
    hydratedFor.current = dealId;
    const server = layoutFromOverrides(deal.field_overrides);
    if (server) {
      setLayout(server);
      writeStore(dealId, server);
      return;
    }
    // No server layout yet. Adopt whatever this device already built and write
    // it up ONCE, so a layout someone made before the lift is not discarded.
    if (hasEdits(local)) {
      setLayout(local);
      void push(local);
    } else {
      setLayout(EMPTY);
    }
  }, [dealId, deal, serverBacked, push]);

  const persist = useCallback(
    (next: WorksheetLayout) => {
      setLayout(next);
      writeStore(dealId, next);
      if (serverBacked) schedule(next);
    },
    [dealId, serverBacked, schedule],
  );

  /** Re-send the last write after a failure (the "Retry" affordance). */
  const retrySave = useCallback(() => {
    if (!serverBacked) return;
    setSaveError(null);
    void push(layout);
  }, [serverBacked, push, layout]);

  const setLabel = useCallback(
    (rowId: string, label: string) => {
      const relabels = { ...layout.relabels };
      const trimmed = label.trim();
      if (trimmed) relabels[rowId] = trimmed;
      else delete relabels[rowId];
      persist({ ...layout, relabels });
    },
    [layout, persist],
  );

  const toggleHidden = useCallback(
    (rowId: string) => {
      const hidden = layout.hidden.includes(rowId)
        ? layout.hidden.filter((r) => r !== rowId)
        : [...layout.hidden, rowId];
      persist({ ...layout, hidden });
    },
    [layout, persist],
  );

  // Reorder a row within its section's ordered id list. `siblings` is the
  // current on-screen order (canonical defaults + curated) the caller derives.
  const move = useCallback(
    (sectionId: string, rowId: string, dir: -1 | 1, siblings: string[]) => {
      const base = layout.order[sectionId] ?? siblings;
      const ids = base.filter((id) => siblings.includes(id));
      for (const s of siblings) if (!ids.includes(s)) ids.push(s);
      const i = ids.indexOf(rowId);
      const j = i + dir;
      if (i < 0 || j < 0 || j >= ids.length) return;
      [ids[i], ids[j]] = [ids[j], ids[i]];
      persist({ ...layout, order: { ...layout.order, [sectionId]: ids } });
    },
    [layout, persist],
  );

  const addCurated = useCallback(
    (section: string, label: string, value: number) => {
      const line: CuratedLine = { id: newId('cur'), section, label: label.trim() || 'New line', value };
      persist({ ...layout, curated: [...layout.curated, line] });
    },
    [layout, persist],
  );

  const updateCurated = useCallback(
    (id: string, patch: Partial<Pick<CuratedLine, 'label' | 'value'>>) => {
      persist({ ...layout, curated: layout.curated.map((c) => (c.id === id ? { ...c, ...patch } : c)) });
    },
    [layout, persist],
  );

  const removeCurated = useCallback(
    (id: string) => persist({ ...layout, curated: layout.curated.filter((c) => c.id !== id) }),
    [layout, persist],
  );

  const setSplit = useCallback(
    (parentId: string, children: SplitChild[]) => {
      const splits = { ...layout.splits };
      if (children.length) splits[parentId] = children;
      else delete splits[parentId];
      persist({ ...layout, splits });
    },
    [layout, persist],
  );

  const addSplitChild = useCallback(
    (parentId: string, label: string, value: number) => {
      const children = [...(layout.splits[parentId] ?? []), { id: newId('sp'), label: label.trim() || 'Line', value }];
      setSplit(parentId, children);
    },
    [layout.splits, setSplit],
  );

  const updateSplitChild = useCallback(
    (parentId: string, id: string, patch: Partial<Pick<SplitChild, 'label' | 'value'>>) => {
      const children = (layout.splits[parentId] ?? []).map((c) => (c.id === id ? { ...c, ...patch } : c));
      setSplit(parentId, children);
    },
    [layout.splits, setSplit],
  );

  const removeSplitChild = useCallback(
    (parentId: string, id: string) => setSplit(parentId, (layout.splits[parentId] ?? []).filter((c) => c.id !== id)),
    [layout.splits, setSplit],
  );

  const reset = useCallback(() => persist(EMPTY), [persist]);

  const isCustomized = useMemo(() => hasEdits(layout), [layout]);

  return {
    layout,
    isCustomized,
    /** True while a PATCH is in flight. */
    saving,
    /** Non-null when the last write failed — the layout on screen is NOT saved. */
    saveError,
    retrySave,
    flush,
    setLabel,
    toggleHidden,
    move,
    addCurated,
    updateCurated,
    removeCurated,
    setSplit,
    addSplitChild,
    updateSplitChild,
    removeSplitChild,
    reset,
  };
}
