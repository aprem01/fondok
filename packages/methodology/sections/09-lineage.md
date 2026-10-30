---
id: lineage
title: "Every number traces back"
decisions: [FON-44]
detailed: none
anchors:
---
<!-- section 8: Every number traces back — from page.tsx lines 1160-1197 -->
::: conceptual {title="Every number traces back"}
::: intro
Pick any headline number on a deal and Fondok can show the chain that produced it, one step at a
time, down to the page of the document it was read from — or state, in the vocabulary of Section 7,
exactly why the chain stops short.
:::

::: card
#### The walk

::: chain
1. **Headline number** — levered IRR, unlevered IRR, equity multiple, Year-1 cash-on-cash, minimum
   DSCR, Year-1 NOI. These are the roots you can start from.
2. **Modelled value** — the value behind it, and each value behind that — IRR to the cash flows, cash
   flows to NOI and debt service, NOI to gross operating profit, profit to revenue, revenue to rooms
   revenue.
3. **Assumption** — the underwriting input the calculation rests on — starting occupancy, starting ADR,
   exit cap rate, LTV — carrying the source that produced it.
4. **Extracted field** — the exact field on the exact extraction that supplied the assumption, with the
   concept it resolved to and the basis and period it carries.
5. **Document and page** — the uploaded document, and the page the number sits on. This is where a walk
   that closes ends.

:::

Three chains end deliberately short of a page, and each says so. Your **override** ends at the
override, carrying your justification. A **platform default** ends at the default. A **benchmark** —
a market forecast, an industry data set, your own portfolio, an STR feed — ends at the benchmark,
and continues to a page only when the benchmark itself arrived as an uploaded document. Each link
also says whether the model asserted it or Fondok inferred it from the dependency between models.

:::

::: card
#### What “stale” means

A chain describes one model run, so it is only as current as that run's inputs. When a document is
uploaded, the deal record edited, or a document the run relied on deleted *after* the run started,
the chain is marked stale. Stale does not mean wrong and it does not hide the chain: the evidence
shown is still exactly what produced the numbers on screen. It means the deal has moved since, and
re-running the model will produce a different chain.

:::

:::

<!-- section 9: Every number traces back — from EngineeringMethodology.tsx lines 1051-1119 -->
::: engineering {title="Every number traces back"}
::: intro
Pick any headline number on a deal and Fondok can show you the chain that produced it, one step at a
time, down to the page of the document it was read from — or state, in the vocabulary of Section 7,
exactly why the chain stops short.
:::

::: card
The pieces already existed separately: each engine emits a per-value trace (the formula, its named
inputs, and a pointer at whichever other value fed it — Section 3's “hover any number”), the
assumption loader records which source produced every input, each extracted field carries the page
it was read from, and the IC memo carries its citations. `GET /deals/{id}/lineage` joins them into
one graph and serves it for the same run every tab is pinned to, so the chain always describes the
numbers on screen rather than a newer or older model run. Each link in that graph also says how it
was established: **asserted** when the engine that computed the value named the assumption or the
upstream value itself, and **inferred** when Fondok had to derive the connection from the engine
dependency graph instead — a genuine link either way, but only the asserted one is the engine's own
claim, and every deal's chain carries the count of each.

:::

::: card
#### The walk

::: chain
1. **KPI** — a headline number — levered IRR, unlevered IRR, equity multiple, Year-1 cash-on-cash,
   minimum DSCR, Year-1 NOI. These are the roots you can start from.
2. **Engine value** — the modeled value behind it, and each modeled value behind that — IRR to the cash
   flows, cash flows to NOI and debt service, NOI to GOP, GOP to total revenue, revenue to rooms
   revenue.
3. **Assumption** — the underwriting input the calculation rests on — starting occupancy, starting ADR,
   exit cap rate, LTV — carrying the source that produced it.
4. **Extracted field** — the exact field on the exact extraction that supplied the assumption, with the
   concept it resolved to (Section 8) and the basis and period it carries.
5. **Document → page** — the uploaded document, and the page the number sits on. This is where a walk
   that closes ends.

:::

Three chains end deliberately short of a page, and each says so rather than looking grounded. An
**analyst override** ends at the override, carrying the justification note the analyst saved with
it. A **seed** — a platform default no document on the deal supports — ends at the seed. A
**benchmark** — CBRE Horizons, the HOST default set, your own portfolio P&L, an STR feed — ends at
the benchmark, and continues to a page only when the benchmark itself arrived as an uploaded
document.

:::

::: card
#### What each step is

Every step in a chain is one of eleven kinds, and its identifier says which — so a link into the
chain is stable and points at exactly one thing.

| Step | Identifier | What it is |
| --- | --- | --- |
| **KPI** | `kpi:` | A headline number a tab renders — the root of a chain. |
| **Engine value** | `engine:` | One modeled value in an engine’s output, with the formula and inputs that produced it. |
| **Assumption** | `assumption:` | A canonical underwriting input, carrying the source that set it. |
| **Statement line** | `line:` | A USALI-normalized statement line. Reserved — the walk gains this step when normalized spreads are stored per run. |
| **Extracted field** | `field:` | One field on one extraction, with its concept, basis and period. |
| **Document** | `doc:` | An uploaded document on the deal. |
| **Page** | `page:` | The page of that document the value was read from. |
| **Override** | `override:` | An analyst override, carrying the justification note. Terminal. |
| **Seed** | `seed:` | A platform default no document supports. Terminal, with its reason. |
| **Benchmark** | `benchmark:` | A benchmark or market feed — CBRE Horizons, HOST, your portfolio P&L, STR. |
| **Memo section** | `memo:` | An IC-memo section, linked from the document and page it cites. |

:::

::: card
#### A link that does not close states its reason

Anything the walk cannot take all the way to a page is reported, never dropped: each unresolved link
names the step it broke on and carries a reason code from the Section 7 vocabulary — `no_document`
when nothing on the deal could ground it, `no_source` when documents exist but none of their fields
resolves to the concept, `str_unavailable` when a requested feed did not populate. An empty list
means every root reached a document page. It never means the search stopped early.

:::

::: card
#### What “stale” means

A chain describes one engine run, so it is only as current as that run's inputs. When a document is
uploaded — or the deal record edited, or a document the run relied on deleted — *after* the run
started, the chain is marked **stale**. Stale does not mean wrong and it does not hide the chain:
the evidence shown is still exactly what produced the numbers you are looking at. It means the deal
has moved since, and re-running the model will produce a different chain. It is the same condition
Section 7's `stale_run` describes, surfaced on the evidence rather than on a single figure.

:::

:::

