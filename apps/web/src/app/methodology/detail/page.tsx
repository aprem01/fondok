'use client';
/**
 * /methodology/detail — the client methodology plus the `detailed` level
 * (formulas, defaults, precedence with values) for administrators (FON-120).
 *
 * Two gates, both required: the page renders the detail only for
 * `useCurrentRole() === 'org:admin'`, and the detailed text is NOT in the
 * JS bundle — it is fetched from the worker (`GET /methodology?level=
 * detailed`), which enforces the stricter gate (an admin of an internal
 * tenant) and answers 403 otherwise. A member who finds the URL sees the
 * public text and a notice, never the detail.
 */
import { useEffect, useState } from 'react';
import { Lock, BookOpen } from 'lucide-react';
import Link from 'next/link';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { api, userMessage, WorkerError } from '@/lib/api';
import { useCurrentRole } from '@/lib/auth';
import { Blocks, SectionView, sectionsFor } from '@/components/methodology/render';
import { METHODOLOGY_PAGES } from '@/lib/methodology/content.generated';
import { CONCEPTUAL } from '@/lib/methodology/conceptual.generated';
import type { MethodologyLevelResponse } from '@/lib/methodology/types';

type DetailState =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ready'; data: MethodologyLevelResponse }
  | { kind: 'forbidden' }
  | { kind: 'error'; message: string };

export default function MethodologyDetailPage() {
  const role = useCurrentRole();
  const isAdmin = role === 'org:admin';
  // 'unknown' is the pre-mount / no-org state: show neither the gate card nor
  // the detail until the role resolves, so the server and client HTML agree.
  const isMember = role === 'org:member';
  const [state, setState] = useState<DetailState>({ kind: 'idle' });
  const page = METHODOLOGY_PAGES.detailed;

  useEffect(() => {
    if (!isAdmin) return;
    const ctl = new AbortController();
    setState({ kind: 'loading' });
    api.methodology
      .level('detailed', ctl.signal)
      .then((data) => setState({ kind: 'ready', data }))
      .catch((e: unknown) => {
        if ((e as { name?: string } | null)?.name === 'AbortError') return;
        if (e instanceof WorkerError && e.status === 403) setState({ kind: 'forbidden' });
        else setState({ kind: 'error', message: userMessage(e) });
      });
    return () => ctl.abort();
  }, [isAdmin]);

  const detailById = new Map(state.kind === 'ready' ? state.data.sections.map((s) => [s.id, s]) : []);

  return (
    <div className="max-w-4xl" data-testid="methodology-detail">
      <PageHeader eyebrow={page.eyebrow} title={page.title} subtitle={page.subtitle} />

      {isMember && (
        <Card className="p-4 mb-8 bg-ink-50 border-border" data-testid="methodology-detail-gate">
          <p className="text-[12.5px] text-ink-700 leading-relaxed flex items-start gap-2">
            <Lock size={13} className="text-ink-500 mt-0.5 flex-shrink-0" aria-hidden="true" />
            <span>
              The detailed methodology is available to workspace administrators. The public methodology
              below is the same text every signed-in user reads on{' '}
              <Link href="/methodology" className="text-brand-700 underline">Methodology</Link>.
            </span>
          </p>
        </Card>
      )}
      {isAdmin && state.kind === 'forbidden' && (
        <Card className="p-4 mb-8 bg-ink-50 border-border" data-testid="methodology-detail-forbidden">
          <p className="text-[12.5px] text-ink-700 leading-relaxed">
            The detailed methodology is available to Fondok administrators only. The public methodology
            below is unchanged.
          </p>
        </Card>
      )}
      {isAdmin && state.kind === 'error' && (
        <Card className="p-4 mb-8 border-danger-200" data-testid="methodology-detail-error">
          <p className="text-[12.5px] text-ink-700 leading-relaxed">
            The detailed methodology could not be loaded: {state.message}
          </p>
        </Card>
      )}
      {isAdmin && state.kind === 'loading' && (
        <p className="text-[12px] text-ink-500 mb-6" data-testid="methodology-detail-loading">
          Loading the detailed methodology…
        </p>
      )}

      {sectionsFor('conceptual', CONCEPTUAL).map(({ id, number, content }) => {
        const detail = detailById.get(id);
        return (
          <SectionView key={id} id={id} number={number} title={content.title} blocks={content.blocks}>
            {detail && (
              <div
                className="mt-2 border-l-2 border-brand-300 pl-4"
                data-testid={`methodology-detail-${id}`}
              >
                <div className="text-[11px] font-semibold text-brand-700 uppercase tracking-wide mb-2">
                  Detail — administrators
                </div>
                <Blocks blocks={detail.blocks} />
              </div>
            )}
          </SectionView>
        );
      })}

      <div className="flex items-center gap-3 mt-8">
        <Link href="/methodology">
          <Button variant="secondary">
            <BookOpen size={14} /> Public methodology
          </Button>
        </Link>
      </div>
    </div>
  );
}
