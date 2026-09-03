'use client';

/**
 * Atlas AI Copilot (design: persistent panel, top-right of the deal view).
 *
 * A collapsible docked rail that travels with the deal across every tab. It:
 *   - knows WHAT PAGE the user is on (per-tab guidance + suggested questions),
 *   - knows WHICH DEAL (grounded on this deal via the /ask dossier backend),
 *   - stays inside its DOMAIN — hotel real-estate underwriting for this deal —
 *     and politely declines anything off-topic before it ever hits the backend.
 *
 * Q&A reuses the existing grounded ``POST /deals/{id}/ask`` (Researcher agent
 * over the deal's Context Data Product), so every answer cites source pages.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Sparkles, ChevronRight, ChevronLeft, Send, Loader2, AlertTriangle, Lightbulb,
} from 'lucide-react';
import { api, isWorkerConnected } from '@/lib/api';
import type { AskAnswerResult } from '@/lib/api';
import { Citation as CitationChip } from '@/components/citations/Citation';
import { cn } from '@/lib/format';

// Per-tab context — the "what page am I on" guidance + a few suggested
// questions/actions that are actually answerable from the deal.
const TAB_GUIDE: Record<string, { title: string; guide: string; suggestions: string[] }> = {
  '': {
    title: 'Data Room',
    guide: "The deal's document hub. Upload the OM, T-12, STR report, and broker pro forma — extraction runs automatically and feeds every downstream tab.",
    suggestions: [
      'Which documents are still missing for a complete underwrite?',
      'Are there any low-confidence fields I should review?',
      'What did we extract from the T-12?',
    ],
  },
  overview: {
    title: 'Overview',
    guide: 'The high-level snapshot — key facts, returns summary, and the capital stack. Numbers with a dotted underline trace back to their source.',
    suggestions: [
      'What are the headline returns on this deal?',
      'How does the purchase price compare to the comp set?',
      'What are the biggest risks here?',
    ],
  },
  market: {
    title: 'Market',
    guide: "STR/CoStar data — the subject's occupancy, ADR, and RevPAR vs its competitive set. Toggle 'Use STR rates in the model' to seed the forecast from live performance.",
    suggestions: [
      "How does the subject's RevPAR compare to its comp set?",
      "What's the RevPAR index (RGI) for this hotel?",
      'Is the subject gaining or losing share vs the comp set?',
    ],
  },
  pl: {
    title: 'Financials',
    guide: 'The USALI operating statement — historicals from your T-12 and the forward projections. Click any cell for its source and confidence.',
    suggestions: [
      'What is the broker NOI vs the T-12 actual NOI?',
      'Which expense lines were synthesized at USALI ratios vs lifted from the T-12?',
      'What NOI margin does the pro forma assume?',
    ],
  },
  investment: {
    title: 'Investment',
    guide: 'Sources & uses and the acquisition basis — purchase price, renovation, closing costs, and how the deal is capitalized.',
    suggestions: [
      "What's the total capitalization and the price per key?",
      'How much renovation is budgeted?',
      "What's the equity check on this deal?",
    ],
  },
  debt: {
    title: 'Debt',
    guide: 'Senior loan terms and debt-service coverage — LTV, rate, amortization, DSCR, and debt yield.',
    suggestions: [
      'What is the Year-1 DSCR and debt yield?',
      'What are the senior loan terms?',
      'Is there refinance risk at exit?',
    ],
  },
  partnership: {
    title: 'Partnership',
    guide: 'The JV waterfall — preferred return, promote tiers, and the GP/LP returns split.',
    suggestions: [
      'How is the promote structured?',
      'What are the LP and GP IRRs?',
      "What's the preferred return hurdle?",
    ],
  },
  'cash-flow': {
    title: 'Cash Flow',
    guide: 'The levered cash-flow build — NOI, debt service, and cash flow to equity across the hold.',
    suggestions: [
      "What's the Year-1 cash-on-cash?",
      'When does the deal turn cash-flow positive?',
      "What's the cash flow at exit?",
    ],
  },
  returns: {
    title: 'Returns',
    guide: 'The return summary with live assumption sliders — flex exit cap, RevPAR growth, hold, LTV and rate to watch IRR, multiple, and exit value move.',
    suggestions: [
      "What's the levered vs unlevered IRR?",
      'How sensitive is the IRR to the exit cap rate?',
      'What exit value does the model assume?',
    ],
  },
  scenarios: {
    title: 'Scenario Analysis',
    guide: 'Sensitivity grids — pick a sensitivity to see how IRR or equity multiple move across two assumptions. The base case is outlined.',
    suggestions: [
      'How sensitive is the deal to the exit cap rate?',
      'What happens to returns if RevPAR growth is 1% lower?',
      'How much does the entry basis affect the IRR?',
    ],
  },
  'ic-memo': {
    title: 'IC Memo',
    guide: "The IC-ready one-pager — deal summary, Fondok's recommendation, thesis, highlights, and risks. Configure the detail below, then export the PDF.",
    suggestions: [
      'Why does Fondok recommend proceeding?',
      'What are the key risks the IC should know?',
      'Summarize the investment thesis in three sentences.',
    ],
  },
};

const DEFAULT_GUIDE = {
  title: 'Atlas',
  guide: 'Your underwriting copilot for this deal. Ask about the property, financials, returns, market, or risks — every answer is grounded in this deal and cites its sources.',
  suggestions: [
    'What are the headline returns on this deal?',
    'What are the biggest risks here?',
    'How does the purchase price compare to the comp set?',
  ],
};

// Domain guard. The real relevance judgment happens server-side: the grounded
// Researcher agent gets this deal's dossier plus an explicit instruction to
// answer only deal / hotel-underwriting questions and otherwise refuse. This
// client denylist is just a fast pre-filter for the obviously off-topic, so we
// never spend a round-trip on "write me a poem". We deliberately do NOT gate on
// an allowlist of domain words — that false-declines legitimate but plainly
// phrased questions ("should we proceed to IC?", "is this a good opportunity?").
const OFF_TOPIC = [
  'weather', 'poem', 'haiku', 'joke', 'recipe', 'cook', 'python', 'javascript',
  ' code', 'coding', 'programming', 'president', 'election', 'politic', 'football',
  'soccer', 'basketball', 'world cup', 'movie', 'film', 'song', 'lyric', 'celebrity',
  'horoscope', 'translate', 'dating', 'stock price', 'crypto', 'bitcoin',
];

function isObviouslyOffTopic(q: string): boolean {
  const s = ` ${q.toLowerCase()} `;
  return OFF_TOPIC.some((t) => s.includes(t));
}

export default function AtlasCopilot({
  dealId,
  activeTab,
  activeLabel,
}: {
  dealId: string;
  activeTab: string;
  activeLabel: string;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<AskAnswerResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offDomain, setOffDomain] = useState(false);

  // Per-viewer collapse preference (survives tab switches + reloads).
  useEffect(() => {
    try {
      const v = localStorage.getItem('atlas-collapsed');
      if (v != null) setCollapsed(v === '1');
    } catch {
      /* private mode / blocked storage — default open */
    }
  }, []);
  const toggle = () =>
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem('atlas-collapsed', next ? '1' : '0');
      } catch {
        /* ignore */
      }
      return next;
    });

  const liveDeal = isWorkerConnected() && /^[0-9a-f-]{36}$/i.test(dealId);
  const guide = useMemo(() => TAB_GUIDE[activeTab] ?? DEFAULT_GUIDE, [activeTab]);

  const ask = async (raw: string) => {
    const q = raw.trim();
    if (!q || asking) return;
    setError(null);
    setOffDomain(false);
    setAnswer(null);

    // Fast client pre-filter — obviously off-topic never leaves the browser.
    if (isObviouslyOffTopic(q)) {
      setOffDomain(true);
      setQuestion(q);
      return;
    }
    if (!liveDeal) {
      setError('Q&A is available on real, worker-connected deals (not the demo deal).');
      return;
    }
    setAsking(true);
    try {
      // Ground the agent with page + domain context and instruct it to stay in
      // its lane — this is the real relevance guard for anything the client
      // pre-filter lets through.
      const contextualized =
        `You are Atlas, a hotel real-estate underwriting copilot for this specific deal. ` +
        `The user is viewing the "${activeLabel}" tab. Answer only if the question relates to ` +
        `this deal or hotel real-estate underwriting; if it is unrelated, reply that you can ` +
        `only help with this deal and hotel underwriting. Question: ${q}`;
      const result = await api.dossier.ask(dealId, contextualized);
      // Display the user's own question, not the contextualized wrapper.
      setAnswer({ ...result, question: q });
      setQuestion('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAsking(false);
    }
  };

  // ── Collapsed: a slim strip pinned right, always accessible ──────────────
  // Chrome maps to the canonical Atlas accent: navy (#14213d) sparkle tile +
  // light-blue (#eef2fb / #dbe3f5) hover, mirroring the design's docked rail.
  // `fondok-*` Tailwind classes are the class-mirror of components/design tokens.
  if (collapsed) {
    return (
      <div className="sticky top-6 self-start shrink-0 ml-2">
        <button
          type="button"
          onClick={toggle}
          aria-label="Open Atlas AI Copilot"
          className="group flex flex-col items-center gap-2 w-11 py-3 rounded-[10px] border border-fondok-border bg-white hover:bg-[#eef2fb] hover:border-[#dbe3f5] transition-colors shadow-sm"
        >
          <span className="h-6 w-6 rounded-md bg-fondok-navy flex items-center justify-center shrink-0">
            <Sparkles size={13} className="text-white" />
          </span>
          <span
            className="text-[10px] font-semibold tracking-[0.14em] text-fondok-link"
            style={{ writingMode: 'vertical-rl' }}
          >
            ATLAS
          </span>
          <ChevronLeft size={13} className="text-fondok-text-muted" />
        </button>
      </div>
    );
  }

  // ── Expanded: the docked panel ───────────────────────────────────────────
  // Chrome extracted from the canonical Atlas panel (design/canonical/*.dc.html):
  // 340px card, white header with a navy (#14213d) sparkle tile + "AI Underwriting
  // Copilot" subtitle, light-blue (#eef2fb / #dbe3f5 / #2f4a8c) context + suggested
  // chips, warm answer bubble, and a navy Send button. `fondok-*` Tailwind classes
  // mirror components/design tokens (navy = #14213d, link = #2f4a8c, border = #eae9e4).
  return (
    <aside className="sticky top-6 self-start shrink-0 w-[340px] ml-4">
      <div className="flex flex-col max-h-[calc(100vh-3rem)] rounded-[10px] border border-fondok-border bg-white shadow-sm overflow-hidden">
        {/* Header — white with a navy sparkle tile (canonical Atlas header). */}
        <div className="flex items-center justify-between px-[18px] py-4 bg-white border-b border-fondok-border shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <span className="shrink-0 h-[30px] w-[30px] rounded-lg bg-fondok-navy flex items-center justify-center">
              <Sparkles size={15} className="text-white" />
            </span>
            <div className="min-w-0">
              <div className="text-[13.5px] font-bold text-fondok-ink leading-tight">Atlas</div>
              <div className="text-[11px] text-fondok-text-muted leading-tight">
                AI Underwriting Copilot
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={toggle}
            aria-label="Collapse Atlas"
            className="text-fondok-text-muted hover:text-fondok-ink p-0.5 rounded"
          >
            <ChevronRight size={16} />
          </button>
        </div>

        {/* Scrollable body */}
        <div className="flex-1 overflow-y-auto px-[18px] py-4 space-y-3.5">
          {/* Page context — light-blue Atlas info card. */}
          <div className="rounded-lg bg-[#eef2fb] border border-[#dbe3f5] px-3 py-2.5">
            <div className="text-[10px] uppercase tracking-wide font-semibold text-fondok-link mb-1">
              You&apos;re on · {guide.title}
            </div>
            <p className="text-[12.5px] text-fondok-ink leading-relaxed">{guide.guide}</p>
          </div>

          {/* Suggested questions — light-blue Atlas chips. */}
          <div>
            <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide font-semibold text-fondok-text-muted mb-1.5">
              <Lightbulb size={11} /> Suggested
            </div>
            <div className="flex flex-col gap-1.5">
              {guide.suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => ask(s)}
                  disabled={asking}
                  className="text-left text-[11.5px] leading-snug px-2.5 py-1.5 rounded-lg bg-[#eef2fb] border border-[#dbe3f5] text-fondok-link hover:bg-[#e3eaf9] hover:border-[#c9d5ef] disabled:opacity-50 transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          {/* Off-domain refusal */}
          {offDomain && (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2.5 text-[11.5px] text-amber-900 leading-relaxed">
              Atlas only answers questions about <span className="font-medium">this deal</span> and
              hotel underwriting — the property, financials, market, returns, debt, or risks. Try a
              suggested question above.
            </div>
          )}

          {/* Error */}
          {error && (
            <div className="inline-flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11.5px] text-amber-900">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Latest answer — warm Atlas bubble (canonical #f3f2ee). */}
          {answer && (
            <div className="rounded-[10px] border border-fondok-border bg-[#f3f2ee] p-3">
              <div className="text-[10px] uppercase tracking-wide text-fondok-text-muted mb-0.5">Question</div>
              <div className="text-[12px] font-medium text-fondok-ink mb-2">{answer.question}</div>
              <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-fondok-text-muted mb-1">
                <Sparkles size={10} className="text-fondok-link" /> Answer
                <span
                  className={cn(
                    'ml-auto normal-case tracking-normal px-1.5 py-0.5 rounded text-[10px] font-medium',
                    answer.confidence >= 0.8
                      ? 'bg-success-50 text-success-700'
                      : answer.confidence >= 0.5
                        ? 'bg-warn-50 text-warn-700'
                        : 'bg-ink-100 text-ink-500',
                  )}
                >
                  {(answer.confidence * 100).toFixed(0)}% conf.
                </span>
              </div>
              <div className="text-[12px] text-fondok-ink leading-relaxed whitespace-pre-wrap">
                {answer.answer || (
                  <span className="text-fondok-text-muted italic">{answer.note ?? 'No answer returned.'}</span>
                )}
              </div>
              {answer.citations.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1 items-baseline">
                  <span className="text-[9.5px] uppercase tracking-wide text-fondok-text-muted">Sources:</span>
                  {answer.citations.map((c, i) => (
                    <CitationChip
                      key={`${c.document_id ?? 'unknown'}:${c.page ?? 0}:${i}`}
                      data={{
                        documentId: c.document_id ?? '',
                        page: c.page ?? 1,
                        field: c.field ?? undefined,
                        excerpt: c.excerpt ?? undefined,
                      }}
                      label={`p.${c.page ?? '—'}`}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Ask input */}
        <div className="border-t border-fondok-border p-3 shrink-0">
          <div className="flex items-end gap-2">
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  ask(question);
                }
              }}
              placeholder="Ask Atlas about this deal…"
              rows={2}
              className="flex-1 text-[12.5px] px-2.5 py-2 border border-fondok-border rounded-md resize-none text-fondok-ink placeholder:text-fondok-text-faint focus:outline-none focus:ring-2 focus:ring-fondok-navy/30"
            />
            <button
              type="button"
              onClick={() => ask(question)}
              disabled={!question.trim() || asking}
              aria-label="Ask Atlas"
              className="shrink-0 h-9 w-9 flex items-center justify-center rounded-md bg-fondok-navy text-white hover:bg-fondok-navy-sidebar disabled:opacity-40 transition-colors"
            >
              {asking ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            </button>
          </div>
          <div className="text-[10px] text-fondok-text-muted mt-1.5">
            Grounded on this deal · answers cite source pages
          </div>
        </div>
      </div>
    </aside>
  );
}
