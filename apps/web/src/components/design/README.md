# Fondok canonical design-system layer

Shared, **presentational, props-driven** React components + tokens extracted
**EXACTLY** from the vendored canonical design bundle
(`design/canonical/*.dc.html`, FON-72). Every tab is meant to be rebuilt from
these — fidelity to the design source is the point.

- **Presentational only** — no data fetching, no tab coupling. The host tab
  passes values, states, and handlers.
- **Fidelity** — colors/px/weights are the design's exact values. The canonical
  prototype is built almost entirely with inline styles + `oklch()`, so these
  components render with inline styles keyed off [`tokens.ts`](./tokens.ts) (the
  single source of truth), rather than approximating via Tailwind classes.
- **Not wired into any tab yet** — this is the design-system phase only.

Per the repo `CLAUDE.md` conflict rule, where the design and the app's current
look differ, **the design wins**.

## Components → canonical `.dc.html` source

| Component | File | Canonical source | What it is |
|---|---|---|---|
| `ProvenanceDot`, `StateBadge` | `ProvenanceDot.tsx` | `Fondok Data Key.dc.html` (`tokens()` strip dots) | The per-value 6-state origin dot, keyed off `ValueState` from `@/lib/api`. |
| `DataKey` | `DataKey.tsx` | `Fondok Data Key.dc.html` (strip + `spec` + "How to read this" modal); cross-checked vs the inline strip in `Cash Flow Tab.dc.html` | The one canonical Data Key strip: DATA KEY label · six origin dots · Total/summary sample · Editable sample · help modal. |
| `FieldValue`, `fieldKindFromState` | `FieldValue.tsx` | `Fondok Field System.dc.html` (`field()` + `specimens` + row markup) | Inline value cell with the field-level provenance + editability treatment (input/override/doc/link/calc/review/awaiting). |
| `WhereThisCameFrom` | `WhereThisCameFrom.tsx` | `Fondok Field System.dc.html` (detail popover markup + `insp` data); model cross-ref `Data Provenance System.dc.html` | The single anchored provenance/lineage popover: header · calculation · source · override · affects-downstream · actions. |
| `SubTabNav` | `SubTabNav.tsx` | `Fondok Sub-Tabs.dc.html` (demo control + `spec`/`states`); cross-checked vs `Cash Flow Tab.dc.html` | The underline sub-tab standard on a full-width content-column hairline. |
| `KpiTile` | `KpiTile.tsx` | `Cash Flow Tab.dc.html` `summary` tiles (== `Investment Tab.dc.html` `kpis`) | Summary metric tile: eyebrow label · 19px value · sub. |
| `SectionCard` | `SectionCard.tsx` | `Investment Tab.dc.html` `sections` (eyebrow variant) + `Cash Flow Tab.dc.html` table-card header (title variant); `Fondok Component Kit.dc.html` "Data card" | White panel with an eyebrow-or-title header. |
| `StatementTable`, `denseValueColor` | `StatementTable.tsx` | `Cash Flow Tab.dc.html` navy grid (Tier 2, dots) + `Fondok Data Key.dc.html` dense grid (Tier 3, colour-only) | The dark-navy statement grid. |
| tokens | `tokens.ts`, `tokens.css` | `Fondok Component Kit.dc.html` swatches + the three system files below | Palette, provenance/field colors, type scale, radii, borders, shadows. |

Cross-cutting system files consumed: `Fondok Component Kit.dc.html`,
`Fondok Data Key.dc.html`, `Fondok Field System.dc.html`,
`Data Provenance System.dc.html`, `Fondok Sub-Tabs.dc.html`, plus the
`Investment Tab.dc.html` / `Cash Flow Tab.dc.html` tab files for shared chrome.

## Token list

Single source of truth: [`tokens.ts`](./tokens.ts). Mirrored as CSS custom
properties in [`tokens.css`](./tokens.css) (`--fondok-*`) and exposed to Tailwind
under `colors.fondok.*` in `tailwind.config.ts` (additive + namespaced; the
existing palette is untouched).

### Core palette — `Fondok Component Kit.dc.html`

| Token | Hex | Use |
|---|---|---|
| `inkNavy` | `#14213d` | Statement-grid header, primary button, active tab |
| `sidebarNavy` | `#0f1a30` | Left-nav sidebar |
| `ground` | `#f5f4f0` | App background |
| `cardWhite` | `#ffffff` | Card / panel surface |
| `surfaceTint` | `#fbfbf9` | Nested surface (data-key strip, formula chips) |
| `border` | `#eae9e4` | Card + control border |
| `ink` | `#1a2233` | Primary text |
| `textSecondary` | `#6b6f76` | Body |
| `textMuted` | `#9a9a95` | Caption |
| `textFaint` | `#b0afaa` | Placeholder / column heads / awaiting em dash |
| `eyebrow` | `#8a8a86` | Section eyebrow label |
| `hairlineRow` | `#f7f6f3` | Grid/card row divider |
| `hairlineSection` | `#f2f1ec` | Card header/footer divider |
| `subtabHairline` | `#e6e5e0` | Sub-tab baseline |
| `hoverInk` | `#3a3f47` | Sub-tab hover / emphasis body |
| `gridHeaderText` | `#c9cede` | Statement-grid header text (on navy) |
| `gridHeaderDivider` | `#2a3a5c` | Statement-grid header column divider |
| `linkBlue` / `linkBlueHover` | `#2f4a8c` / `#1f3568` | Action links |
| `buttonSecondaryBorder` | `#d8d7d2` | Secondary button border |

### Provenance state colors — `Fondok Data Key.dc.html` (the canonical strip)

Drives `DataKey`, `ProvenanceDot`, `StateBadge`, and `denseValueColor`.

| State | Dot rendering | Color |
|---|---|---|
| `document_sourced` | filled green | `oklch(45% 0.12 155)` |
| `linked` | hollow green (white fill, 2px green border) | `oklch(45% 0.12 155)` |
| `assumption` | filled blue | `oklch(45% 0.14 260)` |
| `calculated` | filled grey | `#5f656e` |
| `awaiting_data` | hollow dashed grey | `#b0afaa` |
| `needs_review` | green origin + amber halo ring | ring `0 0 0 3px oklch(88% 0.07 45)` |
| _total / summary_ | bold near-black | `#1a2233` |
| _amber const_ | needs-review family | `oklch(52% 0.15 45)` |
| _review cell tint_ | dense-grid amber tint | `oklch(93% 0.05 45 / .55)` |

### Field-value treatment colors — `Fondok Field System.dc.html`

Drives `FieldValue` + `WhereThisCameFrom` kind colors. **These are a different
oklch tuning from the Data Key strip** (see the flag below).

| Kind | Color | Marks |
|---|---|---|
| `input` (editable assumption) | `#1a4fa0` | blue text · dashed underline `1px dashed rgba(26,79,160,.45)` |
| `override` (overridden) | `#1a4fa0` | blue text · dashed underline · violet corner ▲ `oklch(55% 0.13 300)` |
| `doc` (document-sourced) | ink `#1a2233` | `◱` page-corner glyph `oklch(48% 0.09 165)` · dotted underline `1px dotted #c3c9c2` |
| `link` (linked) | ink `#1a2233` | `↗` outbound-arrow glyph `oklch(52% 0.07 240)` · no rule |
| `calc` (calculated) | ink `#1a2233` | no mark (the default) |
| `review` (needs review) | ink `#1a2233` | `◱` glyph · amber dotted rule + corner `oklch(62% 0.16 55)` |
| `awaiting` (awaiting data) | muted `#b0afaa` | em dash |

Inline editor: `#fff` bg · `1.5px solid #1a4fa0` border · `0 0 0 3px rgba(26,79,160,.12)` ring · radius 5 · `#1a4fa0` 600.

### Popover kind colors — `Fondok Field System.dc.html` (`insp`)

`editable_assumption #1a4fa0` · `overridden oklch(50% 0.13 300)` ·
`sourced_needs_review oklch(52% 0.16 55)` · `document_sourced oklch(45% 0.09 165)` ·
`linked oklch(48% 0.08 240)` · `calculated #1a2233`.

### Type scale, radii, borders, shadows

See `type`, `radius`, `border`, `shadow`, `overlay`, `fontStack` in
[`tokens.ts`](./tokens.ts). Highlights: KPI value 19px/700 · section title
13.5px/700 · sub-tab 13.5px (500 rest / 700 active) · grid cell 12px · card
radius 10px · popover radius 11px · button radius 6px · popover shadow
`0 14px 40px rgba(20,33,61,.16), 0 2px 6px rgba(20,33,61,.06)`.

## Known design ambiguity (flagged for Sam / Prem)

The three cross-cutting system files use **three slightly different oklch
tunings** for the "same" six states — `Fondok Data Key` (strip/dots),
`Fondok Field System` (inline field values), and `Data Provenance System`
(conceptual legend). E.g. document/sourced green is `oklch(45% 0.12 155)` in the
Data Key strip but `oklch(48% 0.09 165)` for the field-value glyph. We follow
**each file for the component it authors** and did **not** silently unify them.
If a single canonical hue per state is wanted, reconcile in the design source
first, then update `tokens.ts`.

The Data Key strip also carries **no per-state counts** — only two illustrative
numbers (Total/summary sample, Editable sample), which `DataKey` exposes as
`totalSample` / `editableSample` props.
