# Fondok E2E (Playwright)

End-to-end regression suite covering the Wave 1 high-value flows so we
catch breakage before Sam/Eshan do on the next demo.

## Running locally

```bash
cd apps/web
pnpm install
pnpm test:e2e              # headless run
pnpm test:e2e:ui           # interactive UI mode (great for first-time exploration)
pnpm test:e2e:debug        # step through with the inspector
```

The default config boots `pnpm dev` on port 3000 with
`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_dummy` so the demo persona
(Eshan Mehta · Brookfield Real Estate) kicks in via
`src/lib/auth.ts` — no real Clerk account needed.

## Pointing at prod

```bash
PLAYWRIGHT_BASE_URL=https://fondok-app.vercel.app pnpm test:e2e
```

When `PLAYWRIGHT_BASE_URL` is set, Playwright skips the local
`webServer` block and just hits the URL you give it. The opt-in
`@prod` upload pipeline test in `07-upload-pipeline-prod.spec.ts` only
runs against the prod URL.

## Fixtures

`./fixtures/sample-t12.pdf` is a 95-byte placeholder PDF that
satisfies the wizard's extension allowlist. It is **not** parseable —
if you want to exercise the real worker pipeline (the `@prod` test),
drop a real T-12 PDF over the placeholder before running.

`./fixtures/tiny-unsupported.zip` is used by the unsupported-file-type
rejection test.

See `./fixtures/README.md` for the longer story.

## Clerk auth bypass

`pk_test_dummy` triggers demo mode via the `isClerkConfigured` check
in `src/lib/auth.ts`. This is the only auth bypass the suite uses —
no fake users, no API tokens. If you ever flip to real Clerk for
auth-gated tests, you'll need a test user + sign-in helper.

## Known limitations

- Override / source-badge tests are best-effort — those affordances
  only render on live worker deals (`isWorkerConnected()` true with a
  non-numeric deal id). They skip cleanly when the surface doesn't
  match. Once we ship the live demo seed, harden them.
- The validation banner regression tests intercept `/document_coverage`
  but the rest of the Validation tab's API calls fall through. If the
  tab gets reworked, the route stub may need to be widened.
- The `@prod` upload test deliberately runs ONLY against the live
  Vercel deploy and is opt-in via `PLAYWRIGHT_BASE_URL`. CI never runs
  it (we don't want CI writing garbage deals into prod).
