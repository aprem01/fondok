'use client';

import { useEffect, useState } from 'react';

/**
 * useNow — a wall-clock sample that re-renders the caller every ``tickMs``
 * while ``active`` is true.
 *
 * Elapsed timers (document stage timers, the wizard upload counter, the
 * engine-run strip) derive their display from ``now - startedAt``; this hook
 * is the single ticker that keeps them moving. One instance per surface —
 * rows read the sampled ``now`` from their parent instead of each owning an
 * interval. The interval is cleared when ``active`` flips false or the
 * component unmounts, so idle surfaces never tick.
 */
export function useNow(active: boolean, tickMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    // Re-sample immediately so a surface that just became active doesn't
    // show a stale first frame, then once per tick.
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), tickMs);
    return () => clearInterval(id);
  }, [active, tickMs]);
  return now;
}
