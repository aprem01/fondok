---
id: reasons
title: "What a dash means"
decisions: [FON-118]
detailed: none
anchors:
  - dash-rule
---
<!-- section 7: What a dash means — from page.tsx lines 1078-1157 -->
::: conceptual {title="What a dash means"}
::: intro
Where Fondok cannot produce a figure it shows a dash — never a placeholder, a zero, or a number
carried over from a prototype. A deal with no key count or purchase price does not run at all: the
model never substitutes a placeholder property, and the run names the missing inputs. Every such
refusal has one of the reasons below, and hovering the dash shows it.
:::

::: card {flush=true}
{{slot:reason-code-table}}

:::

::: card
#### How to read a dash

- {testid=dash-rule} **A figure with no source renders as —.** That holds for every number on every
  screen and in every export: a room count the documents have not stated yet, a confidence score
  before any field has been read, a projection line the model did not produce, a return the engine did
  not compute. Nothing is filled in with a zero, an estimate or a sample figure, and the exported
  workbook leaves the same cell empty.
- **One glyph, many reasons.** Every reason renders the same dash; the hover text is what
  distinguishes them.
- **Where it lands on the Data Key.** “Needs review”, “Basis mismatch”, “Period mismatch” and “Unit
  unknown” describe a figure that exists but cannot be trusted or compared, so they read as *Needs
  review*. Every other reason means the figure is not there yet and reads as *Awaiting data*.
- **A dash is not an error.** A model that could not finish shows the red banner described in Section
  4, naming the model and the reason it stopped, with a Re-run. A screen whose results could not be
  fetched at all says so, with a Retry, rather than pretending the model has not run. A dash is the
  model declining to invent a number it has no grounds for — the reason says which grounds are
  missing.

:::

::: card
#### Structure edits change how a statement reads, never what it computes

On the Historical P&L, **Customize structure** lets you relabel a line, split one line into parts
that must reconcile to the locked parent, reorder lines within their section, hide a line, and add a
line of your own. All five are presentation: the value behind each row, the document it was read
from and its Data Key state are untouched, and no model reads an added line. The layout is stored on
the deal, so every reviewer reads the statement as you arranged it, and **Reset layout** restores
the canonical statement.

:::

:::

<!-- section 7: What a dash means — from EngineeringMethodology.tsx lines 936-1003 -->
::: engineering {title="What a dash means"}
::: intro
Where Fondok cannot produce a figure it shows a dash — never a placeholder, a zero, or a number
carried over from a prototype. Every such refusal resolves to one of the reason codes below. They
are one shared vocabulary (packages/schemas-py/fondok_schemas/reasons.py, mirrored in TypeScript)
and this table is rendered from that module, so it cannot drift from what the code does.
:::

::: card {flush=true}
{{slot:reason-code-table-engineering}}

:::

::: card
#### How to read a dash

- **One glyph, many reasons.** Today every code renders the same “—”; the code is what distinguishes
  them. It travels on the value's provenance record (`reason`, alongside the source and formula that
  “Hover any number to trace it” in Section 3 describes) as each tab and export adopts the vocabulary.
- **Where it lands on the Data Key.** `needs_review`, `basis_mismatch`, `period_mismatch` and
  `unit_unknown` describe a figure that exists but cannot be trusted or compared, so they read as
  *Needs review*. Every other code means the figure is not there yet and reads as *Awaiting data*.
- **A dash is not an error.** An engine that could not finish shows the red banner described in
  Section 4, with a Re-run. A dash is the model declining to invent a number it has no grounds for —
  the code says which grounds are missing.

:::

::: card
#### Structure edits change how a statement reads, never what it computes

On P&L → Historical P&L, **Customize structure** lets an analyst relabel a line, split one line into
presentation children that must reconcile to the locked parent, reorder lines within their section,
hide a line, and add an **analyst line** of their own. All five are **presentation**: the canonical
value behind each row, the document it was read from and the Data Key state it carries are untouched
by any of them, and a reordered row keeps its own lineage because the layout stores row identities,
not values. An analyst line is marked as an assumption and carries an *Analyst line* chip — it is
entered here, is read from no document, and no engine reads it.

The layout is stored **on the deal** (`field_overrides.worksheet_layout`), not in the browser, so
every reviewer who opens the deal reads the statement as the analyst arranged it — a reordered
statement is a deal artifact an IC reviewer must see, not a per-device preference. It is the one
`field_overrides` key the engines never read: the worker skips it explicitly when it routes
overrides into engine input, so a relabel, split, reorder, hide or added line cannot move a modeled
number. If a layout edit fails to save, the worksheet says so and offers a retry rather than letting
the arrangement look saved. **Reset layout** drops every structure edit and restores the canonical
statement.

:::

:::

