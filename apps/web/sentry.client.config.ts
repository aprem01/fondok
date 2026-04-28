/**
 * Sentry browser-side initialization.
 *
 * This file is *only* picked up when the `@sentry/nextjs` plugin is wired
 * via `withSentryConfig` in `next.config.js`. Even then, we no-op unless
 * `NEXT_PUBLIC_SENTRY_DSN` is set, so flipping Sentry on at deploy time is
 * a one-env-var change.
 *
 * To enable Sentry:
 *   1. `npm install` — picks up the optional `@sentry/nextjs` dep.
 *   2. Set `NEXT_PUBLIC_SENTRY_DSN=https://...` on Vercel.
 *   3. Wrap `next.config.js` export with `withSentryConfig`.
 *   4. Redeploy.
 *
 * See DEPLOY.md "Sentry" for the full step-by-step.
 */

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;

if (dsn) {
  // Dynamic import — and routed through a non-literal specifier so the
  // TypeScript checker / bundler doesn't try to resolve @sentry/nextjs at
  // build time. With the package marked as `optionalDependencies`,
  // environments that don't install it just skip this branch silently.
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
        // Lean defaults — adjust as needed.
        tracesSampleRate: 0.1,
        replaysSessionSampleRate: 0.0,
        replaysOnErrorSampleRate: 1.0,
        release: process.env.NEXT_PUBLIC_BUILD_SHA ?? undefined,
      });
    })
    .catch(() => {
      // @sentry/nextjs not installed — silently no-op.
    });
}

export {};
