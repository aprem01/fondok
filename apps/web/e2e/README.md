# Fondok E2E (Playwright)

Browser regression suite for the web app. Runs on every push to `main`
(`.github/workflows/e2e.yml`). A third, opt-in project — the post-deploy smoke
against one real deal in the deployed app — runs after a production alias
promotion (`.github/workflows/post-deploy-smoke.yml`); see
[The post-deploy production smoke](#the-post-deploy-production-smoke-e2eprod).

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

No test in the `mock` or `worker-stub` projects could have found that, and
none ever will. The data that was wrong lives in the production database, and
neither project reads the production database. Two things catch it:

- a **worker-side check** that finds `engine_outputs` rows missing a block the
  current engine version publishes (FON-75), and
- a **post-deploy smoke against the real deploy** that opens a known live deal
  and asserts its Stabilization figures are present. That one now exists, in
  `e2e/prod/` — see the next section.

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

## The projects

The app's backend mode is a build-time constant (`NEXT_PUBLIC_WORKER_URL` is
inlined by Next), so the suite runs two dev servers. A third project exists
only when someone explicitly opts in.

| Project       | Port | `NEXT_PUBLIC_WORKER_URL` | Specs              | Runs in CI            |
| ------------- | ---- | ------------------------ | ------------------ | --------------------- |
| `mock`        | 3000 | `''`                     | `e2e/*.spec.ts`    | yes                   |
| `worker-stub` | 3001 | `/__e2e-worker`          | `e2e/worker/*`     | yes                   |
| `prod`        | —    | the deployed build's     | `e2e/prod/*`       | post-deploy only      |

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

## The post-deploy production smoke (`e2e/prod/`)

This is the check that reads **persisted production data through the deployed
app** — the thing nothing else here does, and the reason the 2026-09-12
Stabilization defect reached an analyst with every suite green.

It opens **one pinned, pre-existing deal** in the deployed app, signed in as a
real user, and asserts the figures are there. Files:

| File                           | Job                                                      |
| ------------------------------ | -------------------------------------------------------- |
| `prod/guard.ts`                | The opt-in rule and the required-secret list, defined once |
| `prod/global-setup.ts`         | Validates secrets, then mints the Clerk Testing Token     |
| `prod/01-live-deal-smoke.spec.ts` | The smoke itself                                       |
| `prod/guard-check.mjs`         | The test **for** the guard                                |

Driven by `.github/workflows/post-deploy-smoke.yml`.

### It asserts presence and structure, never a value

This is the rule, and it is not negotiable. Not an IRR, not a total, not a
sentence of product copy. A pinned number goes red the first time an analyst
legitimately edits an assumption on the pinned deal, and a smoke that cries
wolf is a smoke somebody switches off — which is precisely the 200-run red
streak this suite spent a ticket recovering from. The question it answers is
*"does the deployed app render this deal's numbers at all"*, because that is
the question that was unanswered on 2026-09-12.

What it asserts, all read-only:

1. Overview renders and the `stabilization-needs-rerun` banner is **absent**
   — the exact regression.
2. Every Stabilization row (Occupancy, ADR, Revenue, NOI, NOI Margin) renders
   something containing a digit rather than the em dash.
3. No page-level stale-run banner (FON-75). Written defensively against a set
   of candidate test ids, because that banner is being built in a sibling
   ticket — **when it lands, put its `data-testid` in `STALE_RUN_TESTIDS` or
   this assertion is a no-op.**
4. The engine-failures banner is absent (no engine in the canonical run is
   `failed`).
5. Sources & Uses reports its **in-balance** state — a structural invariant of
   the capital stack (required equity is the plug), not a pinned total.
6. Returns, Debt and Partnership each render a non-dash headline figure. Each
   is matched against a small list of candidate labels, so a legitimate rename
   in a sibling ticket does not go red.
7. The run really happened against `fondok-app.vercel.app`.

### It writes nothing

No deal creation, no upload, no override, no re-run, no PATCH. Every
navigation is a GET. The only state it touches is one `localStorage` key in
its own throwaway browser profile, to stop coachmarks covering the rows it
reads. A smoke that mutates production is a smoke nobody will run — and the
pinned deal has to stay trustworthy for the next hundred runs.

### Credentials model

The app is on a Clerk **development** instance, where a scripted headless
sign-in trips bot detection. So the smoke does not drive the sign-in form at
all: `@clerk/testing` mints a **Testing Token** in global setup, and
`clerk.signIn()` talks to the loaded Clerk client directly.

Every value comes from the environment; **nothing is committed**. As GitHub
Actions repository secrets (Settings → Secrets and variables → Actions):

| Secret                  | What it is                                                                |
| ----------------------- | ------------------------------------------------------------------------- |
| `CLERK_PUBLISHABLE_KEY` | Clerk **development** instance publishable key (`pk_test_…`), the same instance the deployed app was built against |
| `CLERK_SECRET_KEY`      | Clerk **development** instance secret key (`sk_test_…`)                    |
| `FONDOK_SMOKE_EMAIL`    | A dedicated Clerk test user — a `+clerk_test` address, member of the org that owns the smoke deal, owning nothing else |
| `FONDOK_SMOKE_PASSWORD` | That user's password                                                      |
| `FONDOK_SMOKE_DEAL_ID`  | The permanent deal to read. Pick one that will never be archived          |

**Development-instance keys only.** `global-setup.ts` refuses `pk_live_` /
`sk_live_` outright. That is what keeps the blast radius of a leaked key at
"a test tenant with one throwaway user".

Until all five secrets exist the workflow's smoke job no-ops with a warning
rather than failing every deploy — so **a green job is not proof the deploy
was smoked**; read the job summary.

### It runs against the alias, after the alias moves

`fondok-app.vercel.app` is a plain alias over the Vercel project's
auto-managed domain, promoted by `.github/workflows/alias-fondok-app.yml`
*after* a deployment goes Ready. A smoke pointed at a raw
`fondok-<hash>-…vercel.app` URL would test a bundle no analyst is served, so:

- the deploy-fired trigger is the **completion of the alias workflow**
  (plus `workflow_dispatch`, plus a `prod-alias-promoted` `repository_dispatch`
  for an alias promoted by hand from a laptop),
- `guard.ts` requires the base URL's **host** to be exactly that alias, and
- the spec re-asserts the host it actually talked to.

### The guard, and the test for the guard

The `prod` project requires **both** an explicit `FONDOK_E2E_PROD=1` and a
`PLAYWRIGHT_BASE_URL` on the production host. Without both, the project is not
in the config's `projects` array at all — there is no test for a stray
`--project` or a `.only` to select, and a pull request sets neither.

Three layers, one predicate (`e2e/prod/guard.ts`): the config omits the
project, the global setup returns before reading a secret, and the spec skips
itself. And because a guard nobody tests is a guard that drifts open:

```bash
cd apps/web && pnpm test:e2e:prod:guard
```

asks the **real** config what it would select under five environments short of
the opt-in — including a host that merely *contains* the production hostname —
and fails if any of them selects a prod test, or if the ordinary suite selects
nothing (which would make the check vacuously true). It runs on every pull
request that touches `e2e/`, `playwright.config.ts` or `package.json`.

### Running it by hand

```bash
cd apps/web
CLERK_PUBLISHABLE_KEY=pk_test_… CLERK_SECRET_KEY=sk_test_… \
FONDOK_SMOKE_EMAIL=… FONDOK_SMOKE_PASSWORD=… FONDOK_SMOKE_DEAL_ID=… \
FONDOK_E2E_PROD=1 PLAYWRIGHT_BASE_URL=https://fondok-app.vercel.app \
  pnpm test:e2e:prod
```

`test:e2e:prod` passes `--project=prod`, so the `mock` specs are not dragged
along against production — several of them assert mock-mode behaviour and
would be meaningless there.

### Known weakness, worth fixing

`EngineFailuresBanner` has no `data-testid`, so its absence is asserted by
role plus its own vocabulary. If that copy is rewritten the assertion goes
quietly green instead of falsely red — the safe direction for a smoke, but a
real gap. Adding `data-testid="engine-failures"` to that component closes it.

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
- **The `prod` smoke asserts presence and structure, never a value.** It reads
  a real deal an analyst is also editing. The moment it pins a number it
  starts going red for reasons that are not regressions, and then it gets
  switched off. Same reasoning as the first bullet, higher stakes.
