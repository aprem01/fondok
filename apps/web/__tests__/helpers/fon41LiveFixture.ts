/**
 * FON-41 LIVE-SHAPED fixture — generated from the extraction data the
 * coordinator captured on Sam’s deal e577f547 (2026-09-09): four financial
 * statements (March-2025 T-12, 2024 / 2023 / 2019 annual P&Ls).
 *
 *   • LOW-CONFIDENCE fields are REAL: exact field_name, value, confidence
 *     (0.5), reviewed=null, source_page as extracted.
 *   • The remaining sampled field names are real; their VALUES are
 *     synthetic placeholders (marked SYNTH) at confidence 0.98 — the test
 *     only needs them to be present, numeric and in-confidence.
 *
 * Used to lock the regression: for each statement, Data Room badge N ==
 * flagged cells in that statement’s Historicals column.
 */
import type { ExtractionField, ExtractionResult, WorkerDocument } from "@/lib/api";

export const LIVE_DEAL_ID = "e577f547-a3cd-4e78-9ee1-8d761b0c4777";
/** Sam’s deal key count (The Angler’s, Miami Beach). */
export const LIVE_KEYS = 132;

const base = (over: Partial<WorkerDocument>): WorkerDocument => ({
  id: "", deal_id: LIVE_DEAL_ID, tenant_id: "t", filename: "", doc_type: "PNL", status: "EXTRACTED",
  uploaded_at: "", content_hash: null, storage_key: null, size_bytes: 1, page_count: 2, parser: null,
  error_kind: null, error_message: null, ...over,
});
const f = (field_name: string, value: unknown, confidence: number, source_page: number): ExtractionField =>
  ({ field_name, value, unit: null, source_page, confidence, raw_text: null, reviewed: null });
const ex = (document_id: string, fields: ExtractionField[]): ExtractionResult => ({
  document_id, status: "EXTRACTED", fields,
  confidence_report: { overall: 0.8, by_field: {}, low_confidence_fields: [], requires_human_review: true },
  agent_version: null, created_at: null,
});

// ── Copy of The Angler_s - March 2025 Financials.xlsx (T12, fiscal_year 2025, 116 fields live; 27 low-confidence)
export const DOC_T12_2025 = base({"id":"9aba088d-c6fd-4324-9282-ba3bbfaeeb89","filename":"Copy of The Angler_s - March 2025 Financials.xlsx","doc_type":"T12","fiscal_year":2025,"extracted_period_year":null,"uploaded_at":"2026-09-08T20:28:14.943584Z"});
export const EX_T12_2025 = ex(DOC_T12_2025.id, [
  // REAL low-confidence, unreviewed fields
  f("p_and_l_usali.income_before_nonop", 4331540, 0.5, 2),
  f("p_and_l_usali.undistributed.total", 3533430, 0.5, 2),
  f("p_and_l_usali.gross_operating_profit", 4970460, 0.5, 2),
  f("p_and_l_usali.fixed_charges.insurance", 1429570, 0.5, 2),
  f("p_and_l_usali.departmental_profit.total", 8503890, 0.5, 2),
  f("p_and_l_usali.fixed_charges.total_nonop", 1984820, 0.5, 2),
  f("p_and_l_usali.departmental_expenses.rooms", 2770770, 0.5, 2),
  f("p_and_l_usali.departmental_expenses.total", 5311430, 0.5, 2),
  f("p_and_l_usali.net_operating_income.ebitda", 2346710, 0.5, 2),
  f("p_and_l_usali.net_operating_income.noi_usd", 1794100, 0.5, 2),
  f("p_and_l_usali.undistributed.sales_marketing", 1161320, 0.5, 2),
  f("p_and_l_usali.monthly.dec_2024.rooms_revenue", 1001890, 0.5, 2),
  f("p_and_l_usali.monthly.feb_2024.rooms_revenue", 1168900, 0.5, 2),
  f("p_and_l_usali.monthly.jan_2024.rooms_revenue", 1040720, 0.5, 2),
  f("p_and_l_usali.monthly.mar_2024.rooms_revenue", 1109860, 0.5, 2),
  f("p_and_l_usali.operating_revenue.misc_revenue", 1247620, 0.5, 2),
  f("p_and_l_usali.monthly.apr_2024.total_revenues", 1319410, 0.5, 2),
  f("p_and_l_usali.monthly.dec_2024.total_revenues", 1434480, 0.5, 2),
  f("p_and_l_usali.monthly.feb_2024.total_revenues", 1686090, 0.5, 2),
  f("p_and_l_usali.monthly.jan_2024.total_revenues", 1504430, 0.5, 2),
  f("p_and_l_usali.monthly.mar_2024.total_revenues", 1589910, 0.5, 2),
  f("p_and_l_usali.monthly.may_2024.total_revenues", 1164850, 0.5, 2),
  f("p_and_l_usali.monthly.nov_2024.total_revenues", 1047680, 0.5, 2),
  f("p_and_l_usali.operating_revenue.rooms_revenue", 9332100, 0.5, 2),
  f("p_and_l_usali.departmental_expenses.food_beverage", 2533250, 0.5, 2),
  f("p_and_l_usali.undistributed.administrative_general", 1291420, 0.5, 2),
  f("p_and_l_usali.operating_revenue.food_beverage_revenue", 3216620, 0.5, 2),
  // sampled in-confidence field names (values SYNTH)
  f("adr_usd", 285, 0.98, 1),
  f("revpar_usd", 205, 0.98, 1),
  f("occupancy_pct", 0.72, 0.98, 1),
  f("p_and_l_usali.available_rooms", 132, 0.98, 1),
  f("p_and_l_usali.total_rooms_sold", 132, 0.98, 1),
  f("p_and_l_usali.fixed_charges.rent", 1000000, 0.98, 1),
  f("p_and_l_usali.monthly.apr_2024.adr", 285, 0.98, 1),
  f("p_and_l_usali.monthly.apr_2024.gop", 1000000, 0.98, 1),
  f("p_and_l_usali.monthly.aug_2024.adr", 285, 0.98, 1),
  f("p_and_l_usali.monthly.aug_2024.gop", 1000000, 0.98, 1),
  f("p_and_l_usali.monthly.dec_2024.adr", 285, 0.98, 1),
  f("p_and_l_usali.monthly.dec_2024.gop", 1000000, 0.98, 1),
  f("p_and_l_usali.monthly.feb_2024.adr", 285, 0.98, 1),
  f("p_and_l_usali.monthly.feb_2024.gop", 1000000, 0.98, 1),
  f("p_and_l_usali.monthly.jan_2024.adr", 285, 0.98, 1),
  f("p_and_l_usali.monthly.jan_2024.gop", 1000000, 0.98, 1),
  f("p_and_l_usali.monthly.jul_2024.adr", 285, 0.98, 1),
  f("p_and_l_usali.monthly.jul_2024.gop", 1000000, 0.98, 1),
  f("p_and_l_usali.monthly.jun_2024.adr", 285, 0.98, 1),
  f("p_and_l_usali.monthly.jun_2024.gop", 1000000, 0.98, 1),
  f("p_and_l_usali.monthly.mar_2024.adr", 285, 0.98, 1),
  f("p_and_l_usali.monthly.mar_2024.gop", 1000000, 0.98, 1),
  f("p_and_l_usali.monthly.may_2024.adr", 285, 0.98, 1),
]);

// ── Copy of Angler_s 2024 Full Year Detailed P&L.xlsm (PNL, fiscal_year 2024, 645 fields live; 0 low-confidence)
export const DOC_PNL_2024 = base({"id":"136a204d-226f-431c-a1da-74d3438feb04","filename":"Copy of Angler_s 2024 Full Year Detailed P&L.xlsm","doc_type":"PNL","fiscal_year":2024,"extracted_period_year":2024,"uploaded_at":"2026-09-08T20:28:14.243745Z"});
export const EX_PNL_2024 = ex(DOC_PNL_2024.id, [
  // REAL low-confidence, unreviewed fields
  // sampled in-confidence field names (values SYNTH)
  f("p_and_l_usali.period_ending", "2024-12-31", 0.98, 1),
  f("p_and_l_usali.period_start", "2024-01-01", 0.98, 1),
  f("p_and_l_usali.period_type", "annual", 0.98, 1),
  f("p_and_l_usali.period_label", "FY2024", 0.98, 1),
  f("occupancy_pct", 0.72, 0.98, 1),
  f("adr_usd", 285, 0.98, 1),
  f("revpar_usd", 205, 0.98, 1),
  f("p_and_l_usali.operating_revenue.rooms_revenue", 1000000, 0.98, 1),
  f("p_and_l_usali.operating_revenue.food_beverage_revenue", 1000000, 0.98, 1),
  f("p_and_l_usali.operating_revenue.misc_revenue", 1000000, 0.98, 1),
  f("p_and_l_usali.operating_revenue.other_revenue", 1000000, 0.98, 1),
  f("p_and_l_usali.operating_revenue.total_revenue", 1000000, 0.98, 1),
  f("p_and_l_usali.departmental_expenses.rooms", 1000000, 0.98, 1),
  f("p_and_l_usali.departmental_expenses.food_beverage", 1000000, 0.98, 1),
  f("p_and_l_usali.departmental_expenses.other_operated", 1000000, 0.98, 1),
  f("p_and_l_usali.departmental_expenses.total", 1000000, 0.98, 1),
  f("p_and_l_usali.undistributed.administrative_general", 1000000, 0.98, 1),
  f("p_and_l_usali.undistributed.information_telecom", 1000000, 0.98, 1),
  f("property_overview.available_rooms_annual", 132, 0.98, 1),
  f("property_overview.rooms_sold_annual", 132, 0.98, 1),
  f("property_overview.rooms_occupied_annual", 132, 0.98, 1),
  f("property_overview.comp_rooms_annual", 132, 0.98, 1),
  f("property_overview.number_of_guests_annual", 132, 0.98, 1),
  f("property_overview.number_of_arrivals_annual", 132, 0.98, 1),
  f("ttm_summary_per_om.occupancy_pct", 0.72, 0.98, 1),
]);

// ── Copy of Angler_s 2023 P&L.xlsx (PNL, fiscal_year 2023, 100 fields live; 23 low-confidence)
export const DOC_PNL_2023 = base({"id":"e37f4379-1760-4ab3-b480-2a48b027e77f","filename":"Copy of Angler_s 2023 P&L.xlsx","doc_type":"PNL","fiscal_year":2023,"extracted_period_year":2023,"uploaded_at":"2026-09-08T20:28:12.806989Z"});
export const EX_PNL_2023 = ex(DOC_PNL_2023.id, [
  // REAL low-confidence, unreviewed fields
  f("property_overview.keys", 132, 0.5, 1),
  f("p_and_l_usali.miscellaneous_income.revenue_usd", 1004790, 0.5, 1),
  f("p_and_l_usali.total_revenues_usd", 12940200, 0.5, 1),
  f("p_and_l_usali.rooms.expense_usd", 2592840, 0.5, 1),
  f("p_and_l_usali.fb.expense_usd", 2040350, 0.5, 1),
  f("p_and_l_usali.total_departmental_expense_usd", 4640390, 0.5, 1),
  f("p_and_l_usali.total_departmental_profit_usd", 8299810, 0.5, 1),
  f("p_and_l_usali.undistributed.administrative_and_general_usd", 1205100, 0.5, 1),
  f("p_and_l_usali.undistributed.sales_and_marketing_usd", 1182350, 0.5, 1),
  f("p_and_l_usali.undistributed.total_undistributed_expenses_usd", 3563340, 0.5, 1),
  f("p_and_l_usali.gop_usd", 4736470, 0.5, 1),
  f("p_and_l_usali.income_before_non_operating_usd", 4331870, 0.5, 1),
  f("p_and_l_usali.non_operating.insurance_usd", 1351730, 0.5, 1),
  f("p_and_l_usali.non_operating.total_non_operating_usd", 1856960, 0.5, 1),
  f("p_and_l_usali.ebitda_usd", 2474910, 0.5, 1),
  f("p_and_l_usali.ebitda_less_replacement_reserve_usd", 1957310, 0.5, 1),
  f("p_and_l_usali.rooms.dept_profit_usd", 7215150, 0.5, 1),
  f("p_and_l_usali.monthly.jan.total_revenue_usd", 1462160, 0.5, 1),
  f("p_and_l_usali.monthly.feb.total_revenue_usd", 1536610, 0.5, 1),
  f("p_and_l_usali.monthly.mar.total_revenue_usd", 1534400, 0.5, 1),
  f("p_and_l_usali.monthly.apr.total_revenue_usd", 1170310, 0.5, 1),
  f("p_and_l_usali.monthly.may.total_revenue_usd", 1083530, 0.5, 1),
  f("p_and_l_usali.monthly.dec.total_revenue_usd", 1438290, 0.5, 1),
  // sampled in-confidence field names (values SYNTH)
  f("property_overview.name", "The Angler’s", 0.98, 1),
  f("property_overview.statement_period_end", "2024-12-31", 0.98, 1),
  f("property_overview.available_rooms_annual", 132, 0.98, 1),
  f("p_and_l_usali.rooms.total_rooms_sold_annual", 132, 0.98, 1),
  f("ttm_summary_per_om.occupancy_pct", 0.72, 0.98, 1),
  f("ttm_summary_per_om.adr_usd", 285, 0.98, 1),
  f("ttm_summary_per_om.revpar_usd", 205, 0.98, 1),
  f("p_and_l_usali.rooms.revenue_usd", 1000000, 0.98, 1),
  f("p_and_l_usali.fb.revenue_usd", 1000000, 0.98, 1),
  f("p_and_l_usali.other_operated_departments.revenue_usd", 1000000, 0.98, 1),
  f("p_and_l_usali.other_operated_departments.expense_usd", 1000000, 0.98, 1),
  f("p_and_l_usali.undistributed.information_telecom_systems_usd", 1000000, 0.98, 1),
  f("p_and_l_usali.undistributed.property_operations_maintenance_usd", 1000000, 0.98, 1),
  f("p_and_l_usali.undistributed.utilities_usd", 1000000, 0.98, 1),
]);

// ── Copy of Angler_s 2019 P&L.xlsx (PNL, fiscal_year 2019, 118 fields live; 16 low-confidence)
export const DOC_PNL_2019 = base({"id":"b9a90f5d-ce38-4b47-babd-bd62c10bdf53","filename":"Copy of Angler_s 2019 P&L.xlsx","doc_type":"PNL","fiscal_year":2019,"extracted_period_year":2019,"uploaded_at":"2026-09-08T20:28:12.219536Z"});
export const EX_PNL_2019 = ex(DOC_PNL_2019.id, [
  // REAL low-confidence, unreviewed fields
  f("p_and_l_usali.rooms.revenue", 6339940, 0.5, 1),
  f("p_and_l_usali.fb.revenue", 1028110, 0.5, 1),
  f("p_and_l_usali.total_revenue", 8385990, 0.5, 1),
  f("p_and_l_usali.rooms.departmental_expense", 1763710, 0.5, 1),
  f("p_and_l_usali.fb.departmental_expense", 1365820, 0.5, 1),
  f("p_and_l_usali.total_departmental_expense", 3407850, 0.5, 1),
  f("p_and_l_usali.total_departmental_profit", 4978140, 0.5, 1),
  f("p_and_l_usali.undistributed.administrative_and_general", 1042260, 0.5, 1),
  f("p_and_l_usali.undistributed.sales_and_marketing", 1152220, 0.5, 1),
  f("p_and_l_usali.total_undistributed_expenses", 3066080, 0.5, 1),
  f("p_and_l_usali.gop", 1912060, 0.5, 1),
  f("p_and_l_usali.income_before_non_operating", 1644310, 0.5, 1),
  f("p_and_l_usali.total_non_operating_expenses", 1221150, 0.5, 1),
  f("p_and_l_usali.rooms.departmental_profit", 4576230, 0.5, 1),
  f("p_and_l_usali.fb.departmental_profit", -337710, 0.5, 1),
  f("p_and_l_usali.monthly.dec.total_revenue", 1154120, 0.5, 1),
  // sampled in-confidence field names (values SYNTH)
  f("property_overview.name", "The Angler’s", 0.98, 1),
  f("property_overview.statement_period", "2024-12-31", 0.98, 1),
  f("property_overview.available_rooms_annual", 132, 0.98, 1),
  f("property_overview.keys", 132, 0.98, 1),
  f("p_and_l_usali.rooms.total_rooms_sold_annual", 132, 0.98, 1),
  f("ttm_performance.subject.occupancy", 0.72, 0.98, 1),
  f("ttm_performance.subject.adr", 285, 0.98, 1),
  f("ttm_performance.subject.revpar", 205, 0.98, 1),
  f("p_and_l_usali.other_operated_departments.revenue", 1000000, 0.98, 1),
  f("p_and_l_usali.miscellaneous_income.revenue", 1000000, 0.98, 1),
  f("p_and_l_usali.other_operated_departments.departmental_expense", 1000000, 0.98, 1),
  f("p_and_l_usali.undistributed.information_telecom_systems", 1000000, 0.98, 1),
  f("p_and_l_usali.undistributed.property_operations_maintenance", 1000000, 0.98, 1),
  f("p_and_l_usali.undistributed.utilities", 1000000, 0.98, 1),
]);

export const LIVE_DOCS: WorkerDocument[] = [DOC_T12_2025, DOC_PNL_2024, DOC_PNL_2023, DOC_PNL_2019];
export const LIVE_EXTRACTIONS: Record<string, ExtractionResult> = {
  [DOC_T12_2025.id]: EX_T12_2025,
  [DOC_PNL_2024.id]: EX_PNL_2024,
  [DOC_PNL_2023.id]: EX_PNL_2023,
  [DOC_PNL_2019.id]: EX_PNL_2019,
};
/** Data Room badges observed live on 2026-09-09 (2019 marked "verify" by QA). */
export const LIVE_BADGES = { T12_2025: 9, PNL_2024: 0, PNL_2023: 6, PNL_2019: 9 };
