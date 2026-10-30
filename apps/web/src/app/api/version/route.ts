/**
 * GET /api/version — which web bundle is serving.
 *
 * Deploy verification (go-live audit D.2, 2026-10-30). Vercel sets
 * VERCEL_GIT_COMMIT_SHA and VERCEL_ENV on every deployment; the post-deploy
 * smoke's `worker-deployed` job polls this until `sha` equals the pushed
 * commit, and /diag prints it. Public and non-sensitive — a commit SHA and
 * "production" / "preview" say nothing an attacker can use. Unknown values
 * are null, never a placeholder. Listed as a public route in middleware.ts
 * so the poll needs no session.
 */
import { NextResponse } from 'next/server';

// Read the environment at request time, never at build time.
export const dynamic = 'force-dynamic';

export interface VersionBody {
  sha: string | null;
  env: string | null;
}

function clean(value: string | undefined): string | null {
  const v = (value ?? '').trim();
  return v ? v : null;
}

export function versionBody(env: Record<string, string | undefined> = process.env): VersionBody {
  return {
    sha: clean(env.VERCEL_GIT_COMMIT_SHA),
    env: clean(env.VERCEL_ENV),
  };
}

export function GET() {
  return NextResponse.json(versionBody(), {
    headers: { 'Cache-Control': 'no-store' },
  });
}
