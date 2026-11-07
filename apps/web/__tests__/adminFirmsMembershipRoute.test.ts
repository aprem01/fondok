// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const authMock = vi.fn();
const createOrganizationMembership = vi.fn();
const deleteOrganizationMembership = vi.fn();
const updateOrganization = vi.fn();
const getOrganizationMembershipList = vi.fn();
const getUser = vi.fn();

vi.mock('@clerk/nextjs/server', () => ({
  auth: () => authMock(),
  clerkClient: async () => ({
    organizations: { createOrganizationMembership, deleteOrganizationMembership, updateOrganization },
    users: { getOrganizationMembershipList, getUser },
  }),
}));

const HEADERS = { origin: 'https://app.example', 'content-type': 'application/json' };
const TENANT = '11111111-2222-4333-8444-555555555555';
const req = (method: string, body?: unknown) =>
  new Request('https://app.example/api/admin/firms/membership', {
    method,
    headers: HEADERS,
    body: body === undefined ? undefined : JSON.stringify(body),
  });

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubEnv('NEXT_PUBLIC_WORKER_URL', 'https://worker.example');
  vi.stubEnv('PRE_LAUNCH_STAFF_MEMBERSHIP', '');
  vi.stubGlobal('fetch', fetchMock);
  for (const m of [authMock, fetchMock, createOrganizationMembership, deleteOrganizationMembership, updateOrganization, getOrganizationMembershipList, getUser]) m.mockReset();
  authMock.mockResolvedValue({ userId: 'user_1', getToken: async () => 'tok' });
  getUser.mockResolvedValue({ primaryEmailAddressId: 'e1', emailAddresses: [{ id: 'e1', emailAddress: 'prem@fondok.ai' }] });
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});
const workerScope = (scope: string) =>
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ scope, tenants: [] }), { status: 200 }));

describe('/api/admin/firms/membership', () => {
  it('GET reports the window and the caller’s memberships', async () => {
    workerScope('all');
    getOrganizationMembershipList.mockResolvedValue({ data: [{ organization: { id: 'org_a' } }, { organization: { id: 'org_b' } }] });
    const { GET } = await import('@/app/api/admin/firms/membership/route');
    const res = await GET(req('GET'));
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.memberOf).toEqual(['org_a', 'org_b']);
    expect(body.until).toBe('2027-06-27T00:00:00.000Z');
    expect(typeof body.open).toBe('boolean');
  });

  it('refuses a non-Fondok admin', async () => {
    workerScope('own');
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    const res = await POST(req('POST', { orgId: 'org_a', tenantId: TENANT }));
    expect(res.status).toBe(403);
    expect(createOrganizationMembership).not.toHaveBeenCalled();
  });

  it('joins as org:admin and logs it in the firm’s audit log', async () => {
    workerScope('all');
    fetchMock.mockResolvedValueOnce(new Response('{"logged":true}', { status: 201 }));
    createOrganizationMembership.mockResolvedValue({});
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    const res = await POST(req('POST', { orgId: 'org_a', tenantId: TENANT }));
    expect(res.status).toBe(201);
    expect(createOrganizationMembership).toHaveBeenCalledWith({ organizationId: 'org_a', userId: 'user_1', role: 'org:admin' });
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe('https://worker.example/admin/support-access/membership-event');
    expect(JSON.parse(init.body)).toEqual({ tenant_id: TENANT, joined: true, user_id: 'user_1', email: 'prem@fondok.ai' });
  });

  it('undoes the join when the firm’s audit log cannot record it', async () => {
    workerScope('all');
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 503 }));
    createOrganizationMembership.mockResolvedValue({});
    deleteOrganizationMembership.mockResolvedValue({});
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    const res = await POST(req('POST', { orgId: 'org_a', tenantId: TENANT }));
    expect(res.status).toBe(503);
    expect(deleteOrganizationMembership).toHaveBeenCalledWith({ organizationId: 'org_a', userId: 'user_1' });
  });

  it('lifts a member cap once and retries', async () => {
    workerScope('all');
    fetchMock.mockResolvedValueOnce(new Response('{"logged":true}', { status: 201 }));
    createOrganizationMembership
      .mockRejectedValueOnce({ errors: [{ longMessage: 'The maximum number of memberships has been reached.' }] })
      .mockResolvedValueOnce({});
    updateOrganization.mockResolvedValue({});
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    const res = await POST(req('POST', { orgId: 'org_a', tenantId: TENANT }));
    expect(res.status).toBe(201);
    expect(updateOrganization).toHaveBeenCalledWith('org_a', { maxAllowedMemberships: 0 });
    expect(createOrganizationMembership).toHaveBeenCalledTimes(2);
  });

  it('refuses to join once the pre-launch window is closed, but still allows leaving', async () => {
    vi.stubEnv('PRE_LAUNCH_STAFF_MEMBERSHIP', '0');
    workerScope('all');
    const { POST, DELETE } = await import('@/app/api/admin/firms/membership/route');
    const res = await POST(req('POST', { orgId: 'org_a', tenantId: TENANT }));
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({ code: 'pre_launch_closed' });
    expect(createOrganizationMembership).not.toHaveBeenCalled();

    workerScope('all');
    fetchMock.mockResolvedValueOnce(new Response('{"logged":true}', { status: 201 }));
    deleteOrganizationMembership.mockResolvedValue({});
    const left = await DELETE(req('DELETE', { orgId: 'org_a', tenantId: TENANT }));
    expect(left.status).toBe(200);
    expect(await left.json()).toEqual({ left: true, logged: true });
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toMatchObject({ joined: false });
  });

  it('rejects malformed ids before touching Clerk', async () => {
    workerScope('all');
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    const res = await POST(req('POST', { orgId: 'nope', tenantId: 'bad' }));
    expect(res.status).toBe(422);
    expect(createOrganizationMembership).not.toHaveBeenCalled();
  });
});

describe('preLaunchMembershipOpen', () => {
  it('closes by date and by flag', async () => {
    const { preLaunchMembershipOpen, FULL_LAUNCH } = await import('@/lib/server/internalAdmin');
    expect(preLaunchMembershipOpen(new Date('2026-11-07T00:00:00Z'))).toBe(true);
    expect(preLaunchMembershipOpen(new Date(FULL_LAUNCH.getTime() - 1))).toBe(true);
    expect(preLaunchMembershipOpen(FULL_LAUNCH)).toBe(false);
    vi.stubEnv('PRE_LAUNCH_STAFF_MEMBERSHIP', '0');
    expect(preLaunchMembershipOpen(new Date('2026-11-07T00:00:00Z'))).toBe(false);
  });
});
