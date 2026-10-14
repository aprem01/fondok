# CAPEX (PIP / renovation budget / capital plan / historical capex)

A capital-expenditure document for the subject hotel. Four shapes arrive
under this one doc type, and the canonical namespace you emit under is
decided by WHAT THE DOCUMENT IS, never by its filename:

1. **A PIP or renovation budget** — a brand-issued Property Improvement
   Plan priced out, an owner's renovation budget, an approved capital
   plan for the day-one renovation, or a contractor / consultant
   estimate. States ONE renovation budget (a total, usually with a
   hard / soft / contingency split and a scope-of-work table).
   → `capex_plan.pip.*` + `capex_plan.document.*`.
2. **A forward (hold-period) capital plan** — a multi-year schedule of
   future capital spend by year ("2027 $450K roof", "2028 $1.2M
   soft-goods refresh"). This is NOT the day-one renovation.
   → `future_capex.*`. Downstream treats it as reference only; it is
   never read into the model.
3. **A historical capex ledger** — spend already incurred, by year.
   → `historic_capex.*`. Reference only.
4. **An FF&E reserve report** — the reserve balance / contributions.
   → `ffe_reserve.*`. Reference only.

Downstream reads `capex_plan.pip.total_usd` as a CANDIDATE for the
model's renovation budget (FON-101: a PIP / CapEx document outranks the
OM's broker figure when both state one). Nothing else in this schema
moves a modelled number, so the first rule is: emit `capex_plan.pip.*`
ONLY for shape 1. A forward plan's grand total is `future_capex.total_usd`,
never `capex_plan.pip.total_usd`.

## Canonical field-path namespaces

### `capex_plan.pip.<line>` — the day-one renovation budget (shape 1 only)
- `capex_plan.pip.total_usd` — the document's stated TOTAL renovation /
  PIP budget. Emit the grand total the document itself prints; do not
  sum line items into a total the document never states.
- `capex_plan.pip.per_key_usd` — the budget per key when the document
  prints it.
- `capex_plan.pip.hard_usd` — hard costs (construction, FF&E, OS&E).
- `capex_plan.pip.soft_usd` — soft costs (design, permits, fees,
  project management).
- `capex_plan.pip.contingency_usd` — the contingency line as a dollar
  amount. If the document states contingency only as a percent, emit
  `capex_plan.pip.contingency_pct` (0..1 fraction) instead.
- `capex_plan.pip.completion_quarter` — when the renovation completes,
  as printed ("Q3 2027") or ISO date.
- `capex_plan.pip.timing_pct_by_year` — when the document gives a spend
  schedule by year, emit one row per year as
  `capex_plan.pip.timing.<YYYY>.pct` (0..1 fraction of the total) or
  `capex_plan.pip.timing.<YYYY>.usd` (dollars) — whichever the document
  prints. Never convert one into the other.

### `capex_plan.pip.scope_items.<n>.*` — the scope-of-work table
One group per priced scope line, `<n>` 1-indexed in document order:
- `capex_plan.pip.scope_items.<n>.name` — the line as printed
  ("Guestroom soft goods", "Lobby renovation", "Roof replacement").
- `capex_plan.pip.scope_items.<n>.usd` — the line's dollar amount.
- `capex_plan.pip.scope_items.<n>.category` — one of: `guestrooms`,
  `public_space`, `fb_outlets`, `meeting_space`, `exterior`,
  `building_systems`, `technology`, `ffe`, `ose`, `soft_costs`,
  `contingency`, `other`. Pick from the line's wording; use `other`
  rather than guessing.
- `capex_plan.pip.scope_items.<n>.brand_required` — `true` when the
  document marks the item as brand-mandated (a PIP "required" column),
  `false` when it is marked optional / owner-elective. Omit when the
  document does not say.

The downstream critic checks that `capex_plan.pip.total_usd` is within
2% of the sum of the scope items when both are present, and flags (never
alters) the extraction when they disagree — so emit the items exactly as
printed and let the check do its job.

### `capex_plan.document.*` — what kind of document this is, and when
- `capex_plan.document.plan_date` — ISO date the budget / plan was
  issued, approved or revised (the document's own date, not the upload
  date). Prefer the latest revision date the document prints.
- `capex_plan.document.status` — one of exactly:
  `brand_issued_pip` (the brand's / franchisor's PIP), `owner_budget`
  (the owner's or sponsor's internal budget), `approved_capital_plan`
  (a board- or lender-approved plan), `estimate` (a contractor,
  consultant or PCA estimate). Emit the one the document's own wording
  supports; omit when none does.
- `capex_plan.document.prepared_by` — the issuing party as printed
  (brand, consultant, GC, owner entity).
- `capex_plan.document.brand` — the brand the PIP is written against,
  when stated.
- `capex_plan.document.scope_keys` — the key count the budget is
  written for, when stated (so per-key math downstream is grounded).

### `future_capex.*` — forward hold-period capital plan (shape 2)
REFERENCE ONLY downstream — these are never read into the model.
- `future_capex.items.<n>.name` — the project as printed.
- `future_capex.items.<n>.year` — the calendar or fiscal year of the
  spend (four-digit integer). When the document gives a quarter or a
  date, emit the year here and the printed value under
  `future_capex.items.<n>.timing`.
- `future_capex.items.<n>.usd` — the dollar amount.
- `future_capex.items.<n>.category` — same category vocabulary as the
  scope items above.
- `future_capex.total_usd` — the plan's grand total when printed.
- `future_capex.plan_date` / `future_capex.prepared_by` — the plan's
  date and author, when printed.

### `historic_capex.*` — spend already incurred (shape 3)
Reference only.
- `historic_capex.items.<n>.name`, `.year`, `.usd`, `.category` — same
  shape as `future_capex.items`.
- `historic_capex.by_year.<YYYY>.total_usd` — the annual total when the
  ledger prints one.
- `historic_capex.total_usd` — the ledger's grand total when printed.

### `ffe_reserve.*` — reserve report (shape 4)
Reference only.
- `ffe_reserve.balance_usd`, `ffe_reserve.balance_date` (ISO),
  `ffe_reserve.annual_contribution_usd`,
  `ffe_reserve.contribution_pct_of_revenue` (0..1 fraction).

## Tie-breaker rules

- A document that is clearly a day-one renovation (a PIP letter priced
  out, "Renovation Budget", "PIP Scope & Budget") is shape 1 even when
  it also schedules the spend across two or three years — that
  schedule is `capex_plan.pip.timing.<YYYY>.*`, not `future_capex`.
- A document whose spend starts AFTER the renovation and runs through
  the hold ("5-year capital plan", "reserve study", "ongoing capital")
  is shape 2 — `future_capex.*` — even if one of its lines is a
  renovation.
- When a single document carries BOTH a day-one PIP section and a
  separate forward plan, emit both namespaces, each from its own
  section. Never let the forward plan's total leak into
  `capex_plan.pip.total_usd`.
- Dollar amounts are whole USD; percentages are 0..1 fractions;
  dates are ISO (`YYYY-MM-DD`, or `YYYY-MM` when only month and year
  are printed).
