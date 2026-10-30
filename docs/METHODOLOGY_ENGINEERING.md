# Methodology — engineering reference (pointer)

The engineering reference behind the client `/methodology` page is the
**`engineering` level of `packages/methodology/sections/<nn>-<id>.md`**
(FON-120). It is rendered at `/admin/methodology-engineering` when
`NEXT_PUBLIC_SHOW_DIAG=true` (the same gate as `/diag`) from
`apps/web/src/lib/methodology/engineering.generated.ts`, and served by
`GET /methodology?level=engineering` to admins of an internal tenant.

* Field paths, API routes, environment variables, reason codes, the generated
  concept registry table (`{{slot:concept-registry-table}}`) and the lineage
  node vocabulary live in the section files, beside the conceptual text they
  explain, so a reviewer sees the three levels of one topic side by side.
* The client-copy discipline (`apps/web/__tests__/methodologyClientCopy.test.tsx`)
  applies to the `conceptual` level only; the `engineering` level is where the
  engineering vocabulary belongs.
* Three tests pin the engineering wording: `methodologyMarkets`,
  `methodologyReasons`, `methodologyRenovationBudget`.

Authoring rules: `packages/methodology/README.md`.
