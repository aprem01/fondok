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
  { id: 'core', label: 'Core', target: '8-12%', desc: 'Stable, income-focused investments with lower risk' },
  { id: 'value-add', label: 'Value Add', target: '12-18%', desc: 'Moderate repositioning with upside potential' },
  { id: 'opportunistic', label: 'Opportunistic', target: '18%+', desc: 'Higher risk/return with significant value creation' },
];

export const positioningTiers = [
  { id: 'default', label: 'Default', desc: 'Let the model determine optimal positioning' },
  { id: 'luxury', label: 'Luxury', desc: 'Premium tier with highest ADR assumptions' },
  { id: 'upscale', label: 'Upscale', desc: 'Upper-midscale to upscale tier' },
  { id: 'economy', label: 'Economy', desc: 'Economy to midscale tier' },
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

// Backfilled from evals/golden-set/brand-catalog.json (191 brands across 12 families).
// Brand names use the canonical catalog form (e.g., "Hampton by Hilton") with the
// shorter mockData aliases retained as substring-matchable prefixes for legacy deal
// records that store just the short brand (e.g., kimptonAnglerOverview.general.brand = "Kimpton").
export type Brand = { name: string; tier: string };
export type BrandFamily = { family: string; count: number; brands: Brand[] };

export const brandFamilies: BrandFamily[] = [
  { family: 'Hilton', count: 18, brands: [
    { name: 'Hampton by Hilton', tier: 'Upper Midscale' },
    { name: 'Hilton Hotels & Resorts', tier: 'Upper Upscale' },
    { name: 'Hilton Garden Inn', tier: 'Upscale' },
    { name: 'DoubleTree by Hilton', tier: 'Upscale' },
    { name: 'Home2 Suites by Hilton', tier: 'Upper Midscale' },
    { name: 'Embassy Suites by Hilton', tier: 'Upper Upscale' },
    { name: 'Homewood Suites by Hilton', tier: 'Upscale' },
    { name: 'Tru by Hilton', tier: 'Midscale' },
    { name: 'Curio Collection by Hilton', tier: 'Upper Upscale' },
    { name: 'Tapestry Collection by Hilton', tier: 'Upscale' },
    { name: 'Canopy by Hilton', tier: 'Upper Upscale' },
    { name: 'Signia by Hilton', tier: 'Luxury' },
    { name: 'Motto by Hilton', tier: 'Upscale' },
    { name: 'Spark by Hilton', tier: 'Midscale' },
    { name: 'Tempo by Hilton', tier: 'Upscale' },
    { name: 'LXR Hotels & Resorts', tier: 'Luxury' },
    { name: 'Conrad Hotels & Resorts', tier: 'Luxury' },
    { name: 'Waldorf Astoria Hotels & Resorts', tier: 'Luxury' },
  ]},
  { family: 'Marriott International', count: 28, brands: [
    { name: 'Courtyard by Marriott', tier: 'Upscale' },
    { name: 'Marriott Hotels', tier: 'Upper Upscale' },
    { name: 'Fairfield by Marriott', tier: 'Upper Midscale' },
    { name: 'Residence Inn by Marriott', tier: 'Upscale' },
    { name: 'Sheraton', tier: 'Upper Upscale' },
    { name: 'SpringHill Suites by Marriott', tier: 'Upscale' },
    { name: 'TownePlace Suites by Marriott', tier: 'Upper Midscale' },
    { name: 'Autograph Collection', tier: 'Upper Upscale' },
    { name: 'Renaissance Hotels', tier: 'Upper Upscale' },
    { name: 'Aloft Hotels', tier: 'Upscale' },
    { name: 'Four Points by Sheraton', tier: 'Upscale' },
    { name: 'Delta Hotels by Marriott', tier: 'Upper Upscale' },
    { name: 'AC Hotels by Marriott', tier: 'Upscale' },
    { name: 'JW Marriott', tier: 'Luxury' },
    { name: 'The Westin Hotels & Resorts', tier: 'Upper Upscale' },
    { name: 'Element Hotels', tier: 'Upscale' },
    { name: 'Tribute Portfolio', tier: 'Upper Upscale' },
    { name: 'Moxy Hotels', tier: 'Upscale' },
    { name: 'The Luxury Collection', tier: 'Luxury' },
    { name: 'Le Méridien', tier: 'Upper Upscale' },
    { name: 'The Ritz-Carlton', tier: 'Luxury' },
    { name: 'W Hotels', tier: 'Luxury' },
    { name: 'St. Regis Hotels & Resorts', tier: 'Luxury' },
    { name: 'EDITION Hotels', tier: 'Luxury' },
    { name: 'Gaylord Hotels', tier: 'Upper Upscale' },
    { name: 'Bvlgari Hotels & Resorts', tier: 'Luxury' },
    { name: 'City Express by Marriott', tier: 'Midscale' },
    { name: 'Marriott Executive Apartments', tier: 'Upper Upscale' },
  ]},
  { family: 'IHG Hotels & Resorts', count: 17, brands: [
    { name: 'Holiday Inn Express', tier: 'Upper Midscale' },
    { name: 'Holiday Inn', tier: 'Upscale' },
    { name: 'Candlewood Suites', tier: 'Midscale' },
    { name: 'Staybridge Suites', tier: 'Upscale' },
    { name: 'Crowne Plaza Hotels & Resorts', tier: 'Upper Upscale' },
    { name: 'InterContinental Hotels & Resorts', tier: 'Luxury' },
    { name: 'Kimpton Hotels & Restaurants', tier: 'Upper Upscale' },
    { name: 'Hotel Indigo', tier: 'Upscale' },
    { name: 'avid hotels', tier: 'Midscale' },
    { name: 'EVEN Hotels', tier: 'Upscale' },
    { name: 'voco Hotels', tier: 'Upper Upscale' },
    { name: 'Vignette Collection', tier: 'Upper Upscale' },
    { name: 'Atwell Suites', tier: 'Upscale' },
    { name: 'Garner Hotels', tier: 'Midscale' },
    { name: 'Iberostar Beachfront Resorts', tier: 'Upper Upscale' },
    { name: 'Regent Hotels & Resorts', tier: 'Luxury' },
    { name: 'Six Senses', tier: 'Luxury' },
  ]},
  { family: 'Hyatt Hotels Corp.', count: 18, brands: [
    { name: 'Hyatt Place', tier: 'Upscale' },
    { name: 'Hyatt House', tier: 'Upscale' },
    { name: 'Hyatt Regency', tier: 'Upper Upscale' },
    { name: 'Grand Hyatt', tier: 'Upper Upscale' },
    { name: 'Hyatt Centric', tier: 'Upper Upscale' },
    { name: 'Andaz', tier: 'Luxury' },
    { name: 'Park Hyatt', tier: 'Luxury' },
    { name: 'Thompson Hotels', tier: 'Luxury' },
    { name: 'Alila Hotels & Resorts', tier: 'Luxury' },
    { name: 'Destination by Hyatt', tier: 'Upper Upscale' },
    { name: 'JdV by Hyatt', tier: 'Upper Upscale' },
    { name: 'Caption by Hyatt', tier: 'Upscale' },
    { name: 'Hyatt Studios', tier: 'Upper Midscale' },
    { name: 'Hyatt Vacation Club', tier: 'Upper Upscale' },
    { name: 'Hyatt Ziva', tier: 'Upper Upscale' },
    { name: 'Hyatt Zilara', tier: 'Luxury' },
    { name: 'UrCove', tier: 'Upper Midscale' },
    { name: 'Miraval Resorts', tier: 'Luxury' },
  ]},
  { family: 'Wyndham Hotels & Resorts', count: 21, brands: [
    { name: 'Super 8 by Wyndham', tier: 'Economy' },
    { name: 'Days Inn by Wyndham', tier: 'Economy' },
    { name: 'Ramada by Wyndham', tier: 'Midscale' },
    { name: 'Howard Johnson by Wyndham', tier: 'Economy' },
    { name: 'Travelodge by Wyndham', tier: 'Economy' },
    { name: 'Microtel by Wyndham', tier: 'Economy' },
    { name: 'Wingate by Wyndham', tier: 'Upper Midscale' },
    { name: 'Hawthorn Suites by Wyndham', tier: 'Midscale' },
    { name: 'AmericInn by Wyndham', tier: 'Midscale' },
    { name: 'Baymont by Wyndham', tier: 'Midscale' },
    { name: 'La Quinta by Wyndham', tier: 'Midscale' },
    { name: 'Wyndham Garden', tier: 'Upper Midscale' },
    { name: 'Wyndham Hotels & Resorts', tier: 'Upper Upscale' },
    { name: 'Wyndham Grand', tier: 'Upper Upscale' },
    { name: 'Wyndham Alltra', tier: 'Upper Upscale' },
    { name: 'TRYP by Wyndham', tier: 'Upscale' },
    { name: 'Dolce Hotels and Resorts by Wyndham', tier: 'Upper Upscale' },
    { name: 'Trademark Collection by Wyndham', tier: 'Upper Midscale' },
    { name: 'Registry Collection Hotels', tier: 'Luxury' },
    { name: 'Esplendor by Wyndham', tier: 'Upscale' },
    { name: 'Vienna House by Wyndham', tier: 'Upscale' },
  ]},
  { family: 'Choice Hotels International', count: 19, brands: [
    { name: 'Comfort Inn', tier: 'Upper Midscale' },
    { name: 'Comfort Suites', tier: 'Upper Midscale' },
    { name: 'Quality Inn', tier: 'Midscale' },
    { name: 'Sleep Inn', tier: 'Midscale' },
    { name: 'Clarion', tier: 'Upper Midscale' },
    { name: 'Clarion Pointe', tier: 'Upper Midscale' },
    { name: 'Cambria Hotels', tier: 'Upscale' },
    { name: 'MainStay Suites', tier: 'Midscale' },
    { name: 'WoodSpring Suites', tier: 'Economy' },
    { name: 'Suburban Studios', tier: 'Economy' },
    { name: 'Everhome Suites', tier: 'Midscale' },
    { name: 'Econo Lodge', tier: 'Economy' },
    { name: 'Rodeway Inn', tier: 'Economy' },
    { name: 'Ascend Hotel Collection', tier: 'Upper Midscale' },
    { name: 'Radisson Hotels Americas', tier: 'Upper Upscale' },
    { name: 'Radisson Blu', tier: 'Upper Upscale' },
    { name: 'Radisson RED', tier: 'Upscale' },
    { name: 'Park Plaza', tier: 'Upper Upscale' },
    { name: 'Country Inn & Suites by Radisson', tier: 'Upper Midscale' },
  ]},
  { family: 'BWH Hotels (Best Western)', count: 17, brands: [
    { name: 'Best Western', tier: 'Midscale' },
    { name: 'Best Western Plus', tier: 'Upper Midscale' },
    { name: 'Best Western Premier', tier: 'Upscale' },
    { name: 'Vīb', tier: 'Upscale' },
    { name: 'GLō Best Western', tier: 'Upper Midscale' },
    { name: 'Executive Residency by Best Western', tier: 'Upper Midscale' },
    { name: 'BW Signature Collection', tier: 'Upper Midscale' },
    { name: 'BW Premier Collection', tier: 'Upscale' },
    { name: 'Sadie Hotel', tier: 'Upper Upscale' },
    { name: 'Aiden Hotels', tier: 'Upper Midscale' },
    { name: 'WorldHotels Luxury', tier: 'Luxury' },
    { name: 'WorldHotels Elite', tier: 'Upper Upscale' },
    { name: 'WorldHotels Distinctive', tier: 'Upper Upscale' },
    { name: 'WorldHotels Crafted', tier: 'Upscale' },
    { name: 'SureStay Hotel by Best Western', tier: 'Economy' },
    { name: 'SureStay Plus by Best Western', tier: 'Midscale' },
    { name: 'SureStay Studio by Best Western', tier: 'Economy' },
  ]},
  { family: 'Sonesta International Hotels', count: 12, brands: [
    { name: 'Sonesta Hotels & Resorts', tier: 'Upper Upscale' },
    { name: 'Royal Sonesta', tier: 'Upper Upscale' },
    { name: 'Sonesta Select', tier: 'Upscale' },
    { name: 'Sonesta Simply Suites', tier: 'Midscale' },
    { name: 'Sonesta ES Suites', tier: 'Upscale' },
    { name: 'Classico, A Sonesta Collection', tier: 'Upper Upscale' },
    { name: 'MOD, A Sonesta Collection', tier: 'Upper Upscale' },
    { name: 'James Hotels', tier: 'Upper Upscale' },
    { name: 'Red Lion Hotels', tier: 'Upper Midscale' },
    { name: 'Red Lion Inn & Suites', tier: 'Midscale' },
    { name: 'Americas Best Value Inn', tier: 'Economy' },
    { name: 'Knights Inn', tier: 'Economy' },
  ]},
  { family: 'G6 Hospitality', count: 2, brands: [
    { name: 'Motel 6', tier: 'Economy' },
    { name: 'Studio 6', tier: 'Economy' },
  ]},
  { family: 'Red Roof', count: 4, brands: [
    { name: 'Red Roof Inn', tier: 'Economy' },
    { name: 'Red Roof PLUS+', tier: 'Economy' },
    { name: 'HomeTowne Studios by Red Roof', tier: 'Economy' },
    { name: 'The Red Collection', tier: 'Upper Midscale' },
  ]},
  { family: 'Extended Stay America', count: 3, brands: [
    { name: 'Extended Stay America', tier: 'Economy' },
    { name: 'Extended Stay America Suites', tier: 'Midscale' },
    { name: 'Extended Stay America Premier Suites', tier: 'Upper Midscale' },
  ]},
  { family: 'Other Brands', count: 32, brands: [
    { name: 'Four Seasons Hotels and Resorts', tier: 'Luxury' },
    { name: 'Aman Resorts', tier: 'Luxury' },
    { name: 'Mandarin Oriental Hotel Group', tier: 'Luxury' },
    { name: 'Rosewood Hotels & Resorts', tier: 'Luxury' },
    { name: 'Belmond', tier: 'Luxury' },
    { name: 'Capella Hotels and Resorts', tier: 'Luxury' },
    { name: 'Auberge Resorts Collection', tier: 'Luxury' },
    { name: 'Loews Hotels & Co', tier: 'Upper Upscale' },
    { name: 'Omni Hotels & Resorts', tier: 'Upper Upscale' },
    { name: 'Fairmont Hotels and Resorts', tier: 'Luxury' },
    { name: 'Raffles Hotels & Resorts', tier: 'Luxury' },
    { name: 'Sofitel', tier: 'Luxury' },
    { name: 'Pullman Hotels and Resorts', tier: 'Upper Upscale' },
    { name: 'Mövenpick Hotels and Resorts', tier: 'Upper Upscale' },
    { name: 'Novotel', tier: 'Upscale' },
    { name: 'ibis', tier: 'Midscale' },
    { name: 'SLS Hotels', tier: 'Luxury' },
    { name: 'Mondrian', tier: 'Luxury' },
    { name: 'Delano', tier: 'Luxury' },
    { name: '1 Hotels', tier: 'Luxury' },
    { name: 'Baccarat Hotels & Resorts', tier: 'Luxury' },
    { name: 'Treehouse Hotels', tier: 'Upper Upscale' },
    { name: 'Independent / Unflagged', tier: 'Various' },
    { name: 'Margaritaville Hotels & Resorts', tier: 'Upper Upscale' },
    { name: 'Great Wolf Lodge', tier: 'Upper Midscale' },
    { name: 'Drury Hotels', tier: 'Upper Midscale' },
    { name: 'Hard Rock Hotels', tier: 'Upper Upscale' },
    { name: 'citizenM', tier: 'Upper Midscale' },
    { name: 'Pendry Hotels', tier: 'Luxury' },
    { name: 'Montage Hotels & Resorts', tier: 'Luxury' },
    { name: 'Nobu Hotels', tier: 'Luxury' },
    { name: 'Equinox Hotels', tier: 'Luxury' },
  ]},
];

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
