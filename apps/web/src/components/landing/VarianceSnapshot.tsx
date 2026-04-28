import { TrendingDown, FileWarning } from 'lucide-react';

// Bars represent: Broker NOI vs. Fondok-underwritten NOI for one sample line item.
// Numbers mirror the canned story used elsewhere in the demo so the landing
// stays internally consistent.
const lines = [
  { label: 'Rooms revenue', broker: 100, ours: 96, gap: '-4%' },
  { label: 'F&B revenue', broker: 100, ours: 88, gap: '-12%' },
  { label: 'Property tax expense', broker: 100, ours: 118, gap: '+18%' },
  { label: 'Management fees', broker: 100, ours: 112, gap: '+12%' },
];

export default function VarianceSnapshot() {
  return (
    <section className="border-b border-border bg-bg">
      <div className="max-w-6xl mx-auto px-6 md:px-10 py-16 md:py-20">
        <div className="grid md:grid-cols-5 gap-8 items-start">
          <div className="md:col-span-2">
            <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-danger-50 border border-danger-500/20 text-[11px] font-semibold uppercase tracking-wider text-danger-700 mb-3">
              <FileWarning size={11} aria-hidden="true" />
              Variance detected
            </div>
            <h2 className="text-[26px] md:text-[30px] font-semibold tracking-tight text-ink-900 leading-[1.15]">
              Broker NOI overstated by{' '}
              <span className="text-danger-700">$1.0M (19.6%)</span>
            </h2>
            <p className="mt-3 text-[13.5px] text-ink-700 leading-relaxed">
              Fondok cross-checks the broker&apos;s underwriting against its
              own engine output and flags material variances by line item —
              with a citation back to the source document.
            </p>
            <div className="mt-5 flex items-center gap-3 text-[12px] text-ink-500">
              <span className="flex items-center gap-1.5 text-danger-700 font-medium">
                <TrendingDown size={13} aria-hidden="true" />
                Broker NOI: $5.1M
              </span>
              <span className="text-ink-300">·</span>
              <span className="font-medium text-ink-900">Fondok NOI: $4.1M</span>
            </div>
          </div>
          <div className="md:col-span-3">
            <div className="rounded-xl border border-border bg-white p-6 shadow-card">
              <div className="flex items-center justify-between text-[11.5px] text-ink-500 mb-5">
                <span className="font-medium">Line-item variance</span>
                <div className="flex items-center gap-3">
                  <span className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-sm bg-ink-300" />
                    Broker
                  </span>
                  <span className="flex items-center gap-1.5">
                    <span className="w-2 h-2 rounded-sm bg-brand-500" />
                    Fondok
                  </span>
                </div>
              </div>
              <div className="space-y-4">
                {lines.map((l) => (
                  <div key={l.label}>
                    <div className="flex items-center justify-between text-[12.5px] mb-1.5">
                      <span className="text-ink-900 font-medium">{l.label}</span>
                      <span
                        className={
                          l.gap.startsWith('+')
                            ? 'text-warn-700 font-semibold tabular-nums'
                            : 'text-danger-700 font-semibold tabular-nums'
                        }
                      >
                        {l.gap}
                      </span>
                    </div>
                    <div className="space-y-1">
                      <div className="h-2 rounded-sm bg-ink-300/30 overflow-hidden">
                        <div
                          className="h-full bg-ink-300"
                          style={{ width: `${Math.min(100, l.broker)}%` }}
                        />
                      </div>
                      <div className="h-2 rounded-sm bg-brand-50 overflow-hidden">
                        <div
                          className="h-full bg-brand-500"
                          style={{ width: `${Math.min(100, l.ours)}%` }}
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
              <div className="mt-5 pt-4 border-t border-border text-[11px] text-ink-500">
                Sample: Kimpton Angler · Miami Beach · T-12 normalized
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
