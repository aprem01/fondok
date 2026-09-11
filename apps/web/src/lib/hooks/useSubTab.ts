'use client';

import { useCallback, useEffect, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

/**
 * `?tab=<tab>&sub=<subtab>` — the one sub-tab routing convention.
 *
 * FON-59 #4 / FON-61 §3: every "→ Financials (projections)" deep link used to
 * land on Historicals because each host tab invented its own sub-tab state and
 * the emitters carried only a top-level tab id. This hook is the shared half of
 * the fix, lifted from the only complete implementation in the repo
 * (`AnalysisTab`'s `?sub=` read + `useEffect` re-sync + `router.replace`):
 *
 *  - reads `?sub=` (and `opts.legacyParam` — `fin` — as an accepted alias for
 *    one release so the Data Room's existing deep links / bookmarks keep
 *    working),
 *  - re-syncs on every `searchParams` change, so a link followed while the
 *    component is already mounted is honoured (the old `useState`-initializer
 *    reads silently ignored it),
 *  - `setSub` reflects a manual sub-tab click back into the URL with
 *    `router.replace(..., { scroll: false })`, **preserving every other query
 *    param** (`doc`, `focus`, `reviewField`, …) — only the legacy alias is
 *    dropped, since `sub` now carries the same intent.
 *
 * Values are lowercase slugs (`projections`, `historicals`, …), never display
 * labels.
 */
export function useSubTab<T extends string>(
  ids: readonly T[],
  fallback: T,
  opts?: { legacyParam?: string },
): { sub: T; setSub: (id: T) => void } {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const legacyParam = opts?.legacyParam;

  const read = (): T => {
    const raw = searchParams?.get('sub')
      ?? (legacyParam ? searchParams?.get(legacyParam) ?? null : null);
    return raw != null && (ids as readonly string[]).includes(raw) ? (raw as T) : fallback;
  };

  const [sub, setSubState] = useState<T>(read);

  // Defect 3 — a param change under a mounted component must move the sub-tab.
  useEffect(() => {
    const next = read();
    setSubState((cur) => (next === cur ? cur : next));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const setSub = useCallback(
    (id: T) => {
      setSubState(id);
      const qp = new URLSearchParams(searchParams?.toString() ?? '');
      qp.set('sub', id);
      if (legacyParam) qp.delete(legacyParam);
      const query = qp.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams, legacyParam],
  );

  return { sub, setSub };
}

export default useSubTab;
