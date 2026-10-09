/**
 * The project-page tab registry (`/projects/[id]?tab=<id>`).
 *
 * Lives outside `app/projects/[id]/page.tsx` because a Next.js page module may
 * only export its default component — and the registry is what the tab-order
 * test pins.
 *
 * FON-52 — investor-workflow navigation. Order mirrors how an institutional
 * investor evaluates a deal: source data → underwriting → returns → scenario
 * analysis → IC output. Validation, Forecasting, Analysis and Export are
 * removed from the end-user nav (functionality preserved: forecasting lives in
 * Financials → Projections; Analysis + Export are consolidated into IC Memo;
 * Validation stays engine-internal, still deep-linkable). Activity is
 * admin-only (FON-55).
 *
 * Tester round (R-062 / R-063 / R-069) — LABELS changed, ids did NOT, so every
 * existing `?tab=` deep link and bookmark keeps working:
 *   R-062  Investment → "CAPEX", placed immediately after Overview.
 *   R-063  Market     → "Market Comps".
 *   R-069  Debt       → "Financing".
 *
 * Tester round (R-061 / R-073):
 *   R-061  The Returns tab is gone — its content is the "Returns" section at
 *          the foot of Overview. `?tab=returns[&sub=…]` deep links redirect to
 *          `?tab=overview&sub=…` (lib/returnsSection.ts), so no bookmark breaks.
 *   R-073  "Investment Bridge" (equity invested → equity returned, attributed
 *          across acquisition, renovation, operations, financing and exit)
 *          sits immediately before Scenario Analysis, whose lead view is the
 *          Sensitivity analysis.
 */
import {
  FolderOpen, FileText, DollarSign, BarChart3, Activity, Waypoints,
  Briefcase, MapPinned, FileSearch, GitCompareArrows, History, Users,
} from 'lucide-react';

export type ProjectTab = {
  id: string;
  label: string;
  icon: typeof FolderOpen;
  inactive?: boolean;
  /** FON-55 — internal/admin-only tab, hidden from the investor-facing nav. */
  adminOnly?: boolean;
};

/** The user-facing tab names, keyed by the (stable) tab id. Cross-tab link
 *  text should read from here so a rename lands everywhere at once. */
export const TAB_LABEL = {
  dataRoom: 'Data Room',
  overview: 'Overview',
  investment: 'CAPEX',
  market: 'Market Comps',
  pl: 'P&L',
  debt: 'Financing',
  partnership: 'Partnership',
  cashFlow: 'Cash Flow',
  /** R-061 — no longer a tab; the Overview section's heading. */
  returns: 'Returns',
  investmentBridge: 'Investment Bridge',
  scenarios: 'Scenario Analysis',
  icMemo: 'IC Memo',
  activity: 'Activity',
} as const;

export const PROJECT_TABS: ProjectTab[] = [
  { id: '', label: TAB_LABEL.dataRoom, icon: FolderOpen },
  { id: 'overview', label: TAB_LABEL.overview, icon: FileText },
  { id: 'investment', label: TAB_LABEL.investment, icon: Briefcase },
  { id: 'market', label: TAB_LABEL.market, icon: MapPinned },
  { id: 'pl', label: TAB_LABEL.pl, icon: BarChart3 },
  { id: 'debt', label: TAB_LABEL.debt, icon: DollarSign },
  { id: 'partnership', label: TAB_LABEL.partnership, icon: Users },
  { id: 'cash-flow', label: TAB_LABEL.cashFlow, icon: Activity },
  { id: 'investment-bridge', label: TAB_LABEL.investmentBridge, icon: Waypoints },
  { id: 'scenarios', label: TAB_LABEL.scenarios, icon: GitCompareArrows },
  { id: 'ic-memo', label: TAB_LABEL.icMemo, icon: FileSearch },
  { id: 'activity', label: TAB_LABEL.activity, icon: History, adminOnly: true },
];
