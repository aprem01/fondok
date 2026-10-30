import '@testing-library/jest-dom/vitest';
import { afterAll, afterEach, expect } from 'vitest';

// Wave 4 reliability tests assume the worker base URL is set so the
// ``request()`` helper actually invokes fetch instead of throwing the
// "worker not connected" guard. The tests mock fetch directly; the
// value here just needs to be non-empty.
process.env.NEXT_PUBLIC_WORKER_URL = 'http://test-worker.local';

// ---------------------------------------------------------------------------
// No real network in unit tests.
//
// CI (2026-10-30) logged `useHistoricalBaseline: TypeError: fetch failed …
// getaddrinfo EAI_AGAIN test-worker.local`: a test rendered a component whose
// data hook was not mocked, so it hit the configured worker URL. Locally that
// fails fast (ENOTFOUND); on the CI runner the DNS retry stalls, and the
// render races the assertion. Every fetch must now be mocked.
//
// The stub below replaces the real fetch for the whole file. A test overrides
// it with `vi.spyOn(globalThis, 'fetch')`, `vi.stubGlobal('fetch', …)` or by
// assigning `global.fetch`; restoring returns to this stub, never to the real
// fetch. An unmocked call rejects with an error naming the URL and the test,
// and — because hooks usually swallow fetch errors into console.warn — it is
// also recorded and fails the test in afterEach, so it cannot pass silently.
// ---------------------------------------------------------------------------
const unmockedCalls: string[] = [];

function describeUrl(input: unknown): string {
  if (typeof input === 'string') return input;
  if (input instanceof URL) return input.href;
  if (input && typeof input === 'object' && 'url' in input) return String((input as { url: unknown }).url);
  return String(input);
}

function unmockedFetch(input: unknown): Promise<Response> {
  const state = expect.getState();
  const test = state.currentTestName ?? '(outside a test — module scope or a late effect)';
  const file = state.testPath ?? '(unknown file)';
  const message =
    `Unmocked fetch in a unit test: ${describeUrl(input)}\n` +
    `  test: ${test}\n  file: ${file}\n` +
    `Mock the hook or the api the component uses, or stub fetch with vi.spyOn(globalThis, 'fetch').`;
  unmockedCalls.push(message);
  return Promise.reject(new Error(message));
}

globalThis.fetch = unmockedFetch as typeof fetch;

function failOnUnmockedCalls(): void {
  if (unmockedCalls.length === 0) return;
  const calls = unmockedCalls.splice(0);
  throw new Error(`${calls.length} unmocked fetch call(s):\n\n${calls.join('\n\n')}`);
}

afterEach(failOnUnmockedCalls);
afterAll(failOnUnmockedCalls);
