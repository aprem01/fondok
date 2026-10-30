/**
 * Which web bundle is serving — the body of GET /api/version
 * (src/app/api/version/route.ts). Lives here because a Next route module may
 * export only handlers and route config; Next 14's route type guard rejects
 * any other export at build time.
 *
 * Vercel sets VERCEL_GIT_COMMIT_SHA and VERCEL_ENV on every deployment.
 * Unknown or blank values are null, never a placeholder.
 */
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
