/**
 * HScroll — the discoverable horizontal scroll container (FON-41 E-026).
 *
 * External testers: on Historical P&L the extra year columns sat off-screen
 * and the horizontal scroll was undiscoverable. The container now draws a
 * persistent scrollbar (`.fondok-hscroll`, `scrollbar-gutter: stable`) and,
 * while content overflows, a right-edge fade plus a "N more years →" cue
 * counted off the real `[data-year-col]` headers past the visible edge.
 *
 * jsdom has no layout, so the geometry is pinned per element here.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import { HScroll } from '@/components/project/pl/HScroll';

function layout(el: HTMLElement, props: Record<string, number>) {
  for (const [k, v] of Object.entries(props)) {
    Object.defineProperty(el, k, { configurable: true, get: () => v });
  }
}

function Grid({ cols }: { cols: number }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Line</th>
          {Array.from({ length: cols }, (_, i) => (
            <th key={i} data-year-col>{`Y${i + 1}`}</th>
          ))}
        </tr>
      </thead>
    </table>
  );
}

afterEach(cleanup);

describe('HScroll', () => {
  it('renders the persistent scroll container and no cue when nothing overflows', () => {
    render(
      <HScroll unit="year" data-testid="hs">
        <Grid cols={2} />
      </HScroll>,
    );
    const scroller = screen.getByTestId('hs-scroller');
    expect(scroller.className).toContain('fondok-hscroll');
    expect(scroller.getAttribute('data-overflow-right')).toBe('false');
    expect(screen.queryByTestId('hs-cue')).toBeNull();
    expect(document.querySelector('.fondok-hscroll-fade-right')).toBeNull();
  });

  it('counts the year columns past the visible edge and offers "N more years →" with a fade', async () => {
    const { container } = render(
      <HScroll unit="year" data-testid="hs">
        <Grid cols={5} />
      </HScroll>,
    );
    const scroller = screen.getByTestId('hs-scroller');
    // 500px of columns in a 300px viewport, scrolled to the start.
    layout(scroller, { clientWidth: 300, scrollWidth: 500 });
    container.querySelectorAll<HTMLElement>('[data-year-col]').forEach((th, i) =>
      layout(th, { offsetLeft: i * 100, offsetWidth: 100 }),
    );
    fireEvent.scroll(scroller);

    const cue = await screen.findByTestId('hs-cue');
    // Columns at 300 and 400 have their midpoints past the 300px edge.
    expect(cue).toHaveTextContent('2 more years →');
    expect(scroller.getAttribute('data-overflow-right')).toBe('true');
    expect(document.querySelector('.fondok-hscroll-fade-right')).not.toBeNull();
    expect(document.querySelector('.fondok-hscroll-fade-left')).toBeNull();

    // Singular when one column is hidden.
    layout(scroller, { scrollLeft: 100 });
    fireEvent.scroll(scroller);
    await waitFor(() => expect(screen.getByTestId('hs-cue')).toHaveTextContent('1 more year →'));
    expect(document.querySelector('.fondok-hscroll-fade-left')).not.toBeNull();

    // Scrolled to the end → no cue, no right fade.
    layout(scroller, { scrollLeft: 200 });
    fireEvent.scroll(scroller);
    await waitFor(() => expect(screen.queryByTestId('hs-cue')).toBeNull());
    expect(document.querySelector('.fondok-hscroll-fade-right')).toBeNull();
  });

  it('the cue is a real control — it scrolls the container over', async () => {
    const { container } = render(
      <HScroll unit="year" data-testid="hs">
        <Grid cols={5} />
      </HScroll>,
    );
    const scroller = screen.getByTestId('hs-scroller');
    layout(scroller, { clientWidth: 300, scrollWidth: 500 });
    container.querySelectorAll<HTMLElement>('[data-year-col]').forEach((th, i) =>
      layout(th, { offsetLeft: i * 100, offsetWidth: 100 }),
    );
    const scrollBy = vi.fn();
    (scroller as HTMLElement & { scrollBy: typeof scrollBy }).scrollBy = scrollBy;
    fireEvent.scroll(scroller);
    fireEvent.click(await screen.findByTestId('hs-cue'));
    expect(scrollBy).toHaveBeenCalledTimes(1);
    expect((scrollBy.mock.calls[0] as [{ left: number }])[0].left).toBeGreaterThan(0);
  });
});
