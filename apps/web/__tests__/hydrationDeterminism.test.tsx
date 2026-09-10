/**
 * Hydration determinism — the deal page (and everything it SSRs) must render
 * byte-identical HTML on the server and on the client's first render.
 *
 * Production logged React #418 ×3 + #423 (whole-root client fallback) on every
 * /projects/[id] load. Two classes of non-determinism are locked here:
 *
 *  1. DATES. ``new Date(iso).toLocaleDateString()`` depends on the process
 *     time zone + locale (UTC on the server, the analyst's zone in the
 *     browser). ``fmtDate`` / ``fmtDateTime`` pin en-US + UTC, so a server in
 *     one zone and a client in another produce the same string. Proven by
 *     rendering under TZ=Pacific/Kiritimati (UTC+14) and TZ=America/New_York
 *     (UTC−4) — a near-midnight timestamp falls on DIFFERENT calendar days in
 *     those zones, so the naive formatter demonstrably diverges while the
 *     deterministic one does not.
 *
 *  2. CLIENT-ONLY STATE. Relative "3m ago" labels (Date.now()) and
 *     localStorage-driven state can only be known in the browser, so they
 *     render after mount (``useMounted``) — the server HTML never carries them.
 */
import { describe, it, expect, afterEach, vi } from 'vitest';
import React from 'react';
import { renderToString } from 'react-dom/server';
import { fmtDate, fmtDateTime } from '@/lib/format';
import { useMounted } from '@/lib/hooks/useMounted';

const ORIGINAL_TZ = process.env.TZ;
const ZONES = ['Pacific/Kiritimati', 'America/New_York'] as const;

// Node re-reads TZ when process.env.TZ is assigned (v13+), so a test can run
// the same render under two zones in one process.
function withTz<T>(tz: string, fn: () => T): T {
  process.env.TZ = tz;
  try {
    return fn();
  } finally {
    if (ORIGINAL_TZ === undefined) delete process.env.TZ;
    else process.env.TZ = ORIGINAL_TZ;
  }
}

// 03:30 UTC → 17:30 the SAME day in Kiritimati, 23:30 the PREVIOUS day in New York.
const NEAR_MIDNIGHT_ISO = '2026-09-09T03:30:00Z';

/** The shape the deal header renders: "Created {date}" + a docs date cell. */
function DealMeta({ createdAt, uploadedAt }: { createdAt: string; uploadedAt: string }) {
  return (
    <div>
      <span>Created {fmtDate(createdAt)}</span>
      <span>{fmtDate(uploadedAt)}</span>
      <span>{fmtDateTime(uploadedAt)}</span>
    </div>
  );
}

/** The naive shape the page used to render — kept only to prove the test bites. */
function NaiveDealMeta({ createdAt }: { createdAt: string }) {
  return <span>Created {new Date(createdAt).toLocaleDateString()}</span>;
}

/** Relative-time label pattern: nothing on the server, "Xs ago" after mount. */
function LastRun({ at }: { at: number }) {
  const mounted = useMounted();
  if (!mounted) return <span>Engine · Complete</span>;
  return <span>Engine · Complete · Last run {Math.floor((Date.now() - at) / 1000)}s ago</span>;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('fmtDate / fmtDateTime are time-zone independent', () => {
  it('the test harness really switches zones (sanity)', () => {
    const hours = ZONES.map((tz) => withTz(tz, () => new Date(NEAR_MIDNIGHT_ISO).getHours()));
    expect(hours[0]).not.toBe(hours[1]);
  });

  it('the naive toLocaleDateString() diverges across zones — the bug being fixed', () => {
    const naive = ZONES.map((tz) => withTz(tz, () => new Date(NEAR_MIDNIGHT_ISO).toLocaleDateString()));
    expect(naive[0]).not.toBe(naive[1]);
  });

  it('fmtDate renders the same UTC calendar date in every zone', () => {
    const out = ZONES.map((tz) => withTz(tz, () => fmtDate(NEAR_MIDNIGHT_ISO)));
    expect(out[0]).toBe(out[1]);
    expect(out[0]).toBe('9/9/2026');
  });

  it('fmtDateTime renders the same UTC stamp in every zone', () => {
    const out = ZONES.map((tz) => withTz(tz, () => fmtDateTime(NEAR_MIDNIGHT_ISO)));
    expect(out[0]).toBe(out[1]);
    expect(out[0]).toMatch(/^9\/9\/2026, 3:30 AM UTC$/);
  });

  it('renders "—" for missing / unparseable input, never a fabricated date', () => {
    expect(fmtDate(null)).toBe('—');
    expect(fmtDate(undefined)).toBe('—');
    expect(fmtDate('')).toBe('—');
    expect(fmtDate('not a date')).toBe('—');
    expect(fmtDateTime('garbage')).toBe('—');
  });
});

describe('server render (renderToString) is identical across zones', () => {
  it('deal header dates: Pacific/Kiritimati and America/New_York produce the same HTML', () => {
    const html = ZONES.map((tz) =>
      withTz(tz, () =>
        renderToString(<DealMeta createdAt={NEAR_MIDNIGHT_ISO} uploadedAt={NEAR_MIDNIGHT_ISO} />),
      ),
    );
    expect(html[0]).toBe(html[1]);
    // (React separates adjacent text nodes with `<!-- -->` in SSR output.)
    expect(html[0]).toMatch(/Created (<!-- -->)?9\/9\/2026/);
    expect(html[0]).toContain('9/9/2026, 3:30 AM UTC');
  });

  it('…while the naive formatter produced different HTML per zone (regression guard)', () => {
    const html = ZONES.map((tz) =>
      withTz(tz, () => renderToString(<NaiveDealMeta createdAt={NEAR_MIDNIGHT_ISO} />)),
    );
    expect(html[0]).not.toBe(html[1]);
  });
});

describe('client-only relative time renders after mount, never in server HTML', () => {
  it('renderToString output does not depend on Date.now()', () => {
    const at = 1_800_000_000_000;
    const nowSpy = vi.spyOn(Date, 'now');
    nowSpy.mockReturnValue(at + 5_000);
    const a = renderToString(<LastRun at={at} />);
    nowSpy.mockReturnValue(at + 3_600_000);
    const b = renderToString(<LastRun at={at} />);
    expect(a).toBe(b);
    expect(a).not.toContain('ago');
  });
});
