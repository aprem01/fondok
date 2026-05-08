# Fondok AI — Complete Site Walkthrough & Rebuild Spec

> Source: https://fondokai.lovable.app/
> Goal: Pixel-level UI specification — every page, every click, every link.

---

## 1. Product summary

Fondok AI is an AI-powered hotel acquisition underwriting platform. An analyst uploads deal documents (OM, T-12, STR CoStar Trend, CBRE Hotel Horizons, USALI 11th P&L Benchmarker, term sheets); the system extracts financial fields with citations back to source pages, runs an underwriting model with eight deterministic engines (Revenue, F&B, Expense, Capital, Debt, Returns, Sensitivity, Partnership), produces market analysis, generates a broker due-diligence packet, and exports IC memos / Excel models / PowerPoint decks. The persona is **Eshan Mehta — Senior Analyst** at **Brookfield Real Estate (Pro Plan)**.

## 2. Top-level routes

| Route | Page |
|---|---|
| `/` → `/dashboard` | Dashboard |
| `/dashboard` | Portfolio overview |
| `/projects` | List (grid + list view) |
| `/projects/new` | 6-step new project wizard |
| `/projects/:id` | Detail (default = Data Room) |
| `/projects/:id?tab=<name>` | Deep-linkable tabs |
| `/data-library` | Comp Sets / Market Data / Templates |
| `/settings` | Team / Workspace / Notifications / Integrations |

Tab values: `data-room`, `overview`, `investment`, `pl`, `market`, `analysis` are **active**. `debt`, `cash-flow`, `returns`, `partnership`, `export` are **grayed out** ("Soon" badge) per the May 7 scope decision. Default = Data Room.

## 3. Global layout

Two-column: fixed left sidebar (~216px) + content area.

**Sidebar:**
1. Logo block (Fondok AI mark + wordmark)
2. Workspace switcher (Brookfield Real Estate, Pro Plan)
3. Nav: Dashboard, Projects, Data Library, Settings
4. User block (EM, Eshan Mehta, Senior Analyst)

**Content:** page title + subtitle + primary CTA top-right.

## 4. Dashboard

4 stat cards: Active Projects 4, Documents Processed 16, Total Deal Volume $461.9M, Avg Time to IC —.
Recent Projects panel (4 rows, links to /projects/:id).
Team Activity (empty), AI Insights (empty).

## 5. Projects list

Search + status filter (All/Draft/In Review/IC Ready/Archived) + grid/list toggle + + New Project.
4 sample projects per the seed data.
Hyatt Regency Waterfront has the "no documents" upload state.

## 6. New Project wizard (6 steps)
1. Deal Details (name, city, keys, deal stage, optional hotel name + price)
2. Return Profile (Core / Value Add / Opportunistic)
3. Documents (drop zone, skippable)
4. Brand selection (Brand Agnostic + 12 family expandable groups)
5. Positioning (Default / Luxury / Upscale / Economy)
6. Review & Create Shell Deal

## 7. Project detail tabs (6 active + 5 grayed-out)

**Active (May 7 scope):** `Data Room` | `Overview` | `Investment` | `P&L` | `Market` | `Analysis`

**Grayed out ("Soon"):** `Debt` | `Cash Flow` | `Returns` | `Partnership` | `Export`

The active tabs share a header card, Outputs chips, Export to Excel, Run Model, sub-tabs, right rail, and engine legend. Grayed-out tabs render with italic + strike-through label and an amber "Soon" badge — clicks no-op.

**P&L sub-tabs** (Lovable parity, batch 2):
1. **P&L Summary** — single-period operating statement
2. **Historicals** — multi-year proforma with Amount / %Rev / $PAR / $POR per year
3. **Projections** — base + 5-year forecast with AI NOI Summary button
4. **Index Analysis** — subject vs CoStar comp set, 2019–2033 (15-year columns)
5. **Competitive Set** — comp-set fan-out from STR Trend extraction
6. **Due Diligence** — broker-question packet (filterable + Copy/Export/Mark as Sent)

## 8. Data Library

3 tabs: Comp Sets / Market Data / Templates.

## 9. Settings

4 tabs: Team / Workspace / Notifications / Integrations.

---

See https://fondokai.lovable.app/ for live reference. This file is the contract for the UI rebuild.
