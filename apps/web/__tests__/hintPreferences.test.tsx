/**
 * R-074 — hint preferences. Pop-up hints keep their X and gain a small menu:
 * "Hide this hint" / "Hide all hints"; "Show hints again" is global (sidebar
 * user menu + Methodology header). Everything persists in localStorage.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import React from 'react';
import { IntroCard } from '@/components/help/IntroCard';
import { CoachMark } from '@/components/help/CoachMark';
import { ShowHintsAgainButton } from '@/components/help/HintOptionsMenu';
import { hideAllHints, showHintsAgain } from '@/components/help/useHintsEnabled';

beforeEach(() => {
  cleanup();
  window.localStorage.clear();
  document.cookie.split(';').forEach((c) => {
    const name = c.split('=')[0]?.trim();
    if (name) document.cookie = `${name}=; Max-Age=0; Path=/`;
  });
});

function renderIntro(key = 'r074-a') {
  return render(<IntroCard title="What this view is for" body="Body copy" dismissKey={key} />);
}

describe('IntroCard hint menu', () => {
  it('keeps the X and adds a hint-options menu', () => {
    renderIntro();
    expect(screen.getByText('What this view is for')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hint options' }));
    expect(screen.getByRole('menuitem', { name: 'Hide this hint' })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: 'Hide all hints' })).toBeInTheDocument();
  });

  it('"Hide this hint" hides only this card and persists it', () => {
    renderIntro('r074-a');
    fireEvent.click(screen.getByRole('button', { name: 'Hint options' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Hide this hint' }));
    expect(screen.queryByText('What this view is for')).toBeNull();
    expect(window.localStorage.getItem('fondok-intro-r074-a')).toBe('1');
    // A different hint is unaffected.
    cleanup();
    renderIntro('r074-b');
    expect(screen.getByText('What this view is for')).toBeInTheDocument();
  });

  it('"Hide all hints" hides every hint and persists the global switch', () => {
    render(
      <>
        <IntroCard title="Card A" body="a" dismissKey="r074-a" />
        <IntroCard title="Card B" body="b" dismissKey="r074-b" />
      </>,
    );
    fireEvent.click(screen.getAllByRole('button', { name: 'Hint options' })[0]);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Hide all hints' }));
    expect(screen.queryByText('Card A')).toBeNull();
    expect(screen.queryByText('Card B')).toBeNull();
    expect(window.localStorage.getItem('fondok:coachmarks:disabled')).toBe('true');
    cleanup();
    renderIntro('r074-c');
    expect(screen.queryByText('What this view is for')).toBeNull();
  });

  it('"Show hints again" re-enables hints and forgets dismissals', () => {
    window.localStorage.setItem('fondok-intro-r074-a', '1');
    hideAllHints();
    render(
      <>
        <IntroCard title="Card A" body="a" dismissKey="r074-a" />
        <ShowHintsAgainButton />
      </>,
    );
    expect(screen.queryByText('Card A')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /Show hints again/ }));
    expect(screen.getByText('Card A')).toBeInTheDocument();
    expect(window.localStorage.getItem('fondok-intro-r074-a')).toBeNull();
    expect(window.localStorage.getItem('fondok:coachmarks:disabled')).toBe('false');
  });

  it('survives storage that throws (try/catch) — the hint still hides for this view', () => {
    const orig = Storage.prototype.setItem;
    Storage.prototype.setItem = () => { throw new Error('blocked'); };
    try {
      renderIntro();
      fireEvent.click(screen.getByRole('button', { name: 'Hint options' }));
      fireEvent.click(screen.getByRole('menuitem', { name: 'Hide this hint' }));
      expect(screen.queryByText('What this view is for')).toBeNull();
      expect(() => hideAllHints()).not.toThrow();
      expect(() => showHintsAgain()).not.toThrow();
    } finally {
      Storage.prototype.setItem = orig;
    }
  });
});

describe('CoachMark hint menu', () => {
  it('keeps the X, offers Hide this / Hide all, and "Hide this hint" persists the dismissal', async () => {
    render(
      <CoachMark anchorId="r074-coach" title="Coach title" body="Coach body">
        <button type="button">Target</button>
      </CoachMark>,
    );
    expect(await screen.findByText('Coach title')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Dismiss hint' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Hint options' }));
    expect(screen.getByRole('menuitem', { name: 'Hide all hints' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Hide this hint' }));
    expect(screen.queryByText('Coach title')).toBeNull();
    expect(window.localStorage.getItem('fondok:coachmark:r074-coach:dismissed')).toBe('true');

    // Global "Show hints again" brings it back.
    act(() => { showHintsAgain(); });
    expect(await screen.findByText('Coach title')).toBeInTheDocument();
  });
});
