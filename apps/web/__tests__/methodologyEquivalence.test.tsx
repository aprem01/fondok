/**
 * FON-120 — the split of the client methodology into packages/methodology
 * must not change a word of what a reader sees.
 *
 * Renders the client page as it stood before the split (frozen as a fixture,
 * `__tests__/fixtures/methodology/legacyClientPage.tsx`) and the new page
 * (generated from the section markdown), then compares every section's text
 * content, section by section, in order. Whitespace is removed on both sides
 * before comparing: JSX layout whitespace and markdown line wrapping differ,
 * and element boundaries carry no text in either DOM, so the comparison is on
 * every non-whitespace character in order — wording, numbers, punctuation,
 * quotes and dashes.
 *
 * The fixture is a one-time migration guard. When a sentence of the client
 * methodology changes on purpose, change it in the section markdown AND the
 * fixture — or delete the fixture and this test once the split has been
 * reviewed; the client-copy discipline test and methodologyLevels.test.ts
 * carry the anchors from then on.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import React from 'react';
import MethodologyPage from '@/app/methodology/page';
import LegacyMethodologyClientPage from './fixtures/methodology/legacyClientPage';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/methodology',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

afterEach(cleanup);

const squash = (s: string | null | undefined) => (s ?? '').replace(/\s+/g, '');

function sectionsOf(root: HTMLElement): Array<{ id: string; text: string }> {
  return Array.from(root.querySelectorAll('section[id]')).map((el) => ({
    id: el.getAttribute('id') ?? '',
    text: squash(el.textContent),
  }));
}

describe('/methodology — the generated conceptual level renders the legacy page text', () => {
  it('has the same sections, in the same order, with the same ids', () => {
    const legacy = render(<LegacyMethodologyClientPage />);
    const legacyIds = sectionsOf(legacy.container).map((s) => s.id);
    cleanup();
    const next = render(<MethodologyPage />);
    const nextIds = sectionsOf(next.container).map((s) => s.id);
    expect(nextIds).toEqual(legacyIds);
    expect(nextIds).toHaveLength(10);
  });

  it('renders byte-identical text per section (whitespace removed)', () => {
    const legacy = render(<LegacyMethodologyClientPage />);
    const legacySections = sectionsOf(legacy.container);
    cleanup();
    const next = render(<MethodologyPage />);
    const nextSections = sectionsOf(next.container);
    for (let i = 0; i < legacySections.length; i++) {
      const a = legacySections[i];
      const b = nextSections[i];
      expect(b.id).toBe(a.id);
      if (a.text !== b.text) {
        // Point at the first divergence so a failure reads as a sentence, not two blobs.
        let k = 0;
        while (k < a.text.length && k < b.text.length && a.text[k] === b.text[k]) k++;
        const window = (s: string) => s.slice(Math.max(0, k - 60), k + 120);
        throw new Error(
          `section #${a.id} text differs at offset ${k}:\n  legacy: …${window(a.text)}…\n  new:    …${window(b.text)}…`,
        );
      }
    }
  });

  it('renders the same page header and footer actions', () => {
    const legacy = render(<LegacyMethodologyClientPage />);
    const legacyText = squash(legacy.container.textContent);
    cleanup();
    const next = render(<MethodologyPage />);
    expect(squash(next.container.textContent)).toBe(legacyText);
  });
});
