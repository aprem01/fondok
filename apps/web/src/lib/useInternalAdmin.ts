'use client';

import { useEffect, useState } from 'react';
import { api, isWorkerConnected } from '@/lib/api';
import type { AuthRole } from '@/lib/auth';

/**
 * Is the caller an admin of an internal workspace? The worker answers:
 * `GET /admin/tenants` has scope `all` only for a serving internal tenant, the
 * same rule that gates the detailed methodology. For hiding a link only; the
 * worker still enforces access to the content.
 *
 * Fails closed: false until a yes arrives, on any error, and from the moment
 * the workspace changes. The yes is stored against the org it was asked for.
 */
export function useIsInternalAdmin(role: AuthRole, orgId: string | null): boolean {
  const [internalOrgId, setInternalOrgId] = useState<string | null>(null);

  useEffect(() => {
    if (role !== 'org:admin' || !orgId || !isWorkerConnected()) return;
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

  return role === 'org:admin' && orgId != null && internalOrgId === orgId;
}
