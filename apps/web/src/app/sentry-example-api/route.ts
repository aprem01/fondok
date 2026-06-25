import { NextResponse } from 'next/server';

/**
 * Companion route for /sentry-example-page. Throws on every request
 * so the server-side Sentry init has something to capture.
 */
export async function GET() {
  throw new Error('Sentry test: server-side error from /sentry-example-api');
  // Unreachable — satisfies the return-type checker without an as-cast.
  return NextResponse.json({ ok: false });
}
