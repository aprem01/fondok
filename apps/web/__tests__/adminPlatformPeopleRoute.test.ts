// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const authMock = vi.fn();
const getOrganizationMembershipList = vi.fn();
const getOrganizationInvitationList = vi.fn();
const getUserList = vi.fn();

vi.mock('@clerk/nextjs/server', () => ({
  auth: () => authMock(),
  clerkClient: async () => ({
    organizations: { getOrganizationMembershipList, getOrganizationInvitationList },
    users: { getUserList },
  }),
}));

const JSON_SAME_ORIGIN = { origin: 'https://app.example', 'content-type': 'application/json' };
const req = (body: unknown, headers: Record<string, string> = JSON_SAME_ORIGIN) =>
  new Request('https://app.example/api/admin/platform/people', { method: 'POST', body: JSON.stringify(body), headers });

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubEnv('NEXT_PUBLIC_WORKER_URL', 'https://worker.example');
  vi.stubGlobal('fetch', fetchMock);
  for (const m of [authMock, fetchMock, getOrganizationMembershipList, getOrganizationInvitationList, getUserList]) m.mockReset();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

const signedIn = () => authMock.mockResolvedValue({ userId: 'user_1', getToken: async () => 'tok' });
const workerScope = (scope: string) =>
  fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ scope, tenants: [] }), { status: 200 }));

describe('POST /api/admin/platform/people', () => {
  it('refuses a signed-out caller and a cross-site request', async () => {
    const { POST } = await import('@/app/api/admin/platform/people/route');
    authMock.mockResolvedValue({ userId: null, getToken: async () => null });
    expect((await POST(req({ orgIds: ['org_a'] }))).status).toBe(401);
    signedIn();
    expect((await POST(req({ orgIds: ['org_a'] }, { origin: 'https://evil.example', 'content-type': 'application/json' }))).status).toBe(403);
    expect(getOrganizationMembershipList).not.toHaveBeenCalled();
  });

  it('refuses a firm admin who is not a Fondok admin', async () => {
    signedIn();
    workerScope('own');
    const { POST } = await import('@/app/api/admin/platform/people/route');
    expect((await POST(req({ orgIds: ['org_a'] }))).status).toBe(403);
    expect(getOrganizationMembershipList).not.toHaveBeenCalled();
  });

  it('returns each firm\'s team with sign-in times, ignoring malformed org ids', async () => {
    signedIn();
    workerScope('all');
    getOrganizationMembershipList.mockResolvedValue({
      data: [
        {
          role: 'org:admin',
          createdAt: 1_700_000_000_000,
          publicUserData: { userId: 'user_a', firstName: 'Ann', lastName: 'Lee', identifier: 'ann@firm.com' },
        },
        { role: 'org:member', createdAt: 1_700_000_000_000, publicUserData: { userId: 'user_b', identifier: 'bo@firm.com' } },
      ],
    });
    getOrganizationInvitationList.mockResolvedValue({ data: [], totalCount: 2 });
    getUserList.mockResolvedValue({
      data: [
        { id: 'user_a', lastSignInAt: 1_760_000_000_000, lastActiveAt: 1_760_000_100_000 },
        { id: 'user_b', lastSignInAt: null, lastActiveAt: null },
      ],
    });
    const { POST } = await import('@/app/api/admin/platform/people/route');
    const res = await POST(req({ orgIds: ['org_abc', 'not-an-org', 42] }));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { orgs: Record<string, { members: { userId: string; name: string | null; lastSignInAt: string | null }[]; pendingInvitations: number }> };
    expect(Object.keys(body.orgs)).toEqual(['org_abc']);
    expect(getOrganizationMembershipList).toHaveBeenCalledTimes(1);
    const firm = body.orgs.org_abc;
    expect(firm.pendingInvitations).toBe(2);
    expect(firm.members[0]).toMatchObject({ userId: 'user_a', name: 'Ann Lee', lastSignInAt: new Date(1_760_000_000_000).toISOString() });
    expect(firm.members[1]).toMatchObject({ userId: 'user_b', name: null, lastSignInAt: null });
  });

  it('reports a firm whose team cannot be read instead of failing the page', async () => {
    signedIn();
    workerScope('all');
    getOrganizationMembershipList.mockRejectedValue(new Error('clerk down'));
    getOrganizationInvitationList.mockResolvedValue({ data: [], totalCount: 0 });
    const { POST } = await import('@/app/api/admin/platform/people/route');
    const res = await POST(req({ orgIds: ['org_abc'] }));
    expect(res.status).toBe(200);
    const body = (await res.json()) as { orgs: Record<string, { error?: string; members: unknown[] }> };
    expect(body.orgs.org_abc.error).toBeTruthy();
    expect(body.orgs.org_abc.members).toEqual([]);
  });
});
