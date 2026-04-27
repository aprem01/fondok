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
