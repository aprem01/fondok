# STR_SEGMENTATION (STR Demand / Segmentation Report)

A STR Segmentation report decomposes the subject hotel's demand by
**market segment** (transient vs. group, sometimes split further by
channel — Direct / OTA / Brand.com / Voice). Distinct from `STR_TREND`,
which gives property-level Occ / ADR / RevPAR over time but does not
break revenue down by segment or channel.

This report is the **single biggest credibility unlock** for an
institutional revenue projection: without it the underwriter has to
guess the OTA mix, and a 1-percentage-point miss on OTA share at a
20% commission rate moves rooms revenue by ~0.2% of total — material
on a $30M acquisition.

## When the Router classifies a document as STR_SEGMENTATION

- Filename frequently contains "segmentation", "demand mix", "segment
  performance", or just "segment".
- Body text references "Transient" and "Group" rows alongside Occupancy
  / ADR / RevPAR (not just the property total).
- Optional channel-mix block lists Direct / OTA / Brand.com / Voice
  shares of room nights or revenue.

Do **NOT** classify as STR_SEGMENTATION when the report only shows
property-level Occ/ADR/RevPAR with no segment split — that is
STR_TREND. Do **NOT** classify as STR_SEGMENTATION when the document
is a marketing pitch deck on brand share gains — that is MARKET_STUDY.

## Canonical field-path namespace

### Document-level metadata
- `str_segmentation.report_year` — integer year the segmentation
  report covers (use the period_ending year on the cover page).

### Per-period blocks
The report typically carries three periods side-by-side — Trailing
Twelve Months (`ttm`), Month-to-Date (`mtd`), Year-to-Date (`ytd`).
Each emits a property-overall row plus per-segment rows.

For each period `<p>` ∈ `{ttm, mtd, ytd}`:

#### Property overall
- `str_segmentation.<p>.overall.occupancy_pct`
- `str_segmentation.<p>.overall.adr_usd`
- `str_segmentation.<p>.overall.revpar_usd`

#### Transient segment
- `str_segmentation.<p>.transient.occupancy_pct`
- `str_segmentation.<p>.transient.adr_usd`
- `str_segmentation.<p>.transient.revpar_usd`
- `str_segmentation.<p>.transient.mix_pct` — share of total room
  nights (0..1 ratio). REQUIRED — the revenue engine reads this to
  size the transient bucket.

#### Group segment
- `str_segmentation.<p>.group.occupancy_pct`
- `str_segmentation.<p>.group.adr_usd`
- `str_segmentation.<p>.group.revpar_usd`
- `str_segmentation.<p>.group.mix_pct` — share of total room nights.

#### Contract segment (rare; airline crew / sports / extended stay)
- `str_segmentation.<p>.contract.occupancy_pct`
- `str_segmentation.<p>.contract.adr_usd`
- `str_segmentation.<p>.contract.revpar_usd`
- `str_segmentation.<p>.contract.mix_pct` — share of total room nights.

### Optional channel-mix block (within transient demand)
When the report breaks transient demand by booking channel, emit shares
as 0..1 ratios (NOT percentages). Voice channel rolls up into the BAR
(direct) bucket when the engine builds default segments.

- `str_segmentation.<p>.channel_mix.direct_pct`
- `str_segmentation.<p>.channel_mix.ota_pct`
- `str_segmentation.<p>.channel_mix.brand_pct`
- `str_segmentation.<p>.channel_mix.voice_pct`
- `str_segmentation.<p>.channel_mix.corporate_pct` — when explicitly
  broken out as a channel rather than a separate segment row.

## Notes for the Extractor

1. **Mix percentages are room-night shares**, not revenue shares.
   When the report only publishes a revenue share, label that field
   `<p>.<seg>.revenue_mix_pct` instead — the engine will fall back to
   default ADR ratios and reconstruct the room-night share.
2. **Normalize percentages**: extract as `0..1` ratios. The downstream
   normalizer treats values > 1.0 as percentages and divides by 100,
   but emitting in the canonical form keeps audit trails clean.
3. **Period preference**: when the engine pulls defaults it reads the
   TTM block first (most comparable to an annual baseline), falls back
   to YTD, then MTD. Always extract whatever periods the report carries
   — the engine selects.
4. **Don't invent contract demand**: real airline-crew / sports
   contract rows are rare. If the report doesn't break them out,
   leave the `contract.*` fields empty rather than synthesizing a
   "contract = 0" record. The engine knows to default contract to 0%.
