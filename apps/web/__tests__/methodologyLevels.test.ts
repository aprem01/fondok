/**
 * FON-120 — packages/methodology → generated content invariants (plan C.5).
 *
 *  • every section has `conceptual` unless the markdown carries an explicit
 *    `conceptual: none` marker (today: concept-registry, engineering only);
 *  • `detailed` is never in the bundle — the generated modules carry only
 *    `conceptual` and `engineering`;
 *  • every `anchors` testid a section declares is present in its rendered
 *    output (the four methodology test files pin these), and no testid is
 *    rendered that the frontmatter does not declare;
 *  • every slot token in the content has a renderer, and nothing renders as
 *    an unrendered-slot marker;
 *  • the section numbering each page prints is contiguous per level.
 *
 * The frontmatter is read from the package itself so this test cannot pass
 * on a stale generated file: `scripts/gen_methodology.py --check` is the CI
 * drift gate, this is the behavioural one.
 */
import { describe, it, expect, vi } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { render, cleanup } from '@testing-library/react';
import React from 'react';
import { METHODOLOGY_SECTIONS } from '@/lib/methodology/content.generated';
import { CONCEPTUAL } from '@/lib/methodology/conceptual.generated';
import { ENGINEERING } from '@/lib/methodology/engineering.generated';
import { SLOTS } from '@/components/methodology/slots';
import { SectionView, sectionsFor } from '@/components/methodology/render';
import type { BlockNode, InlineNode } from '@/lib/methodology/types';

// AssumptionBadge (the `{{badge:…}}` inline) reads the app router.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  usePathname: () => '/methodology',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

const PACKAGE_DIR = resolve(__dirname, '..', '..', '..', 'packages', 'methodology');

function frontmatter(file: string): Record<string, string> {
  const text = readFileSync(join(PACKAGE_DIR, 'sections', file), 'utf8');
  const m = text.match(/^---\n([\s\S]*?)\n---\n/);
  if (!m) throw new Error(`${file}: no frontmatter`);
  const out: Record<string, string> = {};
  for (const line of m[1].split('\n')) {
    const kv = line.match(/^([a-z_]+):\s*(.*)$/);
    if (kv) out[kv[1]] = kv[2];
  }
  return out;
}

const sectionFiles = readdirSync(join(PACKAGE_DIR, 'sections')).filter((f) => f.endsWith('.md')).sort();

function* walkBlocks(blocks: BlockNode[]): Generator<BlockNode> {
  for (const b of blocks) {
    yield b;
    if (b.type === 'container') yield* walkBlocks(b.children);
  }
}
function* walkInline(nodes: InlineNode[]): Generator<InlineNode> {
  for (const n of nodes) {
    yield n;
    if ('children' in n) yield* walkInline(n.children);
  }
}
function* inlineOf(blocks: BlockNode[]): Generator<InlineNode> {
  for (const b of walkBlocks(blocks)) {
    if (b.type === 'heading' || b.type === 'paragraph') yield* walkInline(b.inline);
    else if (b.type === 'list') for (const it of b.items) yield* walkInline(it.inline);
    else if (b.type === 'table') {
      for (const c of b.head) yield* walkInline(c);
      for (const r of b.rows) for (const c of r) yield* walkInline(c);
    }
  }
}

describe('methodology package — levels', () => {
  it('lists one generated section per markdown file, in file order', () => {
    expect(METHODOLOGY_SECTIONS.map((s) => s.id)).toEqual(
      sectionFiles.map((f) => f.replace(/^\d{2}-/, '').replace(/\.md$/, '')),
    );
  });

  it('every section has conceptual, or an explicit `conceptual: none` marker', () => {
    for (const [i, s] of METHODOLOGY_SECTIONS.entries()) {
      const fm = frontmatter(sectionFiles[i]);
      const has = s.levels.includes('conceptual') && CONCEPTUAL[s.id] !== undefined;
      if (fm.conceptual === 'none') expect(has, `${s.id} marked none but has conceptual`).toBe(false);
      else expect(has, `${s.id} has no conceptual level and no marker`).toBe(true);
    }
  });

  it('detailed is marked `detailed: none` where absent, and is never in the bundle', () => {
    for (const [i, s] of METHODOLOGY_SECTIONS.entries()) {
      const fm = frontmatter(sectionFiles[i]);
      if (fm.detailed === 'none') expect(s.hasDetailed, `${s.id}`).toBe(false);
      else expect(s.hasDetailed, `${s.id} has no detailed level and no marker`).toBe(true);
      // The generated level maps carry only the two bundled levels.
      expect((s.levels as string[]).includes('detailed')).toBe(false);
    }
    const bundled = JSON.stringify({ CONCEPTUAL, ENGINEERING });
    // The detailed level's own intro sentence must not be in either bundle.
    expect(bundled).not.toContain('The formulas, defaults and precedence the projection actually runs');
  });

  it('numbers each level contiguously from 1 over the sections that have it', () => {
    const c = sectionsFor('conceptual', CONCEPTUAL);
    const e = sectionsFor('engineering', ENGINEERING);
    expect(c.map((s) => s.number)).toEqual(c.map((_, i) => i + 1));
    expect(e.map((s) => s.number)).toEqual(e.map((_, i) => i + 1));
    expect(c).toHaveLength(10);
    expect(e).toHaveLength(10);
    // The client page has no concept-registry section; the engineering page has no controls section.
    expect(c.find((s) => s.id === 'lineage')?.number).toBe(8);
    expect(e.find((s) => s.id === 'lineage')?.number).toBe(9);
  });
});

describe('methodology package — slots and anchors', () => {
  it('every slot token has a renderer', () => {
    const blockSlots = new Set<string>();
    const inlineSlots = new Set<string>();
    for (const content of [CONCEPTUAL, ENGINEERING]) {
      for (const lvl of Object.values(content)) {
        for (const b of walkBlocks(lvl.blocks)) if (b.type === 'slot') blockSlots.add(b.name);
        for (const n of inlineOf(lvl.blocks)) if (n.type === 'slot') inlineSlots.add(n.name);
      }
    }
    expect(blockSlots.size).toBeGreaterThan(0);
    for (const name of blockSlots) expect(SLOTS.block[name], `block slot ${name}`).toBeTypeOf('function');
    for (const name of inlineSlots) expect(SLOTS.inline[name], `inline slot ${name}`).toBeTypeOf('function');
  });

  for (const level of ['conceptual', 'engineering'] as const) {
    it(`${level}: renders every declared anchor, no undeclared testid and no unrendered slot`, () => {
      const content = level === 'conceptual' ? CONCEPTUAL : ENGINEERING;
      for (const { id, number, content: c } of sectionsFor(level, content)) {
        const meta = METHODOLOGY_SECTIONS.find((s) => s.id === id)!;
        const { container } = render(
          React.createElement(SectionView, { id, number, title: c.title, blocks: c.blocks }),
        );
        const rendered = new Set(
          Array.from(container.querySelectorAll('[data-testid]')).map((el) => el.getAttribute('data-testid')!),
        );
        // Slot components render their own testids (reason-code-table); anchors cover the markdown's.
        for (const a of meta.anchors) {
          const declaredHere = Array.from(walkBlocks(c.blocks)).some(
            (b) =>
              ('attrs' in b && b.attrs?.testid === a) ||
              (b.type === 'list' && b.items.some((it) => it.attrs?.testid === a)),
          );
          if (declaredHere) expect(rendered.has(a), `${level}/${id}: anchor ${a} not rendered`).toBe(true);
        }
        for (const t of rendered) {
          if (t === 'reason-code-table') continue; // owned by the slot component
          expect(meta.anchors, `${level}/${id}: testid ${t} is rendered but not declared in anchors`).toContain(t);
        }
        expect(container.querySelectorAll('[data-unrendered-slot]')).toHaveLength(0);
        expect(container.querySelectorAll('[data-unknown-reason]')).toHaveLength(0);
        expect(container.querySelector(`section#${id}`)).not.toBeNull();
        cleanup();
      }
    });
  }
});
