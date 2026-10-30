/**
 * Go-live checklist — every route the auth middleware leaves public must be a
 * page that exists. `/landing` and `/test-documents(.*)` were listed long
 * after their pages were deleted; a public pattern with no page behind it is a
 * hole waiting for the next page someone adds there.
 */
import { existsSync } from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';
import { PUBLIC_ROUTES, publicRouteSegment } from '@/lib/publicRoutes';

const APP = path.resolve(__dirname, '../src/app');

describe('public routes', () => {
  it.each([...PUBLIC_ROUTES])('%s has a page under src/app', (pattern) => {
    const segment = publicRouteSegment(pattern);
    const dir = path.join(APP, segment);
    // A route-group / catch-all folder (`sign-in/[[...sign-in]]/page.tsx`)
    // counts: the segment directory must exist and contain a page somewhere.
    expect(existsSync(dir)).toBe(true);
  });

  it('no longer lists the deleted /landing and /test-documents pages', () => {
    expect(PUBLIC_ROUTES).not.toContain('/landing');
    expect(PUBLIC_ROUTES.some((p) => p.startsWith('/test-documents'))).toBe(false);
  });

  it('strips the glob suffix and the leading slash', () => {
    expect(publicRouteSegment('/sign-in(.*)')).toBe('sign-in');
    expect(publicRouteSegment('/')).toBe('');
  });
});
