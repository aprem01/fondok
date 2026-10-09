// Seed data — single source of truth for the demo. Replace with API calls in Phase 3.

export const workspace = {
  name: 'Brookfield Real Estate',
  plan: 'Pro Plan',
  url: 'brookfield',
};

export const currentUser = {
  name: 'Eshan Mehta',
  role: 'Senior Analyst',
  initials: 'EM',
  email: 'eshan@brookfield.com',
};

export const dashboardStats = {
  activeProjects: 4,
  totalProjects: 4,
  documentsProcessed: 16,
  totalDealVolume: 461_900_000,
  avgTimeToIC: null as null | string,
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
    id: 9, name: 'Hilton Garden Inn Downtown', city: 'Austin, TX', keys: 186, service: 'Select Service',
    status: 'In Review', dealStage: 'Teaser', revpar: 142, irr: 15.7, risk: 'Medium',
    aiConfidence: 65, assignee: 'JR', docs: '3/3', noi: 2_840_000,
    createdAt: 'Apr 24, 2026', updatedAt: 'just now',
  },
  {
    id: 7, name: 'Kimpton Angler', city: 'Miami Beach, FL', keys: 132, service: 'Lifestyle',
    status: 'IC Ready', dealStage: 'Under NDA', revpar: 385, irr: 23.48, risk: 'Low',
    aiConfidence: 87, assignee: 'EA', docs: '8/8', noi: 4_281_000,
    createdAt: 'Apr 19, 2026', updatedAt: '1d ago',
  },
  {
    id: 8, name: 'Marriott Magnificent Mile', city: 'Chicago, IL', keys: 312, service: 'Full Service',
    status: 'In Review', dealStage: 'LOI', revpar: 189, irr: 18.2, risk: 'Low',
    aiConfidence: 72, assignee: 'MK', docs: '5/5', noi: 7_120_000, updatedAt: '2d ago',
  },
  {
    id: 10, name: 'Hyatt Regency Waterfront', city: 'Seattle, WA', keys: 425, service: 'Full Service',
    status: 'Draft', dealStage: 'Under NDA', revpar: 210, irr: 14.3, risk: 'Low',
    aiConfidence: 0, assignee: 'SP', docs: '0/0', noDocs: true, updatedAt: '7d ago',
  },
];

export const compSets = [
  {
    name: 'Chicago Full-Service', properties: 6,
    description: 'Luxury & full-service hotels in Chicago CBD',
    usedIn: ['Marriott Magnificent Mile', 'Hyatt Regency Waterfront'],
    starred: true, updated: 'Dec 15, 2025',
  },
  {
    name: 'Austin Select-Service', properties: 8,
    description: 'Select-service hotels near downtown Austin',
    usedIn: ['Hilton Garden Inn Downtown'],
    hidden: true, updated: 'Dec 10, 2025',
  },
  {
    name: 'Nashville Airport', properties: 5,
    description: 'Airport hotels serving BNA',
    hidden: true, updated: 'Dec 1, 2025',
  },
];

export const marketDataLib = [
  { market: 'Chicago, IL', submarket: 'Magnificent Mile/Gold Coast', revpar: 189, adr: 245, occ: 77.1, yoy: 8.2, source: 'STR' },
  { market: 'Austin, TX', submarket: 'Downtown', revpar: 142, adr: 198, occ: 71.8, yoy: 5.4, source: 'STR' },
  { market: 'Seattle, WA', submarket: 'Waterfront/Pike Place', revpar: 210, adr: 285, occ: 73.7, yoy: 12.1, source: 'STR' },
  { market: 'Denver, CO', submarket: 'Airport', revpar: 98, adr: 142, occ: 69.0, yoy: -2.3, source: 'STR' },
];

export const templates = [
  { name: 'Standard Full-Service', description: 'Default assumptions for full-service hotel acquisitions',
    hold: '5 years', ltv: '65%', exitCap: '7.0%', usedIn: 8 },
  { name: 'Value-Add Select-Service', description: 'For select-service hotels with renovation potential',
    hold: '7 years', ltv: '60%', exitCap: '7.5%', usedIn: 4 },
  { name: 'Core Luxury', description: 'Conservative assumptions for core luxury assets',
    hold: '10 years', ltv: '55%', exitCap: '5.5%', usedIn: 2 },
];

export const teamMembers = [
  { name: 'Sarah Chen', email: 'sarah@company.com', role: 'Admin', initials: 'SC' },
  { name: 'Mike Johnson', email: 'mike@company.com', role: 'Analyst', initials: 'MJ' },
  { name: 'Alex Wong', email: 'alex@company.com', role: 'Analyst', initials: 'AW' },
  { name: 'Emily Davis', email: 'emily@company.com', role: 'Principal', initials: 'ED', pending: true },
];

export const notificationDefaults = {
  projectStatus: true, documentUploads: true, aiExtraction: true,
  teamActivity: false, weeklyDigest: true,
};

export const integrations = [
  { name: 'STR', description: 'Competitive set and market data', status: 'Coming Soon' },
  { name: 'Kalibri Labs', description: 'Revenue optimization analytics', status: 'Coming Soon' },
  { name: 'CoStar', description: 'Commercial real estate data', status: 'Coming Soon' },
];

export const dealStages: DealStage[] = ['Teaser', 'Under NDA', 'LOI', 'PSA'];

export const returnProfiles = [
  { id: 'core', label: 'Core', target: '8-12%', desc: 'Stabilized institutional cash flow with conservative leverage.' },
  { id: 'value-add', label: 'Value Add', target: '12-18%', desc: 'Repositioning, PIP, or operating-leverage thesis with moderate execution risk.' },
  { id: 'opportunistic', label: 'Opportunistic', target: '18%+', desc: 'Development, distress, or adaptive reuse with significant execution risk.' },
];

// Standardized to the STR / CBRE chain-scale ladder Sam's review
// flagged (institutional positioning ranges from economy through
// luxury — the prior 4-tier set lumped Upper Midscale + Upscale
// together which produced unrealistic ADR anchors on upper-midscale
// brands). Order is bottom-up so the wizard renders cheapest first.
export const positioningTiers = [
  { id: 'default', label: 'Default', desc: 'Engine-derived from brand catalog + market comps.' },
  { id: 'economy', label: 'Economy', desc: 'Lowest ADR tier (e.g. Days Inn, Super 8)' },
  { id: 'midscale', label: 'Midscale', desc: 'Limited-service value tier (e.g. La Quinta, Wingate)' },
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
// records that store just the short brand (e.g., kimptonAnglerOverview.general.brand = "Kimpton").
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
 * R-024 — the brand's default positioning (a `positioningTiers` id): its STR
 * chain scale where STR publishes one, else its catalog tier. Null when the
 * brand is unknown or has no single scale ("Various" — Independent, Design
 * Hotels, the Outdoor Collection), in which case nothing is pre-filled.
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
export const kimptonAnglerOverview = {
  general: {
    name: 'Kimpton Angler Hotel', location: 'Miami Beach, FL', type: 'Lifestyle Boutique',
    brand: 'Kimpton', keys: 132, yearBuilt: 2015, gba: 142_000,
    meetingSpace: '4,200 SF', parking: 88, fbOutlets: 2,
  },
  // Investment Profile — mirrors the new wizard fields (return_profile /
  // positioning) so the deal detail can render the same intent the analyst
  // selected at deal creation. IRR target is sourced from `returnProfiles`.
  investmentProfile: {
    returnProfile: 'value-add' as 'core' | 'value-add' | 'opportunistic',
    positioning: 'luxury' as 'default' | 'luxury' | 'upscale' | 'economy',
  },
  acquisition: {
    purchasePrice: 36_400_000, pricePerKey: 275_758, entryCapRate: 0.0681,
    closingCosts: 728_736, workingCapital: 500_000,
  },
  reversion: {
    exitCapRate: 0.0700, exitYear: 5, terminalNOI: 5_120_000,
    grossSalePrice: 73_142_000, sellingCosts: 1_462_840,
  },
  returns: {
    leveredIRR: 0.2301, unleveredIRR: 0.1684, equityMultiple: 2.37,
    yearOneCoC: 0.158, hold: 5,
  },
  investment: {
    renovationBudget: 5_280_000, hardCostsPerKey: 30_000, softCosts: 528_000,
    contingency: 528_000, totalCapital: 43_309_906,
  },
  financing: {
    loanAmount: 23_660_000, ltv: 0.65, interestRate: 0.0680, dscr: 2.92,
    annualDebtService: 1_608_880, term: 5, amortization: 30,
  },
  refi: {
    refiYear: 4, refiLTV: 0.60, refiRate: 0.06, refiTerm: 5, refiAmortization: 30,
  },
  sources: [
    { label: 'Senior Debt', amount: 23_683_922, pct: 0.547 },
    { label: 'Equity', amount: 19_625_984, pct: 0.453 },
    { label: 'Total Sources', amount: 43_309_906, pct: 1.0, total: true },
  ],
  uses: [
    { label: 'Purchase Price', amount: 36_436_802 },
    { label: 'Closing Costs', amount: 728_736 },
    { label: 'Renovation', amount: 5_280_000 },
    { label: 'Working Capital', amount: 500_000 },
    { label: 'Loan Costs', amount: 364_368 },
    { label: 'Total Uses', amount: 43_309_906, total: true },
  ],
  proforma: [
    { label: 'Room Revenue', y1: 11_120, y2: 11_676, y3: 12_260, y4: 12_873, y5: 13_517, cagr: 0.05 },
    { label: 'F&B Revenue', y1: 3_240, y2: 3_402, y3: 3_572, y4: 3_751, y5: 3_938, cagr: 0.05 },
    { label: 'Other Revenue', y1: 720, y2: 756, y3: 794, y4: 833, y5: 875, cagr: 0.05 },
    { label: 'Total Revenue', y1: 15_080, y2: 15_834, y3: 16_626, y4: 17_457, y5: 18_330, cagr: 0.05, bold: true },
    { label: 'Operating Expenses', y1: 9_320, y2: 9_660, y3: 10_010, y4: 10_372, y5: 10_745 },
    { label: 'Management Fee', y1: 452, y2: 475, y3: 499, y4: 524, y5: 550 },
    { label: 'FF&E Reserve', y1: 603, y2: 633, y3: 665, y4: 698, y5: 733 },
    { label: 'Net Operating Income', y1: 4_705, y2: 5_066, y3: 5_452, y4: 5_863, y5: 6_302, cagr: 0.075, bold: true },
    { label: 'Debt Service', y1: 1_610, y2: 1_610, y3: 1_610, y4: 1_610, y5: 1_610 },
    { label: 'Cash Flow After Debt', y1: 3_095, y2: 3_456, y3: 3_842, y4: 4_253, y5: 4_692, bold: true },
  ],
};

// Sample documents for project 7
export const kimptonDocuments = [
  { name: 'Offering_Memorandum_Final.pdf', type: 'OM', status: 'Extracted', size: '4.2 MB', date: 'Apr 19, 2026', fields: 87, confidence: 94, populates: ['Investment', 'P&L'] },
  { name: 'T12_FinancialStatement.xlsx', type: 'T12', status: 'Extracted', size: '2.1 MB', date: 'Apr 19, 2026', fields: 143, confidence: 96, populates: ['P&L', 'Cash Flow'] },
  { name: 'STR_MarketReport_Q1.pdf', type: 'STR', status: 'Extracted', size: '1.8 MB', date: 'Apr 19, 2026', fields: 56, confidence: 91, populates: ['Market'] },
  { name: 'Monthly_PL_2024_2025.xlsx', type: 'P&L', status: 'Extracted', size: '892 KB', date: 'Apr 20, 2026', fields: 312, confidence: 98, populates: ['P&L'] },
  { name: 'PIP_Estimate_2026.pdf', type: 'OM', status: 'Processing', size: '3.4 MB', date: 'Apr 21, 2026', fields: 0, confidence: 0, populates: [] },
  { name: 'Lender_Term_Sheet.pdf', type: 'Contract', status: 'Pending', size: '1.1 MB', date: 'Apr 21, 2026', fields: 0, confidence: 0, populates: [] },
  { name: 'STR_Comp_Set_Detail.pdf', type: 'STR', status: 'Extracted', size: '2.6 MB', date: 'Apr 22, 2026', fields: 78, confidence: 89, populates: ['Market'] },
  { name: 'Property_Survey_2024.pdf', type: 'Market Study', status: 'Extracted', size: '5.7 MB', date: 'Apr 22, 2026', fields: 34, confidence: 86, populates: ['Investment'] },
];

// Market tab — Miami Beach
export const miamiMarket = {
  submarket: 'Miami Beach / South Beach, FL',
  asOf: 'Dec 2025',
  kpis: {
    inventory: { rooms: 18_450, hotels: 142, yoy: 1.8 },
    occupancy: { value: 76.2, deltaPts: 2.4 },
    adr: { value: 312.45, yoy: 6.2 },
    revpar: { value: 238.09, yoy: 8.8 },
    demandGrowth: 4.8,
    supplyGrowth: 1.2,
  },
  historical: [
    { year: '2021', occ: 58.4, adr: 248, revpar: 144 },
    { year: '2022', occ: 68.1, adr: 271, revpar: 184 },
    { year: '2023', occ: 71.5, adr: 287, revpar: 205 },
    { year: '2024', occ: 73.8, adr: 294, revpar: 217 },
    { year: '2025', occ: 76.2, adr: 312, revpar: 238 },
  ],
  monthly: [
    { m: 'Jan', occ: 82.1, revpar: 312 }, { m: 'Feb', occ: 85.4, revpar: 348 },
    { m: 'Mar', occ: 87.2, revpar: 362 }, { m: 'Apr', occ: 78.5, revpar: 268 },
    { m: 'May', occ: 71.2, revpar: 218 }, { m: 'Jun', occ: 68.4, revpar: 198 },
    { m: 'Jul', occ: 70.1, revpar: 205 }, { m: 'Aug', occ: 67.8, revpar: 192 },
    { m: 'Sep', occ: 64.2, revpar: 178 }, { m: 'Oct', occ: 73.5, revpar: 232 },
    { m: 'Nov', occ: 79.4, revpar: 278 }, { m: 'Dec', occ: 86.1, revpar: 348 },
  ],
  index: [
    { m: 'Jan', RGI: 1.10, ARI: 1.06, MPI: 1.04 },
    { m: 'Feb', RGI: 1.12, ARI: 1.08, MPI: 1.05 },
    { m: 'Mar', RGI: 1.14, ARI: 1.09, MPI: 1.05 },
    { m: 'Apr', RGI: 1.13, ARI: 1.08, MPI: 1.04 },
    { m: 'May', RGI: 1.11, ARI: 1.07, MPI: 1.03 },
    { m: 'Jun', RGI: 1.10, ARI: 1.07, MPI: 1.03 },
    { m: 'Jul', RGI: 1.11, ARI: 1.07, MPI: 1.04 },
    { m: 'Aug', RGI: 1.12, ARI: 1.08, MPI: 1.04 },
    { m: 'Sep', RGI: 1.12, ARI: 1.08, MPI: 1.04 },
    { m: 'Oct', RGI: 1.13, ARI: 1.09, MPI: 1.04 },
    { m: 'Nov', RGI: 1.14, ARI: 1.09, MPI: 1.04 },
    { m: 'Dec', RGI: 1.12, ARI: 1.08, MPI: 1.04 },
  ],
  segmentation: [
    { name: 'Transient', pct: 52, deltaPts: 3.2 },
    { name: 'Group', pct: 22, deltaPts: 1.8 },
    { name: 'Contract', pct: 26, deltaPts: 1.4 },
  ],
  pipeline: [
    { property: '1 Hotel South Beach Expansion', rooms: 85, status: 'Construction', opening: 'Q3 2026' },
    { property: 'Aman Miami Beach', rooms: 56, status: 'Construction', opening: 'Q1 2027' },
    { property: 'Edition Residences', rooms: 125, status: 'Planning', opening: 'Q4 2027' },
    { property: 'Rosewood Miami Beach', rooms: 148, status: 'Planning', opening: 'Q2 2028' },
  ],
  demandGenerators: [
    { name: 'Miami Beach Convention Center', type: 'Convention', volume: '1.2M attendees' },
    { name: 'Art Basel', type: 'Events', volume: '83,000 annually' },
    { name: 'South Beach Entertainment District', type: 'Tourism/Nightlife', volume: '15M annually' },
    { name: 'Miami International Airport', type: 'Transport', volume: '52M passengers' },
    { name: 'Cruise Port of Miami', type: 'Transport', volume: '7.5M passengers' },
  ],
  sales: [
    { name: 'The Setai Miami Beach', keys: 130, date: 'Aug 2025', price: '$245M', perKey: '$1.9M', cap: '4.8%', buyer: 'Ashkenazy Acquisition' },
    { name: 'Nautilus by Arlo', keys: 250, date: 'May 2025', price: '$98M', perKey: '$392k', cap: '6.2%', buyer: 'Private' },
    { name: 'Loews Miami Beach', keys: 790, date: 'Mar 2025', price: '$520M', perKey: '$658k', cap: '5.4%', buyer: 'Institutional' },
    { name: 'W South Beach', keys: 408, date: 'Feb 2025', price: '$425M', perKey: '$1.04M', cap: '5.1%', buyer: 'PE Fund' },
    { name: 'SLS South Beach', keys: 140, date: 'Dec 2024', price: '$95M', perKey: '$679k', cap: '6.0%', buyer: 'REIT' },
    { name: 'Cadillac Hotel & Beach Club', keys: 357, date: 'Nov 2024', price: '$130M', perKey: '$364k', cap: '6.8%', buyer: 'Institutional' },
  ],
  salesTotals: { ttmVolume: '$1.50B', txns: 6, avgPerKey: '$885,733', avgCap: '6.1%' },
};

// Analysis tab — Kimpton Angler
export const kimptonAnalysis = {
  summary: [
    'Kimpton Angler is a compelling value-add acquisition in the South Beach submarket at $36.4M ($276K/key) — a 22% discount to recent comparable lifestyle-tier transactions. The basis provides meaningful downside protection and supports a 24.5% levered IRR over a 5-year hold.',
    'The Brickell-adjacent location captures both leisure and corporate demand, and Kimpton brand affiliation commands a 14% ADR premium versus independent boutique competitors. STR data shows the asset trailing the comp set on RGI by 4 points, suggesting near-term yield management upside.',
    'We recommend proceeding to LOI at the current ask. PIP requirement of $5.3M ($40K/key) is in line with brand standards refresh and is captured in Year 1 capital plan. Senior debt sized at 65% LTC delivers 1.57x DSCR with comfortable covenant headroom.',
  ],
  risks: [
    { name: 'Overall Risk Score', tier: 'Low Risk', score: 24 },
    { name: 'RevPAR Volatility', tier: 'Low Risk', score: 32 },
    { name: 'Market Supply Risk', tier: 'Medium Risk', score: 38 },
    { name: 'Operator Risk', tier: 'Low Risk', score: 18 },
    { name: 'Capital Needs', tier: 'Low Risk', score: 28 },
  ],
  insights: [
    { title: 'Prime South Beach Location', body: 'Walking distance to ocean and Lincoln Road; positioned for both leisure compression weekends and corporate weekday demand from Brickell.' },
    { title: 'Lifestyle Brand Premium', body: 'Kimpton affiliation delivers a 14% ADR premium versus independent boutique competitors with comparable amenity packages.' },
    { title: 'Seasonal Concentration', body: 'Q1 RevPAR runs 80% above Q3 trough — strong seasonal hedging in revenue model is critical for stable distributions.' },
    { title: 'Attractive Basis', body: '$276K/key represents a 22% discount to replacement cost and 18% discount to last-trade lifestyle-tier comp set.' },
  ],
  scenarios: [
    { name: 'Base Case', probability: 55, irr: 23.01, coc: 15.8, multiple: 2.37, exitValue: 73_142_000 },
    { name: 'Upside Case', probability: 25, irr: 31.20, coc: 19.6, multiple: 2.94, exitValue: 84_500_000 },
    { name: 'Downside Case', probability: 20, irr: 14.80, coc: 11.4, multiple: 1.78, exitValue: 58_200_000 },
  ],
};

export const dealScenarios = [
  { name: 'Downside', irr: 14.8, unleveredIrr: 9.2, multiple: 1.78, avgCoC: 11.4 },
  { name: 'Base Case', irr: 23.01, unleveredIrr: 16.84, multiple: 2.37, avgCoC: 15.8, base: true },
  { name: 'Upside', irr: 31.20, unleveredIrr: 22.10, multiple: 2.94, avgCoC: 19.6 },
];

// ─────────────────── Critic findings — Kimpton Angler ───────────────────
// The Critic agent reads the broker proforma + T-12 + market context and
// surfaces cross-field issues that a per-field variance pass would miss.
// Each finding grounds in a USALI rule_id (or a MULTI_FIELD_* rule from
// the cross-field rule additions). Severity ordered CRITICAL → WARN → INFO.
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

export const kimptonCriticFindings: KimptonCriticFinding[] = [
  {
    id: 'critic-kimpton-1',
    ruleId: 'MULTI_FIELD_INSURANCE_COASTAL_RISK',
    title: 'Coastal insurance held flat in a Florida property',
    narrative:
      'Property is on Miami Beach, where wind/flood reinsurance has driven insurance per key up 30-60% at most renewals over the past 24 months. Broker assumes $1,851/key vs comp set average of $2,800/key (only +1.5% YoY). Underwrite to a 30-50% lift; the gap would lift insurance expense by approximately $244K and shave roughly 17 bps off NOI margin.',
    severity: 'CRITICAL',
    citedFields: ['insurance', 'fixed_charges'],
    citedPages: [22, 24],
    citedDocumentId: 'kimpton-angler-om-2026',
    citedDocumentName: 'Offering_Memorandum_Final.pdf',
    impactEstimateUsd: 244_000,
  },
  {
    id: 'critic-kimpton-2',
    ruleId: 'MULTI_FIELD_NOI_GROWTH_WITHOUT_OPEX_PRESSURE',
    title: 'NOI margin expansion without OpEx ratio movement',
    narrative:
      'Broker projects NOI growth of 13.4% ($3.78M → $4.28M) while OpEx ratio holds at 65.4% (T-12 actual 65.6%, delta 0.2pp). Margin expansion of this size requires an explicit revenue or labor source — the proforma does not show ADR uplift sufficient to support it. Confirm the assumption stack with the asset-management plan before locking the underwrite.',
    severity: 'WARN',
    citedFields: ['noi', 'opex_ratio', 'total_revenue'],
    citedPages: [38, 41],
    citedDocumentId: 'kimpton-angler-om-2026',
    citedDocumentName: 'Offering_Memorandum_Final.pdf',
    impactEstimateUsd: 503_000,
  },
  {
    id: 'critic-kimpton-3',
    ruleId: 'MULTI_FIELD_FNB_MARGIN_AGGRESSIVE',
    title: 'F&B margin aggressive for a lifestyle-tier select-service profile',
    narrative:
      'Broker projects F&B departmental margin of 27.5% on $740K of F&B revenue. Lifestyle Kimpton properties at this scale typically run F&B at 10-15% margin — the on-site bar/cafe operation does not have the banquet base to support full-service economics. Reset F&B margin to 12-14% in the underwrite.',
    severity: 'WARN',
    citedFields: ['fb_revenue', 'dept_expenses.food_beverage'],
    citedPages: [34],
    citedDocumentId: 'kimpton-angler-t12-2026q1',
    citedDocumentName: 'T12_FinancialStatement.xlsx',
    impactEstimateUsd: 96_000,
  },
  {
    id: 'critic-kimpton-4',
    ruleId: 'MULTI_FIELD_SEASONAL_PATTERN_MISSING',
    title: 'Q1 seasonal swing under-modeled vs Miami Beach historical pattern',
    narrative:
      'Miami Beach Q1 RevPAR historically runs 60-90% above Q3 trough on the comp set. Proforma shows a Q1-Q3 swing of only 18%, smoothing the seasonal curve. This overstates Q3 distributable cash and understates Q1 compression revenue — both feed an unrealistically stable DSCR profile. Re-spread monthly RevPAR before locking the debt sizing assumption.',
    severity: 'WARN',
    citedFields: ['revpar', 'occupancy'],
    citedPages: [11, 13],
    citedDocumentId: 'kimpton-angler-om-2026',
    citedDocumentName: 'Offering_Memorandum_Final.pdf',
  },
  {
    id: 'critic-kimpton-5',
    ruleId: 'MULTI_FIELD_PIP_TIMING_INCONSISTENT',
    title: 'Year-1 $5.3M PIP scheduled but Year-1 NOI only dips 2.1%',
    narrative:
      'Broker has a $5.3M PIP scheduled in Year 1 ($40K/key, brand-standard refresh) but Year-1 NOI dips only 2.1%. A soft-good plus FF&E refresh of this size typically takes out 30-50 rooms for 6-10 weeks per phase, costing 4-7% of stabilized NOI in displacement. Either the PIP is being phased off-peak with smaller blocks (model the displacement explicitly) or the timing is unrealistic.',
    severity: 'WARN',
    citedFields: ['noi', 'capex'],
    citedPages: [42, 43],
    citedDocumentId: 'kimpton-angler-om-2026',
    citedDocumentName: 'Offering_Memorandum_Final.pdf',
    impactEstimateUsd: 265_000,
  },
];

export const kimptonCriticSummary = `Fondok identified 5 cross-field issues across the broker proforma (1 CRITICAL, 4 WARN). The coastal-insurance flat-hold is the highest-leverage finding — it understates Year-1 fixed charges by ~$244K and would compress DSCR by ~12 bps if remediated. The NOI-without-OpEx-pressure and PIP-timing flags are companion concerns that compound at the cash-on-cash line. Recommend re-pricing the deal at 30% insurance lift + 12% F&B margin before LOI.`;
