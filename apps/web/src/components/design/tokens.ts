/**
 * Fondok canonical design tokens — extracted EXACTLY from the vendored
 * canonical design bundle (`design/canonical/*.dc.html`, FON-72). This module
 * is the single source of truth for every color / size / weight used by the
 * `components/design/` layer; the components render with inline styles keyed off
 * these constants so fidelity to the design source is exact (the canonical
 * prototype itself is built almost entirely with inline styles + oklch()).
 *
 * Mirrored as CSS custom properties in `tokens.css` and exposed to Tailwind
 * under the `fondok*` theme keys in `tailwind.config.ts`. See `README.md` for
 * the component → `.dc.html` source map.
 *
 * NOTE ON PALETTE VARIANTS: the three cross-cutting design-system files use
 * three slightly different oklch tunings for the "same" six states —
 *   • `Fondok Data Key.dc.html`      → the canonical STRIP + dot colors
 *   • `Fondok Field System.dc.html`  → the inline FIELD-VALUE treatment
 *   • `Data Provenance System.dc.html`→ a conceptual legend proposal
 * We follow each file for the component it authors (DataKey/ProvenanceDot use
 * the Data Key strip colors; FieldValue/WhereThisCameFrom use the Field System
 * colors). The discrepancy is flagged in README + the handoff report for Sam to
 * reconcile — do not silently unify them.
 */

/* ────────────────────────────────────────────────────────────────────────
 * Core palette — `Fondok Component Kit.dc.html` swatches + tab chrome.
 * ──────────────────────────────────────────────────────────────────────── */
export const palette = {
  /** Navy used for the statement-grid header, primary buttons, active tabs. */
  inkNavy: '#14213d',
  /** Left-nav sidebar navy. */
  sidebarNavy: '#0f1a30',
  /** App background — the warm "ground". */
  ground: '#f5f4f0',
  /** Card / panel surface. */
  cardWhite: '#ffffff',
  /** Nested / inset surface tint (data-key strip, formula chips). */
  surfaceTint: '#fbfbf9',
  /** Standard card + control border. */
  border: '#eae9e4',
  /** Primary text (near-black navy). */
  ink: '#1a2233',
  /** Secondary body text. */
  textSecondary: '#6b6f76',
  /** Muted / caption text. */
  textMuted: '#9a9a95',
  /** Faint text — placeholders, "awaiting" em dashes, column heads. */
  textFaint: '#b0afaa',
  /** Eyebrow / section-label gray. */
  eyebrow: '#8a8a86',
  /** Row hairline inside grids/cards. */
  hairlineRow: '#f7f6f3',
  /** Section hairline (card header/footer dividers). */
  hairlineSection: '#f2f1ec',
  /** Sub-tab baseline hairline. */
  subtabHairline: '#e6e5e0',
  /** Sub-tab hover label + emphasis body. */
  hoverInk: '#3a3f47',
  /** Statement-grid header label text (on navy). */
  gridHeaderText: '#c9cede',
  /** Statement-grid header vertical divider (on navy). */
  gridHeaderDivider: '#2a3a5c',
  /** Action / link text (e.g. "Financials →"). */
  linkBlue: '#2f4a8c',
  /** Action / link hover. */
  linkBlueHover: '#1f3568',
  /** Secondary-button border. */
  buttonSecondaryBorder: '#d8d7d2',
  /** Disabled control text. */
  disabledText: '#9098a3',
  /** Disabled control border. */
  disabledBorder: '#e2e1dc',
} as const;

/* ────────────────────────────────────────────────────────────────────────
 * Provenance state colors — `Fondok Data Key.dc.html` (the canonical strip).
 * These drive DataKey + ProvenanceDot + StateBadge.
 * ──────────────────────────────────────────────────────────────────────── */
export const prov = {
  /** Document sourced + Linked share this green (origin is where, not colour). */
  green: 'oklch(45% 0.12 155)',
  /** Assumption / user input. */
  blue: 'oklch(45% 0.14 260)',
  /** Calculated (grey — black is reserved for totals). */
  gray: '#5f656e',
  /** Awaiting data (dashed hollow dot, em dash value). */
  muted: '#b0afaa',
  /** Total / summary (bold, near-black). */
  black: '#1a2233',
  /** Amber accent constant (Needs-review family). */
  amber: 'oklch(52% 0.15 45)',
  /** Needs-review halo ring drawn on top of an origin dot. */
  reviewRing: '0 0 0 3px oklch(88% 0.07 45)',
  /** Needs-review cell tint used in dense grids (colour-only tier). */
  reviewCellTint: 'oklch(93% 0.05 45 / .55)',
} as const;

/** The canonical six ValueState origins, in the fixed strip order. */
export type ProvState =
  | 'document_sourced'
  | 'linked'
  | 'assumption'
  | 'calculated'
  | 'awaiting_data'
  | 'needs_review';

/** Per-state dot rendering — fill / border / ring — from `tokens()` in
 *  `Fondok Data Key.dc.html`. `needs_review` renders a green origin with the
 *  amber halo ring, exactly as the strip + Cash Flow / Sub-Tabs tabs show it. */
export const provDot: Record<
  ProvState,
  { fill: string; border: string; ring: string; label: string }
> = {
  document_sourced: { fill: prov.green, border: 'none', ring: 'none', label: 'Document sourced' },
  linked: { fill: '#fff', border: `2px solid ${prov.green}`, ring: 'none', label: 'Linked' },
  assumption: { fill: prov.blue, border: 'none', ring: 'none', label: 'Assumption' },
  calculated: { fill: prov.gray, border: 'none', ring: 'none', label: 'Calculated' },
  awaiting_data: { fill: '#fff', border: `1px dashed ${prov.muted}`, ring: 'none', label: 'Awaiting data' },
  needs_review: { fill: prov.green, border: 'none', ring: prov.reviewRing, label: 'Needs review' },
} as const;

/* ────────────────────────────────────────────────────────────────────────
 * Field-value treatment — `Fondok Field System.dc.html` (`static C` + specimens).
 * Drives FieldValue (inline value cells) + WhereThisCameFrom kind colors.
 * ──────────────────────────────────────────────────────────────────────── */
export const field = {
  /** The ONLY blue in the interface — a human owns it, can edit here. */
  input: '#1a4fa0',
  /** Plain value ink (document / linked / calculated bodies). */
  ink: '#1a2233',
  /** Document-sourced page-corner glyph. */
  doc: 'oklch(48% 0.09 165)',
  /** Linked outbound-arrow glyph. */
  link: 'oklch(52% 0.07 240)',
  /** Needs-review flag (amber rule + corner). */
  flag: 'oklch(62% 0.16 55)',
  /** Overridden corner triangle (violet). */
  override: 'oklch(55% 0.13 300)',
  /** Editable dashed underline. */
  inputRule: '1px dashed rgba(26,79,160,.45)',
  /** Document-sourced dotted underline. */
  docRule: '1px dotted #c3c9c2',
  /** Glyphs. */
  glyphDoc: '◱',
  glyphLink: '↗',
  /** Active inline editor. */
  editBorder: '1.5px solid #1a4fa0',
  editRing: '0 0 0 3px rgba(26,79,160,.12)',
  /** Row-selected highlight (popover anchor). */
  rowSelectedBg: '#f4f6fb',
  rowSelectedRule: 'inset 2px 0 0 #1a4fa0',
} as const;

/** Popover header "kind" colors — `insp` objects in `Fondok Field System.dc.html`. */
export const popoverKind = {
  editable_assumption: '#1a4fa0',
  overridden: 'oklch(50% 0.13 300)',
  sourced_needs_review: 'oklch(52% 0.16 55)',
  document_sourced: 'oklch(45% 0.09 165)',
  linked: 'oklch(48% 0.08 240)',
  calculated: '#1a2233',
} as const;

/* ────────────────────────────────────────────────────────────────────────
 * Typography scale — measured across the canonical tab + system files.
 * ──────────────────────────────────────────────────────────────────────── */
export const type = {
  /** Tab / screen H1 (Investment, Cash Flow headers). */
  h1: { size: '26px', weight: 600, tracking: '-0.01em' },
  /** Field-System hero H1. */
  h1Hero: { size: '31px', weight: 600, tracking: '-0.015em' },
  /** Data-key proposal H1. */
  h1Sub: { size: '27px', weight: 600, tracking: '-0.01em' },
  /** Popover headline value. */
  popoverValue: { size: '24px', weight: 600, tracking: '-0.01em' },
  /** KPI-tile value. */
  kpiValue: { size: '19px', weight: 700, tracking: '0' },
  /** Card / table section title. */
  sectionTitle: { size: '13.5px', weight: 700, tracking: '0' },
  /** Body. */
  body: { size: '14px', weight: 400, tracking: '0' },
  bodySm: { size: '13px', weight: 400, tracking: '0' },
  /** Grid cell. */
  cell: { size: '12px', weight: 400, tracking: '0' },
  /** KPI-tile eyebrow. */
  kpiLabel: { size: '10px', weight: 700, tracking: '0.04em' },
  /** Section eyebrow. */
  eyebrow: { size: '12px', weight: 700, tracking: '0.03em' },
  /** Data-key strip. */
  strip: { size: '11px', weight: 400, tracking: '0' },
  /** Caption / sub. */
  caption: { size: '11.5px', weight: 400, tracking: '0' },
  /** Sub-tab label. */
  subtab: { size: '13.5px', weight: 500, tracking: '0' },
  subtabActiveWeight: 700,
} as const;

/* ────────────────────────────────────────────────────────────────────────
 * Spacing, radii, borders, shadows.
 * ──────────────────────────────────────────────────────────────────────── */
export const radius = {
  /** Standard card. */
  card: '10px',
  /** Field-System card / popover. */
  cardLg: '12px',
  popover: '11px',
  /** Buttons. */
  button: '6px',
  /** Small controls / inline editor. */
  control: '5px',
  /** Status pill. */
  pill: '20px',
} as const;

export const border = {
  card: `1px solid ${palette.border}`,
  hairlineRow: `1px solid ${palette.hairlineRow}`,
  hairlineSection: `1px solid ${palette.hairlineSection}`,
  subtabActive: `2px solid ${palette.inkNavy}`,
  subtabRest: '2px solid transparent',
  subtabHover: '2px solid #d9d8d2',
} as const;

export const shadow = {
  /** Anchored provenance popover. */
  popover: '0 14px 40px rgba(20,33,61,.16), 0 2px 6px rgba(20,33,61,.06)',
  /** "How to read this" centered help modal. */
  helpModal: '0 24px 56px rgba(16,24,40,.28)',
  /** Inline-editor focus ring. */
  editFocus: field.editRing,
} as const;

export const overlay = {
  /** Help-modal scrim. */
  scrim: 'rgba(16,24,40,.34)',
} as const;

/** The single Inter/system font stack used throughout the canonical design. */
export const fontStack =
  "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif";
