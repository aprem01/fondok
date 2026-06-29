import '@testing-library/jest-dom/vitest';

// Wave 4 reliability tests assume the worker base URL is set so the
// ``request()`` helper actually invokes fetch instead of throwing the
// "worker not connected" guard. The tests mock fetch directly; the
// value here just needs to be non-empty.
process.env.NEXT_PUBLIC_WORKER_URL = 'http://test-worker.local';
