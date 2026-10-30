/**
 * The routes Clerk leaves unauthenticated (`src/middleware.ts`). Kept here,
 * away from the Clerk import, so a test can check every entry against the
 * pages that actually exist under `src/app` — a public route whose page is
 * gone is a hole in the auth wall waiting for a page to be added behind it.
 *
 * Patterns are `createRouteMatcher` globs: `(.*)` = "and everything under".
 */
export const PUBLIC_ROUTES: readonly string[] = [
  '/',
  '/sign-in(.*)',
  '/sign-up(.*)',
  '/diag',
];

/** `/sign-in(.*)` → `sign-in`; `/` → `` (the root page). */
export function publicRouteSegment(pattern: string): string {
  return pattern.replace(/\(\.\*\)$/, '').replace(/^\//, '');
}
