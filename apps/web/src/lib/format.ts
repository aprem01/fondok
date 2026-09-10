export const fmtCurrency = (n: number, opts: { compact?: boolean } = {}) => {
  if (opts.compact) {
    if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
    if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(0)}K`;
    return `$${n.toFixed(0)}`;
  }
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
};

export const fmtPct = (n: number, decimals = 1) => `${(n * 100).toFixed(decimals)}%`;
export const fmtPctRaw = (n: number, decimals = 1) => `${n.toFixed(decimals)}%`;
export const fmtNumber = (n: number) => n.toLocaleString('en-US');
export const fmtMillions = (n: number, decimals = 1) => `$${(n / 1e6).toFixed(decimals)}M`;
export const fmtThousands = (n: number) => `$${(n / 1e3).toFixed(0)}K`;
export const cn = (...classes: (string | false | null | undefined)[]) => classes.filter(Boolean).join(' ');

// ─── Deterministic date formatting (SSR-safe) ────────────────────────────────
//
// `new Date(iso).toLocaleDateString()` renders differently on the server
// (UTC, server locale) and in the browser (user TZ + locale) — a React
// hydration mismatch (#418 → #423 root fallback in production) on any page
// that renders a timestamp during SSR. These helpers pin locale + zone so the
// server HTML and the client's first render are byte-identical. Dates render
// as UTC calendar dates: a deal's ``created_at`` / ``updated_at`` is an audit
// stamp, not a local appointment, so the zone pin is honest. Anything that is
// inherently client-only (relative "3m ago", localStorage state) must render
// after mount instead — see ``lib/hooks/useMounted``.

const DATE_FMT = new Intl.DateTimeFormat('en-US', {
  month: 'numeric',
  day: 'numeric',
  year: 'numeric',
  timeZone: 'UTC',
});
const DATE_TIME_FMT = new Intl.DateTimeFormat('en-US', {
  month: 'numeric',
  day: 'numeric',
  year: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
  timeZone: 'UTC',
  timeZoneName: 'short',
});

function toDate(v: string | number | Date | null | undefined): Date | null {
  if (v == null || v === '') return null;
  const d = v instanceof Date ? v : new Date(v);
  return Number.isFinite(d.getTime()) ? d : null;
}

/** ``M/D/YYYY`` (UTC, en-US) — identical on server and client; ``—`` when unparseable. */
export function fmtDate(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  return d ? DATE_FMT.format(d) : '—';
}

/** ``M/D/YYYY, h:mm AM UTC`` — deterministic replacement for ``toLocaleString()``. */
export function fmtDateTime(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  return d ? DATE_TIME_FMT.format(d) : '—';
}

/**
 * Format one extracted field value using its unit + field name — occupancy /
 * *_pct / margins render as percent, USD renders $/K/M, ratios/percent convert,
 * period_type enums humanize. Shared by the Data Room and the document-detail
 * screen so extracted values read consistently.
 */
export function formatValue(v: unknown, unit: string | null, fieldName?: string): string {
  if (v == null) return '—';
  const fn = (fieldName ?? '').toLowerCase();
  if (typeof v === 'string') {
    if (fn.endsWith('period_type')) {
      return v.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }
    return v;
  }
  if (typeof v === 'number') {
    const pctField =
      fn.includes('occupancy') ||
      fn.endsWith('_pct') ||
      fn.endsWith('margin') ||
      ((fn.includes('gop') || fn.includes('noi')) && Math.abs(v) <= 1.5);
    if (pctField && Math.abs(v) <= 1.5) {
      return `${(v * 100).toFixed(1)}%`;
    }
    if (unit === 'USD') {
      const abs = Math.abs(v);
      if (abs >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
      if (abs >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
      return `$${v.toFixed(0)}`;
    }
    if (unit === 'ratio' || unit === 'percent') {
      return `${(v * (unit === 'percent' ? 1 : 100)).toFixed(1)}%`;
    }
    return v.toLocaleString();
  }
  return String(v);
}
