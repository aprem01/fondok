// @vitest-environment jsdom
// The public home page: one static screen. Nothing advances without the
// visitor, there is exactly one call to action in the body, every tab is
// reachable by keyboard, and the data-handling line is on the page.
import { cleanup, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import Landing, { DATA_HANDLING } from '@/components/landing/Hero';
import { REQUEST_ACCESS_HREF } from '@/components/landing/requestAccess';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('Landing', () => {
  it('shows the thesis, one body CTA and the data-handling line', () => {
    render(<Landing />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Pay the right price for every hotel you buy.');
    const main = screen.getByRole('main');
    const ctas = within(main).getAllByRole('link', { name: /request access/i });
    expect(ctas).toHaveLength(1);
    expect(ctas[0]).toHaveAttribute('href', REQUEST_ACCESS_HREF);
    expect(ctas[0]).toHaveTextContent(/^Request access$/);
    expect(within(main).queryByRole('link', { name: /sign in/i })).toBeNull();
    expect(within(main).queryByText(/see how it works/i)).toBeNull();
    for (const sentence of DATA_HANDLING) expect(screen.getByText(new RegExp(sentence.replace(/[’.()]/g, '.')))).toBeInTheDocument();
  });

  it('does not advance on its own', () => {
    vi.useFakeTimers();
    render(<Landing />);
    const first = screen.getByRole('tab', { selected: true });
    expect(first).toHaveTextContent('OM vs T-12');
    expect(screen.getByText(/OM vs T-12$/, { selector: 'div' })).toBeInTheDocument();
    vi.advanceTimersByTime(60_000);
    expect(screen.getByRole('tab', { selected: true })).toBe(first);
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(document.querySelector('[aria-roledescription="carousel"]')).toBeNull();
    // No crossfade class on first paint: nothing animates until a tab is picked.
    expect(screen.getByRole('tabpanel').className).not.toMatch(/home-swap/);
  });

  it('swaps only the card and caption when a tab is picked, mouse or keyboard', async () => {
    const user = userEvent.setup();
    render(<Landing />);
    const headlineBefore = screen.getByRole('heading', { level: 1 }).textContent;
    await user.click(screen.getByRole('tab', { name: 'Walk-away price' }));
    expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('Walk-away price');
    expect(screen.getByText(/Clears your hurdle/)).toBeInTheDocument();
    expect(screen.getByText(/The price that clears your hurdle, beside the ask/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(headlineBefore);
    expect(screen.getByRole('tabpanel').className).toMatch(/home-swap/);

    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('IC package');
    expect(document.activeElement).toBe(screen.getByRole('tab', { name: 'IC package' }));
    await user.keyboard('{Home}');
    expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('OM vs T-12');
    await user.keyboard('{End}');
    expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('IC package');
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { selected: true })).toHaveTextContent('OM vs T-12');

    // Roving tabindex: exactly one tab is in the tab order.
    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(4);
    expect(tabs.filter((t) => t.tabIndex === 0)).toHaveLength(1);
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', screen.getByRole('tab', { selected: true }).id);
  });

  it('keeps the sample-hotel card in the DOM before the tabs (phones show it)', () => {
    render(<Landing />);
    const figure = document.querySelector('main figure');
    const tablist = screen.getByRole('tablist');
    expect(figure).not.toBeNull();
    expect(figure!.className).not.toMatch(/hidden/);
    expect(figure!.compareDocumentPosition(tablist) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getAllByText(/Illustrative/).length).toBeGreaterThan(0);
  });
});
