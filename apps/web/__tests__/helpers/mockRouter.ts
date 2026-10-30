/**
 * The mocked `next/navigation` router most tab tests share.
 *
 * Files keep their router state in a module-level `vi.hoisted` object so a
 * test can set `nav.params = new URLSearchParams('tab=…&sub=…')` before it
 * mounts. That object outlives each test: a routing test that leaves
 * `?sub=…` behind opens the NEXT test on the wrong sub-tab. In file order the
 * routing block ran last and nothing noticed; in shuffled order (CI's
 * `--sequence.shuffle`) whole files failed. Call `resetRouter(nav)` in the
 * file's top-level `beforeEach` so every test starts on the bare URL.
 */
import type { Mock } from 'vitest';

export interface MockRouterState {
  params: URLSearchParams;
  push: Mock;
  replace: Mock;
}

export function resetRouter(nav: MockRouterState, query = ''): void {
  nav.params = new URLSearchParams(query);
  nav.push.mockClear();
  nav.replace.mockClear();
}
