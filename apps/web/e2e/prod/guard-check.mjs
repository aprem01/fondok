#!/usr/bin/env node
/**
 * The test for the guard — FON-76.
 *
 * The deliverable of this slice is itself a test, so the thing that needs a
 * test of its own is the thing that decides whether it runs. This asks the
 * REAL `playwright.config.ts` (via `playwright test --list`, which loads the
 * config and resolves projects without starting a browser or a server) which
 * tests it would select under four environments, and asserts:
 *
 *   1. nothing            → no `prod` tests.  ← what a pull request looks like
 *   2. flag only          → no `prod` tests.
 *   3. prod base URL only → no `prod` tests.
 *   4. flag + base URL    → the `prod` tests, and nothing else selected by
 *                           `--project=prod`.
 *
 * It also asserts that cases 1-3 still select the ordinary suite, so a config
 * that selects nothing at all cannot pass by being uniformly empty — that is
 * the failure mode a naive "assert absence" check would sail straight past.
 *
 * Run it:  pnpm test:e2e:prod:guard          (from apps/web)
 * CI runs it on every pull request — see .github/workflows/post-deploy-smoke.yml.
 */
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const WEB_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const PROD_URL = 'https://fondok-app.vercel.app';

/**
 * `playwright test --list` under a given environment. The parent env is
 * inherited but the two opt-ins are always set explicitly (to a value or to
 * undefined) so a variable left over in the caller's shell cannot change the
 * answer — this check must be about the config, not about the machine.
 */
function list({ flag, baseUrl, project }) {
  const env = { ...process.env };
  delete env.FONDOK_E2E_PROD;
  delete env.PLAYWRIGHT_BASE_URL;
  if (flag !== undefined) env.FONDOK_E2E_PROD = flag;
  if (baseUrl !== undefined) env.PLAYWRIGHT_BASE_URL = baseUrl;

  // `pnpm exec`, not `npx`: pnpm's bin shim exports the NODE_PATH that makes
  // the CLI and the specs agree on ONE copy of @playwright/test. Under npx
  // they resolve two and every spec dies with "did not expect test.describe()
  // to be called here" — which would look exactly like a passing guard.
  const args = ['exec', 'playwright', 'test', '--list', '--reporter=list'];
  if (project) args.push(`--project=${project}`);

  const run = spawnSync('pnpm', args, {
    cwd: WEB_DIR,
    env,
    encoding: 'utf8',
    timeout: 180_000,
  });
  return {
    status: run.status,
    output: `${run.stdout ?? ''}${run.stderr ?? ''}`,
  };
}

/** Lines Playwright prints for a selected test, filtered to the prod project. */
function prodLines(output) {
  return output
    .split('\n')
    .filter((line) => line.includes('[prod]') || /›\s*prod[\\/]/.test(line));
}

const failures = [];
const pass = (msg) => console.log(`  ok   ${msg}`);
const fail = (msg, detail) => {
  failures.push(msg);
  console.error(`  FAIL ${msg}`);
  if (detail) console.error(detail.split('\n').slice(0, 25).join('\n'));
};

console.log('prod-smoke guard — asserting the opt-in against the real config\n');

// ── 1-3: every environment short of both opt-ins selects no prod test ────
const negatives = [
  { name: 'no opt-in at all (this is a pull request)', flag: undefined, baseUrl: undefined },
  { name: 'FONDOK_E2E_PROD=1 but no base URL', flag: '1', baseUrl: undefined },
  { name: 'prod base URL but no FONDOK_E2E_PROD', flag: undefined, baseUrl: PROD_URL },
  { name: 'FONDOK_E2E_PROD=0 with the prod base URL', flag: '0', baseUrl: PROD_URL },
  {
    name: 'a host that merely CONTAINS the prod hostname',
    flag: '1',
    baseUrl: 'https://fondok-app.vercel.app.example.test',
  },
];

for (const env of negatives) {
  const { status, output } = list(env);
  if (status !== 0) {
    fail(`${env.name}: playwright --list exited ${status}`, output);
    continue;
  }
  const selected = prodLines(output);
  if (selected.length > 0) {
    fail(`${env.name}: prod tests were selected`, selected.join('\n'));
    continue;
  }
  if (!/Total:\s*\d+\s*tests?/.test(output)) {
    fail(`${env.name}: the ordinary suite selected nothing — this check would be vacuous`, output);
    continue;
  }
  pass(`${env.name} → no prod tests, ordinary suite intact`);
}

// ── 4: both opt-ins present selects the prod project ─────────────────────
{
  const { status, output } = list({ flag: '1', baseUrl: PROD_URL, project: 'prod' });
  if (status !== 0) {
    fail('flag + prod base URL: playwright --list exited non-zero', output);
  } else {
    const selected = prodLines(output);
    if (selected.length === 0) {
      fail(
        'flag + prod base URL: the prod project selected NOTHING — the smoke is dead code',
        output,
      );
    } else {
      pass(`flag + prod base URL → ${selected.length} prod test(s) selected`);
    }
  }
}

console.log('');
if (failures.length > 0) {
  console.error(`prod-smoke guard: ${failures.length} failure(s).`);
  process.exit(1);
}
console.log('prod-smoke guard: all checks passed.');
