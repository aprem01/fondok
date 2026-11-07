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
const FIRM = { id: TENANT, clerk_org_id: 'org_a', name: 'Acme Hotels', kind: 'external', status: 'active' };
const INTERNAL = { id: '99999999-2222-4333-8444-555555555555', clerk_org_id: 'org_f', name: 'Fondok', kind: 'internal', status: 'active' };
const workerScope = (scope: string) =>
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ scope, tenants: [FIRM, INTERNAL] }), { status: 200 }));
const staffOk = () => fetchMock.mockResolvedValueOnce(new Response('{"grants":[]}', { status: 200 }));
const staffNo = () => fetchMock.mockResolvedValueOnce(new Response('{"detail":"support_access_requires_staff"}', { status: 403 }));
const logged = () => fetchMock.mockResolvedValueOnce(new Response('{"logged":true}', { status: 201 }));
const capError = { errors: [{ code: 'organization_membership_quota_exceeded', longMessage: 'quota' }] };

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
    const res = await POST(req('POST', { tenantId: TENANT }));
    expect(res.status).toBe(403);
    expect(createOrganizationMembership).not.toHaveBeenCalled();
  });

  it('refuses a Fondok admin who is not staff (no 2FA) before touching Clerk', async () => {
    workerScope('all');
    staffNo();
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    const res = await POST(req('POST', { tenantId: TENANT }));
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({ code: 'requires_staff' });
    expect(createOrganizationMembership).not.toHaveBeenCalled();
  });

  it('joins the org from the server-side registry and logs it in the firm’s audit log', async () => {
    workerScope('all');
    staffOk();
    logged();
    createOrganizationMembership.mockResolvedValue({});
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    const res = await POST(req('POST', { tenantId: TENANT, orgId: 'org_evil' }));
    expect(res.status).toBe(201);
    expect(createOrganizationMembership).toHaveBeenCalledWith({ organizationId: 'org_a', userId: 'user_1', role: 'org:admin' });
    const [url, init] = fetchMock.mock.calls[2];
    expect(url).toBe('https://worker.example/admin/support-access/membership-event');
    expect(JSON.parse(init.body)).toEqual({ tenant_id: TENANT, joined: true, email: 'prem@fondok.ai' });
  });

  it('never joins an internal or unregistered tenant', async () => {
    workerScope('all');
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    expect((await POST(req('POST', { tenantId: INTERNAL.id }))).status).toBe(404);
    workerScope('all');
    expect((await POST(req('POST', { tenantId: '00000000-0000-4000-8000-000000000000' }))).status).toBe(404);
    expect(createOrganizationMembership).not.toHaveBeenCalled();
  });

  it('undoes the join when the firm’s audit log cannot record it, and says so if the undo fails', async () => {
    workerScope('all');
    staffOk();
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 503 }));
    createOrganizationMembership.mockResolvedValue({});
    deleteOrganizationMembership.mockResolvedValueOnce({});
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    let res = await POST(req('POST', { tenantId: TENANT }));
    expect(res.status).toBe(503);
    expect(await res.json()).toMatchObject({ code: 'audit_unavailable' });
    expect(deleteOrganizationMembership).toHaveBeenCalledWith({ organizationId: 'org_a', userId: 'user_1' });

    workerScope('all');
    staffOk();
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 503 }));
    deleteOrganizationMembership.mockRejectedValueOnce(new Error('clerk down'));
    res = await POST(req('POST', { tenantId: TENANT }));
    expect(res.status).toBe(503);
    expect(await res.json()).toMatchObject({ code: 'audit_unavailable_rollback_failed' });
  });

  it('lifts the member cap only on a dev Clerk instance', async () => {
    vi.stubEnv('CLERK_SECRET_KEY', 'sk_test_abc');
    workerScope('all');
    staffOk();
    logged();
    createOrganizationMembership.mockRejectedValueOnce(capError).mockResolvedValueOnce({});
    updateOrganization.mockResolvedValue({});
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    let res = await POST(req('POST', { tenantId: TENANT }));
    expect(res.status).toBe(201);
    expect(updateOrganization).toHaveBeenCalledWith('org_a', { maxAllowedMemberships: 0 });

    vi.stubEnv('CLERK_SECRET_KEY', 'sk_live_abc');
    updateOrganization.mockClear();
    workerScope('all');
    staffOk();
    createOrganizationMembership.mockRejectedValueOnce(capError);
    res = await POST(req('POST', { tenantId: TENANT }));
    expect(res.status).toBe(502);
    expect(updateOrganization).not.toHaveBeenCalled();
  });

  it('refuses to join once the pre-launch window is closed, but still allows leaving', async () => {
    vi.stubEnv('PRE_LAUNCH_STAFF_MEMBERSHIP', '0');
    workerScope('all');
    const { POST, DELETE } = await import('@/app/api/admin/firms/membership/route');
    const res = await POST(req('POST', { tenantId: TENANT }));
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({ code: 'pre_launch_closed' });
    expect(createOrganizationMembership).not.toHaveBeenCalled();

    workerScope('all');
    logged();
    deleteOrganizationMembership.mockResolvedValue({});
    const left = await DELETE(req('DELETE', { tenantId: TENANT }));
    expect(left.status).toBe(200);
    expect(await left.json()).toEqual({ left: true, logged: true });
    expect(deleteOrganizationMembership).toHaveBeenCalledWith({ organizationId: 'org_a', userId: 'user_1' });
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toMatchObject({ joined: false });
  });

  it('rejects malformed ids before touching Clerk', async () => {
    workerScope('all');
    const { POST } = await import('@/app/api/admin/firms/membership/route');
    const res = await POST(req('POST', { tenantId: 'bad' }));
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
