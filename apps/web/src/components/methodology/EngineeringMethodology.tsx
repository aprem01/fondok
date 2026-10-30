'use client';
import { BookOpen, Database } from 'lucide-react';
import Link from 'next/link';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { ShowHintsAgainButton } from '@/components/help/HintOptionsMenu';
import { LevelView } from '@/components/methodology/render';
import { METHODOLOGY_PAGES } from '@/lib/methodology/content.generated';
import { ENGINEERING } from '@/lib/methodology/engineering.generated';

/**
 * Methodology — the engineering reference behind the client page.
 *
 * Renders the `engineering` level of `packages/methodology/sections/*.md`
 * (FON-120): engine field paths, API routes, environment variables,
 * repository paths and reason codes, the generated concept registry table
 * and the lineage node vocabulary. Served at /admin/methodology-engineering
 * only when NEXT_PUBLIC_SHOW_DIAG is 'true' — engineering vocabulary is not
 * for a client deployment at all, so the role gate alone is not enough; the
 * engineering text lives in its own generated module so the public page's
 * chunk never carries it. Three tests (`methodologyMarkets`,
 * `methodologyReasons`, `methodologyRenovationBudget`) render this component
 * and pin its wording.
 */
export default function EngineeringMethodology() {
  const page = METHODOLOGY_PAGES.engineering;
  return (
    <div className="max-w-4xl">
      <PageHeader
        eyebrow={page.eyebrow}
        title={page.title}
        subtitle={page.subtitle}
        action={<ShowHintsAgainButton />}
      />

      <Card className="p-4 mb-8 bg-ink-50 border-border" data-testid="methodology-engineering-notice">
        <p className="text-[12.5px] text-ink-700 leading-relaxed">
          This is the engineering reference behind the client-facing{' '}
          <Link href="/methodology" className="text-brand-700 underline">Methodology</Link> page. It names
          engine fields, API routes, environment variables and repository paths, and is reachable only
          when diagnostics are enabled for this deployment.
        </p>
      </Card>

      <LevelView level="engineering" content={ENGINEERING} />

      <div className="flex items-center gap-3 mt-8">
        <Link href="/projects">
          <Button variant="primary">
            <BookOpen size={14} /> Back to deals
          </Button>
        </Link>
        <Link href="/settings/portfolio-library">
          <Button variant="secondary">
            <Database size={14} /> Portfolio Library
          </Button>
        </Link>
      </div>
    </div>
  );
}
