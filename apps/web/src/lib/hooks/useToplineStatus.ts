'use client';

/**
 * FON-116 / FON-117 — the topline status, read from the deal's provenance
 * payload (`GET /deals/{id}/assumption_sources` → `topline_status`).
 *
 * Its own context for the same reason as `useDisplacementStatus`: the many
 * harnesses that mock `@/lib/hooks/useDealProvenance` down to `{ useSource }`
 * keep rendering, with no provider → `null` → nothing printed, exactly like a
 * worker that predates topline v2.
 */

import { createContext, useContext } from 'react';
import type { ToplineStatus } from '@/lib/api';

export const ToplineStatusContext = createContext<ToplineStatus | null>(null);

/** The deal's topline status, or `null` while loading / absent / no provider. */
export function useToplineStatus(): ToplineStatus | null {
  return useContext(ToplineStatusContext);
}
