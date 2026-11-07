/**
 * FON-120 (Sam's QA) — "Methodology · Detail" is for admins of an internal
 * workspace. The sidebar asks the worker, using the same rule as the page's
 * gate, and hides the link until it has a yes.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, cleanup, waitFor, act } from '@testing-library/react';

const h = vi.hoisted(() => {
  process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY = 'pk_test_sidebar_internal_only';
  return {
    org: { id: 'org_a', name: 'A', slug: 'a', publicMetadata: {} },
    role: 'org:admin',
    list: vi.fn(),
  };
});
vi.mock('@clerk/nextjs', () => ({
  useUser: () => ({
    isLoaded: true,
    isSignedIn: true,
    user: {
      id: 'user_1',
      firstName: 'Ada',
      lastName: 'Analyst',
      primaryEmailAddress: { emailAddress: 'ada@institution.test' },
      publicMetadata: {},
      organizationMemberships: [{ organization: { id: h.org.id } }],
    },
  }),
  useOrganization: () => ({
    isLoaded: true,
    organization: h.org,
    membership: { role: h.role },
    memberships: { data: [] },
  }),
  useAuth: () => ({ getToken: async () => null, sessionId: 'sess_1' }),
  useClerk: () => ({ signOut: async () => {}, setActive: async () => {} }),
  OrganizationSwitcher: () => React.createElement('div', { 'data-testid': 'org-switcher' }),
}));
vi.mock('next/navigation', () => ({
  usePathname: () => '/dashboard',
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: { ...actual.api, admin: { ...actual.api.admin, tenants: { ...actual.api.admin.tenants, list: h.list } } },
  };
});

import Sidebar from '@/components/layout/Sidebar';

const LINK = 'Methodology · Detail';

beforeEach(() => {
  h.org = { id: 'org_a', name: 'A', slug: 'a', publicMetadata: {} };
  h.role = 'org:admin';
  h.list.mockReset();
});
afterEach(cleanup);

describe('Methodology · Detail link', () => {
  it('shows for an admin of an internal workspace', async () => {
    h.list.mockResolvedValue({ scope: 'all', caller_tenant_id: 't', tenants: [] });
    render(<Sidebar />);
    await waitFor(() => expect(screen.getByText(LINK)).toBeInTheDocument());
  });

  it('is hidden for an admin of an external workspace', async () => {
    h.list.mockResolvedValue({ scope: 'own', caller_tenant_id: 't', tenants: [] });
    render(<Sidebar />);
    await waitFor(() => expect(h.list).toHaveBeenCalled());
    await act(async () => {});
    expect(screen.queryByText(LINK)).toBeNull();
    // The plain admin link is untouched.
    expect(screen.getByText('Cost')).toBeInTheDocument();
  });

  it('stays hidden until the worker answers, and when the check fails', async () => {
    let fail: (e: Error) => void = () => {};
    h.list.mockReturnValue(new Promise((_, reject) => { fail = reject; }));
    render(<Sidebar />);
    await waitFor(() => expect(h.list).toHaveBeenCalled());
    expect(screen.queryByText(LINK)).toBeNull();
    await act(async () => { fail(new Error('down')); });
    expect(screen.queryByText(LINK)).toBeNull();
  });

  it('is hidden for a workspace member the worker does not call a Fondok admin', async () => {
    h.role = 'org:member';
    h.list.mockRejectedValueOnce(new Error('403'));
    render(<Sidebar />);
    await act(async () => {});
    expect(screen.queryByText(LINK)).toBeNull();
  });

  it('is shown for a Fondok staff admin who is only a member of this workspace', async () => {
    h.role = 'org:member';
    h.list.mockResolvedValueOnce({ scope: 'all', caller_tenant_id: 't', tenants: [] });
    render(<Sidebar />);
    await waitFor(() => expect(screen.getByText(LINK)).toBeInTheDocument());
  });

  it('disappears at once on switching to an external workspace', async () => {
    h.list.mockResolvedValueOnce({ scope: 'all', caller_tenant_id: 't', tenants: [] });
    const { rerender } = render(<Sidebar />);
    await waitFor(() => expect(screen.getByText(LINK)).toBeInTheDocument());
    h.list.mockReturnValue(new Promise(() => {}));
    h.org = { id: 'org_b', name: 'B', slug: 'b', publicMetadata: {} };
    rerender(<Sidebar />);
    expect(screen.queryByText(LINK)).toBeNull();
  });
});
