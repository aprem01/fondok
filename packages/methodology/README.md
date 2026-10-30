# Methodology package

One source, three detail levels, two renderers, one consumer (FON-120).

```
packages/methodology/
  index.yaml                  section ORDER and ids, page chrome per level, Atlas keywords
  sections/<nn>-<id>.md       one file per section: frontmatter + the three levels
  data/management_fee_guidance.yaml   the one dynamic data file a slot reads
  README.md                   this file — the authoring rules
```

| Level | Who reads it | Where it renders | How it gets there |
|---|---|---|---|
| `conceptual` | every signed-in user | `/methodology` | `scripts/gen_methodology.py` → `apps/web/src/lib/methodology/conceptual.generated.ts` |
| `detailed` | admins of an internal tenant | `/methodology/detail` (fetched at runtime) | `GET /methodology?level=detailed` — **never in the web bundle** |
| `engineering` | diagnostics deployments | `/admin/methodology-engineering` | `engineering.generated.ts`, behind `NEXT_PUBLIC_SHOW_DIAG` |
| conceptual + detailed | Atlas (the deal copilot) | the system prompt of `POST /deals/{id}/ask` | `apps/worker/app/methodology/select.py`, sections chosen by `index.yaml` keywords |

The worker parses these files at boot (`apps/worker/app/methodology/`); `/health`
reports `methodology_sections` and `methodology_missing` when the package did
not load. The web never parses markdown — the codegen emits the parsed AST and
`components/methodology/render.tsx` renders it. CI runs
`scripts/gen_methodology.py --check` (drift) and
`scripts/check_methodology_touched.py` (an engine / runner / schema change must
touch this package or carry the commit trailer `methodology: unchanged`).

## The standing rule

**Methodology changes in the same change set as any underwriting behaviour
change.** If a tab shows something new, a source's precedence moves, a default
changes or a figure starts being withheld, the owning section changes in the
same PR — conceptual for what the reader sees, detailed for the value, and the
engineering level for the field or route. Then regenerate:

```
uv run --project apps/worker python scripts/gen_methodology.py
```

## Section file anatomy

```markdown
---
id: projection                       # must equal the index entry and the file name <nn>-<id>.md
title: "How the Future P&L is built" # the client title; a level may override it
decisions: [FON-44, FON-98]          # tickets whose outcome this section states
detailed: none                       # ONLY when the section deliberately has no detailed level
anchors:                             # every data-testid the body carries — the tests pin these
  - methodology-exit-basis
---
<!-- a comment: never rendered -->
::: conceptual
::: intro
One or two sentences the section opens with.
:::

::: card {testid=methodology-exit-basis}
#### Card title

A paragraph with **strong**, *emphasis* and a {{badge:seed}} badge.

- **Label.** A labelled list item.
- Plain item.
:::
:::

::: detailed
::: intro
…
:::
::: card
#### Exit cap rate — precedence
<!-- source: apps/worker/app/services/engine_runner.py (_seed_defaults exit_cap_rate 0.07) -->
3. Platform default 7.0%, badged Seed.
:::
:::

::: engineering {title="Projection methodology"}
…
:::
```

* **Levels** are top-level fences `::: conceptual` / `::: detailed` /
  `::: engineering` … `:::`. Every section has `conceptual` and `detailed`
  unless the frontmatter says `conceptual: none` / `detailed: none` explicitly.
* **Containers** (`::: card`, `::: stages` › `::: stage {n=1 icon=FileText}`,
  `::: chain` (an ordered precedence list), `::: badges` › `::: badge {source=t12_actual name="T-12 Actual"}`,
  `::: columns` › `::: column {icon=Pencil}`, `::: deflist`, `::: box`, `::: intro`)
  are the page's own layout vocabulary; nothing else is accepted.
* **Blocks**: `####` (card title) and `#####` (box title) headings; `- ` and
  `1. ` lists; GFM pipe tables; `{{slot:<name>}}` alone on a line. A block may
  start with `{testid=…}`.
* **Inline**: `**strong**`, `*em*`, `` `code` `` (literal), `[text](/internal/path)`,
  `{{badge:<source>}}`, `{{reason:<code>}}`, `{{icon:<Name>}}`, inline
  `{{slot:<name>}}`, a hard break as `\` at the end of a line, and escapes
  `\* \` \< \> \{ \} \[ \] \| \_ \# \\`.
* **No HTML.** A raw `<` is a parse error. Comments `<!-- … -->` are stripped.
* **Slots** are the blocks whose facts live in code — `reason-code-table`,
  `reason-code-table-engineering`, `concept-registry-table`, `mgmt-fee-guidance`
  and the inline `mgmt-fee-disclaimer`, `registry-version`, `registry-count`.
  Web: `apps/web/src/components/methodology/slots.tsx`; worker:
  `apps/worker/app/methodology/slots.py`. Both read the same generated sources.

## The `detailed` level — every figure cites its source

A `detailed` block that states a number (a default, a band, a tolerance, a
formula) carries `<!-- source: <repo path> (<symbol or note>) -->` inside it or
on the line before it, and the path must exist. The loader refuses the package
otherwise. Nothing in `detailed` is invented: it states what the code or the
public copy already states, with the value.

## Client-copy discipline (the `conceptual` level)

`apps/web/__tests__/methodologyClientCopy.test.tsx` renders the client page and
greps every piece of visible text. These are its rules, verbatim — the
`conceptual` level must satisfy all of them:

| Rule | Regex (the test's own) |
|---|---|
| No ticket ids, phases, waves | `/FON-\d+\|\bE-0\d\d\|\bR-0\d\d\|Wave \d\|Phase \d/` |
| No ISO dates | `/\b20\d\d-\d\d-\d\d\b/` |
| No API routes | `/\b(GET\|POST\|PATCH\|DELETE) \//` |
| No repository paths | `/apps\/worker\|apps\/web\|packages\/\|\.py\b\|\.yaml\b\|\.json\b/` |
| No model-vendor names | `/\bClaude\b\|\bOpus\b\|\bSonnet\b\|\bHaiku\b\|LlamaParse\|PyMuPDF\|openpyxl/` |
| No snake_case tokens | `/\b[a-z][a-z0-9]*_[a-z0-9_]+\b/` |
| No dotted field paths (`revenue.years[0]`, `p_and_l_usali.x`) | `/\b[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*(\.[a-z0-9_\[\]]+)*\b/` |
| No client names | `/Kimpton\|Brookfield\|Eshan\|Rani\b\|\bSam\b/` |
| No `<code>` spans at all | `document.querySelectorAll('code')` is empty |
| No placeholders | `/Walkthrough video\|Loom\|coming soon\|work in progress/i` |

The same test pins: the ten `Section N` labels; "What a dash means"; the dash
rule sentence (`A figure with no source renders as —.` …); the
`reason-code-table` rows by label and client explanation, never by code; the
`methodology-returns-by-exit-year`, `methodology-exit-basis` and
`methodology-hold-defaults` anchors and their wording. Keep those sentences when
editing around them, or update the test in the same change.

The `engineering` level is exempt from the discipline — it is the place for
field paths, routes and env vars — and three tests pin its wording
(`methodologyMarkets`, `methodologyReasons`, `methodologyRenovationBudget`).

## Tests

* web: `methodologyClientCopy` (discipline), `methodologyLevels` (levels,
  anchors, slots), `methodologyEquivalence` (the split rendered the legacy page
  verbatim — a migration guard with a frozen fixture), the three engineering
  tests above;
* worker: `tests/test_methodology_package.py` (parser, loader invariants, text),
  `tests/test_methodology_routes.py` (levels and gates),
  `tests/test_researcher_methodology.py` (Atlas selection and citation).
