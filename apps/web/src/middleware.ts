/**
 * Auth middleware — feature-flagged by `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`.
 *
 * When Clerk is configured, we wrap the app with `clerkMiddleware` and
 * protect every route except the public surfaces (`/`, `/sign-in/*`,
 * `/sign-up/*`, `/diag`). When unset (demo mode), we export a no-op
 * pass-through so the app boots without any auth backend.
 */
import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server';
import { NextResponse, type NextRequest } from 'next/server';

const clerkKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const isClerkConfigured =
  !!clerkKey &&
  (clerkKey.startsWith('pk_test_') || clerkKey.startsWith('pk_live_')) &&
  !clerkKey.endsWith('_dummy');

const isPublicRoute = createRouteMatcher([
  '/',
  '/sign-in(.*)',
  '/sign-up(.*)',
  '/diag',
  '/landing',
]);

const middleware = isClerkConfigured
  ? clerkMiddleware(async (auth, request) => {
      if (isPublicRoute(request)) return;
      const { userId, redirectToSignIn } = await auth();
      if (!userId) return redirectToSignIn({ returnBackUrl: request.url });
    })
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
