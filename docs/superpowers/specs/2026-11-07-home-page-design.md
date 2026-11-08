# Public home page (`/`) redesign — 2026-11-07

## Why

The live page is a six-slide carousel that advances every 8 s. A visitor sees one message, half-rendered if they land mid-fade, with no proof on screen and no visual at all on phones. Buyer research for institutional acquisitions teams ranks *verifiability* and *analyst override* above speed, weights *data handling* heavily, and penalises auto-rotating heroes. Competitors lead with provenance mockups and security sections. Fondok's thesis ("cited to the source page") is right; the page does not show it.

## Constraints (unchanged)

- Desktop: one screen, no scrolling. Phones scroll.
- Evergreen palette, existing fonts (Source Serif 4 / Inter / JetBrains Mono), sample-hotel figures internally consistent and labelled "Illustrative".
- Outcomes only. No internals, no customer names, pilot mentions, usage numbers, time-saved claims, certifications, testimonials, pricing or team bios.
- The only credibility device beyond showing the product: one line of true data-handling facts.
- Audience: institutional hotel acquisitions teams (analysts, VPs, principals).
- CTA: Request access (mailto). No self sign-up.

## Decision (independent UX review, adopted)

**Layout.** Static two-column screen. Left: kicker, headline, thesis, one CTA, sub-CTA line. Right: the sample-hotel card with four click-only tabs beneath it and a one-line caption. Footer: the data-handling line, full width. No carousel, no progress bars, no prev/next arrows, no ambient blur, no "See how it works", no secondary sign-in button (sign-in stays in the header only).

**Copy (verbatim).**
- Kicker: `HOTEL ACQUISITION UNDERWRITING`
- Headline: `Pay the right price for every hotel you buy.` (italic evergreen "buy.")
- Thesis: `Fondok reads the OM, T-12 and STR reports and builds the underwriting, the walk-away price and the IC package. Every figure is cited to the source page. Your analysts can override any assumption, and the change carries through to the memo, deck and model.`
- CTA: `Request access`
- Sub-CTA: `Fondok onboards your firm. Your team joins by invitation.`
- Tabs → caption:
  - `OM vs T-12` → `Four lines where the OM runs ahead of the trailing twelve months, flagged before they become your basis.`
  - `Cited to the page` → `Year 1 rooms revenue traced to T-12, page 4. Your IC checks the work instead of questioning it.`
  - `Walk-away price` → `The price that clears your hurdle, beside the ask. Bid with conviction, walk away without second-guessing.`
  - `IC package` → `Memo, deck and model from one underwriting. Nothing retyped, nothing contradicting itself at committee.`
- Data-handling line: `Each firm's data is isolated at the database level. Every action is recorded in the firm's activity log. Fondok's access to a firm's deals is time-limited and recorded. Documents are not used to train AI models.`

Every sentence in the data-handling line is true today: Postgres RLS per tenant (`DB_SET_TENANT_GUC=1`), `audit_log` per tenant, support access grants (`apps/worker/app/support_access.py`), and the API providers' no-training terms (`docs/SECURITY_ARCHITECTURE.md`). The override sentence is true via `deals.field_overrides` → `engine_runner` → live export payload.

**Hierarchy.** Headline largest (`clamp(40px, 6.4vh, 60px)`), then the card's two 28 px mono figures, then the 17 px thesis. The CTA is the only filled evergreen block besides the header button. Kicker, sub-CTA, tab captions and data line are muted (`#6b7c76`, 12–13 px). Active tab: dark text, 2 px evergreen underline.

**Motion.** None without user input. A tab switch crossfades the card over 150 ms; disabled under `prefers-reduced-motion`. No entry animation on first paint.

**Accessibility.** `role="tablist"` / `role="tab"` with roving tabindex; Left/Right/Home/End move, Enter/Space activate; card is `role="tabpanel"` with `aria-labelledby`; caption `aria-live="polite"`. The page-level arrow-key handler and `aria-roledescription="carousel"` are removed. 4.5:1 contrast on `#f4f7f5`; CTA focus ring 2 px evergreen, 2 px offset.

**Mobile order.** Header → kicker → headline → thesis → CTA → sub-CTA → card (visible) → tabs as a horizontally scrollable row → caption → data-handling line. One column, 16 px gutters.

## Acceptance checks (automated)

1. Desktop (1280×720, 1440×900): page does not scroll and the data-handling line is inside the viewport.
2. 30 s idle after load: active tab and visible card unchanged; no running CSS animations.
3. Exactly one `REQUEST_ACCESS_HREF` link inside `main`, text `Request access`; at 375 px the card `figure` is visible and precedes the tablist in DOM order.

## Out of scope

Pricing, public methodology page, sample package download, team bios, any analytics beyond what exists. A MiroFish simulation (institutional hotel-acquisitions crowd reacting to the current page vs this design) runs after implementation; its findings may feed a copy revision, not a layout change.
