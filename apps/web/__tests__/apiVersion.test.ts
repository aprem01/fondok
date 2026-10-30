// @vitest-environment node
/**
 * GET /api/version — the web half of deploy verification (2026-10-30).
 * The post-deploy smoke polls it until `sha` equals the pushed commit; it
 * must report exactly what Vercel set and null for anything unknown.
 */
import { describe, it, expect, afterEach } from 'vitest';

import { GET, versionBody } from '@/app/api/version/route';

const saved = { sha: process.env.VERCEL_GIT_COMMIT_SHA, env: process.env.VERCEL_ENV };

afterEach(() => {
  if (saved.sha === undefined) delete process.env.VERCEL_GIT_COMMIT_SHA;
  else process.env.VERCEL_GIT_COMMIT_SHA = saved.sha;
  if (saved.env === undefined) delete process.env.VERCEL_ENV;
  else process.env.VERCEL_ENV = saved.env;
});

describe('GET /api/version', () => {
  it('reports VERCEL_GIT_COMMIT_SHA and VERCEL_ENV', async () => {
    process.env.VERCEL_GIT_COMMIT_SHA = 'a'.repeat(40);
    process.env.VERCEL_ENV = 'production';
    const res = GET();
    expect(res.status).toBe(200);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(await res.json()).toEqual({ sha: 'a'.repeat(40), env: 'production' });
  });

  it('is null, not a placeholder, when Vercel did not set them (local dev)', async () => {
    delete process.env.VERCEL_GIT_COMMIT_SHA;
    delete process.env.VERCEL_ENV;
    expect(await GET().json()).toEqual({ sha: null, env: null });
  });

  it('treats blank values as unknown and trims whitespace', () => {
    expect(versionBody({ VERCEL_GIT_COMMIT_SHA: '  ', VERCEL_ENV: '' })).toEqual({ sha: null, env: null });
    expect(versionBody({ VERCEL_GIT_COMMIT_SHA: 'abc\n', VERCEL_ENV: 'preview ' })).toEqual({
      sha: 'abc',
      env: 'preview',
    });
  });
});
