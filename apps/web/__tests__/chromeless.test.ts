import { describe, expect, it } from 'vitest';
import { isChromelessPath } from '@/components/layout/AppShell';

// Signed-out pages must never render the app sidebar.
describe('isChromelessPath', () => {
  it('hides the app chrome on the home, sign-in and sign-up pages', () => {
    for (const p of ['/', '/sign-in', '/sign-in/factor-one', '/sign-up', '/sign-up/verify']) {
      expect(isChromelessPath(p)).toBe(true);
    }
  });
  it('keeps the app chrome on app pages', () => {
    for (const p of ['/dashboard', '/projects', '/settings', '/sign-inx', '/signin']) {
      expect(isChromelessPath(p)).toBe(false);
    }
  });
});
