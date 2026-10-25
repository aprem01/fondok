// Reference catalogs for the web app — brands, return profiles, positioning
// tiers, deal stages, sourcing channels, document types — plus the legacy demo
// workspace / project list that the mock-id (numeric) routes still read.
//
// Standing rule (no invented data): this file carries NO numeric deal, market
// or analysis fixtures. Every displayed figure traces to an engine output or an
// extracted document field; a figure the engine did not produce renders as "—"
// with the missing field named. The former Kimpton Angler / Miami Beach number
// sets (overview, documents, market, analysis, scenarios, critic findings) were
// removed 2026-10-14 — nothing imported them, and nothing may again.

// The worker-less demo build's persona. Deliberately neutral: it must never
// name a real institution or a real person — if Clerk fails to hydrate on a
// preview deploy, THIS is what every visitor would see in the sidebar.
export const workspace = {
  name: 'Demo Workspace',
  plan: 'Demo',
  url: 'demo',
};

export const currentUser = {
  name: 'Demo Analyst',
  role: 'Analyst',
  initials: 'DA',
  email: 'analyst@example.com',
};

export type ProjectStatus = 'Draft' | 'In Review' | 'IC Ready' | 'Archived';
export type DealStage = 'Teaser' | 'Under NDA' | 'LOI' | 'PSA';
export type Risk = 'Low' | 'Medium' | 'High';

export interface Project {
  id: number;
  name: string;
  city: string;
  keys: number;
  service: string;
  status: ProjectStatus;
  dealStage: DealStage;
  revpar: number;
  irr: number;
  risk: Risk;
  aiConfidence: number;
  assignee: string;
  docs: string;
  noi?: number;
  createdAt?: string;
  updatedAt: string;
  noDocs?: boolean;
}

export const projects: Project[] = [
  {
    id: 9, name: 'Hilton Garden Inn Downtown', city: 'Austin, TX', keys: 186, service: 'Select-Service',
    status: 'In Review', dealStage: 'Teaser', revpar: 142, irr: 15.7, risk: 'Medium',
    aiConfidence: 65, assignee: 'JR', docs: '3/3', noi: 2_840_000,
    createdAt: 'Apr 24, 2026', updatedAt: 'just now',
  },
  {
    id: 7, name: 'Kimpton Angler', city: 'Miami Beach, FL', keys: 132, service: 'Full-Service',
    status: 'IC Ready', dealStage: 'Under NDA', revpar: 385, irr: 23.48, risk: 'Low',
    aiConfidence: 87, assignee: 'EA', docs: '8/8', noi: 4_281_000,
    createdAt: 'Apr 19, 2026', updatedAt: '1d ago',
  },
  {
    id: 8, name: 'Marriott Magnificent Mile', city: 'Chicago, IL', keys: 312, service: 'Full-Service',
    status: 'In Review', dealStage: 'LOI', revpar: 189, irr: 18.2, risk: 'Low',
    aiConfidence: 72, assignee: 'MK', docs: '5/5', noi: 7_120_000, updatedAt: '2d ago',
  },
  {
    id: 10, name: 'Hyatt Regency Waterfront', city: 'Seattle, WA', keys: 425, service: 'Full-Service',
    status: 'Draft', dealStage: 'Under NDA', revpar: 210, irr: 14.3, risk: 'Low',
    aiConfidence: 0, assignee: 'SP', docs: '0/0', noDocs: true, updatedAt: '7d ago',
  },
];

export const dealStages: DealStage[] = ['Teaser', 'Under NDA', 'LOI', 'PSA'];

// FON-100 — Eshan's target levered IRR bands. The ids are persisted as the
// deal's `return_profile` tag, so they never change; legacy deals carrying
// `core` / `value-add` / `opportunistic` keep resolving. A preset is only ever
// applied by an explicit analyst action — nothing auto-fills a target.
export const returnProfiles = [
  { id: 'core', label: 'Core', target: '5-10%', desc: 'Stabilized institutional cash flow with conservative leverage.' },
  { id: 'core-plus', label: 'Core Plus', target: '7-12%', desc: 'Stabilized asset with modest operational or capital upside and moderate leverage.' },
  { id: 'value-add', label: 'Value Add', target: '11-17%', desc: 'Repositioning, PIP, or operating-leverage thesis with moderate execution risk.' },
  { id: 'opportunistic', label: 'Opportunistic', target: '18%+', desc: 'Development, distress, or adaptive reuse with significant execution risk.' },
];
export type ReturnProfileId = 'core' | 'core-plus' | 'value-add' | 'opportunistic';

// Standardized to the STR / CBRE chain-scale ladder Sam's review
// flagged (institutional positioning ranges from economy through
// luxury — the prior 4-tier set lumped Upper Midscale + Upscale
// together which produced unrealistic ADR anchors on upper-midscale
// brands). Order is bottom-up so the wizard renders cheapest first.
export const positioningTiers = [
  { id: 'default', label: 'Default', desc: 'No tier chosen — a brand suggests its STR chain scale, and so may the Offering Memorandum. Until a tier is chosen the model applies no chain-scale filter.' },
  { id: 'economy', label: 'Economy', desc: 'Lowest ADR tier (e.g. Days Inn, Super 8)' },
  { id: 'midscale', label: 'Midscale', desc: 'Limited-service value tier (e.g. Wingate, Tru by Hilton)' },
  { id: 'upper-midscale', label: 'Upper Midscale', desc: 'Select-service tier (e.g. Hampton Inn, Holiday Inn Express)' },
  { id: 'upscale', label: 'Upscale', desc: 'Full-service mid-market (e.g. Courtyard, Hyatt Place)' },
  { id: 'upper-upscale', label: 'Upper Upscale', desc: 'Full-service luxury-adjacent (e.g. Marriott, Hilton, Sheraton)' },
  { id: 'luxury', label: 'Luxury', desc: 'Premium tier (e.g. Ritz-Carlton, Four Seasons, St. Regis)' },
];

// Where the deal originated. Surfaced on the create-deal wizard and
// stored on the deal record for sourcing-channel analytics. Order
// matches institutional convention (broker-led deals lead the list).
export const sourcingChannels = [
  { id: 'broker', label: 'Broker' },
  { id: 'lender', label: 'Lender' },
  { id: 'franchisor', label: 'Franchisor' },
  { id: 'operator', label: 'Operator' },
  { id: 'capital_partner', label: 'Capital Partner' },
  { id: 'direct', label: 'Direct' },
];

export const projectStatuses: (ProjectStatus | 'All Status')[] = [
  'All Status', 'Draft', 'In Review', 'IC Ready', 'Archived',
];

export const documentChecklist = [
  'Financials (3-Year P&L, TTM, Monthly)',
  'Room Revenue Reports', 'STR Reports', 'Offering Memorandum (OM)',
  'Room Mix / Unit Mix', 'Historical CapEx', 'Property Taxes',
  'Basic Property Info', 'Leases & Agreements', 'Surveys & Reviews',
];

export const engines = [
  { id: 'investment', label: 'Investment', progress: 35 },
  { id: 'pl', label: 'P&L', progress: 0 },
  { id: 'debt', label: 'Debt', progress: 70 },
  { id: 'cash-flow', label: 'Cash Flow', progress: 35 },
  { id: 'returns', label: 'Returns', progress: 70 },
  { id: 'partnership', label: 'Partnership', progress: 0 },
];

// Brand catalog. Originally backfilled from evals/golden-set/brand-catalog.json
// (191 brands across 12 families). R-020 / R-021 / R-022 (2026-10-08) re-audited
// every group against an official or authoritative source, recorded per family
// below (`sources`), added Accor, Meliá, Minor Hotels and Motel One, moved
// group-owned brands out of "Other Brands", and added missing current brands.
//
// Starwood Hotels & Resorts no longer exists as a hotel group — Marriott
// completed the acquisition on 23 Sep 2016 (https://marriott.gcs-web.com/node/14521).
// Its legacy brands sit under Marriott and carry the alias "Starwood" so a
// search for "Starwood" still finds them. (Separately, Starwood Capital renamed
// its SH Hotels & Resorts — 1 Hotels, Baccarat, Treehouse — "Starwood Hotels"
// in 2025; those are NOT Marriott and stay under Other Brands.)
//
// `tier` is the catalog's pre-existing label (kept as-is for existing brands;
// new brands take their STR chain scale). `scale` is the brand's STR chain
// scale per STR's published "STR Chain Scales" table (February 2026,
// https://www.costar.com/products/str-benchmark/resources/guidelines/str-chain-scales),
// present only where STR lists the brand. R-024 — `scale` (else `tier`) is the
// brand's default positioning in the wizard / Overview.
//
// Brand names use the canonical catalog form (e.g., "Hampton by Hilton") with the
// shorter mockData aliases retained as substring-matchable prefixes for legacy deal
// records that store just the short brand (e.g., an extracted brand of "Kimpton").
export type ChainScale = 'Luxury' | 'Upper Upscale' | 'Upscale' | 'Upper Midscale' | 'Midscale' | 'Economy';
export type Brand = {
  name: string;
  tier: string;
  /** STR chain scale (Feb 2026 table) — only where STR publishes one. */
  scale?: ChainScale;
  /** Extra search terms (e.g. "Starwood" for the legacy Starwood brands). */
  aliases?: string[];
};
export type BrandFamily = {
  family: string;
  count: number;
  brands: Brand[];
  /** Short parent-chain label shown as secondary text next to a brand
   *  ("IHG" for "IHG Hotels & Resorts"). Falls back to `family`. */
  short?: string;
  /** Official / authoritative source URLs for this group's brand list. */
  sources: string[];
  note?: string;
};

function family(f: Omit<BrandFamily, 'count'>): BrandFamily {
  return { ...f, count: f.brands.length };
}

export const brandFamilies: BrandFamily[] = [
  // Sources: https://stories.hilton.com/brands
  family({ family: "Hilton", short: "Hilton", sources: ["https://stories.hilton.com/brands"], brands: [
    { name: "Hampton by Hilton", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Hilton Hotels & Resorts", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Hilton Garden Inn", tier: "Upscale", scale: "Upscale" },
    { name: "DoubleTree by Hilton", tier: "Upscale", scale: "Upscale" },
    { name: "Home2 Suites by Hilton", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Embassy Suites by Hilton", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Homewood Suites by Hilton", tier: "Upscale", scale: "Upscale" },
    { name: "Tru by Hilton", tier: "Midscale", scale: "Midscale" },
    { name: "Curio Collection by Hilton", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Tapestry Collection by Hilton", tier: "Upscale", scale: "Upper Upscale" },
    { name: "Canopy by Hilton", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Signia by Hilton", tier: "Luxury", scale: "Luxury" },
    { name: "Motto by Hilton", tier: "Upscale", scale: "Upscale" },
    { name: "Spark by Hilton", tier: "Midscale", scale: "Midscale" },
    { name: "Tempo by Hilton", tier: "Upscale", scale: "Upscale" },
    { name: "LXR Hotels & Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Conrad Hotels & Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Waldorf Astoria Hotels & Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Graduate by Hilton", tier: "Upper Upscale", scale: "Upper Upscale", aliases: ["Graduate Hotels"] },
    { name: "NoMad Hotels", tier: "Luxury", scale: "Luxury" },
    { name: "Outset Collection by Hilton", tier: "Upscale", scale: "Upscale" },
    { name: "LivSmart Studios by Hilton", tier: "Midscale", scale: "Midscale" },
  ]}),
  // Sources: https://www.marriott.com/marriott-brands.mi · https://www.sec.gov/Archives/edgar/data/1048286/000104828626000007/mar-20251231.htm · https://marriott.gcs-web.com/node/14521
  family({ family: "Marriott International", short: "Marriott", sources: ["https://www.marriott.com/marriott-brands.mi", "https://www.sec.gov/Archives/edgar/data/1048286/000104828626000007/mar-20251231.htm", "https://marriott.gcs-web.com/node/14521"], note: "Includes the former Starwood Hotels & Resorts brands — Marriott completed its acquisition of Starwood on 23 Sep 2016. Search \"Starwood\" to list them.", brands: [
    { name: "Courtyard by Marriott", tier: "Upscale", scale: "Upscale" },
    { name: "Marriott Hotels", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Fairfield by Marriott", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Residence Inn by Marriott", tier: "Upscale", scale: "Upscale" },
    { name: "Sheraton", tier: "Upper Upscale", scale: "Upper Upscale", aliases: ["Starwood"] },
    { name: "SpringHill Suites by Marriott", tier: "Upscale", scale: "Upscale" },
    { name: "TownePlace Suites by Marriott", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Autograph Collection", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Renaissance Hotels", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Aloft Hotels", tier: "Upscale", scale: "Upscale", aliases: ["Starwood"] },
    { name: "Four Points by Sheraton", tier: "Upscale", scale: "Upscale", aliases: ["Starwood"] },
    { name: "Delta Hotels by Marriott", tier: "Upper Upscale", scale: "Upscale" },
    { name: "AC Hotels by Marriott", tier: "Upscale", scale: "Upscale" },
    { name: "JW Marriott", tier: "Luxury", scale: "Luxury" },
    { name: "The Westin Hotels & Resorts", tier: "Upper Upscale", scale: "Upper Upscale", aliases: ["Starwood"] },
    { name: "Element Hotels", tier: "Upscale", scale: "Upscale", aliases: ["Starwood"] },
    { name: "Tribute Portfolio", tier: "Upper Upscale", scale: "Upper Upscale", aliases: ["Starwood"] },
    { name: "Moxy Hotels", tier: "Upscale", scale: "Upper Midscale" },
    { name: "The Luxury Collection", tier: "Luxury", scale: "Luxury", aliases: ["Starwood"] },
    { name: "Le Méridien", tier: "Upper Upscale", scale: "Upper Upscale", aliases: ["Starwood"] },
    { name: "The Ritz-Carlton", tier: "Luxury", scale: "Luxury" },
    { name: "W Hotels", tier: "Luxury", scale: "Luxury", aliases: ["Starwood"] },
    { name: "St. Regis Hotels & Resorts", tier: "Luxury", scale: "Luxury", aliases: ["Starwood"] },
    { name: "EDITION Hotels", tier: "Luxury", scale: "Luxury" },
    { name: "Gaylord Hotels", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Bvlgari Hotels & Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "City Express by Marriott", tier: "Midscale", scale: "Midscale" },
    { name: "Marriott Executive Apartments", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Design Hotels", tier: "Various", aliases: ["Starwood"] },
    { name: "citizenM", tier: "Upscale", scale: "Upscale" },
    { name: "The Ritz-Carlton Reserve", tier: "Luxury" },
    { name: "Protea Hotels by Marriott", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Four Points Flex by Sheraton", tier: "Midscale", scale: "Midscale", aliases: ["Starwood"] },
    { name: "Series by Marriott", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "StudioRes", tier: "Midscale", scale: "Midscale" },
    { name: "Apartments by Marriott Bonvoy", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Outdoor Collection by Marriott Bonvoy", tier: "Various" },
  ]}),
  // Sources: https://www.ihgplc.com/en/our-brands
  family({ family: "IHG Hotels & Resorts", short: "IHG", sources: ["https://www.ihgplc.com/en/our-brands"], brands: [
    { name: "Holiday Inn Express", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Holiday Inn", tier: "Upscale", scale: "Upper Midscale" },
    { name: "Candlewood Suites", tier: "Midscale", scale: "Midscale" },
    { name: "Staybridge Suites", tier: "Upscale", scale: "Upscale" },
    { name: "Crowne Plaza Hotels & Resorts", tier: "Upper Upscale", scale: "Upscale" },
    { name: "InterContinental Hotels & Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Kimpton Hotels & Restaurants", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Hotel Indigo", tier: "Upscale", scale: "Upper Upscale" },
    { name: "avid hotels", tier: "Midscale", scale: "Midscale" },
    { name: "EVEN Hotels", tier: "Upscale", scale: "Upscale" },
    { name: "voco Hotels", tier: "Upper Upscale", scale: "Upscale" },
    { name: "Vignette Collection", tier: "Upper Upscale", scale: "Luxury" },
    { name: "Atwell Suites", tier: "Upscale", scale: "Upper Midscale" },
    { name: "Garner Hotels", tier: "Midscale", scale: "Midscale" },
    { name: "Iberostar Beachfront Resorts", tier: "Upper Upscale" },
    { name: "Regent Hotels & Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Six Senses", tier: "Luxury", scale: "Luxury" },
    { name: "Ruby Hotels", tier: "Upscale", scale: "Upscale" },
    { name: "HUALUXE Hotels and Resorts", tier: "Upscale", scale: "Upscale" },
  ]}),
  // Sources: https://www.sec.gov/Archives/edgar/data/1468174/000146817426000007/h-20251231.htm · https://investors.hyatt.com/news/investor-news/news-details/2026/Hyatt-Announces-Timing-of-Third-Quarter-2026-Earnings-Release-and-Investor-Conference-Call/default.aspx
  family({ family: "Hyatt Hotels Corp.", short: "Hyatt", sources: ["https://www.sec.gov/Archives/edgar/data/1468174/000146817426000007/h-20251231.htm", "https://investors.hyatt.com/news/investor-news/news-details/2026/Hyatt-Announces-Timing-of-Third-Quarter-2026-Earnings-Release-and-Investor-Conference-Call/default.aspx"], brands: [
    { name: "Hyatt Place", tier: "Upscale", scale: "Upscale" },
    { name: "Hyatt House", tier: "Upscale", scale: "Upscale" },
    { name: "Hyatt Regency", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Grand Hyatt", tier: "Upper Upscale", scale: "Luxury" },
    { name: "Hyatt Centric", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Andaz", tier: "Luxury", scale: "Luxury" },
    { name: "Park Hyatt", tier: "Luxury", scale: "Luxury" },
    { name: "Thompson Hotels", tier: "Luxury", scale: "Luxury" },
    { name: "Alila Hotels & Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Destination by Hyatt", tier: "Upper Upscale", scale: "Luxury" },
    { name: "JdV by Hyatt", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Caption by Hyatt", tier: "Upscale", scale: "Upscale" },
    { name: "Hyatt Studios", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Hyatt Vacation Club", tier: "Upper Upscale" },
    { name: "Hyatt Ziva", tier: "Upper Upscale", scale: "Luxury" },
    { name: "Hyatt Zilara", tier: "Luxury", scale: "Luxury" },
    { name: "UrCove", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Miraval Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Hyatt", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Hyatt Select", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Hyatt Vivid", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "The Unbound Collection by Hyatt", tier: "Luxury", scale: "Luxury" },
    { name: "Dream Hotels", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Unscripted by Hyatt", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "The Standard", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Bunkhouse Hotels", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Me and All Hotels", tier: "Upscale", scale: "Upscale" },
    { name: "Secrets Resorts & Spas", tier: "Luxury", scale: "Luxury" },
    { name: "Breathless Resorts & Spas", tier: "Luxury", scale: "Luxury" },
    { name: "Dreams Resorts & Spas", tier: "Luxury", scale: "Luxury" },
    { name: "Zoëtry Wellness & Spa Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Alua Hotels & Resorts", tier: "Upscale", scale: "Upscale" },
    { name: "Sunscape Resorts & Spas", tier: "Upper Upscale", scale: "Upper Upscale" },
  ]}),
  // Sources: https://corporate.wyndhamhotels.com/our-brands/
  family({ family: "Wyndham Hotels & Resorts", short: "Wyndham", sources: ["https://corporate.wyndhamhotels.com/our-brands/"], brands: [
    { name: "Super 8 by Wyndham", tier: "Economy", scale: "Economy" },
    { name: "Days Inn by Wyndham", tier: "Economy", scale: "Economy" },
    { name: "Ramada by Wyndham", tier: "Midscale", scale: "Midscale" },
    { name: "Howard Johnson by Wyndham", tier: "Economy", scale: "Economy" },
    { name: "Travelodge by Wyndham", tier: "Economy", scale: "Economy" },
    { name: "Microtel by Wyndham", tier: "Economy", scale: "Economy" },
    { name: "Wingate by Wyndham", tier: "Upper Midscale", scale: "Midscale" },
    { name: "Hawthorn Suites by Wyndham", tier: "Midscale", scale: "Midscale" },
    { name: "AmericInn by Wyndham", tier: "Midscale", scale: "Midscale" },
    { name: "Baymont by Wyndham", tier: "Midscale", scale: "Midscale" },
    { name: "La Quinta by Wyndham", tier: "Midscale", scale: "Upper Midscale" },
    { name: "Wyndham Garden", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Wyndham Hotels & Resorts", tier: "Upper Upscale", scale: "Upscale" },
    { name: "Wyndham Grand", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Wyndham Alltra", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "TRYP by Wyndham", tier: "Upscale", scale: "Upper Midscale" },
    { name: "Dolce Hotels and Resorts by Wyndham", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Trademark Collection by Wyndham", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Registry Collection Hotels", tier: "Luxury", scale: "Luxury" },
    { name: "Esplendor by Wyndham", tier: "Upscale", scale: "Upscale" },
    { name: "Vienna House by Wyndham", tier: "Upscale", scale: "Upscale" },
    { name: "ECHO Suites Extended Stay by Wyndham", tier: "Economy", scale: "Economy" },
    { name: "WaterWalk Extended Stay by Wyndham", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Dazzler by Wyndham", tier: "Upscale", scale: "Upscale" },
    { name: "Ramada Encore by Wyndham", tier: "Midscale", scale: "Midscale" },
  ]}),
  // Sources: https://www.sec.gov/Archives/edgar/data/1046311/000104631126000008/chh-20251231.htm
  family({ family: "Choice Hotels International", short: "Choice", sources: ["https://www.sec.gov/Archives/edgar/data/1046311/000104631126000008/chh-20251231.htm"], brands: [
    { name: "Comfort Inn", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Comfort Suites", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Quality Inn", tier: "Midscale", scale: "Midscale" },
    { name: "Sleep Inn", tier: "Midscale", scale: "Midscale" },
    { name: "Clarion", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Clarion Pointe", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Cambria Hotels", tier: "Upscale", scale: "Upscale" },
    { name: "MainStay Suites", tier: "Midscale", scale: "Midscale" },
    { name: "WoodSpring Suites", tier: "Economy", scale: "Economy" },
    { name: "Suburban Studios", tier: "Economy", scale: "Economy" },
    { name: "Everhome Suites", tier: "Midscale", scale: "Midscale" },
    { name: "Econo Lodge", tier: "Economy", scale: "Economy" },
    { name: "Rodeway Inn", tier: "Economy", scale: "Economy" },
    { name: "Ascend Hotel Collection", tier: "Upper Midscale", scale: "Upscale" },
    { name: "Radisson Hotels Americas", tier: "Upper Upscale", scale: "Upscale" },
    { name: "Radisson Blu", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Radisson RED", tier: "Upscale", scale: "Upper Upscale" },
    { name: "Park Plaza", tier: "Upper Upscale", scale: "Upscale" },
    { name: "Country Inn & Suites by Radisson", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Radisson Individuals", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Park Inn by Radisson", tier: "Midscale", scale: "Midscale" },
  ]}),
  // Sources: https://skift.com/2025/11/21/best-westerns-hotel-brands-explained/
  family({ family: "BWH Hotels (Best Western)", short: "Best Western", sources: ["https://skift.com/2025/11/21/best-westerns-hotel-brands-explained/"], brands: [
    { name: "Best Western", tier: "Midscale", scale: "Midscale" },
    { name: "Best Western Plus", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Best Western Premier", tier: "Upscale", scale: "Upscale" },
    { name: "Vīb", tier: "Upscale", scale: "Upscale" },
    { name: "GLō Best Western", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Executive Residency by Best Western", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "BW Signature Collection", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "BW Premier Collection", tier: "Upscale", scale: "Upscale" },
    { name: "Sadie Hotel", tier: "Upper Upscale" },
    { name: "Aiden Hotels", tier: "Upper Midscale", scale: "Upscale" },
    { name: "WorldHotels Luxury", tier: "Luxury", scale: "Luxury" },
    { name: "WorldHotels Elite", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "WorldHotels Distinctive", tier: "Upper Upscale", scale: "Upscale" },
    { name: "WorldHotels Crafted", tier: "Upscale", scale: "Upper Upscale" },
    { name: "SureStay Hotel by Best Western", tier: "Economy", scale: "Economy" },
    { name: "SureStay Plus by Best Western", tier: "Midscale", scale: "Economy" },
    { name: "SureStay Studio by Best Western", tier: "Economy", scale: "Economy" },
    { name: "@Home by Best Western", tier: "Midscale", scale: "Midscale" },
    { name: "SureStay Collection by Best Western", tier: "Economy", scale: "Economy" },
  ]}),
  // Sources: https://www.costar.com/products/str-benchmark/resources/guidelines/str-chain-scales
  family({ family: "Sonesta International Hotels", short: "Sonesta", sources: ["https://www.costar.com/products/str-benchmark/resources/guidelines/str-chain-scales"], brands: [
    { name: "Sonesta Hotels & Resorts", tier: "Upper Upscale", scale: "Upscale" },
    { name: "Royal Sonesta", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Sonesta Select", tier: "Upscale", scale: "Upscale" },
    { name: "Sonesta Simply Suites", tier: "Midscale", scale: "Midscale" },
    { name: "Sonesta ES Suites", tier: "Upscale", scale: "Upper Midscale" },
    { name: "Classico, A Sonesta Collection", tier: "Upper Upscale" },
    { name: "MOD, A Sonesta Collection", tier: "Upper Upscale", scale: "Upscale" },
    { name: "James Hotels", tier: "Upper Upscale" },
    { name: "Red Lion Hotels", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Red Lion Inn & Suites", tier: "Midscale", scale: "Midscale" },
    { name: "Americas Best Value Inn", tier: "Economy", scale: "Economy" },
    { name: "Knights Inn", tier: "Economy", scale: "Economy" },
    { name: "Sonesta Essential", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Signature Inn", tier: "Midscale", scale: "Midscale" },
    { name: "Hotel RL", tier: "Upscale", scale: "Upscale" },
    { name: "GuestHouse", tier: "Midscale", scale: "Midscale" },
    { name: "Canadas Best Value Inn", tier: "Midscale", scale: "Midscale" },
    { name: "Jameson Inn", tier: "Economy", scale: "Economy" },
    { name: "Country Hearth Inn", tier: "Economy", scale: "Economy" },
    { name: "America's Best Inn", tier: "Economy", scale: "Economy" },
  ]}),
  // Sources: https://www.hoteldive.com/news/oyo-parent-company-oravel-stays-rebrands-to-prism/759696/
  family({ family: "G6 Hospitality", short: "G6", sources: ["https://www.hoteldive.com/news/oyo-parent-company-oravel-stays-rebrands-to-prism/759696/"], brands: [
    { name: "Motel 6", tier: "Economy", scale: "Economy" },
    { name: "Studio 6", tier: "Economy", scale: "Economy" },
  ]}),
  // Sources: https://www.costar.com/products/str-benchmark/resources/guidelines/str-chain-scales
  family({ family: "Red Roof", sources: ["https://www.costar.com/products/str-benchmark/resources/guidelines/str-chain-scales"], brands: [
    { name: "Red Roof Inn", tier: "Economy", scale: "Economy" },
    { name: "Red Roof PLUS+", tier: "Economy", scale: "Economy" },
    { name: "HomeTowne Studios by Red Roof", tier: "Economy", scale: "Economy" },
    { name: "The Red Collection", tier: "Upper Midscale", scale: "Upper Midscale" },
  ]}),
  // Sources: https://www.costar.com/products/str-benchmark/resources/guidelines/str-chain-scales
  family({ family: "Extended Stay America", sources: ["https://www.costar.com/products/str-benchmark/resources/guidelines/str-chain-scales"], brands: [
    { name: "Extended Stay America", tier: "Economy" },
    { name: "Extended Stay America Suites", tier: "Midscale", scale: "Midscale" },
    { name: "Extended Stay America Premier Suites", tier: "Upper Midscale", scale: "Midscale" },
    { name: "Extended Stay America Select Suites", tier: "Economy", scale: "Economy" },
  ]}),
  // Sources: https://group.accor.com/en/brands · https://all.accor.com/a/en/brands.html · https://ennismore.com/brands/
  family({ family: "Accor", short: "Accor", sources: ["https://group.accor.com/en/brands", "https://all.accor.com/a/en/brands.html", "https://ennismore.com/brands/"], brands: [
    { name: "Raffles Hotels & Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Orient Express", tier: "Luxury", scale: "Luxury" },
    { name: "Fairmont Hotels and Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Emblems Collection", tier: "Luxury", scale: "Luxury" },
    { name: "Sofitel", tier: "Luxury", scale: "Luxury" },
    { name: "Sofitel Legend", tier: "Luxury", scale: "Luxury" },
    { name: "MGallery Collection", tier: "Luxury", scale: "Luxury" },
    { name: "Faena", tier: "Luxury", scale: "Luxury" },
    { name: "SO/", tier: "Luxury", scale: "Luxury" },
    { name: "Rixos", tier: "Luxury", scale: "Luxury" },
    { name: "SLS Hotels", tier: "Luxury", scale: "Luxury" },
    { name: "Mondrian", tier: "Luxury", scale: "Luxury" },
    { name: "Delano", tier: "Luxury", scale: "Luxury" },
    { name: "21c Museum Hotels", tier: "Luxury", scale: "Luxury" },
    { name: "Our Habitas", tier: "Luxury", scale: "Luxury" },
    { name: "25hours Hotels", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Hyde", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Morgans Originals", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "The Hoxton", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Mama Shelter", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Swissôtel", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Pullman Hotels and Resorts", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Mövenpick Hotels and Resorts", tier: "Upper Upscale", scale: "Upscale" },
    { name: "Grand Mercure", tier: "Upscale", scale: "Upscale" },
    { name: "Mantis Collection", tier: "Luxury", scale: "Luxury" },
    { name: "Peppers", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "The Sebel", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Art Series", tier: "Upscale", scale: "Upscale" },
    { name: "Novotel", tier: "Upscale", scale: "Upscale" },
    { name: "Mercure", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Handwritten Collection", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Mantra", tier: "Upscale", scale: "Upscale" },
    { name: "Adagio", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Adagio Access", tier: "Midscale", scale: "Midscale" },
    { name: "TRIBE", tier: "Midscale", scale: "Midscale" },
    { name: "ibis", tier: "Midscale", scale: "Midscale" },
    { name: "ibis Styles", tier: "Midscale", scale: "Midscale" },
    { name: "greet", tier: "Midscale", scale: "Midscale" },
    { name: "BreakFree", tier: "Midscale", scale: "Midscale" },
    { name: "ibis budget", tier: "Economy", scale: "Economy" },
    { name: "JO&JOE", tier: "Economy", scale: "Economy" },
    { name: "hotelF1", tier: "Economy", scale: "Economy" },
  ]}),
  // Sources: https://www.hospitalitynet.org/news/4125530.html
  family({ family: "Meliá Hotels International", short: "Meliá", sources: ["https://www.hospitalitynet.org/news/4125530.html"], brands: [
    { name: "Gran Meliá", tier: "Luxury", scale: "Luxury" },
    { name: "ME by Meliá", tier: "Luxury", scale: "Luxury" },
    { name: "Paradisus by Meliá", tier: "Luxury", scale: "Luxury" },
    { name: "The Meliá Collection", tier: "Luxury", scale: "Luxury" },
    { name: "ZEL", tier: "Luxury", scale: "Luxury" },
    { name: "Meliá Hotels & Resorts", tier: "Upscale", scale: "Upscale" },
    { name: "INNSiDE by Meliá", tier: "Upscale", scale: "Upscale" },
    { name: "Sol by Meliá", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "Affiliated by Meliá", tier: "Upscale", scale: "Upscale" },
  ]}),
  // Sources: https://www.minorhotels.com/en/brands
  family({ family: "Minor Hotels", short: "Minor", sources: ["https://www.minorhotels.com/en/brands"], brands: [
    { name: "Anantara", tier: "Luxury", scale: "Luxury" },
    { name: "Elewana Collection", tier: "Luxury" },
    { name: "Tivoli", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "NH Collection", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "nhow", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Avani", tier: "Upscale", scale: "Upscale" },
    { name: "NH Hotels", tier: "Upscale", scale: "Upscale" },
    { name: "Oaks Hotels & Resorts", tier: "Upscale", scale: "Upscale" },
  ]}),
  // Sources: https://www.motel-one.com/en/ · https://www.presseportal.de/en/pm/31948/6265751
  family({ family: "Motel One Group", short: "Motel One", sources: ["https://www.motel-one.com/en/", "https://www.presseportal.de/en/pm/31948/6265751"], brands: [
    { name: "Motel One", tier: "Upper Midscale", scale: "Upper Midscale" },
    { name: "The Cloud One Hotels", tier: "Upscale", scale: "Upscale" },
  ]}),
  // Sources: https://www.costar.com/products/str-benchmark/resources/guidelines/str-chain-scales
  family({ family: "Other Brands", sources: ["https://www.costar.com/products/str-benchmark/resources/guidelines/str-chain-scales"], note: "Brands that are not part of a hotel group in this catalog (independent, or owned by a company that is not listed here).", brands: [
    { name: "Four Seasons Hotels and Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Aman Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Mandarin Oriental Hotel Group", tier: "Luxury", scale: "Luxury" },
    { name: "Rosewood Hotels & Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Belmond", tier: "Luxury", scale: "Luxury" },
    { name: "Capella Hotels and Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Auberge Resorts Collection", tier: "Luxury", scale: "Luxury" },
    { name: "Loews Hotels & Co", tier: "Upper Upscale", scale: "Luxury" },
    { name: "Omni Hotels & Resorts", tier: "Upper Upscale", scale: "Luxury" },
    { name: "1 Hotels", tier: "Luxury", scale: "Luxury", aliases: ["Starwood Hotels (Starwood Capital)"] },
    { name: "Baccarat Hotels & Resorts", tier: "Luxury", aliases: ["Starwood Hotels (Starwood Capital)"] },
    { name: "Treehouse Hotels", tier: "Upper Upscale", aliases: ["Starwood Hotels (Starwood Capital)"] },
    { name: "Independent / Unflagged", tier: "Various" },
    { name: "Margaritaville Hotels & Resorts", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Great Wolf Lodge", tier: "Upper Midscale", scale: "Upper Upscale" },
    { name: "Drury Hotels", tier: "Upper Midscale", scale: "Upscale" },
    { name: "Hard Rock Hotels", tier: "Upper Upscale", scale: "Upper Upscale" },
    { name: "Pendry Hotels", tier: "Luxury", scale: "Luxury" },
    { name: "Montage Hotels & Resorts", tier: "Luxury", scale: "Luxury" },
    { name: "Nobu Hotels", tier: "Luxury", scale: "Luxury" },
    { name: "Equinox Hotels", tier: "Luxury" },
    { name: "Club Quarters Hotels", tier: "Upper Upscale", scale: "Upper Upscale" },
  ]}),
];

/**
 * R-020 — brand search for the wizard picker. A family-name hit keeps every
 * brand in that family; otherwise a brand matches on its name or any alias
 * (so "Starwood" lists the legacy Starwood brands under Marriott). Families
 * with no hits are dropped. An empty query returns the whole catalog.
 */
export function searchBrandFamilies(query: string): BrandFamily[] {
  const q = query.trim().toLowerCase();
  if (!q) return brandFamilies;
  return brandFamilies
    .map(f =>
      f.family.toLowerCase().includes(q) || (f.short ?? '').toLowerCase() === q
        ? f
        : {
            ...f,
            brands: f.brands.filter(
              b => b.name.toLowerCase().includes(q) || (b.aliases ?? []).some(a => a.toLowerCase().includes(q)),
            ),
          },
    )
    .filter(f => f.brands.length > 0);
}

const POSITIONING_BY_SCALE: Record<string, string> = {
  Luxury: 'luxury',
  'Upper Upscale': 'upper-upscale',
  Upscale: 'upscale',
  'Upper Midscale': 'upper-midscale',
  Midscale: 'midscale',
  Economy: 'economy',
};

/**
 * Sam 2026-10-14 #1 — the chain-scale label shown beside a brand: STR's
 * published chain scale wherever STR lists the brand, else the catalog's
 * own classification. The same rule `brandDefaultPositioning` pre-fills from.
 */
export function brandPositioningLabel(b: Pick<Brand, 'tier' | 'scale'>): string {
  return b.scale ?? b.tier;
}

/**
 * R-024 — the brand's suggested positioning (a `positioningTiers` id): its STR
 * chain scale where STR publishes one, else its catalog tier. Null when the
 * brand is unknown or has no single scale ("Various" — Independent, Design
 * Hotels, the Outdoor Collection), in which case nothing is suggested. Since
 * Sam 2026-10-15 this is a SUGGESTION the analyst selects — the wizard and the
 * Overview never write it to the deal on their own (the worker would record
 * it as the analyst's choice and the OM could no longer suggest a scale).
 */
export function brandDefaultPositioning(name: string | null | undefined): string | null {
  const hit = findBrand(name);
  if (!hit) return null;
  return POSITIONING_BY_SCALE[hit.brand.scale ?? hit.brand.tier] ?? null;
}

/**
 * Look up a brand by name across all families. Tolerant of legacy short
 * forms (e.g., "Kimpton" should match "Kimpton Hotels & Restaurants" and
 * "Hampton" should match "Hampton by Hilton").
 */
export function findBrand(name: string | null | undefined): { brand: Brand; family: string } | null {
  if (!name) return null;
  const needle = name.trim().toLowerCase();
  if (!needle) return null;
  for (const fam of brandFamilies) {
    // Exact match first.
    const exact = fam.brands.find(b => b.name.toLowerCase() === needle);
    if (exact) return { brand: exact, family: fam.family };
  }
  // Then prefix/contains match (catalog name starts with the short form).
  for (const fam of brandFamilies) {
    const partial = fam.brands.find(b => {
      const n = b.name.toLowerCase();
      return n.startsWith(needle + ' ') || n.startsWith(needle + ',') || n === needle;
    });
    if (partial) return { brand: partial, family: fam.family };
  }
  return null;
}

/** Short parent-chain label for a family ("IHG" for "IHG Hotels & Resorts"). */
export function brandFamilyShort(fam: BrandFamily): string {
  return fam.short ?? fam.family;
}

/**
 * R-019 — parent chain (short label) of a brand name, for the wizard's
 * "Kimpton Hotels & Restaurants · IHG" secondary text. Null when the name
 * is not in the catalog (e.g. "agnostic" or a free-text legacy brand).
 */
export function brandChain(name: string | null | undefined): string | null {
  const hit = findBrand(name);
  if (!hit) return null;
  const fam = brandFamilies.find(f => f.family === hit.family);
  return fam ? brandFamilyShort(fam) : hit.family;
}

// Project 7 — Kimpton Angler — Deep data for the IC Ready demo deal
// ─────────────────── Critic finding shape ───────────────────
// The Critic agent reads the broker proforma + T-12 + market context and
// surfaces cross-field issues that a per-field variance pass would miss.
// Each finding grounds in a USALI rule_id (or a MULTI_FIELD_* rule from
// the cross-field rule additions). Severity ordered CRITICAL → WARN → INFO.
// TYPES ONLY — the live findings come from GET /deals/{id}/critic
// (AnalysisTab maps the worker JSON onto this shape). The names keep their
// historical "Kimpton" prefix for import stability.
export type KimptonCriticSeverity = 'CRITICAL' | 'WARN' | 'INFO';

export interface KimptonCriticFinding {
  id: string;
  ruleId: string;
  title: string;
  narrative: string;
  severity: KimptonCriticSeverity;
  citedFields: string[];
  citedPages: number[];
  citedDocumentId?: string;
  citedDocumentName?: string;
  impactEstimateUsd?: number;
}
