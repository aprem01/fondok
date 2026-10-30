---
id: concept-registry
title: "Concept registry"
decisions: []
conceptual: none
detailed: none
anchors:
---
<!-- section 8: Concept registry — from EngineeringMethodology.tsx lines 1006-1048 -->
::: engineering {title="Concept registry"}
::: intro
Every P&L line, operating statistic, OM figure, debt term and market metric Fondok names is defined
once, in a registry the worker validates at boot and the web app is generated from. Registry
v{{slot:registry-version}} · {{slot:registry-count}} concepts on this table.
:::

::: card
The same line reaches Fondok under many names — an extractor may emit
`p_and_l_usali.gross_operating_profit`, `gop_usd` or `ttm_summary_per_om.gop_usd` for one GOP figure
— and until now each screen and engine kept its own list of those names. The registry
(`apps/worker/app/ontology/concepts.yaml`) holds one entry per concept: its USALI line, unit, sign
and period, the identity it must satisfy (GOP = Total Revenue − Departmental − Undistributed), the
USALI rules that test it, the engines that consume it, and its aliases per document type — each
alias tagged with the basis it carries (a T-12 line is an *actual*; a `broker_proforma.*` line is
the *broker's claim*; the OM's historical-year block and comp-set stats are neither) and, where the
path says so, its period. The worker validates the registry when it starts (duplicate aliases,
unknown rule ids or engines, an identity naming an unknown concept all fail the load) and reports
the version on `/health` as `ontology_version`; `GET /ontology/concepts` serves it, and this page
and the web app read a generated copy that CI refuses to let drift. Resolution is deterministic: an
exact path listed for the document type wins, then a path listed for any document, then a
unit-suffix-stripped match, then the field's last segment against a bare alias — a monthly,
quarterly or year-to-date slice is never served as an annual figure, and a request for the broker's
claim never returns the OM's history. The unit-suffix-stripped tier now *refuses* rather than
reinterprets: it still lets a `_usd` or bare sibling find its concept, but a path that declares a
unit the concept does not carry — a `_pct` column against a dollar line — resolves to
{{reason:unit_unknown}} instead of being read under the concept's unit. A path that declares no unit
at all is unaffected. The dash reasons in Section 7 are drawn from this same registry. Today the
registry is the declared vocabulary and the drift gate; the engines, the USALI scorer, the variance
report and the worksheet are re-wired onto it in the next phase, and their behaviour on the live
system is unchanged until then.

:::

::: card
#### Concepts (generated from the registry)

{{slot:concept-registry-table}}

:::

:::

