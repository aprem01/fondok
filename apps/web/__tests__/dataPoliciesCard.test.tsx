/**
 * FON-116 / FON-96 — Settings → "Data policies" card (the CoStar licensing gate).
 *
 * Internal admins see every registered workspace with its policy and a switch
 * that PATCHes ``/admin/tenants/{id}/data_policies``. Everyone else — members,
 * external admins (the worker scopes their list to ``own``), and any admin on a
 * worker that predates the route (rows carry no ``data_policies``) — sees no
 * card. The copy never claims CoStar is licensed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { AdminTenantsResponse, RegisteredTenant } from '@/lib/api';

const hoisted = vi.hoisted(() => ({
  role: 'org:admin' as string,
  list: vi.fn<() => Promise<unknown>>(),
  setDataPolicies: vi.fn<(id: string, patch: unknown) => Promise<unknown>>(),
  toast: vi.fn(),
}));

vi.mock('@/lib/auth', () => ({
  useCurrentRole: () => hoisted.role,
}));
vi.mock('@/components/ui/Toast', () => ({
  useToast: () => ({ toast: hoisted.toast }),
}));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      admin: {
        ...actual.api.admin,
        tenants: { list: hoisted.list, setDataPolicies: hoisted.setDataPolicies },
      },
    },
  };
});
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/methodology',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

import DataPoliciesCard, { COSTAR_POLICY_LABEL } from '@/components/settings/DataPoliciesCard';
import MethodologyPage from '@/app/methodology/page';

const INTERNAL: RegisteredTenant = {
  id: '11111111-1111-4111-8111-111111111111',
  clerk_org_id: 'org_FondokInternal',
  name: 'Fondok Internal',
  kind: 'internal',
  status: 'active',
  created_at: null,
  data_policies: { costar_derived_defaults: 'allow' },
};
const CUSTOMER: RegisteredTenant = {
  id: '22222222-2222-4222-8222-222222222222',
  clerk_org_id: 'org_HarborPoint',
  name: 'Harbor Point Capital',
  kind: 'external',
  status: 'active',
  created_at: null,
  data_policies: { costar_derived_defaults: 'allow' },
};

function listing(tenants: RegisteredTenant[], scope: AdminTenantsResponse['scope'] = 'all'): AdminTenantsResponse {
  return { scope, caller_tenant_id: INTERNAL.id, tenants };
}

beforeEach(() => {
  hoisted.role = 'org:admin';
  hoisted.list.mockReset();
  hoisted.setDataPolicies.mockReset();
  hoisted.toast.mockReset();
});
afterEach(cleanup);

describe('Data policies card', () => {
  it('shows every workspace with its policy for an internal admin and never says CoStar is licensed', async () => {
    hoisted.list.mockResolvedValue(listing([INTERNAL, { ...CUSTOMER, data_policies: { costar_derived_defaults: 'withhold' } }]));
    render(<DataPoliciesCard />);
    const card = await screen.findByTestId('settings-data-policies');
    expect(within(card).getByText('Data policies')).toBeInTheDocument();
    expect(screen.getByTestId(`data-policy-state-${INTERNAL.id}`)).toHaveTextContent('offered');
    expect(screen.getByTestId(`data-policy-state-${CUSTOMER.id}`)).toHaveTextContent('withheld (licence pending)');
    expect(card.textContent).toContain('CoStar-derived defaults:');
    expect(card.textContent).not.toMatch(/\blicensed\b/i);
    expect(COSTAR_POLICY_LABEL).toEqual({ allow: 'offered', withhold: 'withheld (licence pending)' });

    const switches = within(card).getAllByRole('switch');
    expect(switches).toHaveLength(2);
    expect(within(card).getByRole('switch', { name: 'CoStar-derived defaults for Fondok Internal' })).toHaveAttribute('aria-checked', 'true');
    expect(within(card).getByRole('switch', { name: 'CoStar-derived defaults for Harbor Point Capital' })).toHaveAttribute('aria-checked', 'false');
  });

  it('toggling calls the PATCH with the new value and reflects the stored answer', async () => {
    hoisted.list.mockResolvedValue(listing([INTERNAL, CUSTOMER]));
    hoisted.setDataPolicies.mockResolvedValue({
      tenant_id: CUSTOMER.id,
      data_policies: { costar_derived_defaults: 'withhold' },
    });
    render(<DataPoliciesCard />);
    const sw = await screen.findByRole('switch', { name: 'CoStar-derived defaults for Harbor Point Capital' });
    fireEvent.click(sw);
    await waitFor(() =>
      expect(hoisted.setDataPolicies).toHaveBeenCalledWith(CUSTOMER.id, { costar_derived_defaults: 'withhold' }),
    );
    await waitFor(() =>
      expect(screen.getByTestId(`data-policy-state-${CUSTOMER.id}`)).toHaveTextContent('withheld (licence pending)'),
    );
    expect(sw).toHaveAttribute('aria-checked', 'false');
    // The other row is untouched.
    expect(screen.getByTestId(`data-policy-state-${INTERNAL.id}`)).toHaveTextContent('offered');
    expect(hoisted.toast).toHaveBeenCalledWith(
      'Harbor Point Capital: CoStar-derived defaults withheld (licence pending)',
      { type: 'success' },
    );
  });

  it('a failed PATCH leaves the shown value alone and reports the error', async () => {
    hoisted.list.mockResolvedValue(listing([CUSTOMER]));
    hoisted.setDataPolicies.mockRejectedValue(new Error('boom'));
    render(<DataPoliciesCard />);
    const sw = await screen.findByRole('switch');
    fireEvent.click(sw);
    await waitFor(() => expect(hoisted.toast).toHaveBeenCalled());
    expect(hoisted.toast.mock.calls[0][1]).toEqual({ type: 'error' });
    expect(screen.getByTestId(`data-policy-state-${CUSTOMER.id}`)).toHaveTextContent('offered');
    expect(sw).toHaveAttribute('aria-checked', 'true');
  });

  it('is absent for a member, without calling the worker', async () => {
    hoisted.role = 'org:member';
    hoisted.list.mockResolvedValue(listing([INTERNAL, CUSTOMER]));
    const { container } = render(<DataPoliciesCard />);
    await Promise.resolve();
    expect(container).toBeEmptyDOMElement();
    expect(hoisted.list).not.toHaveBeenCalled();
  });

  it("is absent for an external admin (the worker scopes the list to 'own')", async () => {
    hoisted.list.mockResolvedValue(listing([CUSTOMER], 'own'));
    const { container } = render(<DataPoliciesCard />);
    await waitFor(() => expect(hoisted.list).toHaveBeenCalled());
    await Promise.resolve();
    expect(container).toBeEmptyDOMElement();
  });

  it('is absent on a worker that predates the route (rows carry no data_policies) and on a refused list', async () => {
    const legacy = [INTERNAL, CUSTOMER].map(({ data_policies: _dp, ...rest }) => rest as RegisteredTenant);
    hoisted.list.mockResolvedValue(listing(legacy));
    const first = render(<DataPoliciesCard />);
    await waitFor(() => expect(hoisted.list).toHaveBeenCalledTimes(1));
    await Promise.resolve();
    expect(first.container).toBeEmptyDOMElement();
    cleanup();

    hoisted.list.mockRejectedValue(Object.assign(new Error('forbidden'), { status: 403 }));
    const second = render(<DataPoliciesCard />);
    await waitFor(() => expect(hoisted.list).toHaveBeenCalledTimes(2));
    await Promise.resolve();
    expect(second.container).toBeEmptyDOMElement();
    expect(hoisted.toast).not.toHaveBeenCalled();
  });
});

describe('Methodology — market defaults may be withheld (FON-116)', () => {
  it('states under §3 that a vendor-derived default may be withheld for a workspace until licensing is confirmed', () => {
    render(<MethodologyPage />);
    const p = screen.getByTestId('market-data-policy-methodology');
    expect(p).toHaveTextContent('Market defaults may be withheld for a workspace.');
    expect(p).toHaveTextContent('until that licensing is confirmed');
    expect(p).toHaveTextContent('shown as not applied with its reason, never replaced by a number');
    // Under §3 "Where market assumptions come from" (the #sources section).
    expect(p.closest('#sources')).not.toBeNull();
  });
});
