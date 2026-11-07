import { clerkClient } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { EMAIL_RE, clerkErrorMessage, requireInternalAdmin } from '@/lib/server/internalAdmin';

/**
 * POST /api/admin/firms — set up a new firm (Fondok admins only).
 *
 *   1. Create the firm's Clerk organization. The Fondok admin doing this is
 *      its creator, so they are a member (visible in the firm's team list)
 *      and can open the firm's workspace to support it.
 *   2. Register it with the worker as an active external tenant.
 *   3. Invite the firm's first admin by email (org:admin).
 *
 * Steps run in order and the response says how far it got, so a failure
 * after step 1 can be finished from the Firms page (approve / invite)
 * instead of leaving a half-made firm nobody knows about.
 */
export async function POST(req: Request) {
  const gate = await requireInternalAdmin();
  if (gate instanceof NextResponse) return gate;

  const body = (await req.json().catch(() => null)) as { name?: unknown; adminEmail?: unknown } | null;
  const name = typeof body?.name === 'string' ? body.name.trim() : '';
  const adminEmail = typeof body?.adminEmail === 'string' ? body.adminEmail.trim().toLowerCase() : '';
  if (name.length < 2 || name.length > 200) {
    return NextResponse.json({ error: 'Enter the firm’s name.' }, { status: 422 });
  }
  if (!EMAIL_RE.test(adminEmail)) {
    return NextResponse.json({ error: 'Enter a valid email for the firm’s first admin.' }, { status: 422 });
  }

  const clerk = await clerkClient();

  // 1. Organization
  let orgId: string;
  try {
    const org = await clerk.organizations.createOrganization({ name, createdBy: gate.userId });
    orgId = org.id;
  } catch (err) {
    return NextResponse.json(
      { error: clerkErrorMessage(err, 'Could not create the firm in Clerk.'), step: 'create' },
      { status: 502 },
    );
  }

  // 2. Registry
  const reg = await fetch(`${gate.workerUrl}/admin/tenants`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${gate.token}`, 'content-type': 'application/json' },
    body: JSON.stringify({ clerk_org_id: orgId, name, kind: 'external', status: 'active' }),
    cache: 'no-store',
  }).catch(() => null);
  if (!reg || !reg.ok) {
    return NextResponse.json(
      {
        error: 'The firm was created but could not be approved. Approve it from the list.',
        step: 'register',
        orgId,
      },
      { status: 502 },
    );
  }

  // 3. First admin
  try {
    await clerk.organizations.createOrganizationInvitation({
      organizationId: orgId,
      emailAddress: adminEmail,
      role: 'org:admin',
      inviterUserId: gate.userId,
      redirectUrl: `${new URL(req.url).origin}/sign-up`,
    });
  } catch (err) {
    return NextResponse.json(
      {
        error: `The firm is set up, but the invitation failed: ${clerkErrorMessage(err, 'unknown error')}. Send it again from the list.`,
        step: 'invite',
        orgId,
      },
      { status: 502 },
    );
  }

  return NextResponse.json({ orgId, invited: adminEmail }, { status: 201 });
}
