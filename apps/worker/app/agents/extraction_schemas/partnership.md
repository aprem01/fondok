# Partnership / JV / Equity Waterfall

A prose legal document — an LLC operating agreement, a joint-venture
agreement, or an equity term sheet — that lays out the deal's equity
economics: the GP/LP ownership split, the preferred return, and the
promote (carried-interest) waterfall. Unlike the structured Fondok
upload template (a labeled grid handled deterministically at $0), a
real operating agreement states these terms in sentences and
defined-term paragraphs. Your job is to read the meaning and map it
onto the canonical paths below — the downstream Partnership engine
reads them verbatim, with NO further normalization.

## Canonical field-path namespace

### Ownership + preferred return (scalars)

- `partnership.gp_equity_pct` — sponsor / general-partner /
  managing-member ownership share. Source aliases: "Sponsor", "GP",
  "General Partner", "Managing Member", "Promote Member", "Class B".
- `partnership.lp_equity_pct` — investor / limited-partner /
  capital-member ownership share. Source aliases: "LP", "Limited
  Partner", "Investor Member", "Capital Member", "Class A".
- `partnership.pref_rate` — the preferred return / priority return the
  LP earns before any promote. Source aliases: "Preferred Return",
  "Pref", "Priority Return", "Preferred Distribution".

### Promote waterfall (indexed tiers)

`partnership.waterfall.<idx>.*` where `<idx>` is a 0-based integer that
counts UP from the lowest hurdle:

- `partnership.waterfall.<idx>.hurdle_rate` — the IRR (or return)
  hurdle at the top of promote tier `<idx>`.
- `partnership.waterfall.<idx>.gp_split` — the GP/sponsor (promote)
  share of distributions within that tier.
- `partnership.waterfall.<idx>.lp_split` — the LP/investor share
  within that tier.

Tier 0 is the preferred / return-of-capital tier (its hurdle is the
pref itself, commonly GP 0% / LP 100%). Each higher tier is the next
promote band the agreement steps through ("thereafter to a 15% IRR,
80/20"; "above a 20% IRR, 70/30").

## Anti-fabrication rules

- Emit a tier ONLY when you can ground BOTH its `hurdle_rate` AND at
  least one split (`gp_split` or `lp_split`) in the document. If only
  one split side is stated, emit just that side — the engine derives
  the complement so the two sum to 1.0. Never invent the missing
  hurdle or a split you did not read.
- Number tiers CONTIGUOUSLY from 0 with no gaps. If the agreement
  describes two promote bands, emit only tiers 0 and 1. Do not pad to
  a fixed count.
- If the document states ownership and pref but no promote waterfall,
  emit just the scalars and NO `partnership.waterfall.*` fields.
- If a term is absent or you cannot ground it, omit it. A missing
  field is correct; a guessed number is a defect.

## Units — CRITICAL

Every partnership value is a **0..1 fraction** with `unit` = `ratio`:

- 90% LP ownership → `0.90`
- 10% GP ownership → `0.10`
- 8% preferred return → `0.08`
- 20% promote in a tier → `0.20`

Do NOT emit `90`, `8`, or `20`. The downstream loader applies these
values verbatim with no percent-to-fraction conversion, so a raw
percent would be read as a 9000% / 800% / 2000% share. When a number
could be either an ownership fraction or a dollar capital contribution,
only the ownership fraction belongs under these paths.

## How `hurdle_rate` maps to the model

The engine sorts tiers by `hurdle_rate` ascending and steps the LP up
one band at a time as each hurdle is cleared. So `hurdle_rate` is the
IRR/return threshold that DEFINES the band, and tier 0 (lowest hurdle)
is the preferred-return band. The highest-hurdle tier also absorbs any
residual "thereafter" distributions, so the top promote split should be
the top tier. Read each stated threshold as the hurdle for the band it
gates.

## Example (prose → fields)

Source: "The Managing Member (Sponsor) holds a 10% membership interest
and the Investor Members hold the remaining 90%. Distributions are made
first to the Investor Members until they receive a return of capital
plus an 8% preferred return; then 80% to the Investor Members and 20%
to the Sponsor until the Investor Members achieve a 15% IRR; then 70%
to the Investor Members and 30% to the Sponsor until they achieve a 20%
IRR; and thereafter 60% to the Investor Members and 40% to the Sponsor."

```
partnership.gp_equity_pct = 0.10        (unit: ratio)
partnership.lp_equity_pct = 0.90        (unit: ratio)
partnership.pref_rate = 0.08            (unit: ratio)

partnership.waterfall.0.hurdle_rate = 0.08   (the pref band)
partnership.waterfall.0.gp_split = 0.00
partnership.waterfall.0.lp_split = 1.00

partnership.waterfall.1.hurdle_rate = 0.15
partnership.waterfall.1.gp_split = 0.20
partnership.waterfall.1.lp_split = 0.80

partnership.waterfall.2.hurdle_rate = 0.20
partnership.waterfall.2.gp_split = 0.30
partnership.waterfall.2.lp_split = 0.70
```

Note the "thereafter 60/40" residual above 20% carries no stated
ceiling. When a residual band has no numeric hurdle of its own, do NOT
invent one — leave it out. The analyst adds a top residual band from
the Partnership tab, where an explicit hurdle is entered by hand. Emit
only the bands whose hurdle you actually read.
