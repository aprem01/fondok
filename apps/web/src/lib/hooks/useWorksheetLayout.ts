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
 * Persistence is device-local (localStorage) for now — structure is a per-
 * viewer view preference, so this is honest and safe; the shape is designed to
 * lift onto a per-deal server field later with a one-file swap (replace the two
 * `readStore`/`writeStore` calls with an API round-trip).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

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

const EMPTY: WorksheetLayout = { v: 1, relabels: {}, hidden: [], order: {}, curated: [], splits: {} };
const keyFor = (dealId: string) => `fondok:wslayout:${dealId}`;

function readStore(dealId: string): WorksheetLayout {
  if (typeof window === 'undefined') return EMPTY;
  try {
    const raw = window.localStorage.getItem(keyFor(dealId));
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<WorksheetLayout>;
    return {
      v: 1,
      relabels: parsed.relabels ?? {},
      hidden: parsed.hidden ?? [],
      order: parsed.order ?? {},
      curated: parsed.curated ?? [],
      splits: parsed.splits ?? {},
    };
  } catch {
    return EMPTY;
  }
}

function writeStore(dealId: string, layout: WorksheetLayout) {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(keyFor(dealId), JSON.stringify(layout));
  } catch {
    /* quota / private mode — layout just won't persist */
  }
}

// Unique id, collision-safe across reloads. This is client-only ('use client'
// + only called from event handlers after hydration), so Date.now / Math.random
// are available; a monotonic counter alone would collide with persisted ids
// after a page reload resets it.
let _counter = 0;
const newId = (prefix: string) =>
  `${prefix}_${Date.now().toString(36)}${(_counter += 1).toString(36)}${Math.random().toString(36).slice(2, 6)}`;

export function useWorksheetLayout(dealId: string) {
  const [layout, setLayout] = useState<WorksheetLayout>(EMPTY);

  useEffect(() => {
    setLayout(readStore(dealId));
  }, [dealId]);

  const persist = useCallback(
    (next: WorksheetLayout) => {
      setLayout(next);
      writeStore(dealId, next);
    },
    [dealId],
  );

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

  const isCustomized = useMemo(
    () =>
      Object.keys(layout.relabels).length > 0 ||
      layout.hidden.length > 0 ||
      layout.curated.length > 0 ||
      Object.keys(layout.splits).length > 0 ||
      Object.keys(layout.order).length > 0,
    [layout],
  );

  return {
    layout,
    isCustomized,
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
