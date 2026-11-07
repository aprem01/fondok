'use client';

import { useEffect, useState } from 'react';
import { api, isWorkerConnected } from '@/lib/api';
import type { AuthRole } from '@/lib/auth';

/**
 * Is the caller a Fondok admin? The worker answers: `GET /admin/tenants` has
 * scope `all` for a Fondok staff admin (an account-level role, in any
 * workspace and whatever their role there) or for an admin of a serving
 * internal workspace — the same rule that gates every Fondok-admin view. For
 * hiding a link only; the worker still enforces access to the content.
 *
 * Fails closed: false until a yes arrives, on any error, and from the moment
 * the workspace changes. The yes is stored against the org it was asked for.
 */
export function useIsInternalAdmin(role: AuthRole, orgId: string | null): boolean {
  const [internalOrgId, setInternalOrgId] = useState<string | null>(null);

  useEffect(() => {
    // Ask whatever the workspace role: a staff admin may be a plain member of
    // the client workspace they are in. `unknown` = not loaded yet.
    if (role === 'unknown' || !orgId || !isWorkerConnected()) return;
    const ctl = new AbortController();
    api.admin.tenants
      .list(ctl.signal)
      .then((res) => {
        if (!ctl.signal.aborted) setInternalOrgId(res.scope === 'all' ? orgId : null);
      })
      .catch(() => {
        if (!ctl.signal.aborted) setInternalOrgId(null);
      });
    return () => ctl.abort();
  }, [role, orgId]);

  return role !== 'unknown' && orgId != null && internalOrgId === orgId;
}
