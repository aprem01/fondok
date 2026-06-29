import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

/**
 * Vitest config — Wave 4 reliability fix regression suite.
 *
 * Scope: the small, surgical unit tests that exercise the three Wave
 * 4 reliability fixes (UUID deal load gate, fetch timeout, throttle).
 * NOT meant as a full unit-test runner for the whole app — Playwright
 * still owns the e2e suite. jsdom is enough for the hook + component
 * tests below; nothing here touches a real worker.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    include: ['__tests__/**/*.test.{ts,tsx}'],
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
});
