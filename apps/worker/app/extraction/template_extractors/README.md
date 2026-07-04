# Template extractors

Deterministic, $0 extraction for standardized report formats. See the
package docstring in `__init__.py` for the contract; the short version:

* `try_template_extract(parsed, doc_type)` returns `None` on ANY doubt
  — the caller falls through to the LLM extractor unchanged. A false
  negative costs one ~$0.30 Sonnet call; a false positive costs
  correctness. Bias hard toward `None`.
* On a hit, emit fields in the LLM extractor's exact wire shape using
  the canonical field paths from `app/agents/extraction_schemas/*.md`,
  with `confidence=1.0` on deterministically-read cells.
* Read from `ParsedPage.tables` (the lossless 2D grid), never `.text`.
* Anchor on labels (find the row by its label text, the column by its
  header text), never fixed coordinates.

## Implemented

* `str_trend.py` — STR / CoStar Trend reports. Three layouts: modern
  Monthly STAR `.xlsx`, modern Weekly STAR `.xlsx` (roster-only), and
  legacy Custom Trend `.xls`. Wired in via
  `_try_template_extraction()` in `app/api/documents.py` behind the
  `TEMPLATE_EXTRACTION_ENABLED` config flag.

## Adding CBRE Hotel Horizons (next candidate)

CBRE Horizons is the other standardized family we pay the LLM for
(`CBRE_HORIZONS` doc_type, field namespace `cbre_horizons.*` — see
`app/agents/extraction_schemas/` and `_bucket_cbre` /
`_build_cbre_block` in `app/api/documents.py` for what downstream
reads: `cbre_horizons.year_{1..5}.{occupancy_pct,adr_usd,revpar_usd,
revpar_growth_pct}` plus `submarket`, `chain_scale`,
`publication_date`). To add it:

1. Create `cbre_horizons.py` exposing a `_try_cbre(parsed)` and
   dispatch to it from `try_template_extract` when
   `doc_type == "CBRE_HORIZONS"`.
2. Fingerprint conservatively: CBRE ships PDFs more often than
   workbooks, so first check `parsed.parser` — the current pdfplumber
   table channel may not be reliable enough for a deterministic
   parser; validate against real fixtures
   (`tests/fixtures/sample_cbre_horizons.pdf`) before trusting it.
   If the tables are unstable, keep returning `None` for PDFs and only
   template-match native-Excel Horizons exports.
3. Anchor the four forecast tables (All Hotels + three price tiers) by
   their header labels, and require every anchor to resolve or return
   `None`.
4. Extend the wire-in gate in `documents.py`
   (`doc_type in ("STR", "STR_TREND")`) to include `CBRE_HORIZONS`,
   and use `agent_version = template:cbre_horizons:v1` (the `;pv=vN`
   suffix is appended by `_tag_agent_version`).
5. Test like `tests/test_template_extraction_str.py`: real-fixture
   positive, negative (a P&L must return `None`), field-shape
   assertions, flag-off passthrough.
