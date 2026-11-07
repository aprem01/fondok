/**
 * Auth middleware — feature-flagged by `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`.
 *
 * When Clerk is configured, we wrap the app with `clerkMiddleware` and
 * protect every route except the public surfaces listed in
 * `lib/publicRoutes` (`/`, `/sign-in/*`, `/sign-up/*`, `/diag`). When unset
 * (demo mode), we export a no-op pass-through so the app boots without any
 * auth backend.
 */
import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server';
import { NextResponse, type NextRequest } from 'next/server';
import { PUBLIC_ROUTES } from '@/lib/publicRoutes';

const clerkKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const isClerkConfigured =
  !!clerkKey &&
  (clerkKey.startsWith('pk_test_') || clerkKey.startsWith('pk_live_')) &&
  !clerkKey.endsWith('_dummy');

const isPublicRoute = createRouteMatcher([...PUBLIC_ROUTES]);

// Fail closed in production: if the Clerk key is ever missing or wrong on
// the production deploy, nothing but the public pages is served. Demo mode
// (no auth) is for local development only.
const isProductionDeploy = process.env.VERCEL_ENV === 'production';

const lockedOut = (request: NextRequest) => {
  if (isPublicRoute(request)) return NextResponse.next();
  return new NextResponse('Sign-in is unavailable. Please try again shortly.', {
    status: 503,
    headers: { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' },
  });
};

const middleware = isClerkConfigured
  ? clerkMiddleware(async (auth, request) => {
      // A signed-in person never sees the marketing page or the sign-in
      // form again: `/`, /sign-in and /sign-up send them into the app.
      // (/sign-up keeps working for invitation links: Clerk's ticket flow
      // signs the invitee up before they have a session.)
      const path = request.nextUrl.pathname;
      if (path === '/' || path === '/sign-in' || path.startsWith('/sign-in/')) {
        const { userId } = await auth();
        if (userId) return NextResponse.redirect(new URL('/dashboard', request.url));
        return;
      }
      if (isPublicRoute(request)) return;
      const { userId, redirectToSignIn } = await auth();
      if (!userId) return redirectToSignIn({ returnBackUrl: request.url });
    })
  : isProductionDeploy
    ? lockedOut
    : (_req: NextRequest) => NextResponse.next();

export default middleware;

export const config = {
  matcher: [
    // Skip Next internals and static files
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    // Always run on API routes
    '/(api|trpc)(.*)',
  ],
};
