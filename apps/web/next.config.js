/** @type {import('next').NextConfig} */
const nextConfig = { reactStrictMode: true };

// @sentry/nextjs is in optionalDependencies — defensive load so envs that
// skip optional deps don't break the build. `withSentryConfig` is what
// makes Next.js pick up sentry.client.config.ts + sentry.server.config.ts
// at runtime; without this wrap, the init files exist but never load.
let withSentryConfig = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
  withSentryConfig = require('@sentry/nextjs').withSentryConfig;
} catch {
  withSentryConfig = null;
}

const sentryBuildOptions = {
  // Suppress noisy build output unless something actually goes wrong.
  silent: true,
  // Source-map upload only fires when org/project/auth-token are ALL set.
  // Missing any of them = source maps are bundled but not uploaded; the
  // SDK still ships errors with line numbers (minified) which is enough
  // to act on if Sentry release tracking is set up separately.
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
  authToken: process.env.SENTRY_AUTH_TOKEN,
  // Hide source maps from the public bundle once uploaded — Sentry still
  // reads them for stack-frame resolution.
  hideSourceMaps: true,
  disableLogger: true,
  // Proxy /monitoring/* → Sentry ingestion endpoint so ad-blockers don't
  // drop client-error reports.
  tunnelRoute: '/monitoring',
};

module.exports = withSentryConfig
  ? withSentryConfig(nextConfig, sentryBuildOptions)
  : nextConfig;
