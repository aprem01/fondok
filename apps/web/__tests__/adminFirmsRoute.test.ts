// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const authMock = vi.fn();
const createOrganization = vi.fn();
const createOrganizationInvitation = vi.fn();

vi.mock('@clerk/nextjs/server', () => ({
  auth: () => authMock(),
  clerkClient: async () => ({ organizations: { createOrganization, createOrganizationInvitation } }),
}));

const req = (body: unknown) =>
  new Request('https://app.example/api/admin/firms', { method: 'POST', body: JSON.stringify(body) });

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubEnv('NEXT_PUBLIC_WORKER_URL', 'https://worker.example');
  vi.stubGlobal('fetch', fetchMock);
  authMock.mockReset();
  fetchMock.mockReset();
  createOrganization.mockReset();
  createOrganizationInvitation.mockReset();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const signedIn = () => authMock.mockResolvedValue({ userId: 'user_1', getToken: async () => 'tok' });
const workerScope = (scope: string) =>
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ scope, tenants: [] }), { status: 200 }));

describe('POST /api/admin/firms', () => {
  it('refuses a signed-out caller', async () => {
    authMock.mockResolvedValue({ userId: null, getToken: async () => null });
    const { POST } = await import('@/app/api/admin/firms/route');
    const res = await POST(req({ name: 'Acme', adminEmail: 'a@acme.com' }));
    expect(res.status).toBe(401);
    expect(createOrganization).not.toHaveBeenCalled();
  });

  it('refuses a firm admin who is not a Fondok admin', async () => {
    signedIn();
    workerScope('own');
    const { POST } = await import('@/app/api/admin/firms/route');
    const res = await POST(req({ name: 'Acme', adminEmail: 'a@acme.com' }));
    expect(res.status).toBe(403);
    expect(createOrganization).not.toHaveBeenCalled();
  });

  it('creates, registers and invites for a Fondok admin', async () => {
    signedIn();
    workerScope('all');
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 201 }));
    createOrganization.mockResolvedValue({ id: 'org_abc' });
    createOrganizationInvitation.mockResolvedValue({});
    const { POST } = await import('@/app/api/admin/firms/route');
    const res = await POST(req({ name: ' Acme Hotels ', adminEmail: 'Ann@Acme.com' }));
    expect(res.status).toBe(201);
    expect(createOrganization).toHaveBeenCalledWith({ name: 'Acme Hotels', createdBy: 'user_1' });
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe('https://worker.example/admin/tenants');
    expect(JSON.parse(init.body)).toEqual({ clerk_org_id: 'org_abc', name: 'Acme Hotels', kind: 'external', status: 'active' });
    expect(createOrganizationInvitation).toHaveBeenCalledWith(
      expect.objectContaining({ organizationId: 'org_abc', emailAddress: 'ann@acme.com', role: 'org:admin' }),
    );
  });

  it('says how far it got when registration fails', async () => {
    signedIn();
    workerScope('all');
    fetchMock.mockResolvedValueOnce(new Response('{}', { status: 500 }));
    createOrganization.mockResolvedValue({ id: 'org_abc' });
    const { POST } = await import('@/app/api/admin/firms/route');
    const res = await POST(req({ name: 'Acme', adminEmail: 'a@acme.com' }));
    expect(res.status).toBe(502);
    expect(await res.json()).toMatchObject({ step: 'register', orgId: 'org_abc' });
    expect(createOrganizationInvitation).not.toHaveBeenCalled();
  });

  it('rejects a bad email before touching Clerk', async () => {
    signedIn();
    workerScope('all');
    const { POST } = await import('@/app/api/admin/firms/route');
    const res = await POST(req({ name: 'Acme', adminEmail: 'nope' }));
    expect(res.status).toBe(422);
    expect(createOrganization).not.toHaveBeenCalled();
  });
});

describe('POST /api/admin/firms/invite', () => {
  it('refuses a non-Fondok admin', async () => {
    signedIn();
    workerScope('own');
    const { POST } = await import('@/app/api/admin/firms/invite/route');
    const res = await POST(
      new Request('https://app.example/api/admin/firms/invite', {
        method: 'POST',
        body: JSON.stringify({ orgId: 'org_abc', email: 'a@acme.com' }),
      }),
    );
    expect(res.status).toBe(403);
    expect(createOrganizationInvitation).not.toHaveBeenCalled();
  });
});
