# Labelled extraction corpus (Phase 5.1)

Data only — no application code. This directory holds per-document **labels** for the
Angler's deal (Kimpton Angler's South Beach, 132 keys, Miami Beach) plus the existing
golden documents, so that extraction quality can be measured against values a human has
actually confirmed, and so that the remaining values can be confirmed efficiently.

```
evals/corpus/
├── README.md                    this file
├── manifest.yaml                one case per source document (paths, ids, fixtures, notes)
├── concepts_used.txt            every concept id used by the labels, with counts
├── validate_corpus.py           stdlib-only schema check (run it before committing)
└── labels/
    ├── _provisional_policy.md   what is provisional, why, and the confirmation-session plan
    └── <case-id>.json           list of label records for that document
```

The pytest `apps/worker/tests/test_eval_corpus_schema.py` runs the validator (no DB, no
network).

## The one rule: confirmed vs provisional — no invented values

Every label is one of two things:

| status | meaning | who can set it |
| --- | --- | --- |
| `confirmed` | Ground truth. An analyst accepted or edited the field on the live deal, the value comes from the live-deal audit (2026-09-10), or it is a canonical value in a golden file the team already vetted (`evals/golden-set/documents/*.expected.json`). Must carry `confirmed_by`, `confirmed_at` and `source`. | Sam / Prem (analysts), or a vetted golden file |
| `provisional` | A **real** value nobody has confirmed yet: a live extractor output, a stored extraction payload, a cell read directly from the source workbook, or a figure transcribed from the OM/HMA text and verified to be present on the cited page. Must still carry `source`. | anyone, as long as the value was read from a source file |

Never type a number you did not read from a source file. If you cannot point at the
cell, page or payload field a value came from, it does not go in. If a document has no
value you can source, its label file is an empty list — that is a valid state (seven
cases are empty today; see the manifest notes).

`confirmed` is a statement about provenance, not about the extractor: a confirmed label
whose value equals a low-confidence extractor output is still confirmed. Conversely a
high-confidence extractor output is still provisional until someone signs it.

## Label schema

Each `labels/<case-id>.json` is a JSON list of records:

```json
{
  "concept": "gop",                                  // registry concept id, or null (then proposed_concept is required)
  "field_name": "p_and_l_usali.gross_operating_profit", // extractor field path; or "direct_read:<Sheet>!<cell>" for values read straight from the file
  "basis": "actual",                                 // actual | broker | om_history | market
  "scope": "ttm",                                    // annual | ttm | ytd | quarterly | monthly | null (static fact — needs a note)
  "value": 4970460,                                  // number, string or boolean; never null
  "unit": "USD",                                     // USD | ratio | percent | room_nights | keys | count | sqft | index | date | id | null
  "page": 2,                                         // int >= 1 — see page convention below
  "tolerance": 0.005,                                // relative tolerance (fraction of |value|); 0 for counts and static facts
  "status": "confirmed",                             // confirmed | provisional
  "confirmed_by": "Prem (live deal audit)",          // null when provisional
  "confirmed_at": "2026-09-10",                      // ISO date; null when provisional
  "source": "live deal audit 2026-09-10; ...",       // where the value came from — always required
  "proposed_concept": null,                          // only when concept is null
  "period": "2024-04..2025-03",                      // optional: YYYY, YYYY-MM or a YYYY-MM..YYYY-MM window
  "cell": "T12!P39",                                 // optional: workbook cell the value sits in
  "raw_label": "Gross Operating Profit",             // optional: row label / text as it appears in the document
  "note": "..."                                      // optional: anything the confirmer needs to know
}
```

Conventions:

* **Page.** For PDFs the PDF page number. For workbooks the 1-based sheet index in
  workbook order **including hidden sheets** — this is how `apps/worker/app/extraction/parser.py`
  numbers `source_page`, and it is what the live captures use (e.g. the May-2025 T-12 has two
  hidden IHG sheets first, so its `T12` sheet is page 4). For `.docx` the whole document is
  page 1 (the parser treats Word files as a single page).
* **Scope `null`** is reserved for static facts (keys, room-type counts, contract terms,
  property snapshot facts) and requires a `note`.
* **Basis for seller-side forward figures.** The enum has no `budget`/`plan`/`adjusted`
  value. Owner capex plans, the 2025 insurance projection, business-plan goals and the
  seller-adjusted T-12 sheet use `basis: "broker"` (the seller side's claim) with a note
  saying so. The registry builder should decide whether to add explicit bases; the labels
  are easy to re-tag because each one says why it chose `broker`.
* **`fixed_charges_total`** maps the statement row *Total non-operating income & expenses*
  (non-op income + rent + property & other taxes + insurance + other). **`noi`** maps the
  row *EBITDA: less Replacement Reserve* — the live audit and the golden set both treat that
  row as NOI. Both choices are called out in the label notes.
* **`misc_revenue`** (Miscellaneous Income) is not a registry concept. It is labelled with
  `concept: null, proposed_concept: "misc_revenue"`. Note the USALI identity
  `total_revenue = rooms + fb + other` only holds for these statements if `other_revenue`
  is taken to include miscellaneous income; the registry needs to say which.
* **Values are as read.** Extractor outputs keep the extractor's rounding (six significant
  digits); direct cell reads keep the cell value rounded to 6 dp; percentages from STR
  sheets stay as percentages (`unit: "percent"`), P&L occupancy stays a ratio.
  Tolerance is what lets the two forms of the same figure agree.
* **`period`** is metadata inferred from the document (column headers, statement period).
  Where the live extractor's own month label disagrees with the column the value sits in,
  the note says so (the March-2025 T-12 capture calls Jan–Mar 2025 "2024").

## Concept-id convention

`concept` must be one of the registry ids (the registry is being built in parallel and
will validate these files; the same list is embedded in `validate_corpus.py`):

```
rooms_revenue fb_revenue other_revenue total_revenue rooms_dept_expense fb_dept_expense
other_dept_expense dept_expenses_total dept_profit_total undistributed_total ag_expense
sales_marketing property_ops utilities it_expense gop mgmt_fee ebitda property_tax insurance
ffe_reserve fixed_charges_total noi occupancy adr revpar available_rooms rooms_sold keys
purchase_price exit_cap_rate renovation_budget closing_costs working_capital loan_amount
interest_rate amortization_years ltv
```

If a real field maps to none of these, keep it with `concept: null` and a
`proposed_concept` (snake_case) so the registry builder can decide. `concepts_used.txt`
lists every registry id and every proposed id with its label count; the validator fails
if that file is stale (`python evals/corpus/validate_corpus.py --write-concepts` rebuilds it).

## `FONDOK_CORPUS_DIR`

Source documents are **not** copied into this directory. Every manifest case with
`path_base: corpus_dir` is resolved as `$FONDOK_CORPUS_DIR/<path>`. The documented default
is the folder Sam shared, checked in at the repository root:

```
FONDOK_CORPUS_DIR=<repo>/FL Miami South Beach Anglers (Eshan)
```

Cases with `path_base: repo` (the two non-Angler's golden fixtures) resolve against the
repository root. `document_available` records whether the file was present when the
manifest was built; the validator prints an info line for any case whose file is missing
at run time but never fails on it. Each case also carries the file's `md5` so a moved or
re-exported document can be told apart from the one the labels were read from.

## How to add or confirm a label

1. Find (or add) the document's case in `manifest.yaml`. New cases need `id`, `filename`,
   `doc_type`, `path`, `path_base`, `document_available`, `live_document_id`,
   `payload_fixture`, `golden_case`, `notes` (extra keys are fine; keep the YAML subset).
2. Open the source file and locate the value. Record the page (see the page convention),
   the cell or raw text, and the exact value as it appears.
3. Append a record to `labels/<case-id>.json`. Use the live extractor's field path when
   the field exists on the live deal, otherwise `direct_read:<Sheet>!<cell>` (workbooks)
   or a descriptive dotted name (PDF/DOCX). Set `status: "provisional"` and describe the
   provenance in `source`.
4. To **confirm** a provisional label: set `status: "confirmed"`, fill `confirmed_by`
   (person or golden file), `confirmed_at` (ISO date) and append how it was confirmed to
   `source`. Do not change the value silently — if the confirmed value differs from the
   provisional one, replace the value and say so in `note`.
5. Run `python evals/corpus/validate_corpus.py --write-concepts` then
   `python evals/corpus/validate_corpus.py` (or `pytest apps/worker/tests/test_eval_corpus_schema.py`).

## Where today's labels came from

* **Live deal audit 2026-09-10** — the confirmed KPI / GOP / NOI / revenue values on the
  four live financial statements, the OM summary and broker pro forma, and keys = 132.
* **Live extraction capture 2026-09-09** (`/Users/prem/fon41_live.json`; also rendered as
  `apps/web/__tests__/helpers/fon41LiveFixture.ts`) — every low-confidence, unreviewed field
  on the four live statements (T-12 March 2025, 2024, 2023 and 2019 P&Ls). Its field
  *names* for the in-confidence fields are used where the value could be read from the
  workbook (the fixture's values for those are synthetic and were **not** used).
* **Stored real payloads** — `apps/worker/tests/fixtures/real_payloads/anglers_t12_real.json`
  (the May-2025 *Adjusted* T-12, which is the golden `anglers_t12` document and is not on the
  live deal) and `anglers_annual_pnl_real.json` (the 2023 P&L). All 357 payload fields are
  unreviewed (`reviewed: null`).
* **Golden set** — `evals/golden-set/documents/*.expected.json` canonical fields for the
  2023 P&L and the May-2025 T-12 (byte-identical to the fixtures, matched by md5).
* **Direct reads** of the source workbooks (openpyxl) for statement rows the captures did
  not carry, and for the CapEx, room-mix, insurance and STR workbooks.
* **Transcriptions** from the OM (pages 6, 40, 41) and the HMA / Business Plan Word files,
  each verified as a string match on the cited page before being written.

Not included on purpose: the synthetic `evals/golden-set/kimpton-angler` scenario (listed
under `excluded` in the manifest), forecast months on the May-2025 workbook, and the weekly
STAR report (no weekly scope in the enum yet).
