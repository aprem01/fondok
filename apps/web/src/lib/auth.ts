/**
 * Auth shim — single seam between the demo persona and real Clerk auth.
 *
 * When `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` is set to a real key, these
 * hooks proxy to Clerk's `useUser()` / `useOrganization()`. When unset
 * (current state) or set to a `_dummy` placeholder, they return the
 * static "Eshan Mehta · Brookfield Real Estate" persona from
 * `lib/mockData.ts` so the demo keeps working end-to-end.
 *
 * The `getCurrentOrgId()` helper is the imperative escape hatch used by
 * `lib/api.ts` to attach `X-Tenant-Id` to outbound worker requests
 * without rewiring every fetch call through React context.
 */
'use client';

import { useEffect } from 'react';
import { useAuth, useUser, useOrganization, useClerk } from '@clerk/nextjs';
import { workspace as mockWorkspace, currentUser as mockUser } from './mockData';

// ─── Role types (Wave 5 RBAC) ────────────────────────────────────────
// Clerk's ``useOrganization().membership?.role`` returns the native
// ``org:admin`` / ``org:member`` strings; we keep that shape on the
// frontend and let the backend normalize to ``admin`` / ``member`` in
// its own logic. ``unknown`` covers "no active org" (personal user or
// demo mode without a synthetic role).
export type AuthRole = 'org:admin' | 'org:member' | 'unknown';

const clerkKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
export const isClerkConfigured =
  !!clerkKey &&
  (clerkKey.startsWith('pk_test_') || clerkKey.startsWith('pk_live_')) &&
  !clerkKey.endsWith('_dummy');

export interface CurrentUser {
  name: string;
  role: string;
  initials: string;
  email: string;
  /** Real Clerk user id, or `'demo'` when running unauthenticated. */
  id: string;
}

export interface CurrentOrg {
  /** Clerk organization id, or `null` when no org / demo mode. */
  id: string | null;
  name: string;
  plan: string;
  url: string;
}

function deriveInitials(first: string | null | undefined, last: string | null | undefined, email: string | null | undefined): string {
  const f = (first || '').trim();
  const l = (last || '').trim();
  if (f || l) return `${f[0] ?? ''}${l[0] ?? ''}`.toUpperCase() || '??';
  const local = (email || '').split('@')[0] || '';
  return (local.slice(0, 2) || '??').toUpperCase();
}

/**
 * Returns the active user — real Clerk user when configured + signed in,
 * otherwise the mock persona. Components can render this without
 * branching on auth state.
 */
export function useCurrentUser(): CurrentUser {
  // Hooks must be called unconditionally. When Clerk isn't configured,
  // `useUser` will throw because there is no Provider — guard with the
  // module-level flag so we only call it when safe.
  if (!isClerkConfigured) {
    return {
      name: mockUser.name,
      role: mockUser.role,
      initials: mockUser.initials,
      email: mockUser.email,
      id: 'demo',
    };
  }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const { user, isLoaded } = useUser();
  if (!isLoaded || !user) {
    // Pre-load fallback so we don't flash the mock persona during the
    // brief window before Clerk hydrates. Render a clean skeleton via
    // empty strings so the UI doesn't show a "Loading…" placeholder.
    return {
      name: ' ',
      role: ' ',
      initials: ' ',
      email: '',
      id: 'pending',
    };
  }
  const email = user.primaryEmailAddress?.emailAddress ?? '';
  const name = [user.firstName, user.lastName].filter(Boolean).join(' ') || email || 'User';
  return {
    name,
    role: (user.publicMetadata?.role as string | undefined) ?? 'Analyst',
    initials: deriveInitials(user.firstName, user.lastName, email),
    email,
    id: user.id,
  };
}

/**
 * Returns the active organization. In Clerk this maps to the user's
 * currently selected Organization (the workspace switcher selection).
 * In demo mode returns the static Brookfield Real Estate workspace.
 */
export function useCurrentOrg(): CurrentOrg {
  if (!isClerkConfigured) {
    return {
      id: null,
      name: mockWorkspace.name,
      plan: mockWorkspace.plan,
      url: mockWorkspace.url,
    };
  }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const { organization } = useOrganization();
  if (!organization) {
    // Personal workspace — no org selected. Pretend it's a "Personal"
    // workspace so the sidebar pill still renders something useful.
    return {
      id: null,
      name: 'Personal Workspace',
      plan: 'Free',
      url: 'personal',
    };
  }
  return {
    id: organization.id,
    name: organization.name,
    plan: (organization.publicMetadata?.plan as string | undefined) ?? 'Pro Plan',
    url: organization.slug ?? organization.id,
  };
}

/**
 * Sign-out helper. Calls Clerk's signOut when configured; in demo mode
 * it's a no-op (the caller should toast something the user can see).
 */
export function useSignOut(): () => Promise<void> {
  if (!isClerkConfigured) {
    return async () => {
      /* no-op in demo mode */
    };
  }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const clerk = useClerk();
  return async () => {
    await clerk.signOut();
  };
}

// ────────────────────── imperative org id (for fetch) ──────────────────────
//
// `lib/api.ts` is plain TS (not React) so it can't call `useOrganization`.
// We mirror the active org id into a module-level singleton via a
// listener installed at the AppShell level. When Clerk is unconfigured
// this stays null and the worker falls back to its DEFAULT_TENANT_ID.

let _activeOrgId: string | null = null;

export function setCurrentOrgId(id: string | null): void {
  _activeOrgId = id;
}

export function getCurrentOrgId(): string | null {
  return _activeOrgId;
}

// ────────────────────── current role (Wave 5 RBAC) ────────────────────
//
// Returns the caller's role in the active Clerk organization. Demo
// mode returns ``org:admin`` because the demo persona is a single-user
// solo instance — there is no member/admin distinction to enforce, and
// the backend's "header-only source is trusted" fallback treats the
// same request as admin. When Clerk is configured but no org is
// selected (personal workspace) we return ``unknown`` so admin-only UI
// stays hidden.

export function useCurrentRole(): AuthRole {
  if (!isClerkConfigured) {
    // Demo persona — treat as admin. See note above.
    return 'org:admin';
  }
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const { organization, membership, isLoaded } = useOrganization();
  if (!isLoaded) return 'unknown';
  if (!organization || !membership) return 'unknown';
  const role = membership.role;
  if (role === 'org:admin' || role === 'org:member') return role;
  // Any custom role Clerk hands back (e.g. ``org:owner`` on legacy
  // orgs) falls through to ``unknown`` — the UI treats it as
  // non-admin. Widen this later when we introduce a third role.
  return 'unknown';
}

// ─────────────── imperative session-JWT (for fetch) ───────────────────
//
// ``lib/api.ts`` is plain TS (not React) and can't call ``useAuth`` on
// every worker request. We mirror Clerk's ``getToken`` fn into a
// module-level singleton via ``ClerkTokenBridge`` mounted in AppShell.
// ``getClerkSessionToken`` reads the singleton, caches the JWT in
// memory for its lifetime, and refetches ~30s before ``exp``.
//
// Cache invariant: we ONLY cache tokens whose ``exp`` we can decode.
// If the JWT is opaque or missing ``exp`` we skip caching entirely so
// we don't wedge on a stale token forever.

type ClerkGetTokenFn = (opts?: {
  template?: string;
  organizationId?: string;
  leewayInSeconds?: number;
  skipCache?: boolean;
}) => Promise<string | null>;

let _clerkGetToken: ClerkGetTokenFn | null = null;
let _cachedToken: string | null = null;
let _cachedTokenExpiryMs: number | null = null;

/** Buffer before real ``exp`` at which we treat the cached token as
 *  stale and refetch. 30s covers a slow request that started right
 *  before expiry without letting an already-expired token slip out. */
const TOKEN_EXPIRY_BUFFER_MS = 30_000;

export function setClerkGetTokenFn(fn: ClerkGetTokenFn | null): void {
  _clerkGetToken = fn;
  // Any handoff — sign-in, sign-out, org switch — invalidates the
  // cached token; a stale JWT signed for the previous session/org
  // would be rejected by the worker.
  _cachedToken = null;
  _cachedTokenExpiryMs = null;
}

/**
 * Best-effort decode of the ``exp`` claim (seconds since epoch) from a
 * JWT. Returns null when the token isn't a well-formed JWT or the
 * claim is missing — the caller should treat "unknown expiry" as
 * "don't cache". No signature check: expiry is a hint, not a trust
 * decision (the worker is the one enforcing it).
 */
function _parseJwtExp(token: string): number | null {
  const parts = token.split('.');
  if (parts.length < 2) return null;
  try {
    // JWT uses base64url; convert to standard base64 for atob.
    const payload = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    // Pad to multiple of 4 or atob will throw on some browsers.
    const padded = payload + '='.repeat((4 - (payload.length % 4)) % 4);
    const decoded =
      typeof atob === 'function'
        ? atob(padded)
        : Buffer.from(padded, 'base64').toString('binary');
    const claims = JSON.parse(decoded) as { exp?: number };
    if (typeof claims.exp !== 'number') return null;
    return claims.exp * 1000;
  } catch {
    return null;
  }
}

/**
 * Returns the current Clerk session JWT for outbound worker requests.
 * Returns ``null`` when Clerk isn't configured (demo persona — the
 * worker's header-trust fallback covers this) or the user isn't signed
 * in yet. In-memory cached for the JWT's remaining lifetime minus a
 * 30s buffer.
 *
 * Backend contract: web sends ``Authorization: Bearer <jwt>`` on every
 * worker request in addition to the legacy ``X-Tenant-Id`` header.
 */
export async function getClerkSessionToken(): Promise<string | null> {
  if (!_clerkGetToken) return null;
  const now = Date.now();
  if (
    _cachedToken &&
    _cachedTokenExpiryMs !== null &&
    now < _cachedTokenExpiryMs - TOKEN_EXPIRY_BUFFER_MS
  ) {
    return _cachedToken;
  }
  try {
    // The ``worker`` template lets us mint a JWT with claims tuned for
    // the FastAPI worker (custom ``aud``, tenant claim, etc.) when the
    // Clerk dashboard has one configured. Fall back to the default
    // session token when the template isn't set up — Clerk raises a
    // 404 in that case and we retry without the template.
    let token: string | null = null;
    try {
      token = await _clerkGetToken({ template: 'worker' });
    } catch {
      token = await _clerkGetToken();
    }
    if (!token) {
      _cachedToken = null;
      _cachedTokenExpiryMs = null;
      return null;
    }
    const expiryMs = _parseJwtExp(token);
    _cachedToken = token;
    _cachedTokenExpiryMs = expiryMs;
    return token;
  } catch {
    // Never let a token fetch error crash the request path — the
    // worker will 401 on missing auth, and the UI surfaces that.
    _cachedToken = null;
    _cachedTokenExpiryMs = null;
    return null;
  }
}

/**
 * Mount-once component that pipes Clerk's ``useAuth().getToken`` into
 * the module singleton so ``lib/api.ts`` can attach ``Authorization``
 * headers without dragging React context into every fetch call.
 * Rendered from AppShell; no-op when Clerk isn't configured.
 */
export function ClerkTokenBridge(): null {
  if (!isClerkConfigured) return null;
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const { getToken, sessionId } = useAuth();
  // eslint-disable-next-line react-hooks/rules-of-hooks
  const { organization } = useOrganization();
  const orgId = organization?.id ?? null;
  // Re-install the getter whenever the session or active org changes;
  // Clerk's ``getToken`` closes over the current session, so a cached
  // reference silently returns tokens for the wrong org after a
  // switcher click.
  // eslint-disable-next-line react-hooks/rules-of-hooks
  useEffect(() => {
    setClerkGetTokenFn(getToken as ClerkGetTokenFn);
    return () => {
      setClerkGetTokenFn(null);
    };
  }, [getToken, sessionId, orgId]);
  return null;
}
