import { clerkClient } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { EMAIL_RE, clerkErrorMessage, requireInternalAdmin } from '@/lib/server/internalAdmin';

/** POST /api/admin/firms/invite — invite an admin into a firm (Fondok admins only). */
export async function POST(req: Request) {
  const gate = await requireInternalAdmin(req);
  if (gate instanceof NextResponse) return gate;

  const body = (await req.json().catch(() => null)) as { orgId?: unknown; email?: unknown } | null;
  const orgId = typeof body?.orgId === 'string' ? body.orgId : '';
  const email = typeof body?.email === 'string' ? body.email.trim().toLowerCase() : '';
  if (!/^org_[A-Za-z0-9]+$/.test(orgId)) return NextResponse.json({ error: 'Unknown firm.' }, { status: 422 });
  if (!EMAIL_RE.test(email)) return NextResponse.json({ error: 'Enter a valid email.' }, { status: 422 });

  try {
    const clerk = await clerkClient();
    await clerk.organizations.createOrganizationInvitation({
      organizationId: orgId,
      emailAddress: email,
      role: 'org:admin',
      inviterUserId: gate.userId,
      redirectUrl: `${new URL(req.url).origin}/sign-up`,
    });
  } catch (err) {
    return NextResponse.json({ error: clerkErrorMessage(err, 'The invitation could not be sent.') }, { status: 502 });
  }
  return NextResponse.json({ invited: email }, { status: 201 });
}
