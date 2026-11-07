import { clerkClient } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import {
  FULL_LAUNCH,
  clerkErrorMessage,
  preLaunchMembershipOpen,
  requireInternalAdmin,
} from '@/lib/server/internalAdmin';

/**
 * Pre-launch standing membership for Fondok staff (Fondok admins only).
 *
 *   GET    /api/admin/firms/membership            — `{ open, until, memberOf: orgId[] }`
 *   POST   /api/admin/firms/membership            — `{ orgId, tenantId }` join as org:admin
 *   DELETE /api/admin/firms/membership            — `{ orgId, tenantId }` leave
 *
 * Every join/leave is written to the FIRM's own audit log through the worker
 * (`POST /admin/support-access/membership-event`). A join that cannot be
 * logged is undone and refused: no standing access the firm cannot see.
 * After `FULL_LAUNCH` (or with PRE_LAUNCH_STAFF_MEMBERSHIP=0) POST refuses;
 * DELETE keeps working so memberships can always be removed.
 */

const ORG_RE = /^org_[A-Za-z0-9]+$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Gate = Exclude<Awaited<ReturnType<typeof requireInternalAdmin>>, NextResponse>;

async function readIds(req: Request): Promise<{ orgId: string; tenantId: string } | NextResponse> {
  const body = (await req.json().catch(() => null)) as { orgId?: unknown; tenantId?: unknown } | null;
  const orgId = typeof body?.orgId === 'string' ? body.orgId : '';
  const tenantId = typeof body?.tenantId === 'string' ? body.tenantId : '';
  if (!ORG_RE.test(orgId) || !UUID_RE.test(tenantId)) {
    return NextResponse.json({ error: 'Unknown firm.' }, { status: 422 });
  }
  return { orgId, tenantId };
}

async function logEvent(gate: Gate, tenantId: string, joined: boolean, email: string | null): Promise<boolean> {
  const res = await fetch(`${gate.workerUrl}/admin/support-access/membership-event`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${gate.token}`, 'content-type': 'application/json' },
    body: JSON.stringify({ tenant_id: tenantId, joined, user_id: gate.userId, email }),
    cache: 'no-store',
  }).catch(() => null);
  return !!res && res.ok;
}

async function callerEmail(clerk: Awaited<ReturnType<typeof clerkClient>>, userId: string): Promise<string | null> {
  try {
    const u = await clerk.users.getUser(userId);
    return u.emailAddresses.find((e) => e.id === u.primaryEmailAddressId)?.emailAddress ?? null;
  } catch {
    return null;
  }
}

export async function GET(req: Request) {
  const gate = await requireInternalAdmin(req);
  if (gate instanceof NextResponse) return gate;
  const clerk = await clerkClient();
  let memberOf: string[] = [];
  try {
    const list = await clerk.users.getOrganizationMembershipList({ userId: gate.userId, limit: 100 });
    memberOf = list.data.map((m) => m.organization.id);
  } catch (err) {
    return NextResponse.json({ error: clerkErrorMessage(err, 'Could not read your memberships.') }, { status: 502 });
  }
  return NextResponse.json({ open: preLaunchMembershipOpen(), until: FULL_LAUNCH.toISOString(), memberOf });
}

export async function POST(req: Request) {
  const gate = await requireInternalAdmin(req);
  if (gate instanceof NextResponse) return gate;
  if (!preLaunchMembershipOpen()) {
    return NextResponse.json(
      { error: 'Standing membership closed at launch. Use support access from Platform.', code: 'pre_launch_closed' },
      { status: 403 },
    );
  }
  const ids = await readIds(req);
  if (ids instanceof NextResponse) return ids;
  const clerk = await clerkClient();

  const join = () =>
    clerk.organizations.createOrganizationMembership({ organizationId: ids.orgId, userId: gate.userId, role: 'org:admin' });
  try {
    try {
      await join();
    } catch (err) {
      // Dev-instance orgs default to a 5-member cap; lift it once and retry.
      if (!/maximum|limit|exceed/i.test(clerkErrorMessage(err, ''))) throw err;
      await clerk.organizations.updateOrganization(ids.orgId, { maxAllowedMemberships: 0 });
      await join();
    }
  } catch (err) {
    const msg = clerkErrorMessage(err, 'Could not join the firm.');
    if (/already/i.test(msg)) return NextResponse.json({ joined: true, already: true });
    return NextResponse.json({ error: msg }, { status: 502 });
  }

  const logged = await logEvent(gate, ids.tenantId, true, await callerEmail(clerk, gate.userId));
  if (!logged) {
    await clerk.organizations
      .deleteOrganizationMembership({ organizationId: ids.orgId, userId: gate.userId })
      .catch(() => undefined);
    return NextResponse.json(
      { error: 'The firm’s activity log could not record this, so you were not added. Try again.' },
      { status: 503 },
    );
  }
  return NextResponse.json({ joined: true }, { status: 201 });
}

export async function DELETE(req: Request) {
  const gate = await requireInternalAdmin(req);
  if (gate instanceof NextResponse) return gate;
  const ids = await readIds(req);
  if (ids instanceof NextResponse) return ids;
  const clerk = await clerkClient();
  try {
    await clerk.organizations.deleteOrganizationMembership({ organizationId: ids.orgId, userId: gate.userId });
  } catch (err) {
    const msg = clerkErrorMessage(err, 'Could not leave the firm.');
    if (!/not found|not a member/i.test(msg)) return NextResponse.json({ error: msg }, { status: 502 });
  }
  const logged = await logEvent(gate, ids.tenantId, false, await callerEmail(clerk, gate.userId));
  return NextResponse.json({ left: true, logged });
}
