/**
 * Plain-English definitions for the dense jargon that runs through hotel
 * underwriting. Used by `MetricLabel` (column headers, KPIs) and `Term`
 * (inline narrative). Keys are case-sensitive — the renderSummary helper
 * matches whole-word acronyms only so we don't wrap "noise" or "branded".
 */
export const GLOSSARY: Record<string, string> = {
  NOI: 'Net Operating Income — total revenue minus operating expenses, before debt service or capital expenditures. The truest measure of a hotel\'s earning power.',
  RevPAR:
    'Revenue per Available Room — average daily rate multiplied by occupancy. Combines pricing power and how full the hotel is into one number.',
  ADR: 'Average Daily Rate — the average price paid per occupied room. Higher ADR means stronger pricing power.',
  IRR: 'Internal Rate of Return — the annualized return on the equity investment over the hold period. The headline metric for return.',
  DSCR: 'Debt Service Coverage Ratio — NOI divided by debt service. Tells you if the hotel earns enough to comfortably pay its loan. Lenders typically require 1.20x or higher.',
  LTV: 'Loan to Value — the loan amount as a percentage of the hotel\'s value. Higher LTV means more leverage, more risk.',
  LTC: 'Loan to Cost — the loan amount as a percentage of the total project cost (purchase + renovation + closing). Common on value-add deals.',
  GOP: 'Gross Operating Profit — total revenue minus departmental and undistributed expenses, before fixed charges, FF&E, and management fees.',
  'FF&E':
    'Furniture, Fixtures, and Equipment — money set aside to replace beds, TVs, carpets, lobby furniture as they wear out. Usually 3–5% of revenue.',
  CoC: 'Cash-on-Cash return — annual cash flow to equity divided by equity invested. Tells you how much cash actually hits your account each year.',
  OpEx: 'Operating Expenses — all the costs of running the hotel: labor, utilities, marketing, etc.',
  PIP: 'Property Improvement Plan — required renovations the brand mandates when you buy a flagged hotel. Can run $5–50M depending on size and condition.',
  STR: 'Smith Travel Research — the gold-standard data provider for hotel performance benchmarks.',
  USALI:
    'Uniform System of Accounts for the Lodging Industry — the standard accounting categories every hotel uses. Lets you compare any two hotels apples-to-apples.',
  OM: 'Offering Memorandum — the broker\'s pitch deck for the deal. Includes the property description, market overview, and the broker\'s proforma projections.',
  'T-12':
    'Trailing Twelve Months — the last 12 months of actual financial performance. The most important document — it\'s reality, not a projection.',
  T12: 'Trailing Twelve Months — the last 12 months of actual financial performance. The most important document — it\'s reality, not a projection.',
  'Cap Rate':
    'Capitalization Rate — NOI divided by purchase price. The annual unlevered yield. Lower cap rate means a more expensive property.',
  'Exit Cap':
    'Exit Capitalization Rate — the cap rate you assume when selling. Determines the exit price.',
  'Equity Multiple':
    'Total cash returned to equity divided by equity invested. A 2.0x means investors got back twice their money over the hold.',
  MOIC: 'Multiple on Invested Capital — same as Equity Multiple. 2.0x = investors doubled their money.',
  'Hold Period':
    'How long you own the hotel before selling. Typically 3–7 years for hotels.',
  Pref: 'Preferred Return — the minimum return LPs (limited partner investors) get before the GP (sponsor) earns any profit share. Usually 8–10%.',
  Promote:
    'The GP\'s share of profits above a hurdle. The way the sponsor gets paid for delivering returns.',
  GP: 'General Partner — the sponsor who runs the deal. Typically puts in 5–10% of equity, gets a promote on outsized returns.',
  LP: 'Limited Partner — outside investors who put in most of the equity. Gets a preferred return first, then splits profits with the GP per the waterfall.',
  Waterfall:
    'The rule for splitting cash distributions between GP and LPs. Usually paid in tiers: pref first, then promoted splits as returns climb.',
  'Debt Yield':
    'Year-1 NOI divided by the loan amount. A floor metric lenders use — typically 8–10% — to make sure the hotel can service the loan even if values fall.',
  'Debt Service':
    'The annual cash payments on the loan — interest plus principal, if amortizing.',
  'Price/Key':
    'Purchase price divided by number of guest rooms. A quick yardstick for whether you\'re buying cheap or expensive vs. the comp set.',
  'Per Key':
    'Any per-room figure (rev/key, NOI/key, debt/key). A normalized metric that lets you compare hotels of different sizes.',
  Reversion:
    'The sale of the hotel at the end of the hold period. Reversion proceeds = the cash you get from the buyer at exit.',
  Sources:
    'How the deal is funded — the mix of senior loan, preferred equity, GP equity, and LP equity.',
  Uses: 'Where the money goes — purchase price, closing costs, renovation budget, working capital.',
  Refi: 'Refinancing — replacing the original loan with a new one mid-hold, usually to pull out cash once the hotel\'s NOI has grown.',
  SOFR: 'Secured Overnight Financing Rate — the benchmark short-term rate that variable hotel loans price off of. Replaced LIBOR.',
  Spread:
    'How many basis points (1bp = 0.01%) the lender charges over SOFR. Riskier deals get wider spreads.',
  IO: 'Interest-Only — a period at the start of the loan when you pay only interest, no principal. Boosts early-year cash flow.',
  'Cash Trap':
    'A lender covenant that diverts cash flow away from equity distributions if performance dips below a threshold (usually DSCR or debt yield).',
  Covenant:
    'A promise to the lender — typically a minimum DSCR, debt yield, or LTV. Breaching it can trigger cash trap or default.',
  Origination:
    'The fee the lender charges to issue the loan, usually 1–1.5% of the loan amount. Paid up front.',
  Amortization:
    'The schedule for paying down the loan principal. A 30-year amortization with a 5-year term means you pay it down like a 30-year mortgage but the balloon is due in 5.',
  Levered:
    'After accounting for debt. Levered IRR is what equity investors actually earn after debt service.',
  Unlevered:
    'Before debt — the pure asset-level return, as if the hotel were paid for in cash.',
  EM: 'Equity Multiple — total cash returned to equity divided by equity invested. 2.0x = doubled.',
  RGI: 'Revenue Generation Index — your RevPAR divided by the comp set\'s RevPAR. >1.0 means you\'re outperforming the comp set.',
  ARI: 'Average Rate Index — your ADR divided by the comp set\'s ADR. >1.0 means you\'re pricing higher than the comp set.',
  MPI: 'Market Penetration Index — your occupancy divided by the comp set\'s occupancy. >1.0 means you\'re running fuller than peers.',
  GBA: 'Gross Building Area — total square footage of the hotel.',
  'F&B': 'Food and Beverage — restaurant, bar, banquet, and room-service revenue and expenses.',
  'Working Capital':
    'Cash the buyer leaves on the books at closing to fund day-one operations (payroll, vendor float).',
  'Closing Costs':
    'One-time costs at acquisition — title, legal, transfer tax, lender fees. Usually 1–3% of purchase price.',
  Contingency:
    'A reserve in the renovation budget for surprises (unexpected demolition costs, code upgrades, etc.). Usually 5–10% of hard costs.',
  Stabilized:
    'When the hotel\'s revenue and NOI plateau after ramp-up. Most underwriting projects stabilization by year 2–3.',
  'Ramp-Up':
    'The period after a renovation or rebrand when revenue is climbing toward stabilized levels.',
  'Key Money':
    'A check the brand cuts to the owner at signing of the franchise/management agreement, in exchange for a long-term flag commitment.',
  Critic:
    'Fondok\'s second-pass agent that reads cross-field inconsistencies in the broker proforma after the per-field Variance pass.',
  Variance:
    'The gap between what the broker projects and what the T-12 actuals show. Material variance is a deal-breaker or a negotiation lever.',
};

export function getDefinition(term: string): string | undefined {
  return GLOSSARY[term];
}
