# Email draft — open questions for Sam + Eshan

**To:** Sam, Eshan
**Subject:** Fondok roadmap — 9 product decisions I need before we sprint

---

Hey both,

Two things below — quick status, then nine product questions that came out of the deep planning work this week. Need your answers (Eshan especially) before engineering starts so we don't burn cycles building the wrong thing.

## Status

- All five wizard / Clerk / Sentry crashes from earlier this week are fixed and live on prod.
- Sentry is wired and capturing client + server errors. Next time the app hits an error, we'll have the stack trace, not a screenshot.
- I rewrote the roadmap to strictly the 9 items you asked for on the June 25 call. Everything else (Eshan's full UW Process doc, earlier email feedback, competitor analysis) is preserved for future calls but not in this sprint.
- One security finding from the planning work: 8 endpoints were leaking data across tenants. Already fixed — single commit, tenant_id scoping added everywhere.

## The 9 product questions I need answers on

Numbered to match the roadmap items. Answer inline by reply — no call needed unless something is contentious.

**1. Guided onboarding wizard** (the one you both asked for loudest)
   a. Can a deal exist with ZERO documents (shell deal), or do we require at least the financials?
   b. When dropping a 2025 P&L, should Fondok prompt the analyst to confirm the year, or infer from the document?
   c. If the analyst pre-categorizes a file as "T12" but the AI is highly confident it's actually "PNL_MONTHLY" — do we warn the analyst or silently override?

**2. Onboarding → Validation separation**
   a. Auto-advance ONBOARDING → VALIDATING the moment first document hits EXTRACTED status, or wait for explicit user CTA?
   b. What gates VALIDATING → READY? Recommend: all checklist items green + all critical variance flags marked accepted + all USALI deviations acknowledged. OK to start there?

**3. USALI compliance scoring on upload**
   a. If fewer than 5 USALI rules apply to a sparse extraction, show "Inconclusive" instead of a percentage. OK?
   b. Some rules need market context (e.g., coastal insurance benchmark). When deal lacks that context, label them "requires market context" rather than fail them. OK?

**4. YoY variance → broker questions** (Eshan, this one needs your numbers)
   a. Confirm the threshold defaults. My working assumption from your "15% F&B" framing:
      - 10% YoY on departmental revenue and expenses
      - 15% YoY on F&B specifically
      - 20% YoY on Other Operated
      - 5% YoY on NOI and GOP (stricter, because rolled-up)
      Adjust these if you have institutional norms you'd rather use.
   b. v1 — YoY only, or also detect month-over-month within a year? Recommendation: ship YoY only, add MoM in a later release if analyst feedback warrants.

**5. Seller Q&A re-ingestion loop**
   a. Trust model: when the broker answers a question and Fondok's resolver agent proposes engine overrides (e.g., "change mgmt fee from 2.8% to 3% based on broker's clarification"), does the analyst ALWAYS confirm before the override applies? Recommendation: yes, never auto-apply. Preserves IC memo defensibility — every number is either extracted, seeded, or explicitly analyst-justified.

**6. Manual override + justification note**
   a. Existing overrides in the database don't have justification notes. Auto-migrate them with an empty-note + "legacy" flag, or force the analyst to back-fill a note before they can use those deals? Recommendation: auto-migrate (don't block existing deals).

**7. Gap detection**
   a. Look-back window for sequential gaps. Fixed 5 years (most institutional holds are 5–7 yr) or inferred from the earliest uploaded year? Recommendation: 5-year default with override at the deal level.
   b. Non-calendar fiscal years — should the gap detector flag a property that runs July–June if it's missing one year, or leave that to manual review? Recommendation: flag it, but let the analyst dismiss with one click.

**8. STR comp-set drift tracking**
   a. Property-name fuzzy matching threshold. "Hilton South Beach" vs. "Hilton Hotel South Beach" — exact match wins, fuzzy (>80% Levenshtein) flagged for review, <80% treated as drift. OK with 80% threshold?

**9. Per-customer tenant isolation** (Sam, this is a security architecture decision)
   a. Subdomain routing (apollo.fondok.com, brookfield.fondok.com) or path-prefix (fondok.com/tenant/apollo)? Recommendation: subdomain. Better cookie isolation, cleaner branding for the customer.
   b. Premium "instance-per-customer" tier (separate Railway worker + DB per customer) for the largest contracts who'll demand it during procurement? Recommendation: yes, offer it. Most won't need it, but having it ready avoids dealbreaker conversations.
   c. Does the Fondok internal team need cross-tenant visibility for customer support? If yes, we need an RBAC layer (more work). If no, current single-tenant-per-user works as-is.

## What happens after I get your answers

I sprint Wave 1 (items #6, #2, #1 — they unblock everything else) over the next ~2 weeks. Then we hit Wave 2 in the following sprint. We can re-sync at next Friday's call to look at what's shipped.

Sam — for question 9, this is the only one that's gating progress, since we want it in place before the Brookfield demo. The rest can ship on the cadence above.

Thanks both,
Prem
