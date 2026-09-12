import type { FullConfig } from '@playwright/test';

/**
 * Warm every route the suite navigates to, once, before any test runs.
 *
 * Why this exists: `next dev` compiles a route the first time it is
 * requested. The first `page.goto('/projects/7')` on a cold server therefore
 * pays a 20-60s webpack compile, and the test that happened to be first paid
 * it as a timeout. That produced failures that looked like product bugs,
 * moved from spec to spec between runs, and taught everyone to re-run rather
 * than read — the exact habit that let a permanently red check survive 200
 * pushes.
 *
 * Warming is a plain GET of the server-rendered HTML per route: no browser,
 * no assertions, nothing that can pass or fail a test. Failures are logged
 * and ignored, because a route that cannot be fetched here is a real failure
 * that belongs to the spec that tests it, not to this hook.
 */

/** Routes the specs navigate to, per base URL. */
const ROUTES = [
  '/',
  '/projects',
  '/projects/new',
  '/projects/7',
  '/projects/e577f547-a3cd-4e78-9ee1-8d761b0c4777',
  '/methodology',
];

async function warm(baseURL: string): Promise<void> {
  for (const route of ROUTES) {
    const url = `${baseURL.replace(/\/$/, '')}${route}`;
    try {
      // 120s: a cold compile of the deal page pulls a lot of chunks.
      await fetch(url, { signal: AbortSignal.timeout(120_000) });
    } catch (err) {
      // Not fatal — the spec that needs this route will report it properly.
      // eslint-disable-next-line no-console
      console.warn(`[e2e warm-up] ${url}: ${(err as Error).message}`);
    }
  }
}

export default async function globalSetup(config: FullConfig): Promise<void> {
  // Every distinct baseURL across the configured projects, warmed in
  // parallel — the two dev servers compile independently.
  const baseURLs = Array.from(
    new Set(
      config.projects
        .map((p) => p.use?.baseURL)
        .filter((u): u is string => typeof u === 'string' && u.length > 0),
    ),
  );
  await Promise.all(baseURLs.map(warm));
}
