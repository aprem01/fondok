/**
 * Methodology AST types (FON-120).
 *
 * Mirrors the node shapes `apps/worker/app/methodology/parser.py` emits. The
 * web never parses markdown: `scripts/gen_methodology.py` runs the worker's
 * parser over `packages/methodology/sections/*.md` and writes the AST into
 * `content.generated.ts` (conceptual + engineering); the `detailed` level is
 * fetched from `GET /methodology?level=detailed` at runtime in the same shape.
 * `components/methodology/render.tsx` turns either into React.
 */

export type MethodologyLevel = 'conceptual' | 'detailed' | 'engineering';

export type InlineNode =
  | { type: 'text'; value: string }
  | { type: 'strong'; children: InlineNode[] }
  | { type: 'em'; children: InlineNode[] }
  | { type: 'code'; value: string }
  | { type: 'link'; href: string; children: InlineNode[] }
  | { type: 'br' }
  /** `{{badge:<source>}}` — an AssumptionBadge for a registry source id. */
  | { type: 'badge'; source: string }
  /** `{{reason:<code>}}` — the engineering ReasonTag for a reason code. */
  | { type: 'reason'; code: string }
  /** `{{icon:<Name>}}` — a decorative lucide icon, no text. */
  | { type: 'icon'; name: string }
  /** `{{slot:<name>}}` inside a sentence — rendered by SLOTS.inline. */
  | { type: 'slot'; name: string };

export type BlockAttrs = Partial<Record<'testid' | 'source' | 'name' | 'n' | 'icon' | 'flush' | 'tone' | 'title', string>>;

export type ContainerKind =
  | 'intro'
  | 'card'
  | 'stages'
  | 'stage'
  | 'chain'
  | 'badges'
  | 'badge'
  | 'columns'
  | 'column'
  | 'deflist'
  | 'box';

export interface ListItem {
  attrs: BlockAttrs;
  inline: InlineNode[];
}

export type BlockNode =
  | { type: 'heading'; level: 4 | 5; inline: InlineNode[] }
  | { type: 'paragraph'; attrs: BlockAttrs; inline: InlineNode[] }
  | { type: 'list'; ordered: boolean; attrs: BlockAttrs; items: ListItem[] }
  | { type: 'table'; attrs: BlockAttrs; head: InlineNode[][]; rows: InlineNode[][][] }
  /** A block slot — rendered by SLOTS.block (reason table, registry table, …). */
  | { type: 'slot'; name: string }
  | { type: 'container'; kind: ContainerKind; attrs: BlockAttrs; children: BlockNode[] };

export interface LevelContent {
  /** The section title at this level (the engineering page names sections differently). */
  title: string;
  blocks: BlockNode[];
}

export interface MethodologySection {
  id: string;
  title: string;
  decisions: string[];
  anchors: string[];
  keywords: string[];
  hasDetailed: boolean;
  /** The bundled levels this section carries (`conceptual.generated.ts` /
   *  `engineering.generated.ts`). `detailed` is never bundled. */
  levels: Array<'conceptual' | 'engineering'>;
}

export interface MethodologyPage {
  eyebrow: string;
  title: string;
  subtitle: string;
}

export type MethodologyPages = Record<MethodologyLevel, MethodologyPage>;

/** `GET /methodology?level=…` — mirrors `MethodologyLevelResponse` in the worker. */
export interface MethodologyLevelResponse {
  level: MethodologyLevel;
  page: MethodologyPage;
  sections: Array<{
    id: string;
    number: number;
    title: string;
    decisions: string[];
    anchors: string[];
    blocks: BlockNode[];
  }>;
}
