# Extraction schemas

Phase 4 of the dynamic-extensibility refactor. Per-doc-type extraction
contracts live in this directory as Markdown — the Extractor agent
loads the relevant section dynamically based on the Router's
classification rather than carrying the full universe in one giant
`SYSTEM_PROMPT` constant.

## Layout

```
extraction_schemas/
  README.md          ← this file
  _base.md           ← agent-behavior preamble (loaded for every doc type)
  om.md              ← Offering Memorandum field paths + examples
  t12.md             ← Trailing-twelve-month P&L field paths + examples
  str_trend.md       ← STR / CoStar Trend Report ...
  cbre_horizons.md   ← CBRE Hotel Horizons forecast ...
  pnl_benchmark.md   ← CBRE Benchmarker / HotStats USALI 11th ...
  market_study.md    ← CoStar submarket / market study: inventory, pipeline,
                        supply & demand trend (Market tab growth tiles), and
                        the appendix performance / supply & demand series by
                        class bucket (`market_study.class.<bucket>.<YYYY>.*`,
                        future-P&L market growth — FON-116). This file is ALSO
                        spliced into the legacy `SYSTEM_PROMPT` at import
                        (`extractor._legacy_market_study_block`), so it is the
                        one source of the MARKET_STUDY contract on both paths.
  capex.md           ← PIP / renovation budget (`capex_plan.pip.*`, a candidate
                        for the model's renovation budget — FON-101), forward
                        capital plan (`future_capex.*`, reference only),
                        historical capex ledger, FF&E reserve report
```

The loader maps a Router ``doc_type`` to ``<doc_type_lowercase>.md`` by
filename, so ``CAPEX`` → ``capex.md`` with no registration step. The
``doc_subtype`` an analyst tags a CAPEX file with (``future`` / ``historic``,
FON-41) is NOT visible to the extractor — the schema tells the LLM to pick the
namespace from the document's content, and the engine-input loader gates on
the stored subtype separately (a ``future`` document never feeds the model).

Schemas are loaded by `apps/worker/app/agents/extraction_schemas/loader.py`
and assembled into the Extractor's system prompt by
`apps/worker/app/agents/extractor.py::build_system_prompt`.

## Adding a new doc type

1. Add the doc_type enum value in
   `packages/schemas-py/fondok_schemas/document.py`.
2. Update the Router agent's prompt + valid set in
   `apps/worker/app/agents/router.py`.
3. Drop a new Markdown file here named `<doc_type_lowercase>.md`. The
   file SHOULD contain:
   * Brief description of what the document is (one paragraph).
   * Canonical field-path namespace ("`my_namespace.<line>` — ...").
   * Concrete extraction examples.
4. (Optional) Add an entry to the field catalog YAML at
   `apps/worker/app/extraction/field_catalog.yaml` if any new extracted
   keys need to flow into the engines.

No Python changes required. Worker restart picks up the new file.

## Migration plan

Today the legacy `SYSTEM_PROMPT` constant in `extractor.py` is the
runtime default and contains every doc type's contract inline — except
MARKET_STUDY, whose section is read from `market_study.md` at import
(FON-116) so the two paths cannot drift on it.
Schemas in this directory are loaded as a parallel code path gated on
the env var `EXTRACTOR_USE_DYNAMIC_SCHEMAS`:

* `EXTRACTOR_USE_DYNAMIC_SCHEMAS=1` → assemble the prompt from
  `_base.md` + the doc-type-specific file (when present).
* Unset or `0` → use the legacy embedded prompt.

The cutover lives behind a flag because the Extractor is the most
heavily-tested code path in the worker (Sam's pilots run through it
every upload). We'll switch over once a regression corpus of known
extractions confirms byte-equivalent or improved outputs.
