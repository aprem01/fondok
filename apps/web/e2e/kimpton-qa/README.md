# Kimpton QA regression suite

Automated coverage for Sam's full manual regression pass before replying to
Prem on external-testing readiness (test matrix:
`Fondok_Kimpton_Test_Matrix.xlsx`). Covers the ~20 highest-priority rows: the
three hard Pass Criteria, plus the known-issue tripwires (Levered IRR
regression, PIP/renovation Year-1 displacement, the incentive-fee
contradiction, MPI/ARI Year-1-only target, benchmark safety, Sources & Uses,
duplicate-deal workflow).

## Why this is a separate project from `prod`, not an extension of it

`e2e/prod` has one hard rule: **assert presence and structure, never a
value** (see `e2e/README.md` — a past violation caused a 200-run red
streak). Sam's pass criteria are the opposite: specific pinned numbers
(Revenue $12.94M, ADR $247.08, Levered IRR not regressed below -15%, …)
checked against a deal **she controls for exactly this purpose**. Rather than
weaken `prod`'s guarantee for the whole team, this suite is its own guarded
project with its own opt-in flag (`FONDOK_E2E_KIMPTON`, not
`FONDOK_E2E_PROD`), and is explicitly allowed to pin values because nothing
else reads this deal.

Same discipline as `prod` otherwise: Clerk Testing Token sign-in (no password
typed into a form), read-only (no override, no re-run, no upload — Sam does
the extraction herself, since re-extraction resets reviewed fields), and
gated so it can never run on a pull request.

## One-time setup

1. In the Fondok app, duplicate the Kimpton Angler deal (never the original
   tester deal, never re-upload the reference model). Copy its deal id from
   the URL (`/projects/<id>`).
2. Create `e2e/kimpton-qa/.env.local` (gitignored — never commit it):
   ```
   CLERK_PUBLISHABLE_KEY=pk_test_...
   CLERK_SECRET_KEY=sk_test_...
   FONDOK_SMOKE_EMAIL=...
   FONDOK_SMOKE_PASSWORD=...
   FONDOK_KIMPTON_DEAL_ID=<the duplicate deal's id>
   ```
   Reusing the same Clerk keys / smoke user as `e2e/prod` is fine — that was
   the choice made when this suite was built (same Clerk dev instance,
   same risk profile).

## Running it

```
./e2e/kimpton-qa/run.sh                       # everything
./e2e/kimpton-qa/run.sh --grep "Pass Criteria" # just the pass criteria
```

or, with the env already exported in your shell:

```
pnpm test:e2e:kimpton
```

## What's intentionally NOT automated here

- **The Levered IRR driver-by-driver bridge** (revenue, expense ratios,
  PIP displacement, price, debt, exit). No UI in this codebase decomposes
  IRR into those drivers — the only sensitivity grids are IRR/EM/CoC vs.
  exit-cap-rate × RevPAR-growth and a financing pair, not an attribution
  waterfall. `02-returns-and-benchmark.spec.ts` automates the regression
  *tripwire* (is IRR still catastrophically negative); the driver-by-driver
  explanation is still a manual read, logged in the matrix.
- **External workspace isolation (D18 / R-010)** — needs a second
  workspace/test account; the matrix already classifies this manual-only.
- **MPI/ARI "Year 1 only" scope** — the structural check (labels render
  figures) is automated; whether the UI offers one target vs. one per year
  is a manual look, noted inline in the spec.

## Known gap found while building this (worth raising with Prem)

As of the local checkout this suite was written against (commit `4b9ff98`,
2026-10-06), the Projects list's per-deal menu
(`src/app/projects/page.tsx` → `buildProjectMenu`) has only **View Details /
Archive / Delete Forever** — no Duplicate/Clone action — even though FON-110
("QA tooling for Sam's Phase 1: Duplicate deal, Compare runs, Kimpton golden
baseline") is marked "Ready for QA by Team" in Linear. `04-duplicate-deal-
workflow.spec.ts` checks the deployed app directly, so if Prem has shipped
it since Oct 6 the test should pass; if it's red, that's a real gap between
"Ready for QA" and what's reachable, not a bad test.
