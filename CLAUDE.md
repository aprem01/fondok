# Fondok — engineering guide

AI hotel-RE underwriting SaaS. Monorepo (pnpm): `apps/web` (Next.js 14 → Vercel),
`apps/worker` (FastAPI + Python engines → Railway), `packages/schemas-*`.

## Design source of truth (FON-72)

The canonical UI/UX is the Claude Design prototype vendored at
`design/canonical/FONDOK-MVP-CANONICAL.dc.html`. Its screen→component map is
`design/canonical/DESIGN_MAP.md`. Older Claude Design iterations are **not**
implementation requirements. When building or changing UI, reference the
canonical `.dc.html` (real tokens, spacing, copy) — not screenshots, not the
live claude.ai URL (Cloudflare-gated to tooling).

**Conflict rule — apply in this order:**

1. The **latest explicit Linear** product/logic clarification governs
   *behavior* (business logic, calculations, acceptance criteria).
2. The **canonical Claude Design** governs *look / UX*.
3. **Prototype numbers are representative placeholders** — never wire a value
   from the prototype as data. Production values always come from the
   engine/model source-of-truth logic.
4. When the canonical design conflicts with an explicit Linear requirement or
   with existing model behavior, **flag it** for reconciliation — do not guess,
   and do not overwrite backend/model logic solely to match the prototype.

Never treat the prototype as a replacement for the codebase or backend logic.
Preserve the existing architecture, canonical model calculations, and data
provenance; use the prototype as the front-end reference only.

## Cross-tab reconciliation

Keep the Base Case canonical as it flows downstream:
`Financials / Investment / Debt → Cash Flow → Returns → Scenario Analysis → IC Memo`.
Tab values read from engine outputs (`apps/worker/app/engines/`, orchestrated by
`services/engine_runner.py`), never from static UI placeholders.

## Deploy

- `git push origin main` pushes both remotes (aprem01/fondok + fondok-colab/fondok)
  and triggers Vercel (web) + Railway (worker).
- Web is prod-deployed on push, but the alias is manual — after the build is
  Ready: `vercel alias set <deploy-url> fondok-app.vercel.app --scope aprem01s-projects`.
  Skipping the alias silently serves a stale bundle.
