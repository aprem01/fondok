import { Workflow, Hotel, LineChart } from 'lucide-react';

const features = [
  {
    icon: Workflow,
    title: 'Decision infrastructure',
    body:
      'Multi-agent pipeline routes each task to the right model — Haiku for classification, Sonnet for extraction, Opus for memo synthesis. Every step is observable, auditable, and costed.',
  },
  {
    icon: Hotel,
    title: 'Hotel-native domain',
    body:
      'Purpose-built for hotel acquisitions: T-12 normalization, brand-aware PIP scoping, STR comp sets, and brand/positioning logic baked into the underwriting engines.',
  },
  {
    icon: LineChart,
    title: 'Live IRR + variance detection',
    body:
      'Engines recompute IRR, EM, and CoC in real time as assumptions change. Variance detection flags when broker NOI overstates or understates the underwritten case.',
  },
];

export default function Features() {
  return (
    <section className="border-b border-border bg-white">
      <div className="max-w-6xl mx-auto px-6 md:px-10 py-16 md:py-20">
        <div className="max-w-2xl mb-12">
          <div className="text-[11.5px] font-semibold uppercase tracking-wider text-brand-700 mb-2">
            Why Fondok
          </div>
          <h2 className="text-[26px] md:text-[32px] font-semibold tracking-tight text-ink-900">
            Underwriting infrastructure, not a chatbot.
          </h2>
          <p className="mt-3 text-[14.5px] text-ink-700 leading-relaxed">
            Three engines purpose-built for the hotel acquisition workflow. Every
            output ships with citations to source documents and a confidence
            score.
          </p>
        </div>
        <div className="grid md:grid-cols-3 gap-5">
          {features.map((f) => {
            const Icon = f.icon;
            return (
              <div
                key={f.title}
                className="rounded-xl border border-border bg-white p-6 hover:shadow-card transition-shadow"
              >
                <div className="w-10 h-10 rounded-lg bg-brand-50 border border-brand-500/15 flex items-center justify-center mb-4">
                  <Icon size={18} className="text-brand-600" aria-hidden="true" />
                </div>
                <div className="text-[15px] font-semibold text-ink-900 mb-1.5">
                  {f.title}
                </div>
                <p className="text-[13px] text-ink-700 leading-relaxed">{f.body}</p>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
