# Fondok E2E (Playwright)

Browser regression suite for the web app. Runs on every push to `main`
(`.github/workflows/e2e.yml`).

## What this suite actually covers — read this first

**It tests the frontend against mocks and stubs. There is no real worker, no
real engine run, and no real auth anywhere in it.**

That is not a disclaimer, it is the operating limit, and it is written here
because forgetting it has already cost us. Specifically:

- **No real engine output.** Both servers get their engine data from the
  suite, never from `apps/worker`. Nothing here can tell you that an engine
  computes the right number.
- **No real auth.** `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_dummy` puts
  `src/lib/auth.ts` into its demo persona (Eshan Mehta · Brookfield Real
  Estate). Sign-in, org switching, session expiry and RBAC are untested.
- **No persisted state.** Every deal is rebuilt from a fixture per test.
- **No upload pipeline.** The wizard specs stage files in the browser; nothing
  is parsed, classified or extracted.

### The bug class this cannot catch

On 2026-09-12 a live deal rendered every Stabilization row on Overview as a
dash — Stabilized Occupancy / ADR / Revenue / NOI / Margin. The cause was not
in the frontend and not in the engine: the deal's **persisted** `engine_outputs`
row had been written before the expense engine started publishing a
`stabilization` block, so there was nothing for the UI to read. Re-running the
model fixed it.

No test in this directory could have found that, and none ever will. The data
that was wrong lives in the production database, and this suite never reads
the production database. What would catch it:

- a **worker-side migration/backfill check** that finds `engine_outputs` rows
  missing a block the current engine version publishes, and
- a **post-deploy smoke against the real deploy** that opens a known live deal
  and asserts its Stabilization figures are present — the shape of
  `apps/worker/scripts/smoke_live.py`, pointed at the web app.

What this suite *did* get out of that incident is the other half of the defect:
the screen gave the analyst five dashes and no way to know a re-run fixes it.
The banner that now says so is locked by
`08-overview-noi-basis.spec.ts` and `__tests__/stabilizedParity.test.tsx`.

### Where to put a test that doesn't belong here

Much of the app's behaviour is better proved by the Vitest component suite in
`apps/web/__tests__`, which can drive worker-connected state directly and runs
in seconds. Reach for Playwright only when the browser is load-bearing:
routing, real pointer interaction, portals, focus, a real Next render. If a
jsdom test can prove it, write the jsdom test.

## The two projects

The app's backend mode is a build-time constant (`NEXT_PUBLIC_WORKER_URL` is
inlined by Next), so the suite runs two dev servers.

| Project       | Port | `NEXT_PUBLIC_WORKER_URL` | Specs           |
| ------------- | ---- | ------------------------ | --------------- |
| `mock`        | 3000 | `''`                     | `e2e/*.spec.ts` |
| `worker-stub` | 3001 | `/__e2e-worker`          | `e2e/worker/*`  |

**`mock`** — `isWorkerConnected()` is false, the API client never fetches, and
every tab falls back to `src/lib/mockData.ts`. This is the shell: routing,
the new-deal wizard, tab navigation, and the Overview surfaces that render
without engine data.

Its blind spot is large and worth naming: with no engine output, big parts of
the app render their *empty* state. Financials shows "No P&L output yet", so
the sub-tabs and the Historicals worksheet never mount. Scenario Analysis has
no scenario list, so the chip row renders nothing. On Overview every figure is
a dash. A `mock`-project spec asserting "this is a dash" is therefore asserting
almost nothing about the dash logic — say so in the spec when you write one.

**`worker-stub`** — built against a same-origin path nothing serves, so
`isWorkerConnected()` is true and the app takes its real worker-connected code
paths; `e2e/fixtures/workerStub.ts` fulfils every request with `page.route`.
Same-origin on purpose: no CORS preflight to stub.

The document and extraction payloads are **not** written for this suite — they
are `__tests__/helpers/fon41LiveFixture.ts`, the extraction data captured off
Sam's live deal on 2026-09-09 and already used by the component suite. One
fixture, two consumers, so a contract change breaks both instead of leaving
this copy passing against a shape the worker stopped sending. Anything not
stubbed answers 404, exactly as a worker that never ran that engine would.

## Running locally

```bash
cd apps/web
pnpm install
pnpm exec playwright install chromium
pnpm test:e2e                      # both projects
pnpm test:e2e --project=mock       # just the mock-mode specs
pnpm test:e2e --project=worker-stub
pnpm test:e2e:ui                   # interactive
```

`e2e/global-setup.ts` fetches each route once before any test runs. `next dev`
compiles a route on first request, and without the warm-up the first
`page.goto` of the deal page paid a 20-60s compile — which surfaced as a
timeout on whichever spec happened to run first. That produced failures that
moved around between runs and taught everyone to re-run rather than read.
Don't remove it without replacing the determinism it buys.

## Pointing at a deployed build

```bash
PLAYWRIGHT_BASE_URL=https://fondok-app.vercel.app pnpm test:e2e
```

Playwright then skips both `webServer` blocks and the `worker-stub` project is
dropped from the run — we did not choose that build's `NEXT_PUBLIC_WORKER_URL`,
so there is nothing for the stub to intercept.

## The opt-in production test

`07-upload-pipeline-prod.spec.ts` uploads a real document through the real
worker and writes a real deal. It **never** runs in CI. Both an explicit flag
and the prod base URL are required, so it cannot run by accident:

```bash
FONDOK_E2E_PROD=1 PLAYWRIGHT_BASE_URL=https://fondok-app.vercel.app \
  pnpm test:e2e 07-upload-pipeline-prod
```

## Fixtures

`./fixtures/sample-t12.pdf` is a small placeholder PDF that satisfies the
wizard's extension allowlist. It is **not** parseable — to exercise the real
worker pipeline (the opt-in prod test), drop a real T-12 PDF over it first.

`./fixtures/tiny-unsupported.zip` drives the unsupported-file-type rejection.

`./fixtures/workerStub.ts` is the `worker-stub` route handler described above.

See `./fixtures/README.md` for the longer story on the binaries.

## Conventions

- **Never pin a count or a sentence of product copy.** That is what produced
  the 200-run red streak: a spec asserting "all 11 document categories" and
  three driving `#wizard-t12-drop` went red when FON-34 legitimately changed
  both, stayed red, and were ignored rather than fixed. Assert what must be
  true; a new category is not a regression.
- **Prefer stable hooks** — element ids, ARIA roles, `data-testid`. If the
  hook you need doesn't exist, add a `data-testid` to the component. Do not
  match prose.
- **A spec that tests something gone gets deleted**, not skipped. A skipped
  husk is a claim of coverage that isn't there.
- **A conditional `test.skip()` must name a real, permanent condition** (an
  affordance that only exists on a live deal), never "this was flaky".
