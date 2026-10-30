---
id: extraction
title: "What Fondok reads from your documents"
decisions: [FON-79, FON-88, FON-91]
detailed: none
anchors:
  - methodology-duplicate-deal
---
<!-- section 1: What Fondok reads from your documents — from page.tsx lines 37-114 -->
::: conceptual {title="What Fondok reads from your documents"}
::: intro
Every uploaded document moves through the same stages: it is read, filed by type, its figures are
extracted and checked, and the result is reconciled against the statement's own totals. Nothing is
typed in by Fondok — a figure the documents do not state stays a dash.
:::

::: stages
::: stage {n=1 icon=FileText}
#### Read

PDFs, Excel workbooks, CSV files, Word documents and PowerPoint decks are read into text and tables.
A scanned PDF with no readable text layer is marked as unreadable in the Data Room with a Retry,
rather than quietly producing nothing.

:::

::: stage {n=2 icon=Sparkles}
#### Classify

Fondok reads the file name and first pages and files the document as an Offering Memorandum, T-12,
P&L, STR report, market study, benchmark report, rent roll, hotel program, capital plan, insurance,
tax or contract. A document dropped without a tag is marked “Classified automatically — confirm” in
the Data Room until you confirm or change the type.

:::

::: stage {n=3 icon=BarChart3}
#### Extract

Every grounded figure, identifier and date is pulled into a typed list, each entry carrying the page
it was read from and a confidence score. Anything not grounded in the source is dropped. A field
below the review threshold is flagged in the Data Room and in the Historical P&L until you accept or
correct it.

:::

::: stage {n=4 icon=Database}
#### Reconcile

When a T-12 or P&L carries its own summary sheet, the extracted lines are checked against it: a
department total that disagrees with the statement's own summary by more than five percent is
replaced with the summary value, and the original is kept beside it for review. A revenue line far
below the deal's other full-period statements is held for review so a single outlet line can never
pass as the department total.

:::

::: stage {n=5 icon=Link2}
#### Verify

A second pass re-reads each cited figure against the page it came from. A figure that matches earns
a high confidence; one that does not is lowered so it shows as “to review”. The confidence badge
beside each field is this result.

:::

::: stage {n=6 icon=ArrowRight}
#### File the period

The statement's own period — a full year, a year to date or a trailing twelve months — is read off
the document, so a single-month upload never outranks an annual statement when the model chooses its
Year-1 basis.

:::

:::

::: card
#### Jobs that stall are timed out, never left spinning

A document that stops moving for half an hour is marked Failed with the stage it stopped in and the
time elapsed, and the Data Room offers Retry. Nothing is wrong with the file — the same bytes run
again from the start. A retry on a document that is still moving is refused, so two runs never work
on one file at once.

:::

::: card
#### Re-running extraction

Any finished document can be extracted again from the Data Room — one at a time, or every document
on the deal. A confirmation step says what will happen first. The re-run is fresh under the current
version of the pipeline. Field reviews made on the previous result (accepted, edited or rejected
values) are not carried over, so anything the new result flags needs reviewing again; the previous
result is kept as history. The document type and year you set are kept, and a disagreement with the
new read is flagged for confirmation. Your model assumptions and their justification notes live on
the deal and are untouched. When the last document finishes, the model runs once.

:::

::: card {testid=methodology-duplicate-deal}
#### Duplicating a deal before re-testing

Because a re-extraction does not carry field reviews over, “Duplicate deal” in the deal header menu
makes a working copy first. The copy carries the deal details, every assumption with its
justification note, the scenarios, and every document with its latest extraction — including the
fields you accepted, edited or rejected. The documents point at the same stored files; nothing is
uploaded again. P&L cell comments are copied only when you tick the box in the confirmation. Model
runs and the activity history are not copied: the copy shows no results until the model is run on
it. The original is not changed.

:::

:::

<!-- section 1: Extraction workflow — from EngineeringMethodology.tsx lines 97-159 -->
::: engineering {title="Extraction workflow"}
::: intro
Every uploaded document moves through six stages — parse, classify, extract, reconcile, verify,
reclassify. The pipeline is format-agnostic by design: a single OM may be a text PDF, scanned image,
multi-tab Excel, or PowerPoint deck.
:::

::: stages
::: stage {n=1 icon=FileText}
#### Parse

The parser reads raw bytes into text + tables. PDFs use LlamaParse (when configured) with a PyMuPDF
fallback. Excel (.xlsx / .xlsm) uses openpyxl; legacy .xls uses xlrd. PowerPoint (.pptx) uses
python-pptx. Image-only PDFs without an OCR layer surface as error_kind=no_text so the user gets an
actionable retry path.

:::

::: stage {n=2 icon=Sparkles}
#### Classify

A Haiku 4.5 Router agent reads the filename plus the first ~2K characters and emits a doc_type: OM,
T12, PNL, STR, STR_TREND, CBRE_HORIZONS, PNL_BENCHMARK, RENT_ROLL, MARKET_STUDY, ROOM_MIX, or
CONTRACT. A filename heuristic provides the fallback when the Router is unsure. Files dropped in the
“Drop everything here” zone carry no analyst tag, so the Router’s doc_type is the category; the Data
Room marks them “Classified automatically — confirm” until the analyst confirms or reclassifies.
Accepted formats: PDF, Excel (.xlsx / .xlsm / .xls), CSV, Word (.docx) and PowerPoint (.pptx) —
Google Sheets / Slides via File → Download → Excel / PowerPoint.

:::

::: stage {n=3 icon=BarChart3}
#### Extract

A Sonnet 4.6 Extractor agent loads a per-doc-type schema
(apps/worker/app/agents/extraction_schemas/) and pulls every grounded number, identifier, and date
into a flat list of typed ExtractionField rows: field_name, value, unit, source_page, confidence,
raw_text. Anything not grounded in the source is dropped.

:::

::: stage {n=4 icon=Database}
#### Reconcile

When a P&L-family workbook (T-12 / P&L) carries its own USALI Summary sheet, a deterministic
reconciler reads that sheet by row label — Rooms, Food & Beverage, Other Operated, Total Revenues,
the departmental and undistributed expense lines, GOP, EBITDA — and takes the annual column that
sums the twelve months; any canonical USALI total the extractor read from a department or outlet
sheet that differs from the Summary by more than 5% is replaced with the Summary value, marked
reconciled with the original value and source kept beside it. A second per-deal check caps a
statement's F&B, Rooms or Total revenue at 50% confidence when it is below a quarter of the deal's
other full-period statements for the same line, so a single-outlet GL line can never pass as the
department total unreviewed.

:::

::: stage {n=5 icon=Link2}
#### Verify

A Critic pass re-reads each cited number against the parser cache. Verified fields get a 0.98
confidence floor; mismatches drop to 0.50. The downstream UI surfaces this as the field-level
confidence badge.

:::

::: stage {n=6 icon=ArrowRight}
#### Reclassify

A post-extraction reclassifier reads p_and_l_usali.period_type off the extracted fields and narrows
broad PNL/T12 classifications into PNL_MONTHLY, PNL_YTD, or T12. This is why a single-month upload
no longer outranks an annual T-12 in the engine actuals loaders.

:::

:::

::: card
#### Stuck jobs time out; they never spin forever

Every status change on a document is time-stamped (`status_changed_at`), and every stage log line
carries the job id that drove it. A worker-side watchdog sweeps at startup and once a minute: any
document still Parsing, Classifying or Extracting with no status change for
`EXTRACTION_STUCK_MINUTES` (default 30) is marked Failed with `error_kind=timeout`, a message naming
the stage and the elapsed time, and an audit entry (`document.extraction_timed_out`). The Data Room
shows it as “Timed out after N min — Retry”. A job that dies with an exception — including a worker
restart mid-extraction — is failed on the spot (`error_kind=cancelled`) rather than left in flight.
Retry on a document that is still moving and younger than the threshold is refused, so two pipelines
never run on one file; once the threshold has passed, Retry (or a forced re-extraction) is accepted
and audited. Nothing is wrong with the file in either case — the same bytes run again from the
start.

:::

::: card
#### Re-running extraction

Any document that has finished — extracted or failed — can be extracted again from the Data Room:
the re-run icon on its row, or “Re-run extraction for all documents” in the coverage header. A
confirm step says what will happen first. An extracted document goes back through `POST …/extract`;
a failed one through `POST …/reprocess`, which re-parses it from the stored file. Either way the run
is fresh under the current pipeline version: it never reuses the content-hash cache or a sibling
template mapping, so a pipeline change actually reaches documents uploaded before it. Each request
is audited as `document.re_extract_requested` with the person who asked, the previous status and the
pipeline version. A document still in the pipeline and younger than `EXTRACTION_STUCK_MINUTES` is
refused, and the action is greyed out with that reason meanwhile. The re-run writes a new extraction
result and every reader takes the newest one. Field reviews made on the previous result (accepted,
edited or rejected values) are not carried over, so anything the new result flags needs reviewing
again. The previous result stays in the database as history. The document type and year the analyst
set are kept as their input, and a disagreement with the new read is flagged for confirmation, as on
first extraction. Deal-level model overrides and their justification notes live on the deal and are
untouched. Progress shows through the normal status polling, and when the last document in the
re-run finishes, the model runs once.

:::

::: card {testid=methodology-duplicate-deal}
#### Duplicating a deal before re-testing

Because a re-extraction does not carry field reviews over, “Duplicate deal” in the project header
menu makes a working copy first (`POST /deals/{id}/duplicate`). The copy lands in the same workspace
as “\<name\> (copy YYYY-MM-DD)” and carries the deal details, every model override with its
justification note, the scenarios, and every document with its latest extraction result, including
the fields the analyst accepted, edited or rejected, the document type and year the analyst set, and
the subtype. The documents point at the same stored files; nothing is uploaded again. P&L cell
comments are a per-copy choice: the confirm dialog's “Also copy P&L cell comments” box is off by
default, and only when it is ticked do the comment threads come along, each marked “\[Copied from
\<deal\>\]” and carrying the source deal's id, with their cell references re-pointed at the copied
documents; otherwise the response lists them as not copied. Model runs are not copied, and neither
is the activity history: the copy shows no results until the model is run on it, and its history
starts with one `deal.duplicated` entry naming the source deal. Duplicating is refused while a
document is still being processed. The original deal is not changed, and deleting the copy or one of
its documents never removes a file the original still uses.

:::

:::

