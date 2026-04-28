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

import { useUser, useOrganization, useClerk } from '@clerk/nextjs';
import { workspace as mockWorkspace, currentUser as mockUser } from './mockData';

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
