/**
 * <Refused> — the one honest way the app says "no value" (Phase 4.4).
 *
 * Contracts locked here:
 *
 *  1. EVERY CODE SPEAKS. All 16 `ReasonCode`s render the refusal glyph and,
 *     on hover, the ontology's own label AND explanation — never a phrasing
 *     invented at the call site.
 *
 *  2. NO REASON → NO CHANGE. A refusal the worker cannot explain renders the
 *     glyph and NOTHING else: the markup is byte-identical to the bare `'—'`
 *     literal it replaces (snapshot-compared below), so an unwired row keeps
 *     exactly the DOM, width and accessible name it has today. This is the
 *     drop-in guarantee — an external tester mid-QA must see no difference.
 *
 *  3. THE VOCABULARY IS CLOSED. A string that is not a known code degrades to
 *     the bare glyph rather than rendering an empty tooltip, so a newer worker
 *     can never make this build show a blank explanation.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import React from 'react';

import { Refused, useRefusal, reasonFor, asReasonCode } from '@/components/help/Refused';
import {
  REASONS,
  REASON_CODES,
  REFUSAL_GLYPH,
  type ReasonCode,
} from '@/lib/ontology/reasons.generated';

beforeEach(() => cleanup());
afterEach(() => vi.useRealTimers());

/** Hover the trigger and let the Tooltip's 250ms show-delay elapse. */
function hover(el: HTMLElement) {
  fireEvent.mouseEnter(el);
  act(() => {
    vi.advanceTimersByTime(400);
  });
}

describe('Refused — every reason code renders the glyph and speaks its reason', () => {
  it.each(REASON_CODES as readonly ReasonCode[])(
    '%s: glyph + the ontology label and explanation on hover',
    (code) => {
      vi.useFakeTimers();
      const { container } = render(<Refused reason={code} />);

      // The glyph — and only the glyph — is what the row shows.
      const trigger = container.querySelector('[data-refused]') as HTMLElement;
      expect(trigger).toBeTruthy();
      expect(trigger.getAttribute('data-refused')).toBe(code);
      expect(trigger.textContent).toBe(REFUSAL_GLYPH);

      hover(trigger);

      const tip = screen.getByRole('tooltip');
      expect(tip).toHaveTextContent(REASONS[code].label);
      expect(tip).toHaveTextContent(REASONS[code].explanation);
      cleanup();
    },
  );

  it('appends the worker\'s case-specific detail under the ontology explanation', () => {
    vi.useFakeTimers();
    const { container } = render(
      <Refused reason="not_knowable_as_of" detail="The STR report is dated 2026-03-31." />,
    );
    hover(container.querySelector('[data-refused]') as HTMLElement);
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent(REASONS.not_knowable_as_of.label);
    expect(tip).toHaveTextContent(REASONS.not_knowable_as_of.explanation);
    expect(tip).toHaveTextContent('The STR report is dated 2026-03-31.');
  });

  it('renders a caller-supplied label in place of the glyph, keeping the same tooltip', () => {
    vi.useFakeTimers();
    const { container } = render(
      <Refused reason="awaiting_analyst">Pending analyst decision</Refused>,
    );
    const trigger = container.querySelector('[data-refused]') as HTMLElement;
    expect(trigger.textContent).toBe('Pending analyst decision');
    hover(trigger);
    expect(screen.getByRole('tooltip')).toHaveTextContent(REASONS.awaiting_analyst.explanation);
  });

  it('carries the reason as the accessible name and does not take a tab stop by default', () => {
    const { container } = render(<Refused reason="pin_active" />);
    const trigger = container.querySelector('[data-refused]') as HTMLElement;
    expect(trigger.getAttribute('aria-label')).toBe(REASONS.pin_active.label);
    expect(trigger.hasAttribute('tabindex')).toBe(false);

    cleanup();
    const opted = render(<Refused reason="pin_active" focusable />).container;
    expect((opted.querySelector('[data-refused]') as HTMLElement).getAttribute('tabindex')).toBe('0');
  });
});

describe('Refused — with no knowable reason the markup is exactly a bare dash', () => {
  it('renders byte-identical markup to the `\'—\'` literal it replaces', () => {
    // The row a worker has said nothing about.
    const refused = render(
      <span data-probe="x">
        <Refused reason={null} />
      </span>,
    ).container.innerHTML;
    cleanup();
    // The same row before Phase 4.4 touched it.
    const bare = render(<span data-probe="x">{'—'}</span>).container.innerHTML;

    expect(refused).toBe(bare);
    expect(refused).toBe('<span data-probe="x">—</span>');
  });

  it('adds no element, no handlers and no attributes when the reason is undefined', () => {
    const { container } = render(<Refused />);
    expect(container.innerHTML).toBe(REFUSAL_GLYPH);
    expect(container.querySelector('[data-refused]')).toBeNull();
  });

  it('degrades an unknown code from a newer worker to the bare glyph', () => {
    const { container } = render(<Refused reason={'brand_new_code' as ReasonCode} />);
    expect(container.innerHTML).toBe(REFUSAL_GLYPH);
    expect(container.querySelector('[data-refused]')).toBeNull();
  });

  it('keeps a caller-supplied label untouched when there is no reason', () => {
    const { container } = render(<Refused reason={null}>Pending analyst decision</Refused>);
    expect(container.innerHTML).toBe('Pending analyst decision');
  });

  it('never opens a tooltip without a reason', () => {
    vi.useFakeTimers();
    const { container } = render(
      <span data-probe="x">
        <Refused reason={null} />
      </span>,
    );
    hover(container.querySelector('[data-probe]') as HTMLElement);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });
});

describe('reasonFor / asReasonCode — resolving a key against a reasons map', () => {
  it('resolves a bare ReasonCode string keyed by assumption key', () => {
    const reasons = { ffe_reserve_pct: 'no_document', ltv: 'no_source' };
    expect(reasonFor(reasons, 'ffe_reserve_pct')).toBe('no_document');
    expect(reasonFor(reasons, 'ltv')).toBe('no_source');
  });

  it('returns null for an unknown key, an unknown code, or no map at all', () => {
    expect(reasonFor({ ltv: 'no_source' }, 'exit_cap_rate')).toBeNull();
    expect(reasonFor({ ltv: 'not_a_code' }, 'ltv')).toBeNull();
    expect(reasonFor(undefined, 'ltv')).toBeNull();
    expect(reasonFor({ ltv: 'no_source' }, undefined)).toBeNull();
  });

  it('tolerates the runner\'s richer {code, detail} entry', () => {
    expect(asReasonCode({ code: 'str_unavailable', detail: 'coverage too low' })).toBe(
      'str_unavailable',
    );
    expect(asReasonCode({ code: 'nonsense' })).toBeNull();
    expect(asReasonCode(null)).toBeNull();
  });
});

describe('useRefusal — a key resolves to a code without a provider', () => {
  function Probe({ k }: { k: string }) {
    const reason = useRefusal(k);
    return <Refused reason={reason} testId="probe" />;
  }

  it('renders a bare dash when there is no ProvenanceProvider above it', () => {
    const { container } = render(<Probe k="ffe_reserve_pct" />);
    expect(container.innerHTML).toBe(REFUSAL_GLYPH);
  });

  it('reads the reason off the deal provenance map when one is supplied', async () => {
    vi.resetModules();
    vi.doMock('@/lib/hooks/useDealProvenance', () => ({
      useSource: (key: string | undefined) =>
        key === 'ffe_reserve_pct' ? { source: 'seed', value: null, reason: 'no_document' } : null,
    }));
    const mod = await import('@/components/help/Refused');
    function Live() {
      const reason = mod.useRefusal('ffe_reserve_pct');
      return <mod.Refused reason={reason} testId="live" />;
    }
    render(<Live />);
    expect(screen.getByTestId('live').getAttribute('data-refused')).toBe('no_document');
    vi.doUnmock('@/lib/hooks/useDealProvenance');
    vi.resetModules();
  });
});
