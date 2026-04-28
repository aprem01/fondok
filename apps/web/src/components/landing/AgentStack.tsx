const stack = [
  {
    name: 'Claude Haiku 4.5',
    role: 'Document classifier',
    tag: 'fast',
  },
  {
    name: 'Claude Sonnet 4.6',
    role: 'Field extraction',
    tag: 'precise',
  },
  {
    name: 'Claude Opus 4.7',
    role: 'IC memo synthesis',
    tag: 'reasoning',
  },
];

export default function AgentStack() {
  return (
    <section className="border-b border-border bg-white">
      <div className="max-w-6xl mx-auto px-6 md:px-10 py-14 md:py-16">
        <div className="text-center mb-10">
          <div className="text-[11.5px] font-semibold uppercase tracking-wider text-ink-500 mb-2">
            Built on Anthropic
          </div>
          <h2 className="text-[20px] md:text-[24px] font-semibold tracking-tight text-ink-900">
            Right model for every step of the pipeline
          </h2>
        </div>
        <div className="grid md:grid-cols-3 gap-4 max-w-4xl mx-auto">
          {stack.map((m) => (
            <div
              key={m.name}
              className="rounded-lg border border-border bg-white px-5 py-4 flex items-center gap-3"
            >
              <div className="w-9 h-9 rounded-md bg-gradient-to-br from-ink-900 to-ink-700 flex items-center justify-center text-white font-semibold text-[13px] shadow-sm">
                A
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-[13px] font-semibold text-ink-900 truncate">
                  {m.name}
                </div>
                <div className="text-[11.5px] text-ink-500">{m.role}</div>
              </div>
              <span className="text-[10.5px] font-medium text-brand-700 bg-brand-50 border border-brand-500/15 px-1.5 py-0.5 rounded">
                {m.tag}
              </span>
            </div>
          ))}
        </div>
        <div className="mt-8 text-center text-[11.5px] text-ink-500">
          Prompt caching, citations, and per-agent cost telemetry on every call.
        </div>
      </div>
    </section>
  );
}
