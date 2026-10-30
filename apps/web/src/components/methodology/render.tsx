'use client';
/**
 * Methodology AST → React (FON-120).
 *
 * The restricted renderer for `packages/methodology`: headings, paragraphs,
 * lists, tables, strong/em/code/links, the page's own layout containers and
 * the slot tokens — nothing else, and never raw HTML. Every block that
 * carries `testid=` in the markdown renders `data-testid`, which is how the
 * four methodology test files keep their anchors; each section renders the
 * `<section id>` the "Learn more →" links jump to.
 *
 * The worker renders the same AST to text (`app/methodology/text.py`) for
 * Atlas; keep the two in step when a node kind is added.
 */
import React from 'react';
import Link from 'next/link';
import {
  FileText, Sparkles, BarChart3, Database, Link2, ArrowRight, Pencil, Map, BookOpen,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';
import { cn } from '@/lib/format';
import type { BlockNode, InlineNode, LevelContent, ListItem, MethodologyLevel } from '@/lib/methodology/types';
import { METHODOLOGY_SECTIONS } from '@/lib/methodology/content.generated';
import { ReasonTag, SLOTS } from './slots';

const ICONS: Record<string, typeof FileText> = {
  FileText, Sparkles, BarChart3, Database, Link2, ArrowRight, Pencil, Map, BookOpen,
};

// ───────────────────────────── inline ─────────────────────────────

export function Inline({ nodes }: { nodes: InlineNode[] }) {
  return (
    <>
      {nodes.map((n, i) => (
        <InlineNodeView key={i} node={n} />
      ))}
    </>
  );
}

function InlineNodeView({ node }: { node: InlineNode }) {
  switch (node.type) {
    case 'text':
      return <>{node.value}</>;
    case 'strong':
      return (
        <strong className="font-semibold text-ink-900">
          <Inline nodes={node.children} />
        </strong>
      );
    case 'em':
      return (
        <em>
          <Inline nodes={node.children} />
        </em>
      );
    case 'code':
      return <code className="text-[11.5px]">{node.value}</code>;
    case 'link':
      return (
        <Link href={node.href} className="text-brand-700 underline">
          <Inline nodes={node.children} />
        </Link>
      );
    case 'br':
      return <br />;
    case 'badge':
      return <AssumptionBadge source={node.source} />;
    case 'reason':
      return <ReasonTag code={node.code} />;
    case 'icon': {
      const Icon = ICONS[node.name];
      return Icon ? <Icon size={10} className="inline" aria-hidden="true" /> : null;
    }
    case 'slot': {
      const render = SLOTS.inline[node.name];
      return render ? <>{render()}</> : <UnrenderedSlot name={node.name} inline />;
    }
  }
}

/**
 * A slot the SLOTS map does not know. The loader refuses unknown slot names
 * at codegen, so this is reached only when the two maps drift; it says so on
 * the page rather than leaving a silent gap, and the levels test fails on it.
 */
function UnrenderedSlot({ name, inline = false }: { name: string; inline?: boolean }) {
  const text = `slot not available: ${name}`;
  return inline ? (
    <span data-unrendered-slot={name} className="text-[11px] text-danger-700">{text}</span>
  ) : (
    <div data-unrendered-slot={name} className="text-[12px] text-danger-700 rounded border border-danger-200 p-2">{text}</div>
  );
}

/** The first `strong` node and the rest — for label/description rows. */
function splitLabel(nodes: InlineNode[]): { label: InlineNode[] | null; rest: InlineNode[] } {
  const first = nodes.findIndex((n) => n.type !== 'text' || n.value.trim() !== '');
  if (first >= 0 && nodes[first].type === 'strong') {
    return { label: (nodes[first] as { children: InlineNode[] }).children, rest: nodes.slice(first + 1) };
  }
  return { label: null, rest: nodes };
}

function testid(attrs: { testid?: string } | undefined) {
  return attrs?.testid ? { 'data-testid': attrs.testid } : {};
}

// ───────────────────────────── blocks ─────────────────────────────

export function Blocks({ blocks }: { blocks: BlockNode[] }) {
  return (
    <>
      {blocks.map((b, i) => (
        <BlockView key={i} block={b} />
      ))}
    </>
  );
}

function BlockView({ block }: { block: BlockNode }) {
  switch (block.type) {
    case 'heading':
      return block.level === 4 ? (
        <h4 className="text-[13px] font-semibold text-ink-900 mb-3">
          <Inline nodes={block.inline} />
        </h4>
      ) : (
        <div className="text-[12px] font-semibold text-ink-900 mb-1.5">
          <Inline nodes={block.inline} />
        </div>
      );
    case 'paragraph':
      return (
        <p className="text-[12.5px] text-ink-500 leading-relaxed [&:not(:first-child)]:mt-3" {...testid(block.attrs)}>
          <Inline nodes={block.inline} />
        </p>
      );
    case 'list':
      return <ListView ordered={block.ordered} items={block.items} attrs={block.attrs} />;
    case 'table':
      return (
        <div className="overflow-x-auto" {...testid(block.attrs)}>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-ink-500 text-[11px] uppercase tracking-wide">
                {block.head.map((cell, i) => (
                  <th key={i} className="text-left font-medium px-2 py-1.5">
                    <Inline nodes={cell} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, r) => (
                <tr key={r} className="border-t border-border/50 align-top">
                  {row.map((cell, c) => (
                    <td key={c} className={cn('px-2 py-1.5 text-ink-600', c === 0 && 'whitespace-nowrap')}>
                      <Inline nodes={cell} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case 'slot': {
      const Render = SLOTS.block[block.name];
      return Render ? <Render /> : <UnrenderedSlot name={block.name} />;
    }
    case 'container':
      return <ContainerView block={block} />;
  }
}

function ListView({ ordered, items, attrs }: { ordered: boolean; items: ListItem[]; attrs: { testid?: string } }) {
  // A list whose every item opens with a bold label is the page's
  // "label — description" list (no bullet); anything else is a plain bullet list.
  const labelled = items.length > 0 && items.every((it) => splitLabel(it.inline).label !== null);
  const cls = cn(
    'text-[12.5px] leading-relaxed',
    labelled ? 'space-y-2 text-ink-600' : ordered ? 'list-decimal pl-5 space-y-1 text-ink-600' : 'list-disc pl-5 space-y-1 text-ink-600',
    '[&:not(:first-child)]:mt-3',
  );
  const Tag = ordered ? 'ol' : 'ul';
  return (
    <Tag className={cls} {...testid(attrs)}>
      {items.map((it, i) => (
        <li key={i} {...testid(it.attrs)}>
          <Inline nodes={it.inline} />
        </li>
      ))}
    </Tag>
  );
}

function firstList(children: BlockNode[]) {
  return children.find((c): c is Extract<BlockNode, { type: 'list' }> => c.type === 'list');
}

function ContainerView({ block }: { block: Extract<BlockNode, { type: 'container' }> }) {
  const { kind, attrs, children } = block;
  switch (kind) {
    case 'intro':
      return (
        <>
          {children.map((c, i) =>
            c.type === 'paragraph' ? (
              <p key={i} className="text-[13px] text-ink-500 mb-5 leading-relaxed max-w-3xl" {...testid(c.attrs)}>
                <Inline nodes={c.inline} />
              </p>
            ) : (
              <BlockView key={i} block={c} />
            ),
          )}
        </>
      );
    case 'card':
      return (
        <Card
          className={cn(
            attrs.flush ? 'p-0 overflow-hidden' : 'p-5',
            'mb-4',
            attrs.tone === 'muted' && 'bg-ink-50 border-border',
          )}
          {...testid(attrs)}
        >
          <Blocks blocks={children} />
        </Card>
      );
    case 'stages':
      return (
        <div className="grid grid-cols-1 gap-3 mb-4" {...testid(attrs)}>
          <Blocks blocks={children} />
        </div>
      );
    case 'stage': {
      const Icon = ICONS[attrs.icon ?? ''] ?? FileText;
      const heading = children.find((c) => c.type === 'heading');
      const rest = children.filter((c) => c !== heading);
      return (
        <Card className="p-4" {...testid(attrs)}>
          <div className="flex items-start gap-4">
            <div className="flex flex-col items-center gap-1 flex-shrink-0">
              <div className="w-9 h-9 rounded-lg bg-brand-50 flex items-center justify-center">
                <Icon size={16} className="text-brand-500" />
              </div>
              <span className="text-[10px] text-ink-500 tabular-nums">Stage {attrs.n}</span>
            </div>
            <div className="flex-1">
              {heading && heading.type === 'heading' && (
                <h4 className="text-[13px] font-semibold text-ink-900 mb-1">
                  <Inline nodes={heading.inline} />
                </h4>
              )}
              <Blocks blocks={rest} />
            </div>
          </div>
        </Card>
      );
    }
    case 'chain': {
      const list = firstList(children);
      if (!list) return <Blocks blocks={children} />;
      return (
        <ol className="space-y-2.5" {...testid(attrs)}>
          {list.items.map((s, i) => (
            <li key={i} className="flex items-start gap-3 text-[12.5px]" {...testid(s.attrs)}>
              <span className="inline-flex items-center justify-center w-5 h-5 rounded-full bg-ink-300/30 text-ink-700 text-[10.5px] font-semibold tabular-nums flex-shrink-0">
                {i + 1}
              </span>
              <div className="text-ink-500">
                <Inline nodes={s.inline} />
              </div>
            </li>
          ))}
        </ol>
      );
    }
    case 'badges':
      return (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[12.5px]" {...testid(attrs)}>
          <Blocks blocks={children} />
        </div>
      );
    case 'badge':
      return (
        <div className="flex items-start gap-2.5" {...testid(attrs)}>
          <AssumptionBadge source={attrs.source} className="flex-shrink-0 mt-0.5" />
          <div>
            <div className="font-medium text-ink-900">{attrs.name}</div>
            <div className="text-ink-500 leading-relaxed">
              {children.map((c, i) =>
                c.type === 'paragraph' ? (
                  <p key={i} className="[&:not(:first-child)]:mt-1" {...testid(c.attrs)}>
                    <Inline nodes={c.inline} />
                  </p>
                ) : (
                  <BlockView key={i} block={c} />
                ),
              )}
            </div>
          </div>
        </div>
      );
    case 'columns':
      return (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6 text-[12.5px]" {...testid(attrs)}>
          <Blocks blocks={children} />
        </div>
      );
    case 'column': {
      const Icon = ICONS[attrs.icon ?? ''];
      const heading = children.find((c) => c.type === 'heading');
      const rest = children.filter((c) => c !== heading);
      return (
        <div {...testid(attrs)}>
          <div className="flex items-center gap-2 mb-2">
            {Icon && <Icon size={13} className="text-brand-500" aria-hidden="true" />}
            {heading && heading.type === 'heading' && (
              <h4 className="text-[13px] font-semibold text-ink-900">
                <Inline nodes={heading.inline} />
              </h4>
            )}
          </div>
          <Blocks blocks={rest} />
        </div>
      );
    }
    case 'deflist': {
      const list = firstList(children);
      if (!list) return <Blocks blocks={children} />;
      return (
        <ul className="space-y-2.5 text-[12.5px] text-ink-700" {...testid(attrs)}>
          {list.items.map((it, i) => {
            const { label, rest } = splitLabel(it.inline);
            return (
              <li key={i} className="flex items-start gap-2" {...testid(it.attrs)}>
                {label && (
                  <span className="font-semibold text-ink-900 min-w-[88px]">
                    <Inline nodes={label} />
                  </span>
                )}
                <span className="text-ink-500">
                  <Inline nodes={rest} />
                </span>
              </li>
            );
          })}
        </ul>
      );
    }
    case 'box':
      return (
        <div className="mt-3 rounded-md border border-border bg-ink-50/40 p-3" {...testid(attrs)}>
          <Blocks blocks={children} />
        </div>
      );
  }
}

// ───────────────────────────── sections ─────────────────────────────

export function SectionView({
  id, number, title, blocks, children,
}: {
  id: string;
  number: number;
  title: string;
  blocks: BlockNode[];
  /** Rendered after the level's blocks (the detail page appends the admin panel here). */
  children?: React.ReactNode;
}) {
  return (
    <section id={id} className="mb-10 scroll-mt-24" data-methodology-section={id}>
      <div className="flex items-baseline gap-3 mb-2">
        <span className="text-[11px] font-semibold text-brand-500 uppercase tracking-wide tabular-nums">
          Section {number}
        </span>
      </div>
      <h2 className="text-[20px] font-semibold text-ink-900 mb-2">{title}</h2>
      <Blocks blocks={blocks} />
      {children}
    </section>
  );
}

/**
 * The sections that carry `level`, numbered 1..N in index order. `content`
 * is the level's generated map (`CONCEPTUAL` / `ENGINEERING`) — passed in by
 * the page so the public page's chunk never imports the engineering text.
 */
export function sectionsFor(
  level: Extract<MethodologyLevel, 'conceptual' | 'engineering'>,
  content: Readonly<Record<string, LevelContent>>,
): Array<{ id: string; number: number; content: LevelContent }> {
  const out: Array<{ id: string; number: number; content: LevelContent }> = [];
  for (const s of METHODOLOGY_SECTIONS) {
    const c = content[s.id];
    if (s.levels.includes(level) && c) out.push({ id: s.id, number: out.length + 1, content: c });
  }
  return out;
}

/** Every section of one bundled level, in order. */
export function LevelView({
  level, content,
}: {
  level: Extract<MethodologyLevel, 'conceptual' | 'engineering'>;
  content: Readonly<Record<string, LevelContent>>;
}) {
  return (
    <>
      {sectionsFor(level, content).map(({ id, number, content: c }) => (
        <SectionView key={id} id={id} number={number} title={c.title} blocks={c.blocks} />
      ))}
    </>
  );
}
