'use client';

import { useEffect, useState } from 'react';

/**
 * ``false`` during SSR and the client's hydration render, ``true`` after
 * mount. Gate anything that can only be known in the browser — relative
 * "3m ago" labels built from ``Date.now()``, localStorage-driven state — so
 * the server HTML and the first client render match. A mismatch is a React
 * hydration error (#418, then the #423 whole-root client fallback) in prod.
 */
export function useMounted(): boolean {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  return mounted;
}
