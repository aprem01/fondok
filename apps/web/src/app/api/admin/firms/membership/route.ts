import { clerkClient } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import {
  FULL_LAUNCH,
  clerkErrorMessage,
  preLaunchMembershipOpen,
  requireInternalAdmin,
  type RegistryTenant,
} from '@/lib/server/internalAdmin';

/**
 * Pre-launch standing membership for Fondok staff (Fondok admins only).
 *
 *   GET    /api/admin/firms/membership   — `{ open, until, memberOf: orgId[] }`
 *   POST   /api/admin/firms/membership   — `{ tenantId }` join that firm as org:admin
 *   DELETE /api/admin/firms/membership   — `{ tenantId }` leave it
 *
 * The client names only the firm's tenant id; the Clerk organization comes
 * from the worker's registry (same lookup as the Fondok-admin gate), so the
 * membership and its audit entry can never point at different firms. The
 * worker confirms the caller is Fondok STAFF (two-factor on) before Clerk
 * is touched. Every join/leave is written to the FIRM's own audit log
 * (`POST /admin/support-access/membership-event`); a join that cannot be
 * logged is undone and refused, and a rollback that fails is reported, not
 * hidden. After `FULL_LAUNCH` (or PRE_LAUNCH_STAFF_MEMBERSHIP=0) POST
 * refuses; DELETE keeps working so memberships can always be removed.
 */

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type Gate = Exclude<Awaited<ReturnType<typeof requireInternalAdmin>>, NextResponse>;

async function readFirm(req: Request, gate: Gate): Promise<RegistryTenant | NextResponse> {
  const body = (await req.json().catch(() => null)) as { tenantId?: unknown } | null;
  const tenantId = typeof body?.tenantId === 'string' ? body.tenantId : '';
  if (!UUID_RE.test(tenantId)) return NextResponse.json({ error: 'Unknown firm.' }, { status: 422 });
  const firm = gate.tenants.find((t) => t.id.toLowerCase() === tenantId.toLowerCase());
  if (!firm || firm.kind !== 'external' || !/^org_[A-Za-z0-9]+$/.test(firm.clerk_org_id)) {
    return NextResponse.json({ error: 'Unknown firm.' }, { status: 404 });
  }
  return firm;
}

/** The worker's staff-only listing doubles as a preflight: 403 means "not Fondok staff". */
async function callerIsStaff(gate: Gate): Promise<boolean> {
  const res = await fetch(`${gate.workerUrl}/admin/support-access`, {
    headers: { Authorization: `Bearer ${gate.token}` },
    cache: 'no-store',
  }).catch(() => null);
  return !!res && res.ok;
}

async function logEvent(gate: Gate, tenantId: string, joined: boolean, email: string | null): Promise<boolean> {
  const res = await fetch(`${gate.workerUrl}/admin/support-access/membership-event`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${gate.token}`, 'content-type': 'application/json' },
    body: JSON.stringify({ tenant_id: tenantId, joined, email }),
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

const STAFF_REFUSAL = NextResponse.json(
  { error: 'Joining a firm needs your Fondok staff sign-in with two-factor turned on.', code: 'requires_staff' },
  { status: 403 },
);

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
  const firm = await readFirm(req, gate);
  if (firm instanceof NextResponse) return firm;
  if (!(await callerIsStaff(gate))) return STAFF_REFUSAL;
  const clerk = await clerkClient();
  const orgId = firm.clerk_org_id;

  const join = () =>
    clerk.organizations.createOrganizationMembership({ organizationId: orgId, userId: gate.userId, role: 'org:admin' });
  try {
    try {
      await join();
    } catch (err) {
      // Clerk DEV instances cap organizations at 5 members; lift it once and
      // retry there only. A production cap is a real limit and stays.
      const e = err as { errors?: { code?: string }[] };
      const capHit = e?.errors?.some((x) => x.code === 'organization_membership_quota_exceeded');
      const devInstance = (process.env.CLERK_SECRET_KEY ?? '').startsWith('sk_test_');
      if (!capHit || !devInstance) throw err;
      await clerk.organizations.updateOrganization(orgId, { maxAllowedMemberships: 0 });
      await join();
    }
  } catch (err) {
    const e = err as { errors?: { code?: string }[] };
    if (e?.errors?.some((x) => x.code === 'already_a_member_in_organization')) {
      return NextResponse.json({ joined: true, already: true });
    }
    return NextResponse.json({ error: clerkErrorMessage(err, 'Could not join the firm.') }, { status: 502 });
  }

  const logged = await logEvent(gate, firm.id, true, await callerEmail(clerk, gate.userId));
  if (!logged) {
    const undone = await clerk.organizations
      .deleteOrganizationMembership({ organizationId: orgId, userId: gate.userId })
      .then(() => true)
      .catch(() => false);
    return NextResponse.json(
      {
        error: undone
          ? 'The firm’s activity log could not record this, so you were not added. Try again.'
          : `The firm’s activity log could not record this AND the membership could not be undone. You are an admin in ${firm.name} without a log entry: leave from the Firms page, or remove yourself in Clerk.`,
        code: undone ? 'audit_unavailable' : 'audit_unavailable_rollback_failed',
      },
      { status: 503 },
    );
  }
  return NextResponse.json({ joined: true }, { status: 201 });
}

export async function DELETE(req: Request) {
  const gate = await requireInternalAdmin(req);
  if (gate instanceof NextResponse) return gate;
  const firm = await readFirm(req, gate);
  if (firm instanceof NextResponse) return firm;
  const clerk = await clerkClient();
  try {
    await clerk.organizations.deleteOrganizationMembership({ organizationId: firm.clerk_org_id, userId: gate.userId });
  } catch (err) {
    const e = err as { errors?: { code?: string }[] };
    const notMember = e?.errors?.some((x) => /not_found|not_a_member/i.test(x.code ?? ''));
    if (!notMember) return NextResponse.json({ error: clerkErrorMessage(err, 'Could not leave the firm.') }, { status: 502 });
  }
  const logged = await logEvent(gate, firm.id, false, await callerEmail(clerk, gate.userId));
  return NextResponse.json({ left: true, logged });
}
