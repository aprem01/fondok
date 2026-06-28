import { NextResponse } from 'next/server';

/**
 * Companion route for /sentry-example-page. Throws on every request
 * so the server-side Sentry init has something to capture.
 *
 * ``force-dynamic`` is mandatory here. Without it, Next.js attempts to
 * prerender the route during ``next build`` (App Router defaults route
 * handlers to static when feasible), which executes the throw at build
 * time and kills the Vercel build. The route only makes sense as a
 * runtime call; dynamic eviction is the cheapest fix.
 */
export const dynamic = 'force-dynamic';

export async function GET() {
  throw new Error('Sentry test: server-side error from /sentry-example-api');
  // Unreachable — satisfies the return-type checker without an as-cast.
  return NextResponse.json({ ok: false });
}
