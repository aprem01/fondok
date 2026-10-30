/**
 * GET /api/version — which web bundle is serving.
 *
 * Deploy verification (go-live audit D.2, 2026-10-30). Vercel sets
 * VERCEL_GIT_COMMIT_SHA and VERCEL_ENV on every deployment; the post-deploy
 * smoke's `worker-deployed` job polls this until `sha` equals the pushed
 * commit, and /diag prints it. Public and non-sensitive — a commit SHA and
 * "production" / "preview" say nothing an attacker can use. Listed as a
 * public route in middleware.ts so the poll needs no session. The body
 * builder lives in src/lib/version.ts: a route module may export only
 * handlers and route config.
 */
import { NextResponse } from 'next/server';

import { versionBody } from '@/lib/version';

// Read the environment at request time, never at build time.
export const dynamic = 'force-dynamic';

export function GET() {
  return NextResponse.json(versionBody(), {
    headers: { 'Cache-Control': 'no-store' },
  });
}
