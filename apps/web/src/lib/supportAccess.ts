// Fondok staff support access — the browser side.
//
// While a staff admin has a live support grant for a firm, every worker
// request from THIS TAB carries `X-Support-Tenant: <firm tenant id>` and the
// worker serves it as that firm, read-only, logging each page in the firm's
// own activity log (apps/worker/app/support_access.py). The session lives in
// sessionStorage so it is confined to one tab and ends with it; the worker's
// grant (with its own expiry) is the real authority, this is only a pointer.

export interface SupportSession {
  grantId: string;
  tenantId: string;
  firm: string;
  expiresAt: string;
}

const KEY = 'fondok:support-session';
const EVENT = 'fondok:support-session-changed';
/** Paths the header must never go to: managing grants is not done "as" a firm. */
const EXEMPT = ['/admin/support-access'];

function read(): SupportSession | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw) as SupportSession;
    if (!s?.tenantId || !s?.grantId || !s?.expiresAt) return null;
    if (new Date(s.expiresAt).getTime() <= Date.now()) {
      window.sessionStorage.removeItem(KEY);
      return null;
    }
    return s;
  } catch {
    return null;
  }
}

export function getSupportSession(): SupportSession | null {
  return read();
}

export function setSupportSession(s: SupportSession): void {
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage blocked: support mode simply does not start */
  }
  window.dispatchEvent(new Event(EVENT));
}

export function clearSupportSession(): void {
  try {
    window.sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new Event(EVENT));
}

export function onSupportSessionChange(fn: () => void): () => void {
  window.addEventListener(EVENT, fn);
  return () => window.removeEventListener(EVENT, fn);
}

/** The header to add to a worker request for `path`, if support mode is on. */
export function supportHeaderFor(path: string): Record<string, string> {
  if (EXEMPT.some((p) => path.startsWith(p))) return {};
  const s = read();
  return s ? { 'X-Support-Tenant': s.tenantId } : {};
}
