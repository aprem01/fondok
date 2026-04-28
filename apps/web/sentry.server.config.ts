/**
 * Sentry server-side (Node) initialization.
 *
 * Same gate as the client config — no-op unless `NEXT_PUBLIC_SENTRY_DSN`
 * is set. Server-only secrets (e.g. `SENTRY_AUTH_TOKEN` for source-map
 * upload) belong in `next.config.js` under `withSentryConfig`, not here.
 *
 * See DEPLOY.md "Sentry" for the full step-by-step.
 */

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  const sentryModule = '@sentry/nextjs';
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (Function('m', 'return import(m)') as (m: string) => Promise<any>)(sentryModule)
    .then((Sentry) => {
      Sentry.init({
        dsn,
        environment:
          process.env.NEXT_PUBLIC_VERCEL_ENV ??
          process.env.NODE_ENV ??
          'development',
        tracesSampleRate: 0.1,
        release: process.env.NEXT_PUBLIC_BUILD_SHA ?? undefined,
      });
    })
    .catch(() => {
      // @sentry/nextjs not installed — silently no-op.
    });
}

export {};
