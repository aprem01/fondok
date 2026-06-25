'use client';

import { useState } from 'react';

/**
 * Sentry verification page. Visit /sentry-example-page in prod after
 * NEXT_PUBLIC_SENTRY_DSN is set, click the buttons, and check the
 * Sentry dashboard. Each button covers one error path:
 *
 *  - Client render error: the React error boundary catches and ships.
 *  - Async unhandled rejection: Sentry's global unhandledrejection
 *    listener catches.
 *  - Server thrown error: server-side route handler crashes.
 */
export default function SentryExamplePage() {
  const [serverResult, setServerResult] = useState<string>('');

  function throwClientError() {
    throw new Error(
      'Sentry test: client render error from /sentry-example-page',
    );
  }

  async function throwAsyncRejection() {
    // Unhandled rejection — Sentry's global handler picks this up.
    await Promise.reject(
      new Error('Sentry test: unhandled async rejection'),
    );
  }

  async function triggerServerError() {
    setServerResult('calling...');
    try {
      const res = await fetch('/sentry-example-api');
      setServerResult(`status ${res.status}`);
    } catch (err) {
      setServerResult(`fetch failed: ${(err as Error).message}`);
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center bg-bg px-6 py-12">
      <div className="w-full max-w-lg">
        <h1 className="text-[20px] font-semibold text-ink-900">
          Sentry connection test
        </h1>
        <p className="text-[13px] text-ink-700 mt-2 mb-6">
          Three buttons, three error paths. Each one should show up as a
          separate issue in the Sentry dashboard within a few seconds.
        </p>
        <div className="space-y-3">
          <button
            type="button"
            onClick={throwClientError}
            className="block w-full px-4 py-3 rounded-md bg-danger-50 border border-danger-200 text-danger-700 text-[13px] font-medium hover:bg-danger-100 text-left"
          >
            Throw client render error
            <span className="block text-[11px] text-ink-500 font-normal mt-0.5">
              Synchronous throw — caught by the global error boundary.
            </span>
          </button>
          <button
            type="button"
            onClick={() => void throwAsyncRejection()}
            className="block w-full px-4 py-3 rounded-md bg-warn-50 border border-warn-200 text-warn-700 text-[13px] font-medium hover:bg-warn-100 text-left"
          >
            Throw async unhandled rejection
            <span className="block text-[11px] text-ink-500 font-normal mt-0.5">
              Promise rejected with no .catch() — Sentry global handler.
            </span>
          </button>
          <button
            type="button"
            onClick={() => void triggerServerError()}
            className="block w-full px-4 py-3 rounded-md bg-brand-50 border border-brand-100 text-brand-700 text-[13px] font-medium hover:bg-brand-100 text-left"
          >
            Trigger server-side error
            <span className="block text-[11px] text-ink-500 font-normal mt-0.5">
              Hits /sentry-example-api which throws server-side.
            </span>
          </button>
          {serverResult && (
            <div className="text-[12px] text-ink-700 font-mono px-3 py-2 bg-ink-300/10 rounded">
              {serverResult}
            </div>
          )}
        </div>
        <p className="text-[11px] text-ink-500 mt-6">
          When Sentry is not configured (NEXT_PUBLIC_SENTRY_DSN unset),
          these errors still throw — they just don't ship anywhere.
        </p>
      </div>
    </main>
  );
}
