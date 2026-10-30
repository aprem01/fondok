/**
 * Thin wrapper around `@sentry/nextjs` that respects the optional-dep
 * pattern used in sentry.{client,server}.config.ts.
 *
 * - No-op when NEXT_PUBLIC_SENTRY_DSN isn't set (dev / demo).
 * - No-op when @sentry/nextjs isn't installed (envs that skip optional deps).
 * - Safe to call from server-rendered code; the dynamic import resolves
 *   to either the browser or node SDK based on where it runs.
 */

type Context = Record<string, unknown>;

// `any` here is deliberate: the module shape is whichever Sentry SDK the
// runtime resolves (browser or node), and the package is an optional dep.
const dynImport = (m: string) =>
  (Function('m', 'return import(m)') as (m: string) => Promise<any>)(m);

export async function reportToSentry(
  error: unknown,
  context?: Context,
): Promise<void> {
  if (!process.env.NEXT_PUBLIC_SENTRY_DSN) return;
  try {
    const Sentry = await dynImport('@sentry/nextjs');
    Sentry.captureException(error, context ? { extra: context } : undefined);
  } catch {
    // @sentry/nextjs not installed — silently no-op.
  }
}
