/**
 * FON-93 review finding — with ``hidePersonal`` on the OrganizationSwitcher, a
 * signed-in user who belongs to NO organization used to see a "Personal"
 * pill over a dead-end switcher while every worker call 409'd
 * ``no_active_organization``. The Sidebar now says "No workspace assigned —
 * ask Fondok to add you to your institution's workspace", and never invents
 * an org. A user who belongs to an org that is not yet active is `pending`
 * (ClerkTokenBridge activates it) and sees the switcher, not the notice.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, act } from '@testing-library/react';
import { renderToString } from 'react-dom/server';

const clerk = vi.hoisted(() => {
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = 'pk_test_sidebar_no_workspace';
  return {
    loaded: true,
    organization: null as null | { id: string; name: string; slug: string; publicMetadata: Record<string, unknown> },
    memberships: [] as Array<{ organization: { id: string } }>,
  };
});
vi.mock('@clerk/nextjs', () => ({
  useUser: () =>
    clerk.loaded
      ? {
          isLoaded: true,
          isSignedIn: true,
          user: {
            id: 'user_1',
            firstName: 'Ada',
            lastName: 'Analyst',
            primaryEmailAddress: { emailAddress: 'ada@institution.test' },
            publicMetadata: {},
            organizationMemberships: clerk.memberships,
          },
        }
      : { isLoaded: false, isSignedIn: undefined, user: undefined },
  useOrganization: () =>
    clerk.loaded
      ? {
          isLoaded: true,
          organization: clerk.organization ?? undefined,
          membership: clerk.organization ? { role: 'org:member' } : undefined,
          memberships: { data: [] },
        }
      : { isLoaded: false, organization: undefined, membership: undefined, memberships: undefined },
  useAuth: () => ({ getToken: async () => null, sessionId: 'sess_1' }),
  useClerk: () => ({ signOut: async () => {}, setActive: async () => {} }),
  OrganizationSwitcher: () => React.createElement('div', { 'data-testid': 'org-switcher' }),
}));
vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import Sidebar from '@/components/layout/Sidebar';
import { NO_WORKSPACE_MESSAGE } from '@/lib/auth';

afterEach(() => {
  cleanup();
  clerk.loaded = true;
  clerk.organization = null;
  clerk.memberships = [];
});

describe('Sidebar — a signed-in user with no organization', () => {
  it('shows "No workspace assigned — ask Fondok…" instead of a Personal pill or the switcher', async () => {
    clerk.organization = null;
    clerk.memberships = [];
    render(<Sidebar />);
    const notice = await screen.findByTestId('no-workspace-notice');
    expect(notice).toHaveTextContent('No workspace assigned');
    expect(notice).toHaveTextContent("Ask Fondok to add you to your institution's workspace");
    expect(screen.queryByTestId('org-switcher')).toBeNull();
    expect(document.body.textContent).not.toMatch(/Personal/);
    expect(NO_WORKSPACE_MESSAGE).toMatch(/No workspace assigned — ask Fondok/);
  });

  it('with an active organization the switcher renders and there is no notice', async () => {
    clerk.organization = { id: 'org_1', name: 'Institution Capital', slug: 'institution', publicMetadata: {} };
    clerk.memberships = [{ organization: { id: 'org_1' } }];
    render(<Sidebar />);
    expect(await screen.findByTestId('org-switcher')).toBeInTheDocument();
    expect(screen.queryByTestId('no-workspace-notice')).toBeNull();
  });

  it('a member of an org that is not active yet is pending (the bridge activates it) — switcher, no notice', async () => {
    clerk.organization = null;
    clerk.memberships = [{ organization: { id: 'org_1' } }];
    render(<Sidebar />);
    expect(await screen.findByTestId('org-switcher')).toBeInTheDocument();
    expect(screen.queryByTestId('no-workspace-notice')).toBeNull();
    expect(document.body.textContent).not.toMatch(/Personal/);
  });

  it('the server render (Clerk not loaded) never carries the notice — it is a post-mount state', async () => {
    clerk.loaded = false;
    const server = renderToString(<Sidebar />);
    expect(server).not.toContain('No workspace assigned');
    expect(server).not.toContain('Personal');
    // And the client's first render with Clerk loaded but no org is identical
    // to the server's (hydration determinism): the notice appears after mount.
    clerk.loaded = true;
    const firstClient = renderToString(<Sidebar />);
    expect(firstClient).toBe(server);
    await act(async () => {});
  });
});
