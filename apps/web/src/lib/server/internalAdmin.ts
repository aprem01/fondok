import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';

/**
 * Server-side gate for the Firms admin routes: the caller must be signed in
 * AND be an admin of a serving internal tenant. The worker decides the
 * second part (`GET /admin/tenants` answers `scope: "all"` only for that
 * caller), so there is one rule for "Fondok admin", not two.
 */
export type RegistryTenant = { id: string; clerk_org_id: string; name: string; kind: 'internal' | 'external'; status: string };
export type InternalAdmin = { userId: string; token: string; workerUrl: string; tenants: RegistryTenant[] };

export const workerUrl = () => (process.env.NEXT_PUBLIC_WORKER_URL ?? '').replace(/\/+$/, '');

export async function requireInternalAdmin(req: Request): Promise<InternalAdmin | NextResponse> {
  // These routes are authenticated by the session cookie, so refuse anything
  // that is not a same-origin JSON request from this app (CSRF).
  const origin = req.headers.get('origin');
  const fetchSite = req.headers.get('sec-fetch-site');
  const isJson = (req.headers.get('content-type') ?? '').toLowerCase().startsWith('application/json');
  if (!isJson || origin !== new URL(req.url).origin || (fetchSite && fetchSite !== 'same-origin')) {
    return NextResponse.json({ error: 'forbidden' }, { status: 403 });
  }
  const { userId, getToken } = await auth();
  if (!userId) return NextResponse.json({ error: 'not_signed_in' }, { status: 401 });
  // Same token the browser sends the worker: the `worker` JWT template,
  // falling back to the plain session token where the template is not set up
  // (mirrors ClerkTokenBridge in lib/auth.ts).
  let token: string | null = null;
  try {
    token = await getToken({ template: 'worker' });
  } catch {
    token = null;
  }
  if (!token) token = await getToken();
  const base = workerUrl();
  if (!token || !base) return NextResponse.json({ error: 'not_available' }, { status: 503 });
  let res: Response;
  try {
    res = await fetch(`${base}/admin/tenants`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    });
  } catch {
    return NextResponse.json({ error: 'worker_unreachable' }, { status: 502 });
  }
  if (!res.ok) return NextResponse.json({ error: 'forbidden' }, { status: 403 });
  const body = (await res.json().catch(() => null)) as { scope?: string; tenants?: RegistryTenant[] } | null;
  if (body?.scope !== 'all') return NextResponse.json({ error: 'forbidden' }, { status: 403 });
  return { userId, token, workerUrl: base, tenants: Array.isArray(body.tenants) ? body.tenants : [] };
}

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Clerk errors carry `errors[0].longMessage`; anything else gets a generic line. */
export function clerkErrorMessage(err: unknown, fallback: string): string {
  const e = err as { errors?: { longMessage?: string; message?: string }[] };
  return e?.errors?.[0]?.longMessage ?? e?.errors?.[0]?.message ?? fallback;
}

/**
 * Full launch (Prem, 2026-11-07). Until then Fondok staff may hold standing
 * admin memberships in client firms ("Join as admin" on the Firms page). The
 * window closes by code on this date; `PRE_LAUNCH_STAFF_MEMBERSHIP=0` closes
 * it early. Mirrors `FULL_LAUNCH` in apps/worker/app/support_access.py.
 */
export const FULL_LAUNCH = new Date('2027-06-27T00:00:00Z');

export function preLaunchMembershipOpen(now: Date = new Date()): boolean {
  if (process.env.PRE_LAUNCH_STAFF_MEMBERSHIP === '0') return false;
  return now.getTime() < FULL_LAUNCH.getTime();
}
