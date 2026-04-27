# Fondok AI — Complete Site Walkthrough & Rebuild Spec

> Source: https://fondokai.lovable.app/
> Goal: Pixel-level UI specification — every page, every click, every link.

---

## 1. Product summary

Fondok AI is an AI-powered hotel acquisition underwriting platform. An analyst uploads deal documents (Offering Memorandum, T-12, STR reports, etc.); the system extracts financial fields, runs an underwriting model with multiple "engines" (Investment, P&L, Debt, Cash Flow, Returns, Partnership), produces market analysis, and exports IC memos / Excel models / PowerPoint decks. The persona is **Eshan Mehta — Senior Analyst** at **Brookfield Real Estate (Pro Plan)**.

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

Tab values: `overview`, `investment`, `pl`, `debt`, `cash-flow`, `returns`, `partnership`, `market`, `analysis`, `export`. Default = Data Room.

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

## 7. Project detail tabs (11)

`Data Room` | `Overview` | `Investment` | `P&L` | `Debt` | `Cash Flow` | `Returns` | `Partnership` | `Market` | `Analysis` | `Export`

Engine tabs share: header card, Outputs chips, Export to Excel, Run Model, sub-tabs, right rail, legend.

## 8. Data Library

3 tabs: Comp Sets / Market Data / Templates.

## 9. Settings

4 tabs: Team / Workspace / Notifications / Integrations.

---

See https://fondokai.lovable.app/ for live reference. This file is the contract for the UI rebuild.
