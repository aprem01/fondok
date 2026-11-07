import { clerkClient } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { requireInternalAdmin } from '@/lib/server/internalAdmin';

/**
 * POST /api/admin/platform/people — who is in each firm and when they last
 * signed in (Fondok admins only). The worker knows activity by Clerk user
 * id; Clerk knows names, emails, roles, sign-ins and pending invitations.
 *
 * Body: `{ orgIds: string[] }` (Clerk org ids from `GET /admin/platform`).
 * POST, not GET, so the same-origin JSON gate in requireInternalAdmin
 * applies (browsers omit Origin on same-origin GETs).
 */
export interface PlatformPerson {
  userId: string;
  name: string | null;
  email: string | null;
  role: string;
  joinedAt: string | null;
  lastSignInAt: string | null;
  lastActiveAt: string | null;
}
export interface PlatformOrgPeople {
  members: PlatformPerson[];
  pendingInvitations: number;
  error?: string;
}

const MAX_ORGS = 50;
const iso = (ms: number | null | undefined) => (typeof ms === 'number' && ms > 0 ? new Date(ms).toISOString() : null);

export async function POST(req: Request) {
  const gate = await requireInternalAdmin(req);
  if (gate instanceof NextResponse) return gate;

  const body = (await req.json().catch(() => null)) as { orgIds?: unknown } | null;
  const orgIds = Array.isArray(body?.orgIds)
    ? [...new Set(body.orgIds.filter((o): o is string => typeof o === 'string' && /^org_[A-Za-z0-9]+$/.test(o)))].slice(
        0,
        MAX_ORGS,
      )
    : [];

  const clerk = await clerkClient();
  const out: Record<string, PlatformOrgPeople> = {};

  await Promise.all(
    orgIds.map(async (orgId) => {
      try {
        const [memberships, invitations] = await Promise.all([
          clerk.organizations.getOrganizationMembershipList({ organizationId: orgId, limit: 100 }),
          clerk.organizations
            .getOrganizationInvitationList({ organizationId: orgId, status: ['pending'], limit: 1 })
            .catch(() => null),
        ]);
        const userIds = memberships.data
          .map((m) => m.publicUserData?.userId)
          .filter((u): u is string => typeof u === 'string');
        const users = userIds.length ? (await clerk.users.getUserList({ userId: userIds, limit: 100 })).data : [];
        const byId = new Map(users.map((u) => [u.id, u]));
        out[orgId] = {
          pendingInvitations: invitations?.totalCount ?? 0,
          members: memberships.data.map((m) => {
            const pud = m.publicUserData;
            const u = pud?.userId ? byId.get(pud.userId) : undefined;
            const name = [pud?.firstName, pud?.lastName].filter(Boolean).join(' ').trim();
            return {
              userId: pud?.userId ?? '',
              name: name || null,
              email: pud?.identifier ?? u?.primaryEmailAddress?.emailAddress ?? null,
              role: m.role,
              joinedAt: iso(m.createdAt),
              lastSignInAt: iso(u?.lastSignInAt),
              lastActiveAt: iso(u?.lastActiveAt),
            };
          }),
        };
      } catch {
        out[orgId] = { members: [], pendingInvitations: 0, error: 'Team list unavailable' };
      }
    }),
  );

  return NextResponse.json({ orgs: out });
}
