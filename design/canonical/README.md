# Canonical Design — source of truth

This directory holds the **one** canonical Claude Design prototype for the Fondok
MVP and the map from its screens to the React components that implement them.
It exists so design changes reach engineering as an exact, versioned artifact —
not screenshots, and not "which iteration is current?" (FON-72).

## Files

- `FONDOK-MVP-CANONICAL.dc.html` — the exported canonical prototype (single
  self-contained file: all 11 tabs, real CSS / tokens / copy). **Drop Sam's
  latest export here** and commit it. The commit history is the design version
  log; a design change becomes a reviewable diff.
- `DESIGN_MAP.md` — each prototype tab → the route and component that implement
  it. Start here when translating a design change into code.

## Working model (FON-72)

- **Claude Design** = canonical visual/product UI-UX source of truth (this file).
- **Linear** = implementation requirements, business logic, QA findings,
  acceptance criteria, explicit product clarifications.
- **Vercel / codebase** = the implemented product.

Live-fetching the prototype from claude.ai is **not** a reliable channel —
claude.ai is Cloudflare/auth-gated to automated tooling. The committed
`.dc.html` is the channel for implementation; the Claude Team seat is for humans
viewing and editing the prototype.

## Updating the canonical design

1. Sam makes the change in the Claude Design prototype and writes the
   implementation requirement + acceptance criteria on the relevant `FON-` ticket.
2. Export the prototype and replace `FONDOK-MVP-CANONICAL.dc.html` here; commit
   (`design: refresh canonical prototype — <what changed>`).
3. Implement the delta against the refreshed file, per the conflict rule in the
   repo-root `CLAUDE.md` → **Design source of truth**.

## Conflict rule (summary — full text in `/CLAUDE.md`)

1. Latest explicit Linear product/logic clarification governs **behavior**.
2. Canonical Claude Design governs **look / UX**.
3. Prototype numbers are **representative placeholders** — never wire them as
   data. Production values come from the engines / model source-of-truth logic.
4. Flag material design-vs-requirement conflicts instead of guessing.
