# Fondok Demo Script — 7 minutes

> Audience: institutional hotel investor or LP scout. Persona on screen: **Eshan Mehta — Senior Analyst at Brookfield Real Estate (Pro Plan)**. Demo deal: **Kimpton Angler — Miami**.
>
> Live URL: https://fondok-app.vercel.app/

---

## 0:00–1:00 — Landing → Dashboard

- Open https://fondok-app.vercel.app/
- One sentence: *"Fondok takes a folder of broker materials and produces an IC-ready memo in 17 minutes — with every number cited back to the source page."*
- Click **Try the demo** → lands on the Dashboard.
- Point out the portfolio overview: pipeline value, deals in progress, deals closed.
- Hover the Kimpton Angler row.

## 1:00–2:30 — Open Kimpton Angler → Data Room

- Click into **Kimpton Angler — Miami**.
- Default tab is **Data Room**.
- *"This is what the analyst sees the morning after they drop the broker package in. Eight documents — OM, T-12, STR, term sheet, comp set, env reports — already classified and extracted."*
- Click into the OM row to show extracted fields with confidence scores and the **citation chip** that opens the source page.
- Key talking point: *"No copy-paste. No 'go check the OM again'. The number you see in the model is the number Fondok pulled, with a link back to the page it came from."*

## 2:30–4:00 — Analysis tab → Variance

- Click **Analysis** tab → **Variance** sub-tab.
- Headline finding: **"Broker NOI overstated by $1.0M"**.
- Click into the row to expand: shows broker's stabilized NOI, Fondok's underwritten NOI, the delta, and the line items driving it (typically: optimistic ADR ramp + understated payroll inflation).
- *"The variance agent doesn't just compute the number — it tells the analyst where to push back in the term-sheet negotiation."*

## 4:00–5:30 — Returns tab → live IRR slider

- Click **Returns** tab.
- Show the IRR / EM / CoC at the underwritten case.
- Drag the **IRR slider** (or the exit cap, hold period, or leverage slider) — *"Live recompute. The whole returns waterfall — partnership splits, debt service, residual — recalculates client-side. No round-trip."*
- *"This is what makes Fondok different from a static memo. The analyst can stress-test in front of the IC, not just hand over a deck."*

## 5:30–6:30 — Generate IC Memo (live SSE stream)

- Click **Generate IC Memo**.
- Watch the memo stream in section by section: Executive Summary → Investment Thesis → Market → Returns → Risks → Recommendation.
- *"Six sections, every claim cited. Streaming because Opus 4.7 takes ~90 seconds end-to-end and we don't want the analyst staring at a spinner."*
- Don't wait for it to finish — let it run in the background while you transition.

## 6:30–7:00 — Excel export → close

- Click **Download Excel model**.
- File streams from the worker; opens in Excel/Numbers with all engines wired live.
- *"This is the same model an associate would build in three days. Fondok built it in seventeen minutes — and re-builds it the moment the broker sends a revised T-12."*
- Land the close: *"From OM to IC-ready in 17 minutes, not 17 days. Want to see it on a deal you're working?"*

---

## "If asked" answers

**"How does it handle accuracy?"**
Two layers: the engines are 100% deterministic — pure Python, no LLM in the math path. The extraction layer uses Claude Sonnet 4.6 with cited outputs and confidence scores; the analyst sees and approves every field before it enters the engines. Drift caught by a golden-set regression suite that runs on every PR.

**"What about security / SOC 2?"**
Single-tenant per workspace, Postgres on Railway (encrypted at rest, in transit), Clerk for auth, Anthropic processes inputs but doesn't train on them. SOC 2 Type I targeted Q3.

**"What's the architecture?"**
Next.js 14 on Vercel, FastAPI on Railway, Postgres + Redis, Anthropic for LLM (Haiku for routing, Sonnet for extraction, Opus for memo synthesis). Multi-agent orchestration via LangGraph. Full diagram in `docs/ARCHITECTURE.md`.

**"What does it cost?"**
Pricing is per-deal underwritten (not per-seat). Pilot is free; production target is in line with what an analyst-day costs the firm. Specifics on a follow-up call.

**"Who else is using it?"**
We're in design partnership with one institutional shop and three boutique hotel sponsors. Compete with Zed Truong on the multifamily side; he uses an email-agent workflow, we use a doc-extraction workflow — different surfaces.

**"What if the broker package is messy / handwritten / scanned?"**
The classifier rejects unparseable docs and flags them for the analyst. We don't fake it — empty fields stay empty, with a "needs human review" badge.

**"How long until I can run a deal through it?"**
Same day for the demo flow. For your own deal data behind your own auth: a one-week pilot setup.
